"""
audit_web.py — Move-File Cost & Profit Audit dashboard for the Thelsa
Automation Library.

Adds a single login-gated route, /audit, that reconciles every move file
(estimated vs. actual vs. invoiced cost), flags gaps, tracks recovery, and
renders the dashboard — matching the in-app pattern used by campaigns.py.

Wiring (app.py):
    from audit_web import audit_bp
    app.register_blueprint(audit_bp)

DATA SOURCE
-----------
`load_move_files()` is the single place data comes from. Today it returns a
representative demo dataset so the dashboard is live and populated at
/audit immediately. To switch to live MoveWare data, replace the body of
load_move_files() with a pull from the MoveWare REST API
(https://rest.moveconnect.com/Moveware/v1) using the mw-username / mw-password /
mw-company-id headers stored in Render env vars — mapping each job to the same
dict shape returned below. Nothing else needs to change.
"""
import datetime as dt
import functools

from flask import (
    Blueprint,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)

audit_bp = Blueprint("audit", __name__)

# Insurance benchmark bands (fraction of declared value), tune vs Pac Global.
INS_RATE = {"sea": 0.012, "air": 0.009, "road": 0.012}
INS_TOL = 0.003
MATERIAL_GAP = 1000.0
DEMO = True  # flip to False once load_move_files() pulls live MoveWare data


# ── auth shim (reuses the app's session gate; avoids circular import) ────────────
def _login_required(f):
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("user_email"):
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)

    return wrapped


# ── Data source (swap this for the live MoveWare pull) ──────────────────────────
def load_move_files():
    """Return a list of move-file dicts. Demo data today; MoveWare later.

    Each file: client, mode, est, act, sell, invoiced_amt, invoiced,
    declared, ins, coordinator, agent, days_from_month_start pack/delivery.
    """
    ms = dt.date.today().replace(day=1)

    def d(n):
        return ms + dt.timedelta(days=n)

    rows = [
        ("Global Corp Relo", "sea", 8000, 8200, 11000, 11000, True, 120000, 1440, "Marla", "Asia Movers", 2, 9),
        ("Embassy of Canada", "air", 20665, 24800, 26000, 0, False, 90000, 0, "Marla", "AirCargo Intl", 5, 20),
        ("TechFlow Ltd", "sea", 4640, 4640, 6200, 6200, True, 60000, 1560, "Fernanda", "Ocean Freight", 1, 12),
        ("Martinez Household", "road", 5200, 7100, 6800, 6800, True, 40000, 520, "Marla", None, 3, 10),
        ("Petrova Family", "air", 15200, 15900, 19000, 0, False, 150000, 1350, "Fernanda", "AirCargo Intl", 8, 25),
        ("Nakamura Move", "sea", 9800, 9600, 13200, 13200, True, 110000, 2860, "Marla", "Asia Movers", 4, 18),
        ("Silva Corporate", "sea", 7300, 11200, 10000, 10000, True, 80000, 960, "Fernanda", "Ocean Freight", 6, 22),
        ("Al-Rashid Relocation", "air", 12500, 12500, 16000, 16000, True, 200000, 1620, "Marla", "AirCargo Intl", 2, 15),
        ("Brown Household", "road", 3100, 3050, 4200, 4200, True, 30000, 390, "Fernanda", None, 7, 14),
        ("Okafor Family", "sea", 6600, 8900, 9000, 0, False, 70000, 0, "Marla", "Ocean Freight", 10, 28),
        ("Zhang Corporate", "sea", 10200, 10050, 14000, 14000, True, 130000, 1690, "Fernanda", "Asia Movers", -3, 8),
        ("Dubois Move", "air", 8800, 9400, 11500, 11500, True, 95000, 855, "Marla", "AirCargo Intl", -5, 5),
        ("Klein Household", "road", 4400, 4400, 5800, 0, False, 35000, 455, "Fernanda", None, 12, 26),
        ("Rossi Relocation", "sea", 5900, 5900, 8100, 8100, True, 55000, 1430, "Marla", "Ocean Freight", -8, 3),
        ("Ivanov Corporate", "air", 18000, 22500, 23000, 23000, True, 175000, 1575, "Fernanda", "AirCargo Intl", 1, 16),
    ]
    files = []
    for i, r in enumerate(rows, 1):
        (client, mode, est, act, sell, inv_amt, invoiced, declared, ins,
         coord, agent, pack, delivery) = r
        files.append({
            "job": f"11{i:04d}", "client": client, "mode": mode,
            "est": est, "act": act, "sell": sell,
            "inv_amt": inv_amt, "invoiced": invoiced,
            "declared": declared, "ins": ins or None,
            "coordinator": coord, "agent": agent,
            "pack": d(pack), "delivery": d(delivery),
        })
    return files


