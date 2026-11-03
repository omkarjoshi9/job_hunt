# Deterministic fit score and company tiers (`fit.py`, `tiers.py`)

- Source: `src/jobdigest/fit.py` (161 lines), `src/jobdigest/tiers.py` (50 lines)
- Layer: ranking. Pure functions, no I/O, no DB, no network.
- Depends on: `jobdigest.config.Profile`, `jobdigest.hard_filter.low_priority_function`, `jobdigest.normalize.normalize_company`.
- Used by: `rank.blend_rank` and `rank.select_diverse` (via `quality_score`), `digest._guest_dicts` (shows `quality_score` as the card number), `digest` urgent path (`fit_score` against `URGENT_MIN_FIT = 70.0`), `hard_filter.hard_filter` (`is_top_company`, lets frontend/QA/mobile titles through at top companies).
- Tests: `tests/test_fit.py` (`test_same_llm_score_no_longer_means_same_rank`, `test_new_grad_outranks_intern_when_otherwise_equal`, `test_core_engineering_outranks_frontend_at_a_top_company`, `test_top_company_and_direct_ats_boost_fit`, `test_sort_rows_uses_quality_not_just_llm_score`, `test_select_diverse_applies_quality_bar_and_caps`); `tests/test_urgent.py` for the urgent gate.

## Why this exists / responsibility

The module docstring says it plainly: the LLM tends to give every plausible job the same high score, so ranking leans on a code-owned score instead. `fit_score` is a 0-100 number built from five components that cap at 30 + 20 + 25 + 15 + 10 = **100**:

| Component | Function | Range | Rewards |
|---|---|---|---|
| Role family | `role_points` | 12-30 | backend/ML/security/platform/data-style titles over generic and UI titles |
| Entry level | `level_points` | 4-20 | new-grad titles first, then internships, then entry-level wording in the JD |
| Skill overlap | `skill_points` | 0-25 | profile skills found in title + description (saturates at 4 hits) |
| Trust | `trust_points` | 1-15 (cap) | top company, watchlist, direct ATS source, informative description |
| Freshness | `freshness_points` | 0-10 | recent posting date |

`quality_score` then blends fit with the LLM score:

`quality = FIT_WEIGHT * fit + LLM_WEIGHT * llm = 0.65 * fit + 0.35 * llm`

