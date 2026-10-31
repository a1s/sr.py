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
number -- with only the paths a template named and the engine's version
replaced, since those record where the build happened and which version
of the reference ran it, rather than what it decided. A font's `resolvedFile`
is written `<resolved>`, and an `embed=#false` image's `file` is written
`<resolved>/` and its base name, so that a probe reading several images
still says which went where. The header's `engine` is written `sr <version>`
when it holds the version the reference reports, so the reference starting
work on its next version changes no answer, while a header that disagrees
with the reference's `version` still fails.

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

A probe that reads an image points at one of the small fixtures under `data/`
or `image/`, each a few dozen or a few hundred bytes so that a recorded answer
embedding one stays readable. Those under `data/` are shaped for the questions
the `data/` probes ask, which are about names:

| | |
|---|---|
| `data/one/pic.png`, `data/two/pic.png` | one base name, two contents — what makes a generated name collide |
| `data/two/copy-of-one.png` | `one/pic.png`'s bytes under another name — what makes two entries share |
| `data/alpha.png`, `data/zebra.png` | two more contents whose names sort either side of `pic.png` |

Those under `image/` are shaped for the `image/` probes, which are about sizes
and so mostly read them with `embed=#false`, keeping the base64 out of the
answers:

| | |
|---|---|
| `image/wide.png` | 24 by 12, wider than tall, so that an axis swapped shows |
| `image/odd.png` | 7 by 5, whose ratios to a box do not come out even |
| `image/dpi.png`, `image/wide.jpg` | 24 by 12 with a resolution of 144 and of 300 dots per inch |
| `image/rotated.jpg` | 24 by 12 with an EXIF orientation of 6, a quarter turn |
| `image/progressive.jpg`, `image/wide.gif` | 24 by 12 in the other two encodings |
| `image/screen.gif` | a logical screen of 30 by 20 around a frame of 24 by 12 |
| `image/png.jpg` | PNG bytes under a JPEG name |
| `image/no-pixels.gif` | a logical screen of 0 by 0, and no frame |

`data/font-blob.kdl` carries its own face, in base64: Go Regular cut down
to `x`, `y` and `.notdef`, 652 bytes, under the Go fonts' licence beside
the full faces in `example/fonts/`. `printout/twin-fonts.kdl` carries
the same face. The first does not use two font nodes the same in
everything but their names, because that is the question the second asks.

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
| `printout/twin-fonts` | two fonts that resolve to one face at one size |
| `printout/margins` | a mark outside the page's printable area |
| `printout/quoted-text` | how the reports quote a report's name and a default |
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
| `pagination/nested-column-number` | `COLUMN_NUMBER` in columns inside columns |
| `pagination/column-number-skip` | the same, where the outer block breaks first |
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
| `data/declared-shared` | two declared names for one content |
| `data/content` | the blob an image's `content` child holds, and its name |
| `data/build-order` | what "first" means for a generated name |
| `data/unprinted` | an image built and never drawn |
| `data/font-blob` | a font read out of a `data` node |
| `image/natural` | what an image's natural size is |
| `image/cut` | what `scale="cut"` draws |
| `image/cut-aligned` | which pixels a clipped image keeps |
| `image/fill` | what `scale="fill"` draws |
| `image/grow` | what `scale="grow"` draws, and the box it grows |
| `image/grow-valign` | a grow image shorter than its box |
| `image/band` | what an image does to the band around it |
| `image/split` | an image in a band that splits |
| `image/type` | an image given a `type` |
| `image/no-pixels` | an image with no pixels |
| `subreport/inline` | an inline subreport's own context |
| `subreport/inline-eject` | an eject inside an inline subreport |
| `subreport/inline-before` | a negative-seq subreport that ejects |
| `subreport/inline-columns` | an inline subreport's own columns |
| `subreport/inline-columns-title` | a title across a subreport's columns |
| `subreport/host-columns` | an inline subreport in the host's columns |
| `subreport/graft-in-columns` | a subreport's columns in the host's columns |
| `subreport/group-title` | an inline subreport on a group's title |
| `subreport/graft-beside-columns` | a subreport's columns beside a level's own |
| `subreport/graft-no-room` | a subreport's columns with too little room left |
| `subreport/summary-strip` | a subreport's summary in its columns' footer strip |
| `subreport/summary-strip-eject` | the same, and a page break after the summary |
| `subreport/column-number` | `COLUMN_NUMBER` around a subreport's columns |
| `subreport/pages` | a subreport with pages of its own |
| `subreport/ownpageno` | `ownpageno`, and a negative seq |
| `subreport/paper` | a template's own page geometry |
| `subreport/title-summary` | subreports on the report's title and summary |
| `subreport/split` | a host band that splits |
| `subreport/printwhen` | `printwhen`, answered once per placement |
| `subreport/eject-order` | a band's `eject` nodes and its subreports |
| `subreport/inputs` | what a subreport's `when`, `data` and `arg` read of the host |
| `subreport/parameters` | a subreport's parameters, and their defaults |
| `subreport/coercion` | a subreport's records, from values that arrive typed |
| `subreport/empty` | a subreport over an empty list and over null |
| `subreport/deferred` | deferred values inside subreports |
| `subreport/group-pages` | a group's page number across a subreport's pages |
| `subreport/at-a-break` | a subreport's band at a group break |
| `subreport/pages-at-a-break` | a subreport with pages of its own at a group break |
| `subreport/balance` | a subreport in a balanced block of columns |
| `subreport/balance-pages` | the same, with pages of its own |
| `subreport/nested` | subreports inside subreports |
| `subreport/names` | two templates' fonts, data and groups |
| `subreport/names-order` | who keeps a name two templates use |
| `subreport/names-reserved` | a subreport's declared `data` names, reserved |
| `subreport/font-suffix` | a suffixed font name another font is declared under |
| `subreport/blob-once` | when a template's `data` is produced |
| `outline/placement` | where an outline entry points, and where it is written |
| `outline/selection` | which outline node a band takes, and what its title says |
| `outline/split` | an outline entry in a band that splits |
| `outline/subreports` | outline entries in subreports |
| `outline/names` | which outline entries keep their names |
| `outline/levels` | outline levels with a gap |
| `outline/balance` | an outline entry in a balanced column |
| `outline/missing-target` | an xref whose outline entry never comes |
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
| `expressions/lambda` | the lambda both engines take, and what one prints as |
| `expressions/lambda-parameters` | the lambda parameters this engine leaves out |
| `expressions/function-repr` | what a predeclared function and a bound method print as |
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

