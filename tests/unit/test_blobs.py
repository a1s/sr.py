"""The printout's `data` table: the names it gives, and what it writes.

Each rule of doc/printout.md#data on its own.  The differential suite
holds the same rules to the reference over the `data/` probes,
through whole reports; here the table is driven directly,
so that the order blobs are met in is the test's to choose.

"""

from __future__ import annotations

from sr.layout.blobs import DataTable

LOGO = b"logo bytes"
OTHER = b"other bytes"


def test_a_generated_name_is_the_base_name() -> None:
    table = DataTable({})
    assert table.generated("logo.png", LOGO) == "logo.png"


def test_a_taken_name_is_suffixed_after_the_whole_name() -> None:
    table = DataTable({})
    assert table.generated("logo.png", LOGO) == "logo.png"
    assert table.generated("logo.png", OTHER) == "logo.png-2"
    assert table.generated("logo.png", b"third") == "logo.png-3"


def test_identical_bytes_are_one_entry_whatever_they_are_called() -> None:
    table = DataTable({})
    assert table.generated("logo.png", LOGO) == "logo.png"
    assert table.generated("copy.png", LOGO) == "logo.png"
    assert table.generated("image", LOGO) == "logo.png"


def test_a_declared_name_is_reserved_whether_or_not_it_is_used() -> None:
    table = DataTable({"logo.png": b"reserved", "logo.png-2": b"also"})
    assert table.generated("logo.png", LOGO) == "logo.png-3"
    assert table.written(["logo.png-3"]) == {"logo.png-3": LOGO}


def test_a_declared_name_outranks_a_generated_one_in_either_order() -> None:
    first = DataTable({"aaa": LOGO})
    assert first.generated("pic.png", LOGO) == "aaa"
    assert first.named("aaa") == "aaa"
    second = DataTable({"aaa": LOGO})
    assert second.named("aaa") == "aaa"
    assert second.generated("pic.png", LOGO) == "aaa"


def test_two_declared_names_for_one_content_go_to_the_first_built() -> None:
    table = DataTable({"zz": LOGO, "aa": LOGO})
    assert table.named("aa") == "aa"
    assert table.named("zz") == "aa"


def test_a_generated_blob_takes_the_first_declared_holder() -> None:
    """Where none of them holds the entry yet, document order decides."""
    table = DataTable({"zz": LOGO, "aa": LOGO})
    assert table.generated("pic.png", LOGO) == "zz"
    assert table.named("aa") == "zz"


def test_a_name_stays_taken_when_its_entry_is_not_written() -> None:
    """doc/printout.md#data: names are taken when built, entries written when used."""
    table = DataTable({})
    assert table.generated("pic.png", OTHER) == "pic.png"
    assert table.generated("pic.png", LOGO) == "pic.png-2"
    assert table.written(["pic.png-2"]) == {"pic.png-2": LOGO}


def test_what_is_written_is_sorted_and_once() -> None:
    table = DataTable({"zebra": b"z"})
    table.generated("alpha.png", b"a")
    table.named("zebra")
    written = table.written(["zebra", "alpha.png", "zebra"])
    assert list(written) == ["alpha.png", "zebra"]
