# setup.ps1 — ECHO 一次性初始化（venv 校验 / 补装依赖 / 模型校验 / 建库）
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root 'venv\Scripts\python.exe'
# 若存在 ASCII junction（解决 nagisa/dynet 无法读中文路径的问题），优先使用
$pyAlt = $env:ECHO_PYTHON   # 可选：非 ASCII 路径下的解释器覆盖，见 docs/DEPLOY.md
if ($pyAlt -and (Test-Path $pyAlt)) { $py = $pyAlt }

Write-Host '=== ECHO setup ===' -ForegroundColor Cyan

# 1. venv
if (-not (Test-Path $py)) {
    Write-Host '[!] 未找到 venv。请按 docs/DEPLOY.md 创建：python -m venv venv 后装 requirements.txt' -ForegroundColor Yellow
    Write-Host "    python -m venv venv"
    exit 1
}
Write-Host "[1/4] venv OK: $py"

# 2. 补装轻量依赖（fastapi/uvicorn/pydantic；重依赖已随 venv 就位）
& $py -c "import fastapi, uvicorn, pydantic" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host '[2/4] 安装 fastapi/uvicorn/pydantic ...'
    & $py -m pip install --disable-pip-version-check -q fastapi "uvicorn[standard]" pydantic
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[!] 安装失败，尝试清华镜像...' -ForegroundColor Yellow
        & $py -m pip install --disable-pip-version-check -q -i https://pypi.tuna.tsinghua.edu.cn/simple fastapi "uvicorn[standard]" pydantic
    }
} else {
    Write-Host '[2/4] Web 依赖已就位'
}

# 3. 模型
$need = @('models\faster-whisper\small\model.bin', 'models\sensevoice', 'models\sherpa-onnx-streaming',
          'models\wakeword\kws-zh-en-3m\tokens.txt', 'models\pyannote\pyannote-segmentation-3.0-local')
$missing = @($need | Where-Object { -not (Test-Path (Join-Path $root $_)) })
if ($missing.Count -gt 0) {
    Write-Host "[!] 模型缺失（面板 → 设置 → 模型 里一键下载）:" -ForegroundColor Yellow
    $missing | ForEach-Object { Write-Host "    $_" -ForegroundColor Yellow }
} else {
    Write-Host '[3/4] 本地模型齐全（faster-whisper/SenseVoice/sherpa/KWS/pyannote）'
}

# 4. 建库 + 种子配置
Write-Host '[4/4] 初始化数据库...'
& $py -c "import app.db, app.config; app.db.init(); app.config.settings.seed_defaults(); print('DB OK:', app.db.DB_FILE)"
if ($LASTEXITCODE -ne 0) { Write-Host '[!] 数据库初始化失败' -ForegroundColor Red; exit 1 }

# 5. Deploy the DSH Desktop host plugin (echo-host): auto-starts/guards ECHO and
#    provides the Ctrl+Shift+E right-edge dashboard sidebar. Best-effort: a
#    machine without DSH Desktop installed still gets a working ECHO panel.
$installer = Join-Path $PSScriptRoot 'install-echo-host-plugin.ps1'
if (Test-Path $installer) {
    Write-Host '[5/5] Deploying the DSH Desktop host plugin (echo-host) ...'
    try {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $installer -Quiet
        if ($LASTEXITCODE -eq 0) { Write-Host '[5/5] echo-host plugin deployed' -ForegroundColor Green }
        else { Write-Host '[!] echo-host plugin deployment failed (ECHO panel still works standalone)' -ForegroundColor Yellow }
    } catch {
        Write-Host "[!] echo-host plugin deployment threw: $_" -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host '完成！启动:  scripts\start.ps1   （面板 http://127.0.0.1:8970）' -ForegroundColor Green
