from __future__ import annotations

import base64
import http.client
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from services.request_parser import GenerateImageRequest, UploadedFileInfo
from services.settings import AppSettings

logger = logging.getLogger(__name__)


@dataclass
class PreparedReferenceInput:
    source_type: str
    value: str
    mime_type: str
    file_name: str

    def to_dict(self) -> dict[str, Any]:
        preview = self.value if self.source_type == "url" else f"data:{self.mime_type};base64,..."
        return {
            "sourceType": self.source_type,
            "mimeType": self.mime_type,
            "fileName": self.file_name,
            "preview": preview,
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
        content = self.request_body.get("messages", [{}])[0].get("content", [])
        content_preview = []
        for item in content:
            if item.get("type") == "text":
                content_preview.append(item)
                continue

            image_url = item.get("image_url", {}).get("url", "")
            content_preview.append(
                {
                    "type": item.get("type"),
                    "image_url": {
                        "url": image_url if image_url.startswith("http") else "data:...base64",
                    },
                }
            )

        return {
            "model": self.request_body.get("model"),
            "messages": [
                {
                    "role": "user",
                    "content": content_preview,
                }
            ],
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
    return base_url or "https://api.maibao.chat"


def _build_endpoint(api_url: str, api_path: str) -> str:
    base_url = _normalize_api_url(api_url)
    return f"{base_url}{api_path if not base_url.endswith('/v1') else api_path.removeprefix('/v1')}"


def _read_file_as_data_url(uploaded_file: UploadedFileInfo) -> PreparedReferenceInput:
    storage = uploaded_file.storage
    storage.stream.seek(0)
    file_bytes = storage.read()
    storage.stream.seek(0)
    mime_type = uploaded_file.content_type or "application/octet-stream"
    encoded = base64.b64encode(file_bytes).decode("utf-8")
    return PreparedReferenceInput(
        source_type="file_stream",
        value=f"data:{mime_type};base64,{encoded}",
        mime_type=mime_type,
        file_name=uploaded_file.file_name,
    )


def _prepare_reference_inputs(request_data: GenerateImageRequest) -> list[PreparedReferenceInput]:
    prepared_inputs = [
        PreparedReferenceInput(
            source_type="url",
            value=file_url,
            mime_type="",
            file_name="",
        )
        for file_url in request_data.file_urls
    ]
    prepared_inputs.extend(_read_file_as_data_url(uploaded_file) for uploaded_file in request_data.files)
    return prepared_inputs


def _build_gemini_request_body(prompt: str, prepared_inputs: list[PreparedReferenceInput], model: str) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    content.extend(
        {"type": "image_url", "image_url": {"url": item.value}}
        for item in prepared_inputs
    )
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": content,
            }
        ],
    }


def build_maibao_invocation_plan(request_data: GenerateImageRequest, settings: AppSettings) -> MaibaoInvocationPlan:
    prepared_inputs = _prepare_reference_inputs(request_data)
    resolved_model = request_data.model or settings.default_model_id
    api_path = "/v1/chat/completions"
    request_body = _build_gemini_request_body(request_data.prompt, prepared_inputs, resolved_model)
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

        # 图片生成时长
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
