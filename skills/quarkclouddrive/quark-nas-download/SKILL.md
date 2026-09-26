---
name: quark-nas-download
description: 把夸克网盘上的大文件下载到本机 UGREEN NAS 的共享目录，或把分享链接转存到网盘。攻克了夸克第三方 API 的 50MB 单文件下载上限（错误码 23018），改走「本机服务端转存 + OpenList 直链代理」两段式流程。转存默认「用完即删」（一次性动作，不留常驻任务），仅当用户明确说「保存成任务」时才加 --keep 保留。当用户说「把网盘里的XX下到NAS」「下载这个夸克文件到本地」「转存这个链接/保存到网盘」「夸克下载速度慢/失败」「23018」「50MB 限制」时使用。
---

# 夸克网盘 → 本机 NAS 大文件下载

## 触发场景
- 「把网盘里的《XXX》下载到 NAS / 本地 / 影视库」
- 「转存这个链接」「保存到网盘」「存到网盘的XX目录」
- 「夸克下载失败」「error 23018」「download file size limit」
- 「下载速度只有几十 KB」「不能下载大文件」
- 「把这个保存成任务」（→ 才加 `--keep`，否则一律用完即删）

## ✅ 标准作业流程（照抄即可）

```bash
# 每个 shell 会话先设好（见下方「本机环境」的网络变化说明）
export QAS_URL=http://172.17.0.1:5005 OPENLIST_URL=http://172.17.0.1:5445
PY=/opt/hermes/.venv/bin/python          # 系统 python3 没有 requests
```

```
① 找链接   → xiageba.py 搜索（见 skill `xiageba-search`；转存必须用「别人的」分享链接）
② 看一眼   → $PY scripts/qas.py share "<链接>"        确认里面有啥、多大
③ 转存     → $PY scripts/qas.py save "<链接>" "<网盘路径>"   ⭐ 默认自动删任务
④ 下载     → $PY scripts/qdl.py start / start-dir ...     ⭐ 异步，秒回，进度可查
⑤ 报进度   → $PY scripts/qdl.py status [job_id]
⑥ 校验     → qdl.py status 里看「校验」字段（字节数比对）
⑦ 解压     → 源是 zip 打包时，用 Python zipfile 解压后再核对集数
```

> 🗜️ **分享常把剧集打成 zip 防和谐**（如 4K 版 `01-12.zip` + `13-24.zip`）。
> NAS 上**没有 unzip**，用 Python：
> ```python
> import zipfile
> with zipfile.ZipFile(zp) as z:
>     for info in z.infolist():          # 流式解压，避免一次性读进内存
>         with z.open(info) as s, open(dst, "wb") as d:
>             while chunk := s.read(8*1024*1024): d.write(chunk)
> ```
> 中文文件名在 zip 里通常是 cp437：`info.filename.encode("cp437").decode("utf-8")`（失败再试 gbk）。
> 解压速度实测 ~28 秒/GB 级文件；21GB 的剧集约 11 分钟 —— **用后台任务 + notify_on_complete**，别阻塞对话。

> ⚡ **下载一律用 `qdl.py`（异步），不要再用 olist.py dl / dl-dir（同步阻塞）。**
> 同步下载会把 QQ 机器人的对话卡死十几分钟——用户明确要求优化掉这一点。

> 🔧 **想给别的长任务（转码/rsync/批量处理）也做异步 + 进度推送？**
> 通用手法（detached 子进程 + `@@PROGRESS@@` 哨兵行 + process_registry/gateway
> 补丁 + 哪些改动需要重启 gateway）已抽成独立 skill：
> **`hermes-async-job-progress`**（devops 分类）。新增长任务直接参考它，
> 骨架抄 `qdl.py` 即可。

---

## 🚀 异步下载（qdl.py）—— 默认用法

**核心**：`start` 立刻返回（<1 秒），下载在 setsid 子进程里跑，
Hermes 容器重启、会话重置都不影响；进度写 JSON，随时可查。

