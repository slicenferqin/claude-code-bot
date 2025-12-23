"""文字命令解析器 - 解析用户通过 IM 发送的命令"""

import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple, List


class CommandType(Enum):
    """命令类型"""
    # 权限确认命令
    APPROVE = "approve"          # 批准操作
    DENY = "deny"                # 拒绝操作

    # 任务控制命令
    CANCEL = "cancel"            # 取消任务
    CONTINUE = "continue"        # 继续修改

    # 代码操作命令
    DIFF = "diff"                # 查看改动
    COMMIT = "commit"            # 提交代码
    PUSH = "push"                # 推送代码
    ROLLBACK = "rollback"        # 回滚改动

    # 查询命令
    STATUS = "status"            # 查看任务状态

    # 普通消息（发给 Claude）
    MESSAGE = "message"


@dataclass
class ParsedCommand:
    """解析后的命令"""
    type: CommandType
    args: str = ""               # 命令参数（如 commit 消息）
    raw: str = ""                # 原始输入


class CommandParser:
    """命令解析器

    解析用户输入的文字命令，支持多种格式：
    - 单词命令：ok, y, yes, no, n, cancel, diff, commit, push, rollback
    - 带参数命令：commit 修复登录bug, continue 继续优化, diff handler
    - 普通消息：其他所有输入
    """

    # 命令别名映射
    APPROVE_ALIASES = {"ok", "y", "yes", "approve", "批准", "确认", "同意", "好", "行"}
    DENY_ALIASES = {"no", "n", "deny", "reject", "拒绝", "不", "不行"}
    CANCEL_ALIASES = {"cancel", "取消", "停止", "stop", "abort"}
    CONTINUE_ALIASES = {"continue", "继续", "再", "接着"}
    DIFF_ALIASES = {"diff", "查看", "改动", "变更"}
    COMMIT_ALIASES = {"commit", "提交"}
    PUSH_ALIASES = {"push", "推送", "推"}
    ROLLBACK_ALIASES = {"rollback", "回滚", "撤销", "还原", "revert"}
    STATUS_ALIASES = {"status", "状态", "进度"}

    def parse(self, text: str) -> ParsedCommand:
        """解析用户输入

        Args:
            text: 用户输入的文本

        Returns:
            ParsedCommand: 解析后的命令
        """
        text = text.strip()
        if not text:
            return ParsedCommand(type=CommandType.MESSAGE, raw=text)

        # 提取第一个词和剩余部分
        parts = text.split(maxsplit=1)
        first_word = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        # 匹配命令
        if first_word in self.APPROVE_ALIASES:
            return ParsedCommand(type=CommandType.APPROVE, raw=text)

        if first_word in self.DENY_ALIASES:
            return ParsedCommand(type=CommandType.DENY, raw=text)

        if first_word in self.CANCEL_ALIASES:
            return ParsedCommand(type=CommandType.CANCEL, raw=text)

        if first_word in self.CONTINUE_ALIASES:
            return ParsedCommand(type=CommandType.CONTINUE, args=args, raw=text)

        if first_word in self.DIFF_ALIASES:
            return ParsedCommand(type=CommandType.DIFF, args=args, raw=text)

        if first_word in self.COMMIT_ALIASES:
            return ParsedCommand(type=CommandType.COMMIT, args=args, raw=text)

        if first_word in self.PUSH_ALIASES:
            return ParsedCommand(type=CommandType.PUSH, raw=text)

        if first_word in self.ROLLBACK_ALIASES:
            return ParsedCommand(type=CommandType.ROLLBACK, raw=text)

        if first_word in self.STATUS_ALIASES:
            return ParsedCommand(type=CommandType.STATUS, raw=text)

        # 默认作为普通消息
        return ParsedCommand(type=CommandType.MESSAGE, args=text, raw=text)

    def is_permission_response(self, cmd: ParsedCommand) -> bool:
        """判断是否是权限确认响应"""
        return cmd.type in (CommandType.APPROVE, CommandType.DENY)

    def is_task_control(self, cmd: ParsedCommand) -> bool:
        """判断是否是任务控制命令"""
        return cmd.type in (CommandType.CANCEL, CommandType.CONTINUE)

    def is_code_operation(self, cmd: ParsedCommand) -> bool:
        """判断是否是代码操作命令"""
        return cmd.type in (
            CommandType.DIFF,
            CommandType.COMMIT,
            CommandType.PUSH,
            CommandType.ROLLBACK
        )


