from __future__ import annotations

import json
import logging
from typing import Any

from services.gemini_service import GeminiRawResponse

logger = logging.getLogger(__name__)


def _extract_assistant_text(value: Any) -> str:
    message_content = value.get("choices", [{}])[0].get("message", {}).get("content")
    if isinstance(message_content, str):
        return message_content.strip()
    if isinstance(message_content, list):
        return "\n".join(
            item if isinstance(item, str) else str(item.get("text", ""))
            for item in message_content
        ).strip()
    candidate_parts = (
        value.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        if isinstance(value, dict)
        else []
    )
    if isinstance(candidate_parts, list):
        return "\n".join(
            str(item.get("text", "")).strip()
            for item in candidate_parts
            if isinstance(item, dict) and str(item.get("text", "")).strip()
        ).strip()
    return ""


def extract_text_from_gemini_response(raw_response: GeminiRawResponse) -> str:
    response_text = raw_response.body.decode("utf-8", errors="ignore")
    json_value = json.loads(response_text)

    api_error = json_value.get("error")
    if api_error:
        if isinstance(api_error, dict):
            raise RuntimeError(str(api_error.get("message") or json.dumps(api_error, ensure_ascii=False)))
        raise RuntimeError(str(api_error))
    if json_value.get("message") and json_value.get("code"):
        raise RuntimeError(str(json_value.get("message")))

    text = _extract_assistant_text(json_value)
    if not text:
        raise RuntimeError("Gemini response did not contain any text content.")
    return text
