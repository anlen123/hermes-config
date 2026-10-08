#!/usr/bin/env python3
"""功能清单导出：从**运行中的插件**里取真实触发条件与定时任务。

用法：
    <机器人解释器> dump_features.py [仓库根目录] [输出.json]

用户问「列出所有功能 / 命令表」时跑这个，不要凭记忆罗列——匹配器与定时任务才是事实。

坑：自定义 Rule 函数（`rule=Rule(_is_xxx)`）在 repr 里看不出条件，只会显示「任意消息」。
这些必须回到源码里 grep 出来（例如 note 是只认小写的 `note`、jev_judge 是「判断」前缀、
repeater 是同一消息连续 N 次、biliav 是消息里含 av/BV 号），否则功能表会写错。
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import traceback

DEFAULT_REPO = r"C:\Users\Administrator\Desktop\nb2\my_nonebot2"


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


def describe_rule(rule_repr: str) -> str:
    """把 Rule 的 repr 翻译成人能读的触发条件。"""
    parts: list = []

    for found in re.finditer(r"Command\(cmds=\((.*?)\)\)", rule_repr):
        cmds = re.findall(r"\('([^']*)',\)", found.group(1))
        if cmds:
            parts.append("命令: " + " / ".join(cmds))
    for found in re.finditer(r"Regex\(regex='(.*?)', flags=", rule_repr):
        parts.append("正则: " + found.group(1).replace("\\\\", "\\"))
    for found in re.finditer(r"Startswith\(prefixes=\((.*?)\)\)", rule_repr):
        parts.append("开头是: " + " / ".join(re.findall(r"'([^']*)'", found.group(1))))
    for found in re.finditer(r"Endswith\(suffixes=\((.*?)\)\)", rule_repr):
        parts.append("结尾是: " + " / ".join(re.findall(r"'([^']*)'", found.group(1))))
    for found in re.finditer(r"Keyword\(keywords=\((.*?)\)\)", rule_repr):
        parts.append("含关键词: " + " / ".join(re.findall(r"'([^']*)'", found.group(1))))
    for found in re.finditer(r"Fullmatch\(pattern='(.*?)', flags=", rule_repr):
        parts.append("完全匹配: " + found.group(1))
    if "ToMe()" in rule_repr:
        parts.append("需要 @机器人")
    if not parts:
        # 自定义 Rule 函数，条件看不到，必须回源码里查
        parts.append("任意消息（自定义规则，需回源码确认条件）")
    return " 且 ".join(parts)


def main() -> None:
    repo = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_REPO)
    out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.environ.get("TEMP", "."), "features.json")

    os.chdir(repo)
    if repo not in sys.path:
        sys.path.insert(0, repo)

    import nonebot
    from nonebot.adapters.onebot.v11 import Adapter

    nonebot.init()
    nonebot.get_driver().register_adapter(Adapter)
    for name in read_plugins(repo):
        try:
            nonebot.load_plugin(name)
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    rows = []
    for plugin in sorted(nonebot.get_loaded_plugins(), key=lambda item: item.name):
        module = sys.modules.get(plugin.module_name)
        lines = [line.strip() for line in (getattr(module, "__doc__", "") or "").splitlines()]
        desc = next((line for line in lines if line), "")
        matchers = [
            {
                "触发": describe_rule(repr(matcher.rule)),
                "类型": matcher.type,
                "优先级": matcher.priority,
                "拦截": matcher.block,
            }
            for matcher in plugin.matcher
        ]
        rows.append({"插件": plugin.name, "模块": plugin.module_name, "说明": desc, "匹配器": matchers})

    jobs = []
    try:
        from nonebot_plugin_apscheduler import scheduler

        for job in scheduler.get_jobs():
            jobs.append(
                {
                    "任务": job.id,
                    "模块": getattr(job.func, "__module__", ""),
                    "触发": str(job.trigger),
                }
            )
    except Exception as exc:  # noqa: BLE001
        jobs.append({"错误": f"{type(exc).__name__}: {exc}"})

    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump({"plugins": rows, "jobs": jobs}, handle, ensure_ascii=False, indent=1)

    print("═══ 定时任务 ═══")
    for job in jobs:
        print(f"  {job.get('任务')}  |  {job.get('触发')}  |  {job.get('模块')}")
    print("\n═══ 各插件的触发条件 ═══")
    for row in rows:
        if not row["匹配器"]:
            print(f"\n[{row['插件']}]  （无消息匹配器，只有定时任务）")
            continue
        print(f"\n[{row['插件']}]  {row['说明'][:60]}")
        for matcher in row["匹配器"]:
            print(f"    · {matcher['触发']}   (优先级 {matcher['优先级']}, 拦截={matcher['拦截']})")
    print(f"\n写入 {out_path}　插件 {len(rows)}　定时任务 {len(jobs)}")
    print("提示：显示「任意消息（自定义规则…）」的条目要回源码确认条件后再写进功能表。")


main()
