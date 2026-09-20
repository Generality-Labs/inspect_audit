// gl-components — the figure library for generality.org posts.
//
// Conventions
//   Every component has the signature  component(deps, data, spec) → HTMLElement
//     deps  — libraries supplied by the OJS runtime ({ Plot }), or null for
//             components that build raw DOM/SVG. The module itself imports nothing.
//     data  — a plain data contract, usually straight from a FileAttachment.
//     spec  — options. Common keys shared by all components:
//               caption      figcaption text under the figure
//               provenance   small-print line under the caption
//               width        pixel width (defaults fit the 800px prose column)
//             Plot-based components also share:
//               colorDomain / colorRange   explicit color scale (see model-colors.js)
//   House identity (palette, type, tooltip and axis-label behaviour) lives in
//   the helpers below so each component states only what is unique to it.

export const palette = {
  ink: "#1a1919", muted: "#5b5858", soft: "#837878", hairline: "#e6e6e6",
  bg: "#fafafa", paper: "#f2f2f2",
  mint: "#17cfb9", mintSoft: "#ecf8f5", mintEdge: "#c5e8e1", mintDeep: "#24857a",
  series: ["#24857a", "#d97757", "#7c4dbe", "#3a5fa8", "#eda100", "#d1495b"],
  good: "#1baf7a", warn: "#eda100", warnInk: "#b07d00",
  bad: "#d1495b", badSoft: "#faeceb",
  neutral: "#7a93ad", neutralInk: "#5a748c", neutralSoft: "#edf1f5",
  cleanFill: "#9fc3b4",
};

const FONT = `"Public Sans","Inter",-apple-system,system-ui,sans-serif`;
const SVG_NS = "http://www.w3.org/2000/svg";

/* ---------------------------------------------------------------- helpers */

const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;");

/** Create an HTML element with inline css (and optional innerHTML). */
function el(tag, css, html) {
  const node = document.createElement(tag);
  if (css) node.style.cssText = css;
  if (html != null) node.innerHTML = html;
  return node;
}

/** Create an SVG element and set attributes. */
function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

/** Inject a stylesheet into the document once per id (components may render
    several times per page; their CSS should not). */
function injectOnce(id, cssText) {
  if (document.getElementById(id)) return;
  const style = document.createElement("style");
  style.id = id;
  style.textContent = cssText;
  document.head.appendChild(style);
}

/** Wrap a rendered node in a <figure> with the shared caption/provenance style. */
function frame(node, { caption, provenance } = {}) {
  const fig = el("figure", `margin:1.5rem 0;font-family:${FONT};`);
  fig.appendChild(node);
  if (caption) {
    const c = document.createElement("figcaption");
    c.style.cssText = `font-size:.82rem;color:${palette.muted};margin-top:.5rem;line-height:1.45;`;
    // captions are plain text, or a DOM node (e.g. an OJS html`` fragment
    // carrying links) — never raw HTML strings.
    if (caption instanceof Node) c.appendChild(caption);
    else c.textContent = caption;
    fig.appendChild(c);
  }
  if (provenance) {
    const p = el("div", `font-size:.7rem;color:${palette.soft};margin-top:.35rem;font-variant-numeric:tabular-nums;`);
    p.textContent = provenance;
    fig.appendChild(p);
  }
  return fig;
}

/** The house Plot style block. */
const plotStyle = (fontSize = "13px") =>
  ({ background: "transparent", fontFamily: FONT, fontSize });

/** A centred x-axis config with enough offset to clear two-line tick labels. */
const centredAxis = (label, labelOffset, extra = {}) =>
  ({ label, labelAnchor: "center", labelOffset, ...extra });

/** An HTML x-axis label rendered below the plot (used where Plot's own label
    would collide with tick labels). */
const labelBelow = (text, css) => el("div",
  `text-align:center;font-family:${FONT};margin-top:.2rem;${css}`, esc(text));

/** Colour-swatch legend row rendered below a plot (house style: the model
    family legend sits under the graph, never above it). */
function legendRow(domain, range) {
  return el("div",
    `display:flex;flex-wrap:wrap;justify-content:center;gap:.35rem 1.1rem;` +
    `font-family:${FONT};font-size:12px;color:${palette.muted};margin-top:.4rem;`,
    domain.map((name, i) => `<span style="display:inline-flex;align-items:center;gap:.35rem">` +
      `<span style="width:10px;height:10px;border-radius:3px;background:${range[i % range.length]};display:inline-block"></span>${esc(name)}</span>`).join(""));
}

/* -------------------------------------------------------------- glStatBar */
// Benchmark fact strip for the top of an audit post: the metrics we track
// across every benchmark (size, frontier score, validated ceiling, ...) in a
// hairline-ruled row. Reusable: pass any label/value/detail items.
//   items: [{ label, value, detail? }]
export function glStatBar(_, items, spec = {}) {
  const { caption, provenance } = spec;
  injectOnce("gl-statbar-css", `
    .gl-statbar { display:flex; flex-wrap:wrap;
      border-bottom:2px solid ${palette.hairline};
      font-family:${FONT}; }
    /* sit tight under the post title: lift the whole quarto cell, not the
       bar itself (the OJS output container clips negative child margins) */
    .cell:has(.gl-statbar) { margin-top:-2.5rem; }
    .gl-statbar .gl-stat { flex:0 1 auto; min-width:90px; padding:.1rem 1.8rem .45rem 0; }
    .gl-statbar .gl-stat + .gl-stat { border-left:1px solid ${palette.hairline}; padding-left:1.2rem; }
    .gl-statbar .label { font-family:"Geist Mono",monospace; font-size:9px;
      letter-spacing:.08em; text-transform:uppercase; color:${palette.soft}; }
    .gl-statbar .value { font-size:.95rem; font-weight:600; letter-spacing:-0.01em;
      color:${palette.ink}; font-variant-numeric:tabular-nums; line-height:1.3;
      margin-top:.05rem; }
    .gl-statbar .detail { font-size:10.5px; color:${palette.muted}; margin-top:.05rem;
      line-height:1.3; }
  `);
  const bar = el("div", null);
  bar.className = "gl-statbar";
  for (const it of items) {
    bar.appendChild(el("div", null, `
      <div class="label">${esc(it.label)}</div>
      <div class="value">${esc(it.value)}</div>
      ${it.detail ? `<div class="detail">${esc(it.detail)}</div>` : ""}
    `)).className = "gl-stat";
  }
  return frame(bar, { caption, provenance });
}

/* ------------------------------------------------------------- glScatter */
// Model scatter with optional CI whiskers and living-figure vintage marks.
//   rows: {x, y, label, group, lo?, hi?}
//   spec: x/y/label/group key names, xLabel/yLabel, xDomain/yDomain,
//         lo/hi (CI keys), xFmt/yFmt (tooltip formats),
//         vintage + dateKey — rows dated after `vintage` render as hollow mint,
//         frontier — dotted step line through the running maximum, held flat
//         out to the latest x to make stagnation legible.
export function glScatter({ Plot }, rows, spec = {}) {
  const {
    x = "x", y = "y", label = "label", group = "group",
    xLabel = "", yLabel = "", caption, provenance,
    width = 700, height = 460, xFmt = (v) => v, yFmt = (v) => v,
    vintage, dateKey = "date", lo, hi,
  } = spec;
  const cut = vintage ? new Date(vintage) : null;
  const isNew = (d) => cut && new Date(d[dateKey]) > cut;
  const before = cut ? rows.filter((d) => !isNew(d)) : rows;
  const after = cut ? rows.filter(isNew) : [];

  const marks = [];
  const onFrontier = new Set();
  if (spec.frontier) {
    const sorted = [...rows].sort((a, b) => +a[x] - +b[x]);
    const steps = [];
    let best = -Infinity;
    for (const d of sorted) {
      if (d[y] > best) { best = d[y]; onFrontier.add(d); steps.push({ _fx: d[x], _fy: best }); }
    }
    steps.push({ _fx: sorted[sorted.length - 1][x], _fy: best }); // hold flat to latest release
    marks.push(Plot.line(steps, { x: "_fx", y: "_fy", curve: "step-after",
      stroke: palette.soft, strokeWidth: 1.3, strokeDasharray: "2,4" }));
  }
  // with a frontier drawn, the field fades slightly so the frontier carries the story
  const dotAlpha = (d) => (!spec.frontier || onFrontier.has(d) ? 0.9 : 0.45);
  const ciAlpha = (d) => (!spec.frontier || onFrontier.has(d) ? 0.5 : 0.28);
  if (lo && hi && rows.some((d) => d[lo] != null)) {
    marks.push(Plot.ruleX(rows, { x, y1: lo, y2: hi, stroke: group, strokeWidth: 1.4, strokeOpacity: ciAlpha }));
    // CI end caps: short horizontal segments at lo and hi.
    const xs = rows.map((d) => +d[x]);
    const capW = (Math.max(...xs) - Math.min(...xs)) * 0.006 || 1;
    const caps = rows.flatMap((d) => [
      { ...d, _cy: d[lo], _x1: +d[x] - capW, _x2: +d[x] + capW, _a: ciAlpha(d) },
      { ...d, _cy: d[hi], _x1: +d[x] - capW, _x2: +d[x] + capW, _a: ciAlpha(d) },
    ]).filter((d) => d._cy != null);
    marks.push(Plot.ruleY(caps, { y: "_cy", x1: (d) => new Date(d._x1), x2: (d) => new Date(d._x2),
      stroke: group, strokeWidth: 1.4, strokeOpacity: (d) => d._a }));
  }
  marks.push(Plot.dot(before, { x, y, fill: group, r: 5.5, fillOpacity: dotAlpha }));
  if (after.length)
    marks.push(
      Plot.dot(after, { x, y, stroke: palette.mint, strokeWidth: 2.5, r: 6.5, fill: palette.bg }),
      Plot.dot(after, { x, y, fill: palette.mint, r: 3 }),
    );
  marks.push(Plot.tip(rows, Plot.pointer({ x, y, maxRadius: 24, title: (d) =>
    `${d[label]}${isNew(d) ? "  (added since publication)" : ""}\n${xFmt(d[x])} · ${yFmt(d[y])}` })));

  const node = Plot.plot({
    width, height, marginBottom: 58,
    style: plotStyle(),
    x: centredAxis(xLabel || null, 52, { grid: false, domain: spec.xDomain }),
    y: { label: yLabel || null, grid: true, domain: spec.yDomain },
    color: { legend: !spec.legendBelow, domain: spec.colorDomain, range: spec.colorRange ?? palette.series },
    marks,
  });
  let out = node;
  if (spec.legendBelow && spec.colorDomain) {
    out = el("div");
    out.appendChild(node);
    out.appendChild(legendRow(spec.colorDomain, spec.colorRange ?? palette.series));
  }
  const prov = cut
    ? `${provenance || ""}  ·  ● as published ${vintage} · ◉ added since (live-refreshing)`
    : provenance;
  return frame(out, { caption, provenance: prov });
}

/* ----------------------------------------------------------- glHistogram */
// Overlapping (non-stacked) binned histograms with dashed mean lines.
//   rows: {value, series}; spec.thresholds may be a count or bin-edge array.
export function glHistogram({ Plot }, rows, spec = {}) {
  const {
    value = "value", series = "series",
    xLabel = "", caption, provenance,
    width = 688, height = 470, thresholds = 18,
  } = spec;
  const byValues = {};
  for (const r of rows) (byValues[r[series]] = byValues[r[series]] || []).push(r[value]);
  const meanRows = Object.entries(byValues).map(([k, vs]) =>
    ({ [series]: k, m: vs.reduce((a, b) => a + b, 0) / vs.length }));

  const node = Plot.plot({
    width, height,
    style: plotStyle(),
    x: { label: null, grid: false },
    y: { label: null, grid: true, insetTop: 16 },
    color: { legend: true, domain: spec.colorDomain, range: spec.colorRange ?? [palette.series[0], palette.series[1]] },
    marks: [
      // y2 (not y) so the two distributions overlay instead of stacking.
      Plot.rectY(rows, Plot.binX({ y2: "count" }, { x: value, fill: series, thresholds, fillOpacity: 0.5, inset: 0.5 })),
      Plot.ruleX(meanRows, { x: "m", stroke: series, strokeWidth: 2.6, strokeDasharray: "5,3" }),
    ],
  });
  const wrap = el("div");
  wrap.appendChild(node);
  wrap.appendChild(labelBelow(xLabel, `font-size:.95rem;color:${palette.muted};`));
  return frame(wrap, { caption, provenance });
}

