# new-machine-setup.ps1 — 新机器一键初始化（给拷贝过来的 ECHO 用）
#
# 场景：别人把整个 ECHO 目录（含 venv 与 models）拷给你，首次在本机运行。
# 做的事：
#   [1/4] venv 基础解释器定位修复（venv/pyvenv.cfg 里的 home 指向源机器的 Python，
#         本机不存在会导致 "Fatal Python error: init_fs_encoding"）
#   [2/4] venv 可用性校验（python 版本 + fastapi/uvicorn/pydantic + torch/GPU）
#   [3/4] 调 scripts\setup.ps1（补装依赖 + 模型校验 + 建库）
#   [4/4] 打印后续步骤
#
# 用法：
#   cd <ECHO 根目录>
#   powershell -ExecutionPolicy Bypass -File scripts\new-machine-setup.ps1
#   可选：-SkipSetup 只修 venv 不跑 setup.ps1；-Root <路径> 指定 ECHO 根目录
param(
    [switch]$SkipSetup,
    [string]$Root = (Split-Path $PSScriptRoot -Parent)
)

$ErrorActionPreference = 'Stop'

function Say-Ok($m)   { Write-Host $m -ForegroundColor Green }
function Say-Warn($m) { Write-Host $m -ForegroundColor Yellow }
function Say-Err($m)  { Write-Host $m -ForegroundColor Red }

Write-Host '=== ECHO 新机器初始化 ===' -ForegroundColor Cyan
Write-Host "ECHO 根目录: $Root"

# ---------------------------------------------------------------- 0. 路径检查
if ($Root -match '[^\x00-\x7F]') {
    Say-Warn '[!] 安装路径含非 ASCII 字符（中文等）。'
    Say-Warn '    funasr / nagisa / dynet 无法读取非 ASCII 路径，SenseVoice 转写会失败。'
    Say-Warn "    建议整体移动到纯英文路径后重跑，例如：D:\ECHO"
}

$venvPy = Join-Path $Root 'venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    Say-Err "[1/4] 未找到 venv：$venvPy"
    Say-Err '      说明这个包没带 venv（纯代码包），请改用附录 B 从零建环境，'
    Say-Err '      或让对方把 venv 目录一起拷过来。'
    exit 1
}

# ---------------------------------------------------------------- 1. 定位基础 Python
function Find-BasePython {
    # a) uv 管理的 CPython 3.11（源机器就是这种，装到 %APPDATA%\uv\python 下）
    $uvRoot = Join-Path $env:APPDATA 'uv\python'
    $cands = @()
    $cands += (Join-Path $uvRoot 'cpython-3.11.15-windows-x86_64-none\python.exe')
    if (Test-Path $uvRoot) {
        $cands += (Get-ChildItem $uvRoot -Directory -ErrorAction SilentlyContinue |
                   Where-Object { $_.Name -like 'cpython-3.11*' } |
                   ForEach-Object { Join-Path $_.FullName 'python.exe' })
    }
    foreach ($c in $cands) { if ($c -and (Test-Path $c)) { return $c } }

    # b) 有 uv 就地装一个 3.11.15
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Say-Warn '    本机没有 Python 3.11，尝试 uv python install 3.11.15 ...'
        try { & uv python install 3.11.15 2>$null | Out-Null } catch { }
        $p = Join-Path $uvRoot 'cpython-3.11.15-windows-x86_64-none\python.exe'
        if (Test-Path $p) { return $p }
    }

    # c) py launcher 里已有的 3.11
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            $p = (& py -3.11 -c "import sys;print(sys.executable)" 2>$null | Select-Object -First 1)
            if ($p -and (Test-Path $p)) { return $p }
        } catch { }
    }
    return ''
}

$cfg = Join-Path $Root 'venv\pyvenv.cfg'
$pyHome = ''      # 别用 $home：PowerShell 里 $HOME 是只读自动变量
if (Test-Path $cfg) {
    $line = Get-Content $cfg -ErrorAction SilentlyContinue |
            Where-Object { $_ -match '^\s*home\s*=' } | Select-Object -First 1
    if ($line) { $pyHome = ($line -replace '^\s*home\s*=\s*', '').Trim() }
}

$homeOk = $false
if ($pyHome) { $homeOk = Test-Path (Join-Path $pyHome 'python.exe') }

