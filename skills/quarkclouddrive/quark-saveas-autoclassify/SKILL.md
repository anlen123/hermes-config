---
name: quark-saveas-autoclassify
description: 夸克网盘「转存分享链接 + 自动归类」工作流。当用户甩来一个夸克分享链接并要求「保存到网盘」「转存并分类到网盘」「存到对应分类里」时使用。先用 share-detail 侦察内容，再 saveas 转存，然后移动到网盘已有的分类体系（直接位于网盘根目录顶层的 动漫|电视剧|电影|综艺|游戏|漫画|有声小说|照片|工具|其他）下，最后清理残留并校验。
metadata:
  canonicalSkillId: 'quark-saveas-autoclassify'
  openclaw:
    emoji: "🗂️"
    requires:
      bins: ["node"]
---

# 夸克网盘：转存分享链接 + 自动归类

## 触发条件

**入口 A — 用户直接甩链接**，并带有任一意图：
- 「保存到网盘里」
- 「转存并且你自己分类到网盘里面」
- 「存到对应分类」
- 「帮我归档」

**入口 B — 用户只说想看某内容，没给链接**（「我想看XXX，帮我找到资源保存到夸克」）
→ 先走 skill `xiageba-search` 拿到候选链接，**再用本 skill 归档**。入口 B 的选源规则见下节。

## ⚠️ 本机环境前置（必读）

**所有 CLI 调用必须走包装脚本 `./scripts/qk`**，不要直接调 `node scripts/quark-drive.cjs`。

原因：CLI 有 agent 环境检测，缺少 `HERMES_INTERACTIVE` / `HERMES_SESSION_ID` 时所有命令返回 `-104 无法识别当前 Agent 环境`。包装脚本已自动注入。

```bash
cd /opt/data/skills/quarkclouddrive && ./scripts/qk <command> [options]
```

若 `./scripts/qk` 不存在，先按 skill `quarkclouddrive-nas-env-fix` 重建。

**公共参数**：所有命令都要带 `--session-input "<用户原始提问逐字>"` 和 `--session-id "<同一个>"`（格式 `{unix秒}-{6位随机}`）。

## 网盘已有分类体系（关键：先侦察，别乱建）

**实测快照（2026-09-24）**：分类目录**直接挂在网盘根目录顶层**，`browse`（不带 `--parent-fid`）即可看到。
**注意**：已**不存在**「来自：分享」这层包裹目录（`search --keyword "来自：分享"` 返回 total=0）。若上游文档提到该层，属历史结构，以实测为准。

**用户明确要求（2026-09-24）**：保存 电视剧/综艺/动漫 时**直接落到根目录下的分类目录里**，不要建任何中间包裹层（如「来自：分享」）。根目录 = 分类目录，`/综艺/地球超新鲜 (2026)/…`。

```
/（网盘根，fid = "0"）
├── 动漫        ← 按剧集建子目录
├── 电视剧      ← 按剧名建子目录
├── 电影        ← 按片名建子目录
├── 综艺        ← 按节目建子目录
├── 游戏        ← 按游戏名建子目录
├── 漫画        ← 按作品建子目录
├── 有声小说
├── 照片
├── 工具        （可能为空）
├── 其他        ← 通用回退分类
└── 我的备份    ← category=20，系统目录，**绝对不要动**
```

**实测目录快照（2026-09-24，读取方式见「全盘目录树」节）**：

| 分类 | 已有子目录 |
|---|---|
| 动漫 | 剑来2、吞噬星空、整合包（先保存，防和谐）(2)、石纪元 第四季 Part3（13集全）、🧵🗡️奇🦐💎(2022)=【诛仙】 |
| 电视剧 | X 仙剑奇侠传 (2005) 全34集【2026最新优酷4K超高清修复】【170.5G】、[爱情公寓][全5季][4K][附番外篇+大电影]、大明王朝1566、武林外传 (2006)、苦尽柑来遇见你 |
| 电影 | L 鹿鼎记 (1992) 1080P蓝光压制、周星驰合集、因果报应、环太平洋 Pacific Rim (2013)、环太平洋2：雷霆再起(2018) 4K HDR、冒牌天神2 Evan Almighty (2007) |
| 综艺 | 地球超新鲜 (2026)、怦然心动20岁：冬季 (2026) |
| 游戏 | 双人成行 PC、塞尔达传说：王国之泪 1.4.3全DLC（PC+安卓）…、明末、歧路旅人0;八方旅人0、王泪公主 |

