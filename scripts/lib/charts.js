/*
 * charts.js — puhtaat SVG-kuvaajat (ei kirjastoja, ei JS-ajoa selaimessa).
 * SVG skaalautuu .chart-host-elementin leveyteen viewBoxin kautta ja tulostuu
 * terävänä PDF:ään. Värit designin paletista.
 */
const C = {
  green: "#2E4B3C",
  lime: "#A6CE39",
  limeDeep: "#8FB525",
  red: "#C0504D",
  gray: "#6B7280",
  line: "#E1E4DE",
  lineStrong: "#CBD0C9",
  greenSoft: "#E7EDE8",
  ink: "#1A1D1A",
};
const HEAD = "Archivo, system-ui, sans-serif";
const SANS = "'Source Sans 3', system-ui, sans-serif";

// Suomalainen lukumuoto (pilkku desimaalierottimena), vain akselin merkintöihin.
function fmt(n) {
  if (n == null || isNaN(n)) return "";
  const r = Math.round(n * 10) / 10;
  return String(r).replace(".", ",");
}
function niceMax(v) {
  if (v <= 0) return 1;
  const mag = Math.pow(10, Math.floor(Math.log10(v)));
  const f = v / mag;
  const nf = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
  return nf * mag;
}
// "Nice" porras: pyöristää akselin välin tasaisiin lukuihin (1/2/2,5/5/10 × 10ⁿ).
function niceStep(range, ticks) {
  const raw = (range || 1) / Math.max(1, ticks);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const f = raw / mag;
  const nf = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
  return nf * mag;
}
/* Akselin asteikko jossa lo, hi ja jokainen merkki on tasaluku.
   Sisältää aina 0-perustason. Palauttaa { lo, hi, step, count }. */
function niceScale(dataMin, dataMax, targetTicks = 4) {
  let lo = Math.min(0, dataMin);
  let hi = Math.max(0, dataMax);
  if (lo === hi) hi = lo + 1;
  const step = niceStep(hi - lo, targetTicks);
  lo = Math.floor(lo / step) * step;
  hi = Math.ceil(hi / step) * step;
  const count = Math.max(1, Math.round((hi - lo) / step));
  return { lo, hi, step, count };
}

function svgWrap(vbW, vbH, inner) {
  return `<svg viewBox="0 0 ${vbW} ${vbH}" width="100%" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg" font-family="${SANS}">${inner}</svg>`;
}

/* Pysty(ryhmä)pylväät: { labels:[], series:[{name,values,color?}], unit? } */
function bars(cfg) {
  const W = 600, H = 250, pad = { t: 16, r: 14, b: 34, l: 40 };
  const labels = cfg.labels || [];
  const series = cfg.series || [];
  const all = series.flatMap((s) => s.values).filter((v) => v != null);
  const sc = niceScale(Math.min(0, ...all), Math.max(0, ...all));
  const lo = sc.lo, max = sc.hi;
  const plotW = W - pad.l - pad.r, plotH = H - pad.t - pad.b;
  const y = (v) => pad.t + plotH * (1 - (v - lo) / (max - lo));
  const groupW = plotW / labels.length;
  const palette = [C.green, C.lime, C.gray];
  let g = "";
  // gridlines + y-labels
  const ticks = sc.count;
  for (let i = 0; i <= ticks; i++) {
    const val = lo + ((max - lo) * i) / ticks;
    const yy = y(val);
    g += `<line x1="${pad.l}" y1="${yy}" x2="${W - pad.r}" y2="${yy}" stroke="${C.line}" stroke-width="1"/>`;
    g += `<text x="${pad.l - 6}" y="${yy + 3}" text-anchor="end" font-size="9" fill="${C.gray}">${fmt(val)}</text>`;
  }
  const bw = (groupW * 0.62) / series.length;
  labels.forEach((lab, i) => {
    const gx = pad.l + groupW * i + groupW * 0.19;
    series.forEach((s, si) => {
      const v = s.values[i];
      if (v == null) return;
      const yy = y(v), y0 = y(0);
      const top = Math.min(yy, y0), hh = Math.abs(yy - y0);
      g += `<rect x="${gx + bw * si}" y="${top}" width="${bw * 0.86}" height="${Math.max(hh, 0.5)}" fill="${s.color || palette[si % palette.length]}"/>`;
    });
    g += `<text x="${pad.l + groupW * i + groupW / 2}" y="${H - pad.b + 16}" text-anchor="middle" font-size="9.5" fill="${C.gray}">${lab}</text>`;
  });
  g += `<line x1="${pad.l}" y1="${y(0)}" x2="${W - pad.r}" y2="${y(0)}" stroke="${C.lineStrong}" stroke-width="1.2"/>`;
  return svgWrap(W, H, g);
}

