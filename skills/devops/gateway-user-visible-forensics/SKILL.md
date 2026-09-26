---
name: gateway-user-visible-forensics
description: 用户说「机器人变了/还是有英文/怎么没反应」时先取证再改。
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [hermes, gateway, qqbot, i18n, troubleshooting, forensics, display, logging]
    related_skills: [hermes-gateway-chinese-localization, hermes-gateway-watchdog-nas, hermes-session-reset-nas, hermes-async-job-progress]
---

# 用户视角行为取证（QQ/Telegram 等聊天网关）

## When to Use（触发场景）

用户用**主观感受**描述问题，而不是给你报错：

* 「还是有英文」／「提示还是英文」
* 「怎么没反应了」／「？？？」／「你是不是卡死了」
* 「之前还会…现在怎么不…了」（思考过程、进度、回复速度）
* 「它是不是变了」

⚠️ **第一原则：不要凭猜改配置或改文案。** 用户往往指的是**某一条具体消息**，
先重建「用户实际看到了什么」，再决定改哪里。本机实测踩过：为查「还有英文」
翻了四层源码才发现真正的英文来自 agent 层，而非已汉化的 gateway 层。

## Step 1 — 取证：用户实际收到了什么

```bash
# 一键：近 60 分钟发出去的消息 + 发送日志 + 英文占比最高的几条
/opt/hermes/.venv/bin/python <skill_dir>/scripts/user_visible_dump.py --minutes 60
```

等价的手工查询（跨会话、按时间）：

```python
import sqlite3, datetime
con = sqlite3.connect('/opt/data/state.db'); cur = con.cursor()
for sid, ts, c in cur.execute("""select session_id,timestamp,content from messages
        where role='assistant' and content is not null and content!=''
          and timestamp > <unix起点> order by timestamp"""):
    print(datetime.datetime.fromtimestamp(ts).strftime('%H:%M:%S'),
          (c or '').replace('\n', ' ')[:200])
```

判读要点：

| 现象 | 含义 |
|---|---|
| `display_kind='hidden'` | 内部消息，**不会外发**，排查时排除 |
| DB 有记录但日志无 `Sending response` | 没发出去（或发送失败），不能当作「用户看到了」 |
| 日志 `send retry 1/3 … Send failed: /v2/users/1/messages 请求的资源不存在` | 发给了不存在的 home channel（如 `1`），**静默失败** |
| 一轮之间日志里没有任何 `Sending response` | 那一整段时间用户在屏幕上**一片空白** |

`/opt/data/logs/gateway.log` 和 `agent.log` 都要看：`gateway.log` 会随重启轮转，
`agent.log` 更全。关键行：`response ready: ... time=191.5s api_calls=23 response=894 chars`。

## Step 2 — 分类归因（三类，别混）

### A. 「还是有英文」→ 定位到具体字符串，再查它走不走 i18n

1. 用 Step 1 找到那条消息，抄出**原文**。
2. 查词典：`grep -n "<原文片段>" /opt/hermes/locales/zh.yaml`
   * 有 → 语言设置没生效，查 `display.language`（env `HERMES_LANGUAGE` 优先级更高）。
   * 无 → **这段文案压根没走 i18n**，是上游裸串。

v0.20.1 实测「i18n 覆盖不到」的**两层**：

| 层 | 位置 | 典型内容 |
|---|---|---|
| gateway 层 | `gateway/run.py::_format_session_info`、`hermes_cli/tips.py` | `/new` 横幅的 `◆ Model/Provider/Context` 块 + 380 条英文贴士 |
| **agent 层** | `agent/conversation_loop.py` | `Operation interrupted: waiting for model response (`（第 169 行常量）、`Operation interrupted during retry` / `…handling API error` / `…retrying empty response`、`agent/message_sanitization.py` 的 `"Operation interrupted."` |

**只补了 gateway 层就宣布汉化完成 = 一定还会被投诉。** 改法：加 `locales/{en,zh}.yaml`
词典键 + 把裸串换成 `t("...")`（别一刀切换成中文字面量，否则英文环境也变中文）。

### B. 「怎么没反应了」→ 长任务静默

看那一轮的 `response ready: time=Xs api_calls=N`：本机实测一个回合 **191 秒 / 23 次工具调用**，
期间日志零 `Sending response` → 用户全程黑屏，只能发「？？？」催。

两个常见成因，分开处理：

* 工具阶段太长（检索、侦察、转存、递归列目录）→ **纪律：动手前先发一条中文汇报**，
  之后每几分钟报一次进度，别等用户来问。
* 模型不产出「调工具前的旁白」→ 见 C。

### C. 「思考过程变没了」→ 先查模型，别先改文案

| | 旧 `deepseek-chat` | 新 `deepseek-v4-flash`（本机实测） |
|---|---|---|
| 调工具前的中文旁白（interim message） | ✅ 有（「我先看看…」），会实时推给用户 | ❌ 无，直接闷头调工具 |
| 真思维链 `reasoning` | ❌ 0 条 | ✅ 有，但被 `display.show_reasoning: false` 藏住 |

