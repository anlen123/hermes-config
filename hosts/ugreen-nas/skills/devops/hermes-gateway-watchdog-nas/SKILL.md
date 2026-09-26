---
name: hermes-gateway-watchdog-nas
description: 排查与守护 UGREEN NAS 容器内 Hermes Gateway（QQBot 等平台）掉线/假死。含日志取证法定位 WebSocket 心跳失效、gateway_state.json 判读、cron --script + --deliver 告警编排，以及本环境「无 systemd/supervisor，kill -TERM 1 不可靠」的关键陷阱。当用户说「QQ机器人不在线」「网关掉线」「hermes gateway 假死」「帮我排查机器人为什么不回消息」时加载。
---

# Hermes Gateway 掉线排查与看门狗（UGREEN NAS 容器）

## 触发场景
- 用户说 QQ / Telegram / Discord 机器人「不在线」「不回复」
- hermes gateway 疑似假死（进程活着但不干活）
- 需要为网关做健康监控

## 环境事实（本机实测，2026-09）
| 项 | 值 |
|---|---|
| 网关进程 | PID 1，`/opt/hermes/.venv/bin/hermes gateway run` |
| 状态文件 | `/opt/data/gateway_state.json` |
| 日志 | `/opt/data/logs/agent.log`（全量）、`/opt/data/logs/errors.log`（警告/错误） |
| internal API | `127.0.0.1:18643`，**只有 dashboard restart endpoint，无发送消息接口**（`/` 和 `/openapi.json` 均 404） |
| systemd | **不存在**（无 systemctl） |
| supervisor | **不存在** |
| QQ DM 告警目标 | 见 `/opt/data/channel_directory.json` 的 `platforms.qqbot[0].id` |

## ⚠️ 最重要陷阱：`kill -TERM 1` 在本环境不可靠

Hermes 关闭日志会写：
```
Exiting with code 1 (signal-initiated shutdown without restart request)
so systemd Restart=on-failure can revive the gateway.
```
**这是错误假设 —— 本容器没有 systemd，也没有 supervisor。**

实测后果：`kill -TERM 1` 之后网关完成 graceful shutdown（`gateway_state: "stopped"`，QQ disconnected，日志打 "Hermes Agent stopped"），但 **PID 1 卡在 `do_epoll_wait`（/proc/1/wchan 可见）不退出**，因此：
- 没有外部监管者来拉起它
- 容器主进程没退出，Docker 的 restart 策略也不会触发
- 结果：**网关彻底停摆，且不会自愈**

其它证据：`hermes gateway status` 明确输出 `(Running manually, not as a system service)`。

**结论**：
- 不要用 `kill -TERM 1` 作为看门狗的自动重启手段
- 安全的恢复方式由用户决定：优先让用户在 UGREEN Docker 界面手动重启容器；或确认容器配了 `restart: always` 后才考虑结束 PID 1
- 正规重启入口应优先探索 internal API 18643 的 restart endpoint

## 步骤 1：日志取证（定位根因）

不要只看进程是否活着 —— 要看日志的**断连模式变化**。

```bash
# 1. 当前时间 vs 状态文件时间，判断静默多久
date; cat /opt/data/gateway_state.json

# 2. QQBot 断连全序列（看模式）
grep -iE "qqbot" /opt/data/logs/agent.log | grep -iE "closed|timed out|4009|Reconnecting" | tail -40

# 3. 每天断连次数对比（判断是否退化）
grep -c '2026-09-24.*WebSocket' /opt/data/logs/errors.log
grep -c '2026-09-25.*WebSocket' /opt/data/logs/errors.log

# 4. 断连时间戳，看间隔是否规律
grep "WebSocket error: WebSocket closed" /opt/data/logs/errors.log | tail -15 | awk '{print $1, $2}'
```

### 判读要点（实测结论）
| 现象 | 含义 |
|---|---|
| `code=4009 Session timed out`，**每 3600 秒整一次** | ✅ **正常**。QQ 会话最长有效期 1 小时，服务端到期主动踢，客户端清 session 重 identify。24h 共 23 次属健康 |
| `WebSocket error: WebSocket closed`（**无错误码**），**每 61 秒一次** | ❌ **故障**。无码 = 底层连接被直接掐断；61 秒 = QQ 服务端心跳超时阈值 → **Hermes 侧 heartbeat/ACK 未被正常响应** |
| 断连次数一日内暴涨（如 23 → 279，12 倍） | ❌ 模式退化 |
| 日志完全静默但进程活着 | ❌ **假死**：重连逻辑被拖死（await 永不返回等），事件循环僵死 |

