from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any

from flask import Flask, jsonify, request

from services.image_pipeline import process_image_request
from services.request_auth import RequestAuthError, verify_base_request
from services.request_parser import RequestValidationError, parse_generate_image_request

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="[%(asctime)s] %(levelname)s in %(name)s: %(message)s",
        force=True,
    )


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


def resolve_maibao_api_key(flask_request) -> tuple[str, str]:
    authorization = flask_request.headers.get("Authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip(), "authorization-bearer"

    return "", "missing"


def _build_request_log_summary(flask_request) -> dict[str, Any]:
    base_signature = flask_request.headers.get("X-Base-Signature", "").strip()
    pack_id = flask_request.headers.get("X-Pack-Id", "").strip()
    authorization = flask_request.headers.get("Authorization", "").strip()

    return {
        "method": flask_request.method,
        "path": flask_request.path,
        "contentType": flask_request.headers.get("Content-Type", ""),
        "hasBaseSignature": bool(base_signature),
        "baseSignatureLength": len(base_signature),
        "packId": pack_id,
        "hasAuthorization": bool(authorization),
        "fileFieldCount": len(flask_request.files),
    }


def _build_parsed_request_summary(normalized_request) -> dict[str, Any]:
    return {
        "requestId": normalized_request.request_id,
        "promptLength": len(normalized_request.prompt or ""),
        "model": normalized_request.model,
        "inputType": normalized_request.input_type,
        "fileUrlCount": len(normalized_request.file_urls),
        "fileCount": len(normalized_request.files),
    }


def _build_result_log_summary(
    *,
    success: bool,
    status_code: int,
    message: str,
    normalized_request=None,
    result: dict[str, Any] | None = None,
    error: Exception | None = None,
) -> dict[str, Any]:
    return {
        "success": success,
        "statusCode": int(status_code),
        "message": message,
        "requestId": getattr(normalized_request, "request_id", ""),
        "model": getattr(normalized_request, "model", ""),
        "inputType": getattr(normalized_request, "input_type", ""),
        "ossUrl": (result or {}).get("ossUrl", ""),
        "ossUrlCount": len((result or {}).get("ossUrls", [])),
        "errorType": type(error).__name__ if error else "",
    }


def create_app() -> Flask:
    configure_logging()
    app = Flask(__name__)

    @app.get("/")
    def index():
        return build_json_response(
            success=True,
            message="Flask backend service is running.",
            data={
                "service": "maibao-field-shortcut-backend",
                "version": "runtime",
                "routes": ["/health", "/api/process-image"],
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

    @app.post("/api/process-image")
    def generate_image():
        normalized_request = None
        try:
            normalized_request = parse_generate_image_request(request)
            logger.info(
                "maibao.backend.request.input: %s",
                {
                    **_build_request_log_summary(request),
                    **_build_parsed_request_summary(normalized_request),
                },
            )
            verified_payload = verify_base_request(
                request.headers.get("X-Base-Signature", "").strip(),
                request.headers.get("X-Pack-Id", "").strip(),
            )
            api_key, api_key_source = resolve_maibao_api_key(request)
            logger.debug(
                "maibao.backend.api_key.resolved: %s",
                {
                    "source": api_key_source,
                    "present": bool(api_key),
                    "packId": verified_payload.pack_id,
                },
            )
            result = process_image_request(normalized_request, api_key)
            logger.info(
                "maibao.backend.request.result: %s",
                _build_result_log_summary(
                    success=True,
                    status_code=HTTPStatus.OK,
                    message="Image generated and uploaded successfully.",
                    normalized_request=normalized_request,
                    result=result,
                ),
            )
            return build_json_response(
                success=True,
                message="Image generated and uploaded successfully.",
                data=result,
                status_code=HTTPStatus.OK,
            )
        except RequestAuthError as error:
            logger.warning(
                "maibao.backend.request.result: %s",
                _build_result_log_summary(
                    success=False,
                    status_code=HTTPStatus.FORBIDDEN,
                    message=str(error),
                    normalized_request=normalized_request,
                    error=error,
                ),
            )
            return build_json_response(
                success=False,
                message=str(error),
                status_code=HTTPStatus.FORBIDDEN,
            )
        except RequestValidationError as error:
            logger.warning(
                "maibao.backend.request.result: %s",
                _build_result_log_summary(
                    success=False,
                    status_code=HTTPStatus.BAD_REQUEST,
                    message=str(error),
                    normalized_request=normalized_request,
                    error=error,
                ),
            )
            return build_json_response(
                success=False,
                message=str(error),
                status_code=HTTPStatus.BAD_REQUEST,
            )
        except Exception as error:
            logger.error(
                "maibao.backend.request.result: %s",
                _build_result_log_summary(
                    success=False,
                    status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                    message=str(error),
                    normalized_request=normalized_request,
                    error=error,
                ),
            )
            logger.debug("maibao.backend.request.exception", exc_info=True)
            return build_json_response(
                success=False,
                message=str(error),
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
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
