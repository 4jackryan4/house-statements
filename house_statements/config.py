"""Shared paths and constants."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
STATEMENTS_DIR = DATA_DIR / "statements"
MEMBERS_FILE = DATA_DIR / "members.json"
HEALTH_FILE = DATA_DIR / "health.json"
STATE_FILE = DATA_DIR / "state.json"
SITE_DIR = ROOT / "site"
WEB_DIR = ROOT / "web"

# Start of the 119th Congress. Older statements are ignored.
START_DATE = os.environ.get("START_DATE", "2025-01-03")

USER_AGENT = (
    "house-statements/1.0 (+https://github.com/4jackryan4/house-statements; "
    "collects public press releases from house.gov member sites)"
)

# Which parties to collect. The user asked for House Democrats only for now.
PARTIES = [p.strip() for p in os.environ.get("PARTIES", "Democrat").split(",") if p.strip()]

# Event grouping: statements from at least this many different members form an event.
EVENT_MIN_MEMBERS = int(os.environ.get("EVENT_MIN_MEMBERS", "4"))

