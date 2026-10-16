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
https://www.state.gov/wp-json/wp/v2/types
https://www.state.gov/wp-json/wp/v2/state_release?per_page=3
https://usun.usmission.gov/wp-json/wp/v2/types
https://www.whitehouse.gov/wp-json/wp/v2/types
https://www.whitehouse.gov/omb/news/
https://www.odni.gov/wp-json/wp/v2/types
https://news.va.gov/wp-json/wp/v2/types
https://www.sba.gov/wp-json/wp/v2/types
https://www.sba.gov/wp-json/wp/v2/categories?per_page=100
https://www.sba.gov/article/2026/03/31/sba-announces-new-made-america-loan-guarantee-restore-manufacturing-dominance
https://www.cia.gov/wp-json/wp/v2/types
https://www.cia.gov/stories/press-releases-and-statements/
https://www.justice.gov/api/v1/press_releases.json?pagesize=2&sort=date&direction=DESC
https://www.justice.gov/opa/pr/russian-national-arrested-and-extradited-new-york-charges-evading-sanctions-russia
https://www.justice.gov/news/rss?type=press_release&m=1&page=5
https://home.treasury.gov/news/press-releases?page=1
https://home.treasury.gov/news/press-releases/sb0661
https://www.dhs.gov/news-releases/press-releases?page=1
https://www.dhs.gov/news/2026/10/15/ice-arrests-five-time-deported-felon-during-traffic-stop-california
https://www.hud.gov/news/hud-no-26-085
https://www.doi.gov/news?page=1
https://www.doi.gov/feeds/content/36980/rss.xml
https://www.doi.gov/pressreleases/record-24m-winning-bids-utah-geothermal-leases
https://www.energy.gov/listings/energy-news
https://www.energy.gov/listings/energy-news?page=1
https://www.energy.gov/articles/energy-department-backs-major-grid-expansion-across-eight-states-strengthen-reliability
https://ustr.gov/about-us/policy-offices/press-office/press-releases/2025-0
https://ustr.gov/about/policy-offices/press-office/press-releases/2026
https://ustr.gov/about/policy-offices/press-office/press-releases/2026/october/ambassador-greer-tour-utahs-advanced-manufacturing-plants
https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=945&max=500
https://www.war.gov/News/Releases/Release/Article/4626961/readout-of-secretary-of-war-pete-hegseths-meeting-with-the-united-kingdoms-secr/
http://www.dol.gov/newsroom/releases/osha/osha20261013
https://www.dol.gov/rss/releases.xml?page=2
https://www.hhs.gov/rss/press-releases.xml
https://www.hhs.gov/press-room/rss.xml
https://www.usda.gov/rss/home.xml
https://www.transportation.gov/rss.xml
https://www.epa.gov/rss/epa-news.xml
https://content.govdelivery.com/accounts/USDA/bulletins
https://content.govdelivery.com/accounts/USDA/bulletins.rss
https://content.govdelivery.com/accounts/USDOD/bulletins
https://content.govdelivery.com/accounts/USDOC/bulletins
https://content.govdelivery.com/accounts/USDOL/bulletins
https://content.govdelivery.com/accounts/USHHS/bulletins
https://content.govdelivery.com/accounts/USDOT/bulletins
https://content.govdelivery.com/accounts/USED/bulletins
https://content.govdelivery.com/accounts/USAEPA/bulletins
https://content.govdelivery.com/accounts/USDOJ/bulletins
https://content.govdelivery.com/accounts/USTREAS/bulletins
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
