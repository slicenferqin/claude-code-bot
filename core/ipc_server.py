"""IPC 服务端 - 用于 Bot 服务与 Hook 脚本之间的双向通信"""

import asyncio
import json
import os
from typing import Callable, Dict, Any, Optional, Set
from datetime import datetime


class IPCServer:
    """进程间通信服务端

    使用 Unix Domain Socket 实现 Bot 服务与 Hook 脚本之间的双向通信。
    """

    SOCKET_PATH = "/tmp/claude-code-bot.sock"

    def __init__(self):
        self._handlers: Dict[str, Callable] = {}
        self._pending_requests: Dict[str, asyncio.Future] = {}
        self._server: Optional[asyncio.AbstractServer] = None
        self._clients: Set[asyncio.StreamWriter] = set()
        self._running = False

    def on(self, event_type: str, handler: Callable) -> None:
        """注册消息处理器

        Args:
            event_type: 消息类型
            handler: 处理函数，接收 payload 返回响应
        """
        self._handlers[event_type] = handler

    async def start(self) -> None:
        """启动 IPC 服务"""
        # 清理旧的 socket 文件
        if os.path.exists(self.SOCKET_PATH):
            os.unlink(self.SOCKET_PATH)

        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=self.SOCKET_PATH
        )
        os.chmod(self.SOCKET_PATH, 0o600)  # 仅当前用户可访问
        self._running = True
        print(f"[IPC] Server listening on {self.SOCKET_PATH}")

    async def stop(self) -> None:
        """停止 IPC 服务"""
        self._running = False

        # 关闭所有客户端连接
        for writer in self._clients:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        self._clients.clear()

        # 关闭服务器
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        # 清理 socket 文件
        if os.path.exists(self.SOCKET_PATH):
            os.unlink(self.SOCKET_PATH)

        print("[IPC] Server stopped")

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter
    ) -> None:
        """处理客户端连接"""
        self._clients.add(writer)
        client_id = id(writer)
        print(f"[IPC] Client {client_id} connected")

        try:
            while self._running:
                line = await reader.readline()
                if not line:
                    break

                try:
                    message = json.loads(line.decode())
                    await self._dispatch(message, writer)
                except json.JSONDecodeError as e:
                    print(f"[IPC] Invalid JSON from client {client_id}: {e}")
                except Exception as e:
                    print(f"[IPC] Error handling message from client {client_id}: {e}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[IPC] Client {client_id} error: {e}")
        finally:
            self._clients.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            print(f"[IPC] Client {client_id} disconnected")

    async def _dispatch(self, message: dict, writer: asyncio.StreamWriter) -> None:
        """分发消息到处理器"""
        msg_type = message.get("type")
        request_id = message.get("request_id")
        payload = message.get("payload", {})

        print(f"[IPC] Received message: type={msg_type}, request_id={request_id}")

        # 如果是响应消息（用于 pending requests）
        if request_id and request_id in self._pending_requests:
            future = self._pending_requests[request_id]
            if not future.done():
                future.set_result(message)
            return

        # 调用处理器
        handler = self._handlers.get(msg_type)
        if handler:
            try:
                # 处理器可以是同步或异步的
                result = handler(payload)
                if asyncio.iscoroutine(result):
                    result = await result

                # 如果有响应，发送回客户端
                if result is not None:
                    response = {
                        "type": f"{msg_type}_response",
                        "request_id": request_id,
                        "payload": result
                    }
                    await self._send_to_client(writer, response)

            except Exception as e:
                print(f"[IPC] Handler error for {msg_type}: {e}")
                # 发送错误响应
                error_response = {
                    "type": "error",
                    "request_id": request_id,
                    "payload": {"error": str(e)}
                }
                await self._send_to_client(writer, error_response)
        else:
            print(f"[IPC] No handler for message type: {msg_type}")

    async def _send_to_client(
        self,
        writer: asyncio.StreamWriter,
        message: dict
    ) -> bool:
        """发送消息给指定客户端"""
        try:
            data = json.dumps(message).encode() + b"\n"
            writer.write(data)
            await writer.drain()
            return True
        except Exception as e:
            print(f"[IPC] Failed to send message: {e}")
            return False

    async def broadcast(self, message: dict) -> int:
        """广播消息给所有客户端

        Args:
            message: 要广播的消息

        Returns:
            成功发送的客户端数量
        """
        data = json.dumps(message).encode() + b"\n"
        sent = 0

        for writer in list(self._clients):
            try:
                writer.write(data)
                await writer.drain()
                sent += 1
            except Exception as e:
                print(f"[IPC] Failed to broadcast to client: {e}")
                self._clients.discard(writer)

        return sent

    def create_pending_request(self, request_id: str) -> asyncio.Future:
        """创建一个等待响应的请求

        Args:
            request_id: 请求 ID

        Returns:
            Future 对象，等待响应时会被设置结果
        """
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending_requests[request_id] = future
        return future

    async def wait_for_response(
        self,
        request_id: str,
        timeout: float = 300
    ) -> Optional[dict]:
        """等待指定 request_id 的响应

        Args:
            request_id: 请求 ID
            timeout: 超时时间（秒）

        Returns:
            响应消息，超时返回 None
        """
        future = self._pending_requests.get(request_id)
        if not future:
            future = self.create_pending_request(request_id)

        try:
            result = await asyncio.wait_for(future, timeout)
            return result
        except asyncio.TimeoutError:
            print(f"[IPC] Request {request_id} timed out after {timeout}s")
            return None
        finally:
            self._pending_requests.pop(request_id, None)

    def resolve_request(self, request_id: str, response: dict) -> bool:
        """解决一个等待中的请求

        Args:
            request_id: 请求 ID
            response: 响应内容

        Returns:
            是否成功解决
        """
        future = self._pending_requests.get(request_id)
        if future and not future.done():
            future.set_result({"payload": response})
            return True
        return False

    @property
    def is_running(self) -> bool:
        """服务是否正在运行"""
        return self._running

    @property
    def client_count(self) -> int:
        """当前连接的客户端数量"""
        return len(self._clients)
