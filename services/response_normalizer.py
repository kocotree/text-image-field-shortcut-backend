from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
from dataclasses import dataclass
from typing import Any

from services.gemini_service import GeminiRawResponse

logger = logging.getLogger(__name__)


def _is_json_content_type(content_type: str) -> bool:
    return "application/json" in str(content_type or "").lower()


def _is_binary_content_type(content_type: str, content_disposition: str) -> bool:
    normalized_type = str(content_type or "").lower()
    normalized_disposition = str(content_disposition or "").lower()
    if "attachment" in normalized_disposition:
        return True
    if not normalized_type:
        return False
    if _is_json_content_type(normalized_type) or normalized_type.startswith("text/"):
        return False
    return (
        normalized_type.startswith(("image/", "audio/", "video/"))
        or "application/octet-stream" in normalized_type
        or "application/pdf" in normalized_type
        or "application/zip" in normalized_type
    )


def _parse_file_name_from_disposition(content_disposition: str) -> str:
    disposition = str(content_disposition or "")
    utf8_match = re.search(
        r"filename\*=UTF-8''([^;]+)", disposition, flags=re.IGNORECASE
    )
    if utf8_match:
        return utf8_match.group(1)
    plain_match = re.search(r'filename="?([^";]+)"?', disposition, flags=re.IGNORECASE)
    if plain_match:
        return plain_match.group(1)
    return ""


def _guess_extension(content_type: str) -> str:
    extension = (
        mimetypes.guess_extension(str(content_type or "").split(";")[0].strip())
        or ".bin"
    )
    return extension


def _build_default_file_name(content_type: str, content_disposition: str) -> str:
    return (
        _parse_file_name_from_disposition(content_disposition)
        or f"generated-output{_guess_extension(content_type)}"
    )


def _build_generated_file_name(mime_type: str, index: int, asset_count: int) -> str:
    suffix = f"-{index + 1}" if asset_count > 1 else ""
    return f"generated-output{suffix}{_guess_extension(mime_type)}"


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


def _find_url_payloads(value: Any) -> list[str]:
    payloads: list[str] = []
    if isinstance(value, str):
        markdown_matches = re.findall(
            r"!\[[^\]]*\]\((https?://[^)\s]+)\)",
            value,
            flags=re.IGNORECASE,
        )
        if markdown_matches:
            return markdown_matches
        return re.findall(r"https?://[^\s)\"'<>]+", value, flags=re.IGNORECASE)
    if isinstance(value, list):
        for item in value:
            payloads.extend(_find_url_payloads(item))
    if isinstance(value, dict):
        handled_keys: set[str] = set()
        for key in ("url", "link", "download_url", "image_url", "href"):
            if key in value:
                handled_keys.add(key)
                payloads.extend(_find_url_payloads(value.get(key)))
        for key, item in value.items():
            if key not in handled_keys:
                payloads.extend(_find_url_payloads(item))
    return payloads


def _find_base64_payloads(value: Any) -> list[tuple[str, str]]:
    payloads: list[tuple[str, str]] = []
    if isinstance(value, str):
        markdown_matches = re.findall(
            r"!\[[^\]]*\]\(data:([^;]+);base64,([^)]+)\)", value
        )
        if markdown_matches:
            return markdown_matches
        data_url_match = re.match(r"^data:([^;]+);base64,([\s\S]+)$", value)
        if data_url_match:
            return [(data_url_match.group(1), data_url_match.group(2))]
    if isinstance(value, list):
        for item in value:
            payloads.extend(_find_base64_payloads(item))
    if isinstance(value, dict):
        handled_keys: set[str] = set()
        if isinstance(value.get("b64_json"), str):
            handled_keys.add("b64_json")
            payloads.append(("image/png", value["b64_json"]))
        if isinstance(value.get("base64"), str):
            handled_keys.add("base64")
            payloads.append(("image/png", value["base64"]))
        if isinstance(value.get("image_base64"), str):
            handled_keys.add("image_base64")
            payloads.append(("image/png", value["image_base64"]))
        inline_data = value.get("inline_data")
        if isinstance(inline_data, dict) and isinstance(inline_data.get("data"), str):
            handled_keys.add("inline_data")
            payloads.append(
                (
                    str(inline_data.get("mime_type") or "image/png"),
                    inline_data["data"],
                )
            )
        inline_data = value.get("inlineData")
        if isinstance(inline_data, dict) and isinstance(inline_data.get("data"), str):
            handled_keys.add("inlineData")
            payloads.append(
                (
                    str(inline_data.get("mimeType") or "image/png"),
                    inline_data["data"],
                )
            )
        for key, item in value.items():
            if key not in handled_keys:
                payloads.extend(_find_base64_payloads(item))
    return payloads


@dataclass
class NormalizedGeneratedAsset:
    asset_type: str
    mime_type: str
    file_name: str
    source_kind: str
    payload: bytes | str

    def to_dict(self) -> dict[str, Any]:
        return {
            "assetType": self.asset_type,
            "mimeType": self.mime_type,
            "fileName": self.file_name,
            "sourceKind": self.source_kind,
            "payloadSize": len(self.payload)
            if isinstance(self.payload, bytes)
            else len(str(self.payload)),
        }


@dataclass
class NormalizedModelResult:
    raw_response_type: str
    assets: list[NormalizedGeneratedAsset]
    text_output: str
    raw_meta: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rawResponseType": self.raw_response_type,
            "assetCount": len(self.assets),
            "assets": [item.to_dict() for item in self.assets],
            "textOutput": self.text_output,
            "rawMeta": self.raw_meta,
        }


