# Marketplaces (python-jobspy bridge)

- **Source**: `src/jobdigest/marketplaces/__init__.py` (48 lines), `src/jobdigest/marketplaces/jobspy_bridge.py` (171 lines).
- **Layer**: collection sources (job aggregators: LinkedIn, Indeed, Glassdoor, Naukri, ...).
- **Depends on**: optional extra `marketplaces = ["python-jobspy>=1.1.82"]` (`pyproject.toml:30`), imported lazily as `jobspy.scrape_jobs`; `jobdigest.ats.base` (`NormalizedJob`, `decode_html_content`, `parse_posted_at`); `jobdigest.portals.PortalResult`.
- **Used by**: `collect._collect_side_lanes` (`state.side_unit("marketplace", row, ..., url_key="site")`). Config `config/marketplaces.yaml` (example: `config/marketplaces.example.yaml`).
- **Tests**: `tests/test_marketplaces_bridge.py`, `tests/test_collect_marketplaces.py`.

## Why this exists / responsibility

Large job boards have no public API. The `python-jobspy` library scrapes them and returns a pandas `DataFrame`. This package adapts one **marketplace row** (a search: site, term, location, filters) into a `scrape_jobs(...)` call and converts each DataFrame record into a `NormalizedJob`. It is optional: without the extra installed, a row errors as `error_class="config"` and the rest of the run continues.

A row (from the example config):

```yaml
- name: indeed-intern-india
  site: indeed
  enabled: false
  search_term: software engineer intern
  location: Bengaluru, Karnataka
  country_indeed: India
  results_wanted: 30
  hours_old: 72
  job_type: internship
```
`name` becomes the job `source`; `collect` stores `site` (not a URL) in the error table's URL column (`url_key="site"`).

## Public interface at a glance

| Symbol | Signature | Purpose |
|---|---|---|
| `SITES` | frozenset | `linkedin, indeed, zip_recruiter, glassdoor, google, bayt, naukri, bdjobs` |
| `marketplace_proxies` | `() -> list[str] \| None` | proxies from env |
| `run_one` | `run_one(row, *, scrape_fn=None) -> PortalResult` | one search row -> result |
| `scrape_row` | `scrape_row(row, *, proxies=None, scrape_fn=None) -> list[NormalizedJob]` | call jobspy and map |
| `row_to_normalized` | `row_to_normalized(record, *, source) -> NormalizedJob \| None` | one record -> job |
| `_scrape_kwargs`, `_records_from_scrape`, `_posted_at`, `_workplace_hint`, `_first_job_type`, `_http_url` | private helpers | |

## Detailed walkthrough

### `marketplace_proxies`
**Location** `src/jobdigest/marketplaces/__init__.py:11` · reads env `JOBDIGEST_MARKETPLACE_PROXIES`, splits on commas, strips blanks; returns the list or `None` if empty. LinkedIn rate-limits quickly, so the example config suggests proxies.

### `run_one`
**Location** `src/jobdigest/marketplaces/__init__.py:17` · `def run_one(row: dict, *, scrape_fn=None) -> PortalResult`
1. `name = row["name"]`, `site = str(row.get("site") or "").strip().lower()`.
2. `site not in SITES` -> `PortalResult(error, "parse", "unknown site: <site>")` (empty -> `(empty)`).
3. `jobs = scrape_row(row, proxies=marketplace_proxies(), scrape_fn=scrape_fn)`; return `PortalResult(status="ok", jobs=jobs, name=name)`. **An empty list is still `ok`** (test `test_run_one_empty_scrape_is_ok`), unlike portals/feeds; `collect` then upserts nothing and sweeps the source (see the NOTE in `collect.side_unit`).
4. `except ImportError` -> `error_class="config"`, `detail="python-jobspy not installed"`. (`config` is not one of the portal classes; collect stores the string as is.)
5. `except Exception as exc` -> `"timeout"` if `"timeout"` is in the exception type name or message (case-insensitive), else `"http"`.
No `http_status` or `url` is set on these results.

### `scrape_row`
**Location** `src/jobdigest/marketplaces/jobspy_bridge.py:156` · `def scrape_row(row, *, proxies=None, scrape_fn=None) -> list[NormalizedJob]`
1. If `scrape_fn is None`: `from jobspy import scrape_jobs as scrape_fn` (this raises `ImportError` if the extra is missing; caught by `run_one`).
2. `source = str(row.get("name") or "").strip()`; `payload = scrape_fn(**_scrape_kwargs(row, proxies=proxies))`.
3. For each record in `_records_from_scrape(payload)`: `row_to_normalized(record, source=source)`; keep non-`None`.

