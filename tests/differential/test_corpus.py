"""The comparison itself: one test per case, both engines, byte for byte.

Every case in the corpus is built by the reference and by this engine
into one directory, and the two printouts are compared.  A case the
divergence register covers is allowed to differ, and is required to.
One field of the reference's is dropped before the comparison, the `type`
of an image mark, for the reason :func:`without_image_types` gives.
Its header is stamped with this engine's version in place of its own,
for the reason :func:`~tests.differential.diff.restamped` gives.

This engine's printout is also read back from the file it wrote and
held to the twelve [invariants](doc/printout.md#invariants), which is
what doc/printout.md means by asserting them on every printout the
test suite produces: the engine checks the document before it writes it,
and this checks what the writer made of it.

"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from sr.printout.inspect import read_printout
from sr.printout.verify import violations
from tests.differential.answers import answer_path, distil
from tests.differential.cases import REFUSED, ROOT, Case, corpus, probe_cases
from tests.differential.diff import compare_printouts, restamped
from tests.differential.engines import Build, BuildFailed, Engine, run
from tests.differential.register import Divergence, Register

# The `type` the reference writes on every image mark, right after the
# mark's box, which is flat.  doc/printout.md#image has no such field.
IMAGE_TYPE = re.compile(rb'("kind":"image","box":\{[^{}]*\}),"type":"[a-z]+"')


def without_image_types(printout: bytes) -> bytes:
    """Return a printout with the reference's image `type` fields dropped.

    The one field the comparison does not hold the reference to.
    This engine writes no format on an image mark, and the reference writes
    one on every image mark, so comparing it would leave every case with an
    image differing and hide whatever else differs in it.  The divergence
    `image-type-removed` registers the difference, and the probes that
    still give an image a `type` keep it visible: here that is an unknown
    property, and the warning about it reaches the printout.

    """
    return IMAGE_TYPE.sub(rb"\1", printout)


def attempt(engine: Engine, case: Case, directory: Path) -> Build | BuildFailed:
    """Build the case, returning the failure instead of raising it.

    A registered divergence can show itself as a refusal rather than
    as a difference in bytes -- an expression using a builtin one side
    does not have yet is exactly that -- so the failure has to reach
    the register before it reaches the report.

    """
    try:
        return run(engine, case, directory)
    except BuildFailed as failure:
        return failure


def expected(entry: Divergence, detail: str) -> None:
    """Mark the running test as an expected difference."""
    pytest.xfail(f"{entry.describe()}\n{detail}")


def agreed_on_a_refusal(
    case: Case,
    outcomes: dict[str, Build | BuildFailed],
    refused: dict[str, BuildFailed],
) -> None:
    """Hold a case whose answer is a refusal to both engines refusing it.

    A probe says ``!refused`` when that is its answer, and then neither
    engine may build it and both have to refuse it the same way.  Nothing
    else is compared: no document was written, and doc/cli.md leaves the
    wording of a diagnostic to each engine, so the exit code is the whole
    of what the two have in common.  A refusal with no code -- a timeout,
    or a run that reported success and wrote nothing -- is never that.

    Args:
        case: The case, which declared the refusal.
        outcomes: What each engine did.
        refused: The ones that refused, a subset of it.

    """
    built = sorted(set(outcomes) - set(refused))
    assert not built, (
        f"{case.ident}: its sidecar says both engines refuse it, and "
        f"{', '.join(built)} built it. Take {REFUSED!r} out of the sidecar "
        "and record the answer, or find out why the build came back."
    )
    detail = "\n".join(str(failure) for failure in refused.values())
    codes = {name: failure.code for name, failure in refused.items()}
    assert len(set(codes.values())) == 1 and None not in codes.values(), (
        f"{case.ident}: both engines refuse it, and not in the same way: "
        f"{codes}. doc/cli.md gives each exit code a meaning, so two "
        f"refusals that mean different things are not an answer.\n{detail}"
    )


def has_done_its_work(ident: str, listed: Divergence, registered: bool) -> None:
    """Fail, asking for the entry a comparison no longer needs.

    A listed case that stops differing is how both lists shrink by
    themselves: the entry is read, taken out, and the case joins the
    ones that simply agree.

    Args:
        ident: What was compared.
        listed: The entry that covered it.
        registered: Whether it came from the divergence register
            rather than from the pending list.

    """
    file = "divergences.toml" if registered else "pending.toml"
    word = "divergence" if registered else "pending entry"
    pytest.fail(
        f"{ident} now agrees, so the {word} {listed.ident!r} "
        f"has done its work: remove it from {file}",
        pytrace=False,
    )


def unbuilt(entry: Divergence, detail: str) -> None:
    """Mark the running test as a case this engine does not build yet.

    Worded apart from :func:`expected` because the two mean opposite
    things: a divergence is a decision one of the engines has not
    caught up with, and this is work that has not been done.

    """
    pytest.xfail(
        f"not built yet: {entry.ident}: {entry.summary}\n"
        f"  reason: {entry.reason}\n"
        f"  arrives with: {entry.ticket} (listed {entry.since})\n"
        f"{detail}"
    )


@pytest.mark.differential
@pytest.mark.parametrize("case", corpus(), ids=lambda case: case.ident)
def test_printouts_agree(
    case: Case,
    oracle: Engine,
    local: Engine,
    build_directory: Path,
    register: Register,
    pending: Register,
) -> None:
    judge(case, oracle, local, build_directory, register, pending)


def judge(
    case: Case,
    oracle: Engine,
    local: Engine,
    build_directory: Path,
    register: Register,
    pending: Register,
    without_types: Callable[[bytes], bytes] | None = None,
) -> None:
    """Build a case with both engines and hold the two printouts to each other.

    Args:
        case: What to build, in the encoding it names.
        oracle: The reference.
        local: This engine.
        build_directory: Where both write.
        register: The divergences.
        pending: What this engine does not build yet.
        without_types: What drops the reference's image `type` fields
            from its printout, for the case's encoding.

    """
    strip = without_types or without_image_types
    missing = case.missing_inputs()
    assert not missing, f"{case.ident}: missing input files: " + ", ".join(
        str(path) for path in missing
    )

    entry = register.entry_for(case.ident)
    waiting = None if entry is not None else pending.entry_for(case.ident)
    outcomes = {
        engine.name: attempt(engine, case, build_directory)
        for engine in (oracle, local)
    }
    refused = {
        name: outcome
        for name, outcome in outcomes.items()
        if isinstance(outcome, BuildFailed)
    }
    if case.refused:
        agreed_on_a_refusal(case, outcomes, refused)
        listed = entry if entry is not None else waiting
        if listed is not None:
            has_done_its_work(case.ident, listed, entry is not None)
        return
    if refused:
        detail = "\n".join(str(failure) for failure in refused.values())
        if entry is not None:
            expected(entry, detail)
        if waiting is not None:
            unbuilt(waiting, detail)
        pytest.fail(detail, pytrace=False)

    reference_build = outcomes[oracle.name]
    local_build = outcomes[local.name]
    assert isinstance(reference_build, Build)
    assert isinstance(local_build, Build)
    broken = violations(read_printout(local_build.path))
    assert not broken, f"{case.ident}: the printout breaks " + "; ".join(broken)

    theirs = restamped(reference_build.printout, oracle.version, local.version)
    comparison = compare_printouts(
        strip(theirs),
        local_build.printout,
        left_name=oracle.name,
        right_name=local.name,
        title=case.ident,
    )
    if entry is None and waiting is None:
        assert comparison.identical, comparison.report
        said = summary_of(reference_build), summary_of(local_build)
        assert said[0] == said[1], (
            f"{case.ident}: one printout, and two summary lines: "
            f"{said[0]!r} from the reference, {said[1]!r} here"
        )
        return
    listed = entry if entry is not None else waiting
    assert listed is not None
    if comparison.identical:
        has_done_its_work(case.ident, listed, entry is not None)
    if entry is not None:
        expected(entry, comparison.report)
    unbuilt(listed, comparison.report)


def summary_of(build: Build) -> str:
    """Return the summary line a build ended with, without its output's name.

    doc/cli.md#sr-build ends a build with one line on standard error,
    the output and then what the document holds.  Each engine writes
    a file of its own name, so the name is what is left out, and what
    two engines that built one document have to agree on is the rest.

    Args:
        build: What one engine produced.

    """
    lines = build.stderr.splitlines()
    last = lines[-1] if lines else ""
    return last.removeprefix(f"{build.path}: ")


@pytest.mark.differential
@pytest.mark.parametrize("case", probe_cases(), ids=lambda case: case.ident)
def test_the_reference_builds_every_probe(
    case: Case,
    oracle: Engine,
    build_directory: Path,
    register: Register,
) -> None:
    """Every probe still asks the oracle its question.

    A probe is a template whose answer is read out of the reference's
    printout, so one the reference will not build has stopped measuring
    anything -- and the comparison above would not notice, since it
    skips wherever the oracle is missing.  This holds the corpus up
    on the oracle alone.

    A refusal the register covers is the answer rather than a fault:
    two of the dialect amendments are expressions the reference has
    no name for, and refusing them is exactly the divergence recorded.
    So is one the probe's sidecar declared with ``!refused``, where being
    refused is the answer.  Anything else the reference will not build
    is a probe that has stopped measuring, a new one with a typo in it
    included, and this is what says so.

    """
    outcome = attempt(oracle, case, build_directory)
    if not isinstance(outcome, BuildFailed):
        assert not case.refused, (
            f"{case.ident}: its sidecar says both engines refuse it, and "
            f"the reference built it. Take {REFUSED!r} out of the sidecar "
            "and record the answer."
        )
        return
    entry = register.entry_for(case.ident)
    if entry is not None:
        expected(entry, str(outcome))
    if case.refused:
        return
    pytest.fail(str(outcome), pytrace=False)


@pytest.mark.differential
@pytest.mark.parametrize("case", probe_cases(), ids=lambda case: case.ident)
def test_the_reference_still_answers_the_same_way(
    case: Case,
    oracle: Engine,
    build_directory: Path,
    register: Register,
) -> None:
    """The oracle says today what it said when the probe was written.

    Building is not answering.  Until this engine produces printouts
    the comparison above skips, so a reference that changed its mind
    about line breaking would pass a suite that only checked it still ran.
    The answer committed beside each probe closes that window, and it is
    the answer itself -- the `lines`, the boxes, the `data` keys --
    so a diff in one of those files is a diff in what doc/ was written from.

    Record them again with ``python -m tests.differential.record_answers``,
    and read what changes before committing it.

    A probe the reference refuses has no answer to record, and that is
    the answer only where the register says why or where the sidecar
    says ``!refused``.  Either way the file beside it has to be gone,
    since a stale one would be a lie.

    """
    recorded = answer_path(ROOT / case.template)
    outcome = attempt(oracle, case, build_directory)
    if isinstance(outcome, BuildFailed):
        entry = register.entry_for(case.ident)
        assert entry is not None or case.refused, str(outcome)
        assert not recorded.exists(), (
            f"{case.ident}: the reference refuses this probe, so "
            f"{recorded.name} should not exist. It said:\n{outcome}"
        )
        if entry is not None:
            expected(entry, str(outcome))
        return
    assert recorded.is_file(), (
        f"{case.ident}: no recorded answer; run "
        "`python -m tests.differential.record_answers`"
    )
    assert isinstance(outcome, Build)
    printout = outcome.printout.decode("utf-8")
    got = distil(printout, oracle.version, case.host_fonts)
    want = recorded.read_text(encoding="utf-8")
    assert got == want, (
        f"{case.ident}: the reference no longer answers as recorded in "
        f"{recorded.name}. That is either a change in the oracle, which doc/ "
        "was written from, or a change to the probe. Re-record with "
        "`python -m tests.differential.record_answers` once you know which."
    )


@pytest.mark.differential
def test_corpus_is_not_empty() -> None:
    """The suite measures something.

    An empty corpus passes every comparison it has, which is none.

    """
    assert corpus(), "the differential corpus is empty"


def test_only_an_image_marks_type_is_dropped() -> None:
    image = b'{"kind":"image","box":{"x":0,"y":0,"width":24,"height":12},'
    barcode = b'{"kind":"barcode","box":{"x":0,"y":0,"width":9,"height":9},'
    printout = image + b'"type":"png","data":"wide.png"}' + barcode
    printout += b'"type":"Code128"}'
    assert without_image_types(printout) == (
        image + b'"data":"wide.png"}' + barcode + b'"type":"Code128"}'
    )


def refusal(name: str, code: int | None) -> BuildFailed:
    """Return a refusal as an engine's run would raise it."""
    return BuildFailed(f"{name} refused it", code)


