from __future__ import annotations

import logging

from services.gemini_service import build_gemini_understand_plan, invoke_gemini
from services.request_parser import UnderstandImageRequest
from services.response_extractor import extract_text_from_gemini_response
from services.settings import get_app_settings

logger = logging.getLogger(__name__)


def process_understand_request(request_data: UnderstandImageRequest) -> dict[str, str]:
    settings = get_app_settings()
    plan = build_gemini_understand_plan(request_data, settings)

    logger.info("understand.pipeline.invocation_plan: %s", plan.to_dict())

    raw_response = invoke_gemini(plan, settings.api_key)
    text = extract_text_from_gemini_response(raw_response)

    return {
        "requestId": request_data.request_id,
        "model": plan.model,
        "text": text,
    }
