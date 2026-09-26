#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""搜盘鸭搜索 —— 验证码由外部视觉模型识别（本机无 ddddocr）。
用法:
  python3 sp_vis.py cap [关键词]            # 触发拦截并取验证码图 -> /tmp/sp_cap.png
  python3 sp_vis.py go <code> <关键词...>   # 提交验证码并搜索
  python3 sp_vis.py s <关键词...>           # 复用已通过的 session 直接搜索
"""
import json, base64, sys, time, urllib.parse, urllib.request, http.cookiejar, ssl

BASE = "https://sopanya.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
CJFILE = "/tmp/sp_cookies.txt"
STATE = "/tmp/sp_state.json"


def opener_load():
    cj = http.cookiejar.MozillaCookieJar(CJFILE)
    try:
        cj.load(ignore_discard=True, ignore_expires=True)
    except Exception:
        pass
    op = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    op.addheaders = [("User-Agent", UA), ("Referer", BASE + "/search.html"),
                     ("Origin", BASE), ("Accept", "application/json, text/plain, */*")]
    return op, cj


def post(op, path, payload, timeout=30):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with op.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def sse(op, url, timeout=60):
    out, need_cap = [], False
    req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
    with op.open(req, timeout=timeout) as r:
        start = time.time()
        for raw in r:
            if time.time() - start > timeout:
                break
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if body == "[DONE]":
                break
            try:
                d = json.loads(body)
            except Exception:
                continue
            if d.get("type") == "security":
                need_cap = True
                break
            if d.get("url"):
                out.append(d)
    return out, need_cap


def resolve(op, item, poll=10):
    payload = {"url": urllib.parse.quote(item["url"], safe=""),
               "title": item.get("title") or item.get("name") or "",
               "stoken": item.get("stoken") or "",
               "description": item.get("description") or ""}
    try:
        r = post(op, "/api/other/save_url", payload, timeout=45)
        d = r.get("data") or {}
        if r.get("code") == 200 and d.get("url"):
            return d["url"]
    except Exception:
        pass
    for _ in range(poll):
        try:
            r = post(op, "/api/other/save_status", payload, timeout=8)
            d = (r.get("data") or {})
            res = d.get("resource") or {}
            if d.get("ready") and res.get("url"):
                return res["url"]
        except Exception:
            pass
        time.sleep(0.9)
    return None


def do_search(op, cj, kws):
    for kw in kws:
        url = BASE + "/api/other/web_search?" + urllib.parse.urlencode({"title": kw, "is_type": -1})
        try:
            res, need = sse(op, url)
        except Exception as e:
            print(f"=== {kw}: 搜索异常 {e}")
            continue
        print(f"=== {kw}: {len(res)} 条, 仍被拦截={need} ===")
        for r in res:
            u = r.get("url")
            if not u.startswith("http"):
                u = resolve(op, r) or "(未解析)"
            print(f"  {r.get('title')} || {u} || {r.get('is_type')}")
        cj.save(ignore_discard=True, ignore_expires=True)


if __name__ == "__main__":
    cmd = sys.argv[1]
    op, cj = opener_load()

    if cmd == "cap":
        kw0 = sys.argv[2] if len(sys.argv) > 2 else "test"
        url0 = BASE + "/api/other/web_search?" + urllib.parse.urlencode({"title": kw0, "is_type": -1})
        try:
            _, need = sse(op, url0, timeout=15)
            print("[trigger] need_captcha =", need)
        except Exception as e:
            print("[trigger warn]", e)
        r = post(op, "/api/other/transfer_captcha", {}, timeout=25)
        d = r.get("data") or {}
        if not d.get("image"):
            print("ERR", json.dumps(r, ensure_ascii=False)[:300]); sys.exit(1)
        img = base64.b64decode(d["image"].split(",", 1)[1])
        open("/tmp/sp_cap.png", "wb").write(img)
        json.dump({"token": d["token"]}, open(STATE, "w"))
        cj.save(ignore_discard=True, ignore_expires=True)
        print("OK captcha saved", len(img), "bytes, token", str(d["token"])[:12], "...")

    elif cmd == "go":
        code = sys.argv[2]
        kws = sys.argv[3:]
        st = json.load(open(STATE))
        v = post(op, "/api/other/search_captcha_verify",
                 {"captcha_token": st["token"], "captcha_code": code}, timeout=25)
        print("[verify]", json.dumps(v, ensure_ascii=False)[:200])
        cj.save(ignore_discard=True, ignore_expires=True)
        do_search(op, cj, kws)

    elif cmd == "s":
        do_search(op, cj, sys.argv[2:])

    else:
        print(__doc__)
