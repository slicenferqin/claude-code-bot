"""权限确认管理器 - 管理 Claude Code 的权限确认请求"""

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any, Callable


class PermissionStatus(Enum):
    """权限请求状态"""
    PENDING = "pending"       # 等待用户确认
    APPROVED = "approved"     # 已批准
    DENIED = "denied"         # 已拒绝
    CANCELLED = "cancelled"   # 已取消
    EXPIRED = "expired"       # 已过期


@dataclass
class PermissionRequest:
    """权限确认请求"""
    request_id: str           # 请求 ID
    session_id: str           # 任务会话 ID
    tool_name: str            # 工具名称
    command: str              # 命令描述
    full_input: Dict[str, Any] = field(default_factory=dict)  # 完整输入
    status: PermissionStatus = PermissionStatus.PENDING
    response: Optional[Dict[str, Any]] = None
    created_at: datetime = field(default_factory=datetime.now)
    responded_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "tool_name": self.tool_name,
            "command": self.command,
            "status": self.status.value,
            "response": self.response,
            "created_at": self.created_at.isoformat(),
            "responded_at": self.responded_at.isoformat() if self.responded_at else None,
        }


class PermissionManager:
    """权限确认管理器

    管理所有待确认的权限请求，支持：
    - 创建和存储请求
    - 用户响应处理
    - 超时清理
    - 会话级别的批量操作
    """

    def __init__(self, default_timeout: float = 3600):
        """初始化权限管理器

        Args:
            default_timeout: 默认超时时间（秒），默认 1 小时
        """
        self._requests: Dict[str, PermissionRequest] = {}
        self._session_requests: Dict[str, List[str]] = {}  # session_id -> [request_ids]
        self._lock = threading.RLock()
        self._default_timeout = default_timeout
        self._callbacks: Dict[str, Callable] = {}  # request_id -> callback

    def create_request(
        self,
        request_id: str,
        session_id: str,
        tool_name: str,
        command: str,
        full_input: Optional[Dict[str, Any]] = None
    ) -> PermissionRequest:
        """创建权限确认请求

        Args:
            request_id: 请求 ID
            session_id: 会话 ID
            tool_name: 工具名称
            command: 命令描述
            full_input: 完整的工具输入

        Returns:
            创建的请求对象
        """
        with self._lock:
            request = PermissionRequest(
                request_id=request_id,
                session_id=session_id,
                tool_name=tool_name,
                command=command,
                full_input=full_input or {},
            )
            self._requests[request_id] = request

            # 更新会话索引
            if session_id not in self._session_requests:
                self._session_requests[session_id] = []
            self._session_requests[session_id].append(request_id)

            return request

    def respond(
        self,
        request_id: str,
        decision: str,
        reason: str = ""
    ) -> bool:
        """响应权限请求

        Args:
            request_id: 请求 ID
            decision: 决策（"approve" 或 "deny"）
            reason: 原因说明

        Returns:
            是否成功响应
        """
        with self._lock:
            request = self._requests.get(request_id)
            if not request:
                return False

            if request.status != PermissionStatus.PENDING:
                return False

            # 更新状态
            if decision.lower() in ("approve", "yes", "ok", "y"):
                request.status = PermissionStatus.APPROVED
                decision = "approve"
            else:
                request.status = PermissionStatus.DENIED
                decision = "deny"

            request.response = {
                "decision": decision,
                "reason": reason,
            }
            request.responded_at = datetime.now()

            return True

    def get_request(self, request_id: str) -> Optional[PermissionRequest]:
        """获取请求

        Args:
            request_id: 请求 ID

        Returns:
            请求对象，不存在返回 None
        """
        return self._requests.get(request_id)

    def get_response(self, request_id: str) -> Optional[Dict[str, Any]]:
        """获取请求的响应

        Args:
            request_id: 请求 ID

        Returns:
            响应内容，包含 status 和 response 字段
        """
        with self._lock:
            request = self._requests.get(request_id)
            if not request:
                return {"status": "not_found", "response": None}

            if request.status == PermissionStatus.PENDING:
                # 检查是否过期
                elapsed = (datetime.now() - request.created_at).total_seconds()
                if elapsed > self._default_timeout:
                    request.status = PermissionStatus.EXPIRED
                    request.response = {
                        "decision": "deny",
                        "reason": "Request expired",
                    }

            return {
                "status": "responded" if request.status != PermissionStatus.PENDING else "pending",
                "response": request.response,
            }

    def get_pending_for_session(self, session_id: str) -> List[PermissionRequest]:
        """获取会话的所有待确认请求

        Args:
            session_id: 会话 ID

        Returns:
            待确认请求列表
        """
        with self._lock:
            request_ids = self._session_requests.get(session_id, [])
            return [
                self._requests[rid]
                for rid in request_ids
                if rid in self._requests and
                self._requests[rid].status == PermissionStatus.PENDING
            ]

    def get_latest_pending(self, session_id: str) -> Optional[PermissionRequest]:
        """获取会话最新的待确认请求

        Args:
            session_id: 会话 ID

        Returns:
            最新的待确认请求
        """
        pending = self.get_pending_for_session(session_id)
        return pending[-1] if pending else None

    def cancel_all_for_session(self, session_id: str) -> int:
        """取消会话的所有待确认请求

        Args:
            session_id: 会话 ID

        Returns:
            取消的请求数量
        """
        count = 0
        with self._lock:
            request_ids = self._session_requests.get(session_id, [])
            for rid in request_ids:
                request = self._requests.get(rid)
                if request and request.status == PermissionStatus.PENDING:
                    request.status = PermissionStatus.CANCELLED
                    request.response = {
                        "decision": "deny",
                        "reason": "Task cancelled by user",
                    }
                    request.responded_at = datetime.now()
                    count += 1
        return count

    def cleanup_expired(self) -> int:
        """清理过期的请求

        Returns:
            清理的请求数量
        """
        count = 0
        now = datetime.now()

        with self._lock:
            for request in self._requests.values():
                if request.status == PermissionStatus.PENDING:
                    elapsed = (now - request.created_at).total_seconds()
                    if elapsed > self._default_timeout:
                        request.status = PermissionStatus.EXPIRED
                        request.response = {
                            "decision": "deny",
                            "reason": "Request expired",
                        }
                        request.responded_at = now
                        count += 1

        return count

    def cleanup_old_requests(self, max_age_hours: int = 24) -> int:
        """清理旧的已处理请求

        Args:
            max_age_hours: 最大保留时间（小时）

        Returns:
            清理的请求数量
        """
        from datetime import timedelta

        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        cleaned = 0

        with self._lock:
            to_remove = []
            for request_id, request in self._requests.items():
                if request.status != PermissionStatus.PENDING:
                    check_time = request.responded_at or request.created_at
                    if check_time < cutoff:
                        to_remove.append(request_id)

            for request_id in to_remove:
                request = self._requests.pop(request_id, None)
                if request:
                    # 清理会话索引
                    if request.session_id in self._session_requests:
                        try:
                            self._session_requests[request.session_id].remove(request_id)
                        except ValueError:
                            pass
                    cleaned += 1

        return cleaned

    def format_request_message(self, request: PermissionRequest) -> str:
        """格式化请求为用户可读的消息

        Args:
            request: 请求对象

        Returns:
            格式化的消息文本
        """
        lines = [
            "⚠️ Claude 请求执行以下操作：",
            "",
            f"工具: {request.tool_name}",
            f"命令: {request.command}",
            "",
            "请回复：",
            '- "ok" 或 "y" 批准',
            '- "no" 或 "n" 拒绝',
            '- "cancel" 取消整个任务',
        ]
        return "\n".join(lines)

    @property
    def pending_count(self) -> int:
        """待确认请求数量"""
        with self._lock:
            return sum(
                1 for r in self._requests.values()
                if r.status == PermissionStatus.PENDING
            )

    @property
    def total_count(self) -> int:
        """总请求数量"""
        return len(self._requests)
