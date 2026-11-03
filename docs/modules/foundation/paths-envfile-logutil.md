# paths, envfile and logutil

- Source: `src/jobdigest/paths.py` (7 lines), `src/jobdigest/envfile.py` (48 lines), `src/jobdigest/logutil.py` (45 lines).
- Layer: foundation. Three tiny helpers used at process start-up and for locating files.
- Depends on: stdlib only (`envfile` imports `paths`).
- Tests: `tests/test_envfile.py` (`test_plain_export_comment_and_quotes`, `test_existing_env_not_overridden`). `paths` and `logutil` have no dedicated test (`grep` of `tests/` for `configure`, `get_logger`, `JOBDIGEST_LOG_LEVEL`, `repo_root` finds no test of them).

## Why these exist

- `paths.repo_root` gives one answer to "where is the checkout?" so data files (`data/jobs.db`, `data/boards_india.tsv`, `.env`) can be located independent of the current working directory.
- `envfile` is a ~40-line `.env` loader so the project does not depend on `python-dotenv`. It is called at the top of every entry point.
- `logutil` configures one named logger tree (`jobdigest.*`) with an optional file sink and an env-controlled level.

## Public interface at a glance

| Symbol | Signature | Purpose |
|---|---|---|
| `repo_root` | `def repo_root() -> Path` | Absolute repository root. |
| `_parse_value` | `def _parse_value(raw: str) -> str` | Value part of a `KEY=value` line (quotes, inline comments). |
| `parse_dotenv_line` | `def parse_dotenv_line(raw: str) -> tuple[str, str] \| None` | One line to `(key, value)` or `None`. |
| `load_dotenv` | `def load_dotenv(path: Path \| None = None) -> None` | Load a `.env` into `os.environ` without overriding. |
| `configure` | `def configure() -> None` | Install stderr (and optional file) handlers on logger `jobdigest`. |
| `get_logger` | `def get_logger(name: str) -> logging.Logger` | Child logger `jobdigest.<name>`. |

## Detailed walkthrough

### `repo_root`
**Location** `src/jobdigest/paths.py:6` · **Signature** `def repo_root() -> Path`
Returns `Path(__file__).resolve().parents[2]`. `__file__` is `<repo>/src/jobdigest/paths.py`; `parents[0]` is `jobdigest/`, `[1]` is `src/`, `[2]` is the repo root. Correct for an editable install (`pip install -e .`, as the workflow does) or running from source with `PYTHONPATH=src`; for a non-editable wheel install it would point inside `site-packages`, so default `data/` and `.env` locations assume a source checkout.
**Called by** `envfile.load_dotenv`, `config.discovered_boards_path`, `db/connection.py:174,176` (default DB path), `expand.py:195`, `sheet.py:91` (relative credentials path). (`config.config_dir` repeats the same `parents[2]` expression rather than calling it.)

### `_parse_value`
**Location** `src/jobdigest/envfile.py:9` · **Signature** `def _parse_value(raw: str) -> str`
Input is everything after the first `=`.
1. `val = raw.strip()`; empty gives `""`.
2. If the first char is `'` or `"`: find the matching closing quote with `val.find(quote, 1)`. If found, return the text between the quotes (so `#` inside quotes is kept and anything after the closing quote is dropped). If no closing quote exists, return the whole stripped value including the stray quote (`I='mismatched"` yields `'mismatched"`, asserted in the test).
3. Unquoted: scan for a `#` that has whitespace immediately before it (`idx > 0 and val[idx-1].isspace()`); return `val[:idx].rstrip()`. A `#` glued to text is kept (`E=a#b` stays `a#b`).
4. Else return `val`.
No escape processing, no `${VAR}` expansion, no multi-line values.

