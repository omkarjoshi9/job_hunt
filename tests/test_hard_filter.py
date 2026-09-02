from jobdigest.config import load_profile
from jobdigest.hard_filter import hard_filter


def _check(*, title, location_raw="", description_text="", employment_hint=None):
    return hard_filter(
        load_profile(),
        title=title,
        location_raw=location_raw,
        description_text=description_text,
        employment_hint=employment_hint,
    )


def test_pune_intern_pass():
    status, reason = _check(title="Software Engineer Intern", location_raw="Pune")
    assert status == "pass"
    assert reason == "ok"


def test_remote_us_intern_reject():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Remote US",
        description_text="Remote US",
    )
    assert status == "reject"
    assert reason == "remote_us"


def test_staff_bengaluru_reject():
    status, reason = _check(title="Staff Engineer", location_raw="Bengaluru")
    assert status == "reject"
    assert reason == "title_reject"


def test_senior_software_engineer_reject():
    status, reason = _check(
        title="Senior Software Engineer (Data Platform)",
        location_raw="Bengaluru, India",
    )
    assert status == "reject"
    assert reason == "title_reject"


def test_sr_software_engineer_reject():
    status, reason = _check(
        title="Sr. Software Engineer - Ingestion",
        location_raw="Bengaluru",
    )
    assert status == "reject"
    assert reason == "title_reject"


def test_engineering_manager_reject():
    status, reason = _check(
        title="Engineering Manager",
        location_raw="Bengaluru",
    )
    assert status == "reject"
    assert reason == "title_reject"


def test_intern_three_plus_yoe_reject():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="Requires 3+ years of experience",
    )
    assert status == "reject"
    assert reason == "yoe"


def test_empty_loc_bangalore_in_jd_pass():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="",
        description_text="Work from Bangalore",
    )
    assert status == "pass"


def test_empty_loc_no_india_reject():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="",
        description_text="Build APIs",
    )
    assert status == "reject"
    assert reason == "location"


def test_software_engineer_india_0_1_pass():
    status, reason = _check(
        title="Software Engineer",
        location_raw="Bengaluru",
        description_text="0-1 years",
    )
    assert status == "pass"


def test_zero_to_three_years_not_yoe_reject():
    status, reason = _check(
        title="Software Engineer",
        location_raw="Bengaluru",
        description_text="0-3 years",
    )
    assert status == "pass"


def test_three_to_five_years_yoe_reject():
    status, reason = _check(
        title="Software Engineer",
        location_raw="Bengaluru",
        description_text="3-5 years",
    )
    assert status == "reject"
    assert reason == "yoe"


def test_operations_associate_reject():
    status, reason = _check(
        title="Operations Associate, Financial Crimes EDD/PEP",
        location_raw="Bengaluru",
    )
    assert status == "reject"
    assert reason in {"title_allow", "ops_non_swe"}


def test_verifications_operations_associate_reject():
    status, reason = _check(
        title="Verifications Operations Associate",
        location_raw="Bengaluru",
    )
    assert status == "reject"
    assert reason in {"title_allow", "ops_non_swe"}


def test_product_operations_intern_reject():
    status, reason = _check(
        title="Product Operations & Analytics Intern",
        location_raw="Bengaluru",
    )
    assert status == "reject"
    assert reason == "ops_non_swe"


def test_apprenticeship_ops_associate_reject():
    status, reason = _check(
        title="Operations Associate, Apprenticeship",
        location_raw="Bengaluru",
    )
    assert status == "reject"
    assert reason == "ops_non_swe"


def test_associate_software_engineer_pass():
    status, reason = _check(
        title="Associate Software Engineer",
        location_raw="Bengaluru",
        description_text="0-1 years",
    )
    assert status == "pass"
    assert reason == "ok"


def test_sde_intern_pass():
    status, reason = _check(title="SDE Intern", location_raw="Pune")
    assert status == "pass"
    assert reason == "ok"
