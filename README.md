# House Democrats statements

A website and RSS feeds of every press statement House Democrats post on their official
house.gov sites, and every press release from the Democrats on each House committee
(democrats-judiciary.house.gov and the like). It updates every hour, groups statements about the same event automatically,
and is searchable by member and by the words in the statement.

**Site:** https://4jackryan4.github.io/house-statements/

- **Events**: when several members put out statements about the same thing (a court ruling, a
  shooting, a storm, a vote), those statements are grouped together. Most statements don't
  belong to an event, and that's fine: they're still in the feed and in search.
- **Search**: full text of every statement since January 2025, filterable by member, state and date.
- **Committees**: the Democratic sites of 20 standing committees plus the China select committee
  (`house_statements/committees.py`). They appear in the feed and search labeled by committee, can
  be filtered with "Committees only", and join events (an event still needs 4 members).
- **RSS**: all statements, committee statements, new events, and one feed per member and committee.

Everything is free to run: GitHub Actions does the work and GitHub Pages hosts the site.
Nothing uses a paid API.

## How it works

Every hour, `.github/workflows/update.yml`:

1. Refreshes the list of House Democrats from
   [unitedstates/congress-legislators](https://github.com/unitedstates/congress-legislators)
   (`house_statements/members.py`).
2. Imports House Democrats' statements from the
   [congress-press](https://github.com/dwillis/congress-press) archive, which collects all
   member sites once a day (`import_archive.py`, runs about every 6 hours). This is how the
   history back to January 2025 was loaded. It also fills gaps if the hourly scrape misses
   something.
3. Scrapes each member's press release page with
   [python-statement](https://github.com/dwillis/python-statement) and fetches the text of new
   statements (`collect.py`). `data/health.json` lists any member site that returned nothing or
   errored, which usually means the site was redesigned and its scraper needs an update upstream.
   Committee sites are read with our generic scraper (`fallback.py`), starting from the press
   release page listed in `committees.py`.
4. Commits new statements to `data/statements/YYYY/YYYY-MM.jsonl`.
5. Groups statements into events (`events.py`), builds the site and RSS feeds
   (`build_site.py`) and the [Pagefind](https://pagefind.app) search index
   (`scripts/build_index.mjs`), and publishes to GitHub Pages.

### Event grouping

Each statement's headline and first paragraph become a TF-IDF vector, with member names and
press release boilerplate removed. Statements are walked in date order. Each one joins the most
similar group that has been active in the last 4 days, or starts a new group. A second pass
merges groups that tell the same story over a longer stretch. A group becomes an event once
statements from 4 or more different members are in it. The label is the phrase that best
distinguishes the group's headlines (for example "Birthright Citizenship" or "Lorenzo Salgado
Araujo").

The settings are at the top of `house_statements/events.py`. `EVENT_MIN_MEMBERS` (an
environment variable, default 4) controls how many members make an event.

## Run it locally

```sh
python3.12 -m venv .venv && . .venv/bin/activate
git clone https://github.com/dwillis/python-statement .vendor/python-statement
pip install -r requirements.txt ./.vendor/python-statement
npm ci

PYTHON_STATEMENT_DIR=.vendor/python-statement python -m house_statements.members
python -m house_statements.import_archive          # last 2 months; --since 2025-01 for everything
python -m house_statements.collect                 # scrape member sites
python -m house_statements.build_site && node scripts/build_index.mjs
python -m http.server -d site 8000                 # http://localhost:8000
python -m pytest
```

To include Republicans later, set `PARTIES=Democrat,Republican` in the workflow and rerun
`import_archive --since 2025-01`.
