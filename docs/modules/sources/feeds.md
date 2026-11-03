# RSS/Atom feeds

- **Source**: `src/jobdigest/feeds.py` (159 lines).
- **Layer**: collection sources, third lane next to ATS boards and [portals](portals-overview.md).
- **Depends on**: `feedparser`, `httpx`, `jobdigest.ats.base` (`NormalizedJob`, `decode_html_content`), `jobdigest.portals.PortalResult`, `jobdigest.portals.session.UA`.
- **Used by**: `src/jobdigest/collect.py:_collect_side_lanes` (`state.side_unit("feed", row, feed_run_fn or feeds_mod.run_one)`). Config: `config/feeds.yaml` (rows `name`, `url`, `enabled`, `notes`).
- **Tests**: `tests/test_feeds.py`; fixtures `tests/fixtures/feeds/python_jobs.xml`, `tests/fixtures/feeds/foss_united.xml`; `tests/test_no_stealthy.py` (feeds.py may not mention Scrapling).

## Why this exists / responsibility

An RSS or Atom feed is the cheapest, most stable source: structured XML, no JavaScript, no markup drift. `feeds.py` fetches a feed with plain `httpx`, parses it with `feedparser` (which handles RSS 0.9x/1.0/2.0 and Atom, malformed XML, and date parsing), and maps entries to `NormalizedJob`. Deliberately **no Scrapling**: `test_feeds_module_stays_httpx_feedparser` asserts the source text contains none of `scrapling`, `XMLFeedSpider`, `Fetcher.get`.

Current `config/feeds.yaml`: `python-jobs` (`https://www.python.org/jobs/feed/rss/`, disabled) and `foss-united-rss` (`https://fossunited.org/jobs/rss.xml`, enabled; the note says the HTML portal for the same site stays enabled and dedup is by apply URL).

## Public interface at a glance

| Symbol | Signature | Purpose |
|---|---|---|
| `run_one` | `run_one(feed: dict, *, http_get=None, sleep=None) -> PortalResult` | fetch + parse one feed row |
| `fetch_feed` | `fetch_feed(url, *, http_get=None, sleep=None) -> str` | HTTP GET with jittered delay |
| `parse_feed` | `parse_feed(xml_text: str, *, source: str) -> list[NormalizedJob]` | XML -> jobs |
| `_entry_link`, `_entry_id`, `_entry_posted_at` | private | entry field extraction |
| `_THROTTLE_MIN_S`, `_THROTTLE_MAX_S` | `0.5`, `1.0` | random pre-request delay bounds |

## Detailed walkthrough

### `run_one`
**Location** `src/jobdigest/feeds.py:130` · `def run_one(feed: dict, *, http_get=None, sleep=None) -> PortalResult`
**Purpose** same contract as `portals.run_one`: never raise, return a classified result.
**Called by** `collect.side_unit` (via `feed_run_fn or feeds_mod.run_one`). **Calls** `fetch_feed`, `parse_feed`.
1. `name = str(feed.get("name") or "")`, `url = str(feed.get("url") or "")`.
2. In `try`: `xml = fetch_feed(url, http_get=..., sleep=...)`; `jobs = parse_feed(xml, source=name)`.
3. `except Exception as exc`: `timed_out = isinstance(exc, (httpx.TimeoutException, TimeoutError))`; return `PortalResult(status="error", error_class="timeout" if timed_out else "http", detail=str(exc), name=name, url=url)`. Unlike the portal lane this classifies by **exception type**, not by message text (test `test_run_one_timeout_classified_by_type`: `RuntimeError("a timeout word in message")` is `http`). HTTP status errors from `raise_for_status` (`httpx.HTTPStatusError`) land in `http`, but the status code is not copied into `http_status` (it stays `None`).
4. `not jobs` -> `PortalResult(error, error_class="parse", detail="0 entries", http_status=200)`. A parse error inside `parse_feed` is rare because feedparser is lenient; a non-feed page (HTML) yields zero entries and is reported here.
5. Success: `PortalResult(status="ok", jobs=jobs, http_status=200, name=name, url=url)`.

