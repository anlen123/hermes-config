---
name: xiageba-search
description: 按关键词搜网盘资源（xiageba），解出可转存分享链接。
version: 1.1.0
author: Hermes
license: MIT
metadata:
  hermes:
    tags: [quark, xiageba, search, pan]
    related_skills: [quark-nas-download, quark-saveas-autoclassify]
---

# 全盘搜 (xiageba) 关键词搜网盘资源

## When to Use（触发场景）
- 用户按**关键词**找网盘资源（没给链接）：「帮我找《XXX》」「下载XX到NAS」
- 需要**可直接转存**的分享链接（夸克优先）

> 🚫 **只用 xiageba**。用户 2026-09-26 明确要求：不再使用搜盘鸭（sopanya）。
> `sopanya.py` / `pansou.py` / `sp_vis.py`（过码用）已归档到本技能
> `references/legacy-sopanya/`，**不要调用**（sopanya 要过图形验证码、慢、夸克条目还常解不出）。

## 主命令

```bash
cd /opt/data/skills/quarkclouddrive
PY=/opt/hermes/.venv/bin/python          # 系统 python3 没有 requests，必须用这个

# 直接搜（默认 --smart 自动收窄，这一步通常就够了）
$PY scripts/xiageba.py "深情眼" --pan quark --limit 10

# 常用变体
$PY scripts/xiageba.py "深情眼" --pan quark --pages 3      # 多翻几页，应对长尾
$PY scripts/xiageba.py "深情眼" --pan quark --strict        # 只要标题命中的
$PY scripts/xiageba.py "深情眼" --no-smart --json           # 关自动收窄 / 拿结构化数据
```

输出：`★ 标题 || 真实链接 || 网盘类型`（★ = 标题精确命中关键词）

| 参数 | 说明 |
|---|---|
| `--pan {all,quark,baidu,uc,xunlei,ali}` | 只看某网盘（默认 all） |
| `--pages N` | 每组合关键词翻 N 页（默认 1） |
| `--no-smart` | 关闭自动关键词收窄（默认开） |
| `--strict` | 只输出标题命中结果 |
| `--limit N` | 最多解析前 N 条链接（默认 10） |
| `--json` | JSON 输出，便于脚本处理 |
| `--exact` / `--page-size` | ⚠️ 服务端未实现/忽略，加了没用 |

## ⚠️ 头号坑：站内是「分词模糊匹配」，不是标题包含匹配

**症状**：搜「深情眼」返回 1000 条《深情诱引》《他深情侵入》《迟来深情皆是刃》……
真正的《深情眼》一条都看不到 → 极易误判成「站内没这个资源」。

**根因**：服务端把关键词拆成词元做 AND 模糊匹配 + 按热度/时间排序，
热门长尾（大量短剧）把精确结果挤到几十页之外；`exact=true` 服务端没实现，
`pageSize` 也被忽略（恒返回 10 条/页）。

**解法：多词收窄**（实测一击命中）

| 查询 | 返回条数 | 效果 |
|---|---|---|
| `深情眼` | 1000（泛） | ❌ 精确结果被淹没 |
| `深情眼 2025` | 22 | ✅ 命中《深情眼 (2025)》夸克源 |
| `深情眼 4K` | 16 | ✅ 命中【国剧】深情眼 4K【26集全】 |

`--smart`（默认开）会自动用 `[今年, 去年, 全集, 完结, 4K, 国语中字, 电视剧]` 补后缀重搜，
找到精确命中即停，等价于人工换词，通常一次调用就能搜出来。

**手动换词优先级**：`片名 + 年份` > `片名 + 4K/全集/完结` > `主演名` > 单纯片名。

### 🎯 选源铁律：认「全 N 集 / 完结」，别碰「更新至 X 集」

实测《深情眼》同时存在两类源：

| 标题写法 | 含义 |
|---|---|
| 【国剧】深情眼 4K【26集全】 / 全26集完结 | ✅ **完整**，可整包下 |
| 深情眼（2025）4K 更新至21集 | ⚠️ **追更中的残源**，只到 21 集 |

判断顺序：① 标题含「全 N 集／全集／完结」→ 优先；② 用 `get_tree` 数一遍真实文件数
（例：树里 `S01E01..S01E26` 齐 = 真全集）；③ 标题写「更新至」的一律当残源，别下。
**报「完整」之前必须走 ②，并且拿 `olist.py ls` 的源端项数跟本地比**（踩过 86 集只下 33 集报"全集"的坑）。

## 搜到之后：先看目录树再转存

```bash
# 看这个分享里到底有什么（能看见真实文件名/集数，转存前必做）
$PY - <<'EOF'
import sys; sys.path.insert(0,'/opt/data/skills/quarkclouddrive/scripts')
import xiageba as X
s = X.make_session()
t = X.get_tree(s, "<id>")          # id 从搜索结果拿（--json 里有）
print(t)
EOF
```

实测有用：`【国剧】深情眼 4K【26集全】` 的树里是 `Deep.Affection.Eyes.S01E01..E26.2160p.WEB-DL.H265`，
一眼就能确认是**真正的 26 集全集**，而不是被和谐缺集的版本。

## 完整链路：搜索 → 转存 → 下载到 NAS

```bash
cd /opt/data/skills/quarkclouddrive
export QAS_URL=http://172.17.0.1:5005 OPENLIST_URL=http://172.17.0.1:5445

$PY scripts/xiageba.py "深情眼" --pan quark --limit 6      # ① 找链接
$PY scripts/qas.py share "<夸克链接>"                       # ② 侦察：几个文件、多大
$PY scripts/qas.py save  "<夸克链接>" "/电视剧/片名 (2025)"  # ③ 转存（默认用完即删）
$PY scripts/qdl.py start-dir "/电视剧/片名 (2025)" "/volume1/共享影视作品/电视剧/片名 (2025)"  # ④ 异步下载
```

详见 skill `quark-nas-download`（转存/下载的全部坑在那）。

## 其他要点
- **结果里的 `type` 字段只能参考**，判定网盘类型以 URL 域名为准（接口偶有标错）。
- 转存**必须用别人的分享链接**，夸克禁止转存自己的分享。
- **报「下载完了吗」前必须核对源端清单**：`olist.py ls "<网盘目录>"` 的真实项数才是全集数，
  本地最大序号 ≠ 总集数（踩过坑：86 集的剧只下了 33 集却报"全集"）。分享常缺集
  （如 `01.mp4~24.mp4 + 26.mp4`，缺 25），也可能把两集并成一个 2GB 大文件 —— 要如实告知。
- 有些条目的 `geturl` 会返回空（分享已失效），跳过即可，别卡在一条上。
- 站点偶发 `HTTP 429` 限流 → 等 1~2 分钟重试。

## 备用同类站点（xiageba 搜不到时）
| 站点 | 状态 | 备注 |
|---|---|---|
| sopanya.com（搜盘鸭） | 🚫 已弃用 | 要过图形验证码；夸克条目常解不出 |
| upyunso.com（UP云搜） | 可用 | 接口 AES 加密，取链接需登录 token |
| xiongdipan.com（爱盘搜） | 可访问 | 未细测 |
