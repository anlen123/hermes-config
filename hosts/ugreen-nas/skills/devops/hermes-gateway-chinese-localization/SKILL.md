---
name: hermes-gateway-chinese-localization
description: 把本机 Hermes Gateway 面向用户的英文系统消息汉化成中文（QQBot 等平台）。用幂等补丁脚本 /opt/data/scripts/hermes-cn-patch.py，升级(hermes update)后重跑即可恢复汉化。当用户说「系统消息改成中文」「它怎么还回英文」「汉化 Hermes 提示语」时使用。
---

# Hermes Gateway 系统消息汉化

## 背景

Hermes v0.10.0（非 git 安装，装到 /opt/hermes）的 gateway 面向用户的消息是硬编码英文，
包括：审批请求、更新确认、Agent 闲置/卡住提示、正在处理中、主频道提示、
会话过大、各类快捷命令回复、语音模式、压缩/分支/用量等。

把这些改成中文需要改源码 `/opt/hermes/gateway/run.py`。
**`hermes update` 会覆盖该文件**，所以汉化用「映射表 + 补丁脚本」方式保存，升级后重跑即可。

## 关键文件

| 文件 | 作用 |
|---|---|
| `/opt/data/scripts/hermes-cn-patch.py` | 补丁脚本（幂等、自动备份、语法校验、失败回滚） |
| `/opt/data/hermes-cn-strings.json` | 英文→中文 逐行映射表（79 条） |
| `/opt/data/hermes-cn-deletions.json` | 多行拼接合并后需整行删除的英文残行（2 条） |
| `/opt/data/gateway-run.py.bak-<ts>` | 改动前的源码备份 |

## 用法

```bash
# 应用汉化
/opt/hermes/.venv/bin/python /opt/data/scripts/hermes-cn-patch.py

# 只检查、不写入
/opt/hermes/.venv/bin/python /opt/data/scripts/hermes-cn-patch.py --check

# 回滚到某个备份
/opt/hermes/.venv/bin/python /opt/data/scripts/hermes-cn-patch.py --rollback /opt/data/gateway-run.py.bak-xxx
```

**改完必须重启 gateway 才生效**（源码改动不像 approvals.mode 那样即时读取）：
```bash
/command/s6-svc -r /run/service/gateway-default
```
⚠️ gateway 由 s6 监管（服务名 `gateway-default`），**容器 PID 1 是 s6-svscan，不是 gateway**
（v0.20.1 docker 版；旧文档里 `kill -TERM 1` 的说法已过时，那是杀容器 init）。
重启会中断当前对话，几秒后 s6 自动拉起；NAS 的 Docker 重启策略是兜底。
**重启后必须新开会话**才能看到汉化（旧会话的系统消息已发过）。

## 升级后怎么办

`hermes update` 之后重跑补丁脚本即可。脚本会：
1. 已汉化的行（`s in zh_pool`）跳过 —— 幂等
2. 仍是英文的行按映射替换
3. 输出「未匹配」行清单，提示新版可能改了代码，需人工补映射

## 核心实现要点（踩过的坑）

1. **必须按行匹配，不能整串 replace**。多行 f-string 拼接的块（如主频道提示、闲置提示）
   会被拆成单行，整块映射永远匹配不上。逐行处理才稳。
2. **缩进归一化**：用 `ln.strip()` 做 key 匹配，写入时保留原行缩进
   （`indent + zh.strip()`）。源文件缩进可能和映射表不一致。
3. **重复文本**：同一英文行可能出现在多个缩进层级（如 `iteration {_iter_n}/{_iter_max}).`
   在 9484 和 9489 都是）。用 `setdefault` 建 map + 遍历所有行，即可全部替换。
   注意结尾 `.` 与 `. ` 的细微差异，需各加一条映射。
4. **残行删除**：汉化把多行拼成单行后，原英文的续行（`f"or ignore to skip."`）
   要整行删掉，否则留下语法碎片。用 `ln.strip() == d.strip()` 匹配后置 None 过滤。
   遍历时先判 `ln is not None`，否则第二轮会 AttributeError。
5. **幂等判断**：`elif s in zh_pool: skipped += 1` —— 该行已是中文译文就跳过。
6. **语法校验**：`/opt/hermes/.venv/bin/python -m py_compile`，失败自动 `shutil.copy2` 回滚。

## 验证方法

```bash
# 1. 从英文原版演练：还原备份 -> 跑补丁 -> 与手工汉化版逐字节对比
cd /opt/hermes
cp /opt/data/gateway-run.py.bak-<原始> gateway/run.py
/opt/hermes/.venv/bin/python /opt/data/scripts/hermes-cn-patch.py
diff <手工汉化版副本> gateway/run.py   # 应无输出

# 2. 幂等复跑应显示「本次替换: 0  已汉化跳过: 88」
/opt/hermes/.venv/bin/python /opt/data/scripts/hermes-cn-patch.py --check
```

