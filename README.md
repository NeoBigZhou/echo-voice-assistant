# ECHO — Windows 语音助手 + 会议纪要

Windows 上的一体化个人语音助手：**全局热键/唤醒词 → 本地转写 → 交给大模型执行 → 语音播报结论**，
外加**会议录音 → 转写 → 说话人分离 → 自动生成纪要**，以及一个原生 SPA 控制面板。

LLM 执行层由 [DeepSeek Harness Desktop](https://github.com/NeoBigZhou)（DSH Desktop）本地 API 承担。
ECHO 自己**不做推理**，只负责录音、转写、编排、面板与播报——**所以"本地"指的是 ECHO 这一侧**：
转写与语音识别全在本机，但**纪要与指令执行的文本会离开这台机器**（发给 DSH 配置的模型服务），
**语音合成默认用的是微软在线服务**。逐项见下面「本地 / 联网」一节。

```
热键/唤醒词 → 录音 → 本地转写(SenseVoice / Whisper / Qwen3-ASR / sherpa)
   → DSH 会话执行(可调用技能) → 极简结论语音播报 + 历史入库
会议录音 → 分段存档 → 转写 → 说话人分离(可选) → DSH 生成纪要 → SQLite 管理
控制面板 → 组件状态与启停 / 设置 / 历史 / 会议管理 / 模型清单与下载
```

## 本地 / 联网（重要：哪些数据会出网）

| 功能 | 在哪里执行 | 数据去向 |
|---|---|---|
| 转写（命令/会议）、唤醒词、说话人分离 | **本机**（SenseVoice / Whisper / Qwen3-ASR / sherpa-onnx / KWS / pyannote） | 不出网 |
| 录音与转写文件 | **本机** `data/meetings/`（SQLite + wav + md） | 不出网 |
| **会议纪要 / 议题分段** | 本机记录，**推理在 DSH 配置的模型服务** | **转写全文会发给该模型服务**：内网网关就是贵单位内网，公网 API 就是模型厂商（如 DeepSeek 官方） |
| **指令执行** | 同上（DSH 会话 + 技能） | **你的指令文本与相关上下文会发给该模型服务** |
| **语音播报（TTS）** | 默认 `ttsEngine=auto` → 先试 **edge-tts（微软在线，需访问 `speech.platform.bing.com`）**，失败才降级 Windows SAPI | **要念出来的文本会发给微软**；想全离线就在设置里把引擎固定为 `sapi`（音色差一些） |
| 纪要归档到你自己的技能 | 本机（DSH 会话 + 你的技能） | 取决于你的技能实现（例如写本地笔记库＝不出网） |
| 模型下载 | 本机（ModelScope / hf-mirror 镜像） | 只下载权重，不上传任何数据 |

一句话：**"ECHO 本地"≠"数据不出网"**。要求全程不出网时，请把 `ttsEngine` 设为 `sapi`，
并把 DSH 指向本机/内网的模型服务（或用你信得过、可接受数据外发的服务商）。

## 特性

- **转写与语音识别全本地**：命令与会议转写、唤醒词、说话人分离都在本机跑，音频文件不出网
  （需要出网的是"交给大模型"和"默认的在线语音合成"，见上表）。
- **指令只要一句结论**：提示词要求模型先给极简结论再给详情，语音只念结论，详情留在会话里。
- **会议纪要**：分段录音、按需/常驻双转写引擎、说话人分离（pyannote）、纪要归档可委派给你自己的技能。
- **右缘边条**：`Ctrl+Shift+E` 唤出/收起，折叠态是一条 64px 功能条（录音、电平、说话、状态灯）。
- **模型面板**：设置页列出每个功能需要的模型、体积、落地路径与就绪状态，能从 ModelScope / HF 镜像一键下载。
- **可被其他应用调用**：本地 REST API（转写、TTS、会议、设置），面板与手机 App 共用同一入口。
- **指令只要一句结论**：提示词要求模型先给极简结论再给详情，语音只念结论，详情留在会话里。
- **会议纪要**：分段录音、按需/常驻双转写引擎、说话人分离（pyannote）、纪要归档可委派给你自己的技能。
- **右缘边条**：`Ctrl+Shift+E` 唤出/收起，折叠态是一条 64px 功能条（录音、电平、说话、状态灯）。
- **模型面板**：设置页列出每个功能需要的模型、体积、落地路径与就绪状态，能从 ModelScope / HF 镜像一键下载。
- **可被其他应用调用**：本地 REST API（转写、TTS、会议、设置），面板与手机 App 共用同一入口。

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.11 + FastAPI + uvicorn（单进程，默认端口 8970） |
| 数据库 | SQLite（`data/echo.db`，WAL，版本化迁移） |
| 转写 | faster-whisper / funasr SenseVoice / Qwen3-ASR / sherpa-onnx（本地模型，自动选 GPU） |
| 唤醒 | sherpa-onnx KWS（离线关键词，可自定义） |
| 热键 | ctypes 全局组合键 + 媒体键低级钩子（无 C# 编译依赖） |
| 合成 | edge-tts（在线，自然）→ Windows SAPI（离线兜底） |
| 面板 | 原生 HTML/CSS/JS SPA（无构建链，响应式，可直接在手机浏览器打开） |
| 边条 | .NET 7 WinForms + WebView2（`sidebar/`） |
| 执行层 | DeepSeek Harness Desktop 2.x 本地 API（默认 `http://127.0.0.1:43120`） |

## 快速开始

```powershell
git clone https://github.com/NeoBigZhou/echo-voice-assistant.git
cd echo-voice-assistant

python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
# 有 NVIDIA 显卡时（可选，转写提速明显）：
#   .\venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu128

powershell -File scripts\setup.ps1              # 校验 venv/依赖/模型，并建库
powershell -File scripts\start.ps1 -Background  # 后台启动
# 打开面板：http://127.0.0.1:8970
```

首次使用建议：
1. 面板 → **设置 → 模型**：点一下把 `SenseVoice`（默认转写引擎，约 896MB）下载好；
2. 面板 → **设置 → 语音**：确认热键（默认 `Ctrl+Alt+C` 说话、`Ctrl+Shift+E` 面板）；
3. 面板 → **启动**：查看各组件状态，缺什么点什么（DSH 执行引擎需要另行安装 DSH Desktop）。

更细的安装、显卡、非 ASCII 路径、开机自启、边条编译、插件部署见 **[docs/DEPLOY.md](docs/DEPLOY.md)**。

## 模型从哪来

仓库**不含**模型权重（体积大、且部分上游有授权限制）。三条路：

| 方式 | 适用 |
|---|---|
| 面板 → 设置 → 模型 → **下载** | SenseVoice、Whisper 各档、Qwen3-ASR、sherpa 流式（走 ModelScope / hf-mirror 镜像） |
| 首次使用时自动下载 | SenseVoice 走 ModelScope 缓存；Whisper 走 HF 镜像缓存 |
| 从别处拷贝 | 说话人分离（pyannote，HF 上是 gated 模型）与 KWS 唤醒词模型：按模型面板里给的**落地路径**放对目录即可 |

面板会显示每一项的体积、目标路径与当前是否就绪，落地路径是**代码约定**（改名会加载不到）。

## 配置

设置全部入库（`data/echo.db`），面板里改完即生效，分组为：通用 / 语音与命令 / 唤醒词 / 会议 / 纪要归档 / 面板。
常用项：

| 键 | 含义 |
|---|---|
| `sttModel` | 命令转写引擎：`sensevoice`（默认）/ `qwen3asr` / `sherpa` / `tiny`…`large`（whisper 档） |
| `meetingSttModel` | 会议转写引擎（可与命令不同） |
| `wakeHotkey` / `fallbackHotkey` / `panelHotkey` | 说话 / 备用 / 面板热键 |
| `panelOpenMode` | `sidebar`（右缘边条）/ `app` / `browser` |
| `ttsEngine` | `auto` / `edge-tts` / `sapi` / `off` |
| `minimalReply*` | 「先结论、后详情」的提示词与字数上限 |
| `worklogEnabled` / `worklogVaultRoot` / `worklogMode` | 纪要归档：把归档委派给你自己的技能（见 [docs/worklog.md](docs/worklog.md)） |
| `apiAuthEnabled` | 开启后除 `/api/status` 外都需要 `Authorization: Bearer <token>` |

## 常用 API

| 端点 | 说明 |
|---|---|
| `GET /api/status` | 组件状态 + DSH + 会议 + 忙闲 + 转写引擎加载状态 |
| `GET/PUT /api/settings` | 配置读写（带分组/类型元数据） |
| `POST /api/assistant/command` | 发送文本命令 `{text, source}` |
| `POST /api/assistant/capture` | 触发一次录音命令流 |
| `POST /api/models/download` | 下载指定模型（`GET /api/models` 看清单与进度） |
| `POST /api/system/restart` | 重启 ECHO 服务 |
| `POST /api/boot/component/{id}/start\|stop` | 组件启停（dsh / stt-cmd / stt-meeting / tts / wake / hotkey） |
| `POST /api/meeting/start\|stop` | 会议录音开关 |
| `GET /api/meetings` · `GET /api/meetings/{id}` | 会议列表 / 详情 |
| `POST /api/meetings/{id}/summary/regenerate` | 重新生成纪要 |
| `POST /api/stt/transcribe` · `/api/stt/sentences` | 转写为文本 / 带时间戳句子（可被其他应用调用） |
| `GET /api/logs` · `GET /api/events` | 日志 / 事件流 |

转写 API 示例：

```bash
curl -X POST http://127.0.0.1:8970/api/stt/transcribe \
  -F "file=@录音.mp3" -F "engine=sensevoice" -F "lang=zh"
```

## 目录结构

```
echo-voice-assistant/
├── app/            Python 后端（db/config/dsh/assistant/meeting/boot/audio/modelinfo…）
├── web/            控制面板 SPA（index.html / app.js / app.css）+ 折叠条 rail.html
├── models/         本地模型（不入 git；用面板下载或自行拷贝）
├── data/           echo.db、录音、历史、日志（不入 git）
├── assets/         提示音等资源
├── sidebar/        .NET 7 右缘边条源码（dotnet build -c Release）
├── plugin/         DSH Desktop 宿主插件（可选：让 DSH 启动时守护 ECHO）
├── scripts/        安装 / 启停 / 自启 / 插件部署 / 重启等 PowerShell 脚本
├── docs/           部署指南、纪要归档说明、PowerShell 编码经验
└── .dsh/skills/    随仓库提供的 DSH 技能（meeting-record：用语音开关会议录音）
```

## 与 DSH Desktop 的关系

「执行指令 / 生成纪要」这一步需要 DSH Desktop（本地 API，默认 43120 端口，需在 DSH 里放开本机访问）。
ECHO 通过 JSON-RPC 风格接口与会话交互，把技能（skills）能力直接借过来——所以"整理成表格""查一下天气"
这类任务不需要在 ECHO 里再实现一遍。

插件 `plugin/echo-host/` 是可选的：它让 DSH 启动时顺带守护 ECHO，并在 DSH 升级后自动重装；
ECHO 也可以完全脱离 DSH 独立启动（转写、会议、面板都不依赖它），只把"执行"这一步留白。

## 安全

* API 与容灾代理**只监听 `127.0.0.1`**，并且装了来源守卫（`app/netguard.py`）：
  `Host` / `Origin` 非回环一律 403，跨站页面既读不到数据也发不出有效写入，
  **DNS Rebinding** 与 `Origin: null`（`file://`、sandbox iframe）同样被拒。
  这一层是必要的，因为本地 API 默认不带 token——没有它，你打开的任意网页都能
  `POST /api/meeting/start` 让 ECHO 用服务进程开麦录音再把音频下载走。
* 不要为了手机访问把服务绑到 `0.0.0.0`：保持回环绑定，前面套带认证的反向代理
  （Caddy/Nginx + Basic Auth + TLS），并开启 `apiAuthEnabled` + Bearer Token。详见 [docs/DEPLOY.md](docs/DEPLOY.md#7-安全本机-api-只允许本机访问)。
* API 密钥只以 `sha256(token)` 存库，校验用 `hmac.compare_digest`；`GET /api/keys` 不回 token
  （明文仅在创建时返回一次）。开启 `apiAuthEnabled` 前先建好密钥并存到客户端，否则面板自身会被 401。
* 数据库、录音、历史、日志都在 `data/`（不入 git）；真实凭据只放在环境变量或
  `~/.dsh/.credentials.yaml`，`dsh-failover/config.json` 已在 `.gitignore` 里。

## 许可与致谢

本项目以 **MIT** 许可发布，见 [LICENSE](LICENSE)。

它站在这些开源项目的肩上（各自遵循其原始许可）：

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) / [CTranslate2](https://github.com/OpenNMT/CTranslate2) — Whisper 推理
- [FunASR](https://github.com/modelscope/FunASR) 与 ModelScope 上的 `iic/SenseVoiceSmall` — 中文短语音转写
- [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) — 高精度转写与时间戳
- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)（Apache-2.0）— 流式转写与 KWS 唤醒
- [pyannote.audio](https://github.com/pyannote/pyannote-audio) — 说话人分离（模型需自行在 HF 上接受条款后获取）
- [WebView2](https://learn.microsoft.com/microsoft-edge/webview2/) + .NET 7 — 右缘边条
- [mermaid](https://github.com/mermaid-js/mermaid) — 纪要里的图表渲染（`web/vendor/`）
