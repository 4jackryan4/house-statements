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