## 环境事实

- Hermes 安装：`/opt/hermes`（非 git 仓库，`git rev-parse` 会失败）
- venv：`/opt/hermes/.venv/bin/python`（Python 3.13.5）
- `HERMES_HOME=/opt/data`；配置 `/opt/data/config.yaml`
- 当前汉化规模：88 行替换 + 2 行删除（79 条映射）

## ⚠️ 2026-09-26 版本跳变：补丁已被覆盖，映射需重做

容器镜像/Hermes 升级到 **v0.20.1 (2026.8.13)** 后：

| 项 | v0.10.0（旧） | v0.20.1（现） |
|---|---|---|
| `gateway/run.py` | 10,080 行 / 464 KB | **29,065 行 / 1.4 MB** |
| 文件 mtime | 打补丁后为当天 | 安装时间（Sep 16 15:45 = 上游原文） |
| 中文命中 | 88 行 | **0 行（汉化全丢）** |

- 判断汉化是否还在：`grep -c "[一-龥]" /opt/hermes/gateway/run.py` → 0 即为已被覆盖。
- 跑 `--check` 的实际结果：79 条映射里 **只有 46 条能匹配**新版代码，且脚本的「未匹配」
  计数不可靠（46+0+0≠79），**不能仅凭它判断全量可复现**。
- 结论：升级后**不要直接跑补丁就宣布完成**。必须逐条核对「未匹配/未生效」清单
  （对照新版源码 grep 每条英文原句），重新导出映射表，再跑补丁，最后重启 gateway 实测。
- 上游英文原句也可能被改写（措辞/参数名变化），映射表按「英文原句」匹配，改了就对不上。

## ✅ v0.20.1 起改用官方 i18n 机制（首选方案）

上游已自带 i18n：`agent/i18n.py` 的 `t("dotted.key")`，词典在 `/opt/hermes/locales/<lang>.yaml`，
语言由 `display.language`（config.yaml）决定，env `HERMES_LANGUAGE` 优先级更高，
键缺失回退英文。**所以不要再手改 `gateway/run.py` 里的英文串**，
只要「把裸串换成 t()」+「往词典加键」即可，en/zh 都自动正确。

排查「明明是中文设置却还回英文」的正确顺序：

1. `grep -n language /opt/data/config.yaml` → `display.language` 是否 `zh`
   （默认值在 `hermes_cli/config_defaults.py`；`env HERMES_LANGUAGE` 会覆盖它）。
2. 直接跑一句验证：`HERMES_HOME=/opt/data /opt/hermes/.venv/bin/python -c "from agent.i18n import t,get_language;print(get_language());print(repr(t('gateway.reset.header_new')))"`
   —— 需要 `cd /opt/hermes` 或 `sys.path` 带 `/opt/hermes`。
3. 若 i18n 正常、用户仍看到英文，**多半是那段文案压根没走 i18n**（上游裸 f-string）。
   从日志 `/opt/data/logs/gateway.log` 找到 `Sending response (N chars)` 的字符数，
   再用脚本把候选消息拼出来比对长度，即可确认到底哪一段是英文。

### v0.20.1 已知的两处「i18n 覆盖不到」的英文（/new、/reset 横幅）

`/new`、`/reset` 的回复由三段拼成（`gateway/slash_commands.py` 的 reset 处理）：

| 段 | 来源 | 状态 |
|---|---|---|
| `✨ 会话已重置！重新开始。` | `zh.yaml` → `gateway.reset.header_default` | 官方已汉化 |
| `◆ Model / Provider / Context / Endpoint` | `gateway/run.py::_format_session_info` 裸 f-string | **需补丁** |
| `✦ 提示：<随机贴士>` | `hermes_cli/tips.py` 的 `TIPS`（380 条全英文） | **需补丁** |

### ✅ 2026-09-26 扩充：agent 层裸英文（第四类）

用户开了 `display.show_reasoning: true` 看思考过程后，暴露出更多**不走 i18n 的裸英文**，
已一并收进同一个补丁脚本（第 4 步，共 13 处替换）：

