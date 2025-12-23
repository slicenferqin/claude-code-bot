"""任务管理器 - 管理 Claude Code 任务的生命周期"""

import subprocess
import threading
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"           # 等待执行
    RUNNING = "running"           # 执行中
    WAITING_CONFIRM = "waiting_confirm"  # 等待用户确认
    COMPLETED = "completed"       # 已完成
    FAILED = "failed"            # 执行失败
    CANCELLED = "cancelled"      # 已取消
    CANCELLING = "cancelling"    # 取消中


@dataclass
class Task:
    """任务实体"""
    id: str                      # 任务 ID（通常是 session_id）
    chat_id: str                 # 聊天 ID（用于发送消息）
    user_id: str                 # 用户 ID
    prompt: str                  # 用户输入的任务描述
    workspace: str               # 工作目录
    status: TaskStatus = TaskStatus.PENDING
    process: Optional[subprocess.Popen] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    files_changed: List[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "id": self.id,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "prompt": self.prompt,
            "workspace": self.workspace,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message,
            "files_changed": self.files_changed,
            "summary": self.summary,
        }


class TaskManager:
    """任务管理器

    负责管理所有 Claude Code 任务的生命周期，包括：
    - 任务创建和跟踪
    - 状态更新
    - 任务取消和回滚
    - 并发控制
    """

    def __init__(self, max_concurrent: int = 3, auto_rollback: bool = True):
        """初始化任务管理器

        Args:
            max_concurrent: 最大并发任务数
            auto_rollback: 取消时是否自动回滚改动
        """
        self._tasks: Dict[str, Task] = {}  # session_id -> Task
        self._chat_tasks: Dict[str, List[str]] = {}  # chat_id -> [session_ids]
        self._lock = threading.RLock()
        self._max_concurrent = max_concurrent
        self._auto_rollback = auto_rollback

    def create_task(
        self,
        session_id: str,
        chat_id: str,
        user_id: str,
        prompt: str,
        workspace: str = "."
    ) -> Optional[Task]:
        """创建任务

        Args:
            session_id: 会话 ID
            chat_id: 聊天 ID
            user_id: 用户 ID
            prompt: 任务描述
            workspace: 工作目录

        Returns:
            创建的任务，如果超过并发限制则返回 None
        """
        with self._lock:
            # 检查并发限制
            running_count = sum(
                1 for t in self._tasks.values()
                if t.status in (TaskStatus.RUNNING, TaskStatus.WAITING_CONFIRM)
            )
            if running_count >= self._max_concurrent:
                return None

            # 创建任务
            task = Task(
                id=session_id,
                chat_id=chat_id,
                user_id=user_id,
                prompt=prompt,
                workspace=workspace,
            )
            self._tasks[session_id] = task

            # 更新聊天任务索引
            if chat_id not in self._chat_tasks:
                self._chat_tasks[chat_id] = []
            self._chat_tasks[chat_id].append(session_id)

            return task

    def start_task(self, session_id: str, process: subprocess.Popen) -> bool:
        """标记任务开始执行

        Args:
            session_id: 会话 ID
            process: 子进程对象

        Returns:
            是否成功更新
        """
        with self._lock:
            task = self._tasks.get(session_id)
            if not task:
                return False

            task.status = TaskStatus.RUNNING
            task.process = process
            task.updated_at = datetime.now()
            return True

    def update_task_status(
        self,
        session_id: str,
        status: TaskStatus,
        error_message: Optional[str] = None
    ) -> bool:
        """更新任务状态

        Args:
            session_id: 会话 ID
            status: 新状态
            error_message: 错误信息（可选）

        Returns:
            是否成功更新
        """
        with self._lock:
            task = self._tasks.get(session_id)
            if not task:
                return False

            task.status = status
            task.updated_at = datetime.now()

            if error_message:
                task.error_message = error_message

            if status == TaskStatus.COMPLETED:
                task.completed_at = datetime.now()

            return True

    def get_task(self, session_id: str) -> Optional[Task]:
        """获取任务

        Args:
            session_id: 会话 ID

        Returns:
            任务对象，不存在返回 None
        """
        return self._tasks.get(session_id)

    def get_task_by_chat(self, chat_id: str) -> Optional[Task]:
        """获取聊天的当前活动任务

        Args:
            chat_id: 聊天 ID

        Returns:
            最近的活动任务，没有则返回 None
        """
        with self._lock:
            session_ids = self._chat_tasks.get(chat_id, [])
            for session_id in reversed(session_ids):
                task = self._tasks.get(session_id)
                if task and task.status in (
                    TaskStatus.RUNNING,
                    TaskStatus.WAITING_CONFIRM,
                    TaskStatus.COMPLETED
                ):
                    return task
            return None

    def get_active_tasks(self) -> List[Task]:
        """获取所有活动任务"""
        with self._lock:
            return [
                t for t in self._tasks.values()
                if t.status in (TaskStatus.RUNNING, TaskStatus.WAITING_CONFIRM)
            ]

    def complete_task(
        self,
        session_id: str,
        summary: str = "",
        files_changed: Optional[List[str]] = None
    ) -> bool:
        """标记任务完成

        Args:
            session_id: 会话 ID
            summary: 任务摘要
            files_changed: 修改的文件列表

        Returns:
            是否成功更新
        """
        with self._lock:
            task = self._tasks.get(session_id)
            if not task:
                return False

            task.status = TaskStatus.COMPLETED
            task.summary = summary
            task.files_changed = files_changed or []
            task.updated_at = datetime.now()
            task.completed_at = datetime.now()
            return True

    def cancel_task(self, session_id: str, rollback: bool = None) -> Dict[str, Any]:
        """取消任务

        Args:
            session_id: 会话 ID
            rollback: 是否回滚改动（None 使用默认设置）

        Returns:
            {"success": bool, "message": str}
        """
        if rollback is None:
            rollback = self._auto_rollback

        with self._lock:
            task = self._tasks.get(session_id)
            if not task:
                return {"success": False, "message": "任务不存在"}

            if task.status == TaskStatus.COMPLETED:
                return {"success": False, "message": "任务已完成，无法取消"}

            if task.status == TaskStatus.CANCELLED:
                return {"success": False, "message": "任务已取消"}

            # 更新状态
            task.status = TaskStatus.CANCELLING
            task.updated_at = datetime.now()

        # 终止进程（在锁外执行，避免死锁）
        if task.process and task.process.poll() is None:
            try:
                # 先尝试优雅终止
                task.process.terminate()
                try:
                    task.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    # 强制杀死
                    task.process.kill()
                    task.process.wait()
            except Exception as e:
                print(f"[TaskManager] Error killing process: {e}")

        # 回滚改动
        rollback_result = ""
        if rollback:
            rollback_result = self._rollback_changes(task)

        # 更新最终状态
        with self._lock:
            task.status = TaskStatus.CANCELLED
            task.updated_at = datetime.now()

        message = "任务已取消"
        if rollback_result:
            message += f"，{rollback_result}"

        return {"success": True, "message": message}

    def _rollback_changes(self, task: Task) -> str:
        """回滚改动（使用 git）

        Args:
            task: 任务对象

        Returns:
            回滚结果描述
        """
        workspace = task.workspace
        if not workspace or not os.path.isdir(workspace):
            return "工作目录无效"

        # 检查是否是 git 仓库
        git_dir = os.path.join(workspace, ".git")
        if not os.path.isdir(git_dir):
            return "非 git 仓库，无法回滚"

        try:
            # 检查是否有改动
            status_result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if not status_result.stdout.strip():
                return "无改动需要回滚"

            # 撤销已跟踪文件的改动
            subprocess.run(
                ["git", "checkout", "."],
                cwd=workspace,
                capture_output=True,
                timeout=30,
            )

            # 清理新增的未跟踪文件
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=workspace,
                capture_output=True,
                timeout=30,
            )

            return "改动已回滚"

        except subprocess.TimeoutExpired:
            return "回滚超时"
        except Exception as e:
            return f"回滚失败: {e}"

    def cleanup_old_tasks(self, max_age_hours: int = 24) -> int:
        """清理旧任务

        Args:
            max_age_hours: 最大保留时间（小时）

        Returns:
            清理的任务数量
        """
        from datetime import timedelta

        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        cleaned = 0

        with self._lock:
            to_remove = []
            for session_id, task in self._tasks.items():
                if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED):
                    if task.updated_at < cutoff:
                        to_remove.append(session_id)

            for session_id in to_remove:
                task = self._tasks.pop(session_id, None)
                if task:
                    # 清理聊天任务索引
                    if task.chat_id in self._chat_tasks:
                        try:
                            self._chat_tasks[task.chat_id].remove(session_id)
                        except ValueError:
                            pass
                    cleaned += 1

        return cleaned

    @property
    def task_count(self) -> int:
        """当前任务总数"""
        return len(self._tasks)

    @property
    def active_task_count(self) -> int:
        """活动任务数"""
        return len(self.get_active_tasks())
