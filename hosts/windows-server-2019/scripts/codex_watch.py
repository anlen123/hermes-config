#!/usr/bin/env python3
"""Codex 重置卡 / 额度监视器（配合 Hermes cron 的 no_agent 模式使用）。

数据来源（Codex 官方 CLI 自己在用的后端接口）：
  GET {BASE}/wham/usage                     → 额度窗口（primary=5小时, secondary=每周）+ 重置卡摘要
  GET {BASE}/wham/rate-limit-reset-credits  → 重置卡清单（credits[] + available_count）
凭据取自 ~/.codex/auth.json（codex login 写入的 ChatGPT OAuth）。
token 快过期时自动刷新，并把新 token 回写 auth.json —— 与 codex CLI 保持一致，
否则 CLI 会因为 refresh_token 被轮换而失效。

行为：命中下面任一条件才把中文通知打到 stdout（cron 原样投递到 QQ）；
      没命中就什么都不输出 = 不打扰。

条件 1：可用重置卡数量 > 上次
条件 2：被追踪窗口的「剩余额度」回升 >= MIN_UP_PP 个百分点

窗口按 limit_window_seconds 自动识别（实测 plus 账号的「每周额度」落在 primary 槽，
secondary 为 null），所以不按 primary/secondary 名字硬编码。

用法：
  python codex_watch.py                 # 巡检（静默，除非命中条件）
  python codex_watch.py --show          # 只打印当前快照，不写状态（排查用）
  python codex_watch.py --reset-state   # 清空基线，下次巡检重新记基线且不通知
"""
import argparse
import base64
import datetime
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

HOME = pathlib.Path(os.path.expanduser("~"))
CODEX_HOME = pathlib.Path(os.environ.get("CODEX_HOME") or (HOME / ".codex"))
AUTH_FILE = CODEX_HOME / "auth.json"
WATCH_DIR = pathlib.Path(
    os.environ.get("CODEX_WATCH_DIR") or (HOME / "AppData/Local/hermes/codex-watch")
)
STATE_FILE = WATCH_DIR / "state.json"
LOG_FILE = WATCH_DIR / "watch.log"

BASE = os.environ.get("CODEX_BACKEND_BASE", "https://chatgpt.com/backend-api")
TOKEN_URL = os.environ.get(
    "CODEX_REFRESH_TOKEN_URL_OVERRIDE", "https://auth.openai.com/oauth/token"
)
CLIENT_ID = os.environ.get(
    "CODEX_APP_SERVER_LOGIN_CLIENT_ID", "app_EMoamEEZ73f0CkXaXp7hrann"
)

# 追踪哪个窗口：secondary=每周额度（默认），primary=5小时窗口，both=两个都追踪
TRACK_WINDOW = os.environ.get("CODEX_WATCH_WINDOW", "auto").strip().lower()
MIN_UP_PP = float(os.environ.get("CODEX_WATCH_MIN_UP_PP", "1"))
HTTP_TIMEOUT = 30
REFRESH_MARGIN_S = 600  # 距过期不足 10 分钟就刷新

def now():
    return datetime.datetime.now().astimezone()


def now_iso():
    return now().isoformat(timespec="seconds")


def log(msg):
    try:
        WATCH_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"{now_iso()} {msg}\n")
    except Exception:
        pass


