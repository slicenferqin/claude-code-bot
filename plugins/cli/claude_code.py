"""Claude Code CLI 插件"""

import json
import os
import re
import subprocess
from typing import List, Optional

from core.registry import PluginRegistry
from interfaces.cli import CLITool, ExecutionResult, ExecutionStatus, AsyncExecutionHandle


@PluginRegistry.register_cli("claude_code")
class ClaudeCodeCLI(CLITool):
    """Claude Code CLI 实现

    支持同步和异步两种执行模式：
    - 同步模式：适用于快速任务，阻塞等待结果
    - 异步模式：适用于长任务，配合 Hook 获取进度和结果
    """

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
        self.default_args = default_args or []
        self._project_dir: Optional[str] = None

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
        """同步执行 Claude Code 命令"""
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

    def execute_async(
        self,
        prompt: str,
        session_id: str,
        workspace: str = ".",
    ) -> AsyncExecutionHandle:
        """异步执行 Claude Code 命令（不等待完成）

        使用 Popen 启动进程，通过 Hook 机制获取进度和结果。

        Args:
            prompt: 用户输入的提示词
            session_id: 会话 ID
            workspace: 工作目录

        Returns:
            AsyncExecutionHandle: 异步执行句柄
        """
        # 构建命令
        cmd = self._build_command(prompt, session_id, use_resume=False)

        # 设置环境变量，传递 session_id 给 Hook 脚本
        env = os.environ.copy()
        env["CLAUDE_SESSION_ID"] = session_id
        if self._project_dir:
            env["CLAUDE_CODE_BOT_DIR"] = self._project_dir

        # 使用 Popen 启动，不阻塞
        process = subprocess.Popen(
            cmd,
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        return AsyncExecutionHandle(
            process=process,
            session_id=session_id,
            workspace=workspace,
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

    def setup_hooks(self, project_dir: str) -> bool:
        """配置 Claude Code hooks

        在用户目录创建 ~/.claude/settings.json 配置 Hook。

        Args:
            project_dir: 项目目录（claude-code-bot 的位置）

        Returns:
            是否成功配置
        """
        self._project_dir = project_dir
        hooks_dir = os.path.join(project_dir, "hooks")

        # Hook 配置
        hooks_config = {
            "hooks": {
                "Stop": [
                    {
                        "matcher": {},
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"python3 {hooks_dir}/on_stop.py"
                            }
                        ]
                    }
                ],
                "PostToolUse": [
                    {
                        "matcher": {},
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"python3 {hooks_dir}/on_tool_complete.py"
                            }
                        ]
                    }
                ]
            }
        }

        # 如果不使用 --dangerously-skip-permissions，添加 PermissionRequest Hook
        if "--dangerously-skip-permissions" not in self.default_args:
            hooks_config["hooks"]["PermissionRequest"] = [
                {
                    "matcher": {},
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"python3 {hooks_dir}/on_permission.py"
                        }
                    ]
                }
            ]

        try:
            # 创建全局配置目录
            config_dir = os.path.expanduser("~/.claude")
            os.makedirs(config_dir, exist_ok=True)

            # 读取现有配置
            settings_path = os.path.join(config_dir, "settings.json")
            existing_config = {}
            if os.path.exists(settings_path):
                try:
                    with open(settings_path, "r") as f:
                        existing_config = json.load(f)
                except (json.JSONDecodeError, IOError):
                    pass

            # 合并 hooks 配置
            if "hooks" not in existing_config:
                existing_config["hooks"] = {}

            existing_config["hooks"].update(hooks_config["hooks"])

            # 写入配置
            with open(settings_path, "w") as f:
                json.dump(existing_config, f, indent=2)

            print(f"[ClaudeCode] Hooks configured at {settings_path}")
            return True

        except Exception as e:
            print(f"[ClaudeCode] Failed to setup hooks: {e}")
            return False

    def _build_command(
        self,
        prompt: str,
        session_id: str,
        use_resume: bool = True
    ) -> List[str]:
        """构建命令行参数

        Args:
            prompt: 提示词
            session_id: 会话 ID
            use_resume: 是否使用 --resume（否则用 --session-id）

        Returns:
            命令行参数列表
        """
        cmd = [
            self.path,
            "--print",
            prompt,
            *self.default_args,
        ]

        if use_resume:
            cmd.extend(["--resume", session_id])
        else:
            cmd.extend(["--session-id", session_id])

        return cmd

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
        cmd_resume = self._build_command(prompt, session_id, use_resume=True)

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
            cmd_create = self._build_command(prompt, session_id, use_resume=False)

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