"Quality" is the project term for this blend (see the glossary). It is both the base of the ranking key ([rank-and-selection.md](rank-and-selection.md)) and the number printed on every digest card (the digest rounds it into the guest dict's `score` field, so the "88" on a card is a quality, not the raw LLM score).

## Public interface at a glance

| Symbol | Signature | Purpose |
|---|---|---|
| `FIT_WEIGHT`, `LLM_WEIGHT` | `0.65`, `0.35` | Blend weights |
| `role_points` | `(title: str, company: str, profile: Profile) -> float` | 12/14/22/26/30 by title |
| `level_points` | `(title: str, description: str) -> float` | 4/13/15/20 |
| `skill_points` | `(profile: Profile, title: str, description: str) -> float` | 0-25 in steps of 6.25 |
| `trust_points` | `(row: Any, profile: Profile) -> float` | capped at 15 |
| `freshness_points` | `(row: Any, now: datetime) -> float` | 0/2/3/4/7/10 |
| `fit_score` | `(row: Any, profile: Profile, now: datetime) -> float` | sum, clamped to 0-100 |
| `quality_score` | `(row: Any, profile: Profile, now: datetime) -> float` | 0.65 fit + 0.35 LLM |
| `TOP_COMPANIES`, `is_top_company` | `tiers.py` | curated employer list and matcher |

`row` is anything indexable by column name: a `sqlite3.Row` or a dict. The helpers read with `_row`, so missing columns are treated as empty strings.

## Detailed walkthrough: `fit.py`

### Regex constants

All compiled with `re.I`; they run against the job **title** unless stated.

| Name | Used by | Matches (verified examples) |
|---|---|---|
| `_CORE_ROLE` (`fit.py:21`) | `role_points` | `back[- ]?end`, `\bml\b`, `machine learning`, `\bai\b`, `\bllm\b`, `\bgenai\b`, `security`, `platform`, `infra\w*`, `distributed`, `systems?`, `\bdata\b`, `devops`, `\bsre\b`, `cloud`, `research\w*`, `scientist`, `compiler`, `kernel`, `database`. "Backend Engineer", "Data Analyst", "Database Intern", "Research" match; "Embedded" and the hyphenated "machine-learning" do not. `systems?` has no trailing `\b`, so "Systemic" also matches |
| `_FULLSTACK` | `role_points` | `full[- ]?stack` |
| `_GENERIC_SWE` | `role_points` | `software`, `\bsde\b`, `\bswe\b`, `developer`, `engineer`, `programmer` (note: `software`/`developer`/`engineer` are substrings, not word-bounded) |
| `_NEW_GRAD` | `level_points` | `new[- ]?grad`, `graduate`, `\bsde[- ]?(?:1\|i)\b`, `engineer[- ]?i\b`, `fresher`, `early[- ]career`, `entry[- ]level`, `campus`. Verified: "SDE-1", "Graduate Engineer", "Engineer I" |
| `_INTERN` | `level_points` | `\bintern(?:ship)?s?\b`, `trainee`, `apprentice` |
| `_ENTRY_JD` | `level_points` | in the **description**: `freshers?`, `new grad`, `recent graduates?`, `entry-level`, `0 to 1 years`/`0-1 years`, `class of 20xx`, `graduat(e/ing/ion) [in] 20xx` |
| `_RESPONSIBILITIES` | `trust_points` | `responsibilit`, `what you'll do`, `you will`, `day-to-day`, `in this role`: evidence the description is a real JD |

`_SKILL_ALIASES` maps a profile skill (lower-cased) to the phrases that count as a hit: `rag` -> rag / retrieval augmented / retrieval-augmented; `langchain` and `langgraph` -> either word (so one mention of LangGraph counts for both skills); `aws` -> aws / amazon web services; `gcp` -> gcp / google cloud; `security` -> security / appsec / infosec / cybersecurity; `ml` -> machine learning / ml.

### `_row`

**Location** `src/jobdigest/fit.py:56` · `def _row(row: Any, key: str) -> str`. `row[key]` with `KeyError/IndexError/TypeError` -> `""`; `None` -> `""`; otherwise `str(value)`. Everything downstream is string-typed.

### `_parse_iso`

**Location** `src/jobdigest/fit.py:64` · `def _parse_iso(value: str) -> datetime | None`. `datetime.fromisoformat(value.strip().replace("Z", "+00:00"))`; returns `None` on `TypeError/ValueError/AttributeError`; a naive result is assumed UTC. (`rank.py` has its own `_try_parse_iso` that additionally converts to UTC; the two are duplicates with slightly different handling of non-string input.)

### `_word`

**Location** `src/jobdigest/fit.py:72` · `def _word(term: str, text: str) -> bool`. Whole-token match: `(?<![a-z0-9])term(?![a-z0-9])` with `re.escape(term.lower())`. Used for skills so "rag" does not match "storage"/"rage" but does match "uses rag." (verified False/True). `text` must already be lower-cased. Because the boundary is "not alphanumeric", `c++` style skills work.

### `role_points`

**Location** `src/jobdigest/fit.py:76` · **Signature** `def role_points(title: str, company: str, profile: Profile) -> float`
**Purpose** Score the *family* of the role from the title alone.
**Called by** `fit_score`. **Calls** `hard_filter.low_priority_function`. (`company` and `profile` are accepted but unused in the current body.)

Decision order, first match wins:

| Order | Condition | Points |
|---|---|---|
| 1 | `low_priority_function(title)` (frontend / QA / SDET / test / Android / iOS / mobile / UI / web developer *without* any core-function word) | **14** |
| 2 | `_CORE_ROLE` | **30** |
| 3 | `_FULLSTACK` | **26** |
| 4 | `_GENERIC_SWE` | **22** |
| 5 | otherwise | **12** |

The comment at the 14-point line says such titles only reach here at top companies, because `hard_filter` rejects them with reason `function_low` unless `is_top_company`. Verified: "Frontend Engineer" 14, "Android Developer" 14, "Frontend ML Engineer" 30 (the core word `ml` defeats the low-priority test), "ML Engineer" 30, "Full Stack Developer" 26, "Software Engineer" 22, "Analyst" 12.

### `level_points`

**Location** `src/jobdigest/fit.py:88` · `def level_points(title: str, description: str) -> float`
First match wins: `_NEW_GRAD` in **title** 20 (new grad is the priority); `_INTERN` in title 15; `_ENTRY_JD` in **description** 13; else 4. Verified: "SDE-1" 20, "Graduate Engineer" 20, "Intern" 15, "Trainee" 15, "Developer" + "freshers welcome" 13, + "0-1 years" 13, + "class of 2026" 13, plain "Developer" 4. A title that is both ("New Grad Intern") scores 20.

### `skill_points`

**Location** `src/jobdigest/fit.py:98` · `def skill_points(profile: Profile, title: str, description: str) -> float`
1. `hay = f"{title}\n{description}".lower()`.
2. For each `skill` in `profile.skills`: `key = skill.strip().lower()`; skip blanks; `variants = _SKILL_ALIASES.get(key, (key,))`; if any variant is `_word`-present in `hay`, `matched += 1` (each profile skill counts at most once).
3. Return `25.0 * min(matched, 4) / 4.0`.

So each of the first four matched skills is worth 6.25 and extra matches add nothing. With the example profile (9 skills: Python, FastAPI, LangChain, LangGraph, RAG, Docker, AWS, GCP, security) verified: "appsec only" -> 6.25; "Amazon Web Services and google cloud" -> 12.5; "security langgraph machine learning" -> 18.75 (security, langgraph, and langchain via the alias); "python fastapi aws docker langchain rag gcp" -> 25.0.

### `trust_points`

**Location** `src/jobdigest/fit.py:111` · `def trust_points(row: Any, profile: Profile) -> float`
1. `company = _row(row, "company") or _row(row, "display_name")`.
2. `+8` if `is_top_company(company, profile.top_companies)`.
3. `+7` if the case-folded company equals a case-folded entry in `profile.watchlist` (exact match, blank entries ignored). Top and watchlist stack (15).
4. ATS source: `ats = _row(row, "ats").lower()`: `+4` if not in `{"portal", "rss", "marketplace"}` (a direct ATS board), else `+1`.
5. `+3` if `len(description) >= 600` and `_RESPONSIBILITIES` matches (a substantive JD).
6. `return min(points, 15.0)`.

The cap makes the maximum 15; a watchlisted top company on a direct ATS with a real JD would sum to 22 and be capped. Verified: a non-top greenhouse job with a short description 4.0; Stripe via a portal with a short description 9.0 (8 + 1).

### `freshness_points`

**Location** `src/jobdigest/fit.py:127` · `def freshness_points(row: Any, now: datetime) -> float`
1. `stamp = _parse_iso(posted_at) or _parse_iso(first_seen_at)`. The ATS-reported `posted_at` is preferred; the crawler's `first_seen_at` is the fallback (so a re-listed old job is not fresh if the ATS says it is old).
2. No parseable date -> **3.0** (neutral-low).
3. `when = now` (UTC assumed if naive); `age_days = max(0, (when - stamp).total_seconds() / 86400)`. Future dates clamp to 0.
4. Table: age <= 3 days **10**; <= 7 **7**; <= 14 **4**; <= 30 **2**; older **0**.

### `fit_score`

**Location** `src/jobdigest/fit.py:144` · `def fit_score(row: Any, profile: Profile, now: datetime) -> float`
Reads `title`, `description_text`, `company` via `_row`, sums the five components and returns `max(0.0, min(100.0, total))` (the clamp is a safety net; the maximum is already 100).

**Worked example A** (from the test fixtures; "Backend Software Engineer, New Grad" at "Acme" on greenhouse, 1 day old, JD with python/fastapi/aws/docker/langchain/rag/security, LLM score 90, example profile). Output of the real functions:

| Component | Points | Why |
|---|---|---|
| role | 30 | "Backend" hits `_CORE_ROLE` |
| level | 20 | "New Grad" in title |
| skill | 25 | at least 4 skills matched |
| trust | 4 | Acme is not top; greenhouse +4; description (< 600 chars) gets no bonus |
| freshness | 10 | 1 day old |
| **fit** | **89.0** | |
| **quality** | **89.35** | 0.65 * 89 + 0.35 * 90 = 57.85 + 31.5 |

**Worked example B** ("Software Engineer Intern" at Stripe via a `portal`, JD "Python only.", 10 days old, LLM 70): role 22 + level 15 + skill 6.25 + trust 9 + freshness 4 = **56.25**; quality = 0.65 * 56.25 + 0.35 * 70 = 36.5625 + 24.5 = **61.0625**. The LLM would have rated both jobs highly; fit separates them by about 28 quality points.

### `quality_score`

**Location** `src/jobdigest/fit.py:158` · `def quality_score(row: Any, profile: Profile, now: datetime) -> float`
`llm = float(_row(row, "score") or 0)` then `FIT_WEIGHT * fit_score(...) + LLM_WEIGHT * llm`. The row must therefore include the joined `scores.score` column (the digest queries select `scores.score AS score`; see [../storage/contacts-and-digest-queries.md](../storage/contacts-and-digest-queries.md)). If it is absent the LLM part is silently 0, which would cap quality at 65. Irrelevant jobs never get here: their score was stored as 0 and `relevant = 0` rows are filtered out of the digest queries.

## Detailed walkthrough: `tiers.py`

### `TOP_COMPANIES`

**Location** `src/jobdigest/tiers.py:14` - a `frozenset` of lower-case company names, in groups: global (Google, Microsoft, Amazon, Meta, Apple, Nvidia, OpenAI, Anthropic, Stripe, Databricks, Uber, ...), quant/trading (Jane Street, Citadel, D E Shaw, Tower Research, HRT, Optiver, WorldQuant, ...), and Indian product companies (Razorpay, CRED, Zepto, Swiggy, Zomato, Flipkart, PhonePe, Postman, Juspay, ...). Users extend it with `top_companies:` in `profile.yaml`. It serves two purposes (module docstring): let frontend/QA roles through only at top companies (hard filter) and boost fit (+8 trust).

### `_key`

**Location** `src/jobdigest/tiers.py:36` · `def _key(name: str) -> str`. `normalize_company(name)` (strips legal suffixes like "Inc", "Pvt Ltd" and collapses whitespace; see [../foundation/normalize.md](../foundation/normalize.md)), `casefold()`, then every run of characters outside `[a-z0-9 ]` is replaced by a space, and the result is stripped. "Stripe, Inc." -> "stripe"; "Razorpay Software Pvt Ltd" -> "razorpay".

### `is_top_company`

**Location** `src/jobdigest/tiers.py:40` · **Signature** `def is_top_company(name: str, extra: Iterable[str] = ()) -> bool`
**Called by** `fit.trust_points`, `hard_filter.hard_filter`.

1. `key = _key(name)`; empty -> `False`.
2. `pool = TOP_COMPANIES | {_key(x) for x in extra if x}` (user entries are normalised the same way).
3. Exact: `key in pool` -> `True`.
4. Token match: `words = set(key.split())`. True if **any single-word entry** of the pool is one of the words of `key`, **or** any multi-word entry occurs as a contiguous phrase inside `f" {key} "`.

Effect: "Google Cloud India", "Notion Labs", "Jane Street Capital", "Meta Platforms" all match; "Intelligent Systems" does not match "intel" (whole words only). Verified oddity: because a single-word entry matches *anywhere* in the name, "Pizza Slice Inc" matches the entry "slice" and "Cred Club" matches "cred". Short generic entries ("slice", "ola", "navi", "cred", "amd") can produce false positives for unrelated companies whose names contain that word. This only costs a +8 trust bonus and permission for a frontend title to pass the hard filter, but be aware when adding generic words to `top_companies`.

## Failure modes and edge cases

- Titles are matched with unanchored substrings in places (`software`, `engineer`, `systems?`), so unusual titles ("Systemic Risk Analyst") can still earn the 30-point role score. Role points are a ranking nudge, not a gate: gating is done by `hard_filter`.
- A missing `posted_at` and `first_seen_at` gives 3 freshness points rather than 0.
- `fit_score` is deterministic given `(row, profile, now)`; tests pass an explicit `now`.

## How to modify / extend safely

- The five maxima (30/20/25/15/10) are chosen to sum to 100. If you change one, update the others or the clamp will hide the overflow. Several thresholds elsewhere assume the scale: `URGENT_MIN_FIT = 70.0` in `digest.py`, `digest_min_quality` (default 50) in `profile.yaml`, `COOLDOWN_BYPASS_QUALITY = 85.0` in `rank.py`.
- Change `FIT_WEIGHT`/`LLM_WEIGHT` together so they still sum to 1, otherwise `quality` no longer lives on a 0-100 scale and the above thresholds drift.
- Add a skill synonym in `_SKILL_ALIASES` rather than loosening `_word`.
- Add a company by editing `TOP_COMPANIES` (global) or `top_companies` in the user profile (per user). Prefer multi-word names where the single word is generic.
