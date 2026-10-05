"""Build data/members.json: every current House member with party, district, committees and scraper.

Sources:
  - unitedstates/congress-legislators (members, websites, committee assignments)
  - dwillis/python-statement legislators_with_scrapers.json (which scraper handles each site)
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests

from .config import MEMBERS_FILE, PARTIES, USER_AGENT

LEGISLATORS_BASE = "https://raw.githubusercontent.com/unitedstates/congress-legislators/gh-pages"
COMMITTEE_PREFIX = re.compile(r"^House (Permanent )?(Select )?Committee on (the )?")
SHORT_COMMITTEE_NAMES = {
    "House Select Subcommittee to Investigate the Remaining Questions Surrounding January 6, 2021":
        "Select Subcommittee on January 6",
    "Strategic Competition Between the United States and the Chinese Communist Party": "Select Committee on China",
}
PARTY_LETTER = {"Democrat": "D", "Republican": "R", "Independent": "I"}


def fetch_json(url: str):
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    resp.raise_for_status()
    return resp.json()


def default_scrapers_file() -> Path | None:
    vendor = os.environ.get("PYTHON_STATEMENT_DIR")
    if vendor:
        return Path(vendor) / "legislators_with_scrapers.json"
    return None


def district_label(state: str, district) -> str:
    if district in (None, 0):
        return f"{state}-AL"
    return f"{state}-{int(district):02d}"


def build_members(legislators: list, committees: list, membership: dict, scrapers: list) -> list[dict]:
    house_committees = {
        c["thomas_id"]: SHORT_COMMITTEE_NAMES.get(n, n)
        for c in committees
        if c.get("type") == "house"
        for n in [COMMITTEE_PREFIX.sub("", c["name"])]
    }
    member_committees: dict[str, list[str]] = {}
    for code, roster in membership.items():
        # Full committees only (subcommittee codes are the committee code plus two digits).
        if code not in house_committees:
            continue
        for seat in roster:
            member_committees.setdefault(seat["bioguide"], []).append(house_committees[code])

    scraper_by_bioguide = {s["bioguide"]: s.get("scraper_method") for s in scrapers}

    members = []
    for leg in legislators:
        term = leg["terms"][-1]
        if term["type"] != "rep" or term.get("party") not in PARTIES:
            continue
        bioguide = leg["id"]["bioguide"]
        name = leg["name"].get("official_full") or f"{leg['name']['first']} {leg['name']['last']}"
        party = term.get("party", "")
        district = district_label(term["state"], term.get("district"))
        members.append({
            "bioguide": bioguide,
            "name": name,
            "last_name": leg["name"]["last"],
            "party": party,
            "party_letter": PARTY_LETTER.get(party, party[:1]),
            "state": term["state"],
            "district": district,
            "label": f"{name} ({PARTY_LETTER.get(party, party[:1])}-{district})",
            "url": term.get("url"),
            "rss_url": term.get("rss_url"),
            "committees": sorted(member_committees.get(bioguide, [])),
            "scraper": scraper_by_bioguide.get(bioguide),
        })
    members.sort(key=lambda m: (m["state"], m["district"], m["last_name"]))
    return members


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scrapers-file", type=Path, default=default_scrapers_file())
    args = parser.parse_args(argv)

    legislators = fetch_json(f"{LEGISLATORS_BASE}/legislators-current.json")
    committees = fetch_json(f"{LEGISLATORS_BASE}/committees-current.json")
    membership = fetch_json(f"{LEGISLATORS_BASE}/committee-membership-current.json")
    scrapers = []
    if args.scrapers_file and args.scrapers_file.exists():
        scrapers = json.loads(args.scrapers_file.read_text())
    else:
        print("warning: no python-statement scraper map found; members will have no scraper", file=sys.stderr)

    members = build_members(legislators, committees, membership, scrapers)
    MEMBERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMBERS_FILE.write_text(json.dumps(members, indent=1, ensure_ascii=False) + "\n")
    with_scraper = sum(1 for m in members if m["scraper"])
    print(f"members: {len(members)} House members, {with_scraper} with a scraper")


def load_members() -> list[dict]:
    return json.loads(MEMBERS_FILE.read_text())


def load_sources() -> list[dict]:
    """Members plus the committees' Democratic sites (see committees.py)."""
    from .committees import load_committees

    return load_members() + load_committees()


if __name__ == "__main__":
    main()
