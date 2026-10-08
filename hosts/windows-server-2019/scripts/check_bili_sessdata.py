#!/usr/bin/env python3
"""Check whether BILIBILI_SESSDATA in my_nonebot2/.env.dev is still valid.

Prints exactly one line: VALID uname=<...> / EXPIRED / ERROR <reason>.
Used by the daily cron job "bilibili-SESSDATA 过期检查".
"""
import json
import re
import subprocess
import sys

ENV = r"C:/Users/Administrator/Desktop/nb2/my_nonebot2/.env.dev"
NAV = "https://api.bilibili.com/x/web-interface/nav"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"
REFERER = "https://www.bilibili.com"


def fetch(sess: str, proxy: str | None) -> str | None:
    cmd = ["curl", "-s", "--max-time", "15", NAV,
           "-H", f"User-Agent: {UA}", "-H", f"Referer: {REFERER}",
           "-H", f"Cookie: SESSDATA={sess}"]
    if proxy:
        cmd[2:2] = ["-x", proxy]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        return r.stdout.strip() or None
    except Exception:
        return None


def main() -> None:
    try:
        src = open(ENV, encoding="utf-8").read()
        m = re.search(r"(?m)^BILIBILI_SESSDATA=(.+)$", src)
        if not m:
            print("ERROR no SESSDATA in .env.dev")
            return
        sess = m.group(1).strip()
    except Exception as exc:
        print(f"ERROR {type(exc).__name__} {exc}")
        return

    body = None
    for attempt, proxy in enumerate([None, None, None, "http://127.0.0.1:7892", "http://127.0.0.1:7892"], 1):
        body = fetch(sess, proxy)
        if body:
            break
    if not body:
        print("ERROR network unreachable after retries")
        return
    try:
        d = json.loads(body)
    except Exception:
        print("ERROR bad json from nav")
        return
    data = d.get("data") or {}
    if d.get("code") == 0 and data.get("isLogin"):
        print(f"VALID uname={data.get('uname')} mid={data.get('mid')}")
    else:
        print(f"EXPIRED code={d.get('code')} isLogin={data.get('isLogin')}")


if __name__ == "__main__":
    main()