M11 added the `image/` group and five `data/` probes, and with them
three defects in the reference and two decisions. In `cut-aligned`,
the reference keeps the top-left pixels of every clipped image,
whatever its alignment says; in `grow-valign`, it ignores `valign`
for a grow image shorter than its box, though it honours `halign`;
and in `data/unprinted`, it writes the blob of an image it built
for a footer's reservation and never drew. The first decision is
`no-pixels`: an image with no pixels along an axis is refused here,
and the reference builds it. The second is `type`: the reference
takes a `type` on an image and writes one on every image mark,
though it converts nothing, and here an image has neither.
The comparison drops the reference's mark `type`, so what differs
is the warning a `type` in the template gets here.

After M11 came one decision, in `printout/twin-fonts`. doc/printout.md#fonts
said "one entry per distinct font used", and the reference reads that as
merging two `font` nodes that resolve to one face at one size and style.
It writes one entry, under the name of the font built first, and every
mark set in either font names it. doc/ now keeps the two apart.

M13 added the `outline/` group and `printout/margins`, and with them
four defects in the reference, each a printout that breaks one of
its own invariants, and one decision. In `margins`, the reference
checks no mark against the page's margins, which doc/layout.md#errors
makes an overflow; in `missing-target`, it writes an xref to an outline
entry no entry carries; in `levels`, it writes each entry at the level
its template declared, gaps and all, where doc/ writes its depth in the
tree; and in `balance`, it leaves a balanced band's entry where the fill
put it. The decision is in `names`: doc/printout.md#outline has always
written an entry's `name` only where an xref targets it, and the
reference writes every name.

Five older probes put marks outside the margins without meaning to,
since the reference never said so: `layout/anchored-stretch`,
`layout/float-gaps`, `printout/numbers`, `rounding/negative`, and
`values/dimensions`. M13 moved each mark inside, by a band above it,
a wider page, or an xref to hold an offset that is still negative,
so that each asks its question as before, and each says how.

After M13 came `lambda`. `starlark-go` retired `resolve.AllowLambda`,
so the reference has taken a lambda all along, and this engine refused one
until doc/expressions.md#functions was written. The two agree on plain
parameters and their defaults, on what `type` and `repr` call a function,
and on a function held in a variable, which `expressions/lambda` records.
They do not agree on the rest of Starlark's parameter grammar: the reference
takes `*args`, `**kwargs`, a bare `*`, and `__debug__`, and this engine
refuses all four. That is `expressions/lambda-parameters` and the divergence
`lambda-parameter-forms`, the second of the two that go the way
`values/hex-float` does, built by the reference and refused here.

`lambda` made one older gap worth a probe of its own. Every callable a
template can reach is a Python object here, and Python writes a memory
address into the repr of a function and of a bound method, so a printout
that held one would not be reproducible. Nothing printed a callable until
a lambda could be written, and nothing caught it. `expressions/function-repr`
is that probe: predeclared functions, a method bound to each kind of
receiver, and a function inside a list, each named the way the reference
names it.

The CBOR encoding is compared over a part of the corpus, in
[test_cbor.py](../test_cbor.py), and agrees wherever the NDJSON does,
with one thing set right first: the reference writes the tables whose
keys are names, `data`, `groupRuns`, and `groupKeys`, in its hash map's
order, which changes from one run to the next, where doc/ sorts them.
A case cannot be registered as differing for a reason that comes and
goes, so the comparison sorts the reference's, and a test there fails
once the reference sorts them itself.

Of the rest, every one agrees byte for byte. The three `data/` probes that
waited for an image to be built were in [pending.toml](../pending.toml)
until M11 brought it, and `rounding/tolerance-refuses` until M8 brought
the page eject it needs.
