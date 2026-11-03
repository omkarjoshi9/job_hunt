# Configuration and secrets

jobdigest is configured by **YAML files** (what to hunt for and what to fetch) and **environment variables**
(credentials, switches, paths). This page explains how both are resolved. Per-key reference for every YAML file is in
[infra/config-files](../infra/config-files.md); the code that loads them is in
[foundation/config](../modules/foundation/config.md).

## YAML files

All live in `config/` (or `$JOBDIGEST_CONFIG_DIR`). `config.config_dir()` returns the override directory when
`JOBDIGEST_CONFIG_DIR` is set, otherwise `<repo>/config`.

| File | Loader | What it controls |
|---|---|---|
| `profile.yaml` | `config.load_profile()` -> `Profile` | The candidate: skills, home city, filter regexes, digest size and quality knobs |
| `companies.yaml` | `config.load_companies()` | The curated list of ATS boards to fetch |
| `models.yaml` | `config.load_models()` -> `Models` | LLM model names and prompt versions, batch size |
| `portals.yaml` | `config.load_portals()` | HTML portals (parser, fetch mode, enabled flag) |
| `feeds.yaml` | `config.load_feeds()` | RSS feeds |
| `marketplaces.yaml` | `config.load_marketplaces()` | JobSpy marketplace searches (optional) |
| `data/boards_india.tsv` | `config.load_discovered_boards()` | Boards found by `jobdigest.expand` (auto-merged by `collect`) |

### Example fallback (and why it is risky)

`config._yaml_path(name)` returns `config/<name>.yaml` if it exists, otherwise `config/<name>.example.yaml`, and logs a
**warning** that names the file. This lets a fresh clone run (with everything disabled) but it also means a missing
`profile.yaml` silently becomes the example profile (a fictional candidate). When you see
`falling back to example config` in a log, the real file is missing. See
[`_yaml_path`](../modules/foundation/config.md#_yaml_path).

### Production keeps the YAML in secrets

The real `profile.yaml`, `companies.yaml`, `models.yaml`, `portals.yaml` and `feeds.yaml` hold personal data and the
hunting strategy, so on GitHub Actions they are stored as repository **secrets** (`PROFILE_YAML`, `COMPANIES_YAML`,
`MODELS_YAML`, `PORTALS_YAML`, `FEEDS_YAML`, optional `MARKETPLACES_YAML`). A step named *Write private config* in
`pipeline.yml` writes each secret to the matching file under `config/` before any stage runs, and fails the job
(`exit 1`) if one of the five required secrets is empty. `bot.yml` writes only `PROFILE_YAML` and `MODELS_YAML`.
Details: [infra/github-workflows](../infra/github-workflows.md).

> In this repository `config/feeds.yaml`, `models.yaml` and `portals.yaml` are also committed (they contain no
> secrets), while `profile.yaml` and `companies.yaml` are not. Do not assume the tracked files equal production.

## Environment variables

Loaded from the process environment; locally `envfile.load_dotenv()` also reads `<repo>/.env` and **never overrides**
a variable that is already set. Every variable the source reads (found by scanning `os.environ` in `src/`):

| Variable | Read in | Meaning / default |
|---|---|---|
| `DATABASE_URL` | `db/connection.py` | Postgres connection string. When set *and* no explicit path is passed, Postgres is used; otherwise SQLite |
| `DATABASE_PATH` | `db/connection.py` | SQLite file path (relative paths resolve against the repo root). Default `data/jobs.db` |
| `OPENROUTER_API_KEY` | `score.py`, `digest.py`, `bot.py` | LLM access. Scoring fails fast without it when there is work to do |
| `OPENROUTER_REFERER` | `openrouter.py` | `HTTP-Referer` header; default `https://openrouter.ai` |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | `telegram.py` | Bot credentials and the single allowed chat |
| `JOBDIGEST_REQUIRE_TELEGRAM` | `telegram.py` | `1`/`true`/`yes`: a required send without credentials fails loudly instead of skipping |
| `GOOGLE_SHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON` | `sheet.py` | Sheet id and a path to the service-account JSON |
| `JOBDIGEST_REQUIRE_SHEET` | `sheet.py` | Same idea for the sheet |
| `JOBDIGEST_GITHUB_TOKEN` | `contacts/github.py`, `contacts/run.py` | Enables the GitHub-org recruiter lookup; contact lookup degrades without it |
| `JOBDIGEST_MARKETPLACE_PROXIES` | `marketplaces/__init__.py` | Comma-separated proxies for the JobSpy lane |
| `JOBDIGEST_CONFIG_DIR` | `config.py`, `discover.py` | Override the `config/` directory |
| `JOBDIGEST_DISCOVERED_BOARDS` | `config.py` | Path to the discovered-boards TSV; `off`/`none`/`0`/`false` disables it (tests set `off`) |
| `JOBDIGEST_CRON`, `JOBDIGEST_CRON_URGENT` | `cron.py` | Cron expressions for the digest slot (default `30 3 * * *`) and urgent slot (default `30 8 * * 1-5`; `off` disables) |
| `GITHUB_EVENT_NAME`, `GITHUB_OUTPUT` | `cron.py` | Set by GitHub Actions; used by the gate job |
| `JOBDIGEST_LOG_LEVEL`, `JOBDIGEST_LOG_FILE` | `logutil.py` | Log level (default `INFO`); optional extra log file |
| `JOBDIGEST_SLUG_DIR` | `scripts/_probe_common.py` | Directory of slug lists for the research scripts |

`DATABASE_URL` vs `DATABASE_PATH`: `db.connection.uses_postgres(path)` is true only when `path is None` **and**
`DATABASE_URL` is non-empty. Passing `--db` therefore always selects SQLite even if `DATABASE_URL` is set - this is
what `start.sh` relies on.

## Profile knobs that shape the digest

Fields added for ranking quality, all optional with defaults (read in `config.load_profile`):

| Field | Default | Effect |
|---|---|---|
| `top_companies` | `[]` | Extra employers treated as top-tier (frontend/QA allowed, fit boost) - see [fit-and-tiers](../modules/ranking/fit-and-tiers.md) |
| `digest_min_quality` | `50.0` | Digest never includes jobs below this blended quality |
| `digest_per_company` | `1` | Max roles per company per digest |
| `digest_per_portal` | `2` | Max roles per aggregator source per digest |
| `digest_company_cooldown_days` | `3` | Skip companies sent within this many days unless quality >= 85 |
| `digest_max_age_days` | `21` | Drop postings older than this |
| `urgent_score_min` / `urgent_daily_cap` | `85` / `3` | Urgent alert threshold and daily cap |

See [ranking/rank-and-selection](../modules/ranking/rank-and-selection.md) for how each is applied.
