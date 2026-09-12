#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dsh-failover-proxy — DSH 模型容灾代理（独立常驻服务）

把 DSH 的内网 provider 指向本机这个代理（默认 http://127.0.0.1:8899）。
代理对每个请求：
  1) 先按内网网关转发（默认占位 URL，部署时改成你自己内网的 OpenAI 兼容端点，
     userId 头）
  2) 仅当内网"连不上"（连接拒绝 / DNS 失败 / 握手失败 / 连接/读取超时）时，
     自动改用公网 DeepSeek 官方 API（模型 deepseek-chat），流式照常。
内网一旦可达并返回 HTTP 状态码（包括 4xx/5xx），则原样透传、不回退，尊重网关裁定。

密钥来源（优先级从高到低）：
  - 环境变量 FAILOVER_INTERNAL_TOKEN / FAILOVER_DEEPSEEK_KEY
  - ~/.dsh/.credentials.yaml 中的 INTERNAL_LLM_TOKEN / DEEPSEEK_API_KEY

用法：
  python proxy.py                      # 默认 127.0.0.1:8899
  python proxy.py --port 8899 --config <path>
  python proxy.py --check              # 打印生效配置并退出
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

# ---------------------------------------------------------------------------
# 默认配置
# ---------------------------------------------------------------------------
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8899

DEFAULT_INTERNAL_URL = (
    "https://your-intranet-gateway.example.com"
    "/v1/chat/completions"
)
DEFAULT_INTERNAL_MODEL = "DeepSeek-V4-Flash"
DEFAULT_INTERNAL_USER_ID = "zhoukq1"

DEFAULT_PUBLIC_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_PUBLIC_MODEL = "deepseek-chat"

# 内网连接阶段允许的最长等待（秒）。连不上 = 回退公网；拉太长反而拖垮体验。
DEFAULT_CONNECT_TIMEOUT = 6.0
DEFAULT_READ_TIMEOUT = 360.0
DEFAULT_WRITE_TIMEOUT = 120.0
# 首个数据块前的总等待（秒）。内网迟迟不出流也视为“访问不到”，回退公网。
DEFAULT_FIRST_BYTE_TIMEOUT = 30.0

CRED_YAML = Path.home() / ".dsh" / ".credentials.yaml"


# ---------------------------------------------------------------------------
# 配置读取
# ---------------------------------------------------------------------------
class Config:
    def __init__(self, **kw):
        self.host = kw.get("host", DEFAULT_HOST)
        self.port = int(kw.get("port", DEFAULT_PORT))
        self.internal_url = kw.get("internal_url", DEFAULT_INTERNAL_URL)
        self.internal_model = kw.get("internal_model", DEFAULT_INTERNAL_MODEL)
        self.internal_user_id = kw.get("internal_user_id", DEFAULT_INTERNAL_USER_ID)
        self.public_url = kw.get("public_url", DEFAULT_PUBLIC_URL)
        self.public_model = kw.get("public_model", DEFAULT_PUBLIC_MODEL)
        self.internal_token = kw.get("internal_token", "")
        self.public_key = kw.get("public_key", "")
        self.connect_timeout = float(kw.get("connect_timeout", DEFAULT_CONNECT_TIMEOUT))
        self.read_timeout = float(kw.get("read_timeout", DEFAULT_READ_TIMEOUT))
        self.write_timeout = float(kw.get("write_timeout", DEFAULT_WRITE_TIMEOUT))
        self.first_byte_timeout = float(kw.get("first_byte_timeout", DEFAULT_FIRST_BYTE_TIMEOUT))
        # 内网返回 5xx 时也回退公网（可选；默认 False 只对“连不上”回退）
        self.fallback_on_5xx = bool(kw.get("fallback_on_5xx", False))


def _parse_cred_yaml_keys() -> dict:
    """从 ~/.dsh/.credentials.yaml 提取两个 ref（成败都不抛，缺了就是空）。"""
    out = {}
    try:
        text = CRED_YAML.read_text(encoding="utf-8")
    except Exception:
        return out
    m = re.search(r"^\s*DEEPSEEK_API_KEY\s*:\s*(\S+)\s*$", text, re.M)
    if m:
        out["public_key"] = m.group(1).strip().strip("\"'")
    m = re.search(r"^\s*INTERNAL_LLM_TOKEN\s*:\s*(\S+)\s*$", text, re.M)
    if m:
        out["internal_token"] = m.group(1).strip().strip("\"'")
    return out


def load_config(args) -> Config:
    kw = {}
    # 1) JSON 配置文件（可选；兼容 UTF-8 BOM）
    if getattr(args, "config", None) and os.path.isfile(args.config):
        raw = Path(args.config).read_bytes().decode("utf-8-sig")
        kw.update(json.loads(raw))
    # 2) 认证（环境变量优先，其次 credentials.yaml）
    cred = _parse_cred_yaml_keys()
    kw["internal_token"] = (
        os.environ.get("FAILOVER_INTERNAL_TOKEN")
        or kw.get("internal_token")
        or cred.get("internal_token", "")
    )
    kw["public_key"] = (
        os.environ.get("FAILOVER_DEEPSEEK_KEY")
        or kw.get("public_key")
        or cred.get("public_key", "")
    )
    # 3) CLI 覆盖
    for name in ("host", "port", "internal_url", "public_url"):
        val = getattr(args, name, None)
        if val:
            kw[name] = val
    return Config(**kw)


