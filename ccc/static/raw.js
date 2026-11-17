"use strict";

(function () {
  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const state = {
    paused: false,
    selected: null,
    detail: null,
    view: "decoded",
    firstSeq: params.get("first_seq"),
    lastSeq: params.get("last_seq"),
  };

  if (state.firstSeq && state.lastSeq) {
    const pill = $("cycle-filter");
    pill.hidden = false;
    pill.append(`Messages ${state.firstSeq}–${state.lastSeq} (one cycle) `,
      ccc.el("a", { href: "/raw" }, "show all"));
    state.paused = true;
  }

  function options(select, values) {
    const current = select.value;
    const have = new Set([...select.options].map((o) => o.value));
    for (const value of values) {
      if (!have.has(value)) select.append(ccc.el("option", { value }, value));
    }
    select.value = current;
  }

  async function load() {
    const query = new URLSearchParams({ limit: "200" });
    if ($("f-wlc").value) query.set("wlc", $("f-wlc").value);
    if ($("f-path").value) query.set("path", $("f-path").value);
    if (state.firstSeq) query.set("first_seq", state.firstSeq);
    if (state.lastSeq) query.set("last_seq", state.lastSeq);
    const data = await ccc.get("/api/raw?" + query);

    $("capacity").textContent = ccc.int(data.status.raw_capacity);
    options($("f-wlc"), [...new Set(data.status.streams.map((s) => s.wlc))]);
    options($("f-path"), [...new Set(data.status.streams.map((s) => s.encoding_path))]);

    const body = ccc.clear($("messages").tBodies[0]);
    for (const m of data.messages) {
      body.append(ccc.el("tr", {
        class: "clickable" + (m.seq === state.selected ? " selected" : ""), tabindex: "0",
        onclick: () => open(m.seq),
        onkeydown: (e) => { if (e.key === "Enter") open(m.seq); },
      },
        ccc.el("td", { class: "num" }, m.seq),
        ccc.el("td", {}, ccc.time(m.received_at)),
        ccc.el("td", {}, m.node_id || "–"),
        ccc.el("td", {}, m.subscription_id || "–"),
        ccc.el("td", { class: "mono", title: m.encoding_path }, shortPath(m.encoding_path)),
        ccc.el("td", { class: "num" }, ccc.int(m.rows)),
        ccc.el("td", { class: "num" }, ccc.bytes(m.bytes))));
    }
    $("messages-empty").hidden = data.messages.length > 0;
    $("count").textContent = data.messages.length
      ? `· ${data.messages.length} shown, ${ccc.int(data.status.messages)} received since start` : "";

    const errors = data.status.errors;
    $("errors").hidden = errors.length === 0;
    const ebody = ccc.clear($("error-table").tBodies[0]);
    for (const e of errors) {
      ebody.append(ccc.el("tr", {}, ccc.el("td", {}, ccc.time(e.at)), ccc.el("td", {}, e.peer), ccc.el("td", { class: "wrap" }, e.error)));
    }

    const connected = data.status.connections.length;
    $("live-dot").className = "dot " + (state.paused ? "" : connected ? "good" : "warn");
    $("live-text").textContent = state.paused ? "Paused"
      : connected ? `Live · ${connected} connection${connected === 1 ? "" : "s"}` : "Live · no controller connected";

    if (state.selected === null && data.messages.length && !state.paused) open(data.messages[0].seq, true);
  }

  function shortPath(path) {
    const at = (path || "").indexOf(":");
    return at > 0 ? path.slice(at + 1) : path || "–";
  }

  async function open(seq, quiet) {
    state.selected = seq;
    for (const row of $("messages").tBodies[0].rows) {
      row.classList.toggle("selected", Number(row.cells[0].textContent) === seq);
    }
    try {
      state.detail = await ccc.get("/api/raw/" + seq);
    } catch (err) {
      state.detail = null;
      $("detail").textContent = err.message;
      return;
    }
    if (!quiet && !state.paused) togglePause();
    render();
  }

  function render() {
    const d = state.detail;
    if (!d) return;
    $("detail-title").textContent = `Message ${d.seq}`;
    const head = ccc.clear($("detail-head"));
    const add = (k, v) => head.append(ccc.el("dt", {}, k), ccc.el("dd", {}, v));
    add("Received", ccc.dateTime(d.received_at) + " from " + d.peer);
    add("Controller", d.node_id || "–");
    add("Subscription", d.subscription_id || "–");
    add("Path", d.encoding_path || "–");
    add("Collection id", String(d.collection_id));
    add("Rows / size", `${ccc.int(d.rows)} rows, ${ccc.bytes(d.bytes)}`);
    $("detail").textContent = state.view === "decoded"
      ? JSON.stringify(d.decoded, null, 2) : d.protobuf_text;
    $("detail").scrollTop = 0;
  }

  function togglePause() {
    state.paused = !state.paused;
    $("pause").textContent = state.paused ? "Resume" : "Pause";
    if (!state.paused) load().catch(console.error);
    else { $("live-dot").className = "dot"; $("live-text").textContent = "Paused"; }
  }

  $("pause").textContent = state.paused ? "Resume" : "Pause";
  $("pause").addEventListener("click", togglePause);
  $("f-wlc").addEventListener("change", () => load().catch(console.error));
  $("f-path").addEventListener("change", () => load().catch(console.error));
  $("view").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-view]");
    if (!button) return;
    state.view = button.dataset.view;
    for (const b of $("view").children) b.classList.toggle("on", b === button);
    render();
  });
  $("copy").addEventListener("click", (event) => ccc.copy($("detail").textContent, event.currentTarget));

  load().catch(console.error);
  setInterval(() => { if (!state.paused && !document.hidden) load().catch(console.error); }, 3000);
})();
