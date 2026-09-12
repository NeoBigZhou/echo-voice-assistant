# heal-dsh-plugin.ps1 - self-heal the echo-host registration after a DSH Desktop upgrade.
#
# Why: DSH Desktop rebuilds <install>\resources\app.asar.unpacked and keeps its own
# profile state on every upgrade. echo-host is registered from that installation /
# the active profile, so an upgrade can drop the row again - it did on 2026-09-12
# (2.0.5 -> 2.0.9: the row disappeared from the Loader inventory, so the plugin
# silently stopped loading).
#
# How: install-upgrade-heal.ps1 puts a shortcut to THIS script in the user's
# Startup folder. At logon it waits (default 90s) so DSH Desktop has started,
# then re-runs the idempotent installer - restoring both the deployed copy and
# the profile patch row. No admin rights, no Task Scheduler: "logon"-triggered
# scheduled tasks are blocked by policy on this machine (Access denied / 0x80004005).
#
# Manual run: powershell -ExecutionPolicy Bypass -File scripts\heal-dsh-plugin.ps1
# ASCII-only on purpose: Windows PowerShell 5.1 parses BOM-less .ps1 as ANSI/GBK.
param(
    [int]$DelaySeconds = 90
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$installer = Join-Path $PSScriptRoot 'install-echo-host-plugin.ps1'
$logDir = Join-Path $root 'data\logs'
$logFile = Join-Path $logDir 'heal-dsh-plugin.log'

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Log([string]$message) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

if (-not (Test-Path $installer)) {
    Log "installer missing: $installer"
    exit 1
}

Log "===== heal start (delay ${DelaySeconds}s) ====="
if ($DelaySeconds -gt 0) { Start-Sleep -Seconds $DelaySeconds }

try {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $installer -Quiet
    $code = $LASTEXITCODE
    if ($code -eq 0) { Log "installer OK (plugin row + deployed copy verified)" }
    else { Log "installer FAILED exit=$code (see output above / run it manually)" }
    exit $code
} catch {
    Log "installer threw: $_"
    exit 1
}
