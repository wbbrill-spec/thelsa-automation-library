"""One settings file per company: nothing Thelsa-specific leaks into another company."""
import importlib
import os
import subprocess
import sys

import tenant


def test_thelsa_is_the_default_and_keeps_its_rules():
    assert tenant.tenant_id() == "thelsa"
    assert tenant.get("moveware.company_segment") == "64000"
    assert "edgarespino@thelsa.com" in tenant.get("invoicing.embassy_to")


def test_another_company_gets_its_own_people_and_routing():
    code = ("import coordinator_alerts as ca, survey_finder as sf, faim_web as fw, mw_live as mw;"
            "print(ca.INVOICE_TO, ca.DEFAULT_CC, sorted(ca.KNOWN_COORDINATORS.values()), sf.SALES_MAILBOXES,"
            " sorted(fw.IGNORE_FILES), mw.MW_COMPANY_SEGMENT)")
    env = dict(os.environ, TENANT="example")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                         cwd=os.path.dirname(os.path.abspath(__file__)))
    assert out.returncode == 0, out.stderr[-500:]
    assert "billing@example.com" in out.stdout and "00000" in out.stdout
    assert "thelsa.com" not in out.stdout


def test_every_company_file_has_the_required_settings():
    need = ["id", "app_name", "moveware.company_segment", "people.coordinators", "people.sender_mailbox",
            "invoicing.team_to", "files.lost_statuses"]
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tenants")
    for name in os.listdir(d):
        if name.endswith(".json"):
            os.environ["TENANT_FILE"] = os.path.join(d, name)
            try:
                for k in need:
                    assert tenant.get(k) is not None, f"{name}: {k}"
            finally:
                os.environ.pop("TENANT_FILE")


def test_two_companies_never_share_a_database(tmp_path, monkeypatch):
    from commissions import store
    monkeypatch.setenv("COMMISSION_DATABASE_URL", f"sqlite:///{tmp_path}/c.db")
    store.reset_for_tests()
    try:
        tenant.claim_database()                       # thelsa claims it
        tenant.claim_database()                       # and may keep using it
        monkeypatch.setenv("TENANT", "example")
        import pytest
        with pytest.raises(RuntimeError, match="belongs to company 'thelsa'"):
            tenant.claim_database()
    finally:
        store.reset_for_tests()