```bash
cd /opt/data/skills/quarkclouddrive

# 异步启动（秒回，返回 job_id）
python3 scripts/qdl.py start     "/电影/因果报应/xxx.mkv" "/volume1/共享影视作品/电影/因果报应"
python3 scripts/qdl.py start-dir "/电影/因果报应"        "/volume1/共享影视作品/电影/因果报应"

# 查进度
python3 scripts/qdl.py status              # 列全部作业 + 进度条 + 速度 + ETA
python3 scripts/qdl.py status 0926-0119    # 支持 id 前缀、也支持 label 关键词
python3 scripts/qdl.py watch  <job_id>     # 前台盯到完成（需要阻塞时才用）
python3 scripts/qdl.py cancel <job_id>     # 停止（已下载部分保留，可续传）
python3 scripts/qdl.py clean               # 清理已结束作业记录
```

输出示例：
```
⬇️ [0926-011929-494e] Maharaja.2024.1080p.mkv  —  downloading
   `████░░░░░░░░░░░░░░░░` 21.3%  1.18GB / 5.52GB
   速度 27.06MB/s   ETA 3分15秒   已跑 23秒
   → /volume1/共享影视作品/下载测试
```

### 与 Hermes 的进度联动
作业子进程会往 stdout 写两类控制行，Hermes 的 `process` 工具自动解析：
```
@@PROGRESS@@ {"label":"...","done":123,"total":456,"speed":...,"percent":3,"eta":"13分18秒"}
@@STATE@@    {"job_id":"...","kind":"file","dest":"...","state":"downloading"}
```
→ `process(action="poll"/"list")` 的结果里会多出 `progress` / `state` 字段
（含 `done_h`/`total_h`/`speed_h` 人类可读值）。
→ 开了 `notify_on_complete` 的后台任务，完成/进行中的通知会渲染成
`[⏳ 片名 3% · 6.87MB/s · ETA 13分18秒]` 而不是滚屏日志。

### 进度文件位置
```
/opt/data/cache/dljobs/<job_id>.json   进度 + 元数据（原子写）
/opt/data/cache/dljobs/<job_id>.log    子进程日志
/opt/data/cache/dljobs/<job_id>.pid    子进程 PID
```
容器重启后这些文件还在，`qdl.py status` 依然能报告历史作业。

> 🗑️ **铁律**：转存是一次性动作。`save` 跑完必须确认任务已删（`qas.py tasks` 应为空）。
> 用户没说「保存成任务」就绝不留常驻任务。

---

## 🚨 核心结论（先读这条，能省几小时）

**夸克对第三方 API 有 50MB 单文件下载硬上限**，返回：
```json
{"status":400,"code":23018,"message":"download file size limit[<fid>]"}
```

实测**全部绕不开**：
| 通道 | 结果 |
|---|---|
| 官方 CLI `qk download`（走 `/open/v1/file/get_download_url`） | ❌ 23018 |
| Web API `POST /1/clouddrive/file/download` + 真实 web cookie | ❌ 23018 |
| 域名 drive-pc / drive / pan 换着试 | ❌ 23018 |
| UA 伪装、参数变体、账号 SVIP+ (`Z_VIP`) | ❌ 23018 |
| 414KB 小文件 | ✅ 正常（证明只是**大小**策略） |

**但是「转存」（保存别人分享的文件到自己网盘）不受任何限制。**

所以正解是**两段式**：
```
分享链接 ──[转存]──> 自己的夸克网盘 ──[OpenList 直链代理]──> NAS 本地磁盘
```

---

## 本机环境（已就绪，开箱即用）

> 🚨 **2026-09-26 实测：容器网络已不再是 host 模式**
> `127.0.0.1:5005/5445` **连不通**了（curl 返回 000），要改走 **docker 网桥网关 `172.17.0.1`**。
> 脚本已支持环境变量覆盖，**每次调用前先导出**：
> ```bash
> export QAS_URL=http://172.17.0.1:5005
> export OPENLIST_URL=http://172.17.0.1:5445
> ```
> （`qas.py` 读 `QAS_URL`、`olist.py` 读 `OPENLIST_URL`；`qdl.py` import olist，所以也吃 `OPENLIST_URL`）
>
> ⚠️ **python 必须用 `/opt/hermes/.venv/bin/python`**：
> 系统 `python3`（3.13.5）**没有 requests**；hermes venv 有 requests 但**没有 ddddocr**
> → 搜索一律用 `xiageba.py`（无需验证码）；搜盘鸭 sopanya.py / pansou.py 已弃用并归档，不要调用。
>
> 一行自检：`curl -s -o /dev/null -w '%{http_code}\n' http://172.17.0.1:5005/login` → 期望 302

