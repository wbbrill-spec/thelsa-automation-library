"""Moveware V2 endpoint swap (2026-09-14).

V2 moved the company id out of the mw-company-id header and into the URL path,
and serves LIVE from a host named `rest.moveware-test.app`. The tests below pin
the two things most likely to be "corrected" by someone later: that the live
base URL is the 64000 path on that oddly-named host, and that the client no
longer refuses to start just because MW_COMPANY_ID is absent.
"""
import importlib
import os

import pytest

import mw_live
from crossborder import tms


# ── the endpoint ─────────────────────────────────────────────────────────────


def test_live_base_url_is_the_64000_path():
    assert tms.BASE_URLS["prod"] == "https://rest.moveware-test.app/64000/api"


def test_test_base_url_is_the_08800_path():
    """Same host, different company segment — the host name is not the switch."""
    assert tms.BASE_URLS["test"] == "https://rest.moveware-test.app/08800/api"
    assert tms.BASE_URLS["prod"] != tms.BASE_URLS["test"]


def test_v1_is_still_reachable_as_a_named_rollback():
    assert tms.BASE_URLS["v1"].startswith("https://rest.moveconnect.com/")


def test_mw_live_defaults_to_the_live_v2_endpoint():
    assert mw_live.BASE_URL == "https://rest.moveware-test.app/64000/api"


def test_the_company_segment_switches_databases(monkeypatch):
    monkeypatch.setenv("MW_COMPANY_SEGMENT", "08800")
    monkeypatch.delenv("MOVEWARE_URL", raising=False)
    m = importlib.reload(mw_live)
    try:
        assert m.BASE_URL == "https://rest.moveware-test.app/08800/api"
    finally:
        monkeypatch.undo()
        importlib.reload(mw_live)


def test_an_explicit_moveware_url_still_wins(monkeypatch):
    """A full override must work without a code change — that is the rollback."""
    monkeypatch.setenv("MOVEWARE_URL", "https://rest.moveconnect.com/Moveware/v1")
    m = importlib.reload(mw_live)
    try:
        assert m.BASE_URL == "https://rest.moveconnect.com/Moveware/v1"
    finally:
        monkeypatch.undo()
        importlib.reload(mw_live)


def test_a_client_builds_the_jobs_url_under_the_company_segment():
    c = tms.MovewareClient.__new__(tms.MovewareClient)
    c.base_url = tms.BASE_URLS["prod"]
    assert f"{c.base_url}/jobs" == "https://rest.moveware-test.app/64000/api/jobs"


# ── credentials ──────────────────────────────────────────────────────────────


def test_username_and_password_alone_are_enough(monkeypatch):
    """V2 authenticates on two headers. Requiring the old company id here would
    silently keep the whole TMS pull switched off after Bill clears that var."""
    monkeypatch.setenv("MW_USERNAME", "apiuser")
    monkeypatch.setenv("MW_PASSWORD", "x")
    monkeypatch.delenv("MW_COMPANY_ID", raising=False)
    assert tms.MovewareClient.have_creds() is True
    assert mw_live.have_creds() is True


def test_a_missing_password_still_blocks(monkeypatch):
    monkeypatch.setenv("MW_USERNAME", "apiuser")
    monkeypatch.delenv("MW_PASSWORD", raising=False)
    assert tms.MovewareClient.have_creds() is False


def test_company_id_header_is_omitted_when_unset(monkeypatch):
    monkeypatch.setenv("MW_USERNAME", "apiuser")
    monkeypatch.setenv("MW_PASSWORD", "x")
    monkeypatch.delenv("MW_COMPANY_ID", raising=False)
    h = mw_live._headers()
    assert "mw-company-id" not in h
    assert h["mw-username"] == "apiuser" and h["mw-password"] == "x"


def test_company_id_header_is_sent_when_still_configured(monkeypatch):
    """Leaving the old Render var in place must not break V2, and removing it
    must not break anything either — both directions are safe."""
    monkeypatch.setenv("MW_USERNAME", "apiuser")
    monkeypatch.setenv("MW_PASSWORD", "x")
    monkeypatch.setenv("MW_COMPANY_ID", "64000")
    assert mw_live._headers()["mw-company-id"] == "64000"


def test_headers_never_leak_an_empty_company_id(monkeypatch):
    monkeypatch.setenv("MW_USERNAME", "apiuser")
    monkeypatch.setenv("MW_PASSWORD", "x")
    monkeypatch.setenv("MW_COMPANY_ID", "")
    assert "mw-company-id" not in mw_live._headers()
