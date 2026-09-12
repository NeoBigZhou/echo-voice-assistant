# -*- coding: utf-8 -*-
"""failover_proxy.py — DSH 容灾代理（dsh-failover/proxy.py）的 ECHO 侧守护

确保 http://127.0.0.1:8899（DSH 模型容灾代理）在运行：

- ECHO 每次启动（boot 组件）会调用 start_guard()：立即探测，不在则用同
  venv 的 pythonw 拉起；
- 之后每 30 秒复查一次，代理中途退出会自动拉回。

ECHO 本身由 DSH Desktop 的 echo-host 插件 15 秒守护，因此本守护随 ECHO
一起借力：ECHO 活着 → 代理就绪。避免代理挂掉后 DSH 模型调用全部失败。
"""
import os
import subprocess
import sys
import threading
import time
import urllib.request

PROXY_PORT = 8899
HEALTH_URL = "http://127.0.0.1:%d/health" % PROXY_PORT
GUARD_INTERVAL = 30.0        # 守护复查间隔（秒）
READY_WAIT_MAX = 6.0         # 拉起后等待健康检查的最长时间（秒）

_lock = threading.Lock()
_guard = None                # 守护线程
_stop_evt = None

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROXY_SCRIPT = os.path.join(BASE_DIR, "dsh-failover", "proxy.py")
LOG_DIR = os.path.join(BASE_DIR, "dsh-failover", "logs")


def proxy_online(timeout=1.0):
    """探测容灾代理 /health。在线返回 True。"""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _pythonw():
    """从当前解释器推导同环境 pythonw.exe（无控制台窗口）。"""
    exe = sys.executable or "pythonw"
    low = exe.lower()
    if low.endswith("python.exe"):
        return exe[:-len("python.exe")] + "pythonw.exe"
    if low.endswith("pythonw.exe"):
        return exe
    return exe  # 兜底：直接用当前解释器


def ensure_running():
    """探测代理，不在则拉起。幂等。返回 (ok, detail)。"""
    if proxy_online():
        return True, "代理已在运行"
    script = PROXY_SCRIPT
    if not os.path.isfile(script):
        return False, "代理脚本缺失: %s" % script
    if not os.path.isdir(LOG_DIR):
        try:
            os.makedirs(LOG_DIR)
        except Exception:
            pass
    pyw = _pythonw()
    out = os.path.join(LOG_DIR, "proxy-echo.log")
    err = os.path.join(LOG_DIR, "proxy-echo.err.log")
    try:
        with open(out, "a", encoding="utf-8") as fo, open(err, "a", encoding="utf-8") as fe:
            subprocess.Popen(
                [pyw, script],
                cwd=os.path.dirname(script),
                stdout=fo,
                stderr=fe,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    except Exception as e:
        return False, "拉起代理失败: %s" % e
    # 等待就绪（最多 READY_WAIT_MAX 秒）
    waited = 0.0
    while waited < READY_WAIT_MAX:
        if proxy_online(0.8):
            return True, "代理已拉起"
        time.sleep(0.5)
        waited += 0.5
    return True, "代理进程已启动（健康检查暂未通过，守护线程会继续复查）"


def _guard_loop():
    while not _stop_evt.is_set():
        try:
            ensure_running()
        except Exception:
            pass
        _stop_evt.wait(GUARD_INTERVAL)


def start_guard():
    """启动守护线程（幂等），并立即确保代理在运行。返回 (ok, detail)。"""
    global _guard, _stop_evt
    with _lock:
        if _guard is not None and _guard.is_alive():
            return True, "守护已在运行"
        _stop_evt = threading.Event()
        _guard = threading.Thread(target=_guard_loop, daemon=True,
                                  name="failover-proxy-guard")
        _guard.start()
    return ensure_running()


def stop_guard():
    """停止守护线程（不影响代理进程本身）。"""
    global _guard, _stop_evt
    with _lock:
        if _guard is not None:
            _stop_evt.set()
            _guard = None
        _stop_evt = None
    return True, "守护已停止"
