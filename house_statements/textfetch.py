"""Fetch a statement page and extract its main text (and date, when the listing lacked one)."""

import requests
import trafilatura

from .config import USER_AGENT

MAX_TEXT_CHARS = 40_000


def fetch_text(url: str, session: requests.Session | None = None) -> tuple[str | None, str | None]:
    """Return (text, iso_date) for a statement page; either may be None."""
    http = session or requests
    try:
        resp = http.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
    except requests.RequestException:
        return None, None
    html = resp.text
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False, favor_precision=True)
    date = None
    meta = trafilatura.extract_metadata(html, default_url=url)
    if meta is not None and meta.date:
        date = meta.date[:10]
    if text:
        text = text.strip()[:MAX_TEXT_CHARS]
    return text or None, date
