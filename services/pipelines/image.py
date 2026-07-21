from __future__ import annotations

import logging
from dataclasses import dataclass

from services.domain.requests import GenerateImageRequest
from services.http import build_asset_fetcher
from services.oss_service import upload_asset_to_oss
from services.response_normalizer import NormalizedGeneratedAsset
from services.routing import build_failover_router
from services.settings import AppSettings, get_app_settings

logger = logging.getLogger(__name__)


def process_image_request(
    request_data: GenerateImageRequest,
) -> dict[str, str | bool | list[str]]:
    """生成图片并上传至 OSS。

    参数：
        request_data: 已解析完成的图片生成请求。

    返回值：
        包含请求标识、公共模型、OSS 地址和服务商路由信息的结果。
    """
    settings = get_app_settings()
    route_result = build_failover_router(settings).generate_image(request_data)
    provider_result = route_result.provider_result
    normalized_result = provider_result.result

    upload_results = [
        upload_asset_to_oss(settings, asset) for asset in normalized_result.assets
    ]
    oss_urls = [item.object_url for item in upload_results]
    return {
        "requestId": request_data.request_id,
        "model": provider_result.public_model,
        "ossUrl": oss_urls[0] if oss_urls else "",
        "ossUrls": oss_urls,
        "provider": provider_result.provider,
        "fallbackUsed": route_result.fallback_used,
    }


@dataclass
class GeneratedImageFile:
    data: bytes
    mime_type: str
    file_name: str
    model: str
    provider: str
    fallback_used: bool


def _resolve_asset_bytes(
    asset: NormalizedGeneratedAsset, settings: AppSettings
) -> bytes:
    if asset.source_kind == "bytes":
        return (
            asset.payload
            if isinstance(asset.payload, bytes)
            else bytes(asset.payload)
        )
    if asset.source_kind == "url":
        return build_asset_fetcher(settings).fetch(str(asset.payload)).body
    return str(asset.payload).encode("utf-8")


def generate_image_only(request_data: GenerateImageRequest) -> GeneratedImageFile:
    """生成图片并直接返回文件数据。

    参数：
        request_data: 已解析完成的图片生成请求。

    返回值：
        包含图片字节、文件信息和服务商路由信息的结果。
    """
    settings = get_app_settings()
    route_result = build_failover_router(settings).generate_image(request_data)
    provider_result = route_result.provider_result
    asset = provider_result.result.assets[0]
    return GeneratedImageFile(
        data=_resolve_asset_bytes(asset, settings),
        mime_type=asset.mime_type,
        file_name=asset.file_name,
        model=provider_result.public_model,
        provider=provider_result.provider,
        fallback_used=route_result.fallback_used,
    )
