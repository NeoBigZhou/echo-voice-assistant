# install-qwen3asr.ps1 — 安装 Qwen3-ASR 会议转写引擎（依赖 + 模型下载）
# 依赖：qwen-asr==0.0.6 + transformers==4.57.6（会从 5.x 降级，仅影响 Qwen3-ASR 相关）
# 模型：Qwen/Qwen3-ASR-0.6B（~4GB 显存，modelscope 缓存，无中文路径问题）
$ErrorActionPreference = 'Continue'
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root 'venv\Scripts\python.exe'
# 若存在 ASCII junction（解决 nagisa/dynet 无法读中文路径的问题），优先使用
$pyAlt = $env:ECHO_PYTHON
    # 可选：非 ASCII 路径下的解释器覆盖，见 docs/DEPLOY.md
if ($pyAlt -and (Test-Path $pyAlt)) { $py = $pyAlt }
if (-not (Test-Path $py)) { Write-Host '缺少 venv' -ForegroundColor Red; exit 1 }

Write-Host '[1/2] 安装 qwen-asr 依赖（清华镜像）...' -ForegroundColor Cyan
& $py -m pip install --disable-pip-version-check -i https://pypi.tuna.tsinghua.edu.cn/simple `
    "qwen-asr==0.0.6" "transformers==4.57.6" "accelerate==1.12.0"
if ($LASTEXITCODE -ne 0) {
    Write-Host '[!] 依赖安装失败' -ForegroundColor Red
    exit 1
}

Write-Host '[2/3] 下载 Qwen/Qwen3-ASR-0.6B 模型（modelscope）...' -ForegroundColor Cyan
& $py -c "from modelscope import snapshot_download; p = snapshot_download('Qwen/Qwen3-ASR-0.6B'); print('模型就绪:', p)"
if ($LASTEXITCODE -ne 0) {
    Write-Host '[!] 模型下载失败（可稍后重试，模型已下部分会续传）' -ForegroundColor Red
    exit 1
}

Write-Host '[3/3] 下载 Qwen/Qwen3-ForcedAligner-0.6B（句子时间戳对齐器）...' -ForegroundColor Cyan
& $py -c "from modelscope import snapshot_download; p = snapshot_download('Qwen/Qwen3-ForcedAligner-0.6B'); print('对齐器就绪:', p)"
if ($LASTEXITCODE -ne 0) {
    Write-Host '[!] 对齐器下载失败（转写仍可用，但句子时间戳会退回 whisper 骨架）' -ForegroundColor Yellow
}

Write-Host ''
Write-Host '完成！在面板 设置→会议→会议转写模型 选择 qwen3asr 即可使用' -ForegroundColor Green
