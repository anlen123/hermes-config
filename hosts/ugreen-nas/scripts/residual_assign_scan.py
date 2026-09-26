"""补充扫描：赋值给「消息类变量」的英文串（比如 message = "..." 然后 return message）。"""
import ast
import re

RUN = "/opt/hermes/gateway/run.py"
src = open(RUN, encoding="utf-8").read()
tree = ast.parse(src)

HAN = re.compile(r"[\u4e00-\u9fff]")
MSGNAME = re.compile(r"^(msg|message|text|note|hint|body|reply|out|warn|line|lines|parts?|ephemeral|content|preview|status|detail|desc|label|tail|header|summary)$", re.I)
BAD = re.compile(r"System note|Do NOT |You are |^\[|%[sdfr]|http|/opt/|\.py\b|format|encoding")

hits = []
for node in ast.walk(tree):
    targets = []
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    if not targets:
        continue
    names = []
    for t in targets:
        if isinstance(t, ast.Name):
            names.append(t.id)
        elif isinstance(t, ast.Attribute):
            names.append(t.attr)
    if not names or not any(MSGNAME.match(n) for n in names):
        continue
    for n in ast.walk(node.value):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            v = n.value
            if HAN.search(v) or len(v) < 18:
                continue
            if len(re.findall(r"[A-Za-z]{2,}", v)) < 3:
                continue
            if not re.search(r"\s", v.strip()) or BAD.search(v):
                continue
            hits.append((n.lineno, names[0], v))

print(f"Assign 桶里仍是英文的消息串: {len(hits)}")
for ln, nm, v in sorted(set(hits)):
    print(f"  {ln:>6} [{nm}] {v[:110]!r}")
