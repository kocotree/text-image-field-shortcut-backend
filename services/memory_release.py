from __future__ import annotations

import ctypes
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryReleaseResult:
    malloc_trimmed: bool
    rss_before_bytes: int | None
    rss_after_bytes: int | None


def release_process_memory(*, request_path: str, status_code: int) -> MemoryReleaseResult:
    """归还图片请求结束后由 glibc 保留的空闲堆内存。

    参数：
        request_path: 触发回收的图片接口路径。
        status_code: 已发送响应的 HTTP 状态码。

    返回值：
        包含堆裁剪结果和回收前后 RSS 的诊断结果。
    """
    rss_before_bytes = _read_current_rss_bytes()
    malloc_trimmed = _trim_linux_heap()
    rss_after_bytes = _read_current_rss_bytes()
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
