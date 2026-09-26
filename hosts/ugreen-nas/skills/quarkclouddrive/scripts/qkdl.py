#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qkdl.py —— 夸克网盘大文件下载器（绕过官方 CLI 50MB 限制）

背景：
  官方 quarkclouddrive CLI 走开放平台接口 POST /open/v1/file/get_download_url，
  该接口对单文件有 50MB 上限（错误码 23018: download file size limit[52428800]）。
  本脚本改走网页版接口 POST /1/clouddrive/file/download（需 cookie 或
  带签名的 header），无大小限制，可下载任意大小文件到本地。

依赖：仅标准库（urllib）。如需并发可使用 threading。

用法:
  python3 qkdl.py --fid "<FID>" --output-dir /volume1/xxx
  python3 qkdl.py --fid "<FID>" --fid "<FID2>" --output-dir /volume1/xxx
  python3 qkdl.py --url "https://.../direct.link" --output /path/to/file.mkv
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://drive-pc.quark.cn"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
CLIENT_ID = "third_party_agent"
SIGN_KEY = "cf134812e2de4032bd1cb7c3727e84b3"

CONFIG_CANDIDATES = [
    "/opt/data/skills/quarkclouddrive/hermes/config.json",
    os.path.expanduser("~/.quarkclouddrive/hermes/config.json"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "hermes", "config.json"),
]


def load_token():
    """从 CLI 的登录态里取出 access_token。"""
    for p in CONFIG_CANDIDATES:
        p = os.path.abspath(p)
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        uid = d.get("currentUserId")
        if uid and isinstance(d.get(uid), dict):
            tok = d[uid].get("accessToken")
            if tok:
                return tok
    raise SystemExit("未找到夸克登录态，请先执行: scripts/qk login")


def _req(url, body=None, method=None, token=None, extra_headers=None):
    method = method or ("POST" if body is not None else "GET")
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://pan.quark.cn",
        "Referer": "https://pan.quark.cn/",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Cookie"] = f"__pus={token}; __kp={token}; __puus={token}"
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def signed_headers(path, method="POST", token=None):
    """构造开放平台签名头（x-pan-*），用于免 cookie 调用。"""
    tm = str(int(time.time() * 1000))
    raw = f"{method.upper()}&{path}&{tm}&{SIGN_KEY}"
    tok = hashlib.sha256(raw.encode()).hexdigest()
    h = {
        "x-pan-client-id": CLIENT_ID,
        "x-pan-tm": tm,
        "x-pan-token": tok,
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def get_download_url(fid, token):
    """取直链。优先网页版接口，失败则回退开放平台接口。"""
    # 1) 网页版接口（无 50MB 限制）
    for base in (API_BASE, "https://drive.quark.cn", "https://pan.quark.cn"):
        try:
            r = _req(f"{base}/1/clouddrive/file/download?pr=ucpro&fr=pc",
                     body={"fids": [fid]}, token=token)
            if r.get("status") == 200 and r.get("data"):
                return r["data"][0].get("download_url"), r["data"][0].get("file_name")
            err = f"{r.get('code')}|{r.get('message')}"
        except Exception as e:
            err = str(e)
    # 2) 备用：开放平台接口
    try:
        p = "/open/v1/file/get_download_url"
        url = (f"https://open-api-drive.quark.cn{p}?req_id="
               f"{int(time.time()*1000)}&access_token={urllib.parse.quote(token)}")
        r = _req(url, body={"fid": fid}, extra_headers=signed_headers(p, token=token))
        if r.get("status") == 0 and r.get("data"):
            return r["data"].get("download_url"), r["data"].get("file_name")
    except Exception as e:
        err = str(e)
    raise SystemExit(f"取直链失败: {err}")


def download(url, dest, chunk=1 << 20):
    """断点续传下载。"""
    pos = os.path.getsize(dest) if os.path.exists(dest) else 0
    headers = {"User-Agent": UA, "Referer": "https://pan.quark.cn/"}
    if pos:
        headers["Range"] = f"bytes={pos}-"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers.get("Content-Length", 0)) + pos
        mode = "ab" if pos and r.status == 206 else "wb"
        if mode == "wb":
            pos = 0
        done = pos
        t0 = time.time()
        with open(dest, mode) as f:
            while True:
                buf = r.read(chunk)
                if not buf:
                    break
                f.write(buf)
                done += len(buf)
                sp = (done - pos) / max(time.time() - t0, 1e-6)
                pct = f"{done*100/total:.1f}%" if total else "?"
                print(f"\r  {pct}  {done/1048576:.1f}MB  {sp/1048576:.1f}MB/s",
                      end="", flush=True)
    print()
    return dest


def main():
    ap = argparse.ArgumentParser(description="夸克网盘大文件下载（绕过 50MB 限制）")
    ap.add_argument("--fid", action="append", default=[], help="文件 FID，可多次")
    ap.add_argument("--url", help="直接给直链下载")
    ap.add_argument("--output", help="--url 模式的输出文件路径")
    ap.add_argument("--output-dir", default="./downloads", help="输出目录")
    ap.add_argument("--name", help="强制文件名")
    a = ap.parse_args()

    if a.url:
        dest = a.output or os.path.join(a.output_dir, "download.bin")
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        print(f"下载 -> {dest}")
        download(a.url, dest)
        print("完成:", dest)
        return

    if not a.fid:
        ap.error("需要 --fid 或 --url")
    os.makedirs(a.output_dir, exist_ok=True)
    token = load_token()
    for fid in a.fid:
        print(f"取直链: {fid[:40]}...")
        url, fname = get_download_url(fid, token)
        fname = a.name or fname or f"{fid[:12]}.bin"
        dest = os.path.join(a.output_dir, fname)
        print(f"下载 -> {dest}")
        download(url, dest)
        print("完成:", dest, os.path.getsize(dest) / 1048576, "MB")


if __name__ == "__main__":
    main()
