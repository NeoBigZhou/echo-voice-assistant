# install-autostart.ps1 — 开机自启（启动文件夹快捷方式）
# 用法: powershell -File scripts\install-autostart.ps1   /  -Remove 卸载
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$startup = [Environment]::GetFolderPath('Startup')
$lnkPath = Join-Path $startup 'ECHO 个人助理.lnk'

if ($Remove) {
    if (Test-Path $lnkPath) { Remove-Item $lnkPath -Force; Write-Host '已移除开机自启' }
    else { Write-Host '未安装自启' }
    exit 0
}

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = 'powershell.exe'
$lnk.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$root\scripts\start.ps1`" -Background"
$lnk.WorkingDirectory = $root
$lnk.Description = 'ECHO 个人助理（后台启动，自动打开面板）'
$lnk.Save()
Write-Host "已创建开机自启: $lnkPath"
Write-Host '（如需桌面快捷方式，可手动把该文件复制到桌面）'
