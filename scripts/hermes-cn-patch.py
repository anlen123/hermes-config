#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hermes Gateway 系统消息汉化补丁

作用：把 gateway/run.py 里面向用户的英文系统消息替换成中文。
特性：
  - 幂等：已汉化的行会被跳过，重复执行无副作用
  - 自动备份：改动前备份到 /opt/data/gateway-run.py.bak-<时间戳>
  - 语法校验：改完用 py_compile 校验，失败自动回滚
  - 可回滚：--rollback <备份文件> 一键还原

用法：
  python3 hermes-cn-patch.py            # 应用汉化
  python3 hermes-cn-patch.py --check    # 只检查，不写入
  python3 hermes-cn-patch.py --rollback /opt/data/gateway-run.py.bak-xxx

适用：Hermes Agent v0.10.0（2026.4.16）。升级后重跑即可。
注意：改完必须重启 gateway 才生效。
"""

import json
import os
import shutil
import sys
import time

HERMES_DIR = "/opt/hermes"
TARGET = os.path.join(HERMES_DIR, "gateway", "run.py")
STRINGS_FILE = "/opt/data/hermes-cn-strings.json"
BACKUP_DIR = "/opt/data"


def log(msg):
    print(msg, flush=True)


def backup(path):
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"gateway-run.py.bak-{ts}")
    shutil.copy2(path, dst)
    return dst


def rollback(bak):
    if not os.path.isfile(bak):
        log(f"❌ 备份文件不存在: {bak}")
        return 1
    backup(TARGET)  # 回滚前也存一份当前状态
    shutil.copy2(bak, TARGET)
    log(f"✅ 已回滚: {TARGET}  <==  {bak}")
    log("⚠️  需重启 gateway 才生效。")
    return 0


def main():
    check_only = "--check" in sys.argv

    if "--rollback" in sys.argv:
        i = sys.argv.index("--rollback")
        if i + 1 >= len(sys.argv):
            log("❌ 用法: --rollback <备份文件路径>")
            return 1
        return rollback(sys.argv[i + 1])

    if not os.path.isfile(TARGET):
        log(f"❌ 找不到目标文件: {TARGET}")
        return 1
    if not os.path.isfile(STRINGS_FILE):
        log(f"❌ 找不到映射文件: {STRINGS_FILE}")
        return 1

    pairs = json.load(open(STRINGS_FILE, encoding="utf-8"))

    # 多行拼接合并后遗留的英文残行，需整行删除
    dels = []
    dels_file = "/opt/data/hermes-cn-deletions.json"
    if os.path.isfile(dels_file):
        dels = json.load(open(dels_file, encoding="utf-8"))

    src = open(TARGET, encoding="utf-8").read()

    # 先删残行（按整行匹配，避免误伤子串）
    removed = 0
    lines = src.split("\n")
    for d in dels:
        for i, ln in enumerate(lines):
            if ln is not None and ln.strip() == d.strip():
                lines[i] = None
                removed += 1
    if removed:
        src = "\n".join(ln for ln in lines if ln is not None)

    applied, skipped, missing = 0, 0, []

    # 按行匹配替换（忽略缩进差异，保留原行缩进）
    lines = src.split("\n")
    en_map = {}
    for en, zh in pairs:
        en_map.setdefault(en.strip(), zh)
    zh_pool = {zh.strip() for _, zh in pairs}

    for i, ln in enumerate(lines):
        s = ln.strip()
        if s in en_map:
            zh = en_map[s]
            indent = ln[:len(ln) - len(ln.lstrip())]
            lines[i] = (indent + zh.strip()) if zh.strip() else zh
            applied += 1
        elif s in zh_pool:
            skipped += 1
    src = "\n".join(lines)

    log(f"映射总数: {len(pairs)}   删除残行: {removed}")
    log(f"本次替换: {applied}   已汉化跳过: {skipped}   未匹配: {len(missing)}")

    if missing:
        log("\n未匹配的行（可能是版本升级导致代码变化，需人工确认）：")
        for m in missing[:20]:
            log("  - " + m.strip()[:90])
        if len(missing) > 20:
            log(f"  ... 另有 {len(missing) - 20} 条")

    if check_only:
        log("\n[--check 模式] 未写入任何改动。")
        return 0

    if applied == 0:
        log("\n✅ 无需改动（全部已汉化）。")
        return 0

    bak = backup(TARGET)
    log(f"\n已备份: {bak}")
    open(TARGET, "w", encoding="utf-8").write(src)

    # 语法校验
    rc = os.system(f"/opt/hermes/.venv/bin/python -m py_compile {TARGET} 2>/dev/null")
    if rc != 0:
        log("❌ 语法校验失败！自动回滚。")
        shutil.copy2(bak, TARGET)
        return 1

    log("✅ 语法校验通过。")
    log("⚠️  需重启 gateway 才生效：")
    log("    nohup bash -c 'sleep 2; kill -TERM 1' >/dev/null 2>&1 &")
    return 0


if __name__ == "__main__":
    sys.exit(main())
