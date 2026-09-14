# -*- coding: utf-8 -*-
"""run_mac.py — ECHO macOS 启动入口（零改动 Windows 代码）

原理：Windows 版在 import 时直接依赖 Windows 专有模块（app.hotkey 里的
ctypes.windll、app.runtime 里的 powershell/边条）。本入口在 import app.main
**之前**，把 Mac 实现塞进 sys.modules，覆盖 app.hotkey 与 app.runtime，
并给 app.audio.tts 打补丁 —— 因此原仓库任何文件都不用改。

运行：python mac/run_mac.py   （或 mac/start_mac.sh）
"""
import os
import sys

# ---- 路径：项目根目录 + mac/ 目录都放进 sys.path ----
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAC_DIR = os.path.join(BASE_DIR, "mac")
for p in (BASE_DIR, MAC_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

# ---- 1) 先把 Mac 版配置默认值改好（seed_defaults 之前生效）----
import app.config as _config  # noqa: E402

# 边条是 Windows 专属程序，Mac 用浏览器打开面板
_config.DEFAULTS["panelOpenMode"]["value"] = "browser"
_config.DEFAULTS["panelOpenMode"]["description"] = "browser=浏览器打开面板（macOS 无右缘边条）"
# Mac 上用 Whisper，避免 funasr/SenseVoice 在 Apple 芯片上的兼容坑
_config.DEFAULTS["sttModel"]["value"] = "base"
_config.DEFAULTS["meetingSttModel"]["value"] = "small"
# Mac 没有 CUDA
_config.DEFAULTS["device"]["value"] = "cpu"
# 去掉会强制改回 sidebar 的迁移
_config.DEFAULT_MIGRATIONS.pop("panelOpenMode", None)

# ---- 2) 注入 Mac 版 runtime / hotkey（必须在 import app.main 之前）----
# 注意顺序：先注册 app.hotkey，再 import mac_runtime（它顶部会 from app.hotkey import）
import hotkey_mac  # noqa: E402

sys.modules["app.hotkey"] = hotkey_mac

import mac_runtime  # noqa: E402

sys.modules["app.runtime"] = mac_runtime

# ---- 3) TTS 补丁：Windows SAPI → macOS say；winsound → sounddevice ----
import app.audio.tts as _tts  # noqa: E402
import tts_mac  # noqa: E402

tts_mac.patch(_tts)

# ---- 4) 启动 ----
from app.main import main  # noqa: E402

if __name__ == "__main__":
    print("ECHO (macOS) 启动中…")
    main()
