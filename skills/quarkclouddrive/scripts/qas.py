#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qas.py — 操作本机绿联「网盘工具」(quark-auto-save / QAS) 的脚本

这个绿联 App 实际是开源项目 quark-auto-save：
    https://github.com/Cp0204/quark-auto-save
它常驻登录着你的夸克账号，核心能力是**把夸克分享链接转存到你的网盘**。

为什么需要它
------------
夸克网盘对第三方下载有 50MB 单文件上限（23018），无法直接下载大文件。
但「转存」（保存别人分享的文件到你自己的网盘）**不受此限制**。
所以完整流程是：
    1) qas.py save     把分享链接转存到网盘指定目录（默认用完即删任务）
    2) olist.py dl     再用 OpenList 直链下载到 NAS 本地（无大小限制）

⚠️ 转存任务的默认生命周期（用户明确要求）
------------------------------------------
    转存 = 一次性动作，不是常驻配置。
    `save` 默认走「转存 → 立即执行 → 删除任务」，只留下文件，
    不留任何定时追更任务。只有当用户**明确说「保存成任务」**时，
    才加 --keep 保留常驻任务。

连接信息（本机已就绪）
----------------------
    http://127.0.0.1:5005   账号 admin / admin
    API Token: 见 --token / 从 /data 的 api_token 字段读取

用法
----
    python3 qas.py info                    # 显示账号/任务/token 概览
    python3 qas.py tasks                   # 列出转存任务
    python3 qas.py save <分享链接> <保存路径> [任务名] [--keep]
                                           # ⭐ 一次性转存（默认用完即删）
    python3 qas.py save ... --keep          # 仅当用户要求"保存成任务"时
    python3 qas.py add <分享链接> <保存路径> [任务名]
    python3 qas.py run                     # 立即执行全部任务
    python3 qas.py share <分享链接>          # 解析分享链接内容（不转存）
    python3 qas.py paths [网盘路径]          # 列网盘目录（QAS 视角）
    python3 qas.py rm <任务名>               # 删除任务

典型用法
--------
    # 转存「冒牌天神」合集到网盘 /电影/冒牌天神 (2003-2007)
    python3 qas.py add "https://pan.quark.cn/s/6d5b2411c83d" "/电影/冒牌天神 (2003-2007)" "冒牌天神"
    python3 qas.py run

