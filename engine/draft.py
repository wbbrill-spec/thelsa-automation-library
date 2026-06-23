"""Cron alias: `python -m engine.draft` runs the drafting task for all active campaigns."""
from .run import main

if __name__ == "__main__":
    main("draft")
