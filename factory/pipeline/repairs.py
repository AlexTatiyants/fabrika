"""Cutting a round's findings into repair units, and deciding whether the
loop goes round again. Arithmetic, not an agent.
"""

from __future__ import annotations

from typing import Callable, Sequence

from ..schemas import GateReport, GateResult, OracleSuite, RepairPlan, RepairUnit

from .oracle_suite import oracle_files
from .ledger import FindingLedger


def oracle_problems(
    gates: GateReport, blind: GateResult | None, oracle: OracleSuite,
) -> str:
    """What is wrong with the oracle's own files, in words it can act on.

    Two things, and both are about the files rather than the feature: a test
    file that would not load (with the error it printed), and a project check
    that fails only because of files the oracle wrote (with that check's
    output). A test that loads and fails an assertion is not here -- whether
    the feature or the test is wrong is the criterion table's question, and a
    test author told to make it pass would weaken the contract.

    Empty when there is nothing to fix.
    """
    mine = {f.path for f in oracle_files(oracle)}
    sections: list[str] = []
    for path in (blind.named_unloadable if blind else []):
        said = (blind.file_output.get(path) or "").strip() if blind else ""
        # A file with no output never ran -- its environment could not be
        # reset first -- and that is not the file's fault.
        if path in mine and said:
            sections.append(f"### `{path}` would not load\n\n```\n{said[-2000:]}\n```")
    for path in (blind.leaked if blind else []):
        if path in mine and path not in (blind.unstable if blind else []):
            sections.append(
                f"### `{path}` leaves something behind\n\nIt passed, then failed when run "
                "again straight after with nothing reset between -- so it breaks its own next "
                "run, and whatever runs after it. Make it clean up after itself the way this "
                "project's tests do (see how tests here clean up, above); do not change what "
                "it asserts.\n\n```\n" + (blind.leak_output.get(path) or "")[-2000:] + "\n```")
    for g in gates.results:
        if not g.passed and g.failed_on == "oracle_files":
            sections.append(
                f"### The project's check `{g.name}` fails on your files\n\n"
                f"`$ {g.command}`\n\n```\n{(g.output_tail or '')[-3000:]}\n```")
    return "\n\n".join(sections)