| 服务 | 地址 | 凭证 | 作用 |
|---|---|---|---|
| **QAS**（绿联「网盘工具」App） | `http://172.17.0.1:5005` | `admin` / `admin` | 转存分享链接到网盘 |
| **OpenList**（AList 分支） | `http://172.17.0.1:5445` | `admin` / `admin` | 已挂载夸克，提供直链下载 |
| Hermes 容器 | 桥接网络（实测容器 IP `172.17.0.4`） | — | ⚠️ **不能用 `127.0.0.1`**（返回 000）；宿主端口一律经网桥网关 `172.17.0.1` |

> ⚠️ 绿联 App 商店里那个「网盘工具」实际就是开源项目
> **quark-auto-save**（https://github.com/Cp0204/quark-auto-save）。
> 它不是绿色 App 独有的，所以它的 API/TUI 逻辑可查官方 wiki。

### 现成脚本（直接用，别重写）
```bash
cd /opt/data/skills/quarkclouddrive

# 1) ⭐ 转存（一步式：转存 → 立即执行 → 自动删任务）
python3 scripts/qas.py save "<分享链接>" "<网盘路径>" [任务名]   # 默认用完即删
python3 scripts/qas.py save "<分享链接>" "<网盘路径>" --keep     # 仅用户明确要"保存成任务"时
python3 scripts/qas.py share "<分享链接>"                       # 只解析看内容，不转存
python3 scripts/qas.py info                                     # 看账号/任务/token
python3 scripts/qas.py tasks                                    # 列任务（正常应为空）
python3 scripts/qas.py paths "/电影"                             # 列网盘目录

# 2) 下载（⭐ 默认用异步 qdl.py，见上节；下面 olist 是同步/查询用）
python3 scripts/olist.py login
python3 scripts/olist.py ls  "/电影/目标目录"                    # 列目录
python3 scripts/olist.py url "/电影/目标目录/文件.mkv"           # 取直链
python3 scripts/olist.py dl  "/电影/目标目录/文件.mkv" "/volume1/共享影视作品/电影/目标目录"   # ⚠️ 同步阻塞
python3 scripts/olist.py dl-dir "/电影/目标目录" "/volume1/共享影视作品/电影/目标目录"          # ⚠️ 同步阻塞

# 2b) 异步下载（推荐）
python3 scripts/qdl.py start     "/电影/目标目录/文件.mkv" "/volume1/共享影视作品/电影/目标目录"
python3 scripts/qdl.py start-dir "/电影/目标目录"          "/volume1/共享影视作品/电影/目标目录"
python3 scripts/qdl.py status
```

> 🗑️ **转存任务的生命周期（用户明确要求，务必遵守）**
> 转存 = **一次性动作**，不是常驻配置。`save` 默认「转存→执行→删任务」，
> 只留文件、不留定时追更任务。**只有用户明确说「保存成任务」才加 `--keep`。**
> 每次转存后都要确认 `qas.py tasks` 为空（或至少不含本次任务）。
> 旧的 `add` + `run` 两段式仍可用，但属于"保留常驻任务"的场景，别默认用。

### 一条龙示例（实测跑通 2026-09-26）
```bash
cd /opt/data/skills/quarkclouddrive
python3 scripts/qas.py save "https://pan.quark.cn/s/6d5b2411c83d" \
        "/电影/冒牌天神 (2003-2007)" "冒牌天神"
# → 📢 ✅ 转存成功 → 🗑️ 任务已自动清理
python3 scripts/qdl.py start-dir "/电影/冒牌天神 (2003-2007)" \
        "/volume1/共享影视作品/电影/冒牌天神 (2003-2007)"
# → 🚀 已启动下载作业 0926-xxxxxx（秒回，对话不阻塞）
python3 scripts/qdl.py status
# → ✅ done | 校验: ok | 1.53GB + 1.95GB，字节数完全匹配，~26MB/s
```

