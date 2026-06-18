/*
 * generate.js — RAPORTIN GENEROINTI.
 *
 *   node scripts/generate.js [data/report.json] [outputs/raportti.html]
 *
 * Lukee JSON-sisällön + designtemplaten ja kirjoittaa valmiin, omavaraisen
 * HTML-raportin. Ei tekoälyä, ei verkkoa — sama JSON tuottaa aina saman taiton.
 */
const fs = require("fs");
const path = require("path");
const { blocks, inline, esc } = require("./lib/blocks");
const { adapt, isPromptSchema } = require("./lib/adapt");
const charts = require("./lib/charts");

const ROOT = path.resolve(__dirname, "..");
const dataPath = process.argv[2] || path.join(ROOT, "data", "report.json");
const outPath = process.argv[3] || path.join(ROOT, "outputs", "raportti.html");
const templatePath = path.join(ROOT, "template", "report.template.html");

// Lue JSON. Siedä mallin mahdollisesti lisäämät ```json-aidat ja ympäröivän tekstin.
function loadJson(p) {
  let raw = fs.readFileSync(p, "utf8").trim();
  const fence = raw.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fence) raw = fence[1].trim();
  else if (raw[0] !== "{") {
    const i = raw.indexOf("{"), j = raw.lastIndexOf("}");
    if (i >= 0 && j > i) raw = raw.slice(i, j + 1);
  }
  try {
    return JSON.parse(raw);
  } catch (e) {
    throw new Error(`Virheellinen JSON tiedostossa ${p}: ${e.message}`);
  }
}

let data = loadJson(dataPath);
// Tuotantopromptin (v3) skeema → sisäinen lohkomalli. Sisäistä skeemaa
// käytetään sellaisenaan (esim. data/report.json).
if (isPromptSchema(data)) data = adapt(data);
if (!Array.isArray(data.sections) || !data.sections.length) {
  throw new Error("JSONista puuttuu 'sections'-lista (tai se on tyhjä).");
}
let template = fs.readFileSync(templatePath, "utf8");

const meta = data.meta || {};
const brandName = meta.brandName || meta.company || "Valuatum";
const footerLeft = meta.footerLeft || `${brandName} · ${meta.reportTitle || "AI-Arvonmääritysraportti"}`;
const headLine = meta.headerRight || [meta.company, meta.businessId, meta.date].filter(Boolean).join(" · ");

/* ---------- otsikkopalkki (sivun ylä/alaosa) ---------- */
const headerHtml = `<div class="phead"><span class="brandmark"><i></i>${esc(brandName)}</span><span>${esc(headLine)}</span></div>`;
const footerHtml = `<div class="pfoot"><span>${esc(footerLeft)}</span><span class="pf-r" data-pf=""></span></div>`;

/* ---------- kiinteät sivut: kansi + snapshot + sisällys ---------- */
function secHead(number, title, subtitle, neutralNum) {
  const numStyle = neutralNum ? ' style="background:var(--green); color:#fff;"' : "";
  return (
    `<div class="sec-head"><span class="sec-num"${numStyle}>${number}</span>` +
    `<div class="sh-t"><h2>${inline(title)}</h2>` +
    (subtitle ? `<div class="sh-sub">${inline(subtitle)}</div>` : "") +
    `</div></div>\n    <div class="sec-rule"></div>`
  );
}

/* Arvostusvälin jana (low ——|mid—— high). r = { caption, captionRight,
   lowLabel, highLabel, midLabel, midUnit, midPct } — kaikki esimuotoiltuja. */
function rangeBarHtml(r, extraStyle) {
  if (!r) return "";
  const mid = Math.max(0, Math.min(100, r.midPct == null ? 50 : r.midPct));
  return `<div class="rangebar"${extraStyle ? ` style="${extraStyle}"` : ""}>
      <div class="rb-caption"><span>${esc(r.caption || "Arvostusväli")}</span><span>${esc(r.captionRight || "")}</span></div>
      <div class="rb-track">
        <div class="rb-line"></div>
        <div class="rb-band" style="left:0%; right:0%;"></div>
        <div class="rb-tick end" style="left:0%;"></div>
        <div class="rb-lab" style="left:0%;">${esc(r.lowLabel || "")}</div>
        <div class="rb-tick mid" style="left:${mid}%;"></div>
        <div class="rb-lab mid" style="left:${mid}%;">${esc(r.midLabel || "")}${r.midUnit ? ` <span class="lu">${esc(r.midUnit)}</span>` : ""}</div>
        <div class="rb-tick end" style="left:100%;"></div>
        <div class="rb-lab" style="left:100%;">${esc(r.highLabel || "")}</div>
      </div>
    </div>`;
}