### `parse_dotenv_line`
**Location** `src/jobdigest/envfile.py:25` · **Signature** `def parse_dotenv_line(raw: str) -> tuple[str, str] | None`
1. `line = raw.strip()`; return `None` for blank lines, lines starting `#`, or lines without `=`.
2. `key, _, val = line.partition("=")`; `key = key.strip()`.
3. Strip an `export` prefix only if it is followed by whitespace: `key.startswith("export") and key[6:7].isspace()` then `key = key[6:].strip()`. So `export   C = spaced` gives key `C`, while `exportK=nope` keeps the literal key `exportK` (tested).
4. Empty key gives `None`; otherwise `(key, _parse_value(val))`.

### `load_dotenv`
**Location** `src/jobdigest/envfile.py:38` · **Signature** `def load_dotenv(path: Path | None = None) -> None`
1. `env_path = path` or `repo_root() / ".env"`. If it is not a file, return silently (normal in CI where secrets are real environment variables).
2. For each line of the file (UTF-8) parse with `parse_dotenv_line`; skip `None`.
3. `if key not in os.environ: os.environ[key] = val`. **Existing environment always wins** over the file, so GitHub Actions secrets or an interactive `export` override `.env`. An empty-valued key that already exists in the environment (even `""`) is also not overwritten.
**Called by** `cli.main` and also directly by `collect.run`, `score.run`, `digest.run`, `sheet.run`/`sync`, `contacts/run.py:run`, `bot.py` main loop; calling it repeatedly is harmless because of the no-override rule. `.env.example` in the repo root lists the variable names.

### `configure`
**Location** `src/jobdigest/logutil.py:11` · **Signature** `def configure() -> None`
Module state: `_LOGGER_NAME = "jobdigest"`, `_CONFIGURED = False`.
1. `raw = (os.environ.get("JOBDIGEST_LOG_LEVEL") or "INFO").strip().upper()`; `level = getattr(logging, raw, None)`; if that is not an `int` (an unknown name, or a non-integer attribute of `logging` such as `BASIC_FORMAT`) fall back to `logging.INFO`.
2. `root = logging.getLogger("jobdigest")`; `root.setLevel(level)`; `root.propagate = False` so records are not also emitted by the Python root logger (prevents duplicates).
3. If already configured: only update each existing handler's level to the new level and return. Handlers are never added twice, so calling `configure()` from `cli.main` and again inside `collect.run` etc. is safe.
4. First call: build `Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")`, add a `StreamHandler(sys.stderr)`.
5. If `JOBDIGEST_LOG_FILE` is non-empty add a `FileHandler(path, encoding="utf-8")` with the same level and format. A path that cannot be opened raises `OSError` at this point. The file is appended to.
6. Set `_CONFIGURED = True`.
Logs go to stderr so stdout stays reserved for program output such as the digest text or the `due=...` lines from the cron gate.

### `get_logger`
**Location** `src/jobdigest/logutil.py:44`. Returns `logging.getLogger(f"jobdigest.{name}")`, e.g. `get_logger("collect")` gives `jobdigest.collect`. Modules use it as `log = get_logger("collect")`. `config.py` and a few others call `logging.getLogger("jobdigest.config")` directly, which lands in the same tree. Child loggers have no level of their own, so they inherit from `jobdigest` and reach its handlers.

## Failure modes and edge cases
- Before `configure()` runs (e.g. a library import emits a warning) records on `jobdigest.*` fall to Python's "last resort" handler (WARNING and above to stderr, bare format).
- `JOBDIGEST_LOG_LEVEL=debug` works (upper-cased); `JOBDIGEST_LOG_LEVEL=nonsense` silently becomes INFO.
- A `.env` line like `KEY="a" # note` returns `a`; `KEY=a b` returns `a b`.

## How to modify / extend safely
- Need `${VAR}` expansion or multi-line values: extend `_parse_value` and add cases to `tests/test_envfile.py`.
- Adding a new entry point: call `load_dotenv()` then `configure()` first, as `cli.main` does (see [cli-and-entrypoints.md](cli-and-entrypoints.md)).
