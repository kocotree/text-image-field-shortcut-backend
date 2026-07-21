from __future__ import annotations

import base64
import logging
import mimetypes
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import parse

import httpx

from services.domain.errors import (
    ErrorCategory,
    ProviderError,
    provider_error_from_httpx,
    provider_error_from_status,
)
from services.http import AssetFetcher, build_asset_fetcher, build_request_timeout, get_http_client
from services.request_parser import GenerateImageRequest, UnderstandImageRequest
from services.settings import AppSettings, HttpClientSettings

logger = logging.getLogger(__name__)

GEMINI_MODEL_ALIASES = {
    "gemini-3.1-flash-image-preview": "gemini-3.1-flash-image",
    "gemini-3-pro-image-preview": "gemini-3-pro-image",
}


def resolve_gemini_model_id(requested_model: str, default_model: str) -> str:
    """解析 Gemini 模型 ID，并按受控别名表路由旧模型。

    参数：
        requested_model: 请求中显式传入的模型 ID，为空时使用默认模型。
        default_model: 服务端配置的默认 Gemini 模型 ID。

    返回值：
        可直接用于调用 Gemini 接口的正式版模型 ID。
    """
    resolved_model = str(requested_model or default_model or "").strip()
    stable_model = GEMINI_MODEL_ALIASES.get(resolved_model)
    if not stable_model:
        return resolved_model

    logger.info(
        "gemini.backend.model.compatibility_route: %s",
        {
            "requestedModel": resolved_model,
            "resolvedModel": stable_model,
        },
    )
    return stable_model


def _guess_mime_type(file_name: str, fallback: str = "application/octet-stream") -> str:
    guessed, _ = mimetypes.guess_type(file_name)
    return guessed or fallback


def _safe_file_name(file_name: str, fallback: str) -> str:
    clean_name = Path(str(file_name or "")).name.strip()
    return clean_name or fallback


def _build_inline_data_part(payload: bytes, mime_type: str) -> dict[str, Any]:
    return {
        "inline_data": {
            "mime_type": mime_type,
            "data": base64.b64encode(payload).decode("utf-8"),
        }
    }


def _decode_data_url(value: str) -> tuple[str, bytes] | None:
    prefix, separator, encoded = str(value or "").partition(",")
    if not separator or not prefix.startswith("data:") or ";base64" not in prefix:
        return None

    mime_type = prefix.removeprefix("data:").split(";", 1)[0].strip() or "application/octet-stream"
    return mime_type, base64.b64decode(encoded)


@dataclass
class PreparedReferenceInput:
    source_type: str
    mime_type: str
    file_name: str
    payload: bytes
    source_ref: str = ""
    payload_size: int = field(init=False)

    def __post_init__(self) -> None:
        self.payload_size = len(self.payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourceType": self.source_type,
            "mimeType": self.mime_type,
            "fileName": self.file_name,
            "payloadSize": self.payload_size,
            "preview": self.source_ref or f"inline_data:{self.mime_type}",
        }


@dataclass
class GeminiInvocationPlan:
    api_url: str
    api_path: str
    model: str
    prompt: str
    prepared_inputs: list[PreparedReferenceInput]
    request_body: dict[str, Any]

    def _build_request_body_preview(self) -> dict[str, Any]:
        parts = self.request_body.get("contents", [{}])[0].get("parts", [])
        parts_preview = []
        for item in parts:
            if isinstance(item.get("text"), str):
                parts_preview.append({"text": item["text"]})
                continue

            inline_data = item.get("inline_data", {})
            if isinstance(inline_data, dict):
                parts_preview.append(
                    {
                        "inline_data": {
                            "mime_type": inline_data.get("mime_type", "application/octet-stream"),
                            "data": "<base64>",
                        }
                    }
                )
                continue

            parts_preview.append(item)

        return {
            "contents": [
                {
                    "role": "user",
                    "parts": parts_preview,
                }
            ],
            "generationConfig": self.request_body.get("generationConfig", {}),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "apiUrl": self.api_url,
            "apiPath": self.api_path,
            "model": self.model,
            "prompt": self.prompt,
            "preparedInputCount": len(self.prepared_inputs),
            "preparedInputs": [item.to_dict() for item in self.prepared_inputs],
            "requestBody": self._build_request_body_preview(),
        }


@dataclass
class GeminiRawResponse:
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


