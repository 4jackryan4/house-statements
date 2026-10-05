"""Group statements about the same event (a ruling, a shooting, a storm, a vote) together.

Free and deterministic, no AI service: each statement becomes a TF-IDF vector of its headline
and opening paragraph (member names and press-release boilerplate removed). Statements are
walked in date order; each joins the most similar recent group, or starts a new one. Groups
that pick up statements from several different members within a few days are events. A second
pass merges groups that are clearly the same story told over a longer stretch (follow-ups).
"""

import datetime as dt
import re
from collections import Counter, defaultdict

import numpy as np
from scipy import sparse
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer

from .config import EVENT_MIN_MEMBERS

JOIN_THRESHOLD = 0.30      # similarity to a group's centroid needed to join it
WINDOW_DAYS = 4            # a group stays open this long after its latest statement
MERGE_THRESHOLD = 0.40     # centroid similarity at which two groups are the same story
MERGE_GAP_DAYS = 14        # ...if they are no more than this far apart
LEAD_CHARS = 700

PRESS_WORDS = """
congressman congresswoman congressmember rep reps representative representatives today announced
announces announce statement statements said says district washington house democrat democrats
democratic member members congress congressional press release releases office joined joins join
colleagues colleague leader leaders act bill bills legislation introduced introduce introduces
reintroduces issued issue issues american americans federal news icymi watch video week year
""".split()

TOKEN_PATTERN = r"(?u)\b[a-zA-Z][a-zA-Z0-9'\-]{2,}\b"
DATELINE = re.compile(r"^[^\n]{0,90}?\s[–—-]{1,2}\s")


def _stop_words(members: list[dict]) -> list[str]:
    names = set()
    for m in members:
        names.update(t.lower() for t in re.findall(r"[A-Za-z]+", m["name"]))
    return sorted(ENGLISH_STOP_WORDS | names | set(PRESS_WORDS))


def _document(r: dict) -> str:
    lead = DATELINE.sub("", (r.get("text") or "")[:LEAD_CHARS], count=1)
    return f"{r['title']} {r['title']} {lead}"


def _days(date: str) -> int:
    return dt.date.fromisoformat(date).toordinal()


class _Group:
    __slots__ = ("idx", "first", "last", "sum")

    def __init__(self, i, day, vec):
        self.idx, self.first, self.last, self.sum = [i], day, day, vec


def _online_groups(X, days: list[int]) -> list[_Group]:
    """Single pass in date order; X rows are L2-normalised sparse vectors.

    Open groups keep a dense running sum in a preallocated table so each statement is compared
    with every open group in one vectorised step. Closed groups are stored sparse.
    """
    n_features = X.shape[1]
    indptr, indices, data = X.indptr, X.indices, X.data.astype(np.float32)
    done: list[_Group] = []
    active: list[_Group] = []
    sums = np.zeros((64, n_features), dtype=np.float32)
    norms = np.zeros(64, dtype=np.float32)
    current_day = None

    for i in range(X.shape[0]):
        day = days[i]
        n = len(active)
        if day != current_day:
            keep = [k for k, g in enumerate(active) if day - g.last <= WINDOW_DAYS]
            if len(keep) < n:
                for k, g in enumerate(active):
                    if day - g.last > WINDOW_DAYS:
                        g.sum = sparse.csr_matrix(sums[k])
                        done.append(g)
                sums[: len(keep)] = sums[keep]
                norms[: len(keep)] = norms[keep]
                active = [active[k] for k in keep]
                n = len(active)
            current_day = day

        cols, vals = indices[indptr[i]:indptr[i + 1]], data[indptr[i]:indptr[i + 1]]
        best, best_sim = -1, 0.0
        if n and len(cols):
            sims = (sums[:n, cols] @ vals) / np.maximum(norms[:n], 1e-9)
            best = int(np.argmax(sims))
            best_sim = float(sims[best])
        if best >= 0 and best_sim >= JOIN_THRESHOLD:
            g = active[best]
            g.idx.append(i)
            g.last = max(g.last, day)
            sums[best, cols] += vals
            norms[best] = np.linalg.norm(sums[best, :])
        else:
            if n == len(sums):
                sums = np.vstack([sums, np.zeros_like(sums)])
                norms = np.concatenate([norms, np.zeros_like(norms)])
            sums[n] = 0
            sums[n, cols] = vals
            norms[n] = np.linalg.norm(vals)
            active.append(_Group(i, day, None))

    for k, g in enumerate(active):
        g.sum = sparse.csr_matrix(sums[k])
    return done + active


