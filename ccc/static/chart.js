"use strict";

// One line chart: time on x, a count on y (always from zero), up to four series.
// ccc.lineChart(container, { series: [{ name, points: [[ms, value], ...] }], from, to })
ccc.lineChart = function (container, options) {
  const NS = "http://www.w3.org/2000/svg";
  const svgEl = (tag, attrs) => {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    return node;
  };
  const series = options.series.filter((s) => s.points.length).slice(0, 4);
  ccc.clear(container);
  if (!series.length) {
    container.append(ccc.el("div", { class: "empty" }, options.empty || "No data in this period."));
    return;
  }

  const width = Math.max(container.clientWidth, 320);
  const height = 220;
  const pad = { left: 44, right: 14, top: 10, bottom: 24 };
  const from = options.from, to = options.to;
  const span = Math.max(to - from, 1);

  let max = 0;
  for (const s of series) for (const p of s.points) if (p[1] > max) max = p[1];
  const step = niceStep(Math.max(max, 1) / 4);
  const top = Math.max(step * Math.ceil(max / step), step);
  const x = (t) => pad.left + ((t - from) / span) * (width - pad.left - pad.right);
  const y = (v) => height - pad.bottom - (v / top) * (height - pad.top - pad.bottom);

  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": options.label || "Line chart" });

  for (let v = 0; v <= top + 1e-9; v += step) {
    svg.append(svgEl("line", { class: v === 0 ? "axis-line" : "grid-line", x1: pad.left, x2: width - pad.right, y1: y(v), y2: y(v) }));
    const label = svgEl("text", { x: pad.left - 8, y: y(v) + 4, "text-anchor": "end" });
    label.textContent = compact(v);
    svg.append(label);
  }
  for (const tick of timeTicks(from, to, Math.max(2, Math.floor(width / 130)))) {
    const label = svgEl("text", { x: x(tick.t), y: height - 6, "text-anchor": "middle" });
    label.textContent = tick.label;
    svg.append(label);
  }

  // A gap of several missed samples is a gap in the line, not a straight bridge.
  series.forEach((s, index) => {
    const gaps = [];
    for (let i = 1; i < s.points.length; i += 1) gaps.push(s.points[i][0] - s.points[i - 1][0]);
    gaps.sort((a, b) => a - b);
    const typical = gaps.length ? gaps[Math.floor(gaps.length / 2)] : 0;
    const segments = [[]];
    s.points.forEach((p, i) => {
      if (i && typical && p[0] - s.points[i - 1][0] > typical * 3.5) segments.push([]);
      segments[segments.length - 1].push(p);
    });
    for (const seg of segments) {
      const d = seg.map((p, i) => (i ? "L" : "M") + x(p[0]).toFixed(1) + " " + y(p[1]).toFixed(1)).join(" ");
      if (series.length === 1 && seg.length > 1) {
        const base = y(0).toFixed(1);
        svg.append(svgEl("path", {
          class: `area f${index + 1}`,
          d: `${d} L${x(seg[seg.length - 1][0]).toFixed(1)} ${base} L${x(seg[0][0]).toFixed(1)} ${base} Z`,
        }));
      }
      if (seg.length === 1) {
        svg.append(svgEl("circle", { class: `f${index + 1}`, cx: x(seg[0][0]), cy: y(seg[0][1]), r: 2.5 }));
      } else {
        svg.append(svgEl("path", { class: `line s${index + 1}`, d }));
      }
    }
  });

  const cross = svgEl("line", { class: "cross", y1: pad.top, y2: height - pad.bottom, visibility: "hidden" });
  svg.append(cross);
  const markers = series.map((s, index) => {
    const m = svgEl("circle", { class: `marker f${index + 1}`, r: 5, visibility: "hidden" });
    svg.append(m);
    return m;
  });
  container.append(svg);

  if (series.length > 1) {
    container.append(ccc.el("div", { class: "legend" }, series.map((s, index) =>
      ccc.el("span", {}, ccc.el("span", { class: `key b${index + 1}` }), s.name))));
  }

  const tip = ccc.el("div", { class: "tip", hidden: true });
  container.append(tip);

  function nearest(points, t) {
    let lo = 0, hi = points.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (points[mid][0] < t) lo = mid; else hi = mid;
    }
    return Math.abs(points[lo][0] - t) <= Math.abs(points[hi][0] - t) ? points[lo] : points[hi];
  }

  svg.addEventListener("pointermove", (event) => {
    const box = svg.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * width;
    const t = from + ((px - pad.left) / (width - pad.left - pad.right)) * span;
    const anchor = nearest(series[0].points, t);
    const cx = x(anchor[0]);
    cross.setAttribute("x1", cx);
    cross.setAttribute("x2", cx);
    cross.setAttribute("visibility", "visible");
    ccc.clear(tip).append(ccc.el("div", { class: "t" }, ccc.dateTime(new Date(anchor[0]).toISOString())));
    series.forEach((s, index) => {
      const p = nearest(s.points, anchor[0]);
      markers[index].setAttribute("cx", x(p[0]));
      markers[index].setAttribute("cy", y(p[1]));
      markers[index].setAttribute("visibility", "visible");
      tip.append(ccc.el("div", {},
        series.length > 1 ? ccc.el("span", { class: `key b${index + 1}` }) : null,
        ccc.el("span", { class: "v" }, ccc.int(p[1])),
        " " + (series.length > 1 ? s.name : options.unit || "")));
    });
    tip.hidden = false;
    const scale = box.width / width;
    const left = cx * scale;
    const flip = left > box.width - 180;
    tip.style.left = (flip ? left - tip.offsetWidth - 12 : left + 12) + "px";
    tip.style.top = "8px";
  });
  svg.addEventListener("pointerleave", () => {
    cross.setAttribute("visibility", "hidden");
    markers.forEach((m) => m.setAttribute("visibility", "hidden"));
    tip.hidden = true;
  });

  function niceStep(raw) {
    const power = Math.pow(10, Math.floor(Math.log10(raw)));
    const unit = raw / power;
    const nice = unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 5 ? 5 : 10;
    return Math.max(1, nice * power);
  }

  function compact(v) {
    if (top >= 10000) return (v / 1000).toLocaleString("en-GB") + "K";
    return v.toLocaleString("en-GB");
  }

  function timeTicks(start, end, count) {
    const steps = [5, 10, 15, 30, 60, 120, 180, 360, 720, 1440, 2880, 10080].map((m) => m * 60000);
    const size = steps.find((s) => (end - start) / s <= count) || steps[steps.length - 1];
    const ticks = [];
    // Align on local wall-clock boundaries.
    const offset = new Date(start).getTimezoneOffset() * 60000;
    let t = Math.ceil((start - offset) / size) * size + offset;
    for (; t <= end; t += size) {
      const d = new Date(t);
      const label = size >= 86400000
        ? d.toLocaleDateString("en-GB", { day: "numeric", month: "short" })
        : (end - start > 86400000 * 1.5
          ? d.toLocaleDateString("en-GB", { day: "numeric", month: "short" }) + " " : "")
          + d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
      ticks.push({ t, label });
    }
    return ticks;
  }
};
