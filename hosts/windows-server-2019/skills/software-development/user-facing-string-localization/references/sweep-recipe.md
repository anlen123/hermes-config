# 汉化清点与验收 · 配方

## 1. 面枚举（按这些关键词 grep 调用点）

| 面 | 典型位置 |
|---|---|
| 运营通知 | `_deliver_platform_notice(...)`、`notify*`、`*_watcher*`、启动 / 重启 / 升级 / 数据库异常 |
| 命令回复 | `slash_commands*.py`、命令注册表里的 usage 与描述 |
| 忙碌态 / 队列 / 中断 | `run_busy*`、`run_inbound*` 的 ack 文案 |
| 错误提示 | 模型服务 401/402/529、限流、额度用尽、超时、卡住、错误分类器 |
| 平台适配器 | `platforms/<name>/*.py` 中发给该平台用户的限制 / 失败提示 |
| 卡片 / 按钮 | 审批卡、模型选择卡、任务卡：标题、按钮标签、超时说明 |
| 资源型文案 | `assets/*.yaml`、`locales/*.yaml`、模板文件 |

先跑一遍「哪些文件已被汉化过」：按 CJK 字符 grep 命中文件名，能立刻看出上一轮只动了哪几个面。

## 2. AST 过滤器

**一律排除**：docstring（Module / FunctionDef / AsyncFunctionDef / ClassDef 的 `body[0]` 字符串）、
注释、`logger.*` / `print` 的参数、以 `[` 开头的机器标记、正则、SQL、类型注解、字典键、
环境变量名、URL、路径、工具名 / 参数名 / 枚举值，以及被 `in` / `find` / `startswith` / `re`
当**匹配串**用的英文。

**保留判据**：含 ≥3 个英文单词、不是纯标识符（无空格且多下划线）、不在排除清单里。

**残留判定**：`re.search(r"[\u4e00-\u9fff]", s)` 为真 → 已含中文，跳过。
纯 ASCII 判定只适用于纯英文语料。

## 3. 子代理指令模板（要点）

写死下面每一条，缺一条就会出问题：

- 只翻**用户可见**串；整段隐式拼接表达式**一起换**；
- 占位符 `{x}` / `%s` / `%d` / `%r` 原样不动；
- 反引号里的命令、路径、配置项不动（`/new`、`config.yaml`、`hermes doctor`）；
- emoji、markdown、`\n` 不动；
- **不改任何逻辑、分支、参数、默认值**；
- 中文没有单复数与首字母大写，相关三元 / `_plural()` / `.capitalize()` 可随之去掉；
- `[` 开头的机器标记保持英文；
- 完成后自己跑 `py_compile`，并回报「英文原文 → 中文译文」对照与故意未改项；
- 报告用中文写。

切批原则：按文件切、每批 2–6 个、**互不相交**。文件不重叠时允许子代理直接改文件，
但仍然要父级验收 —— 子代理的「已完成」是自述，不是事实。

## 4. 验收脚本要点

**AST 骨架比对**：

```python
import ast
def norm(src):
    t = ast.parse(src)
    for n in ast.walk(t):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            n.value = "<S>"
    return ast.unparse(t)          # 或 ast.dump(t, include_attributes=False)
```

对 pristine 与现状两侧 `norm()` 后 diff；差异应只出现在「单复数 / 大小写」相关三元上。

**导入 + 实跑**：先 `py_compile` 全量，再逐个 `importlib.import_module`，最后调用纯格式化函数。
调用时注意签名（可能必填参数），先用 `dir()` 找函数名、看异常信息补参。

**残留扫描**：见 SKILL.md「验收三连」第 3 条 —— 混排语料用 CJK 判据。

## 5. 落地

改动要落成**升级后可重放**的产物（补丁 + 映射/译文字典 + README 里的重放步骤），
否则下一次升级会全部覆盖。重放包的目录约定与重放步骤随应用而定，写进该应用的 README。

生效必须重启目标进程；**重启前确认没有在跑的后台子代理**。
