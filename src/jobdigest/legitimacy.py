from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from jobdigest.hard_filter import _remote_us
from jobdigest.normalize import collapse_ws, description_hash, normalize_company

AGE_LIMIT_SECONDS = 90 * 86400
EMPTY_DESCRIPTION_HASH = description_hash("")

_RESPONSIBILITY_CUES = (
    "responsibilit",
    "what you will do",
    "what you'll do",
    "what you’ll do",
    "you will",
    "you'll",
    "you’ll",
    "day to day",
    "day-to-day",
    "in this role",
)

_CHIP_LABELS = {
    "age_90d": "90d",
    "company_mismatch": "mismatch",
    "no_responsibilities": "thin",
    "us_lang_india_co": "us",
    "jd_repost": "repost",
}

_CHIP_MAX = 24


def _get(job: Any, key: str, default: Any = None) -> Any:
    if job is None:
        return default
    if isinstance(job, dict):
        return job.get(key, default)
    try:
        return job[key]
    except (KeyError, IndexError, TypeError):
        pass
    return getattr(job, key, default)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _try_parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e12:
            seconds = seconds / 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(dt)


def _company_id_set(job: Any) -> bool:
    cid = _get(job, "company_id")
    if cid is None or cid == "":
        return False
    try:
        return int(cid) != 0
    except (TypeError, ValueError):
        return True


def _is_canonical(job: Any) -> bool:
    value = _get(job, "canonical_job_id")
    return value is None or value == ""


def _src_ext(job: Any) -> tuple[str, str]:
    return (str(_get(job, "source") or ""), str(_get(job, "external_id") or ""))


def _url(job: Any) -> str:
    return str(_get(job, "apply_url_norm") or "")


def _different_identity(job: Any, peer: Any) -> bool:
    return _src_ext(job) != _src_ext(peer) or _url(job) != _url(peer)


def _has_responsibility_cues(text: str) -> bool:
    folded = collapse_ws(text).casefold()
    return any(cue in folded for cue in _RESPONSIBILITY_CUES)


def evaluate_structural(
    job: Any,
    *,
    company_display_name: str | None = None,
    peers: Iterable[Any] = (),
) -> tuple[str, list[str]]:
    codes: list[str] = []
    raw_company = _get(job, "raw_company") or _get(job, "company") or ""
    if _company_id_set(job):
        job_name = normalize_company(str(raw_company)).casefold()
        board_name = normalize_company(company_display_name).casefold()
        if job_name and board_name and job_name != board_name:
            codes.append("company_mismatch")

    description = str(_get(job, "description_text") or "")
    collapsed = collapse_ws(description)
    if len(collapsed) >= 200 and not _has_responsibility_cues(description):
        codes.append("no_responsibilities")

    hard_status = str(_get(job, "hard_status") or "")
    if hard_status == "pass" and _company_id_set(job):
        title = str(_get(job, "title") or "")
        location_raw = str(_get(job, "location_raw") or "")
        blob = f"{title}\n{location_raw}\n{description[:4000]}"
        if _remote_us(blob):
            codes.append("us_lang_india_co")

    desc_hash = str(_get(job, "description_hash") or "")
    if (
        _is_canonical(job)
        and desc_hash
        and desc_hash != EMPTY_DESCRIPTION_HASH
        and len(collapsed) >= 200
    ):
        for peer in peers:
            if not _is_canonical(peer):
                continue
            peer_hash = str(_get(peer, "description_hash") or "")
            if peer_hash != desc_hash:
                continue
            plen = _get(peer, "description_len")
            if plen is not None:
                try:
                    if int(plen) < 200:
                        continue
                except (TypeError, ValueError):
                    pass
            if not _different_identity(job, peer):
                continue
            codes.append("jd_repost")
            break

    return ("suspect" if codes else "ok", codes)


def age_codes(posted_at: Any, first_seen_at: Any, *, now: datetime) -> list[str]:
    dt = _try_parse_dt(posted_at)
    if dt is None:
        dt = _try_parse_dt(first_seen_at)
    if dt is None:
        return []
    now_utc = _as_utc(now)
    if (now_utc - dt).total_seconds() > AGE_LIMIT_SECONDS:
        return ["age_90d"]
    return []


def merge_flag(
    structural_codes: Iterable[str] | None,
    age: Iterable[str] | None,
) -> tuple[str, list[str]]:
    codes: list[str] = []
    seen: set[str] = set()
    for item in list(structural_codes or []) + list(age or []):
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        codes.append(text)
    return ("suspect" if codes else "ok", codes)


def chip(flag: str | None, codes: Iterable[str] | None) -> str:
    seq = [str(c) for c in (codes or []) if str(c).strip()]
    if flag != "suspect":
        return ""
    if len(seq) == 1:
        label = _CHIP_LABELS.get(seq[0])
        if label:
            return f"suspect:{label}"[:_CHIP_MAX]
    return "suspect"[:_CHIP_MAX]
