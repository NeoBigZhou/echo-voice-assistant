# rollback-echo-host-plugin.ps1 - undo the ECHO host plugin deployment.
#
# The deployment mechanism changed on 2026-09-12 (DSH Desktop 2.0.9):
#   * plugin source of truth : ECHO\plugin\echo-host\           (was ~\.dsh\profiles\desktop\echo-host)
#   * deployment + installer : ECHO\scripts\install-echo-host-plugin.ps1
#   * deployed overlay       : <DSH resources>\app.asar.unpacked\cordis.patch.yml
#   * deployed copy          : <DSH resources>\app.asar.unpacked\echo-host\
#
# Uninstalling is now a single command (restores the pre-deploy overlay snapshot,
# or removes the overlay this tool created, then deletes the deployed copy):
#
#   powershell -ExecutionPolicy Bypass -File scripts\install-echo-host-plugin.ps1 -Uninstall
#
# Reinstalling:
#
#   powershell -ExecutionPolicy Bypass -File scripts\install-echo-host-plugin.ps1
#
# This wrapper only forwards to the installer so the historical path keeps working.
# ASCII-only on purpose: Windows PowerShell 5.1 parses BOM-less .ps1 as ANSI/GBK.
param()

$ErrorActionPreference = 'Stop'
$installer = Join-Path $PSScriptRoot 'install-echo-host-plugin.ps1'

Write-Host '=== rollback ECHO host plugin ===' -ForegroundColor Cyan
if (-not (Test-Path $installer)) {
    Write-Host "[FAIL] installer not found: $installer" -ForegroundColor Red
    exit 1
}
& powershell -NoProfile -ExecutionPolicy Bypass -File $installer -Uninstall
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'Rollback done. Restart DSH Desktop for it to take effect.' -ForegroundColor Yellow
