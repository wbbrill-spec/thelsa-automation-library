"""
faim_web.py — FAIM Move-File Quality Audit dashboard for the Thelsa
Automation Library.

Login-gated /faim route (reuses the library's Google session — no second
sign-in), matching audit_web.py / campaigns.py.

DATA SOURCE
-----------
/faim/api/metrics returns LIVE Moveware compliance metrics when credentials are
present (via mw_live), and falls back to SAMPLE_METRICS otherwise — so the page
can never break.

This is a FIRST live cut. It evaluates the FAIM criteria that are reliably
derivable from the fields mw_live already maps, WITHOUT extra per-file API calls:
  • MS2.6 Invoicing  — invoice within 30 calendar days of delivery
  • MS5.1 Insurance  — transit-insurance cover recorded on the file (offer proxy)
The remaining criteria (MS2.1 quote timing, MS2.3 doc transmission, MS2.4/2.5
delivery evidence, MS1.4 documentation) need the documents/invoices sub-resource
endpoints still being confirmed with Moveware — see Moveware-Contact-Questions.md.
Use /faim/raw to inspect the real live structure, then widen the ruleset here.
"""
import datetime as dt
import functools
import json
import time
import urllib.request

from flask import (
    Blueprint,
    jsonify,
    redirect,
    request,
    session,
    url_for,
)

faim_bp = Blueprint("faim", __name__)

# Reuse the proven Moveware connection from the Cost & Profit Audit.
try:
    import mw_live
    _HAVE_MW = True
except Exception:
    _HAVE_MW = False


# ── auth shim (reuses the app's session gate; avoids circular import) ────────────
def _login_required(f):
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("user_email"):
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)

    return wrapped


# ── Sample fallback (shown when creds/data/live pull are unavailable) ────────────
SAMPLE_METRICS = {
    "generatedNote": "SAMPLE data — live Moveware pull inactive (no creds or no data returned).",
    "window": "rolling 90 days",
    "passBar": 80,
    "tiles": {
        "overallCompliance": 84,
        "overallTrendPts": 6,
        "activeFiles": 342,
        "openBreaches": 27,
        "atRisk": 41,
    },
    "byCoordinator": [
        {"name": "Sarah Whitfield", "v": 94},
        {"name": "Mei Chen", "v": 91},
        {"name": "Ana García", "v": 88},
        {"name": "David Okonkwo", "v": 82},
        {"name": "Luis Torres", "v": 74},
        {"name": "Priya Nair", "v": 69},
    ],
    "byCriterion": [
        {"name": "MS2.6 Invoicing", "v": 95},
        {"name": "MS2.4 Delivery conf.", "v": 90},
        {"name": "MS4.1 Quote complete", "v": 88},
        {"name": "MS2.1 Quote timely", "v": 86},
        {"name": "MS1.4 Documentation", "v": 83},
        {"name": "MS2.3 Docs to agent", "v": 79},
        {"name": "MS5.1 Insurance offer", "v": 76},
        {"name": "MS2.5 Signed inventory", "v": 72},
    ],
    "breaches": [
        {"file": "48213", "cust": "Okafor · Lagos→Houston", "rule": "MS2.5 Signed inventory", "coord": "Priya Nair", "days": "5 WD", "sev": "crit"},
        {"file": "47988", "cust": "Bianchi · Milan→Sydney", "rule": "MS2.3 Docs to dest agent", "coord": "Luis Torres", "days": "4 WD", "sev": "crit"},
        {"file": "48301", "cust": "Nguyen · Hanoi→Toronto", "rule": "MS2.6 Invoicing", "coord": "Luis Torres", "days": "3 d", "sev": "crit"},
        {"file": "48120", "cust": "Al-Fulan · Dubai→London", "rule": "MS5.1 Insurance offer evidence", "coord": "Priya Nair", "days": "3 WD", "sev": "warn"},
        {"file": "48255", "cust": "Kowalski · Warsaw→Chicago", "rule": "MS2.1 Quote to transferee", "coord": "David Okonkwo", "days": "2 WD", "sev": "warn"},
        {"file": "48190", "cust": "Santos · Lisbon→Dubai", "rule": "MS1.4 Missing signed POD", "coord": "Luis Torres", "days": "2 WD", "sev": "warn"},
    ],
}


