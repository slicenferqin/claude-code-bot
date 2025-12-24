# 技术设计方案 V3：基于 Claude Agent SDK 的权限确认

> **状态**: 方案确定，准备实施
> **更新日期**: 2024-12-24

## 0. 研究结论

### 关键发现

1. **Stream-JSON 输入格式已确认**：
   ```json
   {"type": "user", "message": {"role": "user", "content": "your message"}}
   ```

2. **支持多轮对话** - 可以持续通过 stdin 发送多条消息

3. **权限在 --print 模式下自动拒绝** - 无法通过 stdin 发送权限批准

4. **推荐方案**：使用 `--allowedTools` 预先授权特定工具

---

## 1. 背景与问题

### 1.1 V2 方案的问题

V2 设计基于 Claude Code Hooks 实现双向通信，但在测试中发现：

**`--print` 模式下 Hooks 不触发**

这是 Claude Code CLI 的设计限制：
- `--print` 模式是非交互式的，为了快速执行，跳过了 Hook 机制
- Hooks 只在交互式终端模式下工作

### 1.2 当前状态

我们已经实现了 `--output-format stream-json` 来读取 Claude 的输出：
- ✅ 可以实时获取 Claude 的响应
- ✅ 可以看到进度更新
- ❌ 无法发送权限确认给正在等待的 Claude 进程

### 1.3 核心问题

当 Claude 请求权限时：
```
Claude: 我需要你的批准来搜索文件系统。请回复 "ok" 来批准。
```

用户在飞书回复 "ok"，但这个消息无法传递给**正在等待的 Claude 进程**，因为：
1. Bot 创建了一个新的消息转发给 Claude
2. 原来那个等待权限的 Claude 进程并没有收到 stdin 输入

## 2. 解决方案：Stream-JSON 双向通信

### 2.1 发现

Claude CLI 支持 `--input-format stream-json` 参数：

```bash
claude --print "your prompt" \
    --output-format stream-json \
    --input-format stream-json
```

这意味着可以：
- **stdout**: 实时读取 JSON 输出
- **stdin**: 发送 JSON 消息给 Claude

### 2.2 关键：保持 stdin 打开

```python
process = subprocess.Popen(
    cmd,
    stdin=subprocess.PIPE,   # 关键：保持 stdin 打开
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)

# 可以随时写入 stdin
process.stdin.write('{"type": "user_input", "content": "ok"}\n')
process.stdin.flush()
```

## 3. 新架构设计

### 3.1 架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              本地机器                                    │
│                                                                         │
│   ┌─────────────────────────────────────────────────────────────────┐  │
│   │                       Bot 服务 (main.py)                         │  │
│   │                                                                  │  │
│   │   ┌──────────────┐     ┌──────────────┐     ┌──────────────┐   │  │
│   │   │ IM 消息处理   │────►│  任务管理器   │────►│  CLI 管理器   │   │  │
│   │   └──────────────┘     └──────────────┘     └───────┬──────┘   │  │
│   │          ▲                    │                      │          │  │
│   │          │                    │                      ▼          │  │
│   │          │              ┌─────┴─────┐     ┌──────────────────┐  │  │
│   │          │              │ 权限管理器 │     │ Claude 进程管理  │  │  │
│   │          │              └───────────┘     │                  │  │  │
│   │          │                    ▲           │  stdin ──────►   │  │  │
│   │          │                    │           │  stdout ◄────    │  │  │
│   │          │                    └───────────│  (stream-json)   │  │  │
│   │          │                                └──────────────────┘  │  │
│   └──────────┼──────────────────────────────────────────────────────┘  │
│              │                                                          │
│              │ WebSocket 长连接                                          │
│              ▼                                                          │
└──────────────┼──────────────────────────────────────────────────────────┘
               │
               ▼
        ┌─────────────┐
        │  飞书服务器  │◄─────────────► 你的手机
        └─────────────┘
