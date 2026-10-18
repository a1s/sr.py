# Changelog

## [0.1.0] - 2026-10-18

First release. A second implementation of the
[sr](https://github.com/a1s/sr) report format, in Python,
written from the specification rather than ported from the Go engine.

### Engine

- Banded report engine with `title`, `summary`, `header`, `footer`,
  and `detail` bands.
- Group scoping, keep-together, orphan/widow control, balanced columns.
- Deferred evaluation, so the first page can print the final page count.
- Running variables with twelve `calc` modes for data aggregation.

### Templates and data

- [KDL](https://kdl.dev) v2 templates.
- A node or property the format does not define is a warning, not an error.
  `--strict-names` refuses it; `--accept` and an `x-` prefix silence it.
- JSON or NDJSON input in UTF-8, with field types declared in the template.
- Sandboxed [Starlark](https://github.com/bazelbuild/starlark) expressions:
  no imports, no filesystem, no network.
- Typed report parameters, with defaults and default expressions.
- Two kinds of subreports: an inline `embedded` block with its own records,
  and a `template=` reference to a separate file that paginates onto pages
  of its own geometry.
- Embedded and referenced images, with relative paths in printouts.
- Seven barcode types, encoded here from the standards: Code 128, Code 39,
  Code 93, Interleaved 2 of 5, QR, Data Matrix, and Aztec.

### Printout and renderer

- Printout: a renderer-neutral, absolutely-positioned, archive-friendly
  document, written as NDJSON or CBOR.
- Same printout round-trips to a byte-identical PDF.
- Printout embeds images and data blobs; fonts and non-embedded images
  are relative references, so a printout and the files it points at
  move as one tree.
- Every printout is checked against its twelve invariants before it
  is written, and `s-reports inspect` checks any printout it reads.
- PDF output with no layout decisions of its own.
- Reproducible: same template + same data + `--build-time` + `--strict-fonts`
  produce same bytes on any machine.

### Command line

- `s-reports build`, `s-reports validate`, `s-reports render`,
  `s-reports inspect`, `s-reports version`, `s-reports help`.
- The same program runs as `python -m sr`, and as `python sr.py`
  in a checkout with nothing installed.
- Document on stdout, diagnostics on stderr, three exit codes.
- Both streams are UTF-8 with LF line endings on every platform,
  Windows included.

### Library

- `sr.api` for the engine, `sr.printout` for the document a renderer reads,
  `sr.pdf` for the renderer. Fully typed, with `py.typed`.
- Python 3.11 to 3.14, tested on Linux, macOS, and Windows.
- Five dependencies: ckdl, fontTools, cbor2, Pillow, and tzdata.
  The expression evaluator, the barcode encoders, the layout,
  and the PDF writer are written here.

### Compared with the Go engine

- The test suite builds the examples and a corpus of probes with both
  engines, and compares the printouts byte for byte, the PDFs pixel
  by pixel, and the command line's reports line by line.
- Where the two differ, `doc/` decides. The 65 differences are listed in
  [divergences.toml](https://github.com/a1s/sr.py/blob/v0.1.0/tests/differential/divergences.toml),
  each with the section of `doc/` that settles it.
  40 of them are filed against the Go engine.

### Documentation

- The template format, the expression environment, layout and pagination,
  the printout format, barcode encoding, PDF rendering, and the command line
  are each specified in [doc/](https://github.com/a1s/sr.py/tree/v0.1.0/doc).
  This is the Go project's specification, with the questions it left
  to the code answered, and barcode encoding added.
- Three worked examples.
  [minimal](https://github.com/a1s/sr.py/tree/v0.1.0/example/minimal)
  is the printout doc/printout.md prints, built and compared byte for byte
  by the tests.
  [sakila](https://github.com/a1s/sr.py/tree/v0.1.0/example/sakila)
  and [invoices](https://github.com/a1s/sr.py/tree/v0.1.0/example/invoices)
  between them use every node in the format and all twelve `calc` modes.
