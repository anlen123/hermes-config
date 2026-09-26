#!/usr/bin/env python3
"""
搜盘鸭 (sopanya.com) 网盘资源搜索 —— 全自动：过验证码 + 解析 + 解链

用法:
    python3 sopanya.py "关键词" [is_type]
      is_type: -1=全部(默认)  0=夸克  1=百度  2=UC  3=阿里  4=迅雷

输出:
    标题 || 真实链接 || 类型名

说明:
    - 页面 SSR 部分含「本地缓存结果」，直接给明文直链
    - SSE /api/other/web_search 返回的 url 是密文，需经
      POST /api/other/save_url {url(encodeURIComponent), title, stoken, description}
      再由 POST /api/other/save_status 轮询取 result.resource.url 得到真链
    - 搜索需过 /api/other/transfer_captcha + search_captcha_verify 图形验证码
"""
import json, base64, sys, time, urllib.parse, urllib.request
import http.cookiejar, ssl, re

BASE = "https://sopanya.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TYPE_NAMES = {0: "夸克", 1: "百度", 2: "UC", 3: "阿里", 4: "迅雷"}

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(cj),
    urllib.request.HTTPSHandler(context=ssl.create_default_context()),
)
opener.addheaders = [
    ("User-Agent", UA),
    ("Referer", BASE + "/search.html"),
    ("Origin", BASE),
    ("Accept", "application/json, text/plain, */*"),
]

_ocr = None


def _post(path, payload, timeout=20):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _ocr_init():
    global _ocr
    if _ocr is None:
        import ddddocr
        _ocr = ddddocr.DdddOcr(show_ad=False)
    return _ocr


def solve_captcha(max_try=12):
    for _ in range(max_try):
        r = _post("/api/other/transfer_captcha", {})
        if r.get("code") != 200 or not r.get("data"):
            time.sleep(1); continue
        d = r["data"]
        img = base64.b64decode(d["image"].split(",", 1)[1])
        code = re.sub(r"[^0-9a-zA-Z]", "", _ocr_init().classification(img) or "")
        if len(code) == 4:
            return d["token"], code
        time.sleep(0.3)
    return None


def _sse(url, timeout=30):
    results, need_cap = [], False
    req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
    with opener.open(req, timeout=timeout) as r:
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
                need_cap = True; break
            if d.get("url"):
                results.append(d)
    return results, need_cap


def _pass_captcha():
    for attempt in range(3):
        sc = solve_captcha()
        if not sc:
            continue
        try:
            if _post("/api/other/search_captcha_verify",
                     {"captcha_token": sc[0], "captcha_code": sc[1]}).get("code") == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def search_raw(keyword, is_type=-1):
    url = (BASE + "/api/other/web_search?" +
           urllib.parse.urlencode({"title": keyword, "is_type": is_type}))
    results, need_cap = _sse(url, 25)
    if need_cap:
        if not _pass_captcha():
            raise RuntimeError("验证码未通过")
        results, need_cap = _sse(url, 60)
        if need_cap:
            raise RuntimeError("验证后仍被拦截")
    return results


def resolve_encrypted(item, poll=14):
    """把 SSE 密文 url 解成真实分享链接"""
    payload = {
        "url": urllib.parse.quote(item["url"], safe=""),
        "title": item.get("title") or item.get("name") or "",
        "stoken": item.get("stoken") or "",
        "description": item.get("description") or "",
    }
    try:
        r = _post("/api/other/save_url", payload, timeout=45)
        d = r.get("data") or {}
        if r.get("code") == 200 and d.get("url"):
            return d["url"]
    except Exception:
        pass
    # 旁路轮询
    for _ in range(poll):
        try:
            r = _post("/api/other/save_status", payload, timeout=8)
            d = (r.get("data") or {})
            res = d.get("resource") or {}
            if d.get("ready") and res.get("url"):
                return res["url"]
        except Exception:
            pass
        time.sleep(0.9)
    return None


def fetch_page_results(keyword):
    """SSR 页面内嵌的本地缓存结果（明文直链）"""
    u = BASE + "/search.html?q=" + urllib.parse.quote(keyword)
    req = urllib.request.Request(u, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": BASE + "/",
    })
    try:
        with opener.open(req, timeout=25) as r:
            t = r.read().decode("utf-8", "ignore")
    except Exception as e:
        print(f"[warn] 页面抓取失败: {e}", file=sys.stderr)
        return []
    out = []
    for m in re.finditer(r"let jsonData = '(\[.*?\])';", t, re.S):
        s = (m.group(1).replace("\\/", "/").replace('\\"', '"')
             .replace("\\'", "'"))
        s = re.sub(r"[\x00-\x1f\x7f]", "", s)
        try:
            for it in json.loads(s):
                if it.get("url"):
                    out.append({"title": it.get("title") or it.get("name"),
                                "url": it["url"],
                                "is_type": it.get("is_type")})
        except Exception:
            pass
    return out


def search(keyword, is_type=-1, resolve=True):
    merged, seen = [], set()

    for r in fetch_page_results(keyword):
        k = r.get("url")
        if k and k not in seen:
            seen.add(k); merged.append(r)

    for r in search_raw(keyword, is_type):
        u = r.get("url")
        if not u or u in seen:
            continue
        if not u.startswith("http"):
            if not resolve:
                continue
            real = resolve_encrypted(r)
            if not real:
                continue
            r = dict(r); r["url"] = real
            u = real
        if u in seen:
            continue
        seen.add(u); merged.append(r)

    return merged


if __name__ == "__main__":
    kw = sys.argv[1] if len(sys.argv) > 1 else ""
    it = int(sys.argv[2]) if len(sys.argv) > 2 else -1
    if not kw:
        print(__doc__); sys.exit(1)
    res = search(kw, it)
    for r in res:
        tn = TYPE_NAMES.get(r.get("is_type"), r.get("is_type"))
        print(f"{r.get('title')} || {r.get('url')} || {tn}")
    print(f"# 共 {len(res)} 条", file=sys.stderr)
