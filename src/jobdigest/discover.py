from __future__ import annotations

import argparse
import io
import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import redirect_stdout
from dataclasses import dataclass, replace
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import yaml

INTERN_INTL_RAW = (
    "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/INTERN_INTL.md"
)

INDIA_RE = re.compile(
    r"\b(india|bengaluru|bangalore|hyderabad|mumbai|pune|chennai|"
    r"noida|gurgaon|gurugram|delhi|ncr|kolkata)\b",
    re.IGNORECASE,
)

_TABLE_MARK = re.compile(r"<!--\s*(TABLE_[A-Z0-9_]*)\s*-->", re.IGNORECASE)
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)")
_BARE_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_TAG_RE = re.compile(r"<[^>]+>")
_SEP_RE = re.compile(r"^:?-{2,}:?$")
_SLUG_RE = re.compile(r"[^a-z0-9-]+")

_GH_HOSTS = frozenset(
    {
        "job-boards.greenhouse.io",
        "boards.greenhouse.io",
        "job-boards.eu.greenhouse.io",
        "boards.eu.greenhouse.io",
    }
)
_LEVER_HOSTS = frozenset({"jobs.lever.co", "jobs.eu.lever.co"})
_ASHBY_HOSTS = frozenset({"jobs.ashbyhq.com"})
_KILL_HOST_SUFFIXES = (
    "linkedin.com",
    "internshala.com",
    "otta.com",
    "discord.com",
    "discord.gg",
    "llmhire.com",
)
_GH_SKIP_SEGMENTS = frozenset({"embed", "jobs"})


@dataclass(frozen=True)
class InternRow:
    company_name: str
    location: str
    apply_url: str
    ats: str = ""
    token: str = ""
    skip_reason: str = ""


@dataclass(frozen=True)
class Unresolved:
    reason: str = "greenhouse_embed_unresolved"


@dataclass(frozen=True)
class Skip:
    reason: str


@dataclass(frozen=True)
class Diff:
    known: tuple[InternRow, ...]
    new: tuple[dict[str, Any], ...]
    unresolved: tuple[InternRow, ...]


def india_location(location: str) -> bool:
    return bool(INDIA_RE.search(location or ""))


