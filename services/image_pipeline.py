from __future__ import annotations

import logging

from services.maibao_service import build_maibao_invocation_plan, invoke_maibao
from services.oss_service import upload_asset_to_oss
from services.request_parser import GenerateImageRequest
from services.response_normalizer import normalize_maibao_response
from services.settings import get_app_settings

logger = logging.getLogger(__name__)


def process_image_request(request_data: GenerateImageRequest, api_key: str = "") -> dict[str, str | list[str]]:
    settings = get_app_settings()

    maibao_plan = build_maibao_invocation_plan(request_data, settings)
    raw_response = invoke_maibao(maibao_plan, api_key)
    normalized_result = normalize_maibao_response(raw_response)

    upload_results = [upload_asset_to_oss(settings, asset) for asset in normalized_result.assets]
    oss_urls = [item.object_url for item in upload_results]

    return {
        "requestId": request_data.request_id,
        "model": maibao_plan.model,
        "ossUrl": oss_urls[0] if oss_urls else "",
        "ossUrls": oss_urls,
    }
