# -*- coding: utf-8 -*-
"""probe_mediakeys.py — 临时诊断脚本（在真实桌面会话双击运行）
用 WH_KEYBOARD_LL 全量打印键盘/媒体键事件（VK 码）到 data\logs\media-probe.log，
用于确认耳机/键盘的媒体键是否走"键盘输入"通道（即 ECHO 的 app/hotkey.py 能否捕获）。
运行 N 秒（默认 90）后自动退出。
用法: python scripts/probe_mediakeys.py [seconds]
"""
import ctypes
import os
import sys
import time
import datetime
from ctypes import wintypes as wt

try:
    import winsound
    def beep(f, ms):
        try:
            winsound.Beep(f, ms)
        except Exception:
            pass
except Exception:
    def beep(f, ms):
        pass

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

# 与 app/hotkey.py 中 MEDIA_KEYS 一致
MEDIA = {0xAD: "vol_mute", 0xAE: "vol_down", 0xAF: "vol_up",
         0xB0: "next", 0xB1: "prev", 0xB2: "stop", 0xB3: "play_pause"}

HOOKPROC = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_int, wt.WPARAM, wt.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.windll.kernel32

# 规范签名 —— 修复 last_error=126：此前 GetModuleHandleW 返回值被默认类型
# c_int 截断成 32 位，传给 SetWindowsHookExW 的 hMod 是坏指针（模块找不到）。
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HMODULE, wt.DWORD]
user32.CallNextHookEx.restype = ctypes.c_long
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.UnhookWindowsHookEx.restype = wt.BOOL
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]

LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "logs", "media-probe.log")
out = open(LOG_PATH, "w", encoding="utf-8", buffering=1)


def log(m):
    line = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3] + " " + m
    print(line, flush=True)
    out.write(line + "\n")


def main():
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    log("=== media-key probe start ===")
    beep(800, 180)

    hook_ref = [None]

    def proc(nCode, wParam, lParam):
        if nCode >= 0 and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN, WM_KEYUP, WM_SYSKEYUP):
            kbd = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = int(kbd.vkCode)
            name = MEDIA.get(vk)
            kind = "KEYUP" if wParam in (WM_KEYUP, WM_SYSKEYUP) else "KEYDOWN"
            mark = "  <<<< MEDIA " + name if name else ""
            if name or wParam == WM_KEYDOWN:
                log("0x%02X scan=0x%02X flags=0x%02X %-7s %s%s"
                    % (vk, kbd.scanCode, kbd.flags, kind, name or "key", mark))
        return user32.CallNextHookEx(proc_hh, nCode, wParam, lParam)

    hook_ref[0] = HOOKPROC(proc)
    proc_hh = user32.SetWindowsHookExW(WH_KEYBOARD_LL, hook_ref[0],
                                       kernel32.GetModuleHandleW(None), 0)
    if not proc_hh:
        log("hook install FAILED (last_error=%d)" % ctypes.get_last_error())
        out.close()
        sys.exit(1)
    log("hook installed OK")

    end = time.time() + seconds
    msg = wt.MSG()
    last_beat = time.time()
    while time.time() < end:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        now = time.time()
        if now - last_beat >= 10:
            last_beat = now
            log("alive (t=%.0fs)" % (seconds - (end - now)))
        time.sleep(0.02)

    user32.UnhookWindowsHookEx(proc_hh)
    log("=== probe done (ran %d s) ===" % seconds)
    out.close()
    beep(1200, 300)
    beep(1500, 350)
    print("DONE-PROBE")


if __name__ == "__main__":
    main()