/* ---------------------------------------------------------------- glBars */
// Horizontal bars, one per label, coloured by group.
//   rows: {label, value, group}
//   spec.sort (default true) sorts descending; spec.tipName renames the
//   tooltip's value line.
export function glBars({ Plot }, rows, spec = {}) {
  const {
    value = "value", label = "label", group = "group",
    xLabel = "", caption, provenance, width = 688,
    valueFmt = (v) => v, sort = true,
    // Margins are overridable so a chart with short row labels does not keep
    // 210px of empty space on its left.
    marginLeft = 210, marginTop = 20, marginBottom = 46, className,
  } = spec;
  const data = sort ? rows.slice().sort((a, b) => b[value] - a[value]) : rows;
  const height = spec.height ?? Math.max(160, data.length * 16 + 80);

  const node = Plot.plot({
    width, height, marginLeft, marginTop, marginBottom,
    style: plotStyle("12px"),
    x: centredAxis(xLabel, 38, { grid: true, tickFormat: spec.xTickFormat }),
    y: { label: null, domain: data.map((d) => d[label]) },
    color: { legend: false, domain: spec.colorDomain, range: spec.colorRange ?? palette.series },
    marks: [
      Plot.barX(data, { y: label, x: value, fill: group, rx: 2, insetTop: 1.5, insetBottom: 1.5 }),
      // pointerY: hovering anywhere along a bar's row triggers that bar's tip.
      Plot.tip(data, Plot.pointerY({ y: label, x: value, title: (d) =>
        spec.tipTitle ? spec.tipTitle(d)
          : `${d[label]}\n${spec.tipName || xLabel || value}: ${valueFmt(d[value])}` })),
    ],
  });
  const wrap = el("div");
  wrap.appendChild(node);
  // legend: false suppresses the swatch row (e.g. when bars are coloured
  // per-label and the y axis already names them)
  if (spec.colorDomain && spec.legend !== false)
    wrap.appendChild(legendRow(spec.colorDomain, spec.colorRange ?? palette.series));
  const fig = frame(wrap, { caption, provenance });
  if (className) fig.classList.add(className);
  if (spec.margin) fig.style.margin = spec.margin;
  // tight: pull the figure up against a companion figure directly above it
  if (spec.tight) fig.style.marginTop = "-1.4rem";
  return fig;
}

/* ------------------------------------------------------------ glSolveDist */
// Solve-count distribution: how many questions were solved by exactly k of
// the leaderboard models. Hover a column for counts.
//   rows: questions.json ({item, question, gold, n_solved, n_models})
export function glSolveDist({ Plot }, rows, spec = {}) {
  const { caption, provenance, width = 688, height = 340 } = spec;
  const nModels = rows[0]?.n_models ?? 49;
  const counts = new Map();
  for (const r of rows) counts.set(r.n_solved, (counts.get(r.n_solved) || 0) + 1);
  const bins = Array.from({ length: nModels + 1 }, (_, k) => ({ k, n: counts.get(k) || 0 }));

  const node = Plot.plot({
    width, height, marginBottom: 48,
    style: plotStyle(),
    x: centredAxis(`models solving the question (of ${nModels})`, 40),
    y: { label: "questions", grid: true, insetTop: 14 },
    marks: [
      Plot.rectY(bins, { x1: (d) => d.k - 0.45, x2: (d) => d.k + 0.45, y: "n",
        fill: (d) => (d.k === 0 ? palette.bad : palette.mintDeep), fillOpacity: 0.85, rx: 1.5 }),
      Plot.tip(bins, Plot.pointerX({ x: "k", y: "n", title: (d) =>
        `${d.n} questions solved by exactly ${d.k} model${d.k === 1 ? "" : "s"}` })),
    ],
  });
  return frame(node, { caption, provenance });
}

/* --------------------------------------------------------- glForcingPairs */
// Two stacked panels of vertical natural/forced bar pairs per model:
// accuracy (with 95% CIs) on top, abstention below. Bars are tinted by
// provider colour (rows carry `hex`): pale = natural, solid = forced.
// Sorted by the forced-minus-natural accuracy difference. Hovering a model's
// column shows both arms at once.
//   rows: {label, hex, nat_score, for_score, nat_se, for_se, nat_abst, for_abst}
export function glForcingPairs({ Plot }, rows, spec = {}) {
  const { caption, provenance, width = 688 } = spec;
  const order = rows.slice()
    .sort((a, b) => (b.for_score - b.nat_score) - (a.for_score - a.nat_score))
    .map((d) => d.label);

  const panel = (aKey, bKey, seA, seB, yLabel, withCI) => {
    const flat = rows.flatMap((d) => [
      { label: d.label, arm: "natural", v: d[aKey], se: seA ? d[seA] : null, hex: d.hex },
      { label: d.label, arm: "forced", v: d[bKey], se: seB ? d[seB] : null, hex: d.hex },
    ]);
    const tips = rows.map((d) => ({ label: d.label, a: d[aKey], b: d[bKey],
      top: Math.max(d[aKey], d[bKey]) }));
    return Plot.plot({
      width, height: 330, marginBottom: 64, marginLeft: 56,
      style: plotStyle("12.5px"),
      fx: { label: null, domain: order, tickRotate: -28, padding: 0.25, paddingOuter: 0.5, align: 0.6 },
      x: { axis: null, domain: ["natural", "forced"], padding: 0.12 },
      y: { label: yLabel, grid: true, insetTop: 14 },
      marks: [
        Plot.barY(flat, { fx: "label", x: "arm", y: "v", rx: 2,
          fill: (d) => d.hex, fillOpacity: (d) => (d.arm === "natural" ? 0.32 : 0.92) }),
        ...(withCI ? [
          Plot.ruleX(flat.filter((d) => d.se), { fx: "label", x: "arm",
            y1: (d) => d.v - 1.96 * d.se, y2: (d) => d.v + 1.96 * d.se,
            stroke: palette.ink, strokeWidth: 1.3 }),
        ] : []),
        // one tip per model, anchored between the two bars (dx = half a band)
        Plot.tip(tips, Plot.pointerX({ fx: "label", x: () => "natural", y: "top", dx: 15, title: (d) =>
          `${d.label}\nnatural: ${(100 * d.a).toFixed(1)}%\nforced: ${(100 * d.b).toFixed(1)}%` })),
      ],
    });
  };

  const wrap = el("div");
  wrap.appendChild(el("div", `font-size:.8rem;color:${palette.muted};margin-bottom:.25rem;font-family:${FONT};`,
    "pale = natural &nbsp;·&nbsp; solid = forced answer"));
  wrap.appendChild(panel("nat_score", "for_score", "nat_se", "for_se", "headline accuracy", true));
  wrap.appendChild(el("div", "height:14px;"));
  wrap.appendChild(panel("nat_abst", "for_abst", null, null, "abstention rate", false));
  return frame(wrap, { caption, provenance });
}

/* ------------------------------------------------------- glTripletScatter */
// Several metrics per model on a date axis. Colour = group (provider),
// symbol = metric (first metric is drawn filled), a faint vertical rule ties
// one model's readings together. Dates get a stable per-model jitter so
// same-day releases don't overplot.
//   rows: {x: Date, label, group, <one column per metric>}
export function glTripletScatter({ Plot }, rows, spec = {}) {
  const {
    x = "x", label = "label", group = "group", metrics = [],
    xLabel = "", caption, provenance,
    width = 688, height = 480, valueFmt = (v) => v.toFixed(3),
  } = spec;

  const JITTER_DAYS = 1.3, DAY_MS = 86400000;
  const jmap = new Map(rows.map((r) => {
    const h = [...String(r[label])].reduce((a, c) => a + c.charCodeAt(0), 0);
    return [r[label], new Date(+r[x] + ((h % 13) - 6) * JITTER_DAYS * DAY_MS)];
  }));
  const jx = (d) => jmap.get(d[label]);

  const flat = [];
  for (const r of rows) for (const m of metrics)
    if (r[m] != null) flat.push({ ...r, metric: m, value: r[m] });
  const heads = flat.filter((d) => d.metric === metrics[0]);
  const others = flat.filter((d) => d.metric !== metrics[0]);
  const extent = (d, fn) => fn(...metrics.map((m) => d[m]).filter((v) => v != null));

  const node = Plot.plot({
    width, height, marginBottom: 46,
    style: plotStyle(),
    x: { label: null, grid: false, domain: spec.xDomain },
    y: { label: null, grid: true, domain: spec.yDomain },
    color: { legend: false, domain: spec.colorDomain, range: spec.colorRange ?? palette.series },
    symbol: { legend: false, domain: metrics, range: ["circle", "diamond2", "times"] },
    marks: [
      Plot.ruleX(rows, { x: jx, y1: (d) => extent(d, Math.min), y2: (d) => extent(d, Math.max),
        stroke: group, strokeWidth: 1.1, strokeOpacity: 0.35 }),
      Plot.dot(heads, { x: jx, y: "value", fill: group, symbol: "circle", r: 4.5 }),
      Plot.dot(others, { x: jx, y: "value", stroke: group, symbol: "metric", r: 4.5, strokeWidth: 1.8 }),
      Plot.tip(flat, Plot.pointer({ x: jx, y: "value", title: (d) =>
        `${d[label]}\n${d.metric}: ${valueFmt(d.value)}` })),
    ],
  });
  // metric key drawn inside the plot area (colour carries provider, shape carries metric)
  const GLYPH = ["●", "◇", "✕"];
  const key = el("div",
    `position:absolute;top:10px;left:52px;font-family:${FONT};font-size:12px;` +
    `color:${palette.muted};display:flex;flex-direction:column;gap:.15rem;pointer-events:none;`,
    metrics.map((m, i) => `<span><span style="color:${palette.ink};display:inline-block;width:1.1em">${GLYPH[i] ?? "•"}</span>${esc(m)}</span>`).join(""));
  const plotBox = el("div", "position:relative;");
  plotBox.appendChild(node);
  plotBox.appendChild(key);

  const wrap = el("div");
  wrap.appendChild(plotBox);
  wrap.appendChild(labelBelow(xLabel, `font-size:.92rem;font-weight:600;color:${palette.ink};margin-top:.15rem;`));
  if (spec.colorDomain) wrap.appendChild(legendRow(spec.colorDomain, spec.colorRange ?? palette.series));
  return frame(wrap, { caption, provenance });
}

/* --------------------------------------------------------------- glRadar */
// Overlaid radar polygons, one per model, axes = knowledge areas.
//   data: {axes: [...], rows: [{model, axis, acc, n}]}
//   spec: colors {model: hex}, rMin/rMax (radial domain), legend: false to
//   suppress the built-in legend (e.g. when two radars share one).
export function glRadar(_, data, spec = {}) {
  const { caption, provenance, width = 560, rMin = 0.4, rMax = 0.85, colors = {} } = spec;
  const RING_VALUES = [0.5, 0.6, 0.7, 0.8];
  const LABEL_OVERHANG = 0.11;             // axis labels sit just past rMax
  const R_MARGIN = 58;                     // svg edge to polygon edge

  const axes = data.axes;
  const models = [...new Set(data.rows.map((r) => r.model))];
  const size = width, cx = size / 2, cy = size / 2 + 6, R = size / 2 - R_MARGIN;
  const ang = (i) => (Math.PI * 2 * i) / axes.length - Math.PI / 2;
  const rr = (v) => R * Math.max(0, Math.min(1, (v - rMin) / (rMax - rMin)));
  const pt = (i, v) => [cx + rr(v) * Math.cos(ang(i)), cy + rr(v) * Math.sin(ang(i))];

  const svg = svgEl("svg", { viewBox: `0 0 ${size} ${size + 10}` });
  svg.style.cssText = `width:100%;max-width:${size}px;height:auto;font-family:${FONT};display:block;margin:0 auto;`;

  for (const g of RING_VALUES) {
    svg.appendChild(svgEl("polygon", {
      points: axes.map((_, i) => pt(i, g).join(",")).join(" "),
      fill: "none", stroke: palette.hairline }));
    const [x, y] = pt(0, g);
    const t = svgEl("text", { x: x + 4, y: y + 3, style: `font-size:9.5px;fill:${palette.soft};` });
    t.textContent = g.toFixed(1);
    svg.appendChild(t);
  }
  axes.forEach((a, i) => {
    const [x1, y1] = pt(i, rMin), [x2, y2] = pt(i, rMax);
    svg.appendChild(svgEl("line", { x1, y1, x2, y2, stroke: palette.hairline }));
    const [lx, ly] = pt(i, rMax + (rMax - rMin) * LABEL_OVERHANG);
    const anchor = Math.abs(Math.cos(ang(i))) < 0.35 ? "middle" : (Math.cos(ang(i)) > 0 ? "start" : "end");
    const t = svgEl("text", { x: lx, y: ly + 4, "text-anchor": anchor,
      style: `font-size:11px;fill:${palette.muted};` });
    t.textContent = a;
    svg.appendChild(t);
  });

  const byModel = {};
  for (const r of data.rows) (byModel[r.model] = byModel[r.model] || {})[r.axis] = r;
  models.forEach((mName, mi) => {
    const col = colors[mName] || palette.series[mi];
    const pts = axes.map((a, i) => pt(i, byModel[mName][a]?.acc ?? rMin));
    svg.appendChild(svgEl("polygon", {
      points: pts.map((p) => p.join(",")).join(" "),
      fill: col, "fill-opacity": "0.13",
      stroke: col, "stroke-width": "2.2", "stroke-linejoin": "round" }));
    pts.forEach(([x, y], i) => {
      const c = svgEl("circle", { cx: x, cy: y, r: 3.6, fill: col });
      const r = byModel[mName][axes[i]];
      const title = svgEl("title");
      title.textContent = `${mName} · ${axes[i]}: ${(100 * r.acc).toFixed(1)}% (n=${r.n})`;
      c.appendChild(title);
      svg.appendChild(c);
    });
  });

  const wrap = el("div");
  wrap.appendChild(svg);
  if (spec.legend !== false) {
    wrap.appendChild(el("div",
      `display:flex;gap:1.2rem;justify-content:center;font-size:.85rem;color:${palette.muted};font-family:${FONT};margin-top:.2rem;`,
      models.map((mName, mi) =>
        `<span><span style="color:${colors[mName] || palette.series[mi]}">●</span> ${mName}</span>`).join("")));
  }
  return frame(wrap, { caption, provenance });
}

