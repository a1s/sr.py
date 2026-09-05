# PDF rendering

A **renderer** turns a [printout](printout.md) into a document a person can
look at. This one writes PDF.

It does no layout. Every box in a printout is absolute, every string already
wrapped, every barcode already encoded, every font already resolved to a file,
so rendering is a translation of marks into drawing operators. A renderer that
re-wraps text, re-fits an image, or re-encodes a barcode is in violation of the
printout format, not merely inefficient.

What is left to a renderer is what a printout deliberately does not say, and
this document specifies those decisions so that two runs, and two readers, agree.

## Contents

- [What the renderer decides](#what-the-renderer-decides)
- [Coordinates](#coordinates)
- [Text](#text)
- [Lines and rectangles](#lines-and-rectangles)
- [Images](#images)
- [Barcodes](#barcodes)
- [Outline and links](#outline-and-links)
- [Fonts in the file](#fonts-in-the-file)
- [Document metadata](#document-metadata)
- [File structure](#file-structure)
- [Reproducibility](#reproducibility)
- [Errors](#errors)
- [Boundaries](#boundaries)

## What the renderer decides

Four things, and nothing else:

| | |
|---|---|
| Where a baseline sits inside the leading a text mark reserved | [Text](#text) |
| How a justified line spends its slack | [Justification](#justification) |
| What a `dash` enumeration measures | [Lines and rectangles](#lines-and-rectangles) |
| Where an underline sits and how thick it is | [Underline](#underline) |

Everything else is read out of the printout.

## Coordinates

The printout's origin is the top left of the page and Y grows down. PDF's is
the bottom left and Y grows up. The renderer flips each Y coordinate as it
writes it -- `y' = pageHeight − y` -- rather than concatenating a mirroring
matrix at the top of the page, because a matrix that mirrors the page mirrors
the glyphs with it.

A page's size is its own `width` and `height` when it states them, and the
header's `page` defaults otherwise. It becomes the page's `MediaBox`. Margins
are not written: a printout's marks are already placed, and a `MediaBox` is all
a reader needs.

Lengths reach the file with at most three decimal places, which is the
printout's own precision. Trailing zeros are dropped and a negative zero
is written `0`.

## Text

A [`text` mark](printout.md#text) carries a box, a font, a colour,
an alignment, a leading, and its lines already wrapped.
Line *n* of the mark sits on the baseline

```
baseline(n) = box.y + (leading − (ascender + descender)) / 2
                    + ascender + n × leading
```

where `ascender` and `descender` come from the resolved face's `hhea` table,
scaled to the font size, the descender counted positive downward.

`hhea`, and not OS/2's `sTypoAscender` and `sTypoDescender`. A face may set
`USE_TYPO_METRICS` to ask readers to prefer that second pair, and shaping
libraries honour it, but the [font descriptor](#fonts-in-the-file) this renderer
writes carries the `hhea` values — so a reader that reflows or re-measures the
text has only those. One table answers both, and it is this one.

That is: the face's own em extent is centred in the leading the printout
reserved, and the baseline measured down from the top of it. Two properties
follow, and both are the point of the rule.

- **Leading is not touched.** It is a constant multiple of the size
  ([1.2](layout.md#text-metrics)), fixed by the engine because it decides
  pagination. The renderer positions inside it and never adjusts it.
- **A face taller than its leading overhangs evenly.** `ascender + descender`
  exceeds `1.2 × size` in many faces. Centring puts half the excess above the
  slot and half below, rather than pushing the whole overhang to one end where
  it would collide with one neighbour and leave a gap at the other.

The horizontal position depends on `align`, measured from the line's width
in the same face the engine measured it with:

| `align` | Line starts at |
|---|---|
| `left` | `box.x` |
| `center` | `box.x + (box.width - width) / 2` |
| `right` | `box.x + box.width - width` |
| `justified` | `box.x`, and see below |

Measuring the line again is not re-layout: the printout fixes *which*
characters are on the line, and a renderer needs the width only to place
the line it was given. It must measure the way the engine does --
[`hmtx` advances, no kerning, no shaping](layout.md#text-metrics) -- or a
right-aligned column drifts. A renderer built on a text stack that shapes
by default has to be told not to.

Glyphs are painted in the mark's `color` as a non-stroking colour.

### Justification

A justified line is cut into **segments** at its internal runs of whitespace.
A segment is one word together with the whitespace that follows it, so the
spaces are drawn as part of the text and remain in what a reader extracts.
Leading whitespace stays attached to the first segment rather than becoming
a gap of its own, so an indent does not stretch.

The slack is `box.width` minus the sum of the segments' widths, divided evenly
among the *n - 1* joins. Each segment is positioned by an exact displacement
from the one before, so a segment's position does not depend on the glyph
advances that precede it, and the last segment ends on the box's far edge.

The slack is measured against the sum of the segments rather than against the
whole line because the segments are what is drawn, each is measured and
[rounded](layout.md#coordinates-and-rounding) on its own, and it is their sum
that has to reach the edge.

A line is justified when `align` is `justified` and either it is not
the last line of the mark, or it is and
[`lastLineJustified`](printout.md#text) is set -- which is how a
[split band](layout.md#band-splitting) says that the paragraph continues
on the next frame. The last line of a finished paragraph is set flush left,
which is what justified setting means everywhere.

A line with no room to grow, or with no join to grow at, is set flush left
rather than stretched.

### Underline

When the [font entry](printout.md#fonts) says `underline`, each line gets
a filled rule under it: its top `underlinePosition` below the baseline and
`underlineThickness` tall, both from the face's `post` table scaled to the
size, in the text's own colour. A face that states neither gets a twentieth
of an em thick, a tenth of an em down.

The rule spans the drawn line -- the box's full width for a justified line,
the line's measured width otherwise. A line that is only whitespace gets none.

## Lines and rectangles

A [`line` mark](printout.md#line) is stroked from its box's top-left corner
to its bottom-right, or bottom-left to top-right when `backslant` is set.

A [`rectangle` mark](printout.md#rectangle) is stroked when it carries a
`stroke` and filled when it carries a `fill`, independently; a `radius` turns
the corners into quarter-circle Bézier curves, clamped to half the shorter
side. A rectangle with neither a stroke nor a fill draws **nothing** --
that is what the template's `stroke=#false opaque=#false` resolves to,
and it is not the same as a zero width.

`width` is passed through as the PDF stroke width, `0` included: PDF defines
a zero width as the thinnest line the output device renders, which is exactly
what the format's [hairline](template.md#rectangle) means. It is not turned
into a width of the renderer's choosing.

Dash patterns are in points, and absolute rather than a multiple of the stroke
width, so a hairline and a two-point rule dash on the same rhythm:

| `dash` | Pattern |
|---|---|
| `solid` | — |
| `dot` | 1 on, 2 off |
| `dash` | 3 on, 2 off |
| `dashdot` | 3 on, 2 off, 1 on, 2 off |

## Images

The printout has already resolved the template's `scale` and `proportional`:
the [`box`](printout.md#image) is the drawn rectangle and `crop`, when there
is one, names the source pixels that fill it. So there is no fitting to do.

- With no `crop`, the image is mapped onto the box.
- With a `crop`, the scale is `box.width / crop.width` horizontally and
  `box.height / crop.height` vertically; the whole image is placed at that
  scale, offset so that the crop's corner lands on the box's, and the box
  is set as a clipping path. Only `scale="cut"` produces a crop, and it
  arrives at scale 1 -- a point per pixel.

An image's bytes come from the header's [`data`](printout.md#data) table,
or from the `file` the mark names, [resolved against the printout's own
directory](printout.md#paths). Two marks reading one source share one
embedded object.

A baseline JPEG is embedded as it stands, keeping its own compression. Every
other format is decoded and re-encoded as eight-bit samples -- grey where the
source is grey, RGB otherwise -- with a soft mask when any pixel is not opaque.
Progressive and CMYK JPEGs take the decoding road too: the first is not
universally supported by readers, and the second needs an inverted decode
array that is easy to get wrong.

## Barcodes

A [`barcode` mark](printout.md#barcode) carries its geometry in modules.
The renderer draws filled rectangles and encodes nothing.

Both run arrays start with a **light** run, so index 0 is light, index 1 dark,
and so on. Nothing records where the alternation starts because nothing has to.
Where a symbol genuinely opens dark -- possible only for a symbology that asks
for no quiet zone -- the array opens with a **zero-length light run**.

- **1-D**: the `stripes` alternate space and bar along the coding direction,
  starting with the leading quiet zone, each spanning the whole extent
  across it. The coding direction is rightward, or downward when `vertical`
  is set.
- **2-D**: each row of `rows` alternates light and dark along the coding
  direction, starting with light, and each row is one module deep across it.
  The quiet zone is part of the geometry: the outermost rows are wholly
  light, and every other row opens and closes light.
  With `vertical` set the symbol is turned a **quarter turn clockwise**:
  the coding direction runs down the page and the rows advance leftward
  from the box's right edge.

Bars are painted in `ink`, which is always present and is `#000000` unless
the template asked otherwise. When `paper` is present it is filled over the
whole box **first**, quiet zones included, and the bars go on top of it; when
it is absent nothing is laid down and whatever is underneath shows through.

A renderer that prints in one colour -- a label printer driver, say --
may ignore both and print the symbol in the only ink it has.

## Outline and links

[`outline` marks](printout.md#outline) become the document outline.
Their `level` builds the tree: an entry one deeper than its predecessor
is its child. A level that jumps by more than one -- which the printout's
own [invariants](printout.md#invariants) rule out -- is treated as one deeper
rather than rejected.

Each entry's destination is `/XYZ` at the entry's own `x` and `y` on the page
it appeared on, in that page's coordinates. An entry with children states how
many entries a reader shows when it is expanded, and states it **negated** when
the entry is `closed`, which is how PDF distinguishes a collapsed entry from
an expanded one. A document with any outline entry also asks the reader to
show the outline pane.

An [`xref` mark](printout.md#xref) becomes a link annotation over its box,
with no visible border. Its nested marks are drawn exactly as top-level marks
are -- they carry page coordinates -- so the box is purely a hit region.
`type="url"` becomes a URI action; `type="outline"` becomes a destination
equal to the target entry's. A `caption` becomes the annotation's hover text.

## Fonts in the file

Each font in the header becomes a **Type0 font with Identity-H encoding and
a CIDFontType2 descendant**, carrying a subset of the face the printout names.
The face is opened from its `resolvedFile`, or from the header `data` entry
its `resolvedData` names, at the face index `resolvedIndex` states.

A **code per character**, not per glyph. Code 0 is the face's empty glyph, and
every character the document sets gets a code of its own, in code point order.
Two characters that share a glyph -- a non-breaking space and a space, in most
faces -- therefore get two codes, and a character the face
[does not have](template.md#missing-glyphs) gets a code that draws the empty
glyph. So the text reads back out of the file as exactly what the printout
said, missing glyphs included, and stays searchable where a glyph is absent.

The widths the font dictionary states are the advances the engine measured,
written to **three decimal places** of a thousandth of an em rather than
rounded to whole thousandths. The file's numbers are then exact for the
usual units per em, and the pen inside a shown string lands where the
printout measured it to well under a thousandth of a point. Rounding
to whole thousandths, which is what every writer surveyed in
[decisions.md](decisions.md#font-metrics-and-pdf) does, is what leaves
a hundredth-of-a-point drift across a long line.

The embedded program carries `glyf`, `loca`, `head`, `hhea`, `hmtx` and `maxp`,
rebuilt for the subset, plus the hinting programs where the face has them.
Nothing else -- a composite font addresses glyphs directly, so no table in
the file maps characters. A composite glyph pulls its components into the
subset and is rewritten to name their new positions.

The font descriptor's `Ascent` and `Descent` are `hhea`'s -- the same pair
the [baseline](#text) is measured from, so nothing in the file contradicts
where the lines were put. They are signed as PDF defines them, the ascent above
the baseline positive and the descent below it negative, whichever signs the
face itself used: a face storing its descender as a magnitude is not rare,
and copying that through would state a descent above the face's own baseline.

The `BaseFont` name carries the six upper-case letters PDF requires in front of
a subset, derived from the face and the glyphs it holds, so two subsets of one
face differ and the same subset is stable across runs. The name after the tag
is the face's own PostScript name.

## Document metadata

The information dictionary comes from the printout's header: the report's
`name`, `author` and `description` become `Title`, `Author` and `Subject`,
the `engine` becomes `Creator` and `Producer`, and `built` -- the run's
`BUILD_TIME` -- becomes `CreationDate` and `ModDate`.

The creation date is the build time and not the moment of rendering.
Rendering one printout twice gives one document, and the build time
is the date the document actually carries.

## File structure

PDF 1.7, a classic cross reference table, one shared resource dictionary,
and one content stream per page. Content streams, embedded fonts and
re-encoded images are Flate-compressed; a JPEG keeps its own filter.
Nothing is encrypted and no object streams are used.

## Reproducibility

Rendering is a pure function of the printout. The same printout renders
to the same bytes: no timestamp of the renderer's own, no map iteration
order, no dependence on what is installed. Together with the engine's own
[reproducibility](template.md#font-resolution) -- a fixed `BUILD_TIME` and
strict fonts — a template and its data determine the PDF byte for byte,
on any machine.

A printout serialized to a file and read back renders to the same bytes
as the one it was built from. That is what makes a printout worth archiving:
the file is the document, not a stage on the way to one.

## Errors

Rendering fails, naming what it could not do, rather than producing
a page with something missing from it:

- A `text` mark naming a font the header does not carry.
- A font file or image file that cannot be read, or a data entry
  the header does not hold. The diagnostic names the font or the file.
- A face with PostScript (CFF) outlines; see below.
- An `xref type="outline"` whose target no outline entry claims -- a
  region that looks clickable and is not is worse than a stopped render.
- A printout with no pages. A PDF has at least one, and there is nothing
  sensible to invent.

## Boundaries

Two things this renderer does not do, both deliberate and both stated
so that a reader is not left guessing:

- **PostScript outlines.** A face whose outlines are in a `CFF` table rather
  than `glyf` — an `.otf` in the common case -- is refused, naming the font
  and the file. Embedding one means a CIDFontType0 descendant and a CID-keyed
  CFF, which is a separate piece of work; refusing is honest, and a wrong guess
  at it would produce files that fail to render on some readers only.
- **Standalone font files.** The embedded subset is the minimal set of tables
  PDF asks of a CIDFontType2 program, which does not include `cmap`. So it
  cannot be opened as a font file by anything that insists on one --
  which is every font library, correctly. That is a property of the format,
  not a defect: the [Stage 1 spike](decisions.md#font-metrics-and-pdf)
  found the same of all three candidate writers' output.
