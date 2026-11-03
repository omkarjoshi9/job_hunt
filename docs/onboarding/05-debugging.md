# 5. Debugging guide

A symptom-first guide. Always start with the logs, then the database, then the code.

## Step 0: turn up the logs

```bash
JOBDIGEST_LOG_LEVEL=DEBUG python -m jobdigest collect --db data/jobs.db
```

Logs go to **stderr**; the intentional output (summary lines, digest text) goes to stdout. On GitHub Actions open the failed step
in the run log. Useful lines: `collect start ... yaml_enabled=`, `company fetch done id=... status=... jobs=...`,
`job insert|update ... hard_status=... hard_reason=...` (DEBUG), `score batch fail ...`, `digest guests=N titles=[...]`.

## Step 1: ask the database

SQLite files open in any SQLite client (`sqlite3 data/jobs.db`, DB Browser). The same SQL works on Postgres.

```sql
-- What happened in recent runs?
SELECT id, kind, status, error, started_at, stats_json FROM runs ORDER BY id DESC LIMIT 10;

-- Why are jobs being rejected? (distribution of reasons)
SELECT hard_reason, COUNT(*) FROM jobs GROUP BY hard_reason ORDER BY 2 DESC;

-- Which sources failed in the last collect?
SELECT portal_name, error_class, http_status, detail FROM portal_errors
WHERE run_id = (SELECT MAX(id) FROM runs WHERE kind = 'collect');

-- Jobs waiting for a score
SELECT COUNT(*) FROM jobs j WHERE hard_status='pass' AND is_active=1 AND canonical_job_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM scores s WHERE s.job_id=j.id AND s.stale=0);

-- What was sent and when?
SELECT company, title, notify_kind, notified_at FROM jobs WHERE notified_at IS NOT NULL ORDER BY notified_at DESC LIMIT 20;
```

## Symptom -> cause -> where to look

| Symptom | Likely cause | Check |
|---|---|---|
| Log says `falling back to example config` | `config/profile.yaml` or `companies.yaml` is missing; the example is used | create the file ([setup](03-setup.md)); in CI check *Write private config* |
| `collect ok fetched=0` | no company enabled, or all boards empty/dead | `company fetch id=` lines; `companies.yaml` `enabled: true` |
| `COLLECT FAILED` on Telegram | every enabled source failed | `portal_errors` query above; network/blocked IP |
| Digest says `COLLECT FAILED` but collect looked fine | latest collect run is not `ok`, **or older than 26 hours** | `runs` query; the scheduler skipped a day |
| Digest says `SCORE FAILED` | LLM batch failed or `OPENROUTER_API_KEY` missing | `score batch fail` log lines; the key |
| No digest message at all, exit 0 | nothing met the quality bar / age / cooldown rules, or credentials missing locally | `digest guests=0`; `digest empty skip send`; `telegram skip missing credentials` |
| The same company keeps appearing | state is not persisted between runs | is `DATABASE_URL` set, or does the state cache restore? ([deployment](../architecture/deployment-and-scheduling.md)) |
| A job you expected is missing | it was rejected, closed, duplicate, applied-to, or already sent | `SELECT hard_status, hard_reason, is_active, canonical_job_id, notified_at FROM jobs WHERE title LIKE '%...%'` |
| Buttons do nothing | no bot process is polling | is `bot.yml` running / `python -m jobdigest.bot`? ([button tap](../workflows/telegram-button-tap.md)) |
| Button says "Job not found" | bot uses a different database than the digest | same `DATABASE_URL` for both |
| `TELEGRAM FAILED ...` | bad token/chat id or Telegram error | the message after it; token never appears in logs |
| Sheet rows not appearing | missing credentials (skipped) or a sheet error | `sheet-sync skip` vs `sheet-sync error ...` |
| Portal returns 0 cards | selector drift or blocked page | `portal_errors.error_class` (`parse`, `cloudflare`); the parser in `portals/` |

## Reproducing a single decision offline

Reject reasons are the most common question. Call the filter directly:

```python
from jobdigest.config import load_profile
from jobdigest.hard_filter import hard_filter

p = load_profile()          # example profile unless config/profile.yaml exists
print(hard_filter(p, title="Software Engineer", location_raw="Pune, India",
                  description_text="We hire freshers.", company="Acme"))
# ('pass', 'ok')
```

Rank and fit work the same way: `fit.fit_score(row, profile, now)` and `fit.quality_score(...)` take a dict (or `sqlite3.Row`) with
`title`, `company`, `description_text`, `ats`, `posted_at`/`first_seen_at`, `score`. See
[fit-and-tiers](../modules/ranking/fit-and-tiers.md) for worked numeric examples.

## Rules of thumb

* **A filter change only affects jobs that are seen again.** `hard_status` is recomputed in `upsert_job`; re-run `collect` after editing
  `hard_filter.py` or the profile.
* **Scores are cached per `(model, prompt_version)`.** Change the prompt and bump `prompt_version` in `models.yaml`, or old scores
  are reused.
* **"Why is it slow?"** Almost always the network: sequential board fetches with a 0.2-0.4 s pause each, plus portal browsers.
  Use `--ats-only`, `--skip-portals`, or `--portal-fetch http`.
* **Postgres vs SQLite differences** are confined to `db/connection.py` and `db/schema.py`; see
  [storage](../modules/storage/connection-and-postgres-shim.md).
* Known quirks that look like bugs but are documented behaviour: [known issues](../architecture/known-issues-and-limitations.md).

Next: [6. Adding features](06-adding-features.md).
