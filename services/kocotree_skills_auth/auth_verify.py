import os
from functools import wraps
import logging

import httpx
from flask import jsonify, request

from services.http import get_http_client
from services.settings import get_app_settings

logger = logging.getLogger(__name__)

AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://kocotree-skills-auth:5050")
VERIFY_URL = f"{AUTH_SERVICE_URL}/api/v1/auth/verify"


def _verify_token(auth_header: str) -> dict | None:
    """调用鉴权服务校验访问令牌。

    参数：
        auth_header: 原始 Authorization 请求头。

    返回值：
        鉴权服务返回的 JSON 对象；网络或响应异常时返回空值。
    """
    settings = get_app_settings()
    client = get_http_client("auth", settings.http.auth)
    logger.info("auth.verify.start")
    try:
        resp = client.get(VERIFY_URL, headers={"Authorization": auth_header})
        resp.raise_for_status()
        result = resp.json()
        logger.info("auth.verify.success: %s", {"code": result.get("code")})
        return result
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        logger.warning("auth.verify.failed: %s", {"errorType": type(exc).__name__})
        return None


def require_auth(f):
    """为 Flask 视图添加访问令牌校验。

    参数：
        f: 需要鉴权的 Flask 视图函数。

    返回值：
        包装后的 Flask 视图函数。
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")

        if not auth_header:
            return jsonify({"code": 401, "data": None, "msg": "Missing access token."}), 401

        result = _verify_token(auth_header)
        if not result or result.get("code") != 0:
            msg = result.get("msg", "Invalid or expired token.") if result else "Auth service unavailable."
            return jsonify({"code": 401, "data": None, "msg": msg}), 401

        return f(*args, **kwargs)

    return decorated
