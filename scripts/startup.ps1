# startup.ps1 - logon entry point for ECHO (Startup shortcut runs this).
#
# Goal: ECHO must come up on its own at every logon and must NOT depend on DSH
# Desktop or on the echo-host plugin. A DSH Desktop upgrade then never requires
# restarting ECHO: if the plugin registration is lost, only the optional sidebar
# is affected, and the self-heal below restores it silently.
#
# What it does, in order:
#   1. self-heal the DSH plugin registration (idempotent, quiet, best-effort)
#   2. start ECHO if port 8970 is not listening
#   3. supervise: restart ECHO whenever it stops listening (resident loop)
#
# ASCII-ONLY ON PURPOSE. Windows PowerShell 5.1 parses a BOM-less .ps1 as ANSI/GBK,
# so any non-ASCII literal here would be read back mangled and the file would fail
# to parse - that silently broke start.ps1/launch-desktop.ps1 on 2026-09-12 while
# every log said nothing at all. Keep every script we create ASCII-only.
#
# Manual run: powershell -ExecutionPolicy Bypass -File scripts\startup.ps1
# Uninstall : remove the shortcut "ECHO startup.lnk" from the Startup folder
#             (or run scripts\install-autostart.ps1 -Remove, then re-create it
#             pointing at this script).
param(
    [int]$RestartDelaySeconds = 10
)

$ErrorActionPreference = 'Continue'
$root = Split-Path $PSScriptRoot -Parent
$logDir = Join-Path $root 'data\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$supLog = Join-Path $logDir 'echo-supervisor.log'

# Hide this console window from inside the script.
# The Startup shortcut must NOT use -WindowStyle Hidden: this machine's policy
# blocks hidden powershell launches from shortcuts (the script would then never run
# at all - documented in launch-desktop.ps1). So the script hides its OWN console
# after starting, exactly like launch-desktop.ps1 does. Without this, the logon
# launch leaves a black "ECHO startup" console on screen forever, because the
# supervisor loop below never exits (reported by the user on 2026-09-12).
try {
    if (-not ('EchoConHider' -as [type])) {
        Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class EchoConHider { [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow(); [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow); }'
    }
    [EchoConHider]::ShowWindow([EchoConHider]::GetConsoleWindow(), 0) | Out-Null
    $script:hiddenConsole = $true
} catch {
    $script:hiddenConsole = $false
}

function SupLog([string]$message) {
    Add-Content -Path $supLog -Value ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message) -Encoding UTF8
}

SupLog "===== startup.ps1 begin (restartDelay=${RestartDelaySeconds}s, consoleHidden=$($script:hiddenConsole)) ====="

# ---- 1. DSH plugin registration self-heal (idempotent, never blocks ECHO) ----
$installer = Join-Path $PSScriptRoot 'install-echo-host-plugin.ps1'
if (Test-Path $installer) {
    try {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $installer -Quiet
        SupLog ("plugin self-heal exit=" + $LASTEXITCODE)
    } catch {
        SupLog "plugin self-heal threw: $_"
    }
}

# ---- 2/3. resolve pythonw and keep ECHO alive ----
$py = Join-Path $root 'venv\Scripts\python.exe'
$pyAlt = $env:ECHO_PYTHON   # 可选：非 ASCII 路径下的解释器覆盖，见 docs/DEPLOY.md
if ($pyAlt -and (Test-Path $pyAlt)) { $py = $pyAlt }
if (-not (Test-Path $py)) { SupLog "venv missing - cannot start ECHO: $py"; exit 1 }
$pyw = $py -replace 'python\.exe$', 'pythonw.exe'
if (-not (Test-Path $pyw)) { SupLog "pythonw missing: $pyw"; exit 1 }

$outLog = Join-Path $logDir 'echo-server.log'
$errLog = "$outLog.err"

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

# ECHO has its own anti-duplicate guard (it exits when the port is taken), so this
# probe-then-start dance can never race with a live instance.
function Start-EchoOnce {
    $p = Start-Process -FilePath $pyw -ArgumentList @('-m', 'app.main') `
        -WorkingDirectory $root -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog -PassThru
    return $p
}

if (Test-EchoPort) { SupLog "ECHO already listening on 8970" }
else { SupLog "ECHO not listening at logon - starting" }

while ($true) {
    if (Test-EchoPort) { Start-Sleep -Seconds 15; continue }
    SupLog "ECHO not listening - starting"
    try {
        $p = Start-EchoOnce
        SupLog "started pid=$($p.Id)"
    } catch {
        SupLog "start failed: $_"
    }
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        if (Test-EchoPort) { break }
    }
    if (-not (Test-EchoPort)) {
        SupLog "still not listening after 30s - retry in ${RestartDelaySeconds}s"
        Start-Sleep -Seconds $RestartDelaySeconds
    }
}
