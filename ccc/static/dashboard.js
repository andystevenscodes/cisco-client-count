"use strict";

(function () {
  const $ = (id) => document.getElementById(id);
  const state = { hours: 24, sort: "clients", desc: true, filter: "", ap: null, data: null };

  function tiles(data) {
    const latest = data.latest;
    const withClients = latest.filter((r) => r.clients > 0).length;
    const clients = latest.reduce((sum, r) => sum + r.clients, 0);
    $("t-clients").textContent = latest.length ? ccc.int(clients) : "–";
    $("t-aps").textContent = latest.length ? ccc.int(latest.length) : "–";
    $("t-aps-note").textContent = latest.length ? `${ccc.int(withClients)} with clients` : " ";

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
    $("chart-title").textContent = series.length > 1 ? "Connected clients per controller" : "Connected clients";
    if (series.length > 4) {
      // More controllers than the chart can tell apart: show the busiest four.
      series.sort((a, b) => Math.max(...b.points.map((p) => p[1])) - Math.max(...a.points.map((p) => p[1])));
      $("chart-title").textContent = "Connected clients, four busiest controllers";
    }
    ccc.lineChart($("chart"), { from, to, unit: "clients", series, label: "Connected clients over time" });
  }

  function table() {
    const body = ccc.clear($("aps").tBodies[0]);
    const needle = state.filter.toLowerCase();
    let rows = state.data.latest;
    if (needle) {
      rows = rows.filter((r) => [r.ap_name, r.location, r.site_tag, r.wlc].some((v) => (v || "").toLowerCase().includes(needle)));
    }
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
    $("aps-empty").textContent = state.data.latest.length ? "No access point matches the filter." : "Nothing counted yet.";
    const shown = Math.min(rows.length, 2000);
    $("table-note").textContent = state.data.latest.length
      ? `· ${ccc.int(shown)} of ${ccc.int(state.data.latest.length)}` : "";
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

  async function load() {
    state.data = await ccc.get(`/api/dashboard?hours=${state.hours}`);
    tiles(state.data);
    table();
    streams(state.data);
    await chart();
  }

  $("range").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-hours]");
    if (!button) return;
    state.hours = Number(button.dataset.hours);
    for (const b of $("range").children) b.classList.toggle("on", b === button);
    load().catch(console.error);
  });
  $("filter").addEventListener("input", (event) => { state.filter = event.target.value; table(); });
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
