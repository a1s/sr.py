"""Command line options for the test suite.

pytest only takes options from a conftest it loads at startup,
so this one sits at the root even though the option it adds
belongs to the differential suite in ``tests/differential/``.

"""

from __future__ import annotations

import pytest


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
