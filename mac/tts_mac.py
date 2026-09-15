# -*- coding: utf-8 -*-
"""tts_mac.py — app.audio.tts 的 macOS 补丁

原模块两个 Windows 专属点：
  1. play_beep()  用 winsound 播放提示音 → Mac 上静默失效
  2. _speak_sapi() 用 PowerShell + System.Speech → Mac 上不可用
本补丁把它们替换成跨平台实现：
  * 提示音：soundfile 解码 + sounddevice 播放
  * 离线合成：macOS 自带 `say`（自动挑中文音色），作为 edge-tts 失败时的兜底

调用：tts_mac.patch(app.audio.tts)
"""
import os
import subprocess
import threading

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BEEPS_DIR = os.path.join(BASE_DIR, "assets", "beeps")

_voice_cache = {"name": None, "ready": False}


def _zh_voice():
    """挑一个系统里的中文音色名（如 Tingting / Ting-Ting）。找不到返回 None。"""
    if _voice_cache["ready"]:
        return _voice_cache["name"]
    _voice_cache["ready"] = True
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True,
                             text=True, timeout=10).stdout
        for line in out.splitlines():
            low = line.lower()
            if "zh_cn" in low or "zh-cn" in low or " zh_" in low:
                _voice_cache["name"] = line.split()[0]
                break
    except Exception:
        pass
    return _voice_cache["name"]


def play_beep(name):
    """播放 assets/beeps/<name>.wav（非阻塞线程）。"""
    wav = os.path.join(BEEPS_DIR, name + ".wav")
    if not os.path.isfile(wav):
        return

    def _play():
        try:
            import soundfile as sf
            import sounddevice as sd
            data, sr = sf.read(wav, dtype="float32")
            sd.play(data, sr)
            sd.wait()
        except Exception:
            pass

    threading.Thread(target=_play, daemon=True).start()


def speak_say(text, timeout=90):
    """用 macOS `say` 朗读（阻塞）。中文优先挑中文音色。"""
    if not text:
        return False
    voice = _zh_voice()
    attempts = []
    if voice:
        attempts.append(["say", "-v", voice, text])
    attempts.append(["say", text])
    for argv in attempts:
        try:
            r = subprocess.run(argv, timeout=timeout)
            if r.returncode == 0:
                return True
        except Exception:
            continue
    return False


def patch(tts_module):
    """把 Windows 实现替换为 macOS 实现。"""
    tts_module.play_beep = play_beep
    tts_module._speak_sapi = speak_say
    # 常驻/一次性 SAPI 入口也一并替换，避免任何路径漏到 powershell
    tts_module._speak_sapi_persistent = speak_say
    tts_module._speak_sapi_once = speak_say
    print("[mac] TTS 已切换为 edge-tts / macOS say")
    return tts_module
