"""把 hermes-cn-translations-v2.py 里的 [前缀, 中文] 解析成精确的 [英文原文, 中文] 映射。

用法：
  /opt/hermes/.venv/bin/python gen_mapping.py            # 生成映射 JSON + 报告
  /opt/hermes/.venv/bin/python gen_mapping.py --report   # 额外打印短字符串的全部出现位置（人工确认安全）
  /opt/hermes/.venv/bin/python gen_mapping.py --list     # 打印全部解析结果供人工校对
"""
import ast
import importlib.util
import json
import re
import sys

# 解析基准：优先用未打补丁的上游原文（保证映射表在升级后仍完整可用）
RUN_CANDIDATES = [
    "/opt/data/hermes-run-py-<ver>.orig",
    "/opt/hermes/gateway/run.py",
]
RUN_PY = "/opt/hermes/gateway/run.py"
import glob
_origs = sorted(glob.glob("/opt/data/hermes-run-py-*.orig"))
if _origs:
    RUN_PY = _origs[-1]
TRANS = "/opt/data/hermes-cn-translations-v2.py"
OUT = "/opt/data/hermes-cn-strings-v2.json"

spec = importlib.util.spec_from_file_location("trans", TRANS)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
PAIRS = mod.PAIRS

src = open(RUN_PY, encoding="utf-8").read()
tree = ast.parse(src)
lines = src.split("\n")

docstrings = set()
for node in ast.walk(tree):
    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            docstrings.add(id(body[0].value))

values = {}
for node in ast.walk(tree):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if id(node) in docstrings:
            continue
        if node.value:
            values.setdefault(node.value, []).append(node.lineno)

HAVE_HAN = re.compile(r"[\u4e00-\u9fff]")

resolved, unresolved, noop, empty = [], [], [], []
extended = []
for prefix, zh in PAIRS:
    if not zh.strip():
        empty.append(prefix)
        continue
    if zh == prefix:
        noop.append(prefix)
        continue
    hits = [v for v in values if v.startswith(prefix)]
    if not hits:
        unresolved.append(prefix)
        continue
    # 只接受「完整值等于前缀」或「唯一以该前缀开头的值」
    exact = [v for v in hits if v == prefix]
    if len(hits) == 1:
        chosen = hits[0]
    elif len(exact) == 1:
        chosen = exact[0]
    else:
        unresolved.append(f"[歧义 {len(hits)}] " + prefix)
        continue
    if HAVE_HAN.search(chosen):
        noop.append(prefix)
        continue
    if chosen != prefix:
        extended.append((prefix, chosen))
    resolved.append([chosen, zh])

print(f"解析成功 {len(resolved)} | 未命中 {len(unresolved)} | 跳过(已相同/已汉化) {len(noop)} | 空译文 {len(empty)}")
if unresolved:
    print("\n[未命中的前缀]")
    for u in unresolved:
        print("  -", repr(u[:90]))
if empty:
    print("\n[空译文（已忽略）]")
    for e in empty:
        print("  -", repr(e[:60]))

if extended:
    print(f"\n[前缀被扩展 {len(extended)} 条] 前缀命中的完整字符串更长，中文必须覆盖整段尾部文本：")
    for prefix, chosen in extended:
        print(f"  - 前缀: {prefix[-40:]!r}")
        print(f"    尾部多出: {chosen[len(prefix):]!r}")

json.dump(resolved, open(OUT, "w"), ensure_ascii=False, indent=1)
print(f"\n已写入 {OUT}（{len(resolved)} 条）")

if "--list" in sys.argv:
    print("\n================ 全部解析结果 ================")
    for en, zh in resolved:
        print(f"\nEN: {en!r}\nZH: {zh!r}\n行: {values.get(en, [])[:6]}")

if "--report" in sys.argv:
    print("\n================ 短字符串（<=32 字符）出现位置 ================")
    for en, zh in resolved:
        if len(en) <= 32:
            print(f"\n>>> {en!r} -> {zh!r}   出现 {len(values[en])} 次")
            for ln in values[en][:8]:
                print(f"    {ln}: {lines[ln-1].strip()[:110]}")