/* ------------------------------------------------------------- glInfSteps */
// AISI-style per-attempt token-budget CDF: x = a task's own token cost
// (log), y = cumulative success rate, step curves, right-rail labels with
// final scores instead of a legend, optional horizontal bands.
//   data: {nTasks, series: [{label, color, official: [{x,y}], corrected: [...],
//          officialAcc, correctedAcc}], meta}
//   spec: track "official"|"corrected"|...; title; bands [{from, to, label?,
//         color?, hatch?, angle?}] (hatch: diagonal lines in `color`; else
//         AISI solid grey); xDomain; railWidth; models (array of series keys
//         to include); compare {track, dash?, suffix?} to overlay a second
//         track for the same models (dotted by default).
export function glInfSteps({ Plot }, data, spec = {}) {
  const { caption, width = 720, height = 440, track = "official",
    bands = [], title, railWidth = 218, tips = true } = spec;
  const ser = spec.models
    ? data.series.filter((s) => spec.models.includes(s.key))
    : data.series;
  // spec.dash dashes the primary track (e.g. corrected curves dashed while
  // the compare track stays solid via compare.dash: "")
  const trackDefs = [{ track, dash: spec.dash ?? null, suffix: spec.suffix ?? "" }];
  if (spec.compare)
    trackDefs.push({ track: spec.compare.track,
      dash: spec.compare.dash ?? "2,3.5",
      suffix: spec.compare.suffix ?? ` (${spec.compare.track})` });
  // per-track colours: spec.colors overrides the primary track's colour per
  // series key (e.g. corrected curves in their own shades); the compare
  // track keeps the series' canonical colour unless spec.compare.colors says
  // otherwise — so the dotted defaults match the other figures.
  const trackColor = (s, ti) =>
    (ti === 0 ? spec.colors?.[s.key] : spec.compare?.colors?.[s.key]) ?? s.color;
  const flat = [];
  for (let ti = 0; ti < trackDefs.length; ti++)
    for (const s of ser)
      for (const p of s[trackDefs[ti].track])
        flat.push({ x: p.x, y: p.y, model: s.label, ti, harness: !!s.harness,
          series: `${s.label}${trackDefs[ti].suffix}` });
  const fmtTok = (v) => v >= 1e6 ? `${(v / 1e6).toFixed(v < 3e6 ? 1 : 0)}M`
    : `${Math.round(v / 1e3)}K`;
  // minor log ticks keep their marks; only the decades get labels
  const [xLo, xHi] = spec.xDomain ?? [8e3, 7e6];
  const xTicks = [];
  for (let e = 3; e <= 7; e++)
    for (let m = 1; m <= 9; m++) {
      const v = m * 10 ** e;
      if (v >= xLo && v <= xHi) xTicks.push(v);
    }
  // one line mark per (track, harness-style) combination; each mark's paths
  // follow its series order, recorded for the hover mapping
  const lineSpecs = [];
  for (let ti = 0; ti < trackDefs.length; ti++)
    for (const h of [false, true]) {
      const group = ser.filter((s) => !!s.harness === h);
      if (!group.length) continue;
      lineSpecs.push({ ti, h,
        seriesNames: group.map((s) => `${s.label}${trackDefs[ti].suffix}`),
        dash: trackDefs[ti].dash ?? (h ? "6,4" : null) });
    }
  const node = Plot.plot({
    // marginTop lifts the y-axis label clear of the "1.0" tick
    width, height, marginRight: railWidth, marginTop: 30, marginBottom: 46,
    marginLeft: 46,
    style: plotStyle(),
    x: centredAxis("Agent tokens per attempt (log)", 36, { type: "log",
      domain: [xLo, xHi], ticks: xTicks,
      tickFormat: (v) => ({ 1e4: "10K", 1e5: "100K", 1e6: "1M" }[v] ?? "") }),
    y: { label: "Cumulative success rate", domain: [0, 1], grid: true,
      ticks: Array.from({ length: 11 }, (_, i) => i / 10),
      tickFormat: (v) => v.toFixed(1) },
    color: { domain: trackDefs.flatMap((td, ti) =>
        ser.map((s) => `${s.label}${td.suffix}`)),
      range: trackDefs.flatMap((td, ti) =>
        ser.map((s) => trackColor(s, ti))) },
    marks: [
      ...lineSpecs.map((ls) =>
        Plot.line(flat.filter((d) => d.ti === ls.ti && d.harness === ls.h),
          { x: "x", y: "y", z: "series", stroke: "series", strokeWidth: 2.4,
            curve: "step-after", clip: true,
            ...(ls.dash ? { strokeDasharray: ls.dash } : {}) })),
      // crosshair hover instead of a floating tip: thin dashed drop-lines
      // from the nearest point to both axes, no value readouts. The pointer
      // transform still sets node.value, so the rail/curve focus softening
      // below keeps working.
      ...(tips ? [
        Plot.ruleX(flat, Plot.pointer({ px: "x", py: "y", maxRadius: 24,
          x: "x", y1: 0, y2: "y",
          stroke: palette.muted, strokeWidth: 1, strokeDasharray: "3,4" })),
        Plot.ruleY(flat, Plot.pointer({ px: "x", py: "y", maxRadius: 24,
          y: "y", x1: xLo, x2: "x",
          stroke: palette.muted, strokeWidth: 1, strokeDasharray: "3,4" })),
      ] : []),
    ],
  });
  const svg = node.tagName === "svg" ? node
    : [...node.querySelectorAll("svg")].pop();
  const xs = node.scale("x"), ys = node.scale("y");
  const [x0, x1] = xs.range;

  // bands behind the curves: hatched (colored) or AISI solid grey
  const styleNode = svg.querySelector("style");
  let insertAt = styleNode ? styleNode.nextSibling : svg.firstChild;
  for (const b of bands) {
    const yTop = ys.apply(b.to ?? 1), yBot = ys.apply(b.from);
    const g = svgEl("g");
    // label centred in the band, white halo so it reads over hatching
    const bandLabel = (fill, weight) => {
      const t = svgEl("text", { x: (x0 + x1) / 2, y: (yTop + yBot) / 2,
        fill, "font-size": "13px", "font-weight": weight,
        "text-anchor": "middle", "dominant-baseline": "central",
        stroke: "#ffffff", "stroke-width": "4", "paint-order": "stroke" });
      t.textContent = b.label;
      g.appendChild(t);
    };
    if (b.hatch) {
      const color = b.color ?? palette.bad;
      const pid = `gl-hatch-${Math.random().toString(36).slice(2, 8)}`;
      const defs = svgEl("defs");
      defs.innerHTML =
        `<pattern id="${pid}" patternUnits="userSpaceOnUse" width="8" height="8" ` +
        `patternTransform="rotate(${b.angle ?? 45})">` +
        `<line x1="0" y1="0" x2="0" y2="8" stroke="${color}" stroke-width="1.6" stroke-opacity=".45"/>` +
        `</pattern>`;
      g.appendChild(defs);
      g.appendChild(svgEl("rect", { x: x0, y: yTop, width: x1 - x0,
        height: yBot - yTop, fill: `url(#${pid})` }));
      if (b.label) bandLabel(color, "600");
    } else {
      g.appendChild(svgEl("rect", { x: x0, y: yTop, width: x1 - x0,
        height: yBot - yTop, fill: "#f4f4f4" }));
      if (b.label) bandLabel("#8a8a8a", "400");
    }
    g.appendChild(svgEl("line", { x1: x0, y1: yBot, x2: x1, y2: yBot,
      stroke: "#c9c9c9", "stroke-width": "1.4" }));
    svg.insertBefore(g, insertAt);
  }

  // right rail: colour marker (dashed/dotted matching the curve), then bold
  // name, then final score; one entry per (series, track), dodged upward
  const items = [];
  for (let ti = 0; ti < trackDefs.length; ti++)
    for (const s of ser) {
      const td = trackDefs[ti];
      const v = s[`${td.track}Acc`] ?? s.officialAcc;
      items.push({ series: `${s.label}${td.suffix}`,
        railLabel: `${s.railLabel ?? s.label}${td.suffix}`,
        color: trackColor(s, ti), dash: td.dash ?? (s.harness ? "4,2.5" : null),
        val: v, ly: v });
    }
  items.sort((a, b) => a.ly - b.ly);
  const MIN_GAP = 0.052;
  for (let i = 1; i < items.length; i++)
    if (items[i].ly - items[i - 1].ly < MIN_GAP)
      items[i].ly = items[i - 1].ly + MIN_GAP;
  svg.style.overflow = "visible";        // never clip a long rail label
  const railEls = {};
  for (const it of items) {
    const yp = ys.apply(it.ly);
    const g = svgEl("g");
    g.appendChild(svgEl("line", { x1: x1 + 8, y1: yp - 8, x2: x1 + 8, y2: yp + 8,
      stroke: it.color, "stroke-width": "4.5",
      ...(it.dash ? { "stroke-dasharray": "3,2" } : {}) }));
    const t = svgEl("text", { x: x1 + 20, y: yp + 4, "text-anchor": "start",
      "font-size": "12px", fill: palette.ink });
    const name = svgEl("tspan", { "font-weight": "700" });
    name.textContent = it.railLabel;
    const val = svgEl("tspan", { dx: "6", "font-size": "11px" });
    val.textContent = it.val.toFixed(3);
    t.appendChild(name); t.appendChild(val);
    g.appendChild(t);
    svg.appendChild(g);
    railEls[it.series] = g;
  }

  // hover: soften every other (series, track) curve and rail entry. Colour
  // doesn't identify a curve (tracks/harness share it), so map DOM paths by
  // mark order: each line mark's paths follow its lineSpec's series order.
  {
    const lineGroups = [...svg.querySelectorAll('g[aria-label^="line"]')];
    const pathOf = {};
    lineGroups.forEach((grp, gi) => {
      const ls = lineSpecs[gi];
      if (!ls) return;
      [...grp.querySelectorAll("path")].forEach((p, i) => {
        if (ls.seriesNames[i]) pathOf[ls.seriesNames[i]] = p;
      });
    });
    const all = [...Object.values(pathOf), ...Object.values(railEls)];
    for (const n of all) n.style.transition = "opacity .15s ease";
    const setFocus = (key) => {
      for (const [k, p] of Object.entries(pathOf))
        p.style.opacity = key && k !== key ? 0.15 : 1;
      for (const [k, g] of Object.entries(railEls))
        g.style.opacity = key && k !== key ? 0.3 : 1;
    };
    node.addEventListener("input", () => setFocus(node.value?.series));
    for (const [key, g] of Object.entries(railEls)) {
      g.addEventListener("pointerenter", () => setFocus(key));
      g.addEventListener("pointerleave", () => setFocus(null));
    }
  }

  const wrap = el("div");
  if (title) wrap.appendChild(el("div",
    `font-family:${FONT};font-weight:700;font-size:1.02rem;color:${palette.ink};margin:0 0 .25rem .1rem;`,
    esc(title)));
  wrap.appendChild(node);
  return frame(wrap, { caption });
}

