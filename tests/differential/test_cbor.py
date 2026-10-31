"""The CBOR encoding, compared byte for byte over part of the corpus.

doc/printout.md#cbor writes the same records as NDJSON, so what
the NDJSON comparison settles about a printout holds here too,
and what is left to compare is the encoding: which numbers are
integers, how wide a float is, how a string and a map are framed.
The cases below between them carry every field a printout has, so
building every case a second time would add time and nothing else.

A case keeps its identifier, so the divergence register and
the pending list cover it as they cover its NDJSON build: a case
that differs for a reason doc/ records differs in either encoding.

Two things of the reference's are set right before the comparison.
One is the image `type` the NDJSON comparison drops as well.
The other is an empty `groupRuns` or `groupKeys`: doc/printout.md
leaves both out while no group has opened, which the reference does
in NDJSON and not in CBOR, so comparing them would leave every case
without a group differing and hide whatever else differs in it.
The divergence `cbor-empty-group-tables` registers that, and
`cli/format/over-a-known-extension` keeps it visible, being the
one command line that writes a CBOR file and compares it whole.

There was a third until the reference began writing the three tables
whose keys are names, `data`, `groupRuns`, and `groupKeys`, in the
sorted order doc/printout.md gives them; sorting them here is no longer
needed, and nothing else is sorted, so what the comparison reads is
otherwise the bytes the reference wrote.

"""

from __future__ import annotations

import fnmatch
from dataclasses import replace
from pathlib import Path
from typing import Any

import cbor2
import pytest

from sr.printout.inspect import cbor_item, cbor_records
from tests.differential.cases import Case, corpus
from tests.differential.engines import Engine, run
from tests.differential.register import Register
from tests.differential.test_corpus import judge

# Between them: every mark kind and every field of each, crops, a file
# reference, embedded data, fonts from a file and from data, warnings with
# a record and without, the group tables, page overrides, nested xrefs,
# outline entries with and without names, and numbers at both ends.
CBOR_CASES = (
    "example/*",
    "probe/printout/*",
    "probe/outline/*",
    "probe/data/*",
    "probe/image/cut",
    "probe/image/natural",
    "probe/barcode/aztec",
    "probe/barcode/code128",
    "probe/pagination/overflow",
    "probe/pagination/groups",
    # Two groups, so the two group tables have an order to read off,
    # which :func:`test_the_reference_writes_its_cbor_tables_sorted` reads.
    "probe/deferred/nested-groups",
    "probe/subreport/paper",
    "probe/layout/xref",
)


def cbor_cases() -> list[Case]:
    """Return the cases built as CBOR."""
    return [
        replace(case, encoding="cbor")
        for case in corpus()
        if any(fnmatch.fnmatchcase(case.ident, one) for one in CBOR_CASES)
    ]


# The header's group tables, which are absent until a group has opened.
GROUP_TABLES = ("groupRuns", "groupKeys")


def as_compared(printout: bytes) -> bytes:
    """Return a reference CBOR printout with what is set right set right.

    Its image `type` fields are dropped, as
    :func:`~tests.differential.test_corpus.without_image_types` drops them
    from NDJSON, and so is an empty group table.  A record that needs
    either is decoded, changed, and encoded again; every other record
    keeps the bytes the reference wrote, so the encoding is compared
    wherever nothing had to change.

    """
    kept = b""
    start = 0
    while start < len(printout):
        record, end = cbor_item(printout, start, 0)
        changed = drop_image_types(record)
        changed = drop_empty_group_tables(record) or changed
        if changed:
            kept += cbor2.dumps(record)
        else:
            kept += printout[start:end]
        start = end
    return kept


def drop_empty_group_tables(record: Any) -> bool:
    """Remove a header's empty group tables, saying whether any went.

    doc/printout.md#header-line writes `groupRuns` and `groupKeys` once
    a group has opened and leaves them out while none has.  The reference
    leaves them out of its NDJSON and writes them empty in its CBOR,
    which is `cbor-empty-group-tables` in divergences.toml.  A table
    with anything in it is left alone, so a difference in what a group
    counted is still compared.

    Args:
        record: A record, which is changed only where it is a header.

    """
    if not isinstance(record, dict) or record.get("kind") != "header":
        return False
    dropped = False
    for name in GROUP_TABLES:
        if record.get(name) == {}:
            del record[name]
            dropped = True
    return dropped


