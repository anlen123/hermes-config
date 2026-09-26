#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xiageba.py — 全盘搜 (xiageba.liumingye.cn) 网盘资源搜索 + 真实分享链接解析

本机唯一在用的网盘搜索工具（用户要求：不再使用搜盘鸭 sopanya）。
优点：无需图形验证码，直接出明文直链，快。

用法:
    python3 xiageba.py "关键词" [选项]

选项:
    --pan {all,quark,baidu,uc,xunlei,ali}   只看某网盘 (默认 all)
    --pages N                               每组合关键词翻 N 页 (默认 1)
    --no-smart                              关闭自动关键词收窄（见下）
    --strict                                只输出「标题含关键词」的结果
    --page N / --page-size N                单次搜索的页码/每页条数（服务端 pageSize 无效，恒 10）
    --exact                                 服务端未实现，加了等于没加（保留兼容）
    --sort {default,time,size}              排序
    --time {any,day,week,month,year}        时间范围
    --no-url                                只要元数据，不解析真实下载链接(快)
    --json                                  以 JSON 输出
    --limit N                               最多解析前 N 条(默认 10)

输出(默认): 标题 || 真实链接 || 网盘类型     （★ = 标题精确命中关键词）

⚠️ 关键坑：服务端把关键词**分词后做 AND 模糊匹配**，不是标题包含匹配！
   * 搜「深情眼」→ 被拆成「深情」+「眼」，返回 1000 条《深情诱引》《他深情侵入》之类的
     泛结果，真正的《深情眼》挤不进前 10 条 → 看起来像"站内没有"，其实是排序被淹没。
   * `exact=true` 服务端没实现，`pageSize` 也被忽略（恒返回 10 条/页）。
   * 解法 = **多词收窄**：`深情眼 2025`(22 条) / `深情眼 4K`(16 条) 都能一击命中。
     本脚本的 --smart（默认开）会自动补 [今年/去年/4K/全集/完结] 后缀重搜，
     并优先展示标题命中项，等效于人工换词。

站点机制 (逆向结论, 2026-09):
  * 搜索:  GET /api/source/search?q=&page=&pageSize=&exact=&pan=&sort=&time=&type=
           → {"data":[{id,title,menu,type,createdAt}], "total","page","pageSize","totalPages"}
  * 详情:  GET /api/source/<id>?similar=1
  * 链接:  POST /api/source/geturl   body={"id":"<id>"}  → {"url":"https://pan.quark.cn/s/..."}
           链接有效期 30 分钟，服务端有 redis 缓存
           也支持 body={"url":"<密文/明文url>"} 由搜索页转存弹窗使用
  * 目录:  GET /api/source/tree?id=<id>   → {"success":true,"tree":"..."}