> 该快照仅作命名风格参考，**每次任务仍要实测**（目录随时会变，FID 一定会变）。

### 分类判定规则（按此顺序）

1. 有明确体裁 → 动漫 / 电视剧 / 电影 / 综艺 / 漫画 / 有声小说 / 照片 / 游戏
2. 软件、工具、教程、杂项 → 工具；**工具放不下或不确定** → 其他
3. 完全无法判断 → 其他（并在汇报里说明分类依据不确定，请用户确认）

### ⚠️ 侦察规则（必做）

**每次任务仍要实测确认**，不要硬编码 FID：
- 顶层分类：`browse --page-size 100`（不带 `--parent-fid`）→ 解析 `file_type=="0"` 的目录
- 子目录：`browse --parent-fid "<分类 fid>" --page-size 100`

FID 会随目录重建而变，且很长易抄错，**全程用 Python 变量传递，不要手抄**。

## 入口 B：从搜索结果选源（实测流程 2026-09）

用户没给链接时，先 `xiageba.py "<关键词>"` 搜（默认 --smart 自动收窄），再**逐个 `share-detail` 侦察后比较**，不要看到第一个夸克链接就转存。

**选源判据（按优先级）**：
1. `share_info.all_file_num`（文件总数）× `share_info.size`（总大小）——**越大越完整**
2. 是否含全集（看能否找到完整期数/集数序列）
3. 是否含多版本（4K 子目录 / 1080p 各一份）
4. 只取夸克链接（`pan.quark.cn`），跳过百度/UC/阿里（除非用户指定）

**实测样例**（搜「怦然心动20岁」，3 个夸克链接）：

| 链接 | 标题 | all_file_num | size | 结论 |
|---|---|---|---|---|
| `4ed33b69f550` | 怦然心动20岁：冬季 | **133** | **67 GB** | ✅ 采用（含全集+加更+特辑+4K目录） |
| `d79732b2c3cc` | P怦R心D20岁：DJ | 67 | 27 GB | 内容较少 |
| `7632ed645f0d` | P【忄平!@然!#心!&云力!@20!#岁】(2) | 22 | 13 GB | 只有部分期数 |

> **搜索结果的标题常被打码**（`忄平丨肰`、`P怦R心D`、`!@然!#心` 之类），无法凭标题判断内容，**必须靠 `all_file_num` / `size` / 展开子目录来评估**。同理：标题打码不影响转存，转存下来的目录名也是打码的，用户网盘里看到的会是原名（分享方自己的命名）。

**汇报时**告诉用户你比较了几个源、为什么选这个（附对比表），用户很在意这个。

### ⚠️ 入口 B 的断点：目标只有非夸克源

`saveas` **只能转存夸克分享链接**。若搜出来用户要的内容只有百度/UC/阿里/迅雷源：

- **不要静默跳过、不要假装能存、也不要拿别的片子凑数**。
- 如实列对比表（标题 / 网盘 / 链接 / 能否转存），给出选项：
  **A.** 只存有夸克源的部分；**B.** 用户自己补夸克链接；**C.** 换搜索源再找（见 skill `xiageba-search` 的搜索词技巧）。
- 判定网盘以 **URL 域名**为准（`pan.quark.cn` = 夸克，`pan.baidu.com`/`drive.uc.cn` = 不可转存），搜索结果里的类型字段常标错。

## 执行步骤

### Step 1 — 侦察分享内容

```bash
cd /opt/data/skills/quarkclouddrive
./scripts/qk share-detail --url "<分享链接>" --size 50 --session-input "<原提问>" --session-id "<sid>"
```

若链接失效会返回 `41012 该链接已被用户取消分享` → **直接告知用户换链接，不要重试**。

输出里拿到：顶层条目名、大小、文件列表。若是文件夹需继续看子目录：

```bash
./scripts/qk share-detail --url "<链接>" --pdir-fid "<子目录 fid>" --size 50 ...
```

> 注意：文件列表在 stdout 的 `type:"list"` 行里，`data.files` 数组。用 Python 解析 JSON 行提取 `filename` / `dir` 更省 token（CLI 输出很啰嗦）。

### Step 2 — 拿最新分类目录 FID

不再需要搜「来自：分享」（该层已不存在）。直接列根目录：

