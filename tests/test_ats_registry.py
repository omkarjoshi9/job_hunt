import httpx
import pytest

from jobdigest.ats import get_adapter
from jobdigest.ats.ashby import AshbyAdapter
from jobdigest.ats.greenhouse import GreenhouseAdapter
from jobdigest.ats.lever import LeverAdapter
from jobdigest.ats.workable import WorkableAdapter


def test_unimplemented_workday_raises():
    with httpx.Client() as client:
        with pytest.raises(ValueError, match="unimplemented"):
            get_adapter("workday", client, sleep=lambda s: None)


def test_unimplemented_getro_raises():
    with httpx.Client() as client:
        with pytest.raises(ValueError, match="unimplemented"):
            get_adapter("getro", client, sleep=lambda s: None)


def test_known_ats_return_adapters():
    with httpx.Client() as client:
        sleep = lambda s: None
        assert isinstance(get_adapter("greenhouse", client, sleep=sleep), GreenhouseAdapter)
        assert isinstance(get_adapter("Lever", client, sleep=sleep), LeverAdapter)
        assert isinstance(get_adapter("ashby", client, sleep=sleep), AshbyAdapter)
        assert isinstance(get_adapter("workable", client, sleep=sleep), WorkableAdapter)