def _normalize_api_url(api_url: str) -> str:
    normalized = str(api_url or "").strip().rstrip("/")
    base_url = normalized.removesuffix("/v1/chat/completions")
    return base_url


def _build_endpoint(api_url: str, api_path: str) -> str:
    base_url = _normalize_api_url(api_url)
    return f"{base_url}{api_path if not base_url.endswith('/v1') else api_path.removeprefix('/v1')}"


def _read_file_as_inline_input(uploaded_file: UploadedFileInfo) -> PreparedReferenceInput:
    storage = uploaded_file.storage
    storage.stream.seek(0)
    file_bytes = storage.read()
    storage.stream.seek(0)
    mime_type = uploaded_file.content_type or _guess_mime_type(uploaded_file.file_name)
    return PreparedReferenceInput(
        source_type="file_stream",
        mime_type=mime_type,
        file_name=uploaded_file.file_name,
        payload=file_bytes,
        source_ref=uploaded_file.file_name,
    )


def _read_url_as_inline_input(file_url: str, asset_fetcher: AssetFetcher) -> PreparedReferenceInput:
    decoded_data_url = _decode_data_url(file_url)
    if decoded_data_url:
        mime_type, payload = decoded_data_url
        return PreparedReferenceInput(
            source_type="data_url",
            mime_type=mime_type,
            file_name=f"reference{mimetypes.guess_extension(mime_type) or '.bin'}",
            payload=payload,
            source_ref="data_url",
        )

    request_url = str(file_url or "").strip()
    if not request_url:
        raise RuntimeError("Encountered an empty reference image URL.")

    url_parts = parse.urlparse(request_url)
    fallback_name = Path(url_parts.path).name or "reference"

    try:
        fetched_asset = asset_fetcher.fetch(request_url)
        payload = fetched_asset.body
        response_mime_type = fetched_asset.content_type
    except Exception as exc:
        raise RuntimeError(f"Failed to download reference image URL: {request_url}") from exc

    mime_type = response_mime_type or _guess_mime_type(fallback_name)
    file_name = _safe_file_name(fallback_name, f"reference{mimetypes.guess_extension(mime_type) or '.bin'}")
    return PreparedReferenceInput(
        source_type="url",
        mime_type=mime_type,
        file_name=file_name,
        payload=payload,
        source_ref=request_url,
    )


def _prepare_reference_inputs(
    request_data: GenerateImageRequest, asset_fetcher: AssetFetcher
) -> list[PreparedReferenceInput]:
    prepared_inputs = [_read_url_as_inline_input(file_url, asset_fetcher) for file_url in request_data.file_urls]
    prepared_inputs.extend(_read_file_as_inline_input(uploaded_file) for uploaded_file in request_data.files)
    return prepared_inputs


def _prepare_url_reference_inputs(
    file_urls: list[str], asset_fetcher: AssetFetcher
) -> list[PreparedReferenceInput]:
    return [_read_url_as_inline_input(file_url, asset_fetcher) for file_url in file_urls]


def _build_gemini_request_body(
    prompt: str,
    prepared_inputs: list[PreparedReferenceInput],
    aspect_ratio: str | None,
    image_size: str,
) -> dict[str, Any]:
    parts: list[dict[str, Any]] = [{"text": prompt}]
    parts.extend(_build_inline_data_part(item.payload, item.mime_type) for item in prepared_inputs)
    image_config = {
        "imageSize": image_size,
    }
    if aspect_ratio:
        image_config["aspectRatio"] = aspect_ratio

    return {
        "contents": [
            {
                "role": "user",
                "parts": parts,
            }
        ],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": image_config,
        },
    }


def build_gemini_invocation_plan(request_data: GenerateImageRequest, settings: AppSettings) -> GeminiInvocationPlan:
    prepared_inputs = _prepare_reference_inputs(request_data, build_asset_fetcher(settings))
    resolved_model = resolve_gemini_model_id(request_data.model, settings.default_model_id)
    api_path = f"/v1beta/models/{resolved_model}:generateContent"
    request_body = _build_gemini_request_body(
        request_data.prompt,
        prepared_inputs,
        request_data.aspect_ratio,
        request_data.image_size,
    )
    return GeminiInvocationPlan(
        api_url=_build_endpoint(settings.api_base_url, api_path),
        api_path=api_path,
        model=resolved_model,
        prompt=request_data.prompt,
        prepared_inputs=prepared_inputs,
        request_body=request_body,
    )


