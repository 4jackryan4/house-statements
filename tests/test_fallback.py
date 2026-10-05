import datetime as dt
import json

from house_statements import fallback

CARDS = """<html><body><nav><a href="/media/press-releases/menu-link-that-is-long-enough">Menu link that is long enough</a></nav>
<div class="views-row"><div class="h5"><a href="/media/press-releases/rep-acts-on-flooding">Rep. Smith Acts on Flooding in the District</a></div>
<div class="col-auto">September 29, 2026</div><div><a href="/media/press-releases">Press Release</a></div></div>
<div class="views-row"><div class="h5"><a href="/media/press-releases/rep-backs-relief-bill">Rep. Smith Backs the Flood Relief Bill</a></div>
<div class="col-auto">September 2, 2026</div></div>
<div class="views-row"><div class="h5"><a href="/media/in-the-news/paper-covers-rep">Local Paper: Smith Visits Flooded Towns</a></div>
<div class="col-auto">September 1, 2026</div></div>
<div class="views-row"><div class="h5"><a href="/media/press-releases/taking-the-oath-of-office">Taking the Oath of Office</a></div>
<div class="col-auto">September 1, 2026</div></div>
<ul class="pager"><li class="pager__item--next"><a href="?page=1"><span>›</span></a></li></ul>
</body></html>"""

# Date above each headline, with an earlier summary that mentions another date.
FLAT = """<html><body><div id="press">
<span class="date">September 29, 2026</span><h3><a href="/media/press-releases/first-statement-here">First Statement About the Storm</a></h3>
<p>WASHINGTON (September 29, 2026) - Today ... <a href="/media/press-releases/first-statement-here">Continue Reading</a></p><hr>
<span class="date">September 25, 2026</span><h3><a href="/media/press-releases/second-statement-here">Second Statement About the Storm</a></h3><hr>
<span class="date">August 1, 2026</span><h3><a href="/media/press-releases/third-statement-here">Third Statement About the Budget</a></h3>
</div></body></html>"""

FEED = """<?xml version="1.0"?><rss><channel>
<item><title>Clyburn Statement on the Passing of a Colleague</title><link>https://clyburn.house.gov/clyburn-statement-on-the-passing-of-a-colleague/</link>
<pubDate>Sun, 12 Jul 2026 14:00:00 +0000</pubDate><category>Press Releases</category></item>
<item><title>Random Lengths News: Lawmakers Push Clean Shipping</title><link>https://clyburn.house.gov/random-lengths-news-lawmakers-push-clean-shipping/</link>
<pubDate>Tue, 30 Jun 2026 14:00:00 +0000</pubDate><category>In the News</category></item>
<item><title>Tours and Tickets</title><link>https://clyburn.house.gov/services/tours-and-tickets</link>
<pubDate>Tue, 30 Jun 2026 14:00:00 +0000</pubDate></item>
</channel></rss>"""

NEXT = """<html><body><script id="__NEXT_DATA__" type="application/json">%s</script></body></html>""" % json.dumps(
    {"props": {"pageProps": {"q": [{"node": {"title": "Rep. Torres Urges Regulators to Act Now", "date": "2026-09-11T10:00:00",
                                              "uri": "/posts/rep-torres-urges-regulators-to-act-now"}}]}}}
)


def test_cards_use_each_cards_own_date_and_skip_coverage_and_placeholders():
    items = fallback._filter(fallback.from_listing(CARDS, "https://smith.house.gov/media/press-releases"), "smith.house.gov")
    got = {i["title"]: i["date"] for i in items}
    assert got == {
        "Rep. Smith Acts on Flooding in the District": dt.date(2026, 9, 29),
        "Rep. Smith Backs the Flood Relief Bill": dt.date(2026, 9, 2),
    }
    assert fallback.next_page_url(CARDS, "https://smith.house.gov/media/press-releases") == (
        "https://smith.house.gov/media/press-releases?page=1"
    )


def test_flat_list_takes_the_date_written_above_each_headline():
    items = fallback._filter(fallback.from_listing(FLAT, "https://m.house.gov/news"), "m.house.gov")
    assert [(i["title"], i["date"]) for i in items] == [
        ("First Statement About the Storm", dt.date(2026, 9, 29)),
        ("Second Statement About the Storm", dt.date(2026, 9, 25)),
        ("Third Statement About the Budget", dt.date(2026, 8, 1)),
    ]


def test_feed_keeps_posts_and_drops_press_coverage_and_service_pages():
    items = fallback._filter(fallback.from_rss(FEED, "https://clyburn.house.gov/feed/"), "clyburn.house.gov")
    assert [i["title"] for i in items] == ["Clyburn Statement on the Passing of a Colleague"]


