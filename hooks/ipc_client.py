"""IPC 客户端 - 供 Hook 脚本使用与 Bot 服务通信"""

import socket
import json
import sys
import os
import time
from typing import Optional, Dict, Any


class IPCClient:
    """进程间通信客户端

    使用 Unix Domain Socket 与 Bot 服务通信。
    设计为同步阻塞模式，适用于 Hook 脚本。
    """

    SOCKET_PATH = "/tmp/claude-code-bot.sock"
    BUFFER_SIZE = 65536
    DEFAULT_TIMEOUT = 10  # 连接和读取的默认超时

    def __init__(self, socket_path: Optional[str] = None):
        """初始化客户端

        Args:
            socket_path: 可选的自定义 socket 路径
        """
        self._socket_path = socket_path or self.SOCKET_PATH
        self._sock: Optional[socket.socket] = None
        self._buffer = b""

    def connect(self, timeout: float = DEFAULT_TIMEOUT) -> bool:
        """连接到 Bot 服务

        Args:
            timeout: 连接超时时间（秒）

        Returns:
            是否连接成功
        """
        if not os.path.exists(self._socket_path):
            print(f"[IPCClient] Socket not found: {self._socket_path}", file=sys.stderr)
            return False

        try:
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.settimeout(timeout)
            self._sock.connect(self._socket_path)
            return True
        except socket.timeout:
            print(f"[IPCClient] Connection timeout", file=sys.stderr)
            self._cleanup()
            return False
        except Exception as e:
            print(f"[IPCClient] Connection failed: {e}", file=sys.stderr)
            self._cleanup()
            return False

    def send(
        self,
        msg_type: str,
        payload: Dict[str, Any],
        request_id: Optional[str] = None,
        wait_response: bool = False,
        response_timeout: float = 60
    ) -> Optional[Dict[str, Any]]:
        """发送消息

        Args:
            msg_type: 消息类型
            payload: 消息内容
            request_id: 请求 ID（用于匹配响应）
            wait_response: 是否等待响应
            response_timeout: 响应超时时间（秒）

        Returns:
            如果 wait_response=True，返回响应消息；否则返回 None
        """
        if not self._sock:
            print("[IPCClient] Not connected", file=sys.stderr)
            return None

        message = {
            "type": msg_type,
            "payload": payload,
        }

        if request_id:
            message["request_id"] = request_id

        try:
            data = json.dumps(message).encode() + b"\n"
            self._sock.sendall(data)

            if wait_response and request_id:
                return self._wait_for_response(request_id, response_timeout)

            return None

        except Exception as e:
            print(f"[IPCClient] Send failed: {e}", file=sys.stderr)
            return None

    def _wait_for_response(
        self,
        request_id: str,
        timeout: float
    ) -> Optional[Dict[str, Any]]:
        """等待指定 request_id 的响应

        Args:
            request_id: 请求 ID
            timeout: 超时时间（秒）

        Returns:
            响应消息，超时返回 None
        """
        if not self._sock:
            return None

        self._sock.settimeout(timeout)
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                # 尝试读取数据
                chunk = self._sock.recv(self.BUFFER_SIZE)
                if not chunk:
                    # 连接关闭
                    return None

                self._buffer += chunk

                # 检查是否有完整的消息
                while b"\n" in self._buffer:
                    line, self._buffer = self._buffer.split(b"\n", 1)
                    try:
                        message = json.loads(line.decode())
                        # 检查是否是我们等待的响应
                        if message.get("request_id") == request_id:
                            return message
                    except json.JSONDecodeError:
                        continue

            except socket.timeout:
                # 继续等待
                continue
            except Exception as e:
                print(f"[IPCClient] Receive failed: {e}", file=sys.stderr)
                return None

        return None

    def poll_for_response(
        self,
        msg_type: str,
        request_id: str,
        poll_interval: float = 2.0,
        max_wait: float = 3600
    ) -> Optional[Dict[str, Any]]:
        """轮询等待响应

        用于长时间等待场景（如用户确认）。
        定期发送查询请求，直到收到响应或超时。

        Args:
            msg_type: 查询消息类型
            request_id: 请求 ID
            poll_interval: 轮询间隔（秒）
            max_wait: 最大等待时间（秒）

        Returns:
            响应消息，超时返回 None
        """
        start_time = time.time()

        while time.time() - start_time < max_wait:
            # 发送查询请求
            response = self.send(
                msg_type,
                {"request_id": request_id},
                request_id=f"poll_{request_id}_{int(time.time())}",
                wait_response=True,
                response_timeout=poll_interval + 1
            )

            if response:
                payload = response.get("payload", {})
                status = payload.get("status")

                if status == "responded":
                    return payload.get("response")
                elif status == "cancelled":
                    return {"decision": "deny", "reason": "Task cancelled"}
                elif status == "not_found":
                    return {"decision": "deny", "reason": "Request not found"}
                # status == "pending" 继续等待

            time.sleep(poll_interval)

        return None

    def close(self) -> None:
        """关闭连接"""
        self._cleanup()

    def _cleanup(self) -> None:
        """清理资源"""
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self._buffer = b""

    def __enter__(self):
        """Context manager 入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager 出口"""
        self.close()
        return False

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self._sock is not None


def get_session_id_from_env() -> Optional[str]:
    """从环境变量获取 session ID

    Claude Code 通过环境变量传递 session ID。
    """
    return os.environ.get("CLAUDE_SESSION_ID")


def get_project_dir() -> str:
    """获取项目根目录

    Hook 脚本运行时的工作目录通常是项目根目录。
    """
    # 首先尝试环境变量
    project_dir = os.environ.get("CLAUDE_CODE_BOT_DIR")
    if project_dir:
        return project_dir

    # 否则使用 hooks 目录的父目录
    hooks_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(hooks_dir)
