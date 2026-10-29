# 桌面图标调用的启动器：后台启动本地助手（没有终端窗口），然后打开浏览器。
# 已在运行时只打开浏览器。退出：网页「设置 → 研究方向 → 本地助手 → 退出本地助手」。
param([int]$Port = 8765)
$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root ".venv\Scripts\dailypaper.exe"
$url = "http://127.0.0.1:$Port"

function Test-Port([int]$p) {
    try { $c = New-Object Net.Sockets.TcpClient; $c.Connect("127.0.0.1", $p); $c.Close(); return $true } catch { return $false }
}

if (-not (Test-Path $exe)) {
    [System.Windows.Forms.MessageBox] | Out-Null
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show("找不到 $exe`n`n请先在项目目录创建虚拟环境并执行 pip install -e .[all]", "DailyPaper") | Out-Null
    exit 1
}

if (-not (Test-Port $Port)) {
    $logDir = Join-Path $root "logs"; New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $log = Join-Path $logDir "app.log"
    Start-Process -FilePath $exe -ArgumentList @("app", "--no-browser", "--port", "$Port") -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError (Join-Path $logDir "app.err.log")
    $deadline = (Get-Date).AddSeconds(20)
    while (-not (Test-Port $Port) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 300 }
}
Start-Process $url