def out(text):
    """通知内容走 stdout —— cron 会原样投递。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(text)


# ---------------------------------------------------------------- auth


def jwt_payload(token):
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


def token_exp(token):
    return float(jwt_payload(token).get("exp") or 0)


def load_auth():
    if not AUTH_FILE.exists():
        raise SystemExit(f"找不到 {AUTH_FILE} —— 先用 codex login 登录")
    return json.loads(AUTH_FILE.read_text(encoding="utf-8"))


def http(url, token=None, account_id=None, method="GET", payload=None, headers=None):
    hdrs = {"User-Agent": "codex-cli", "Accept": "application/json"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    if account_id:
        hdrs["ChatGPT-Account-Id"] = account_id
    if headers:
        hdrs.update(headers)
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def refresh_tokens(auth):
    """用 refresh_token 换新 token，并回写 auth.json（保持与 codex CLI 同步）。"""
    tokens = auth.get("tokens") or {}
    rt = tokens.get("refresh_token")
    if not rt:
        raise RuntimeError("auth.json 里没有 refresh_token，需要重新 codex login")
    resp = http(
        TOKEN_URL,
        method="POST",
        payload={
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": rt,
        },
    )
    for key in ("access_token", "id_token", "refresh_token"):
        if resp.get(key):
            tokens[key] = resp[key]
    auth["tokens"] = tokens
    auth["last_refresh"] = now().astimezone(datetime.timezone.utc).isoformat()
    tmp = AUTH_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(auth, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, AUTH_FILE)
    log("token 已刷新并回写 auth.json")
    return auth


def access_token(auth, force_refresh=False):
    tokens = auth.get("tokens") or {}
    tok = tokens.get("access_token") or ""
    exp = token_exp(tok)
    if force_refresh or not tok or (exp and exp - time.time() < REFRESH_MARGIN_S):
        auth = refresh_tokens(auth)
        tok = (auth.get("tokens") or {}).get("access_token") or ""
    return tok, (auth.get("tokens") or {}).get("account_id")


# ---------------------------------------------------------------- 数据


def window_label(seconds):
    if not seconds:
        return "额度窗口"
    if seconds >= 86400:
        days = seconds / 86400
        return "每周额度" if abs(days - 7) < 0.5 else f"{days:.0f} 天额度"
    return f"{seconds / 3600:.0f} 小时窗口"


def window_of(details, slot):
    win = (details or {}).get(slot)
    if not win or win.get("used_percent") is None:
        return None
    resets_at = win.get("reset_at")  # unix 秒
    reset_after = win.get("reset_after_seconds")
    reset_ts = None
    if isinstance(resets_at, (int, float)) and resets_at > 1_000_000_000:
        reset_ts = resets_at
    elif isinstance(reset_after, (int, float)):
        reset_ts = time.time() + reset_after
    used = float(win["used_percent"])
    seconds = win.get("limit_window_seconds")
    return {
        "slot": slot.replace("_window", ""),
        "label": window_label(seconds),
        "used_percent": used,
        "remaining_percent": round(100.0 - used, 1),
        "limit_window_seconds": seconds,
        "reset_at": int(reset_ts) if reset_ts else None,
    }


def collect_windows(rate_limit):
    found = []
    for slot in ("primary_window", "secondary_window"):
        win = window_of(rate_limit, slot)
        if win:
            found.append(win)
    found.sort(key=lambda w: w.get("limit_window_seconds") or 0, reverse=True)
    return {w["slot"]: w for w in found}


def tracked(windows):
    """要盯的窗口：默认盯最长的那个（通常是每周额度）。"""
    if not windows:
        return []
    if TRACK_WINDOW == "both":
        return list(windows)
    if TRACK_WINDOW in ("primary", "secondary"):
        return [s for s in (TRACK_WINDOW,) if s in windows] or list(windows)
    return [max(windows, key=lambda s: (windows[s].get("limit_window_seconds") or 0))]


def fetch_snapshot(auth):
    tok, account_id = access_token(auth)
    usage = http(f"{BASE}/wham/usage", token=tok, account_id=account_id)
    if usage.get("account_id") and not account_id:
        account_id = usage["account_id"]
    credits = None
    try:
        credits = http(
            f"{BASE}/wham/rate-limit-reset-credits", token=tok, account_id=account_id
        )
    except urllib.error.HTTPError as exc:
        log(f"重置卡接口失败: HTTP {exc.code}")
    rl = usage.get("rate_limit") or {}
    return {
        "at": now_iso(),
        "plan_type": usage.get("plan_type"),
        "windows": collect_windows(rl),
        "limit_reached": rl.get("limit_reached"),
        "credits_available": (credits or {}).get(
            "available_count",
            ((usage.get("rate_limit_reset_credits") or {}).get("available_count")),
        ),
        "credits": (credits or {}).get("credits") or [],
    }


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            log(f"状态文件损坏，重新记基线: {exc}")
    return {}


def save_state(state):
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = now_iso()
    hist = state.get("history") or []
    state["history"] = hist[-40:]
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8"
    )


# ---------------------------------------------------------------- 通知文案


def fmt_clock(ts):
    if not ts:
        return "?"
    return datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def quota_line(win):
    if not win:
        return None
    text = (
        f"{win['label']}：已用 {win['used_percent']:.0f}%"
        f"（剩 {win['remaining_percent']:.0f}%）"
    )
    if win.get("reset_at"):
        text += f"，{fmt_clock(win['reset_at'])} 重置"
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="只打印当前快照，不写状态")
    ap.add_argument("--reset-state", action="store_true", help="清空基线")
    args = ap.parse_args()

    if args.reset_state:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        out("已清空 Codex 监视基线，下次巡检重新记基线（不会通知）。")
        return 0

    try:
        auth = load_auth()
        snap = fetch_snapshot(auth)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        if exc.code in (401, 403):
            try:
                auth = refresh_tokens(load_auth())
                snap = fetch_snapshot(auth)
            except Exception as exc2:
                log(f"认证失败: HTTP {exc.code} {detail} / 刷新后仍失败: {exc2}")
                out(
                    "⚠️ Codex 监视器：登录态失效，拿不到数据。\n"
                    "请重新登录：`codex login --device-auth`"
                )
                return 0
        else:
            log(f"接口失败: HTTP {exc.code} {detail}")
            return 0
    except Exception as exc:
        log(f"巡检异常: {type(exc).__name__}: {exc}")
        return 0

    if args.show:
        out(
            json.dumps(
                {
                    "at": snap["at"],
                    "plan_type": snap["plan_type"],
                    "limit_reached": snap["limit_reached"],
                    "credits_available": snap["credits_available"],
                    "windows": snap["windows"],
                    "追踪": tracked(snap["windows"]),
                },
                ensure_ascii=False,
                indent=1,
            )
        )
        out(f"重置卡明细（{len(snap['credits'])} 张）：")
        for c in snap["credits"]:
            out(
                f"  - {c.get('title') or c.get('reset_type')}｜status={c.get('status')}"
                f"｜发放 {str(c.get('granted_at'))[:10]}｜过期 {str(c.get('expires_at'))[:10]}｜id={c.get('id')}"
            )
        return 0

    prev = load_state()
    first_run = not prev
    msgs = []

    # 条件 1：重置卡变多
    prev_credits = prev.get("credits_available")
    if not first_run and prev_credits is not None and snap["credits_available"] is not None:
        delta = snap["credits_available"] - prev_credits
        if delta > 0:
            msgs.append(f"🔔 Codex 重置卡 +{delta}（现有 {snap['credits_available']} 张）")
            known = {c.get("id") for c in (prev.get("credits") or [])}
            for c in snap["credits"]:
                if c.get("id") not in known:
                    line = f"🏷 {c.get('title') or c.get('reset_type') or '重置卡'}"
                    if c.get("reset_type"):
                        line += f"（{c['reset_type']}）"
                    if c.get("granted_at"):
                        line += f"｜发放 {str(c['granted_at'])[:10]}"
                    if c.get("expires_at"):
                        line += f"｜{str(c['expires_at'])[:10]} 前有效"
                    msgs.append(line)

    # 条件 2：额度回升
    prev_windows = prev.get("windows") or {}
    for slot in tracked(snap["windows"]):
        cur = snap["windows"][slot]
        old = prev_windows.get(slot) if not first_run else None
        if not old:
            continue
        up = round(cur["remaining_percent"] - float(old.get("remaining_percent", 0)), 1)
        if up < MIN_UP_PP:
            continue
        # 上次看到的窗口结束时间已经过去 = 时间到了自然滚动；
        # 窗口还没到期额度就回来 = 有人动过额度（官方调整 / 用了重置卡）
        natural = bool(old.get("reset_at") and old["reset_at"] <= time.time())
        reason = "窗口自然滚动" if natural else "官方/重置卡重置"
        msgs.append(
            f"📈 Codex 额度回升 +{up:.0f} 个百分点（{reason}）\n"
            f"　{cur['label']}：已用 {cur['used_percent']:.0f}%"
            f"（剩 {cur['remaining_percent']:.0f}%，之前剩 {float(old.get('remaining_percent', 0)):.0f}%）"
        )

    if msgs:
        lines = msgs + ["———"]
        for slot in tracked(snap["windows"]):
            line = quota_line(snap["windows"].get(slot))
            if line:
                lines.append("📊 " + line)
        if snap.get("plan_type"):
            lines.append(
                f"　套餐：{snap['plan_type']}｜重置卡：{snap['credits_available']} 张"
            )
        out("\n".join(lines))
        log("已通知: " + " / ".join(m.splitlines()[0] for m in msgs))
    else:
        brief = " ".join(
            f"{w['label']}={w['used_percent']:.0f}%" for w in snap["windows"].values()
        )
        log(f"无变化 卡={snap['credits_available']} {brief}")

    save_state(
        {
            "at": snap["at"],
            "plan_type": snap.get("plan_type"),
            "credits_available": snap["credits_available"],
            "credits": snap["credits"],
            "windows": snap["windows"],
            "limit_reached": snap.get("limit_reached"),
            "history": (prev.get("history") or [])
            + [
                {
                    "at": snap["at"],
                    "credits": snap["credits_available"],
                    "windows": {
                        k: v.get("used_percent") for k, v in snap["windows"].items()
                    },
                }
            ],
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
