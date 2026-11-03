# config: YAML loading, `Profile` and `Models`

- Source: `src/jobdigest/config.py` (218 lines); also `src/jobdigest/paths.py` (7 lines, see [paths-envfile-logutil.md](paths-envfile-logutil.md)).
- Layer: foundation. Every pipeline stage that needs user settings imports from here.
- Depends on: `yaml` (PyYAML), `jobdigest.paths.repo_root`, stdlib `os`, `logging`, `dataclasses`, `pathlib`.
- Used by: `collect.py` (`load_companies`, `load_portals`, `load_feeds`, `load_marketplaces`, `load_discovered_boards`), `score.py`, `digest.py`, `bot.py`, `sheet.py` (`load_profile`, `load_models`), `expand.py` (`load_profile`), `probe.py` and `discover.py` (`load_companies`, `config_dir`), plus every module that takes a `Profile` as a parameter (`hard_filter.py`, `fit.py`, `rank.py`, `blurb.py`, `email_draft.py`, `db/digest_queries.py`).
- Tests: `tests/test_config.py`, `tests/test_dual_hash.py` (uses `load_profile`/`load_models`), `tests/test_probe.py` (sets `JOBDIGEST_CONFIG_DIR`), `tests/test_pipeline_e2e.py::test_collect_includes_discovered_boards_without_duplicating_curated`, `tests/conftest.py` (forces `JOBDIGEST_DISCOVERED_BOARDS=off`).

## Why this exists / responsibility

All behaviour that differs between users lives in YAML files under `config/` (profile, company list, portals, feeds, marketplaces, model ids). `config.py` is the only place that knows where those files are, how to read them, and how to turn the two structured ones (`profile.yaml`, `models.yaml`) into typed, immutable dataclasses. The other three YAMLs (`companies`, `portals`, `feeds`, `marketplaces`) are returned as plain `dict`s because their shape is interpreted by their consumers (`collect.py`).

Private config is git-ignored (`.gitignore` contains `config/*.yaml`; the `*.example.yaml` files are committed via the negation rules in that file). That produces the central design decision: **if `config/<name>.yaml` is missing, fall back to `config/<name>.example.yaml` and log a warning**. A fresh clone, a CI job, or the test-suite therefore runs without any private file.

In the working tree at documentation time `config/` contains `companies.example.yaml`, `feeds.example.yaml`, `feeds.yaml`, `marketplaces.example.yaml`, `models.example.yaml`, `models.yaml`, `portals.example.yaml`, `portals.yaml`, `profile.example.yaml`. There is no `profile.yaml`, `companies.yaml`, or `marketplaces.yaml`, so `load_profile()` currently logs the fallback warning (observed: `.../config/profile.yaml not found; falling back to example config .../config/profile.example.yaml`). In the GitHub pipeline the private files are written from secrets by a "Write private config" step (see `../../infra/github-workflows.md`, `../../infra/config-files.md`).

## Public interface at a glance

| Symbol | Signature | Purpose |
|---|---|---|
| `config_dir` | `def config_dir() -> Path` | Directory holding the YAML files (`JOBDIGEST_CONFIG_DIR` or `<repo>/config`). |
| `_yaml_path` | `def _yaml_path(name: str) -> Path` | Resolve `name`, falling back to `<stem>.example.yaml` with a warning. |
| `_read_yaml` | `def _read_yaml(name: str) -> Any` | `yaml.safe_load` the resolved file; empty file is an error. |
| `Profile` | frozen dataclass, 29 fields | The user's profile and digest knobs. |
| `Models` | frozen dataclass, 7 fields | LLM model ids and prompt versions. |
| `_int_field` | `def _int_field(data: dict, key: str, default: int) -> int` | Optional int with default; treats missing, `None`, `""` as absent. |
| `load_profile` | `def load_profile() -> Profile` | Build `Profile` from `profile.yaml`. |
| `load_companies` / `load_portals` / `load_feeds` / `load_marketplaces` | `-> dict[str, Any]` | Raw YAML payloads. |
| `discovered_boards_path` | `def discovered_boards_path() -> Path \| None` | Path of the TSV written by `expand`, or `None` when disabled. |
| `load_discovered_boards` | `def load_discovered_boards(path: Path \| None = None) -> list[dict[str, Any]]` | TSV rows converted to company-row dicts. |
| `load_models` | `def load_models() -> Models` | Build `Models` from `models.yaml` with free-model guards. |
| `load_all` | `def load_all() -> dict[str, Any]` | All six configs in one dict. No caller found in `src/` or `tests/`. |

