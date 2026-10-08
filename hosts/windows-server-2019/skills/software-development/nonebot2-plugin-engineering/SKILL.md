---
name: nonebot2-plugin-engineering
description: Use when editing this user's nonebot2 QQ bot plugins.
version: 1.0.0
author: hermes-curator
license: MIT
metadata:
  hermes:
    tags: [nonebot2, qq-bot, plugin-refactor, python, windows]
    related_skills: [browserless-web-extraction, codebase-inspection]
---

# nonebot2 QQ 机器人插件工程

用户有一个常驻的 nonebot2 QQ 机器人，会反复提「优化插件 / 加功能 / 重做某个命令」这类需求。
这是那一类工作的做法与硬规则。

## When to Use

命中任一条件就加载本技能：

- 用户提到「qqbot」「nonebot」「机器人」并要给插件加功能、改命令、改文案。
- 用户说「优化/重构某个插件」「把冗余代码删掉」「改成更优雅的代码」。
- 要新增一个命令或新插件，或改 `bot.py` 的插件清单。
- 要从 GitHub 装第三方 nonebot 插件（审计、依赖对账、建表、触发白名单那一整套）。
- 要排查「某条命令没反应 / 回错东西」（匹配器优先级与 `COMMAND_START` 问题）。
- 要在不重启机器人的前提下验证插件改动。

## 硬规则（每次都适用）

- **重启机器人必须由用户发话。** 正在跑的进程一直执行旧代码，改完只说明「需要重启才生效」，
  不要自己去重启或杀 `python bot.py`。用户明确要求「长任务跑完再重启」时，先确认手头任务结束。
- **只做本地 git 提交，绝不 push。** 远端是公开 GitHub 仓库，而 `.env.dev` / `.env.prod` 已被
  跟踪且含真实密钥。发现凭据入库就如实报告并建议轮换，不要自己改远端状态。
- **行为等价优先于优雅。** 触发词、正则、`priority` / `block`、回复文案含义、配置键名、
  被别处 import 的公开名字，一律不动。重构只能动「实现」，不能动「契约」。
- **不擅自扩大范围。** 用户批准的是一个具体动作，不是「把事情全弄好」。
  测试失败先判断「是不是本来就不该在这个环境通过」，如实报告比粉饰有价值；
  不要为了让指标好看而改测试或加装无关依赖。
  上游接口不支持某种输入时（只吃文本的接口塞不进图片），**如实说明 + 给选项**（拒绝 / 先记待办）
  让用户拍板；不要为了「把功能做全」顺手接一个识图模型或新依赖 —— 这类扩范围会被叫停。
- **需求含糊先问再动手。** 用户的原话可能很短、指代不清（「改成直接引用那句话」）。用 clarify 提
  2~3 个彼此独立、每个都把推荐项放第一位的问题，拿到答复再一口气做完 —— 比猜错方向重做便宜得多。
- **改「谁能用」这类权限配置：先问再改，绝不静默替换。** 用户新给的白名单常常和既有设置冲突
  （先给了群 A，过一阵又说「让某人在群 B 能用」）。直接把旧的换掉，用户只有在踩到「那个群怎么不回了」
  时才发现，等于替他做了个他没意识到的决定。做法：把「保留旧的 / 换成新的 / 两个都要」摆成选项问一句
  （推荐项放第一），拿到答复再动手。**设计开关时用按对象/按群的映射表而不是全局一刀切**
  （`{"群号": [用户...]}`，语义是「表里没出现的 = 不限制」）—— 用户几乎必然要按群区分，
  一次到位省一轮返工；这种「否定的那一半」必须写进配置项 docstring。

## 环境

| 项 | 值 |
| --- | --- |
| 仓库 | `C:\Users\Administrator\Desktop\nb2\my_nonebot2`（Windows，shell 是 git-bash） |
| 解释器 | `C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe` |
| 启动 | 手动 `python bot.py`，**没有自启动项**；依赖已 pip 装在 3.13 下 |
| 配置 | `.env` 只写 `environment=dev`，真正的键都在 `.env.dev` |
| 插件清单 | `bot.py` 的 `PLUGINS` 元组，**显式加载，不做目录扫描** |
| 共享工具 | `plugins/common/`，不是 nonebot 插件，永远不会被加载 |

给原生 Windows 程序传路径时用 `C:/Users/...` 形式；用 git-bash 时记得给含空格的路径加引号。
`$TMPDIR` 在不同调用里可能展开成不同值，需要确定路径时直接写绝对路径。

## 重启机器人（用户发话之后）

1. 先确认手头没有中断风险的任务（下载、解压、校验）在跑，再动手。
2. 找进程：按**命令行含 `bot.py`** 找，不要按进程名——同机还有好几个别的 python 进程。
   `powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Select-Object ProcessId,ParentProcessId,CommandLine"`
3. **进程树可能有多层，别只杀一层**：父进程 `python bot.py` 下面还会挂着 spawn 出来的子进程
   （实测见过两种：父进程自己占着 8899 + 一个 `multiprocessing` 子进程；也有中间再夹一层 uvicorn
   reloader 的时候）。**谁占端口不要预设**，按端口查。只杀一层会留下占端口的孤儿。省事的办法是按**命令行特征**一把清掉，
   不用手记 pid：

   ```powershell
   Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
     Where-Object { $_.CommandLine -match 'bot\.py|multiprocessing' } |
     ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
   ```

   特征用 `bot.py`（主进程）与 `multiprocessing`（spawn 出的子进程），不会误伤 Hermes 自己的 python。
   另一条路是 `taskkill /F /T /PID <pid>`，但**必须单斜杠**：本环境禁用了 MSYS 路径转换，
   惯用的 `//F` 会原样传给 taskkill 并报「无效参数/选项 - '//F'」（GBK 乱码，看着像别的毛病）。
   杀完确认 8899 已释放、相关进程数为 0，再启动。

   **这段写成 `.ps1` 文件用 `powershell -File` 跑时，文件内容必须是纯 ASCII**（或存成带 UTF-8 BOM）。
   PowerShell 5.1 把无 BOM 的 `.ps1` 按 GBK 读，`write_file` 写出的 UTF-8 中文注释与中文字符串会让它报
   「字符串缺少终止符 / TerminatorExpectedAtEndOfString」，而且**行号指向别处**，看起来像脚本逻辑坏了、
   其实是编码问题。脚本里的提示文字一律用英文，中文字符串只留在传给 `-Command` 的内联命令里。
4. 确认端口已释放：`netstat -ano | grep ":8899.*LISTENING"` 应无输出。
   **别把这条和后面的启动串进同一条 `&&` 链**：grep 没匹配时退出码是 1，整条链会在这一步静默中断，
   表现成「后面的命令没执行」而不是「端口释放了」。释放检查、启动、验证分三次调用。
