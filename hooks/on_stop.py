#!/usr/bin/env python3
"""Stop Hook - 任务完成时通知 Bot 服务

当 Claude Code 任务结束时触发，通知 Bot 服务任务已完成。
"""

import json
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hooks.ipc_client import IPCClient


def main():
    """主函数"""
    # 读取 Claude Code 传入的数据
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        # 如果没有输入或格式错误，静默退出
        sys.exit(0)

    # Debug: 打印收到的数据
    with open("/tmp/claude-hook-debug.log", "a") as f:
        f.write(f"on_stop input: {json.dumps(input_data, indent=2)}\n\n")

    # session_id 可能在不同字段中
    session_id = (
        input_data.get("session_id") or
        input_data.get("sessionId") or
        os.environ.get("CLAUDE_SESSION_ID", "")
    )
    transcript_path = input_data.get("transcript_path", "")
    stop_hook_active = input_data.get("stop_hook_active", False)

    # 尝试获取会话摘要
    summary = ""
    files_changed = []

    # 如果有 transcript，尝试读取最后几行作为摘要
    if transcript_path and os.path.exists(transcript_path):
        try:
            with open(transcript_path, "r") as f:
                content = f.read()
                # 简单处理：取最后 500 字符
                summary = content[-500:] if len(content) > 500 else content
        except Exception:
            pass

    # 连接 Bot 服务
    client = IPCClient()
    if not client.connect():
        # Bot 未运行，静默退出
        sys.exit(0)

    try:
        # 发送任务完成通知
        client.send("task_complete", {
            "session_id": session_id,
            "transcript_path": transcript_path,
            "summary": summary,
            "files_changed": files_changed,
            "stop_hook_active": stop_hook_active,
        })
    except Exception as e:
        print(f"[on_stop] Error: {e}", file=sys.stderr)
    finally:
        client.close()


if __name__ == "__main__":
    main()
