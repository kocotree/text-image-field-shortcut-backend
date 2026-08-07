from __future__ import annotations

import logging
import os
from functools import partial

from flask import Flask, Response, request

from api.request_logging import start_request_context
from api.routes import health_blueprint, image_blueprint, understanding_blueprint
from services.generation_gate import GenerationGate, get_generation_gate
from services.model_registry import load_provider_configuration
from services.memory_release import release_process_memory
from services.settings import get_app_settings

logger = logging.getLogger(__name__)
IMAGE_GENERATION_PATHS = frozenset(
    {"/api/process-image", "/api/generate-image"}
)


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
    if settings.image_generation.trim_memory_after_request:
        generation_gate = get_generation_gate(
            settings.image_generation.max_concurrency
        )
        app.after_request(
            partial(
                _schedule_image_request_memory_release,
                rss_threshold_bytes=(
                    settings.image_generation.trim_rss_threshold_bytes
                ),
                cooldown_seconds=(
                    settings.image_generation.trim_cooldown_seconds
                ),
                generation_gate=generation_gate,
            )
        )
    app.register_blueprint(health_blueprint)
    app.register_blueprint(image_blueprint)
    app.register_blueprint(understanding_blueprint)
    logger.info(
        "api.routes.registered: %s",
        {"blueprintCount": 3},
    )
    return app


def _schedule_image_request_memory_release(
    response: Response,
    *,
    rss_threshold_bytes: int,
    cooldown_seconds: float,
    generation_gate: GenerationGate,
) -> Response:
    """在图片响应发送完毕后安排进程内存回收。

    参数：
        response: Flask 已构建、尚未发送完成的响应对象。
        rss_threshold_bytes: 允许触发 glibc 堆裁剪的进程 RSS 下限。
        cooldown_seconds: 两次堆裁剪之间需要间隔的最短秒数。
        generation_gate: 用于确认当前没有执行中或等待中的图片任务。

    返回值：
        已注册关闭回调的原响应对象；非图片接口保持不变。
    """
    request_path = request.path
    if request_path not in IMAGE_GENERATION_PATHS:
        return response
    status_code = response.status_code
    response.call_on_close(
        lambda: release_process_memory(
            request_path=request_path,
            status_code=status_code,
            rss_threshold_bytes=rss_threshold_bytes,
            cooldown_seconds=cooldown_seconds,
            generation_gate=generation_gate,
        )
    )
    return response
