# -*- coding: utf-8 -*-
"""worklog.py — 会议纪要归档（委派给用户自己的归档技能）

设计（2026-09-12 定稿）
----------------------
ECHO 在这个环节**不做任何归档决策**，只负责三件事：

  1. 把纪要拼成一份自包含的 markdown 落到本地（`{meeting}/meeting_note.md`）；
  2. 把「笔记库路径 + 会议结构化信息 + 该 md 的绝对路径 + 归档要求」送进 DSH；
  3. 把 DSH（其实是用户的归档技能）回的一句话结果原样带回面板。

写哪个目录、日志什么格式、有哪些专项与年会体系——**全部是技能的事**。
所以 ECHO 代码里不再出现任何个人笔记库路径、专项清单或单位会议体系，
第三方用户只要写好自己的归档技能，改两个设置就能用。

会话与工作目录（2026-09-15 定稿：一场会议一个会话）
--------------------------------------------------
归档复用**本场会议的纪要会话**（见 db.meeting_sessions），使同一场会议的
纪要/分段/归档消息都在同一个会话里，并在 DSH 侧栏正确归入会议工作区。
提示词里给的是 `{vault}` 与 `{md_path}` 的**绝对路径**，所以不依赖会话的工作
目录，技能照常能完成归档。
（老会议若没有登记会话，兜底会在笔记库目录新建一个会话；那种会话会落到
DSH 的「未分组」，仅作兼容。）
"""
import os

import app.db as db
from app.config import settings
from app.dsh import get_client

# 归档 md 在会议目录下的固定文件名
NOTE_FILENAME = "meeting_note.md"

# 等待 DSH 完成的超时：归档是自主多步操作（读文件 → 判归属 → 多文件写入），
# 比单轮问答慢，给足时间。
ARCHIVE_TIMEOUT = 600


# ---------------------------------------------------------------- 配置

def enabled():
    """归档总开关（未启用/未配置笔记库时，面板不提供写工作日志）。"""
    return bool(settings.get("worklogEnabled", False))


def vault_root():
    """笔记库根目录；未配置返回空串。"""
    return (settings.get("worklogVaultRoot", "") or "").strip()


def mode():
    """归档方式：skill=委派归档技能；off=不归档。"""
    return (settings.get("worklogMode", "skill") or "skill").strip()


def ready():
    """是否具备归档条件，返回 (ok, 原因)。"""
    if not enabled():
        return False, "纪要归档未启用（设置 → 纪要归档 → 启用纪要归档）"
    if mode() == "off":
        return False, "归档方式为「不归档」"
    vault = vault_root()
    if not vault:
        return False, "未配置笔记库根目录（设置 → 纪要归档 → 笔记库根目录）"
    if not os.path.isdir(vault):
        return False, f"笔记库根目录不存在：{vault}"
    return True, ""


# ---------------------------------------------------------------- 材料准备

def _speakers_text(meeting_id):
    """参会人：取该会议说话人显示名；分不出人时返回「未区分」。"""
    try:
        rows = db.get_speakers(meeting_id) or []
    except Exception:
        rows = []
    names = []
    for r in rows:
        n = (r.get("name") or r.get("label") or "").strip()
        if n and n not in names:
            names.append(n)
    return "、".join(names) if names else "未区分"


def export_note(meeting, folder, full_text, content=""):
    """把完整纪要落成一份自包含 markdown，返回绝对路径。

    内容优先用拼装好的全文（摘要→纪要→议题分段→转写详情），
    只有在全文为空时才退回单份 summary 正文。
    """
    body = (full_text or content or "").strip()
    if not body:
        return ""
    path = os.path.join(folder, NOTE_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body if body.endswith("\n") else body + "\n")
    return path


def render_prompt(meeting, note_path, archive_hint="", date_str="", hour=None):
    """按模板渲染送入 DSH 的归档提示词。

    模板里的占位符全部来自设置（worklogPrompt），用户可自行改写；
    未知占位符不会抛错（保持原样），避免用户写错一个花括号就整个归档失败。
    """
    started = (meeting.get("started_at") or "").replace("T", " ")[:16]
    duration = int(round(meeting.get("duration_seconds") or 0))
    values = {
        "vault": vault_root(),
        "title": (meeting.get("title") or "").strip() or meeting.get("name", ""),
        "started_at": started or "未知",
        "date": date_str or "未知",
        "hour": "" if hour is None else str(hour),
        "duration": str(duration),
        "speakers": _speakers_text(meeting.get("id")),
        "md_path": note_path,
        "archive_hint": (archive_hint or "").strip() or "（无，按你的默认规则判断）",
        "meeting_id": meeting.get("name", ""),
    }

    class _Safe(dict):
        """缺失的键原样保留 {key}，不让用户模板失误变成异常。"""
        def __missing__(self, key):
            return "{" + key + "}"

    tpl = settings.get("worklogPrompt", "") or ""
    if not tpl.strip():
        return ""
    return tpl.format_map(_Safe(values))


# ---------------------------------------------------------------- 委派执行

def delegate_archive(meeting, note_path, archive_hint="", date_str="", hour=None):
    """把归档任务送进 DSH，等技能做完，返回 (ok, 人话结果)。

    会话选择（2026-09-15 定稿：一场会议一个会话）：
      优先复用本场会议的纪要会话（db.meeting_sessions 里登记的），这样归档消息
      也落在同一场会议的会话里，与会话分组一致；
      只有在拿不到该会话时（老会议 / 已归档）才退回"笔记库目录新建会话"。
      —— 注意：那种老方式建在笔记库目录下，会落到 DSH 的「未分组」，仅作兜底。

    不做任何结果解析：技能回什么就带什么给面板。
    """
    prompt = render_prompt(meeting, note_path, archive_hint=archive_hint,
                           date_str=date_str, hour=hour)
    if not prompt:
        return False, "归档提示词为空（设置 → 纪要归档 → 归档提示词模板）"
    client = get_client()
    try:
        # ① 复用本场会议的会话（与纪要同一会话）
        sid = ""
        try:
            row = db.get_meeting_session(meeting.get("name", ""))
            if row and row.get("session_id"):
                sid = row["session_id"]
        except Exception:
            sid = ""
        reused = bool(sid)
        if not reused:
            # ② 兜底：在笔记库目录新建（会落到未分组）
            sid = client.create_session(cwd=vault_root())
            db.add_log("warn", "meeting",
                       "本场会议没有可用会话，归档改为在笔记库目录新建会话"
                       "（该会话会落在 DSH 未分组）")
        if not sid:
            return False, "创建归档会话失败（DSH 未就绪？）"
        client.clear_stuck(sid)
        client.prompt(sid, prompt, mode="queue")
        reply, _done = client.wait_for_reply(sid, timeout=ARCHIVE_TIMEOUT, poll=2)
    except Exception as e:
        db.add_log("error", "meeting", f"归档委派失败: {e}")
        return False, f"归档委派失败：{e}"
    result = (reply or "").strip()
    if not result:
        return False, "归档技能未返回结果（可能超时，详见服务日志）"
    db.add_log("info", "meeting",
               f"归档委派完成（{'复用会议会话' if reused else '新建兜底会话'} {sid}）：{result[:200]}")
    return True, result
