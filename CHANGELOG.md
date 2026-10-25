# Changelog

## [Unreleased]

- Support for building reports from a sequence of records.
- Add per-process cache for the host font table lookups.
- Build, read records, and render faster.

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
- A node or property the format does not define is accepted with a warning,
  and is an error when it is one edit from a legal name. An `accept` node,
  `--accept`, or an `x-` prefix accepts a name without a warning;
  `--strict-names` makes every other unknown name an error.
- JSON or NDJSON input in UTF-8, with field types declared in the template.
- Sandboxed [Starlark](https://github.com/bazelbuild/starlark) expressions:
  no imports, no filesystem, no network.
- Typed report parameters, with defaults and default expressions.
- Subreports, from an `embedded` layout in the same template or from
  a separate file named by `template=`. Either kind prints inline
  in the host's frame or on pages of its own; a separate file can
  give those pages a geometry of their own.
- Embedded and referenced images, with relative paths in printouts.
- Seven barcode types, encoded here from the standards: Code 128, Code 39,
  Code 93, Interleaved 2 of 5, QR, Data Matrix, and Aztec.

### Printout and renderer

- Printout: a renderer-neutral, absolutely-positioned, archive-friendly
  document, written as NDJSON or CBOR.
- Same printout round-trips to a byte-identical PDF.
- Printout embeds images and data blobs, fonts given by `data=` included.
  Fonts and images the template names by path are written relative to
  the printout, so a printout and those files move as one tree.
  A font found on the host is written as an absolute path.
- Every printout is checked against its twelve invariants before it is
  written, and `s-reports inspect` checks any printout it reads.
- PDF output with no layout decisions of its own.
- Reproducible: same template + same data + `--build-time` + `--strict-fonts`
  produce same bytes on any machine.

### Command line

- `s-reports build`, `s-reports validate`, `s-reports render`,
  `s-reports inspect`, `s-reports version`, `s-reports help`.
- The same program runs as `python -m sr`, and as `python sr.py`
  in a checkout without installing the package.
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
- The 65 differences are listed in
  [divergences.toml](https://github.com/a1s/sr.py/blob/v0.1.0/tests/differential/divergences.toml),
  each with its reason, and with the section of `doc/` that settles it
  where there is one. 40 of them are filed against the Go engine.

### Documentation

- The template format, the expression environment, layout and pagination,
  the printout format, barcode encoding, PDF rendering, and the command line
  are each specified in [doc/](https://github.com/a1s/sr.py/tree/v0.1.0/doc).
  This is the Go project's specification, with the questions it left
  to the code answered, and barcode encoding added.
- Three worked examples.
  [minimal](https://github.com/a1s/sr.py/tree/v0.1.0/example/minimal)
  is the printout doc/printout.md prints,
  built and compared byte for byte by the tests.
  [sakila](https://github.com/a1s/sr.py/tree/v0.1.0/example/sakila)
  and [invoices](https://github.com/a1s/sr.py/tree/v0.1.0/example/invoices)
  between them use every node in the format but `accept`, and all
  twelve `calc` modes.