```bash
# 顶层分类目录 + FID（⚠️ 根目录 fid 是字面量 "0"，必须显式传）
cd /opt/data/skills/quarkclouddrive
./scripts/qk browse --parent-fid "0" --page-size 100 --session-input "<原提问>" --session-id "<sid>"
```

用 Python 解析出 `file_type == "0"` 的目录及其 `fid`，得到类别名 → FID 映射：

```python
import json, subprocess, time, random
SKILL = '/opt/data/skills/quarkclouddrive'
sid = f"{int(time.time())}-{random.randint(0,999999):06d}"

def qk(*args):
    cmd = ['bash','-c', f'cd {SKILL} && timeout 180 ./scripts/qk ' + ' '.join(f'"{a}"' for a in args)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=220)
    out = []
    for l in r.stdout.strip().split('\n'):
        l = l.strip()
        if l.startswith('{'):
            try: out.append(json.loads(l))
            except: pass
    return out

root = qk('browse','--page-size','100','--session-input','<原提问>','--session-id',sid)
cat = {o['data']['filename']: o['data']['fid']
       for o in root if 'filename' in o.get('data',{}) and o['data'].get('file_type')=='0'}
# cat == {'动漫': '~1...', '电视剧': '~1...', ...}
```

> **注意**：顶层分类目录的 `parent_fid` 各不相同（都是网盘顶层），不要用 parent_fid 去反推结构。

### Step 3 — 转存（saveas）

**关键坑：`--to-pdir-path` 参数会被 CLI 忽略**，文件会落到网盘默认转存位置而不是你指定的路径。所以**直接裸转存**，然后自己 move：

```bash
./scripts/qk saveas --url "<分享链接>" --session-input "<原提问>" --session-id "<sid>"
```

返回 `data.save_path` 与 `data.save_as.to_pdir_fid`（实际落点目录 FID）。

**`saveas` 返回里几个有用的字段（实测）**：
- `save_as.save_as_sum_num` —— **本次转存的文件总数**（用它做后续校验基准，比数列表可靠）
- `save_as.remain_capacity` —— 剩余容量（字节），转存前可确认空间够不够
- `save_as.to_pdir_fid` —— 落点目录 FID（下一步 `browse` 用）
- `save_as.save_as_top_fids[0]` —— 顶层落点 FID，实测**常与 `to_pdir_fid` 指向同一处**
- `save_path` —— 显示为「来自：分享」，这是**平台内部路径标签，不代表网盘真有一层叫这名字的目录**（实际目录结构见上文实测快照）

> 转存任务异步，实测大文件集也基本秒完（`status:2` 即完成）。返回 `status` 非 2 时等待重查。

### Step 4 — 确定最终归档路径

判断内容属于哪一类，落到 `/<类别>/` 下（根目录顶层，无「来自：分享」前缀）：

- 先 `browse` 目标类别目录，看是否已有**同节目/同系列的目录**（比如综艺里可能已有该节目的其他季）。同系列 → 移动进已有目录；没有 → 在类别目录下 `create-folder` 新建以节目名命名的目录。
- 新建目录：
  ```bash
  ./scripts/qk create-folder --dir-path "<节目名>" --parent-fid "<类别目录 fid>" ...
  ```
  **务必传 `--parent-fid`**，否则会建到平台默认目录而不是你想要的位置。
- **综艺类特例（实测反复出现）**：该目录下可能直接散落着某节目的 mp4（没有节目子目录，2026-09 时就有 24 个）。此时**主动在综艺下新建以节目名命名的子目录**再放进去，不要继续往根下堆散文件。
- **归档目录命名**：保持用户现有风格（带年份/季数），如 `武林外传 (2006)`、`环太平洋 Pacific Rim (2013)`、`JOJO奇妙冒险1-8`、`大明王朝1566`、`怦然心动20岁：冬季 (2026)`。

### Step 5 — 移动文件到位（关键步骤）

转存后文件通常散落在转存落点目录里，**必须**用 `move` 归位。

> **实测坑（2026-09）**：`data.save_as.to_pdir_fid` 有时**就等于某个分类目录本身的 FID**。此时 `browse` 该 FID 会同时列出你刚转存的文件**和该目录原有的全部子项**。移动时必须**只挑刚转存的那几个 fid**（用转存返回的 fid 列表，或对比转存前后的差异），绝不能把整个目录列表都 move 进去——否则会把已有子目录搬走，破坏原有结构。另外不要对该目录执行「清空残留」逻辑。