5. 重新起来：**用独立进程，别用 Hermes 的 `terminal(background=True)`**（重启 Hermes 网关会把机器人一起
   杀掉，见下面踩坑）。直接跑 `scripts/restart_nb2_bot.ps1`：先按命令行特征清进程，再
   `Start-Process cmd /c "... bot.py >> bot.log 2>&1"`，全程不依赖 Hermes 的进程树。
   日志用 **`>>` 追加而不是 `>`**——覆盖会抹掉上一段记录，而复盘上一个事故往往正需要它。
   `bot.log` 放仓库根目录，被 `*.log` 规则忽略，不脏工作区。
6. **启动要 30~40 秒**（28 个插件逐个 load），十秒时看端口还没监听**不等于**重启失败：先 `tail bot.log`
   等到最后一个插件的 `Succeeded to load plugin` 行出现，再验下面三件事，缺一不可：端口重新 LISTENING
   （这时按端口找**新**进程号）；QQ 侧客户端已连上（`netstat -ano | grep ":8899"` 出现 `ESTABLISHED`，
   对端是 NapCat 的 pid）；日志里出现**重启之后**的真实事件（`[message.private.friend]` /
   `[message.group.normal]`，或某个匹配器的 `running complete`）。
   注意：`OneBot V11 | Bot <QQ号> connected` 这行**不是每次都有** —— 实测同一套环境两次重启，
   一次有、一次没有，而没有那次的机器人照样正常收发消息。缺这行**不等于没连上**，
   别据此判定重启失败或反复重启；以端口 + 真实事件为准。QQ 侧是**反向连接**
   （客户端连到 8899），重启后它会自己重连，不用去动它。
7. 报告时给出新进程号、日志路径、连接时间。**进程号看着小得离谱也别怀疑看错**（PID 会被回收，
   实测见过主进程拿到两位数），以「谁占着 8899」为准。

   **查日志别用带行首锚点的 grep。** `bot.log` 每行都带 ANSI 颜色转义码（`cat -v` 能看到 `^[[32m`），
   `grep '^09-29 22:5'` 会**一条都匹配不到**，现象是「日志里明明有、就是 grep 不到」，
   很容易误判成没启动、白重启一次。按时间子串过滤（`grep -a "22:5"`），或先
   `sed 's/\x1b\[[0-9;]*m//g'` 去色再匹配。

**热重载不可信，别把它当生效手段**：`fastapi_reload=true` 时 uvicorn 收到文件变动会打印
`WatchFiles detected changes in 'bot.py'. Reloading...`，但它**可能就卡在这一步、worker 再也不重启**
（实测卡了二十多分钟仍无新 worker，机器人在跑旧代码）。判断「是否真的重启过」只看 worker 子进程的
创建时间，不要相信那行 `Reloading` 日志。
结论：**改完一律手动重启**，并明确告诉用户「改动还在磁盘上、没生效」；
看到 `Reloading` 就当没发生。

## 工作流程：先立基线，再改，再对账

1. 动手**之前**先跑全插件加载自检，记下「插件总数 N　成功 N　失败 0」。
2. 改完再跑，结果必须与基线**完全一致**。不一致就是这次弄坏的，先修掉再往下。
3. 改过的文件逐个 `python -m py_compile <file>`。
4. 向用户报告时贴自检的**实际输出**（汇总行），不要只说「通过了」。
5. 涉及网络的功能要在真机上打一次真实请求验证，不能只看代码像对。

自检脚本见 `scripts/qa_load_plugins.py`（默认指向该仓库，可用 argv[1] 覆盖路径）。

## 行为等价怎么证（不是「看着没改」）

自检只能证明「还能加载」，证不了「行为没变」。要机器对账，三层缺一不可，命令与脚本见
`references/plugin-refactor-playbook.md`：

1. **匹配器指纹对照**：把重构前的仓库从备份解到临时目录，两边各跑一次
   `scripts/dump_matchers.py`，逐匹配器比对「类型 / 优先级 / block / 权限 / 规则」。
   用 `--compare` 子命令直接出差异表。
2. **先消掉集合顺序噪声**：权限里的 `Dependent(call=X)`、`Command(cmds=(...))` 的命令项、`Rule(...)`
   里的多个检查，内部都是集合，repr 顺序每个进程都不一样。规范化的硬办法是**同一份代码连跑两次**——
   自己就会变的那些一律不算改动，剩下能稳定复现的才是真改动。
3. **中文文案流失检查**：AST 收集所有含中日韩字符的字符串字面量（排除 docstring），比对
   「重构前有、重构后没有」的集合。剩下的每一条都要能解释掉，解释不掉就是误删了用户可见文案。

预期会剩下的「假流失」就那么几类：`print("…%s", x)` 改成 f-string（格式符消失、前缀还在）、
docstring 重写、日志改成 `logger` 参数化。

### 为什么不能用 `import bot` 取插件清单

`bot.py` 的模块体在 import 时就 `nonebot.load_plugin(...)` 全量加载一遍。测试脚本再 import 它，
等于重复加载，全部报 `RuntimeError: Plugin already exists: <name>`，看起来像「27 个插件全失败」，
实际是测试脚本自己的问题。用 `ast` 解析 `PLUGINS` 元组。

插件加载报错时，**先怀疑自己的测试脚手架**（重复加载、cwd 不对、`sys.path` 没含仓库根），
再怀疑被改的插件。

同一个道理的另一面：**导入 `plugins.<插件包>` 会执行它的 `__init__.py`** —— `require(...)`、
`on_command(...)`、定时任务注册都在那一刻发生，没初始化时直接
`ValueError: NoneBot has not been initialized.`，看着像插件坏了，其实是测试脚本的导入姿势不对。
只想单独测 `config.py` / `providers.py` / `render.py` / `history.py` 这类子模块时，按**文件路径**
用 `importlib.util.spec_from_file_location` 加载，绕开包 `__init__`（这些子模块本来就不该依赖
nonebot 运行时，用不着 `nonebot.init()`）；要走真链路（命令、定时任务）时再 `nonebot.init()` +
注册适配器 + `load_plugin`。

## 验证「实际发出去了什么」（别只看代码）

改回复类逻辑（发几条消息、发什么图、文案里有没有链接）时，用假 bot 把消息截下来检查：

- `nonebot.init()` + 注册 onebot.v11 Adapter 后 `nonebot.load_plugin(...)`，然后**直接 await handler
  函数**，不要走匹配器。脚本必须在仓库根目录运行，`nonebot.init()` 会自动读 `.env` / `.env.dev`。
  Adapter 注册行**照抄 `scripts/probe_*.py`**：现装 nonebot 的 `register_adapter` 只收 Adapter 一个
  位置参数，手写 `register_adapter("onebot.v11", Adapter)` 两个参数直接 TypeError。
