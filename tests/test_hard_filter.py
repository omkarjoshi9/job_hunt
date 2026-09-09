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


def test_increase_not_ncr():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="",
        description_text="Our mission is to increase the GDP of the internet",
    )
    assert status == "reject"
    assert reason == "location"


def test_indiana_location_not_india():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Indianapolis, Indiana",
    )
    assert status == "reject"
    assert reason == "location"


def test_delhi_ncr_still_india():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Delhi NCR",
    )
    assert status == "pass"
    assert reason == "ok"


def test_postman_us_intern_bangalore_founded_reject():
    status, reason = _check(
        title="AI Engineer, Internship - Summer 2026 - Applications Open Now",
        location_raw=(
            "Berkeley, California, United States; San Francisco, California, "
            "United States | Berkeley, California, United States"
        ),
        description_text="Offices in San Francisco, London, and Bangalore - where Postman was founded.",
    )
    assert status == "reject"
    assert reason == "location"


def test_stripe_swe_intern_bengaluru_pass():
    status, reason = _check(
        title="Software Engineer, Intern",
        location_raw="Bengaluru | Bengaluru, Karnataka, India",
        description_text="At least 1 year of university education",
    )
    assert status == "pass"
    assert reason == "ok"


def test_internal_audit_not_intern():
    status, reason = _check(
        title="Internal Audit - Treasury",
        location_raw="Bengaluru, India",
    )
    assert status == "reject"
    assert reason == "title_allow"


def test_internal_tools_designer_not_intern():
    status, reason = _check(
        title="Product Designer, Internal Tools",
        location_raw="Bengaluru, India",
    )
    assert status == "reject"
    assert reason == "title_allow"


def test_campus_recruiter_reject():
    status, reason = _check(
        title="Campus Recruiter",
        location_raw="Bengaluru, India",
    )
    assert status == "reject"
    assert reason == "title_allow"


def test_summer_internships_still_intern():
    status, reason = _check(
        title="Summer Internships",
        location_raw="Bengaluru, India",
    )
    intern_ok = status == "pass" or reason != "title_allow"
    assert intern_ok


def test_university_recruiter_reject():
    status, reason = _check(
        title="University Recruiter",
        location_raw="Bengaluru, India",
    )
    assert status == "reject"
    assert reason == "title_allow"


def test_university_graduate_still_pass():
    status, reason = _check(
        title="University Graduate Software Engineer",
        location_raw="Bengaluru, India",
    )
    assert status == "pass"
    assert reason == "ok"


def test_notion_infra_at_least_4_past_2000_reject():
    status, reason = _check(
        title="Software Engineer, Infrastructure",
        location_raw="Hyderabad, India",
        description_text=("x" * 2500) + "at least 4 years of experience",
    )
    assert status == "reject"
    assert reason == "yoe"


def test_stripe_internal_systems_8plus_late_reject():
    status, reason = _check(
        title="Software Engineer, Internal Systems",
        location_raw="Bengaluru, India",
        description_text=("x" * 4800) + "Minimum requirements 8+ years of experience",
    )
    assert status == "reject"
    assert reason == "yoe"


def test_over_10_years_yoe_reject():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="Over 10 years of experience",
    )
    assert status == "reject"
    assert reason == "yoe"


def test_plus_4_years_yoe_reject():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="related field plus 4 years of experience",
    )
    assert status == "reject"
    assert reason == "yoe"


def test_at_least_1_year_not_yoe_reject():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Bengaluru",
        description_text="At least 1 year of university education",
    )
    assert status == "pass"
    assert reason == "ok"


def test_benefits_plus_4_years_vesting_not_yoe():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="benefits plus 4 years vesting",
    )
    assert status == "pass"
    assert reason == "ok"


def test_founded_more_than_10_years_ago_not_yoe():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="founded more than 10 years ago",
    )
    assert status == "pass"
    assert reason == "ok"


def test_us_dash_remote_reject():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="US - Remote | US",
    )
    assert status == "reject"
    assert reason == "remote_us"


def test_us_perm_reject():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Seattle, WA | US-PERM",
    )
    assert status == "reject"
    assert reason == "remote_us"


def test_empty_loc_bangalore_us_remote_in_jd_reject():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="",
        description_text="Work from Bangalore. Role is US - Remote",
    )
    assert status == "reject"
    assert reason == "remote_us"


def test_two_to_eight_years_yoe_reject():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="2-8 years",
    )
    assert status == "reject"
    assert reason == "yoe"


