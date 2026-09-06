"""Tests for the corpus and for probe discovery.

M1 adds probes by dropping files into ``probes/``.  What that convention is
had better be tested, or the first probe that is silently not picked up
costs an afternoon.

"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.differential.cases import (
    BUILD_TIME,
    Case,
    corpus,
    example_cases,
    probe_cases,
)

TEMPLATE = Path("example/sakila/sakila.kdl")
DATA = Path("example/sakila/payments.jsonl")


def test_the_arguments_name_every_input(tmp_path: Path) -> None:
    out = tmp_path / "reference.srp.jsonl"
    arguments = Case("probe/one", TEMPLATE, DATA).argv(out)
    assert arguments[0] == "build"
    assert str(out) in arguments
    assert "--template" in arguments
    assert "--data" in arguments


def test_every_case_is_reproducible(tmp_path: Path) -> None:
    """The two flags that make byte-identity mean anything are always set."""
    arguments = Case("probe/one", TEMPLATE).argv(tmp_path / "out.srp.jsonl")
    assert "--strict-fonts" in arguments
    assert arguments[arguments.index("--build-time") + 1] == BUILD_TIME


def test_a_case_without_data_omits_the_flag(tmp_path: Path) -> None:
    arguments = Case("probe/one", TEMPLATE).argv(tmp_path / "out.srp.jsonl")
    assert "--data" not in arguments


def test_parameters_are_passed_in_order(tmp_path: Path) -> None:
    case = Case("probe/one", TEMPLATE, params=("first=1", "second=2"))
    arguments = case.argv(tmp_path / "out.srp.jsonl")
    assert arguments.count("--param") == 2
    assert arguments[arguments.index("--param") + 1] == "first=1"


def test_an_unknown_encoding_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown encoding"):
        Case("probe/one", TEMPLATE, encoding="xml")


def test_the_encoding_chooses_the_extension() -> None:
    assert Case("probe/one", TEMPLATE).suffix == ".srp.jsonl"
    assert Case("probe/one", TEMPLATE, encoding="cbor").suffix == ".srp.cbor"


def test_the_example_inputs_are_on_disk() -> None:
    for case in example_cases():
        assert not case.missing_inputs(), f"{case.ident}: inputs have moved"


def test_case_identifiers_are_unique() -> None:
    identifiers = [case.ident for case in corpus()]
    assert len(identifiers) == len(set(identifiers))


def probe_tree(directory: Path) -> None:
    """Lay out one of each kind of probe file."""
    (directory / "nested").mkdir(parents=True)
    (directory / "plain.kdl").write_text("report {}\n", encoding="utf-8")
    (directory / "nested" / "withdata.kdl").write_text("report {}\n", encoding="utf-8")
    (directory / "nested" / "withdata.jsonl").write_text("{}\n", encoding="utf-8")
    (directory / "nested" / "withdata.args").write_text(
        "# a comment\n\nperiod_start=2005-06-01\n--allow-overflow\n", encoding="utf-8"
    )
    (directory / "shared.inc.kdl").write_text("report {}\n", encoding="utf-8")


def test_probes_are_discovered(tmp_path: Path) -> None:
    probe_tree(tmp_path)
    found = {case.ident: case for case in probe_cases(tmp_path)}
    assert set(found) == {"probe/plain", "probe/nested/withdata"}


def test_a_probe_finds_its_data_and_arguments(tmp_path: Path) -> None:
    probe_tree(tmp_path)
    found = {case.ident: case for case in probe_cases(tmp_path)}
    withdata = found["probe/nested/withdata"]
    assert withdata.data is not None
    assert withdata.data.name == "withdata.jsonl"
    assert withdata.params == ("period_start=2005-06-01",)
    assert withdata.flags == ("--allow-overflow",)
    assert found["probe/plain"].data is None


def test_an_include_is_not_a_case_of_its_own(tmp_path: Path) -> None:
    """A template another probe pulls in is not built by itself."""
    probe_tree(tmp_path)
    assert all(case.ident != "probe/shared.inc" for case in probe_cases(tmp_path))


def test_a_missing_probe_directory_is_not_an_error(tmp_path: Path) -> None:
    assert probe_cases(tmp_path / "absent") == []
