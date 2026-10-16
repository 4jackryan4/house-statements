"""Load agency pages in headless Chromium (for sites behind JavaScript bot challenges).

Usage: python scripts/probe_browser.py URL [URL ...]  -> probe/browser/<n>.html + index.json
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

out = Path("probe/browser")
out.mkdir(parents=True, exist_ok=True)
index = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_context(locale="en-US").new_page()
    for n, url in enumerate(sys.argv[1:]):
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(8000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            body, status = page.content(), resp.status if resp else None
        except Exception as e:
            body, status = str(e), None
        (out / f"{n}.html").write_text(body)
        index.append({"n": n, "url": url, "final_url": page.url, "status": status, "bytes": len(body),
                      "title": page.title() if body else ""})
        print(index[-1])
    browser.close()
(out / "index.json").write_text(json.dumps(index, indent=1))
