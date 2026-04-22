from __future__ import annotations

import os
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any

from flask import Flask, jsonify, request

from services.image_pipeline import build_demo_image_job


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_json_response(
    *,
    success: bool,
    message: str,
    data: dict[str, Any] | None = None,
    status_code: int = HTTPStatus.OK,
):
    payload = {
        "success": success,
        "message": message,
        "timestamp": utc_now_iso(),
        "data": data or {},
    }
    return jsonify(payload), status_code


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        return build_json_response(
            success=True,
            message="Flask demo service is running.",
            data={
                "service": "maibao-field-shortcut-backend",
                "version": "demo",
                "routes": ["/health", "/api/v1/generate-image"],
            },
        )

    @app.get("/health")
    def health():
        return build_json_response(
            success=True,
            message="ok",
            data={
                "status": "healthy",
            },
        )

    @app.post("/api/v1/generate-image")
    def generate_image():
        payload = request.get_json(silent=True) or {}
        return build_json_response(
            success=True,
            message="Demo image job created.",
            data=build_demo_image_job(payload),
            status_code=HTTPStatus.ACCEPTED,
        )

    return app


app = create_app()


def main():
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "true").lower() == "true"
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
