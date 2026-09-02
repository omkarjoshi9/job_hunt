from __future__ import annotations

from collections.abc import Callable

import httpx

from jobdigest.ats.ashby import AshbyAdapter
from jobdigest.ats.base import BoardAdapter, CompanyConfig, FetchResult, FetchStatus, NormalizedJob
from jobdigest.ats.greenhouse import GreenhouseAdapter
from jobdigest.ats.lever import LeverAdapter
from jobdigest.ats.workable import WorkableAdapter

__all__ = [
    "AshbyAdapter",
    "BoardAdapter",
    "CompanyConfig",
    "FetchResult",
    "FetchStatus",
    "GreenhouseAdapter",
    "LeverAdapter",
    "NormalizedJob",
    "WorkableAdapter",
    "get_adapter",
]

_KNOWN = frozenset({"greenhouse", "lever", "ashby", "workable"})


def get_adapter(
    ats: str,
    client: httpx.Client,
    *,
    sleep: Callable[[float], None],
) -> BoardAdapter:
    key = (ats or "").strip().lower()
    if key not in _KNOWN:
        raise ValueError(f"unimplemented ats: {ats!r}")
    if key == "greenhouse":
        return GreenhouseAdapter(client, sleep=sleep)
    if key == "lever":
        return LeverAdapter(client, sleep=sleep)
    if key == "ashby":
        return AshbyAdapter(client, sleep=sleep)
    return WorkableAdapter(client, sleep=sleep)
