# install-echo-host-plugin.ps1 - deploy / self-heal the ECHO host plugin for DSH Desktop
#
# Background (2026-09-12): DSH Desktop rebuilds resources\app.asar.unpacked on
# every upgrade. The 2.0.5 -> 2.0.9 upgrade therefore deleted both the patch
# file (cordis.patch.yml) and the deployed plugin copy, so echo-host silently
# stopped loading (no panel sidebar, no ECHO guard). This script redeploys both
# from the single source of truth (ECHO\plugin\echo-host) and verifies the
# resulting composition with DSH's own loader before declaring success.
#
# NOTE: this file is intentionally ASCII-only: Windows PowerShell 5.1 parses
# BOM-less .ps1 as ANSI/GBK, which corrupts non-ASCII text (see
# docs/powershell-encoding-and-scripts notes).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\install-echo-host-plugin.ps1
#   powershell ... -Quiet        # self-heal mode used on every ECHO launch
#   powershell ... -Uninstall    # remove patch + deployed copy
param(
    [switch]$Quiet,
    [switch]$Uninstall,
    [string]$ResourcesDir,
    [string]$ProfileName
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$sourceDir = Join-Path $root 'plugin\echo-host'
$checkScript = Join-Path $root 'plugin\deploy-check.cjs'

# Which profile does DSH Desktop actually compose? It reads
# %APPDATA%\DSH Desktop\profile-selection\state.json ("active") and then composes
# that profile's own patch layer. On this machine the active profile is "web",
# NOT "desktop" - a registration written only to profiles\desktop is never read,
# which is exactly how the plugin stayed absent after a restart.
function Resolve-ActiveProfileNames {
    $names = New-Object System.Collections.Generic.List[string]
    $statePath = Join-Path $env:APPDATA 'DSH Desktop\profile-selection\state.json'
    if (Test-Path $statePath) {
        try {
            $active = (Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json).active
            if ($active) { $names.Add([string]$active) }
        } catch { }
    }
    # Register in both surfaces so switching the active profile cannot drop the
    # plugin: Desktop's own "desktop" profile and the shipped "web" template.
    foreach ($fallback in @('desktop', 'web')) {
        if (-not $names.Contains($fallback)) { $names.Add($fallback) }
    }
    return $names
}

if ($ProfileName) { $profileNames = @($ProfileName) } else { $profileNames = @(Resolve-ActiveProfileNames) }
$profilesRoot = Join-Path $env:USERPROFILE '.dsh\profiles'
$profilePatches = @($profileNames | ForEach-Object { Join-Path (Join-Path $profilesRoot $_) 'cordis.patch.yml' })
$profilePatch = $profilePatches[0]   # primary (active) profile, used by the verifier

function Say([string]$msg, [string]$color = 'Gray') { if ($Quiet) { return } Write-Host $msg -ForegroundColor $color }
function Fail([string]$msg) { Write-Host "[echo-host] $msg" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- 1. locate DSH Desktop
function Resolve-ResourcesDir([string]$explicit) {
    $cands = @()
    if ($explicit) { $cands += $explicit }
    $cands += @(
        (Join-Path $env:LOCALAPPDATA 'Programs\DSH Desktop\resources'),
        (Join-Path $env:ProgramFiles 'DSH Desktop\resources'),
        (Join-Path ${env:ProgramFiles(x86)} 'DSH Desktop\resources')
    )
    foreach ($c in $cands) {
        if ($c -and (Test-Path (Join-Path $c 'app.asar'))) { return (Resolve-Path $c).Path }
    }
    return ''
}

$res = Resolve-ResourcesDir $ResourcesDir
if (-not $res) { Fail '[FAIL] DSH Desktop resources dir (app.asar) not found. Install DSH Desktop or pass -ResourcesDir.' }

$unpacked = Join-Path $res 'app.asar.unpacked'
$patchFile = Join-Path $unpacked 'cordis.patch.yml'
$deployDir = Join-Path $unpacked 'echo-host'
$deployEntry = Join-Path $deployDir 'index.js'
# Legacy 2.0.5-era overlay: DSH Desktop 2.0.5 read this file, 2.0.9 does not.
# The tool no longer writes it; it only records what lived there before and can
# clean it up on -Uninstall.
$patchBak = "$patchFile.bak-echo-dist"
$installRoot = $res
function Resolve-NodeExe {
    # A real node.exe is needed to run the composition check. The DSH cli cache
    # carries .cmd shims (not the binary itself in every layout), PATH may or may
    # not have node, and the installer itself must run under Windows PowerShell
    # 5.1, so probing both is deliberate.
    $cmd = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $cliRoot = Join-Path $env:APPDATA 'DSH Desktop\cli'
    if (Test-Path $cliRoot) {
        $found = Get-ChildItem $cliRoot -Recurse -Filter 'node.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    return $null
}

function Resolve-DesktopExe {
    # "DSH Desktop.exe" run with ELECTRON_RUN_AS_NODE=1 is an asar-aware node,
    # which is what the composition check needs to import DSH's own loader code.
    $cands = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\DSH Desktop\DSH Desktop.exe'),
        (Join-Path $env:ProgramFiles 'DSH Desktop\DSH Desktop.exe')
    )
    foreach ($c in $cands) { if ($c -and (Test-Path $c)) { return $c } }
    return $null
}

$node = Resolve-NodeExe
$desktopExe = Resolve-DesktopExe

Say '=== ECHO host plugin -> DSH Desktop ===' 'Cyan'
Say "  DSH resources : $res"
Say "  plugin source : $sourceDir"

if (-not (Test-Path (Join-Path $sourceDir 'index.js'))) { Fail "[FAIL] plugin source missing: $sourceDir\index.js" }
if (-not (Test-Path $unpacked)) { New-Item -ItemType Directory -Force -Path $unpacked | Out-Null }

# Drop the legacy app.asar.unpacked overlay this tool created for DSH 2.0.5.
# Only when it is recognizably ours and carries nothing else - DSH 2.0.9 never
# reads it, so leaving it behind only misleads the next reader.
if (Test-Path $patchFile) {
    $legacy = Get-Content -LiteralPath $patchFile -Raw -Encoding UTF8
    $isOurs = $legacy -match 'ECHO host plugin patch layer' -and
              ([regex]::Matches($legacy, '(?m)^\s*- insert:\s*$')).Count -eq 1 -and
              -not (Test-Path $patchBak)
    if ($isOurs) {
        Remove-Item -LiteralPath $patchFile -Force
        Say '  removed the obsolete app.asar.unpacked overlay (not read by DSH 2.0.9)'
    }
}

# Registration goes into the desktop profile's own patch layer, which Desktop
# composes after every bundle layer (app-boot's prepareDesktopProfile ->
# boot(patches)). DSH Desktop 2.0.9 does NOT read the app.asar.unpacked overlay
# that 2.0.5 used, which is why the plugin silently vanished on that upgrade -
# verified against the running Desktop's Loader inventory: the echo-host row was
# absent while every bundle row was present.
#
# The managed block is ASCII-only on purpose: Windows PowerShell 5.1 reads a
# BOM-less UTF-8 file as ANSI/GBK, which merges lines and corrupts the YAML, so
# non-ASCII comment text must never be read back from this file.
$markerStart = '# >>> echo-host plugin (managed by ECHO install-echo-host-plugin.ps1) >>>'
$markerEnd = '# <<< echo-host plugin <<<'
# A file URL keeps the row independent of the patch file's own directory and
# escapes spaces in "...\DSH Desktop\...".
$pluginUrl = ([uri]("file:///" + ($deployEntry -replace '\\', '/'))).AbsoluteUri

# The managed block, written into every profile patch layer found above.
$block = @(
    $markerStart
    '# Registration row for the ECHO host plugin. Generated - manual edits are overwritten.'
    '- insert:'
    '    - id: echo-host'
    "      name: $pluginUrl"
    $markerEnd
)

# Remove the managed block from one profile patch file. Returns $true when the
# file changed. Shared by -Uninstall and by the install pass (to re-add cleanly).
function Remove-ManagedBlock([string]$patchPath) {
    if (-not (Test-Path $patchPath)) { return $false }
    $lines = @(Get-Content -LiteralPath $patchPath -Encoding UTF8)
    $at = [array]::IndexOf([string[]]$lines, $markerStart)
    if ($at -lt 0) { return $false }
    $out = New-Object System.Collections.Generic.List[string]
    $skipping = $false
    foreach ($line in $lines) {
        if ($line -eq $markerStart) { $skipping = $true; continue }
        if ($line -eq $markerEnd) { $skipping = $false; continue }
        if (-not $skipping) { $out.Add($line) }
    }
    Set-Content -LiteralPath $patchPath -Value $out -Encoding UTF8
    return $true
}

# Parse one YAML file with the yaml library shipped in the Desktop harness (or a
# sibling harness checkout). Returns $true when it parses, and prints the parser
# error when it does not. A profile patch that cannot parse makes DSH fail loud
# at boot, so nothing is written before this passes.
function Test-PatchYaml([string]$file) {
    if (-not $node) { return $true }   # no node: step 5 still checks with DSH's own parser
    $yamlDir = $null
    $candidates = @(
        (Join-Path $res 'app.asar.unpacked\node_modules\yaml'),
        (Join-Path $env:APPDATA 'DSH Desktop'),
        # Sibling harness checkout used on this machine. "Desktop\<4E60><4E66>"
        # (the Chinese folder name) is built from code points so this file stays
        # pure ASCII: PS 5.1 parses a BOM-less .ps1 as ANSI and would mangle it.
        (Join-Path $env:USERPROFILE ('Desktop\' + [char]0x5B66 + [char]0x4E60 + '\DSH\node_modules'))
    )
    foreach ($c in $candidates) {
        if (Test-Path (Join-Path $c 'yaml\package.json')) { $yamlDir = (Join-Path $c 'yaml'); break }
    }
    if (-not $yamlDir) { return $true }   # library not found: step 5 is authoritative
    $script = @'
const fs = require("node:fs");
const YAML = require(process.argv[3]);
try {
  YAML.parse(fs.readFileSync(process.argv[2], "utf8"));
  process.exit(0);
} catch (cause) {
  console.log(String(cause && cause.message).split("\n").slice(0, 4).join(" | "));
  process.exit(1);
}
'@
    $scriptFile = Join-Path $env:TEMP "echo-host-yamlcheck-$PID.cjs"
    Set-Content -LiteralPath $scriptFile -Value $script -Encoding ASCII
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $node $scriptFile $file $yamlDir 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $old
        Remove-Item -LiteralPath $scriptFile -Force -ErrorAction SilentlyContinue
    }
    if ($code -ne 0) { foreach ($line in $out) { Say ("  YAML: " + $line) 'Red' } }
    return ($code -eq 0)
}

# Insert or refresh the managed block in one profile patch file.
function Set-ManagedBlock([string]$patchPath) {
    $dir = Split-Path $patchPath -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $existingLines = @()
    if (Test-Path $patchPath) { $existingLines = @(Get-Content -LiteralPath $patchPath -Encoding UTF8) }

    # The shipped profile template ends with a bare "[]" (an empty patch list). A
    # flow sequence cannot be an ITEM of a block sequence, so leaving that line in
    # place while appending "- insert:" makes the whole file an invalid YAML stream
    # ("Unexpected seq-item-ind token", confirmed with the yaml parser). Turn the
    # no-op default into a comment instead of a value.
    $converted = $false
    for ($i = 0; $i -lt $existingLines.Count; $i++) {
        if ($existingLines[$i] -eq '[]' -or $existingLines[$i] -eq '--- []') {
            $existingLines[$i] = '# (empty default removed - this layer now carries the echo-host row below)'
            $converted = $true
        }
    }
    $at = [array]::IndexOf([string[]]$existingLines, $markerStart)
    if ($at -ge 0) {
        $endAt = [array]::IndexOf([string[]]$existingLines, $markerEnd, $at)
        $merged = @()
        if ($at -gt 0) { $merged += $existingLines[0..($at - 1)] }
        $merged += $block
        if ($endAt -ge 0 -and ($endAt + 1) -le ($existingLines.Count - 1)) { $merged += $existingLines[($endAt + 1)..($existingLines.Count - 1)] }
    } else {
        # Drop trailing blank lines so the appended block starts on a clean line.
        $trimmed = @($existingLines)
        while ($trimmed.Count -gt 0 -and [string]::IsNullOrWhiteSpace($trimmed[-1])) {
            $trimmed = @($trimmed[0..($trimmed.Count - 2)])
        }
        $merged = @($trimmed) + @('') + $block
    }
    # Validate the merged YAML before replacing the live file: a profile patch
    # that cannot parse makes DSH Desktop fail loud at boot.
    $tmp = "$patchPath.tmp-$PID"
    Set-Content -LiteralPath $tmp -Value $merged -Encoding UTF8
    $ok = Test-PatchYaml $tmp
    if (-not $ok) {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        Fail "[FAIL] refusing to write an unparsable profile patch: $patchPath (see the YAML error above)"
    }
    Move-Item -LiteralPath $tmp -Destination $patchPath -Force
    return $converted
}

# ---------------------------------------------------------------- 2. uninstall
if ($Uninstall) {
    foreach ($patchPath in $profilePatches) {
        if (Remove-ManagedBlock $patchPath) { Say "  removed the echo-host row from $patchPath" 'Yellow' }
    }
    if (Test-Path $deployDir) { Remove-Item $deployDir -Recurse -Force }
    # Legacy 2.0.5-era overlay: drop it if this tool created it.
    if (Test-Path $patchFile) {
        if (Test-Path $patchBak) { Copy-Item $patchBak $patchFile -Force }
        else { Remove-Item $patchFile -Force }
        Say '  cleaned the legacy app.asar.unpacked overlay' 'Yellow'
    }
    Say '  uninstalled (plugin stops loading after the next DSH Desktop restart)' 'Yellow'
    exit 0
}

# ---------------------------------------------------------------- 3. deploy plugin files
New-Item -ItemType Directory -Force -Path $deployDir | Out-Null
Get-ChildItem -Path $deployDir -File -Force -ErrorAction SilentlyContinue | Remove-Item -Force
Copy-Item (Join-Path $sourceDir '*') $deployDir -Recurse -Force
# Tell the plugin where this checkout lives: the deployed copy cannot deduce it, so we write a
# marker file next to index.js (the plugin reads ECHO_ROOT env, then echo-root.txt, then a
# placeholder). Without this the plugin would point at a non-existent path after a fresh clone.
Set-Content -Path (Join-Path $deployDir 'echo-root.txt') -Value $root -Encoding UTF8 -NoNewline
Add-Content -Path (Join-Path $deployDir 'README-DEPLOYED.txt') `
    -Value "Deployed from $sourceDir at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') by scripts\install-echo-host-plugin.ps1. Do not edit this copy." -Encoding UTF8
$buildMatch = Select-String -Path (Join-Path $deployDir 'index.js') -Pattern 'ECHO_HOST_BUILD\s*=\s*"([^"]+)"' | Select-Object -First 1
$deployedBuild = if ($buildMatch) { $buildMatch.Matches.Groups[1].Value } else { 'unknown' }
Say "  deployed plugin copy in $deployDir (build=$deployedBuild)" 'Green'

# ---------------------------------------------------------------- 4. register in every profile patch layer
foreach ($patchPath in $profilePatches) {
    $hadBlock = (Test-Path $patchPath) -and ((Get-Content -LiteralPath $patchPath -Encoding UTF8) -contains $markerStart)
    [void](Set-ManagedBlock $patchPath)
    $verb = if ($hadBlock) { 'refreshed' } else { 'added' }
    Say "  $verb the echo-host row in $patchPath" 'Green'
}

# ---------------------------------------------------------------- 5. verify the composition with DSH's own loader
$verifyFailed = $false
try {
    if ((Test-Path $checkScript) -and ($desktopExe -or $node)) {
        # A GUI-subsystem exe (DSH Desktop.exe) detaches from the PowerShell
        # pipeline, so run it through cmd.exe with redirected output; that is the
        # only form that returns both stdout and the exit code reliably.
        $outFile = Join-Path $env:TEMP 'echo-host-deploy-check.out'
        Remove-Item $outFile -Force -ErrorAction SilentlyContinue
        if ($desktopExe) {
            # Preferred: Electron in Node mode has asar support, so the check can
            # import dsh-app-boot from inside app.asar.
            cmd /c "set ELECTRON_RUN_AS_NODE=1&& `"$desktopExe`" `"$checkScript`" `"$res`" `"$profilePatch`" > `"$outFile`" 2>&1"
        } else {
            cmd /c "`"$node`" `"$checkScript`" `"$res`" `"$profilePatch`" > `"$outFile`" 2>&1"
        }
        $code = $LASTEXITCODE
        if (Test-Path $outFile) { foreach ($line in (Get-Content $outFile)) { Say ("  " + $line) } }
        if ($code -ne 0) { $verifyFailed = $true }
    } else {
        Say '  [SKIP] DSH Desktop.exe / node.exe or deploy-check.cjs not found; composition check skipped'
    }
} catch {
    Say ("  [WARN] composition check threw: " + $_.Exception.Message) 'Yellow'
    $verifyFailed = $true
}
if ($verifyFailed) { Fail '[FAIL] composition check failed - the patch may be invalid. See the output above.' }

Say ''
Say '  Done. The plugin loads on the next DSH Desktop start:' 'Green'
Say '    - guards/auto-starts ECHO'
Say '    - Ctrl+Shift+E expands/collapses the right-edge dashboard sidebar'
Say '  DSH Desktop must be restarted: the profile patch layer is composed at start.' 'Yellow'
