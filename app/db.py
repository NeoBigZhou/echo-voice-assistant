# -*- coding: utf-8 -*-
"""db.py — ECHO 统一数据层（SQLite，单库 data/echo.db）

设计原则（区别于早期实现的渐进式堆叠）：
  * 单一事实来源：配置、命令历史、DSH 会话、会议、说话人、转写行、
    纪要记录、组件状态、日志、事件、API 密钥全部在同一个库。
  * WAL 模式 + 每次操作短连接，Windows 下稳定且支持多线程读写。
  * 版本化迁移：schema_version 记录当前版本，MIGRATIONS 顺序执行，
    未来加字段/加表只需追加一条迁移。
  * 时间统一用本地时间 ISO 字符串（datetime('now','localtime')）。
  * 所有写接口幂等、可重入；行级数据带 updated_at 便于未来同步/审计。

表概览：
  meta              schema 版本等元信息
  settings          配置（键值 + 面板渲染元数据：分组/类型/选项）
  commands          命令历史（生命周期：pending→sent→running→done/failed）
  dsh_sessions      ECHO 登记的 DSH 会话（命令会话/纪要会话/聊天会话）
  meetings          会议（一场一行，文件夹/时长/状态/转写配置）
  speakers          会议说话人（可改名/合并，UNIQUE(meeting_id,label)）
  lines             转写行（句级时间戳、说话人、修订标记；kind 预留扩展）
  summary_runs      纪要生成记录（可追加要求重新生成）
  component_states  组件运行状态快照（面板轮询展示）
  logs              运行日志（面板可查，替代散落 .log 文件）
  events            事件流（审计/统计/未来移动端推送）
  api_keys          外部触点（手机 App 等）的访问密钥（预留，默认关闭）
"""
import json
import os
import sqlite3
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(BASE_DIR), "data")
DB_FILE = os.path.join(DATA_DIR, "echo.db")

SCHEMA_VERSION = 1

