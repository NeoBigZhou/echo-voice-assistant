# -*- coding: utf-8 -*-
"""stt.py — ECHO 语音转文字（离线，自动 GPU）

引擎：
  whisper    faster-whisper（base/small/medium/large-v3），本地模型在 models/faster-whisper/
  sensevoice funasr SenseVoiceSmall（中文短命令最快、自带标点），本地模型在 models/sensevoice/
  sherpa     sherpa-onnx 流式 zipformer 中英（CPU 实时），本地模型在 models/sherpa-onnx-streaming/

进程内单例（懒加载）：
  长期驻留进程对同一 CUDA 模型反复加载会因 ctranslate2/torch 动态库顺序冲突
  （WinError 127）崩溃 —— 每个引擎只加载一次，失败自动回退 CPU（已实测验证）。

CLI：python -m app.audio.stt <wav> [--engine ...] [--model ...] [--lang ...]
"""
import os
import re
import sys
import threading

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODELS_DIR = os.path.join(BASE_DIR, "models")
os.environ.setdefault("HF_HOME", MODELS_DIR)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")

MODEL_ALIASES = {"large": "large-v3"}
WHISPER_MODELS = {"tiny", "base", "small", "medium", "large", "large-v3"}

# Qwen3-ASR 语言提示（funasr/qwen-asr 需要全称，如 "Chinese"）
_LANG_MAP = {"zh": "Chinese", "zh-cn": "Chinese", "en": "English"}

# ---------------------------------------------------------------- 设备解析

def resolve_device(choice="auto"):
    if choice == "cpu":
        return "cpu", "int8"
    if choice == "cuda":
        return "cuda", "float16"
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


# ---------------------------------------------------------------- 本地模型定位

def _whisper_dir(model_name):
    name = MODEL_ALIASES.get(model_name, model_name)
    d = os.path.join(MODELS_DIR, "faster-whisper", name)
    return d if os.path.isfile(os.path.join(d, "model.bin")) else ""


def _sensevoice_dir():
    """models/sensevoice 下可能有多层 snapshots/<hash>，自动下探找到 model.pt。

    注意：funasr 在 Windows 上无法加载含非 ASCII 字符（如中文）的本地路径，
    遇到这种情况返回 ""，让调用方走模型名（iic/SenseVoiceSmall，落 modelscope 缓存）。
    """
    root = os.path.join(MODELS_DIR, "sensevoice")
    if not os.path.isdir(root):
        return ""
    for dirpath, _dirs, files in os.walk(root):
        if "model.pt" in files or "model.pb" in files:
            if any(ord(ch) > 127 for ch in dirpath):
                return ""
            return dirpath
    if any(ord(ch) > 127 for ch in root):
        return ""
    return root


def _sherpa_files():
    d = os.path.join(MODELS_DIR, "sherpa-onnx-streaming")
    if not os.path.isdir(d):
        return None
    enc = next((f for f in os.listdir(d) if f.startswith("encoder") and f.endswith(".onnx")), "")
    dec = next((f for f in os.listdir(d) if f.startswith("decoder") and f.endswith(".onnx")), "")
    joi = next((f for f in os.listdir(d) if f.startswith("joiner") and f.endswith(".onnx")), "")
    tok = os.path.join(d, "tokens.txt")
    if not (enc and dec and joi and os.path.isfile(tok)):
        return None
    return (os.path.join(d, enc), os.path.join(d, dec), os.path.join(d, joi), tok)


# ---------------------------------------------------------------- 引擎单例

_ENGINES = {}
_ENGINE_LOCK = threading.Lock()


def _clean_sv_text(t):
    return re.sub(r"<\|[^|]*\|>", "", t or "").strip()


def _get_whisper(model_name="small", device="auto"):
    key = f"whisper:{model_name}"
    with _ENGINE_LOCK:
        if key in _ENGINES:
            return _ENGINES[key]
        from faster_whisper import WhisperModel
        model_ref = _whisper_dir(model_name) or model_name
        try:
            dev, compute = resolve_device(device)
            model = WhisperModel(model_ref, device=dev, compute_type=compute)
        except Exception as e:
            print(f"[stt] {model_name}@{device} 加载失败，回退 CPU int8: {e}", file=sys.stderr)
            model = WhisperModel(model_ref, device="cpu", compute_type="int8")
        _ENGINES[key] = model
        _cache_gpu_name()   # ctranslate2 已加载，此时导入 torch 顺序安全
        return model