- **handler 有两种签名，先看清再挑脚本：**
  * `async def handler(bot, event)` —— 用 `scripts/probe_send.py`，按 (bot, event) 调进去。
  * `async def handler()`（无参，nonebot v2 常见写法：从匹配器上下文取 bot / event）—— 按
    (bot, event) 调会直接 `TypeError`；改用 `scripts/probe_command.py`，它把**匹配器实例**的
    `send` / `finish` 换成假函数（`module.<匹配器变量>.send = fake`，实例属性可以覆盖类方法），
    再 `await handler()`，既不用凑上下文也不会真发到 QQ。
  * `(bot, event)` 签名、但 handler 里要读**只有群聊才有的字段**（`event.group_id`）或要发好几条消息
    —— `probe_send.py` 的 FakeEvent 只有 `.message`，读 group_id 直接 `AttributeError`。改用
    `scripts/probe_group_send.py`：它用 `model_validate` 造**真事件**，再把 `module.matcher.send`
    覆盖成假函数，既能读真实字段又不会真发到 QQ。
  handler 内部真的要用 event（读子命令参数之类）时别硬凑上下文，改去测它调用的那几个协程
  （如 `_build_entries()`）—— 上下文脚手架是这一步最容易出错的地方。
- **假 `finish` 必须抛 `FinishedException`。** `Matcher.finish` 是靠抛异常结束当前流程的；换成
  返回 `None` 的假函数，handler 会在 `finish()` 之后继续往下跑（接着查接口、接着写状态），
  于是测出生产里根本不存在的路径、得到一堆假失败。先看 handler 里 `finish()` 是不是唯一出口，
  再决定要不要包 `try/except FinishedException`。`scripts/probe_command.py` 已按真实语义抛异常。
- 假 bot 收 `send(**kwargs)`；handler 有时传 `Message`、有时传裸 `MessageSegment`，
  先用 `Message(seg)` 兜一下再遍历段，否则会得到「'MessageSegment' object is not iterable」。
- 图片段是 `file='base64://...'`：解出来用 PIL 看 `format` / `size` / 字节数，才能确认图到底是什么，
  而不是「大概发出去了」。
- **尺寸不等于画对了：把 PNG 落到 `$TMPDIR` 再用 `vision_analyze` 逐字读回卡片文字。**
  重叠、截断、贴边、越界只有看图才发现；`880x767` 这种数字证明不了排版。一次渲染里把所有分支
  （有基准 / 无基准 / 基准更早 / 失败行 / 零变动）都摆上，一张图看全，少来回几轮。
- **新行为只在罕见数据条件下才出现时，先伪造出那个条件再跑真链路。** 例如「有历史才显示对比」，
  就把运行期状态文件里的基准改成「2 小时前、数值减掉一个固定量」，再真跑一次命令：
  真实接口数值 + 必然出现的差值，一张图同时证明分支连通、画法正确。
- 报「改好了」之前，把实测的「几条消息 / 每段类型 / 图格式尺寸」贴给用户。
- 网络类取数函数**要重试一次并连异常类型一起记日志**：这台机器偶发连接失败（同一 URL 连打三次
  两成一败），而且有些异常的 `str()` 是空的，只写 `{exc}` 会得到一行「：」后面什么都没有的日志，
  根本没法排查。写成 `{type(exc).__name__} {exc}`。B 站接口同样中招（`Cannot connect to host …:443`，
  重试第 2~3 次才成），所以**探针脚本自己也要重试**，否则你会把网络抖动误判成「代码坏了」。
- **一次性触发的手动查询必须自带重试，周期性轮询可以不重试。** 用户发个关键词、点一下就等这一下，
  失败就没有第二次；轮询下一轮会自愈。做法是给手动路径写个薄包装
  （`_fetch_x_for_manual_query()` 循环 3 次 + 间隔），**别去改共用取数函数的行为**（那会连带改掉
  轮询的请求量）。同时**「查询失败」与「查到了但没在播」必须分开记日志**：混在一起时，一次网络抖动
  会被写成「一个都没在播」，现场完全看不出是网的问题。

## 用户说「功能没实现 / 数据没进去」时：先出证据，再改码

先分三种可能：链路真断了 / 链路是好的而用户看的是另一处的数字 / 模型口味问题。**别先改代码。**
按下面顺序取证据，多数情况当场就能定位，也顺手满足用户「我只想看原始请求体」的诉求。

1. **从 `bot.log` 取三层现场证据，一个 grep 就够：**
   - 收到的事件原文：`[message.private.friend]: Message <id> from <uid> '[reply:id=<被引id>]判断 …'`
     —— 引用段与被引 id 都在里面，能证明引用确实到了机器人。
   - 插件自己的判定日志（例：`[jev] <uid> 判断了 N 字（引用）`）—— 证明走的是哪条分支。
   - 机器人实际发出内容：`message_sent` 事件里的 `raw_message` —— 证明它拿什么当的 state。
   （重启用 `> bot.log` 会清空上一次的记录；要复盘更早的事故，先别急着重启。）
   `message_sent` 一行带完整 raw JSON（几十 KB），直接 grep 会把输出撑爆截断；用
   `grep -oE "^[0-9: ]{11}.*?(raw_message': '.{0,80}|group_id': [0-9]+)"` 只抽「时间戳 + 内容片段 +
   群号」，再按时间拼事件线。
2. **把出站请求体截下来给用户看。** 在脚本进程里把模块的 `httpx.AsyncClient` 换成截获桩
   （`post()` 记下 `json=` 再返回一个带 `status_code = 200` / `.json()` 的假响应），然后**走真链路**：
   `model_validate` 造事件 → `await _check_reply(fake_bot, event)` → `await rule(event)` →
   `await handler(...)`，最后 `json.dumps(captured, ensure_ascii=False, indent=2)`。
   拿到的是**代码真正拼出来的 body**，不是手抄的，用户能逐字核对（看图不如给 JSON）。
3. **用「用量指纹」证明截获的就是当时那一份**：拿同样内容再真打一次接口，比对
   `usage.input_tokens` / `output_tokens` 与卡片/日志里记下的数字，一致即同一份 payload。
   比任何解释都有说服力，也能直接排除「中间把字丢了」。
4. **结论不对 ≠ 链路不对。** 判断/打分类接口会拿真实世界知识压过你给的 state：同一个 state，
   问「…叫及时雨」13%，改问「…的名号是及时雨」就 84%。要它「按上文判」，在问题里加
   「只依据上文内容判断」，或用 noul 的 `criteria`（true/false 各写一句「上文支持/不支持」），
   实测能把 46% 抬到 91~93%、反问题压到 3%。给用户解释时把两件事分开说：**链路已验证 + 模型口味如此**，
   并给出「要不要自动加限定/`criteria`」的选项让他拍板。
5. **顺手把请求体写进日志**，让用户以后自己核对：发请求前记一行单行 JSON
   （`json.dumps(payload, ensure_ascii=False)`，单行便于 grep），并对 `api_key` 做一次兜底替换
   （万一 key 被写进 state）。密钥永远不进日志。

## 插件加载顺序会影响裁决

同 `priority` 的两个匹配器抢同一条消息时，先注册的赢；注册顺序就是 `PLUGINS` 的书写顺序。
因此可以重排这个元组的**排版**（分组、加注释、统一命名），**但必须保持原有相对顺序**。
`repeater` 这类 catch-all 匹配器尤其敏感。

