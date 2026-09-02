# jobdigest

Daily intern/new-grad India job digest: ATS APIs + scrape → SQLite or Neon → OpenRouter score + summarizer → Telegram Bot API at 10:00 IST.

No auto-apply. Hunt YAML under `config/` is gitignored (`*.example.yaml` stubs stay public). GitHub workflow YAML stays in git.

## I7 — hosted collect + digest

GitHub Actions is the clock. Python is the cook. Telegram Bot API is the mailman.

| Workflow | When | Command |
|---|---|---|
| [`.github/workflows/collect.yml`](.github/workflows/collect.yml) | every 6 hours UTC + `workflow_dispatch` | `python -m jobdigest collect --ats-only` |
| [`.github/workflows/digest.yml`](.github/workflows/digest.yml) | `30 4 * * *` UTC + `workflow_dispatch` | `python -m jobdigest digest` then `sheet-sync` |
| [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | push / pull_request | `pytest` only |

| UTC cron | IST |
|---|---|
| `30 4 * * *` | 10:00 (digest) |
| `0 */6 * * *` | collect every 6 hours |
| `0 4 * * *` | 09:30 — **do not use** |

Hosted collect is `--ats-only` because `FetcherSession` still pulls Playwright even for HTTP. Portals and RSS stay local after a 200. Actions runners wipe disk, so hosted job state is **Neon Postgres** (`DATABASE_URL`). Local pytest and `--db` stay SQLite.

Real files in `config/` (`profile.yaml`, `companies.yaml`, `models.yaml`, `portals.yaml`, `feeds.yaml`) are not in git. Collect and digest write them from secrets on the runner. Example YAML in git is a stub for pytest. Set GitHub secret `JOBDIGEST_ENABLE` to a comma-separated list of company `id` values (for example `razorpay,postman`). Collect treats those ids as enabled. An empty list is a successful no-op, not a collect failure. Optional `portal:name` / `feed:name` prefixes apply when you run collect without `--ats-only`.

Telegram: Bot API `sendMessage`, plain text, no HTML parse mode. Non-empty digest bodies are sent (split ~3800 chars). Empty stdout after a good collect is silent (no send). `COLLECT FAILED` / `SCORE FAILED` are sent as alerts. Cron workflows set `JOBDIGEST_REQUIRE_TELEGRAM=1` so a required send with missing token or chat id fails loud.

### GitHub secrets

**Secrets:** `OPENROUTER_API_KEY`, `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `JOBDIGEST_ENABLE`, `PROFILE_YAML`, `COMPANIES_YAML`, `MODELS_YAML`, `PORTALS_YAML`, `FEEDS_YAML`, `GOOGLE_SHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`.

Paste the full contents of each local `config/*.yaml` file into the matching `*_YAML` secret. `JOBDIGEST_ENABLE` is the comma-separated company ids. `GOOGLE_SERVICE_ACCOUNT_JSON` on GitHub is the service-account JSON **text**, not a path.

Never put keys in workflow YAML or git. Chat ids stay out of git.

### BotFather

1. In Telegram, talk to [@BotFather](https://t.me/BotFather), create a bot, copy the token into `TELEGRAM_BOT_TOKEN` (`.env` locally, Actions secret hosted).
2. Message your bot, then call `getUpdates` (or use a user/group id you already know) and put that id in `TELEGRAM_CHAT_ID`.

### Neon

Create a Postgres project and copy the connection string into `DATABASE_URL` (`.env` locally if you want Neon on your machine; Actions secret for hosted). Empty `DATABASE_URL` keeps SQLite (`DATABASE_PATH` / `--db`). Schema and identity match SQLite (`apply_url_norm` UNIQUE, partial unique `(source, external_id)` when id is set, applications unique on `apply_url_norm`).

`workflow_dispatch` on digest sends a real Telegram when secrets exist (manual smoke). Default `pytest` is offline SQLite only.

## I6 — Google Sheet

Hosted **digest** runs `python -m jobdigest sheet-sync` after Telegram. It **appends** new offcampus guest jobs (same relevant scored jobs as the digest, up to `digest_max` new URLs). If a link is already on the sheet, that row is left alone — including Applied, Status, Campus, and Notes you typed. Collect never writes the sheet.

Columns: Date, Company, Role, Link, Source, **Campus**, **Applied**, Status, Notes. Repo rows are `Campus=offcampus` with Applied and Status blank. **Oncampus** rows are yours only; add them by hand.

`python -m jobdigest sheet-append --url …` still writes a local `applications` row and appends unsynced applies (local). Duplicate and utm-tagged URLs stay one DB row and one sheet row.

Put the spreadsheet id in `GOOGLE_SHEET_ID`. Locally, `GOOGLE_SERVICE_ACCOUNT_JSON` is a gitignored JSON **path**. On Actions, the secret is the **JSON file contents**; the workflow writes a temp file. Share the sheet with the service account `client_email` as Editor. Freeze row 1.

Missing Google creds fail **sheet-append** / **sheet-sync** only. Cron digest sets `JOBDIGEST_REQUIRE_SHEET=1`.

## I5 — portals + RSS

`python -m jobdigest collect` also loops `config/portals.yaml` (one HTTP `FetcherSession`) and `config/feeds.yaml` (`feedparser`) unless you pass `--ats-only`. Duplicate apply URLs collapse with ATS into one SQLite row.

Scrapling is HTTP-only (`scrapling` + `curl_cffi` + `browserforge` + `protego`). No Chromium, no `scrapling install`, no Playwright extra. Local portals and feeds stay `enabled: false` in gitignored YAML. Flip `enabled: true` locally after a 200. Kill-list rows (Otta, Hugging Face jobs, LLMHire, Discord, LinkedIn) stay false.

## I4 — score + digest

`python -m jobdigest score` scores hard-filter `pass` jobs with OpenRouter JSON (`config/models.yaml` score model). `python -m jobdigest digest` requires a recent successful collect (within 26h); otherwise it prints `COLLECT FAILED` and exits 1. Score HTTP/parse failures print `SCORE FAILED` and skip the summarizer.

Digest prints Telegram plain text to stdout (Actions logs) and POSTs the same parts via Bot API when token and chat id are set. Quiet empty stdout after a good collect means nothing new to show. If the summarizer dies, Python bullets for the same guest list are the fallback.

Needs `OPENROUTER_API_KEY` in `.env` (copy `.env.example`). Two paid models in local `config/models.yaml` (not `:free`, not `openrouter/auto`). Collect stays LLM-free.

## I3 — collect

`python -m jobdigest collect` upserts ATS jobs (`--db` or `DATABASE_PATH`, default `data/jobs.db`). If `DATABASE_URL` is set and you omit `--db`, collect uses Neon. Hard filters run in Python before any model. `--ats-only` skips portals and RSS.

Hunt list is local `config/companies.yaml` (copy from `config/companies.example.yaml`). Rows stay `enabled: false`. Enable locally after `python -m jobdigest.probe` returns HTTP 200 (`ok` or `empty_board`), or set `JOBDIGEST_ENABLE`. Never commit `data/*.db` or WAL.

Maintainer (live HTTP; not CI):

```
python -m jobdigest.probe
```

Optional live (not CI): copy `.env`, collect, then `python -m jobdigest digest --db data/jobs.db`.

## Config and secrets

Copy examples, then edit locally (gitignored):

```
cp config/profile.example.yaml config/profile.yaml
cp config/companies.example.yaml config/companies.yaml
cp config/models.example.yaml config/models.yaml
cp config/portals.example.yaml config/portals.yaml
cp config/feeds.example.yaml config/feeds.yaml
```

Secrets are `.env` (copy from `.env.example`) plus the same values in GitHub Actions secrets. Never commit `.env`, `config/*.yaml` (except `*.example.yaml`), `data/*.db`, Google JSON, Telegram chat ids, or Neon URLs.

## Install

```
pip install -e ".[dev]"
```

## CLI

```
chmod +x start.sh && ./start.sh
```

Git Bash on Windows. One-shot `collect --ats-only` then `digest` against `data/jobs.db`.

```
python -m jobdigest collect|score|digest|sheet-append|sheet-sync
```

```
python -m jobdigest collect --ats-only --db data/jobs.db
python -m jobdigest score --db data/jobs.db
python -m jobdigest digest --db data/jobs.db
python -m jobdigest sheet-append --url https://example.com/job --db data/jobs.db
python -m jobdigest sheet-sync --db data/jobs.db
```
