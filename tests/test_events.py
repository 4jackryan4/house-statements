import random

from house_statements import events

MEMBERS = [{"bioguide": f"M{i:03d}", "name": f"Member{i} Person{i}"} for i in range(40)]
FILLER_TOPICS = [
    "farm bill crop insurance", "veterans clinic hours", "school lunch funding", "bridge repair grant",
    "small business loans", "broadband expansion", "wildfire smoke masks", "postal delivery delays",
    "housing vouchers", "transit station upgrade", "water main replacement", "airport noise study",
]


def _corpus():
    rng = random.Random(7)
    records, n = [], 0

    def add(title, text, member, date):
        nonlocal n
        n += 1
        records.append({"id": f"s{n:04d}", "title": title, "text": text, "bioguide": member, "date": date})

    # Background noise: one-off local statements spread over two months.
    for i in range(300):
        topic = rng.choice(FILLER_TOPICS)
        day = f"2026-{rng.choice(['08', '09'])}-{rng.randint(1, 28):02d}"
        add(f"Member announces {topic} update {i}", f"Today the member announced news about {topic} number {i}.",
            rng.choice(MEMBERS)["bioguide"], day)
    # A calamity: many members react within two days.
    for i, m in enumerate(MEMBERS[:12]):
        add(f"Statement on Hurricane Zelda landfall in the Gulf Coast",
            "Hurricane Zelda made landfall overnight. FEMA must surge disaster aid to the Gulf Coast "
            "families hit by Hurricane Zelda flooding.", m["bioguide"], f"2026-09-{14 + i % 2:02d}")
    return records


def test_calamity_statements_form_one_event():
    found = events.find_events(_corpus(), MEMBERS)
    zelda = [e for e in found if "Zelda" in e["label"] or "Zelda" in e["headline"]]
    assert len(zelda) == 1
    assert zelda[0]["member_count"] == 12
    assert "Zelda" in zelda[0]["label"]
    # Unrelated one-off statements are not swept into events with it.
    assert zelda[0]["statement_count"] == 12


def test_too_few_members_is_not_an_event():
    records = [r for r in _corpus() if "Zelda" not in r["title"]]
    hurricane = [r for r in _corpus() if "Zelda" in r["title"]][:2]
    found = events.find_events(records + hurricane, MEMBERS)
    assert not any("Zelda" in e["label"] for e in found)