/* ------------------------------------------------------------ glInfScale */
// Serial vs parallel inference scaling on one shared token axis.
// Colour = model, symbol = arm (× serial, ○ parallel); parallel lines dashed.
//   data: {series: [{name, arm, points: [{x, y, k?, budget?}]}]}
//   The model name is everything in `name` before " serial"/" parallel".
export function glInfScale({ Plot }, data, spec = {}) {
  const { caption, width = 688, height = 440, colors = {} } = spec;
  const flat = [];
  for (const s of data.series) {
    const model = s.name.replace(/ (serial|parallel).*$/, "");
    for (const p of s.points) flat.push({ ...p, model, arm: s.arm, series: s.name });
  }
  const arms = new Set(flat.map((d) => d.arm));
  const fmtTok = (v) => v >= 1e6 ? `${Math.round(v / 1e6)}M`
    : v >= 1e3 ? `${Math.round(v / 1e3)}k` : `${Math.round(v)}`;
  const tipTitle = (d) => {
    const detail = d.k ? ` (k=${d.k})`
      : d.budget != null && d.arm === "serial" ? ` (budget ${d.budget || "off"})` : "";
    const arm = arms.size > 1 ? ` · ${d.arm}` : "";
    return `${d.model}${arm}${detail}\n~${fmtTok(d.x)} tokens · ${(100 * d.y).toFixed(1)}%`;
  };
  const node = Plot.plot({
    // marginTop clears the topmost y tick vertically; marginLeft gives a wide
    // top tick label like "100%" room so its leading digit isn't clipped at
    // the svg's left edge (Plot's 40px default leaves it ~1px of slack).
    width, height, marginBottom: 48, marginTop: 34, marginLeft: 50,
    style: plotStyle(),
    x: centredAxis(spec.xLabel ?? "median generated tokens per question", 40,
      { type: spec.xType ?? "linear", grid: false, domain: spec.xDomain,
        tickFormat: spec.xFmt }),
    y: { label: spec.yLabel ?? "accuracy", grid: true, domain: spec.yDomain ?? [0, 0.85],
      tickFormat: spec.yFmt },
    color: { domain: Object.keys(colors), range: Object.values(colors) },
    symbol: arms.size > 1
      ? { legend: true, domain: ["serial", "parallel"], range: ["times", "circle"] }
      : undefined,
    marks: [
      // strokeDasharray is a constant style in Plot, not a channel — draw
      // dashed series (parallel arm, or points flagged `dash`) as their own mark.
      // clip so curves whose cheap tail sits left of a cropped xDomain don't
      // draw outside the frame.
      Plot.line(flat.filter((d) => !(d.arm === "parallel" || d.dash)),
        { x: "x", y: "y", z: "series", stroke: "model", strokeWidth: 2, clip: true }),
      Plot.line(flat.filter((d) => d.arm === "parallel" || d.dash),
        { x: "x", y: "y", z: "series", stroke: "model", strokeWidth: 2,
          strokeDasharray: "6,4", clip: true }),
      ...(spec.dots === false ? [] : [
        Plot.dot(flat.filter((d) => d.arm === "serial"),
          { x: "x", y: "y", stroke: "model", symbol: "times", r: 5.5, strokeWidth: 2.2,
            clip: true }),
        Plot.dot(flat.filter((d) => d.arm === "parallel"),
          { x: "x", y: "y", stroke: "model", symbol: "circle", r: 5, strokeWidth: 2,
            clip: true }),
      ]),
      Plot.tip(flat, Plot.pointer({ x: "x", y: "y", maxRadius: 26, title: tipTitle })),
    ],
  });
  // House legend under the graph (never above it): one line-sample swatch per
  // model — dashed when that model's series is dashed — laid out on a
  // spec.legendColumns grid, or a centred wrapping row otherwise.
  const dashed = {};
  for (const s of data.series) {
    const model = s.name.replace(/ (serial|parallel).*$/, "");
    dashed[model] = s.arm === "parallel" || s.points.every((p) => p.dash);
  }
  const legendWrap = el("div",
    (spec.legendColumns
      ? `display:grid;grid-template-columns:repeat(${spec.legendColumns},max-content);justify-content:center;`
      : `display:flex;flex-wrap:wrap;justify-content:center;`) +
    `gap:.3rem 1.2rem;font-family:${FONT};font-size:14px;color:${palette.ink};margin-top:.5rem;`);
  const swatchEls = Object.entries(colors).map(([label, col]) => {
    const item = el("span", `display:inline-flex;align-items:center;gap:.4rem;`,
      `<svg width="22" height="10" style="flex:none"><line x1="1" y1="5" x2="21" y2="5" ` +
      `stroke="${col}" stroke-width="2.5"${dashed[label] ? ' stroke-dasharray="5,3"' : ""}/></svg>` +
      esc(label));
    legendWrap.appendChild(item);
    return item;
  });
  // Hover focus: while the tip is up — or a legend name is hovered — soften
  // every other series and its legend entry. Stroke colour is unique per
  // model in `colors`, so lines and dots are matched by their stroke
  // attribute; legend entries by label text.
  {
    const svgRoot = node.tagName === "svg" ? node
      : [...node.querySelectorAll("svg")].pop();
    const markEls = [...svgRoot.querySelectorAll(
      'g[aria-label^="line"] path, g[aria-label^="dot"] path')];
    for (const n of [...markEls, ...swatchEls])
      n.style.transition = "opacity .15s ease";
    const setFocus = (label) => {
      const active = (label && colors[label]) ? colors[label].toLowerCase() : null;
      for (const p of markEls) {
        const s = (p.getAttribute("stroke") || "").toLowerCase();
        p.style.opacity = active && s !== active ? 0.18 : 1;
      }
      for (const sw of swatchEls)
        sw.style.opacity = active && sw.textContent.trim() !== label ? 0.35 : 1;
    };
    node.addEventListener("input", () => setFocus(node.value?.model));
    for (const sw of swatchEls) {
      sw.addEventListener("pointerenter", () => setFocus(sw.textContent.trim()));
      sw.addEventListener("pointerleave", () => setFocus(null));
    }
  }
  // Optional hatched band (METR-style): spec.band = {from, to?, label?, color?}
  // marks a y-range as artifactual (e.g. a broken-question ceiling).
  if (spec.band) {
    const { from, to = (spec.yDomain ?? [0, 0.85])[1], label,
      color = palette.bad, fontSize = 13 } = spec.band;
    const svg = node.tagName === "svg" ? node
      : [...node.querySelectorAll("svg")].pop();
    const xs = node.scale("x"), ys = node.scale("y");
    const [x0, x1] = xs.range;
    const yTop = ys.apply(to), yBot = ys.apply(from);
    const pid = `gl-hatch-${Math.random().toString(36).slice(2, 8)}`;
    const defs = svgEl("defs");
    defs.innerHTML =
      `<pattern id="${pid}" patternUnits="userSpaceOnUse" width="8" height="8" ` +
      `patternTransform="rotate(45)">` +
      `<line x1="0" y1="0" x2="0" y2="8" stroke="${color}" stroke-width="1.8" stroke-opacity=".55"/>` +
      `</pattern>`;
    const styleNode = svg.querySelector("style");
    svg.insertBefore(defs, styleNode ? styleNode.nextSibling : svg.firstChild);
    svg.insertBefore(svgEl("rect", { x: x0, y: yTop, width: x1 - x0,
      height: yBot - yTop, fill: `url(#${pid})` }), defs.nextSibling);
    if (label) {
      // centred in the band, white halo so it reads over the hatching
      const t = svgEl("text", { x: (x0 + x1) / 2, y: (yTop + yBot) / 2,
        fill: color, "font-size": `${fontSize}px`, "font-weight": "600",
        "text-anchor": "middle", "dominant-baseline": "central",
        stroke: "#ffffff", "stroke-width": "4", "paint-order": "stroke" });
      t.textContent = label;
      svg.appendChild(t);
    }
  }
  const wrap = el("div");
  wrap.appendChild(node);
  if (swatchEls.length) wrap.appendChild(legendWrap);
  return frame(wrap, { caption });
}

/* ----------------------------------------------------------- glConfusion */
// Paired 3×3 grade-confusion matrices (original judge vs regrade), diagonal
// in mint, off-diagonal heat in red scaled by row share.
//   data: {flash: {labels, counts, n_runs}, mini: {...}}
export function glConfusion(_, data, spec = {}) {
  const { caption, titles = {
    flash: "Flash-era runs, regraded by mini",
    mini: "mini-era runs, regraded by mini (noise floor)",
  } } = spec;
  const CELL = { w: 74, h: 52 };
  const HEAT_GAIN = 14;                    // off-diagonal share → red opacity

  const wrap = el("div", `display:flex;gap:2rem;flex-wrap:wrap;justify-content:center;font-family:${FONT};`);
  for (const key of ["flash", "mini"]) {
    const d = data[key];
    const G = d.labels;
    const rowTot = d.counts.map((r) => r.reduce((a, b) => a + b, 0));

    const box = el("div");
    box.appendChild(el("div", `font-size:.85rem;color:${palette.muted};margin-bottom:.45rem;text-align:center;`,
      esc(titles[key])));

    const tbl = el("table", "border-collapse:separate;border-spacing:3px;");
    tbl.appendChild(el("tr", null,
      `<td></td>` + G.map((g) =>
        `<td style="text-align:center;font-size:.75rem;color:${palette.soft};padding:2px 6px;">→ ${g}</td>`).join("")));
    d.counts.forEach((row, i) => {
      const cells = row.map((v, j) => {
        const share = rowTot[i] ? v / rowTot[i] : 0;
        const diag = i === j;
        const bg = diag
          ? `rgba(23,207,185,${0.10 + 0.5 * share})`
          : `rgba(209,73,91,${Math.min(0.85, share * HEAT_GAIN)})`;
        const ink = (!diag && share * HEAT_GAIN > 0.45) ? "#fff" : palette.ink;
        return `<td style="width:${CELL.w}px;height:${CELL.h}px;text-align:center;border-radius:5px;background:${bg};color:${ink};">` +
          `<div style="font-size:1rem;font-weight:600;font-variant-numeric:tabular-nums;">${v.toLocaleString()}</div>` +
          `<div style="font-size:.68rem;opacity:.75;">${(100 * share).toFixed(1)}%</div></td>`;
      }).join("");
      tbl.appendChild(el("tr", null,
        `<td style="font-size:.75rem;color:${palette.soft};padding:2px 6px;text-align:right;">${G[i]}</td>` + cells));
    });
    box.appendChild(tbl);
    box.appendChild(el("div", `font-size:.7rem;color:${palette.soft};margin-top:.3rem;text-align:center;`,
      `rows: original grade · ${d.n_runs} runs`));
    wrap.appendChild(box);
  }
  return frame(wrap, { caption });
}

