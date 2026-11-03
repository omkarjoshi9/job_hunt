from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from jobdigest.db import last_collect_run

log = logging.getLogger("jobdigest.health")

Conn = Any
_SECTION_CAP = 15
HEALTHY = "All sources healthy."


@dataclass
class HealthReport:
    errored: list[str] = field(default_factory=list)
    empty_boards: list[str] = field(default_factory=list)
    zero_cards: list[str] = field(default_factory=list)
    dead_tokens: list[str] = field(default_factory=list)

    def is_healthy(self) -> bool:
        return not (
            self.errored or self.empty_boards or self.zero_cards or self.dead_tokens
        )


def _cell(row: Any, name: str, default: str = "") -> str:
    try:
        value = row[name]
    except (KeyError, IndexError, TypeError) as exc:
        log.debug("health row has no column %r: %s", name, exc)
        return default
    if value is None:
        return default
    return str(value)


def _line(row: Any, extra: str = "") -> str:
    slug = _cell(row, "slug") or _cell(row, "portal_name")
    ats = _cell(row, "ats")
    http_status = _cell(row, "last_http_status") or _cell(row, "http_status")
    parts = [p for p in (slug, ats, http_status, extra) if p]
    return " ".join(parts)


def collect_health(conn: Conn) -> HealthReport:
    report = HealthReport()
    companies = conn.execute(
        """
        SELECT slug, ats, last_http_status, last_error, last_job_count,
               dead_streak, enabled
        FROM companies
        WHERE enabled = 1
        ORDER BY slug
        """
    ).fetchall()
    for row in companies:
        err = str(row["last_error"] or "").strip()
        count = row["last_job_count"]
        streak = int(row["dead_streak"] or 0)
        if err == "dead_token" and streak >= 2:
            report.dead_tokens.append(_line(row, f"streak={streak}"))
        elif err and err != "dead_token":
            report.errored.append(_line(row, err[:80]))
        elif err == "" and count == 0:
            report.empty_boards.append(_line(row, "empty"))
    last = last_collect_run(conn)
    if last is not None:
        zeros = conn.execute(
            """
            SELECT portal_name, error_class, http_status, detail
            FROM portal_errors
            WHERE run_id = ?
              AND error_class = 'parse'
              AND detail LIKE '%0 cards%'
            ORDER BY id
            """,
            (int(last["id"]),),
        ).fetchall()
        for row in zeros:
            name = str(row["portal_name"] or "")
            detail = str(row["detail"] or "")[:80]
            report.zero_cards.append(f"{name} parse {detail}".strip())
        extras = conn.execute(
            """
            SELECT portal_name, error_class, http_status, detail
            FROM portal_errors
            WHERE run_id = ?
              AND NOT (error_class = 'parse' AND detail LIKE '%0 cards%')
            ORDER BY id
            """,
            (int(last["id"]),),
        ).fetchall()
        seen = {line.split()[0] for line in report.errored if line}
        for row in extras:
            name = str(row["portal_name"] or "")
            if name in seen:
                continue
            klass = str(row["error_class"] or "error")
            detail = str(row["detail"] or "")[:80]
            report.errored.append(f"{name} {klass} {detail}".strip())
            seen.add(name)
    report.errored = report.errored[:_SECTION_CAP]
    report.empty_boards = report.empty_boards[:_SECTION_CAP]
    report.zero_cards = report.zero_cards[:_SECTION_CAP]
    report.dead_tokens = report.dead_tokens[:_SECTION_CAP]
    return report


def format_health(report: HealthReport) -> str:
    if report.is_healthy():
        return HEALTHY
    blocks: list[str] = []
    if report.errored:
        blocks.append("ERRORS\n" + "\n".join(report.errored))
    if report.empty_boards:
        blocks.append("EMPTY\n" + "\n".join(report.empty_boards))
    if report.zero_cards:
        blocks.append("0 CARDS\n" + "\n".join(report.zero_cards))
    if report.dead_tokens:
        blocks.append("DEAD TOKENS\n" + "\n".join(report.dead_tokens))
    return "\n\n".join(blocks)
