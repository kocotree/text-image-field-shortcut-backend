from __future__ import annotations

import logging
import os
from http import HTTPStatus

from io import BytesIO

from flask import Flask, request, send_file

from api.auth import RequestAuthError, require_auth, verify_base_request
from api.parsers import parse_generate_image_request, parse_understand_image_request
from api.request_logging import (
    build_parsed_request_summary,
    build_request_log_summary,
    build_result_log_summary,
)
from api.responses import build_json_response, provider_error_response
from services.domain.errors import ProviderError
from services.domain.requests import RequestValidationError
from services.image_pipeline import generate_image_only, process_image_request
from services.model_registry import load_provider_configuration
from services.routing import build_failover_router
from services.settings import get_app_settings
from services.understand_pipeline import process_understand_request

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="[%(asctime)s] %(levelname)s in %(name)s: %(message)s",
        force=True,
    )


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
                    **build_request_log_summary(request),
                    **build_parsed_request_summary(normalized_request),
                },
            )
            verify_base_request(
                request.headers.get("X-Base-Signature", "").strip(),
                request.headers.get("X-Pack-Id", "").strip(),
            )
            result = process_image_request(normalized_request)
            logger.info(
                "gemini.backend.request.result: %s",
                build_result_log_summary(
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
                build_result_log_summary(
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
                build_result_log_summary(
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
            return provider_error_response(error)
        except Exception as error:
            public_message = "内部服务错误，请稍后重试。"
            logger.error(
                "gemini.backend.request.result: %s",
                build_result_log_summary(
                    success=False,
                    status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                    message=public_message,
                    normalized_request=normalized_request,
                    error=error,
                ),
            )
            return build_json_response(
                success=False,
                message=public_message,
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
                    **build_request_log_summary(request),
                    **build_parsed_request_summary(normalized_request),
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
            return provider_error_response(error)
        except Exception as error:
            logger.error(
                "gemini.backend.generate.error: %s",
                {
                    "requestId": getattr(normalized_request, "request_id", ""),
                    "errorType": type(error).__name__,
                },
            )
            return build_json_response(
                success=False,
                message="内部服务错误，请稍后重试。",
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
                    **build_request_log_summary(request),
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
            return provider_error_response(error)
        except Exception as error:
            logger.error(
                "understand.backend.request.result: %s",
                {
                    "success": False,
                    "statusCode": int(HTTPStatus.INTERNAL_SERVER_ERROR),
                    "message": "内部服务错误，请稍后重试。",
                    "requestId": getattr(normalized_request, "request_id", ""),
                    "errorType": type(error).__name__,
                },
            )
            return build_json_response(
                success=False,
                message="内部服务错误，请稍后重试。",
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
