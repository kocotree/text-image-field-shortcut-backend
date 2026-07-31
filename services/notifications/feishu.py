from __future__ import annotations

from dataclasses import dataclass
import logging

import httpx

from services.http import get_http_client
from services.settings import AlertSettings, HttpClientSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlertMessage:
    level: str
    title: str
    fields: tuple[tuple[str, str], ...]


class FeishuAlertNotifier:
    """通过飞书自定义机器人发送脱敏告警。"""

    def __init__(
        self,
        alert_settings: AlertSettings,
        http_settings: HttpClientSettings,
        client: httpx.Client | None = None,
    ) -> None:
        self._settings = alert_settings
        self._http_settings = http_settings
        self._client = client

    def send(self, message: AlertMessage) -> bool:
        """发送飞书告警且不向业务链路抛出异常。

        参数：
            message: 已脱敏的告警级别、标题和字段。

        返回值：
            飞书明确确认成功时返回真，否则返回假。
        """
        if not self._settings.enabled:
            return False
        if not self._settings.webhook_url or not self._settings.keyword:
            logger.error("notification.feishu.configuration_invalid")
            return False

        field_lines = "\n".join(
            f"{name}: {value}" for name, value in message.fields
        )
        text = f"[{message.level}] {message.title}"
        text = f"{self._settings.keyword} {text}"
        if field_lines:
            text = f"{text}\n{field_lines}"
        payload = {
            "msg_type": "text",
            "content": {"text": text},
        }
        logger.debug(
            "notification.feishu.send.start: %s",
            {"level": message.level, "title": message.title},
        )
        try:
            client = self._client or get_http_client(
                "feishu", self._http_settings
            )
            response = client.post(self._settings.webhook_url, json=payload)
            response.raise_for_status()
            response_payload = response.json()
            success = (
                response_payload.get("code") == 0
                or response_payload.get("StatusCode") == 0
            )
            if not success:
                business_code = response_payload.get(
                    "code", response_payload.get("StatusCode")
                )
                business_message = response_payload.get(
                    "msg", response_payload.get("StatusMessage")
                )
                logger.error(
                    "notification.feishu.send.rejected: %s",
                    {
                        "statusCode": response.status_code,
                        "businessCode": business_code,
                        "businessMessage": business_message,
                    },
                )
                return False
            logger.info(
                "notification.feishu.send.success: %s",
                {"level": message.level, "title": message.title},
            )
            return True
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            status_code = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else None
            )
            logger.error(
                "notification.feishu.send.failed: %s",
                {
                    "errorType": type(exc).__name__,
                    "statusCode": status_code,
                },
            )
            return False
