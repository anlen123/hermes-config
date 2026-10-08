# Restart the my_nonebot2 QQ bot: kill by command-line match, then start it detached.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File restart_nb2_bot.ps1
#
# WHY a script instead of a one-liner: the kill must match BOTH the main process
# (bot.py) and the spawned child (multiprocessing) - matching by process name alone
# would hit other python processes on this machine.
# WHY not Hermes terminal(background=True): that process lives under Hermes' own
# process tree and dies silently when the Hermes gateway restarts.
#
# IMPORTANT: keep this file ASCII-only. PowerShell 5.1 reads BOM-less .ps1 as GBK,
# and CJK text in here breaks parsing with "string missing terminator".
#
# After running: wait 60-90s, then
#   1) netstat -ano | grep ":8899"          (new PID must be LISTENING)
#   2) grep -a "<HH:MM>" bot.log             (post-restart real events)
# Do NOT grep the log with ^ anchors - every line carries ANSI colour escapes.

$repo = "C:\Users\Administrator\Desktop\nb2\my_nonebot2"
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"

$targets = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'bot\.py|multiprocessing' }

if (-not $targets) {
    Write-Output "no bot process found"
} else {
    foreach ($p in $targets) {
        Write-Output ("killing PID={0} started={1}" -f $p.ProcessId, $p.CreationDate)
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
}
Start-Sleep -Seconds 2

$left = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'bot\.py|multiprocessing' }
Write-Output ("remaining matching processes: " + (@($left).Count))

# Append (>>) keeps the previous log for troubleshooting; > would wipe it.
Start-Process -FilePath 'cmd.exe' -WindowStyle Hidden -ArgumentList '/c',
    ("cd /d {0} && {1} bot.py >> bot.log 2>&1" -f $repo, $py)

Write-Output "start command issued - wait 60-90s, then verify port 8899 + real events in bot.log"
