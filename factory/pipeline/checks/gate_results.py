"""Checks on what the gates reported: tests that ran nothing, failures the
feature did not cause, tests that do not clean up after themselves.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Sequence

from ...schemas import Finding, GateReport, GateResult, OracleSuite, TraceRow

from .blind import blind_summary


def check_test_execution(gates: GateReport, trace: Sequence[TraceRow]) -> list[Finding]:
    """A green suite that executed nothing, or skipped the tests that mattered.

    Computed, because it is arithmetic on the runner's own summary and there is
    nothing for a model to judge. This is the failure the exit code cannot see:
    a criterion marked verified by a test that never ran is worse than a
    criterion marked untested, because it carries a claim.
    """
    test_gates = [g for g in gates.results if "test" in g.name.lower()] or list(gates.results)
    if not test_gates:
        return []
    output = "\n".join(g.output_tail for g in test_gates)
    summary = blind_summary(test_gates)
    [name for row in trace for name in row.test_names]
    findings: list[Finding] = []

    if summary.zero_ran and any(g.passed for g in test_gates):
        findings.append(Finding(
            id="execution-1",
            title="A check reported success without running any tests",
            severity="blocker",
            category="verification",
            detail=(
                "The gate exited zero and the runner reports that nothing ran. Every criterion "
                "this suite was supposed to verify is unverified, and the packet's green would "
                "have been an artefact of an empty run."
            ),
            evidence="\n".join(
                f"{g.name}: {g.tests_total} test file(s) run" for g in test_gates
            ) + "\n\n" + output[-1200:],
            recommendation="Fix the invocation or the collection path before reading anything else here.",
        ))

    # Skips are not detectable here, and saying so is better than a check that
    # looks for them and always finds none. A skipped test exits zero exactly as
    # a passing one does, and nothing here reads a runner's output. The
    # exposure is bounded by the oracle's own instruction never to skip or
    # `xfail` a criterion, and by `check_blind_attribution`, which reports what
    # this project's runner was measured to do when handed nothing.
    return findings


def check_attribution(gates: GateReport) -> list[Finding]:
    """A failing gate, and whether this feature caused it.

    Computed from two process runs, not asked for. The distinction is the whole
    reason the comparison exists: on a repository with pre-existing failures,
    every packet would otherwise carry the same red gates and no reviewer could
    tell which of them the feature was responsible for.
    """
    findings: list[Finding] = []
    caused = [g for g in gates.results if g.caused_here]
    pre = [g for g in gates.results if g.pre_existing]
    unknown = [g for g in gates.results
               if not g.passed and not g.skipped and g.at_base == "could_not_run"]

    for gate in caused:
        findings.append(Finding(
            id=f"broke-{gate.name}",
            title=f"This feature broke a check that used to pass — {gate.name}",
            severity="blocker",
            category="regression",
            detail=(
                f"{gate.name!r} passes at {gate.base_sha[:12]}, the commit this work branched "
                "from, and fails here. The same command, in the same image, against the same "
                "project. Whatever it is reporting, this change caused it."
            ),
            evidence=f"$ {gate.command}\n\n{gate.output_tail[-1500:]}",
            recommendation="Read this before anything else in the packet.",
        ))

    if pre:
        findings.append(Finding(
            id="pre-existing-1",
            title=("Some checks were already failing before this feature — "
                   + ", ".join(g.name for g in pre)),
            severity="minor",
            category="verification",
            detail=(
                "These fail identically at the commit this work branched from, so they are not "
                "this feature's doing and nothing here should be read as a regression. They do "
                "mean the signal from those gates is unavailable for this change: a gate that "
                "was red before and is red after cannot tell you whether it got worse."
            ),
            evidence="\n".join(
                f"{g.name}: failed at {g.base_sha[:12]} and here" for g in pre),
            recommendation=(
                "Worth fixing as its own feature -- until then these gates carry no signal."),
        ))

    if unknown:
        findings.append(Finding(
            id="unattributed-1",
            title="Some failing checks could not be traced to this feature or to what came before it",
            severity="major",
            category="verification",
            detail=(
                "The base checkout could not be measured -- no git worktree, no container, or "
                "setup failed there -- so it is not known whether this feature caused these "
                "failures or inherited them. Unattributed is not innocent."
            ),
            evidence="\n".join(f"{g.name} at {g.base_sha[:12] or 'unknown'}" for g in unknown),
            recommendation="Run the gate yourself at the base commit before ruling.",
        ))
    return findings


def _raised_in(paths: Sequence[str], outputs: Sequence[str]) -> list[str]:
    """Which of `paths` an error in `outputs` was raised in, by `path:line`.

    Matched by the path's tail, as `check_blind_helper_errors` does: a runner
    prints paths relative to wherever it started -- or absolute, inside a
    container -- so the name alone would match too much and the whole path
    would match nothing.
    """
    hits: list[str] = []
    for path in paths:
        parts = PurePosixPath(path).parts
        tails = ["/".join(parts[i:]) for i in range(0, max(1, len(parts) - 1))]
        if any(re.search(r"(?<![\w.-])" + re.escape(t) + r":\d+", said)
               for said in outputs for t in tails):
            hits.append(path)
    return sorted(dict.fromkeys(hits))


def check_blind_helper_errors(gates: GateReport, oracle: OracleSuite) -> list[Finding]:
    """Blind tests that fail inside a helper the oracle wrote, not in the test.

    On one project two tests failed on `os.environ["OWNER_DATABASE_URL"]` in the
    oracle's own `support.py` -- a variable this project never sets. Nothing
    said so: a failing test is the criterion table's to report, and whether
    anyone noticed the cause depended on a reviewer reading the output. Fixed,
    the suite went from 23 of 25 to 25 of 25.

    Raised, never routed. An error inside a helper is not always the helper's
    fault: a helper reading a field the feature does not return fails there
    too, and an author told to fix that would loosen the helper and hide the
    bug. So this says where the error was raised and shows it, and the arbiter
    decides whose it is.

    Found by where the error was raised, never by what kind it is: a failing
    file's output names the helper as `<path>:<line>`, the one convention every
    runner's traceback shares.
    """
    blind = next((g for g in gates.results if g.name == "blind-tests"), None)
    if blind is None:
        return []
    helpers = [f.path for f in oracle.support]
    loaded_but_failed = [p for p in blind.named_failing if p not in blind.named_unloadable]
    hits: dict[str, list[str]] = {}
    for path in loaded_but_failed:
        said = blind.file_output.get(path) or ""
        for helper in helpers:
            parts = PurePosixPath(helper).parts
            # The runner prints paths relative to wherever it was started, so
            # the helper is matched by its tail -- never by its name alone.
            tails = ["/".join(parts[i:]) for i in range(0, max(1, len(parts) - 1))]
            if any(re.search(re.escape(t) + r":\d+", said) for t in tails):
                hits.setdefault(path, []).append(helper)
    if not hits:
        return []
    listed = "\n".join(f"- {test} fails inside {', '.join(h)}" for test, h in hits.items())
    return [Finding(
        id="blind-helper-1",
        title=("Some independent tests fail inside a helper the oracle wrote, not in the "
               f"test itself — {len(hits)} test file(s)"),
        severity="major",
        category="verification",
        detail=(
            "These tests loaded and then failed with an error raised in a file the oracle "
            "wrote to support them, rather than at one of their own assertions. That is "
            "either the helper's fault -- a wrong setting, a wrong import, a variable this "
            "project never sets -- which only the oracle may fix, or the feature's, when "
            "the helper reads something the feature does not provide. The error below "
            "says which.\n\n" + listed
        ),
        evidence="\n\n".join(
            f"{test}:\n{(blind.file_output.get(test) or '')[-1200:]}" for test in hits),
        files=sorted({h for hs in hits.values() for h in hs}),
        recommendation=(
            "The oracle, if the error is about the helper itself; a repair, if it is about "
            "what the feature returns."),
    )]


def check_leaks(gates: GateReport) -> list[Finding]:
    """Test files that do not clean up after themselves, measured by outcome.

    Each passed, then failed when run again straight after with nothing reset
    between. That is what a test that leaves data behind does to the next test,
    and the next test is usually one the project already had: a suite that
    added lists to the one demo board every browser test opens made the
    project's own board test fail, and it read as the feature breaking it.

    How a test should clean up is the project's own convention, and nothing here
    knows it. The finding names the files, shows the second run, and leaves the
    mechanism to whoever fixes them.
    """
    leaked: dict[str, tuple[GateResult, bool]] = {}
    unstable: list[str] = []
    for g in gates.results:
        for path in g.leaked:
            if path in g.unstable:
                unstable.append(path)
            else:
                leaked[path] = (g, path in g.leak_confirmed)
    findings: list[Finding] = []
    if leaked:
        confirmed = [p for p, (_, sure) in leaked.items() if sure]
        guessed = [p for p, (_, sure) in leaked.items() if not sure]
        findings.append(Finding(
            id="leaks-1",
            title=("Some tests don't clean up after themselves, so they break the tests that "
                   f"run after them — {len(leaked)} file(s)"),
            severity="major",
            category="verification",
            detail=(
                "Each of these passed, then failed when run again straight after with nothing "
                "reset between: it leaves something behind that breaks its own next run, and "
                "whatever runs after it -- including tests this project already has. Those "
                "failures read as the feature being broken, and none of them is.\n\n"
                + (f"Confirmed by the project's reset -- they pass again once it has run: "
                   f"{', '.join(confirmed)}\n\n" if confirmed else "")
                + (f"Not confirmed, because this project declares no reset to try them from a "
                   f"clean state (a flaky test would look the same): {', '.join(guessed)}"
                   if guessed else "")
            ).strip(),
            evidence="\n\n".join(f"{p} (run again):\n{g.leak_output.get(p, '')[-1200:]}"
                                   for p, (g, _) in leaked.items())[:4000],
            files=sorted(leaked),
            recommendation=(
                "Make each clean up after itself the way this project's tests already do -- "
                "whatever that mechanism is here -- without changing what it asserts."),
        ))
    if unstable:
        findings.append(Finding(
            id="unstable-1",
            title=f"Some tests pass or fail from one run to the next — {len(unstable)} file(s)",
            severity="minor",
            category="verification",
            detail=(
                "Each passed, then failed when run again, and failed a third time even after "
                "the project's reset -- so leftover data is not what moves it. Timing, order "
                "or something outside the test. Nothing here says whose it is, and its results "
                "in this packet are worth less for it: " + ", ".join(sorted(set(unstable)))),
            evidence="",
            files=sorted(set(unstable)),
            recommendation="Run it a few times by hand before trusting either outcome.",
        ))
    return findings


def check_unreset_sort(gates: GateReport) -> list[Finding]:
    """A failure put on the code by an experiment that could not start clean.

    The sort sets this feature's test files aside and runs the check again. If
    the project gives no way to reset its data, what those files wrote in the
    first run is still there for the second -- so "red without them" does not
    separate "the code is wrong" from "they left a mess". Said, rather than
    reported as a fact about the code.

    Not for a check that failed at the base commit too: this feature's test
    files did not exist there, so their leftovers cannot be why, and the
    packet already says it is not this feature's.
    """
    hit = [g for g in gates.results if not g.passed and g.unreset and not g.pre_existing]
    if not hit:
        return []
    return [Finding(
        id="sort-unreset",
        title=("A failure is put on the code, but the check could not be re-run from a "
               "clean state — " + ", ".join(g.name for g in hit)),
        severity="major",
        category="verification",
        detail=(
            "These checks still failed with this feature's test files set aside, so they "
            "are recorded against the rest of the branch. But this project declares no way "
            "to reset its data between runs, so anything those test files wrote on the "
            "first run -- rows, lists, users -- was still there on the second. A failure "
            "caused by that leftover data looks exactly like one caused by the code. Read "
            "these as unsettled, not as the code's."
        ),
        evidence="\n\n".join(f"$ {g.command}\n{(g.output_tail or '')[-800:]}" for g in hit),
        recommendation=(
            "Give the project's environment a `test_prepare` step that puts its data back "
            "to the seeded state -- recreate the schema and reseed, or truncate and reseed. "
            "It then runs before every re-run, and before each blind test file."),
    )]


def check_oracle_file_failures(gates: GateReport) -> list[Finding]:
    """A project check fails on files the oracle wrote, not on the feature.

    Set aside this feature's oracle files and the check passes; put them back
    and it fails; and the blind tests themselves are not what fails. So the
    problem is in the files -- an unused import, a type error, a helper the
    project's runner will not load -- and no worker may edit them.
    """
    hit = [g for g in gates.results if not g.passed and g.failed_on == "oracle_files"]
    if not hit:
        return []
    return [Finding(
        id="oracle-files-1",
        title="Files the oracle wrote fail the project's own checks — "
              + ", ".join(g.name for g in hit),
        severity="major",
        category="verification",
        detail=(
            "These checks pass with this feature's oracle files set aside and fail with "
            "them in place, and it is not the blind tests failing that does it. The "
            "problem is in the files the oracle wrote, not in the feature, and the "
            "branch will fail CI until it is fixed. Workers cannot edit those files; "
            "the oracle is shown the output and asked to fix them."
        ),
        evidence="\n\n".join(f"$ {g.command}\n{(g.output_tail or '')[-1500:]}" for g in hit),
        recommendation="Read the check output below; it names the file and the rule.",
    )]


def check_attribution_wiring(project_state) -> list[Finding]:
    """Whether this project can say *which test failed*, asked before it pays.

    The same shape has cost a real finding three times, and each time the
    machinery was present and unwired:

    - `Gate.report` was configured on every rule and named the orchestrator's
      path to a containerised runner, so the reports were written where nothing
      read them and `read_junit` returned nothing. A missing report means "fall
      back to the exit code", by design -- so eighteen of twenty-five criteria
      were reported failing off four files, fifteen off one.
    - `rework.breaker_file_command` was empty, so nine probes ran as one command
      and a genuine migration error was filed as "no probe could be blamed".
    - The arbiter deduplicated round 0 and never ran across rounds, so sixteen
      re-discoveries of six defects arrived as sixteen escalations.

    Every one leaned the same way: fewer findings, a cleaner-looking number, and
    a packet that read better than the run deserved. And every one was knowable
    before the run rather than after it -- which is what this is for. It reads
    configuration and nothing else; it cannot tell whether a command works, only
    whether the project said how.
    """
    rules = list(getattr(project_state, "test_file_commands", None) or ())
    findings: list[Finding] = []

    if not rules:
        findings.append(Finding(
            id="attribution-1",
            title="This project has no way to run a single test file, so a failure can't be pinned to one",
            severity="major",
            category="verification",
            detail=(
                "Every test verdict in this run will be a file's exit code. A file holding "
                "twelve cases fails as one thing, and every criterion tagged to it fails with "
                "it -- so a packet can report a dozen criteria broken on the strength of one "
                "assertion, and nothing on the screen will say which.\n\n"
                "The blind suite is attributed this way, and so are the breaker's probes, "
                "which fall back to these same rules."
            ),
            evidence="project.test_file_commands is empty",
            recommendation=(
                "Declare one in the project's testing setup for each way this repository "
                "runs a test, with `{path}` where the file goes. It is one line each, and "
                "it is the difference between `the file failed` and `this test failed`. "
                "Which command that is belongs to the survey a human approved at gate 0; "
                "this module does not know and must not guess."
            ),
        ))
        return findings

    blind = [r for r in rules if not (getattr(r, "report", "") or "").strip()]
    if blind:
        findings.append(Finding(
            id="attribution-2",
            title="Some tests can't report which one failed, so a failure can't be traced to a requirement",
            severity="major",
            category="verification",
            detail=(
                "A rule with no `report` runs the file and reads its exit code, so every "
                "criterion tagged to that file shares the file's verdict. A rule with one "
                "emits JUnit XML and each case answers for itself.\n\n"
                + "\n".join(f"- {r.match}: {r.command}" for r in blind)
            ),
            evidence="TestFileCommand.report is empty for: "
                     + ", ".join(r.match for r in blind),
            recommendation=(
                "Press Resurvey on the project. For a rule with no `report`, the reading "
                "asks that runner itself how it writes JUnit XML, "
                "proves the answer on this project's own canary test, and offers the proven "
                "command as a change to accept -- nothing is applied until you do. What it "
                "offers is the same command with the runner's JUnit flag added and `{report}` "
                "where the path goes."
            ),
            criterion_ids=[],
        ))
    return findings


def check_testing_conventions(testing) -> list[Finding]:
    """A build whose oracle was never told how this repository imports its code.

    The durable half of the version stamp. `reading_behind` says a question was
    never put, and goes quiet the moment the record is next written -- including
    by a re-survey somebody declined. This reads the field instead, so a project
    that has the question and answered it with nothing is not mistaken for one
    that answered it.

    It is a blind spot, said out loud, in the shape `check_blind_attribution`
    established: nothing here is a statement about the code. The oracle authors
    in an empty directory and cannot open a test file to see whether a component
    arrives as a default or by name, so with this field empty it guesses. The
    cost is measured rather than feared -- one guess, `import TrialDetail from`
    against a repository whose every page is `export function TrialDetail`,
    collected no tests and took six criteria with it.
    """
    tiers = [t for t in (getattr(testing, "tiers", None) or ())
             if getattr(t, "verdict", "") == "usable"]
    if not tiers or any(getattr(t, "import_examples", None) for t in tiers):
        return []
    return [Finding(
        id="conventions-1",
        title="The independent tests had to guess how this project's tests import its code",
        severity="major",
        category="verification",
        detail=(
            "The oracle works in an empty directory: that air gap is what makes its "
            "tests worth running, and it is also why it cannot look this up. It can "
            "derive a module path from the spec and the directory it writes in. It "
            "cannot derive whether this project exports a thing as a default or by "
            "name, and nothing in a specification says.\n\n"
            "So it picks one. Where it picks wrong the file imports nothing, collects "
            "nothing, and every criterion that file covered comes back failed -- "
            "reading, to anyone who sees the packet, like broken code.\n\n"
            "Nothing here says this run's suite got it wrong. It says nothing in the "
            "project made it likely to get it right."
        ),
        evidence=("`testing.tiers[].import_examples` is empty on "
                  + ", ".join(t.tier for t in tiers)),
        recommendation=(
            "Re-survey this project. The surveyor is asked for these lines and copies "
            "them out of test files that already pass here -- two or three per tier is "
            "enough, and they are the only source for the one fact a blind test author "
            "cannot work out for itself."
        ),
    )]