# (version, sql) —— 顺序执行；新变更 append 即可
MIGRATIONS = [
    (1, """
    CREATE TABLE IF NOT EXISTS meta (
      key   TEXT PRIMARY KEY,
      value TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS settings (
      key         TEXT PRIMARY KEY,
      value       TEXT DEFAULT 'null',        -- JSON 编码的任意值
      grp         TEXT DEFAULT 'general',     -- 分组：general/voice/meeting/dsh/panel
      label       TEXT DEFAULT '',
      description TEXT DEFAULT '',
      value_type  TEXT DEFAULT 'str',         -- str/int/float/bool/json/list
      options     TEXT DEFAULT '[]',          -- JSON 候选值（面板下拉用）
      updated_at  TEXT DEFAULT (datetime('now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS commands (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      ts          TEXT DEFAULT (datetime('now','localtime')),
      source      TEXT DEFAULT 'api',         -- hotkey|wake|web|skill|api|mobile|test
      text        TEXT DEFAULT '',
      status      TEXT DEFAULT 'pending',     -- pending|sent|running|done|failed
      session_id  TEXT DEFAULT '',
      reply       TEXT DEFAULT '',            -- DSH 最终回复全文
      brief       TEXT DEFAULT '',            -- 语音简报（精简文本）
      duration_ms INTEGER DEFAULT 0,
      error       TEXT DEFAULT '',
      meta        TEXT DEFAULT '{}'           -- JSON 扩展（可放标记、会议关联等）
    );
    CREATE INDEX IF NOT EXISTS idx_commands_ts ON commands(ts DESC);

    CREATE TABLE IF NOT EXISTS dsh_sessions (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      name         TEXT DEFAULT '',           -- 用途名：command / summary / chat-xxx
      kind         TEXT DEFAULT 'chat',       -- command|summary|chat
      session_id   TEXT DEFAULT '',           -- DSH 侧 sessionId
      created_at   TEXT DEFAULT (datetime('now','localtime')),
      last_used_at TEXT DEFAULT ''
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_kind ON dsh_sessions(kind);

    CREATE TABLE IF NOT EXISTS meetings (
      id               INTEGER PRIMARY KEY AUTOINCREMENT,
      name             TEXT UNIQUE,           -- 文件夹名 2026-08-21_10-00-00
      title            TEXT DEFAULT '',       -- 可编辑标题
      started_at       TEXT DEFAULT '',
      ended_at         TEXT DEFAULT '',
      duration_seconds REAL DEFAULT 0,
      status           TEXT DEFAULT '',       -- recording|transcribing|done|error
      stt_model        TEXT DEFAULT 'small',
      stt_device       TEXT DEFAULT 'auto',
      diarize          INTEGER DEFAULT 0,
      segments         INTEGER DEFAULT 0,     -- 音频分段数
      audio_bytes      INTEGER DEFAULT 0,
      notes            TEXT DEFAULT '{}',     -- JSON 备注/标签（未来扩展）
      created_at       TEXT DEFAULT (datetime('now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS speakers (
      id         INTEGER PRIMARY KEY AUTOINCREMENT,
      meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
      label      TEXT NOT NULL,               -- S1/S2/SPEAKER_00…
      name       TEXT DEFAULT '',             -- 显示名：说话人1 / 张三
      color      TEXT DEFAULT '',             -- 面板颜色（预留）
      UNIQUE(meeting_id, label)
    );

    CREATE TABLE IF NOT EXISTS lines (
      id             INTEGER PRIMARY KEY AUTOINCREMENT,
      meeting_id     INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
      seg_index      INTEGER DEFAULT 1,       -- 第几段音频
      start          REAL DEFAULT 0,          -- 段内相对秒
      end            REAL DEFAULT 0,
      speaker_label  TEXT DEFAULT '',         -- 冗余存 label，避免 join 顺序问题
      text           TEXT DEFAULT '',
      kind           TEXT DEFAULT 'speech',   -- speech/note/action…（预留扩展）
      revised        INTEGER DEFAULT 0,
      updated_at     TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_lines_meeting ON lines(meeting_id, seg_index, start);

    CREATE TABLE IF NOT EXISTS summary_runs (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      meeting_id   INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
      extra        TEXT DEFAULT '',           -- 追加要求
      status       TEXT DEFAULT 'pending',    -- pending|done|failed
      requested_at TEXT DEFAULT (datetime('now','localtime')),
      finished_at  TEXT DEFAULT '',
      result_path  TEXT DEFAULT ''            -- summary.md 路径（预留）
    );

    CREATE TABLE IF NOT EXISTS component_states (
      name       TEXT PRIMARY KEY,            -- dsh|server|stt|tts|wake|hotkey|meeting|diarize
      status     TEXT DEFAULT 'unknown',      -- online|offline|active|idle|error|disabled
      detail     TEXT DEFAULT '',
      pid        INTEGER DEFAULT 0,
      updated_at TEXT DEFAULT (datetime('now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS logs (
      id      INTEGER PRIMARY KEY AUTOINCREMENT,
      ts      TEXT DEFAULT (datetime('now','localtime')),
      level   TEXT DEFAULT 'info',            -- debug|info|warn|error
      source  TEXT DEFAULT '',                -- assistant|meeting|dsh|hotkey|wake|api…
      message TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(id DESC);

    CREATE TABLE IF NOT EXISTS events (
      id      INTEGER PRIMARY KEY AUTOINCREMENT,
      ts      TEXT DEFAULT (datetime('now','localtime')),
      type    TEXT DEFAULT '',                -- command_sent|meeting_started|wake_fired…
      payload TEXT DEFAULT '{}'               -- JSON
    );

    CREATE TABLE IF NOT EXISTS api_keys (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      name         TEXT DEFAULT '',
      token        TEXT UNIQUE,
      scopes       TEXT DEFAULT '["read"]',   -- JSON 权限列表
      enabled      INTEGER DEFAULT 1,
      created_at   TEXT DEFAULT (datetime('now','localtime')),
      last_used_at TEXT DEFAULT ''
    );
    """),
]


# ---------------------------------------------------------------- 连接管理

