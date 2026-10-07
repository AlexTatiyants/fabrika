"""The integrator's seam checks, and the check that a seam was checked.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Sequence

from ...gates import rule_for
from ...schemas import FileWrite, Finding, GateReport, Gate, IntegrationReport, TestFileCommand

from .gate_results import _raised_in


#: What an integration records when it left no seam check behind.
NO_SEAM_CHECK = "no seam check was written, so nothing shows the seams hold"


#: The gates an integration's seam checks become. A prefix, because the
#: project's own gates are named by the project and these must not collide.
SEAM_GATE = "seam-"


def settle_integration(integration: IntegrationReport, seam_marker: str) -> IntegrationReport:
    """Split the integrator's seam checks off its edits, and say what it amounts to.

    Decided from the files themselves, never from anyone's account of the
    session: a check is a file the integrator wrote whose NAME carries the
    marker, and the factory runs it. Asking afterwards what the integrator had
    run would go to a fresh model that was not in the session -- one answered
    "placeholder", and an integrator that had loaded the seam in a live
    browser was recorded as having checked nothing.

    By name rather than by directory because the directory is not ours. A
    seam check is an ordinary integration test in the project's own test
    tree, which it shares with the project and with every feature built here
    before this one; a directory this tool invented would hold checks nobody
    else's suite ever runs, and a check like that stops running the day the
    run ends.

    Editing nothing is not failing: the seams may already hold, and the
    executor's "the whole of integration" -- written for any unit whose harness
    edited no file -- is not a statement about them. Leaving no check is, and
    stays unresolved.
    """
    from ...executors import NO_EDITS

    integration.seam_files = [f for f in integration.files if is_seam_check(f.path, seam_marker)]
    integration.files = [f for f in integration.files
                         if not is_seam_check(f.path, seam_marker)]
    if not integration.files:
        integration.unresolved = [u for u in integration.unresolved
                                  if not u.startswith("the whole of integration")]
        integration.seam_issues = [f for f in integration.seam_issues if f != NO_EDITS]
    if not seam_checks(integration) and NO_SEAM_CHECK not in integration.unresolved:
        integration.unresolved.append(NO_SEAM_CHECK)
    return integration


def is_seam_check(path: str, seam_marker: str) -> bool:
    """Whether this path is one of the integrator's seam checks.

    Matched against the file NAME, case-insensitively, so `test_seam_tags.py`
    and `cardTags.Seam.test.ts` both count and a directory called `seams`
    somewhere in the project does not sweep in files nobody here wrote. A
    helper the integrator writes alongside a check leaves the marker off, and
    is then an ordinary edit like any other.
    """
    marker = (seam_marker or "").strip().lower()
    return bool(marker) and marker in PurePosixPath(path).name.lower()


def seam_checks(integration: IntegrationReport | None) -> list[FileWrite]:
    """The seam checks this integration left, in the order it wrote them."""
    if integration is None:
        return []
    return list(integration.seam_files)


def integration_detail(integration: IntegrationReport) -> str:
    """The integrator's line in the run, from what its record says."""
    parts = [f"{len(integration.files)} file(s) touched",
             f"{len(seam_checks(integration))} seam check(s)"]
    if integration.unresolved:
        parts.append(f"{len(integration.unresolved)} unresolved")
    return ", ".join(parts)


def seam_gates(
    integration: IntegrationReport | None,
    rules: Sequence[TestFileCommand] = (),
) -> list[Gate]:
    """The integrator's seam checks, as gates run beside the project's own.

    Run every round, like the breaker's probes, so a repair that breaks a seam
    the integrator showed holding is a regression and not a surprise.

    Each one is invoked by the project's OWN rule for running a single test
    file -- the same `test_file_commands` the blind suite is attributed with,
    proposed by the surveyor and approved by a human at gate 0. Nothing here
    decides how to run a test. A command this tool chose -- `sh {path}`, say,
    for a file an agent wrote without being told which shell would run it --
    dies on a `sh`-is-dash image at the file's own `set -o pipefail` without
    testing anything, and ten identical failures reach a human as a BLOCKER
    about the feature.

    A check no rule matches gets no gate: there is no command for it, and
    fabricating one is exactly that bug.
    """
    gates: list[Gate] = []
    for f in seam_checks(integration):
        rule = rule_for(rules, f.path)
        if rule is None or not (rule.command or "").strip():
            continue
        gates.append(Gate(name=f"{SEAM_GATE}{PurePosixPath(f.path).stem}",
                          command=rule.command.replace("{path}", f.path)))
    return gates


def check_seams(integration: IntegrationReport, gates: GateReport,
                seam_marker: str = "", written: Sequence[str] = ()) -> list[Finding]:
    """A seam nothing checked, or a seam check that fails.

    Computed, and restated every round: a seam check that starts passing
    closes its finding, and one that starts failing opens it.

    `seam_marker` tells a record whose seam checks are real tests from an older
    one whose checks are `tests/seams/*.sh` scripts. Nothing runs those
    scripts, and counting them would be the worst outcome available: the
    guard below would see checks, find no failing gate because none of them
    ran, and report nothing at all -- a silent green over a seam nobody
    measured. Discounted here, so the absence is reported as an absence.

    `written` is the files this feature wrote that a repair may change. A
    failing seam check names the check itself, which a repair may not touch,
    and also every one of `written` its output raised an error in -- found the
    way `check_blind_helper_errors` finds a helper, by `path:line`. On one run
    the only file named was the check, the error was raised in
    `web/e2e/fixtures.ts:36`, and two rounds each sent a repairer to a file it
    was forbidden to write while the one that needed fixing was nobody's.
    """
    current = [f for f in seam_checks(integration)
               if not seam_marker or is_seam_check(f.path, seam_marker)]
    if not current:
        return [Finding(
            id="seams-unchecked",
            title="No check was left for the seams between the units",
            severity="major",
            category="integration",
            detail=(
                "The integrator left no seam check -- no test that exercises both "
                "sides of a seam together -- so this run has no evidence either way "
                "about whether the units fit. Each unit's own tests stop at its own side. "
                "This is not a claim that a seam is broken, or that the integrator did "
                "not look: only that nothing it did can be run again and shown."),
            evidence=integration.summary[-1500:],
            recommendation="Check the seams by hand before accepting, or build again.",
        )]
    failing = [g for g in gates.results
               if g.name.startswith(SEAM_GATE) and not g.passed and not g.skipped]
    if not failing:
        return []
    return [Finding(
        id="seams-failing",
        title="A check across the seams between the units fails",
        severity="blocker",
        category="integration",
        detail=(
            "The integrator wrote these to show the seams hold, and they do not pass "
            "on the branch:\n\n" + "\n".join(f"- `{g.command}`" for g in failing)),
        evidence="\n\n".join(
            f"$ {g.command}\n[exit {g.exit_code}]\n{(g.output_tail or '')[-800:]}"
            for g in failing)[:4000],
        files=[f.path for f in seam_checks(integration)
               if any(f.path in (g.command or "") for g in failing)]
        + _raised_in(written, [g.output_tail or "" for g in failing]),
        recommendation="Fix whichever side of the seam is wrong; the output says which.",
    )]
