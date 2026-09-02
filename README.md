# jobdigest

```
     ██╗ ██████╗ ██████╗     ██╗  ██╗██╗   ██╗███╗   ██╗████████╗
     ██║██╔═══██╗██╔══██╗    ██║  ██║██║   ██║████╗  ██║╚══██╔══╝
     ██║██║   ██║██████╔╝    ███████║██║   ██║██╔██╗ ██║   ██║
██   ██║██║   ██║██╔══██╗    ██╔══██║██║   ██║██║╚██╗██║   ██║
╚█████╔╝╚██████╔╝██████╔╝    ██║  ██║╚██████╔╝██║ ╚████║   ██║
 ╚════╝  ╚═════╝ ╚═════╝     ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝   ╚═╝
```

Daily intern and new-grad job digest for India-based roles.

The pipeline fetches public ATS boards, applies Python hard filters, scores remaining jobs with OpenRouter, and sends a Telegram digest at 10:00 IST. There is no auto-apply.

GitHub Actions schedules the work. Python runs collect, score, and digest. Telegram Bot API delivers the message. Hosted job state lives in Neon Postgres because Actions runners do not persist disk.

Hunt config under `config/` is gitignored (`*.example.yaml` stubs stay in git). Workflow YAML stays in git.

## Design constraints

- No auto-apply.
- Real hunt YAML is local or stored in GitHub secrets. Only `config/*.example.yaml` is public.
- LinkedIn, Discord, Otta, Hugging Face jobs HTML, and LLMHire are on a kill list and stay disabled.
- HTTP-only fetching (`scrapling`, `curl_cffi`, `browserforge`, `protego`). No Playwright, Chromium, `scrapling install`, or stealth fetchers.
- Collect does not call an LLM. Scoring runs later.
- An empty digest after a successful collect is silent. `COLLECT FAILED` and `SCORE FAILED` are sent as Telegram alerts.

## Pipeline

```
companies.yaml (enabled: true)
        │
        ▼
   collect  ──  every 6h UTC, --ats-only on GitHub
        │       Greenhouse / Lever / Ashby / Workable
        │       (portals and RSS only when run locally)
        ▼
   hard_filter (title, years of experience, US-remote, India)
        │
        ▼
   Neon or SQLite  (jobs, scores, runs, applications)
        │
        ▼
   digest  ──  10:00 IST (default JOBDIGEST_CRON = 30 4 * * *)
        │       score (OpenRouter JSON)
        │       summarize (Python bullets if the summarizer fails)
        ├──────────► Telegram (plain text, split ~3800 characters)
        └──────────► sheet-sync (new offcampus guests only)
```

Duplicate apply URLs collapse to one row (UTM and click IDs stripped). The same posting from ATS and a portal is stored once. URLs already in `applications` are excluded from the digest guest list.

## Schedule

