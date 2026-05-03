"""Image generation providers with quality tiers.

Supports fal-ai and Alibaba Cloud Model Studio / DashScope Qwen-Image. If
`IMAGE_PROVIDER=dashscope` is set, or `DASHSCOPE_API_KEY` is present and
`FAL_KEY` is not, generation uses Qwen-Image and no FAL_KEY is required.

`_args_for` keeps the per-model arg-shape divergence localised — seedream uses
`image_size`, nano-banana uses `aspect_ratio`. Add new entries here as more
models join.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any

import fal_client
import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

TIER_MODELS: dict[str, str] = {
    "fast":     "fal-ai/nano-banana",
    "balanced": "fal-ai/nano-banana-pro",
    "pro":      "fal-ai/bytedance/seedream/v4/text-to-image",
}
TIER_ENV_KEYS: dict[str, str] = {
    "fast":     "FAL_IMAGE_MODEL_FAST",
    "balanced": "FAL_IMAGE_MODEL_BALANCED",
    "pro":      "FAL_IMAGE_MODEL_PRO",
}
DEFAULT_TIER = "balanced"

DASHSCOPE_IMAGE_ENDPOINT = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
    "multimodal-generation/generation"
)
DASHSCOPE_TIER_MODELS: dict[str, str] = {
    "fast": "qwen-image-plus",
    "balanced": "qwen-image-plus",
    "pro": "qwen-image-2.0-pro",
}
DASHSCOPE_TIER_ENV_KEYS: dict[str, str] = {
    "fast": "DASHSCOPE_IMAGE_MODEL_FAST",
    "balanced": "DASHSCOPE_IMAGE_MODEL_BALANCED",
    "pro": "DASHSCOPE_IMAGE_MODEL_PRO",
}

# Aspect strings → seedream-style image_size enum (fal expects one of these).
SEEDREAM_SIZE_MAP: dict[str, str] = {
    "16:9": "landscape_16_9",
    "9:16": "portrait_16_9",
    "1:1":  "square_hd",
    "4:3":  "landscape_4_3",
    "3:4":  "portrait_4_3",
}

QWEN_IMAGE_PLUS_SIZE_MAP: dict[str, str] = {
    "16:9": "1664*928",
    "9:16": "928*1664",
    "1:1": "1328*1328",
    "4:3": "1472*1104",
    "3:4": "1104*1472",
}

QWEN_IMAGE_2_SIZE_MAP: dict[str, str] = {
    "16:9": "2688*1536",
    "9:16": "1536*2688",
    "1:1": "2048*2048",
    "4:3": "2368*1728",
    "3:4": "1728*2368",
}


@dataclass
class GeneratedImage:
    jpeg_bytes: bytes
    mime_type: str
    model: str
    provider_request_id: str | None


def _ensure_fal_key() -> None:
    if not os.environ.get("FAL_KEY"):
        raise RuntimeError("FAL_KEY is not set")


def _image_provider() -> str:
    requested = os.environ.get("IMAGE_PROVIDER", "").strip().lower()
    if requested in ("fal", "dashscope"):
        return requested
    if os.environ.get("DASHSCOPE_API_KEY") and not os.environ.get("FAL_KEY"):
        return "dashscope"
    return "fal"


def _resolve_tier(tier: str | None) -> str:
    candidate = (tier or os.environ.get("FAL_IMAGE_TIER") or DEFAULT_TIER).lower()
    if candidate not in TIER_MODELS:
        return DEFAULT_TIER
    return candidate


def _resolve_model(tier: str | None, model_override: str | None) -> str:
    if model_override:
        return model_override
    resolved_tier = _resolve_tier(tier)
    env_key = TIER_ENV_KEYS[resolved_tier]
    legacy = os.environ.get("FAL_IMAGE_MODEL")  # backwards-compat for old setups
    return os.environ.get(env_key) or legacy or TIER_MODELS[resolved_tier]


def _resolve_dashscope_model(tier: str | None, model_override: str | None) -> str:
    if model_override:
        return model_override
    resolved_tier = _resolve_tier(tier)
    env_key = DASHSCOPE_TIER_ENV_KEYS[resolved_tier]
    legacy = os.environ.get("DASHSCOPE_IMAGE_MODEL")
    return (
        os.environ.get(env_key)
        or legacy
        or DASHSCOPE_TIER_MODELS[resolved_tier]
    )


def _args_for(model: str, prompt: str, aspect_ratio: str) -> dict[str, Any]:
    if "seedream" in model:
        return {
            "prompt": prompt,
            "image_size": SEEDREAM_SIZE_MAP.get(aspect_ratio, "landscape_16_9"),
        }
    # nano-banana + nano-banana-pro both accept aspect_ratio directly.
    return {"prompt": prompt, "aspect_ratio": aspect_ratio}


async def generate_image(
    prompt: str,
    aspect_ratio: str,
    tier: str | None = None,
    model_override: str | None = None,
) -> GeneratedImage:
    from obs import span

    if _image_provider() == "dashscope":
        model = _resolve_dashscope_model(tier, model_override)
        async with span(
            "image.generate",
            provider="dashscope",
            model=model,
            prompt_len=len(prompt),
        ) as ctx:
            result = await _dashscope_generate_image(model, prompt, aspect_ratio)
            image_info = _first_dashscope_image(result)
            jpeg_bytes, mime = await _fetch_image_bytes(image_info)
            ctx["bytes"] = len(jpeg_bytes)
        return GeneratedImage(
            jpeg_bytes=jpeg_bytes,
            mime_type=mime,
            model=model,
            provider_request_id=str(result.get("request_id") or "") or None,
        )

    _ensure_fal_key()
    model = _resolve_model(tier, model_override)
    async with span(
        "image.generate", provider="fal", model=model, prompt_len=len(prompt)
    ) as ctx:
        result = await _fal_subscribe(model, _args_for(model, prompt, aspect_ratio))
        image_info = _first_image(result)
        jpeg_bytes, mime = await _fetch_image_bytes(image_info)
        ctx["bytes"] = len(jpeg_bytes)
    return GeneratedImage(
        jpeg_bytes=jpeg_bytes,
        mime_type=mime,
        model=model,
        provider_request_id=str(result.get("requestId") or "") or None,
    )


def encode_data_url(jpeg_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    b64 = base64.b64encode(jpeg_bytes).decode("ascii")
    return f"data:{mime_type};base64,{b64}"


def _first_image(result: dict) -> dict:
    images = result.get("images") or []
    if not images:
        raise RuntimeError("fal returned no images")
    first = images[0]
    if not isinstance(first, dict):
        raise RuntimeError("fal image entry malformed")
    return first


def _qwen_size(model: str, aspect_ratio: str) -> str:
    if "2.0" in model:
        return QWEN_IMAGE_2_SIZE_MAP.get(aspect_ratio, "2688*1536")
    return QWEN_IMAGE_PLUS_SIZE_MAP.get(aspect_ratio, "1664*928")


def _trim_prompt(prompt: str, limit: int = 800) -> str:
    cleaned = " ".join(prompt.split())
    return cleaned[:limit]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _safe_dashscope_prompt(prompt: str) -> str:
    base = _trim_prompt(prompt, 560)
    return (
        "Create a family-friendly educational visual explainer as a clean "
        "non-photorealistic infographic. Use neutral objects, diagrams, arrows, "
        "labels, and simple callouts. Avoid realistic people, injury, weapons, "
        "nudity, politics, medical gore, disturbing imagery, or any unsafe "
        f"content. Subject and layout: {base}"
    )[:800]


def _dashscope_payload(
    model: str,
    prompt: str,
    aspect_ratio: str,
    *,
    safe_mode: bool = False,
) -> dict:
    return {
        "model": model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "text": (
                                _safe_dashscope_prompt(prompt)
                                if safe_mode
                                else _trim_prompt(prompt)
                            )
                        }
                    ],
                }
            ]
        },
        "parameters": {
            "negative_prompt": "low quality, blurry, unreadable text, cluttered layout",
            # The planner already writes a detailed prompt. Letting Qwen-Image
            # extend it can invent details that trip output moderation.
            "prompt_extend": _env_bool("DASHSCOPE_IMAGE_PROMPT_EXTEND", False)
            and not safe_mode,
            "watermark": False,
            "size": _qwen_size(model, aspect_ratio),
            "n": 1,
        },
    }


def _dashscope_error_message(data: dict, fallback: str) -> str:
    message = data.get("message") or data.get("code") or fallback
    return str(message)


def _is_inappropriate_content(message: str) -> bool:
    lowered = message.lower()
    return (
        "inappropriate content" in lowered
        or "data inspection failed" in lowered
        or "content_policy" in lowered
        or "sensitive" in lowered
    )


async def _dashscope_generate_image(
    model: str, prompt: str, aspect_ratio: str
) -> dict:
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is not set")
    endpoint = os.environ.get("DASHSCOPE_IMAGE_ENDPOINT", DASHSCOPE_IMAGE_ENDPOINT)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_message = ""
    for safe_mode in (False, True):
        resp = await _http_client().post(
            endpoint,
            headers=headers,
            json=_dashscope_payload(model, prompt, aspect_ratio, safe_mode=safe_mode),
        )
        try:
            data = resp.json()
        except ValueError:
            data = {"message": resp.text}
        if resp.status_code < 400:
            return data
        last_message = _dashscope_error_message(data, resp.text)
        if safe_mode or not _is_inappropriate_content(last_message):
            break

    raise RuntimeError(f"DashScope image generation failed: {last_message}")


def _first_dashscope_image(result: dict) -> dict:
    output = result.get("output") or {}

    # Qwen-Image synchronous multimodal endpoint currently returns:
    # output.choices[].message.content[].image
    choices = output.get("choices") or []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") or {}
        if not isinstance(message, dict):
            continue
        content = message.get("content") or []
        if isinstance(content, dict):
            content = [content]
        for item in content:
            if isinstance(item, dict) and item.get("image"):
                return {
                    "url": item["image"],
                    "content_type": "image/png",
                }

    # Older docs/examples show output.results[].url. Keep supporting it.
    results = output.get("results") or []
    if not results:
        raise RuntimeError(
            f"DashScope returned no images; output keys: {list(output.keys())}"
        )
    first = results[0]
    if not isinstance(first, dict) or not first.get("url"):
        raise RuntimeError("DashScope image entry malformed")
    return {
        "url": first["url"],
        "content_type": "image/png",
    }


_HTTPX: httpx.AsyncClient | None = None


def _http_client() -> httpx.AsyncClient:
    global _HTTPX
    if _HTTPX is None or _HTTPX.is_closed:
        _HTTPX = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(
                max_keepalive_connections=20, max_connections=50
            ),
        )
    return _HTTPX


async def _fetch_image_bytes(image_info: dict) -> tuple[bytes, str]:
    url = image_info.get("url")
    if not isinstance(url, str) or not url:
        raise RuntimeError("fal image missing url")
    mime = str(image_info.get("content_type") or "image/jpeg")
    resp = await _http_client().get(url)
    resp.raise_for_status()
    return resp.content, mime


def _is_retryable(exc: BaseException) -> bool:
    """fal/transport transients worth retrying. 4xx-other should fail fast.

    fal_client raises its own exception hierarchy (`FalClientHTTPError`,
    `FalClientTimeoutError`) for queue/HTTP failures — NOT bare httpx
    exceptions — so the classifier checks those first. Falls back to httpx
    exceptions for the post-fal CDN download path.
    """
    if isinstance(exc, fal_client.FalClientHTTPError):
        code = exc.status_code
        return code == 429 or 500 <= code < 600
    if isinstance(exc, fal_client.FalClientTimeoutError):
        return True
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or 500 <= code < 600
    return False


async def _fal_subscribe(model: str, arguments: dict) -> dict:
    """fal_client.subscribe_async with bounded exponential backoff.

    Three attempts max. Doesn't retry on auth/4xx-other so a misconfigured
    key fails fast. Wider safety net would mask real bugs.
    """
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    ):
        with attempt:
            return await fal_client.subscribe_async(
                model, arguments=arguments, with_logs=False
            )
    raise RuntimeError("unreachable")  # pragma: no cover
