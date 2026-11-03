from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from collections.abc import Callable
from typing import Any, Protocol

import httpx

from jobdigest.ats.http_client import parse_json_or_none, request_get

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


class FetchStatus(str, Enum):
    OK = "ok"
    EMPTY_BOARD = "empty_board"
    DEAD_TOKEN = "dead_token"
    ERROR = "error"


@dataclass(frozen=True)
class CompanyConfig:
    id: str
    name: str
    ats: str
    token: str
    enabled: bool = False
    notes: str = ""


@dataclass
class NormalizedJob:
    title: str
    company: str
    location_raw: str
    apply_url: str
    hosted_url: str
    employment_hint: str | None
    workplace_hint: str | None
    description_text: str
    source: str
    ats: str
    external_id: str
    raw_json: str
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    posted_at: datetime | str | None = None


@dataclass
class FetchResult:
    status: FetchStatus
    http_status: int | None
    jobs: list[NormalizedJob]
    error: str | None = None
    complete: bool = True


class BoardAdapter(Protocol):
    ats: str

    def fetch(self, company: CompanyConfig) -> FetchResult:
        ...


def decode_html_content(raw: str | None) -> str:
    if not raw:
        return ""
    text = html.unescape(html.unescape(raw))
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    return _WS.sub(" ", text).strip()


def join_locations(parts: list[str | None]) -> str:
    seen: list[str] = []
    for part in parts:
        s = (part or "").strip()
        if s and s not in seen:
            seen.append(s)
    return " | ".join(seen)


def classify_http_status(status_code: int) -> FetchStatus | None:
    """Non-200 outcomes. Return None if the body must be parsed (HTTP 200)."""
    if status_code in (404, 410):
        return FetchStatus.DEAD_TOKEN
    if status_code in (301, 302, 303, 307, 308):
        return FetchStatus.DEAD_TOKEN
    if status_code != 200:
        return FetchStatus.ERROR
    return None


def empty_or_ok(n_jobs: int) -> FetchStatus:
    if n_jobs == 0:
        return FetchStatus.EMPTY_BOARD
    return FetchStatus.OK


def parse_posted_at(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e12:
            seconds = seconds / 1000.0
        try:
            dt = datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=0)
        except (OSError, OverflowError, ValueError):
            return None
        return dt.isoformat().replace("+00:00", "Z")
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    dt = dt.replace(microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")


def posted_at_from_item(item: dict, *keys: str) -> str | None:
    for key in keys:
        parsed = parse_posted_at(item.get(key))
        if parsed is not None:
            return parsed
    return None


# Single source of truth for ATS types that have an adapter.
KNOWN_ATS = frozenset(
    {"greenhouse", "lever", "ashby", "workable", "smartrecruiters", "bamboohr"}
)


@dataclass
class Page:
    """One fetched JSON page. `failure` is a ready FetchResult when the page is unusable."""

    payload: Any = None
    http_status: int | None = None
    failure: FetchResult | None = None


def get_json_page(
    client: httpx.Client,
    url: str,
    *,
    sleep: Callable[[float], None],
    valid: Callable[[Any], bool],
    label: str,
) -> Page:
    """GET url and map transport/HTTP/shape problems to a failure FetchResult."""
    try:
        response = request_get(client, url, sleep=sleep)
    except httpx.HTTPError as exc:
        return Page(failure=FetchResult(FetchStatus.ERROR, None, [], error=str(exc)))
    code = response.status_code
    terminal = classify_http_status(code)
    if terminal is not None:
        return Page(http_status=code, failure=FetchResult(terminal, code, [], error=None))
    payload = parse_json_or_none(response)
    if not valid(payload):
        return Page(
            http_status=code,
            failure=FetchResult(FetchStatus.ERROR, code, [], error=f"invalid {label} json"),
        )
    return Page(payload=payload, http_status=code)


class JobCollector:
    """Dedupe raw items by id; items without an id are kept as-is."""

    def __init__(self, id_of: Callable[[dict], str]) -> None:
        self._id_of = id_of
        self.by_id: dict[str, dict] = {}
        self.extras: list[dict] = []

    def absorb(self, items: list[dict]) -> None:
        for item in items:
            jid = self._id_of(item)
            if not jid:
                self.extras.append(item)
            elif jid not in self.by_id:
                self.by_id[jid] = item

    def new_ids(self, items: list[dict]) -> set[str]:
        ids = {self._id_of(item) for item in items}
        ids.discard("")
        return ids - set(self.by_id)

    def items(self) -> list[dict]:
        return list(self.by_id.values()) + self.extras

    def __len__(self) -> int:
        return len(self.by_id)
