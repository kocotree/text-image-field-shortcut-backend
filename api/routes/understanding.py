from __future__ import annotations

import logging
from http import HTTPStatus

from flask import Blueprint, request

from api.auth import RequestAuthError, verify_base_request
from api.parsers import parse_understand_image_request
from api.request_logging import build_request_log_summary
from api.responses import build_json_response, provider_error_response
from services.domain.errors import ProviderError
from services.domain.requests import RequestValidationError
from services.pipelines import process_understand_request

logger = logging.getLogger(__name__)
understanding_blueprint = Blueprint("understanding", __name__)


@understanding_blueprint.post("/api/understand-image")
def understand_image():
    """理解图片并返回文本结果。

    返回值：
        包含理解文本和服务商路由信息的统一 JSON 响应。
    """
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
