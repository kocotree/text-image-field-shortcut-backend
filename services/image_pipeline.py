from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib import request as url_request

from services.gemini_service import build_gemini_invocation_plan, invoke_gemini
from services.oss_service import upload_asset_to_oss
from services.request_parser import GenerateImageRequest
from services.response_normalizer import NormalizedGeneratedAsset, normalize_gemini_response
from services.settings import get_app_settings

logger = logging.getLogger(__name__)


def process_image_request(request_data: GenerateImageRequest) -> dict[str, str | list[str]]:
    settings = get_app_settings()

    gemini_plan = build_gemini_invocation_plan(request_data, settings)
    raw_response = invoke_gemini(gemini_plan, settings.api_key)
    normalized_result = normalize_gemini_response(raw_response)

    upload_results = [upload_asset_to_oss(settings, asset) for asset in normalized_result.assets]
    oss_urls = [item.object_url for item in upload_results]

    return {
        "requestId": request_data.request_id,
        "model": gemini_plan.model,
        "ossUrl": oss_urls[0] if oss_urls else "",
        "ossUrls": oss_urls,
    }


@dataclass
class GeneratedImageFile:
    data: bytes
    mime_type: str
    file_name: str


def _resolve_asset_bytes(asset: NormalizedGeneratedAsset) -> bytes:
    if asset.source_kind == "bytes":
        return asset.payload if isinstance(asset.payload, bytes) else bytes(asset.payload)
    if asset.source_kind == "url":
        with url_request.urlopen(str(asset.payload), timeout=120) as resp:
            return resp.read()
    return str(asset.payload).encode("utf-8")


def generate_image_only(request_data: GenerateImageRequest) -> GeneratedImageFile:
    settings = get_app_settings()

    gemini_plan = build_gemini_invocation_plan(request_data, settings)
    raw_response = invoke_gemini(gemini_plan, settings.api_key)
    normalized_result = normalize_gemini_response(raw_response)

    if not normalized_result.assets:
        raise RuntimeError("No image generated")

    asset = normalized_result.assets[0]
    return GeneratedImageFile(
        data=_resolve_asset_bytes(asset),
        mime_type=asset.mime_type,
        file_name=asset.file_name,
    )