这个仓库实测的分层（数字越小越先拿到消息）：功能命令（`判断` / `记事本` / `ai余额`）与史官记账在
`priority` 5 上下，`话痨榜` / `撤回` 在 10，**Hermes 桥接兜底在 98（`block=True`）**，`repeater` 在 99
且不 block。所以「闲聊被 AI 接走、命令仍然优先响应」是设计结果而非谁抢了谁；用户问
「为什么这条没给 AI 回」或「为什么是别的插件回的我」，先看有没有更靠前的匹配器命中。

**「消息里出现 X 就做事」这类需求先判归属，再谈实现。** 用户嘴里的「写个插件」常常其实是
**某个既有插件的附加功能**（B 站开播 → `bilibili_live`；话痨榜 → `nonebot_plugin_group_historian`），
你新建了独立插件之后他会要求「并进那个插件、把新开发的那个删掉」。所以先 grep 谁已经拥有这个领域
（`grep -rn "<关键词>" plugins/ --include=*.py`，再看谁的配置/接口就是干这个的），把功能写进那个插件，
并**复用它的取数函数与文案**（两个入口（定时推送 / 手动触发）必须共用同一个内容构造函数，否则迟早
两边长得不一样）；只有确实没有归属插件时才新建 `plugins/<名字>/`。已经建错了就干脆删掉目录，并把
`bot.py` 的改动还原到**净零** —— `git diff -- bot.py` 里自己的增删必须互相抵消，剩下的差异都属于
更早的未提交改动，报告时要说明「这处不是这次改的」。

**确实要独立插件时的默认档位**：`priority` 与现有功能命令同档（5 上下）、`block=False`、写进
`PLUGINS` 末尾。理由分别是：同 `priority` 时**后注册的先输**，追加到末尾等于不改变任何既有裁决；
`block=False` 才不会让这条消息不再给 Hermes 桥接或别的插件处理。用户没明说就别擅自 block ——
「机器人回复完还该不该给 AI 回」是用户会追问的问题，作为选项提出来让他拍板。

## 重构一类插件

完整派单模板与约束见 `references/plugin-refactor-playbook.md`。核心约束：

- 一次只碰一个插件目录，彼此不重叠；不新增功能、不加依赖、不「顺手优化」逻辑。
- 大范围重构可以并行派子代理：**一个子代理一个（或一组）不重叠的插件目录**，每个都必须跑同一份
  自检脚本并回报 `git diff --stat`。派单时把「哪些路径绝对不许动」明确写进 context。
- 带嵌套 `.git` 的插件目录是 vendored 上游代码，**内部不要动**（升级会覆盖），只清它的垃圾；
  连那些 `.git` 目录也别删——先按下面踩坑里的办法查子模块链接，否则会留下悬空的空壳。
- 删除前先确认没人 import：空文件、只有 `config.py` 没有 `__init__.py` 的目录、从未被调用的函数/常量/import。
- 改完看 `git diff --stat` 确认只动了自己负责的目录。

## 给插件加功能：运行期状态与「和上次对比」类需求

「记住上次查到的 X」「和上次对比」「只推新增的」——这类需求共用一个骨架，规则是硬的：

- **运行期数据放 `<仓库根>/data/<插件名>/<用途>.json`**，不要塞进插件目录：这个仓库是「纯代码库」，
  `/data/` 已被 `.gitignore` 覆盖。落地前 `git check-ignore -v <路径>` 确认一次，别凭记忆假设。
- 路径照 `NOTE_STORAGE_PATH` / `DEEPSEEK_BALANCE_HISTORY` 的写法做成可覆盖：`config.py` 里给
  `_DEFAULT_*` 常量，`load_config()` 返回该键，相对路径按仓库根解析。
- **写入用「临时文件 + `os.replace`」原子替换；读取出任何异常都降级成「没有历史」并记一条日志。**
  状态文件是附加功能，绝不能把主流程带崩。
- **本轮没取到值的条目不写回**，沿用上一次的成功记录 —— 失败一轮不能把基准抹掉，
  下一轮成功时仍要算得出跨次差值。
- **「读-改-写」加 `asyncio.Lock`**：定时任务与手动命令会同时跑同一个状态文件。
- **展示层分三态，别一律写「较上次」**：有基准给「数字 + 百分比」，没基准写「首次查询」，
  基准比整卡基准更早时写明「较 <时刻>」，零变动按持平处理。分组/合计类数字只在口径完整时才给。
- 对比只做展示，**不得改变原有状态判定**（预警线、状态色、推不推送）的语义。
- 交付时主动点明**基准口径**（例如「手动查询与定时推送共用同一个基准」），并给出「各来源各算
  一份」的选项让用户拍板 —— 这是他会追问的第一个问题。
- **要复用别的插件写在 `.env` 里的配置（例如按群绑定的监控名单）时，自己带一份解析函数，别
  `import` 那个插件的 `config` 子模块。** 导入 `plugins.<别的插件>.config` 会先执行它父包的
  `__init__.py`（`require(...)`、注册定时任务），新插件能不能加载就取决于加载顺序。照抄那 20 行
  `key=value` 解析（跳注释、去行内注释、`json.loads` 后按写法归一化）最省事，两个插件也能各自独立测试。
- **配置放在「触发时」重读，不放在模块加载时。** 用户改完 `.env` 不用为配置改动重启机器人
  （重启只用来让新代码生效）；每触发一次读一个小文件的代价可以忽略。解析失败按「没有配置」降级
  并记一条日志，绝不能把 handler 带崩。
- 反查「这个群里配了谁」要把配置**翻转成 `{群号: {对象}}`**（配置本身是 `{对象: [群号]}`），
  一个对象绑多个群时按群查才对得上；群号用 `str(event.group_id)` 与配置里的字符串比。

做法、状态结构设计、渲染坐标账与四层验证打法见 `references/plugin-runtime-state.md`；
无参 handler 的真链路探测用 `scripts/probe_command.py`。

## 给插件加功能：定时推送类需求

「每天几点把这个东西发出来」这类需求，先定三个口径再写码，缺一个就会返工：

- **推哪一天的数据**：零点那一刻新的一天刚开始、当天数据是空的，所以 00:00 发的是**刚结束的那一天**
  （`datetime.now().date() - timedelta(days=1)`）。把推荐项按这个给，并说明原因 —— 用户听到「零点那天的
  数据是空的」会当场认同。
- **发给谁**：优先「那一天有记录的所有群」（DB 查 `distinct group_id`），用户不用维护名单；要白名单
  就让他给群号，别自己编一个。
- **发什么**：复用命令那条路的取数与渲染，别写第二套文案。

实现与验证的硬规则：

- 任务写在**领域插件内部**（见上「先判归属」）；vendored 插件按最小补丁新加一个独立模块 +
  包 `__init__.py` 末尾一行 import，并留 `.bak-localpatch`。
- 推送函数签名带 `day: Optional[date] = None`（默认「今天减一天」）—— **你验证那一刻的「昨天」往往
  没有数据**，只有能显式传日期才跑得出真链路。
- 先取 bot，取不到就记一条 warning 后返回；发之前出现的每一种失败（取数、渲染、单个群发送）都只记
  日志继续，定时任务不能因为一处异常整个抛掉。逐个群之间留 `SEND_GAP`。
