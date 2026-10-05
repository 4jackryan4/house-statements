"""Generic scraper for member sites the python-statement scrapers don't handle.

Used when a member has no scraper or their scraper returns nothing (new members, site
redesigns). It reads the site's RSS feeds and common press-release listing pages, keeps only
links that look like individual statements on the member's own site, and picks the source with
the freshest statements. Listing pages can be followed to older pages for a backfill.
"""

import datetime as dt
import json
import re
import threading
import time
import warnings
from collections import Counter
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, NavigableString, Tag, XMLParsedAsHTMLWarning
from dateutil import parser as dateparser

from . import store
from .config import START_DATE, USER_AGENT

# Tried only when the homepage links to no press or news page.
GUESS_PATHS = ["media/press-releases", "news/press-releases", "press-releases", "media-center/press-releases", "news"]
LISTING_LINK = re.compile(r"press|news|statement", re.I)
NOT_LISTING = re.compile(
    r"in[-_]the[-_]news|newsletter|news[-_]clips|subscribe|sign-?up|press-kit|inquir|headshot|email|digest", re.I
)
# On a sweep of sites whose own scraper works, only press-release pages and feeds are read: general
# "news" pages there mostly repost press coverage.
PRESS_LISTING = re.compile(r"press|statement", re.I)
MAX_LISTINGS = 4
# house.gov sits behind one firewall: keep the generic scraper to a few requests a second overall.
MIN_INTERVAL = 0.25
_lock = threading.Lock()
_last = [0.0]
statuses: Counter = Counter()
# Paths that look like a single statement page.
STATEMENT_PATH = re.compile(
    r"documentsingle\.aspx\?documentid=\d+|/(press-releases?|news|media|statements?|posts?|newsroom|"
    r"media-center|press|\d{4}/\d{2})/(?!page/|category/|tag/|feed|press-releases?/?$)[^?#]*[a-z0-9][^?#]{8,}",
    re.I,
)
DATE_TEXT = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b"
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}\.\d{1,2}\.\d{2,4}\b",
    re.I,
)
# Coverage of the member and site pages that share those paths.
EXCLUDE_PATH = re.compile(
    r"/(in[-_]the[-_]news|in[-_]the[-_]media|news[-_]clips|media[-_]mentions|news[-_]articles|[a-z-]*newsletters?|"
    r"printed-media[^/]*|events?|photos?|videos?|galler(?:y|ies)|services|press-kit|email|[a-z-]*digest|"
    r"[a-z-]+-update|imo)(/|$)|documentquery\.aspx",
    re.I,
)
# WordPress feeds often put posts at the site root: /clyburn-statement-on-the-passing-of-...
ROOT_SLUG = re.compile(r"^/[a-z0-9%]+(?:-[a-z0-9%]+){4,}/?$", re.I)
# Placeholder posts that come with every new member's Drupal site.
TEMPLATE_TITLES = {
    "taking the oath of office", "newest member of congress", "newest members of congress",
    "119th united states congress", "119th united states congress convenes",
}
SKIP_CATEGORIES = re.compile(r"in the news|news clips?|media coverage|in the media|events?$|newsletters?", re.I)
MIN_TITLE = 15
SKIP_TITLES = (
    "read more", "continue reading", "learn more", "full release", "view all", "more news", "upcoming events",
)

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


def _get(url: str, session) -> requests.Response | None:
    with _lock:
        wait = _last[0] + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()
    try:
        r = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    except requests.RequestException as e:
        statuses[type(e).__name__] += 1
        return None
    statuses[r.status_code] += 1
    return r if r.ok else None


def candidates(home_html: str, home_url: str) -> tuple[list[str], list[str]]:
    """(feeds, listing pages) that a member's homepage links to."""
    soup = BeautifulSoup(home_html, "lxml")
    host = _host(home_url)
    feeds = []
    for link in soup.find_all("link", href=True, type=re.compile("rss|atom", re.I)):
        url = urljoin(home_url, link["href"])
        if _host(url) == host and "comments" not in url and url not in feeds:
            feeds.append(url)
    listings = []
    for a in soup.find_all("a", href=True):
        url = urljoin(home_url, a["href"]).split("#")[0]
        path = urlsplit(url).path
        depth = len([seg for seg in path.split("/") if seg])
        if (
            _host(url) == host and 1 <= depth <= 2 and LISTING_LINK.search(path) and not NOT_LISTING.search(path)
            and not is_statement_url(url, host) and not ROOT_SLUG.match(path) and url not in listings
        ):
            listings.append(url)
    return feeds, listings[:MAX_LISTINGS]


def _parse_date(text: str, last: bool = False):
    """First (or last) date written in text, or None."""
    found = list(DATE_TEXT.finditer(text or ""))
    for m in (reversed(found) if last else found):
        s = m.group(0)
        try:
            d = dateparser.parse(s.replace(".", "/") if re.match(r"\d", s) else s).date()
        except (ValueError, OverflowError):
            continue
        if 1990 < d.year < 2100:
            return d
    return None


