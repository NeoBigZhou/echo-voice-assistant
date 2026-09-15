---
name: meeting-record
description: 快速开始或结束 ECHO 会议录音。用户说"开始录音/录会议/开始会议录音"时调用开始接口并仅回复"已开始录音"；用户说"结束录音/停止录音/停录/结束会议录音"时调用结束接口并仅回复"已结束录音"。
whenToUse: 用户要求开始或结束会议录音、开始录会议、停止录音、停录等场景
---

# 会议录音开关（ECHO 版）

通过 ECHO 本地服务快速开始/结束会议录音。**端口不要写死**：优先环境变量 `ECHO_PORT`，
否则读 ECHO 仓库的 `data\echo-port.txt`（ECHO 启动时写出的权威端口；两个都拿不到才按默认 8970 试）。
开始或结束后**只回复指定的一句话**，不要附加说明、路径或其他内容。

## 开始录音

```powershell
# 端口解析：ECHO_PORT → .\data\echo-port.txt（在 ECHO 仓库根目录下执行）→ 8970
$port = if ($env:ECHO_PORT) { [int]$env:ECHO_PORT }
        elseif (Test-Path '.\data\echo-port.txt') { [int](Get-Content '.\data\echo-port.txt' -Raw).Trim() }
        else { 8970 }
Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/meeting/start" -Method Post -ContentType 'application/json' -Body '{}' -TimeoutSec 10
```

- 返回 `ok: true` → **只回复**：「已开始录音」
- 返回 `ok: false` → 只简短回复返回的 `message`（例如「会议录音已在进行中」）

## 结束录音

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/meeting/stop" -Method Post -ContentType 'application/json' -Body '{}' -TimeoutSec 10
```

- 返回 `ok: true` → **只回复**：「已结束录音」
- 返回 `ok: false` → 只简短回复返回的 `message`（例如「没有进行中的会议」）

## 异常处理

- 连接失败/超时（ECHO 服务未启动）→ 回复：「ECHO 服务未运行」。
- 每次只调用一次对应接口，不要重复调用或轮询；如需确认真实状态，可查 `GET http://127.0.0.1:$port/api/meeting/status` 的 `active` 字段。
