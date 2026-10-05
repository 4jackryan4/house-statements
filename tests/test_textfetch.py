from house_statements.textfetch import fetch_text

PAGE = """<html><head><meta property="article:published_time" content="2026-10-01"><title>X</title></head>
<body><nav>Home About Contact</nav><article><h1>Rep. Example Statement on Hurricane Zelda</h1>
<p>WASHINGTON - Today Rep. Example released the following statement on Hurricane Zelda, which made landfall
overnight along the Gulf Coast and left thousands without power.</p>
<p>"Our hearts are with every family affected. FEMA must move quickly to deliver aid," said Rep. Example.
"I will be working with local officials around the clock."</p></article><footer>Privacy policy</footer></body></html>"""


class FakeResponse:
    text = PAGE

    def raise_for_status(self):
        pass


class FakeSession:
    def get(self, url, **kwargs):
        return FakeResponse()


def test_fetch_text_extracts_body_and_date():
    text, date = fetch_text("https://example.house.gov/news/zelda", FakeSession())
    assert "FEMA must move quickly" in text
    assert "Privacy policy" not in text
    assert date == "2026-10-01"


def test_site_name_is_trimmed_from_page_titles():
    from house_statements.textfetch import SITE_SUFFIX

    assert SITE_SUFFIX.sub("", "Rep. Pappas Helps Launch Shipyard Caucus | Congressman Chris Pappas") == (
        "Rep. Pappas Helps Launch Shipyard Caucus"
    )
    assert SITE_SUFFIX.sub("", "Statement on Gaza - Ceasefire Now") == "Statement on Gaza - Ceasefire Now"
