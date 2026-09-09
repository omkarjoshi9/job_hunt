from __future__ import annotations

import re

from jobdigest.config import Profile

_RANGE = re.compile(
    r"(?<!\d)(\d+)\s*(?:[-–]|to)\s*(\d+)\s*(?:\+)?\s*(?:years?|yrs?)\b",
    re.I,
)
_PLUS = re.compile(r"(?<!\d)(\d+)\s*\+\s*(?:years?|yrs?)\b", re.I)
_MIN = re.compile(
    r"(?:minimum|min(?:imum)?|at least|(?<![a-z])over|more than)\s*(\d+)\s*(?:years?|yrs?)\b(?!\s+ago)",
    re.I,
)
_PLUS_N = re.compile(
    r"\bplus\s+(\d+)\s*(?:years?|yrs?)\s+(?:of\s+)?(?:experience|relevant|professional|related)\b",
    re.I,
)

_REMOTE_US = (
    re.compile(r"work authorization in the united states", re.I),
    re.compile(r"eligible to work in the (us|u\.s\.?|united states)", re.I),
    re.compile(r"remote\s*[-–/]?\s*(us|u\.s\.?|usa|united states)\b", re.I),
    re.compile(r"(?<![a-z])(?:us|u\.s\.?|usa|united states)\s*[-–/]\s*remote\b", re.I),
    re.compile(r"\bus[- ]?perm\b", re.I),
    re.compile(r"united states only", re.I),
    re.compile(r"us only remote", re.I),
)

_SWE_TITLE = re.compile(
    r"software|engineer|developer|\bsde\b|\bswe\b",
    re.I,
)
_OPS_TITLE = re.compile(
    r"operations?|\bsupport\b|\bsales\b|marketing|financial crimes|\baml\b|"
    r"\bedd\b|\bpep\b|verifications?",
    re.I,
)


def ops_non_swe(title: str) -> bool:
    text = title or ""
    if not _OPS_TITLE.search(text):
        return False
    return not _SWE_TITLE.search(text)


def yoe_too_high(blob: str, min_yoe_reject: int) -> bool:
    for m in _RANGE.finditer(blob):
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo == 0:
            continue
        if hi < lo:
            continue
        if lo >= min_yoe_reject or (lo >= 1 and hi >= min_yoe_reject):
            return True
    for m in _PLUS.finditer(blob):
        if int(m.group(1)) >= min_yoe_reject:
            return True
    for m in _MIN.finditer(blob):
        if int(m.group(1)) >= min_yoe_reject:
            return True
    for m in _PLUS_N.finditer(blob):
        if int(m.group(1)) >= min_yoe_reject:
            return True
    return False


def _kw_hit(keyword: str, blob: str) -> bool:
    return re.search(r"\b" + re.escape(keyword.lower()) + r"\b", blob) is not None


def _india_ok(profile: Profile, title: str, location_raw: str, desc2000: str) -> bool:
    loc = (location_raw or "").strip()
    blob = f"{title}\n{loc}".lower() if loc else f"{title}\n{desc2000}".lower()
    return any(_kw_hit(kw, blob) for kw in profile.india_keywords)


def _remote_us(blob: str) -> bool:
    return any(pat.search(blob) for pat in _REMOTE_US)


def hard_filter(
    profile: Profile,
    *,
    title: str,
    location_raw: str,
    description_text: str,
    employment_hint: str | None = None,
) -> tuple[str, str]:
    """Return (hard_status, hard_reason) where status is pass|reject."""
    desc2000 = (description_text or "")[:2000]
    blob_yoe = f"{title}\n{location_raw}\n{(description_text or '')}"
    blob_remote = f"{title}\n{location_raw}\n{desc2000}"
    if profile.title_reject_regex and re.search(
        profile.title_reject_regex, title or "", re.I
    ):
        return "reject", "title_reject"
    if yoe_too_high(blob_yoe, profile.min_yoe_reject):
        return "reject", "yoe"
    if profile.reject_remote_us and _remote_us(blob_remote):
        return "reject", "remote_us"
    if not _india_ok(profile, title or "", location_raw or "", desc2000):
        return "reject", "location"
    allow_text = title or ""
    if employment_hint:
        allow_text = f"{allow_text} {employment_hint}"
    intern_ok = bool(
        profile.title_allow_intern
        and re.search(profile.title_allow_intern, allow_text, re.I)
    )
    newgrad_ok = bool(
        profile.title_allow_newgrad
        and re.search(profile.title_allow_newgrad, allow_text, re.I)
    )
    if not intern_ok and not newgrad_ok:
        return "reject", "title_allow"
    if ops_non_swe(title or ""):
        return "reject", "ops_non_swe"
    return "pass", "ok"
