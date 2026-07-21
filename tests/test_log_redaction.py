from __future__ import annotations

import json
import unittest

from services.gemini_service import GeminiInvocationPlan, PreparedReferenceInput
from services.openai_image_service import OpenAIImageInvocationPlan
from services.request_parser import GenerateImageRequest, UnderstandImageRequest


class LogRedactionTestCase(unittest.TestCase):
    def test_request_summaries_exclude_prompt_and_reference_url(self) -> None:
        prompt = "不应进入日志的提示词"
        reference_url = "https://assets.example/image.png?token=secret"
        generate_request = GenerateImageRequest(
            request_id="request-1",
            prompt=prompt,
            model="gemini-3.1-flash-image",
            aspect_ratio="1:1",
            image_size="1K",
            input_type="file_url",
            file_urls=[reference_url],
            files=[],
            raw_payload={},
        )
        understand_request = UnderstandImageRequest(
            request_id="request-2",
            prompt=prompt,
            model="gemini-3.1-flash-image",
            file_urls=[reference_url],
            raw_payload={},
        )

        serialized = json.dumps(
            [generate_request.to_dict(), understand_request.to_dict()],
            ensure_ascii=False,
        )

        self.assertNotIn(prompt, serialized)
        self.assertNotIn(reference_url, serialized)

    def test_invocation_plan_summaries_exclude_sensitive_content(self) -> None:
        prompt = "不应进入日志的提示词"
        reference_url = "https://assets.example/image.png?token=secret"
        reference = PreparedReferenceInput(
            source_type="url",
            mime_type="image/png",
            file_name="image.png",
            payload=b"image",
            source_ref=reference_url,
        )
        gemini_plan = GeminiInvocationPlan(
            api_url="https://provider.example/v1/models/test:generateContent",
            api_path="/v1/models/test:generateContent",
            model="test",
            prompt=prompt,
            prepared_inputs=[reference],
            request_body={"contents": [{"parts": [{"text": prompt}]}]},
        )
        openai_plan = OpenAIImageInvocationPlan(
            api_url="https://provider.example/v1/images/generations",
            model="gpt-image-2",
            prompt=prompt,
            size="1024x1024",
            n=1,
            quality="auto",
            request_body={"model": "gpt-image-2", "prompt": prompt},
        )

        serialized = json.dumps(
            [gemini_plan.to_dict(), openai_plan.to_dict()],
            ensure_ascii=False,
        )

        self.assertNotIn(prompt, serialized)
        self.assertNotIn(reference_url, serialized)


if __name__ == "__main__":
    unittest.main()