/* Viiva: { labels:[], series:[{name,values,color?}] } */
function line(cfg) {
  const W = 600, H = 250, pad = { t: 16, r: 14, b: 34, l: 40 };
  const labels = cfg.labels || [];
  const series = cfg.series || [];
  const all = series.flatMap((s) => s.values).filter((v) => v != null);
  const sc = niceScale(Math.min(0, ...all), Math.max(0, ...all));
  const lo = sc.lo, max = sc.hi;
  const plotW = W - pad.l - pad.r, plotH = H - pad.t - pad.b;
  const x = (i) => pad.l + (plotW * i) / Math.max(1, labels.length - 1);
  const y = (v) => pad.t + plotH * (1 - (v - lo) / (max - lo));
  const palette = [C.green, C.lime, C.red];
  let g = "";
  const ticks = sc.count;
  for (let i = 0; i <= ticks; i++) {
    const val = lo + ((max - lo) * i) / ticks;
    const yy = y(val);
    g += `<line x1="${pad.l}" y1="${yy}" x2="${W - pad.r}" y2="${yy}" stroke="${C.line}" stroke-width="1"/>`;
    g += `<text x="${pad.l - 6}" y="${yy + 3}" text-anchor="end" font-size="9" fill="${C.gray}">${fmt(val)}</text>`;
  }
  series.forEach((s, si) => {
    const col = s.color || palette[si % palette.length];
    const pts = s.values.map((v, i) => (v == null ? null : `${x(i)},${y(v)}`)).filter(Boolean);
    g += `<polyline points="${pts.join(" ")}" fill="none" stroke="${col}" stroke-width="2.4"/>`;
    s.values.forEach((v, i) => { if (v != null) g += `<circle cx="${x(i)}" cy="${y(v)}" r="3" fill="${col}"/>`; });
  });
  labels.forEach((lab, i) => {
    g += `<text x="${x(i)}" y="${H - pad.b + 16}" text-anchor="middle" font-size="9.5" fill="${C.gray}">${lab}</text>`;
  });
  return svgWrap(W, H, g);
}

/* Yhdistelmä: pylväät (vasen akseli) + viiva (oikea akseli, esim. EBIT-%).
   { labels:[], bars:{name,values}, line:{name,values,percent?} } */
function combo(cfg) {
  const W = 600, H = 260, pad = { t: 16, r: 44, b: 34, l: 42 };
  const labels = cfg.labels || [];
  const b = cfg.bars, ln = cfg.line;
  const plotW = W - pad.l - pad.r, plotH = H - pad.t - pad.b;
  const bVals = b.values.filter((v) => v != null);
  const lVals = ln.values.filter((v) => v != null);
  const bs = niceScale(Math.min(0, ...bVals), Math.max(0, ...bVals));
  const ls = niceScale(Math.min(0, ...lVals), Math.max(0, ...lVals));
  // Sama merkkimäärä molemmilla akseleilla → ruudukko kohdistuu. Laajennetaan
  // pienempi asteikko tasaluvuilla (lo tai hi askelin) yhtä moneen väliin.
  const ticks = Math.max(bs.count, ls.count);
  const grow = (s) => {
    while (s.count < ticks) {
      if (s.lo < 0 && (s.hi <= 0 || -s.lo <= s.hi)) s.lo -= s.step;
      else s.hi += s.step;
      s.count++;
    }
  };
  grow(bs); grow(ls);
  const bLo = bs.lo, bMax = bs.hi, lLo = ls.lo, lMax = ls.hi;
  const yb = (v) => pad.t + plotH * (1 - (v - bLo) / (bMax - bLo));
  const yl = (v) => pad.t + plotH * (1 - (v - lLo) / (lMax - lLo));
  const groupW = plotW / labels.length;
  const xMid = (i) => pad.l + groupW * i + groupW / 2;
  let g = "";
  for (let i = 0; i <= ticks; i++) {
    const val = bLo + ((bMax - bLo) * i) / ticks;
    const yy = yb(val);
    g += `<line x1="${pad.l}" y1="${yy}" x2="${W - pad.r}" y2="${yy}" stroke="${C.line}" stroke-width="1"/>`;
    g += `<text x="${pad.l - 6}" y="${yy + 3}" text-anchor="end" font-size="9" fill="${C.gray}">${fmt(val)}</text>`;
    const rval = lLo + ((lMax - lLo) * i) / ticks;
    g += `<text x="${W - pad.r + 6}" y="${yy + 3}" text-anchor="start" font-size="9" fill="${C.limeDeep}">${fmt(rval)}${ln.percent ? " %" : ""}</text>`;
  }
  const bw = groupW * 0.5;
  labels.forEach((lab, i) => {
    const v = b.values[i];
    if (v != null) {
      const yy = yb(v), y0 = yb(0);
      g += `<rect x="${xMid(i) - bw / 2}" y="${Math.min(yy, y0)}" width="${bw}" height="${Math.max(Math.abs(yy - y0), 0.5)}" fill="${C.green}"/>`;
    }
    g += `<text x="${xMid(i)}" y="${H - pad.b + 16}" text-anchor="middle" font-size="9.5" fill="${C.gray}">${lab}</text>`;
  });
  g += `<line x1="${pad.l}" y1="${yb(0)}" x2="${W - pad.r}" y2="${yb(0)}" stroke="${C.lineStrong}" stroke-width="1.2"/>`;
  const pts = ln.values.map((v, i) => (v == null ? null : `${xMid(i)},${yl(v)}`)).filter(Boolean);
  g += `<polyline points="${pts.join(" ")}" fill="none" stroke="${C.limeDeep}" stroke-width="2.6"/>`;
  ln.values.forEach((v, i) => { if (v != null) g += `<circle cx="${xMid(i)}" cy="${yl(v)}" r="3.2" fill="${C.limeDeep}"/>`; });
  return svgWrap(W, H, g);
}

