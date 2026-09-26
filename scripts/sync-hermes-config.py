#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync-hermes-config.py — 把本机 Hermes 的配置/记忆/脚本/技能同步到 GitHub 备份仓库

用法:
    python3 /opt/data/scripts/sync-hermes-config.py            # 聚合 → 脱敏 → 复扫 → 提交 → 推送
    python3 /opt/data/scripts/sync-hermes-config.py --dry-run  # 只做到复扫，不提交不推送

设计要点
--------
* **只收白名单路径**，不做「把 /opt/data 整个变成仓库」——那样迟早误传 .env / sessions / 日志。
* **推送前强制安全复扫**：拿 /opt/data/.env 里的真实值反查备份目录，命中即中止。
* git 推送必须 `env -u GIT_SSH_COMMAND`：本机环境被注入了
  `GIT_SSH_COMMAND='ssh -o PubkeyAuthentication=no'`（禁掉公钥认证），
  它的优先级**高于** core.sshCommand，只能在调用时解除。

未备份（有意为之）: .env / auth.json / sessions / logs / cache / checkpoints / platforms
"""
import json
import os
import re
import shutil
import subprocess
import sys

# ── 路径自适应（跨机通用）──────────────────────────────────────────────
# 脚本须位于 <SRC>/hermes-config/scripts/ 下，SRC 由脚本自身位置反推：
#   NAS(docker):  /opt/data/scripts/…                          → SRC=/opt/data
#   Windows 本机: %LOCALAPPDATA%\hermes\hermes-config\scripts\… → SRC=…\hermes
# 可用环境变量 HERMES_SRC 强制覆盖。
def _detect_src():
    here = os.path.dirname(os.path.abspath(__file__))      # …/hermes-config/scripts
    cand = os.path.dirname(os.path.dirname(here))          # …/hermes-config 的父目录
    for p in (os.environ.get("HERMES_SRC"), cand, "/opt/data",
              os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes"),
              os.path.join(os.path.expanduser("~"), ".hermes")):
        if p and os.path.exists(os.path.join(p, "config.yaml")):
            return os.path.normpath(p)
    raise SystemExit("❌ 找不到 Hermes 主目录（需含 config.yaml），请设 HERMES_SRC")


SRC = _detect_src()
DST = os.path.join(SRC, "hermes-config")
# 自建技能白名单（由 list-custom-skills.py 生成）：NAS 上在 SRC/scripts，
# 本机没有 SRC/scripts，则用克隆里那一份。
WHITELIST = next((p for p in (os.path.join(SRC, "scripts", "custom-skills.json"),
                              os.path.join(DST, "scripts", "custom-skills.json"))
                  if os.path.exists(p)),
                 os.path.join(SRC, "scripts", "custom-skills.json"))
PAN_ACCOUNT = ""                      # 脱敏目标：网盘账号名。从**不备份**的 <SRC>/.sync-mask.txt 读取，
                                      # 避免脚本自身把真实账号名带进公开仓库
_mask = os.path.join(SRC, ".sync-mask.txt")
if os.path.exists(_mask):
    PAN_ACCOUNT = open(_mask, encoding="utf-8").read().strip()
PLACEHOLDER = "YOUR_QUARK_ACCOUNT"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store", "*.lock")
DRY = "--dry-run" in sys.argv
# 技能目录里混着的运行时/凭据残留，一律不进备份：
#   hermes/            → quarkclouddrive CLI 的状态目录（含 accessToken / refreshToken）
#   .quarkclouddrive/  → CLI 探测标记
#   search/            → 搜索历史（含网盘文件名与 userId）
#   *.jsonl/*.log      → 同上
SKIP_DIRS = {"hermes", ".quarkclouddrive", "search", "node_modules", ".git"}
SKIP_FILES = shutil.ignore_patterns("*.jsonl", "*.log")


def _skill_ignore(dirpath, names):
    drop = {n for n in names if n in SKIP_DIRS}
    return drop | set(SKIP_FILES(dirpath, names))


def _write(path, text):
    """统一按 LF 写（Windows 默认会把 \n 翻成 \r\n，导致每次同步都产生整文件 diff 噪音）。"""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def run(cmd, cwd=None, env=None, check=False):
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if check and r.returncode != 0:
        print(f"❌ 命令失败: {' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
        sys.exit(1)
    return r


def stage():
    """白名单聚合 + 脱敏。"""
    os.makedirs(DST, exist_ok=True)
    for f in ("config.yaml", "SOUL.md", "RESTART-NOTES.txt"):
        if os.path.exists(f"{SRC}/{f}"):
            shutil.copy2(f"{SRC}/{f}", f"{DST}/{f}")

    # 脚本：全部为自建，先清后拷 —— 但**绝不删**白名单与同步脚本自身。
    #（本机没有 SRC/scripts，克隆里那份就是唯一副本，照着老逻辑 rmtree 会永久丢失）
    s_dir, d_dir = os.path.join(SRC, "scripts"), os.path.join(DST, "scripts")
    if os.path.isdir(s_dir):
        os.makedirs(d_dir, exist_ok=True)
        for name in os.listdir(d_dir):
            if name == "custom-skills.json" or name.startswith("sync-hermes-config"):
                continue
            p = os.path.join(d_dir, name)
            shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
        shutil.copytree(s_dir, d_dir, ignore=IGNORE, dirs_exist_ok=True, symlinks=False)
    else:
        print(f"ℹ️  本机无 {s_dir}，保留克隆内 scripts/ 原样（白名单与同步脚本不受影响）")

    # 技能：**只备份用户自建技能**（白名单来自 list-custom-skills.py）。
    # Hermes 镜像自带的 196 个技能（/opt/hermes/skills + /opt/hermes/optional-skills）
    # 不备份 —— 升级/换镜像时自带，放进来只是 12MB 无用噪音。
    if os.path.exists(f"{DST}/skills"):
        shutil.rmtree(f"{DST}/skills")
    custom = json.load(open(WHITELIST, encoding="utf-8"))
    # 父目录已在白名单里的（如 quarkclouddrive 下的子技能）跳过，整目录拷贝即可，避免重复拷贝报错
    def _covered(rel, others):
        return any(rel != o and rel.startswith(o.rstrip("/") + "/") for o in others)
    top = [r for r in custom if not _covered(r, custom)]
    for rel in top:
        src = f"{SRC}/skills/{rel}"
        if not os.path.isdir(src):
            print(f"⚠️  白名单里的技能已不存在，跳过: {rel}")
            continue
        shutil.copytree(src, f"{DST}/skills/{rel}", ignore=_skill_ignore, symlinks=False)
    _write(f"{DST}/skills/README.md",
        "# 本目录只收录「用户自建技能」\n\n"
        "Hermes 镜像自带的技能（含可选库）不在备份范围内，升级或换镜像后自带。\n"
        f"白名单由 `/opt/data/scripts/list-custom-skills.py` 生成，当前 {len(custom)} 个：\n\n"
        + "".join(f"- `{c}`\n" for c in custom))

    os.makedirs(f"{DST}/memories", exist_ok=True)
    masked = 0
    for name in ("MEMORY.md", "USER.md"):
        p = f"{SRC}/memories/{name}"
        if not os.path.exists(p):
            continue
        t = open(p, encoding="utf-8").read()
        # 守卫：PAN_ACCOUNT 为空串时 str.replace("", X) 会把占位符插进**每个字符之间**，
        # 直接把记忆文件撑成几十倍垃圾（本机没有 .sync-mask.txt 就会踩到）
        if PAN_ACCOUNT:
            masked += t.count(PAN_ACCOUNT)
            t = t.replace(PAN_ACCOUNT, PLACEHOLDER)
        _write(f"{DST}/memories/{name}", t)
    note = f"脱敏账号名 {masked} 处" if PAN_ACCOUNT else f"未配置 {_mask}，无账号名需脱敏"
    print(f"① 聚合完成（{note}；自建技能 {len(custom)} 个，镜像自带技能已排除）")


def gen_env_example():
    keys = []
    for line in open(f"{SRC}/.env", encoding="utf-8", errors="ignore"):
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k:
                keys.append(k)
    cred = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|WEBHOOK|SID", re.I)
    out = ["# Hermes Agent 环境变量清单（只有变量名，值一律不备份）",
           "# 恢复时按需在 /opt/data/.env 里填回真实值", ""]
    out += [f"{k}=" + ("<在此填入真实值>" if cred.search(k) else "") for k in keys]
    _write(f"{DST}/.env.example", "\n".join(out) + "\n")
    print(f"② .env.example 已刷新（{len(keys)} 个变量名）")


def verify():
    """安全闸门：真实凭据值一律不得出现在备份目录里。"""
    real = []
    for line in open(f"{SRC}/.env", encoding="utf-8", errors="ignore"):
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if len(v) >= 12 and re.search(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD", k, re.I):
                real.append((k, v))
    pats = [(k, re.compile(re.escape(v))) for k, v in real]
    hits = []
    for root, dirs, names in os.walk(DST):
        dirs[:] = [d for d in dirs if d != ".git"]
        for n in names:
            p = os.path.join(root, n)
            try:
                t = open(p, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            for label, pat in pats:
                if pat.search(t):
                    hits.append((os.path.relpath(p, DST), label))
    if hits:
        print("❌ 安全复扫发现真实凭据泄漏，已中止：")
        for p, k in hits:
            print(f"   {p} ← {k}")
        sys.exit(1)

    # 第二道闸门：令牌字段 + 真实账号标识（userId / deviceId / 账号名），都不许出现在备份里
    token_pat = re.compile(r'"(?:access|refresh|client|bearer)[_A-Za-z]*[Tt]oken"\s*:\s*"[^"]{16,}"')
    sensitive = [("账号名", PAN_ACCOUNT)]
    cli_cfg = f"{SRC}/skills/quarkclouddrive/hermes/config.json"
    if os.path.exists(cli_cfg):
        try:
            cfg = json.load(open(cli_cfg, encoding="utf-8"))
            for k, v in cfg.items():
                if isinstance(v, str) and len(v) >= 16 and re.fullmatch(r"[0-9a-f]{16,}", v):
                    sensitive.append((k, v))          # deviceId / currentUserId
        except Exception:
            pass
    leaked = []
    for root, dirs, names in os.walk(DST):
        dirs[:] = [d for d in dirs if d != ".git"]
        for n in names:
            p = os.path.join(root, n)
            if n == ".env.example":
                continue
            try:
                t = open(p, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            rel = os.path.relpath(p, DST)
            if token_pat.search(t):
                leaked.append((rel, "令牌字段"))
                continue
            for label, val in sensitive:
                if val and val in t:
                    leaked.append((rel, label))
                    break
    if leaked:
        print("❌ 安全复扫（令牌/账号标识）命中，已中止：")
        for p, k in leaked:
            print(f"   {p} ← {k}")
        sys.exit(1)
    print(f"③ 安全复扫通过（{len(real)} 个真实凭据值 + 令牌/{len(sensitive)} 项账号标识，零命中）")


def publish():
    env = {k: v for k, v in os.environ.items() if k != "GIT_SSH_COMMAND"}  # 关键：解除注入
    run(["git", "add", "-A"], cwd=DST, check=True)
    st = run(["git", "status", "--porcelain"], cwd=DST, env=env)
    if not st.stdout.strip():
        # 注意：这里**不能 return** —— 上一次若提交成功而推送失败，
        # 本地就留下了未推送的提交，early-return 会让它永远推不上去。
        print("④ 内容无变化，跳过提交（下面仍尝试推送）")
    else:
        msg = "同步 Hermes 配置备份（脱敏后）"
        run(["git", "commit", "-q", "-m", msg], cwd=DST, env=env, check=True)
        print("④ 已提交:", run(["git", "log", "--oneline", "-1"], cwd=DST, env=env).stdout.strip())
    r = run(["git", "push", "origin", "main"], cwd=DST, env=env)
    out = (r.stdout + r.stderr).strip()
    print("⑤ 推送:", out.splitlines()[-1] if out else "完成")
    if r.returncode != 0:
        sys.exit(1)


if __name__ == "__main__":
    stage()
    gen_env_example()
    verify()
    if DRY:
        print("（--dry-run：未提交、未推送）")
    else:
        publish()
