---
name: meeting-record
description: 快速开始或结束 ECHO 会议录音。用户说"开始录音/录会议/开始会议录音"时调用开始接口并仅回复"已开始录音"；用户说"结束录音/停止录音/停录/结束会议录音"时调用结束接口并仅回复"已结束录音"。
whenToUse: 用户要求开始或结束会议录音、开始录会议、停止录音、停录等场景
---

# 会议录音开关（ECHO 版）

通过 ECHO 本地服务（端口 8970；如改了 ECHO 面板设置里的 `serverPort`，以实际值为准）快速开始/结束会议录音。开始或结束后**只回复指定的一句话**，不要附加说明、路径或其他内容。

## 开始录音

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8970/api/meeting/start' -Method Post -ContentType 'application/json' -Body '{}' -TimeoutSec 10
```

- 返回 `ok: true` → **只回复**：「已开始录音」
- 返回 `ok: false` → 只简短回复返回的 `message`（例如「会议录音已在进行中」）

## 结束录音

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8970/api/meeting/stop' -Method Post -ContentType 'application/json' -Body '{}' -TimeoutSec 10
```

- 返回 `ok: true` → **只回复**：「已结束录音」
- 返回 `ok: false` → 只简短回复返回的 `message`（例如「没有进行中的会议」）

## 异常处理

- 连接失败/超时（ECHO 服务未启动）→ 回复：「ECHO 服务未运行」。
- 每次只调用一次对应接口，不要重复调用或轮询；如需确认真实状态，可查 `GET http://127.0.0.1:8970/api/meeting/status` 的 `active` 字段。