def normalize_gemini_response(raw_response: GeminiRawResponse) -> NormalizedModelResult:
    """将服务商原始响应转换为统一的图片资源列表。

    参数：
        raw_response: 服务商返回的状态、响应头和原始响应体。

    返回值：
        包含全部生成图片、文本输出和原始元数据的标准结果。
    """
    content_type = raw_response.content_type
    content_disposition = raw_response.content_disposition
    raw_meta = raw_response.to_dict()

    if _is_binary_content_type(content_type, content_disposition):
        result = NormalizedModelResult(
            raw_response_type="binary",
            assets=[
                NormalizedGeneratedAsset(
                    asset_type="binary_file",
                    mime_type=content_type or "application/octet-stream",
                    file_name=_build_default_file_name(
                        content_type, content_disposition
                    ),
                    source_kind="bytes",
                    payload=raw_response.body,
                )
            ],
            text_output="",
            raw_meta=raw_meta,
        )
        return result

    response_text = raw_response.body.decode("utf-8", errors="ignore")

    if _is_json_content_type(content_type):
        json_value = json.loads(response_text)

        api_error = json_value.get("error")
        if api_error:
            if isinstance(api_error, dict):
                raise RuntimeError(
                    str(
                        api_error.get("message")
                        or json.dumps(api_error, ensure_ascii=False)
                    )
                )
            raise RuntimeError(str(api_error))
        if json_value.get("message") and json_value.get("code"):
            raise RuntimeError(str(json_value.get("message")))

        base64_payloads = _find_base64_payloads(json_value)
        if base64_payloads:
            asset_count = len(base64_payloads)
            result = NormalizedModelResult(
                raw_response_type="json_base64",
                assets=[
                    NormalizedGeneratedAsset(
                        asset_type="image_base64",
                        mime_type=mime_type or "image/png",
                        file_name=_build_generated_file_name(
                            mime_type or "image/png", index, asset_count
                        ),
                        source_kind="bytes",
                        payload=base64.b64decode(base64_payload),
                    )
                    for index, (mime_type, base64_payload) in enumerate(base64_payloads)
                ],
                text_output="",
                raw_meta=raw_meta,
            )
            logger.debug(
                "image.response.normalized: %s",
                {
                    "rawResponseType": result.raw_response_type,
                    "assetCount": asset_count,
                },
            )
            return result

        url_payloads = _find_url_payloads(json_value)
        if url_payloads:
            asset_count = len(url_payloads)
            result = NormalizedModelResult(
                raw_response_type="json_url",
                assets=[
                    NormalizedGeneratedAsset(
                        asset_type="image_url",
                        mime_type="image/png",
                        file_name=_build_generated_file_name(
                            "image/png", index, asset_count
                        ),
                        source_kind="url",
                        payload=url_payload,
                    )
                    for index, url_payload in enumerate(url_payloads)
                ],
                text_output="",
                raw_meta=raw_meta,
            )
            logger.debug(
                "image.response.normalized: %s",
                {
                    "rawResponseType": result.raw_response_type,
                    "assetCount": asset_count,
                },
            )
            return result

        assistant_text = _extract_assistant_text(json_value)
        assistant_urls = _find_url_payloads(assistant_text)
        if assistant_urls:
            asset_count = len(assistant_urls)
            result = NormalizedModelResult(
                raw_response_type="assistant_url",
                assets=[
                    NormalizedGeneratedAsset(
                        asset_type="image_url",
                        mime_type="image/png",
                        file_name=_build_generated_file_name(
                            "image/png", index, asset_count
                        ),
                        source_kind="url",
                        payload=assistant_url,
                    )
                    for index, assistant_url in enumerate(assistant_urls)
                ],
                text_output=assistant_text,
                raw_meta=raw_meta,
            )
            logger.debug(
                "image.response.normalized: %s",
                {
                    "rawResponseType": result.raw_response_type,
                    "assetCount": len(result.assets),
                },
            )
            return result

        result = NormalizedModelResult(
            raw_response_type="json_text",
            assets=[
                NormalizedGeneratedAsset(
                    asset_type="text_file",
                    mime_type="text/plain; charset=utf-8",
                    file_name="generated-output.txt",
                    source_kind="text",
                    payload=assistant_text or response_text,
                )
            ],
            text_output=assistant_text or response_text,
            raw_meta=raw_meta,
        )
        return result

    direct_urls = _find_url_payloads(response_text)
    if direct_urls:
        asset_count = len(direct_urls)
        result = NormalizedModelResult(
            raw_response_type="text_url",
            assets=[
                NormalizedGeneratedAsset(
                    asset_type="image_url",
                    mime_type="image/png",
                    file_name=_build_generated_file_name(
                        "image/png", index, asset_count
                    ),
                    source_kind="url",
                    payload=direct_url,
                )
                for index, direct_url in enumerate(direct_urls)
            ],
            text_output=response_text,
            raw_meta=raw_meta,
        )
        logger.debug(
            "image.response.normalized: %s",
            {
                "rawResponseType": result.raw_response_type,
                "assetCount": len(result.assets),
            },
        )
        return result

    result = NormalizedModelResult(
        raw_response_type="text",
        assets=[
            NormalizedGeneratedAsset(
                asset_type="text_file",
                mime_type="text/plain; charset=utf-8",
                file_name="generated-output.txt",
                source_kind="text",
                payload=response_text,
            )
        ],
        text_output=response_text,
        raw_meta=raw_meta,
    )
    return result
