# Probes

One template per specification question.

The method, from M1 onward: write a template that isolates one question,
build it with the reference binary, read the answer out of the printout,
state the rule in `doc/`, and leave the probe here so it stays answered.

## The convention

For a probe named `NAME`:

| | |
|---|---|
| `NAME.kdl` | The template. Required — it is what makes the probe a case. |
| `NAME.jsonl` | Its records. Optional; see below. |
| `NAME.args` | Parameters, extra flags, and harness directives. Optional. |
| `NAME.answer.jsonl` | The reference's printout, committed. Generated, not written. |
| `NAME.inc.kdl` | A template another probe pulls in. Not built on its own. |

`NAME.answer.jsonl` is what keeps a probe answered rather than merely
building. The suite rebuilds every probe with the reference and holds it
to that file, so an oracle that changed its mind about line breaking fails
here rather than moving both engines at once. It is the printout as written --
the `lines`, the boxes, the `data` keys, and the exact spelling of every
number -- with only a font's `resolvedFile` replaced, since that records
where the build happened rather than what it decided.

Regenerate after adding or deliberately changing a probe:

```bash
python -m tests.differential.record_answers
```

A change to one of those files in a diff is a change in what `doc/` was written
from. Read it before committing it. A probe the reference refuses has no answer
file, and the suite checks that too.

A probe without records of its own is built over [`records.jsonl`](records.jsonl)
at the top of this directory: one record, whose contents nothing reads,
there so that a `detail` band runs once. Almost every probe wants exactly
that, and committing the same file beside each of them would suggest the
copies might differ and leave a reader diffing them to find out they do not.

A probe that wants **no** records ships an empty `NAME.jsonl`. That is a file,
so the shared records do not stand in for it, and an empty one reads to the
engine as no data at all.

Subdirectories are allowed and become part of the case identifier:
`probes/breaking/hyphen.kdl` is the case `probe/breaking/hyphen`. That
identifier appears in the test id, in a failure report, and in the
divergence register's patterns, so it is renamed only deliberately.

`NAME.args` holds one argument per line. A line starting with `-` is passed
to the engine as written; a line starting with `!` is a directive to the
harness rather than an argument; any other line is a `NAME=VALUE` parameter.
Blank lines and lines starting with `#` are ignored. There is one directive,
`!host-fonts`, below, and any other is refused rather than ignored.

```
# probes/breaking/overflow.args
--allow-overflow
period_start=2005-06-01
```

Every probe is built with `--build-time` fixed and `--strict-fonts` set, so
it must resolve its fonts by path. The committed faces in `example/fonts/`
are what to point at.

A probe that reads an image points at one of the small fixtures under `data/`,
each a few dozen bytes so that a recorded answer embedding one stays readable.
They are shaped for the questions the `data/` probes ask:

| | |
|---|---|
| `data/one/pic.png`, `data/two/pic.png` | one base name, two contents — what makes a generated name collide |
| `data/two/copy-of-one.png` | `one/pic.png`'s bytes under another name — what makes two entries share |
| `data/alpha.png`, `data/zebra.png` | two more contents whose names sort either side of `pic.png` |

An image in `example/` works too, and `example/fonts/` is where a probe's
fonts come from, but prefer these: a fixture that exists to be a fixture
can be changed when a probe needs it to be.

### Fonts from the host

