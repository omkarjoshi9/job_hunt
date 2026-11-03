from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from jobdigest.paths import repo_root

log = logging.getLogger("jobdigest.config")


def config_dir() -> Path:
    override = os.environ.get("JOBDIGEST_CONFIG_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "config"


def _yaml_path(name: str) -> Path:
    path = config_dir() / name
    if path.is_file():
        return path
    example = config_dir() / f"{Path(name).stem}.example.yaml"
    if example.is_file():
        log.warning("%s not found; falling back to example config %s", path, example)
        return example
    raise FileNotFoundError(path)


def _read_yaml(name: str) -> Any:
    path = _yaml_path(name)
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        raise ValueError(f"empty yaml: {path}")
    return data


@dataclass(frozen=True)
class Profile:
    name: str
    college: str
    degree: str
    batch: int
    cgpa: float
    location: str
    relocate: list[str]
    work_auth: str
    role_types: list[str]
    skills: list[str]
    internships: list[str]
    india_keywords: list[str]
    title_allow_intern: str
    title_allow_newgrad: str
    title_reject_regex: str
    reject_remote_us: bool
    min_yoe_reject: int
    timezone: str
    digest_max: int
    watchlist: list[str]
    urgent_score_min: int
    urgent_daily_cap: int
    github_url: str = ""
    top_companies: list[str] = field(default_factory=list)
    digest_min_quality: float = 50.0
    digest_per_company: int = 1
    digest_per_portal: int = 2
    digest_company_cooldown_days: int = 3
    digest_max_age_days: int = 21


@dataclass(frozen=True)
class Models:
    score_model: str
    summarize_model: str
    score_prompt_version: str
    summarize_prompt_version: str
    batch_size: int
    blurb_model: str | None = None
    blurb_prompt_version: str | None = None


def _int_field(data: dict, key: str, default: int) -> int:
    if key not in data or data[key] is None or data[key] == "":
        return default
    return int(data[key])


def load_profile() -> Profile:
    d = _read_yaml("profile.yaml")
    return Profile(
        name=str(d["name"]),
        college=str(d["college"]),
        degree=str(d["degree"]),
        batch=int(d["batch"]),
        cgpa=float(d["cgpa"]),
        location=str(d["location"]),
        relocate=list(d["relocate"]),
        work_auth=str(d["work_auth"]),
        role_types=list(d["role_types"]),
        skills=list(d["skills"]),
        internships=list(d["internships"]),
        india_keywords=list(d["india_keywords"]),
        title_allow_intern=str(d["title_allow_intern"]),
        title_allow_newgrad=str(d["title_allow_newgrad"]),
        title_reject_regex=str(d["title_reject_regex"]),
        reject_remote_us=bool(d["reject_remote_us"]),
        min_yoe_reject=int(d["min_yoe_reject"]),
        timezone=str(d["timezone"]),
        digest_max=int(d["digest_max"]),
        watchlist=[str(x) for x in (d.get("watchlist") or [])],
        urgent_score_min=_int_field(d, "urgent_score_min", 85),
        urgent_daily_cap=_int_field(d, "urgent_daily_cap", 3),
        github_url=str(d.get("github_url") or ""),
        top_companies=[str(x) for x in (d.get("top_companies") or [])],
        digest_min_quality=float(d.get("digest_min_quality", 50.0)),
        digest_per_company=_int_field(d, "digest_per_company", 1),
        digest_company_cooldown_days=_int_field(d, "digest_company_cooldown_days", 3),
        digest_max_age_days=_int_field(d, "digest_max_age_days", 21),
        digest_per_portal=_int_field(d, "digest_per_portal", 2),
    )


def load_companies() -> dict[str, Any]:
    return _read_yaml("companies.yaml")


def discovered_boards_path() -> Path | None:
    """data/boards_india.tsv (written by ``python -m jobdigest.expand``).

    JOBDIGEST_DISCOVERED_BOARDS overrides the path; "off" disables the feature.
    """
    raw = (os.environ.get("JOBDIGEST_DISCOVERED_BOARDS") or "").strip()
    if raw.lower() in {"off", "none", "0", "false"}:
        return None
    return Path(raw) if raw else repo_root() / "data" / "boards_india.tsv"


def load_discovered_boards(path: Path | None = None) -> list[dict[str, Any]]:
    """Company rows for the boards found by ``expand`` (empty when there are none)."""
    path = path or discovered_boards_path()
    if path is None or not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        parts = line.split("\t")
        if i == 0 and parts[:1] == ["company"]:
            continue
        if len(parts) < 3 or not parts[2].strip():
            continue
        name, ats, token = parts[0].strip(), parts[1].strip().lower(), parts[2].strip()
        rows.append(
            {
                "id": f"{ats}:{token}",
                "name": name or token,
                "ats": ats,
                "token": token,
                "enabled": True,
                "notes": "discovered by jobdigest.expand",
            }
        )
    return rows


def load_portals() -> dict[str, Any]:
    return _read_yaml("portals.yaml")


def load_feeds() -> dict[str, Any]:
    return _read_yaml("feeds.yaml")


def load_marketplaces() -> dict[str, Any]:
    return _read_yaml("marketplaces.yaml")


def load_models() -> Models:
    d = _read_yaml("models.yaml")
    score = d["score"]
    summarize = d["summarize"]
    score_model = str(score["model"])
    summarize_model = str(summarize["model"])
    if score_model.endswith(":free") or summarize_model.endswith(":free"):
        raise ValueError("models.yaml must not use OpenRouter :free ids")
    blurb = d.get("blurb") or {}
    if blurb:
        bm = str(blurb["model"])
        if bm.endswith(":free") or bm == "openrouter/auto":
            raise ValueError("blurb model must not be :free or openrouter/auto")
        blurb_model = bm
        blurb_prompt_version = str(blurb["prompt_version"])
    else:
        blurb_model = None
        blurb_prompt_version = None
    return Models(
        score_model=score_model,
        summarize_model=summarize_model,
        score_prompt_version=str(score["prompt_version"]),
        summarize_prompt_version=str(summarize["prompt_version"]),
        batch_size=int(score.get("batch_size", d.get("batch_size", 12))),
        blurb_model=blurb_model,
        blurb_prompt_version=blurb_prompt_version,
    )


def load_all() -> dict[str, Any]:
    return {
        "profile": load_profile(),
        "companies": load_companies(),
        "portals": load_portals(),
        "feeds": load_feeds(),
        "marketplaces": load_marketplaces(),
        "models": load_models(),
    }