def _get_sensevoice(device="auto"):
    key = "sensevoice"
    with _ENGINE_LOCK:
        if key in _ENGINES:
            return _ENGINES[key]
        os.environ["TQDM_DISABLE"] = "1"
        os.environ.setdefault("MODELSCOPE_DISABLE_PROGRESS_BAR", "1")
        from funasr import AutoModel
        use_cuda = False
        try:
            import torch
            use_cuda = device != "cpu" and torch.cuda.is_available()
        except Exception:
            use_cuda = False
        dev = "cuda:0" if use_cuda else "cpu"
        model_dir = _sensevoice_dir()
        kwargs = dict(model=model_dir or "iic/SenseVoiceSmall",
                      vad_model="fsmn-vad",
                      vad_kwargs={"max_single_segment_time": 30000},
                      device=dev, disable_update=True)
        try:
            model = AutoModel(**kwargs)
        except Exception as e:
            print(f"[stt] SenseVoice 加载失败，回退 CPU: {e}", file=sys.stderr)
            kwargs["device"] = "cpu"
            model = AutoModel(**kwargs)
        _ENGINES[key] = model
        _cache_gpu_name()
        return model


def _get_sherpa():
    key = "sherpa"
    with _ENGINE_LOCK:
        if key in _ENGINES:
            return _ENGINES[key]
        files = _sherpa_files()
        if not files:
            raise RuntimeError("sherpa 流式模型未就绪: " + os.path.join(MODELS_DIR, "sherpa-onnx-streaming"))
        import sherpa_onnx
        enc, dec, joi, tok = files
        recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=tok, encoder=enc, decoder=dec, joiner=joi,
            num_threads=2, sample_rate=16000, feature_dim=80,
            enable_endpoint_detection=True,
            rule1_min_trailing_silence=0.8,
            rule2_min_trailing_silence=0.5,
            rule3_min_utterance_length=10,
        )
        _ENGINES[key] = recognizer
        return recognizer


def _get_qwen3asr(device="auto", model_name="Qwen/Qwen3-ASR-0.6B", forced_aligner=None):
    """funasr Qwen3-ASR（qwen-asr 包，52 语言，中文准确率高于 SenseVoice）。

    依赖：qwen-asr==0.0.6 + transformers==4.57.6（scripts/setup.ps1 或 README 有说明）。
    模型经 modelscope 缓存（~/.cache/modelscope，无中文路径，funasr 可直接加载）。
    显存：0.6B ~4GB，1.7B ~8GB（GPU 不足自动回退 CPU）。
    forced_aligner: "Qwen/Qwen3-ForcedAligner-0.6B" 时启用字符级时间戳（会议转写原生句子）。
    """
    key = f"qwen3asr:{model_name}:{forced_aligner or ''}"
    with _ENGINE_LOCK:
        if key in _ENGINES:
            return _ENGINES[key]
        from funasr import AutoModel
        use_cuda = False
        try:
            import torch
            use_cuda = device != "cpu" and torch.cuda.is_available()
        except Exception:
            use_cuda = False
        dev = "cuda:0" if use_cuda else "cpu"
        dtype = "bf16" if use_cuda else "fp32"
        # model 保留模型名（funasr 注册表查找类），model_path 用本地快照路径（离线加载，跳过联网检查）
        model_path = _resolve_ms_cache(model_name)
        aligner_path = _resolve_ms_cache(forced_aligner) if forced_aligner else None
        kwargs = dict(model=model_name, hub="ms", device=dev, dtype=dtype, disable_update=True)
        if model_path and model_path != model_name:
            kwargs["model_path"] = model_path
        if aligner_path:
            kwargs["forced_aligner"] = aligner_path
        try:
            model = AutoModel(**kwargs)
        except Exception as e:
            print(f"[stt] Qwen3-ASR 加载失败，回退 CPU: {e}", file=sys.stderr)
            kwargs["device"] = "cpu"
            kwargs["dtype"] = "fp32"
            model = AutoModel(**kwargs)
        _ENGINES[key] = model
        _cache_gpu_name()
        return model


def _resolve_ms_cache(model_id):
    """把 modelscope 模型名解析为本地快照路径（存在则返回，否则返回原名）。"""
    if not model_id:
        return model_id
    import os as _os
    cache = _os.path.expanduser(
        f"~/.cache/modelscope/models/{model_id.replace('/', '--')}/snapshots/master")
    return cache if _os.path.isdir(cache) else model_id


# ---------------------------------------------------------------- 转写入口

