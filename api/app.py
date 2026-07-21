from __future__ import annotations

import logging
import os

from flask import Flask

from api.request_logging import start_request_context
from api.routes import health_blueprint, image_blueprint, understanding_blueprint
from services.model_registry import load_provider_configuration
from services.settings import get_app_settings

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    """按照运行环境配置标准日志输出。

    返回值：
        无。
    """
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="[%(asctime)s] %(levelname)s in %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def create_app() -> Flask:
    """创建并配置 Flask 应用。

    返回值：
        完成日志、服务商配置校验和 Blueprint 注册的 Flask 应用。
    """
    configure_logging()
    settings = get_app_settings()
    provider_configuration = load_provider_configuration(
        settings.provider_config_path
    )
    logger.info(
        "provider.configuration.loaded: %s",
        {
            "primaryProvider": provider_configuration.primary_provider,
            "fallbackProviders": provider_configuration.fallback_providers,
            "defaultModel": provider_configuration.default_model,
            "modelCount": len(provider_configuration.models),
            "fallbackEnabled": settings.fallback_enabled,
        },
    )

    app = Flask(__name__)
    app.before_request(start_request_context)
    app.register_blueprint(health_blueprint)
    app.register_blueprint(image_blueprint)
    app.register_blueprint(understanding_blueprint)
    logger.info(
        "api.routes.registered: %s",
        {"blueprintCount": 3},
    )
    return app
