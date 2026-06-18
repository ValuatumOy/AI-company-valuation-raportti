/*
 * adapt.js — muuntaa tuotantopromptin (v3) JSON-outputin sisäiseen
 * lohkomalliin, jonka blocks.js/charts.js renderöi designiin.
 *
 * Promptin skeema on sopimus; tämä adapteri sopeutuu siihen. Jos prompti
 * muuttuu, päivitä mappaukset tässä — ei templatessa eikä blocks.js:ssä.
 */

// Suomalainen lukumuoto: välilyönti tuhaterottimena, pilkku desimaalierottimena,
// − (U+2212) miinusmerkkinä. Vuosiluvut (1900–2100) jätetään muotoilematta.
function fmtNum(v) {
  if (typeof v !== "number" || !isFinite(v)) return v;
  if (Number.isInteger(v) && v >= 1900 && v <= 2100) return String(v);
  const neg = v < 0;
  const a = Math.abs(v);
  const s = Number.isInteger(a)
    ? a.toLocaleString("fi-FI")
    : a.toLocaleString("fi-FI", { minimumFractionDigits: 1, maximumFractionDigits: 2 });
  return (neg ? "−" : "") + s;
}

const CALLOUT_VARIANT = { info: "neutral", warning: "kill", key: "reality" };

function adaptTable(b) {
  if (b.status === "not_available") {
    return [{ type: "p", text: b.reason || "Taulukkoa ei voida muodostaa toimitetulla datalla." }];
  }
  return [{
    type: "table",
    headers: (b.columns || []).map((c) => (typeof c === "number" ? fmtNum(c) : c)),
    rows: (b.rows || []).map((r) => (Array.isArray(r) ? r : r.cells || []).map(fmtNum)),
    note: b.unit && !(b.columns || []).some((c) => String(c).includes(b.unit)) ? `Yksikkö: ${b.unit}` : undefined,
  }];
}

function adaptChart(b) {
  const title = b.title;
  if (b.status === "not_available") {
    const out = [];
    if (title) out.push({ type: "h", text: title });
    out.push({ type: "p", text: b.reason || "Kuvaajaa ei voida muodostaa toimitetulla datalla." });
    return out;
  }
  const labels = b.x_axis || b.labels || [];
  const series = b.series || [];
  const ct = b.chart_type || b.type;
  const isPct = (name) => /%/.test(name || "") || /%/.test(b.unit || "");

  if (ct === "bar_line") {
    const bar = series.find((s) => s.type === "bar") || series[0];
    const line = series.find((s) => s.type === "line") || series[1];
    if (!bar || !line) return adaptChart({ ...b, chart_type: "bar_grouped" });
    return [{
      type: "chart", kind: "combo", title, caption: b.caption, labels,
      bars: { name: bar.name, values: bar.values },
      line: { name: line.name, values: line.values, percent: isPct(line.name) },
      legend: [
        { type: "bar", label: bar.name, color: "#2E4B3C" },
        { type: "line", label: line.name, color: "#8FB525" },
      ],
    }];
  }

  if (ct === "heatmap_or_matrix") {
    // Pohja ei renderöi heatmapia → esitä matriisi taulukkona.
    const xs = b.x_axis || [];
    const ys = b.y_axis || [];
    const vals = b.values || [];
    const out = [];
    if (title) out.push({ type: "h", text: title });
    if (!vals.length) {
      out.push({ type: "p", text: b.reason || "Herkkyysmatriisia ei voida muodostaa toimitetulla datalla." });
      return out;
    }
    out.push({
      type: "table",
      headers: ["", ...xs.map(String)],
      rows: ys.map((y, i) => [String(y), ...(vals[i] || []).map(fmtNum)]),
    });
    return out;
  }

  // bar / bar_grouped → pystypylväät
  return [{
    type: "chart", kind: "bars", title, caption: b.caption, labels,
    series: series.map((s) => ({ name: s.name, values: s.values })),
  }];
}

