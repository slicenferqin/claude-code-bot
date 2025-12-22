"""会话管理器"""

import uuid
import threading
from typing import Dict, Optional
from datetime import datetime, timedelta


class SessionManager:
    """会话管理器

    管理聊天会话与 CLI 会话的映射关系。
    每个聊天会话对应一个唯一的 CLI session_id。
    """

    def __init__(self, session_ttl_hours: int = 24):
        """初始化会话管理器

        Args:
            session_ttl_hours: 会话过期时间（小时）
        """
        self._sessions: Dict[str, str] = {}  # chat_id -> session_id
        self._last_active: Dict[str, datetime] = {}  # chat_id -> last_active_time
        self._lock = threading.Lock()
        self._ttl = timedelta(hours=session_ttl_hours)

        # 启动清理线程
        self._start_cleanup_thread()

    def get_or_create_session_id(self, chat_id: str) -> str:
        """获取或创建会话 ID

        Args:
            chat_id: 聊天会话 ID

        Returns:
            CLI session_id
        """
        with self._lock:
            if chat_id not in self._sessions:
                # 使用 UUID5 生成确定性的 session_id
                session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"im-cli-bot:{chat_id}"))
                self._sessions[chat_id] = session_id

            self._last_active[chat_id] = datetime.now()
            return self._sessions[chat_id]

    def remove_session(self, chat_id: str) -> None:
        """移除会话

        Args:
            chat_id: 聊天会话 ID
        """
        with self._lock:
            self._sessions.pop(chat_id, None)
            self._last_active.pop(chat_id, None)

    def get_session_id(self, chat_id: str) -> Optional[str]:
        """获取会话 ID（不创建）

        Args:
            chat_id: 聊天会话 ID

        Returns:
            CLI session_id，如果不存在则返回 None
        """
        return self._sessions.get(chat_id)

    def _start_cleanup_thread(self) -> None:
        """启动会话清理线程"""

        def cleanup():
            while True:
                threading.Event().wait(300)  # 每 5 分钟清理一次
                self._cleanup_expired_sessions()

        thread = threading.Thread(target=cleanup, daemon=True)
        thread.start()

    def _cleanup_expired_sessions(self) -> None:
        """清理过期会话"""
        with self._lock:
            now = datetime.now()
            expired = [
                chat_id
                for chat_id, last_active in self._last_active.items()
                if now - last_active > self._ttl
            ]
            for chat_id in expired:
                del self._sessions[chat_id]
                del self._last_active[chat_id]

            if expired:
                print(f"[SessionManager] Cleaned up {len(expired)} expired sessions")
