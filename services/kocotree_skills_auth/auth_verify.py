import os
from functools import wraps

import requests
from flask import jsonify, request

AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://kocotree-skills-auth:5050")

VERIFY_URL = f"{AUTH_SERVICE_URL}/api/v1/auth/verify"
CREATE_KEY_URL = f"{AUTH_SERVICE_URL}/api/v1/keys"


def _create_key():
    """调 auth 服务创建 key，返回 data dict 或 None。"""
    try:
        resp = requests.post(CREATE_KEY_URL, json={}, timeout=5)
        data = resp.json()
        if data.get("code") == 0:
            return data["data"]
    except (requests.RequestException, ValueError):
        pass
    return None


def _verify_key(auth_header):
    """调 auth 服务校验 key，透传 Authorization header，返回 True/False。"""
    try:
        resp = requests.get(VERIFY_URL, headers={"Authorization": auth_header}, timeout=5)
        data = resp.json()
        return data.get("code") == 0
    except (requests.RequestException, ValueError):
        return False


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")

        if not auth_header:
            key_data = _create_key()
            if not key_data:
                return jsonify({"code": 500, "data": None, "msg": "Failed to create API key."}), 500
            return jsonify({"code": 100, "data": key_data, "msg": "key_created"})

        if not _verify_key(auth_header):
            return jsonify({"code": 401, "data": None, "msg": "Invalid API key."}), 401

        return f(*args, **kwargs)

    return decorated
