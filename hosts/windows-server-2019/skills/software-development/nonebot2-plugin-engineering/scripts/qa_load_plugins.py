#!/usr/bin/env python3
"""全插件加载自检：用 AST 读出 bot.py 的插件清单，逐个加载并报告成败。

用法：
    <机器人解释器> qa_load_plugins.py [仓库根目录]

重构前先跑一次记下基线，重构后再跑，结果必须与基线完全一致。
不要用 `import bot` 拿清单——那会真的把插件全加载一遍，测试脚本再加载就报
`RuntimeError: Plugin already exists`，看起来像满盘失败。
"""

import ast
import os
import sys
import traceback

REPO = os.path.abspath(
    sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\Administrator\Desktop\nb2\my_nonebot2"
)

os.chdir(REPO)
if REPO not in sys.path:
    sys.path.insert(0, REPO)


def read_plugins() -> list:
    """读 PLUGINS 元组；老版本 bot.py 是一堆散落的 load_plugin 调用，按书写顺序退回提取。"""
    with open(os.path.join(REPO, "bot.py"), encoding="utf-8") as handle:
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


PLUGINS = read_plugins()

import nonebot  # noqa: E402
from nonebot.adapters.onebot.v11 import Adapter  # noqa: E402

nonebot.init()
nonebot.get_driver().register_adapter(Adapter)

ok, failed = [], []
for name in PLUGINS:
    try:
        plugin = nonebot.load_plugin(name)
        if plugin is None and nonebot.get_plugin(name) is None:
            # 返回 None 通常是「已被别的插件 require 提前拉进来」，不是失败：
            # 但要能从已加载实例里查到它，否则才算真失败。
            raise RuntimeError("插件既没加载出来，也拿不到已加载的实例")
        ok.append(name)
    except Exception as exc:  # noqa: BLE001
        failed.append((name, f"{type(exc).__name__}: {exc}"))
        traceback.print_exc()

print("\n" + "=" * 64)
print(f"插件总数 {len(PLUGINS)}　成功 {len(ok)}　失败 {len(failed)}")
print("\n成功：")
for name in ok:
    print(f"  ✓ {name}")
print("\n失败：")
for name, error in failed:
    print(f"  ✗ {name}\n      {error}")
print("=" * 64)
sys.exit(1 if failed else 0)
