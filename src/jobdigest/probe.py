from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Sequence

import httpx

from jobdigest.ats import get_adapter
from jobdigest.ats.base import KNOWN_ATS, CompanyConfig
from jobdigest.ats.http_client import make_client
from jobdigest.config import load_companies


def _parse_ids(raw: list[str] | None) -> set[str] | None:
    if not raw:
        return None
    ids: set[str] = set()
    for chunk in raw:
        for part in str(chunk).split(","):
            key = part.strip()
            if key:
                ids.add(key)
    return ids or None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m jobdigest.probe")
    parser.add_argument(
        "--ids",
        action="append",
        default=None,
        metavar="ID",
        help="only probe these company ids (repeatable or comma-separated)",
    )
    parser.add_argument(
        "--enabled-only",
        action="store_true",
        help="only probe companies with enabled: true",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] | None = None,
) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    want_ids = _parse_ids(args.ids)
    enabled_only = bool(args.enabled_only)
    own_client = client is None
    if client is None:
        client = make_client()
    if sleep is None:
        sleep = time.sleep
    try:
        payload = load_companies()
        rows = payload.get("companies") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            company_id = str(row.get("id") or "")
            if want_ids is not None and company_id not in want_ids:
                continue
            if enabled_only and not bool(row.get("enabled", False)):
                continue
            ats = str(row.get("ats") or "")
            token = str(row.get("token") or "")
            if ats.strip().lower() not in KNOWN_ATS:
                print(f"{company_id}\t{ats}\tunimplemented")
                continue
            company = CompanyConfig(
                id=company_id,
                name=str(row.get("name") or ""),
                ats=ats,
                token=token,
                enabled=bool(row.get("enabled", False)),
                notes=str(row.get("notes") or ""),
            )
            adapter = get_adapter(company.ats, client, sleep=sleep)
            try:
                result = adapter.fetch(company)
            except httpx.HTTPError as exc:
                print(f"{company.id}\t{company.ats}\t{company.token}\t\terror\t0\t{exc}")
                continue
            http_status = "" if result.http_status is None else result.http_status
            print(
                f"{company.id}\t{company.ats}\t{company.token}\t"
                f"{http_status}\t{result.status.value}\t{len(result.jobs)}"
            )
    finally:
        if own_client:
            client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
