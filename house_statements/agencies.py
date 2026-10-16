"""Press releases from cabinet-level federal agencies, collected alongside member sites.

Each agency is a "source" shaped like a member in data/members.json (id "agency-<slug>"), so
storage, search and feeds treat it the same way. Cabinet level means the 15 executive
departments plus the agencies whose heads hold cabinet rank (EPA, SBA, OMB, USTR, ODNI, CIA and
the UN ambassador). Each department's main newsroom only, not its sub-agencies.

Agency newsrooms are not house.gov sites, so they have their own readers here: an RSS feed, a
press release listing (followed to older pages for a backfill), or a WordPress API. Statement
links must match the agency's `pattern`. Several agencies block plain HTTP clients from cloud
servers, so `client` picks how pages are fetched:
  - "plain": requests with an honest browser-compatible User-Agent
  - "chrome": curl_cffi impersonating Chrome's TLS fingerprint (gets past Akamai bot rules)
  - "browser": cookies from a real Chrome window (JavaScript challenges: EPA's AWS WAF, Education's
    Azure WAF), then curl_cffi with those cookies. Needs Chrome and a display (xvfb-run in CI).
Commerce sits behind a Cloudflare challenge that even real Chrome on GitHub's servers doesn't pass;
it is tried every run and shows as "error" in data/health.json until that changes.
"""

import datetime as dt
import html
import re
import threading
import time
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from . import fallback, store
from .config import START_DATE

AGENCY_PREFIX = "agency-"
CABINET = "Cabinet agencies"
HONEST_UA = "Mozilla/5.0 (compatible; house-statements/1.0; +https://github.com/4jackryan4/house-statements)"
MIN_INTERVAL = 0.5  # seconds between requests to the same agency
WP_PAGE_SIZE = 100
SHORT_TEXT = 400  # extracted text shorter than this is checked against the page's <article>

# slug, label, homepage, how to read it.
#   feeds: RSS feeds read every run. "{max}" is filled with how many items to ask for.
#   listing: press release listing page, followed to older pages on a backfill.
#   paged_feed: an RSS feed with a page number ("{page}"), read page by page on a backfill.
#   wp: WordPress REST endpoint (posts come with their full text).
#   years: yearly listing pages ("{year}"), this year's read every run, older ones on a backfill.
#   ids: (URL template with "{n}", first, last) ranges of numbered release pages, fetched one by one
#     on a backfill, for newsrooms whose listings can't be paged (Treasury, HUD). Missing numbers 404.
#   paged: listing page URL with a page number ("{page}", counting from `first_page`), read page
#     by page on a backfill (for sites whose "next" links don't work or that block the listing).
#   scope: CSS selector of the part of a listing page that holds the releases.
#   claims: other sources whose copy of a release this one takes over (OSHA from Labor).
#   text_url: (regex, replacement) turning a listed address into the page that has the text.
#   interval: seconds between requests, for newsrooms that answer 429 at the usual pace.
WH_PATTERN = r"^/(releases|briefings-statements|fact-sheets|presidential-actions|remarks|research|articles)/20\d\d/\d\d/"
WH, SUB, REG, IND, LEG = ("White House offices", "Agencies within departments", "Independent regulators",
                          "Independent agencies", "Legislative branch agencies")


def _wh_office(slug, label):
    """A White House office: its news page lists its releases, which are whitehouse.gov posts."""
    return dict(slug=slug, label=label, url=f"https://www.whitehouse.gov/{slug}/", group=WH,
                listing=f"https://www.whitehouse.gov/{slug}/news/", scope="main", claims=["whitehouse"],
                paged=f"https://www.whitehouse.gov/{slug}/news/?query-54-page={{page}}", first_page=2,
                pattern=WH_PATTERN)


def _dvids(slug, label, url, path, group=SUB):
    """Military newsrooms on the DoD web platform: listing pages numbered with ?Page=N."""
    return dict(slug=slug, label=label, url=url, group=group, client="chrome", listing=url + path,
                paged=url + path + "?Page={page}", first_page=2, pattern=r"/Article/\d{6,}/")


