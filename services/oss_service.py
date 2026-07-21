from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
import re

import alibabacloud_oss_v2 as oss

from services.response_normalizer import NormalizedGeneratedAsset
from services.http import build_asset_fetcher
from services.settings import AppSettings

logger = logging.getLogger(__name__)


@dataclass
class OssUploadResult:
    bucket_name: str
    bucket_prefix: str
    endpoint: str
    region: str
    object_key: str
    object_url: str
    etag: str
    request_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "bucketName": self.bucket_name,
            "bucketPrefix": self.bucket_prefix,
            "endpoint": self.endpoint,
            "region": self.region,
            "objectKey": self.object_key,
            "objectUrl": self.object_url,
            "etag": self.etag,
            "requestId": self.request_id,
        }


def build_datetime_file_name(extension: str = ".png") -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S-%f")
    return f"{timestamp}{extension if extension.startswith('.') else f'.{extension}'}"


def build_object_key(bucket_prefix: str, file_name: str) -> str:
    clean_prefix = str(bucket_prefix or "").strip().strip("/")
    return f"{clean_prefix}/{file_name}" if clean_prefix else file_name


def build_object_url(bucket_name: str, endpoint: str, object_key: str) -> str:
    return f"https://{bucket_name}.{endpoint}/{object_key}"


def create_oss_client(settings: AppSettings) -> oss.Client:
    credentials_provider = oss.credentials.EnvironmentVariableCredentialsProvider()
    cfg = oss.config.load_default()
    cfg.credentials_provider = credentials_provider
    cfg.region = settings.oss.region
    cfg.endpoint = settings.oss.endpoint
    return oss.Client(cfg)


def _looks_like_timestamp_name(file_name: str) -> bool:
    stem = Path(str(file_name or "")).stem
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}[_-]\d{2}[-_]\d{2}[-_]\d{2}", stem) or re.match(r"^\d{8}[_-]?\d{6}", stem))


def _resolve_file_name(asset: NormalizedGeneratedAsset) -> str:
    original_name = Path(asset.file_name or "").name
    suffix = Path(original_name).suffix or ".bin"
    if original_name and _looks_like_timestamp_name(original_name):
        return original_name
    return build_datetime_file_name(suffix)


def _resolve_asset_bytes(settings: AppSettings, asset: NormalizedGeneratedAsset) -> bytes:
    if asset.source_kind == "bytes":
        body = asset.payload if isinstance(asset.payload, bytes) else bytes(asset.payload)
        return body
    if asset.source_kind == "text":
        body = str(asset.payload).encode("utf-8")
        return body
    if asset.source_kind == "url":
        return build_asset_fetcher(settings).fetch(str(asset.payload)).body
    raise RuntimeError(f"Unsupported asset source kind: {asset.source_kind}")


def upload_asset_to_oss(settings: AppSettings, asset: NormalizedGeneratedAsset) -> OssUploadResult:
    file_name = _resolve_file_name(asset)
    object_key = build_object_key(settings.oss.bucket_prefix, file_name)
    body = _resolve_asset_bytes(settings, asset)

    logger.debug(
        "gemini.backend.oss.upload.start: %s",
        {
            "bucketName": settings.oss.bucket_name,
            "endpoint": settings.oss.endpoint,
            "objectKey": object_key,
            "assetType": asset.asset_type,
            "sourceKind": asset.source_kind,
            "mimeType": asset.mime_type,
            "bodyLength": len(body),
        },
    )

    client = create_oss_client(settings)

    result = client.put_object(
        oss.PutObjectRequest(
            bucket=settings.oss.bucket_name,
            key=object_key,
            body=body,
            content_type=asset.mime_type,
        )
    )

    upload_result = OssUploadResult(
        bucket_name=settings.oss.bucket_name,
        bucket_prefix=settings.oss.bucket_prefix,
        endpoint=settings.oss.endpoint,
        region=settings.oss.region,
        object_key=object_key,
        object_url=build_object_url(settings.oss.bucket_name, settings.oss.endpoint, object_key),
        etag=getattr(result, "etag", ""),
        request_id=getattr(result, "request_id", ""),
    )

    logger.debug(
        "gemini.backend.oss.upload.success: %s",
        {
            "bucketName": upload_result.bucket_name,
            "objectKey": upload_result.object_key,
            "objectUrl": upload_result.object_url,
            "etag": upload_result.etag,
            "requestId": upload_result.request_id,
            "bodyLength": len(body),
        },
    )

    return upload_result
