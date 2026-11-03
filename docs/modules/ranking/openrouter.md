# OpenRouter client (`openrouter.py`)

- Source: `src/jobdigest/openrouter.py` (121 lines)
- Layer: external-service adapter. The only module that talks to an LLM provider.
- Depends on: `httpx`, `os`, `json`. Used by: `jobdigest.score` ([score-llm.md](score-llm.md)), `jobdigest.digest` (`default_chat` for the summary), `jobdigest.bot` (`chat as openrouter_chat` for the "why me" blurb and e-mail drafts), and indirectly `summarize.py` / `blurb.py`, which catch `OpenRouterError`.
- Tests: no dedicated test file. `OpenRouterError` is raised from fake `chat_fn`s throughout `tests/test_score.py`, `tests/test_summarize.py`, `tests/test_blurb.py`, `tests/test_pipeline_e2e.py`. The HTTP code in `chat` is not exercised by a test in the repo (verified: no test imports `openrouter.chat` and builds a mock transport). The behaviours described below were confirmed by running `chat` offline against an `httpx.MockTransport`.

## Why this exists / responsibility

OpenRouter is a proxy that exposes many LLM vendors through one OpenAI-compatible `POST /chat/completions` endpoint. This module turns "a list of chat messages plus a model id" into the reply string, applies privacy-related provider settings, and converts every failure into one exception type, so callers have exactly two things to handle: `OpenRouterError` (service problem) and `ValueError` (the configured model is not allowed).

## Public interface at a glance

| Symbol | Signature | Purpose |
|---|---|---|
| `OPENROUTER_URL` | `"https://openrouter.ai/api/v1/chat/completions"` | Endpoint |
| `DEFAULT_REFERER` | `"https://openrouter.ai"` | Fallback for the `HTTP-Referer` header |
| `SCORE_SCHEMA` | dict | JSON schema used for scoring replies |
| `OpenRouterError` | `class OpenRouterError(Exception)` | Any transport / HTTP / payload-shape failure |
| `chat` | `chat(messages, *, model, api_key, json_mode=False, client=None) -> str` | One completion |
| `default_chat` | same signature | Thin wrapper; the default `chat_fn` for score and digest |

## Detailed walkthrough

### `SCORE_SCHEMA`

**Location** `src/jobdigest/openrouter.py:11`
A JSON Schema (`additionalProperties: False` everywhere) for `{"jobs": [ {...} ]}`. Each item requires all eight keys: `job_id` (integer), `score` (number, 0-100), `relevant`, `level_ok`, `location_ok`, `stack_ok` (booleans), `why` (string, max 160), `kill` (string or null). It is sent with `strict: true` so a compliant provider must produce exactly this shape. `score.py` still validates defensively (`_score_item`), because "strict" depends on the upstream provider honouring it. Note `minimum`/`maximum`/`maxLength` are part of the schema but `_score_item` clamps and truncates anyway.

### `OpenRouterError`

**Location** `src/jobdigest/openrouter.py:47` - an empty `Exception` subclass. Callers catch it to mean "the LLM call failed; fall back or retry later".

### `chat`

**Location** `src/jobdigest/openrouter.py:51`
**Signature** `def chat(messages: list[dict[str, str]], *, model: str, api_key: str, json_mode: bool = False, client: httpx.Client | None = None) -> str`
**Purpose** Perform one non-streaming chat completion and return `choices[0].message.content`.
**Called by** `default_chat`; `bot.py` passes it directly as `chat_fn` (`openrouter_chat`). **Calls** `httpx.Client.post`.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `messages` | `list[dict[str, str]]` | | OpenAI-style `{"role","content"}` messages |
| `model` | `str` | | OpenRouter model id, e.g. `openai/gpt-4o-mini` |
| `api_key` | `str` | | Bearer token (never logged here) |
| `json_mode` | `bool` | `False` | If true, request strict JSON-schema output using `SCORE_SCHEMA` |
| `client` | `httpx.Client \| None` | `None` | Reusable client for tests; if `None` a client is created and closed per call |

**Returns** The reply text. **Raises** `ValueError` (model guard), `OpenRouterError` (everything else).

**Step-by-step**

