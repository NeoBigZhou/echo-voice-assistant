# -*- coding: utf-8 -*-
"""netguard.py — 只允许"本机回环"访问的来源守卫（防跨站读取 / 跨站写入 / DNS Rebinding）

背景（2026-09-13 安全审计 CRITICAL-1）
------------------------------------
ECHO 的本地 API 原本既不校验来源、CORS 又是 `allow_origins=["*"]`。后果是：你只要打开**任意一个网页**，
它的 JS 就能：

  * 读光数据：`GET /api/meetings` 列清单，再逐条 `GET /api/meetings/{id}/audio?seg=N` 把会议原始 wav、
    `/file?kind=transcript|summary` 把逐字转写与纪要整段拉走；
  * 反向写入：`POST /api/meeting/start` 让 **ECHO 服务进程**（不是浏览器）开始录音——不需要麦克风权限、
    浏览器也不会亮录音指示灯，随后再把音频下载走：一条可远程触发的窃听链路；
  * `POST /api/assistant/command` 让大模型执行任意指令；`POST /control/echo/stop` 直接停服；
    `POST /models/download` 拉几 GB 占满磁盘。

两层防护，缺一不可
------------------
1. **CORS 收紧到回环来源**：浏览器才不会把跨域响应交给页面（`allow_origins=["*"]` 相当于"欢迎读取"）。
2. **本守卫按 Host / Origin 判定来源，非回环一律 403**：
   * 只收紧 CORS 还是挡不住"简单请求"（无预检的 POST，例如不带自定义头的
     `fetch('http://127.0.0.1:8970/api/meeting/start', {method:'POST'})`）——请求照样会被服务端执行，
     只是页面读不到响应。必须在服务端拒绝。
   * 同时封死 DNS Rebinding：攻击者用自己控制的域名，先解析到真实 IP 通过 CORS 校验，再把 DNS 改指
     127.0.0.1，浏览器就认为同源了。不看 Host 头就拦不住这条路。

判定规则
--------
* `Host` 的主机部分必须是 `127.0.0.1` / `localhost` / `::1`（端口随意）；
* 带 `Origin` 时必须也是回环来源（`http(s)://` + 回环主机 + 任意端口）；
* **不带 Origin 的请求放行**：curl、PowerShell、DSH 技能、原生 App 都不带 Origin；
* `Origin: null`（`file://` 页面、sandbox iframe、data: URL）**一律拒绝** —— 因此折叠条页面改成经
  `http://127.0.0.1:8970/web/rail.html` 同源加载，不再用 `file://`。

副作用（预期行为）：这样会让"用局域网 IP 从手机/别的机器访问面板"失效。
**不要为此把服务绑到 0.0.0.0**；正确做法是保持 127.0.0.1 绑定，前面套一个带认证的反向代理
（Caddy/Nginx + Basic Auth + TLS），只对代理放行，并给 ECHO 打开 `apiAuthEnabled` + Bearer Token。
详见 docs/DEPLOY.md。
"""
import re

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# 回环主机名（Host 头里不带端口的那部分）
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# 回环来源（浏览器 Origin 头）：协议 + 回环主机 + 可选端口
LOOPBACK_ORIGIN_REGEX = r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$"
_ORIGIN_RE = re.compile(LOOPBACK_ORIGIN_REGEX, re.IGNORECASE)

# CORS 允许的方法/头（不再用通配）
ALLOW_METHODS = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
ALLOW_HEADERS = ["Content-Type", "Authorization"]


def is_loopback_host(host_header: str) -> bool:
    """Host 头（可能带端口，IPv6 可能带方括号）是否指向本机回环。"""
    host = (host_header or "").strip()
    if not host:
        return False
    if host.startswith("["):                      # [::1]:8970
        end = host.find("]")
        name = host[1:end] if end > 0 else host.strip("[]")
    else:
        name = host.rsplit(":", 1)[0] if ":" in host else host
    return name.lower() in LOOPBACK_HOSTS


def is_loopback_origin(origin: str) -> bool:
    """浏览器 Origin 头是否为本机回环来源。"""
    return bool(_ORIGIN_RE.match((origin or "").strip()))


async def local_only_guard(request, call_next):
    """拒绝一切非回环来源的请求（Host 或 Origin 任一不合规即 403）。"""
    if not is_loopback_host(request.headers.get("host", "")):
        return JSONResponse({"detail": "forbidden: non-loopback Host (DNS rebinding guard)"},
                            status_code=403)
    origin = request.headers.get("origin")
    if origin and not is_loopback_origin(origin):
        return JSONResponse({"detail": "forbidden: cross-site Origin"},
                            status_code=403)
    return await call_next(request)


def install(app):
    """给 FastAPI 应用装上「CORS 只允许回环来源」+「Host/Origin 守卫」。

    中间件顺序：后加的在外层，所以守卫先跑——非回环请求直接 403，不会进入业务路由，
    也就挡掉了"简单请求"式的跨站写入。
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=LOOPBACK_ORIGIN_REGEX,
        allow_methods=ALLOW_METHODS,
        allow_headers=ALLOW_HEADERS,
    )
    app.middleware("http")(local_only_guard)
    return app
