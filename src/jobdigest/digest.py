from __future__ import annotations

import io
import json
import os
import sys
from collections.abc import Callable
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from jobdigest.config import Models, Profile, load_models, load_profile
from jobdigest.db import (
    collect_is_ok,
    connect,
    finish_run,
    guest_rows,
    insert_run,
    relevant_unapplied_jobs,
    stamp_notified,
    urgent_sent_count,
)
from jobdigest.envfile import load_dotenv
from jobdigest.fit import fit_score, quality_score
from jobdigest.logutil import configure, get_logger
from jobdigest.openrouter import default_chat
from jobdigest.score import run as score_run
from jobdigest.rank import age_chip, source_chip
from jobdigest.followups import parse_iso
from jobdigest.legitimacy import age_codes, chip, merge_flag
from jobdigest.summarize import render_card, summarize_digest
from jobdigest.telegram import deliver as telegram_deliver
from jobdigest.telegram import send_action_card, send_guest_pager, would_post
from jobdigest.telegram import split_message

ChatFn = Callable[..., str]
TelegramHttpPost = Callable[..., object]
URGENT_MIN_FIT = 70.0

log = get_logger("digest")


def _digest_url(apply_url: str | None) -> str:
    url = apply_url or ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return ""


def _row_get(row, key: str, default=None):
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    if value is None:
        return default
    return value


def _reasons(reasons_json: str | None) -> dict:
    out = {
        "why": "",
        "stack_ok": True,
        "level_ok": True,
        "location_ok": True,
        "kill": "",
        "gap_labels": "",
    }
    if not reasons_json:
        return out
    try:
        data = json.loads(reasons_json)
    except json.JSONDecodeError:
        return out
    if not isinstance(data, dict):
        return out
    out["why"] = str(data.get("why") or "")[:160]
    out["stack_ok"] = bool(data.get("stack_ok", True))
    out["level_ok"] = bool(data.get("level_ok", True))
    out["location_ok"] = bool(data.get("location_ok", True))
    kill = data.get("kill")
    out["kill"] = "" if kill is None else str(kill)
    labels: list[str] = []
    if not out["stack_ok"]:
        labels.append("stack")
    if not out["level_ok"]:
        labels.append("level")
    if not out["location_ok"]:
        labels.append("location")
    if out["kill"]:
        labels.append(" ".join(out["kill"].split())[:32])
    out["gap_labels"] = "+".join(labels)
    return out


