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


def test_specific_agency_claims_a_release_and_skip_list():
    labor = {"bioguide": "agency-labor", "kind": "agency"}
    osha = {"bioguide": "agency-osha", "kind": "agency", "claims": ["agency-labor"]}
    item = {"url": "https://www.dol.gov/newsroom/releases/osha/osha20261001", "title": "OSHA Cites Employer After Fall", "date": dt.date(2026, 10, 1)}
    records = {}
    sid = collect.merge_items(records, labor, [item], "2026-10-05T01:00:00+00:00")[0]
    assert collect.merge_items(records, osha, [item], "2026-10-05T02:00:00+00:00") == []
    assert records[sid]["bioguide"] == "agency-osha"
    other = {"url": "https://www.dol.gov/old", "title": "An Old Release From Years Ago", "date": None}
    assert collect.merge_items(records, labor, [other], "2026-10-05T02:00:00+00:00", skip={store.statement_id(other["url"])}) == []


def test_union_keeps_statements_committed_meanwhile(tmp_path):
    main, run = tmp_path / "main", tmp_path / "run"
    rec = lambda i, **kw: {"id": i, "url": f"https://x.gov/{i}", "title": "A Statement About Something",
                           "date": "2026-10-01", "bioguide": "A000001"} | kw
    store.save_all({"a": rec("a", text="old text"), "b": rec("b")}, main)  # b came from a backfill meanwhile
    store.save_all({"a": rec("a", title="Retitled Statement About Something"), "c": rec("c")}, run)
    collect.union(str(run), main)
    out = store.load_all(main)
    assert sorted(out) == ["a", "b", "c"]
    assert out["a"]["title"].startswith("Retitled") and out["a"]["text"] == "old text"
