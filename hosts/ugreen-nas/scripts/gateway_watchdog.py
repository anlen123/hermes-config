#!/usr/bin/env python3
"""
Hermes 网关看门狗
=================
检测 Hermes Gateway（PID 1）是否"假死"，必要时告警并自动重启。

背景：2026-09-25 出现故障 —— QQ WebSocket 心跳保活失效，服务端每 61 秒断连一次，
最终把重连逻辑拖死，进程活着但日志静默 3 小时 14 分（假死）。

检测三层：
  1. gateway_state.json 的 updated_at 是否停滞（主判据）
  2. PID 1 进程是否还活着
  3. errors.log 最近的 WebSocket 断连密度（辅判据）

动作分级：
  - OK         : 正常，静默
  - WARN       : 停滞 >= WARN_MIN 分钟，只告警
  - CRIT       : 停滞 >= CRIT_MIN 分钟 或 进程死亡，告警 + 缓冲后自动重启

告警通道：QQ DM + 日志文件（双通道）
"""

import json
import os
import sys
import time
import subprocess
from datetime import datetime, timezone, timedelta

# ── 配置 ──────────────────────────────────────────────────────────
GATEWAY_STATE = "/opt/data/gateway_state.json"
ERROR_LOG = "/opt/data/logs/errors.log"
WATCHDOG_LOG = "/opt/data/logs/watchdog.log"
STATE_FILE = "/opt/data/logs/.watchdog_state.json"

# 停滞阈值（分钟）
WARN_MIN = 5       # 超过 5 分钟无状态更新 -> 告警
CRIT_MIN = 15      # 超过 15 分钟 -> 告警 + 自动重启

RESTART_GRACE_SEC = 120   # 告警后等待多久再重启（2 分钟缓冲）

QQ_TARGET = "AB581D95695C9AF0A65EA450C3DDB6D3"   # QQ DM 告警目标
CST = timezone(timedelta(hours=8))


def log(msg, level="INFO"):
    ts = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} [{level}] {msg}"
    print(line)
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
    except Exception as e:
        log(f"写入 {path} 失败: {e}", "ERROR")


def pid_alive(pid):
    """检查进程是否存在（且不是僵尸）"""
    try:
        with open(f"/proc/{pid}/stat", "r") as f:
            stat = f.read().split()
        # 第 3 个字段是状态，Z = 僵尸
        if stat[2] == "Z":
            return False
        return True
    except Exception:
        return False


def check_state_stall():
    """返回 (停滞分钟数, 详情dict)。停滞基于 updated_at。"""
    st = load_json(GATEWAY_STATE)
    if not st:
        return None, {"error": "无法读取 gateway_state.json"}

    updated = st.get("updated_at")
    if not updated:
        return None, {"error": "gateway_state.json 缺少 updated_at 字段"}

    try:
        dt = datetime.fromisoformat(updated)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception as e:
        return None, {"error": f"解析 updated_at 失败: {e}"}

    stall = (datetime.now(timezone.utc) - dt).total_seconds() / 60.0

    platforms = st.get("platforms", {})
    qq = platforms.get("qqbot", {})
    return stall, {
        "updated_at": updated,
        "gateway_state": st.get("gateway_state"),
        "qq_state": qq.get("state"),
        "qq_error_code": qq.get("error_code"),
        "qq_error_message": qq.get("error_message"),
    }


