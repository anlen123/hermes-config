---
name: git-history-secret-scrubbing
description: Purge secrets from git history; Windows filter-repo fixes.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [git, security, secrets, history-rewrite, windows, filter-repo, force-push]
    category: software-development
    related_skills: [github, upgrade-safe-source-patching]
---

# 从 git 历史中清除文件 / 密钥

## When to Use
- GitHub push protection 拒绝推送并点出含密钥的提交
- 某文件（凭据配置、隐私数据库）需要从**全部历史**移除，即改写历史 + force push

## 先判断暴露范围，再动手
```bash
# 该文件在多少个提交里
for c in $(git rev-list --all); do git ls-tree -r --name-only "$c" | grep -qE '\.env\.(prod|dev)$' && echo "$c"; done | wc -l
# 密钥串是否进过远端（空=尚未公开，无需轮换）
git log origin/master --oneline --pickaxe-regex -S'sk-[A-Za-z0-9_-]{16,}' -- <file>
```
只查引用不下载对象：`git ls-remote --heads origin`。

## 头号大坑：Windows 上的非法路径
历史里若有 Windows 无法表示的路径（典型：文件名就是一个反斜杠 `\`），两个工具都会崩：

| 工具 | 症状 |
|---|---|
| `git filter-branch --index-filter` | `error: invalid path '\'` + `Could not initialize the index` |
| `git filter-repo` | `OSError: [Errno 22] Invalid argument` at `self._output.flush()` |

filter-repo 的 Errno 22 极易误判成「Python 版本 / 管道 I/O 问题」——**真因是 fast-import 子进程因非法路径先退出了**（重定向输出到文件、换 Python 3.13 都无效）。

诊断非法路径：
```bash
git rev-list --all --objects | cut -c 42- | sort -u | awk 'length($0)>0 && length($0)<3' | cat -A
```

**解法：把非法路径和目标文件一起过滤。** filter-repo 在 Python 层算新树，fast-import 就不会碰到它。
```bash
cat > paths-to-remove.txt <<'EOF'
.env.prod
literal:\
EOF
py -3.13 git-filter-repo.py --paths-from-file paths-to-remove.txt --invert-paths --force > log 2>&1
```

## 安全网（必做）
- `git clone --mirror <repo> <repo>-backup.git` —— 完整保留原始历史（filter-repo 会重写**所有** refs，包括备份分支，所以分支不算备份）
- 被删文件若本地仍要用：先 `cp` 到仓库外。filter-repo 结束会 `reset --hard`，工作区里该文件会消失

## filter-repo 的副作用
- **会删掉 origin remote** → 事后 `git remote add origin <url>` 加回
- 所有提交 SHA 变化 → push 必须 force，且远端没有这些对象，会**全量重传**（用 `git count-objects -vH` 估算 size-pack）
- 长距离上传用 `background=true` + 轮询，别用前台超时等

## 验证（并识别误报）
```bash
git log --all --oneline --pickaxe-regex -S'sk-[A-Za-z0-9_-]{16,}'   # 应为空
git grep -nE 'sk-[A-Za-z0-9_-]{16,}' HEAD                          # 当前树
```
误报来源：README 里的 URL（`How-To-Ask-Questions` 命中 `sk-`）、代码里的 `os.getenv("XXX_API_KEY")` 键名。这些无害，别当成泄露。

## force push
```bash
git push --force-with-lease=master:<远端当前SHA> origin master
```
比裸 `--force` 安全（远端 SHA 用 `ls-remote` 现查）。GitHub 上旧提交变不可达，但仍可能按 SHA 直接访问一段时间 —— **已公开过的密钥一律去平台轮换，历史清理不能替代轮换**。

## 更省事的替代：直接重建仓库
历史污染严重（或本就没有需要保留的历史）时，别折腾重写 —— 重建更快更干净：
1. `mv .git ../old-git-backup`（改名而非 `rm`，留退路）
2. `git init -b master` + 完善 `.gitignore`（运行数据、日志、缓存、大体积资源全排掉）
3. `git add -A` 后**必须验证暂存清单**：`git diff --cached --name-only | wc -l`、按目录统计体积、用 grep 守红线 `'\.(env|db|cdb|ttc|log)$|^data/|^picture/|^\.idea/'`
4. 提交前扫密钥：`git diff --cached --name-only -z | xargs -0 grep -lIE 'sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}'`
5. 推送到**新仓库**（旧的删掉）
好处：不碰历史、不必全量重传旧对象、体积能降一个数量级（本例 70M → 2.1M）。

## 预防（治本）
`.gitignore` 写 `.env` 只匹配名为 `.env` 的文件，而且**对已被跟踪的文件完全无效**。正确做法：
```gitignore
.env
.env.*
!.env.example
```
再 `git rm --cached <file>` 把已入库的移出跟踪（工作区文件保留）。提交前用 `git status --short` 确认它不再出现。
