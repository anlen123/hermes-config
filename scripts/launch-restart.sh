#!/bin/bash
# 把延时重启脚本以脱离进程组的方式丢出去，自己立刻退出。
# 这样 gateway 随后被 s6 重启时，不会连带杀掉这个「延时器」。
setsid nohup /opt/data/scripts/delayed-gateway-restart.sh >/dev/null 2>&1 </dev/null &
exit 0
