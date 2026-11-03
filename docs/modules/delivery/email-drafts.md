# Email drafts (`email_draft.py` and templates)

- Source: `src/jobdigest/email_draft.py` (197 lines); `src/jobdigest/templates/email/cold_outreach.j2`, `recruiter_outreach.j2`, `fallbacks.yaml`
- Layer: delivery. Produces a *draft only*: the text is sent to Telegram for the user to copy or open as a `mailto:` link. Nothing is ever sent by SMTP (the footer says "Send manually — no SMTP").
- Depends on: `jinja2`, `yaml`, `config.Profile`, `normalize.collapse_ws`. Contacts come from the [contacts subsystem](../contacts/overview.md) via `db.best_contact_for_job`.
- Used by: `bot._handle_mail` ([bot.md](bot.md#_handle_mail)).
- Tests: `tests/test_email_draft.py`; the bot flow in `tests/test_bot.py::test_handle_update_mail_sends_draft` and friends.

## Why this exists / responsibility

The Mail button on a card should give a ready-to-send, personalised outreach email for the best contact found for that job. The structure is a fixed Jinja template (so tone is predictable and nothing is hallucinated beyond one sentence); the only optional model output is a single "observation" sentence about the company/role, with a canned fallback if no model is available or the call fails.

## Public interface at a glance

| Symbol | Signature | Purpose |
|---|---|---|
| `EmailDraft` | dataclass: `subject, to, body, source, confidence: float\|None, mailto` | Result. |
| `draft_email` | `(*, profile, job_row, contact, chat_fn=None, model=None, api_key="") -> EmailDraft` | Build a draft. |
| `format_telegram` | `(draft) -> str` | Render for Telegram. |
| `_observation`, `_select_fallback`, `_fallback_observations`, `_mailto`, `_row_get` | helpers | Documented below. |
| `_RECRUIT` | `re.compile(r"recruit", re.I)` | Template selector. |
| `_DEFAULT_FALLBACK` | one sentence | Used if `fallbacks.yaml` is unreadable. |

## Detailed walkthrough

### `_ENV` (Jinja environment)

**Location** `src/jobdigest/email_draft.py:23`. `Environment(loader=FileSystemLoader(templates dir), autoescape=select_autoescape(enabled_extensions=()), keep_trailing_newline=True)`. An empty `enabled_extensions` means **no autoescaping** (plain-text email, so `&` stays `&`). The templates directory is resolved relative to the module file, so it works from any working directory and when installed as a package (provided the templates are packaged; see [packaging](../../infra/packaging-and-scripts.md)).

### `_row_get`

`:41`. Reads `key` from `None` (default), a `dict` (`.get`) or a row (`row[key]`, `KeyError/IndexError/TypeError` -> default). Lets tests pass plain dicts and the bot pass DB rows.

### `_fallback_observations` and `_select_fallback`

`_fallback_observations()` (`:57`) loads `fallbacks.yaml` with `yaml.safe_load`; accepts either `{"observations": [...]}` or a bare list; `OSError`/`YAMLError` or a wrong shape return `[]`; each non-empty item goes through `collapse_ws`. The shipped file has three sentences (generic "strong fit", "how this team builds production systems", "India intern bar ... Python services and applied ML").

`_select_fallback(key="")` (`:69`) returns `_DEFAULT_FALLBACK` if the list is empty; otherwise `items[zlib.crc32(key.encode("utf-8")) % len(items)]`. CRC32 is deterministic across processes (unlike Python's salted `hash`), so the same job+contact always gets the same sentence, while different jobs spread across all of them (`test_fallback_selection_deterministic_and_uses_all` shows 200 keys cover every item).

### `_observation`

**Location** `:77` · **Signature** `def _observation(*, profile, job_row, contact, chat_fn, model, api_key) -> str`

1. `title`, `company`, contact `email` via `_row_get`; `fallback_key = f"{company}|{title}|{email}"`.
2. `if chat_fn is None or not (model or "").strip(): return _select_fallback(fallback_key)`: no model, no LLM call.
3. Build two messages: a system prompt ("Write one short sentence observing the company or role. No greeting. No name. Plain English.") and a user message with `company`, `title`, and the profile `skills` joined by commas.
4. `raw = chat_fn(messages, model=model, api_key=api_key, json_mode=False)`; `text = collapse_ws(str(raw or ""))`; if non-empty return `text[:280]`.
5. Any `Exception` (the function is injected, so it can raise anything) logs a warning with traceback and falls through to `_select_fallback(fallback_key)`; an empty response also falls back. `test_draft_cold_includes_github_and_fallback_observation` raises from `chat_fn`, asserts it was called once and that a fallback sentence appears in the body.

### `_mailto`

`:120`. Empty `email` -> `""`. Otherwise `mailto:<email>?subject=<quote(subject)>&body=<quote(body)>`, using `urllib.parse.quote` (default safe `/`; spaces become `%20`, newlines `%0A`). Verified example: `mailto:a@acme.com?subject=SWE%20Intern%20application%20%E2%80%94%20Example%20%282027%20grad%29&body=Hi%20Ann%20Lee%2C%0A%0A...`.

### `draft_email`

**Location** `:130` · **Signature** `def draft_email(*, profile: Profile, job_row: Any, contact: Any, chat_fn: ChatFn | None = None, model: str | None = None, api_key: str = "") -> EmailDraft`

**Called by** `bot._handle_mail`. **Calls** `_observation`, Jinja `get_template(...).render`, `collapse_ws`, `_mailto`.

**Steps**

1. **Template selection** by the contact's `role`: `"recruiter_outreach.j2"` if `_RECRUIT.search(role)` (case-insensitive "recruit" anywhere: "Recruiter", "Technical Recruiting Lead"), else `"cold_outreach.j2"` (hiring managers, founders, etc.). Verified: role "Technical Recruiter" used the recruiter template; role "CTO" the cold one.
2. `observation = _observation(...)`.
3. Build the context: `your_name=profile.name`, `skills=list(profile.skills or [])`, `batch=profile.batch`, `github_url` (stripped profile attribute, `""` if absent), `title`, `company`, `contact_name` (stripped, or `None` when blank so the template says "Hi there,"), `product_observation`.
4. Render and strip surrounding newlines. The first line is the subject: `subject_line, _, rest = rendered.partition("\n")`; `subject = subject_line.removeprefix("Subject:").strip()`; `body = rest.lstrip("\n")`. So both templates must start with a `Subject:` line followed by the body.
5. `to = collapse_ws(contact email).lower()`; `confidence = float(conf_raw)` if not `None` (invalid -> `None`); `source` string.
6. Return `EmailDraft(subject, to, body, source, confidence, mailto=_mailto(to, subject, body))`.

### Templates

- `cold_outreach.j2`: subject `{{ title }} — {{ your_name }}`; greeting `Hi {{ contact_name or "there" }},`; intro line naming company and title; `{{ product_observation }}`; a stack/batch paragraph (`skills | join(", ")`); a GitHub line wrapped in `{% if github_url %}`; closing question "Would you be open to a brief chat this week?".
- `recruiter_outreach.j2`: subject `{{ title }} application — {{ your_name }} ({{ batch }} grad)`; first-person introduction with skills; the observation; "next steps in your hiring process"; optional `GitHub:` line.
- Empty `github_url` removes the line entirely (`test_draft_uses_recruiter_template_and_omits_empty_github` asserts "GitHub" is absent).

### `format_telegram`

**Location** `:183`. Lines: `Subject: ...`, `To: <email or "(no email)">`, blank, body, blank, then `source=<source or "unknown"> confidence=<g-formatted or "n/a"> — Send manually — no SMTP`, and the `mailto:` link as a last line only if it exists. Verified output for a recruiter contact: `source=pattern confidence=0.8 — Send manually — no SMTP`. With no email, `To: (no email)` and no mailto (`test_draft_without_email_skips_mailto`).

## Failure modes and edge cases

- Template missing or Jinja error: `draft_email` raises; the bot maps any exception to "Can't draft: email template missing." ([bot.md](bot.md#_handle_mail)).
- LLM failure never fails the draft; it only changes the observation sentence.
- A contact without an email still produces a draft (useful to copy into LinkedIn) but no `mailto`.
- `confidence` scale depends on the contacts subsystem (tests use 90/100 and the example shows 0.8); `format_telegram` just prints it.

## How to modify / extend safely

- New template: add a `.j2` file starting with a `Subject:` line, and extend the selection in `draft_email`; use only the context keys above or add them to `ctx`.
- New fallback sentences: append to `fallbacks.yaml` (changes which sentence each key maps to, since selection is `crc32 % len`).
