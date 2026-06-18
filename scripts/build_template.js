/*
 * build_template.js — AJETAAN VAIN KERRAN (tai kun designia muutetaan).
 *
 * Lukee alkuperäisen bundleroidun index.html:n, purkaa designin (CSS + fontit
 * + sivutuslogiikan), upottaa fontit data-URI:na (täysin offline / identtinen
 * design) ja korvaa kaiken dynaamisen sisällön {{PLACEHOLDER}}-merkeillä.
 *
 * Lopputulos: template/report.template.html — staattinen designrunko, johon
 * scripts/generate.js syöttää JSON-sisällön.
 *
 *   node scripts/build_template.js [lähde index.html] [kohde template.html]
 */
const fs = require("fs");
const path = require("path");
const zlib = require("zlib");

const ROOT = path.resolve(__dirname, "..");
const srcPath = process.argv[2] || path.join(ROOT, "index.html");
const outPath = process.argv[3] || path.join(ROOT, "template", "report.template.html");

const bundle = fs.readFileSync(srcPath, "utf8");

const manifest = JSON.parse(
  bundle.match(/<script type="__bundler\/manifest">\s*([\s\S]*?)\s*<\/script>/)[1]
);
let template = JSON.parse(
  bundle.match(/<script type="__bundler\/template">\s*([\s\S]*?)\s*<\/script>/)[1]
);

// 1) Upota fontit. @font-face viittaa assetteihin url("UUID"); korvataan ne
//    data:-URI:lla, jolloin HTML on täysin omavarainen eikä tarvitse bundleria.
let fontCount = 0;
for (const [uuid, entry] of Object.entries(manifest)) {
  if (!template.includes(uuid)) continue; // pudota käyttämätön asset (mm. dead JS)
  let buf = Buffer.from(entry.data, "base64");
  if (entry.compressed) buf = zlib.gunzipSync(buf);
  const dataUri = `data:${entry.mime};base64,${buf.toString("base64")}`;
  template = template.split(`"${uuid}"`).join(`"${dataUri}"`);
  fontCount += 1;
}

// 2) Korvaa kiinteät sivut (kansi/snapshot/sisällys) placeholderilla.
template = template.replace(
  /<div id="report">[\s\S]*?<\/div>\s*(?=<div id="flow-source")/,
  '<div id="report">{{FIXED_PAGES}}</div>\n'
);

// 3) Korvaa virtaava sisältö (osiot) placeholderilla.
template = template.replace(
  /(<div id="flow-source" class="flow-source">)[\s\S]*?(<\/div>\s*<script>)/,
  '$1{{FLOW_SECTIONS}}$2'
);

// 4) Templatisoi otsikko + sivutusskriptin header/footer-merkkijonot.
template = template.replace(
  /<title>[\s\S]*?<\/title>/,
  "<title>{{REPORT_TITLE}}</title>"
);
template = template.replace(
  /var header = '[\s\S]*?';\n/,
  "var header = {{HEADER_JS}};\n"
);
template = template.replace(
  /var footer = '[\s\S]*?';\n/,
  "var footer = {{FOOTER_JS}};\n"
);
template = template.replace(
  /var body = makePage\('[^']*'\);/,
  "var body = makePage({{FIRST_PAGE_LABEL_JS}});"
);

const checks = ["{{FIXED_PAGES}}", "{{FLOW_SECTIONS}}", "{{HEADER_JS}}", "{{FOOTER_JS}}", "{{REPORT_TITLE}}"];
const missing = checks.filter((c) => !template.includes(c));
if (missing.length) {
  throw new Error("Templatisointi epäonnistui, puuttuu: " + missing.join(", "));
}

fs.mkdirSync(path.dirname(outPath), { recursive: true });
fs.writeFileSync(outPath, template, "utf8");
console.log(`OK — ${fontCount} fonttia upotettu, template kirjoitettu: ${path.relative(ROOT, outPath)} (${template.length} merkkiä)`);
