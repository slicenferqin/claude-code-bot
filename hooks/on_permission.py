#!/usr/bin/env python3
"""PermissionRequest Hook - 处理权限确认请求

当 Claude Code 需要执行敏感操作时触发。
将请求转发给 Bot 服务，等待用户通过 IM 确认。
"""

import json
import sys
import os
import uuid

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hooks.ipc_client import IPCClient


def format_tool_input(tool_name: str, tool_input: dict) -> str:
    """格式化工具输入为可读字符串"""
    if tool_name == "Bash":
        return tool_input.get("command", str(tool_input))
    elif tool_name == "Edit":
        file_path = tool_input.get("file_path", "")
        return f"Edit: {file_path}"
    elif tool_name == "Write":
        file_path = tool_input.get("file_path", "")
        return f"Write: {file_path}"
    elif tool_name == "Read":
        file_path = tool_input.get("file_path", "")
        return f"Read: {file_path}"
    else:
        # 其他工具，显示简化的 JSON
        try:
            return json.dumps(tool_input, ensure_ascii=False)[:200]
        except Exception:
            return str(tool_input)[:200]


def main():
    """主函数"""
    # 读取 Claude Code 传入的数据
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        # 格式错误，拒绝操作
        print(json.dumps({
            "decision": "deny",
            "reason": f"Invalid input: {e}"
        }))
        sys.exit(0)

    session_id = input_data.get("session_id", "")
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    # 格式化命令描述
    command_desc = format_tool_input(tool_name, tool_input)

    # 连接 Bot 服务
    client = IPCClient()
    if not client.connect():
        # Bot 未运行，拒绝操作以确保安全
        print(json.dumps({
            "decision": "deny",
            "reason": "Bot service not running"
        }))
        sys.exit(0)

    try:
        request_id = str(uuid.uuid4())

        # 1. 发送确认请求
        client.send("permission_request", {
            "request_id": request_id,
            "session_id": session_id,
            "tool_name": tool_name,
            "command": command_desc,
            "full_input": tool_input,
        })

        # 2. 轮询等待用户响应（最长 1 小时）
        response = client.poll_for_response(
            msg_type="get_permission_response",
            request_id=request_id,
            poll_interval=2.0,
            max_wait=3600  # 1 小时
        )

        if response:
            # 返回用户决策
            print(json.dumps({
                "decision": response.get("decision", "deny"),
                "reason": response.get("reason", "")
            }))
        else:
            # 超时，拒绝
            print(json.dumps({
                "decision": "deny",
                "reason": "Confirmation timeout (1 hour)"
            }))

    except Exception as e:
        print(json.dumps({
            "decision": "deny",
            "reason": f"Error: {e}"
        }))
    finally:
        client.close()


if __name__ == "__main__":
    main()