### `fetch_feed`
**Location** `:105` · `def fetch_feed(url, *, http_get=None, sleep=None) -> str`
1. Real path (`http_get is None`): sleep `random.uniform(0.5, 1.0)` seconds using `(sleep or time.sleep)`, then `httpx.get(url, headers={"User-Agent": UA}, timeout=30.0, follow_redirects=True)`, `response.raise_for_status()`, return `response.text`. The jitter is a politeness delay between feed requests (feeds are fetched back-to-back by collect).
2. Injected path (tests): `response = http_get(url)`; return it if `str`; else `.text` if not `None`; else decode `.content` bytes; else `str(response)`.

### `parse_feed`
**Location** `:38` · `def parse_feed(xml_text: str, *, source: str) -> list[NormalizedJob]`
1. `parsed = feedparser.parse(xml_text or "")`; iterate `parsed.entries`.
2. `link = _entry_link(entry)`; skip unless it starts with `http://`/`https://`. `title = str(entry.title or "").strip()`; skip if empty.
3. `desc` = `entry.summary` or `entry.description` or `""`; `ext = _entry_id(entry)`, falling back to `urlsplit(link).path.strip("/")`.
4. Build `NormalizedJob(title, company=source, location_raw="", apply_url=link, hosted_url=link, employment_hint=None, workplace_hint=None, description_text=decode_html_content(desc), source=source, ats="rss", external_id=str(ext), raw_json=<compact json of {"title","url","description"}>, posted_at=_entry_posted_at(entry))`.
   Feeds carry no company or location, so `company` is the feed name and `location_raw` is empty (the hard filter's India rules therefore rely on title/description text).
Verified on fixtures: `python_jobs.xml` -> 2 jobs, first is `Software Engineer Intern` with `external_id="https://www.python.org/jobs/1"` and `ats="rss"`; `foss_united.xml` -> 2 jobs with GUID external ids (`https://fossunited.org/jobs/fixture-intern-a`), `posted_at=None` since the fixtures have no `pubDate`.

### `_entry_link`
**Location** `:79` · `entry.link` stripped; else the first non-empty `href` among `entry.links` (dicts or objects), else `""`.

### `_entry_id`
**Location** `:90` · tries keys `id` then `guid` (attribute, then dict `.get`); a dict value is reduced to `val["value"]` or `val["guid"]`; first non-empty stripped string wins, else `""`. So the external id is the feed GUID when present, which makes it stable even if the link changes.

### `_entry_posted_at`
**Location** `:23` · for `published_parsed` then `updated_parsed`: take the `time.struct_time`, build `datetime(*tuple(st)[:6], tzinfo=timezone.utc)`, and return `isoformat().replace("+00:00", "Z")`. `TypeError`/`ValueError` -> try the next key. feedparser has already normalised the date to UTC, so e.g. `Mon, 01 Sep 2025 10:30:00 +0530` becomes `2025-09-01T05:00:00Z` (test `test_parse_feed_populates_posted_at_iso_z`). `None` if no date.

## Failure modes and edge cases

- Feed returns an HTML error page with HTTP 200: zero entries -> `parse` / `0 entries`.
- `raise_for_status` raises for 4xx/5xx -> `http` with message like `Client error '404 Not Found' for url ...`.
- `feedparser` never raises on malformed XML (sets `bozo`); `bozo` is ignored here.
- An empty feed counts as an error (so it is never an "ok but empty" source that could tombstone the previous jobs; see the note in `collect.side_unit`).
- Duplicate coverage with a portal for the same site is resolved by the DB's apply-URL dedup, not here.

## How to modify / extend safely

Add feeds by adding rows to `config/feeds.yaml`; no code needed. If you need location or company, extract them from `entry.tags`/`entry.author` in `parse_feed`. Keep the module free of Scrapling to satisfy `test_no_stealthy.py`.
