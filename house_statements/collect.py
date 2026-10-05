"""Scrape the latest statements from every House member's website.

Uses dwillis/python-statement, which knows the press-release page layout for each member site,
then fetches the full text of anything new. Writes a per-member health report so broken
scrapers (usually after a site redesign) are easy to spot.
"""

import argparse
import datetime as dt
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from . import store
from .config import HEALTH_FILE, START_DATE
from .members import load_members
from .textfetch import fetch_text


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def scrape_member(member: dict) -> tuple[list[dict], str | None]:
    """Return (items, error) for one member. Items have url, title, date (date or None)."""
    from python_statement import Feed, Scraper

    try:
        if member.get("scraper"):
            items = Scraper.run_scraper(member["scraper"], 1)
        elif member.get("rss_url"):
            items = Feed.from_rss(member["rss_url"])
        else:
            return [], "no scraper or RSS feed for this site"
    except Exception as e:  # one broken site must not stop the run
        return [], f"{type(e).__name__}: {e}"
    return [i for i in (items or []) if i and i.get("url") and i.get("title")], None


def iso(d) -> str | None:
    if d is None:
        return None
    if isinstance(d, (dt.date, dt.datetime)):
        return d.strftime("%Y-%m-%d")
    return str(d)[:10] or None


def merge_items(records: dict, member: dict, items: list[dict], now: str) -> list[str]:
    """Add unseen items to records; return ids of the new records."""
    new_ids = []
    for item in items:
        url = item["url"].strip()
        if url.rstrip("/") == str(item.get("source", "")).rstrip("/"):
            continue  # listing page, not a statement
        sid = store.statement_id(url)
        date = iso(item.get("date"))
        if sid in records:
            if date and not records[sid].get("date"):
                records[sid]["date"] = date
            continue
        if date and date < START_DATE:
            continue
        records[sid] = {
            "id": sid,
            "url": url,
            "title": " ".join(item["title"].split()),
            "date": date,
            "date_source": "listing" if date else None,
            "bioguide": member["bioguide"],
            "source": "member-site",
            "first_seen": now,
            "text": None,
            "text_attempts": 0,
        }
        new_ids.append(sid)
    return new_ids


def fill_text(records: dict, ids: list[str], workers: int) -> int:
    session = requests.Session()
    filled = 0

    def work(sid):
        return sid, fetch_text(records[sid]["url"], session)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(work, sid) for sid in ids]):
            sid, (text, page_date) = fut.result()
            r = records[sid]
            r["text_attempts"] = r.get("text_attempts", 0) + 1
            if text:
                r["text"] = text
                filled += 1
            if not r.get("date"):
                if page_date:
                    r["date"], r["date_source"] = page_date, "page"
                elif r["text_attempts"] >= 3:
                    r["date"], r["date_source"] = r["first_seen"][:10], "first_seen"
    return filled


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=12, help="members scraped in parallel")
    parser.add_argument("--max-text", type=int, default=600, help="max pages to fetch text for this run")
    parser.add_argument("--only", nargs="*", help="limit to these bioguide ids (for testing)")
    args = parser.parse_args(argv)

    members = load_members()
    if args.only:
        members = [m for m in members if m["bioguide"] in set(args.only)]
    records = store.load_all()
    now = utcnow()

    health, new_ids = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(scrape_member, m): m for m in members}
        for fut in as_completed(futures):
            m = futures[fut]
            items, error = fut.result()
            added = merge_items(records, m, items, now)
            new_ids.extend(added)
            dates = [iso(i.get("date")) for i in items if i.get("date")]
            health.append({
                "bioguide": m["bioguide"],
                "label": m["label"],
                "scraper": m.get("scraper"),
                "status": "error" if error else ("ok" if items else "empty"),
                "error": error,
                "count": len(items),
                "new": len(added),
                "latest_date": max(dates) if dates else None,
            })

    # Text for new statements first, then retry earlier misses.
    retry = [sid for sid, r in records.items() if not r.get("text") and r.get("text_attempts", 0) < 3 and sid not in new_ids]
    todo = (new_ids + retry)[: args.max_text]
    filled = fill_text(records, todo, workers=8) if todo else 0

    store.save_all(records)
    health.sort(key=lambda h: (h["status"] == "ok", h["label"]))
    HEALTH_FILE.write_text(json.dumps(health, indent=1) + "\n")

    counts = {s: sum(1 for h in health if h["status"] == s) for s in ("ok", "empty", "error")}
    print(f"collect: {len(new_ids)} new statements, text fetched for {filled}/{len(todo)}; sites {counts}")
    if members and counts["ok"] == 0:
        print("collect: no member site returned anything; the runner may be blocked", file=sys.stderr)


if __name__ == "__main__":
    main()
