# ATS token research scripts

- **Source**: `scripts/_probe_common.py` (45 lines), `scripts/probe_ats_tokens.py` (835), `scripts/probe_ats_tokens_wave2.py` (465).
- **Layer**: maintainer tooling, one-off research; not imported by the package and not run by CI or the pipeline. The in-package probe for existing boards is [probe](probe.md); the automated widening is [expand](expand.md).
- **Depends on**: `httpx`, `yaml`. Network access (public Lever/Ashby/Workable board APIs) when run.
- **Tests**: none found for these scripts (I grepped `tests/`).

## Why this exists / responsibility

They were used to find *candidate* companies on Lever, Ashby and Workable: take curated slug lists, probe each public board API, and write a YAML file (`config/research_ats_boards.yaml`) of boards that returned a valid JSON payload, annotated with evidence and a confidence level. A human then copies chosen entries into `config/companies.yaml`.

## `_probe_common.py`

- `SLUG_DIR_ENV = "JOBDIGEST_SLUG_DIR"`; expected file names inside the slug directory: `lever_slugs.txt`, `ashby_slugs.txt`, `hire_signal.txt`, `workable_candidates.txt` (the `.txt` files are read as **JSON** by `load_json`; see below).
- `add_slug_dir_arg(parser)` (`:19`) adds `--slug-dir`; see [`add_slug_dir_arg`](#add_slug_dir_arg).
- `resolve_slug_dir(value)` (`:31`): `--slug-dir` wins, then `$JOBDIGEST_SLUG_DIR`; if neither, `sys.exit("error: pass --slug-dir DIR or set $JOBDIGEST_SLUG_DIR (no default slug directory)")`; if the path is not a directory, exits `slug directory not found`. There is no default location, so the script cannot run without one of the two.
- `confidence(jobs, notable)` (`:42`): `"high"` if `jobs > 0`, else `"medium"` if notable, else `"low"`.

## `probe_ats_tokens.py`

Module constants: `OUT = <repo>/config/research_ats_boards.yaml`; `SKIP_IDS`/`SKIP_TOKENS` (companies already tracked, e.g. cred, groww, zepto, notion, huggingface); `UA` (a "jobdigest-research" browser-like UA); `INDIA_RE` (India city/state names, broader than the pipeline's); large hand-written dicts `LEVER_NAMES`, `ASHBY_NAMES`, `WORKABLE_NOTABLE` mapping slug -> (display name, note).

Helpers (all small):
- `title_name(slug)` (`:493`) title-cases a slug; `india_hits(blob)` (`:497`) regex test; `lever_locations`, `ashby_locations`, `workable_locations` (`:501`-`:521`) join up to 80 jobs' location strings (workable also appends the shortcode) into one blob; `skipped(id_, token)` (`:648`); `load_json(path)` (`:652`).
- `probe_lever(client, token)` (`:544`): tries `api.lever.co` then `api.eu.lever.co`. 404 -> next host; other non-200 -> remember error (and `rate_limited` for 429); a 200 non-list/invalid JSON -> `None`; a 200 list -> `{"ok": True, "jobs": n, "india": bool, "sample": first text, "api": url, "region": "eu"|"us"}`. If every host 404s -> `None`; if some host errored -> `{"ok": False, "error": ..., "rate_limited": ...}`.
- `_lever_hit(token, hit)` (`:656`): prints and returns `None` for the `ok: False` case, else passes the hit through.
- `probe_ashby(client, token)` (`:587`): GET `api.ashbyhq.com/posting-api/job-board/{token}`; needs a dict with a list under `jobs` or `jobPostings`; returns the hit dict or `None` (all errors collapse to `None`).
- `probe_workable(client, token)` (`:614`): GET `apply.workable.com/api/v1/widget/accounts/{token}?details=true`; on 403/429 sleeps 8 s and retries once; needs a dict with a `jobs` list; also returns `board_name`.

`main(argv=None)` (`:664`):
1. `add_slug_dir_arg`, `resolve_slug_dir`; load the four files as JSON: `lever_slugs`/`ashby_slugs` (sets), `hire` (rows with `source`, `id`, `name`, `sector`), `wk_cands` (rows with `companySlug`, `displayName`, `hq`, `sector`, `description`).
2. Build candidate maps: curated `LEVER_NAMES`/`ASHBY_NAMES`, plus `hire` rows whose `source` is `lever`/`ashby` (`setdefault`, note `hire-signal <sector> company`). Drop skipped tokens; record `in_list` (slug present in the slug list).
3. Workable candidates from `wk_cands` are kept only if India-ish (hq/description), in `WORKABLE_NOTABLE`, or a "productish" sector regex; remaining `WORKABLE_NOTABLE` entries are appended.
4. Probe: Lever and Ashby via a `ThreadPoolExecutor(max_workers=10)` with a shared `httpx.Client(follow_redirects=True, http2=False)`; Workable **sequentially** with `time.sleep(0.35)` between calls (Workable rate-limits).
5. For every non-empty hit build a record `{id, name, ats, token, notes, confidence}`; notes combine the static note, "India locations present on live board", "valid empty board at probe time", the EU host, a sample role (80 chars), and an `evidence:` string naming the source list and the GET URL.
6. Write `OUT` as YAML `{generated, skip_already_in_companies_yaml, counts, companies}` and print counts. Returns 0.
Output location is fixed (`config/research_ats_boards.yaml`); the file is overwritten.

## `probe_ats_tokens_wave2.py`

A second pass on Lever only. It imports `LEVER_NAMES, UA, _lever_hit, probe_lever, skipped` from `probe_ats_tokens` and `confidence` from `_probe_common` (so it must be run from `scripts/` or with that directory on `sys.path`; the import of `probe_ats_tokens` does not require a slug dir). `WAVE2` is a dict of extra slugs. `main()` (`:395`) **requires `config/research_ats_boards.yaml` to exist** (it reads it, built by the first script), skips tokens already skipped, in `LEVER_NAMES`, or already present as `("lever", token)`, probes the rest with 10 threads, builds records the same way, extends `data["companies"]`, sorts by `(ats, name.lower())`, recomputes `counts`, and rewrites the same YAML. It takes no arguments (not even `--slug-dir`).

## Detailed walkthrough

All outputs below were produced offline with a fake `httpx` client (no network). The fakes returned canned `status_code`/`json()` values; nothing here was observed from a live board.

### `add_slug_dir_arg`

**Location** `scripts/_probe_common.py:19` · **Signature** `def add_slug_dir_arg(parser: argparse.ArgumentParser) -> None`
**Purpose** Registers the single `--slug-dir` option on a caller-owned parser, so every research script documents the four expected slug files and the `JOBDIGEST_SLUG_DIR` fallback in the same words.
**Called by** `scripts/probe_ats_tokens.py:main` (the only caller; `probe_ats_tokens_wave2.py` does not use it). **Calls** `parser.add_argument`.
**Behavior** Adds `--slug-dir` with `default=None`; the help text is an f-string built from `LEVER_SLUGS_FILE`, `ASHBY_SLUGS_FILE`, `HIRE_SIGNAL_FILE`, `WORKABLE_CAND_FILE` and `SLUG_DIR_ENV`. It performs no validation: the value is resolved later by [`resolve_slug_dir`](#_probe_commonpy) (`--slug-dir` first, then the env var). Verified: `parse_args([])` gives `Namespace(slug_dir=None)`, `parse_args(["--slug-dir","/x"])` gives `Namespace(slug_dir='/x')`.

### `ashby_locations`

**Location** `scripts/probe_ats_tokens.py:509` · **Signature** `def ashby_locations(jobs: list) -> str`
**Purpose** Flattens the location fields of the first 80 Ashby postings into one string, so `india_hits` can run a single regex over it instead of walking the structure.
**Called by** `probe_ashby` · **Calls** nothing outside builtins.
**Behavior** For each of `jobs[:80]`: append `str(j.get("location") or "")`; then for each entry in `j.get("locations") or []`, append `locationName` (else `name`, else the dict itself via `str(loc)`) when the entry is a dict, otherwise `str(loc)`. Everything is joined with `" | "`. Empty locations contribute empty segments (so the result can end in `| `). Only the first 80 postings are inspected, so a board whose India roles appear later is reported `india: False`.
Verified output for `[{"location":"Bengaluru","locations":[{"locationName":"Remote"},"Pune",{"x":1}]},{}]`:
`Bengaluru | Remote | Pune | {'x': 1} | `

### `workable_locations`

**Location** `scripts/probe_ats_tokens.py:521` · **Signature** `def workable_locations(jobs: list) -> str`
**Purpose** Same idea as `ashby_locations` for Workable's widget payload, where `location` is normally an object.
**Called by** `probe_workable` (which then appends the board `name`) · **Calls** nothing outside builtins.
**Behavior** For each of `jobs[:80]`: if `location` is a dict, join the truthy values of `city`, `region`, `country`, `country_code` with spaces; otherwise append `str(loc)`. A missing location becomes `{}` through `or {}`, which is a dict with no truthy values, so it yields an empty segment. Then always append `str(j.get("shortcode") or "")` as its own segment, so shortcodes end up in the blob (harmless for the India regex, but it is why the blob has twice as many segments as jobs). Verified output for a Pune job with shortcode `AB12` plus a `"Remote"` string-location job with no shortcode:
`Pune MH India IN | AB12 | Remote | `

### `probe_lever`

**Location** `scripts/probe_ats_tokens.py:544` · **Signature** `def probe_lever(client: httpx.Client, token: str) -> dict | None`
**Purpose** Decide whether a Lever board exists for `token` and summarise it. Lever has a US and an EU API host, and a company is only on one of them, so the function tries both.
**Called by** `probe_ats_tokens.main` (inside `run_lever`, wrapped by `_lever_hit`) and `probe_ats_tokens_wave2.main` · **Calls** `client.get`, `lever_locations`, `india_hits`.

**Returns**
| Case | Value |
|---|---|
| 200 with a JSON list on some host | `{"ok": True, "jobs": n, "india": bool, "sample": first posting "text" or "", "api": url, "region": "us"|"eu"}` |
| 200 but body not JSON, or JSON that is not a list | `None` (returns immediately, the other host is not tried) |
| every host 404, no errors | `None` |
| no hit and at least one non-404 failure | `{"ok": False, "error": str, "rate_limited": bool}` |

**Step by step**
1. Build `urls` = `api.lever.co/v0/postings/{token}?mode=json`, then `api.eu.lever.co/...`; `last_err = None`, `rate_limited = False`.
2. For each URL, `client.get(url, timeout=20.0)`. `httpx.HTTPError` is caught: `last_err = str(exc)` and continue to the next host.
3. `404` -> `continue` silently (board simply is not on this host). Any other non-200: `last_err = f"HTTP {r.status_code}"`, and `rate_limited = rate_limited or r.status_code == 429`; continue.
4. On 200, `r.json()`; any exception, or a non-list, returns `None` (the "shape is wrong" case is treated as "no board").
5. Build `loc_blob = lever_locations(data)` (each posting's `categories.location`, else `country`, first 80 postings), `region = "eu" if "eu.lever" in url else "us"`, and return the hit dict.
6. After the loop: `last_err is not None` -> the error dict, else `None`.

```python
if r.status_code == 404:
    continue
if r.status_code != 200:
    last_err = f"HTTP {r.status_code}"
    rate_limited = rate_limited or r.status_code == 429
    continue
```
Verified: a fake where only the EU host serves a list returned `region: 'eu'` after calling the US host first; a 429 on the US host and 404 on EU returned `{'ok': False, 'error': 'HTTP 429', 'rate_limited': True}`; all-404 returned `None`.

**Plain-English summary** "Does this company have a Lever board, and does it hire in India?" with three outcomes: yes (dict), no (`None`), or "couldn't tell" (`ok: False`, so the caller reports it instead of treating it as a miss). Note that a 429 on the first host followed by a 404 on the second still reports the error; the 404 does not clear it.

### `probe_ashby`

**Location** `scripts/probe_ats_tokens.py:587` · **Signature** `def probe_ashby(client: httpx.Client, token: str) -> dict | None`
**Purpose** Same question for Ashby's public posting API.
**Called by** `probe_ats_tokens.main` (`run_ashby`) · **Calls** `client.get`, `ashby_locations`, `india_hits`.
**Step by step** (1) GET `https://api.ashbyhq.com/posting-api/job-board/{token}` with `timeout=20.0`; `httpx.HTTPError` -> `None`. (2) Any status other than 200 -> `None`. (3) `r.json()` failure or a non-dict body -> `None`. (4) `jobs = data.get("jobs") or data.get("jobPostings") or []`; if that is not a list -> `None`. (5) Return `{"ok": True, "jobs": len(jobs), "india": india_hits(ashby_locations(jobs)), "sample": first title or "", "api": url}`.
**Returns** that dict or `None`. Unlike `probe_lever` there is no `ok: False` channel: network errors, 404 and 429 are indistinguishable, so a rate-limited Ashby board is silently dropped. Verified: a fake returning `{"jobs":[{"title":"Eng","location":"Mumbai"}]}` gave `{'ok': True, 'jobs': 1, 'india': True, 'sample': 'Eng', 'api': '.../job-board/x'}`; a JSON array body gave `None`. An empty `jobs` list is a valid hit (`jobs: 0`).

### `probe_workable`

**Location** `scripts/probe_ats_tokens.py:614` · **Signature** `def probe_workable(client: httpx.Client, token: str) -> dict | None`
**Purpose** Same question for Workable's widget API, which is the one most likely to rate-limit.
**Called by** `probe_ats_tokens.main` (sequential Workable loop) · **Calls** `client.get`, `time.sleep`, `workable_locations`, `india_hits`.
**Step by step**
1. GET `https://apply.workable.com/api/v1/widget/accounts/{token}?details=true`, `timeout=25.0`; `httpx.HTTPError` -> `None`.
2. If the status is 403 or 429: `time.sleep(8)` and repeat the GET once (a second `HTTPError` -> `None`). There is no further retry.
3. Status != 200 -> `None`. Non-JSON or non-dict -> `None`. `jobs = data.get("jobs")`; not a list -> `None` (a payload without a `jobs` key is therefore treated as no board).
4. `loc_blob = workable_locations(jobs) + " " + str(data.get("name") or "")`, so a company *named* e.g. "Delhi Foods" counts as India.
5. Return `{"ok": True, "jobs": len(jobs), "india": ..., "sample": first job "title" or "", "api": url, "board_name": data.get("name") or ""}`.
Verified: 200 with `{"name":"W Inc","jobs":[...Delhi...]}` returned `india: True`, `board_name: 'W Inc'`; a payload missing `jobs` returned `None`. `board_name` is returned but `main` never reads it.

### `main` (`probe_ats_tokens.py`)

**Location** `scripts/probe_ats_tokens.py:664` · **Signature** `def main(argv: list[str] | None = None) -> int` (168 lines)
**Purpose** The whole research run: assemble candidate slugs for three ATS vendors, probe each public API, and write `config/research_ats_boards.yaml` for a human to review.
**Called by** the `if __name__ == "__main__": sys.exit(main())` guard. **Calls** `add_slug_dir_arg`, `resolve_slug_dir`, `load_json`, `title_name`, `skipped`, `india_hits`, `probe_lever`/`_lever_hit`, `probe_ashby`, `probe_workable`, `confidence`, `httpx.Client`, `ThreadPoolExecutor`, `yaml.safe_dump`.
**Returns** `0` on completion. It does not catch exceptions; a missing slug file, invalid JSON, or a failed write propagates. `resolve_slug_dir` can `sys.exit` with a message.

**Stage 1: arguments and slug loading**
```python
parser = argparse.ArgumentParser(description=__doc__)
add_slug_dir_arg(parser)
args = parser.parse_args(argv)
slug_dir = resolve_slug_dir(args.slug_dir)
lever_slugs = set(load_json(slug_dir / LEVER_SLUGS_FILE))
```
`argv=None` means `sys.argv[1:]`; passing a list makes the function testable. Four files are read with `load_json` (`json.loads`, despite the `.txt` names): `lever_slugs`/`ashby_slugs` become sets of strings, `hire` and `wk_cands` stay lists of row dicts.

**Stage 2: candidate maps** (all in memory, no network)
- `lever_candidates` is a copy of `LEVER_NAMES` (206 entries in the current file); `ashby_candidates = dict(ASHBY_NAMES)` (149); `WORKABLE_NOTABLE` has 53.
- For each `hire` row: `src` is the lower-cased `source`, `token` the stripped `id` (rows without one are skipped), `name` falls back to `title_name(token)`. `notes = f"hire-signal {row.get('sector') or 'tech'} company"`. `setdefault` adds the row to the Lever or Ashby map only if the slug is not already curated, so curated notes win. Rows from other sources are ignored.
- `lever_probe` / `ashby_probe` are lists of `(token, name, notes, in_list)`; entries where `skipped(token, token)` is true (id or token in `SKIP_IDS`, i.e. companies already in `companies.yaml`) are dropped, and `in_list = token in lever_slugs` (resp. `ashby_slugs`) records whether the slug came from the scraped dump, for the evidence text. Curated slugs missing from the dump are still probed.

**Stage 3: the Workable "India filter" (this is the only place candidates are filtered by relevance)**
For each `wk_cands` row, after dropping empty or skipped slugs:
```python
india = india_hits(hq + " " + desc)
notable = token in WORKABLE_NOTABLE
productish = bool(re.search(r"SaaS|fintech|AI|product|gaming|media|cyber|HR tech|edtech|"
                            r"insurtech|deep tech|marketplace|payments|developer|crypto", sector, re.I))
if not (india or notable or productish):
    continue
```
A candidate survives if it is India-ish by HQ/description, in `WORKABLE_NOTABLE`, or has a product-like sector. Its notes come from `WORKABLE_NOTABLE` (else the sector) with `"; India HQ/ops"` appended when `india`. `in_list` is `True` for these. Remaining `WORKABLE_NOTABLE` entries not seen in the candidates (and not skipped) are appended with `in_list=False`. Note `AI` in that regex is a case-insensitive substring match, so it also matches the "ai" inside "Retail": in my offline run an Oslo-HQ candidate with sector `Retail` was **not** dropped (the probe count was 55 = 53 `WORKABLE_NOTABLE` + the India candidate + that Retail one). Treat the regex as loose.

**Stage 4: thread pools and per-ATS probing**
```python
with httpx.Client(headers=headers, follow_redirects=True, http2=False) as client:
    ...
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = [pool.submit(run_lever, it) for it in lever_probe]
        futs += [pool.submit(run_ashby, it) for it in ashby_probe]
```
One shared client (UA `jobdigest-research`, `Accept: application/json`) serves all threads. `run_lever` wraps `probe_lever` in `_lever_hit` (which prints `lever <token>: rate-limited|error (...)` and turns `ok: False` into `None`); `run_ashby` calls `probe_ashby`. Each worker returns a 6-tuple `(ats, token, name, notes, in_list, hit)`. `as_completed` collects them into `results`, printing progress every 25. Workable then runs **sequentially** in the main thread, `time.sleep(0.35)` after every probe, because the Workable widget API rate-limits (and `probe_workable` itself sleeps 8 s on 403/429). Progress is printed every 15.

**Stage 5: "India filtering" is annotation, not filtering**
Hits are not discarded for lacking India roles. `hit["india"]` only adds the note `India locations present on live board`. The records that survive are those with a non-`None` hit (a valid payload, even with zero jobs).

**Stage 6: building records**
`sorted(results, key=lambda x: (x[0], x[2].lower()))` orders by ATS then display name. For each hit: `cid = re.sub(r"[^a-z0-9-]+", "-", token.lower()).strip("-")`; skip again if `skipped(cid, token)`. `sources` accumulates evidence: the slug-list origin when `in_list` (`Feashliaa/job-board-aggregator slug list`, or `ever-jobs workable-candidates` for Workable), an Ashby curated-list note when `token in ASHBY_NAMES`, and always the `GET <api> → 200 ...` line. `note_parts` = static notes, India note, `valid empty board at probe time` if `jobs == 0`, `EU Lever host api.eu.lever.co` if `hit["region"] == "eu"`, `sample role: <first 80 chars>`, then `evidence: ...`. The record is `{id, name, ats, token, notes, confidence}`; `confidence` is `confidence(jobs, True)`, i.e. `notable` is always `True`, so the result is `"high"` for boards with jobs and `"medium"` for empty ones, never `"low"`. `counts[ats]` is incremented.

**Stage 7: YAML output**
```python
payload = {"generated": "public ATS board research (Lever / Ashby / Workable)",
           "skip_already_in_companies_yaml": sorted(SKIP_IDS),
           "counts": counts, "companies": companies}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
```
`OUT` is `config/research_ats_boards.yaml`, overwritten every run (so wave 2 additions are lost if the first script is re-run). It prints `Wrote <path>` and `counts ... total N`.

**Data flow** slug files + curated dicts -> candidate tuples -> thread pool results -> filtered/sorted records -> YAML.

**Verified example** With a fake client, a 4-file slug directory (`100ms` Lever slug; `newco` Ashby hire-signal row; an India-HQ Workable candidate) and `httpx.Client`, `time.sleep` and `OUT` monkeypatched, the run printed `Probing Lever 205 | Ashby 149 | Workable 55` (curated dicts dominate; `cred` from the hire-signal row was dropped by `SKIP_IDS`) and `counts {'lever': 1, 'ashby': 1, 'workable': 1}`. The Lever record's notes were: `India product (Bengaluru); live jobs.lever.co/100ms; India locations present on live board; sample role: Eng; evidence: Feashliaa/job-board-aggregator slug list; GET https://api.lever.co/v0/postings/100ms?mode=json → 200 JSON array (1 jobs)` with `confidence: high`; the empty Ashby board got `valid empty board at probe time` and `confidence: medium`.

**Plain-English summary** Build a long list of company slugs per ATS (curated, plus whatever the hire-signal file adds), probe every one against its public API with polite concurrency (fast for Lever/Ashby, slow for Workable), keep only the ones that answered with a valid board, label them with evidence, and dump the lot to YAML for manual review.

### `main` (`probe_ats_tokens_wave2.py`)

**Location** `scripts/probe_ats_tokens_wave2.py:395` · **Signature** `def main() -> int`
**Purpose** Extend the YAML written by the first script with a second batch of Lever-only slugs (`WAVE2`) without re-probing the first batch.
**Called by** the `__main__` guard. **Calls** `yaml.safe_load`, `skipped`, `probe_lever`/`_lever_hit` (imported from `probe_ats_tokens`), `confidence`, `httpx.Client`, `ThreadPoolExecutor`, `yaml.safe_dump`.
**Returns** `0`. No error handling: if `OUT` does not exist, `OUT.read_text` raises `FileNotFoundError`; a file without a `companies` key raises `KeyError`.
**Step by step**
1. `data = yaml.safe_load(OUT.read_text(...))`; `existing = {(c["ats"], c["token"]) for c in data["companies"]}`.
2. Build `todo`: for each `WAVE2` entry skip it if `skipped(token, token)`, if it is in `LEVER_NAMES` (already probed by the first script), or if `("lever", token)` is in `existing`. Prints `Wave2 Lever candidates: N`.
3. One `httpx.Client(headers={"User-Agent": UA, "Accept": "application/json"}, follow_redirects=True)` (note: `http2` is not set here, unlike the first script) and a 10-worker pool; each task returns `(token, name, notes, _lever_hit(token, probe_lever(client, token)))`. Results are consumed with `as_completed`, progress every 20.
4. For each non-`None` hit, build the same record as the first script: `cid` slugified, `note_parts = [notes]` plus the India/empty/EU/sample parts, and a fixed evidence string `evidence: GET <api> → 200 JSON array (N jobs); slug in Feashliaa/job-board-aggregator lever_companies.json and/or public jobs.lever.co board`. `confidence(jobs, True)` again. Records are appended to `added`.
5. `data["companies"].extend(added)`, re-sorted by `(ats, name.lower())`; `counts` is recomputed over all companies; the file is rewritten with `yaml.safe_dump(..., sort_keys=False, allow_unicode=True, width=100)`; prints `added N counts {...}`.
**Oddity** the evidence string claims the slug is in the Feashliaa list without checking it, unlike the first script's `in_list` logic.

## Failure modes and oddities
- Slug lists are `.txt` files but parsed as JSON; a plain-text list would raise `json.JSONDecodeError`.
- Boards that 404 are silently omitted, so absence is not proof that a company has no board.
- The scripts hit real third-party APIs; the UA claims `+https://github.com/jobdigest`, which is a placeholder URL.
- The evidence string for Ashby says `GET ... -> 200 JSON jobs`; counts include empty boards (`jobs == 0`) marked "valid empty board at probe time".

## How to run (reference)
```
python scripts/probe_ats_tokens.py --slug-dir /path/to/slugs      # or export JOBDIGEST_SLUG_DIR
python scripts/probe_ats_tokens_wave2.py                          # after the first one
```
Review `config/research_ats_boards.yaml` by hand before copying entries into `companies.yaml`.
