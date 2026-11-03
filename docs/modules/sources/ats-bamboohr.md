# BambooHR adapter

- Source: `src/jobdigest/ats/bamboohr.py` (153 lines). Layer: source acquisition ([ATS overview](ats-overview.md)).
- Depends on: `jobdigest.ats.base` (`classify_http_status`, `empty_or_ok`, `join_locations`, `posted_at_from_item`), `jobdigest.ats.http_client`. Used by: `get_adapter`.
- Tests: `tests/test_ats_bamboohr.py` (fixtures `tests/fixtures/ats/bamboohr/{two_jobs,empty,not_found}.json`), `test_ats_registry.py::test_get_adapter_bamboohr`, `test_probe.py::test_probe_bamboohr_is_not_unimplemented`.

## Endpoint and pagination

`GET https://{token}.bamboohr.com/careers/list`. Note that the **token is a subdomain**, not a path segment: each BambooHR customer has `https://<token>.bamboohr.com`. The response is `{"meta": {"totalCount": N}, "result": [...]}`. One request returns the list; there is no pagination and the adapter does not call per-job detail pages (`test_single_get_no_pagination_params` and the `"/detail" not in url` assertion). `complete` is passed explicitly as `True`; `meta.totalCount` is not read or checked.

## Public interface

| Symbol | Signature | Purpose |
|---|---|---|
| `BambooHRAdapter` | `(client, *, sleep)` | `ats = "bamboohr"` |
| `fetch` | `(company) -> FetchResult` | single request |
| `_map` | `(company, item) -> NormalizedJob \| None` | mapping; `None` skips item |
| helpers | `_list_url`, `_as_dict`, `_str_field`, `_item_id`, `_title`, `_employment_hint`, `_location`, `_description`, `_apply_url` | below |

## Helpers

