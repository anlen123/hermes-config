#!/usr/bin/env python3
"""匹配器指纹导出 / 对账：机器级证明重构没有改变任何触发行为。

用法：
    <机器人解释器> dump_matchers.py <仓库根目录> <输出.json>        # 导出指纹
    <机器人解释器> dump_matchers.py --compare <前.json> <后.json>   # 对账出差异表

指纹 = 每个匹配器的「类型 / 优先级 / block / 权限 / 规则」，规则 repr 里的内存地址先归一化。

**对账前必须消掉集合顺序噪声**：权限里的 `Dependent(call=X)`、`Command(cmds=(...))` 的命令项、
`Rule(...)` 里的多个检查，内部都是集合，repr 顺序每个进程都不一样，直接比会得到一堆假差异。
本脚本的 --compare 会做规范化。判定「这是噪声不是改动」的硬办法是**同一份代码连跑两次**，
自己就会变的那些一律不算改动。

拿老版本仓库作基线时注意：那时的 bot.py 可能没有 PLUGINS 元组，而是散落的 load_plugin 调用。
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sys
import traceback

# 规则 / 权限 repr 里会出现对象地址，两次进程必然不同
ADDRESS = re.compile(r"0x[0-9a-fA-F]+")


def read_plugins(repo: str) -> list:
    """读 PLUGINS 元组；老版本 bot.py 是一堆散落的 load_plugin 调用，按书写顺序退回提取。"""
    with open(os.path.join(repo, "bot.py"), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "PLUGINS":
            return list(ast.literal_eval(node.value))

    names = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "attr", "") != "load_plugin" or not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            names.append(arg.value)
    if not names:
        raise SystemExit("bot.py 里既没有 PLUGINS 也没有 load_plugin 调用")
    return names


def dump(repo: str, out_path: str) -> None:
    """在指定仓库里加载全部插件，把匹配器指纹写进 json。"""
    repo = os.path.abspath(repo)
    os.chdir(repo)
    if repo not in sys.path:
        sys.path.insert(0, repo)

    import nonebot
    from nonebot.adapters.onebot.v11 import Adapter

    nonebot.init()
    nonebot.get_driver().register_adapter(Adapter)

    failed = []
    for name in read_plugins(repo):
        try:
            plugin = nonebot.load_plugin(name)
            if plugin is None and nonebot.get_plugin(name) is None:
                # 返回 None 通常是「已被别的插件 require 提前拉进来」，不是失败
                raise RuntimeError("插件既没加载出来，也拿不到已加载的实例")
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{name} :: {type(exc).__name__}: {exc}")
            traceback.print_exc()

    plugins = {}
    for plugin in nonebot.get_loaded_plugins():
        rows = []
        for matcher in plugin.matcher:
            rule = ADDRESS.sub("0xADDR", repr(matcher.rule))
            rows.append(
                {
                    "type": matcher.type,
                    "priority": matcher.priority,
                    "block": matcher.block,
                    "permission": ADDRESS.sub("0xADDR", repr(matcher.permission)),
                    "rule": rule,
                    "rule_hash": hashlib.sha1(rule.encode("utf-8")).hexdigest()[:12],
                }
            )
        plugins[plugin.module_name] = rows

    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(
            {"plugins": plugins, "load_failed": failed},
            handle,
            ensure_ascii=False,
            indent=1,
            sort_keys=True,
        )
    print(f"写入 {out_path}")
    print(f"插件 {len(plugins)}　加载失败 {len(failed)}")
    for item in failed:
        print("  ✗", item)


def canon(rows: list) -> list:
    """规范化成与集合顺序无关的指纹。"""
    out = []
    for row in rows:
        perms = sorted(re.findall(r"call=([A-Za-z_0-9]+)\)", row["permission"]))
        found = re.search(r"Command\(cmds=\((.*)\)\)", row["rule"])
        if found:
            cmds = sorted(re.findall(r"\('([^']*)',\)", found.group(1)))
            rule_key = "Command(" + "|".join(cmds) + ")"
        else:
            rule_key = row["rule_hash"]
        out.append([row["type"], row["priority"], row["block"], perms, rule_key])
    return sorted(out, key=repr)


def compare(before_path: str, after_path: str) -> int:
    """比对两份指纹，打印差异表。"""
    with open(before_path, encoding="utf-8") as handle:
        before = json.load(handle)["plugins"]
    with open(after_path, encoding="utf-8") as handle:
        after = json.load(handle)["plugins"]

    only_before = sorted(set(before) - set(after))
    only_after = sorted(set(after) - set(before))
    if only_before or only_after:
        print("只在重构前存在:", only_before)
        print("只在重构后存在:", only_after)

    changed = [
        name
        for name in sorted(set(before) & set(after))
        if canon(before[name]) != canon(after[name])
    ]
    print(f"\n指纹有变化的插件（{len(changed)}）:", changed if changed else "无")
    for name in changed:
        print(f"\n  【{name}】")
        print("    前:", canon(before[name]))
        print("    后:", canon(after[name]))
    print("\n每一条变化都要能说出「这是本次有意改的」；说不出来就是弄坏了。")
    return 1 if changed or only_before or only_after else 0


def main() -> None:
    args = sys.argv[1:]
    if len(args) == 3 and args[0] == "--compare":
        sys.exit(compare(args[1], args[2]))
    if len(args) == 2:
        dump(args[0], args[1])
        return
    raise SystemExit(__doc__)


main()
