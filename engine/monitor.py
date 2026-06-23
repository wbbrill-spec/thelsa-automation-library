"""Cron alias: `python -m engine.monitor` runs the monitoring task for all active campaigns."""
from .run import main

if __name__ == "__main__":
    main("monitor")
