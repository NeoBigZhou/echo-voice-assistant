# dsh-failover — 大模型"内网优先、公网兜底"代理（可选）

一个极小的本地反向代理：把 OpenAI 兼容请求**先打内网网关**，内网不通（连接失败 / 超时 / 5xx）
时自动回退到公网 API。给 DSH Desktop（或任何 OpenAI 兼容客户端）当 `baseURL` 用，
好处是内网自建模型跑得动就用内网的，跑不动也不至于整个卡死。

```
DSH Desktop ──▶ http://127.0.0.1:8899/chat/completions ──▶ ① 内网网关（优先）
                                                         └▶ ② 公网 API（回退）
```

- 状态页：`http://127.0.0.1:8899/dashboard`（最近请求走了哪条路、计数、耗时）
- 健康检查：`GET /health`（ECHO 面板上的「模型容灾路由」卡片就是读它）
- 路由记录写在 `dsh-failover/logs/`，请求体只转发不落盘

## 配置

把 `config.example.json` 复制成 `config.json`（**该文件已在 `.gitignore` 里，不会进仓库**）再改：

```json
{
  "host": "127.0.0.1",
  "port": 8899,
  "internal_url": "https://your-intranet-gateway.example.com/v1/chat/completions",
  "internal_model": "your-internal-model-name",
  "internal_user_id": "your-user-id",
  "public_url": "https://api.deepseek.com/chat/completions",
  "public_model": "deepseek-chat",
  "connect_timeout": 6.0,
  "first_byte_timeout": 30.0,
  "read_timeout": 360.0,
  "write_timeout": 120.0,
  "fallback_on_5xx": false
}
```

密钥**不写在配置文件里**，走环境变量（或 `~/.dsh/.credentials.yaml`）：

| 环境变量 | 用途 |
|---|---|
| `FAILOVER_INTERNAL_TOKEN` | 内网网关的 token（也认 `INTERNAL_LLM_TOKEN`） |
| `FAILOVER_DEEPSEEK_KEY` | 公网 API 的 key |

没有内网网关？把 `internal_url` 指向任意 OpenAI 兼容端点即可；或者干脆不用这个代理
（让 DSH 直连公网 API，只是少了"内网优先"这一层，ECHO 面板上的容灾卡片会显示"代理未运行"）。

## 跑起来

```powershell
.\venv\Scripts\python.exe dsh-failover\proxy.py --port 8899 --config dsh-failover\config.json
powershell -File dsh-failover\start.ps1     # 或者后台启动
```

然后把 DSH 的 provider `baseURL` 指到 `http://127.0.0.1:8899`。ECHO 侧不用配：
面板 → 仪表盘 →「模型容灾路由」卡片读 `/health`，显示当前走内网还是公网。
