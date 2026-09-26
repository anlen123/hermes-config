---
name: upgrade-safe-source-patching
description: "Patch installed Python source in place; survives upgrades."
version: 1.0.1
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [patching, ast, i18n, localization, upgrade-safe, python, idempotent]
---

# 给「已安装的第三方 Python 源码」打可重放的补丁

适用：要改一个**非 git 安装**的应用源码（汉化系统消息、改文案、微调行为），
而该应用**升级/重装会覆盖源码**（`hermes update`、重装包）。

## When to Use（触发场景）

- 要汉化/改文案/微调第三方已安装源码，且升级会覆盖
- 之前的补丁因版本升级大面积失效，需要重做并做成**升级后可一键重放**
- 需要精确替换源码里的字符串，但逐行文本改写总是改错、改漏、改坏语法
  （改文件用 `patch` 工具、查找用 `search_files`、读取用 `read_file`，别手工拼 shell 改写）

## 决策：三种手段的优先级

| 优先级 | 手段 | 何时用 |
|---|---|---|
| 1 | **应用自带的 i18n / 配置层** | 只要存在（如 Hermes 的 `display.language` + `locales/*.yaml`）就**永远先走这条**：改配置或补词典，0 源码风险、升级自动继承 |
| 2 | **AST 精确跨度替换 + 映射表**（本 skill 主体） | 没有 i18n、或官方词典管不到的**硬编码字符串** |
| 3 | 逐行文本替换（按整行匹配英文） | ❌ 不要用。版本一变缩进、措辞、拼接方式就全失效（实战：79 条映射升级后只剩 46 条命中，且脚本自报的「未命中」计数不可信） |

> 发现应用有官方 i18n 时，先审计词典覆盖度：对比 `{en, lang}` 键集，并逐键检查**值是否仍与英文相同**
> —— 很多「没汉化」其实是键存在但值是英文。

## 核心算法

1. **收集目标**：用 `ast` 抽出所有字符串常量，按**上下文分类**只保留「用户可见」的：
   `Return` 里的、包装成 `Reply(...)` 之类的、`append/extend` 到 `lines/parts/msg/text/note` 这类列表的。
   排除：docstring、`logger.*`/`print` 参数、给模型的提示词（特征：以 `[` 开头、含 `System note`/`Do NOT`/`You are`）、
   正则、类型注解、字典键、URL/路径。
2. **写译文表**：`[英文前缀, 译文]` —— 前缀只用于定位，不必逐字符复刻长英文。
3. **解析成精确映射**：生成脚本把前缀解析成 **AST 里的完整字符串值**，输出 `[[en, zh], ...]`。
   必须报告三类异常：**未命中**（0 个）、**歧义**（命中多个不同值）、**扩展**（命中的值比前缀更长）。
   扩展最危险：说明相邻字面量被解析器合并了，译文**必须覆盖整段尾部**，否则原文被静默丢弃。
   解析基准必须是**未打补丁的上游原文**（留一份 pristine 副本），否则补丁跑过一遍后再生成映射会丢条目，
   升级后无法重放。
4. **替换**（只做两件事，详见「关键坑」2/3）：
   - 普通字面量（含跨行隐式拼接）→ 整体换成带引号的译文字面量（`json.dumps(zh, ensure_ascii=False)`）
   - f-string → **重建整个 `JoinedStr` 节点**（文本片段换译文，`FormattedValue` 原样保留），
     用 `ast.unparse` 出源码，替换**整个节点跨度**
5. **安全写入**：先 `--out 临时文件` + `py_compile` 试跑；正式写文件前自动备份，编译失败自动回滚。
6. **幂等**：按「英文原文的值」匹配，跑过之后原文消失 → 第二遍必然 0 命中。以「0 命中」作为幂等验收。
7. **残留自查**：改完再扫一遍，重点看「同一句里已有中文却还残留英文片段」的消息 —— 漏翻的都是半截句。
8. **生效**：源码/词典改动通常被目标进程缓存，**必须重启目标进程**；重启方式要先探测监管者再决定
   （systemd / s6 / bare — 见 references）。

## 关键坑（全部为实战踩出）

1. **逐行替换升级后大面积失效** → 换 AST 值匹配 + 精确跨度替换；只要英文原文没被改写就仍能命中。
2. **`ast.literal_eval` 判定跨行字面量组要加括号**：`"a"` 换行 `"b"` 这类隐式拼接在 AST 里是**一个** Constant，
   跨度含两边引号和中间缩进；直接 `literal_eval(span)` 会因续行缩进报 `IndentationError`，
   必须 `ast.literal_eval("(" + span + ")")`。判定成功后用**带引号**译文整体替换。
3. **f-string 片段跨度可能吃掉结束引号** → 症状 `SyntaxError: unterminated f-string literal` /
   `unterminated string literal`。原因：f-string 与相邻字面量被合并为一个常量片段。
   **不要猜「该不该补引号」**（文本本身以引号结尾时会误判），直接**重建整个 JoinedStr**。
4. **重叠编辑互相破坏** → 症状：整行/括号消失、`SyntaxError: invalid syntax. Perhaps you forgot a comma?`。
   整节点替换后必须剔除它覆盖的子节点，并用**绝对偏移**做包含判定（`s>=rs and e<=re`），
   不要用「行号相等」这类近似判断。
5. **`col_offset` 是 UTF-8 字节偏移**：行内含 emoji/中文时必须
   `line.encode('utf-8')[:c1].decode('utf-8')` 切片。
6. **带括号表达式的节点跨度可能包含括号**（Python 3.12+ 实测 JoinedStr 的 span 从 `(` 之后起算）——
   替换前先打印 span 文本核对，否则吃掉 `(`、留下孤立 `)`。
7. **别用管道截断工具（如 `head -N`）包住「会写文件」的脚本**：读端提前关闭管道 → 脚本抛
   `BrokenPipeError` 中断 → **文件没写出来却以为成功**（实战：映射 JSON 没更新，补丁报「0 命中」）。
   改成 `cmd > logfile 2>&1`，再用 `read_file` 看结果。
8. **译文放进 f-string 片段必须转义**：真换行→`\n`、制表符→`\t`、反斜杠先转义、引号转义、
   `{`/`}` 双写成 `{{`/`}}`，否则被当占位符。
9. **只翻用户可见的串**：把给模型的提示词/日志/正则/键名翻掉会改变程序行为，分类阶段就要排除。
10. **回滚必须一键**：pristine 副本 + 每次写入自动备份 + 编译失败自动 `shutil.copy2` 还原。

## 交付物结构（升级后一键重放）

```
<work>/<app>-translations.py   # [英文前缀, 译文] 人写译文表
<work>/<app>-strings.json      # 生成脚本产出的精确映射 [[en, zh], ...]
<work>/gen_mapping.py          # 前缀 -> AST 精确值（未命中/歧义/扩展 报告）
<work>/patch_engine.py         # AST 跨度替换（--check / --list / --out / --rollback，幂等）
<work>/residual_scan.py        # 残留自查（半汉化句子 + 仍英文的回复）
<work>/<app>-py-<ver>.orig     # pristine 上游副本（解析基准）
```

升级后流程：`gen_mapping.py` → `patch_engine.py --check` → `patch_engine.py` → `residual_scan.py` → 重启进程。

## 支持文件

- `scripts/ast_patch_engine.py` —— 精简可复用引擎（AST 跨度替换 + f-string 重建 + 备份/回滚/幂等）
- `references/ast-span-quirks.md` —— 跨度行为实测记录、错误签名对照表、重启方式探测
