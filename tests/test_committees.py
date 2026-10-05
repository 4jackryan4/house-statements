from house_statements import committees


def test_committee_sources_are_shaped_like_members():
    rows = committees.load_committees()
    ids = [c["bioguide"] for c in rows]
    assert len(ids) == len(set(ids)) == len(committees.COMMITTEES)
    for c in rows:
        assert committees.is_committee(c["bioguide"])
        assert c["kind"] == "committee" and c["state"] == "" and c["label"].endswith("Democrats")
        assert c["press_url"].startswith(c["url"] + "/") and ".house.gov" in c["url"]
    names = {c["bioguide"]: c["name"] for c in rows}
    assert names["committee-judiciary"] == "Judiciary Committee Democrats"
    assert names["committee-china"] == "China Select Committee Democrats"
