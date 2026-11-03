# Digest rendering and intro guard (`summarize.py`)

- Source: `src/jobdigest/summarize.py` (187 lines)
- Layer: ranking/delivery boundary. Builds the text of the Telegram digest from the selected "guest" dicts.
- Depends on: `jobdigest.openrouter.OpenRouterError`, `jobdigest.rank.age_chip` / `source_chip` ([rank-and-selection.md](rank-and-selection.md)).
- Used by: `jobdigest.digest` (`summarize_digest` for the digest text; `render_card` for urgent cards), `jobdigest.bot` (`render_card` for single-job action cards). See [../delivery/digest.md](../delivery/digest.md).
- Tests: `tests/test_summarize.py` (`test_summarize_drops_invented_ids`, `test_summarize_appends_missing_urls`, `test_summarize_fallback_on_error`, `test_summarize_empty_guest`, `test_fallback_bullets_includes_why_score_chips`, `test_render_card_*`, `test_summarize_model_path_uses_renderer_not_model_card_body`, `test_summarize_system_includes_prompt_version`, `test_summarize_value_error_falls_back`), plus `tests/test_digest.py` and `tests/test_pipeline_e2e.py` through the digest.

## Why this exists / responsibility

The digest must never contain a company, URL, score or job that the pipeline did not select. So the **cards are always rendered by code** from the guest dicts. The LLM is only allowed to contribute an optional one-sentence intro, and even that is filtered by a guard (`_intro_from_model`) that throws it away if it mentions anything not grounded in the guest data. If the LLM fails, the digest is simply the cards with no intro.

## Public interface at a glance

| Symbol | Signature | Purpose |
|---|---|---|
| `summarize_digest` | `(guest, *, chat_fn, model, api_key="", prompt_version) -> tuple[str, str]` | Digest text and path `"model"`, `"fallback"` or `"empty"` |
| `render_card` | `(row: dict, n: int, *, now=None) -> str` | Two-line card for one job |
| `render_cards` | `(guest, *, now=None) -> str` | Cards joined by blank lines |
| `fallback_bullets` | `(guest) -> str` | Alias for `render_cards(guest)` |
| `_intro_from_model`, `_compose_digest` | | Intro guard and assembly |
| `_one_line`, `_join_slots`, `_gap_labels` | | Formatting helpers |

## Detailed walkthrough

### Constants

- Field caps: `_COMPANY_MAX` 80, `_TITLE_MAX` 120, `_LOCATION_MAX` 80, `_WHY_MAX` 160, `_URL_MAX` 300, `_INTRO_MAX` 200, `_KILL_MAX` 32.
- `_ID_CUT = \[id=\d+\]` finds an id marker the model might emit. `_URL_RE = https?://\S+` finds URLs. `_WORD = [a-z0-9]{4,}` (case-insensitive) finds "words" of 4+ alphanumerics, the unit the guard checks.
- `_INTRO_OK`: words always allowed in an intro: today, intern, interns, roles, india, pune, bengaluru, bangalore, digest, jobs, new, grad.
- `_SYSTEM`: instructions to the model: at most one short intro sentence, plain text, mention only companies in the JSON, no added companies/roles, no URLs fetched, no job list, no `[id=]` markers, no apply URLs, no score/why lines, empty string if nothing safe.

### `_one_line`

**Location** `src/jobdigest/summarize.py:51` · `def _one_line(value: object, max_len: int) -> str`: `" ".join(str(value or "").split())[:max_len]`. Collapses all whitespace (including newlines, so multi-line `why` cannot break the two-line card format) and truncates.

### `_join_slots`

**Location** `src/jobdigest/summarize.py:56` · `def _join_slots(parts: list[str]) -> str`: `" | ".join` of the non-empty parts, so an empty slot (no location, no chip) leaves no `| |`.

### `_gap_labels`

**Location** `src/jobdigest/summarize.py:60` · `def _gap_labels(row: dict) -> str`
Builds the "Gap" label from the model's stored reasons. Appends `"stack"` if `row.get("stack_ok", True) is False`, `"level"` if `level_ok is False`, `"location"` if `location_ok is False` (missing key means OK; only an explicit `False` counts), then the `kill` string (collapsed, capped at 32) if present; joins with `+`. Verified: `level_ok=False, kill="yoe"` -> `level+yoe`.

### `render_card`

**Location** `src/jobdigest/summarize.py:74` · **Signature** `def render_card(row: dict, n: int, *, now: datetime | None = None) -> str`
**Called by** `render_cards`, `digest` (urgent: `render_card(guest[0], 1, now=now)`), `bot` (action card). **Calls** `age_chip`, `source_chip`, `_one_line`, `_join_slots`, `_gap_labels`.

**Step-by-step**

