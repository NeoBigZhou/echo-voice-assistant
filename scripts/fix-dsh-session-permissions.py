# -*- coding: utf-8 -*-
"""【已过时，优先用运行时自愈】把 ECHO 的 summary/command 会话权限提升为
danger-full-access + never，使 DSH 能直接读写 ECHO 的 data 目录（生成纪要/分段摘要）。
用法：先停止 DSH，运行本脚本，再启动 DSH。

2026-09-15 起不再需要它：DSH 把会话权限存进 `~/.dsh/storages/session_projcache/
sessions/<sessionId>.json`（本脚本改的聚合文件 `session_projcache.json` 是旧格式），
而会议归档改成"一场会议一个会话"后，这里覆盖的 summary/command 会话也不是归档会话了。
现行做法见 app/worklog.py：每次归档前（以及 ECHO 启动时）用 settings RPC 把
`permission.defaultPreset` 校正为 danger-full-access —— live 生效、无需重启 DSH。
本脚本保留仅作历史参考。
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app.db as db

CACHE = os.path.expanduser(r"~\.dsh\storages\session_projcache.json")
BACKUP = CACHE + ".bak-echo"

# 1) 备份
shutil.copy2(CACHE, BACKUP)
print("已备份:", BACKUP)

# 2) 读并修改
with open(CACHE, "r", encoding="utf-8") as f:
    data = json.load(f)

sessions = data["tables"]["sessions"]
changed = []
for kind in ("summary", "command"):
    row = db.get_session(kind)
    if not row:
        print("无", kind, "会话记录")
        continue
    sid = row["session_id"]
    s = sessions.get(sid)
    if not s:
        print(kind, "会话不在缓存:", sid)
        continue
    perm = s.setdefault("rows", {}).setdefault("permissions", {}).setdefault("val", {})
    old = dict(perm)
    perm["preset"] = "danger-full-access"
    perm["sandbox"] = "danger-full-access"
    perm["approval"] = "never"
    changed.append((kind, sid, old, dict(perm)))

with open(CACHE, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

for kind, sid, old, new in changed:
    print(f"[{kind}] {sid}")
    print("   old:", old)
    print("   new:", new)

print("\n完成。现在请启动 DSH，权限将生效。")
