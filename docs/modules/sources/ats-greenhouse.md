# Greenhouse adapter

- Source: `src/jobdigest/ats/greenhouse.py` (151 lines). Layer: source acquisition, one of six [ATS adapters](ats-overview.md).
- Depends on: `jobdigest.ats.base` (`get_json_page`, `JobCollector`, `decode_html_content`, `join_locations`, `posted_at_from_item`, `empty_or_ok`), `jobdigest.logutil.get_logger`. Used by: `get_adapter` (`ats/__init__.py`), hence `collect.py` and `probe.py`.
- Tests: `tests/test_ats_greenhouse.py` (fixtures `tests/fixtures/ats/greenhouse/{two_jobs,empty,not_found,encoded_html,offices_parent_vs_leaf,page2_new}.json`), `test_probe.py`.

## Endpoint and pagination

`GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` (page 1) and `...&page=N` for N >= 2 (`_jobs_url`). `content=true` makes the API include each job's HTML description, so no per-job follow-up request is needed.

The response is `{"jobs": [...], "meta": {"total": N}}`. Pagination is driven by `meta.total`: if the first page holds fewer unique jobs than `total`, request page 2, 3, ... until the collected count reaches `total`, a page fails or adds nothing new, or `max_pages` (default 20) is reached. If `meta.total` is absent or not an int, no further pages are requested and the first page is taken as the whole board.

## Public interface

| Symbol | Signature | Purpose |
|---|---|---|
| `GreenhouseAdapter` | `GreenhouseAdapter(client, *, sleep, max_pages=20)` | adapter; `ats = "greenhouse"` |
| `GreenhouseAdapter.fetch` | `(company: CompanyConfig) -> FetchResult` | paginated board fetch |
| `GreenhouseAdapter._map` | `(company, item) -> NormalizedJob` | raw item to `NormalizedJob` |
| `_jobs_url`, `_meta_total`, `_item_id`, `_dict_jobs`, `_loc_bit` | private helpers | see below |

## Helpers

- `_jobs_url(token: str, page: int | None = None) -> str` (`greenhouse.py:36`): builds the URL; `page=None` omits the `&page=` parameter, so page 1 is requested without it.
- `_meta_total(payload: dict) -> int | None` (`:46`): `payload["meta"]["total"]` if `meta` is a dict and `total` is an `int` and not a `bool`; otherwise `None`.
- `_item_id(item: dict) -> str` (`:56`): `str(item.get("id") or "").strip()`; the key function for `JobCollector`. An id of `0`/missing becomes `""` and the item is kept in `extras`.
- `_dict_jobs(payload: dict) -> list[dict]` (`:60`): the `jobs` list filtered to dict elements; non-list -> `[]`.
- `_loc_bit(value: object) -> str | None` (`:27`): a stripped string, or `None` if not a string, empty, or a placeholder (`"n/a"`, `"na"`, case-insensitive; `_PLACEHOLDERS`, `:22`). Greenhouse boards use `"N/A"` as a literal location name.

## Detailed walkthrough

### `GreenhouseAdapter.__init__`
`greenhouse.py:70`. Stores `client`, `sleep`, and `max_pages = max(1, int(max_pages))` so a bad value cannot disable fetching.

### `GreenhouseAdapter.fetch`
**Location** `src/jobdigest/ats/greenhouse.py:81` · **Signature** `def fetch(self, company: CompanyConfig) -> FetchResult`
**Purpose** Fetch all pages of one board, tell the caller whether the result is the whole board, and map to `NormalizedJob`.
**Called by** `collect._default_fetch`, `probe.main` · **Calls** `get_json_page`, `JobCollector.absorb/new_ids/items`, `_meta_total`, `_dict_jobs`, `self._map`, `empty_or_ok`, `log.warning`.

**Step-by-step**
1. Define the local predicate `valid(payload)`: payload is a `dict` with a list under `jobs`. And `get(url)` which wraps `get_json_page(..., label="greenhouse")`.
2. `first = get(_jobs_url(company.token))`. `if first.failure is not None: return first.failure`. A failure on page 1 (404 -> `DEAD_TOKEN`, 403/5xx/HTML/bad JSON -> `ERROR`) is returned unchanged.
3. `collector = JobCollector(_item_id)`; `collector.absorb(_dict_jobs(first.payload))`; `total = _meta_total(first.payload)`; `last_status = first.http_status`; `complete = True`; `pages_fetched = 1`; `page = 2`.
4. Pagination loop:
```python
while (
    total is not None
    and total > len(collector)
    and pages_fetched < self._max_pages
):
    nxt = get(_jobs_url(company.token, page=page))
    pages_fetched += 1
    if nxt.http_status is not None:
        last_status = nxt.http_status
    items = _dict_jobs(nxt.payload) if nxt.failure is None else []
    if nxt.failure is not None or not collector.new_ids(items):
        complete = False
        break
    collector.absorb(items)
    page += 1
```
   The loop runs only while the board advertises more jobs than collected. It stops with `complete = False` if the page failed (any `Page.failure`, including 404 mid-way) or if the page contributed no id not already collected (some boards return the same page for any `page=` value; the `new_ids` check prevents an endless/duplicating loop). `last_status` follows the most recent response, including failed ones.