```sql
-- 有没有思维链（对比不同 session 立刻看出模型差异）
select count(*) from messages where session_id='<sid>'
   and reasoning_content is not null and reasoning_content!='';
-- 模型什么时候换的
select session_id, model, first_seen, last_seen from session_model_usage
 order by last_seen desc limit 10;
```

再 `diff /opt/data/config.yaml.bak-* /opt/data/config.yaml` 看 `model.default` 何时变。
（本机 `display.show_reasoning` 在**所有历史备份里都是 false**，从没开过 →
用户「以前看得到」几乎一定是旁白，不是思维链。）

⚠️ **`show_reasoning: true` 未必符合「全中文」偏好**：`deepseek-v4-flash` 的 reasoning 是
**英文**原生输出，开了就是大段英文刷屏。想恢复中文「思考过程」，优先「要求模型每步动手前
先用一句中文说明在做什么」，把开 reasoning 当作备选而不是默认推荐。

## Step 3 — 改之前先算清「这个开关当前解析成什么」

`display.*` 不是单一来源，解析顺序（第一个非 None 胜出）：

```
display.platforms.<platform>.<key>  →  display.<key>  →  _PLATFORM_DEFAULTS[platform][key]  →  _GLOBAL_DEFAULTS[key]
```

源码：`/opt/hermes/gateway/display_config.py`。
**qqbot 不在 `_PLATFORM_DEFAULTS` 里** → 直接落到全局默认（`tool_progress=all`、
`show_reasoning=false`、`interim_assistant_messages=true`…）。
想给 QQ 单独开某项，要写 `display.platforms.qqbot.<key>`。

确认当前值（不靠记忆）：

```bash
grep -n -A20 "^display:" /opt/data/config.yaml
/opt/hermes/.venv/bin/python -c "import yaml;d=yaml.safe_load(open('/opt/data/config.yaml'));print(yaml.dump(d.get('display'),allow_unicode=True,sort_keys=False))"
```

## 本机环境自检（跑任何本机服务脚本前先做，30 秒）

| 检查 | 命令 | 期望 |
|---|---|---|
| 服务活着吗 | `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5005/login` | `302` |
| 127.0.0.1 不通？ | 改试 docker 网桥网关 IP `172.17.0.1`（本机实测 QAS 5005 / OpenList 5445 都在这里） | `302` / `200` |
| 用哪个 python | `/opt/hermes/.venv/bin/python`（本机脚本依赖 `requests`；系统 `python3` 没有） | 能 import requests |

本机脚本普遍靠**环境变量**切地址，别改代码：

```bash
export QAS_URL=http://172.17.0.1:5005
export OPENLIST_URL=http://172.17.0.1:5445
```

> 容器网络模式会变（本机曾从 `host` 变 bridge，之后 `127.0.0.1` 全不通）。
> **每个任务开头先测端口**，别凭记忆/印象下结论。
> 夸克网盘链路（转存+下载）详见用户自有技能 `quark-nas-download` / `quark-saveas-autoclassify`。

## 坑（都实测过）

1. **DB 里有记录 ≠ 用户看到了**：投递失败会静默（发到不存在的 home channel 直接 400）。
2. **别用「日志里没有」下结论**：`gateway.log` 重启即轮转，用 `agent.log` 交叉验证。
3. **别越界改用户的表达偏好**：用户抱怨英文时，先把清单列出来让用户确认「是不是这几条」，
   再动手；不要擅自重写无关文案（本机用户明确反感「擅自扩大任务范围」）。
4. **改完源码必须重启 gateway 才生效**；用脱离进程组的延时重启，避免掐断当前回复：
   `setsid nohup sh -c 'sleep 25; /command/s6-svc -r /run/service/gateway-default' >/dev/null 2>&1 </dev/null &`
5. 读 DB 用 `/opt/hermes/.venv/bin/python`（系统 python3 缺依赖）。
6. 排查完**给结论 + 选项**（表格），别丢一堆日志给用户看 —— 本机用户偏好结论先行 + 表格。

## 相关技能（同一领域，本机为用户自有，改动前先征得用户同意）

| 技能 | 覆盖 |
|---|---|
| `hermes-gateway-chinese-localization` | 汉化落地：`hermes-cn-patch.py` / `hermes-i18n-fix.py`、词典与重启流程 |
| `hermes-gateway-watchdog-nas` | 网关掉线/假死（真·不说话） |
| `hermes-session-reset-nas` | 会话过期导致「失忆」 |
| `hermes-async-job-progress` | 长任务异步化 + `@@PROGRESS@@` 进度推送 |

本技能专注**「在线状态下用户感知到的行为变化」的取证与归因**；定位到具体成因后，
去上表挑对应技能执行修复。

## 支持文件

* `scripts/user_visible_dump.py` —— 输入时间窗，输出：窗口内发出去的助手消息（含英文占比
  与 ASCII 词清单）、网关日志的 `Sending response` / 发送失败行。纯标准库，直接跑即可。