def _merge_groups(groups: list[_Group]) -> list[list[int]]:
    """Union groups (2+ statements) whose centroids match and whose dates are close."""
    multi = [g for g in groups if len(g.idx) > 1]
    parent = list(range(len(multi)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    if multi:
        cents = sparse.vstack([g.sum for g in multi]).tocsr()
        norms = np.sqrt(np.asarray(cents.multiply(cents).sum(axis=1)).ravel())
        cents = sparse.diags(1 / np.maximum(norms, 1e-9)) @ cents
        sim = (cents @ cents.T).toarray()
        order = sorted(range(len(multi)), key=lambda k: multi[k].first)
        for a_pos, a in enumerate(order):
            for b in order[a_pos + 1:]:
                if multi[b].first - multi[a].last > MERGE_GAP_DAYS:
                    break
                if sim[a, b] >= MERGE_THRESHOLD:
                    parent[find(b)] = find(a)

    merged = defaultdict(list)
    for k, g in enumerate(multi):
        merged[find(k)].extend(g.idx)
    singles = [g.idx for g in groups if len(g.idx) == 1]
    return list(merged.values()) + singles


# ---------------------------------------------------------------- labels

LABEL_STOP = set(ENGLISH_STOP_WORDS) | {
    "statement", "statements", "rep", "reps", "congressman", "congresswoman", "joins", "join", "joined",
    "colleagues", "applauds", "condemns", "slams", "celebrates", "announces", "introduces", "demands",
    "urges", "calls", "leads", "votes", "vote", "following", "news", "release", "press", "re",
    "urge", "push", "pushing", "pushes", "demand", "demanding", "block", "blocks", "stop", "stops",
    "protect", "protects", "reverse", "lead", "leading", "take", "takes", "distribute", "congratulate",
    "congratulates", "submit", "submits", "receive", "receives", "secure", "secures", "help", "helps",
    "pass", "passes", "passage", "honor", "honors", "marks", "mark", "launch", "launches", "seek",
    "seeks", "decision", "democrats", "democratic", "house", "delegation", "members", "member",
    "bipartisan", "lawmakers", "colleague", "trump's", "trump’s", "administration's", "administration’s",
    "new", "bill", "act's", "says", "said", "speaks", "remarks", "responds", "response", "reacts",
}
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’.\-]*")


def _phrases(title: str, max_n: int = 4):
    words = WORD.findall(title)
    seen = set()
    for n in range(max_n, 0, -1):
        for k in range(len(words) - n + 1):
            gram = words[k:k + n]
            low = tuple(w.lower().strip(".’'") for w in gram)
            if low[0] in LABEL_STOP or low[-1] in LABEL_STOP or low in seen:
                continue
            if n == 1 and (len(low[0]) < 3 or low[0].isdigit()):
                continue
            seen.add(low)
            yield low, " ".join(gram)


def build_label_index(titles: list[str], name_words: set[str]):
    """Document frequency of each phrase across all headlines (to down-weight generic phrases)."""
    df = Counter()
    for t in titles:
        df.update({low for low, _ in _phrases(t)})
    return df, len(titles), name_words


def label_for(titles: list[str], label_index) -> str:
    df, total, name_words = label_index
    counts, surface = Counter(), defaultdict(Counter)
    for t in titles:
        for low, text in _phrases(t):
            if any(w in name_words for w in low):
                continue
            counts[low] += 1
            surface[low][text] += 1
    if not counts:
        return titles[0]
    n = len(titles)

    def score(low):
        coverage = counts[low] / n
        rarity = np.log(total / (1 + df[low]))
        content_words = sum(1 for w in low if w not in ENGLISH_STOP_WORDS)
        return coverage * rarity**2 * (1 + 0.5 * (content_words - 1))

    best = max(counts, key=score)
    # Prefer the spelling used in normally-cased headlines (keeps acronyms like USMCA intact).
    forms = surface[best].most_common()
    text = next((f for f, _ in forms if not f.isupper()), forms[0][0])
    # Headlines are often ALL CAPS or Title Case; present the phrase in Title Case.
    if text.isupper() or text.islower():
        text = " ".join(w if len(w) <= 5 else w.capitalize() for w in text.split())
    return text.strip(" .,:;–—-'’\"“”")


# ---------------------------------------------------------------- entry point

def find_events(records: list[dict], members: list[dict]) -> list[dict]:
    """Return events (newest first). Each has id, label, headline, member_count, first, last, statement_ids."""
    docs = sorted((r for r in records if r.get("date")), key=lambda r: (r["date"], r["id"]))
    if len(docs) < 2:
        return []
    vectorizer = TfidfVectorizer(
        stop_words=_stop_words(members), ngram_range=(1, 2), min_df=2, max_df=max(0.03, 50 / len(docs)),
        sublinear_tf=True, token_pattern=TOKEN_PATTERN, max_features=30_000, dtype=np.float32,
    )
    try:
        X = vectorizer.fit_transform([_document(r) for r in docs]).tocsr()
    except ValueError:  # too few statements to have any shared vocabulary
        return []
    days = [_days(r["date"]) for r in docs]
    groups = _merge_groups(_online_groups(X, days))

    name_words = set()
    for m in members:
        name_words.update(t.lower() for t in re.findall(r"[A-Za-z]+", m["name"]) if len(t) > 2)
    label_index = build_label_index([r["title"] for r in docs], name_words)

    events = []
    for idx in groups:
        members_in = {docs[i]["bioguide"] for i in idx}
        if len(members_in) < EVENT_MIN_MEMBERS:
            continue
        idx.sort(key=lambda i: (docs[i]["date"], docs[i]["id"]))
        rows = [docs[i] for i in idx]
        centroid = np.asarray(X[idx].sum(axis=0)).ravel()
        sims = X[idx] @ centroid
        headline = rows[int(np.argmax(sims))]["title"]
        events.append({
            "id": rows[0]["id"],
            "label": label_for([r["title"] for r in rows], label_index),
            "headline": headline,
            "member_count": len(members_in),
            "statement_count": len(rows),
            "first": rows[0]["date"],
            "last": rows[-1]["date"],
            "statement_ids": [r["id"] for r in rows],
        })
    events.sort(key=lambda e: (e["last"], e["member_count"]), reverse=True)
    return events
