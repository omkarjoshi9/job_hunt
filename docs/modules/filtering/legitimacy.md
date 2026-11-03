# Legitimacy flags (`jobdigest.legitimacy`)

- **Source:** `src/jobdigest/legitimacy.py` (228 lines)
- **Layer:** filtering / annotation. It never removes a job; it only attaches warnings.
- **Depends on:** `jobdigest.hard_filter._remote_us`, `jobdigest.normalize` (`collapse_ws`, `description_hash`, `normalize_company`).
- **Used by:** structural half: `src/jobdigest/db/jobs.py:_stamp_legitimacy` (called from `_post_process_job` after every insert/update inside `upsert_job`). Age and chip half: `src/jobdigest/digest.py:_guest_dicts` (`merge_flag`, `age_codes`, `chip`) at render time.
- **Tests:** `tests/test_legitimacy.py` (company mismatch, no responsibilities, US-language, JD repost, 90-day age, chip, "does not change score or relevance").

## Why this exists / responsibility

Some listings are technically valid but suspicious: a staffing agency posting under a client's board, a one-line marketing blurb with no duties, a US-only role from an India-hosted board, the same job text re-posted under a new id, or a posting that has been open for months. The legitimacy module computes a **flag** (`ok` or `suspect`) and a list of **reason codes**. The flag is advisory: `tests/test_legitimacy.py::test_evaluate_does_not_change_score_or_relevant` and `::test_blend_rank_identical_with_suspect_flag` pin that it does not affect scoring or ranking; it only produces a short "chip" such as `suspect:90d` that the digest shows next to a job.

The work is split in two because some signals are stable per job and can be stored, while age changes with the clock:

| Half | When computed | Where stored | Codes |
|---|---|---|---|
| structural (`evaluate_structural`) | at ingest, on every upsert | `jobs.legitimacy_flag`, `jobs.legitimacy_reasons_json` (JSON array) | `company_mismatch`, `no_responsibilities`, `us_lang_india_co`, `jd_repost` |
| age (`age_codes`) | at digest render time with `now` | not stored | `age_90d` |

`merge_flag` combines both lists and `chip` renders the label.

## Public interface at a glance

| Symbol | Signature | Purpose |
|---|---|---|
| `evaluate_structural` | `(job, *, company_display_name=None, peers=()) -> tuple[str, list[str]]` | Structural flag + codes. |
| `age_codes` | `(posted_at, first_seen_at, *, now) -> list[str]` | `["age_90d"]` when older than 90 days. |
| `merge_flag` | `(structural_codes, age) -> tuple[str, list[str]]` | Union, de-duplicated, order-preserving. |
| `chip` | `(flag, codes) -> str` | Short label for the digest. |
| `_names_differ`, `_core_name` | `(a, b) -> bool`, `(name) -> (set, str)` | Company-name comparison. |
| `AGE_LIMIT_SECONDS` | `90 * 86400` | Age threshold. |
| `EMPTY_DESCRIPTION_HASH` | `description_hash("")` | `e3b0c442...b855` (sha256 of the empty string); a job with this hash is never a repost. |

## Key concepts

- **Job input is duck-typed.** `_get(job, key, default)` (`legitimacy.py:37`) reads from a dict, a `sqlite3.Row`-style mapping, or an object attribute, in that order, returning `default` for `None` or missing. This lets `db/jobs.py` pass a plain dict and the tests pass dicts or rows.
- **Canonical vs duplicate rows.** `link_duplicate_jobs` (in `db/jobs.py`, see [storage/jobs.md](../storage/jobs.md)) sets `canonical_job_id` on jobs that are cross-source duplicates. `_is_canonical(job)` (`:90`) is "`canonical_job_id` is None or empty". Repost detection only compares canonical rows so a deliberately merged duplicate is not called a repost ("dual hash alias", `tests/test_legitimacy.py::test_dual_hash_alias_is_not_jd_repost`).
- **Peers** are other jobs with the same `description_hash`, queried by `db/jobs.py:_legitimacy_peers` (`WHERE description_hash = ? AND id != ?`), each carrying `description_len` (collapsed length) so that tiny descriptions are not compared.

## Detailed walkthrough

### `evaluate_structural`

**Location** `src/jobdigest/legitimacy.py:138` · **Signature** `def evaluate_structural(job: Any, *, company_display_name: str | None = None, peers: Iterable[Any] = ()) -> tuple[str, list[str]]`