function adaptBlock(b) {
  switch (b.type) {
    case "heading": return [{ type: "h", text: b.text }];
    case "paragraph": return [{ type: "p", text: b.text }];
    case "callout":
      return [{
        type: "callout",
        variant: CALLOUT_VARIANT[b.variant] || "neutral",
        title: b.title,
        paragraphs: b.text ? [b.text] : b.paragraphs || [],
      }];
    case "metric_cards":
      return [{
        type: "metrics",
        columns: Math.min(b.cards.length, 2) || 1,
        cards: b.cards.map((c) => ({
          value: typeof c.value === "number" ? fmtNum(c.value) : c.value,
          label: c.label,
          accent: !!c.emphasis,
          valueSize: c.value && String(c.value).length > 14 ? "11pt" : "16pt",
        })),
      }];
    case "key_value":
      return [
        ...(b.title ? [{ type: "h", text: b.title }] : []),
        {
          type: "kv",
          rows: b.items.map((it) => ({
            k: it.key,
            v: (typeof it.value === "number" ? fmtNum(it.value) : it.value) +
              (it.source ? ` — ${it.source}` : ""),
          })),
        },
      ];
    case "table": return adaptTable(b);
    case "chart": return adaptChart(b);
    default: return [{ type: "p", text: typeof b.text === "string" ? b.text : "" }];
  }
}

function adaptBlocks(arr) {
  return (arr || []).flatMap(adaptBlock);
}

// Tunnistaa, onko data tuotantopromptin skeemassa.
function isPromptSchema(data) {
  return !!(
    data.report_type ||
    data.machine_readable ||
    (data.meta && data.meta.company_name) ||
    (Array.isArray(data.sections) &&
      data.sections.some((s) => Array.isArray(s.blocks) &&
        s.blocks.some((b) => ["paragraph", "heading", "metric_cards", "key_value"].includes(b.type))))
  );
}

const SNAP_COLORS = ["#A6CE39", "#2E4B3C", "#6B7280"]; // lime, green, gray
const CONF_LEVELS = ["Matala", "Kohtalainen", "Korkea"];
const LEVEL_COLOR = { matala: "#C0504D", kohtalainen: "#8FB525", korkea: "#2E4B3C" };

// Arvostusväli + luottamustaso + snapshot v3-datasta. Palauttaa { range,
// confidence, snapshot } tai {} jos pohjadataa (skenaariot) ei ole.
function buildSnapshot(data, unit) {
  const mr = data.machine_readable || {};
  const ev = data.expected_value || {};
  const scen = Array.isArray(mr.scenarios) ? mr.scenarios : [];
  const mid = typeof ev.value === "number" ? ev.value : (typeof mr.expected_value_tEUR === "number" ? mr.expected_value_tEUR : null);
  if (!scen.length || mid == null) return {};

  // Kentän nimi vaihtelee mallin outputissa: owner_value_tEUR | owner_value | equity_value.
  const scenVal = (s) => {
    for (const k of ["owner_value_tEUR", "owner_value", "equity_value_tEUR", "equity_value", "value"]) {
      if (typeof s[k] === "number") return s[k];
    }
    return 0;
  };
  const vals = scen.map(scenVal);
  let low = Math.min(mid, ...vals), high = Math.max(mid, ...vals);
  if (high === low) high = low + 1;
  const midPct = Math.max(0, Math.min(100, ((mid - low) / (high - low)) * 100));
  const range = {
    caption: "Arvostusväli",
    captionRight: `Painotettu odotusarvo ${fmtNum(mid)} ${unit}`,
    lowLabel: fmtNum(low),
    highLabel: fmtNum(high),
    midLabel: fmtNum(mid),
    midUnit: unit,
    midPct,
  };

  const conf = data.confidence || {};
  const level = conf.level || "";
  const levelColor = LEVEL_COLOR[level.toLowerCase()] || "#C0504D";
  const confidence = {
    caption: "Arvion luottamustaso",
    note: conf.deciding_rule || "",
    levels: CONF_LEVELS.map((l) => ({ label: l, on: l.toLowerCase() === level.toLowerCase(), color: levelColor })),
  };
  // Jos taso ei ole vakiolistalla, näytä se sellaisenaan aktiivisena.
  if (level && !confidence.levels.some((l) => l.on)) {
    confidence.levels = [{ label: level, on: true, color: levelColor }, ...CONF_LEVELS.filter((l) => l.toLowerCase() !== level.toLowerCase()).map((l) => ({ label: l, on: false }))];
  }

  const dq = (data.data_quality || {}).class || "—";
  const cards = [
    { value: fmtNum(mid), unit, label: "Oman pääoman arvo (estimaatti)", accent: true },
    { value: `${fmtNum(low)}–${fmtNum(high)}`, unit, label: "Arvostusväli", valueSize: "15pt" },
    { value: level || "—", label: "Arvion luottamustaso", valueSize: "15pt", color: level ? levelColor : undefined },
    { value: dq, label: "Datan laatu (osio 2)", valueSize: "15pt" },
  ];

  const donut = scen.map((s, i) => ({ value: s.probability || 0, color: SNAP_COLORS[i % SNAP_COLORS.length], textColor: i % SNAP_COLORS.length === 0 ? "#2E4B3C" : "#fff" }));
  const legend = scen.map((s, i) => ({
    color: SNAP_COLORS[i % SNAP_COLORS.length],
    label: s.name || `Skenaario ${i + 1}`,
    sub: `${Math.round((s.probability || 0) * 100)} % · ${fmtNum(scenVal(s))} ${unit}`,
  }));
  const methods = {
    title: "Skenaarioiden arvot ja painotettu odotusarvo",
    unit,
    items: [
      ...scen.map((s) => ({ label: s.name || "Skenaario", value: scenVal(s), muted: true })),
      { label: "Painotettu odotusarvo", value: mid },
    ],
  };

  return {
    range,
    confidence,
    snapshot: {
      label: "02 Snapshot",
      title: "Snapshot",
      subtitle: "Arvion tiivistetyt avainluvut",
      cards,
      range,
      weights: { title: "Skenaariopainot", donut, legend },
      methods,
      kill: conf.deciding_rule ? { title: "Mikä kaataisi tämän arvion", text: conf.deciding_rule } : undefined,
    },
  };
}

