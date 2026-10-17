"""Rendering, held to the reference's renderer, page by page.

Printouts are compared byte for byte; PDFs are not.  Two writers
lay a file out differently, and doc/render.md does not ask them
to agree on objects, subsets or compression.  What it does specify
is what a reader draws: where a baseline sits, how a justified line
spends its slack, what a dash measures, where a bar or an image lands.
So each page is drawn by pdfium, a reader neither engine wrote, and
the two drawings are compared pixel by pixel.

Each case's printout is built once, by this engine, and rendered
by both renderers, so a difference is between the renderers and
not between the engines that built the printouts.  The printout read
back from its file is what this renderer draws, which is the route
`sr.py render` takes, and which doc/render.md#reproducibility makes
the same as rendering the one in memory.

The comparison allows :data:`TOLERANCE` levels on any channel of any
pixel, and nothing more.  That is room for an edge whose path runs
the other way round, which pdfium shades a level differently, and
not for a glyph a hundredth of a point out of place.

A render the divergence register names, as ``render/`` and then the
case's identifier, is expected to differ, and fails once it does not.

"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import ImageChops

from sr.cli import main
from sr.errors import SrError
from sr.pdf import render
from sr.printout.read import read
from tests.differential.cases import Case, render_cases, rendered
from tests.differential.reference import ReferenceBinary
from tests.differential.register import Register
from tests.differential.test_corpus import expected
from tests.pdfcheck import check, pages

pytest.importorskip("pypdfium2")

# Pixels per point.  At 144 dots per inch a hundredth of a point
# is a fiftieth of a pixel, which still moves an edge's shade.
SCALE = 2

# How far apart two pixels may be on one channel, out of 255.
TOLERANCE = 2

# Generous, as for a build: a cold cache on a large case is slow.
RENDER_TIMEOUT = 300.0


@pytest.mark.differential
@pytest.mark.parametrize("case", render_cases(), ids=rendered)
def test_pages_look_the_same(
    case: Case,
    reference: ReferenceBinary,
    build_directory: Path,
    register: Register,
) -> None:
    ident = rendered(case)
    printout = build_directory / ("local" + case.suffix)
    if main(case.argv(printout)) != 0:
        pytest.skip(f"this engine does not build {case.ident}")
    entry = register.entry_for(ident)
    ours = theirs = None
    failures = []
    try:
        ours = render(read(printout))
        check(ours)
    except SrError as refused:
        failures.append(f"this renderer refused it: {refused}")
    target = build_directory / "reference.pdf"
    done = subprocess.run(  # the argv is ours, not a shell string
        [str(reference.path), "render", str(printout), "-o", str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=RENDER_TIMEOUT,
        check=False,
    )
    if done.returncode == 0:
        theirs = target.read_bytes()
    else:
        failures.append(f"the reference refused it: {done.stderr.strip()}")
    if ours is not None and theirs is not None:
        failures.extend(differences(ours, theirs))
    detail = f"{ident}:\n" + "\n".join(f"  {one}" for one in failures)
    if entry is None:
        assert not failures, detail
        return
    if not failures:
        pytest.fail(
            f"{ident} now agrees, so the divergence {entry.ident!r} "
            "has done its work: remove it from divergences.toml",
            pytrace=False,
        )
    expected(entry, detail)


def differences(ours: bytes, theirs: bytes) -> list[str]:
    """Return how two renders of one printout draw differently, page by page.

    Args:
        ours: This renderer's file.
        theirs: The reference's.

    """
    left, right = pages(ours, SCALE), pages(theirs, SCALE)
    if len(left) != len(right):
        return [f"{len(left)} pages here and {len(right)} in the reference"]
    found = []
    paired = zip(left, right, strict=True)
    for number, (one, two) in enumerate(paired, start=1):
        if one.size != two.size:
            sizes = f"{one.size} pixels here, {two.size} there"
            found.append(f"page {number}: {sizes}")
            continue
        # Each pixel's widest difference over its three channels.
        bands = ImageChops.difference(one, two).split()
        widest = bands[0]
        for band in bands[1:]:
            widest = ImageChops.lighter(widest, band)
        levels = widest.histogram()
        seen = [level for level, count in enumerate(levels) if count]
        worst = max(seen, default=0)
        if worst <= TOLERANCE:
            continue
        count = sum(levels[TOLERANCE + 1 :])
        far = widest.point(lambda level: 255 if level > TOLERANCE else 0)
        left_edge, top, right_edge, bottom = far.getbbox() or (0, 0, 0, 0)
        found.append(
            f"page {number}: {count} pixels differ by more than {TOLERANCE},"
            f" the most by {worst}, between {left_edge / SCALE},{top / SCALE}"
            f" and {right_edge / SCALE},{bottom / SCALE} in points"
        )
    return found
