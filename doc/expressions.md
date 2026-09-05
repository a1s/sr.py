# Expression environment

Template expressions are [Starlark](https://github.com/bazelbuild/starlark)
expressions. This document specifies the dialect, the names in scope,
the formatting mechanism, and variable accumulation semantics.

## Contents

- [Language](#language)
- [Determinism](#determinism)
- [Names in scope](#names-in-scope)
- [Predefined variables](#predefined-variables)
- [Modules and builtins](#modules-and-builtins)
- [The `decimal` type](#the-decimal-type)
- [Truth values](#truth-values)
- [Formatting](#formatting)
- [Variables](#variables)
- [Compilation](#compilation)

## Language

Starlark is Python-like, not Python. What matters for templates:

- **No `**` operator.** Use `math.pow(x, y)`.
- **No statements.** An expression is a single expression.
- **No recursion and no global reassignment.** Both are dialect options
  and both are left off.
- **No classes, no exceptions.** `fail(msg)` aborts with a message.
- **Integers are arbitrary precision.** `123456789012345678901234567890 + 1` is
  exact.
- **`/` is float division**, `//` is floor division. `1/3` is a float.
- **Strings are immutable** and indexable by byte; `.elems()` and `.codepoints()`
  iterate.
- **`set` is available**, which `calc="set"` uses. Sets are a dialect option
  in Starlark rather than part of the core language; the engine enables them
  explicitly and does not rely on the host default.
- **`%` interpolation has no flags, width, or precision.** See
  [Formatting](#formatting).

## Determinism

The same template over the same data produces the same printout. Two properties
guarantee it:

- **There is no clock.** `time.now` is not in scope. Use `BUILD_TIME`,
  which the engine sets once per run and a caller may set explicitly,
  making output bit-reproducible.
- **Iteration order is fixed.** Dictionaries preserve insertion order
  and `set` iterates in first-seen order.

## Names in scope

Resolution order, first match wins:

1. [Predefined variables](#predefined-variables) — `PAGE_NUMBER`, `THIS`,
   and the rest.
2. [Modules and builtins](#modules-and-builtins) — `math`, `time`, `format`, …
3. `parameter` names. A parameter's value has the type its
   [declaration](template.md#parameter) gives, whether it came
   from the caller as text, from `default`, or from `defaultexpr`.
4. `variable` names.
5. Declared fields of the current data record.

A record field shadowed by a variable of the same name is reachable as
`THIS.<name>`. Fields are always reachable that way, which is also how to
reach a field whose name is not a valid identifier, or one that
[`records`](template.md#records) does not declare.

```
amount                      # bare
THIS.amount                 # equivalent
customer.last_name          # nested object
film.title                  # nested object
THIS["odd name"]            # subscript
```

Nested objects come from nested JSON and are declared
`member ... type="object"`.

## Predefined variables

| Name | Type | Meaning |
|---|---|---|
| `THIS` | record | The current data record. |
| `ITEM_NUMBER` | int | 1-based index of the current record in the data sequence. Differs from `REPORT_COUNT` when details are suppressed by `printwhen`. |
| `DATA_COUNT` | int | Total number of records. |
| `REPORT_COUNT` | int | Detail sections printed since the start of the report. |
| `PAGE_COUNT` | int | Detail sections printed since the start of the page. |
| `COLUMN_COUNT` | int | Detail sections printed since the start of the column. |
| *group*`_COUNT` | int | Detail sections printed since the start of the named group. |
| `PAGE_NUMBER` | int | Current page number, 1-based. |
| `COLUMN_NUMBER` | int | Current column number, 1-based. |
| *group*`_PAGE_NUMBER` | int | Page number relative to the start of the named group, 1-based. |
| `VERTICAL_POSITION` | float, points | Distance from the top of the frame to where the section being measured begins. |
| `VERTICAL_SPACE` | float, points | Space from there to the frame's reserved bottom — what the section has left to grow into. |
| `BUILD_TIME` | time | When this run started. Constant for the whole run. |
| `FINAL` | namespace | Every name above, and every `variable`, read at the end of a scope instead of now. See [`FINAL`](#final). |

The names divide into two families. `DATA_COUNT` and `ITEM_NUMBER` describe
the **input**: how many records there are, and which one is current. Everything
ending in `_COUNT` other than `DATA_COUNT` describes the **output**: how many
detail sections have been *printed* since the start of some scope. So `DATA_COUNT`
is records and `REPORT_COUNT` is rows on paper, and they differ whenever a `printwhen`
suppresses a detail.

The `_COUNT` names therefore count detail sections, not pages. `PAGE_COUNT` is how
many detail rows this page has printed. For the number of pages, see
[`FINAL`](#final).

`DATA_COUNT` is a total and needs nothing special, because the engine buffers
the whole dataset before laying anything out — the same fact that makes
[keep-together](layout.md#keeping-content-together) lookahead possible. Totals
about the *output* are different: nothing knows how many pages there will be
until there are none left to make.

### `FINAL`

Every name in the table above, and every `variable`, is also reachable as
`FINAL.`*name*, which reads the value that name holds when a scope **ends**
rather than the value it holds now. Which scope is the element's
[`evaltime`](template.md#content-sources).

```kdl
field expr="'Page %d of %d' % (PAGE_NUMBER, FINAL.PAGE_NUMBER)" \
      evaltime="report" text="Page 999 of 999"
```

`PAGE_NUMBER` is the page this field prints on. `FINAL.PAGE_NUMBER` is what
`PAGE_NUMBER` reaches by the end of the report, which is the number of pages.
There is no separate name for the page total: it is the page number at the end,
spelled that way.

The same form gives every other end-of-scope value, with no new names
for any of them:

| | |
|---|---|
| `FINAL.PAGE_NUMBER`, `evaltime="report"` | pages in the report |
| `FINAL.PAGE_COUNT`, `evaltime="page"` | detail rows on this page |
| `FINAL.customer_COUNT`, `evaltime="customer"` | rows in this customer's group |
| `FINAL.total_amount`, `evaltime="report"` | a report-scoped variable's final total |
| `FINAL.THIS.region`, `evaltime="page"` | a field of the last record on this page |

`FINAL` holds only names whose value changes as the report is built: the predefined
variables and the `variable` accumulators. A `parameter` is constant, and a record
field belongs to a record rather than to a scope — reach one through `FINAL.THIS`.

`FINAL` lives in **`expr` and nowhere else.** `expr` is the only property whose
evaluation `evaltime` defers; a `printwhen`, a `style when`, an `outline title`
and the rest are all evaluated when the band is measured, which is before the scope
ends, so `FINAL` in one of those would have nothing to bind to. Deciding whether to
print a band from a value that does not exist yet is not something the engine can do,
and writing it is a [validation error](template.md#validation) rather than a value
that quietly reads as `None`.

`FINAL` and `evaltime` require each other. `FINAL` in the `expr` of an element with
no `evaltime` has no scope to refer to; an `evaltime` whose `expr` never mentions
`FINAL` defers an expression that would give the same answer either way. Both are
validation errors.

Mechanics — when the substitution happens, and what it costs — are
in [layout.md](layout.md#deferred-evaluation).

`VERTICAL_POSITION` and `VERTICAL_SPACE` are read at
[measurement](layout.md#measure-decide-commit) time, so they describe the frame
the section is being tried against. If the section does not fit and ejects,
it is re-measured in the new frame and both names read the new values.
A band whose expressions reference either is not
[cached](layout.md#measure-decide-commit), because the cache key is
content and width, which do not capture a dependence on position.

## Modules and builtins

### Starlark builtins

Available: `abs` `all` `any` `bool` `bytes` `chr` `dict` `dir` `enumerate` `fail`
`float` `getattr` `hasattr` `hash` `int` `len` `list` `max` `min` `ord` `range`
`repr` `reversed` `set` `sorted` `str` `tuple` `type` `zip`.

`print` is not available — a template has nowhere to print to.
There is no `round` builtin; use `math.round` or [`format`](#formatting).

List and dict comprehensions, conditional expressions (`a if c else b`),
and slicing are all available.

### Methods

Every value in scope is **frozen**: records, parameters, and variable
accumulators cannot be mutated from an expression. The mutating methods below
exist but fail on a frozen receiver, so in practice only the query methods
are usable.

| Type | Methods |
|---|---|
| string | `capitalize` `codepoint_ords` `codepoints` `count` `elem_ords` `elems` `endswith` `find` `format` `index` `isalnum` `isalpha` `isdigit` `islower` `isspace` `istitle` `isupper` `join` `lower` `lstrip` `partition` `removeprefix` `removesuffix` `replace` `rfind` `rindex` `rpartition` `rsplit` `rstrip` `split` `splitlines` `startswith` `strip` `title` `upper` |
| list | `index` — plus the mutating `append` `clear` `extend` `insert` `pop` `remove` |
| dict | `get` `items` `keys` `values` — plus the mutating `clear` `pop` `popitem` `setdefault` `update` |
| set | `difference` `intersection` `issubset` `issuperset` `union` — plus the mutating `add` `clear` `discard` `pop` `remove` `symmetric_difference`. There is no `update`; sets also support `\|` `&` `-` |
| bytes | `elems` |

A `list` or `set` variable is frozen too, so build a new value rather than
extending one:

```
', '.join(sorted(set(all_tags)))
```

### `math`

`ceil` `floor` `round` `mod` `pow` `sqrt` `fabs` `exp` `log` `hypot` `copysign`
`remainder`, the trigonometric and hyperbolic functions, `degrees`, `radians`,
`gamma`, and the constants `pi` and `e`.

### `time`

Constructors: `time.time(year=, month=, day=, hour=, minute=, second=,
nanosecond=, location=)`, `time.parse_time(s, format=, location=)`,
`time.from_timestamp(sec, nsec=)`, `time.parse_duration(s)`.

`time.now` is **not** available; see [Determinism](#determinism).
`time.is_valid_timezone(name)` is.

A time value has `.year .month .day .hour .minute .second .nanosecond .unix
.unix_nano`, plus `.in_location(name)` and `.format(layout)`.

Times compare with `<` `<=` `==` and so on. Subtracting two times gives a
**duration**; adding a duration to a time gives a time. A duration has `.hours
.minutes .seconds .milliseconds .microseconds .nanoseconds`, all floats, and the
module supplies the constants `time.hour` `time.minute` `time.second`
`time.millisecond` `time.microsecond` `time.nanosecond` to build them from.

```
(period_end - period_start).hours / 24        # days in the period
rental_date + 3 * time.hour
```

`.format` takes a **Go reference-time layout**:

```
rental_date.format("02.01.2006")        # → "24.05.2005"
```

For the familiar directives, use `strftime`.

### `strftime`

```
strftime(t, spec) -> string
```

Formats a time using `%Y %y %m %d %H %M %S %j %B %b %A %a %p %I %Z %z %%`.
Locale-independent — month and day names are English.

```
strftime(rental_date, '%d.%m.%Y')
```

### `format`

See [Formatting](#formatting).

## The `decimal` type

A member declared `type="decimal"` produces `decimal` values,
and the type is available to expressions.

```
decimal("19.99")            # from a string
decimal(5)                  # from an int
```

Arithmetic:

- `+` `-` `*` between decimals, or a decimal and an int, are **exact**.
  The result's scale is what exactness requires: adding two 2-place values
  gives 2 places, multiplying them gives 4.
- `/` between decimals produces a decimal quantized to 6 fractional digits,
  rounding half away from zero. Use `quantize` for a different scale.
- Comparisons between decimals are exact, and `min`, `max` and `sorted` follow
  them. A comparison **between a decimal and an int or a float** is not
  available: an ordered comparison raises, and `==` is false however the values
  compare. This is the host dialect's rule for two unrelated types rather than
  a choice, so write `amount > decimal("0")`, not `amount > 0`.
- **Mixing a decimal with a float is an error.** Convert deliberately with
  `float(d)` or `decimal(str(f))`.

Helpers:

```
quantize(d, places)         # round to `places` fractional digits, half away from zero
float(d)                    # explicit, lossy
str(d)                      # plain decimal text, no exponent
int(d)                      # truncates toward zero
```

`calc="sum"`, `"avg"`, `"min"`, `"max"` over decimals produce decimals;
`avg` quantizes like `/`. `"std"` and `"var"` produce floats.

## Truth values

`printwhen`, `style when`, `eject when`, and `outline when` take an expression
and test it for truth. The rules:

| Value | True when |
|---|---|
| `None` | never |
| bool | it is `True` |
| int, float | non-zero |
| `decimal` | non-zero |
| string, list, dict, set | non-empty |
| time | it is not the **zero time** (year 1, January 1, midnight UTC) |
| duration | non-zero |
| record | it is not `None` — an empty record is still true |

The time rule matters for the common "is this field filled in?" test. A JSON
`null` in a `nullable` member becomes `None`, which is false, so this suppresses
the field when there is no return date:

```kdl
field expr="strftime(return_date, '%d.%m.%Y')" printwhen="return_date"
```

A stored timestamp that happens to *be* the zero time is also false. Where that
distinction matters, test for it exactly:

```kdl
field expr="…" printwhen="return_date != None"
```

A JSON `null` in a member that is **not** `nullable` is an error naming the
member and the record index, not a silent `None`.

## Formatting

Numeric presentation happens in the engine, not inside expressions:
Starlark's `%` operator supports no flags, width, or precision,
and `.format()` rejects format specs.

### The `format=` property

`field format=` and `barcode format=` are applied by the engine to the
expression's result, using a `%`-style specification with the full set
of flags, width, and precision:

```kdl
field expr="amount" format="%.2f"
field expr="ITEM_NUMBER" format="%3d."
field expr="(customer.last_name, customer.first_name, customer_amount)"
      format="Total for %s, %s: %.2f"
```

A tuple result spreads across the conversions positionally;
the count must match exactly. The default is `"%s"`.

Supported conversions: `%s` `%q` `%d` `%i` `%o` `%x` `%X` `%b` `%e` `%E` `%f` `%g`
`%G` `%c` `%%`, each accepting the flags `-` `+` `#` `0` `' '`, a width, and a
precision. `%q` is a quoted string; `%i` is an alias for `%d`. There is no `%r`.

`%d` and the float conversions accept `decimal` values and format them exactly:
`%.2f` on a decimal rounds half away from zero without going through a float.

`%s` on a float uses the shortest representation that round-trips, so `1.0/3`
renders as `0.3333333333333333`. Give a precision.

### The `format` builtin

The same formatter, callable from an expression:

```
format("%.2f", amount)
format("%s, %s", customer.last_name, customer.first_name)
```

Use this rather than `%` whenever precision is involved.

### Starlark's own `%`

Available for plain conversions:

```
'%s, %s' % (customer.last_name, customer.first_name)
```

Supported: `%s` `%r` `%d` `%i` `%o` `%x` `%X` `%e` `%f` `%g` `%E` `%G` `%c` `%%`,
with no flags, width, or precision. The argument count must match.

Prefer `format`.

## Variables

A `variable` folds a value into an accumulator as records are consumed.

```kdl
variable "total_amount"    expr="amount" calc="sum"   reset="report"
variable "row_number"      expr="1"      calc="sum"   reset="page"
variable "customer_amount" expr="amount" calc="sum"   reset="group" resetgrp="customer"
variable "first_title"     expr="film.title" calc="first" reset="group" resetgrp="customer"
```

### `calc`

| `calc` | Result | Retains values |
|---|---|---|
| `first` | the first value folded since the last reset | no |
| `last` | the most recent value | no |
| `count` | number of values folded | no |
| `sum` | sum | no |
| `avg` | sum ÷ count | no |
| `min` / `max` | extremum | no |
| `std` / `var` | sample standard deviation / variance, as floats | no |
| `list` | all values, in order | **yes** |
| `set` | distinct values, in first-seen order | **yes** |
| `chain` | concatenation of values, each of which must be a sequence | **yes** |

Only `list`, `set`, and `chain` retain individual values. Everything else is an
incremental accumulator with constant memory.

An empty accumulator reads as `0` for `count`; `None` for `first`, `last`, `sum`,
`avg`, `min`, `max`, `std`, `var`; and an empty `list`, `set`, or `chain`
otherwise. `sum` of nothing is `None` rather than `0`, so "no rows" stays
distinguishable from "rows summing to zero" — write `total_amount or 0`
where the distinction does not matter.

The guard is not an edge case. A page header and footer are
[measured when the frame begins](layout.md#headerfooter-reservation),
which on the first page is before any record has been consumed, so
a footer that prints a running total reaches an empty accumulator
every time. Both reference templates need it.

`std` and `var` are **sample** statistics, dividing by *n*−1, so they are `None`
for a single value as well as for none at all. A summary line that prints them
needs a guard, or a `printwhen` on the field:

```kdl
field expr="format('std %.2f, var %.2f', total_std, total_var)" \
      printwhen="total_std != None"
```

### `iter` and `reset`

`iter` says when `expr` is evaluated and folded in.
`reset` says when the accumulator is cleared.

| Value | Fires |
|---|---|
| `report` | once, at the start (`iter`) or end (`reset`) of the report |
| `page` | at each page boundary |
| `column` | at each column boundary |
| `group` | at each break of the group named by `itergrp` / `resetgrp` |
| `detail` | once per printed detail section |
| `item` | once per data record, whether or not its detail prints |

`detail` is the default for `iter`, `report` for `reset`.

`detail` and `item` differ for records whose detail is suppressed by `printwhen`:
`iter="detail"` skips them, `iter="item"` counts them.

`init`, if given, is evaluated at each reset and folded in as the first value.

### Ordering against section printing

Within one record, the order is:

1. `THIS` and `ITEM_NUMBER` advance to the new record.
2. Group expressions are evaluated outermost-first; the first that changed
   determines the break level, and all groups nested inside it also break.
3. For each breaking group, innermost-first: its `summary` prints, using
   the **previous** record's values.
4. Variables reset for the scopes that just ended.
5. For each breaking group, outermost-first: variables iterate for that scope,
   then its `title` prints.
6. The `detail` section's variables iterate, then the detail prints.

Step 3 precedes step 4, which is what lets a group summary print that group's
own total.

A detail section that turns out not to fit and is deferred to the next frame has
its variable fold rolled back and reapplied after the eject, so a value is never
counted twice.

### The report boundary

`iter="report"` fires once, before the `title` band is built, so a report-scoped
value seeded by `init` is available to the title.

At the other end the order is: the last record's detail is committed, every
remaining group summary is built innermost-first, the report's `summary` band
is built, and only then do `reset="report"` variables clear. So the `summary` band
reads the final report totals. The reset is nominal — nothing is built after it.

### Inside a subreport

A subreport has its own variables, and its scopes are its own:

- `report` means **one invocation** of the subreport. A `reset="report"`
  variable starts empty for each invocation, which is how a line-item total
  resets per invoice.
- `page` and `column` mean the boundaries of whatever pages the subreport's bands
  land on. For a non-inline subreport those are its own pages; for an
  [inline](layout.md#subreports) one they are the parent's,
  since it shares the parent's pagination.
- `group` and `detail` mean the subreport's own groups and detail band.

The predefined names are the subreport's own too, with two exceptions. `THIS`,
`ITEM_NUMBER`, `DATA_COUNT`, `REPORT_COUNT`, `PAGE_COUNT` and `COLUMN_COUNT`
count the subreport's own records: `DATA_COUNT` is the length of the sequence
this invocation was given, and `PAGE_COUNT` is how many of its rows printed on
the current page. `PAGE_NUMBER` and `COLUMN_NUMBER` are the document's, so a
subreport's page header prints the number the reader sees on the paper -- unless
the subreport node asked for [`ownpageno`](layout.md#pages), which is the whole
point of that property. `BUILD_TIME` is the document's.

A subreport's parameters come from its `arg` nodes, evaluated in the **host's**
context. So an `arg` reads the host's names and the subreport reads its own, and
`arg "heading" value="film.title"` is how a value crosses between them. Nothing
else does: the host's variables and record fields are not in scope inside the
subreport, and the subreport's are not in scope outside it.

An expression in a subreport that has to react to where its bands landed --
`VERTICAL_POSITION`, `VERTICAL_SPACE` -- reads the frame it is printing in,
which under `inline` is the host's.

Every deferral a subreport registers resolves when its invocation ends, or
earlier if a page it is printing on ends first. `FINAL` in a subreport therefore
reads the subreport's own scope, never the host's; a deferred value that has to
read the host's final page state belongs on a host band.

## Compilation

Each expression is parsed and compiled once, at template load, into a function
whose parameters are the names it references:

```
# expr="amount * qty + math.floor(rate)"
def _e(amount, qty, rate):
    return amount * qty + math.floor(rate)
```

Per evaluation the engine calls it with just the values that expression needs, so
cost does not scale with the number of columns in the data. Throughput is roughly
0.26 µs per evaluation on one core.

Starlark resolves names at compile time, so every name an expression can reference
must be known before compilation. That is what the
[`records`](template.md#records) declaration provides.
A field not declared there is reachable only as `THIS["name"]`.

### Error reporting

A compile error names the template file, the node path, the property,
and the position within the expression. A runtime error — a missing field,
a type mismatch, `fail()` — additionally names the record index and the section
being built.