# ---------------------------------------------------------------------------
# 路由可观测性（累计统计 + 最近历史，供 /health 与状态页查看走了内网还是公网）
# ---------------------------------------------------------------------------
_route_stats = {
    "requests": 0,      # 收到的模型请求总数
    "internal": 0,      # 走内网成功的请求数
    "public": 0,        # 回退公网成功的请求数
    "failed": 0,        # 两边都失败
    "last_route": None,  # internal | public | failed
    "last_route_at": None,
}
# 每次“切换”（internal <-> public <-> failed）的时间戳，供状态页展示
_route_history = []  # [{t, route, req}], 最多 30 条


def _note_route(route: str):
    _route_stats["requests"] += 1
    key = "internal" if route == "internal" else ("public" if route == "public" else "failed")
    _route_stats[key] += 1
    _route_stats["last_route"] = route
    _route_stats["last_route_at"] = time.strftime("%H:%M:%S")
    # 路由变化（或首次）时记录一次历史；同路由连续请求合并
    if not _route_history or _route_history[-1]["route"] != route:
        _route_history.append({"t": time.strftime("%H:%M:%S"), "route": route,
                               "req": _route_stats["requests"]})
        if len(_route_history) > 30:
            _route_history.pop(0)
    print(f"[route] {route}  (internal={_route_stats['internal']} public={_route_stats['public']} failed={_route_stats['failed']})", flush=True)


# ---------------------------------------------------------------------------
# 请求转发
# ---------------------------------------------------------------------------
def _body_model(body: dict) -> str:
    return body.get("model") or ""


def _is_transport_error(exc: Exception) -> bool:
    """判断是否属“内网访问不到”类错误，应回退公网。"""
    if isinstance(exc, httpx.ConnectError):
        return True
    if isinstance(exc, httpx.ConnectTimeout):
        return True
    if isinstance(exc, httpx.ReadTimeout):
        return True
    if isinstance(exc, httpx.WriteTimeout):
        return True
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.RemoteProtocolError):
        return True
    # TLS / 网络层包装错误
    return False


def _health(response: httpx.Response) -> bool:
    return 200 <= response.status_code < 300


def make_client(cfg: Config) -> httpx.AsyncClient:
    limits = httpx.Limits(max_connections=32, max_keepalive_connections=16)
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=cfg.connect_timeout,
            read=cfg.read_timeout,
            write=cfg.write_timeout,
            pool=None,
        ),
        limits=limits,
        follow_redirects=True,
        trust_env=True,
    )


def internal_headers(cfg: Config, incoming: dict) -> dict:
    headers = {
        "Accept": incoming.get("Accept", "text/event-stream"),
        "user-agent": "dsh-failover-proxy",
    }
    if cfg.internal_token:
        headers["Authorization"] = f"Bearer {cfg.internal_token}"
    if cfg.internal_user_id:
        headers["userId"] = cfg.internal_user_id
    return headers


def public_headers(cfg: Config, incoming: dict) -> dict:
    headers = {
        "Accept": incoming.get("Accept", "application/json"),
        "user-agent": "dsh-failover-proxy",
    }
    if cfg.public_key:
        headers["Authorization"] = f"Bearer {cfg.public_key}"
    return headers


# 公网兼容：仅保留标准 OpenAI Chat Completions 字段，剔除内部网关专有字段
PUBLIC_SAFE_FIELDS = {
    "messages", "model", "temperature", "top_p", "top_k", "max_tokens", "max_completion_tokens",
    "stream", "stream_options", "stop", "n", "presence_penalty", "frequency_penalty", "logit_bias",
    "logprobs", "top_logprobs", "tools", "tool_choice", "parallel_tool_calls", "response_format",
    "seed", "user", "reasoning_effort", "metadata", "store", "grammar",
}


def rewrite_for_public(cfg: Config, body: dict) -> dict:
    """公网回退：仅保留公网识别的标准字段，并把模型名映射为 deepseek-chat。"""
    new = {k: v for k, v in body.items() if k in PUBLIC_SAFE_FIELDS}
    if new.get("model"):
        new["model"] = cfg.public_model
    return new


async def _establish(cfg, client, url, headers, body):
    """发起请求，等待响应头并读取首块数据（同步等待，供回退判定）。

    返回 (response, body_iterator) 或 (None, None)。body_iterator 是**单一**迭代器：
    首块已在内部读出并并入，直接逐块消费即可，不在二次 aiter_bytes
    （httpx 响应体只能流一次）。
    """
    try:
        resp = await client.send(client.build_request("POST", url, json=body, headers=headers), stream=True)
    except Exception:
        return None, None

    it = resp.aiter_bytes()
    try:
        # 首块耐心窗口：内网迟迟无数据视为“访问不到”，交由上层回退。
        first = await asyncio.wait_for(anext(it, None), timeout=cfg.first_byte_timeout)
    except (asyncio.TimeoutError, httpx.TimeoutException, httpx.RemoteProtocolError, httpx.StreamError):
        await resp.aclose()
        return None, None
    except Exception:
        await resp.aclose()
        return None, None

    async def merged():
        try:
            if first is not None:
                yield first
            async for chunk in it:
                yield chunk
        finally:
            await resp.aclose()

    return resp, merged()


