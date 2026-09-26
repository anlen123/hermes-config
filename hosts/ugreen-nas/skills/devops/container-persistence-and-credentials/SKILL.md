---
name: container-persistence-and-credentials
description: 本机容器里哪些路径持久、凭据该放哪（SSH/git 等）。
version: 1.0.1
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [container, docker, persistence, credentials, ssh, nas]
    related_skills: [quarkclouddrive-nas-env-fix, github-auth]
---

# 容器内的持久化边界与凭据放置（UGREEN NAS 上的 Hermes 容器）

## When to Use（触发场景）
- 要**生成 SSH 密钥**、配 git/GitHub 认证、放置 token / cookie / 证书
- 问「哪些路径重启后还在」「装完的东西怎么又没了」「哪些能持久保存」
- 脚本报「找不到 key / 配置没生效」，但文件明明就在那儿
- 需要让容器内某个程序（ssh、git、CLI 工具）读到一份配置

## 环境事实（2026-09-26 实测）
| 项 | 值 |
|---|---|
| 运行用户 | root（uid 0） |
| `$HOME` 环境变量 | `/opt/data/home` |
| **`/etc/passwd` 里的家目录** | **`/root`** ← OpenSSH 等按这个展开 `~` |
| 根文件系统 | overlay（容器可写层）——**不持久** |
| 持久卷 | `/opt/data`（宿主 volume1 池）＋ `/volume1/共享影视作品`、`/volume1/共享相册` |

## 持久 vs 非持久（铁律）

| 路径 | 持久？ | 说明 |
|---|---|---|
| `/opt/data/**` | ✅ | Hermes 家目录：memories/ skills/ scripts/ cache/ config.yaml |
| `/volume1/**` | ✅ | 只挂载了共享影视作品、共享相册等 |
| `/root/**` | ❌ | 容器可写层，镜像更新/重建即丢 |
| `/usr`、`/etc`、`/opt/hermes`、`/tmp` | ❌ | 同上；`hermes update` 还会重写 `/opt/hermes` 下的源码 |

**结论**：密钥、token、cookie、脚本、配置一律落 `/opt/data/**`。必须出现在 `/root`、`/etc`
才生效的东西，用**软链或绝对路径配置**指向 `/opt/data`，并**把重建命令写下来**，
否则镜像更新后就失联（用户会以为「密钥坏了」）。

## 头号坑：`~` 不等于 `$HOME`

OpenSSH（及部分 CLI）按 `/etc/passwd` 展开 `~`，**不看 `$HOME` 环境变量**。后果：
- 生成到 `$HOME/.ssh/id_ed25519` 的密钥，`ssh` / `git` **根本不会用**；
- 放进 `/root/.ssh` 虽然能被读到，但**镜像更新就没了**。

先诊断再动手：
```bash
getent passwd root                                   # 真实的 ~（本机是 /root）
echo $HOME                                           # 可能是 /opt/data/home
ssh -G github.com | grep -iE "identityfile|userknownhostsfile"
df -h /root /opt/data                                # 哪个是挂载的持久卷
```

## SSH / GitHub 的标准落法（本机已验证到握手通过）
```bash
mkdir -p /opt/data/home/.ssh && chmod 700 /opt/data/home/.ssh
ssh-keygen -t ed25519 -C "hermes-agent@<host>" -f /opt/data/home/.ssh/id_ed25519 -N ""
ln -sfn /opt/data/home/.ssh /root/.ssh          # 让 ssh 的默认路径指向持久卷
```

`/opt/data/home/.ssh/config`（**一律写绝对路径，别依赖 `~`**）：
```
Host github.com
    HostName github.com
    User git
    IdentityFile /opt/data/home/.ssh/id_ed25519
    IdentitiesOnly yes
    UserKnownHostsFile /opt/data/home/.ssh/known_hosts

Host ssh.github.com
    HostName ssh.github.com
    User git
    Port 443
    IdentityFile /opt/data/home/.ssh/id_ed25519
    IdentitiesOnly yes
    UserKnownHostsFile /opt/data/home/.ssh/known_hosts
```

> ⚠️ `/root/.ssh` 只是软链。**镜像更新后重建**（一条命令）：
> `ln -sfn /opt/data/home/.ssh /root/.ssh`
> 密钥、config、known_hosts 都在持久卷上，不会丢，所以重建成本极低。

完整实录（输出样例、指纹核对、逐条排错、公钥交付话术）见
`references/ssh-github-nas-setup.md`。

