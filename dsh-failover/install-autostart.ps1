# install-autostart.ps1 — ECHO 模型路由开机自启（启动文件夹快捷方式）
#   用法: powershell -ExecutionPolicy Bypass -File dsh-failover\install-autostart.ps1
#        powershell -ExecutionPolicy Bypass -File dsh-failover\install-autostart.ps1 -Remove
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot
$startup = [Environment]::GetFolderPath('Startup')
$lnkPath = Join-Path $startup 'ECHO 模型路由.lnk'
# 早期版本用的名字（那时还叫「容灾代理」）：安装/卸载时一并清掉，避免两套自启并存
$legacyLnk = Join-Path $startup 'DSH 模型容灾代理.lnk'

if ($Remove) {
    $removed = $false
    foreach ($p in @($lnkPath, $legacyLnk)) {
        if (Test-Path $p) { Remove-Item $p -Force; $removed = $true }
    }
    if ($removed) { Write-Host '已移除开机自启' } else { Write-Host '未安装自启' }
    exit 0
}

if (Test-Path $legacyLnk) { Remove-Item $legacyLnk -Force }

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = 'powershell.exe'
$lnk.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$dir\start.ps1`""
$lnk.WorkingDirectory = $dir
$lnk.Description = 'ECHO 模型路由（ECHO AUTO 模型组，按优先级派发；http://127.0.0.1:8899）'
$lnk.Save()
Write-Host "已创建开机自启: $lnkPath"
Write-Host '详情: powershell -ExecutionPolicy Bypass -File dsh-failover\status.ps1'