## 实测性能基线（参考范围，非承诺）
| 项目 | 实测范围 |
|---|---|
| `qdl.py start` 返回耗时 | 约 0.1–0.3 秒（无论文件多大） |
| 下载速度 | **约 11–40 兆/秒**，随网盘侧波动（单集会先快后慢） |
| 12.4GB 整剧耗时 | 约 5–10 分钟 |
| 字节校验 | ✅ 12.4GB 剧集 26 个文件逐一比对完全一致 |

> 速度与耗时都取决于夸克侧，别拿单次数值当承诺；并行下第二个任务会分摊带宽。

---

## 原理：为什么 OpenList 能绕过 50MB

OpenList 把夸克当**存储挂载**（通过 `driver=Quark` + 你的 web cookie），
取直链走的是 `/p/<urlencoded_path>?sign=...` 通道。
这条通道是 **OpenList 服务端做流式代理**，不是夸克给第三方的受限下载 API，所以：
- ✅ 无 50MB 限制（实测 2GB 文件正常）
- ✅ 支持 HTTP Range → 断点续传（`curl -C -`）
- ✅ 实测 ~26 MB/s
- ✅ 直链带 `sign`，有时效；过期重新 `/api/fs/get` 即可

---

## 关键 API 速查

### QAS（端口 5005）
| 接口 | 方法 | 说明 |
|---|---|---|
| `/login` | POST 表单 | 字段 `username`/`password`，成功 302 |
| `/data` | GET | 整体配置：`cookie`(夸克web cookie, ~2.3KB)、`api_token`、`tasklist`、`crontab`、`plugins` |
| `/api/add_task?token=<T>` | POST JSON | `{taskname, shareurl, savepath, pattern, replace}`，`taskname/shareurl/savepath` 必填 |
| `/run_script_now?token=<T>` | POST | 立即执行全部任务，返回 SSE 日志流 |
| `/get_share_detail` | POST JSON | `{shareurl}` → 分享内容列表（**验链用这个**） |
| `/get_savepath_detail?path=` | **GET** | 列网盘目录（POST 会 405） |
| `/update` | POST JSON | 覆盖式写回整体配置（改 tasklist 用） |
| `/task_suggestions` | GET | 猜测的转存路径建议 |

> **`api_token` 就在 `/data` 里**（本机为 `5bc5d58b60d55bfc`），
> 它由用户名密码派生，改密码前永不过期。文档：
> https://raw.githubusercontent.com/wiki/Cp0204/quark-auto-save/API接口.md

### OpenList（端口 5445）
| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/auth/login` | POST | `{username,password}` → `data.token` |
| `/api/fs/list` | POST | `{path,page,per_page,refresh}` → `data.content[]` |
| `/api/fs/get` | POST | `{path,refresh}` → `data.raw_url`（带 sign 直链） |
| `/api/admin/storage/list` | POST | 存储列表（看 driver/mount_path/status） |
| `/api/admin/storage/update` | POST | 改存储配置 |

---

## 😱 必踩的坑（都验证过了）

### 1. OpenList 路径 `/` 改成 `0` 才能列出夸克目录
**症状**：`/api/fs/list` 返回 `{"content":null,"total":0}`，
`/api/fs/get` 报 `failed get dir: object not found`。
**原因**：夸克驱动的 `addition.root_folder_id` 默认写成 `/`，但夸克根目录也要用字面量 `"0"`。
**修复**：
```bash
# 取配置 → 改 root_folder_id 为 "0" → 写回 → 重新加载
T=$(curl -s http://172.17.0.1:5445/api/auth/login -X POST \
    -H 'Content-Type: application/json' \
    -d '{"username":"admin","password":"admin"}' | python3 -c "import json,sys;print(json.load(sys.stdin)['data']['token'])")
