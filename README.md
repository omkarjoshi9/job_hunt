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

The pipeline fetches public ATS boards (and optional RSS, HTML portals, and JobSpy marketplaces), applies Python hard filters, scores remaining jobs with OpenRouter, and sends a Telegram digest at 9:00 IST. There is no auto-apply.

GitHub Actions schedules the work. Python runs collect, score, and digest. Telegram Bot API delivers the message. Hosted job state lives in Neon Postgres because Actions runners do not persist disk.

Hunt config under `config/` is gitignored (`*.example.yaml` stubs stay in git). Workflow YAML stays in git.

## Design constraints

- No auto-apply.
- Real hunt YAML is local or stored in GitHub secrets. Only `config/*.example.yaml` is public.
- LinkedIn, Discord, Otta, Hugging Face jobs HTML, and LLMHire stay disabled on the `portals.yaml` kill list (`parser: kill`). An optional JobSpy marketplace lane (`marketplaces.yaml`) is a separate opt-in and is off in the public stub.
- Portals use Scrapling (`scrapling[fetchers]` plus pinned Playwright/Patchright **wheels**). Chromium is optional locally and required on the hosted `pipeline` workflow. Pytest never launches a browser. RSS stays `httpx` + `feedparser` (not Scrapling). Marketplace scrape is optional (`python-jobspy`) and never imported by collect.
- Collect does not call an LLM. Scoring runs later. Contacts enrich is regex + public HTTP (optional `JOBDIGEST_GITHUB_TOKEN`); it never calls OpenRouter.
- An empty digest after a successful collect is silent. `COLLECT FAILED` and `SCORE FAILED` are sent as Telegram alerts.

## Pipeline

```
companies.yaml (enabled: true)
        │
        ▼
   collect  ──  GitHub Actions (ATS + RSS + all portal fetch modes + optional marketplaces)
        │       Greenhouse / Lever / Ashby / Workable / SmartRecruiters / BambooHR
        ▼
   hard_filter (title, years of experience, US-remote, India)
        │
        ▼
   Neon or SQLite  (jobs, scores, runs, applications, contacts)
        │
        ▼
   digest  ──  9:00 IST (default JOBDIGEST_CRON = 30 3 * * *)
        │       weekday 14:00 IST urgent: digest --urgent-only (no sheet-sync)
        │       score (OpenRouter JSON)
        │       summarize (Python bullets if the summarizer fails)
        ├──────────► Telegram (plain text + pager; split ~3800 characters)
        ▼
   contacts ──  guest job_ids from digest stats (JD regex + optional GitHub)
        │
        ├──────────► Telegram Mail tile (draft only; tap m:{id} on the bot worker)
        └──────────► sheet-sync (digest slot only; new offcampus guests)
```

Duplicate apply URLs collapse to one row (UTM and click IDs stripped). The same posting from ATS and a portal is stored once. URLs already in `applications` are excluded from the digest guest list.

## Schedule

