# Layout algorithm

How a [template](template.md) plus a data sequence become
a [printout](printout.md).

## Contents

- [Coordinates and rounding](#coordinates-and-rounding)
- [Text metrics](#text-metrics)
- [Line breaking](#line-breaking)
- [Measure, decide, commit](#measure-decide-commit)
- [Frames](#frames)
- [Building a band](#building-a-band)
- [Floating elements](#floating-elements)
- [Placing a band](#placing-a-band)
- [Band splitting](#band-splitting)
- [Ejects](#ejects)
- [Balanced columns](#balanced-columns)
- [Keeping content together](#keeping-content-together)
- [The record loop](#the-record-loop)
- [Deferred evaluation](#deferred-evaluation)
- [Subreports](#subreports)
- [Errors](#errors)

## Coordinates and rounding

Origin is the top-left of the page. X grows right, Y grows **down**.
All lengths are PostScript points.

Every computed coordinate and extent is rounded to 3 decimal places immediately
after the computation that produces it. This is normative: it decides whether
a band fits, so rounding only at output time gives different page breaks.

**Halves round away from zero.** 0.0005 becomes 0.001 and -0.0005 becomes
-0.001; the rule is symmetric about zero, so it is neither truncation
toward zero nor rounding toward positive infinity, and it is not the
round-half-to-even that several languages make their default.

That tie-break is shared with `quantize` and the
[`round` builtin](expressions.md#starlark-builtins), so a reader never has
to remember which of three rules applies where. Only the tie-break is shared:
those work on an exact decimal and on a float respectively, while a coordinate
is rounded by the binary64 arithmetic below, and reaching for `quantize` to
round a coordinate gives a different answer.

The arithmetic is normative too, because it is observable. Rounding is

```
round_half_away_from_zero(value * 1000) / 1000
```

evaluated in IEEE 754 binary64, and *not* rounding of the exact decimal the
source text spelled. The two differ. A `left` written as `0.1235` is held as
0.12349999999999999866…, which is below the half and would round down to 0.123;
multiplying by 1000 first gives exactly 123.5, which rounds to 0.124. 0.124 is
the answer, so the scaling comes first. An implementation that reaches for an
exact decimal type here disagrees with this one in the last digit, which is
enough to move a page break.

Comparisons against frame boundaries use a tolerance of 0.001 pt, so a band
whose height matches the remaining space exactly fits rather than ejecting.
Both sides of such a comparison are already rounded, so the tolerance absorbs
one unit in the last place rather than an accumulated error.

The tolerance is added in binary64 like everything else here, and the test is

```
extent <= limit + 0.001
```

which is not the same as comparing thousandths as integers, because adding
0.001 to a three-decimal value does not always reach the next one. `20.003 +
0.001` is exactly 20.004, so a band of 20.004 pt fits a remaining 20.003 pt;
`1.001 + 0.001` is 1.0019999999999998, so a band of 1.002 pt does **not** fit
a remaining 1.001 pt and the page breaks.

Which way a value falls depends on its binade, not on its size in any smooth
way. Across the 842 pt of an A4 page the addition falls short for 22.5% of
three-decimal values, so an implementation comparing thousandths instead
would disagree about roughly one exact fit in five — but the rate is 49.6%
in [1, 2), 41.6% in [256, 512), and exactly zero throughout [4, 8), [16, 32)
and [64, 256). That is why the rule has to be the arithmetic rather than the
intent: "a tolerance of one unit in the last place" describes what it is for,
and this expression is what decides where the page breaks.

## Text metrics

One measurement rule governs every band's height, so both halves of it are
normative.

**Advance means `hmtx`.** The engine sums per-glyph advances from the font's own
table and does not shape, so it does not kern. A renderer must be told the same,
rather than left on its default: kerning is sparse and content-dependent — a
kerning-heavy line measures several points narrower under shaping than the
printout says, while an ordinary sentence measures identically — so a renderer
that kerns disagrees with the document exactly where it is hardest to notice.

**Leading is 1.2 times the font size.** A constant multiplier is predictable and
font-independent, which is what a paginating engine needs: line spacing must not
change when a typeface is substituted, because that changes how many lines fit
and therefore where every page after it breaks.

A character the resolved font lacks is measured and drawn as `.notdef`, which is
a visible empty box, and recorded as a
[warning](template.md#missing-glyphs). Metrics are unaffected, so nothing shifts.

## Line breaking

A text mark's [`lines`](printout.md#text) array is part of the printout,
and how many entries it has decides the height of a stretch field, the height
of its band, and therefore where the page breaks. So this is normative to the
character, and two engines that wrap differently agree about nothing further
down the document.

What is wrapped is the string the element finally holds: `expr`, `text`
or `data` resolved, and `format` applied. Where a character came from
makes no difference to how it is treated.

### Mandatory breaks

**U+000A LINE FEED ends a line**, whether or not the box needed a break there.
It is consumed, appearing on neither side. Nothing else does this: U+000D,
U+000B, U+000C, U+0085 and U+2028 are ordinary characters, so a CRLF leaves
its carriage return at the end of the line before it.

A paragraph that is empty stays a line: text beginning with a newline starts
with an empty line, text ending with one ends with an empty line, and a run
of newlines gives a run of empty lines. `lines` is never empty, so text that
is empty or reduces to nothing is one empty line rather than none.

### Break opportunities

Within a paragraph the only break opportunities are **U+0020 SPACE and
U+0009 TAB**. No other character is one — not a hyphen, a soft hyphen,
a solidus or an em dash, and not a no-break space, a zero-width space,
an em space, a figure space or an ideographic space. Nothing happens
between CJK characters either. Those all measure and draw normally,
and a line may be cut in the middle of a run of them only by the rule
for overlong runs below.

That this list is two characters long rather than a Unicode line-breaking
algorithm is a choice, and the cost of it is that a language which does
not separate its words with spaces does not wrap at its own boundaries.

### Fitting

Take the paragraph as a sequence of **chunks**, each one a maximal run
of characters that are neither space nor tab, together with the run of
spaces and tabs immediately after it. Either run may be empty, which is
what makes a paragraph's leading whitespace a chunk of its own — an empty
word and the spaces after it — so that it lands at the start of the first
line. Fill each line with whole chunks while they fit, then break.

The whitespace inside a chunk counts toward the fit. That is the part worth
stating outright, because it is where a plausible implementation goes wrong:
a line takes another word only when that word **and the whitespace before
the next one** still fit. `"xxxx xxxx"` set in Go-Regular at size 10 measures
42.778 pt, and 45.557 pt counting the space after it; in a box between those
two widths, a line takes one word rather than two when more text follows,
and both words when nothing does.

**A line's width is accumulated, and rounded at every step.** Start at zero;
for each chunk added, add that chunk's own width and round the running total
to 3 decimals by the rule under [Coordinates and rounding](#coordinates-and-rounding).
The line still fits while that total is no more than **the limit**: the box's
own rounded width plus the usual 0.001 pt tolerance.

Measuring the candidate line as one string instead, and rounding once at the
end, is a different rule and gives different documents. In a box of 72.280 pt —
limit 72.281 — `"q q q q q q q q q"` at size 10 measures 72.2802734375 pt, which
rounds to 72.280 and is within the limit; accumulated chunk by chunk it reaches
72.282 pt, which is not. The engine breaks it, so the accumulation is normative
rather than an implementation detail — as the rounding rule it follows from
already implies, since a line's width is a computed extent like any other.

Each chunk contributes **its own width, rounded once** — not the total of a
walk through its codepoints, which can differ by a unit in the last place. The
two granularities are separate and neither borrows the other's arithmetic.

### Overlong runs

A chunk too wide for the space left on the current line moves to a line of its
own. Whether it fits there is settled by **walking it**, codepoint by codepoint:
start at zero, add each codepoint's advance, round the running total at every
one, and stop at the last codepoint that keeps the total within the limit —
except that **the walk always takes its first codepoint**, whether or not
that one fits. That exception is what keeps wrapping terminating, and it is
why content overflows a box narrower than a single character rather than
wrapping forever.

The walk is both the test and the cut, and what decides between them is **how
much of the chunk the walk consumed**, not whether the chunk fits. Consuming
all of it means the chunk starts the line; stopping short means the codepoints
taken are a line of their own, and what is left is walked again.

Those two are not the same test, and a box narrower than one codepoint is
where they come apart: there the forced first codepoint may be the whole
of what remains, so the walk consumed the chunk while nothing about it fits.
It starts the line. Being the line in progress rather than an emitted cut,
it is then trimmed at the end of the paragraph like any other — which is
why `"Wa"` followed by a tab, in a box of 6.89 pt, ends `["W", "a", ""]`
and not `["W", "a", "<tab>"]`.

That the walk is what decides takes a worked example, because the two
measurements are never far apart. A box of 11.122 pt has a **limit**
of 11.123 pt — the box rounded, plus the tolerance. `"qq"` at size 10
is 11.123 pt measured as one string, which is within that limit, and
11.124 pt walked, which is not. The engine puts one `q` on the line,
so the walk is the figure it consulted.

The walked total is used for nothing else. A chunk that fits contributes
its own rounded width to the line, as above, and the walk is forgotten.

What a cut leaves behind starts the next line, and wrapping goes on from there
as usual: the remainder is walked again, cut again if the walk still stops
short, and otherwise joined by the chunks after it in the ordinary way.

The unit is the **codepoint**: not the byte, not the UTF-16 code unit, and not
the grapheme cluster. A cut may therefore fall between a letter and a combining
mark that follows it. Cutting by grapheme cluster would be kinder and is not
what this does.

### Whitespace

A break at a break opportunity consumes the whole run of spaces and tabs
it falls at, so the run survives on neither line. Whitespace anywhere else
is kept as it is: leading whitespace stays at the start of a line, and
whitespace inside a line is neither collapsed nor trimmed.

**Trimming follows from that, and only from that.** A line ended at a break
opportunity loses its trailing run of spaces and tabs, because the break
consumed them, and so does the last line of a paragraph. Trimming stops
at the first character that is neither space nor tab, so a trailing no-break
space stays and takes any whitespace before it with it.

**A line ended by a cut is not trimmed**, because a cut consumes nothing:
it falls between two codepoints, wherever they happen to be, and whitespace
it happens to end on is inside the line rather than at a break. So a box
too narrow for a word can yield a line that is a single space, kept — while
the same whitespace reaching the end of a line the ordinary way is removed,
and a line that was nothing but a consumed run is left empty.

Because trimming happens before the line reaches the printout, `align="right"`
and `align="center"` measure the line without that whitespace, while leading
whitespace still counts.

### A box of zero width

A box whose width is zero is **not wrapped**. Zero is the width a box has
when nothing determined one, and one codepoint per line is not a useful
reading of that. Mandatory breaks still apply, because they do not consult
a width. Any positive width wraps, including one narrower than a single
character.

## Measure, decide, commit

Every band goes through three steps:

```
measure(band, context) → Measurement       pure: no mutation, no emission
decide(measurement, frame)                 fits / split / eject / error
commit(measurement, frame)                 emit marks, advance, update variables
```

**Measure** resolves styles, evaluates expressions, wraps text, runs the floating
solver, and computes the band's height and its marks at band-relative coordinates.
It mutates nothing: no variable folds, no counters, no output.

**Decide** compares the measured height against the space remaining in the frame
and chooses one of: commit as-is, split, eject and re-measure, or fail.

**Commit** translates the marks to page coordinates, appends them to the page,
advances the frame's fill position, and applies variable iteration.

Band splitting, keep-together, orphan and widow control, measured header
reservation, and deferred evaluation are all consequences of this separation.

### Cost

A band is measured once when it fits, twice when an eject intervenes, and up to
`minrows + 1` times when a group is keeping content together. Measurement is the
expensive step because it wraps text.

Measurements are cached against resolved content and available width,
so re-measuring after an eject at the same width is free.

A band whose expressions read `VERTICAL_POSITION` or `VERTICAL_SPACE` is
not cached. Its content depends on where in the frame it lands, which the
cache key does not capture.

## Frames

A **frame** is a rectangular region that bands fill from the top down.

| Field | Meaning |
|---|---|
| `x`, `width` | Horizontal extent of the current column. |
| `top`, `bottom` | Vertical extent available to content, after header and footer reservation. |
| `columnCount`, `columnGap` | 1 and 0 for a non-column frame. |
| `column` | Current 0-based column index. |
| `header`, `footer` | Bands reserved at the frame's top and bottom. |
| `parent`, `children` | Tree links. |
| `fillY` | Where the next band goes. |
| `floor` | The lowest edge a band placed in an ancestor reaches on this page. |

Frames form a tree, each frame's geometry derived from its parent's.
A template nests one group per level, so the tree is a chain:
a frame has at most one child.

### Construction

Built once, before any data is read:

1. **Page frame.** The page box inset by the four margins. Its `header` and
   `footer` are `layout`'s. Their measured heights are reserved from `top`
   and `bottom` — see [reservation](#headerfooter-reservation).
2. **Column frames.** A `columns` node creates a child frame with
   `width = (parent.width - (count - 1) × gap) / count`, carrying `count` and
   `gap`. If that `columns` node has its own header or footer, they attach to
   this frame and are reserved out of each of its columns.
3. **Group levels.** For each `group`, from outermost in, its `columns` node — if
   any — creates frames by the same rule. The group's `title` and `summary` bands
   belong to the frame that *contains* the group's columns, so a group title spans
   all columns.
4. **Detail frame.** The innermost frame holds the `detail` band.

`title` and `summary` at layout level belong to the page frame, which is the
frame that contains the layout's own columns. `swapheader` / `swapfooter`
moves them outside the page header and footer as well; see
[below](#swapheader-and-swapfooter).

### Extent and fill

A frame's **extent** is fixed for the length of a column. Its `top` is its
parent's `top` with its own header reserved below that, and its `bottom`
its parent's `bottom` with its own footer reserved above that; the page
frame's are the margins'. So a column frame reaches down to the page
frame's `bottom`, a column footer sits directly above the page footer's
reservation, and `VERTICAL_POSITION` is measured from a `top` that does
not move while the column is being filled.

Where the next band goes is the frame's **fill**, and a committed band
moves three sets of fills:

- **Its own frame's**, which advances by the band's height.
- **Every ancestor's**, which comes down to at least the band's bottom edge.
  A column lies inside its parent's current column, so a band in a column
  is in the parent's way too. A band placed in the parent afterwards --
  a group summary under a group's columns, a report summary under the
  report's -- therefore goes below the **deepest** column, not below
  the one that happened to be filling last.
- **Every descendant's**, which comes down to at least the band's bottom
  edge too, since the band spans all of their columns. The descendant
  keeps that edge as its **floor**, and a column it opens later on the
  same page begins at its `top` or at its floor, whichever is lower.
  So a column opened after a group title begins below the title, beside
  the column opened before it, rather than on top of it.

A header and a footer take no fill of their own: they are drawn at the edges
of the column. They do come in the way of the frames above, which is why a
report title placed after the first page's column headers goes below them,
and why a column that has had its footer placed counts as filled to its
bottom: a band placed across the columns after that one has ended needs
the next page.

A column is **empty** when its fill is as high as a column of its frame
can begin. That is the frame's `top`, or, in a frame with columns inside it,
the lowest edge their headers were drawn to: every column it opens draws
them again, and a band placed across them goes below. Where each header
took what it reserved, that edge is the `top` of the innermost of them.
No column of the frame gives a band more room. A column that begins at its
floor is not empty, even with nothing placed in it: the next column on the
page begins as low, but the next page begins at the top. Nor is a column
below a [swapped title](#swapheader-and-swapfooter) on the first page,
 since the next page's content begins higher by the title's height.

A band in a frame with columns inside it stops above their footers,
at the innermost column's `bottom` rather than the frame's own.
Those footers are drawn when their columns end, flush with the bottom,
and a band placed across the columns before then would be under them.
`VERTICAL_SPACE` for such a band is measured to there.

A column eject resets the frames inside the ejecting one to their
first column; a page eject resets every frame, and every floor.

### Header/footer reservation

A frame reserves space for its header and footer by measuring them.
Both are measured against the context as it stands when the frame begins,
and a frame begins **each time one of its columns opens**: the page frame
on every page, a column frame on every page and at every column eject.
So a header whose content changes from page to page reserves a different
height on each, and the columns of one page may start at different heights.

So each of the two is measured **twice**: once to find out how much space
to reserve, when the column opens, and once when the band is built onto
the page. The two measurements can disagree, since the footer is built when
the column ends and a `printwhen` or a stretch field may answer differently
by then. Where they do, the reservation is what the frame was inset by
and the second measurement is what is drawn. On the first page the
reservation is measured before any record has been read.

A footer is placed flush against the frame's reserved bottom band — including
a column footer. For content that should follow immediately below the last band,
use a group `summary`.

**Flush means the frame's bottom edge, not the reservation's.** The two are
the same place whenever the two measurements agree, and where they do not,
this is what keeps the band on the page: a footer guarded by
`printwhen="THIS != None"` -- the guard [below](#what-a-header-or-footer-sees)
recommends -- prints nothing at reservation, because there is no record yet,
and reserves nothing; at the end of the page it prints, and its bottom edge
is the frame's. A footer that measured taller than its reservation therefore
grows upward, into the content, rather than off the paper.

Deferred values inside a header or footer are sized from their placeholder
content; see [deferred evaluation](#deferred-evaluation).

#### What the two counters report

`VERTICAL_POSITION` and `VERTICAL_SPACE` describe the frame a band is being
tried against, and for these two bands that frame is not the one the content
fills:

| | `VERTICAL_POSITION` | `VERTICAL_SPACE` |
|---|---|---|
| header | 0 | the frame less the **footer's** reservation |
| footer | how far the content frame was filled | the strip reserved for the footer |

A header is at the top of the page by construction, so its position is zero,
and the space below it is everything the page has left once the footer is out --
its own reservation is not subtracted, because the header is what is being
measured. A footer is the other way about: it is drawn at a fixed place,
so what is worth reporting is where the content stopped, and the space
it has to grow into is the band held for it rather than the page.

That the header's figure has one reservation taken out and not the other
is worth stating because it is the only asymmetric thing here, and it
follows from what each number is for: a band is told what it may grow
into, and a band never has to make room for itself.

### What a header or footer sees

A footer is built against the **outgoing** context, a header against
the **incoming** one — before and after the advance in the [eject
sequence](#sequence) respectively. So a page footer reports the page it sits on,
and a page header the page it opens.

In both, the names resolve as they do anywhere else. Specifically:

- `THIS` is the last record that entered the [record loop](#the-record-loop),
  and the record field names read from it.
- Counters hold their current values: end-of-page values in a page footer,
  and the reset values in the next page's header.
- Variables hold their accumulated values. In a footer that is before the reset
  for the scope just ended, so a `reset="page"` total is that page's.
- A group's own names hold
  [unspecified](expressions.md#between-a-groups-runs) values between its
  runs, and the last page's footers come after every group's last run.

**At a group break** an eject can end the page, or the column, that the
runs which ended are on, and begin the one the new runs start on. That is
so while nothing of the new record has been placed: an eject a keep-together
rule causes as a group opens, one of its title's `eject` nodes, the title
not fitting, and for a group whose title does not print, the first detail
band not fitting or one of its `eject` nodes. Such an eject's footers are
built against the context the break's summaries were built in: `THIS` and
`ITEM_NUMBER` are the previous record's, every group of the break reads
as its ended run left it, and every variable holds what it held then.
Its headers are built against the new record, with every group of the
break at the start of its new run: `_COUNT` at 0, `_PAGE_NUMBER` at 1,
and the variables it resets reset. As in the headers of any eject a band
causes, the band's own fold is not in them yet: a group's `iter="group"`
fold goes with its title, and is counted on the page the title lands on.

Once a band of the new record has been placed, an outer group's title for
one, an eject is built as any other, and a group of the break that has not
opened yet is [between its runs](expressions.md#between-a-groups-runs) in
its footers.

Before the first record — on a page filled by a tall `title`, or in a report with
no data at all — `THIS` is `None` and reading a record field from it is an error.
A header or footer that names a record field must therefore guard for it:

```kdl
field expr="region" printwhen="THIS != None"
```

### `swapheader` and `swapfooter`

`swapheader` on `title` and `swapfooter` on `summary` attach the band to the page
frame, outside the page header and footer. The space comes out of the page it
lands on:

- The `title` is placed at the top of the page frame on the first page,
  and that page's header is reserved below it. Content on page 1 starts
  that much lower, so no column below it is [empty](#extent-and-fill):
  a band that fits a page without the title goes to page 2 rather than
  overflowing on page 1.
- The `summary` is placed in the page frame's reserved bottom band on
  the last page, and that page's footer is placed immediately above it.
  The last page's content space is that much shorter.

Since the last page is only known when the record loop ends, the summary is
placed like any other band: if what remains above the enlarged bottom
reservation is already filled, a page eject happens first, and the summary gets a
fresh page carrying that page's header and footer. What remains is measured
above the column footers as well, since they move up with the page footer.

A swapped title taller than the page frame, or a swapped summary taller
than an empty page offers, is an [overflow](#errors). Where that is allowed,
it is placed where it would go unswapped, at the top of the page frame,
and the summary after one page eject, as any band that fits nowhere is.

Where it fits, the page frame's bottom moves up by the summary's height before
the last page's footers are placed, so the page footer and every column footer
are placed flush against the new bottom, above the summary. The page footer's
`VERTICAL_POSITION` then counts the summary as filled: it is measured to the
summary's bottom edge.

## Building a band

Given a band template and a context, measurement proceeds:

1. **Test the band's `printwhen`.** If false the band is dropped — no marks,
   no height — and none of the steps below run.
2. **Resolve the band's style.** Walk `style` nodes in document order, innermost
   scope first: the band's own, then its `columns`, then each enclosing `group`,
   then `layout`. The first whose `when` is true supplies `font`, `color`, and
   `bgcolor`. Unset properties fall through to the next match in the same walk.
   The walk is one sequence, each scope's nodes in document order, and the
   next match may be a later node of the same scope: a `layout` whose first
   `style` sets a `font` and a `color` and whose second sets a `bgcolor`
   gives a band with no styles of its own all three.
3. **Resolve the band's geometry** against the frame, per
   [the two-of-three rule](template.md#position-and-size-any-two-of-three).
   An explicit `height` is known here; `height="auto"` is settled at step 6.
4. **For each element, in document order:**
   1. **Test its `printwhen`.** If false the element is dropped and the
      remaining sub-steps are skipped. It contributes no marks and no height,
      so a suppressed element neither shows nor pushes its followers down.
   2. **Resolve its style**, by the same outward walk as step 2 but starting
      at the element's own `style` children.
   3. **Resolve its geometry** within the band, applying `maxwidth` / `maxheight`
      clamps. In a band whose height is still unsettled, an extent that depends on
      the band's bottom edge is left for step 6.
   4. **Resolve its content:**
      - `field`: evaluate `expr` (or take `text` / `data`), apply `format`,
        wrap to the box width by the rule in [Line breaking](#line-breaking).
        With `stretch`, the box height grows to the wrapped text;
        without it, lines beyond the box are dropped at a line boundary.
        A stretch field that is [container-dependent](#building-a-band)
        is the exception: its box is the one the band gives it in step 6
        and does not grow, and its text keeps every line and runs past
        that box, placed by `valign` like any content taller than its box.
        A `maxheight` on a stretch field, whether or not the field is
        container-dependent and whether or not the clamp is what made
        its box short, drops the lines beyond the box as they are dropped
        without `stretch`.
      - `image`: decode and sniff the type, then apply `scale`. `cut` draws the
        image at natural size clipped to the box, and the retained region becomes
        the mark's `crop`. `fill` scales the image to the box: with `proportional=#true`
        the aspect ratio is preserved, so it is scaled to fit within the box
        and positioned by `halign` / `valign`, and with `#false` it is stretched
        to the box exactly. `grow` expands the box wherever the image exceeds it,
        so the image is drawn at natural size, neither scaled nor clipped.
        Only `fill` scales, so `proportional` is consulted only for `fill`;
        only `cut` produces a `crop`.
      - `barcode`: encode, obtaining stripe widths and a minimum symbol size;
        the box grows along the coding direction to at least that minimum.
      - `xref`: recurse — an xref is a container of elements and is measured as one.
        Its own box comes from its geometry alone and never grows to what it
        holds. An xref has no content height, so it is container-dependent,
        and waits for step 6, unless it declares a `height` and no `bottom`.
        Its children are then built against that box as a band's elements
        are built against the band, except that the height the
        container-dependent among them resolve against is the xref's own
        rather than a maximum they take part in. The xref's `halign` and
        `valign` move nothing: each child is placed by its own geometry
        and its own alignment.

        What an xref holds still takes room from the band, in the second
        maximum of step 6 and not the first. A child whose vertical extent
        is its own reaches as far as its box, whether or not its mark does;
        a container-dependent child reaches as far as its mark; a nested
        xref reaches as far as its own contents do. So a stretch field that
        runs past its xref pushes the next band down, and so does a field
        declared taller than the xref that holds it, while a rule beside
        the xref spans only the xref.
      - A field or barcode with `evaltime` is measured from its placeholder content
        and [registered](#deferred-evaluation).
5. **Resolve floating elements.** See [below](#floating-elements).
6. **Determine band height.** The band is as tall as the greater of its declared
   `height` and the lowest bottom edge any element produced. A declared `height`
   is a **minimum, not a cap**: content that needs more room gets it, and the band
   grows. `height="auto"` is the same rule with a minimum of zero.

   An element whose vertical extent is **container-dependent** takes no part in
   that maximum. It is resolved afterwards, against the height the other elements
   produced. Container-dependent means the element is anchored to the band's
   bottom edge: either its `bottom` was derived and it has no height of its own,
   or it declared a `bottom` outright.

   An element has a height of its own — a **content height** — when it is

   - a `field` with `stretch=#true`: the wrapped text's height;
   - a `barcode`: the symbol's minimum height;
   - an `image` with `scale="grow"`: the bitmap's natural height.

   Those three participate in the maximum even with no vertical geometry
   given at all, which is why a band of barcodes and stretch fields sizes
   to them. A non-stretch `field`, a `line`, a `rectangle`, an image that
   is not `grow`, and an `xref` have no content height, so with a derived
   `bottom` they are container-dependent.

   If every element in a band is container-dependent and the band declares
   no height, the height is zero and all of them collapse.
   [Validation](template.md#validation) warns about that.
7. **Align content.** For each element, align its content box inside its resolved
   box per `halign` / `valign`; for a `field`, align each line per `align`.

The emitted mark's box is the content box from step 7, not the declared box.

### The band's height is settled twice

Step 6 reads as one maximum and is two, and the difference shows in every band
that holds a rule. The first is over the **declared** boxes and is what the
container-dependent elements are resolved against; the second is over the
**marks**, and is the band's height.

1. Take the greater of the declared `height` and the lowest bottom edge among
   the elements whose vertical extent is their own.
2. Resolve the container-dependent elements against that. A `line` written
   `top=10` and nothing else ends up exactly there, and one written with no
   geometry at all spans it.
3. The band is then as tall as the greatest bottom edge of the marks
   [step 7](#building-a-band) produced, or that first height, whichever is
   greater.

Only step 3 sees content that overflowed the box it was given: a `field`
without `stretch` in a box shorter than one line still draws that line, and a
container-dependent field whose box came out empty draws its line below the
top edge. So a band whose only content is such a field grows to the text,
while a rule inside it keeps the height the declared boxes gave.

The second maximum cannot feed back into step 2, and that is not a
simplification for its own sake: resolving the rule against the final height
would make the two define each other, which is the reason step 6 excludes
container-dependent elements from the first maximum at all.

## Floating elements

An element with `float=#true` has no fixed vertical position: it sits below
whatever lies above it, using measured heights rather than declared ones.

Resolution is a partial order, not declaration order:

1. Consider every element that prints and whose vertical extent
   is **its own**: either a declared `height`, or a
   [content height](#building-a-band) the element determines itself.
   An element sized from the band's bottom edge does not participate,
   and neither does one its `printwhen` suppressed, since it is not there
   at all. An element of zero height does participate.
2. Build the DAG from the **declared** boxes. An element's declared box
   runs from its `top` down by its declared `height`, or by nothing where
   it declared none: a stretch field given only a `top` has a declared box
   of no height at that top. The declared height is the one written, before
   `maxheight` clamps it.
   - A non-floating element precedes a floating element it is **wholly
     above**: its declared bottom edge is no lower than the floating
     element's declared top.
   - A floating element precedes another floating element when it
     **starts earlier**: its declared top is strictly higher. That is the
     whole test between two floating elements, so one precedes another
     whose declared box it overlaps, and two that start level precede
     neither way, which settles the case of a zero-height floater.

   Only the vertical axis is read. An element at the far side of the band
   is above one at the near side all the same.
3. Walk the DAG in topological order, assigning each floating element
   `top = max(bottom edges of its predecessors) + gap`. The bottom edges
   are the predecessors' as they now stand, floated, grown, and clamped.
   `gap` is the element's declared distance to the **nearest element
   wholly above it** -- the one whose declared bottom is lowest -- among
   those step 1 considers. Where none is, it is the distance to the band's
   top edge, or to the highest declared top among those elements where one
   starts above that edge. A floating element with no predecessor at all
   keeps its declared top.

The gap is measured to what is wholly above because a distance to an element
the box overlaps is not a distance. Where only an overlapping floating
element precedes, the gap is therefore measured from the band's top edge
and added below that element, which moves the follower a long way down:
a template that overlaps two floating boxes is usually one to fix.

The element the gap is measured to need not precede. The one case where
it does not is a floating element with no declared height that starts level
with this one: its declared box is wholly above, at a distance of nothing,
but it does not start earlier. The gap is then zero, and the element sits
directly on its predecessors.

Both sides of the gap are declared, and what it is added to is not:
under a `maxheight`, the gap below an element keeps the distance from
its declared bottom and is added to its clamped one.

Nothing in the rule depends on the floating element's own height. A rule
of zero height floated 2 pt under a paragraph stays 2 pt under it when
the paragraph grows.

A suppressed element is not there to precede anything, so it does not push
its followers down, and their gaps are measured past it to whatever is above.
A non-floating element of zero height, such as a rule, is there, and a gap
below it is measured from it.

### What a floating element may be

A floating element **may** `stretch`, and a floating `barcode` or `scale="grow"`
image is fine too. Content is resolved in step 4, before this pass, so by the time
the DAG is walked a stretch field's wrapped height, a barcode's symbol height,
and a grow image's natural height are all known. Step 1 asks for a height
the element owns, not for a declared one.

What a floating element may **not** do is take its height from the band — a derived
`bottom` with no content height of its own. Its top is not known until this pass
has finished, and the band's height is not known until step 6, so such an element
has neither end fixed. It is excluded here for the same reason it is excluded from
the band's height: the two would define each other.
[Validation](template.md#validation) refuses one.

A floating element that **declares** a `bottom` is another matter. With a height
of its own, a declared `height` or a content height, it loads, but a declared
`bottom` makes an element [container-dependent](#building-a-band) whatever its
content, so it is resolved against the band in step 6 like any other and takes
no part in this pass. Its `float=#true` has no effect. A stretch field of that
kind keeps the box the band gives it, as any container-dependent stretch field
does.

The DAG itself is built from **declared** boxes, so it depends on the template and
not on the data. Measured heights are used to propagate positions along it, but they
never change which element precedes which — the same template floats things in the
same order for every record.

## Placing a band

```
measurement := measure(band, context)
available   := frame.bottom - frame.fillY

if measurement.height <= available + TOLERANCE:
    commit(measurement)

else if band splits and a legal split point fits `available`:
    split there, commit the head, column eject, continue with the tail

else if measurement.height <= frame.height(empty):
    column eject, re-measure, commit

else if band splits and any cut point fits `available`:
    # taller than an empty frame: every split preference is given up
    split at the last such cut point, commit the head, column eject, continue

else if no eject has moved the band yet:
    # a later page may offer more: its headers may take less
    column eject, re-measure, start again from the top

else:
    band cannot fit any frame → see Errors
```

`frame.bottom` is where a band in the frame must stop, which in
a frame with columns inside it is above their footers: see
[extent and fill](#extent-and-fill).

Each branch is tried against the frame **as it stands**, and
the fourth is no exception: a band too tall for any frame is cut here,
at the last cut point that fits what this column has left, and not after
an eject has found it an emptier one. A band is only ever moved whole
when it would fit an empty column, when it fits nowhere and has not been
moved yet, or when it overflows.

A **cut point** is an offset no mark's span falls through; a **legal split point**
is a cut point that also divides content and satisfies `orphans` and `widows`.
Both are defined in [legal split points](#legal-split-points).

The two differ only in the last-resort branch: a band too tall for any frame is split
wherever it can be cut at all, because making progress beats honouring a preference.
A band too tall for any frame in which *no* cut point exists — an image or a barcode
taller than the frame — is an error either way.

Branch order matters. A cut that leaves one side blank is excluded by requirement 2
precisely so that branch 2 declines it and branch 3 ejects the band whole, which is
the better outcome. Without that, branch 2 would win by being tried first.

`frame.height(empty)` is the room an [empty](#extent-and-fill) column
offers, from where it begins down to `bottom`: the most a band could get.
It is taken on the page the band is tried on, since a page's headers are
measured when it opens and a later page's may take more or less. The one
difference known in advance is a swapped title, which only the first page
has, and on that page the height is taken as if the title were not there.

Only the fifth branch moves a band whole out of an empty column. A band
that fails the first branch there is taller than `frame.height(empty)`,
so the third does not take it, and the fourth cuts it where it is.
One that cannot be cut is carried to the next page once, where the headers
may leave it more room: an eject from an empty column looks for room, and
the next column on the same page offers none. Where it still fits nowhere,
it overflows. A column that is not empty, including one that begins
at its floor, ejects under the third branch until the band fits or
the column is empty, which is on the next page at the latest.

That is why the empty height is measured below the headers of the columns
inside the frame, as they were drawn, and above their footers. Measured
from the frame's own `top`, it would count room that no page offers a
band across the columns, and so would a header that was drawn taller than
it reserved, measured from its reservation. A band in the page frame that
fits the frame, but not the room below the column headers, would then be
ejected from page to page for ever.

An eject a band triggers by not fitting is always a **column** eject.
In a single-column frame that is the same thing as a page eject, and
in a multi-column one it advances to the next column, escalating to a
page eject only when no column remains, or none that would offer the band
more room -- see [which frames participate](#which-frames-participate).
A band that overflows its column should move to the next column, not skip
the rest of the page.

An [`eject` node](template.md#eject) is the only way to force a page eject,
via `type="page"`.

After committing, `frame.fillY` advances by the band's height, and the frames
above and below it move as [extent and fill](#extent-and-fill) describes.

## Band splitting

A band with `split=#true` may break across frames.

### Legal split points

Three requirements. A **cut point** satisfies the first;
a **legal split point** satisfies all three.

#### 1. The cut must not fall through a mark

A **cut point** is a band-relative offset *y* such that,
for every element that produced marks:

- the element's vertical span does not strictly contain *y*, **or**
- the element is a `stretch` field and *y* falls on one of its line boundaries.

An element's **vertical span is the span of the marks it produced** — its
[content box](#building-a-band), not its resolved box. What cannot be cut is
what is drawn; empty space inside a box that content did not fill has nothing
in it to divide. This is also the only reading the printout can express,
since a mark's box *is* the content box.

The distinction decides ordinary bands rather than exotic ones. A non-stretch `field`
with no vertical geometry is [container-dependent](#building-a-band): its resolved box
fills the band, but it draws one line, at the top or wherever `valign` puts it. Under
the resolved-box reading it would strictly contain every interior offset and no band
holding such a field could ever split, which would make `split=#true` inert in most
templates that set it. Under this one it spans its line, and cuts below that line
are legal.

Barcodes, images, and band-spanning rectangles block a cut anywhere inside their span.
A `rectangle` has no natural size to shrink to — its content is its box — so one given
`bottom=0` genuinely does span the band and genuinely does block. A `stretch` field
permits cuts between its lines.

#### 2. The cut must divide content

A cut point with all the band's marks on **one side** of it is not a legal split
point. Splitting there would move whitespace to another frame and nothing else.

This is not an edge case, because a band's declared `height` is a
[minimum](#building-a-band) and so a band is routinely taller than what is in it.
A 13 mm detail row whose four fields each draw one line is 36.85 pt of band around 11 pt
of text, and **every** offset below the text's bottom edge is a cut point: no mark span
contains it, and `orphans` and `widows` hold vacuously because no line boundary is
being crossed. With 20 pt of room left, the greatest such offset is 20 — so without
this requirement the band would split there, the head taking all four fields and the
tail carrying 16.85 pt of blank onto the next frame, pushing everything after it down.

Ejecting the band whole is better in every case of that shape, and it is what the
third branch of [placing a band](#placing-a-band) does once this requirement takes
the cut out of consideration.

The rule is symmetric: a cut whose **head** would be empty is excluded for the same
reason, since that only relocates a blank strip. A split divides content or it does
not happen.

#### 3. `orphans` and `widows` must hold

At least `orphans` lines remain above the cut and at least `widows` below it, per
field. A field with fewer than `orphans + widows` lines permits no internal cut and
blocks like an unsplittable element.

### Splitting

The greatest legal *y* not exceeding the available space is chosen. The head
commits: elements wholly above *y* as-is, split fields with their leading lines and
`lastLineJustified: true` so the renderer does not treat a continued line as a
paragraph end. Then a column eject, and the tail continues with the remaining
lines, its elements re-placed from the tail's top.

Non-splittable elements below the cut move to the tail whole.

The tail is carried over the eject rather than measured again: it is already
wrapped, and measuring it a second time would ask its expressions a second
question. So its marks were built against the column the band started in, and
are moved horizontally to the column it continues in as they are committed --
the columns of a frame are all one width, so there is nothing else to change.

A band that does not fit even an empty frame is split at the last available **cut
point**, giving up `orphans`, `widows`, and the requirement that both sides carry
marks — see [placing a band](#placing-a-band). If it has no cut point at all, it is
an [error](#errors).

## Ejects

An eject ends the current page or column and starts the next.

### Which frames participate

Given the frame of the band that triggered the eject, the walk finds
the **ejecting frame**:

- **Page eject**: the page frame.
- **Column eject**: that frame and ancestors, stopping at the first frame that
  still has an unused column. If none does, the walk reaches the page frame and the
  column eject becomes a page eject.

A column eject that is looking for room also walks past a frame whose next
column would offer the band no more than it has. In that column the band
begins at the frame's floor, or where it began in this one, whichever is
lower, so it gains nothing where its column is filled no lower than that:
a column that opened below a band placed across it, and has had nothing
placed in it since, or an empty one. Every eject looks for room
except one an [`eject` node](#eject-nodes) without `require` asks for,
which is an instruction and goes to the next column whatever that offers.
A band that does not fit, a keep-together rule, and a `require` that is not
met all want the room, and a column that begins no higher would not give it.

The ejecting frame participates, and so does **every frame inside it**,
since the columns they are filling end with it. For a band in the innermost
frame that is the same set as the walk passed through; for a band in an outer
frame -- a group summary placed across the columns of its group -- it includes
the columns below the band as well, so each of them has its footer placed
before the page ends and its header placed when the next one begins.

### Sequence

1. **Footers, innermost first.** Each participating frame's footer is built and
   placed flush at its reserved bottom band. Footers are built against the
   **outgoing** context, so a page footer reports the page it belongs to.
2. **Resolve deferred values** for the scopes that just ended — `column` for a
   column eject, `page` and `column` for a page eject.
3. **Advance.** A column eject:
   - resets `COLUMN_COUNT`;
   - applies `column`-scoped variable resets, then `column`-scoped iterations;
   - increments the ejecting frame's `column`, sets its `x`
     to `parent.x + (width + gap) × column`, and resets
     every descendant frame to column 0.

   A page eject ends a column as well, so it does all of the above and then:
   - increments `PAGE_NUMBER` and resets `PAGE_COUNT`;
   - applies `page`-scoped variable resets, then `page`-scoped iterations;
   - adds one to each group's `_PAGE_NUMBER`, except a group that is still
     opening;
   - starts a new page, with every frame's `column` back to 0.
4. **Headers, outermost first**: the reverse of the footer order.
   Each participating frame's next column opens: its header and footer
   are reserved and its header is placed at its top.

The two orders together are the order in which the bands appear on the page.

A group is **opening** from its break, where the record loop starts its
new run, until a band of that run has been placed: its title, or for
a group whose title does not print, the first band inside it. An eject
in that time -- one of the title's `eject` nodes, a
[keep-together](#keeping-content-together) rule, or the title not fitting --
moves the group to the page that band lands on rather than making that page
its second, so `_PAGE_NUMBER` is 1 wherever a run's first band is.

### `eject` nodes

A band's `eject` nodes are tested in document order. The first whose `when` is true
is selected, and the search stops there whether or not it ejects; a `when`-false
node is skipped and the next is tried. The selected node ejects unconditionally if
it has no `require`, and otherwise only when less than `require` remains in the
frame and the column is not [empty](#extent-and-fill), since no eject could
give it more room. Full table in [template.md](template.md#eject). A column
eject with no `require` goes to the next column; one with `require` is
looking for room, and passes over a column that would offer none, as
[which frames participate](#which-frames-participate) says.

A band that does not print tests none of its `eject` nodes, whatever
their `when`: `printwhen` suppresses the band with what it would have asked
of the page. A group whose title does not print therefore opens without them.

Ejects are evaluated **before** the band is placed, with one exception: a report's
own `title` — the band at `layout` or `embedded` level — evaluates them **after**,
so an `eject` there gives the title a page of its own. A group's `title` is not the
exception; its ejects run first, which is what lets one say "open this group
somewhere it has room".

## Balanced columns

A frame fills each column before starting the next, so content that stops
part way down the last one leaves it short: twenty rows on the left and two
on the right. `columns balance=#true` spreads that run of bands over the columns
so that they end at similar heights.

What balances is the **fragment**: what the frame was given since the current
page opened. Every page balances as it ends -- at the page break, before the
footers are placed, and at the end of the report before the summary -- so a
frame that prints on several pages is not laid out one way on the page a group
happens to end on and another way on the page before it. A page the content
filled is even already, and comes out of the pass unchanged.

### What it does

1. Each column of the fragment begins where its first band was placed. Columns
   the fragment never reached begin at the frame's `top`, and are open to it
   only when neither the frame nor anything under it has a header or a footer:
   those are placed as a column opens, measured against the context of that
   moment, and balancing has no such moment to place one in afterwards.
2. The fill is reproduced by packing the bands into the same columns to the same
   bottom. If that does not put every band exactly where it went -- a different
   column, or the same one at a different height -- then something other than
   the room left decided it, and the fragment is left alone.
3. The shallowest bottom the same bands still reach in those columns is found by
   bisection, and every band is moved to the column and position it is assigned.
   The bisection runs over whole thousandths of a point, which is the grid
   every coordinate is on, so what it finds is the smallest bottom that
   grid has, and packing to it gives the assignment.
4. The frame is left filled to the deepest of the balanced columns, so that
   what follows starts immediately below them rather than at the bottom the
   ragged fill reached. It stays in the column the fill left it in, which is
   what `COLUMN_NUMBER` reads after the pass.

A column the fragment never reached begins at the frame's `top`, or at
its [floor](#extent-and-fill) where a band placed across the columns
earlier on the page reaches lower.

Nothing is measured or evaluated a second time: the columns are the same width,
so moving a band is a translation of the marks already built. An expression
that read its own position -- `COLUMN_NUMBER`, `VERTICAL_SPACE` -- was answered
where the band was first placed and keeps that answer. Marks keep the order
they were painted in.

### What is left where the fill put it

The whole fragment stays exactly as it was placed when any of these happens
in it:

- **An `eject` node, or an eject that keeps a group together**, that stays
  on the page. The band was moved for a reason that packing by height would
  not reproduce. An eject that starts a new page decides nothing about the
  fragment it starts, so that one leaves it alone.
- **A band split.** Its two halves belong at the column edge they were cut on.
- **A subreport.** Its bands are the child engine's rather than a band of the
  host's, and the frame has no way to carry them along when it moves one.
- **A `column` deferral.** It is resolved when the column ends, against
  the column it ended in.
- **A band placed outside the frame after the fragment's first one.**
  It interleaves with the columns and would be left behind by anything that moved.
  A group `summary` outside that group's own `columns` block is the usual case.
- **Another `columns` block inside it.** The inner block fills side by side
  rather than one band after another, so what reaches the outer frame is not
  in the order the page reads it. The band belongs to the innermost balanced
  frame above it, and every balanced frame outside that one is left alone --
  it holds only what sits between the inner block's runs.

## Keeping content together

Three mechanisms, from coarsest to finest. They compose by taking the maximum:
see [how they combine](#how-they-combine).

### `group keeptogether`

Before committing a group's `title`, the group's whole extent is measured:
its title, every detail, its summary, and the titles and summaries of the
groups inside it. It accumulates until either the group ends or the total
exceeds an empty frame. If it does not fit the space remaining, an eject
happens first.

Accumulation stops at the empty-frame height, which bounds the lookahead cost
regardless of group size. A group that cannot fit one frame cannot be kept
together, and what it asks for is then an empty frame: it starts at the top
of an empty column, where it gets as much of itself onto one frame as any
frame could hold.

Lookahead is available because the data sequence is fully buffered.
It measures what the record loop would place, folding variables and
counting rows as the loop would, and then puts all of that back:
nothing it measures is committed, and nothing it folds is kept.

### `group minrows` and `mintailrows`

`minrows` is the minimum number of detail rows that must share a frame with the
group title. Before committing the title, it plus the next `minrows` details are
measured, with any nested group titles and summaries among them; if the total
does not fit, an eject happens first. Like `keeptogether`, what it asks for is
capped at an empty frame, and a group with fewer rows asks for all of them.

`mintailrows` is the minimum that must share a frame with the group summary.
When the record loop reaches the point where `mintailrows` details plus the
summary remain, they are measured together; if they do not fit, an eject
happens before the first of them. The point is the first printed row from
which no more than `mintailrows` printed rows remain in the group, so a group
shorter than `mintailrows` is tested at its first row, and each opening of a
group is tested once. What is measured runs from that row through the group's
summary, nested summaries included, and is capped at an empty frame like the
rest.

The default for both is 1, which is not a no-op: a title is never left at
the bottom of a frame without a row after it, and a summary never starts
a frame without a row before it.

No keep-together rule ejects from an [empty](#extent-and-fill) column,
and nor does an `eject require` on any band. An eject there could offer
no more room, and would leave a blank page behind. An `eject require`
taller than an empty frame asks for more than any eject could give, and
what the others ask for is capped at an empty frame. A column that begins
at its floor is not empty, and a rule it does not satisfy ejects from it as
from any other. The columns left on that page begin below the same band and
offer no more room, so the eject passes over them to the next page: see
[which frames participate](#which-frames-participate).

Both are counted in rows, distinct from a band's line-counted `orphans` and
`widows`. **Rows means printed rows**: a record whose `detail` is suppressed
by `printwhen` occupies no space, so the lookahead skips it and counts on to
the next one that prints. A group whose remaining records all suppress can
satisfy neither count and neither forces an eject.

### `eject require`

Ejects when less than a given amount of space remains — "start this band with at
least this much room". Combine it with `when` on the same node to restrict it to the
records where it matters; see [`eject`](template.md#eject).

### How they combine

More than one may apply to the same group — invoices commonly set `keeptogether`
along with `minrows`. They are not applied in turn. Before the group's `title`
is committed, each mechanism that applies contributes the height it wants available:

- `keeptogether`: the whole group's extent, capped at an empty frame's height;
- `minrows`: the title plus the next `minrows` printed detail rows,
  capped likewise;
- `eject require` on the title: the `require` dimension,
  if a `when` selected that node.

The largest of those is compared against the space remaining, and **at most one
eject results** — the mechanisms decide whether to eject, not how many times.
`mintailrows` is separate because it is tested later in the record loop,
at the group's tail rather than its head.

**Which kind of eject.** Take the maximum on that axis too. A page eject is the
stronger of the two, and only a selected `eject` node can ask for one — `keeptogether`
and `minrows` are about fitting a *frame*, which is a column, so they ask for a column
eject exactly as a band that does not fit does. So if a selected `eject` node says
`type="page"`, the eject is a page eject, whichever contributor demanded the most
space.

Escalating costs nothing, which is what makes the rule safe: a new page starts
at column 0 with a full frame, so it offers a column's worth of room at least.
Deciding the kind from *which contributor happened to be largest* was the alternative,
and it would make the kind of break depend on the data — a group ejecting to a column
on one run and to a page on the next.

## The record loop

For each record, in this order:

1. `THIS` and `ITEM_NUMBER` advance.
2. Evaluate every group's `expr`, outermost first. The outermost that changed
   determines the break level; every group nested inside it breaks too.
3. For each breaking group, **innermost first**: place its `summary`, built against
   the **previous** record's context, so a group summary can print its own total.
   The previous record's context is the whole of it: `THIS` and `ITEM_NUMBER`
   are the previous record's while the summaries are placed, including in
   the footers and headers of any eject a summary causes.
4. Reset variables whose scope just ended, and every breaking group's
   `_COUNT` to 0 and its `_PAGE_NUMBER` to 1. Each breaking group's new
   run begins here, and is **opening** until a band of it is placed.
5. For each breaking group, **outermost first**: the group opens --
   its group-scoped variables iterate -- then its `title` is placed.
   What a group's own names read between its break and its opening,
   in an outer group's summary or title, is
   [unspecified](expressions.md#between-a-groups-runs). An eject in
   steps 5 and 6 before anything of the new record has been placed
   is built on both sides of the break; see
   [what a header or footer sees](#what-a-header-or-footer-sees).
6. Iterate `item`-scoped variables; then, where the `detail` band prints,
   `detail`-scoped variables; then place the `detail` band.

Full variable semantics are in
[expressions.md](expressions.md#variables).

### Records must be ordered by group key

A group breaks when its `expr` changes between **adjacent** records. It does not
collect records with equal keys from across the sequence, so the input must arrive
with each group's records contiguous — normally by sorting in the query that
produced the data. Grouping then costs one pass and no per-group buffering.

Unsorted input is not an error: a repeating key is legal, since a report may group
by a value such as weekday. It produces a group that opens more than once. The
printout header records the number of distinct group runs alongside the number of
distinct keys, so the discrepancy is visible.

### Rollback

A detail band that is measured, folded into variables, and then found not to fit
has its fold rolled back before the eject and reapplied after, so no value is
counted twice. Step 6 iterates variables before placing because the band's content
usually depends on them.

The same holds for every eject a band causes before it is committed:
one of its `eject` nodes, a [keep-together](#keeping-content-together)
rule, or its not fitting. The fold is undone, so the footers of the page
it leaves do not count it; the eject resets what it resets; and the fold
is applied again, evaluated afresh, so the band counts on the page it
lands on. A `reset="page"` total therefore neither counts a row twice
nor loses one to the reset in between.

A group's title is a band like any other here, and its fold is the
group-scoped iteration of step 5. A title that moves to the next page
takes its group's iteration with it, as a detail band takes its own.

A band that is split is committed, head first, and nothing is rolled back:
its fold belongs to the page its head is on.

A band suppressed by `printwhen` does not iterate `detail`-scoped variables, but
does advance `ITEM_NUMBER` and iterates `item`-scoped ones.

### The end of the report

After the last record, every group still open is closed, innermost first:
its summary is placed against the last record's context, as step 3 places
one at a break. Then the fragment of any balanced frame is balanced, the
report's `summary` is placed, and the last page's footers are placed,
innermost first.

### Report structure

```
title
  [page header, first page]
  for each record: group titles / detail / group summaries
  [page footer, last page]
summary
```

`swapheader` on `title` places it above the first page header; `swapfooter` on
`summary` places it below the last page footer. Both attach the band to the page
frame instead of the inner frame — see
[`swapheader` and `swapfooter`](#swapheader-and-swapfooter) for where the space
comes from.

## Deferred evaluation

A `field` or `barcode` with `evaltime` is not evaluated when its band is built. It
is registered against the named scope; when that scope ends, the real value is
computed and substituted. This is how a page footer prints the final page count.

`evaltime` names the scope. What the expression takes from the end of that scope,
rather than from where it sits, is [`FINAL`](expressions.md#final) — so the two
always appear together.

### What a deferred expression sees

Two rules, and they cover every case:

- Every name reads its value **where the element sits**, exactly as it would
  with no `evaltime` at all.
- [`FINAL.`*name*](expressions.md#final) reads the value that name holds
  when the `evaltime` scope **ends**.

So the author says which half of the expression is deferred, name by name.
The engine keeps no list of which quantities a given scope makes final.

```kdl
field expr="'Page %d of %d' % (PAGE_NUMBER, FINAL.PAGE_NUMBER)" \
      evaltime="report" text="Page 999 of 999"
```

`PAGE_NUMBER` is the page the field is printed on. `FINAL.PAGE_NUMBER` is
what `PAGE_NUMBER` has become by the end of the report. One expression, one field,
and nothing in the engine that knows what a page total is.

### How the substitution works

When the band is measured, the engine evaluates nothing. It **snapshots** the value
of every name the expression references except `FINAL`, which it does not bind yet.
It knows those names from [compilation](expressions.md#compilation), which has
already turned each expression into a function of exactly the names it uses — so
the snapshot costs a lookup per name and no analysis.

When the scope ends, `FINAL` is bound to the values reached at that moment
and the function is called. Everything else it sees is the snapshot.

That is why the snapshot is per element rather than per band: two fields
in one footer may sit at the same place but name different things.

### When a scope ends

| `evaltime` | Resolved |
|---|---|
| `column` | at each column eject, and at the end of the report |
| `page` | at each page eject, and at the end of the report |
| *group* | when that group breaks, after its `summary` is committed, and at the end of the report |
| `report` | after the `summary` band is committed |

The trailing "and at the end of the report" covers the last page, last column,
and last group, which end without an eject or a break.

A group's deferrals resolve after its `summary`, so both read the same final group
totals. Report-scoped variables are not reset until after the `summary` is
committed, for the same reason — see
[the report boundary](expressions.md#the-report-boundary).

### Placeholders

A placeholder is **required** exactly when the deferred element's own size depends
on its content: a `field` with `stretch=#true`, or any `barcode`. In both cases the
box grows to fit, so the space it will need cannot be known from the geometry alone.

Everywhere else the resolved box is content-independent — geometry resolution always
produces a complete box — so there is nothing to reserve and the placeholder is
optional.

The placeholder is the element's `text` or `data`, and the band is measured from it.
Validation rejects a deferred element that needs one and has none. See
[content sources](template.md#content-sources).

### Re-measurement

After substitution the element is re-measured:

- **Height unchanged or smaller** — accepted. The box shrinks to the resolved
  value, a barcode's on both axes, and the element's `halign` and `valign`
  re-anchor it inside the room the placeholder reserved, so the whitespace
  falls on the side the alignment does not name.
- **Height larger** — **error**, naming the field, its placeholder, and both
  heights.

So a placeholder must be sized for the worst case: `text="Page 999 of 999"`, 
not `text="Page 1 of 1"`.

## Subreports

A `subreport` runs another template over a nested sequence. It is a nested builder
with its own context: its own parameters, fed by `arg` nodes, its own
[records](template.md#records), its own variables and groups, and its own record
loop.

An [`embedded`](template.md#embedded) layout is written inside the report it
belongs to and shares that report's fonts, data blobs, base directory and page;
its style search continues outward into the enclosing `layout`, because the
search walks outward through the document and the layout is where it is written.
A layout named by `template=` is a separate document with fonts, data and a base
directory of its own, and its style search ends at its own `layout` -- the same
rule applied to a different tree. Either way the printout carries one font table
and one data table for the whole document, so a face two templates both resolve
is measured and embedded once, and a name two templates give to different things
is published under distinct names.

- **Non-inline** (default): the child builds complete pages, which are spliced into
  the parent's page list at the point the subreport occurs. `ownpageno` restarts
  page numbering inside the child; otherwise numbering continues from the parent
  and resumes after it.
- **Inline**: the child's bands are placed into the parent's current frame,
  continuing the parent's pagination. An inline subreport must match the parent's
  page size and inherits its margins. `inline` and `ownpageno` are mutually
  exclusive.

A subreport belongs on a `detail` band, on a `title` without `swapheader`, or on
a `summary` without `swapfooter`. Nowhere else: a subreport takes frame space of
its own, and every other band is placed outside the ordinary fill of the frame
it belongs to. A `header` and a `footer` -- the frame's own or a `columns`
block's -- are measured and reserved before the page they bound is filled, and
a swapped band sits beyond that reservation. Which frame the host band belongs to
does not come into it: a band inside a `columns` block carries a subreport like
any other, and the subreport's bands fill the column and eject to the next one
with it.

Nesting is bounded at 32. A subreport may name the layout it is written in,
which is how a template walks a tree, and the data normally ends that walk;
nothing guarantees it does, so the recursion stops with an error naming the node
rather than running out of stack.

### Where a subreport's bands go

A subreport is not laid out inside its host band's box. A band is a fixed region;
a subreport emits whole bands of its own. `seq` orders it against the host band
as a whole:

- `seq` negative — the subreport's bands are placed into the frame **before**
  the host band, so the host band follows them.
- `seq` non-negative — the host band is committed first and the subreport's bands
  follow it, starting at the frame's new `fillY`.

Ties break on document order. Either way the subreport consumes frame space
of its own; it takes nothing from the host band's height, and the host band's
height is unaffected by it.

When the host band splits, the subreport goes outside the whole split,
not between the fragments: `seq` negative places it before the head,
`seq` non-negative after the tail.

**A host band suppressed by `printwhen` runs none of its subreports.**
The subreport hangs off the band; an invoice that does not print has
no line items to print either.

The band's `printwhen` is answered **once per placement**, before any of its
subreports run, and that one answer decides the band, the subreports before it
and the subreports after it. It has to be one answer: a negative-seq subreport
runs between the question and the band's own measurement and may eject, so a
condition reading `VERTICAL_POSITION`, `VERTICAL_SPACE`, `PAGE_NUMBER` or
`PAGE_COUNT` would answer differently at each point. The frame position
it sees is therefore the one before the subreport's bands were placed.

The same answer stands across the retries inside one placement, so a band
cannot appear or vanish part way through being placed. A header, a footer
and the [keep-together lookahead](#keeping-content-together) each ask on
their own, being measurements rather than placements.

A negative-seq subreport runs after the host band's own variables have folded,
so both sides of the band read the same values. It is committed before the host
band is measured, so if the host band then turns out not to fit and ejects,
the subreport's bands stay where they were placed and the host band follows
them onto the next page -- which is what "the host band follows them" means.

### Pages

**Inline.** The child prints in the frame the host band belongs to, from
the host's current fill position. It shares the host's `PAGE_NUMBER` and
`COLUMN_NUMBER`, and an eject inside it is the host's eject: the host's
footers are placed, the host's page-scoped variables reset, and the host's
header is reserved on the next page, all alongside the child's own. An inline
subreport therefore has no page of its own to attach a header or a footer to,
and defines none.

It may open a `columns` block. That reserves a frame inside the host's rather
than one of its own, so it is grafted onto the host frame for the length of the
invocation and removed again at the end of it. The frames begin where the host
is filled -- the space above belongs to the host -- and stay there for the rest
of that page, so a column the child opens on the page it started on begins
beside the first, not above it. A page break puts them back at the top of
the host's frame. The child's own `columns` header and footer are placed and
measured in the child's context, which is live for as long as the frames are.

**Its own pages.** The host's current page is closed -- footers placed, page
and column deferrals resolved -- the child builds complete pages, and the host
resumes on a fresh one. The child's pages land in the printout between the two,
which is the splice: everything the host has built is already before that point.
So a subreport that paginates itself always ends the host's page, whether or not
that page was full.

The child may run at a page size and margins of its own. Its pages carry
whatever differs from the document's own geometry, which is what the
printout's [per-page overrides](printout.md#page-lines) are for.

Without `ownpageno` the child continues the host's numbering and the host
resumes after it: a host on page 3 whose subreport takes three pages resumes
on page 7. With `ownpageno` the child numbers from 1 and the host's numbering
is untouched, so the host resumes on page 4.

### What the lookahead does not see

[Keep-together and `minrows`](#keeping-content-together) measure the host's own
bands. A subreport's bands are not measured in advance: the child is a nested
builder over a sequence the host has not evaluated yet, and running it to find
out how tall it is would mean running it twice.

So a group whose detail rows carry subreports is kept together against the
height of the rows alone. It is an estimate, and it is the only place in the
engine where one is left: everything the host itself contributes is measured.

### Names and values

The child's parameters are bound from the `arg` nodes, which are evaluated in
the **host's** context before the child has one, and type-checked against the
declaration rather than parsed for it. A parameter with neither an `arg` nor a
default is a load-time error: a subreport has no command line to fall back on.

The `data` expression is evaluated in the host's context too, and must yield
a sequence. Its elements are coerced by the child's own
[`records`](template.md#records), exactly as the input file is coerced
by the report's -- a `list` member arrives from JSON untyped, and that
declaration is what turns its fields into ints, decimals and times.

Every deferral a child registered resolves when its invocation ends. For a child
with its own pages that is the ordinary end-of-report resolution. For an inline
one it is earlier than the host's page ends, and deliberately so: its `report`
scope ends with the invocation, and its `page` and `column` scopes belong to a
host whose end it cannot see, so once the invocation is over nothing it could
still contribute is outstanding. A deferred value that has to read the host's
final page state belongs on a host band. An eject that happens *during* the
invocation does resolve the child's page and column deferrals along with the
host's, because there both are printing in the scope that ended.

### Page headers and footers

Only a paginating report has them. A **non-inline** subreport builds its own pages
and so uses its own `header` and `footer`. An **inline** subreport shares the
parent's pages, whose header and footer are already reserved, so it has no page
of its own to attach them to: an inline subreport must not define `header`,
`footer`, a `swapheader` title or a `swapfooter` summary, and doing so is a
[validation error](template.md#validation).

For a heading that prints once per invocation — column labels above a line-item
table, say — use `title` and `summary`, which are per-report bands and work in
both modes. Under `inline` they print once at the start and end of each
invocation, in the parent's frame. For one that repeats down the invocation,
put a `header` on a `columns` block of the child's own -- a block of one column
is a frame with nothing else to it -- and it is re-placed on every page and
column the invocation reaches.

## Errors

Each of these names the template node, the record index, and the measured values.

| Condition | Behaviour |
|---|---|
| Band taller than an empty frame, splitting not allowed | **overflow** |
| Band taller than an empty frame, splitting allowed, some cut point exists | split there, giving up every split preference |
| Band taller than an empty frame, splitting allowed, no cut point exists | **overflow** |
| A mark lands outside the page's printable area | **overflow** |
| Deferred value taller than its placeholder | error |
| Header and footer reservations together exceed the frame | error |
| Barcode content not encodable in the selected type | error |
| Expression type mismatch, missing field, or a `null` in a member that is not `nullable` | error |
| A value a `calc="sum"` accumulator cannot add to its running total | error |
| `FINAL` without `evaltime`, or `evaltime` without `FINAL` | error, at template validation |
| Column count so high that column width is non-positive | error, at template validation |
| A subreport's `data` yields something that is not a sequence | error |
| An `arg` value whose type is not the parameter's | error |
| Subreports nested more than 32 deep | error |
| A subreport parameter with no `arg` and no default | error, at template validation |
| A template that reaches itself through a subreport | error, at template load |

The rows marked **overflow** are errors that `--allow-overflow` downgrades to a
warning, placing the marks anyway. The warning is recorded in the printout header,
so an overflowing document is identifiable from the artifact.

An oversized band is first carried by one eject, unless an eject has
moved it already, since a later page may leave it more room: see
[placing a band](#placing-a-band). Where it still fits nowhere, it is placed
at the top of an [empty](#extent-and-fill) column: where it is if that column
is empty, and otherwise after as many column ejects as it takes to reach one,
which is the next page at the latest. It runs past that column's bottom,
and the band after it starts the next column.
The warning is raised once for each band, before those ejects, and carries
the record that band was being placed for, the first record's 0 included.

A mark outside the printable area is the case a negative `right` or `bottom`
produces. Such an offset is legal in the template — it means the box reaches past
its container, which for a container in the middle of the page is perfectly
ordinary. Overflow is judged on the resulting page coordinates, not on the
declaration, so only a mark that actually crosses a margin is one.
