# Claude Code Bot

打通 IM 平台和本地 CLI 工具，让你轻松实现移动办公。

通过 IM 消息触发本地 CLI 工具（如 Claude Code），随时随地远程操控你的开发环境。

**场景示例**：在外面没带电脑，收到线上紧急 bug 反馈，直接在飞书发送 Jira 链接给机器人，让 Claude Code 自动修复、你审阅 diff、确认后提交部署。

## 功能特性

### V2 已实现

- **异步任务执行** - 后台执行，不阻塞，无超时限制
- **Hook 双向通信** - 通过 Claude Code Hooks + IPC 实现实时通信
- **实时进度推送** - CLI 执行进度主动推送到 IM
- **权限确认流程** - CLI 需要确认时推送到 IM，支持长达 1 小时等待
- **任务管理** - 查看状态、取消任务、回滚改动
- **交互式命令** - diff/commit/push/rollback/continue 等操作
- **插件化架构** - 支持多 IM 平台和多 CLI 工具扩展
- **会话上下文保持** - 基于 session-id 维持对话上下文

### 支持的命令

| 命令 | 别名 | 说明 |
|------|------|------|
| `ok` | y, yes, 批准, 好 | 批准 Claude 的操作请求 |
| `no` | n, 拒绝, 不 | 拒绝操作请求 |
| `cancel` | 取消, stop | 取消当前任务 |
| `diff` | 查看, 改动 | 查看代码改动 |
| `diff <文件>` | - | 查看指定文件改动 |
| `commit <消息>` | 提交 | 提交代码 |
| `push` | 推送 | 推送到远程仓库 |
| `rollback` | 回滚, 撤销 | 撤销所有改动 |
| `continue <指令>` | 继续 | 继续让 Claude 修改 |
| `status` | 状态 | 查看任务状态 |

## Roadmap

```
V1 ✅
├── ✅ 插件化架构
├── ✅ 飞书 IM 集成
├── ✅ Claude Code CLI 集成
└── ✅ 基础消息交互

V2 ✅ (当前版本)
├── ✅ IPC 双向通信 (Unix Socket)
├── ✅ Claude Code Hooks 集成
├── ✅ 异步任务执行
├── ✅ 实时进度推送
├── ✅ 权限确认流程 (1小时超时)
├── ✅ 任务取消与回滚
└── ✅ 交互式命令 (diff/commit/push/rollback)

V3 (计划中)
├── 📋 飞书卡片交互
├── 📋 更多 IM 平台 (钉钉/Slack/Telegram)
├── 📋 更多 CLI 工具 (Aider/Cursor)
├── 📋 多项目/多工作区管理
└── 📋 CI/CD 集成
```

## 交互示例

```
你: claude code 修复 src/api/handler.py 中的空指针异常

Bot: 🚀 任务已启动
     📝 任务: 修复 src/api/handler.py 中的空指针异常
     🔑 Session: a1b2c3d4...

--- 1 分钟后 ---

Bot: 📍 Read: completed
     正在分析代码...

Bot: ⚠️ Claude 请求执行以下操作：

     工具: Edit
     命令: Edit: src/api/handler.py

     请回复：
     - "ok" 或 "y" 批准
     - "no" 或 "n" 拒绝
     - "cancel" 取消整个任务

你: ok

Bot: ✅ 已approve

--- 1 分钟后 ---

Bot: ✅ 任务完成

     修改了 1 个文件:
       - src/api/handler.py

     可用命令:
       diff - 查看改动
       commit <消息> - 提交代码
       rollback - 撤销改动
       continue <指令> - 继续修改

你: diff

Bot: 📄 改动:

     ```diff
     @@ -25,7 +25,11 @@
      def process_request(data):
     -    result = data.get("value").process()
     +    value = data.get("value")
     +    if value is None:
     +        raise ValueError("value is required")
     +    result = value.process()
     ```

你: commit 修复空指针异常

Bot: ✅ 已提交: def4567
     消息: 修复空指针异常

     回复 "push" 推送到远程

你: push

Bot: ✅ 推送成功
```

## 架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              本地机器                                    │
│                                                                         │
│   ┌─────────────┐          ┌─────────────┐          ┌─────────────┐    │
│   │ Claude Code │ ──Hook──►│  Hook 脚本   │◄──IPC──►│   Bot 服务   │    │
│   │   (进程)    │          │  (子进程)    │  Socket  │   (进程)    │    │
│   └─────────────┘          └─────────────┘          └──────┬──────┘    │
│         ▲                                                   │           │
│         │ subprocess.Popen (不阻塞)                         │           │
│         └───────────────────────────────────────────────────┘           │
│                                                                         │
│                                    WebSocket 长连接                      │
└────────────────────────────────────────┬────────────────────────────────┘
                                         │
                                         ▼
                                 ┌─────────────┐
                                 │  飞书服务器  │◄────────────► 你的手机
                                 └─────────────┘
