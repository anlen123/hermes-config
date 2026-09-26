"""残留扫描：找出 gateway/run.py 里仍然是英文、且会发给用户的字符串。

三类：
  A. Return / EphemeralReply 等回复位置的英文常量
  B. append 到 lines/parts 之类列表的英文常量
  C. 同一 f-string 里已有中文、但仍有英文片段的消息（说明漏翻了）
"""
import ast
import re

RUN = "/opt/hermes/gateway/run.py"
src = open(RUN, encoding="utf-8").read()
tree = ast.parse(src)
lines = src.split("\n")

parents = {}
for node in ast.walk(tree):
    for child in ast.iter_child_nodes(node):
        parents[id(child)] = node

docstrings = set()
for node in ast.walk(tree):
    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            docstrings.add(id(body[0].value))

log_ids = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Call):
        f = node.func
        nm = None
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            nm = f"{f.value.id}.{f.attr}"
        elif isinstance(f, ast.Name):
            nm = f.id
        if nm and (nm.startswith("logger.") or nm.startswith("logging.") or nm in ("print", "_log")):
            for a in ast.walk(node):
                log_ids.add(id(a))

HAN = re.compile(r"[\u4e00-\u9fff]")
MODEL_HINT = re.compile(r"System note|Do NOT |Address the user|Report to the user|your task|You are a|The user sent|CONTINUE the interrupted|maintain|prompt", re.I)
EXCLUDE = re.compile(
    r"^\[|System note|Do NOT |str \| None|dict \| None|list\[|tuple\[|Optional\[|OrderedDict|Callable\[|"
    r"^\s*$|^[a-z_]+$|^[A-Z_]+$|^[\w./-]+$|%[sdfr]"
)


def walk_strs(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            yield n


def englishy(s):
    if not re.search(r"[A-Za-z]{3,}", s) or HAN.search(s):
        return False
    if not re.search(r"\s", s.strip()):
        return False
    if len(re.findall(r"[A-Za-z]{2,}", s)) < 2:
        return False
    if EXCLUDE.search(s.strip()) or MODEL_HINT.search(s):
        return False
    if re.search(r"\.py\b|/opt/|def |import |re\.compile|SELECT |CREATE TABLE|http", s):
        return False
    return True


A, B, C = [], [], []
seen = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Return) and node.value is not None:
        for n in walk_strs(node.value):
            if id(n) not in docstrings and id(n) not in log_ids and englishy(n.value) and (n.lineno, n.value) not in seen:
                seen.add((n.lineno, n.value))
                A.append((n.lineno, n.value))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("append", "extend", "add"):
        base = node.func.value
        if isinstance(base, ast.Name) and re.search(r"line|part|msg|out|text|body|reply|note|buf|chunk", base.id, re.I):
            for a in node.args:
                for n in walk_strs(a):
                    if id(n) not in docstrings and englishy(n.value) and (n.lineno, n.value) not in seen:
                        seen.add((n.lineno, n.value))
                        B.append((n.lineno, n.value))

# C: 同一 f-string 里已有中文 → 把里面残留的英文片段也列出来
for node in ast.walk(tree):
    if not isinstance(node, ast.JoinedStr):
        continue
    parts = [v for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
    if not any(HAN.search(p.value) for p in parts):
        continue
    for p in parts:
        if re.search(r"[A-Za-z]{3,}", p.value) and not HAN.search(p.value):
            C.append((p.lineno, p.value, node.lineno))

print(f"A(回复位置)={len(A)}  B(列表追加)={len(B)}  C(半汉化消息残留)={len(C)}")
print("\n===== C：同一消息里还残留英文的片段（优先修）=====")
for ln, val, g in sorted(set((a, b, c) for a, b, c in C)):
    print(f"  {ln:>6} (消息起 {g}) {val[:110]!r}")
print("\n===== A：回复位置仍是英文 =====")
for ln, val in sorted(set(A)):
    print(f"  {ln:>6} {val[:110]!r}")
print("\n===== B：列表追加仍是英文 =====")
for ln, val in sorted(set(B)):
    print(f"  {ln:>6} {val[:110]!r}")
