# start.ps1 — 启动 ECHO 模型路由（独立常驻服务）
#   后台隐藏启动（默认）：
#     powershell -ExecutionPolicy Bypass -File dsh-failover\start.ps1
#   前台运行（调试）：
#     powershell -ExecutionPolicy Bypass -File dsh-failover\start.ps1 -Foreground
param(
    [switch]$Foreground,
    [int]$Port = 0,            # 覆盖端口（0 = 用 config 默认）
    [string]$Config = ""       # 覆盖配置文件
)
$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot

# 定位 venv 的 python / pythonw
$py = Join-Path (Split-Path $PSScriptRoot -Parent) 'venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    # 若 ECHO 用了 ASCII junction 的 venv
    $pyAlt = $env:ECHO_PYTHON
    if ($pyAlt -and (Test-Path $pyAlt)) { $py = $pyAlt }
}
if (-not (Test-Path $py)) { Write-Host '缺少 venv python' -ForegroundColor Red; exit 1 }
$pyw = $py -replace 'python\.exe$', 'pythonw.exe'

$argsList = @( (Join-Path $dir 'proxy.py') )
if ($Config -ne '')   { $argsList += @('--config', $Config) }
if ($Port -ne 0)      { $argsList += @('--port', $Port) }

# 日志目录
$logDir = Join-Path $dir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log    = Join-Path $logDir 'proxy.log'
$logErr = Join-Path $logDir 'proxy.err.log'

# 已在运行则退出（端口探测：读 config.json）
# 端口以 config.json 的 "port" 为准（不再写死：Windows 动态端口段会被
# Hyper-V/WSL 保留且重启漂移，落在其中的端口 bind 会失败）
$cfgPath = Join-Path $PSScriptRoot 'config.json'
$probe = 0
if (Test-Path $cfgPath) {
    try { $probe = [int]((Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json).port) } catch { $probe = 0 }
}
if (-not $probe -or $probe -le 0) { $probe = 8899 }
if ($Port -ne 0) { $probe = $Port }
try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:$probe/health" -TimeoutSec 2 -ErrorAction Stop
    if ($h.status -eq 'ok') { Write-Host "已在运行: http://127.0.0.1:$probe/health (status=ok)" -ForegroundColor Green; exit 0 }
} catch { }

if ($Foreground) {
    & $py @argsList
    exit $LASTEXITCODE
}

if (-not (Test-Path $pyw)) { Write-Host "缺少 pythonw: $pyw" -ForegroundColor Red; exit 1 }
$p = Start-Process -FilePath $pyw -ArgumentList $argsList `
    -WorkingDirectory $dir -RedirectStandardOutput $log `
    -RedirectStandardError $logErr -PassThru
Start-Sleep -Milliseconds 1500
try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:$probe/health" -TimeoutSec 3 -ErrorAction Stop
    Write-Host "ECHO 模型路由已启动 (PID $($p.Id))  http://127.0.0.1:$probe/health  status=$($h.status)" -ForegroundColor Green
} catch {
    Write-Host "启动失败，请查看日志: $logErr" -ForegroundColor Red
    exit 1
}
