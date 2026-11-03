# 4. Your first trace

Run the whole pipeline once on fake data, then follow each printed line back to the code that produced it. The demo script is
[`first_trace.py`](first_trace.py); it uses a fake ATS board, a fake LLM and a fake Telegram, so it needs no network and no secrets.

```bash
python docs/onboarding/first_trace.py 2>/dev/null
```

`2>/dev/null` hides the log lines (they go to stderr). You will see this (real output):

```
collect ok fetched=3 inserted=3 updated=0 skipped=0 pass=1 reject=2 missed=0 tombstoned=0
collect exit code: 0

hard filter verdicts:
  Backend Software Engineer, New Grad      pass    ok
  Senior Staff Engineer                    reject  title_reject
  HR Intern - Talent Acquisition           reject  function
1. Acme — Backend Software Engineer, New Grad | Bengaluru, India | 86 | <1h | ats | https://boards.greenhouse.io/acme/jobs/1
Why you: Python backend, FastAPI and AWS match

digest exit code: 0

scores table:
  {'job_id': 1, 'score': 80.0, 'relevant': 1}

Telegram calls made:
  sendMessage 1. Acme — Backend Software Engineer, New Grad | Bengaluru, India | 86 | ...
  sendMessage Tap a number to open a card.

notified: [{'title': 'Backend Software Engineer, New Grad', 'notify_kind': 'digest'}]
```

## Walk-through: each line and its code

**`collect ok fetched=3 inserted=3 ... pass=1 reject=2`** - `collect.run` called the fake board through
`_collect_ats` -> `ats.greenhouse` -> `FetchResult`, then `_upsert_jobs` -> `db.jobs.upsert_job` for each of the 3 jobs. The summary
line is printed by `collect.run` via `_summary_line`. Trace: [collect-run](../workflows/collect-run.md).

**The verdict table** - read straight from the `jobs` table. `hard_status` and `hard_reason` were set by `hard_filter.hard_filter`
inside `upsert_job`:

* "Senior Staff Engineer" -> `title_reject`: the profile's `title_reject_regex` matched `staff|senior`.
* "HR Intern - Talent Acquisition" -> `function`: it passed the intern check, but the code-level function gate saw `hr`/`talent` and no engineering word
  ([function_ok](../modules/filtering/hard-filter.md)).
* The new-grad backend role passed every gate in order: title reject, years of experience, remote-US, India location, entry-level
  title, function, seniority, level.

**The card line** - `digest.run` first called `score.run`, which sent only the one passing job to the (fake) LLM and stored
`scores(score=80, relevant=1)`. Then `db.guest_rows` ranked and selected it, `digest._guest_dicts` converted it to a dict, and
`summarize.render_card` formatted it. Notice the card shows **86**, not 80: the displayed score is the *quality*
(`0.65 * fit + 0.35 * 80`), and this role earned a high deterministic fit (core backend title, "new grad", Python/FastAPI/AWS
skills in the text, fresh posting). See [fit-and-tiers](../modules/ranking/fit-and-tiers.md) and
[scoring-and-digest](../workflows/scoring-and-digest.md).

**Two `sendMessage` calls** - `_deliver_digest` posted the digest text through `telegram.deliver` (the fake recorded the call), then
`send_guest_pager` posted the "Tap a number to open a card." message with the numbered buttons. The `notified` line proves
`stamp_notified` ran after a successful send.

## Exercises

Edit a copy of the script and re-run it after each change. Predict the result first.

1. **Break the level gate.** In job 1, remove the sentence `We hire freshers.` from the body and drop `, New Grad` from the title
   so it reads `"Backend Software Engineer"`. Predict the verdict, then run it: the job is rejected with `level_unknown`, because a title
   with no intern/new-grad signal needs entry-level evidence in the job text ([entry_level](../modules/filtering/hard-filter.md)).
   Put `We hire freshers.` back and it passes again.
2. **Add a duplicate.** Add the same role again with a different `id` and URL. Which row becomes canonical
   (`canonical_job_id`)? Verified: the lower id keeps `canonical_job_id = NULL` and the newer row points at it. Query it with SQLite after the run. See [link_duplicate_jobs](../modules/storage/jobs.md#link_duplicate_jobs).
3. **Make the board empty.** Keep the `jobs` list in a variable, run `collect.run` once normally, set the list to `[]`, and run it twice more.
   After the first empty run the job has `miss_count = 1` and is still active; after the second it has `is_active = 0` and
   `close_reason = 'unseen_board'` (verified). Query `SELECT id, is_active, miss_count, close_reason FROM jobs`.
4. **Run digest twice.** Is anything sent the second time? Why not? (`notified_at`.)
5. **Raise the bar.** After the loop that copies `profile`/`models` into `cfg`, append `digest_min_quality: 95` to the copied profile:
   `(cfg / "profile.yaml").write_text((cfg / "profile.yaml").read_text() + "digest_min_quality: 95\n")`. The same job (quality 86) no longer
   qualifies: `digest` exits 0, sends nothing, and `notified` stays empty. This is the "fewer, better jobs" rule in
   [select_diverse](../modules/ranking/rank-and-selection.md).

## Where to go from here

* The same flow with real failure handling: [scheduled-pipeline](../workflows/scheduled-pipeline.md).
* What happens when you tap a button: [telegram-button-tap](../workflows/telegram-button-tap.md).
* The tests that cover this flow: `tests/test_pipeline_e2e.py` ([e2e-and-fixtures](../testing/e2e-and-fixtures.md)).
* Next onboarding page: [5. Debugging](05-debugging.md).
