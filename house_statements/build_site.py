"""Build the static website into site/.

Writes the page shells and assets, the JSON the pages load (events, latest statements, members),
RSS feeds, and build/records.jsonl, which scripts/build_index.mjs turns into the Pagefind
full-text search index.
"""

import datetime as dt
import json
import os
import shutil
from email.utils import format_datetime
from xml.sax.saxutils import escape

from . import store
from .config import ROOT, SITE_DIR, WEB_DIR
from .events import find_events
from .members import load_members

SITE_URL = os.environ.get("SITE_URL", "https://4jackryan4.github.io/house-statements/").rstrip("/") + "/"
RECORDS_FILE = ROOT / "build" / "records.jsonl"
RECENT_EVENT_DAYS = 45
LATEST_COUNT = 300
FEED_COUNT = 50


def when_buckets(date: str, today: dt.date) -> list[str]:
    age = (today - dt.date.fromisoformat(date)).days
    out = []
    for label, days in (("Past 3 days", 3), ("Past week", 7), ("Past month", 31), ("Past 3 months", 92)):
        if age <= days:
            out.append(label)
    out.append(date[:4])
    return out


def short(r: dict, event_by_statement: dict) -> dict:
    ev = event_by_statement.get(r["id"])
    return {
        "id": r["id"], "title": r["title"], "date": r["date"], "url": r["url"], "member": r["bioguide"],
        **({"event": ev["id"], "eventLabel": ev["label"], "eventMembers": ev["member_count"]} if ev else {}),
    }


def rss(path, title: str, link: str, description: str, items: list[dict]) -> None:
    """items: dicts with title, link, guid, date (YYYY-MM-DD), description."""
    def rfc822(d):
        return format_datetime(dt.datetime.fromisoformat(d).replace(hour=12, tzinfo=dt.timezone.utc))

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>',
        f"<title>{escape(title)}</title><link>{escape(link)}</link><description>{escape(description)}</description>",
        f'<atom:link href="{escape(SITE_URL + str(path.relative_to(SITE_DIR)))}" rel="self" type="application/rss+xml"/>',
    ]
    for it in items:
        parts.append(
            f"<item><title>{escape(it['title'])}</title><link>{escape(it['link'])}</link>"
            f'<guid isPermaLink="false">{escape(it["guid"])}</guid><pubDate>{rfc822(it["date"])}</pubDate>'
            f"<description>{escape(it['description'])}</description></item>"
        )
    parts.append("</channel></rss>\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(parts))


def main():
    today = dt.datetime.now(dt.timezone.utc).date()
    members = load_members()
    by_bioguide = {m["bioguide"]: m for m in members}
    records = [r for r in store.load_all().values() if r.get("date") and r["bioguide"] in by_bioguide]
    records.sort(key=lambda r: (r["date"], r.get("first_seen", ""), r["id"]), reverse=True)

    events = find_events(records, members)
    event_by_statement = {sid: e for e in events for sid in e["statement_ids"]}
    by_id = {r["id"]: r for r in records}

    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
    (SITE_DIR / "data").mkdir(parents=True)
    for f in WEB_DIR.iterdir():
        if f.is_file():
            shutil.copy(f, SITE_DIR / f.name)

    # ---- data the pages load
    counts_90 = {}
    cutoff_90 = (today - dt.timedelta(days=90)).isoformat()
    for r in records:
        if r["date"] >= cutoff_90:
            counts_90[r["bioguide"]] = counts_90.get(r["bioguide"], 0) + 1
    member_rows = [
        {k: m[k] for k in ("bioguide", "name", "label", "state", "district", "url", "committees")}
        | {"recent": counts_90.get(m["bioguide"], 0)}
        for m in members
    ]

    def event_json(e, with_statements=True):
        out = {k: e[k] for k in ("id", "label", "headline", "member_count", "statement_count", "first", "last")}
        if with_statements:
            rows = sorted((by_id[s] for s in e["statement_ids"]), key=lambda r: (r["date"], r["id"]), reverse=True)
            out["statements"] = [short(r, {}) for r in rows]
        return out

    recent_cutoff = (today - dt.timedelta(days=RECENT_EVENT_DAYS)).isoformat()
    write_json = lambda name, obj: (SITE_DIR / "data" / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    write_json("members.json", member_rows)
    write_json("events-recent.json", [event_json(e) for e in events if e["last"] >= recent_cutoff])
    write_json("events.json", [event_json(e) for e in events])
    write_json("latest.json", [short(r, event_by_statement) for r in records[:LATEST_COUNT]])
    write_json("build.json", {"built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
                              "statements": len(records), "events": len(events), "members": len(members)})

    # ---- RSS
    def statement_item(r):
        m = by_bioguide[r["bioguide"]]
        ev = event_by_statement.get(r["id"])
        desc = f"{m['label']}, {r['date']}." + (f" Part of: {ev['label']} ({ev['member_count']} members)." if ev else "")
        return {"title": f"{m['name']}: {r['title']}", "link": r["url"], "guid": r["id"], "date": r["date"], "description": desc}

    rss(SITE_DIR / "feeds" / "all.xml", "House Democrats: all statements", SITE_URL,
        "Latest press statements from House Democrats' official websites.",
        [statement_item(r) for r in records[:100]])
    newest_events = sorted(events, key=lambda e: (e["first"], e["member_count"]), reverse=True)[:FEED_COUNT]
    rss(SITE_DIR / "feeds" / "events.xml", "House Democrats: events", SITE_URL + "events.html",
        "A new item each time several House Democrats put out statements on the same event.",
        [{"title": f"{e['label']} ({e['member_count']} members)", "link": f"{SITE_URL}events.html#{e['id']}",
          "guid": f"event-{e['id']}", "date": e["first"], "description": e["headline"]} for e in newest_events])
    per_member = {}
    for r in records:
        per_member.setdefault(r["bioguide"], []).append(r)
    # Per-member statement lists, so picking a member (with no search words) is instant.
    (SITE_DIR / "data" / "members").mkdir()
    for m in members:
        write_json(f"members/{m['bioguide']}.json", [short(r, event_by_statement) for r in per_member.get(m["bioguide"], [])])
    for m in members:
        rows = per_member.get(m["bioguide"], [])[:FEED_COUNT]
        rss(SITE_DIR / "feeds" / "members" / f"{m['bioguide']}.xml", f"{m['label']}: statements",
            m.get("url") or SITE_URL, f"Press statements from {m['name']}.", [statement_item(r) for r in rows])

    # ---- search records for Pagefind
    RECORDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with RECORDS_FILE.open("w") as f:
        for r in records:
            m = by_bioguide[r["bioguide"]]
            ev = event_by_statement.get(r["id"])
            meta = {"title": r["title"], "member": m["label"], "bioguide": m["bioguide"], "date": r["date"]}
            if ev:
                meta |= {"event": ev["label"], "event_id": ev["id"], "event_members": str(ev["member_count"])}
            f.write(json.dumps({
                "url": r["url"],
                "content": f"{r['title']}\n\n{r.get('text') or ''}",
                "language": "en",
                "meta": meta,
                "filters": {"member": [m["label"]], "state": [m["state"]], "when": when_buckets(r["date"], today),
                            "committee": m["committees"] or ["(none)"]},
                "sort": {"date": r["date"]},
            }, ensure_ascii=False) + "\n")

    print(f"build_site: {len(records)} statements, {len(events)} events, {len(members)} members -> {SITE_DIR}")


if __name__ == "__main__":
    main()
