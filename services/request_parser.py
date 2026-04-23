from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

logger = logging.getLogger(__name__)


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
    input_type: str
    file_urls: list[str]
    files: list[UploadedFileInfo]
    raw_payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "requestId": self.request_id,
            "prompt": self.prompt,
            "model": self.model,
            "inputType": self.input_type,
            "fileUrlCount": len(self.file_urls),
            "fileCount": len(self.files),
            "receivedFileUrls": self.file_urls,
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

    if files and file_urls:
        input_type = "mixed"
    elif files:
        input_type = "file_stream"
    elif file_urls:
        input_type = "file_url"
    else:
        input_type = "empty"

    prompt = _stringify(payload.get("prompt") if is_json_request else form_data.get("prompt"))
    model = _stringify(
        payload.get("model") if is_json_request else form_data.get("model"),
    )
    request_id = _stringify(
        payload.get("requestId") if is_json_request else form_data.get("requestId"),
    )

    raw_payload = payload if is_json_request else form_data.to_dict(flat=False)

    parsed_request = GenerateImageRequest(
        request_id=request_id,
        prompt=prompt,
        model=model,
        input_type=input_type,
        file_urls=file_urls,
        files=files,
        raw_payload=raw_payload,
    )

    logger.debug(
        "maibao.backend.request_parser.parsed: %s",
        {
            "requestId": parsed_request.request_id,
            "promptLength": len(parsed_request.prompt or ""),
            "model": parsed_request.model,
            "inputType": parsed_request.input_type,
            "fileUrlCount": len(parsed_request.file_urls),
            "fileCount": len(parsed_request.files),
            "receivedFiles": [item.to_dict() for item in parsed_request.files],
        },
    )

    return parsed_request