### `_scrape_kwargs`
**Location** `:109` · builds the keyword arguments for `jobspy.scrape_jobs`:
- always: `site_name=[site]`, `search_term=row.search_term or None`, `location=row.location or None`, `results_wanted=int(row.results_wanted or 30)`, `verbose=0`, `linkedin_fetch_description=bool(fetch_desc)`.
- `fetch_desc = row.get("linkedin_fetch_description")`; if that is `None` **and** `site == "naukri"`, it defaults to `True` (Naukri descriptions need it; test `test_naukri_defaults_linkedin_fetch_description`).
- optional, only when set: `google_search_term`, `country_indeed`, `hours_old` (int; `0`-like `""`/None skipped), `job_type`, `is_remote` (when the key is present and not `None`), `proxies` (when non-empty).
Verified: a naukri row with `results_wanted: ""`, `hours_old: 72`, `is_remote: False` and proxies gives `{'site_name': ['naukri'], ..., 'results_wanted': 30, 'linkedin_fetch_description': True, 'hours_old': 72, 'job_type': 'internship', 'is_remote': False, 'proxies': ['http://p']}`.

### `_records_from_scrape`
**Location** `:141` · `None` -> `[]`; an object with `to_dict` (a DataFrame): `[]` if `.empty`, else `payload.to_dict("records")` filtered to dicts; a plain `list` -> its dict items (the test seam); anything else -> `[]`.

### `row_to_normalized`
**Location** `:77` · `def row_to_normalized(record: dict, *, source: str) -> NormalizedJob | None`
1. `title = str(record.get("title") or "").strip()`; empty -> `None`.
2. `job_url = _http_url(record["job_url"])`, `direct = _http_url(record["job_url_direct"])`; `apply_url = direct or job_url`; `hosted_url = job_url or apply_url`. So the employer's own page is preferred for applying while the marketplace listing is the "hosted" page.
3. `ext = str(record.get("id") or "").strip()`; if empty and a hosted URL exists, `ext = sha256(hosted_url).hexdigest()[:16]`.
4. If there is no apply URL, no hosted URL **and** no id -> `None`.
5. `desc_text = decode_html_content(str(desc))` if description truthy else `""`.
6. `NormalizedJob(... company=record.company, location_raw=record.location, employment_hint=_first_job_type(job_type), workplace_hint=_workplace_hint(record), ats="marketplace", external_id=ext, raw_json=json.dumps(record, separators=(",", ":"), ensure_ascii=False, default=str), posted_at=_posted_at(record["date_posted"]))`.
   `default=str` lets dates and pandas types serialise.
Example (run offline): record `{"id":"li-1","title":"SWE Intern","company":"Acme","location":"Bengaluru","job_url":"https://x/y","job_url_direct":"https://acme.com/a","job_type":"internship, fulltime","is_remote":True,"date_posted":date(2026,9,1),"description":"<p>Hi</p>"}` becomes `apply_url="https://acme.com/a"`, `hosted_url="https://x/y"`, `employment_hint="internship"`, `workplace_hint="remote"`, `description_text="Hi"`, `ats="marketplace"`, `external_id="li-1"`, `posted_at="2026-09-01T00:00:00Z"`.

### `_first_job_type`, `_workplace_hint`, `_http_url`
- `_first_job_type` (`:34`): first comma-separated token of `job_type`, or `None`.
- `_workplace_hint` (`:43`): non-empty `work_from_home_type` wins; else `is_remote` truthy (`True`, `"true"`, `"1"`) -> `"remote"`; else `None`.
- `_http_url` (`:27`): the value only if it starts with http(s), else `""`.

### `_posted_at`
**Location** `:53` · `def _posted_at(value) -> str | None` normalises the many types JobSpy returns into ISO-8601 UTC with `Z`:
1. `None` -> `None`. `value != value` (NaN/NaT is not equal to itself) -> `None`; `TypeError/ValueError` from the comparison is ignored.
2. Objects with `to_pydatetime` (pandas `Timestamp`) are converted (`ValueError/TypeError` -> `None`).
3. `datetime`: naive is assumed UTC; converted to UTC and passed to `parse_posted_at(isoformat)`. `date`: midnight of that day. Otherwise `parse_posted_at(value)` handles strings and epoch numbers.
Verified outputs: `date(2026,9,1)` -> `2026-09-01T00:00:00Z`; `datetime(2026,9,1,12,0)` -> `2026-09-01T12:00:00Z`; `nan`/`None`/`"garbage"` -> `None`; `"2026-09-01"` -> `2026-09-01T00:00:00Z`. (Pandas is not installed in the doc venv so the `Timestamp` branch was not executed; `test_posted_at_normalized_for_all_input_types` covers the input types.)

## Failure modes and edge cases

- Not installed: `config` error each run for each enabled marketplace row. If **all** enabled units fail, `collect._Collector.failed` is true (`error_units == enabled_units`).
- Site blocks (LinkedIn 429): exceptions from jobspy become `http`/`timeout` with the library's message.
- Empty result is `ok` (inconsistent with portals/feeds, see above).
- `results_wanted` non-numeric strings raise `ValueError` inside `_scrape_kwargs`, caught by `run_one` as `http`.
- Jobs inherit `company` straight from the marketplace; blank company is allowed.

## How to modify / extend safely

Add a site by adding it to `SITES` (it must be a JobSpy `site_name`) and, if it needs special arguments, to `_scrape_kwargs`. Use `scrape_fn` in tests to avoid importing jobspy. Marketplace rows are never run through robots.txt (JobSpy talks to the sites itself), so keep them disabled by default as the example config does.
