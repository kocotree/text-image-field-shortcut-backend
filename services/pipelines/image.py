from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace

from services.domain.requests import GenerateImageRequest, RequestValidationError
from services.http import build_asset_fetcher
from services.oss_service import upload_asset_to_oss
from services.reference_images import materialize_reference_images
from services.response_normalizer import NormalizedGeneratedAsset
from services.routing import FailoverRouter, build_failover_router
from services.settings import AppSettings, get_app_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeneratedBatchItem:
    index: int
    asset: NormalizedGeneratedAsset
    model: str
    provider: str
    fallback_used: bool


def _build_batch_prompt(prompt: str, index: int, total: int) -> str:
    """为多图子任务补充当前图片序号。

    参数：
        prompt: 客户端传入的原始提示词。
        index: 当前图片在批次中的零基序号。
        total: 当前批次请求的图片总数。

    返回值：
        单图请求保持原提示词，多图请求返回带序号约束的提示词。
    """
    if total <= 1:
        return prompt
    current = index + 1
    prefix = (
        f"这是本批次第 {current} 张，共 {total} 张。"
        f"若提示词包含分图要求，只执行第 {current} 张对应的要求；"
        "仅生成一张完整图片，禁止拼图或显示序号。"
    )
    return f"{prefix}\n\n{prompt}"


def _generate_batch_item(
    router: FailoverRouter,
    request_data: GenerateImageRequest,
    index: int,
) -> GeneratedBatchItem:
    """执行单张图片生成任务。

    参数：
        router: 已完成配置的主备服务商路由器。
        request_data: 当前批次共用的图片生成请求。
        index: 当前图片在批次中的零基序号。

    返回值：
        包含单张图片及其路由信息的批次结果。
    """
    logger.debug(
        "image.process.generation.item.start: %s",
        {
            "requestId": request_data.request_id,
            "imageIndex": index,
            "requestedCount": request_data.image_count,
        },
    )
    item_request_id = request_data.request_id
    if request_data.image_count > 1 and item_request_id:
        item_request_id = f"{item_request_id}:{index + 1}"
    item_request = replace(
        request_data,
        request_id=item_request_id,
        prompt=_build_batch_prompt(
            request_data.prompt,
            index,
            request_data.image_count,
        ),
        image_count=1,
    )
    route_result = router.generate_image(item_request)
    provider_result = route_result.provider_result
    assets = provider_result.result.assets
    if len(assets) > 1:
        logger.warning(
            "image.process.generation.item.extra_assets_ignored: %s",
            {
                "requestId": request_data.request_id,
                "imageIndex": index,
                "assetCount": len(assets),
            },
        )
    item = GeneratedBatchItem(
        index=index,
        asset=assets[0],
        model=provider_result.public_model,
        provider=provider_result.provider,
        fallback_used=route_result.fallback_used,
    )
    logger.debug(
        "image.process.generation.item.completed: %s",
        {
            "requestId": request_data.request_id,
            "imageIndex": index,
            "requestedCount": request_data.image_count,
            "provider": item.provider,
            "fallbackUsed": item.fallback_used,
        },
    )
    return item


