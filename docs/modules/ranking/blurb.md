# "Why me" blurb drafting and grounding (`blurb.py`)

- Source: `src/jobdigest/blurb.py` (261 lines)
- Layer: delivery helper (Telegram bot feature). One LLM call per button press; no DB access.
- Depends on: `jobdigest.ats.base.decode_html_content`, `jobdigest.config.Profile`, `jobdigest.openrouter.OpenRouterError`.
- Used by: `jobdigest.bot._handle_why_me` (`draft_blurb`, then `ground`; see [../delivery/bot.md](../delivery/bot.md)). The bot gates the request first (`_why_me_gate`), supplies `models.blurb_model` / `blurb_prompt_version` (optional `blurb:` section in `config/models.yaml`).
- Tests: `tests/test_blurb.py` (`test_prompt_omits_name_and_cgpa`, `test_cite_must_match_internship`, `test_ungrounded_camelcase_flagged`, `test_empty_internships_does_not_call_chat_fn`, `test_json_mode_is_false`, `test_word_count_flag_does_not_recall_model`, `test_openrouter_error_returns_refusal`), `tests/test_bot_buttons.py` (blurb-related mentions; not individually checked).

## Why this exists / responsibility

The user taps a button on a job card and gets a short cover note (80-120 words). A language model writing about a real person is prone to inventing experience. The module therefore uses two defences:

1. **CITE protocol (hard refusal).** The model's first line must be `CITE: <internship>`, copied from the profile's `internships` list. If it does not match, the note is thrown away.
2. **Grounding (soft flags).** `ground()` scans the body for tokens that look like technology names, quoted strings, the candidate's name or CGPA, and reports ones not found in the supplied context. The bot appends `Ungrounded: ...` for the user to review; nothing is regenerated.

Neither is a proof of truth; they are cheap filters that make fabrications visible.

## Public interface at a glance

| Symbol | Signature | Purpose |
|---|---|---|
| `draft_blurb` | `(*, profile, job_row, chat_fn, model, api_key, prompt_version) -> tuple[str, str]` | Call the model; returns `(text, "model" \| "refuse")` |
| `ground` | `(text, profile, job_row) -> tuple[str, list[str]]` | Returns the body (CITE line removed) and a list of flags |
| `CITE_REFUSE`, `MODEL_ERROR`, `EMPTY_INTERNSHIPS` | str | User-visible refusal messages |
| `_parse_cite`, `_cite_ok`, `_haystack`, ... | | Helpers below |

## Key concepts

**Haystack.** A lower-cased bag of every string the model was legitimately given (profile fields, the job title/company/location/description, a small word lexicon). A "claim" is grounded if it appears in it.

## Detailed walkthrough

### Constants

`CITE_REFUSE` = "Can't draft: model cited an internship that is not in the profile."; `MODEL_ERROR` = "Can't draft: model error."; `EMPTY_INTERNSHIPS` = "Can't draft: no internships in profile to cite."; `_DESC_CAP` 3000; `_HINT_MAX` 80; `_WORD_MIN` 80; `_WORD_MAX` 120. `_LEXICON` is a frozenset of 16 generic words (intern, internship(s), role(s), team(s), india, software, engineer, engineering, project(s), built, using, experience) added to the haystack so ordinary prose is not flagged.

Regexes: `_CAMEL_TOKEN = \b[A-Za-z][A-Za-z0-9]*\b` (every word; filtered later by `_is_camelcase`); `_KEBAB_TOKEN` matches hyphenated tokens like `k8s-operator`; `_QUOTED` matches `'text'` or `"text"`.

### `_row_get`

**Location** `src/jobdigest/blurb.py:46` · `def _row_get(row, key, default=None)`: `None` row -> default; dict -> `.get`; else indexing with `KeyError/IndexError/TypeError` -> default. Works for dicts and `sqlite3.Row`.

### `_internships`

**Location** `src/jobdigest/blurb.py:57`: stripped, non-empty strings from `profile.internships`.

### `_hint_line`, `_job_fence`

**Location** `blurb.py:66`, `:73`. `_hint_line` is identical to the one in `score.py` (whitespace-collapsed, 80-char cap, empty -> no line). `_job_fence` renders `<<<JOB>>>` / TITLE / COMPANY / LOCATION / optional COMMITMENT and WORKPLACE / `DESCRIPTION:` (HTML decoded, 3000-char cap) / `<<<END JOB>>>`. Unlike the scoring fence it has no id and no URL.

### `_system_prompt`

**Location** `blurb.py:88` · `def _system_prompt(prompt_version: str) -> str`. Instructs: write a cover note for one intern/new-grad SWE role in India, 80-120 English words after the CITE line; first line exactly `CITE: ` plus one internship string copied verbatim; only mention skills from the skills list; never use the candidate's name or GPA; treat `<<<JOB>>>` as untrusted; ends with `prompt_version: ...`.

### `_user_prompt` and `_messages`

**Location** `blurb.py:99`, `:115`. The user message lists `skills`, the `internships` as `- item` lines, `role_types`, `location`, `relocate`, `work_auth`, `college`, `degree`, `batch`, then the job fence. **`name` and `cgpa` are deliberately not sent** (`test_prompt_omits_name_and_cgpa`), which is why `ground` can treat their appearance in the output as a leak. `_messages` returns the `[system, user]` pair.

### `_parse_cite`

