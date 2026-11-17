"use strict";

(function () {
  const $ = (id) => document.getElementById(id);
  const state = {
    hours: 24,
    sort: "clients",
    desc: true,
    filter: new URLSearchParams(location.search).get("filter") || "",
    hideEmpty: false,
    ap: null,
    data: null,
    request: 0,
  };
  $("filter").value = state.filter;

  function tiles(data) {
    const latest = data.latest;
    const filtered = Boolean(data.filter);
    const withClients = latest.filter((r) => r.clients > 0).length;
    const clients = latest.reduce((sum, r) => sum + r.clients, 0);
    $("t-clients-label").textContent = filtered ? "Connected clients, filtered" : "Connected clients";
    $("t-clients").textContent = data.latest_total ? ccc.int(clients) : "–";
    $("t-clients-note").textContent = filtered ? data.filter : " ";
    $("t-aps").textContent = data.latest_total ? ccc.int(latest.length) : "–";
    $("t-aps-note").textContent = !data.latest_total ? " "
      : filtered ? `of ${ccc.int(data.latest_total)}, ${ccc.int(withClients)} with clients`
        : `${ccc.int(withClients)} with clients`;

    const live = new Set();
    for (const s of data.status.streams) {
      const limit = Math.max((s.period || 300) * 2.5, 120);
      if (s.last_seen_age !== null && s.last_seen_age < limit) live.add(s.wlc);
    }
    $("t-wlcs").textContent = live.size;
    $("t-wlcs-note").textContent = live.size ? [...live].join(", ") : "none right now";

    const last = data.database.last_cycle;
    $("t-last").textContent = last ? ccc.time(last, false) : "–";
    $("t-last-note").textContent = last ? ccc.age((Date.now() - new Date(last)) / 1000) : " ";
    $("waiting").hidden = Boolean(last);
  }

  async function chart() {
    const to = Date.now(), from = to - state.hours * 3600000;
    if (state.ap) {
      const history = await ccc.get(
        `/api/ap-history?wlc=${encodeURIComponent(state.ap.wlc)}&ap=${encodeURIComponent(state.ap.ap_name)}&hours=${state.hours}`);
      $("chart-title").textContent = `Connected clients on ${state.ap.ap_name}`;
      $("chart-back").hidden = false;
      $("chart-reset").textContent = state.data.filter ? "← Back to the filtered total" : "← Back to all access points";
      ccc.lineChart($("chart"), {
        from, to, unit: "clients", label: `Connected clients on ${state.ap.ap_name}`,
        series: [{ name: state.ap.ap_name, points: history.points.map((p) => [new Date(p.ts).getTime(), p.clients]) }],
      });
      return;
    }
    $("chart-back").hidden = true;
    const byWlc = new Map();
    for (const row of state.data.totals) {
      if (!byWlc.has(row.wlc)) byWlc.set(row.wlc, []);
      byWlc.get(row.wlc).push([new Date(row.ts).getTime(), row.clients]);
    }
    let series = [...byWlc.entries()].sort().map(([name, points]) => ({ name, points }));
    let title = state.data.filter ? `Connected clients matching ${state.data.filter}` : "Connected clients";
    if (series.length > 4) {
      // More controllers than the chart can tell apart: show the busiest four.
      series.sort((a, b) => Math.max(...b.points.map((p) => p[1])) - Math.max(...a.points.map((p) => p[1])));
      title += ", four busiest controllers";
    } else if (series.length > 1) {
      title += ", per controller";
    }
    $("chart-title").textContent = title;
    ccc.lineChart($("chart"), {
      from, to, unit: "clients", series, label: title,
      empty: state.data.filter ? "Nothing matches the filter in this period." : undefined,
    });
  }

  function table() {
    const body = ccc.clear($("aps").tBodies[0]);
    let rows = state.data.latest;
    if (state.hideEmpty) rows = rows.filter((r) => r.clients > 0);
    const key = state.sort;
    rows = [...rows].sort((a, b) => {
      const order = key === "clients" ? a.clients - b.clients : String(a[key]).localeCompare(String(b[key]), undefined, { numeric: true });
      return (state.desc ? -order : order) || a.ap_name.localeCompare(b.ap_name, undefined, { numeric: true });
    });
    for (const r of rows.slice(0, 2000)) {
      const selected = state.ap && state.ap.ap_name === r.ap_name && state.ap.wlc === r.wlc;
      body.append(ccc.el("tr", {
        class: "clickable" + (selected ? " selected" : ""), tabindex: "0",
        onclick: () => select(r),
        onkeydown: (e) => { if (e.key === "Enter") select(r); },
      },
        ccc.el("td", {}, r.ap_name),
        ccc.el("td", {}, r.location || "–"),
        ccc.el("td", {}, r.site_tag || "–"),
        ccc.el("td", {}, r.wlc),
        ccc.el("td", { class: "num" }, ccc.int(r.clients))));
    }
    $("aps-empty").hidden = rows.length > 0;
    $("aps-empty").textContent = !state.data.latest_total ? "Nothing counted yet."
      : state.data.latest.length ? "Every access point here is empty." : "No access point matches the filter.";
    const shown = Math.min(rows.length, 2000);
    $("table-note").textContent = state.data.latest_total
      ? `· ${ccc.int(shown)} of ${ccc.int(state.data.latest_total)}` : "";
    $("csv").href = "/export/ap-counts.csv?days=1"
      + (state.data.filter ? "&filter=" + encodeURIComponent(state.data.filter) : "");
  }

  function groups() {
    const body = ccc.clear($("groups").tBodies[0]);
    for (const g of state.data.groups) {
      const active = state.data.filter === g.pattern;
      body.append(ccc.el("tr", {
        class: "clickable" + (active ? " selected" : ""), tabindex: "0",
        onclick: () => apply(g.pattern),
        onkeydown: (e) => { if (e.key === "Enter") apply(g.pattern); },
      },
        ccc.el("td", {}, g.name),
        ccc.el("td", { class: "mono" }, g.pattern),
        ccc.el("td", { class: "num" }, ccc.int(g.aps)),
        ccc.el("td", { class: "num" }, ccc.int(g.clients)),
        ccc.el("td", { class: "num" }, ccc.el("button", {
          class: "link small", type: "button", "aria-label": `Remove group ${g.name}`,
          onclick: (e) => { e.stopPropagation(); remove(g.name); },
        }, "Remove"))));
    }
    $("groups-empty").hidden = state.data.groups.length > 0;
  }

  function streams(data) {
    const body = ccc.clear($("streams").tBodies[0]);
    const use = { clients: "Client count", inventory: "Access point list" };
    for (const s of data.status.streams) {
      const stale = s.last_seen_age > Math.max((s.period || 300) * 2.5, 120);
      body.append(ccc.el("tr", {},
        ccc.el("td", {}, s.wlc),
        ccc.el("td", {}, s.subscription_id),
        ccc.el("td", { class: "mono" }, s.encoding_path),
        ccc.el("td", {}, use[s.kind] || "Raw view only"),
        ccc.el("td", { class: "num" }, s.period ? ccc.duration(s.period) : "–"),
        ccc.el("td", { class: "num" }, ccc.int(s.last_cycle_rows)),
        ccc.el("td", {}, ccc.el("span", { class: "dot " + (stale ? "warn" : "good") }), " ", ccc.age(s.last_seen_age))));
    }
    $("streams-empty").hidden = data.status.streams.length > 0;
    const db = data.database;
    $("db-note").textContent = db.first_cycle
      ? `Stored since ${ccc.dateTime(db.first_cycle)} · ${ccc.int(db.client_cycles)} counts · database ${ccc.bytes(db.database_bytes)}`
      : "";
  }

  function select(row) {
    state.ap = { wlc: row.wlc, ap_name: row.ap_name };
    table();
    chart().catch(console.error);
    $("chart").scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function apply(filter) {
    state.filter = filter;
    state.ap = null;
    $("filter").value = filter;
    load().catch(console.error);
  }

  async function remove(name) {
    if (!confirm(`Remove the group "${name}"? The counts themselves stay.`)) return;
    const result = await ccc.send("DELETE", "/api/groups/" + encodeURIComponent(name));
    state.data.groups = result.groups;
    groups();
  }

  async function load() {
    const request = ++state.request;
    const query = new URLSearchParams({ hours: state.hours });
    if (state.filter.trim()) query.set("filter", state.filter);
    const data = await ccc.get("/api/dashboard?" + query);
    if (request !== state.request) return; // a newer request is on its way
    state.data = data;

    const url = new URL(location.href);
    if (data.filter) url.searchParams.set("filter", data.filter); else url.searchParams.delete("filter");
    history.replaceState(null, "", url);
    $("filter-clear").hidden = !data.filter;
    $("group-open").hidden = !data.filter || !$("group-form").hidden;

    tiles(data);
    table();
    groups();
    streams(data);
    await chart();
  }

  $("range").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-hours]");
    if (!button) return;
    state.hours = Number(button.dataset.hours);
    for (const b of $("range").children) b.classList.toggle("on", b === button);
    load().catch(console.error);
  });

  let typing;
  $("filter").addEventListener("input", (event) => {
    state.filter = event.target.value;
    state.ap = null;
    clearTimeout(typing);
    typing = setTimeout(() => load().catch(console.error), 250);
  });
  $("filter-clear").addEventListener("click", () => apply(""));
  $("hide-empty").addEventListener("change", (event) => { state.hideEmpty = event.target.checked; table(); });

  $("group-open").addEventListener("click", () => {
    $("group-form").hidden = false;
    $("group-open").hidden = true;
    $("group-error").textContent = "";
    $("group-name").focus();
  });
  $("group-cancel").addEventListener("click", () => {
    $("group-form").hidden = true;
    $("group-open").hidden = !state.data.filter;
  });
  $("group-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const result = await ccc.send("POST", "/api/groups", { name: $("group-name").value, filter: state.data.filter });
      state.data.groups = result.groups;
      $("group-form").hidden = true;
      $("group-open").hidden = false;
      $("group-name").value = "";
      groups();
    } catch (err) {
      $("group-error").textContent = err.message;
    }
  });

  $("chart-reset").addEventListener("click", () => { state.ap = null; table(); chart().catch(console.error); });
  for (const th of document.querySelectorAll("#aps th.sortable")) {
    th.addEventListener("click", () => {
      state.desc = state.sort === th.dataset.sort ? !state.desc : th.dataset.sort === "clients";
      state.sort = th.dataset.sort;
      table();
    });
  }
  let resize;
  window.addEventListener("resize", () => { clearTimeout(resize); resize = setTimeout(() => state.data && chart().catch(console.error), 150); });

  load().catch(console.error);
  setInterval(() => { if (!document.hidden) load().catch(console.error); }, 20000);
})();
