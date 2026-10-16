"""Save raw pages from federal agency newsrooms (run in CI; the dev sandbox can't reach them).

Usage: python scripts/probe_agencies.py [URL ...]   (default: the candidate list below)
Writes probe/agencies/<n>.html plus probe/agencies/index.json with status, size and feed links.
"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

HONEST_UA = "Mozilla/5.0 (compatible; house-statements/1.0; +https://github.com/4jackryan4/house-statements)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from house_statements.config import USER_AGENT  # noqa: E402

CANDIDATES = """
https://www.state.gov/wp-json/wp/v2/state_press_release?per_page=2
https://www.state.gov/wp-json/wp/v2/state_briefing?per_page=1
https://usun.usmission.gov/wp-json/wp/v2/posts?per_page=2
https://usun.usmission.gov/wp-json/wp/v2/categories?per_page=100
https://news.va.gov/wp-json/wp/v2/news-releases?per_page=2
https://www.odni.gov/newsroom/press-releases/
https://www.odni.gov/wp-json/wp/v2/posts?per_page=2
https://www.justice.gov/api/v1/press_releases.json?pagesize=2&parameters[component]=5d1f8b1c-0d0c-4d38-9b5e-3c7c5d5b2f6c
https://www.justice.gov/news/rss?type=press_release&m=1&page=5
https://www.doi.gov/news
https://www.usda.gov/about-usda/news/press-releases
https://www.usda.gov/rss/latest-releases.xml
https://www.commerce.gov/news/press-releases
https://www.commerce.gov/feeds/news
https://www.dol.gov/newsroom/releases/osha/osha20261013
https://www.dol.gov/newsroom/releases
https://www.hhs.gov/press-room/index.html
https://www.hhs.gov/rss/news.xml
https://www.transportation.gov/newsroom/press-releases
https://www.ed.gov/about/news/press-release
https://www.epa.gov/newsreleases/search
https://www.epa.gov/newsreleases/search/rss
https://www.war.gov/News/Releases/Release/Article/4626961/readout-of-secretary-of-war-pete-hegseths-meeting-with-the-united-kingdoms-secr/
https://www.sba.gov/wp-json/wp/v2/types
https://www.whitehouse.gov/wp-json/wp/v2/types
""".split()


def feed_links(html: str, url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    out = [urljoin(url, l["href"]) for l in soup.find_all("link", href=True, type=re.compile("rss|atom", re.I))]
    out += [urljoin(url, a["href"]) for a in soup.find_all("a", href=True)
            if re.search(r"rss|feed|\.xml", a["href"], re.I)]
    return list(dict.fromkeys(out))[:40]


out = Path("probe/agencies")
out.mkdir(parents=True, exist_ok=True)
index = []
for n, url in enumerate(sys.argv[1:] or CANDIDATES):
    row = {"n": n, "url": url}
    best = None
    for label, ua in (("bot", USER_AGENT), ("honest", HONEST_UA), ("browser", BROWSER_UA), ("chrome", None)):
        try:
            if ua is None:
                from curl_cffi import requests as creq
                r = creq.get(url, impersonate="chrome", timeout=30)
            else:
                r = requests.get(url, headers={"User-Agent": ua}, timeout=30)
            status, final, body = r.status_code, str(r.url), r.text
        except Exception as e:
            status, final, body = None, url, str(e)
        row[label] = {"status": status, "final_url": final, "bytes": len(body)}
        if best is None or (status == 200 and best[0] != 200):
            best = (status, final, body)
    status, final, body = best
    (out / f"{n}.html").write_text(body)
    row["feeds"] = feed_links(body, final) if body.lstrip()[:5] != "<?xml" else []
    index.append(row)
    print(n, row.get("bot", {}).get("status"), row.get("browser", {}).get("status"), len(body), url)
(out / "index.json").write_text(json.dumps(index, indent=1))
