from __future__ import annotations

import os
from dataclasses import dataclass


def normalize_endpoint(endpoint: str) -> str:
    normalized = str(endpoint or "").strip()
    return normalized.removeprefix("https://").removeprefix("http://").rstrip("/")


def endpoint_to_region(endpoint: str) -> str:
    return normalize_endpoint(endpoint).replace(".aliyuncs.com", "").removeprefix("oss-")


@dataclass
class OssSettings:
    endpoint: str
    region: str
    bucket_name: str
    bucket_prefix: str


@dataclass
class AppSettings:
    api_base_url: str
    api_key: str
    nano_banana_2_model_id: str
    nano_banana_pro_model_id: str
    gpt_image_model_id: str
    oss: OssSettings

    @property
    def default_model_id(self) -> str:
        return self.nano_banana_2_model_id or self.nano_banana_pro_model_id


def get_app_settings() -> AppSettings:
    oss_endpoint = normalize_endpoint(os.getenv("OSS_ENDPOINT", ""))

    return AppSettings(
        api_base_url=os.getenv("DEFAULT_API_URL", "https://easyrouter.io").strip(),
        api_key=os.getenv("DEFAULT_API_KEY", "").strip(),
        nano_banana_2_model_id=os.getenv("NANO_BANANA_2_MODEL_ID", "").strip(),
        nano_banana_pro_model_id=os.getenv("NANO_BANANA_PRO_MODEL_ID", "").strip(),
        gpt_image_model_id=os.getenv("GPT_IMAGE_MODEL_ID", "gpt-image-2").strip(),
        oss=OssSettings(
            endpoint=oss_endpoint,
            region=endpoint_to_region(oss_endpoint),
            bucket_name=os.getenv("OSS_BUCKET_NAME", "").strip(),
            bucket_prefix=os.getenv("OSS_BUCKET_FOLDER_PREFIX", "").strip(),
        ),
    )