curl -s "http://172.17.0.1:5445/api/admin/storage/get?id=1" -H "Authorization: $T" -o /tmp/st.json
python3 - <<'EOF'
import json
d=json.load(open('/tmp/st.json'))['data']
add=json.loads(d['addition']) if isinstance(d.get('addition'),str) else d['addition']
add['root_folder_id']='0'
d['addition']=json.dumps(add,ensure_ascii=False)
json.dump(d,open('/tmp/st_upd.json','w'),ensure_ascii=False)
EOF
curl -s "http://172.17.0.1:5445/api/admin/storage/update" -X POST \
     -H "Authorization: $T" -H 'Content-Type: application/json' -d @/tmp/st_upd.json
curl -s "http://172.17.0.1:5445/api/admin/storage/load_all" -X POST -H "Authorization: $T"
```
> 同理，夸克 API 的根目录 fid 也必须用字面量 `"0"`。

### 2. QAS 不能转存「自己创建的分享」
**症状**：`📢 ❌《任务》转存失败：用户禁止转存自己的分享`
这不是 bug，是夸克策略。**必须用别人的分享链接**（用 `xiageba.py` 去搜，见 skill `xiageba-search`）。

### 3. curl 的 Netscape cookie jar 会过滤掉 HttpOnly 行
**症状**：解析出空 cookie 字符串。
**原因**：curl 把 HttpOnly cookie 写成 `#HttpOnly_127.0.0.1\tFALSE\t/...`，**行首带 `#`**，
用 `startswith("#")` 过滤会误杀。
**修复**：`line.lstrip("#HttpOnly_")` 后再按 `\t` 切分（`qas.py` 已修好）。

### 4. 容器看不到主机文件系统 ≠ 网络不通
Hermes 容器跑在 **docker 桥接网络**：`/opt/hermes/docker-compose.yaml` 里是 `ports:` 端口映射，
**没有** `network_mode: host`；实测容器 IP `172.17.0.4`，`curl 127.0.0.1:5005` 返回 `000`。
宿主端口一律经**网桥网关 `172.17.0.1`** 访问（5005/5445/9443/8200 实测可达）。
容器只挂载了 `/volume1/共享影视作品`、`/volume1/共享相册`、`/opt/data`。
要操作主机 App，优先走 **HTTP API**，不必改 docker-compose、不必重启。

### 5. `qas.py paths` 偶发返回空 → 用 OpenList 交叉验证
**症状**：转存明明成功（`run` 日志显示 ✅），但 `qas.py paths "/电影/XXX"` 无输出。
**原因**：QAS 的 `get_savepath_detail` 有时对新建目录缓存/权限视图滞后。
**验证**：改用 OpenList 列目录，更权威：
```bash
python3 scripts/olist.py ls "/电影/测试目录"    # → 🎞️ 文件.mp4  1.90GB
```

### 6. 清理网盘测试残留用 OpenList 的 remove
```bash
python3 -c "
import sys; sys.path.insert(0,'/opt/data/skills/quarkclouddrive/scripts')
import olist
print(olist._api('/api/fs/remove', {'dir':'/电影','names':['测试目录']}))"
```

### 7. `save` 后必须验证任务真的消失了
`save` 内部虽已调用 `delete_task`，但**别盲信**——转存后一定要跑一次：
```bash
python3 scripts/qas.py tasks      # 期望输出：(无任务)
```
若仍有残留，手动 `python3 scripts/qas.py rm "<任务名>"`。

### 8. 别用 `terminal` 跑超长/带大量引号的命令
容易超时或被 shell 转义搞坏。用 `execute_code` + 写脚本文件到 `/tmp` 再执行。

**下载（>1GB）必须用 `qdl.py start` 异步**，别用 `olist.py dl`（同步会卡住对话）。
如果确实需要阻塞等完，用 `qdl.py watch <job_id>` 并给 `terminal` 足够的 timeout。