- **卡片上「实时抓取」的版块要有「周期内累积」兜底。** 播报时才现拉的实时数据（如每小时播报的
  最近弹幕）撞上网络抖动那一分钟会拿空；用户定下的标准是「抓取失败也要显示最近的 10 条」——
  运行期间就同步累积一份**有序的**最近数据（会话里维护、随状态落盘），取数失败**或取到空列表**时
  退回它的末尾 N 条上卡，并记一条日志说明用了兜底。取数层常把网络错误吞成「空列表」而不是抛异常，
  所以兜底条件要判「列表为空」，光 try/except 兜不住。
- 结算日志写「成功 N / 共 M 个群」，这就是回报给用户的数字。

**验证次序**：`scripts/probe_scheduler_jobs.py` 打 `next_run_time`（证明真的落在那个点）→ 假 bot 截获
跑一次真链路（把 `nonebot.get_bot` 换成假对象，**绝不真发到群**）→ 出图落盘 + `vision_analyze` 复核 →
加载自检与基线对账。细节、ORM 取群号的写法、「不装 pytest 也能验证不破坏上游测试脚手架」的跑法见
`references/scheduled-push-tasks.md`。

## 接第三方接口 / 改交互类功能

- **先读上游接口的官方文档，确认能力边界，再写代码。** 要确认两件事：能吃什么输入，以及关键字段的
  语义。判断 / 补全类接口的 `instructions` 往往既能收陈述句也能收疑问句，别替上游设限；只吃文本的
  接口（多数判断类）不接受图片，塞 base64 图片段或图片链接都不会生效，别浪费一轮去试。
- **文档站被本机代理劫持成假 IP 时，`web_extract` 会以「private or internal network address」拒收。**
  改用 terminal 里的 `curl -s`（本机走代理，能通）；Mintlify 系文档站还额外提供
  `https://docs.<站>/llms.txt`（全部页面索引）和把 `.md` 拼在页面地址后面（干净 Markdown）两个入口，
  比解析 HTML 省事，页面里内嵌的压缩 JS 记得先滤掉。
- **要和用户控制台 / Playground 的数字对得上，得先对齐 payload 的「形态」，不只是字段名。**
  同一个输入、同一个问题，`state` 发纯字符串还是发 `{"state": "…"}`，给分系统性差十几个点。
  **口径已由用户拍板：按官方 quickstart / 接口参考用纯字符串**（2026-09-29 他明确否掉包对象：
  「好像包一个对象是错的」）。控制台 State 面板是 JSON 编辑器，那个对象是编辑器形态、不是接口要求，
  **别再照控制台去包对象**（我照控制台改过一次，随后被否）。两边差十几个点这件事要如实告诉他，
  但不要用改代码去凑控制台的数字。
  定这个结论只能靠**受控 A/B**：固定输入、一次只换一个要素、每个变体各跑 3 次
  （单次采样 ±5~10 点是模型抖动，一次的差值不算证据）；改完再用插件真链路复跑 3 次，
  按「参照值 / 改前 / 改后」列表回报，并交代老路径分数也会跟着变、怎么回退。
  方法与实测数据见 `references/model-api-parity-testing.md`。
- **「引用某条消息再提问」这类交互的数据流是固定的：** 被引用的内容 = `state`，用户输入 = 问题；
  用户没写问题（只发了触发词）时回退到配置里的默认问题常量，既不报错也不当作没触发。
  三条异常分支各给一句明确回复：取不到被引用消息 / 引用里没有可判断的文字 / 引用的是图片。
  图文混排时用文字部分当 `state`，并在结果里注明图片已被忽略。
- **老用法逐字未变要有断言。** 把改动前那段用户可见文本（卡片、命令回复）写成字符串常量 assert 一次；
  「看着像没改」不算（`git diff` 里 `-` 开头的含中文行要逐条解释得掉）。
- **改「通知长什么样」时，新渲染器必须留降级路径，并断言降级结果与改动前逐字一致。** 取数、出图都
  可能失败，降级要退回原来的「封面图 + 文字」而不是发一坨空白；验证办法是把新渲染函数换成
  `lambda **kw: None` 再跑一遍真链路，逐字比对新旧字符串 —— 只测成功路径，等于把这条退路押在
  「出图不会失败」上。
- **测「引用」类功能必须先让适配器处理一遍事件。** 手工 `event.reply = Reply(...)` 会绕过
  `_check_reply` 的副作用（删 reply 段、调 get_msg、首段文字 lstrip、删紧跟的 at 段），于是测试全绿、
  真机上仍走老分支。正确顺序：`PrivateMessageEvent.model_validate({... "message": [reply段, text段]})`
  → `await nonebot.adapters.onebot.v11.bot._check_reply(fake_bot, event)` → `await rule(event)` → handler。
  假 bot 要有 `get_msg`（返回被引消息的段列表）/ `send` / `delete_msg`；「取不到被引消息」的分支靠
  `get_msg` 抛异常来测。假 `matcher.finish` 必须抛 `FinishedException`，否则错误分支会继续往下跑，
  看起来像「提示发完又发了卡片」。

## 装第三方插件（本地仓库形式）

用户丢一个 GitHub 地址说「装这个插件」时，默认做法是**克隆成本地仓库放进 `plugins/`**（不是
`pip install` 一个 PyPI 包）：他以后用 `git pull` 更新，也不用动 site-packages。步骤与硬规则：

1. **先审计再装。** `git clone --depth 1 <url>` 到 scratch 目录，先扫三样：全部 `https?://` 主机
   （有没有第三方上报域名）、`eval|exec|os.system|subprocess|pickle|b64decode`、遥测关键词
   （telemetry/analytics/track/sentry）。报告口径是**「有没有外联 + 本地会写什么库/目录」**，
   而不是只说一句「干净」——用户要的是判断依据；细节清单见 `references/third-party-plugin-install.md`。
2. **依赖先对账再装。** 读它 `pyproject.toml` 的 `dependencies`，与本机 `pip list` 逐项比，缺的
   **列出来给用户拍板**：他批准的是「装这个插件」，不等于批准插件带进来的全部依赖。
3. **`pip install --dry-run` 先看影响面，再真装。** 逐条读 `Would install`，重点抓**已装包被升级**
   —— 尤其 `starlette` / `fastapi` / `pydantic` / `uvicorn`：机器人自己的 FastAPI 驱动就在用它们。
   发现要升级就把那个包用 `==当前版本` 一起钉住，pip 会退到能兼容的旧版本依赖，于是**只新增、不升级**：
   `pip install "fastmcp>=3,<5" "starlette==1.0.0"`（实测这样从「升级 starlette」变成「41 个新包、
   零升级」）。装完复核关键包版本与装前**逐个一致**，并在报告里写明「几个新增、零升级」。
