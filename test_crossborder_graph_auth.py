"""The file readers must get their token from the app that holds Files.Read.All.

Regression guard for 2026-09-21: the readers borrowed ms_graph's token, which
prefers MS_* (the web sign-in app) whenever MS_CLIENT_SECRET is set, so a change
to the sign-in app silently blanked Remisiones and Plan de Viajes.
"""
from __future__ import annotations

import pytest

from crossborder import graph_auth


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    graph_auth._cache.clear()
    for k in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET",
              "MS_TENANT_ID", "MS_CLIENT_ID", "MS_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)


def _env(monkeypatch, prefix, client):
    monkeypatch.setenv(f"{prefix}TENANT_ID", "tenant")
    monkeypatch.setenv(f"{prefix}CLIENT_ID", client)
    monkeypatch.setenv(f"{prefix}CLIENT_SECRET", "s3cret-" + client)


def test_graph_creds_win_even_when_ms_creds_are_set(monkeypatch):
    _env(monkeypatch, "GRAPH_", "files-app")
    _env(monkeypatch, "MS_", "signin-app")
    used = []

    def post(url, data, timeout):
        used.append(data["client_id"])
        return _Resp(200, {"access_token": "tok", "expires_in": 3600})

    monkeypatch.setattr("requests.post", post)
    token, info = graph_auth.get_token()
    assert token == "tok" and info == {"creds": "GRAPH_*", "app": "files-app"}
    assert used == ["files-app"]


def test_falls_back_to_ms_and_says_why(monkeypatch):
    _env(monkeypatch, "GRAPH_", "files-app")
    _env(monkeypatch, "MS_", "signin-app")

    def post(url, data, timeout):
        if data["client_id"] == "files-app":
            return _Resp(401, {"error": "invalid_client",
                               "error_description": "AADSTS7000222: The provided client secret keys are expired.\r\nTrace ID: x"})
        return _Resp(200, {"access_token": "tok2", "expires_in": 3600})

    monkeypatch.setattr("requests.post", post)
    token, info = graph_auth.get_token()
    assert token == "tok2" and info["creds"] == "MS_*"
    assert "AADSTS7000222" in info["fell_back_after"][0]["error"]


def test_failure_reports_azure_code_and_never_the_secret(monkeypatch):
    _env(monkeypatch, "GRAPH_", "files-app")
    monkeypatch.setattr("requests.post", lambda url, data, timeout: _Resp(
        401, {"error": "invalid_client", "error_description": "AADSTS7000215: Invalid client secret provided."}))
    token, info = graph_auth.get_token()
    assert token is None
    assert info["attempts"][0]["app"] == "files-app"
    assert "AADSTS7000215" in info["attempts"][0]["error"]
    assert "s3cret" not in repr(info)


def test_incomplete_triplets_are_never_mixed(monkeypatch):
    monkeypatch.setenv("GRAPH_CLIENT_ID", "files-app")          # no secret, no tenant
    _env(monkeypatch, "MS_", "signin-app")
    used = []
    monkeypatch.setattr("requests.post", lambda url, data, timeout: (
        used.append((data["client_id"], data["client_secret"])) or _Resp(200, {"access_token": "t"})))
    graph_auth.get_token()
    assert used == [("signin-app", "s3cret-signin-app")]


def test_no_creds_at_all(monkeypatch):
    token, info = graph_auth.get_token()
    assert token is None and "GRAPH_CLIENT_SECRET" in info["error"]
