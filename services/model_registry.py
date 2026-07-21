from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


class ModelRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderDefinition:
    name: str
    adapter: str
    base_url: str
    api_key_env: str


@dataclass(frozen=True)
class ModelDefinition:
    public_model: str
    aliases: tuple[str, ...]
    capabilities: frozenset[str]
    provider_models: dict[str, str]


@dataclass(frozen=True)
class ProviderConfiguration:
    primary_provider: str
    fallback_providers: tuple[str, ...]
    default_model: str
    providers: dict[str, ProviderDefinition]
    models: dict[str, ModelDefinition]


class ModelRegistry:
    """解析公共模型别名并提供服务商模型映射。"""

    def __init__(self, configuration: ProviderConfiguration) -> None:
        self.configuration = configuration
        self._aliases: dict[str, str] = {}
        for public_model, definition in configuration.models.items():
            self._aliases[public_model] = public_model
            for alias in definition.aliases:
                existing = self._aliases.get(alias)
                if existing and existing != public_model:
                    raise ModelRegistryError(f"模型别名重复：{alias}")
                self._aliases[alias] = public_model

    def resolve(self, requested_model: str) -> str:
        """解析请求模型为公共正式版模型。

        参数：
            requested_model: 客户端请求中的模型 ID。

        返回值：
            解析后的公共模型 ID；未配置别名的模型保持原值。
        """
        selected = str(
            requested_model or self.configuration.default_model
        ).strip()
        return self._aliases.get(selected, selected)

    def provider_model(self, public_model: str, provider: str) -> str:
        """获取公共模型对应的服务商模型 ID。

        参数：
            public_model: 已解析的公共模型 ID。
            provider: 目标服务商名称。

        返回值：
            目标服务商接口使用的模型 ID。
        """
        definition = self.configuration.models.get(public_model)
        provider_model = definition.provider_models.get(provider, "") if definition else ""
        if not provider_model:
            raise ModelRegistryError(f"模型 {public_model} 未配置服务商 {provider} 的映射。")
        return provider_model

    def supports(self, public_model: str, capability: str) -> bool:
        definition = self.configuration.models.get(public_model)
        return bool(definition and capability in definition.capabilities)


def _require_string(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ModelRegistryError(f"配置字段不能为空：{field_name}")
    return normalized


@lru_cache(maxsize=8)
def load_provider_configuration(path: str) -> ProviderConfiguration:
    """读取并校验服务商配置文件。

    参数：
        path: 服务商 JSON 配置文件路径。

    返回值：
        完成结构校验的服务商与模型配置。
    """
    config_path = Path(path)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelRegistryError(f"无法读取服务商配置：{config_path}") from exc

    providers: dict[str, ProviderDefinition] = {}
    for name, raw_definition in payload.get("providers", {}).items():
        providers[name] = ProviderDefinition(
            name=name,
            adapter=_require_string(raw_definition.get("adapter"), f"providers.{name}.adapter"),
            base_url=_require_string(
                raw_definition.get("base_url"), f"providers.{name}.base_url"
            ),
            api_key_env=_require_string(
                raw_definition.get("api_key_env"), f"providers.{name}.api_key_env"
            ),
        )

    models: dict[str, ModelDefinition] = {}
    for public_model, raw_definition in payload.get("models", {}).items():
        provider_models = {
            str(provider): _require_string(model, f"models.{public_model}.providers.{provider}")
            for provider, model in raw_definition.get("providers", {}).items()
        }
        unknown_model_providers = set(provider_models).difference(providers)
        if unknown_model_providers:
            raise ModelRegistryError(
                f"模型 {public_model} 引用了未定义的服务商："
                f"{', '.join(sorted(unknown_model_providers))}"
            )
        models[public_model] = ModelDefinition(
            public_model=public_model,
            aliases=tuple(str(item).strip() for item in raw_definition.get("aliases", []) if str(item).strip()),
            capabilities=frozenset(
                str(item).strip() for item in raw_definition.get("capabilities", []) if str(item).strip()
            ),
            provider_models=provider_models,
        )

    primary_provider = _require_string(payload.get("primary_provider"), "primary_provider")
    fallback_providers = tuple(
        str(item).strip() for item in payload.get("fallback_providers", []) if str(item).strip()
    )
    referenced_providers = {primary_provider, *fallback_providers}
    missing_providers = referenced_providers.difference(providers)
    if missing_providers:
        raise ModelRegistryError(f"路由引用了未定义的服务商：{', '.join(sorted(missing_providers))}")

    default_model = _require_string(payload.get("default_model"), "default_model")
    if default_model not in models:
        raise ModelRegistryError(f"默认模型未在 models 中定义：{default_model}")

    configuration = ProviderConfiguration(
        primary_provider=primary_provider,
        fallback_providers=fallback_providers,
        default_model=default_model,
        providers=providers,
        models=models,
    )
    ModelRegistry(configuration)
    return configuration


def load_model_registry(path: str) -> ModelRegistry:
    """加载指定配置文件对应的模型注册表。

    参数：
        path: 服务商 JSON 配置文件路径。

    返回值：
        可解析别名与服务商模型映射的注册表。
    """
    return ModelRegistry(load_provider_configuration(path))
