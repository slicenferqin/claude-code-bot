# Claude Code Bot

打通 IM 平台和本地 CLI 工具，让你轻松实现移动办公。

通过 IM 消息触发本地 CLI 工具（如 Claude Code），随时随地远程操控你的开发环境。

## 功能特性

- **插件化架构** - 支持多 IM 平台和多 CLI 工具扩展
- **会话上下文保持** - 基于 session-id 维持对话上下文，支持多轮对话
- **多会话支持** - 不同的聊天窗口对应独立的 CLI 会话
- **消息去重** - 自动过滤重复消息，避免重复执行
- **超时控制** - 可配置的执行超时保护
- **实时反馈** - 即时返回"处理中"状态和执行结果

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         Claude Code Bot                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐        │
│  │   Feishu    │     │  DingTalk   │     │    Slack    │  ...   │
│  │   Plugin    │     │   Plugin    │     │   Plugin    │        │
│  └──────┬──────┘     └──────┬──────┘     └──────┬──────┘        │
│         │                   │                   │                │
│         └───────────────────┼───────────────────┘                │
│                             ▼                                    │
│                    ┌─────────────────┐                           │
│                    │   Bot Core      │                           │
│                    │  - 消息路由      │                           │
│                    │  - 会话管理      │                           │
│                    │  - 插件注册      │                           │
│                    └────────┬────────┘                           │
│                             │                                    │
│         ┌───────────────────┼───────────────────┐                │
│         │                   │                   │                │
│         ▼                   ▼                   ▼                │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐        │
│  │ Claude Code │     │    Aider    │     │   Cursor    │  ...   │
│  │   Plugin    │     │   Plugin    │     │   Plugin    │        │
│  └─────────────┘     └─────────────┘     └─────────────┘        │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
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
│   └── registry.py         # 插件注册中心
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
└── utils/                  # 工具函数
    └── helpers.py
```

## 支持的平台

### IM 平台

| 平台 | 状态 | 说明 |
|------|------|------|
| 飞书 | ✅ 已实现 | 支持私聊和群聊 |
| 钉钉 | 🚧 待实现 | - |
| Slack | 🚧 待实现 | - |
| Telegram | 🚧 待实现 | - |

### CLI 工具

| 工具 | 状态 | 说明 |
|------|------|------|
| Claude Code | ✅ 已实现 | Anthropic 官方 CLI |
| Aider | 🚧 待实现 | - |
| Cursor | 🚧 待实现 | - |

## 前置要求

- Python 3.8+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) 已安装并配置
- 飞书开放平台应用（需要机器人能力）
- Anthropic API Key

## 安装

1. 克隆仓库

```bash
git clone git@github.com:slicenferqin/claude-code-bot.git
cd claude-code-bot
```

2. 安装依赖

```bash
pip install -r requirements.txt
```

3. 配置环境变量

```bash
export FEISHU_APP_ID="your_app_id"
export FEISHU_APP_SECRET="your_app_secret"
export ANTHROPIC_API_KEY="your_anthropic_api_key"
```

4. （可选）修改配置文件 `config.yaml`

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

## 使用方法

1. 启动 Bot

```bash
python main.py
```

2. 在飞书中与机器人对话，发送包含 `claude code` 的消息：

```
claude code 帮我写一个 hello world
```

```
claude code 查看当前目录有哪些文件
```

```
claude code 解释一下 main.py 的代码逻辑
```

## 配置文件

`config.yaml` 支持以下配置：

```yaml
# Bot 配置
bot:
  trigger_keyword: "claude code"  # 触发关键词
  default_timeout: 180            # 超时时间（秒）
  max_output_length: 3000         # 最大输出长度
  workspace: "."                  # 工作目录

# IM 平台配置
im:
  feishu:
    enabled: true
    app_id: ""      # 或使用环境变量 FEISHU_APP_ID
    app_secret: ""  # 或使用环境变量 FEISHU_APP_SECRET

# CLI 工具配置
cli:
  active: claude_code  # 当前使用的 CLI
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

## 注意事项

- Bot 运行在本地，需要保持程序运行才能响应消息
- CLI 工具会在 Bot 启动的工作目录下执行操作
- 默认超时时间为 3 分钟，可在配置文件中调整
- 输出超过 3000 字符会自动截断

## License

MIT
