# jobdigest documentation

A technical knowledge base for the `jobdigest` repository: a pipeline that gathers entry-level engineering jobs in India,
filters and ranks them, and delivers the best to Telegram, with a bot that answers button taps. These documents were written against the
source (not the README) and verified against it; see the [coverage report](reference/coverage-report.md) for exactly what is and is not
covered. Documented against the working tree of 2026-11-03.

## Start here: recommended reading order

A path from "what is this" to "I can change it", about a day of reading.

| Step | Read | You will learn |
|---|---|---|
| 1 | [What is this project?](onboarding/01-what-is-this.md) | purpose, the daily flow, vocabulary |
| 2 | [Repository tour](onboarding/02-repo-tour.md) | where every file lives and why |
| 3 | [Architecture overview](architecture/overview.md) | the system on one page, components, stage order |
| 4 | [Development setup](onboarding/03-setup.md) | install, run tests, run locally |
| 5 | [Your first trace](onboarding/04-first-trace.md) | run the whole pipeline on fake data and follow it into the code |
| 6 | [Data model and job lifecycle](architecture/data-model.md) | tables, identity, states |
| 7 | The workflows, in order: [collect](workflows/collect-run.md) -> [score and digest](workflows/scoring-and-digest.md) -> [button tap](workflows/telegram-button-tap.md) | end-to-end execution traces with function names |
| 8 | [Debugging guide](onboarding/05-debugging.md) | symptoms -> causes, useful SQL |
| 9 | [Adding features](onboarding/06-adding-features.md) and [testing guide](onboarding/07-testing-guide.md) | recipes and safety rails |
| 10 | Module reference pages (below) as needed | per-function internals |

## Map of the documentation

### Onboarding (`onboarding/`)
[what is this](onboarding/01-what-is-this.md) · [repo tour](onboarding/02-repo-tour.md) · [setup](onboarding/03-setup.md) ·
[first trace](onboarding/04-first-trace.md) (+ runnable [`first_trace.py`](onboarding/first_trace.py)) · [debugging](onboarding/05-debugging.md) ·
[adding features](onboarding/06-adding-features.md) · [testing guide](onboarding/07-testing-guide.md)

### Architecture (`architecture/`) - concepts and decisions
[overview](architecture/overview.md) · [layers and dependencies](architecture/layers-and-dependencies.md) · [data model](architecture/data-model.md) ·
[configuration and secrets](architecture/configuration-and-secrets.md) · [deployment and scheduling](architecture/deployment-and-scheduling.md) ·
[error handling and observability](architecture/error-handling-and-observability.md) · [design decisions](architecture/design-decisions.md) ·
[known issues and limitations](architecture/known-issues-and-limitations.md)

### Workflows (`workflows/`) - entry point to outcome, step by step
[collect run](workflows/collect-run.md) · [scoring and digest](workflows/scoring-and-digest.md) · [Telegram button tap](workflows/telegram-button-tap.md) ·
[contacts and mail](workflows/contacts-and-mail.md) · [sheet sync](workflows/sheet-sync.md) · [scheduled pipeline](workflows/scheduled-pipeline.md) ·
[board discovery](workflows/board-discovery.md)

### Module reference (`modules/`) - function-level internals
| Subsystem | Pages |
|---|---|
| Foundation | [config](modules/foundation/config.md) · [paths, envfile, logutil](modules/foundation/paths-envfile-logutil.md) · [normalize](modules/foundation/normalize.md) · [CLI and entry points](modules/foundation/cli-and-entrypoints.md) · [cron gate](modules/foundation/cron-gate.md) |
| Storage | [overview](modules/storage/overview.md) · [connection and Postgres shim](modules/storage/connection-and-postgres-shim.md) · [schema and migrations](modules/storage/schema-and-migrations.md) · [jobs](modules/storage/jobs.md) · [runs, scores, errors](modules/storage/runs-scores-errors.md) · [applications](modules/storage/applications.md) · [contacts and digest queries](modules/storage/contacts-and-digest-queries.md) |
| Sources: ATS | [overview](modules/sources/ats-overview.md) · [base and HTTP client](modules/sources/ats-base-and-http-client.md) · [Greenhouse](modules/sources/ats-greenhouse.md) · [Lever](modules/sources/ats-lever.md) · [Ashby](modules/sources/ats-ashby.md) · [Workable](modules/sources/ats-workable.md) · [SmartRecruiters](modules/sources/ats-smartrecruiters.md) · [BambooHR](modules/sources/ats-bamboohr.md) · [probe](modules/sources/probe.md) |
| Sources: other | [portals overview](modules/sources/portals-overview.md) · [session and robots](modules/sources/portals-session-and-robots.md) · [parsers](modules/sources/portals-parsers.md) · [feeds](modules/sources/feeds.md) · [marketplaces](modules/sources/marketplaces.md) · [discover](modules/sources/discover.md) · [expand](modules/sources/expand.md) · [research scripts](modules/sources/research-scripts.md) |
| Collection and filtering | [collect orchestrator](modules/collection/collect-orchestrator.md) · [hard filter](modules/filtering/hard-filter.md) · [legitimacy](modules/filtering/legitimacy.md) |
| Ranking and LLM | [score (LLM)](modules/ranking/score-llm.md) · [OpenRouter client](modules/ranking/openrouter.md) · [fit and tiers](modules/ranking/fit-and-tiers.md) · [rank and selection](modules/ranking/rank-and-selection.md) · [summarize](modules/ranking/summarize.md) · [blurb](modules/ranking/blurb.md) |
| Delivery | [digest](modules/delivery/digest.md) · [Telegram](modules/delivery/telegram.md) · [bot](modules/delivery/bot.md) · [follow-ups and health](modules/delivery/followups-and-health.md) · [sheet](modules/delivery/sheet.md) · [email drafts](modules/delivery/email-drafts.md) |
| Contacts | [overview](modules/contacts/overview.md) · [pipeline and run](modules/contacts/pipeline-and-run.md) · [domain and pattern](modules/contacts/domain-and-pattern.md) · [GitHub and JD](modules/contacts/github-and-jd.md) · [rank and email](modules/contacts/rank-and-email.md) |

### Testing (`testing/`) and infrastructure (`infra/`)
[strategy](testing/strategy.md) · [test map](testing/test-map.md) · [e2e and fixtures](testing/e2e-and-fixtures.md) ·
[GitHub workflows](infra/github-workflows.md) · [config files](infra/config-files.md) · [packaging and scripts](infra/packaging-and-scripts.md)

### Reference (`reference/`)
[symbol index](reference/symbol-index.md) (generated, every function with line numbers) · [glossary](reference/glossary.md) ·
[coverage report](reference/coverage-report.md)

## How these docs are organised and kept honest

* **Layers:** architecture pages explain *why*; workflow pages trace *what runs when*; module pages explain *how each function works*
  (location, signature, callers/callees, step-by-step internals, failure modes). Pages link to each other instead of repeating content.
* **Function entries** use headings like ``### `upsert_job` `` so anchors are predictable (`modules/storage/jobs.md#upsert_job`).
* **Tooling in `docs/_tools/`:** `build_reference.py` regenerates the symbol index from the AST; `check_docs.py` verifies internal links,
  `path:line` references, every `Location` line against the real `def`/`class`, Mermaid syntax (with the real Mermaid parser) and symbol coverage.
  Run both after changing code or docs:

  ```bash
  python docs/_tools/build_reference.py
  python docs/_tools/check_docs.py            # add --mermaid-dir DIR (npm i mermaid jsdom) to validate diagrams
  ```
* **Conventions:** source references are `path:line`; examples shown as "real output" were produced by running the code offline.