def drop_image_types(value: Any) -> bool:
    """Remove `type` from every image mark inside a record, saying whether any went.

    Args:
        value: A record, or anything inside one.

    """
    dropped = False
    if isinstance(value, dict):
        if value.get("kind") == "image" and "type" in value:
            del value["type"]
            dropped = True
        for item in value.values():
            dropped = drop_image_types(item) or dropped
    elif isinstance(value, list):
        for item in value:
            dropped = drop_image_types(item) or dropped
    return dropped


@pytest.mark.differential
@pytest.mark.parametrize("case", cbor_cases(), ids=lambda case: case.ident)
def test_cbor_printouts_agree(
    case: Case,
    oracle: Engine,
    local: Engine,
    build_directory: Path,
    register: Register,
    pending: Register,
) -> None:
    judge(
        case,
        oracle,
        local,
        build_directory,
        register,
        pending,
        as_compared,
    )


# A case per name-keyed table whose header fills it with more than one
# key, since one key has no order and says nothing about the next.
# `key-order` carries three blobs whose names the reference's hash map
# yielded unsorted whichever of its rotations a run started from;
# `nested-groups` has two groups, so both group tables have two keys.
ORDERED = (
    ("probe/data/key-order", "data"),
    ("probe/deferred/nested-groups", "groupRuns"),
    ("probe/deferred/nested-groups", "groupKeys"),
)


@pytest.mark.differential
@pytest.mark.parametrize(("ident", "table"), ORDERED, ids=lambda one: one)
def test_the_reference_writes_its_cbor_tables_sorted(
    ident: str, table: str, oracle: Engine, build_directory: Path
) -> None:
    """What the comparison reads raw, held to account.

    The reference wrote these in its hash map's order until it began
    sorting them as doc/printout.md does.  Were it to go back, the
    comparison would pass or fail by chance, so this says which it is,
    for each of the three tables and not just the one that used to show
    it most readily.

    """
    (case,) = [one for one in cbor_cases() if one.ident == ident]
    built = run(oracle, case, build_directory)
    header = cbor_records(built.printout)[0]
    keys = list(header.get(table) or {})
    assert len(keys) > 1, (
        f"{ident} no longer fills `{table}` with more than one key, so this "
        "reads no order at all: point it at a case that does"
    )
    assert keys == sorted(keys), (
        f"the reference writes its CBOR `{table}` table unsorted again, so "
        "the comparison turns on its hash map's order: sort the reference's "
        "named tables in as_compared before holding it to this engine's"
    )


def test_the_cbor_cases_are_in_the_corpus() -> None:
    """Every pattern names something, so none is left behind by a rename."""
    idents = [case.ident for case in corpus()]
    for pattern in CBOR_CASES:
        assert fnmatch.filter(idents, pattern), f"{pattern} matches no case"


def test_only_an_image_marks_type_is_dropped_from_cbor() -> None:
    image = {"kind": "image", "box": {"x": 0}, "type": "png", "data": "wide.png"}
    barcode = {"kind": "barcode", "type": "Code128"}
    header = cbor2.dumps({"sr": 1, "kind": "header"})
    page = cbor2.dumps({"kind": "page", "marks": [image, barcode]})
    stripped = as_compared(header + page)
    assert stripped.startswith(header)
    rest = cbor2.loads(stripped[len(header) :])
    assert rest["marks"] == [
        {"kind": "image", "box": {"x": 0}, "data": "wide.png"},
        barcode,
    ]


def test_a_record_that_needs_nothing_keeps_the_bytes_it_came_in() -> None:
    header = cbor2.dumps(
        {
            "kind": "header",
            "groupRuns": {"b": 1, "a": 2},
            "data": {"z": {}, "a": {}},
            "page": {"width": 1.0, "height": 1.0},
        }
    )
    assert as_compared(header) == header


def test_only_an_empty_group_table_is_dropped() -> None:
    compared = cbor2.loads(
        as_compared(
            cbor2.dumps(
                {
                    "kind": "header",
                    "groupRuns": {},
                    "groupKeys": {"g": 1},
                    "data": {},
                }
            )
        )
    )
    assert compared == {"kind": "header", "groupKeys": {"g": 1}, "data": {}}


def test_a_page_keeps_a_table_a_header_would_lose() -> None:
    page = cbor2.dumps({"kind": "page", "groupRuns": {}})
    assert cbor2.loads(as_compared(page)) == {"kind": "page", "groupRuns": {}}
