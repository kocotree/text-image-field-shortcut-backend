from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from services.request_parser import GenerateImageRequest, UnderstandImageRequest
from services.response_normalizer import NormalizedModelResult


@dataclass(frozen=True)
class ImageProviderResult:
    provider: str
    public_model: str
    provider_model: str
    result: NormalizedModelResult
    request_id: str = ""
    elapsed_ms: float = 0.0


@dataclass(frozen=True)
class TextProviderResult:
    provider: str
    public_model: str
    provider_model: str
    text: str
    request_id: str = ""
    elapsed_ms: float = 0.0


class ProviderClient(Protocol):
    name: str

    def generate_image(
        self,
        request: GenerateImageRequest,
        public_model: str,
        provider_model: str,
        timeout_seconds: float | None = None,
    ) -> ImageProviderResult:
        """调用服务商生成图片。"""
        ...

    def understand_image(
        self,
        request: UnderstandImageRequest,
        public_model: str,
        provider_model: str,
        timeout_seconds: float | None = None,
    ) -> TextProviderResult:
        """调用服务商理解图片。"""
        ...
