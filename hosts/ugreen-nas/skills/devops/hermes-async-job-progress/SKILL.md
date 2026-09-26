---
name: hermes-async-job-progress
description: 把本机 Hermes 里任何「跑很久」的任务（下载、转码、rsync、批量处理）改造成非阻塞异步作业 —— 工具调用秒回、对话不被卡死、实时进度自动推进到 QQ/Telegram。核心手法：detached 子进程 + @@PROGRESS@@/@@STATE@@ 哨兵行 + process_registry/gateway 解析渲染。当用户说「别卡住对话」「下载/任务要异步」「要能看进度」「同步跑十几分钟受不了」时加载。
---

# Hermes 长任务异步化 + 进度回传

## 触发场景

- 「优化下载链路，用异步方式」「不要同步下载卡住对话」
- 「跑一个要十几分钟的任务，但我要能继续聊天」
- 「任务进度能推到 QQ / 群里吗」
- 任何 >1 分钟的 shell 任务（下载、ffmpeg 转码、rsync、模型跑批）

## 🚨 先搞清楚：什么会卡、什么不会

| 方式 | 行为 | 结论 |
|---|---|---|
| `terminal(cmd)` 前台 | 工具调用一直阻塞到命令结束 | ❌ 长任务绝对不用 |
| `terminal(cmd, background=True)` | Hermes 托管的后台进程，有 `process` 工具可管 | ✅ 但**会话结束/容器重启**会丢 |
| **detached 子进程（setsid）+ 哨兵行日志** | 与 Hermes 完全解耦，重启也不影响 | ✅✅ 生产首选 |
| `qdl.py start` 这类封装 | 启动即返回 job_id，进度写 JSON | ✅✅ 推荐接口形态 |

> 关键认知：**「秒回」不是靠 Hermes 的 background 参数，而是靠
> `subprocess.Popen(..., start_new_session=True)` 把活儿甩出去，父进程立刻 return。**

## ✅ 标准作业流程

```
① 写成「启动器 + 执行器」两个角色
     启动器：解析参数 → Popen(detached) → 立刻打印 job_id → 退出（<0.2s）
     执行器：真正干活，周期性打印进度哨兵行
② 进度落地双写
     结构化：/opt/data/cache/<area>/<job_id>.json   ← status 命令读这个（原子写）
     文本流：/opt/data/cache/<area>/<job_id>.log    ← Hermes 的 watcher 在 tail
③ Hermes 侧联动（见下节补丁）
     process_registry 解析哨兵行 → get/poll/list 多出 progress 字段
     gateway/run.py 渲染成 [⏳ 名称 42% · 6.8MB/s · ETA 3分] 消息推给用户
④ 收尾必须校验产物（字节数/退出码），并把状态写回 JSON
```

## 关键实现片段

### 1) 启动器：detached + 秒回

```python
import os, subprocess, sys, uuid, json
from pathlib import Path

JOB_DIR = Path("/opt/data/cache/myjobs"); JOB_DIR.mkdir(parents=True, exist_ok=True)
job_id = f"{datetime.now():%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
log = JOB_DIR / f"{job_id}.log"

with open(log, "wb") as fh:
    p = subprocess.Popen(
        [sys.executable, __file__, "--run", job_id, *rest],   # 列表传参，不经 shell
        stdout=fh, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,          # ⭐ 脱离父进程/终端，Hermes 被杀也不影响
        cwd="/tmp",
    )
(JOB_DIR / f"{job_id}.pid").write_text(str(p.pid))
print(json.dumps({"job_id": job_id, "state": "started"}))
# ← 到这里就 return 了，整个过程 <0.2s，QQ 对话完全不被阻塞
```

**要点**
- `start_new_session=True`（等价 `setsid`）是灵魂，缺了它容器重启就死。
- 一定用 **列表传参**，别拼 shell 字符串 —— 文件名带空格/中文/emoji 才不会坏。
- stderr 也要并进同一个日志，否则报错看不见。

### 2) 执行器：哨兵行进度协议

```python
def emit_progress(label, done, total, speed=None, eta=None):
    pct = round(done * 100 / total, 1) if total else 0
    print(f"@@PROGRESS@@ {json.dumps(dict(label=label, done=done, total=total,"
          f"speed=speed, percent=pct, eta=eta), ensure_ascii=False)}", flush=True)

def emit_state(**kw):
    print(f"@@STATE@@ {json.dumps(kw, ensure_ascii=False)}", flush=True)
```

- `flush=True` 必须有，否则日志里看不到实时进度。
- **每 0.5s 轮询产物大小**算进度，而不是解析下载器的输出
  （`curl -w`、`aria2` 的 stdout 格式多变，且 `-w` 只结束时输出一次）。
- JSON 每 ~1s 落盘一次（原子写：写 `.tmp` 再 `os.replace`），`status` 读快照。

### 3) status 命令要能抗重启