# Not included because their newsrooms can't be read from GitHub's servers (checked 2026-10-16):
# Commerce (Cloudflare challenge; listed below, shows as an error), BOP, CDC, Census, USPTO and the
# Park Service (lists built by JavaScript), BLS (release pages are overwritten each month), SSA,
# FLRA, MSPB, Peace Corps, AmeriCorps and TVA (403 even to a real browser), USAID (site taken
# down) and the Library of Congress (bot challenge).
AGENCIES = [
    dict(slug="state", label="State Department", url="https://www.state.gov",
         wp="https://www.state.gov/wp-json/wp/v2/state_press_release", pattern=r"^/releases/",
         paged="https://www.state.gov/press-releases/page/{page}/", first_page=1,
         skip_titles=r"^public schedule\b"),
    dict(slug="treasury", label="Treasury Department", url="https://home.treasury.gov",
         listing="https://home.treasury.gov/news/press-releases", pattern=r"^/news/press-releases/[a-z]+\d+",
         # Releases are numbered by Secretary: jy (Yellen, to Jan 20 2025), sb (Bessent, from Jan 28 2025).
         ids=[("https://home.treasury.gov/news/press-releases/jy{n:04d}", 2760, 2830),
              ("https://home.treasury.gov/news/press-releases/sb{n:04d}", 1, 1200)]),
    dict(slug="war", label="Department of War (Defense)", url="https://www.war.gov", client="chrome",
         feeds=["https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=945&max={max}"],
         pattern=r"^/News/Releases/Release/Article/\d+"),
    dict(slug="justice", label="Justice Department", url="https://www.justice.gov", client="chrome",
         feeds=["https://www.justice.gov/news/rss?type=press_release&m=1"],
         doj_api="https://www.justice.gov/api/v1/press_releases.json", pattern=r"^/opa/pr/"),
    dict(slug="interior", label="Interior Department", url="https://www.doi.gov",
         listing="https://www.doi.gov/news", pattern=r"^/pressreleases/"),
    dict(slug="agriculture", label="Agriculture Department (USDA)", url="https://www.usda.gov", client="chrome",
         feeds=["https://www.usda.gov/rss/latest-releases.xml"],
         listing="https://www.usda.gov/about-usda/news/press-releases",
         pattern=r"^/about-usda/news/press-releases/20\d\d/"),
    dict(slug="commerce", label="Commerce Department", url="https://www.commerce.gov", client="chrome",
         feeds=["https://www.commerce.gov/feeds/news"], listing="https://www.commerce.gov/news/press-releases",
         pattern=r"^/news/press-releases/20\d\d/"),
    dict(slug="labor", label="Labor Department", url="https://www.dol.gov", client="chrome",
         feeds=["https://www.dol.gov/rss/releases.xml"], listing="https://www.dol.gov/newsroom/releases",
         pattern=r"^/newsroom/releases/[a-z]+/[a-z]+\d+"),
    dict(slug="hhs", label="Health and Human Services (HHS)", url="https://www.hhs.gov", client="chrome",
         feeds=["https://www.hhs.gov/rss/news.xml"], listing="https://www.hhs.gov/press-room/index.html",
         pattern=r"^/press-room/(?!index\.html)[a-z0-9-]+\.html$"),
    dict(slug="hud", label="Housing and Urban Development (HUD)", url="https://www.hud.gov",
         listing="https://www.hud.gov/news", pattern=r"^/news/hud-no-\d+-\d+",
         # The listing only reaches back to February 2025; earlier 2025 releases by number.
         ids=[("https://www.hud.gov/news/hud-no-25-{n:03d}", 1, 40)]),
    dict(slug="transportation", label="Transportation Department", url="https://www.transportation.gov",
         client="chrome", listing="https://www.transportation.gov/newsroom/press-releases",
         pattern=r"^/briefing-room/(?!dot-events)[a-z0-9-]{20,}"),
    dict(slug="energy", label="Energy Department", url="https://www.energy.gov",
         listing="https://www.energy.gov/listings/energy-news", pattern=r"^/articles/"),
    dict(slug="education", label="Education Department", url="https://www.ed.gov", client="browser",
         listing="https://www.ed.gov/about/news/press-release", pattern=r"^/about/news/press-release/"),
    dict(slug="va", label="Veterans Affairs (VA)", url="https://news.va.gov",
         wp="https://news.va.gov/wp-json/wp/v2/news-releases", pattern=r"^/press-room/"),
    dict(slug="dhs", label="Homeland Security (DHS)", url="https://www.dhs.gov",
         listing="https://www.dhs.gov/news-releases/press-releases", pattern=r"^/news/20\d\d/\d\d/\d\d/"),
    dict(slug="epa", label="Environmental Protection Agency (EPA)", url="https://www.epa.gov", client="browser",
         listing="https://www.epa.gov/newsreleases/search",
         paged="https://www.epa.gov/newsreleases/search?page={page}", first_page=1, pattern=r"^/newsreleases/(?!search)[a-z0-9-]{15,}"),
    # SBA is moving sites: 2026 releases are listed on legacy.sba.gov, 2025 ones in www.sba.gov's feed.
    dict(slug="sba", label="Small Business Administration (SBA)", url="https://www.sba.gov", client="chrome",
         feeds=["https://www.sba.gov/feed/"], paged_feed="https://www.sba.gov/feed/?paged={page}",
         listing="https://legacy.sba.gov/about-sba/sba-newsroom", pattern=r"^/(sba-newsroom|article)/20\d\d/"),
    dict(slug="omb", label="Office of Management and Budget (OMB)", url="https://www.whitehouse.gov/omb",
         listing="https://www.whitehouse.gov/omb/news/", scope="main", claims=["whitehouse"],
         pattern=WH_PATTERN),
    dict(slug="ustr", label="U.S. Trade Representative (USTR)", url="https://ustr.gov",
         feeds=["https://ustr.gov/rss.xml"],
         years="https://ustr.gov/about/policy-offices/press-office/press-releases/{year}",
         backfill=["https://ustr.gov/about-us/policy-offices/press-office/press-releases/2025-0"],
         pattern=r"/press-releases/20\d\d/[a-z]+/[a-z0-9-]+"),
    dict(slug="odni", label="Director of National Intelligence (ODNI)", url="https://www.odni.gov",
         listing="https://www.odni.gov/newsroom/press-releases/", pattern=r"^/\d{3,6}/[a-z0-9-]+/?$"),
    dict(slug="cia", label="CIA", url="https://www.cia.gov",
         listing="https://www.cia.gov/stories/press-releases-and-statements/", pattern=r"^/stories/story/"),
    dict(slug="un", label="U.S. Mission to the UN", url="https://usun.usmission.gov",
         wp="https://usun.usmission.gov/wp-json/wp/v2/posts", wp_params={"categories_exclude": "139,141"},
         paged="https://usun.usmission.gov/news/page/{page}/", first_page=1,
         pattern=r"^/[a-z0-9-]{15,}/?$"),
    # ---- Beyond the cabinet (added at the user's request, October 2026)
    dict(slug="whitehouse", label="White House", url="https://www.whitehouse.gov", group=WH,
         feeds=["https://www.whitehouse.gov/news/feed/"], listing="https://www.whitehouse.gov/news/", scope="main",
         pattern=WH_PATTERN),
    _wh_office("ostp", "Office of Science and Technology Policy"),
    _wh_office("ondcp", "Office of National Drug Control Policy"),
    _wh_office("ceq", "Council on Environmental Quality"),

    dict(slug="fema", label="FEMA", url="https://www.fema.gov", group=SUB,
         feeds=["https://www.fema.gov/feeds/news.rss"], listing="https://www.fema.gov/about/news-multimedia/press-releases",
         pattern=r"^/press-release/\d{8}/"),
    dict(slug="cbp", label="Customs and Border Protection (CBP)", url="https://www.cbp.gov", group=SUB,
         listing="https://www.cbp.gov/newsroom/national-media-release",
         paged="https://www.cbp.gov/newsroom/national-media-release?combine=&field_date_release_value=All"
               "&field_newsroom_type_target_id_1=81&page={page}", first_page=1,
         pattern=r"^/newsroom/national-media-release/[a-z0-9-]{15,}"),
    dict(slug="ice", label="Immigration and Customs Enforcement (ICE)", url="https://www.ice.gov", group=SUB,
         listing="https://www.ice.gov/newsroom", pattern=r"^/news/releases/[a-z0-9-]{10,}"),
    dict(slug="secretservice", label="Secret Service", url="https://www.secretservice.gov", group=SUB,
         listing="https://www.secretservice.gov/newsroom/releases", pattern=r"^/newsroom/releases/20\d\d/\d\d/"),
    dict(slug="tsa", label="TSA", url="https://www.tsa.gov", group=SUB,
         listing="https://www.tsa.gov/news/press/releases", pattern=r"^/news/press/releases/20\d\d/\d\d/\d\d/"),
    dict(slug="coastguard", label="Coast Guard", url="https://www.news.uscg.mil", group=SUB, client="chrome",
         listing="https://www.news.uscg.mil/Press-Releases/", paged="https://www.news.uscg.mil/Press-Releases/?Page={page}",
         first_page=2, pattern=r"^/Press-Releases/Article/\d{6,}/"),
    dict(slug="cisa", label="CISA", url="https://www.cisa.gov", group=SUB,
         feeds=["https://www.cisa.gov/news.xml"], listing="https://www.cisa.gov/news-events/news",
         pattern=r"^/news-events/news/[a-z0-9-]{15,}"),
    dict(slug="uscis", label="USCIS", url="https://www.uscis.gov", group=SUB,
         feeds=["https://www.uscis.gov/news/rss-feed/22984"], listing="https://www.uscis.gov/newsroom/news-releases",
         pattern=r"^/newsroom/news-releases/[a-z0-9-]{10,}"),
    dict(slug="fbi", label="FBI", url="https://www.fbi.gov", group=SUB, client="chrome",
         feeds=["https://www.fbi.gov/news/press-releases/RSS"], listing="https://www.fbi.gov/news/press-releases",
         paged="https://www.fbi.gov/news/press-releases?page={page}", first_page=2,
         pattern=r"^/(news/press-releases|contact-us/field-offices/[a-z]+/news)/[a-z0-9-]{15,}"),
    dict(slug="dea", label="DEA", url="https://www.dea.gov", group=SUB, client="browser",
         listing="https://www.dea.gov/what-we-do/news/press-releases",
         paged="https://www.dea.gov/what-we-do/news/press-releases?page={page}", first_page=1, pattern=r"^/press-releases/20\d\d/\d\d/\d\d/"),
    dict(slug="atf", label="ATF", url="https://www.atf.gov", group=SUB, client="chrome",
         listing="https://www.atf.gov/news/press-releases",
         paged="https://www.atf.gov/news/press-releases?page={page}", first_page=1, pattern=r"^/news/press-releases/[a-z0-9-]{15,}"),
    dict(slug="usms", label="U.S. Marshals", url="https://www.usmarshals.gov", group=SUB, client="browser",
         listing="https://www.usmarshals.gov/news", pattern=r"^/news/press-release/[a-z0-9-]{10,}"),
    # Every U.S. Attorney's office posts on justice.gov; the Justice Department API has them all.
    dict(slug="usao", label="U.S. Attorneys' Offices", url="https://www.justice.gov/usao", group=SUB,
         doj_api="https://www.justice.gov/api/v1/press_releases.json", doj_hourly=True, pattern=r"^/usao-[a-z-]+/pr/"),
    dict(slug="fda", label="FDA", url="https://www.fda.gov", group=SUB,
         feeds=["https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml"],
         listing="https://www.fda.gov/news-events/fda-newsroom/press-announcements",
         pattern=r"^/news-events/press-announcements/[a-z0-9-]{15,}"),
    dict(slug="nih", label="NIH", url="https://www.nih.gov", group=SUB,
         feeds=["https://www.nih.gov/news-releases/feed.xml"], listing="https://www.nih.gov/news-events/news-releases",
         pattern=r"^/news-events/news-releases/[a-z0-9-]{10,}"),
    dict(slug="cms", label="CMS (Medicare and Medicaid)", url="https://www.cms.gov", group=SUB,
         listing="https://www.cms.gov/about-cms/contact/newsroom",
         pattern=r"^/newsroom/(press-releases|news-alert|fact-sheets)/[a-z0-9-]{15,}"),
    dict(slug="irs", label="IRS", url="https://www.irs.gov", group=SUB,
         listing="https://www.irs.gov/newsroom/news-releases-for-current-month", backfill=['https://www.irs.gov/newsroom/news-releases-for-january-2025', 'https://www.irs.gov/newsroom/news-releases-for-february-2025', 'https://www.irs.gov/newsroom/news-releases-for-march-2025', 'https://www.irs.gov/newsroom/news-releases-for-april-2025', 'https://www.irs.gov/newsroom/news-releases-for-may-2025', 'https://www.irs.gov/newsroom/news-releases-for-june-2025', 'https://www.irs.gov/newsroom/news-releases-for-july-2025', 'https://www.irs.gov/newsroom/news-releases-for-august-2025', 'https://www.irs.gov/newsroom/news-releases-for-september-2025', 'https://www.irs.gov/newsroom/news-releases-for-october-2025', 'https://www.irs.gov/newsroom/news-releases-for-november-2025', 'https://www.irs.gov/newsroom/news-releases-for-december-2025', 'https://www.irs.gov/newsroom/news-releases-for-january-2026', 'https://www.irs.gov/newsroom/news-releases-for-february-2026', 'https://www.irs.gov/newsroom/news-releases-for-march-2026', 'https://www.irs.gov/newsroom/news-releases-for-april-2026', 'https://www.irs.gov/newsroom/news-releases-for-may-2026', 'https://www.irs.gov/newsroom/news-releases-for-june-2026', 'https://www.irs.gov/newsroom/news-releases-for-july-2026', 'https://www.irs.gov/newsroom/news-releases-for-august-2026', 'https://www.irs.gov/newsroom/news-releases-for-september-2026'],
         pattern=r"^/newsroom/(?!news-releases-for|tax-relief-in|irs-statements-and|working-families)[a-z0-9-]{30,}$"),
    dict(slug="occ", label="Comptroller of the Currency (OCC)", url="https://www.occ.gov", group=SUB,
         feeds=["https://www.occ.gov/rss/occ_news.xml"], pattern=r"^/news-issuances/news-releases/20\d\d/",
         ids=[("https://www.occ.gov/news-issuances/news-releases/2025/nr-occ-2025-{n}.html", 1, 200),
              ("https://www.occ.gov/news-issuances/news-releases/2026/nr-occ-2026-{n}.html", 1, 200)]),
    dict(slug="noaa", label="NOAA", url="https://www.noaa.gov", group=SUB,
         feeds=["https://www.noaa.gov/news-release-feed/rss.xml"], listing="https://www.noaa.gov/news-releases",
         pattern=r"^/news-release/[a-z0-9-]{10,}"),
    dict(slug="nws", label="National Weather Service", url="https://www.weather.gov", group=SUB,
         listing="https://www.weather.gov/news/", pattern=r"^/news/\d{6}-"),
    dict(slug="faa", label="FAA", url="https://www.faa.gov", group=SUB,
         feeds=["https://www.faa.gov/newsroom/press_releases/rss"], listing="https://www.faa.gov/newsroom/press_releases",
         pattern=r"^/newsroom/(?!press_releases|faa-daily|statements$)[a-z0-9-]{15,}$"),
    dict(slug="nhtsa", label="NHTSA", url="https://www.nhtsa.gov", group=SUB, client="chrome",
         listing="https://www.nhtsa.gov/press-releases", paged="https://www.nhtsa.gov/press-releases?page={page}",
         first_page=1, pattern=r"^/press-releases/[a-z0-9-]{10,}"),
    # OSHA's releases are the Labor Department's /osha/ releases; this source takes them over.
    dict(slug="osha", label="OSHA", url="https://www.osha.gov", group=SUB, client="chrome", claims=["labor"],
         feeds=["https://www.dol.gov/rss/releases.xml"], listing="https://www.dol.gov/newsroom/releases",
         pattern=r"^/newsroom/releases/osha/"),
    dict(slug="blm", label="Bureau of Land Management", url="https://www.blm.gov", group=SUB,
         listing="https://www.blm.gov/news", pattern=r"^/press-release/[a-z0-9-]{10,}"),
    dict(slug="bia", label="Bureau of Indian Affairs", url="https://www.bia.gov", group=SUB,
         listing="https://www.bia.gov/news", pattern=r"^/news/[a-z0-9-]{20,}"),
    dict(slug="forestservice", label="Forest Service", url="https://www.fs.usda.gov", group=SUB,
         listing="https://www.fs.usda.gov/about-agency/newsroom/releases",
         pattern=r"^/about-agency/newsroom/releases/[a-z0-9-]{10,}"),
    dict(slug="fsis", label="Food Safety and Inspection Service", url="https://www.fsis.usda.gov", group=SUB,
         client="browser", listing="https://www.fsis.usda.gov/news-events/news-press-releases",
         pattern=r"^/news-events/news-press-releases/[a-z0-9-]{10,}"),
    dict(slug="army", label="Army", url="https://www.army.mil", group=SUB, client="chrome",
         feeds=["https://www.army.mil/rss/static/1.xml"], pattern=r"^/article/\d+/"),
    _dvids("navy", "Navy", "https://www.navy.mil", "/Press-Office/Press-Releases/"),
    _dvids("airforce", "Air Force", "https://www.af.mil", "/News/Press-Releases/"),
    _dvids("marines", "Marine Corps", "https://www.marines.mil", "/News/Press-Releases/"),
    _dvids("spaceforce", "Space Force", "https://www.spaceforce.mil", "/News/"),
    _dvids("nationalguard", "National Guard", "https://www.nationalguard.mil", "/News/Press-Releases/"),

    dict(slug="fed", label="Federal Reserve", url="https://www.federalreserve.gov", group=REG,
         feeds=["https://www.federalreserve.gov/feeds/press_all.xml"],
         years="https://www.federalreserve.gov/newsevents/pressreleases/{year}-press.htm",
         pattern=r"^/newsevents/pressreleases/[a-z]+\d{8}[a-z]\.htm"),
    dict(slug="sec", label="SEC", url="https://www.sec.gov", group=REG, client="chrome",
         feeds=["https://www.sec.gov/news/pressreleases.rss"], listing="https://www.sec.gov/newsroom/press-releases",
         pattern=r"^/newsroom/press-releases/20\d\d-\d+"),
    dict(slug="ftc", label="FTC", url="https://www.ftc.gov", group=REG, client="chrome",
         feeds=["https://www.ftc.gov/feeds/press-release.xml"], listing="https://www.ftc.gov/news-events/news/press-releases",
         pattern=r"^/news-events/news/press-releases/20\d\d/\d\d/"),
    dict(slug="fcc", label="FCC", url="https://www.fcc.gov", group=REG, client="chrome",
         listing="https://www.fcc.gov/news-events/headlines",
         paged="https://www.fcc.gov/news-events/headlines?page={page}", first_page=1, pattern=r"^/document/[a-z0-9-]{10,}"),
    dict(slug="cfpb", label="CFPB", url="https://www.consumerfinance.gov", group=REG,
         feeds=["https://www.consumerfinance.gov/about-us/newsroom/feed/"], listing="https://www.consumerfinance.gov/about-us/newsroom/",
         pattern=r"^/about-us/newsroom/[a-z0-9-]{10,}/?$"),
    dict(slug="fdic", label="FDIC", url="https://www.fdic.gov", group=REG,
         listing="https://www.fdic.gov/news/press-releases", pattern=r"^/news/press-releases/20\d\d/"),
    dict(slug="cftc", label="CFTC", url="https://www.cftc.gov", group=REG,
         listing="https://www.cftc.gov/PressRoom/PressReleases", interval=3, pattern=r"^/PressRoom/PressReleases/\d+-\d+"),
    dict(slug="ferc", label="FERC", url="https://www.ferc.gov", group=REG,
         listing="https://www.ferc.gov/news-events/news", pattern=r"^/news-events/news/(?!news-releases-headlines)[a-z0-9-]{15,}"),
    dict(slug="nrc", label="Nuclear Regulatory Commission", url="https://www.nrc.gov", group=REG,
         years="https://www.nrc.gov/reading-rm/doc-collections/news/{year}/", pattern=r"^/about-nrc/news-releases/20\d\d/"),
    dict(slug="nlrb", label="NLRB", url="https://www.nlrb.gov", group=REG,
         feeds=["https://www.nlrb.gov/rss/rssPressReleases.xml"], pattern=r"^/news-outreach/news-story/"),
    dict(slug="eeoc", label="EEOC", url="https://www.eeoc.gov", group=REG,
         feeds=["https://www.eeoc.gov/rss/newsroom"], listing="https://www.eeoc.gov/newsroom/search",
         pattern=r"^/newsroom/(?!search)[a-z0-9-]{15,}"),
    dict(slug="fec", label="FEC", url="https://www.fec.gov", group=REG,
         listing="https://www.fec.gov/updates/?update_type=press-release", pattern=r"^/updates/(?!record-archive)[a-z0-9-]{15,}/?$"),
    dict(slug="cpsc", label="CPSC", url="https://www.cpsc.gov", group=REG,
         listing="https://www.cpsc.gov/Newsroom/News-Releases", pattern=r"^/Newsroom/News-Releases/20\d\d/"),
    dict(slug="ntsb", label="NTSB", url="https://www.ntsb.gov", group=REG,
         listing="https://www.ntsb.gov/news/press-releases/Pages/default.aspx", pattern=r"^/news/press-releases/Pages/NR\d+"),
    dict(slug="fhfa", label="FHFA", url="https://www.fhfa.gov", group=REG,
         listing="https://www.fhfa.gov/news/news-release", pattern=r"^/news/news-release/[a-z0-9-]{10,}"),
    dict(slug="ncua", label="NCUA", url="https://ncua.gov", group=REG,
         listing="https://ncua.gov/newsroom/press-releases", pattern=r"^/newsroom/press-release/20\d\d/"),
    dict(slug="fmc", label="Federal Maritime Commission", url="https://www.fmc.gov", group=REG,
         listing="https://www.fmc.gov/news/", pattern=r"^/articles/[a-z0-9-]{10,}"),
    dict(slug="stb", label="Surface Transportation Board", url="https://www.stb.gov", group=REG,
         feeds=["https://www.stb.gov/feed/"], listing="https://www.stb.gov/news-communications/latest-news/",
         pattern=r"^/news-communications/latest-news/pr-\d+-\d+"),
    dict(slug="usitc", label="U.S. International Trade Commission", url="https://www.usitc.gov", group=REG,
         listing="https://www.usitc.gov/news_releases", pattern=r"^/press_room/news_release/20\d\d/"),
    dict(slug="prc", label="Postal Regulatory Commission", url="https://www.prc.gov", group=REG,
         listing="https://www.prc.gov/press-releases", pattern=r"^/press-releases/[a-z0-9-]{10,}"),

    dict(slug="nasa", label="NASA", url="https://www.nasa.gov", group=IND,
         wp="https://www.nasa.gov/wp-json/wp/v2/press-release", pattern=r"^/news-release/"),
    dict(slug="usps", label="U.S. Postal Service", url="https://about.usps.com", group=IND,
         feeds=["https://about.usps.com/news/latestnews.rss"], pattern=r"^/newsroom/rssrequest\.htm",
         # The feed links to a redirect page; the release itself is at the path in ?nr=.
         text_url=(r"/newsroom/rssrequest\.htm\?nr=", "/newsroom/")),
    dict(slug="opm", label="Office of Personnel Management", url="https://www.opm.gov", group=IND,
         listing="https://www.opm.gov/news/news-releases/", pattern=r"^/news/news-releases/[a-z0-9-]{10,}"),
    dict(slug="gsa", label="General Services Administration", url="https://www.gsa.gov", group=IND,
         listing="https://www.gsa.gov/about-us/newsroom/news-releases", pattern=r"^/about-(us|gsa)/newsroom/news-releases/[a-z0-9-]{10,}"),
    dict(slug="nsf", label="National Science Foundation", url="https://www.nsf.gov", group=IND,
         feeds=["https://www.nsf.gov/rss/rss_www_news.xml"], listing="https://www.nsf.gov/news", pattern=r"^/news/[a-z0-9-]{10,}"),
    dict(slug="archives", label="National Archives", url="https://www.archives.gov", group=IND,
         listing="https://www.archives.gov/press/press-releases", pattern=r"^/press/press-releases/(20\d\d/)?nr\d\d-\d+"),
    dict(slug="exim", label="Export-Import Bank", url="https://www.exim.gov", group=IND,
         listing="https://www.exim.gov/news", pattern=r"^/news/[a-z0-9-]{15,}"),
    dict(slug="dfc", label="Development Finance Corporation", url="https://www.dfc.gov", group=IND,
         feeds=["https://www.dfc.gov/rss.xml"], listing="https://www.dfc.gov/media/press-releases",
         pattern=r"^/media/press-releases/[a-z0-9-]{10,}"),
    dict(slug="amtrak", label="Amtrak", url="https://media.amtrak.com", group=IND,
         wp="https://media.amtrak.com/wp-json/wp/v2/posts", pattern=r"^/20\d\d/\d\d/"),
    dict(slug="pbgc", label="Pension Benefit Guaranty Corporation", url="https://www.pbgc.gov", group=IND,
         listing="https://www.pbgc.gov/news", pattern=r"^/news/press/pr\d\d-\d+",
         ids=[("https://www.pbgc.gov/news/press/pr25-{n:03d}", 1, 80), ("https://www.pbgc.gov/news/press/pr26-{n:03d}", 1, 80)]),
    dict(slug="eac", label="Election Assistance Commission", url="https://www.eac.gov", group=IND,
         listing="https://www.eac.gov/news", pattern=r"^/news/20\d\d/\d\d/\d\d/"),
    dict(slug="osc", label="Office of Special Counsel", url="https://osc.gov", group=IND,
         listing="https://osc.gov/news", pattern=r"^/news/20\d\d-\d\d-\d\d/"),
    dict(slug="oge", label="Office of Government Ethics", url="https://www.oge.gov", group=IND,
         listing="https://www.oge.gov/web/oge.nsf/News%20Releases", pattern=r"(?i)^/web/oge\.nsf/News(%20| )Releases/[0-9A-F]{32}"),
    dict(slug="usccr", label="U.S. Commission on Civil Rights", url="https://www.usccr.gov", group=IND,
         listing="https://www.usccr.gov/news", pattern=r"^/news/20\d\d/"),
    dict(slug="nea", label="National Endowment for the Arts", url="https://www.arts.gov", group=IND,
         listing="https://www.arts.gov/news/press-releases", pattern=r"^/news/press-releases/20\d\d/"),
    dict(slug="neh", label="National Endowment for the Humanities", url="https://www.neh.gov", group=IND,
         listing="https://www.neh.gov/news/press-releases", pattern=r"^/news/[a-z0-9-]{15,}$"),
    dict(slug="usagm", label="U.S. Agency for Global Media", url="https://www.usagm.gov", group=IND,
         wp="https://www.usagm.gov/wp-json/wp/v2/posts", pattern=r"^/20\d\d/\d\d/\d\d/"),
    dict(slug="usip", label="U.S. Institute of Peace", url="https://www.usip.org", group=IND,
         feeds=["https://www.usip.org/feed/"], pattern=r"^/[a-z0-9-]{8,}/?$"),
    dict(slug="imls", label="Institute of Museum and Library Services", url="https://www.imls.gov", group=IND,
         client="browser", listing="https://www.imls.gov/", pattern=r"^/news/[a-z0-9-]{15,}"),

    dict(slug="gao", label="Government Accountability Office", url="https://www.gao.gov", group=LEG, client="chrome",
         feeds=["https://www.gao.gov/rss/reports.xml"], listing="https://www.gao.gov/press-center",
         pattern=r"^/(press-release|products)/[a-z0-9-]{6,}"),
    dict(slug="cbo", label="Congressional Budget Office", url="https://www.cbo.gov", group=LEG, client="browser",
         feeds=["https://www.cbo.gov/publications/all/rss.xml"], pattern=r"^/publication/\d+",
         ids=[("https://www.cbo.gov/publication/{n}", 60850, 63200)], max_misses=400),
    # CRS reports by way of EveryCRSReport.com, which republishes them (congress.gov blocks GitHub's servers).
    dict(slug="crs", label="Congressional Research Service", url="https://www.everycrsreport.com", group=LEG,
         feeds=["https://www.everycrsreport.com/rss.xml"], pattern=r"^/reports/[A-Z]+\d+"),
]


