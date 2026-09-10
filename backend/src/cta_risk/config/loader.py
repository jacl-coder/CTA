"""安全加载 YAML，拒绝重复键和未知设置，路径相对配置文件解析。"""

from collections.abc import Hashable
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from yaml.nodes import MappingNode

from cta_risk.config.models import Settings


class ConfigurationError(ValueError):
    """对命令行呈现的配置错误。"""


class UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        result: dict[Hashable, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise ConfigurationError("配置键必须是字符串")
            if key in result:
                raise ConfigurationError(f"重复配置键：{key}")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def load_settings(path: Path) -> Settings:
    try:
        with path.open(encoding="utf-8") as stream:
            raw = yaml.load(stream, Loader=UniqueKeyLoader)
        if not isinstance(raw, dict):
            raise ConfigurationError("配置根节点必须是映射")
        if "frontend_dir" in raw:
            directory = raw["frontend_dir"]
            if not isinstance(directory, str) or not directory.strip():
                raise ConfigurationError("frontend_dir 必须是非空路径字符串")
            raw["frontend_dir"] = (path.resolve().parent / directory).resolve()
        if isinstance(raw.get("ledger"), dict) and "database" in raw["ledger"]:
            database = raw["ledger"]["database"]
            if not isinstance(database, str) or not database.strip() or database == ":memory:":
                raise ConfigurationError("ledger.database 必须是持久化文件路径")
            raw["ledger"]["database"] = (path.resolve().parent / database).resolve()
        if isinstance(raw.get("settlement"), dict) and "report_dir" in raw["settlement"]:
            directory = raw["settlement"]["report_dir"]
            if not isinstance(directory, str) or not directory.strip():
                raise ConfigurationError("settlement.report_dir 必须是非空路径字符串")
            raw["settlement"]["report_dir"] = (path.resolve().parent / directory).resolve()
        return Settings.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ConfigurationError(f"无法加载配置 {path}: {exc}") from exc
