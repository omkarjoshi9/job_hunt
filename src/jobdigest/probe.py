from __future__ import annotations

import time
from collections.abc import Callable, Sequence

import httpx

from jobdigest.ats import get_adapter
from jobdigest.ats.base import CompanyConfig
from jobdigest.ats.http_client import make_client
from jobdigest.config import load_companies

_KNOWN = frozenset({"greenhouse", "lever", "ashby", "workable"})


def main(
    argv: Sequence[str] | None = None,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] | None = None,
) -> int:
    _ = argv
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
            ats = str(row.get("ats") or "")
            token = str(row.get("token") or "")
            if ats.strip().lower() not in _KNOWN:
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
            result = adapter.fetch(company)
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
