# -*- coding: utf-8 -*-
"""文件路径边界检查工具。"""
import os


def safe_under(base: str, *parts: str, allow_base: bool = False) -> str | None:
    """安全地把 ``parts`` 拼到 ``base`` 下，越界时返回 ``None``。

    使用 ``realpath`` 解析 ``..`` 和符号链接，再用 ``commonpath`` 判断目录
    边界，避免简单字符串前缀把 ``/web-backup`` 误认为在 ``/web`` 内。
    """
    base_real = os.path.realpath(base)
    target = os.path.realpath(os.path.join(base_real, *parts))
    try:
        inside = os.path.commonpath((base_real, target)) == base_real
    except ValueError:  # Windows 不同盘符没有共同路径
        return None
    if not inside or (target == base_real and not allow_base):
        return None
    return target
