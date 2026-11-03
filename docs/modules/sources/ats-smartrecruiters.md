# SmartRecruiters adapter

- Source: `src/jobdigest/ats/smartrecruiters.py` (208 lines). Layer: source acquisition ([ATS overview](ats-overview.md)).
- Depends on: `jobdigest.ats.base` (`get_json_page`, `JobCollector`, `empty_or_ok`, `join_locations`, `posted_at_from_item`), `jobdigest.logutil`. Used by: `get_adapter`.
- Tests: `tests/test_ats_smartrecruiters.py` (fixtures `tests/fixtures/ats/smartrecruiters/{two_jobs,page0,page1,empty,not_found}.json`), `test_ats_registry.py`, `test_probe.py::test_probe_smartrecruiters_is_not_unimplemented`.

## Endpoint and pagination

`GET https://api.smartrecruiters.com/v1/companies/{token}/postings?limit={limit}&offset={offset}` (`_postings_url`). Response: `{"offset", "limit", "totalFound", "content": [...]}`. `limit` is clamped to `min(100, max(1, page_limit))` (default 100). Pagination is offset based and bounded by `max_pages` (default 20), so at most 2000 postings per board with defaults.

**No description.** The list endpoint returns postings **without** body text; the full text lives behind a per-posting detail URL (`ref`) that this adapter does not call. Every `NormalizedJob.description_text` is therefore `""` (`_map` hard-codes it). The rest of the pipeline works from title, location and employment hints for these jobs; `decode_html_content` has nothing to decode. This is the main behavioral difference from the other adapters.

## Public interface

| Symbol | Signature | Purpose |
|---|---|---|
| `SmartRecruitersAdapter` | `(client, *, sleep, page_limit=100, max_pages=20)` | `ats = "smartrecruiters"` |
| `fetch` | `(company) -> FetchResult` | offset pagination |
| `_map` | `(company, item) -> NormalizedJob \| None` | mapping; `None` skips the item |
| `_postings_url`, `_item_id`, `_total_found`, `_content`, `_http_url`, `_company_blob`, `_company_identifier`, `_company_name`, `_employment_hint`, `_location_and_workplace`, `_apply_url` | helpers | below |

## Helpers (summarized; each is a few lines)

