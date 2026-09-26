---
name: hermes-session-reset-nas
description: 排查与调整本机 Hermes 的会话记忆过期策略 —— 回答「机器人聊一会儿就忘了之前说什么」「它是多少分钟自动开新会话」，修改 idle_minutes / at_hour / mode，以及「改完要不要重启 gateway」「等机器人任务跑完再重启」的判据。当用户抱怨 QQ/Telegram 机器人失忆、问会话保留多久、或要重启 gateway 时加载。
---

# Hermes 会话重置（记忆过期）策略

## 触发场景
- 用户说「聊了一段时间他就忘了之前的对话」「它是多少分钟自动开新会话」
- 用户想调整记忆保留时长（延长或缩短）
- 需要解释「为什么明明没聊多久也失忆了」

## 关键位置（本机实测，2026-09）

配置在 `/opt/data/config.yaml`，**靠近文件末尾**（约 294 行），不在顶部的
`agent:` 段里 —— 用 grep 找 `session_reset` 最快：

```yaml
session_reset:
  mode: both          # idle | daily | both
  idle_minutes: 1440  # 默认 24 小时
  at_hour: 4          # 每天凌晨 4 点定时重置
group_sessions_per_user: true
```

## ⚠️ 第一要点：模式是 `both`，两个条件各自独立触发

**只调 `idle_minutes` 不等于就能留得更久**。`mode: both` 意味着：

1. 空闲超过 `idle_minutes` → 下次开口开新会话
2. 每天 `at_hour` 点 → 无论是否在聊，**强制切新会话**

所以「我把空闲改成 7 天」之后，每天凌晨 4 点那刀**依然生效** —— 如果用户天天聊，
每天还是断一次上下文。想让空闲时长真正说了算，必须同时把
`mode` 改成 `idle`（只按空闲重置）。

| mode | 行为 |
|---|---|
| `idle` | 只按空闲时间重置 |
| `daily` | 只在每天 `at_hour` 重置 |
| `both` | 两者都启用（默认） |

## 分钟换算速查

| 目标 | idle_minutes |
|---|---|
| 12 小时 | 720 |
| 24 小时（默认） | 1440 |
| 3 天 | 4320 |
| 7 天 | 10080 |
| 30 天 | 43200 |
| 1 年 | 525600 |

## 步骤

1. **先备份**
   ```bash
   cp /opt/data/config.yaml /opt/data/config.yaml.bak-$(date +%Y%m%d-%H%M%S)
   ```
2. 改 `/opt/data/config.yaml` 的 `session_reset` 段（用 patch 工具，别用 sed）
3. **验证解析**（务必用绝对路径 venv + HERMES_HOME，见下方坑）
   ```bash
   cd /opt/hermes && HERMES_HOME=/opt/data .venv/bin/python -c "
   from hermes_cli.config import load_config
   sr = load_config().get('session_reset', {})
   print('session_reset:', sr)
   print('idle =', sr.get('idle_minutes'), '=', sr.get('idle_minutes')/1440, '天')
   "
   ```
4. **必须重启 gateway 才生效** —— 见下方「最重要的坑」。
   改完 config 后必须重启，否则跑的还是内存里的旧策略。
   重启会**清空当前会话上下文**，重启前先告知用户。

## ⚠️⚠️ 第二要点：`session_reset` 改完必须重启 gateway

**这一点极易搞错，而且我曾给用户答错过一次，务必按下面的证据链判断。**

`session_reset` 是在 **gateway 启动时被快照进内存**的，不是每轮读取：

| 位置 | 代码 | 含义 |
|---|---|---|
| `gateway/run.py:578` | `self.config = config or load_gateway_config()` | `GatewayRunner.__init__` **只加载一次** config |
| `gateway/session.py:509` | `self.config = config` | `SessionStore` 持有这个内存对象 |
| `gateway/session.py:593` | `policy = self.config.get_reset_policy(...)` | 读的是**内存对象**，不重读文件 |

所以：**改 `session_reset` → 必须重启 gateway**。

### 对比：`approvals.mode` 不需要重启

`approvals.mode` 是**每次调用现读** `config.yaml`（`tools/approval.py` 的
`_get_approval_mode()`），改完立即生效，**不用重启**。

**别把这两个混为一谈** —— 这是最容易搞错的地方：

| 配置 | 生效方式 | 要重启吗 |
|---|---|---|
| `approvals.mode` | 每次调用现读 config.yaml | ❌ 不用 |
| `session_reset.*` | gateway 启动时快照进内存 | ✅ 必须重启 |

### 怎么判断改动到底生效了没

对比 **gateway 进程启动时间** 和 **config.yaml 修改时间**：

```bash
ps -o pid,lstart,etime,cmd -p 1        # gateway 是容器 PID 1
stat -c '%y  %n' /opt/data/config.yaml
```

**若进程启动时间早于 config 修改时间 → 改动还没生效，需要重启。**
（实测案例：进程 22:40:46 启动，config 23:43:39 修改 → 相差约 1 小时，旧策略仍在内存里。）