4. **判「某个依赖是不是可选」，grep 整个包，别只看 `__init__.py`。** 包 `__init__` 里可能是函数内的
   惰性导入（`from .mcp import ...` 写在函数体内），而 `handlers/commands.py` 顶层就
   `from .. import mcp` —— 只看 `__init__` 会得出「开关默认关着就不必装」的错误结论，装完插件加载
   直接 `ModuleNotFoundError`。`grep -rn "<模块名>" --include=*.py <插件包>/` 一遍成本极低，能省一次返工。
5. **目录名 = 包名（下划线）**：克隆进 `plugins/<包名>/`，加载路径 `plugins.<目录>.<包名>`，追加到
   `bot.py` 的 `PLUGINS` **末尾**（保持现有匹配器相对顺序不变）。带连字符的仓库名
   （`nonebot-plugin-hermes`）**不能**当模块路径用，克隆时就改成下划线名。
6. **装完跑插件加载自检并与基线对账**（见上「先立基线」）：多装了 N 个插件就该多 N 条成功，失败必须仍为 0。
7. **触发范围默认锁死。** 把 QQ 消息桥接到「有工具权限的 agent」的插件（Hermes 桥接这类），等于把
   本机操作权交给能触发它的人。默认「群聊仅白名单群 + @触发、私聊仅主人」，并在报告里点明这层风险。
   **白名单空值往往等于「全部允许」**（如 `HERMES_ALLOW_GROUPS` 空 = 所有群都行），所以先填一个
   不存在的占位值，并明确告诉用户「现在谁也触发不了，等你给真实群号」，绝不留空。
8. **插件自带 `migrations/`（依赖 `nonebot-plugin-orm`）时，必须先把表建好再启动**：ORM 的启动检查
   是**交互式确认**，没有 TTY 时会直接中止 —— 先跑 `scripts/orm_migrate_plugin.py` 建表，再重启。
9. 报告时列清五件事：改了哪些文件 / 装了哪些包（新增几个、有没有升级）/ 每个插件怎么用 + 前提
   （如「机器人需是群管理员」「需本机 Hermes API 服务」）/ 本地会记什么数据 / 还等用户提供什么。

克隆进去的目录带 `.git`，以后 `git add -A` 会把它们记成空壳 gitlink。**这个用户已拍板：把嵌套 `.git`
删掉**（既不留在仓库里当 gitlink，也不正式收编进版本库）。删前必做两步：`git ls-files -s | awk '$1=="160000"'`
确认父仓库没把它们记成 gitlink；把每个仓库的 `git rev-parse HEAD` 与 remote 存档到
`data/third_party_plugins.json` —— **删了 `.git` 就没有 `git pull` 了**，以后更新只能按 commit 对比上游
重新下载覆盖，这份存档是唯一的版本凭据。删完 `find plugins -maxdepth 3 -name .git` 必须为 0，
并在报告里明说「更新方式从 git pull 变成重新下载」这个代价。

完整清单（审计 grep 模板、ORM 建表细节、Hermes 桥接的前置条件与白名单键语义）见
`references/third-party-plugin-install.md`。

## 升级 nonebot2 本体 / 依赖评估

用户会问「我用的是哪个版本、有没有新版、能升吗、有没有不兼容」。**只做只读评估，不要顺手装**：
把「版本现状表 + 兼容性证据表 + 回退方案」摆出来，等他点头再 `pip install`（装包会改环境，属他拍板的事）。

- **本机版本**逐个打 `importlib.metadata.version(pkg)`（nonebot 本体 + adapter + 每个 nonebot 系插件）；
  **最新版与发布时间**从 `https://pypi.org/pypi/<包>/json` 取 —— `upload_time` 能说明「新版本已经放了多久」，
  比版本号本身更能支撑「能不能升」的判断（刚发几天的和放了半年的，风险完全不同）。
- **影响面**用 `pip install --dry-run <包>==<版本>`，只读 `Would install`，理想结果是**只有那一个包**。
- **生态约束**对每个已装插件打 `md.requires(pkg)`，把依赖 nonebot2 的那几条抽出来逐条核对；
  `~=2.4` 的上界是 3.0 而不是 2.5，别照字面判成「不兼容新版本」。
- **破坏性变更**先读官方更新日志的「破坏性变更」小节，再自己机械核一遍：下载新旧两个 wheel，比对
  **文件清单 + 各文件顶层函数/类名**，并把仓库里实际 import 到的 nonebot 名字在两版里各查一次存在性。
- 升级动作固定为：备份 `pip freeze` → 装 → 全插件加载自检与基线对账 → 重启 → 真机验证；
  回退就是 `pip install <包>==<旧版本>` 再重启一次。命令与比对脚本片段见
  `references/dependency-upgrade-assessment.md`。

## 踩过的坑

- **对照实验先验凭据**：拿一份已失效的 SESSDATA 做「匿名 vs 登录」对照，两组都等于未登录，
  于是错误地得出「跟登录态无关」，让用户白跑一轮。跑 A/B 之前先验证凭据本身有效
  （B 站看 `nav` 的 `isLogin`）——失效凭据的对照等于没做。

- **适配器会把 `reply` 段从消息里删掉 —— 判引用千万别扫 `event.message`。** `_check_reply()`
  （`nonebot/adapters/onebot/v11/bot.py`）取回被引消息后放进 `event.reply`：**成功时
  `del event.message[index]` 把段删了**；取失败（get_msg 抛异常）时节段还在但 `event.reply` 是 `None`。
  所以「扫 `event.message` 找 `reply` 段」在正常引用时**恒为假**，功能会静默退回老分支（实测踩过，
  用户在真机上试才发现）。正确判法：

  ```python
  def has_reply(event) -> bool:
      if event.reply is not None:                              # 取回成功（正常情况）
          return True
      original = getattr(event, "original_message", None)      # 原样副本，取回失败时还留着段
      return any(seg.type == "reply" for seg in original or [])
  ```

  被引内容直接取 `event.reply.message`，别自己再调 `get_msg`。
- **`event.reply.message` 是 `Message`（`MessageSegment` 对象列表），不是段字典列表。** 只认 dict 的
  解析器会静默返回空串，于是所有分支都走进「没有内容」。解析引用内容要同时吃
  `MessageSegment` / 段字典 / CQ 码字符串三种形态，见 `references/onebot-v11-event-message.md`。
- **改环境变量名之前，全树 grep 谁在读它。** 同一个变量可能被别的插件当兜底读走
  （余额插件会把截图插件的代理变量当第二候选），只改自己那份就静默改掉了别人的行为。
  `grep -rn "<VAR>" plugins/ --include=*.py` 是必须的一步。
- **`from pydantic import BaseSettings` 在 pydantic 2 上是坏的。** 遇到这种 v1 残留：
  没人 import 就整个删掉；有人用就照仓库现有的 Settings 写法改。不要为了「让它能跑」而降级 pydantic。
- **仓库根目录的文件可能是别的项目的模板。** 这个仓库的 `README.md` 一度整篇是 go-cqhttp 官方文档，
  `pyproject.toml` 写着 `nb/plugins` 和「QQ 频道」适配器。动手前先确认根文件描述的是不是这个项目。