1. Clean `company`, `title`, `location` with `_one_line` and their caps.
2. `score = str(int(round(float(row.get("score") or 0))))`. This is the guest dict's `score`, which `digest._guest_dicts` sets to `round(quality_score(...))` (the blend, not the raw LLM number).
3. Age: `row["age_chip"]` (cap 8); if empty and `now` is given, compute `age_chip(row.get("first_seen_at"), now)`. With neither, the slot is omitted.
4. Source chip: `row["source_chip"]` (cap 16), else `source_chip(row.get("ats"))`. Legitimacy chip: `row["legitimacy_chip"]` (cap 24; suspect/age warnings, see [../filtering/legitimacy.md](../filtering/legitimacy.md)). URL capped at 300.
5. Line 1 = `_join_slots([f"{n}. {company} — {title}", location, score, age, chip, legit, url])`.
6. Line 2 by cases: `why` and gaps -> `Why you: {why} · Gap: {gaps}`; only why -> `Why you: {why}`; only gaps -> `Gap: {gaps}`; neither -> `Why you: —`.
7. Return `f"{line1}\n{line2}"`.

Real output:

```
1. Acme — Backend Intern | Pune | 88 | 4h | ats | https://a/1
Why you: Python backend · Gap: level+yoe

2. Beta — SDE | 0 | portal
Why you: —
```

(second card: no location, `score=None` -> 0, no URL, chip derived from `ats="portal"`).

### `render_cards`

**Location** `src/jobdigest/summarize.py:111` · `"\n\n".join(render_card(row, i, now=now) for i, row in enumerate(guest, 1))`, numbering from 1.

### `fallback_bullets`

**Location** `src/jobdigest/summarize.py:115` · `render_cards(guest)`; kept as a named entry point (tested by `test_fallback_bullets_includes_why_score_chips`).

### `_intro_from_model`

**Location** `src/jobdigest/summarize.py:119` · **Signature** `def _intro_from_model(raw: str, guest: list[dict]) -> str`
**Purpose** Sanitise the model's reply down to a safe intro, or `""`.

1. `prefix = raw or ""`; if an `[id=N]` marker exists, keep only the text **before** the first one. (A misbehaving model that starts listing jobs is cut to whatever preceded the list.)
2. Remove URLs (`_URL_RE.sub("", prefix)`), collapse whitespace, cap at 200 -> `intro`.
3. If `"[id="` still appears (e.g. a malformed marker) -> `""`.
4. Build `hay`: casefolded concatenation of every guest's `company`, `title`, `location`.
5. For each 4+ character word in `intro`: allowed if `word.casefold()` is in `_INTRO_OK` or appears **as a substring** of `hay`; the first disallowed word -> return `""` (the whole intro is discarded, not edited).
6. Return `intro`.

Verified: "Fresh intern roles at Acme today." -> `""` ("fresh" is neither allowlisted nor in the guest text); "Great roles at Zeta today" -> `""`; "Hi [id=1] Acme" -> "Hi"; "Acme https://evil.com roles" -> "Acme roles"; a 400-character reply starting with `x` -> `""`. So the guard is deliberately strict: only vocabulary drawn from the jobs themselves plus the small allowlist survives; most free-form model prose is dropped. Words under four characters are never checked. The substring test on `hay` is loose (a word contained inside any title or company passes).

### `_compose_digest`

**Location** `src/jobdigest/summarize.py:138` · `cards = render_cards(guest)`, `intro = _intro_from_model(raw, guest)`; returns `f"{intro}\n\n{cards}"` if an intro survived, else just `cards`. Note `now` is not passed, so age chips come from each guest dict's own `age_chip`.

### `summarize_digest`

**Location** `src/jobdigest/summarize.py:146` · **Signature** `def summarize_digest(guest: list[dict], *, chat_fn: ChatFn, model: str, api_key: str = "", prompt_version: str) -> tuple[str, str]`
**Called by** `digest._run_digest`. **Calls** `chat_fn`, `_compose_digest`, `render_cards`.

1. `if not guest: return "", "empty"` (the chat function is not called; `test_summarize_empty_guest`).
2. `payload` = list of dicts with `id, company, title, location, url, score, why, age_chip, source_chip` per guest, JSON-encoded with `ensure_ascii=False`, followed by the line `format a short Telegram digest in plain text`.
3. `system = f"prompt_version: {prompt_version}\n{_SYSTEM}"` (the version is embedded so a prompt change is visible; `test_summarize_system_includes_prompt_version`).
4. `raw = chat_fn(messages, model=model, api_key=api_key, json_mode=False)` (plain text, not the score schema).
5. `except (OpenRouterError, ValueError)`: return `(render_cards(guest), "fallback")`. `ValueError` is the model guard refusing the configured model ([openrouter.md](openrouter.md)).
6. Success: `(_compose_digest(str(raw), guest), "model")`. Note `"model"` only means the call succeeded; the intro may still have been discarded and the text may be just cards. The digest run stores this path in its `runs` stats.

Test `test_summarize_drops_invented_ids` feeds a model reply containing an invented `[id=99]` FooCorp block; the output keeps the real cards and has no FooCorp, because only the text before the first `[id=` is considered (here "Today").

## Failure modes and edge cases

- The user data (titles, company names) goes to the model as JSON; the job description does not.
- An empty or all-rejected intro is normal, not an error.
- Cards can exceed Telegram's 4096 limit with many guests; splitting is handled by the digest (`split_message`), not here.

## How to modify / extend safely

- Add card fields in `render_card`, and keep the two-line contract (`test_render_card_is_two_lines`). Fields must pass through `_one_line`.
- To allow more intro vocabulary, extend `_INTRO_OK`; never relax the check to trust the model's company names.
- Anything new the model returns must be validated against guest data the way `_intro_from_model` does.