/* -------------------------------------------------------------- glSankey */
// Three-step audit flow with hover highlighting: hovered band widens (never
// grows vertically, so slivers cannot overlap) and its ribbons deepen. The
// final step splits ambiguous + wrong by whether the label error actually
// costs models points, driven by each verdict row's `bites` flag.
//   verdicts: audit_verdicts.json ({final, bites, ...} per flagged item)
export function glSankey(_, verdicts, spec = {}) {
  const { caption, provenance, width = 688, height = 500, total = 1000 } = spec;

  // Layout constants.
  const BAR = 13;                          // band thickness
  const GAP = 15;                          // vertical gap between bands
  const MIN_BAND = 2.5;                    // slivers stay visible
  const HIT_MIN = 30, HIT_PAD = 10;        // invisible hover target size
  const COL = { c1: 0.38, c2: 0.70 };      // column positions as width fractions
  const M = { t: 18, b: 18, l: 142, r: 138 };  // l fits "1000 questions" even at hover font size

  const count = (k) => verdicts.filter((v) => v.final === k).length;
  const bitesOf = (k) => verdicts.filter((v) => v.final === k && v.bites === true).length;
  const FLAG = verdicts.length, NON = total - FLAG;
  const terminals = [
    { id: "fine", name: "fine", n: count("ACTUALLY_FINE"), color: palette.good, side: "right" },
    { id: "unv", name: "unverifiable", n: count("UNVERIFIABLE"), color: palette.neutral, side: "right" },
    { id: "amb", name: "ambiguous", n: count("CONFIRMED_AMBIGUOUS"), color: palette.warn, side: "left" },
    { id: "wrong", name: "wrong", n: count("CONFIRMED_BAD"), color: palette.bad, side: "left" },
  ];
  const ambB = bitesOf("CONFIRMED_AMBIGUOUS"), ambH = count("CONFIRMED_AMBIGUOUS") - ambB;
  const wrB = bitesOf("CONFIRMED_BAD"), wrH = count("CONFIRMED_BAD") - wrB;
  const nCosts = ambB + wrB, nHarm = ambH + wrH;

  const W = width - M.l - M.r, H = height - M.t - M.b;
  const sc = (v) => (v / total) * (H - 6 * GAP);
  const x0 = M.l, x1 = M.l + W * COL.c1, x2 = M.l + W * COL.c2, x3 = M.l + W;

  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}` });
  svg.style.cssText = `width:100%;height:auto;font-family:${FONT};`;
  const style = svgEl("style");
  style.textContent = `
    .gl-band rect.viz { transition: transform .18s ease; transform-box: fill-box; transform-origin: center; }
    .gl-band text { transition: font-size .18s ease; font-size: 13px; fill: ${palette.muted}; }
    .gl-band .num { font-variant-numeric: tabular-nums; }
    .gl-band.hot rect.viz { transform: scaleX(1.45); }
    .gl-band.hot text { font-size: 14.5px; font-weight: 600; fill: ${palette.ink}; }
    .gl-band rect.hit { fill: transparent; pointer-events: all; }
    .gl-ribbon { transition: fill-opacity .18s ease; fill-opacity: .32; }
    .gl-ribbon.hot { fill-opacity: .58; }
  `;
  svg.appendChild(style);

  const ribbonPath = (xa, xb, a0, a1, b0, b1) => {
    const mx = (xa + xb) / 2;
    return `M${xa},${a0} C${mx},${a0} ${mx},${b0} ${xb},${b0} L${xb},${b1} C${mx},${b1} ${mx},${a1} ${xa},${a1} Z`;
  };

  const groups = {}, ribbons = {};
  function band(id, x, y, h, color, name, n, side) {
    const g = svgEl("g", { class: "gl-band" });
    g.dataset.id = id;
    const bandH = Math.max(h, MIN_BAND);
    const hitH = Math.max(bandH + HIT_PAD, HIT_MIN);
    g.appendChild(svgEl("rect", { class: "hit",
      x: x - 6, y: y + bandH / 2 - hitH / 2, width: BAR + 12, height: hitH }));
    g.appendChild(svgEl("rect", { class: "viz", x, y, width: BAR, height: bandH, rx: 2, fill: color }));
    const t = svgEl("text", {
      y: y + bandH / 2 + 4.5,
      x: side === "left" ? x - 10 : x + BAR + 10,
      "text-anchor": side === "left" ? "end" : "start" });
    t.innerHTML = n === "" ? name : `${name} <tspan class="num">${n}</tspan>`;
    g.appendChild(t);
    svg.appendChild(g);
    groups[id] = g;
  }
  function ribbon(id, xa, xb, a0, a1, b0, b1, color) {
    const p = svgEl("path", { class: "gl-ribbon", d: ribbonPath(xa, xb, a0, a1, b0, b1), fill: color });
    p.dataset.id = id;
    svg.insertBefore(p, svg.firstChild.nextSibling);   // ribbons under bands
    (ribbons[id] = ribbons[id] || []).push(p);
  }

  // Column 0: source.
  band("src", x0 - BAR, M.t, sc(total) + GAP, palette.soft, "1000 questions", "", "left");
  // Column 1: not flagged on top, flagged below.
  const nY = M.t, nH = sc(NON);
  const fY = nY + nH + GAP, fH = sc(FLAG);
  band("non", x1, nY, nH, palette.good, "not flagged", NON, "right");
  band("flag", x1, fY, fH, palette.soft, "flagged", FLAG, "left");
  ribbon("non", x0, x1, M.t, M.t + nH, nY, nY + nH, palette.good);
  ribbon("flag", x0, x1, M.t + nH, M.t + nH + fH, fY, fY + fH, palette.soft);
  // Column 2: verdicts from flagged; ambiguous + wrong continue to column 3.
  let ySrc = fY, yDst = fY;
  const pos = {};
  for (const t of terminals) {
    const h = Math.max(sc(t.n), MIN_BAND);
    band(t.id, x2, yDst, sc(t.n), t.color, t.name, t.n, t.side);
    ribbon(t.id, x1 + BAR, x2, ySrc, ySrc + sc(t.n), yDst, yDst + h, t.color);
    pos[t.id] = { y: yDst, h };
    ySrc += sc(t.n); yDst += h + GAP;
  }
  // Column 3: harmless above, costs-points below.
  const hY = pos.amb.y - 2;
  const cY = hY + Math.max(sc(nHarm), MIN_BAND) + GAP * 1.6;
  band("harmless", x3, hY, sc(nHarm), palette.cleanFill, "harmless", nHarm, "right");
  band("costs", x3, cY, sc(nCosts), palette.bad, "costs points", nCosts, "right");
  ribbon("harmless", x2 + BAR, x3, pos.amb.y, pos.amb.y + sc(ambH), hY, hY + sc(ambH), palette.warn);
  ribbon("harmless", x2 + BAR, x3, pos.wrong.y, pos.wrong.y + sc(wrH), hY + sc(ambH), hY + sc(ambH) + sc(wrH), palette.bad);
  ribbon("costs", x2 + BAR, x3, pos.amb.y + sc(ambH), pos.amb.y + sc(ambH) + sc(ambB), cY, cY + sc(ambB), palette.warn);
  ribbon("costs", x2 + BAR, x3, pos.wrong.y + sc(wrH), pos.wrong.y + sc(wrH) + sc(wrB), cY + sc(ambB), cY + sc(ambB) + sc(wrB), palette.bad);

  // Hover wiring: a band and its ribbons heat together, from either side.
  const setHot = (id, hot) => {
    groups[id]?.classList.toggle("hot", hot);
    (ribbons[id] || []).forEach((r) => r.classList.toggle("hot", hot));
  };
  for (const [id, g] of Object.entries(groups)) {
    g.addEventListener("mouseenter", () => setHot(id, true));
    g.addEventListener("mouseleave", () => setHot(id, false));
  }
  for (const [id, rs] of Object.entries(ribbons))
    rs.forEach((r) => {
      r.addEventListener("mouseenter", () => setHot(id, true));
      r.addEventListener("mouseleave", () => setHot(id, false));
    });

  const wrap = el("div");
  wrap.appendChild(svg);
  return frame(wrap, { caption, provenance });
}

/* ----------------------------------------------------------- glAuditCard */
// A random flagged question with the verifier's verdict and reasoning,
// coloured by verdict class; refresh to resample.
//   verdicts: audit_verdicts.json rows
export function glAuditCard(_, verdicts, spec = {}) {
  const { caption } = spec;
  const CLASSES = {
    CONFIRMED_BAD: { name: "wrong", color: palette.bad, bg: palette.badSoft },
    CONFIRMED_AMBIGUOUS: { name: "ambiguous", color: palette.warnInk, bg: "#faf3e0" },
    UNVERIFIABLE: { name: "unverifiable", color: palette.neutralInk, bg: palette.neutralSoft },
  };
  const pool = verdicts.filter((v) => CLASSES[v.final]);

  const card = el("div", `border-radius:6px;padding:.85rem 1.1rem;font-family:${FONT};` +
    `font-size:.93rem;line-height:1.55;transition:opacity .25s ease, background .25s ease, border-color .25s ease;` +
    `border-left:3px solid;min-height:11em;`);
  const render = () => {
    const v = pool[Math.floor(Math.random() * pool.length)];
    const c = CLASSES[v.final];
    card.style.borderLeftColor = c.color;
    card.style.background = c.bg;
    card.innerHTML =
      `<span style="display:inline-block;font-size:.7rem;font-weight:600;letter-spacing:.06em;text-transform:uppercase;` +
      `color:#fff;background:${c.color};border-radius:999px;padding:.14rem .6rem;margin-bottom:.5rem;">${c.name}</span>` +
      `<div>“${esc(v.question)}” <span style="color:${palette.soft}">— gold: <strong>${esc(v.gold)}</strong></span></div>` +
      (v.correct_answer ? `<div style="margin-top:.4rem;color:${c.color};">verifier's answer: <strong>${esc(v.correct_answer)}</strong></div>` : "") +
      `<div style="margin-top:.45rem;color:${palette.muted};font-size:.85rem;">${esc(v.reason)}</div>` +
      (v.source_url ? `<div style="margin-top:.35rem;font-size:.78rem;"><a href="${esc(v.source_url)}" target="_blank" rel="noopener" ` +
        `style="color:${palette.mintDeep};border-bottom:1px solid ${palette.mintEdge};">source</a></div>` : "");
  };
  render();

  const btn = el("button", `background:transparent;color:${palette.mintDeep};border:1px solid ${palette.mintEdge};` +
    `border-radius:999px;padding:.45rem 1.05rem;font-family:${FONT};font-size:.85rem;cursor:pointer;`);
  btn.textContent = "↻ show another flagged question";
  btn.onclick = () => {
    card.style.opacity = 0;
    setTimeout(() => { render(); card.style.opacity = 1; }, 180);
  };
  const btnRow = el("div", "display:flex;justify-content:center;margin-top:.8rem;");
  btnRow.appendChild(btn);

  const wrap = el("div");
  wrap.appendChild(card);
  wrap.appendChild(btnRow);
  return frame(wrap, { caption });
}

/* ------------------------------------------------------ glQuestionTriplet */
// Example-question card(s) with a refresh button. Refresh crossfades to a new
// random question; cards are fixed-height so the button never moves.
//   rows: questions.json; spec.seedItems pins the initial cards by item id;
//   spec.count — how many cards (default 1).
const TRIPLET_CSS = `
  @keyframes gl-fade-out {
    from { opacity: 1; }
    to { opacity: 0; }
  }
  @keyframes gl-fade-in {
    from { opacity: 0; transform: translateY(5px); }
    to { opacity: 1; transform: translateY(0); }
  }
  /* sit close under the preceding prose: cancel the stacked cell+figure margins */
  .cell:has(.gl-qcard) { margin-top: -2.2rem; }
  .gl-qcard { border-left: 3px solid ${palette.mint}; background: ${palette.mintSoft};
    padding: .7rem 1.05rem; border-radius: 0 6px 6px 0; margin: .85rem 0;
    font-family: ${FONT}; font-size: .97rem; line-height: 1.55;
    height: 6.2em; overflow-y: auto; display: flex; align-items: center; }
  .gl-qcard .inner { width: 100%; }
  .gl-qcard .inner.leaving { animation: gl-fade-out .16s ease both; }
  .gl-qcard .inner.entering { animation: gl-fade-in .3s ease both; }
  .gl-qcard .meta { font-size: .74rem; color: ${palette.soft}; margin-top: .3rem; font-variant-numeric: tabular-nums; }
  .gl-qrefresh { background: transparent; color: ${palette.mintDeep}; border: 1px solid ${palette.mintEdge};
    border-radius: 999px; padding: .45rem 1.05rem; font-family: ${FONT}; font-size: .85rem;
    cursor: pointer; transition: background .15s, border-color .15s; }
  .gl-qrefresh:hover { background: ${palette.mintSoft}; border-color: ${palette.mintDeep}; }
`;

export function glQuestionTriplet(_, rows, spec = {}) {
  const { seedItems = [], caption, count = 1 } = spec;
  injectOnce("gl-question-triplet-css", TRIPLET_CSS);

  const byItem = new Map(rows.map((r) => [r.item, r]));
  const current = seedItems.map((i) => byItem.get(i)).filter(Boolean).slice(0, count);
  while (current.length < count) current.push(rows[Math.floor(Math.random() * rows.length)]);

  const wrap = el("div");
  const render = (card, q) => {
    card.querySelector(".inner").innerHTML =
      `“${esc(q.question)}”` +
      `<div class="meta">solved by ${q.n_solved} of ${q.n_models} leaderboard models</div>`;
  };
  const cards = current.map((q) => {
    const card = el("div", null, `<div class="inner"></div>`);
    card.className = "gl-qcard";
    wrap.appendChild(card);
    render(card, q);
    return card;
  });

  const btn = el("button");
  btn.className = "gl-qrefresh";
  btn.textContent = count === 1 ? "↻ show me another" : "↻ show me more";
  btn.onclick = () => {
    for (const card of cards) {
      const inner = card.querySelector(".inner");
      inner.classList.add("leaving");
      inner.addEventListener("animationend", () => {
        inner.classList.remove("leaving");
        render(card, rows[Math.floor(Math.random() * rows.length)]);
        inner.classList.add("entering");
        inner.addEventListener("animationend", () => inner.classList.remove("entering"), { once: true });
      }, { once: true });
    }
  };
  const btnRow = el("div", "display:flex;justify-content:center;margin-top:.9rem;");
  btnRow.appendChild(btn);
  wrap.appendChild(btnRow);
  return frame(wrap, { caption });
}

