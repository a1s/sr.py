# Layout algorithm

How a [template](template.md) plus a data sequence become
a [printout](printout.md).

## Contents

- [Coordinates and rounding](#coordinates-and-rounding)
- [Text metrics](#text-metrics)
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

Comparisons against frame boundaries use a tolerance of 0.001 pt, so a band
whose height matches the remaining space exactly fits rather than ejecting.

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

Frames form a tree, each frame's geometry derived from its parent's.

### Construction

Built once, before any data is read:

1. **Page frame.** The page box inset by the four margins. Its `header` and
   `footer` are `layout`'s. Their measured heights are reserved from `top`
   and `bottom` — see [reservation](#headerfooter-reservation).
2. **Column frames.** A `columns` node creates a child frame with
   `width = (parent.width - (count - 1) × gap) / count`, carrying `count` and
   `gap`. If that `columns` node has its own header or footer, they attach to
   this frame and a further child frame holds the content.
3. **Group levels.** For each `group`, from outermost in, its `columns` node — if
   any — creates frames by the same rule. The group's `title` and `summary` bands
   belong to the frame that *contains* the group's columns, so a group title spans
   all columns.
4. **Detail frame.** The innermost frame holds the `detail` band.

`title` and `summary` at layout level belong to the innermost non-column frame,
unless `swapheader` / `swapfooter` is set, which moves them to the page frame so
they sit outside the page header and footer.

### Header/footer reservation

A frame reserves space for its header and footer by measuring them.
Both are measured against the context as it stands when the frame begins.

A footer is placed flush against the frame's reserved bottom band — including
a column footer. For content that should follow immediately below the last band,
use a group `summary`.

Deferred values inside a header or footer are sized from their placeholder
content; see [deferred evaluation](#deferred-evaluation).

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

- The `title` is placed at the top of the page frame on the first page, and that
  page's header is reserved below it. Content on page 1 starts that much lower.
- The `summary` is placed in the page frame's reserved bottom band on the last
  page, and that page's footer is placed immediately above it. The last page's
  content space is that much shorter.

Since the last page is only known when the record loop ends, the summary is
placed like any other band: if what remains above the enlarged bottom
reservation is already filled, a page eject happens first, and the summary gets a
fresh page carrying that page's header and footer.

## Building a band

Given a band template and a context, measurement proceeds:

1. **Test the band's `printwhen`.** If false the band is dropped — no marks,
   no height — and none of the steps below run.
2. **Resolve the band's style.** Walk `style` nodes in document order, innermost
   scope first: the band's own, then its `columns`, then each enclosing `group`,
   then `layout`. The first whose `when` is true supplies `font`, `color`, and
   `bgcolor`. Unset properties fall through to the next match in the same walk.
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
      - `field`: evaluate `expr` (or take `text` / `data`), apply `format`, wrap
        to the box width. With `stretch`, the box height grows to the wrapped text;
        without it, lines beyond the box are dropped at a line boundary.
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

   Those three participate in the maximum even with no vertical geometry given
   at all, which is why a band of barcodes and stretch fields sizes to them.
   A non-stretch `field`, a `line`, a `rectangle`, and an image that is
   not `grow` have no content height, so with a derived `bottom` they are
   container-dependent.

   If every element in a band is container-dependent and the band declares
   no height, the height is zero and all of them collapse.
   [Validation](template.md#validation) warns about that.
7. **Align content.** For each element, align its content box inside its resolved
   box per `halign` / `valign`; for a `field`, align each line per `align`.

The emitted mark's box is the content box from step 7, not the declared box.

## Floating elements

An element with `float=#true` has no fixed vertical position: it sits below
whatever lies above it, using measured heights rather than declared ones.

Resolution is a partial order, not declaration order:

1. Consider every element whose vertical extent is **its own** and non-negative:
   either a declared `height`, or a
   [content height](#building-a-band) the element determines itself.
   An element sized from the band's bottom edge does not participate.
2. Build the minimal DAG of "wholly above" relations from the **declared** boxes:
   - a non-floating element precedes a floating element it is wholly above;
   - a floating element precedes another floating element only when it starts
     earlier, which settles the case of a zero-height floater.

   Minimal means: if C is above both A and B, and B is above A, C depends on B
   only. Transitivity supplies the rest.
3. Walk the DAG in topological order, assigning each floating element
   `top = max(bottom edges of its predecessors) + gap`, where `gap` is that
   element's declared distance to the nearest predecessor.

Zero-height elements are skipped, so a suppressed field does not push its
followers down.

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

else:
    band cannot fit any frame → see Errors
```

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

`frame.height(empty)` is `bottom - top` for a fresh frame — the most a band could
ever get.

An eject a band triggers by not fitting is always a **column** eject. In a
single-column frame that is the same thing as a page eject, and in a multi-column
one it advances to the next column, escalating to a page eject only when no column
remains — see [which frames participate](#which-frames-participate). A band that
overflows its column should move to the next column, not skip the rest of the page.

An [`eject` node](template.md#eject) is the only way to force a page eject, via
`type="page"`.

After committing, `frame.fillY` advances by the band's height, and every descendant
frame's `top` moves down with it.

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

Given the frame of the band that triggered the eject:

- **Page eject**: that frame and every ancestor, up to and including the page
  frame.
- **Column eject**: that frame and ancestors, stopping at the first frame that
  still has an unused column. If none does, the walk reaches the page frame and the
  column eject becomes a page eject.

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
   - updates each group's `_PAGE_NUMBER`;
   - starts a new page, with every frame's `column` back to 0.
4. **Headers, outermost first** — the reverse of the footer order.

The two orders together are the order in which the bands appear on the page.

### `eject` nodes

A band's `eject` nodes are tested in document order. The first whose `when` is true
is selected, and the search stops there whether or not it ejects; a `when`-false
node is skipped and the next is tried. The selected node ejects unconditionally if
it has no `require`, and otherwise only when less than `require` remains in the
frame. Full table in [template.md](template.md#eject).

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
4. The frame is left filled to the deepest of the balanced columns, so that
   what follows starts immediately below them rather than at the bottom the
   ragged fill reached.

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

Before committing a group's `title`, the group's whole extent — title, every
detail, summary — is measured, accumulating until either the group ends or the
total exceeds an empty frame. If it fits an empty frame but not the space
remaining, an eject happens first.

Accumulation stops at the empty-frame height, which bounds the lookahead cost
regardless of group size: a group that cannot fit one frame cannot be kept
together.

Lookahead is available because the data sequence is fully buffered.

### `group minrows` and `mintailrows`

`minrows` is the minimum number of detail rows that must share a frame with the
group title. Before committing the title, it plus the next `minrows` details are
measured; if the total does not fit, an eject happens first.

`mintailrows` is the minimum that must share a frame with the group summary. When
the record loop reaches the point where `mintailrows` details plus the summary
remain, they are measured together; if they do not fit, an eject happens before the
first of them.

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
- `minrows`: the title plus the next `minrows` printed detail rows;
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
4. Reset variables whose scope just ended.
5. For each breaking group, **outermost first**: iterate variables for that scope,
   then place its `title`.
6. Iterate `detail`-scoped variables, then place the `detail` band.

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

A band suppressed by `printwhen` does not iterate `detail`-scoped variables, but
does advance `ITEM_NUMBER`.

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

A mark outside the printable area is the case a negative `right` or `bottom`
produces. Such an offset is legal in the template — it means the box reaches past
its container, which for a container in the middle of the page is perfectly
ordinary. Overflow is judged on the resulting page coordinates, not on the
declaration, so only a mark that actually crosses a margin is one.
