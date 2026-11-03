from __future__ import annotations

import httpx
import pytest

from jobdigest.ats import BambooHRAdapter, SmartRecruitersAdapter, get_adapter
from jobdigest.ats.base import CompanyConfig
from jobdigest.ats import KNOWN_ATS
from jobdigest.collect import _board_url


def test_get_adapter_smartrecruiters():
    with httpx.Client() as client:
        adapter = get_adapter("smartrecruiters", client, sleep=lambda _: None)
        also = get_adapter("SmartRecruiters", client, sleep=lambda _: None)
    assert isinstance(adapter, SmartRecruitersAdapter)
    assert isinstance(also, SmartRecruitersAdapter)
    assert adapter.ats == "smartrecruiters"


def test_get_adapter_workday_and_getro_still_unimplemented():
    with httpx.Client() as client:
        with pytest.raises(ValueError, match="unimplemented"):
            get_adapter("workday", client, sleep=lambda _: None)
        with pytest.raises(ValueError, match="unimplemented"):
            get_adapter("getro", client, sleep=lambda _: None)


def test_collect_knows_smartrecruiters_board_url():
    assert "smartrecruiters" in KNOWN_ATS
    company = CompanyConfig(
        id="ixigo",
        name="ixigo",
        ats="smartrecruiters",
        token="ixigo",
        enabled=False,
    )
    assert _board_url(company) == (
        "https://api.smartrecruiters.com/v1/companies/ixigo/postings"
    )


def test_get_adapter_bamboohr():
    with httpx.Client() as client:
        adapter = get_adapter("bamboohr", client, sleep=lambda _: None)
        also = get_adapter("BambooHR", client, sleep=lambda _: None)
    assert isinstance(adapter, BambooHRAdapter)
    assert isinstance(also, BambooHRAdapter)
    assert adapter.ats == "bamboohr"


def test_collect_knows_bamboohr_board_url():
    assert "bamboohr" in KNOWN_ATS
    company = CompanyConfig(
        id="zepto",
        name="Zepto",
        ats="bamboohr",
        token="zepto",
        enabled=False,
    )
    assert _board_url(company) == "https://zepto.bamboohr.com/careers/list"
