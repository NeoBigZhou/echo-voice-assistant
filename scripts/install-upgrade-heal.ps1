# install-upgrade-heal.ps1 - make "DSH Desktop upgraded, plugin gone" self-healing.
#
# Background: DSH Desktop rebuilds <install>\resources\app.asar.unpacked and keeps
# its own profile state on every upgrade. echo-host is registered from that
# installation / the active profile, so an upgrade can drop the registration -
# it did on 2026-09-12 (2.0.5 -> 2.0.9): the row disappeared from the Loader
# inventory and the plugin silently stopped loading.
#
# What this does: puts a shortcut to scripts\heal-dsh-plugin.ps1 in the user's
# Startup folder. At every logon that script waits (default 90s) and re-runs the
# idempotent installer, so a freshly upgraded DSH Desktop finds the plugin row
# restored by itself.
#
# Why not a scheduled task: "logon"-triggered tasks are blocked by policy on this
# machine (schtasks /sc onlogon -> Access denied; Register-ScheduledTask with any
# trigger -> 0x80004005). The Startup folder is the mechanism this account already
# uses for ECHO autostart (see install-autostart.ps1), so we reuse it.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\install-upgrade-heal.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install-upgrade-heal.ps1 -Remove
#   powershell -ExecutionPolicy Bypass -File scripts\install-upgrade-heal.ps1 -DelaySeconds 30
#
# ASCII-only on purpose: Windows PowerShell 5.1 parses BOM-less .ps1 as ANSI/GBK,
# so a non-ASCII literal here would be read back corrupted (that bit us once -
# a shortcut name came out as mojibake).
param(
    [switch]$Remove,
    [int]$DelaySeconds = 90
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$healScript = Join-Path $PSScriptRoot 'heal-dsh-plugin.ps1'
$startup = [Environment]::GetFolderPath('Startup')
$lnkPath = Join-Path $startup 'ECHO DSH plugin heal.lnk'

if ($Remove) {
    if (Test-Path $lnkPath) { Remove-Item $lnkPath -Force; Write-Host "removed: $lnkPath" -ForegroundColor Yellow }
    else { Write-Host "not installed: $lnkPath" }
    exit 0
}

if (-not (Test-Path $healScript)) { Write-Host "[FAIL] missing $healScript" -ForegroundColor Red; exit 1 }

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = 'powershell.exe'
# No -WindowStyle Hidden: this machine's policy blocks hidden powershell launches
# from shortcuts (see launch-desktop.ps1). The window is short-lived anyway.
$lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$healScript`" -DelaySeconds $DelaySeconds"
$lnk.WorkingDirectory = $root
$lnk.Description = 'ECHO: restores the DSH Desktop plugin registration after a DSH upgrade'
$lnk.Save()

Write-Host "installed logon heal: $lnkPath" -ForegroundColor Green
Write-Host "  delay: ${DelaySeconds}s after logon, then install-echo-host-plugin.ps1 -Quiet"
Write-Host "  log:   $root\data\logs\heal-dsh-plugin.log"
Write-Host "  remove with: -Remove"
