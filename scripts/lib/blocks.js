/*
 * blocks.js — muuntaa JSON-lohkot designin HTML:ksi.
 * Lohkotyypit: h, h3, p, list, table, callout, metrics, kv, chart, legend, raw.
 */
const charts = require("./charts");

function esc(v) {
  return String(v == null ? "" : v)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Kevyt inline-markdown: **lihavointi**, *kursiivi*. Muu teksti escapetaan.
function inline(v) {
  let s = esc(v);
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  return s;
}

function listHtml(b) {
  const tag = b.ordered ? "ol" : "ul";
  const items = b.items || [];
  // splitItems (oletus true): jokainen kohta omana listanaan, jotta sivutus voi
  // katkaista kohtien välistä. Numerointi säilyy start-attribuutilla.
  const split = b.splitItems !== false;
  if (split) {
    return items
      .map((it, i) => {
        const startAttr = b.ordered ? ` start="${(b.start || 1) + i}"` : "";
        return `<${tag} class="note-list flow-list"${startAttr}><li>${inline(it)}</li></${tag}>`;
      })
      .join("\n");
  }
  return `<${tag} class="note-list flow-list">${items.map((it) => `<li>${inline(it)}</li>`).join("")}</${tag}>`;
}

function tableHtml(b) {
  const headers = b.headers || [];
  const rows = b.rows || [];
  const cls = "score content-table" + (b.class ? " " + b.class : "");
  const thead = headers.length
    ? `<thead><tr>${headers.map((h) => `<th>${inline(h)}</th>`).join("")}</tr></thead>`
    : "";
  const tbody = `<tbody>${rows
    .map((r) => {
      const cells = Array.isArray(r) ? r : r.cells;
      const rowCls = !Array.isArray(r) && r.class ? ` class="${r.class}"` : "";
      return `<tr${rowCls}>${cells.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`;
    })
    .join("")}</tbody>`;
  let out = `<table class="${cls}">\n    ${thead}\n    ${tbody}\n  </table>`;
  if (b.note) out += `\n<p class="intro-note">${inline(b.note)}</p>`;
  return out;
}

function calloutHtml(b) {
  const variant = b.variant || "neutral";
  let inner = `<div class="co-t"><span class="co-badge"></span>${inline(b.title || "")}</div>`;
  if (b.items) {
    const tag = b.ordered ? "ol" : "ul";
    inner += `<${tag}>${b.items.map((it) => `<li>${inline(it)}</li>`).join("")}</${tag}>`;
  }
  (b.paragraphs || (b.text ? [b.text] : [])).forEach((p) => {
    inner += `<p>${inline(p)}</p>`;
  });
  return `<div class="callout ${variant}">${inner}</div>`;
}

function metricsHtml(b) {
  const cols = b.columns || b.cards.length;
  const cards = b.cards
    .map((c) => {
      const style = c.valueSize ? ` style="font-size:${c.valueSize};"` : "";
      const unit = c.unit ? `<span class="u">${esc(c.unit)}</span>` : "";
      return `<div class="mcard${c.accent ? " accent" : ""}"><div class="mval"${style}>${inline(c.value)}${unit}</div><div class="mlabel">${inline(c.label)}</div></div>`;
    })
    .join("");
  const extra = b.style ? " " + b.style : "";
  return `<div class="mgrid" style="grid-template-columns:repeat(${cols},1fr);${extra}">${cards}</div>`;
}

function kvHtml(b) {
  const rows = b.rows
    .map((r) => {
      const k = r.bold ? `<strong>${inline(r.k)}</strong>` : inline(r.k);
      const v = r.bold ? `<strong>${inline(r.v)}</strong>` : inline(r.v);
      return `<div class="kv"><span class="k">${k}</span><span class="v">${v}</span></div>`;
    })
    .join("");
  return `<div>${rows}</div>`;
}

function legendHtml(b) {
  const items = b.items
    .map((it) => {
      const icon =
        it.type === "line"
          ? `<i class="line" style="border-top-color:${it.color || "#2E4B3C"}"></i>`
          : `<i class="bar" style="background:${it.color || "#2E4B3C"}"></i>`;
      return `<span>${icon}${esc(it.label)}</span>`;
    })
    .join("");
  return `<div class="legend">${items}</div>`;
}

function chartHtml(b) {
  // Koko kuvaaja (otsikko + kuvaaja + selite + legenda) yhtenä lohkona, jotta
  // sivutus ei koskaan erota otsikkoa kuvaajasta.
  let out = `<div class="figure" style="break-inside:avoid;">`;
  if (b.title) out += `<h4 class="blk" style="margin-top:0;">${inline(b.title)}</h4>`;
  out += `<div class="chart-host">${charts.render(b)}</div>`;
  if (b.caption) out += `<div class="fig-cap">${inline(b.caption)}</div>`;
  if (b.legend) out += legendHtml({ items: b.legend });
  out += `</div>`;
  return out;
}

function block(b) {
  switch (b.type) {
    case "h": return `<h4 class="blk">${inline(b.text)}</h4>`;
    case "h3": return `<h3 class="blk">${inline(b.text)}</h3>`;
    case "p": return `<p${b.class ? ` class="${b.class}"` : ""}>${inline(b.text)}</p>`;
    case "list": return listHtml(b);
    case "table": return tableHtml(b);
    case "callout": return calloutHtml(b);
    case "metrics": return metricsHtml(b);
    case "kv": return kvHtml(b);
    case "chart": return chartHtml(b);
    case "legend": return legendHtml(b);
    case "raw": return b.html || "";
    default: throw new Error("Tuntematon lohkotyyppi: " + b.type);
  }
}

function blocks(arr) {
  return (arr || []).map(block).join("\n");
}

module.exports = { block, blocks, inline, esc };