| Helper | Location | Behavior |
|---|---|---|
| `_list_url(token)` | `bamboohr.py:21` | `f"https://{token}.bamboohr.com/careers/list"` |
| `_as_dict(value)` | `:25` | dict or `{}` |
| `_str_field(blob, key)` | `:29` | stripped non-empty string value or `None` |
| `_item_id(item)` | `:36` | `str(item.get("id") or "").strip()` |
| `_title(item)` | `:40` | `jobOpeningName`, else `title`, stripped |
| `_employment_hint(item)` | `:44` | see [`_employment_hint`](#_employment_hint) |
| `_location(item)` | `:56` | below |
| `_description(item)` | `:74` | see [`_description`](#_description) |
| `_apply_url(company, item)` | `:87` | below |

### `_location`
`bamboohr.py:56`. Reads `location` (`city`, `state`) and `atsLocation` (`city`, `state`, `province`, `country`) and `isRemote`. `location_raw = join_locations([loc.city, loc.state, ats.city, ats.state, ats.province, ats.country, "Remote" if remote else None])`; workplace hint `"remote"` when `item["isRemote"] is True`. Because `join_locations` de-duplicates, the fixture's `city: Bengaluru` and `atsLocation.city: Bengaluru` collapse. Example (fixture job 101): `Bengaluru | Karnataka | India`; job 102 (`location` all null, `atsLocation.city Austin`, `country USA`, `isRemote true`): `Austin | USA | Remote`.

### `_apply_url` (synthesized URLs)
`bamboohr.py:87`:
```python
jid = _item_id(item)
token = (company.token or "").strip()
if token and jid:
    return f"https://{token}.bamboohr.com/careers/{jid}"
return ""
```
The list payload contains **no URL fields**, so the adapter builds the posting page URL from the subdomain token and the job id. Both `apply_url` and `hosted_url` are set to this synthesized value. If the BambooHR careers URL layout changes, all BambooHR URLs would silently be wrong; the tests assert exactly `https://example.bamboohr.com/careers/101`.

## Detailed walkthrough

### `BambooHRAdapter.fetch`
**Location** `bamboohr.py:107` · **Signature** `def fetch(self, company: CompanyConfig) -> FetchResult`
**Step-by-step**
1. `url = _list_url(company.token)`.
2. `request_get` with `except httpx.HTTPError` -> `FetchResult(ERROR, None, [], error=str(exc))`.
3. `classify_http_status`; terminal -> `FetchResult(terminal, status, [], error=None)`. A nonexistent subdomain typically means a DNS/connection failure or a redirect; the former is a transport error (`ERROR`), the latter `DEAD_TOKEN` (behavior of BambooHR itself not verified here).
4. `parse_json_or_none` (rejects HTML: BambooHR's login or "not found" pages return HTML with 200; `test_200_html_login_is_error`), require dict with list at `result`, else `ERROR`, `error="invalid bamboohr json"`.
5. Loop over `payload["result"]`, skip non-dicts, call `_map`; append non-`None`.
6. `FetchResult(empty_or_ok(len(jobs)), status, jobs, complete=True)`.

### `BambooHRAdapter._map`
**Location** `bamboohr.py:133` · **Signature** `def _map(self, company: CompanyConfig, item: dict) -> NormalizedJob | None`
1. Compute `apply` and `external_id`; if neither exists return `None` (item skipped).
2. Build the job:

| Field | Source | Rule |
|---|---|---|
| `title` | `jobOpeningName` / `title` | stripped |
| `company` | n/a | `company.name` |
| `location_raw`, `workplace_hint` | `_location` | |
| `apply_url`, `hosted_url` | `_apply_url` | synthesized; identical |
| `employment_hint` | `employmentStatusLabel` / `employmentType` | |
| `description_text` | `_description` | usually `""` |
| `external_id` | `id` | |
| `posted_at` | `datePosted`, then `postedDate` | `posted_at_from_item` |

**Real example** (fixture `two_jobs.json`, job 101):
```json
{"id": "101", "jobOpeningName": "  Software Engineer Intern  ", "departmentLabel": "Software Engineering",
 "employmentStatusLabel": "Internship", "employmentType": null,
 "location": {"city": "Bengaluru", "state": "Karnataka"},
 "atsLocation": {"country": "India", "state": "Karnataka", "province": null, "city": "Bengaluru"},
 "isRemote": false, "datePosted": "2024-06-01T00:00:00Z"}
```
Offline output with token `example`:
```json
{"title": "Software Engineer Intern", "company": "Example Co", "location_raw": "Bengaluru | Karnataka | India",
 "apply_url": "https://example.bamboohr.com/careers/101", "hosted_url": "https://example.bamboohr.com/careers/101",
 "employment_hint": "Internship", "workplace_hint": null, "description_text": "",
 "source": "exco", "ats": "bamboohr", "external_id": "101", "posted_at": "2024-06-01T00:00:00Z"}
```

### `BambooHRAdapter.__init__`
**Location** `src/jobdigest/ats/bamboohr.py:98` · **Signature** `def __init__(self, client: httpx.Client, *, sleep: Callable[[float], None]) -> None`
Stores the injected `httpx.Client` as `self._client` and the injected `sleep` callable as `self._sleep`. Nothing else happens at construction: no network call and
no validation. Both are injected by `ats.get_adapter(ats, client, *, sleep)` (called from `collect._default_fetch` and `probe.main`), which is what lets tests pass a
`MockTransport` client and a no-op sleep.

### `_employment_hint`

**Location** `src/jobdigest/ats/bamboohr.py:44` · **Signature** `def _employment_hint(item: dict) -> str | None`
**Purpose** Extracts a human-readable employment type ("Internship", "Full-Time") from one list item. BambooHR payloads vary in which key carries it and whether it is a string or an object, so the function tolerates both.
**Called by** `BambooHRAdapter._map` (`bamboohr.py:145`, becomes `NormalizedJob.employment_hint`). Downstream the hint is stored in `jobs.employment_hint`, fed to the hard filter (`hard_filter.py:216` appends it to the allow-text) and shown to the LLM as `COMMITMENT` (`score.py:95`, `blurb.py:75`). **Calls** nothing outside builtins.
**Returns** a stripped non-empty string, or `None`.
**Step by step**
1. Iterate the keys in priority order: `("employmentStatusLabel", "employmentType")`.
2. If the value is a string with non-blank content, return it stripped.
3. If it is a dict, take `str(raw.get("label") or raw.get("name") or raw.get("id") or "").strip()` and return it when non-empty.
4. Any other type (number, list, `None`) or a blank value falls through to the next key; after both keys, return `None`.
```python
if isinstance(raw, dict):
    label = str(raw.get("label") or raw.get("name") or raw.get("id") or "").strip()
    if label:
        return label
```
Verified offline: `{"employmentStatusLabel":"Internship","employmentType":"FT"}` -> `'Internship'` (first key wins); `{"employmentStatusLabel":"  ","employmentType":"Full-Time"}` -> `'Full-Time'`; `{"employmentStatusLabel":{"name":"Contract"}}` -> `'Contract'`; `{"employmentType":{"label":"","id":7}}` -> `'7'` (a numeric id is stringified); `{"employmentStatusLabel":5}` and `{}` -> `None`.

### `_description`

**Location** `src/jobdigest/ats/bamboohr.py:74` · **Signature** `def _description(item: dict) -> str`
**Purpose** Best-effort job description from the list item. The recorded fixture has none (see Quirks), so this is a defensive probe of four candidate keys rather than a known payload field.
**Called by** `BambooHRAdapter._map` (`bamboohr.py:147`, becomes `description_text`). **Calls** nothing outside builtins.
**Returns** the first non-blank string among `description`, `descriptionText`, `jobDescription`, `jobOpeningShareText` (checked in that order), stripped; `""` when none qualifies.
**Step by step** loop over the four keys; `raw = item.get(key)`; `if isinstance(raw, str) and raw.strip(): return raw.strip()`; non-string values (lists, dicts, `None`) are skipped without error; after the loop `return ""`.
No HTML decoding or tag stripping happens here (unlike adapters that call `decode_html_content`). Verified offline: `{"description":"  hi "}` -> `'hi'`; `{"description":"","descriptionText":" t "}` -> `'t'`; `{"description":["x"],"jobOpeningShareText":"share"}` -> `'share'`; `{"description":"<p>A&amp;B</p>"}` -> `'<p>A&amp;B</p>'` (raw); `{}` -> `''`.

## Quirks

- The listing carries no description in the fixture (`description_text == ""` is asserted in `test_two_jobs_ok_trims_title_and_https_apply`). `_description` probes four candidate keys defensively, but this is not verified against live payloads; whether real BambooHR list responses ever contain one of them is unknown from the repo. If one does, note it is returned stripped but raw (not passed through `decode_html_content`).
- The `departmentLabel` / `departmentId` fields are present in the payload but unused; they remain available in `raw_json`.
- `meta.totalCount` is ignored, so a truncated list would not be detected.

## Failure modes and edge cases

404 -> `DEAD_TOKEN` (`test_404_is_dead_token_not_empty_board`); `{"result": []}` -> `EMPTY_BOARD` (`test_empty_board`); invalid JSON or HTML -> `ERROR`. An item with neither id nor token is dropped silently.

## How to modify safely

If a detail endpoint is added to fetch descriptions, remember `test_single_get_no_pagination_params` explicitly forbids it today; also keep `complete=False` for any path where detail fetches fail partway only if the list itself is partial (a missing description is not an incomplete board).
