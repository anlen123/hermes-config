"""生成「用户自建技能」白名单 → <HERMES_HOME>/scripts/custom-skills.json

（Windows/本机版；NAS 版见 hosts/ugreen-nas/scripts/list-custom-skills.py，两份判定规则一致）

判定规则（两条都基于可核验的证据，不靠记忆）：
  1. 技能名不出现在 Hermes 自带库里（hermes-agent/skills、hermes-agent/optional-skills）
     —— 只要自带库里有，就属于随发行包附带的技能；
  2. 且 SKILL.md 的修改时间晚于本机安装时间点（默认 2026-09-26 00:00）
     —— 排除「自带库里没有、但属于上一版发行包」的存量技能。

已经被自带库收录、只是本机版本不同的（上游版本漂移）也一律不算用户自建。

用法：
    python list-custom-skills.py              # 写入 <HERMES_HOME>/scripts/custom-skills.json
    python list-custom-skills.py --show       # 只打印，不写文件
"""
import json
import os
import re
import sys
import time


def detect_home():
    """HERMES_HOME：优先环境变量，其次由本脚本位置反推（脚本位于 <HOME>/scripts/）。"""
    for p in (os.environ.get("HERMES_HOME"), os.environ.get("HERMES_SRC"),
              os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
        if p and os.path.isdir(os.path.join(p, "skills")):
            return os.path.normpath(p)
    sys.exit("❌ 找不到 Hermes 主目录（需含 skills/），请设 HERMES_HOME")


HOME = detect_home()
ROOT = os.path.join(HOME, "skills")
IMG_DIRS = (os.path.join(HOME, "hermes-agent", "skills"),
            os.path.join(HOME, "hermes-agent", "optional-skills"))
OUT = os.path.join(HOME, "scripts", "custom-skills.json")
# 安装时间点：早于此时间的 SKILL.md 视为随发行包自带（本机装于 2026-09-26）
CUT = time.mktime(time.strptime(os.environ.get("CUSTOM_SKILLS_CUT", "2026-09-26 00:00"),
                                "%Y-%m-%d %H:%M"))


def name_of(p):
    t = open(p, encoding="utf-8", errors="ignore").read(2000)
    m = re.search(r"^name:\s*(.+)$", t, re.M)
    return m.group(1).strip().strip("\"'") if m else os.path.basename(os.path.dirname(p))


def image_names():
    names = set()
    for base in IMG_DIRS:
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            if "SKILL.md" in files:
                names.add(name_of(os.path.join(root, "SKILL.md")))
    return names


def local_skills():
    out = {}
    for root, dirs, files in os.walk(ROOT):
        if "SKILL.md" in files:
            p = os.path.join(root, "SKILL.md")
            out[os.path.relpath(root, ROOT).replace("\\", "/")] = (name_of(p), os.path.getmtime(p))
    return out


if __name__ == "__main__":
    img = image_names()
    loc = local_skills()
    custom = sorted(rel for rel, (n, mt) in loc.items() if n not in img and mt > CUT)
    print(f"自带库技能名 {len(img)} 个；本机技能 {len(loc)} 个")
    print(f"用户自建 {len(custom)} 个 → {OUT}")
    for c in custom:
        print("   +", c)
    if "--show" not in sys.argv:
        json.dump(custom, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("已写入白名单（同步脚本会据此备份 <HOME>/skills/ 下这些目录）")
