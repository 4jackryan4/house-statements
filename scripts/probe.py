"""Save raw HTML of member sites for debugging scrapers (run in CI, where house.gov is reachable).

Usage: python scripts/probe.py BIOGUIDE [BIOGUIDE ...]   (default: members not "ok" in data/health.json)
Writes probe/<bioguide>/<n>.html plus probe/index.json.
"""
import json
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from house_statements.config import HEALTH_FILE, USER_AGENT  # noqa: E402
from house_statements.members import load_sources  # noqa: E402

PATHS = ["", "rss.xml", "news/rss.aspx", "feed/", "media/press-releases", "news/press-releases", "press-releases",
         "news", "media", "media-center/press-releases", "newsroom/press-releases", "press",
         "news/documentquery.aspx?DocumentTypeID=27"]

ids = sys.argv[1:] or [h["bioguide"] for h in json.loads(HEALTH_FILE.read_text()) if h["status"] != "ok"]
members = {m["bioguide"]: m for m in load_sources()}
out = Path("probe")
index = []
for bid in ids:
    m = members[bid]
    base = (m.get("url") or "").rstrip("/") + "/"
    (out / bid).mkdir(parents=True, exist_ok=True)
    for n, path in enumerate(PATHS):
        url = urljoin(base, path)
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            status, final, body = r.status_code, r.url, r.text
        except requests.RequestException as e:
            status, final, body = None, url, str(e)
        (out / bid / f"{n}.html").write_text(body)
        index.append({"bioguide": bid, "n": n, "url": url, "final_url": final, "status": status, "bytes": len(body)})
        print(bid, status, len(body), url, "->", final)
(out / "index.json").write_text(json.dumps(index, indent=1))
