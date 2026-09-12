# -*- coding: utf-8 -*-
"""dsh.py — DSH Desktop 2.x 本地 RPC 客户端与会话管理

新版 DeepSeek Harness（DSH Desktop 2.0.4 / @deepseek-ai/dsh 0.1.2-alpha.1）不再
提供独立的 3080 服务，改为桌面客户端内置 Web 服务（GUI 与 API 同端口，默认
http://127.0.0.1:43120）。JSON-RPC 风格端点挂在 /api/<namespace/method>：

    请求：{"type":"client-request","rpcId":"...","method":"session/list",
           "payload":{"args":{...}}}           # payload 必须恰好一个 args 键
    响应：{"type":"server-response","rpcId":"...","result":{"ok":true,"value":...}}

注意：URL 路径与 body 的 method 字段均为斜杠分隔（session/list，不是 session.list）。

访问控制（两层，均需通过）：
  1. Desktop 网关：只放行带 x-dsh-desktop-renderer 令牌的请求（令牌每次启动随机
     生成、仅存主进程内存，外部不可得）。外部程序必须让桌面版处于"普通浏览器
     访问"开启状态：~/.dsh/settings.yaml 中 dsh-desktop.mode = compatibility 且
     openBrowser = true（仅本机 loopback，安全）。
  2. /api 通道：校验签名 Cookie dsh-auth-<b64(sha256(authority))>=v1.<body>.<sig>，
     签名密钥在 ~/.dsh/.credentials.yaml 的
     records['client-connection/browser-session'].payload.secret（base64url 32B），
     本模块启动时读取并自铸 Cookie（HMAC-SHA256，与桌面版同一算法）。

常用方法（对齐 0.1.2-alpha.1）：
    session/create  args {request:{workspaceId?,cwd?,sessionId?,agentPreset?}} -> {ok,value:{sessionId}}
    session/list    args {_request:{}}                                        -> {ok,value:{items:[...]}}
    session/prompt  args {request:{requestId,sessionId,mode,content}}         -> {ok}
    session/cancel  args {request:{sessionId}}                                -> {ok}
    session/page    args {request:{address:{kind:'session',sessionId},throughSeq,maxMessages}}
                                                                              -> {ok,value:{records:[{type,event}],hasMore}}
    session.history 在新版已移除，本模块的 history() 用 session.page 兼容实现。
"""
import base64
import datetime
import hashlib
import hmac
import json
import os
import re
import time
import uuid
import urllib.error
import urllib.request

import app.db as db
from app.config import settings

DEFAULT_BASE_URL = "http://127.0.0.1:43120"

CREDENTIALS_PATH = os.path.join(os.path.expanduser("~"), ".dsh", ".credentials.yaml")
AUTH_RECORD_KEY = "client-connection/browser-session"
COOKIE_PREFIX = "dsh-auth-"
COOKIE_MAX_AGE_DAYS = 30   # 与桌面版默认一致（服务端校验 expiresAt <= issuedAt+30d）


