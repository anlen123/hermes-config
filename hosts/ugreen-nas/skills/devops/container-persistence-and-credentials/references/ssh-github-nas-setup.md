# SSH / GitHub 配置实录（UGREEN NAS Hermes 容器，2026-09-26）

一次真实交付的完整过程，含实测输出、踩到的报错、修法和「哪些是已验证 / 哪些待用户完成」的分界。

## 0. 起点探测

```bash
echo "HOME=$HOME whoami=$(whoami) uid=$(id -u)"
# -> HOME=/opt/data/home  whoami=root  uid=0

ls -la ~/.ssh/            # -> (不存在)
git config --global user.name   # -> 未设
which git ssh ssh-keygen        # -> /usr/bin/git /usr/bin/ssh /usr/bin/ssh-keygen
git --version; ssh -V           # -> git 2.47.3 ; OpenSSH_10.0p2 Debian-7+deb13u4
which gh                        # -> 未装（走 git-only 方案，无需 sudo）

# 出网能力
timeout 8 bash -c "cat < /dev/null > /dev/tcp/github.com/22"      && echo "22 可达"
timeout 8 bash -c "cat < /dev/null > /dev/tcp/ssh.github.com/443" && echo "443 可达"
# -> 两个都可达
```

## 1. 生成密钥（ed25519，无密码短语，便于自动拉取/推送）

```python
import os, subprocess
SSHDIR = os.path.join(os.path.expanduser("~"), ".ssh")   # /opt/data/home/.ssh —— 持久卷 ✅
os.makedirs(SSHDIR, mode=0o700, exist_ok=True); os.chmod(SSHDIR, 0o700)
KEY = os.path.join(SSHDIR, "id_ed25519")
subprocess.run(["ssh-keygen", "-t", "ed25519", "-C", "hermes-agent@ugreen-nas",
                "-f", KEY, "-N", "", "-q"], check=True)
os.chmod(KEY, 0o600); os.chmod(KEY + ".pub", 0o644)
```
> 用 Python 的 `subprocess` 而不是 shell：文件名/注释里可能带 emoji、中文，shell 转义容易坏。

结果：
```
公钥: ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGAgjHQvO2War6iqaLoScu+syVYF9BBaTrAMQ/RlU2Jp hermes-agent@ugreen-nas
指纹: SHA256:D1QpSk29TS6w/2uAhCEn/eYVRJ/XLFjhme8w+zjyv50 (ED25519)
权限: drwx------ .ssh/ ; -rw------- id_ed25519 ; -rw-r--r-- id_ed25519.pub
```

## 2. 第一个报错：known_hosts 写了却不被读到

按 GitHub 官方公布的主机公钥写好 `known_hosts` 后测试：

```
$ ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes git@github.com
No ED25519 host key is known for github.com and you have requested strict checking.
Host key verification failed.
```

文件本身没问题（内容、行数、字段数都对），根因在**路径**：

```
$ ssh -G github.com | grep -iE "userknownhostsfile|identityfile"
userknownhostsfile /root/.ssh/known_hosts /root/.ssh/known_hosts2     ← 实际读这里
identityfile ~/.ssh/id_ed25519                                        ← 且按 /etc/passwd 展开

$ getent passwd root
root:x:0:0:root:/root:/bin/bash          ← ssh 的 ~ = /root，不是 $HOME=/opt/data/home

$ df -h /root /opt/data
overlay /                    ← 临时层
/dev/mapper/ug_..._volume1 /opt/data      ← 持久卷
```

于是：文件放 `$HOME/.ssh`（持久但读不到），或放 `/root/.ssh`（读得到但不持久）——**两难**。

## 3. 修法：软链打通 + 绝对路径 config

```bash
ln -sfn /opt/data/home/.ssh /root/.ssh      # ssh 默认路径 → 持久卷
cat > /opt/data/home/.ssh/config <<'EOF'
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
EOF
chmod 600 /opt/data/home/.ssh/config
```

验证：
```
$ ls -ld /root/.ssh
lrwxrwxrwx ... /root/.ssh -> /opt/data/home/.ssh
$ ssh -T -o BatchMode=yes git@github.com
git@github.com: Permission denied (publickey).      ← ✅ 期望值（还没把公钥加到 GitHub）
$ ssh -o BatchMode=yes -p 443 git@ssh.github.com
git@ssh.github.com: Permission denied (publickey).  ← 443 备用通道同样握手成功
```

## 4. 主机公钥来源与指纹核对

```bash
curl -s https://api.github.com/meta | python3 -c "
import json,sys
for k in json.load(sys.stdin)['ssh_keys']:
    if k.startswith('ssh-ed25519'):
        print('github.com', k); print('ssh.github.com', k)" > /opt/data/home/.ssh/known_hosts
ssh-keygen -lf /opt/data/home/.ssh/known_hosts
# 256 SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU github.com (ED25519)
# 256 SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU ssh.github.com (ED25519)
```
`api.github.com/meta` 公布的 `ssh_fingerprints` 里含同一条 `+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU`
→ 官方值与写进 known_hosts 的一致（另外两条是 RSA `uNiVzt...` 与 ECDSA `p2QAMX...`）。

## 5. 交付话术（用户要公钥去 GitHub 粘贴）

给用户三样东西，缺一不可：
1. **整行公钥**（单独代码块，便于复制——别拆行、别加换行）
2. **路径**：GitHub → Settings → SSH and GPG keys → New SSH key；Title 建议用机器名；
   Key type 选 Authentication Key
3. **指纹**（`SHA256:D1QpSk29TS6w/2uAhCEn/eYVRJ/XLFjhme8w+zjyv50`），让用户加完能在 GitHub 上核对

同时明确交代状态边界：
> 本机已就绪：主机公钥按官方值核对过，握手正常；返回的「Permission denied」是**还没授权**，
> 不是连不上。你保存好后说一声，我立刻测真实登录。

## 6. 镜像更新后的恢复（一条命令）

```bash
ln -sfn /opt/data/home/.ssh /root/.ssh
```
密钥 / config / known_hosts 都在 `/opt/data/home/.ssh`（持久卷），无需重新生成、无需重新在
GitHub 添加公钥。

## 7. 仍未验证的部分（诚实记账）

- `git push` 端到端未实测：会话结束时用户尚未把公钥贴到 GitHub，**账号授权那一环没有跑过**。
- `git config --global user.name/user.email` 尚未设置（提交身份需要用户提供姓名与邮箱）。
- 未启用 `git config --global url."git@github.com:".insteadOf "https://github.com/"`
  （全局改写 HTTPS→SSH，会影响所有仓库，属可选，需用户确认后再开）。
