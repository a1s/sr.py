"""Fixtures for the differential suite.

The oracle is built once per session.  Where it cannot be -- no Go
toolchain, no source tree next door -- every test that needs it skips
with the reason, so a machine without Go still runs the rest of the suite.

That is a convenience, and conveniences hide things.  ``--differential-required``,
or ``SR_DIFFERENTIAL_REQUIRED=1``, turns every one of those skips into a
failure; CI sets it, so a suite that measured nothing cannot pass there.

"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import pytest

from tests.differential.engines import (
    UNAVAILABLE,
    Engine,
    EngineNotImplemented,
    local_engine,
    reference_engine,
)
from tests.differential.reference import ReferenceBinary, build_reference
from tests.differential.register import Register, load_register

Product = TypeVar("Product")


# Spellings of "no" for SR_DIFFERENTIAL_REQUIRED, compared lowercased
# so that `False` reads as false rather than as a non-empty string.
DENIALS = frozenset({"", "0", "false", "no", "off"})


def required(config: pytest.Config) -> bool:
    """Report whether an unavailable engine must fail rather than skip."""
    if bool(config.getoption("--differential-required")):
        return True
    return os.environ.get("SR_DIFFERENTIAL_REQUIRED", "").strip().lower() not in DENIALS


def obtain(
    config: pytest.Config, label: str, produce: Callable[[], Product]
) -> Product:
    """Call ``produce``, turning an unavailable engine into a skip.

    The skip names which of the two is missing, because "reference
    unavailable" and "the Python engine does not build yet" are read
    very differently.

    ``--differential-required`` promotes that skip to a failure -- but
    only for an engine that is *meant* to be here.  An engine the plan
    has not reached raises :class:`EngineNotImplemented`, and that always
    skips: otherwise the flag could not be switched on until M6, and
    before that a missing oracle would go unguarded.

    """
    try:
        return produce()
    except EngineNotImplemented as pending:
        pytest.skip(f"{label} unavailable: {pending}")
    except UNAVAILABLE as unavailable:
        reason = f"{label} unavailable: {unavailable}"
    # Outside the handler, so the report is the reason
    # rather than the reason wrapped in a chained traceback.
    if required(config):
        pytest.fail(reason + " (and --differential-required is set)", pytrace=False)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def reference(pytestconfig: pytest.Config) -> ReferenceBinary:
    """Return the Go reference binary, built once.

    Go's own build cache makes the repeat build a no-op, so this is
    the cheapest correct way to keep the oracle in step with its source.

    """
    return obtain(pytestconfig, "reference", build_reference)


@pytest.fixture(scope="session")
def oracle(reference: ReferenceBinary) -> Engine:
    """Return the reference engine, ready to run a case."""
    return reference_engine(reference)


@pytest.fixture(scope="session")
def local(pytestconfig: pytest.Config) -> Engine:
    """Return this repository's engine, ready to run a case.

    Unavailable until the engine produces the first printout, at which point
    every comparison in this directory starts running instead of skipping.

    """
    return obtain(pytestconfig, "local engine", local_engine)


@pytest.fixture(scope="session")
def register() -> Register:
    """Return the known-divergence register."""
    return load_register()


@pytest.fixture
def build_directory(tmp_path: Path) -> Path:
    """Return a directory both engines write their printout into.

    One directory for the pair, because a printout's paths are
    relative to where it lands.

    """
    directory = tmp_path / "build"
    directory.mkdir()
    return directory
