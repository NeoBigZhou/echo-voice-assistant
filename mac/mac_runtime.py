# -*- coding: utf-8 -*-
"""mac_runtime.py — app.runtime 的 macOS 实现

对外接口与 Windows 版保持一致（start_hotkey/stop_hotkey/start_wake/stop_wake/
restart_echo/toggle_sidebar/autostart_sidebar/open_panel_window/start_all/stop_all），因此
app.boot / app.api / app.main 无需任何改动。

差异：
  * 没有右缘边条（Windows .NET 程序）→ 一律用浏览器打开面板
  * 重启走 mac/restart_mac.sh（不再是 powershell）
  * 唤醒沿用跨平台的 app.audio.wake.WakeListener
"""
import os
import subprocess
import threading
import time

import app.assistant as assistant
from app.audio.wake import WakeListener
from app.config import settings
from app.hotkey import HotkeyListener
from app import services

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_hotkey = None
_wake = None
_lock = threading.Lock()
_panel_last_open = 0.0


def sidebar_exe_path():
    """macOS 没有 Windows 边条程序。"""
    return None


def toggle_sidebar():
    """Mac 上“边条”不存在 → 退化为打开面板。"""
    return open_panel_window()


def autostart_sidebar():
    """macOS 没有 Windows 右缘边条，启动时不弹面板。

    「启动时自动显示折叠条」（panelAutoStart）按定义只在 panelOpenMode=sidebar 时生效，
    而 mac 上 panelOpenMode 固定为 browser（见 run_mac.py），所以这里保持静默；
    用户要面板用 panelHotkey（默认 Ctrl+Shift+E）或直接开浏览器。
    """
    return "skip: macOS 无右缘边条（panelOpenMode=browser）"


def open_panel_window():
    """用系统默认浏览器打开 ECHO 面板（1.5 秒防抖）。"""
    global _panel_last_open
    now = time.time()
    if now - _panel_last_open < 1.5:
        return False
    _panel_last_open = now
    port = int(settings.get("serverPort", 8970))
    url = f"http://127.0.0.1:{port}/"
    try:
        subprocess.Popen(["open", url], close_fds=True)
        print(f"[mac] 打开面板: {url}")
        return True
    except Exception as e:
        print(f"[mac] 打开面板失败: {e}")
        return False


def restart_echo():
    """自我重启：脱离进程组起 mac/restart_mac.sh（停 → 等端口释放 → 起）。"""
    script = os.path.join(BASE_DIR, "mac", "restart_mac.sh")
    if not os.path.isfile(script):
        return False, "找不到 mac/restart_mac.sh"
    log_dir = os.path.join(BASE_DIR, "data", "logs")
    os.makedirs(log_dir, exist_ok=True)
    out_path = os.path.join(log_dir, "restart-mac.out")
    err_path = os.path.join(log_dir, "restart-mac.err")
    try:
        with open(out_path, "ab") as fo, open(err_path, "ab") as fe:
            subprocess.Popen(
                ["/bin/bash", script],
                cwd=BASE_DIR,
                stdin=subprocess.DEVNULL,
                stdout=fo,
                stderr=fe,
                start_new_session=True,   # 脱离进程组，父进程被杀不影响它
            )
        print(f"[mac] 已请求重启 ECHO: {script}")
        return True, "正在重启 ECHO（约 3~8 秒，面板会自动重连）"
    except Exception as e:
        print(f"[mac] 重启失败: {e}")
        return False, f"重启失败: {e}"


def _hotkey_cb(source, detail):
    """与 Windows 版语义一致：panelHotkey → 开面板；其余 → 录音命令流。"""
    if source == "hotkey":
        if detail == "panelHotkey":
            open_panel_window()
        else:
            assistant.capture("hotkey")
    elif source == "mediakey":
        keys = settings.get("triggerKeys", ["vol_up"]) or ["vol_up"]
        if detail in keys:
            assistant.capture("mediakey")


def start_hotkey():
    global _hotkey
    with _lock:
        if _hotkey is not None and _hotkey.is_alive():
            return True, "热键监听已在运行"
        _hotkey = HotkeyListener(settings.get, on_trigger=_hotkey_cb)
        _hotkey.start()
        err = getattr(_hotkey, "error", "") or ""
        if err:
            services.report_hotkey("offline", err[:160])
            return False, err
        services.report_hotkey("online", "macOS 全局热键")
        return True, "热键监听已启动"


def stop_hotkey():
    global _hotkey
    with _lock:
        if _hotkey is not None:
            _hotkey.shutdown()
            _hotkey = None
        services.report_hotkey("offline", "已停止")
        return True, "热键监听已停止"


def _wake_cb():
    assistant.capture("wake")


def start_wake():
    global _wake
    with _lock:
        if _wake is not None and _wake.is_alive():
            return True, "唤醒监听已在运行"
        _wake = WakeListener(settings.get, on_wake=_wake_cb)
        _wake.start()
        services.report_wake("online", "sherpa KWS")
        return True, "唤醒监听已启动"


def stop_wake():
    global _wake
    with _lock:
        if _wake is not None:
            _wake.shutdown()
            _wake = None
        services.report_wake("offline", "已停止")
        return True, "唤醒监听已停止"


def start_all():
    """按配置启动监听器（唤醒词需启用才启动）。"""
    results = [start_hotkey()]
    if settings.get("wakeEnabled", False):
        results.append(start_wake())
    else:
        services.report_wake("disabled", "未启用")
    return results


def stop_all():
    stop_hotkey()
    stop_wake()
