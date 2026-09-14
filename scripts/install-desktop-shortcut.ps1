# install-desktop-shortcut.ps1 — 在桌面创建"ECHO 个人助理"快捷方式（双击一键启动）
# 可重复运行（覆盖更新）；卸载时加 -Remove
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$desktop = [Environment]::GetFolderPath('Desktop')
$lnkPath = Join-Path $desktop 'ECHO 个人助理.lnk'
$target = Join-Path $root 'scripts\launch-desktop.ps1'

if ($Remove) {
    if (Test-Path $lnkPath) { Remove-Item $lnkPath -Force; Write-Host '已移除桌面快捷方式' }
    else { Write-Host '桌面快捷方式不存在' }
    exit 0
}

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = 'powershell.exe'
# 注意：不能带 -WindowStyle Hidden（组策略会拦截隐藏启动 powershell.exe，
# 导致双击无反应）；launch-desktop.ps1 内部会自行隐藏窗口。
$lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$target`""
$lnk.WorkingDirectory = $root
$lnk.Description = 'ECHO 个人助理：一键启动（后台）+ 自动拉起 DSH + 打开面板'
# 图标：优先 python.exe，兜底默认
$icon = Join-Path $root 'venv\Scripts\python.exe'
if (Test-Path $icon) { $lnk.IconLocation = "$icon,0" }
else { $lnk.IconLocation = "$env:SystemRoot\System32\shell32.dll,220" }
$lnk.Save()
Write-Host "已创建桌面快捷方式: $lnkPath"
Write-Host '双击即可启动 ECHO 并打开控制面板（端口以 data\echo-port.txt 为准）'
