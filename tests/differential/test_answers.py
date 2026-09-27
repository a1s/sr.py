"""Tests for how a probe's recorded answer is distilled from a printout.

The recorded answer is compared with the reference on every run, so what
it leaves out decides what a changed oracle can get away with.  Each case
here is one thing that must be left out, or one that must not be.

"""

from __future__ import annotations

from tests.differential.answers import SUBSTITUTE, distil

HEADER = (
    '{"sr":1,"kind":"header","fonts":['
    '{"name":"body","requested":"Nowhere","resolvedFile":"C:/Fonts/cour.ttf",'
    '"resolvedIndex":2,"resolvedFace":"Courier New","resolvedBy":"substitute"},'
    '{"name":"plain","resolvedFile":"x.ttf","resolvedFace":"Go",'
    '"resolvedBy":"explicit"}],'
    '"warnings":[{"kind":"font",'
    '"message":"set in the substitute face \\"Courier New\\""}]}'
)
PAGE = '{"kind":"page","number":1,"marks":[{"kind":"text","lines":["Courier New"]}]}'
PRINTOUT = HEADER + "\n" + PAGE + "\n"


def test_a_strict_answer_keeps_the_face_it_resolved() -> None:
    """Only the path is the machine's when fonts are named by file."""
    distilled = distil(PRINTOUT)
    assert '"resolvedFile":"<resolved>"' in distilled
    assert '"resolvedFace":"Courier New"' in distilled
    assert '"resolvedIndex":2' in distilled


def test_a_host_fonts_answer_names_no_substitute() -> None:
    distilled = distil(PRINTOUT, host_fonts=True)
    assert "Courier New" not in distilled.split("\n")[0]
    assert f'"resolvedFace":"{SUBSTITUTE}"' in distilled
    assert f'substitute face \\"{SUBSTITUTE}\\"' in distilled
    assert "resolvedIndex" not in distilled


def test_a_host_fonts_answer_keeps_what_is_not_the_substitute() -> None:
    """An explicit face stays named, and so does text that happens to match.

    The page's `lines` hold the substitute's name as content, not
    as the name of anything the host chose, and the answer keeps it.

    """
    distilled = distil(PRINTOUT, host_fonts=True)
    assert '"resolvedFace":"Go"' in distilled
    assert '"requested":"Nowhere"' in distilled
    assert distilled.split("\n")[1] == PAGE
