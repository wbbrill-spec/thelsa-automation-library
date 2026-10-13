"""
faim_web.py — FAIM Move-File Quality Audit dashboard for the Thelsa
Automation Library.

Login-gated /faim route (reuses the library's session — no second sign-in),
matching audit_web.py / campaigns.py.

This is an EVIDENCE-COVERAGE view, not a compliance score: it reports which
evidence IS and ISN'T on record per move file (survey, delivery date,
coordinator). Full FAIM scoring waits on the Moveware document/claims endpoints.

WHERE THE FILES COME FROM
-------------------------
/faim does not read Moveware itself. It reads the file book that mw_live's
background auditor already keeps for the Move-File Audit page (/audit): the
whole list walked, hidden lost/cancelled jobs read by id, saved to disk and
refreshed every few hours. One reader, one book, so /faim and /audit agree.

WHICH FILES COUNT (Moveware Integration Guide, Part B)
------------------------------------------------------
- One row per removal: a sequel (412042A) is its own move (rule 1).
- A file is a move in the window when its key date falls in the last
  12 months, bounded at today (rules 2-3). The key date is the collect date —
  Thelsa logs it as uplift or, more often, pack; mw_live's `pack` field is
  "uplift, else pack" — and, when there is none, the DELIVERY date. Thelsa
  often handles only the import side of a move into Mexico and neither knows
  nor needs the day it was packed at origin (Bill, 13 Oct 2026).
- Neither date = not a move yet: left out and counted separately (§3).
- Lost and cancelled files (status L, C) are left out of the audit and counted
  (rule 9). The codes are config: FAIM_LOST_STATUSES.
"""
import calendar
import datetime as dt
import functools
import os
import re

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


# ── Config (client-specific values live in the environment, not in the rules) ────
def _env_codes(name, default):
    vals = [x.strip().upper() for x in os.environ.get(name, "").split(",") if x.strip()]
    return set(vals or default)


LOST_STATUSES = _env_codes("FAIM_LOST_STATUSES", ["L", "C"])
BOOKED_STATUS = "W"
WINDOW_MONTHS = int(os.environ.get("FAIM_WINDOW_MONTHS", "12"))
PASS_BAR = 80
_MAX_GAP_ROWS = 15

_EMPTY_TILES = {"moves": 0, "lost": 0, "audited": 0, "booked": 0, "pending": 0,
                "missingSurvey": 0}

# Shown only when Moveware credentials are not set on the host.
SAMPLE_METRICS = {
    "generatedNote": "No live data \u2014 Moveware credentials are not set on this host.",
    "window": "not connected",
    "passBar": PASS_BAR,
    "tiles": dict(_EMPTY_TILES),
    "byCoordinator": [],
    "byCriterion": [],
    "breaches": [],
}


def _pct(p, a):
    return round(100 * p / a) if a else 0


def _as_date(v):
    """A date from mw_live's record: a date object, or an ISO string, or None."""
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str) and len(v) >= 10:
        try:
            return dt.date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def _months_ago(day, months):
    y, m = day.year, day.month - months
    while m <= 0:
        m += 12
        y -= 1
    return dt.date(y, m, min(day.day, calendar.monthrange(y, m)[1]))


def _base_number(num):
    """Collapse a sequel to its base file number: 412042W -> 412042."""
    s = str(num or "")
    i = len(s)
    while i > 0 and s[i - 1].isalpha():
        i -= 1
    return s[:i] or s


def _status_label(code):
    if code == BOOKED_STATUS:
        return "Booked"
    return {"P": "Pending", "": "No status"}.get(code, code)


