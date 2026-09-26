#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
olist.py — 通过本机 OpenList(AList) 访问夸克网盘 / 取直链 / 下载到 NAS

背景
----
夸克对第三方 API 有 50MB 单文件下载上限（错误码 23018），
任何 cookie / 账号等级 / API 域名都绕不开。

但本机（绿联 NAS）跑着 OpenList，里面已经挂载了夸克网盘（driver=Quark）。
OpenList 的 /p/ 直链通道**不受 50MB 限制**，可断点续传，实测 ~26MB/s。

前置条件（本机已满足）
----------------------
1. OpenList 监听 127.0.0.1:5445，管理员 admin/admin
2. OpenList 里已配置 Quark 存储，且 addition.root_folder_id = "0"
   （若是 "/" 会导致 fs/list 返回空列表 —— 这是本机踩过的坑）
3. QAS(quark-auto-save, 127.0.0.1:5005) 用于「转存分享链接到网盘」

用法
----
    python3 olist.py login                      # 测试登录，打印 token
    python3 olist.py ls <网盘路径>                # 列目录
    python3 olist.py ls /电影
    python3 olist.py url <网盘文件路径>           # 取直链（含 sign，有时效）
    python3 olist.py dl <网盘文件路径> <本地目录>   # 下载到 NAS（自动断点续传）
    python3 olist.py dl-dir <网盘目录> <本地目录>   # 下载整个目录

示例
----
    python3 olist.py ls "/电影/冒牌天神 (2003-2007)"
    python3 olist.py dl "/电影/冒牌天神 (2003-2007)/冒牌天神 2003.mkv" \\
        "/volume1/共享影视作品/电影/冒牌天神 (2003-2007)"

注意
----
* 直链带 sign 签名，有时效（通常几十分钟~数小时），过期重新取即可
* OpenList 会把夸克文件**实时代理**给你，不占网盘额外空间
* 本脚本零第三方依赖（只用标准库 + curl）
"""

import json
import os
import subprocess
import sys
import urllib.parse

BASE = os.environ.get("OPENLIST_URL", "http://127.0.0.1:5445")
USER = os.environ.get("OPENLIST_USER", "admin")
PASS = os.environ.get("OPENLIST_PASS", "admin")
TOKEN_CACHE = "/tmp/.olist_token"


def _curl(url, payload=None, method="POST", token=None, timeout=60):
    cmd = ["curl", "-s", "-X", method, url, "-H", "Content-Type: application/json"]
    if token:
        cmd += ["-H", "Authorization: " + token]
    if payload is not None:
        cmd += ["-d", json.dumps(payload, ensure_ascii=False)]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    try:
        return json.loads(out)
    except Exception:
        return {"code": -1, "message": out[:300]}


def login():
    """登录 OpenList，返回 token。"""
    r = _curl(f"{BASE}/api/auth/login", {"username": USER, "password": PASS})
    tok = (r.get("data") or {}).get("token")
    if not tok:
        raise SystemExit(f"❌ 登录失败: {r.get('message') or r}")
    with open(TOKEN_CACHE, "w") as f:
        f.write(tok)
    return tok


def get_token(force=False):
    """取 token（带本地缓存）。"""
    if not force and os.path.exists(TOKEN_CACHE):
        t = open(TOKEN_CACHE).read().strip()
        if t:
            return t
    return login()


def _api(path, payload=None, method="POST"):
    """带自动重登的 API 调用（token 失效时重试一次）。"""
    tok = get_token()
    r = _curl(f"{BASE}{path}", payload, method, tok)
    if r.get("code") == 401:
        tok = login()
        r = _curl(f"{BASE}{path}", payload, method, tok)
    return r


def ls(path):
    """列目录，返回 [(name, is_dir, size), ...]"""
    r = _api("/api/fs/list", {"path": path, "page": 1, "per_page": 500, "refresh": True})
    if r.get("code") != 200:
        raise SystemExit(f"❌ 列目录失败 [{path}]: {r.get('message')}")
    out = []
    for it in (r.get("data") or {}).get("content") or []:
        out.append((it.get("name"), it.get("is_dir"), it.get("size") or 0))
    return out


def get_url(path):
    """取文件直链（含 sign）。"""
    r = _api("/api/fs/get", {"path": path, "refresh": True})
    if r.get("code") != 200:
        raise SystemExit(f"❌ 取直链失败 [{path}]: {r.get('message')}")
    url = (r.get("data") or {}).get("raw_url")
    if not url:
        raise SystemExit(f"❌ 未返回直链: {path}")
    return url


def _h(n):
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.2f}{u}"
        n /= 1024
    return f"{n:.2f}PB"


def download(path, local_dir, retry=5):
    """下载单个网盘文件到本地目录（curl -C - 断点续传）。"""
    os.makedirs(local_dir, exist_ok=True)
    name = path.rstrip("/").split("/")[-1]
    dest = os.path.join(local_dir, name)
    url = get_url(path)
    cmd = ["curl", "-L", "-C", "-", "--retry", str(retry), "--retry-delay", "3",
           "-o", dest, "-w", "%{http_code} %{size_download} %{speed_download}",
           "--create-dirs", url]
    print(f"⬇️  {name}")
    print(f"   → {dest}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    parts = (r.stdout or "").split()
    if parts and parts[0] in ("200", "206"):
        sz = os.path.getsize(dest) if os.path.exists(dest) else 0
        sp = float(parts[2]) / 1048576 if len(parts) > 2 else 0
        print(f"   ✅ HTTP {parts[0]} | {_h(sz)} | {sp:.1f} MB/s")
        return dest
    print(f"   ❌ 失败: {r.stderr[:200]}")
    return None


def download_dir(remote_dir, local_dir):
    """递归下载整个网盘目录。"""
    results = []
    for name, is_dir, size in ls(remote_dir):
        rp = f"{remote_dir.rstrip('/')}/{name}"
        if is_dir:
            results += download_dir(rp, os.path.join(local_dir, name))
        else:
            results.append(download(rp, local_dir))
    return results


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    act = sys.argv[1]

    if act == "login":
        t = login()
        print(f"✅ 登录成功, token 长度 {len(t)}")

    elif act == "ls":
        path = sys.argv[2] if len(sys.argv) > 2 else "/"
        rows = ls(path)
        print(f"📁 {path} ({len(rows)} 项)")
        for name, is_dir, size in rows:
            print(f"  {'📁' if is_dir else '🎞️'} {name:<42} {'' if is_dir else _h(size)}")

    elif act == "url":
        print(get_url(sys.argv[2]))

    elif act == "dl":
        if len(sys.argv) < 4:
            raise SystemExit("用法: olist.py dl <网盘文件路径> <本地目录>")
        download(sys.argv[2], sys.argv[3])

    elif act == "dl-dir":
        if len(sys.argv) < 4:
            raise SystemExit("用法: olist.py dl-dir <网盘目录> <本地目录>")
        res = download_dir(sys.argv[2], sys.argv[3])
        ok = [r for r in res if r]
        print(f"\n完成: {len(ok)}/{len(res)} 个文件")

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
