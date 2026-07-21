from __future__ import annotations

import logging

from services.domain.requests import UnderstandImageRequest
from services.routing import build_failover_router
from services.settings import get_app_settings

logger = logging.getLogger(__name__)


def process_understand_request(request_data: UnderstandImageRequest) -> dict[str, str | bool]:
    """执行图片理解并返回服务商路由信息。

    参数：
        request_data: 已解析完成的图片理解请求。

    返回值：
        包含文本、公共模型和服务商路由信息的结果。
    """
    settings = get_app_settings()
    route_result = build_failover_router(settings).understand_image(request_data)
    provider_result = route_result.provider_result

    return {
        "requestId": request_data.request_id,
        "model": provider_result.public_model,
        "text": provider_result.text,
        "provider": provider_result.provider,
        "fallbackUsed": route_result.fallback_used,
    }
