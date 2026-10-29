"""Moveware schema reader: hidden from everyone but the admins; reports shapes, never customer data."""

import os

os.environ.setdefault("ASSISTANT_SCHEDULER", "0")
os.environ.setdefault("ENABLE_SCHEDULER", "0")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

import pytest

import mw_schema


@pytest.fixture
def client():
    import app as appmod
    appmod.app.config["TESTING"] = True
    with appmod.app.test_client() as c:
        yield c


def login(c, email):
    with c.session_transaction() as s:
        s.clear()
        s["user_email"] = email


def test_not_signed_in_gets_404(client):
    assert client.get("/admin/mw-schema").status_code == 404


def test_library_user_gets_404(client):
    login(client, "someone@thelsa.com")
    assert client.get("/admin/mw-schema?result=1").status_code == 404


def test_admin_can_collect(client, tmp_path, monkeypatch):
    monkeypatch.setattr(mw_schema, "RESULT_PATH", str(tmp_path / "r.json"))
    login(client, "bbrill@thelsa.com")
    r = client.get("/admin/mw-schema?result=1")
    assert r.status_code == 200


def test_no_tile_on_home_page():
    html = open("index.html", encoding="utf-8").read()
    assert "mw-schema" not in html


def test_shape_keeps_codes_not_personal_data():
    sh = mw_schema.Shape()
    sh.add({"status": "W", "type": "IMA", "name": "Jane Doe",
            "addresses": {"origin": {"street": "1 Main St", "city": "Monterrey"}},
            "contact": {"email": "jane@example.com"}, "jobValue": 1234.5})
    rep = sh.report()
    paths = {f["path"] for f in rep["fields"]}
    assert "addresses.origin.street" in paths          # field name reported
    blob = str(rep)
    for secret in ("Jane Doe", "1 Main St", "Monterrey", "jane@example.com", "1234.5"):
        assert secret not in blob                       # values never reported
    assert rep["code_values"]["status"] == {"W": 1}
    assert rep["code_values"]["type"] == {"IMA": 1}


def test_run_read_against_fake_moveware(monkeypatch):
    import urllib.error
    jobs = [{"id": 1, "type": "IMA", "status": "W", "name": "X"},
            {"id": 2, "type": "DIN", "status": "L", "name": "Y"}]

    def fake(path, timeout=10):
        if path.startswith("/jobs?"):
            return {"data": jobs} if "page=1&" in path else {"data": []}
        if path in ("/jobs/1", "/jobs/2"):
            return {"id": 1, "status": "W", "activityDates": {"uplift": {"date": "2026-06-13"}}}
        if path.endswith("/options"):
            return {"options": [{"id": 9}]}
        if path.endswith("/options/9/charges"):
            return {"charges": [{"rate": 10.0, "group": "TRK"}]}
        if path.endswith("/roles"):
            return {"roles": [{"type": "moveManager", "entity": {"code": "AB1"}}]}
        if path == "/codes":
            return {"codes": [{"code": "W", "description": "Won"}]}
        raise urllib.error.HTTPError(path, 404, "nf", {}, None)

    monkeypatch.setattr(mw_schema.mw_live, "_get_timed", fake)
    monkeypatch.setattr(mw_schema.mw_live, "_page_jobs", lambda p: p.get("data", []))
    out = mw_schema.run_read(pages=2)
    assert out["jobs_sampled"] == 2
    assert out["availability"]["GET /jobs/{id}/diary"] == {"404": 2}
    assert "GET /jobs/{id}/options/{optId}/charges" in out["schemas"]
    assert out["reference"]["/codes"]["codes"][0]["code"] == "W"
