from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cmd_collect(args: argparse.Namespace) -> int:
    from jobdigest.collect import run

    db = Path(args.db) if args.db else None
    portal_fetch = tuple(args.portal_fetch) if getattr(args, "portal_fetch", None) else None
    return int(
        run(
            db_path=db,
            ats_only=bool(args.ats_only),
            skip_ats=bool(getattr(args, "skip_ats", False)),
            skip_discovered=bool(getattr(args, "skip_discovered", False)),
            skip_portals=bool(getattr(args, "skip_portals", False)),
            skip_feeds=bool(getattr(args, "skip_feeds", False)),
            skip_marketplaces=bool(getattr(args, "skip_marketplaces", False)),
            portal_fetch=portal_fetch,
        )
    )


def _cmd_score(args: argparse.Namespace) -> int:
    from jobdigest.score import run

    db = Path(args.db) if args.db else None
    return int(run(db_path=db))


def _cmd_digest(args: argparse.Namespace) -> int:
    from jobdigest.digest import run

    db = Path(args.db) if args.db else None
    return int(run(db_path=db, urgent_only=bool(args.urgent_only)))


def _cmd_sheet(args: argparse.Namespace) -> int:
    from jobdigest.sheet import run

    db = Path(args.db) if args.db else None
    return int(
        run(
            db_path=db,
            url=args.url,
            company=args.company,
            title=args.title,
            status=args.status,
            notes=args.notes,
            source=args.source,
        )
    )


def _cmd_contacts(args: argparse.Namespace) -> int:
    from jobdigest.contacts import run

    db = Path(args.db) if args.db else None
    job_ids = list(args.job_id) if getattr(args, "job_id", None) else None
    return int(run(db_path=db, limit=args.limit, job_ids=job_ids))


def _cmd_sheet_sync(args: argparse.Namespace) -> int:
    from jobdigest.sheet import sync

    db = Path(args.db) if args.db else None
    return int(sync(db_path=db))


_DB_HELP = "SQLite path (default DATABASE_PATH or data/jobs.db)"


def _add_db(p: argparse.ArgumentParser) -> None:
    p.add_argument("--db", default=None, help=_DB_HELP)


def _add_flag(p: argparse.ArgumentParser, name: str, help_text: str) -> None:
    p.add_argument(name, action="store_true", help=help_text)


def _add_collect_parser(sub) -> None:
    p = sub.add_parser(
        "collect", help="fetch ATS JSON, portals, RSS, and marketplaces into SQLite"
    )
    _add_flag(p, "--ats-only", "skip portals, feeds, and marketplaces")
    _add_flag(p, "--skip-ats", "skip companies.yaml ATS fetch")
    _add_flag(p, "--skip-discovered", "skip boards found by jobdigest.expand")
    _add_flag(p, "--skip-portals", "skip portals.yaml")
    _add_flag(p, "--skip-feeds", "skip feeds.yaml")
    _add_flag(p, "--skip-marketplaces", "skip marketplaces.yaml")
    p.add_argument(
        "--portal-fetch",
        action="append",
        choices=("http", "dynamic", "stealth"),
        dest="portal_fetch",
        help="only run portals with this fetch mode (repeatable)",
    )
    _add_db(p)
    p.set_defaults(func=_cmd_collect)


def _add_digest_parser(sub) -> None:
    p = sub.add_parser("digest", help="score then print and send Telegram digest")
    _add_db(p)
    _add_flag(
        p,
        "--urgent-only",
        "push watchlist/high-score action cards; skip the 12-job digest",
    )
    p.set_defaults(func=_cmd_digest)


def _add_sheet_parser(sub) -> None:
    p = sub.add_parser("sheet-append", help="append applied jobs to Google Sheets")
    _add_db(p)
    p.add_argument("--url", default=None, help="mark this apply URL then push")
    p.add_argument("--company", default="")
    p.add_argument("--title", default="")
    p.add_argument("--status", default="applied")
    p.add_argument("--notes", default="")
    p.add_argument("--source", default="")
    p.set_defaults(func=_cmd_sheet)


def _add_contacts_parser(sub) -> None:
    p = sub.add_parser(
        "contacts",
        help="enrich hiring-manager contacts for last digest guests",
    )
    _add_db(p)
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="max jobs to enrich (default: all last-digest ids)",
    )
    p.add_argument(
        "--job-id",
        action="append",
        type=int,
        dest="job_id",
        help="enrich this job id (repeatable; skips last-digest lookup)",
    )
    p.set_defaults(func=_cmd_contacts)


def _add_simple_parser(sub, name: str, help_text: str, func) -> None:
    p = sub.add_parser(name, help=help_text)
    _add_db(p)
    p.set_defaults(func=func)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobdigest",
        description="Daily intern job digest (collect, score, digest, contacts, sheet-append, sheet-sync).",
    )
    sub = parser.add_subparsers(dest="command", required=False)
    _add_collect_parser(sub)
    _add_simple_parser(sub, "score", "score pass jobs with OpenRouter JSON", _cmd_score)
    _add_digest_parser(sub)
    _add_sheet_parser(sub)
    _add_simple_parser(
        sub,
        "sheet-sync",
        "append new offcampus digest guests to Google Sheets; skip existing URLs",
        _cmd_sheet_sync,
    )
    _add_contacts_parser(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    from jobdigest.envfile import load_dotenv
    from jobdigest.logutil import configure

    load_dotenv()
    configure()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
