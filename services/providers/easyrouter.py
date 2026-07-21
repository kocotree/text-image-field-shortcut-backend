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
from services.domain.requests import GenerateImageRequest, UnderstandImageRequest
from services.response_extractor import extract_text_from_gemini_response
from services.response_normalizer import normalize_gemini_response
from services.settings import AppSettings


class EasyRouterProvider:
    """通过 EasyRouter 的 Gemini 与 OpenAI 兼容接口执行模型调用。"""

    name = "easyrouter"

    def __init__(self, settings: AppSettings, base_url: str, api_key: str) -> None:
        """创建 EasyRouter 服务商适配器。

        参数：
            settings: HTTP 客户端和资源下载等应用配置。
            base_url: 从服务商配置文件读取的 EasyRouter 地址。
            api_key: 从服务商指定环境变量读取的访问密钥。

        返回值：
            无。
        """
        self._settings = settings
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def generate_image(
        self,
        request: GenerateImageRequest,
        public_model: str,
        provider_model: str,
        timeout_seconds: float | None = None,
    ) -> ImageProviderResult:
        """调用 EasyRouter 生成图片。

        参数：
            request: 业务层标准图片生成请求。
            public_model: 客户端使用的公共模型 ID。
            provider_model: EasyRouter 实际接收的模型 ID。
            timeout_seconds: 路由层分配给本次调用的最大秒数。

        返回值：
            包含标准图片结果、模型和耗时的服务商结果。
        """
        provider_request = replace(request, model=provider_model)
        start_time = time.perf_counter()
        if provider_model.startswith("gpt-image"):
            plan = build_openai_image_invocation_plan(
                provider_request, self._base_url
            )
            raw_response = invoke_openai_image(
                plan,
                self._api_key,
                self._settings.http.provider,
                timeout_seconds=timeout_seconds,
            )
        else:
            plan = build_gemini_invocation_plan(
                provider_request, self._settings, self._base_url
            )
            raw_response = invoke_gemini(
                plan,
                self._api_key,
                self._settings.http.provider,
                timeout_seconds=timeout_seconds,
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
        self,
        request: UnderstandImageRequest,
        public_model: str,
        provider_model: str,
        timeout_seconds: float | None = None,
    ) -> TextProviderResult:
        """调用 EasyRouter 理解图片。

        参数：
            request: 业务层标准图片理解请求。
            public_model: 客户端使用的公共模型 ID。
            provider_model: EasyRouter 实际接收的模型 ID。
            timeout_seconds: 路由层分配给本次调用的最大秒数。

        返回值：
            包含文本、模型和耗时的服务商结果。
        """
        provider_request = replace(request, model=provider_model)
        start_time = time.perf_counter()
        plan = build_gemini_understand_plan(
            provider_request, self._settings, self._base_url
        )
        raw_response = invoke_gemini(
            plan,
            self._api_key,
            self._settings.http.provider,
            timeout_seconds=timeout_seconds,
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
