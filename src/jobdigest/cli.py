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
            skip_portals=bool(getattr(args, "skip_portals", False)),
            skip_feeds=bool(getattr(args, "skip_feeds", False)),
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
    return int(run(db_path=db))


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


def _cmd_sheet_sync(args: argparse.Namespace) -> int:
    from jobdigest.sheet import sync

    db = Path(args.db) if args.db else None
    return int(sync(db_path=db))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobdigest",
        description="Daily intern job digest (collect, score, digest, sheet-append, sheet-sync).",
    )
    sub = parser.add_subparsers(dest="command", required=False)
    p_collect = sub.add_parser("collect", help="fetch ATS JSON, portals, and RSS into SQLite")
    p_collect.add_argument(
        "--ats-only",
        action="store_true",
        help="skip portals.yaml and feeds.yaml",
    )
    p_collect.add_argument(
        "--skip-ats",
        action="store_true",
        help="skip companies.yaml ATS fetch",
    )
    p_collect.add_argument(
        "--skip-portals",
        action="store_true",
        help="skip portals.yaml",
    )
    p_collect.add_argument(
        "--skip-feeds",
        action="store_true",
        help="skip feeds.yaml",
    )
    p_collect.add_argument(
        "--portal-fetch",
        action="append",
        choices=("http", "dynamic", "stealth"),
        dest="portal_fetch",
        help="only run portals with this fetch mode (repeatable)",
    )
    p_collect.add_argument(
        "--db",
        default=None,
        help="SQLite path (default DATABASE_PATH or data/jobs.db)",
    )
    p_collect.set_defaults(func=_cmd_collect)

    db_help = "SQLite path (default DATABASE_PATH or data/jobs.db)"
    p_score = sub.add_parser("score", help="score pass jobs with OpenRouter JSON")
    p_score.add_argument("--db", default=None, help=db_help)
    p_score.set_defaults(func=_cmd_score)

    p_digest = sub.add_parser(
        "digest", help="score then print and send Telegram digest"
    )
    p_digest.add_argument("--db", default=None, help=db_help)
    p_digest.set_defaults(func=_cmd_digest)

    p_sheet = sub.add_parser("sheet-append", help="append applied jobs to Google Sheets")
    p_sheet.add_argument("--db", default=None, help=db_help)
    p_sheet.add_argument("--url", default=None, help="mark this apply URL then push")
    p_sheet.add_argument("--company", default="")
    p_sheet.add_argument("--title", default="")
    p_sheet.add_argument("--status", default="applied")
    p_sheet.add_argument("--notes", default="")
    p_sheet.add_argument("--source", default="")
    p_sheet.set_defaults(func=_cmd_sheet)

    p_sync = sub.add_parser(
        "sheet-sync",
        help="append new offcampus digest guests to Google Sheets; skip existing URLs",
    )
    p_sync.add_argument("--db", default=None, help=db_help)
    p_sync.set_defaults(func=_cmd_sheet_sync)
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
