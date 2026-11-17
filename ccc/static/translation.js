"use strict";

(function () {
  const $ = (id) => document.getElementById(id);
  const state = { cycles: [], id: null, cycle: null, ap: null, filter: "" };

  function kv(target, pairs) {
    const list = ccc.clear(target);
    for (const [key, value] of pairs) list.append(ccc.el("dt", {}, key), ccc.el("dd", {}, value));
  }

  async function refresh() {
    const [clients, inventory, status] = await Promise.all([
      ccc.get("/api/cycles?kind=clients"), ccc.get("/api/cycles?kind=inventory"), ccc.get("/api/status"),
    ]);
    state.cycles = clients.cycles;
    state.inventory = inventory.cycles;
    state.status = status;
    $("gap").textContent = status.cycle_gap_seconds;

    const select = $("cycle");
    const wanted = $("follow").checked || state.id === null ? (state.cycles[0] || {}).id : state.id;
    // Rebuild the list only when it changed, so an open dropdown stays open.
    const listed = state.cycles.map((c) => c.id).join(",");
    if (listed !== state.listed) {
      state.listed = listed;
      ccc.clear(select);
      for (const c of state.cycles) {
        select.append(ccc.el("option", { value: c.id },
          `${ccc.dateTime(c.ts)} · ${c.wlc} · ${ccc.int(c.counted)} clients`));
      }
    }
    $("none").hidden = state.cycles.length > 0;
    $("steps").hidden = state.cycles.length === 0;
    $("kept").textContent = state.cycles.length
      ? `The newest ${state.cycles.length} update${state.cycles.length === 1 ? " is" : "s are"} kept in memory for this page.` : "";
    if (!state.cycles.length) return;
    const exists = state.cycles.some((c) => c.id === wanted);
    const id = exists ? wanted : state.cycles[0].id;
    select.value = id;
    if (id !== state.id) await show(id);
  }

  async function show(id) {
    state.id = id;
    state.ap = null;
    state.cycle = await ccc.get("/api/cycles/" + id);
    render();
    await rows();
  }

  function render() {
    const c = state.cycle;
    const ids = c.collection_ids;
    kv($("arrived"), [
      ["Controller", `${c.wlc} (from ${c.peer})`],
      ["Subscription", c.subscription_id],
      ["Path", c.encoding_path],
      ["Received", `${ccc.dateTime(c.ts)}, burst lasted ${(c.duration_ms / 1000).toFixed(1)} s`],
      ["Messages", `${ccc.int(c.messages)} (${ccc.bytes(c.bytes)})`],
      ["Rows", ccc.int(c.rows)],
      ["Collection id", ids.distinct > 1 ? `${ids.first} – ${ids.last} (${ids.distinct} distinct)` : String(ids.first)],
    ]);
    $("raw-link").href = `/raw?first_seq=${c.first_seq}&last_seq=${c.last_seq}`;

    $("sample-row").textContent = c.sample_row ? JSON.stringify(c.sample_row, null, 2) : "This update had no rows.";
    const d = c.sample_decision;
    if (d) {
      kv($("sample-fields"), [
        [ccc.el("code", {}, "keys / client-mac"), d.client_mac || "–"],
        [ccc.el("code", {}, "content / ap-name"), d.ap_name || "–"],
        [ccc.el("code", {}, "content / co-state"), d.co_state || "–"],
        [ccc.el("code", {}, "content / wlan-id"), d.wlan_id ?? "–"],
      ]);
      ccc.clear($("sample-verdict")).append(
        ccc.el("span", { class: d.counted ? "yes" : "no" }, d.counted ? "✓ Counted" : "✗ Not counted"),
        d.counted ? ` for ${d.ap_name}: ` : ": ", d.reason + ".");
    } else {
      ccc.clear($("sample-fields"));
      $("sample-verdict").textContent = "–";
    }

    const allowed = state.status.count_states;
    ccc.clear($("rule")).append(
      "A client is counted for the access point in ", ccc.el("code", {}, "ap-name"),
      " when its ", ccc.el("code", {}, "co-state"), " is ",
      ...allowed.flatMap((s, i) => [i ? " or " : "", ccc.el("code", {}, s)]),
      ". A MAC address that appears more than once in the same update is counted once.");

    const body = ccc.clear($("states").tBodies[0]);
    for (const s of c.states) {
      body.append(ccc.el("tr", {},
        ccc.el("td", { class: "mono" }, s.co_state),
        ccc.el("td", { class: "num" }, ccc.int(s.clients)),
        ccc.el("td", { class: "num" }, ccc.int(s.counted))));
    }
    kv($("sum"), [
      ["Rows received", ccc.int(c.rows)],
      ["Repeated MAC addresses", "− " + ccc.int(c.duplicates)],
      ["Clients", "= " + ccc.int(c.clients)],
      ["Not counted", "− " + ccc.int(c.not_counted)],
      ["Connected clients", "= " + ccc.int(c.counted)],
      ["On access points", ccc.int(c.aps_with_clients)],
    ]);

    counts();

    const stored = ccc.clear($("stored"));
    if (c.stored) {
      const n = Object.keys(c.counts).length;
      stored.append(ccc.el("span", { class: "yes" }, "✓ Stored. "),
        `${ccc.int(n)} rows written to `, ccc.el("code", {}, "ap_client_count"),
        ` with timestamp ${c.ts} (${ccc.int(c.aps_with_clients)} access points with clients`,
        c.zero_filled.length ? `, ${ccc.int(c.zero_filled.length)} with 0).` : ").");
    } else {
      stored.append(ccc.el("span", { class: "error" }, "Not stored: " + (c.store_error || "no database")));
    }
    const inv = state.inventory.find((i) => i.wlc === c.wlc);
    $("inventory").textContent = inv
      ? `Access point list of ${c.wlc}: ${ccc.int(inv.rows)} access points, received ${ccc.dateTime(inv.ts)} (subscription ${inv.subscription_id}). An access point on the list without clients is stored as 0.`
      : `No access point list received from ${c.wlc} since this service started. Without it, an access point is stored as 0 only when it had clients in the last days.`;
  }

  function counts() {
    const c = state.cycle;
    const zero = new Set(c.zero_filled);
    const needle = state.filter.toLowerCase();
    const entries = Object.entries(c.counts)
      .filter(([ap]) => !needle || ap.toLowerCase().includes(needle))
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], undefined, { numeric: true }));
    const body = ccc.clear($("counts").tBodies[0]);
    for (const [ap, n] of entries.slice(0, 2000)) {
      body.append(ccc.el("tr", {
        class: "clickable" + (state.ap === ap ? " selected" : ""), tabindex: "0",
        onclick: () => pick(ap), onkeydown: (e) => { if (e.key === "Enter") pick(ap); },
      },
        ccc.el("td", {}, ap),
        ccc.el("td", { class: "num" }, ccc.int(n)),
        ccc.el("td", { class: "muted" }, zero.has(ap)
          ? (c.zero_fill_source === "inventory" ? "0, on the access point list" : "0, had clients recently")
          : `${ccc.int(n)} counted row${n === 1 ? "" : "s"}`)));
    }
    $("ap-note").textContent = `· ${ccc.int(Object.keys(c.counts).length)} access points, ${ccc.int(c.counted)} clients`;
  }

  async function pick(ap) {
    state.ap = ap;
    counts();
    await rows();
  }

  async function rows() {
    const query = state.ap === null ? "counted=0" : "ap=" + encodeURIComponent(state.ap);
    const data = await ccc.get(`/api/cycles/${state.id}/rows?${query}&limit=500`);
    $("rows-title").textContent = state.ap === null ? "Rows that were not counted" : `Rows naming ${state.ap}`;
    $("rows-reset").hidden = state.ap === null;
    const body = ccc.clear($("rows").tBodies[0]);
    for (const r of data.rows) {
      body.append(ccc.el("tr", {},
        ccc.el("td", { class: "mono" }, r.client_mac || "–"),
        ccc.el("td", {}, r.ap_name || "–"),
        ccc.el("td", { class: "mono" }, r.co_state || "–"),
        ccc.el("td", { class: "num" }, r.wlan_id ?? "–"),
        ccc.el("td", { class: "num" }, r.slot ?? "–"),
        ccc.el("td", { class: r.counted ? "yes" : "no", title: r.reason }, r.counted ? "✓ yes" : "✗ no")));
    }
    $("rows-empty").hidden = data.rows.length > 0;
    $("rows-empty").textContent = state.ap === null ? "Every row in this update was counted." : "No rows name this access point.";
    const counted = data.rows.filter((r) => r.counted).length;
    $("rows-note").textContent = data.total > data.rows.length
      ? `Showing ${data.rows.length} of ${ccc.int(data.total)} rows.`
      : state.ap !== null && data.rows.length ? `${counted} of ${data.rows.length} rows counted: that is the number for ${state.ap}.` : "";
  }

  $("cycle").addEventListener("change", (event) => {
    $("follow").checked = false;
    show(Number(event.target.value)).catch(console.error);
  });
  $("follow").addEventListener("change", () => refresh().catch(console.error));
  $("ap-filter").addEventListener("input", (event) => { state.filter = event.target.value; counts(); });
  $("rows-reset").addEventListener("click", () => { state.ap = null; counts(); rows().catch(console.error); });

  refresh().catch(console.error);
  setInterval(() => { if (!document.hidden) refresh().catch(console.error); }, 5000);
})();
