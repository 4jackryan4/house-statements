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

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from house_statements.config import USER_AGENT  # noqa: E402

CANDIDATES = """
https://www.state.gov/
https://www.state.gov/press-releases/
https://www.state.gov/rss-feeds/
https://www.state.gov/rss-feed/press-releases/feed/
https://home.treasury.gov/news/press-releases
https://home.treasury.gov/rss
https://www.war.gov/News/Releases/
https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=945&max=10
https://www.defense.gov/News/Releases/
https://www.justice.gov/news
https://www.justice.gov/news/press-releases
https://www.justice.gov/news/rss?type=press_release&m=1
https://www.justice.gov/feeds/opa/justice-news.xml
https://www.doi.gov/news
https://www.doi.gov/pressreleases
https://www.doi.gov/rss
https://www.usda.gov/about-usda/news/press-releases
https://www.usda.gov/rss/latest-releases.xml
https://www.commerce.gov/news/press-releases
https://www.commerce.gov/feeds/news
https://www.dol.gov/newsroom/releases
https://www.dol.gov/rss/releases.xml
https://www.hhs.gov/press-room/index.html
https://www.hhs.gov/rss/news.xml
https://www.hud.gov/news
https://www.hud.gov/press/press_releases_media_advisories
https://www.transportation.gov/newsroom/press-releases
https://www.transportation.gov/rss
https://www.energy.gov/newsroom
https://www.energy.gov/rss
https://www.ed.gov/about/news/press-release
https://www.ed.gov/feed
https://news.va.gov/press-room/
https://news.va.gov/press-room/feed/
https://www.va.gov/opa/pressrel/
https://www.dhs.gov/news-releases/press-releases
https://www.dhs.gov/rss
https://www.epa.gov/newsreleases/search
https://www.epa.gov/newsreleases/search/rss
https://www.sba.gov/newsroom/press-releases
https://www.sba.gov/rss
https://www.whitehouse.gov/omb/
https://www.whitehouse.gov/omb/briefing-room/
https://ustr.gov/about-us/policy-offices/press-office/press-releases
https://ustr.gov/rss.xml
https://www.dni.gov/index.php/newsroom/press-releases
https://www.dni.gov/index.php/newsroom/press-releases?format=feed&type=rss
https://www.cia.gov/stories/
https://www.cia.gov/about/news/
https://usun.usmission.gov/
https://usun.usmission.gov/feed/
https://www.whitehouse.gov/news/
https://www.whitehouse.gov/feed/
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
    for label, ua in (("bot", USER_AGENT), ("browser", BROWSER_UA)):
        try:
            r = requests.get(url, headers={"User-Agent": ua}, timeout=30)
            status, final, body = r.status_code, r.url, r.text
        except requests.RequestException as e:
            status, final, body = None, url, str(e)
        row[label] = {"status": status, "final_url": final, "bytes": len(body)}
        if label == "bot" and status == 200:
            break
    (out / f"{n}.html").write_text(body)
    row["feeds"] = feed_links(body, final) if body.lstrip()[:5] != "<?xml" else []
    index.append(row)
    print(n, row.get("bot", {}).get("status"), row.get("browser", {}).get("status"), len(body), url)
(out / "index.json").write_text(json.dumps(index, indent=1))
