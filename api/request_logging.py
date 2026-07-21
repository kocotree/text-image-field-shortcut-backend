from __future__ import annotations

from typing import Any


def build_request_log_summary(flask_request: Any) -> dict[str, Any]:
    """构建不包含签名、提示词和图片内容的请求日志摘要。"""
    base_signature = flask_request.headers.get("X-Base-Signature", "").strip()
    pack_id = flask_request.headers.get("X-Pack-Id", "").strip()
    return {
        "method": flask_request.method,
        "path": flask_request.path,
        "contentType": flask_request.headers.get("Content-Type", ""),
        "hasBaseSignature": bool(base_signature),
        "baseSignatureLength": len(base_signature),
        "packId": pack_id,
        "fileFieldCount": len(flask_request.files),
    }


def build_parsed_request_summary(normalized_request: Any) -> dict[str, Any]:
    """构建不包含请求正文的解析结果日志摘要。"""
    return {
        "requestId": normalized_request.request_id,
        "promptLength": len(normalized_request.prompt or ""),
        "model": normalized_request.model,
        "inputType": normalized_request.input_type,
        "fileUrlCount": len(normalized_request.file_urls),
        "fileCount": len(normalized_request.files),
    }


def build_result_log_summary(
    *,
    success: bool,
    status_code: int,
    message: str,
    normalized_request: Any = None,
    result: dict[str, Any] | None = None,
    error: Exception | None = None,
) -> dict[str, Any]:
    """构建不包含密钥和业务正文的接口结果日志摘要。"""
    return {
        "success": success,
        "statusCode": int(status_code),
        "message": message,
        "requestId": getattr(normalized_request, "request_id", ""),
        "model": getattr(normalized_request, "model", ""),
        "inputType": getattr(normalized_request, "input_type", ""),
        "ossUrl": (result or {}).get("ossUrl", ""),
        "ossUrlCount": len((result or {}).get("ossUrls", [])),
        "provider": (result or {}).get("provider", ""),
        "fallbackUsed": (result or {}).get("fallbackUsed", False),
        "errorType": type(error).__name__ if error else "",
    }