- **根目录遗留文件里可能有明文凭据**（历史 `config.yml` 写着 QQ 账号密码）。发现就报告，
  并提醒凭据仍在 git 历史里、需要轮换——删掉工作区文件并不等于解除泄露。
- **改 `pyproject.toml` 的 `[tool.nonebot] plugin_dirs` 要小心。** `nb run` 会按它自动加载插件，
  而 `bot.py` 也在加载同一批，两边一起加载就撞 `Plugin already exists`。留空，或保持指向不存在的目录。
- **一个匹配器 + 内部分发 优于 多个重叠的 `on_regex`。** 子命令用
  `argument.partition(" ")` 在同一个 handler 里分发，可以彻底避开匹配器抢占和 priority 调参。
- **`on_message` 的 rule 里必须自己判群聊。** `on_message` 私聊也会进，而私聊消息 `get_plaintext()`
  一样取得到文本，于是「消息含某关键词」的规则在私聊也为真，handler 里读 `event.group_id`（或别的
  `GroupMessageEvent` 专有字段）直接 `AttributeError` —— 表现是私聊里发那句话，机器人无回复、
  日志里一条报错。规则写成 `isinstance(event, GroupMessageEvent) and 关键词 in event.get_plaintext()`，
  规则函数的类型标注用 `MessageEvent`（只有群聊专用的匹配器才配得上 `GroupMessageEvent`）。
- **删嵌套 `.git` 之前，先查它有没有被记成「子模块链接」。** 目录里带 `.git` 时，即使仓库没有
  `.gitmodules`，`git add` 也会把它记成 gitlink（`git ls-files -s` 里 mode 是 `160000`），
  于是版本库里只有个空壳；把 `.git` 删掉后这些链接就悬空，且备份 zip 里通常不含嵌套 `.git` 内容，
  历史无从恢复。查：`git ls-files -s | awk '$1=="160000" {print $4}'`。
  要收编成普通文件必须 `git rm --cached -rf <路径>`（不加 `-f` 会因
  「staged content different from both the file and the HEAD」被拒），再 `git add <路径>`。
  vendored 插件自带的 `.gitignore` 会被 `git add` 尊重，这是对的，别用 `-f` 绕过它。
- **`git restore --staged a b c` 是全有或全无**：只要有一个 pathspec 不存在，整条命令报错、
  什么都不取消暂存（`&&` 链后接的清理步骤也会被短路）。逐个路径确认存在，或一个路径一条命令。
- **几百个文件暂存时别直接执行 `git status --porcelain`**，输出会爆到几万字符被截断。
  先 `git status --porcelain | cut -c1-2 | sort | uniq -c` 看状态码分布
  （`AD` = 已暂存新增但工作区已删，`git add -A` 会清掉；`??` 未跟踪；` M` 未暂存改动），再按需展开路径。
- **本仓库换行符是 CRLF，而 `write_file` 写出来的是 LF。** 改完把动过的文件统一转回 CRLF，
  否则工作区一半 CRLF 一半 LF（`core.autocrlf=true`，所以这影响的是工作区观感，不影响提交内容）。
  统计换行符要**写成 `.py` 文件再跑**——在 `execute_code` 里内联写 `b"\r\n"` 字面量会被转义层
  吃掉，得到「0 个换行」这种一眼假的数字。
- **Pillow 的 `textbbox` / `textlength` 可能返回浮点数。** 用它算出的画布尺寸要先 `int()` 再交给
  `Image.new`，否则尺寸类型报错。
- **别用 Hermes 的终端后台方式启动用户的机器人 —— 重启 Hermes 网关会把它一起杀掉。**
  实测：`terminal(background=True)` 起的 `python bot.py` 挂在 Hermes 自己的进程树下，13:12:36 重启网关后
  机器人 13:12:45 静默死亡（日志停在那一刻、8899 无监听、无任何报错），用户侧表现是「私聊机器人没反应」——
  很容易误判成插件坏了。要么让用户自己起，要么用独立进程：
  `powershell -NoProfile -Command "Start-Process -FilePath 'cmd.exe' -ArgumentList '/c','cd /d <repo> && <py> bot.py > bot.log 2>&1' -WindowStyle Hidden"`。
  排查「用户说没反应」时**第一步先看 bot.log 最后写入时间 + 进程/端口是否还在**，再看插件逻辑。
- **B 站接口（`bilibili_live` 用到的）实测事实：** `room/v1/Room/getRoomInfoOld?mid=<uid>` 的 data 里
  **没有 uname**（要名字得再打 `x/web-interface/card`，取 `data.card.name`），`liveStatus == 1` 才是在播；
  本机到 `api.live.bilibili.com` **偶发 TLS 握手重置**（同一地址连打三四次才成，日志里是
  `Cannot connect to host …:443 ssl:default [None]`）。一次性查询（手动触发）必须自带重试，
  分钟级轮询靠下一轮自愈、可以不改 —— 要改也先问用户；另外**取数失败不能当成「未开播」记日志**，
  否则查不出东西时会骗自己（把失败与未开播分开计数）。
- **「推送/通知隔了很久」先分段计时再下结论。** 轮询型推送的总延迟 = 「实际发生 → 某轮轮询检测到」
  +「检测到 → 通知发出」两段，从 bot.log 各量出来再说话：后者通常只有十几秒（取昵称、下封面、
  画卡片），前者才是大头——轮询连续失败的那几分钟机器人是「瞎」的，网络断口改代码救不了，
  手动触发的查询也撞同一堵墙。把分段结论摆给用户，改不改轮询节奏由他拍板（加急重试他拍板过
  「维持现状」，别自作主张加）。
- **给 vendored 第三方插件补上游缺失的能力时，按「配置项 + 判定函数」最小切法打本地补丁，并留 `.bak-localpatch`。**
  例：`nonebot-plugin-hermes` 的群隔离只看群号不看人，加「按群的用户白名单」只需改两处 ——
  `config.py` 加 `hermes_group_user_allowlist: dict[str, set[str]] = {}`，`utils.py` 的群聊分支加
  `allowed = plugin_config.hermes_group_user_allowlist.get(group_id)` +
  `if allowed and user_id not in allowed: return False`；
  **语义选「只在表里出现的群才限制人」**（不在表里 = 不限制），这样一个开关同时支持
  「A 群全员可用 / B 群仅本人可用」，不用再加第二个开关。
  挑 `check_isolation` 这种**所有入口（消息 / 命令 / notice）共用的单一闸门**改，一处生效。
  验证写成分支枚举脚本（放行/拒绝各几种组合），比人工试群可靠。
  改前先 `shutil.copy2` 出 `*.bak-localpatch`，并明确告诉用户「插件更新会覆盖、需重放」。
