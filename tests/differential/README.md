# The differential harness

Runs a template through the Go reference engine and through this one,
and compares the printout bytes.

The Go implementation is the **oracle**, not the design: its outputs settle
questions the specification leaves open, and its source is never read.
Where the two disagree and the specification does not say who is right,
the fix is a sentence in `doc/`, not a workaround here.

## Running it

```bash
pytest tests/differential
```

The reference is built once per session into `tmp/reference/`, from the
Go source tree found beside this repository. Go's own build cache makes
a repeat build a no-op, so the oracle is always in step with its source.

| | |
|---|---|
| `SR_GO_REPO` | The Go source tree, when it is not a sibling directory. |
| `SR_REFERENCE_BINARY` | A prebuilt binary, for a machine with no Go toolchain. |
| `SR_GO` | The `go` command, when it is not `go`. |
| `--differential-required`, `SR_DIFFERENTIAL_REQUIRED=1` | Fail instead of skipping when an engine is unavailable. |

Without a toolchain and without a tree, every comparison **skips** with
the reason rather than failing, so the rest of the suite still runs.
That is a convenience, and conveniences hide things, which is what
`--differential-required` is for: on a machine that is supposed
to have the oracle, a suite that measured nothing should not pass.
Run `make test-required` there.

The flag insists on an engine that is *meant* to be here, which is not
the same as every engine. An engine the plan has not reached raises
`EngineNotImplemented` and always skips -- otherwise the flag could not
be switched on until this engine is ready, and until then the thing it guards,
a missing oracle, would go unguarded. Once `sr.py` is committed nothing raises
that any more: an entry point that is absent or will not run is then a fault,
and the flag fails on it.

Until we produce the first printout here there is no second engine, and
the comparisons skip on that side instead. What runs today is the harness'
own tests, which include running the reference against itself: that
exercises the build, the argument construction, the output paths and the
byte comparison, and checks the reproducibility `doc/cli.md` promises.

## Cases

A case is one template, its data, its parameters, and the two flags that
make a build reproducible: `--build-time` fixed and `--strict-fonts` set.
The same case produces the argument list for **both** engines, so nothing
about the comparison can drift between them, and both write into one
directory, because a printout's paths are relative to where it lands.

The corpus is the reports in `example/`, plus every probe.

## Probes

A probe is one template that isolates one specification question.
Drop the files into `probes/` and they are compared from then on;
the convention is in [probes/README.md](probes/README.md).

## The divergence register

Some differences are there on purpose, because a decision has been taken
and only one engine has been changed yet. Those are written down in
[divergences.toml](divergences.toml), with the reason and the work
that retires each one.

- A registered case that still differs is reported, and does not fail.
- A registered case that **stops** differing fails, asking for the entry
  to be removed.

Without the register a real regression hides among the expected failures.
Without the second rule the expected failures never get cleaned up.
Every entry is a defect somewhere, so this is a list to shorten rather
than a place to file things.

No entry may cover a case in `example/`. Those are the broadest comparison
the corpus has, and excusing one excuses the whole report.

## One thing byte-identity will run into

A printout header carries `engine`, which
[doc/cli.md](../../doc/cli.md#sr-version) describes as the version stamped
into the binary, "so an artifact and the binary that made it can be matched
up". Two implementations cannot both write that field truthfully *and* agree
byte for byte. Either this engine writes `sr 0.1.0` as the reference does,
and a printout no longer says which implementation made it, or the field
differs by construction and the comparison must be told to ignore it.

The harness does not decide this: it reports `fields: engine` on the header
record, which is the answer arriving as a question. It belongs in `doc/`
when this engine first produces a header.