def test_next_js_sites():
    items = fallback._filter(fallback.from_next_data(NEXT, "https://ritchietorres.house.gov/"), "ritchietorres.house.gov")
    assert items[0]["url"] == "https://ritchietorres.house.gov/posts/rep-torres-urges-regulators-to-act-now"
    assert items[0]["date"] == dt.date(2026, 9, 11)


class FakeSession:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, **kw):
        class R:
            pass

        r = R()
        r.url, r.text = url, self.pages.get(url, "")
        r.ok = url in self.pages
        r.status_code = 200 if r.ok else 404
        return r


def test_scrape_site_follows_pages_and_records_origin():
    page2 = FLAT.replace("first-statement-here", "older-one-here").replace("2026", "2025")
    home = '<html><body><a href="/media/press-releases">Press Releases</a><a href="/media/in-the-news">In the News</a></body></html>'
    assert fallback.candidates(home, "https://smith.house.gov/") == ([], ["https://smith.house.gov/media/press-releases"])
    session = FakeSession({
        "https://smith.house.gov/": home,
        "https://smith.house.gov/media/press-releases": CARDS,
        "https://smith.house.gov/media/press-releases?page=1": page2,
    })
    items = fallback.scrape_site("https://smith.house.gov", session=session, pages=5)
    assert len(items) == 5
    assert {i["origin"] for i in items} == {"https://smith.house.gov/media/press-releases"}
    known = fallback.scrape_site("https://smith.house.gov", session=session, known=["https://smith.house.gov/media/press-releases"])
    assert len(known) == 2


def test_site_pages_and_short_link_text():
    html = """<html><body>
<div><a href="/media/press-releases/rep-launches-shipyard-caucus-with-colleagues">Public Shipyard Caucus</a> June 1, 2026</div>
<div><a href="/media/press-list-sign-up-for-reporters">Press List Sign Up for Reporters</a> June 1, 2026</div>
<div><a href="/media/press-releases/this-is-a-test-post">This is the fifth test post for testing purposes</a> June 1, 2026</div>
</body></html>"""
    items = fallback._filter(fallback.from_listing(html, "https://p.house.gov/media/press-releases"), "p.house.gov")
    assert [(i["title"], i.get("title_check")) for i in items] == [("Public Shipyard Caucus", True)]


def test_committee_layouts_date_span_above_headline_and_id_links():
    html = """<html><body><div id="press">
      <span class="date black">September 28, 2026</span>
      <h2 class="title"><a href="/news/press-releases/thompson-statement-on-ice-oversight-this-summer">Thompson Statement on ICE Oversight This Summer</a></h2>
      <p class="summary">(WASHINGTON) - Today the Ranking Member said ... <a href="/news/press-releases/thompson-statement-on-ice-oversight-this-summer">Continue Reading</a></p>
      <hr/>
      <span class="date black">09.25.26</span>
      <h2 class="title"><a href="/news/press-releases/thompson-on-preparedness-against-terrorism">Thompson on Preparedness Against Terrorism Cuts</a></h2>
      <p class="summary">A long summary of the statement that mentions nothing about when it was posted at all.</p>
    </div></body></html>"""
    items = fallback._filter(fallback.from_listing(html, "https://democrats-homeland.house.gov/news/press-releases"),
                             "democrats-homeland.house.gov")
    assert [(i["date"].isoformat(), i["title"][:8]) for i in items] == [("2026-09-28", "Thompson"), ("2026-09-25", "Thompson")]

    table = """<html><body><table><tbody>
      <tr><td class="recordListDate">9/24/26</td><td><a href="/press-releases?ID=E8A9F5BF-845E-4797-83C0-82BE72092693">Members Demand Answers on Delayed Separation Pay</a>
        <a href="/press-releases?ID=E8A9F5BF-845E-4797-83C0-82BE72092693#RelatedFiles"><img alt="Files"></a></td></tr>
      <tr><td class="recordListDate">9/15/26</td><td><a href="/press-releases?ID=332F3E77-BF7D-4F45-9220-504F17B924D8">Smith Statement on CBO Estimate of War Costs</a></td></tr>
      <tr><td><a href="/press-releases?page=2">2</a></td></tr>
    </tbody></table></body></html>"""
    items = fallback._filter(fallback.from_listing(table, "https://democrats-armedservices.house.gov/press-releases"),
                             "democrats-armedservices.house.gov")
    assert [i["date"].isoformat() for i in items] == ["2026-09-24", "2026-09-15"]
