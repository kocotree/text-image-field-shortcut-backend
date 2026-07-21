from __future__ import annotations

from dataclasses import replace
import time

from services.domain.errors import ErrorCategory, ProviderError
from services.domain.provider import ImageProviderResult, TextProviderResult
from services.gemini_service import (
    build_gemini_invocation_plan,
    build_gemini_understand_plan,
    invoke_gemini,
)
from services.openai_image_service import build_openai_image_invocation_plan, invoke_openai_image
from services.request_parser import GenerateImageRequest, UnderstandImageRequest
from services.response_extractor import extract_text_from_gemini_response
from services.response_normalizer import normalize_gemini_response
from services.settings import AppSettings


class EasyRouterProvider:
    """通过 EasyRouter 的 Gemini 与 OpenAI 兼容接口执行模型调用。"""

    name = "easyrouter"

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    def generate_image(
        self, request: GenerateImageRequest, public_model: str, provider_model: str
    ) -> ImageProviderResult:
        """调用 EasyRouter 生成图片。

        参数：
            request: 业务层标准图片生成请求。
            public_model: 客户端使用的公共模型 ID。
            provider_model: EasyRouter 实际接收的模型 ID。

        返回值：
            包含标准图片结果、模型和耗时的服务商结果。
        """
        provider_request = replace(request, model=provider_model)
        start_time = time.perf_counter()
        if provider_model.startswith("gpt-image"):
            plan = build_openai_image_invocation_plan(provider_request, self._settings)
            raw_response = invoke_openai_image(
                plan,
                self._settings.api_key,
                self._settings.http.provider,
            )
        else:
            plan = build_gemini_invocation_plan(provider_request, self._settings)
            raw_response = invoke_gemini(
                plan,
                self._settings.api_key,
                self._settings.http.provider,
            )

        try:
            normalized_result = normalize_gemini_response(raw_response)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                provider=self.name,
                category=ErrorCategory.INVALID_RESPONSE,
                message="EasyRouter 返回了无法解析的图片响应。",
                retryable=True,
                cause=exc,
            ) from exc

        return ImageProviderResult(
            provider=self.name,
            public_model=public_model,
            provider_model=provider_model,
            result=normalized_result,
            elapsed_ms=round((time.perf_counter() - start_time) * 1000, 2),
        )

    def understand_image(
        self, request: UnderstandImageRequest, public_model: str, provider_model: str
    ) -> TextProviderResult:
        """调用 EasyRouter 理解图片。

        参数：
            request: 业务层标准图片理解请求。
            public_model: 客户端使用的公共模型 ID。
            provider_model: EasyRouter 实际接收的模型 ID。

        返回值：
            包含文本、模型和耗时的服务商结果。
        """
        provider_request = replace(request, model=provider_model)
        start_time = time.perf_counter()
        plan = build_gemini_understand_plan(provider_request, self._settings)
        raw_response = invoke_gemini(
            plan,
            self._settings.api_key,
            self._settings.http.provider,
        )
        try:
            text = extract_text_from_gemini_response(raw_response)
        except Exception as exc:
            raise ProviderError(
                provider=self.name,
                category=ErrorCategory.INVALID_RESPONSE,
                message="EasyRouter 返回了无法解析的图片理解响应。",
                retryable=True,
                cause=exc,
            ) from exc
        return TextProviderResult(
            provider=self.name,
            public_model=public_model,
            provider_model=provider_model,
            text=text,
            elapsed_ms=round((time.perf_counter() - start_time) * 1000, 2),
        )
