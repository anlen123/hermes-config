---
name: hermes-gateway-behavior-diagnostics
description: 诊断机器人行为变化：变安静/没思考过程/仍冒英文。
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [hermes, gateway, qqbot, debugging, forensics, i18n, localization, reasoning]
    related_skills: [hermes-gateway-chinese-localization, hermes-gateway-watchdog-nas, hermes-async-job-progress]
---

# Hermes 网关「用户感知行为」诊断

用 state.db + 网关日志反推**用户实际收到的消息序列**，定位模型切换、长回合静默、
未汉化串，并给出可验证修法。

## When to Use（触发场景）

用户抱怨的不是「机器人挂了」，而是**行为变了**：

- 「怎么没反应了」「？？？」「刚才那几分钟你干嘛去了」
- 「以前回我还有思考过程，现在怎么不回了」
- 「还是有英文，你自己看」「提示还是英文」
- 长回合结束后只收到一条结论，中间像断线

这类投诉**根因在「用户收到的消息序列」里，不在进程状态里**。
所以第一步永远是**取证**：把「用户实际收到了什么」还原出来，再谈修。

> 掉线 / 假死（进程活着但不干活）走另一条线：skill `hermes-gateway-watchdog-nas`。

---

## 一、取证：反推用户实际收到了什么

> 💡 **一键探针（只读，不改任何东西）**：
> `/opt/hermes/.venv/bin/python /opt/data/skills/devops/hermes-gateway-behavior-diagnostics/scripts/gateway-behavior-probe.py`
> 传入 `--session <sid>` 只看某个会话；它会把「旁白缺失 / 思维链有无 / 模型切换 / 长回合静默」一次报出来。

### 1) state.db —— 消息级真相

```bash
/opt/hermes/.venv/bin/python - <<'EOF'
import sqlite3
con = sqlite3.connect('/opt/data/state.db'); cur = con.cursor()

SID = '<会话 id>'
rows = list(cur.execute(
    "select role, content, reasoning, reasoning_content, display_kind, timestamp "
    "from messages where session_id=? order by rowid", (SID,)))
asst = [r for r in rows if r[0] == 'assistant']
print('assistant 总条数', len(asst),
      '| 有正文', sum(1 for r in asst if (r[1] or '').strip()),
      '| 有 reasoning', sum(1 for r in asst if (r[2] or '').strip()),
      '| 有 reasoning_content', sum(1 for r in asst if (r[3] or '').strip()))

# 判断「今天模型被换了没」：按首见时间列出模型 + 推理 token
for r in cur.execute("select session_id, model, task, api_call_count, reasoning_tokens,"
                     " datetime(first_seen,'unixepoch','localtime') "
                     "from session_model_usage order by first_seen desc limit 10"):
    print(r)
EOF
```

`messages` 表关键列：`role` / `content` / `tool_calls` / `reasoning` / `reasoning_content` /
`display_kind`（`hidden` = 不进聊天）/ `timestamp` / `active` / `compacted` / `platform_message_id`。

判读要点：

| 观察 | 结论 |
|---|---|
| assistant 行大多是**空 content**（纯工具调用） | 模型**不输出调工具前的旁白** → 用户眼里「中间一片空白」 |
| `reasoning` / `reasoning_content` 有内容 | 模型**有**思维链，只是被 `display.show_reasoning` 藏住了 |
| 旧会话 reasoning 计数为 0、新会话不为 0 | 模型被换过（旁白型 ↔ 推理型），用 `session_model_usage` 定位切换时刻 |
| 用户说「重启前那条提示」但在 DB 里找不到 | ⚠️ `/new`、`/reset` **会清空该会话消息**，横幅不留在 DB —— 别在 DB 里找它，去源码/词典里拼出来比长度 |

### 2) 日志 —— 投递级真相

```bash
cd /opt/data
grep -n "response ready:\|Sending response\|inbound message:\|C2C message" logs/gateway.log | tail -40
```

- `response ready: platform=qqbot ... time=191.5s api_calls=23 response=894 chars`
  → 这一回合跑了 **191 秒 / 23 次 API 调用**，而这段时间里**一条 `Sending` 都没有**
  ⇒ 用户面对的是**完全静默的 3 分钟**（他会发「？？？」，这是必然结果，不是他脾气差）。
- **对账口径**：`inbound message`（用户发了什么）↔ `response ready`（耗时/轮数/字数）↔
  `Sending response (N chars)`（真的投出去了没有）。
- 投递失败长这样：`QQ Bot API error [400] /v2/users/1/messages: 请求的资源不存在(用户/群已注销)`
  → 投递目标是 Home channel 的占位 ID（`1`）而非真实 C2C 用户，去
  `/opt/data/channel_directory.json` 核对。

---

## 二、常见结论 → 修法

### A. 「没有思考过程了」

两个原因常常**同时**成立，要分别说清：

| 原因 | 机制 | 修法 |
|---|---|---|
| 模型换了 | 旧的 `deepseek-chat` 会在每次调工具前输出一句**中文旁白**（「我先看看…」），网关把它实时推给用户 —— 那才是用户眼里的「思考过程」。新的 `deepseek-v4-flash` 直接调工具、不产出旁白 | ①要求每步动手前用一句中文说明在做什么 ②或换回旧模型 |
| 思维链被藏 | v4-flash **有** reasoning，但 `display.show_reasoning: false` 不展示 | `hermes config set display.show_reasoning true` |

