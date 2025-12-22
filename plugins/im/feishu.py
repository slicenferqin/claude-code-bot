"""飞书 IM 平台插件"""

import json
from typing import Callable, Optional

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
    P2ImMessageReceiveV1,
)

from core.registry import PluginRegistry
from interfaces.im import IMPlatform, Message, Reply


@PluginRegistry.register_im("feishu")
class FeishuPlatform(IMPlatform):
    """飞书平台实现"""

    def __init__(self, app_id: str, app_secret: str):
        """初始化飞书平台

        Args:
            app_id: 飞书应用 App ID
            app_secret: 飞书应用 App Secret
        """
        self.app_id = app_id
        self.app_secret = app_secret
        self._on_message: Optional[Callable[[Message], None]] = None
        self._client: Optional[lark.Client] = None
        self._ws_client: Optional[lark.ws.Client] = None

    @property
    def name(self) -> str:
        return "feishu"

    def start(self, on_message: Callable[[Message], None]) -> None:
        """启动飞书 WebSocket 监听"""
        self._on_message = on_message

        # 创建事件处理器
        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._handle_message)
            .build()
        )

        # 创建客户端
        self._client = (
            lark.Client.builder()
            .app_id(self.app_id)
            .app_secret(self.app_secret)
            .build()
        )

        # 创建 WebSocket 客户端
        self._ws_client = lark.ws.Client(
            self.app_id,
            self.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.DEBUG,
        )

        # 启动长连接
        self._ws_client.start()

    def stop(self) -> None:
        """停止监听"""
        # lark SDK 没有提供显式的 stop 方法
        pass

    def send(self, chat_id: str, reply: Reply) -> bool:
        """发送消息到指定会话"""
        if not self._client:
            return False

        content = json.dumps({"text": reply.content}, ensure_ascii=False)

        try:
            request = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = self._client.im.v1.message.create(request)
            return response.success()
        except Exception as e:
            print(f"[Feishu] 发送消息失败: {e}")
            return False

    def reply(self, message: Message, reply: Reply) -> bool:
        """回复消息"""
        if not self._client:
            return False

        content = json.dumps({"text": reply.content}, ensure_ascii=False)

        try:
            if message.is_private:
                # 私聊直接发送
                return self.send(message.chat_id, reply)
            else:
                # 群聊回复
                request = (
                    ReplyMessageRequest.builder()
                    .message_id(message.id)
                    .request_body(
                        ReplyMessageRequestBody.builder()
                        .content(content)
                        .msg_type("text")
                        .build()
                    )
                    .build()
                )
                response = self._client.im.v1.message.reply(request)
                return response.success()
        except Exception as e:
            print(f"[Feishu] 回复消息失败: {e}")
            return False

    def _handle_message(self, data: P2ImMessageReceiveV1) -> None:
        """处理接收到的消息"""
        if not self._on_message:
            return

        # 只处理文本消息
        if data.event.message.message_type != "text":
            return

        try:
            content = json.loads(data.event.message.content)["text"]
        except (json.JSONDecodeError, KeyError):
            return

        # 构建统一消息格式
        message = Message(
            id=data.event.message.message_id,
            chat_id=data.event.message.chat_id,
            content=content,
            sender_id=data.event.sender.sender_id.user_id or "",
            is_private=data.event.message.chat_type == "p2p",
            raw=data,
        )

        # 调用回调
        self._on_message(message)
