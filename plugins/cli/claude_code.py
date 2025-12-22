"""Claude Code CLI 插件"""

import os
import re
import subprocess
from typing import List, Optional

from core.registry import PluginRegistry
from interfaces.cli import CLITool, ExecutionResult, ExecutionStatus


@PluginRegistry.register_cli("claude_code")
class ClaudeCodeCLI(CLITool):
    """Claude Code CLI 实现"""

    def __init__(
        self,
        path: str = "/opt/homebrew/bin/claude",
        default_args: Optional[List[str]] = None,
    ):
        """初始化 Claude Code CLI

        Args:
            path: claude 命令路径
            default_args: 默认命令行参数
        """
        self.path = path
        self.default_args = default_args or ["--dangerously-skip-permissions"]

    @property
    def name(self) -> str:
        return "claude_code"

    def execute(
        self,
        prompt: str,
        session_id: str,
        workspace: str = ".",
        timeout: int = 180,
    ) -> ExecutionResult:
        """执行 Claude Code 命令"""
        try:
            result = self._run_with_session(prompt, session_id, workspace, timeout)

            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()

            if result.returncode != 0:
                error_msg = stderr if stderr else f"exit code {result.returncode}"
                return ExecutionResult(
                    status=ExecutionStatus.ERROR,
                    output="",
                    error=error_msg,
                )

            # 清理 ANSI 转义序列
            stdout = self._clean_ansi(stdout)

            if not stdout:
                stdout = "已执行，但无输出"

            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                output=stdout,
            )

        except subprocess.TimeoutExpired:
            return ExecutionResult(
                status=ExecutionStatus.TIMEOUT,
                output="",
                error="执行超时",
            )
        except FileNotFoundError:
            return ExecutionResult(
                status=ExecutionStatus.ERROR,
                output="",
                error=f"找不到 claude 命令: {self.path}",
            )
        except Exception as e:
            return ExecutionResult(
                status=ExecutionStatus.ERROR,
                output="",
                error=str(e),
            )

    def is_available(self) -> bool:
        """检查 Claude Code CLI 是否可用"""
        # 检查命令是否存在
        if not os.path.exists(self.path):
            return False

        # 检查 API Key
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("[ClaudeCode] ANTHROPIC_API_KEY 未设置")
            return False

        return True

    def _run_with_session(
        self,
        prompt: str,
        session_id: str,
        workspace: str,
        timeout: int,
    ) -> subprocess.CompletedProcess:
        """使用 session 执行命令

        先尝试 --resume，如果会话不存在则使用 --session-id 创建
        """
        # 先尝试恢复会话
        cmd_resume = [
            self.path,
            "--print",
            prompt,
            *self.default_args,
            "--resume",
            session_id,
        ]

        result = subprocess.run(
            cmd_resume,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        combined = f"{stdout}\n{stderr}".strip()

        # 如果会话不存在，创建新会话
        if "No conversation found with session ID" in combined:
            cmd_create = [
                self.path,
                "--print",
                prompt,
                *self.default_args,
                "--session-id",
                session_id,
            ]

            result = subprocess.run(
                cmd_create,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            # 如果 session_id 已被使用，返回原始结果
            if "is already in use" in (result.stderr or ""):
                return result

        return result

    def _clean_ansi(self, text: str) -> str:
        """去除 ANSI 转义序列"""
        ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
        return ansi_escape.sub("", text)
