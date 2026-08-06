from __future__ import annotations

import base64
import binascii
import logging
import mimetypes
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

from services.domain.errors import ErrorCategory, ProviderError
from services.domain.requests import GenerateImageRequest, UploadedFileInfo
from services.http import AssetFetchError, build_asset_fetcher
from services.settings import AppSettings

logger = logging.getLogger(__name__)


def _decode_data_url(value: str) -> tuple[str, bytes] | None:
    prefix, separator, encoded = str(value or "").partition(",")
    if not separator or not prefix.startswith("data:") or ";base64" not in prefix:
        return None
    mime_type = (
        prefix.removeprefix("data:").split(";", 1)[0].strip()
        or "application/octet-stream"
    )
    return mime_type, base64.b64decode(encoded, validate=True)


def _build_reference_name(url: str, mime_type: str, index: int) -> str:
    url_name = Path(urlparse(url).path).name.strip()
    if url_name:
        return url_name
    extension = mimetypes.guess_extension(mime_type) or ".bin"
    return f"reference-{index + 1}{extension}"


def _materialize_uploaded_file(uploaded_file: UploadedFileInfo) -> UploadedFileInfo:
    if uploaded_file.content is not None:
        return uploaded_file
    storage = uploaded_file.storage
    storage.stream.seek(0)
    content = storage.read()
    storage.stream.seek(0)
    return replace(
        uploaded_file,
        content=content,
        content_length=len(content),
    )


def materialize_reference_images(
    request_data: GenerateImageRequest,
    settings: AppSettings,
) -> GenerateImageRequest:
    """下载并缓存当前生成请求的全部参考图。

    参数：
        request_data: 包含临时 URL 或上传文件流的图片生成请求。
        settings: 包含参考图下载限制和 HTTP 客户端配置的应用设置。

    返回值：
        不再包含外部 URL、且所有参考图均已保存为内存字节的生成请求。
    """
    if not request_data.file_urls and all(
        uploaded_file.content is not None for uploaded_file in request_data.files
    ):
        return request_data

    logger.debug(
        "image.reference.materialize.start: %s",
        {
            "requestId": request_data.request_id,
            "fileUrlCount": len(request_data.file_urls),
            "fileCount": len(request_data.files),
        },
    )
    materialized_files = [
        _materialize_uploaded_file(uploaded_file)
        for uploaded_file in request_data.files
    ]
    asset_fetcher = build_asset_fetcher(settings) if request_data.file_urls else None

    try:
        for index, file_url in enumerate(request_data.file_urls):
            decoded_data_url = _decode_data_url(file_url)
            if decoded_data_url:
                mime_type, body = decoded_data_url
                source_url = "data_url"
            else:
                if asset_fetcher is None:
                    raise RuntimeError("参考图片下载器尚未初始化。")
                fetched_asset = asset_fetcher.fetch(file_url)
                mime_type = fetched_asset.content_type
                body = fetched_asset.body
                source_url = fetched_asset.final_url
            materialized_files.append(
                UploadedFileInfo(
                    field_name="fileUrls",
                    file_name=_build_reference_name(source_url, mime_type, index),
                    content_type=mime_type,
                    content_length=len(body),
                    storage=None,
                    content=body,
                )
            )
    except (AssetFetchError, binascii.Error, ValueError) as exc:
        logger.warning(
            "image.reference.materialize.failed: %s",
            {
                "requestId": request_data.request_id,
                "errorType": type(exc).__name__,
            },
        )
        raise ProviderError(
            provider="reference",
            category=ErrorCategory.INVALID_ASSET,
            message="参考图片下载失败或格式不受支持。",
            retryable=False,
            counts_toward_circuit=False,
            cause=exc,
        ) from exc

    total_bytes = sum(item.content_length for item in materialized_files)
    logger.debug(
        "image.reference.materialize.completed: %s",
        {
            "requestId": request_data.request_id,
            "referenceCount": len(materialized_files),
            "totalBytes": total_bytes,
        },
    )
    return replace(
        request_data,
        input_type="file_stream" if materialized_files else "empty",
        file_urls=[],
        files=materialized_files,
    )