| 文件 | 英文原文 | 中文 |
|---|---|---|
| `gateway/run.py` | `-# 💭 Reasoning` / `> 💭 **Reasoning:**` / `💭 **Reasoning:**` | `💭 思考过程` / `💭 **思考过程：**`（3 种 reasoning_style 全覆盖） |
| `gateway/run.py` | `_... (N more lines)_` | `_...（另外 N 行）_` |
| `agent/conversation_loop.py` | `INTERRUPT_WAITING_FOR_MODEL_PREFIX = "Operation interrupted: waiting for model response ("` | `操作已中断：等待模型响应（` |
| `agent/conversation_loop.py` | `...{api_elapsed:.1f}s elapsed).` | `...{api_elapsed:.1f} 秒）。` |
| `agent/conversation_loop.py` | `Operation interrupted during retry (...)` / `handling API error` / `retrying API call after error` / `retrying empty response from model` | 4 条 `操作已中断：…` |
| `agent/conversation_loop.py` | `_HANDOFF_SKIP_FINAL_RESPONSE`（上下文压缩后的收尾语） | 中文两句 |
| `agent/message_sanitization.py` | `"Operation interrupted."` | `"操作已中断。"` |

⚠️ **改 `INTERRUPT_WAITING_FOR_MODEL_PREFIX` 的值是安全的**：`gateway/run.py`、
`tui_gateway/server.py`、ACP 适配器都是 **import 这个常量**来匹配中断元数据的，
改值后它们自动跟随；但**不要**去改那些文件里手写的英文字面量（没有）。

排查思路（可复用）：用户在聊天里看到英文 → 先 `grep -rn "<英文片段>" /opt/hermes --include=*.py`，
命中的若是裸 f-string/常量 → 加进 `UI_TEXT_EDITS` 表；若命中 `t("...")` → 那是词典缺键，补 `locales/zh.yaml`。

新增替换项后**先跑 `--check`**，确认「未匹配 0」再应用；有未匹配说明上游改了写法，
要照新写法改 `old` 字符串（脚本按英文原文精确匹配，改了措辞就对不上）。

修复脚本：**`/opt/data/scripts/hermes-i18n-fix.py`**（幂等，自动备份到 `/opt/data/backups/*.i18n-bak-*`）

```bash
/opt/hermes/.venv/bin/python /opt/data/scripts/hermes-i18n-fix.py --check   # 只看差异
/opt/hermes/.venv/bin/python /opt/data/scripts/hermes-i18n-fix.py           # 应用（含 py_compile + 中英各跑一遍验证）
/command/s6-svc -r /run/service/gateway-default                             # 重启生效
```

脚本做三件事：
1. 往 `locales/en.yaml`、`locales/zh.yaml` 的 `gateway:` 段插入 `session_info.*`
   （model/provider/context/endpoint + source_{config,detected,default}）。
   ⚠️ 幂等判断**不能用** `"session_info:" in text` —— 上游 `/usage` 段有
   `header_session_info:` 会误命中，必须正则锚定行首两空格的 `^  session_info:$`。
2. 把 `_format_session_info` 里 3 处裸 f-string 换成 `t("gateway.session_info.*")`。
3. 给 `hermes_cli/tips.py` 插入 `TIPS_ZH`（380 条中文贴士，源文件
   `/opt/data/cache/tips-zh/part{1,2,3}.zh.json`，按 stride-3 切分后翻译、重建时
   `out[3*j+i]=parts[i][j]` round-robin 还原），并让 `get_random_tip` 在
   `get_language()=="zh"` 时优先取中文贴士。

### 重启 gateway 又不掐断当前回复的技巧

`/command/s6-svc -r ...` 会杀掉 gateway 进程树 → 正在生成回复的 agent 也被杀，回复发不出去。
要「先回复、后重启」，用**带延时的后台进程**：

```bash
# ⚠️ 不要用 setsid/nohup 自己脱离（实测被环境清掉、根本没跑起来）
# ✅ 用 terminal(background=true) 跑一个「先 sleep 再重启」的脚本
terminal(command="bash /opt/data/scripts/delayed-gateway-restart.sh 30", background=true)
```
脚本内容（`/opt/data/scripts/delayed-gateway-restart.sh`）：写日志 → `sleep ${1:-30}` →
`/command/s6-svc -r /run/service/gateway-default` → 再 sleep 25 后对比 `gateway.pid` 是否变化。

要点与坑（2026-09-26 实测）：
1. **延时给 30 秒**，够把回复发出去；给 20 秒也勉强够。
2. **重启会连这个重启脚本一起杀掉**（它是 gateway 的子进程），所以脚本里「重启后的自证」
   那一行**不会**写进日志 —— 别据此判断失败。
3. **判断是否真的重启成功**看两处：日志里有 `已发送重启信号 (s6-svc rc=0)`，
   且 `ps -eo pid,etime,cmd | grep '[h]ermes gateway'` 的 **pid 变了 / etime 归零**。
4. `gateway.pid` 是 JSON（`{"pid":11567,...}`），取值要 `python3 -c "import json;print(json.load(open('/opt/data/gateway.pid'))['pid'])"`，直接 `cat` 拿不到数字。
5. 重启后 **agent 会话会自动恢复**，旧会话里已经发过的英文系统消息不会变。


