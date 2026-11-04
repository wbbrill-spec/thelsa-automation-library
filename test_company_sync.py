"""One intake for all apps: a copy with COMPANY_SETTINGS_URL reads its settings
from QuoteRelay's onboarding form, keeps the last good copy, and refuses
another company's settings. Without the URL (Thelsa) nothing changes."""
import types

import company_sync as cs
import tenant


def _reply(tenant_id, app, settings):
    return {"contract": "company-profile/1", "tenant": tenant_id, "app": app, "settings": settings}


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("TENANT", "acme")
    monkeypatch.setenv("COMPANY_SETTINGS_URL", "https://mattapp.example/api/company/settings?t=acme")
    monkeypatch.setenv("COMPANY_SETTINGS_TOKEN", "tok")
    monkeypatch.setenv("COMPANY_SETTINGS_DIR", str(tmp_path))
    monkeypatch.delenv("TENANT_FILE", raising=False)
    cs.clear()


def _fake_requests(monkeypatch, body=None, fail=False, seen=None):
    def get(url, headers=None, timeout=None):
        if seen is not None:
            seen.append((url, headers))
        if fail:
            raise OSError("down")
        return types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)
    import sys
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=get))


def test_reads_settings_from_the_intake(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    seen = []
    _fake_requests(monkeypatch, _reply("acme", "auditrack", {"id": "acme", "name": "Acme"}), seen=seen)
    assert tenant.get("name") == "Acme"
    assert seen[0][0].endswith("t=acme&app=auditrack") and seen[0][1]["Authorization"] == "Bearer tok"


def test_keeps_the_last_good_copy_when_quoterelay_is_down(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    _fake_requests(monkeypatch, _reply("acme", "assistant", {"tenant": {"name": "Acme"}}))
    assert cs.get("assistant")["tenant"]["name"] == "Acme"
    cs.clear()                                  # e.g. after a restart
    _fake_requests(monkeypatch, fail=True)
    assert cs.get("assistant")["tenant"]["name"] == "Acme"    # from disk


def test_refuses_another_companys_settings(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    _fake_requests(monkeypatch, _reply("thelsa", "auditrack", {"id": "thelsa"}))
    assert cs.get("auditrack") is None


def test_no_url_means_the_repo_file(monkeypatch):
    monkeypatch.delenv("COMPANY_SETTINGS_URL", raising=False)
    monkeypatch.delenv("TENANT", raising=False)
    cs.clear()
    assert cs.get("auditrack") is None
    assert tenant.get("moveware.company_segment") == "64000"
