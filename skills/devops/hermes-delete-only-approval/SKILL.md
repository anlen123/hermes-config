---
name: hermes-delete-only-approval
description: 给 Hermes 增加第四种审批模式 delete-only —— 除删除类命令（rm/unlink/shred/find -exec rm/git rm/xargs rm/find -delete）必须 /approve 外，其余命令一律自动放行。用于「我只想管删除，别的别烦我」的场景。含可重放补丁脚本，hermes update 覆盖后可一键重打。
---

# Hermes delete-only 审批模式

## 触发场景

- 用户说「删除操作要审批，非删除的随便执行」
- 用户嫌 `/approve` 太烦，但又不想完全裸奔（`off` / `--yolo`）
- `hermes update` 之后发现审批行为变了、mode 失效

## 背景：内置只有三档

`approvals.mode` 原生只支持：

| mode | 行为 |
|---|---|
| `manual` | 所有危险命令都问（默认） |
| `smart` | 辅助 LLM 判断风险，低危自动放行（不精确，模型说了算） |
| `off` | 全部放行（等价 `--yolo`） |

**没有「按类别审批」这个配置项**。用户要的「只审删除」必须自己加一档 —— 本 skill 就是干这个的。

## 快速使用

```bash
# 1) 打补丁（幂等，已打过会直接报告）
python3 /opt/data/skills/hermes-delete-only-approval/scripts/apply_delete_only_patch.py

# 2) 只检查锚点是否还在（不写文件）
python3 /opt/data/skills/hermes-delete-only-approval/scripts/apply_delete_only_patch.py --check

# 3) 回滚到最近一次备份
python3 /opt/data/skills/hermes-delete-only-approval/scripts/apply_delete_only_patch.py --revert
```

## 配置

编辑 `/opt/data/config.yaml`：

```yaml
approvals:
  mode: "delete-only"     # 注意加引号！裸写 off/on 之类会被 YAML 1.1 当布尔值
  timeout: 60
```

**改完不需要重启 gateway** —— `_get_approval_mode()` 每次调用都重新读 config。

验证：

```bash
cd /opt/hermes && HERMES_HOME=/opt/data .venv/bin/python -c \
  "import tools.approval as a; print(a._get_approval_mode())"
# 应输出 delete-only
```

## 行为验证（端到端）

用 `check_all_command_guards(cmd, 'local', None)` 逐条探测。
期望：非删除类返回 `approved=True`；删除类返回未批准（会走审批弹窗）。
非删除样本可挑「读目录、重定向写块设备、改权限、改服务、装包、强制推、关机、格式化」
这类上游高危模式；删除样本覆盖裸 rm、带 flag 的 rm、递归 rm、unlink/shred、
`find -exec rm`、`find -delete`、`xargs rm`、`git rm`、`sudo rm`。
测试时把 `a._get_approval_mode` 临时替换为 `lambda: 'delete-only'`，避免依赖真实 config。

## 补丁做了什么（5 处改动，全在 /opt/hermes）

1. `tools/approval.py` — `DANGEROUS_PATTERNS` 每条从 2 元组变 3 元组，加类别标签
   （`CAT_DELETE / CAT_DISK / CAT_PERMISSION / CAT_SYSTEM / CAT_SELF_KILL / CAT_DB / CAT_OTHER`）
2. `tools/approval.py` — 新增 `DELETE_ONLY_EXTRA_PATTERNS` + `detect_delete_only_matches()`
3. `tools/approval.py` — `detect_dangerous_command()` 的解包循环改 3 元组
4. `tools/approval.py` — `check_all_command_guards()` 加 delete-only 前置过滤 + 把删除命中项
   强制塞进 warnings 列表
5. `hermes_cli/config.py` — 注释里补文档（纯装饰，锚点找不到会静默跳过）

## 关键坑（踩过的）

- **裸 `rm file.txt` 上游故意不拦**：`tests/tools/test_approval.py::TestRmFalsePositiveFix`
  明确断言 `rm readme.txt` **不应**被判危险（manual 模式下那是噪音）。
  所以不能在 `DANGEROUS_PATTERNS` 里加宽泛的裸 rm 正则 —— 那会挂掉 8 个上游测试。
  正确做法：把「只删模式专用」的窄模式放进独立的 `DELETE_ONLY_EXTRA_PATTERNS`，
  再在 warnings 阶段强制注入。这样上游 119 个测试全绿。
- **宽泛裸 rm 正则会抢占更具体的模式**：加了它之后 `find -exec rm` 的 pattern_key 会变成
  "file delete"，挂掉 `TestPatternKeyUniqueness` 和 `TestFindExecFullPathRm`。
- **改完务必跑上游测试**：`cd /opt/hermes && HERMES_HOME=/opt/data .venv/bin/python -m pytest tests/tools/test_approval.py -o 'addopts=' -q`
  → 必须 `119 passed`。
- **解释器用绝对路径 venv**：`/opt/hermes/.venv/bin/python`。系统 `python3` 没有 `hermes_cli` 模块。
  环境变量 `HERMES_HOME=/opt/data` 必须带上，否则 config 解析不到。
- **`hermes update` 会覆盖源码**：升级后重跑补丁脚本即可（幂等）。
- **补丁脚本自身改过 3 轮才稳**，验证方式：从**原始备份**出发重打补丁，比对行为是否与线上一致。
  不要只在已打补丁的文件上测 —— 那样测不出锚点是否还匹配真实上游。
- **`src.index("]", start)` 会撞上正则里的字符类 `[^\s]`**：定位 `DANGEROUS_PATTERNS` 块尾
  必须用 `src.index("\n]", start)`（行首的孤立 `]`）。
- **正则字面量里含引号/逗号**：给模式行打类别标签时，正则要锚定结尾形式
  `^(\s*\(r(?:f)?'.*,\s*)"([^"]+)"\),\s*$`，用懒惰 + 结尾锚，不能用非贪婪 `.*?',`。

## 安全提示

`delete-only` 只拦「删除」。**磁盘破坏类命令仍然放行**：写块设备、格式化文件系统、
把根目录改成全局可写、重启/关机、强制推远端 等，都会在无提示下执行。
NAS 上的影视库/相册属于不可再生数据，如果在意，应改用 `smart` 或 `manual`。
务必向用户讲清这一点。
