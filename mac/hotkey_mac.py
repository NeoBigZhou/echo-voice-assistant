# -*- coding: utf-8 -*-
"""hotkey_mac.py — app.hotkey 的 macOS 实现（pynput，可选）

对外接口与 Windows 版 HotkeyListener 兼容：
    HotkeyListener(settings_get, on_trigger=cb).start() / .is_alive() / .shutdown()
    .error 为空表示可用，否则为给用户看的原因。

依赖 pynput（可选）。首次使用时 macOS 会要求在
「系统设置 → 隐私与安全性 → 辅助功能 / 输入监控」里给终端或 Python 授权，
未安装或未授权时本模块优雅降级：热键不可用，但面板录音等一切照常。
"""
# Windows 命名键 → pynput 键名
_NAMED = {
    "SPACE": "<space>", "ENTER": "<enter>", "RETURN": "<enter>", "TAB": "<tab>",
    "ESC": "<esc>", "ESCAPE": "<esc>", "BACKSPACE": "<backspace>",
    "DELETE": "<delete>", "INSERT": "<insert>", "HOME": "<home>", "END": "<end>",
    "PGUP": "<page_up>", "PAGEUP": "<page_up>", "PGDN": "<page_down>",
    "PAGEDOWN": "<page_down>", "LEFT": "<left>", "UP": "<up>",
    "RIGHT": "<right>", "DOWN": "<down>",
    **{f"F{i}": f"<f{i}>" for i in range(1, 25)},
}


def _to_pynput(combo):
    """'Ctrl+Alt+C' → '<ctrl>+<alt>+c'；无法解析返回 None。"""
    if not combo:
        return None
    parts = [p.strip() for p in str(combo).split("+") if p.strip()]
    if len(parts) < 2:
        return None
    out = []
    for m in parts[:-1]:
        ml = m.lower()
        if ml == "ctrl" or ml == "control":
            out.append("<ctrl>")
        elif ml == "alt" or ml == "option":
            out.append("<alt>")
        elif ml == "shift":
            out.append("<shift>")
        elif ml in ("win", "cmd", "command", "super"):
            out.append("<cmd>")
        else:
            return None
    last = parts[-1]
    ul = last.upper()
    if len(last) == 1 and last.isalnum():
        out.append(last.lower())
    elif ul in _NAMED:
        out.append(_NAMED[ul])
    else:
        return None
    return "+".join(out)


class HotkeyListener:
    """pynput 全局热键监听。接口兼容 Windows 版。"""

    def __init__(self, settings_get, on_trigger=None, daemon=True):
        self.settings_get = settings_get
        self.on_trigger = on_trigger or (lambda source, detail: None)
        self.error = ""
        self._listener = None

    def _fire(self, key_name):
        try:
            self.on_trigger("hotkey", key_name)
        except Exception:
            pass

    def start(self):
        try:
            from pynput import keyboard
        except Exception as e:
            self.error = ("未安装 pynput，Mac 全局热键不可用"
                          "（可运行：venv/bin/pip install pynput）")
            print(f"[hotkey-mac] {self.error}: {e}")
            return

        mapping = {}
        for key in ("wakeHotkey", "fallbackHotkey", "panelHotkey"):
            hk = _to_pynput(self.settings_get(key, ""))
            if not hk:
                continue
            # 闭包固定 key 名
            mapping[hk] = (lambda k=key: self._fire(k))
        if not mapping:
            self.error = "配置里没有可用的热键"
            return
        try:
            self._listener = keyboard.GlobalHotKeys(mapping)
            self._listener.daemon = True
            self._listener.start()
            print(f"[hotkey-mac] 已注册热键: {list(mapping)}")
        except Exception as e:
            self.error = ("热键启动失败，请在 系统设置 → 隐私与安全性 → 辅助功能/IP监控 "
                          f"给本程序授权后重试：{e}")
            print(f"[hotkey-mac] {self.error}")

    def is_alive(self):
        return bool(self._listener is not None and self._listener.is_alive())

    def shutdown(self):
        try:
            if self._listener is not None:
                self._listener.stop()
        except Exception:
            pass


def run_once(seconds=15, settings_get=None, callback=None):
    """调试：跑 seconds 秒打印触发事件。"""
    from app.config import settings as _s
    g = settings_get or _s.get
    seen = []
    hk = HotkeyListener(g, on_trigger=callback or (lambda s, d: seen.append((s, d))))
    hk.start()
    import time
    time.sleep(seconds)
    hk.shutdown()
    return seen


if __name__ == "__main__":
    print(run_once())