**批量移动**（move 单次最多 100 个）：优先用 `saveas` 返回值里的 fid，若没有则 browse 后**按 filename 精确匹配**筛出刚转存的条目：

```python
# 用 Python 驱动，避免 --all artifact 的额外开销
import json, subprocess
SKILL = '/opt/data/skills/quarkclouddrive'
NEW = ['<刚转存的文件名1>', '<文件名2>']   # 来自 saveas/share-detail 输出

r = subprocess.run(['bash','-c',
    f'cd {SKILL} && timeout 180 ./scripts/qk browse --parent-fid "<落点fid>" --page-size 100 '
    f'--session-input "<原提问>" --session-id "<sid>"'],
    capture_output=True, text=True, timeout=220)
fids = []
for l in r.stdout.strip().split('\n'):
    if not l.startswith('{'): continue
    try: d = json.loads(l)['data']
    except: continue
    if d.get('filename') in NEW:
        fids.append(d['fid'])
args = ' '.join(f'"{f}"' for f in fids)
subprocess.run(['bash','-c',
    f'cd {SKILL} && timeout 180 ./scripts/qk move {args} --target-fid "<目标目录fid>" '
    f'--session-input "<原提问>" --session-id "<sid>"'], timeout=220)
```

**分页坑**：用 `search` 拿 fid 会受分页限制（只返回一页）。**列目录 fid 一律用 `browse`**，它一次性返回该目录全部直接子项（配 `--page-size 100`）。

**批量移动（实测 69 项可一次跑完，但要分批）**：`move` 单次上限 **100**，稳妥按 **50 一批**循环，每批独立调用：

```python
B = 50
for i in range(0, len(fids), B):
    batch = fids[i:i+B]
    args = ' '.join(f'"{x}"' for x in batch)
    r = subprocess.run(['bash','-c',
        f'cd {SKILL} && timeout 180 ./scripts/qk move {args} --target-fid "{TFID}" '
        f'--session-input "{PROMPT}" --session-id "{sid}"'],
        capture_output=True, text=True, timeout=220)
    # 成功的响应是单行 {"code":0,...,"data":{"fids":[...]}} —— 一批只回一行，
    # ⚠️ 不要用「统计 code:0 的行数」来断言成功条数（会误判为只成功 1 项）。
    # 校验一律放到最后用 browse 点数。
```

> **校验断言的正确做法**：批量 move 的返回值**不是一行一项**，所以别靠它计数。移完后统一 `browse` 落点（期望 0 项）和 `browse` 目标目录（期望 == `save_as_sum_num`）来验收。

**含子目录的转存**：本次转存的文件夹里可能自带子目录（如 `4K-`、`新建文件夹`）。`move` 直接传子目录的 fid 即可整体搬走，**不需要**递归处理其内部文件。

### Step 6 — 重命名打码目录（可选但推荐）

分享方常把标题/文件夹名**打码**（如 `llljiunnnbbaiduuuuu`、`【P】忄平丨肰`），转存后用户网盘里就是这串乱码。建议重命名成有意义的名字。

**`rename` 的 items-file 格式（实测 2026-09，这两个字段缺一不可）**：

```json
{
  "schema_version": 1,
  "batch_id": "<16位十六进制，随机生成即可>",
  "items": [
    {"fid": "<要改名的条目 fid>", "old_name": "<当前名>", "new_name": "<新名>"}
  ]
}
```

```python
batch_id = '%016x' % random.getrandbits(64)
items = {"schema_version": 1, "batch_id": batch_id,
         "items": [{"fid": inner_fid, "old_name": inner_name, "new_name": "灵魂摆渡AI剧 全24集"}]}
json.dump(items, open('/tmp/rename.json','w'), ensure_ascii=False)
qk('rename','--items-file','/tmp/rename.json','--session-input',PROMPT,'--session-id',sid)
# 成功返回 result_status: "SUCCESS"，并生成 record_file / batch_id（撤销用 rename-revert --batch-id）
```

> **试错记录**（别重走弯路）：`schema_version` 必填且必须为 `1`（缺失报 `UNSUPPORTED_SCHEMA`）；`items` 必须是**顶层数组**，不要套在 `operations.request_items` 里（会报 `INVALID_FIELD_TYPE`）；`old_name` 必须与当前实际名完全一致。
> **撤销**：`rename-revert --batch-id <batch_id>`（batch_id 从成功返回里拿）。

