"""打印 apscheduler 已注册的定时任务与下次运行时刻 —— 证明「每天几点」真的落在那个点。

用法（必须在仓库根目录跑：nonebot.init() 按 cwd 读 .env / .env.dev）：

    <Python313> probe_scheduler_jobs.py [--repo <仓库根>] [插件模块 ...]

只加载 nonebot_plugin_apscheduler 时只打印它自己注册的任务；要检查某个插件的定时任务就把模块路径
一起传进来，例如：

    <Python313> probe_scheduler_jobs.py plugins.nonebot_plugin_group_historian.nonebot_plugin_group_historian

输出：调度器时区、每个任务的 id / trigger / next_run_time（含时区偏移）。看到 `hour=0 minute=0`
之后仍要看 next_run_time —— `hour=0` 只说明「参数写了 0」，next_run_time 才证明「下一次真的落在
今天/明天零点」。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

DEFAULT_REPO = r"C:\Users\Administrator\Desktop\nb2\my_nonebot2"


def parse_args(argv: list[str]) -> tuple[str, list[str]]:
    """返回 (仓库根, 要额外加载的插件模块列表)。"""
    repo = os.environ.get("NB_REPO", DEFAULT_REPO)
    modules: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--repo":
            index += 1
            if index >= len(argv):
                raise SystemExit("--repo 后面要跟仓库路径")
            repo = argv[index]
        else:
            modules.append(arg)
        index += 1
    return repo, modules


def prepare(repo: str) -> Path:
    path = Path(repo)
    if not path.is_dir():
        raise SystemExit(f"仓库目录不存在：{repo}")
    sys.path.insert(0, str(path))
    os.chdir(path)  # nonebot.init() 按 cwd 找 .env / .env.dev
    return path


async def main() -> None:
    repo, modules = parse_args(sys.argv[1:])
    prepare(repo)

    import nonebot
    from nonebot.adapters.onebot.v11 import Adapter

    nonebot.init()
    nonebot.get_driver().register_adapter(Adapter)
    nonebot.load_plugin("nonebot_plugin_apscheduler")

    for module in modules:
        nonebot.load_plugin(module)
        print(f"已加载插件：{module}")

    from nonebot_plugin_apscheduler import scheduler

    scheduler.start()
    print(f"调度器时区：{scheduler.timezone}")

    jobs = scheduler.get_jobs()
    print(f"共 {len(jobs)} 个定时任务：")
    for job in jobs:
        next_run = job.next_run_time
        print(f"  · {job.id}")
        print(f"      触发器：{job.trigger}")
        if next_run is None:
            print("      下次运行：未排期（任务被暂停或触发时间已过）")
            continue
        print(f"      下次运行：{next_run}")
        print(f"      抄核：hour={next_run.hour} minute={next_run.minute} tzinfo={next_run.tzinfo}")

    scheduler.shutdown(wait=False)


asyncio.run(main())