if ($homeOk) {
    Say-Ok "[1/4] venv 基础解释器有效: $pyHome"
} else {
    if ($pyHome) { Say-Warn "[1/4] venv 的 home 指向本机不存在的解释器: $pyHome" }
    else       { Say-Warn '[1/4] venv\pyvenv.cfg 缺少 home 行' }
    $base = Find-BasePython
    if (-not $base) {
        Say-Err '      本机找不到 Python 3.11，无法修复 venv。请任选一种装好后重跑：'
        Say-Err '        A) 装 uv 后执行： uv python install 3.11.15        （推荐，与源机器同版本）'
        Say-Err '        B) 装 Python 3.11.x（python.org），勾选 Add to PATH'
        exit 1
    }
    $baseDir = Split-Path $base -Parent
    Copy-Item $cfg "$cfg.bak-$(Get-Date -Format yyyyMMdd-HHmmss)" -Force
    $lines = @(Get-Content $cfg)
    if ($lines | Where-Object { $_ -match '^\s*home\s*=' }) {
        $lines = $lines | ForEach-Object {
            if ($_ -match '^\s*home\s*=') { "home = $baseDir" } else { $_ }
        }
    } else {
        $lines = @("home = $baseDir") + $lines
    }
    Set-Content -Path $cfg -Value $lines -Encoding ASCII
    Say-Ok "[1/4] 已修正 venv\pyvenv.cfg：home = $baseDir （原文件已备份为 .bak-*）"
}

# ---------------------------------------------------------------- 2. venv 校验
# 说明：WinPS 5.1 在 $ErrorActionPreference='Stop' 下，原生命令的 stderr（2>&1）
# 会升级成终止性错误，所以探测一律用本函数（内部临时切 Continue 并捕获输出）。
function Invoke-VenvPy([string]$code) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $raw = (& $venvPy -c $code 2>&1 | Out-String)
        # 去掉 WinPS 的 NativeCommandError 包装（"At 行:字符" / "+ CategoryInfo" 等），
        # 只保留 Python 自己的报错文本
        $keep = ($raw -split "`r?`n") | Where-Object {
            $_.Trim() -and $_ -notmatch '^(At |所在位置|\s*\+|pythonw?\.exe\s*:)' -and
            $_ -notmatch 'CategoryInfo|FullyQualifiedErrorId'
        }
        $out = ($keep -join ' ').Trim()
        if ($out.Length -gt 300) { $out = $out.Substring(0, 300) + ' ...' }
        return @{ code = $LASTEXITCODE; out = $out }
    } finally { $ErrorActionPreference = $prev }
}

$r = Invoke-VenvPy "import sys;print(sys.version.split()[0])"
if ($r.code -ne 0) {
    Say-Err "[2/4] venv 仍然不可用：$($r.out)"
    Say-Err '      常见原因：基础 Python 与 venv 小版本差异过大（venv 由 3.11.15 建），'
    Say-Err '      或 venv 拷贝不完整（缺少 Lib\site-packages）。'
    exit 1
}
Say-Ok "[2/4] venv Python 可用：$($r.out)"

$r = Invoke-VenvPy "import fastapi, uvicorn, pydantic; print('ok')"
if ($r.code -ne 0) { Say-Warn "      Web 依赖缺失（setup.ps1 会自动补装）: $($r.out)" }
else { Say-Ok '      Web 依赖 OK（fastapi/uvicorn/pydantic）' }

$r = Invoke-VenvPy "import torch;print(torch.__version__, torch.cuda.is_available())"
if ($r.code -ne 0) {
    Say-Warn "      torch 不可用：$($r.out)"
    Say-Warn '      → 不是致命问题，但转写会退化为 CPU 或不可用；可用 python -m pip 补装'
} else {
    Say-Ok "      torch $($r.out)  （第二项 True=GPU 可用，False=走 CPU）"
}

# ---------------------------------------------------------------- 3. setup.ps1
if ($SkipSetup) {
    Say-Warn '[3/4] 已跳过 setup.ps1（-SkipSetup）'
} else {
    Write-Host '[3/4] 运行 scripts\setup.ps1 ...' -ForegroundColor Cyan
    # setup.ps1 内部要 import app.*，必须在 ECHO 根目录执行（脚本自身不切目录）
    Push-Location $Root
    try {
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root 'scripts\setup.ps1')
    } finally { Pop-Location }
    $dbFile = Join-Path $Root 'data\echo.db'
    if (Test-Path $dbFile) { Say-Ok "      数据库已建立: $dbFile" }
    else { Say-Warn '      未发现 data\echo.db，setup.ps1 可能未跑完，请检查上面的输出' }
}

# ---------------------------------------------------------------- 4. 后续步骤
Write-Host ''
Say-Ok '[4/4] 初始化结束，接下来：'
Write-Host '  1) 启动 DSH Desktop，并在 设置 → 常规 打开「普通浏览器访问」'
Write-Host '  2) 启动 ECHO（日常用桌面快捷方式）：'
Write-Host "       cd $Root        # 必须先切到 ECHO 根目录，脚本不会自己切"
Write-Host '       powershell -ExecutionPolicy Bypass -File scripts\start.ps1            # 前台调试'
Write-Host '       powershell -ExecutionPolicy Bypass -File scripts\install-desktop-shortcut.ps1   # 桌面一键启动'
Write-Host '  3) 打开面板（端口见 data\echo-port.txt），在 设置 里把「会议纪要工作区」改成当前路径'
Write-Host '  4) 无 NVIDIA 显卡时：设置 → 通用 → 计算设备 改为 cpu'
Write-Host ''
Write-Host '提示：请用 python -m pip 安装依赖，不要用 venv\Scripts\pip.exe（其中写死了源机器路径）。'
