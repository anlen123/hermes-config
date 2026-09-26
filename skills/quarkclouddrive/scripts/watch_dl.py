#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盯住一个 qdl.py 作业，按「逐文件字节比对」算真实进度，全部到位才退出。
用法: watch_dl.py <job_json路径> [间隔秒] [最长分钟]
输出: 人类可读中文进度 + @@PROGRESS@@ 控制行（供 Hermes process 工具渲染）
"""
import json
import os
import subprocess
import sys
import time

JOB = sys.argv[1]
INTERVAL = int(sys.argv[2]) if len(sys.argv) > 2 else 20
MAX_MIN = int(sys.argv[3]) if len(sys.argv) > 3 else 90


def human(n):
    for u, s in (("T", 1024**4), ("G", 1024**3), ("M", 1024**2), ("K", 1024)):
        if n >= s:
            return f"{n/s:.2f}{u}"
    return f"{n}B"


def load():
    with open(JOB, encoding="utf-8") as f:
        return json.load(f)


def probe():
    j = load()
    items = j.get("items") or []
    total = sum(i.get("size") or 0 for i in items)
    done = 0
    n_done = 0
    n_part = 0
    missing = []
    for i in items:
        local = i.get("local") or ""
        sz = i.get("size") or 0
        try:
            s = os.path.getsize(local)
        except OSError:
            s = 0
        done += min(s, sz) if sz else s
        if sz and s >= sz:
            n_done += 1
        else:
            (missing if s == 0 else []).append(os.path.basename(local))
            if s > 0:
                n_part += 1
    return j, total, done, n_done, n_part, len(items), missing


def curl_alive():
    try:
        out = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return False
    return sum(1 for l in out.splitlines() if l.strip().startswith("curl")) > 0


def main():
    deadline = time.time() + MAX_MIN * 60
    last_done = 0
    last_t = time.time()
    while True:
        try:
            j, total, done, n_done, n_part, n_all, missing = probe()
        except Exception as e:  # noqa
            print("读取作业失败:", e, flush=True)
            time.sleep(INTERVAL)
            continue

        now = time.time()
        dt = max(now - last_t, 0.001)
        speed = max(done - last_done, 0) / dt
        last_done, last_t = done, now
        pct = done * 100 / total if total else 0
        label = j.get("label") or os.path.basename(JOB)
        eta = ""
        if speed > 0 and total > done:
            rem = (total - done) / speed
            eta = f"{int(rem//60)}分{int(rem%60)}秒"

        print(
            f"【{label}】已下 {n_done}/{n_all} 个文件"
            f"（{n_part} 个正在下），共 {human(done)}/{human(total)}"
            f"（{pct:.1f}%），每秒约 {human(speed)}"
            + (f"，预计还需 {eta}" if eta else ""),
            flush=True,
        )
        print("@@" + "PROGRESS@@ " + json.dumps(
            {"label": label, "done": done, "total": total, "percent": round(pct, 1),
             "speed": speed, "eta": eta, "files_done": n_done, "files_total": n_all},
            ensure_ascii=False), flush=True)

        if n_done == n_all and n_all:
            print("✅ 全部文件已到位（逐文件字节比对通过），收工。", flush=True)
            return 0
        if now > deadline:
            print("⏰ 监视超时退出，下载可能仍在后台继续。", flush=True)
            return 2
        if not curl_alive() and n_done < n_all:
            time.sleep(INTERVAL)
            if not curl_alive():
                print(f"⚠️ 下载进程已不在，但仍有 {n_all - n_done} 个文件没到位"
                      f"（缺: {', '.join(missing[:8]) or '部分文件未完成'}）。", flush=True)
                return 3
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
