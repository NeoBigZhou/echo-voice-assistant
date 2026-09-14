# stop.ps1 — 停止 ECHO 模型路由
#   用法: powershell -ExecutionPolicy Bypass -File dsh-failover\stop.ps1
$ErrorActionPreference = 'SilentlyContinue'
$dir = $PSScriptRoot
# 端口以 config.json 的 "port" 为准（不再写死：Windows 动态端口段会被
# Hyper-V/WSL 保留且重启漂移，落在其中的端口 bind 会失败）
$cfgPath = Join-Path $PSScriptRoot 'config.json'
$probe = 0
if (Test-Path $cfgPath) {
    try { $probe = [int]((Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json).port) } catch { $probe = 0 }
}
if (-not $probe -or $probe -le 0) { $probe = 8899 }
# 从 config 读端口（若存在且含 port）
$cfgFile = Join-Path $dir 'config.json'
if (Test-Path $cfgFile) {
    try {
        $cfg = Get-Content $cfgFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($cfg.port) { $probe = [int]$cfg.port }
    } catch { }
}

# 找到占用端口的进程并结束
$conn = Get-NetTCPConnection -LocalPort $probe -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    $pids = $conn.OwningProcess | Sort-Object -Unique
    foreach ($pid_ in $pids) {
        $proc = Get-Process -Id $pid_ -ErrorAction SilentlyContinue
        if ($proc) {
            $proc | Stop-Process -Force
            Write-Host "已停止模型路由 PID $pid_ (端口 $probe)"
        }
    }
} else {
    Write-Host "模型路由未在运行 (端口 $probe 无监听)"
}