/* ------------------------------------------------------------- glPipeline */
// Benchmark-pipeline flow diagram: a row of stage cards joined by arrows,
// with optional cards hung below a stage (harness, aggregation, ...) and an
// optional banner row above the flow (e.g. a rubric feeding a stage).
// Hover lifts a card. All wording lives in the calling post's OJS cell.
//   data: {above?: [{title, lines, tone?, span?, arrows?: [flowIdx, ...]}],
//          flow: [{title, lines, tone?, chips?: [{text, tone}],
//                  below?: {title, lines, tone?, arrow?, span?}}]}
//   Tones are palette keys (mintDeep, warnInk, bad, good, neutralInk, ...)
//   or raw hex; spans are grid-columns like "1 / 6". below.arrow: "↓"/"↕",
//   "↘" to branch from the PREVIOUS stage instead, or "none" for no arrow —
//   adjacent below-cards are auto-joined with "→", forming a second pipeline.
export function glPipeline(_, data, spec = {}) {
  const { caption, provenance } = spec;
  const TONES = [palette.neutralInk, palette.mintDeep, palette.warnInk, palette.bad];
  const toneOf = (t, fallback) => palette[t] ?? t ?? fallback;
  const card = (s) => {
    const c = el("div",
      `border:1px solid ${palette.hairline};border-top:3px solid ${s.accent};` +
      `border-radius:6px;padding:.6rem .7rem;background:${palette.bg};` +
      `transition:transform .15s ease, box-shadow .15s ease;`);
    c.onmouseenter = () => { c.style.transform = "translateY(-2px)";
      c.style.boxShadow = "0 3px 10px rgba(0,0,0,.07)"; };
    c.onmouseleave = () => { c.style.transform = ""; c.style.boxShadow = ""; };
    c.appendChild(el("div",
      `font-weight:600;font-size:.9rem;color:${palette.ink};margin-bottom:.3rem;`,
      esc(s.title)));
    for (const line of s.lines) c.appendChild(el("div",
      `font-size:.76rem;color:${palette.muted};line-height:1.45;margin-bottom:.25rem;`,
      esc(line)));
    if (s.chips) {
      const chips = el("div", "display:flex;gap:.3rem;margin-top:.35rem;");
      for (const ch of s.chips) chips.appendChild(el("span",
        `font-size:.68rem;font-weight:600;letter-spacing:.04em;text-transform:uppercase;` +
        `color:#fff;background:${toneOf(ch.tone, palette.soft)};border-radius:999px;padding:.12rem .5rem;`,
        esc(ch.text)));
      c.appendChild(chips);
    }
    return c;
  };
  const arrow = (glyph) => el("div",
    `display:flex;align-items:center;justify-content:center;color:${palette.soft};font-size:1.15rem;`,
    glyph);
  const place = (node, col, row) => {
    node.style.gridColumn = col;
    node.style.gridRow = row;
    return node;
  };

  // Grid: N card columns with arrow gutters between (odd cols cards, even
  // cols arrows). Optional banner row (+ its ↓ arrows) sits above the main
  // flow; below it, vertical/branch arrows, then any `below` cards, each
  // under (or spanning from) its parent stage.
  const flow = data.flow;
  const above = data.above ?? [];
  const off = above.length ? 2 : 0;          // rows taken by the banner
  const grid = el("div",
    `display:grid;font-family:${FONT};` +
    `grid-template-columns:${flow.map(() => "1fr").join(" 22px ")};` +
    `row-gap:.15rem;align-items:stretch;min-width:560px;`);
  above.forEach((s, i) => {
    grid.appendChild(place(card({ ...s, accent: toneOf(s.tone, palette.neutralInk) }),
      s.span ?? "1 / -1", "1"));
    for (const idx of s.arrows ?? [])
      grid.appendChild(place(arrow("↓"), `${2 * idx + 1}`, "2"));
  });
  flow.forEach((s, i) => {
    const col = `${2 * i + 1}`;
    if (i) grid.appendChild(place(arrow("→"), `${2 * i}`, `${1 + off}`));
    grid.appendChild(place(card({ ...s, accent: toneOf(s.tone, TONES[i % TONES.length]) }),
      col, `${1 + off}`));
    if (s.below) {
      const a = s.below.arrow ?? "↓";
      // "↘" branches from the previous stage: the glyph sits in the gutter
      if (a === "↘") grid.appendChild(place(arrow("↘"), `${2 * i}`, `${2 + off}`));
      else if (a !== "none") grid.appendChild(place(arrow(a), col, `${2 + off}`));
      grid.appendChild(place(card({ ...s.below,
        accent: toneOf(s.below.tone, TONES[i % TONES.length]) }),
        s.below.span ?? col, `${3 + off}`));
      // adjacent below-cards form their own pipeline: join with →
      if (i && flow[i - 1].below)
        grid.appendChild(place(arrow("→"), `${2 * i}`, `${3 + off}`));
    }
  });

  // narrow screens scroll the diagram rather than crushing the cards
  const scroller = el("div", "overflow-x:auto;");
  scroller.appendChild(grid);
  // bottom-left footnote inside the diagram (e.g. acronym expansions)
  if (spec.footnote) scroller.appendChild(el("div",
    `font-size:.7rem;color:${palette.soft};margin-top:.35rem;text-align:left;line-height:1.5;`,
    esc(spec.footnote)));
  return frame(scroller, { caption, provenance });
}

