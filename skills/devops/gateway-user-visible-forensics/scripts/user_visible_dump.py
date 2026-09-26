#!/usr/bin/env python3
"""取证：聊天网关里「用户屏幕上到底出现了什么」。

用法:
    python3 user_visible_dump.py [--minutes 60] [--db /opt/data/state.db]
                                 [--log-dir /opt/data/logs] [--head 200]

输出:
  1) 窗口内真正生成了内容的助手消息（含英文占比 + ASCII 词，用于「还有英文」类投诉）
  2) 网关日志里的 Sending response / response ready / 发送失败 行
  3) 疑似「静默回合」提示：单回合耗时很长，期间却没有 Sending response

纯标准库；建议用 /opt/hermes/.venv/bin/python 跑（避免环境差异）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sqlite3
import sys

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_\-./]{1,}")
# 这些英文词属于技术标识/路径噪声，不必当成「没汉化」
SKIP = re.compile(
    r"^(https?|www|volume1|opt|data|usr|bin|etc|var|tmp|json|yaml|yml|py|sh|log|md|txt|"
    r"zip|rar|png|jpg|mp4|mkv|and|the|to|of|a|in|is|it|for|with|on|be|are|this|that|"
    r"my|your|i|s|t|m|k|g|hdr|mb|gb|kb|eta|nas|api|id|ok)$",
    re.I,
)
SEND_RE = re.compile(r"Sending response \((\d+) chars\)")
READY_RE = re.compile(r"response ready:.*?time=([\d.]+)s api_calls=(\d+) response=(\d+) chars")
FAIL_RE = re.compile(r"Send failed|send retry")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="聊天网关用户可见输出取证")
    p.add_argument("--minutes", type=float, default=60.0, help="回溯多少分钟（默认 60）")
    p.add_argument("--db", default="/opt/data/state.db")
    p.add_argument("--log-dir", default="/opt/data/logs")
    p.add_argument("--head", type=int, default=200, help="每条消息打印的字符数")
    p.add_argument("--logs", default="gateway.log,agent.log")
    return p.parse_args(argv)


def english_report(text: str):
    words = sorted({w for w in ASCII_WORD.findall(text) if not SKIP.match(w)})
    ascii_letters = sum(1 for ch in text if "a" <= ch.lower() <= "z")
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    total = ascii_letters + cjk
    ratio = (ascii_letters / total * 100.0) if total else 0.0
    return ratio, words


def dump_messages(db: str, cutoff: float, head: int):
    if not os.path.exists(db):
        print(f"!! 找不到会话库: {db}")
        return [], 0
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cur = con.cursor()
    try:
        rows = list(
            cur.execute(
                """select session_id, timestamp, content, display_kind
                     from messages
                    where role='assistant' and content is not null and content!=''
                      and timestamp >= ?
                    order by timestamp""",
                (cutoff,),
            )
        )
    except sqlite3.Error as exc:
        print(f"!! 查询失败（列可能不同）: {exc}")
        return [], 0
    finally:
        con.close()

    hidden = 0
    print(f"\n===== ① 窗口内生成内容的助手消息（{len(rows)} 条）=====")
    for sid, ts, content, kind in rows:
        when = dt.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S")
        if kind == "hidden":
            hidden += 1
            print(f"[{when}] {str(sid)[:18]} (hidden，不会外发) — 跳过正文")
            continue
        ratio, words = english_report(content)
        flag = "⚠️" if ratio >= 12 else "  "
        print(f"[{when}] {str(sid)[:18]} 英文占比 {ratio:4.1f}% {flag} len={len(content)}")
        print("           " + content.replace("\n", " ")[:head])
        if words:
            print("           ASCII词: " + ", ".join(words[:40]))
    if hidden:
        print(f"\n（另有 {hidden} 条 display_kind=hidden，属内部消息，排查时排除）")
    return rows, len(rows)


def dump_logs(log_dir: str, names: str, cutoff_dt: dt.datetime, head: int):
    print(f"\n===== ② 网关日志（{cutoff_dt:%m-%d %H:%M} 之后）=====")
    sends = 0
    readies = []
    fails = 0
    for name in [n.strip() for n in names.split(",") if n.strip()]:
        path = os.path.join(log_dir, name)
        if not os.path.exists(path):
            print(f"-- {name}: 不存在")
            continue
        hits = []
        with open(path, errors="ignore") as fh:
            for line in fh:
                m = TS_RE.match(line)
                if not m:
                    continue
                try:
                    ts = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if ts < cutoff_dt:
                    continue
                if SEND_RE.search(line) or READY_RE.search(line) or FAIL_RE.search(line):
                    hits.append((ts, line.rstrip()))
        print(f"-- {name}: {len(hits)} 条相关行")
        for ts, line in hits[-60:]:
            if SEND_RE.search(line):
                sends += 1
            m = READY_RE.search(line)
            if m:
                readies.append((ts, float(m.group(1)), int(m.group(2)), int(m.group(3))))
            if FAIL_RE.search(line):
                fails += 1
            print(f"   [{ts:%H:%M:%S}] {line[:head]}")

    print(f"\n===== ③ 小结 =====")
    print(f"成功发送(Sending response): {sends} 条；发送失败/重试行: {fails} 条")
    if readies:
        print("回合耗时（response ready）:")
        for ts, secs, calls, chars in readies:
            mark = "⚠️ 长时间静默" if secs >= 120 and calls >= 10 else ""
            print(f"   [{ts:%H:%M:%S}] {secs:.1f}s / {calls} 次工具调用 / 回复 {chars} 字符 {mark}")
    if sends == 0:
        print("⚠️ 窗口内一条 Sending response 都没有 —— 用户很可能看到的是空白，"
              "或消息全部投递失败（检查 400 /v2/users/<id>/messages 之类错误）。")
    return sends, readies


def main(argv=None) -> int:
    args = parse_args(argv)
    now = dt.datetime.now()
    cutoff_dt = now - dt.timedelta(minutes=args.minutes)
    print(f"取证窗口：{cutoff_dt:%Y-%m-%d %H:%M:%S} → {now:%Y-%m-%d %H:%M:%S}")
    dump_messages(args.db, cutoff_dt.timestamp(), args.head)
    dump_logs(args.log_dir, args.logs, cutoff_dt, args.head)
    print("\n提示：DB 里有记录 ≠ 用户看到了。以「Sending response」行 + 是否报 400 为准。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
