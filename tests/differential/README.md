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

Both engines are here now: `sr.py build` writes a printout from M6 on,
so every case is built twice and compared. The harness' own tests still
run the reference against itself, which exercises the build, the argument
construction, the output paths and the byte comparison, and checks the
reproducibility `doc/cli.md` promises.

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

## The pending list

A second list, [pending.toml](pending.toml), holds the cases this engine
cannot build **yet**: work a later milestone brings rather than a decision
either engine has to catch up with. The two rules are the same two, and so
is the reason for them.

It is read only where the divergence register says nothing, and a case may
not be in both -- one expected failure with two reasons is a reason nobody
reads. Unlike a divergence, a pending entry may cover an example: the two
example reports use every node in the format, so they cannot pass until
the last of those nodes is built.

Each entry names the milestone that retires it, and the suite fails
the moment the case starts agreeing, so a milestone that lands takes
its entries with it.

## The one field byte-identity ran into

A printout header carries `engine`, which
[doc/cli.md](../../doc/cli.md#sr-version) describes as the version stamped
into the binary, "so an artifact and the binary that made it can be matched
up". Two implementations cannot both write that field truthfully *and* agree
byte for byte.

M6 settled it, in [doc/printout.md](../../doc/printout.md#the-engine-field):
the field names the engine **this specification** describes, so both write
`sr 0.1.0` and the comparison needs no exception. The cost is stated there
rather than hidden here: a printout does not record which implementation
produced it, only which version of the format and engine did.
