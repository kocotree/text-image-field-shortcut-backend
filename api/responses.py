from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any

from flask import jsonify

from services.domain.errors import ErrorCategory, ProviderError
from services.routing import FailoverExhaustedError


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_json_response(
    *,
    success: bool,
    message: str,
    data: dict[str, Any] | None = None,
    status_code: int = HTTPStatus.OK,
):
    """构建统一的接口 JSON 响应。

    参数：
        success: 本次请求是否成功。
        message: 面向客户端的稳定消息。
        data: 响应业务数据。
        status_code: HTTP 状态码。

    返回值：
        Flask JSON 响应和 HTTP 状态码。
    """
    payload = {
        "success": success,
        "message": message,
        "timestamp": utc_now_iso(),
        "data": data or {},
    }
    return jsonify(payload), status_code


def provider_error_response(error: ProviderError):
    """将服务商错误转换成稳定的客户端响应。

    参数：
        error: 路由层或服务商适配器返回的标准错误。

    返回值：
        Flask JSON 响应及对应 HTTP 状态码。
    """
    if isinstance(error, FailoverExhaustedError):
        status_code = HTTPStatus.BAD_GATEWAY
        message = "模型主服务商和兜底服务商当前均不可用，请稍后重试。"
    elif error.category in {
        ErrorCategory.INVALID_REQUEST,
        ErrorCategory.INVALID_ASSET,
        ErrorCategory.CONTENT_POLICY,
        ErrorCategory.CAPABILITY,
    }:
        status_code = HTTPStatus.BAD_REQUEST
        message = "模型请求未被服务商接受，请检查输入参数。"
    elif error.category in {
        ErrorCategory.CONNECTION,
        ErrorCategory.TIMEOUT,
        ErrorCategory.RATE_LIMIT,
        ErrorCategory.UPSTREAM_UNAVAILABLE,
        ErrorCategory.LOCAL_CAPACITY,
    }:
        status_code = HTTPStatus.SERVICE_UNAVAILABLE
        message = "模型服务暂时不可用，请稍后重试。"
    else:
        status_code = HTTPStatus.BAD_GATEWAY
        message = "模型服务调用失败，请稍后重试。"
    return build_json_response(
        success=False, message=message, status_code=status_code
    )
