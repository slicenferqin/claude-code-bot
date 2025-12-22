#!/usr/bin/env python3
"""Claude Code Bot 入口"""

import os
import sys

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

    # 创建 Bot
    bot = Bot(
        cli_tool=cli_tool,
        trigger_keyword=config.bot.trigger_keyword,
        workspace=config.bot.workspace,
        default_timeout=config.bot.default_timeout,
        max_output_length=config.bot.max_output_length,
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

    # 启动
    bot.start()


if __name__ == "__main__":
    main()
