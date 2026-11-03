"""Run collect -> digest end to end on fake data and print every stage's result.

Usage (from anywhere, with the package installed):  python docs/onboarding/first_trace.py
No network, no secrets: the ATS board, the LLM and Telegram are all fakes.
See docs/onboarding/04-first-trace.md for a guided walk-through.
"""
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path

import httpx
import yaml

from jobdigest import collect, digest

work = Path(tempfile.mkdtemp())
cfg = work / "config"
cfg.mkdir()
repo = Path(__file__).resolve().parents[2]  # docs/onboarding/first_trace.py -> repo root
for name in ("profile", "models"):
    (cfg / f"{name}.yaml").write_text((repo / "config" / f"{name}.example.yaml").read_text())
(cfg / "companies.yaml").write_text(yaml.safe_dump({"companies": [
    {"id": "acme", "name": "Acme", "ats": "greenhouse", "token": "acme", "enabled": True, "notes": ""}]}))
for name in ("portals", "feeds", "marketplaces"):
    (cfg / f"{name}.yaml").write_text(f"{name}: []\n")

os.environ.update(JOBDIGEST_CONFIG_DIR=str(cfg), JOBDIGEST_DISCOVERED_BOARDS="off")
os.environ.pop("DATABASE_URL", None)

def job(i, title, loc, body):
    return {"id": i, "title": title, "location": {"name": loc}, "company_name": "Acme",
            "absolute_url": f"https://boards.greenhouse.io/acme/jobs/{i}", "content": f"<p>{body}</p>",
            "updated_at": "2026-11-01T00:00:00Z"}

JOBS = [
    job(1, "Backend Software Engineer, New Grad", "Bengaluru, India",
        "Responsibilities: build Python and FastAPI services on AWS with Docker. We hire freshers."),
    job(2, "Senior Staff Engineer", "Bengaluru, India", "10+ years."),
    job(3, "HR Intern - Talent Acquisition", "Bengaluru, India", "Recruit people."),
]
client = httpx.Client(transport=httpx.MockTransport(
    lambda req: httpx.Response(200, json={"jobs": JOBS, "meta": {"total": len(JOBS)}})))

db = work / "jobs.db"
print("collect exit code:", collect.run(db_path=db, client=client, sleep=lambda s: None, ats_only=True))

conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
print("\nhard filter verdicts:")
for r in conn.execute("SELECT title, hard_status, hard_reason FROM jobs ORDER BY id"):
    print(f"  {r['title']:40} {r['hard_status']:7} {r['hard_reason']}")

def fake_llm(messages, *, model, api_key, json_mode=False, **kw):
    ids = [int(x) for x in re.findall(r"<<<JOB id=(\d+)>>>", messages[-1]["content"])]
    return json.dumps({"jobs": [{"job_id": i, "score": 80, "relevant": True, "level_ok": True,
        "location_ok": True, "stack_ok": True, "why": "Python backend, FastAPI and AWS match", "kill": None} for i in ids]})

sent = []
def fake_telegram(url, json=None, timeout=None):
    sent.append((url.rsplit("/", 1)[-1], json))
    return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

os.environ.update(TELEGRAM_BOT_TOKEN="123:fake", TELEGRAM_CHAT_ID="42")
print("\ndigest exit code:", digest.run(db_path=db, score_chat_fn=fake_llm,
      summarize_chat_fn=lambda *a, **k: "", telegram_http_post=fake_telegram))
print("\nscores table:")
for r in conn.execute("SELECT job_id, score, relevant FROM scores"):
    print(" ", dict(r))
print("\nTelegram calls made:")
for method, payload in sent:
    print(" ", method, (payload or {}).get("text", "")[:110].replace("\n", " | "))
print("\nnotified:", [dict(r) for r in conn.execute("SELECT title, notify_kind FROM jobs WHERE notified_at IS NOT NULL")])
