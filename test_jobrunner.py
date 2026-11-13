"""Heavy jobs run in their own capped process (4 Nov 2026)."""
import datetime as dt
import json
import subprocess
import sys

import jobrunner


def test_payload_round_trip_keeps_dates():
    s = json.dumps({"args": [[{"job": "1", "pack": dt.date(2026, 10, 1)}], {"1": "X"}]}, default=jobrunner._encode)
    back = json.loads(s, object_hook=jobrunner._decode)
    assert back["args"][0][0]["pack"] == dt.date(2026, 10, 1) and back["args"][1] == {"1": "X"}


def test_a_runaway_job_is_stopped_by_its_memory_cap(monkeypatch):
    monkeypatch.setenv("JOB_MEM_LIMIT_MB", "300")
    p = subprocess.run([sys.executable, "-c", "x = bytearray(1024 ** 3)"], preexec_fn=jobrunner._limit,
                       capture_output=True, text=True)
    assert p.returncode != 0 and "MemoryError" in p.stderr


def test_every_heavy_job_is_known():
    assert set(jobrunner.JOBS) == {"survey_finder", "doc_check", "invoice_mail", "mw_costs", "measures"}
