# Hermes Agent 配置备份（UGREEN NAS）

本仓库是运行在 **UGREEN NAS** 上的 Hermes Agent 的自定义内容快照，用于换机、重装或迁移时快速复原。

## 目录结构

| 路径 | 内容 |
|---|---|
| `config.yaml` | 主配置（模型、网关、审批模式、界面语言等） |
| `SOUL.md` | 人格文件 |
| `memories/MEMORY.md` | 助手长期笔记（环境事实、路径、踩过的坑） |
| `memories/USER.md` | 用户偏好档案 |
| `scripts/` | 自定义脚本：聊天汉化补丁、网关健康检查/看门狗、重启辅助等 |
| `skills/` | **只收录用户自建技能**（网盘下载、汉化、网关运维等 15 个）。Hermes 镜像自带的技能不在备份范围内 —— 升级或换镜像后自带，不需要也不应该放进仓库 |
| `RESTART-NOTES.txt` | 重启前的状态快照记录 |
| `.env.example` | 需要的环境变量**清单**（只有变量名，没有值） |

## 有意没有备份的内容

| 未备份 | 原因 |
|---|---|
| `.env`、`auth.json` | 含真实凭据（大模型接口密钥、机器人密钥等）。**凭据不进版本库**：git 历史是永久的，一旦提交即使删文件也仍然可被找回 |
| `sessions/` | 完整对话记录，含隐私内容 |
| `logs/`、`cache/`、`tmp/`、`state/` | 运行时产物 |
| `checkpoints/`（约 2.6G）、`backups/` | 体积大且为自动生成 |
| `home/` | 工具缓存与临时环境 |

## 脱敏说明

- `memories/MEMORY.md` 里的网盘账号名 → 已替换为 `YOUR_QUARK_ACCOUNT`
- `.env.example` 里的凭据值 → 已替换为 `<在此填入真实值>`
- 除以上两处，其余内容为原样备份

## 如何用它恢复

1. 按官方文档装好 Hermes Agent（本机为 docker 部署，`HERMES_HOME` = `/opt/data`）
2. 把本仓库内容复制到 `HERMES_HOME`
3. 按 `.env.example` 的清单在 `HERMES_HOME/.env` 里填回真实凭据
4. 把 `memories/MEMORY.md` 里的 `YOUR_QUARK_ACCOUNT` 换回真实账号名
5. 重启网关服务使配置生效

## 维护方式

本仓库内容由 NAS 上的 `/opt/data/hermes-config/` 目录生成并推送，源文件仍在 Hermes 的正常运行目录中，互不影响。

```bash
# 1) 刷新「用户自建技能」白名单（判定依据：技能名不在镜像自带目录里，且文件晚于镜像安装时间）
/opt/hermes/.venv/bin/python /opt/data/scripts/list-custom-skills.py

# 2) 聚合 → 脱敏 → 安全复扫 → 提交 → 推送（--dry-run 只到复扫）
/opt/hermes/.venv/bin/python /opt/data/scripts/sync-hermes-config.py
```

镜像自带的技能位于 `/opt/hermes/skills`（81 个）与 `/opt/hermes/optional-skills`（115 个），共 196 个，**不进仓库**。
