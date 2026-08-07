from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from services.generation_gate import GenerationGate

logger = logging.getLogger(__name__)
_trim_lock = threading.Lock()
_last_trim_at = 0.0


@dataclass(frozen=True)
class MemoryReleaseResult:
    malloc_trimmed: bool
    rss_before_bytes: int | None
    rss_after_bytes: int | None


def release_process_memory(
    *,
    request_path: str,
    status_code: int,
    rss_threshold_bytes: int,
    cooldown_seconds: float,
    generation_gate: GenerationGate,
) -> MemoryReleaseResult:
    """归还图片请求结束后由 glibc 保留的空闲堆内存。

    参数：
        request_path: 触发回收的图片接口路径。
        status_code: 已发送响应的 HTTP 状态码。
        rss_threshold_bytes: 允许触发 glibc 堆裁剪的进程 RSS 下限。
        cooldown_seconds: 两次堆裁剪之间需要间隔的最短秒数。
        generation_gate: 用于确认当前没有执行中或等待中的图片任务。

    返回值：
        包含堆裁剪结果和回收前后 RSS 的诊断结果。
    """
    rss_before_bytes = _read_current_rss_bytes()
    if (
        rss_before_bytes is None
        or rss_before_bytes < rss_threshold_bytes
    ):
        return MemoryReleaseResult(
            malloc_trimmed=False,
            rss_before_bytes=rss_before_bytes,
            rss_after_bytes=rss_before_bytes,
        )
    if not _trim_lock.acquire(blocking=False):
        return MemoryReleaseResult(
            malloc_trimmed=False,
            rss_before_bytes=rss_before_bytes,
            rss_after_bytes=rss_before_bytes,
        )
    global _last_trim_at
    try:
        now = time.monotonic()
        if now - _last_trim_at < cooldown_seconds:
            return MemoryReleaseResult(
                malloc_trimmed=False,
                rss_before_bytes=rss_before_bytes,
                rss_after_bytes=rss_before_bytes,
            )
        with generation_gate.idle_guard() as generation_idle:
            if not generation_idle:
                return MemoryReleaseResult(
                    malloc_trimmed=False,
                    rss_before_bytes=rss_before_bytes,
                    rss_after_bytes=rss_before_bytes,
                )
            malloc_trimmed = _trim_linux_heap()
            _last_trim_at = now
            rss_after_bytes = _read_current_rss_bytes()
    finally:
        _trim_lock.release()
    released_bytes = None
    if rss_before_bytes is not None and rss_after_bytes is not None:
        released_bytes = max(0, rss_before_bytes - rss_after_bytes)

    result = MemoryReleaseResult(
        malloc_trimmed=malloc_trimmed,
        rss_before_bytes=rss_before_bytes,
        rss_after_bytes=rss_after_bytes,
    )
    logger.info(
        "memory.image_request.release.completed: %s",
        {
            "path": request_path,
            "statusCode": status_code,
            "rssThresholdBytes": rss_threshold_bytes,
            "cooldownSeconds": cooldown_seconds,
            "mallocTrimmed": malloc_trimmed,
            "rssBeforeBytes": rss_before_bytes,
            "rssAfterBytes": rss_after_bytes,
            "rssReleasedBytes": released_bytes,
        },
    )
    return result


def _trim_linux_heap() -> bool:
    """调用 glibc 归还空闲堆页；非 Linux 环境直接跳过。"""
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL(None)
        malloc_trim = libc.malloc_trim
        malloc_trim.argtypes = [ctypes.c_size_t]
        malloc_trim.restype = ctypes.c_int
        return bool(malloc_trim(0))
    except (AttributeError, OSError):
        return False


def _read_current_rss_bytes() -> int | None:
    """读取 Linux 当前进程 RSS；不可用时返回空值。"""
    try:
        status_text = Path("/proc/self/status").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in status_text.splitlines():
        if line.startswith("VmRSS:"):
            fields = line.split()
            if len(fields) >= 2:
                return int(fields[1]) * 1024
    return None
