import datetime as dt

from house_statements import collect, store

MEMBER = {"bioguide": "X000001", "label": "Pat Example (D-NY-01)"}


def test_merge_items_adds_new_statements_once():
    records = {}
    items = [
        {"url": "https://example.house.gov/news/1", "title": "  First \n statement ", "date": dt.date(2026, 10, 1),
         "source": "https://example.house.gov/news"},
        {"url": "https://example.house.gov/news", "title": "Listing page", "date": None,
         "source": "https://example.house.gov/news"},
        {"url": "https://example.house.gov/news/old", "title": "Too old", "date": dt.date(2024, 5, 1)},
        {"url": "https://example.house.gov/news/2", "title": "No date yet", "date": None},
    ]
    new = collect.merge_items(records, MEMBER, items, "2026-10-05T01:00:00+00:00")
    assert len(new) == 2
    first = records[store.statement_id("https://example.house.gov/news/1")]
    assert first["title"] == "First statement"
    assert first["date"] == "2026-10-01"
    assert first["bioguide"] == "X000001"
    assert records[store.statement_id("https://example.house.gov/news/2")]["date"] is None

    # Seen again (with a date this time): nothing new, but the missing date is filled in.
    items[3]["date"] = dt.date(2026, 10, 3)
    assert collect.merge_items(records, MEMBER, items, "2026-10-05T02:00:00+00:00") == []
    assert records[store.statement_id("https://example.house.gov/news/2")]["date"] == "2026-10-03"


def test_future_listing_dates_are_ignored():
    records = {}
    items = [{"url": "https://example.house.gov/news/3", "title": "Typo date", "date": dt.date(2027, 7, 27)}]
    collect.merge_items(records, MEMBER, items, "2026-10-05T01:00:00+00:00")
    assert records[store.statement_id("https://example.house.gov/news/3")]["date"] is None


def test_same_release_at_a_second_url_is_not_added_twice():
    records = {}
    first = [{"url": "https://example.house.gov/2026/7/rep-acts", "title": "Rep. Example Acts on Floods",
              "date": dt.date(2026, 7, 2)}]
    collect.merge_items(records, MEMBER, first, "2026-10-05T01:00:00+00:00")
    again = [{"url": "https://example.house.gov/media/press-releases/rep-acts", "title": "Rep. Example acts on floods",
              "date": dt.date(2026, 7, 1)},
             {"url": "https://example.house.gov/media/press-releases/rep-acts-again", "title": "Rep. Example Acts on Floods",
              "date": dt.date(2026, 9, 1)}]
    assert len(collect.merge_items(records, MEMBER, again, "2026-10-05T02:00:00+00:00")) == 1
