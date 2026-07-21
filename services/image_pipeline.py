from __future__ import annotations

import logging
from dataclasses import dataclass

from services.gemini_service import build_gemini_invocation_plan, invoke_gemini
from services.http import build_asset_fetcher
from services.openai_image_service import build_openai_image_invocation_plan, invoke_openai_image
from services.oss_service import upload_asset_to_oss
from services.request_parser import GenerateImageRequest
from services.response_normalizer import NormalizedGeneratedAsset, NormalizedModelResult, normalize_gemini_response
from services.settings import AppSettings, get_app_settings

logger = logging.getLogger(__name__)


def _has_image_content(result: NormalizedModelResult) -> bool:
    return any(
        asset.asset_type in ("binary_file", "image_base64", "image_url")
        for asset in result.assets
    )


def _is_openai_image_model(model: str, settings: AppSettings) -> bool:
    resolved = model or settings.default_model_id
    return resolved.startswith("gpt-image")


def _invoke_and_normalize(request_data: GenerateImageRequest, settings: AppSettings) -> tuple[NormalizedModelResult, str]:
    if _is_openai_image_model(request_data.model, settings):
        plan = build_openai_image_invocation_plan(request_data, settings)
        raw_response = invoke_openai_image(plan, settings.api_key, settings.http.provider)
        return normalize_gemini_response(raw_response), plan.model
    else:
        plan = build_gemini_invocation_plan(request_data, settings)
        raw_response = invoke_gemini(plan, settings.api_key, settings.http.provider)
        return normalize_gemini_response(raw_response), plan.model


def process_image_request(request_data: GenerateImageRequest) -> dict[str, str | list[str]]:
    settings = get_app_settings()

    normalized_result, resolved_model = _invoke_and_normalize(request_data, settings)

    if not _has_image_content(normalized_result):
        logger.warning("gemini.backend.pipeline.empty_response, retrying once: %s", normalized_result.to_dict())
        normalized_result, resolved_model = _invoke_and_normalize(request_data, settings)

    if not _has_image_content(normalized_result):
        raise RuntimeError("图片生成失败，模型返回了空内容，请稍后重试")

    upload_results = [upload_asset_to_oss(settings, asset) for asset in normalized_result.assets]
    oss_urls = [item.object_url for item in upload_results]

    return {
        "requestId": request_data.request_id,
        "model": resolved_model,
        "ossUrl": oss_urls[0] if oss_urls else "",
        "ossUrls": oss_urls,
    }


@dataclass
class GeneratedImageFile:
    data: bytes
    mime_type: str
    file_name: str


def _resolve_asset_bytes(asset: NormalizedGeneratedAsset, settings: AppSettings) -> bytes:
    if asset.source_kind == "bytes":
        return asset.payload if isinstance(asset.payload, bytes) else bytes(asset.payload)
    if asset.source_kind == "url":
        return build_asset_fetcher(settings).fetch(str(asset.payload)).body
    return str(asset.payload).encode("utf-8")


def generate_image_only(request_data: GenerateImageRequest) -> GeneratedImageFile:
    settings = get_app_settings()

    normalized_result, _ = _invoke_and_normalize(request_data, settings)

    if not _has_image_content(normalized_result):
        logger.warning("gemini.backend.pipeline.empty_response, retrying once: %s", normalized_result.to_dict())
        normalized_result, _ = _invoke_and_normalize(request_data, settings)

    if not _has_image_content(normalized_result):
        raise RuntimeError("图片生成失败，模型返回了空内容，请稍后重试")

    asset = normalized_result.assets[0]
    return GeneratedImageFile(
        data=_resolve_asset_bytes(asset, settings),
        mime_type=asset.mime_type,
        file_name=asset.file_name,
    )
