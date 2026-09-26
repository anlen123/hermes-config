#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync-hermes-config.py — 把本机 Hermes 的配置/记忆/脚本/技能同步到 GitHub 备份仓库

用法:
    python3 <repo>/scripts/sync-hermes-config.py            # 聚合 → 脱敏 → 复扫 → 提交 → 推送
    python3 <repo>/scripts/sync-hermes-config.py --dry-run  # 只做到复扫，不提交不推送

仓库目录约定（多台机器共用一个仓库）
------------------------------------
    hermes-config/
    ├── scripts/sync-hermes-config.py      ← 共用工具，全仓库只此一份（各机都跑它，别各留副本）
    └── hosts/<host>/                      ← 每台机器一个目录，互相不覆盖
        ├── config.yaml  SOUL.md  RESTART-NOTES.txt  .env.example
        └── memories/  skills/  scripts/   ← 该机自己的记忆、自建技能、运行时脚本

设计要点
--------
* **每机一目录**：config.yaml / SOUL.md / memories 这类「单一文件」以前会互相覆盖，
  分到 hosts/<host>/ 后各机互不干扰。
* **机器标识必须固定**：读**不备份**的 <SRC>/.sync-host 或环境变量 HERMES_HOST，缺失即报错退出。
  不用 hostname 自动推断 —— 容器里 hostname 常是随机 ID，会凭空造出 hosts/<乱码>/ 目录。
* **只收白名单路径**，不做「把 <SRC> 整个变成仓库」——那样迟早误传 .env / sessions / 日志。
* **推送前强制安全复扫**：拿 <SRC>/.env 里的真实值反查备份目录，命中即中止。
* git 推送必须 `env -u GIT_SSH_COMMAND`：NAS 上该环境变量被注入了
  `GIT_SSH_COMMAND='ssh -o PubkeyAuthentication=no'`（禁掉公钥认证），
  它的优先级**高于** core.sshCommand，只能在调用时解除。
* **技能备份可整机退出**：存在**不备份**控制文件 `<SRC>/.sync-no-skills`（或加 `--no-skills`）时，
  本机完全不碰自己目录里的 skills/ —— 适合「这台机器不要某类技能、但别处还在用」。