## Key concepts

**Frozen dataclass.** `@dataclass(frozen=True)` makes instances immutable and hashable; assigning to a field raises `FrozenInstanceError`. Profile/Models are loaded once per command and then passed around by value, so nothing downstream can mutate shared settings.

**No caching.** Each `load_*` call re-reads and re-parses the file. Callers load once at the top of their `run()` and pass the object down.

**Required vs optional keys.** `load_profile` indexes `d["key"]` for the 19 required fields, so a missing key raises `KeyError` naming the key. Optional keys use `d.get(...)` or `_int_field` with a default.

## Detailed walkthrough

### `config_dir`
**Location** `src/jobdigest/config.py:16` · **Signature** `def config_dir() -> Path`
**Purpose** Single source of truth for where YAML lives.
**Called by** `_yaml_path`; `discover.py` (lines ~392-394 compares a destination directory with it, `_config_dir_override` at ~499 temporarily sets the env var). **Calls** `os.environ.get`, `Path.expanduser/resolve`.
**Steps**
1. Read `JOBDIGEST_CONFIG_DIR`. If non-empty, return `Path(override).expanduser().resolve()` (so `~` and relative paths work).
2. Otherwise return `Path(__file__).resolve().parents[2] / "config"`. `parents[2]` of `src/jobdigest/config.py` is the repo root, so this is `<repo>/config`. (It duplicates `paths.repo_root()` instead of calling it.)
The directory is not checked for existence here.

