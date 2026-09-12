"""Record each probe's answer from the reference.

Run it when a probe is added or deliberately changed::

    python -m tests.differential.record_answers

It rewrites the ``*.answer.jsonl`` files beside the probes, which are
then committed.  A change to one of those files in a diff is a change
to what the oracle says, and wants reading rather than waving through.

"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from tests.differential.answers import answer_path, distil
from tests.differential.cases import ROOT, probe_cases
from tests.differential.engines import BuildFailed, reference_engine, run
from tests.differential.reference import ReferenceUnavailable, build_reference


def main() -> int:
    """Rebuild every probe's recorded answer."""
    try:
        reference = build_reference()
    except ReferenceUnavailable as unavailable:
        print(f"the reference is needed to record answers: {unavailable}")
        return 1

    oracle = reference_engine(reference)
    written = refused = 0
    with tempfile.TemporaryDirectory() as scratch:
        for case in probe_cases():
            destination = answer_path(ROOT / case.template)
            try:
                build = run(oracle, case, Path(scratch))
            except BuildFailed:
                # A registered divergence can be a refusal; there is no
                # answer to record, and a stale file would be a lie.
                if destination.exists():
                    destination.unlink()
                    print(f"  removed {destination.name} ({case.ident} is refused)")
                refused += 1
                continue
            destination.write_text(
                distil(build.printout.decode("utf-8")),
                encoding="utf-8",
                newline="\n",
            )
            written += 1
    print(f"{written} answers recorded, {refused} probes refused by the reference")
    return 0


if __name__ == "__main__":
    sys.exit(main())
