# sr.py — structural reporting

A banded report formatter. Apply a template to a sequence of records
and get a paginated document: bands, groups, page and column headers
and footers, running aggregates, and page footers that know the final
page count.

A Python library with a CLI over it, counterpart to the parallel
[Go project](https://github.com/a1s/sr). Template plus JSON in, PDF out.

## Documentation

| | |
|---|---|
| [doc/template.md](https://github.com/a1s/sr.py/blob/main/doc/template.md) | Template format: nodes, properties, geometry, ordering rules, font resolution |
| [doc/expressions.md](https://github.com/a1s/sr.py/blob/main/doc/expressions.md) | Expression language, predefined names, formatting, variable semantics |
| [doc/layout.md](https://github.com/a1s/sr.py/blob/main/doc/layout.md) | Layout and pagination |
| [doc/printout.md](https://github.com/a1s/sr.py/blob/main/doc/printout.md) | The intermediate document a renderer consumes |
| [doc/barcode.md](https://github.com/a1s/sr.py/blob/main/doc/barcode.md) | Barcode encoding: which symbol each type draws for a value |
| [doc/render.md](https://github.com/a1s/sr.py/blob/main/doc/render.md) | PDF rendering: what a renderer decides, and what it must not |
| [doc/cli.md](https://github.com/a1s/sr.py/blob/main/doc/cli.md) | The command line: subcommands, flags, streams, exit codes |
| [example/minimal/](https://github.com/a1s/sr.py/tree/main/example/minimal/) | The smallest complete report, and the printout doc/printout.md shows |
| [example/sakila/](https://github.com/a1s/sr.py/tree/main/example/sakila/) | Reference template and dataset |
| [example/invoices/](https://github.com/a1s/sr.py/tree/main/example/invoices/) | Second example: both kinds of subreport, region grouping, the remaining variable modes |
| [example/fonts/](https://github.com/a1s/sr.py/tree/main/example/fonts/) | Fonts committed so examples resolve identically everywhere |

## How it works

Three artifacts and one language:

- A **template** is a [KDL](https://kdl.dev) v2 document describing page geometry
  and a tree of bands. Bands are `title`, `summary`, `header`, `footer`, and
  `detail`; groups nest around the detail band.
- **Data** is JSON or NDJSON. Field types are declared in the template, which is
  what turns `"19.99"` into an exact decimal and `"2005-05-24T22:53:30Z"` into a
  time value.
- A **printout** is the engine's output: pages of absolutely-positioned marks
  with nothing evaluable left in them. Renderers do no layout.
- **Expressions** are [Starlark](https://github.com/bazelbuild/starlark) —
  sandboxed, with no imports, filesystem, or network.

Layout is speculative: every band is measured before it is committed, then placed,
split, or deferred to the next frame. Band splitting, keep-together, orphan and
widow control, and correct deferred page counts all follow from that.

## Command line

```bash
python sr.py build --template sakila.kdl --data payments.jsonl --out report.pdf
```

That is the launcher at the root of a checkout, which runs the program
without installing the package. Installed with `pip install s-reports`,
the same program is the `s-reports` command, and `python -m sr` where
the interpreter's scripts directory is not on the path.

| Flag | |
|---|---|
| `-t`, `--template` | Template file. Required. |
| `-d`, `--data` | JSON array or NDJSON file, `-` for standard input. Omit for a template with no records. |
| `-o`, `--out` | Output path, `-` for standard output. Extension selects the format: `.pdf`, `.srp.jsonl`, `.srp.cbor`. |
| `--format` | `pdf`, `jsonl` or `cbor`, when the extension does not say. |
| `--param NAME=VALUE` | Supply a report parameter. Repeatable. `VALUE` is text, parsed per the parameter's declared type. A name the template does not declare is an error, not a value that goes nowhere. |
| `--build-time` | RFC 3339. Fixes `BUILD_TIME` for reproducible output. |
| `--strict-fonts` | Resolve only explicitly named font files; fail otherwise. |
| `--strict-names` | Refuse a node or property the format does not define, instead of warning about it. |
| `--accept NAME` | Take one such name without a warning. Repeatable. |
| `--allow-overflow` | Downgrade oversized-band errors to warnings recorded in the printout. |
| `--uncompressed` | Leave PDF streams uncompressed, to read the file in an editor. |
| `-v`, `--verbose` | Report host font diagnostics. |

Other subcommands:

```bash
python sr.py validate sakila.kdl             # check a template without data
python sr.py inspect report.srp.jsonl        # dump a printout as readable text
python sr.py render report.srp.jsonl -o report.pdf
```

`build` writing a printout and `render` reading one back produce the same PDF as
`build` writing PDF directly, which is what makes printouts worth archiving.

The document goes to standard output and everything about the run to
standard error, so `-o -` pipes. Exit 0 is success, 1 a run that failed,
2 a mistake in the command line; warnings never change it, because they
travel in the printout header where archiving keeps them. A one-letter flag
takes one dash and a long one two, so `-t` and `--template`, but not `--t`
or `-template`. A value is attached with `=` or separate, a flag may follow
the positional arguments, and a switch also takes `=true` or `=false`.
Full reference: [doc/cli.md](https://github.com/a1s/sr.py/blob/main/doc/cli.md).

## Library

```python
from pathlib import Path

from sr.api import Options, build
from sr.printout.write import write_jsonl

result = build(
    Path("sakila.kdl"),
    Path("payments.jsonl"),
    Options(params={"period_start": "2005-06-01"}, strict_fonts=True),
)
out = Path("sakila.srp.jsonl")
with out.open("w", encoding="utf-8", newline="") as handle:
    write_jsonl(result.printout, handle, out.parent)
```

`build` returns the printout as an object, which is the primary artifact:
serializing it is a separate step, and the directory it is written to is what
its font and image paths are made relative to. Every flag on the command line
is a field of `Options`, because the command line
[decides nothing](https://github.com/a1s/sr.py/blob/main/doc/cli.md).

Rendering is a separate step too, and takes the printout in memory
or one read back from a file. Both give the same bytes:

```python
from sr.pdf import render
from sr.printout.read import read

Path("sakila.pdf").write_bytes(render(result.printout))
Path("again.pdf").write_bytes(render(read(out)))
```

## Running the examples

```bash
python sr.py build -t example/minimal/minimal.kdl -d example/minimal/films.jsonl -o minimal.srp.jsonl
```

[minimal.kdl](https://github.com/a1s/sr.py/blob/main/example/minimal/minimal.kdl)
is the smallest report there is: one font, a header with a rule,
and one field per record. Its printout is the example at the end of
[doc/printout.md](https://github.com/a1s/sr.py/blob/main/doc/printout.md#example),
which the test suite builds and compares byte for byte, so that example is a
document rather than an illustration of one.

```bash
python sr.py build -t example/sakila/sakila.kdl -d example/sakila/payments.jsonl -o sakila.pdf
```

Narrowed to one month, using the template's `date` parameters:

```bash
python sr.py build -t example/sakila/sakila.kdl -d example/sakila/payments.jsonl -o june.pdf --param period_start=2005-06-01 --param period_end=2005-07-01
```

The second example, which uses both kinds of subreport:

```bash
python sr.py build -t example/invoices/invoices.kdl -d example/invoices/invoices.jsonl -o invoices.pdf
```

[sakila.kdl](https://github.com/a1s/sr.py/blob/main/example/sakila/sakila.kdl) —
a payment list grouped by customer in two columns: every band type,
a group with its own title and summary, column header and footer,
stretch fields, a floating element, six barcode types, an embedded image
and a referenced one, both kinds of cross-reference, conditional outline entries,
a deferred page count, justified text, and typed parameters.

[invoices.kdl](https://github.com/a1s/sr.py/blob/main/example/invoices/invoices.kdl) —
invoices by region with line items: an inline `embedded` subreport
with an `arg` and its own `records`, a `template=` subreport in
[region_sheet.kdl](https://github.com/a1s/sr.py/blob/main/example/invoices/region_sheet.kdl)
that paginates itself onto landscape pages of its own, a group using `keeptogether`
with `minrows` and `mintailrows`, a `summary` with `swapfooter`, an image with
`embed=#false`, a compressed `data` blob, `iter="item"` against `iter="detail"`,
and the `calc` modes sakila leaves out.

Between them the two templates use every node in the format and all twelve `calc`
modes. They do not exhaust every property. What they leave out, so that reading
them as a reference does not mislead:

| | |
|---|---|
| Properties | `layout width` / `height` / `landscape`, `font typeface` / `data` / `bold` / `italic`, `line backslant`, `image type`, `barcode data`, `data expr`, `maxheight`, `format` as a date-parse layout on either `parameter` or `member`, `subreport ownpageno`, `columns balance` |
| Spellings | the `x` / `y` aliases for `left` / `top` — both examples use `left` and `top` throughout |
| Enumerations | barcode types `Code93`, `DataMatrix`, `QR-M`; `image scale="cut"` and `"grow"`; `dash="dash"` |
| Scopes | `iter="report"` / `"page"` / `"column"`; `reset="detail"` / `"item"` |

Both reference the [committed fonts](https://github.com/a1s/sr.py/tree/main/example/fonts/)
by path, so they build identically on any machine and work under `--strict-fonts`.
Swap `file=` for `typeface=` to use whatever the machine has instead.

## Reproducible output

The same template over the same data produces byte-identical printouts when
`--build-time` is fixed and `--strict-fonts` is set -- **on any machine**,
not just across runs on one. Rendering adds no timestamp of its own.
Without the first, the run timestamp differs; without the second,
output depends on which fonts are installed.

Nothing machine-specific survives in a strict printout. Strict mode admits only
fonts the template named by path, and every path the template named — fonts and
`embed=#false` images alike — is written relative to the printout rather than
absolute. So a printout and the files it points at move as one tree. The header
records which font file each typeface resolved to and by which step, so a difference
is diagnosable from the artifact.

## Development

```bash
make install    # a venv, the dependencies, and the test and lint tools
make check      # ruff, mypy, pytest
```

Everything generated goes under `build/`: the pytest, ruff and mypy caches,
setuptools' metadata, and compiled bytecode. The first three come from
`pyproject.toml` and the fourth from `PYTHONPYCACHEPREFIX`, which the
`GNUmakefile` exports — a `pytest` typed straight into a shell still writes
`__pycache__` beside the sources unless that variable is exported there too.

Tests are `pytest`. `tests/unit/` covers a module at a time,
`tests/golden/` holds this engine to documents that are printed in the
specification, and `tests/differential/` compares this engine against the
Go one, byte for byte, over the examples and over a corpus of probes.
It compares the renderers too, by drawing each page of both PDFs
with pdfium, from the `oracles` extra, and comparing the pixels;
a PDF is also held to its structure by the tests' own reader,
and by `qpdf --check` where qpdf is on the path.
And it compares the command line: what `validate` reports on every
template in the corpus, what `inspect` dumps of every printout,
and the exit code, the output, and the summary line of a table
of command lines, each asking one question of doc/cli.md.

The engine was built a milestone at a time, and every command and
output format is here now. Every node the format has is built: it paginates
reports of `field`, `line`, `rectangle`, `image`, `barcode`, and `xref`
elements and outline entries, with groups, columns, band splitting,
`eject` nodes, keep-together rules, balanced columns, deferred values,
and subreports of both kinds, inline and with pages of their own,
and writes them as NDJSON, CBOR, or PDF. Each printout is held to its
own [invariants](https://github.com/a1s/sr.py/blob/main/doc/printout.md#invariants)
before it is written, and `s-reports inspect` holds
any printout it reads to them. Every barcode type is encoded here,
from the standards, rather than by a library, and an image
is interpreted only as far as the header that states its size.
The PDF writer is written here too: the object table and the cross
reference, the drawing, the outline and the links. `fontTools` subsets
the faces and Pillow decodes the images the file does not carry
as they stand. Each rendered page is drawn by pdfium and compared,
pixel by pixel, with the same printout rendered by the reference.
The command line and its two reports are held to the reference's too.
The cases the differential corpus cannot yet compare are listed,
with what each waits for, in
[tests/differential/pending.toml](https://github.com/a1s/sr.py/blob/main/tests/differential/pending.toml).

The Go implementation is the **oracle**: a reference binary whose *outputs*
settle questions the specification leaves open. Its source is not read --
this is a second implementation from the specification, not a port.
The harness builds it from a tree beside this repository, and skips
with a reason where there is no Go toolchain to build it with.
See [tests/differential/README.md](https://github.com/a1s/sr.py/blob/main/tests/differential/README.md).

Where the two engines disagree and `doc/` does not say who is right,
the answer is a sentence in `doc/`, not a workaround in either engine.

## License

MIT. See [LICENSE](https://github.com/a1s/sr.py/blob/main/LICENSE).
