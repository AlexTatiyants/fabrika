"""Checks on the blind suite: what it could not prove, what it could not
set up, what it did not record, and whether it loaded at all.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from ...gates import TestSummary
from ...schemas import TestingSurface, Finding, GateReport, GateResult, OracleSuite, Spec
from ...store import EvidenceStore

from ..oracle_suite import _CRITERION_ID


def check_oracle_discards(
    store: EvidenceStore, spec_hash: str, root: Path | str | None,
) -> list[Finding]:
    """Independent tests that depend on something the factory threw away.

    The oracle may only keep what it writes inside its own directory; anything
    else is dropped, and that part is right -- it is how an agent that has
    never seen the code is kept from editing the code. What must not happen is
    silence. An oracle has written its helpers into a sibling folder, the
    folder was dropped, four test files that import it could not load, and the
    packet explained the red gate as a naming problem -- because the one record
    that knew, the list of discarded files, was read by nothing.

    Crude on purpose: a kept test that mentions a discarded file's name, or the
    name of a folder only discarded files were in, is taken to depend on it.
    That is enough to say where the cause is, which is the whole job.
    """
    record = None
    for candidate in store.records():
        if candidate.get("kind") == "oracle_session" and candidate.get("spec_hash") == spec_hash:
            record = candidate
    if record is None or root is None:
        return []
    payload = record.get("payload") or {}
    kept = [str(p) for p in payload.get("files") or []]
    dropped = [str(p) for p in payload.get("outside") or []]
    if not kept or not dropped:
        return []

    kept_dirs = {str(PurePosixPath(p).parent) for p in kept}
    names: dict[str, str] = {}
    for path in dropped:
        pure = PurePosixPath(path)
        if pure.stem != "__init__":
            names[pure.stem] = path
        for folder in pure.parents:
            if str(folder) in ("", ".") or str(folder) in kept_dirs:
                continue
            if any(str(folder) == str(PurePosixPath(k).parent) or
                   str(PurePosixPath(k)).startswith(str(folder) + "/") for k in kept):
                continue
            names.setdefault(folder.name, path)
    if not names:
        return []

    dependents: dict[str, list[str]] = {}
    for path in kept:
        try:
            text = (Path(root) / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # On a line that brings something in, not anywhere: "support" is an
        # ordinary English word, and a README describing the suite would match
        # it.
        loading = [line for line in text.splitlines()
                   if re.search(r"\b(import|from|require|include|use)\b", line)]
        hits = sorted(n for n in names
                      if any(re.search(rf"\b{re.escape(n)}\b", line) for line in loading))
        if hits:
            dependents[path] = hits
    if not dependents:
        return []

    listed = "\n".join(f"- {path} uses {', '.join(hits)}" for path, hits in dependents.items())
    return [Finding(
        id="oracle-discard-1",
        title=("Some independent tests depend on files the factory discarded — "
               f"{len(dependents)} test file(s)"),
        severity="major",
        category="verification",
        detail=(
            f"{len(dropped)} file(s) the oracle wrote were outside its own directory and "
            "were dropped before the suite ran: " + ", ".join(dropped) + ". The test "
            "files below refer to them, so they cannot load, and the criteria they "
            "cover come back unverified. That is a fact about where the tests' helpers "
            "were put, not about the feature.\n\n" + listed
        ),
        evidence=listed,
        recommendation=(
            "Verify again: the oracle is now told its helpers must live inside its own "
            "directory, and that directory is named so it can be imported from. The "
            "implementation does not need to change for this."
        ),
    )]


def blind_summary(results: Sequence[GateResult]) -> TestSummary:
    """One summary over the gates that ran tests, counted in files.

    Sums what each gate recorded. `known` is true only if some gate was run file
    by file, because that is the only arrangement in which a failure can be
    traced to a criterion -- a whole-suite gate contributes an exit code and
    nothing that could attribute it.
    """
    known = any(r.summary_known for r in results)
    total = sum(r.tests_total for r in results)
    return TestSummary(
        known=known,
        passed=sum(r.tests_passed for r in results),
        failed=sum(r.tests_failed for r in results),
        total=total,
        zero_ran=any(r.zero_ran for r in results) or (known and total == 0),
    )


def check_blind_attribution(
    gates: GateReport, empty_run_detected: bool | None,
) -> list[Finding]:
    """Two ways the blind gate can be unable to say what it proved.

    The first is arrangement: run as one command, its exit code covers the whole
    suite, so a red gate cannot name the criterion that failed and a green one
    cannot name the criteria that passed. A rule in the project's
    `test_file_commands` is what fixes it, and it is one line.

    The second is deeper and is the reason any of this exists. An exit code
    cannot distinguish a file whose tests passed from a file whose tests were
    never collected -- both are zero. Most runners exit non-zero when pointed at
    nothing, and "most runners do" is precisely the kind of belief a table of
    framework regexes would encode, so it is measured against this
    project's own command at gate 0 rather than assumed. A project whose runner
    was measured as tolerating an empty selection is not broken; it has a blind
    spot, and this is the packet saying so out loud instead of quietly reading
    green as verified.
    """
    blind = [g for g in gates.results if g.name == "blind-tests"]
    if not blind or not blind[0].started:
        return []          # a suite that did not run is another check's business
    findings: list[Finding] = []
    if not blind[0].summary_known:
        findings.append(Finding(
            id="attribution-1",
            title="The independent tests ran as one batch, so no result can be tied to a requirement",
            severity="major",
            category="verification",
            detail=(
                "The suite's exit code covers every blind test at once. A failure "
                "cannot be traced to the criterion it belongs to, and a pass cannot "
                "be traced either -- so every criterion below rests on one number "
                "that describes all of them.\n\nNothing here is a statement about "
                "the code."
            ),
            evidence=blind[0].command,
            recommendation=(
                "Give this project a rule in its `test_file_commands`: the command that "
                "runs a single test file, with `{path}` where the path goes. Each file's "
                "exit code then settles the criteria that file covers."
            ),
        ))
    if empty_run_detected is False:
        findings.append(Finding(
            id="attribution-2",
            title="This project's test runner reports success even when there are no tests to run",
            severity="major",
            category="verification",
            detail=(
                "Measured at gate 0 against this project's own command: pointed at a "
                "selection that cannot match, the runner still exits zero. So a blind "
                "test file that collected no test at all is indistinguishable here "
                "from one whose tests passed, and 'verified' means 'the command "
                "exited zero' rather than 'the assertions ran'.\n\nThis is a known "
                "property of the project's tooling, not a defect in the feature. It "
                "is said here because a reader would otherwise take the criterion "
                "table to mean more than it can."
            ),
            evidence="gate 0 probe: empty selection exited zero",
            recommendation=(
                "Most runners have a flag that fails on an empty selection. Adding it "
                "to the test command closes this, and re-running gate 0 re-measures it."
            ),
        ))
    return findings


def available_fixtures(surface: TestingSurface) -> set[str]:
    """The names a test author can actually get hold of, from the gate-0 survey.

    Each entry the surveyor recorded is a name followed by its description --
    `make_patient(slug, **columns) -- a patient row of that tenant...` -- so the
    name is the leading identifier and everything after it is prose for a human.
    Matching on the identifier is what makes the comparison mechanical: a
    criterion naming `make_patient` is asking for something this project has,
    and one naming "a tenant user whose email can be sent as X-Operator-Id" is
    not naming a fixture at all, which is the whole signal.
    """
    names: set[str] = set()
    for tier in surface.tiers or ():
        for entry in tier.fixtures or ():
            if name := _leading_name(entry):
                names.add(name)
    return names


def _leading_name(entry: str) -> str:
    """The identifier an inventory line or a setup line starts with.

    Both are `name -- prose for a human`, and matching on the identifier is what
    keeps the comparison mechanical: `make_patient` is a fixture this project
    has, and "a tenant user whose email can be sent as X-Operator-Id" is not
    naming a fixture at all, which is the whole signal.
    """
    leading = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)", entry or "")
    return leading.group(1) if leading else ""


def unmet_setup(spec: Spec, surface: TestingSurface) -> list[tuple[str, str]]:
    """(criterion id, the setup it named) for everything no fixture provides.

    The arrange half of arrange-act-assert. `verification` says how an observer
    would confirm a behaviour; it does not say how a test reaches the state
    where the behaviour happens, and that is the half that needs affordances
    the repository may not have.

    One real feature -- built expressly to make a repository verifiable -- was
    specified as twenty-one behaviours of the thing being made testable and not
    one affordance for testing it. Five capabilities it never provided were
    each the direct consequence of an answer given at gate 1, and all five
    surfaced seven phases later as findings about the code.
    """
    known = available_fixtures(surface)
    if not known:
        # A survey from before fixtures were recorded says nothing, and nothing
        # is not "this project has none". Guessing would put a claim in front of
        # a human that no reading supports.
        return []

    # Plus whatever this spec builds. A feature whose whole job is to make a
    # repository testable adds fixtures, and comparing its criteria against a
    # survey taken before it ran reports every one of them as a gap: one run
    # specified `make_user`, `app_client` and `make_portal_token` as criteria of
    # their own and was told that fourteen of its twenty-five criteria needed
    # setup the project could not provide. The oracle writes its suite after the
    # workers, so a fixture this spec commits to exists by the time a blind test
    # reaches for it.
    known = known | {
        name
        for criterion in spec.acceptance_criteria
        for entry in criterion.provides or ()
        if (name := _leading_name(entry))
    }

    out: list[tuple[str, str]] = []
    for criterion in spec.acceptance_criteria:
        for want in criterion.setup or ():
            if _leading_name(want) in known:
                continue
            out.append((criterion.id, (want or "").strip()))
    return out


def promised_setup(spec: Spec) -> dict[str, list[str]]:
    """Fixture this spec adds -> the criteria that depend on having it.

    Not a gap and not nothing. Thirteen criteria on one spec were verifiable
    only if `make_user` got built, which makes one criterion load-bearing for
    half the packet -- and if it is dropped, those thirteen come back unverified
    with the reason attached to their own code rather than to the fixture that
    never arrived.
    """
    provided = {
        name: criterion.id
        for criterion in spec.acceptance_criteria
        for entry in criterion.provides or ()
        if (name := _leading_name(entry))
    }
    out: dict[str, list[str]] = {}
    for criterion in spec.acceptance_criteria:
        for want in criterion.setup or ():
            name = _leading_name(want)
            if name in provided and provided[name] != criterion.id:
                out.setdefault(name, []).append(criterion.id)
    return out


def check_spec_testability(spec: Spec, surface: TestingSurface,
                           manual: Mapping[str, str] | None = None) -> list[Finding]:
    """Criteria this project cannot verify, said before the spec is frozen.

    Computed, not asked: each criterion declares the level it can honestly be
    checked at, the survey recorded what each level offers, and this is the
    comparison. No model is involved and none should be. (INV-3)

    It exists because of *when* the same information otherwise arrives. Without
    it, a spec of twenty-five criteria was frozen, built, verified and reported
    on -- three hours -- before anything said that eleven of them could not be
    checked by this project's test setup at all. Six needed a browser and the
    project had no runner for one; four needed a fixture that could create a
    patient, a referral or a database, and the project's only integration test
    built its own world inline and left nothing behind to reuse.

    Every one of those was knowable at gate 0. Reported at gate 1, a human
    narrows a criterion, rewrites one, or freezes it anyway knowing what it
    will cost -- which is a decision. Reported in the packet, it is a fact about
    the harness arriving too late to act on, wearing the clothes of a fact about
    the code.

    Silent when the project can verify everything asked of it, which is the
    answer a well-equipped repository should get.
    """
    if not spec.acceptance_criteria:
        return []
    # An older survey that never recorded testability says nothing about it, and
    # nothing is not the same as "absent". Guessing either way here would put a
    # claim in front of a human that no reading supports.
    if not surface.tiers:
        return []

    findings: list[Finding] = []
    # The arrange half, first, because it is the one nothing else asks about. A
    # tier answers "is there a runner at this level"; this answers "can a test
    # get into the state this criterion is about", and a project can pass the
    # first and fail the second for every criterion in a spec.
    missing = unmet_setup(spec, surface)
    if missing:
        by_criterion: dict[str, list[str]] = {}
        for cid, want in missing:
            by_criterion.setdefault(cid, []).append(want)
        findings.append(Finding(
            id="setup-gap-1",
            title="Some requirements can't be tested, because nothing can set up the situation they describe",
            severity="major",
            category="verification",
            detail=(
                "Each of these declares state that must exist before the behaviour can be "
                "observed, and named something that is not among the fixtures this project's "
                "testing surface provides. They can still be built -- nothing here is about "
                "whether the code will work -- but a blind test author will have no way to "
                "arrange them, and the packet will report them unverified with the reason "
                "attached to the code rather than to the harness.\n\n"
                + "\n\n".join(
                    f"{cid} -- needs {'; '.join(wants)}" for cid, wants in by_criterion.items())
                + "\n\nThree ways out, and all of them are cheaper now than after the build: "
                "add the fixture to this project and re-survey, narrow the criterion to "
                "something observable with what exists, or freeze it knowing it will come "
                "back unverified."
            ),
            evidence="available fixtures: " + ", ".join(sorted(available_fixtures(surface))),
            criterion_ids=sorted(by_criterion),
        ))

    # The other half of the same comparison: not a fixture that is missing, but
    # one that does not exist yet because this spec is what builds it. Silent,
    # a criterion carrying half the packet on its back reads like any other.
    promised = promised_setup(spec)
    if promised:
        worst = max(promised.items(), key=lambda kv: len(kv[1]))
        findings.append(Finding(
            id="setup-promised-1",
            title="Some requirements can only be tested if this feature delivers the test helpers it promises",
            severity="minor",
            category="verification",
            detail=(
                "These fixtures are not in the project's testing surface and are not gaps: this "
                "spec adds them, and the oracle writes its suite after the workers, so they exist "
                "by the time a blind test reaches for one. What is worth knowing before the "
                "freeze is how much rests on each.\n\n"
                + "\n\n".join(
                    f"{name} -- {len(cs)} criteri{'on' if len(cs) == 1 else 'a'} depend on it: "
                    f"{', '.join(cs)}" for name, cs in sorted(promised.items()))
                + f"\n\nIf {worst[0]} is dropped, narrowed or built differently, "
                f"{len(worst[1])} criteria come back unverified with the reason attached to "
                "their own code rather than to the fixture that never arrived."
            ),
            evidence="promised by: " + ", ".join(
                f"{name} ({criterion.id})"
                for criterion in spec.acceptance_criteria
                for entry in criterion.provides or ()
                if (name := _leading_name(entry)) in promised),
            criterion_ids=sorted({c for cs in promised.values() for c in cs}),
        ))

    # Levels whose tests share state -- a database, a running server -- and at
    # which this project's own tests were read as not cleaning up after
    # themselves. A test written there leaves data for the tests after it; one
    # suite added lists to the demo board every browser test opens, and a test
    # the project already had failed for it, reading as the feature's fault.
    # Unit tests share nothing, so they are not asked. `None` is an older survey
    # that did not record this, and says nothing either way.
    # `manual` is `workspace.unchecked_levels`: level -> why it is not tested.
    # Criteria there are a person's to check, said once below, and nothing else
    # here is about them -- a gap nobody will test into is not a gap to close.
    skipped = dict(manual or {})
    unclean: dict[str, list[str]] = {}
    for criterion in spec.acceptance_criteria:
        level = criterion.verified_at
        if level in skipped:
            continue
        tier = surface.tier(level) if level in ("integration", "user") else None
        if tier is None or tier.verdict == "absent" or tier.cleanup is None:
            continue
        # Who resets the data decides it, where the reading says. A reading
        # that does not say has only an empty `cleanup` to mean nobody. "Each
        # test undoes its own" is a gap too: nothing catches the test that
        # forgets.
        by = tier.cleanup_by or ("nobody" if not tier.cleanup.strip() else None)
        if by in ("each_test", "nobody"):
            unclean.setdefault(level, []).append(criterion.id)
    if unclean:
        findings.append(Finding(
            id="cleanup-gap-1",
            title="Some requirements are checked where tests don't clean up after themselves",
            severity="major",
            category="verification",
            detail=(
                "At these levels this project's tests leave behind whatever they create, so "
                "every test written for these criteria will too -- and a test that leaves data "
                "behind breaks the tests that run after it, including ones this project already "
                "has. Each of those failures reads as the feature being broken.\n\n"
                + "\n\n".join(f"{level}: {', '.join(ids)}" for level, ids in unclean.items())
                + "\n\nHow the cleanup is done is this project's business -- a schema made "
                "fresh per test, a transaction rolled back, a teardown that deletes what it "
                "made. What is missing is any of them."
            ),
            evidence="\n".join(f"{t.tier}: {t.cleanup_summary or t.note or 'no cleanup recorded'}"
                                for t in surface.tiers if t.tier in unclean),
            recommendation=(
                "The project's survey offers these fixes, on the project page: "
                + "; ".join(o.title for t in surface.tiers if t.tier in unclean
                            for o in t.cleanup_options)
                + ". Or add a criterion whose `provides` is a fixture that makes its own data "
                "and removes it afterwards -- the workers build it, and every blind test uses "
                "it by name. Or freeze these knowing the tests written for them may break "
                "others."
                if any(t.cleanup_options for t in surface.tiers if t.tier in unclean) else
                "Add a criterion whose `provides` is a fixture that makes its own data and "
                "removes it afterwards -- the workers build it, and every blind test uses it "
                "by name. Or re-survey the project, which proposes fixes you can apply. Or "
                "freeze these knowing the tests written for them may break others."),
            criterion_ids=sorted({c for ids in unclean.values() for c in ids}),
        ))

    WORDS = {
        "unit": "logic in isolation",
        "integration": "the application's own interfaces",
        "user": "what a person sees on a screen",
    }
    by_hand: dict[str, list[str]] = {}
    for criterion in spec.acceptance_criteria:
        if criterion.verified_at in skipped:
            by_hand.setdefault(criterion.verified_at, []).append(criterion.id)
    if by_hand:
        count = sum(len(ids) for ids in by_hand.values())
        findings.append(Finding(
            id="unchecked-1",
            title="Some criteria will be checked by a person, not by Fabrika",
            severity="minor",
            category="verification",
            detail=(
                f"{count} of {len(spec.acceptance_criteria)} criteria. "
                "Their test level can't run cleanly in this project, and a test there would "
                "fail for reasons that say nothing about the code. So Fabrika doesn't write "
                "or run tests for them; they come to review as a checklist.\n\n"
                + "\n\n".join(f"{level} ({skipped[level]}): {', '.join(ids)}"
                              for level, ids in by_hand.items())),
            recommendation=("Freeze knowing you'll check these by hand. Or set up the level "
                            "on the project page and re-survey, and they'll be tested."),
            criterion_ids=[cid for ids in by_hand.values() for cid in ids],
        ))

    blocked: dict[str, list[str]] = {}
    for criterion in spec.acceptance_criteria:
        level = criterion.verified_at
        if not level or level in skipped:
            continue
        tier = surface.tier(level)
        if tier is None or tier.verdict == "usable":
            continue
        blocked.setdefault(level, []).append(criterion.id)
    if not blocked:
        return findings

    lines: list[str] = []
    for level, ids in blocked.items():
        tier = surface.tier(level)
        assert tier is not None
        why = (f"this project has no {level} test runner"
               if tier.verdict == "absent" else
               f"this project's {level} tests each build their own setup inline, "
               "so there is nothing a new test can reuse")
        lines.append(
            f"{', '.join(ids)} -- {len(ids)} criteri{'on' if len(ids) == 1 else 'a'} "
            f"checkable only at the {level} level ({WORDS[level]}), and {why}"
            + (f". {tier.note}" if tier.note else "."))

    total = sum(len(ids) for ids in blocked.values())
    findings.append(Finding(
        id="testability-1",
        title="Some requirements can't be tested with this project's test setup",
        severity="major",
        category="verification",
        detail=(
            f"{total} of {len(spec.acceptance_criteria)} criteria. "
            "Each criterion below is checkable only at a level this project does not "
            "offer a new test author anything to write against. They can still be "
            "built -- nothing here is about whether the code will work -- but nothing "
            "will independently confirm them, and the packet will report them "
            "unverified.\n\n" + "\n\n".join(lines) + "\n\n"
            "Freezing them is a choice rather than a mistake. It is put here because "
            "it is a choice, and because after this gate it stops being one."
        ),
        evidence="\n".join(
            f"{t.tier}: {t.verdict}" + (f" ({t.runner})" if t.runner else " (no runner)")
            for t in surface.tiers),
        recommendation=(
            "Three ways out, in the order they usually cost least. Narrow the "
            "criterion to something this project can observe. Or accept it now and "
            "read it as unverified later, deliberately. Or give the project the setup "
            "it lacks -- the survey can propose the fixtures and harness, and gate 0 "
            "is where you would apply them."
        ),
        criterion_ids=[cid for ids in blocked.values() for cid in ids],
    ))

    return findings


def check_blind_suite_missing(oracle: OracleSuite | None, spec: Spec) -> list[Finding]:
    """No blind suite at all, raised as one blocker rather than left to be read.

    The sibling of `check_blind_tags`, for the case that one deliberately does
    not cover: a suite with nothing in it that asserts. That check fires when
    tests exist and name nothing; this fires when there are no tests, which
    happens three ways -- the harness wrote none, the verify lane failed
    outright, or everything it wrote was scaffolding.

    That third way is the reason this reads `oracle.tests` rather than the
    whole suite. An oracle that produced a conftest, a manifest and a README and
    no test at all would look like a suite if files were counted, because the
    three sit in the same list as the tests and carry tags. Counting what
    asserts is what this says.

    Left alone, the consequence is a criterion table of quiet `no_test` rows,
    and that reads as an oracle that looked at the spec and declined. It is not
    the same thing at all: nothing looked. A human seeing twelve unremarkable
    rows draws a conclusion about the feature; the true statement is about the
    run, and it belongs where a human will not miss it.
    """
    if oracle is not None and oracle.tests:
        return []
    criteria = len(spec.acceptance_criteria)
    why = (oracle.notes.strip() if oracle is not None and oracle.notes.strip()
           else "No reason was recorded, which is itself worth asking about.")
    scaffolding = list(oracle.support) if oracle is not None else []
    if scaffolding:
        why = "\n\n".join(filter(None, [
            why,
            f"The lane did write {len(scaffolding)} file(s), and none of them asserts "
            "anything -- they are the suite's scaffolding, declared as such by the "
            f"oracle itself: {', '.join(f.path for f in scaffolding[:10])}"]))
    return [Finding(
        id="unverified-1",
        title="No independent tests were written, so no requirement was checked by anyone but the builders",
        severity="blocker",
        category="verification",
        detail=(
            f"None of the {criteria} acceptance criteria. "
            "The verify lane produced nothing that asserts, so nothing in this run was "
            "checked against the specification by an agent that had not seen the "
            "implementation. "
            "That is the whole point of the lane, and without it the gates below measure "
            "only that the project's own tests still pass -- which the feature's authors "
            "wrote, or did not.\n\nRead every criterion in this packet as unverified for "
            f"that reason, and not for anything about the code.\n\nWhat happened:\n\n{why}"),
        evidence=why[:2000],
        recommendation=(
            "Rebuild rather than rule on this packet. The build itself may be sound -- it "
            "was kept deliberately rather than thrown away with the lane that failed -- "
            "but nothing here can tell you whether it meets the contract."),
    )]


def browser_ran(oracle: OracleSuite | None, spec: Spec | None, gates: GateReport) -> bool:
    """Whether a test that drives a browser actually ran this round.

    Two facts, one declared and one measured, and neither is enough alone. The
    spec says which criteria are verified in the browser -- `verified_at:
    user`, frozen before the build and already checked against what the
    project can do. The blind gate says which files it ran, because it times
    each one. A file covering a browser criterion that has a time is a browser
    that ran; a browser criterion whose file never started is not.
    """
    if oracle is None or spec is None:
        return False
    in_browser = {c.id.upper() for c in spec.acceptance_criteria
                  if c.id and c.verified_at == "user"}
    blind = next((g for g in gates.results if g.name == "blind-tests"), None)
    if not in_browser or blind is None:
        return False
    ran = set(blind.file_seconds)
    return any(test.path in ran
               and any(cid.upper() in in_browser for cid in (test.criterion_ids or []))
               for test in oracle.tests)


def current_recordings(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]] | None:
    """The `traces` payloads that describe the tree a person is looking at.

    One assessment's, and the latest one's. An assessment ends with its `gates`
    record; recordings written after the last one belong to an assessment still
    running, and are the newest there are. A build, a rebuild or a re-check
    starts again from nothing.

    Not every round's recordings since the last dispatch, rebuild or re-check:
    a repair round starts none of those, so a packet on a feature two repairs
    along would show fifteen recordings of the tree before either repair beside
    two of the one after, with nothing to say which was which.

    An assessment that recorded nothing is answered with nothing, not with the
    round before it: a recording of a tree that no longer exists is not
    evidence about the one that does. `None` means nothing was ever recorded
    this way, so a caller can fall back to whatever it did before.
    """
    closed: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    seen = False
    for record in records:
        kind = record.get("kind")
        if kind in ("dispatch", "rebuild", "revalidate"):
            current = []
        elif kind == "traces" and isinstance(record.get("payload"), dict):
            seen = True
            current.append(record["payload"])
        elif kind == "gates" and not (record.get("meta") or {}).get("attributed"):
            closed.append(current)
            current = []
    if not seen:
        return None
    if current:
        return current
    return closed[-1] if closed else []


def check_trace_coverage(
    trace_dirs: Sequence[str], traces: Sequence[Mapping[str, Any]], browser_ran: bool,
) -> list[Finding]:
    """A runner that can record a run, and did not.

    The surveyor is asked to name the directory and to put the switch in
    `recommendations` when it is off. That is prose in a brief, and a brief is
    guidance a model can skip -- so the outcome is measured here instead, the
    way the rest of this file measures things it would otherwise have to
    trust.

    Asked only where a browser ran: a blind file that checks a criterion the
    spec says is verified in the browser, and that actually ran this round. So
    a project with no browser is never asked for recordings it cannot make.
    It looks for a recording, not a screenshot: a recording is every moment of
    what a screenshot would have shown, and nothing asks a suite for
    screenshots, so a signal that depended on them would be quiet -- which is
    the failure this function exists to catch.

    Two shapes of gap, and they want different sentences. Nothing declared is
    a reading that did not answer -- the surveyor never said where. Declared
    and empty is a switch nobody turned on, which is a line in a config file
    this factory does not own.
    """
    if not browser_ran or traces:
        return []
    declared = [d for d in (trace_dirs or []) if (d or "").strip()]
    if declared:
        return [Finding(
            id="traces-none",
            title="The browser runs left no recording, though this project says where they go",
            severity="minor",
            category="verification",
            detail=(
                "Tests that drive a browser ran, and the directories this project names for "
                f"recordings are empty: {', '.join(declared)}.\n\n"
                "A recording is the only evidence in a packet a person can judge by watching "
                "rather than by reading code. Without one, every criterion about what a person "
                "sees is an assertion the reader takes on trust.\n\n"
                "Usually this is one line of the runner's configuration, switched off."),
            evidence="declared: " + ", ".join(declared) + "; collected: none",
            recommendation=(
                "Switch recording on in the runner's own configuration, for every run and not "
                "only the failing ones. For Playwright that is `trace: 'on'` under `use`, with "
                "`video: { mode: 'on', size: <the viewport> }` beside it -- the video is never "
                "collected, but it is the only setting that raises the recording's frames "
                "from 800 wide to the page's own size. If this project has decided against "
                "recording, say so and the directory should come off the project instead."),
        )]
    return [Finding(
        id="traces-undeclared",
        title="A browser suite ran here and nothing says where it could record",
        severity="minor",
        category="verification",
        detail=(
            "Tests that drive a browser ran this round, and this project names no directory "
            "where a recording of a run would go, so none is collected. A recording is every "
            "moment of the test, with the step it was on, and it is the only evidence in a "
            "packet that can be judged by watching.\n\n"
            "This is a reading that did not answer, not a decision: the question is which "
            "directory the runner writes to, and it is answered by looking at the repository."),
        evidence="a browser test ran; no recording directory is recorded",
        recommendation=(
            "Read the project again so the surveyor can name the directory, and switch "
            "recording on if it is off. A project whose runner genuinely cannot record "
            "should say that, so this is asked once rather than every run."),
    )]


def check_recording_coverage(
    oracle: OracleSuite | None, traces: Sequence[Mapping[str, Any]],
) -> list[Finding]:
    """A criterion a browser test checked, with no recording that names it.

    A recording is the one piece of evidence a person can judge without
    reading code, and the question this asks is: of the criteria that *could*
    be shown, which were not? A recording is matched to its criterion by its
    test's title -- `[AC-13] the control filters typed
    input` -- which is the only place the runner writes anything a criterion
    can be read from.

    Which criteria could be shown is measured rather than declared, from the
    file a recording was swept after: a blind file that left a recording is a
    file driving a browser, whatever tier it claims. So a project with no
    browser is never asked for recordings, and a criterion checked below the
    browser is not a gap.

    What the title cannot do is prove the frames. This knows a test *said* it
    was about a criterion, and never what the test showed -- which is why the
    oracle is asked to write one browser test per criterion, driven all the way
    to the state the criterion describes.
    """
    if oracle is None or not oracle.tests:
        return []
    recorded_files: set[str] = set()
    named: set[str] = set()
    for record in traces:
        recorded_files.update(str(v) for v in (record.get("by_test") or {}).values())
        for title in (record.get("titles") or {}).values():
            # The same reading every other criterion reference gets: `AC-13`,
            # `AC_13` and `ac 13` are one criterion however the title spells it.
            named.update(f"AC-{int(n)}" for n in _CRITERION_ID.findall(title or ""))
    if not recorded_files:
        return []

    missing: list[tuple[str, str]] = []
    for test in oracle.tests:
        if test.path not in recorded_files:
            continue
        for cid in (test.criterion_ids or []):
            said = _CRITERION_ID.findall(cid or "")
            if said and f"AC-{int(said[0])}" not in named:
                missing.append((cid, test.path))
    if not missing:
        return []

    listed = "\n".join(f"- {cid} — covered by {path}, which was recorded this run"
                       for cid, path in missing)
    return [Finding(
        id="recordings-uncovered",
        title=("Something a person can see was checked without a recording that names it"
               + f" — {len(missing)} criterion(s)"),
        severity="minor",
        category="verification",
        detail=(
            "These criteria are covered by a test file that drove a browser and was recorded "
            "on this run, and no recording's test is titled for them. So a reader looking for "
            "what each one looks like has nowhere to look.\n\n"
            + listed
            + "\n\nA recording is matched to a criterion by its test's title. A test "
            "covering several criteria under a title naming none of them, or naming only the "
            "first, is evidence for whichever one the reader already believed."),
        evidence="recordings titled: " + (", ".join(sorted(named)) or "(none)"),
        recommendation=(
            "Give each of these criteria its own browser test, titled with its id -- "
            "`test('[AC-13] ...')` -- and driven to the state the criterion describes."),
    )]


def check_blind_tags(oracle: OracleSuite | None, spec: Spec) -> list[Finding]:
    """A blind suite that names no criterion proves nothing about the contract.

    Raised as one blocker rather than left to surface as a row of quiet
    `no_test` results, because those two readings ask for opposite things from a
    human. Twelve criteria each reporting "no blind test was tagged to this"
    reads as an oracle that declined the work. One finding saying the suite
    exists, ran, and is tagged to nothing reads as what it is: the verification
    layer is inert, and every criterion below it is unverified for that reason
    and not for anything about the code.

    The oracle's own prompt already says an untagged test proves nothing. This
    is the part that checks.
    """
    if oracle is None or not oracle.tests:
        return []
    tagged = [t for t in oracle.tests if t.criterion_ids]
    if tagged:
        return []
    return [Finding(
        id="untagged-1",
        title="The independent tests don't say which requirements they check",
        severity="blocker",
        category="verification",
        detail=(
            f"{len(oracle.tests)} file(s), tagged to no acceptance criterion. "
            "Every test the oracle wrote carries criterion ids that name nothing in "
            "this spec, so no criterion can be verified by any of them however they "
            "run. The suite is not missing and not failing -- it is unattached, and "
            "the criteria below are unverified because of that rather than because of "
            "anything in the code. Read no signal into their status until this is "
            "fixed."
        ),
        evidence="\n".join(
            f"{t.path}: {t.criterion_ids or '(none)'}" for t in oracle.tests)[:2000],
        files=[t.path for t in oracle.tests],
        recommendation=(
            "The ids are extracted from whatever the oracle writes, so this means it "
            "wrote no recognisable id at all. Check the criterion ids in the frozen "
            "spec and what the oracle returned for `criterion_ids`."
        ),
    )]


def check_oracle_requirements(oracle: OracleSuite | None, spec: Spec) -> list[Finding]:
    """The oracle named a capability it needed and did not have.

    This is a finding about the harness, and it is the only one in this file
    whose subject is neither the code nor the oracle's own work. The oracle is
    blind to the implementation on purpose; blindness to its environment is a
    different thing and buys nothing. A run that told it
    which packages it could import and nothing about what would be running spent
    2h43m and produced zero verified criteria, because the suite invented a live
    system that had to be provisioned for it and then stopped when it was not.

    A requirement is reported at whatever it actually cost. One naming criteria
    this spec has is a blocker: those criteria are unverified, the reason is
    outside the code, and a reader who is not told that will read the criterion
    table as a statement about the feature. One naming none is a minor note --
    still worth a human's attention, because the next run over this project hits
    the same wall, but it did not cost this packet anything.

    Ids are checked against the spec rather than trusted. An id that names no
    criterion here cannot have cost one, and quietly counting it would let a
    typo escalate a note into a blocker.
    """
    if oracle is None or not oracle.requires:
        return []
    known = {c.id for c in spec.acceptance_criteria}
    findings: list[Finding] = []
    for index, requirement in enumerate(oracle.requires, start=1):
        need = (requirement.need or "").strip()
        if not need:
            continue
        blocked = [cid for cid in requirement.criterion_ids if cid in known]
        unknown = [cid for cid in requirement.criterion_ids if cid not in known]
        cost = (
            f"{len(blocked)} criterion/criteria go unverified for want of it: "
            f"{', '.join(blocked)}"
            if blocked else
            "The oracle reported that this cost no criterion."
        )
        detail = (requirement.detail or "").strip()
        note = (
            "\n\nThe oracle also named "
            f"{', '.join(unknown)}, which this spec does not contain -- those ids "
            "are ignored here."
        ) if unknown else ""
        findings.append(Finding(
            id=f"requires-{index}",
            title=f"The independent tests needed something this environment doesn't have — {need}",
            severity="blocker" if blocked else "minor",
            category="verification",
            detail=(
                "The oracle could read this criterion and knew how to check it. What it "
                "lacked was a way to reach the system: the environment its suite runs in "
                "does not offer this, and an agent that has never seen the repository "
                "cannot arrange it.\n\n"
                f"{cost}\n\n"
                "Read those criteria as unverified because of the harness, not because "
                "of the code. Nothing here says the feature is wrong; it says nothing "
                "checked it." + (f"\n\nWhat the oracle would have done with it:\n\n{detail}"
                                 if detail else "") + note
            ),
            evidence=f"oracle requires: {need}" + (f"\n\n{detail}" if detail else ""),
            recommendation=(
                "Decide which this is. If the capability can be provided, describe it in "
                "this project's `oracle_runtime` so the next run's oracle is told about it, "
                "and declare a service or extend the command that runs its files if it "
                "needs a server, a variable or a database that is not already there. If it genuinely cannot be provided "
                "here, the criterion is not checkable by this pipeline and the spec is "
                "where that belongs -- either narrow it to something observable or accept "
                "it as verified by hand."
            ),
            criterion_ids=blocked,
        ))
    return findings


def check_blind_suite_loads(gates: GateReport) -> list[Finding]:
    """The blind suite failed to load, which is the oracle's bug, not the code's.

    Raised separately from every other red gate because it is the one failure
    that invalidates the rest of the report. When the suite does not import,
    every criterion is unverified -- and a reader looking at twelve unverified
    criteria beside a red test gate will conclude something about the feature,
    which is exactly wrong and has happened twice.

    The oracle writes tests it can never run: it has not seen the repository and
    it does not execute anything. So its files carry ordinary bugs like any
    unrun code, and the cost of one is a whole run's verification. Naming it
    here is the difference between "the feature is unverified because the
    verifier is broken" and the silence that reads as "the feature is broken".
    """
    blind = [g for g in gates.results if g.name == "blind-tests" and not g.passed]
    if not blind:
        return []
    result = blind[0]
    if "could not be loaded" not in (result.output_tail or ""):
        return []
    return [Finding(
        id="blind-load-1",
        title="Some independent tests would not run, so the requirements they cover were not checked",
        severity="blocker",
        category="verification",
        detail=(
            "The acceptance tests failed to import or collect, so none of them ran. "
            "Every criterion is unverified as a result, and that says nothing about "
            "the feature -- the fault is in the oracle's own files. Read no signal "
            "into the criterion table until this is fixed; the code beneath it has "
            "not been judged either way."
        ),
        evidence=(result.output_tail or "")[-2000:],
        files=[],
        recommendation=(
            "The oracle writes these blind and never runs them, so they carry "
            "ordinary bugs. Read the collection error: it names the file and the "
            "line. Re-running the feature gets a fresh suite."
        ),
    )]
