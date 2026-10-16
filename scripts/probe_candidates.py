"""Probe candidate newsrooms for more federal agencies (run in CI). Writes probe/candidates/index.json.

For each URL: status and size with a plain client and with Chrome impersonation, RSS/Atom links
and a WordPress API link found on the page, and for feeds the item count and date range.
"""
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as creq

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from house_statements import fallback  # noqa: E402

UA = "Mozilla/5.0 (compatible; house-statements/1.0; +https://github.com/4jackryan4/house-statements)"
C = {}
exec((Path(__file__).parent / "agency_candidates.txt").read_text(), C)
CANDIDATES = C["CANDIDATES"]
out = Path("probe/candidates")
out.mkdir(parents=True, exist_ok=True)


def fetch(url, client):
    try:
        if client == "plain":
            r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
        else:
            r = creq.get(url, impersonate="chrome", timeout=25)
        return r.status_code, r.text, str(r.url)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"[:200], url


def summarize(url, body):
    info = {}
    head = body.lstrip()[:400].lower()
    if head.startswith("<?xml") or "<rss" in head or "<feed" in head:
        items = fallback.from_rss(body, url)
        dates = sorted(i["date"].isoformat() for i in items if i.get("date"))
        info["feed"] = {"items": len(items), "dates": [dates[0], dates[-1]] if dates else None,
                        "sample": [(i["url"], i["title"][:70]) for i in items[:3]]}
        return info
    if head.startswith("[") or head.startswith("{"):
        info["json"] = body[:300]
        return info
    soup = BeautifulSoup(body, "lxml")
    info["title"] = (soup.title.get_text(" ", strip=True) if soup.title else "")[:80]
    info["feeds"] = sorted({urljoin(url, l.get("href")) for l in soup.find_all("link", type=re.compile("rss|atom"))
                            if l.get("href")})[:6]
    info["feeds"] += sorted({urljoin(url, a["href"]) for a in soup.find_all("a", href=True)
                             if re.search(r"rss|\.xml|/feed/?$", a["href"], re.I)})[:6]
    api = soup.find("link", rel="https://api.w.org/")
    info["wp"] = api.get("href") if api else None
    host = urlsplit(url).netloc
    links = []
    for a in soup.find_all("a", href=True):
        h = urljoin(url, a["href"].strip())
        if urlsplit(h).netloc == host and len(urlsplit(h).path) > 25:
            links.append(urlsplit(h).path)
    info["links"] = len(links)
    info["sample_links"] = list(dict.fromkeys(links))[10:18]
    info["next"] = fallback.next_page_url(body, url)
    return info


def probe(entry):
    slug, urls = entry[0], entry[3]
    rows = []
    for url in urls:
        row = {"slug": slug, "url": url}
        best = None
        for client in ("plain", "chrome"):
            status, body, final = fetch(url, client)
            row[client] = [status, len(body)]
            if status == 200 and best is None:
                best = (client, body, final)
        if best:
            row["ok_with"] = best[0]
            row["final"] = best[2] if best[2] != url else None
            row |= summarize(best[2], best[1])
            # Follow discovered feeds once.
            for f in [f for f in row.get("feeds", []) if f not in urls][:3]:
                st, b, _ = fetch(f, best[0])
                row.setdefault("found_feeds", []).append({"url": f, "status": st, **(summarize(f, b) if st == 200 else {})})
        else:
            row["error"] = body[:150]
        rows.append(row)
    return rows


with ThreadPoolExecutor(12) as pool:
    results = [r for rows in pool.map(probe, CANDIDATES) for r in rows]
(out / "index.json").write_text(json.dumps(results, indent=1))
print(len(results), "urls;", sum(1 for r in results if r.get("ok_with")), "ok")