## known_hosts：用官方公布的主机公钥，别用 ssh-keyscan 盲信

`ssh-keyscan` 是 TOFU（首次即信），当场被中间人顶替也发现不了。GitHub 官方公布主机公钥，
直接取来写死：
```bash
curl -s https://api.github.com/meta | python3 -c "
import json,sys
for k in json.load(sys.stdin)['ssh_keys']:
    if k.startswith('ssh-ed25519'):
        print('github.com', k); print('ssh.github.com', k)" > /opt/data/home/.ssh/known_hosts
ssh-keygen -lf /opt/data/home/.ssh/known_hosts      # 与官方指纹逐条核对
```
已核对通过（2026-09）：`SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU`

## 怎么读测试结果（最容易误判的一步）

| 输出 | 含义 |
|---|---|
| `Permission denied (publickey)` | ✅ **正常**：DNS、主机密钥校验、握手全部通过，只差账号授权（公钥还没贴到 GitHub） |
| `Host key verification failed` / `No ED25519 host key is known ...` | ❌ 真问题：`known_hosts` 放错位置或没被读到 |
| `Connection refused` / 超时（22 端口） | 网络层；改走 443：`ssh.github.com` + `Port 443` |

## 环境断言会腐烂：照抄前先复验（本 skill 存在的理由之一）

「某地址能不能通、某路径能不能写、某服务在不在、容器是不是 host 模式」这类说法会随时间过期，
而它们**直接决定命令成败**。本机就真实发生过：多份 skill 里写着容器是 `network_mode: host`、
能用 `127.0.0.1`，实测却 `127.0.0.1` 返回 `000`、只有网桥网关 `172.17.0.1` 能通——照抄就全错。

花 5 秒验一次，验完**立刻把 skill 改对**（别只记在心里）：

```bash
# ① 地址可达性（对比回环与网桥网关）
for u in http://127.0.0.1:5005/login http://172.17.0.1:5005/login; do
  printf "%-42s -> " "$u"; curl -s -o /dev/null -m 5 -w '%{http_code}\n' "$u"; done
# ② 家目录与持久性
getent passwd "$(whoami)"; echo "HOME=$HOME"; df -h /root /opt/data
# ③ 某程序真正生效的配置（别猜它读哪份）
ssh -G github.com | grep -iE "identityfile|userknownhostsfile"
```

读结果：`000` = 连不上（不通）；`200/302/400/401` 都算**通**，只代表路径或权限不对，不代表服务不在。
改完文档后回头 grep 一遍全文，把同类的旧断言一起清掉（只改一处会留下自相矛盾的说明）。

## 通用自检清单（动完凭据/配置后跑一遍）
1. 文件在持久卷吗？`ls -l` 看路径是否 `/opt/data/**`
2. 权限对吗？目录 `700`，私钥 / config / known_hosts `600`
3. 目标程序真读的是这份吗？用工具自己的打印口（`ssh -G`、`--help`、`config get`）确认
4. 有没有依赖临时层的软链/文件？列出来，并在交付说明里写明「更新后如何重建」
5. 有没有把「未验证」说成「已通过」？只报实测结论，并区分「已验证」与「待用户完成外部步骤」

## 头号坑 2：`git` 走 SSH 必须 `env -u GIT_SSH_COMMAND`

本机环境里被注入了 `GIT_SSH_COMMAND='ssh -o PubkeyAuthentication=no'`（禁掉公钥认证）。
它的优先级**高于** `core.sshCommand` 和仓库级配置，**只加 config 覆盖不掉**。症状很容易误判：

| 现象 | 含义 |
|---|---|
| `ssh -T git@github.com` 成功返回 `Hi <user>!` | 说明密钥、known_hosts、config 全部正确 |
| 但 `git push` / `git ls-remote` 报 `Permission denied (publickey)` | ❌ 就是被这个环境变量拦的 |

解法（调用时解除，别去改全局配置）：
```bash
env -u GIT_SSH_COMMAND git push origin main
# 或 python subprocess 里：env = {k:v for k,v in os.environ.items() if k != "GIT_SSH_COMMAND"}
```

## 备份本机配置到 GitHub（已落地，2026-09-26）

- 仓库：`git@github.com:anlen123/hermes-config.git`（**公开**，所以内容一律按公开标准脱敏）
- 聚合目录 `/opt/data/hermes-config/`；同步脚本 `/opt/data/scripts/sync-hermes-config.py`
  （聚合 → 脱敏 → **安全复扫** → 提交 → `env -u GIT_SSH_COMMAND` 推送；带 `--dry-run`）