/* ------------------------------------------------------------------ glPie */
// Part-to-whole donut: a small number of segments that add to the whole, with
// every segment named and valued in the key beside it. Suits <= 6 segments
// whose values are well separated; for close values or many classes use
// glBars instead.
//   rows: [{label, value, count?}]  (value in the same unit for every row)
//   spec.colorDomain / colorRange — label order and its fixed hue order
//   spec.valueFmt — how a key value is printed (default: one decimal + %)
//   spec.ringFmt  — the label on the segment itself, given the row; defaults
//                   to valueFmt(row.value), so the ring and the key agree
//   spec.countFmt — the second line of the segment's hover tooltip
//   spec.keySub   — a muted line under each key row; omitted when not given
//   spec.total    — text drawn in the middle of the donut
//   spec.totalSub — smaller text under it; pass an array to wrap it over
//                   several lines, since anything wider than the hole runs
//                   across the segments
export function glPie(rows, spec = {}) {
  const {
    caption, provenance, width = 688, valueFmt = (v) => `${v.toFixed(1)}%`,
    ringFmt, countFmt, keySub, total, totalSub, keyVal = true, center,
    colorDomain, colorRange = palette.series, className, margin,
  } = spec;

  const colorOf = (label) => {
    const i = colorDomain ? colorDomain.indexOf(label) : -1;
    return colorRange[(i < 0 ? rows.indexOf(rows.find((r) => r.label === label)) : i) % colorRange.length];
  };

  const H = 236, CY = H / 2, R = 92, INNER = R * 0.6;
  // `center: true` centres the donut + key block inside the frame: the
  // content spans from the donut's left edge (incl. outside labels) to the
  // end of the key's longest label (plus the value column when keyVal is
  // on); shift both anchors so that span sits mid-frame.
  const left = 118 - R - 15;
  const right = 268 + 19 + 7.2 * Math.max(...rows.map((r) => r.label.length)) +
    (keyVal ? 60 : 0);
  const shift = center
    ? Math.max(0, (width - (right - left)) / 2 - left) : 0;
  const CX = 118 + shift, KX = 268 + shift;
  const sum = rows.reduce((a, r) => a + r.value, 0) || 1;
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${H}`, width: "100%",
    style: `min-width:520px;font-family:${FONT};` });

  // Arc path for a donut segment, drawn clockwise from 12 o'clock.
  const arc = (a0, a1) => {
    if (a1 <= a0) return "";
    const p = (a, r) => [CX + r * Math.sin(a), CY - r * Math.cos(a)];
    const [x0, y0] = p(a0, R), [x1, y1] = p(a1, R);
    const [x2, y2] = p(a1, INNER), [x3, y3] = p(a0, INNER);
    // A single SVG arc cannot represent a complete circle with coincident ends.
    if (a1 - a0 >= 2 * Math.PI - 1e-9) {
      const [xm, ym] = p(a0 + Math.PI, R), [xim, yim] = p(a0 + Math.PI, INNER);
      return `M ${x0} ${y0} A ${R} ${R} 0 0 1 ${xm} ${ym}` +
             ` A ${R} ${R} 0 0 1 ${x0} ${y0} L ${x3} ${y3}` +
             ` A ${INNER} ${INNER} 0 0 0 ${xim} ${yim}` +
             ` A ${INNER} ${INNER} 0 0 0 ${x3} ${y3} Z`;
    }
    const big = a1 - a0 > Math.PI ? 1 : 0;
    return `M ${x0} ${y0} A ${R} ${R} 0 ${big} 1 ${x1} ${y1}` +
           ` L ${x2} ${y2} A ${INNER} ${INNER} 0 ${big} 0 ${x3} ${y3} Z`;
  };

  const segs = [];
  let acc = 0;
  for (const r of rows) {
    const a0 = (acc / sum) * 2 * Math.PI;
    acc += r.value;
    const a1 = (acc / sum) * 2 * Math.PI;
    segs.push({ ...r, a0, a1, mid: (a0 + a1) / 2, color: colorOf(r.label) });
  }

  // Hovering a segment or its key row dims everything else, so identity is
  // never carried by colour alone.
  const parts = [];
  const focus = (i) => {
    for (const p of parts) {
      const on = i == null || p.i === i;
      p.seg.setAttribute("opacity", on ? 1 : 0.28);
      p.row.style.opacity = on ? "1" : "0.45";
    }
  };

  segs.forEach((s, i) => {
    // The 2px surface-coloured stroke is the gap between adjacent fills.
    const path = svgEl("path", { d: arc(s.a0, s.a1), fill: s.color,
      stroke: palette.bg, "stroke-width": 2, "stroke-linejoin": "round",
      style: "transition:opacity .12s ease;cursor:default;" });
    const tip = svgEl("title", {});
    tip.textContent = `${s.label}: ${valueFmt(s.value)}` +
      (countFmt ? `\n${countFmt(s)}` : "");
    path.appendChild(tip);
    path.onmouseenter = () => focus(i);
    path.onmouseleave = () => focus(null);
    svg.appendChild(path);
    parts.push({ i, seg: path, row: null });
  });

  // Value labels: inside the segment when it is wide enough to hold one,
  // otherwise just outside with a short leader.
  segs.forEach((s) => {
    if (s.value <= 0) return;
    const wide = s.a1 - s.a0 > 0.42;
    const r = wide ? (R + INNER) / 2 : R + 15;
    const x = CX + r * Math.sin(s.mid), y = CY - r * Math.cos(s.mid);
    if (!wide) {
      const [x0, y0] = [CX + (R + 2) * Math.sin(s.mid), CY - (R + 2) * Math.cos(s.mid)];
      const [x1, y1] = [CX + (R + 8) * Math.sin(s.mid), CY - (R + 8) * Math.cos(s.mid)];
      svg.appendChild(svgEl("path", { d: `M ${x0} ${y0} L ${x1} ${y1}`,
        stroke: palette.soft, "stroke-width": 1, fill: "none" }));
    }
    const t = svgEl("text", { x, y: y + 3, "text-anchor": "middle",
      "font-size": wide ? 9 : 8, "font-weight": 600,
      "font-family": FONT, fill: wide ? "#fff" : palette.muted,
      "pointer-events": "none" });
    t.textContent = ringFmt ? ringFmt(s) : valueFmt(s.value);
    svg.appendChild(t);
  });

  const subLines = totalSub == null ? []
    : Array.isArray(totalSub) ? totalSub : [totalSub];
  if (total) {
    const t = svgEl("text", { x: CX, y: CY + (subLines.length ? -4 : 5),
      "text-anchor": "middle", "font-size": 20, "font-weight": 600,
      "font-family": FONT, fill: palette.ink });
    t.textContent = total;
    svg.appendChild(t);
  }
  subLines.forEach((line, i) => {
    const t = svgEl("text", { x: CX, y: CY + 11 + i * 12, "text-anchor": "middle",
      "font-size": 9.5, "font-family": FONT, fill: palette.soft });
    t.textContent = line;
    svg.appendChild(t);
  });

  // Key: swatch, name, value and count. Doubles as the table view.
  const rowH = Math.min(46, (H - 30) / segs.length);
  const KY = CY - (segs.length * rowH) / 2 + rowH / 2;
  segs.forEach((s, i) => {
    const y = KY + i * rowH;
    const g = svgEl("g", { style: "transition:opacity .12s ease;cursor:default;" });
    g.appendChild(svgEl("rect", { x: KX, y: y - 12, width: 11, height: 11, rx: 2.5,
      fill: s.color }));
    const name = svgEl("text", { x: KX + 19, y: y - 2, "font-size": 13,
      "font-family": FONT, fill: palette.ink });
    name.textContent = s.label;
    g.appendChild(name);
    if (keyVal) {
      const val = svgEl("text", { x: width - 8, y: y - 2, "text-anchor": "end",
        "font-size": 13, "font-weight": 600, "font-family": FONT, fill: palette.ink });
      val.textContent = valueFmt(s.value);
      g.appendChild(val);
    }
    if (keySub) {
      const sub = svgEl("text", { x: KX + 19, y: y + 13, "font-size": 11,
        "font-family": FONT, fill: palette.soft });
      sub.textContent = keySub(s);
      g.appendChild(sub);
    }
    g.onmouseenter = () => focus(i);
    g.onmouseleave = () => focus(null);
    svg.appendChild(g);
    parts[i].row = g;
  });

  const scroller = el("div", "overflow-x:auto;");
  scroller.appendChild(svg);
  const fig = frame(scroller, { caption, provenance });
  if (className) fig.classList.add(className);
  if (margin) fig.style.margin = margin;
  return fig;
}

/* --------------------------------------------------------- glStackedBars */
// Horizontal stacked bars: one bar per row, segments stacked left-to-right
// with counts printed inside. Segment tones are palette keys or hex.
//   rows: [{label, segments: [{name, value, tone?}]}]
//   spec.xLabel — axis label; segment names become the legend.
export function glStackedBars({ Plot }, rows, spec = {}) {
  const { caption, provenance, width = 688, xLabel = "" } = spec;
  const names = [...new Set(rows.flatMap((r) => r.segments.map((s) => s.name)))];
  const toneFor = (name) => {
    for (const r of rows) {
      const s = r.segments.find((x) => x.name === name);
      if (s) return palette[s.tone] ?? s.tone ?? palette.series[names.indexOf(name)];
    }
  };
  const colors = names.map(toneFor);
  const flat = rows.flatMap((r) => r.segments
    .filter((s) => s.value > 0)
    .map((s) => ({ label: r.label, name: s.name, value: s.value })));
  const height = rows.length * 56 + 84;
  const node = Plot.plot({
    width, height, marginLeft: 170, marginBottom: 42,
    style: plotStyle("12px"),
    x: centredAxis(xLabel, 36, { grid: true }),
    y: { label: null, domain: rows.map((r) => r.label) },
    color: { domain: names, range: colors },
    marks: [
      Plot.barX(flat, Plot.stackX({ y: "label", x: "value", z: "name",
        fill: "name", order: names, rx: 2, insetTop: 1.5, insetBottom: 1.5 })),
      Plot.text(flat, Plot.stackX({ y: "label", x: "value", z: "name",
        order: names, text: (d) => String(d.value), fill: "#ffffff",
        fontWeight: 600 })),
      Plot.tip(flat, Plot.pointer(Plot.stackX({ y: "label", x: "value",
        z: "name", order: names, maxRadius: 40,
        title: (d) => `${d.name}\n${d.value} responses` }))),
    ],
  });
  const wrap = el("div");
  wrap.appendChild(node);
  wrap.appendChild(legendRow(names, colors));
  return frame(wrap, { caption, provenance });
}

/* -------------------------------------------------------- glMetricScatter */
// Axis-switchable model scatter for audit posts. Colour = provider family;
// symbol = weights class (closed ●, open ○) with mini-audit reruns as
// haloed diamonds, joined to their leaderboard twin by an arrow showing the
// movement. Hovering a point softens everything unrelated to it. Pair with
// button/toggle cells for the axis pickers and filters.
//   rows: [{model, lbModel?, provider, open, source: "leaderboard"|"ours",
//           ars, ors, <other metric keys: eci, date, ...>}]
//   spec.x / spec.y: {key, label, time?, zero?, fmt?} — time parses ISO
//   dates; zero anchors the axis at 0; fmt formats tooltip values.
export function glMetricScatter({ Plot }, rows, spec = {}) {
  const { caption, provenance, width = 700, height = 480,
    colorDomain, colorRange, pointRadius = 5,
    errorStrokeWidth = 1.1, errorOpacity: baseErrorOpacity = 0.55 } = spec;
  const ax = (a) => ({ time: false, zero: false, fmt: (v) => v, ...a });
  const X = ax(spec.x), Y = ax(spec.y);
  const val = (r, a) => (a.time ? new Date(r[a.key]) : r[a.key]);
  const pts = rows
    .filter((r) => r[X.key] != null && r[Y.key] != null)
    .map((r) => ({ ...r, _x: val(r, X), _y: val(r, Y) }));
  // tooltip shows the plotted y metric (± its CI when present); spec.tipX
  // adds the x metric too, for metric-vs-metric views
  const ciKeyTip = `${Y.key}Ci`;
  const tip = (d) => `${d.model}\n${d.source === "ours" ? `audit (${d.auditVariant ?? "regraded"})` : "leaderboard"}`;
  const lb = pts.filter((d) => d.source !== "ours");
  const closed = lb.filter((d) => !d.open);
  const opened = lb.filter((d) => d.open);
  const ours = pts.filter((d) => d.source === "ours");
  // movement arrows: leaderboard twin -> our rerun of the same model
  const lbByModel = new Map(lb.map((d) => [d.model, d]));
  const links = ours
    .map((o) => ({ o, l: lbByModel.get(o.lbModel) }))
    .filter((p) => p.l)
    .map((p) => ({ x1: p.l._x, y1: p.l._y, x2: p.o._x, y2: p.o._y,
      o: p.o, l: p.l }));
  // optional error whiskers: rows carrying <yKey>Ci get a vertical ±
  // segment; rows carrying <xKey>Ci get a horizontal one (metric x-axes)
  const ciKey = `${Y.key}Ci`;
  const xCiKey = `${X.key}Ci`;
  // a score bounded at zero cannot have an interval reaching below it
  const lo = (v, ci, ax) => (ax.zero ? Math.max(0, v - ci) : v - ci);
  const errorOpacity = (d) => d.source === "ours" ? baseErrorOpacity : (spec.lbOpacity ?? baseErrorOpacity);
  const errMarks = spec.errors ? [
    Plot.ruleX(pts.filter((d) => d[ciKey] != null), { x: "_x",
      y1: (d) => lo(d._y, d[ciKey], Y), y2: (d) => d._y + d[ciKey],
      stroke: "provider", strokeWidth: errorStrokeWidth, strokeOpacity: errorOpacity }),
    Plot.ruleY(pts.filter((d) => !X.time && d[xCiKey] != null), { y: "_y",
      x1: (d) => lo(d._x, d[xCiKey], X), x2: (d) => d._x + d[xCiKey],
      stroke: "provider", strokeWidth: errorStrokeWidth, strokeOpacity: errorOpacity }),
  ] : [];
  // optional per-weights-class linear fits: solid = closed, dashed = open
  const fitMarks = spec.fit ? [
    Plot.linearRegressionY(closed, { x: "_x", y: "_y", stroke: "#52514e",
      strokeWidth: 1.5, ci: 0 }),
    Plot.linearRegressionY(opened, { x: "_x", y: "_y", stroke: "#52514e",
      strokeWidth: 1.5, strokeDasharray: "5,4", ci: 0 }),
  ] : [];
  // mark order matters: hover wiring below finds the groups by this order
  const node = Plot.plot({
    width, height, marginBottom: 46,
    style: plotStyle(),
    x: centredAxis(X.label, 38, { grid: false, zero: X.zero,
      type: X.time ? "time" : undefined }),
    y: { label: Y.label, grid: true, zero: Y.zero },
    color: { domain: colorDomain, range: colorRange },
    marks: [
      ...(spec.links === false ? [] :
        [spec.linkStyle === "line"
          ? Plot.link(links, { x1: "x1", y1: "y1", x2: "x2", y2: "y2",
              stroke: "#a8a5a0", strokeWidth: 1.3 })
          : Plot.arrow(links, { x1: "x1", y1: "y1", x2: "x2", y2: "y2",
              stroke: "#a8a5a0", strokeWidth: 1.3, headLength: 8,
              insetStart: 7, insetEnd: 11 })]),
      ...fitMarks,
      ...errMarks,
      Plot.dot(closed, { x: "_x", y: "_y", fill: "provider", r: pointRadius,
        fillOpacity: spec.lbOpacity ?? 0.85 }),
      Plot.dot(opened, { x: "_x", y: "_y", stroke: "provider", r: pointRadius,
        strokeWidth: pointRadius * 0.36, strokeOpacity: spec.lbOpacity ?? 1 }),
      Plot.dot(ours.filter((d) => !d.open), { x: "_x", y: "_y",
        fill: "provider", symbol: spec.auditSymbol ?? "diamond2", r: pointRadius }),
      Plot.dot(ours.filter((d) => d.open), { x: "_x", y: "_y",
        stroke: "provider", symbol: spec.auditSymbol ?? "diamond2", r: pointRadius, strokeWidth: pointRadius * 0.36 }),
      Plot.tip(pts, Plot.pointer({ x: "_x", y: "_y", maxRadius: 30, title: tip })),
    ],
  });
  // whisker elements are collected so hover can fade them with their dot
  const errEls = [];
  const errorCaps = svgEl("g", { "aria-label": "error-bar caps" });
  node.insertBefore(errorCaps, node.querySelector("g[aria-label=dot]"));
  if (spec.errors) {
    const xs = node.scale("x"), ys = node.scale("y"),
      cs = node.scale("color");
    for (const d of pts) {
      const stroke = cs ? cs.apply(d.provider) : "#a8a5a0";
      if (d[ciKey] != null) {
        const px = xs.apply(d._x);
        for (const yv of [lo(d._y, d[ciKey], Y), d._y + d[ciKey]]) {
          errEls.push({ p: errorCaps.appendChild(svgEl("line",
            { x1: px - 3.5, x2: px + 3.5,
              y1: ys.apply(yv), y2: ys.apply(yv), stroke,
              "stroke-width": errorStrokeWidth, "stroke-opacity": errorOpacity(d) })), d });
        }
      }
      if (!X.time && d[xCiKey] != null) {
        const py = ys.apply(d._y);
        for (const xv of [lo(d._x, d[xCiKey], X), d._x + d[xCiKey]]) {
          errEls.push({ p: errorCaps.appendChild(svgEl("line",
            { y1: py - 3.5, y2: py + 3.5,
              x1: xs.apply(xv), x2: xs.apply(xv), stroke,
              "stroke-width": errorStrokeWidth, "stroke-opacity": errorOpacity(d) })), d });
        }
      }
    }
  }
  // focus machinery: one predicate softens every non-matching dot; arrows
  // stay lit when either of their endpoints matches. Used by point hover
  // (Plot.pointer publishes the hovered datum on node.value) and by the
  // interactive legend below. Mark groups are matched by render order.
  const dotGroups = [...node.querySelectorAll("g[aria-label=dot]")];
  const dotEls = [];
  [closed, opened, ours.filter((d) => !d.open),
    ours.filter((d) => d.open)].forEach((arr, gi) => {
    const g = dotGroups[gi];
    if (g) [...g.children].forEach((p, i) => dotEls.push({ p, d: arr[i] }));
  });
  // the stems themselves: errMarks renders ruleX (y-interval) then ruleY
  // (x-interval), so the rule groups come back in that order
  if (spec.errors) {
    const ruleGroups = [...node.querySelectorAll("g[aria-label=rule]")];
    [pts.filter((d) => d[ciKey] != null),
      pts.filter((d) => !X.time && d[xCiKey] != null)].forEach((arr, gi) => {
      const g = ruleGroups[gi];
      if (g) [...g.children].forEach((p, i) => {
        if (arr[i]) errEls.push({ p, d: arr[i] });
      });
    });
  }
  const arrowG = node.querySelector("g[aria-label=arrow], g[aria-label=link]");
  const linkEls = [...(arrowG?.children ?? [])].map((p, i) => ({ p, d: links[i] }));
  const setFocus = (pred) => {
    for (const { p, d } of dotEls) {
      const highlighted = pred && pred(d);
      p.style.opacity = (!pred || highlighted) ? 1 : 0.2;
      if (d.source !== "ours") {
        const baseOpacity = spec.lbOpacity ?? (d.open ? 1 : 0.85);
        p.style[d.open ? "strokeOpacity" : "fillOpacity"] = highlighted ? 1 : baseOpacity;
      }
    }
    for (const { p, d } of linkEls)
      p.style.opacity = (!pred || pred(d.o) || pred(d.l)) ? 1 : 0.15;
    // whiskers fade harder than their dots: they are the busiest ink here
    for (const { p, d } of errEls) {
      const highlighted = pred && pred(d);
      p.style.opacity = (!pred || highlighted) ? 1 : 0.2;
      p.style.strokeOpacity = errorOpacity(d);
    }
  };
  node.addEventListener("input", () => {
    const v = node.value;
    setFocus(v == null ? null :
      (d) => d.model === v.model && d.source === v.source && d.auditVariant === v.auditVariant);
  });

  const wrap = el("div");
  wrap.appendChild(node);
  if (spec.legend === false) {
    const fig = frame(wrap, { caption, provenance });
    fig.glFocus = setFocus;   // external legends can drive this panel's focus
    return fig;
  }
  // interactive legend: hovering an entry focuses its subset of the plot
  const legendRowEl = () => el("div",
    `display:flex;flex-wrap:wrap;justify-content:center;gap:.35rem 1.1rem;` +
    `font-family:${FONT};font-size:12px;color:${palette.muted};margin-top:.3rem;`);
  const legendItem = (row, html, pred) => {
    const item = el("span",
      `display:inline-flex;align-items:center;gap:.35rem;cursor:default;`, html);
    item.onmouseenter = () => setFocus(pred);
    item.onmouseleave = () => setFocus(null);
    row.appendChild(item);
  };
  if (colorDomain) {
    const row = legendRowEl();
    colorDomain.forEach((name, i) => legendItem(row,
      `<span style="width:10px;height:10px;border-radius:3px;background:${colorRange[i]};display:inline-block"></span>${esc(name)}`,
      (d) => d.provider === name));
    wrap.appendChild(row);
  }
  {
    const row = legendRowEl();
    legendItem(row, "● closed-weights", (d) => d.source !== "ours" && !d.open);
    legendItem(row, "○ open-weights", (d) => d.source !== "ours" && d.open);
    legendItem(row, `<span style="font-size:16px;line-height:1">◆</span> <span style="font-size:16px;line-height:1">◇</span> audit`, (d) => d.source === "ours");
    wrap.appendChild(row);
  }
  return frame(wrap, { caption, provenance });
}

/* ---------------------------------------------------------- glFlowSankey */
// Generic column-flow Sankey. Bars are thin and unlabelled by count; a
// readout card follows the cursor instead. Columns are vertically centred so
// facing edges never line up and every ribbon bends. Hovering a ribbon heats
// it and everything upstream of its source (its provenance); hovering a bar
// heats the bar and every flow incident to it.
//   data: {columns: [[{id, label, count, tone?}], ...],
//          flows: [{from, to, count, tone?}], total?}
//   spec: caption, provenance, width, height, labelWidth, tintFrom (first
//   column index whose labels take their bar's colour; default 2).
export function glFlowSankey(_, data, spec = {}) {
  const { caption, provenance, width = 720, height = 470,
    labelWidth = 175, tintFrom = 2 } = spec;
  const toneOf = (t) => palette[t] ?? t ?? palette.soft;
  const BAR = 9, GAP = 26, MIN = 3;
  const M = { t: 16, b: 12 };
  const cols = data.columns;
  const total = data.total ?? cols[0].reduce((s, n) => s + n.count, 0);
  const avail = height - M.t - M.b;
  const maxGaps = Math.max(...cols.map((c) => (c.length - 1) * GAP));
  const unit = (avail - maxGaps) / total;
  const sc = (v) => Math.max(v * unit, MIN);
  const xAt = (i) => 8 + i * ((width - labelWidth - 8 - BAR) / (cols.length - 1));
  const pct = (v) => `${v.toLocaleString()} / ${total.toLocaleString()}` +
    ` (${Math.round(100 * v / total)}%)`;

  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}` });
  svg.style.cssText = `width:100%;height:auto;font-family:${FONT};`;
  const style = svgEl("style");
  style.textContent = `
    .gl-fs-band rect.viz { transition: transform .18s ease; transform-box: fill-box; transform-origin: center; }
    .gl-fs-band text { transition: font-size .18s ease; font-size: 12.5px; fill: ${palette.ink}; font-weight: 600; }
    .gl-fs-band.hot rect.viz { transform: scaleX(1.6); }
    .gl-fs-band.hot text { font-size: 13.5px; }
    .gl-fs-ribbon { transition: fill-opacity .18s ease; fill-opacity: .30; }
    .gl-fs-ribbon.hot { fill-opacity: .55; }
  `;
  svg.appendChild(style);
  // full-size transparent rect so the svg sees pointer moves everywhere
  const bg = svgEl("rect", { x: 0, y: 0, width, height, fill: "transparent" });
  bg.style.pointerEvents = "all";
  svg.appendChild(bg);

  // every column is vertically centred on the canvas
  const colY = (contentH) => M.t + Math.max(0, (avail - contentH) / 2);
  const nodes = {};
  cols.forEach((col, ci) => {
    const contentH = col.reduce((a, n) => a + sc(n.count), 0) + (col.length - 1) * GAP;
    let y = colY(contentH);
    for (const n of col) {
      const h = sc(n.count), x = xAt(ci);
      const g = svgEl("g", { class: "gl-fs-band" });
      g.appendChild(svgEl("rect", { class: "viz", x, y, width: BAR,
        height: h, rx: 2, fill: toneOf(n.tone) }));
      // a label may carry newlines: lines stack, centred on the bar. An empty
      // label draws a bare bar — for pass-through bands that only exist to
      // keep a ribbon from cutting across its neighbours; `title` still names
      // them in the hover readout.
      const lines = n.label ? String(n.label).split("\n") : [];
      lines.forEach((ln, li) => {
        const t = svgEl("text", { x: x + BAR + 9,
          y: y + h / 2 + 4.5 - (lines.length - 1) * 7 + li * 14 });
        t.textContent = ln;
        g.appendChild(t);
      });
      svg.appendChild(g);
      nodes[n.id] = { id: n.id, g, x, y, h,
        title: n.title ?? lines.join(" "), count: n.count };
      y += h + GAP;
    }
  });

  // ribbons tile each bar pro-rata, so min-height clamping never leaves gaps
  const outT = new Map(), inT = new Map();
  for (const f of data.flows) {
    outT.set(f.from, (outT.get(f.from) || 0) + f.count);
    inT.set(f.to, (inT.get(f.to) || 0) + f.count);
  }
  const outC = new Map(), inC = new Map(), ribbons = [];
  for (const f of data.flows) {
    const a = nodes[f.from], b = nodes[f.to];
    if (!a || !b) continue;
    const h0 = a.h * f.count / outT.get(f.from);
    const h1 = b.h * f.count / inT.get(f.to);
    const y0 = a.y + (outC.get(f.from) || 0);
    const y1 = b.y + (inC.get(f.to) || 0);
    outC.set(f.from, (outC.get(f.from) || 0) + h0);
    inC.set(f.to, (inC.get(f.to) || 0) + h1);
    const x0 = a.x + BAR, x1 = b.x;
    // control points pulled past the midpoint give a stronger S-curve
    const cx0 = x0 + 0.68 * (x1 - x0), cx1 = x1 - 0.68 * (x1 - x0);
    const dpath = `M${x0},${y0} C${cx0},${y0} ${cx1},${y1} ${x1},${y1}` +
      ` L${x1},${y1 + h1} C${cx1},${y1 + h1} ${cx0},${y0 + h0} ${x0},${y0 + h0} Z`;
    const p = svgEl("path", { class: "gl-fs-ribbon", d: dpath, fill: toneOf(f.tone) });
    p.dataset.src = f.from; p.dataset.dst = f.to;
    p.dataset.tip = `${a.title} \u2192 ${b.title}\n${pct(f.count)}`;
    p.style.pointerEvents = "none";
    svg.insertBefore(p, bg.nextSibling);
    ribbons.push(p);
  }

  // readout card follows the cursor, flipping near the right edge
  const wrap = el("div", "position:relative;overflow-x:auto;");
  wrap.appendChild(svg);
  const tipEl = el("div", "position:absolute;left:0;top:0;display:none;" +
    "pointer-events:none;background:#fff;border:1px solid #e3e3e3;" +
    "border-radius:6px;padding:.28rem .6rem;font-size:12px;line-height:1.35;" +
    `color:${palette.ink};box-shadow:0 2px 8px rgba(0,0,0,.08);white-space:pre;` +
    "font-variant-numeric:tabular-nums;");
  wrap.appendChild(tipEl);
  const showTip = (e, txt) => {
    const b = wrap.getBoundingClientRect();
    const x = e.clientX - b.left, flip = x > b.width - 190;
    tipEl.textContent = txt;
    tipEl.style.display = "block";
    tipEl.style.left = flip ? "auto" : `${x + 12}px`;
    tipEl.style.right = flip ? `${b.width - x + 12}px` : "auto";
    tipEl.style.top = `${e.clientY - b.top - 34}px`;
  };
  const hideTip = () => { tipEl.style.display = "none"; };

  const setState = (hotRibbons, hotBands, includeIncident) => {
    for (const r of ribbons)
      r.classList.toggle("hot", hotRibbons.has(r) || (includeIncident &&
        (hotBands.has(r.dataset.src) || hotBands.has(r.dataset.dst))));
    for (const n of Object.values(nodes))
      n.g.classList.toggle("hot", hotBands.has(n.id));
  };
  // the hovered ribbon plus, transitively, every ribbon upstream of it
  const backChain = (rb) => {
    const hot = new Set([rb]);
    const bands = new Set([rb.dataset.src, rb.dataset.dst]);
    let frontier = [rb.dataset.src];
    while (frontier.length) {
      const next = [];
      for (const r of ribbons)
        if (!hot.has(r) && frontier.includes(r.dataset.dst)) {
          hot.add(r); bands.add(r.dataset.src); next.push(r.dataset.src);
        }
      frontier = next;
    }
    return { hot, bands };
  };
  const svgPt = (e) => {
    const p = svg.createSVGPoint();
    p.x = e.clientX; p.y = e.clientY;
    return p.matrixTransform(svg.getScreenCTM().inverse());
  };
  const bandAt = (pt) => Object.values(nodes).find((n) => {
    const yc = n.y + n.h / 2, half = Math.max(n.h / 2 + 8, 15);
    return pt.x >= n.x - 8 && pt.x <= n.x + BAR + labelWidth &&
      pt.y >= yc - half && pt.y <= yc + half;
  });
  svg.addEventListener("pointermove", (e) => {
    const pt = svgPt(e);
    const rb = ribbons.find((r) => r.isPointInFill(pt));
    if (rb) {
      const c = backChain(rb);
      setState(c.hot, c.bands, false);
      showTip(e, rb.dataset.tip);
      return;
    }
    const band = bandAt(pt);
    setState(new Set(), new Set(band ? [band.id] : []), true);
    if (band) showTip(e, `${band.title}\n${pct(band.count)}`);
    else hideTip();
  });
  svg.addEventListener("pointerleave", () => {
    setState(new Set(), new Set(), false);
    hideTip();
  });

  const outer = el("div");
  outer.appendChild(wrap);
  if (spec.footnote) outer.appendChild(el("div",
    `font-size:.7rem;color:${palette.soft};margin-top:.35rem;line-height:1.5;`,
    esc(spec.footnote)));
  return frame(outer, { caption, provenance });
}

