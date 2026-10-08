#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""记账本 CLI —— 纯标准库，Python 3.8+ 即可运行。

用法:
  python ledger.py add "买菜 66.47 买西瓜"           # 记一笔（自动解析金额/类型/日期/备注）
  python ledger.py import-csv x.csv --dry-run         # 批量导入（先预演，不加 --dry-run 才写盘）
  python ledger.py add "昨天打车 23" --json
  python ledger.py report                            # 本月账目表
  python ledger.py report --month 2026-09
  python ledger.py report --all                      # 各月汇总
  python ledger.py stats --month 2026-10             # 按类型汇总
  python ledger.py edit 12 --amount 90 --note 买西瓜  # 12 = 当月第 12 笔（或 u44）
  python ledger.py delete 12
  python ledger.py show 12                             # 看某一笔的全部字段（含原话）
  python ledger.py search 西瓜
  python ledger.py export --month 2026-10 --out C:/x/out.csv
  python ledger.py renumber                          # 按日期压实全局 UID（慎用）

数据: $LEDGER_DIR/ledger.jsonl   默认 ~/AppData/Local/hermes/ledger
配置: $LEDGER_DIR/config.json    {"numbering": "monthly" | "global"}
"""
import argparse
import csv
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------- 存储位置

def _default_dir() -> Path:
    env = os.environ.get("LEDGER_DIR")
    if env:
        return Path(env)
    home = Path.home()
    if os.name == "nt":
        return home / "AppData" / "Local" / "hermes" / "ledger"
    return home / ".hermes" / "ledger"


DATA_DIR = _default_dir()
LEDGER_FILE = DATA_DIR / "ledger.jsonl"
CONFIG_FILE = DATA_DIR / "config.json"

DEFAULT_CONFIG = {"numbering": "monthly"}   # monthly=每月从 1 开始；global=跨月连续

# ---------------------------------------------------------------- 类型表
# 顺序即优先级（同分时靠前者胜）；关键词按“最长匹配”计分。
CATEGORIES = [
    {"name": "话费网络", "emoji": "📶", "keywords": [
        "话费", "充话费", "流量", "宽带", "网费", "无线网", "wifi", "手机费",
        "移动", "联通", "电信", "物业费", "生活缴费"]},
    {"name": "水电燃气", "emoji": "💡", "keywords": [
        "水电", "水费", "电费", "燃气费", "燃气", "天然气", "取暖费", "取暖"]},
    {"name": "买菜", "emoji": "🥬", "keywords": [
        "买菜", "菜市场", "蔬菜", "水果", "西瓜", "苹果", "香蕉", "葡萄", "草莓", "橘子",
        "肉", "猪肉", "牛肉", "羊肉", "鸡肉", "鱼", "虾", "鸡蛋", "蛋", "豆腐",
        "超市", "生鲜", "米", "面粉", "粮油", "食用油", "牛奶", "面包", "馒头", "零食"]},
    {"name": "医疗健康", "emoji": "💊", "keywords": [
        "买药", "医药", "药", "医院", "挂号", "体检", "疫苗", "口罩", "保健品",
        "诊所", "看病", "牙医", "眼镜", "理疗", "按摩"]},
    {"name": "外面吃饭", "emoji": "🍚", "keywords": [
        "吃饭", "早饭", "午饭", "晚饭", "早餐", "午餐", "晚餐", "早点", "宵夜", "夜宵",
        "外卖", "买水", "水", "奶茶", "咖啡", "饮料", "可乐", "快餐", "食堂", "餐厅",
        "饭店", "小炒", "米粉", "米线", "面条", "吃面", "烧烤", "火锅", "卤味"]},
    {"name": "交通", "emoji": "🚌", "keywords": [
        "打车", "出租", "滴滴", "地铁", "公交", "高铁", "火车", "机票", "飞机",
        "加油", "油费", "停车", "过路费", "共享单车", "单车", "出行", "车票", "船票", "过桥费"]},
    {"name": "订阅会员", "emoji": "🎫", "keywords": [
        "会员", "订阅", "网盘", "夸克", "百度网盘", "阿里云盘", "icloud", "vpn", "加速器",
        "云服务", "服务器", "域名", "steam", "视频会员", "音乐会员"]},
    {"name": "AI充值", "emoji": "🤖", "keywords": [
        "ai充值", "ai", "gpt", "chatgpt", "claude", "gemini", "api", "大模型", "词元", "token",
        "cursor", "copilot", "智谱", "通义", "kimi", "deepseek", "豆包", "openai", "anthropic"]},
    {"name": "车辆相关", "emoji": "🏍️", "keywords": [
        "摩托车", "救援", "修车", "车辆", "车险", "年检", "机油", "打气"]},
    {"name": "数码家电", "emoji": "📱", "keywords": [
        "数码", "家电", "手机膜", "手机绳", "手机壳", "防丢器", "充电器", "数据线",
        "耳机", "平板", "ipad", "电脑", "键盘", "鼠标", "显示器", "电池"]},
    {"name": "居家日用", "emoji": "🧺", "keywords": [
        "日用", "居家", "清洁剂", "纸巾", "卫生纸", "抽纸", "洗衣液", "牙膏", "洗发水",
        "卫生巾", "安睡裤", "拖鞋", "收纳", "垃圾桶", "衣架"]},
    {"name": "买水果", "emoji": "🍉", "keywords": [
        "买水果", "水果店", "果切", "切果", "橙子", "橙", "柚子", "梨", "桃", "芒果",
        "柠檬", "樱桃", "蓝莓", "火龙果", "哈密瓜", "猕猴桃", "圣女果"]},
    {"name": "娱乐", "emoji": "🎮", "keywords": [
        "娱乐", "游戏", "电影", "电影票", "门票", "ktv", "唱歌", "桌游", "网吧", "钓鱼"]},
    {"name": "人情", "emoji": "🎁", "keywords": ["人情", "份子", "随礼", "红包", "赶礼"]},
    {"name": "快递物流", "emoji": "📮", "keywords": [
        "快递", "寄快递", "寄件", "运费", "邮费", "物流", "顺丰", "菜鸟", "驿站"]},
    {"name": "其他支出", "emoji": "📦", "keywords": ["其他支出"]},
]

OTHER = {"name": "其他", "emoji": "📦"}

TRIGGER_WORDS = ["记一笔", "记个账", "记账本", "记账", "记帐", "记帐本", "账本", "花销", "花费", "花了", "支出"]
FILLER_WORDS = ["一笔", "共", "合计", "元", "块钱", "块", "圆"]
REL_DAYS = {"今天": 0, "当天": 0, "昨天": -1, "昨天晚上": -1, "昨晚": -1,
            "前天": -2, "大前天": -3}

FW_MAP = {ord(c): str(i) for i, c in enumerate("０１２３４５６７８９")}
FW_MAP[ord("．")] = "."
FW_MAP[ord("￥")] = "¥"
FW_MAP[ord("：")] = ":"


# ---------------------------------------------------------------- 基础工具

def norm(text: str) -> str:
    return (text or "").translate(FW_MAP)


def today_str(now_arg=None) -> str:
    """当前日期一律取系统时间（除非测试显式指定 --now / LEDGER_NOW）。"""
    if now_arg:
        return now_arg
    env = os.environ.get("LEDGER_NOW")
    if env:
        return env
    return datetime.now().strftime("%Y-%m-%d")


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def load_entries():
    if not LEDGER_FILE.exists():
        return []
    out = []
    for line in LEDGER_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        e["uid"] = int(e.get("uid") or e.get("id") or 0)   # 兼容旧格式
        if not e["uid"]:
            e["uid"] = max([int(x.get("uid", 0)) for x in out], default=0) + 1
        out.append(e)
    return out


def save_entries(entries):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER_FILE.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        for e in entries:
            e = dict(e)
            e.pop("id", None)
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    os.replace(tmp, LEDGER_FILE)


def append_entry(entry):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with LEDGER_FILE.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def cat_emoji(name: str) -> str:
    for c in CATEGORIES:
        if c["name"] == name:
            return c["emoji"]
    return OTHER["emoji"]


def money(v) -> str:
    return f"{float(v):.2f}"


def month_label(month: str) -> str:
    return f"{month[:4]}年{int(month[5:7])}月"


def next_uid(entries):
    return max((int(e["uid"]) for e in entries), default=0) + 1


def display_ids(entries, cfg):
    """返回 {uid: 表格里显示的编号}。monthly=当月从 1 开始；global=全局连续。"""
    if cfg.get("numbering") == "global":
        return {int(e["uid"]): int(e["uid"]) for e in entries}
    out = {}
    by_month = {}
    for e in entries:
        by_month.setdefault(str(e["date"])[:7], []).append(e)
    for rows in by_month.values():
        rows = sorted(rows, key=lambda e: (str(e["date"]), int(e["uid"])))
        for i, e in enumerate(rows, 1):
            out[int(e["uid"])] = i
    return out


def rows_of_month(entries, month):
    return sorted([e for e in entries if str(e["date"])[:7] == month],
                  key=lambda e: (str(e["date"]), int(e["uid"])))


def month_total(entries, month):
    rows = [e for e in entries if str(e.get("date", ""))[:7] == month]
    return len(rows), round(sum(float(e["amount"]) for e in rows), 2)


def resolve_entry(entries, ref, month, cfg):
    """把用户写的编号解析成条目。支持 u44（全局 UID）与纯数字（当月第 N 笔）。"""
    s = str(ref).strip()
    m = re.fullmatch(r"(?:u|uid)[:#]?(\d+)", s, re.I)
    if m:
        for i, e in enumerate(entries):
            if int(e["uid"]) == int(m.group(1)):
                return i, e
        return None, None
    if not re.fullmatch(r"\d+", s):
        return None, None
    n = int(s)
    if cfg.get("numbering") != "global":
        rows = rows_of_month(entries, month)
        if 1 <= n <= len(rows):
            tgt = rows[n - 1]
            for i, e in enumerate(entries):
                if e is tgt:
                    return i, e
    for i, e in enumerate(entries):
        if int(e["uid"]) == n:
            return i, e
    return None, None


# ---------------------------------------------------------------- 解析

CURRENCY_SUFFIX = re.compile(r"(\d+(?:\.\d{1,2})?)\s*(?:元|块钱|块|圆|¥|￥|rmb)", re.I)
CURRENCY_PREFIX = re.compile(r"[¥￥]\s*(\d+(?:\.\d{1,2})?)")
PLAIN_NUM = re.compile(r"(?<![\d.])(\d+(?:\.\d{1,2})?)(?![\d.])")
UNIT_AFTER = re.compile(r"^\s*(个|杯|份|张|次|斤|瓶|包|袋|只|条|件|公里|千米|km|分钟|小时|点|人|天|周|年|月|日|号|楼|层|台|部|本|支|卷|板|听|罐|盒|桶|把|双|套)")

DATE_FULL = re.compile(r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*[日号]?")
DATE_MD = re.compile(r"(?<![\d-])(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*[日号]")

CN_DIGITS = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}


def cn_num_to_int(s: str):
    """支持 十/十五/二十/二十五/一百/一百二十/三百五 这类简单中文数字。"""
    s = s.strip()
    if not s:
        return None
    section, num = 0, 0
    for ch in s:
        if ch in CN_DIGITS:
            num = CN_DIGITS[ch]
        elif ch == "十":
            section += (num or 1) * 10
            num = 0
        elif ch == "百":
            section += (num or 1) * 100
            num = 0
        else:
            return None
    return section + num


CN_AMOUNT = re.compile(r"([零一两二三四五六七八九十百]+)\s*(?:元|块钱|块|圆)")

# 「5块5」「5元5角」「3块5毛」= 5.5 / 5.5 / 3.5（整元 + 角零头）
CURRENCY_JIAO = re.compile(r"(\d+(?:\.\d{1,2})?)\s*(?:元|块钱|块|圆)\s*(\d{1,2})\s*(?:毛|角)?")
# 零头后面跟着这些字，说明那个数字是数量（「5块 3斤」「10块 2个」），不是角
_TRAILING_QUANTITY = set("年月日号斤个杯份张瓶包袋只条件米分秒小时点人天周次片盒罐桶双套台部本支卷颗根把")


def parse_amount(text: str, ignore_spans):
    """返回 (amount, span) 或 (None, None)。"""
    def inside(span):
        s, e = span
        return any(s >= a and e <= b for a, b in ignore_spans)

    cands = []
    for m in CURRENCY_JIAO.finditer(text):
        tail = text[m.end():m.end() + 1]
        if tail and (tail.isdigit() or tail in _TRAILING_QUANTITY):
            continue          # 后面的数字是数量，不是角零头
        if inside((m.start(), m.end())):
            continue
        frac = int(m.group(2))
        frac = frac / 10 if len(m.group(2)) == 1 else frac / 100   # 5块5=5.5；16块47=16.47
        cands.append((100, m.start(), m.end(), float(m.group(1)) + frac))
    for m in CURRENCY_SUFFIX.finditer(text):
        cands.append((99, m.start(), m.end(), float(m.group(1))))
    for m in CURRENCY_PREFIX.finditer(text):
        cands.append((98, m.start(), m.end(), float(m.group(1))))
    if not cands:
        for m in PLAIN_NUM.finditer(text):
            if inside((m.start(), m.end())):
                continue
            if UNIT_AFTER.match(text[m.end():]):
                continue
            if re.search(r"[第#号]$", text[max(0, m.start() - 2):m.start()]):
                continue
            cands.append((50, m.start(), m.end(), float(m.group(1))))
    if not cands:
        for m in CN_AMOUNT.finditer(text):
            if inside((m.start(), m.end())):
                continue
            v = cn_num_to_int(m.group(1))
            if v:
                cands.append((40, m.start(), m.end(), float(v)))
    if not cands:
        return None, None
    cands.sort(key=lambda c: (c[0], c[1]))
    best = cands[-1]
    return best[3], (best[1], best[2])


def parse_date(text: str, today: str):
    """返回 (iso_date, span) 或 (None, None)。显式日期优先于相对词。"""
    m = DATE_FULL.search(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat(), (m.start(), m.end())
        except ValueError:
            pass
    for word, off in REL_DAYS.items():
        idx = text.find(word)
        if idx >= 0:
            return (date.fromisoformat(today) + timedelta(days=off)).isoformat(), (idx, idx + len(word))
    m = DATE_MD.search(text)
    if m:
        try:
            return date(date.fromisoformat(today).year, int(m.group(1)), int(m.group(2))).isoformat(), (m.start(), m.end())
        except ValueError:
            pass
    return None, None


def detect_category(text: str):
    """最长关键词匹配优先，同长按类型表顺序。返回 (name, matched_kw)。"""
    low = text.lower()
    best = None
    for c in CATEGORIES:
        for kw in c["keywords"]:
            k = kw.lower()
            if k.isascii():
                if not re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", low):
                    continue
            elif k not in low:
                continue
            if best is None or len(k) > best[0]:
                best = (len(k), c["name"], kw)
    return (best[1], best[2]) if best else (OTHER["name"], None)


def build_note(text: str, spans, cat_name: str):
    note = text
    for s, e in sorted([sp for sp in spans if sp], key=lambda x: -x[0]):
        note = note[:s] + " " + note[e:]
    for w in sorted(TRIGGER_WORDS + FILLER_WORDS, key=len, reverse=True):
        note = note.replace(w, " ")
    tokens = [t for t in re.split(r"[\s,，。.、;；:：/|]+", note) if t.strip()]
    if len(tokens) > 1:
        tokens = [t for t in tokens if t != cat_name]
    return " ".join(tokens).strip(" -·*")


def parse_entry(raw: str, today: str):
    text = norm(raw).strip()
    date_iso, dspan = parse_date(text, today)
    amount, aspan = parse_amount(text, [dspan] if dspan else [])
    cat_name, _kw = detect_category(text)
    note = build_note(text, [sp for sp in (dspan, aspan) if sp], cat_name)
    return {"date": date_iso or today, "amount": amount, "category": cat_name, "note": note}


# ---------------------------------------------------------------- 命令

def cmd_add(args):
    entries = load_entries()
    cfg = load_config()
    today = today_str(args.now)
    parsed = parse_entry(args.text, today)
    if parsed["amount"] is None or parsed["amount"] <= 0:
        out = {"ok": False, "error": "need_amount", "parsed": parsed,
               "hint": "没识别出金额，问用户这条记的是多少钱（不要自己猜）。"}
        print(json.dumps(out, ensure_ascii=False) if args.json else
              "❓没识别出金额。这条记的是多少钱？（例：记账 买菜 66.47 买西瓜）")
        return 2
    date_iso = args.date or parsed["date"]
    entry = {
        "uid": next_uid(entries),
        "date": date_iso,
        "amount": round(float(parsed["amount"]), 2),
        "category": parsed["category"],
        "note": parsed["note"],
        "raw": (args.text or "").strip(),
        "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    append_entry(entry)
    entries = load_entries()
    month = date_iso[:7]
    cnt, tot = month_total(entries, month)
    shown = display_ids(entries, cfg).get(entry["uid"], entry["uid"])
    emoji = cat_emoji(entry["category"])
    line = (f"✅ 已记账 #{shown} · {date_iso[5:]} {emoji}{entry['category']} "
            f"{money(entry['amount'])} 元｜备注：{entry['note'] or '（无）'}｜"
            f"{month_label(month)} {cnt} 笔 共 {money(tot)} 元")
    if entry["raw"]:
        line += f"\n📝 原话：{entry['raw']}"
    if entry["category"] == OTHER["name"]:
        line += f"\n⚠️ 类型没匹配上，暂记「{OTHER['emoji']}其他」——要不要新建一个类型？"
    if args.json:
        print(json.dumps({"ok": True, "entry": entry, "shown_id": shown, "month": month,
                          "month_count": cnt, "month_total": tot}, ensure_ascii=False))
    else:
        print(line)
    return 0


def review_line(rows):
    by_cat = {}
    for e in rows:
        d = by_cat.setdefault(e["category"], {"total": 0.0, "n": 0})
        d["total"] += float(e["amount"])
        d["n"] += 1
    ranked = sorted(by_cat.items(), key=lambda kv: -kv[1]["total"])

    def desc(name, d):
        return (f"{cat_emoji(name)}{name}（{d['n']} 笔共 {money(d['total'])} 元）" if d["n"] > 1
                else f"{cat_emoji(name)}{name}（{money(d['total'])} 元）")

    if not ranked:
        return ""
    parts = [desc(*ranked[0])]
    if len(ranked) > 1 and ranked[1][1]["total"] >= ranked[0][1]["total"] * 0.8:
        parts.append(desc(*ranked[1]))
    lines = ["最大头是" + "和".join(parts) + "。"]
    big = max(rows, key=lambda e: float(e["amount"]))
    if big["category"] != ranked[0][0] or ranked[0][1]["n"] > 1:
        lines.append(f"最大单笔：{str(big['date'])[5:]} {big['note'] or big['category']} "
                     f"{money(big['amount'])} 元。")
    return "\n".join(lines)


def cmd_report(args):
    entries = load_entries()
    cfg = load_config()
    today = today_str(args.now)
    if args.all:
        months = sorted({str(e["date"])[:7] for e in entries})
        if not months:
            print("账本还是空的。")
            return 0
        print(f"账本汇总（共 {len(months)} 个月）\n")
        print("| 月份 | 笔数 | 金额 |")
        print("| --- | --- | --- |")
        gn = gt = 0
        for m in months:
            n, t = month_total(entries, m)
            gn += n
            gt = round(gt + t, 2)
            print(f"| {month_label(m)} | {n} | {money(t)} |")
        print(f"\n合计 {gn} 笔，共 {money(gt)} 元。")
        return 0
    month = args.month or today[:7]
    rows = [e for e in entries if str(e["date"])[:7] == month]
    if not rows:
        print(f"{month_label(month)}还没有账目。")
        return 0
    ids = display_ids(entries, cfg)
    rows.sort(key=lambda e: ids.get(int(e["uid"]), 0), reverse=True)
    cnt, tot = month_total(entries, month)
    print(f"{month_label(month)}账目（{cnt} 笔，共 {money(tot)} 元）")
    print()
    if args.format == "plain":
        for e in rows:
            print(f"#{ids.get(int(e['uid']))} · {str(e['date'])[5:]} · "
                  f"{cat_emoji(e['category'])}{e['category']} · {money(e['amount'])} · {e['note'] or ''}")
    else:
        print("| 编号 | 日期 | 类型 | 金额 | 备注 |")
        print("| --- | --- | --- | --- | --- |")
        for e in rows:
            print(f"| {ids.get(int(e['uid']))} | {str(e['date'])[5:]} | "
                  f"{cat_emoji(e['category'])}{e['category']} | {money(e['amount'])} | {e['note'] or ''} |")
    rev = review_line(rows)
    if rev:
        print()
        print(rev)
    return 0


def cmd_stats(args):
    entries = load_entries()
    today = today_str(args.now)
    month = args.month or today[:7]
    rows = [e for e in entries if str(e["date"])[:7] == month]
    if not rows:
        print(f"{month_label(month)}还没有账目。")
        return 0
    by_cat = {}
    for e in rows:
        d = by_cat.setdefault(e["category"], {"total": 0.0, "n": 0})
        d["total"] += float(e["amount"])
        d["n"] += 1
    cnt, tot = month_total(entries, month)
    print(f"{month_label(month)}按类型汇总（{cnt} 笔，共 {money(tot)} 元）\n")
    print("| 类型 | 笔数 | 金额 | 占比 |")
    print("| --- | --- | --- | --- |")
    for name, d in sorted(by_cat.items(), key=lambda kv: -kv[1]["total"]):
        pct = (d["total"] / tot * 100) if tot else 0
        print(f"| {cat_emoji(name)}{name} | {d['n']} | {money(d['total'])} | {pct:.1f}% |")
    return 0


def cmd_edit(args):
    entries = load_entries()
    cfg = load_config()
    today = today_str(args.now)
    month = args.month or today[:7]
    idx, entry = resolve_entry(entries, args.ref, month, cfg)
    if entry is None:
        print(f"找不到编号 {args.ref}（{month_label(month)}内）。跨月引用用 u<全局UID>，可用 search 查。")
        return 1
    changes = []
    if args.amount is not None:
        entry["amount"] = round(float(args.amount), 2)
        changes.append(f"金额={money(entry['amount'])}")
    if args.note is not None:
        entry["note"] = args.note
        changes.append(f"备注={args.note}")
    if args.category is not None:
        entry["category"] = args.category
        changes.append(f"类型={args.category}")
    if args.date is not None:
        entry["date"] = args.date
        changes.append(f"日期={args.date}")
    if not changes:
        print("没给要改的字段（--amount / --note / --category / --date）。")
        return 2
    entries[idx] = entry
    save_entries(entries)
    print(f"✅ 已修改 {entry['date'][5:]} {cat_emoji(entry['category'])}{entry['category']} "
          f"{money(entry['amount'])} 元 {entry['note']}（{'，'.join(changes)}）")
    return 0


def cmd_delete(args):
    entries = load_entries()
    cfg = load_config()
    today = today_str(args.now)
    month = args.month or today[:7]
    idx, entry = resolve_entry(entries, args.ref, month, cfg)
    if entry is None:
        print(f"找不到编号 {args.ref}（{month_label(month)}内）。")
        return 1
    deleted = entries.pop(idx)
    save_entries(entries)
    print(f"🗑️ 已删除：{deleted['date'][5:]} {cat_emoji(deleted['category'])}{deleted['category']} "
          f"{money(deleted['amount'])} 元 {deleted['note']}")
    if not args.quiet:
        m = str(deleted["date"])[:7]
        n, t = month_total(entries, m)
        print(f"{month_label(m)}现在 {n} 笔，共 {money(t)} 元。")
    return 0


def cmd_search(args):
    entries = load_entries()
    kw = norm(args.keyword).lower()
    rows = [e for e in entries
            if kw in str(e.get("note", "")).lower() or kw in str(e.get("raw", "")).lower()
            or kw in str(e.get("category", "")).lower()]
    rows.sort(key=lambda e: (str(e["date"]), int(e["uid"])), reverse=True)
    if not rows:
        print(f"没有匹配「{args.keyword}」的记录。")
        return 0
    total = round(sum(float(e["amount"]) for e in rows), 2)
    print(f"匹配「{args.keyword}」共 {len(rows)} 笔，合计 {money(total)} 元（编号为全局 UID）\n")
    for e in rows:
        line = (f"u{e['uid']} · {e['date']} · {cat_emoji(e['category'])}{e['category']} · "
                f"{money(e['amount'])} · {e['note'] or ''}")
        raw = str(e.get("raw") or "").strip()
        if raw and raw != str(e.get("note") or "").strip():
            line += f"｜原话：{raw}"
        print(line)
    return 0


def cmd_export(args):
    entries = load_entries()
    rows = [e for e in entries if not args.month or str(e["date"])[:7] == args.month]
    rows.sort(key=lambda e: (str(e["date"]), int(e["uid"])))
    out = Path(args.out) if args.out else (DATA_DIR / "ledger.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["UID", "日期", "类型", "金额", "备注"])
        for e in rows:
            w.writerow([e["uid"], e["date"], e["category"], money(e["amount"]), e["note"]])
    print(f"已导出 {len(rows)} 笔到 {out}")
    return 0


def cmd_renumber(args):
    entries = load_entries()
    entries.sort(key=lambda e: (str(e["date"]), int(e["uid"])))
    for i, e in enumerate(entries, 1):
        e["uid"] = i
    save_entries(entries)
    print(f"已按日期压实 {len(entries)} 笔的全局 UID（表格编号不受影响）。")
    return 0


def cmd_show(args):
    """打印一笔的全部字段（含原话）。"""
    entries = load_entries()
    cfg = load_config()
    month = args.month or today_str(args.now)[:7]
    _idx, e = resolve_entry(entries, args.ref, month, cfg)
    if e is None:
        print(f"找不到编号 {args.ref}（{month_label(month)}内）。跨月引用用 u<全局UID>。")
        return 1
    ids = display_ids(entries, cfg)
    src = e.get("src") or {}
    print(f"#{ids.get(int(e['uid']), e['uid'])}（全局 u{e['uid']}）")
    print(f"日期：{e['date']}")
    print(f"类型：{cat_emoji(e['category'])}{e['category']}")
    print(f"金额：{money(e['amount'])} 元")
    print(f"备注：{e.get('note') or '（无）'}")
    print(f"原话：{e.get('raw') or '（无）'}")
    print(f"登记：{e.get('ts') or '（无）'}")
    if src:
        print(f"来源：{src.get('file') or '?'} 第 {src.get('no') or '?'} 行")
    return 0


def cmd_config(args):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    if args.numbering:
        cfg["numbering"] = args.numbering
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(cfg, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- CSV 批量导入

CSV_TYPE_ALIASES = {"其他": "其他支出"}


def _norm_date(raw: str, fallback: str) -> str:
    m = re.fullmatch(r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*[日号]?", raw or "")
    if not m:
        return fallback
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def plan_import(path: Path, month=None):
    """解析导出的 CSV → (待写入条目, 跳过统计, 文件名, 未知类型)。不写盘。"""
    with path.open(encoding="utf-8-sig", newline="") as fh:
        table = [r for r in csv.reader(fh) if any((c or "").strip() for c in r)]
    header = [h.strip() for h in (table[0] if table else [])]

    def cell(row, *names):
        """按列名取值（列名做去空白匹配），取不到返回空串。"""
        for n in names:
            if n in header:
                i = header.index(n)
                if i < len(row):
                    return (row[i] or "").strip()
        return ""

    rows = table[1:]
    entries = load_entries()
    seen = set()
    for e in entries:
        src = e.get("src") or {}
        if src.get("file"):
            seen.add((str(src["file"]), str(src.get("no", ""))))
    skipped = {"dup": 0, "bad": 0, "income": 0, "other_month": 0}
    unknown, picked = [], []
    for r in rows:
        no = cell(r, "编号")
        date_iso = _norm_date(cell(r, "日期"), "")
        if not date_iso:
            skipped["bad"] += 1
            continue
        if month and date_iso[:7] != month:
            skipped["other_month"] += 1
            continue
        kind = cell(r, "收支")
        if kind not in ("", "支出"):
            skipped["income"] += 1
            continue
        amt_raw = norm(cell(r, "金额（元）", "金额", "金额(元)")).replace(",", "").replace("¥", "").replace("￥", "")
        try:
            amount = round(float(amt_raw), 2)
        except ValueError:
            skipped["bad"] += 1
            continue
        if amount <= 0:
            skipped["bad"] += 1
            continue
        if (path.name, no) in seen:
            skipped["dup"] += 1
            continue
        cat = cell(r, "类型") or OTHER["name"]
        cat = CSV_TYPE_ALIASES.get(cat, cat)
        if cat != OTHER["name"] and not any(c["name"] == cat for c in CATEGORIES):
            unknown.append(cat)
        picked.append({
            "date": date_iso, "amount": amount, "category": cat,
            "note": cell(r, "备注"),
            "raw": "｜".join(c for c in (x.strip() for x in r) if c),
            "reg": cell(r, "登记时间"), "no": no,
        })
    picked.sort(key=lambda d: (d["date"], d["reg"], int(d["no"]) if d["no"].isdigit() else 0))
    return picked, skipped, path.name, sorted(set(unknown))


def cmd_import(args):
    path = Path(args.path)
    if not path.is_file():
        print(f"找不到文件：{path}")
        return 1
    picked, skipped, fname, unknown = plan_import(path, args.month)
    by_cat, by_month = {}, {}
    for d in picked:
        c = by_cat.setdefault(d["category"], [0, 0.0])
        c[0] += 1
        c[1] = round(c[1] + d["amount"], 2)
        m = by_month.setdefault(d["date"][:7], [0, 0.0])
        m[0] += 1
        m[1] = round(m[1] + d["amount"], 2)
    total = round(sum(d["amount"] for d in picked), 2)
    print(f"📥 import-csv{' 预演（未写入）' if args.dry_run else ''}")
    print(f"文件：{fname}")
    if args.month:
        print(f"只导月份：{args.month}")
    if not picked:
        print("没有可导入的行。")
    else:
        print(f"{'可导入' if args.dry_run else '导入'} {len(picked)} 笔，共 {money(total)} 元")
        for m in sorted(by_month):
            n, t = by_month[m]
            print(f"  {month_label(m)}：{n} 笔 {money(t)} 元")
        print("类型：")
        for name, (n, t) in sorted(by_cat.items(), key=lambda kv: -kv[1][1]):
            print(f"  {cat_emoji(name)}{name}  {n} 笔 {money(t)} 元")
    parts = [f"重复跳过 {skipped['dup']}", f"无法解析 {skipped['bad']}"]
    if skipped["income"]:
        parts.append(f"非支出跳过 {skipped['income']}")
    if skipped["other_month"]:
        parts.append(f"非本月跳过 {skipped['other_month']}")
    print("跳过：" + "、".join(parts))
    if unknown:
        print("⚠️ 类型表里没有：" + "、".join(unknown) + "（会按原样写入，报告里用 📦 显示）")
    if args.dry_run or not picked:
        if args.dry_run:
            print("（去掉 --dry-run 才会真正写入）")
        return 0
    uid = next_uid(load_entries())
    for d in picked:
        append_entry({
            "uid": uid, "date": d["date"], "amount": d["amount"], "category": d["category"],
            "note": d["note"], "raw": d["raw"],
            "ts": (d["reg"].replace(" ", "T") or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")),
            "src": {"file": fname, "no": d["no"]},
        })
        uid += 1
    entries = load_entries()
    print()
    for m in sorted(by_month):
        n, t = month_total(entries, m)
        print(f"✅ {month_label(m)}现在 {n} 笔，共 {money(t)} 元。")
    return 0


# ---------------------------------------------------------------- 入口

def main(argv=None):
    p = argparse.ArgumentParser(description="记账本 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="记一笔")
    a.add_argument("text")
    a.add_argument("--date", help="强制日期 YYYY-MM-DD")
    a.add_argument("--now", help="覆盖“今天”（测试用）")
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=cmd_add)

    r = sub.add_parser("report", help="账目表")
    r.add_argument("--month", help="YYYY-MM")
    r.add_argument("--all", action="store_true")
    r.add_argument("--format", choices=["md", "plain"], default="md")
    r.add_argument("--now", help="覆盖“今天”")
    r.set_defaults(func=cmd_report)

    s = sub.add_parser("stats", help="按类型汇总")
    s.add_argument("--month")
    s.add_argument("--now")
    s.set_defaults(func=cmd_stats)

    e = sub.add_parser("edit", help="修改某笔")
    e.add_argument("ref", help="当月编号，或 u44")
    e.add_argument("--amount", type=float)
    e.add_argument("--note")
    e.add_argument("--category")
    e.add_argument("--date")
    e.add_argument("--month", help="编号所属月份 YYYY-MM")
    e.add_argument("--now")
    e.set_defaults(func=cmd_edit)

    d = sub.add_parser("delete", help="删除某笔")
    d.add_argument("ref", help="当月编号，或 u44")
    d.add_argument("--month")
    d.add_argument("--now")
    d.add_argument("--quiet", action="store_true")
    d.set_defaults(func=cmd_delete)

    q = sub.add_parser("search", help="搜索")
    q.add_argument("keyword")
    q.set_defaults(func=cmd_search)

    x = sub.add_parser("export", help="导出 CSV")
    x.add_argument("--month")
    x.add_argument("--out")
    x.set_defaults(func=cmd_export)

    n = sub.add_parser("renumber", help="压实全局 UID")
    n.set_defaults(func=cmd_renumber)

    i = sub.add_parser("import-csv", help="从导出的 CSV 批量导入")
    i.add_argument("path")
    i.add_argument("--month", help="只导入该月 YYYY-MM")
    i.add_argument("--dry-run", action="store_true", help="只预演，不写盘")
    i.set_defaults(func=cmd_import)

    w = sub.add_parser("show", help="看某一笔的全部字段（含原话）")
    w.add_argument("ref", help="当月编号，或 u44")
    w.add_argument("--month")
    w.add_argument("--now")
    w.set_defaults(func=cmd_show)

    c = sub.add_parser("config", help="查看/设置编号模式")
    c.add_argument("--numbering", choices=["monthly", "global"])
    c.set_defaults(func=cmd_config)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
