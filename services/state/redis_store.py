from __future__ import annotations

import logging
from functools import lru_cache
import time
import uuid

import redis

from services.settings import StateSettings
from services.state.base import NullStateStore, StateStore

logger = logging.getLogger(__name__)


class RedisStateStore:
    """通过 Redis 保存跨 worker 的路由状态。"""

    available = True

    def __init__(self, client: redis.Redis, namespace: str) -> None:
        self._client = client
        self._namespace = namespace.strip(":")

    def get(self, key: str) -> str | None:
        try:
            value = self._client.get(self._key(key))
            if value is None:
                return None
            return value.decode("utf-8") if isinstance(value, bytes) else str(value)
        except redis.RedisError:
            self._report_failure("get", key)
            return None

    def set(self, key: str, value: str, ttl_seconds: int) -> bool:
        try:
            return bool(self._client.set(self._key(key), value, ex=ttl_seconds))
        except redis.RedisError:
            self._report_failure("set", key)
            return False

    def delete(self, *keys: str) -> bool:
        if not keys:
            return True
        try:
            self._client.delete(*(self._key(key) for key in keys))
            return True
        except redis.RedisError:
            self._report_failure("delete", keys[0])
            return False

    def increment(self, key: str, ttl_seconds: int) -> int:
        namespaced_key = self._key(key)
        try:
            with self._client.pipeline(transaction=True) as pipeline:
                pipeline.incr(namespaced_key)
                pipeline.expire(namespaced_key, ttl_seconds)
                count, _ = pipeline.execute()
            return int(count)
        except redis.RedisError:
            self._report_failure("increment", key)
            return 0

    def acquire_lock(self, key: str, ttl_seconds: int) -> bool:
        try:
            return bool(
                self._client.set(
                    self._key(key),
                    "1",
                    nx=True,
                    ex=ttl_seconds,
                )
            )
        except redis.RedisError:
            self._report_failure("acquire_lock", key)
            return True

    def record_event(self, key: str, window_seconds: int) -> int:
        namespaced_key = self._key(key)
        now = time.time()
        member = f"{now}:{uuid.uuid4().hex}"
        try:
            with self._client.pipeline(transaction=True) as pipeline:
                pipeline.zadd(namespaced_key, {member: now})
                pipeline.zremrangebyscore(
                    namespaced_key, "-inf", now - window_seconds
                )
                pipeline.zcard(namespaced_key)
                pipeline.expire(namespaced_key, window_seconds)
                _, _, count, _ = pipeline.execute()
            return int(count)
        except redis.RedisError:
            self._report_failure("record_event", key)
            return 0

    def _key(self, key: str) -> str:
        return f"{self._namespace}:{key}" if self._namespace else key

    @staticmethod
    def _report_failure(operation: str, key: str) -> None:
        logger.error(
            "state.redis.operation_failed: %s",
            {"operation": operation, "key": key},
            exc_info=True,
        )


@lru_cache(maxsize=4)
def _build_redis_store(
    redis_url: str, namespace: str, socket_timeout_seconds: float
) -> RedisStateStore:
    client = redis.Redis.from_url(
        redis_url,
        socket_connect_timeout=socket_timeout_seconds,
        socket_timeout=socket_timeout_seconds,
        health_check_interval=30,
    )
    logger.info(
        "state.redis.client_created: %s",
        {"namespace": namespace, "socketTimeoutSeconds": socket_timeout_seconds},
    )
    return RedisStateStore(client, namespace)


@lru_cache(maxsize=1)
def _build_null_state_store() -> NullStateStore:
    logger.warning("state.redis.disabled")
    return NullStateStore()


def build_state_store(settings: StateSettings) -> StateStore:
    """根据应用配置创建路由状态存储。

    参数：
        settings: Redis 地址、命名空间和超时设置。

    返回值：
        已配置 Redis 时返回 Redis 状态存储，否则返回无状态实现。
    """
    if not settings.redis_url:
        return _build_null_state_store()
    return _build_redis_store(
        settings.redis_url,
        settings.namespace,
        settings.socket_timeout_seconds,
    )
