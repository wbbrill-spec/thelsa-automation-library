"""End-to-end as a second company (4 Nov 2026): no page, email or job of company B
may show company A's names, addresses, files or rules."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

SCRIPT = r'''
import os, re, sys, json, logging, datetime as dt
logging.disable(logging.CRITICAL)
import app as A
import coordinator_alerts as ca, date_gap_alerts as dga, faim_web as fw, mw_live, survey_finder as sf
import invoice_mail, engine.config as ec, ms_graph, milestone_apply, health
leak = re.compile(r"thelsa|\bTMS\b|Embajada|US Embassy|Lupita|Sara Reyes|Edgar|Plan de Viajes|CONTPAQ|64000|MG2", re.I)
found = []

def check(where, text):
    for m in leak.finditer(str(text)):
        found.append(f"{where}: {m.group(0)}")

# every page
c = A.app.test_client()
with c.session_transaction() as s:
    s["user_email"] = "jane@example.com"; s["user_name"] = "Jane"
for rule in A.app.url_map.iter_rules():
    if "GET" in rule.methods and not rule.arguments and not rule.rule.startswith("/static"):
        try:
            check(rule.rule, c.get(rule.rule).get_data(as_text=True))
        except Exception as e:
            found.append(f"{rule.rule}: crashed {type(e).__name__}")
# the invoice alerts
wl = [{"job": "200001", "client": "Smith", "coordinator": "Jane Doe", "value": 5000.0, "currency": "USD",
       "pack": "2026-10-01", "embassy": False}]
for a in ca.build_invoice_alerts(wl):
    check("invoice alert", json.dumps(a))
# the date alerts
gaps = [{"file": "200002", "status": "W", "type": "EXP", "coord": "Jane Doe", "coord_email": "jane@example.com",
         "missing": ["pack"], "sit_pack": "", "sit_delivery": "", "key_date": "2026-10-01"}]
check("date alert", json.dumps(dga.build(gaps), default=str))
# the FAIM audit on this company's files
files = [{"job": "200003", "status": "W", "job_type": "EXP", "pack": "2026-10-10", "coordinator": "Jane Doe",
          "client": "US Embassy Mexico"}]
check("faim", json.dumps(fw.faim_snapshot(files, today=dt.date(2026, 11, 4), email=None), default=str))
# who the app would write to and read from
check("settings", json.dumps({"sender": ec.MAILBOX, "alert": ec.ALERT_EMAIL, "allowed": ec.ALLOWED_MAILBOXES,
      "mailboxes": sf.MAILBOXES, "sales": sf.SALES_MAILBOXES, "coords": ms_graph.TMS_COORDINATORS,
      "segment": mw_live.MW_COMPANY_SEGMENT, "sign": milestone_apply.SIGN_AS_EMAIL, "cc": ca.DEFAULT_CC,
      "ignore": sorted(fw.IGNORE_FILES), "domains": sorted(A.ALLOWED_DOMAINS), "health_to": health.TO,
      "invoice_report": invoice_mail.SUBJECT_WORDS, "embassy": mw_live._is_embassy("US Embassy Mexico")}))
# Thelsa-only tools are not mounted
for path in ("/crossborder", "/tim-exec", "/commissions", "/campaigns"):
    if c.get(path).status_code == 200:
        found.append(f"{path}: Thelsa-only tool is open")
print(json.dumps(found))
'''


def test_company_b_shows_nothing_of_company_a():
    env = dict(os.environ, TENANT="example", FAIM_KEEPER="0", HEALTH_WATCH="0")
    env.pop("PYTEST_CURRENT_TEST", None)
    p = subprocess.run([sys.executable, "-c", SCRIPT], env=env, cwd=HERE, capture_output=True, text=True,
                       timeout=600)
    assert p.returncode == 0, p.stderr[-1500:]
    import json
    found = json.loads(p.stdout.strip().splitlines()[-1])
    assert found == [], "\n".join(found[:40])
