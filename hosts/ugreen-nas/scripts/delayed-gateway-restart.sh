#!/bin/bash
# 延时重启 gateway（给当前回复留出送达时间），并自证是否成功拉起。
# 用法：terminal(command="bash /opt/data/scripts/delayed-gateway-restart.sh", background=true)
# 说明：重启会杀掉本进程（它是 gateway 的子进程），但重启信号在杀之前就已发出。
LOG=/opt/data/logs/i18n-restart.log
DELAY="${1:-30}"
{
  echo "=== $(date '+%F %T') 计划 $DELAY 秒后重启 gateway ==="
  OLD_PID=$(python3 -c "import json;print(json.load(open('/opt/data/gateway.pid'))['pid'])" 2>/dev/null)
  echo "旧 gateway pid: $OLD_PID"
  sleep "$DELAY"
  /command/s6-svc -r /run/service/gateway-default
  echo "$(date '+%T') 已发送重启信号 (s6-svc rc=$?)"
  sleep 25
  NEW_PID=$(python3 -c "import json;print(json.load(open('/opt/data/gateway.pid'))['pid'])" 2>/dev/null)
  echo "新 gateway pid: $NEW_PID"
  if [ -n "$NEW_PID" ] && [ "$NEW_PID" != "$OLD_PID" ]; then
    echo "结果: ✅ 重启成功（pid $OLD_PID -> $NEW_PID）"
  else
    echo "结果: ⚠️ pid 未变化（$OLD_PID），可能没重启成功，需人工检查 s6"
  fi
} >> "$LOG" 2>&1
