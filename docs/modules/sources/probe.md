# probe: manual board health check CLI

- Source: `src/jobdigest/probe.py` (101 lines). Layer: maintainer tooling on top of the [ATS adapters](ats-overview.md).
- Depends on: `jobdigest.ats.get_adapter`, `jobdigest.ats.base` (`KNOWN_ATS`, `CompanyConfig`), `jobdigest.ats.http_client.make_client`, `jobdigest.config.load_companies` ([config](../foundation/config.md)). Used by: humans (`python -m jobdigest.probe`, see README "probe" examples). Nothing in the pipeline imports it.
- Tests: `tests/test_probe.py` (`test_probe_mock_status_and_does_not_write_yaml`, `test_all_seed_companies_disabled`, `test_probe_ids_filters_rows`, `test_probe_ids_repeatable`, `test_probe_enabled_only`, `test_probe_smartrecruiters_is_not_unimplemented`, `test_probe_bamboohr_is_not_unimplemented`, `test_probe_ids_and_enabled_only_intersect`).

## Why this exists

Before enabling a company in `companies.yaml` you want to know whether its token is alive and how many jobs the board returns. `probe` runs each configured company through the real adapter once and prints one tab-separated line per company. It is read-only: it never writes the YAML, never touches the database, and never flips `enabled`. Live HTTP use is for maintainers; the CI test suite uses mocked transports (README: "Live HTTP is for maintainers via `python -m jobdigest.probe`").

## Public interface

| Symbol | Signature | Purpose |
|---|---|---|
| `main` | `main(argv=None, client=None, sleep=None) -> int` | CLI entry; always returns 0 unless an exception escapes |
| `_build_parser` | `() -> argparse.ArgumentParser` | defines `--ids`, `--enabled-only` |
| `_parse_ids` | `(raw: list[str] \| None) -> set[str] \| None` | flatten repeatable/comma-separated ids |

CLI: `python -m jobdigest.probe [--ids ID[,ID...]]... [--enabled-only]`. `--ids` is `action="append"` (repeatable and each value may be comma-separated); `--enabled-only` is a flag.

## Detailed walkthrough

### `_parse_ids`
**Location** `probe.py:15` · `def _parse_ids(raw: list[str] | None) -> set[str] | None`. `None` or empty list -> `None` (no filter). Otherwise for each chunk, split on `,`, strip, drop empties, add to a set. An empty resulting set also returns `None` (e.g. `--ids ","` means no filter, not "match nothing"). Example: `["a,b", "c"] -> {"a","b","c"}`. Called by `main`.

### `_build_parser`
`probe.py:27`. Builds `ArgumentParser(prog="python -m jobdigest.probe")` with the two options described above.

### `main`
**Location** `src/jobdigest/probe.py:44` · **Signature** `def main(argv: Sequence[str] | None = None, client: httpx.Client | None = None, sleep: Callable[[float], None] | None = None) -> int`
**Purpose** Drive one fetch per selected company and print its outcome. `client` and `sleep` are injectable so tests supply a `MockTransport` client and a no-op sleep.
**Called by** the `__main__` guard (`raise SystemExit(main())`) and tests · **Calls** `_build_parser().parse_args`, `_parse_ids`, `make_client`, `load_companies`, `get_adapter`, `adapter.fetch`.

**Step-by-step**
1. Parse args (`list(argv)` if given, else `sys.argv`). `want_ids = _parse_ids(args.ids)`; `enabled_only = bool(args.enabled_only)`.
2. `own_client = client is None`; create `make_client()` if none; default `sleep = time.sleep`. The client is closed in a `finally` only if `main` created it.
3. `payload = load_companies()` reads `companies.yaml` from `JOBDIGEST_CONFIG_DIR` or `config/`, falling back to `companies.example.yaml` with a warning. `rows = payload.get("companies")`; if the payload is not a dict or `companies` not a list: `return 0` with no output. A missing config file raises `FileNotFoundError` (not caught here).
4. For each `row` (non-dict rows skipped):
   - `company_id = str(row.get("id") or "")`; skip if `want_ids` is set and the id is not in it (exact, case-sensitive match).
   - skip if `enabled_only` and `not bool(row.get("enabled", False))`. The two filters intersect (`test_probe_ids_and_enabled_only_intersect`). Without `--enabled-only`, disabled companies **are** probed (the seed file has everything disabled; `test_all_seed_companies_disabled`).
   - `ats = str(row.get("ats") or "")`, `token = str(row.get("token") or "")`. If `ats.strip().lower()` is not in `KNOWN_ATS`, print `"{id}\t{ats}\tunimplemented"` and continue (e.g. `peakxv\tgetro\tunimplemented`). It never reports `empty_board` for unsupported ATS.
   - Build `CompanyConfig(id, name, ats, token, enabled, notes)` and `adapter = get_adapter(company.ats, client, sleep=sleep)`.
   - `result = adapter.fetch(company)` in `try`; `except httpx.HTTPError` prints `"{id}\t{ats}\t{token}\t\terror\t0\t{exc}"` and continues. (Adapters already catch `httpx.HTTPError` and return `ERROR`, so this is a second safety net.)
   - Otherwise `http_status = "" if result.http_status is None else result.http_status` and print `"{id}\t{ats}\t{token}\t{http_status}\t{result.status.value}\t{len(result.jobs)}"`.
5. `finally`: close the client if owned. `return 0`.

**Output format** (from the code and `test_probe.py` assertions):

| Case | Line |
|---|---|
| fetched | `id<TAB>ats<TAB>token<TAB>http_status<TAB>status<TAB>job_count` |
| unsupported ATS | `id<TAB>ats<TAB>unimplemented` |
| transport exception | `id<TAB>ats<TAB>token<TAB><TAB>error<TAB>0<TAB>message` |

`status` is one of `ok`, `empty_board`, `dead_token`, `error`; `http_status` is empty when no response arrived. Example lines against the mock client in the tests: `deadco ... 404 dead_token 0`, `okco ... 200 ok 2`.

Notes: probe prints only `ok`/count; it does not show `complete` or the `error` text of an `ERROR` result. The exit code is always 0 for any per-company outcome. It reads only the curated `companies.yaml`, not `data/boards_india.tsv` discovered boards. Requests are sequential, with the usual jitter and retries from `request_get`.

## Failure modes and edge cases

- Missing/invalid config handled as described in step 3.
- `get_adapter` cannot raise `ValueError` here because `KNOWN_ATS` is checked first.
- Test safety: `test_probe_mock_status_and_does_not_write_yaml` asserts the tmp YAML and the repo example YAML are byte-identical afterwards and exactly 4 HTTP calls occur (unimplemented rows make none).

## How to modify safely

Keep it side-effect free. If you add output columns, update the tab-split assertions in `tests/test_probe.py` (they check `line.split("\t")[-1]` and `startswith` prefixes). New ATS adapters are probed automatically once added to `KNOWN_ATS` and `get_adapter`.
