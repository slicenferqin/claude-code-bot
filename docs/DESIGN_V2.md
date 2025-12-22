# 技术设计方案 V2：基于 Hook 的双向通信架构

## 1. 背景与目标

### 1.1 当前问题

1. **超时断联** - 任务超时后与 CLI 断联，无法获取后续状态
2. **单向通信** - Bot 只能发指令，无法获取实时进度
3. **阻塞式执行** - `subprocess.run()` 同步等待，占用资源
4. **无法确认** - CLI 需要用户确认时，无法传递到飞书

### 1.2 目标架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              本地机器                                    │
│                                                                         │
│   ┌─────────────┐          ┌─────────────┐          ┌─────────────┐    │
│   │  Claude CLI │ ──Hook──►│   Hook脚本   │◄──IPC──►│   Bot服务    │    │
│   │   (进程B)   │          │   (子进程)   │          │   (进程A)   │    │
│   └─────────────┘          └─────────────┘          └──────┬──────┘    │
│         ▲                                                   │           │
│         │ subprocess.Popen (不等待)                         │           │
│         │                                                   │           │
│   ┌─────┴───────────────────────────────────────────────────┘           │
│   │                                                                     │
│   │                         WebSocket 长连接                            │
│   │                              │                                      │
└───┼──────────────────────────────┼──────────────────────────────────────┘
    │                              │
    │                              ▼
    │                      ┌─────────────┐
    └──────────────────────│  飞书服务器  │◄─────────────► 你的手机
                           └─────────────┘
```

## 2. 核心组件设计

### 2.1 进程间通信 (IPC) 服务

使用 **Unix Domain Socket** 实现 Bot 服务与 Hook 脚本之间的双向通信。

**为什么选 Unix Socket：**
- 比 TCP 更轻量，无网络开销
- 天然的本地安全性
- Python 原生支持
- 支持双向通信

**Socket 路径：** `/tmp/claude-code-bot.sock`

**通信协议：** JSON 消息，换行符分隔

```python
# 消息格式
{
    "type": "event_type",
    "request_id": "uuid",      # 用于匹配请求和响应
    "payload": { ... }
}
```

### 2.2 消息类型定义

#### Hook → Bot 的消息

| type | 说明 | payload |
|------|------|---------|
| `task_progress` | 进度更新 | `{ "session_id", "tool_name", "status", "output" }` |
| `task_complete` | 任务完成 | `{ "session_id", "summary", "files_changed" }` |
| `permission_request` | 需要确认 | `{ "session_id", "request_id", "tool_name", "command", "reason" }` |
| `notification` | 通知消息 | `{ "session_id", "message" }` |

#### Bot → Hook 的消息

| type | 说明 | payload |
|------|------|---------|
| `permission_response` | 确认响应 | `{ "request_id", "decision": "approve/deny", "reason" }` |
| `cancel_task` | 取消任务 | `{ "session_id" }` |

### 2.3 IPC Server（Bot 端）

```python
# core/ipc_server.py

import asyncio
import json
import os
from typing import Callable, Dict, Any

