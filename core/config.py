"""配置加载"""

import os
from dataclasses import dataclass, field
from typing import Dict, Any, Optional

import yaml


@dataclass
class BotConfig:
    """Bot 配置"""

    trigger_keyword: str = "claude code"
    default_timeout: int = 180
    max_output_length: int = 3000
    workspace: str = "."


@dataclass
class FeishuConfig:
    """飞书配置"""

    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""


@dataclass
class ClaudeCodeConfig:
    """Claude Code 配置"""

    path: str = "/opt/homebrew/bin/claude"
    default_args: list = field(default_factory=lambda: ["--dangerously-skip-permissions"])


@dataclass
class Config:
    """总配置"""

    bot: BotConfig = field(default_factory=BotConfig)
    im: Dict[str, Any] = field(default_factory=dict)
    cli: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, config_path: str = "config.yaml") -> "Config":
        """从文件加载配置

        Args:
            config_path: 配置文件路径

        Returns:
            Config 实例
        """
        config = cls()

        # 尝试加载配置文件
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            # 解析 bot 配置
            if "bot" in data:
                config.bot = BotConfig(**data["bot"])

            # 解析 IM 配置
            config.im = data.get("im", {})

            # 解析 CLI 配置
            config.cli = data.get("cli", {})

        # 环境变量覆盖
        config._apply_env_overrides()

        return config

    def _apply_env_overrides(self) -> None:
        """应用环境变量覆盖"""
        # 飞书配置
        if "feishu" not in self.im:
            self.im["feishu"] = {}

        feishu = self.im["feishu"]
        if os.environ.get("FEISHU_APP_ID"):
            feishu["app_id"] = os.environ["FEISHU_APP_ID"]
        if os.environ.get("FEISHU_APP_SECRET"):
            feishu["app_secret"] = os.environ["FEISHU_APP_SECRET"]

        # 如果有 app_id 和 app_secret，默认启用
        if feishu.get("app_id") and feishu.get("app_secret"):
            feishu.setdefault("enabled", True)

    def get_feishu_config(self) -> Optional[FeishuConfig]:
        """获取飞书配置"""
        feishu = self.im.get("feishu", {})
        if not feishu.get("enabled"):
            return None

        return FeishuConfig(
            enabled=True,
            app_id=feishu.get("app_id", ""),
            app_secret=feishu.get("app_secret", ""),
        )

    def get_cli_config(self) -> tuple[str, dict]:
        """获取 CLI 配置

        Returns:
            (active_cli_name, cli_config)
        """
        active = self.cli.get("active", "claude_code")
        cli_config = self.cli.get(active, {})
        return active, cli_config
