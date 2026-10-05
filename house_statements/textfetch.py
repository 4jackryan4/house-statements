"""Fetch a statement page and extract its main text (and date, when the listing lacked one)."""

import re

import requests
import trafilatura

from .config import USER_AGENT

MAX_TEXT_CHARS = 40_000


# " | Congresswoman Jane Doe" and similar site names after a page title.
SITE_SUFFIX = re.compile(r"\s+[|\u2013\u2014-]\s+[^|\u2013\u2014]*(congress|representative|house\.gov|u\.s\. house)[^|]*$", re.I)


def fetch_page(url: str, session: requests.Session | None = None) -> dict:
    """Text, ISO date and title of a statement page; any may be None."""
    http = session or requests
    try:
        resp = http.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
    except requests.RequestException:
        return {"text": None, "date": None, "title": None}
    html = resp.text
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False, favor_precision=True)
    date = title = None
    meta = trafilatura.extract_metadata(html, default_url=url)
    if meta is not None:
        date = meta.date[:10] if meta.date else None
        title = SITE_SUFFIX.sub("", " ".join((meta.title or "").split())) or None
    if text:
        text = text.strip()[:MAX_TEXT_CHARS]
    return {"text": text or None, "date": date, "title": title}


def fetch_text(url: str, session: requests.Session | None = None) -> tuple[str | None, str | None]:
    """Return (text, iso_date) for a statement page; either may be None."""
    page = fetch_page(url, session)
    return page["text"], page["date"]
