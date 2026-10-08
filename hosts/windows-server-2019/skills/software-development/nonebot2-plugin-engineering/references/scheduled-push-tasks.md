# 定时推送类需求（每天几点把某个东西发出来）

骨架：领域插件内注册一个 apscheduler cron 任务 → 到点取数 → 逐个群发 → 记一条结算日志。
SKILL.md 里是先要定下的三个口径与硬规则，这里是实现与验证细节。

## 三个口径先问清楚（用 clarify，推荐项放第一）

| 口径 | 推荐做法 | 为什么 |
| --- | --- | --- |
| 推哪一天的数据 | **刚结束的那一天**（`now().date() - 1 天`） | 零点是新一天的开始，当天数据是空的 |
| 发给谁 | 那一天**有记录的所有群**（DB `distinct group_id`） | 不用用户维护名单；没记录的群跳过 |
| 发什么 | 复用命令那条路的渲染与收尾文案 | 同一功能两套文案，用户迟早问「怎么不一样」 |

先把库查一遍再问，推荐项就能带真实数字（「实测今天 8 个群都有记录」比空泛的推荐有说服力）。
查表直接读插件的 ORM 库（`.env.dev` 里的 `SQLALCHEMY_DATABASE_URL`，本项目是 `data/orm.db`）。

## cron 注册（写在领域插件里）

```python
require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

@scheduler.scheduled_job("cron", hour=0, minute=0, id="<插件>_daily_report")
async def daily_report_job() -> None:
    await send_daily_report()
```

- **时区不用自己传**：`nonebot_plugin_apscheduler` 的 `Config.apscheduler_config` 默认就是
  `{"apscheduler.timezone": "Asia/Shanghai"}`，`.env` 没设 `APSCHEDULER_CONFIG` 就是中国时间。
  要确认就 `print(scheduler.timezone)`。
- 注册发生在**模块 import 时**，那会儿调度器还没 start —— 这是允许的（APScheduler 支持先加任务后启动），
  现有插件（`bilibili_live` 的分钟级轮询）就是这写法。
- 写完**必须**打一次 `job.next_run_time` 看它落在几点，别只信 `hour=0` 的字面值 ——
  用 `scripts/probe_scheduler_jobs.py`。

## 推送函数

```python
async def send_daily_report(day: Optional[date] = None) -> int:
    target = day or (datetime.now().date() - timedelta(days=1))
    groups = await groups_with_data(target)               # distinct group_id
    if not groups:
        logger.info(f"{target} 没有任何群有记录，跳过推送")
        return 0
    try:
        bot = nonebot.get_bot()
    except Exception as exc:                              # 机器人没连上也要安静退出
        logger.warning(f"没有可用的 bot，放弃本次推送：{exc}")
        return 0
    sent = 0
    for group_id in groups:
        try:
            ranking = await get_daily_ranking(group_id, target)
            if not ranking:
                continue
            img = await asyncio.get_event_loop().run_in_executor(None, render, ...)
            await bot.send_group_msg(group_id=int(group_id), message=img + text)
            sent += 1
            await asyncio.sleep(SEND_GAP)
        except Exception as exc:                          # 单个群失败不能影响其他群
            logger.warning(f"群 {group_id} 推送失败：{type(exc).__name__} {exc}")
    logger.info(f"{target} 已推送到 {sent}/{len(groups)} 个群")
    return sent
```

- `day` 参数不是摆设：**你验证时刻的「昨天」往往没有数据**（数据是今天刚记的），只有能显式传日期
  才跑得出真链路；它同时也是「手动补发」的入口。
- 取不到 bot / 取数失败 / 单个群失败，三种都只记日志继续 —— 定时任务最忌因为一处异常整个抛掉，
  那样当场那一批群里剩下的都收不到。
- 渲染类（PIL、词云）用 `run_in_executor` 丢出去，别在事件循环里连续阻塞好几个群的时间。
- 结算日志的「成功 N / 共 M 个群」就是回报给用户的数字，别另算一遍。
- 传给用户的「已推送到 8/8」与「真实发出的消息条数」应当一致，探针里两个都打印出来对账。

## ORM 插件取「那一天有记录的群」

```python
from sqlalchemy import select
from nonebot_plugin_orm import get_session
from .data import DailyMessage           # 用插件自己的模型，别在推送模块里重声明表

async def groups_with_data(day: date) -> list[str]:
    async with get_session() as session:
        rows = await session.execute(
            select(DailyMessage.group_id).where(DailyMessage.timestamp == day).distinct()
        )
        return [str(r[0]) for r in rows.fetchall()]
```

## 验证（不能真发到群）

1. **时刻**：`scripts/probe_scheduler_jobs.py` 打 `scheduler.timezone` + 每个 job 的 `next_run_time`。
2. **真链路**：`nonebot.init()` + 注册适配器 + `load_plugin(...)`，把 `nonebot.get_bot` 换成假对象
   （`nonebot.get_bot = lambda *a, **k: fake_bot`；假 bot 的 `send_group_msg` 只把 kwargs 记下来），
   再 `await send_daily_report(date(YYYY, M, D))`——**传有数据的那天**。
   逐条打印「群号 / 段类型 / 图片字节数 + 文件头」（`b'\xff\xd8\xff'` 是 JPEG、`b'\x89PN'` 是 PNG）。
3. **图**：图片段落盘后用 `vision_analyze` 确认是正常的榜/卡片（有标题、有名次、无方框无空白），
   不要只看字节数。
4. **不破坏上游测试脚手架**：插件自带 `tests/conftest.py` 的那种加载方式——`DRIVER=~none` +
   `LOCALSTORE_BASE_DIR/DATA_DIR/CONFIG_DIR/CACHE_DIR` 指向临时目录 + `SQL_DIALECT=sqlite` +
   `SQLALCHEMY_DATABASE_URL` 指向临时库 + `ALEMBIC_STARTUP_CHECK=false`，然后 `load_plugin("<包名>")`——
   写个临时脚本复刻这几行环境变量跑一遍加载即可。**本机没装 pytest（不要为跑它去装）**，
   「按 conftest 的方式能加载」是能给出的最强证据，报告里就照这个口径说。
5. 交付时把「重启才生效」单独列出来：定时任务的改动更是如此 —— 用户想着「今晚零点看效果」，
   而没重启就还是旧代码、那一晚什么都不会发。
