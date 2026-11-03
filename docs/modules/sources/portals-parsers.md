# Portal parsers

- **Source**: `src/jobdigest/portals/generic.py` (135 lines), `foss_united.py` (51), `hasjob.py` (170), `null_community.py` (44).
- **Layer**: collection sources; pure functions from HTML/JSON to `NormalizedJob`. No network.
- **Used by**: `portals.PARSERS` (key `foss_united`, `null_community`, `hasjob`, `generic_cards`) via [`run_one`](portals-overview.md#run_one).
- **Tests**: `tests/test_portals_parsers.py` with `tests/fixtures/portals/*`.

## Why this exists / responsibility

Each site has its own markup. A parser takes whatever the fetch stage produced (a Scrapling `Response` with a live DOM, or an HTML string) and returns `NormalizedJob`s. All four parsers share the same pattern: select card nodes with CSS, build a plain dict per card (`title`, `url`, `company`, `location`, `description`), then call `generic.jobs_from_cards`, which is the single place that validates a card and builds the `NormalizedJob`.

All four have the signature `parse(page_or_html, *, source, base_url) -> list[NormalizedJob]`. `source` is the portal `name` from YAML (`foss-united`); `base_url` is the portal URL, used to resolve relative links.

## Public interface at a glance

| Module.symbol | Purpose |
|---|---|
| `generic.parse` | heuristic parser for unknown sites (`generic_cards`) |
| `generic.jobs_from_cards` | card dict -> `NormalizedJob` (shared by all) |
| `generic.to_root`, `css_select`, `node_text`, `node_href`, `first_text`, `first_href`, `_http_url` | DOM helpers |
| `foss_united.parse` | FOSS United jobs page (two layouts) |
| `null_community.parse` | null community job board (two layouts) |
| `hasjob.parse` | Hasjob: JSON first, then HTML (two layouts) |

## Shared helpers (`generic.py`)

### `_http_url`
**Location** `src/jobdigest/portals/generic.py:10` · `def _http_url(url: str, base: str) -> str`. `urljoin(base, url.strip())`; returns it only if it starts with `http://` or `https://` (case-insensitive), else `""`. This rejects `javascript:`, `mailto:`, `#` anchors resolved to non-http, etc. `null_community` imports this private helper; `hasjob` defines its own near-identical `_http_url(raw, base_url)` that also short-circuits on an empty string.

### `jobs_from_cards`
**Location** `:17` · **Signature** `def jobs_from_cards(cards: list[dict], *, source: str, ats: str = "portal") -> list[NormalizedJob]`
**Purpose** convert card dicts into jobs; the quality gate for all HTML parsers.
**Called by** all four parsers. **Calls** `urlsplit`, `decode_html_content` (`src/jobdigest/ats/base.py:70`), `json.dumps`.

For each `card`:
1. `apply_url = str(card.get("url") or "")`; skip unless it starts with `http://`/`https://`.
2. `title = str(card.get("title") or "").strip()`; skip if empty.
3. `path = urlsplit(apply_url).path.strip("/")` becomes `external_id`. So the identity of a portal job is its URL path; two jobs with the same path on different hosts would collide, but sources are separated by `source` in the DB.
4. Build:
```python
NormalizedJob(title=title, company=str(card.get("company") or source),
              location_raw=str(card.get("location") or ""),
              apply_url=apply_url, hosted_url=apply_url,
              employment_hint=None, workplace_hint=None,
              description_text=decode_html_content(str(card.get("description") or "")),
              source=source, ats=ats, external_id=path,
              raw_json=json.dumps(card, separators=(",", ":"), ensure_ascii=False))
```
   `ats` is always `"portal"`; `company` defaults to the portal name when the card has none; `employment_hint`/`workplace_hint`/`posted_at` are never set (portals have no structured values). `raw_json` is the card dict, compact.
5. No de-duplication here (generic does it itself with `seen`; hasjob JSON path de-dupes by URL).

### `to_root`
**Location** `:54` · `def to_root(page_or_html, base_url="")`. If the argument is not `str`/`bytes` and has a `css` attribute (a Scrapling `Response`/`Selector`), return it unchanged. Otherwise decode bytes (UTF-8, replace) and build `scrapling.parser.Selector(str(html or ""), url=base_url, adaptive=False)`. `adaptive=False` turns off Scrapling's element-relocation feature.

### `css_select`, `node_text`, `node_href`, `first_text`, `first_href`
- `css_select(node, selector)` (`:66`): `node.css(selector, adaptive=False)` as a list; `[]` if the node has no callable `css` or the result is `None`.
- `node_text(node)` (`:74`): `None` -> `""`; else `node.text` stripped if truthy; else `node.get_all_text(separator=" ", strip=True)`; else `str(node).strip()`.
- `node_href(node)` (`:86`): `node.attrib["href"]` stripped, `""` if absent.
- `first_text(node, selector)` (`:94`): text of the first match or `""`.
- `first_href(node, selector, *, base)` (`:101`): `_http_url(node_href(first match), base)`.

**Oddity (verified on fixtures)**: `node_text` tests `if text:` on Scrapling's *direct* text. For a card such as `<div class="job-card">\n <div ...>` the direct text is whitespace only (`'\n      '`), which is truthy, so the function returns `"\n      ".strip() == ""` and never reaches `get_all_text`. Consequently the card `description` is empty for the `foss_united.html`, `foss_united_v3.html`, `hasjob*.html`, `null_community*.html` fixtures (verified by running the parsers), while `generic_cards.html` (an `<article>` whose direct text is `''`) falls through to `get_all_text` and gets `"Frontend Intern Apply"`. Title and location are unaffected. The consequence is that downstream stages that read `description_text` (YOE regex, summaries) see nothing for those portals. This is existing behaviour, not a documented decision.

## Parsers

### `generic.parse` (`generic_cards`)
**Location** `src/jobdigest/portals/generic.py:108` · for sites with no dedicated parser (e.g. HuntYourTribe).
1. `root = to_root(page_or_html, base_url)`; `cards = []`; `seen = set()` of hrefs.
2. For `selector` in `("article", '[class*="job"]', "li")` (tried in this order, **all** of them, results accumulate):
   - for each `node`: `title = first_text(node, "h2") or first_text(node, "h3")`.
   - iterate `a[href]` descendants; the first anchor with an http(s) URL (after `_http_url(href, base_url)`) becomes `href`; if no title yet, the anchor text becomes the title; `break`.
   - skip if no `href` or `href in seen`; else record and append a card with `company=source`, `location=""`, `description=node_text(node)`.
3. `return jobs_from_cards(cards, source=source)`.
Because of the shared `seen` set, an `<li>` nested in an `<article>` does not produce a duplicate. Precision is low by design: any `<li>` with a link (nav menus, footers) is a card, so a site like that needs a dedicated parser. Fixture `generic_cards.html` -> 2 jobs (`Frontend Intern` -> `https://example.com/a`, external_id `a`).

### `foss_united.parse`
**Location** `src/jobdigest/portals/foss_united.py:24`; helper `_attr(node, name)` (`:17`) reads and strips an attribute.
Two layouts handled, appended into one `cards` list:
- **Legacy `.job-card`** (fixture `foss_united.html`): `title=first_text(card, ".job-title")`, `url=first_href(card, "a.apply", base=base_url)`, `location=first_text(card, ".job-location")`, `company=source`, `description=node_text(card)`.
- **v3 `.job-item`** (fixture `foss_united_v3.html`): `href = first_href(item, "a", base=base_url)`, falling back to `urljoin(base_url, item["data-route"])`; `title = first_text(item, "h6") or item["data-title"]`; `location = item["data-location"]`; `company = item["data-company-display"] or source`.
The CSS class `.job-item` does not match the `job-item-expired` class in the fixture (class tokens are matched whole), so expired entries are skipped (test `test_foss_united_v3_job_item_cards` expects 2).

Example (run offline on the fixtures):

| Fixture | title | company | location | apply_url | external_id |
|---|---|---|---|---|---|
| `foss_united.html` #1 | Software Engineer Intern | foss-united | Bengaluru | https://fossunited.org/jobs/1 | `jobs/1` |
| `foss_united_v3.html` #1 | SDE Intern | Zingg AI | Bengaluru | https://fossunited.org/jobs/one | `jobs/one` |

### `null_community.parse`
**Location** `src/jobdigest/portals/null_community.py:18`.
- **`li.job`** (fixture `null_community.html`): first `a` gives `url=_http_url(node_href(a), base_url)` and `title=node_text(a)`; `location=first_text(card, ".meta")`; company = source.
- **`.job-item`** (fixture `null_community_job_item.html`): `title=first_text(item, ".job-title")`, `url=first_href(item, ".job-title a", base=base_url)`, `location=first_text(item, ".item-location")`, `company=first_text(item, ".company-title") or source`.
Example: second layout gives `company="Null Co"`, `apply_url="https://jobs.null.community/jobs/post/1/Null-Co/Platform-Intern/"`, `external_id="jobs/post/1/Null-Co/Platform-Intern"`.

### `hasjob`
Hasjob is the most involved because the site can answer with JSON (when `Accept: application/json`, which `run_one` sets for parser `hasjob`) or HTML cards; and Scrapling wraps JSON responses in HTML.

#### `hasjob.parse`
**Location** `src/jobdigest/portals/hasjob.py:144`.
1. `json_jobs = _try_json_jobs(...)`; if it is **not None** (even an empty list) return it.
2. Otherwise treat as HTML: `div.job-card` -> `title=first_text(card, "a.job-title")`, `url=first_href(card, "a.job-title", ...)`, `company=first_text(card, ".job-company") or source`, `location=first_text(card, ".job-location")`.
3. `.stickie` (the live Hasjob layout; fixture `hasjob_stickie.html`) -> `title=first_text(stickie, ".headline")`, `url=_stickie_url(stickie, base_url)`, `company=first_text(stickie, ".company-name") or source`, `location=first_text(stickie, ".top-left")`.
4. `jobs_from_cards`.

Note on empty JSON: if JSON parses but yields zero cards, `parse` returns `[]` without trying HTML; `_parse_jobs` then reports `0 cards`.

#### `_try_json_jobs` / `_as_text` / `_json_candidate`
- `_as_text` (`:38`): `bytes` -> decode; `str` -> as is; else `page.html_content`, then `page.html` (bytes decoded) or `None`.
- `_json_candidate(text)` (`:114`): strip; empty -> `None`; if it starts with `{` or `[` return it. Else match `_P_INNER = <p>([\s\S]*?)</p>` and `html.unescape` the inner text; return it if it starts with `{`/`[`. This handles Scrapling's HTTP fetcher, which wraps `application/json` as `<html><body><p>{...}</p></body></html>`. Verified: `<html><body><p>{&quot;a&quot;:1}</p></body></html>` -> `{"a":1}`.
- `_try_json_jobs` (`:130`): `None` if no text/candidate or `json.JSONDecodeError`; else `jobs_from_cards(_json_cards(payload, ...), source=source)`. Invalid JSON such as `{not-json` therefore falls through to the HTML path, finds nothing, and `run_one` reports `parse` / `0 cards` (test `test_hasjob_invalid_json_does_not_crash_run_one`).

#### `_json_cards` and `_flatten_rows`
`_flatten_rows(items)` (`:61`): for each item, a dict with a list under `"posts"` is replaced by those posts (Hasjob groups posts per employer), otherwise the item is kept.
`_json_cards(payload, *, source, base_url)` (`:71`):
1. List payload -> `rows = _flatten_rows(payload)`. Dict payload -> `payload["grouped"]` flattened first, then the **first non-empty** list among keys `posts`, `jobs`, `items`, `data` (loop `break`s after the first hit).
2. For each dict `row`: `title` = first truthy of `headline`, `title`, `name`, `job_title`; `raw_url` = first of `url`, `href`, `link`, `apply_url`; `url=_http_url(raw_url, base_url)`; skip if no title/url or URL already seen.
3. `company = company_name or company or source`; `location`, `description` from same-named keys; emit the card dict.
Fixture `hasjob.json` (grouped -> posts, relative url `/acme/intern-1`) yields `apply_url=https://intern.hasjob.co/acme/intern-1`, company `Acme Labs`, `external_id=acme/intern-1`. Tests also cover list payloads, `jobs`/`items`/`data` wrappers and skipping rows without a URL/title.

#### `_stickie_url`, `_attr`, `_http_url`
`_stickie_url` (`:30`): `href` of the node else its `data-href` attribute, joined with `base_url`, accepted only if http(s). Needed because the second stickie in the fixture is a `div` with `data-href` rather than an anchor.

## Failure modes and edge cases

- A parser that raises is caught by `_parse_jobs` and reported as `parse`; one returning `[]` is reported as `parse: 0 cards`.
- Relative URLs are resolved against the *portal URL*, not the page's `<base>` tag.
- Jobs with a non-http URL (`javascript:void(0)`) or empty title are dropped silently.
- `external_id` is the URL path, so a site that puts a changing token or query string in the path would create new rows each run. Query strings are not part of the id.
- Parsers see the *final* page only; pagination, infinite scroll and "load more" are not followed (Hasjob JSON has a `loadmore` key that is ignored).

## How to modify / extend safely

1. Capture a real page into `tests/fixtures/portals/<site>.html` (strip personal data).
2. Write `<site>.py` with `parse(page_or_html, *, source, base_url)`, build card dicts, return `jobs_from_cards(cards, source=source)`.
3. Register in `PARSERS` (`src/jobdigest/portals/__init__.py:26`), add the YAML row to `config/portals.example.yaml`, add a test.
4. Keep selectors tolerant of both layouts, as the existing parsers do, since sites redesign.
5. If you want descriptions to be populated, fix `node_text` (use the stripped direct text only when non-empty) and re-check the fixtures; this changes `description_text` for every portal.
