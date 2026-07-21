from __future__ import annotations

from typing import Protocol


class StateStore(Protocol):
    available: bool

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
        """尝试获取带有效期的分布式锁。"""
        ...

    def record_event(self, key: str, window_seconds: int) -> int:
        """记录滚动窗口事件并返回窗口内事件数。"""
        ...


class NullStateStore:
    """在 Redis 未配置或不可用时保持业务链路可运行。"""

    available = False

    def get(self, key: str) -> str | None:
        return None

    def set(self, key: str, value: str, ttl_seconds: int) -> bool:
        return False

    def delete(self, *keys: str) -> bool:
        return False

    def increment(self, key: str, ttl_seconds: int) -> int:
        return 0

    def acquire_lock(self, key: str, ttl_seconds: int) -> bool:
        return True

    def record_event(self, key: str, window_seconds: int) -> int:
        return 0
