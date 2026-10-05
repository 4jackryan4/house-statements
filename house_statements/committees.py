"""House committees' Democratic (minority) websites, collected alongside member sites.

Each committee is a "source" shaped like a member in data/members.json, so collection, storage,
search and feeds treat it the same way. Committee ids start with "committee-". The press page is
the listing scraped with the generic scraper in fallback.py (python-statement has no House
committee scrapers). Ethics is left out: it has no Democratic site.
"""

COMMITTEE_PREFIX = "committee-"

# (id suffix, committee name as in members' committee lists, site, press release listing)
COMMITTEES = [
    ("agriculture", "Agriculture", "https://democrats-agriculture.house.gov",
     "news/documentquery.aspx?DocumentTypeID=27"),
    ("appropriations", "Appropriations", "https://democrats-appropriations.house.gov", "news/press-releases"),
    ("armed-services", "Armed Services", "https://democrats-armedservices.house.gov", "press-releases"),
    ("budget", "Budget", "https://democrats-budget.house.gov", "news/press-releases"),
    ("education-workforce", "Education and Workforce", "https://democrats-edworkforce.house.gov",
     "media/press-releases"),
    ("energy-commerce", "Energy and Commerce", "https://democrats-energycommerce.house.gov", "media/press-releases"),
    ("financial-services", "Financial Services", "https://democrats-financialservices.house.gov",
     "news/documentquery.aspx?DocumentTypeID=2636"),
    ("foreign-affairs", "Foreign Affairs", "https://democrats-foreignaffairs.house.gov", "press-releases"),
    ("homeland-security", "Homeland Security", "https://democrats-homeland.house.gov", "news/press-releases"),
    ("house-administration", "House Administration", "https://democrats-cha.house.gov", "media/press-releases"),
    ("intelligence", "Intelligence", "https://democrats-intelligence.house.gov",
     "news/documentquery.aspx?DocumentTypeID=27"),
    ("judiciary", "Judiciary", "https://democrats-judiciary.house.gov", "media-center/press-releases"),
    ("natural-resources", "Natural Resources", "https://democrats-naturalresources.house.gov", "media/press-releases"),
    ("oversight", "Oversight and Government Reform", "https://oversightdemocrats.house.gov", "news/press-releases"),
    ("rules", "Rules", "https://democrats-rules.house.gov", "media/press-releases"),
    ("science", "Science, Space, and Technology", "https://democrats-science.house.gov", "news/press-releases"),
    ("small-business", "Small Business", "https://democrats-smallbusiness.house.gov",
     "news/documentquery.aspx?DocumentTypeID=27"),
    ("transportation", "Transportation and Infrastructure", "https://democrats-transportation.house.gov",
     "news/press-releases"),
    ("veterans-affairs", "Veterans' Affairs", "https://democrats-veterans.house.gov", "news/press-releases"),
    ("ways-and-means", "Ways and Means", "https://democrats-waysandmeans.house.gov", "media-center/press-releases"),
    ("china", "Select Committee on China", "https://democrats-selectcommitteeontheccp.house.gov",
     "media/press-releases"),
]

SHORT_NAMES = {
    "Oversight and Government Reform": "Oversight",
    "Science, Space, and Technology": "Science",
    "Transportation and Infrastructure": "Transportation",
    "Select Committee on China": "China Select Committee",
}


def is_committee(source_id: str) -> bool:
    return source_id.startswith(COMMITTEE_PREFIX)


def load_committees() -> list[dict]:
    out = []
    for slug, committee, url, press in COMMITTEES:
        name = f"{SHORT_NAMES.get(committee, committee)} Committee Democrats".replace(
            "Select Committee Committee", "Select Committee"
        )
        out.append({
            "bioguide": COMMITTEE_PREFIX + slug,
            "kind": "committee",
            "name": name,
            "last_name": name,
            "party": "Democrat",
            "state": "",
            "district": "",
            "label": name,
            "url": url,
            "rss_url": None,
            "press_url": f"{url}/{press}",
            "committees": [committee],
            "scraper": None,
        })
    return out
