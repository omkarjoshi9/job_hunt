"""Verify the documentation against the source tree.

    python docs/_tools/check_docs.py [--mermaid-dir DIR] [--json OUT]

Checks
  1. every relative markdown link resolves to a file, and its #anchor to a heading
  2. every backticked ``path/to/file.py:LINE`` reference points at an existing file/line
  3. every ``Location`` line under a ``### `name` `` heading names a def/class at that line
  4. every ```mermaid block parses (needs ``npm i mermaid jsdom``; pass --mermaid-dir)
  5. coverage: which source symbols have a dedicated heading on their mapped page
Exit status is non-zero if any hard check (1-4) fails.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from symbols import DOC_PAGE, ROOT, heading_slug, pages_for, scan_all  # noqa: E402

DOCS = ROOT / "docs"
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)\)")
REF = re.compile(r"`((?:src|scripts|tests|\.github)/[\w./-]+\.(?:py|yml|yaml|j2|toml|sh|tsv|mjs)):(\d+)(?:-(\d+))?`")
LOC = re.compile(r"\*\*Location\*\*\s*`?([\w./-]+\.py):(\d+)`?")


def md_files() -> list[Path]:
    return sorted(p for p in DOCS.rglob("*.md") if "_tools" not in p.parts)


def headings(path: Path) -> list[tuple[int, str, str]]:
    out, seen, fence = [], Counter(), False
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("```"):
            fence = not fence
        if fence or not re.match(r"#{1,6} ", line):
            continue
        title = line.lstrip("#").strip()
        slug = heading_slug(title)
        k = seen[slug]
        seen[slug] += 1
        out.append((n, title, slug if k == 0 else f"{slug}-{k}"))
    return out


def check_links(files, errors):
    anchors = {f: {s for _, _, s in headings(f)} for f in files}
    for f in files:
        fence = False
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if line.startswith("```"):
                fence = not fence
            if fence:
                continue
            for target in LINK.findall(re.sub(r"`[^`]*`", "", line)):
                if re.match(r"[a-z]+:", target) or target.startswith("#") and False:
                    continue
                path_part, _, anchor = target.partition("#")
                dest = f if not path_part else (f.parent / path_part).resolve()
                if path_part and not dest.exists():
                    errors.append(f"{f.relative_to(ROOT)}:{n}: broken link -> {target}")
                    continue
                if anchor and dest.suffix == ".md" and dest in anchors and anchor not in anchors[dest]:
                    errors.append(f"{f.relative_to(ROOT)}:{n}: missing anchor -> {target}")
                elif anchor and dest.suffix == ".md" and dest not in anchors and dest.exists():
                    pass


def check_refs(files, errors):
    stats = Counter()
    for f in files:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for path, a, b in REF.findall(line):
                stats["refs"] += 1
                src = ROOT / path
                if not src.exists():
                    errors.append(f"{f.relative_to(ROOT)}:{n}: reference to missing file {path}")
                    continue
                total = len(src.read_text(encoding="utf-8").splitlines())
                if int(a) > total or (b and int(b) > total):
                    errors.append(f"{f.relative_to(ROOT)}:{n}: {path}:{a} beyond end of file ({total} lines)")
    return stats


def resolve_source(path: str, page: Path) -> Path | None:
    """Resolve a (possibly bare) file name from a Location line to a source file.

    Pages often write ``collect.py:564``. Try the literal path first, then match by
    basename; when several files share a basename (``rank.py``, ``__init__.py``) prefer the
    ones mapped to this page in ``DOC_PAGE``.
    """
    direct = ROOT / path
    if direct.exists():
        return direct
    base = Path(path).name
    candidates = [p for p in ROOT.glob(f"**/{base}") if "tests" not in p.parts and ".venv" not in p.parts
                  and (p.relative_to(ROOT).as_posix() in DOC_PAGE)]
    if len(candidates) > 1:
        rel_page = page.relative_to(DOCS).as_posix()
        mapped = [p for p in candidates if DOC_PAGE.get(p.relative_to(ROOT).as_posix()) == rel_page]
        candidates = mapped or candidates
    return candidates[0] if len(candidates) == 1 else None


def check_locations(files, errors):
    checked = 0
    for f in files:
        lines = f.read_text(encoding="utf-8").splitlines()
        current = None
        for n, line in enumerate(lines, 1):
            m = re.match(r"#{2,6} `([\w.]+)`", line)
            if m:
                current = (m.group(1), n)
                continue
            loc = LOC.search(line)
            if loc and current and n - current[1] <= 6:
                name = current[0].split(".")[-1]
                path, ln = loc.group(1), int(loc.group(2))
                src = resolve_source(path, f)
                if src is None:
                    errors.append(f"{f.relative_to(ROOT)}:{n}: Location file missing or ambiguous: {path}")
                    continue
                text = src.read_text(encoding="utf-8").splitlines()
                window = "\n".join(text[max(0, ln - 1): ln + 1]) if ln <= len(text) else ""
                checked += 1
                pat = rf"\b(def|class)\s+{re.escape(name)}\b|^\s*{re.escape(name)}\s*(:[^=]*)?="
                if not re.search(pat, window, re.M):
                    errors.append(
                        f"{f.relative_to(ROOT)}:{n}: `{current[0]}` Location {path}:{ln} "
                        f"does not point at its definition"
                    )
                current = None
    return checked


def check_mermaid(files, errors, mdir):
    blocks = []
    for f in files:
        fence, buf, start = False, [], 0
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip().startswith("```mermaid"):
                fence, buf, start = True, [], n
            elif fence and line.strip().startswith("```"):
                blocks.append({"where": f"{f.relative_to(ROOT)}:{start}", "code": "\n".join(buf)})
                fence = False
            elif fence:
                buf.append(line)
    if not blocks:
        return 0, 0
    if not mdir:
        return len(blocks), -1
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
        json.dump(blocks, tf)
    script = Path(__file__).parent / "mermaid_check.mjs"
    run = subprocess.run(["node", str(script), tf.name], cwd=mdir, capture_output=True, text=True)
    bad = 0
    for line in run.stdout.splitlines():
        if line.startswith("FAIL"):
            errors.append("mermaid " + line)
            bad += 1
    return len(blocks), bad


def coverage():
    rows, per_file = [], {}
    for info in scan_all():
        page = DOC_PAGE.get(info.path)
        heads = []
        for pg in pages_for(info.path):
            if (DOCS / pg).is_file():
                heads += [t.replace("`", "") for _, t, _ in headings(DOCS / pg)]
        words = {w for t in heads for w in re.split(r"\W+", t) if w} | set(heads)
        documented = missing = 0
        for s in info.symbols:
            short = s.name.split(".")[-1]
            ok = s.name in words or short in words
            documented += ok
            if not ok:
                missing += 1
                rows.append((info.path, s))
        per_file[info.path] = (page, len(info.symbols), documented, bool(page and (DOCS / page).is_file()))
    return per_file, rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mermaid-dir")
    ap.add_argument("--json")
    args = ap.parse_args()
    files = md_files()
    errors: list[str] = []
    check_links(files, errors)
    ref_stats = check_refs(files, errors)
    loc_checked = check_locations(files, errors)
    n_blocks, n_bad = check_mermaid(files, errors, args.mermaid_dir)
    per_file, missing = coverage()

    total_syms = sum(v[1] for v in per_file.values())
    total_doc = sum(v[2] for v in per_file.values())
    print(f"markdown files: {len(files)}")
    print(f"source references checked: {ref_stats['refs']}; Location lines verified: {loc_checked}")
    print(f"mermaid blocks: {n_blocks}" + ("" if n_bad >= 0 else " (NOT validated: pass --mermaid-dir)") + (f", failed: {n_bad}" if n_bad > 0 else ""))
    print(f"symbols with a dedicated entry: {total_doc}/{total_syms}")
    for e in errors:
        print("ERROR", e)
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "markdown_files": len(files),
                    "refs_checked": ref_stats["refs"],
                    "locations_verified": loc_checked,
                    "mermaid_blocks": n_blocks,
                    "mermaid_failed": n_bad,
                    "symbols_total": total_syms,
                    "symbols_documented": total_doc,
                    "per_file": {k: list(v) for k, v in per_file.items()},
                    "missing": [[p, s.name, s.kind, s.line, s.private] for p, s in missing],
                    "errors": errors,
                },
                indent=1,
            ),
            encoding="utf-8",
        )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
