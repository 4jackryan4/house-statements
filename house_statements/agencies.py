"""Press releases from cabinet-level federal agencies, collected alongside member sites.

Each agency is a "source" shaped like a member in data/members.json (id "agency-<slug>"), so
storage, search and feeds treat it the same way. Cabinet level means the 15 executive
departments plus the agencies whose heads hold cabinet rank (EPA, SBA, OMB, USTR, ODNI, CIA and
the UN ambassador). Each department's main newsroom only, not its sub-agencies.

Agency newsrooms are not house.gov sites, so they have their own readers here: an RSS feed, a
press release listing (followed to older pages for a backfill), or a WordPress API. Statement
links must match the agency's `pattern`. Several agencies block plain HTTP clients from cloud
servers, so `client` picks how pages are fetched:
  - "plain": requests with an honest browser-compatible User-Agent
  - "chrome": curl_cffi impersonating Chrome's TLS fingerprint (gets past Akamai bot rules)
  - "browser": cookies from a real Chrome window (JavaScript challenges: EPA's AWS WAF, Education's
    Azure WAF), then curl_cffi with those cookies. Needs Chrome and a display (xvfb-run in CI).
Commerce sits behind a Cloudflare challenge that even real Chrome on GitHub's servers doesn't pass;
it is tried every run and shows as "error" in data/health.json until that changes.
"""

import datetime as dt
import html
import re
import threading
import time
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from . import fallback, store
from .config import START_DATE

AGENCY_PREFIX = "agency-"
HONEST_UA = "Mozilla/5.0 (compatible; house-statements/1.0; +https://github.com/4jackryan4/house-statements)"
MIN_INTERVAL = 0.5  # seconds between requests to the same agency
WP_PAGE_SIZE = 100
SHORT_TEXT = 400  # extracted text shorter than this is checked against the page's <article>