def _qwen3asr_sentences(m, wav, lang_hint):
    """Qwen3-ASR + ForcedAligner：返回 (完整文本, [(start, end, 句子), ...])。

    funasr 返回的 timestamp 是「token 级」[start_sec, end_sec]（长度=token 数，
    通常 < 文本字符数），这里按字符位置比例映射到 token 时间轴，再按句末
    标点聚合成自然句子。失败返回 ("", [])。
    """
    try:
        res = m.generate(input=wav, language=lang_hint, return_time_stamps=True)
        if not res:
            return "", []
        text = (res[0].get("text") or "").strip()
        ts = res[0].get("timestamp") or []
        if not text:
            return "", []
        if not ts:
            return text, [(0.0, 0.0, text)]
        n_tok, n_ch = len(ts), len(text)

        def tok_idx(i):
            return min(n_tok - 1, round(i * n_tok / max(1, n_ch - 1)))

        sentences = []
        cur = []
        seg_start = None
        last_end = 0.0
        for i, ch in enumerate(text):
            t = ts[tok_idx(i)]
            s, e = float(t[0]), float(t[1])
            last_end = e
            if seg_start is None:
                seg_start = s
            cur.append(ch)
            if ch in "。！？…!?；;":
                txt = "".join(cur).strip()
                if txt:
                    sentences.append((seg_start, e, txt))
                cur = []
                seg_start = None
        if cur:
            txt = "".join(cur).strip()
            if txt:
                sentences.append((seg_start, last_end, txt))
        return text, sentences
    except Exception as e:
        print(f"[stt] Qwen3-ASR 时间戳转写失败: {e}", file=sys.stderr)
        return "", []

def transcribe(wav, engine="sensevoice", model="small", lang="zh", device="auto"):
    """转写单个 wav，返回文本（失败返回空串并打印 stderr）。"""
    if not os.path.isfile(wav):
        return ""

    if engine == "sensevoice":
        try:
            sv = _get_sensevoice(device)
            res = sv.generate(input=wav, cache={}, language="auto", use_itn=True, batch_size_s=60)
            if not res:
                return ""
            return _clean_sv_text(res[0].get("text", ""))
        except Exception as e:
            print(f"[stt] SenseVoice 转写失败: {e}", file=sys.stderr)
            return ""

    if engine == "sherpa":
        try:
            import numpy as np
            import wave as wave_mod
            rec = _get_sherpa()
            stream = rec.create_stream()
            with wave_mod.open(wav, "rb") as f:
                sr = f.getframerate()
                data = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
            chunk = 5120
            tail = np.zeros(int(0.6 * sr), dtype=np.float32)
            samples = np.concatenate([data, tail])
            for i in range(0, len(samples), chunk):
                stream.accept_waveform(sr, samples[i:i + chunk])
                while rec.is_ready(stream):
                    rec.decode_stream(stream)
            r = rec.get_result(stream)
            return (r if isinstance(r, str) else r.text).strip()
        except Exception as e:
            print(f"[stt] sherpa 转写失败: {e}", file=sys.stderr)
            return ""

    if engine == "qwen3asr":
        try:
            # model 参数默认是 whisper 的 "small"，这里要解析成 Qwen3-ASR 模型名
            if model.startswith("Qwen/"):
                qwen_model = model
            elif model in ("0.6B", "1.7B"):
                qwen_model = f"Qwen/Qwen3-ASR-{model}"
            else:
                qwen_model = "Qwen/Qwen3-ASR-0.6B"
            m = _get_qwen3asr(device, qwen_model)
            lang_hint = _LANG_MAP.get(str(lang).lower(), None)
            res = m.generate(input=wav, language=lang_hint)
            if not res:
                return ""
            return " ".join((res[0].get("text") or "").split())
        except Exception as e:
            print(f"[stt] Qwen3-ASR 转写失败: {e}", file=sys.stderr)
            return ""

    # faster-whisper
    try:
        wm = _get_whisper(model, device)
        segments, _info = wm.transcribe(
            wav, language=lang, vad_filter=True, beam_size=5,
            initial_prompt="以下是普通话的日常对话片段。")
        text = "".join(seg.text for seg in segments).strip()
        return " ".join(text.split())
    except Exception as e:
        print(f"[stt] whisper 转写失败: {e}", file=sys.stderr)
        return ""


def reset_engines():
    """卸载全部引擎（改配置/释放显存后调用）。"""
    with _ENGINE_LOCK:
        _ENGINES.clear()


# ---------------------------------------------------------------- 引擎状态