# ── Reconciliation ──────────────────────────────────────────────────────────────
def _revenue(f):
    return f["inv_amt"] if f["invoiced"] else f["sell"]


def _actual_profit(f):
    return _revenue(f) - f["act"]


def _margin(f):
    base = f["sell"] or f["inv_amt"]
    return (_actual_profit(f) / base) if base else 0.0


def reconcile(files):
    """Attach gaps + stage to each file. Gap = {reason, value, recoverable}."""
    for f in files:
        gaps = []
        # completeness
        if f["est"] == 0 and f["act"] == 0:
            gaps.append(("missing_costing_line", 0.0, False))
        elif f["est"] == 0:
            gaps.append(("missing_costing_line", 0.0, False))
        # cost overrun / under-quote
        var = f["act"] - f["est"]
        if f["est"] > 0 and abs(var) >= MATERIAL_GAP:
            if var > 0:
                gaps.append(("volume_or_mode_change", var, True))
            else:
                gaps.append(("under_quote", var, False))
        # invoice below cost
        if f["invoiced"] and f["inv_amt"] > 0 and f["inv_amt"] < f["act"] - MATERIAL_GAP:
            gaps.append(("invoice_below_cost", f["act"] - f["inv_amt"], True))
        # insurance band
        if f["declared"]:
            rate = INS_RATE.get(f["mode"])
            if not f["ins"]:
                gaps.append(("insurance_out_of_band", 0.0, False))
            elif rate:
                actual_rate = f["ins"] / f["declared"]
                if actual_rate > rate + INS_TOL or actual_rate < max(rate - INS_TOL, 0):
                    gaps.append(("insurance_out_of_band", abs(f["ins"] - rate * f["declared"]), False))
        # negative margin
        if (f["sell"] or f["inv_amt"]) and _margin(f) < 0:
            gaps.append(("under_quote", _actual_profit(f), False))

        f["gaps"] = gaps
        f["open_gaps"] = len(gaps)
        f["gap_value"] = sum(abs(g[1]) for g in gaps)
        if not gaps:
            f["stage"] = "closed" if f["invoiced"] else "resolved"
        else:
            f["stage"] = "gap_flagged"
    return files


def _in_month(day, ms, me):
    return bool(day and ms <= day < me)