**Purpose.** Return `("suspect", codes)` if any of the four structural checks fires, else `("ok", [])`. Codes are appended in a fixed order: `company_mismatch`, `no_responsibilities`, `us_lang_india_co`, `jd_repost`. **Called by** `db/jobs.py:_stamp_legitimacy`, which builds the `job` dict from the stored row (keys: `company_id`, `raw_company`, `company`, `description_text`, `description_hash`, `source`, `external_id`, `apply_url_norm`, `canonical_job_id`, `hard_status`, `title`, `location_raw`) and `company_display_name` from `companies.display_name`; the result is written with `UPDATE jobs SET legitimacy_flag = ?, legitimacy_reasons_json = ?`. **Calls** `_get`, `_company_id_set`, `normalize_company`, `_names_differ`, `collapse_ws`, `_has_responsibility_cues`, `_remote_us`, `_is_canonical`, `_different_identity`.

**Parameters**

| name | type | default | meaning |
|---|---|---|---|
| `job` | dict / row / object | n/a | Fields listed above. |
| `company_display_name` | `str \| None` | None | The board's configured company name (from the `companies` row); `None` for jobs without a company id. |
| `peers` | iterable | `()` | Other jobs sharing the description hash. |

**Returns** `(flag, codes)`; `flag` is `"suspect"` iff `codes` is non-empty.

**Step-by-step**

