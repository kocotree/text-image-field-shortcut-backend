from __future__ import annotations

import logging

from services.domain.errors import ErrorCategory, ProviderError
from services.notifications.feishu import AlertMessage, FeishuAlertNotifier
from services.settings import AlertSettings
from services.state import StateStore

logger = logging.getLogger(__name__)


class RoutingEventReporter:
    """聚合路由事件并生成飞书告警与恢复通知。"""

    def __init__(
        self,
        store: StateStore,
        notifier: FeishuAlertNotifier,
        settings: AlertSettings,
    ) -> None:
        self._store = store
        self._notifier = notifier
        self._settings = settings

    def on_provider_failure(
        self,
        provider: str,
        capability: str,
        public_model: str,
        error: ProviderError,
        request_id: str,
    ) -> None:
        """记录服务商失败并发送严重配置类告警。

        参数：
            provider: 发生失败的服务商名称。
            capability: 发生失败的能力名称。
            public_model: 公共模型 ID。
            error: 标准服务商错误。
            request_id: 当前业务请求标识。

        返回值：
            无。
        """
        try:
            if not self._settings.enabled:
                return
            if self._is_health_failure(error):
                self._store.delete(self._recovery_key(provider, capability))
            if error.category not in {
                ErrorCategory.AUTHENTICATION,
                ErrorCategory.BILLING,
                ErrorCategory.PERMISSION,
            }:
                return
            self._mark_incident(provider, capability)
            self._send_with_cooldown(
                key=f"critical:{provider}:{capability}:{error.category}",
                cooldown_seconds=self._settings.critical_cooldown_seconds,
                message=self._message(
                    "Critical",
                    "服务商鉴权、余额或权限异常",
                    provider=provider,
                    capability=capability,
                    public_model=public_model,
                    error_category=error.category,
                    request_id=request_id,
                ),
            )
        except Exception:
            logger.error("notification.routing.provider_failure_failed", exc_info=True)

    def on_fallback_used(
        self,
        primary_provider: str,
        fallback_provider: str,
        capability: str,
        public_model: str,
        error_category: str,
        request_id: str,
    ) -> None:
        """记录一次成功兜底并在窗口内达到阈值时告警。

        参数：
            primary_provider: 主服务商名称。
            fallback_provider: 实际完成请求的兜底服务商名称。
            capability: 当前模型能力名称。
            public_model: 公共模型 ID。
            error_category: 触发兜底的主服务商错误分类。
            request_id: 当前业务请求标识。

        返回值：
            无。
        """
        try:
            if not self._settings.enabled:
                return
            self._mark_incident(primary_provider, capability)
            event_key = f"fallback:{primary_provider}:{capability}:events"
            count = self._store.record_event(
                event_key, self._settings.fallback_window_seconds
            )
            logger.warning(
                "notification.routing.fallback_recorded: %s",
                {
                    "primaryProvider": primary_provider,
                    "fallbackProvider": fallback_provider,
                    "capability": capability,
                    "count": count,
                },
            )
            if count < self._settings.fallback_threshold:
                return
            self._send_with_cooldown(
                key=f"fallback:{primary_provider}:{capability}",
                cooldown_seconds=self._settings.fallback_cooldown_seconds,
                message=self._message(
                    "Warning",
                    "服务商兜底频率过高",
                    provider=primary_provider,
                    fallback_provider=fallback_provider,
                    capability=capability,
                    public_model=public_model,
                    error_category=error_category,
                    fallback_count=str(count),
                    request_id=request_id,
                ),
            )
        except Exception:
            logger.error("notification.routing.fallback_failed", exc_info=True)

    def on_circuit_open(
        self,
        provider: str,
        capability: str,
        public_model: str,
        error_category: str,
        request_id: str,
    ) -> None:
        """在熔断器打开时发送告警。

        参数：
            provider: 进入熔断状态的服务商名称。
            capability: 熔断的能力名称。
            public_model: 公共模型 ID。
            error_category: 导致熔断的错误分类。
            request_id: 当前业务请求标识。

        返回值：
            无。
        """
        try:
            if not self._settings.enabled:
                return
            self._mark_incident(provider, capability)
            self._send_with_cooldown(
                key=f"circuit:{provider}:{capability}",
                cooldown_seconds=self._settings.fallback_cooldown_seconds,
                message=self._message(
                    "Warning",
                    "服务商熔断器已打开",
                    provider=provider,
                    capability=capability,
                    public_model=public_model,
                    error_category=error_category,
                    request_id=request_id,
                ),
            )
        except Exception:
            logger.error("notification.routing.circuit_failed", exc_info=True)

    def on_all_providers_failed(
        self,
        primary_provider: str,
        capability: str,
        public_model: str,
        error_category: str,
        request_id: str,
    ) -> None:
        """在主备服务商均失败时发送严重告警。

        参数：
            primary_provider: 主服务商名称。
            capability: 当前模型能力名称。
            public_model: 公共模型 ID。
            error_category: 最后一次失败的错误分类。
            request_id: 当前业务请求标识。

        返回值：
            无。
        """
        try:
            if not self._settings.enabled:
                return
            self._mark_incident(primary_provider, capability)
            self._send_with_cooldown(
                key=f"all_failed:{primary_provider}:{capability}",
                cooldown_seconds=self._settings.critical_cooldown_seconds,
                message=self._message(
                    "Critical",
                    "主备服务商均调用失败",
                    provider=primary_provider,
                    capability=capability,
                    public_model=public_model,
                    error_category=error_category,
                    request_id=request_id,
                ),
            )
        except Exception:
            logger.error("notification.routing.all_failed_failed", exc_info=True)

    def on_primary_success(
        self,
        provider: str,
        capability: str,
        public_model: str,
        request_id: str,
    ) -> None:
        """累计主服务商恢复成功次数并发送一次恢复通知。

        参数：
            provider: 主服务商名称。
            capability: 恢复的能力名称。
            public_model: 公共模型 ID。
            request_id: 当前业务请求标识。

        返回值：
            无。
        """
        try:
            if not self._settings.enabled:
                return
            incident_key = self._incident_key(provider, capability)
            if not self._store.get(incident_key):
                return
            recovery_key = self._recovery_key(provider, capability)
            count = self._store.increment(
                recovery_key, self._settings.incident_ttl_seconds
            )
            if count < self._settings.recovery_success_threshold:
                return
            if not self._store.acquire_lock(
                f"alert:recovery:{provider}:{capability}",
                self._settings.fallback_cooldown_seconds,
            ):
                return
            sent = self._notifier.send(
                self._message(
                    "Recovery",
                    "主服务商已恢复",
                    provider=provider,
                    capability=capability,
                    public_model=public_model,
                    recovery_count=str(count),
                    request_id=request_id,
                )
            )
            if sent:
                self._store.delete(incident_key, recovery_key)
            else:
                self._store.delete(f"alert:recovery:{provider}:{capability}")
        except Exception:
            logger.error("notification.routing.recovery_failed", exc_info=True)

    def _mark_incident(self, provider: str, capability: str) -> None:
        self._store.set(
            self._incident_key(provider, capability),
            "1",
            self._settings.incident_ttl_seconds,
        )
        self._store.delete(self._recovery_key(provider, capability))

    def _send_with_cooldown(
        self, key: str, cooldown_seconds: int, message: AlertMessage
    ) -> bool:
        if not self._settings.enabled:
            return False
        if not self._store.acquire_lock(f"alert:{key}", cooldown_seconds):
            return False
        sent = self._notifier.send(message)
        if not sent:
            self._store.delete(f"alert:{key}")
        return sent

    def _message(self, level: str, title: str, **fields: str) -> AlertMessage:
        base_fields = (
            ("环境", self._settings.environment),
            ("服务", self._settings.service_name),
        )
        labels = {
            "provider": "服务商",
            "fallback_provider": "兜底服务商",
            "capability": "能力",
            "public_model": "公共模型",
            "error_category": "错误分类",
            "fallback_count": "窗口兜底次数",
            "recovery_count": "连续恢复次数",
            "request_id": "示例 requestId",
        }
        event_fields = tuple(
            (labels.get(name, name), self._safe_field(value))
            for name, value in fields.items()
            if str(value)
        )
        safe_base_fields = tuple(
            (name, self._safe_field(value)) for name, value in base_fields
        )
        return AlertMessage(level=level, title=title, fields=safe_base_fields + event_fields)

    @staticmethod
    def _incident_key(provider: str, capability: str) -> str:
        return f"incident:{provider}:{capability}"

    @staticmethod
    def _recovery_key(provider: str, capability: str) -> str:
        return f"recovery:{provider}:{capability}"

    @staticmethod
    def _is_health_failure(error: ProviderError) -> bool:
        return error.counts_toward_circuit and error.category in {
            ErrorCategory.CONNECTION,
            ErrorCategory.TIMEOUT,
            ErrorCategory.RATE_LIMIT,
            ErrorCategory.UPSTREAM_UNAVAILABLE,
            ErrorCategory.INVALID_RESPONSE,
            ErrorCategory.EMPTY_RESPONSE,
            ErrorCategory.AUTHENTICATION,
            ErrorCategory.BILLING,
            ErrorCategory.PERMISSION,
        }

    @staticmethod
    def _safe_field(value: object) -> str:
        return str(value).replace("\r", " ").replace("\n", " ")[:200]
