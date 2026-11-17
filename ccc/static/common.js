"use strict";

const ccc = {
  async get(url) {
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    if (response.status === 401) {
      location.href = "/login?next=" + encodeURIComponent(location.pathname);
      throw new Error("signed out");
    }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || response.statusText);
    return body;
  },

  async send(method, url, body) {
    const response = await fetch(url, {
      method,
      headers: { Accept: "application/json", "Content-Type": "application/json", "X-Requested-With": "ccc" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || response.statusText);
    return data;
  },

  // el("td", {class: "num"}, "12") — text children are always text, never HTML.
  el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return node;
  },

  clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
    return node;
  },

  int(value) {
    return value === null || value === undefined ? "–" : Number(value).toLocaleString("en-GB");
  },

  bytes(value) {
    if (value === null || value === undefined) return "–";
    const units = ["B", "kB", "MB", "GB", "TB"];
    let n = Number(value), i = 0;
    while (n >= 1000 && i < units.length - 1) { n /= 1000; i += 1; }
    return (i === 0 ? n : n.toFixed(n < 10 ? 1 : 0)) + " " + units[i];
  },

  age(seconds) {
    if (seconds === null || seconds === undefined) return "–";
    const s = Math.max(0, Math.round(seconds));
    if (s < 90) return s + " s ago";
    if (s < 5400) return Math.round(s / 60) + " min ago";
    if (s < 172800) return Math.round(s / 3600) + " h ago";
    return Math.round(s / 86400) + " d ago";
  },

  duration(seconds) {
    if (seconds === null || seconds === undefined) return "–";
    const s = Math.round(seconds);
    if (s < 120) return s + " s";
    if (s % 60 === 0 || s >= 600) return Math.round(s / 60) + " min";
    return (s / 60).toFixed(1) + " min";
  },

  time(iso, withSeconds = true) {
    if (!iso) return "–";
    return new Date(iso).toLocaleTimeString("en-GB", {
      hour: "2-digit", minute: "2-digit", second: withSeconds ? "2-digit" : undefined,
    });
  },

  dateTime(iso) {
    if (!iso) return "–";
    const d = new Date(iso);
    return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" }) + " " + ccc.time(iso);
  },

  async copy(text, button) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (err) {
      // Plain http on a LAN address has no clipboard API: fall back.
      const area = ccc.el("textarea", { class: "mono" });
      area.value = text;
      document.body.append(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    if (button) {
      const before = button.textContent;
      button.textContent = "Copied";
      setTimeout(() => { button.textContent = before; }, 1500);
    }
  },
};
