"""Render a report JSON straight to HTML/PDF — no LLM run, no DB, no cost.

    python -m app.render_preview report.json out.html
    python -m app.render_preview report.json out.pdf

Iterate on report design in seconds against a saved/golden JSON instead of
waiting on (and paying for) a full multi-minute 6-stage pipeline run. Capture a
real report JSON from GET /api/runs/{rid}/report-source to use as input.
"""
import json
import sys

from . import render


def main(argv):
    if len(argv) < 2:
        sys.stderr.write(__doc__)
        return 2
    src, out = argv[0], argv[1]
    with open(src, encoding="utf-8") as f:
        report = json.load(f)
    if out.lower().endswith(".pdf"):
        render.render_pdf(report, out)
    else:
        with open(out, "w", encoding="utf-8") as f:
            f.write(render.render_html(report))
    sys.stderr.write(f"wrote {out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
