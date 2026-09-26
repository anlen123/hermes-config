#!/usr/bin/env python3
"""延时重启 gateway（脱离进程组，避免掐断当前正在生成的回复）。

gateway 由 s6 监管；重启会杀掉 gateway 的整个进程组，正在跑同一进程组的 agent
（也就是发起本次重启的那个会话）会一起被杀、回复发不出去。所以这里：
  1. fork 一次，父进程立刻退出（调用方拿到返回值，不阻塞对话）；
  2. 子进程 setsid() 脱离原进程组/会话，改用 /dev/null 作 stdio；
  3. sleep N 秒（给回复留出发送时间）后再 s6-svc -r。

用法： python3 /opt/data/scripts/gw-restart-later.py [延时秒数，默认25]
"""

import os
import subprocess
import sys
import time

SERVICE = "/run/service/gateway-default"


def main() -> int:
    delay = 25
    if len(sys.argv) > 1:
        try:
            delay = int(sys.argv[1])
        except ValueError:
            pass

    pid = os.fork()
    if pid != 0:
        # 父进程：立刻返回，让对话继续。
        print(f"已安排 {delay} 秒后重启 gateway（后台 pid={pid}）")
        return 0

    # 子进程：脱离进程组与会话，静默运行。
    os.setsid()
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)

    time.sleep(delay)
    try:
        subprocess.run(["/command/s6-svc", "-r", SERVICE], check=False)
    except Exception:
        pass
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
