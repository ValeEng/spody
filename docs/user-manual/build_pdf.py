# Copyright 2026 ValeEng
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Build the SpOdy user manual as a single PDF.

Pipeline:
    1. Read every `.md` in `source/` in lexical order (numeric prefix).
    2. Render with the Python `markdown` library (extensions for
       tables, fenced code, code highlighting via Pygments, and
       attribute lists used for admonition classes).
    3. Wrap the body in a self-contained HTML template that pulls
       `style.css` inline so the PDF needs no external assets.
    4. Drive Microsoft Edge in headless mode (`--print-to-pdf`) to
       render the HTML to A4 with margins + page-numbered footer.

Edge is picked because it's preinstalled on Windows 10/11 with the
exact Chromium-quality print-to-PDF stack we need; no GTK / LaTeX /
extra installs required. The script falls back to instructions if
the Edge binary isn't where it expects.

Run from this directory:

    python build_pdf.py            # writes spody-user-manual.pdf
    python build_pdf.py --html-only  # leaves spody-user-manual.html
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import markdown

HERE = Path(__file__).resolve().parent
SRC_DIR  = HERE / "source"
STYLE    = HERE / "style.css"
HTML_OUT = HERE / "spody-user-manual.html"
PDF_OUT  = HERE / "spody-user-manual.pdf"

# Hardcoded version label shown on the cover. Kept in sync with
# spody_gui/__init__.py:__version__ by convention; touched once per
# release.
APP_VERSION = "0.1.0-alpha"

# Candidates for a Chromium-class browser, queried in order. The
# print-to-PDF flag (`--print-to-pdf`) is identical across Edge,
# Chrome and Chromium since they all share the Chromium codebase.
#
# Windows: msedge ships with Win10/11.
# macOS:   Chromium / Chrome installed via brew or .dmg; Safari is
#          NOT Chromium-based so it cannot do --print-to-pdf.
# Linux:   chromium / chromium-browser / google-chrome from the
#          distro package manager. Edge for Linux also works if
#          someone installed it, but it's rare on CI runners.
BROWSER_BINARY_NAMES = [
    "msedge", "chrome", "google-chrome",
    "chromium", "chromium-browser",
]
BROWSER_FIXED_PATHS = [
    # Windows -- Edge installed by default
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    # macOS -- typical .app installations
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]

# Markdown extensions used across every chapter. Order matters only
# for `toc` which needs to run last.
MD_EXTENSIONS = [
    "fenced_code",
    "tables",
    "codehilite",     # syntax highlighting through Pygments
    "attr_list",      # {.note} / {.tip} class attributes on blocks
    "def_list",
    "footnotes",
    "smarty",
    "toc",
]
MD_EXT_CONFIG = {
    "codehilite": {"guess_lang": False, "noclasses": True,
                   "linenums": False, "pygments_style": "default"},
    "toc":        {"title": "Table of contents", "toc_depth": "1-3"},
}


def main() -> int:
    args = _parse_args()
    chapters = _collect_chapters()
    if not chapters:
        sys.stderr.write(f"[error] no .md files under {SRC_DIR}\n")
        return 2

    print(f">>> rendering {len(chapters)} chapter(s) to HTML")
    body_md = "\n\n".join(p.read_text(encoding="utf-8") for p in chapters)
    md_engine = markdown.Markdown(
        extensions=MD_EXTENSIONS, extension_configs=MD_EXT_CONFIG,
    )
    body_html = md_engine.convert(body_md)
    toc_html  = md_engine.toc

    html = _wrap_html(body_html, toc_html)
    HTML_OUT.write_text(html, encoding="utf-8")
    print(f"  wrote {HTML_OUT.relative_to(HERE)}")

    if args.html_only:
        return 0

    rc = _print_to_pdf(HTML_OUT, PDF_OUT)
    if rc == 0:
        size_kb = PDF_OUT.stat().st_size / 1024
        print(f">>> {PDF_OUT.relative_to(HERE)}  ({size_kb:.1f} KB)")
    return rc


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--html-only", action="store_true",
                   help="stop after writing the HTML "
                        "(skip the Edge headless step).")
    return p.parse_args()


def _collect_chapters() -> list[Path]:
    """Numeric-prefix sort so source/00-intro.md comes before
    01-install.md regardless of filesystem order."""
    return sorted(p for p in SRC_DIR.glob("*.md") if p.is_file())


def _wrap_html(body: str, toc: str) -> str:
    """Inline the CSS into a self-contained HTML doc + add cover and
    TOC blocks. Keeping everything in one file (no external links)
    means the HTML is portable and the print step has nothing to
    fetch over the network."""
    css = STYLE.read_text(encoding="utf-8")
    cover = f"""\
<section class="cover">
  <div>
    <div class="title">SpOdy</div>
    <div class="subtitle">User manual &mdash; v{APP_VERSION}</div>
  </div>
  <div class="meta">
    Desktop frontend for the SpOdy orbital propagator.<br>
    Patran-style file-based workflow: fill a TOML, run the binary,
    inspect the output.
  </div>
</section>
"""
    toc_section = f'<section class="toc">{toc}</section>'
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SpOdy user manual</title>
<style>{css}</style>
</head>
<body>
{cover}
{toc_section}
{body}
</body>
</html>
"""


def _find_browser() -> str | None:
    """First Chromium-class browser binary that exists on this machine,
    or None. Tries PATH lookups first (where CI runners install
    chromium / google-chrome), then the OS-specific fixed paths."""
    for name in BROWSER_BINARY_NAMES:
        found = shutil.which(name)
        if found:
            return found
    for cand in BROWSER_FIXED_PATHS:
        if Path(cand).is_file():
            return cand
    return None


def _print_to_pdf(src_html: Path, dst_pdf: Path) -> int:
    """Drive a Chromium-class browser headless to render the HTML to
    a print-quality PDF. The `file://` URL is absolute so the browser
    resolves it regardless of its own cwd; the --no-pdf-header-footer
    flag is omitted on purpose so the @page rules in style.css apply."""
    browser = _find_browser()
    if browser is None:
        sys.stderr.write(
            "[error] no Chromium-class browser found.\n"
            "Install one of: Microsoft Edge (Windows-default), Google\n"
            "Chrome, or Chromium (`apt install chromium`, `brew install\n"
            "chromium`). Alternatively open spody-user-manual.html in\n"
            "your browser and print to PDF manually.\n"
        )
        return 1
    print(f">>> printing via {browser}")
    args = [
        browser,
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",          # let the CSS @page rules win
        # Build a sidebar outline of all h1..h6 tags in PDF readers
        # that support it (Edge, Acrobat). Combined with the anchor
        # links the markdown 'toc' extension already injects into the
        # rendered TOC, the user gets two ways to navigate: click a
        # TOC entry to jump, or use the PDF outline panel.
        "--generate-pdf-document-outline",
        f"--print-to-pdf={dst_pdf}",
        src_html.as_uri(),
    ]
    proc = subprocess.run(args, capture_output=True, text=True)
    # Headless mode is chatty on stderr even on success; only surface
    # output when something actually failed.
    if proc.returncode != 0 or not dst_pdf.is_file():
        sys.stderr.write(proc.stderr or "(no stderr)")
        return proc.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