## 重启前：确认 QQ/机器人那边真的空闲了

用户常说「等它任务跑完再重启」。别靠猜，**查 session DB**（`/opt/data/state.db`）。

`state.db` 表结构（实测）：
- `sessions`：列有 `id, source, user_id, started_at, ended_at, end_reason, message_count, tool_call_count, ...`
  - ⚠️ **没有 `updated_at` 列**（`SessionEntry` 有，DB 里没有），别写错
- `messages`：列有 `session_id, role, content, tool_name, timestamp, ...`
  - ⚠️ **时间列叫 `timestamp`，不叫 `created_at`**；`started_at`/`timestamp` 都是
    **Unix 时间戳浮点数**，需要用 `datetime.fromtimestamp()` 转换

**「正在忙」的判据**：

```bash
cd /opt/hermes && HERMES_HOME=/opt/data .venv/bin/python -c "
import sqlite3, time
c = sqlite3.connect('/opt/data/state.db'); c.row_factory = sqlite3.Row
now = time.time()
print('--- 最新 5 条消息（看距今多久） ---')
for r in c.execute('select session_id, role, timestamp, substr(content,1,50) c from messages order by timestamp desc limit 5'):
    d = dict(r); d['ago_s'] = round(now - d['timestamp'], 1); d.pop('timestamp'); print(d)
print()
print('--- qqbot 会话 ---')
for r in c.execute(\"select id, started_at, ended_at, end_reason, message_count from sessions where source='qqbot' order by started_at desc limit 4\"):
    print(dict(r))
"
```

判读：
- **最新消息距今 < 约 60s** → 大概率还在干活，**别重启**
- **有 `source='qqbot'` 且 `ended_at IS NULL` 且 `message_count > 0`** → 活跃会话
- 注意：`message_count: 0` 且 `ended_at IS NULL` 的空会话是刚创建还没用，不算忙碌
- 辅助：`tail -20 /opt/data/logs/agent.log`，看有无 `Auxiliary ... flush_memories/compression`
  （记忆刷新/压缩中 = 正在收尾，等它跑完）

### ⚠️ 极易误判：任务可能已经被「打断」而不是在跑

实测踩坑：QQ 会话显示在下载文件，但实际上**任务早已被打断**。证据在消息里：

```
{"status": "interrupted", "output": "[execution interrupted — user s..."}
```

**用户中途发新消息会打断当前回合**，会话随即被 `compression` 或 `session_reset` 终结
（看 `sessions.end_reason`）。此时虽然看起来「刚在下载」，其实**没有任何传输进程在跑**。

**务必用进程表二次确认**，别只看 DB：

```bash
ps -eo pid,etime,cmd | grep -Ei "aria2|curl|wget|quark|qk " | grep -v grep
```

### ⚠️ 重启会丢「跑在会话里的」长任务

- **后台进程**（`terminal(background=true)` 起的）→ 重启 **不一定** 影响
- **跑在会话回合里的**长任务（如 `qk` 下载/转存）→ **会话一断就被丢弃**
- `SessionStore._is_session_expired()` 有 `_has_active_processes_fn` 保护
  （`gateway/run.py:598` 用 `process_registry.has_active_for_session`），
  但那只覆盖进程注册表里的任务，**不覆盖会话回合本身**

**结论：重启前先确认没有半途的长任务，否则可能留下残缺文件。**

## 关键坑

- **解释器必须用绝对路径 venv**：`/opt/hermes/.venv/bin/python`。
  系统 `python3` 没有 `hermes_cli` 模块，直接 import 会失败。
- **必须带 `HERMES_HOME=/opt/data`**，否则 config 解析到别处、读不到你的改动。
- **不要在顶部 `agent:` 段找** —— `gateway_timeout` / `gateway_timeout_warning`
  是**单轮请求超时**，和「记忆保留多久」完全无关，别混淆。
- **「聊一会就忘」不一定是开新会话**：如果用户聊的时间很短却失忆，更可能是
  **上下文压缩（compaction）** —— 对话变长后早期内容被摘要，细节模糊。
  这种情况会话没断，用 `session_search` 仍能捞回历史。
  排查时先问清「隔了多久回来」，再判断是 idle 重置还是压缩。

## 排查话术

判断是哪一类失忆：

| 用户描述 | 大概率原因 | 处理 |
|---|---|---|
| 隔了一天才回来，之前全忘 | `idle_minutes` 到期 | 调大 idle_minutes |
| 每天固定时间点前后失忆 | `at_hour` 定时重置 | 改 mode 为 idle |
| 一口气聊很久，越聊越记不清早期细节 | 上下文压缩 | 非配置问题，说明 session_search 可回溯 |
| 刚聊完没几分钟就忘 | 罕见，查 gateway 是否重启过 | 看 `/opt/data/logs/agent.log` |

## 附带

`group_sessions_per_user: true` 表示群里**按用户**隔离会话（每人在群里各有独立上下文），
与重置策略无关，但常一起被问到。