| Workflow | When | Command |
|---|---|---|
| [`.github/workflows/pipeline.yml`](.github/workflows/pipeline.yml) | `*/15 * * * *` with a cron gate + `workflow_dispatch` | `collect` then `digest` (or `digest --urgent-only`) then `contacts` then `sheet-sync` on the digest slot only |
| [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | push / pull_request | `pytest` only (wheels OK; no Chromium) |

| UTC cron | IST | Role |
|---|---|---|
| `30 3 * * *` (default `JOBDIGEST_CRON`) | 09:00 | full collect + digest + contacts + sheet-sync |
| `30 8 * * 1-5` (default `JOBDIGEST_CRON_URGENT`) | 14:00 weekdays | collect + `digest --urgent-only` + contacts; no sheet-sync |
| `0 4 * * *` | 09:30 | do not use |

The pipeline workflow ticks every 15 minutes. The gate writes `due` and `slot` (`digest` / `urgent`). An empty `JOBDIGEST_CRON` keeps `30 3 * * *`. An empty `JOBDIGEST_CRON_URGENT` keeps `30 8 * * 1-5`; set the secret to `off` to disable urgent. Manual **Run workflow** always uses the digest slot (collect, digest, contacts, sheet-sync). Hosted collect installs/caches Chromium so `fetch: dynamic` and `fetch: stealth` portals can run in the same job as ATS, RSS, and HTTP portals.

`--ats-only` still skips portals, RSS, and marketplaces (local/debug). Hosted collect has no `--portal-fetch` filter. Hosted state is **Neon** (`DATABASE_URL`). Local pytest and `--db` use SQLite. Playwright wheels are required so `FetcherSession` can import; Chromium binaries are required only on the hosted pipeline (and locally if you enable dynamic/stealth). GitHub datacenter IPs can still lose to Cloudflare; `solve_cloudflare: true` is per-row and not a guarantee.

Telegram uses Bot API `sendMessage` with plain text (no HTML parse mode). Cron workflows set `JOBDIGEST_REQUIRE_TELEGRAM=1` so a required send fails if the token or chat id is missing.

## Local setup

Python 3.11+ (CI uses 3.12).

```bash
pip install -e ".[dev]"
pip install -e ".[marketplaces]"        # optional JobSpy lane
python -m playwright install chromium   # only if you will enable fetch: dynamic or stealth
cp .env.example .env
cp config/profile.example.yaml config/profile.yaml
cp config/companies.example.yaml config/companies.yaml
cp config/models.example.yaml config/models.yaml
cp config/portals.example.yaml config/portals.yaml
cp config/feeds.example.yaml config/feeds.yaml
cp config/marketplaces.example.yaml config/marketplaces.yaml
```

Edit the copies. Git ignores them.

Set `enabled: true` on companies you want collected. Probe boards first (live HTTP; not CI):

```bash
python -m jobdigest.probe
python -m jobdigest.probe --enabled-only
python -m jobdigest.probe --ids razorpay,groww,devrev
```

Expect `ok` or `empty_board`. Leave dead tokens disabled. Unimplemented ATS types (`getro`, `workday`) must not be treated as empty boards.

Token discovery from a saved intern list is local only. It never writes `companies.yaml` / `companies.hosted.yaml` and never sets `enabled: true`. Copy the suggested YAML by hand, then probe and enable yourself. Do not put discover on the hosted pipeline or `start.sh`.

```bash
python -m jobdigest.discover --markdown INTERN_INTL.md
python -m jobdigest.discover --markdown INTERN_INTL.md --companies config/companies.yaml --probe
python -m jobdigest.discover --fetch   # maintainer only; pytest never fetches
```

One-shot local run (Git Bash on Windows is fine):

```bash
chmod +x start.sh && ./start.sh
```

That runs `collect --portal-fetch http`, then `digest`, then `sheet-sync` against `data/jobs.db`. It does **not** start `python -m jobdigest.bot`.

```bash
python -m jobdigest collect --portal-fetch http --db data/jobs.db
python -m jobdigest collect --ats-only --db data/jobs.db
python -m jobdigest collect --skip-ats --skip-portals --skip-feeds --db data/jobs.db
python -m playwright install chromium
python -m jobdigest collect --skip-ats --skip-feeds --portal-fetch dynamic --portal-fetch stealth --db data/jobs.db
python -m jobdigest score --db data/jobs.db
python -m jobdigest digest --db data/jobs.db
python -m jobdigest digest --urgent-only --db data/jobs.db
python -m jobdigest.bot --db data/jobs.db
python -m jobdigest sheet-append --url https://example.com/job --db data/jobs.db
python -m jobdigest sheet-sync --db data/jobs.db
```

`--ats-only` skips `config/portals.yaml`, `config/feeds.yaml`, and `config/marketplaces.yaml`. Without it, collect walks enabled portals (`fetch: http` via `FetcherSession`; `dynamic` / `stealth` via Playwright sessions), RSS (`feedparser`), and enabled marketplace rows (JobSpy). User-Agent is `JobHunt/0.1`, with robots.txt and a 3.5s delay between requests to the same host. Enable a portal or feed only after a real 200 with that fetch mode. SPA and kill-list rows stay `enabled: false`. Install Chromium before enabling `fetch: dynamic` or `fetch: stealth`. `--skip-marketplaces` skips JobSpy even when other lanes run.

If `DATABASE_URL` is set and you omit `--db`, collect and digest use Neon. `--db` always selects SQLite.

## Marketplace lane (optional)

This is **not** the `portals.yaml` kill list. LinkedIn / Internshala / Naukri rows there stay `parser: kill` and disabled. Marketplace collection is a fourth collect lane:

1. `pip install -e ".[marketplaces]"` (pulls `python-jobspy` and pandas).
2. Copy `config/marketplaces.example.yaml` → `config/marketplaces.yaml`.
3. Set `enabled: true` on the searches you want. Every public stub row is `enabled: false`.
4. Run `python -m jobdigest collect --skip-ats --skip-portals --skip-feeds`.

Each YAML row is one JobSpy search (`site` + `search_term` / `location`). `name` is `jobs.source` and the sweep key (unseen SERP rows tombstone after two successful runs). Stored `ats` is `marketplace`. Naukri descriptions need `linkedin_fetch_description: true` (already set on the Naukri stub). LinkedIn almost always needs `JOBDIGEST_MARKETPLACE_PROXIES` (comma-separated). Never call JobSpy without an explicit `site` — the lane always passes one site per row.

Hosted CI (`ci.yml`) does not install JobSpy. The hosted pipeline installs `.[marketplaces]` and writes `MARKETPLACES_YAML` only if that secret is set; otherwise the example stub (all disabled) is a no-op.

## Hosted setup (GitHub Actions)

Actions checks out the public example YAML, then writes private config from secrets onto the runner. A company, portal, feed, or marketplace row is fetched only when that row’s `enabled:` is true. All-false YAML is a successful no-op, not a collect failure. The hosted pipeline runs ATS, RSS, every enabled portal fetch mode, and enabled marketplace searches in one collect.

Paste the full contents of each local `config/*.yaml` file into the matching `*_YAML` secret. Companies you want collected must have `enabled: true` in `COMPANIES_YAML`. GitHub Actions secrets are capped at 48 KB; the full `companies.yaml` inventory can exceed that. In that case paste the enabled-only snapshot `config/companies.hosted.yaml` (collect ignores `enabled: false` rows anyway).

### GitHub secrets

| Secret | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | Paid OpenRouter models (not `:free`, not `openrouter/auto`) |
| `DATABASE_URL` | Neon connection string |
| `TELEGRAM_BOT_TOKEN` | From [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | User or group id (not committed) |
| `JOBDIGEST_CRON` | Five-field UTC cron; empty = `30 3 * * *` |
| `JOBDIGEST_CRON_URGENT` | Weekday urgent slot; empty = `30 8 * * 1-5`; `off` disables |
| `PROFILE_YAML` | Full `config/profile.yaml` |
| `COMPANIES_YAML` | Enabled companies (`config/companies.hosted.yaml` if full `companies.yaml` is over 48 KB) |
| `MODELS_YAML` | Full `config/models.yaml` |
| `PORTALS_YAML` | Full `config/portals.yaml` |
| `FEEDS_YAML` | Full `config/feeds.yaml` |
| `MARKETPLACES_YAML` | Optional full `config/marketplaces.yaml`; omit to keep the all-disabled example stub |
| `JOBDIGEST_MARKETPLACE_PROXIES` | Optional comma-separated proxies for JobSpy (LinkedIn) |
| `JOBDIGEST_GITHUB_TOKEN` | Optional PAT for GitHub org/commit lookup during contacts enrich; never use Actions’ `GITHUB_TOKEN`. Empty is fine (JD parse still runs). |
| `GOOGLE_SHEET_ID` | Spreadsheet id |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Service-account JSON **text**, not a path |

Do not put keys in workflow YAML or git. Do not commit `.env`, `config/*.yaml` (except `*.example.yaml`), `data/*.db`, Google JSON, Telegram chat ids, or Neon URLs.

### BotFather

1. In Telegram, talk to [@BotFather](https://t.me/BotFather), create a bot, and copy the token into `TELEGRAM_BOT_TOKEN` (`.env` locally, Actions secret hosted).
2. Message the bot, then call `getUpdates` (or use a user/group id you already know) and set `TELEGRAM_CHAT_ID`.

### Telegram bot worker

Actions **push** the morning digest (ranked list + numeric pager) and weekday urgent cards. The hosted pipeline also pre-fills hiring-manager `contacts` for those guest jobs (fail-soft; missing GitHub token or 403 is not `COLLECT FAILED`). Callbacks (`o:` open card, `s:` save, `a:` applied, `h:` hide, `w:` Why me?, `m:` Mail draft) and slash commands (`/followups`, `/health`) need a long-poll worker. GitHub Actions cannot hold `getUpdates`. Run **one** replica on a laptop or small VM (two pollers fight):

```bash
python -m jobdigest.bot --db data/jobs.db
```

Omit `--db` when `DATABASE_URL` is set (Neon). Apply is a URL button; status actions edit in place. Hide removes the job from the next guest list without marking it applied. Mail posts a Subject / To / Body draft plus a `mailto:` link when an email exists — send it yourself; there is no SMTP. Guest jobs only (last digest `job_ids`). Optional `github_url` on the profile is included in the draft when set. `start.sh` and `pipeline.yml` never start this process.

### Neon

Create a Postgres project and copy the connection string into `DATABASE_URL`. An empty `DATABASE_URL` keeps SQLite (`DATABASE_PATH` / `--db`). Schema and identity match SQLite: `apply_url_norm` UNIQUE, partial unique `(source, external_id)` when id is set, applications unique on `apply_url_norm`.

`workflow_dispatch` on the pipeline uses the digest slot (collect, digest, contacts, sheet-sync). Default `pytest` is offline SQLite only.

## Google Sheet

Hosted pipeline runs `python -m jobdigest sheet-sync` after contacts enrich (digest slot only). It appends new offcampus guest jobs (the same relevant scored jobs as the digest, up to `digest_max` new URLs). If a link is already on the sheet, that row is left unchanged, including Applied, Status, Campus, and Notes. Collect never writes the sheet.

Columns: Date, Company, Role, Link, Source, **Campus**, **Applied**, Status, Notes.

- Rows written by the repo: `Campus=offcampus`, Applied and Status blank.
- Oncampus rows are added by hand and are not updated by sheet-sync.

`python -m jobdigest sheet-append --url …` writes a local `applications` row and appends unsynced applies (local). Duplicate and UTM-tagged URLs stay one DB row and one sheet row.

Locally, `GOOGLE_SERVICE_ACCOUNT_JSON` is a gitignored JSON path. On Actions, the secret is the JSON file contents; the workflow writes a temp file. Share the spreadsheet with the service account `client_email` as Editor. Freeze row 1.

Missing Google credentials skip **sheet-append** / **sheet-sync** with exit 0. The hosted pipeline sets `JOBDIGEST_REQUIRE_SHEET=1` so missing creds print `SHEET FAILED missing GOOGLE_SHEET_ID or GOOGLE_SERVICE_ACCOUNT_JSON` and exit 1.

## Score and digest

`python -m jobdigest score` scores hard-filter `pass` jobs with OpenRouter JSON (`config/models.yaml` score model). `python -m jobdigest digest` requires a successful collect within 26 hours; otherwise it prints `COLLECT FAILED` and exits 1. Score HTTP or parse failures print `SCORE FAILED` and skip the summarizer.

Digest prints Telegram plain text to stdout (Actions logs) and POSTs the same parts via Bot API when token and chat id are set. Empty stdout after a good collect means there is nothing new to send. If the summarizer fails, Python bullets for the same guest list are used.

Requires `OPENROUTER_API_KEY` in `.env`. Use two paid models in local `config/models.yaml`. Collect stays LLM-free. The scorer receives skills, location, and role types from the profile — not GPA.

Hard filters run in Python at every job upsert: reject senior titles, reject 3+ years of experience, reject US-remote work-auth language, require India-related location text, and require an intern or new-grad title. Only `pass` jobs are scored.

## CLI

```
python -m jobdigest collect|score|digest|sheet-append|sheet-sync
python -m jobdigest digest --urgent-only
python -m jobdigest.bot
python -m jobdigest.probe
python -m jobdigest.discover
```

The `jobdigest` console script is also installed. With no subcommand, the CLI prints help and exits 0.

## Tests

```bash
pytest
```

Default pytest is offline (mocked ATS HTTP, injected LLM, fake Telegram, fake sheet). Live HTTP is for maintainers via `python -m jobdigest.probe`, not CI.

## License

MIT.
