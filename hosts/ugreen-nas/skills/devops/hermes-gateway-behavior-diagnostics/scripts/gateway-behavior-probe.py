#!/usr/bin/env python3
"""gateway-behavior-probe.py — 「用户感知行为」取证探针（只读，不改任何东西）。

回答三个问题：
  1. 最近的会话里，助手到底有没有输出「调工具前的旁白」？（空 content 占比）
  2. 模型有没有思维链？是不是最近被换过？（reasoning 列 + session_model_usage）
  3. 用户是不是经历了一段静默？投递真的成功了吗？（response ready / Sending response 对账）

用法：
    /opt/hermes/.venv/bin/python gateway-behavior-probe.py            # 最近 6 个会话
    /opt/hermes/.venv/bin/python gateway-behavior-probe.py --session <sid>
    /opt/hermes/.venv/bin/python gateway-behavior-probe.py --lines 60 # 多看几行日志
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sqlite3
from pathlib import Path

DB = Path("/opt/data/state.db")
LOG_FILES = [Path("/opt/data/logs/gateway.log"), Path("/opt/data/logs/agent.log")]


def ts(v: float | int | None) -> str:
    if not v:
        return "?"
    try:
        return dt.datetime.fromtimestamp(float(v)).strftime("%m-%d %H:%M:%S")
    except Exception:
        return "?"


def sessions(cur: sqlite3.Cursor, limit: int) -> list[str]:
    rows = cur.execute(
        "select session_id, max(timestamp) mx from messages "
        "where session_id is not null group by session_id order by mx desc limit ?",
        (limit,),
    ).fetchall()
    return [r[0] for r in rows]


def probe_session(cur: sqlite3.Cursor, sid: str) -> None:
    info = cur.execute(
        "select model, platform, session_start from sessions where session_id=?", (sid,)
    ).fetchone()
    rows = cur.execute(
        "select role, content, reasoning, reasoning_content, display_kind, timestamp, active "
        "from messages where session_id=? order by rowid",
        (sid,),
    ).fetchall()
    asst = [r for r in rows if r[0] == "assistant"]
    with_text = [r for r in asst if (r[1] or "").strip()]
    with_reason = [r for r in asst if (r[2] or "").strip()]
    with_rc = [r for r in asst if (r[3] or "").strip()]

    print(f"\n=== {sid} ===")
    if info:
        print(f"  模型={info[0]} 平台={info[1]} 起始={ts(info[2])}")
    print(
        f"  助手消息 {len(asst)} 条 | 有正文(旁白/回答) {len(with_text)} "
        f"| reasoning {len(with_reason)} | reasoning_content {len(with_rc)}"
    )
    if asst and len(with_text) * 4 < len(asst):
        print("  ⚠ 绝大多数助手消息没有正文 → 该模型不输出「调工具前的中文旁白」，")
        print("     用户会感到「中间一片空白」。")
    if with_reason or with_rc:
        print("  ℹ 有思维链 → 若用户看不到，是 display.show_reasoning=false 藏住了。")
    else:
        print("  ℹ 无思维链 → 该模型是「旁白型」，不产出 reasoning。")
    for r in with_text[-6:]:
        print(f"    [{ts(r[5])}] {r[1].strip().splitlines()[0][:90]}")


def probe_models(cur: sqlite3.Cursor) -> None:
    print("\n=== 模型使用历史（按首见倒序，用于定位「今天模型被换了」） ===")
    try:
        for r in cur.execute(
            "select session_id, model, task, api_call_count, reasoning_tokens, "
            "datetime(first_seen,'unixepoch','localtime') "
            "from session_model_usage order by first_seen desc limit 12"
        ):
            print(f"  {r[5]}  {r[1]:<18} task={r[2] or '-':<16} calls={r[3]:<4} reasoning_tok={r[4]}")
    except sqlite3.Error as exc:
        print(f"  (读不到 session_model_usage: {exc})")


def probe_logs(lines: int) -> None:
    print("\n=== 投递对账（inbound → response ready → Sending response） ===")
    pat = re.compile(r"inbound message:|response ready:|Sending response|Interrupt|Interrupted|send retry")
    for lf in LOG_FILES:
        if not lf.is_file():
            continue
        print(f"  --- {lf}")
        tail = lf.read_text(encoding="utf-8", errors="ignore").splitlines()[-4000:]
        hits = [ln for ln in tail if pat.search(ln)]
        for ln in hits[-lines:]:
            print("   ", ln[:200])
        # 静默检测：response ready 里 time 明显大于 api 调用耗时的回合
        for ln in hits:
            m = re.search(r"time=([\d.]+)s api_calls=(\d+) response=(\d+) chars", ln)
            if m and float(m.group(1)) > 60:
                print(
                    f"    ⚠ 长回合：{m.group(1)}s / {m.group(2)} 次 API 调用 / 最终只发了 "
                    f"{m.group(3)} 字符 —— 期间若无 Sending，用户就是干等了这么久。"
                )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", help="只探这一个会话 id")
    ap.add_argument("--limit", type=int, default=6, help="探最近 N 个会话")
    ap.add_argument("--lines", type=int, default=40, help="日志显示行数")
    args = ap.parse_args()

    if not DB.is_file():
        print(f"! 找不到 {DB}")
        return 1
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()
    print(f"state.db = {DB}   （只读打开）")

    if args.session:
        probe_session(cur, args.session)
    else:
        for sid in sessions(cur, args.limit):
            probe_session(cur, sid)
    probe_models(cur)
    probe_logs(args.lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