```

### 3.2 进程管理

```python
class ProcessHandle:
    """Claude 进程句柄"""

    def __init__(self, process: subprocess.Popen, session_id: str):
        self.process = process
        self.session_id = session_id
        self.stdin = process.stdin
        self.stdout = process.stdout
        self.stderr = process.stderr

        # 状态
        self.waiting_permission = False
        self.permission_request = None

    def send_input(self, message: dict) -> None:
        """发送消息到 Claude stdin"""
        if self.stdin and not self.stdin.closed:
            json_str = json.dumps(message) + "\n"
            self.stdin.write(json_str)
            self.stdin.flush()

    def send_permission_response(self, approve: bool) -> None:
        """发送权限确认响应"""
        # 需要研究 stream-json 的输入格式
        # 可能是类似：{"type": "permission_response", "approve": true}
        self.send_input({
            "type": "permission_response",
            "approve": approve
        })
```

### 3.3 任务与进程映射

```python
class ProcessManager:
    """进程管理器 - 管理所有活动的 Claude 进程"""

    def __init__(self):
        self._processes: Dict[str, ProcessHandle] = {}  # session_id -> ProcessHandle
        self._lock = threading.Lock()

    def create_process(self, session_id: str, cmd: List[str], workspace: str) -> ProcessHandle:
        """创建并管理 Claude 进程"""
        process = subprocess.Popen(
            cmd,
            cwd=workspace,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        handle = ProcessHandle(process, session_id)

        with self._lock:
            self._processes[session_id] = handle

        return handle

    def get_process(self, session_id: str) -> Optional[ProcessHandle]:
        """获取进程句柄"""
        return self._processes.get(session_id)

    def send_permission(self, session_id: str, approve: bool) -> bool:
        """发送权限确认到指定会话的 Claude 进程"""
        handle = self.get_process(session_id)
        if handle and handle.waiting_permission:
            handle.send_permission_response(approve)
            handle.waiting_permission = False
            return True
        return False
```

## 4. 待研究的问题

### 4.1 Stream-JSON 输入格式

需要确认 `--input-format stream-json` 的具体格式：

```bash
# 测试命令
echo '{"type": "test"}' | claude --print "hello" --input-format stream-json --output-format stream-json
```

可能的输入格式：
1. 用户消息：`{"type": "user", "content": "ok"}`
2. 权限响应：`{"type": "permission", "approve": true}`
3. 或者其他格式...

### 4.2 权限请求的 JSON 格式

需要确认 stream-json 输出中权限请求的格式：

```json
// 可能的格式
{
  "type": "permission_request",
  "tool": "Bash",
  "command": "mdfind ...",
  "request_id": "..."
}
```

### 4.3 进程生命周期

- Claude 在等待权限时是否阻塞？
- stdin 关闭后进程是否终止？
- 如何优雅地取消正在执行的任务？

## 5. 实现方案

### 5.1 方案 A：Stream-JSON 双向通信

**优点：**
- 不依赖 Hooks
- 实时双向通信
- 单进程管理

**缺点：**
- 需要研究 stream-json 的输入格式
- 可能需要 Claude Code 支持（未必支持通过 stdin 发送权限响应）

### 5.2 方案 B：混合模式（推荐）

如果 stream-json 不支持权限响应输入，可以：

1. **首次执行**：使用 `--dangerously-skip-permissions` 快速完成
2. **需要权限时**：
   - 检测到权限请求
   - 终止当前进程
   - 显示确认消息给用户
   - 用户确认后，使用 `--allowedTools "Bash(specific_command)"` 重新执行

```python
def handle_permission_request(self, session_id: str, tool_name: str, command: str):
    """处理权限请求"""
    task = self._task_manager.get_task(session_id)

    # 发送确认请求到飞书
    self._send_permission_message(task.chat_id, tool_name, command)

    # 存储待确认信息
    self._pending_permissions[session_id] = {
        "tool_name": tool_name,
        "command": command,
        "original_prompt": task.prompt,
    }

def on_permission_approved(self, session_id: str):
    """用户批准后"""
    pending = self._pending_permissions.get(session_id)
    if not pending:
        return

    # 使用 --allowedTools 重新执行
    allowed_tool = f"{pending['tool_name']}({pending['command']})"
    self._restart_with_allowed_tool(session_id, pending['original_prompt'], allowed_tool)
```

### 5.3 方案 C：使用非 --print 模式

启动 Claude Code 时不使用 `--print`，而是交互模式：

```python
process = subprocess.Popen(
    [
        "claude",
        "--output-format", "stream-json",
        "--session-id", session_id,
    ],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)

# 发送初始 prompt
process.stdin.write(f"{prompt}\n")
process.stdin.flush()

# 后续可以继续发送消息
process.stdin.write("ok\n")  # 权限确认
```

**优点：**
- Hooks 可能会触发（需验证）
- 支持多轮对话

**缺点：**
- 需要管理交互式会话
- 复杂度更高

## 6. 下一步行动

### 6.1 研究阶段（1-2 小时）

1. **测试 stream-json 输入格式**
   ```bash
   # 测试双向通信
   claude --print "test" --input-format stream-json --output-format stream-json
   ```

2. **测试非 --print 模式的 Hook 触发**
   ```bash
   # 交互模式是否触发 hooks
   echo "hello" | claude --output-format stream-json
   ```

3. **查看 Claude Code 源码或文档**
   - 确认 stream-json 输入格式
   - 确认权限请求的输出格式

### 6.2 实现阶段

根据研究结果选择方案并实现。

### 6.3 建议的优先级

1. **立即可用**：先启用 `--dangerously-skip-permissions`
2. **短期目标**：研究并实现 stream-json 双向通信
3. **长期目标**：完善权限确认流程，支持细粒度控制

## 7. 临时解决方案

在完成研究之前，先启用 `--dangerously-skip-permissions` 让系统跑起来：

```yaml
# config.yaml
cli:
  claude_code:
    default_args:
      - "--dangerously-skip-permissions"
```

这样 Claude 会自动执行所有操作，无需权限确认。

**风险：**
- Claude 可能执行危险操作
- 建议在测试环境或沙箱中使用

**缓解措施：**
- 设置安全的 workspace
- 使用 git 保护代码（可以 rollback）
- 限制 Claude 的 allowedTools

---

## 8. 最终方案：双阶段执行 + 动态授权

基于研究结果，推荐以下方案：

### 8.1 核心思路

```
用户发送任务
    ↓
第一阶段：探索模式（只读）
    - 使用 --allowedTools "Read Glob Grep"
    - Claude 分析需求，确定需要哪些操作
    - 输出需要执行的敏感操作列表
    ↓
Bot 发送确认消息给用户
    - "Claude 需要执行以下操作：..."
    - 用户回复 "ok" 确认
    ↓
第二阶段：执行模式（授权后）
    - 使用 --allowedTools "Bash Edit Write ..."
    - 或 --dangerously-skip-permissions
    - Claude 执行实际操作
    ↓
返回结果给用户
```

### 8.2 实现细节

```python
class ClaudeCodeCLI:

    def execute_with_permission(
        self,
        prompt: str,
        session_id: str,
        workspace: str,
        on_permission_request: Callable,  # 回调：请求用户确认
    ):
        """带权限确认的执行流程"""

        # 第一阶段：探索（只读）
        explore_result = self._execute_explore(prompt, session_id, workspace)

        # 检查是否有权限被拒绝
        if explore_result.get("permission_denials"):
            # 提取被拒绝的操作
            denied_tools = explore_result["permission_denials"]

            # 请求用户确认
            approved = await on_permission_request(denied_tools)

            if approved:
                # 第二阶段：执行（授权后）
                allowed_tools = self._build_allowed_tools(denied_tools)
                return self._execute_with_allowed(
                    prompt, session_id, workspace, allowed_tools
                )
            else:
                return {"status": "denied", "reason": "用户拒绝了操作"}

        return explore_result

    def _execute_explore(self, prompt, session_id, workspace):
        """探索模式：只允许只读操作"""
        cmd = [
            self.path,
            "--print", prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--allowedTools", "Read,Glob,Grep,Task,WebSearch,WebFetch",
            "--session-id", session_id,
        ]
        # ... 执行并返回结果

    def _execute_with_allowed(self, prompt, session_id, workspace, allowed_tools):
        """执行模式：使用授权的工具"""
        cmd = [
            self.path,
            "--print", prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--allowedTools", allowed_tools,
            "--resume", session_id,
        ]
        # ... 执行并返回结果
```

### 8.3 用户交互流程

```
用户: 帮我修复 src/api/handler.py 中的空指针问题

Bot: 🤔 正在分析...

Bot: ⚠️ Claude 需要执行以下操作：

     1. 编辑文件: src/api/handler.py
     2. 运行命令: npm test

     回复 "ok" 确认，"no" 取消

用户: ok

Bot: ✅ 正在执行...

Bot: ✅ 完成！
     修改了 1 个文件：
     - src/api/handler.py (+5, -1)

     测试结果：15 passed, 0 failed
```

### 8.4 优势

1. **安全可控** - 用户明确知道将要执行什么操作
2. **灵活授权** - 可以针对特定操作授权
3. **实现简单** - 不需要 Hook 机制
4. **体验良好** - 减少不必要的确认步骤

### 8.5 实施步骤

1. 修改 `claude_code.py`：
   - 添加 `--allowedTools` 支持
   - 实现双阶段执行逻辑
   - 解析 `permission_denials` 字段

2. 修改 `bot.py`：
   - 添加权限确认消息发送
   - 处理用户的确认响应
   - 管理任务状态

3. 配置支持：
   - 配置默认允许的工具
   - 配置是否启用权限确认

---

## 9. 总结

| 方案 | 复杂度 | 可行性 | 推荐度 |
|------|--------|--------|--------|
| A. Stream-JSON 双向（权限响应） | 中 | ❌ 不支持 | - |
| B. 双阶段执行 + 动态授权 | 中 | ✅ 高 | ⭐⭐⭐⭐⭐ |
| C. 混合模式（重启） | 低 | ✅ 高 | ⭐⭐⭐⭐ |
| D. 临时方案（跳过权限） | 低 | ✅ 高 | ⭐⭐⭐ |

**最终方案：B - 双阶段执行 + 动态授权**

这个方案：
- 利用了 `--allowedTools` 参数
- 利用了 `permission_denials` 输出字段
- 不依赖 Hook 机制
- 提供了良好的用户体验

**建议路径：**
1. 先用方案 D（`--dangerously-skip-permissions`）让系统快速跑起来
2. 实现方案 B（双阶段执行）提供安全的权限控制

---

## 10. 最终方案：Claude Agent SDK 集成

基于 Claude Code 官方推荐，最佳方案是使用 **Claude Agent SDK**。

### 10.1 SDK 核心能力

Claude Agent SDK (`claude-code-sdk`) 提供了 Python 接口来调用 Claude Code：

```python
from claude_code_sdk import query, ClaudeCodeOptions

async for message in query(
    prompt="你的任务",
    options=ClaudeCodeOptions(
        allowed_tools=["Read", "Glob", "Grep"],
        permission_mode="default",
    )
):
    print(message)
```

**关键特性：**
- ✅ 异步流式输出
- ✅ 自定义工具权限控制 (`can_use_tool` 回调)
- ✅ 会话管理 (`session_id`)
- ✅ 工作目录控制 (`cwd`)

### 10.2 权限控制机制

SDK 提供 `can_use_tool` 回调，在工具执行**之前**调用：

```python
async def can_use_tool(tool: str, input: dict) -> bool:
    """
    自定义权限检查函数

    Args:
        tool: 工具名称，如 "Bash", "Edit", "Write"
        input: 工具参数，如 {"command": "rm -rf /", "dangerouslyDisableSandbox": True}

    Returns:
        True 允许执行，False 拒绝执行
    """
    if tool == "Bash" and input.get("dangerouslyDisableSandbox"):
        # 危险操作，需要用户确认
        approved = await request_user_approval(tool, input)
        return approved
    return True
```

### 10.3 新架构设计

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              本地机器                                    │
│                                                                         │
│   ┌─────────────────────────────────────────────────────────────────┐  │
│   │                       Bot 服务 (main.py)                         │  │
│   │                                                                  │  │
│   │   ┌──────────────┐     ┌──────────────┐     ┌──────────────┐   │  │
│   │   │ IM 消息处理   │────►│  任务管理器   │────►│  SDK 管理器   │   │  │
│   │   └──────────────┘     └──────────────┘     └───────┬──────┘   │  │
│   │          ▲                    │                      │          │  │
│   │          │                    │                      ▼          │  │
│   │          │              ┌─────┴─────┐     ┌──────────────────┐  │  │
│   │          │              │ 权限管理器 │◄────│ Claude Agent SDK │  │  │
│   │          │              │            │     │                  │  │  │
│   │          │              │ 等待用户   │     │  can_use_tool()  │  │  │
│   │          │              │ 确认中...  │     │  callback        │  │  │
│   │          │              └───────────┘     └──────────────────┘  │  │
│   └──────────┼──────────────────────────────────────────────────────┘  │
│              │                                                          │
│              │ WebSocket 长连接                                          │
│              ▼                                                          │
└──────────────┼──────────────────────────────────────────────────────────┘
               │
               ▼
        ┌─────────────┐
        │  飞书服务器  │◄─────────────► 你的手机
        └─────────────┘
```

### 10.4 实现细节

#### 10.4.1 新的 CLI 插件：claude_code_sdk.py

```python
"""Claude Code SDK 插件"""

import asyncio
from typing import Optional, Callable, Dict, Any, AsyncIterator

from claude_code_sdk import query, ClaudeCodeOptions, Message
from core.registry import PluginRegistry
from interfaces.cli import CLITool, ExecutionResult, ExecutionStatus


@PluginRegistry.register_cli("claude_code_sdk")
class ClaudeCodeSDK(CLITool):
    """基于 Claude Agent SDK 的 CLI 实现

    相比子进程方式，SDK 提供：
    - 更好的权限控制（can_use_tool 回调）
    - 更简洁的 API
    - 更好的错误处理
    """

    def __init__(
        self,
        allowed_tools: Optional[list] = None,
        permission_mode: str = "default",
    ):
        """初始化 SDK

        Args:
            allowed_tools: 允许的工具列表，None 表示使用默认
            permission_mode: 权限模式 ("default", "acceptEdits", "bypassPermissions")
        """
        self.allowed_tools = allowed_tools
        self.permission_mode = permission_mode

        # 权限请求回调
        self._permission_callback: Optional[Callable] = None
        self._progress_callback: Optional[Callable] = None
        self._complete_callback: Optional[Callable] = None

    def set_permission_callback(
        self,
        callback: Callable[[str, Dict[str, Any]], asyncio.Future[bool]]
    ):
        """设置权限请求回调

        当 Claude 需要执行敏感操作时，会调用此回调等待用户确认。

        Args:
            callback: 异步回调函数，参数为 (tool_name, tool_input)，返回是否批准
        """
        self._permission_callback = callback

    def set_callbacks(
        self,
        on_progress: Optional[Callable] = None,
        on_complete: Optional[Callable] = None,
    ):
        """设置进度和完成回调"""
        self._progress_callback = on_progress
        self._complete_callback = on_complete

    @property
    def name(self) -> str:
        return "claude_code_sdk"

    async def _can_use_tool(self, tool: str, input: dict) -> bool:
        """权限检查回调

        危险操作会触发此回调，等待用户确认。
        """
        # 定义需要确认的危险操作
        dangerous_tools = ["Bash", "Write", "Edit", "NotebookEdit"]

        if tool not in dangerous_tools:
            return True  # 只读操作自动允许

        # 特殊处理：Bash 命令分析
        if tool == "Bash":
            command = input.get("command", "")
            # 安全命令白名单
            safe_commands = ["ls", "pwd", "cat", "head", "tail", "grep", "find", "echo"]
            if any(command.strip().startswith(cmd) for cmd in safe_commands):
                return True

        # 需要用户确认
        if self._permission_callback:
            try:
                return await self._permission_callback(tool, input)
            except asyncio.TimeoutError:
                return False  # 超时拒绝

        return False  # 无回调时默认拒绝

    async def execute_async(
        self,
        prompt: str,
        session_id: str,
        workspace: str = ".",
    ) -> AsyncIterator[Message]:
        """异步执行任务

        Args:
            prompt: 用户提示词
            session_id: 会话 ID
            workspace: 工作目录

        Yields:
            Message: Claude 的响应消息
        """
        options = ClaudeCodeOptions(
            cwd=workspace,
            session_id=session_id,
            allowed_tools=self.allowed_tools,
            permission_mode=self.permission_mode,
        )

        # 添加权限检查回调
        if self._permission_callback:
            options.can_use_tool = self._can_use_tool

        try:
            async for message in query(prompt=prompt, options=options):
                # 处理不同类型的消息
                if hasattr(message, 'type'):
                    if message.type == 'progress':
                        if self._progress_callback:
                            self._progress_callback({
                                'session_id': session_id,
                                'tool_name': getattr(message, 'tool', ''),
                                'status': 'running',
                            })
                    elif message.type == 'result':
                        if self._complete_callback:
                            self._complete_callback({
                                'session_id': session_id,
                                'status': 'completed',
                                'summary': getattr(message, 'result', ''),
                            })

                yield message

        except Exception as e:
            if self._complete_callback:
                self._complete_callback({
                    'session_id': session_id,
                    'status': 'failed',
                    'summary': str(e),
                })
            raise

    def is_available(self) -> bool:
        """检查 SDK 是否可用"""
        try:
            import claude_code_sdk
            return True
        except ImportError:
            return False
```

#### 10.4.2 权限管理器集成

```python
# core/permission_manager.py 新增方法

class PermissionManager:
    """权限管理器 - 处理 SDK 的权限请求"""

    async def request_approval(
        self,
        session_id: str,
        tool_name: str,
        tool_input: Dict[str, Any],
        chat_id: str,
        platform: IMPlatform,
    ) -> bool:
        """请求用户批准

        1. 发送确认消息到 IM
        2. 等待用户响应
        3. 返回批准结果

        Args:
            session_id: 会话 ID
            tool_name: 工具名称
            tool_input: 工具参数
            chat_id: 聊天 ID
            platform: IM 平台

        Returns:
            用户是否批准
        """
        # 创建等待事件
        request_id = str(uuid.uuid4())
        approval_future = asyncio.get_event_loop().create_future()

        self._pending_approvals[request_id] = {
            'session_id': session_id,
            'future': approval_future,
            'created_at': datetime.now(),
        }

        # 格式化确认消息
        msg = self._format_approval_message(tool_name, tool_input)
        platform.send(chat_id, Reply(content=msg))

        try:
            # 等待用户响应（带超时）
            result = await asyncio.wait_for(
                approval_future,
                timeout=self.default_timeout
            )
            return result
        except asyncio.TimeoutError:
            platform.send(chat_id, Reply(content="⏰ 权限确认超时，已自动拒绝"))
            return False
        finally:
            self._pending_approvals.pop(request_id, None)

    def _format_approval_message(self, tool_name: str, tool_input: dict) -> str:
        """格式化权限确认消息"""
        if tool_name == "Bash":
            command = tool_input.get("command", "")
            return (
                f"⚠️ Claude 需要执行命令：\n\n"
                f"```bash\n{command}\n```\n\n"
                f"回复 \"ok\" 确认，\"no\" 拒绝"
            )
        elif tool_name == "Edit":
            file_path = tool_input.get("file_path", "")
            return (
                f"⚠️ Claude 需要编辑文件：\n\n"
                f"📄 {file_path}\n\n"
                f"回复 \"ok\" 确认，\"no\" 拒绝"
            )
        elif tool_name == "Write":
            file_path = tool_input.get("file_path", "")
            return (
                f"⚠️ Claude 需要创建文件：\n\n"
                f"📄 {file_path}\n\n"
                f"回复 \"ok\" 确认，\"no\" 拒绝"
            )
        else:
            return (
                f"⚠️ Claude 需要使用工具：{tool_name}\n\n"
                f"参数：{json.dumps(tool_input, indent=2)}\n\n"
                f"回复 \"ok\" 确认，\"no\" 拒绝"
            )
```

#### 10.4.3 Bot 集成

```python
# core/bot.py 修改

class Bot:
    def __init__(self, ...):
        # ... 现有代码 ...

        # 使用 SDK 时的权限回调
        if hasattr(self.cli_tool, 'set_permission_callback'):
            self.cli_tool.set_permission_callback(self._on_permission_request_sdk)

    async def _on_permission_request_sdk(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
    ) -> bool:
        """处理 SDK 的权限请求回调"""
        # 获取当前任务
        # 注意：需要通过某种方式关联 session_id

        # 发送确认消息并等待用户响应
        return await self._permission_manager.request_approval(
            session_id=...,
            tool_name=tool_name,
            tool_input=tool_input,
            chat_id=...,
            platform=...,
        )
```

### 10.5 用户交互流程

```
用户: 帮我在 src/api 目录下创建一个新的 auth.py 文件

Bot: 🤔 思考中...

Bot: ⚠️ Claude 需要创建文件：

     📄 src/api/auth.py

     回复 "ok" 确认，"no" 拒绝

用户: ok

Bot: ✅ 正在执行...

Bot: ⚠️ Claude 需要执行命令：

     ```bash
     python -m pytest tests/
     ```

     回复 "ok" 确认，"no" 拒绝

用户: ok

Bot: ✅ Claude:

     已创建 src/api/auth.py，包含以下功能：
     - JWT token 生成和验证
     - 用户认证中间件
     - 密码哈希工具函数

     测试结果：12 passed, 0 failed
```

### 10.6 实施步骤

#### 阶段 1：创建 SDK 插件（优先）

1. 创建 `plugins/cli/claude_code_sdk.py`
2. 实现基本的 `query()` 调用
3. 实现 `can_use_tool` 回调
4. 测试基本功能

#### 阶段 2：集成权限管理

1. 修改 `PermissionManager` 添加异步等待
2. 修改 `Bot` 注册 SDK 权限回调
3. 实现用户响应处理

#### 阶段 3：配置和测试

1. 添加配置项：选择使用 CLI 还是 SDK
2. 完整流程测试
3. 边界情况处理

### 10.7 配置示例

```yaml
# config.yaml
cli:
  # 选择使用的后端
  active: claude_code_sdk  # 或 claude_code（subprocess 模式）

  claude_code_sdk:
    # 权限模式
    permission_mode: "default"  # default, acceptEdits, bypassPermissions

    # 默认允许的工具（可选）
    # allowed_tools:
    #   - Read
    #   - Glob
    #   - Grep

    # 权限确认超时（秒）
    permission_timeout: 300
```

### 10.8 优势对比

| 特性 | CLI 模式 (subprocess) | SDK 模式 |
|------|----------------------|----------|
| 权限控制 | ❌ --print 模式不支持 | ✅ can_use_tool 回调 |
| 实现复杂度 | 中（需要解析 stream-json） | 低（直接使用 SDK） |
| 错误处理 | 手动解析 stderr | SDK 内置 |
| 会话管理 | 手动 --session-id/--resume | SDK 自动处理 |
| 进度更新 | ✅ stream-json | ✅ 消息流 |
| 依赖 | 无（调用本地二进制） | claude-code-sdk 包 |

### 10.9 总结

**最终推荐：使用 Claude Agent SDK**

理由：
1. **官方推荐** - Claude Code 团队明确建议非交互模式使用 SDK
2. **权限控制** - `can_use_tool` 回调是唯一支持异步权限确认的方式
3. **代码简洁** - 无需解析 stream-json，无需管理子进程
4. **未来兼容** - SDK 会随 Claude Code 更新，保持兼容

**迁移路径：**
1. ✅ 当前：使用 subprocess + stream-json（已实现）
2. 🔄 下一步：添加 SDK 插件，两种模式共存
3. 🎯 最终：SDK 作为默认，subprocess 作为备用
