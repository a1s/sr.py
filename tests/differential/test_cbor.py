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
The other is the order of the three tables whose keys are names,
`data`, `groupRuns`, and `groupKeys`: doc/printout.md sorts them,
and the reference sorts them in NDJSON and writes them in CBOR
in its hash map's order, which changes from one run to the next.
A comparison against that would pass or fail by chance, so the
reference's are sorted here, and
:func:`test_the_reference_still_writes_cbor_tables_unsorted` fails
the day that is no longer needed.

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


# The header's tables whose keys are names rather than fields.
NAMED_TABLES = ("data", "groupRuns", "groupKeys")


def as_compared(printout: bytes) -> bytes:
    """Return a reference CBOR printout with what is set right set right.

    Its image `type` fields are dropped, as
    :func:`~tests.differential.test_corpus.without_image_types` drops them
    from NDJSON, and its name-keyed tables are sorted.  A record that needs
    either is decoded, changed, and encoded again; every other record keeps
    the bytes the reference wrote, so the encoding is compared wherever
    nothing had to change.

    """
    kept = b""
    start = 0
    while start < len(printout):
        record, end = cbor_item(printout, start, 0)
        changed = drop_image_types(record)
        changed = sort_named_tables(record) or changed
        if changed:
            kept += cbor2.dumps(record)
        else:
            kept += printout[start:end]
        start = end
    return kept


def sort_named_tables(record: Any) -> bool:
    """Sort a header's name-keyed tables in place, saying whether any moved.

    Args:
        record: A record, which is changed only where it is a header.

    """
    if not isinstance(record, dict) or record.get("kind") != "header":
        return False
    moved = False
    for name in NAMED_TABLES:
        table = record.get(name)
        if isinstance(table, dict) and list(table) != sorted(table):
            record[name] = {key: table[key] for key in sorted(table)}
            moved = True
    return moved


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


@pytest.mark.differential
def test_the_reference_still_writes_cbor_tables_unsorted(
    oracle: Engine, build_directory: Path
) -> None:
    """The reason :func:`sort_named_tables` exists, held to account.

    Three blobs whose names the reference's hash map never yields
    in sorted order, whichever of its rotations a run starts from.

    """
    (case,) = [one for one in cbor_cases() if one.ident == "probe/data/key-order"]
    built = run(oracle, case, build_directory)
    header = cbor_records(built.printout)[0]
    assert list(header["data"]) != sorted(header["data"]), (
        "the reference now writes its CBOR `data` table sorted: drop "
        "sort_named_tables from test_cbor.py, and the note that explains it"
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


def test_a_headers_named_tables_are_sorted_and_nothing_else_is() -> None:
    header = {
        "kind": "header",
        "groupRuns": {"b": 1, "a": 2},
        "data": {"z": {}, "a": {}},
        "page": {"width": 1.0, "height": 1.0},
    }
    compared = cbor2.loads(as_compared(cbor2.dumps(header)))
    assert list(compared["groupRuns"]) == ["a", "b"]
    assert list(compared["data"]) == ["a", "z"]
    assert list(compared["page"]) == ["width", "height"]