def plan_repairs(
    ledger: "FindingLedger", target_ids: Sequence[str], cap: int,
    writable: Callable[[str], bool] = lambda path: True,
) -> RepairPlan:
    """Cut a round's findings into units. Arithmetic, not an agent.

    A remediator is not asked for this, because the question it would be
    asked -- "which of these would you group, and which will you leave out" --
    is the wrong question in both halves.

    The grouping half is not a judgment. Units run against one tree and two
    of them writing one file is how a round produces a worse tree than it
    started with, so findings that name a shared file are one unit. That is
    a fact about the findings, computable, and computing it costs nothing
    and cannot be talked out of.

    The leaving-out half is actively harmful. Asked what it cannot fix, a
    remediator answers honestly and at length -- a frozen spec decision, a
    file it may not write -- and every one of those answers is a finding
    nobody works while the round it belongs to runs at a quarter capacity.
    Nothing is left out here. The round takes as many units as it has slots,
    in the order it was given, and whatever does not fit is simply not
    attempted: not charged, not deferred, not explained away.

    Order is preserved from `target_ids`, which arrives sorted by
    `human_first` -- a person's flags, then severity, then corroboration.

    A unit owns only files a repair may write (`writable`, or unlocked for it).
    One whose findings name nothing else is not a unit at all: it is listed in
    `deferred` and its findings go to a person. The alternative was measured
    twice in one run -- a unit whose brief listed a seam check both as the one
    file it owned and among the paths refused before they land, whose every
    edit was refused, and whose slot was the round's.
    """
    clusters: list[list[str]] = []
    where: dict[str, int] = {}
    unroutable: list[str] = []
    for fid in target_ids:
        finding = ledger.findings.get(fid)
        paths = [p for p in (finding.files if finding else []) if p]
        joined = {where[p] for p in paths if p in where}
        if joined:
            first, *rest = sorted(joined)
            clusters[first].append(fid)
            # Two files already in different clusters, joined by this finding.
            for other in reversed(rest):
                clusters[first].extend(clusters[other])
                clusters[other] = []
            for path, index in list(where.items()):
                if index in set(rest):
                    where[path] = first
            for path in paths:
                where[path] = first
        else:
            clusters.append([fid])
            for path in paths:
                where[path] = len(clusters) - 1

    units: list[RepairUnit] = []
    for group in [c for c in clusters if c]:
        if len(units) >= max(1, cap):
            break
        findings = [ledger.findings[f] for f in group if f in ledger.findings]
        if not findings:
            continue
        named = sorted({p for f in findings for p in f.files if p})
        # A test this unit may rewrite, because a finding routed to it says
        # that test asserts something the design makes impossible. Routed
        # before the round by a human or the arbiter (`oracle`), so the unit
        # cannot decide mid-repair that the test was the problem.
        unlocked = sorted({
            path for f in findings
            if (rec := ledger.records.get(f.id)) is not None and rec.disposition == "oracle"
            for path in f.files if path})
        files = [p for p in named if p in unlocked or writable(p)]
        if named and not files:
            unroutable += [
                f"{f.id}: every file it names ({', '.join(named)}) is one a repair may "
                "not change, so no repairer could act on it"
                for f in findings]
            continue
        units.append(RepairUnit(
            id=f"R-{len(units) + 1}",
            title=findings[0].title,
            finding_ids=[f.id for f in findings],
            # The findings' own words. A paraphrase here would be a model's
            # reading of a finding standing between the finding and the agent
            # that has to act on it.
            objective=(
                "Close the findings below, and nothing else. Make the narrowest change "
                "that resolves them; do not improve anything on the way past.\n\n"
                + "\n\n".join(
                    f"- {f.id} [{f.severity}] {f.title}\n  {f.detail}"
                    + (f"\n  Evidence: {f.evidence}" if f.evidence else "")
                    + (f"\n  Recommended: {f.recommendation}" if f.recommendation else "")
                    + (f"\n  Routed here because: {r.disposition_reason}"
                       if (r := ledger.records.get(f.id)) is not None
                       and r.disposition_reason else "")
                    for f in findings)),
            files_expected=files,
            unlocked=unlocked,
        ))

    attempted = {fid for unit in units for fid in unit.finding_ids}
    waiting = [fid for fid in target_ids if fid not in attempted]
    return RepairPlan(
        summary=(
            f"{len(units)} unit(s) from {len(attempted)} finding(s), grouped where they "
            "name a shared file."
            + (f" {len(waiting)} did not fit this round and were not attempted."
               if waiting else "")),
        units=units,
        deferred=unroutable,
    )


def reopen_after_panel(
    targets: Sequence[str], *, enabled: bool, rate_limited: bool,
    round_index: int, max_rounds: int, affordable: bool, out_of_time: bool,
) -> list[str]:
    """Whether the panel on the way out has sent the loop back round.

    The loop exits through a full panel because a repair can break something
    nobody was looking at, and only an agent reading the whole change would see
    it. That panel is a real panel: in one run it was five agents finding, at
    once, that a patient's bearer token was being written into the audit table
    and handed back by the endpoint the same change had just added.

    Found *after* the decision to stop, all of it would be routed for repair
    and then dropped on the floor, and the packet would say so in the sentence
    nobody wants to read -- "routed for repair and never attempted: the loop
    stopped first" -- with rounds and budget left unspent (four of five rounds
    and most of the budget, in that run). `_converge` promises that "every
    stopping condition below is a number in factory.yaml"; converging while
    holding work somebody has just decided to do is not one of those numbers,
    it is an artefact of the order things happen in.

    So: if the panel gave the loop something to repair and no ceiling has been
    reached, the loop is not finished. Every ceiling here is still a number --
    the round cap, the budget, the wall clock, the plan's own window -- and the
    per-finding attempt cap inside `repairable` bounds it besides.
    """
    if not targets:
        return []
    if not enabled or rate_limited:
        return []
    if round_index >= max_rounds or not affordable or out_of_time:
        return []
    return list(targets)