def get_conn():
    """打开一个短连接（WAL + Row 工厂）。调用方负责 close/commit。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_write_lock = threading.Lock()


def _exec(sql, params=()):
    """写辅助：加锁 + 自动 commit/close。"""
    with _write_lock:
        conn = get_conn()
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


def _query(sql, params=()):
    conn = get_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _query_one(sql, params=()):
    conn = get_conn()
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------- 初始化/迁移

def init():
    """建库 + 跑迁移。幂等，可反复调用。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with _write_lock:
        conn = get_conn()
        try:
            conn.executescript(MIGRATIONS[0][1])
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            cur = int(row["value"]) if row else 0
            for version, sql in MIGRATIONS:
                if version > cur:
                    conn.executescript(sql)
                    conn.execute(
                        "INSERT INTO meta(key,value) VALUES('schema_version',?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (str(version),))
            conn.commit()
        finally:
            conn.close()
    return DB_FILE


# ---------------------------------------------------------------- 工具

def _json_dumps(v):
    return json.dumps(v, ensure_ascii=False)


def _json_loads(s, default=None):
    try:
        return json.loads(s)
    except Exception:
        return default if default is not None else {}


# ---------------------------------------------------------------- settings

def get_setting(key, default=None):
    row = _query_one("SELECT value FROM settings WHERE key=?", (key,))
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return row["value"]


def set_setting(key, value, grp="general", label="", description="", value_type="str", options=None):
    options = options or []
    _exec(
        "INSERT INTO settings(key,value,grp,label,description,value_type,options,updated_at) "
        "VALUES(?,?,?,?,?,?,?,datetime('now','localtime')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now','localtime')",
        (key, _json_dumps(value), grp, label, description, value_type, _json_dumps(options)))


def sync_setting_meta(key, grp, label, description, value_type, options):
    """仅同步设置的面板元数据（分组/说明/选项），保留用户已存的 value。"""
    _exec(
        "UPDATE settings SET grp=?, label=?, description=?, value_type=?, options=? WHERE key=?",
        (grp, label, description, value_type, _json_dumps(options or []), key))


def all_settings():
    rows = _query("SELECT * FROM settings ORDER BY grp, key")
    for r in rows:
        try:
            r["value"] = json.loads(r["value"])
        except Exception:
            pass
        try:
            r["options"] = json.loads(r["options"])
        except Exception:
            r["options"] = []
    return rows


def upsert_settings(mapping):
    """批量更新（仅更新 value，不覆盖元数据）。"""
    with _write_lock:
        conn = get_conn()
        try:
            for k, v in mapping.items():
                conn.execute(
                    "UPDATE settings SET value=?, updated_at=datetime('now','localtime') WHERE key=?",
                    (_json_dumps(v), k))
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------- commands

def add_command(text, source="api", status="pending", session_id="", meta=None):
    return _exec(
        "INSERT INTO commands(ts,source,text,status,session_id,meta) "
        "VALUES(datetime('now','localtime'),?,?,?,?,?)",
        (source, text, status, session_id, _json_dumps(meta or {})))


def update_command(cmd_id, **fields):
    """更新命令：status/reply/brief/duration_ms/error/session_id。"""
    allowed = {"status", "reply", "brief", "duration_ms", "error", "session_id"}
    sets, params = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            params.append(v)
    if not sets:
        return
    params.append(cmd_id)
    _exec(f"UPDATE commands SET {', '.join(sets)} WHERE id=?", params)


def list_commands(limit=100, offset=0):
    return _query(
        "SELECT * FROM commands ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))


def get_command(cmd_id):
    return _query_one("SELECT * FROM commands WHERE id=?", (cmd_id,))


def count_commands():
    return _query_one("SELECT COUNT(*) AS n FROM commands")["n"]


def clear_commands():
    _exec("DELETE FROM commands")


# ---------------------------------------------------------------- dsh_sessions

def upsert_session(kind, session_id, name=""):
    """每个 kind 至多一条（command/summary）。chat 会话不入此表。"""
    _exec(
        "INSERT INTO dsh_sessions(name,kind,session_id) VALUES(?,?,?) "
        "ON CONFLICT(kind) DO UPDATE SET session_id=excluded.session_id, name=excluded.name, "
        "last_used_at=datetime('now','localtime')",
        (name, kind, session_id))


def get_session(kind):
    return _query_one("SELECT * FROM dsh_sessions WHERE kind=?", (kind,))


def list_sessions():
    return _query("SELECT * FROM dsh_sessions ORDER BY kind")


def touch_session(kind):
    _exec("UPDATE dsh_sessions SET last_used_at=datetime('now','localtime') WHERE kind=?", (kind,))


# ---------------------------------------------------------------- meetings

def create_meeting(name, started_at="", stt_model="small", stt_device="auto", diarize=0):
    return _exec(
        "INSERT INTO meetings(name,started_at,stt_model,stt_device,diarize,status) "
        "VALUES(?,?,?,?,?,?)",
        (name, started_at, stt_model, stt_device, 1 if diarize else 0, "recording"))


def update_meeting(meeting_id, **fields):
    allowed = {"title", "ended_at", "duration_seconds", "status",
               "stt_model", "stt_device", "diarize", "segments", "audio_bytes", "notes"}
    sets, params = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            params.append(v)
    if not sets:
        return
    params.append(meeting_id)
    _exec(f"UPDATE meetings SET {', '.join(sets)} WHERE id=?", params)


def get_meeting_by_name(name):
    return _query_one("SELECT * FROM meetings WHERE name=?", (name,))


def get_meeting(meeting_id):
    return _query_one("SELECT * FROM meetings WHERE id=?", (meeting_id,))


def list_meetings(limit=100, offset=0):
    return _query("SELECT * FROM meetings ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))


def delete_meeting(meeting_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM meetings WHERE id=?", (meeting_id,))
        conn.commit()
    finally:
        conn.close()


def set_meeting_status_by_name(name, status):
    _exec("UPDATE meetings SET status=? WHERE name=?", (status, name))


def clear_meeting_lines(meeting_id):
    """清空说话人+转写行（重新转写前调用；保留纪要记录）。"""
    conn = get_conn()
    try:
        conn.execute("DELETE FROM speakers WHERE meeting_id=?", (meeting_id,))
        conn.execute("DELETE FROM lines WHERE meeting_id=?", (meeting_id,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- speakers

def replace_speakers(meeting_id, speaker_map):
    """speaker_map: {label: 显示名}。保留用户改过名的条目。"""
    conn = get_conn()
    try:
        existing = {r["label"]: r["name"] for r in conn.execute(
            "SELECT label,name FROM speakers WHERE meeting_id=?", (meeting_id,)).fetchall()}
        conn.execute("DELETE FROM speakers WHERE meeting_id=?", (meeting_id,))
        for label, default_name in speaker_map.items():
            name = existing.get(label, "") or default_name
            conn.execute(
                "INSERT OR IGNORE INTO speakers(meeting_id,label,name) VALUES(?,?,?)",
                (meeting_id, label, name))
        conn.commit()
    finally:
        conn.close()


def get_speakers(meeting_id):
    return _query("SELECT * FROM speakers WHERE meeting_id=? ORDER BY id", (meeting_id,))


def rename_speaker(meeting_id, label, new_name):
    _exec("UPDATE speakers SET name=? WHERE meeting_id=? AND label=?",
          (new_name.strip(), meeting_id, label))


def merge_speakers(meeting_id, source_label, target_label):
    """把 source_label 合并进 target_label：所有行改标 target，删除 source。"""
    conn = get_conn()
    try:
        conn.execute("UPDATE lines SET speaker_label=? WHERE meeting_id=? AND speaker_label=?",
                     (target_label, meeting_id, source_label))
        conn.execute("DELETE FROM speakers WHERE meeting_id=? AND label=?",
                     (meeting_id, source_label))
        conn.commit()
    finally:
        conn.close()


def cleanup_empty_speakers(meeting_id):
    _exec("""DELETE FROM speakers WHERE meeting_id=? AND label NOT IN
             (SELECT DISTINCT speaker_label FROM lines
              WHERE meeting_id=? AND speaker_label != '')""",
          (meeting_id, meeting_id))


# ---------------------------------------------------------------- lines

def add_lines(meeting_id, rows):
    """rows: [(seg_index, start, end, speaker_label, text), ...]"""
    if not rows:
        return
    with _write_lock:
        conn = get_conn()
        try:
            conn.executemany(
                "INSERT INTO lines(meeting_id,seg_index,start,end,speaker_label,text) "
                "VALUES(?,?,?,?,?,?)",
                [(meeting_id, seg, s, e, spk, txt) for seg, s, e, spk, txt in rows])
            conn.commit()
        finally:
            conn.close()


def get_lines(meeting_id):
    return _query(
        "SELECT * FROM lines WHERE meeting_id=? ORDER BY seg_index, start, id", (meeting_id,))


def update_line(line_id, new_text):
    _exec("UPDATE lines SET text=?, revised=1, updated_at=datetime('now','localtime') WHERE id=?",
          (new_text.strip(), line_id))


def set_line_kind(line_id, kind):
    _exec("UPDATE lines SET kind=? WHERE id=?", (kind, line_id))


# ---------------------------------------------------------------- summary_runs

def add_summary_run(meeting_id, extra=""):
    return _exec("INSERT INTO summary_runs(meeting_id,extra) VALUES(?,?)", (meeting_id, extra))


def finish_summary_run(run_id, status="done", result_path=""):
    _exec("UPDATE summary_runs SET status=?, finished_at=datetime('now','localtime'), "
          "result_path=? WHERE id=?", (status, result_path, run_id))


def get_summary_runs(meeting_id):
    return _query("SELECT * FROM summary_runs WHERE meeting_id=? ORDER BY id DESC", (meeting_id,))


# ---------------------------------------------------------------- component_states

def set_component_state(name, status, detail="", pid=0):
    _exec(
        "INSERT INTO component_states(name,status,detail,pid,updated_at) "
        "VALUES(?,?,?,?,datetime('now','localtime')) "
        "ON CONFLICT(name) DO UPDATE SET status=excluded.status, detail=excluded.detail, "
        "pid=excluded.pid, updated_at=datetime('now','localtime')",
        (name, status, detail, pid))


def get_component_states():
    return _query("SELECT * FROM component_states ORDER BY name")


def get_component_state(name):
    return _query_one("SELECT * FROM component_states WHERE name=?", (name,))


# ---------------------------------------------------------------- logs / events

def add_log(level, source, message):
    try:
        _exec("INSERT INTO logs(level,source,message) VALUES(?,?,?)",
              (level, source, str(message)[:2000]))
    except Exception:
        pass


def list_logs(limit=200, level="", source=""):
    conds, params = [], []
    if level:
        conds.append("level=?")
        params.append(level)
    if source:
        conds.append("source=?")
        params.append(source)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    params.append(limit)
    return _query(f"SELECT * FROM logs{where} ORDER BY id DESC LIMIT ?", tuple(params))


def clear_logs():
    _exec("DELETE FROM logs")


def add_event(type_, payload=None):
    try:
        _exec("INSERT INTO events(type,payload) VALUES(?,?)",
              (type_, _json_dumps(payload or {})))
    except Exception:
        pass


def list_events(limit=200):
    return _query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))


# ---------------------------------------------------------------- api_keys（预留）

def add_api_key(name, scopes=None):
    import secrets
    token = "echo_" + secrets.token_hex(24)
    _exec("INSERT INTO api_keys(name,token,scopes) VALUES(?,?,?)",
          (name, token, _json_dumps(scopes or ["read"])))
    return token


def list_api_keys():
    rows = _query("SELECT id,name,token,scopes,enabled,created_at,last_used_at FROM api_keys")
    for r in rows:
        r["scopes"] = _json_loads(r["scopes"], [])
    return rows


def verify_api_key(token):
    row = _query_one("SELECT * FROM api_keys WHERE token=? AND enabled=1", (token,))
    if row:
        _exec("UPDATE api_keys SET last_used_at=datetime('now','localtime') WHERE id=?", (row["id"],))
        row["scopes"] = _json_loads(row["scopes"], [])
        return row
    return None


def delete_api_key(key_id):
    _exec("DELETE FROM api_keys WHERE id=?", (key_id,))
