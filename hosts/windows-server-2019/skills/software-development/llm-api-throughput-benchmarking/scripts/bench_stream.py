# -*- coding: utf-8 -*-
"""LLM 通道流式吞吐/延迟实测。

用法:
  python bench_stream.py --trials 10 \
    --target "官方直连|https://api.deepseek.com/v1/chat/completions|DEEPSEEK_API_KEY|deepseek-flash" \
    --target "OpenRouter|https://openrouter.ai/api/v1/chat/completions|OPENROUTER_API_KEY|deepseek/deepseek-v4.1-flash" \
    --target "OpenRouter(pin DeepSeek)|https://openrouter.ai/api/v1/chat/completions|OPENROUTER_API_KEY|deepseek/deepseek-v4.1-flash|DeepSeek"

target 格式: 名称|url|KEY_ENV名|模型[|provider pin]
key 来源: 环境变量优先, 其次 ~/AppData/Local/hermes/.env

输出: 逐次明细 + 分阶段中位数, 并写 bench_result.json
"""
import argparse
import json
import os
import statistics
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

ENV_FILE = os.path.expanduser("~/AppData/Local/hermes/.env")


def load_env():
    env = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env


def chat_url(url):
    if url.rstrip("/").endswith("/chat/completions"):
        return url.rstrip("/")
    return url.rstrip("/") + "/chat/completions"


def one_run(url, key, model, prompt, provider_pin, max_tokens):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if provider_pin:
        body["provider"] = {"order": [provider_pin], "allow_fallbacks": True}

    req = urllib.request.Request(
        chat_url(url),
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "HTTP-Referer": "https://localhost",
            "X-Title": "hermes-bench",
        },
    )
    t0 = time.perf_counter()
    t_first = t_first_content = None
    usage = provider = None
    with urllib.request.urlopen(req, timeout=300) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
            except Exception:
                continue
            if obj.get("provider"):
                provider = obj["provider"]
            if obj.get("usage"):
                usage = obj["usage"]
            for ch in obj.get("choices", []):
                d = ch.get("delta") or {}
                # 两个字段名都要判: 官方 DeepSeek 用 reasoning_content,
                # OpenRouter 用 reasoning。只判一个会漏掉整段思考。
                has_think = bool(d.get("reasoning") or d.get("reasoning_content"))
                has_text = bool(d.get("content"))
                if (has_think or has_text) and t_first is None:
                    t_first = time.perf_counter() - t0
                if has_text and t_first_content is None:
                    t_first_content = time.perf_counter() - t0
    total = time.perf_counter() - t0

    u = usage or {}
    ctok = u.get("completion_tokens")
    rtok = (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
    think_s = (t_first_content - t_first) if (t_first and t_first_content) else None
    body_s = (total - t_first_content) if t_first_content else None
    return {
        "t_first": t_first,
        "t_first_content": t_first_content,
        "total": total,
        "ctok": ctok,
        "rtok": rtok,
        "think_rate": rtok / think_s if think_s and think_s > 0.05 else None,
        "body_rate": (ctok - rtok) / body_s if body_s and body_s > 0.05 else None,
        "provider": provider,
        "usage": usage,
    }


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def fmt(x, unit="", nd=2):
    return "n/a" if x is None else f"{x:.{nd}f}{unit}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", action="append", required=True,
                    help="名称|url|KEY_ENV名|模型[|provider pin]")
    ap.add_argument("--trials", type=int, default=10)
    ap.add_argument("--max-tokens", type=int, default=8000)
    ap.add_argument("--prompt", default="用中文写一段 300 字左右的短文，介绍南极企鹅的生存现状。")
    ap.add_argument("--out", default="bench_result.json")
    args = ap.parse_args()

    file_env = load_env()
    targets = []
    for spec in args.target:
        parts = spec.split("|")
        if len(parts) < 4:
            sys.exit(f"target 格式错误: {spec}")
        name, url, key_name, model = parts[0], parts[1], parts[2], parts[3]
        pin = parts[4] if len(parts) > 4 and parts[4] else None
        key = os.environ.get(key_name) or file_env.get(key_name)
        if not key:
            sys.exit(f"找不到 key: {key_name}")
        targets.append((name, url, key, model, pin))

    res = {t[0]: [] for t in targets}
    for i in range(args.trials):
        for name, url, key, model, pin in targets:
            try:
                r = one_run(url, key, model, args.prompt, pin, args.max_tokens)
                res[name].append(r)
                print(f"[{i+1}] {name}: 首字 {fmt(r['t_first'], 's')} | "
                      f"思考 {r['rtok']}tok/{fmt(r['think_rate'], ' tok/s', 0)} | "
                      f"正文 {fmt((r['ctok'] or 0) - r['rtok'], '', 0)}tok/"
                      f"{fmt(r['body_rate'], ' tok/s', 0)} | 落点 {r['provider']}")
            except Exception as e:
                print(f"[{i+1}] {name}: 失败 {type(e).__name__}: {e}")
                res[name].append({"error": f"{type(e).__name__}: {e}"})

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    print("\n=========== 汇总（中位数） ===========")
    for name, rs in res.items():
        ok = [r for r in rs if r.get("ctok")]
        if not ok:
            print(f"{name}: 无有效样本（{len(rs)} 次全部失败）")
            continue
        print(f"\n{name}  [n={len(ok)}/{len(rs)}]")
        print(f"   首个 token: {fmt(median([r['t_first'] for r in ok]), ' 秒')}")
        print(f"   首个正文:  {fmt(median([r['t_first_content'] for r in ok]), ' 秒')}")
        print(f"   思考阶段:  {fmt(median([r['think_rate'] for r in ok]), ' token/秒', 0)}"
              f"  样本 {[round(r['think_rate']) for r in ok if r['think_rate']]}")
        print(f"   正文阶段:  {fmt(median([r['body_rate'] for r in ok]), ' token/秒', 0)}"
              f"  样本 {[round(r['body_rate']) for r in ok if r['body_rate']]}")
        print(f"   路由落点:  {sorted({r['provider'] for r in ok}, key=str)}")
        print(f"   输出 tokens 中位数: {median([r['ctok'] for r in ok]):.0f}"
              f"（其中思考 {median([r['rtok'] for r in ok]):.0f}）")
    print(f"\n原始样本已写入 {args.out}")


if __name__ == "__main__":
    main()
