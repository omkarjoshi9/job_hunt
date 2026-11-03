# 6. Adding features without breaking things

Recipes for the changes contributors make most often. Each recipe lists the files to touch, in order, and the tests that
protect you. Run `python -m pytest -q` after each step; the suite takes about 30 seconds and is fully offline.

## General rules

1. **Find the stage first.** Decide whether the change belongs in a source, the filter, scoring, selection, delivery or the bot
   ([repo tour](02-repo-tour.md)).
2. **Keep external calls injectable.** If new code calls the network, an LLM or Telegram, accept the callable as a parameter with a
   real default so tests can pass a fake (see [testing strategy](../testing/strategy.md)).
3. **No test-only branches in production code.** Use a fixture or a parameter instead.
4. **Add a test for the behaviour you change**, and run the end-to-end tests (`tests/test_pipeline_e2e.py`) because they exercise
   the real stage wiring.
5. **Update the docs page** of the module you changed (this repo keeps a function-level page per module) and re-run
   `python docs/_tools/build_reference.py` and `python docs/_tools/check_docs.py`.

## Recipe: add a new ATS vendor

1. Create `src/jobdigest/ats/<vendor>.py` with a class implementing `BoardAdapter` (`ats = "<vendor>"`, `fetch(CompanyConfig) -> FetchResult`).
   Use `get_json_page` and `JobCollector` from `ats/base.py`; return `complete=False` when pagination fails midway so the tombstone
   sweep is skipped. Model it on [ats-ashby](../modules/sources/ats-ashby.md) (single page) or
   [ats-greenhouse](../modules/sources/ats-greenhouse.md) (paginated).
2. Add the vendor to `KNOWN_ATS` in `ats/base.py`.
3. Add a branch in `ats.get_adapter` **before** the final line - the function ends with an unconditional `return WorkableAdapter(...)`,
   so forgetting the branch silently selects the wrong adapter.
4. Add the vendor's URL to `collect._board_url`.
5. Add fixtures under `tests/fixtures/ats/<vendor>/` (`two_jobs.json`, `empty.json`, `not_found.json`) and `tests/test_ats_<vendor>.py`;
   extend `tests/test_ats_registry.py`.
6. If the vendor has a public token list, teach `expand.py` (`_SUPPORTED`, `_url`, `_jobs`) about it.

## Recipe: change a filter rule

1. Edit `src/jobdigest/hard_filter.py`. Keep the gate **order** in mind (see the decision flow in
   [hard-filter](../modules/filtering/hard-filter.md)); a new rule that returns a new reason string should be added to the page's reason table.
2. Parametrise a case in `tests/test_hard_filter.py` (and `tests/test_fit.py` has regression titles from real digests).
3. Remember the filter is applied in `upsert_job`, so existing rows only change when seen again; re-run `collect`.
4. If the rule is a screen on titles, check `expand.promising`, which reuses `function_ok` and `seniority_title`.

## Recipe: change how jobs are ranked

* Deterministic points: `fit.py` (`role_points`, `level_points`, `skill_points`, `trust_points`, `freshness_points`); the weights add to 100.
* Blend and selection: `fit.quality_score` (65/35), `rank.blend_rank`, `rank.select_diverse` (quality floor, age, caps, cooldown).
* Tests: `tests/test_fit.py` (including identical LLM scores ranking differently), `tests/test_rank.py`, `tests/test_urgent.py`.
* Changing `FIT_WEIGHT`/`LLM_WEIGHT` or point tables changes every card score; check `digest_min_quality` still makes sense.

## Recipe: add a profile setting

1. Add the field **with a default** to `Profile` in `config.py` and read it in `load_profile` (use `_int_field` for ints so a YAML
   null does not crash).
2. Document it in `config/profile.example.yaml`. Production profiles live in a GitHub secret, so a default is mandatory:
   an old secret must keep working.
3. Use it where needed and add a test that sets it via `dataclasses.replace(load_profile(), your_field=...)`.

## Recipe: add a Telegram button

1. Pick a new one-letter kind and extend `_CB` in `bot.py` (`^(o|s|a|h|w|m):(\d+)$`).
2. Add the button to `telegram.action_markup` (callback data must stay within 64 bytes: `<kind>:<job_id>`).
3. Handle it in `bot._handle_callback` (or `_handle_slow_action` for LLM/network work). **Every path must answer the tap and leave a
   visible message** - use `_send_plain` for refusals and errors.
4. Add the kind to `KINDS` in `tests/test_bot_buttons.py`; the parametrised contract tests then check the happy path, a missing job,
   a crashing handler and an unreachable database.

## Recipe: add a database column

1. Add it to `ADDED_COLUMNS` in `db/schema.py` (this one list both builds `CREATE TABLE` and migrates old databases).
2. If `upsert_job` writes it, add it to `_JobRow`, `_build_job_row` and `_SHARED_COLUMNS` in `db/jobs.py`.
3. For an index, add it to the schema's late-index list so it is created **after** the column exists on old databases.
4. Test with a database created by the previous schema (see `tests/test_db_internals.py`).

## Recipe: add a pipeline stage

1. Write `src/jobdigest/<stage>.py` with `run(*, db_path=None, ...) -> int`; record a `runs` row with `insert_run`/`finish_run`.
2. Add a subparser and a lazy-importing `_cmd_<stage>` in `cli.py`.
3. Add a step to `.github/workflows/pipeline.yml` (and to `start.sh` if it belongs in the local flow).
4. Cover it in `tests/test_pipeline_e2e.py` and check `tests/test_workflows.py` constraints still hold.

## Things that break when you are not looking

* **README wording is pinned by tests** (`tests/test_scaffold_constraints.py`); edit README prose carefully and run that file.
* **Workflow text is pinned by `tests/test_workflows.py`** (for example the digest cron string and forbidden commands).
* **`DEFAULT_CRON`, secret names and config keys** are referenced by both code and workflows.
* **Never commit real secrets or personal config.** `config/profile.yaml`, `companies.yaml` and `.env` are git-ignored on purpose.

Next: [7. Testing guide](07-testing-guide.md).
