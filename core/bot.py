"""Bot 核心逻辑 V2 - 支持异步执行和 Hook 通信"""

import asyncio
import threading
import uuid
import os
from typing import List, Optional, Dict, Any
from datetime import datetime

from interfaces.im import IMPlatform, Message, Reply
from interfaces.cli import CLITool, ExecutionStatus
from core.session import SessionManager
from core.ipc_server import IPCServer
from core.task_manager import TaskManager, TaskStatus
from core.permission_manager import PermissionManager
from core.command_parser import CommandParser, CommandType, GitOperations


class Bot:
    """Bot 核心类 V2

    负责：
    - 管理 IM 平台和 CLI 工具
    - 消息路由和处理
    - 会话管理
    - IPC 通信（与 Hook 脚本交互）
    - 异步任务执行
    - 权限确认流程
    """

    def __init__(
        self,
        cli_tool: CLITool,
        trigger_keyword: str = "claude code",
        workspace: str = ".",
        default_timeout: int = 180,
        max_output_length: int = 3000,
        max_concurrent_tasks: int = 3,
        permission_timeout: float = 3600,
        auto_setup_hooks: bool = True,
    ):
        """初始化 Bot

        Args:
            cli_tool: CLI 工具实例
            trigger_keyword: 触发关键词
            workspace: 工作目录
            default_timeout: 默认超时时间（秒）
            max_output_length: 最大输出长度
            max_concurrent_tasks: 最大并发任务数
            permission_timeout: 权限确认超时时间（秒）
            auto_setup_hooks: 是否自动配置 Hook
        """
        self.cli_tool = cli_tool
        self.trigger_keyword = trigger_keyword.lower()
        self.workspace = os.path.abspath(workspace)
        self.default_timeout = default_timeout
        self.max_output_length = max_output_length
        self.auto_setup_hooks = auto_setup_hooks

        self._im_platforms: List[IMPlatform] = []
        self._session_manager = SessionManager()
        self._processed_messages = set()
        self._processed_lock = threading.Lock()

        # V2 新增组件
        self._ipc_server = IPCServer()
        self._task_manager = TaskManager(
            max_concurrent=max_concurrent_tasks,
            auto_rollback=True
        )
        self._permission_manager = PermissionManager(
            default_timeout=permission_timeout
        )
        self._command_parser = CommandParser()

        # 事件循环（用于异步操作）
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._async_thread: Optional[threading.Thread] = None

        # 注册 IPC 事件处理器
        self._register_ipc_handlers()

    def _register_ipc_handlers(self) -> None:
        """注册 IPC 消息处理器"""
        self._ipc_server.on("task_progress", self._on_task_progress)
        self._ipc_server.on("task_complete", self._on_task_complete)
        self._ipc_server.on("permission_request", self._on_permission_request)
        self._ipc_server.on("get_permission_response", self._on_get_permission_response)

    def add_im_platform(self, platform: IMPlatform) -> None:
        """添加 IM 平台"""
        self._im_platforms.append(platform)

    def start(self) -> None:
        """启动 Bot"""
        print(f"\n{'=' * 60}")
        print(f"Bot V2 启动中...")
        print(f"启动时间: {datetime.now()}")
        print(f"触发关键词: {self.trigger_keyword}")
        print(f"工作目录: {self.workspace}")
        print(f"CLI 工具: {self.cli_tool.name}")
        print(f"IM 平台: {[p.name for p in self._im_platforms]}")
        print(f"{'=' * 60}\n")

        # 检查 CLI 工具是否可用
        if not self.cli_tool.is_available():
            print(f"[Bot] CLI 工具 {self.cli_tool.name} 不可用")
            return

        # 配置回调（用于 stream-json 模式）
        if hasattr(self.cli_tool, 'set_callbacks'):
            self.cli_tool.set_callbacks(
                on_progress=self._on_task_progress,
                on_complete=self._on_task_complete,
            )

        # 配置 Hook（可选，作为备用）
        if self.auto_setup_hooks:
            project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if hasattr(self.cli_tool, 'setup_hooks'):
                self.cli_tool.setup_hooks(project_dir)

        # 启动异步事件循环
        self._start_async_loop()

        # 启动 IPC 服务
        asyncio.run_coroutine_threadsafe(
            self._ipc_server.start(),
            self._loop
        )

        # 启动所有 IM 平台
        for platform in self._im_platforms:
            print(f"[Bot] 启动 IM 平台: {platform.name}")
            platform.start(self._on_message)

    def stop(self) -> None:
        """停止 Bot"""
        # 停止 IPC 服务
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self._ipc_server.stop(),
                self._loop
            )

        # 停止 IM 平台
        for platform in self._im_platforms:
            platform.stop()

        # 停止事件循环
        self._stop_async_loop()

        print("[Bot] 已停止")

    def _start_async_loop(self) -> None:
        """启动异步事件循环（在单独线程）"""
        def run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()

        self._async_thread = threading.Thread(target=run_loop, daemon=True)
        self._async_thread.start()

        # 等待事件循环启动
        import time
        while self._loop is None:
            time.sleep(0.01)

    def _stop_async_loop(self) -> None:
        """停止异步事件循环"""
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _on_message(self, message: Message) -> None:
        """消息回调"""
        # 消息去重
        with self._processed_lock:
            if message.id in self._processed_messages:
                return
            self._processed_messages.add(message.id)

        content = message.content.strip()
        content_lower = content.lower()

        # 先尝试解析为命令
        cmd = self._command_parser.parse(content)

        # 检查是否有活动任务
        active_task = self._task_manager.get_task_by_chat(message.chat_id)

        # 如果是命令且有活动任务，处理命令
        if active_task and cmd.type != CommandType.MESSAGE:
            self._handle_command(message, cmd, active_task)
            return

        # 直接使用消息内容作为 prompt（不再需要触发关键词）
        prompt = content.strip()
        if not prompt:
            return

        print(f"\n[Bot] 收到命令: {prompt[:100]}")
        print(f"[Bot] 消息 ID: {message.id}")
        print(f"[Bot] 会话 ID: {message.chat_id}")

        # 启动任务
        self._start_task(message, prompt)

    def _handle_command(self, message: Message, cmd, task) -> None:
        """处理用户命令"""
        platform = self._find_platform_for_message(message)
        if not platform:
            return

        session_id = task.id

        # 权限确认响应 - 只有在确实有待确认的请求时才处理
        if self._command_parser.is_permission_response(cmd):
            pending = self._permission_manager.get_latest_pending(session_id)
            if pending:
                decision = "approve" if cmd.type == CommandType.APPROVE else "deny"
                self._permission_manager.respond(pending.request_id, decision)
                emoji = "✅" if decision == "approve" else "❌"
                platform.send(message.chat_id, Reply(content=f"{emoji} 已{decision}"))
                return
            # 没有待确认的请求，将消息转发给 Claude（不 return）
            self._start_task(message, cmd.raw)
            return

        # 取消任务
        if cmd.type == CommandType.CANCEL:
            result = self._task_manager.cancel_task(session_id)
            self._permission_manager.cancel_all_for_session(session_id)
            platform.send(message.chat_id, Reply(content=f"⏹️ {result['message']}"))
            return

        # 查看 diff
        if cmd.type == CommandType.DIFF:
            success, diff = GitOperations.get_diff(task.workspace, cmd.args or None)
            if success:
                if len(diff) > self.max_output_length:
                    diff = diff[:self.max_output_length] + "\n\n... (已截断)"
                platform.send(message.chat_id, Reply(content=f"📄 改动:\n\n```diff\n{diff}\n```"))
            else:
                platform.send(message.chat_id, Reply(content=f"❌ {diff}"))
            return

        # 提交代码
        if cmd.type == CommandType.COMMIT:
            commit_msg = cmd.args or "Update by Claude Code Bot"
            success, result = GitOperations.commit(task.workspace, commit_msg)
            if success:
                platform.send(message.chat_id, Reply(
                    content=f"✅ 已提交: {result}\n消息: {commit_msg}\n\n回复 \"push\" 推送到远程"
                ))
            else:
                platform.send(message.chat_id, Reply(content=f"❌ 提交失败: {result}"))
            return

        # 推送代码
        if cmd.type == CommandType.PUSH:
            success, result = GitOperations.push(task.workspace)
            emoji = "✅" if success else "❌"
            platform.send(message.chat_id, Reply(content=f"{emoji} {result}"))
            return

        # 回滚
        if cmd.type == CommandType.ROLLBACK:
            success, result = GitOperations.rollback(task.workspace)
            emoji = "✅" if success else "❌"
            platform.send(message.chat_id, Reply(content=f"{emoji} {result}"))
            return

        # 查看状态
        if cmd.type == CommandType.STATUS:
            status_info = self._format_task_status(task)
            platform.send(message.chat_id, Reply(content=status_info))
            return

        # 继续修改
        if cmd.type == CommandType.CONTINUE:
            if cmd.args:
                self._start_task(message, cmd.args)
            else:
                platform.send(message.chat_id, Reply(content="请输入继续修改的指令"))
            return

    def _start_task(self, message: Message, prompt: str) -> None:
        """启动新任务"""
        platform = self._find_platform_for_message(message)
        if not platform:
            return

        # 立即回复"思考中"（满足飞书3秒响应要求）
        platform.send(message.chat_id, Reply(content="🤔 思考中..."))

        # 生成会话 ID
        session_id = self._session_manager.get_or_create_session_id(message.chat_id)

        # 创建任务
        task = self._task_manager.create_task(
            session_id=session_id,
            chat_id=message.chat_id,
            user_id=message.sender_id,
            prompt=prompt,
            workspace=self.workspace,
        )

        if not task:
            platform.send(message.chat_id, Reply(
                content="⚠️ 任务队列已满，请稍后再试"
            ))
            return

        # 异步执行
        try:
            handle = self.cli_tool.execute_async(
                prompt=prompt,
                session_id=session_id,
                workspace=self.workspace,
            )
            self._task_manager.start_task(session_id, handle.process)
        except Exception as e:
            self._task_manager.update_task_status(session_id, TaskStatus.FAILED, str(e))
            platform.send(message.chat_id, Reply(content=f"❌ 启动失败: {e}"))

    def _format_task_status(self, task) -> str:
        """格式化任务状态信息"""
        status_emoji = {
            TaskStatus.PENDING: "⏳",
            TaskStatus.RUNNING: "🔄",
            TaskStatus.WAITING_CONFIRM: "⚠️",
            TaskStatus.COMPLETED: "✅",
            TaskStatus.FAILED: "❌",
            TaskStatus.CANCELLED: "⏹️",
            TaskStatus.CANCELLING: "⏹️",
        }
        emoji = status_emoji.get(task.status, "❓")

        lines = [
            f"{emoji} 任务状态: {task.status.value}",
            f"📝 任务: {task.prompt[:50]}...",
            f"🕐 创建: {task.created_at.strftime('%H:%M:%S')}",
        ]

        if task.status == TaskStatus.COMPLETED:
            lines.append(f"\n可用命令: diff, commit, push, rollback, continue")

        return "\n".join(lines)

    # ============ IPC 事件处理器 ============

    def _on_task_progress(self, payload: Dict[str, Any]) -> None:
        """处理任务进度更新"""
        session_id = payload.get("session_id", "")
        tool_name = payload.get("tool_name", "")
        status = payload.get("status", "")
        output_preview = payload.get("output_preview", "")

        task = self._task_manager.get_task(session_id)
        if not task:
            return

        platform = self._get_platform_for_chat(task.chat_id)
        if not platform:
            return

        # 发送进度消息（简化，避免刷屏）
        msg = f"📍 {tool_name}: {status}"
        if output_preview and len(output_preview) < 100:
            msg += f"\n{output_preview}"

        platform.send(task.chat_id, Reply(content=msg))

    def _on_task_complete(self, payload: Dict[str, Any]) -> None:
        """处理任务完成 - 发送 Claude 的回复内容"""
        session_id = payload.get("session_id", "")
        summary = payload.get("summary", "")
        status = payload.get("status", "completed")

        task = self._task_manager.get_task(session_id)
        if not task:
            return

        platform = self._get_platform_for_chat(task.chat_id)
        if not platform:
            return

        # 直接发送 Claude 的回复内容
        if summary:
            # 截断过长的内容
            if len(summary) > self.max_output_length:
                summary = summary[:self.max_output_length] + "\n\n... (内容已截断)"

            emoji = "✅" if status == "completed" else "❌"
            platform.send(task.chat_id, Reply(content=f"{emoji} Claude:\n\n{summary}"))

        # 更新任务状态
        self._task_manager.complete_task(session_id, summary, [])

    def _on_permission_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """处理权限确认请求"""
        request_id = payload.get("request_id", "")
        session_id = payload.get("session_id", "")
        tool_name = payload.get("tool_name", "")
        command = payload.get("command", "")

        task = self._task_manager.get_task(session_id)
        if not task:
            return {"decision": "deny", "reason": "Task not found"}

        # 更新任务状态
        self._task_manager.update_task_status(session_id, TaskStatus.WAITING_CONFIRM)

        # 创建权限请求
        request = self._permission_manager.create_request(
            request_id=request_id,
            session_id=session_id,
            tool_name=tool_name,
            command=command,
            full_input=payload.get("full_input", {}),
        )

        # 发送确认消息到 IM
        platform = self._get_platform_for_chat(task.chat_id)
        if platform:
            msg = self._permission_manager.format_request_message(request)
            platform.send(task.chat_id, Reply(content=msg))

        return {"status": "pending"}

    def _on_get_permission_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """获取权限请求的响应（供 Hook 轮询）"""
        request_id = payload.get("request_id", "")
        return self._permission_manager.get_response(request_id)

    # ============ 辅助方法 ============

    def _find_platform_for_message(self, message: Message) -> Optional[IMPlatform]:
        """根据消息找到对应的 IM 平台"""
        if self._im_platforms:
            return self._im_platforms[0]
        return None

    def _get_platform_for_chat(self, chat_id: str) -> Optional[IMPlatform]:
        """根据 chat_id 找到 IM 平台"""
        if self._im_platforms:
            return self._im_platforms[0]
        return None
