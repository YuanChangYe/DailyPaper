# 取消 install_task.ps1 注册的每日任务。
param([string]$TaskName = "DailyPaper")
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "已移除任务 $TaskName（如果存在）。"