### Step 7 — 清理与校验

1. 删掉新建但没用上的空目录（若 CLI 无 delete 命令则跳过，告知用户）
2. **校验原位置已无本次转存文件**（不要断言整个目录为空——可能还有其他内容）：
   ```bash
   cd /opt/data/skills/quarkclouddrive
   ./scripts/qk browse --parent-fid "<原落点fid>" --page-size 100 | grep -c '<刚转存文件名关键词>'
   # 期望 0
   ```
3. **校验目标目录条目数**是否等于预期文件数
4. 向用户汇报：文件名、文件数、总大小、最终路径

## 子流程：整理散落文件到节目子目录（实测 2026-09-24）

用户说「XX 目录里的视频是乱的，帮我建文件夹移进去」时走这条。

**背景**：往分类目录里 saveas 散文件时，文件会直接摊在分类目录根下（如综艺里 24 个 mp4），没有节目子目录。需要事后归拢。

**步骤**：

1. `browse` 目标分类目录（`--parent-fid <分类fid>`），**只挑 `file_type=="1"` 的文件**，列出全部候选。
2. **先和用户确认内容归属**——散文件常混多个节目（实测综艺里 24 个 mp4 全是《地球超新鲜》，但包含 2 个 6 月的「回顾特辑」容易误判成别的节目）。把文件名列表给用户确认。
3. `create-folder --dir-path "<节目名> (<年份>)" --parent-fid "<分类fid>"` 建子目录，**记下返回的 TFID**。
4. `move` 全部文件 fid → `--target-fid <TFID>`（≤100/次，稳妥 50 一批）。
5. **校验**：分类目录下 `file_type=="1"` 的数量应为 **0**；新子目录内文件数应等于移动数。

```python
# 关键是过滤：只搬文件，绝不搬已有目录
items = [o['data'] for o in qk('browse','--parent-fid',zfid,'--page-size','100',
         '--session-input',PROMPT,'--session-id',sid) if 'filename' in o.get('data',{})]
fids = [x['fid'] for x in items if x.get('file_type') == '1']   # ← 只取文件
r = qk('move', *fids, '--target-fid', TFID, '--session-input',PROMPT,'--session-id',sid)
# 校验
left = [x for x in qk_browse(zfid) if x.get('file_type')=='1']
inside = qk_browse(TFID)
assert len(left) == 0 and len(inside) == len(fids)
```

> **用户偏好**：今后往 电视剧/综艺/动漫 转存时，**保存阶段就直接落到「分类/节目名 (年份)/」子目录**，不要先在分类根摊散文件再整理。saveas 后立刻 create-folder + move，一步到位。

## 常用脚手架：一次性导出全盘目录树

需要快速了解结构时直接跑这段（已实测可用，全程用变量传 FID）：

```python
import json, subprocess, time, random
SKILL = '/opt/data/skills/quarkclouddrive'
sid = f"{int(time.time())}-{random.randint(0,999999):06d}"
PROMPT = '<原提问>'

def qk(*args, timeout=180):
    cmd = ['bash','-c', f'cd {SKILL} && timeout {timeout} ./scripts/qk ' + ' '.join(f'"{a}"' for a in args)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout+40)
    out = []
    for l in r.stdout.strip().split('\n'):
        l = l.strip()
        if l.startswith('{'):
            try: out.append(json.loads(l))
            except: pass
    return out

def children(fid, page='100'):
    """列目录全部直接子项。⚠️ 根目录必须显式传 fid='0'（不传会走到默认行为，实测拿不到列表）。
    ⚠️ `--all` 模式返回的是**单个**对象 {data:{total:N, file_list:[...]}}，
       而普通模式是**每个子项一行** {data:{filename:...}}。两种都要兼容。"""
    res = {}
    for o in qk('browse','--parent-fid',fid,'--all','--session-input',PROMPT,'--session-id',sid):
        d = o.get('data') or {}
        fl = d.get('file_list')
        if isinstance(fl, list):
            for it in fl: res[it['filename']] = it
        elif 'filename' in d:
            res[d['filename']] = d
    return res

top = children('0')                   # 根目录（fid 固定为 "0"）
for name, meta in top.items():
    if meta.get('file_type') != '0': continue
    print(f"=== {name} ({meta.get('category')}) ===")
    for n, d in children(meta['fid']).items():
        print(f"  {'📁' if d.get('file_type')=='0' else '📄'} {n}")
```

