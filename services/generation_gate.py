from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from services.domain.errors import ErrorCategory, ProviderError

logger = logging.getLogger(__name__)


class GenerationGate:
    """限制单进程内同时调用图片生成服务商的任务数量。"""

    def __init__(self, capacity: int) -> None:
        """创建生成任务并发闸门。

        参数：
            capacity: 允许同时执行的单张图片生成任务数。

        返回值：
            无。
        """
        self.capacity = capacity
        self._semaphore = threading.BoundedSemaphore(capacity)
        self._state_lock = threading.Lock()
        self._active_count = 0
        self._waiting_count = 0

    @property
    def idle(self) -> bool:
        with self._state_lock:
            return self._active_count == 0 and self._waiting_count == 0

    @contextmanager
    def acquire(
        self,
        *,
        timeout_seconds: float,
        request_id: str,
        image_index: int,
    ) -> Iterator[None]:
        """等待并占用一个图片生成名额。

        参数：
            timeout_seconds: 当前任务最多允许等待的秒数。
            request_id: 用于关联日志的业务请求标识。
            image_index: 当前图片在批次中的零基序号。

        返回值：
            获得名额后进入的上下文管理器。
        """
        started_at = time.monotonic()
        with self._state_lock:
            self._waiting_count += 1
            waiting_count = self._waiting_count
        logger.debug(
            "image.generation.queue.waiting: %s",
            {
                "requestId": request_id,
                "imageIndex": image_index,
                "waitingCount": waiting_count,
                "capacity": self.capacity,
            },
        )
        acquired = False
        try:
            acquired = self._semaphore.acquire(timeout=max(timeout_seconds, 0.0))
        finally:
            with self._state_lock:
                self._waiting_count -= 1

        wait_ms = round((time.monotonic() - started_at) * 1000, 2)
        if not acquired:
            logger.warning(
                "image.generation.queue.timeout: %s",
                {
                    "requestId": request_id,
                    "imageIndex": image_index,
                    "waitMs": wait_ms,
                    "capacity": self.capacity,
                },
            )
            raise ProviderError(
                provider="backend",
                category=ErrorCategory.LOCAL_CAPACITY,
                message="图片生成任务排队超时。",
                retryable=True,
                counts_toward_circuit=False,
            )

        with self._state_lock:
            self._active_count += 1
            active_count = self._active_count
        logger.debug(
            "image.generation.queue.acquired: %s",
            {
                "requestId": request_id,
                "imageIndex": image_index,
                "waitMs": wait_ms,
                "activeCount": active_count,
                "capacity": self.capacity,
            },
        )
        try:
            yield
        finally:
            with self._state_lock:
                self._active_count -= 1
                active_count = self._active_count
            self._semaphore.release()
            logger.debug(
                "image.generation.queue.released: %s",
                {
                    "requestId": request_id,
                    "imageIndex": image_index,
                    "activeCount": active_count,
                    "capacity": self.capacity,
                },
            )


_gate_lock = threading.Lock()
_generation_gate: GenerationGate | None = None


def get_generation_gate(capacity: int) -> GenerationGate:
    """获取当前进程共享的图片生成并发闸门。

    参数：
        capacity: 配置的进程级最大图片生成并发数。

    返回值：
        当前进程内复用的并发闸门。
    """
    global _generation_gate
    with _gate_lock:
        if _generation_gate is None:
            _generation_gate = GenerationGate(capacity)
        elif _generation_gate.capacity != capacity and _generation_gate.idle:
            _generation_gate = GenerationGate(capacity)
        elif _generation_gate.capacity != capacity:
            logger.warning(
                "image.generation.queue.capacity_change_deferred: %s",
                {
                    "currentCapacity": _generation_gate.capacity,
                    "requestedCapacity": capacity,
                },
            )
        return _generation_gate
