from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any

from io import BytesIO

from flask import Flask, jsonify, request, send_file

from services.domain.errors import ErrorCategory, ProviderError
from services.image_pipeline import generate_image_only, process_image_request
from services.model_registry import load_provider_configuration
from services.routing import FailoverExhaustedError, build_failover_router
from services.settings import get_app_settings
from services.understand_pipeline import process_understand_request
from services.request_auth import RequestAuthError, verify_base_request
from services.request_parser import RequestValidationError, parse_generate_image_request, parse_understand_image_request
from services.kocotree_skills_auth.auth_verify import require_auth

logger = logging.getLogger(__name__)


def _provider_error_response(error: ProviderError):
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
    return build_json_response(success=False, message=message, status_code=status_code)


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


def _build_request_log_summary(flask_request) -> dict[str, Any]:
    base_signature = flask_request.headers.get("X-Base-Signature", "").strip()
    pack_id = flask_request.headers.get("X-Pack-Id", "").strip()

    return {
        "method": flask_request.method,
        "path": flask_request.path,
        "contentType": flask_request.headers.get("Content-Type", ""),
        "hasBaseSignature": bool(base_signature),
        "baseSignatureLength": len(base_signature),
        "packId": pack_id,
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
        "provider": (result or {}).get("provider", ""),
        "fallbackUsed": (result or {}).get("fallbackUsed", False),
        "errorType": type(error).__name__ if error else "",
    }


