#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 /opt/data/hermes-i18n-zh-overlay.yaml 里的中文条目合并进 Hermes 的 locales/zh.yaml。

用途：Hermes 官方 zh 词典里有几十条仍是英文（未翻译），这个脚本把它们补成中文。
升级（hermes update / 换容器镜像）会覆盖 locales/zh.yaml，重跑本脚本即可恢复。

用法：
  /opt/hermes/.venv/bin/python hermes-i18n-apply.py           # 应用
  /opt/hermes/.venv/bin/python hermes-i18n-apply.py --check   # 只报告差异
"""
import os
import shutil
import sys
import time

import yaml

ZH = "/opt/hermes/locales/zh.yaml"
EN = "/opt/hermes/locales/en.yaml"
OVERLAY = "/opt/data/hermes-i18n-zh-overlay.yaml"


def flat(node, prefix="", out=None):
    if out is None:
        out = {}
    if isinstance(node, dict):
        for k, v in node.items():
            flat(v, f"{prefix}.{k}" if prefix else str(k), out)
    elif isinstance(node, str):
        out[prefix] = node
    return out


def unflat(flat_map):
    root = {}
    for key, val in flat_map.items():
        parts = key.split(".")
        cur = root
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
            if not isinstance(cur, dict):
                raise ValueError(f"键冲突: {key}")
        cur[parts[-1]] = val
    return root


def main():
    check = "--check" in sys.argv
    overlay = yaml.safe_load(open(OVERLAY, encoding="utf-8")) or {}
    zh_raw = yaml.safe_load(open(ZH, encoding="utf-8")) or {}
    en_flat = flat(yaml.safe_load(open(EN, encoding="utf-8")) or {})
    zh_flat = flat(zh_raw)

    changed, same, missing_in_en = [], [], []
    for key, val in overlay.items():
        cur = zh_flat.get(key)
        if cur == val:
            same.append(key)
            continue
        if key not in en_flat:
            missing_in_en.append(key)
        changed.append(key)
        zh_flat[key] = val

    print(f"overlay 条目: {len(overlay)} | 需要写入: {len(changed)} | 已是中文: {len(same)}")
    if missing_in_en:
        print(f"⚠️  en.yaml 里没有的键（仍会写入，仅提示）: {missing_in_en}")
    if not changed:
        print("✅ 无需改动。")
        return 0
    for k in changed:
        print(f"  {k}\n     旧: {en_flat.get(k, '(无)')[:70]}\n     新: {overlay[k][:70]}")
    if check:
        print("\n[--check 模式] 未写入。")
        return 0

    bak = f"{ZH}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(ZH, bak)
    with open(ZH, "w", encoding="utf-8") as f:
        yaml.safe_dump(unflat(zh_flat), f, allow_unicode=True, sort_keys=True, default_flow_style=False, width=10000)

    # 回读校验
    back = flat(yaml.safe_load(open(ZH, encoding="utf-8")) or {})
    bad = [k for k, v in overlay.items() if back.get(k) != v]
    if bad:
        print(f"❌ 回读校验失败: {bad} —— 恢复备份")
        shutil.copy2(bak, ZH)
        return 1
    print(f"✅ 已写入 {ZH}（备份 {bak}），回读校验通过 {len(overlay)} 条。")
    print("⚠️  i18n 语言与词典在进程内缓存 —— 需重启 gateway 才能生效：")
    print("    /command/s6-svc -r /run/service/gateway-default")
    return 0


if __name__ == "__main__":
    sys.exit(main())
