# start.ps1 - start the ECHO service.
#   foreground : powershell -File scripts\start.ps1
#   background : powershell -File scripts\start.ps1 -Background
#   supervised : powershell -File scripts\start.ps1 -Background -Supervise
#
# -Supervise is what the Startup shortcut uses: a resident watchdog that restarts
# ECHO whenever it is not listening, so ECHO's survival does NOT depend on the DSH
# Desktop plugin. A DSH upgrade then never requires restarting ECHO; if the plugin
# registration is lost, only the optional sidebar is affected.
#
# ASCII-ONLY ON PURPOSE. Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI/GBK:
# non-ASCII text gets mangled and the script fails to parse (that silently broke
# this very file on 2026-09-12 - "%s" style mojibake in a string literal -> "The
# string is missing the terminator"). Keep all scripts in this folder ASCII-only.
param(
    [switch]$Background,
    [switch]$Supervise,
    [int]$RestartDelaySeconds = 10
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent

# Best-effort self-heal of the DSH Desktop plugin deployment (idempotent, quiet).
$installer = Join-Path $PSScriptRoot 'install-echo-host-plugin.ps1'
if (Test-Path $installer) {
    try { & powershell -NoProfile -ExecutionPolicy Bypass -File $installer -Quiet } catch { }
}

$py = Join-Path $root 'venv\Scripts\python.exe'
# Prefer the ASCII junction (works around tools that cannot read non-ASCII paths).
$pyAlt = $env:ECHO_PYTHON   # 可选：非 ASCII 路径下的解释器覆盖，见 docs/DEPLOY.md
if ($pyAlt -and (Test-Path $pyAlt)) { $py = $pyAlt }
if (-not (Test-Path $py)) { Write-Host 'venv missing - run scripts\setup.ps1 first' -ForegroundColor Red; exit 1 }

$logDir = Join-Path $root 'data\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$outLog = Join-Path $logDir 'echo-server.log'
$errLog = "$outLog.err"
$supLog = Join-Path $logDir 'echo-supervisor.log'

function SupLog([string]$message) {
    Add-Content -Path $supLog -Value ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message) -Encoding UTF8
}

# pythonw.exe (GUI subsystem): no console window, so closing any window never sends
# a window-CLOSE event to ECHO (python.exe -NoNewWindow used to attach the service
# to the launcher console and a forrtl abort killed it on window close).
$pyw = $py -replace 'python\.exe$', 'pythonw.exe'
if (-not (Test-Path $pyw)) { Write-Host "pythonw missing: $pyw" -ForegroundColor Red; exit 1 }

# ECHO refuses to start when the port is already taken (anti-duplicate guard), so
# "probe then start" can never race with an existing instance.
function Test-EchoPort([int]$port = 8970) {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $c.BeginConnect('127.0.0.1', $port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(1200)) { return $false }
        $c.EndConnect($iar)
        return $true
    } catch { return $false }
    finally { $c.Dispose() }
}

function Start-EchoOnce([switch]$Quiet) {
    $p = Start-Process -FilePath $pyw -ArgumentList @('-m', 'app.main') `
        -WorkingDirectory $root -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog -PassThru
    if (-not $Quiet) { Write-Host "ECHO started in background (PID $($p.Id))  panel: http://127.0.0.1:8970" }
    return $p
}

if ($Background -and -not $Supervise) {
    [void](Start-EchoOnce)
    exit 0
}

if ($Supervise) {
    SupLog "===== supervisor start (restartDelay=${RestartDelaySeconds}s) ====="
    while ($true) {
        if (Test-EchoPort) { Start-Sleep -Seconds 15; continue }
        SupLog "ECHO not listening - starting"
        try {
            $p = Start-EchoOnce -Quiet
            SupLog "started pid=$($p.Id)"
        } catch {
            SupLog "start failed: $_"
        }
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Seconds 1
            if (Test-EchoPort) { break }
        }
        if (-not (Test-EchoPort)) {
            SupLog "not listening after 30s - retry in ${RestartDelaySeconds}s"
            Start-Sleep -Seconds $RestartDelaySeconds
        }
    }
}

# Foreground (debug)
& $py -m app.main
