# -*- coding: utf-8 -*-
"""dsh.py — DSH 客户端兼容层（实现已迁至 app/agents/dsh_agent.py）

历史：本模块原先是 ECHO 唯一的执行底层接入点。为了支持"可切换智能体产品"，
DSH 的实现被迁到 `app/agents/dsh_agent.py` 并实现 `AgentAdapter` 接口；
本模块保留原有导出名，避免既有调用点失效：

    DshClient / DshError / get_client() / ECHO_WORKSPACE

注意 get_client() 现在**始终返回 DSH 适配器**（不随 agentBackend 配置变化）——
它服务于"DSH 进程/目标列表"这类 DSH 专属场景（app/manager.py、/api/dsh/targets）。
需要"当前选中的智能体"请用 `app.agents.active_agent()`。
"""
from app.agents.base import ECHO_WORKSPACE                     # noqa: F401
from app.agents.dsh_agent import (                              # noqa: F401
    DshAgent,
    DshError,
    build,
    _load_browser_secret,
    _make_cookie,
    DEFAULT_BASE_URL,
    CREDENTIALS_PATH,
)

# 兼容旧名：DshClient 现在就是 DshAgent
DshClient = DshAgent


def get_client():
    """取 DSH 适配器实例（单例，由注册表缓存）。"""
    from app.agents import get_agent
    return get_agent("dsh")
