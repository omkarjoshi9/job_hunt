# Telegram transport (`telegram.py`)

- Source: `src/jobdigest/telegram.py` (372 lines)
- Layer: delivery transport. The *only* module that talks to the Telegram Bot API; a test enforces that (`tests/test_telegram.py::test_live_telegram_url_only_in_telegram_module` fails if `api.telegram.org` appears in any other `src/jobdigest` file).
- Depends on: `httpx`, `logutil`. No database access.
- Used by: [`digest.py`](digest.md) (`deliver`, `send_guest_pager`, `send_action_card`, `would_post`, `split_message`), [`bot.py`](bot.md) (everything else), tests.
- Tests: `tests/test_telegram.py`, `tests/test_digest_telegram.py`, `tests/test_bot.py`, `tests/test_bot_buttons.py`.

## Why this exists / responsibility

One place that knows: where credentials come from, how to post JSON to the Bot API without leaking the bot token in logs or exceptions, how to cut messages to Telegram's size limit, how to build inline keyboards with a compact `callback_data` protocol, and how local/dry-run mode behaves when credentials are absent.

Everything is **plain text** (no `parse_mode`), so job titles containing `_`, `*` or `<` never break formatting (`tests/test_telegram.py::test_send_message_posts_plain_text_no_parse_mode`).

## Public interface at a glance

| Symbol | Signature | Purpose |
|---|---|---|
| `TelegramError` | `class TelegramError(Exception)` | Any send failure; messages are token-free. |
| `split_message` | `(text, limit=3800) -> list[str]` | Cut on blank lines to `<= limit`. |
| `credentials` | `() -> tuple[str, str]` | `(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)`, stripped. |
| `would_post` | `(http_post=None) -> bool` | Credentials present? |
| `require_telegram` | `() -> bool` | `JOBDIGEST_REQUIRE_TELEGRAM` in `{1,true,yes}`. |
| `bot_post` | `(method, payload, *, token, http_post=None, timeout=30.0) -> dict` | The one HTTP call; error sanitizing. |
| `send_message` | `(text, *, token, chat_id, http_post=None, timeout=30.0, reply_markup=None) -> None` | `sendMessage`. |
| `get_updates` | `(*, token, offset=0, timeout=25, http_post=None, allowed_updates=None) -> list` | Long-poll `getUpdates`. |
| `answer_callback_query` | `(callback_query_id, *, token, http_post=None, text="") -> None` | Stop the button spinner (optionally toast text). |
| `edit_message_text` | `(chat_id, message_id, text, *, token, http_post=None, reply_markup=None) -> None` | Rewrite a message in place. |
| `pager_markup` | `(guest) -> dict` | Numbered `o:<id>` buttons. |
| `action_markup` | `(job_id, apply_url, *, status_buttons=True, why_me=True, mail=True) -> dict` | Card keyboard. |
| `send_blurb` | `(text, *, token, chat_id, http_post=None) -> None` | Thin wrapper over `send_message`. |
| `deliver` | `(text, *, required=False, http_post=None) -> bool` | Send possibly multi-part text. |
| `send_guest_pager` | `(guest, *, required=False, http_post=None) -> bool` | Send the numbered pager message. |
| `send_action_card` | `(text, *, job_id, apply_url, required=False, http_post=None, status_buttons=True, why_me=True, mail=True) -> bool` | Send one card with buttons. |
| constants | `TELEGRAM_API_URL`, `SPLIT_LIMIT=3800`, `ALLOWED_UPDATES=["message","callback_query"]`, `PAGER_TEXT="Tap a number to open a card."` | |

## Key concepts

