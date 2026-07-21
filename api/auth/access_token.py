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


class AuthServiceUnavailableError(RuntimeError):
    """鉴权服务无法提供有效响应。"""


def _verify_token(auth_header: str) -> dict[str, Any]:
    """调用鉴权服务校验访问令牌。

    参数：
        auth_header: 原始 Authorization 请求头。

    返回值：
        鉴权服务返回的 JSON 对象，包括正常结果和鉴权拒绝结果。

    异常：
        AuthServiceUnavailableError: 网络异常、服务端异常或响应无法解析。
    """
    settings = get_app_settings()
    auth_service_url = os.getenv(
        "AUTH_SERVICE_URL", "http://kocotree-skills-auth:5050"
    ).rstrip("/")
    verify_url = f"{auth_service_url}/api/v1/auth/verify"
    client = get_http_client("auth", settings.http.auth)
    logger.debug("auth.verify.start")
    try:
        response = client.get(
            verify_url, headers={"Authorization": auth_header}
        )
    except httpx.RequestError as exc:
        logger.error(
            "auth.verify.unavailable: %s",
            {"errorType": type(exc).__name__},
        )
        raise AuthServiceUnavailableError from exc

    if (
        response.status_code < 200
        or 300 <= response.status_code < 400
        or response.status_code >= 500
    ):
        logger.error(
            "auth.verify.unavailable: %s",
            {"statusCode": response.status_code},
        )
        raise AuthServiceUnavailableError

    try:
        result = response.json()
    except (ValueError, AttributeError) as exc:
        if response.status_code in {401, 403}:
            logger.warning(
                "auth.verify.rejected: %s",
                {"statusCode": response.status_code},
            )
            return {"code": response.status_code, "msg": "Invalid or expired token."}
        logger.error(
            "auth.verify.unavailable: %s",
            {
                "statusCode": response.status_code,
                "errorType": type(exc).__name__,
            },
        )
        raise AuthServiceUnavailableError from exc

    if not isinstance(result, dict):
        logger.error(
            "auth.verify.unavailable: %s",
            {"statusCode": response.status_code, "errorType": "InvalidPayload"},
        )
        raise AuthServiceUnavailableError

    if response.status_code in {401, 403} or result.get("code") != 0:
        logger.warning(
            "auth.verify.rejected: %s",
            {"statusCode": response.status_code, "code": result.get("code")},
        )
    else:
        logger.debug(
            "auth.verify.success: %s",
            {"statusCode": response.status_code, "code": result.get("code")},
        )
    return result


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

        try:
            result = _verify_token(auth_header)
        except AuthServiceUnavailableError:
            return jsonify(
                {"code": 503, "data": None, "msg": "Auth service unavailable."}
            ), 503

        if result.get("code") != 0:
            message = str(
                result.get("msg") or "Invalid or expired token."
            )
            return jsonify({"code": 401, "data": None, "msg": message}), 401
        return view(*args, **kwargs)

    return decorated
