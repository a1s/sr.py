# Command line

`s-reports` is a command line script over the [library](../README.md#library).
It applies a [template](template.md) to JSON data and writes a PDF
or a [printout](printout.md).

An installed package puts the `s-reports` command on the path, and
`python -m sr` runs the same program. In a checkout, the launcher at the
root runs it as `python sr.py`. Whichever starts it, what follows holds.

The command line decides nothing. Every flag maps to a library option, every
diagnostic comes from the engine or the renderer, and the two routes to a PDF
produce the same bytes: build it directly, or build a printout and render that
later. What is specified here is only the shell: how arguments are spelled,
which stream output goes to, what an exit code means, and what the two
reporting commands print.

## Contents

- [Conventions](#conventions)
- [`s-reports build`](#sr-build)
- [`s-reports validate`](#sr-validate)
- [`s-reports render`](#sr-render)
- [`s-reports inspect`](#sr-inspect)
- [`s-reports version`](#sr-version)
- [Reproducibility](#reproducibility)
- [What the command line does not do](#what-the-command-line-does-not-do)

## Conventions

### Subcommands

```
s-reports build     template plus data in, PDF or printout out
s-reports validate  check a template without data
s-reports render    printout in, PDF out
s-reports inspect   dump a printout as readable text
s-reports version   print the version
s-reports help      print usage, for one command or for all of them
```

`s-reports` with no arguments says that a command is required,
points at `s-reports help`, and exits 2. `s-reports help build`,
`s-reports build -h`, and `s-reports build --help` all print
the same thing and exit 0. Every command rejects an argument
it has no use for, `version` included, and `help` takes one command
at most and no flags: a command it does not know is a usage error,
and so is a flag after it.

### Flags

A flag's one-letter name takes one dash and its long name two,
and its value is attached after an `=` or separate:
`-t x.kdl`, `-t=x.kdl`, `--template x.kdl`, and `--template=x.kdl`
are the same flag. `--t` and `-template` are no flags, and nor is
anything with three dashes: each is a usage error, and the message says
how a flag is spelled. A letter does not run into its value, so `-tx.kdl`
is an unknown flag. This is the convention most command lines keep,
and it leaves each of a flag's names one spelling.

Help is `-h` or `--help`, in place of a command or anywhere among
a command's flags. It is looked for before any other flag is read,
so it wins over a mistake elsewhere in the command line, and a flag
that takes a value does not take it as one: `s-reports build -t --help`
prints the usage of `build`. After `--` it is positional, as everything
there is. Help is a [switch](#flags), so `--help=false` does not ask
for it, and `--help=yes` is a usage error.

Flags may follow positional arguments,
so `s-reports render out.srp.jsonl -o out.pdf`
works. `--` ends them: what follows it is positional,
even where it starts with a dash. A `-` alone is positional
wherever it is, since it names a [stream](#--for-a-stream).

A flag that takes no value is a switch. A switch may also be given
`=true` or `=false`, as in `--strict-fonts=false`, so that a script
that always writes the flag can turn it off. No other value is one:
`--strict-fonts=1` is a usage error, and so is `--strict-fonts true`,
whose `true` is a positional argument.

A flag given twice takes the last value given. A value that is empty
is the flag not given, so `-d ""` reads no records and `-o ""` is
a missing `--out`. That lets a script pass a variable that may be empty
without a test around the flag.

A switch is the exception: its values are `true` and `false`,
and an empty one is neither, so `--strict-fonts=` is a usage error,
as `--strict-fonts=yes` is. A script that sets a switch from a variable
sets the variable to one of the two.

`--param` and `--accept` are the repeatable flags, and are an exception too:
an empty one names nothing, and is a usage error. An `--accept` given
twice is the one name. `--param` values accumulate in the order given,
and two mistakes in them are refused rather than absorbed:

- **A name the template does not declare.** The value would go nowhere,
  the report would build with the default in its place, and nothing would
  say so. A misspelling is the likelier mistake in a generated command line,
  and it is the one with the worse failure mode. The message names the
  declared parameters. The library refuses it too, at build time.
- **A name given twice.** Last-wins is the convention, and it is wrong here:
  two places both think they own that parameter.

Both are usage errors, and so is a `--param` that is not `NAME=VALUE`,
or whose name is empty. The first needs the template to find,
and is found once it has been read; the rest are found before.
A value that does not parse as its parameter's type is not
a usage error but a run that failed: the command line was well formed,
and what it supplied was wrong for this template.

### Streams

What a command **produces** goes to stdout. What it **says about the run**
goes to stderr: the summary line, warnings, host diagnostics. So

```bash
sr inspect report.srp.jsonl | less
```

pages through the dump and nothing else, and a warning is still visible on the
terminal.

Both streams are **UTF-8**, on every machine and under any console code page.
A printout's text is Unicode, and a dump whose bytes changed with the machine
it ran on could not be diffed against one made on another. For the same reason
every line ends with U+000A alone, on Windows too, where a pipe or a file
would otherwise get U+000D before each one. Text that cannot be encoded
at all, such as a file name that is not valid Unicode, is written as
a backslash escape rather than ending the run; in a printout, written
to a file or to standard output, that escape is JSON's own. CBOR has
no escapes, and a [CBOR printout](printout.md#cbor) writes U+FFFD instead.
No environment variable changes the bytes, `PYTHONIOENCODING` included.

On Windows this settles what `s-reports` writes, not what the next program
makes of it:

- **A console** shows the text as it should. Python writes to it through
  the console's own interface, and the code page does not come into it.
  Python's legacy console mode, `PYTHONLEGACYWINDOWSSTDIO`, turns that off,
  and the console then shows the UTF-8 bytes in its own code page, garbled.
- **A pipe or a file** gets UTF-8, rather than the legacy code page Windows
  would otherwise pick, so a `validate` report or a dump holding a character
  outside that page is written, not refused. Windows PowerShell 5.1 decodes
  a program's output with `[Console]::OutputEncoding` before a pipe or a `>`
  passes it on, and keeps the text only where that is UTF-8.

### `-` for a stream

`--data -` reads records from standard input. `--out -` writes to standard
output; for `s-reports build` that also requires `--format`, because there
is no extension left to read the format from.

Records on standard input are [UTF-8](template.md#data-input), as they are
in a file, and are read the same way. On Windows a pipe would otherwise be
decoded with the legacy code page, and an `é` in the data would arrive as
two other characters with nothing said about it.

Windows PowerShell 5.1 encodes what it pipes into a program with
`$OutputEncoding`, which is ASCII by default and turns every other
character into `?` before `s-reports` reads it. Set it to UTF-8 first:

```powershell
$OutputEncoding = [Text.Encoding]::UTF8
```

PowerShell may then put one or two byte order marks in front of the records,
depending on `[Console]::InputEncoding`. They are
[skipped](template.md#data-input).

A printout being *read* is always a file. Relative paths in a printout (a font,
an image with `embed=#false`) resolve against the directory it was read from,
and standard input has no directory, so `render` and `inspect` take a path,
and `-` in its place is a usage error.

### Exit codes

| | |
|---|---|
| 0 | success |
| 1 | the run failed: a file that would not open, a template that would not load, a required parameter with no value, a parameter value that does not parse, a band that would not fit, a font that would not resolve, a printout that violates its own invariants |
| 2 | usage: an unknown flag, a flag with the wrong number of dashes, a missing required flag, a flag value that is not one (a switch's other than `true` or `false`, a `--build-time` that is not RFC 3339, a `--pages` that is not a list of ranges), an output extension that names no format, a `--param` the template does not declare |

**Warnings do not change the exit code.** A build that records a missing glyph,
a substituted font, or an oversized band under `--allow-overflow`, succeeded:
the printout exists and carries the warnings in its
[header](printout.md#header-line). The exit code says whether the document was
produced, and the warning list says what is wrong with it. A caller that wants
to treat warnings as failures tests the header, which survives archiving; an
exit code does not.

## `s-reports build`

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
An extension is compared in any case, so `report.PDF` is a PDF.

`--format` names a format as an extension does, without the dot and in any case:
`pdf`, `jsonl`, `ndjson`, or `cbor`, the third being the second's other name.

`--format` **over a recognized extension warns**, on stderr, and proceeds:
`--format cbor -o report.jsonl` writes a file that `render` and `inspect`
will try to read as NDJSON, because they take the encoding from the extension.
Over an unrecognized extension -- or no extension, as for `-`, or one that
agrees -- it is silent, since overriding is what the flag is for.

Every check on the command line runs **before** the template is read,
so a mistyped flag is reported as itself rather than after a missing
file or a template's load warnings. A `--build-time` that is not RFC 3339
is one of them. The exceptions are the two that need the template:
`--param` against the declared names, and the build itself.

`--param` and `--build-time` are the only two ways a build depends on anything
but the template and the data, and both are recorded: parameters through
whatever the template prints, `BUILD_TIME` in the printout header's `built` field.

On success the command writes one line to stderr:

```
report.pdf: 15 pages, 4 fonts, 2 warnings
```

The output, as `--out` named it, and then what the document holds:
its pages and its fonts, always, and its warnings, where there are any.
A count of 1 takes the singular, so a report of no fonts is `1 page, 0 fonts`.
An output of `-` is named `standard output`, since `-` names nothing a reader
would recognize.

Warnings, when there are any, are listed before it, one per line,
each as `warning:` and then the line the dump's
[`warnings`](#sr-inspect) section writes for it:

```
warning: overflow  record 0  report > layout > detail  the band measures 80 pt and the largest frame offers 40 pt, and it cannot be cut
```

A note the load made about the template, which the printout does not carry
because it is about the template rather than the document, is listed with
them as `warning:` and the diagnostic.

A build without `--allow-overflow` that meets a band too tall for an empty frame
fails instead, with the node named -- that is the engine's rule, not this one's.

## `s-reports validate`

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

The fifth line counts what the template holds, in this order: columns,
groups, members, variables, fonts, data blobs, subreports. A term whose
count is zero is left out, which is why the block above says nothing about
subreports. Columns is the one that is never zero: a layout with no `columns`
node has [one column](template.md#columns).

The last term counts `subreport` nodes, not the layouts they name, so an
`embedded` layout invoked three times counts three. The layouts themselves
are not counted: the `subreports` section below already names every node that
uses one.

A name, and then something short and uniform after it, are padded to line up;
the rest of a line runs on, because what follows differs from row to row and
lining up fields that are not the same kind of thing reads as though they were.
A font line is the name the template gave it, its size and style, the
[step](template.md#font-resolution) that resolved it, the file, and the face
inside that file.

The first line names the template as the command line gave it,
as `inspect` names a printout. Text is quoted as the expression language
[quotes a string](expressions.md#quoted-strings): the report's name,
a typeface a font asked for, the family of the face it got,
and a parameter's default. A version and an author are written
as they are, as the dump writes them.

`ok` is the last line, so a `tail -1` reads the verdict; a check that failed
does not print it. Four sections may appear before it, each only when it
holds something:

| | |
|---|---|
| `subreports` | The [subreport](template.md#subreport) nodes the template carries, by path. A `template=` one is loaded and checked with its host, so a fault in it is reported against its own file. Its fonts are resolved too, each against its own `basedir` and its own `data` blobs, and one document is checked once however many nodes name it. Its failures and warnings appear in the sections below, but its fonts are not listed in `fonts`: that table is what *this* template declares. A subreport has no command line, so `--param` reaches the host document only. |
| `warnings` | Load diagnostics, an [unknown name](template.md#unknown-names), a substituted typeface. The check still passes. A warning the printout would carry is written as the dump's [`warnings`](#sr-inspect) section writes one; a load diagnostic is written as the diagnostic. |
| `failures` | Fonts that did not resolve. These are why the exit code is 1, so they are not filed as warnings. |
| `diagnostics` | Under `--verbose`: what the [host font enumeration](template.md#host-enumeration) had to say. About the machine, not the template. |

Exit 1 means the template did not load, a font it declares did not resolve,
or a `--param` value does not parse as its parameter's type.
A `--param` naming a parameter the template does not declare
is a usage error, exit 2, as it is for `build`.

## `s-reports render`

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
be inspected to find that out. They are written as `build` writes them,
and a last line names the output and counts the pages the PDF has:

```
report.pdf: 15 pages
```

## `s-reports inspect`

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
| `--pages` | Page numbers to dump: `1`, `4-6`, `10-`, and `-3` are all ranges, and they combine with commas. An open end has no bound, so `10-` is every page numbered 10 or more, and a bare `-` is every page. Default: all. |
| `--summary` | The header only, no pages. |

A page number is decimal digits, however many, counting from 1, and
spaces around a number are ignored. What is not a range is a usage error,
found before the printout is read: a range that is empty, as between
two commas, a number below 1 or written in anything but decimal digits,
and a range that counts backwards, as `3-1`. A page past the last is
not an error; there is just no page there to dump. Each page is held
to the ranges by the `number` its page line carries, so in a printout
whose numbering is broken, which is one `inspect` is there to show,
`3-` still reaches a page numbered 5 where there are only three.

A CBOR printout is read as [the format writes one](printout.md#cbor),
and a tag, a byte string, or an indefinite length is refused at the byte
it starts at: the file is not a printout. A general CBOR decoder reads
a tag as the type it stands for, and the dump would show that type
as if a printout could hold it.

Inspecting also checks the printout against its own
[invariants](printout.md#invariants): that every mark lies inside its page,
that a text mark's box holds its lines, that an xref names a target that exists.
A violation is written to stderr after the dump, and the exit code is 1. An
inspector that read a broken printout without saying so would be the wrong tool.

The whole printout is checked, whatever `--pages` or `--summary` leave out
of the dump. Each violation is one line, `invariant:` and then where it is
and what is wrong, and a last line counts them:

```
invariant: the header counts 2 pages, and 1 page lines follow
invariant: page 1, mark 1: its font "nowhere" is not in the header's font table
sr: report.srp.jsonl: 2 invariants broken
```

A mark is numbered from 1 in paint order, and one inside an xref after
the xref's own number, so `mark 5.1` is the first mark inside the fifth.

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

The first line names the file as the command line gave it. The header's
last line counts the pages and the fonts, always, and the data blobs,
where there are any; a count of 1 takes the singular, here and in every
section below, as in `1 run`. Warnings are not counted there, since the
section that lists them is the count.

The header is followed by a section per table the header carries -- `groups`,
`fonts`, `data`, `warnings` -- each present only when it holds something,
and each padded like the [check's](#sr-validate). A blob's size is the bytes
it holds, decoded, rather than the length of its spelling in the printout,
and literal text is counted in UTF-8. A warning is its kind, then `record` and
the record's index where a record produced it, then its node, with a property
after it as `name=` where the warning is about one, then its message, each two
spaces from the next:

```
warnings
  overflow  record 0  report > layout > detail  the band measures 80 pt and the largest frame offers 40 pt, and it cannot be cut
  unknown  report > layout > detail wobble=  unknown property `wobble`, accepted and ignored
```

Every length is a number of points, as the printout holds it, with every digit
it holds: the dump does not round a number another writer left unrounded.
A mark's `box` is `left,top widthxheight`. A page line names the size the page
runs at, `page 1  595.276 x 841.89  138 marks`. A page that sets any of its
own [geometry](printout.md#page-lines) -- which a subreport paginating itself
produces -- names all four of its margins too, as the header's page line does,
since those are what a mark outside the printable area was judged against:
`page 2  841.89 x 595.276 pt, margins left 42.52 right 42.52 top 42.52 bottom 42.52  12 marks`.
An outline entry is its point, its level, its title, its name where it has one,
and `closed` where it is:
`outline  at 70.866,42.52  level 1  "DVD rental payments"  name "top"  closed`.
Text is quoted as the expression language
[quotes a string](expressions.md#quoted-strings), a value, a title,
a name, a target, and a caption alike. An [xref](printout.md#xref)'s
nested marks are indented under it, which is the only nesting a printout has.
Text lines are quoted, one per line, under the mark that carries them,
so a wrapped paragraph reads as the lines the engine actually broke it into.
A field a mark does not carry is left out rather than written empty:
a rectangle with no `stroke` has no `width`, an unrotated barcode does
not say so, and a barcode with no `paper` shows only its `ink`.
A barcode's line ends with how many runs a 1-D symbol has, `69 stripes`,
or how many rows a 2-D one has, `25 rows`; the runs themselves are
not shown. An image's line ends with its `crop` where it has one,
written as a box is, in pixels: `crop 7,3.5 10x5`.

## `s-reports version`

```
sr 0.1.0
```

`s-reports --version` prints the same. `s-reports -version` is a usage error,
since a long name takes [two dashes](#flags). The version is
what a printout header's [`engine`](printout.md#the-engine-field)
field carries, so an artifact and the version of the engine that made it
can be matched up.

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
