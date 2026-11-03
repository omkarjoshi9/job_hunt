# Contacts: GitHub org recruiters and JD name parsing

- **Source**: `src/jobdigest/contacts/github.py` (126 lines), `src/jobdigest/contacts/jd.py` (91 lines).
- **Role**: the two candidate-person sources. JD parsing is local; GitHub needs a token.
- **Used by**: [`run_pipeline`](pipeline-and-run.md#run_pipeline); `github_headers`/`github_token` are also used by [`pattern.py`](domain-and-pattern.md).
- **Tests**: `tests/test_contacts_github.py`, `tests/test_contacts_jd.py`; fixtures `tests/fixtures/contacts/github_*.json`, `jd_*.txt`.

## GitHub

Idea: public GitHub org members sometimes list a recruiting job in their profile bio. The module lists up to 20 members of the org derived from the company domain, fetches each profile, and keeps those whose bio mentions recruiting/talent terms.

Constants: `_BIO_RE = re.compile(r"recruit|talent|campus|university|hiring|people|\bhr\b", re.I)`; `_MEMBER_CAP = 20`.

### `github_token`

**Location** `src/jobdigest/contacts/github.py:20` · `def github_token() -> str`. Returns `(os.environ.get("JOBDIGEST_GITHUB_TOKEN") or "").strip()`. Deliberately not `GITHUB_TOKEN`, which Actions injects with different scope (`test_github_token_ignores_actions_token`). Callers: `run_pipeline`, `github_headers`.

### `github_headers`

**Location** `src/jobdigest/contacts/github.py:24` · `def github_headers(token: str | None = None) -> dict[str, str]`

Builds `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`, `User-Agent` (`JobHunt/0.1`). Token resolution: `(token if token is not None else github_token()).strip()`; non-empty adds `Authorization: Bearer <token>`. As in `run_pipeline`, `token=""` means "no auth, do not fall back to env". Callers: `github_org_recruiters`, `pattern.email_pattern_detection`.

### `_guess_role`

**Location** `src/jobdigest/contacts/github.py:36` · `def _guess_role(bio: str) -> str`. Lowercases the bio; `recruit|talent|\bhr\b|people` -> `"Recruiter"`; else `campus|university|hiring` -> `"University Recruiter"`; else `"Recruiter"`. Order matters: "University hiring" gives `University Recruiter`, but "campus talent" gives `Recruiter` because the first test hits "talent". Verified outputs: `"Technical recruiter"` -> Recruiter, `"University hiring"` -> University Recruiter, `"People ops"` -> Recruiter, `"campus talent"` -> Recruiter.

### `_fetch_members`

**Location** `src/jobdigest/contacts/github.py:45` · `def _fetch_members(slug: str, client: httpx.Client, headers: dict[str, str]) -> list[Any]`. `GET /orgs/{slug}/members?per_page=30`, `timeout=10`. Non-200 (including 403/404) logs at info and returns `[]`; `httpx.HTTPError`/`ValueError` logs a warning and returns `[]`; a non-list JSON body returns `[]`. The caller truncates to 20, so 10 of the 30 requested are never used.

### `_fetch_user`

**Location** `src/jobdigest/contacts/github.py:64` · `def _fetch_user(login: str, client: httpx.Client, headers: dict[str, str]) -> dict[str, Any] | None`. `GET /users/{login}`, `timeout=5`; non-200, HTTP/JSON errors, or a non-dict body give `None`.

### `_contact_from_user`

**Location** `src/jobdigest/contacts/github.py:82` · `def _contact_from_user(data: dict[str, Any]) -> DiscoveredContact | None`

1. `bio = str(data.get("bio") or "")`; if `_BIO_RE` does not match -> `None` (this is what filters engineers out: `github_user_engineer.json` vs `github_user_recruiter.json`).
2. `name = collapse_ws(data.get("name") or "")`; no display name -> `None` (login alone is not used).
3. `email = collapse_ws(data.get("email") or "").lower() or None`; any address containing `noreply` is discarded.
4. Return `DiscoveredContact(name, source="github", role=_guess_role(bio), email=email, confidence=70.0)`. `company`/`domain` are filled later by the merge. The email here is observed (a public profile email), so `email_inferred` stays `False`.

Verified: bio `"Talent @ Acme"` with name `"A  B"` and a `users.noreply.github.com` email yields name `A B`, email `None`, role `Recruiter`; bio `"I build things"` yields `None`. Because the bio regex includes the substring `people` and `hiring`, a bio like "we love hiring great people" or "I help people learn" also qualifies; precision is modest.

### `github_org_recruiters`

**Location** `src/jobdigest/contacts/github.py:101` · `def github_org_recruiters(org_slug: str, client: httpx.Client, *, token: str | None = None) -> list[DiscoveredContact]`

1. `slug = (org_slug or "").strip()`; empty -> `[]`.
2. `headers = github_headers(token)`; `out`, `seen` (casefolded names).
3. For each member in `_fetch_members(...)[:_MEMBER_CAP]`: skip non-dicts or missing `login`; `_fetch_user`; `_contact_from_user`; skip `None`; skip a casefolded duplicate name; otherwise append.
4. Return `out`.

Cost: up to 1 + 20 requests per call, sequential, per job (no cross-job caching). Tests: `test_github_org_recruiters_filters_engineers`, `test_github_org_403_is_empty`. The function itself does not guard against exceptions other than HTTP/JSON ones; `run_pipeline` adds a guard for `HTTPError`/`ValueError` and a final catch-all.

## JD name parsing

Idea: job descriptions occasionally state "Reports to: X" or "Recruiter: X". Extract the capitalised name after such a label and reject lookalikes (job titles, degrees).

### Constants

- `_PART = r"[A-Z][a-z]+(?:['-][A-Za-z]+)?"`: one name word: capital + lowercase letters, optionally followed by an apostrophe or hyphen plus letters (`Anne-Marie`, `D'Souza`).
- `_NAME = rf"({_PART}(?:\s+{_PART}){{0,2}})"`: one to three such words, captured.
- `_LABELS`: tuples `(compiled regex, role, confidence)`:

| Regex (prefix) | Role | Confidence |
|---|---|---|
| `(?i)reports\s+to[:\s]+(?-i:NAME)` | Hiring Manager | 100.0 |
| `(?i)hiring\s+manager[:\s]+(?-i:NAME)` | Hiring Manager | 100.0 |
| `(?i)recruiter[:\s]+(?-i:NAME)` | Recruiter | 90.0 |
| `(?i)HR\s+contact[:\s]+(?-i:NAME)` | Recruiter | 90.0 |

The label is case-insensitive but the name group uses the inline scoped flag `(?-i:...)` so it stays case-sensitive: `"reports to priya sharma"` -> nothing, `"Reports to: Priya Sharma"` -> match. This matters because a case-insensitive name pattern would capture arbitrary lowercase prose.
- `_TRAIL_PUNCT = [.,;:]+$`.
- `_STOP`: words removed from the captured name (`the, this, you, after, team, intern, role, hiring, recruiter, contact, hr, manager, reports`). Compared casefolded.
- `_TITLE_WORDS`: job-title words (`senior, junior, staff, principal, engineer(s), manager(s), lead(s), director, head, team(s), vp, president, developer, officer, analyst, associate, architect, scientist, specialist, coordinator, executive, founder, cto, ceo, intern(s)`). A candidate containing any of them is rejected.
- `_DEGREE_FALSE`: `\b(bachelor|bachelors|master|masters|phd|mba|degree|university|college|science|department|faculty|school|engineering|arts|honours|honors|computer)\b`, case-insensitive. Catches sentences like "reports to the Computer Science department".

### `_clean_name`

**Location** `src/jobdigest/contacts/jd.py:53` · `def _clean_name(name: str) -> str`. `collapse_ws`, strip trailing punctuation, then drop any whitespace-separated word whose casefold is in `_STOP`, and rejoin. Example: `"Arun Mehta Hiring"` -> `"Arun Mehta"` (verified, from input `Recruiter: Arun Mehta Hiring`).

### `_valid_name`

**Location** `src/jobdigest/contacts/jd.py:59` · `def _valid_name(name: str) -> bool`. Cleans again, then rejects: empty, more than 3 words, any `_DEGREE_FALSE` match, or any word in `_TITLE_WORDS`. So the `_STOP` list handles words that are noise around a real name, while `_TITLE_WORDS` rejects the whole candidate.

### `parse_jd_names`

**Location** `src/jobdigest/contacts/jd.py:70` · `def parse_jd_names(description_text: str | None) -> list[DiscoveredContact]`

1. `text = description_text or ""`; `found`, `seen`.
2. For each `(pattern, role, confidence)` in `_LABELS` order (hiring-manager patterns first), for each `finditer` match: `name = _clean_name(match.group(1))`; skip if `not _valid_name(name)`; dedupe by casefold across all labels (a person labelled both ways keeps the first, higher-confidence label); append `DiscoveredContact(name, source="jd", role, confidence)`.
3. Return the list (possibly empty).

Called by `run_pipeline`. Real runs of the fixtures:

```
jd_hiring_manager.txt        -> [Priya Sharma (Hiring Manager, 100.0)]
jd_recruiter_and_degree.txt  -> [Arun Mehta (Recruiter, 90.0), Kavita Rao (Recruiter, 90.0)]
```
The second fixture also contains "This internship reports to the Computer Science department", which is rejected: the capture "Computer Science" is blocked by `_DEGREE_FALSE`. Edge-case probes (verified):

| Input | Result |
|---|---|
| `Reports to: Senior Engineer` | none (title word) |
| `Reports to: The team` | none (only `The` is captured, since `team` is lowercase; `_STOP` strips it, leaving nothing) |
| `Reports to: Priya Sharma, VP` | `Priya Sharma` (the regex stops at the comma) |
| `Reports to John` | `John` (a single first name is accepted) |
| `Recruiter Arun` | `Arun` (the `[:\s]+` separator allows a bare space) |
| `Hiring manager: Anne-Marie O'Neil` | `Anne-Marie` only |

The last row is a limitation: `O'Neil` does not satisfy `_PART` (it needs at least one lowercase letter after the first capital, but `O` is followed by `'`), so the capture ends before it. Single-word results mean a contact with `first` only; `construct_email` can then build `{first}.{last}` addresses with an empty last name (see [rank-and-email](rank-and-email.md#construct_email)).

Tests: `test_parse_jd_hiring_manager`, `test_parse_jd_recruiter_and_rejects_degree_false_positive`, `test_parse_jd_empty`, `test_parse_jd_rejects_job_title_lookalikes`, `test_parse_jd_keeps_real_name_next_to_title_words`.

## How confidence maps

JD roles carry the confidence directly (100/90), though `merge_contacts` recomputes it with `_base_confidence`; GitHub uses 70. See [rank-and-email](rank-and-email.md#_base_confidence).

## How to modify safely

- New label: add a tuple to `_LABELS`; keep the `(?-i:...)` wrapper so the name stays case-sensitive; add a title/degree false positive to the sets rather than loosening `_PART`.
- Add false-positive words to `_TITLE_WORDS` / `_DEGREE_FALSE`; each addition must keep real names valid (`Priya Sharma` is the canary).
- Do not log or persist full JD text here.
