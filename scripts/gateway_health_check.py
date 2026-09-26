#!/usr/bin/env python3
"""
Hermes 网关健康检查（供 cron --script 调用）
===========================================
只做检测与输出，不做发送。输出内容会被注入 cron 任务的 prompt。

背景：2026-09-25 故障 —— QQ WebSocket 心跳保活失效，服务端每 61 秒断连，
最终重连逻辑被拖死，进程活着但日志静默 3 小时 14 分（假死）。

输出约定：
  - 一切正常 -> 输出 "STATUS=OK" 单行，cron prompt 据此保持静默
  - 异常     -> 输出 "STATUS=WARN/CRIT" + 详细多行说明
  - 自动重启 -> 附带 ACTION=RESTARTED 表示本次已执行重启
"""

import json
import os
import sys
import time
import subprocess
from datetime import datetime, timezone, timedelta

GATEWAY_STATE = "/opt/data/gateway_state.json"
ERROR_LOG = "/opt/data/logs/errors.log"
WATCHDOG_LOG = "/opt/data/logs/watchdog.log"
STATE_FILE = "/opt/data/logs/.watchdog_state.json"

WARN_MIN = 5        # 停滞 >= 5 分钟 -> WARN
CRIT_MIN = 15       # 停滞 >= 15 分钟 -> CRIT
RESTART_GRACE_SEC = 120  # CRIT 后缓冲 2 分钟再重启
DISCONNECT_ALERT = 10    # 最近 5 分钟断连次数阈值

CST = timezone(timedelta(hours=8))


def log(msg, level="INFO"):
    ts = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} [{level}] {msg}"
    try:
        with open(WATCHDOG_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def pid_alive(pid):
    try:
        with open(f"/proc/{pid}/stat", "r") as f:
            stat = f.read().split()
        return stat[2] != "Z"
    except Exception:
        return False


def check_state():
    st = load_json(GATEWAY_STATE)
    if not st:
        return None, {}, "无法读取 gateway_state.json"
    updated = st.get("updated_at")
    if not updated:
        return None, st, "gateway_state.json 缺少 updated_at"
    try:
        dt = datetime.fromisoformat(updated)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception as e:
        return None, st, f"解析 updated_at 失败: {e}"
    stall = (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
    qq = (st.get("platforms") or {}).get("qqbot", {}) or {}
    meta = {
        "updated_at": updated,
        "gateway_state": st.get("gateway_state"),
        "qq_state": qq.get("state"),
        "qq_error": qq.get("error_message") or qq.get("error_code"),
    }
    return stall, meta, None


def count_disconnects(minutes=5):
    try:
        cutoff = datetime.now() - timedelta(minutes=minutes)
        n = 0
        with open(ERROR_LOG, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "WebSocket" not in line:
                    continue
                try:
                    ts = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue
                if ts >= cutoff:
                    n += 1
        return n
    except Exception:
        return 0


def restart():
    log("执行网关重启（SIGTERM -> PID 1）", "CRIT")
    try:
        subprocess.run(["kill", "-TERM", "1"], timeout=10)
        return True
    except Exception as e:
        log(f"重启失败: {e}", "ERROR")
        return False


def main():
    now = time.time()
    prev = load_json(STATE_FILE, {}) or {}

    stall, meta, err = check_state()
    alive = pid_alive(1)
    disc = count_disconnects(5)

    level = "OK"
    reasons = []

    if not alive:
        level = "CRIT"
        reasons.append("网关进程（PID 1）不存在或为僵尸")
    elif err:
        level = "WARN"
        reasons.append(f"状态文件异常: {err}")
    elif stall >= CRIT_MIN:
        level = "CRIT"
        reasons.append(f"状态停止更新 {stall:.1f} 分钟（阈值 {CRIT_MIN} 分钟）")
    elif stall >= WARN_MIN:
        level = "WARN"
        reasons.append(f"状态停止更新 {stall:.1f} 分钟（阈值 {WARN_MIN} 分钟）")

    if disc >= DISCONNECT_ALERT:
        if level == "OK":
            level = "WARN"
        reasons.append(f"最近 5 分钟 WebSocket 断连 {disc} 次（疑似心跳保活失效）")

    snapshot = {
        "last_check": datetime.now(CST).isoformat(),
        "level": level,
        "stall_min": round(stall, 2) if stall is not None else None,
        "pid_alive": alive,
        "disconnects_5min": disc,
        "reasons": reasons,
    }

    # ── 正常：静默 ──
    if level == "OK":
        log(f"正常 | 停滞={stall:.1f}min 进程=活 断连={disc}/5min")
        save_json(STATE_FILE, snapshot)
        print("STATUS=OK")
        return 0

    reason_txt = "；".join(reasons)
    log(f"异常 [{level}] {reason_txt}", "WARN" if level == "WARN" else "CRIT")

    # ── 去抖：同级别 10 分钟内不重复打扰 ──
    last_ts = prev.get("last_alert_ts", 0)
    last_level = prev.get("last_alert_level")
    debounced = (level == last_level) and (now - last_ts < 600)

    action = "NONE"
    # ── CRIT + 进程活着：缓冲后自动重启 ──
    if level == "CRIT" and alive:
        crit_since = prev.get("crit_since")
        if not crit_since:
            snapshot["crit_since"] = now
            action = "GRACE_STARTED"
            log(f"进入 CRIT，{RESTART_GRACE_SEC}s 后自动重启")
        else:
            waited = now - crit_since
            if waited >= RESTART_GRACE_SEC:
                if restart():
                    action = "RESTARTED"
                    snapshot["last_restart_ts"] = now
                else:
                    action = "RESTART_FAILED"
                snapshot.pop("crit_since", None)
            else:
                snapshot["crit_since"] = crit_since
                action = "GRACE_WAITING"
                log(f"缓冲中：{waited:.0f}s / {RESTART_GRACE_SEC}s")

    if not debounced:
        snapshot["last_alert_ts"] = now
        snapshot["last_alert_level"] = level
        announce = "YES"
    else:
        announce = "NO"

    save_json(STATE_FILE, snapshot)

    # ── 输出给 cron prompt ──
    print(f"STATUS={level}")
    print(f"ANNOUNCE={announce}")
    print(f"ACTION={action}")
    print(f"STALL_MIN={stall:.1f}" if stall is not None else "STALL_MIN=?")
    print(f"PID_ALIVE={alive}")
    print(f"DISCONNECTS_5MIN={disc}")
    print(f"REASONS={reason_txt}")
    if meta:
        print(f"QQ_STATE={meta.get('qq_state')}")
        print(f"LAST_STATE_UPDATE={meta.get('updated_at')}")
        if meta.get("qq_error"):
            print(f"QQ_ERROR={meta.get('qq_error')}")
    return 1 if level == "WARN" else 2


if __name__ == "__main__":
    sys.exit(main())