### 9. qdl.py 的坑（新脚本，2026-09 实测）
* **curl `-w` 只在结束时输出**，所以进度不是读 curl 输出，而是**每 0.5s 轮询目标文件大小**算出来的。文件名含空格/中文没问题（走 `subprocess` 列表传参，不经过 shell）。
* `@@PROGRESS@@` 是 stdout 控制行 → 用 `grep` 看日志时记得过滤：`grep -v '@@' *.log`。
* 作业 JSON 每 ~1 秒（或整数百分比变化时）写一次，`status` 读的是快照，不是实时字节。
* 直链过期：`download_one` 失败后会 `get_url` 重取一次再续传；仍失败才标 `failed`。
* `start-dir` 会**预先递归展开**所有文件（含 size），所以启动前会花几秒列目录——但这不影响"秒回"。
* 校验逻辑：`total_bytes` 来自 OpenList 的 size，`delete`/`move` 之后 size 会变，别拿旧 job 的 total 当基准。
* **改了 `process_registry.py` / `gateway/run.py` 后必须重启 gateway 才生效**
  （源码是启动时加载的）；但改 `config.yaml` 里的 approvals 不用重启。
  本机 gateway 是容器 PID 1，重启前先确认 `qdl.py status` 没有正在跑的作业
  ——作业本身是 detached 子进程不会死，但进度推送会断几秒。

---

### 10. 🚨 报「下载完了吗」之前必须核对**源端总集数**（血的教训）

**错误示范（真实犯过）**：看本地 `电视剧/水浒传 (2011)/` 目录里最大是 EP33，
就回答「33/33 全集齐全、已下完」。**实际网盘源有 86 集，只下了 33 集，漏了 53 集。**

根因：把「本地已有的最大序号」误当成「总集数」。本地文件数 = 已下载数，
**不等于** 源端应有的总数；只有源端才定义了「完整」。

**正确流程**（报告进度/完整性前，必须三步都做）：

```bash
cd /opt/data/skills/quarkclouddrive

# ① 源端真实清单（权威）
python3 scripts/olist.py ls "/电视剧/水浒传 (2011)" | head -1     # → 📁 ... (86 项)
python3 scripts/olist.py ls "/电视剧/水浒传 (2011)" | grep -c '🎞️'  # → 文件总数

# ② 本地实际清单
ls -1 "/volume1/共享影视作品/电视剧/水浒传 (2011)"/*.mkv | wc -l

# ③ 逐集比对，算出缺失集号（别只看数量，要看缺哪几集）
```

判定规则：
* 源端 N 项、本地 N 个、字节数全对 → 才可说「完整」。
* 本地少于源端 → **必须报告缺哪些集 + 是否仍有下载在跑**，不得说「下完了」。
* 有 `.fg.ed` / `.fg.op` / `.part` 等临时文件 → 说明有过中断，重点核查。
* 顺带确认 `qdl.py status` 与 `ps aux | grep curl`，判断是「下完了」还是「中断了」。

> 延伸：源端目录名常带年份（`水浒传 (2011)`），不同年份版本集数不同
> （2011 版 86 集 / 1998 央视版 43 集）。**以 olist.py 列出的真实项数为准，别靠记忆或印象。**

---

### 11. 分享缺集 → 补单集（实测流程）

分享常缺集（例：《深情眼》源端只有 `01–24 + 26.mp4`，缺 25）。
不要重新下整包，只补那一集：

```bash
# ① 找「全集齐」的源，并用 get_tree 确认目标集文件名真存在（见 skill xiageba-search）
#    例：《深情眼》→ 源 pan.quark.cn/s/5f10d30f7883 里有 Deep.Affection.Eyes.S01E25....mp4
# ② 转存该源到**临时目录**（QAS 整包转存，秒级，不下载）
$PY scripts/qas.py save "<全集源链接>" "/电视剧/_tmp_补集" "补集"
# ③ 只拉目标那一集
$PY scripts/qdl.py start "/电视剧/_tmp_补集/<目标集文件>" "<本地目标目录>"
# ④ 改名为与本地一致的编号（用 python os.rename，别用 shell —— 文件名常带 emoji/中文）
# ⑤ 删掉网盘临时目录，别留几十 G 垃圾
python3 -c "import sys;sys.path.insert(0,'/opt/data/skills/quarkclouddrive/scripts');import olist;print(olist._api('/api/fs/remove',{'dir':'/电视剧','names':['_tmp_补集']}))"
# ⑥ $PY scripts/qas.py tasks   → 应为空
```

