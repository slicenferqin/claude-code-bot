#!/usr/bin/env python3
"""Claude Code Bot V2 入口

支持功能：
- 异步任务执行（不阻塞）
- Hook 双向通信（进度、权限确认）
- 文字命令交互（ok/no/diff/commit/push/rollback）
- 任务管理（取消、继续）
"""

import os
import sys
import signal

# 确保项目根目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import Config, Bot, PluginRegistry

# 导入插件以触发注册
import plugins  # noqa: F401


def main():
    """主函数"""
    # 加载配置
    config = Config.load("config.yaml")

    # 获取 CLI 工具
    cli_name, cli_config = config.get_cli_config()
    cli_class = PluginRegistry.get_cli(cli_name)

    if not cli_class:
        print(f"[Main] 未找到 CLI 插件: {cli_name}")
        print(f"[Main] 可用的 CLI 插件: {PluginRegistry.list_cli_plugins()}")
        sys.exit(1)

    cli_tool = cli_class(**cli_config)

    # 创建 Bot（V2 支持更多参数）
    bot = Bot(
        cli_tool=cli_tool,
        trigger_keyword=config.bot.trigger_keyword,
        workspace=config.bot.workspace,
        default_timeout=config.bot.default_timeout,
        max_output_length=config.bot.max_output_length,
        max_concurrent_tasks=config.bot.max_concurrent_tasks,
        permission_timeout=config.bot.permission_timeout,
        auto_setup_hooks=config.bot.auto_setup_hooks,
    )

    # 添加 IM 平台
    feishu_config = config.get_feishu_config()
    if feishu_config:
        feishu_class = PluginRegistry.get_im("feishu")
        if feishu_class:
            feishu = feishu_class(
                app_id=feishu_config.app_id,
                app_secret=feishu_config.app_secret,
            )
            bot.add_im_platform(feishu)
        else:
            print("[Main] 未找到飞书插件")

    # 检查是否有 IM 平台
    if not bot._im_platforms:
        print("[Main] 没有启用任何 IM 平台，请检查配置")
        print(f"[Main] 可用的 IM 插件: {PluginRegistry.list_im_plugins()}")
        sys.exit(1)

    # 注册信号处理
    def signal_handler(sig, frame):
        print("\n[Main] 收到退出信号，正在停止...")
        bot.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 启动
    print("[Main] Claude Code Bot V2 启动中...")
    print("[Main] 支持命令: ok, no, cancel, diff, commit, push, rollback, continue, status")
    bot.start()


if __name__ == "__main__":
    main()
