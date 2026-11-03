"""Company tiers: a small curated list of top-tier employers.

Used to (a) let frontend/QA roles through only at top companies and (b) boost fit.
Extend per user with ``top_companies`` in profile.yaml.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from jobdigest.normalize import normalize_company

TOP_COMPANIES = frozenset(
    {
        # global
        "google", "alphabet", "microsoft", "amazon", "aws", "meta", "apple", "netflix",
        "nvidia", "openai", "anthropic", "deepmind", "stripe", "databricks", "uber",
        "airbnb", "linkedin", "adobe", "salesforce", "atlassian", "cloudflare", "figma",
        "notion", "snowflake", "datadog", "coinbase", "shopify", "palantir", "github",
        "gitlab", "hashicorp", "mongodb", "confluent", "elastic", "twilio", "intuit",
        "servicenow", "vmware", "cisco", "qualcomm", "intel", "amd", "samsung",
        # quant / trading
        "jane street", "citadel", "de shaw", "d e shaw", "tower research",
        "hudson river trading", "graviton", "optiver", "squarepoint", "quadeye",
        "alphagrep", "world quant", "worldquant",
        # india product
        "razorpay", "cred", "zepto", "swiggy", "zomato", "meesho", "phonepe", "flipkart",
        "groww", "postman", "freshworks", "browserstack", "sarvam", "ola", "dream11",
        "cars24", "slice", "zerodha", "juspay", "rippling", "navi", "sharechat",
        "myntra", "lenskart", "unacademy", "hasura", "chargebee", "clevertap",
    }
)


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", normalize_company(name or "").casefold()).strip()


def is_top_company(name: str, extra: Iterable[str] = ()) -> bool:
    key = _key(name)
    if not key:
        return False
    pool = TOP_COMPANIES | {_key(x) for x in extra if x}
    if key in pool:
        return True
    words = set(key.split())
    return any(" " not in t and t in words for t in pool) or any(
        " " in t and t in f" {key} " for t in pool
    )
