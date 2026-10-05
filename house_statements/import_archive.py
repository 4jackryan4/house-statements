"""Import House statements from the dwillis/congress-press archive (MIT licensed).

congress-press runs the same python-statement scrapers once a day and keeps full text, so it
backfills history and fills gaps if our own hourly scrape misses something or gets blocked.
"""

import argparse
import datetime as dt
import json

import requests

from . import store
from .config import START_DATE, USER_AGENT
from .members import load_members

ARCHIVE_URL = "https://raw.githubusercontent.com/dwillis/congress-press/main/data/{year}/{year}-{month:02d}.jsonl"


def months_between(start: dt.date, end: dt.date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def import_month(records: dict, house_ids: set, year: int, month: int, now: str) -> tuple[int, int]:
    resp = requests.get(ARCHIVE_URL.format(year=year, month=month), headers={"User-Agent": USER_AGENT}, timeout=120)
    if resp.status_code == 404:
        return 0, 0
    resp.raise_for_status()
    added = updated = 0
    for line in resp.text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        member = row.get("member") or {}
        bioguide = member.get("bioguide_id")
        if member.get("chamber") != "House" or bioguide not in house_ids:
            continue
        date = row.get("date")
        if not date or date < START_DATE or not row.get("url") or not row.get("title"):
            continue
        sid = store.statement_id(row["url"])
        text = (row.get("text") or "").strip() or None
        existing = records.get(sid)
        if existing:
            if text and not existing.get("text"):
                existing["text"] = text
                updated += 1
            continue
        records[sid] = {
            "id": sid,
            "url": row["url"],
            "title": " ".join(row["title"].split()),
            "date": date,
            "date_source": "archive",
            "bioguide": bioguide,
            "source": "congress-press",
            "first_seen": now,
            "text": text,
            "text_attempts": 0 if text is None else 1,
        }
        added += 1
    return added, updated


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default=None, help="first month to import (YYYY-MM); default: last 2 months")
    parser.add_argument("--min-hours", type=float, default=0, help="skip if the last import was more recent than this")
    args = parser.parse_args(argv)

    state = store.load_state()
    now_dt = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    last = state.get("archive_imported_at")
    if args.min_hours and last and (now_dt - dt.datetime.fromisoformat(last)).total_seconds() < args.min_hours * 3600:
        print(f"import_archive: last import {last}, skipping")
        return

    today = now_dt.date()
    if args.since:
        start = dt.date.fromisoformat(args.since + "-01")
    else:
        start = (today.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    start = max(start, dt.date.fromisoformat(START_DATE).replace(day=1))

    house_ids = {m["bioguide"] for m in load_members()}
    records = store.load_all()
    now = now_dt.isoformat()
    total_added = total_updated = 0
    for year, month in months_between(start, today):
        added, updated = import_month(records, house_ids, year, month, now)
        print(f"import_archive: {year}-{month:02d}: {added} added, {updated} texts filled")
        total_added += added
        total_updated += updated

    store.save_all(records)
    state["archive_imported_at"] = now
    store.save_state(state)
    print(f"import_archive: {total_added} added, {total_updated} texts filled")


if __name__ == "__main__":
    main()
