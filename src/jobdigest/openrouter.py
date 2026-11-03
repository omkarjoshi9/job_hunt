from __future__ import annotations

import json
import os

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_REFERER = "https://openrouter.ai"

SCORE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["jobs"],
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "job_id",
                    "score",
                    "relevant",
                    "level_ok",
                    "location_ok",
                    "stack_ok",
                    "why",
                    "kill",
                ],
                "properties": {
                    "job_id": {"type": "integer"},
                    "score": {"type": "number", "minimum": 0, "maximum": 100},
                    "relevant": {"type": "boolean"},
                    "level_ok": {"type": "boolean"},
                    "location_ok": {"type": "boolean"},
                    "stack_ok": {"type": "boolean"},
                    "why": {"type": "string", "maxLength": 160},
                    "kill": {"type": ["string", "null"]},
                },
            },
        }
    },
}


class OpenRouterError(Exception):
    pass


def chat(
    messages: list[dict[str, str]],
    *,
    model: str,
    api_key: str,
    json_mode: bool = False,
    client: httpx.Client | None = None,
) -> str:
    if model.endswith(":free") or model == "openrouter/auto":
        raise ValueError(f"refused OpenRouter model: {model}")

    payload: dict = {
        "model": model,
        "temperature": 0,
        "messages": messages,
        "provider": {"data_collection": "deny", "require_parameters": True},
    }
    if json_mode:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "job_scores",
                "strict": True,
                "schema": SCORE_SCHEMA,
            },
        }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.environ.get("OPENROUTER_REFERER") or DEFAULT_REFERER,
        "X-Title": "jobdigest",
    }

    own_client = client is None
    http = client if client is not None else httpx.Client(timeout=60.0)
    try:
        try:
            resp = http.post(OPENROUTER_URL, json=payload, headers=headers, timeout=60.0)
        except httpx.HTTPError as exc:
            raise OpenRouterError(str(exc)) from exc
        if resp.status_code != 200:
            raise OpenRouterError(f"HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise OpenRouterError("invalid JSON response") from exc
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise OpenRouterError("missing choices[0].message.content") from exc
        if not isinstance(content, str):
            raise OpenRouterError("missing choices[0].message.content")
        return content
    finally:
        if own_client:
            http.close()


def default_chat(
    messages: list[dict[str, str]],
    *,
    model: str,
    api_key: str,
    json_mode: bool = False,
    client: httpx.Client | None = None,
) -> str:
    """Shared chat wrapper used as the default chat_fn by score/digest."""
    return chat(
        messages, model=model, api_key=api_key, json_mode=json_mode, client=client
    )