| Workflow | When | Command |
|---|---|---|
| [`.github/workflows/collect.yml`](.github/workflows/collect.yml) | `0 */6 * * *` UTC + `workflow_dispatch` | `python -m jobdigest collect --ats-only` |
| [`.github/workflows/digest.yml`](.github/workflows/digest.yml) | `*/15 * * * *` with a cron gate + `workflow_dispatch` | `python -m jobdigest digest` then `sheet-sync` |
| [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | push / pull_request | `pytest` only |

| UTC cron | IST | Role |
|---|---|---|
| `30 4 * * *` (default `JOBDIGEST_CRON`) | 10:00 | digest |
| `0 */6 * * *` | — | collect (ATS only) |
| `0 4 * * *` | 09:30 | do not use |

The digest workflow ticks every 15 minutes and no-ops unless `JOBDIGEST_CRON` matches that UTC slot. An empty secret keeps `30 4 * * *`. Manual **Run workflow** always runs. Collect stays `0 */6 * * *`.

Hosted collect is `--ats-only` because `FetcherSession` still imports Playwright even for HTTP. Portals and RSS stay local after a confirmed HTTP 200. Hosted state is **Neon** (`DATABASE_URL`). Local pytest and `--db` use SQLite.

Telegram uses Bot API `sendMessage` with plain text (no HTML parse mode). Cron workflows set `JOBDIGEST_REQUIRE_TELEGRAM=1` so a required send fails if the token or chat id is missing.

## Local setup

Python 3.11+ (CI uses 3.12).

```bash
pip install -e ".[dev]"
cp .env.example .env
cp config/profile.example.yaml config/profile.yaml
cp config/companies.example.yaml config/companies.yaml
cp config/models.example.yaml config/models.yaml
cp config/portals.example.yaml config/portals.yaml
cp config/feeds.example.yaml config/feeds.yaml
```

Edit the copies. Git ignores them.

Set `enabled: true` on companies you want collected. Probe boards first (live HTTP; not CI):

```bash
python -m jobdigest.probe
```

Expect `ok` or `empty_board`. Leave dead tokens disabled. Unimplemented ATS types (`getro`, `workday`) must not be treated as empty boards.

One-shot local run (Git Bash on Windows is fine):

```bash
chmod +x start.sh && ./start.sh
```

That runs `collect --ats-only`, then `digest`, then `sheet-sync` against `data/jobs.db`.

```bash
python -m jobdigest collect --ats-only --db data/jobs.db
python -m jobdigest score --db data/jobs.db
python -m jobdigest digest --db data/jobs.db
python -m jobdigest sheet-append --url https://example.com/job --db data/jobs.db
python -m jobdigest sheet-sync --db data/jobs.db
```

Without `--ats-only`, collect also walks `config/portals.yaml` (one HTTP `FetcherSession`, User-Agent `JobHunt/0.1`, robots.txt, 3.5s delay between requests to the same host) and `config/feeds.yaml` (`feedparser`). Enable a portal or feed only after a real 200 response. SPA targets and kill-list rows stay `enabled: false`.

If `DATABASE_URL` is set and you omit `--db`, collect and digest use Neon. `--db` always selects SQLite.

## Hosted setup (GitHub Actions)

Actions checks out the public example YAML, then writes private config from secrets onto the runner. A company, portal, or feed is fetched only when that row’s `enabled:` is true. All-false YAML is a successful no-op, not a collect failure. Hosted collect is `--ats-only`, so portal and feed flags apply only to local runs.

Paste the full contents of each local `config/*.yaml` file into the matching `*_YAML` secret. Companies you want collected must have `enabled: true` in `COMPANIES_YAML`.

### GitHub secrets

| Secret | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | Paid OpenRouter models (not `:free`, not `openrouter/auto`) |
| `DATABASE_URL` | Neon connection string |
| `TELEGRAM_BOT_TOKEN` | From [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | User or group id (not committed) |
| `JOBDIGEST_CRON` | Five-field UTC cron; empty = `30 4 * * *` |
| `PROFILE_YAML` | Full `config/profile.yaml` |
| `COMPANIES_YAML` | Full `config/companies.yaml` |
| `MODELS_YAML` | Full `config/models.yaml` |
| `PORTALS_YAML` | Full `config/portals.yaml` |
| `FEEDS_YAML` | Full `config/feeds.yaml` |
| `GOOGLE_SHEET_ID` | Spreadsheet id |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Service-account JSON **text**, not a path |

Do not put keys in workflow YAML or git. Do not commit `.env`, `config/*.yaml` (except `*.example.yaml`), `data/*.db`, Google JSON, Telegram chat ids, or Neon URLs.

### BotFather

1. In Telegram, talk to [@BotFather](https://t.me/BotFather), create a bot, and copy the token into `TELEGRAM_BOT_TOKEN` (`.env` locally, Actions secret hosted).
2. Message the bot, then call `getUpdates` (or use a user/group id you already know) and set `TELEGRAM_CHAT_ID`.

### Neon

Create a Postgres project and copy the connection string into `DATABASE_URL`. An empty `DATABASE_URL` keeps SQLite (`DATABASE_PATH` / `--db`). Schema and identity match SQLite: `apply_url_norm` UNIQUE, partial unique `(source, external_id)` when id is set, applications unique on `apply_url_norm`.

`workflow_dispatch` on digest sends a real Telegram message when secrets exist (manual smoke test). Default `pytest` is offline SQLite only.

## Google Sheet

Hosted digest runs `python -m jobdigest sheet-sync` after Telegram. It appends new offcampus guest jobs (the same relevant scored jobs as the digest, up to `digest_max` new URLs). If a link is already on the sheet, that row is left unchanged, including Applied, Status, Campus, and Notes. Collect never writes the sheet.

Columns: Date, Company, Role, Link, Source, **Campus**, **Applied**, Status, Notes.

- Rows written by the repo: `Campus=offcampus`, Applied and Status blank.
- Oncampus rows are added by hand and are not updated by sheet-sync.

`python -m jobdigest sheet-append --url …` writes a local `applications` row and appends unsynced applies (local). Duplicate and UTM-tagged URLs stay one DB row and one sheet row.

Locally, `GOOGLE_SERVICE_ACCOUNT_JSON` is a gitignored JSON path. On Actions, the secret is the JSON file contents; the workflow writes a temp file. Share the spreadsheet with the service account `client_email` as Editor. Freeze row 1.

Missing Google credentials fail **sheet-append** / **sheet-sync** only. Cron digest sets `JOBDIGEST_REQUIRE_SHEET=1`.

## Score and digest

`python -m jobdigest score` scores hard-filter `pass` jobs with OpenRouter JSON (`config/models.yaml` score model). `python -m jobdigest digest` requires a successful collect within 26 hours; otherwise it prints `COLLECT FAILED` and exits 1. Score HTTP or parse failures print `SCORE FAILED` and skip the summarizer.

Digest prints Telegram plain text to stdout (Actions logs) and POSTs the same parts via Bot API when token and chat id are set. Empty stdout after a good collect means there is nothing new to send. If the summarizer fails, Python bullets for the same guest list are used.

Requires `OPENROUTER_API_KEY` in `.env`. Use two paid models in local `config/models.yaml`. Collect stays LLM-free. The scorer receives skills, location, and role types from the profile — not GPA.

Hard filters run in Python at every job upsert: reject senior titles, reject 3+ years of experience, reject US-remote work-auth language, require India-related location text, and require an intern or new-grad title. Only `pass` jobs are scored.

## CLI

```
python -m jobdigest collect|score|digest|sheet-append|sheet-sync
```

The `jobdigest` console script is also installed. With no subcommand, the CLI prints help and exits 0.

## Tests

```bash
pytest
```

Default pytest is offline (mocked ATS HTTP, injected LLM, fake Telegram, fake sheet). Live HTTP is for maintainers via `python -m jobdigest.probe`, not CI.

## License

MIT.
