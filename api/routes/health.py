from __future__ import annotations

import logging

from flask import Blueprint

from api.auth import require_auth
from api.responses import build_json_response
from services.routing import build_failover_router
from services.settings import get_app_settings

logger = logging.getLogger(__name__)
health_blueprint = Blueprint("health", __name__)


@health_blueprint.get("/")
def index():
    """返回服务信息和可用接口列表。

    返回值：
        统一 JSON 服务信息响应。
    """
    return build_json_response(
        success=True,
        message="Flask backend service is running.",
        data={
            "service": "text-image-field-shortcut-backend",
            "version": "runtime",
            "routes": [
                "/health",
                "/health/providers",
                "/api/process-image",
                "/api/generate-image",
                "/api/understand-image",
            ],
        },
    )


@health_blueprint.get("/health")
def health():
    """返回应用存活状态。

    返回值：
        不调用外部模型服务的存活检查响应。
    """
    return build_json_response(
        success=True,
        message="ok",
        data={"status": "healthy"},
    )


@health_blueprint.get("/health/providers")
@require_auth
def provider_health():
    """返回当前 worker 的服务商路由与熔断状态。

    返回值：
        已脱敏的服务商状态响应。
    """
    logger.debug("provider.health.requested")
    return build_json_response(
        success=True,
        message="ok",
        data=build_failover_router(get_app_settings()).status(),
    )
