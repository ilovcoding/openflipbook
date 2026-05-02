"""OpenAI-compatible LLM/VLM client.

Supports two providers behind the same OpenAI SDK surface:

- OpenRouter: set OPENROUTER_API_KEY. Keeps the original `:online` /
  OpenRouter web-plugin behavior.
- Alibaba Cloud Model Studio / DashScope: set LLM_PROVIDER=dashscope and
  DASHSCOPE_API_KEY. Uses https://dashscope.aliyuncs.com/compatible-mode/v1
  by default, with Qwen text + VL models.

The project only needs chat completions and image_url messages, so both
providers stay isolated to this file.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI, BadRequestError

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_OPENROUTER_VLM_MODEL = "google/gemini-3-flash-preview"
DEFAULT_OPENROUTER_TEXT_MODEL = "google/gemini-3-flash-preview"
DEFAULT_DASHSCOPE_VLM_MODEL = "qwen-vl-plus"
DEFAULT_DASHSCOPE_TEXT_MODEL = "qwen-plus"


@dataclass
class PagePlan:
    page_title: str
    prompt: str
    facts: list[str]


@dataclass
class ClickResolution:
    subject: str
    style: str


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    api_key_env: str
    base_url: str
    text_model_env: str
    vlm_model_env: str
    default_text_model: str
    default_vlm_model: str


_OPENAI_CLIENTS: dict[str, AsyncOpenAI] = {}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _provider() -> ProviderConfig:
    requested = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if not requested:
        requested = (
            "dashscope"
            if os.environ.get("DASHSCOPE_API_KEY")
            and not os.environ.get("OPENROUTER_API_KEY")
            else "openrouter"
        )

    if requested in ("dashscope", "aliyun", "bailian", "qwen"):
        return ProviderConfig(
            name="dashscope",
            api_key_env="DASHSCOPE_API_KEY",
            base_url=os.environ.get("DASHSCOPE_BASE_URL", DASHSCOPE_BASE_URL),
            text_model_env="DASHSCOPE_TEXT_MODEL",
            vlm_model_env="DASHSCOPE_VLM_MODEL",
            default_text_model=DEFAULT_DASHSCOPE_TEXT_MODEL,
            default_vlm_model=DEFAULT_DASHSCOPE_VLM_MODEL,
        )

    if requested != "openrouter":
        raise RuntimeError(
            f"Unsupported LLM_PROVIDER={requested!r}; use openrouter or dashscope"
        )

    return ProviderConfig(
        name="openrouter",
        api_key_env="OPENROUTER_API_KEY",
        base_url=OPENROUTER_BASE_URL,
        text_model_env="OPENROUTER_TEXT_MODEL",
        vlm_model_env="OPENROUTER_VLM_MODEL",
        default_text_model=DEFAULT_OPENROUTER_TEXT_MODEL,
        default_vlm_model=DEFAULT_OPENROUTER_VLM_MODEL,
    )


def _client() -> AsyncOpenAI:
    """Provider-keyed singleton AsyncOpenAI client.

    Constructing AsyncOpenAI is cheap individually (~5 ms) but happens up to 4
    times per /sse/generate today; the underlying httpx pool also restarts
    each time, so warm keepalives never benefit. Reuse one instance.
    """
    cfg = _provider()
    cached = _OPENAI_CLIENTS.get(cfg.name)
    if cached is not None:
        return cached

    api_key = os.environ.get(cfg.api_key_env)
    if not api_key:
        raise RuntimeError(f"{cfg.api_key_env} is not set")

    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "base_url": cfg.base_url,
    }
    if cfg.name == "openrouter":
        kwargs["default_headers"] = {
            "HTTP-Referer": os.environ.get(
                "OPENROUTER_REFERER", "https://github.com/eren23/openflipbook"
            ),
            "X-Title": "Endless Canvas",
        }
    client = AsyncOpenAI(**kwargs)
    _OPENAI_CLIENTS[cfg.name] = client
    return client


def _cache_enabled() -> bool:
    return _provider().name == "openrouter" and _env_bool("OPENROUTER_CACHE", True)


def _system_message(text: str) -> dict[str, Any]:
    """System message body. Wraps in a content-block list with `cache_control`
    when caching is enabled, so OpenRouter passes the marker through to
    backends that honour it (Anthropic, Gemini-on-Vertex). Backends that
    don't recognise the marker ignore it silently — no behavior regression.
    """
    if _cache_enabled():
        return {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": text,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    return {"role": "system", "content": text}


def _log_cache_usage(span_ctx: dict[str, Any], response: Any) -> None:
    try:
        usage = getattr(response, "usage", None)
        if not usage:
            return
        details = getattr(usage, "prompt_tokens_details", None)
        cached = (
            getattr(details, "cached_tokens", None)
            if details is not None
            else None
        )
        if cached is not None:
            span_ctx["cached_tokens"] = cached
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        if prompt_tokens is not None:
            span_ctx["prompt_tokens"] = prompt_tokens
    except Exception:  # noqa: BLE001
        pass


def _vlm_model() -> str:
    cfg = _provider()
    return os.environ.get(cfg.vlm_model_env, cfg.default_vlm_model)


def _web_search_enabled(online: bool) -> bool:
    if not online:
        return False
    cfg = _provider()
    if cfg.name == "dashscope":
        return _env_bool("DASHSCOPE_ENABLE_WEB_SEARCH", True)
    return _env_bool("OPENROUTER_ENABLE_WEB_SEARCH", True)


def _supports_online_suffix(model: str) -> bool:
    # Gemini-family on OpenRouter requires the web plugin path; other models
    # accept the `:online` suffix shorthand.
    lowered = model.lower()
    if "gemini" in lowered:
        return False
    return True


def _text_model(online: bool) -> str:
    cfg = _provider()
    base = os.environ.get(cfg.text_model_env, cfg.default_text_model)
    if (
        cfg.name == "openrouter"
        and _web_search_enabled(online)
        and _supports_online_suffix(base)
    ):
        return f"{base}:online"
    return base


def _dashscope_supports_thinking_toggle(model: str) -> bool:
    lowered = model.lower()
    return (
        (
            lowered.startswith("qwen3")
            or lowered.startswith("qwen3.5")
            or lowered.startswith("qwen3.6")
        )
        and "thinking" not in lowered
    )


def _extra_body(model: str, online: bool) -> dict[str, Any]:
    cfg = _provider()
    if cfg.name == "dashscope":
        extra: dict[str, Any] = {}
        if _web_search_enabled(online):
            extra["enable_search"] = True
        if _dashscope_supports_thinking_toggle(model):
            # For this app JSON adherence matters more than visible reasoning.
            # Users can opt back in with DASHSCOPE_ENABLE_THINKING=true.
            extra["enable_thinking"] = _env_bool("DASHSCOPE_ENABLE_THINKING", False)
        return extra

    if _web_search_enabled(online) and not _supports_online_suffix(model):
        return {"plugins": [{"id": "web"}]}
    return {}


def _response_format_unsupported(exc: BadRequestError) -> bool:
    text = str(exc).lower()
    return "response_format" in text or "json_object" in text


async def _json_chat_completion(client: AsyncOpenAI, **kwargs: Any) -> Any:
    """Request JSON mode when supported; retry plain chat on provider mismatch."""
    try:
        return await client.chat.completions.create(
            **kwargs,
            response_format={"type": "json_object"},
        )
    except BadRequestError as exc:
        if _provider().name == "dashscope" and _response_format_unsupported(exc):
            return await client.chat.completions.create(**kwargs)
        raise


async def click_to_subject(
    image_data_url: str,
    x_pct: float,
    y_pct: float,
    parent_title: str,
    parent_query: str,
    output_locale: str | None = None,
    user_hint: str | None = None,
) -> ClickResolution:
    """Resolve the click region to a subject phrase AND a style descriptor.

    The image has a red crosshair at the click point (see
    `apps/web/lib/image-click.ts:annotateClickPoint`); numeric coords are a
    fallback. We also ask the VLM to summarise the illustration's visual
    style so the next page can match it — cheapest way to keep aesthetic
    continuity across hops without a second VLM round-trip.
    """
    client = _client()
    locale_clause = (
        f" The `subject` MUST be written in language code '{output_locale}' — "
        "the next page is being generated in that language and the subject "
        "phrase will be the page title."
        if output_locale and output_locale.lower() not in ("en", "auto", "")
        else ""
    )
    system = (
        "You examine a generated illustration of the page titled "
        f"'{parent_title}' (user query: '{parent_query}'). A red crosshair with "
        "a white halo has been drawn on the image to mark where the user "
        "clicked. Do TWO things and return them as JSON: "
        "(1) `subject` — a 2-8 word noun phrase naming the specific thing "
        "under the crosshair (ignore the crosshair itself); should make a "
        "good next query for a visual explainer. "
        "(2) `style` — a single sentence (<=30 words) describing the "
        "illustration's visual style: art medium (e.g. flat infographic, "
        "watercolor, technical line drawing, photoreal, anime, blueprint), "
        "dominant palette, line work, level of detail, perspective. "
        "Return JSON: {\"subject\": \"...\", \"style\": \"...\"}."
        + locale_clause
    )
    hint_clause = ""
    if user_hint:
        hint_clause = (
            "\n\nUser's note for this click (treat as guidance for what they "
            f"want from the subject phrase): \"{user_hint}\". "
            "Let it shape the angle/framing of the subject if relevant, but "
            "keep the subject concrete and grounded in what's actually under "
            "the crosshair."
        )
    user_text = (
        "Look at the red crosshair marker on the image and tell me the "
        "specific subject beneath it. Also describe the visual style of "
        "the illustration so the next page can be drawn in the SAME style. "
        "If the crosshair is not visible for any reason, fall back to the "
        f"numeric position x={x_pct:.3f}, y={y_pct:.3f} "
        "(0-1 normalized, origin top-left)."
        + hint_clause
    )
    from obs import span

    vlm_model = _vlm_model()
    async with span("vlm.click_to_subject", model=vlm_model) as ctx:
        response = await _json_chat_completion(
            client,
            model=vlm_model,
            messages=[
                _system_message(system),
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {
                            "type": "image_url",
                            "image_url": {"url": image_data_url, "detail": "high"},
                        },
                    ],
                },
            ],
            temperature=0.2,
            max_tokens=300,
            extra_body=_extra_body(vlm_model, online=False) or None,
        )
        _log_cache_usage(ctx, response)
    raw = (response.choices[0].message.content or "{}").strip()
    parsed = _safe_json(raw)
    subject = str(parsed.get("subject", "")).strip()
    style = str(parsed.get("style", "")).strip()
    return ClickResolution(
        subject=subject or parent_title,
        style=style,
    )


async def plan_page(
    query: str,
    web_search: bool,
    style_anchor: str | None = None,
    output_locale: str | None = None,
) -> PagePlan:
    """Produce a page title, image-gen prompt, and factual snippets for the query.

    `style_anchor` (when set) is the parent illustration's visual style as
    described by the click-resolver VLM. We weave it into both the planner
    system prompt AND the final image-gen prompt so the renderer sees an
    explicit style instruction. Without this, generations drift across hops
    (a flat infographic parent can produce a photoreal child).
    """
    client = _client()
    system_parts = [
        "You design a visual-explainer page for a given user query. Return JSON "
        "with keys: page_title (<=8 words, title case), prompt (<=120 words, a "
        "rich description of a single illustrated diagram suitable for a "
        "text-capable image model — include labels, annotations, callouts, and "
        "layout hints), facts (list of 3-6 short factual bullets that should be "
        "visible as labels in the illustration). Do not include any text "
        "outside the JSON."
    ]
    if style_anchor:
        system_parts.append(
            "VISUAL STYLE LOCK (CRITICAL): the new illustration MUST be drawn "
            f"in this exact style — \"{style_anchor}\". Match the medium, "
            "palette, line work, level of stylization, and perspective. Do "
            "NOT switch to a different art style. Begin the `prompt` with a "
            "leading clause that names the style explicitly so the image "
            "model can lock onto it (e.g. \"Flat infographic illustration "
            "with bold blue accents and clean line work, ...\")."
        )
    if output_locale and output_locale.lower() not in ("en", "auto", ""):
        system_parts.append(
            "OUTPUT LANGUAGE LOCK (CRITICAL): `page_title` and every entry in "
            f"`facts` MUST be written in language code '{output_locale}'. The "
            "image-gen prompt itself stays in English (the renderer is "
            "English-trained), BUT it MUST instruct the renderer to draw all "
            "in-image labels, callouts, and on-page text in "
            f"'{output_locale}'. Include a sentence like \"All labels, "
            "callouts, and text inside the illustration are written in "
            f"{output_locale}.\" near the start of the prompt."
        )
    system = " ".join(system_parts)
    user = (
        f"Query: {query}\n\n"
        "Design the illustrated page. Keep the layout readable at 1280x720."
    )
    if style_anchor:
        user += f"\n\nVisual style to preserve verbatim: {style_anchor}"
    from obs import span

    text_model = _text_model(online=web_search)
    async with span(
        "planner.plan_page", model=text_model, web_search=web_search
    ) as ctx:
        response = await _json_chat_completion(
            client,
            model=text_model,
            messages=[
                _system_message(system),
                {"role": "user", "content": user},
            ],
            temperature=0.7,
            max_tokens=900,
            extra_body=_extra_body(text_model, online=web_search) or None,
        )
        _log_cache_usage(ctx, response)
    raw = (response.choices[0].message.content or "{}").strip()
    parsed = _safe_json(raw)
    page_title = str(parsed.get("page_title", query)).strip() or query
    prompt = str(parsed.get("prompt", query)).strip() or query
    facts_raw = parsed.get("facts", [])
    facts: list[str] = []
    if isinstance(facts_raw, list):
        for f in facts_raw:
            if isinstance(f, str) and f.strip():
                facts.append(f.strip())
    return PagePlan(page_title=page_title, prompt=prompt, facts=facts)


async def rewrite_motion_prompt(
    *,
    page_title: str,
    page_prompt: str | None = None,
    image_data_url: str | None = None,
    duration_seconds: int = 5,
) -> str:
    """Rewrite a page title/prompt into a motion-rich video prompt.

    LTX/Wan/Hunyuan i2v models are extremely sensitive to prompt detail —
    feeding them a bare page title produces near-static clips. This helper
    asks a VLM (when an image is supplied) or LLM (when not) to compose a
    cinematographic prompt naming a camera move, the primary subject's
    action, and a short atmospheric beat, capped to one sentence.

    Strictly additive: failures fall back to the original page_title so
    animate never breaks if the LLM provider is misconfigured.
    """
    seed = (page_title or "").strip()
    if not seed:
        return page_prompt or ""
    if os.environ.get("ANIMATE_PROMPT_REWRITE", "true").lower() in (
        "0",
        "false",
        "no",
    ):
        return seed

    client = _client()
    system = (
        "You convert a still illustration's caption into a one-sentence "
        "image-to-video prompt for a diffusion video model (LTX, Wan, "
        "Hunyuan-class). The clip is short — about "
        f"{duration_seconds} seconds — and starts from the supplied still. "
        "Name ONE camera move (slow dolly-in, gentle pan-left, push-out, "
        "static with parallax), ONE subject action that fits the caption, "
        "and ONE atmospheric beat (lighting shift, dust motes, rising steam). "
        "Stay faithful to the caption — do not invent unrelated subjects or "
        "switch art styles. Return ONLY the rewritten sentence, 25-45 words, "
        "no preamble, no quotes."
    )
    user_text_parts = [f"Caption: {seed}"]
    if page_prompt and page_prompt.strip() and page_prompt.strip() != seed:
        user_text_parts.append(f"Scene description: {page_prompt.strip()}")
    user_text = "\n".join(user_text_parts)

    from obs import span

    try:
        async with span("llm.rewrite_motion") as ctx:
            if image_data_url:
                vlm_model = _vlm_model()
                response = await client.chat.completions.create(
                    model=vlm_model,
                    messages=[
                        _system_message(system),
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": user_text},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": image_data_url,
                                        "detail": "low",
                                    },
                                },
                            ],
                        },
                    ],
                    temperature=0.4,
                    max_tokens=160,
                    extra_body=_extra_body(vlm_model, online=False) or None,
                )
            else:
                text_model = _text_model(online=False)
                response = await client.chat.completions.create(
                    model=text_model,
                    messages=[
                        _system_message(system),
                        {"role": "user", "content": user_text},
                    ],
                    temperature=0.4,
                    max_tokens=160,
                    extra_body=_extra_body(text_model, online=False) or None,
                )
            _log_cache_usage(ctx, response)
        rewritten = (response.choices[0].message.content or "").strip()
        return rewritten or seed
    except Exception:  # noqa: BLE001
        return seed


async def polish_edit_instruction(
    instruction: str,
    page_title: str | None = None,
    style_anchor: str | None = None,
) -> str:
    """Expand a terse edit instruction into a model-friendly prompt.

    Skipped at the call site if the instruction is already long. Keeps the
    polish strictly additive — never invents a different operation than what
    the user asked for.
    """
    instruction = instruction.strip()
    if not instruction:
        return instruction
    if len(instruction.split()) > 20:
        return instruction
    client = _client()
    system = (
        "You rewrite a short image-edit instruction into a single sentence "
        "that is concrete enough for an image-editing model to act on. Keep "
        "the user's intent EXACTLY — never add operations they didn't ask "
        "for, never remove ones they did. Aim for 15-30 words. Mention the "
        "subject, where in the frame, and any relevant style cues. Return "
        "ONLY the rewritten instruction, no preamble."
    )
    context_parts = [f"User instruction: {instruction}"]
    if page_title:
        context_parts.append(f"Current page: {page_title}")
    if style_anchor:
        context_parts.append(f"Existing visual style to preserve: {style_anchor}")
    user = "\n".join(context_parts)
    from obs import span

    text_model = _text_model(online=False)
    try:
        async with span("llm.polish_edit") as ctx:
            response = await client.chat.completions.create(
                model=text_model,
                messages=[
                    _system_message(system),
                    {"role": "user", "content": user},
                ],
                temperature=0.3,
                max_tokens=120,
                extra_body=_extra_body(text_model, online=False) or None,
            )
            _log_cache_usage(ctx, response)
        polished = (response.choices[0].message.content or "").strip()
        return polished or instruction
    except Exception:  # noqa: BLE001
        return instruction


def _safe_json(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return {}
    return {}