/* Luottamustaso-pillit. cf = { caption, note, levels:[{label,on}] } */
function confHtml(cf) {
  if (!cf || !cf.levels) return "";
  const pills = cf.levels.map((l) => `<span${l.on ? ` class="on"${l.color ? ` style="background:${l.color};border-color:${l.color}"` : ""}` : ""}>${esc(l.label)}</span>`).join("");
  return `<div class="cv-conf">
    <h4 class="blk">${esc(cf.caption || "Arvion luottamustaso")}</h4>
    <div class="conf">${pills}</div>
    ${cf.note ? `<div class="conf-note">${inline(cf.note)}</div>` : ""}
  </div>`;
}

function coverPage() {
  const c = data.cover || {};
  const metaLines = (c.metaLines || []).map(esc).join("<br>");
  const hl = c.headline || {};
  // Caption ENSIN (CSS .cap on display:block ja asettuu numeron yläpuolelle).
  const big =
    `<div class="cv-big" style="font-size:${hl.size || "30pt"};">` +
    (hl.caption ? `<span class="cap">${esc(hl.caption)}</span>` : "") +
    `${inline(hl.big || "")}` +
    (hl.unit ? `<span class="u">${esc(hl.unit)}</span>` : "") +
    `</div>`;
  // Rikas kansi (rangebar oikealla) vain jos arvostusväli on saatavilla.
  const headline = c.range
    ? `<div class="cv-headline">${big}${rangeBarHtml(c.range)}</div>${confHtml(c.confidence)}`
    : `<div style="margin-top:${c.headlineGap || "32mm"};">${big}</div>`;
  return `<section class="page cover" data-screen-label="01 Kansi">
  <div class="pbody">
    <div class="cv-brand"><span></span>${esc(brandName)}</div>
    <div class="cv-tag">${esc(c.tagline || meta.reportTitle || "AI-Arvonmääritysraportti")}</div>
    <h1><strong>${inline(c.title || meta.reportTitle || "AI-Arvonmääritysraportti")}</strong></h1>
    <div class="cv-co">${esc(c.company || meta.company || "")}</div>
    <div class="cv-meta">${metaLines}</div>
    ${headline}
  </div>
  ${footerHtml}
</section>`;
}

function fixedPage(label, number, title, subtitle, bodyHtml, neutralNum) {
  return `<section class="page" data-screen-label="${esc(label)}">
  ${headerHtml}
  <div class="pbody">
    ${secHead(number, title, subtitle, neutralNum !== false)}
    ${bodyHtml}
  </div>
  ${footerHtml}
</section>`;
}

function cardHtml(c) {
  const style = c.valueSize || c.color
    ? ` style="${c.valueSize ? `font-size:${c.valueSize};` : ""}${c.color ? `color:${c.color};` : ""}"`
    : "";
  const unit = c.unit ? `<span class="u">${esc(c.unit)}</span>` : "";
  return `<div class="mcard${c.accent ? " accent" : ""}"><div class="mval"${style}>${inline(c.value)}${unit}</div><div class="mlabel">${inline(c.label)}</div></div>`;
}

