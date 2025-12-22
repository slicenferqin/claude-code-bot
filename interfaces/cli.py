"""CLI 工具抽象接口"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ExecutionStatus(Enum):
    """执行状态"""

    SUCCESS = "success"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass
class ExecutionResult:
    """执行结果"""

    status: ExecutionStatus
    output: str
    error: Optional[str] = None


class CLITool(ABC):
    """CLI 工具抽象接口

    所有 CLI 工具插件都需要实现此接口。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称"""
        pass

    @abstractmethod
    def execute(
        self,
        prompt: str,
        session_id: str,
        workspace: str = ".",
        timeout: int = 180,
    ) -> ExecutionResult:
        """执行命令并返回结果

        Args:
            prompt: 用户输入的提示词
            session_id: 会话 ID，用于维持上下文
            workspace: 工作目录
            timeout: 超时时间（秒）

        Returns:
            ExecutionResult: 执行结果
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """检查工具是否可用

        Returns:
            工具是否可用
        """
        pass
