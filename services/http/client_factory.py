from __future__ import annotations

import atexit
import logging
import os
from threading import RLock

import httpx

from services.settings import HttpClientSettings

logger = logging.getLogger(__name__)


def create_http_client(settings: HttpClientSettings) -> httpx.Client:
    """创建同步 HTTP 客户端。

    参数：
        settings: 客户端超时、连接池和重定向配置。

    返回值：
        可在线程间共享并长期复用的同步 HTTPX 客户端。
    """
    timeout = settings.timeout
    return httpx.Client(
        timeout=httpx.Timeout(
            connect=timeout.connect,
            read=timeout.read,
            write=timeout.write,
            pool=timeout.pool,
        ),
        limits=httpx.Limits(
            max_connections=settings.max_connections,
            max_keepalive_connections=settings.max_keepalive_connections,
        ),
        follow_redirects=False,
        max_redirects=settings.max_redirects,
        trust_env=settings.trust_env,
    )


class HttpClientRegistry:
    """按用途管理当前 worker 内长期复用的 HTTPX 客户端。"""

    def __init__(self) -> None:
        self._pid = os.getpid()
        self._clients: dict[str, httpx.Client] = {}
        self._lock = RLock()

    def get(self, name: str, settings: HttpClientSettings) -> httpx.Client:
        """获取指定用途的客户端。

        参数：
            name: 客户端用途名称，例如 provider、asset 或 auth。
            settings: 创建客户端时使用的配置。

        返回值：
            当前 worker 内与用途名称对应的共享客户端。
        """
        with self._lock:
            self._reset_after_fork()
            client = self._clients.get(name)
            if client is None:
                client = create_http_client(settings)
                self._clients[name] = client
                logger.info("http.client.created: %s", {"name": name, "pid": self._pid})
            return client

    def close(self) -> None:
        """关闭当前进程中已创建的全部客户端。"""
        with self._lock:
            for name, client in self._clients.items():
                try:
                    client.close()
                    logger.info("http.client.closed: %s", {"name": name, "pid": self._pid})
                except Exception:
                    logger.warning("http.client.close_failed: %s", {"name": name}, exc_info=True)
            self._clients.clear()

    def _reset_after_fork(self) -> None:
        current_pid = os.getpid()
        if current_pid == self._pid:
            return
        for client in self._clients.values():
            try:
                client.close()
            except Exception:
                logger.warning("http.client.inherited_close_failed", exc_info=True)
        self._clients.clear()
        self._pid = current_pid
        logger.info("http.client.registry_reset_after_fork: %s", {"pid": current_pid})


_registry = HttpClientRegistry()


def get_http_client(name: str, settings: HttpClientSettings) -> httpx.Client:
    """获取当前 worker 的共享 HTTP 客户端。

    参数：
        name: 客户端用途名称。
        settings: 客户端配置。

    返回值：
        长期复用的同步 HTTPX 客户端。
    """
    return _registry.get(name, settings)


def close_http_clients() -> None:
    """关闭当前 worker 已创建的全部 HTTP 客户端。"""
    _registry.close()


atexit.register(close_http_clients)