def recent_disconnect_count(minutes=5):
    """errors.log 中最近 N 分钟的 WebSocket 断连次数"""
    try:
        cutoff = datetime.now() - timedelta(minutes=minutes)
        count = 0
        with open(ERROR_LOG, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "WebSocket" not in line:
                    continue
                try:
                    ts = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue
                if ts >= cutoff:
                    count += 1
        return count
    except Exception:
        return 0


def send_qq_alert(message):
    """通过 hermes CLI 发 QQ 消息"""
    try:
        env = dict(os.environ)
        env["HERMES_INTERACTIVE"] = "1"
        cmd = ["/opt/hermes/.venv/bin/hermes", "message", "send",
               "--platform", "qqbot", "--target", QQ_TARGET,
               "--text", message]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
        if r.returncode == 0:
            log("QQ 告警已发出")
            return True
        log(f"QQ 告警发送失败 rc={r.returncode}: {r.stderr[:300]}", "ERROR")
        return False
    except Exception as e:
        log(f"QQ 告警异常: {e}", "ERROR")
        return False


def restart_gateway(reason):
    """重启网关：给 PID 1 发 SIGTERM，由 systemd/容器策略拉起"""
    log(f"执行网关重启，原因: {reason}", "CRIT")
    try:
        subprocess.run(["kill", "-TERM", "1"], timeout=10)
        log("已发送 SIGTERM 给 PID 1，等待自动拉起")
        return True
    except Exception as e:
        log(f"重启失败: {e}", "ERROR")
        return False


def main():
    now_ts = time.time()
    prev = load_json(STATE_FILE, {}) or {}

    stall, detail = check_state_stall()
    alive = pid_alive(1)
    disc = recent_disconnect_count(5)

    # ── 判定 ──
    level = "OK"
    reasons = []

    if not alive:
        level = "CRIT"
        reasons.append("网关进程（PID 1）不存在或已是僵尸")
    elif stall is None:
        level = "WARN"
        reasons.append(f"状态文件异常: {detail.get('error')}")
    elif stall >= CRIT_MIN:
        level = "CRIT"
        reasons.append(f"状态停滞 {stall:.1f} 分钟（阈值 {CRIT_MIN}）")
    elif stall >= WARN_MIN:
        level = "WARN"
        reasons.append(f"状态停滞 {stall:.1f} 分钟（阈值 {WARN_MIN}）")

    if disc >= 10:
        if level == "OK":
            level = "WARN"
        reasons.append(f"最近 5 分钟 WebSocket 断连 {disc} 次（异常频繁）")

    # ── 记录状态快照 ──
    snapshot = {
        "last_check": datetime.now(CST).isoformat(),
        "level": level,
        "stall_min": round(stall, 2) if stall is not None else None,
        "pid_alive": alive,
        "disconnects_5min": disc,
        "reasons": reasons,
    }

    if level == "OK":
        log(f"正常 | 停滞={stall:.1f}min 进程={'活' if alive else '死'} 断连={disc}/5min")
        save_json(STATE_FILE, snapshot)
        return 0

    # ── 非 OK，需要处理 ──
    reason_txt = "；".join(reasons)
    log(f"异常 [{level}] {reason_txt}", "WARN" if level == "WARN" else "CRIT")

    # 去抖：同一 level 在 10 分钟内不重复告警
    last_alert = prev.get("last_alert_ts", 0)
    last_level = prev.get("last_alert_level")
    debounce = (level == last_level) and (now_ts - last_alert < 600)

    if not debounce:
        icon = "⚠️" if level == "WARN" else "🚨"
        msg = (
            f"{icon} Hermes 网关看门狗告警\n"
            f"级别: {level}\n"
            f"时间: {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"原因: {reason_txt}\n"
            f"状态停滞: {stall:.1f} 分钟\n" if stall is not None else ""
        )
        if level == "CRIT" and alive:
            msg += f"\n将在 {RESTART_GRACE_SEC // 60} 分钟后自动重启网关。"
            msg += "\n如需阻止，请在看门狗重启前介入。"
        if not alive:
            msg += "\n网关进程已不在，等待自动拉起。"

        send_qq_alert(msg)
        snapshot["last_alert_ts"] = now_ts
        snapshot["last_alert_level"] = level

    # ── CRIT: 缓冲后自动重启 ──
    if level == "CRIT" and alive:
        pending_since = prev.get("crit_since")
        if not pending_since:
            # 第一次进入 CRIT，记录时间，等待缓冲
            snapshot["crit_since"] = now_ts
            log(f"进入 CRIT，将在 {RESTART_GRACE_SEC}s 后重启（缓冲期开始）")
        else:
            waited = now_ts - pending_since
            if waited >= RESTART_GRACE_SEC:
                restart_gateway(reason_txt)
                snapshot.pop("crit_since", None)
                snapshot["last_restart_ts"] = now_ts
            else:
                log(f"缓冲期进行中：已等 {waited:.0f}s / {RESTART_GRACE_SEC}s")
                snapshot["crit_since"] = pending_since
    else:
        snapshot.pop("crit_since", None)

    save_json(STATE_FILE, snapshot)
    return 1 if level == "WARN" else 2


if __name__ == "__main__":
    sys.exit(main())
