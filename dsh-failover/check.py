# -*- coding: utf-8 -*-
"""check.py — 模型路由自检（命令行，不依赖 ECHO 面板）

用法：
    python dsh-failover/check.py              # 看健康表 + 组配置 + DSH 注册态
    python dsh-failover/check.py --call       # 再发一次真实请求，看命中哪个成员
    python dsh-failover/check.py --call --token <ECHO_ROUTER_TOKEN>

--call 需要路由令牌（组 echo-auto 开了校验）：不传就从 ~/.dsh/.credentials.yaml 读。
"""
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CREDS = Path.home() / ".dsh" / ".credentials.yaml"


def _router_port() -> int:
    """路由端口以同目录 config.json 为准（不再写死）。"""
    try:
        cfg = json.loads((Path(__file__).resolve().parent / "config.json").read_text(encoding="utf-8-sig"))
        return int(cfg.get("port") or 8899)
    except Exception:
        return 8899


def _echo_port() -> int:
    """ECHO 端口以 data/echo-port.txt 为准（ECHO 启动时写出），回退 8970。"""
    try:
        return int((ROOT / "data" / "echo-port.txt").read_text(encoding="ascii").strip())
    except Exception:
        return 8970


BASE = "http://127.0.0.1:%d" % _router_port()
ECHO_API = "http://127.0.0.1:%d/api" % _echo_port()


def get(url, timeout=8):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def token() -> str:
    if "--token" in sys.argv:
        return sys.argv[sys.argv.index("--token") + 1]
    try:
        m = re.search(r"ECHO_ROUTER_TOKEN:\s*(\S+)", CREDS.read_text(encoding="utf-8"))
        return m.group(1) if m else ""
    except Exception:
        return ""


def main() -> int:
    try:
        h = get(BASE + "/health")
    except Exception as exc:
        print(f"❌ 模型路由未运行（{BASE}）：{type(exc).__name__}: {exc}")
        print("   启动：powershell -ExecutionPolicy Bypass -File dsh-failover\\start.ps1")
        return 1

    print(f"{h.get('service')} v{h.get('version')}  status={h.get('status')}")
    for g in h.get("groups", []):
        tok = "（需令牌）" if g.get("require_token") else ""
        print(f"\n[{g['id']}] {g['display_name']}  启用={g.get('active')} "
              f"健康={g.get('healthy')} {tok}")
        for m in g.get("members", []):
            off = " " if m.get("enabled", True) else "×"
            state = "熔断" if m.get("state") == "open" else (
                "可达" if m.get("reachable") is True else
                ("不可达" if m.get("reachable") is False else "未判"))
            ttfb = f"{m['last_ttfb_ms']}ms" if m.get("last_ttfb_ms") is not None else "—"
            key = "" if m.get("has_token") else "  ❌缺凭据"
            print(f"  {off}通道{m['priority']} {m['name']:<18} {state:<4} "
                  f"ok/fail={m['ok']}/{m['fail']:<3} ttfb={ttfb:<7} {m.get('detail','')}{key}")
            if m.get("last_error"):
                print(f"      最近错误：{m['last_error']}")
    rt = h.get("routes") or {}
    print(f"\n请求 共 {rt.get('requests', 0)} · 失败 {rt.get('failed', 0)}"
          + (f" · 最近命中 通道{rt.get('last_channel')} {rt.get('last_member') or ''}".rstrip()
             + f" @ {rt.get('last_route_at')}" if rt.get("last_route_at") else ""))

    # ECHO 侧（可选）：注册态 + 配置里的成员顺序
    try:
        v = get(ECHO_API + "/router/status", timeout=20)
        reg = v.get("registration", {})
        print(f"\nDSH 注册：{'已注册' if reg.get('registered') else '未注册'}"
              f" · {reg.get('models', 0)} 个模型 · 令牌{'就位' if reg.get('has_token') else '缺失'}"
              f" · 候选模型 {len(v.get('candidates', []))} 个")
    except Exception as exc:
        print(f"\n（ECHO 面板接口不可用，跳过注册态：{type(exc).__name__}）")

    if "--call" not in sys.argv:
        return 0

    print("\n—— 发一次真实请求（model=echo-auto）——")
    body = json.dumps({"model": "echo-auto",
                       "messages": [{"role": "user", "content": "只回复两个字：收到"}],
                       "max_tokens": 256}).encode()
    req = urllib.request.Request(BASE + "/chat/completions", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    tk = token()
    if tk:
        req.add_header("Authorization", "Bearer " + tk)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode("utf-8"))
            from urllib.parse import unquote
            print(f"HTTP {r.status} · 通道 {r.headers.get('X-ECHO-Channel')} "
                  f"（{unquote(r.headers.get('X-ECHO-Route') or '?')}）"
                  f" · 首字节 {r.headers.get('X-ECHO-TTFB-Ms')}ms · 上游 model={data.get('model')}")
            for c in (data.get("choices") or []):
                print("回复：", (c.get("message") or {}).get("content", "").strip()[:80])
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}（通道 {exc.headers.get('X-ECHO-Channel')}）：{exc.read().decode('utf-8', 'ignore')[:300]}")
        if exc.code == 401:
            print("提示：这是路由令牌校验失败，用 --token <ECHO_ROUTER_TOKEN> 重试")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
