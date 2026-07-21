from __future__ import annotations

import logging
import os
from functools import wraps
from typing import Any, Callable

import httpx
from flask import jsonify, request

from services.http import get_http_client
from services.settings import get_app_settings

logger = logging.getLogger(__name__)


def _verify_token(auth_header: str) -> dict[str, Any] | None:
    """调用鉴权服务校验访问令牌。

    参数：
        auth_header: 原始 Authorization 请求头。

    返回值：
        鉴权服务返回的 JSON 对象；网络或响应异常时返回空值。
    """
    settings = get_app_settings()
    auth_service_url = os.getenv(
        "AUTH_SERVICE_URL", "http://kocotree-skills-auth:5050"
    ).rstrip("/")
    verify_url = f"{auth_service_url}/api/v1/auth/verify"
    client = get_http_client("auth", settings.http.auth)
    logger.info("auth.verify.start")
    try:
        response = client.get(
            verify_url, headers={"Authorization": auth_header}
        )
        response.raise_for_status()
        result = response.json()
        logger.info("auth.verify.success: %s", {"code": result.get("code")})
        return result
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        logger.warning(
            "auth.verify.failed: %s", {"errorType": type(exc).__name__}
        )
        return None


def require_auth(view: Callable) -> Callable:
    """为 Flask 视图增加访问令牌校验。

    参数：
        view: 需要鉴权的 Flask 视图函数。

    返回值：
        包装后的 Flask 视图函数。
    """

    @wraps(view)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header:
            return jsonify(
                {"code": 401, "data": None, "msg": "Missing access token."}
            ), 401

        result = _verify_token(auth_header)
        if not result or result.get("code") != 0:
            message = (
                result.get("msg", "Invalid or expired token.")
                if result
                else "Auth service unavailable."
            )
            return jsonify({"code": 401, "data": None, "msg": message}), 401
        return view(*args, **kwargs)

    return decorated
