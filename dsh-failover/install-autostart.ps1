# install-autostart.ps1 — DSH 容灾代理开机自启（启动文件夹快捷方式）
#   用法: powershell -ExecutionPolicy Bypass -File dsh-failover\install-autostart.ps1
#        powershell -ExecutionPolicy Bypass -File dsh-failover\install-autostart.ps1 -Remove
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot
$startup = [Environment]::GetFolderPath('Startup')
$lnkPath = Join-Path $startup 'DSH 模型容灾代理.lnk'

if ($Remove) {
    if (Test-Path $lnkPath) { Remove-Item $lnkPath -Force; Write-Host '已移除开机自启' }
    else { Write-Host '未安装自启' }
    exit 0
}

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = 'powershell.exe'
$lnk.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$dir\start.ps1`""
$lnk.WorkingDirectory = $dir
$lnk.Description = 'DSH 模型容灾代理（内网优先，内网不可达自动切公网；http://127.0.0.1:8899）'
$lnk.Save()
Write-Host "已创建开机自启: $lnkPath"
Write-Host '详情: powershell -ExecutionPolicy Bypass -File dsh-failover\status.ps1'
