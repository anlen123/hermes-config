#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pansou.py — 网盘资源「双源并查」：搜盘鸭(sopanya) + 全盘搜(xiageba)

用法:
    python3 pansou.py "关键词" [--pan quark] [--json]

策略:
    - 两站并发查询，统一去重（按真实链接）
    - 全盘搜快、无需验证码；搜盘鸭慢但覆盖广（需过图形验证码）
    - 任一源失败不影响另一源，结果里标明来源

输出(默认): 标题 || 真实链接 || 网盘类型 || 来源
"""
import argparse
import json
import sys
import concurrent.futures as cf
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

TYPE_NAMES = {0: "夸克", 1: "百度", 2: "UC", 3: "阿里", 4: "迅雷"}
QUARK_CODE = {"quark": 0}


def run_xiageba(keyword, pan="all"):
    """全盘搜：搜索 + geturl。返回 list[dict]"""
    import xiageba
    sess = xiageba.make_session()
    items, _ = xiageba.search(sess, keyword, page=1, page_size=10,
                              pan=pan if pan != "all" else None, exact=False)
    out = []
    for it in items:
        rid = it.get("id")
        if not rid:
            continue
        res = xiageba.get_url(sess, rid=rid)
        if isinstance(res, tuple):
            continue
        out.append({
            "title": it.get("title", ""),
            "url": res,
            "pan": xiageba.detect_pan(res, it.get("type", "other")),
            "source": "全盘搜",
        })
    return out


def run_sopanya(keyword, pan="all"):
    """搜盘鸭：is_type 映射。返回 list[dict]"""
    import sopanya
    is_type = QUARK_CODE.get(pan, -1) if pan != "all" else -1
    out = []
    for r in sopanya.search(keyword, is_type):
        u = r.get("url")
        if not u:
            continue
        t = r.get("is_type")
        label = TYPE_NAMES.get(t, str(t))
        out.append({"title": r.get("title"), "url": u,
                    "pan": label, "source": "搜盘鸭"})
    return out


def main():
    ap = argparse.ArgumentParser(description="网盘资源双源并查")
    ap.add_argument("keyword")
    ap.add_argument("--pan", default="all",
                    choices=["all", "quark", "baidu", "uc", "xunlei", "ali"])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    results, errors = [], {}
    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        futs = {
            ex.submit(run_xiageba, args.keyword, args.pan): "全盘搜",
            ex.submit(run_sopanya, args.keyword, args.pan): "搜盘鸭",
        }
        for f in cf.as_completed(futs):
            name = futs[f]
            try:
                results.extend(f.result())
            except Exception as e:  # noqa
                errors[name] = str(e)[:120]

    # 去重（按 url），全盘搜优先
    seen, merged = set(), []
    for r in results:
        u = r["url"]
        if u in seen:
            continue
        seen.add(u)
        merged.append(r)

    if args.json:
        print(json.dumps({"keyword": args.keyword, "items": merged,
                          "errors": errors}, ensure_ascii=False, indent=2))
        return

    print(f"# 「{args.keyword}」 双源合计 {len(merged)} 条")
    for r in merged:
        print(f"{r['title']} || {r['url']} || {r['pan']} || {r['source']}")
    if errors:
        for k, v in errors.items():
            print(f"# [!] {k} 失败: {v}", file=sys.stderr)


if __name__ == "__main__":
    main()
