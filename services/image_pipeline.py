from __future__ import annotations

import logging

from services.gemini_service import build_gemini_invocation_plan, invoke_gemini
from services.oss_service import upload_asset_to_oss
from services.request_parser import GenerateImageRequest
from services.response_normalizer import normalize_gemini_response
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
