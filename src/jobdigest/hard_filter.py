from __future__ import annotations

import re

from jobdigest.config import Profile
from jobdigest.tiers import is_top_company

_RANGE = re.compile(
    r"(?<!\d)(\d+)\s*(?:[-–]|to)\s*(\d+)\s*(?:\+)?\s*(?:years?|yrs?)\b",
    re.I,
)
_PLUS = re.compile(r"(?<!\d)(\d+)\s*\+\s*(?:years?|yrs?)\b", re.I)
_MIN = re.compile(
    r"(?:minimum|min(?:imum)?|at least)\s*(\d+)\s*(?:years?|yrs?)\b(?!\s+ago)",
    re.I,
)
# "over/more than N years" also appears in company-history text ("over 10 years of
# innovation"), so require experience-ish words right after.
_MIN_OVER = re.compile(
    r"(?<![a-z])(?:over|more than)\s*(\d+)\s*(?:years?|yrs?)\b"
    r"\s+(?:of\s+)?(?:(?:[\w-]+\s+){0,2}?)(?:experience|exp|relevant|industry|professional)\b",
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

# --- code-level quality gates -------------------------------------------------
# These do not depend on the (secret, user-edited) profile regexes, so a loose
# profile can never let non-engineering or experienced roles through.

_STRONG_ENG = re.compile(
    r"engineer|developer|\bsde\b|\bswe\b|software|programmer|devops|\bsre\b|"
    r"scientist|\bsdet\b|\bqa\b|full[- ]?stack|back[- ]?end|front[- ]?end|"
    r"\bandroid\b|\bios\b",
    re.I,
)
_WEAK_ENG = re.compile(
    r"\bml\b|machine learning|\bai\b|\bgenai\b|\bllm\b|research|security|"
    r"\bdata\b|cloud|infra|platform|embedded|firmware",
    re.I,
)
_NON_ENG = re.compile(
    r"\bhr\b|human resources?|talent|recruit\w*|\bcontent\b|copy(?:writer)?\b|"
    r"\bwriter\b|social media|business development|\bbd\b|\bsales\b|marketing|"
    r"growth|go[- ]to[- ]market|\bgtm\b|financ\w*|\baccount(?:s|ing|ant)?\b|"
    r"compliance|secretarial|legal|operations?|\bsupport\b|customer|"
    r"onboarding|\badmin\w*|payroll|procurement|designer|ui/ux|\bux\b|brand|"
    r"community|public relations|\bseo\b|annotator|transcri\w+|data collection|"
    r"verification|specialist|coordinator|executive|insights|evaluation|"
    r"delivery|\bpm\b|product manager|analyst",
    re.I,
)
# Frontend / QA / mobile: only worth surfacing at top-tier companies.
_LOW_FN = re.compile(
    r"front[- ]?end|\bqa\b|\bsdet\b|quality assurance|test(?:ing)? engineer|"
    r"test automation|\bandroid\b|\bios\b|\bmobile\b|\bui\b|web developer",
    re.I,
)
_CORE_FN = re.compile(
    r"back[- ]?end|full[- ]?stack|platform|infra|\bml\b|machine learning|\bai\b|"
    r"\bllm\b|security|\bdata\b|devops|\bsre\b|cloud|systems?|distributed|"
    r"research|scientist",
    re.I,
)
_INTERNISH = re.compile(r"\bintern(?:ship)?s?\b|trainee|apprentice|graduate|fresher", re.I)

_SENIORITY_TITLE = re.compile(
    r"architect|principal|specialist|consultant|evangelist|"
    r"(?:engineer|developer|sde|swe|scientist)\s*[-,:]?\s*(?:ii|iii|iv|v|2|3|4)\b|"
    r"\bsde[- ]?(?:2|3|ii|iii)\b|\bl[3-9]\b",
    re.I,
)
_ENTRY_TITLE = re.compile(
    r"\bintern(?:ship)?s?\b|trainee|apprentice|new[- ]?grad|graduate|fresher|"
    r"junior|\bsde[- ]?1\b|\bsde[- ]?i\b|engineer[- ]?i\b|associate|"
    r"early[- ]career|entry|campus|0[-–]1",
    re.I,
)
_ENTRY_JD = re.compile(
    r"\bfreshers?\b|new[- ]?grad|recent graduates?|entry[- ]level|"
    r"0\s*(?:[-–]|to)\s*1\s*years?|"
    r"no (?:prior|previous) (?:work )?experience|"
    r"graduat(?:e|ing|ion)\W+(?:in\W+)?20\d\d|class of 20\d\d|"
    r"campus (?:hire|hiring|recruit)",
    re.I,
)


def function_ok(title: str) -> bool:
    """True when the title is an engineering/technical role (not HR, sales, ...)."""
    text = title or ""
    strong = bool(_STRONG_ENG.search(text))
    if _NON_ENG.search(text) and not strong:
        return False
    if strong:
        return True
    return bool(_WEAK_ENG.search(text)) and bool(_INTERNISH.search(text))


def low_priority_function(title: str) -> bool:
    """Frontend/QA/mobile titles with no backend/ML/security/platform signal."""
    text = title or ""
    return bool(_LOW_FN.search(text)) and not _CORE_FN.search(text)


def seniority_title(title: str) -> bool:
    """True for titles that signal experienced roles (Engineer II, Architect, ...)."""
    return bool(_SENIORITY_TITLE.search(title or ""))


_INTERN_TITLE = re.compile(r"\bintern(?:ship)?s?\b|trainee|apprentice", re.I)


def entry_level(title: str, description_text: str) -> bool:
    """True when the title or the job text signals an entry-level role."""
    if _ENTRY_TITLE.search(title or ""):
        return True
    return bool(_ENTRY_JD.search(description_text or ""))


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
    for pat in (_MIN, _MIN_OVER):
        for m in pat.finditer(blob):
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
    company: str = "",
) -> tuple[str, str]:
    """Return (hard_status, hard_reason) where status is pass|reject."""
    desc2000 = (description_text or "")[:2000]
    blob_yoe = f"{title}\n{location_raw}\n{(description_text or '')}"
    blob_remote = f"{title}\n{location_raw}\n{desc2000}"
    if profile.title_reject_regex and re.search(
        profile.title_reject_regex, title or "", re.I
    ):
        return "reject", "title_reject"
    # Interns tolerate stray "1 year" mentions; everyone else must be 0-1 years.
    min_yoe = (
        profile.min_yoe_reject
        if _INTERN_TITLE.search(title or "")
        else min(profile.min_yoe_reject, 1)
    )
    if yoe_too_high(blob_yoe, min_yoe):
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
    if not function_ok(title or ""):
        return "reject", "function"
    if seniority_title(title or ""):
        return "reject", "seniority"
    if low_priority_function(title or "") and not is_top_company(
        company, profile.top_companies
    ):
        return "reject", "function_low"
    if not entry_level(title or "", description_text or ""):
        return "reject", "level_unknown"
    return "pass", "ok"
