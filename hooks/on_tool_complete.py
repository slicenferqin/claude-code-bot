#!/usr/bin/env python3
"""PostToolUse Hook - 工具执行完成后通知进度

当 Claude Code 执行完一个工具后触发。
将进度信息发送给 Bot 服务，以便推送给用户。
"""

import json
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hooks.ipc_client import IPCClient


def format_output_preview(tool_output: dict, max_length: int = 200) -> str:
    """格式化输出预览"""
    stdout = tool_output.get("stdout", "")
    stderr = tool_output.get("stderr", "")

    if stderr:
        preview = f"stderr: {stderr}"
    elif stdout:
        preview = stdout
    else:
        preview = "(no output)"

    if len(preview) > max_length:
        preview = preview[:max_length] + "..."

    return preview


def main():
    """主函数"""
    # 读取 Claude Code 传入的数据
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        # 如果没有输入或格式错误，静默退出
        sys.exit(0)

    session_id = input_data.get("session_id", "")
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})
    tool_output = input_data.get("tool_output", {})

    # 获取执行状态
    exit_code = tool_output.get("exit_code")
    if exit_code is not None:
        status = "success" if exit_code == 0 else "failed"
    else:
        status = "completed"

    # 格式化输出预览
    output_preview = format_output_preview(tool_output)

    # 连接 Bot 服务
    client = IPCClient()
    if not client.connect():
        # Bot 未运行，静默退出
        sys.exit(0)

    try:
        # 发送进度更新
        client.send("task_progress", {
            "session_id": session_id,
            "tool_name": tool_name,
            "status": status,
            "exit_code": exit_code,
            "output_preview": output_preview,
        })
    except Exception as e:
        print(f"[on_tool_complete] Error: {e}", file=sys.stderr)
    finally:
        client.close()


if __name__ == "__main__":
    main()
