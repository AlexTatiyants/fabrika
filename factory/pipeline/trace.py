"""Traceability, computed both ways: which files answer to which criterion
(INV-3 forwards), and which files no criterion accounts for (backwards).
"""

from __future__ import annotations

import re
from typing import Collection, Sequence

from ..schemas import (
    Decision,
    FileWrite,
    IntegrationReport,
    OracleSuite,
    Plan,
    Spec,
    TraceRow,
    UnclaimedFile,
    WorkerOutput,
)


# ==========================================================================
# pure functions -- the invariants
# ==========================================================================


def criterion_files(
    workers: Sequence[WorkerOutput],
    extra_decisions: Sequence[Decision] = (),
    plan: Plan | None = None,
    written: Sequence[FileWrite] = (),
) -> dict[str, set[str]]:
    """Which files answer to which criterion. One index, computed, used twice.

    `compute_trace` walks it forwards and `compute_unclaimed` inverts it, so the
    two must never be built from different sources. Built separately, they let
    a real packet report AC-9 and AC-10 as traced while listing
    `TrialDetail.tsx` and `Dashboard.tsx`, the 1,305 lines that *are* AC-9 and
    AC-10, under "Not asked for" -- both statements from this module, minutes
    apart.

    The primary source is a decision that names files and names the criteria
    they serve. The second source is for the case that produces that
    contradiction -- a file the plan assigned to a criterion, which somebody
    wrote, but whose author attached no criterion to its decision. That
    happens whenever a repair round or the integrator writes a file the
    original unit never delivered.

    INV-3 survives it, and two constraints are why.

    A planned file counts only if it is in `written`. The plan alone can never
    make a criterion look implemented -- if the unit produced nothing, nothing
    is claimed, and the criterion is still an orphan. Intent is not evidence;
    intent plus a file on the branch is.

    And a criterion a worker *disclaimed* is never traced this way, whatever the
    plan says. A unit that owns three criteria and writes one file has not
    thereby implemented all three, and the author is the one who knows: when a
    worker puts an AC id in `not_implemented`, that is the most reliable signal
    in the run and it outranks the plan's intent. Without this a unit that
    wrote `spin.py` and said in as many words that the stop path was missing
    would have AC-2 traced to `spin.py`.
    """
    index: dict[str, set[str]] = {}
    for decision in [d for w in workers for d in w.decisions] + list(extra_decisions):
        for cid in decision.criterion_ids:
            index.setdefault(cid, set()).update(f for f in decision.files if f)

    on_branch = {f.path for f in written}
    if plan is None or not on_branch:
        return index

    disclaimed = disclaimed_criteria(workers)
    for unit in plan.units:
        for cid in unit.criterion_ids:
            # Gaps only. The plan says which criteria a unit is *for*, not which
            # of its files serve which -- so a unit owning seven files and eight
            # criteria would otherwise claim all seven for all eight -- ten of
            # twelve criteria traced to the same seven files, and AC-1 ("the
            # table gains four columns") listing `queue.py` and `patients.py`
            # among its implementation.
            #
            # That is worse than the false orphan the plan is consulted to fix.
            # The packet's promise is that opening a criterion shows only the
            # code serving it, and a whole-unit answer is not an answer.
            #
            # So the plan is consulted only where the decisions said nothing at
            # all. Where a worker named files for a criterion, that is a real
            # attribution by the agent that wrote the code, and nothing here
            # improves on it.
            if cid in disclaimed or index.get(cid):
                continue
            for path in unit.files_expected:
                if path in on_branch:
                    index.setdefault(cid, set()).add(path)
    return index


def disclaimed_criteria(workers: Sequence[WorkerOutput]) -> set[str]:
    """Criteria a worker said, in its own words, that it did not build.

    Read out of `not_implemented` by looking for AC ids in it. Matched on a word
    boundary so `AC-1` does not also claim `AC-12`, which is the kind of thing
    that would be silent and wrong for a long time.

    Deliberately one-way: this can only *remove* a trace the plan would
    otherwise have inferred, never add one. A disclosure that names no id is
    still a disclosure -- it just cannot be attached to a criterion here, and
    the worker's own summary carries it into the packet regardless.
    """
    out: set[str] = set()
    for worker in workers:
        for line in worker.disclosure.not_implemented:
            out.update(m.group(0) for m in re.finditer(r"\bAC-\d+\b", line or ""))
    return out