def _host(parsed) -> str:
    host = (parsed.hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    return host


def _path_parts(parsed) -> list[str]:
    return [unquote(p) for p in parsed.path.split("/") if p]


def _is_kill_host(host: str, path: str) -> bool:
    if any(host == s or host.endswith("." + s) for s in _KILL_HOST_SUFFIXES):
        return True
    if "llmhire" in host:
        return True
    if host == "huggingface.co" and path.casefold().startswith("/jobs"):
        return True
    return False


def _is_workday(host: str) -> bool:
    return "myworkdayjobs.com" in host or "myworkday.com" in host


def _is_getro(host: str) -> bool:
    return host == "getro.com" or host.endswith(".getro.com")


def extract_token(url: str) -> tuple[str, str] | Unresolved | Skip:
    raw = (url or "").strip()
    if not raw:
        return Skip("empty_url")
    parsed = urlparse(raw)
    if not parsed.scheme:
        parsed = urlparse("https://" + raw)
    host = _host(parsed)
    path = parsed.path or ""
    query = parse_qs(parsed.query)
    parts = _path_parts(parsed)

    if _is_kill_host(host, path):
        return Skip("kill_list")
    if _is_workday(host):
        return Skip("workday")
    if _is_getro(host):
        return Skip("getro")

    if host in _GH_HOSTS:
        for_token = (query.get("for") or [""])[0].strip()
        first = parts[0] if parts else ""
        if first and first.casefold() not in _GH_SKIP_SEGMENTS:
            return ("greenhouse", first)
        if for_token:
            return ("greenhouse", for_token)
        return Unresolved()

    if host in _LEVER_HOSTS:
        if not parts:
            return Skip("unsupported")
        return ("lever", parts[0])

    if host in _ASHBY_HOSTS:
        if not parts:
            return Skip("unsupported")
        return ("ashby", parts[0])

    if "gh_jid" in query:
        return Unresolved()
    return Skip("unsupported")


def _table_bodies(text: str) -> list[str]:
    markers = list(_TABLE_MARK.finditer(text))
    bodies: list[str] = []
    for i, match in enumerate(markers):
        name = match.group(1).upper()
        if name == "TABLE_END" or name.endswith("_END"):
            continue
        start = match.end()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        bodies.append(text[start:end])
    return bodies


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    parts = [p.strip() for p in stripped.split("|")]
    if parts and parts[0] == "":
        parts = parts[1:]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def _is_separator(cells: list[str]) -> bool:
    if not cells:
        return False
    return all(_SEP_RE.fullmatch(c.replace(" ", "")) for c in cells)


def _cell_text(cell: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", cell)
    text = _TAG_RE.sub("", text)
    text = unescape(text)
    text = text.replace("**", "").replace("__", "")
    text = text.split("↳")[0]
    return " ".join(text.split()).strip()


def _apply_url(cell: str) -> str:
    hrefs = _HREF_RE.findall(cell)
    if hrefs:
        return hrefs[0].strip()
    md = _MD_LINK_RE.findall(cell)
    if md:
        return md[0].strip()
    bare = _BARE_URL_RE.findall(cell)
    if bare:
        return bare[0].rstrip(").,;]")
    return ""


def _col_index(headers: list[str], *needles: str) -> int | None:
    lowered = [h.casefold() for h in headers]
    for i, header in enumerate(lowered):
        if any(needle in header for needle in needles):
            return i
    return None


def _parse_table(body: str) -> list[InternRow]:
    rows: list[InternRow] = []
    header: list[str] | None = None
    name_i, loc_i, apply_i = 0, 2, 3
    for line in body.splitlines():
        cells = _split_row(line)
        if not cells or _is_separator(cells):
            continue
        labels = [_cell_text(c).casefold() for c in cells]
        if header is None and any("company" in h or h == "name" for h in labels):
            header = cells
            ni = _col_index(labels, "company", "name")
            li = _col_index(labels, "location", "city")
            ai = _col_index(labels, "application", "posting", "apply")
            if ai is None:
                ai = _col_index(labels, "link")
            if ni is not None:
                name_i = ni
            if li is not None:
                loc_i = li
            if ai is not None:
                apply_i = ai
            continue
        need = max(name_i, loc_i, apply_i)
        if len(cells) <= need:
            continue
        name = _cell_text(cells[name_i])
        location = _cell_text(cells[loc_i])
        apply_url = _apply_url(cells[apply_i])
        if not name and not apply_url:
            continue
        rows.append(InternRow(company_name=name, location=location, apply_url=apply_url))
    return rows


def parse_intern_markdown(text: str) -> list[InternRow]:
    rows: list[InternRow] = []
    for body in _table_bodies(text):
        rows.extend(_parse_table(body))
    return rows


def slugify_token(token: str) -> str:
    slug = _SLUG_RE.sub("-", (token or "").casefold()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug or "company"


def _company_rows(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    rows = payload.get("companies") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _inventory_keys(rows: Sequence[Mapping[str, Any]]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for row in rows:
        ats = str(row.get("ats") or "").casefold()
        token = str(row.get("token") or "").casefold()
        if ats and token:
            keys.add((ats, token))
    return keys


def _existing_ids(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    return {str(row.get("id") or "") for row in rows if row.get("id")}


def _name_has_greenhouse(name: str, rows: Sequence[Mapping[str, Any]]) -> bool:
    want = name.strip().casefold()
    if not want:
        return False
    for row in rows:
        if str(row.get("ats") or "").casefold() != "greenhouse":
            continue
        if str(row.get("name") or "").strip().casefold() == want:
            return True
    return False


def annotate_row(row: InternRow) -> InternRow:
    result = extract_token(row.apply_url)
    if isinstance(result, tuple):
        return replace(row, ats=result[0], token=result[1], skip_reason="")
    if isinstance(result, Unresolved):
        return replace(row, skip_reason=result.reason)
    return replace(row, skip_reason=result.reason)


def _assign_id(token: str, ats: str, used: set[str]) -> str:
    base = slugify_token(token)
    candidate = base
    if candidate in used:
        candidate = f"{base}-{ats.casefold()}"
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{ats.casefold()}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def diff_tokens(
    extracted: Sequence[InternRow],
    companies_payload: Mapping[str, Any] | None,
    unresolved: Sequence[InternRow] | None = None,
) -> Diff:
    inventory = _company_rows(companies_payload)
    keys = _inventory_keys(inventory)
    used_ids = _existing_ids(inventory)
    known: list[InternRow] = []
    new_map: dict[tuple[str, str], dict[str, Any]] = {}
    leftover_unresolved: list[InternRow] = []

    for row in extracted:
        ats = row.ats.casefold()
        token = row.token
        key = (ats, token.casefold())
        if key in keys:
            known.append(row)
            continue
        if key in new_map:
            prev = new_map[key]
            loc = row.location.strip()
            if loc and loc not in str(prev.get("notes") or ""):
                notes = str(prev.get("notes") or "")
                notes = notes.replace("; probe before enable", f", {loc}; probe before enable")
                prev["notes"] = notes
            continue
        suggested_id = _assign_id(token, row.ats, used_ids)
        new_map[key] = {
            "id": suggested_id,
            "name": row.company_name,
            "ats": row.ats,
            "token": token,
            "enabled": False,
            "notes": f"INTERN_INTL.md {row.location}; probe before enable".strip(),
        }

    for row in unresolved or ():
        if _name_has_greenhouse(row.company_name, inventory):
            known.append(row)
        else:
            leftover_unresolved.append(row)

    return Diff(known=tuple(known), new=tuple(new_map.values()), unresolved=tuple(leftover_unresolved))


def format_suggested_yaml(new_rows: Sequence[Mapping[str, Any]]) -> str:
    companies: list[dict[str, Any]] = []
    for row in new_rows:
        companies.append(
            {
                "id": str(row.get("id") or ""),
                "name": str(row.get("name") or ""),
                "ats": str(row.get("ats") or ""),
                "token": str(row.get("token") or ""),
                "enabled": False,
                "notes": str(row.get("notes") or ""),
            }
        )
    return yaml.safe_dump({"companies": companies}, sort_keys=False, allow_unicode=True)


def write_overlay(
    base_payload: Mapping[str, Any],
    new_rows: Sequence[Mapping[str, Any]],
    dest_dir: Path,
) -> Path:
    dest = dest_dir.resolve()
    from jobdigest.config import config_dir

    if dest == config_dir().resolve():
        raise RuntimeError("refusing to write overlay into hunt config dir")
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "companies.yaml"
    base = list(_company_rows(base_payload))
    extra = []
    for row in new_rows:
        extra.append(
            {
                "id": str(row.get("id") or ""),
                "name": str(row.get("name") or ""),
                "ats": str(row.get("ats") or ""),
                "token": str(row.get("token") or ""),
                "enabled": False,
                "notes": str(row.get("notes") or ""),
            }
        )
    path.write_text(
        yaml.safe_dump({"companies": base + extra}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def load_companies_payload(path: Path | None) -> dict[str, Any]:
    if path is not None:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"companies": []}
    from jobdigest.config import load_companies

    return load_companies()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m jobdigest.discover")
    parser.add_argument(
        "--markdown",
        type=Path,
        default=None,
        metavar="PATH",
        help="local INTERN_INTL.md (or any intern-list markdown)",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="GET raw GitHub markdown (maintainer only; not pytest)",
    )
    parser.add_argument(
        "--url",
        default=None,
        metavar="URL",
        help="raw markdown URL; requires --fetch",
    )
    parser.add_argument(
        "--companies",
        type=Path,
        default=None,
        metavar="PATH",
        help="inventory YAML to diff against (default: load_companies())",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="probe new ids via temp YAML overlay (does not write hunt YAML)",
    )
    return parser


def _read_markdown(args: argparse.Namespace, client: Any) -> tuple[str | None, int]:
    if args.markdown is not None:
        path = Path(args.markdown)
        if not path.is_file():
            print(f"error: markdown not found: {path}", file=sys.stderr)
            return None, 1
        return path.read_text(encoding="utf-8"), 0
    if not args.fetch:
        print("error: --markdown or --fetch required", file=sys.stderr)
        return None, 1
    if args.url and not args.fetch:
        print("error: --url requires --fetch", file=sys.stderr)
        return None, 1
    url = args.url or INTERN_INTL_RAW
    own = client is None
    if client is None:
        import httpx

        client = httpx.Client(headers={"User-Agent": "JobHunt/0.1"}, timeout=30.0)
    try:
        response = client.get(url)
        if getattr(response, "status_code", 500) >= 400:
            print(f"error: fetch failed HTTP {response.status_code}", file=sys.stderr)
            return None, 1
        return response.text, 0
    except Exception as exc:  # noqa: BLE001 — maintainer fetch only
        print(f"error: fetch failed: {exc}", file=sys.stderr)
        return None, 1
    finally:
        if own:
            client.close()


def _print_section(title: str, rows: Sequence[InternRow]) -> None:
    print(f"## {title}")
    for row in rows:
        reason = row.skip_reason or ""
        print(f"{row.company_name}\t{reason}\t{row.apply_url}")
    print()


def main(argv: Sequence[str] | None = None, client: Any | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    if args.url is not None and not args.fetch and args.markdown is None:
        print("error: --url requires --fetch", file=sys.stderr)
        return 1
    text, code = _read_markdown(args, client)
    if text is None:
        return code

    parsed = parse_intern_markdown(text)
    india_rows = [row for row in parsed if india_location(row.location)]
    classified = [annotate_row(row) for row in india_rows]
    extracted = [row for row in classified if row.ats and row.token]
    skipped = [row for row in classified if row.skip_reason and row.skip_reason != Unresolved.reason]
    unresolved_raw = [row for row in classified if row.skip_reason == Unresolved().reason]

    payload = load_companies_payload(args.companies)
    diff = diff_tokens(extracted, payload, unresolved=unresolved_raw)

    print("# discover intern_intl")
    print(
        f"parsed_rows={len(parsed)} india_rows={len(india_rows)} "
        f"extracted={len(extracted)} new={len(diff.new)} known={len(diff.known)} "
        f"skipped={len(skipped)} unresolved={len(diff.unresolved)}"
    )
    print()
    _print_section("skipped", skipped)
    _print_section("unresolved", diff.unresolved)

    if args.probe and diff.new:
        ids = ",".join(row["id"] for row in diff.new)
        old_env = os.environ.get("JOBDIGEST_CONFIG_DIR")
        with tempfile.TemporaryDirectory(prefix="jobdigest-discover-") as tmp:
            dest = Path(tmp)
            write_overlay(payload, diff.new, dest)
            os.environ["JOBDIGEST_CONFIG_DIR"] = str(dest)
            buf = io.StringIO()
            try:
                from jobdigest.probe import main as probe_main

                with redirect_stdout(buf):
                    probe_main(argv=["--ids", ids], client=client)
            finally:
                if old_env is None:
                    os.environ.pop("JOBDIGEST_CONFIG_DIR", None)
                else:
                    os.environ["JOBDIGEST_CONFIG_DIR"] = old_env
        print("## probe")
        probe_out = buf.getvalue()
        if probe_out:
            sys.stdout.write(probe_out)
            if not probe_out.endswith("\n"):
                print()
        print()

    print("## suggested yaml")
    sys.stdout.write(format_suggested_yaml(diff.new))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
