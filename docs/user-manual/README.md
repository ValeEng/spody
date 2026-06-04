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
alongside this README. Both are gitignored: the source files are
the tracked truth.

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

The intent is to ship the produced PDF inside the PyInstaller
bundle (`docs/` next to `spody-gui.exe`), reachable from the
**Help &rsaquo; User manual** menu entry in the GUI. The bundle
spec at `python/spody_gui.spec` can include this PDF as a
`datas` entry once the writing pass is complete.
