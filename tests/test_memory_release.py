from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from api.app import create_app
from services.memory_release import release_process_memory
from services.settings import get_app_settings


class MemoryReleaseTestCase(unittest.TestCase):
    def test_memory_trim_setting_is_loaded_from_environment(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "MEMORY_TRIM_AFTER_IMAGE_REQUEST": "true",
                "MEMORY_TRIM_RSS_THRESHOLD_MB": "640",
            },
        ):
            settings = get_app_settings()

        self.assertTrue(settings.image_generation.trim_memory_after_request)
        self.assertEqual(
            settings.image_generation.trim_rss_threshold_bytes,
            640 * 1024 * 1024,
        )

    def test_release_trims_linux_heap(self) -> None:
        with (
            patch(
                "services.memory_release._trim_linux_heap",
                return_value=True,
            ),
            patch(
                "services.memory_release._read_current_rss_bytes",
                side_effect=[200, 120],
            ),
            self.assertLogs("services.memory_release", level="INFO") as captured,
        ):
            result = release_process_memory(
                request_path="/api/process-image",
                status_code=200,
                rss_threshold_bytes=150,
            )

        self.assertTrue(result.malloc_trimmed)
        self.assertEqual(result.rss_before_bytes, 200)
        self.assertEqual(result.rss_after_bytes, 120)
        self.assertIn("'rssReleasedBytes': 80", captured.output[0])

    def test_release_skips_heap_trim_below_rss_threshold(self) -> None:
        with (
            patch(
                "services.memory_release._read_current_rss_bytes",
                return_value=100,
            ),
            patch("services.memory_release._trim_linux_heap") as trim_heap,
            self.assertNoLogs("services.memory_release", level="INFO"),
        ):
            result = release_process_memory(
                request_path="/api/process-image",
                status_code=200,
                rss_threshold_bytes=150,
            )

        self.assertFalse(result.malloc_trimmed)
        trim_heap.assert_not_called()

    def test_enabled_app_releases_memory_after_image_response_closes(self) -> None:
        settings = get_app_settings()
        settings.image_generation = replace(
            settings.image_generation,
            trim_memory_after_request=True,
        )
        with (
            patch("api.app.get_app_settings", return_value=settings),
            patch("api.app.release_process_memory") as release_memory,
        ):
            app = create_app()
            response = app.test_client().post("/api/process-image", json={})
            self.assertEqual(response.status_code, 403)
            self.assertFalse(release_memory.called)
            response.close()

        release_memory.assert_called_once_with(
            request_path="/api/process-image",
            status_code=403,
            rss_threshold_bytes=512 * 1024 * 1024,
        )

    def test_enabled_app_ignores_non_image_response(self) -> None:
        settings = get_app_settings()
        settings.image_generation = replace(
            settings.image_generation,
            trim_memory_after_request=True,
        )
        with (
            patch("api.app.get_app_settings", return_value=settings),
            patch("api.app.release_process_memory") as release_memory,
        ):
            app = create_app()
            response = app.test_client().get("/health")
            response.close()

        release_memory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
