#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermes gateway 汉化补丁 v2（AST 精确替换，适配 v0.20.1+）

策略：
  1) 普通字符串字面量（含跨行隐式拼接）→ 用带引号的中文字面量整体替换
  2) f-string → 找到包含目标片段的整个 JoinedStr 节点，重建所有文本片段后
     用 ast.unparse 生成新的 f-string 源码，整体替换该节点的完整跨度
     （这样能正确处理「两个相邻字面量被解析器合并成一个常量片段」的情况）
特性：幂等、自动备份、py_compile 校验失败自动回滚。

用法：
  python3 hermes-cn-patch2.py            # 应用
  python3 hermes-cn-patch2.py --check    # 只报告
  python3 hermes-cn-patch2.py --list     # 报告并列出每条替换
  python3 hermes-cn-patch2.py --rollback <备份>
"""
import ast
import json
import os
import shutil
import sys
import time

RUN = "/opt/hermes/gateway/run.py"
MAP_FILE = "/opt/data/hermes-cn-strings-v2.json"
BACKUP_DIR = "/opt/data"
PYC = "/opt/hermes/.venv/bin/python"


def log(m):
    print(m, flush=True)


def backup(path):
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"gateway-run.py.i18n-bak-{ts}")
    shutil.copy2(path, dst)
    return dst


def _byte_offsets(src: str):
    data = src.encode("utf-8")
    offs, pos = [], 0
    for ln in src.split("\n"):
        offs.append(pos)
        pos += len(ln.encode("utf-8")) + 1
    return offs, data


def span_text(offs, data, node) -> str:
    s = offs[node.lineno - 1] + node.col_offset
    e = offs[node.end_lineno - 1] + node.end_col_offset
    return data[s:e].decode("utf-8")


def splice(src, edits):
    """按（行号, 字节列号）从后往前替换，支持跨行。edits: (l1,c1,l2,c2,repl)"""
    lines = src.split("\n")
    for (l1, c1, l2, c2, repl) in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        if l1 == l2:
            b = lines[l1 - 1].encode("utf-8")
            lines[l1 - 1] = b[:c1].decode("utf-8") + repl + b[c2:].decode("utf-8")
        else:
            head = lines[l1 - 1].encode("utf-8")[:c1].decode("utf-8")
            tail = lines[l2 - 1].encode("utf-8")[c2:].decode("utf-8")
            lines[l1 - 1:l2] = [head + repl + tail]
    return "\n".join(lines)


def collect_edits(tree, src, trans, offs, data):
    """返回 (edits, 命中的英文片段列表, 跳过说明列表)"""
    docstrings = set()
    joined_parts = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
        if isinstance(node, ast.JoinedStr):
            for v in node.values:
                joined_parts.add(id(v))

    # ---- 1) f-string：整节点重建 ----
    edits = []
    handled_ranges = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        parts = [v for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
        if not any(p.value in trans for p in parts):
            handled_ranges.append((offs[node.lineno - 1] + node.col_offset, offs[node.end_lineno - 1] + node.end_col_offset))
            continue
        new_parts = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                new_parts.append(ast.Constant(value=trans.get(v.value, v.value)))
            else:
                new_parts.append(v)
        try:
            new_src = ast.unparse(ast.Expression(body=ast.JoinedStr(values=new_parts)))
        except Exception as exc:
            return None, [], [f"unparse 失败 line {node.lineno}: {exc}"]
        edits.append((node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, new_src, node, None))
        handled_ranges.append((offs[node.lineno - 1] + node.col_offset, offs[node.end_lineno - 1] + node.end_col_offset))

    # ---- 2) 普通字面量（含跨行隐式拼接）----
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docstrings or node.value not in trans or id(node) in joined_parts:
            continue
        s = offs[node.lineno - 1] + node.col_offset
        e = offs[node.end_lineno - 1] + node.end_col_offset
        inside = any(s >= rs and e <= re_ for (rs, re_) in handled_ranges)
        if inside:
            continue
        zh = trans[node.value]
        if '"' in zh:
            return None, [], [f"译文含双引号 line {node.lineno}"]
        edits.append((node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, json.dumps(zh, ensure_ascii=False), node, zh))
    return edits, [(e[5].value if isinstance(e[5], ast.Constant) else "".join(p.value for p in e[5].values if isinstance(p, ast.Constant) and isinstance(p.value, str))) for e in edits], []


def main():
    if "--rollback" in sys.argv:
        i = sys.argv.index("--rollback")
        if i + 1 >= len(sys.argv):
            log("❌ 用法: --rollback <备份文件>")
            return 1
        bak = sys.argv[i + 1]
        if not os.path.isfile(bak):
            log(f"❌ 备份不存在: {bak}")
            return 1
        backup(RUN)
        shutil.copy2(bak, RUN)
        log(f"✅ 已回滚: {RUN} <= {bak}")
        return 0

    check_only = "--check" in sys.argv
    pairs = json.load(open(MAP_FILE, encoding="utf-8"))
    trans = {}
    for en, zh in pairs:
        trans.setdefault(en, zh)

    src = open(RUN, encoding="utf-8").read()
    tree = ast.parse(src)
    offs, data = _byte_offsets(src)

    edits, hits, problems = collect_edits(tree, src, trans, offs, data)
    if edits is None:
        log("❌ 无法生成替换：")
        for p in problems:
            log("   " + p)
        return 1

    log(f"映射条目: {len(trans)}   f-string 整节点重建: {sum(1 for e in edits if e[6] is None)}   普通字面量: {sum(1 for e in edits if e[6] is not None)}")

    if "--list" in sys.argv:
        for (l1, c1, l2, c2, repl, node, zh) in sorted(edits):
            log(f"  {l1:>6}-{l2:<6} {repl[:100]!r}")

    if check_only:
        log("\n[--check 模式] 未写入任何改动。")
        return 0
    if not edits:
        log("\n✅ 无需改动（英文原文已不存在，可能已汉化）。")
        return 0

    new_src = splice(src, [(e[0], e[1], e[2], e[3], e[4]) for e in edits])
    out_path = RUN
    if "--out" in sys.argv:
        out_path = sys.argv[sys.argv.index("--out") + 1]
        open(out_path, "w", encoding="utf-8").write(new_src)
        import subprocess
        r = subprocess.run([PYC, "-m", "py_compile", out_path], capture_output=True, text=True)
        log(f"写入 {out_path}，py_compile rc={r.returncode}")
        log(r.stderr[-2500:])
        return 0 if r.returncode == 0 else 1
    bak = backup(RUN)
    open(RUN, "w", encoding="utf-8").write(new_src)
    rc = os.system(f"{PYC} -m py_compile {RUN} 2>/dev/null")
    if rc != 0:
        log("❌ 语法校验失败，自动回滚。")
        shutil.copy2(bak, RUN)
        return 1
    log(f"✅ 已写入（备份 {bak}）")
    log("⚠️  重启 gateway 生效：/command/s6-svc -r /run/service/gateway-default")
    return 0


if __name__ == "__main__":
    sys.exit(main())
