# start-all.ps1 — 一键启动：DSH 执行引擎 + ECHO 面板（后台）
$ErrorActionPreference = 'Continue'
Write-Host '[1/3] 检查 DSH (3080) ...'
try { Invoke-WebRequest -Uri 'http://127.0.0.1:3080/' -UseBasicParsing -TimeoutSec 2 | Out-Null
    Write-Host '      DSH 已在运行' -ForegroundColor Green
} catch {
    Write-Host '      DSH 未运行，尝试拉起...' -ForegroundColor Yellow
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot '..\app\..\scripts\start.ps1') -Background 2>$null
    try {
        powershell -NoProfile -Command "Start-Sleep 2; try { (Invoke-WebRequest -Uri 'http://127.0.0.1:3080/' -UseBasicParsing -TimeoutSec 2).StatusCode } catch { 0 }" 2>$null | Out-Null
    } catch { }
    # 通过 ECHO 的 manager 拉起更可靠（见下）
}

Write-Host '[2/3] 启动 ECHO 面板 (8970) ...'
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'start.ps1') -Background
Start-Sleep 2

Write-Host '[3/3] 若 DSH 未就绪，通过面板拉起:'
Write-Host '      1. 浏览器打开面板（端口见 data\echo-port.txt）'
Write-Host '      2. 仪表盘 → DSH 执行引擎 → 启动'
