"""Shared helpers for the docs tooling: AST symbol inventory + module -> doc-page map.

Run the other tools from the repo root:

    python docs/_tools/build_reference.py   # regenerate docs/reference/symbol-index.md
    python docs/_tools/check_docs.py        # verify links, source references, coverage
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_GLOBS = ("src/jobdigest/**/*.py", "scripts/*.py")

# source file (relative to ROOT) -> documentation page (relative to docs/)
DOC_PAGE: dict[str, str] = {
    "src/jobdigest/__init__.py": "modules/foundation/cli-and-entrypoints.md",
    "src/jobdigest/__main__.py": "modules/foundation/cli-and-entrypoints.md",
    "src/jobdigest/cli.py": "modules/foundation/cli-and-entrypoints.md",
    "src/jobdigest/config.py": "modules/foundation/config.md",
    "src/jobdigest/paths.py": "modules/foundation/paths-envfile-logutil.md",
    "src/jobdigest/envfile.py": "modules/foundation/paths-envfile-logutil.md",
    "src/jobdigest/logutil.py": "modules/foundation/paths-envfile-logutil.md",
    "src/jobdigest/normalize.py": "modules/foundation/normalize.md",
    "src/jobdigest/cron.py": "modules/foundation/cron-gate.md",
    "src/jobdigest/db/__init__.py": "modules/storage/overview.md",
    "src/jobdigest/db/_common.py": "modules/storage/overview.md",
    "src/jobdigest/db/connection.py": "modules/storage/connection-and-postgres-shim.md",
    "src/jobdigest/db/schema.py": "modules/storage/schema-and-migrations.md",
    "src/jobdigest/db/jobs.py": "modules/storage/jobs.md",
    "src/jobdigest/db/runs.py": "modules/storage/runs-scores-errors.md",
    "src/jobdigest/db/applications.py": "modules/storage/applications.md",
    "src/jobdigest/db/contacts.py": "modules/storage/contacts-and-digest-queries.md",
    "src/jobdigest/db/digest_queries.py": "modules/storage/contacts-and-digest-queries.md",
    "src/jobdigest/ats/__init__.py": "modules/sources/ats-overview.md",
    "src/jobdigest/ats/base.py": "modules/sources/ats-base-and-http-client.md",
    "src/jobdigest/ats/http_client.py": "modules/sources/ats-base-and-http-client.md",
    "src/jobdigest/ats/greenhouse.py": "modules/sources/ats-greenhouse.md",
    "src/jobdigest/ats/lever.py": "modules/sources/ats-lever.md",
    "src/jobdigest/ats/ashby.py": "modules/sources/ats-ashby.md",
    "src/jobdigest/ats/workable.py": "modules/sources/ats-workable.md",
    "src/jobdigest/ats/smartrecruiters.py": "modules/sources/ats-smartrecruiters.md",
    "src/jobdigest/ats/bamboohr.py": "modules/sources/ats-bamboohr.md",
    "src/jobdigest/probe.py": "modules/sources/probe.md",
    "src/jobdigest/portals/__init__.py": "modules/sources/portals-overview.md",
    "src/jobdigest/portals/session.py": "modules/sources/portals-session-and-robots.md",
    "src/jobdigest/portals/robots.py": "modules/sources/portals-session-and-robots.md",
    "src/jobdigest/portals/generic.py": "modules/sources/portals-parsers.md",
    "src/jobdigest/portals/foss_united.py": "modules/sources/portals-parsers.md",
    "src/jobdigest/portals/hasjob.py": "modules/sources/portals-parsers.md",
    "src/jobdigest/portals/null_community.py": "modules/sources/portals-parsers.md",
    "src/jobdigest/feeds.py": "modules/sources/feeds.md",
    "src/jobdigest/marketplaces/__init__.py": "modules/sources/marketplaces.md",
    "src/jobdigest/marketplaces/jobspy_bridge.py": "modules/sources/marketplaces.md",
    "src/jobdigest/discover.py": "modules/sources/discover.md",
    "src/jobdigest/expand.py": "modules/sources/expand.md",
    "scripts/_probe_common.py": "modules/sources/research-scripts.md",
    "scripts/probe_ats_tokens.py": "modules/sources/research-scripts.md",
    "scripts/probe_ats_tokens_wave2.py": "modules/sources/research-scripts.md",
    "src/jobdigest/collect.py": "modules/collection/collect-orchestrator.md",
    "src/jobdigest/hard_filter.py": "modules/filtering/hard-filter.md",
    "src/jobdigest/legitimacy.py": "modules/filtering/legitimacy.md",
    "src/jobdigest/score.py": "modules/ranking/score-llm.md",
    "src/jobdigest/openrouter.py": "modules/ranking/openrouter.md",
    "src/jobdigest/fit.py": "modules/ranking/fit-and-tiers.md",
    "src/jobdigest/tiers.py": "modules/ranking/fit-and-tiers.md",
    "src/jobdigest/rank.py": "modules/ranking/rank-and-selection.md",
    "src/jobdigest/summarize.py": "modules/ranking/summarize.md",
    "src/jobdigest/blurb.py": "modules/ranking/blurb.md",
    "src/jobdigest/digest.py": "modules/delivery/digest.md",
    "src/jobdigest/telegram.py": "modules/delivery/telegram.md",
    "src/jobdigest/bot.py": "modules/delivery/bot.md",
    "src/jobdigest/followups.py": "modules/delivery/followups-and-health.md",
    "src/jobdigest/health.py": "modules/delivery/followups-and-health.md",
    "src/jobdigest/sheet.py": "modules/delivery/sheet.md",
    "src/jobdigest/email_draft.py": "modules/delivery/email-drafts.md",
    "src/jobdigest/contacts/__init__.py": "modules/contacts/overview.md",
    "src/jobdigest/contacts/types.py": "modules/contacts/overview.md",
    "src/jobdigest/contacts/pipeline.py": "modules/contacts/pipeline-and-run.md",
    "src/jobdigest/contacts/run.py": "modules/contacts/pipeline-and-run.md",
    "src/jobdigest/contacts/domain.py": "modules/contacts/domain-and-pattern.md",
    "src/jobdigest/contacts/pattern.py": "modules/contacts/domain-and-pattern.md",
    "src/jobdigest/contacts/github.py": "modules/contacts/github-and-jd.md",
    "src/jobdigest/contacts/jd.py": "modules/contacts/github-and-jd.md",
    "src/jobdigest/contacts/rank.py": "modules/contacts/rank-and-email.md",
    "src/jobdigest/contacts/email.py": "modules/contacts/rank-and-email.md",
}


# Extra pages that may hold the entry for a symbol of a file whose "home" page is DOC_PAGE[file].
EXTRA_PAGES: dict[str, list[str]] = {
    "src/jobdigest/ats/__init__.py": ["modules/sources/ats-base-and-http-client.md"],
    "src/jobdigest/contacts/types.py": ["modules/contacts/pipeline-and-run.md"],
}


def pages_for(path: str) -> list[str]:
    home = DOC_PAGE.get(path)
    return ([home] if home else []) + EXTRA_PAGES.get(path, [])


@dataclass
class Symbol:
    name: str  # "fn" or "Class.method"
    kind: str  # function | class | method
    line: int
    length: int
    private: bool
    file: str = ""


@dataclass
class FileInfo:
    path: str
    lines: int
    symbols: list[Symbol] = field(default_factory=list)
    constants: list[str] = field(default_factory=list)


def source_files() -> list[Path]:
    found: list[Path] = []
    for pattern in SRC_GLOBS:
        found.extend(ROOT.glob(pattern))
    return sorted(set(found))


def scan(path: Path) -> FileInfo:
    rel = path.relative_to(ROOT).as_posix()
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    info = FileInfo(rel, len(text.splitlines()))

    def add(node, name, kind):
        info.symbols.append(
            Symbol(
                name,
                kind,
                node.lineno,
                (node.end_lineno or node.lineno) - node.lineno + 1,
                name.split(".")[-1].startswith("_") and not name.endswith("__"),
                rel,
            )
        )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node, node.name, "function")
        elif isinstance(node, ast.ClassDef):
            add(node, node.name, "class")
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add(sub, f"{node.name}.{sub.name}", "method")
        elif isinstance(node, ast.Assign):
            info.constants += [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            info.constants.append(node.target.id)
    return info


def scan_all() -> list[FileInfo]:
    return [scan(p) for p in source_files()]


def heading_slug(title: str) -> str:
    """GitHub-style anchor for a heading line's text."""
    import re

    text = title.strip().lower()
    text = text.replace("`", "")
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"\s", "-", text.strip())