def compute_trace(
    spec: Spec,
    workers: Sequence[WorkerOutput],
    oracle: OracleSuite | None,
    extra_decisions: Sequence[Decision] = (),
    plan: Plan | None = None,
    written: Sequence[FileWrite] = (),
    manual: Collection[str] = (),
) -> list[TraceRow]:
    """AC-8.8 / INV-3 -- traceability is computed, never asked for.

    `manual` names the criteria a person checks by hand because their test
    level cannot run cleanly here (`workspace.unchecked_levels`). Implemented,
    they are `manual`, not `untested`: nothing was supposed to test them, and
    saying "no test" would read as the oracle having missed one.

    A model must not be able to report a criterion as traced when nothing
    implements it, so no model is consulted here. Implementation comes from
    `criterion_files`; tests come from oracle test tags.
    """
    implementing = criterion_files(workers, extra_decisions, plan, written)

    tested: dict[str, set[str]] = {}
    for test in (oracle.tests if oracle else []):
        for cid in test.criterion_ids:
            tested.setdefault(cid, set()).add(test.path)

    rows: list[TraceRow] = []
    for criterion in spec.acceptance_criteria:
        files = sorted(implementing.get(criterion.id, set()))
        tests = sorted(tested.get(criterion.id, set()))
        if not files:
            status = "orphan_requirement"
        elif criterion.id in manual:
            status = "manual"
        elif not tests:
            status = "untested"
        else:
            status = "traced"
        rows.append(TraceRow(
            criterion_id=criterion.id,
            statement=criterion.statement,
            implementing_files=files,
            test_names=tests,
            status=status,
        ))
    return rows


#: {path: (added, removed)}, measured off the branch by `Sandbox.diffstat`.
DiffStat = dict[str, tuple[int, int]]


def change_size(
    path: str, contents: str, diffstat: DiffStat | None,
) -> tuple[int, int]:
    """(added, removed) for one file: what the branch says, or all-new if there
    is no branch to ask.

    Every "N lines" a human reads in the packet comes through here. Not
    `len(contents.splitlines())` at each call site, which is the size of the
    file an agent handed back rather than the size of what it changed -- and
    since `FileWrite.contents` is always the complete file, the two are only the
    same number for a file that did not exist before.

    The fallback is that case and not a shrug: a path the diff cannot account
    for has no earlier version to compare against, so every line in it is new.
    It is also the most a caller without a sandbox -- a unit test, a stored
    packet being re-read -- can truthfully claim.
    """
    if diffstat is not None and path in diffstat:
        return diffstat[path]
    return (len(contents.splitlines()) if contents else 0, 0)


def compute_unclaimed(
    workers: Sequence[WorkerOutput],
    written: Sequence[FileWrite],
    extra_decisions: Sequence[Decision] = (),
    integration: IntegrationReport | None = None,
    plan: Plan | None = None,
    diffstat: DiffStat | None = None,
) -> list[UnclaimedFile]:
    """INV-3 read backwards: which files no criterion accounts for.

    `compute_trace` walks the criteria and asks what implements each one. A file
    that no decision ties to any criterion is invisible to that walk -- it is in
    the materiality table and in the diff, and nothing anywhere says what it
    answers to. That is the shape a scope widening has, and the shape a default
    nobody asked for has.

    Same index, inverted, and no model is consulted here either. A file cannot
    reach this list because an agent chose to mention it, and cannot escape it
    by staying quiet.
    """
    # The same index `compute_trace` walks forwards, so the two cannot disagree
    # about whether a file answers to a criterion.
    claimed = {
        path
        for paths in criterion_files(workers, extra_decisions, plan, written).values()
        for path in paths
    }

    # Who wrote it: from the writes themselves, not from what a decision claims,
    # so a file written without any decision naming it still has an author.
    authors: dict[str, set[str]] = {}
    if integration is not None:
        # The integrator writes files too, and an unclaimed file with no author
        # is the one row on that screen a human cannot act on.
        for write in integration.files:
            authors.setdefault(write.path, set()).add("integrator")
    for worker in workers:
        for write in worker.files:
            authors.setdefault(write.path, set()).add(worker.unit_id)
        for decision in worker.decisions:
            for path in decision.files:
                if path:
                    authors.setdefault(path, set()).add(worker.unit_id)

    # An unclaimed file's only account of itself is usually its author's own
    # disclosure. Carry it verbatim (INV-9) rather than paraphrasing it here.
    said: dict[str, list[str]] = {}
    for worker in workers:
        lines = (list(worker.disclosure.assumptions)
                 + list(worker.disclosure.deviations_from_spec)
                 + list(worker.disclosure.flags))
        if not lines:
            continue
        for write in worker.files:
            if write.path not in claimed:
                said.setdefault(write.path, []).extend(
                    f"{worker.unit_id}: {line}" for line in lines)

    rows: list[UnclaimedFile] = []
    for write in written:
        if write.path in claimed:
            continue
        added, removed = change_size(write.path, write.contents, diffstat)
        rows.append(UnclaimedFile(
            path=write.path,
            lines=added + removed,
            added=added,
            removed=removed,
            written_by=sorted(authors.get(write.path, set())),
            disclosures=said.get(write.path, []),
        ))
    return sorted(rows, key=lambda row: row.path)
