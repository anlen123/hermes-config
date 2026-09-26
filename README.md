# Hermes Agent 配置备份（多机共用）

本仓库是 Hermes Agent **自定义内容**的快照，用于换机、重装或迁移时快速复原。
**多台机器共用一个仓库，每台机器一个目录** —— `config.yaml` / `SOUL.md` / `memories/` 是「单一文件」，
不分目录的话两台机器会互相覆盖。

## 目录结构

```
hermes-config/
├── scripts/sync-hermes-config.py      ← 共用工具，全仓库只此一份（各机都跑它，别各留副本）
└── hosts/
    ├── ugreen-nas/                    ← UGREEN NAS（docker，HERMES_HOME=/opt/data）
    │   ├── config.yaml  SOUL.md  RESTART-NOTES.txt  .env.example
    │   ├── memories/{MEMORY.md,USER.md}
    │   ├── scripts/                   该机运行时脚本（聊天汉化补丁、网关健康检查/看门狗、重启辅助…）
    │   └── skills/                    **只收录该机用户自建技能**（白名单来自 list-custom-skills.py）
    └── windows-server-2019/           ← Windows Server 2019
        ├── config.yaml  SOUL.md  .env.example
        ├── memories/{MEMORY.md,USER.md}
        └── skills/ …（有自建技能时才有）
```

Hermes 镜像自带的技能（`/opt/hermes/skills` + `optional-skills`，约 196 个）**不进仓库** ——
升级或换镜像后自带，放进来只是体积噪音。

## 各机接入（3 步）

```bash
# 1) 克隆到 <HERMES_HOME>/hermes-config/
git -c core.autocrlf=false clone https://github.com/anlen123/hermes-config.git <HERMES_HOME>/hermes-config
#    注：Windows 上必须带 -c core.autocrlf=false。默认 true 会把工作区转成 CRLF，
#    而仓库存 LF，结果每个文件都显示成「已修改」（上万行假 diff）。

# 2) 固定本机标识（**不备份**，缺失时脚本会拒绝运行）
echo windows-server-2019 > <HERMES_HOME>/.sync-host     # 本机
echo ugreen-nas          > /opt/data/.sync-host         # NAS

# 3) 跑同步（--dry-run 只到安全复扫，不提交不推送）
python3 <HERMES_HOME>/hermes-config/scripts/sync-hermes-config.py --dry-run
python3 <HERMES_HOME>/hermes-config/scripts/sync-hermes-config.py
```

机器标识**必须手工固定，脚本不会用 hostname 猜**：容器里 hostname 常是随机 ID，
一猜就会凭空造出一个 `hosts/<乱码>/` 目录。

## 可选的「不备份」控制文件

放在 `<HERMES_HOME>/` 下，都被 `.gitignore` 排除，只影响本机行为：

| 文件 | 作用 |
|---|---|
| `.sync-host` | **必需**，本机标识（= 目录名 `hosts/<这个值>/`） |
| `.sync-mask.txt` | 内容为需要脱敏的真实账号名；脚本推送前把它替换成 `YOUR_QUARK_ACCOUNT` |
| `.sync-no-skills` | 存在则本机**完全不碰**自己的 `skills/`（适合「这台机器不要某类技能」） |

等价的环境变量：`HERMES_HOST`、`HERMES_SRC`；命令行还有 `--no-skills`。

## 有意没有备份的内容

| 未备份 | 原因 |
|---|---|
| `.env`、`auth.json` | 含真实凭据（大模型接口密钥、机器人密钥等）。**凭据不进版本库**：git 历史是永久的，一旦提交即使删文件也仍然可被找回 |
| `sessions/` | 完整对话记录，含隐私内容 |
| `logs/`、`cache/`、`tmp/`、`state/` | 运行时产物 |
| `checkpoints/`（约 2.6G）、`backups/` | 体积大且为自动生成 |
| `home/` | 工具缓存与临时环境 |

推送前有**两道强制安全闸门**：拿 `<HERMES_HOME>/.env` 里的真实凭据值反查**整个仓库**，
命中即中止推送；第二道查令牌字段与账号标识（userId / deviceId / 账号名）。

## 如何用它恢复某台机器

1. 按官方文档装好 Hermes Agent
2. 把 `hosts/<机器名>/` 的内容复制到该机的 `HERMES_HOME`
3. 按 `.env.example` 的清单在该机 `.env` 里填回真实凭据
4. 把 `memories/MEMORY.md` 里的 `YOUR_QUARK_ACCOUNT` 换回真实账号名
5. 重启网关服务使配置生效

## 注意：旧版脚本会写回旧布局

根目录若重新出现 `config.yaml` / `SOUL.md` / `memories/` / `skills/`（旧布局），
说明**还有机器在跑旧版同步脚本** —— 请把那台机器的脚本换成仓库里的 `scripts/sync-hermes-config.py`，
否则它每跑一次都会重新写回根目录。脚本会自动检测并提醒这一点。
