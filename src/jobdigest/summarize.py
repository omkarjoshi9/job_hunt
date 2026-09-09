from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime

from jobdigest.openrouter import OpenRouterError
from jobdigest.rank import age_chip, source_chip

ChatFn = Callable[..., str]

_ID_CUT = re.compile(r"\[id=\d+\]")
_URL_RE = re.compile(r"https?://\S+", re.I)
_WORD = re.compile(r"[a-z0-9]{4,}", re.I)
_INTRO_OK = frozenset(
    {
        "today",
        "intern",
        "interns",
        "roles",
        "india",
        "pune",
        "bengaluru",
        "bangalore",
        "digest",
        "jobs",
        "new",
        "grad",
    }
)

_SYSTEM = (
    "You write at most one short intro sentence for a Telegram intern-job digest "
    "in plain text. You may only mention companies that appear in the JSON array. "
    "Do not add companies or roles. Do not fetch URLs. Do not list jobs. "
    "Do not emit [id=] markers. Do not emit apply URLs. Do not emit score or why lines. "
    "Job cards are appended in code after your intro. "
    "If you have nothing safe to say, reply with an empty string."
)

_COMPANY_MAX = 80
_TITLE_MAX = 120
_LOCATION_MAX = 80
_WHY_MAX = 160
_URL_MAX = 300
_INTRO_MAX = 200
_KILL_MAX = 32


def _one_line(value: object, max_len: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:max_len]


def _join_slots(parts: list[str]) -> str:
    return " | ".join(p for p in parts if p)


def _gap_labels(row: dict) -> str:
    labels: list[str] = []
    if row.get("stack_ok", True) is False:
        labels.append("stack")
    if row.get("level_ok", True) is False:
        labels.append("level")
    if row.get("location_ok", True) is False:
        labels.append("location")
    kill = _one_line(row.get("kill") or "", _KILL_MAX)
    if kill:
        labels.append(kill)
    return "+".join(labels)


def render_card(row: dict, n: int, *, now: datetime | None = None) -> str:
    company = _one_line(row.get("company"), _COMPANY_MAX)
    title = _one_line(row.get("title"), _TITLE_MAX)
    location = _one_line(row.get("location"), _LOCATION_MAX)
    score = str(int(round(float(row.get("score") or 0))))
    age = _one_line(row.get("age_chip"), 8)
    if not age and now is not None:
        age = age_chip(row.get("first_seen_at"), now)
    chip = _one_line(row.get("source_chip"), 16)
    if not chip:
        chip = source_chip(row.get("ats"))
    legit = _one_line(row.get("legitimacy_chip"), 24)
    url = _one_line(row.get("url"), _URL_MAX)
    line1 = _join_slots(
        [
            f"{n}. {company} — {title}",
            location,
            score,
            age,
            chip,
            legit,
            url,
        ]
    )
    why = _one_line(row.get("why"), _WHY_MAX)
    gaps = _gap_labels(row)
    if why and gaps:
        line2 = f"Why you: {why} · Gap: {gaps}"
    elif why:
        line2 = f"Why you: {why}"
    elif gaps:
        line2 = f"Gap: {gaps}"
    else:
        line2 = "Why you: —"
    return f"{line1}\n{line2}"


def render_cards(guest: list[dict], *, now: datetime | None = None) -> str:
    return "\n\n".join(render_card(row, i, now=now) for i, row in enumerate(guest, 1))


def fallback_bullets(guest: list[dict]) -> str:
    return render_cards(guest)


def _intro_from_model(raw: str, guest: list[dict]) -> str:
    prefix = raw or ""
    cut = _ID_CUT.search(prefix)
    if cut:
        prefix = prefix[: cut.start()]
    intro = _one_line(_URL_RE.sub("", prefix), _INTRO_MAX)
    if "[id=" in intro:
        return ""
    hay = " ".join(
        str(g.get(k) or "") for g in guest for k in ("company", "title", "location")
    ).casefold()
    for word in _WORD.findall(intro):
        token = word.casefold()
        if token in _INTRO_OK or token in hay:
            continue
        return ""
    return intro


def _compose_digest(raw: str, guest: list[dict]) -> str:
    cards = render_cards(guest)
    intro = _intro_from_model(raw, guest)
    if intro:
        return f"{intro}\n\n{cards}"
    return cards


def summarize_digest(
    guest: list[dict],
    *,
    chat_fn: ChatFn,
    model: str,
    api_key: str = "",
    prompt_version: str,
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
            "age_chip": g.get("age_chip") or "",
            "source_chip": g.get("source_chip") or "",
        }
        for g in guest
    ]
    user = (
        json.dumps(payload, ensure_ascii=False)
        + "\nformat a short Telegram digest in plain text"
    )
    system = f"prompt_version: {prompt_version}\n{_SYSTEM}"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    try:
        raw = chat_fn(messages, model=model, api_key=api_key, json_mode=False)
    except OpenRouterError:
        return render_cards(guest), "fallback"

    return _compose_digest(str(raw), guest), "model"
