from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

logger = logging.getLogger(__name__)

SUPPORTED_IMAGE_SIZES = ("1K", "2K", "4K")
SUPPORTED_ASPECT_RATIOS = (
    "1:1",
    "2:3",
    "3:2",
    "3:4",
    "4:3",
    "4:5",
    "5:4",
    "9:16",
    "16:9",
    "21:9",
)
DEFAULT_IMAGE_SIZE = "1K"
MAX_REFERENCE_IMAGE_COUNT = 14


class RequestValidationError(ValueError):
    pass


@dataclass
class UploadedFileInfo:
    field_name: str
    file_name: str
    content_type: str
    content_length: int
    storage: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "fieldName": self.field_name,
            "fileName": self.file_name,
            "contentType": self.content_type,
            "contentLength": self.content_length,
        }


@dataclass
class GenerateImageRequest:
    request_id: str
    prompt: str
    model: str
    aspect_ratio: str | None
    image_size: str
    input_type: str
    file_urls: list[str]
    files: list[UploadedFileInfo]
    raw_payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "requestId": self.request_id,
            "promptLength": len(self.prompt),
            "model": self.model,
            "aspectRatio": self.aspect_ratio,
            "imageSize": self.image_size,
            "inputType": self.input_type,
            "fileUrlCount": len(self.file_urls),
            "fileCount": len(self.files),
            "receivedFiles": [item.to_dict() for item in self.files],
        }


def _stringify(value: Any, default: str = "") -> str:
    return str(value or default).strip()


def _normalize_url_values(*values: Any) -> list[str]:
    urls: list[str] = []
    for value in values:
        if isinstance(value, list):
            urls.extend(_normalize_url_values(*value))
            continue

        text = _stringify(value)
        if text:
            urls.append(text)

    return urls


def _resolve_request_value(is_json_request: bool, payload: dict[str, Any], form_data: Any, key: str, default: str = "") -> str:
    return _stringify(payload.get(key) if is_json_request else form_data.get(key), default=default)


def _normalize_image_size(value: Any) -> str:
    normalized = _stringify(value, DEFAULT_IMAGE_SIZE).upper()
    if normalized not in SUPPORTED_IMAGE_SIZES:
        raise RequestValidationError(
            f"Unsupported imageSize: {normalized}. Supported values: {', '.join(SUPPORTED_IMAGE_SIZES)}."
        )
    return normalized


def _normalize_aspect_ratio(value: Any) -> str:
    normalized = _stringify(value).replace(" ", "")
    if normalized not in SUPPORTED_ASPECT_RATIOS:
        raise RequestValidationError(
            f"Unsupported aspectRatio: {normalized}. Supported values: {', '.join(SUPPORTED_ASPECT_RATIOS)}."
        )
    return normalized


def _validate_reference_count(file_urls: list[str], files: list[UploadedFileInfo]) -> None:
    reference_count = len(file_urls) + len(files)
    if reference_count > MAX_REFERENCE_IMAGE_COUNT:
        raise RequestValidationError(
            f"Too many reference images: {reference_count}. The current limit is {MAX_REFERENCE_IMAGE_COUNT}."
        )


def _collect_uploaded_files(flask_request: Any) -> list[UploadedFileInfo]:
    files: list[UploadedFileInfo] = []
    for field_name in flask_request.files:
        for file_storage in flask_request.files.getlist(field_name):
            files.append(
                UploadedFileInfo(
                    field_name=field_name,
                    file_name=_stringify(getattr(file_storage, "filename", "")),
                    content_type=_stringify(getattr(file_storage, "content_type", "")),
                    content_length=int(getattr(file_storage, "content_length", 0) or 0),
                    storage=file_storage,
                )
            )
    return files


def parse_generate_image_request(flask_request: Any) -> GenerateImageRequest:
    is_json_request = bool(flask_request.is_json)
    payload = flask_request.get_json(silent=True) or {} if is_json_request else {}
    form_data = flask_request.form

    file_urls = _normalize_url_values(
        payload.get("fileUrl"),
        payload.get("fileUrls"),
        form_data.get("fileUrl"),
        form_data.getlist("fileUrls"),
    )
    files = _collect_uploaded_files(flask_request)
    _validate_reference_count(file_urls, files)

    if files and file_urls:
        input_type = "mixed"
    elif files:
        input_type = "file_stream"
    elif file_urls:
        input_type = "file_url"
    else:
        input_type = "empty"

    prompt = _resolve_request_value(is_json_request, payload, form_data, "prompt")
    model = _resolve_request_value(is_json_request, payload, form_data, "model")
    request_id = _resolve_request_value(is_json_request, payload, form_data, "requestId")
    raw_aspect_ratio = payload.get("aspectRatio") if is_json_request else form_data.get("aspectRatio")
    aspect_ratio = _normalize_aspect_ratio(raw_aspect_ratio) if _stringify(raw_aspect_ratio) else None
    image_size = _normalize_image_size(
        payload.get("imageSize") if is_json_request else form_data.get("imageSize")
    )

    raw_payload = payload if is_json_request else form_data.to_dict(flat=False)

    parsed_request = GenerateImageRequest(
        request_id=request_id,
        prompt=prompt,
        model=model,
        aspect_ratio=aspect_ratio,
        image_size=image_size,
        input_type=input_type,
        file_urls=file_urls,
        files=files,
        raw_payload=raw_payload,
    )

    logger.debug(
        "gemini.backend.request_parser.parsed: %s",
        {
            "requestId": parsed_request.request_id,
            "promptLength": len(parsed_request.prompt or ""),
            "model": parsed_request.model,
            "aspectRatio": parsed_request.aspect_ratio,
            "imageSize": parsed_request.image_size,
            "inputType": parsed_request.input_type,
            "fileUrlCount": len(parsed_request.file_urls),
            "fileCount": len(parsed_request.files),
            "receivedFiles": [item.to_dict() for item in parsed_request.files],
        },
    )

    return parsed_request


@dataclass
class UnderstandImageRequest:
    request_id: str
    prompt: str
    model: str
    file_urls: list[str]
    raw_payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "requestId": self.request_id,
            "promptLength": len(self.prompt),
            "model": self.model,
            "fileUrlCount": len(self.file_urls),
        }


def parse_understand_image_request(flask_request: Any) -> UnderstandImageRequest:
    payload = flask_request.get_json(silent=True) or {}

    file_urls = _normalize_url_values(payload.get("fileUrl"), payload.get("fileUrls"))
    if len(file_urls) > MAX_REFERENCE_IMAGE_COUNT:
        raise RequestValidationError(
            f"Too many reference images: {len(file_urls)}. The current limit is {MAX_REFERENCE_IMAGE_COUNT}."
        )

    parsed_request = UnderstandImageRequest(
        request_id=_stringify(payload.get("requestId")),
        prompt=_stringify(payload.get("prompt")),
        model=_stringify(payload.get("model")),
        file_urls=file_urls,
        raw_payload=payload,
    )

    logger.debug(
        "understand.backend.request_parser.parsed: %s",
        parsed_request.to_dict(),
    )

    return parsed_request
