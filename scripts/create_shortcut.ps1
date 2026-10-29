# 在桌面创建「DailyPaper」快捷方式：双击即后台启动本地助手并打开网页，不出现终端窗口。
#   用法：powershell -ExecutionPolicy Bypass -File scripts\create_shortcut.ps1
#   可选：-Desktop 改为别的目录；-Port 改端口
param([string]$Desktop = [Environment]::GetFolderPath("Desktop"), [int]$Port = 8765)

$root = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $PSScriptRoot "launch_app.ps1"
$icon = Join-Path $root "src\dailypaper\app\static\assets\dailypaper.ico"
if (-not (Test-Path $icon)) { & (Join-Path $root ".venv\Scripts\python.exe") (Join-Path $PSScriptRoot "make_icon.py") }

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path $Desktop "DailyPaper.lnk"))
$lnk.TargetPath = "powershell.exe"
$lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`" -Port $Port"
$lnk.WorkingDirectory = $root
$lnk.IconLocation = "$icon,0"
$lnk.Description = "DailyPaper：每日论文精读"
$lnk.WindowStyle = 7
$lnk.Save()

Write-Host "已创建快捷方式：$(Join-Path $Desktop 'DailyPaper.lnk')"
Write-Host "双击即可启动（约 3 秒后自动打开 http://127.0.0.1:$Port）。退出请在网页的「设置 → 研究方向 → 本地助手」中操作。"