class IPCServer:
    """进程间通信服务端"""

    SOCKET_PATH = "/tmp/claude-code-bot.sock"

    def __init__(self):
        self._handlers: Dict[str, Callable] = {}
        self._pending_requests: Dict[str, asyncio.Future] = {}
        self._server = None
        self._clients = set()

    def on(self, event_type: str, handler: Callable):
        """注册消息处理器"""
        self._handlers[event_type] = handler

    async def start(self):
        """启动 IPC 服务"""
        # 清理旧的 socket 文件
        if os.path.exists(self.SOCKET_PATH):
            os.unlink(self.SOCKET_PATH)

        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=self.SOCKET_PATH
        )
        os.chmod(self.SOCKET_PATH, 0o600)  # 仅当前用户可访问
        print(f"[IPC] Server listening on {self.SOCKET_PATH}")

    async def _handle_client(self, reader, writer):
        """处理客户端连接"""
        self._clients.add(writer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break

                message = json.loads(line.decode())
                await self._dispatch(message, writer)
        finally:
            self._clients.discard(writer)
            writer.close()

    async def _dispatch(self, message: dict, writer):
        """分发消息到处理器"""
        msg_type = message.get("type")
        request_id = message.get("request_id")

        # 如果是响应消息，解除等待
        if request_id and request_id in self._pending_requests:
            self._pending_requests[request_id].set_result(message)
            return

        # 否则调用处理器
        handler = self._handlers.get(msg_type)
        if handler:
            result = await handler(message.get("payload", {}))
            if result:
                response = {
                    "type": f"{msg_type}_response",
                    "request_id": request_id,
                    "payload": result
                }
                writer.write(json.dumps(response).encode() + b"\n")
                await writer.drain()

    async def request(self, request_id: str, timeout: float = 300) -> dict:
        """等待指定 request_id 的响应"""
        future = asyncio.get_event_loop().create_future()
        self._pending_requests[request_id] = future
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending_requests.pop(request_id, None)

    async def broadcast(self, message: dict):
        """广播消息给所有 Hook 客户端"""
        data = json.dumps(message).encode() + b"\n"
        for writer in self._clients:
            writer.write(data)
            await writer.drain()
```

### 2.4 IPC Client（Hook 脚本端）

```python
# hooks/ipc_client.py

import socket
import json
import sys
import os

class IPCClient:
    """进程间通信客户端（供 Hook 脚本使用）"""

    SOCKET_PATH = "/tmp/claude-code-bot.sock"

    def __init__(self):
        self._sock = None

    def connect(self) -> bool:
        """连接到 Bot 服务"""
        if not os.path.exists(self.SOCKET_PATH):
            return False

        try:
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.connect(self.SOCKET_PATH)
            return True
        except Exception as e:
            print(f"IPC connect failed: {e}", file=sys.stderr)
            return False

    def send(self, msg_type: str, payload: dict, request_id: str = None) -> dict:
        """发送消息并等待响应"""
        message = {
            "type": msg_type,
            "request_id": request_id,
            "payload": payload
        }

        self._sock.sendall(json.dumps(message).encode() + b"\n")

        # 如果有 request_id，等待响应
        if request_id:
            response = b""
            while b"\n" not in response:
                response += self._sock.recv(4096)
            return json.loads(response.decode())

        return None

    def close(self):
        if self._sock:
            self._sock.close()
```

### 2.5 Hook 脚本实现

#### Stop Hook（任务完成通知）

```python
#!/usr/bin/env python3
# hooks/on_stop.py

import json
import sys
sys.path.insert(0, "/path/to/claude-code-bot")

from hooks.ipc_client import IPCClient

def main():
    # 读取 Claude Code 传入的数据
    input_data = json.load(sys.stdin)

    session_id = input_data.get("session_id")
    transcript_path = input_data.get("transcript_path")

    # 连接 Bot 服务
    client = IPCClient()
    if not client.connect():
        # Bot 未运行，直接退出
        sys.exit(0)

    try:
        # 发送任务完成通知
        client.send("task_complete", {
            "session_id": session_id,
            "transcript_path": transcript_path,
            "summary": "任务已完成"
        })
    finally:
        client.close()

if __name__ == "__main__":
    main()
```

#### PermissionRequest Hook（确认请求）

```python
#!/usr/bin/env python3
# hooks/on_permission.py

import json
import sys
import uuid
sys.path.insert(0, "/path/to/claude-code-bot")

from hooks.ipc_client import IPCClient

def main():
    input_data = json.load(sys.stdin)

    session_id = input_data.get("session_id")
    tool_name = input_data.get("tool_name")
    tool_input = input_data.get("tool_input", {})

    client = IPCClient()
    if not client.connect():
        # Bot 未运行，阻止操作
        print(json.dumps({"decision": "deny", "reason": "Bot service not running"}))
        sys.exit(0)

    try:
        request_id = str(uuid.uuid4())

        # 发送确认请求并等待响应
        response = client.send("permission_request", {
            "session_id": session_id,
            "tool_name": tool_name,
            "command": tool_input.get("command", str(tool_input)),
            "reason": f"Claude wants to use {tool_name}"
        }, request_id=request_id)

        # 返回决策给 Claude Code
        decision = response.get("payload", {}).get("decision", "deny")
        print(json.dumps({
            "decision": decision,
            "reason": response.get("payload", {}).get("reason", "")
        }))

    finally:
        client.close()

if __name__ == "__main__":
    main()
```

#### PostToolUse Hook（进度追踪）

```python
#!/usr/bin/env python3
# hooks/on_tool_complete.py

import json
import sys
sys.path.insert(0, "/path/to/claude-code-bot")

from hooks.ipc_client import IPCClient

def main():
    input_data = json.load(sys.stdin)

    session_id = input_data.get("session_id")
    tool_name = input_data.get("tool_name")
    tool_output = input_data.get("tool_output", {})

    client = IPCClient()
    if not client.connect():
        sys.exit(0)

    try:
        client.send("task_progress", {
            "session_id": session_id,
            "tool_name": tool_name,
            "status": "completed",
            "exit_code": tool_output.get("exit_code"),
            "output_preview": (tool_output.get("stdout", ""))[:200]
        })
    finally:
        client.close()

if __name__ == "__main__":
    main()
```

## 3. Bot 服务改造

### 3.1 新的 Bot 架构

```python
# core/bot.py (重构)

import asyncio
from core.ipc_server import IPCServer
from core.task_manager import TaskManager

class Bot:
    def __init__(self, ...):
        # ... 原有初始化
        self._ipc_server = IPCServer()
        self._task_manager = TaskManager()

        # 注册 IPC 事件处理
        self._ipc_server.on("task_progress", self._on_task_progress)
        self._ipc_server.on("task_complete", self._on_task_complete)
        self._ipc_server.on("permission_request", self._on_permission_request)

    async def start(self):
        # 启动 IPC 服务
        await self._ipc_server.start()

        # 启动 IM 平台
        for platform in self._im_platforms:
            platform.start(self._on_message)

    async def _on_task_progress(self, payload: dict):
        """处理任务进度更新"""
        session_id = payload["session_id"]
        task = self._task_manager.get_task(session_id)

        if task:
            # 推送进度到飞书
            message = f"📍 进度更新\n工具: {payload['tool_name']}\n状态: {payload['status']}"
            await self._send_to_chat(task.chat_id, message)

    async def _on_task_complete(self, payload: dict):
        """处理任务完成"""
        session_id = payload["session_id"]
        task = self._task_manager.get_task(session_id)

        if task:
            # 推送完成通知到飞书（带卡片按钮）
            await self._send_completion_card(task.chat_id, payload)
            self._task_manager.complete_task(session_id)

    async def _on_permission_request(self, payload: dict) -> dict:
        """处理权限确认请求"""
        session_id = payload["session_id"]
        task = self._task_manager.get_task(session_id)

        if not task:
            return {"decision": "deny", "reason": "Task not found"}

        # 发送确认卡片到飞书
        request_id = payload["request_id"]
        await self._send_permission_card(
            task.chat_id,
            request_id,
            payload["tool_name"],
            payload["command"]
        )

        # 等待用户响应（通过飞书卡片回调）
        response = await self._ipc_server.request(request_id, timeout=300)
        return response.get("payload", {"decision": "deny"})
```

### 3.2 任务管理器

```python
# core/task_manager.py

from dataclasses import dataclass
from typing import Dict, Optional
from datetime import datetime
from enum import Enum
import subprocess
import threading

class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_CONFIRM = "waiting_confirm"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class Task:
    id: str
    chat_id: str
    session_id: str
    prompt: str
    status: TaskStatus
    process: Optional[subprocess.Popen]
    created_at: datetime
    updated_at: datetime

class TaskManager:
    """任务管理器"""

    def __init__(self):
        self._tasks: Dict[str, Task] = {}  # session_id -> Task
        self._lock = threading.Lock()

    def create_task(self, chat_id: str, session_id: str, prompt: str) -> Task:
        """创建任务"""
        with self._lock:
            task = Task(
                id=session_id,
                chat_id=chat_id,
                session_id=session_id,
                prompt=prompt,
                status=TaskStatus.PENDING,
                process=None,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            self._tasks[session_id] = task
            return task

    def start_task(self, session_id: str, process: subprocess.Popen):
        """标记任务开始执行"""
        with self._lock:
            if session_id in self._tasks:
                self._tasks[session_id].status = TaskStatus.RUNNING
                self._tasks[session_id].process = process
                self._tasks[session_id].updated_at = datetime.now()

    def get_task(self, session_id: str) -> Optional[Task]:
        """获取任务"""
        return self._tasks.get(session_id)

    def complete_task(self, session_id: str):
        """标记任务完成"""
        with self._lock:
            if session_id in self._tasks:
                self._tasks[session_id].status = TaskStatus.COMPLETED
                self._tasks[session_id].updated_at = datetime.now()

    def cancel_task(self, session_id: str) -> bool:
        """取消任务"""
        with self._lock:
            task = self._tasks.get(session_id)
            if task and task.process:
                task.process.terminate()
                task.status = TaskStatus.CANCELLED
                return True
            return False
```

### 3.3 CLI 工具改造

```python
# plugins/cli/claude_code.py (重构)

import subprocess
from interfaces.cli import CLITool, ExecutionResult, ExecutionStatus

class ClaudeCodeCLI(CLITool):

    def execute_async(
        self,
        prompt: str,
        session_id: str,
        workspace: str = ".",
    ) -> subprocess.Popen:
        """异步执行（不等待完成）"""

        cmd = [
            self.path,
            "--print",
            prompt,
            *self.default_args,
            "--resume", session_id,
        ]

        # 使用 Popen 启动，不阻塞
        process = subprocess.Popen(
            cmd,
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        return process

    def setup_hooks(self, project_dir: str):
        """配置 Claude Code hooks"""
        import json
        import os

        hooks_config = {
            "hooks": {
                "Stop": [{
                    "hooks": [{
                        "type": "command",
                        "command": f"python3 {project_dir}/hooks/on_stop.py"
                    }]
                }],
                "PermissionRequest": [{
                    "hooks": [{
                        "type": "command",
                        "command": f"python3 {project_dir}/hooks/on_permission.py"
                    }]
                }],
                "PostToolUse": [{
                    "hooks": [{
                        "type": "command",
                        "command": f"python3 {project_dir}/hooks/on_tool_complete.py"
                    }]
                }]
            }
        }

        settings_dir = os.path.join(project_dir, ".claude")
        os.makedirs(settings_dir, exist_ok=True)

        settings_path = os.path.join(settings_dir, "settings.json")
        with open(settings_path, "w") as f:
            json.dump(hooks_config, f, indent=2)
```

## 4. 飞书卡片交互

### 4.1 确认卡片模板

```json
{
  "config": {
    "wide_screen_mode": true
  },
  "header": {
    "title": {
      "tag": "plain_text",
      "content": "⚠️ 需要确认操作"
    },
    "template": "orange"
  },
  "elements": [
    {
      "tag": "div",
      "text": {
        "tag": "lark_md",
        "content": "**工具**: ${tool_name}\n**命令**: `${command}`"
      }
    },
    {
      "tag": "action",
      "actions": [
        {
          "tag": "button",
          "text": { "tag": "plain_text", "content": "✅ 批准" },
          "type": "primary",
          "value": { "action": "approve", "request_id": "${request_id}" }
        },
        {
          "tag": "button",
          "text": { "tag": "plain_text", "content": "❌ 拒绝" },
          "type": "danger",
          "value": { "action": "deny", "request_id": "${request_id}" }
        }
      ]
    }
  ]
}
```

### 4.2 完成通知卡片

```json
{
  "header": {
    "title": { "tag": "plain_text", "content": "✅ 任务完成" },
    "template": "green"
  },
  "elements": [
    {
      "tag": "div",
      "text": {
        "tag": "lark_md",
        "content": "${summary}"
      }
    },
    {
      "tag": "action",
      "actions": [
        {
          "tag": "button",
          "text": { "tag": "plain_text", "content": "📄 查看 Diff" },
          "type": "default",
          "value": { "action": "view_diff", "session_id": "${session_id}" }
        },
        {
          "tag": "button",
          "text": { "tag": "plain_text", "content": "✅ 提交代码" },
          "type": "primary",
          "value": { "action": "commit", "session_id": "${session_id}" }
        },
        {
          "tag": "button",
          "text": { "tag": "plain_text", "content": "🔄 继续修改" },
          "type": "default",
          "value": { "action": "continue", "session_id": "${session_id}" }
        }
      ]
    }
  ]
}
```

## 5. 新目录结构

```
claude-code-bot/
├── main.py
├── config.yaml
├── requirements.txt
│
├── core/
│   ├── __init__.py
│   ├── bot.py              # Bot 主逻辑（重构）
│   ├── session.py
│   ├── config.py
│   ├── registry.py
│   ├── ipc_server.py       # 新增：IPC 服务端
│   └── task_manager.py     # 新增：任务管理
│
├── interfaces/
│   ├── im.py
│   └── cli.py
│
├── plugins/
│   ├── im/
│   │   └── feishu.py       # 需增加卡片消息支持
│   └── cli/
│       └── claude_code.py  # 重构：异步执行
│
├── hooks/                  # 新增：Hook 脚本
│   ├── __init__.py
│   ├── ipc_client.py       # IPC 客户端
│   ├── on_stop.py          # Stop hook
│   ├── on_permission.py    # PermissionRequest hook
│   └── on_tool_complete.py # PostToolUse hook
│
└── utils/
    └── helpers.py
```

## 6. 实施计划

### Phase 1：基础通信（预计 2-3 小时）
1. 实现 IPC Server
2. 实现 IPC Client
3. 基础 Hook 脚本（on_stop）
4. 测试通信链路

### Phase 2：任务管理（预计 2 小时）
5. 实现 TaskManager
6. 改造 CLI 为异步执行
7. 进度推送功能

### Phase 3：确认流程（预计 2-3 小时）
8. PermissionRequest Hook
9. 飞书卡片消息发送
10. 卡片回调处理
11. 确认响应回传

### Phase 4：完善和测试（预计 1-2 小时）
12. 错误处理
13. 超时处理
14. 重连机制
15. 端到端测试

## 7. 风险与注意事项

1. **Hook 脚本路径** - 需要使用绝对路径，或通过环境变量配置
2. **Socket 权限** - 确保只有当前用户可访问
3. **进程清理** - Bot 退出时需清理 socket 文件和子进程
4. **Hook 超时** - Claude Code 默认 60 秒，确认流程需要更长时间
5. **并发任务** - 需要处理多个任务同时运行的情况

## 8. 飞书卡片回调方案

### 8.1 问题

飞书卡片按钮点击事件需要 **HTTP 回调接口**，无法通过 WebSocket 接收。

这意味着我们需要：
1. 一个公网可访问的 HTTP 服务
2. 或者使用内网穿透工具

### 8.2 方案对比

| 方案 | 优点 | 缺点 |
|------|------|------|
| **A. 云服务器部署 HTTP 服务** | 稳定可靠 | 需要服务器成本，架构变复杂 |
| **B. 内网穿透 (ngrok/frp)** | 本地开发方便 | 不稳定，地址会变 |
| **C. Cloudflare Tunnel** | 免费，相对稳定 | 需要域名 |
| **D. 放弃卡片，用文字命令** | 简单，无需公网 | 交互体验差 |

### 8.3 推荐方案：混合模式

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│  ┌─────────────┐     WebSocket      ┌─────────────┐                        │
│  │   飞书服务器  │ ◄───(消息接收)────► │             │                        │
│  │             │                    │             │                        │
│  │             │     HTTP POST      │   Bot 服务   │◄──IPC──► Hook 脚本     │
│  │             │ ───(卡片回调)─────► │             │                        │
│  └─────────────┘                    └──────┬──────┘                        │
│         ▲                                  │                               │
│         │                                  │                               │
│         │ 如果无公网，用文字命令替代         │                               │
│         │                                  ▼                               │
│         │                           本地 HTTP 服务                          │
│         │                           (端口 8080)                            │
│         │                                  │                               │
│         │                                  │ 内网穿透（可选）                │
│         │                                  ▼                               │
│         └──────────────────────────  公网地址（可选）                        │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**策略：**
1. 默认使用**文字命令**交互（无需公网）
2. 如果用户配置了公网地址，启用**卡片交互**增强体验

### 8.4 文字命令交互设计（无公网方案）

当卡片不可用时，用文字命令替代：

```
Bot: ⚠️ Claude 请求执行以下操作：

     工具: Bash
     命令: npm install && npm test

     请回复：
     - "ok" 或 "y" 批准
     - "no" 或 "n" 拒绝
     - "cancel" 取消整个任务

你: ok

Bot: ✅ 已批准，继续执行...
```

**完成通知：**
```
Bot: ✅ 任务完成

     修改了 2 个文件：
     - src/api/handler.py (+15, -3)
     - tests/test_handler.py (+25, -0)

     请回复：
     - "diff" 查看改动详情
     - "diff handler" 查看指定文件
     - "commit" 提交代码
     - "commit 修复登录bug" 提交并指定消息
     - "rollback" 撤销改动
     - "continue 继续优化" 继续修改

你: diff handler

Bot: 📄 src/api/handler.py 的改动：

     @@ -10,6 +10,18 @@
      def handle_request(req):
     +    # 新增输入验证
     +    if not validate(req):
     +        return error_response()
          ...

你: commit 修复登录验证bug

Bot: ✅ 已提交: abc1234
     要推送到远程吗？回复 "push" 确认
```

### 8.5 确认等待机制设计

**问题：** Hook 默认超时 60 秒，用户确认可能需要几分钟甚至几小时。

**解决方案：非阻塞确认队列**

```python
# 确认请求不阻塞 Hook，而是存入队列等待

class PermissionManager:
    """权限确认管理器"""

    def __init__(self):
        # request_id -> { session_id, tool_name, command, status, response }
        self._pending: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def create_request(self, request_id: str, session_id: str,
                       tool_name: str, command: str) -> None:
        """创建确认请求"""
        with self._lock:
            self._pending[request_id] = {
                "session_id": session_id,
                "tool_name": tool_name,
                "command": command,
                "status": "pending",
                "response": None,
                "created_at": datetime.now(),
            }

    def respond(self, request_id: str, decision: str, reason: str = "") -> bool:
        """响应确认请求"""
        with self._lock:
            if request_id not in self._pending:
                return False
            self._pending[request_id]["status"] = "responded"
            self._pending[request_id]["response"] = {
                "decision": decision,
                "reason": reason,
            }
            return True

    def get_response(self, request_id: str, timeout: float = 3600) -> Optional[dict]:
        """获取响应（轮询等待）"""
        start = time.time()
        while time.time() - start < timeout:
            with self._lock:
                req = self._pending.get(request_id)
                if req and req["status"] == "responded":
                    return req["response"]
            time.sleep(0.5)
        return None

    def get_pending_for_session(self, session_id: str) -> List[dict]:
        """获取某个会话的所有待确认请求"""
        with self._lock:
            return [
                {"request_id": rid, **req}
                for rid, req in self._pending.items()
                if req["session_id"] == session_id and req["status"] == "pending"
            ]
```

**Hook 脚本修改（支持长等待）：**

```python
#!/usr/bin/env python3
# hooks/on_permission.py

import json
import sys
import uuid
import time

# ... 省略导入

def main():
    input_data = json.load(sys.stdin)

    session_id = input_data.get("session_id")
    tool_name = input_data.get("tool_name")
    tool_input = input_data.get("tool_input", {})

    client = IPCClient()
    if not client.connect():
        print(json.dumps({"decision": "deny", "reason": "Bot not running"}))
        sys.exit(0)

    try:
        request_id = str(uuid.uuid4())

        # 1. 发送确认请求（不等待响应）
        client.send("permission_request", {
            "request_id": request_id,
            "session_id": session_id,
            "tool_name": tool_name,
            "command": tool_input.get("command", str(tool_input)),
        })

        # 2. 轮询等待响应（最长 1 小时）
        timeout = 3600  # 1 小时
        start = time.time()

        while time.time() - start < timeout:
            response = client.send("get_permission_response", {
                "request_id": request_id,
            })

            if response and response.get("payload", {}).get("status") == "responded":
                result = response["payload"]["response"]
                print(json.dumps(result))
                sys.exit(0)

            time.sleep(2)  # 每 2 秒轮询一次

        # 超时，拒绝
        print(json.dumps({"decision": "deny", "reason": "Confirmation timeout"}))

    finally:
        client.close()

if __name__ == "__main__":
    main()
```

## 9. 取消任务设计

### 9.1 取消场景

1. **用户主动取消** - 发送 "cancel" 命令
2. **超时自动取消** - 任务执行超过最大时间
3. **异常取消** - Bot 服务重启、网络断开等

### 9.2 取消流程

```
用户: cancel

Bot: 正在取消任务...

     ┌─────────────────────────────────────────┐
     │  1. 更新任务状态为 CANCELLING           │
     │  2. 发送 SIGTERM 给 Claude Code 进程    │
     │  3. 等待进程退出（最多 10 秒）           │
     │  4. 如果还未退出，发送 SIGKILL          │
     │  5. 清理相关资源                        │
     │  6. 更新任务状态为 CANCELLED            │
     └─────────────────────────────────────────┘

Bot: ✅ 任务已取消

     改动已撤销，工作目录已恢复。
```

### 9.3 取消实现

```python
# core/task_manager.py

import signal
import time

class TaskManager:
    # ... 其他方法

    def cancel_task(self, session_id: str, rollback: bool = True) -> dict:
        """取消任务

        Args:
            session_id: 会话 ID
            rollback: 是否回滚改动

        Returns:
            {"success": bool, "message": str}
        """
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

        # 终止进程
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
        if rollback:
            self._rollback_changes(session_id)

        # 更新最终状态
        with self._lock:
            task.status = TaskStatus.CANCELLED
            task.updated_at = datetime.now()

        return {"success": True, "message": "任务已取消"}

    def _rollback_changes(self, session_id: str):
        """回滚改动（使用 git）"""
        task = self._tasks.get(session_id)
        if not task:
            return

        try:
            # 使用 git checkout 撤销改动
            subprocess.run(
                ["git", "checkout", "."],
                cwd=task.workspace,
                capture_output=True,
                timeout=30,
            )
            # 清理新增的未跟踪文件
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=task.workspace,
                capture_output=True,
                timeout=30,
            )
        except Exception as e:
            print(f"[TaskManager] Rollback failed: {e}")
```

### 9.4 取消待确认的请求

当任务取消时，也需要取消所有待确认的权限请求：

```python
class PermissionManager:
    # ... 其他方法

    def cancel_all_for_session(self, session_id: str) -> int:
        """取消某个会话的所有待确认请求"""
        count = 0
        with self._lock:
            for rid, req in self._pending.items():
                if req["session_id"] == session_id and req["status"] == "pending":
                    req["status"] = "cancelled"
                    req["response"] = {
                        "decision": "deny",
                        "reason": "Task cancelled by user",
                    }
                    count += 1
        return count
```

## 10. 完整交互流程示例

### 10.1 正常执行流程

```
你: claude code 修复 src/api/handler.py 中的空指针异常

Bot: 🚀 任务已启动
     Session: abc123
     正在分析问题...

--- 30 秒后 ---

Bot: 📍 进度更新
     正在读取文件 src/api/handler.py...

--- 1 分钟后 ---

Bot: ⚠️ Claude 请求执行：

     工具: Edit
     文件: src/api/handler.py
     改动: 添加空值检查

     回复 "ok" 批准，"no" 拒绝

你: ok

Bot: ✅ 已批准，继续执行...

--- 30 秒后 ---

Bot: 📍 进度更新
     正在运行测试...

--- 1 分钟后 ---

Bot: ✅ 任务完成

     修改了 1 个文件：
     - src/api/handler.py (+5, -1)

     测试结果：15 passed, 0 failed

     回复：
     - "diff" 查看改动
     - "commit 消息" 提交
     - "continue 指令" 继续修改

你: diff

Bot: 📄 src/api/handler.py

     @@ -25,7 +25,11 @@
      def process_request(data):
     -    result = data.get("value").process()
     +    value = data.get("value")
     +    if value is None:
     +        raise ValueError("value is required")
     +    result = value.process()
          return result

你: commit 修复空指针异常

Bot: ✅ 已提交: def4567
     "修复空指针异常"

     回复 "push" 推送到远程
```

### 10.2 取消任务流程

```
你: claude code 重构整个认证模块

Bot: 🚀 任务已启动
     Session: xyz789
     正在分析代码结构...

--- 2 分钟后 ---

Bot: 📍 进度更新
     正在修改 src/auth/login.py...

你: cancel

Bot: ⏹️ 正在取消任务...

     - 终止 Claude 进程
     - 撤销文件改动
     - 清理临时文件

Bot: ✅ 任务已取消

     所有改动已撤销。
```

### 10.3 长时间等待确认

```
Bot: ⚠️ Claude 请求执行：

     工具: Bash
     命令: rm -rf node_modules && npm install

     回复 "ok" 批准，"no" 拒绝

--- 你去开会了，30 分钟后 ---

你: ok

Bot: ✅ 已批准，继续执行...

--- 继续执行 ---
```

## 11. 新目录结构（更新）

```
claude-code-bot/
├── main.py
├── config.yaml
├── requirements.txt
│
├── core/
│   ├── __init__.py
│   ├── bot.py                  # Bot 主逻辑
│   ├── session.py              # 会话管理
│   ├── config.py               # 配置加载
│   ├── registry.py             # 插件注册
│   ├── ipc_server.py           # IPC 服务端
│   ├── task_manager.py         # 任务管理
│   └── permission_manager.py   # 权限确认管理（新增）
│
├── interfaces/
│   ├── im.py
│   └── cli.py
│
├── plugins/
│   ├── im/
│   │   └── feishu.py           # 飞书插件（增加命令解析）
│   └── cli/
│       └── claude_code.py      # Claude Code 插件
│
├── hooks/
│   ├── __init__.py
│   ├── ipc_client.py           # IPC 客户端
│   ├── on_stop.py              # Stop hook
│   ├── on_permission.py        # PermissionRequest hook
│   └── on_tool_complete.py     # PostToolUse hook
│
├── server/                     # 新增：HTTP 服务（可选）
│   ├── __init__.py
│   └── callback.py             # 飞书卡片回调处理
│
└── docs/
    └── DESIGN_V2.md
```

## 12. 修订后的实施计划

### Phase 1：IPC 通信基础（2-3 小时）
1. 实现 IPC Server
2. 实现 IPC Client
3. 基础 Hook 脚本（on_stop）
4. 测试通信链路

### Phase 2：任务管理（2 小时）
5. 实现 TaskManager
6. 改造 CLI 为异步执行
7. 任务取消功能
8. 进度推送功能

### Phase 3：权限确认（3-4 小时）
9. 实现 PermissionManager
10. PermissionRequest Hook（轮询模式）
11. 文字命令解析器（ok/no/cancel 等）
12. 确认响应回传

### Phase 4：增强交互（2-3 小时）
13. diff 查看命令
14. commit 命令
15. continue 命令
16. rollback 命令

### Phase 5：可选功能（2-3 小时）
17. HTTP 回调服务（卡片交互）
18. 内网穿透集成
19. 飞书卡片模板

### Phase 6：测试和文档（1-2 小时）
20. 端到端测试
21. 错误处理完善
22. 更新 README

---

**总计预估：12-17 小时**

## 13. 配置文件更新

```yaml
# config.yaml

bot:
  trigger_keyword: "claude code"
  default_timeout: 180
  max_output_length: 3000
  workspace: "."

  # 新增：确认相关配置
  permission:
    timeout: 3600           # 确认超时时间（秒），默认 1 小时
    poll_interval: 2        # 轮询间隔（秒）

  # 新增：任务相关配置
  task:
    max_concurrent: 3       # 最大并发任务数
    auto_rollback: true     # 取消时自动回滚

im:
  feishu:
    enabled: true
    app_id: ""
    app_secret: ""

    # 新增：HTTP 回调配置（可选）
    callback:
      enabled: false
      host: "0.0.0.0"
      port: 8080
      public_url: ""        # 公网地址，如 https://xxx.ngrok.io

cli:
  active: claude_code
  claude_code:
    path: /opt/homebrew/bin/claude
    default_args:
      - "--dangerously-skip-permissions"

# 新增：Hook 配置
hooks:
  project_dir: ""           # 留空则使用当前目录
  auto_setup: true          # 是否自动配置 .claude/settings.json
```

---

三酒，这个版本补充了：

1. **飞书卡片回调方案** - 混合模式，默认用文字命令，可选卡片
2. **文字命令交互设计** - 详细的命令和回复格式
3. **长时间确认等待机制** - 轮询模式，支持 1 小时等待
4. **取消任务完整设计** - 进程终止、回滚、清理
5. **完整交互流程示例** - 正常流程、取消流程、长等待
6. **更新的实施计划** - 分 6 个阶段，总计 12-17 小时

你看看还有什么需要补充的？
