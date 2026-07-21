from __future__ import annotations

from collections.abc import Mapping
import os

from services.domain.provider import ProviderClient
from services.model_registry import ProviderConfiguration
from services.providers.easyrouter import EasyRouterProvider
from services.providers.openrouter import OpenRouterProvider
from services.settings import AppSettings


def build_provider_clients(
    settings: AppSettings, configuration: ProviderConfiguration
) -> Mapping[str, ProviderClient]:
    """根据配置创建服务商客户端。

    参数：
        settings: 包含服务商地址、密钥和 HTTP 配置的应用设置。
        configuration: 已校验的服务商结构配置。

    返回值：
        以服务商名称为键的客户端映射。
    """
    clients: dict[str, ProviderClient] = {}
    for name, definition in configuration.providers.items():
        if definition.adapter == "easyrouter":
            clients[name] = EasyRouterProvider(
                settings,
                definition.base_url,
                os.getenv(definition.api_key_env, "").strip(),
            )
        elif definition.adapter == "openrouter":
            clients[name] = OpenRouterProvider(
                settings,
                definition.base_url,
                os.getenv(definition.api_key_env, "").strip(),
            )
        else:
            raise ValueError(f"不支持的服务商适配器：{definition.adapter}")
    return clients
