"""CLI 工具抽象接口"""

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ExecutionStatus(Enum):
    """执行状态"""

    SUCCESS = "success"
    TIMEOUT = "timeout"
    ERROR = "error"
    RUNNING = "running"
    CANCELLED = "cancelled"


@dataclass
class ExecutionResult:
    """执行结果"""

    status: ExecutionStatus
    output: str
    error: Optional[str] = None


@dataclass
class AsyncExecutionHandle:
    """异步执行句柄

    用于跟踪和管理异步执行的任务。
    """
    process: subprocess.Popen
    session_id: str
    workspace: str

    def is_running(self) -> bool:
        """检查进程是否仍在运行"""
        return self.process.poll() is None

    def get_return_code(self) -> Optional[int]:
        """获取返回码（进程结束后）"""
        return self.process.poll()

    def terminate(self) -> None:
        """优雅终止进程"""
        if self.is_running():
            self.process.terminate()

    def kill(self) -> None:
        """强制杀死进程"""
        if self.is_running():
            self.process.kill()


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
        """同步执行命令并返回结果

        Args:
            prompt: 用户输入的提示词
            session_id: 会话 ID，用于维持上下文
            workspace: 工作目录
            timeout: 超时时间（秒）

        Returns:
            ExecutionResult: 执行结果
        """
        pass

    def execute_async(
        self,
        prompt: str,
        session_id: str,
        workspace: str = ".",
    ) -> AsyncExecutionHandle:
        """异步执行命令（不等待完成）

        Args:
            prompt: 用户输入的提示词
            session_id: 会话 ID，用于维持上下文
            workspace: 工作目录

        Returns:
            AsyncExecutionHandle: 异步执行句柄

        Raises:
            NotImplementedError: 如果子类未实现此方法
        """
        raise NotImplementedError("Async execution not implemented")

    @abstractmethod
    def is_available(self) -> bool:
        """检查工具是否可用

        Returns:
            工具是否可用
        """
        pass

    def setup_hooks(self, project_dir: str) -> bool:
        """配置工具的 Hook（可选）

        Args:
            project_dir: 项目目录

        Returns:
            是否成功配置
        """
        return True