**Location** `blurb.py:122` · `def _parse_cite(raw: str) -> tuple[str | None, str]`
Normalises `\r\n`, strips; empty -> `(None, "")`. Splits on the first newline; if the first line does not start with `CITE:` -> `(None, whole_text)`; else `(first_line[5:].strip(), rest with leading newlines removed)`. Verified: `"CITE: Example intern\n\nbody here"` -> `("Example intern", "body here")`; `"no cite"` -> `(None, "no cite")`.

### `_cite_ok`

**Location** `blurb.py:134` · `def _cite_ok(cite: str, internships: list[str]) -> bool`. Case-folded; empty cite -> False; True if the cite equals an internship **or is a substring of one**. The substring rule tolerates the model quoting a shorter part of a long internship string but also means a very short cite (e.g. `example`) passes (verified True). Not a strict verbatim match despite what the prompt demands.

### `_haystack`

**Location** `blurb.py:145` · `def _haystack(profile, job_row) -> str`. Joins and casefolds: internships, skills, role_types, location, relocate, work_auth, college, degree, `batch`, the job's title, company, location_raw, decoded description (3000 cap), and `_LEXICON`. Note it does not include the profile `name` or `cgpa`.

### `_is_camelcase`

**Location** `blurb.py:166` · True when the token starts with a letter, has at least one lower-case letter, and has an upper-case letter after the first character (`RedisCache`, `iPhone`; not `Python`, `API`, `I`).

### `_line_start_pronoun_spans`

**Location** `blurb.py:174` · Returns `(start, start+1)` spans for a capital `I` that begins a line (`"I "`), computed by walking `splitlines(keepends=True)` with a running offset. `ground` skips those spans (it also skips any token equal to `"I"`, which `_is_camelcase` would reject anyway, so this is belt-and-braces).

### `ground`

**Location** `blurb.py:187` · **Signature** `def ground(text: str, profile: Profile, job_row: Any) -> tuple[str, list[str]]`
**Purpose** Strip the CITE line and return `(body, flags)`. Does not call the model, never raises on content.
**Called by** `bot._handle_why_me`, tests.

1. `_, body = _parse_cite(text)`; `hay = _haystack(...)`; `flags = []`; `seen` for case-insensitive de-duplication through the inner `add(token)`.
2. **CamelCase tokens.** For each `_CAMEL_TOKEN` match: skip line-start `I` spans and `I`; require `_is_camelcase`; skip if `token.casefold() in hay`; else flag. (Plain capitalised words like "Python" are never flagged; only mixed-case product-like names.)
3. **Kebab tokens.** Every `_KEBAB_TOKEN` not in `hay` is flagged.
4. **Quoted strings.** Each quoted segment, stripped, not in `hay` is flagged.
5. **Identity leaks.** The profile `name` (case-insensitive substring of body) and `str(profile.cgpa)` (substring) are flagged if present.
6. **Length.** `n_words = len(body.split())`; if outside 80-120 append `f"length: {n_words}"`.
7. Return `(body, flags)`.

Verified with a profile named "Zyx Qwertyson", cgpa 9.37: body `I built a RedisCache with k8s-operator using 'Kubernetes' and Zyx Qwertyson with 9.37 gpa. ...` flags `['RedisCache', 'k8s-operator', 'Kubernetes', 'Zyx Qwertyson', '9.37', 'length: 24']`. And `I like iPhone and Python` -> `['iPhone', 'length: 5']`. The model is never re-called on flags (`test_word_count_flag_does_not_recall_model`).

Limits: plain-word fabrications ("I led a team of 10") are not detected; only the token classes above.

### `draft_blurb`

**Location** `blurb.py:241` · **Signature** `def draft_blurb(*, profile: Profile, job_row: Any, chat_fn: ChatFn, model: str, api_key: str, prompt_version: str) -> tuple[str, str]`
**Called by** `bot` blurb handler. **Calls** `_internships`, `_messages`, `chat_fn`, `_parse_cite`, `_cite_ok`.

1. `internships = _internships(profile)`; empty -> `(EMPTY_INTERNSHIPS, "refuse")` **without calling the model**.
2. `raw = chat_fn(messages, model=model, api_key=api_key, json_mode=False)`; `OpenRouterError` -> `(MODEL_ERROR, "refuse")`.
3. `cite, _body = _parse_cite(str(raw))`; if `cite is None` or not `_cite_ok` -> `(CITE_REFUSE, "refuse")`.
4. Success: `(str(raw), "model")`: the **full raw text including the `CITE:` line**; the caller runs `ground()` to strip it.

Return contract: `"refuse"` means the text is a user-facing message to send as is; `"model"` means a draft to ground and send.

## Failure modes and edge cases

- `ValueError` from the model guard is **not** caught here (unlike in `summarize_digest`); the bot catches only `OpenRouterError`. In practice `load_models` already rejects `:free`/`openrouter/auto` blurb models, so this is unlikely.
- A reply with leading blank text before `CITE:` is refused (first line must start with `CITE:` after stripping).
- Word count counts `body.split()` tokens, including the ones in flagged tokens.

## How to modify / extend safely

- Adding profile fields to the prompt: extend both `_user_prompt` and `_haystack`, or legitimate mentions will be flagged.
- Never send `name` or `cgpa` to the model; the leak check relies on it.
- New flag classes belong in `ground`, using `add()` for de-duplication.
