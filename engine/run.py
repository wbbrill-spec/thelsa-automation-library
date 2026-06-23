"""
run.py — engine entrypoint. Run as a scheduled job:

    python -m engine.draft      # Task A (drafting)   -> e.g. weekly Sun 19:00 CT
    python -m engine.monitor    # Task B (monitoring) -> e.g. daily

(Equivalently: `python -m engine.run draft|monitor`.)

It iterates every campaign whose status is 'active' in the registry, so adding a
campaign needs no code change. Each task writes a `runs` row; failures also write
an alert. Set DRY_RUN=1 to exercise the full path without creating real drafts.
"""
import sys
from datetime import datetime, timezone

from . import config, sheets, alerts, draft_task, monitor_task


def _mailer():
    if config.graph_configured():
        from .mailer import GraphMailer
        return GraphMailer()
    return None  # DRY/local: tasks still run, just create no drafts / read no mail


def main(task):
    if task not in ("draft", "monitor"):
        raise SystemExit("usage: python -m engine.run draft|monitor")
    mailer = _mailer()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    campaigns = [c for c in sheets.read_tab("Campaigns")
                 if (c.get("status") or "").lower() == "active" and c.get("campaign_id")]
    totals = {}
    for c in campaigns:
        cid = c["campaign_id"]
        run_id = f"{task}_{cid}_{stamp}"
        try:
            fn = draft_task.run if task == "draft" else monitor_task.run
            totals[cid] = fn(cid, mailer, run_id=run_id)
        except Exception as exc:
            alerts.notify(cid, f"{task} task failed: {exc}")
            totals[cid] = {"error": str(exc)}
    print(f"{task} complete:", totals)
    return totals


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