def test_two_en_dash_five_years_yoe_reject():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="2–5 years",
    )
    assert status == "reject"
    assert reason == "yoe"


def test_two_to_eight_yrs_words_yoe_reject():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Bengaluru",
        description_text="2 to 8 years",
    )
    assert status == "reject"
    assert reason == "yoe"


def test_two_to_eight_plus_years_yoe_reject():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="2-8+ years",
    )
    assert status == "reject"
    assert reason == "yoe"


def test_one_to_two_years_not_yoe_reject():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="1-2 years",
    )
    assert status == "pass"
    assert reason == "ok"


def test_two_to_three_months_not_yoe_reject():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="2-3 months",
    )
    assert status == "pass"
    assert reason == "ok"


def test_phase04_one_to_three_years_yoe_reject():
    """Phase 0.4: 1-3 years rejects (lo>=1 and hi>=min_yoe_reject). Conscious junior-band drop."""
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="1-3 years",
    )
    assert status == "reject"
    assert reason == "yoe"


def test_phase04_two_to_three_years_yoe_reject():
    """Phase 0.4: 2-3 years rejects."""
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Bengaluru",
        description_text="2-3 years",
    )
    assert status == "reject"
    assert reason == "yoe"


def test_phase04_two_to_three_years_preferred_yoe_reject():
    """Phase 0.4: 2-3 years preferred still matches the range."""
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="2-3 years preferred",
    )
    assert status == "reject"
    assert reason == "yoe"


def test_two_plus_years_not_range_yoe_reject():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="2+ years",
    )
    assert status == "pass"
    assert reason == "ok"


def test_minimum_two_years_not_yoe_reject():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="Minimum 2 years",
    )
    assert status == "pass"
    assert reason == "ok"


def test_zero_to_five_years_not_yoe_reject():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="0-5 years",
    )
    assert status == "pass"
    assert reason == "ok"


def test_between_two_and_eight_years_not_range_match():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="between 2 and 8 years",
    )
    assert status == "pass"
    assert reason == "ok"


def test_two_to_eight_yoe_not_range_match():
    status, reason = _check(
        title="Software Engineer Intern",
        location_raw="Pune",
        description_text="2-8 YOE",
    )
    assert status == "pass"
    assert reason == "ok"


def test_tech_leadership_intern_not_title_reject():
    status, reason = _check(
        title="Tech Leadership Intern",
        location_raw="Pune",
    )
    assert status == "pass"
    assert reason == "ok"


def test_fellowship_program_intern_not_title_reject():
    status, reason = _check(
        title="Fellowship Program Intern",
        location_raw="Bengaluru",
    )
    assert status == "pass"
    assert reason == "ok"


def test_head_office_intern_not_title_reject():
    status, reason = _check(
        title="Head Office Intern",
        location_raw="Pune",
    )
    assert status == "pass"
    assert reason == "ok"


def test_directory_in_title_does_not_reject():
    status, reason = _check(
        title="India Engineering Directory Intern",
        location_raw="Bengaluru",
    )
    assert status == "pass"
    assert reason == "ok"


def test_lead_software_engineer_intern_still_title_reject():
    status, reason = _check(
        title="Lead Software Engineer Intern",
        location_raw="Pune",
    )
    assert status == "reject"
    assert reason == "title_reject"


def test_tech_lead_intern_still_title_reject():
    status, reason = _check(
        title="Tech Lead Intern",
        location_raw="Pune",
    )
    assert status == "reject"
    assert reason == "title_reject"


def test_research_fellow_intern_still_title_reject():
    status, reason = _check(
        title="Research Fellow Intern",
        location_raw="Bengaluru",
    )
    assert status == "reject"
    assert reason == "title_reject"


def test_director_of_engineering_still_title_reject():
    status, reason = _check(
        title="Director of Engineering",
        location_raw="Bengaluru",
    )
    assert status == "reject"
    assert reason == "title_reject"


def test_vp_engineering_still_title_reject():
    status, reason = _check(
        title="VP Engineering",
        location_raw="Bengaluru",
    )
    assert status == "reject"
    assert reason == "title_reject"


def test_head_of_engineering_still_title_reject():
    status, reason = _check(
        title="Head of Engineering",
        location_raw="Bengaluru",
    )
    assert status == "reject"
    assert reason == "title_reject"


def test_svp_engineering_not_title_reject():
    status, reason = _check(
        title="SVP Engineering",
        location_raw="Bengaluru",
    )
    assert status == "reject"
    assert reason != "title_reject"
