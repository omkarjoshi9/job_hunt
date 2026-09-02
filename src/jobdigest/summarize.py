from __future__ import annotations

import json
import re
from collections.abc import Callable

from jobdigest.openrouter import OpenRouterError

ChatFn = Callable[..., str]

_CARD_START = re.compile(r"(?=\[id=\d+\])")
_ID_AT_START = re.compile(r"\[id=(\d+)\]")

_SYSTEM = (
    "You are a news anchor writing a short Telegram digest in plain text. "
    "You may only mention jobs that appear in the JSON array. "
    "Do not add companies or roles. Do not fetch URLs. "
    "Every job card must keep that row's url. "
    "Prefix each job card with [id={id}] on its own first line."
)


def fallback_bullets(guest: list[dict]) -> str:
    parts: list[str] = []
    for i, row in enumerate(guest, 1):
        parts.append(
            f"{i}. {row.get('company') or ''} — {row.get('title') or ''}\n"
            f"{row.get('location') or ''}\n"
            f"{row.get('url') or ''}"
        )
    return "\n\n".join(parts)


def _post_validate(text: str, guest: list[dict]) -> str | None:
    by_id = {int(g["id"]): g for g in guest}
    allowed = set(by_id)
    chunks = _CARD_START.split(text)
    intro = ""
    kept: list[str] = []
    for chunk in chunks:
        if not chunk:
            continue
        m = _ID_AT_START.match(chunk)
        if not m:
            intro += chunk
            continue
        job_id = int(m.group(1))
        if job_id not in allowed:
            continue
        url = str(by_id[job_id].get("url") or "")
        if url and url not in chunk:
            if not chunk.endswith("\n"):
                chunk += "\n"
            chunk += url
        kept.append(chunk)
    if not kept:
        return None
    return (intro + "".join(kept)).rstrip() + "\n"


def summarize_digest(
    guest: list[dict],
    *,
    chat_fn: ChatFn,
    model: str,
    api_key: str = "",
) -> tuple[str, str]:
    """Return (text, 'model'|'fallback'|'empty')."""
    if not guest:
        return "", "empty"

    payload = [
        {
            "id": g["id"],
            "company": g.get("company") or "",
            "title": g.get("title") or "",
            "location": g.get("location") or "",
            "url": g.get("url") or "",
            "score": g.get("score"),
            "why": g.get("why") or "",
        }
        for g in guest
    ]
    user = (
        json.dumps(payload, ensure_ascii=False)
        + "\nformat a short Telegram digest in plain text"
    )
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]
    try:
        raw = chat_fn(messages, model=model, api_key=api_key, json_mode=False)
    except OpenRouterError:
        return fallback_bullets(guest), "fallback"

    cleaned = _post_validate(str(raw), guest)
    if cleaned is None:
        return fallback_bullets(guest), "fallback"
    return cleaned, "model"
