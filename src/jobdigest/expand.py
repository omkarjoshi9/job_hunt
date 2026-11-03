"""Widen the candidate pool: probe thousands of known ATS boards once and keep the
ones that list India entry-level engineering roles.

    python -m jobdigest.expand --tsv data/ats_board_tokens_gla.tsv

The result (``data/boards_india.tsv``) is picked up by ``collect`` automatically, so
the scheduled pipeline only ever fetches boards that are worth fetching.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import httpx

from jobdigest.config import Profile, load_profile
from jobdigest.hard_filter import function_ok, seniority_title
from jobdigest.paths import repo_root

DEFAULT_OUT = "data/boards_india.tsv"
_SUPPORTED = ("greenhouse", "lever", "ashby")
_ENTRYISH = re.compile(
    r"\bintern(?:ship)?s?\b|trainee|apprentice|new[- ]?grad|graduate|fresher|junior|"
    r"\bsde[- ]?(?:1|i)\b|engineer[- ]?i\b|associate|early[- ]career|entry|campus|"
    r"software (?:development )?engineer\b|\bsde\b|\bswe\b|"
    r"(?:backend|back-end|platform|ml|machine learning|ai|security|data|devops|"
    r"full[- ]?stack)\s+engineer\b",
    re.I,
)
_TITLE_REJECT = re.compile(
    r"\bstaff\b|principal|distinguished|\bsenior\b|\bsr\.?\b|\blead\b|\bmanager\b|"
    r"\bdirector\b|\bvp\b|\bhead of\b",
    re.I,
)
UA = "JobHunt/0.1 (+board discovery)"


@dataclass(frozen=True)
class Board:
    company: str
    ats: str
    token: str


@dataclass(frozen=True)
class Found:
    board: Board
    candidates: int
    sample: str


def read_boards(path: Path) -> list[Board]:
    boards: list[Board] = []
    seen: set[tuple[str, str]] = set()
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if i == 0 and line.lower().startswith("company\t"):
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        company, ats, token = parts[0].strip(), parts[1].strip().lower(), parts[2].strip()
        if ats not in _SUPPORTED or not token or (ats, token) in seen:
            continue
        seen.add((ats, token))
        boards.append(Board(company or token, ats, token))
    return boards


def _url(board: Board) -> str:
    if board.ats == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{board.token}/jobs"
    if board.ats == "lever":
        return f"https://api.lever.co/v0/postings/{board.token}?mode=json"
    return f"https://api.ashbyhq.com/posting-api/job-board/{board.token}"


def _jobs(board: Board, payload: object) -> list[tuple[str, str]]:
    """(title, location) pairs from one board's JSON payload."""
    out: list[tuple[str, str]] = []
    if board.ats == "lever":
        items = payload if isinstance(payload, list) else []
        for it in items:
            if not isinstance(it, dict):
                continue
            cats = it.get("categories") if isinstance(it.get("categories"), dict) else {}
            out.append((str(it.get("text") or ""), str(cats.get("location") or "")))
        return out
    items = payload.get("jobs") if isinstance(payload, dict) else None
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        loc = it.get("location")
        if isinstance(loc, dict):
            loc = loc.get("name")
        extra = it.get("secondaryLocations")
        if isinstance(extra, list):
            loc = " | ".join(
                [str(loc or "")]
                + [str(x.get("location") if isinstance(x, dict) else x) for x in extra]
            )
        out.append((str(it.get("title") or ""), str(loc or "")))
    return out


def _india(profile: Profile, location: str) -> bool:
    low = location.lower()
    return any(
        re.search(r"\b" + re.escape(k.lower()) + r"\b", low) for k in profile.india_keywords
    )


def promising(profile: Profile, title: str, location: str) -> bool:
    """Recall-oriented screen on title+location only (descriptions come later)."""
    return (
        _india(profile, location)
        and bool(_ENTRYISH.search(title))
        and not _TITLE_REJECT.search(title)
        and not seniority_title(title)
        and function_ok(title)
    )


def probe(
    board: Board,
    profile: Profile,
    client: httpx.Client,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> Found | None:
    try:
        resp = client.get(_url(board), timeout=20.0)
        if resp.status_code == 429:
            sleep(min(float(resp.headers.get("Retry-After", "5") or 5), 20.0))
            resp = client.get(_url(board), timeout=20.0)
        if resp.status_code != 200:
            return None
        payload = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    hits = [t for t, loc in _jobs(board, payload) if promising(profile, t, loc)]
    if not hits:
        return None
    return Found(board, len(hits), hits[0])


def expand(
    boards: Iterable[Board],
    profile: Profile,
    *,
    client: httpx.Client,
    workers: int = 12,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[int, int, int], None] | None = None,
) -> list[Found]:
    todo = list(boards)
    found: list[Found] = []
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(probe, b, profile, client, sleep=sleep) for b in todo]
        for fut in as_completed(futures):
            done += 1
            hit = fut.result()
            if hit is not None:
                found.append(hit)
            if progress and done % 500 == 0:
                progress(done, len(todo), len(found))
    found.sort(key=lambda f: (-f.candidates, f.board.company.lower()))
    return found


def write_found(path: Path, found: Iterable[Found]) -> None:
    lines = ["company\tats\ttoken\tcandidates\tsample"]
    for f in found:
        lines.append(
            f"{f.board.company}\t{f.board.ats}\t{f.board.token}\t{f.candidates}\t{f.sample}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m jobdigest.expand")
    ap.add_argument("--tsv", default="data/ats_board_tokens_gla.tsv")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0, help="probe only the first N boards")
    args = ap.parse_args(argv)
    root = repo_root()
    tsv, out = Path(args.tsv), Path(args.out)
    tsv = tsv if tsv.is_absolute() else root / tsv
    out = out if out.is_absolute() else root / out
    boards = read_boards(tsv)
    if args.limit:
        boards = boards[: args.limit]
    profile = load_profile()
    print(f"probing {len(boards)} boards with {args.workers} workers", flush=True)
    client = httpx.Client(headers={"User-Agent": UA}, follow_redirects=False)
    try:
        found = expand(
            boards,
            profile,
            client=client,
            workers=args.workers,
            progress=lambda d, n, h: print(f"  {d}/{n} probed, {h} boards kept", flush=True),
        )
    finally:
        client.close()
    write_found(out, found)
    print(f"kept {len(found)} boards -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
