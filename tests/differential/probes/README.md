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
| `NAME.jsonl` | Its records. Optional; omitted for a template that reads none. |
| `NAME.args` | Parameters and extra flags. Optional. |
| `NAME.inc.kdl` | A template another probe pulls in. Not built on its own. |

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

## What is here

Nothing yet. M1 fills this directory: about a dozen line-breaking probes,
two for the rounding mode, one for generated blob names, and one confirming
what the reference currently does with a non-ASCII string.