### `_yaml_path`
**Location** `src/jobdigest/config.py:23` · **Signature** `def _yaml_path(name: str) -> Path`
**Purpose** Implements the example fallback.
**Called by** `_read_yaml`. **Calls** `config_dir`, `log.warning`.
**Steps**
1. `path = config_dir() / name`; `if path.is_file(): return path`.
2. Otherwise `example = config_dir() / f"{Path(name).stem}.example.yaml"`. For `profile.yaml` the stem is `profile`, so `profile.example.yaml`.
3. If the example is a file, log `"%s not found; falling back to example config %s"` at WARNING on logger `jobdigest.config` and return it.
4. Else `raise FileNotFoundError(path)` (the path of the *primary* file).
The warning goes to stderr once logging is configured (see [paths-envfile-logutil.md](paths-envfile-logutil.md#configure)); before `configure()` runs, Python's last-resort handler prints WARNING and above to stderr as well. Covered by `test_example_fallback_logs_warning`.

### `_read_yaml`
**Location** `src/jobdigest/config.py:34` · **Signature** `def _read_yaml(name: str) -> Any`
1. `path = _yaml_path(name)`.
2. Open UTF-8 and `yaml.safe_load` (safe loader: no arbitrary object construction).
3. `data is None` (empty file, or only comments) raises `ValueError(f"empty yaml: {path}")`; otherwise returns the parsed value, whatever type it is (not validated to be a mapping).
YAML syntax errors propagate as `yaml.YAMLError`.

### `Profile`
**Location** `src/jobdigest/config.py:44` · frozen dataclass.
Field table (consumer column lists real usages found by grep; "-" means no use found outside `config.py`).

| Field | Type | Default | Required in YAML | Meaning / consumers |
|---|---|---|---|---|
| `name` | `str` | none | yes | Candidate name; `blurb.py`, `email_draft.py`. |
| `college` | `str` | none | yes | `blurb.py`. |
| `degree` | `str` | none | yes | `blurb.py`. |
| `batch` | `int` | none | yes | Graduation year; `score.py`, `blurb.py`, `email_draft.py`. |
| `cgpa` | `float` | none | yes | `blurb.py`. |
| `location` | `str` | none | yes | Home city; `score.py`, `blurb.py`, `db/digest_queries.py`. |
| `relocate` | `list[str]` | none | yes | Cities willing to relocate to; `score.py`, `db/digest_queries.py`. |
| `work_auth` | `str` | none | yes | Work authorization; `score.py`, `blurb.py`. |
| `role_types` | `list[str]` | none | yes | e.g. `intern`, `new-grad`; `score.py`, `blurb.py`. |
| `skills` | `list[str]` | none | yes | `score.py`, `blurb.py`, `email_draft.py`. |
| `internships` | `list[str]` | none | yes | Prior experience; `score.py`, `bot.py`, `blurb.py`. |
| `india_keywords` | `list[str]` | none | yes | Location tokens treated as India; `hard_filter.py`, `expand.py`. |
| `title_allow_intern` | `str` (regex) | none | yes | `hard_filter.py`. |
| `title_allow_newgrad` | `str` (regex) | none | yes | `hard_filter.py`. |
| `title_reject_regex` | `str` (regex) | none | yes | `hard_filter.py`, `score.py`. |
| `reject_remote_us` | `bool` | none | yes | `hard_filter.py`. |
| `min_yoe_reject` | `int` | none | yes | Years-of-experience cut-off; `hard_filter.py`, `score.py`. |
| `timezone` | `str` | none | yes | Loaded and stored, but no code in `src/` reads `profile.timezone` (grep for `.timezone` finds only `config.py:113`). |
| `digest_max` | `int` | none | yes | Digest size; `digest.py`, `sheet.py`. |
| `watchlist` | `list[str]` | none (dataclass has no default) | no: `[]` when absent/empty | Companies that trigger urgent cards; `fit.py`, `digest.py`. |
| `urgent_score_min` | `int` | none in dataclass; `85` in loader | no | Threshold for urgent push; `digest.py`. |
| `urgent_daily_cap` | `int` | none in dataclass; `3` in loader | no | Max urgent cards per day; `digest.py`. |
| `github_url` | `str` | `""` | no | `str(d.get("github_url") or "")`. |
| `top_companies` | `list[str]` | `[]` (`field(default_factory=list)`) | no | `hard_filter.py`, `fit.py`. |
| `digest_min_quality` | `float` | `50.0` | no | Quality floor in `rank.py`. |
| `digest_per_company` | `int` | `1` | no | Per-company cap in `rank.py`. |
| `digest_per_portal` | `int` | `2` | no | Per-portal cap in `rank.py`. |
| `digest_company_cooldown_days` | `int` | `3` | no | Passed to `db/digest_queries.py`. |
| `digest_max_age_days` | `int` | `21` | no | Age limit in `rank.py`. |

Dataclass-level note: fields without defaults must come before fields with defaults, so `watchlist`, `urgent_score_min`, `urgent_daily_cap` have no dataclass default even though the loader supplies one. Constructing `Profile(...)` directly (no code in `src/` or `tests/` does so; only `load_profile` does) therefore requires those three explicitly; the 7 trailing fields can be omitted.

### `Models`
**Location** `src/jobdigest/config.py:77`

| Field | Type | Default | Meaning |
|---|---|---|---|
| `score_model` | `str` | required | OpenRouter id for the scoring LLM. |
| `summarize_model` | `str` | required | OpenRouter id for summaries. |
| `score_prompt_version` | `str` | required | Version tag stored with scores (see `../ranking/score-llm.md`). |
| `summarize_prompt_version` | `str` | required | Same for summaries. |
| `batch_size` | `int` | required | Jobs per scoring request. |
| `blurb_model` | `str \| None` | `None` | Model for blurbs/drafts; `None` disables blurb features (bot answers "blurb model not configured"). |
| `blurb_prompt_version` | `str \| None` | `None` | Paired with `blurb_model`. |

### `_int_field`
**Location** `src/jobdigest/config.py:87`. Returns `default` if the key is absent, the value is `None`, or `== ""`; otherwise `int(data[key])`. This lets a YAML line like `urgent_score_min:` (null) behave as "use default". `int()` on a non-numeric string raises `ValueError`. Note `digest_min_quality` does not use it: `float(d.get("digest_min_quality", 50.0))` so an explicit null would raise `TypeError` (`float(None)`).

### `load_profile`
**Location** `src/jobdigest/config.py:93` · **Signature** `def load_profile() -> Profile`
**Called by** `collect.py:537` (inside `run`), `expand.py:202`, `score.py:312`, `sheet.py:364`, `bot.py:142` and `:246`, `digest.py:366`, `load_all`, tests. **Calls** `_read_yaml("profile.yaml")`, `_int_field`.
**Steps**
1. `d = _read_yaml("profile.yaml")` (fallback to `profile.example.yaml` if needed).
2. Build `Profile(...)` with coercions: `str(...)`, `int(...)`, `float(...)`, `list(...)`, `bool(...)`. Required keys use `d["key"]`, so a missing key raises `KeyError('key')`. `list(d["relocate"])` on a string would split it into characters, so lists must be YAML lists.
3. Optional keys:
   - `watchlist=[str(x) for x in (d.get("watchlist") or [])]`, same pattern for `top_companies`;
   - `urgent_score_min`, `urgent_daily_cap`, `digest_per_company`, `digest_company_cooldown_days`, `digest_max_age_days`, `digest_per_portal` through `_int_field`;
   - `digest_min_quality=float(d.get("digest_min_quality", 50.0))`;
   - `github_url=str(d.get("github_url") or "")`.
Quirk: `bool(d["reject_remote_us"])` of the YAML string `"false"` would be `True`; write an unquoted YAML boolean.
Verified output with the example profile: `p.name == "Example"`, `p.digest_per_portal == 2`, `p.digest_min_quality == 50.0`.
Tests: `test_profile_filter_types`, `test_load_profile_without_github_url_key`, `test_profile_has_no_digest_hour`.

### `load_companies`, `load_portals`, `load_feeds`, `load_marketplaces`
**Location** `config.py:128`, `:169`, `:173`, `:177`. One-liners: `_read_yaml("companies.yaml")` etc. Return the parsed mapping untouched; shapes are interpreted in `collect.py` (see [../collection/collect-orchestrator.md](../collection/collect-orchestrator.md)). `test_load_marketplaces_from_config_dir` and `test_example_fallback_logs_warning` cover marketplaces/feeds.

### `discovered_boards_path`
**Location** `src/jobdigest/config.py:132` · **Signature** `def discovered_boards_path() -> Path | None`
1. `raw = (os.environ.get("JOBDIGEST_DISCOVERED_BOARDS") or "").strip()`.
2. If `raw.lower()` is one of `off`, `none`, `0`, `false` return `None` (feature disabled).
3. Else `Path(raw)` if `raw` is non-empty, otherwise `repo_root() / "data" / "boards_india.tsv"`.
`tests/conftest.py` sets the variable to `off` for every test; the e2e test sets it to a temp TSV to opt in. Relative override paths are used as-is (relative to the current working directory, not the repo).

### `load_discovered_boards`
**Location** `src/jobdigest/config.py:143` · **Signature** `def load_discovered_boards(path: Path | None = None) -> list[dict[str, Any]]`
**Purpose** Convert the TSV written by `python -m jobdigest.expand` (columns `company`, `ats`, `token`, `candidates`, `sample`; see [../sources/expand.md](../sources/expand.md)) into rows shaped like entries of `companies.yaml`.
**Called by** `collect.run` (`collect.py:543`, merged via `_with_discovered`). **Calls** `discovered_boards_path`.
**Steps**
1. `path = path or discovered_boards_path()`. If `None` or not a file, return `[]`.
2. Iterate `enumerate(text.splitlines())`, splitting each line on tab.
3. Skip the header: `if i == 0 and parts[:1] == ["company"]`.
4. Skip short or token-less lines: `len(parts) < 3 or not parts[2].strip()`.
5. Build `name, ats, token = parts[0].strip(), parts[1].strip().lower(), parts[2].strip()` and append:
```python
{"id": f"{ats}:{token}", "name": name or token, "ats": ats, "token": token,
 "enabled": True, "notes": "discovered by jobdigest.expand"}
```
Real output from the repo's `data/boards_india.tsv` (222 rows): `{'id': 'greenhouse:point72', 'name': 'Point72', 'ats': 'greenhouse', 'token': 'point72', 'enabled': True, 'notes': 'discovered by jobdigest.expand'}`. No ATS-name validation happens here; an unknown `ats` is passed through for `collect.py` to handle.

### `load_models`
**Location** `src/jobdigest/config.py:181` · **Signature** `def load_models() -> Models`
1. `d = _read_yaml("models.yaml")`; `score = d["score"]`, `summarize = d["summarize"]` (`KeyError` if missing).
2. `score_model`/`summarize_model` = `str(...["model"])`. If either `.endswith(":free")` raise `ValueError("models.yaml must not use OpenRouter :free ids")`. Rationale: free OpenRouter variants are rate-limited and unreliable for a pipeline that must finish daily.
3. `blurb = d.get("blurb") or {}`. If truthy: `bm = str(blurb["model"])`; reject `:free` and the literal `openrouter/auto` (`ValueError("blurb model must not be :free or openrouter/auto")`); set `blurb_model`, `blurb_prompt_version = str(blurb["prompt_version"])`. If falsy both are `None`.
4. `batch_size=int(score.get("batch_size", d.get("batch_size", 12)))`: prefers `score.batch_size`, then legacy top-level `batch_size`, then 12 (`test_models_batch_size_under_score_and_legacy_top_level`).
The `openrouter/auto` ban applies only to the blurb model, not to score/summarize. Tests: `test_models_both_keys_not_free`, `test_load_models_without_blurb_block`, `test_example_blurb_model_is_paid_and_distinct_from_score`.

### `load_all`
**Location** `src/jobdigest/config.py:210`. Returns `{"profile", "companies", "portals", "feeds", "marketplaces", "models"}`. No caller in `src/` or `tests/` (grep); effectively a convenience/diagnostic helper. `discovered boards` are not included.

## Failure modes and edge cases

| Situation | Result |
|---|---|
| Neither `X.yaml` nor `X.example.yaml` present | `FileNotFoundError(<path of X.yaml>)` |
| File empty / comments only | `ValueError("empty yaml: <path>")` |
| Missing required Profile key | `KeyError` with the key name |
| `:free` model id | `ValueError` from `load_models` |
| `JOBDIGEST_CONFIG_DIR` points to a dir with only `*.example.yaml` | Works, with warnings |
| Example fallback active in production | Only a log warning; the pipeline runs with example identity data |

## How to modify / extend safely
- **Add a profile field**: add it to `Profile` *after* the last non-default field with a default, read it in `load_profile` with `d.get`/`_int_field` so older private `profile.yaml` files keep loading, add it to `config/profile.example.yaml`, and extend `tests/test_config.py`.
- **Add a new YAML file**: add `load_x()` calling `_read_yaml("x.yaml")`, commit `config/x.example.yaml` (the fallback needs the `<stem>.example.yaml` naming), and mention it in `load_all`.
- Do not read `profile.timezone` expecting a behaviour: nothing implements it yet; see [cron-gate.md](cron-gate.md) for how scheduling is actually decided (UTC cron strings).
