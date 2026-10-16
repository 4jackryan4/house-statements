"""Check how agency listings page (run in CI under xvfb-run). Writes probe/paging/index.json + pages."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from house_statements import agencies, fallback  # noqa: E402

out = Path("probe/paging")
out.mkdir(parents=True, exist_ok=True)
rows = []
URLS = {
    "agency-epa": ["https://www.epa.gov/newsreleases/search"]
                  + [f"https://www.epa.gov/newsreleases/search?page={n}" for n in (1, 2, 3, 4, 5, 6, 20)],
    "agency-sba": [f"https://www.sba.gov/feed/?paged={n}" for n in (1, 2, 3, 5, 10)]
                  + ["https://legacy.sba.gov/about-sba/sba-newsroom", "https://legacy.sba.gov/about-sba/sba-newsroom?page=2",
                     "https://www.sba.gov/sba-newsroom/feed/?post_type=news-release&paged=2"],
}
for aid, urls in URLS.items():
    a = agencies.AGENCY_BY_ID[aid]
    pat = re.compile(a["pattern"])
    s = agencies.session_for(aid)
    for n, url in enumerate(urls):
        r = agencies.get(url, s)
        body = r.text if r is not None else ""
        (out / f"{aid}-{n}.html").write_text(body)
        if "feed" in url:
            items = fallback.from_rss(body, url)
        else:
            items = fallback._items_from(body, url, pat)
        dates = sorted(i["date"].isoformat() for i in items if i.get("date"))
        rows.append({"url": url, "status": r.status_code if r is not None else None, "bytes": len(body),
                     "items": len(items), "matching": sum(1 for i in items if pat.search(i["url"])),
                     "dates": [dates[0], dates[-1]] if dates else None,
                     "next": fallback.next_page_url(body, url) if body else None,
                     "sample": [i["url"] for i in items[:3]]})
        print(rows[-1], flush=True)
(out / "index.json").write_text(json.dumps(rows, indent=1))
