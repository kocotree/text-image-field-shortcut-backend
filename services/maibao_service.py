from __future__ import annotations

import base64
import http.client
import json
import logging
import mimetypes
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import parse, request

from services.request_parser import GenerateImageRequest, UploadedFileInfo
from services.settings import AppSettings

logger = logging.getLogger(__name__)

DEFAULT_MAIBAO_API_URL = "https://api.maibao.chat"
DEFAULT_REFERENCE_FETCH_TIMEOUT = 120


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
class MaibaoInvocationPlan:
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
class MaibaoRawResponse:
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
    return base_url or DEFAULT_MAIBAO_API_URL


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


def _read_url_as_inline_input(file_url: str, timeout: int = DEFAULT_REFERENCE_FETCH_TIMEOUT) -> PreparedReferenceInput:
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
        with request.urlopen(request_url, timeout=timeout) as response:
            payload = response.read()
            response_mime_type = response.headers.get_content_type()
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


def _prepare_reference_inputs(request_data: GenerateImageRequest) -> list[PreparedReferenceInput]:
    prepared_inputs = [_read_url_as_inline_input(file_url) for file_url in request_data.file_urls]
    prepared_inputs.extend(_read_file_as_inline_input(uploaded_file) for uploaded_file in request_data.files)
    return prepared_inputs


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


def build_maibao_invocation_plan(request_data: GenerateImageRequest, settings: AppSettings) -> MaibaoInvocationPlan:
    prepared_inputs = _prepare_reference_inputs(request_data)
    resolved_model = request_data.model or settings.default_model_id
    api_path = f"/v1beta/models/{resolved_model}:generateContent"
    request_body = _build_gemini_request_body(
        request_data.prompt,
        prepared_inputs,
        request_data.aspect_ratio,
        request_data.image_size,
    )
    return MaibaoInvocationPlan(
        api_url=_build_endpoint(settings.maibao_api_url, api_path),
        api_path=api_path,
        model=resolved_model,
        prompt=request_data.prompt,
        prepared_inputs=prepared_inputs,
        request_body=request_body,
    )


def invoke_maibao(invocation_plan: MaibaoInvocationPlan, api_key: str, timeout: int = 300) -> MaibaoRawResponse:
    if not api_key:
        raise RuntimeError("Missing Maibao API key. Pass Authorization: Bearer <key> in request headers.")

    request_body = json.dumps(invocation_plan.request_body).encode("utf-8")
    normalized_endpoint = invocation_plan.api_url.removeprefix("https://")
    host, _, path = normalized_endpoint.partition("/")
    request_path = f"/{path}" if path else "/"
    connection = http.client.HTTPSConnection(host, timeout=timeout)
    start_time = time.perf_counter()

    logger.debug(
        "maibao.backend.maibao.request.start: %s",
        {
            "apiUrl": invocation_plan.api_url,
            "host": host,
            "path": request_path,
            "model": invocation_plan.model,
            "timeout": timeout,
            "promptLength": len(invocation_plan.prompt or ""),
            "preparedInputCount": len(invocation_plan.prepared_inputs),
            "preparedInputs": [item.to_dict() for item in invocation_plan.prepared_inputs],
            "hasApiKey": bool(api_key),
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
            "maibao.backend.maibao.generation_time: %s",
            {
                "model": invocation_plan.model,
                "status": response.status,
                "elapsedMs": elapsed_ms,
            },
        )

        logger.debug(
            "maibao.backend.maibao.response.received: %s",
            {
                "status": response.status,
                "contentType": response.getheader("content-type", ""),
                "contentDisposition": response.getheader("content-disposition", ""),
                "bodyLength": len(response_body),
                "elapsedMs": elapsed_ms,
            },
        )

        if response.status >= 400:
            error_body = response_body.decode("utf-8", errors="ignore")
            logger.debug(
                "maibao.backend.maibao.request.http_error: %s",
                {
                    "status": response.status,
                    "elapsedMs": elapsed_ms,
                    "bodyPreview": error_body[:1000],
                },
            )
            raise RuntimeError(f"Maibao HTTP {response.status}: {error_body[:4000]}")

        return MaibaoRawResponse(
            status_code=response.status,
            content_type=response.getheader("content-type", ""),
            content_disposition=response.getheader("content-disposition", ""),
            body=response_body,
        )
    except TimeoutError as exc:
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.debug(
            "maibao.backend.maibao.request.timeout: %s",
            {
                "apiUrl": invocation_plan.api_url,
                "model": invocation_plan.model,
                "timeout": timeout,
                "elapsedMs": elapsed_ms,
                "errorType": type(exc).__name__,
            },
            exc_info=True,
        )
        raise RuntimeError(f"Maibao request timeout after {elapsed_ms}ms")
    except http.client.HTTPException as exc:
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.debug(
            "maibao.backend.maibao.request.http_exception: %s",
            {
                "apiUrl": invocation_plan.api_url,
                "model": invocation_plan.model,
                "timeout": timeout,
                "elapsedMs": elapsed_ms,
                "errorType": type(exc).__name__,
                "error": str(exc),
            },
            exc_info=True,
        )
        raise RuntimeError(f"Maibao request failed: {exc}")
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.debug(
            "maibao.backend.maibao.request.unexpected_exception: %s",
            {
                "apiUrl": invocation_plan.api_url,
                "model": invocation_plan.model,
                "timeout": timeout,
                "elapsedMs": elapsed_ms,
                "errorType": type(exc).__name__,
                "error": str(exc),
            },
            exc_info=True,
        )
        raise
    finally:
        connection.close()