function snapshotPage() {
  const s = data.snapshot;
  if (!s) return "";
  // Vanha (geneerinen) snapshot: lohkolista.
  if (s.blocks && !s.cards) {
    return fixedPage(s.label || "02 Snapshot", s.number || "·", s.title || "Snapshot", s.subtitle || "", blocks(s.blocks));
  }
  let body = "";
  if (s.cards && s.cards.length) {
    body += `<div class="mgrid" style="grid-template-columns:repeat(${s.cards.length},1fr);">${s.cards.map(cardHtml).join("")}</div>`;
  }
  if (s.range) body += rangeBarHtml(s.range, "margin-top:20px;");
  if (s.weights || s.methods) {
    const w = s.weights || {};
    const legend = (w.legend || [])
      .map((it) => `<div style="display:flex; align-items:center; gap:6px; margin-bottom:6px;"><i style="width:11px;height:11px;background:${it.color};display:inline-block;"></i><span><strong>${inline(it.label)}</strong><br><span class="muted">${esc(it.sub || "")}</span></span></div>`)
      .join("");
    const left = w.donut
      ? `<div><h4 class="blk">${esc(w.title || "Skenaariopainot")}</h4>
          <div style="display:flex; align-items:center; gap:10px;">
            <div style="width:120px;"><div class="chart-host">${charts.render({ kind: "donut", segments: w.donut })}</div></div>
            <div style="font-size:7.8pt; line-height:1.4;">${legend}</div>
          </div></div>`
      : "<div></div>";
    const right = s.methods
      ? `<div><h4 class="blk">${esc(s.methods.title || "Skenaarioiden arvot")}</h4><div class="chart-host">${charts.render({ kind: "hbars", items: s.methods.items, unit: s.methods.unit })}</div></div>`
      : "<div></div>";
    body += `<div class="two-col" style="margin-top:14px; grid-template-columns:0.82fr 1.18fr;">${left}${right}</div>`;
  }
  if (s.kill) {
    body += `<div class="callout kill" style="margin-top:14px;"><div class="co-t"><span class="co-badge"></span>${inline(s.kill.title || "Mikä kaataisi tämän arvion")}</div><p>${inline(s.kill.text || "")}</p></div>`;
  }
  return fixedPage(s.label || "02 Snapshot", s.number || "·", s.title || "Snapshot", s.subtitle || "Arvion tiivistetyt avainluvut", body);
}

function tocPage() {
  const t = data.toc || {};
  const sections = data.sections || [];
  const rows = sections
    .map(
      (sec) =>
        `<div class="toc-row" data-toc-for="${esc(sec.id)}"><span class="tn">${esc(sec.number)}</span><span class="tt">${inline(sec.title)}</span><span class="td"></span><span class="tp"></span></div>`
    )
    .join("");
  let body = `<div class="toc">${rows}</div>`;
  if (t.note) {
    body += `\n    <div class="callout ${t.noteVariant || "neutral"}" style="margin-top:22px;">
      <div class="co-t"><span class="co-badge"></span>${inline(t.noteTitle || "Näin raporttia luetaan")}</div>
      <p>${inline(t.note)}</p>
    </div>`;
  }
  return fixedPage(
    t.label || "03 Sisällysluettelo",
    t.number || "·",
    t.title || "Sisällys",
    t.subtitle || `${meta.reportTitle || "AI-Arvonmääritysraportti"} · ${meta.company || ""}`,
    body
  );
}

const fixedPages = [coverPage(), snapshotPage(), tocPage()].filter(Boolean).join("\n\n");

/* ---------- virtaavat osiot ---------- */
function sectionHtml(sec) {
  const start = `<div class="section-start" data-section-id="${esc(sec.id)}" data-section-number="${esc(sec.number)}" data-section-title="${esc(sec.title)}">
      ${secHead(sec.number, sec.title, sec.subtitle || `${meta.company || ""} · ${meta.reportTitle || "AI-Arvonmääritysraportti"}`)}
    </div>`;
  return start + "\n" + blocks(sec.blocks);
}
const flowSections = (data.sections || []).map(sectionHtml).join("\n");

/* ---------- sijoita templateen ---------- */
const replacements = {
  "{{REPORT_TITLE}}": esc(`${meta.company || brandName} — ${meta.reportTitle || "AI-Arvonmääritysraportti"}`),
  "{{FIXED_PAGES}}": fixedPages,
  "{{FLOW_SECTIONS}}": flowSections,
  "{{HEADER_JS}}": JSON.stringify(headerHtml),
  "{{FOOTER_JS}}": JSON.stringify(footerHtml),
  "{{FIRST_PAGE_LABEL_JS}}": JSON.stringify(meta.firstFlowLabel || "04 Raportti"),
};
for (const [k, v] of Object.entries(replacements)) {
  template = template.split(k).join(v);
}

fs.mkdirSync(path.dirname(outPath), { recursive: true });
fs.writeFileSync(outPath, template, "utf8");
console.log(`OK — raportti kirjoitettu: ${path.relative(ROOT, outPath)} (${(template.length / 1024).toFixed(0)} kB, ${data.sections ? data.sections.length : 0} osiota)`);
