from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from jobdigest.config import Profile
from jobdigest.normalize import collapse_ws

ChatFn = Callable[..., str]

_TEMPLATES = Path(__file__).resolve().parent / "templates" / "email"
_ENV = Environment(
    loader=FileSystemLoader(str(_TEMPLATES)),
    autoescape=select_autoescape(enabled_extensions=()),
    keep_trailing_newline=True,
)
_RECRUIT = re.compile(r"recruit", re.I)


@dataclass
class EmailDraft:
    subject: str
    to: str
    body: str
    source: str
    confidence: float | None
    mailto: str


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _select_fallback() -> str:
    path = _TEMPLATES / "fallbacks.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError:
        data = None
    items = []
    if isinstance(data, dict):
        items = data.get("observations") or []
    elif isinstance(data, list):
        items = data
    if isinstance(items, list) and items:
        return collapse_ws(str(items[0]))
    return "This intern/new-grad role looks like a strong fit for the stack I've been shipping with."


def _observation(
    *,
    profile: Profile,
    job_row: Any,
    contact: Any,
    chat_fn: ChatFn | None,
    model: str | None,
    api_key: str,
) -> str:
    if chat_fn is None or not (model or "").strip():
        return _select_fallback()
    title = str(_row_get(job_row, "title") or "")
    company = str(_row_get(job_row, "company") or "")
    messages = [
        {
            "role": "system",
            "content": (
                "Write one short sentence observing the company or role. "
                "No greeting. No name. Plain English."
            ),
        },
        {
            "role": "user",
            "content": (
                f"company: {company}\n"
                f"title: {title}\n"
                f"skills: {', '.join(str(s) for s in (profile.skills or []))}\n"
            ),
        },
    ]
    try:
        raw = chat_fn(messages, model=model, api_key=api_key, json_mode=False)
        text = collapse_ws(str(raw or ""))
        if text:
            return text[:280]
    except Exception:
        pass
    _ = contact
    return _select_fallback()


def _contact_get(contact: Any, key: str, default: Any = None) -> Any:
    return _row_get(contact, key, default)


def _mailto(email: str, subject: str, body: str) -> str:
    if not email:
        return ""
    return (
        f"mailto:{email}"
        f"?subject={quote(subject)}"
        f"&body={quote(body)}"
    )


def draft_email(
    *,
    profile: Profile,
    job_row: Any,
    contact: Any,
    chat_fn: ChatFn | None = None,
    model: str | None = None,
    api_key: str = "",
) -> EmailDraft:
    role = str(_contact_get(contact, "role") or "")
    template_name = (
        "recruiter_outreach.j2" if _RECRUIT.search(role) else "cold_outreach.j2"
    )
    observation = _observation(
        profile=profile,
        job_row=job_row,
        contact=contact,
        chat_fn=chat_fn,
        model=model,
        api_key=api_key,
    )
    github_url = str(getattr(profile, "github_url", "") or "").strip()
    ctx = {
        "your_name": profile.name,
        "skills": list(profile.skills or []),
        "batch": profile.batch,
        "github_url": github_url,
        "title": str(_row_get(job_row, "title") or ""),
        "company": str(_row_get(job_row, "company") or ""),
        "contact_name": str(_contact_get(contact, "name") or "").strip() or None,
        "product_observation": observation,
    }
    rendered = _ENV.get_template(template_name).render(**ctx).strip("\n")
    subject_line, _, rest = rendered.partition("\n")
    subject = subject_line.removeprefix("Subject:").strip()
    body = rest.lstrip("\n")
    to = collapse_ws(str(_contact_get(contact, "email") or "")).lower()
    conf_raw = _contact_get(contact, "confidence")
    try:
        confidence = float(conf_raw) if conf_raw is not None else None
    except (TypeError, ValueError):
        confidence = None
    source = str(_contact_get(contact, "source") or "")
    return EmailDraft(
        subject=subject,
        to=to,
        body=body,
        source=source,
        confidence=confidence,
        mailto=_mailto(to, subject, body),
    )


def format_telegram(draft: EmailDraft) -> str:
    to_line = draft.to or "(no email)"
    conf = "" if draft.confidence is None else f" {draft.confidence:g}"
    source = draft.source or "unknown"
    parts = [
        f"Subject: {draft.subject}",
        f"To: {to_line}",
        "",
        draft.body,
        "",
        f"source={source} confidence={conf.strip() or 'n/a'} — Send manually — no SMTP",
    ]
    if draft.mailto:
        parts.append(draft.mailto)
    return "\n".join(parts)
