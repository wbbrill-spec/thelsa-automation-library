"""/faim reads the persisted mw_ingest build; it never calls Moveware in a request."""
import json

import pytest
from flask import Flask

import faim_web
import mw_ingest


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(mw_ingest, "_BUILD_FILE", str(tmp_path / "faim_build.json"))
    started = []
    monkeypatch.setattr(mw_ingest, "trigger_build",
                        lambda: started.append(1) or {"started": True})
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(faim_web.faim_bp)
    app.add_url_rule("/login", "login", lambda: "login")
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_email"] = "someone@thelsa.com"
    c.started = started
    c.build_file = tmp_path / "faim_build.json"
    return c


def test_metrics_need_a_signed_in_user(client):
    with client.session_transaction() as s:
        s.clear()
    assert client.get("/faim/api/metrics").status_code == 302


def test_no_build_yet_starts_one_and_says_so(client, monkeypatch):
    monkeypatch.setattr(faim_web.mw_live, "have_creds", lambda: True)
    data = client.get("/faim/api/metrics").get_json()
    assert client.started == [1]
    assert "Building the live view" in data["generatedNote"]
    assert data["tiles"] == {"removals": 0, "active": 0, "missingSurvey": 0, "unassigned": 0}


def test_no_build_and_no_credentials_starts_nothing(client, monkeypatch):
    monkeypatch.setattr(faim_web.mw_live, "have_creds", lambda: False)
    client.get("/faim/api/metrics")
    assert client.started == []


def test_metrics_come_from_the_saved_build(client):
    removals = [
        {"number": "500001", "base": "500001", "lost": False, "coord": "Ana Sanchez",
         "survey": "2026-09-01", "delivery": "2026-10-01", "uplift": "2026-09-10"},
        {"number": "500002", "base": "500002", "lost": False, "coord": "Unassigned",
         "survey": "", "delivery": "", "uplift": ""},
        {"number": "500003", "base": "500003", "lost": True, "coord": "Ana Sanchez",
         "survey": "", "delivery": "", "uplift": ""},
    ]
    client.build_file.write_text(json.dumps({
        "generatedAt": "2026-10-12T23:00:00Z",
        "diagnostics": {"removals_in_window": 3, "files_in_window": 3,
                        "complete": True, "coverage_note": "note"},
        "metrics": mw_ingest._faim_metrics(removals),
    }))
    data = client.get("/faim/api/metrics").get_json()
    assert client.started == []
    assert data["window"] == "live build"
    assert data["tiles"] == {"removals": 3, "active": 2, "missingSurvey": 1, "unassigned": 1}
    assert "3 removals in the window" in data["generatedNote"]
    assert [b["file"] for b in data["breaches"]] == ["500002"]


def test_partial_build_is_labelled_partial(client):
    client.build_file.write_text(json.dumps({
        "generatedAt": "2026-10-12T23:00:00Z",
        "diagnostics": {"complete": False},
        "metrics": mw_ingest._faim_metrics([]),
    }))
    assert client.get("/faim/api/metrics").get_json()["window"] == "live build · partial"


def test_raw_reports_builder_status_and_rebuild_starts_a_build(client):
    raw = client.get("/faim/raw").get_json()
    assert raw["have_ingest"] is True and raw["status"]["has_build"] is False
    assert client.post("/faim/rebuild").get_json() == {"started": True}
    assert client.started == [1]
