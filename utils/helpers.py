"""工具函数"""

import re


def clean_ansi(text: str) -> str:
    """去除 ANSI 转义序列

    Args:
        text: 包含 ANSI 转义序列的文本

    Returns:
        清理后的文本
    """
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


def truncate_text(text: str, max_length: int = 3000, suffix: str = "...") -> str:
    """截断文本

    Args:
        text: 原始文本
        max_length: 最大长度
        suffix: 截断后的后缀

    Returns:
        截断后的文本
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix
