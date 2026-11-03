# Workable adapter

- Source: `src/jobdigest/ats/workable.py` (110 lines). Layer: source acquisition ([ATS overview](ats-overview.md)).
- Depends on: `jobdigest.ats.base` (`classify_http_status`, `decode_html_content`, `empty_or_ok`, `join_locations`, `posted_at_from_item`), `jobdigest.ats.http_client`. Used by: `get_adapter` (it is the fall-through return there).
- Tests: `tests/test_ats_workable.py` (fixtures `tests/fixtures/ats/workable/{two_jobs,empty,not_found}.json`).

## Endpoint and pagination

`GET https://apply.workable.com/api/v1/widget/accounts/{token}?details=true`. This is Workable's public "widget" API: it returns `{"name": ..., "description": ..., "jobs": [...]}` with all active jobs in one response (`details=true` adds the description). The source comment says the endpoint takes no page, offset or cursor parameters; `test_no_pagination_params_sent` asserts a single request. `complete` is always `True`.

## Public interface

| Symbol | Signature | Purpose |
|---|---|---|
| `WorkableAdapter` | `WorkableAdapter(client, *, sleep)` | `ats = "workable"` |
| `WorkableAdapter.fetch` | `(company) -> FetchResult` | single request |
| `WorkableAdapter._map` | `(company, item, envelope_name) -> NormalizedJob` | mapping |
| `_location_entry` | `(entry: object) -> str \| None` | location element to text |

## Detailed walkthrough

### `_location_entry`
**Location** `workable.py:22` · `def _location_entry(entry: object) -> str | None`. For the optional `locations` array: a string is returned unchanged; a dict is flattened with `join_locations([city, state, region, country, location, name])` (each included only if it is a string) and `None` if that is empty; other types give `None`. The fixtures do not include a `locations` array, so this function is not exercised by the fixture tests.

### `WorkableAdapter.fetch`
**Location** `workable.py:47` · **Signature** `def fetch(self, company: CompanyConfig) -> FetchResult`
**Step-by-step**
1. URL built from `company.token` with `?details=true`.
2. `request_get` with `except httpx.HTTPError` -> `FetchResult(ERROR, None, [], error=str(exc))`.
3. `classify_http_status`; non-`None` -> `FetchResult(terminal, status, [], error=None)`.
4. `parse_json_or_none`; require a dict with a list at `jobs`, else `ERROR` with `error="invalid workable json"`.
5. `envelope_name = str(payload.get("name") or "").strip()`: the company's display name from the envelope.
6. `jobs = [self._map(company, item, envelope_name) for item in payload["jobs"] if isinstance(item, dict)]`. Unlike Ashby, no jobs are dropped.
7. `FetchResult(empty_or_ok(len(jobs)), response.status_code, jobs)`.

### `WorkableAdapter._map`
**Location** `workable.py:73` · **Signature** `def _map(self, company: CompanyConfig, item: dict, envelope_name: str) -> NormalizedJob`

| Field | Raw source | Rule |
|---|---|---|
| `title` | `title` | stripped |
| `company` | envelope `name` | `envelope_name or company.name` (`test_company_name_taken_from_envelope`) |
| `location_raw` | `city`, `state`, `country`, then each `locations[]` entry | `join_locations([...])`; top-level string fields only if they are strings |
| `external_id` | `shortcode` | string; this is Workable's job id |
| `apply_url` | `application_url` | stripped; if empty and `shortcode` is present, synthesized as `https://apply.workable.com/j/{shortcode}/apply` |
| `hosted_url` | `url`, else `shortlink` | `""` if both missing |
| `employment_hint` | `employment_type` | stripped string or `None` |
| `workplace_hint` | `workplace_type` | if the key exists (not `None`): stripped string or `None`; elif `telecommuting is True`: `"remote"`; else `None` |
| `description_text` | `description` | `decode_html_content` |
| `posted_at` | `created_at` | `posted_at_from_item` |

The `workplace_hint` fallback (`test_telecommuting_true_falls_back_to_remote_hint`) exists so a payload carrying only the boolean `telecommuting` (no `workplace_type`) still yields a remote hint (the reason is inferred from the code and its test).

**Real example** (fixture `two_jobs.json`, first job; envelope `name` is `"Example Corp"`):
```json
{"shortcode": "INT001", "title": "Software Intern", "city": "Bengaluru", "state": "Karnataka", "country": "India",
 "url": "https://apply.workable.com/example/j/INT001/", "application_url": "https://apply.workable.com/j/INT001/apply",
 "employment_type": "Internship", "workplace_type": "on_site", "created_at": "2024-06-01T00:00:00Z",
 "description": "<p>Build in Bengaluru</p>"}
```
Offline output:
```json
{"title": "Software Intern", "company": "Example Corp", "location_raw": "Bengaluru | Karnataka | India",
 "apply_url": "https://apply.workable.com/j/INT001/apply", "hosted_url": "https://apply.workable.com/example/j/INT001/",
 "employment_hint": "Internship", "workplace_hint": "on_site", "description_text": "Build in Bengaluru",
 "source": "exco", "ats": "workable", "external_id": "INT001", "posted_at": "2024-06-01T00:00:00Z"}
```
Note that `location_raw` here is `"Bengaluru | Karnataka | India"` (three parts joined by `" | "`) whereas Greenhouse puts a whole string such as `"Bengaluru, India"` in one part.

### `WorkableAdapter.__init__`
**Location** `src/jobdigest/ats/workable.py:43` · **Signature** `def __init__(self, client: httpx.Client, *, sleep: Callable[[float], None]) -> None`
Stores the injected `httpx.Client` as `self._client` and the injected `sleep` callable as `self._sleep`. Nothing else happens at construction: no network call and
no validation. Both are injected by `ats.get_adapter(ats, client, *, sleep)` (called from `collect._default_fetch` and `probe.main`), which is what lets tests pass a
`MockTransport` client and a no-op sleep.

## Quirks

- `external_id` is the `shortcode`, not a numeric id; if `shortcode` were missing, `external_id` is `""` and the job is still emitted (no skip rule, unlike SmartRecruiters/BambooHR), which can leave no stable identity.
- The first fixture's `url` includes the account slug (`/example/j/INT001/`) while the synthesized apply URL does not (`/j/INT001/apply`); both forms appear in the fixtures.

## Failure modes and edge cases

404 -> `DEAD_TOKEN`; `{"jobs": []}` -> `EMPTY_BOARD`; a non-dict or missing `jobs` -> `ERROR`. A Workable `name` containing only whitespace falls back to `company.name`.

## How to modify safely

Preserve the "single request, no pagination params" contract unless Workable's API changes; update `test_no_pagination_params_sent` together with any change.
