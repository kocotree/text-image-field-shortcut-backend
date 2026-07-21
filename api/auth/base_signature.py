from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key

BASE_PUBLIC_KEY = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAxKNV23rheRvtUKDMJPOW
GhUt+W25k63X4Q1QYhztPlobF2VNIDR6eHVFUDP22aytzVguisJ/GaOKZ7FJDKis
9YvMUiCIFnfu1LWB4b4pa4ajmPk/Rr9DMSLz6frKRP0QqirWFe7t+u0K0nzzPe3/
a5ScSmJwYACmayQfLZFTFjyL0Z1SQFZM6pZ1J1w9ETxWI0NrpkMU7eqzVGvhf+OO
dmxsXrHARWa1Ldm3WqPCF3k5jKuPG7s0zB+iuBHamSitZ7ktBf0mzBBjsAjKQll1
kmdjryGbKX5sLXhEgOb5ndakYeA0Oy7vve2Hm78kH5MtaSv6MfNVjm5ForMjPAPQ
BQIDAQAB
-----END PUBLIC KEY-----"""

logger = logging.getLogger(__name__)


class RequestAuthError(RuntimeError):
    """字段捷径签名认证失败。"""


@dataclass
class BaseSignaturePayload:
    source: str
    version: str
    pack_id: str
    exp: int


def _decode_base64_urlsafe(value: str) -> bytes:
    normalized = str(value or "").strip()
    padding_size = (-len(normalized)) % 4
    return base64.urlsafe_b64decode(normalized + ("=" * padding_size))


def _load_signature_payload(base_signature: str) -> tuple[str, bytes]:
    data, separator, signature = str(base_signature or "").partition(".")
    if not separator or not data or not signature:
        raise RequestAuthError("Invalid baseSignature format")
    return (
        _decode_base64_urlsafe(data).decode("utf-8"),
        _decode_base64_urlsafe(signature),
    )


def _verify_signature(src_data: str, src_sign_data: bytes) -> None:
    public_key = load_pem_public_key(BASE_PUBLIC_KEY)
    try:
        public_key.verify(
            src_sign_data,
            src_data.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except InvalidSignature as error:
        raise RequestAuthError("baseSignature verification failed") from error


def _parse_payload(src_data: str) -> BaseSignaturePayload:
    payload = json.loads(src_data)
    return BaseSignaturePayload(
        source=str(payload.get("source") or "").strip(),
        version=str(payload.get("version") or "").strip(),
        pack_id=str(payload.get("packID") or "").strip(),
        exp=int(payload.get("exp") or 0),
    )


def verify_base_request(
    base_signature: str, pack_id: str
) -> BaseSignaturePayload:
    """校验字段捷径请求签名。

    参数：
        base_signature: `X-Base-Signature` 请求头原值。
        pack_id: `X-Pack-Id` 请求头原值。

    返回值：
        已验证的签名载荷。
    """
    if not base_signature:
        raise RequestAuthError("Missing X-Base-Signature header")
    if not pack_id:
        raise RequestAuthError("Missing X-Pack-Id header")

    src_data, src_sign_data = _load_signature_payload(base_signature)
    _verify_signature(src_data, src_sign_data)
    payload = _parse_payload(src_data)

    if payload.source != "base":
        raise RequestAuthError("Invalid baseSignature source")
    if payload.version != "v1":
        raise RequestAuthError("Unsupported baseSignature version")
    if payload.pack_id != pack_id:
        raise RequestAuthError("Pack ID mismatch")
    if payload.exp and payload.exp < int(time.time() * 1000):
        raise RequestAuthError("baseSignature expired")

    logger.debug(
        "api.auth.base_signature.success: %s",
        {
            "source": payload.source,
            "version": payload.version,
            "packId": payload.pack_id,
            "exp": payload.exp,
        },
    )
    return payload
