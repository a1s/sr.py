# Template format

A **template** describes how to turn a sequence of data records into a paginated
document. It is a [KDL](https://kdl.dev) v2 document whose root node is `report`,
conventionally in a file with a `.kdl` extension.

Applying a template to data produces a **printout** —
see [printout.md](printout.md). The rules governing how bands are placed
and paginated are in [layout.md](layout.md). Expressions are specified in
[expressions.md](expressions.md).

## Contents

- [Document model](#document-model)
- [KDL conventions](#kdl-conventions)
- [Value types](#value-types)
- [Geometry](#geometry)
- [Ordering rules](#ordering-rules)
- [Element reference](#element-reference)
  - [`report`](#report)
  - [`parameter`](#parameter)
  - [`records`](#records)
  - [Data input](#data-input)
  - [`variable`](#variable)
  - [`font`](#font)
  - [`data`](#data)
  - [`layout`](#layout)
  - [`style`](#style)
  - [`printwhen`](#printwhen)
  - [Content sources](#content-sources)
  - [`eject`](#eject)
  - [Sections: `title`, `summary`, `header`, `footer`, `detail`](#sections-title-summary-header-footer-detail)
  - [`columns`](#columns)
  - [`group`](#group)
  - [`field`](#field)
  - [`line`](#line)
  - [`rectangle`](#rectangle)
  - [`image`](#image)
  - [`barcode`](#barcode)
  - [`xref`](#xref)
  - [`outline`](#outline)
  - [`subreport`](#subreport)
  - [`arg`](#arg)
  - [`embedded`](#embedded)
- [Font resolution](#font-resolution)
- [Validation](#validation)

## Document model

A template has a declaration part and a layout part.

```
report
├── parameter*        values supplied by the caller
├── records?          declared types of the input record members
├── variable*         accumulators evaluated as data is consumed
├── font*             named font definitions
├── data*             named literal blobs (images, font files)
└── layout            page geometry, then the band tree
    ├── style*
    ├── embedded*     reusable sub-layouts for subreports
    ├── title?
    ├── summary?
    ├── header?
    ├── footer?
    ├── columns?
    └── group? | detail?
```

Exactly one of `group` or `detail` is required at each level that can hold them
(`layout`, `group`, `embedded`). Groups nest one level at a time; the innermost
level holds the `detail`.

**Sections** (also called bands) are `title`, `summary`, `header`, `footer`,
and `detail`. All five take the same set of children:

```
<section>
├── style*            conditional formatting, first match wins
├── eject*            forced page or column break, first match wins
├── outline*          document outline (bookmark) entry
├── field*            text
├── line*
├── rectangle*
├── image*
├── barcode*
├── xref*             a link, containing its own body elements
└── subreport*
```

`field`, `line`, `rectangle`, `image`, and `barcode` are collectively
**body elements**. They are the only nodes that put marks on the page.

## KDL conventions

The format uses a consistent subset of KDL:

- **A node's identity is a positional argument** where it has one:
  `font "body"`, `variable "total"`, `group "customer"`, `parameter "start"`.
  Everything else is a named property.
- **Booleans are `#true` and `#false`.**
- **Literal text content is the `text` property**, not a child node:
  `field text="Total:"`.
- **Long blobs use multi-line strings**, which dedent to the closing delimiter:

  ```kdl
  data "logo" encoding="base64" {
    content """
      iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8
      z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==
      """
  }
  ```

- **`/-` comments out the next node, or the next property.**
  Both are KDL v2; a v1 parser will not honour it on a property.

  ```kdl
  /-rectangle width=0 { box y=2 }          // the whole node
  field expr="amount" /-format="%.2f"      // just the property
  ```

### Parser requirements

The format uses KDL **v2** specifically: `#true` / `#false` / `#null`, raw
strings `#"…"#`, and triple-quoted multi-line strings. A v1 parser cannot
read it, and some v1 parsers accept v2 input without reading it correctly.

## Value types

### Dimension

A length. Bare numbers are PostScript points (1/72 inch).
Quoted strings may carry a unit suffix:

| Suffix | Unit |
|---|---|
| `pt` | point, 1/72 in (the default) |
| `mil` | 1/1000 in |
| `mm` | millimetre |
| `cm` | centimetre |
| `in` | inch |

`width=35` and `width="35pt"` are identical.

Internally all dimensions are points, rounded to 3 decimal places after
every computation. Rounding is normative: it decides whether a box fits.

### Color

Any of:

- `"#RRGGBB"`
- a name, case-insensitive: the 16 HTML 4.01 names plus `cyan`, `darkgray`,
  `lightgray`, `magenta`, `orange`, `pink`
- three comma-separated integers 0–255, or three floats 0–1: `"0,89,0"`
- a single integer, red in bits 16–23, green in 8–15, blue in 0–7

The canonical form, and what appears in a printout, is always `"#RRGGBB"`.

### Boolean

`#true` or `#false`.

### Integer

A KDL integer.

### Expression

A string holding a Starlark expression. See [expressions.md](expressions.md).
A string literal inside an expression needs its own quotes: `target="'top'"`,
not `target="top"`.

### Enumerations

| Type | Values |
|---|---|
| `align` (text) | `left` `center` `right` `justified` |
| `halign` | `left` `center` `right` |
| `valign` | `top` `center` `bottom` |
| `calc` | `count` `list` `set` `chain` `first` `last` `sum` `avg` `min` `max` `std` `var` |
| `iter` / `reset` | `report` `page` `column` `group` `detail` `item` |
| `eject type` | `page` `column` |
| `barcode type` | `Code128` `Code39` `Code93` `2of5i` `DataMatrix` `Aztec` `QR-L` `QR-M` `QR-Q` `QR-H` |
| `image scale` | `cut` `fill` `grow` |
| `xref type` | `outline` `url` |
| `dash` | `solid` `dot` `dash` `dashdot` |
| `encoding` | `base64` |
| `compress` | `zlib` `gzip` |

Page size names accepted by `layout pagesize=`:

- ISO 216: `A1` `A2` `A3` `A4` `A5` `A6` `B3` `B4` `B5` `B6`
- North American: `Letter` `Legal` `Ledger` `Executive` `Statement` `Quatro`
  `Royal` `BusinessCard`
- ISO 269 envelopes: `EnvelopeC3` `EnvelopeC4` `EnvelopeC5` `EnvelopeC6`
  `EnvelopeDL` `EnvelopeB4` `EnvelopeB5`
- North American envelopes: `Envelope#10` `EnvelopeA2` `EnvelopeA6` `EnvelopeA7`

All are given portrait (width × height); `landscape=#true` swaps them.

## Geometry

Every body element, every section, and every `xref` occupies a box
positioned within its container. The container is the section for a body
element, the frame for a section, and the `xref` for elements inside one.

### Position and size: any two of three

Horizontally: `left`, `right`, `width`. Vertically: `top`, `bottom`, `height`.
`right` and `bottom` are offsets measured **inward** from the container's far
edge. Specify any two per axis; the third is derived.

Missing values are filled in a fixed order — `left` then `right`, `top` then
`bottom` — until two of the three are known:

| Specified | Resolves as |
|---|---|
| nothing | `left=0 right=0` — the container's full width |
| `left` | `left`, `right=0` — extend to the container's right edge |
| `width` | `left=0`, `width` |
| `right` | `left=0`, `right` |
| any two | as given |
| all three | **error** |

Vertically the same, with `top`/`bottom`/`height`.

A negative `right` or `bottom` means the box extends past the container edge,
and is legal — for a container in the middle of the page that is perfectly ordinary.
A mark that ends up outside the *page's* printable area is an
[overflow](layout.md#errors), judged on the final page coordinates
rather than on the declaration.

`x` and `y` are accepted as aliases for `left` and `top`.

`maxwidth` and `maxheight` clamp a resolved extent and are not part
of the two-of-three count:

```kdl
field left=10 right=10 maxwidth=50
```

`maxheight` clamps a declared height and a height derived from the band alike.
On a `field` it clamps a stretched one too, and the lines beyond the clamped box
are dropped at a line boundary, exactly as they are for a field without
`stretch`. It does not clamp a `barcode`, whose box extent along the coding
direction is fixed by its stripe count and module, nor a `grow` image, which is
drawn at natural size: shrinking either box would describe a mark that is not
what gets drawn.

### Section height

A section's `height` is a **minimum**. The band is as tall as the greater of that
value and the lowest mark it produces, so content that needs more room gets it
and the band grows. `height="auto"` is the default and means a minimum of zero —
purely content-determined.

That is why `stretch=#true` works inside a band with a declared height: a row
declared `height=12` stays 12 points tall until a wrapped field needs 24, and
then it is 24. To hold a band to a fixed size instead, leave `stretch` off —
a `field` without it truncates at a line boundary rather than growing.

Which elements contribute to the measured height, and which are left to fill
whatever height the others settle on, is in
[layout.md](layout.md#building-a-band).

An element's `bottom=0` is a different thing: it means extend to the container's
bottom edge.

### Alignment

`halign` and `valign` align an element's **content inside its box**,
once the content's natural size is known. They do not move the box.
The defaults are `halign="left"` and `valign="top"`.

For an `image` the content is the bitmap and for a `barcode` the symbol,
so `halign` and `valign` position it inside the resolved box.

A `field` is the case where the two properties meet. Its text mark carries
the **resolved box**, not a box shrunk to the widest line — that is what
lets `align="right"` right-align a number in a column, and it is the form
[printout.md](printout.md#text) shows. `align` positions each line within
that box, and adds `justified`. `halign` then has nothing else to act on
horizontally, so it **supplies the alignment when `align` was not written**:

```kdl
field halign="center" text="Sakila payment list"   // centred
field align="right" expr="amount" format="%.2f"    // right-aligned
field halign="center" align="left" text="…"        // left: align wins
```

`valign` is unaffected: it positions the wrapped text's height,
which is content-sized, within the box's height.

### Floating elements

`float=#true` on a body element means its vertical position is not fixed: it is
pushed down below whatever elements lie above it, after their actual heights are
known. Placement is by partial order, not declaration order — see
[layout.md](layout.md#floating-elements).

A floating element's height must come from the element: a declared `height`, or
content, which includes `stretch=#true` on a `field`. It cannot come from the
container, since a floating element's own top is not settled until the other
elements have been placed. In practice that rules out giving a floating element
`bottom` and nothing else.

## Ordering rules

Node order is significant in three places. Reordering siblings changes output.

1. **Body elements paint in document order.** Later elements draw on top of
   earlier ones. A filled `rectangle` must precede the fields that sit on it.
2. **`style` matches first-win.** Each section's `style` nodes are tested in
   document order and the first whose `when` is true supplies the formatting.
   Search continues outward: the section's own styles, then `columns`, then
   each enclosing `group`, then `layout`.
3. **`eject` selects first-win**, in document order: the first node whose
   `when` is true is selected and stops the search, even if its `require`
   then declines to eject. See [`eject`](#eject).

`outline` nodes resolve first-win too, so a section emits **at most one** outline
entry each time it prints. Several `outline` nodes in one section are alternatives
selected by `when`, not a list of entries. For a nested outline, nest the groups
that produce it.

`subreport` is ordered by its numeric `seq`, not by document position.
Negative `seq` runs before the section's own content, non-negative after.
Ties break on document order.

## Element reference

### `report`

Root node.

| Property | Type | Default |
|---|---|---|
| `name` | string | — |
| `description` | string | — |
| `version` | string | — |
| `author` | string | — |
| `basedir` | string | template's directory |

`basedir` is the base for resolving relative paths in `image file=`,
`font file=`, and `subreport template=`.

Children: `parameter*`, `records?`, `variable*`, `font*`, `data*`, `layout`
(exactly one).

### `parameter`

A value supplied by the caller, available to expressions by name.

```kdl
parameter "period_start" type="date" default="2005-01-01"
parameter "period_end"   type="date"
parameter "min_amount"   type="decimal" default="0.00"
parameter "title_suffix" default="draft"
parameter "as_of"        type="datetime" defaultexpr="BUILD_TIME"
parameter "due"          type="date" format="02.01.2006" default="31.12.2005"
```

| | Type | Default |
|---|---|---|
| *(arg 1)* | string, the name | required |
| `type` | `string` `int` `decimal` `float` `bool` `datetime` `date` `object` `list` | `string` |
| `default` | text, parsed per `type` | — |
| `defaultexpr` | expression | — |
| `format` | string, for parsing `datetime` / `date` | RFC 3339 |
| `prompt` | boolean | `#false` |

`type` gives the parameter's value type. Inside expressions the value has
that type, so a `date` parameter supports `.year` and `.unix` and a `decimal`
parameter stays exact in arithmetic.

At most one of `default` and `defaultexpr` may be given. A parameter with neither
is **required**: building without a value for it is an error naming the parameter.

- `default` is text in the same form the caller supplies on a command line,
  parsed per `type` — see [below](#parameter-values-as-text).
- `defaultexpr` is an expression, for a default that has to be computed.

Either is used only when the caller supplies no value.

`prompt` marks the parameter as one an interactive front end should ask about;
the engine ignores it.

#### Parameter values as text

A parameter value arriving as text — from `--param NAME=VALUE`, or from
`default` — is parsed according to `type`:

| `type` | Accepted text |
|---|---|
| `string` | any, verbatim |
| `int` | optional sign and decimal digits; arbitrary precision |
| `decimal` | optional sign, digits, optional `.` and fractional digits |
| `float` | as `decimal`, plus exponent notation, `inf`, `nan` |
| `bool` | `true` `false` `1` `0`, case-insensitive |
| `date` | RFC 3339 date, `2005-01-01`, or `format` if given |
| `datetime` | RFC 3339 timestamp, `2005-05-24T22:53:30Z`, or `format` if given |
| `object` | a JSON object |
| `list` | a JSON array |

`date` yields a time value with zero time of day.

Text that does not parse is an error naming the parameter, its declared type, and
the offending text.

A `defaultexpr` result, and a subreport [`arg`](#arg) value, must match the
declared `type`; a mismatch is an error naming the parameter.

### `records`

Declares the members of the input records and their types.

```kdl
records {
  member "customer_id"  type="int"
  member "amount"       type="decimal"
  member "rental_date"  type="datetime"
  member "return_date"  type="datetime" nullable=#true
  member "customer"     type="object"
  member "film"         type="object"
}
```

Each `member` node:

| | Type | Default |
|---|---|---|
| *(arg 1)* | string, the member name | required |
| `type` | `string` `int` `decimal` `float` `bool` `datetime` `date` `object` `list` | `string` |
| `nullable` | boolean | `#false` |
| `format` | string, for parsing `datetime` / `date` | RFC 3339 |

Declared members are coerced once, at load, and are the names expressions may
reference as bare identifiers.

A member present in the data but not declared is not an error — data often carries
members a report does not use. It is not reachable as a bare name, but remains
reachable as `THIS["name"]`.

A JSON `null` in a member that is not `nullable` is an error naming the member
and the record index. In a `nullable` member it becomes `None`, which is
[false](expressions.md#truth-values).

`type="object"` and `type="list"` pass nested JSON through; their contents are
not declared and are reached by attribute or subscript. A `list` member is what
a [`subreport`](#subreport) runs over, and the subreport's own `records` is what
declares and coerces the elements — a raw JSON string `"4.25"` inside the list
becomes an exact decimal there, exactly as at report level. Without that
declaration the elements stay as JSON gives them, and `unit * qty` on two strings
is string repetition rather than arithmetic.

### Data input

Records come from JSON: either a single array document, or NDJSON with one record
per line. The engine buffers the whole dataset — `DATA_COUNT`, report-scoped
aggregates, and [keep-together](layout.md#keeping-content-together) lookahead
all require the full sequence.

The library API takes a Go slice directly; JSON is the CLI's front end.

### `variable`

An accumulator updated as data is consumed.

```kdl
variable "total_amount"    expr="amount" calc="sum" reset="report"
variable "row_number"      expr="1"      calc="sum" reset="page"
variable "customer_amount" expr="amount" calc="sum" reset="group" resetgrp="customer"
```

| | Type | Default |
|---|---|---|
| *(arg 1)* | string, the name | required |
| `expr` | expression | required |
| `init` | expression | — |
| `calc` | calc enum | `first` |
| `iter` | iter enum | `detail` |
| `itergrp` | string, a group name | — |
| `reset` | iter enum | `report` |
| `resetgrp` | string, a group name | — |

`iter` says when `expr` is evaluated and folded into the accumulator; `reset` says
when the accumulator is cleared. When either is `group`, the corresponding
`itergrp` / `resetgrp` names which group. `init`, if present, seeds the
accumulator as its first value.

Full semantics are in [expressions.md](expressions.md#variables).

### `font`

A named font definition, referenced by `style font=`.

```kdl
font "body"  typeface="Helvetica" size=9
font "bold"  typeface="Helvetica" size=9 bold=#true
font "title" typeface="Times"     size=18
font "mono"  file="fonts/DejaVuSansMono.ttf" size=8
```

| | Type | Default |
|---|---|---|
| *(arg 1)* | string, the name | required |
| `typeface` | string | — |
| `file` | string, path to a TTF/OTF | — |
| `data` | string, a `data` node name | — |
| `size` | integer, points | required |
| `bold` | boolean | `#false` |
| `italic` | boolean | `#false` |
| `underline` | boolean | `#false` |

Exactly one of `typeface`, `file`, or `data` is required. `file` and `data`
name a font resource directly; `typeface` goes through
[font resolution](#font-resolution).

`bold` and `italic` select a face when resolving by `typeface`.

With `file` or `data` they usually select nothing, because a font file
usually holds one face and that face is what was named. A **collection** --
a `.ttc`, or an `.otc` -- holds several, and then they choose among them:
the first face whose own [style bits](#host-enumeration) are exactly the
ones declared, and the file's first face when none matches. Style, not
position: face 0 of a collection is not reliably its regular one.

Either way they are also descriptive: they travel to the printout's
[font entry](printout.md#fonts) and record what the template says the face
is, and the face that was chosen is recorded beside them as
[`resolvedIndex`](printout.md#fonts).

Because that entry is read as a description of the face, declaring a style
the file does not carry is reported: resolution reads the face's own
[style bits](#host-enumeration) and warns. The reverse is not reported:
naming `Go-Bold.ttf` without `bold=#true` is ordinary use, since the flag
would only repeat what the file already says.

`underline` is drawn by the renderer and does not affect metrics.

### `data`

A named literal blob: image bytes, a font file, or field text.

```kdl
data "logo" encoding="base64" compress="zlib" {
  content """
    eJzT0yMAAGTvBe8=
    """
}
data "footnote" expr="parameter_notice"
```

| | Type | Default |
|---|---|---|
| *(arg 1)* | string, the name | required |
| `encoding` | encoding enum | none — content is literal text |
| `compress` | compress enum | none |
| `expr` | expression | — |

Either a `content` child or `expr`, not both. With `expr`,
the blob is produced at build time from the expression's result.

### `layout`

Page geometry and the root of the band tree.

| Property | Type | Default |
|---|---|---|
| `pagesize` | page size name | — |
| `width` | dimension | — |
| `height` | dimension | — |
| `landscape` | boolean | `#false` |
| `leftmargin` | dimension | 0 |
| `rightmargin` | dimension | 0 |
| `topmargin` | dimension | 0 |
| `bottommargin` | dimension | 0 |

Either `pagesize` or both `width` and `height` is required.

Children: `style*`, `embedded*`, `title?`, `summary?`, `header?`, `footer?`,
`columns?`, and exactly one of `group?` / `detail?`.

### `style`

Conditional formatting. First match wins; see [ordering](#ordering-rules).

```kdl
style font="body" color="black"
style bgcolor="#F3EDE7" when="PAGE_COUNT % 2"
```

| Property | Type | Default |
|---|---|---|
| `when` | expression | `True` |
| `font` | string, a `font` name | — |
| `color` | color | — |
| `bgcolor` | color | — |

`when` selects the style. `font` must name a defined `font`;
an unknown name is a validation error.

Unset properties on the matched style fall through to the next match
in the same outward walk, so a band-level style setting only `bgcolor`
still inherits a `font` from `layout`.

### `printwhen`

Every section and every body element accepts `printwhen`, an expression that
suppresses it when false. A suppressed element produces no marks and contributes
no height, so a band whose `height="auto"` collapses when everything in it is
suppressed.

```kdl
detail printwhen="amount > 0" {
  rectangle width=0 printwhen="PAGE_COUNT % 2"
  field expr="film.title"
}
```

`printwhen` is evaluated independently of style matching.

### Content sources

`field` and `barcode` take their content from `expr`, `text`, or `data`.

**Without `evaltime`**, exactly one of the three.

**With `evaltime`**, `expr` is the content and is required — it is what gets
deferred. `text` or `data` may accompany it as a **placeholder**: the value the
element is measured from before the real one is known. At most one placeholder.

```kdl
// immediate — one source
field expr="amount" format="%.2f"
field text="Total:"

// deferred — expr is the content, text sizes the box until it resolves
field expr="'Page %d of %d' % (PAGE_NUMBER, FINAL.PAGE_NUMBER)" \
      evaltime="report" text="Page 999 of 999"
barcode type="2of5i" expr="FINAL.PAGE_COUNT" format="%04d" evaltime="page" \
      text="9999"
```

`evaltime` names the scope; [`FINAL`](expressions.md#final) names what is taken
from the end of it. Everything else reads its value where the element sits,
so `PAGE_NUMBER` above is the page the field prints on while `FINAL.PAGE_NUMBER`
is the page count. The two properties require each other — see
[what a deferred expression sees](layout.md#what-a-deferred-expression-sees).

A placeholder is **required** exactly when the element's own size depends
on its content, which is two cases:

- a `field` with `stretch=#true`, whose height grows to fit the wrapped text;
- any `barcode`, whose box grows along the coding direction to at least the
  symbol's minimum size.

Everywhere else the resolved box is independent of the content, so there is nothing
to reserve and a placeholder is optional. Note that this includes elements sized by
`bottom` — geometry resolution always yields a complete box, so "the box is
incomplete without knowing the content" never arises.

`image` and `data` have no deferred form. `image` takes exactly one of `file`,
`data`, or a `content` child; `data` exactly one of `expr` or a `content` child.

### `eject`

Forces a page or column break.

```kdl
eject type="page" when="customer_COUNT > 20"
eject require="3cm"
eject type="page" when="customer_COUNT > 20" require="3cm"
```

| Property | Type | Default |
|---|---|---|
| `type` | eject enum | `page` |
| `when` | expression | `True` |
| `require` | dimension | — |

`when` **selects** the node. `require` then decides whether the selected node
ejects.

A band's `eject` nodes are tested in document order. The first whose `when` is true
is selected and the search stops there — later nodes are never considered, whether
or not the selected node ejects. A node whose `when` is false is skipped and the
next is tried.

Once a node is selected:

| `require` | Result |
|---|---|
| absent | eject |
| present | eject only if less than `require` remains in the frame |

So the two combine as a conjunction, and `require` is reachable only through a true
`when`:

| `when` | `require` | Result |
|---|---|---|
| false | any | not selected; the next `eject` node is tried |
| true or absent | absent | eject |
| true or absent | present | eject if remaining space < `require` |

The three examples above therefore mean: eject whenever the customer has more than
20 rows; eject whenever less than 3cm remains; and eject only when both hold.

Eject is evaluated at the **beginning** of the section, except in a report's own
`title` — the band directly under `layout` or `embedded` — where it is evaluated
at the end, so that an `eject` there puts the title on a page of its own. A group's
`title` follows the ordinary rule.

A band that does not fit the space remaining ejects on its own, without any `eject`
node — see [layout.md](layout.md#placing-a-band).

### Sections: `title`, `summary`, `header`, `footer`, `detail`

| Property | Type | Default | Applies to |
|---|---|---|---|
| `height` | dimension or `"auto"`, a **minimum** | `"auto"` | all |
| `printwhen` | expression | — | all |
| `split` | boolean | `#false` | all |
| `orphans` | integer, text lines | 1 | all, when `split` |
| `widows` | integer, text lines | 1 | all, when `split` |
| `swapheader` | boolean | `#false` | `title` only |
| `swapfooter` | boolean | `#false` | `summary` only |

`split=#true` allows the band to break across frames, continuing wrapped text of
`stretch` fields on the next. `orphans` and `widows` are minimum line counts at a
break. Elements that cannot split — barcodes, images, band-spanning rectangles —
prevent a break in their vertical span.

`swapheader` on `title` places the title above the page header on the first page
rather than below it; `swapfooter` on `summary` does the same at the bottom.

`title` prints once before any data, `summary` once after. `header` and `footer`
repeat per frame. `detail` prints once per data record.

### `columns`

Splits the enclosing frame into columns.

```kdl
columns count=2 gap="5mm" {
  header height=20 { ... }
  footer height=12 { ... }
}
```

| Property | Type | Default |
|---|---|---|
| `count` | integer | required |
| `gap` | dimension | 0 |
| `balance` | boolean | `#false` |

Children: `style*`, `header?`, `footer?`.

Column width is `(frame width - (count - 1) × gap) / count`.

`balance=#true` spreads the run of bands the frame holds on each page over its
columns, so that they end at similar heights instead of the last one running
short. It needs more than one column to spread between. Some content cannot
be moved once it is placed and is left where the fill put it; see
[layout.md](layout.md#balanced-columns) for the list.

### `group`

A data-driven grouping level.

```kdl
group "customer" expr="customer_id" keeptogether=#true minrows=2 {
  title { ... }
  summary { ... }
  detail { ... }
}
```

| | Type | Default |
|---|---|---|
| *(arg 1)* | string, the name | required |
| `expr` | expression | required |
| `keeptogether` | boolean | `#false` |
| `minrows` | integer, detail rows | 1 |
| `mintailrows` | integer, detail rows | 1 |

The group breaks whenever `expr` changes value between adjacent records,
so records must arrive ordered by the group key — see
[layout.md](layout.md#records-must-be-ordered-by-group-key).
A break invalidates all nested groups.

`keeptogether=#true` places the whole group on one frame if it fits on an empty
one. `minrows` is the minimum number of detail rows that must follow the group
title in the same frame; `mintailrows` the minimum that must precede the group
summary. These are counted in rows, distinct from a section's line-counted
`orphans` / `widows`.

Children: `style*`, `title?`, `summary?`, `columns?`, and exactly one of `group?`
/ `detail?`.

Defining a group named `X` makes `X_COUNT` and `X_PAGE_NUMBER` available to
expressions.

### `field`

Text.

```kdl
field expr="amount" format="%.2f" align="right" width="15mm"
field text="Film title" left="3mm" width="32mm" valign="center"
field expr="notes" stretch=#true left=0 right=0
```

| Property | Type | Default |
|---|---|---|
| `expr` | expression | — |
| `text` | string, literal content | — |
| `data` | string, a `data` node name | — |
| `evaltime` | `report` `page` `column` or a group name | — |
| `align` | text align enum | `left` |
| `format` | string, a `%` format | `"%s"` |
| `stretch` | boolean | `#false` |

Plus the geometry and alignment properties from [Geometry](#geometry),
`printwhen`, and `style*` children.

Content comes from `expr`, `text`, or `data` — see
[content sources](#content-sources). `format` is applied to the `expr` result,
so a tuple-valued expression spreads across several conversions:

```kdl
field expr="(customer.last_name, customer.first_name, customer_amount)"
      format="Total for %s, %s: %.2f"
```

`evaltime` names a scope whose end the expression's [`FINAL`](expressions.md#final)
names are read at — this is how a page footer prints the final page count.
See [layout.md](layout.md#deferred-evaluation).

`stretch=#true` grows the box's height to fit wrapped text.
Without it, text that does not fit is truncated at a line boundary.

### `line`

```kdl
line width=1 left=0 right=0 height=0
line dash="dot" left=0 right=0
```

| Property | Type | Default |
|---|---|---|
| `width` | dimension, stroke width | 0 (hairline) |
| `dash` | dash enum | `solid` |
| `backslant` | boolean | `#false` |

Plus geometry, `printwhen`, and `style*`.

The line runs corner to corner of its box: top-left to bottom-right,
or bottom-left to top-right when `backslant=#true`.

`width` on a `line` is the **stroke width**, not a geometry extent.
Use `left`/`right` for a line's horizontal extent.

### `rectangle`

```kdl
rectangle width=1 radius=2
rectangle width=1 opaque=#false          // outline only
rectangle stroke=#false                  // fill only
```

| Property | Type | Default |
|---|---|---|
| `width` | dimension, stroke width | 0 (hairline) |
| `dash` | dash enum | `solid` |
| `radius` | dimension, corner radius | 0 |
| `opaque` | boolean | `#true` |
| `stroke` | boolean | `#true` |

Plus geometry, `printwhen`, and `style*`.

The stroke uses the style's `color`, the fill its `bgcolor`. The two switch off
independently: `opaque=#false` suppresses the fill, `stroke=#false` suppresses
the outline. A `width` of 0 draws a hairline, which is the thinnest line the device
can manage — it does **not** mean "no outline".

For a background block, then, `rectangle stroke=#false` is the form to use.
Leaving `color` out of the rectangle's own `style` does not work: unset style
properties fall through to the enclosing styles, so a `color` set at `layout` level
still reaches it and the block gets a hairline border.

### `image`

```kdl
image file="logo.png" scale="fill" width="2cm" height="1cm"
image data="TheLarch" scale="grow"
```

| Property | Type | Default |
|---|---|---|
| `file` | string, path relative to `basedir` | — |
| `data` | string, a `data` node name | — |
| `type` | `png` `jpeg` `gif` | sniffed from content |
| `scale` | scale enum | `cut` |
| `proportional` | boolean | `#true` |
| `embed` | boolean | `#true` |

Plus geometry, alignment, `printwhen`, and `style*`.

Exactly one of `file` or `data`, or a `content` child.

`scale` decides how the image meets the box:

| `scale` | Effect |
|---|---|
| `cut` | Drawn at natural size and clipped to the box. |
| `fill` | Scaled to the box. |
| `grow` | The box expands wherever the image exceeds it; the image is drawn at natural size. |

`proportional` preserves the aspect ratio, and so applies **only to `fill`** —
`cut` and `grow` do not scale. Under `fill` with `proportional=#true` the image
is scaled to fit within the box and then positioned by `halign` / `valign`;
with `#false` it is stretched to the box exactly.

Only `cut` clips, and the clipped region is what the printout records
as [`crop`](printout.md#image).

`embed=#true`, the default, copies the image bytes into the printout, so the
printout is self-contained. `embed=#false` records a file reference instead:
the path is resolved against `basedir` when the template is read, and written
into the printout relative to the printout itself, so the two travel together --
see [printout.md](printout.md#image). The cost is that rendering then depends
on the image still being where the printout expects it.
`embed=#false` needs `file=`, since a reference is all it writes and only
`file` supplies a path; `data` and a `content` child are always embedded.

`type` is optional and sniffed from the content; give it to override.

### `barcode`

```kdl
barcode type="Code128" text="Code 128" valign="top"
barcode type="QR-Q" expr="FINAL.PAGE_COUNT" evaltime="page" text="1000" grow=#true
```

| Property | Type | Default |
|---|---|---|
| `type` | barcode enum | required |
| `expr` | expression | — |
| `text` | string, literal content | — |
| `data` | string, a `data` node name | — |
| `evaltime` | as for `field` | — |
| `format` | string, a `%` format | `"%s"` |
| `module` | dimension, narrow bar width | `"10mil"` |
| `vertical` | boolean | `#false` |
| `grow` | boolean | `#false` |
| `ink` | colour, the bars | `"black"` |
| `paper` | colour, the background | — |

Plus geometry, alignment, `printwhen`, and `style*`.

Content comes from `expr`, `text`, or `data` — see
[content sources](#content-sources). A barcode's size is always content-dependent,
so a deferred barcode always needs a placeholder.

`format` is applied to the `expr` result before encoding, which is how a numeric
value gets a fixed width:

```kdl
barcode type="2of5i" expr="order_id" format="%08d"
```

The box always grows along the coding direction — vertically when
`vertical=#true`, horizontally otherwise — to at least the symbol's minimum size.
`grow=#true` additionally expands the symbol to use the available box: for 2-D
types this recomputes `module`, for 1-D types it grows the bar height.

Each type constrains what it can encode, and content it cannot encode is an error
naming the type, the value, and the reason:

| Type | Accepts |
|---|---|
| `Code128` | any of ASCII 0–127 |
| `Code39` | digits, `A`–`Z` upper case, space, and `- . $ / + %` |
| `Code93` | as `Code39`, plus the full ASCII range through its shift characters |
| `2of5i` | digits only, and an **even** number of them |
| `DataMatrix` `Aztec` `QR-*` | any bytes, up to the symbol's capacity, which for the `QR-*` types shrinks as the error-correction level rises |

`2of5i` encodes digits in pairs, so an odd-length value is an error
rather than being padded. Use `format` to fix the width:

```kdl
barcode type="2of5i" expr="PAGE_COUNT" format="%04d"
```

Every symbol carries the quiet zone its standard requires: ten modules
at each end of a 1-D symbol, four all round a QR, one round a Data Matrix,
none round an Aztec; and the box is sized to include it. A symbol without
its margin is not read, so this is not something a template can turn off.

#### Colour

`ink` and `paper` are the barcode's own and are **not** taken from the enclosing
`style`. A style's `color` falls through from every scope outside the element,
so honouring it here would let a layout that colours its text quietly recolour
every symbol under it, and change what scans.

`paper` fills the whole box, quiet zone included, before the bars go down.
Without it nothing is painted and whatever the band left underneath shows
through -- which is fine over a white page and is the reason `paper` exists
anywhere else.

Colour is constrained by how a scanner sees. It reads in red light,
around 660 nm, so what matters is not how dark a colour looks but
how little red it reflects:

| | Reads | Does not |
|---|---|---|
| Bars | black, navy, dark green, brown, purple | red, orange, yellow, light grey |
| Background | white, yellow, orange, pink, red | navy, dark green, black |

A pair that cannot be read is a **load error**, not a warning:

```kdl
barcode type="Code128" text="7350053850019" ink="navy" paper="#FFE9B0"
barcode type="QR-H" text="https://example.invalid/" ink="yellow"
//                                                  ^ refused: yellow reflects
//                                                    red light as fully as the
//                                                    white behind it does
```

Naming only `ink` measures it against white, because that is what an unprinted
background is. The check is deliberately coarse: it works from the colours the
template names and cannot know what ink, paper stock, or printer will do to
them, so it catches what cannot work in principle and nothing subtler.
A symbol going into production still wants verifying off a printed label.

#### Drawing over a symbol

Nothing in `barcode` places a logo, and nothing needs to: document order
is paint order, so an element declared after a barcode lands on top of it.
A QR code with a logo in the middle is a `barcode`, then a filled `rectangle`,
then an `image`:

```kdl
barcode type="QR-H" text="https://example.invalid/" grow=#true paper="white" \
        right=0 width="18mm" top="2mm" height="18mm"
rectangle stroke=#false left="165mm" right="6mm" top="8mm" height="6mm" {
  style bgcolor="white"
}
image file="logo.png" right="6.75mm" width="4.5mm" top="8.75mm" height="4.5mm"
```

What keeps it readable is the error-correction level (`QR-H` recovers about
30% of the symbol) and covering well inside that budget, centrally, where only
data and error-correction modules live. The white rectangle matters too:
without it the logo's own edges read as modules. `grow` with an explicit square
box is what makes the overlay's position predictable enough to write down.

See [invoices.kdl](../example/invoices/invoices.kdl) for the worked example.

Barcodes are always embedded in the printout, as stripe geometry
rather than a bitmap; see [printout.md](printout.md#barcode).

### `xref`

A link region that contains its own body elements.

```kdl
xref type="url" target="'https://dev.mysql.com/doc/sakila/en/'" \
     right=0 height="1cm" halign="right" {
  field align="right" text="DVD rental payments" { style font="title" }
}
```

| Property | Type | Default |
|---|---|---|
| `type` | xref enum | required |
| `target` | expression | required |
| `caption` | expression | — |

Plus geometry and body-element children (`field`, `line`, `rectangle`, `image`,
`barcode`, and nested `xref`).

`type="url"` links to the expression's string result.
`type="outline"` links to an `outline` whose `name` matches.

An `xref`'s box follows the ordinary [geometry](#geometry) rules, the same as
a body element's: it is a container for the elements inside it, and with nothing
specified it fills its section. Use `halign` to put a narrower run of content at
one end of it.

### `outline`

An entry in the document's outline (bookmark) tree.

```kdl
outline title="'DVD rental payments'" name="'top'" closed=#true
outline title="'%s, %s' % (customer.last_name, customer.first_name)" level=2
```

| Property | Type | Default |
|---|---|---|
| `title` | expression | required |
| `level` | integer | 1 |
| `name` | expression | — |
| `when` | expression | — |
| `closed` | boolean | `#false` |

`name` makes the entry a target for `xref type="outline"`. `closed=#true` renders
the entry collapsed.

A section emits at most one outline entry per print. Where a section has several
`outline` nodes they are alternatives, resolved first-win by `when` — see
[ordering](#ordering-rules).

### `subreport`

Runs another template over a nested data sequence.

```kdl
subreport seq=10 template="detail_lines.kdl" data="THIS.lines" {
  arg "heading" value="film.title"
}
```

| Property | Type | Default |
|---|---|---|
| `template` | string, path relative to `basedir` | — |
| `embedded` | string, an `embedded` node name | — |
| `seq` | integer | required |
| `data` | expression yielding a sequence | required |
| `when` | expression | — |
| `inline` | boolean | `#false` |
| `ownpageno` | boolean | `#false` |

Exactly one of `template` or `embedded`.

`seq` orders a subreport against the section it belongs to: negative places its
bands before the section, non-negative after it. A subreport is not laid out inside
the section's box and takes nothing from its height — see
[where a subreport's bands go](layout.md#where-a-subreports-bands-go).

`inline=#true` places the subreport's bands into the current frame instead of
starting fresh pages; an inline subreport must match the parent's page size,
inherits its margins, and defines no `header`, `footer`, `swapheader` title
or `swapfooter` summary of its own -- it does not own the pages it prints on.
It may open a `columns` block: that reserves a frame inside the host's, which
is the one thing an inline subreport does own. `ownpageno=#true` restarts page
numbering inside the subreport, and is incompatible with `inline`.

A `subreport` belongs on a `detail` band, on a `title` without `swapheader`,
or on a `summary` without `swapfooter`, and nowhere else -- see
[where a subreport's bands go](layout.md#where-a-subreports-bands-go).
Any of those three carries one wherever it sits, a `columns` block included.

A host band suppressed by `printwhen` runs none of its subreports.

`template=` is read when this template is read, so one `sr validate` checks the
whole tree and one load error names whichever file it came from. A template that
reaches itself, directly or through another, is refused at load.

Children: `arg*`.

### `arg`

Supplies a value for a subreport `parameter`.

| | Type | Default |
|---|---|---|
| *(arg 1)* | string, the parameter name | required |
| `value` | expression | required |

`value` is an expression, not text, so it is not parsed per the parameter's `type`.
Its result must already match that type. It is evaluated in the **host's**
context, at the point the subreport runs.

A subreport has no command line, so a parameter of the layout it names that has
neither an `arg` here nor a `default` or `defaultexpr` of its own is a load-time
error.

### `embedded`

A subreport layout defined inline, referenced by `subreport embedded=`.

| | Type | Default |
|---|---|---|
| *(arg 1)* | string, the name | required |

Children: `parameter*`, `records?`, `variable*`, `style*`, `title?`, `summary?`,
`header?`, `footer?`, `columns?`, exactly one of `group?` / `detail?`,
and nested `embedded*`.

An `embedded` layout is its own namespace for its `parameter`, `records`,
`variable`, and `group` names, but shares the enclosing report's `font`
and `data` definitions, its `basedir`, and its page size and margins.

Its style search continues outward into the enclosing `layout`, per the
[ordering rules](#ordering-rules): the walk is the band's own styles, then
the `embedded` node's, then the `layout` the `embedded` node is written in.
A layout named by `template=` is a separate document and its walk ends at
its own `layout`, which is the same rule read against a different tree.

The scope of an `embedded` name is lexical, searched from the inside out: the
layouts declared beside the one the subreport is written in -- which includes
that one, so a layout can invoke itself and walk a tree -- then those declared
in each layout enclosing it, out to the report's own. A layout nested inside a
*sibling* is not in scope, which is what makes a nested one private to its
parent. See also [the nesting bound](layout.md#subreports).

Two layouts in one scope chain may not share a name: not two siblings, and not
a nested one repeating a name an enclosing layout already has, since the search
runs outward and the second would shadow the first. Two unrelated layouts may
each nest a private one of the same name, because neither is in the other's
scope.

A name is resolved once, when the template is loaded, so the check and the build
always mean the same layout by it.

Its `records` declares the fields of the sequence the subreport runs over, which
is a different shape from the parent's records — an invoice report's records are
invoices, its subreport's are line items. It also **coerces** those elements:
a `list` member arrives from JSON untyped, and the subreport's `records` is what
turns each element's fields into ints, decimals, and times. Without the declaration
the subreport's expressions have no names to compile against, and no types to
compute with, exactly as at report level.

`header` and `footer` are per-page bands and belong only to a subreport
that paginates itself. An `embedded` layout used by an
[`inline`](layout.md#page-headers-and-footers) subreport must not define them;
use `title` and `summary` for content that prints once per invocation.

A subreport given by `template=` is an ordinary `report` document and carries
its own `records`, `font` and `data` definitions, `basedir`, and page geometry.
It validates on its own, so a layout meant only ever to be run as a subreport
still passes `sr validate` -- its parameters are simply ones a caller has to
supply.

## Font resolution

A `font` node naming a `typeface` is resolved by trying, in order:

1. An explicit `file` or `data` on the `font` node. If either is present,
   resolution ends there and failure is an error.
2. [Host enumeration](#host-enumeration): every font the machine has, matched by
   family and style.
3. A built-in table of **family aliases** — `Helvetica` → `Arial`,
   `Courier` → `Courier New` and the rest — each alias then looked for by step 2.
4. A last-resort [substitute](#the-substitute-face).

The alias table is consulted **after** the host has been searched, never before: an
alias is what to try when the machine has no family of that name, and a machine that
has one must win. `Helvetica`, `Times` and `Courier` are all real families on macOS.

**Strict mode** stops after step 1 and fails with the unresolved typeface named.
Enable it with `--strict-fonts` on the CLI, or the equivalent library option.

The printout records which file and face were actually measured, and which step of
the chain produced them — see [printout.md](printout.md#fonts). A font the template
named with `file=` is recorded relative to the printout, so it travels with it;
one the engine found on the host is recorded as it was opened. Under strict mode
only the first case can arise.

### Host enumeration

Step 2 builds a table of every face on the machine, keyed by family, boldness and
slant, and looks the requested `typeface` up in it. Sources, all of them merged
into the one table:

| Platform | |
|---|---|
| Windows | the registry, plus `%WINDIR%\Fonts` and `%LOCALAPPDATA%\Microsoft\Windows\Fonts` |
| Linux | fontconfig's font list and its configured directories |
| macOS | `/System/Library/Fonts` and its `Supplemental` subdirectory, `/Library/Fonts`, `~/Library/Fonts` |

These are sources for one table, not alternatives tried in turn, and the printout
records `host` without naming which of them found the face. A directory that does
not exist is not an error.

**Collections are enumerated face by face.** A `.ttc` holds several faces and every
one of them is a separate entry. This is not a refinement to add later: on macOS
two thirds of the installed faces live in collections, `Helvetica`, `Times`,
`Courier` and `Menlo` among them, so an enumeration that skips `.ttc` does
not work on that platform at all.

**The family name comes from a name record, the style does not.** Family is name ID
16 where the font has one and name ID 1 otherwise. Boldness and slant come from the
style bits, in this order:

1. `OS/2.fsSelection` — bit 0 `ITALIC`, bit 5 `BOLD`, bit 9 `OBLIQUE`.
2. `head.macStyle` — bit 0 bold, bit 1 italic.
3. The subfamily string, name ID 17 or 2, as a last resort.

This ranks **sources, not answers**: the first table the face has decides, and the
subfamily string is read only when neither table is present — not when it disagrees.
A face whose bits are wrong is therefore classified wrongly, and the engine has no
way to tell that case from a face whose subfamily string is merely a weight name it
could not have classified anyway. Reading a contradicting string as an override
would trade a rare wrong answer for a common one.

**The engine matches by family itself.** It may not delegate to a platform matcher
that answers every query rather than reporting a miss — `fc-match` is one such,
returning the host's default for a family that does not exist. Matching is
case-insensitive.

No rule here recovers a weight the format cannot express: `bold` is a boolean,
so a family offering `Semibold`, `Bold` and `Black` is matched on the bit,
not on which of them is *most* bold.

**Two faces can claim one key.** The first found wins, scanning the sources in the
order tabulated above and files within a directory in Unicode order by name. The
loser is recorded as an enumeration diagnostic. This is not rare — a font installed
in two directories, or an ornament face declaring its parent's family, will do it.

**Nothing is dropped silently.** A file the engine cannot use is one of two cases:

- A format it does not support — Type 1, Multiple Master, a datafork font,
  a bitmap face — is **classified and skipped**, recorded as an enumeration
  diagnostic.
- A file that presents itself as sfnt and then fails to parse is a **warning**.

Neither is decided from the filename extension. Filtering on `.ttf`/`.ttc`/`.otf`
would satisfy this rule by accident while hiding real faces, which is the defect
it exists to prevent.

**Enumeration diagnostics are not report warnings.** They describe the machine,
not the document, so they do not enter the [printout's warning
list](printout.md#header-line) and are surfaced by `sr validate` and by the library's
diagnostic hook instead. A stock macOS has three files that can never be parsed
and cannot be removed, so putting them in the printout would attach a permanent,
unfixable warning to every report and teach the reader to ignore the list that also
carries missing glyphs and overflow. A file that *would* have answered a lookup and
could not be read is a different matter, and is a warning.

### The substitute face

Step 4 tries, per platform, in order. A filename is looked for in the directories
[listed above](#host-enumeration); a generic family is a query to the platform.

| Platform | |
|---|---|
| Windows | `cour.ttf`, then `consola.ttf` |
| Linux | fontconfig's answer for the generic family `monospace`; failing that `DejaVuSansMono.ttf`, `LiberationMono-Regular.ttf`, `NotoSansMono-Regular.ttf` |
| macOS | `Monaco.ttf` and `Menlo.ttc` in `/System/Library/Fonts`, then `Courier New.ttf` in its `Supplemental` subdirectory |

Every candidate is **monospaced**, and the engine verifies it: in the resolved face,
every Latin-1 character whose glyph advances the pen must advance it by the same
amount, and a warning is recorded if they do not. Glyphs with zero advance are
not compared, since a combining mark correctly has none.

The range is part of the rule rather than a shortcut. Beyond Latin-1 a genuinely
monospaced face may carry zero-advance format characters, and may give a stray glyph
such as `€` an advance a fraction of a unit off the rest, so a check over the whole
`cmap` warns on faces that are fit for the purpose.

If nothing is found, resolution fails with an error naming the typeface and what
was tried. `bold` and `italic` are ignored at this step, since only regular faces
are named. A `.ttc` candidate resolves to the face in the collection whose style
bits say neither bold nor slanted — not to face 0, which is the same face in `Menlo.ttc`
but is not a rule collections keep.

A substitute is a guess, and text set in one may overlap rather than overflow
visibly. So the dependable signal that one was used is not the appearance
of the page: it is `resolvedBy: "substitute"` in the [printout
header](printout.md#fonts), together with a warning naming the typeface.
A reader that cares whether the output can be trusted tests that field.

### Missing glyphs

A resolved font may still lack a character the data or the template asks for. That
is not an error: the engine measures and the renderer draws the font's `.notdef`
glyph, which is visible as an empty box, and records a warning in the printout
header naming the character, the font, and the node. Text keeps its metrics, so
nothing shifts.

Under `--strict-fonts` the pinned file is the only one considered, which makes this
the likelier failure — a template using `…` or `—` needs a font that has them.

## Validation

Validation runs once, at load, before any data is read. It checks:

- Node nesting and cardinality.
- Required properties present; property values in range for their type.
- Exactly one of `group` / `detail` at each level that takes them.
- `layout` has `pagesize`, or both `width` and `height`.
- Unique names within a namespace: `parameter`, `variable`, `group`, `font`,
  `data`, `embedded`. An `embedded` layout is a separate namespace for parameters,
  records, variables, and groups.
- No `parameter`, `variable`, or `group` name collides with a
  [predefined name](expressions.md#predefined-variables), a module, or a builtin,
  since resolution puts those first and the declaration would be unreachable.
  For a `group` the derived names count too: a group may not be called `PAGE`
  or `DATA`, because `PAGE_COUNT` and `DATA_COUNT` already exist.
- Every `style font=` names a defined `font`.
- Every `itergrp` / `resetgrp` names a defined `group`, and every `evaltime` names
  `report`, `page`, `column`, or a defined group.
- `FINAL` appears **only in the `expr` of a `field` or `barcode` that has
  an `evaltime`**, and such an element's `expr` names `FINAL` at least once.
  Each without the other is a mistake rather than a no-op — the first has no scope to
  read from, the second defers an expression that would give the same answer in place.
- `FINAL` in any other property is an error, including in the same element's
  `printwhen` or a `style when` beneath it. `expr` is the only property whose
  evaluation is deferred; everything else on the element is evaluated when the band
  is measured, which is before the scope ends, so there would be nothing for `FINAL`
  to bind to.
- Every name used as `FINAL.`*name* is a predefined variable or a declared
  `variable`. Parameters and bare record fields are not in `FINAL`.
- Every `xref type="outline"` has a reachable target `outline name=`.
- Per axis, at most two of `left`/`right`/`width` and at most two of
  `top`/`bottom`/`height`.
- [Content sources](#content-sources) are consistent: on a `field` or `barcode`
  without `evaltime`, exactly one of `expr` / `text` / `data`; with `evaltime`,
  `expr` plus at most one placeholder. `evaltime` without `expr` is an error.
  On `image`, exactly one of `file` / `data` / `content`; on `data`, exactly
  one of `expr` / `content`.
- At most one of `default` / `defaultexpr` on a `parameter`, and any `default`
  parses as its declared `type`.
- `format` on a `parameter` or a `member` — where it is a date parsing layout —
  appears only when that node's type is `date` or `datetime`. `format` on a `field`
  or a `barcode` is a different property, a `%` format specification, and carries
  no such restriction; see [Formatting](expressions.md#formatting).
- Every `arg` names a `parameter` of the subreport it belongs to, once.
- Every deferred `stretch` field and every deferred `barcode` has a placeholder.
- Every `parameter` a subreport's layout requires has an `arg` or a default.
- `subreport` has exactly one of `template` / `embedded`, sits on a `detail`,
  on a `title` without `swapheader` or on a `summary` without `swapfooter`,
  and does not combine `inline` with `ownpageno`.
- An `inline` subreport's layout defines no `header`, no `footer`, no `swapheader`
  title and no `swapfooter` summary, and runs at the page size of the report that
  invokes it.
- No `subreport template=` chain reaches the template it started from.
- Every `subreport embedded=` names a layout in scope where it is written, and
  no two embedded layouts in one scope chain share a name.
- `image` does not combine `embed=#false` with `data` or a `content` child.
- `columns count` does not make the column width non-positive.
- Expressions parse. Name resolution is not checked at load, since undeclared
  record fields are reached dynamically.

It warns when a band that declares no `height` contains only elements whose
vertical extent is [container-dependent](layout.md#building-a-band), since
such a band collapses to zero height.

Every diagnostic names the file, the node path, and the property.
