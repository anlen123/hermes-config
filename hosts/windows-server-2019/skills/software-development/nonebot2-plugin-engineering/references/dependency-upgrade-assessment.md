# 评估 nonebot 本体 / 依赖升级（只读取证，不擅自装）

触发语：「我用的是哪个版本 / 有没有新版 / 升级行不行 / 有没有不兼容」。
产出是**证据表**（版本现状 + 兼容性 + 回退方案），装不装让用户点头。

## 1. 本机装了什么

```python
import importlib.metadata as md
for p in ["nonebot2", "nonebot-adapter-onebot", "nonebot-plugin-orm", "nonebot-plugin-localstore",
          "nonebot-plugin-apscheduler", "nonebot-plugin-waiter", "nonebot-plugin-session",
          "nonebot-plugin-alconna", "pydantic", "starlette", "fastapi", "httpx", "aiohttp"]:
    try:
        print(p, "==", md.version(p))
    except md.PackageNotFoundError:
        print(p, ": 未安装")
```

`nonebot.__version__` 也能读，但 `importlib.metadata` 对没装成包的名字会明确报「未安装」，更适合列表式输出。

## 2. 最新版与发布时间

```python
import json, urllib.request
d = json.load(urllib.request.urlopen("https://pypi.org/pypi/nonebot2/json"))
print("最新:", d["info"]["version"])
for v in ("2.4.4", "2.5.0"):
    files = d["releases"].get(v) or []
    if files:
        print(v, "发布时间:", files[0]["upload_time"])
```

直连 PyPI 不稳时脚本里带重试；发布日期要写进报告 —— 「已发布半年」和「上周刚发」是两个不同的风险档位。

## 3. 影响面：只会动那一个包吗

```bash
<python> -m pip install --dry-run "nonebot2==2.5.0"
```

读最后那几行：出现 `Would install nonebot2-2.5.0` 之外的名字（pydantic / starlette / fastapi / uvicorn）
就要警惕 —— 那些是机器人 FastAPI 驱动在用的包。要「只新增不升级」就按 `references/third-party-plugin-install.md`
的办法把已装版本一起钉住。

## 4. 生态约束逐条核

```python
for p in ["nonebot-adapter-onebot", "nonebot-plugin-orm", ...]:
    print(p, [r for r in (md.requires(p) or []) if "nonebot2" in r])
```

判读要点：`nonebot2>=2.2.0,<3.0.0` 放行；`nonebot2~=2.4` **也放行**（兼容版本号语义是 `>=2.4,<3.0`，
不是「锁死 2.4」）；`nonebot2>=2.4.3` 无上界，放行。真卡住的情况（`<2.5` 这种）才需要报告成阻塞项。

## 5. 机械比对两个 wheel：有没有删接口

先各下一份（**两个版本写进同一条 `pip download` 会 `ResolutionImpossible`，只能分两条命令下**）：

```bash
<python> -m pip download nonebot2==2.5.0 --no-deps -d .
<python> -m pip download nonebot2==2.4.4 --no-deps -d .
```

然后 AST 收集每个 `.py` 的顶层函数/类/赋值名，逐文件比对：

```python
import ast, zipfile

def surface(whl):
    out = {}
    with zipfile.ZipFile(whl) as z:
        for name in z.namelist():
            if name.endswith(".py") and name.startswith("nonebot/"):
                tree = ast.parse(z.read(name).decode("utf-8", "replace"))
                names = set()
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        names.add(node.name)
                    elif isinstance(node, ast.Assign):
                        names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
                out[name] = names
    return out

old, new = surface("nonebot2-2.4.4-py3-none-any.whl"), surface("nonebot2-2.5.0-py3-none-any.whl")
print("删除的文件:", sorted(set(old) - set(new)) or "无")
print("删除的顶层名:", {f: sorted(old[f] - new[f]) for f in set(old) & set(new) if old[f] - new[f]} or "无")
```

再把**仓库自己用到**的名字核一遍：正则扫 `repo/bot.py` 与 `repo/plugins/**/*.py`，取
`from nonebot.X import a, b` 与 `nonebot.<name>` 两种形态，对两版各查一次存在性，只报**状态不一致**的条目。
（子模块名如 `nonebot.log` 用「找 `nonebot/log.py`」这种朴素判据会有假阴性，只要两版结果一致就不影响结论。）

## 6. 报告形状

| 表 | 内容 |
| --- | --- |
| 版本现状 | 包 / 本机版本（含发布日期）/ 最新版（含发布日期）/ 结论 |
| 不兼容项 | 逐条列破坏性变更，并给「对本机是否适用」的判定（例：移除 Python 3.9 支持 → 本机 3.13 无影响） |
| 兼容性核查 | 三行：安装预演影响面 / 各插件版本约束 / 两个 wheel 的接口比对 |
| 动作与回退 | 备份 `pip freeze` → 装 → 自检对账 → 重启 → 真机验证；回退一条命令 |

结尾必须把「要不要现在升、升的话要重启一次机器人、离用户依赖的时间点还有多久」摆出来让他拍板；
升级后跑不了第三方插件自带的 pytest 套件这类限制（本机没装 pytest）要如实说明，不要为它去装依赖。
