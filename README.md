# Claude Code Bot

打通 IM 平台和本地 CLI 工具，让你轻松实现移动办公。

通过 IM 消息触发本地 CLI 工具（如 Claude Code），随时随地远程操控你的开发环境。

**场景示例**：在外面没带电脑，收到线上紧急 bug 反馈，直接在飞书发送 Jira 链接给机器人，让 Claude Code 自动修复、你审阅 diff、确认后提交部署。

## 功能特性

### 已实现

- **插件化架构** - 支持多 IM 平台和多 CLI 工具扩展
- **会话上下文保持** - 基于 session-id 维持对话上下文，支持多轮对话
- **多会话支持** - 不同的聊天窗口对应独立的 CLI 会话
- **消息去重** - 自动过滤重复消息，避免重复执行

### 开发中 (V2)

- **Hook 集成** - 通过 Claude Code Hooks 实现双向通信
- **异步任务执行** - 后台执行，不阻塞，无超时限制
- **实时进度推送** - CLI 执行进度主动推送到 IM
- **权限确认** - CLI 需要确认时推送到 IM，支持长时间等待
- **任务管理** - 查看状态、取消任务、回滚改动
- **交互式命令** - diff/commit/push/rollback/continue 等操作

## Roadmap

```
V1 (当前)
├── ✅ 插件化架构
├── ✅ 飞书 IM 集成
├── ✅ Claude Code CLI 集成
└── ✅ 基础消息交互

V2 (开发中)
├── 🚧 IPC 双向通信 (Unix Socket)
├── 🚧 Claude Code Hooks 集成
├── 🚧 异步任务执行
├── 🚧 实时进度推送
├── 🚧 权限确认流程
├── 🚧 任务取消与回滚
└── 🚧 交互式命令 (diff/commit/push)

V3 (计划中)
├── 📋 飞书卡片交互
├── 📋 更多 IM 平台 (钉钉/Slack/Telegram)
├── 📋 更多 CLI 工具 (Aider/Cursor)
├── 📋 多项目管理
└── 📋 CI/CD 集成
```

## V2 交互预览

```
你: claude code 修复 src/api/handler.py 中的空指针异常

Bot: 🚀 任务已启动，正在分析问题...

--- 1 分钟后 ---

Bot: ⚠️ Claude 请求执行：

     工具: Edit
     文件: src/api/handler.py

     回复 "ok" 批准，"no" 拒绝

你: ok

Bot: ✅ 已批准，继续执行...

--- 2 分钟后 ---

Bot: ✅ 任务完成

     修改了 1 个文件：
     - src/api/handler.py (+5, -1)

     回复：
     - "diff" 查看改动
     - "commit 消息" 提交
     - "cancel" 撤销

你: diff

Bot: 📄 src/api/handler.py

     @@ -25,7 +25,11 @@
      def process_request(data):
     -    result = data.get("value").process()
     +    value = data.get("value")
     +    if value is None:
     +        raise ValueError("value is required")
     +    result = value.process()

你: commit 修复空指针异常

Bot: ✅ 已提交: def4567

     回复 "push" 推送到远程
```

## 架构

### V1 架构（当前）

```
┌─────────────┐     WebSocket      ┌─────────────┐  subprocess   ┌─────────────┐
│   飞书 App   │ ◄───────────────► │   Bot 服务   │ ───────────► │ Claude Code │
└─────────────┘                    └─────────────┘               └─────────────┘
```

### V2 架构（开发中）

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              本地机器                                    │
│                                                                         │
│   ┌─────────────┐          ┌─────────────┐          ┌─────────────┐    │
│   │ Claude Code │ ──Hook──►│  Hook 脚本   │◄──IPC──►│   Bot 服务   │    │
│   └─────────────┘          └─────────────┘          └──────┬──────┘    │
│                                                            │           │
│                                    WebSocket 长连接         │           │
└────────────────────────────────────────┬───────────────────┘           │
                                         │                               │
                                         ▼                               │
                                 ┌─────────────┐                         │
                                 │  飞书服务器  │◄────────────► 你的手机   │
                                 └─────────────┘                         │
```

## 目录结构

```
claude-code-bot/
├── main.py                 # 入口文件
├── config.yaml             # 配置文件
├── requirements.txt
│
├── core/                   # 核心模块
│   ├── bot.py              # Bot 主逻辑
│   ├── session.py          # 会话管理
│   ├── config.py           # 配置加载
│   ├── registry.py         # 插件注册中心
│   ├── ipc_server.py       # IPC 服务端 (V2)
│   ├── task_manager.py     # 任务管理 (V2)
│   └── permission_manager.py # 权限确认 (V2)
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
├── hooks/                  # Hook 脚本 (V2)
│   ├── ipc_client.py
│   ├── on_stop.py
│   ├── on_permission.py
│   └── on_tool_complete.py
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
  trigger_keyword: "claude code"
  default_timeout: 180
  max_output_length: 3000
  workspace: "."

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
from interfaces.cli import CLITool, ExecutionResult

@PluginRegistry.register_cli("aider")
class AiderCLI(CLITool):
    @property
    def name(self) -> str:
        return "aider"

    def execute(self, prompt, session_id, workspace=".", timeout=180):
        # 实现命令执行
        pass

    def is_available(self) -> bool:
        # 检查工具是否可用
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