# GPU 型号名缓存：引擎加载完成后（torch 已安全导入）再取，请求路径不碰 torch
_GPU_NAME = ""


def _cache_gpu_name():
    """在 ctranslate2 已加载（顺序安全）后缓存 GPU 型号名，供 engine_status 展示。"""
    global _GPU_NAME
    if _GPU_NAME:
        return
    try:
        import torch
        if torch.cuda.is_available():
            _GPU_NAME = torch.cuda.get_device_name(0).split()[-1]
    except Exception:
        pass


# 设备信息缓存（请求路径热读，避免每次 /api/status 都 import ctranslate2 + 探测 CUDA）
_DEVICE_INFO = None


def _detect_device():
    global _DEVICE_INFO
    if _DEVICE_INFO is not None:
        return _DEVICE_INFO
    cuda = False
    dev = "cpu"
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            cuda = True
            dev = "cuda:0" + (f" · {_GPU_NAME}" if _GPU_NAME else "")
    except Exception:
        pass
    _DEVICE_INFO = {"cuda": cuda, "dev": dev}
    return _DEVICE_INFO


def device_label():
    return _detect_device()["dev"]


def engine_status():
    """返回转写引擎加载状态（面板展示 + 外部调用诊断）。

    {"loaded": [{"key","engine","model"}...], "device": "cuda:0"|"cpu", "cuda": bool}

    请求路径热读缓存设备信息，不 import torch / 不重复探测 CUDA。
    """
    info = _detect_device()
    loaded = []
    with _ENGINE_LOCK:
        for key in sorted(_ENGINES):
            engine, _, model = key.partition(":")
            loaded.append({"key": key, "engine": engine, "model": model or ""})
    return {"loaded": loaded, "device": info["dev"], "cuda": info["cuda"]}


# ---------------------------------------------------------------- 引擎拆分（boot 编排用）

def resolve_engine(choice):
    """把配置值解析成 (engine_name, model)。"""
    choice = (choice or "sensevoice").strip()
    if choice == "sensevoice":
        return "sensevoice", "sensevoice"
    if choice == "sherpa":
        return "sherpa", ""
    if choice == "qwen3asr":
        return "qwen3asr", "Qwen/Qwen3-ASR-0.6B"
    if choice.startswith("Qwen/"):
        return "qwen3asr", choice
    if choice in ("0.6B", "1.7B"):
        return "qwen3asr", f"Qwen/Qwen3-ASR-{choice}"
    return "whisper", choice


def engine_key(engine_name, model):
    """返回该引擎在 _ENGINES 里的 key。"""
    if engine_name == "sensevoice":
        return "sensevoice"
    if engine_name == "sherpa":
        return "sherpa"
    if engine_name == "qwen3asr":
        return f"qwen3asr:{model}:"
    return f"whisper:{model}"


def load_engine(engine_name, model, device="auto"):
    """按需/常驻加载引擎，返回其 _ENGINES key。

    torch 系（sensevoice/qwen3asr）加载前先 import ctranslate2，
    保证 ctranslate2 先于 torch 加载（否则 CUDA 动态库冲突 WinError 127）。
    """
    if engine_name in ("sensevoice", "qwen3asr"):
        try:
            import ctranslate2  # noqa: F401  顺序安全
        except Exception:
            pass
    if engine_name == "sensevoice":
        _get_sensevoice(device)
        return "sensevoice"
    if engine_name == "sherpa":
        _get_sherpa()
        return "sherpa"
    if engine_name == "qwen3asr":
        _get_qwen3asr(device, model)
        return f"qwen3asr:{model}:"
    _get_whisper(model, device)
    return f"whisper:{model}"


def key_loaded(key):
    with _ENGINE_LOCK:
        return key in _ENGINES


def unload_key(key):
    with _ENGINE_LOCK:
        _ENGINES.pop(key, None)


# ---------------------------------------------------------------- CLI

def main():
    import argparse
    ap = argparse.ArgumentParser(description="ECHO 语音转文字")
    ap.add_argument("wav")
    ap.add_argument("--engine", default="sensevoice",
                    choices=["whisper", "sensevoice", "sherpa", "qwen3asr"])
    ap.add_argument("--model", default="small")
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--device", default="auto")
    args = ap.parse_args()
    text = transcribe(args.wav, engine=args.engine, model=args.model,
                      lang=args.lang, device=args.device)
    print(text)
    return 0 if text else 2


if __name__ == "__main__":
    sys.exit(main())
