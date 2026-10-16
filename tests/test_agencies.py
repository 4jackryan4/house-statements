import re

from house_statements import agencies, fallback


def test_agency_sources_are_shaped_like_members():
    rows = agencies.load_agencies()
    ids = [a["bioguide"] for a in rows]
    assert len(ids) == len(set(ids)) == len(agencies.AGENCIES) == 22
    for a in rows:
        assert agencies.is_agency(a["bioguide"]) and a["kind"] == "agency" and a["state"] == ""
    for a in agencies.AGENCIES:
        re.compile(a["pattern"])
        assert any(a.get(k) for k in ("wp", "feeds", "listing", "years"))


def test_listing_with_pattern_keeps_cards_inside_header_and_nav():
    html = """<html><body><nav class="usa-nav"><a href="/about">About</a>
      <article><header><h3><a href="/pressreleases/interior-announces-new-wildfire-crews">Interior
      Announces New Wildfire Crews for the West</a></h3></header><time datetime="2026-10-02">Oct 2</time></article>
      <article><header><h3><a href="/pressreleases/secretary-visits-alaska-parks">Secretary Visits Alaska
      National Parks This Week</a></h3></header><time datetime="2026-10-01">Oct 1</time></article>
    </nav></body></html>"""
    items = fallback.from_listing(html, "https://www.doi.gov/news", re.compile(r"^/pressreleases/"))
    assert [str(i["date"]) for i in items] == ["2026-10-02", "2026-10-01"]
    assert items[0]["url"] == "https://www.doi.gov/pressreleases/interior-announces-new-wildfire-crews"


def test_wordpress_html_to_text():
    text = agencies.html_to_text("<section><h2>Title</h2><p>First  para.</p><figure>img</figure><p>Second.</p></section>")
    assert text == "Title\n\nFirst para.\n\nSecond."
