"""分类 gateway/run.py 里的硬编码英文字符串，挑出「会发给用户」的候选。

输出三个桶：
  A. Return 语句里的字符串（slash 命令直接回复）
  B. EphemeralReply(...) / 明显回复函数的实参
  C. 追加到 lines/parts 列表、且函数体最终会拼接返回的字符串（启发式）

排除：模型提示词（以 [ 开头、含 System note）、类型注解、正则、日志、路径。
"""
import ast
import json
import re

PATH = "/opt/hermes/gateway/run.py"
src = open(PATH, encoding="utf-8").read()
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

# 模型提示词/内部文本的排除规则
EXCLUDE = re.compile(
    r"^\[|System note|Do NOT |Address the user|Report to the user|You are |Your task|"
    r"str \| None|dict \| None|list\[|tuple\[|Optional\[|OrderedDict|Callable\[|"
    r"^\s*$|^[a-z_]+$|^[A-Z_]+$|^[\w./-]+$|%[sdfr]",
)

MODEL_HINT = re.compile(r"System note|Do NOT |Address the user|Report to the user|your task|You are a|The user sent|CONTINUE the interrupted", re.I)


def walk_strs(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            yield n
        elif isinstance(n, ast.JoinedStr):
            for v in n.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    yield v


def is_candidate(n):
    s = n.value
    if id(n) in docstrings or id(n) in log_ids:
        return False
    if not re.search(r"[A-Za-z]{3,}", s) or re.search(r"[\u4e00-\u9fff]", s):
        return False
    if not re.search(r"\s", s.strip()):
        return False
    if len(re.findall(r"[A-Za-z]{2,}", s)) < 2:
        return False
    if EXCLUDE.search(s.strip()):
        return False
    if MODEL_HINT.search(s):
        return False
    if re.search(r"\.py\b|/opt/|def |import |re\.compile|SELECT |CREATE TABLE", s):
        return False
    return True


buckets = {"A_return": [], "B_ephemeral": [], "C_list_append": []}
seen = set()

for node in ast.walk(tree):
    if isinstance(node, ast.Return) and node.value is not None:
        for n in walk_strs(node.value):
            if is_candidate(n) and (n.lineno, n.value) not in seen:
                seen.add((n.lineno, n.value))
                buckets["A_return"].append({"line": n.lineno, "text": n.value[:200]})

    if isinstance(node, ast.Call):
        f = node.func
        nm = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
        if nm in ("EphemeralReply", "format_session_db_unavailable"):
            for a in node.args:
                for n in walk_strs(a):
                    if is_candidate(n) and (n.lineno, n.value) not in seen:
                        seen.add((n.lineno, n.value))
                        buckets["B_ephemeral"].append({"line": n.lineno, "text": n.value[:200]})

# C: append(...) 到名字像 lines/parts/msg/out/text 的列表
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("append", "extend", "add"):
        base = node.func.value
        if isinstance(base, ast.Name) and re.search(r"line|part|msg|out|text|body|reply|note|buf|chunk", base.id, re.I):
            for a in node.args:
                for n in walk_strs(a):
                    if is_candidate(n) and (n.lineno, n.value) not in seen:
                        seen.add((n.lineno, n.value))
                        buckets["C_list_append"].append({"line": n.lineno, "text": n.value[:200]})

tot = sum(len(v) for v in buckets.values())
print("候选合计:", tot)
for k, v in buckets.items():
    print(f"  {k}: {len(v)}")

json.dump(buckets, open("/opt/data/i18n_buckets.json", "w"), ensure_ascii=False, indent=1)

for k in ("A_return", "B_ephemeral", "C_list_append"):
    print(f"\n===== {k} =====")
    for c in buckets[k]:
        print(f'{c["line"]:>6} | {c["text"]!r}')
