# 7. Testing guide

A practical how-to. For the design rationale and a file-by-file list see [testing strategy](../testing/strategy.md),
[test map](../testing/test-map.md) and [end-to-end tests and fixtures](../testing/e2e-and-fixtures.md).

## Running tests

```bash
python -m pytest -q                              # everything (about 30 s, offline)
python -m pytest tests/test_hard_filter.py -q    # one file
python -m pytest -k "tombstone and empty" -q     # by keyword
python -m pytest tests/test_bot_buttons.py -x -q # stop at the first failure
python -m pytest --lf -q                         # only what failed last time
```

`pyproject.toml` sets `testpaths = ["tests"]` and `pythonpath = ["src"]`. One test (`test_marketplaces_bridge.py`) is skipped when
`pandas` is not installed.

## The rules the suite enforces

* **Nothing touches the network.** `tests/conftest.py` has an autouse fixture that clears Telegram credentials, sets
  `JOBDIGEST_DISCOVERED_BOARDS=off`, and replaces the real Telegram POST with a function that raises, so a test that forgets to inject a
  fake fails loudly instead of sending a message.
* **Tests use the example config.** `load_profile()` falls back to `config/profile.example.yaml` because `profile.yaml` is not tracked.
  If you create a local `config/profile.yaml`, some tests will behave differently; set `JOBDIGEST_CONFIG_DIR` to an empty temp dir if in doubt.
* **Constraint tests guard the repository itself:** `test_scaffold_constraints.py` (dependencies, README wording), `test_no_stealthy.py`
  (where scraping classes may appear), `test_workflows.py` (workflow contents). Read them before editing those areas.

## Writing a unit test

Most logic is pure; pass plain dicts or `dataclasses.replace(load_profile(), ...)` and assert on the return value:

```python
from dataclasses import replace
from jobdigest.config import load_profile
from jobdigest.hard_filter import hard_filter

def test_zero_to_one_years_is_entry_level():
    status, reason = hard_filter(
        load_profile(), title="Software Engineer", location_raw="Pune, India",
        description_text="0-1 years of experience.", company="Acme")
    assert (status, reason) == ("pass", "ok")
```

## Writing a database test

Use `tmp_path` for a throwaway SQLite file and the real `connect`:

```python
from jobdigest.config import load_profile
from jobdigest.db import connect, upsert_job, upsert_score
profile = load_profile()
conn = connect(tmp_path / "jobs.db")          # schema is created and migrated automatically
action, job_id = upsert_job(conn, profile, apply_url=..., hosted_url=..., source="greenhouse",
                            external_id="1", company_id=None, company="Acme", title="...",
                            location_raw="Pune", description_text="...", raw_json="{}", ats="greenhouse")
```

See `tests/test_db_upsert.py`, `tests/test_tombstone.py` and `tests/test_digest.py` for patterns (helper functions named `_pass_job`,
`_score`, `_collect_ok`).

## Fakes you can reuse

| Need | Fake | Where |
|---|---|---|
| ATS HTTP | `httpx.MockTransport` handler; `Board` class | `tests/test_pipeline_e2e.py` |
| LLM | a function `(messages, *, model, api_key, json_mode=False, **kw) -> str` returning schema JSON; `fake_llm` | `tests/test_pipeline_e2e.py`, `tests/test_fit.py` |
| Telegram | `http_post(url, json=None, timeout=None)` recording calls; `FakeTelegram` | `tests/test_pipeline_e2e.py`, `tests/test_bot_buttons.py`, `tests/test_bot.py` (`_OkPost`) |
| Google Sheet | `FakeSheet` | `tests/test_sheet.py` |
| Bot taps | `_tap(kind, job_id)`, `_mail_setup(...)` | `tests/test_bot_buttons.py`, `tests/test_bot.py` |

The LLM prompt marks each job as `<<<JOB id=N>>>`; fakes find ids with `re.findall(r"<<<JOB id=(\d+)>>>", messages[-1]["content"])`.

## Fixtures for parsers

`tests/fixtures/` holds captured payloads: `ats/<vendor>/*.json`, `portals/*.html`, `feeds/*.xml`, `contacts/*`, `discover/*`. A new parser test
loads a fixture, calls the parser, and asserts on the resulting `NormalizedJob` list. Capture a real response once, strip secrets and
personal data, and commit it.

## Testing time-dependent code

Pass `now` explicitly where the function accepts it (`select_diverse(..., now=NOW)`, `guest_rows(..., now=...)`, `quality_score(row, profile, now)`); tests use a fixed
`NOW`. Where a function reads the clock itself (`db.stamp_notified`), set the column directly with SQL in the test instead of patching time.

## Before you push

1. `python -m pytest -q` is green. 2. `python docs/_tools/check_docs.py` (add `--mermaid-dir <dir with mermaid+jsdom installed>` to validate
diagrams) reports no errors. 3. New behaviour has a test and a documentation entry.
