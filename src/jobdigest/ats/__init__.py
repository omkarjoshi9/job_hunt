from __future__ import annotations

from collections.abc import Callable

import httpx

from jobdigest.ats.ashby import AshbyAdapter
from jobdigest.ats.bamboohr import BambooHRAdapter
from jobdigest.ats.base import (
    KNOWN_ATS,
    BoardAdapter,
    CompanyConfig,
    FetchResult,
    FetchStatus,
    NormalizedJob,
)
from jobdigest.ats.greenhouse import GreenhouseAdapter
from jobdigest.ats.lever import LeverAdapter
from jobdigest.ats.smartrecruiters import SmartRecruitersAdapter
from jobdigest.ats.workable import WorkableAdapter

__all__ = [
    "AshbyAdapter",
    "BambooHRAdapter",
    "BoardAdapter",
    "CompanyConfig",
    "FetchResult",
    "FetchStatus",
    "GreenhouseAdapter",
    "KNOWN_ATS",
    "LeverAdapter",
    "NormalizedJob",
    "SmartRecruitersAdapter",
    "WorkableAdapter",
    "get_adapter",
]

def get_adapter(
    ats: str,
    client: httpx.Client,
    *,
    sleep: Callable[[float], None],
) -> BoardAdapter:
    key = (ats or "").strip().lower()
    if key not in KNOWN_ATS:
        raise ValueError(f"unimplemented ats: {ats!r}")
    if key == "greenhouse":
        return GreenhouseAdapter(client, sleep=sleep)
    if key == "lever":
        return LeverAdapter(client, sleep=sleep)
    if key == "ashby":
        return AshbyAdapter(client, sleep=sleep)
    if key == "smartrecruiters":
        return SmartRecruitersAdapter(client, sleep=sleep)
    if key == "bamboohr":
        return BambooHRAdapter(client, sleep=sleep)
    return WorkableAdapter(client, sleep=sleep)
