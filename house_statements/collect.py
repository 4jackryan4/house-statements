"""Scrape the latest statements from every House member's website.

Uses dwillis/python-statement, which knows the press-release page layout for each member site,
then fetches the full text of anything new. Writes a per-member health report so broken
scrapers (usually after a site redesign) are easy to spot.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from . import store
from .config import HEALTH_FILE, START_DATE
from .members import load_sources
from .textfetch import fetch_page


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


STALE_DAYS = 30
FALLBACK = os.environ.get("FALLBACK_SCRAPER", "1") == "1"


def scrape_member(
    member: dict, sweep: bool = False, pages: int = 1, known: list[str] | None = None
) -> tuple[list[dict], str | None, str]:
    """Return (items, error, method) for one member. Items have url, title, date (date or None).

    Uses the member's python-statement scraper. The generic scraper in fallback.py also runs when
    there is no scraper, it returns nothing or only old items (new members, redesigned sites), or
    on a sweep, which checks every site for statements the scrapers miss.
    """
    from python_statement import Feed, Scraper

    from .fallback import scrape_site

    items, error = [], None
    try:
        if member.get("scraper"):
            items = Scraper.run_scraper(member["scraper"], 1) or []
        elif member.get("rss_url"):
            items = Feed.from_rss(member["rss_url"]) or []
    except Exception as e:  # one broken site must not stop the run
        error = f"{type(e).__name__}: {e}"
    items = [i for i in items if i and i.get("url") and i.get("title")]
    newest = max((iso(i.get("date")) for i in items if i.get("date")), default=None)
    stale_before = (dt.date.today() - dt.timedelta(days=STALE_DAYS)).isoformat()
    fresh = bool(items and newest and newest >= stale_before)
    if not FALLBACK or not member.get("url") or (fresh and not sweep and pages <= 1):
        if items:
            return items, None, "python-statement"
        return [], error or "no statements found on the site", "none"

    try:
        extra = scrape_site(member["url"], member.get("rss_url"), pages=pages, known=known, press_only=fresh)
    except Exception as e:
        extra = []
        error = error or f"fallback {type(e).__name__}: {e}"
    if items and extra:
        return items + extra, None, "python-statement+fallback"
    if items:
        return items, None, "python-statement"
    if extra:
        return extra, None, "fallback"
    return [], error or "no statements found on the site", "none"


def iso(d) -> str | None:
    if d is None:
        return None
    if isinstance(d, (dt.date, dt.datetime)):
        return d.strftime("%Y-%m-%d")
    return str(d)[:10] or None


def _title_key(title: str) -> str:
    return re.sub(r"\W+", " ", (title or "").lower()).strip()


def _nearby(date: str, days: int = 3) -> list[str]:
    d = dt.date.fromisoformat(date)
    return [(d + dt.timedelta(days=k)).isoformat() for k in range(-days, days + 1)]


def merge_items(records: dict, member: dict, items: list[dict], now: str) -> list[str]:
    """Add unseen items to records; return ids of the new records."""
    new_ids = []
    tomorrow = (dt.date.fromisoformat(now[:10]) + dt.timedelta(days=1)).isoformat()
    # The same release is often reachable at two URLs (e.g. /2026/7/slug and /media/press-releases/slug).
    seen_titles = {
        (_title_key(r["title"]), r.get("date")) for r in records.values() if r["bioguide"] == member["bioguide"]
    }
    for item in items:
        url = item["url"].strip()
        if url.rstrip("/") == str(item.get("source", "")).rstrip("/"):
            continue  # listing page, not a statement
        sid = store.statement_id(url)
        date = iso(item.get("date"))
        if date and date > tomorrow:
            date = None  # a misread listing date; the page itself or first-seen date is used instead
        if sid in records:
            if date and not records[sid].get("date"):
                records[sid]["date"] = date
            continue
        if date and date < START_DATE:
            continue
        key = _title_key(item["title"])
        if date and any((key, d) in seen_titles for d in _nearby(date)):
            continue
        seen_titles.add((key, date))
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
        if item.get("origin"):
            records[sid]["origin"] = item["origin"]
        if item.get("title_check"):
            records[sid]["title_check"] = True
        new_ids.append(sid)
    return new_ids


def _seed(source: dict) -> list[str] | None:
    """Committee sites start from their known press release listing."""
    return [source["press_url"]] if source.get("press_url") else None


def fill_text(records: dict, ids: list[str], workers: int) -> int:
    session = requests.Session()
    filled = 0

    def work(sid):
        return sid, fetch_page(records[sid]["url"], session)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(work, sid) for sid in ids]):
            sid, page = fut.result()
            text, page_date = page["text"], page["date"]
            r = records[sid]
            if r.get("title_check") and page["title"]:
                if len(page["title"].split()) > len(r["title"].split()):
                    r["title"] = page["title"]
                del r["title_check"]
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


def count_http(counter: Counter) -> None:
    """Tally the HTTP status of every request this process makes (python-statement swallows errors)."""
    send = requests.Session.send

    def counted(self, request, **kwargs):
        try:
            response = send(self, request, **kwargs)
        except requests.RequestException as e:
            counter[type(e).__name__] += 1
            raise
        counter[str(response.status_code)] += 1
        return response

    requests.Session.send = counted


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=12, help="members scraped in parallel")
    parser.add_argument("--max-text", type=int, default=600, help="max pages to fetch text for this run")
    parser.add_argument("--only", nargs="*", help="limit to these bioguide ids (\"committees\" for all committees)")
    parser.add_argument("--sweep", action="store_true", help="also check every site with the generic scraper")
    parser.add_argument("--pages", type=int, default=1, help="listing pages to read per site (backfill)")
    parser.add_argument("--report", help="dry run: write what would be added to this JSON file, save nothing")
    args = parser.parse_args(argv)

    members = load_sources()
    if args.only:
        only = set(args.only)
        members = [m for m in members if m["bioguide"] in only or ("committees" in only and m.get("kind") == "committee")]
    records = store.load_all()
    state = store.load_state()
    known = state.setdefault("fallback_sources", {})
    now = utcnow()
    http = Counter()
    count_http(http)

    health, new_ids = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(scrape_member, m, args.sweep, args.pages, known.get(m["bioguide"]) or _seed(m)): m
            for m in members
        }
        for fut in as_completed(futures):
            m = futures[fut]
            items, error, method = fut.result()
            origins = sorted({i["origin"] for i in items if i.get("origin")})
            if origins:
                known[m["bioguide"]] = origins
            added = merge_items(records, m, items, now)
            new_ids.extend(added)
            dates = [iso(i.get("date")) for i in items if i.get("date")]
            health.append({
                "bioguide": m["bioguide"],
                "label": m["label"],
                "scraper": m.get("scraper"),
                "method": method,
                "status": "error" if error else ("ok" if items else "empty"),
                "error": error,
                "count": len(items),
                "new": len(added),
                "latest_date": max(dates) if dates else None,
            })

    counts = {s: sum(1 for h in health if h["status"] == s) for s in ("ok", "empty", "error")}
    http_summary = dict(sorted(http.items()))
    print(f"collect: sites {counts}; HTTP responses {http_summary}")
    if args.report:
        report = {
            "http": http_summary,
            "health": sorted(health, key=lambda h: h["label"]),
            "sources": {b: known[b] for b in sorted(known)},
            "new": [
                {k: records[sid].get(k) for k in ("bioguide", "date", "title", "url", "origin")} for sid in new_ids
            ],
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "w") as f:
            json.dump(report, f, indent=1)
        print(f"collect: dry run, {len(new_ids)} statements would be added; report in {args.report}")
        return

    # Text for new statements first, then retry earlier misses.
    retry = [sid for sid, r in records.items() if not r.get("text") and r.get("text_attempts", 0) < 3 and sid not in new_ids]
    todo = (new_ids + retry)[: args.max_text]
    filled = fill_text(records, todo, workers=8) if todo else 0

    # Repair any future-dated statement left by an earlier misread listing.
    tomorrow = (dt.date.fromisoformat(now[:10]) + dt.timedelta(days=1)).isoformat()
    for r in records.values():
        if r.get("date") and r["date"] > tomorrow:
            r["date"], r["date_source"] = r["first_seen"][:10], "first_seen"

    store.save_all(records)
    state["last_collect"] = {"at": now, "sites": counts, "http": http_summary}
    store.save_state(state)
    health.sort(key=lambda h: (h["status"] == "ok", h["label"]))
    HEALTH_FILE.write_text(json.dumps(health, indent=1) + "\n")

    print(f"collect: {len(new_ids)} new statements, text fetched for {filled}/{len(todo)}")
    if members and counts["ok"] == 0:
        print("collect: no member site returned anything; the runner may be blocked", file=sys.stderr)


if __name__ == "__main__":
    main()