class DshError(Exception):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _load_browser_secret() -> str:
    """从 ~/.dsh/.credentials.yaml 提取 browser-session 签名密钥（base64url 文本）。"""
    try:
        with open(CREDENTIALS_PATH, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return ""
    # 找 records: 下的 client-connection/browser-session 段落里的 secret:
    in_records = False
    in_record = False
    for raw in lines:
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        key = line.strip()
        if indent == 0:
            in_records = key.startswith("records:")
            in_record = False
            continue
        if not in_records:
            continue
        if in_record and key.startswith("secret:"):
            return key.split(":", 1)[1].strip().strip("'\"")
        if key == AUTH_RECORD_KEY + ":":
            in_record = True
    return ""


def _make_cookie(base_url: str) -> str:
    """按桌面版算法自铸签名 Cookie（payload v1 + HMAC-SHA256，authority=Host 头）。"""
    secret_b64 = _load_browser_secret()
    if not secret_b64:
        raise DshError(
            f"无法读取浏览器会话密钥（{CREDENTIALS_PATH} 中缺少 "
            f"records.{AUTH_RECORD_KEY}.payload.secret）")
    secret = _b64url_decode(secret_b64)

    from urllib.parse import urlsplit
    host = urlsplit(base_url).netloc  # 127.0.0.1:43120
    authority = host if host else "127.0.0.1:43120"

    name = COOKIE_PREFIX + _b64url(hashlib.sha256(authority.encode("utf-8")).digest())
    now_ms = int(time.time() * 1000)
    payload = {
        "version": 1,
        "authority": authority,
        "issuedAt": now_ms,
        "expiresAt": now_ms + COOKIE_MAX_AGE_DAYS * 86400 * 1000,
    }
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64url(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
    return f"{name}=v1.{body}.{sig}"


class DshClient:
    def __init__(self, base_url=None):
        self.base_url = (base_url or settings.get("dshBaseUrl", DEFAULT_BASE_URL)).rstrip("/")
        self._cookie = None
        self._cookie_ts = 0.0

    # ------------------------------------------------------------- 底层 RPC

    def _cookie_header(self):
        """Cookie 约 5 分钟重铸一次（服务端仅校验时间窗口，宽松即可）。"""
        if not self._cookie or time.time() - self._cookie_ts > 300:
            self._cookie = _make_cookie(self.base_url)
            self._cookie_ts = time.time()
        return self._cookie

    def rpc(self, method, args=None, timeout=15):
        body = {
            "type": "client-request",
            "rpcId": f"echo-{uuid.uuid4().hex[:12]}",
            "method": method,
            "payload": {"args": args or {}},
        }
        req = urllib.request.Request(
            self.base_url + "/api/" + method,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Cookie": self._cookie_header(),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            hint = ""
            if e.code in (401, 403):
                hint = ("。请确认 DSH Desktop 已运行，且已开启普通浏览器访问"
                        "（~/.dsh/settings.yaml: dsh-desktop.mode = compatibility 且 "
                        "openBrowser = true，修改后需重启 DSH Desktop）")
            raise DshError(f"DSH RPC {method} 失败: HTTP {e.code} {e.reason}{hint}") from e
        except Exception as e:
            raise DshError(f"DSH RPC {method} 失败: {e}（DSH Desktop 是否已启动？）") from e
        result = data.get("result") or {}
        if not result.get("ok"):
            err = result.get("error") or {}
            raise DshError(f"DSH RPC {method} 返回错误: {err.get('code')} {err.get('message')}")
        return result

    def ping(self):
        """探活：带 Cookie 调 session.list 是否成功。"""
        try:
            self.rpc("session/list", {"_request": {}}, timeout=3)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------- 会话

    def create_session(self, cwd=None):
        args = {"request": {"cwd": cwd} if cwd else {}}
        res = self.rpc("session/create", args)
        return (res.get("value") or {}).get("sessionId", "")

    def list_sessions(self):
        res = self.rpc("session/list", {"_request": {}}, timeout=8)
        return (res.get("value") or {}).get("items", []) or []

    # ----------------------------------------------------- 命令目标（工作区/对话）

    def list_workspaces(self):
        """聚合全部会话的工作区（cwd）列表。"""
        ws = set()
        for it in self.list_sessions():
            cwd = it.get("cwd")
            if cwd:
                ws.add(cwd)
        return sorted(ws)

    def list_sessions_for(self, workspace=None):
        """返回会话列表（可只按工作区过滤），附带标题/时间等展示信息。"""
        items = []
        for it in self.list_sessions():
            if workspace is not None and (it.get("cwd") or "") != workspace:
                continue
            proj = it.get("projections") or {}
            items.append({
                "sessionId": it.get("sessionId"),
                "title": (proj.get("values") or {}).get("title") or "",
                "cwd": it.get("cwd") or "",
                "updatedAt": it.get("updatedAt"),
                "running": bool(it.get("running")),
                "blank": bool(it.get("blank")),
            })
        items.sort(key=lambda x: -(x["updatedAt"] or 0))
        return items

    def resolve_target_session(self, workspace=None, session_id=None):
        """解析命令发送目标会话：
        - 显式 session_id → 直接用
        - 指定工作区 → 该工作区最近更新的会话，没有则新建
        - 否则返回 None（由调用方走 ensure_session 默认路径）
        """
        if session_id:
            return session_id
        if workspace:
            for it in self.list_sessions_for(workspace):
                if not it.get("blank"):
                    return it["sessionId"]
            return self.create_session(cwd=workspace)
        return None

    def _cursor_of(self, session_id):
        """取会话当前写入游标（session.list 的 projections.asOfSeq），用作 page 的
        throughSeq。throughSeq=-1 会读空，超过游标会 bad-request，必须取实时值。"""
        try:
            for item in self.list_sessions():
                if item.get("sessionId") == session_id:
                    proj = item.get("projections") or {}
                    return proj.get("asOfSeq")
        except Exception:
            pass
        return None

    def history(self, session_id, max_messages=80):
        """新版无 session.history：用 session.page 拉最近事件，返回兼容旧解析的
        [{"event": {seq, type, data, ...}}, ...]。"""
        through = self._cursor_of(session_id)
        if through is None:
            return []
        args = {
            "request": {
                "address": {"kind": "session", "sessionId": session_id},
                "throughSeq": through,
                "maxMessages": max_messages,
            }
        }
        try:
            res = self.rpc("session/page", args, timeout=10)
        except DshError:
            # 竞态：游标在读取间隙又推进导致越界，降级重试一次
            through = self._cursor_of(session_id)
            if through is None:
                return []
            args["request"]["throughSeq"] = through
            res = self.rpc("session/page", args, timeout=10)
        records = (res.get("value") or {}).get("records", []) or []
        return [{"event": r.get("event") or {}} for r in records]

    def prompt(self, session_id, text, mode="queue"):
        args = {
            "request": {
                "requestId": str(uuid.uuid4()),
                "sessionId": session_id,
                "mode": mode,
                "content": [{"type": "text", "text": text}],
            }
        }
        res = self.rpc("session/prompt", args, timeout=10)
        return res.get("ok", False)

    def cancel(self, session_id):
        """取消会话当前正在执行的一轮（模型卡在交互提问/工具时用于解卡）。"""
        try:
            return self.rpc("session/cancel", {"request": {"sessionId": session_id}}, timeout=8)
        except Exception:
            return None

    def is_running(self, session_id):
        try:
            for item in self.list_sessions():
                if item.get("sessionId") == session_id:
                    return bool(item.get("running"))
        except Exception:
            pass
        return False

    def clear_stuck(self, session_id):
        """会话若被卡住（running 且上一轮未完成），取消之，保证新命令可进。"""
        if self.is_running(session_id):
            self.cancel(session_id)
            time.sleep(1)
            return True
        return False

    # ------------------------------------------------------------- 命令流程

    # 延续意图词：文本命中任一即视为用户要求接着上一话题（不轮换默认会话）。
    # 词表偏宽是刻意的——误保留旧会话最多多耗一次上下文，误轮换则丢衔接。
    _CONTINUATION_RE = re.compile(
        r"继续|接着说|接着上|上一(个|次|段|轮|条)?(话题|问题|对话|内容|指令|命令)?"
        r"|刚才|刚才说|上回|上次|回顾|回到刚才|之前(说|讨论|聊|提到|那个|的)"
        r"|往下说|然后呢|还有呢|聊到哪|说到哪")

    def ensure_command_session(self, text=""):
        """取默认（command）命令会话；若距上次使用超过 commandIdleRotateHours
        小时且本次命令未要求延续上一话题，则**新开会话并替换默认会话**，
        避免长会话的上下文污染与 token 浪费。返回会话 id（可能 None）。
        commandIdleRotateHours=0 或空 时关闭自动轮换。"""
        row = db.get_session("command")
        sid = (row or {}).get("session_id") or ""
        idle_h = None
        if row and sid:
            used = (row.get("last_used_at") or "").strip()
            try:
                last = datetime.datetime.strptime(used, "%Y-%m-%d %H:%M:%S")
                idle_h = (datetime.datetime.now() - last).total_seconds() / 3600.0
            except (ValueError, TypeError):
                idle_h = None
        rotate_hours = float(settings.get("commandIdleRotateHours", 4) or 0)
        wants_cont = bool(self._CONTINUATION_RE.search(text or ""))
        if (sid and idle_h is not None and rotate_hours > 0
                and idle_h >= rotate_hours and not wants_cont):
            new_sid = self.create_session(cwd=ECHO_WORKSPACE)
            if new_sid:
                db.upsert_session("command", new_sid, "命令会话")  # 顺带刷新 last_used_at
                db.add_log("info", "assistant",
                           f"默认命令会话空闲 {idle_h:.1f}h（阈值 {rotate_hours:g}h）"
                           f"且未要求延续，已轮换新会话 {new_sid}")
                sid = new_sid
        if not sid:
            sid = self.create_session(cwd=ECHO_WORKSPACE)
            if sid:
                db.upsert_session("command", sid, "命令会话")
        db.touch_session("command")  # 记录本次使用时间，作为下次轮换依据
        return sid

    def ensure_session(self, kind, name=""):
        """取回（无则创建）指定用途的 DSH 会话：command / summary。"""
        row = db.get_session(kind)
        if row and row.get("session_id"):
            return row["session_id"]
        sid = self.create_session(cwd=ECHO_WORKSPACE)
        if sid:
            db.upsert_session(kind, sid, name or kind)
        return sid

    def wait_for_reply(self, session_id, timeout=90, poll=0.5):
        """发送后轮询会话事件，等待助手最终回复。

        返回 (reply, done)：
          reply — 最后一条 assistant 文本（中间消息会被最终回复覆盖）
          done  — 会话空闲（running=false）或事件序列稳定

        信号1：session.list 中该会话 running=false
        信号2：事件 seq 停止推进且已取到回复
        """
        # 记录发送前最后事件 seq，只认新消息
        sent_seq = None
        try:
            events = self.history(session_id, max_messages=3)
            if events:
                sent_seq = events[-1]["event"].get("seq")
        except Exception:
            pass

        reply = None
        last_seq = -1
        stable = 0
        saw_turn_end = False
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(poll)
            done_now = False
            try:
                for item in self.list_sessions():
                    if item.get("sessionId") == session_id:
                        done_now = not bool(item.get("running"))
                        break
            except Exception:
                pass
            cur_seq = -1
            try:
                events = self.history(session_id, max_messages=120)
                if events:
                    cur_seq = events[-1]["event"].get("seq", -1)
                latest = None
                for ev in events:
                    ev_obj = ev.get("event") or {}
                    if sent_seq is not None and ev_obj.get("seq", 0) <= sent_seq:
                        continue
                    if ev_obj.get("type") == "turn/end":
                        # 本轮用户请求完整处理结束的可靠信号（DSH 会多次 read 大文件，
                        # 工具调用间隙 running 会短暂闪断，不能据此判定完成）
                        saw_turn_end = True
                    if ev_obj.get("type") == "assistant/message":
                        # 新版 data 直接是 message（旧版是 {"message": ...}），两者都兼容
                        msg = ev_obj.get("data") or {}
                        if isinstance(msg, dict) and isinstance(msg.get("message"), dict):
                            msg = msg["message"]
                        if msg.get("role") == "assistant":
                            texts = [c.get("text", "") for c in (msg.get("content") or [])
                                     if isinstance(c, dict) and c.get("type") == "text" and c.get("text")]
                            if texts:
                                latest = "".join(texts).strip()
                if latest:
                    reply = latest  # 持续用最新助手文本覆盖，turn 结束时即为最终交付
            except Exception:
                pass
            if cur_seq == last_seq:
                stable += 1
            else:
                stable = 0
                last_seq = cur_seq
            # 完成判定：拿到本轮 turn/end 且已取到助手文本且事件序列稳定（trailing 消息落定）。
            # 兜底：无法识别 turn/end（旧版本）时，会话空闲 + 回复稳定才返回。
            if saw_turn_end and reply and stable >= 2:
                break
            if (not saw_turn_end) and done_now and reply and stable >= 8:
                break
        if reply is None:
            # 超时未收到回复：取消当前轮，避免会话被卡住影响下一条命令
            try:
                self.cancel(session_id)
            except Exception:
                pass
        return reply, done_now


# ECHO 工作区（命令会话在此目录下创建，GUI 会话列表中归属清晰）
ECHO_WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 模块级单例（API/assistant 共用）
_client = None


def get_client():
    global _client
    if _client is None:
        _client = DshClient()
    return _client
