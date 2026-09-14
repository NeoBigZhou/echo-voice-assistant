# -*- coding: utf-8 -*-
"""把 ECHO 的 summary/command 会话权限提升为 danger-full-access + never，
使 DSH 能直接读写 ECHO 的 data 目录（生成纪要/分段摘要）。
用法：先停止 DSH，运行本脚本，再启动 DSH。
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