def declared_refused() -> Case:
    """Return a case whose sidecar says both engines refuse it."""
    return Case(
        ident="probe/refused",
        template=Path("tests/differential/probes/values/negative-size.kdl"),
        refused=True,
    )


def test_two_refusals_with_one_exit_code_are_an_answer() -> None:
    refused = {"reference": refusal("reference", 1), "local": refusal("local", 1)}
    agreed_on_a_refusal(declared_refused(), dict(refused), refused)


@pytest.mark.parametrize(
    ("theirs", "ours"),
    [(1, 2), (2, 1), (1, None), (None, None)],
    ids=["one-two", "two-one", "no-code-here", "no-code-either-side"],
)
def test_two_refusals_that_mean_different_things_are_not(
    theirs: int | None, ours: int | None
) -> None:
    """doc/cli.md gives each exit code a meaning.

    A timeout and a run that wrote no file carry none, so neither is ever
    the same refusal as anything else, their own kind included.

    """
    refused = {
        "reference": refusal("reference", theirs),
        "local": refusal("local", ours),
    }
    with pytest.raises(AssertionError, match="not in the same way"):
        agreed_on_a_refusal(declared_refused(), dict(refused), refused)


def test_a_declared_refusal_one_engine_built_is_a_failure() -> None:
    """The declaration is held to, so it cannot go stale unnoticed."""
    case = declared_refused()
    theirs = refusal("local", 1)
    outcomes: dict[str, Build | BuildFailed] = {
        "reference": Build(
            engine=Engine("reference", (), "sr 0.1.0"),
            case=case,
            path=Path("out.srp.jsonl"),
            printout=b"",
            stdout="",
            stderr="",
        ),
        "local": theirs,
    }
    with pytest.raises(AssertionError, match="reference built it"):
        agreed_on_a_refusal(case, outcomes, {"local": theirs})
