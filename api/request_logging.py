from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from flask import g, has_request_context, request


def start_request_context() -> None:
    """初始化当前请求的日志上下文。

    功能说明：记录请求开始时间并生成用于关联接收与完成日志的追踪编号。

    返回值：
        无。
    """
    g.request_started_at = time.perf_counter()
    g.request_trace_id = uuid4().hex


def request_elapsed_ms() -> float:
    """读取当前请求已经消耗的毫秒数。

    返回值：
        从请求进入应用到当前节点的耗时，单位为毫秒。
    """
    started_at = getattr(g, "request_started_at", time.perf_counter())
    return round((time.perf_counter() - started_at) * 1000, 2)


def request_trace_id() -> str:
    """读取当前请求的追踪编号。

    返回值：
        用于关联同一请求日志的追踪编号。
    """
    return str(getattr(g, "request_trace_id", ""))


def build_received_log_summary(
    flask_request: Any,
    normalized_request: Any,
) -> dict[str, Any]:
    """构建不包含凭证值和请求正文的接收日志摘要。

    功能说明：记录请求入口、请求模型以及认证字段是否存在，便于确认请求已进入业务接口。

    参数：
        flask_request: 当前 Flask 请求对象，用于提取接口与请求头信息。
        normalized_request: 规范化后的业务请求对象，用于提取请求模型。

    返回值：
        可直接交给结构化日志记录器的安全字段字典。
    """
    authorization = flask_request.headers.get("Authorization", "").strip()
    base_signature = flask_request.headers.get("X-Base-Signature", "").strip()
    pack_id = flask_request.headers.get("X-Pack-Id", "").strip()
    return {
        "traceId": request_trace_id(),
        "method": flask_request.method,
        "path": flask_request.path,
        "model": normalized_request.model,
        "contentType": flask_request.headers.get("Content-Type", ""),
        "hasAuthorization": bool(authorization),
        "hasBaseSignature": bool(base_signature),
        "hasPackId": bool(pack_id),
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
    if has_request_context():
        summary = {
            "traceId": request_trace_id(),
            "method": request.method,
            "path": request.path,
            **summary,
        }
    if elapsed_ms is not None:
        summary["totalElapsedMs"] = elapsed_ms
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