def create_app() -> Flask:
    """创建并配置 Flask 应用。

    返回值：
        完成日志、服务商配置校验和路由注册的 Flask 应用。
    """
    configure_logging()
    settings = get_app_settings()
    provider_configuration = load_provider_configuration(settings.provider_config_path)
    logger.info(
        "provider.configuration.loaded: %s",
        {
            "primaryProvider": provider_configuration.primary_provider,
            "fallbackProviders": provider_configuration.fallback_providers,
            "modelCount": len(provider_configuration.models),
            "fallbackEnabled": settings.fallback_enabled,
        },
    )
    app = Flask(__name__)

    @app.get("/")
    def index():
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

    @app.get("/health")
    def health():
        return build_json_response(
            success=True,
            message="ok",
            data={
                "status": "healthy",
            },
        )

    @app.get("/health/providers")
    @require_auth
    def provider_health():
        logger.info("provider.health.requested")
        return build_json_response(
            success=True,
            message="ok",
            data=build_failover_router(get_app_settings()).status(),
        )

    @app.post("/api/process-image")
    def process_image():
        normalized_request = None
        try:
            normalized_request = parse_generate_image_request(request)
            logger.info(
                "gemini.backend.request.input: %s",
                {
                    **_build_request_log_summary(request),
                    **_build_parsed_request_summary(normalized_request),
                },
            )
            verify_base_request(
                request.headers.get("X-Base-Signature", "").strip(),
                request.headers.get("X-Pack-Id", "").strip(),
            )
            result = process_image_request(normalized_request)
            logger.info(
                "gemini.backend.request.result: %s",
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
                "gemini.backend.request.result: %s",
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
                "gemini.backend.request.result: %s",
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
        except ProviderError as error:
            logger.warning(
                "gemini.backend.request.provider_error: %s",
                {
                    "provider": error.provider,
                    "category": error.category,
                    "requestId": getattr(normalized_request, "request_id", ""),
                },
            )
            return _provider_error_response(error)
        except Exception as error:
            logger.error(
                "gemini.backend.request.result: %s",
                _build_result_log_summary(
                    success=False,
                    status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                    message=str(error),
                    normalized_request=normalized_request,
                    error=error,
                ),
            )
            logger.debug("gemini.backend.request.exception", exc_info=True)
            return build_json_response(
                success=False,
                message=str(error),
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    @app.post("/api/generate-image")
    @require_auth
    def generate_image():
        normalized_request = None
        try:
            normalized_request = parse_generate_image_request(request)
            logger.info(
                "gemini.backend.generate.input: %s",
                {
                    **_build_request_log_summary(request),
                    **_build_parsed_request_summary(normalized_request),
                },
            )
            image_file = generate_image_only(normalized_request)
            logger.info(
                "gemini.backend.generate.result: %s",
                {
                    "success": True,
                    "requestId": normalized_request.request_id,
                    "mimeType": image_file.mime_type,
                    "fileName": image_file.file_name,
                    "size": len(image_file.data),
                    "provider": image_file.provider,
                    "fallbackUsed": image_file.fallback_used,
                },
            )
            response = send_file(
                BytesIO(image_file.data),
                mimetype=image_file.mime_type,
                download_name=image_file.file_name,
            )
            response.headers["X-Model-Provider"] = image_file.provider
            response.headers["X-Fallback-Used"] = str(image_file.fallback_used).lower()
            return response
        except RequestValidationError as error:
            logger.warning("gemini.backend.generate.validation_error: %s", str(error))
            return build_json_response(
                success=False,
                message=str(error),
                status_code=HTTPStatus.BAD_REQUEST,
            )
        except ProviderError as error:
            logger.warning(
                "gemini.backend.generate.provider_error: %s",
                {"provider": error.provider, "category": error.category},
            )
            return _provider_error_response(error)
        except Exception as error:
            logger.error("gemini.backend.generate.error: %s", str(error))
            logger.debug("gemini.backend.generate.exception", exc_info=True)
            return build_json_response(
                success=False,
                message=str(error),
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    @app.post("/api/understand-image")
    def understand_image():
        normalized_request = None
        try:
            normalized_request = parse_understand_image_request(request)
            logger.info(
                "understand.backend.request.input: %s",
                {
                    **_build_request_log_summary(request),
                    "requestId": normalized_request.request_id,
                    "promptLength": len(normalized_request.prompt or ""),
                    "model": normalized_request.model,
                    "fileUrlCount": len(normalized_request.file_urls),
                },
            )
            verify_base_request(
                request.headers.get("X-Base-Signature", "").strip(),
                request.headers.get("X-Pack-Id", "").strip(),
            )
            result = process_understand_request(normalized_request)
            logger.info(
                "understand.backend.request.result: %s",
                {
                    "success": True,
                    "requestId": normalized_request.request_id,
                    "model": normalized_request.model,
                    "textLength": len(result.get("text", "")),
                    "provider": result.get("provider", ""),
                    "fallbackUsed": result.get("fallbackUsed", False),
                },
            )
            return build_json_response(
                success=True,
                message="Image understanding completed successfully.",
                data=result,
                status_code=HTTPStatus.OK,
            )
        except RequestAuthError as error:
            logger.warning(
                "understand.backend.request.result: %s",
                {
                    "success": False,
                    "statusCode": int(HTTPStatus.FORBIDDEN),
                    "message": str(error),
                    "requestId": getattr(normalized_request, "request_id", ""),
                },
            )
            return build_json_response(
                success=False,
                message=str(error),
                status_code=HTTPStatus.FORBIDDEN,
            )
        except RequestValidationError as error:
            logger.warning(
                "understand.backend.request.result: %s",
                {
                    "success": False,
                    "statusCode": int(HTTPStatus.BAD_REQUEST),
                    "message": str(error),
                    "requestId": getattr(normalized_request, "request_id", ""),
                },
            )
            return build_json_response(
                success=False,
                message=str(error),
                status_code=HTTPStatus.BAD_REQUEST,
            )
        except ProviderError as error:
            logger.warning(
                "understand.backend.provider_error: %s",
                {"provider": error.provider, "category": error.category},
            )
            return _provider_error_response(error)
        except Exception as error:
            logger.error(
                "understand.backend.request.result: %s",
                {
                    "success": False,
                    "statusCode": int(HTTPStatus.INTERNAL_SERVER_ERROR),
                    "message": str(error),
                    "requestId": getattr(normalized_request, "request_id", ""),
                },
            )
            logger.debug("understand.backend.request.exception", exc_info=True)
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
