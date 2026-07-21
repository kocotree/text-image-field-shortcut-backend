from __future__ import annotations

import json
from pathlib import Path
import unittest

import httpx

from services.domain.errors import ErrorCategory, ProviderError, provider_error_from_httpx
from services.model_registry import load_model_registry
from services.providers.openrouter import OpenRouterProvider
from services.request_parser import GenerateImageRequest, UnderstandImageRequest
from services.settings import AppSettings, OssSettings


def _build_settings() -> AppSettings:
    return AppSettings(
        oss=OssSettings(endpoint="", region="", bucket_name="", bucket_prefix=""),
    )


class ModelRegistryTestCase(unittest.TestCase):
    def test_preview_alias_routes_to_stable_provider_models(self) -> None:
        registry = load_model_registry("config/providers.json")

        public_model = registry.resolve("gemini-3.1-flash-image-preview")

        self.assertEqual(public_model, "gemini-3.1-flash-image")
        self.assertEqual(
            registry.provider_model(public_model, "easyrouter"),
            "gemini-3.1-flash-image",
        )
        self.assertEqual(
            registry.provider_model(public_model, "openrouter"),
            "google/gemini-3.1-flash-image",
        )

    def test_empty_model_uses_configured_default(self) -> None:
        registry = load_model_registry("config/providers.json")

        self.assertEqual(registry.resolve(""), "gemini-3.1-flash-image")
        self.assertEqual(
            registry.configuration.providers["easyrouter"].base_url,
            "https://easyrouter.io",
        )

    def test_invalid_duplicate_alias_is_rejected(self) -> None:
        config_path = Path("tests/.provider-config-invalid.json")
        config_path.write_text(
            json.dumps(
                {
                    "primary_provider": "primary",
                    "fallback_providers": [],
                    "default_model": "model-a",
                    "providers": {
                        "primary": {
                            "adapter": "easyrouter",
                            "base_url": "https://provider.example",
                            "api_key_env": "KEY",
                        }
                    },
                    "models": {
                        "model-a": {
                            "aliases": ["legacy"],
                            "capabilities": [],
                            "providers": {"primary": "a"},
                        },
                        "model-b": {
                            "aliases": ["legacy"],
                            "capabilities": [],
                            "providers": {"primary": "b"},
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        self.addCleanup(config_path.unlink, missing_ok=True)

        with self.assertRaisesRegex(ValueError, "模型别名重复"):
            load_model_registry(str(config_path))


class OpenRouterProviderTestCase(unittest.TestCase):
    def test_generate_image_uses_official_images_schema(self) -> None:
        captured_request: httpx.Request | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(
                200,
                headers={"x-request-id": "or-request"},
                json={"created": 1, "data": [{"b64_json": "aW1hZ2U="}]},
            )

        request_data = GenerateImageRequest(
            request_id="request-1",
            prompt="生成图片",
            model="gemini-3.1-flash-image",
            aspect_ratio="16:9",
            image_size="2K",
            input_type="file_url",
            file_urls=["https://assets.example/reference.png"],
            files=[],
            raw_payload={},
        )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            provider = OpenRouterProvider(
                _build_settings(),
                "https://openrouter.example/api/v1",
                "openrouter-key",
                client,
            )
            result = provider.generate_image(
                request_data,
                "gemini-3.1-flash-image",
                "google/gemini-3.1-flash-image",
            )

        self.assertEqual(result.provider, "openrouter")
        self.assertEqual(result.request_id, "or-request")
        self.assertEqual(result.result.assets[0].payload, b"image")
        self.assertIsNotNone(captured_request)
        payload = json.loads(captured_request.content)
        self.assertEqual(captured_request.url.path, "/api/v1/images")
        self.assertEqual(payload["resolution"], "2K")
        self.assertEqual(payload["aspect_ratio"], "16:9")
        self.assertEqual(
            payload["input_references"][0],
            {
                "type": "image_url",
                "image_url": {"url": "https://assets.example/reference.png"},
            },
        )

    def test_understand_image_uses_chat_completions_schema(self) -> None:
        captured_request: httpx.Request | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "图片里有一只猫。"}}]},
            )

        request_data = UnderstandImageRequest(
            request_id="request-2",
            prompt="描述图片",
            model="gemini-3.1-flash-image",
            file_urls=["https://assets.example/cat.png"],
            raw_payload={},
        )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            provider = OpenRouterProvider(
                _build_settings(),
                "https://openrouter.example/api/v1",
                "openrouter-key",
                client,
            )
            result = provider.understand_image(
                request_data,
                "gemini-3.1-flash-image",
                "google/gemini-3.1-flash-image",
            )

        self.assertEqual(result.text, "图片里有一只猫。")
        self.assertIsNotNone(captured_request)
        payload = json.loads(captured_request.content)
        self.assertEqual(captured_request.url.path, "/api/v1/chat/completions")
        self.assertEqual(payload["messages"][0]["content"][0]["type"], "text")
        self.assertEqual(payload["messages"][0]["content"][1]["type"], "image_url")

    def test_openrouter_error_type_is_normalized(self) -> None:
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                400,
                json={
                    "error": {
                        "message": "blocked",
                        "error_type": "content_policy_violation",
                    }
                },
            )
        )
        request_data = GenerateImageRequest(
            request_id="request-3",
            prompt="生成图片",
            model="gemini-3.1-flash-image",
            aspect_ratio=None,
            image_size="1K",
            input_type="empty",
            file_urls=[],
            files=[],
            raw_payload={},
        )

        with httpx.Client(transport=transport) as client:
            provider = OpenRouterProvider(
                _build_settings(),
                "https://openrouter.example/api/v1",
                "openrouter-key",
                client,
            )
            with self.assertRaises(ProviderError) as raised:
                provider.generate_image(
                    request_data,
                    "gemini-3.1-flash-image",
                    "google/gemini-3.1-flash-image",
                )

        self.assertEqual(raised.exception.category, ErrorCategory.CONTENT_POLICY)
        self.assertFalse(raised.exception.retryable)


class ProviderErrorMappingTestCase(unittest.TestCase):
    def test_pool_timeout_does_not_count_toward_circuit(self) -> None:
        request = httpx.Request("POST", "https://provider.example")

        error = provider_error_from_httpx("easyrouter", httpx.PoolTimeout("busy", request=request))

        self.assertEqual(error.category, ErrorCategory.LOCAL_CAPACITY)
        self.assertTrue(error.retryable)
        self.assertFalse(error.counts_toward_circuit)


if __name__ == "__main__":
    unittest.main()