| Helper | Location | Behavior |
|---|---|---|
| `_postings_url(token, *, limit, offset)` | `:25` | the URL above |
| `_item_id(item)` | `:32` | `str(item.get("id") or item.get("uuid") or "").strip()`; `uuid` is the fallback id |
| `_total_found(payload)` | `:36` | `payload["totalFound"]` if a non-bool int, else `None` |
| `_content(payload)` | `:43` | `payload["content"]` filtered to dicts; non-list -> `[]` |
| `_http_url(value)` | `:50` | the stripped string only if it starts with `http://` or `https://` (case-insensitive), else `None` |
| `_company_blob(item)` | `:58` | `item["company"]` if dict else `{}` |
| `_company_identifier(company, item)` | `:63` | `company.identifier` from the item, else `company.token` |
| `_company_name(company, item)` | `:70` | `company.name` from the item, else `CompanyConfig.name` |
| `_employment_hint(item)` | `:75` | see [`_employment_hint`](#_employment_hint) |
| `_location_and_workplace(item)` | `:88` | see below |
| `_apply_url(company, item)` | `:102` | see below |

### `_location_and_workplace`
`smartrecruiters.py:88`. Reads `item["location"]` (dict), `remote = loc.get("remote") is True`. `location_raw = join_locations([city, region, country, "Remote" if remote else None])` (strings only), and returns `(location_raw, "remote" if remote else None)`. So a remote job gets the literal text `Remote` appended to `location_raw` as well as `workplace_hint="remote"`. Example: `{"city":"Austin","country":"USA","remote":true}` -> `("Austin | USA | Remote", "remote")`.

### `_apply_url`
`smartrecruiters.py:102`. Ordered fallbacks:
1. `applyUrl` if it is an http(s) URL.
2. Synthesize `https://www.smartrecruiters.com/{identifier}/{id}` when `identifier` and `id` exist.
3. Same with `uuid` instead of `id`.
4. `ref` if it is http(s) **and** does not contain `api.smartrecruiters.com` (the `ref` normally points at the API, which is useless as an apply link).
5. `""`.

## Detailed walkthrough

### `SmartRecruitersAdapter.__init__`
`smartrecruiters.py:122`. `self._page_limit = min(100, max(1, int(page_limit)))`; `self._max_pages = max(1, int(max_pages))`.

### `SmartRecruitersAdapter.fetch`
**Location** `smartrecruiters.py:135` · **Signature** `def fetch(self, company: CompanyConfig) -> FetchResult`
**Called by** `collect._default_fetch`, `probe.main` · **Calls** `get_json_page`, `JobCollector`, `_total_found`, `_content`, `self._map`, `empty_or_ok`, `log.warning`.
**Step-by-step**
1. Local `valid(payload)`: dict with a list under `content`.
2. Init: `offset = 0`, `limit = self._page_limit`, `collector = JobCollector(_item_id)`, `last_status = None`, `complete = True`, `pages_fetched = 0`, `total = None`.
3. `while pages_fetched < self._max_pages:`
   1. `pg = get_json_page(...)`; update `last_status` if `pg.http_status` is set.
   2. `pg.failure`: if `pages_fetched == 0` return it (first-page failure keeps its original status); else `complete = False; break`.
   3. `if total is None: total = _total_found(pg.payload)` (taken from the first successful page only).
   4. `items = _content(pg.payload)`; `collector.absorb(items)`; `pages_fetched += 1`; `n = len(items)`.
   5. Stop conditions: `if n == 0 or n < limit: break` (short page = last); `if total is not None and offset + n >= total: break` (reached the advertised total).
   6. `offset += n`.
4. After the loop: `got = len(collector)`; `if total is not None and total > got:` `complete = False` and a WARNING `smartrecruiters incomplete token=%s total=%s got=%s`. This covers both `max_pages` exhaustion and a mid-way failure.
5. Map every item from `collector.items()`; items for which `_map` returns `None` are skipped.
6. `FetchResult(empty_or_ok(len(jobs)), last_status, jobs, complete=complete)`.

Note the check `total > got` compares against unique ids; a board with duplicate ids across pages or items lacking ids can end up flagged incomplete.
Tests: `test_pagination_concatenates_without_dup_ids` (`page_limit=2`, offsets 0 then 2, ids `sr-101..sr-103`, `complete True`, URLs contain `limit=2`, `offset=0`/`offset=2`), `test_max_pages_marks_incomplete` (`limit=1`, `max_pages=3`, `totalFound=50` -> 3 jobs, `complete False`), `test_empty_board`, `test_404_is_dead_token_not_empty_board`, `test_invalid_json_is_error`, `test_200_html_challenge_is_error`.

### `SmartRecruitersAdapter._map`
**Location** `smartrecruiters.py:188` · **Signature** `def _map(self, company: CompanyConfig, item: dict) -> NormalizedJob | None`
1. `apply = _apply_url(company, item)`; `external_id = _item_id(item)`.
2. `if not apply and not external_id: return None` (nothing identifies the job, so it is skipped and not counted in `empty_or_ok`).
3. Build the `NormalizedJob`:

| Field | Raw source | Rule |
|---|---|---|
| `title` | `name` | stripped (fixture has `"  Software Engineer Intern  "`) |
| `company` | `company.name` | falls back to `CompanyConfig.name` |
| `location_raw` | `location.city/region/country` + `Remote` | see above |
| `apply_url`, `hosted_url` | `_apply_url` | same value for both |
| `employment_hint` | `typeOfEmployment.label` | e.g. `Internship`, `Full-time` |
| `workplace_hint` | `location.remote` | `"remote"` or `None` |
| `description_text` | n/a | always `""` |
| `external_id` | `id` or `uuid` | |
| `posted_at` | `releasedDate`, then `createdOn` | `posted_at_from_item` |

**Real example** (fixture `two_jobs.json`, second job, which has no `applyUrl`):
```json
{"id": "sr-102", "uuid": "uuid-102", "name": "Staff Engineer",
 "location": {"city": "Austin", "country": "USA", "remote": true},
 "typeOfEmployment": {"id": "permanent", "label": "Full-time"},
 "company": {"identifier": "example", "name": "Example Corp"}}
```
Offline output:
```json
{"title": "Staff Engineer", "company": "Example Corp", "location_raw": "Austin | USA | Remote",
 "apply_url": "https://www.smartrecruiters.com/example/sr-102", "hosted_url": "https://www.smartrecruiters.com/example/sr-102",
 "employment_hint": "Full-time", "workplace_hint": "remote", "description_text": "",
 "source": "exco", "ats": "smartrecruiters", "external_id": "sr-102", "posted_at": null}
```
The first fixture job has an explicit `applyUrl` and `releasedDate` and keeps them (`posted_at = "2024-06-01T00:00:00Z"`).

### `_employment_hint`

**Location** `src/jobdigest/ats/smartrecruiters.py:75` · **Signature** `def _employment_hint(item: dict) -> str | None`
**Purpose** Reads SmartRecruiters' `typeOfEmployment` (normally an object like `{"id": "permanent", "label": "Full-time"}`) into the single string stored as `NormalizedJob.employment_hint`. Downstream that string goes to `jobs.employment_hint`, the hard filter's allow-text (`hard_filter.py:216`) and the LLM `COMMITMENT` line (`score.py:95`).
**Called by** `SmartRecruitersAdapter._map` (`smartrecruiters.py:200`). **Calls** nothing outside builtins.
**Step by step**
1. `raw = item.get("typeOfEmployment")`.
2. Dict: take `label` stripped; if non-empty return it. Otherwise take `id` stripped and return it, or `None` if that is empty too. (So `label` is preferred, `id` is the fallback.)
3. String: return it stripped, or `None` if blank.
4. Anything else (missing, number, list): `None`.
Verified offline: `{"label":"Full-time","id":"permanent"}` -> `'Full-time'`; `{"label":" ","id":"intern"}` -> `'intern'`; `{}` -> `None`; `" Contract "` -> `'Contract'`; `"  "`, `3` and a missing key -> `None`.

## Failure modes and edge cases

- A mid-pagination failure returns `OK` with partial jobs and `complete=False` (different from Lever only in that the stop condition is also driven by `totalFound`).
- `totalFound` absent: pagination stops on the first short page; `complete` stays `True` even if the board was larger than `max_pages * limit` unless a short page was reached.
- Empty descriptions mean any later stage that depends on description text (LLM scoring, blurbs, keyword matching in descriptions) has less to work with for SmartRecruiters jobs; the title and location still flow through.

## How to modify safely

To fetch descriptions, add a per-posting request to the `ref` URL inside `fetch`/`_map` with its own failure handling; be aware this multiplies requests (each already preceded by jitter in `request_get`) and consider capping it.
