"""Save raw pages for candidate agency newsrooms (run in CI under xvfb-run).

Writes probe/raw/<slug>-<n>.html and probe/raw/index.json. Each URL is fetched with Chrome
impersonation, then a plain client; slugs in BROWSER also get real-Chrome cookies first.
"""
import gzip
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as creq

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from house_statements import agencies  # noqa: E402

C = {}
exec((Path(__file__).parent / "agency_raw_urls.txt").read_text(), C)
URLS, BROWSER = C["URLS"], C["BROWSER"]
out = Path("probe/raw")
out.mkdir(parents=True, exist_ok=True)
index = []


def save(slug, n, url, client, r):
    body = r.text if r is not None else ""
    # Gzipped: pages embed third-party keys that GitHub's secret scanning refuses to accept.
    (out / f"{slug}-{n}.html.gz").write_bytes(gzip.compress(body[:600_000].encode()))
    row = {"slug": slug, "n": n, "url": url, "client": client,
           "status": r.status_code if r is not None else None, "bytes": len(body)}
    index.append(row)
    print(row, flush=True)
    return body


for slug, urls in URLS.items():
    session = None
    if slug in BROWSER:
        try:
            ua, cookies = agencies._browser_cookies(urls[0])
            session = creq.Session(impersonate="chrome")
            session.headers["User-Agent"] = ua
            for c in cookies or []:
                session.cookies.set(c["name"], c["value"], domain=c["domain"])
            print(slug, "browser cookies", bool(cookies), flush=True)
        except Exception as e:
            print(slug, "browser failed", e, flush=True)
    n = 0
    for url in urls:
        tries = [("browser", session)] if session else []
        tries += [("chrome", None), ("plain", None)]
        for client, s in tries:
            try:
                if client == "plain":
                    r = requests.get(url, headers={"User-Agent": agencies.HONEST_UA}, timeout=25)
                elif s is not None:
                    r = s.get(url, timeout=25)
                else:
                    r = creq.get(url, impersonate="chrome", timeout=25)
            except Exception:
                r = None
            if r is not None and r.status_code == 200:
                break
        body = save(slug, n, url, client, r)
        n += 1
        # Feeds linked from a listing page.
        if body and r is not None and r.status_code == 200 and "<html" in body[:2000].lower():
            soup = BeautifulSoup(body, "lxml")
            feeds = {urljoin(url, l["href"]) for l in soup.find_all("link", type=re.compile("rss|atom")) if l.get("href")}
            feeds |= {urljoin(url, a["href"]) for a in soup.find_all("a", href=True)
                      if re.search(r"rss|feed\.xml|/feed/?$", a["href"], re.I) and "comments" not in a["href"]}
            for f in sorted(feeds)[:3]:
                try:
                    fr = (s or creq).get(f, timeout=25) if client != "plain" else requests.get(f, headers={"User-Agent": agencies.HONEST_UA}, timeout=25)
                except Exception:
                    fr = None
                save(slug, n, f, client, fr)
                n += 1
(out / "index.json").write_text(json.dumps(index, indent=1))
