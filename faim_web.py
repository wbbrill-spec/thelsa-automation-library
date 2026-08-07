"""
faim_web.py — FAIM Move-File Quality Audit dashboard for the Thelsa
Automation Library.

Login-gated /faim route (reuses the library's Google session — no second
sign-in), matching audit_web.py / campaigns.py.

This build is an EVIDENCE-COVERAGE view, not a compliance score. On live data,
most FAIM criteria depend on Moveware fields/sub-resources that are still being
confirmed (documents, invoices, signed inventories) — so instead of scoring
compliance we honestly report what evidence IS vs ISN'T on record per file:
survey, insurance, priced quote, delivery date. Full compliance scoring resumes
once the field mapping + document/invoice endpoints are confirmed with Moveware.

DATA ACCESS
-----------
/faim/api/metrics pages through the ENTIRE /jobs feed (following Moveware's
`_links` pagination) to count all files for company 64000, then deep-checks a
time-bounded sample via /quotes for the evidence fields. /faim/raw exposes the
raw structures + total count + pagination links for validation.
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

try:
    import mw_live
    _HAVE_MW = True
except Exception:
    _HAVE_MW = False


def _login_required(f):
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("user_email"):
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)

    return wrapped


# ── Sample fallback (shown only when creds/data are unavailable) ─────────────────
SAMPLE_METRICS = {
    "generatedNote": "SAMPLE data — live Moveware pull inactive (no creds or no data returned).",
    "window": "sample",
    "passBar": 80,
    "tiles": {"totalFiles": 0, "activeFiles": 0, "sampleFiles": 0, "missingInsurance": 0},
    "byCoordinator": [],
    "byCriterion": [
        {"name": "Survey on file", "v": 0},
        {"name": "Delivery logged", "v": 0},
        {"name": "Insurance recorded", "v": 0},
        {"name": "Priced quote", "v": 0},
    ],
    "breaches": [],
}


# ── Config ──────────────────────────────────────────────────────────────────────
_LIVE_CACHE = {"at": 0.0, "data": None}
_LIVE_TTL = 600            # seconds
_PAGE_BUDGET = 12.0        # seconds spent paging the light /jobs feed
_ENRICH_BUDGET = 14.0      # seconds spent deep-checking sampled files
_MAX_PAGES = 60
_SAMPLE_N = 20             # deep-checked files per load (kept small for speed)


def _pct(p, a):
    return round(100 * p / a) if a else 0


def _mw_get(path_or_url, timeout=6):
    """GET a Moveware path OR a full _links URL."""
    if str(path_or_url).startswith("http"):
        url = path_or_url
    else:
        url = mw_live.BASE_URL + "/" + str(path_or_url).lstrip("/")
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


def _has(v):
    return bool(str(v or "").strip()) and str(v).strip() not in ("0", "0.00")


def _next_link(payload):
    """Return the pagination 'next' href from a Moveware _links block, if any."""
    if not isinstance(payload, dict):
        return None
    links = payload.get("_links") or {}
    candidates = [links.get("next")]
    pages = links.get("pages")
    if isinstance(pages, dict):
        candidates.append(pages.get("next"))
    for c in candidates:
        if isinstance(c, dict) and c.get("href"):
            return c["href"]
        if isinstance(c, str) and c.startswith("http"):
            return c
    return None


def _fetch_all_jobs():
    """Page through the whole /jobs feed. Returns (jobs, pages_fetched)."""
    start = time.time()
    payload = _mw_get("/jobs", timeout=10)
    jobs = list(payload.get("jobs") or []) if isinstance(payload, dict) else []
    pages = 1
    while pages < _MAX_PAGES and time.time() - start < _PAGE_BUDGET:
        nxt = _next_link(payload)
        if not nxt:
            break
        try:
            payload = _mw_get(nxt, timeout=8)
        except Exception:
            break
        jobs.extend(payload.get("jobs") or [] if isinstance(payload, dict) else [])
        pages += 1
    return jobs, pages


def _faim_live_metrics():
    """Build a live evidence-coverage snapshot, or None to fall back to sample."""
    if not _HAVE_MW or not mw_live.have_creds():
        return None
    now = time.time()
    if _LIVE_CACHE["data"] is not None and now - _LIVE_CACHE["at"] < _LIVE_TTL:
        return _LIVE_CACHE["data"]

    try:
        all_jobs, pages = _fetch_all_jobs()
    except Exception:
        return None
    if not all_jobs:
        return None

    total = len(all_jobs)
    active = [j for j in all_jobs if str(_g(j, "status")).upper() != "C"]

    # Deep-check a time-bounded sample of active files for evidence fields.
    start = time.time()
    sample = []
    for j in active:
        if len(sample) >= _SAMPLE_N or time.time() - start > _ENRICH_BUDGET:
            break
        jid = str(_g(j, "id", "jobId", "jobNumber"))
        if not jid:
            continue
        try:
            qd = _mw_get(f"/jobs/{jid}/quotes", timeout=6)
        except Exception:
            continue
        quotes = qd.get("quotes") if isinstance(qd, dict) else None
        q0 = quotes[0] if quotes else {}
        rich = q0.get("job", {}) if isinstance(q0, dict) else {}
        roles = q0.get("roles", {}) if isinstance(q0, dict) else {}
        dates = rich.get("dates", {}) if isinstance(rich, dict) else {}
        ins = rich.get("services", {}).get("insurance", {}) if isinstance(rich.get("services"), dict) else {}
        opts = q0.get("options", []) if isinstance(q0, dict) else []
        sample.append({
            "file": jid,
            "cust": _g(rich, "name") or _g(j, "name") or jid,
            "coord": _g(roles.get("manager", {}), "name") or _g(j, "moveManager") or "Unassigned",
            "survey": _has(_g(dates.get("survey", {}), "date")) or _has(_g(j, "survey")),
            "delivery": _has(_g(dates.get("delivery", {}), "date")) or _has(_g(j, "delivery")),
            "insurance": any(_has(ins.get(k)) for k in ("type", "value", "premium", "insurerCode")),
            "quote": any(_num(_g(o, "valueInc", "value")) > 0 for o in opts),
        })

    s = len(sample)
    cov = {
        "Survey on file": sum(1 for x in sample if x["survey"]),
        "Delivery logged": sum(1 for x in sample if x["delivery"]),
        "Insurance recorded": sum(1 for x in sample if x["insurance"]),
        "Priced quote": sum(1 for x in sample if x["quote"]),
    }
    by_criterion = sorted(
        [{"name": k, "v": _pct(v, s)} for k, v in cov.items()],
        key=lambda x: -x["v"],
    )

    coord_tot, coord_cov = {}, {}
    for x in sample:
        c = x["coord"]
        coord_tot[c] = coord_tot.get(c, 0) + 1
        if x["survey"] and x["insurance"] and x["quote"]:
            coord_cov[c] = coord_cov.get(c, 0) + 1
    by_coordinator = sorted(
        [{"name": c, "v": _pct(coord_cov.get(c, 0), coord_tot[c])} for c in coord_tot],
        key=lambda x: -x["v"],
    )

    missing = []
    for x in sample:
        gaps = []
        if not x["survey"]:
            gaps.append("survey")
        if not x["insurance"]:
            gaps.append("insurance")
        if not x["quote"]:
            gaps.append("priced quote")
        if not x["delivery"]:
            gaps.append("delivery date")
        if gaps:
            missing.append({
                "file": x["file"], "cust": x["cust"],
                "rule": "Missing: " + ", ".join(gaps), "coord": x["coord"],
                "days": f"{len(gaps)} gap{'s' if len(gaps) != 1 else ''}",
                "sev": "crit" if len(gaps) >= 3 else "warn", "_age": len(gaps),
            })
    missing.sort(key=lambda m: -m["_age"])
    for m in missing:
        m.pop("_age", None)

    result = {
        "generatedNote": (
            f"LIVE Moveware — company 64000 · {total} files found across {pages} page(s) "
            f"· evidence deep-checked on {s} active files this load. This view reports "
            "what's on record vs. missing; full compliance scoring resumes once the "
            "document/invoice endpoints + field mapping are confirmed."
        ),
        "window": "live coverage snapshot",
        "passBar": 80,
        "tiles": {
            "totalFiles": total,
            "activeFiles": len(active),
            "sampleFiles": s,
            "missingInsurance": sum(1 for x in sample if not x["insurance"]),
        },
        "byCoordinator": by_coordinator,
        "byCriterion": by_criterion,
        "breaches": missing[:12],
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
    """Debug: total file count, pagination links, and raw structures."""
    if not _HAVE_MW:
        return jsonify({"error": "mw_live not importable"})
    out = {}
    try:
        first = _mw_get("/jobs", timeout=10)
        out["jobs_links"] = first.get("_links") if isinstance(first, dict) else None
        out["first_page_count"] = len(first.get("jobs") or []) if isinstance(first, dict) else 0
        out["next_link"] = _next_link(first)
        jobs, pages = _fetch_all_jobs()
        out["total_files"] = len(jobs)
        out["pages_fetched"] = pages
    except Exception as e:
        out["jobs_error"] = str(e)
    try:
        out["mw_sample"] = mw_live.raw_sample()
    except Exception as e:
        out["mw_sample_error"] = str(e)
    return jsonify(out)


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
.banner{margin-top:14px;background:color-mix(in srgb,var(--series-1) 12%,transparent);color:var(--series-1);border:1px solid color-mix(in srgb,var(--series-1) 30%,transparent);border-radius:9px;padding:9px 13px;font-size:12.5px;font-weight:600}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;padding:18px 20px;margin-top:18px}
.card h2{font-size:14px;margin:0 0 2px}
.card .note{font-size:12px;color:var(--muted);margin:0 0 16px}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:18px}
.tile{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;padding:16px 18px}
.tile .label{font-size:12px;color:var--text-secondary);margin:0}
.tile .val{font-size:30px;font-weight:650;margin:6px 0 0;letter-spacing:-.01em}
.tile .sub2{font-size:12px;color:var(--muted);margin-top:4px}
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
<p class="sub">Thelsa Mobility Solutions · FAIM 3.4 move-file evidence coverage · <span id="window">—</span></p>
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
<h2>Coverage by coordinator</h2>
<p class="note">Share of each coordinator's checked files that have survey + insurance + priced quote all on record. Line = 80% target.</p>
<div class="bars" id="byCoord"></div>
</div>
<div class="card">
<h2>Evidence coverage by type</h2>
<p class="note">Share of checked files that carry each piece of evidence in Moveware. Line = 80% target.</p>
<div class="bars" id="byCrit"></div>
</div>
</div>
<div class="legend">
<span class="k"><span class="sw" style="background:var(--series-1)"></span> At / above 80%</span>
<span class="k"><span class="sw" style="background:var(--critical)"></span> ⚠ Below target</span>
<span class="k"><span class="thr-key"></span> 80% target</span>
</div>

<div class="card">
<h2>Files missing evidence</h2>
<p class="note">What's not on record for each checked file. (Alerting + auto-drafted follow-ups are a later phase — this view only reports gaps, it does not send anything yet.)</p>
<table>
<thead><tr><th>File</th><th>Customer</th><th>Missing evidence</th><th>Coordinator</th><th class="num">Gaps</th><th>Status</th></tr></thead>
<tbody id="breaches"></tbody>
</table>
</div>

<p class="foot" id="foot"></p>
</div>
<script>
function toggleTheme(){const r=document.documentElement;r.setAttribute('data-theme',r.getAttribute('data-theme')==='dark'?'light':'dark');}
const THR=80;
function bars(el,data){
if(!data||!data.length){el.innerHTML='<p style="font-size:12px;color:var(--muted);margin:0">No files checked this load.</p>';return;}
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
<div class="tile"><p class="label">Live files in Moveware</p><p class="val">${t.totalFiles}</p><p class="sub2">company 64000, all pages</p></div>
<div class="tile"><p class="label">Active (not cancelled)</p><p class="val">${t.activeFiles}</p><p class="sub2">eligible for audit</p></div>
<div class="tile"><p class="label">Deep-checked this load</p><p class="val">${t.sampleFiles}</p><p class="sub2">sampled for evidence</p></div>
<div class="tile"><p class="label">Missing insurance</p><p class="val">${t.missingInsurance}</p><p class="sub2">of the checked sample</p></div>`;
bars(document.getElementById('byCoord'),d.byCoordinator||[]);
bars(document.getElementById('byCrit'),d.byCriterion||[]);
const tb=document.getElementById('breaches');
const rows=(d.breaches||[]);
tb.innerHTML=rows.length?rows.map(b=>`
<tr><td class="num">${b.file}</td><td>${b.cust}</td><td>${b.rule}</td><td>${b.coord}</td>
<td class="num">${b.days}</td><td><span class="pill ${b.sev}">${b.sev==='crit'?'● Several gaps':'▲ Minor gaps'}</span></td></tr>`).join(''):'<tr><td colspan="6" style="color:var(--muted)">No gaps in the checked sample.</td></tr>';
document.getElementById('foot').textContent='Evidence coverage is read live from Moveware; a file counts as covered only when the evidence is actually on record. Full FAIM compliance scoring (deadlines, documents, signed inventories) resumes once the remaining Moveware endpoints and field mapping are confirmed.';
}
load();
</script>
</body>
</html>
"""
