"""
metrics.py — is consolidation actually improving?

Nobody was measuring it. The programme's whole case is that filling trucks
saves money, and until now the dashboard could not say whether the team was
filling them any better this month than last. If we cannot show the curve, we
cannot defend the spend (Bill, 2026-09-23).

What is measured
----------------
  utilisation        space used ÷ trailer capacity, across every planned and
                     already-made load. The headline number.
  shipments/load     how many customers share a truck. Consolidation working
                     looks like this number going up.
  solo loads         trucks carrying exactly one file. The thing consolidation
                     is supposed to reduce; the clearest single indicator.
  trucks avoided     files beyond the first on each load. Defensible without
                     any cost assumption at all: eight files on one truck is
                     seven trucks that did not run.
  spare capacity     m³ of trailer already paid for and going out empty.

On money
--------
Savings in pesos need a baseline nobody has given us yet. Rather than invent
one, this module reports **trucks avoided** — a count we can defend from the
plan itself — and converts to money ONLY when a cost is configured, labelling
it an assumption wherever it appears. The one real figure we have is Fernanda's
22,000 MXN for a hired 53 ft trailer Monterrey → Mexico City (training,
21 Sep); that is the default, and it is one lane's price, not a rate card.

    CB_TRUCK_COST_MXN     default cost of a hired 53 ft trailer (22000)
    CB_LANE_COST          per-lane overrides, "Monterrey → Mexico City:22000,…"

A "truck avoided" is an upper bound: a single 12 m³ file would not really hire
a full 53 ft trailer. The number to quote out loud is utilisation and
shipments-per-load; trucks-avoided is the direction of travel, not an invoice.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
from typing import Optional

from .models import TRUCK_53_M3, norm_text

__all__ = ["summarise", "truck_cost", "record", "history", "reset"]

DEFAULT_TRUCK_COST_MXN = 22000.0
_LOCK = threading.Lock()
MAX_POINTS = int(os.environ.get("CB_METRICS_MAX", "400") or 400)


def truck_cost(lane: str = "") -> float:
    """Assumed cost of one hired 53 ft trailer on this lane, in MXN."""
    raw = (os.environ.get("CB_LANE_COST") or "").replace(";", ",")
    for pair in raw.split(","):
        if ":" in pair:
            k, v = pair.rsplit(":", 1)
            if norm_text(k) and norm_text(k) in norm_text(lane):
                try:
                    return float(v.strip())
                except ValueError:
                    pass
    try:
        return float(os.environ.get("CB_TRUCK_COST_MXN") or DEFAULT_TRUCK_COST_MXN)
    except ValueError:
        return DEFAULT_TRUCK_COST_MXN


def _load_rows(plan: dict) -> list[dict]:
    """Every truck the plan knows about: suggested loads AND the consolidations
    a coordinator has already made. Measuring only our own suggestions would
    flatter us — the team's own groupings are the real work."""
    rows: list[dict] = []
    for ld in plan.get("loads") or []:
        rows.append({
            "kind": "suggested", "lane": ld.get("lane", ""), "leg": ld.get("leg", ""),
            "files": len(ld.get("shipments") or []),
            "space": float(ld.get("space_m3") or ld.get("m3") or 0),
            "m3": float(ld.get("m3") or 0),
            "fill": float(ld.get("fill_pct") or 0),
        })
    for g in plan.get("groups") or []:
        rows.append({
            "kind": "actual", "lane": g.get("lane", ""), "leg": g.get("leg", ""),
            "files": int(g.get("customers") or 0),
            "space": float(g.get("space_m3") or 0),
            "m3": float(g.get("m3") or 0),
            "fill": float(g.get("fill_pct") or 0),
        })
    return rows


