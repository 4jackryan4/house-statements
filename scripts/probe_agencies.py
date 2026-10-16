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
https://www.justice.gov/opa/press-releases
https://www.justice.gov/opa/pr
https://www.justice.gov/news/press-releases?page=2
https://www.justice.gov/api/v1/press_releases.json?pagesize=3&parameters[component]=Office%20of%20Public%20Affairs
https://www.justice.gov/api/v1/press_releases.json?pagesize=3&page=9000
https://home.treasury.gov/news/press-releases?page=2
https://home.treasury.gov/news/press-releases?page=150
https://www.war.gov/News/Releases/?Page=2
https://www.war.gov/News/Releases/?Page=40
https://www.hud.gov/news?page=1
https://legacy.sba.gov/about-sba/sba-newsroom
https://legacy.sba.gov/about-sba/sba-newsroom/press-releases-media-advisories
https://www.sba.gov/sba-newsroom/feed/?post_type=news-release
https://www.sba.gov/wp-json/wp/v2/search?subtype=any&per_page=5&search=announces
https://www.state.gov/wp-json/wp/v2/state_press_release?page=200
https://www.dhs.gov/news-releases/press-releases?page=60
https://www.ed.gov/about/news/press-release?page=40
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
