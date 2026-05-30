from __future__ import annotations

import http.client
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from services.request_parser import GenerateImageRequest, RequestValidationError
from services.settings import AppSettings

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
    invocation_plan: OpenAIImageInvocationPlan, api_key: str, timeout: int = 300
) -> OpenAIImageRawResponse:
    if not api_key:
        raise RuntimeError("Missing API key.")

    request_body = json.dumps(invocation_plan.request_body).encode("utf-8")
    normalized_endpoint = invocation_plan.api_url.removeprefix("https://")
    host, _, path = normalized_endpoint.partition("/")
    request_path = f"/{path}" if path else "/"
    connection = http.client.HTTPSConnection(host, timeout=timeout)
    start_time = time.perf_counter()

    logger.info(
        "openai_image.backend.request.start: %s",
        {
            "apiUrl": invocation_plan.api_url,
            "host": host,
            "path": request_path,
            "model": invocation_plan.model,
            "requestBodySize": len(request_body),
        },
    )

    try:
        connection.request(
            "POST",
            request_path,
            body=request_body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "Accept": "*/*",
            },
        )
        response = connection.getresponse()
        response_body = response.read()
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

        logger.info(
            "openai_image.backend.generation_time: %s",
            {
                "model": invocation_plan.model,
                "status": response.status,
                "elapsedMs": elapsed_ms,
            },
        )

        logger.debug(
            "openai_image.backend.response.received: %s",
            {
                "status": response.status,
                "contentType": response.getheader("content-type", ""),
                "bodyLength": len(response_body),
                "elapsedMs": elapsed_ms,
            },
        )

        if response.status >= 400:
            error_body = response_body.decode("utf-8", errors="ignore")
            logger.debug(
                "openai_image.backend.request.http_error: %s",
                {
                    "status": response.status,
                    "elapsedMs": elapsed_ms,
                    "bodyPreview": error_body[:1000],
                },
            )
            raise RuntimeError(f"OpenAI Image API HTTP {response.status}: {error_body[:4000]}")

        return OpenAIImageRawResponse(
            status_code=response.status,
            content_type=response.getheader("content-type", ""),
            content_disposition=response.getheader("content-disposition", ""),
            body=response_body,
        )
    except TimeoutError as exc:
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.debug(
            "openai_image.backend.request.timeout: %s",
            {
                "apiUrl": invocation_plan.api_url,
                "model": invocation_plan.model,
                "timeout": timeout,
                "elapsedMs": elapsed_ms,
            },
            exc_info=True,
        )
        raise RuntimeError(f"OpenAI Image API request timeout after {elapsed_ms}ms")
    except http.client.HTTPException as exc:
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
        raise RuntimeError(f"OpenAI Image API request failed: {exc}")
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
    finally:
        connection.close()