def _stream_upstream(response: httpx.Response, body_iterator, extra_headers=None) -> StreamingResponse:
    async def gen():
        async for chunk in body_iterator:
            yield chunk

    media = (
        "text/event-stream"
        if response.headers.get("content-type", "").startswith("text/event-stream")
        else response.headers.get("content-type", "application/json")
    )
    headers = dict(extra_headers or {})
    return StreamingResponse(gen(), status_code=response.status_code, media_type=media, headers=headers)


async def _try_internal(cfg, client, body, incoming_headers):
    """尝试转发到内网。返回 (response, body_iterator) 或 (None, None)。"""
    resp, it = await _establish(cfg, client, cfg.internal_url, internal_headers(cfg, incoming_headers), body)
    if resp is None:
        return None, None
    if cfg.fallback_on_5xx and resp.status_code >= 500:
        await resp.aclose()
        return None, None
    return resp, it


async def _try_public(cfg, client, body, incoming_headers):
    return await _establish(cfg, client, cfg.public_url, public_headers(cfg, incoming_headers), rewrite_for_public(cfg, body))


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------
def create_app(cfg: Config):
    app = FastAPI(title="dsh-failover-proxy", version="1.0.0")
    client = make_client(cfg)

    @app.on_event("shutdown")
    async def _close():
        await client.aclose()

    @app.get("/health")
    async def health():
        return {"status": "ok", "internal": cfg.internal_url, "public": cfg.public_url,
                "has_internal_token": bool(cfg.internal_token), "has_public_key": bool(cfg.public_key),
                "routes": dict(_route_stats), "history": list(_route_history)}

    @app.get("/models")
    async def models():
        return {"object": "list", "data": [
            {"id": cfg.internal_model, "object": "model"},
            {"id": cfg.public_model, "object": "model"},
        ]}

    @app.get("/")
    async def dashboard():
        """自带状态页：实时展示当前/最近走的是内网还是公网。"""
        path = Path(__file__).resolve().parent / "dashboard.html"
        try:
            html = path.read_text(encoding="utf-8")
        except OSError:
            html = "<h1>dsh-failover-proxy</h1><p>dashboard.html missing</p>"
        return Response(content=html, media_type="text/html; charset=utf-8")

    @app.api_route("/{path:path}", methods=["POST"])
    async def proxy_post(path: str, request: Request):
        if not (path.endswith("chat/completions") or path.endswith("/completions")):
            return Response(status_code=404, content='{"error":"not found"}',
                            media_type="application/json")
        body = await request.json()
        incoming_headers = {k: v for k, v in request.headers.items()}

        # 1) 内网优先（读到首块）
        resp, first = await _try_internal(cfg, client, body, incoming_headers)

        # 2) 内网连不上/无响应 → 回退公网
        route = "internal"
        if resp is None:
            route = "public"
            try:
                resp, first = await _try_public(cfg, client, body, incoming_headers)
            except Exception as exc:
                _note_route("failed")
                return Response(
                    status_code=502,
                    content=json.dumps({"error": {"message": f"failover proxy: internal unreachable and public failed: {exc}"}}),
                    media_type="application/json",
                    headers={"X-Failover-Route": "failed"},
                )
        if resp is None:
            _note_route("failed")
            return Response(
                status_code=502,
                content=json.dumps({"error": {"message": "failover proxy: public upstream unreachable"}}),
                media_type="application/json",
                headers={"X-Failover-Route": "failed"},
            )
        _note_route(route)
        return _stream_upstream(resp, first, extra_headers={"X-Failover-Route": route})

    return app, client


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="DSH 模型容灾代理")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--config", default=None, help="JSON 配置文件路径")
    parser.add_argument("--internal-url", dest="internal_url", default=None)
    parser.add_argument("--public-url", dest="public_url", default=None)
    parser.add_argument("--check", action="store_true", help="打印生效配置并退出")
    args = parser.parse_args()

    cfg = load_config(args)
    if args.check:
        print(json.dumps({
            "host": cfg.host, "port": cfg.port,
            "internal_url": cfg.internal_url, "internal_model": cfg.internal_model,
            "public_url": cfg.public_url, "public_model": cfg.public_model,
            "has_internal_token": bool(cfg.internal_token),
            "has_public_key": bool(cfg.public_key),
            "internal_token_len": len(cfg.internal_token),
            "public_key_len": len(cfg.public_key),
            "connect_timeout": cfg.connect_timeout,
            "first_byte_timeout": cfg.first_byte_timeout,
        }, ensure_ascii=False, indent=2))
        return

    import uvicorn

    app, _client = create_app(cfg)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