def _generate_batch(
    request_data: GenerateImageRequest,
    settings: AppSettings,
) -> list[GeneratedBatchItem]:
    """按请求数量并发生成图片。

    参数：
        request_data: 已解析完成且包含图片数量的生成请求。
        settings: 图片数量、并发上限和服务商路由配置。

    返回值：
        按请求序号排列的单图生成结果列表。
    """
    if request_data.image_count > settings.image_generation.max_count:
        raise RequestValidationError(
            "imageCount must be between 1 and "
            f"{settings.image_generation.max_count}."
        )

    prepared_request = materialize_reference_images(request_data, settings)
    router = build_failover_router(settings)
    worker_count = min(
        prepared_request.image_count,
        settings.image_generation.max_concurrency,
    )
    logger.debug(
        "image.process.generation.batch.start: %s",
        {
            "requestId": prepared_request.request_id,
            "requestedCount": prepared_request.image_count,
            "workerCount": worker_count,
        },
    )

    if prepared_request.image_count == 1:
        results = [_generate_batch_item(router, prepared_request, 0)]
    else:
        ordered_results: list[GeneratedBatchItem | None] = [
            None
        ] * prepared_request.image_count
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="image-generation",
        ) as executor:
            future_indexes: dict[Future[GeneratedBatchItem], int] = {
                executor.submit(
                    _generate_batch_item,
                    router,
                    prepared_request,
                    index,
                ): index
                for index in range(prepared_request.image_count)
            }
            try:
                for future in as_completed(future_indexes):
                    index = future_indexes[future]
                    ordered_results[index] = future.result()
            except Exception:
                failed_index = future_indexes.get(future, -1)
                logger.debug(
                    "image.process.generation.item.failed: %s",
                    {
                        "requestId": prepared_request.request_id,
                        "imageIndex": failed_index,
                        "requestedCount": prepared_request.image_count,
                    },
                    exc_info=True,
                )
                for pending_future in future_indexes:
                    pending_future.cancel()
                raise
        results = [item for item in ordered_results if item is not None]

    logger.debug(
        "image.process.generation.batch.completed: %s",
        {
            "requestId": prepared_request.request_id,
            "requestedCount": prepared_request.image_count,
            "generatedCount": len(results),
        },
    )
    return results


def process_image_request(
    request_data: GenerateImageRequest,
) -> dict[str, str | bool | int | list[str]]:
    """生成图片并上传至 OSS。

    参数：
        request_data: 已解析完成的图片生成请求。

    返回值：
        包含请求数量、公共模型、OSS 地址和服务商路由信息的结果。
    """
    settings = get_app_settings()
    generated_items = _generate_batch(request_data, settings)

    logger.debug(
        "image.process.upload.start: %s",
        {
            "requestId": request_data.request_id,
            "assetCount": len(generated_items),
        },
    )
    upload_results = []
    for item in generated_items:
        try:
            upload_result = upload_asset_to_oss(settings, item.asset)
        except Exception:
            logger.exception(
                "image.process.upload.failed: %s",
                {
                    "requestId": request_data.request_id,
                    "assetIndex": item.index,
                    "assetCount": len(generated_items),
                },
            )
            raise
        upload_results.append(upload_result)
        logger.debug(
            "image.process.upload.completed: %s",
            {
                "requestId": request_data.request_id,
                "assetIndex": item.index,
                "assetCount": len(generated_items),
                "objectKey": upload_result.object_key,
            },
        )
    oss_urls = [item.object_url for item in upload_results]
    providers = {item.provider for item in generated_items}
    provider = generated_items[0].provider if len(providers) == 1 else "mixed"
    fallback_used = any(item.fallback_used for item in generated_items)
    logger.debug(
        "image.process.completed: %s",
        {
            "requestId": request_data.request_id,
            "requestedCount": request_data.image_count,
            "assetCount": len(generated_items),
            "ossObjectCount": len(oss_urls),
        },
    )
    return {
        "requestId": request_data.request_id,
        "model": generated_items[0].model,
        "requestedCount": request_data.image_count,
        "generatedCount": len(oss_urls),
        "ossUrl": oss_urls[0] if oss_urls else "",
        "ossUrls": oss_urls,
        "provider": provider,
        "fallbackUsed": fallback_used,
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
            asset.payload if isinstance(asset.payload, bytes) else bytes(asset.payload)
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
    if request_data.image_count != 1:
        raise RequestValidationError(
            "imageCount greater than 1 is only supported by /api/process-image."
        )
    settings = get_app_settings()
    prepared_request = materialize_reference_images(request_data, settings)
    route_result = build_failover_router(settings).generate_image(
        prepared_request
    )
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