def _build_gemini_text_request_body(
    prompt: str,
    prepared_inputs: list[PreparedReferenceInput],
) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    parts.extend(_build_inline_data_part(item.payload, item.mime_type) for item in prepared_inputs)
    if prompt:
        parts.append({"text": prompt})
    return {
        "contents": [
            {
                "role": "user",
                "parts": parts,
            }
        ],
    }


NANO_BANANA_MODEL = "gemini-2.5-flash-image"


def build_gemini_understand_plan(request_data: UnderstandImageRequest, settings: AppSettings) -> GeminiInvocationPlan:
    prepared_inputs = _prepare_url_reference_inputs(request_data.file_urls, build_asset_fetcher(settings))
    resolved_model = resolve_gemini_model_id(request_data.model, NANO_BANANA_MODEL)
    api_path = f"/v1beta/models/{resolved_model}:generateContent"
    request_body = _build_gemini_text_request_body(
        request_data.prompt,
        prepared_inputs,
    )
    return GeminiInvocationPlan(
        api_url=_build_endpoint(settings.api_base_url, api_path),
        api_path=api_path,
        model=resolved_model,
        prompt=request_data.prompt,
        prepared_inputs=prepared_inputs,
        request_body=request_body,
    )


def invoke_gemini(
    invocation_plan: GeminiInvocationPlan,
    api_key: str,
    client_settings: HttpClientSettings | None = None,
    client: httpx.Client | None = None,
    timeout_seconds: float | None = None,
) -> GeminiRawResponse:
    """调用 Gemini 兼容接口。

    参数：
        invocation_plan: 已完成模型解析和请求体构建的调用计划。
        api_key: 服务商 API Key。
        client_settings: 共享客户端使用的超时与连接池配置。
        client: 测试或特殊场景注入的 HTTPX 客户端。
        timeout_seconds: 路由层分配给本次调用的最大秒数。

    返回值：
        包含响应状态、响应头和原始字节的 Gemini 响应。
    """
    if not api_key:
        raise ProviderError(
            provider="easyrouter",
            category=ErrorCategory.AUTHENTICATION,
            message="EasyRouter API Key 未配置。",
            retryable=True,
            counts_toward_circuit=True,
        )

    request_body_size = len(str(invocation_plan.request_body).encode("utf-8"))
    resolved_client_settings = client_settings or HttpClientSettings()
    http_client = client or get_http_client(
        "easyrouter",
        resolved_client_settings,
    )
    start_time = time.perf_counter()

    logger.info(
        "gemini.backend.request.start: %s",
        {
            "apiUrl": invocation_plan.api_url,
            "hasApiKey": bool(api_key),
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
            timeout=build_request_timeout(resolved_client_settings, timeout_seconds),
        )
        response_body = response.content
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

        logger.info(
            "gemini.backend.generation_time: %s",
            {
                "model": invocation_plan.model,
                "status": response.status_code,
                "elapsedMs": elapsed_ms,
            },
        )

        logger.debug(
            "gemini.backend.response.received: %s",
            {
                "status": response.status_code,
                "contentType": response.headers.get("content-type", ""),
                "contentDisposition": response.headers.get("content-disposition", ""),
                "bodyLength": len(response_body),
                "elapsedMs": elapsed_ms,
            },
        )

        if response.status_code >= 400:
            error_body = response_body.decode("utf-8", errors="ignore")
            logger.debug(
                "gemini.backend.request.http_error: %s",
                {
                    "status": response.status_code,
                    "elapsedMs": elapsed_ms,
                    "bodyPreview": error_body[:1000],
                },
            )
            raise provider_error_from_status(
                "easyrouter",
                response.status_code,
                f"Gemini HTTP {response.status_code}: {error_body[:1000]}",
                headers=response.headers,
                request_id=response.headers.get("x-request-id", ""),
            )

        return GeminiRawResponse(
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
            "gemini.backend.request.http_exception: %s",
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
            "gemini.backend.request.unexpected_exception: %s",
            {
                "apiUrl": invocation_plan.api_url,
                "model": invocation_plan.model,
                "elapsedMs": elapsed_ms,
                "errorType": type(exc).__name__,
                "error": str(exc),
            },
            exc_info=True,
        )
        raise RuntimeError(f"Gemini request to {invocation_plan.api_url} failed: {exc}") from exc
