# 2. Repository tour

```
job_hunt/
├── src/jobdigest/            the Python package (all application code)
│   ├── cli.py, __main__.py   command-line entry points
│   ├── collect.py            fetch everything and store it
│   ├── score.py, digest.py   LLM scoring; selection and Telegram digest
│   ├── hard_filter.py        regex gates that reject non-fits
│   ├── fit.py, rank.py, tiers.py   deterministic score, ranking, company tiers
│   ├── legitimacy.py         structural "suspicious posting" checks
│   ├── telegram.py, bot.py   sending messages; answering button taps
│   ├── sheet.py              Google Sheet mirror
│   ├── summarize.py, blurb.py, email_draft.py, openrouter.py   LLM-facing helpers
│   ├── followups.py, health.py     follow-up reminders; collect health report
│   ├── config.py, paths.py, envfile.py, logutil.py, normalize.py   foundations
│   ├── cron.py               scheduling gate for GitHub Actions
│   ├── expand.py, discover.py, probe.py   finding and checking boards
│   ├── ats/                  one adapter per ATS vendor + shared base and HTTP client
│   ├── portals/              HTML portal scraping (Scrapling) and parsers
│   ├── feeds.py, marketplaces/     RSS lane; optional JobSpy lane
│   ├── contacts/             guessing recruiter / hiring-manager contacts
│   ├── db/                   storage: connection, schema, jobs, runs, applications, ...
│   └── templates/email/      Jinja templates for outreach drafts
├── tests/                    pytest suite (offline) + fixtures/
├── config/                   YAML configuration (examples are tracked)
├── data/                     board lists (TSV); SQLite files at runtime
├── scripts/                  one-off research scripts for board discovery
├── .github/workflows/        pipeline.yml, bot.yml, ci.yml
├── start.sh                  run the pipeline locally
├── pyproject.toml            packaging, dependencies, pytest config
└── docs/                     this documentation
```

## Where to look for what

| I want to... | Start in | Then read |
|---|---|---|
| change which jobs are rejected | `hard_filter.py` | [hard-filter](../modules/filtering/hard-filter.md) |
| change how jobs are ranked | `fit.py`, `rank.py` | [fit-and-tiers](../modules/ranking/fit-and-tiers.md), [rank-and-selection](../modules/ranking/rank-and-selection.md) |
| change the LLM prompt | `score.py` (`_system_prompt`) | [score-llm](../modules/ranking/score-llm.md) |
| add or fix a job source | `ats/`, `portals/`, `feeds.py` | [sources](../modules/sources/ats-overview.md) |
| change what the digest looks like | `summarize.py` (`render_card`), `digest.py` | [summarize](../modules/ranking/summarize.md), [digest](../modules/delivery/digest.md) |
| change what a button does | `bot.py`, `telegram.py` | [bot](../modules/delivery/bot.md) |
| change tables or queries | `db/` | [storage](../modules/storage/overview.md) |
| change the schedule or deployment | `.github/workflows/`, `cron.py` | [deployment](../architecture/deployment-and-scheduling.md) |
| understand a failing test | `tests/` | [test map](../testing/test-map.md) |

## Three structural rules worth knowing early

1. **Stages talk only through the database.** `collect` writes rows, `digest` reads them. You can run any stage alone.
2. **Everything external is a parameter.** Functions like `collect.run` take the HTTP client, `digest.run` takes the LLM and
   Telegram functions. Tests pass fakes; production passes nothing and gets the real ones.
3. **Imports flow downward.** Entry points import stages, stages import domain logic, domain logic imports foundations. See
   [layers and dependencies](../architecture/layers-and-dependencies.md).

## Files you should skim first (about 1 hour)

1. `src/jobdigest/cli.py` - the commands. 2. `src/jobdigest/collect.py` - the orchestrator. 3. `src/jobdigest/hard_filter.py` -
the heart of "what counts as a good job". 4. `src/jobdigest/digest.py` - end of the pipeline. 5. `tests/test_pipeline_e2e.py` - the
whole flow in one test file. The page [first trace](04-first-trace.md) walks through them with a runnable script.
