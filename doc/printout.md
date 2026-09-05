# Printout format

A **printout** is the result of applying a template to data: a sequence of pages,
each holding a flat list of absolutely-positioned marks in paint order.

Nothing in a printout is evaluable. Every expression has been evaluated, every box
resolved to absolute coordinates, every string wrapped to lines, every barcode
encoded to stripe widths, every font resolved to a specific file. A renderer
needs no data, no template, and no expression evaluator.

Printouts are machine-generated and not intended for hand editing.

What a printout leaves to a renderer -- baseline placement inside a line's
leading, how a justified line spends its slack, what a dash pattern measures --
is specified in [render.md](render.md), which is the contract on the other
side of this one.

## Contents

- [Encoding](#encoding)
- [Header line](#header-line)
- [Page lines](#page-lines)
- [Marks](#marks)
  - [`text`](#text)
  - [`line`](#line)
  - [`rectangle`](#rectangle)
  - [`image`](#image)
  - [`barcode`](#barcode)
  - [`outline`](#outline)
  - [`xref`](#xref)
- [Invariants](#invariants)
- [Example](#example)

## Encoding

Two encodings carry the same data model:

- **NDJSON** (default, `.srp.jsonl`): one JSON object per line, LF-terminated.
  The first line is the [header](#header-line); every following line is a
  [page](#page-lines).
- **CBOR** (`.srp.cbor`): the same objects as a CBOR sequence, in the same order.
  Smaller, for large printouts.

The in-memory structure is the primary artifact — the engine hands it to a renderer
directly, and serializes only when asked. Serializing is not purely a re-encoding:
[paths](#paths) are rewritten relative to where the printout is being written.

### Units and number format

Every length is a number of PostScript points (1/72 inch), rounded to at most 3
decimal places. Colors are `"#RRGGBB"` strings.

Numbers serialize in the shortest form that round-trips. Integral values are
written without a fractional part, so `72` not `72.0`.

### Paths

Two fields hold filesystem paths: an [`image`](#image) mark's `file`,
and a [font](#fonts) entry's `resolvedFile`. One rule reads both:

- A **relative** path resolves against the directory the printout was read from.
- An **absolute** path is used as it stands.

So a renderer needs no base directory, and the header carries none. The printout is
a file, and a file has a location; that base cannot drift out of step with the document
the way a stored path could. Separators are `/` on every platform, so a printout
written on Windows renders on Linux.

Which form a path takes follows from where it came from, not from where it happens
to sit:

| Path | Form |
|---|---|
| Named by the template — `image file=` with `embed=#false`, or `font file=` | relative to the printout |
| Found on the host — a `font` resolved by `host`, `alias`, or `substitute` | absolute, as opened |

A path the template named is a project asset, so it travels with the printout. A font
the engine found is a system resource that was never in the project tree, and
`resolvedFile` doubles as the record of which file was measured — a diagnostic should
say literally what was opened. `resolvedBy` on the same entry distinguishes the two,
though a renderer need not look: relative or absolute is enough.

Relative paths are written at **serialization**, since that is when the printout's
location is known. In memory a path is whatever the engine resolved against the
template's `basedir`. Two consequences:

- Writing one printout to two directories gives two different values, and both are
  right.
- With no destination directory — a pipe, or an in-memory hand-off straight to a
  renderer — the process's working directory stands in.

A template-named file with no relative path to the printout at all, such as one on a
different Windows drive, is written absolute. There is nothing else to say about it.

### Nothing is copied onto the filesystem

Bytes go **into the document**: `embed=#true` puts an image's bytes, and a `data=`
font puts a font file's bytes, into the header's [`data`](#data) table. Files are
never put **beside the document**: writing a printout creates one file, the printout,
and nothing else.

So rewriting a path is only a rewrite. The file it names stays where it is, and a
relative path that reaches outside the printout's own directory —
`../fonts/Go-Bold.ttf` — is the ordinary case, not a defect. "A printout and its files
move as one tree" means the tree its paths span, which is normally the project.

A file the printout can already reach is therefore never duplicated. Where the same
font already sits at the destination, it is referenced rather than written again, and
never overwritten: two printouts in one directory using one font point at that font,
not at two copies of it. Inside the document the same rule holds — two images from one
source share one `data` entry.

Making the printout's own directory self-contained is a separate, deliberate act:
`embed=#true` for an image, a `data` node for a font, or moving the files yourself.

## Header line

```json
{
  "sr": 1,
  "kind": "header",
  "report": {
    "name": "DVD rental payments",
    "description": "Payments by customer",
    "version": "1.0",
    "author": "als"
  },
  "built": "2026-08-04T09:12:44Z",
  "engine": "sr 0.1.0",
  "strictFonts": false,
  "pages": 37,
  "groupRuns": { "customer": 599 },
  "groupKeys": { "customer": 599 },
  "page": {
    "width": 595.276, "height": 841.89,
    "leftMargin": 42.52, "rightMargin": 42.52,
    "topMargin": 28.35, "bottomMargin": 28.35
  },
  "fonts": [ … ],
  "data": { … }
}
```

| Field | Meaning |
|---|---|
| `sr` | Format version. `1` for this specification. |
| `kind` | Always `"header"`. |
| `report` | Metadata from the template's `report` node. Omitted fields are absent, not null. |
| `built` | RFC 3339, the run's `BUILD_TIME`. |
| `engine` | Name and version of the producing engine. |
| `strictFonts` | Whether font guessing was disabled for this run. |
| `pages` | Number of page lines that follow. |
| `groupRuns` | Per group name, how many times it opened. Group names are per-report namespaces, and this table is the document's, so a host and a [subreport](layout.md#subreports) that both call a group `region` are counted together here. |
| `groupKeys` | Per group name, how many distinct key values it saw. A `groupRuns` value larger than its `groupKeys` value means the input was not ordered by that group's key. |
| `page` | Default page geometry, inherited by every page that does not override it. |
| `fonts` | Resolved font table. |
| `data` | Shared blobs, keyed by name. |
| `warnings` | Present only when the build produced any. An array of objects with `kind`, `node`, `record`, and `message`. `kind` is `overflow` for an error `--allow-overflow` suppressed, `glyph` for a [character the resolved font lacks](template.md#missing-glyphs), or `font` for a `typeface` that reached the [substitute](template.md#the-substitute-face). Diagnostics about fonts on the host that this report did not use are [not warnings](template.md#host-enumeration). |

### `fonts`

One entry per distinct font used, sorted by `name`.

```json
{
  "name": "body",
  "size": 9,
  "bold": false, "italic": false, "underline": false,
  "requested": "Helvetica",
  "resolvedFile": "C:/Windows/Fonts/arial.ttf",
  "resolvedFace": "Arial",
  "resolvedBy": "alias"
}
```

This is the alias case: the template asked for `Helvetica`, the host had no family
of that name, and the alias table pointed at Arial. `requested` is present because
a `typeface` went through the resolution chain, and `resolvedFile` is the absolute
path that was opened. Had the machine held a real Helvetica — macOS does —
`resolvedBy` would be `host` and `resolvedFace` would say `Helvetica`.

`resolvedIndex` is the face's position inside a font collection, absent for the
ordinary single-face file. A `.ttc` holds several faces and the file name alone
does not say which one was measured, so a renderer that assumed the first would
set the text in a different typeface with nothing to show that it had. It can be
non-zero however the font was resolved: a template naming a collection by `file`
[chooses a face inside it](template.md#font) as much as host resolution does.

`resolvedFile` and `resolvedFace` are what was measured. `resolvedBy` is the step
of the [resolution chain](template.md#font-resolution) that produced it:

| | |
|---|---|
| `explicit` | the template named a `file` or `data` |
| `host` | [enumeration](template.md#host-enumeration) of the machine's fonts |
| `alias` | the family-alias table, then found on the host under the aliased name |
| `substitute` | the [last resort](template.md#the-substitute-face) — text may overflow, or overlap |

`host` covers every way a face was found on the machine — the Windows registry,
fontconfig, a directory scan — because nothing observable follows from which
of them produced it, and `resolvedFile` already says what was opened.

`resolvedFile` follows the [path rule](#paths): relative to the printout when
`resolvedBy` is `explicit`, because then the template named the file, and absolute
otherwise. Under `--strict-fonts` only `explicit` can occur, so every font path
in a strict printout is relative and the printout carries its fonts with it.

`requested` is the template's `typeface`. A `font` node that named a `file`
or `data` instead has no typeface to record, so `requested` is **absent** and
`resolvedBy` is `explicit`. Absent `requested` and `resolvedBy: "explicit"`
together mean the template pinned the font outright.

An embedded font names a `data` entry via `"resolvedData": "<name>"` instead of
`resolvedFile`.

### `data`

Blobs referenced by more than one mark, so image bytes are stored once
no matter how many pages show the logo.

```json
"data": {
  "logo": { "encoding": "base64", "content": "iVBORw0KGgo…" }
}
```

`encoding` is `"base64"` for binary, absent for literal text.
Content is stored decompressed.

Entries come from three places: the template's `data` nodes, which keep their
declared names; embedded fonts; and images the template gave as `file=` with
`embed=#true`, the default. Those last have no name in the template, so the engine
assigns one, stable for a given source file, and distinct from every declared name.
Two images from the same file share one entry.

## Page lines

```json
{
  "kind": "page",
  "number": 1,
  "marks": [ … ]
}
```

| Field | Meaning |
|---|---|
| `kind` | Always `"page"`. |
| `number` | 1-based page number as printed. Not necessarily the line's ordinal — a subreport with `ownpageno` restarts numbering. |
| `width`, `height` | Optional. Present only when they differ from the header's `page` defaults. |
| `leftMargin`, `rightMargin`, `topMargin`, `bottomMargin` | Optional, and independently so. Present only where they differ from the header's, which is why zero is written rather than omitted: a page flush to the paper edge under a header that insets is an override. |
| `marks` | Array of [marks](#marks) in paint order. |

Pages appear in output order. Sections are flattened: a detail band's rectangle,
four fields, and rule become five entries in `marks`, with no record of which band
produced them.

The overrides exist for a [subreport that paginates
itself](layout.md#pages): it may run at a page size and inset of its own, and
its pages sit in the middle of the host's. Everything else about the document
stays single: one font table, one data table, one sequence of pages. A mark
carries no record of which template produced it, exactly as it carries no record
of which band did.

## Marks

Every mark has `kind` and a `box`. The box is four required numbers — absolute,
with non-negative width and height, no alignment and no relative encoding:

```json
"box": { "x": 42.52, "y": 411.02, "width": 184.25, "height": 10 }
```

`x`/`y` are the top-left corner, measured from the top-left of the page. Y grows
downward. A renderer targeting a Y-up coordinate system flips once, at the page
level.

### `text`

```json
{
  "kind": "text",
  "box": { "x": 184.25, "y": 411.02, "width": 42.52, "height": 10 },
  "font": "body",
  "color": "#000000",
  "align": "right",
  "leading": 10.8,
  "lines": ["4.99"]
}
```

| Field | Meaning |
|---|---|
| `font` | Name of a header `fonts` entry. |
| `color` | Stroke colour of the glyphs. |
| `align` | `left` `center` `right` `justified`. |
| `leading` | Baseline-to-baseline distance. |
| `lines` | Wrapped lines, in order. Never empty. |
| `lastLineJustified` | Optional, default `false`. Only meaningful with `align: "justified"`. |

Text arrives already wrapped. A renderer must not re-wrap, and needs font metrics
only to place glyphs within a line.

`lastLineJustified` is `true` when a [split band](layout.md#band-splitting)
continues onto the next frame, so the line is not the paragraph's last and should
be justified.

### `line`

```json
{
  "kind": "line",
  "box": { "x": 42.52, "y": 421, "width": 510.24, "height": 0 },
  "width": 0.5,
  "dash": "dot",
  "color": "#808080",
  "backslant": false
}
```

The line runs from the box's top-left corner to its bottom-right,
or bottom-left to top-right when `backslant` is `true`. `width` is
the stroke width; `0` means a hairline — the thinnest the device draws.

### `rectangle`

```json
{
  "kind": "rectangle",
  "box": { "x": 42.52, "y": 400, "width": 510.24, "height": 20 },
  "width": 1,
  "dash": "solid",
  "stroke": "#000000",
  "fill": "#F3EDE7",
  "radius": 2
}
```

`stroke` and `fill` are independently optional. Absent `stroke` means no outline
is drawn regardless of `width`; absent `fill` means the interior is untouched.
The template's `stroke=#false` resolves to an absent `stroke`, and its
`opaque=#false` to an absent `fill`.

### `image`

```json
{
  "kind": "image",
  "box": { "x": 42.52, "y": 100, "width": 56.7, "height": 28.35 },
  "type": "png",
  "data": "logo",
  "crop": { "x": 0, "y": 0, "width": 120, "height": 60 }
}
```

| Field | Meaning |
|---|---|
| `type` | `png` `jpeg` `gif`. Always present. |
| `data` | Name of a header `data` entry. |
| `file` | Path to the image, when the template set `embed=#false`. Relative to the printout. Mutually exclusive with `data`. |
| `crop` | Optional source-pixel rectangle. Present only when the image was clipped, which only `scale="cut"` does. |

Exactly one of `data` and `file` is present. `data` is the default case: an image
given as `file=` with `embed=#true` has its bytes copied into the header's
[`data`](#data) table under a generated name, so the printout stands alone.

`file` appears only for `embed=#false`. The template named it, so it follows the
[path rule](#paths) and is written relative to the printout — a printout and the
images it points at can be copied, archived, or checked in as one tree and still
render.

`box` is the final drawn rectangle. `crop` names the source pixels that fill it;
when `crop` is absent the whole image does. Scaling is whatever maps the one onto
the other, so a renderer needs no notion of fitting: `cut` arrives as a `crop` at
scale 1, `fill` as a box the image is stretched into, and `grow` as a box already
the image's natural size.

The template's `scale` and `proportional` do not appear — both are resolved away.

### `barcode`

```json
{
  "kind": "barcode",
  "box": { "x": 42.52, "y": 60, "width": 90.7, "height": 36 },
  "type": "Code128",
  "value": "Code 128",
  "module": 10,
  "vertical": false,
  "ink": "#000000",
  "paper": "#FFFF00",
  "stripes": [10, 2, 1, 2, 2, 2, 2, 1, 1, 4, 10]
}
```

`stripes` is the encoded geometry, in modules. Both arrays alternate **light
and dark starting with light**, so index 0 is a light run, index 1 a dark one,
and so on. Polarity is positional, and nothing records it separately:

- **1-D types**: a flat array of alternating space and bar widths. The leading
  quiet zone is simply the first element and the trailing one the last.
- **2-D types**: an array of rows, each an array of alternating light and dark
  run lengths. A row that opens dark opens with a **zero-length light run**,
  which keeps the alternation unambiguous while letting the runs still sum to
  the whole extent. Only a symbology that asks for no quiet zone produces one.

Quiet zones are part of the geometry for every type, because a symbol drawn
without its margin does not scan. Each side carries what its standard requires:
ten modules for the 1-D types, four for QR, one for Data Matrix, and none for
Aztec. `box`, `module`, and the run sums all describe the symbol **including**
that margin.

`ink` is the bar colour and is always present. `paper` is optional: present, it
is filled over the whole box before the bars, so the quiet zone carries that
colour too; absent, nothing is laid down and whatever is beneath the mark shows
through.

A 1-D symbol's extent across the coding direction — its bar height — is fifteen
per cent of the symbol's length or a quarter of an inch, whichever is greater,
unless `grow` expands it to the box.

`module` is the narrow-element width in points, after any `grow` adjustment.
`value` is the encoded string, recorded so a reader can verify the encoding
without re-deriving it. A renderer draws filled rectangles and does no encoding.

### `outline`

```json
{
  "kind": "outline",
  "title": "Smith, John",
  "level": 2,
  "name": "cust-148",
  "closed": false,
  "x": 42.52,
  "y": 200
}
```

An outline entry has a point rather than a box — it names a scroll position.
`name` is present only when some `xref` targets it. Entries appear in the order
they must appear in the outline tree; `level` gives the nesting.

### `xref`

```json
{
  "kind": "xref",
  "box": { "x": 300, "y": 60, "width": 210, "height": 28.35 },
  "type": "url",
  "target": "https://dev.mysql.com/doc/sakila/en/",
  "caption": "Sakila documentation",
  "marks": [ … ]
}
```

`type` is `url` or `outline`; for `outline`, `target` matches an outline mark's
`name`. `caption` is optional hover text.

Nested `marks` use **page coordinates**, not coordinates relative to the xref box,
so a renderer can flatten them recursively and draw in one pass. The xref box is
purely a hit region.

## Invariants

A valid printout satisfies all of these. They are asserted on every printout
produced in the test suite.

1. `sr` is a version the reader understands.
2. The number of page lines equals the header's `pages`.
3. Every `font` referenced by a `text` mark exists in the header's `fonts`.
4. Every `data` referenced by an `image` or a font exists in the header's `data`.
5. Every `xref` with `type: "outline"` has a `target` matching some outline mark's
   `name`, somewhere in the printout.
6. Every box has non-negative `width` and `height`.
7. Every mark lies within its page's printable area — inside the margins — to
   within the 3-decimal rounding tolerance, unless the header carries a warning of
   kind `overflow`. A negative `right` or `bottom` in the template is what usually
   produces one; see [errors](layout.md#errors).
8. Every `text` mark has at least one line, and `lines` count times `leading` does
   not exceed the box height by more than the rounding tolerance.
9. `stripes` sums, times `module`, equal the box extent along the coding
   direction, within tolerance. For a 2-D symbol every row sums alike, and
   the row count times `module` equals the extent across that direction.
10. Outline `level` never jumps by more than one from the previous entry.
11. Every `image` mark carries exactly one of `data` and `file`.
12. Every `barcode` mark carries an `ink` colour.

## Example

A one-page printout with one font, a rule, and two detail rows.
The template pinned its font with `file=`, so `resolvedFile`
is [relative to the printout](#paths):

```json
{"sr":1,"kind":"header","report":{"name":"Minimal"},"built":"2026-08-04T09:12:44Z","engine":"sr 0.1.0","strictFonts":true,"pages":1,"page":{"width":595.276,"height":841.89,"leftMargin":42.52,"rightMargin":42.52,"topMargin":28.35,"bottomMargin":28.35},"fonts":[{"name":"body","size":9,"bold":false,"italic":false,"underline":false,"resolvedFile":"../fonts/Go-Regular.ttf","resolvedFace":"Go","resolvedBy":"explicit"}],"data":{}}
{"kind":"page","number":1,"marks":[
  {"kind":"text","box":{"x":42.52,"y":28.35,"width":200,"height":10.8},"font":"body","color":"#000000","align":"left","leading":10.8,"lines":["Film title"]},
  {"kind":"line","box":{"x":42.52,"y":39.15,"width":510.24,"height":0},"width":0.5,"color":"#000000","dash":"solid","backslant":false},
  {"kind":"text","box":{"x":42.52,"y":39.15,"width":200,"height":10.8},"font":"body","color":"#000000","align":"left","leading":10.8,"lines":["ACADEMY DINOSAUR"]},
  {"kind":"text","box":{"x":42.52,"y":49.95,"width":200,"height":10.8},"font":"body","color":"#000000","align":"left","leading":10.8,"lines":["ACE GOLDFINGER"]}
]}
```

The page line is shown wrapped for readability. In a real file it is one line.