- **给 vendored 插件补「上游没有的定时任务」时，别改它已有的函数体**：新加一个独立模块
  （如 `daily_report.py`），在包 `__init__.py` **末尾**一行
  `from . import <模块>  # noqa: E402,F401 — 导入即注册定时任务`，模块内 `require("nonebot_plugin_apscheduler")`
  后 `@scheduler.scheduled_job("cron", hour=0, minute=0, id=...)`。这样上游更新时只有那一行需要重放，
  文件头的 docstring 里写清「上游没有这块、覆盖后按 .bak-localpatch 重放」。
  改完照插件自带 `tests/conftest.py` 的加载方式（`DRIVER=~none` + `LOCALSTORE_*` 环境变量 +
  `load_plugin("<包名>")`）单独跑一遍加载，证明新模块没破坏它的测试脚手架 —— 本机没装 pytest
  （不要为跑它去装），复刻那几行环境变量的临时脚本就够。
- **内联 `\r\n` 字面量在 `execute_code` 里会被转义层吃掉。** 要按行插入含 CRLF 的文件，写成 `.py` 文件再跑，
  用 `(chr(13)+chr(10))` 或 `LF.join(lines).replace(LF, CRLF)` 还原，别在单元格里写 `"...\r\n..."`（锚点会永远匹配不上）。
- **行内新增元素后，同一条横线上原有文字的宽度上限要跟着减。** 加增减文字、角标这类东西时，
  先量右侧新元素的宽度，再从左侧那段的可用宽度里减掉它加间距（留个最小值兜底），
  否则两段会叠在一起 —— 这是本仓库画卡片时最容易漏的一步。
- **插件访问本机服务（`127.0.0.1`）时，httpx 会被 Windows 系统代理劫持，症状是「空体 502」。**
  httpx 默认按系统代理设置走（`getproxies()` 读注册表），而注册表里那个绕过列表（`ProxyOverride` 写了
  `127.*`）**httpx 并不吃**；curl 压根不读注册表，于是同一时刻 **curl 200、httpx 502** —— 最容易误判成
  服务端故障。三条判据：502 响应**没有 `Server` 头**且 `content-length: 0`（真服务端返的是带 JSON 错误体的
  信封）；服务端日志里查不到这次请求；`python -c "import httpx;print(httpx._utils.get_environment_proxies())"`
  直接打印出代理映射。隔离手法：**同一个进程里 httpx 与 curl 各打一次**，把「客户端差异」和「服务端状态」
  分开；再用 `httpx.AsyncClient(trust_env=False)` 打一次确认。修法是在进程早期（`bot.py` 顶部，
  `import nonebot` / `httpx` 之前）设 `os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost,::1")`
  与同值的小写 `no_proxy` —— 改自己的入口文件，对所有插件的本机请求一起生效；**不要**去改 vendored 插件的
  `trust_env`（插件升级会覆盖），**也不要**去动系统代理设置。自己写的探针脚本同样中招，脚本里先设
  `NO_PROXY` 或直接走 curl。

## 报告风格（用户偏好，每次适用）

- **结论先行**，随后用表格列「项 / 结论」或「改前 / 改后」。
- 解释性文字**全中文**；`ETA`、`MB/s` 这类英文缩写也要换成中文说法
  （路径、命令、文件名、代码标识符保留原样）。
- 长任务每完成一步给一句中文旁白，不要长时间静默；用户看不到中间过程会催。
- emoji 与表格排版可以放开用，用户接受度高。
- 主动把「需要你拍板的事」单独列出来（例如重启时机、是否 push、要不要轮换密钥），不要把风险埋在正文里。
- 重构中发现的**既有缺陷不要自己修**——修了就改变对外行为，超出批准范围。写成
  「位置 / 问题 / 实际影响 / 修复成本」表格，明确标注「未修」，让用户挑，并附一句自己的建议
  （例如「建议至少修前两个，属于功能名义上存在但实际不工作」）。
- 用户问「列出所有功能」这类问题时，用 `scripts/dump_features.py` 从**运行中的插件**导出真实触发条件
  与定时任务，不要凭记忆罗列；`bot.py` 里显式加载的第三方插件要标明「第三方」，
  底层库（navicat / waiter / session / alconna 之类不直接响应用户的）单独说明而不混进功能表。

## 相关

- `references/plugin-refactor-playbook.md` —— 派子代理重构插件时的完整约束与验证清单。
- `references/bazaar-cards-plugin.md` —— 「大巴扎」插件的命令、数据源、缓存布局与中文渲染做法。
- `scripts/qa_load_plugins.py` —— 全插件加载自检（AST 读清单 + 逐个加载；老版本 bot.py 退回解析 load_plugin 调用）。
- `scripts/dump_matchers.py` —— 导出/对账匹配器指纹，证明重构没改触发行为（`--compare` 出差异表）。
- `scripts/dump_features.py` —— 从运行中的插件导出功能清单（触发条件 + 定时任务）。
- `scripts/probe_send.py` —— 用假 bot 跑 `(bot, event)` 签名的 handler，检查实际发出的消息。
- `scripts/probe_command.py` —— 跑**无参** handler（匹配器 `send` / `finish` 换成假函数），图片落盘供视觉复核。
- `scripts/probe_group_send.py` —— 跑**群聊** handler：造真事件（读得到 `group_id` / `get_plaintext()`），
  覆盖 `matcher.send` / `finish`，逐条列出实际发出的消息；带 `--rule-name` 时顺带打印规则在群聊/私聊下的判定。
- `scripts/probe_scheduler_jobs.py` —— 打印 apscheduler 已注册的定时任务与每个 job 的 `next_run_time`
  （含时区），用来证明「每天几点」真的落在那个点，别只信 `hour=0` 的字面值。
- `references/plugin-runtime-state.md` —— 「记住上次状态 / 和上次对比」类功能的状态文件骨架、对比语义与验证打法。
- `references/scheduled-push-tasks.md` —— 「每天几点推某个东西」：cron 注册与时区、三个口径、ORM 取群号、
  假 bot 验证、不装 pytest 也能证明没破坏上游测试脚手架的跑法。
- `references/pil-card-rendering.md` —— 用 PIL 画通知卡片：可用字体、为什么不能用 emoji 字形、
  渐变/圆角/封面裁剪、JPEG 还是 PNG、视觉复核的打法。
- `references/dependency-upgrade-assessment.md` —— 评估 nonebot 本体/依赖升级：查本机与最新版本、
  `pip --dry-run` 看影响面、逐个核插件约束、两个 wheel 机械比对公开接口、回退方案。
- `references/onebot-v11-event-message.md` —— OneBot v11 的事件/消息形态：被引用消息从哪来、三种消息形态、探针里怎么造真事件。
- `references/model-api-parity-testing.md` —— 接第三方判断/评分接口时，让本地结果与参照实现（控制台 / Playground）对齐的受控 A/B 打法、payload 形态实测数据与接口事实。
- `references/third-party-plugin-install.md` —— 从 GitHub 装第三方插件的完整清单：审计 grep 模板、依赖钉版本、ORM 建表、Hermes 桥接前置条件与白名单语义、本机服务被系统代理劫持（空体 502）的诊断与修法。
- `scripts/orm_migrate_plugin.py` —— 给自带 `migrations/` 的插件建表（无 `nb` CLI 时用它，绕开 ORM 的交互式启动检查）。
- 网页抓取/截图的部分见 `browserless-web-extraction`。
