#!/usr/bin/env python
"""给自带 migrations/ 的 nonebot 插件建表。

等价于 nonebot-plugin-orm 启动时做的事（init -> check -> upgrade），但去掉了交互式确认 ——
后台启动的机器人没有 TTY，ORM 的 `click.confirm("目标数据库未更新到最新迁移, 是否更新?")` 会直接
Abort，导致插件起不来。所以新装带 migrations 的插件后，先用本脚本建表，再重启机器人。

用法：
    <Python313> scripts/orm_migrate_plugin.py <插件模块路径> [仓库根]

例：
    <Python313> scripts/orm_migrate_plugin.py \\
        plugins.nonebot_plugin_group_historian.nonebot_plugin_group_historian

前提：`.env.dev` 里已配 SQLALCHEMY_DATABASE_URL（建议用绝对路径的 sqlite+aiosqlite:///...）。
脚本要在仓库根运行（nonebot.init() 会自己读 .env / .env.dev）。
"""

from __future__ import annotations

import asyncio
import os
import sys
from argparse import Namespace
from pathlib import Path

DEFAULT_REPO = Path(r"C:/Users/Administrator/Desktop/nb2/my_nonebot2")


def main() -> int:
    plugin_module = sys.argv[1] if len(sys.argv) > 1 else None
    if not plugin_module:
        print(__doc__)
        return 2
    repo = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_REPO

    sys.path.insert(0, str(repo))
    os.chdir(repo)

    import nonebot
    from nonebot.adapters.onebot.v11 import Adapter

    nonebot.init()
    nonebot.get_driver().register_adapter(Adapter)

    # ORM 依赖 localstore；两个都是插件，先加载好再加载目标插件，ORM 才能发现它的 migrations/
    nonebot.load_plugin("nonebot_plugin_localstore")
    nonebot.load_plugin("nonebot_plugin_orm")
    nonebot.load_plugin(plugin_module)

    import nonebot_plugin_orm as orm
    from nonebot_plugin_orm import migrate
    from sqlalchemy import text
    from sqlalchemy.util import greenlet_spawn

    async def run() -> None:
        print("数据库 URL:", orm.plugin_config.sqlalchemy_database_url)
        orm._init_orm()  # 建引擎、收集各插件的 Model 与迁移目录（启动时的 private 入口）
        cmd_opts = Namespace()
        with migrate.AlembicConfig(stdout=sys.stdout, cmd_opts=cmd_opts) as cfg:
            cmd_opts.cmd = (migrate.upgrade, [], [])
            await greenlet_spawn(migrate.upgrade, cfg)
        async with orm.get_session() as session:
            rows = await session.execute(
                text("select name from sqlite_master where type='table' order by name")
            )
            print("表:", [row[0] for row in rows.fetchall()])
        async with orm.get_session() as session:
            rows = await session.execute(text("select * from alembic_version"))
            print("迁移版本:", [row[0] for row in rows.fetchall()])

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
