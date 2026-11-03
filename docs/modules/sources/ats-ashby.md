# Ashby adapter

- Source: `src/jobdigest/ats/ashby.py` (95 lines). Layer: source acquisition ([ATS overview](ats-overview.md)).
- Depends on: `jobdigest.ats.base` (`classify_http_status`, `decode_html_content`, `empty_or_ok`, `join_locations`, `posted_at_from_item`), `jobdigest.ats.http_client` (`request_get`, `parse_json_or_none`). Used by: `get_adapter`.
- Tests: `tests/test_ats_ashby.py` (fixtures `tests/fixtures/ats/ashby/{two_jobs,empty,not_found}.json`).

## Endpoint and pagination

`GET https://api.ashbyhq.com/posting-api/job-board/{token}`. One response contains every listed job as `{"jobs": [...]}`. The source comment at the top of `fetch` states the endpoint accepts no offset, cursor or page parameters, so there is no loop. `test_no_pagination_params_sent` asserts exactly one request with no query parameters. Because the whole board arrives at once, the result is always `complete=True` (dataclass default).

## Public interface

| Symbol | Signature | Purpose |
|---|---|---|
| `AshbyAdapter` | `AshbyAdapter(client, *, sleep)` | `ats = "ashby"` |
| `AshbyAdapter.fetch` | `(company) -> FetchResult` | single-request fetch |
| `AshbyAdapter._map` | `(company, item) -> NormalizedJob` | field mapping |
| `_location_text` | `(value: object) -> str \| None` | location string extractor |

## Detailed walkthrough

### `_location_text`
**Location** `ashby.py:22` · `def _location_text(value: object) -> str | None`. A string is returned as-is; a dict yields `value.get("location") or value.get("name")` if that is a string; anything else is `None`. It exists because Ashby locations appear both as plain strings (primary location) and as objects (secondary locations).

### `AshbyAdapter.fetch`
**Location** `ashby.py:38` · **Signature** `def fetch(self, company: CompanyConfig) -> FetchResult`
**Called by** `collect._default_fetch`, `probe.main` · **Calls** `request_get`, `classify_http_status`, `parse_json_or_none`, `self._map`, `empty_or_ok`.
**Step-by-step**
1. Build `url = f"https://api.ashbyhq.com/posting-api/job-board/{company.token}"`.
2. `request_get` in `try`; `except httpx.HTTPError as exc: return FetchResult(FetchStatus.ERROR, None, [], error=str(exc))`.
3. `terminal = classify_http_status(response.status_code)`; if not `None` return `FetchResult(terminal, response.status_code, [], error=None)`.
4. `payload = parse_json_or_none(response)`; require `dict` with a list in `payload["jobs"]`, else `FetchResult(ERROR, status, [], error="invalid ashby json")` (so an empty-but-wrong-shaped body is never `EMPTY_BOARD`; `test_invalid_json_structure_is_error`).
5. Build `jobs` from items that are dicts and satisfy `item.get("isListed") is not False`. Unlisted jobs (hidden by the company but still returned by the API) are dropped; a job with **no** `isListed` key is kept. `test_unlisted_job_is_excluded` checks this with fixture job `ash-2`.
6. `return FetchResult(empty_or_ok(len(jobs)), response.status_code, jobs)`.

Consequence of dropping unlisted jobs: if a previously listed job becomes unlisted it disappears from the fetch, so after two runs the sweep tombstones it, which is the intended behavior.

(Steps 2-4 are what `get_json_page` does for the paginated adapters; Ashby inlines them because it needs no loop.)

### `AshbyAdapter._map`
**Location** `ashby.py:60` · **Signature** `def _map(self, company: CompanyConfig, item: dict) -> NormalizedJob`

| Field | Raw source | Rule |
|---|---|---|
| `title` | `title` | stripped (fixture has a leading space) |
| `company` | n/a | `company.name` |
| `location_raw` | `location` + `secondaryLocations[]` | `join_locations([loc, *extra])`; for each secondary entry: a dict contributes `entry["location"]` if it is a string, else `_location_text(entry)` (checks `location` again, then `name`); a non-dict entry goes through `_location_text` |
| `hosted_url` | `jobUrl` | `""` if missing |
| `apply_url` | `applyUrl` | falls back to `jobUrl` |
| `employment_hint` | `employmentType` | stripped string or `None` (values seen in fixture: `Intern`, `FullTime`) |
| `workplace_hint` | `workplaceType` | stripped string or `None` (`OnSite`, `Remote`) |
| `description_text` | `descriptionPlain` if non-blank, else `decode_html_content(descriptionHtml)` | |
| `external_id` | `id` | string |
| `posted_at` | `publishedAt`, then `publishedDate` | first parseable via `posted_at_from_item` |

**Real example** (fixture `two_jobs.json`, `ash-1`):
```json
{"id": "ash-1", "title": " Software Engineer Intern", "location": "Bengaluru, India", "isListed": true,
 "employmentType": "Intern", "workplaceType": "OnSite",
 "jobUrl": "https://jobs.ashbyhq.com/example/ash-1",
 "applyUrl": "https://jobs.ashbyhq.com/example/ash-1/application",
 "descriptionPlain": "Intern in Bengaluru", "publishedAt": "2024-06-01T00:00:00Z", "secondaryLocations": []}
```
Offline output:
```json
{"title": "Software Engineer Intern", "company": "Example Co", "location_raw": "Bengaluru, India",
 "apply_url": "https://jobs.ashbyhq.com/example/ash-1/application", "hosted_url": "https://jobs.ashbyhq.com/example/ash-1",
 "employment_hint": "Intern", "workplace_hint": "OnSite", "description_text": "Intern in Bengaluru",
 "source": "exco", "ats": "ashby", "external_id": "ash-1", "posted_at": "2024-06-01T00:00:00Z"}
```
The fixture's second job has `isListed: false` and is excluded: the fetch result had 1 job.

### `AshbyAdapter.__init__`
**Location** `src/jobdigest/ats/ashby.py:34` · **Signature** `def __init__(self, client: httpx.Client, *, sleep: Callable[[float], None]) -> None`
Stores the injected `httpx.Client` as `self._client` and the injected `sleep` callable as `self._sleep`. Nothing else happens at construction: no network call and
no validation. Both are injected by `ats.get_adapter(ats, client, *, sleep)` (called from `collect._default_fetch` and `probe.main`), which is what lets tests pass a
`MockTransport` client and a no-op sleep.

## Failure modes and edge cases

- `workplace_hint` values are not normalized (`OnSite`, `Remote`); downstream code must match case-insensitively.
- Because the API returns every job at once, a very large board is one large response; there is no size guard.
- 404 -> `DEAD_TOKEN` (`test_404_is_dead_token`); `{"jobs": []}` -> `EMPTY_BOARD` (`test_empty_board`).

## How to modify safely

Keep the `isListed is not False` rule (strict `False` check) so boards that omit the field still work. If Ashby later adds pagination, switch to `get_json_page` + `JobCollector` and set `complete` accordingly.
