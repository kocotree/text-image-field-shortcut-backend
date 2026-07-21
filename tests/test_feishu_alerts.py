from __future__ import annotations

import base64
import hashlib
import hmac
import json
import unittest

import httpx

from services.domain.errors import ErrorCategory, ProviderError
from services.notifications.feishu import (
    AlertMessage,
    FeishuAlertNotifier,
    build_feishu_signature,
)
from services.notifications.routing_events import RoutingEventReporter
from services.settings import AlertSettings, HttpClientSettings


class MemoryEventStore:
    available = True

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.locks: set[str] = set()
        self.events: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str, ttl_seconds: int) -> bool:
        self.values[key] = value
        return True

    def delete(self, *keys: str) -> bool:
        for key in keys:
            self.values.pop(key, None)
            self.locks.discard(key)
        return True

    def increment(self, key: str, ttl_seconds: int) -> int:
        count = int(self.values.get(key, "0")) + 1
        self.values[key] = str(count)
        return count

    def acquire_lock(self, key: str, ttl_seconds: int) -> bool:
        if key in self.locks:
            return False
        self.locks.add(key)
        return True

    def record_event(self, key: str, window_seconds: int) -> int:
        count = self.events.get(key, 0) + 1
        self.events[key] = count
        return count


class CollectingNotifier:
    def __init__(self) -> None:
        self.messages: list[AlertMessage] = []

    def send(self, message: AlertMessage) -> bool:
        self.messages.append(message)
        return True


def _alert_settings() -> AlertSettings:
    return AlertSettings(
        enabled=True,
        webhook_url="https://feishu.example/hook",
        secret="secret",
        service_name="image-service",
        environment="test",
        fallback_window_seconds=300,
        fallback_threshold=2,
        fallback_cooldown_seconds=900,
        critical_cooldown_seconds=60,
        recovery_success_threshold=2,
        incident_ttl_seconds=3600,
    )


class FeishuAlertNotifierTestCase(unittest.TestCase):
    def test_signature_matches_feishu_algorithm(self) -> None:
        timestamp = 1_700_000_000
        key = f"{timestamp}\nsecret".encode("utf-8")
        expected = base64.b64encode(
            hmac.new(key, b"", hashlib.sha256).digest()
        ).decode("ascii")

        self.assertEqual(build_feishu_signature(timestamp, "secret"), expected)

    def test_send_uses_signed_text_payload(self) -> None:
        captured_request: httpx.Request | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(200, json={"code": 0})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            notifier = FeishuAlertNotifier(
                _alert_settings(),
                HttpClientSettings(),
                client,
                clock=lambda: 1_700_000_000,
            )
            sent = notifier.send(
                AlertMessage(
                    level="Warning",
                    title="服务商兜底频率过高",
                    fields=(("服务商", "easyrouter"),),
                )
            )

        self.assertTrue(sent)
        self.assertIsNotNone(captured_request)
        payload = json.loads(captured_request.content)
        self.assertEqual(payload["timestamp"], "1700000000")
        self.assertEqual(payload["msg_type"], "text")
        self.assertNotIn("secret", captured_request.content.decode("utf-8"))

    def test_send_failure_returns_false(self) -> None:
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(500, json={"code": 1})
        )
        with httpx.Client(transport=transport) as client:
            notifier = FeishuAlertNotifier(
                _alert_settings(), HttpClientSettings(), client
            )
            with self.assertLogs(
                "services.notifications.feishu", level="ERROR"
            ) as captured_logs:
                self.assertFalse(
                    notifier.send(AlertMessage("Critical", "发送失败", ()))
                )

        self.assertNotIn(
            "https://feishu.example/hook", "\n".join(captured_logs.output)
        )


class RoutingEventReporterTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryEventStore()
        self.notifier = CollectingNotifier()
        self.reporter = RoutingEventReporter(
            self.store,
            self.notifier,
            _alert_settings(),
        )

    def test_fallback_threshold_is_aggregated_and_cooled_down(self) -> None:
        for request_id in ("request-1", "request-2", "request-3"):
            self.reporter.on_fallback_used(
                "easyrouter",
                "openrouter",
                "image_generation",
                "gemini-3.1-flash-image",
                "timeout",
                request_id,
            )

        self.assertEqual(len(self.notifier.messages), 1)
        self.assertEqual(self.notifier.messages[0].level, "Warning")

    def test_critical_provider_error_uses_independent_cooldown(self) -> None:
        error = ProviderError(
            provider="easyrouter",
            category=ErrorCategory.BILLING,
            message="billing",
            retryable=True,
        )

        self.reporter.on_provider_failure(
            "easyrouter",
            "image_generation",
            "gemini-3.1-flash-image",
            error,
            "request-1",
        )
        self.reporter.on_provider_failure(
            "easyrouter",
            "image_generation",
            "gemini-3.1-flash-image",
            error,
            "request-2",
        )

        self.assertEqual(len(self.notifier.messages), 1)
        self.assertEqual(self.notifier.messages[0].level, "Critical")

    def test_recovery_requires_consecutive_successes_and_sends_once(self) -> None:
        self.reporter.on_fallback_used(
            "easyrouter",
            "openrouter",
            "image_generation",
            "gemini-3.1-flash-image",
            "timeout",
            "request-1",
        )
        self.reporter.on_primary_success(
            "easyrouter",
            "image_generation",
            "gemini-3.1-flash-image",
            "request-2",
        )
        self.reporter.on_primary_success(
            "easyrouter",
            "image_generation",
            "gemini-3.1-flash-image",
            "request-3",
        )
        self.reporter.on_primary_success(
            "easyrouter",
            "image_generation",
            "gemini-3.1-flash-image",
            "request-4",
        )

        recovery_messages = [
            message
            for message in self.notifier.messages
            if message.level == "Recovery"
        ]
        self.assertEqual(len(recovery_messages), 1)


if __name__ == "__main__":
    unittest.main()
