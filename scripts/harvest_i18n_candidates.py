import ast, json, re

path = "/opt/hermes/gateway/run.py"
src = open(path, encoding="utf-8").read()
tree = ast.parse(src)

docstrings = set()
for node in ast.walk(tree):
    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            docstrings.add(id(body[0].value))

parents = {}
for node in ast.walk(tree):
    for child in ast.iter_child_nodes(node):
        parents[id(child)] = node


def call_name(n):
    if isinstance(n, ast.Call):
        f = n.func
        if isinstance(f, ast.Attribute):
            base = f.value
            if isinstance(base, ast.Name):
                return f"{base.id}.{f.attr}"
            if isinstance(base, ast.Attribute):
                return f"{base.attr}.{f.attr}"
            return f.attr
        if isinstance(f, ast.Name):
            return f.id
    return None


log_call_ids = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Call):
        nm = call_name(node)
        if nm and (nm.startswith("logger.") or nm.startswith("logging.") or nm in ("print", "_log", "warn", "debug")):
            for a in ast.walk(node):
                log_call_ids.add(id(a))

cands = []
for node in ast.walk(tree):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        s = node.value
        if id(node) in docstrings:
            continue
        if id(node) in log_call_ids:
            continue
        if not re.search(r"[A-Za-z]{3,}", s):
            continue
        if re.search(r"[\u4e00-\u9fff]", s):
            continue
        if not re.search(r"\s", s.strip()):
            continue
        words = re.findall(r"[A-Za-z]{2,}", s)
        if len(words) < 2:
            continue
        if re.search(r"%[sdfr]|\.py\b|http|/opt/|def |import ", s):
            continue
        p = parents.get(id(node))
        cands.append({
            "line": node.lineno,
            "parent": type(p).__name__ if p else "?",
            "ctx": call_name(p) if isinstance(p, ast.Call) else None,
            "text": s[:200],
        })

print("候选硬编码字符串:", len(cands))
json.dump(cands, open("/opt/data/i18n_hardcoded_candidates.json", "w"), ensure_ascii=False, indent=1)
for c in cands[:80]:
    print(f'{c["line"]:>6} [{c["parent"]}/{c["ctx"]}] {c["text"]!r}')
