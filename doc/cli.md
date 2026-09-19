# Command line

`sr.py` is a command line script over the [library](../README.md#library).
It applies a [template](template.md) to JSON data and writes a PDF
or a [printout](printout.md).

The command line decides nothing. Every flag maps to a library option, every
diagnostic comes from the engine or the renderer, and the two routes to a PDF
produce the same bytes: build it directly, or build a printout and render that
later. What is specified here is only the shell: how arguments are spelled,
which stream output goes to, what an exit code means, and what the two
reporting commands print.

## Contents

- [Conventions](#conventions)
- [`sr.py build`](#sr-build)
- [`sr.py validate`](#sr-validate)
- [`sr.py render`](#sr-render)
- [`sr.py inspect`](#sr-inspect)
- [`sr.py version`](#sr-version)
- [Reproducibility](#reproducibility)
- [What the command line does not do](#what-the-command-line-does-not-do)

## Conventions

### Subcommands

```
sr.py build     template plus data in, PDF or printout out
sr.py validate  check a template without data
sr.py render    printout in, PDF out
sr.py inspect   dump a printout as readable text
sr.py version   print the version
sr.py help      print usage, for one command or for all of them
```

`sr.py` with no arguments says that a command is required, points at
`sr.py help`, and exits 2. `sr.py help build`, `sr.py build -h` and
`sr.py build --help` all print the same thing and exit 0. Every command
rejects an argument it has no use for, `version` included.

### Flags

A flag may be written with one dash or two, and its value attached or separate:
`-t x.kdl`, `--template x.kdl` and `--template=x.kdl` are the same flag.
Flags may follow positional arguments, so `sr.py render out.srp.jsonl -o out.pdf`
works.

`--param` is the one repeatable flag. Values accumulate in the order given,
and two mistakes in it are refused rather than absorbed:

- **A name the template does not declare.** The value would go nowhere,
  the report would build with the default in its place, and nothing would
  say so. A misspelling is the likelier mistake in a generated command line,
  and it is the one with the worse failure mode. The message names the
  declared parameters. The library refuses it too, at build time.
- **A name given twice.** Last-wins is the convention, and it is wrong here:
  two places both think they own that parameter.

### Streams

What a command **produces** goes to stdout. What it **says about the run**
goes to stderr: the summary line, warnings, host diagnostics. So

```bash
sr inspect report.srp.jsonl | less
```

pages through the dump and nothing else, and a warning is still visible on the
terminal.

### `-` for a stream

`--data -` reads records from standard input. `--out -` writes to standard
output; for `sr.py build` that also requires `--format`, because there is no
extension left to read the format from.

A printout being *read* is always a file. Relative paths in a printout (a font,
an image with `embed=#false`) resolve against the directory it was read from,
and standard input has no directory, so `render` and `inspect` take a path.

### Exit codes

| | |
|---|---|
| 0 | success |
| 1 | the run failed: a file that would not open, a template that would not load, a required parameter with no value, a band that would not fit, a font that would not resolve, a printout that violates its own invariants |
| 2 | usage: an unknown flag, a missing required flag, an output extension that names no format |

**Warnings do not change the exit code.** A build that records a missing glyph,
a substituted font, or an oversized band under `--allow-overflow`, succeeded:
the printout exists and carries the warnings in its
[header](printout.md#header-line). The exit code says whether the document was
produced, and the warning list says what is wrong with it. A caller that wants
to treat warnings as failures tests the header, which survives archiving; an
exit code does not.

## `sr.py build`

```bash
sr build -t sakila.kdl -d payments.jsonl -o report.pdf
```

| Flag | |
|---|---|
| `-t`, `--template` | Template file. Required. |
| `-d`, `--data` | JSON array or NDJSON file; `-` for standard input. Omit for a template with no records. |
| `-o`, `--out` | Output path; `-` for standard output. Required. |
| `--format` | `pdf`, `jsonl` or `cbor`. Defaults to what `--out` ends in; required when `--out` is `-`. |
| `--param NAME=VALUE` | A report parameter. Repeatable. `VALUE` is text, parsed per the parameter's [declared type](template.md#parameter-values-as-text). |
| `--build-time` | RFC 3339. Fixes `BUILD_TIME`. |
| `--strict-fonts` | Resolve only fonts the template names by file or data; fail with the typeface named otherwise. |
| `--strict-names` | Refuse any [unknown node or property](template.md#unknown-names) instead of warning about it. |
| `--accept NAME` | Accept the named node or property without a warning, as a template's own [`accept`](template.md#accept) does. Repeatable. |
| `--allow-overflow` | Record an oversized band as a warning instead of failing. |
| `--uncompressed` | Leave PDF streams uncompressed, which makes the file readable in a text editor. Ignored for a printout. |
| `-v`, `--verbose` | Report host font diagnostics on stderr. |

The output format comes from `--format`, or from what `--out` ends in:

| | |
|---|---|
| `.pdf` | PDF |
| `.jsonl`, `.ndjson` | printout as NDJSON |
| `.cbor` | printout as CBOR |

Any other extension is a usage error naming the three. Guessing would write
a printout to a file called `report.txt` and call it done, and these are the
extensions the [printout format](printout.md#encoding) itself names.

`--format` **over a recognized extension warns**, on stderr, and proceeds:
`--format cbor -o report.jsonl` writes a file that `render` and `inspect`
will try to read as NDJSON, because they take the encoding from the extension.
Over an unrecognized extension -- or no extension, as for `-`, or one that
agrees -- it is silent, since overriding is what the flag is for.

Every check on the command line runs **before** the template is read, so a
mistyped flag is reported as itself rather than after a missing file or a
template's load warnings. The exceptions are the two that need the template:
`--param` against the declared names, and the build itself.

`--param` and `--build-time` are the only two ways a build depends on anything
but the template and the data, and both are recorded: parameters through
whatever the template prints, `BUILD_TIME` in the printout header's `built` field.

On success the command writes one line to stderr:

```
report.pdf: 15 pages, 4 fonts, 2 warnings
```

Warnings, when there are any, are listed before it, one per line.
A build without `--allow-overflow` that meets a band too tall for an empty frame
fails instead, with the node named -- that is the engine's rule, not this one's.

## `sr.py validate`

```bash
sr validate -t sakila.kdl
sr validate sakila.kdl
```

Loads and validates a template, resolves the fonts it declares, and reports what
it found. No data is read, so this is the check that belongs in a commit hook.

| Flag | |
|---|---|
| `-t`, `--template` | Template file. May also be given as the sole positional argument. |
| `--param NAME=VALUE` | A report parameter. Repeatable. |
| `--strict-fonts`, `--strict-names`, `--accept NAME` | As for `build`. |
| `-q`, `--quiet` | Print nothing on success. |
| `-v`, `--verbose` | Include host font diagnostics. |

Fonts are resolved because everything else in a template is settled at load,
from the document alone. Font resolution is the one part that depends on the
machine, and [enumeration diagnostics](template.md#the-substitute-face) exist
to be read here rather than attached to every printout.

Parameters are not required. One with no value, no `default` and no
`defaultexpr` is listed as `required` rather than failing the check:
a template whose values arrive at build time is not thereby invalid.
The one thing a missing value can cost is a `data` blob whose `expr`
reads it, and that is reported against the font that needed the blob.

The report goes to stdout:

```
template example/sakila/sakila.kdl
  report "DVD rental payments" version 2 by als
  Payments by customer, from the Sakila sample database
  page 595.276 x 841.89 pt, margins left 70.866 right 42.52 top 42.52 bottom 42.52
  2 columns, 1 group, 6 members, 5 variables, 4 fonts, 1 data blob
parameters
  period_start  date      default "2005-01-01"  prompt
  period_end    date      default "2006-01-01"  prompt
  as_of         datetime  defaultexpr
fonts
  body       8pt   explicit  example/fonts/Go-Regular.ttf  "Go"
  bold       8pt   explicit  example/fonts/Go-Bold.ttf  "Go"
  pagetitle  12pt  explicit  example/fonts/Go-Regular.ttf  "Go"
  title      14pt  explicit  example/fonts/Go-Bold.ttf  "Go"
ok
```

A name, and then something short and uniform after it, are padded to line up;
the rest of a line runs on, because what follows differs from row to row and
lining up fields that are not the same kind of thing reads as though they were.
A font line is the name the template gave it, its size and style, the
[step](template.md#font-resolution) that resolved it, the file, and the face
inside that file.

`ok` is the last line, so a `tail -1` reads the verdict; a check that failed
does not print it. Three sections may appear before it, each only when it
holds something:

| | |
|---|---|
| `subreports` | The [subreport](template.md#subreport) nodes the template carries, by path. A `template=` one is loaded and checked with its host, so a fault in it is reported against its own file. |
| `warnings` | Load diagnostics, an [unknown name](template.md#unknown-names), a substituted typeface. The check still passes. |
| `failures` | Fonts that did not resolve. These are why the exit code is 1, so they are not filed as warnings. |
| `diagnostics` | Under `--verbose`: what the [host font enumeration](template.md#host-enumeration) had to say. About the machine, not the template. |

Exit 1 means the template did not load, or a font it declares did not resolve.

## `sr.py render`

```bash
sr render report.srp.jsonl -o report.pdf
```

Reads a printout and renders it. The renderer does no layout, so this
produces the same PDF as the `build` that wrote the printout -- which is
what makes a printout worth archiving.

| Flag | |
|---|---|
| `-o`, `--out` | PDF path; `-` for standard output. Required. |
| `--uncompressed` | As for `build`. |

The printout path is positional, and its extension chooses the encoding to read:
`.cbor` is CBOR, anything else NDJSON.

A render reads the font files the printout resolved, and those are not part of it.
One that has moved since the printout was written is an error naming it, and the
previous output file is left where it was: the whole document is rendered before
the file is opened.

The printout's own warnings, recorded when it was built, are repeated on
stderr, because this is the point at which somebody looks at the document,
and an archived printout with a substituted font in it should not have to
be inspected to find that out.

## `sr.py inspect`

```bash
sr inspect report.srp.jsonl
sr inspect report.srp.cbor --summary
sr inspect report.srp.jsonl --pages 1,4-6
```

Dumps a printout as text, one mark per line, in paint order. The printout
is machine-generated JSON; this is the same content in a form that can be
read and diffed.

| Flag | |
|---|---|
| `--pages` | Page numbers to dump: `1`, `4-6`, `10-` and `-3` are all ranges, and they combine with commas. An open end means the first or the last page, so a bare `-` is every page. Default: all. |
| `--summary` | The header only, no pages. |

Inspecting also checks the printout against its own
[invariants](printout.md#invariants): that every mark lies inside its page,
that a text mark's box holds its lines, that an xref names a target that exists.
A violation is written to stderr after the dump, and the exit code is 1. An
inspector that read a broken printout without saying so would be the wrong tool.

The dump:

```
printout out/sakila.srp.jsonl
  format 1, engine "sr 0.1.0", built 2026-08-04T09:12:44Z, strict fonts
  report "DVD rental payments" version 2 by als
  Payments by customer, from the Sakila sample database
  page 595.276 x 841.89 pt, margins left 70.866 right 42.52 top 42.52 bottom 42.52
  1 page, 4 fonts, 2 data blobs
groups
  customer  4 runs  4 keys
fonts
  body       8pt   explicit  ../example/fonts/Go-Regular.ttf  "Go"
  title      14pt  explicit  ../example/fonts/Go-Bold.ttf  "Go"
data
  swatch        base64  92 bytes
  thelarch.jpg  base64  2415 bytes
page 1  595.276 x 841.89  138 marks
  barcode  box 70.866,42.52 102.96x18  Code128  "Code 128"  module 0.72  ink #000080  69 stripes
  rectangle  box 70.866,210.413 226.772x16  stroke #F3EDE7  width 0  dash solid  fill #000900
  text  box 79.37,213.613 90.709x9.6  font bold  color #F3EDE7  align left  leading 9.6
    "Film title"
  xref  box 394.866,42.52 157.89x28.346  url  "https://dev.mysql.com/doc/sakila/en/"  caption "Sakila sample database documentation"
    text  box 394.866,42.52 157.89x16.8  font title  color #000000  align right  leading 16.8
      "DVD rental payments"
```

The header is followed by a section per table the header carries -- `groups`,
`fonts`, `data`, `warnings` -- each present only when it holds something,
and each padded like the [check's](#sr-validate).

Every length is a number of points, as the printout holds it. A mark's `box` is
`left,top widthxheight`. A page line names the size the page runs at; a page that
[differs from the document](printout.md#page-lines) -- which a subreport paginating
itself produces -- names its margins too, since those are what a mark outside the
printable area was judged against. An [xref](printout.md#xref)'s nested marks are indented
under it, which is the only nesting a printout has. Text lines are quoted, one
per line, under the mark that carries them, so a wrapped paragraph reads as the
lines the engine actually broke it into. A field a mark does not carry is left
out rather than written empty: a rectangle with no `stroke` has no `width`,
an unrotated barcode does not say so, and a barcode with no `paper` shows only
its `ink`.

## `sr.py version`

```
sr 0.1.0
```

`sr.py --version` prints the same. The version is stamped in at link time
and is what a printout header's `engine` field carries, so an artifact
and the binary that made it can be matched up.

## Reproducibility

```bash
sr build -t t.kdl -d d.jsonl -o out.pdf --build-time 2026-08-04T09:12:44Z --strict-fonts
```

Those two flags are what make the output byte-identical on any machine. Without
`--build-time` the run's timestamp differs; without `--strict-fonts` the output
depends on which fonts are installed. Nothing else about the invocation matters:
the working directory does not reach the document, and a printout's paths are
written relative to where it lands.

## What the command line does not do

- **Prompt.** A parameter marked `prompt` is a hint for an interactive front end,
  and this is not one. A missing required parameter is an error naming it.
- **Stream.** The whole dataset is buffered, because `DATA_COUNT`, report-scoped
  aggregates and keep-together lookahead all need the full sequence. `--data -`
  reads standard input to the end before laying out a page.
- **Read a configuration file, or an environment variable.** Every input is
  an argument, so an invocation is reproducible from the command line alone.
- **Watch, serve, or open the result.** One run, one document.
