from __future__ import annotations

import unittest

from api.app import create_app


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


if __name__ == "__main__":
    unittest.main()
