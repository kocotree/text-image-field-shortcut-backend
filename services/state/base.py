from __future__ import annotations

from typing import Protocol


class StateStore(Protocol):
    available: bool
    backend_name: str

    def get(self, key: str) -> str | None:
        """读取字符串状态。"""
        ...

    def set(self, key: str, value: str, ttl_seconds: int) -> bool:
        """写入带有效期的字符串状态。"""
        ...

    def delete(self, *keys: str) -> bool:
        """删除一个或多个状态。"""
        ...

    def increment(self, key: str, ttl_seconds: int) -> int:
        """原子递增计数并维护有效期。"""
        ...

    def acquire_lock(self, key: str, ttl_seconds: int) -> bool:
        """尝试获取带有效期的互斥标记。"""
        ...

    def record_event(self, key: str, window_seconds: int) -> int:
        """记录滚动窗口事件并返回窗口内事件数。"""
        ...
