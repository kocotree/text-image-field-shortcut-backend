from __future__ import annotations

import functools
import logging
import os

import requests as http_requests
from flask import jsonify, request

logger = logging.getLogger(__name__)

AUTH_SERVICE_URL = os.environ.get(
    "AUTH_SERVICE_URL", "http://kocotree-skills-auth:5050"
)
VERIFY_URL = f"{AUTH_SERVICE_URL}/api/v1/auth/verify"


def require_api_key(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"code": 401, "msg": "missing or malformed Authorization header"}), 401

        token = auth_header[7:]
        try:
            resp = http_requests.get(
                VERIFY_URL,
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
            )
        except http_requests.RequestException as e:
            logger.error("auth service unreachable: %s", e)
            return jsonify({"code": 502, "msg": "auth service unreachable"}), 502

        if resp.status_code != 200:
            return jsonify({"code": 401, "msg": "invalid or expired api key"}), 401

        return fn(*args, **kwargs)
    return wrapper
