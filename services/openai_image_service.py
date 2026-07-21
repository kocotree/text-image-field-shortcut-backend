from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from services.domain.errors import (
    ErrorCategory,
    ProviderError,
    provider_error_from_httpx,
    provider_error_from_status,
)
from services.http import get_http_client
from services.request_parser import GenerateImageRequest, RequestValidationError
from services.settings import AppSettings, HttpClientSettings

logger = logging.getLogger(__name__)

_ASPECT_RATIO_TO_SIZE = {
    "1:1": "1024x1024",
    "3:2": "1536x1024",
    "4:3": "1536x1024",
    "5:4": "1536x1024",
    "16:9": "2048x1152",
    "21:9": "2048x1152",
    "2:3": "1024x1536",
    "3:4": "1024x1536",
    "4:5": "1024x1536",
    "9:16": "1152x2048",
}


def _aspect_ratio_to_size(aspect_ratio: str | None) -> str:
    if not aspect_ratio:
        return "auto"
    return _ASPECT_RATIO_TO_SIZE.get(aspect_ratio, "auto")


def _normalize_api_url(api_url: str) -> str:
    normalized = str(api_url or "").strip().rstrip("/")
    return normalized.removesuffix("/v1/chat/completions").removesuffix("/v1")


@dataclass
class OpenAIImageInvocationPlan:
    api_url: str
    model: str
    prompt: str
    size: str
    n: int
    quality: str
    request_body: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "apiUrl": self.api_url,
            "model": self.model,
            "prompt": self.prompt,
            "size": self.size,
            "n": self.n,
            "quality": self.quality,
            "requestBody": self.request_body,
        }


@dataclass
class OpenAIImageRawResponse:
    status_code: int
    content_type: str
    content_disposition: str
    body: bytes

    def to_dict(self) -> dict[str, Any]:
        return {
            "statusCode": self.status_code,
            "contentType": self.content_type,
            "contentDisposition": self.content_disposition,
            "bodyLength": len(self.body),
        }


def build_openai_image_invocation_plan(
    request_data: GenerateImageRequest, settings: AppSettings
) -> OpenAIImageInvocationPlan:
    if request_data.file_urls or request_data.files:
        raise RequestValidationError(
            "GPT-image models do not support reference images. "
            "Remove fileUrl/fileUrls/files or use a Gemini model."
        )

    resolved_model = request_data.model or settings.gpt_image_model_id
    size = _aspect_ratio_to_size(request_data.aspect_ratio)
    base_url = _normalize_api_url(settings.api_base_url)
    api_url = f"{base_url}/v1/images/generations"

    request_body: dict[str, Any] = {
        "model": resolved_model,
        "prompt": request_data.prompt,
        "n": 1,
        "size": size,
        "quality": "auto",
        "moderation": "low",
    }

    return OpenAIImageInvocationPlan(
        api_url=api_url,
        model=resolved_model,
        prompt=request_data.prompt,
        size=size,
        n=1,
        quality="auto",
        request_body=request_body,
    )


def invoke_openai_image(
    invocation_plan: OpenAIImageInvocationPlan,
    api_key: str,
    client_settings: HttpClientSettings | None = None,
    client: httpx.Client | None = None,
) -> OpenAIImageRawResponse:
    """调用 OpenAI Images 兼容接口。

    参数：
        invocation_plan: 已构建完成的图片生成调用计划。
        api_key: 服务商 API Key。
        client_settings: 共享客户端使用的超时与连接池配置。
        client: 测试或特殊场景注入的 HTTPX 客户端。

    返回值：
        包含响应状态、响应头和原始字节的接口响应。
    """
    if not api_key:
        raise ProviderError(
            provider="easyrouter",
            category=ErrorCategory.AUTHENTICATION,
            message="EasyRouter API Key 未配置。",
            retryable=True,
        )

    request_body_size = len(str(invocation_plan.request_body).encode("utf-8"))
    http_client = client or get_http_client(
        "easyrouter",
        client_settings or HttpClientSettings(),
    )
    start_time = time.perf_counter()

    logger.info(
        "openai_image.backend.request.start: %s",
        {
            "apiUrl": invocation_plan.api_url,
            "model": invocation_plan.model,
            "requestBodySize": request_body_size,
        },
    )

    try:
        response = http_client.post(
            invocation_plan.api_url,
            json=invocation_plan.request_body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "Accept": "*/*",
            },
        )
        response_body = response.content
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

        logger.info(
            "openai_image.backend.generation_time: %s",
            {
                "model": invocation_plan.model,
                "status": response.status_code,
                "elapsedMs": elapsed_ms,
            },
        )

        logger.debug(
            "openai_image.backend.response.received: %s",
            {
                "status": response.status_code,
                "contentType": response.headers.get("content-type", ""),
                "bodyLength": len(response_body),
                "elapsedMs": elapsed_ms,
            },
        )

        if response.status_code >= 400:
            error_body = response_body.decode("utf-8", errors="ignore")
            logger.debug(
                "openai_image.backend.request.http_error: %s",
                {
                    "status": response.status_code,
                    "elapsedMs": elapsed_ms,
                    "bodyPreview": error_body[:1000],
                },
            )
            raise provider_error_from_status(
                "easyrouter",
                response.status_code,
                f"OpenAI Image API HTTP {response.status_code}: {error_body[:1000]}",
                headers=response.headers,
                request_id=response.headers.get("x-request-id", ""),
            )

        return OpenAIImageRawResponse(
            status_code=response.status_code,
            content_type=response.headers.get("content-type", ""),
            content_disposition=response.headers.get("content-disposition", ""),
            body=response_body,
        )
    except ProviderError:
        raise
    except httpx.HTTPError as exc:
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.debug(
            "openai_image.backend.request.http_exception: %s",
            {
                "apiUrl": invocation_plan.api_url,
                "model": invocation_plan.model,
                "elapsedMs": elapsed_ms,
                "errorType": type(exc).__name__,
                "error": str(exc),
            },
            exc_info=True,
        )
        raise provider_error_from_httpx("easyrouter", exc) from exc
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.error(
            "openai_image.backend.request.unexpected_exception: %s",
            {
                "apiUrl": invocation_plan.api_url,
                "model": invocation_plan.model,
                "elapsedMs": elapsed_ms,
                "errorType": type(exc).__name__,
                "error": str(exc),
            },
            exc_info=True,
        )
        raise RuntimeError(f"OpenAI Image API request to {invocation_plan.api_url} failed: {exc}") from exc
