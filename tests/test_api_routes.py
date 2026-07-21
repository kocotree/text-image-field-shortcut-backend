from __future__ import annotations

import re
import unittest
from unittest.mock import patch

import httpx

from api.app import create_app
from services.pipelines.image import GeneratedImageFile


class ApiRoutesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.client = self.app.test_client()

    def test_expected_routes_are_registered(self) -> None:
        routes = {rule.rule for rule in self.app.url_map.iter_rules()}

        self.assertTrue(
            {
                "/",
                "/health",
                "/health/providers",
                "/api/process-image",
                "/api/generate-image",
                "/api/understand-image",
            }.issubset(routes)
        )

    def test_health_returns_standard_response(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["success"])
        self.assertEqual(response.json["data"]["status"], "healthy")

    def test_protected_routes_require_their_existing_credentials(self) -> None:
        self.assertEqual(self.client.get("/health/providers").status_code, 401)
        self.assertEqual(self.client.post("/api/generate-image").status_code, 401)
        self.assertEqual(
            self.client.post("/api/process-image", json={}).status_code, 403
        )
        self.assertEqual(
            self.client.post("/api/understand-image", json={}).status_code,
            403,
        )

    def test_auth_server_401_is_reported_as_invalid_token(self) -> None:
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                401,
                json={"code": 401, "msg": "Token expired."},
            )
        )
        with httpx.Client(transport=transport) as auth_client:
            with patch(
                "api.auth.access_token.get_http_client",
                return_value=auth_client,
            ):
                response = self.client.get(
                    "/health/providers",
                    headers={"Authorization": "Bearer expired"},
                )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json["msg"], "Token expired.")

    def test_auth_server_failure_is_reported_as_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection failed", request=request)

        with httpx.Client(transport=httpx.MockTransport(handler)) as auth_client:
            with patch(
                "api.auth.access_token.get_http_client",
                return_value=auth_client,
            ):
                response = self.client.get(
                    "/health/providers",
                    headers={"Authorization": "Bearer token"},
                )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["msg"], "Auth service unavailable.")

    def test_successful_generation_emits_received_and_completed_logs(self) -> None:
        generated_file = GeneratedImageFile(
            data=b"image",
            mime_type="image/png",
            file_name="image.png",
            model="gemini-3-pro-image",
            provider="easyrouter",
            fallback_used=False,
        )
        with (
            patch(
                "api.auth.access_token._verify_token",
                return_value={"code": 0},
            ),
            patch(
                "api.routes.image.generate_image_only",
                return_value=generated_file,
            ),
            self.assertLogs("api.routes.image", level="INFO") as captured,
        ):
            response = self.client.post(
                "/api/generate-image",
                headers={"Authorization": "Bearer token"},
                json={
                    "prompt": "生成图片",
                    "model": "gemini-3-pro-image-preview",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(captured.output), 2)
        received_log, completed_log = captured.output
        self.assertIn("api.request.received", received_log)
        self.assertIn("'model': 'gemini-3-pro-image-preview'", received_log)
        self.assertIn("'hasAuthorization': True", received_log)
        self.assertNotIn("contentLength", received_log)
        self.assertNotIn("Bearer token", received_log)
        self.assertIn("api.request.completed", completed_log)
        self.assertIn("'resolvedModel': 'gemini-3-pro-image'", completed_log)

        received_trace_id = re.search(r"'traceId': '([^']+)'", received_log)
        completed_trace_id = re.search(r"'traceId': '([^']+)'", completed_log)
        self.assertIsNotNone(received_trace_id)
        self.assertIsNotNone(completed_trace_id)
        self.assertEqual(received_trace_id.group(1), completed_trace_id.group(1))


if __name__ == "__main__":
    unittest.main()