def summarise(plan: dict, today: Optional[dt.date] = None) -> dict:
    """The consolidation scoreboard for one day's plan."""
    today = today or dt.date.today()
    rows = _load_rows(plan)
    loads = len(rows)
    files = sum(r["files"] for r in rows)
    space = round(sum(r["space"] for r in rows), 1)
    m3 = round(sum(r["m3"] for r in rows), 1)
    capacity = round(loads * TRUCK_53_M3, 1)
    solo = sum(1 for r in rows if r["files"] == 1)
    shared = [r for r in rows if r["files"] > 1]
    avoided = sum(r["files"] - 1 for r in rows if r["files"] > 1)

    money = None
    if avoided:
        per = [truck_cost(r["lane"]) * (r["files"] - 1) for r in shared]
        money = {
            "trucks_avoided": avoided,
            "assumed_mxn": round(sum(per)),
            "per_load_mxn": round(sum(per) / len(shared)) if shared else 0,
            "assumption": f"a hired 53 ft trailer at {round(truck_cost()):,} MXN "
                          "(Fernanda's Monterrey → Mexico City figure, 21 Sep). "
                          "An upper bound: a single small file would not really "
                          "hire a whole trailer.",
        }

    spare_m3 = 0.0
    for h in (plan.get("spare_by_hub") or {}).values():
        if isinstance(h, dict) and h.get("hub") != "Unknown":
            spare_m3 += float(h.get("spare_m3") or 0)

    return {
        "as_of": today.isoformat(),
        "loads": loads,
        "actual_loads": sum(1 for r in rows if r["kind"] == "actual"),
        "files_on_trucks": files,
        "utilisation_pct": round(space / capacity * 100) if capacity else 0,
        "space_m3": space, "capacity_m3": capacity, "volume_m3": m3,
        "files_per_load": round(files / loads, 1) if loads else 0,
        "solo_loads": solo,
        "solo_pct": round(solo / loads * 100) if loads else 0,
        "shared_loads": len(shared),
        "savings": money,
        "paid_spare_m3": round(spare_m3, 1),
        "unplannable": int(plan.get("unsized") or 0),
        "by_leg": _by_leg(rows),
    }


def _by_leg(rows: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for r in rows:
        b = out.setdefault(r["leg"] or "?", {"leg": r["leg"] or "?", "loads": 0,
                                             "files": 0, "space": 0.0})
        b["loads"] += 1
        b["files"] += r["files"]
        b["space"] += r["space"]
    for b in out.values():
        cap = b["loads"] * TRUCK_53_M3
        b["utilisation_pct"] = round(b["space"] / cap * 100) if cap else 0
        b["files_per_load"] = round(b["files"] / b["loads"], 1) if b["loads"] else 0
        b["space"] = round(b["space"], 1)
    return list(out.values())


# ── the curve ────────────────────────────────────────────────────────────────
# One point a day. Without this the board can say "72% today" but never
# "72%, up from 58% a month ago", and the second sentence is the one that
# settles whether this programme is working.

def _state_path() -> str:
    override = (os.environ.get("CB_METRICS_PATH") or "").strip()
    if override:
        return override
    for c in ("/var/data", "/data"):
        if os.path.isdir(c) and os.access(c, os.W_OK):
            return os.path.join(c, "crossborder_metrics.json")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "crossborder_metrics.json")


def _read() -> list:
    try:
        with open(_state_path()) as fh:
            return json.load(fh) or []
    except Exception:  # noqa: BLE001
        return []


def record(summary: dict) -> dict:
    """Keep one point per day — the last reading of the day wins."""
    day = summary.get("as_of") or dt.date.today().isoformat()
    keep = {k: summary.get(k) for k in
            ("as_of", "loads", "files_on_trucks", "utilisation_pct", "files_per_load",
             "solo_loads", "solo_pct", "space_m3", "volume_m3", "paid_spare_m3")}
    keep["trucks_avoided"] = (summary.get("savings") or {}).get("trucks_avoided", 0)
    with _LOCK:
        points = [p for p in _read() if p.get("as_of") != day]
        points.append(keep)
        points = sorted(points, key=lambda p: p.get("as_of") or "")[-MAX_POINTS:]
        try:
            p = _state_path()
            with open(p + ".tmp", "w") as fh:
                json.dump(points, fh)
            os.replace(p + ".tmp", p)
        except Exception:  # noqa: BLE001 — a nicety, never load-bearing
            pass
    return {"points": len(points), "day": day}


def history(limit: int = 90) -> dict:
    points = _read()[-max(1, limit):]
    out = {"count": len(points), "points": points}
    if len(points) >= 2:
        first, last = points[0], points[-1]
        out["change"] = {
            "from": first.get("as_of"), "to": last.get("as_of"),
            "utilisation_pct": (last.get("utilisation_pct") or 0) - (first.get("utilisation_pct") or 0),
            "files_per_load": round((last.get("files_per_load") or 0) - (first.get("files_per_load") or 0), 1),
            "solo_pct": (last.get("solo_pct") or 0) - (first.get("solo_pct") or 0),
        }
    return out


def reset() -> None:
    with _LOCK:
        try:
            os.remove(_state_path())
        except OSError:
            pass
