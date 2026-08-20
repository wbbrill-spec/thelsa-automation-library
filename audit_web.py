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
        # Demo-only: seed a couple of intentional "revenue doesn't add up"
        # mismatches so the calculation-accuracy section is visibly populated.
        # Live data supplies rev_reported / rev_lines from real charge lines.
        rev_lines = sell
        if i == 2:
            rev_lines = sell - 300      # reported header is 300 over the line items
        elif i == 7:
            rev_lines = sell + 150      # reported header is 150 under the line items
        files.append({
            "job": f"11{i:04d}", "client": client, "mode": mode,
            "est": est, "act": act, "sell": sell,
            "inv_amt": inv_amt, "invoiced": invoiced,
            "declared": declared, "ins": ins or None,
            "coordinator": coord, "agent": agent,
            "pack": d(pack), "delivery": d(delivery),
            "rev_reported": sell, "rev_lines": rev_lines, "n_rev_lines": 4,
            "cost_lines": est,
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


def _reconcile_revenue(files):
    """Revenue/invoicing reconciliation for live Thelsa data (no cost available).

    Stages follow the INVOICING pipeline, and gaps are revenue-only:
      • invoiced_vs_quoted — invoiced total differs from the quoted sell price
      • delivered_not_invoiced — delivery date passed but nothing invoiced yet
    """
    today = dt.date.today()
    for f in files:
        gaps = []
        sell = f.get("sell", 0) or 0
        inv = f.get("inv_amt", 0) or 0
        invoiced = bool(f.get("invoiced"))
        delivery = f.get("delivery")

        if invoiced and sell and abs(inv - sell) >= CALC_EPS:
            gaps.append(("invoiced_vs_quoted", inv - sell, True))
        if (not invoiced) and delivery and delivery < today and sell:
            gaps.append(("delivered_not_invoiced", sell, True))

        f["gaps"] = gaps
        f["open_gaps"] = len(gaps)
        f["gap_value"] = sum(abs(g[1]) for g in gaps)
        if invoiced:
            f["stage"] = "closed"
        elif delivery and delivery < today:
            f["stage"] = "gap_flagged"        # delivered but not yet invoiced
        elif sell:
            f["stage"] = "in_review"          # quoted / in progress
        else:
            f["stage"] = "not_started"
    return files


def reconcile(files, cost_available=True):
    """Attach gaps + stage to each file. Gap = {reason, value, recoverable}.

    When cost isn't available (live Thelsa RestV1) we use the revenue/invoicing
    reconciliation instead of the cost-based one.
    """
    if not cost_available:
        return _reconcile_revenue(files)
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


# ── Calculation-accuracy check (revenue & cost) ──────────────────────────────────
# For every ACTIVE file, verify the revenue and cost figures are accurately
# calculated. Two lenses, per spec ("both"):
#   1. Internal recalculation  — does each figure add up from its own line items?
#   2. Quote-to-invoice recon  — do the figures agree across the chain
#      (quoted sell → client invoice, quoted cost → actual/supplier cost)?
# Any non-zero difference beyond half-a-cent float noise is flagged; the file's
# responsible move coordinator is alerted (draft, see coordinator_alerts.py) and
# the amount rolls up into the "discrepancy by coordinator" dashboard metric.
CALC_EPS = 0.005


def _disc_active(f):
    # Mirror compute_metrics' notion of "active" (anything not fully closed).
    return f.get("stage") != "closed"


def check_calculations(files):
    """Attach `disc_flags` (list) and `disc_value` (float) to each file.

    disc_flags entries: {type, label, expected, found, diff}. disc_value is the
    summed absolute discrepancy across all flags on that file. Only active files
    are checked; closed files get an empty result.
    """
    for f in files:
        flags = []
        # Revenue checks run on EVERY file (including invoiced/closed) — the
        # quote-vs-invoice mismatch is exactly what we want to catch on invoiced
        # files. The cost check below simply won't fire when cost is unavailable.
        if True:
            # 1a) Revenue internal recalculation: the reported revenue header must
            #     equal the sum of its charge line items. Skipped when no line
            #     items are available (n_rev_lines == 0) so we never invent a flag
            #     on files that simply lack a breakdown.
            rev_rep = f.get("rev_reported")
            rev_lines = f.get("rev_lines")
            if f.get("n_rev_lines", 0) and rev_rep is not None and rev_lines is not None:
                diff = round(rev_rep - rev_lines, 2)
                if abs(diff) > CALC_EPS:
                    flags.append({
                        "type": "revenue_not_summing",
                        "label": "Revenue total ≠ sum of line items",
                        "expected": round(rev_lines, 2), "found": round(rev_rep, 2),
                        "diff": diff,
                    })
            # 1b) Revenue quote-to-invoice: the client-invoiced total must equal
            #     the quoted/agreed sell price.
            if f.get("invoiced") and f.get("inv_amt", 0):
                diff = round(f["inv_amt"] - f["sell"], 2)
                if abs(diff) > CALC_EPS:
                    flags.append({
                        "type": "invoiced_vs_quoted",
                        "label": "Invoiced amount ≠ quoted revenue",
                        "expected": round(f["sell"], 2), "found": round(f["inv_amt"], 2),
                        "diff": diff,
                    })
            # 2) Cost quote-to-actual: the actual/supplier cost must equal the
            #    quoted (estimated) cost. Only asserted when both are present.
            if f.get("est", 0) and f.get("act", 0):
                diff = round(f["act"] - f["est"], 2)
                if abs(diff) > CALC_EPS:
                    flags.append({
                        "type": "cost_quote_vs_actual",
                        "label": "Actual cost ≠ quoted cost",
                        "expected": round(f["est"], 2), "found": round(f["act"], 2),
                        "diff": diff,
                    })
        f["disc_flags"] = flags
        f["disc_value"] = round(sum(abs(g["diff"]) for g in flags), 2)
    return files


def _in_month(day, ms, me):
    return bool(day and ms <= day < me)


def compute_metrics(files, live_counts=None, cost_available=True):
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
    _lm = len(money) or 1
    avg_rev = round(sum(_revenue(f) for f in money) / _lm, 0)
    avg_prof = round(sum(_actual_profit(f) for f in money) / _lm, 0)
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

    # ── Calculation-accuracy discrepancies by move coordinator ──
    # For the revenue audit (no cost), invoiced/closed files are exactly the ones
    # whose invoiced-vs-quoted mismatch matters, so scope to ALL files. In the
    # cost/demo view, keep the original "active files" scope.
    disc_scope = files if not cost_available else active
    disc_by_coord = {}
    disc_files = 0
    for f in disc_scope:
        dv = f.get("disc_value", 0) or 0
        c = f.get("coordinator") or "Unassigned"
        entry = disc_by_coord.setdefault(c, {"coordinator": c, "value": 0.0, "files": 0})
        if dv > 0:
            disc_files += 1
            entry["value"] += dv
            entry["files"] += 1
    by_coordinator_disc = sorted(
        [{"coordinator": e["coordinator"], "value": round(e["value"], 2), "files": e["files"]}
         for e in disc_by_coord.values() if e["files"]],
        key=lambda r: -r["value"],
    )
    total_disc = round(sum(e["value"] for e in by_coordinator_disc), 2)
    disc_worklist = sorted(
        [{"job": f["job"], "client": f["client"],
          "coordinator": f.get("coordinator") or "Unassigned",
          "value": f.get("disc_value", 0),
          "types": ", ".join(sorted({g["label"] for g in f.get("disc_flags", [])}))}
         for f in disc_scope if f.get("disc_value", 0) > 0],
        key=lambda r: -r["value"],
    )

    # True active-file count comes from paging the light /jobs feed (no per-file
    # sub-calls). The deep-loaded `files` are only a sample used for the financial
    # worklist, so `len(active)` here would just be the sample cap (~10). When the
    # live feed count is available, show the REAL number and mark the sample.
    sample_n = len(files)
    if live_counts:
        feed_total = live_counts.get("total") or 0
        feed_total_approx = bool(live_counts.get("total_approx"))
        true_active = live_counts.get("active")   # may be None (active not computed)
        active_available = true_active is not None
        active_estimated = bool(live_counts.get("active_estimated"))
        feed_exhausted = bool(live_counts.get("exhausted"))
        feed_pages = live_counts.get("pages", 0)
        audit_running = bool(live_counts.get("audit_running"))
        audit_wrapped = bool(live_counts.get("audit_wrapped"))
    else:
        true_active = len(active)
        active_available = True
        feed_total = len(files)
        feed_total_approx = False
        active_estimated = False
        feed_exhausted = True
        feed_pages = 0
        audit_running = False
        audit_wrapped = False

    # Revenue is real (posted invoices / quoted sell). Cost is only meaningful
    # when we actually have supplier cost — false on live RestV1 data, so the
    # template hides profit / margin / leakage rather than showing 0s.
    tot_revenue = round(tot_rev)

    return {
        "as_of": today.isoformat(),
        "cost_available": cost_available,
        "tot_revenue": tot_revenue,
        "total_active": true_active, "by_stage": by_stage,
        "active_estimated": active_estimated, "feed_total_approx": feed_total_approx,
        "active_available": active_available,
        "audit_running": audit_running, "audit_wrapped": audit_wrapped,
        "sample_n": sample_n, "feed_total": feed_total,
        "feed_exhausted": feed_exhausted, "feed_pages": feed_pages,
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
        "total_disc": total_disc, "disc_files": disc_files,
        "coords_affected": len(by_coordinator_disc),
        "by_coordinator_disc": by_coordinator_disc, "disc_worklist": disc_worklist,
    }


# ── Route ───────────────────────────────────────────────────────────────────────
def _load_checked():
    """Return (files, is_live, counts).

    LIVE: render from the background auditor's growing cache — never call the
    Moveware API synchronously on the page load (that caused the timeouts/500s).
    `files` is whatever the auditor has revenue-checked so far (may be empty while
    it warms up). `counts` carries the feed total + audit progress for the tiles.
    DEMO (no creds): the built-in demo dataset with cost, as before.
    """
    try:
        import mw_live
        if mw_live.have_creds():
            mw_live.ensure_auditor()
            audited = mw_live.audited_files()
            prog = mw_live.audit_progress()
            counts = {
                "total": prog.get("total"),
                "total_approx": True,
                "active": None,           # per-status active needs RestV2
                "active_estimated": True,
                "exhausted": False,
                "pages": 0,
                "audit_running": prog.get("running"),
                "audit_wrapped": prog.get("wrapped"),
            }
            files = reconcile(audited, cost_available=False) if audited else []
            check_calculations(files)
            return files, True, counts
    except Exception:
        pass
    # DEMO fallback (no credentials)
    files = reconcile(load_move_files())
    check_calculations(files)
    return files, False, None


@audit_bp.route("/audit")
@_login_required
def audit():
    files, is_live, counts = _load_checked()
    # Live RestV1 data has NO supplier cost (see mw_live._map_job), so profit and
    # margin can't be computed from it — hide them rather than show fabricated 0s.
    m = compute_metrics(files, live_counts=counts, cost_available=not is_live)
    return render_template_string(TEMPLATE, m=m, demo=not is_live)


@audit_bp.route("/audit/alerts")
@_login_required
def audit_alerts_preview():
    """Preview the coordinator discrepancy alerts. Creates nothing."""
    from flask import jsonify
    import coordinator_alerts as ca
    files, is_live, _counts = _load_checked()
    return jsonify({
        "live": is_live,
        "enabled": ca.alerts_enabled(),
        "note": ("Preview only — no drafts created. Drafts are created via POST "
                 "/audit/alerts/draft, and only when AUDIT_ALERTS_ENABLED=1, "
                 "DRY_RUN!=1, and the data is live."),
        "cc": ca._cc_list(),
        "alerts": ca.build_alerts(files),
    })


@audit_bp.route("/audit/alerts/draft", methods=["POST"])
@_login_required
def audit_alerts_draft():
    """Create one review-ready DRAFT per coordinator (gated; never sends)."""
    from flask import jsonify
    import coordinator_alerts as ca
    files, is_live, _counts = _load_checked()
    return jsonify(ca.create_drafts(files, live=is_live))


@audit_bp.route("/audit/raw")
@_login_required
def audit_raw():
    from flask import jsonify
    try:
        import mw_live
        return jsonify(mw_live.raw_sample())
    except Exception as e:
        return jsonify({"error": str(e)})


@audit_bp.route("/audit/counts")
@_login_required
def audit_counts():
    """Debug: the TRUE file counts from paging the light /jobs feed, incl. the
    per-status breakdown used to refine which statuses count as 'active'."""
    from flask import jsonify
    try:
        import mw_live
        return jsonify(mw_live.live_file_counts() or {"note": "no live counts (no creds or no data)"})
    except Exception as e:
        return jsonify({"error": str(e)})


@audit_bp.route("/audit/probe")
@_login_required
def audit_probe():
    """Debug: try candidate cost/creditor sub-resources for a job id and report
    which exist and what they contain, so we can find where supplier cost lives."""
    from flask import jsonify, request
    import mw_live
    jid = request.args.get("id", "").strip()
    if not jid:
        return jsonify({"error": "pass ?id=JOBID"})
    candidates = [
        f"/jobs/{jid}/account",
        f"/jobs/{jid}/account?type=creditor",
        f"/jobs/{jid}/account?entity=creditor",
        f"/jobs/{jid}/creditors",
        f"/jobs/{jid}/creditor",
        f"/jobs/{jid}/costs",
        f"/jobs/{jid}/cost",
        f"/jobs/{jid}/expenses",
        f"/jobs/{jid}/payables",
        f"/jobs/{jid}/payable",
        f"/jobs/{jid}/purchaseorders",
        f"/jobs/{jid}/suppliers",
        f"/jobs/{jid}/disbursements",
        f"/jobs/{jid}/financials",
        f"/jobs/{jid}/charges",
        f"/jobs/{jid}/estimate",
        f"/jobs/{jid}/costing",
        f"/creditors?jobId={jid}",
        f"/creditorinvoices?jobId={jid}",
    ]
    out = {"id": jid, "results": {}}
    for path in candidates:
        try:
            r = mw_live._get_timed(path, 8)
            if isinstance(r, dict):
                keys = list(r.keys())
                arrs = {k: len(v) for k, v in r.items() if isinstance(v, list)}
                out["results"][path] = {"ok": True, "keys": keys, "arrays": arrs}
            else:
                out["results"][path] = {"ok": True, "type": str(type(r))}
        except Exception as e:
            msg = str(e)
            out["results"][path] = {"ok": False, "err": msg[:120]}
    return jsonify(out)


@audit_bp.route("/audit/scancost")
@_login_required
def audit_scancost():
    """Debug: scan N jobs' /account ledger and report, across all entries, which
    entity types appear (debtor vs creditor vs other) — i.e. whether V1's account
    endpoint EVER exposes creditor/supplier COST, not just debtor/revenue. Also
    dumps a few sample creditor entries if any are found."""
    from flask import jsonify, request
    import mw_live
    off = int(request.args.get("offset", "1"))
    n = min(int(request.args.get("n", "12")), 20)
    try:
        payload = mw_live._get_timed(f"/jobs?limit={n}&offset={off}", 10)
        jobs = mw_live._page_jobs(payload)
    except Exception as e:
        return jsonify({"error": f"job list: {e}"})
    entity_key_counts: dict = {}
    field_counts: dict = {}
    jobs_with_creditor = []
    creditor_samples = []
    scanned = 0
    for j in jobs:
        jid = str(j.get("id") or "")
        if not jid:
            continue
        try:
            acc = mw_live._get_timed(f"/jobs/{jid}/account", 8)
        except Exception:
            continue
        scanned += 1
        entries = acc.get("account", []) if isinstance(acc, dict) else []
        has_cred = False
        for e in entries:
            ent = e.get("entity", {}) if isinstance(e, dict) else {}
            for k in (ent.keys() if isinstance(ent, dict) else []):
                entity_key_counts[k] = entity_key_counts.get(k, 0) + 1
            for k in (e.keys() if isinstance(e, dict) else []):
                field_counts[k] = field_counts.get(k, 0) + 1
            # look for any cost/creditor signal anywhere in the entry
            blob = str(e).lower()
            if "creditor" in blob or "supplier" in blob:
                has_cred = True
                if len(creditor_samples) < 3:
                    creditor_samples.append(e)
        if has_cred:
            jobs_with_creditor.append(jid)
    return jsonify({
        "offset": off, "requested": n, "jobs_scanned": scanned,
        "entity_key_counts": entity_key_counts,
        "entry_field_counts": field_counts,
        "jobs_with_creditor_signal": jobs_with_creditor,
        "creditor_samples": creditor_samples,
    })


@audit_bp.route("/audit/rawjobs")
@_login_required
def audit_rawjobs():
    """Debug: light list of REAL jobs at a given feed offset (default: the most
    recent 15). Lets us pick live job ids that actually carry money to inspect."""
    from flask import jsonify, request
    try:
        import mw_live
        off = int(request.args.get("offset", "485"))
        lim = int(request.args.get("limit", "15"))
        payload = mw_live._get_timed(f"/jobs?limit={lim}&offset={off}", 10)
        jobs = mw_live._page_jobs(payload)
        keys = ("id", "name", "status", "jobType", "method", "origin", "destination")
        out = [{k: j.get(k) for k in keys} for j in jobs if isinstance(j, dict)]
        return jsonify({"offset": off, "limit": lim, "count": len(jobs), "jobs": out})
    except Exception as e:
        return jsonify({"error": str(e)})


@audit_bp.route("/audit/rawjob")
@_login_required
def audit_rawjob():
    """Debug: full quotes / invoices / account payloads for one job id, so we can
    see exactly where sell, estimated cost, and actual (supplier) cost live."""
    from flask import jsonify, request
    import mw_live
    jid = request.args.get("id", "").strip()
    out = {"id": jid}
    if not jid:
        return jsonify({"error": "pass ?id=JOBID"})
    for res in ("quotes", "invoices", "account"):
        try:
            out[res] = mw_live._get_timed(f"/jobs/{jid}/{res}", 10)
        except Exception as e:
            out[res + "_error"] = str(e)
    return jsonify(out)


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
  {% if not demo %}
  {% if m.sample_n == 0 %}
  <div style="background:color-mix(in srgb,var(--amber) 12%,transparent);border:1px solid #f0dcb8;border-radius:12px;padding:11px 15px;margin-bottom:6px;font-size:12.5px;color:var(--amber)">
    <b>Audit warming up.</b> A background job is scanning your {% if m.feed_total %}~{{ "{:,}".format(m.feed_total) }}{% endif %} MoveWare files and revenue-checking them one batch at a time. Revenue and invoicing figures will start appearing within a minute — refresh shortly.
  </div>
  {% else %}
  <div style="background:var(--tint);border:1px solid var(--line);border-radius:12px;padding:11px 15px;margin-bottom:6px;font-size:12.5px;color:var(--muted)">
    <b style="color:var(--ink)">{% if m.feed_total_approx %}~{% endif %}{{ "{:,}".format(m.feed_total) }} files in MoveWare</b> · <b style="color:var(--ink)">{{ "{:,}".format(m.sample_n) }}</b> revenue-checked so far{% if m.audit_wrapped %} (full pass complete){% elif m.audit_running %} and counting{% endif %}. The revenue &amp; invoicing figures below cover the files checked to date and grow as the background audit expands coverage. Cost, profit and margin are not shown — Thelsa's current MoveWare API exposes revenue (quotes &amp; invoices) but not supplier cost.
  </div>
  {% endif %}
  {% endif %}
  <h2>{% if m.cost_available %}Profitability{% else %}Revenue{% endif %}</h2>
  {% if m.cost_available %}
  <div class="grid g4">
    <div class="tile"><div class="label">Total revenue</div><div class="value num">{{ "{:,.0f}".format(m.tot_rev) }}</div><div class="sub">cost {{ "{:,.0f}".format(m.tot_cost) }}</div></div>
    <div class="tile"><div class="label">Total profit</div><div class="value num {{ 'good' if m.tot_profit>=0 else 'bad' }}">{{ "{:,.0f}".format(m.tot_profit) }}</div><div class="sub">gross margin {{ m.gross_margin }}%</div></div>
    <div class="tile"><div class="label">Profit leakage (est − act)</div><div class="value num {{ 'bad' if m.leakage>0 else 'good' }}">{{ "{:,.0f}".format(m.leakage) }}</div><div class="sub">estimated vs actual profit</div></div>
    <div class="tile"><div class="label">Negative-margin files</div><div class="value num {{ 'bad' if m.neg else 'good' }}">{{ m.neg }}</div><div class="sub">avg profit/file {{ "{:,.0f}".format(m.avg_prof) }}</div></div>
  </div>
  {% else %}
  <div class="row">
    <div class="tile" style="flex:1;min-width:240px"><div class="label">Revenue (checked so far)</div><div class="value num">{{ "{:,.0f}".format(m.tot_revenue) }}</div><div class="sub">invoiced where billed, else quoted · {{ "{:,}".format(m.sample_n) }} files</div></div>
    <div class="tile" style="flex:2;min-width:320px;background:var(--tint)"><div class="label" style="color:var(--rust-dark)">Cost &amp; profit — not available yet</div><div class="sub" style="margin-top:6px;line-height:1.5">Moveware RestV1 does not expose supplier/creditor cost (the account endpoint returns client receivables, not what Thelsa pays agents/carriers). Profit and margin are hidden rather than shown as fabricated zeros. Restoring them needs a real cost source — RestV2 or a confirmed creditor endpoint.</div></div>
  </div>
  {% endif %}
  <h2>Workload &amp; Pipeline</h2>
  <div class="row">
    <div class="tile" style="flex:1;min-width:220px"><div class="label">{% if demo %}Active files{% else %}Files in MoveWare{% endif %}</div><div class="value num">{% if demo %}{{ m.total_active }}{% else %}{% if m.feed_total_approx %}~{% endif %}{{ "{:,}".format(m.feed_total) }}{% endif %}</div><div class="sub">{% if not demo %}{{ "{:,}".format(m.sample_n) }} revenue-checked so far{% else %}{{ m.audited_this_month }} audited this month{% endif %}</div></div>
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
  {% if m.cost_available %}
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
  {% endif %}
  <h2>Calculation Accuracy — Revenue &amp; Cost</h2>
  <div class="grid g4">
    <div class="tile"><div class="label">Total discrepancy</div><div class="value num {{ 'bad' if m.total_disc else 'good' }}">{{ "{:,.0f}".format(m.total_disc) }}</div><div class="sub">across active files</div></div>
    <div class="tile"><div class="label">Files with discrepancies</div><div class="value num {{ 'bad' if m.disc_files else 'good' }}">{{ m.disc_files }}</div><div class="sub">revenue/cost not reconciling</div></div>
    <div class="tile"><div class="label">Coordinators affected</div><div class="value num {{ 'warn' if m.coords_affected else 'good' }}">{{ m.coords_affected }}</div><div class="sub">each alerted by draft</div></div>
    <div class="tile"><div class="label">Checks applied</div><div class="value num">3</div><div class="sub">recalc · quote↔invoice · quote↔cost</div></div>
  </div>
  <div class="row" style="margin-top:12px">
    <div class="tile" style="flex:1;min-width:320px"><div class="label" style="margin-bottom:8px">Total discrepancy by move coordinator <span style="color:var(--muted)">· amount · # files</span></div>
      {% if m.by_coordinator_disc %}
      <div class="bars">{% set mxd = (m.by_coordinator_disc[0].value if m.by_coordinator_disc else 1) or 1 %}
      {% for c in m.by_coordinator_disc %}<div class="bar"><span style="width:150px">{{ c.coordinator }}</span>
        <span class="track"><span class="fill" style="width:{{ (c.value/mxd*100)|round(0) }}%"></span></span>
        <span class="num" style="width:120px;text-align:right">{{ "{:,.0f}".format(c.value) }} <span style="color:var(--muted)">· {{ c.files }}</span></span></div>{% endfor %}</div>
      {% else %}<div class="sub good">No revenue/cost discrepancies on active files. ✓</div>{% endif %}
    </div>
  </div>
  {% if m.disc_worklist %}
  <table style="margin-top:12px"><tr><th>Job</th><th>Client</th><th>Coordinator</th><th>Discrepancy type(s)</th><th>Amount</th></tr>
  {% for r in m.disc_worklist %}<tr>
    <td class="num">{{ r.job }}</td><td>{{ r.client }}</td><td>{{ r.coordinator }}</td>
    <td>{{ r.types }}</td><td class="num bad">{{ "{:,.0f}".format(r.value) }}</td></tr>{% endfor %}</table>
  {% endif %}

  <h2>{% if m.cost_available %}Files Needing Attention{% else %}Sampled Files{% endif %}</h2>
  {% if m.cost_available %}
  <table><tr><th>Job</th><th>Client</th><th>Mode</th><th>Stage</th><th>Margin</th><th>Actual profit</th><th>Open gaps</th><th>Gap value</th></tr>
  {% for r in m.worklist %}<tr>
    <td class="num">{{ r.job }}</td><td>{{ r.client }}</td><td>{{ r.mode }}</td>
    <td>{% if r.stage=='gap_flagged' %}<span class="pill gap">gap flagged</span>{% elif r.stage in ('resolved','closed') %}<span class="pill ok">{{ r.stage }}</span>{% else %}<span class="pill rev">{{ r.stage.replace('_',' ') }}</span>{% endif %}</td>
    <td class="num {{ 'bad' if r.margin<0 else '' }}">{{ r.margin }}%</td><td class="num">{{ "{:,.0f}".format(r.profit) }}</td>
    <td class="num">{{ r.open_gaps }}</td><td class="num">{{ "{:,.0f}".format(r.gap_value) }}</td></tr>{% endfor %}</table>
  {% else %}
  <table><tr><th>Job</th><th>Client</th><th>Mode</th><th>Stage</th></tr>
  {% for r in m.worklist %}<tr>
    <td class="num">{{ r.job }}</td><td>{{ r.client }}</td><td>{{ r.mode }}</td>
    <td>{% if r.stage=='gap_flagged' %}<span class="pill gap">gap flagged</span>{% elif r.stage in ('resolved','closed') %}<span class="pill ok">{{ r.stage }}</span>{% else %}<span class="pill rev">{{ r.stage.replace('_',' ') }}</span>{% endif %}</td></tr>{% endfor %}</table>
  <p class="sub" style="color:var(--muted);margin-top:8px">Sample of {{ m.sample_n }} files deep-checked this load (of {{ m.total_active }} active). Cost/margin columns hidden — supplier cost not available from Moveware RestV1.</p>
  {% endif %}
  <footer>Thelsa Automation Library · the audit runs on imperfect data and flags it — figures in file currency (mixed).</footer>
</main></body></html>
"""
