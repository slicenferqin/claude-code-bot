"""IM 平台抽象接口"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional, Any


@dataclass
class Message:
    """统一消息格式"""

    id: str  # 消息 ID
    chat_id: str  # 会话 ID
    content: str  # 消息内容
    sender_id: str  # 发送者 ID
    is_private: bool  # 是否私聊
    raw: Optional[Any] = None  # 原始数据（平台特定）


@dataclass
class Reply:
    """统一回复格式"""

    content: str  # 回复内容
    reply_to_message_id: Optional[str] = None  # 回复的消息 ID


class IMPlatform(ABC):
    """IM 平台抽象接口

    所有 IM 平台插件都需要实现此接口。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """平台名称"""
        pass

    @abstractmethod
    def start(self, on_message: Callable[[Message], None]) -> None:
        """启动监听

        Args:
            on_message: 消息回调函数，收到消息时调用
        """
        pass

    @abstractmethod
    def stop(self) -> None:
        """停止监听"""
        pass

    @abstractmethod
    def send(self, chat_id: str, reply: Reply) -> bool:
        """发送消息

        Args:
            chat_id: 目标会话 ID
            reply: 回复内容

        Returns:
            是否发送成功
        """
        pass

    @abstractmethod
    def reply(self, message: Message, reply: Reply) -> bool:
        """回复消息

        Args:
            message: 原始消息
            reply: 回复内容

        Returns:
            是否发送成功
        """
        pass