1. **`company_mismatch`.** `raw_company = _get(job, "raw_company") or _get(job, "company") or ""`. Only when `_company_id_set(job)` is true (the job came from a configured ATS board, i.e. `company_id` not None/""/0): `job_name = normalize_company(str(raw_company)).casefold()`, `board_name = normalize_company(company_display_name).casefold()`; if both non-empty and `_names_differ(job_name, board_name)` append `"company_mismatch"`. Portal/feed jobs have no `company_id`, so the check is skipped (`test_company_mismatch_skips_portals_without_company_id`). Legal suffixes ("Pvt Ltd", "Software", "Corporation" ...) are stripped by `normalize_company` first (`test_company_mismatch_skips_normalized_legal_suffix`). The real case it targets: a recruiting agency posting on a client's board (`test_company_mismatch_agency_name`).
2. **`no_responsibilities`.** `description = str(_get(job, "description_text") or "")`; `collapsed = collapse_ws(description)`. If `len(collapsed) >= 200` and `not _has_responsibility_cues(description)` -> append. Short descriptions (under 200 characters collapsed) are exempt since they are probably just truncated or empty (`test_no_responsibilities_skips_short_and_empty_sr`); the code fires on a long marketing blurb (`test_no_responsibilities_fires_on_marketing_blurb`).
3. **`us_lang_india_co`.** Only if `hard_status == "pass"` and `_company_id_set(job)`: `blob = f"{title}\n{location_raw}\n{description[:4000]}"`; if `_remote_us(blob)` append. The hard filter already rejects US-only text in the first 2000 characters ([hard-filter](hard-filter.md#_remote_us-and-_remote_us)); this check catches text in characters 2000-4000 of an otherwise-passing job (`test_us_lang_in_first_2000_is_hard_reject_not_guest`, `test_us_lang_after_2000_pass_gets_us_chip`). Beyond 4000 characters nothing is checked.
4. **`jd_repost`.** `desc_hash = str(_get(job, "description_hash") or "")`. Eligibility: `_is_canonical(job)`, a non-empty hash different from `EMPTY_DESCRIPTION_HASH`, and `len(collapsed) >= 200`. Then for each `peer` in `peers`: skip if the peer is not canonical; skip if `peer_hash != desc_hash`; if `description_len` is available and `int(plen) < 200` skip (a non-numeric `plen` is tolerated and not skipped); require `_different_identity(job, peer)`. On the first qualifying peer append `"jd_repost"` and `break`.
5. Return `("suspect" if codes else "ok", codes)`.

   `_different_identity(job, peer)` (`:103`) = `_src_ext(job) != _src_ext(peer) or _url(job) != _url(peer)`, where `_src_ext` is `(source, external_id)` and `_url` is `apply_url_norm`. Same row identity is not a repost of itself; a different source/external id or a different normalized URL with identical text is.

Verified outputs (calling the function offline; `base` is a canonical job from company id 5 with a 460-character description containing "You will"):

| Scenario | Result |
|---|---|
| plain, board "Acme" | `('ok', [])` |
| raw_company "Globex Staffing", board "Acme" | `('suspect', ['company_mismatch'])` |
| 500-char description with no duty phrase | `('suspect', ['no_responsibilities'])` |
| description + "Remote - US", hard_status pass | `('suspect', ['us_lang_india_co'])` |
| same but `company_id=None` | `('ok', [])` |
| peer with same hash, other source/id/url, `description_len=300` | `('suspect', ['jd_repost'])` |
| same peer but `canonical_job_id=3` | `('ok', [])` |
| same peer with `description_len=50` | `('ok', [])` |
| peer with identical source, external id and url | `('ok', [])` |

**Plain-English summary.** Four independent heuristics, each guarded so that missing data (no company id, short text, empty hash) disables the check instead of producing false alarms.

### `_has_responsibility_cues`

**Location** `legitimacy.py:107`. `folded = collapse_ws(text).casefold()`; True if any phrase of `_RESPONSIBILITY_CUES` (`:13`) is a substring: `responsibilit`, `what you will do`, `what you'll do` (straight and curly apostrophe variants), `you will`, `you'll`, `you'll` (curly), `day to day`, `day-to-day`, `in this role`. Substring match, so "Responsibilities" and "responsibility" both hit via the stem.

### `_core_name` and `_names_differ`

**Location** `legitimacy.py:118` and `:124` · **Signatures** `def _core_name(name: str) -> tuple[set[str], str]`, `def _names_differ(a: str, b: str) -> bool`

**Purpose.** Decide whether two company names are genuinely different, tolerant of decoration. `HackerRank Careers` vs `hackerrank` and `Paytm Payments Services` vs `paytmpayments` are the same company (docstring).

**`_core_name(name)`:** split on non-alphanumerics (`re.split(r"[^a-z0-9]+", name.casefold())`), drop empties; remove words in `_GENERIC_NAME_WORDS` (`careers, career, jobs, hiring, recruiting, talent, team, inc, labs, technologies, technology, group, holdings, the, co, ltd`), falling back to all words if that empties the list; return `(set(core), "".join(core))` (word set and glued string).

**`_names_differ(a, b)`:**
1. `wa, ja = _core_name(a)`; `wb, jb = _core_name(b)`.
2. If one word set is a subset of the other (`wa <= wb or wb <= wa`) -> not different (False). This makes "Acme Staffing" vs "Acme" equal, and "Stripe" vs "Stripe Payments" equal.
3. Otherwise compare the glued strings: `short, long_ = sorted((ja, jb), key=len)`; they are *not* different if `len(short) >= 4 and long_.startswith(short)` (handles "Paytm Payments Services" = glued `paytmpaymentsservices` vs `paytmpayments`).
4. Else True.

Verified: ("HackerRank Careers","hackerrank") False; ("Paytm Payments Services","paytmpayments") False; ("Acme Staffing","Acme") False; ("Razorpay Software","Razorpay") False; ("TCS","Stripe") True; ("Go","Goldman Sachs") True (short name under 4 chars); ("Acme Corp","Zeta Labs") True. Oddity: ("Meta","Metabase") is False (`metabase` starts with `meta`, length 4), a false negative, accepted by the prefix rule.

### `_get`

**Location** `src/jobdigest/legitimacy.py:37` · **Signature** `def _get(job: Any, key: str, default: Any = None) -> Any`
**Purpose** One accessor for the three shapes a "job" takes in this module: a plain `dict` (what `db/jobs.py:_stamp_legitimacy` and most tests pass), a `sqlite3.Row`/psycopg-shim row, and an object with attributes.
**Called by** `evaluate_structural`, `_company_id_set` and the `_is_canonical`/identity helpers (about a dozen call sites, `legitimacy.py:81`-`178`). **Calls** nothing outside builtins.
**Step by step**
1. `job is None` -> `default`.
2. `isinstance(job, dict)` -> `job.get(key, default)` (returns immediately, never tries attributes).
3. Otherwise `try: return job[key]`; `KeyError`, `IndexError`, `TypeError` are swallowed (an unknown column in a `sqlite3.Row` raises `IndexError`, a non-subscriptable object `TypeError`).
4. Fall back to `getattr(job, key, default)`.
`default` applies only when the key is **absent**: a key that exists with value `None` returns `None`, not `default` (callers therefore write `_get(job, "x") or ""`). Verified offline: `_get(None,"a","D")` -> `'D'`; `_get({"a":1},"a")` -> `1`; `_get({"a":None},"a","D")` -> `None`; `_get({},"a","D")` -> `'D'`; a `sqlite3.Row` with column `a=1` -> `1`, unknown column -> `'D'`, NULL column -> `None`; `SimpleNamespace(a=5)` -> `5`; empty namespace and a list -> `'D'`. (The sentence in Key concepts saying `default` is returned "for `None`" is only true for a `None` *job*, not a `None` value.)

### `_try_parse_dt`

**Location** `src/jobdigest/legitimacy.py:55` · **Signature** `def _try_parse_dt(value: Any) -> datetime | None`
**Purpose** Turn whatever an ATS or the DB put in `posted_at`/`first_seen_at` (ISO strings, datetimes, epoch seconds or milliseconds) into a timezone-aware UTC `datetime`, or `None` when it cannot. It never raises, so a bad date degrades to "no age signal" instead of aborting the digest.
**Called by** `age_codes` (twice: `posted_at`, then `first_seen_at` as fallback). **Calls** `_as_utc`, `datetime.fromtimestamp`, `datetime.fromisoformat`.
**Returns** aware UTC `datetime` or `None`.
**Step by step (first matching rule wins)**
1. `None` -> `None`.
2. `datetime` -> `_as_utc(value)` (naive values are assumed UTC, aware ones converted).
3. `bool` -> `None`. This precedes the numeric rule because `bool` is a subclass of `int` (`True` would otherwise parse as 1 second after the epoch).
4. `int`/`float`: `seconds = float(value)`; `if seconds > 1e12: seconds = seconds / 1000.0` (values above 1e12 are taken as milliseconds); `datetime.fromtimestamp(seconds, tz=timezone.utc)`; `OSError`, `OverflowError`, `ValueError` -> `None`.
5. Otherwise `text = str(value).strip()`; empty -> `None`; `datetime.fromisoformat(text.replace("Z", "+00:00"))` (the `Z` replacement is needed because `fromisoformat` accepts a bare `Z` only on newer Pythons; it is a no-op otherwise); `ValueError` -> `None`; success -> `_as_utc(dt)`.
```python
if isinstance(value, (int, float)):
    seconds = float(value)
    if seconds > 1e12:
        seconds = seconds / 1000.0
```
Verified offline (all results UTC-aware): `'2026-10-30T10:00:00Z'` -> `2026-10-30 10:00+00:00`; `'2026-01-01 00:00:00'` -> midnight UTC; `'2026-10-30'` -> midnight UTC; an aware `+05:30` datetime -> shifted to `2026-01-01 00:00 UTC`; `1700000000` and `1700000000000` -> both `2023-11-14 22:13:20+00:00`; `True`, `'garbage'`, `'  '`, `nan`, `inf`, `1e30` -> `None`.
**Oddities** A numeric *string* such as `'1700000000'` returns `None` (it goes to `fromisoformat`), while the int `20261030` parses as epoch seconds (1970-08-23), so date-like integers are silently misread. Epoch values between 1e9 and 1e12 are all treated as seconds, so millisecond stamps below 1e12 (before 2001-09-09) would be misread; irrelevant for real postings.

### `_company_id_set`

**Location** `legitimacy.py:80`. False for `None` / `""`; otherwise `int(cid) != 0`; a non-numeric value counts as set (`True`). Distinguishes ATS-board jobs (have a `companies.id`) from portal/feed/marketplace jobs.

### `age_codes`

**Location** `src/jobdigest/legitimacy.py:193` · **Signature** `def age_codes(posted_at: Any, first_seen_at: Any, *, now: datetime) -> list[str]`

**Purpose.** Flag postings older than 90 days. **Called by** `digest._guest_dicts` for each selected job, never during ingest (age depends on `now`).

**Steps.** `dt = _try_parse_dt(posted_at)`; if that is `None`, fall back to `_try_parse_dt(first_seen_at)`; if still `None` return `[]`. `now_utc = _as_utc(now)`. If `(now_utc - dt).total_seconds() > AGE_LIMIT_SECONDS` return `["age_90d"]`, else `[]`. Strictly greater than 90 days: exactly 90 days is OK, 90 days plus one second is flagged (`test_age_90d_at_91_days_not_at_89`). The posted date, when parseable, beats first-seen. Note that an unparseable `posted_at` also falls through to `first_seen_at`.

Helpers: `_as_utc(value)` (`:49`) makes naive datetimes UTC and converts aware ones to UTC. `_try_parse_dt(value)` (`:55`): `None` -> None; `datetime` -> UTC; `bool` -> None; int/float -> epoch seconds (values `> 1e12` are treated as milliseconds), invalid -> None; otherwise string, stripped, `Z` replaced by `+00:00`, `datetime.fromisoformat`, `ValueError` -> None. Verified: `age_codes(1.6e12, None, now=2026-11-03)` -> `['age_90d']`; `age_codes("garbage", "2026-10-30", ...)` -> `[]`; `age_codes(None, "2026-01-01 00:00:00", ...)` -> `['age_90d']`.

### `merge_flag`

**Location** `legitimacy.py:205` · **Signature** `def merge_flag(structural_codes: Iterable[str] | None, age: Iterable[str] | None) -> tuple[str, list[str]]`

Concatenates `structural_codes` then `age`, strips each, drops empty and duplicate items while preserving first-seen order, returns `("suspect" if codes else "ok", codes)`. `None` is accepted for either. Verified: `merge_flag(["jd_repost"], ["age_90d","jd_repost"])` -> `('suspect', ['jd_repost','age_90d'])`; `merge_flag([], [])` and `(None, None)` -> `('ok', [])`. In `digest._guest_dicts` the structural codes come from `digest._legitimacy_codes(legitimacy_reasons_json)`, a defensive JSON parser (bad JSON or non-list -> `[]`).

### `chip`

**Location** `legitimacy.py:220` · **Signature** `def chip(flag: str | None, codes: Iterable[str] | None) -> str`

Builds the digest label. Not suspect -> `""`. Exactly one code with a known label -> `suspect:<label>` using `_CHIP_LABELS` (`age_90d`->`90d`, `company_mismatch`->`mismatch`, `no_responsibilities`->`thin`, `us_lang_india_co`->`us`, `jd_repost`->`repost`), truncated to `_CHIP_MAX = 24`. Zero codes, several codes or an unknown code -> plain `"suspect"`. Verified: `chip("suspect",["age_90d"])` = `suspect:90d`; two codes = `suspect`; `["weird"]` = `suspect`; `chip("ok",[])`, `chip(None,None)` = `""`; `chip("suspect", [])` = `suspect`. The result also reaches `summarize.py` as the `legitimacy_chip` field of a job row (shown to the LLM summarizer as a 24-character line).

## Failure modes and edge cases

- **False positives:** `no_responsibilities` can fire on any long description that does not use the listed phrases (e.g. German or telegraphic text). `company_mismatch` depends on how the board name is configured in `companies.yaml`; a parent/subsidiary with unrelated names flags.
- **False negatives:** prefix rule in `_names_differ`; `jd_repost` is skipped when the description is under 200 characters or the hash is empty; `us_lang_india_co` only looks at the first 4000 characters and only for `hard_status == 'pass'` jobs from configured boards.
- **Stored flag staleness:** the structural flag is recomputed on every upsert (so it follows description changes) but a job no longer on its board is not re-evaluated. The repost check depends on peer rows at the time of evaluation: when job A is stamped first and a same-text job B arrives later, B can be flagged `jd_repost` while A stays `ok` until A is upserted again (inferred from the code order; peers are read at stamp time).
- **Age uses `posted_at` when present**, which some sources set to a stale "created" time.

## How to modify / extend safely

- Add a new code in `evaluate_structural` (append in the existing order), add its label to `_CHIP_LABELS` so single-code chips are readable, and add a test in `tests/test_legitimacy.py`.
- Remember the stored JSON is a plain array of strings; `digest._legitimacy_codes` keeps unknown strings, and `chip` degrades unknown codes to `suspect`.
- Do not use the flag to filter or rank without revisiting the tests that pin "advisory only".
- Keep `_get` duck-typing: `_stamp_legitimacy` passes a dict, tests pass dicts, other callers may pass rows.
