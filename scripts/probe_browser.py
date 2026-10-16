"""Load agency pages in Chromium (for sites behind JavaScript bot challenges).

Writes probe/browser/<name>.html + index.json. Tries: headless Chromium, then real Chrome with a
visible window (run under xvfb-run), and for EPA, reusing the browser's cookies from curl_cffi.
"""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

out = Path("probe/browser")
out.mkdir(parents=True, exist_ok=True)
index = []
TARGETS = {
    "ed": "https://www.ed.gov/about/news/press-release",
    "commerce": "https://www.commerce.gov/news/press-releases",
    "epa": "https://www.epa.gov/newsreleases/search",
}


def load(page, name, url):
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(10000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        body, status = page.content(), resp.status if resp else None
    except Exception as e:
        body, status = str(e), None
    (out / f"{name}.html").write_text(body)
    row = {"name": name, "url": url, "final_url": page.url, "status": status, "bytes": len(body), "title": page.title()}
    index.append(row)
    print(row, flush=True)


with sync_playwright() as p:
    for mode, kwargs in (("headful-chrome", {"channel": "chrome", "headless": False}),
                         ("headless-chrome", {"channel": "chrome", "headless": True})):
        try:
            browser = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"], **kwargs)
        except Exception as e:
            print(mode, "launch failed", e)
            continue
        ctx = browser.new_context(locale="en-US", viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        for name, url in TARGETS.items():
            load(page, f"{mode}-{name}", url)
        if mode == "headful-chrome":
            cookies = ctx.cookies()
            ua = page.evaluate("navigator.userAgent")
            (out / "epa-cookies.json").write_text(json.dumps({"ua": ua, "cookies": cookies}, indent=1))
        browser.close()

# Reuse the EPA cookies outside the browser.
from curl_cffi import requests as creq  # noqa: E402

saved = json.loads((out / "epa-cookies.json").read_text())
jar = {c["name"]: c["value"] for c in saved["cookies"] if "epa.gov" in c["domain"]}
for name, url in (("epa-cookie-listing2", "https://www.epa.gov/newsreleases/search?page=1"),
                  ("epa-cookie-article", "https://www.epa.gov/newsreleases/epa-completes-cleanup-former-hospital-site-colorado")):
    for client in ("curl", "requests"):
        try:
            if client == "curl":
                r = creq.get(url, impersonate="chrome", cookies=jar, headers={"User-Agent": saved["ua"]}, timeout=30)
            else:
                import requests
                r = requests.get(url, cookies=jar, headers={"User-Agent": saved["ua"]}, timeout=30)
            status, body = r.status_code, r.text
        except Exception as e:
            status, body = None, str(e)
        (out / f"{name}-{client}.html").write_text(body)
        index.append({"name": f"{name}-{client}", "url": url, "status": status, "bytes": len(body)})
        print(index[-1], flush=True)

for name, url in (("sba1", "https://www.sba.gov/about-sba/sba-newsroom"), ("sba2", "https://www.sba.gov/sba-newsroom/"),
                  ("sba3", "https://www.sba.gov/sba-newsroom/feed/"), ("sba4", "https://www.sba.gov/category/news-release/feed/"),
                  ("sba5", "https://www.sba.gov/news-releases/")):
    try:
        r = creq.get(url, impersonate="chrome", timeout=30)
        status, body, final = r.status_code, r.text, str(r.url)
    except Exception as e:
        status, body, final = None, str(e), url
    (out / f"{name}.html").write_text(body)
    index.append({"name": name, "url": url, "final_url": final, "status": status, "bytes": len(body)})
    print(index[-1], flush=True)
(out / "index.json").write_text(json.dumps(index, indent=1))
