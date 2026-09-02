from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


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
    digest_hour: int
    digest_max: int


@dataclass(frozen=True)
class Models:
    score_model: str
    summarize_model: str
    score_prompt_version: str
    summarize_prompt_version: str
    batch_size: int


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
        digest_hour=int(d["digest_hour"]),
        digest_max=int(d["digest_max"]),
    )


def load_companies() -> dict[str, Any]:
    return _read_yaml("companies.yaml")


def load_portals() -> dict[str, Any]:
    return _read_yaml("portals.yaml")


def load_feeds() -> dict[str, Any]:
    return _read_yaml("feeds.yaml")


def load_models() -> Models:
    d = _read_yaml("models.yaml")
    score = d["score"]
    summarize = d["summarize"]
    score_model = str(score["model"])
    summarize_model = str(summarize["model"])
    if score_model.endswith(":free") or summarize_model.endswith(":free"):
        raise ValueError("models.yaml must not use OpenRouter :free ids")
    return Models(
        score_model=score_model,
        summarize_model=summarize_model,
        score_prompt_version=str(score["prompt_version"]),
        summarize_prompt_version=str(summarize["prompt_version"]),
        batch_size=int(d.get("batch_size", score.get("batch_size", 12))),
    )


def load_all() -> dict[str, Any]:
    return {
        "profile": load_profile(),
        "companies": load_companies(),
        "portals": load_portals(),
        "feeds": load_feeds(),
        "models": load_models(),
    }
