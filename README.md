# jobdigest

```
     ██╗ ██████╗ ██████╗     ██╗  ██╗██╗   ██╗███╗   ██╗████████╗
     ██║██╔═══██╗██╔══██╗    ██║  ██║██║   ██║████╗  ██║╚══██╔══╝
     ██║██║   ██║██████╔╝    ███████║██║   ██║██╔██╗ ██║   ██║
██   ██║██║   ██║██╔══██╗    ██╔══██║██║   ██║██║╚██╗██║   ██║
╚█████╔╝╚██████╔╝██████╔╝    ██║  ██║╚██████╔╝██║ ╚████║   ██║
 ╚════╝  ╚═════╝ ╚═════╝     ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝   ╚═╝
            intern / new-grad / India / 10:00 IST / no mercy
```

A feral little Python daemon that **hunts intern and new-grad jobs in India**, yeets the junk, scores the rest with a paid model, and slaps a digest into your Telegram at **10:00 IST** like a news anchor who actually likes you.

It does **not** apply for you. It does **not** scrape LinkedIn. It does **not** wake Chromium. It does **not** live on a VPS whispering into WhatsApp. GitHub Actions is the clock. Python is the cook. Telegram Bot API is the mailman. Neon is the fridge.

If you wanted a polite spreadsheet, close this tab.

---

## The manifesto (read this or suffer)

| This repo | The cursed timeline we refused |
|---|---|
| Public ATS JSON (Greenhouse, Lever, Ashby, Workable) | "just log into twelve career sites every morning" |
| Hard filters in boring Python **before** any LLM | dumping raw JD soup into a chatbot and praying |
| OpenRouter, paid models, JSON schema | `:free` roulette and `openrouter/auto` |
| Telegram `sendMessage`, plain text, no HTML parse mode | a second phone, a second brain, a second religion |
| Neon Postgres on Actions (runners wipe disk) | committing `data/jobs.db` like a war crime |
| `enabled: true` only in **your** private YAML | publishing your hunt list for the whole internet |
| HTTP-only Scrapling + robots.txt + a 3.5s nap | Playwright stealth browsers, ToS speedruns, LinkedIn |

**Laws of the hunt**

1. No auto-apply. You still have to be a person.
2. Committed hunt YAML is a **stub**. Real `config/*.yaml` is gitignored. Examples stay public.
3. Kill-list stays dead: Otta, Hugging Face jobs HTML, LLMHire, Discord, LinkedIn.
4. No Playwright. No Chromium. No `scrapling install`. No StealthyFetcher. If a site wants a browser, we walk away.
5. Collect is LLM-free. Scoring happens later. The board APIs do not need your personality.
6. Empty digest after a good collect is **silence**. Failed collect / failed score is a loud `COLLECT FAILED` / `SCORE FAILED` in Telegram.

---

## The machine

```
  companies.yaml (enabled: true)
           │
           ▼
   ┌──────────────┐     every 6h UTC, --ats-only on GitHub
   │   COLLECT    │ ──► Greenhouse / Lever / Ashby / Workable
   └──────────────┘     (portals + RSS only on your laptop)
           │
           ▼  hard_filter (title / YoE / US-remote / India)
           │
   ┌──────────────┐
   │  Neon / SQLite  jobs · scores · runs · applications
   └──────────────┘
           │
           ▼  10:00 IST (default JOBDIGEST_CRON = 30 4 * * *)
   ┌──────────────┐
   │    DIGEST    │ ──► score (gpt-4o-mini JSON)
   └──────────────┘ ──► summarize (or Python bullets if the poet dies)
           │
           ├──────────► Telegram  (split ~3800 chars)
           └──────────► sheet-sync (new offcampus guests only)
```

Duplicate apply URLs collapse. UTM tags get shaved. Same job from ATS + a portal is **one row**. Applied URLs fall out of the guest list so you are not recommended the same intern role you already bled on.

---

## The clock (this one eats people)

