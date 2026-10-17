// Front end for the Members, Committees and Agencies tabs and the event archive. Data comes from ./data/*.json (built hourly) and the
// Pagefind index in ./pagefind/ (full-text search with member/state/date filters).

const PAGE_SIZE = 20;
const $ = (sel) => document.querySelector(sel);

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const fmtDate = (iso) =>
  new Date(iso + "T12:00:00Z").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

function fmtRange(first, last) {
  if (first === last) return fmtDate(first);
  const a = new Date(first + "T12:00:00Z"), b = new Date(last + "T12:00:00Z");
  const sameYear = a.getUTCFullYear() === b.getUTCFullYear();
  const left = a.toLocaleDateString("en-US", { month: "short", day: "numeric", ...(sameYear ? {} : { year: "numeric" }), timeZone: "UTC" });
  return `${left} – ${fmtDate(last)}`;
}

const daysAgo = (iso) => Math.floor((Date.now() - new Date(iso + "T12:00:00Z")) / 86400000);

async function getJSON(name) {
  const res = await fetch(`data/${name}`);
  if (!res.ok) throw new Error(`${name}: ${res.status}`);
  return res.json();
}

let membersById = new Map();
async function loadMembers() {
  const rows = await getJSON("members.json");
  membersById = new Map(rows.map((m) => [m.bioguide, m]));
  return rows;
}

// Each kind of source has its own tab.
const TAB_FOR = { member: "./", committee: "committees.html", agency: "agencies.html" };
const kindOf = (id) => membersById.get(id)?.kind || "member";
const tabLink = (ids, extra = "") => {
  const kinds = new Set(ids.map(kindOf));
  const qs = ids.map((id) => `member=${encodeURIComponent(id)}`).join("&");
  return `${TAB_FOR[kinds.size === 1 ? [...kinds][0] : "member"]}?${qs}${kinds.size > 1 ? "&type=all" : ""}${extra}`;
};

// One statement row. `s` has title, url, date, member (bioguide), optional event, excerpt.
function statementHTML(s, { showEvent = true } = {}) {
  const m = membersById.get(s.member);
  const who = m ? `<a class="who" href="${tabLink([m.bioguide])}">${esc(m.label)}</a>` : "";
  let ev = "";
  if (showEvent && s.event) {
    const label = s.eventLabel;
    if (label) ev = `<a class="ev-chip" href="events.html#${esc(s.event)}">${esc(label)}${s.eventMembers ? ` · ${esc(s.eventMembers)} members` : ""}</a>`;
  }
  const excerpt = s.excerpt ? `<p class="excerpt">${s.excerpt}</p>` : "";
  return `<li class="st">
    <a class="st-title" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>
    <div class="st-meta">${who}<time datetime="${esc(s.date)}">${fmtDate(s.date)}</time>${ev}</div>${excerpt}
  </li>`;
}

function eventHTML(e) {
  const active = daysAgo(e.last) <= 2 ? `<span class="live">Active</span>` : "";
  const items = (e.statements || []).map((s) => statementHTML(s, { showEvent: false })).join("");
  const memberIds = [...new Set((e.statements || []).map((s) => s.member))];
  const filterLink = tabLink(memberIds);
  const nc = e.committee_count || 0, na = e.agency_count || 0;
  const committees = (nc ? ` + ${nc} committee${nc === 1 ? "" : "s"}` : "") + (na ? ` + ${na} agenc${na === 1 ? "y" : "ies"}` : "");
  return `<details class="event" id="${esc(e.id)}">
    <summary>
      <span class="event-label">${esc(e.label)}</span>
      <span class="event-meta"><span class="count">${e.member_count} members${committees}</span>${fmtRange(e.first, e.last)}${active}</span>
      <span class="event-headline">${esc(e.headline)}</span>
    </summary>
    <ol class="statements">${items}</ol>
    <div class="event-actions">
      <a href="./?q=${encodeURIComponent(e.label)}">Search for more on “${esc(e.label)}”</a>
      <a href="${filterLink}">All statements from these ${e.member_count} members${committees}</a>
    </div>
  </details>`;
}

async function setBuilt() {
  try {
    const b = await getJSON("build.json");
    const when = new Date(b.built_at).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
    const agencies = b.agencies ? `, plus ${b.agency_statements.toLocaleString()} press releases from ${b.agencies} federal agencies` : "";
    $("#built").textContent = `Last updated ${when}. ${b.statements.toLocaleString()} statements from ${b.members} members${b.committees ? ` and ${b.committees} committees` : ""}${agencies} since January 2025.`;
  } catch {}
}

