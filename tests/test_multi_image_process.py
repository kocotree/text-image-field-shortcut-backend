from __future__ import annotations

import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services.domain.requests import GenerateImageRequest
from services.gemini_service import GeminiRawResponse
from services.pipelines.image import process_image_request
from services.response_normalizer import (
    NormalizedGeneratedAsset,
    NormalizedModelResult,
    normalize_gemini_response,
)


def _build_json_response(payload: dict[str, object]) -> GeminiRawResponse:
    return GeminiRawResponse(
        status_code=200,
        content_type="application/json",
        content_disposition="",
        body=json.dumps(payload).encode("utf-8"),
    )


class MultiImageResponseNormalizerTestCase(unittest.TestCase):
    def test_collects_all_gemini_inline_images_in_response_order(self) -> None:
        raw_response = _build_json_response(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "第一张"},
                                {
                                    "inlineData": {
                                        "mimeType": "image/png",
                                        "data": base64.b64encode(b"first").decode(
                                            "ascii"
                                        ),
                                    }
                                },
                                {"text": "第二张"},
                                {
                                    "inline_data": {
                                        "mime_type": "image/jpeg",
                                        "data": base64.b64encode(b"second").decode(
                                            "ascii"
                                        ),
                                    }
                                },
                            ]
                        }
                    }
                ]
            }
        )

        result = normalize_gemini_response(raw_response)

        self.assertEqual(result.raw_response_type, "json_base64")
        self.assertEqual(
            [asset.payload for asset in result.assets],
            [b"first", b"second"],
        )
        self.assertEqual(
            [asset.mime_type for asset in result.assets],
            ["image/png", "image/jpeg"],
        )
        self.assertEqual(
            [asset.file_name for asset in result.assets],
            ["generated-output-1.png", "generated-output-2.jpg"],
        )

    def test_collects_all_image_urls_in_response_order(self) -> None:
        raw_response = _build_json_response(
            {
                "data": [
                    {"url": "https://assets.example/first.png"},
                    {"url": "https://assets.example/second.png"},
                ]
            }
        )

        result = normalize_gemini_response(raw_response)

        self.assertEqual(result.raw_response_type, "json_url")
        self.assertEqual(
            [asset.payload for asset in result.assets],
            [
                "https://assets.example/first.png",
                "https://assets.example/second.png",
            ],
        )


class MultiImageProcessPipelineTestCase(unittest.TestCase):
    @patch("services.pipelines.image.get_app_settings")
    @patch("services.pipelines.image.build_failover_router")
    @patch("services.pipelines.image.upload_asset_to_oss")
    def test_uploads_every_image_and_returns_all_urls(
        self,
        upload_asset_to_oss,
        build_failover_router,
        get_app_settings,
    ) -> None:
        request_data = GenerateImageRequest(
            request_id="request-1",
            prompt="生成两张图片",
            model="gemini-3.1-flash-image",
            aspect_ratio="1:1",
            image_size="1K",
            input_type="empty",
            file_urls=[],
            files=[],
            raw_payload={},
        )
        assets = [
            NormalizedGeneratedAsset(
                asset_type="image_base64",
                mime_type="image/png",
                file_name=f"generated-output-{index + 1}.png",
                source_kind="bytes",
                payload=payload,
            )
            for index, payload in enumerate((b"first", b"second"))
        ]
        provider_result = SimpleNamespace(
            public_model="gemini-3.1-flash-image",
            provider="easyrouter",
            result=NormalizedModelResult(
                raw_response_type="json_base64",
                assets=assets,
                text_output="",
                raw_meta={},
            ),
        )
        build_failover_router.return_value.generate_image.return_value = (
            SimpleNamespace(
                provider_result=provider_result,
                fallback_used=False,
            )
        )
        upload_asset_to_oss.side_effect = [
            SimpleNamespace(
                object_key="images/first.png",
                object_url="https://bucket.example/images/first.png",
            ),
            SimpleNamespace(
                object_key="images/second.png",
                object_url="https://bucket.example/images/second.png",
            ),
        ]

        result = process_image_request(request_data)

        self.assertEqual(upload_asset_to_oss.call_count, 2)
        self.assertEqual(
            result["ossUrls"],
            [
                "https://bucket.example/images/first.png",
                "https://bucket.example/images/second.png",
            ],
        )
        self.assertEqual(
            result["ossUrl"],
            "https://bucket.example/images/first.png",
        )
        get_app_settings.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