- **Bot API over HTTPS POST.** Each method is `POST https://api.telegram.org/bot<TOKEN>/<method>` with a JSON body. Because the token is *in the URL*, any logged URL or exception string that includes the request URL leaks the token. This is why `bot_post` rewrites errors (see below).
- **Inline keyboard and `callback_data`.** A message can carry buttons. A button either opens a URL (`url`) or sends `callback_data` (a string of at most 64 bytes) back to the bot as a `callback_query` update when tapped. This code encodes `<kind>:<job_id>`.
- **Long polling.** `getUpdates` with a `timeout` makes Telegram hold the request open until an update arrives or the timeout expires; `offset` acknowledges everything below it. See [bot.md](bot.md#poll_once).
- **Injected `http_post`.** Every function takes `http_post: Callable | None`. If `None`, `_httpx_post` is used; tests pass a recorder that returns a fake response. The call convention is `post(url, json=payload, timeout=timeout)`.

### Callback data protocol

| Prefix | Meaning | Created by | Handled in |
|---|---|---|---|
| `o:<job_id>` | open: send the full card with its buttons | `pager_markup` | `bot._handle_callback` kind `o` |
| `s:<job_id>` | save | `action_markup` | status kind `s` -> `"saved"` |
| `a:<job_id>` | applied | `action_markup` | kind `a` -> `"applied"` |
| `h:<job_id>` | hide | `action_markup` | kind `h` -> `"hidden"` |
| `w:<job_id>` | "Why me?" cover blurb | `action_markup` | `_handle_why_me` |
| `m:<job_id>` | "Mail" cold-email draft | `action_markup` | `_handle_mail` |

The bot validates with `^(o|s|a|h|w|m):(\d+)$`. `tests/test_telegram.py::test_callback_data_fits_64_bytes` (and `_w_`/`_m_` variants) check that even a job id of `10**18` stays within 64 bytes.

## Detailed walkthrough

### `split_message`

**Location** `src/jobdigest/telegram.py:26` · **Signature** `def split_message(text: str, limit: int = SPLIT_LIMIT) -> list[str]`

**Purpose** Telegram rejects messages over 4096 characters; the limit here is 3800 for headroom. Cards are separated by blank lines, so cutting on `"\n\n"` keeps each card whole.

**Called by** `deliver`, `digest._print_digest`. 

**Steps**

1. `stripped = text.strip("\n")`; if `len(stripped) <= limit` return `[stripped]`.
2. `blocks = stripped.split("\n\n")`; greedy packing into `buf`: `candidate = block if not buf else f"{buf}\n\n{block}"`; if it fits, extend `buf`.
3. If it does not fit and `buf` is non-empty, flush `buf` to `parts`. Then if the block alone fits, it starts the new `buf`.
4. A single block longer than `limit` is hard-cut into `limit`-sized slices (`block[start:start+limit]`); this is the only case where text is split mid-block (a card longer than 3800 chars cannot be kept whole).
5. Flush the remainder; `return parts or [stripped[:limit]]`.

**Example (verified)** `split_message("a"*10 + "\n\n" + "b"*10, 15)` -> `['aaaaaaaaaa', 'bbbbbbbbbb']`.

Tests: `test_split_message_keeps_short_text`, `_breaks_long_block`, `_keeps_two_line_cards_on_double_newline`.

### `credentials`, `would_post`, `require_telegram`

- `credentials()` (`:53`): reads `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from the environment, `.strip()`ped; `("", "")` if unset. Read at each call, never cached, so tests can `monkeypatch.setenv`.
- `would_post(http_post=None)` (`:59`) <a id="would_post"></a>: `bool(token and chat_id)`. The `http_post` argument is accepted and **ignored** ("for call-site symmetry", per its docstring). Consequence: injecting a fake `http_post` without setting credentials still counts as "would not post". Callers use it to decide whether to `stamp_notified`.
- `require_telegram()` (`:65`): true when `JOBDIGEST_REQUIRE_TELEGRAM` lowercases to `1`, `true` or `yes`. Hosted cron sets it so missing credentials are a failure rather than a silent skip.

### `_httpx_post` and `_response_json`

`_httpx_post(url, json=None, timeout=30.0)` (`:70`) opens a short-lived `httpx.Client(timeout=timeout)` per call and POSTs the JSON. `_response_json(resp)` (`:75`) is deliberately duck-typed so fake responses work: if `resp.json` is callable, call it and accept a `dict`; on `ValueError` log at debug and fall through; otherwise parse `resp.text` with `json.loads`; anything non-dict becomes `{}`.

### `bot_post`

**Location** `src/jobdigest/telegram.py:94` · **Signature** `def bot_post(method: str, payload: dict[str, Any], *, token: str, http_post: HttpPost | None = None, timeout: float = 30.0) -> dict`

**Purpose** The only function that issues HTTP. Normalizes every failure into `TelegramError` without token leakage.

**Called by** `send_message`, `get_updates`, `answer_callback_query`, `edit_message_text`.

**Steps**

1. `url = TELEGRAM_API_URL.format(token=token, method=method)`; `post = http_post if http_post is not None else _httpx_post`.
2. `resp = post(url, json=payload, timeout=timeout)` inside `try`.
3. Error sanitization:

```python
except httpx.HTTPError as exc:
    # httpx messages can embed the request URL, which contains the bot token.
    log.error("telegram send fail transport")
    raise TelegramError(f"transport error: {type(exc).__name__}") from None
except Exception as exc:
    log.error("telegram send fail")
    raise TelegramError(f"send error: {type(exc).__name__}") from None
```

   Only the exception *class name* is kept, and `from None` suppresses chaining so the original (token-bearing) exception cannot appear in a traceback.
4. `status = int(getattr(resp, "status_code", 0) or 0)`; any status other than `200` logs and raises `TelegramError(f"HTTP {status}: {body}")` where `body` is the first 500 characters of `resp.text`. Telegram's error bodies describe the problem (e.g. "message is too long") and do not contain the token.
5. Success: log the method and status, return `_response_json(resp)`.

Note: a 200 response whose JSON says `"ok": false` is not detected here; Telegram uses non-200 for errors in practice.

### `send_message`, `get_updates`, `answer_callback_query`, `edit_message_text`, `send_blurb`

- `send_message` (`:122`): payload `{"chat_id", "text"}` plus `reply_markup` only if not `None`; calls `bot_post("sendMessage", ...)`; returns `None`.
- `get_updates` (`:143`): payload `{"offset": int, "timeout": int, "allowed_updates": [...]}` defaulting to `list(ALLOWED_UPDATES)` (`["message", "callback_query"]`, so edits, channel posts etc. are never delivered). The HTTP timeout is `float(timeout) + 10.0` so the client does not give up before Telegram's long poll returns. Returns `data["result"]` if it is a list, else `[]` (`test_get_updates_allowed_updates_message_and_callback`).
- `answer_callback_query` (`:169`): `{"callback_query_id": id}` plus `"text"` if non-empty. Telegram shows `text` as a brief toast. A tap must be answered or the user's button shows a spinner.
- `edit_message_text` (`:182`): `editMessageText` with `chat_id`, `message_id`, `text` and optional `reply_markup`. Passing an empty keyboard `{"inline_keyboard": []}` would remove buttons; the bot instead passes `None` in that case (see [bot.md](bot.md#_edit_card)).
- `send_blurb` (`:245`): alias of `send_message` used by the bot for "plain" replies.

### `pager_markup`

**Location** `:201` · **Signature** `def pager_markup(guest: list[dict]) -> dict`

Builds `inline_keyboard` rows of at most 6 buttons: button `text` is the 1-based position `str(i)`, `callback_data` is `f"o:{job_id}"`. Verified with seven guests: first row 6 buttons, second row 1. Numbers match the numbering in the digest cards (`render_card(row, n)`), so "tap 3" opens the third card.

### `action_markup`

**Location** `:215` · **Signature** `def action_markup(job_id: int, apply_url: str, *, status_buttons: bool = True, why_me: bool = True, mail: bool = True) -> dict`

Builds up to three rows:

1. `[{"text": "Apply ↗", "url": apply_url}]` only if `apply_url` (stripped) starts with `http://` or `https://` (case-insensitive). A URL button has no callback, so Apply never hits the bot.
2. If `status_buttons`: Save `s:`, `Applied ✓` `a:`, `Hide ✗` `h:`.
3. A final row with `Why me?` (`w:`) if `why_me` and `Mail` (`m:`) if `mail`.

Verified: `action_markup(42, "https://x.io/a")` has all three rows; `action_markup(42, "", status_buttons=False, why_me=False, mail=False)` -> `{'inline_keyboard': []}`. The flags let the bot rebuild a *post-tap* keyboard (status buttons removed once a status is set; Hide also removes Why me/Mail).

### `_gate_send`

**Location** `:255` · **Signature** `def _gate_send(*, required: bool, http_post: HttpPost | None) -> tuple[str, str] | None`

The three-way credential gate shared by `deliver`, `send_guest_pager`, `send_action_card`:

| Credentials | `required` and `JOBDIGEST_REQUIRE_TELEGRAM` | Result |
|---|---|---|
| present | any | `(token, chat_id)` |
| missing | both true | prints `TELEGRAM FAILED missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID` to stderr, logs, raises `TelegramError("missing credentials")` |
| missing | otherwise | `None` ("skip", debug log) |

Callers catch the `TelegramError` and return `False`; they treat `None` as "nothing to do, success" and return `True`. `tests/test_telegram.py::test_deliver_required_missing_creds_fails_when_flag_set` and `::test_deliver_required_missing_creds_ok_locally` cover both.

### `deliver`

**Location** `:273` · **Signature** `def deliver(text: str, *, required: bool = False, http_post: HttpPost | None = None) -> bool`

1. Blank/whitespace text: debug log, return `True` (`test_deliver_skips_empty`).
2. `parts = split_message(text)`.
3. `creds = _gate_send(...)` (a `TelegramError` becomes `return False`; `None` becomes `return True`).
4. For each part `send_message(...)` (no markup). A `TelegramError` prints `TELEGRAM FAILED <exc>` to stderr and returns `False`; the remaining parts are not sent.
5. Return `True`.

### `send_guest_pager`

**Location** `:306`. Empty guest list returns `True` without sending. Otherwise the same gate, then one `send_message(PAGER_TEXT, ..., reply_markup=pager_markup(guest))`. Errors print `TELEGRAM FAILED` and return `False`. Tested by `test_send_guest_pager_posts_inline_o_callbacks`.

### `send_action_card`

**Location** `:336`. Empty text returns `True`. After the gate, builds `markup = action_markup(...)` and sends `text.strip("\n")` with `reply_markup` only if the keyboard has rows. Used for urgent cards ([digest.md](digest.md#maybe_send_urgent)) and the bot's "open" button. `test_send_action_card_apply_is_url_not_callback` checks Apply is a URL button.

## Failure modes and edge cases

- Message longer than the limit in one block: hard-cut mid-text.
- `deliver` partial failure: earlier parts already sent; not rolled back.
- Missing credentials locally are "success" (`True`), by design, so offline runs and tests work; hosted runs opt in to strictness.
- Token never appears in `TelegramError`; logs say only "telegram send fail" / `http_status=`.

## How to modify / extend safely

- New button kind: add a prefix in `action_markup`/`pager_markup`, extend `_CB` and `_handle_callback` in `bot.py`, and keep `callback_data` within 64 bytes (add a test like `test_callback_data_m_fits_64_bytes`).
- New Bot API method: add a thin function over `bot_post`; do not call `httpx` directly (the token-leak sanitization lives in `bot_post`, and the repo test forbids the API URL elsewhere).