# ── The audit selection + evidence coverage ──────────────────────────────────────
def faim_snapshot(files, today=None):
    """Pick the legitimate move files out of the file book and measure evidence.

    `files` are mw_live's mapped records. Returns {"metrics": ..., "selection": ...}
    where `selection` is counts only (no names) for /faim/raw.
    """
    today = today or dt.date.today()
    start = _months_ago(today, WINDOW_MONTHS)

    moves, no_date, upcoming, older = [], [], [], 0
    by_key = {"pack": 0, "delivery": 0}
    for f in files or []:
        if not isinstance(f, dict):
            continue
        status = str(f.get("status") or "").strip().upper()
        pack = _as_date(f.get("pack"))
        delivered = _as_date(f.get("delivery"))
        # Key date: pack/collect, else delivery (an import handled at destination
        # only has no pack date; its delivery date is the one that counts).
        collect = pack or delivered
        row = {
            "number": str(f.get("job") or f.get("number") or f.get("job_id") or ""),
            "status": status,
            "collect": collect,
            # Moveware's own coordinator / move manager; a record read before that
            # field existed falls back to the name the audit page uses.
            "coord": str((f.get("mw_coordinator") if "mw_coordinator" in f
                          else f.get("coordinator")) or "").strip() or "Unassigned",
            "survey_read": "survey" in f,
            "survey": bool(_as_date(f.get("survey"))),
            "delivery": bool(delivered),
            "type": str(f.get("job_type") or "").strip().upper(),
        }
        if collect is None:
            no_date.append(row)
        elif collect > today:
            upcoming.append(row)
        elif collect < start:
            older += 1
        else:
            moves.append(row)
            by_key["pack" if pack else "delivery"] += 1

    lost = [r for r in moves if r["status"] in LOST_STATUSES]
    audited = [r for r in moves if r["status"] not in LOST_STATUSES]
    booked = [r for r in audited if r["status"] == BOOKED_STATUS]
    n = len(audited)

    # Survey dates arrive as the auditor re-reads each file; a record read before
    # that carries no survey field at all, which is "not read", not "missing".
    survey_known = [r for r in audited if r["survey_read"]]
    survey_pending = n - len(survey_known)

    by_criterion = [
        {"name": "Coordinator assigned",
         "v": _pct(sum(1 for r in audited if r["coord"] != "Unassigned"), n)},
        {"name": "Survey on file",
         "v": _pct(sum(1 for r in survey_known if r["survey"]), len(survey_known))},
        {"name": "Delivery date logged",
         "v": _pct(sum(1 for r in audited if r["delivery"]), n)},
    ]
    by_criterion.sort(key=lambda x: -x["v"])

    ctot, ccov = {}, {}
    for r in audited:
        ctot[r["coord"]] = ctot.get(r["coord"], 0) + 1
        if r["survey"] and r["delivery"]:
            ccov[r["coord"]] = ccov.get(r["coord"], 0) + 1
    by_coordinator = sorted(
        [{"name": c, "v": _pct(ccov.get(c, 0), ctot[c]), "files": ctot[c]} for c in ctot],
        key=lambda x: (-x["v"], -x["files"], x["name"]))

    gaps_rows = []
    for r in audited:
        gaps = []
        if r["survey_read"] and not r["survey"]:
            gaps.append("survey")
        if not r["delivery"]:
            gaps.append("delivery date")
        if r["coord"] == "Unassigned":
            gaps.append("coordinator")
        if gaps:
            gaps_rows.append({
                "file": r["number"], "cust": _status_label(r["status"]),
                "rule": "Missing: " + ", ".join(gaps), "coord": r["coord"],
                "days": f"{len(gaps)} gap{'s' if len(gaps) != 1 else ''}",
                "sev": "crit" if len(gaps) >= 2 else "warn",
                "_k": (-len(gaps), r["status"] != BOOKED_STATUS, -r["collect"].toordinal()),
            })
    gaps_rows.sort(key=lambda b: b["_k"])
    for b in gaps_rows:
        b.pop("_k", None)

    def _by(rows, field="status"):
        out = {}
        for r in rows:
            k = r[field] or "(blank)"
            out[k] = out.get(k, 0) + 1
        return out

    _by_status = _by

    return {
        "metrics": {
            "tiles": {
                "moves": len(moves),
                "lost": len(lost),
                "audited": n,
                "booked": len(booked),
                "pending": n - len(booked),
                "missingSurvey": sum(1 for r in survey_known if not r["survey"]),
            },
            "byCriterion": by_criterion,
            "byCoordinator": by_coordinator,
            "breaches": gaps_rows[:_MAX_GAP_ROWS],
            "gapFiles": len(gaps_rows),
        },
        "selection": {
            "window_start": start.isoformat(),
            "window_end": today.isoformat(),
            "lost_statuses": sorted(LOST_STATUSES),
            "book_records": len(files or []),
            "moves_in_window": len(moves),
            "move_files_in_window": len({_base_number(r["number"]) for r in moves}),
            "moves_by_status": _by_status(moves),
            "moves_by_key_date": by_key,
            "lost_or_cancelled": len(lost),
            "audited": n,
            "booked": len(booked),
            "no_key_date": len(no_date),
            "no_key_date_by_status": _by_status(no_date),
            # job type codes (import / export / ...) of the booked files with no date
            "booked_no_key_date_by_type": _by(
                [r for r in no_date if r["status"] == BOOKED_STATUS], "type"),
            "key_date_ahead": len(upcoming),
            "key_date_ahead_by_status": _by_status(upcoming),
            "key_date_before_window": older,
            "survey_not_read_yet": survey_pending,
        },
    }