坑
--
* QAS **不能转存自己创建的分享**（会报「用户禁止转存自己的分享」）—— 找别人的分享链接
* add_task 的 savepath 会自动创建目录
* /get_savepath_detail 只支持 GET，POST 会返回 405
* 运行日志是 SSE 流，用 curl 直接读 `data:` 行即可
"""

import json
import os
import subprocess
import sys

BASE = os.environ.get("QAS_URL", "http://127.0.0.1:5005")
USER = os.environ.get("QAS_USER", "admin")
PASS = os.environ.get("QAS_PASS", "admin")
COOKIE_JAR = "/tmp/.qas_cookie"


def _curl(url, payload=None, method="GET", user_agent=None, extra=None, timeout=120,
          cookie=None, raw=False):
    cmd = ["curl", "-s", "-X", method, url]
    if user_agent:
        cmd += ["-A", user_agent]
    if payload is not None:
        cmd += ["-H", "Content-Type: application/json", "-d",
                json.dumps(payload, ensure_ascii=False)]
    if cookie:
        cmd += ["-H", "Cookie: " + cookie]
    for e in (extra or []):
        cmd += e
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    if raw:
        return out
    try:
        return json.loads(out)
    except Exception:
        return {"code": -1, "message": out[:400]}


def login():
    """表单登录，保存 Cookie 到本地 jar。"""
    out = subprocess.run(
        ["curl", "-s", "-c", COOKIE_JAR, "-X", "POST", f"{BASE}/login",
         "-H", "Content-Type: application/x-www-form-urlencoded",
         "--data-urlencode", f"username={USER}",
         "--data-urlencode", f"password={PASS}",
         "-o", "/dev/null", "-w", "%{http_code}"],
        capture_output=True, text=True, timeout=30).stdout
    if out.strip() != "302":
        raise SystemExit(f"❌ 登录失败 HTTP {out.strip()}")
    return cookie()


def cookie():
    """从 jar 读 Cookie 字符串。

    注意：curl 的 Netscape 格式会把 HttpOnly cookie 写成
    `#HttpOnly_127.0.0.1\tFALSE\t/...`，行首带 '#'。
    所以不能简单过滤 '#' 开头的行，必须先剥掉 '#HttpOnly_' 前缀。
    """
    try:
        out = []
        for line in open(COOKIE_JAR).read().splitlines():
            if not line or line.startswith("# ") or line.startswith("# Netscape"):
                continue
            line = line.lstrip("#HttpOnly_")
            parts = line.split("\t")
            if len(parts) >= 7:
                out.append(f"{parts[5]}={parts[6]}")
        return "; ".join(out)
    except Exception:
        return ""


def get_cookie(force=False):
    if force or not os.path.exists(COOKIE_JAR):
        return login()
    c = cookie()
    return c if c else login()


def api_token():
    """读 QAS 的 API Token（用于 /api/add_task）。"""
    d = _curl(f"{BASE}/data", cookie=get_cookie())
    return (d.get("data") or {}).get("api_token")


def data():
    """整体配置数据。"""
    d = _curl(f"{BASE}/data", cookie=get_cookie())
    if not d.get("data"):
        # cookie 可能过期，重登一次
        _curl(f"{BASE}/data", cookie=login())
        d = _curl(f"{BASE}/data", cookie=get_cookie())
    return d.get("data") or {}


def add_task(shareurl, savepath, taskname=None, pattern="", replace=""):
    """添加转存任务（走官方 /api/add_task 接口）。"""
    tk = api_token()
    payload = {
        "taskname": taskname or savepath.rstrip("/").split("/")[-1],
        "shareurl": shareurl,
        "savepath": savepath,
        "pattern": pattern,
        "replace": replace,
    }
    r = _curl(f"{BASE}/api/add_task?token={tk}", payload, "POST")
    return r


def run_now():
    """立即执行全部任务，返回日志文本。"""
    tk = api_token()
    out = _curl(f"{BASE}/run_script_now?token={tk}", {}, "POST", raw=True)
    # SSE: 取 data: 行
    lines = []
    for ln in out.splitlines():
        if ln.startswith("data: "):
            v = ln[6:].rstrip()
            if v:
                lines.append(v)
    return "\n".join(lines)


def share_detail(shareurl):
    """解析分享链接（QAS 视角，能直接看到文件列表）。"""
    return _curl(f"{BASE}/get_share_detail", {"shareurl": shareurl},
                 "POST", cookie=get_cookie())


def share_detail_get(shareurl):
    """get_share_detail 的 GET 变体（部分版本只支持 GET）。"""
    return _curl(f"{BASE}/get_share_detail?shareurl={shareurl}",
                 None, "GET", cookie=get_cookie())


def paths(path="/"):
    """列 QAS 视角下的网盘目录。"""
    return _curl(f"{BASE}/get_savepath_detail?path={path}", None, "GET",
                 cookie=get_cookie())


def set_tasks(tasklist):
    """覆盖式更新任务列表。"""
    d = data()
    d["tasklist"] = tasklist
    return _curl(f"{BASE}/update", d, "POST", cookie=get_cookie())


def delete_task(name):
    """按任务名删除。"""
    d = data()
    tl = [t for t in (d.get("tasklist") or []) if t.get("taskname") != name]
    removed = len(d.get("tasklist") or []) - len(tl)
    d["tasklist"] = tl
    _curl(f"{BASE}/update", d, "POST", cookie=get_cookie())
    return removed


def save_once(shareurl, savepath, taskname=None, pattern="", replace="",
              keep=False):
    """⭐ 一次性转存：加任务 -> 立即执行 -> （默认）删除任务。

    keep=True 时保留为常驻任务（仅当用户明确要求"保存成任务"时才用）。
    返回 (执行日志, 是否已清理)。
    """
    name = taskname or savepath.rstrip("/").split("/")[-1]
    r = add_task(shareurl, savepath, name, pattern, replace)
    if not r.get("success"):
        return f"❌ 加任务失败: {r.get('message') or r}", False
    log = run_now()
    if keep:
        return log, False
    # 用完即删：只保留本次转存效果，不留常驻任务
    delete_task(name)
    left = [t.get("taskname") for t in (data().get("tasklist") or [])]
    cleaned = name not in left
    return log, cleaned


def _fmt(r):
    return json.dumps(r, ensure_ascii=False, indent=2)[:1500]


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    act = sys.argv[1]

    if act == "login":
        print("✅ 登录成功")
        print("Cookie:", get_cookie()[:80], "...")

    elif act == "info":
        d = data()
        ck = d.get("cookie")
        if isinstance(ck, list):
            ck = "; ".join(ck)
        print(f"🔑 API Token : {d.get('api_token')}")
        print(f"🍪 夸克Cookie: {len(ck or '')} 字节")
        print(f"📋 任务数    : {len(d.get('tasklist') or [])}")
        print(f"⏰ crontab   : {d.get('crontab')}")
        print(f"📤 推送配置  : {list((d.get('push_config') or {}).keys())}")

    elif act == "tasks":
        tl = data().get("tasklist") or []
        if not tl:
            print("(无任务)")
        for t in tl:
            print(f"  📌 {t.get('taskname')}")
            print(f"     {t.get('shareurl')}  ->  {t.get('savepath')}")

    elif act == "add":
        if len(sys.argv) < 4:
            raise SystemExit("用法: qas.py add <分享链接> <保存路径> [任务名]")
        r = add_task(sys.argv[2], sys.argv[3],
                     sys.argv[4] if len(sys.argv) > 4 else None)
        print("✅" if r.get("success") else "❌", r.get("message") or r)

    elif act == "save":
        if len(sys.argv) < 4:
            raise SystemExit("用法: qas.py save <分享链接> <保存路径> [任务名] [--keep]")
        args = sys.argv[2:]
        keep = "--keep" in args
        args = [a for a in args if a != "--keep"]
        log, cleaned = save_once(args[0], args[1],
                                 args[2] if len(args) > 2 else None, keep=keep)
        print(log)
        if keep:
            print("\n📌 已保留为常驻任务")
        else:
            print("\n🗑️ 任务已自动清理" if cleaned else "\n⚠️ 任务清理失败，请手动检查")

    elif act == "run":
        print(run_now())

    elif act == "share":
        r = share_detail(sys.argv[2])
        if not r.get("success"):
            r = share_detail_get(sys.argv[2])
        d = r.get("data") or {}
        sh = d.get("share") or {}
        print(f"标题: {sh.get('title')}  文件数: {sh.get('file_num')}  "
              f"大小: {(sh.get('size') or 0)/1024**3:.2f}GB")
        for f in d.get("list") or []:
            print(f"  {'📁' if f.get('dir') else '🎞️'} {f.get('file_name')}")

    elif act == "paths":
        p = sys.argv[2] if len(sys.argv) > 2 else "/"
        r = paths(p)
        d = r.get("data") or {}
        for f in d.get("list") or []:
            print(f"  {'📁' if f.get('dir') else '🎞️'} {f.get('file_name')}")

    elif act == "rm":
        n = delete_task(sys.argv[2])
        print(f"✅ 删除 {n} 个任务" if n else "⚠️ 未找到该任务")

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