/* ------------------------------------------------------------ glDumbbell */
// Horizontal dumbbells: one row per label, a muted grey dot at the starting value
// joined by a thin arrow to an accent dot at the final value, with an optional
// intermediate stage dot between. The gap IS the correction; the delta in
// percentage points is printed beside the final end. rows may carry a `note`
// (small grey annotation right of the starting dot, e.g. "160 empty").
//   rows: [{label, from, mid?, to, note?}]
//   spec: xLabel, fromLabel/midLabel/toLabel (tooltip + legend wording),
//         accent / midTone (tones or hex), fmt, sort ("desc" by `from`,
//         default) — plus caption/provenance/width/height.
export function glDumbbell({ Plot }, rows, spec = {}) {
  const { caption, provenance, width = 396, xLabel = "",
    fromLabel = "reported", toLabel = "corrected", midLabel = "intermediate",
    fmt = (v) => `${v.toFixed(1)}%` } = spec;
  const accent = palette[spec.accent] ?? spec.accent ?? palette.bad;
  const midTone = palette[spec.midTone] ?? spec.midTone ?? palette.warn;
  const data = (spec.sort === false ? rows
    : rows.slice().sort((a, b) => b.from - a.from));
  const height = spec.height ?? data.length * 44 + 78;
  const delta = (d) => {
    const v = d.to - d.from;
    return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)} pp`;
  };
  const mids = data.filter((d) => d.mid != null);
  const node = Plot.plot({
    width, height, marginLeft: spec.marginLeft ?? 144, marginBottom: 42, marginRight: 70,
    style: { ...plotStyle("12px"), color: "#000" },
    x: centredAxis(xLabel, 36, { grid: true, zero: true,
      ...(spec.xDomain ? { domain: spec.xDomain } : {}),
      ...(spec.xTicks ? { ticks: spec.xTicks } : {}) }),
    y: { label: null, domain: data.map((d) => d.label) },
    marks: [
      Plot.arrow(data, { x1: "from", x2: "to", y1: "label", y2: "label",
        stroke: "#777672", strokeWidth: 1, headLength: 5,
        insetStart: 3.5, insetEnd: 3.5 }),
      Plot.dot(data, { x: "from", y: "label", fill: "#777672", r: 3.5 }),
      Plot.dot(mids, { x: "mid", y: "label", fill: midTone, r: 3.5 }),
      Plot.dot(data, { x: "to", y: "label", fill: accent, r: 3.5 }),
      Plot.text(data, { x: (d) => Math.max(d.from, d.to), y: "label",
        text: delta, dx: 12, textAnchor: "start", fill: "#000",
        fontWeight: 600 }),
      ...(spec.tip === false ? [] : [
        Plot.tip(data, Plot.pointerY({ y: "label", x: "to", maxRadius: 40,
          title: (d) => `${d.label}\n${fromLabel}: ${fmt(d.from)}` +
            (d.mid != null ? `\n${midLabel}: ${fmt(d.mid)}` : "") +
            `\n${toLabel}: ${fmt(d.to)} (${delta(d)})` }))]),
    ],
  });
  const wrap = el("div");
  wrap.appendChild(node);
  return frame(wrap, { caption, provenance });
}