def _host(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def is_statement_url(url: str, host: str, from_feed: bool = False) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or _host(url) != host or EXCLUDE_PATH.search(parts.path):
        return False
    if from_feed and ROOT_SLUG.match(parts.path):
        return True
    return bool(STATEMENT_PATH.search(parts.path + ("?" + parts.query if parts.query else "")))


def _clean_title(title: str) -> str:
    return " ".join((title or "").split())


def _keep(item: dict, host: str) -> bool:
    title = item["title"]
    return (
        is_statement_url(item["url"], host, from_feed=item.get("feed", False))
        and len(title) >= MIN_TITLE
        and not title.lower().startswith(SKIP_TITLES)
        and title.lower().strip(" .") not in TEMPLATE_TITLES
    )


def from_rss(xml: str, source: str) -> list[dict]:
    soup = BeautifulSoup(xml, "xml")
    items = []
    for it in soup.find_all(["item", "entry"]):
        link = it.find("link")
        url = (link.get("href") or link.get_text()).strip() if link else ""
        title = _clean_title(it.find("title").get_text(" ", strip=True)) if it.find("title") else ""
        date_el = it.find(["pubDate", "published", "updated", "date"])
        date = None
        if date_el:
            try:
                date = dateparser.parse(date_el.get_text(strip=True)).date()
            except (ValueError, OverflowError):
                pass
        categories = [c.get_text(strip=True) for c in it.find_all("category")]
        if categories and all(SKIP_CATEGORIES.search(c) for c in categories):
            continue  # press coverage of the member, not their own statement
        if url and title:
            items.append({"url": url, "title": title, "date": date, "source": source, "feed": True})
    return items


def from_next_data(html: str, page_url: str) -> list[dict]:
    """Posts embedded in a Next.js page's __NEXT_DATA__ (headless WordPress member sites)."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return []
    items = []

    def walk(o):
        if isinstance(o, dict):
            link = o.get("link") or (urljoin(page_url, o["uri"]) if isinstance(o.get("uri"), str) else None)
            if isinstance(o.get("title"), str) and isinstance(o.get("date"), str) and link:
                try:
                    date = dateparser.parse(o["date"]).date()
                except (ValueError, OverflowError):
                    date = None
                items.append({"url": link, "title": _clean_title(o["title"]), "date": date, "source": page_url})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return items


def _stream(root: Tag, host: str, page_url: str):
    """Document-order tokens: ("link", url, a) for statement links and ("text", str) for other text."""
    for node in root.descendants:
        if isinstance(node, Tag):
            if node.name == "a" and node.get("href"):
                url = urljoin(page_url, node["href"]).split("#")[0]
                if is_statement_url(url, host):
                    yield ("link", url, node)
            elif node.name == "time" and node.get("datetime"):
                yield ("text", " " + node["datetime"][:10] + " ")
        elif isinstance(node, NavigableString) and not any(
            p.name == "a" and p.get("href") and is_statement_url(urljoin(page_url, p["href"]), host)
            for p in node.parents if isinstance(p, Tag) and p.name == "a"
        ):
            text = str(node).strip()
            if text and type(node) is NavigableString:
                yield ("text", " " + text + " ")


def from_listing(html: str, page_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    for junk in soup.select("nav, header, footer, script, style, noscript, [role=navigation]"):
        junk.decompose()
    host = _host(page_url)
    body = soup.body or soup

    # Each link's own card: the largest ancestor holding no other statement link.
    titles: dict[str, str] = {}
    card_date: dict[str, dt.date | None] = {}
    order: list[str] = []
    for a in body.find_all("a", href=True):
        url = urljoin(page_url, a["href"]).split("#")[0]
        if not is_statement_url(url, host) or url.rstrip("/") == page_url.rstrip("/"):
            continue
        title = _clean_title(a.get_text(" ", strip=True)) or _clean_title(a.get("title") or a.get("aria-label"))
        if url not in titles:
            order.append(url)
            titles[url] = ""
            card_date[url] = None
        if len(title) > len(titles[url]) and not title.lower().startswith(SKIP_TITLES):
            titles[url] = title
        if card_date[url]:
            continue
        node, card = a, None
        while node.parent is not None and node.parent is not body:
            urls = {
                urljoin(page_url, x["href"]).split("#")[0] for x in node.parent.find_all("a", href=True)
            }
            if len({u for u in urls if is_statement_url(u, host)} - {url}) > 0:
                break
            node = card = node.parent
        if card is not None:
            t = card.find("time", datetime=True)
            card_date[url] = (_parse_date(t["datetime"]) if t else None) or _parse_date(card.get_text(" ", strip=True))

    # Flat lists (date and headline as siblings): use the date written between this link and the
    # previous one, or, if the page puts dates after headlines, between this link and the next.
    segments: dict[str, list[str]] = {u: [] for u in order}
    before: dict[str, str] = {}
    current, buf = None, []
    for tok in _stream(body, host, page_url):
        if tok[0] == "link":
            url = tok[1]
            if url == current:
                continue
            if url in segments and url not in before:
                before[url] = "".join(buf)
            if current in segments:
                segments[current].append("".join(buf))
            current, buf = url, []
        else:
            buf.append(tok[1])
    after = {u: (segments[u][0] if segments[u] else "".join(buf) if u == current else "") for u in order}
    flat = [u for u in order if not card_date[u]]
    dates_first = bool(flat) and _parse_date(before.get(flat[0], "")[-300:]) is not None

    items = []
    for url in order:
        date = card_date[url]
        if date is None:
            date = _parse_date(before.get(url, "")[-300:], last=True) if dates_first else _parse_date(after[url][:300])
        items.append({"url": url, "title": titles[url], "date": date, "source": page_url})
    return items


def next_page_url(html: str, page_url: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        classes = " ".join(sum((p.get("class") or [] for p in [a, *list(a.parents)[:2]] if isinstance(p, Tag)), []))
        label = " ".join([a.get_text(" ", strip=True), a.get("aria-label") or "", a.get("title") or ""]).lower()
        if "next" in (a.get("rel") or []) or re.search(r"\bnext\b", classes, re.I) or re.search(
            r"^(next|older|›|»|next page|next ›|next »|older posts|older entries)\b", label.strip()
        ):
            url = urljoin(page_url, a["href"])
            if url.rstrip("/") != page_url.rstrip("/") and _host(url) == _host(page_url):
                return url
    return None


def _items_from(text: str, url: str) -> list[dict]:
    head = text[:2000]
    if "<rss" in head or "<feed" in head or "<rdf" in head:
        return from_rss(text, url)
    return from_next_data(text, url) + from_listing(text, url)


def _filter(items: list[dict], host: str) -> list[dict]:
    seen, out = set(), []
    for i in items:
        i["url"] = i["url"].strip()
        if i["url"] in seen or not _keep(i, host):
            continue
        seen.add(i["url"])
        out.append(i)
    return out


def _recent(items: list[dict]) -> int:
    return sum(1 for i in items if i["date"] and i["date"].isoformat() >= START_DATE)


def scrape_site(
    base_url: str, rss_url: str | None = None, session=None, pages: int = 1, known: list[str] | None = None,
    press_only: bool = False,
) -> list[dict]:
    """Recent statements from a member site; with pages > 1, also older listing pages.

    Every feed and listing page that yields statements since START_DATE contributes; the listing
    with the most of them is the one followed to older pages. Each item's "origin" is the feed or
    listing it came from; pass earlier origins as `known` to skip rediscovering them.
    """
    session = session or requests.Session()
    base = base_url.rstrip("/") + "/"
    host = _host(base)
    known = [u for u in known or [] if not NOT_LISTING.search(u)]
    if known:
        found = _scrape(host, session, pages, known, press_only=press_only)
        if found:
            return found
    home = _get(base, session)
    if home is None:
        return []
    feeds, listings = candidates(home.text, home.url)
    if not listings:
        listings = [urljoin(base, p) for p in GUESS_PATHS]
        if not feeds and "wp-content" in home.text:
            feeds = [urljoin(base, "feed/")]
    urls = list(dict.fromkeys(([rss_url] if rss_url else []) + feeds + listings))
    return _scrape(host, session, pages, urls, first=home, press_only=press_only)


def _scrape(host: str, session, pages: int, urls: list[str], first=None, press_only: bool = False) -> list[dict]:
    sources = []  # (items, response)
    tried = set()
    for url, r in ([(first.url, first)] if first is not None else []) + [(u, None) for u in urls]:
        if r is None:
            r = _get(url, session)
        if r is None or r.url in tried:
            continue
        tried.add(r.url)
        items = [i for i in _filter(_items_from(r.text, r.url), host) if i["date"]]
        for i in items:
            i["origin"] = url
        if press_only and items and not items[0].get("feed") and not PRESS_LISTING.search(urlsplit(url).path):
            continue
        if _recent(items):
            sources.append((items, r))
    if not sources:
        return []
    sources.sort(key=lambda s: _recent(s[0]), reverse=True)

    found, seen = [], set()

    def add(items):
        added = 0
        for i in items:
            key = store.normalize_url(i["url"])
            if key not in seen:
                seen.add(key)
                found.append(i)
                added += 1
        return added

    for items, _ in sources:
        add(items)

    # Older pages of the main listing, until they run past START_DATE.
    main = next(((items, r) for items, r in sources if not items[0].get("feed")), None)
    listing = main[1] if main else None
    page, seen_pages = listing, {listing.url} if listing else set()
    for _ in range(pages - 1 if listing else 0):
        nxt = next_page_url(page.text, page.url)
        if not nxt or nxt in seen_pages:
            break
        seen_pages.add(nxt)
        page = _get(nxt, session)
        if page is None:
            break
        more = [i for i in _filter(_items_from(page.text, page.url), host) if i["date"]]
        for i in more:
            i["origin"] = main[0][0]["origin"]
        if not add(more):
            break
        if max(i["date"] for i in more).isoformat() < START_DATE:
            break
    return found
