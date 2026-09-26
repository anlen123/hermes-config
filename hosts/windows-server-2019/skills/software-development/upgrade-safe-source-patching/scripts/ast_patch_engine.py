#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可重放的 AST 源码替换引擎（从实战验证过的 Hermes 汉化补丁精简而来）。

用法（示例：替换 <TARGET> 里的字符串常量，映射表为 [[en, zh], ...]）：

    python3 ast_patch_engine.py --target /path/app/module.py --map mapping.json --check
    python3 ast_patch_engine.py --target /path/app/module.py --map mapping.json --out /tmp/try.py
    python3 ast_patch_engine.py --target /path/app/module.py --map mapping.json
    python3 ast_patch_engine.py --rollback /path/backup.py

设计要点（踩坑换来的，改动前先读 SKILL.md 的「关键坑」）：
  * 按「英文原文值」匹配 → 幂等，跑过一遍后第二遍 0 命中
  * 普通字面量（含跨行隐式拼接）→ 带引号整体替换
  * f-string → 重建整个 JoinedStr 节点，用 ast.unparse 出源码
  * 写入前 py_compile 校验，失败自动回滚
"""
import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import time

PY = sys.executable


def log(m):
    print(m, flush=True)


def _byte_offsets(src):
    data = src.encode("utf-8")
    offs, pos = [], 0
    for ln in src.split("\n"):
        offs.append(pos)
        pos += len(ln.encode("utf-8")) + 1
    return offs, data


def _abs(offs, lineno, col):
    return offs[lineno - 1] + col


def span_text(offs, data, node):
    return data[_abs(offs, node.lineno, node.col_offset):_abs(offs, node.end_lineno, node.end_col_offset)].decode("utf-8")


def splice(src, edits):
    """edits: (l1, c1, l2, c2, repl)，按位置倒序替换（字节列偏移）。"""
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


def fstr_escape(s):
    """译文放进 f-string 片段：转义换行/制表符/反斜杠/引号，并双写花括号。"""
    out = s.replace("\\", "\\\\")
    out = out.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    out = out.replace('"', '\\"')
    return out.replace("{", "{{").replace("}", "}}")


def collect_edits(tree, offs, trans):
    docstrings, joined_parts = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
        if isinstance(node, ast.JoinedStr):
            for v in node.values:
                joined_parts.add(id(v))

    edits, handled = [], []
    # 1) f-string：整节点重建
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        parts = [v for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
        rs, re_ = _abs(offs, node.lineno, node.col_offset), _abs(offs, node.end_lineno, node.end_col_offset)
        if not any(p.value in trans for p in parts):
            handled.append((rs, re_))
            continue
        new_parts = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                new_parts.append(ast.Constant(value=trans.get(v.value, v.value)))
            else:
                new_parts.append(v)
        try:
            new_src = ast.unparse(ast.Expression(body=ast.JoinedStr(values=new_parts)))
        except Exception as exc:  # noqa: BLE001
            log(f"⚠️  unparse 失败 line {node.lineno}: {exc}（跳过）")
            continue
        edits.append((node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, new_src))
        handled.append((rs, re_))

    # 2) 普通字面量（含跨行隐式拼接）
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docstrings or id(node) in joined_parts or node.value not in trans:
            continue
        s, e = _abs(offs, node.lineno, node.col_offset), _abs(offs, node.end_lineno, node.end_col_offset)
        if any(s >= rs and e <= re_ for (rs, re_) in handled):
            continue
        zh = trans[node.value]
        if '"' in zh:
            log(f"⚠️  译文含双引号，跳过 line {node.lineno}")
            continue
        edits.append((node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, json.dumps(zh, ensure_ascii=False)))
    return edits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--map", required=True, help="[[en, zh], ...] JSON")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--out", help="写到临时文件并只跑 py_compile（试跑）")
    ap.add_argument("--rollback", help="从备份还原")
    a = ap.parse_args()

    if a.rollback:
        shutil.copy2(a.rollback, a.target)
        log(f"✅ 已回滚 {a.target} <= {a.rollback}")
        return 0

    trans = {}
    for en, zh in json.load(open(a.map, encoding="utf-8")):
        trans.setdefault(en, zh)

    src = open(a.target, encoding="utf-8").read()
    tree = ast.parse(src)
    offs, _ = _byte_offsets(src)
    edits = collect_edits(tree, offs, trans)
    log(f"映射条目 {len(trans)}，本次命中替换 {len(edits)}")

    if a.list:
        for e in sorted(edits):
            log(f"  {e[0]:>6} {e[4][:100]!r}")
    if a.check:
        log("[--check] 未写入。")
        return 0
    if not edits:
        log("✅ 无需改动（英文原文已不存在 = 已打过补丁）。")
        return 0

    new_src = splice(src, [(e[0], e[1], e[2], e[3], e[4]) for e in edits])

    if a.out:
        open(a.out, "w", encoding="utf-8").write(new_src)
        r = subprocess.run([PY, "-m", "py_compile", a.out], capture_output=True, text=True)
        log(f"试跑 {a.out}: py_compile rc={r.returncode}")
        if r.returncode:
            log(r.stderr[-2000:])
        return r.returncode

    bak = f"{a.target}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(a.target, bak)
    open(a.target, "w", encoding="utf-8").write(new_src)
    rc = subprocess.run([PY, "-m", "py_compile", a.target], capture_output=True, text=True).returncode
    if rc:
        log("❌ 语法校验失败，自动回滚")
        log(subprocess.run([PY, "-m", "py_compile", a.target], capture_output=True, text=True).stderr[-2000:])
        shutil.copy2(bak, a.target)
        return 1
    log(f"✅ 已写入（备份 {bak}）—— 记得重启目标进程才生效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
