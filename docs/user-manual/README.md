# SpOdy user manual

End-user documentation for the SpOdy desktop bundle. Markdown
source under [`source/`](source/), CSS in
[`style.css`](style.css), built to PDF by
[`build_pdf.py`](build_pdf.py).

## Building

```powershell
# from this directory
..\..\python\.venv\Scripts\python.exe build_pdf.py
```

Produces `spody-user-manual.html` and `spody-user-manual.pdf`
alongside this README. The HTML is an intermediate and is
gitignored; **the PDF is tracked** so bundle builds never depend on
a Chromium-class browser being available in CI.

That makes rebuilding a manual step: nothing regenerates the
committed PDF automatically. After any change to `source/*.md`, run
`build_pdf.py` and commit the refreshed PDF in the same `docs:`
commit as the Markdown.

The build needs:

- the `markdown` and `pygments` Python libraries (installed in the
  GUI venv at `python/.venv/`);
- Microsoft Edge (preinstalled on Windows 10/11), used in headless
  mode for the final HTML &rArr; PDF print step. No LaTeX, no GTK,
  no pandoc.

Pass `--html-only` to stop after rendering the HTML, useful when
iterating on the CSS without spending time on the PDF step.

## Adding a chapter

Drop a new `NN-name.md` into `source/`. The numeric prefix decides
the chapter order (the build script sorts lexically). Top-level
`#` headings produce chapter numbers automatically via CSS
counters; you do not write chapter numbers in the Markdown source.

## Pipeline overview

1. `_collect_chapters` &mdash; glob `source/*.md` in lexical order.
2. `python-markdown` &mdash; convert to HTML with extensions for
   tables, fenced code blocks, Pygments highlighting, and the
   `toc` index generator.
3. `_wrap_html` &mdash; inline the CSS, prepend a cover page and
   the auto-generated TOC.
4. Microsoft Edge headless `--print-to-pdf` &mdash; render the
   self-contained HTML to A4 PDF with CSS-controlled margins and
   page-numbered footer. `--generate-pdf-document-outline` enables
   the sidebar bookmarks in PDF readers.

## Distribution

The PDF ships inside the PyInstaller bundle under `docs/` next to
`spody-gui.exe`, reachable from the **Help &rsaquo; User manual**
menu entry in the GUI. The bundle spec at
[`python/spody_gui.spec`](../../python/spody_gui.spec) probes for
the tracked PDF and appends it to `datas`; if the file is missing
it prints a warning and the bundle ships without the menu entry
working. [`python/build_bundle.py`](../../python/build_bundle.py)
attempts a rebuild before packing, best-effort: if Edge is missing
it warns and falls back to the committed PDF, which is exactly why
that PDF is tracked.
