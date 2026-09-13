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
| `NAME.args` | Parameters and extra flags. Optional. |
| `NAME.answer.jsonl` | The reference's printout, committed. Generated, not written. |
| `NAME.inc.kdl` | A template another probe pulls in. Not built on its own. |

`NAME.answer.jsonl` is what keeps a probe answered rather than merely
building. The suite rebuilds every probe with the reference and holds it
to that file, so an oracle that changed its mind about line breaking fails
here instead of passing unnoticed until M6. It is the printout as written --
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
to the engine as written; any other line is a `NAME=VALUE` parameter. Blank
lines and lines starting with `#` are ignored.

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
| `data/blob-names` | the name an embedded image gets |
| `data/blob-collision` | a generated name that is already taken |
| `data/key-order` | the order of the header's `data` object |
| `data/declared-names` | a declared name against an identical file |
| `expressions/strings` | `len`, indexing and slicing over non-ASCII text |
| `expressions/string-methods` | the string type's iteration methods |
| `expressions/round` | the `round` builtin |
| `expressions/decimal-int` | comparing a decimal with an int |
| `expressions/decimal-abs` | the one builtin that will not take a decimal |
| `expressions/time-fields` | what `time.time` does with the fields it is not given |
| `expressions/decimal-precision` | how far a decimal's digits survive a float conversion |
| `expressions/null-folds` | what a null does to an accumulator |
| `values/dimensions` | what a dimension string means |
| `values/colors` | what each colour spelling resolves to |
| `values/hex-float` | a number the host's parser takes and the grammar does not |
| `values/non-finite` | a dimension KDL can write and points cannot hold |

Ten are [registered divergences](../divergences.toml) and are *expected*
to differ: the eight in the `expressions/` and `data/` groups, where the
reference refuses four outright -- which the harness treats as a difference
like any other -- and `values/hex-float` and `values/non-finite`, which
go the other way, built by the reference and refused here.
`expressions/time-fields` and `expressions/decimal-precision` are the odd
ones: most of their rows agree, and one row each carries the difference --
a date before year 1, and a precision deeper than a float reaches.
The remaining twenty must agree byte for byte once this engine builds them.
