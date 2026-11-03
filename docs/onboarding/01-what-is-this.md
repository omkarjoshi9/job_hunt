# 1. What is this project?

**jobdigest** finds fresh job postings from public sources, keeps only genuine *entry-level engineering roles in India*,
ranks them, and sends the best few to one person on Telegram each morning. That person can tap buttons on each message to
save, hide, or mark a job as applied, ask for a short cover note, or get a draft email to a recruiter. The program never applies
for anything and never sends email.

## A day in the life of the system

1. **Early morning (GitHub Actions).** A scheduled workflow wakes up, fetches jobs from the configured company job boards
   (Greenhouse, Lever, Ashby and others - dozens of curated ones plus any found by board discovery), a few portals and an RSS feed, and stores them in a database.
2. Every job passes through **hard filters** written in Python: wrong seniority, too much experience, outside India,
   not an engineering role, and so on are discarded immediately.
3. The remaining jobs are **scored** by an LLM (relevance) and by deterministic code (fit with the candidate's skills, level,
   freshness, company). The two are blended.
4. A **selection step** picks the top few with rules that keep the message varied and new (one job per company, no recently
   sent companies, nothing stale).
5. The digest is **sent to Telegram** as numbered cards with buttons.
6. The same run **guesses contacts** for those jobs and **copies them into a Google Sheet**.
7. During the day a **bot** answers button taps.

## Vocabulary you will see everywhere

| Term | Meaning |
|---|---|
| ATS | Applicant tracking system: the hosted job board a company uses (Greenhouse, Lever...). We read their public JSON |
| Board | One company's job board at one ATS (`ats` + `token`) |
| Pool | All jobs fetched in a run |
| Hard filter | Code that rejects a job at ingest time |
| Fit / quality | Deterministic score / its blend with the LLM score (`0.65 fit + 0.35 LLM`) |
| Digest / guest | The Telegram message / a job selected for it (the code calls these *guest rows*) |
| Slot | Which scheduled send is due: `digest` (daily) or `urgent` (weekday, exceptional jobs only) |
| Tombstone | Marking a job closed after it disappears from its board twice |

The full glossary is in [reference/glossary](../reference/glossary.md).

## What it is built with

Python 3.11+ (3.12 in CI), `httpx` for HTTP, SQLite or Postgres for storage, OpenRouter for LLM calls, Telegram Bot API for
delivery, `gspread` for Google Sheets, Scrapling/Playwright for the few HTML portals, and GitHub Actions for scheduling. There is
no web server and no frontend.

## What to read next

* [2. Repository tour](02-repo-tour.md) - where everything lives.
* [Architecture overview](../architecture/overview.md) - the whole system on one page.
* [3. Setup](03-setup.md) - get it running on your machine.
