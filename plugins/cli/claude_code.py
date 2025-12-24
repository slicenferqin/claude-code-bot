"""Claude Code CLI 插件"""

import json
import os
import re
import subprocess
import threading
from typing import List, Optional, Callable, Dict, Any

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

        # 事件回调
        self._on_progress: Optional[Callable[[Dict[str, Any]], None]] = None
        self._on_complete: Optional[Callable[[Dict[str, Any]], None]] = None
        self._on_permission: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None

    def set_callbacks(
        self,
        on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_permission: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        """设置事件回调

        Args:
            on_progress: 进度更新回调
            on_complete: 任务完成回调
            on_permission: 权限请求回调（返回决策）
        """
        self._on_progress = on_progress
        self._on_complete = on_complete
        self._on_permission = on_permission

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

        使用 --output-format stream-json 获取实时进度。
        先尝试 --resume 恢复会话，如果失败则用 --session-id 创建新会话。

        Args:
            prompt: 用户输入的提示词
            session_id: 会话 ID
            workspace: 工作目录

        Returns:
            AsyncExecutionHandle: 异步执行句柄
        """
        # 设置环境变量
        env = os.environ.copy()
        env["CLAUDE_SESSION_ID"] = session_id
        if self._project_dir:
            env["CLAUDE_CODE_BOT_DIR"] = self._project_dir

        # 先尝试 resume
        cmd = self._build_command(prompt, session_id, use_resume=True, use_stream_json=True)

        process = subprocess.Popen(
            cmd,
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        # 启动后台线程读取和解析 JSON 流
        reader_thread = threading.Thread(
            target=self._read_stream_json,
            args=(process, session_id, workspace, env, prompt),
            daemon=True
        )
        reader_thread.start()

        return AsyncExecutionHandle(
            process=process,
            session_id=session_id,
            workspace=workspace,
        )

    def _read_stream_json(
        self,
        process: subprocess.Popen,
        session_id: str,
        workspace: str = ".",
        env: dict = None,
        prompt: str = "",
    ) -> None:
        """读取并解析 stream-json 输出

        如果会话不存在，自动用 --session-id 重试创建新会话。

        Args:
            process: 子进程
            session_id: 会话 ID
            workspace: 工作目录
            env: 环境变量
            prompt: 原始提示词（用于重试）
        """
        print(f"[ClaudeCode] Starting stream reader for session {session_id[:8]}...")
        has_output = False

        try:
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue

                has_output = True
                print(f"[ClaudeCode] Received line: {line[:100]}...")
                try:
                    data = json.loads(line)
                    self._handle_stream_event(data, session_id)
                except json.JSONDecodeError:
                    # 非 JSON 行，忽略
                    print(f"[ClaudeCode] Non-JSON line: {line[:50]}")
                    continue

        except Exception as e:
            print(f"[ClaudeCode] Stream reader error: {e}")

        finally:
            print(f"[ClaudeCode] Stream reader finished, has_output={has_output}")
            # 确保读取完 stderr
            stderr = process.stderr.read() if process.stderr else ""
            if stderr:
                print(f"[ClaudeCode] stderr: {stderr}")

            # 检查是否需要重试（会话不存在）
            if "No conversation found with session ID" in stderr and prompt:
                print(f"[ClaudeCode] Session not found, creating new session...")
                self._retry_with_new_session(prompt, session_id, workspace, env)

    def _retry_with_new_session(
        self,
        prompt: str,
        session_id: str,
        workspace: str,
        env: dict,
    ) -> None:
        """用 --session-id 创建新会话重试"""
        # 用 --session-id 创建新会话
        cmd = self._build_command(prompt, session_id, use_resume=False, use_stream_json=True)

        process = subprocess.Popen(
            cmd,
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

        # 递归调用，但不再传递 prompt 以防止无限循环
        self._read_stream_json(process, session_id, workspace, env, prompt="")

    def _handle_stream_event(self, data: Dict[str, Any], session_id: str) -> None:
        """处理单个 stream 事件

        Args:
            data: JSON 事件数据
            session_id: 会话 ID
        """
        event_type = data.get("type", "")

        if event_type == "assistant":
            # 助手消息（包含工具使用等）
            message = data.get("message", {})
            content = message.get("content", [])

            for item in content:
                if item.get("type") == "tool_use":
                    # 工具开始使用
                    tool_name = item.get("name", "unknown")
                    if self._on_progress:
                        self._on_progress({
                            "session_id": session_id,
                            "tool_name": tool_name,
                            "status": "running",
                            "output_preview": "",
                        })

        elif event_type == "tool_result":
            # 工具执行结果
            tool_name = data.get("tool", "")
            is_error = data.get("is_error", False)
            content = data.get("content", "")

            if self._on_progress:
                status = "failed" if is_error else "success"
                preview = content[:200] if isinstance(content, str) else str(content)[:200]
                self._on_progress({
                    "session_id": session_id,
                    "tool_name": tool_name,
                    "status": status,
                    "output_preview": preview,
                })

        elif event_type == "assistant":
            # 助手文本响应
            message = data.get("message", {})
            content = message.get("content", [])

            for item in content:
                if item.get("type") == "text":
                    # 提取助手的文本回复
                    text = item.get("text", "")
                    if text and self._on_complete:
                        self._on_complete({
                            "session_id": session_id,
                            "status": "completed",
                            "summary": text,  # 发送完整回复
                        })

        elif event_type == "result":
            # 最终结果（如果之前没有发送过）
            is_error = data.get("is_error", False)
            result = data.get("result", "")

            # result 类型通常包含最终回复，发送它
            if result and self._on_complete:
                self._on_complete({
                    "session_id": session_id,
                    "status": "failed" if is_error else "completed",
                    "summary": result,  # 发送完整回复
                })

    def is_available(self) -> bool:
        """检查 Claude Code CLI 是否可用"""
        # 检查命令是否存在
        if not os.path.exists(self.path):
            print(f"[ClaudeCode] 找不到 claude 命令: {self.path}")
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
        use_resume: bool = True,
        use_stream_json: bool = False,
    ) -> List[str]:
        """构建命令行参数

        Args:
            prompt: 提示词
            session_id: 会话 ID
            use_resume: 是否使用 --resume（否则用 --session-id）
            use_stream_json: 是否使用 stream-json 输出格式

        Returns:
            命令行参数列表
        """
        cmd = [
            self.path,
            "--print",
            prompt,
            *self.default_args,
        ]

        # 使用 stream-json 格式获取实时输出
        if use_stream_json:
            cmd.extend(["--output-format", "stream-json", "--verbose"])

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
