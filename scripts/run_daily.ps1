# 本地每日运行脚本（任务计划程序调用，也可以手动运行；参数原样传给 dailypaper run）。
# 日志由 dailypaper 自己写到 <项目目录>\logs\daily-YYYY-MM-DD.log（UTF-8），保留 30 天。
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root ".venv\Scripts\dailypaper.exe"
if (-not (Test-Path $exe)) {
    Write-Error "找不到 $exe，请先在项目目录创建虚拟环境并执行 pip install -e .[all]"
    exit 1
}
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("daily-" + (Get-Date -Format "yyyy-MM-dd") + ".log")

Set-Location $root
& $exe --log-file $log run @args | Out-Null
$code = $LASTEXITCODE

Get-ChildItem $logDir -Filter "daily-*.log" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } | Remove-Item -Force
exit $code