# slug, label, homepage, how to read it.
#   feeds: RSS feeds read every run. "{max}" is filled with how many items to ask for.
#   listing: press release listing page, followed to older pages on a backfill.
#   paged_feed: an RSS feed with a page number ("{page}"), read page by page on a backfill.
#   wp: WordPress REST endpoint (posts come with their full text).
#   years: yearly listing pages ("{year}"), this year's read every run, older ones on a backfill.
#   ids: (URL template with "{n}", first, last) ranges of numbered release pages, fetched one by one
#     on a backfill, for newsrooms whose listings can't be paged (Treasury, HUD). Missing numbers 404.
#   paged: listing page URL with a page number ("{page}", counting from `first_page`), read page
#     by page on a backfill (for sites whose "next" links don't work or that block the listing).
AGENCIES = [
    dict(slug="state", label="State Department", url="https://www.state.gov",
         wp="https://www.state.gov/wp-json/wp/v2/state_press_release", pattern=r"^/releases/",
         paged="https://www.state.gov/press-releases/page/{page}/", first_page=1,
         skip_titles=r"^public schedule\b"),
    dict(slug="treasury", label="Treasury Department", url="https://home.treasury.gov",
         listing="https://home.treasury.gov/news/press-releases", pattern=r"^/news/press-releases/[a-z]+\d+",
         # Releases are numbered by Secretary: jy (Yellen, to Jan 20 2025), sb (Bessent, from Jan 28 2025).
         ids=[("https://home.treasury.gov/news/press-releases/jy{n:04d}", 2760, 2830),
              ("https://home.treasury.gov/news/press-releases/sb{n:04d}", 1, 1200)]),
    dict(slug="war", label="Department of War (Defense)", url="https://www.war.gov", client="chrome",
         feeds=["https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=945&max={max}"],
         pattern=r"^/News/Releases/Release/Article/\d+"),
    dict(slug="justice", label="Justice Department", url="https://www.justice.gov", client="chrome",
         feeds=["https://www.justice.gov/news/rss?type=press_release&m=1"],
         doj_api="https://www.justice.gov/api/v1/press_releases.json", pattern=r"^/opa/pr/"),
    dict(slug="interior", label="Interior Department", url="https://www.doi.gov",
         listing="https://www.doi.gov/news", pattern=r"^/pressreleases/"),
    dict(slug="agriculture", label="Agriculture Department (USDA)", url="https://www.usda.gov", client="chrome",
         feeds=["https://www.usda.gov/rss/latest-releases.xml"],
         listing="https://www.usda.gov/about-usda/news/press-releases",
         pattern=r"^/about-usda/news/press-releases/20\d\d/"),
    dict(slug="commerce", label="Commerce Department", url="https://www.commerce.gov", client="chrome",
         feeds=["https://www.commerce.gov/feeds/news"], listing="https://www.commerce.gov/news/press-releases",
         pattern=r"^/news/press-releases/20\d\d/"),
    dict(slug="labor", label="Labor Department", url="https://www.dol.gov", client="chrome",
         feeds=["https://www.dol.gov/rss/releases.xml"], listing="https://www.dol.gov/newsroom/releases",
         pattern=r"^/newsroom/releases/[a-z]+/[a-z]+\d+"),
    dict(slug="hhs", label="Health and Human Services (HHS)", url="https://www.hhs.gov", client="chrome",
         feeds=["https://www.hhs.gov/rss/news.xml"], listing="https://www.hhs.gov/press-room/index.html",
         pattern=r"^/press-room/(?!index\.html)[a-z0-9-]+\.html$"),
    dict(slug="hud", label="Housing and Urban Development (HUD)", url="https://www.hud.gov",
         listing="https://www.hud.gov/news", pattern=r"^/news/hud-no-\d+-\d+",
         # The listing only reaches back to February 2025; earlier 2025 releases by number.
         ids=[("https://www.hud.gov/news/hud-no-25-{n:03d}", 1, 40)]),
    dict(slug="transportation", label="Transportation Department", url="https://www.transportation.gov",
         client="chrome", listing="https://www.transportation.gov/newsroom/press-releases",
         pattern=r"^/briefing-room/(?!dot-events)[a-z0-9-]{20,}"),
    dict(slug="energy", label="Energy Department", url="https://www.energy.gov",
         listing="https://www.energy.gov/listings/energy-news", pattern=r"^/articles/"),
    dict(slug="education", label="Education Department", url="https://www.ed.gov", client="browser",
         listing="https://www.ed.gov/about/news/press-release", pattern=r"^/about/news/press-release/"),
    dict(slug="va", label="Veterans Affairs (VA)", url="https://news.va.gov",
         wp="https://news.va.gov/wp-json/wp/v2/news-releases", pattern=r"^/press-room/"),
    dict(slug="dhs", label="Homeland Security (DHS)", url="https://www.dhs.gov",
         listing="https://www.dhs.gov/news-releases/press-releases", pattern=r"^/news/20\d\d/\d\d/\d\d/"),
    dict(slug="epa", label="Environmental Protection Agency (EPA)", url="https://www.epa.gov", client="browser",
         listing="https://www.epa.gov/newsreleases/search", pattern=r"^/newsreleases/(?!search)[a-z0-9-]{15,}"),
    # SBA is moving sites: 2026 releases are listed on legacy.sba.gov, 2025 ones in www.sba.gov's feed.
    dict(slug="sba", label="Small Business Administration (SBA)", url="https://www.sba.gov", client="chrome",
         feeds=["https://www.sba.gov/feed/"], paged_feed="https://www.sba.gov/feed/?paged={page}",
         listing="https://legacy.sba.gov/about-sba/sba-newsroom", pattern=r"^/(sba-newsroom|article)/20\d\d/"),
    dict(slug="omb", label="Office of Management and Budget (OMB)", url="https://www.whitehouse.gov/omb",
         listing="https://www.whitehouse.gov/omb/news/",
         pattern=r"^/(releases|briefings-statements|articles|presidential-actions)/20\d\d/\d\d/"),
    dict(slug="ustr", label="U.S. Trade Representative (USTR)", url="https://ustr.gov",
         feeds=["https://ustr.gov/rss.xml"],
         years="https://ustr.gov/about/policy-offices/press-office/press-releases/{year}",
         backfill=["https://ustr.gov/about-us/policy-offices/press-office/press-releases/2025-0"],
         pattern=r"/press-releases/20\d\d/[a-z]+/[a-z0-9-]+"),
    dict(slug="odni", label="Director of National Intelligence (ODNI)", url="https://www.odni.gov",
         listing="https://www.odni.gov/newsroom/press-releases/", pattern=r"^/\d{3,6}/[a-z0-9-]+/?$"),
    dict(slug="cia", label="CIA", url="https://www.cia.gov",
         listing="https://www.cia.gov/stories/press-releases-and-statements/", pattern=r"^/stories/story/"),
    dict(slug="un", label="U.S. Mission to the UN", url="https://usun.usmission.gov",
         wp="https://usun.usmission.gov/wp-json/wp/v2/posts", wp_params={"categories_exclude": "139,141"},
         paged="https://usun.usmission.gov/news/page/{page}/", first_page=1,
         pattern=r"^/[a-z0-9-]{15,}/?$"),
]


