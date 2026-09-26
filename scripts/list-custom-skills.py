"""生成「用户自建技能」白名单 → /opt/data/scripts/custom-skills.json

判定规则（两条都基于可核验的证据，不靠记忆）：
  1. 技能名不出现在镜像自带目录里（/opt/hermes/skills、/opt/hermes/optional-skills）
     —— 只要镜像里有，就属于 Hermes 自带的 196 个技能之一；
  2. 且 SKILL.md 的修改时间晚于镜像安装时间点（2026-09-23 23:30）
     —— 排除那些"镜像可选库里没有、但属于上一版发行包"的存量技能。

已经被镜像收录、只是本机版本不同的（上游版本漂移）也一律不算用户自建。
"""
import os, re, time, json, sys

CUT = time.mktime(time.strptime('2026-09-23 23:30', '%Y-%m-%d %H:%M'))
ROOT = '/opt/data/skills'
IMG_DIRS = ('/opt/hermes/skills', '/opt/hermes/optional-skills')
OUT = '/opt/data/scripts/custom-skills.json'


def name_of(p):
    t = open(p, encoding='utf-8', errors='ignore').read(2000)
    m = re.search(r'^name:\s*(.+)$', t, re.M)
    return m.group(1).strip().strip('"\'') if m else os.path.basename(os.path.dirname(p))


def image_names():
    names = set()
    for base in IMG_DIRS:
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            if 'SKILL.md' in files:
                names.add(name_of(os.path.join(root, 'SKILL.md')))
    return names


def local_skills():
    out = {}
    for root, dirs, files in os.walk(ROOT):
        if 'SKILL.md' in files:
            p = os.path.join(root, 'SKILL.md')
            out[os.path.relpath(root, ROOT)] = (name_of(p), os.path.getmtime(p))
    return out


def main():
    img = image_names()
    custom = sorted(rel for rel, (n, mt) in local_skills().items()
                    if n not in img and mt > CUT)
    json.dump(custom, open(OUT, 'w'), ensure_ascii=False, indent=1)
    print(f"镜像自带技能名 {len(img)} 个；本机技能 {len(local_skills())} 个")
    print(f"用户自建 {len(custom)} 个 → {OUT}")
    for c in custom:
        print("   +", c)
    return 0


if __name__ == '__main__':
    sys.exit(main())
