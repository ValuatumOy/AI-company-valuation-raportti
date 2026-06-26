"""Connect a finished run to the assembled JSON -> HTML/PDF renderer."""
import os

from . import render

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPORTS_DIR = os.path.join(_BACKEND, "_reports")


def _ensure_dir():
    os.makedirs(REPORTS_DIR, exist_ok=True)


def find_chrome() -> str | None:
    return render.find_chrome()


def generator_available() -> bool:
    return True


def generate_html(rid: str, report_json: dict) -> str:
    """Render assembled JSON to a standalone HTML file."""
    _ensure_dir()
    html_path = os.path.join(REPORTS_DIR, f"{rid}.html")
    html = render.render_html(report_json)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    return html_path


def generate_pdf(rid: str, report_json: dict) -> str:
    """Render assembled JSON to a PDF file with headless Chrome."""
    _ensure_dir()
    pdf_path = os.path.join(REPORTS_DIR, f"{rid}.pdf")
    return render.render_pdf(report_json, pdf_path)
