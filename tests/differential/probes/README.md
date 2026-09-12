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
| `NAME.inc.kdl` | A template another probe pulls in. Not built on its own. |

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
or at an image in `example/`. `data/one/pic.png` and `data/two/pic.png` share
a base name and differ in content, which is what the generated-name probes
need; `data/two/copy-of-one.png` has one's bytes under the other's name.

## What is here

M1 filled this directory. Each probe's own header comment states the question
it isolates and the answer the reference gave, and names the section of `doc/`
that answer became.

| | |
|---|---|
| `breaking/opportunities` | which characters break a line |
| `breaking/whitespace` | what a break does to the whitespace at it |
| `breaking/trailing-space` | whether that whitespace counts toward the fit |
| `breaking/overlong` | a word wider than its box |
| `breaking/hard-break` | `U+000A`, from `text`, an expression and `format` |
| `breaking/codepoints` | the unit a cut falls between |
| `breaking/narrow` | boxes too narrow to wrap into, and boxes of zero width |
| `breaking/tolerance` | the comparison the fit test makes |
| `rounding/halfway` | which way a coordinate on the half goes |
| `rounding/negative` | the same question below zero |
| `data/blob-names` | the name an embedded image gets |
| `data/blob-collision` | a generated name that is already taken |
| `data/key-order` | the order of the header's `data` object |
| `data/declared-names` | a declared name against an identical file |
| `expressions/strings` | `len`, indexing and slicing over non-ASCII text |
| `expressions/string-methods` | the string type's iteration methods |
| `expressions/round` | the `round` builtin |
| `expressions/decimal-int` | comparing a decimal with an int |

The last five are [registered divergences](../divergences.toml) and are
*expected* to differ; the reference refuses two of them outright, which
the harness treats as a difference like any other. The other thirteen
must agree byte for byte once this engine builds them.
