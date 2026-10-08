# 第三方插件安装清单（本地仓库形式）

目标：把 GitHub 上的 nonebot 插件装成**本地仓库**（克隆进 `plugins/<包名>/`），并保证
①装之前知道它会不会外联、②装的时候不动现有包、③装完能加载、④触发范围是锁死的。

本用户已定的口径：**嵌套 `.git` 一律删掉**（不留在仓库里当 gitlink、也不正式收编进版本库），
删前把 commit 存档 —— 见末节「仓库卫生」。代价是没有 `git pull`，更新靠按 commit 对比上游重新下载。

## 1. 审计（静态，先不装不跑）

```bash
mkdir -p "$LOCALAPPDATA/hermes/cache/scratch/audit_x" && cd "$LOCALAPPDATA/hermes/cache/scratch/audit_x"
git clone --depth 1 <url> repo
find repo -type f -not -path "*/.git/*" | head -50      # 先看体量，几百个文件的要读 README/CONFIG 挑重点
```

在**读文件**层面扫（不要执行任何脚本）：

| 类别 | 正则 |
| --- | --- |
| 出站主机 | `https?://[^\s"'\)\],]+` |
| 危险调用 | `\b(eval\|exec\|os\.system\|subprocess\.\|Popen\|__import__\|pickle\.loads\|b64decode\|socket\.)\b` |
| 遥测痕迹 | `(?i)\b(telemetry\|analytics\|track(ing)?\|sentry\|posthog\|mixpanel)\b` |
| 凭据读取 | `(?i)(api[_-]?key\|token\|secret\|password\|webhook)` |

判读口径：

- 命中的主机**只有它自己的主页 / 文档 / 上游仓库链接** → 无外联。
- 主机里有第三方统计域名 → 展开上下文，看它上报哪些字段（查询文本、路径、设备指纹才是问题）。
- 命中 `eval`/`subprocess` 未必恶意，但要在报告里点出**它拿 shell 做什么**。
- 装到机器人里也要看**本地写什么**：SQLite 库路径、图片缓存目录、保留天数。插件记群消息内容
  属于要主动告知用户的事实（「只在本机、不上传，但它在记」）。

报告给用户时用表格：插件 / 功能 / 网络行为 / 依赖 / 需要他拍板的事项。

## 2. 依赖对账 + 「只新增不升级」

```bash
<Python313> -m pip list | grep -iE "<候选包名>"
<Python313> -m pip install --dry-run "<spec>" [更多 spec]
```

`--dry-run` 的 `Would install` 里既可能是真新增，也可能是**升级已有包**，要看名字跟已装列表对。
机器人环境里**绝不能被动升级**的：`starlette`、`fastapi`、`pydantic`、`uvicorn`（FastAPI 驱动）、
`Pillow`（画图插件）、`nonebot2` 本身。

发现冲突就用 `==当前版本` 把它钉住，让 resolver 退到兼容的旧版本依赖：

```bash
<Python313> -m pip install "fastmcp>=3,<5" "starlette==1.0.0"
```

实测效果：不钉 → 会装 fastmcp 4.x 并把 starlette 升到 1.7.0；钉住 → 选 fastmcp 3.4.0，41 个新包、
零升级，装完 `fastapi 0.135.2 / starlette 1.0.0 / pydantic 2.12.5 / uvicorn 0.42.0` 与装前一致。

MCP 系 SDK（`fastmcp` → `fastmcp-slim` + `mcp` + `jsonschema` + `cryptography` + `keyring` + `rich` …）
依赖树动辄 40 个包，**装之前先把数量告诉用户**，别装完才说。

## 3. 加载与自检

```bash
cd <仓库> && <Python313> <skills>/scripts/qa_load_plugins.py     # 与基线对比：多 N 条成功、失败仍为 0
```

加载失败的**真实异常**要单独抓（自检脚本只给一句概括）：写个几行脚本 `nonebot.init()` → 注册适配器 →
`load_plugin(<模块路径>)`，用 `traceback.print_exc()` 打全栈；`Failed to import` 的尾巴通常直接就是
结论（如 `ModuleNotFoundError: No module named 'mcp'`）。

要探插件内部对象（比如拿它的 API 客户端直接打一次后端）：**包 `__init__` 不一定把实例再导出**，
按真实模块路径 import 才靠得住 —— `plugins.<目录>.<包名>.core.<模块>` 里的模块级实例（如
`core/hermes_client.py` 末尾的 `hermes_client = HermesClient()`），`getattr` 包对象会 `AttributeError`。

