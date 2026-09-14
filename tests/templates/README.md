# Templates

One template per validation rule, and a few that are valid on purpose.

`broken/` is the corpus `sr.py validate` is held to: each file isolates
one rule of [template.md#validation](../../doc/template.md#validation)
and is otherwise a template that would load. `valid/` is the other half:
templates that must pass, including the ones that pass *with something
to say*.

## The convention

A template states its own expectations in its header comment, so that
the question and the answer are one file and adding a case is writing
a template rather than editing a table:

```kdl
// broken/no-page.kdl -- a layout that names neither a page size nor both extents.
//
// expect: a layout needs a pagesize, or both a width and a height
```

| | |
|---|---|
| `// expect: TEXT` | An error whose message contains `TEXT`. |
| `// warn: TEXT` | A warning whose message contains `TEXT`. |

The comment block ends at the first line that is not a comment, so the
markers have to be at the top.

A file in `broken/` must produce at least one error and must state
at least one `expect:`. A file in `valid/` must produce none, and must
produce exactly the warnings it says it does — no `warn:` line means
no warnings at all.

Every case is loaded with the default options, so a template carrying
an [unknown name](../../doc/template.md#unknown-names) belongs in `valid/`
however much `--strict-names` would refuse it. What that flag does is
a question about one load rather than about one template, and it is
asked in [`test_load.py`](../unit/test_load.py) and
[`test_cli.py`](../unit/test_cli.py) instead.

`NAME.inc.kdl` is a template another case pulls in with `subreport
template=`. It is not loaded on its own, because on its own it is valid:
what makes the pair a case is the relationship between them — a cycle,
or a page size that does not match its host's.

The driver is [`tests/unit/test_templates.py`](../unit/test_templates.py),
which also holds the two examples in `example/` to loading clean and
silent.

## What is here

Each file's first line says what it isolates. Between them they cover
the validation section's list, less the two rules that wait for the barcode
encoders in M10: what a symbology can encode, and whether an `ink` and
a `paper` can be read apart.