5. After the loop: `got = len(collector)`. `if total is not None and total > got:` set `complete = False` and log `greenhouse incomplete token=%s total=%s got=%s` at WARNING. This also covers hitting `max_pages`, where the loop exits without `break`.
6. `jobs = [self._map(company, item) for item in collector.items()]` (id order of first appearance, then id-less items).
7. `return FetchResult(empty_or_ok(len(jobs)), last_status, jobs, complete=complete)`.

**Why `complete` is computed this way.** Greenhouse's `total` is the ground truth for "how many jobs exist". Any shortfall means jobs are missing from the result, so the tombstone sweep must not run (see [overview](ats-overview.md#why-complete-matters-tombstone-sweep)). Tests: `test_meta_total_mismatch_duplicate_page_is_incomplete` (total 5, page 2 repeats page 1 -> `complete is False`, exactly 2 requests, log text asserted), `test_meta_total_mismatch_second_page_appends` (total 3, page 2 adds job 103 -> `complete is True`, 2 requests), `test_page2_http_error_keeps_page1_incomplete` (page 2 gets 500; jobs from page 1 retained, `status OK`), `test_max_pages_marks_incomplete` (`max_pages=3`, `total=50`).

Note `request_get` already retries 5xx twice, so the "page 2 returns 500" case reaches the loop only after those retries.

**Plain-English summary.** Fetch page 1; keep asking for more pages while the vendor says there are more jobs than we have; stop and mark the result partial if a page errors or repeats; then convert everything.

### `GreenhouseAdapter._map`
**Location** `greenhouse.py:127` · **Signature** `def _map(self, company: CompanyConfig, item: dict) -> NormalizedJob`
Field mapping:

| `NormalizedJob` field | Source in raw item | Rule |
|---|---|---|
| `title` | `title` | `str(...).strip()` |
| `company` | `company_name` | stripped; falls back to `company.name` when empty/absent |
| `location_raw` | `location.name` + each `offices[].location` | `join_locations([_loc_bit(loc.get("name")), *office_bits])`; placeholders (`N/A`) dropped; duplicates dropped |
| `apply_url` / `hosted_url` | `absolute_url` | both identical (Greenhouse exposes only one link) |
| `employment_hint`, `workplace_hint` | n/a | always `None` |
| `description_text` | `content` | `decode_html_content` |
| `source` | `company.id` | |
| `external_id` | `id` | `str(id or "")` |
| `raw_json` | whole item | compact JSON |
| `posted_at` | `updated_at` | `posted_at_from_item(item, "updated_at")`. Note: this is the last-update time, not the first-publish time |

Offices: only each office's `location` string (the leaf address) is used, never the office `name`, because a parent office such as `"India Locations"` would pollute `location_raw`. Fixture `offices_parent_vs_leaf.json` and `test_offices_parent_name_not_in_location` cover this.

**Real example** (fixture `two_jobs.json`, first item) mapped offline with `CompanyConfig("exco", "Example Co", "greenhouse", "example")`:

Raw:
```json
{"id": 101, "title": "Software Engineer Intern",
 "absolute_url": "https://boards.greenhouse.io/example/jobs/101",
 "location": {"name": "Bengaluru, India"},
 "content": "&lt;p&gt;Build APIs in Bangalore&lt;/p&gt;",
 "company_name": "Example Corp", "updated_at": "2024-06-01T00:00:00.000Z"}
```
Normalized (raw_json and fetched_at omitted):
```json
{"title": "Software Engineer Intern", "company": "Example Corp", "location_raw": "Bengaluru, India",
 "apply_url": "https://boards.greenhouse.io/example/jobs/101", "hosted_url": "https://boards.greenhouse.io/example/jobs/101",
 "employment_hint": null, "workplace_hint": null, "description_text": "Build APIs in Bangalore",
 "source": "exco", "ats": "greenhouse", "external_id": "101", "posted_at": "2024-06-01T00:00:00Z"}
```
The second fixture item has no `company_name` or `updated_at`, giving `company: "Example Co"` (the config name) and `posted_at: null`.

Parent-vs-leaf example (job 581): `location.name = "Bengaluru"` plus office `location = "Bengaluru, Karnataka, India"` gives `location_raw = "Bengaluru | Bengaluru, Karnataka, India"` (distinct strings are both kept). Job 579 (`location.name = "N/A"`, office `location = null`) gives `location_raw = ""`.

Double-encoded content: fixture `encoded_html.json` `"&amp;lt;p&amp;gt;Bengaluru intern&amp;lt;/p&amp;gt;"` becomes `"Bengaluru intern"` (`test_double_encoded_html_description`).

## Failure modes and edge cases

- Page 1: 404/410/3xx `DEAD_TOKEN`; 403 `ERROR` (`test_403_is_error_not_dead_token`); 429 retried once inside `request_get`, then `ERROR` (`test_429_then_200_empty_calls_sleep`, `test_429_after_retry_is_error`); HTML 200 `ERROR` (`test_200_html_challenge_is_error`); `{"jobs": []}` -> `EMPTY_BOARD` (`test_empty_board`).
- Missing/invalid `meta.total`: single page assumed, `complete` stays `True` even if the board is actually larger. This is a trust-the-vendor assumption.
- Items without an `id` cannot be de-duplicated and do not count toward `len(collector)`.
- `location_raw` may be `""`; `apply_url` may be `""` if `absolute_url` is missing (there is no skip rule here, unlike SmartRecruiters/BambooHR).

## How to modify safely

If you change pagination, keep `complete = False` on every path where you stop before `len(collector) >= total`; otherwise a transient page failure will tombstone real jobs after two runs. Update the matching tests above.
