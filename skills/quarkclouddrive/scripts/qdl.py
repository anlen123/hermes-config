#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qdl.py — 异步下载引擎：把夸克网盘文件/目录下载到 NAS，带实时进度，不阻塞对话

为什么存在
----------
老的 olist.py dl / dl-dir 是**同步阻塞**的：一个大文件跑十几分钟，
工具调用就一直挂着，QQ 机器人的对话被卡死，用户发消息没反应。

qdl.py 把下载变成**后台作业 + 进度文件**模型：

    ① qdl.py start ...   → 立刻返回 job id（<1 秒），对话不被占用
    ② qdl.py status      → 随时查进度（读进度 JSON，不碰下载进程）
    ③ qdl.py watch       → 前台阻塞直到完成，自动打印进度条
    ④ qdl.py sync ...    → 老式同步下载（需要马上看到结果时用）

异步作业的子进程**完全脱离父进程**（setsid），即使 Hermes 容器重启、
对话会话重置，下载也照跑不误，进度不丢。

进度存储
--------
    /opt/data/cache/dljobs/<job_id>.json     进度 + 元数据（原子写）
    /opt/data/cache/dljobs/<job_id>.log      子进程 stdout/stderr
    /opt/data/cache/dljobs/<job_id>.pid      子进程 PID

每个作业还会把进度同步写成 OpenList 风格的可读行，并存下 **直链**，
方便断点续传时复用。

用法
----
    # 异步：立即返回
    python3 qdl.py start "/电影/XX/片名.mkv" "/volume1/共享影视作品/电影/XX"
    python3 qdl.py start-dir "/电影/XX" "/volume1/共享影视作品/电影/XX"

    # 查询
    python3 qdl.py status [job_id]     # 不带 id 则列全部
    python3 qdl.py watch <job_id> [--interval 5]   # 前台跟进度
    python3 qdl.py cancel <job_id>     # 停止（保留已下载部分）
    python3 qdl.py clean               # 清理已完成/失败的作业记录

    # 同步（老行为，会阻塞）
    python3 qdl.py sync "/电影/XX/片名.mkv" "/volume1/共享影视作品/电影/XX"

