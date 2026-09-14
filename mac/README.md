# ECHO macOS 模块

本目录是给 **macOS** 用的独立模块。原仓库是 Windows 版，这里**不改动任何原文件**，
通过启动入口把 Windows 专属部分替换成 Mac 实现。

## 原理（一句话）

`run_mac.py` 在加载主程序前，把 `app.hotkey`、`app.runtime` 换成 Mac 版，
并给语音合成打补丁。原代码一行没动。

## 能用的功能

| 功能 | Mac | 说明 |
|---|---|---|
| 打开控制面板 | ✅ | 浏览器打开 `http://127.0.0.1:8970` |
| 录音转文字 | ✅ | 默认 Whisper（可换 sherpa 等） |
| 会议录音 + 纪要 | ✅ | 需要接 DSH 才能自动生成纪要 |
| 语音合成（朗读） | ✅ | 在线 edge-tts；离线兜底用 macOS `say` |
| 桌面通知 | ✅ | 通知中心（`osascript`），首次会申请通知权限 |
| 提示音 | ✅ | 用 sounddevice 播放 |
| 重启服务 | ✅ | 面板里的「重启」按钮 |
| 全局热键 | ⚠️ 可选 | 需装 `pynput` 并在系统设置里授权 |
| 右缘边条 | ❌ | Windows 专属程序，Mac 上改用浏览器 |
| 语音唤醒 | ⚠️ 可选 | 需装 sherpa 唤醒模型 |

## 安装（只做一次）

```bash
cd 本地语音项目
mac/setup_mac.sh
```

脚本会自动：安装 Python 3.11 和 PortAudio（用 Homebrew）→ 建虚拟环境 → 装依赖。

> 前提：已安装 [Homebrew](https://brew.sh)。

## 启动 / 停止 / 重启

```bash
mac/start_mac.sh      # 启动
mac/stop_mac.sh       # 停止
mac/restart_mac.sh    # 重启
```

启动后打开：**http://127.0.0.1:8970**

首次使用建议：

1. 面板 → **设置 → 模型**：下载 Whisper（默认 `base`，约 145MB）。
2. 面板 → **启动**：看各组件状态。转写、会议、面板不依赖 DSH 就能用。
3. 想让它「听懂指令并执行 / 自动写纪要」，需要本机另外跑一个 DSH。

## 全局热键（可选）

默认关闭（未装 pynput 时热键组件显示“未安装”）。需要时：

```bash
venv/bin/pip install pynput
mac/restart_mac.sh
```

然后到 **系统设置 → 隐私与安全性 → 辅助功能 / 输入监控**，把运行它的程序
（终端 / Python）勾上授权。热键在 面板 → 设置 → 语音与命令 里配置。
Mac 上暂不支持耳机媒体键触发，请用组合键。
授权没给够时热键组件会显示失败原因，不会假装在线。

## 面板打开方式

Mac 上 `panelOpenMode` 固定为 `browser`（没有 Windows 右缘边条），
所以「启动时自动显示折叠条」（`panelAutoStart`）在 Mac 上不生效 ——
启动后不会自动弹面板，需要时按 `Ctrl+Shift+E` 或直接打开 `http://127.0.0.1:8970`。

## 可选组件（按需再装）

```bash
venv/bin/pip install funasr modelscope  # SenseVoice / Qwen3-ASR（中文短命令更快）
venv/bin/pip install pyannote.audio  # 会议说话人分离（会拉 torch，体积很大）
```

## 日志与排错

- 运行日志：`data/logs/echo-mac.out`
- 重启日志：`data/logs/restart-mac.out` / `.err`
- 端口被占：`mac/stop_mac.sh` 会清理 8970 上的残留进程

## 与 Windows 版的关系

Windows 版原样保留，不受影响。两边共用同一份 `app/` 代码，只有
`mac/` 这一个目录是 Mac 专用。在 Mac 上永远通过 `mac/run_mac.py` 启动；
直接跑 `python -m app.main` 会因 Windows 依赖而失败（这是预期的）。