def is_agency(source_id: str) -> bool:
    return source_id.startswith(AGENCY_PREFIX)


def load_agencies() -> list[dict]:
    return [{
        "bioguide": AGENCY_PREFIX + a["slug"],
        "kind": "agency",
        "group": a.get("group", CABINET),
        "claims": [AGENCY_PREFIX + c for c in a.get("claims", [])],
        "name": a["label"],
        "last_name": a["label"],
        "party": "",
        "state": "",
        "district": "",
        "label": a["label"],
        "url": a["url"],
        "rss_url": None,
        "committees": [],
        "scraper": None,
    } for a in AGENCIES]


AGENCY_BY_ID = {AGENCY_PREFIX + a["slug"]: a for a in AGENCIES}
# Newsrooms that answer 429 (too many requests) at the usual pace.
HOST_INTERVAL = {urlsplit(a["url"]).netloc: a["interval"] for a in AGENCIES if a.get("interval")}


# ---------------------------------------------------------------- fetching

_locks: dict[str, threading.Lock] = {}
_last: dict[str, float] = {}
_sessions: dict[str, object] = {}
_session_lock = threading.Lock()


def session_for(source_id: str):
    """HTTP session for an agency's pages (shared by listing and text fetches)."""
    agency = AGENCY_BY_ID.get(source_id, {})
    client = agency.get("client", "plain")
    key = source_id  # one session per agency; get() keeps each agency's requests one at a time
    with _session_lock:
        if key not in _sessions:
            _sessions[key] = _new_session(client, agency)
        return _sessions[key]


