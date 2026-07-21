from __future__ import annotations

import logging
from http import HTTPStatus
from io import BytesIO

from flask import Blueprint, request, send_file

from api.auth import RequestAuthError, require_auth, verify_base_request
from api.parsers import parse_generate_image_request
from api.request_logging import (
    build_parsed_request_summary,
    build_request_log_summary,
    build_result_log_summary,
)
from api.responses import build_json_response, provider_error_response
from services.domain.errors import ProviderError
from services.domain.requests import RequestValidationError
from services.pipelines import generate_image_only, process_image_request

logger = logging.getLogger(__name__)
image_blueprint = Blueprint("image", __name__)


@image_blueprint.post("/api/process-image")
def process_image():
    """生成图片并上传 OSS。

    返回值：
        包含 OSS 地址和服务商路由信息的统一 JSON 响应。
    """
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


@image_blueprint.post("/api/generate-image")
@require_auth
def generate_image():
    """生成图片并直接返回文件。

    返回值：
        图片文件响应，包含服务商和兜底标识响应头。
    """
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
        response.headers["X-Fallback-Used"] = str(
            image_file.fallback_used
        ).lower()
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