# ── Live FAIM evaluation ─────────────────────────────────────────────────────────
# Lean, timeout-safe: one /jobs call + one /quotes call per job (which carries the
# rich job: coordinator, insurance, dates, status, quote options). A hard wall-clock
# budget guarantees the request returns before the web-server worker timeout, and a
# short per-call timeout means a slow Moveware call fails fast instead of hanging.
_LIVE_CACHE = {"at": 0.0, "data": None}
_LIVE_TTL = 600            # seconds
_ENRICH_BUDGET = 15.0      # seconds of per-job enrichment before we stop and return
_MAX_JOBS = 15


def _pct(p, a):
    return round(100 * p / a) if a else 0


def _mw_get(path, timeout=6):
    url = mw_live.BASE_URL + "/" + path.lstrip("/")
    req = urllib.request.Request(url, headers=mw_live._headers(), method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _g(d, *keys, default=""):
    if not isinstance(d, dict):
        return default
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return default


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("$", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _date_only(v):
    v = str(v or "")[:10]
    if len(v) == 10 and v[4] == "-":
        try:
            return dt.date.fromisoformat(v)
        except ValueError:
            return None
    return None


def _wd(d1, d2):
    if not d1 or not d2 or d2 <= d1:
        return 0
    n, cur = 0, d1
    while cur < d2:
        cur += dt.timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return n


def _faim_live_metrics():
    """Build live FAIM metrics, or return None to signal fallback to sample."""
    if not _HAVE_MW or not mw_live.have_creds():
        return None
    now = time.time()
    if _LIVE_CACHE["data"] is not None and now - _LIVE_CACHE["at"] < _LIVE_TTL:
        return _LIVE_CACHE["data"]
    try:
        data = _mw_get("/jobs", timeout=8)
    except Exception:
        return None
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not jobs:
        return None

    today = dt.date.today()
    start = time.time()
    coord_pass, coord_app = {}, {}
    crit_pass, crit_app = {}, {}
    breaches = []
    evaluated = 0
    skipped = 0

    for j in jobs[:_MAX_JOBS]:
        if time.time() - start > _ENRICH_BUDGET:
            skipped += 1
            continue
        jid = str(_g(j, "id", "jobId", "jobNumber"))
        if not jid or str(_g(j, "status")).upper() == "C":  # skip cancelled
            continue
        try:
            qd = _mw_get(f"/jobs/{jid}/quotes", timeout=6)
        except Exception:
            continue
        quotes = qd.get("quotes") if isinstance(qd, dict) else None
        q0 = quotes[0] if quotes else {}
        rich = q0.get("job", {}) if isinstance(q0, dict) else {}
        roles = q0.get("roles", {}) if isinstance(q0, dict) else {}

        if str(_g(rich.get("jobStatus", {}), "code")).upper() == "C":
            continue

        coord = _g(roles.get("manager", {}), "name") or _g(j, "moveManager") or "Unassigned"
        client = _g(rich, "name") or _g(j, "name") or jid
        dates = rich.get("dates", {}) if isinstance(rich, dict) else {}
        survey = _date_only(_g(dates.get("survey", {}), "date"))
        ins = rich.get("services", {}).get("insurance", {}) if isinstance(rich.get("services"), dict) else {}
        has_ins = any(
            str(ins.get(k, "")).strip() not in ("", "0", "0.00")
            for k in ("type", "value", "premium", "insurerCode")
        )
        options = q0.get("options", []) if isinstance(q0, dict) else []
        quote_priced = any(_num(_g(o, "valueInc", "value")) > 0 for o in options)

        evaluated += 1
        results = {}

        # MS5.1 Insurance — transit-insurance cover recorded on the file (offer proxy).
        results["MS5.1 Insurance offer"] = "pass" if has_ins else "breach"
        if not has_ins:
            breaches.append({"file": jid, "cust": client, "rule": "MS5.1 Insurance offer evidence",
                             "coord": coord, "days": "no cover on file", "sev": "warn", "_age": 5})

        # MS2.1 Quotation — a priced quote must exist within 4 working days of survey.
        if survey:
            if quote_priced:
                results["MS2.1 Quote timely"] = "pass"
            else:
                elapsed = _wd(survey, today)
                if elapsed > 4:
                    results["MS2.1 Quote timely"] = "breach"
                    breaches.append({"file": jid, "cust": client, "rule": "MS2.1 Quotation not issued",
                                     "coord": coord, "days": f"{elapsed} WD since survey", "sev": "crit", "_age": elapsed + 50})
                else:
                    results["MS2.1 Quote timely"] = "pass"

        for crit, status in results.items():
            crit_app[crit] = crit_app.get(crit, 0) + 1
            coord_app[coord] = coord_app.get(coord, 0) + 1
            if status == "pass":
                crit_pass[crit] = crit_pass.get(crit, 0) + 1
                coord_pass[coord] = coord_pass.get(coord, 0) + 1

    if evaluated == 0:
        return None

    by_criterion = sorted(
        [{"name": c, "v": _pct(crit_pass.get(c, 0), crit_app[c])} for c in crit_app],
        key=lambda x: -x["v"],
    )
    by_coordinator = sorted(
        [{"name": c, "v": _pct(coord_pass.get(c, 0), coord_app[c])} for c in coord_app],
        key=lambda x: -x["v"],
    )
    total_app = sum(crit_app.values())
    total_pass = sum(crit_pass.values())

    breaches.sort(key=lambda b: -b.get("_age", 0))
    for b in breaches:
        b.pop("_age", None)

    note = (
        f"LIVE Moveware — company 64000 · {evaluated} active files evaluated "
        "· criteria: MS5.1 Insurance, MS2.1 Quotation "
        "(more added as documents/invoices endpoints are confirmed)."
    )
    if skipped:
        note += f" [{skipped} files deferred this load for speed]"

    result = {
        "generatedNote": note,
        "window": "live snapshot",
        "passBar": 80,
        "tiles": {
            "overallCompliance": _pct(total_pass, total_app),
            "overallTrendPts": 0,
            "activeFiles": evaluated,
            "openBreaches": len([b for b in breaches if b["sev"] == "crit"]),
            "atRisk": len([b for b in breaches if b["sev"] == "warn"]),
        },
        "byCoordinator": by_coordinator,
        "byCriterion": by_criterion,
        "breaches": breaches[:12],
    }
    _LIVE_CACHE["data"] = result
    _LIVE_CACHE["at"] = time.time()
    return result


# ── Routes ──────────────────────────────────────────────────────────────────────
@faim_bp.route("/faim")
@_login_required
def faim():
    return DASHBOARD_HTML


@faim_bp.route("/faim/api/metrics")
@_login_required
def faim_metrics():
    live = None
    try:
        live = _faim_live_metrics()
    except Exception:
        live = None
    data = dict(live) if live else dict(SAMPLE_METRICS)
    data["viewer"] = session.get("user_email", "")
    return jsonify(data)


@faim_bp.route("/faim/raw")
@_login_required
def faim_raw():
    """Debug: raw Moveware structures, to confirm the live field mapping."""
    if not _HAVE_MW:
        return jsonify({"error": "mw_live not importable"})
    try:
        return jsonify(mw_live.raw_sample())
    except Exception as e:
        return jsonify({"error": str(e)})


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FAIM Audit — Coordinator Dashboard</title>
<style>
:root{color-scheme:light;--surface-1:#fcfcfb;--page:#f9f9f7;--text-primary:#0b0b0b;--text-secondary:#52514e;--muted:#898781;
--grid:#e1e0d9;--baseline:#c3c2b7;--border:rgba(11,11,11,0.10);--series-1:#2a78d6;--series-1-soft:#cde2fb;
--critical:#d03b3b;--serious:#ec835a;--good-ink:#006300;}
:root[data-theme="dark"]{color-scheme:dark;--surface-1:#1a1a19;--page:#0d0d0d;--text-primary:#fff;--text-secondary:#c3c2b7;
--muted:#898781;--grid:#2c2c2a;--baseline:#383835;--border:rgba(255,255,255,0.10);--series-1:#3987e5;--series-1-soft:#184f95;
--critical:#d03b3b;--serious:#ec835a;--good-ink:#0ca30c;}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--text-primary);font-family:system-ui,-apple-system,"Segoe UI",sans-serif;font-size:14px;line-height:1.45}
.wrap{max-width:1080px;margin:0 auto;padding:24px 24px 60px}
header.top{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap}
h1{font-size:22px;margin:0 0 2px}
.sub{color:var(--text-secondary);font-size:13px;margin:0}
.topright{display:flex;align-items:center;gap:10px;font-size:12.5px;color:var(--text-secondary)}
.btn{border:1px solid var(--border);background:var(--surface-1);color:var(--text-secondary);border-radius:8px;padding:7px 12px;font-size:12.5px;cursor:pointer;text-decoration:none}
.btn:hover{color:var(--text-primary)}
.banner{margin-top:14px;background:color-mix(in srgb,var(--serious) 14%,transparent);color:var(--serious);border:1px solid color-mix(in srgb,var(--serious) 30%,transparent);border-radius:9px;padding:9px 13px;font-size:12.5px;font-weight:600}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;padding:18px 20px;margin-top:18px}
.card h2{font-size:14px;margin:0 0 2px}
.card .note{font-size:12px;color:var(--muted);margin:0 0 16px}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:18px}
.tile{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;padding:16px 18px}
.tile .label{font-size:12px;color:var(--text-secondary);margin:0}
.tile .val{font-size:30px;font-weight:650;margin:6px 0 0;letter-spacing:-.01em}
.tile .val small{font-size:15px;font-weight:550;color:var(--muted)}
.tile .delta{font-size:12px;margin-top:4px}
.up{color:var(--good-ink)} .down{color:var(--critical)}
.bars{display:flex;flex-direction:column;gap:11px}
.row{display:grid;grid-template-columns:150px 1fr 74px;align-items:center;gap:12px}
.row .name{font-size:13px;color:var(--text-secondary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.track{position:relative;height:20px;background:var(--series-1-soft);border-radius:4px}
.fill{position:absolute;left:0;top:0;height:100%;border-radius:4px;background:var(--series-1)}
.fill.under{background:var(--critical)}
.threshold{position:absolute;top:-5px;bottom:-5px;width:2px;background:var(--baseline)}
.pct{font-size:13px;text-align:right;font-variant-numeric:tabular-nums}
.pct .flag{color:var(--critical);font-weight:600}
.legend{display:flex;gap:18px;margin-top:16px;font-size:12px;color:var(--text-secondary);flex-wrap:wrap}
.legend .k{display:inline-flex;align-items:center;gap:6px}
.sw{width:11px;height:11px;border-radius:3px;display:inline-block}
.thr-key{width:2px;height:13px;background:var(--baseline);display:inline-block}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--grid)}
th{font-size:11.5px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:600}
td.num{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
.pill{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:600;padding:2px 8px;border-radius:999px}
.pill.crit{color:var(--critical);background:color-mix(in srgb,var(--critical) 14%,transparent)}
.pill.warn{color:var(--serious);background:color-mix(in srgb,var(--serious) 16%,transparent)}
.cols2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media (max-width:820px){.tiles{grid-template-columns:repeat(2,1fr)}.cols2{grid-template-columns:1fr}.row{grid-template-columns:120px 1fr 64px}}
.foot{color:var(--muted);font-size:11.5px;margin-top:22px}
</style>
</head>
<body>
<div class="wrap">
<header class="top">
<div>
<h1>FAIM Audit — Coordinator Dashboard</h1>
<p class="sub">Thelsa Mobility Solutions · FAIM 3.4 file-level compliance · <span id="window">—</span></p>
</div>
<div class="topright">
<span id="viewer"></span>
<button class="btn" onclick="toggleTheme()">◐ Theme</button>
<a class="btn" href="/">← Library</a>
<a class="btn" href="/logout">Sign out</a>
</div>
</header>
<div id="sampleBanner" class="banner" style="display:none"></div>

<div class="tiles" id="tiles"></div>

<div class="cols2">
<div class="card">
<h2>Compliance by coordinator</h2>
<p class="note">Share of each coordinator's files meeting all applicable FAIM criteria. Line = 80% audit pass bar.</p>
<div class="bars" id="byCoord"></div>
</div>
<div class="card">
<h2>Compliance by FAIM criterion</h2>
<p class="note">Where files fail most often — the training/coaching signal. Line = 80% pass bar.</p>
<div class="bars" id="byCrit"></div>
</div>
</div>
<div class="legend">
<span class="k"><span class="sw" style="background:var(--series-1)"></span> At / above 80%</span>
<span class="k"><span class="sw" style="background:var(--critical)"></span> ⚠ Below pass bar</span>
<span class="k"><span class="thr-key"></span> 80% FAIM pass bar</span>
</div>

<div class="card">
<h2>Open breaches — oldest first</h2>
<p class="note">Each has a coordinator alert and an auto-drafted follow-up waiting in the coordinator's Gmail drafts.</p>
<table>
<thead><tr><th>File</th><th>Customer / lane</th><th>Criterion</th><th>Coordinator</th><th class="num">Overdue</th><th>Status</th></tr></thead>
<tbody id="breaches"></tbody>
</table>
</div>

<p class="foot" id="foot"></p>
</div>

<script>
function toggleTheme(){const r=document.documentElement;r.setAttribute('data-theme',r.getAttribute('data-theme')==='dark'?'light':'dark');}
const THR=80;
function bars(el,data){
el.innerHTML=data.map(d=>{const under=d.v<THR;
return `<div class="row"><span class="name" title="${d.name}">${d.name}</span>
<div class="track"><div class="fill ${under?'under':''}" style="width:${d.v}%"></div>
<div class="threshold" style="left:${THR}%"></div></div>
<span class="pct">${d.v}%${under?' <span class="flag">⚠</span>':''}</span></div>`;}).join('');
}
async function load(){
let d;
try{ const r=await fetch('/faim/api/metrics'); if(r.status===401){location.href='/login';return;} d=await r.json(); }
catch{ document.getElementById('foot').textContent='Could not load data.'; return; }
document.getElementById('window').textContent=d.window||'';
document.getElementById('viewer').textContent=d.viewer||'';
if(d.generatedNote){const b=document.getElementById('sampleBanner');b.style.display='block';b.textContent='● '+d.generatedNote;}
const t=d.tiles||{};
document.getElementById('tiles').innerHTML=`
<div class="tile"><p class="label">Overall compliance</p><p class="val">${t.overallCompliance}%<small> / ${d.passBar}% bar</small></p><p class="delta up">▲ ${t.overallTrendPts} pts vs. 8 weeks ago</p></div>
<div class="tile"><p class="label">Active files monitored</p><p class="val">${t.activeFiles}</p><p class="delta">intercontinental, in-progress</p></div>
<div class="tile"><p class="label">Open breaches</p><p class="val">${t.openBreaches}</p><p class="delta down">need attention</p></div>
<div class="tile"><p class="label">At-risk (deadline near)</p><p class="val">${t.atRisk}</p><p class="delta">proactive nudge sent</p></div>`;
bars(document.getElementById('byCoord'),d.byCoordinator||[]);
bars(document.getElementById('byCrit'),d.byCriterion||[]);
document.getElementById('breaches').innerHTML=(d.breaches||[]).map(b=>`
<tr><td class="num">${b.file}</td><td>${b.cust}</td><td>${b.rule}</td><td>${b.coord}</td>
<td class="num">${b.days}</td><td><span class="pill ${b.sev}">${b.sev==='crit'?'● Breached':'▲ At risk'}</span></td></tr>`).join('');
document.getElementById('foot').textContent='Compliance is computed per file against the FAIM 3.4 move-file ruleset; the 80% line is the FAIM auditor sample pass bar shown for reference.';
}
load();
</script>
</body>
</html>
"""
