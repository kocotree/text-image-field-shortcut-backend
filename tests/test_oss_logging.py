from __future__ import annotations

import unittest
from unittest.mock import patch

from services.oss_service import upload_asset_to_oss
from services.response_normalizer import NormalizedGeneratedAsset
from services.settings import AppSettings, OssSettings


class _FakeOssResult:
    etag = "etag"
    request_id = "oss-request"


class _FakeOssClient:
    def put_object(self, _request):
        return _FakeOssResult()


class OssLoggingTestCase(unittest.TestCase):
    def test_successful_upload_does_not_emit_info_log(self) -> None:
        settings = AppSettings(
            oss=OssSettings(
                endpoint="oss-cn-hangzhou.aliyuncs.com",
                region="cn-hangzhou",
                bucket_name="bucket",
                bucket_prefix="images",
            )
        )
        asset = NormalizedGeneratedAsset(
            asset_type="image_base64",
            mime_type="image/png",
            file_name="image.png",
            source_kind="bytes",
            payload=b"image",
        )

        with (
            patch(
                "services.oss_service.create_oss_client",
                return_value=_FakeOssClient(),
            ),
            self.assertNoLogs("services.oss_service", level="INFO"),
        ):
            upload_asset_to_oss(settings, asset)


if __name__ == "__main__":
    unittest.main()