⚠️ 开 `show_reasoning` 前必须告知用户：**思维链是模型原生英文**（DeepSeek 用英文思考），
最多 15 行，嫌吵就 `hermes config set display.show_reasoning false` 关掉。

**生效规则（别搞错）**：`show_reasoning` 是**实况读**的（每条回复经
`_resolve_gateway_display_bool` → `_load_gateway_config()`）→ **改完即时生效**；
但渲染用的 `💭 思考过程：` **标签是源码字符串 → 必须重启 gateway** 才是中文。

### B. 「还是有英文」

三处来源，逐条排查（用户会连**助手正文里的缩写**都算，见第三节）：

1. **官方 i18n 未覆盖的 agent 层裸英文**（`display.language: zh` 管不到 `agent/*.py`）。
   已确认并汉化的 13 处：

   | 文件 | 原文 | 译文 |
   |---|---|---|
   | `gateway/run.py` | `💭 **Reasoning:**`（三种渲染样式：`-# ` 小字 / `> ` 引用 / 代码块） | `💭 **思考过程：**` |
   | 同上 | `_... ({n} more lines)_` | `_...（另外 {n} 行）_` |
   | `agent/conversation_loop.py` | `INTERRUPT_WAITING_FOR_MODEL_PREFIX = "Operation interrupted: waiting for model response ("` | `"操作已中断：等待模型响应（"` |
   | 同上 | `f"{PREFIX}{api_elapsed:.1f}s elapsed)."` | `f"{PREFIX}{api_elapsed:.1f} 秒）。"` |
   | 同上 | 另 5 条 `Operation interrupted…`（retry / API error / empty response，参数位保留） | `操作已中断：…` |
   | 同上 | `_HANDOFF_SKIP_FINAL_RESPONSE`（`Context was compacted…`） | 中文 |
   | `agent/message_sanitization.py` | `"Operation interrupted."` | `"操作已中断。"` |

   **关键技巧**：`INTERRUPT_WAITING_FOR_MODEL_PREFIX` 是**被 import 的常量**
   （`gateway/run.py`、`tui_gateway/server.py`、`acp_adapter/server.py` 都
   `from agent.conversation_loop import` 它来做「中断元数据 vs 正文」的判定）
   → **翻它的值不会破坏判定逻辑，不必改调用点**；千万不要去改调用点里的字面量副本。
   这类串不走 `t()`、没有词典可加 → 只能「原文→译文」精确替换 + 「译文已在文中」判幂等。

2. **`/new`、`/reset` 横幅里残留的 `tokens`**（官方词典已汉化其余部分）。

3. **助手自己正文里的技术缩写**（ETA / MB/s / GB / HDR …）——见下节。

### C. 「怎么没反应了」

长回合（>1 分钟、多轮工具调用）期间用户**收不到任何中间信号**。
异步化只解决「任务在跑」，**不解决「你在干什么」**。
→ 每步动手前先发一句中文旁白，阶段完成时报一次进度（细节见 skill `hermes-async-job-progress`）。

---

## 三、正文语言纪律（本用户的硬要求）

用户会**逐条挑英文，连我自己的正文都算**。原话：「你上面的回复中还是有英文，你自己看」。

- `ETA 7分钟` → `预计还剩 7 分钟`；`23MB/s` → `23 兆/秒`
- **文件名 / 路径 / 命令 / 配置键保持原样**（必须精确可复制），解释性文字全中文
- 说「英文已修好」之前，先把**自己刚发出的那条消息**扫一遍再回：
  ```python
  import re; print(sorted(set(re.findall(r'[A-Za-z][A-Za-z0-9\-\./]*', msg))))
  ```
- 用户说「你自己看」时，**列出候选清单**（哪条消息、哪段文字、能否投递到），
  并补一句「如果你指的是别的某条，截图发我」—— 比反问「你指哪条？」更受欢迎。

---

## 四、汇报与改动纪律

- 结论先行 + 表格 + emoji；按「现象 → 根因 → 修法 → 生效条件」组织。
- 改源码（汉化、渲染）时：**幂等补丁脚本 + 自动备份 + `py_compile` 校验**，
  新串加进同一个补丁脚本，`hermes update` 后重跑即可恢复。
- **重启 gateway 的时机要问/要等**：用户明确要求「下载/解压这类长任务跑完再重启」。
  重启前先确认没有进行中的作业；"先回复后重启" 用脱离进程组的延时重启
  （见 skill `hermes-gateway-chinese-localization`）。
- 报「已修好」必须附**可复现的验证输出**（补丁统计「替换 N / 未匹配 0」、py_compile 通过、
  中英双语各跑一遍），不要只说自己改完了。

---

## 📌 本机维护现状（重要）

以下 skill 都是 **user-owned**，后台策展**无权自动修改**：

`quark-nas-download`、`hermes-gateway-chinese-localization`、`hermes-gateway-watchdog-nas`、
`hermes-async-job-progress`

想让我长期自动维护它们（例如环境变化后自动修正内容），需用户执行：

```bash
hermes curator adopt <skill-name>
```

在此之前，本 skill 是这类诊断知识的**可写落点**；若发现上述 skill 内容已过时，
在回复里指出并建议 adopt，而不是尝试改写。