补来的集若来自不同画质源（如 4K vs 1080p），**要在汇报里提醒用户**体积/清晰度不一致。

### 12. ⚠️ 别用 `qdl.py watch` 盯进度 —— 它会提前误判退出

实测 `qdl.py watch <job_id>` 会在下载**中途**打印 `done (进程已结束)` 并 `exit 0`，
同时 curl 仍在正常下载（作业状态其实是 `downloading`）—— 只看它的输出会误报「下载完了」。

改用按**逐文件字节比对**的看门狗（本机已有，2026-09 实测可靠）：

```bash
python3 /opt/data/skills/quarkclouddrive/scripts/watch_dl.py /opt/data/cache/dljobs/<job_id>.json 20 90
# 后台跑 + notify_on_complete；每轮输出中文进度 + @@PROGRESS@@ 控制行
# 只有「每个文件本地 size == 作业 JSON 里的 size」才报 ✅ 收工
```

另外 `qdl.py status` 的百分比曾冲到几百 %（旧版拿「当前文件」当分母），**2026-09-26 已修**：
现在目录作业的 `total_bytes` 恒为「所有文件之和」，只有单文件作业才用自身大小。
curl 名称以 `.` 开头的 `.fg.ed` / `.fg.op` 文件是下载残留，全部完成后可安全删除（`rm -f .*.fg.ed .*.fg.op`）。

## 排查清单

```bash
# 服务活着吗
curl -s -o /dev/null -w '%{http_code}\n' http://172.17.0.1:5005/login    # 期望 200
curl -s -o /dev/null -w '%{http_code}\n' http://172.17.0.1:5445/api/public/settings  # 期望 200

# QAS 登录态 + token
cd /opt/data/skills/quarkclouddrive && python3 scripts/qas.py info

# OpenList 存储是否 work
python3 -c "
import json,subprocess
t=json.loads(subprocess.run(['curl','-s','http://172.17.0.1:5445/api/auth/login','-X','POST','-H','Content-Type: application/json','-d','{\"username\":\"admin\",\"password\":\"admin\"}'],capture_output=True,text=True).stdout)['data']['token']
r=json.loads(subprocess.run(['curl','-s','http://172.17.0.1:5445/api/admin/storage/list','-H','Authorization: '+t],capture_output=True,text=True).stdout)
[print(i['id'],i['driver'],i['mount_path'],i['status']) for i in r['data']['content']]"

# 下载后校验字节数（对比 /api/fs/get 的 size）
python3 -c "
import json,subprocess,os
t=open('/tmp/.olist_token').read().strip()
p='/电影/目标/文件.mkv'
d=json.loads(subprocess.run(['curl','-s','http://172.17.0.1:5445/api/fs/get','-X','POST','-H','Authorization: '+t,'-H','Content-Type: application/json','-d',json.dumps({'path':p,'refresh':True})],capture_output=True,text=True).stdout)['data']
print('期望',d['size'],'实际',os.path.getsize('/volume1/共享影视作品/.../文件.mkv'))"
```

## 备选（本机存在但未打通）
| 服务 | 端口 | 状态 |
|---|---|---|
| aria2 RPC | 6800 | ⚠️ 2026-09-26 实测 `172.17.0.1:6800` **完全不通**（返回 000），此前记录的「在线但 Unauthorized」已失效 |
| OpenList 的 `aria2` 插件 | — | 可配 `auto_download` 让 QAS 转存后自动推给 aria2，需填 aria2 secret |

> 目前 **OpenList 直链 + curl** 已能满足需求，不必碰 aria2。

## 主机其他服务（经 `172.17.0.1` 访问，勿误伤）

2026-09-26 实测可达性（`curl http://172.17.0.1:<port>/`）：

| 端口 | 服务 | 实测 |
|---|---|---|
| 5005 | QAS（网盘工具） | ✅ 200 |
| 5445 | OpenList | ✅ 200 |
| 9443 | UGOS Pro 系统 UI | ✅ 400（可达） |
| 8200 | MiniDLNA | ✅ 200 |
| 6800 | aria2 | ❌ 000（不通） |
| 8395 | Syncthing | ❌ 000（不通） |