A question about the [substitute face](../../../doc/template.md#the-substitute-face)
cannot be asked that way, since `--strict-fonts` refuses a `typeface` outright.
A probe whose sidecar holds `!host-fonts` is built without it, by both engines,
and resolves its fonts against the machine it runs on. Keep such a probe
to that question: everything else belongs in a strict one.

What the host chose is then in the printout, and it is the machine's rather
than the build's. The two engines run on one machine and are compared as
they are, but the recorded answer is not allowed to depend on it: the
substitute face's name is written `<substitute>` in the header, wherever
it appears there, and a `resolvedIndex` is taken out, since one platform's
substitute is a collection and the others' are not. `resolvedFile` is replaced
as for every probe. The layout does not move with the face, because leading
is a multiple of the font size, as long as the probe's text is too short to
wrap; a probe that let a line wrap would record one machine's metrics.

Every platform doc/ names ships a substitute face. A host without one fails
the build in both engines, and the probe with it.

## What is here

Each probe's own header comment states the question it isolates and the answer
the reference gave, and names the section of `doc/` that answer became.

| | |
|---|---|
| `breaking/opportunities` | which characters break a line |
| `breaking/whitespace` | what a break does to the whitespace at it |
| `breaking/trailing-space` | whether that whitespace counts toward the fit |
| `breaking/accumulation` | how a line's width is arrived at |
| `breaking/overlong` | a word wider than its box |
| `breaking/cut-edges` | the walk that tests and cuts, and what it leaves |
| `breaking/hard-break` | `U+000A`, from `text`, an expression and `format` |
| `breaking/codepoints` | the unit a cut falls between |
| `breaking/narrow` | boxes too narrow to wrap into, and boxes of zero width |
| `breaking/tolerance` | the comparison the fit test makes |
| `rounding/halfway` | which way a coordinate on the half goes |
| `rounding/negative` | the same question below zero |
| `rounding/tolerance-refuses` | how the 0.001 pt tolerance is added |
| `rounding/tolerance-admits` | the same, where the addition lands exactly |
| `printout/numbers` | how a number reaches the file |
| `printout/strings` | how a string reaches the file |
| `printout/used-fonts` | which fonts the header's table lists |
| `printout/no-fonts` | the table when no element prints |
| `printout/font-warning` | the warning a substituted typeface gives |
| `layout/band-height` | the two maxima a band's height is |
| `layout/alignment` | where a field's content sits in its box |
| `layout/field-format` | what an element's `format` applies to |
| `layout/reserved-bands` | what a header and a footer are measured against |
| `layout/guarded-footer` | a footer whose `printwhen` reserves nothing |
| `layout/content-height` | which elements have a height of their own |
| `layout/anchored-stretch` | a stretch field that declared a `bottom` |
| `layout/rules` | lines, rectangles, and boxes that reach past their band |
| `layout/clamps` | what `maxwidth` and `maxheight` clamp, and which edge stays |
| `layout/style-walk` | how far an unset style property falls through |
| `layout/floats` | the floating DAG, its gaps, and what is not in it |
| `layout/float-chains` | floating elements below floating elements |
| `layout/float-order` | which elements precede a floating one |
| `layout/float-gaps` | what a floating element's gap is measured to |
| `layout/float-zero-height` | a floating element of no height |
| `layout/xref` | a link region, measured as a container |
| `layout/xref-reach` | how far an xref's contents take the band |
| `pagination/fill` | what a page break does to the bands around it |
| `pagination/reservations` | when a header's space is reserved |
| `pagination/columns` | filling columns, and what each band sees |
| `pagination/across-columns` | a band placed across columns |
| `pagination/column-footer-full` | a column that has had its footer |
| `pagination/summary-above-column-footers` | a band across columns, and their footers |
| `pagination/column-after-title` | a column opened below a report title |
| `pagination/column-after-group-title` | a group's columns, below its title |
| `pagination/eject-below-title` | a band that fits a column, but not below a title |
| `pagination/split` | where a band splits, and what each half keeps |
| `pagination/orphans-widows` | a split preference, then giving it up |
| `pagination/cut-in-place` | the last resort, where the band is |
| `pagination/overflow` | where `--allow-overflow` puts a band |
| `pagination/overflow-first-record` | the warning an overflow on record 0 carries |
| `pagination/overflow-below-title` | an oversized band in a column below a title |
| `pagination/carried-once` | a band that fits only a later page |
| `pagination/eject-blank-page` | an `eject` node on an empty page |
| `pagination/eject-require` | `require`, and a column eject in one column |
| `pagination/require-empty-column` | `eject require` in an empty column |
| `pagination/title-eject` | a report title's ejects, tested after it |
| `pagination/suppressed-ejects` | the `eject` nodes of a band that does not print |
| `pagination/groups` | the record loop around one group |
| `pagination/nested-groups` | nested groups, and the end of the report |
| `pagination/group-pages` | a group's page number |
| `pagination/group-title-moves` | a group whose title moves to the next page |
| `pagination/footer-at-a-break` | the footer and header of a page that ends at a group break |
| `pagination/keeptogether` | keeping a group on one frame |
| `pagination/keeptogether-capped` | a group too big to keep together |
| `pagination/keeptogether-below-title` | keeping a group together below a title |
| `pagination/keeptogether-nested` | keeping a group together with groups inside it |
| `pagination/minrows` | rows that must follow a group's title |
| `pagination/mintailrows` | rows that must precede a group's summary |
| `pagination/swapped-bands` | where `swapheader` and `swapfooter` put a band |
| `pagination/swapfooter-ejects` | a swapped summary that does not fit |
| `pagination/swapfooter-column-footers` | a swapped summary, and the column footers |
| `pagination/swapfooter-overflow` | a swapped summary taller than a page |
| `pagination/swapped-title-floor` | the page below a swapped title |
| `pagination/balance` | spreading a page's bands over the columns |
| `pagination/balance-unreached` | a column the fill never reached |
| `pagination/item-order` | when `iter="item"` folds |
| `pagination/scope-resets` | `reset="item"` and `reset="detail"` |
| `pagination/first-page-init` | `init` from the start of the report, in every scope |
| `pagination/group-tables-empty` | the group tables over no records |
| `deferred/page-count` | a page count, and what a deferral reads |
| `deferred/scopes` | when each scope ends, and what `FINAL` holds |
| `deferred/nested-groups` | a group's deferrals, among groups |
| `deferred/columns` | the column scope, and a column's own bands |
| `deferred/balance-column` | a column deferral in a balanced frame |
| `deferred/balance-page` | a page deferral in a balanced frame |
| `deferred/balance-column-footer` | a column deferral in a footer |
| `deferred/balance-late-footer` | a column deferral only the last footer places |
| `deferred/balance-outside` | a column deferral outside the frame |
| `deferred/room` | where a resolved value is set |
| `deferred/centred` | the arithmetic that centres a resolved value |
| `deferred/placeholders` | what a placeholder reserves, and where |
| `deferred/swapped` | deferrals in swapped bands and a lookahead |
| `deferred/split` | a deferred stretch field in a band that splits |
| `deferred/at-a-break` | `FINAL` where a page ends at a group break |
| `deferred/glyphs` | the glyph warnings of a deferred field |
| `deferred/final-vertical` | the vertical names, through `FINAL` |
| `barcode/code128` | which code sets a Code 128 symbol uses |
| `barcode/code39` | Code 39's wide elements and its check character |
| `barcode/code93` | Code 93's full ASCII, and its check characters |
| `barcode/interleaved` | Interleaved 2 of 5's wide elements |
| `barcode/qr` | a QR symbol's mode, version, and mask |
| `barcode/datamatrix` | a Data Matrix symbol's encodation and size |
| `barcode/aztec` | how an Aztec symbol spends its bits, and its size |
| `barcode/geometry` | where a symbol sits in its box |
| `barcode/grow` | what `grow` expands a symbol to |
| `barcode/deferred` | where a deferred barcode's symbol goes |
| `barcode/colours` | the colours a barcode mark carries |
| `barcode/band` | what a barcode does to the band around it |
| `barcode/split` | a barcode in a band that splits |
| `barcode/rounding` | a barcode's lengths at three decimals |
| `barcode/charset` | the bytes a 2-D symbol carries, and its ECI |
| `barcode/qr-plus` | a plus sign among a QR value's digits |
| `data/blob-names` | the name an embedded image gets |
| `data/blob-collision` | a generated name that is already taken |
| `data/key-order` | the order of the header's `data` object |
| `data/declared-names` | a declared name against an identical file |
| `expressions/strings` | `len`, indexing and slicing over non-ASCII text |
| `expressions/string-methods` | the string type's iteration methods |
| `expressions/escapes` | what a literal's escapes name, where Python's and Starlark's part |
| `expressions/round` | the `round` builtin |
| `expressions/decimal-int` | comparing a decimal with an int |
| `expressions/decimal-abs` | the one builtin that will not take a decimal |
| `expressions/time-fields` | what `time.time` does with the fields it is not given |
| `expressions/decimal-precision` | how far a decimal's digits survive a float conversion |
| `expressions/null-folds` | what a null does to an accumulator |
| `expressions/date-layout` | a point in a layout that is not a fraction |
| `values/dimensions` | what a dimension string means |
| `values/colors` | what each colour spelling resolves to |
| `values/hex-float` | a number the host's parser takes and the grammar does not |
| `values/non-finite` | a dimension KDL can write and points cannot hold |
| `values/pagesize-iso` | what a page size name is worth, in millimetres |
| `values/pagesize-inches` | the same, where the standard is in inches |
| `values/pagesize-envelope` | the same, under `landscape` |
| `values/pagesize-card` | the same, for the entry whose round unit is not its own |
| `values/unknown-names` | a node and a property the format does not define |
| `values/negative-size` | a `height` below zero |

Twelve are [registered divergences](../divergences.toml) and are *expected*
to differ: the nine in the `expressions/` and `data/` groups, where the
reference refuses four outright -- which the harness treats as a difference
like any other -- and `values/hex-float` and `values/non-finite`, which
go the other way, built by the reference and refused here.
`expressions/time-fields` and `expressions/decimal-precision` are the odd
ones: most of their rows agree, and one row each carries the difference --
a date before year 1, and a precision deeper than a float reaches.
`values/unknown-names` is the twelfth and the only one about a rule neither
engine had written down: both refused a name the format does not define,
doc/ now accepts it, and the reference is the side that has not caught up.

A thirteenth was added in M6 and covers the two `breaking` probes that
report more than one missing glyph: the reference has two orders for
the `warnings` array and uses them both, so it cannot settle the question
and doc/printout.md does.

M7 added three more.
`layout/float-zero-height` is one: the reference drops the gap of a
floating element whose height is zero, which no reading of doc/ gives.
`printout/no-fonts` is the other: where no element prints, the reference
writes the font table as `null` rather than as an empty array.
Both of those are filed against the reference. The third,
`values/negative-size`, is a decision: doc/ now refuses a negative size,
and the reference still builds one.

`printout/font-warning` is older than any of them and was registered last.
The reference leaves `node` out of a `font` warning, which doc/ requires,
and has since M5; no probe could see it until `!host-fonts` let one reach
the substitute face.

M8 added the `pagination/` group, and fifteen divergences with it.
Eleven are defects in the reference's pagination rather than decisions:
`column-after-title` and `column-after-group-title`, where a column opened
after a band across the columns is drawn over it; `cut-in-place`, where
a band too tall for any frame is cut on a page the reference first ejects
to; `overflow-first-record`, where an overflow warning loses its record 0;
`nested-groups`, where the last outer group summary goes missing;
`group-title-moves`, where a group whose title moves counts the page it
left; `mintailrows`, which the reference does not implement;
`scope-resets`, where `reset="item"` and `reset="detail"` never fire;
`keeptogether-nested`, where a group is kept together as if the groups
inside it never broke; `require-empty-column`, where `eject require`
leaves a blank page; `summary-above-column-footers`,
`swapfooter-column-footers`, and `column-footer-full`, one divergence
between them, where a band across the columns runs into the strip their
footers are drawn in; and `swapfooter-overflow`, where a swapped summary
taller than a page is drawn above the paper.
The other four are decisions. In `footer-at-a-break`, a page that ends at
a group break has a footer that reads the run that ended and a header that
reads the run that begins, where the reference gives both the new run.
In `first-page-init`, every variable is seeded from `init` at the start
of the report, where the reference seeds only the report's and leaves
the first page's and first column's totals without it.
In `carried-once`, a band that fits only a later page is carried there
before it is judged an overflow, where the reference refuses the report.
In `suppressed-ejects`, a band that does not print tests none of its
`eject` nodes, where the reference tests them all the same.

Two more defects have no probe, because each would hold the suite up
for the harness's build timeout. A band in the page frame that fits
the frame's full height, but not the room below the column headers,
is ejected by the reference from page to page for ever, since no page
offers the height it is ejected in search of. So is one that fits below
the column headers as they reserved, but not as they were drawn, where
a header prints more when it is built than when it is reserved.
doc/layout.md#placing-a-band measures an empty frame below the headers
as drawn, and against that each band is an overflow. The tests of this
engine's answer are `test_an_empty_frame_is_measured_below_the_column_headers`
and `test_an_empty_frame_begins_below_the_headers_as_drawn` in
[test_pagination.py](../../unit/test_pagination.py).

M10 added the `barcode/` group and three defects in the reference with it,
all about what a symbol is rather than where it goes. In `code93`, the
reference writes Code 93's first check character and leaves out the second,
which the specification requires; in `qr-plus`, it writes digits with a plus
sign among them in numeric mode, which drops the sign; and in `rounding`,
it writes a barcode's length, its bars, and a grown module as binary64 left
them rather than at three decimals. The last would show in every barcode
drawn at a module that is not a whole number of points, so every other
`barcode/` probe draws at one point, and compares byte for byte.

M10 also added one decision, in `charset`. The reference writes a 2-D
value's UTF-8 bytes with no ECI, which every 2-D standard reads as
ISO 8859-1. doc/ keeps that as the default and adds `charset` and `eci`
for readers that follow the standard. The reference refuses both as unknown
properties, so the probe is refused there until it catches up.

Of the rest, every one agrees byte for byte except the three `data/`
probes, which need an image. Those are in [pending.toml](../pending.toml)
with the milestone that brings them. `rounding/tolerance-refuses` was
the fourth until M8 brought the page eject it needs.