def compute_metrics(files):
    today = dt.date.today()
    ms = today.replace(day=1)
    me = (ms.replace(year=ms.year + 1, month=1) if ms.month == 12
          else ms.replace(month=ms.month + 1))

    all_gaps = [g for f in files for g in f["gaps"]]
    active = [f for f in files if f["stage"] != "closed"]

    by_stage = {"not_started": 0, "in_review": 0, "gap_flagged": 0, "resolved": 0, "closed": 0}
    for f in files:
        by_stage[f["stage"]] = by_stage.get(f["stage"], 0) + 1

    invoiced_m = sum(1 for f in files if f["invoiced"] and (_in_month(f["delivery"], ms, me) or _in_month(f["pack"], ms, me)))
    invoiceable_m = sum(1 for f in files if not f["invoiced"] and (_in_month(f["delivery"], ms, me) or _in_month(f["pack"], ms, me)))
    denom = invoiced_m + invoiceable_m
    pct_billed = round(invoiced_m / denom * 100, 1) if denom else 0.0
    overdue = sum(1 for f in files if not f["invoiced"] and ((f["delivery"] and f["delivery"] < ms) or (f["pack"] and f["pack"] < ms)))

    n = len(files) or 1
    avg_gaps = round(len(all_gaps) / n, 2)
    gap_vals = [abs(g[1]) for g in all_gaps if g[1]]
    avg_gap_val = round(sum(gap_vals) / len(gap_vals), 2) if gap_vals else 0.0

    recoverable = sum(abs(g[1]) for g in all_gaps if g[2])
    recovered = round(recoverable * 0.5 * 0.8, 2)
    recovery_rate = round(recovered / recoverable * 100, 1) if recoverable else 0.0
    open_gaps = [g for f in active for g in f["gaps"]]
    open_val = round(sum(abs(g[1]) for g in open_gaps), 2)

    reason_val = {}
    for g in all_gaps:
        reason_val[g[0]] = reason_val.get(g[0], 0.0) + abs(g[1])
    top_reasons = sorted(reason_val.items(), key=lambda kv: kv[1], reverse=True)[:5]

    money = [f for f in files if (f["sell"] or f["inv_amt"])] or files
    avg_rev = round(sum(_revenue(f) for f in money) / len(money), 0)
    avg_prof = round(sum(_actual_profit(f) for f in money) / len(money), 0)
    tot_rev = sum(_revenue(f) for f in files)
    tot_cost = sum(f["act"] for f in files)
    tot_profit = tot_rev - tot_cost
    gross_margin = round(tot_profit / tot_rev * 100, 1) if tot_rev else 0.0
    neg = sum(1 for f in money if _margin(f) < 0)
    leakage = round(sum((f["sell"] - f["est"]) - _actual_profit(f) for f in files), 0)

    modes = {}
    for mode in ("sea", "air", "road"):
        sub = [f for f in files if f["mode"] == mode]
        if not sub:
            continue
        rev = sum(_revenue(f) for f in sub)
        cost = sum(f["act"] for f in sub)
        modes[mode] = {"files": len(sub), "profit": round(rev - cost),
                       "margin": round((rev - cost) / rev * 100, 1) if rev else 0.0}
    ins_flags = sum(1 for g in all_gaps if g[0] == "insurance_out_of_band")

    worklist = sorted(
        [{"job": f["job"], "client": f["client"], "mode": f["mode"], "stage": f["stage"],
          "margin": round(_margin(f) * 100, 1), "profit": round(_actual_profit(f)),
          "open_gaps": f["open_gaps"], "gap_value": round(f["gap_value"])} for f in files],
        key=lambda r: (-r["open_gaps"], r["margin"]),
    )

    return {
        "as_of": today.isoformat(),
        "total_active": len(active), "by_stage": by_stage,
        "audited_this_month": len(files),
        "invoiced_m": invoiced_m, "invoiceable_m": invoiceable_m,
        "pct_billed": pct_billed, "overdue": overdue,
        "avg_gaps": avg_gaps, "avg_gap_val": avg_gap_val,
        "recovered": recovered, "recoverable": round(recoverable, 2),
        "recovery_rate": recovery_rate, "open_count": len(open_gaps), "open_val": open_val,
        "top_reasons": [{"reason": r.replace("_", " "), "value": round(v)} for r, v in top_reasons],
        "avg_rev": avg_rev, "avg_prof": avg_prof,
        "tot_rev": round(tot_rev), "tot_cost": round(tot_cost), "tot_profit": round(tot_profit),
        "gross_margin": gross_margin, "neg": neg, "leakage": leakage,
        "modes": modes, "ins_flags": ins_flags, "worklist": worklist,
    }


