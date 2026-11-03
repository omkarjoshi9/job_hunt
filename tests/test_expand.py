from __future__ import annotations

import httpx

from jobdigest.config import load_profile
from jobdigest.expand import Board, expand, probe, promising, read_boards, write_found


def test_promising_screens_title_and_location():
    p = load_profile()
    assert promising(p, "Software Engineer, Intern", "Bengaluru, India")
    assert promising(p, "Backend Engineer", "Pune")
    assert not promising(p, "Software Engineer, Intern", "New York, USA")
    assert not promising(p, "Senior Software Engineer", "Bengaluru")
    assert not promising(p, "HR Intern - Talent Acquisition", "Bengaluru")
    assert not promising(p, "Software Engineer II", "Bengaluru")
    assert not promising(p, "Account Executive", "Mumbai")


def _client(routes: dict[str, object]) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        for needle, payload in routes.items():
            if needle in str(req.url):
                if payload is None:
                    return httpx.Response(404)
                return httpx.Response(200, json=payload)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_probe_each_ats_shape_and_dead_boards():
    p = load_profile()
    client = _client(
        {
            "greenhouse/jobs": None,
            "boards/gh1/jobs": {"jobs": [{"title": "Software Engineer, New Grad", "location": {"name": "Bangalore"}}]},
            "postings/lv1": [{"text": "Backend Engineer Intern", "categories": {"location": "Pune, India"}}],
            "job-board/ab1": {"jobs": [{"title": "ML Engineer Intern", "location": "Remote", "secondaryLocations": [{"location": "Hyderabad"}]}]},
            "boards/dead/jobs": None,
        }
    )
    assert probe(Board("G", "greenhouse", "gh1"), p, client).candidates == 1
    assert probe(Board("L", "lever", "lv1"), p, client).sample == "Backend Engineer Intern"
    assert probe(Board("A", "ashby", "ab1"), p, client).candidates == 1
    assert probe(Board("D", "greenhouse", "dead"), p, client) is None


def test_expand_keeps_only_boards_with_hits_and_roundtrips_tsv(tmp_path):
    p = load_profile()
    client = _client(
        {
            "boards/good/jobs": {"jobs": [{"title": "Software Engineer Intern", "location": {"name": "Chennai"}}]},
            "boards/us/jobs": {"jobs": [{"title": "Software Engineer Intern", "location": {"name": "Austin, TX"}}]},
        }
    )
    boards = [Board("Good", "greenhouse", "good"), Board("Us", "greenhouse", "us")]
    found = expand(boards, p, client=client, workers=2, sleep=lambda _s: None)
    assert [f.board.token for f in found] == ["good"]
    out = tmp_path / "out.tsv"
    write_found(out, found)
    assert out.read_text().splitlines()[1].startswith("Good\tgreenhouse\tgood\t1")


def test_read_boards_dedupes_and_skips_unsupported(tmp_path):
    tsv = tmp_path / "t.tsv"
    tsv.write_text(
        "company\tats\ttoken\tsources\nA\tgreenhouse\ta\tx\nA2\tgreenhouse\ta\ty\nB\tworkday\tb\tx\nC\tlever\tc\tx\n",
        encoding="utf-8",
    )
    assert [(b.ats, b.token) for b in read_boards(tsv)] == [("greenhouse", "a"), ("lever", "c")]