> **实测坑**：根目录 `fid` 是字面量 `"0"`。传空字符串或省略 `--parent-fid` 时**不会**返回根列表（会返回别的默认内容），必须显式 `--parent-fid "0"`。
> 单次全盘遍历约 60–90 秒（每个目录一次 CLI 调用）。只查需要的分类即可，不必全跑。

## 汇报格式

用表格 + emoji，包含：分享链接、内容名、文件数、大小、最终位置、分类依据、坑点提醒。

**入口 B（自己搜的）额外要报**：候选源对比表（文件数/大小/结论）+ 选择理由。用户明确在意「你比较了几个、为什么选这个」。

## 常见错误

| 现象 | 原因 | 处理 |
|---|---|---|
| `-104 无法识别当前 Agent 环境` | 没用 `./scripts/qk` 包装脚本 | 改用包装脚本 |
| `error: unknown command 'ls'` | CLI 无 `ls` 命令 | 列目录用 `browse` |
| `search --keyword "来自：分享"` 返回 total=0 | 该层包裹目录已不存在 | 分类目录直接在根目录顶层，用裸 `browse` |
| `41012 该链接已被用户取消分享` | 分享失效 | 告知用户换链接，不重试 |
| 文件没进指定目录 | `--to-pdir-path` 被忽略 | 裸转存 + 手动 `move` |
| `move` 只移动了部分文件 | 用了 `search` 的 fid（受分页） | 改用 `browse` 列全量 fid |
| `move` 误搬了已有目录 | 把落点目录全部子项都移动了 | 只按 filename 精确匹配筛出本次转存项 |
| 新建目录位置不对 | `create-folder` 漏传 `--parent-fid` | 显式传父目录 FID |
| FID 抄错 | FID 含 `\|` 且极长 | 全程用脚本/Python 传递，不手抄 |
| 搜索结果解析不到 `data.filename` | `search` 的 stdout 结构是 `file_list` | 从 `artifact` 行的 jsonl 文件读，或改用 `browse` |
| 批量 move 返回只有 1 行 `code:0` | 一批全部 fid 的回执是**单行**，含 `data.fids` 数组 | 正常现象，别误判；用 `browse` 点数校验 |
| 搜索到的标题是乱码/打码（`忄平丨肰`） | 分享方为防和谐用了谐音异体字 | 不影响转存，靠 `all_file_num`/`size` 判优劣 |
| 搜不到想要的内容（或只有非夸克源） | 搜索源对老片/冷门片收录不全（实测《冒牌天神1 (2003)》只有百度源，夸克源无收录） | 如实列对比表给用户选：A 只存有夸克源的部分 / B 用户补夸克链接 / C 换搜索源再试。**不要拿别的片子凑数、不要假装能存** |
| 转存目录名带年份/季数不匹配 | 分享方命名与内容季数可能不符 | 归档时按**实际内容**重命名子目录（如 `怦然心动20岁：冬季 (2026)`） |
| `rename` 报 `UNSUPPORTED_SCHEMA` | items-file 缺 `schema_version` | 加上 `"schema_version": 1` |
| `rename` 报 `INVALID_FIELD_TYPE` | `items` 被套进了 `operations` 对象 | `items` 必须是顶层数组 |
| **落点目录里混有用户原有目录** | 分享内容落在与旧内容相同的父目录 | ⚠️ **只能按 filename 精确匹配本次转存项**，见 Step 5；曾实测落点里有用户原有的「怦然心动20岁」目录，全靠精确匹配才没被误搬 |
| 内容无法辨识、不知归哪类 | 分享标题+文件名全打码（如 `llljiunnnbbaiduuuuu` + `01.mp4~24.mp4`） | **先问用户**「这是什么内容」，不要猜着归类；附上文件数/大小/命名规律供用户判断 |
| `browse --parent-fid ""` 拿不到根目录列表 | 根 fid 是字面量 `"0"`，传空串会走默认行为 | 显式 `--parent-fid "0"` |
| `--all` 模式解析不到 `data.filename` | `--all` 返回**单个**对象 `{data:{total,file_list:[...]}}`，非一行一项 | 从 `data.file_list` 取值（见脚手架 `children()`） |
| 整理散落文件时误搬了节目目录 | 没过滤 `file_type` | 只取 `file_type=="1"` 的文件，目录另建 |
