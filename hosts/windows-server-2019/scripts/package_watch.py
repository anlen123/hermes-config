#!/usr/bin/env python3
"""快递物流监视器（配合 Hermes cron 的 no_agent 模式使用）。

数据源：快递100 的免 key 查询接口
  GET https://www.kuaidi100.com/query?type=<承运商>&postid=<单号>&temp=<随机>&phone=
  （需要 Referer: https://www.kuaidi100.com/；返回 data[] 按时间倒序）
注意：它的「单号自动识别」接口 autoComNum 对本机返回 `非法IP`（出口在境外），
所以这里改成**按单号形态排序后逐个承运商试**，命中真实轨迹的那个即为其承运商，
并把结果缓存到 tracked.json，之后只查这一家。

数据源：默认**只用快递鸟**（PACKAGE_SOURCE=kdniao）。快递100 相关通道保留在代码里但默认不启用。
查询频率（自适应，省额度）：在途/已揽收 240 分钟一次；派件中 60 分钟一次；已签收 720 分钟一次；
夜间 23:00-07:00 不查（派件中除外）。定时任务仍是每小时 tick，由脚本自己决定要不要真查。

用法：
  python package_watch.py                       # 巡检（静默，除非有新动态）
  python package_watch.py --add <单号> [--carrier <代码>] [--label <备注>]
  python package_watch.py --remove <单号>
  python package_watch.py --list
  python package_watch.py --check [单号]        # 立刻打当前状态（不写状态，排查用）
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
import pathlib
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HOME = pathlib.Path(os.path.expanduser("~"))
DATA_DIR = pathlib.Path(
    os.environ.get("PACKAGE_WATCH_DIR") or (HOME / "AppData/Local/hermes/package-tracking")
)
TRACKED_FILE = DATA_DIR / "tracked.json"
LOG_FILE = DATA_DIR / "watch.log"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)
# 免 key 的老接口：能出数据但会被限流、且 type 与真实承运商不符时会返回别家轨迹（只作兜底）
QUERY_URL = "https://www.kuaidi100.com/query"
REFERER = "https://www.kuaidi100.com/"
# 正规接口：快递100 开放平台 / 快递鸟（凭证从 ~/AppData/Local/hermes/.env 或环境变量读）
KUAIDI100_POLL_URL = "https://poll.kuaidi100.com/poll/query.do"
KUAIDI100_AUTO_URL = "https://api.kuaidi100.com/autonumber/auto"
KDNIAO_URL = "https://api.kdniao.com/Ebusiness/EbusinessOrderHandle.aspx"
HERMES_ENV = HOME / "AppData/Local/hermes/.env"
# 快递鸟的承运商代码（与快递100 不同）
KDNIAO_CODE = {
    "shunfeng": "SF",
    "jd": "JD",
    "yuantong": "YTO",
    "jtexpress": "JT",
    "zhongtong": "ZTO",
    "shentong": "STO",
    "yunda": "YD",
    "ems": "EMS",
    "debangwuliu": "DBL",
    "youzhengguonei": "YZPY",
    "huitongkuaidi": "HTKY",
    "tiantian": "HHTT",
    "youshuwuliu": "UC",
}


def load_env():
    """把 Hermes 的 .env 读进环境变量（不覆盖已存在的）。"""
    if not HERMES_ENV.exists():
        return
    try:
        for line in HERMES_ENV.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception as exc:
        log(f"读 .env 失败: {exc}")


def has_kuaidi100_pro():
    return bool(os.environ.get("KUAIDI100_CUSTOMER") and os.environ.get("KUAIDI100_KEY"))


def has_kdniao():
    return bool(os.environ.get("KDNIAO_EBUSINESS_ID") and os.environ.get("KDNIAO_API_KEY"))


def query_kuaidi100_pro(carrier, num, phone=""):
    """快递100 开放平台·实时查询（POST 表单，sign=MD5(param+key+customer) 大写）。"""
    cust = os.environ.get("KUAIDI100_CUSTOMER", "")
    key = os.environ.get("KUAIDI100_KEY", "")
    param = json.dumps(
        {"com": carrier, "num": num, "phone": phone or ""}, ensure_ascii=False, separators=(",", ":")
    )
    sign = hashlib.md5((param + key + cust).encode("utf-8")).hexdigest().upper()
    body = urllib.parse.urlencode({"customer": cust, "sign": sign, "param": param}).encode()
    req = urllib.request.Request(
        KUAIDI100_POLL_URL,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "User-Agent": UA,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        return {"__error__": f"{type(exc).__name__}: {exc}"}
    payload["__source__"] = "kuaidi100-pro"
    if payload.get("returnCode") and payload.get("returnCode") != "200":
        payload["__error__"] = f"{payload.get('returnCode')} {payload.get('message')}"
    return payload


def detect_kuaidi100_pro(num):
    """快递100 单号识别（只需 key）。返回承运商代码或 None。"""
    key = os.environ.get("KUAIDI100_KEY", "")
    url = "%s?%s" % (KUAIDI100_AUTO_URL, urllib.parse.urlencode({"num": num, "key": key}))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        log(f"单号识别失败: {type(exc).__name__}: {exc}")
        return None
    for item in data.get("auto") or []:
        if item.get("comCode"):
            return item["comCode"]
    return None


def query_kdniao(carrier, num, phone=""):
    """快递鸟·**免费**即时查询（在途监控，RequestType=8001）。

    实测：1002（标准即时查询）在他账号上报「没有可用套餐」＝需付费；
    8001 可用且免费，但要求 RequestData 里带 CustomerName（收件人手机后四位），
    中通/顺丰等隐私面单必需。
    """
    eid = os.environ.get("KDNIAO_EBUSINESS_ID", "")
    appkey = os.environ.get("KDNIAO_API_KEY", "")
    shipper = KDNIAO_CODE.get(carrier, carrier)
    # 签名必须对「未 urlencode 的原始 JSON」求 MD5，再 base64；编码只在上送时做
    payload = {"ShipperCode": shipper, "LogisticCode": num}
    if phone and len(phone) >= 4:
        # 快递鸟这个字段名就是 CustomerName，放手机后四位（不是 CustomerPwd）
        payload["CustomerName"] = phone[-4:]
    raw_request = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # 快递鸟要求：DataSign = Base64( MD5(原始JSON + AppKey) 的【32位十六进制小写字符串】 )
    # 注意是「先取 hex 字符串再 base64」，不是 base64(原始 digest 字节)，也不是大写 hex。
    md5_hex = hashlib.md5((raw_request + appkey).encode("utf-8")).hexdigest()
    sign = base64.b64encode(md5_hex.encode("utf-8")).decode()
    body = urllib.parse.urlencode(
        {
            "EBusinessID": eid,
            "RequestType": "8001",
            "RequestData": raw_request,
            "DataSign": sign,  # urlencode 会负责编码；预先 quote 会二次编码
            "DataType": "2",
        }
    ).encode()
    req = urllib.request.Request(
        KDNIAO_URL,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "User-Agent": UA,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        return {"__error__": f"{type(exc).__name__}: {exc}"}
    # 归一成与快递100 相同的结构
    events = []
    for tr in raw.get("Traces") or []:
        ctx = (tr.get("AcceptStation") or "").strip()
        if ctx:
            events.append({"time": (tr.get("AcceptTime") or "").strip(), "context": ctx})
    events.reverse()  # 快递鸟是正序
    payload = {
        "message": "ok" if raw.get("Success") else (raw.get("Reason") or "err"),
        "state": "3" if raw.get("State") == "3" else str(raw.get("State") or ""),
        "data": events,
        "__source__": "kdniao",
    }
    if not raw.get("Success"):
        payload["__error__"] = raw.get("Reason") or "查询失败"
    return payload
MAX_NEW_LINES = 4  # 每条通知最多列几行轨迹

# 数据源：kdniao（默认，只用快递鸟）/ kuaidi100 / free（免 key 兜底，实测不可靠）
SOURCE = os.environ.get("PACKAGE_SOURCE", "kdniao").strip().lower()
# 自适应查询间隔（分钟）：省接口额度
INTERVAL_OUT_FOR_DELIVERY = int(os.environ.get("PACKAGE_INTERVAL_DELIVERING", "180"))
INTERVAL_DEFAULT = int(os.environ.get("PACKAGE_INTERVAL_DEFAULT", "180"))
INTERVAL_DONE = int(os.environ.get("PACKAGE_INTERVAL_DONE", "720"))
# 安静时段：这段时间一次都不查（用户拍板 03:00-09:00）
QUIET_START_HOUR = int(os.environ.get("PACKAGE_QUIET_START_HOUR", "3"))
QUIET_END_HOUR = int(os.environ.get("PACKAGE_QUIET_END_HOUR", "9"))
PROBE_LIMIT = 10  # 单个单号最多试几家承运商

# 承运商代码 / 中文名 / 单号形态（用于排序，先试最像的）
CARRIERS = [
    ("shunfeng", "顺丰速运", r"^SF\d{12,}$"),
    ("jd", "京东物流", r"^JD[A-Z]{0,3}\d{6,}$"),
    ("yuantong", "圆通速递", r"^YT\d{10,}$"),
    ("jtexpress", "极兔速递", r"^JT\d{10,}$"),
    ("zhongtong", "中通快递", r"^(75|78|68|73|76)\d{10,}$"),
    ("shentong", "申通快递", r"^(77|88|66|55|56)\d{10,}$"),
    ("yunda", "韵达速递", r"^(3\d{12}|4\d{12}|5\d{12})$"),
    ("ems", "EMS/邮政", r"^([A-Z]{2}\d{9}[A-Z]{2}|1\d{12})$"),
    ("debangwuliu", "德邦快递", r"^(DPK|5\d{11})"),
    ("youzhengguonei", "邮政快递包裹", r""),
    ("huitongkuaidi", "百世快递", r""),
    ("tiantian", "天天快递", r""),
    ("youshuwuliu", "优速快递", r""),
    ("jd", "京东物流", r""),
]
CARRIER_LABEL = {}
for _code, _label, _ in CARRIERS:
    CARRIER_LABEL.setdefault(_code, _label)

STATE_TEXT = {
    "0": "在途",
    "1": "已揽收",
    "2": "疑难件",
    "3": "已签收",
    "4": "已退签",
    "5": "派件中",
    "6": "退回中",
    "7": "转投",
    "8": "清关",
    "14": "拒签",
}


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def log(msg):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"{now_iso()} {msg}\n")
    except Exception:
        pass


def out(text):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(text)


# ---------------------------------------------------------------- 存取


def load():
    data = {"packages": []}
    if TRACKED_FILE.exists():
        try:
            data = json.loads(TRACKED_FILE.read_text(encoding="utf-8"))
            data.setdefault("packages", [])
        except Exception as exc:
            log(f"tracked.json 损坏，重建: {exc}")
    # 编号（no）在加入时分配、永不重排：删掉 #1 后 #2 仍是 #2，用户记的号不会串
    used = [p["no"] for p in data["packages"] if isinstance(p.get("no"), int)]
    nxt = max(used) + 1 if used else 1
    for p in data["packages"]:
        if not isinstance(p.get("no"), int):
            p["no"] = nxt
            nxt += 1
    return data


def by_no_or_id(data, key):
    """按编号（不重排的 no）或完整单号定位。"""
    key = str(key).strip()
    if key.isdigit():
        for p in data["packages"]:
            if str(p.get("no")) == key:
                return p
    return find(data, key)


def save(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = now_iso()
    tmp = TRACKED_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, TRACKED_FILE)


def find(data, num):
    num = num.strip().upper()
    for p in data["packages"]:
        if p["id"].upper() == num:
            return p
    return None


# ---------------------------------------------------------------- 查询


def query(carrier, num, phone=""):
    """数据源由 PACKAGE_SOURCE 决定；默认只用快递鸟，查不到就如实失败，不偷换渠道。"""
    if SOURCE == "kdniao":
        if not has_kdniao():
            log("未配置 KDNIAO_EBUSINESS_ID/KDNIAO_API_KEY，本次不查询")
            return {"__error__": "快递鸟凭证未配置"}
        payload = query_kdniao(carrier, num, phone)
        if payload.get("__error__"):
            log(f"快递鸟查询失败: {payload['__error__'][:140]}")
        return payload
    if SOURCE == "kuaidi100":
        if not has_kuaidi100_pro():
            return {"__error__": "快递100 凭证未配置"}
        payload = query_kuaidi100_pro(carrier, num, phone)
        if payload.get("__error__"):
            log(f"快递100 开放平台查询失败: {payload['__error__'][:140]}")
        return payload
    # free：免 key 兜底（会限流、会串数据，仅手动排查用）
    url = "%s?%s" % (
        QUERY_URL,
        urllib.parse.urlencode(
            {"type": carrier, "postid": num, "temp": f"{random.random():.6f}", "phone": phone}
        ),
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REFERER})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
            payload["__source__"] = "kuaidi100-free"
            return payload
    except Exception as exc:
        return {"__error__": f"{type(exc).__name__}: {exc}"}


def query_interval_minutes(entry):
    """按当前状态决定这次该不该查（省额度）。"""
    state = str(entry.get("state") or "")
    if state == "5":  # 派件中
        return INTERVAL_OUT_FOR_DELIVERY
    if state in ("3", "4", "14"):  # 已签收/退签/拒签
        return INTERVAL_DONE
    return INTERVAL_DEFAULT


def in_quiet_hours(dt=None):
    """安静时段内不查询。支持跨零点与不跨零点两种写法。"""
    hour = (dt or datetime.datetime.now()).hour
    if QUIET_START_HOUR <= QUIET_END_HOUR:
        return QUIET_START_HOUR <= hour < QUIET_END_HOUR
    return hour >= QUIET_START_HOUR or hour < QUIET_END_HOUR


def due_reason(entry, force=False):
    """返回 None 表示该查；返回字符串表示跳过原因。"""
    if force:
        return None
    last = entry.get("last_query_at")
    if last:
        try:
            elapsed = (
                datetime.datetime.now().astimezone() - datetime.datetime.fromisoformat(last)
            ).total_seconds() / 60
        except Exception:
            elapsed = 1e9
        need = query_interval_minutes(entry)
        if elapsed < need:
            return f"距上次 {elapsed:.0f} 分钟 < {need} 分钟"
    if in_quiet_hours():
        return f"安静时段({QUIET_START_HOUR:02d}:00-{QUIET_END_HOUR:02d}:00)不查"
    return None


def events_of(payload):
    """返回 [(time, context)]，按时间倒序（接口本身就是倒序）。"""
    rows = []
    for item in payload.get("data") or []:
        ctx = (item.get("context") or "").strip()
        if not ctx or ctx == "查无结果":
            continue
        rows.append((item.get("time") or item.get("ftime") or "", ctx))
    return rows


def carrier_candidates(num, prefer=None):
    num_u = num.upper()
    matched, rest = [], []
    for code, _label, pattern in CARRIERS:
        if prefer and code == prefer:
            matched.insert(0, code)
        elif pattern and re.match(pattern, num_u, re.I):
            matched.append(code)
        else:
            rest.append(code)
    seen, ordered = set(), []
    for code in matched + rest:
        if code not in seen:
            seen.add(code)
            ordered.append(code)
    return ordered[:PROBE_LIMIT]


def resolve(num, prefer=None, phone=""):
    """找出能返回真实轨迹的承运商：(code, payload) 或 (None, 最后响应)。"""
    last = {}
    if prefer:
        # 调用方/用户指定的承运商是权威：只查它，查不到就如实报失败，绝不偷偷换成别家
        for attempt in range(3):
            payload = query(prefer, num, phone)
            if not payload.get("__error__") and events_of(payload):
                return prefer, payload
            last = payload
            if attempt < 2:
                time.sleep(1.5)
        return None, last
    if has_kuaidi100_pro():
        code = detect_kuaidi100_pro(num)
        if code:
            payload = query(code, num, phone)
            if not payload.get("__error__") and events_of(payload):
                return code, payload
            last = payload
    for code in carrier_candidates(num, prefer):
        payload = query(code, num, phone)
        if "__error__" in payload:
            last = payload
            continue
        last = payload
        if payload.get("message") == "ok" and events_of(payload):
            return code, payload
        time.sleep(0.2)
    return None, last


# ---------------------------------------------------------------- 命令


def cmd_add(args):
    data = load()
    num = args.add.strip().upper()
    existing = find(data, num)
    if existing and not args.carrier:
        out(f"ℹ️ 这个单号已经在盯了：{num}（{existing.get('carrier_label') or existing['carrier']}）")
        return 0
    code, payload = resolve(num, args.carrier, args.phone or "")
    if not code:
        msg = (payload or {}).get("message") or (payload or {}).get("__error__") or "未知原因"
        out(
            f"⚠️ 没能查到 {num} 的物流：{msg}\n"
            "可能原因：单号有误、刚发货还没揽收、或需要承运商代码/手机后四位（顺丰）。\n"
            "可以换一种说法重试：`快递 <承运商> <单号>`。"
        )
        return 0
    events = events_of(payload)
    entry = existing or {"id": num, "added_at": now_iso()}
    if not isinstance(entry.get("no"), int):
        entry["no"] = max([p["no"] for p in data["packages"] if isinstance(p.get("no"), int)] or [0]) + 1
    entry["locked"] = bool(args.carrier)
    if args.phone:
        entry["phone"] = args.phone
    entry["last_query_at"] = now_iso()
    entry.update(
        {
            "carrier": code,
            "carrier_label": CARRIER_LABEL.get(code, code),
            "label": args.label or entry.get("label") or "",
            "last_time": events[0][0],
            "last_context": events[0][1],
            "state": str(payload.get("state", "")),
            "signed_notified": False,
            "fails": 0,
        }
    )
    if not existing:
        data["packages"].append(entry)
    save(data)
    out(
        f"✅ 已在盯 #{entry['no']}：{entry['id']}｜{entry['carrier_label']}"
        f"｜{STATE_TEXT.get(entry['state'], entry['state'])}\n"
        f"　最新：{events[0][0]} {events[0][1]}\n"
        f"　每小时自动查一次，有新动态才通知你；取件后回我「快递已取 {entry['no']}」即可停止。"
    )
    return 0


def cmd_remove(args):
    data = load()
    entry = by_no_or_id(data, args.remove)
    if not entry:
        listing = "、".join(str(p.get("no")) for p in data["packages"]) or "无"
        out(f"ℹ️ 没找到「{args.remove.strip()}」对应的快递（当前编号：{listing}）。")
        return 0
    data["packages"] = [p for p in data["packages"] if p is not entry]
    save(data)
    lines = [
        f"✅ 已停止跟踪 #{entry.get('no')} {entry['id']}"
        f"（{entry.get('carrier_label') or entry['carrier']}"
        + (f"｜{entry['label']}" if entry.get("label") else "")
        + "），后面不会再发它的消息。"
    ]
    if data["packages"]:
        lines.append("还在盯：")
        for p in data["packages"]:
            lines.append(
                f"{p.get('no')}. {p['id']}｜{p.get('carrier_label') or p['carrier']}"
                f"｜{STATE_TEXT.get(str(p.get('state')), '?')}"
            )
    else:
        lines.append("现在没有在盯的快递了。")
    out("\n".join(lines))
    return 0


def cmd_list(_args):
    data = load()
    if not data["packages"]:
        out("当前没有在盯的快递。")
        return 0
    lines = [f"📦 在盯 {len(data['packages'])} 个快递："]
    for p in data["packages"]:
        lines.append(
            f"{p.get('no')}. {p['id']}｜{p.get('carrier_label') or p['carrier']}"
            f"｜{STATE_TEXT.get(str(p.get('state')), '') or '?'}"
            f"｜最新 {p.get('last_time') or '?'} {p.get('last_context') or ''}"
            + (f"｜备注 {p['label']}" if p.get("label") else "")
        )
    out("\n".join(lines))
    return 0


def cmd_check(args):
    data = load()
    targets = (
        [find(data, args.check)]
        if args.check
        else data["packages"]
    )
    if not targets or any(t is None for t in targets):
        out("找不到该单号，或当前没有在盯的快递。")
        return 0
    blocks = []
    for p in targets:
        code = p.get("carrier") or ""
        payload = query(code, p["id"], p.get("phone") or "") if code else {}
        events = events_of(payload)
        if not events and not code:
            code, payload = resolve(p["id"])
            events = events_of(payload)
        head = f"{p['id']}｜{CARRIER_LABEL.get(code, code)}｜{STATE_TEXT.get(str(payload.get('state')), payload.get('state'))}"
        body = "\n".join(f"· {t} {c}" for t, c in events[:6]) or "（暂无轨迹）"
        blocks.append(head + "\n" + body)
    out("\n\n".join(blocks))
    return 0


# ---------------------------------------------------------------- 巡检


def render(entry, new_events, signed_now):
    code = entry.get("carrier", "")
    label = entry.get("label") or entry["id"]
    head = (
        f"📦 #{entry.get('no')} {label}"
        f"｜{entry.get('carrier_label') or CARRIER_LABEL.get(code, code)}"
        f"｜{STATE_TEXT.get(str(entry.get('state')), entry.get('state'))}"
    )
    lines = [head]
    for t, c in new_events[:MAX_NEW_LINES]:
        lines.append(f"· {t} {c}")
    if len(new_events) > MAX_NEW_LINES:
        lines.append(f"· …另有 {len(new_events) - MAX_NEW_LINES} 条更早动态")
    if signed_now:
        lines.append(f"　已签收。取件后回我「快递已取 {entry.get('no')}」就不再提醒。")
    return "\n".join(lines)


def run_check(_args):
    data = load()
    if not data["packages"]:
        log("无在盯单号")
        return 0
    blocks = []
    changed = False
    for entry in data["packages"]:
        reason = due_reason(entry)
        if reason:
            log(f"跳过 #{entry.get('no')} {entry['id']}: {reason}")
            continue
        entry["last_query_at"] = now_iso()
        code = entry.get("carrier") or ""
        payload = query(code, entry["id"], entry.get("phone") or "") if code else {}
        events = events_of(payload)
        if not events:
            if "__error__" in payload:
                err = str(payload["__error__"])
                if "未配置" in err:
                    log(f"{entry['id']} 未查询（{err}）")
                    continue
                entry["fails"] = entry.get("fails", 0) + 1
                log(f"{entry['id']} 查询失败({entry['fails']}): {err[:120]}")
                if entry["fails"] == 6:
                    blocks.append(
                        f"⚠️ #{entry.get('no')} {entry['id']} 连续 6 次查询都没结果，"
                        f"可能是数据源额度用尽或单号异常（{err[:60]}）。"
                    )
                    changed = True
                continue
            if entry.get("locked"):
                log(f"{entry['id']} 指定承运商 {code} 暂无轨迹（locked，不改判）")
                continue
            # 缓存承运商查不到 → 重新识别一次
            new_code, payload2 = resolve(entry["id"], None, entry.get("phone") or "")
            if new_code:
                entry["carrier"] = new_code
                entry["carrier_label"] = CARRIER_LABEL.get(new_code, new_code)
                code = new_code
                events = events_of(payload2)
                payload = payload2
        entry["fails"] = 0
        if not events:
            log(f"{entry['id']} 无轨迹（{payload.get('message')}）")
            continue
        entry["state"] = str(payload.get("state", entry.get("state", "")))
        newest = events[0]
        seen = (entry.get("last_time") or "", entry.get("last_context") or "")
        if newest == seen:
            continue
        # 只报告比上次新的（接口倒序，取到上次记录为止）
        fresh = []
        for item in events:
            if item == seen:
                break
            fresh.append(item)
        if not fresh:
            fresh = [newest]
        signed_now = entry["state"] == "3" and not entry.get("signed_notified")
        if signed_now:
            entry["signed_notified"] = True
        entry["last_time"], entry["last_context"] = newest
        blocks.append(render(entry, fresh, signed_now))
        changed = True
        log(f"{entry['id']} 新动态: {newest[0]} {newest[1][:60]}")
    save(data)
    if changed and blocks:
        out("📦 快递动态（%d 单有更新）\n%s" % (len(blocks), "\n\n".join(blocks)))
    return 0


def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--add", metavar="单号")
    ap.add_argument("--remove", metavar="单号")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--check", nargs="?", const="", metavar="单号")
    ap.add_argument("--carrier", help="指定承运商代码（跳过自动识别）")
    ap.add_argument("--label", help="备注，例如「耳机」")
    ap.add_argument("--phone", help="收件人手机后四位（顺丰有时需要）")
    args = ap.parse_args()
    if not (has_kuaidi100_pro() or has_kdniao()):
        log("未配置 KUAIDI100_CUSTOMER/KUAIDI100_KEY 或 KDNIAO_*，本次使用会被限流的免 key 接口")

    if args.add:
        return cmd_add(args)
    if args.remove:
        return cmd_remove(args)
    if args.list:
        return cmd_list(args)
    if args.check is not None:
        return cmd_check(args)
    return run_check(args)


if __name__ == "__main__":
    sys.exit(main())