```

**通信流程：**
1. 你在飞书发送消息 → Bot 服务收到
2. Bot 通过 `Popen` 异步启动 Claude Code
3. Claude Code 执行过程中触发 Hook 脚本
4. Hook 脚本通过 IPC (Unix Socket) 与 Bot 通信
5. Bot 将进度/确认请求推送到飞书
6. 你回复命令 → Bot 处理并响应 Hook

## 目录结构

```
claude-code-bot/
├── main.py                 # 入口文件
├── config.yaml             # 配置文件
├── requirements.txt
│
├── core/                   # 核心模块
│   ├── bot.py              # Bot 主逻辑 (V2)
│   ├── session.py          # 会话管理
│   ├── config.py           # 配置加载
│   ├── registry.py         # 插件注册中心
│   ├── ipc_server.py       # IPC 服务端 (Unix Socket)
│   ├── task_manager.py     # 任务生命周期管理
│   ├── permission_manager.py # 权限确认管理
│   └── command_parser.py   # 命令解析 + Git 操作
│
├── interfaces/             # 抽象接口
│   ├── im.py               # IM 平台接口
│   └── cli.py              # CLI 工具接口
│
├── plugins/                # 插件实现
│   ├── im/
│   │   └── feishu.py       # 飞书插件
│   └── cli/
│       └── claude_code.py  # Claude Code 插件
│
├── hooks/                  # Hook 脚本
│   ├── ipc_client.py       # IPC 客户端
│   ├── on_stop.py          # 任务完成 Hook
│   ├── on_permission.py    # 权限确认 Hook
│   └── on_tool_complete.py # 进度更新 Hook
│
└── docs/
    └── DESIGN_V2.md        # V2 技术设计文档
```

## 支持的平台

### IM 平台

| 平台 | 状态 | 说明 |
|------|------|------|
| 飞书 | ✅ 已实现 | 支持私聊和群聊 |
| 钉钉 | 📋 计划中 | - |
| Slack | 📋 计划中 | - |
| Telegram | 📋 计划中 | - |

### CLI 工具

| 工具 | 状态 | 说明 |
|------|------|------|
| Claude Code | ✅ 已实现 | Anthropic 官方 CLI |
| Aider | 📋 计划中 | - |
| Cursor | 📋 计划中 | - |

## 快速开始

### 前置要求

- Python 3.8+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) 已安装并配置
- 飞书开放平台应用（需要机器人能力）
- Anthropic API Key

### 安装

```bash
git clone git@github.com:slicenferqin/claude-code-bot.git
cd claude-code-bot
pip install -r requirements.txt
```

### 配置

```bash
export FEISHU_APP_ID="your_app_id"
export FEISHU_APP_SECRET="your_app_secret"
export ANTHROPIC_API_KEY="your_anthropic_api_key"
```

### 运行

```bash
python main.py
```

### 使用

在飞书中发送：

```
claude code 帮我写一个 hello world
```

## 飞书应用配置

1. 登录 [飞书开放平台](https://open.feishu.cn/)
2. 创建企业自建应用
3. 添加「机器人」能力
4. 在「事件订阅」中：
   - 选择 **使用长连接接收事件**（无需公网服务器）
   - 添加事件：`im.message.receive_v1`
5. 在「权限管理」中添加：
   - `im:message`
   - `im:message:send_as_bot`
6. 发布应用版本并审核通过

## 配置文件

```yaml
# config.yaml

bot:
  trigger_keyword: "claude code"    # 触发关键词
  default_timeout: 180              # 默认超时（秒）
  max_output_length: 3000           # 最大输出长度
  workspace: "."                    # 工作目录

  # V2 配置
  max_concurrent_tasks: 3           # 最大并发任务数
  permission_timeout: 3600          # 权限确认超时（秒），默认1小时
  auto_setup_hooks: true            # 自动配置 Claude Code hooks

im:
  feishu:
    enabled: true
    app_id: ""      # 或使用环境变量 FEISHU_APP_ID
    app_secret: ""  # 或使用环境变量 FEISHU_APP_SECRET

cli:
  active: claude_code
  claude_code:
    path: /opt/homebrew/bin/claude
    default_args:
      - "--dangerously-skip-permissions"
    # 注释掉上面一行可启用权限确认流程
```

## 扩展开发

### 添加新的 IM 平台

```python
# plugins/im/dingtalk.py
from core.registry import PluginRegistry
from interfaces.im import IMPlatform, Message, Reply

@PluginRegistry.register_im("dingtalk")
class DingtalkPlatform(IMPlatform):
    @property
    def name(self) -> str:
        return "dingtalk"

    def start(self, on_message):
        # 实现消息监听
        pass

    def stop(self):
        pass

    def send(self, chat_id: str, reply: Reply) -> bool:
        # 实现发送消息
        pass

    def reply(self, message: Message, reply: Reply) -> bool:
        # 实现回复消息
        pass
```

### 添加新的 CLI 工具

```python
# plugins/cli/aider.py
from core.registry import PluginRegistry
from interfaces.cli import CLITool, ExecutionResult, AsyncExecutionHandle

@PluginRegistry.register_cli("aider")
class AiderCLI(CLITool):
    @property
    def name(self) -> str:
        return "aider"

    def execute(self, prompt, session_id, workspace=".", timeout=180):
        # 同步执行
        pass

    def execute_async(self, prompt, session_id, workspace="."):
        # 异步执行（V2）
        pass

    def is_available(self) -> bool:
        pass

    def setup_hooks(self, project_dir: str) -> bool:
        # 配置 Hook（V2）
        pass
```

## 贡献

欢迎提交 Issue 和 Pull Request！

特别欢迎：
- 新的 IM 平台插件（钉钉、Slack、Telegram 等）
- 新的 CLI 工具插件（Aider、Cursor 等）
- Bug 修复和功能优化

## License

MIT