/* Vaakapylväät: { items:[{label, value, status?, muted?}], unit? } */
function hbars(cfg) {
  const items = cfg.items || [];
  const rowH = 30, padL = 150, padR = 60, padT = 8, padB = 8;
  const W = 600, H = padT + padB + rowH * items.length;
  const vals = items.map((it) => (typeof it.value === "number" ? it.value : 0));
  const max = niceMax(Math.max(1, ...vals));
  const plotW = W - padL - padR;
  let g = "";
  items.forEach((it, i) => {
    const cy = padT + rowH * i + rowH / 2;
    const hasVal = typeof it.value === "number";
    const bw = hasVal ? (plotW * it.value) / max : 0;
    const muted = it.muted || !hasVal;
    g += `<text x="${padL - 8}" y="${cy + 3}" text-anchor="end" font-size="9.5" fill="${muted ? C.gray : C.ink}" font-weight="600">${it.label}</text>`;
    if (hasVal) {
      g += `<rect x="${padL}" y="${cy - 8}" width="${Math.max(bw, 1)}" height="16" fill="${muted ? C.lineStrong : C.green}"/>`;
      g += `<text x="${padL + bw + 6}" y="${cy + 3}" font-size="9.5" fill="${C.gray}" font-family="${HEAD}" font-weight="700">${fmt(it.value)}</text>`;
    } else {
      g += `<text x="${padL + 4}" y="${cy + 3}" font-size="8.5" fill="${C.gray}" font-style="italic">${it.status || "ei arvoa"}</text>`;
    }
  });
  return svgWrap(W, H, g);
}

/* Donitsi (rengaskaavio): { segments:[{value, color, textColor?}] }.
   Prosenttiluku piirretään kunkin sektorin keskelle. */
function donut(cfg) {
  const segs = (cfg.segments || []).filter((s) => (s.value || 0) > 0);
  const total = segs.reduce((s, x) => s + (x.value || 0), 0) || 1;
  const cx = 100, cy = 100, rO = 94, rI = 54;
  const pol = (r, a) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  let a0 = -Math.PI / 2, g = "";
  segs.forEach((seg) => {
    const frac = (seg.value || 0) / total;
    const a1 = a0 + frac * 2 * Math.PI;
    const large = frac > 0.5 ? 1 : 0;
    const [xo0, yo0] = pol(rO, a0), [xo1, yo1] = pol(rO, a1);
    const [xi0, yi0] = pol(rI, a0), [xi1, yi1] = pol(rI, a1);
    g += `<path d="M${xo0.toFixed(2)} ${yo0.toFixed(2)} A${rO} ${rO} 0 ${large} 1 ${xo1.toFixed(2)} ${yo1.toFixed(2)} L${xi1.toFixed(2)} ${yi1.toFixed(2)} A${rI} ${rI} 0 ${large} 0 ${xi0.toFixed(2)} ${yi0.toFixed(2)} Z" fill="${seg.color}"/>`;
    if (frac > 0.06) {
      const am = (a0 + a1) / 2, [lx, ly] = pol((rO + rI) / 2, am);
      g += `<text x="${lx.toFixed(2)}" y="${(ly + 3.6).toFixed(2)}" fill="${seg.textColor || "#fff"}" font-size="11" text-anchor="middle" font-weight="700" font-family="${SANS}" style="font-variant-numeric:tabular-nums lining-nums;">${Math.round(frac * 100)} %</text>`;
    }
    a0 = a1;
  });
  return svgWrap(200, 200, g);
}

function render(cfg) {
  switch (cfg.kind) {
    case "bars": return bars(cfg);
    case "line": return line(cfg);
    case "combo": return combo(cfg);
    case "hbars": return hbars(cfg);
    case "donut": return donut(cfg);
    default: throw new Error("Tuntematon kuvaajatyyppi: " + cfg.kind);
  }
}

module.exports = { render, C };
