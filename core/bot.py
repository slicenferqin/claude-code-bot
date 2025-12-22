"""Bot 核心逻辑"""

import threading
from typing import List, Optional
from datetime import datetime

from interfaces.im import IMPlatform, Message, Reply
from interfaces.cli import CLITool, ExecutionStatus
from core.session import SessionManager


class Bot:
    """Bot 核心类

    负责：
    - 管理 IM 平台和 CLI 工具
    - 消息路由和处理
    - 会话管理
    """

    def __init__(
        self,
        cli_tool: CLITool,
        trigger_keyword: str = "claude code",
        workspace: str = ".",
        default_timeout: int = 180,
        max_output_length: int = 3000,
    ):
        """初始化 Bot

        Args:
            cli_tool: CLI 工具实例
            trigger_keyword: 触发关键词
            workspace: 工作目录
            default_timeout: 默认超时时间（秒）
            max_output_length: 最大输出长度
        """
        self.cli_tool = cli_tool
        self.trigger_keyword = trigger_keyword.lower()
        self.workspace = workspace
        self.default_timeout = default_timeout
        self.max_output_length = max_output_length

        self._im_platforms: List[IMPlatform] = []
        self._session_manager = SessionManager()
        self._processed_messages = set()
        self._processed_lock = threading.Lock()

    def add_im_platform(self, platform: IMPlatform) -> None:
        """添加 IM 平台

        Args:
            platform: IM 平台实例
        """
        self._im_platforms.append(platform)

    def start(self) -> None:
        """启动 Bot"""
        print(f"\n{'=' * 60}")
        print(f"Bot 启动中...")
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

        # 启动所有 IM 平台
        for platform in self._im_platforms:
            print(f"[Bot] 启动 IM 平台: {platform.name}")
            platform.start(self._on_message)

    def stop(self) -> None:
        """停止 Bot"""
        for platform in self._im_platforms:
            platform.stop()
        print("[Bot] 已停止")

    def _on_message(self, message: Message) -> None:
        """消息回调

        Args:
            message: 收到的消息
        """
        # 消息去重
        with self._processed_lock:
            if message.id in self._processed_messages:
                return
            self._processed_messages.add(message.id)

        # 检查触发关键词
        content_lower = message.content.lower()
        if self.trigger_keyword not in content_lower:
            return

        # 提取 prompt
        prompt = content_lower.replace(self.trigger_keyword, "", 1).strip()
        if not prompt:
            prompt = "hello"

        print(f"\n[Bot] 收到命令: {prompt[:100]}")
        print(f"[Bot] 消息 ID: {message.id}")
        print(f"[Bot] 会话 ID: {message.chat_id}")

        # 异步处理
        thread = threading.Thread(
            target=self._process_task,
            args=(message, prompt),
            daemon=True,
        )
        thread.start()

    def _process_task(self, message: Message, prompt: str) -> None:
        """处理任务

        Args:
            message: 原始消息
            prompt: 提取的 prompt
        """
        # 找到对应的 IM 平台
        platform = self._find_platform_for_message(message)
        if not platform:
            print(f"[Bot] 找不到消息对应的 IM 平台")
            return

        try:
            # 发送处理中反馈
            platform.reply(
                message,
                Reply(content=f"🤖 正在处理您的请求...\n\n📝 任务: {prompt}"),
            )

            # 获取会话 ID
            session_id = self._session_manager.get_or_create_session_id(message.chat_id)

            # 执行 CLI 命令
            result = self.cli_tool.execute(
                prompt=prompt,
                session_id=session_id,
                workspace=self.workspace,
                timeout=self.default_timeout,
            )

            # 构建回复
            if result.status == ExecutionStatus.SUCCESS:
                output = result.output
                if len(output) > self.max_output_length:
                    output = (
                        output[: self.max_output_length]
                        + f"\n\n... (输出过长，已截断，共 {len(result.output)} 字符)"
                    )
                reply_content = f"✅ 任务完成\n\n{output}"
            elif result.status == ExecutionStatus.TIMEOUT:
                reply_content = "⏱️ 执行超时"
            else:
                reply_content = f"❌ 执行失败\n\n{result.error or result.output}"

            # 发送结果
            platform.send(message.chat_id, Reply(content=reply_content))

        except Exception as e:
            print(f"[Bot] 处理任务出错: {e}")
            import traceback

            traceback.print_exc()
            platform.send(message.chat_id, Reply(content=f"❌ 处理出错: {str(e)}"))

    def _find_platform_for_message(self, message: Message) -> Optional[IMPlatform]:
        """根据消息找到对应的 IM 平台

        Args:
            message: 消息

        Returns:
            IM 平台实例
        """
        # 简单实现：返回第一个平台
        # 未来可以根据 message.raw 中的信息判断
        if self._im_platforms:
            return self._im_platforms[0]
        return None