- 白名单只收：`config.yaml`、`SOUL.md`、`RESTART-NOTES.txt`、`memories/`、`scripts/`、
  **以及用户自建技能（不含镜像自带技能）**
- **推送前强制复扫**（两道闸门）：① 拿 `/opt/data/.env` 里的真实凭据值反查聚合目录；
  ② 扫 `"accessToken": "长串"` 这类令牌字段 + 真实账号标识（账号名 / userId / deviceId）。
  任一命中即中止 —— 这条比任何 `.gitignore` 都可靠，因为 git 历史是永久的
- 不备份：`.env`、`auth.json`、`sessions/`、`logs/`、`cache/`、`checkpoints/`（约 2.6G）、`platforms/`
- 记忆里的网盘账号名统一替换为 `YOUR_QUARK_ACCOUNT`；账号名本身存在**不备份**的
  `/opt/data/.sync-mask.txt` 里（脚本不再自带明文账号名）

### skills/ 只收「用户自建技能」（2026-09-26 用户明确要求后改正）

初版脚本 `shutil.copytree("/opt/data/skills")` 把**整个技能树**都推上去了：128 个技能 /
12MB，其中 113 个是 Hermes 镜像自带的（`/opt/hermes/skills` 81 个 + `/opt/hermes/optional-skills`
115 个，共 196 个名字），既无意义又混进了运行时残留。正确做法：

```bash
# 生成白名单（判定=技能名不在镜像自带目录里 且 SKILL.md 晚于镜像安装时间）
/opt/hermes/.venv/bin/python /opt/data/scripts/list-custom-skills.py   # → /opt/data/scripts/custom-skills.json
```

判定依据两条，缺一不可：
1. frontmatter 的 `name:` **不出现**在 `/opt/hermes/skills` + `/opt/hermes/optional-skills` 里；
2. `SKILL.md` 的 mtime 晚于镜像安装时间点。

为什么两条都要：镜像可选库用**技能名**做目录名（`audiocraft-audio-generation`），
而 `/opt/data/skills` 里是缩写目录名（`audiocraft`），只按目录名比对会把 15 个镜像技能误判成自建；
反过来只按 mtime 又可能把「上游版本漂移」的技能算进来（同名的本地副本与镜像不同、但里面
一个字的中文都没有 → 那是版本更新，不是自建）。

### 技能目录里必须排除的运行时残留（踩过一次，真的把 accessToken 推上去了）

`skills/quarkclouddrive/` 内部混着 CLI 自己写的状态：
`hermes/config.json`（**含 accessToken / refreshToken / deviceId / userId**）、
`hermes/search/*.jsonl`（搜索历史，含网盘文件名）、`.quarkclouddrive/`、`*.log`。
拷贝技能时必须用自定义 ignore 过滤（见脚本里的 `_skill_ignore` / `SKIP_DIRS` / `SKIP_FILES`）。

**通用教训**：备份「用户目录」时，目录里往往混着工具自己的凭据缓存；
白名单只管到目录层级是不够的，必须在目录内部再排一次，且推送前用**内容特征**（令牌字段、
真实标识值）复扫，而不是只比对已知密钥值。

### 已推错内容如何回滚（不留痕）

```bash
cd /opt/data/hermes-config
git checkout --orphan clean && git add -A && git commit -m "..."
git branch -M clean main
git reflog expire --expire=now --all && git gc --prune=now -q
env -u GIT_SSH_COMMAND git push --force origin main
```
⚠️ 强推只让旧提交从分支上消失；**GitHub 侧按 commit SHA 仍可能短期可访问**。若误传的是
凭据，务必**轮换凭据**（如夸克重新登录使旧 token 失效），别只依赖强推。

## 坑
- 别把密钥/凭据放 `/root`、`/tmp`、`/opt/hermes`——当下能用，更新即失联。
- `hermes update` / 换镜像会重置 `/opt/hermes` 下的源码与 i18n 词典：本机的汉化补丁脚本需重跑
  （见 skill `hermes-gateway-chinese-localization`），源码级补丁同理。
- `whoami` 是 root ≠ 家目录可用；一律以 `getent passwd <user>` 为准。
- 交付时如实区分「已实测通过」和「待你完成的外部动作」（例如到 GitHub 粘贴公钥），
  不要因为命令跑通就宣称整条链路打通。