function adapt(data) {
  const m = data.meta || {};
  const cov = data.cover || {};
  const reportTitle = "AI-Arvonmääritysraportti";
  const unit = m.unit || "tEUR";
  const snap = buildSnapshot(data, unit);

  const metaLines = [
    [m.y_tunnus ? `Y-tunnus ${m.y_tunnus}` : null, m.report_date].filter(Boolean).join(" · "),
    `Toimiala: ${m.industry || "ei ilmoitettu input-datassa"}`,
    ...(cov.secondary_lines || []),
  ].filter(Boolean);

  const headlineBig =
    cov.headline_value ||
    (data.expected_value && data.expected_value.value != null ? fmtNum(data.expected_value.value) : "") ||
    "Ei määritettävissä";

  const sections = (data.sections || [])
    .filter((s) => !/^kansi$/i.test(s.id || "") && !/^kansi$/i.test(s.title || ""))
    .map((s) => ({
      id: s.id,
      number: s.id,
      title: s.title,
      blocks: adaptBlocks(s.blocks),
    }));

  return {
    meta: {
      brandName: "Valuatum",
      company: m.company_name || "",
      businessId: m.y_tunnus || "",
      date: m.report_date || "",
      reportTitle,
      headerRight: [m.company_name, m.y_tunnus, m.report_date].filter(Boolean).join(" · "),
    },
    cover: {
      tagline: reportTitle,
      title: reportTitle,
      company: m.company_name || "",
      metaLines,
      headline: {
        big: headlineBig,
        unit: headlineBig && !String(headlineBig).includes(unit) ? unit : "",
        caption: cov.headline_label || "Oman pääoman arvo",
        size: String(headlineBig).length > 16 ? "22pt" : "30pt",
      },
      range: snap.range,
      confidence: snap.confidence,
    },
    snapshot: snap.snapshot,
    toc: {
      note:
        data.confidence && data.confidence.level
          ? `Arvion luottamustaso: ${data.confidence.level}. ${data.confidence.deciding_rule || ""}`.trim()
          : undefined,
    },
    sections,
  };
}

module.exports = { adapt, isPromptSchema, fmtNum };
