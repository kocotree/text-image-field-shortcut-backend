from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def build_demo_image_job(payload: dict[str, Any]) -> dict[str, Any]:
    prompt = str(payload.get("prompt") or "").strip()
    attachments = payload.get("attachments") or []
    model = str(payload.get("model") or "gemini-3.1-flash-image-preview").strip()

    return {
        "jobId": f"demo-{int(datetime.now(timezone.utc).timestamp())}",
        "status": "accepted",
        "prompt": prompt,
        "model": model,
        "attachmentCount": len(attachments) if isinstance(attachments, list) else 0,
        "imageUrl": "https://example.com/demo-generated-image.png",
        "ossUrl": "https://example.com/demo-generated-image.png",
        "nextStep": "Replace this demo stub with real Maibao image generation and OSS upload logic.",
    }
