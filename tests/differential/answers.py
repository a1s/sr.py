"""The answer a probe records, and how it is kept.

A probe exists to hold one question answered.  Comparing the two engines
does that, but only while both agree: a probe both engines build the same
wrong way is a probe that says nothing, and so is one the comparison skips
because the oracle is not on this machine.  So each probe's printout is
committed beside it, and the reference is held to it on every run.

The golden is the printout as written, minus the one part of it that is not
a property of the build: a font's ``resolvedFile`` is relative to wherever
the printout landed, which is a temporary directory.  Everything else is
kept verbatim, including the spelling of every number -- ``0`` against
``0.0``, and an exponent against a written-out fraction, are exactly the
kind of difference a probe is here to catch, and parsing the JSON would
throw them away.

A probe built with ``!host-fonts`` has one more part that is a property
of the machine rather than of the build: which face the host offered as
the substitute.  Its name is replaced wherever it is written, in the font
table and in the warning that names it, and so is a collection index,
which one platform's substitute has and the others' do not.  The layout
does not move with the face, since leading is a multiple of the size,
as long as the probe keeps its text too short to wrap.

"""

from __future__ import annotations

import json
import re
from pathlib import Path

__all__ = ["ANSWER_SUFFIX", "answer_path", "distil"]

# Beside ``NAME.kdl``, its recorded answer.
ANSWER_SUFFIX = ".answer.jsonl"

# ``resolvedFile`` is relative to the output directory, so it says
# where the build happened rather than what the build decided.
RESOLVED_FILE = re.compile(r'("resolvedFile":)"(?:[^"\\]|\\.)*"')

# Where a face sits in a collection, which only a `.ttc` substitute has.
RESOLVED_INDEX = re.compile(r'"resolvedIndex":[0-9]+,')

# What a host's substitute face is called in a recorded answer.
SUBSTITUTE = "<substitute>"


def distil(printout: str, host_fonts: bool = False) -> str:
    """Return the comparable part of a printout, as text.

    The substitution is deliberately textual.  Reading the JSON
    and writing it back would normalise the numbers, which is the one
    thing that must survive.  The header is parsed only to learn which
    names to replace.

    Args:
        printout: The printout, as written.
        host_fonts: Whether the probe resolved fonts on the host,
            so that the substitute face's name is the machine's
            rather than the build's.

    """
    normalised = RESOLVED_FILE.sub(r'\1"<resolved>"', printout)
    if host_fonts:
        normalised = anonymised(normalised)
    return normalised.replace("\r\n", "\n").rstrip("\n") + "\n"


def anonymised(printout: str) -> str:
    """Return a printout with the host's substitute faces unnamed.

    Only the header line is touched, since the header is the only place
    the host's choice is written: the font table, and the warning that
    names the face.  There a name is replaced where it is a whole JSON
    string and where it is quoted inside one, as the warning quotes it.
    A page is left alone, so text that happens to spell the face's name
    is kept as the content it is.

    Args:
        printout: The printout, ``resolvedFile`` already replaced.

    """
    first, newline, rest = printout.partition("\n")
    header = json.loads(first)
    faces = {
        entry["resolvedFace"]
        for entry in header.get("fonts") or ()
        if entry.get("resolvedBy") == "substitute"
    }
    if not faces:
        return printout
    for face in sorted(faces, key=len, reverse=True):
        inner = json.dumps(face)[1:-1]
        first = first.replace(f'"{inner}"', f'"{SUBSTITUTE}"')
        first = first.replace(f'\\"{inner}\\"', f'\\"{SUBSTITUTE}\\"')
    return RESOLVED_INDEX.sub("", first) + newline + rest


def answer_path(template: Path) -> Path:
    """Return where the probe at ``template`` keeps its recorded answer."""
    return template.with_name(template.name.removesuffix(".kdl") + ANSWER_SUFFIX)