/* ------------------------------------------------------------------ tabs */

// The directory of one kind of source: members by state, agencies by group, committees in one list.
function directoryHTML(rows, text) {
  const groups = new Map();
  rows.forEach((m) => {
    const key = m.kind === "committee" ? "" : m.kind === "agency" ? (m.group || "Cabinet agencies") : m.state;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(m);
  });
  if (!rows.length) return `<p class="empty">Nothing matches “${esc(text)}”.</p>`;
  return [...groups].map(([g, ms]) => `<section class="state-block">${g ? `<h2 class="month">${esc(g)}</h2>` : ""}<div class="member-grid">${
    ms.map((m) => `<div class="member">
      <div class="member-name">${esc(m.name)}</div>
      <div class="member-sub">${m.district ? `${esc(m.district)} · ` : ""}${m.recent} statement${m.recent === 1 ? "" : "s"} in the last 90 days</div>
      ${m.committees.length && m.kind !== "committee" ? `<div class="member-sub">${esc(m.committees.join(", "))}</div>` : ""}
      <div class="member-links"><a href="${tabLink([m.bioguide])}">Statements</a><a href="feeds/members/${esc(m.bioguide)}.xml">RSS</a>${m.url ? `<a href="${esc(m.url)}" target="_blank" rel="noopener">Website</a>` : ""}</div>
    </div>`).join("")}</div></section>`).join("");
}

