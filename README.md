# Claude Code Bot

打通飞书和本地 Claude Code，让你轻松实现移动办公。

通过飞书消息触发本地 Claude Code CLI，随时随地远程操控你的开发环境。

## 功能特性

- **飞书消息触发** - 在飞书中发送指令，自动调用本地 Claude Code
- **会话上下文保持** - 基于 `--session-id` 维持对话上下文，支持多轮对话
- **多会话支持** - 不同的飞书聊天窗口对应独立的 Claude 会话
- **消息去重** - 自动过滤重复消息，避免重复执行
- **超时控制** - 3 分钟执行超时保护
- **实时反馈** - 即时返回"处理中"状态和执行结果

## 架构

```
┌─────────────┐     WebSocket      ┌─────────────┐     subprocess     ┌─────────────┐
│   飞书 App   │ ◄──────────────► │  Python Bot  │ ◄────────────────► │ Claude Code │
└─────────────┘                   └─────────────┘                    └─────────────┘
                                        │
                                        ▼
                                  会话管理器
                                (session-id)
```

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

## 配置说明

| 环境变量 | 说明 | 必填 |
|---------|------|------|
| `FEISHU_APP_ID` | 飞书应用 App ID | 是 |
| `FEISHU_APP_SECRET` | 飞书应用 App Secret | 是 |
| `ANTHROPIC_API_KEY` | Anthropic API Key | 是 |

## 注意事项

- Bot 运行在本地，需要保持程序运行才能响应消息
- Claude Code 会在 Bot 启动的工作目录下执行操作
- 默认超时时间为 3 分钟，可在代码中调整
- 输出超过 3000 字符会自动截断

## License

MIT
