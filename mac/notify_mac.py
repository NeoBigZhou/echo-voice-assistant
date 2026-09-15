# -*- coding: utf-8 -*-
"""notify_mac.py — app.assistant.notify 的 macOS 实现

原实现（app/assistant.py:notify）是 Windows 的 PowerShell NotifyIcon：
macOS 上既没有 powershell 也没有 System.Windows.Forms，异常会被 `except: pass`
吞掉 —— 结果就是「已发送 / 没听清 / DSH 未运行」三类通知在 Mac 上**静默失效**。

这里换成 macOS 自带的「通知中心」（osascript display notification）：
  * 标题/正文通过 osascript 的 **argv** 传入，不做字符串拼接，避免引号/换行注入；
  * 后台线程执行，不阻塞命令流（与 Windows 版语义一致：失败只是没弹窗）。

注意：首次通知时系统会向「运行 ECHO 的程序」（终端 / Python）申请通知权限；
在 系统设置 → 通知 里关掉它，通知就不再出现，但不影响其它功能。
"""
import subprocess
import threading

# 用 on run argv 接收参数：无论标题里有引号还是换行都不会破坏脚本
_SCRIPT = (
    "on run argv\n"
    "  display notification (item 2 of argv) with title (item 1 of argv)\n"
    "end run"
)


def notify(title, text):
    """弹出 macOS 通知（后台线程，不阻塞，失败静默）。"""

    def _run():
        try:
            subprocess.run(
                ["osascript", "-e", _SCRIPT, str(title), str(text)],
                timeout=8,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()
