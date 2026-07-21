from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from services.state.base import StateStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ExpiringValue:
    value: str
    expires_at: float


class MemoryStateStore:
    """在线程安全的进程内内存中保存路由状态。"""

    available = True
    backend_name = "memory"
    shared_across_workers = False

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """创建内存状态存储。

        参数：
            clock: 提供单调时间的函数，用于计算状态过期时间。

        返回值：
            无。
        """
        self._clock = clock
        self._values: dict[str, _ExpiringValue] = {}
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> str | None:
        with self._lock:
            value = self._get_live_value(key)
            return value.value if value else None

    def set(self, key: str, value: str, ttl_seconds: int) -> bool:
        with self._lock:
            self._values[key] = _ExpiringValue(
                value=str(value),
                expires_at=self._clock() + ttl_seconds,
            )
        return True

    def delete(self, *keys: str) -> bool:
        with self._lock:
            for key in keys:
                self._values.pop(key, None)
                self._events.pop(key, None)
        return True

    def increment(self, key: str, ttl_seconds: int) -> int:
        with self._lock:
            current = self._get_live_value(key)
            try:
                count = int(current.value if current else "0") + 1
            except ValueError:
                count = 1
            self._values[key] = _ExpiringValue(
                value=str(count),
                expires_at=self._clock() + ttl_seconds,
            )
            return count

    def acquire_lock(self, key: str, ttl_seconds: int) -> bool:
        with self._lock:
            if self._get_live_value(key) is not None:
                return False
            self._values[key] = _ExpiringValue(
                value="1",
                expires_at=self._clock() + ttl_seconds,
            )
            return True

    def record_event(self, key: str, window_seconds: int) -> int:
        with self._lock:
            now = self._clock()
            events = self._events.setdefault(key, deque())
            cutoff = now - window_seconds
            while events and events[0] <= cutoff:
                events.popleft()
            events.append(now)
            return len(events)

    def _get_live_value(self, key: str) -> _ExpiringValue | None:
        value = self._values.get(key)
        if value is not None and value.expires_at <= self._clock():
            self._values.pop(key, None)
            return None
        return value


_factory_lock = threading.Lock()
_store_pid: int | None = None
_store: MemoryStateStore | None = None


def build_state_store() -> StateStore:
    """获取当前 worker 的路由状态存储。

    返回值：
        当前进程共享的线程安全内存状态存储；进程 fork 后自动创建独立实例。
    """
    global _store_pid, _store

    current_pid = os.getpid()
    with _factory_lock:
        if _store is None or _store_pid != current_pid:
            _store = MemoryStateStore()
            _store_pid = current_pid
            logger.info(
                "state.memory.store_created: %s",
                {"pid": current_pid, "sharedAcrossWorkers": False},
            )
        return _store