**一句话根因模板**：QQ 网关心跳保活失效 → 服务端每 60s 误判离线并断开 → 反复断连把重连逻辑拖死 → 进程「活着但不干活」。

诱因排序：① 本机到 `api.sgroup.qq.com` 网络抖动（NAS 家宽掉包）② access_token 续期与心跳竞态 ③ QQ 平台侧限流/风控。

## 步骤 2：状态文件判读

```bash
cat /opt/data/gateway_state.json
```
关键字段：
- `updated_at` / `platforms.qqbot.updated_at` — **是否停滞**（主判据）
- `gateway_state` — `running` / `draining` / `stopped`
- `platforms.qqbot.state` — `connected` / `disconnected`
- `active_agents` — >0 表示有会话在跑，会延长 drain

⚠️ **坑**：`gateway_state: "connected"` 可能停留在假死前的旧时间戳，**不要只看 state 值为 connected 就判定健康**，必须比对 `updated_at`。

⚠️ **坑**：draining/stopped 期间 `updated_at` **仍会被刷新**，因此单纯看「是否停滞」会把 draining 误判为健康。检测逻辑应额外检查 `gateway_state` 是否为 `stopped`/`draining`。

## 步骤 3：告警通道（cron 编排）

**错误方式**：`hermes message send ...` —— **该命令不存在**。`hermes` 子命令只有：
`chat model gateway setup whatsapp login logout auth status cron webhook doctor dump debug backup import config pairing skills plugins memory tools mcp sessions insights claw version update uninstall acp profile completion dashboard logs`

**正确方式**：用 `hermes cron` 的 `--script` + `--deliver` 组合：
- `--script` 跑检测脚本，stdout 注入 prompt（脚本输出 `STATUS=OK` 时让 prompt 保持静默）
- `--deliver` 指定投递目标，如 `qqbot:AB581D...`

```bash
hermes cron create "every 5m" \
  --name "网关健康检查" \
  --script /opt/data/scripts/gateway_health_check.py \
  --deliver qqbot:<QQ_TARGET_ID> \
  "根据脚本输出判断：STATUS=OK 则不要发消息；STATUS=WARN/CRIT 则把 REASONS 内容作为告警发出。"
```

参考实现：`/opt/data/scripts/gateway_health_check.py`（输出 `STATUS=` / `ANNOUNCE=` / `ACTION=` / `STALL_MIN=` / `PID_ALIVE=` / `DISCONNECTS_5MIN=` / `REASONS=`）。

检测三层：① `updated_at` 停滞（WARN≥5min，CRIT≥15min）② PID 1 存活 ③ errors.log 近 5 分钟断连密度（≥10 次异常）。

## ⚠️ 测试陷阱（我实际踩过，代价是真实重启了一次网关）

测「缓冲期满→自动重启」分支时，我手工把 state 文件里的 `crit_since` 往前调了 200 秒，结果脚本**真的执行了 `kill -TERM 1`，把生产网关停了**。

**规则：测任何会触发副作用的代码路径，必须先加 dry-run 开关或把重启函数打桩，绝不用篡改时间戳来"模拟"时间流逝。**

安全的测试顺序：
1. 先测纯判定逻辑（把 `updated_at` 改成过去时间 → 只读，无副作用）✅
2. 再测去抖（立即重跑）✅
3. **测重启动作前，先把 `restart()` 换成 print 打桩**，或加 `DRY_RUN=1` 环境变量

另注：测试改 `gateway_state.json` 时要先备份，且注意网关可能在你测试期间**正常刷新该文件覆盖你的假数据**（这本身说明网关健康）。

## 步骤 4：恢复

网关已停摆时的恢复选项（**必须让用户确认，不要擅自操作**）：
- A. 等容器自动重启 —— 通常无效，因为 PID 1 没退出
- B. `kill -9 1` 结束容器主进程 —— 仅当确认容器有 `restart: always` 时可行
- C. **推荐**：让用户在 UGREEN Docker 界面手动重启容器 —— 最稳妥

## 附带发现
- 重启时若 CLI 会话正开着，errors.log 会打印 `Shutdown diagnostic — other hermes processes running`，列出被牵连的进程
- `agent.log` 中 `tools.checkpoint_manager: Git command skipped: git init (working directory not found: ...)` 属噪音，可忽略
- 网关 stop 时会给 QQ 目标发 shutdown 通知（用户会看到上下线提示）