def _legitimacy_codes(reasons_json: str | None) -> list[str]:
    if not reasons_json:
        return []
    try:
        data = json.loads(reasons_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in data:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _guest_dicts(rows, *, now: datetime, profile: Profile | None = None) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        reasons = _reasons(_row_get(row, "reasons_json", None))
        ats = str(_row_get(row, "ats", "") or "")
        first_seen = str(_row_get(row, "first_seen_at", "") or "")
        posted_at = _row_get(row, "posted_at", None)
        structural = _legitimacy_codes(_row_get(row, "legitimacy_reasons_json", None))
        flag, codes = merge_flag(structural, age_codes(posted_at, first_seen, now=now))
        out.append(
            {
                "id": int(row["id"]),
                "company": row["company"] or "",
                "title": row["title"] or "",
                "location": row["location_raw"] or "",
                "url": _digest_url(row["apply_url"]),
                "score": round(
                    quality_score(row, profile, now)
                    if profile is not None
                    else float(row["score"] or 0)
                ),
                "why": reasons["why"],
                "first_seen_at": first_seen,
                "ats": ats,
                "age_chip": age_chip(first_seen, now),
                "source_chip": source_chip(ats),
                "legitimacy_flag": flag,
                "legitimacy_chip": chip(flag, codes),
                "stack_ok": reasons["stack_ok"],
                "level_ok": reasons["level_ok"],
                "location_ok": reasons["location_ok"],
                "kill": reasons["kill"],
                "gap_labels": reasons["gap_labels"],
            }
        )
    return out


def _print_digest(text: str) -> None:
    parts = split_message(text)
    n = len(parts)
    if n == 1:
        print(parts[0])
        return
    for i, part in enumerate(parts, 1):
        print(f"{i}/{n}")
        print(part)
        if i < n:
            print()


def _loud_fail(
    conn,
    run_id: int,
    message: str,
    *,
    telegram_http_post: TelegramHttpPost | None = None,
) -> int:
    finish_run(conn, run_id, "error", {"job_ids": []}, error=message)
    log.error("digest failed: %s", message)
    print(message)
    print(message, file=sys.stderr)
    telegram_deliver(message, required=True, http_post=telegram_http_post)
    return 1


def _company_meta(conn) -> dict[int, tuple[str, str]]:
    rows = conn.execute("SELECT id, slug, display_name FROM companies").fetchall()
    out: dict[int, tuple[str, str]] = {}
    for row in rows:
        out[int(row["id"])] = (str(row["slug"] or ""), str(row["display_name"] or ""))
    return out


def _watchlisted(
    row, profile: Profile, meta: dict[int, tuple[str, str]]
) -> bool:
    needles = {
        (w or "").strip().casefold()
        for w in profile.watchlist
        if (w or "").strip()
    }
    if not needles:
        return False
    company = str(_row_get(row, "company", "") or "").strip().casefold()
    if company in needles:
        return True
    cid = _row_get(row, "company_id", None)
    if cid is None:
        return False
    pair = meta.get(int(cid))
    if pair is None:
        return False
    slug, display = pair
    return slug.casefold() in needles or display.casefold() in needles


def _within_urgent_lookback(first_seen: str | None, now: datetime) -> bool:
    dt = parse_iso(first_seen)
    if dt is None:
        return False
    now_utc = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    now_utc = now_utc.astimezone(timezone.utc)
    age = (now_utc - dt).total_seconds()
    return 0 <= age <= 36 * 3600


def maybe_send_urgent(
    conn,
    *,
    models: Models,
    profile: Profile,
    now: datetime,
    http_post: TelegramHttpPost | None = None,
) -> list[int]:
    already = urgent_sent_count(conn, now)
    budget = max(0, int(profile.urgent_daily_cap) - already)
    if budget <= 0:
        return []
    meta = _company_meta(conn)
    min_score = float(profile.urgent_score_min)
    rows = relevant_unapplied_jobs(
        conn,
        models.score_model,
        models.score_prompt_version,
        profile=profile,
        now=now,
    )
    candidates = []
    for row in rows:
        if str(_row_get(row, "notified_at", "") or "").strip():
            continue
        if not _within_urgent_lookback(_row_get(row, "first_seen_at", None), now):
            continue
        # Urgent means exceptional: a high LLM score AND a strong deterministic fit.
        llm_score = float(_row_get(row, "score", 0) or 0)
        if not _watchlisted(row, profile, meta) and (
            llm_score < min_score or fit_score(row, profile, now) < URGENT_MIN_FIT
        ):
            continue
        candidates.append(row)
    sent_ids: list[int] = []
    for row in candidates:
        if len(sent_ids) >= budget:
            break
        guest = _guest_dicts([row], now=now, profile=profile)
        if not guest:
            continue
        job_id = int(row["id"])
        ok = send_action_card(
            render_card(guest[0], 1, now=now),
            job_id=job_id,
            apply_url=str(guest[0].get("url") or ""),
            required=True,
            http_post=http_post,
        )
        if not ok:
            continue
        if would_post(http_post):
            stamp_notified(conn, job_id, "urgent")
            sent_ids.append(job_id)
            log.info("urgent sent job_id=%s", job_id)
    return sent_ids


def _run_urgent(conn, run_id, *, models, profile, now, http_post) -> int:
    sent_ids = maybe_send_urgent(
        conn, models=models, profile=profile, now=now, http_post=http_post
    )
    n = len(sent_ids)
    log.info("digest urgent sent=%s", n)
    finish_run(
        conn,
        run_id,
        "ok",
        {"urgent": n, "path": "urgent", "job_ids": sent_ids},
    )
    return 0


def _deliver_digest(conn, guest, text, *, http_post) -> int:
    sent = telegram_deliver(text, required=True, http_post=http_post)
    log.info("digest telegram_ok=%s", sent)
    if not sent:
        return 1
    if would_post(http_post):
        for item in guest:
            stamp_notified(conn, int(item["id"]), "digest")
    pager_ok = send_guest_pager(guest, required=True, http_post=http_post)
    log.info("digest pager_ok=%s", pager_ok)
    return 0 if pager_ok else 1


def _run_digest(
    conn, run_id, *, models, profile, now, sum_fn, http_post
) -> int:
    rows = guest_rows(
        conn,
        models.score_model,
        models.score_prompt_version,
        profile.digest_max,
        profile=profile,
        now=now,
    )
    guest = _guest_dicts(rows, now=now, profile=profile)
    titles = [f"{g['company']}: {g['title']}" for g in guest]
    log.info("digest guests=%s titles=%s", len(guest), titles)
    if not guest:
        log.info("digest empty skip send")
        finish_run(conn, run_id, "ok", {"guest": 0, "path": "empty", "job_ids": []})
        return 0

    text, path = summarize_digest(
        guest,
        chat_fn=sum_fn,
        model=models.summarize_model,
        api_key=os.environ.get("OPENROUTER_API_KEY") or "",
        prompt_version=models.summarize_prompt_version,
    )
    _print_digest(text)
    finish_run(
        conn,
        run_id,
        "ok",
        {
            "guest": len(guest),
            "path": path,
            "job_ids": [int(item["id"]) for item in guest],
        },
    )
    return _deliver_digest(conn, guest, text, http_post=http_post)


def run(
    *,
    db_path: Path | None = None,
    chat_fn: ChatFn | None = None,
    score_chat_fn: ChatFn | None = None,
    summarize_chat_fn: ChatFn | None = None,
    telegram_http_post: TelegramHttpPost | None = None,
    urgent_only: bool = False,
) -> int:
    load_dotenv()
    configure()
    score_fn = score_chat_fn or chat_fn
    sum_fn = summarize_chat_fn or chat_fn
    if sum_fn is None:
        sum_fn = default_chat

    models = load_models()
    profile = load_profile()
    conn = connect(db_path)
    run_id = insert_run(conn, "digest")
    try:
        collect_ok = collect_is_ok(conn)
        log.info("digest collect_is_ok=%s", collect_ok)
        if not collect_ok:
            return _loud_fail(
                conn, run_id, "COLLECT FAILED", telegram_http_post=telegram_http_post
            )

        with redirect_stdout(io.StringIO()):
            score_code = score_run(db_path=db_path, chat_fn=score_fn)
        if score_code != 0:
            return _loud_fail(
                conn, run_id, "SCORE FAILED", telegram_http_post=telegram_http_post
            )

        conn.close()
        conn = connect(db_path)
        now = datetime.now(timezone.utc)
        if urgent_only:
            return _run_urgent(
                conn,
                run_id,
                models=models,
                profile=profile,
                now=now,
                http_post=telegram_http_post,
            )
        return _run_digest(
            conn,
            run_id,
            models=models,
            profile=profile,
            now=now,
            sum_fn=sum_fn,
            http_post=telegram_http_post,
        )
    finally:
        conn.close()
