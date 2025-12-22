"""插件注册中心"""

from typing import Dict, Type, List

from interfaces.im import IMPlatform
from interfaces.cli import CLITool


class PluginRegistry:
    """插件注册中心

    使用装饰器模式注册插件，支持动态加载。

    Example:
        @PluginRegistry.register_im("feishu")
        class FeishuPlatform(IMPlatform):
            pass

        @PluginRegistry.register_cli("claude_code")
        class ClaudeCodeCLI(CLITool):
            pass
    """

    _im_plugins: Dict[str, Type[IMPlatform]] = {}
    _cli_plugins: Dict[str, Type[CLITool]] = {}

    @classmethod
    def register_im(cls, name: str):
        """装饰器：注册 IM 插件

        Args:
            name: 插件名称，用于配置文件中引用
        """

        def decorator(plugin_class: Type[IMPlatform]):
            cls._im_plugins[name] = plugin_class
            return plugin_class

        return decorator

    @classmethod
    def register_cli(cls, name: str):
        """装饰器：注册 CLI 插件

        Args:
            name: 插件名称，用于配置文件中引用
        """

        def decorator(plugin_class: Type[CLITool]):
            cls._cli_plugins[name] = plugin_class
            return plugin_class

        return decorator

    @classmethod
    def get_im(cls, name: str) -> Type[IMPlatform]:
        """获取 IM 插件类

        Args:
            name: 插件名称

        Returns:
            插件类，如果不存在则返回 None
        """
        return cls._im_plugins.get(name)

    @classmethod
    def get_cli(cls, name: str) -> Type[CLITool]:
        """获取 CLI 插件类

        Args:
            name: 插件名称

        Returns:
            插件类，如果不存在则返回 None
        """
        return cls._cli_plugins.get(name)

    @classmethod
    def list_im_plugins(cls) -> List[str]:
        """列出所有已注册的 IM 插件"""
        return list(cls._im_plugins.keys())

    @classmethod
    def list_cli_plugins(cls) -> List[str]:
        """列出所有已注册的 CLI 插件"""
        return list(cls._cli_plugins.keys())