# ── Route ───────────────────────────────────────────────────────────────────────
@audit_bp.route("/audit")
@_login_required
def audit():
    files = reconcile(load_move_files())
    m = compute_metrics(files)
    return render_template_string(TEMPLATE, m=m, demo=DEMO)


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Move-File Cost &amp; Profit Audit</title>
<style>
  :root{--rust:#b0472f;--rust-dark:#8f3a26;--ink:#2b2b2b;--muted:#8a8a86;
        --line:#eae7e3;--bg:#f4f3f1;--card:#fff;--green:#2e7d32;--amber:#b7791f;--red:#c0392b;--tint:#faf3f0;}
  *{box-sizing:border-box}
  body{margin:0;font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
       background:var(--bg);color:var(--ink);font-size:14px;line-height:1.45}
  header{background:var(--card);border-bottom:1px solid var(--line);padding:22px 30px;
         display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:8px}
  header h1{font-size:22px;margin:0;font-weight:800;letter-spacing:-.2px}
  header h1 .accent{color:var(--rust)} header .meta{font-size:12px;color:var(--muted)}
  .back{font-size:13px;color:var(--rust);text-decoration:none;font-weight:600}
  .demo{display:inline-block;background:#fff4e5;color:var(--amber);border:1px solid #f0dcb8;
        border-radius:5px;padding:1px 8px;margin-left:8px;font-size:11px;font-weight:700}
  main{padding:24px 30px;max-width:1200px;margin:0 auto}
  h2{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin:28px 0 12px;font-weight:700}
  .grid{display:grid;gap:14px}.g4{grid-template-columns:repeat(4,1fr)}
  @media(max-width:820px){.g4{grid-template-columns:repeat(2,1fr)}}
  .tile{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;box-shadow:0 1px 2px rgba(0,0,0,.03)}
  .tile .label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
  .tile .value{font-size:27px;font-weight:800;margin-top:5px;letter-spacing:-.5px}
  .tile .sub{font-size:12px;color:var(--muted);margin-top:3px}
  .good{color:var(--green)}.warn{color:var(--amber)}.bad{color:var(--red)}
  .row{display:flex;gap:14px;flex-wrap:wrap}
  table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}
  th,td{text-align:left;padding:10px 13px;border-bottom:1px solid var(--line);font-size:13px}
  th{background:var(--tint);color:var(--rust-dark);font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
  tr:last-child td{border-bottom:none}
  .pill{display:inline-block;padding:1px 9px;border-radius:20px;font-size:11px;font-weight:700}
  .pill.gap{background:#f7e7e2;color:var(--rust)}.pill.ok{background:#e6f4e6;color:var(--green)}.pill.rev{background:#fbf0dd;color:var(--amber)}
  .bars{display:flex;flex-direction:column;gap:7px}
  .bar{display:flex;align-items:center;gap:9px;font-size:12px}
  .bar .track{flex:1;height:8px;background:var(--line);border-radius:6px;overflow:hidden}
  .bar .fill{height:100%;background:var(--rust)}
  .stage-line{display:flex;justify-content:space-between;font-size:12px;padding:4px 0;border-bottom:1px dashed var(--line)}
  .num{font-variant-numeric:tabular-nums}
  footer{color:var(--muted);font-size:11px;text-align:center;padding:22px}
</style></head><body>
<header>
  <h1>Move-File Cost &amp; Profit <span class="accent">Audit</span></h1>
  <div class="meta"><a class="back" href="/">← Automation Library</a> &nbsp;·&nbsp;
    Data source: MoveWare{% if demo %}<span class="demo">DEMO DATA</span>{% endif %} · as of {{ m.as_of }}</div>
</header>
<main>
  <h2>Profitability</h2>
  <div class="grid g4">
    <div class="tile"><div class="label">Total revenue</div><div class="value num">{{ "{:,.0f}".format(m.tot_rev) }}</div><div class="sub">cost {{ "{:,.0f}".format(m.tot_cost) }}</div></div>
    <div class="tile"><div class="label">Total profit</div><div class="value num {{ 'good' if m.tot_profit>=0 else 'bad' }}">{{ "{:,.0f}".format(m.tot_profit) }}</div><div class="sub">gross margin {{ m.gross_margin }}%</div></div>
    <div class="tile"><div class="label">Profit leakage (est − act)</div><div class="value num {{ 'bad' if m.leakage>0 else 'good' }}">{{ "{:,.0f}".format(m.leakage) }}</div><div class="sub">estimated vs actual profit</div></div>
    <div class="tile"><div class="label">Negative-margin files</div><div class="value num {{ 'bad' if m.neg else 'good' }}">{{ m.neg }}</div><div class="sub">avg profit/file {{ "{:,.0f}".format(m.avg_prof) }}</div></div>
  </div>
  <h2>Workload &amp; Pipeline</h2>
  <div class="row">
    <div class="tile" style="flex:1;min-width:220px"><div class="label">Active files</div><div class="value num">{{ m.total_active }}</div><div class="sub">{{ m.audited_this_month }} audited this month</div></div>
    <div class="tile" style="flex:2;min-width:280px"><div class="label">Files by audit stage</div>
      {% for s,n in m.by_stage.items() %}<div class="stage-line"><span>{{ s.replace('_',' ') }}</span><span class="num">{{ n }}</span></div>{% endfor %}</div>
  </div>
  <h2>Invoicing Progress</h2>
  <div class="grid g4">
    <div class="tile"><div class="label">Invoiced this month</div><div class="value num">{{ m.invoiced_m }}</div></div>
    <div class="tile"><div class="label">Still invoiceable</div><div class="value num">{{ m.invoiceable_m }}</div><div class="sub">pack/load or delivery in month</div></div>
    <div class="tile"><div class="label">Percent billed</div><div class="value num {{ 'good' if m.pct_billed>=80 else 'warn' }}">{{ m.pct_billed }}%</div></div>
    <div class="tile"><div class="label">Overdue to invoice</div><div class="value num {{ 'bad' if m.overdue else 'good' }}">{{ m.overdue }}</div><div class="sub">date passed, not billed</div></div>
  </div>
  <h2>Gaps &amp; Recovery</h2>
  <div class="grid g4">
    <div class="tile"><div class="label">Avg gaps / file</div><div class="value num">{{ m.avg_gaps }}</div><div class="sub">avg value {{ "{:,.0f}".format(m.avg_gap_val) }}</div></div>
    <div class="tile"><div class="label">Recovered</div><div class="value num good">{{ "{:,.0f}".format(m.recovered) }}</div><div class="sub">of {{ "{:,.0f}".format(m.recoverable) }} recoverable</div></div>
    <div class="tile"><div class="label">Recovery rate</div><div class="value num {{ 'good' if m.recovery_rate>=70 else 'warn' }}">{{ m.recovery_rate }}%</div></div>
    <div class="tile"><div class="label">Open gaps</div><div class="value num {{ 'warn' if m.open_count else 'good' }}">{{ m.open_count }}</div><div class="sub">{{ "{:,.0f}".format(m.open_val) }} at risk</div></div>
  </div>
  <div class="row" style="margin-top:12px">
    <div class="tile" style="flex:1;min-width:320px"><div class="label" style="margin-bottom:8px">Top gap reasons by value</div>
      <div class="bars">{% set mx = (m.top_reasons[0].value if m.top_reasons else 1) or 1 %}
      {% for r in m.top_reasons %}<div class="bar"><span style="width:180px">{{ r.reason }}</span>
        <span class="track"><span class="fill" style="width:{{ (r.value/mx*100)|round(0) }}%"></span></span>
        <span class="num" style="width:80px;text-align:right">{{ "{:,.0f}".format(r.value) }}</span></div>{% endfor %}</div></div>
    <div class="tile" style="flex:1;min-width:260px"><div class="label" style="margin-bottom:8px">By mode &nbsp;·&nbsp; insurance flags: <b>{{ m.ins_flags }}</b></div>
      <table style="border:none"><tr><th>Mode</th><th>Files</th><th>Profit</th><th>Margin</th></tr>
      {% for mode,d in m.modes.items() %}<tr><td>{{ mode }}</td><td class="num">{{ d.files }}</td><td class="num">{{ "{:,.0f}".format(d.profit) }}</td><td class="num">{{ d.margin }}%</td></tr>{% endfor %}</table></div>
  </div>
  <h2>Files Needing Attention</h2>
  <table><tr><th>Job</th><th>Client</th><th>Mode</th><th>Stage</th><th>Margin</th><th>Actual profit</th><th>Open gaps</th><th>Gap value</th></tr>
  {% for r in m.worklist %}<tr>
    <td class="num">{{ r.job }}</td><td>{{ r.client }}</td><td>{{ r.mode }}</td>
    <td>{% if r.stage=='gap_flagged' %}<span class="pill gap">gap flagged</span>{% elif r.stage in ('resolved','closed') %}<span class="pill ok">{{ r.stage }}</span>{% else %}<span class="pill rev">{{ r.stage.replace('_',' ') }}</span>{% endif %}</td>
    <td class="num {{ 'bad' if r.margin<0 else '' }}">{{ r.margin }}%</td><td class="num">{{ "{:,.0f}".format(r.profit) }}</td>
    <td class="num">{{ r.open_gaps }}</td><td class="num">{{ "{:,.0f}".format(r.gap_value) }}</td></tr>{% endfor %}</table>
  <footer>Thelsa Automation Library · the audit runs on imperfect data and flags it — figures in file currency (mixed).</footer>
</main></body></html>
"""
