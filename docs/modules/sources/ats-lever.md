# Lever adapter

- Source: `src/jobdigest/ats/lever.py` (98 lines). Layer: source acquisition ([ATS overview](ats-overview.md)).
- Depends on: `jobdigest.ats.base` (`get_json_page`, `decode_html_content`, `join_locations`, `parse_posted_at`, `empty_or_ok`). Used by: `get_adapter`.
- Tests: `tests/test_ats_lever.py` (fixtures `tests/fixtures/ats/lever/{page0,page1,empty,not_found}.json`).

## Endpoint and pagination

`GET https://api.lever.co/v0/postings/{token}?mode=json&limit={page_limit}&skip={skip}` (default `page_limit = 100`). The response is a bare JSON **array** of postings (not an object). Pagination is offset based: start at `skip=0`, add the number of items received, stop when a page is shorter than `page_limit`. There is no total in the payload, so the adapter cannot verify completeness other than by page failure.

## Public interface

| Symbol | Signature | Purpose |
|---|---|---|
| `LeverAdapter` | `LeverAdapter(client, *, sleep, page_limit=100)` | `ats = "lever"` |
| `LeverAdapter.fetch` | `(company) -> FetchResult` | offset-paginated fetch |
| `LeverAdapter._map` | `(company, item) -> NormalizedJob` | field mapping |

## Detailed walkthrough

### `LeverAdapter.__init__`
`lever.py:22`. Stores `client`, `sleep`, `page_limit` (no clamping or validation, unlike Greenhouse/SmartRecruiters; `page_limit=0` would loop forever, see edge cases).

### `LeverAdapter.fetch`
**Location** `src/jobdigest/ats/lever.py:33` · **Signature** `def fetch(self, company: CompanyConfig) -> FetchResult`
**Called by** `collect._default_fetch`, `probe.main` · **Calls** `get_json_page`, `self._map`, `empty_or_ok`.
**Step-by-step**
1. Init `skip = 0`, `collected: list[dict] = []`, `last_status = None`, `complete = True`.
2. `while True:` build the URL with `limit` and `skip`, call `get_json_page(..., valid=lambda p: isinstance(p, list), label="lever")`.
3. `if pg.http_status is not None: last_status = pg.http_status`.
4. If `pg.failure is not None`:
   - nothing collected yet (first page): `return pg.failure` (so 404 -> `DEAD_TOKEN`, bad shape -> `ERROR`).
   - otherwise `complete = False; break`: keep what has been fetched, mark partial.
5. `payload = pg.payload`; `collected.extend(item for item in payload if isinstance(item, dict))`.
6. `if len(payload) < self._page_limit: break` (last page). Else `skip += len(payload)` (advance by the raw count, including any non-dict entries) and loop.
7. Map `collected` through `_map`, return `FetchResult(empty_or_ok(len(jobs)), last_status, jobs, complete=complete)`.

Edge: the first page being empty (`[]`) yields `EMPTY_BOARD` (`test_empty_board`). A full-length last page costs one extra request that returns `[]` (which is shorter than the limit and ends the loop).

Why partial results are returned as `OK`: a later-page failure after a good first page means most of the board was seen; discarding it would throw away fresh jobs, so they are upserted and only the tombstone sweep is suppressed via `complete=False` (`test_partial_pagination_failure_is_incomplete_but_ok`: page 0 ok, page at skip=2 returns 404, result `OK`, `complete False`, 2 jobs). Note that even a 404 on a later page is treated as a partial failure, never as `DEAD_TOKEN`.

Tests: `test_two_pages_yield_three_jobs` uses `page_limit=2` with two fixture pages and serves page 0 for `skip=0` and page 1 for `skip=2`, and checks ids `lev-1..lev-3` in order and the mapped fields of the first and third job.

### `LeverAdapter._map`
**Location** `lever.py:65` · **Signature** `def _map(self, company: CompanyConfig, item: dict) -> NormalizedJob`

| `NormalizedJob` field | Raw source | Rule |
|---|---|---|
| `title` | `text` | stripped |
| `company` | n/a | `company.name` (Lever's payload carries no company name) |
| `location_raw` | `categories.location` + `categories.allLocations[]` | `join_locations([cats.get("location"), *[str(x) for x in extra]])` |
| `hosted_url` | `hostedUrl` | `""` if missing |
| `apply_url` | `applyUrl` | falls back to `hostedUrl` |
| `employment_hint` | `categories.commitment` | stripped string, `None` if absent or empty |
| `workplace_hint` | `workplaceType` | stripped string, `None` if absent or empty |
| `description_text` | `descriptionPlain` else HTML parts | see below |
| `external_id` | `id` | string |
| `posted_at` | `createdAt` | `parse_posted_at` (epoch milliseconds; an int > 1e12 is divided by 1000) |

Description logic:
```python
plain = item.get("descriptionPlain")
if isinstance(plain, str) and plain.strip():
    desc = plain.strip()
else:
    chunks = [
        decode_html_content(item.get("description")),
        str(item.get("openingPlain") or "").strip(),
        str(item.get("additionalPlain") or "").strip(),
    ]
    desc = join_locations(chunks).replace(" | ", " ")
```
Preferred: `descriptionPlain`. Fallback: the HTML `description` (decoded) plus the `openingPlain` and `additionalPlain` blocks. The fallback reuses `join_locations` only for its "drop empty and duplicate chunks, join with ' | '" behavior, then swaps the separator for a space. Side effect: an exact duplicate chunk is dropped; a description that itself contains the substring `" | "` also has it replaced by a space. Nothing in the tests covers the fallback branch (the fixtures all have `descriptionPlain`).

**Real example** (fixture `page0.json`, item 1):
```json
{"id": "lev-1", "text": "Backend Intern",
 "hostedUrl": "https://jobs.lever.co/example/lev-1",
 "applyUrl": "https://jobs.lever.co/example/lev-1/apply",
 "categories": {"location": "Bengaluru", "commitment": "Intern", "allLocations": ["India"]},
 "workplaceType": "hybrid", "descriptionPlain": "Work in Bengaluru", "createdAt": 1700000000000}
```
becomes (offline run, `CompanyConfig("exco","Example Co","lever","example")`):
```json
{"title": "Backend Intern", "company": "Example Co", "location_raw": "Bengaluru | India",
 "apply_url": "https://jobs.lever.co/example/lev-1/apply", "hosted_url": "https://jobs.lever.co/example/lev-1",
 "employment_hint": "Intern", "workplace_hint": "hybrid", "description_text": "Work in Bengaluru",
 "source": "exco", "ats": "lever", "external_id": "lev-1", "posted_at": "2023-11-14T22:13:20Z"}
```
Item 2 has no `createdAt`, so `posted_at` is `null`.

## Quirks

- The payload is a top-level array, not an object. `workplaceType` is passed through verbatim (the fixtures use lowercase `remote`, `hybrid`).
- No de-duplication of postings across pages (the adapter uses a plain list, not `JobCollector`), so a vendor that repeated an item across pages would produce duplicate `NormalizedJob`s; downstream upsert by `(source, external_id)` would collapse them.

## Failure modes and edge cases

- No page cap: if a vendor kept returning full pages forever, the loop would not stop (Greenhouse and SmartRecruiters have `max_pages`; Lever does not). `page_limit <= 0` would make `len(payload) < 0` never true and, since `skip += len(payload)` can stay 0 for an empty page, loop forever on an empty response. The default 100 avoids this; nothing validates the argument.
- `FetchResult.http_status` is the status of the last request (including a failed later page).

## How to modify safely

If you add a `max_pages` cap, set `complete = False` when it triggers, as Greenhouse does.