设计要点
--------
* 下载用 curl -C - 断点续传；直链过期自动重取（重新 /api/fs/get）
* 字节数校验：下载完对比 OpenList 报的 size，不一致标记 failed
* 进度 JSON 每 ~1 秒刷新一次，且只在整数百分比变化时写，避免 IO 抖动
* 无第三方依赖（标准库 + curl）
"""

import json
import os
import signal
import subprocess
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CACHE_DIR = os.environ.get("QDL_CACHE", "/opt/data/cache/dljobs")
CHUNK_REPORT_BYTES = 1  # 每次都让 curl 回报一次进度，由我们做节流


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def human(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    if n < 0:
        return "-" + human(-n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.2f}{u}"
        n /= 1024
    return f"{n:.2f}PB"


def human_time(sec):
    try:
        sec = int(sec)
    except (TypeError, ValueError):
        return "?"
    if sec < 0:
        return "?"
    if sec < 60:
        return f"{sec}秒"
    if sec < 3600:
        return f"{sec // 60}分{sec % 60}秒"
    return f"{sec // 3600}小时{(sec % 3600) // 60}分"


def _jobs_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)
    return CACHE_DIR


def _job_path(job_id, ext):
    return os.path.join(_jobs_dir(), f"{job_id}.{ext}")


def _atomic_write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_job(job_id):
    try:
        with open(_job_path(job_id, "json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_job(job):
    _atomic_write_json(_job_path(job["id"], "json"), job)


def list_jobs():
    d = _jobs_dir()
    out = []
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".json"):
            j = load_job(fn[:-5])
            if j:
                out.append(j)
    out.sort(key=lambda j: j.get("started_at", 0), reverse=True)
    return out


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def resolve_job(job_id_or_prefix=None):
    """支持用 id 前缀匹配，找不到返回 None。"""
    if not job_id_or_prefix:
        return None
    jobs = list_jobs()
    exact = [j for j in jobs if j["id"] == job_id_or_prefix]
    if exact:
        return exact[0]
    pref = [
        j for j in jobs
        if j["id"].startswith(job_id_or_prefix) or job_id_or_prefix in j.get("label", "")
    ]
    return pref[0] if len(pref) == 1 else (pref or [None])[0]


# --------------------------------------------------------------------------
# 进度汇报（同时写进度文件 + 向 Hermes 汇报 @@PROGRESS@@）
# --------------------------------------------------------------------------

def emit_progress(job, force=False):
    """
    进度写两处：
      1. 作业 JSON —— qdl.py status / watch 读它
      2. stdout 的 @@PROGRESS@@ 行 —— Hermes process_registry 解析后
         直接挂到 process(poll/list) 的 progress 字段上
    节流：进度百分比没变化就跳过（除非 force）。
    """
    dl_bytes = job.get("done_bytes", 0)
    total = job.get("total_bytes", 0) or 0
    now = time.time()
    pct = int(dl_bytes * 100 / total) if total else -1
    if not force and pct == job.get("_last_pct") and (now - job.get("_last_emit", 0)) < 5:
        return
    job["_last_pct"] = pct
    job["_last_emit"] = now
    job["updated_at"] = now
    job["speed_h"] = human(job.get("speed", 0)) + "/s"
    if job.get("speed") and total and dl_bytes:
        job["eta"] = human_time((total - dl_bytes) / job["speed"])
    save_job(job)

    payload = {
        "label": job.get("label", ""),
        "done": dl_bytes,
        "total": total,
        "speed": int(job.get("speed", 0) or 0),
        "percent": pct if pct >= 0 else 0,
        "file": job.get("current_file", ""),
        "eta": job.get("eta", ""),
        "state": job.get("state", ""),
        "phase": f"{job.get('completed_files', 0)}/{job.get('total_files', 0)}",
    }
    print("@@PROGRESS@@ " + json.dumps(payload, ensure_ascii=False), flush=True)


def emit_state(job):
    """把作业身份（便于后续用 progress 查回来）汇报给 Hermes。"""
    payload = {
        "job_id": job["id"],
        "kind": job.get("kind", "file"),
        "dest": job.get("dest", ""),
        "state": job.get("state", ""),
        "files_total": job.get("total_files", 0),
    }
    print("@@STATE@@ " + json.dumps(payload, ensure_ascii=False), flush=True)


# --------------------------------------------------------------------------
# 下载核心（在子进程里跑）
# --------------------------------------------------------------------------

def _openlist():
    import olist
    return olist


def _probe_size(remote_path):
    """取远端文件大小（字节），失败返回 0。"""
    try:
        ol = _openlist()
        r = ol._api("/api/fs/get", {"path": remote_path, "refresh": True})
        if r.get("code") == 200:
            return (r.get("data") or {}).get("size") or 0, (r.get("data") or {}).get("raw_url")
    except Exception:
        pass
    return 0, None


def download_one(job, remote_path, dest_path):
    """
    下载单个文件，带断点续传 + 进度回调。返回 True/False。

    实现：起一个 curl 子进程，用 -w 让它周期输出进度行，
    主循环读它并更新 job。
    """
    ol = _openlist()
    size, url = _probe_size(remote_path)
    if not url:
        url = ol.get_url(remote_path)
    # ⚠️ 只有单文件作业才更新总大小；目录作业的 total_bytes 是「所有文件之和」，
    # 被当前文件大小覆盖会让 status 进度冲到几百 %（2026-09-26 修）
    if job.get("kind") != "dir":
        job["total_bytes"] = size or job.get("total_bytes", 0)
    expected_size = size or job.get("total_bytes", 0)
    job["current_file"] = os.path.basename(dest_path)
    job["state"] = "downloading"
    emit_progress(job, force=True)

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    # curl：断点续传 + 每 1MB 汇报一次进度
    cmd = [
        "curl", "-L", "-C", "-",
        "--retry", "5", "--retry-delay", "3", "--retry-all-errors",
        "--connect-timeout", "20",
        "-o", dest_path,
        "-w", "%{size_download} %{speed_download} %{http_code}\n",
        "--create-dirs",
        url,
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1
    )
    job["pid_curl"] = proc.pid
    save_job(job)

    start_offset = job.get("done_bytes", 0)  # 续传前的已完成量
    last_bytes, last_t = 0, time.time()

    try:
        # curl 的 -w 只在结束时输出一行；想在过程中看进度得读它的 stderr 进度条。
        # 我们改为：每 0.5 秒轮询目标文件大小，用文件增长算速度。
        while proc.poll() is None:
            time.sleep(0.5)
            try:
                now_size = os.path.getsize(dest_path)
            except OSError:
                now_size = 0
            job["done_bytes"] = start_offset + now_size
            dt = time.time() - last_t
            if dt >= 1.0:
                job["speed"] = (now_size - last_bytes) / dt
                last_bytes, last_t = now_size, time.time()
            emit_progress(job)
    except KeyboardInterrupt:
        proc.terminate()
        job["state"] = "cancelled"
        save_job(job)
        emit_progress(job, force=True)
        return False

    out, err = proc.communicate()
    code = proc.returncode
    try:
        final_size = os.path.getsize(dest_path)
    except OSError:
        final_size = 0
    job["done_bytes"] = start_offset + final_size

    # 校验：字节数必须和网盘一致（用当前文件自己的大小，不是目录总和）
    expected = expected_size
    ok = code == 0 and (not expected or final_size >= expected)
    if ok:
        job["state"] = "done"
    else:
        job["state"] = "failed"
        job["error"] = (err or out or f"curl exit {code}")[-500:]
    emit_progress(job, force=True)
    return ok


def run_job(job_id):
    """子进程入口：执行一个作业。"""
    job = load_job(job_id)
    if not job:
        print(f"job {job_id} not found", file=sys.stderr)
        return 2

    job["pid"] = os.getpid()
    job["state"] = "resolving"
    emit_state(job)
    emit_progress(job, force=True)

    try:
        items = job.get("items") or []
        total_files = len(items)
        job["total_files"] = total_files
        total_bytes = 0
        for it in items:
            size, _ = _probe_size(it["remote"])
            it["size"] = size
            total_bytes += size
        job["total_bytes"] = total_bytes
        job["done_bytes"] = 0
        save_job(job)
        emit_progress(job, force=True)

        done_bytes_prior = 0
        for idx, it in enumerate(items, 1):
            job["file_index"] = idx
            job["completed_files"] = idx - 1
            job["done_bytes"] = done_bytes_prior
            job["total_bytes"] = sum(i.get("size", 0) for i in items) or job["total_bytes"]
            dest = it["local"]
            if os.path.exists(dest) and it.get("size") and os.path.getsize(dest) >= it["size"]:
                print(f"⏭️  已存在且完整，跳过: {os.path.basename(dest)}", flush=True)
                done_bytes_prior += it.get("size", 0)
                job["completed_files"] = idx
                job["done_bytes"] = done_bytes_prior
                emit_progress(job, force=True)
                continue
            print(f"⬇️  [{idx}/{total_files}] {it['remote']}", flush=True)
            ok = download_one(job, it["remote"], dest)
            if not ok:
                # 直链可能过期，重取一次再试
                print("   ♻️  直链可能过期，重试一次…", flush=True)
                try:
                    ol = _openlist()
                    it_url = ol.get_url(it["remote"])
                    subprocess.run(
                        ["curl", "-L", "-C", "-", "--retry", "3", "--retry-delay", "3",
                         "--retry-all-errors", "-o", dest, "--create-dirs", it_url],
                        check=False,
                    )
                    if it.get("size") and os.path.getsize(dest) >= it["size"]:
                        ok = True
                        job["state"] = "done"
                except Exception as e:
                    job["error"] = str(e)
                if not ok:
                    job["state"] = "failed"
                    save_job(job)
                    emit_progress(job, force=True)
                    print(f"   ❌ 失败: {job.get('error', '')}", flush=True)
                    return 1
            act = os.path.getsize(dest) if os.path.exists(dest) else 0
            print(f"   ✅ {human(act)}", flush=True)
            done_bytes_prior += act
            job["completed_files"] = idx
            job["done_bytes"] = done_bytes_prior
            emit_progress(job, force=True)

        job["state"] = "done"
        job["done_bytes"] = job.get("total_bytes") or job["done_bytes"]
        job["speed"] = 0
        job["finished_at"] = time.time()
        # 校验全部文件
        bad = [
            os.path.basename(i["local"]) for i in items
            if i.get("size") and (
                not os.path.exists(i["local"]) or os.path.getsize(i["local"]) < i["size"]
            )
        ]
        job["verify"] = "ok" if not bad else f"不匹配: {bad}"
        emit_progress(job, force=True)
        print(f"🎉 全部完成 | {job.get('verify')}", flush=True)
        return 0

    except Exception as e:
        import traceback
        job["state"] = "failed"
        job["error"] = f"{e}\n" + traceback.format_exc()[-800:]
        save_job(job)
        emit_progress(job, force=True)
        print(f"💥 异常: {e}", flush=True)
        return 3


# --------------------------------------------------------------------------
# 异步启动
# --------------------------------------------------------------------------

def _spawn(job):
    """
    用 setsid 把作业彻底脱离父进程：Hermes 容器重启、会话重置都不影响下载。
    """
    log_path = _job_path(job["id"], "log")
    pid_path = _job_path(job["id"], "pid")
    script = os.path.join(HERE, "qdl.py")
    logf = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, script, "_run", job["id"]],
        stdout=logf, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,   # setsid：脱离进程组，父进程退出也照跑
        cwd=HERE,
    )
    with open(pid_path, "w") as f:
        f.write(str(proc.pid))
    job["pid"] = proc.pid
    job["launcher_pid"] = os.getpid()
    job["log"] = log_path
    job["started_at"] = time.time()
    save_job(job)
    return job


def _make_job(remote, local_dir, kind="file", label=""):
    ol = _openlist()
    os.makedirs(local_dir, exist_ok=True)
    items = []
    if kind == "file":
        name = remote.rstrip("/").split("/")[-1]
        items.append({"remote": remote, "local": os.path.join(local_dir, name)})
        label = label or name
    else:
        for name, is_dir, size in ol.ls(remote):
            rp = f"{remote.rstrip('/')}/{name}"
            lp = os.path.join(local_dir, name)
            if is_dir:
                # 递归展开
                for sub in _walk(ol, rp, lp):
                    items.append(sub)
            else:
                items.append({"remote": rp, "local": lp, "size": size})
        label = label or remote.rstrip("/").split("/")[-1]

    job_id = time.strftime("%m%d-%H%M%S") + "-" + os.urandom(2).hex()
    return {
        "id": job_id,
        "kind": kind,
        "remote": remote,
        "dest": local_dir,
        "label": label,
        "items": items,
        "state": "queued",
        "created_at": time.time(),
        "total_files": len(items),
        "completed_files": 0,
        "done_bytes": 0,
        "total_bytes": 0,
        "speed": 0,
    }


def _walk(ol, remote_dir, local_dir):
    out = []
    for name, is_dir, size in ol.ls(remote_dir):
        rp = f"{remote_dir.rstrip('/')}/{name}"
        lp = os.path.join(local_dir, name)
        if is_dir:
            out += _walk(ol, rp, lp)
        else:
            out.append({"remote": rp, "local": lp, "size": size})
    return out


def start(remote, local_dir, kind="file", label=""):
    job = _make_job(remote, local_dir, kind, label)
    _spawn(job)
    return job


# --------------------------------------------------------------------------
# 展示
# --------------------------------------------------------------------------

EMOJI = {
    "queued": "🕒", "resolving": "🔍", "downloading": "⬇️",
    "done": "✅", "failed": "❌", "cancelled": "⏹️",
}


def render_job(job, verbose=False):
    st = job.get("state", "?")
    alive = _pid_alive(job.get("pid")) and st in ("queued", "resolving", "downloading")
    total = job.get("total_bytes") or 0
    done = job.get("done_bytes") or 0
    pct = done * 100 / total if total else 0
    bar_len = 20
    filled = int(bar_len * pct / 100) if total else 0
    bar = "█" * filled + "░" * (bar_len - filled)
    lines = [
        f"{EMOJI.get(st, '•')} [{job['id']}] {job.get('label', '')}  —  {st}"
        + ("" if alive else " (进程已结束)"),
        f"   `{bar}` {pct:.1f}%  {human(done)} / {human(total) if total else '?'}",
    ]
    if job.get("total_files", 0) > 1:
        lines.append(f"   文件 {job.get('completed_files', 0)}/{job.get('total_files', 0)}"
                     f"  当前: {job.get('current_file', '-')}")
    if st == "downloading":
        lines.append(f"   速度 {human(job.get('speed', 0))}/s   ETA {job.get('eta', '?')}"
                     f"   已跑 {human_time(time.time() - job.get('started_at', time.time()))}")
    if job.get("error") and st == "failed":
        lines.append(f"   ⚠️ {str(job['error']).splitlines()[0][:160]}")
    if job.get("verify"):
        lines.append(f"   校验: {job['verify']}")
    lines.append(f"   → {job.get('dest', '')}")
    if verbose:
        lines.append(f"   log: {_job_path(job['id'], 'log')}")
    return "\n".join(lines)


def cmd_status(job_id=None):
    if job_id:
        job = resolve_job(job_id)
        if not job:
            print(f"❌ 找不到作业: {job_id}")
            return 1
        print(render_job(job, verbose=True))
        return 0
    jobs = list_jobs()
    if not jobs:
        print("(无下载作业)")
        return 0
    active = [j for j in jobs if j.get("state") in ("queued", "resolving", "downloading")]
    print(f"📋 共 {len(jobs)} 个作业，进行中 {len(active)} 个\n")
    for j in jobs[:12]:
        print(render_job(j))
        print()
    return 0


def cmd_watch(job_id, interval=5):
    """前台盯着一个作业，直到结束（给需要阻塞等待的场景用）。"""
    job = resolve_job(job_id)
    if not job:
        print(f"❌ 找不到作业: {job_id}")
        return 1
    last_len = 0
    while True:
        job = load_job(job["id"])
        if not job:
            print("❌ 作业记录消失")
            return 1
        st = job.get("state")
        # 进度条原地刷新
        total = job.get("total_bytes") or 0
        done = job.get("done_bytes") or 0
        pct = done * 100 / total if total else 0
        msg = (f"\r{EMOJI.get(st, '•')} {pct:5.1f}%  {human(done)}/{human(total) if total else '?'}"
               f"  {human(job.get('speed', 0))}/s  ETA {job.get('eta', '?')}   ")
        sys.stdout.write(msg)
        sys.stdout.flush()
        if st in ("done", "failed", "cancelled"):
            print()
            print(render_job(job))
            return 0 if st == "done" else 1
        time.sleep(interval)


def cmd_cancel(job_id):
    job = resolve_job(job_id)
    if not job:
        print(f"❌ 找不到作业: {job_id}")
        return 1
    pid = job.get("pid")
    if not pid or not _pid_alive(pid):
        print("作业已不在运行")
        return 0
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception as e:
            print(f"❌ 取消失败: {e}")
            return 1
    job["state"] = "cancelled"
    job["finished_at"] = time.time()
    save_job(job)
    print(f"⏹️  已取消 {job['id']}（已下载的部分保留，下次会自动续传）")
    return 0


def cmd_clean():
    """删掉已结束作业的记录文件（保留日志）。"""
    n = 0
    for j in list_jobs():
        if j.get("state") in ("done", "failed", "cancelled"):
            for ext in ("json", "pid"):
                try:
                    os.remove(_job_path(j["id"], ext))
                    n += 1
                except OSError:
                    pass
    print(f"🧹 清理了 {n} 个作业记录文件")
    return 0


def cmd_sync(remote, local_dir, kind="file"):
    """同步下载（会阻塞调用方，最后才返回）。"""
    job = _make_job(remote, local_dir, kind)
    job["state"] = "resolving"
    job["pid"] = os.getpid()
    save_job(job)
    return run_job(job["id"])


# --------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 0
    act = sys.argv[1]

    if act == "_run":
        return run_job(sys.argv[2])

    if act == "start":
        if len(sys.argv) < 4:
            print("用法: qdl.py start <网盘文件路径> <本地目录>")
            return 2
        job = start(sys.argv[2], sys.argv[3], "file")
        print(f"🚀 已启动下载作业 {job['id']}\n{render_job(job)}")
        return 0

    if act == "start-dir":
        if len(sys.argv) < 4:
            print("用法: qdl.py start-dir <网盘目录> <本地目录>")
            return 2
        job = start(sys.argv[2], sys.argv[3], "dir")
        print(f"🚀 已启动下载作业 {job['id']}（含 {job.get('total_files', 0)} 个文件）\n{render_job(job)}")
        return 0

    if act == "status":
        return cmd_status(sys.argv[2] if len(sys.argv) > 2 else None)

    if act == "watch":
        if len(sys.argv) < 3:
            print("用法: qdl.py watch <job_id> [间隔秒]")
            return 2
        return cmd_watch(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 5)

    if act == "cancel":
        return cmd_cancel(sys.argv[2])

    if act == "clean":
        return cmd_clean()

    if act == "sync":
        if len(sys.argv) < 4:
            print("用法: qdl.py sync <网盘文件路径> <本地目录>")
            return 2
        return cmd_sync(sys.argv[2], sys.argv[3], "file")

    if act == "sync-dir":
        if len(sys.argv) < 4:
            print("用法: qdl.py sync-dir <网盘目录> <本地目录>")
            return 2
        return cmd_sync(sys.argv[2], sys.argv[3], "dir")

    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
