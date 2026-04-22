from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

import alibabacloud_oss_v2 as oss


ENV_FILE = Path(__file__).with_name(".env")


def load_env_file(env_file: Path) -> None:
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def normalize_endpoint(endpoint: str) -> str:
    normalized = endpoint.strip()
    normalized = normalized.removeprefix("https://").removeprefix("http://")
    return normalized.rstrip("/")


def endpoint_to_region(endpoint: str) -> str:
    host = normalize_endpoint(endpoint)
    return host.replace(".aliyuncs.com", "").removeprefix("oss-")


def build_datetime_file_name(extension: str = ".txt") -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    clean_extension = extension if extension.startswith(".") else f".{extension}"
    return f"{timestamp}{clean_extension}"


def build_object_key(prefix: str, object_name: str | None, extension: str = ".txt") -> str:
    clean_prefix = prefix.strip().strip("/")
    file_name = object_name or build_datetime_file_name(extension)
    return f"{clean_prefix}/{file_name}" if clean_prefix else file_name


def create_client() -> tuple[oss.Client, str, str, str]:
    load_env_file(ENV_FILE)

    endpoint = os.environ["OSS_ENDPOINT"]
    bucket_name = os.environ["OSS_BUCKET_NAME"]
    object_prefix = os.getenv("OSS_BUCKET_FOLDER_PREFIX", "").strip()

    # The V2 SDK reads OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET from environment variables.
    credentials_provider = oss.credentials.EnvironmentVariableCredentialsProvider()

    cfg = oss.config.load_default()
    cfg.credentials_provider = credentials_provider
    cfg.region = endpoint_to_region(endpoint)
    cfg.endpoint = normalize_endpoint(endpoint)

    return oss.Client(cfg), bucket_name, object_prefix, normalize_endpoint(endpoint)


def put_bytes(client: oss.Client, bucket_name: str, object_key: str, body: bytes, content_type: str):
    return client.put_object(
        oss.PutObjectRequest(
            bucket=bucket_name,
            key=object_key,
            body=body,
            content_type=content_type,
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple OSS upload demo using Alibaba Cloud OSS Python SDK V2.")
    parser.add_argument("--file", help="Optional local file path to upload.")
    parser.add_argument("--object-name", help="Optional object name in OSS.")
    parser.add_argument(
        "--text",
        default="hello from maibao-field-shortcut-backend oss demo",
        help="Text content to upload when --file is not provided.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client, bucket_name, object_prefix, endpoint = create_client()

    if args.file:
        local_file = Path(args.file)
        object_key = build_object_key(object_prefix, args.object_name, local_file.suffix or ".bin")
        body = local_file.read_bytes()
        content_type = "application/octet-stream"
    else:
        object_key = build_object_key(object_prefix, args.object_name)
        body = args.text.encode("utf-8")
        content_type = "text/plain; charset=utf-8"

    result = put_bytes(client, bucket_name, object_key, body, content_type)
    object_url = f"https://{bucket_name}.{endpoint}/{object_key}"

    print("OSS upload succeeded.")
    print(f"bucket={bucket_name}")
    print(f"object_key={object_key}")
    print(f"etag={result.etag}")
    print(f"request_id={result.request_id}")
    print(f"url={object_url}")


if __name__ == "__main__":
    main()
