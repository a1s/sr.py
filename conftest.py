"""Command line options and per-test state for the whole suite.

pytest only takes options from a conftest it loads at startup,
so this one sits at the root even though the option it adds
belongs to the differential suite in ``tests/differential/``.

The host font table is kept for the life of a process, which in a test
run is every test one worker runs, so it is dropped here around each.

"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from sr.fonts.hostenum import forget_host_catalog


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add the flag that forbids skipping the differential comparisons."""
    parser.addoption(
        "--differential-required",
        action="store_true",
        default=False,
        help=(
            "fail instead of skipping when an engine the differential suite "
            "needs is unavailable"
        ),
    )


@pytest.fixture(autouse=True)
def nothing_kept() -> Iterator[None]:
    """Start and end every test with no host font table kept.

    A test that patches something enumeration calls would otherwise be
    handed a table an earlier test built, and pass or fail by the order
    the tests ran in.

    """
    forget_host_catalog()
    yield
    forget_host_catalog()
