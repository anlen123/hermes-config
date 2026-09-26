# AST 跨度行为实测记录 & 错误签名对照

在 CPython **3.13.5** 上对真实大型源码（29,065 行 / 1.4 MB）实测所得。
`col_offset` / `end_col_offset` 一律为 **UTF-8 字节**偏移。

## 1. 隐式拼接：多个字面量 = 一个 Constant，跨度吃掉引号与缩进

```python
x = (
    "aaa bbb "
    "ccc ddd"
)
```
AST：`Constant(value='aaa bbb ccc ddd')`，span = **(第2行, col 4) → (第3行, col 13)**
—— 起点是第 2 行 `"` 的位置，终点是第 3 行结束引号之后（含两边引号）。

⇒ 替换必须用**带引号**的译文整体换掉这个跨度：
`json.dumps(zh, ensure_ascii=False)`。
span 文本形如 `"aaa bbb "\n    "ccc ddd"`，`ast.literal_eval(span)` 会抛
**`IndentationError: unexpected indent`**（续行缩进），必须写
`ast.literal_eval("(" + span + ")")` 才能判定「这是一个自洽字面量组」。

## 2. f-string 片段：跨度**可能吃掉结束引号**（最坑）

```python
return (
    f"The request failed: {err[:300]}\n"
    "Try again or use /reset to start a fresh session."
)
```
- 相邻的普通字面量被合并进 f-string 的常量片段，值 = `'\nTry again or use /reset to start a fresh session.'`
- 该片段的 span：**从上一行 `}` 之后的 `\n` 开始，到第二行结束引号为止**
  —— 起点在字面量内部（不含起始引号），终点却**含结束引号**。

后果：
| 替换方式 | 结果 |
|---|---|
| 裸文本替换 | `SyntaxError: unterminated f-string literal`（结束引号被吃掉） |
| 带引号替换 | `SyntaxError`（f-string 被提前闭合，后面裸文本） |
| **判定「结尾是引号就补一个引号」** | 也不可靠：文本本身可能以引号结尾（例：`f"...tool call: '{preview}'"` 的片段就以致命引号收尾） |

⇒ 唯一稳妥做法：**不要逐片段替换**，改为重建整个 `JoinedStr`：
文本片段用译文、`FormattedValue` 原样保留、`ast.unparse(ast.Expression(body=JoinedStr(...)))` 出源码，
然后替换 **JoinedStr 节点的整个跨度**。

## 3. 重叠编辑：症状是「整行/括号凭空消失」

同一句里既有整节点替换、又有子片段替换时，两个编辑互相踩踏：

```
SyntaxError: invalid syntax. Perhaps you forgot a comma?     ← 常见
（或）某行的 `)` 不见了、两行被并成一行
```

⇒ 两个必要条件：
1. 整节点替换后，把它覆盖的子节点（`id(node) in joined_parts`）从其它分支里**剔除**；
2. 包含判定用**绝对偏移** `s >= rs and e <= re_`，
   ⚠️ 不要用「行号区间相同 + 列落在区间内」这种近似判断 —— 跨行节点会漏判（本次真实踩到）。

## 4. 带括号表达式的节点跨度可能包含括号

Python 3.12+ 观察到：`JoinedStr` 的 `col_offset` 指向 `(` 之后一位（即括号**算进**了节点跨度），
而普通 `Constant` 的跨行拼接却不含外层括号。

⇒ 替换前先打印 span 文本核对；若把 `(` 吃进替换范围而 `)` 留在外面，会得到孤立的 `)`。

## 5. 幂等性靠「按值匹配」天然获得

补丁跑过之后英文原文在 AST 里不复存在 ⇒ 第二遍 `命中替换 = 0`。
把「复跑 0 命中」作为幂等验收，而不是靠额外标记文件。

## 6. 错误签名 → 原因 → 处置 速查

| 症状 | 原因 | 处置 |
|---|---|---|
| `unterminated f-string literal` / `unterminated string literal` | 片段跨度吃掉结束引号（见 §2） | 改为整 `JoinedStr` 重建 |
| `invalid syntax. Perhaps you forgot a comma?` / 整行消失 / 括号丢失 | 重叠编辑（见 §3） | 剔除子节点 + 绝对偏移包含判定 |
| `IndentationError: unexpected indent`（在 `literal_eval` 里） | 跨行字面量组未加外层括号（见 §1） | `literal_eval("(" + span + ")")` |
| 补丁报告「0 命中」但源码明明是英文 | 映射表是旧的 / 生成脚本没写成功（见 §7） | 重新生成映射并核对 JSON 内容 |
| 译文里出现 `{name}` 之类未替换的占位符 | f-string 片段里的 `{`/`}` 未双写 | `fstr_escape()`：`{`→`{{` |
| 中文里出现 `\n` 字面量而不是换行 | 片段替换时真换行被转义（3.12+ 允许，属正常） | 确认渲染结果即可 |

## 7. 「文件没写出来却以为成功」：管道截断的隐性杀伤

把会**写文件**的脚本接到 `| head -N` 上：`head` 读够行数就关闭管道，
脚本后续 `print()` 抛 `BrokenPipeError` 直接中断，**`json.dump()` 那行永远没执行**，
而 stdout 里前面的「解析成功 145 条」看起来一切正常 —— 后续步骤基于旧文件运行，报出「0 命中」的假象。

⇒ 规则：写文件的脚本一律 `cmd > /tmp/x.log 2>&1`，再看 `tail` / `read_file` 的结果；
不要用管道截断它的输出。

## 8. 改动生效需要重启：先探测监管者，再决定重启方式

源码与语言/词典类配置常被进程内缓存（例如 lru_cache、启动时快照）。
重启方式按环境探测，**不要照抄旧文档**：

```bash
tr '\0' ' ' < /proc/1/cmdline; echo    # PID 1 是谁（可能是 init/监管者，不一定是应用本体）
ls /run/service                         # s6 监管的服务（存在即 s6 环境）
pgrep -af "s6-supervise|systemd|<app>"  # 谁在看管应用进程
```

- **systemd 环境**：`systemctl restart <unit>`。
- **裸进程**：必须自己拉起，kill 后不会自愈 —— 先在 tmux/后台里准备拉起命令再动手。