未备份（有意为之）: .env / auth.json / sessions / logs / cache / checkpoints / platforms
"""
import json
import os
import re
import shutil
import subprocess
import sys


def _detect_src():
    """<SRC> 由脚本自身位置反推（仓库位于 <SRC>/hermes-config/ 下）。"""
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

# ── 机器标识（必须固定，见模块 docstring）─────────────────────────────
HOST_FILE = os.path.join(SRC, ".sync-host")
HOST = os.environ.get("HERMES_HOST", "").strip()
if not HOST and os.path.exists(HOST_FILE):
    HOST = open(HOST_FILE, encoding="utf-8").read().strip()
if not HOST:
    raise SystemExit(
        "❌ 未固定机器标识，拒绝执行（避免凭空造出 hosts/<random>/）。\n"
        f"   请创建这个**不备份**的文件：{HOST_FILE}\n"
        "   内容一行，例如：  ugreen-nas   或   windows-server-2019\n"
        "   （或设环境变量 HERMES_HOST=…）")
HOST = re.sub(r"[^A-Za-z0-9._-]", "-", HOST).strip("-")
HDST = os.path.join(DST, "hosts", HOST)        # 本机专属目录

# 自建技能白名单（由 list-custom-skills.py 生成）：优先 <SRC>/scripts/，其次本机目录内那份
WHITELIST = next((p for p in (os.path.join(SRC, "scripts", "custom-skills.json"),
                              os.path.join(HDST, "scripts", "custom-skills.json"))
                  if os.path.exists(p)), "")
PAN_ACCOUNT = ""                      # 脱敏目标：网盘账号名。从**不备份**的 <SRC>/.sync-mask.txt 读取，
                                      # 避免脚本自身把真实账号名带进公开仓库
_mask = os.path.join(SRC, ".sync-mask.txt")
if os.path.exists(_mask):
    PAN_ACCOUNT = open(_mask, encoding="utf-8").read().strip()
NO_SKILLS_FILE = os.path.join(SRC, ".sync-no-skills")
NO_SKILLS = os.path.exists(NO_SKILLS_FILE) or "--no-skills" in sys.argv
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
    """统一按 LF 写（Windows 默认会把 \\n 翻成 \\r\\n，导致每次同步都产生整文件 diff 噪音）。"""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def run(cmd, cwd=None, env=None, check=False):
    # 必须显式指定 encoding：Windows 上 text=True 会按系统区域编码（中文机器是 GBK）解码，
    # 而 git 输出是 UTF-8（提交信息含中文）→ reader 线程 UnicodeDecodeError 被静默吞掉，
    # 结果 stdout 变成 None，下游 .strip() 就炸 AttributeError。
    try:
        r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True,
                           encoding="utf-8", errors="replace")
    except FileNotFoundError:
        print(f"❌ 找不到命令 {cmd[0]}（本机没装或不在 PATH 里）")
        sys.exit(1)
    r.stdout, r.stderr = r.stdout or "", r.stderr or ""
    if check and r.returncode != 0:
        print(f"❌ 命令失败: {' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
        sys.exit(1)
    return r


def warn_legacy():
    """根目录残留旧布局 = 还有机器在跑旧版脚本，提醒去更新那台机器。"""
    old = [f for f in ("config.yaml", "SOUL.md", "memories", "skills",
                       ".env.example", "RESTART-NOTES.txt")
           if os.path.exists(os.path.join(DST, f))]
    if old:
        print(f"⚠️  仓库根目录仍有旧布局内容 {old}\n"
              f"    → 说明另一台机器还在跑**旧版**同步脚本，请把它换成仓库里的 "
              f"scripts/sync-hermes-config.py，否则它每跑一次都会重新写回根目录。")


def stage():
    """白名单聚合 + 脱敏，全部写进 hosts/<HOST>/。"""
    os.makedirs(HDST, exist_ok=True)
    for f in ("config.yaml", "SOUL.md", "RESTART-NOTES.txt"):
        if os.path.exists(f"{SRC}/{f}"):
            shutil.copy2(f"{SRC}/{f}", f"{HDST}/{f}")

    # 该机运行时脚本：先清后拷 —— 但**绝不删**白名单与同步脚本自身
    #（有的机器没有 <SRC>/scripts，仓库里那份就是唯一副本）
    s_dir, d_dir = os.path.join(SRC, "scripts"), os.path.join(HDST, "scripts")
    if os.path.isdir(s_dir):
        os.makedirs(d_dir, exist_ok=True)
        for name in os.listdir(d_dir):
            if name == "custom-skills.json" or name.startswith("sync-hermes-config"):
                continue
            p = os.path.join(d_dir, name)
            shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
        shutil.copytree(s_dir, d_dir, ignore=IGNORE, dirs_exist_ok=True, symlinks=False)
    else:
        print(f"ℹ️  本机无 {s_dir}，保留 {HOST}/scripts/ 原样")

    # 技能：**只备份本机自建技能**（白名单来自 list-custom-skills.py）。
    # Hermes 镜像自带的技能（/opt/hermes/skills + optional-skills）不备份 ——
    # 升级/换镜像时自带，放进来只是无用噪音。
    custom = []
    if NO_SKILLS:
        print(f"ℹ️  本机不参与技能备份（{NO_SKILLS_FILE} 存在），{HOST}/skills/ 保持原样")
    elif not WHITELIST:
        print(f"⚠️  找不到自建技能白名单（{SRC}/scripts/custom-skills.json），本次不更新 skills/")
    else:
        custom = json.load(open(WHITELIST, encoding="utf-8"))
        if os.path.exists(f"{HDST}/skills"):
            shutil.rmtree(f"{HDST}/skills")

        # 父目录已在白名单里的（如 quarkclouddrive 下的子技能）跳过，整目录拷贝即可
        def _covered(rel, others):
            return any(rel != o and rel.startswith(o.rstrip("/") + "/") for o in others)
        for rel in [r for r in custom if not _covered(r, custom)]:
            src = f"{SRC}/skills/{rel}"
            if not os.path.isdir(src):
                print(f"⚠️  白名单里的技能已不存在，跳过: {rel}")
                continue
            shutil.copytree(src, f"{HDST}/skills/{rel}", ignore=_skill_ignore, symlinks=False)
        _write(f"{HDST}/skills/README.md",
            f"# {HOST} 的自建技能\n\n"
            "只收录**该机用户自建**的技能；Hermes 镜像自带的技能（含可选库）不备份，"
            "升级或换镜像后自带。\n"
            f"白名单由 `scripts/list-custom-skills.py` 生成，当前 {len(custom)} 个：\n\n"
            + "".join(f"- `{c}`\n" for c in custom))

    os.makedirs(f"{HDST}/memories", exist_ok=True)
    masked = 0
    for name in ("MEMORY.md", "USER.md"):
        p = f"{SRC}/memories/{name}"
        if not os.path.exists(p):
            continue
        t = open(p, encoding="utf-8").read()
        # 守卫：PAN_ACCOUNT 为空串时 str.replace("", X) 会把占位符插进**每个字符之间**
        if PAN_ACCOUNT:
            masked += t.count(PAN_ACCOUNT)
            t = t.replace(PAN_ACCOUNT, PLACEHOLDER)
        _write(f"{HDST}/memories/{name}", t)
    note = f"脱敏账号名 {masked} 处" if PAN_ACCOUNT else f"未配置 {_mask}，无账号名需脱敏"
    print(f"① 聚合完成 → hosts/{HOST}/（{note}；自建技能 {len(custom)} 个）")


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
           f"# 机器: {HOST}    恢复时按需在 <SRC>/.env 里填回真实值", ""]
    out += [f"{k}=" + ("<在此填入真实值>" if cred.search(k) else "") for k in keys]
    _write(f"{HDST}/.env.example", "\n".join(out) + "\n")
    print(f"② .env.example 已刷新（{len(keys)} 个变量名）")


def verify():
    """安全闸门：真实凭据值一律不得出现在备份目录里。扫描**整个仓库**（含根目录残留）。"""
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
        msg = f"同步 Hermes 配置备份（{HOST}，脱敏后）"
        run(["git", "commit", "-q", "-m", msg], cwd=DST, env=env, check=True)
        print("④ 已提交:", run(["git", "log", "--oneline", "-1"], cwd=DST, env=env).stdout.strip())
    r = run(["git", "push", "origin", "main"], cwd=DST, env=env)
    out = (r.stdout + r.stderr).strip()
    print("⑤ 推送:", out.splitlines()[-1] if out else "完成")
    if r.returncode != 0:
        sys.exit(1)


if __name__ == "__main__":
    print(f"→ 机器标识: {HOST}   （备份目录 hosts/{HOST}/）")
    warn_legacy()
    stage()
    gen_env_example()
    verify()
    if DRY:
        print("（--dry-run：未提交、未推送）")
    else:
        publish()
