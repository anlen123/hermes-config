"""扫其它 gateway 模块里可能发给用户的英文串（排除 run.py）。"""
import ast
import glob
import re

FILES = sorted(glob.glob("/opt/hermes/gateway/*.py")) + sorted(glob.glob("/opt/hermes/gateway/platforms/*.py")) + sorted(glob.glob("/opt/hermes/gateway/platforms/**/*.py", recursive=True))
SKIP = ("/run.py",)

HAN = re.compile(r"[\u4e00-\u9fff]")
BAD = re.compile(r"System note|Do NOT |You are |^\[|def |import |re\.compile|https?://|%[sd]|\.py\b|SELECT |CREATE |sqlite|log|Log")

for path in FILES:
    if any(path.endswith(s) for s in SKIP):
        continue
    try:
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src)
    except Exception:
        continue
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if HAN.search(v) or len(v) < 25:
                continue
            if len(re.findall(r"[A-Za-z]{2,}", v)) < 3:
                continue
            if not re.search(r"\s", v.strip()):
                continue
            if BAD.search(v):
                continue
            hits.append((node.lineno, v))
    if hits:
        print(f"\n===== {path}  ({len(hits)}) =====")
        for ln, v in hits[:12]:
            print(f"  {ln:>6} {v[:120]!r}")
