# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Claude Code Bot bridges IM platforms (like Feishu/Lark) with local CLI tools (like Claude Code), enabling remote development workflows. Users send messages through IM to trigger local CLI operations, with real-time progress updates and permission confirmations flowing back to IM.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the bot
python main.py

# Required environment variables
export FEISHU_APP_ID="your_app_id"
export FEISHU_APP_SECRET="your_app_secret"
```

## Architecture

### Core Communication Flow

```
IM (Feishu) <--WebSocket--> Bot Service <--IPC (Unix Socket)--> Hook Scripts <--Hook--> Claude Code CLI
```

1. **Bot Service** (`main.py`, `core/bot.py`) - Main orchestrator running an async event loop
2. **IPC Server** (`core/ipc_server.py`) - Unix Domain Socket at `/tmp/claude-code-bot.sock` for bidirectional Hook communication
3. **Hook Scripts** (`hooks/`) - Invoked by Claude Code via its Hooks system, communicate with Bot via `IPCClient`

### Plugin System

Uses decorator-based registration in `core/registry.py`:

```python
@PluginRegistry.register_im("feishu")
class FeishuPlatform(IMPlatform): ...

@PluginRegistry.register_cli("claude_code")
class ClaudeCodeCLI(CLITool): ...
```

- **IM Plugins** implement `interfaces/im.py:IMPlatform` (methods: `start`, `stop`, `send`, `reply`)
- **CLI Plugins** implement `interfaces/cli.py:CLITool` (methods: `execute`, `execute_async`, `is_available`, `setup_hooks`)

### Key V2 Features

- **Async Execution**: CLI runs via `subprocess.Popen` (non-blocking), tracked by `TaskManager`
- **Hook-based Communication**: Claude Code Hooks trigger Python scripts that send IPC messages
- **Permission Flow**: `on_permission.py` hook polls for user approval (up to 1 hour timeout)
- **Task Management**: `TaskManager` tracks status (pending/running/waiting_confirm/completed/failed/cancelled)
- **Command Parser**: `CommandParser` handles interactive commands (ok/no/diff/commit/push/rollback/continue)

### Message Flow for Permission Requests

1. Claude Code triggers `PermissionRequest` Hook → `hooks/on_permission.py`
2. Hook sends IPC message to Bot → Bot forwards to IM for user confirmation
3. User replies in IM → Bot stores response in `PermissionManager`
4. Hook polls `get_permission_response` → receives decision → returns to Claude Code

## Configuration

`config.yaml` structure:
- `bot.trigger_keyword` - Message prefix to activate bot (default: "claude code")
- `bot.permission_timeout` - How long to wait for user confirmation (default: 3600s)
- `bot.auto_setup_hooks` - Auto-configure `.claude/settings.json` with hooks
- `im.feishu` - Feishu credentials (can be overridden by env vars)
- `cli.active` - Which CLI plugin to use
- `cli.claude_code.default_args` - Comment out `--dangerously-skip-permissions` to enable permission flow

## Directory Structure

- `core/` - Bot logic, IPC server, task/permission/session managers
- `interfaces/` - Abstract base classes for plugins
- `plugins/im/` - IM platform implementations (Feishu)
- `plugins/cli/` - CLI tool implementations (Claude Code)
- `hooks/` - Scripts invoked by Claude Code Hooks system
