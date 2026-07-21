from __future__ import annotations

import os
from dataclasses import dataclass, field


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


@dataclass(frozen=True)
class HttpTimeoutSettings:
    connect: float = 10.0
    read: float = 300.0
    write: float = 60.0
    pool: float = 5.0


@dataclass(frozen=True)
class HttpClientSettings:
    timeout: HttpTimeoutSettings = field(default_factory=HttpTimeoutSettings)
    max_connections: int = 16
    max_keepalive_connections: int = 8
    max_redirects: int = 0
    trust_env: bool = False


@dataclass(frozen=True)
class HttpSettings:
    provider: HttpClientSettings = field(default_factory=HttpClientSettings)
    asset: HttpClientSettings = field(
        default_factory=lambda: HttpClientSettings(
            timeout=HttpTimeoutSettings(connect=10.0, read=120.0, write=60.0, pool=5.0),
            max_redirects=3,
        )
    )
    auth: HttpClientSettings = field(
        default_factory=lambda: HttpClientSettings(
            timeout=HttpTimeoutSettings(connect=2.0, read=5.0, write=5.0, pool=1.0),
        )
    )
    notification: HttpClientSettings = field(
        default_factory=lambda: HttpClientSettings(
            timeout=HttpTimeoutSettings(connect=2.0, read=3.0, write=3.0, pool=1.0),
        )
    )
    asset_max_bytes: int = 52_428_800


@dataclass
class AppSettings:
    api_base_url: str
    api_key: str
    nano_banana_2_model_id: str
    nano_banana_pro_model_id: str
    gpt_image_model_id: str
    oss: OssSettings
    http: HttpSettings = field(default_factory=HttpSettings)
    provider_config_path: str = "config/providers.json"
    fallback_enabled: bool = False
    openrouter_api_url: str = "https://openrouter.ai/api/v1"
    openrouter_api_key: str = ""

    @property
    def default_model_id(self) -> str:
        return self.nano_banana_2_model_id or self.nano_banana_pro_model_id


def get_app_settings() -> AppSettings:
    """加载应用配置。

    返回值：
        从环境变量解析得到的应用配置。
    """
    oss_endpoint = normalize_endpoint(os.getenv("OSS_ENDPOINT", ""))

    max_connections = _read_positive_int("HTTP_MAX_CONNECTIONS", 16)
    max_keepalive_connections = _read_positive_int("HTTP_MAX_KEEPALIVE_CONNECTIONS", 8)
    provider_timeout = HttpTimeoutSettings(
        connect=_read_positive_float("PROVIDER_CONNECT_TIMEOUT_SECONDS", 10.0),
        read=_read_positive_float("PROVIDER_READ_TIMEOUT_SECONDS", 300.0),
        write=_read_positive_float("PROVIDER_WRITE_TIMEOUT_SECONDS", 60.0),
        pool=_read_positive_float("PROVIDER_POOL_TIMEOUT_SECONDS", 5.0),
    )
    trust_env = _read_bool("HTTP_TRUST_ENV", False)

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
        http=HttpSettings(
            provider=HttpClientSettings(
                timeout=provider_timeout,
                max_connections=max_connections,
                max_keepalive_connections=max_keepalive_connections,
                trust_env=trust_env,
            ),
            asset=HttpClientSettings(
                timeout=HttpTimeoutSettings(
                    connect=_read_positive_float("REFERENCE_CONNECT_TIMEOUT_SECONDS", 10.0),
                    read=_read_positive_float("REFERENCE_READ_TIMEOUT_SECONDS", 120.0),
                    write=provider_timeout.write,
                    pool=provider_timeout.pool,
                ),
                max_connections=max_connections,
                max_keepalive_connections=max_keepalive_connections,
                max_redirects=_read_non_negative_int("REFERENCE_MAX_REDIRECTS", 3),
                trust_env=trust_env,
            ),
            auth=HttpClientSettings(
                timeout=HttpTimeoutSettings(
                    connect=_read_positive_float("AUTH_VERIFY_CONNECT_TIMEOUT_SECONDS", 2.0),
                    read=_read_positive_float("AUTH_VERIFY_READ_TIMEOUT_SECONDS", 5.0),
                    write=5.0,
                    pool=_read_positive_float("AUTH_VERIFY_POOL_TIMEOUT_SECONDS", 1.0),
                ),
                max_connections=max_connections,
                max_keepalive_connections=max_keepalive_connections,
                trust_env=trust_env,
            ),
            notification=HttpClientSettings(
                timeout=HttpTimeoutSettings(
                    connect=_read_positive_float("FEISHU_CONNECT_TIMEOUT_SECONDS", 2.0),
                    read=_read_positive_float("FEISHU_READ_TIMEOUT_SECONDS", 3.0),
                    write=3.0,
                    pool=_read_positive_float("FEISHU_POOL_TIMEOUT_SECONDS", 1.0),
                ),
                max_connections=max_connections,
                max_keepalive_connections=max_keepalive_connections,
                trust_env=trust_env,
            ),
            asset_max_bytes=_read_positive_int("REFERENCE_MAX_BYTES", 52_428_800),
        ),
        provider_config_path=os.getenv("PROVIDER_CONFIG_PATH", "config/providers.json").strip(),
        fallback_enabled=_read_bool("FALLBACK_ENABLED", False),
        openrouter_api_url=os.getenv("OPENROUTER_API_URL", "https://openrouter.ai/api/v1").strip(),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
    )


def _read_positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero.")
    return value


def _read_positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero.")
    return value


def _read_non_negative_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 0:
        raise ValueError(f"{name} must be zero or greater.")
    return value


def _read_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