def is_agency(source_id: str) -> bool:
    return source_id.startswith(AGENCY_PREFIX)


def load_agencies() -> list[dict]:
    return [{
        "bioguide": AGENCY_PREFIX + a["slug"],
        "kind": "agency",
        "name": a["label"],
        "last_name": a["label"],
        "party": "",
        "state": "",
        "district": "",
        "label": a["label"],
        "url": a["url"],
        "rss_url": None,
        "committees": [],
        "scraper": None,
    } for a in AGENCIES]


AGENCY_BY_ID = {AGENCY_PREFIX + a["slug"]: a for a in AGENCIES}


# ---------------------------------------------------------------- fetching

_locks: dict[str, threading.Lock] = {}
_last: dict[str, float] = {}
_sessions: dict[str, object] = {}
_session_lock = threading.Lock()


def session_for(source_id: str):
    """HTTP session for an agency's pages (shared by listing and text fetches)."""
    agency = AGENCY_BY_ID.get(source_id, {})
    client = agency.get("client", "plain")
    key = source_id  # one session per agency; get() keeps each agency's requests one at a time
    with _session_lock:
        if key not in _sessions:
            _sessions[key] = _new_session(client, agency)
        return _sessions[key]


def _new_session(client: str, agency: dict):
    if client == "plain":
        s = requests.Session()
        s.headers["User-Agent"] = HONEST_UA
        return s
    from curl_cffi import requests as creq

    s = creq.Session(impersonate="chrome")
    if client == "browser":
        _browser_agency[id(s)] = agency
        _load_cookies(s, agency)
    return s


_browser_agency: dict[int, dict] = {}  # browser-client sessions -> their agency
_refreshes: dict[int, int] = {}
MAX_REFRESHES = 4


def _load_cookies(s, agency: dict) -> bool:
    for attempt in range(3):
        try:
            ua, cookies = _browser_cookies(agency.get("listing") or agency["url"])
        except Exception as e:  # no browser available: plain impersonation may still work
            print(f"agencies: browser for {agency['slug']} failed: {type(e).__name__}: {e}", flush=True)
            continue
        if cookies is None:
            print(f"agencies: {agency['slug']} challenge not passed (try {attempt + 1})", flush=True)
            continue
        s.headers["User-Agent"] = ua
        for c in cookies:
            s.cookies.set(c["name"], c["value"], domain=c["domain"])
        return True
    return False


CHALLENGE_TITLES = re.compile(r"just a moment|service unavailable|access denied|attention required|^$", re.I)


