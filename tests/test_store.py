from house_statements import store


def test_normalize_url_treats_variants_as_one_statement():
    a = "http://www.Example.house.gov/news/release-1/?utm_source=x#top"
    b = "https://example.house.gov/news/release-1"
    assert store.normalize_url(a) == store.normalize_url(b)
    assert store.statement_id(a) == store.statement_id(b)


def test_save_and_load_round_trip(tmp_path):
    records = {
        "a": {"id": "a", "date": "2026-09-30", "title": "One"},
        "b": {"id": "b", "date": "2026-10-01", "title": "Two"},
        "c": {"id": "c", "date": None, "title": "Undated"},
    }
    store.save_all(records, tmp_path)
    assert (tmp_path / "2026" / "2026-09.jsonl").exists()
    assert (tmp_path / "2026" / "2026-10.jsonl").exists()
    assert (tmp_path / "undated.jsonl").exists()
    assert store.load_all(tmp_path) == records

    # Dating the undated record moves it and removes the now-empty file.
    records["c"]["date"] = "2026-10-02"
    store.save_all(records, tmp_path)
    assert not (tmp_path / "undated.jsonl").exists()
    assert store.load_all(tmp_path) == records
