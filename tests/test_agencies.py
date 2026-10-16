import re

from house_statements import agencies, fallback


def test_agency_sources_are_shaped_like_members():
    rows = agencies.load_agencies()
    ids = [a["bioguide"] for a in rows]
    assert len(ids) == len(set(ids)) == len(agencies.AGENCIES)
    for a in rows:
        assert agencies.is_agency(a["bioguide"]) and a["kind"] == "agency" and a["state"] == ""
    for a in agencies.AGENCIES:
        re.compile(a["pattern"])
        assert any(a.get(k) for k in ("wp", "feeds", "listing", "years", "doj_api"))


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


def test_groups_and_claims():
    rows = {a["bioguide"]: a for a in agencies.load_agencies()}
    assert rows["agency-labor"]["group"] == agencies.CABINET
    assert rows["agency-osha"]["group"] == agencies.SUB and rows["agency-osha"]["claims"] == ["agency-labor"]
    assert len(rows) > 90


def test_url_date():
    assert str(agencies._url_date("https://x.gov/news/2026/10/15/slug")) == "2026-10-15"
    assert str(agencies._url_date("https://x.gov/news/20250103/slug")) == "2025-01-03"
    assert str(agencies._url_date("https://x.gov/n/2025-02-28/slug")) == "2025-02-28"
    assert agencies._url_date("https://x.gov/news/2025/13/40/slug") is None
    assert agencies._url_date("https://x.gov/news/slug") is None


def test_listing_items_scope_placeholder_titles_and_url_dates():
    html = """<html><body><div class="sidebar"><a href="/news/2024/01/02/old-sidebar-story-here">Old Sidebar Story
      About Something Else</a></div><main>
      <a href="/news/2026/10/14/agency-announces-new-safety-rule">Read more about Agency Announces New Safety Rule
      for Bridges</a>
      <a href="/news/2026/10/13/grant-awards">Read more</a></main></body></html>"""
    items = agencies._listing_items(html, "https://www.x.gov/news", re.compile(r"^/news/20"), "main")
    assert [i["url"].rsplit("/", 1)[-1] for i in items] == ["agency-announces-new-safety-rule", "grant-awards"]
    assert items[0]["title"].startswith("Agency Announces") and not items[0].get("title_check")
    assert items[1]["title"] == "Release grant-awards" and items[1]["title_check"]
    assert [str(i["date"]) for i in items] == ["2026-10-14", "2026-10-13"]
