"""Generic scraper for member sites the python-statement scrapers don't handle.

Used automatically when a member has no scraper or their scraper returns nothing (new members,
site redesigns). It tries the site's RSS feed first, then common press-release listing pages,
and keeps links that look like individual statements, with dates read from the listing.
"""

import re
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from .config import USER_AGENT

RSS_PATHS = ["rss.xml", "news/rss.aspx", "feed/", "rss/press-releases.xml"]
LISTING_PATHS = [
    "media/press-releases", "news/press-releases", "press-releases", "media-center/press-releases",
    "newsroom/press-releases", "news/documentquery.aspx?DocumentTypeID=27", "news", "media", "press",
    "newsroom", "media-center", "",
]
# Paths that look like a single statement page.
STATEMENT_PATH = re.compile(
    r"documentsingle\.aspx\?documentid=\d+|/(press-releases?|news|media|statements?|posts?|newsroom|"
    r"media-center|press)/(?!page/|category/|tag/|feed)[^?#]*[a-z0-9][^?#]{8,}",
    re.I,
)
DATE_TEXT = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b"
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}\.\d{1,2}\.\d{2,4}\b",
    re.I,
)
MIN_TITLE = 20


def _get(url: str, session) -> requests.Response | None:
    try:
        r = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    except requests.RequestException:
        return None
    return r if r.ok else None


def _parse_date(text: str):
    m = DATE_TEXT.search(text or "")
    if not m:
        return None
    try:
        return dateparser.parse(m.group(0).replace(".", "/") if re.match(r"\d", m.group(0)) else m.group(0)).date()
    except (ValueError, OverflowError):
        return None


def from_rss(xml: str, source: str) -> list[dict]:
    soup = BeautifulSoup(xml, "xml")
    items = []
    for it in soup.find_all(["item", "entry"]):
        link = it.find("link")
        url = (link.get("href") or link.get_text()).strip() if link else ""
        title = it.find("title").get_text(" ", strip=True) if it.find("title") else ""
        date_el = it.find(["pubDate", "published", "updated", "dc:date"])
        date = None
        if date_el:
            try:
                date = dateparser.parse(date_el.get_text(strip=True)).date()
            except (ValueError, OverflowError):
                pass
        if url and title:
            items.append({"url": url, "title": title, "date": date, "source": source})
    return items


def from_listing(html: str, page_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    for junk in soup.select("nav, header, footer, script, style, noscript, [role=navigation]"):
        junk.decompose()
    host = urlsplit(page_url).netloc.lower().removeprefix("www.")
    seen, items = set(), []
    for a in soup.find_all("a", href=True):
        url = urljoin(page_url, a["href"]).split("#")[0]
        parts = urlsplit(url)
        if parts.netloc.lower().removeprefix("www.") != host or url.rstrip("/") == page_url.rstrip("/"):
            continue
        if not STATEMENT_PATH.search(parts.path + ("?" + parts.query if parts.query else "")):
            continue
        title = " ".join(a.get_text(" ", strip=True).split())
        if len(title) < MIN_TITLE or title.lower().startswith(("read more", "continue reading", "learn more")):
            continue
        if url in seen:
            continue
        seen.add(url)
        # The date usually sits in the same card: look outward a few levels.
        date, node = None, a
        for _ in range(4):
            node = node.parent
            if node is None:
                break
            t = node.find("time")
            if t is not None:
                date = _parse_date(t.get("datetime", "")) or _parse_date(t.get_text(" "))
            date = date or _parse_date(node.get_text(" ", strip=True)[:400])
            if date:
                break
        items.append({"url": url, "title": title, "date": date, "source": page_url})
    return items


def scrape_site(base_url: str, rss_url: str | None = None, session=None) -> list[dict]:
    """Best-effort list of recent statements for a member site."""
    session = session or requests.Session()
    base = base_url.rstrip("/") + "/"

    for url in ([rss_url] if rss_url else []) + [urljoin(base, p) for p in RSS_PATHS]:
        r = _get(url, session)
        if r is not None and ("<rss" in r.text[:2000] or "<feed" in r.text[:2000]):
            items = from_rss(r.text, url)
            if items:
                return items

    best: list[dict] = []
    for path in LISTING_PATHS:
        url = urljoin(base, path)
        r = _get(url, session)
        if r is None:
            continue
        items = from_listing(r.text, r.url)
        dated = sum(1 for i in items if i["date"])
        if dated >= 3 and dated >= sum(1 for i in best if i["date"]):
            best = items
            if dated >= 8:
                break
    return [i for i in best if i["date"]] if any(i["date"] for i in best) else best
