"""Client for the local llama.cpp vision server (OpenAI-compatible API)."""

from __future__ import annotations

import base64
import io
import json
import re
import time

import requests
from PIL import Image

from config import FIELDS, PROMPT


def prepare_image(image_path: str, max_size: int = 1024) -> str:
    """Resize + compress the card image into a base64 JPEG (cuts CPU prompt load)."""
    with Image.open(image_path) as img:
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.thumbnail((max_size, max_size))
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG", quality=85)
    return base64.b64encode(buffered.getvalue()).decode()


def parse_card_json(raw: str) -> dict:
    """Parse model output into a clean {field: value} dict.

    Robust against: markdown code fences, chatty preambles, missing keys,
    and values like "" / "null" / "N/A" (all normalised to "Null").
    """
    text = (raw or "").strip()

    # 1) strip ```json ... ``` fences if the model ignored the prompt
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    # 2) try direct JSON
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # 3) fall back: first {...} block in the response
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError(f"model returned no JSON (raw: {raw[:160]!r})")
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON from model: {exc}") from exc

    if not isinstance(obj, dict):
        raise ValueError("model returned JSON that is not an object")

    null_tokens = {"", "null", "none", "n/a", "na", "-", "—"}
    clean = {}
    for field in FIELDS:
        value = obj.get(field, "")
        if value is None:
            value = ""
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        value = str(value).strip()
        clean[field] = value if value.lower() not in null_tokens else "Null"
    return clean


def extract_from_image(
    image_path: str,
    server_url: str,
    model: str = "default",
    max_size: int = 1024,
    timeout: int = 600,
):
    """Run one business card through the llama.cpp server.

    Returns (fields, meta):
      fields -> {"First Name": ..., "Last Name": ..., ...} (always all 7 keys)
      meta   -> raw model output + wall time + token counts (for debugging)
    """
    img_b64 = prepare_image(image_path, max_size)
    payload = {
        "model": model,
        "temperature": 0.0,  # deterministic output / zero hallucination
        "messages": [
            {
                "role": "user",
                "content": [
                    # 1) text prompt FIRST to support KV caching
                    {"type": "text", "text": PROMPT},
                    # 2) resized image SECOND
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                    },
                ],
            }
        ],
    }

    started = time.time()
    response = requests.post(
        f"{server_url.rstrip('/')}/v1/chat/completions", json=payload, timeout=timeout
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"llama-server HTTP {response.status_code}: {response.text[:300]}"
        )
    data = response.json()
    try:
        raw = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(
            f"unexpected llama-server response: {str(data)[:300]}"
        ) from exc

    fields = parse_card_json(raw)
    usage = data.get("usage", {}) or {}
    meta = {
        "raw": raw,
        "wall_time": round(time.time() - started, 2),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "cached_tokens": (usage.get("prompt_tokens_details", {}) or {}).get(
            "cached_tokens", 0
        ),
    }
    return fields, meta


def check_server(server_url: str, timeout: int = 6):
    """Lightweight connectivity probe. Returns (ok, human-readable info)."""
    try:
        resp = requests.get(f"{server_url.rstrip('/')}/v1/models", timeout=timeout)
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        models = [m.get("id", "?") for m in resp.json().get("data", [])]
        return True, "connected · " + (", ".join(models) if models else "default model")
    except requests.RequestException as exc:
        return False, f"unreachable ({type(exc).__name__})"