def _book():
    """(files, progress) from the shared background auditor; starts it if needed."""
    mw_live.ensure_auditor()
    return mw_live.audited_in_window(), mw_live.audit_progress()


# ── Routes ──────────────────────────────────────────────────────────────────────
@faim_bp.route("/faim")
@_login_required
def faim():
    return DASHBOARD_HTML


@faim_bp.route("/faim/api/metrics")
@_login_required
def faim_metrics():
    viewer = session.get("user_email", "")
    if not _HAVE_MW or not mw_live.have_creds():
        return jsonify(dict(SAMPLE_METRICS, viewer=viewer))
    try:
        files, prog = _book()
    except Exception:
        files, prog = [], {}
    if not files:
        return jsonify({
            "generatedNote": "Reading the file book from Moveware \u2014 this takes a "
                             "few minutes after a restart. Reload shortly.",
            "window": "loading", "passBar": PASS_BAR, "tiles": dict(_EMPTY_TILES),
            "byCoordinator": [], "byCriterion": [], "breaches": [], "viewer": viewer,
        })

    snap = faim_snapshot(files)
    sel, m = snap["selection"], snap["metrics"]
    complete = bool(prog.get("complete"))
    no_date_booked = sel["no_key_date_by_status"].get(BOOKED_STATUS, 0)
    note = (
        "LIVE Moveware \u2014 Thelsa (port 64000), the same file book as the Move-File "
        f"Audit page. Moves dated {sel['window_start']} to {sel['window_end']} by "
        "pack/collect date or, where a file has none (an import), by delivery date "
        f"({sel['moves_by_key_date']['delivery']} files). Lost and cancelled files "
        f"({', '.join(sel['lost_statuses'])}) are left out. Not counted: "
        f"{sel['no_key_date']} files with neither date in Moveware ({no_date_booked} "
        f"of them booked) and {sel['key_date_ahead']} dated ahead."
    )
    if not complete:
        note += (" The book is still being read from Moveware "
                 f"({prog.get('audited_total', 0)} of {prog.get('total') or '?'} files so "
                 "far), so these counts can still rise.")
    if sel["survey_not_read_yet"]:
        note += (f" Survey dates are still being read for {sel['survey_not_read_yet']} "
                 "files; the survey figures cover the rest.")
    return jsonify({
        "generatedNote": note,
        "window": "live" + ("" if complete else " \u00b7 book still loading"),
        "passBar": PASS_BAR,
        "tiles": m["tiles"],
        "byCoordinator": m["byCoordinator"],
        "byCriterion": m["byCriterion"],
        "breaches": m["breaches"],
        "gapFiles": m["gapFiles"],
        "viewer": viewer,
    })


@faim_bp.route("/faim/raw")
@_login_required
def faim_raw():
    """Debug: how the audited files were picked, and the state of the file book.
    Counts, dates and status codes only \u2014 no names."""
    if not _HAVE_MW or not mw_live.have_creds():
        return jsonify({"have_creds": False})
    out = {"have_creds": True}
    try:
        files, prog = _book()
        out["book"] = prog
        out["selection"] = faim_snapshot(files)["selection"]
    except Exception as e:
        out["error"] = str(e)
    return jsonify(out)


_CHECK_PATH = re.compile(r"^/jobs/\d+(/[A-Za-z]+(/\d+)?)*$")
_DATE_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_CODE_LIKE = re.compile(r"^[A-Z0-9]{1,6}$")


def _shape(v, depth=0):
    """The shape of a Moveware payload without its contents: field names, dates,
    numbers and short upper-case codes are shown; every other text is reduced to
    its length, so no name, address, email or note leaves the server."""
    if isinstance(v, dict):
        if depth >= 6:
            return f"object({len(v)} fields)"
        return {k: _shape(x, depth + 1) for k, x in v.items()}
    if isinstance(v, list):
        return {"count": len(v), "first": [_shape(x, depth + 1) for x in v[:3]]}
    if isinstance(v, str):
        if not v:
            return ""
        if _DATE_LIKE.match(v) or _CODE_LIKE.match(v):
            return v
        return f"text({len(v)})"
    return v


