from __future__ import annotations

import unittest

from services.domain.errors import ErrorCategory, ProviderError
from services.domain.provider import ImageProviderResult, TextProviderResult
from services.model_registry import load_model_registry
from services.request_parser import GenerateImageRequest, UnderstandImageRequest
from services.response_normalizer import NormalizedGeneratedAsset, NormalizedModelResult
from services.routing import FailoverExhaustedError, FailoverRouter
from services.routing.circuit_breaker import CircuitOpenError, CircuitState
from services.settings import AppSettings, OssSettings, RoutingSettings


def _image_result(provider: str, public_model: str, provider_model: str) -> ImageProviderResult:
    return ImageProviderResult(
        provider=provider,
        public_model=public_model,
        provider_model=provider_model,
        result=NormalizedModelResult(
            raw_response_type="json_base64",
            assets=[
                NormalizedGeneratedAsset(
                    asset_type="image_base64",
                    mime_type="image/png",
                    file_name="image.png",
                    source_kind="bytes",
                    payload=b"image",
                )
            ],
            text_output="",
            raw_meta={},
        ),
    )


def _empty_image_result(
    provider: str, public_model: str, provider_model: str
) -> ImageProviderResult:
    return ImageProviderResult(
        provider=provider,
        public_model=public_model,
        provider_model=provider_model,
        result=NormalizedModelResult(
            raw_response_type="json_text",
            assets=[],
            text_output="",
            raw_meta={},
        ),
    )


class FakeProvider:
    def __init__(self, name: str, image_outcomes: list, text_outcomes: list | None = None) -> None:
        self.name = name
        self.image_outcomes = list(image_outcomes)
        self.text_outcomes = list(text_outcomes or [])
        self.calls: list[tuple[str, str, float | None]] = []

    def generate_image(
        self,
        request: GenerateImageRequest,
        public_model: str,
        provider_model: str,
        timeout_seconds: float | None = None,
    ) -> ImageProviderResult:
        self.calls.append((public_model, provider_model, timeout_seconds))
        outcome = self.image_outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def understand_image(
        self,
        request: UnderstandImageRequest,
        public_model: str,
        provider_model: str,
        timeout_seconds: float | None = None,
    ) -> TextProviderResult:
        self.calls.append((public_model, provider_model, timeout_seconds))
        outcome = self.text_outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class PrimaryOpenCircuitBreaker:
    state_available = True

    def before_call(self, provider: str, capability: str) -> CircuitState:
        if provider == "easyrouter":
            raise CircuitOpenError(provider, capability, CircuitState.OPEN)
        return CircuitState.CLOSED

    def record_success(self, provider: str, capability: str) -> None:
        return None

    def record_failure(
        self, provider: str, capability: str, error: ProviderError
    ) -> bool:
        return False

def _build_settings(*, fallback_enabled: bool = True) -> AppSettings:
    return AppSettings(
        api_base_url="https://easyrouter.example",
        api_key="easy-key",
        nano_banana_2_model_id="gemini-3.1-flash-image",
        nano_banana_pro_model_id="gemini-3-pro-image",
        gpt_image_model_id="gpt-image-2",
        oss=OssSettings(endpoint="", region="", bucket_name="", bucket_prefix=""),
        fallback_enabled=fallback_enabled,
        routing=RoutingSettings(
            request_deadline_seconds=30,
            primary_max_attempts=1,
            fallback_max_attempts=1,
            primary_empty_response_retry_count=1,
        ),
    )


def _build_request(model: str = "gemini-3.1-flash-image-preview") -> GenerateImageRequest:
    return GenerateImageRequest(
        request_id="request-1",
        prompt="生成图片",
        model=model,
        aspect_ratio="1:1",
        image_size="1K",
        input_type="empty",
        file_urls=[],
        files=[],
        raw_payload={},
    )


class FailoverRouterTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = load_model_registry("config/providers.json")

    def test_primary_success_does_not_call_fallback(self) -> None:
        primary_result = _image_result(
            "easyrouter",
            "gemini-3.1-flash-image",
            "gemini-3.1-flash-image",
        )
        primary = FakeProvider("easyrouter", [primary_result])
        fallback = FakeProvider("openrouter", [])
        router = FailoverRouter(
            _build_settings(),
            self.registry,
            {"easyrouter": primary, "openrouter": fallback},
        )

        result = router.generate_image(_build_request())

        self.assertFalse(result.fallback_used)
        self.assertEqual(len(primary.calls), 1)
        self.assertEqual(fallback.calls, [])
        self.assertEqual(primary.calls[0][0], "gemini-3.1-flash-image")

    def test_retryable_primary_error_routes_to_openrouter(self) -> None:
        primary_error = ProviderError(
            provider="easyrouter",
            category=ErrorCategory.TIMEOUT,
            message="timeout",
            retryable=True,
        )
        fallback_result = _image_result(
            "openrouter",
            "gemini-3.1-flash-image",
            "google/gemini-3.1-flash-image",
        )
        primary = FakeProvider("easyrouter", [primary_error])
        fallback = FakeProvider("openrouter", [fallback_result])
        router = FailoverRouter(
            _build_settings(),
            self.registry,
            {"easyrouter": primary, "openrouter": fallback},
        )

        result = router.generate_image(_build_request())

        self.assertTrue(result.fallback_used)
        self.assertEqual(fallback.calls[0][1], "google/gemini-3.1-flash-image")
        self.assertGreater(fallback.calls[0][2] or 0, 0)
        self.assertEqual([item.provider for item in result.attempts], ["easyrouter", "openrouter"])

    def test_non_retryable_error_does_not_call_fallback(self) -> None:
        primary_error = ProviderError(
            provider="easyrouter",
            category=ErrorCategory.INVALID_REQUEST,
            message="invalid",
            retryable=False,
        )
        primary = FakeProvider("easyrouter", [primary_error])
        fallback = FakeProvider("openrouter", [])
        router = FailoverRouter(
            _build_settings(),
            self.registry,
            {"easyrouter": primary, "openrouter": fallback},
        )

        with self.assertRaises(ProviderError) as raised:
            router.generate_image(_build_request())

        self.assertEqual(raised.exception.category, ErrorCategory.INVALID_REQUEST)
        self.assertEqual(fallback.calls, [])

    def test_empty_primary_response_retries_before_fallback(self) -> None:
        empty = _empty_image_result(
            "easyrouter",
            "gemini-3.1-flash-image",
            "gemini-3.1-flash-image",
        )
        success = _image_result(
            "easyrouter",
            "gemini-3.1-flash-image",
            "gemini-3.1-flash-image",
        )
        primary = FakeProvider("easyrouter", [empty, success])
        fallback = FakeProvider("openrouter", [])
        router = FailoverRouter(
            _build_settings(),
            self.registry,
            {"easyrouter": primary, "openrouter": fallback},
        )

        result = router.generate_image(_build_request())

        self.assertFalse(result.fallback_used)
        self.assertEqual(len(primary.calls), 2)
        self.assertEqual(fallback.calls, [])

    def test_both_providers_fail_raises_exhausted_error(self) -> None:
        primary_error = ProviderError(
            provider="easyrouter",
            category=ErrorCategory.TIMEOUT,
            message="timeout",
            retryable=True,
        )
        fallback_error = ProviderError(
            provider="openrouter",
            category=ErrorCategory.RATE_LIMIT,
            message="limited",
            retryable=True,
        )
        router = FailoverRouter(
            _build_settings(),
            self.registry,
            {
                "easyrouter": FakeProvider("easyrouter", [primary_error]),
                "openrouter": FakeProvider("openrouter", [fallback_error]),
            },
        )

        with self.assertRaises(FailoverExhaustedError) as raised:
            router.generate_image(_build_request())

        self.assertEqual(len(raised.exception.errors), 2)

    def test_fallback_switch_can_disable_openrouter(self) -> None:
        primary_error = ProviderError(
            provider="easyrouter",
            category=ErrorCategory.TIMEOUT,
            message="timeout",
            retryable=True,
        )
        fallback = FakeProvider("openrouter", [])
        router = FailoverRouter(
            _build_settings(fallback_enabled=False),
            self.registry,
            {"easyrouter": FakeProvider("easyrouter", [primary_error]), "openrouter": fallback},
        )

        with self.assertRaises(ProviderError):
            router.generate_image(_build_request())

        self.assertEqual(fallback.calls, [])

    def test_open_primary_circuit_skips_directly_to_fallback(self) -> None:
        fallback_result = _image_result(
            "openrouter",
            "gemini-3.1-flash-image",
            "google/gemini-3.1-flash-image",
        )
        primary = FakeProvider("easyrouter", [])
        fallback = FakeProvider("openrouter", [fallback_result])
        router = FailoverRouter(
            _build_settings(),
            self.registry,
            {"easyrouter": primary, "openrouter": fallback},
            PrimaryOpenCircuitBreaker(),
        )

        result = router.generate_image(_build_request())

        self.assertTrue(result.fallback_used)
        self.assertEqual(primary.calls, [])
        self.assertEqual(result.attempts[0].attempt, 0)


if __name__ == "__main__":
    unittest.main()