## 4. 依赖 `nonebot-plugin-orm` 的插件（自带 `migrations/`）

- 配置键 `SQLALCHEMY_DATABASE_URL` 写在 `.env.dev`。用**绝对路径**的 SQLite，落到仓库 `data/` 下：
  `sqlite+aiosqlite:///C:/Users/Administrator/Desktop/nb2/my_nonebot2/data/orm.db`
  （相对路径按进程 cwd 解析，迁移脚本和 bot 的 cwd 一旦不同就会建出两个库）。落地后
  `git check-ignore -v data/orm.db` 确认被忽略（本仓库 `/data/` 已忽略）。
- ORM 的 `alembic_startup_check` 默认 True：**数据库没迁移到最新版时，它会在启动时 `click.confirm`
  询问是否更新**。机器人是后台进程、没有 TTY，这一步会 Abort → 插件起不来。所以**必须先建表**。
- 没有 `nb` CLI（本机没装 nb-cli）时用 `scripts/orm_migrate_plugin.py`：它复刻 ORM 启动时做的事
  （`init` → `AlembicConfig(stdout=..., cmd_opts=...)` → `cmd_opts.cmd=(migrate.upgrade,[],[])` →
  `greenlet_spawn(migrate.upgrade, cfg)`），但把交互确认去掉。
- ORM **不需要**仓库根有 `migrations/`：找不到就用内置 generic 模板，各插件的迁移目录按已加载插件
  自动发现（`files(plugin.module)/"migrations"`）。
- 验证建表成功：`sqlite_master` 里有该插件的表（如 `daily_messages`），`alembic_version` 里有版本号。

## 5. Hermes 桥接类插件（把 QQ 接到本机 Hermes agent）

这类插件（`nonebot-plugin-hermes`）的后端是**本机 Hermes 的 API Server**，装它要同时动两边：

| 位置 | 键 | 说明 |
| --- | --- | --- |
| `~/.hermes/.env`（Windows：`AppData\Local\hermes\.env`） | `API_SERVER_ENABLED=true`、`API_SERVER_PORT=8642`、`API_SERVER_KEY=<64 hex>` | Hermes 侧开关；8642 是默认端口 |
| 机器人 `.env.dev` | `HERMES_API_URL=http://127.0.0.1:8642`、`HERMES_API_KEY=<同一把 key>` | key 不一致 → 会话续接被拒 / 401 |

- 生成密钥：`python -c "import secrets; print(secrets.token_hex(32))"`，**写进两边文件、不进聊天**；
  报告里给长度 + 指纹（`sha256[:16]`）即可核对。
- 改完 Hermes 侧**要重启 Hermes 网关**才生效，而重启会**中断正在进行的 QQ 会话**（就是当前这个对话）
  → 必须等用户发话再做，并提前说明会断几秒。
- **重启动作要延迟发出，否则会把这一轮的回复一起带走**（网关进程就是跑本次会话的进程，前台执行
  `gateway restart` 会在回复送达前把自己杀掉）：写一个几行的 `.ps1`（`Start-Sleep -Seconds 25` 后调
  `<hermes>/bin/hermes.exe gateway restart`，输出重定向到 scratch 日志），再用
  `powershell -NoProfile -Command "Start-Process powershell.exe -ArgumentList '...','-File','<ps1>'"`
  岔出去执行，随后同轮把报告发完。重启结果**自己无法验证**（进程已被换掉），所以要明确说
  「你回一句我就去查」；事后看 `hermes gateway status`、`netstat -ano | grep :8642`、以及脚本自己的日志。
- 验证 API 服务本身：无 key `GET /v1/models` 应 401、带 key 应 200 且返回模型列表；对话测试要
  `POST /v1/chat/completions`，**body 写成 `.json` 文件用 `--data-binary @file`** —— 中文内容内联进 shell
  引号极易被转义弄坏，得到 `400 Invalid JSON in request body`，看着像服务端不认（其实是自己拼坏了）。
- 查 Hermes 侧默认值/键名，**优先 grep 本机源码树**（`~/AppData/Local/hermes/hermes-agent`，
  `grep -rn "API_SERVER_" --include=*.py`）。文档站常被本机代理劫持成假 IP，`web_extract` 会以
  「private or internal network address」拒收，翻源码比绕文档快。
