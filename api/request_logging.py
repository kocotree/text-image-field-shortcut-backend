from __future__ import annotations

import time
from typing import Any

from flask import g


def start_request_timer() -> None:
    """记录当前请求开始时间。

    返回值：
        无。
    """
    g.request_started_at = time.perf_counter()


def request_elapsed_ms() -> float:
    """读取当前请求已经消耗的毫秒数。"""
    started_at = getattr(g, "request_started_at", time.perf_counter())
    return round((time.perf_counter() - started_at) * 1000, 2)


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
    elapsed_ms: float | None = None,
    response_bytes: int = 0,
    text_length: int = 0,
) -> dict[str, Any]:
    """构建不包含密钥和业务正文的接口结果日志摘要。

    功能说明：统一成功与失败请求的完成日志字段，并按需补充耗时、响应大小和错误类型。

    参数：
        success: 请求是否成功。
        status_code: 接口响应状态码。
        message: 失败时需要记录的安全错误说明。
        normalized_request: 规范化后的请求对象，用于提取请求编号和请求模型。
        result: 服务执行结果，用于提取实际模型、服务商及兜底信息。
        error: 请求处理期间产生的异常对象。
        elapsed_ms: 请求从进入接口到当前节点的总耗时，单位为毫秒。
        response_bytes: 二进制响应体大小，单位为字节。
        text_length: 文本响应字符数。

    返回值：
        可直接交给结构化日志记录器的安全字段字典。
    """
    resolved_result = result or {}
    summary = {
        "success": success,
        "statusCode": int(status_code),
        "requestId": getattr(normalized_request, "request_id", ""),
        "requestedModel": getattr(normalized_request, "model", ""),
        "resolvedModel": resolved_result.get("model", ""),
        "provider": resolved_result.get("provider", ""),
        "fallbackUsed": resolved_result.get("fallbackUsed", False),
    }
    if elapsed_ms is not None:
        summary["elapsedMs"] = elapsed_ms
    if resolved_result.get("ossUrls"):
        summary["ossObjectCount"] = len(resolved_result["ossUrls"])
    if response_bytes:
        summary["responseBytes"] = response_bytes
    if text_length:
        summary["textLength"] = text_length
    if not success:
        summary["message"] = message
    if error:
        summary["errorType"] = type(error).__name__
    return summary