| Workflow | When | What it actually runs |
|---|---|---|
| [`.github/workflows/collect.yml`](.github/workflows/collect.yml) | `0 */6 * * *` UTC + **Run workflow** | `python -m jobdigest collect --ats-only` |
| [`.github/workflows/digest.yml`](.github/workflows/digest.yml) | ticks `*/15 * * * *`, then a gate | `python -m jobdigest digest` then `sheet-sync` |
| [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | push / pull_request | `pytest` only. Offline. No secrets. No drama. |

| UTC | IST | Meaning |
|---|---|---|
| `30 4 * * *` (default `JOBDIGEST_CRON`) | **10:00** | digest o'clock. this is the one. |
| `0 */6 * * *` | whenever | collect. ATS only. |
| `0 4 * * *` | 09:30 | **do not use.** you will be early, you will be wrong, you will be sad. |

Digest YAML wakes every 15 minutes and **no-ops** unless `JOBDIGEST_CRON` matches that UTC slot. Empty secret keeps `30 4 * * *`. Manual **Run workflow** always runs (the gate lets `workflow_dispatch` through). Collect does not play this game; it is a straight `0 */6 * * *`.

Hosted collect is `--ats-only` because `FetcherSession` still drags Playwright into the import graph even for HTTP. Portals and RSS are a local-after-200 privilege. Actions runners are goldfish: hosted state lives in **Neon** (`DATABASE_URL`). Local pytest and `--db` stay SQLite.

Telegram: Bot API `sendMessage`, plain text, no HTML parse mode. Cron workflows set `JOBDIGEST_REQUIRE_TELEGRAM=1` so a required send with a missing token or chat id **fails loud** instead of pretending it mailed you.

---

## Steal this (local)

Python 3.11+ (CI is 3.12). Then:

```bash
pip install -e ".[dev]"
cp .env.example .env
cp config/profile.example.yaml config/profile.yaml
cp config/companies.example.yaml config/companies.yaml
cp config/models.example.yaml config/models.yaml
cp config/portals.example.yaml config/portals.yaml
cp config/feeds.example.yaml config/feeds.yaml
```

Edit the copies. Git will ignore them. That is the point.

Flip `enabled: true` on companies you actually want. Probe the boards first (live HTTP, not CI):

```bash
python -m jobdigest.probe
```

You want `ok` or `empty_board`. Dead tokens stay disabled. Unimplemented ATS (`getro`, `workday`) are museum pieces: they must not pretend to be empty boards.

One-shot local loop (Git Bash on Windows is fine):

```bash
chmod +x start.sh && ./start.sh
```

That is `collect --ats-only` → `digest` → `sheet-sync` against `data/jobs.db`.

Or pull the levers yourself:

```bash
python -m jobdigest collect --ats-only --db data/jobs.db
python -m jobdigest score --db data/jobs.db
python -m jobdigest digest --db data/jobs.db
python -m jobdigest sheet-append --url https://example.com/job --db data/jobs.db
python -m jobdigest sheet-sync --db data/jobs.db
```

`python -m jobdigest collect` without `--ats-only` also walks `portals.yaml` (one HTTP `FetcherSession`, User-Agent `JobHunt/0.1`, robots, 3.5s same-host sleep) and `feeds.yaml` (`feedparser`). Enable a portal/feed only after you have seen a real 200 HTML/RSS body. SPAs and kill-list rows stay false forever.

If `DATABASE_URL` is set and you omit `--db`, you are on Neon even from your laptop. `--db` always wins and is SQLite.

---

## Hosted: GitHub as your unpaid intern

Actions checks out the **public stubs**, then writes real YAML from secrets onto the runner. Collect / digest only fetch a company, portal, or feed when that row’s `enabled:` is true. All-false YAML is a successful no-op, not a collect failure. Hosted collect is `--ats-only`, so portal/feed flags only matter on your machine.

Paste the **full file contents** of each local `config/*.yaml` into the matching secret. Companies you want collected must be `enabled: true` inside `COMPANIES_YAML`.

### GitHub secrets (the whole cursed altar)

| Secret | What it is |
|---|---|
| `OPENROUTER_API_KEY` | paid models. not `:free`. not `openrouter/auto`. |
| `DATABASE_URL` | Neon connection string |
| `TELEGRAM_BOT_TOKEN` | from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | your user/group id. stays out of git. |
| `JOBDIGEST_CRON` | five-field UTC cron. empty = `30 4 * * *` |
| `PROFILE_YAML` | full `config/profile.yaml` |
| `COMPANIES_YAML` | full `config/companies.yaml` |
| `MODELS_YAML` | full `config/models.yaml` |
| `PORTALS_YAML` | full `config/portals.yaml` |
| `FEEDS_YAML` | full `config/feeds.yaml` |
| `GOOGLE_SHEET_ID` | spreadsheet id |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | service-account JSON **text**, not a path |

Never put keys in workflow YAML. Never commit `.env`, `config/*.yaml` (except `*.example.yaml`), `data/*.db`, Google JSON, Telegram chat ids, or Neon URLs.

### BotFather (two minutes, then you are dangerous)

1. In Telegram, talk to [@BotFather](https://t.me/BotFather), create a bot, copy the token into `TELEGRAM_BOT_TOKEN` (`.env` locally, Actions secret hosted).
2. Message your bot, then call `getUpdates` (or use a user/group id you already know) and put that id in `TELEGRAM_CHAT_ID`.

### Neon

Create a Postgres project. Copy the connection string into `DATABASE_URL`. Empty `DATABASE_URL` keeps SQLite (`DATABASE_PATH` / `--db`). Schema and identity match: `apply_url_norm` UNIQUE, partial unique `(source, external_id)` when id is set, applications unique on `apply_url_norm`.

`workflow_dispatch` on digest sends a real Telegram when secrets exist (manual smoke). Default `pytest` is offline SQLite only.

---

## The Google Sheet (your war room)

Hosted **digest** runs `python -m jobdigest sheet-sync` after Telegram. It **appends** new offcampus guest jobs (the same relevant scored jobs as the digest, up to `digest_max` new URLs). If a link is already on the sheet, that row is left alone — including Applied, Status, Campus, and Notes you typed. Collect never writes the sheet.

Columns: Date, Company, Role, Link, Source, **Campus**, **Applied**, Status, Notes.

- Repo-written rows: `Campus=offcampus`, Applied and Status blank.
- **Oncampus** rows are yours. Add them by hand. The robot will not touch them.

`python -m jobdigest sheet-append --url …` still writes a local `applications` row and appends unsynced applies (local). Duplicate and utm-tagged URLs stay one DB row and one sheet row.

Locally, `GOOGLE_SERVICE_ACCOUNT_JSON` is a gitignored JSON **path**. On Actions, the secret is the **JSON file contents**; the workflow writes a temp file. Share the sheet with the service account `client_email` as Editor. Freeze row 1.

Missing Google creds fail **sheet-append** / **sheet-sync** only. Cron digest sets `JOBDIGEST_REQUIRE_SHEET=1`.

---

## Score + digest (the part with opinions)

`python -m jobdigest score` scores hard-filter `pass` jobs with OpenRouter JSON (`config/models.yaml` score model). `python -m jobdigest digest` requires a recent successful collect (within 26h); otherwise it prints `COLLECT FAILED` and exits 1. Score HTTP/parse failures print `SCORE FAILED` and skip the summarizer.

Digest prints Telegram plain text to stdout (Actions logs) and POSTs the same parts via Bot API when token and chat id are set. Quiet empty stdout after a good collect means nothing new to show. If the summarizer dies, Python bullets for the same guest list are the fallback. That is a feature. The mailman still walks.

Needs `OPENROUTER_API_KEY` in `.env`. Two paid models in local `config/models.yaml`. Collect stays LLM-free. The profile blurb sent to the scorer is skills / location / roles — not your GPA dumped into a prompt like a cry for help.

Hard filter (Python, at upsert, every time): reject staff/principal energy, reject 3+ YoE, reject US-remote visa novels, require an India-shaped location, require intern **or** new-grad title. The model only sees survivors.

---

## CLI (all the knives)

```
python -m jobdigest collect|score|digest|sheet-append|sheet-sync
```

Also `jobdigest` on PATH after install. No args prints help and walks away (exit 0).

---

## Tests (the part that keeps us honest)

```bash
pytest
```

Offline. Mocked ATS JSON. Injected LLM. Fake Telegram. Fake sheet. Constraint tests will scream if you try to smuggle Playwright, document a dead messenger, or forget `--ats-only` on hosted collect.

Live HTTP is a maintainer sport: `python -m jobdigest.probe`, then maybe a real collect. Not CI.

---

## License

MIT. Take the machine. Do not take the secrets. Do not take the LinkedIn ToS as a challenge.

Now go set `enabled: true` on something that deserves it.