def _new_session(client: str, agency: dict):
    if client == "plain":
        s = requests.Session()
        s.headers["User-Agent"] = HONEST_UA
        return s
    from curl_cffi import requests as creq

    s = creq.Session(impersonate="chrome")
    if client == "browser":
        _browser_agency[id(s)] = agency
        _load_cookies(s, agency)
    return s


_browser_agency: dict[int, dict] = {}  # browser-client sessions -> their agency
_refreshes: dict[int, int] = {}
MAX_REFRESHES = 4


def _load_cookies(s, agency: dict) -> bool:
    for attempt in range(3):
        try:
            ua, cookies = _browser_cookies(agency.get("listing") or agency["url"])
        except Exception as e:  # no browser available: plain impersonation may still work
            print(f"agencies: browser for {agency['slug']} failed: {type(e).__name__}: {e}", flush=True)
            continue
        if cookies is None:
            print(f"agencies: {agency['slug']} challenge not passed (try {attempt + 1})", flush=True)
            continue
        s.headers["User-Agent"] = ua
        for c in cookies:
            s.cookies.set(c["name"], c["value"], domain=c["domain"])
        return True
    return False


CHALLENGE_TITLES = re.compile(r"just a moment|service unavailable|access denied|attention required|^$", re.I)


def _browser_cookies(url: str):
    """Load a page in Chrome until its JavaScript challenge lets the real page through; return
    (UA, cookies), or (UA, None) if it never does."""
    import os

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # A visible window passes challenges that headless Chrome fails; CI provides one with xvfb-run.
        browser = p.chromium.launch(channel="chrome", headless=not os.environ.get("DISPLAY"),
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(locale="en-US", viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        passed = False
        for _ in range(30):  # up to about a minute
            page.wait_for_timeout(2000)
            try:
                if not CHALLENGE_TITLES.search(page.title().strip()) and len(page.content()) > 20000:
                    passed = True
                    break
            except Exception:  # navigating mid-check
                pass
        page.wait_for_timeout(2000)
        ua = page.evaluate("navigator.userAgent")
        cookies = ctx.cookies() if passed else None
        browser.close()
    return ua, cookies


def get(url: str, session):
    """GET, one request at a time per host with a pause between; None on any failure."""
    host = urlsplit(url).netloc
    with _session_lock:
        lock = _locks.setdefault(host, threading.Lock())
    with lock:
        wait = _last.get(host, 0) + HOST_INTERVAL.get(host, MIN_INTERVAL) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            r = session.get(url, timeout=30)
            # A JavaScript challenge again (its cookies expired): pass it again and retry once.
            if r.status_code in (202, 403) and id(session) in _browser_agency \
                    and _refreshes.get(id(session), 0) < MAX_REFRESHES:
                _refreshes[id(session)] = _refreshes.get(id(session), 0) + 1
                if _load_cookies(session, _browser_agency[id(session)]):
                    r = session.get(url, timeout=30)
        except Exception as e:
            fallback.statuses[type(e).__name__] += 1
            return None
        finally:
            _last[host] = time.monotonic()
    fallback.statuses[r.status_code] += 1
    if r.status_code != 200:
        return None
    try:  # pages that don't declare a charset are UTF-8 here, not requests' Latin-1 default
        if (r.encoding or "").lower() in ("", "iso-8859-1"):
            r.encoding = "utf-8"
    except Exception:
        pass
    return r if r.text else None


def fetch_page(url: str, source_id: str) -> dict:
    """Text, date and title of one agency press release (see textfetch.fetch_page)."""
    from .textfetch import extract_page

    rewrite = AGENCY_BY_ID.get(source_id, {}).get("text_url")
    fetch_url = re.sub(rewrite[0], rewrite[1], url) if rewrite else url
    r = get(fetch_url, session_for(source_id))
    if r is None:
        return {"text": None, "date": None, "title": None}
    page = extract_page(r.text, fetch_url)
    if len(page["text"] or "") < SHORT_TEXT:
        # Some newsrooms (e.g. Interior) confuse the extractor into returning the .gov banner.
        soup = BeautifulSoup(r.text, "lxml")
        body = soup.find("article") or soup.find("main")
        if body is not None:
            text = html_to_text(str(body))
            if len(text) > len(page["text"] or ""):
                page["text"] = text[:40_000]
    return page


# ---------------------------------------------------------------- scraping

def html_to_text(fragment: str) -> str:
    soup = BeautifulSoup(fragment or "", "lxml")
    for junk in soup.select("script, style, noscript, figure, img, svg"):
        junk.decompose()
    blocks = [" ".join(el.get_text(" ", strip=True).split()) for el in soup.find_all(["p", "li", "h2", "h3", "h4", "blockquote"])]
    text = "\n\n".join(b for b in blocks if b)
    return text or " ".join(soup.get_text(" ", strip=True).split())


def _wp(agency: dict, session, pages: int) -> list[dict]:
    per_page = WP_PAGE_SIZE if pages > 1 else 20
    items = []
    for page in range(1, max(pages, 1) + 1):
        params = {"per_page": per_page, "page": page, "after": f"{START_DATE}T00:00:00",
                  "_fields": "date,link,title,content", **agency.get("wp_params", {})}
        url = agency["wp"] + "?" + "&".join(f"{k}={v}" for k, v in params.items())
        r = get(url, session)
        if r is None:
            break
        try:
            posts = r.json()
        except ValueError:
            break
        if not isinstance(posts, list) or not posts:
            break
        for p in posts:
            title = html.unescape(BeautifulSoup((p.get("title") or {}).get("rendered", ""), "lxml").get_text(" ", strip=True))
            text = html_to_text((p.get("content") or {}).get("rendered", ""))
            items.append({"url": p["link"], "title": " ".join(title.split()),
                          "date": dt.date.fromisoformat(p["date"][:10]), "text": text or None})
        # Some sites ignore per_page (State and the UN mission return 10), so go on until a page
        # comes back empty or reaches back past START_DATE.
        if min(p["date"][:10] for p in posts) < START_DATE:
            break
    return [i for i in items if i["date"].isoformat() >= START_DATE]


def _numbered(agency: dict, source_id: str, known: set[str], pages: int) -> list[dict]:
    """Numbered release pages that exist (backfill only). Stops a range after 25 misses in a row."""
    items = []
    for template, first, last in agency["ids"]:
        misses, limit = 0, agency.get("max_misses", 25)
        for n in range(first, last + 1):
            url = template.format(n=n)
            if url in known:
                misses = 0
                continue
            page = fetch_page(url, source_id)
            if not page["title"] or not page["date"]:
                misses += 1
                if misses >= limit and n > first + limit:
                    break
                continue
            misses = 0
            title = re.split(r"\s+[|\u2013\u2014]\s+", page["title"])[0]  # drop " | U.S. Department of ..."
            items.append({"url": url, "title": title, "date": dt.date.fromisoformat(page["date"]),
                          "text": page["text"]})
    return [i for i in items if i["date"].isoformat() >= START_DATE]


def _doj_api(agency: dict, pattern, session, pages: int) -> list[dict]:
    """Justice Department API: every DOJ office's releases, oldest first, so read from the end."""
    size = 50
    session = session_for("plain")  # the API answers plain clients, not the Chrome fingerprint
    r = get(f"{agency['doj_api']}?pagesize=1", session)
    try:
        count = int(r.json()["metadata"]["resultset"]["count"])
    except Exception:
        return []
    items = []
    for page in range((count - 1) // size, max((count - 1) // size - pages * 4, -1), -1):
        r = get(f"{agency['doj_api']}?pagesize={size}&page={page}", session)
        try:
            rows = r.json()["results"]
        except Exception:
            break
        dates = []
        for row in rows:
            try:
                date = dt.datetime.fromtimestamp(int(row["date"]), dt.timezone.utc).date()
            except (KeyError, TypeError, ValueError):
                continue
            dates.append(date)
            if pattern.search(urlsplit(row.get("url") or "").path):
                items.append({"url": row["url"], "title": " ".join(html.unescape(row.get("title") or "").split()),
                              "date": date, "text": html_to_text(row.get("body") or "") or None})
        if dates and max(dates).isoformat() < START_DATE:
            break
    return [i for i in items if i["date"].isoformat() >= START_DATE]


def _scoped(page_html: str, scope: str | None) -> str:
    """Only the part of a listing page inside `scope` (a CSS selector), when the site's header or
    menus link to featured releases that would otherwise be read as part of every listing."""
    if not scope:
        return page_html
    part = BeautifulSoup(page_html, "lxml").select_one(scope)
    return str(part) if part is not None else page_html


def _listing_items(page_html: str, url: str, pattern, scope: str | None = None) -> list[dict]:
    """Release links on one listing page, with titles cleaned and dates taken from the address when missing."""
    raw = fallback._items_from(_scoped(page_html, scope), url, pattern)
    for i in raw:
        # "Read more about ..." and bare "Continue reading" links: the release page has the title.
        i["title"] = LINK_PREFIX.sub("", i["title"]).strip()
        if len(i["title"]) < fallback.MIN_TITLE or i["title"].lower().startswith(fallback.SKIP_TITLES):
            i["title"] = "Release " + urlsplit(i["url"]).path.rstrip("/").rsplit("/", 1)[-1]
            i["title_check"] = True
    found = fallback._filter(raw, fallback._host(url), pattern)
    for i in found:
        i["date"] = i["date"] or _url_date(i["url"])
    return found


def _listing_pages(start_url: str, pattern, session, pages: int, template: str | None = None, first: int = 0,
                   scope: str | None = None) -> list[dict]:
    """A listing page and, for a backfill, the older pages after it (by "next" links or `template`)."""
    items, url, seen, empty = [], start_url, set(), 0
    for k in range(max(pages, 1)):
        if url in seen:
            break
        seen.add(url)
        r = get(url, session)
        if r is None:
            break
        found = _listing_items(r.text, url, pattern, scope)
        new = [i for i in found if i["url"] not in {x["url"] for x in items}]
        items.extend(new)
        # Stop once a whole page is older than the start date (one pinned old release doesn't count).
        # Links without a date are kept; the release page dates them when its text is fetched.
        dated = [i["date"] for i in new if i["date"]]
        # A numbered page can hold only other offices' links (ATF lists many DOJ releases), so
        # numbered paging gives up after three such pages in a row rather than one.
        empty = 0 if new else empty + 1
        if empty >= (3 if template else 1) or (dated and max(dated).isoformat() < START_DATE):
            break
        url = template.format(page=first + k + 1) if template else fallback.next_page_url(r.text, url)
        if not url:
            break
    return items


URL_DATE = re.compile(r"/(20\d\d)/(\d\d)/(\d\d)/|/(20\d\d)(\d\d)(\d\d)/|/(20\d\d)-(\d\d)-(\d\d)/")


def _url_date(url: str):
    """A date written into a release's address (/2026/10/15/, /20261015/ or /2026-10-15/), if any."""
    m = URL_DATE.search(url)
    if not m:
        return None
    y, mo, d = [g for g in m.groups() if g]
    try:
        return dt.date(int(y), int(mo), int(d))
    except ValueError:
        return None


LINK_PREFIX = re.compile(r"^((more|read more|press release)\s*:\s*|read more about\s+)", re.I)


def scrape_agency(source: dict, pages: int = 1) -> tuple[list[dict], str | None, str]:
    """Return (items, error, method) like collect.scrape_member. Items may carry full text."""
    agency = AGENCY_BY_ID[source["bioguide"]]
    pattern = re.compile(agency["pattern"])
    host = fallback._host(agency["url"])
    session = session_for(source["bioguide"])
    backfill = pages > 1
    items, methods = [], []

    if agency.get("wp"):
        found = _wp(agency, session, pages)
        items += [i for i in found if pattern.search(urlsplit(i["url"]).path)]
        methods.append("wordpress")
    for feed in agency.get("feeds", []):
        url = feed.replace("{max}", "1000" if backfill else "30")
        r = get(url, session)
        if r is not None:
            items += [i for i in fallback._filter(fallback.from_rss(r.text, url), host, pattern) if i["date"]]
            methods.append("rss")
    if agency.get("paged_feed") and backfill:
        for page in range(1, pages + 1):
            url = agency["paged_feed"].format(page=page)
            r = get(url, session)
            if r is None:
                break
            # The feed mixes in posts that aren't press releases: stop on an empty page, not an unmatched one.
            entries = [i for i in fallback.from_rss(r.text, url) if i["date"]]
            if not entries:
                break
            items += fallback._filter(entries, host, pattern)
            if max(i["date"] for i in entries).isoformat() < START_DATE:
                break
        methods.append("rss pages")
    if agency.get("ids") and backfill:
        items += _numbered(agency, source["bioguide"], {i["url"] for i in items}, pages)
        methods.append("numbered")
    if agency.get("doj_api") and (backfill or agency.get("doj_hourly")):
        items += _doj_api(agency, pattern, session, pages)
        methods.append("api")
    scope = agency.get("scope")
    listings = [agency["listing"]] if agency.get("listing") else []
    if agency.get("years"):
        this_year = dt.date.today().year
        years = range(int(START_DATE[:4]), this_year + 1) if backfill else [this_year]
        listings += [agency["years"].format(year=y) for y in years]
    listings += agency.get("backfill", []) if backfill else []
    for url in listings:
        items += _listing_pages(url, pattern, session, pages, scope=scope)
        methods.append("listing")
    if agency.get("paged") and backfill:
        first = agency.get("first_page", 0)
        items += _listing_pages(agency["paged"].format(page=first), pattern, session, pages, agency["paged"], first,
                                scope=scope)
        methods.append("listing")

    skip = re.compile(agency["skip_titles"], re.I) if agency.get("skip_titles") else None
    out, seen = [], set()
    for i in items:
        i["url"] = i["url"].strip().replace("http://", "https://", 1)
        i["title"] = LINK_PREFIX.sub("", i["title"])
        key = store.normalize_url(i["url"])
        if key in seen or (skip and skip.search(i["title"])):
            continue
        seen.add(key)
        i.pop("feed", None)
        i.pop("categories", None)
        out.append(i)
    method = "+".join(dict.fromkeys(methods)) or "none"
    if out:
        return out, None, method
    return [], "no press releases found (blocked or page layout changed)", method