def _browser_cookies(url: str):
    """Load a page in Chrome until its JavaScript challenge lets the real page through; return
    (UA, cookies), or (UA, None) if it never does."""
    import os

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # A visible window passes challenges that headless Chrome fails; CI provides one with xvfb-run.
        browser = p.chromium.launch(channel="chrome", headless=not os.environ.get("DISPLAY"),
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(locale="en-US", viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        passed = False
        for _ in range(30):  # up to about a minute
            page.wait_for_timeout(2000)
            try:
                if not CHALLENGE_TITLES.search(page.title().strip()) and len(page.content()) > 20000:
                    passed = True
                    break
            except Exception:  # navigating mid-check
                pass
        page.wait_for_timeout(2000)
        ua = page.evaluate("navigator.userAgent")
        cookies = ctx.cookies() if passed else None
        browser.close()
    return ua, cookies


def get(url: str, session):
    """GET, one request at a time per host with a pause between; None on any failure."""
    host = urlsplit(url).netloc
    with _session_lock:
        lock = _locks.setdefault(host, threading.Lock())
    with lock:
        wait = _last.get(host, 0) + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            r = session.get(url, timeout=30)
            # A JavaScript challenge again (its cookies expired): pass it again and retry once.
            if r.status_code in (202, 403) and id(session) in _browser_agency \
                    and _refreshes.get(id(session), 0) < MAX_REFRESHES:
                _refreshes[id(session)] = _refreshes.get(id(session), 0) + 1
                if _load_cookies(session, _browser_agency[id(session)]):
                    r = session.get(url, timeout=30)
        except Exception as e:
            fallback.statuses[type(e).__name__] += 1
            return None
        finally:
            _last[host] = time.monotonic()
    fallback.statuses[r.status_code] += 1
    if r.status_code != 200:
        return None
    try:  # pages that don't declare a charset are UTF-8 here, not requests' Latin-1 default
        if (r.encoding or "").lower() in ("", "iso-8859-1"):
            r.encoding = "utf-8"
    except Exception:
        pass
    return r if r.text else None


def fetch_page(url: str, source_id: str) -> dict:
    """Text, date and title of one agency press release (see textfetch.fetch_page)."""
    from .textfetch import extract_page

    r = get(url, session_for(source_id))
    if r is None:
        return {"text": None, "date": None, "title": None}
    page = extract_page(r.text, url)
    if len(page["text"] or "") < SHORT_TEXT:
        # Some newsrooms (e.g. Interior) confuse the extractor into returning the .gov banner.
        soup = BeautifulSoup(r.text, "lxml")
        body = soup.find("article") or soup.find("main")
        if body is not None:
            text = html_to_text(str(body))
            if len(text) > len(page["text"] or ""):
                page["text"] = text[:40_000]
    return page


# ---------------------------------------------------------------- scraping

def html_to_text(fragment: str) -> str:
    soup = BeautifulSoup(fragment or "", "lxml")
    for junk in soup.select("script, style, noscript, figure, img, svg"):
        junk.decompose()
    blocks = [" ".join(el.get_text(" ", strip=True).split()) for el in soup.find_all(["p", "li", "h2", "h3", "h4", "blockquote"])]
    text = "\n\n".join(b for b in blocks if b)
    return text or " ".join(soup.get_text(" ", strip=True).split())


def _wp(agency: dict, session, pages: int) -> list[dict]:
    per_page = WP_PAGE_SIZE if pages > 1 else 20
    items = []
    for page in range(1, max(pages, 1) + 1):
        params = {"per_page": per_page, "page": page, "after": f"{START_DATE}T00:00:00",
                  "_fields": "date,link,title,content", **agency.get("wp_params", {})}
        url = agency["wp"] + "?" + "&".join(f"{k}={v}" for k, v in params.items())
        r = get(url, session)
        if r is None:
            break
        try:
            posts = r.json()
        except ValueError:
            break
        if not isinstance(posts, list) or not posts:
            break
        for p in posts:
            title = html.unescape(BeautifulSoup((p.get("title") or {}).get("rendered", ""), "lxml").get_text(" ", strip=True))
            text = html_to_text((p.get("content") or {}).get("rendered", ""))
            items.append({"url": p["link"], "title": " ".join(title.split()),
                          "date": dt.date.fromisoformat(p["date"][:10]), "text": text or None})
        # Some sites ignore per_page (State and the UN mission return 10), so go on until a page
        # comes back empty or reaches back past START_DATE.
        if min(p["date"][:10] for p in posts) < START_DATE:
            break
    return [i for i in items if i["date"].isoformat() >= START_DATE]


def _numbered(agency: dict, source_id: str, known: set[str], pages: int) -> list[dict]:
    """Numbered release pages that exist (backfill only). Stops a range after 25 misses in a row."""
    items = []
    for template, first, last in agency["ids"]:
        misses = 0
        for n in range(first, last + 1):
            url = template.format(n=n)
            if url in known:
                misses = 0
                continue
            page = fetch_page(url, source_id)
            if not page["title"] or not page["date"]:
                misses += 1
                if misses >= 25 and n > first + 25:
                    break
                continue
            misses = 0
            title = re.split(r"\s+[|\u2013\u2014]\s+", page["title"])[0]  # drop " | U.S. Department of ..."
            items.append({"url": url, "title": title, "date": dt.date.fromisoformat(page["date"]),
                          "text": page["text"]})
    return [i for i in items if i["date"].isoformat() >= START_DATE]


def _doj_api(agency: dict, pattern, session, pages: int) -> list[dict]:
    """Justice Department API: every DOJ office's releases, oldest first, so read from the end."""
    size = 50
    session = session_for("plain")  # the API answers plain clients, not the Chrome fingerprint
    r = get(f"{agency['doj_api']}?pagesize=1", session)
    try:
        count = int(r.json()["metadata"]["resultset"]["count"])
    except Exception:
        return []
    items = []
    for page in range((count - 1) // size, max((count - 1) // size - pages * 4, -1), -1):
        r = get(f"{agency['doj_api']}?pagesize={size}&page={page}", session)
        try:
            rows = r.json()["results"]
        except Exception:
            break
        dates = []
        for row in rows:
            try:
                date = dt.datetime.fromtimestamp(int(row["date"]), dt.timezone.utc).date()
            except (KeyError, TypeError, ValueError):
                continue
            dates.append(date)
            if pattern.search(urlsplit(row.get("url") or "").path):
                items.append({"url": row["url"], "title": " ".join(html.unescape(row.get("title") or "").split()),
                              "date": date, "text": html_to_text(row.get("body") or "") or None})
        if dates and max(dates).isoformat() < START_DATE:
            break
    return [i for i in items if i["date"].isoformat() >= START_DATE]


def _listing_pages(start_url: str, pattern, session, pages: int, template: str | None = None, first: int = 0) -> list[dict]:
    """A listing page and, for a backfill, the older pages after it (by "next" links or `template`)."""
    host = fallback._host(start_url)
    items, url, seen = [], start_url, set()
    for k in range(max(pages, 1)):
        if url in seen:
            break
        seen.add(url)
        r = get(url, session)
        if r is None:
            break
        found = [i for i in fallback._filter(fallback._items_from(r.text, url, pattern), host, pattern) if i["date"]]
        new = [i for i in found if i["url"] not in {x["url"] for x in items}]
        items.extend(new)
        if not new or min(i["date"] for i in new).isoformat() < START_DATE:
            break
        url = template.format(page=first + k + 1) if template else fallback.next_page_url(r.text, url)
        if not url:
            break
    return items


LINK_PREFIX = re.compile(r"^(more|read more|press release)\s*:\s*", re.I)


def scrape_agency(source: dict, pages: int = 1) -> tuple[list[dict], str | None, str]:
    """Return (items, error, method) like collect.scrape_member. Items may carry full text."""
    agency = AGENCY_BY_ID[source["bioguide"]]
    pattern = re.compile(agency["pattern"])
    host = fallback._host(agency["url"])
    session = session_for(source["bioguide"])
    backfill = pages > 1
    items, methods = [], []

    if agency.get("wp"):
        found = _wp(agency, session, pages)
        items += [i for i in found if pattern.search(urlsplit(i["url"]).path)]
        methods.append("wordpress")
    for feed in agency.get("feeds", []):
        url = feed.replace("{max}", "1000" if backfill else "30")
        r = get(url, session)
        if r is not None:
            items += [i for i in fallback._filter(fallback.from_rss(r.text, url), host, pattern) if i["date"]]
            methods.append("rss")
    if agency.get("paged_feed") and backfill:
        for page in range(pages):
            url = agency["paged_feed"].format(page=page)
            r = get(url, session)
            if r is None:
                break
            found = [i for i in fallback._filter(fallback.from_rss(r.text, url), host, pattern) if i["date"]]
            if not found:
                break
            items += found
            if min(i["date"] for i in found).isoformat() < START_DATE:
                break
    if agency.get("ids") and backfill:
        items += _numbered(agency, source["bioguide"], {i["url"] for i in items}, pages)
        methods.append("numbered")
    if agency.get("doj_api") and backfill:
        items += _doj_api(agency, pattern, session, pages)
        methods.append("api")
    listings = [agency["listing"]] if agency.get("listing") else []
    if agency.get("years"):
        this_year = dt.date.today().year
        years = range(int(START_DATE[:4]), this_year + 1) if backfill else [this_year]
        listings += [agency["years"].format(year=y) for y in years]
        listings += agency.get("backfill", []) if backfill else []
    for url in listings:
        items += _listing_pages(url, pattern, session, pages)
        methods.append("listing")
    if agency.get("paged") and backfill:
        first = agency.get("first_page", 0)
        items += _listing_pages(agency["paged"].format(page=first), pattern, session, pages, agency["paged"], first)
        methods.append("listing")

    skip = re.compile(agency["skip_titles"], re.I) if agency.get("skip_titles") else None
    out, seen = [], set()
    for i in items:
        i["url"] = i["url"].strip().replace("http://", "https://", 1)
        i["title"] = LINK_PREFIX.sub("", i["title"])
        key = store.normalize_url(i["url"])
        if key in seen or (skip and skip.search(i["title"])):
            continue
        seen.add(key)
        i.pop("feed", None)
        i.pop("categories", None)
        out.append(i)
    method = "+".join(dict.fromkeys(methods)) or "none"
    if out:
        return out, None, method
    return [], "no press releases found (blocked or page layout changed)", method