进度全在磁盘 JSON 里 → 容器重启后 `myjob status` 依然能报历史作业。
用 pid 存活检测（`os.kill(pid, 0)`）判断 `running` / `dead` / `done`，别信内存状态。

## Hermes 侧补丁（让进度自动进聊天）

> ⚠️ 改的是 Hermes 源码，`hermes update` 会被覆盖。改前 `cp` 一份备份。

### `/opt/hermes/tools/process_registry.py`

- 加 `_human_bytes(n)` → `1.18GB` / `6.87MB/s` 人类可读。
- `get()` / `poll()` / `list_sessions()`：扫日志尾部，正则抓
  `@@PROGRESS@@` / `@@STATE@@`，把解析结果塞进返回 dict 的
  `progress` / `state` 字段（含 `done_h`/`total_h`/`speed_h`）。
- 已知能用：正则 `r'@@(PROGRESS|STATE)@@\s*(\{.*\})'`，取最后一条。

### `/opt/hermes/gateway/run.py`

- 模块级加 `_strip_progress_lines(text)` —— 从要展示的日志里剔除哨兵行，别刷屏。
- 模块级加 `_format_progress_line(progress)` —— 渲染成
  `[⏳ 片名 3% · 6.87MB/s · ETA 13分18秒]`。
- 改 `_format_completion_text()` —— 完成消息带校验结论/最终速度。
- 给 `notify_mode == "all"` 分支加「有新输出但不是 agent 通知」时走状态更新消息，
  而不是把原始日志全推出去。

### 生效方式（别搞错）

| 改动 | 是否需要重启 |
|---|---|
| `approvals.mode`（config.yaml） | ❌ 每次调用现读 |
| `session_reset` | ✅ 必须重启 gateway（启动时快照进内存） |
| `process_registry.py` / `gateway/run.py`（源码） | ✅ 必须重启 gateway |

重启 gateway 前先确认没有正在跑的作业 —— 本机是 detached 子进程，
**作业本身不会死**，但 Hermes 的进度推送会断几秒。

## 验证清单

```bash
# 1) 启动真的秒回吗（实测应 <0.2s）
time python3 scripts/myjob.py start "<大文件>" "<目标目录>"

# 2) 哨兵行真的写进日志了吗
tail -5 /opt/data/cache/myjobs/<job_id>.log | grep -v '@@STATE@@'
grep -c '@@PROGRESS@@' /opt/data/cache/myjobs/<job_id>.log

# 3) Hermes 能解析吗（用 venv python，系统 python3 没 pytest/yaml）
/opt/hermes/.venv/bin/python -c "
import sys; sys.path.insert(0,'/opt/hermes')
from tools.process_registry import _human_bytes
print(_human_bytes(5928847706))"     # → 5.52GB

# 4) 渲染函数干净吗
/opt/hermes/.venv/bin/python -c "
import sys; sys.path.insert(0,'/opt/hermes')
from gateway.run import _format_progress_line, _strip_progress_lines
print(_format_progress_line({'label':'x','percent':42,'speed_h':'6.8MB/s','eta':'3分'}))"

# 5) 回归测试（改源码后必跑）
cd /opt/hermes && /opt/hermes/.venv/bin/python -m pytest tests/tools/ -q -o addopts=
```

## 😱 坑（都踩过）

1. **`terminal` 的 `sleep` >60s 会超时**（`exit_code 124`），
   `process(action="wait")` 也被夹到 60s 上限。
   → 等长任务别用 sleep 死等；用 `background=True` + `notify_on_complete=True`，
   或者干脆轮询 `status`/`tail` 看百分比。
2. **别用 `terminal` 跑超长 / 带大量引号的命令** —— 转义会把脚本搞坏。
   写脚本文件到 `/tmp` 再执行，或用 `execute_code`。
3. **系统 `python3` 缺 pytest / yaml**，测 Hermes 必须用
   `/opt/hermes/.venv/bin/python`。
4. `pytest tests/` 全量 >60s 会超时 → 拆成 `tests/tools/` 单跑，
   或用 `-o addopts=` 消掉内置 flag。
5. **哨兵行会污染日志**：`grep`/展示时记得过滤 `@@`，
   否则用户看到一堆 JSON。
6. **进度别存在内存里** —— 容器重启就没了。磁盘 JSON + pid 存活检测才是抗打。
7. 手改源码后 `.pyc` 缓存偶尔作怪，改完可清掉对应 `__pycache__` 再跑测试。
8. **删 `__pycache__` 这类递归删除在 Hermes 里要审批**，批量清理前先想好。

## 实战案例

`quark-nas-download` skill 的 `qdl.py` 就是本模式的标准落地：
`qdl.py start` 0.12s 返回；5.52GB 文件 4 分 10 秒下完，25–27MB/s；
字节校验 5928847706 完全一致；进度自动推进 QQ。
新增长任务时**直接抄 `qdl.py` 的骨架**，别从零写。