@faim_bp.route("/faim/check")
@_login_required
def faim_check():
    """Debug: the shape of one Moveware job path, e.g. ?path=/jobs/110926/activities
    — field names, dates and codes only (see _shape). Read-only (GET)."""
    path = (request.args.get("path") or "").strip()
    if not _CHECK_PATH.match(path):
        return jsonify({"error": "pass ?path=/jobs/<id> or /jobs/<id>/<resource>"}), 400
    if not _HAVE_MW or not mw_live.have_creds():
        return jsonify({"have_creds": False})
    try:
        return jsonify({"path": path, "shape": _shape(mw_live._get_timed(path, 12))})
    except Exception as e:
        return jsonify({"path": path, "error": str(e)[:200]})


@faim_bp.route("/faim/rebuild", methods=["GET", "POST"])
@_login_required
def faim_rebuild():
    """Re-read the in-window files from Moveware now (background)."""
    if not _HAVE_MW or not mw_live.have_creds():
        return jsonify({"started": False, "reason": "not connected to Moveware"})
    try:
        mw_live.ensure_auditor()
        started = mw_live.force_refresh()
        return jsonify({"started": bool(started),
                        "reason": "" if started else "already running"})
    except Exception as e:
        return jsonify({"started": False, "reason": str(e)})


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
.tile .label{font-size:12px;color:var(--text-secondary);margin:0}
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
<button class="btn" onclick="rebuild()">↻ Refresh data</button>
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
<p class="note">Share of each coordinator's audited files with both survey and delivery date on record. Line = 80% target.</p>
<div class="bars" id="byCoord"></div>
</div>
<div class="card">
<h2>Evidence coverage by type</h2>
<p class="note">Share of audited files that carry each piece of evidence in Moveware. Line = 80% target.</p>
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
<p class="note">What's not on record, worst first<span id="gapCount"></span>. (Alerting + auto-drafted follow-ups are a later phase — this view only reports gaps, it does not send anything yet.)</p>
<table>
<thead><tr><th>File</th><th>Moveware status</th><th>Missing evidence</th><th>Coordinator</th><th class="num">Gaps</th><th>Severity</th></tr></thead>
<tbody id="breaches"></tbody>
</table>
</div>

<p class="foot" id="foot"></p>
</div>
<script>
function toggleTheme(){const r=document.documentElement;r.setAttribute('data-theme',r.getAttribute('data-theme')==='dark'?'light':'dark');}
async function rebuild(){const b=document.getElementById('sampleBanner');b.style.display='block';b.textContent='● Asking Moveware for the latest…';try{const r=await fetch('/faim/rebuild',{method:'POST'});const j=await r.json();b.textContent='● '+(j.started?'Re-reading the last 12 months from Moveware in the background — reload this page in a few minutes.':('Refresh not started: '+(j.reason||'unknown reason')+'.'));}catch(e){b.textContent='● Could not start the refresh.';}}
const THR=80;
function bars(el,data){
if(!data||!data.length){el.innerHTML='<p style="font-size:12px;color:var(--muted);margin:0">No files to show yet.</p>';return;}
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
<div class="tile"><p class="label">Moves in the window</p><p class="val">${t.moves}</p><p class="sub2">packed or delivered in the last 12 months, any status</p></div>
<div class="tile"><p class="label">Lost or cancelled</p><p class="val">${t.lost}</p><p class="sub2">left out of the audit</p></div>
<div class="tile"><p class="label">Files audited</p><p class="val">${t.audited}</p><p class="sub2">${t.booked} booked · ${t.pending} not marked booked</p></div>
<div class="tile"><p class="label">Missing survey</p><p class="val">${t.missingSurvey}</p><p class="sub2">of audited files</p></div>`;
bars(document.getElementById('byCoord'),d.byCoordinator||[]);
bars(document.getElementById('byCrit'),d.byCriterion||[]);
const tb=document.getElementById('breaches');
document.getElementById('gapCount').textContent=d.gapFiles?` — showing ${(d.breaches||[]).length} of ${d.gapFiles} files with a gap`:'';
const rows=(d.breaches||[]);
tb.innerHTML=rows.length?rows.map(b=>`
<tr><td class="num">${b.file}</td><td>${b.cust}</td><td>${b.rule}</td><td>${b.coord}</td>
<td class="num">${b.days}</td><td><span class="pill ${b.sev}">${b.sev==='crit'?'● Several gaps':'▲ Minor gaps'}</span></td></tr>`).join(''):'<tr><td colspan="6" style="color:var(--muted)">No gaps on the audited files.</td></tr>';
document.getElementById('foot').textContent='Evidence coverage is read from Moveware; a file counts as covered only when the evidence is actually on record. Full FAIM compliance scoring (deadlines, documents, signed inventories) resumes once the remaining Moveware endpoints and field mapping are confirmed.';
}
load();
</script>
</body>
</html>
"""
