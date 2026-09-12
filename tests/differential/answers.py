"""The answer a probe records, and how it is kept.

A probe exists to hold one question answered.  Comparing the two engines does
that from M6 on, but until then nothing pins what the reference said: a probe
that still *builds* passes, even if the oracle changed its mind about line
breaking overnight.  So each probe's printout is committed beside it, and
the reference is held to it on every run.

The golden is the printout as written, minus the one part of it that is not
a property of the build: a font's ``resolvedFile`` is relative to wherever
the printout landed, which is a temporary directory.  Everything else is
kept verbatim, including the spelling of every number -- ``0`` against
``0.0``, and an exponent against a written-out fraction, are exactly the
kind of difference a probe is here to catch, and parsing the JSON would
throw them away.

"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["ANSWER_SUFFIX", "answer_path", "distil"]

# Beside ``NAME.kdl``, its recorded answer.
ANSWER_SUFFIX = ".answer.jsonl"

# ``resolvedFile`` is relative to the output directory, so it says
# where the build happened rather than what the build decided.
RESOLVED_FILE = re.compile(r'("resolvedFile":)"(?:[^"\\]|\\.)*"')


def distil(printout: str) -> str:
    """Return the comparable part of a printout, as text.

    The substitution is deliberately textual.  Reading the JSON
    and writing it back would normalise the numbers, which is the one
    thing that must survive.

    """
    normalised = RESOLVED_FILE.sub(r'\1"<resolved>"', printout)
    return normalised.replace("\r\n", "\n").rstrip("\n") + "\n"


def answer_path(template: Path) -> Path:
    """Return where the probe at ``template`` keeps its recorded answer."""
    return template.with_name(template.name.removesuffix(".kdl") + ANSWER_SUFFIX)