- 触发/白名单键（语义都要在报告里说清）：
  * `HERMES_GROUP_TRIGGER` = `at` / `all` / `keyword`
  * `HERMES_ALLOW_GROUPS` —— **空 = 全部群允许**（危险）；用非真实群号（如 `["0"]`）当占位，
    等用户给真实群号再填
  * `HERMES_PRIVATE_TRIGGER=allowlist` + `HERMES_ALLOW_USERS=["<主人QQ>"]`
  * `HERMES_ADMIN_USERS=["onebotv11:<uid>"]`（adapter 名小写、去空格点；空集 = 敏感命令对所有人 deny）
- 反向 MCP 通道（`HERMES_MCP_ENABLED`）默认关；开启需要 fastmcp、在 Hermes 侧注册 MCP server，
  并把插件的 `SKILL.md` 装到 `~/.hermes/skills/`。**不要为了「把功能开全」顺手打开。**

## 6. 本机服务的网络坑：httpx 被系统代理劫持（空体 502）

机器上开了系统级代理时（注册表 `ProxyEnable=1` + `ProxyServer=127.0.0.1:7892`），**httpx 会把
`http://127.0.0.1:8642` 这类本机请求也丢给代理**，代理回一个空体 502。

为什么难查：curl **不读注册表**，同一时刻 curl 打同一地址是 200 → 很容易误判成「服务端间歇性故障」。

三条判据（任一命中就在代理方向查）：

| 判据 | 预期 |
| --- | --- |
| 502 响应头 | 只有 `connection: close` + `content-length: 0`，**没有 `Server` 头**（真服务端是 `Python/3.x aiohttp/…` 且带 JSON 错误体） |
| 服务端日志 | 查不到这次请求（请求根本没到） |
| `<Python313> -c "import httpx;print(httpx._utils.get_environment_proxies())"` | 打印出 `{'http://': 'http://127.0.0.1:7892', ...}` |

隔离手法（本机实测有效的顺序）：

1. **同一个进程里**先 httpx 打一次、再用 `subprocess` 调 curl 打一次 —— 两者结果不同就锁定客户端差异；
2. `httpx.AsyncClient(trust_env=False)` 再打一次，确认就是代理问题；
3. 不要靠逐个加请求头去试（UA / Accept-Encoding / Connection 全试一遍都是 200，白花时间）。

修法（改机器人自己的入口，不动 vendor 代码、不动系统设置）：

```python
# bot.py 顶部，必须在 import nonebot / httpx 之前
import os
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost,::1")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost,::1")
```

- 大小写两个都要设。设完 `get_environment_proxies()` 会变成 `{'all://127.0.0.1': None, 'all://localhost': None, ...}`：
  本机请求直连，外部请求（bilibili / 图床那些）照旧走代理。
- **不要把 `no_proxy` 写进 `.env.dev` 了事**：nonebot 读 .env 进的是 driver 配置，**不会导出到
  `os.environ`**，而 httpx 看的是环境变量 → 写在那里不生效。
- **不要**给 vendored 插件打 `trust_env=False` 补丁（升级会覆盖），**不要**去改系统代理的绕过列表
  （注册表里已写了 `127.*`，httpx 不吃这套，改了没用）。
- 改完**要重启机器人才生效**，报告时必须说明当前进程仍在跑旧代码。
- 自己写的探针脚本同样中招：在 `import httpx` 之前设 `NO_PROXY`，或者干脆用 curl。

## 7. 装完要交给用户的清单

1. 改了哪些文件（`bot.py` 追加了哪几行、`.env.dev` 加了哪些键、`.env` 加了什么）
2. 装了哪些包（几个新增 / 是否零升级）
3. 每个插件：怎么用、前提条件（群管理员权限、本机服务）、本地记什么数据、保留多久。
   **「怎么触发」要读代码取证，不能只看配置键名**：私聊/群聊分支（如 `if target.private:
   is_explicit_trigger = True` → 私聊直接发消息就触发，不需要 @）、白名单判定（`utils.py` 里
   `allowlist` + `allow_users`；`allow_groups` **为空是否等于放行**）、忽略前缀、以及
   `COMMAND_START=[""]` 意味着命令**不带斜杠也能触发**。
4. 还等他提供什么（群号、密钥、重启时机），以及重启会有什么可见影响
5. 仓库卫生（**用户已定：删掉嵌套 `.git`**）：先 `git ls-files -s | awk '$1=="160000"'` 确认父仓库没把它
   记成 gitlink，再把每个仓库的 `git rev-parse HEAD` + remote 写进 `data/third_party_plugins.json`，
   然后 `rm -rf <插件目录>/.git`，最后 `find plugins -maxdepth 3 -name .git` 应为 0。
   删后没有 `git pull`，报告里要写明「以后更新按 commit 重新下载」这个代价。
