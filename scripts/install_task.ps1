# 在 Windows 任务计划程序中注册每日运行（当前用户，不需要管理员权限）。
#   默认每天 14:00；到点时电脑没开机的话，开机后自动补跑。
#   用法：powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 [-Time 14:00]
#   取消：powershell -ExecutionPolicy Bypass -File scripts\uninstall_task.ps1
param([string]$Time = "14:00", [string]$TaskName = "DailyPaper")

$script = Join-Path $PSScriptRoot "run_daily.ps1"
$argument = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "DailyPaper: daily arXiv screening and deep reading" -Force | Out-Null

Write-Host "已注册任务 $TaskName：每天 $Time 运行 $script（错过时开机后补跑）。"
Write-Host "在「任务计划程序」中可以查看或修改。立即试运行：schtasks /Run /TN $TaskName"
