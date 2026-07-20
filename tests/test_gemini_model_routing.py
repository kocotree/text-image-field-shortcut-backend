from __future__ import annotations

import unittest

from services.gemini_service import (
    build_gemini_invocation_plan,
    build_gemini_understand_plan,
    resolve_gemini_model_id,
)
from services.request_parser import GenerateImageRequest, UnderstandImageRequest
from services.settings import AppSettings, OssSettings


class ResolveGeminiModelIdTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = AppSettings(
            api_base_url="https://example.com",
            api_key="test-key",
            nano_banana_2_model_id="gemini-3.1-flash-image-preview",
            nano_banana_pro_model_id="",
            gpt_image_model_id="gpt-image-2",
            oss=OssSettings(endpoint="", region="", bucket_name="", bucket_prefix=""),
        )

    def test_preview_request_model_routes_to_stable_model(self) -> None:
        self.assertEqual(
            resolve_gemini_model_id("gemini-3.1-flash-image-preview", ""),
            "gemini-3.1-flash-image",
        )

    def test_preview_default_model_routes_to_stable_model(self) -> None:
        self.assertEqual(
            resolve_gemini_model_id("", "gemini-3-pro-image-preview"),
            "gemini-3-pro-image",
        )

    def test_stable_model_remains_unchanged(self) -> None:
        self.assertEqual(
            resolve_gemini_model_id("gemini-3.1-flash-image", ""),
            "gemini-3.1-flash-image",
        )

    def test_unmapped_preview_model_remains_unchanged(self) -> None:
        self.assertEqual(
            resolve_gemini_model_id("gemini-2.0-flash-image-preview", ""),
            "gemini-2.0-flash-image-preview",
        )

    def test_generate_plan_routes_preview_default_model_to_stable_endpoint(self) -> None:
        request_data = GenerateImageRequest(
            request_id="test-request",
            prompt="测试提示词",
            model="",
            aspect_ratio=None,
            image_size="1K",
            input_type="empty",
            file_urls=[],
            files=[],
            raw_payload={},
        )

        plan = build_gemini_invocation_plan(request_data, self.settings)

        self.assertEqual(plan.model, "gemini-3.1-flash-image")
        self.assertEqual(
            plan.api_url,
            "https://example.com/v1beta/models/gemini-3.1-flash-image:generateContent",
        )

    def test_understand_plan_routes_preview_request_model_to_stable_endpoint(self) -> None:
        request_data = UnderstandImageRequest(
            request_id="test-request",
            prompt="描述图片",
            model="gemini-3-pro-image-preview",
            file_urls=[],
            raw_payload={},
        )

        plan = build_gemini_understand_plan(request_data, self.settings)

        self.assertEqual(plan.model, "gemini-3-pro-image")
        self.assertEqual(
            plan.api_url,
            "https://example.com/v1beta/models/gemini-3-pro-image:generateContent",
        )


if __name__ == "__main__":
    unittest.main()