"""
import sys
import json
import time
import argparse
import urllib.parse

try:
    import requests
except ImportError:
    sys.stderr.write("[!] 需要 requests: 用 /opt/hermes/.venv/bin/python 跑\n")
    sys.exit(2)

BASE = "https://xiageba.liumingye.cn"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 域名 → 网盘类型（以 URL 为准，不信接口 type 字段）
PAN_HOSTS = [
    ("pan.quark.cn", "quark"),
    ("drive-m.quark.cn", "quark"),
    ("pan.baidu.com", "baidu"),
    ("drive.uc.cn", "uc"),
    ("fast.uc.cn", "uc"),
    ("aliyundrive.com", "ali"),
    ("alipan.com", "ali"),
    ("cloud.189.cn", "tianyi"),
    ("caiyun.139.com", "mobile"),
    ("115.com", "115"),
    ("115cdn.com", "115"),
    ("xunlei.com", "xunlei"),
    ("pan.xunlei.com", "xunlei"),
]

PAN_LABEL = {
    "quark": "夸克", "baidu": "百度", "uc": "UC", "ali": "阿里",
    "xunlei": "迅雷", "tianyi": "天翼", "mobile": "移动", "115": "115",
    "other": "其他", "music": "音乐", "ai": "AI",
}

# --smart 自动补的后缀（单个中文关键词才启用）
SMART_SUFFIXES = ("全集", "完结", "4K", "国语中字", "电视剧")


def detect_pan(url: str, fallback: str = "other") -> str:
    if not url:
        return fallback
    u = url.lower()
    for host, name in PAN_HOSTS:
        if host in u:
            return name
    return fallback


def make_session() -> "requests.Session":
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Origin": BASE,
        "Referer": BASE + "/",
    })
    return s


def search(sess, q, page=1, page_size=10, exact=False, pan=None,
           sort=None, time_range=None, types=None, timeout=30, retries=3):
    """搜索资源列表。返回 (items, meta)。"""
    params = {"q": q, "page": str(page), "pageSize": str(page_size)}
    if pan and pan not in ("all", ""):
        params["pan"] = pan
    if sort and sort != "default":
        params["sort"] = sort
    if time_range and time_range != "any":
        params["time"] = time_range
    if exact:
        params["exact"] = "true"
    last_err = None
    for attempt in range(retries):
        try:
            r = sess.get(f"{BASE}/api/source/search", params=params, timeout=timeout)
            if r.status_code == 200:
                d = r.json()
                return d.get("data", []), d
            last_err = f"HTTP {r.status_code}"
        except Exception as e:  # noqa
            last_err = str(e)
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"搜索失败: {last_err}")


def get_url(sess, rid=None, url=None, timeout=30, retries=3):
    """POST /api/source/geturl → 真实分享链接。"""
    body = {"id": rid} if rid else {"url": url}
    last_err = None
    for attempt in range(retries):
        try:
            r = sess.post(f"{BASE}/api/source/geturl", json=body,
                          timeout=timeout,
                          headers={"Referer": f"{BASE}/source/{rid}" if rid else BASE})
            if r.status_code == 200:
                d = r.json()
                if d.get("url"):
                    return d["url"]
                last_err = d.get("message") or d.get("error") or "无 url 字段"
            else:
                last_err = f"HTTP {r.status_code}"
        except Exception as e:  # noqa
            last_err = str(e)
        time.sleep(1.2 * (attempt + 1))
    return None, last_err if last_err else "失败"


def get_tree(sess, rid, timeout=30):
    try:
        r = sess.get(f"{BASE}/api/source/tree", params={"id": rid}, timeout=timeout)
        if r.status_code == 200:
            d = r.json()
            return d.get("tree") if d.get("success") else None
    except Exception:  # noqa
        pass
    return None


# --------------------------------------------------------------------------
# 关键词收窄 / 深搜（解决"分词模糊匹配把精确结果淹没"的问题）
# --------------------------------------------------------------------------

def _tokens(keyword):
    return [t for t in (keyword or "").split() if t]


def title_hit(title, keyword):
    """标题是否包含关键词的全部词元（多词 = AND）。"""
    toks = _tokens(keyword)
    return bool(toks) and all(t in (title or "") for t in toks)


def expand_queries(keyword, smart=True):
    """单个中文关键词 → 自动补后缀收窄（年份最有效）。"""
    qs = [keyword]
    if smart and len(_tokens(keyword)) == 1 and len(keyword.strip()) >= 2:
        year = time.localtime().tm_year
        for suf in (str(year), str(year - 1)) + SMART_SUFFIXES:
            q = f"{keyword} {suf}"
            if q not in qs:
                qs.append(q)
    return qs


def deep_search(sess, keyword, pages=1, smart=True, pan=None, sort=None,
                time_range=None, page_size=10, pause=0.3):
    """多组合关键词 + 翻页去重，命中项优先。返回 (hits, rest, metas)。"""
    seen, hits, rest, metas = set(), [], [], []
    for q in expand_queries(keyword, smart):
        for p in range(1, max(1, pages) + 1):
            try:
                batch, meta = search(sess, q, page=p, page_size=page_size,
                                     pan=pan, sort=sort, time_range=time_range)
            except Exception as e:  # noqa
                metas.append({"q": q, "page": p, "error": str(e)})
                break
            metas.append({"q": q, "page": p, "total": meta.get("total"),
                          "n": len(batch)})
            for it in batch:
                rid = it.get("id")
                if not rid or rid in seen:
                    continue
                seen.add(rid)
                (hits if title_hit(it.get("title"), keyword) else rest).append(it)
            total_pages = meta.get("totalPages") or 1
            if p >= total_pages or len(batch) < page_size:
                break
            time.sleep(pause)
        if hits:
            break  # 已找到精确命中，不再浪费请求
    return hits, rest, metas


def main():
    ap = argparse.ArgumentParser(description="全盘搜(xiageba)网盘资源搜索")
    ap.add_argument("keyword")
    ap.add_argument("--pan", default="all",
                    choices=["all", "quark", "baidu", "uc", "xunlei", "ali"])
    ap.add_argument("--exact", action="store_true", help="(服务端未实现，无效)")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--page-size", type=int, default=10, help="(服务端忽略)")
    ap.add_argument("--pages", type=int, default=1, help="每组合关键词翻页数")
    ap.add_argument("--no-smart", action="store_true", help="关闭关键词自动收窄")
    ap.add_argument("--strict", action="store_true", help="只输出标题命中的结果")
    ap.add_argument("--sort", default="default")
    ap.add_argument("--time", default="any")
    ap.add_argument("--no-url", action="store_true", help="不解析真实链接")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=10, help="最多解析链接条数")
    args = ap.parse_args()

    sess = make_session()
    if args.pages > 1 or not args.no_smart:
        hits, rest, metas = deep_search(
            sess, args.keyword, pages=args.pages, smart=not args.no_smart,
            pan=args.pan, sort=args.sort, time_range=args.time)
    else:
        try:
            batch, meta = search(sess, args.keyword, page=args.page,
                                 page_size=args.page_size, exact=args.exact,
                                 pan=args.pan, sort=args.sort, time_range=args.time)
        except Exception as e:  # noqa
            sys.stderr.write(f"[!] {e}\n")
            sys.exit(1)
        hits = [it for it in batch if title_hit(it.get("title"), args.keyword)]
        rest = [it for it in batch if it not in hits]
        metas = [{"q": args.keyword, "page": args.page, "total": meta.get("total")}]

    items = hits + rest
    if args.strict:
        items = hits

    if not items:
        if args.json:
            print(json.dumps({"keyword": args.keyword, "items": []}, ensure_ascii=False))
        else:
            print(f"未找到结果: {args.keyword}")
            print("  提示：换成『片名 + 年份』『片名 + 4K』『主演名』再试（站内是分词匹配）")
        return

    out = []
    for i, it in enumerate(items):
        rid = it.get("id")
        typ = it.get("type", "other")
        rec = {
            "id": rid,
            "title": it.get("title", ""),
            "type": typ,
            "type_label": PAN_LABEL.get(typ, typ),
            "created_at": it.get("createdAt"),
            "hit": title_hit(it.get("title"), args.keyword),
            "url": None,
            "real_pan": typ,
        }
        if not args.no_url and i < args.limit and rid:
            res = get_url(sess, rid=rid)
            if isinstance(res, tuple):
                rec["error"] = res[1]
            else:
                rec["url"] = res
                rec["real_pan"] = detect_pan(res, typ)
        out.append(rec)
        time.sleep(0.35)  # 温和请求

    if args.json:
        print(json.dumps({
            "keyword": args.keyword,
            "queries": [m.get("q") for m in metas],
            "items": out,
        }, ensure_ascii=False, indent=2))
    else:
        queried = ", ".join(dict.fromkeys(m.get("q") for m in metas if m.get("q")))
        print(f"# 关键词「{args.keyword}」 命中 {len(hits)} 条 / 参考 {len(rest)} 条")
        print(f"# 实际查询: {queried}")
        if not hits:
            print("# ⚠️ 没有标题精确命中的结果，以下均为分词模糊匹配，建议换词"
                  "（片名+年份 / 片名+4K / 主演名）")
        for rec in out:
            star = "★ " if rec.get("hit") else "  "
            if rec["url"]:
                print(f"{star}{rec['title']} || {rec['url']} || {PAN_LABEL.get(rec['real_pan'], rec['real_pan'])}")
            else:
                note = f" [未解析: {rec.get('error','')}]" if rec.get("error") else " [未解析]"
                print(f"{star}{rec['title']} || - || {rec['type_label']}{note}")


if __name__ == "__main__":
    main()