1. **Model guard.** `if model.endswith(":free") or model == "openrouter/auto": raise ValueError(f"refused OpenRouter model: {model}")`. Free-tier variants may log or train on prompts and `openrouter/auto` routes to an unpredictable model; the app sends job data and personal profile details, so it refuses both before making any request. The same rule exists earlier at config time (`config.load_models` raises for `:free` score/summarize models and for `:free`/`openrouter/auto` blurb models), so this is a second line of defence for ids that reach `chat` another way. Because `ValueError` is not `OpenRouterError`, `score._request_items` converts it to `_InvalidModel` (abort the run), while `summarize_digest` treats it as "fall back to code-rendered cards" (see [summarize.md](summarize.md)).
2. **Payload.**

```python
payload: dict = {
    "model": model,
    "temperature": 0,
    "messages": messages,
    "provider": {"data_collection": "deny", "require_parameters": True},
}
```

   - `temperature: 0` for repeatable scoring.
   - `provider.data_collection = "deny"`: only route to providers that do not retain/train on prompts.
   - `provider.require_parameters = True`: only route to providers that actually support every parameter in the request (notably `response_format` json_schema). Without it OpenRouter may silently drop an unsupported parameter and return free-form text.
3. **JSON mode.** If `json_mode`, `payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "job_scores", "strict": True, "schema": SCORE_SCHEMA}}`. Only `score.py` passes `json_mode=True`. The summary and blurb calls use `False`, so they get plain text. (`json_mode` is tied to the scoring schema; there is no way to ask for any other schema.)
4. **Headers.** `Authorization: Bearer <api_key>`, `Content-Type: application/json`, `HTTP-Referer` from env `OPENROUTER_REFERER` or `DEFAULT_REFERER`, `X-Title: jobdigest` (OpenRouter's attribution headers).
5. **Client.** `own_client = client is None`; the default client is `httpx.Client(timeout=60.0)`; the request itself also passes `timeout=60.0`.
6. **Request/response handling** inside `try/finally` (the `finally` closes the client only if it was created here):
   - `httpx.HTTPError` (connection errors, timeouts) -> `OpenRouterError(str(exc))`.
   - `resp.status_code != 200` -> `OpenRouterError(f"HTTP {code}: {resp.text[:500]}")`.
   - `resp.json()` raising `json.JSONDecodeError` -> `OpenRouterError("invalid JSON response")`.
   - `data["choices"][0]["message"]["content"]` raising `KeyError/IndexError/TypeError` -> `OpenRouterError("missing choices[0].message.content")`.
   - content not a `str` (for example `null`) -> the same error.
   - otherwise return the content.

There is no retry or backoff inside `chat`: retry policy lives in the callers (`score._score_batch` re-asks once for omitted jobs; a failed batch retries on the next run).

Verified offline with `httpx.MockTransport`: payload keys are `model, temperature, messages, provider` (plus `response_format` in JSON mode); headers include `x-title: jobdigest` and `http-referer: https://openrouter.ai`; a 500 yields `OpenRouterError('HTTP 500: bad')`; a non-JSON 200 yields `invalid JSON response`; `{"choices": []}` and `content: null` yield `missing choices[0].message.content`; `a/b:free` and `openrouter/auto` yield `refused OpenRouter model: ...`.

### `default_chat`

**Location** `src/jobdigest/openrouter.py:110` - same parameters as `chat`; it simply forwards. It exists so `score.run` and `digest.run` have a stable module-level default that tests can monkeypatch or replace via the `chat_fn` argument, and so a future change (shared client, retries) can be made in one place.

## Failure modes and edge cases

- A 200 response whose body is an OpenRouter error envelope (`{"error": {...}}` without `choices`) becomes "missing choices[0].message.content".
- `resp.text[:500]` is included in the exception text, which flows into logs; do not put secrets in prompts.
- Missing/empty `api_key` is not checked here (score checks it in `run`; `bot` checks before calling), so an empty key results in an HTTP 401 -> `OpenRouterError`.

## How to modify / extend safely

- New provider preferences go in `payload["provider"]`; keep `data_collection: "deny"` unless the privacy stance is intentionally changed.
- To add a second structured-output schema, parametrise `json_mode` (currently a boolean hard-wired to `SCORE_SCHEMA`), and update every `chat_fn` stub in the tests, which accept `json_mode` as a keyword.
- Any `chat_fn` replacement must accept `(messages, *, model, api_key, json_mode=False, client=None)` (tests use that shape, e.g. `tests/test_summarize.py`).