class GitOperations:
    """Git 操作工具类"""

    @staticmethod
    def get_diff(workspace: str, file_pattern: Optional[str] = None) -> Tuple[bool, str]:
        """获取 git diff

        Args:
            workspace: 工作目录
            file_pattern: 文件匹配模式（可选）

        Returns:
            (成功, diff内容或错误信息)
        """
        try:
            cmd = ["git", "diff"]
            if file_pattern:
                # 支持部分匹配
                cmd.append(f"*{file_pattern}*")

            result = subprocess.run(
                cmd,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                return False, result.stderr or "获取 diff 失败"

            diff = result.stdout.strip()
            if not diff:
                # 检查暂存区
                result = subprocess.run(
                    ["git", "diff", "--staged"],
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                diff = result.stdout.strip()

            if not diff:
                return True, "没有改动"

            return True, diff

        except subprocess.TimeoutExpired:
            return False, "操作超时"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def get_status(workspace: str) -> Tuple[bool, str]:
        """获取 git status

        Args:
            workspace: 工作目录

        Returns:
            (成功, status内容或错误信息)
        """
        try:
            result = subprocess.run(
                ["git", "status", "--short"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                return False, result.stderr or "获取状态失败"

            status = result.stdout.strip()
            if not status:
                return True, "工作目录干净，没有改动"

            return True, status

        except Exception as e:
            return False, str(e)

    @staticmethod
    def commit(workspace: str, message: str) -> Tuple[bool, str]:
        """提交代码

        Args:
            workspace: 工作目录
            message: 提交信息

        Returns:
            (成功, 提交hash或错误信息)
        """
        try:
            # 先添加所有改动
            subprocess.run(
                ["git", "add", "-A"],
                cwd=workspace,
                capture_output=True,
                timeout=30,
            )

            # 检查是否有要提交的内容
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if not status.stdout.strip():
                return False, "没有要提交的改动"

            # 提交
            result = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                return False, result.stderr or "提交失败"

            # 获取提交 hash
            hash_result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=10,
            )

            commit_hash = hash_result.stdout.strip()
            return True, commit_hash

        except Exception as e:
            return False, str(e)

    @staticmethod
    def push(workspace: str) -> Tuple[bool, str]:
        """推送代码

        Args:
            workspace: 工作目录

        Returns:
            (成功, 结果信息)
        """
        try:
            result = subprocess.run(
                ["git", "push"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode != 0:
                return False, result.stderr or "推送失败"

            return True, "推送成功"

        except subprocess.TimeoutExpired:
            return False, "推送超时"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def rollback(workspace: str) -> Tuple[bool, str]:
        """回滚改动

        Args:
            workspace: 工作目录

        Returns:
            (成功, 结果信息)
        """
        try:
            # 撤销已跟踪文件的改动
            subprocess.run(
                ["git", "checkout", "."],
                cwd=workspace,
                capture_output=True,
                timeout=30,
            )

            # 清理未跟踪文件
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=workspace,
                capture_output=True,
                timeout=30,
            )

            return True, "已回滚所有改动"

        except Exception as e:
            return False, str(e)

    @staticmethod
    def get_changed_files(workspace: str) -> List[str]:
        """获取改动的文件列表

        Args:
            workspace: 工作目录

        Returns:
            文件路径列表
        """
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )

            files = []
            for line in result.stdout.strip().split("\n"):
                if line:
                    # 格式：XY filename
                    files.append(line[3:].strip())

            return files

        except Exception:
            return []