async function tabPage() {
  const kind = document.body.dataset.kind;
  const all = await loadMembers();
  const params = new URLSearchParams(location.search);

  // Old links (?type=committee or ?type=agency on the search page) go to that tab.
  const legacy = params.get("type");
  if (legacy && legacy !== kind && TAB_FOR[legacy]) {
    params.delete("type");
    location.replace(`${TAB_FOR[legacy]}${params.toString() ? `?${params}` : ""}`);
    return;
  }

  const views = [...document.querySelectorAll("[data-view]")].filter((el) => el.tagName === "BUTTON").map((b) => b.dataset.view);
  const state = {
    q: params.get("q") || "",
    members: params.getAll("member").filter((id) => membersById.has(id)),
    st: params.get("state") || "",
    when: params.get("when") || "",
    type: params.get("type") === "all" ? "all" : kind,
    sort: params.get("sort") || "date",
    view: views.includes(params.get("view")) ? params.get("view") : views[0],
  };
  if (state.members.some((id) => kindOf(id) !== kind)) state.type = "all";

  // Filter controls. The name list follows the scope: this tab's sources, or everything.
  const pickable = () => (state.type === "all" ? all : all.filter((m) => (m.kind || "member") === kind));
  const fillNames = () => { $("#member-list").innerHTML = pickable().map((m) => `<option value="${esc(m.label)}"></option>`).join(""); };
  fillNames();
  if ($("#state")) {
    const states = [...new Set(all.filter((m) => !m.kind).map((m) => m.state).filter(Boolean))].sort();
    $("#state").insertAdjacentHTML("beforeend", states.map((s) => `<option>${esc(s)}</option>`).join(""));
    $("#state").value = state.st;
  } else state.st = "";
  $("#q").value = state.q;
  $("#when").value = state.when;
  $("#type").value = state.type;

  const renderChips = () => {
    $("#member-chips").innerHTML = state.members
      .map((id) => `<span class="chip">${esc(membersById.get(id).label)}<button type="button" data-remove="${esc(id)}" aria-label="Remove">×</button></span>`)
      .join("");
  };
  renderChips();

  const pickMember = () => {
    const input = $("#member-input");
    const text = input.value.trim().toLowerCase();
    if (!text) return;
    const choices = pickable();
    let m = choices.find((x) => x.label.toLowerCase() === text);
    if (!m) {
      const matches = choices.filter((x) => x.label.toLowerCase().includes(text));
      if (matches.length === 1) m = matches[0];
    }
    if (m && !state.members.includes(m.bioguide)) {
      state.members.push(m.bioguide);
      input.value = "";
      renderChips();
      run();
    }
  };
  $("#member-input").addEventListener("change", pickMember);
  $("#member-input").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); pickMember(); } });
  $("#member-chips").addEventListener("click", (ev) => {
    const id = ev.target.dataset.remove;
    if (!id) return;
    state.members = state.members.filter((x) => x !== id);
    renderChips();
    run();
  });

  let timer;
  $("#q").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { state.q = $("#q").value.trim(); run(); }, 250); });
  $("#search").addEventListener("submit", (ev) => { ev.preventDefault(); state.q = $("#q").value.trim(); run(); });
  $("#state")?.addEventListener("change", () => { state.st = $("#state").value; run(); });
  $("#when").addEventListener("change", () => { state.when = $("#when").value; run(); });
  $("#type").addEventListener("change", () => {
    state.type = $("#type").value;
    if (state.type !== "all") state.members = state.members.filter((id) => kindOf(id) === kind);
    fillNames(); renderChips(); run();
  });
  document.querySelectorAll("[data-sort]").forEach((b) =>
    b.addEventListener("click", () => { state.sort = b.dataset.sort; run(); }));
  $("#clear").addEventListener("click", () => {
    Object.assign(state, { q: "", members: [], st: "", when: "", type: kind });
    $("#q").value = ""; if ($("#state")) $("#state").value = ""; $("#when").value = ""; $("#type").value = kind;
    fillNames(); renderChips(); run();
  });
  document.querySelectorAll("button[data-view]").forEach((b) =>
    b.addEventListener("click", () => { state.view = b.dataset.view; showView(); run(); }));

  // Pagefind loads lazily, the first time a search is needed.
  let pagefind;
  const getPagefind = async () => {
    if (!pagefind) {
      pagefind = await import("./pagefind/pagefind.js");
      await pagefind.options({ excerptLength: 28 });
      await pagefind.init();
    }
    return pagefind;
  };

  let runId = 0;
  async function run() {
    const id = ++runId;
    const qs = new URLSearchParams();
    if (state.q) qs.set("q", state.q);
    state.members.forEach((m) => qs.append("member", m));
    if (state.st) qs.set("state", state.st);
    if (state.when) qs.set("when", state.when);
    if (state.type === "all") qs.set("type", "all");
    if (state.sort !== "date") qs.set("sort", state.sort);
    if (state.view !== views[0]) qs.set("view", state.view);
    history.replaceState(null, "", qs.toString() ? `?${qs}` : location.pathname);

    const searching = state.q || state.members.length || state.st || state.when || state.type === "all";
    $("#home").hidden = !!searching;
    $("#results").hidden = !searching;
    if (!searching) return;

    document.querySelectorAll("[data-sort]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.sort === state.sort)));
    $("#result-count").textContent = "Searching…";
    $("#result-list").innerHTML = "";
    $("#more").hidden = true;

    // Name filter without search words: use the per-source lists (instant) instead of the index.
    if (!state.q && state.members.length) {
      const maxAge = { "Past 3 days": 3, "Past week": 7, "Past month": 31, "Past 3 months": 92 }[state.when] ?? Infinity;
      const lists = await Promise.all(state.members.map((m) => getJSON(`members/${m}.json`)));
      if (id !== runId) return;
      const rows = lists.flat()
        .filter((s) => daysAgo(s.date) <= maxAge && (!state.st || membersById.get(s.member)?.state === state.st) &&
          (state.type === "all" || kindOf(s.member) === state.type))
        .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
      $("#result-count").textContent = rows.length ? `${rows.length.toLocaleString()} statement${rows.length === 1 ? "" : "s"}` : "No statements match";
      let shown = 0;
      const showMore = () => {
        const slice = rows.slice(shown, shown + PAGE_SIZE);
        shown += slice.length;
        $("#result-list").insertAdjacentHTML("beforeend", slice.map((s) => statementHTML(s)).join(""));
        $("#more").hidden = shown >= rows.length;
      };
      $("#more").onclick = showMore;
      showMore();
      return;
    }

    const pf = await getPagefind();
    const filters = {};
    if (state.members.length) filters.member = { any: state.members.map((x) => membersById.get(x).label) };
    if (state.st) filters.state = state.st;
    if (state.when) filters.when = state.when;
    if (state.type !== "all") filters.type = state.type;
    const opts = { filters };
    if (state.sort === "date" || !state.q) opts.sort = { date: "desc" };
    const search = await pf.search(state.q || null, opts);
    if (id !== runId || !search) return;

    const n = search.results.length;
    $("#result-count").textContent = n ? `${n.toLocaleString()} statement${n === 1 ? "" : "s"}` : "No statements match";
    let shown = 0;
    const showMore = async () => {
      const slice = search.results.slice(shown, shown + PAGE_SIZE);
      shown += slice.length;
      const rows = await Promise.all(slice.map((r) => r.data()));
      if (id !== runId) return;
      $("#result-list").insertAdjacentHTML("beforeend", rows.map((d) => statementHTML({
        title: d.meta.title, url: d.url, date: d.meta.date, member: d.meta.bioguide,
        event: d.meta.event_id, eventLabel: d.meta.event, eventMembers: d.meta.event_members,
        excerpt: state.q ? d.excerpt.replace(/<(?!\/?mark>)[^>]*>/g, "") : "",
      })).join(""));
      $("#more").hidden = shown >= n;
    };
    $("#more").onclick = showMore;
    await showMore();
  }

  // Views when nothing is searched: events (Members tab only), latest, and the directory.
  // Each view's data loads the first time it is shown.
  const loaded = new Set();
  const loaders = {
    async events() {
      const events = await getJSON("events-recent.json");
      // Bigger and more recent events first: member count, halved for every week since the last statement.
      const weight = (e) => e.member_count * Math.pow(0.5, daysAgo(e.last) / 7);
      events.sort((a, b) => weight(b) - weight(a));
      let evShown = 0;
      const moreEvents = () => {
        const slice = events.slice(evShown, evShown + 8);
        if (evShown === 0) $("#events").innerHTML = slice.length ? "" : `<p class="empty">No events in the last few weeks.</p>`;
        evShown += slice.length;
        $("#events").insertAdjacentHTML("beforeend", slice.map(eventHTML).join(""));
        $("#events-more").hidden = evShown >= events.length;
      };
      $("#events-more").onclick = moreEvents;
      moreEvents();
    },
    async latest() {
      const latest = await getJSON(`latest-${kind}.json`);
      let latestShown = 0;
      const moreLatest = () => {
        const slice = latest.slice(latestShown, latestShown + 25);
        latestShown += slice.length;
        $("#latest").insertAdjacentHTML("beforeend", slice.map((s) => statementHTML(s)).join(""));
        $("#latest-more").hidden = latestShown >= latest.length;
      };
      $("#latest-more").onclick = moreLatest;
      moreLatest();
    },
    async browse() {
      const rows = all.filter((m) => (m.kind || "member") === kind);
      const render = (text) => {
        const t = text.trim().toLowerCase();
        const match = rows.filter((m) =>
          !t || m.label.toLowerCase().includes(t) || m.state.toLowerCase() === t || m.committees.some((c) => c.toLowerCase().includes(t)));
        $("#member-directory").innerHTML = directoryHTML(match, text);
      };
      render("");
      $("#member-filter").addEventListener("input", (ev) => render(ev.target.value));
    },
  };
  function showView() {
    document.querySelectorAll("button[data-view]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === state.view)));
    document.querySelectorAll("section[data-view]").forEach((s) => { s.hidden = s.dataset.view !== state.view; });
    if (!loaded.has(state.view)) {
      loaded.add(state.view);
      loaders[state.view]().catch((err) => {
        console.error(err);
        document.querySelector(`section[data-view="${state.view}"]`).insertAdjacentHTML("beforeend", `<p class="empty">Something went wrong loading this.</p>`);
      });
    }
  }
  showView();
  run();
}

/* ------------------------------------------------------------------ events page */

async function eventsPage() {
  await loadMembers();
  const events = await getJSON("events.json");
  const box = $("#event-archive");

  const render = (text) => {
    const t = text.trim().toLowerCase();
    const match = (e) =>
      !t || e.label.toLowerCase().includes(t) || e.headline.toLowerCase().includes(t) ||
      e.statements.some((s) => s.title.toLowerCase().includes(t) || membersById.get(s.member)?.label.toLowerCase().includes(t));
    let html = "", month = "";
    let count = 0;
    for (const e of events) {
      if (!match(e)) continue;
      const m = new Date(e.last + "T12:00:00Z").toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
      if (m !== month) { html += `${month ? "</div>" : ""}<h2 class="month">${esc(m)}</h2><div class="events">`; month = m; }
      html += eventHTML(e);
      count++;
    }
    box.innerHTML = count ? html + "</div>" : `<p class="empty">No events match “${esc(text)}”.</p>`;
  };
  render("");
  $("#event-filter").addEventListener("input", (ev) => render(ev.target.value));

  const openTarget = () => {
    const el = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (el) { el.open = true; el.classList.add("flash"); el.scrollIntoView({ block: "start" }); }
  };
  openTarget();
  window.addEventListener("hashchange", openTarget);
}

/* ------------------------------------------------------------------ boot */

setBuilt();
const page = document.body.dataset.page;
const pages = { tab: tabPage, events: eventsPage };
pages[page]?.().catch((err) => {
  console.error(err);
  const target = document.querySelector(".loading") || document.querySelector("main");
  target.insertAdjacentHTML("beforeend", `<p class="empty">Something went wrong loading this page.</p>`);
});
