"""Merging partial reports, scoring the criteria, and the packet a human reads
(INV-4: the rapporteur presents; it cannot omit).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..gates import TestSummary, match_case
from ..schemas import (
    BreakerReport,
    CaseOutcome,
    CriterionResult,
    Decision,
    FileWrite,
    Finding,
    FindingRecord,
    GateReport,
    GateResult,
    MaterialityItem,
    OracleSuite,
    ManualCheck,
    Packet,
    PacketStats,
    QAReport,
    ReviewReport,
    ReworkSummary,
    ScoutReport,
    Spec,
    TraceRow,
    UnclaimedFile,
    WorkerOutput,
    PacketJudgement,
)
from ..workspace import PathEscape, safe_join

from .text import VERDICT_RANK
from .trace import DiffStat, change_size


def merge_scouts(reports: Sequence[ScoutReport]) -> ScoutReport:
    """Union several partial views of one repository into a complete one.

    Deterministic on purpose. A model asked to merge nine reports is a model
    with the opportunity to drop the one entry that mattered, and the whole
    reason for slicing was to stop losing things. De-duplication is by a
    normalised prefix, so two scouts describing the same convention in slightly
    different words collapse to one.
    """
    def union(field: str) -> list[str]:
        seen: dict[str, str] = {}
        for report in reports:
            for item in getattr(report, field, []) or []:
                key = re.sub(r"\W+", " ", str(item).lower()).strip()[:70]
                if key and key not in seen:
                    seen[key] = str(item)
        return list(seen.values())

    def union_by(field: str, key) -> list:
        seen: dict[str, Any] = {}
        for report in reports:
            for item in getattr(report, field, []) or []:
                k = re.sub(r"\W+", " ", str(key(item)).lower()).strip()[:70]
                if not k:
                    continue
                if k in seen:
                    # The same rule seen in two slices holds in both sets of files.
                    held = seen[k]
                    held.files = list(dict.fromkeys([*held.files, *item.files]))
                    if hasattr(held, "in_feature_area"):
                        held.in_feature_area = held.in_feature_area or item.in_feature_area
                else:
                    seen[k] = item.model_copy(deep=True)
        return list(seen.values())

    summaries = [r.summary.strip() for r in reports if r.summary.strip()]
    return ScoutReport(
        summary="\n\n".join(summaries),
        stack=union("stack"),
        observed_conventions=union_by("observed_conventions", lambda c: c.slug or c.rule),
        contradictions=union_by("contradictions", lambda c: f"{c.guide} {c.rule}"),
        existing_capabilities=union("existing_capabilities"),
        do_not_duplicate=union("do_not_duplicate"),
        relevant_files=union("relevant_files"),
        risks=union("risks"),
    )


def merge_reviews(reports: Sequence[ReviewReport]) -> ReviewReport:
    """Fold several passes over the same evidence into one report.

    Used both across samples of a single agent and across every review agent.
    De-duplicate by title keeping the harshest reading, keep any objection
    raised even once, and let the worst verdict win. The point of sampling is
    that one pass sees what the others talked themselves out of, so nothing is
    dropped for having been said once.
    """
    if not reports:
        return ReviewReport(summary="", verdict="accept")

    findings = dedupe_findings([f for r in reports for f in r.findings])
    worst = max(reports, key=lambda r: VERDICT_RANK.get(r.verdict, 0))
    conceded: list[str] = []
    for report in reports:
        for item in report.conceded:
            if item not in conceded:
                conceded.append(item)
    return ReviewReport(
        summary=worst.summary,
        verdict=worst.verdict,
        strongest_objection=worst.strongest_objection,
        findings=findings,
        conceded=conceded,
    )


def what_landed(files: Sequence[FileWrite], root: Path | None,
                guarded: Callable[[str], bool]) -> str:
    """What of a unit's work is on the branch, read off the branch.

    A unit's summary is written from its own diff, before anything decides
    whether that diff lands. A write refused as protected is described there
    as done -- "the unit's sole change is to web/e2e/moveCard.seam.spec.ts" --
    and two reviewers, reading that, reported a check "made green by changing
    the check itself". Nothing had changed it: the write was refused, and the
    only place that said so was a separate finding a thousand lines away.

    Said only where it differs from the account, so a unit whose files all
    landed costs the reader one line.
    """
    if root is None or not files:
        return ""
    refused: list[str] = []
    changed: list[str] = []
    gone: list[str] = []
    for f in files:
        try:
            now: str | None = safe_join(root, f.path).read_text(encoding="utf-8")
        except (OSError, PathEscape, UnicodeDecodeError):
            now = None
        if now == f.contents or (f.deleted and now is None):
            continue
        if guarded(f.path):
            refused.append(f.path)
        elif now is None:
            gone.append(f.path)
        else:
            changed.append(f.path)
    head = "### What reached the branch (read off the branch, not from the account above)\n"
    if not (refused or changed or gone):
        return head + f"All {len(files)} file(s) this unit wrote are on the branch as it wrote them.\n\n"
    lines = [head]
    if refused:
        lines.append(
            "- **Refused, and never on the branch:** " + ", ".join(f"`{p}`" for p in refused)
            + ". A repair may not change these, so whatever the account above says it did "
            "to them did not happen.")
    if changed:
        lines.append("- Changed since by later work, so the branch no longer holds this unit's "
                     "version: " + ", ".join(f"`{p}`" for p in changed))
    if gone:
        lines.append("- Not on the branch: " + ", ".join(f"`{p}`" for p in gone))
    return "\n".join(lines) + "\n\n"


def dedupe_findings(findings: Sequence[Finding]) -> list[Finding]:
    """One finding per distinct objection, keeping the harshest reading.

    Used wherever the same context is sampled more than once: the point of
    sampling is that one pass sees what the others missed, so nothing is dropped
    for having been raised only once.
    """
    seen: dict[str, Finding] = {}
    for finding in findings:
        key = re.sub(r"\W+", " ", finding.title.strip().lower()).strip()
        existing = seen.get(key)
        if existing is None or _severity_rank(finding.severity) > _severity_rank(existing.severity):
            seen[key] = finding.model_copy(deep=True)
    return sorted(seen.values(), key=lambda f: -_severity_rank(f.severity))


def _severity_rank(severity: str) -> int:
    return {"nit": 0, "minor": 1, "major": 2, "blocker": 3}.get(severity, 1)


def order_packet(
    judgement: PacketJudgement,
    decisions: Sequence[Decision],
    findings: Sequence[Finding],
) -> Packet:
    """Turn the rapporteur's ordering into a packet, using ids and nothing else.

    The text of a decision comes from the worker that made it and the text of a
    finding from the agent that raised it. The rapporteur ranks them; it never
    re-types them, so it cannot soften one in passing. An id it names that does
    not exist is ignored, and anything it leaves out is added by
    `restore_packet` immediately after this -- omission is not available to it.
    """
    by_decision = {d.id: d for d in decisions}
    ranked: list[Decision] = []
    for rank, decision_id in enumerate(judgement.decision_order, start=1):
        source = by_decision.pop(decision_id, None)
        if source is not None:
            ranked.append(source.model_copy(deep=True, update={"rank": rank}))

    by_finding = {f.id: f for f in findings}
    ordered: list[Finding] = []
    for finding_id in judgement.finding_order:
        source = by_finding.pop(finding_id, None)
        if source is not None:
            ordered.append(source.model_copy(deep=True))

    return Packet(
        headline=judgement.headline,
        gist=judgement.gist,
        verdict=judgement.verdict,
        attention_budget_minutes=judgement.attention_budget_minutes,
        summary=judgement.summary,
        strongest_objection=judgement.strongest_objection,
        materiality=[m.model_copy(deep=True) for m in judgement.materiality],
        decisions=ranked,
        findings=ordered,
    )


def restore_packet(
    packet: Packet,
    *,
    decisions: Sequence[Decision] = (),
    findings: Sequence[Finding] = (),
    files: Sequence[FileWrite] = (),
    trace: Sequence[TraceRow] = (),
    unclaimed: Sequence[UnclaimedFile] = (),
    disclosures: Sequence[str] = (),
    open_questions: Sequence[str] = (),
    stats: PacketStats | None = None,
    records: Sequence[FindingRecord] = (),
    rework: ReworkSummary | None = None,
    diffstat: DiffStat | None = None,
) -> Packet:
    """AC-8.9 / INV-4 -- the rapporteur presents; it cannot omit.

    Restore every collected decision, every finding, every written file. Files
    it failed to classify default to `novel`. The computed trace and the
    computed unclaimed list both overwrite whatever it returned. A presenter that can hide things is the most dangerous
    agent in the pipeline, so its power is structurally limited to ordering and
    labelling.
    """
    out = packet.model_copy(deep=True)

    kept_decisions = {d.id: d for d in out.decisions}
    next_rank = max((d.rank for d in out.decisions), default=0)
    for decision in decisions:
        if decision.id not in kept_decisions:
            next_rank += 1
            restored = decision.model_copy(deep=True)
            restored.rank = next_rank
            out.decisions.append(restored)
    out.decisions.sort(key=lambda d: (d.rank if d.rank else 10_000, d.id))

    seen_findings = {f.id for f in out.findings} | {
        re.sub(r"\W+", " ", f.title.strip().lower()).strip() for f in out.findings
    }
    for finding in findings:
        key = re.sub(r"\W+", " ", finding.title.strip().lower()).strip()
        if finding.id not in seen_findings and key not in seen_findings:
            out.findings.append(finding.model_copy(deep=True))
            seen_findings.add(finding.id)
            seen_findings.add(key)
    out.findings.sort(key=lambda f: -_severity_rank(f.severity))

    classified = {m.path for m in out.materiality}
    for f in files:
        if f.path not in classified:
            out.materiality.append(MaterialityItem(
                path=f.path,
                klass="novel",
                reason="Not classified by the rapporteur. Unclassified defaults to novel.",
            ))
            classified.add(f.path)
    # The rapporteur classifies; it does not get to say how big a change is.
    # Measured off the branch and written over whatever it reported, for the
    # same reason its unclaimed list is discarded: a number a model chose is a
    # claim, and this one is checkable.
    sizes = {f.path: change_size(f.path, f.contents, diffstat) for f in files}
    for item in out.materiality:
        if item.path in sizes:
            item.added, item.removed = sizes[item.path]
            item.lines = item.added + item.removed

    out.trace = [row.model_copy(deep=True) for row in trace]
    out.unclaimed = [row.model_copy(deep=True) for row in unclaimed]

    for line in disclosures:
        if line not in out.disclosures:
            out.disclosures.append(line)
    for question in open_questions:
        if question not in out.open_questions:
            out.open_questions.append(question)

    if stats is not None:
        out.stats = stats.model_copy(deep=True)
    # INV-11 -- the same principle as INV-4, applied to time rather than to one
    # agent. What happened to a finding is computed from the rounds; the
    # rapporteur orders the list and cannot edit an outcome, and it cannot drop
    # a repaired finding on the grounds that it is no longer a problem.
    out.records = [r.model_copy(deep=True) for r in records]
    if rework is not None:
        out.rework = rework.model_copy(deep=True)
    return out


def _gate_evidence(
    gates: GateReport, scope: Sequence[GateResult], blind_paths: Sequence[str],
) -> tuple[set[str], set[str], TestSummary, bool]:
    """The run's parsed facts, and whether they can settle a negative.

    Positives and negatives are not symmetric here, and conflating them is what
    put two false passes in a real packet. Finding a test's name on a failing
    line proves it failed, whatever else was lost to truncation. *Not* finding
    it proves nothing unless the search covered the whole output and was
    actually looking for that name.

    So: positives and negatives both come from the gates' own test reports,
    which name every test and say what became of each. A gate that never went
    through `record_evidence` -- hand-built in a test, or restored from a run
    older than this -- contributes nothing and clears `parsed_all`, because the
    alternative is reading its console tail, and reading tails is what put two
    false passes in a real packet. Negatives are gated on `settled`, which
    requires every gate in scope to have been parsed *and* to have been handed
    the blind tests to witness. Anything less and the caller must say it does
    not know.
    """
    named_failing: set[str] = set()
    witnessed: set[str] = set()
    parsed_all = bool(scope)

    totals = {"passed": 0, "failed": 0, "total": 0}
    known = False
    zero_ran = False

    for gate in scope:
        if gate.evidence_parsed:
            witnessed.update(gate.witnessed)
            if gate.summary_known:
                known = True
            totals["passed"] += gate.tests_passed
            totals["failed"] += gate.tests_failed
            totals["total"] += gate.tests_total
            zero_ran = zero_ran or gate.zero_ran
        else:
            # A result that never went through `record_evidence`: hand-built in
            # a test, or restored from a run older than reports existed. There
            # is nothing here to read -- the console tail is not evidence,
            # which is the whole point of reading reports -- so it contributes
            # nothing and `settled` refuses every negative that depended on it.
            parsed_all = False

    for gate in gates.results:
        if gate.passed or not gate.evidence_parsed:
            continue
        named_failing.update(gate.named_failing)

    settled = parsed_all and (not blind_paths or set(blind_paths) <= witnessed)
    summary = TestSummary(
        known=known, passed=totals["passed"], failed=totals["failed"],
        total=totals["total"], zero_ran=zero_ran or (known and totals["total"] == 0),
    )
    return named_failing, summary, settled


def _why_unsettled(scope: Sequence[GateResult], blind_paths: Sequence[str]) -> str:
    """Which of the two reasons a negative could not be settled, in words a
    human can act on. Both are harness faults, not facts about the code."""
    unparsed = [g.name for g in scope if not g.evidence_parsed]
    if unparsed:
        return (
            "the complete output of "
            + ", ".join(sorted(unparsed)[:3])
            + " was not parsed, so only its truncated tail is available"
        )
    witnessed: set[str] = set()
    for gate in scope:
        witnessed.update(gate.witnessed)
    missing = [p for p in blind_paths if p not in witnessed]
    if missing:
        return (
            f"{len(missing)} blind test(s) were never handed to a gate to witness, "
            f"beginning with {missing[0]}"
        )
    return "the run recorded no evidence capable of settling it"


def case_verdicts(
    oracle: OracleSuite | None, gates: GateReport,
) -> tuple[dict[str, list[CaseOutcome]], dict[str, list[str]]]:
    """Per-criterion test outcomes, and the declarations nothing ran.

    The first is what turns "the file failed" into "this test failed". The
    second is why that is safe: a test the oracle tagged and the runner never
    reported is an attribution failure, and it has to read as one. Reported as a
    passing criterion it would be a lie; reported as a failing one it would
    blame the feature for a name that did not match.
    """
    reported: list[CaseOutcome] = [c for g in gates.results for c in g.cases]
    by_criterion: dict[str, list[CaseOutcome]] = {}
    missing: dict[str, list[str]] = {}
    if not oracle or not reported:
        return by_criterion, missing
    for test_file in oracle.tests:
        here = [c for c in reported if not c.file or c.file == test_file.path]
        for case in test_file.cases:
            found = match_case(case.name, here)
            for cid in case.criterion_ids:
                if found:
                    by_criterion.setdefault(cid, []).extend(found)
                else:
                    missing.setdefault(cid, []).append(
                        f"{test_file.path}::{case.name}")
    return by_criterion, missing


def reported_in(unmatched: Sequence[str], gates: GateReport) -> str:
    """What the runner reported for the files these unmatched names are in, failures first."""
    files = {name.split("::", 1)[0] for name in unmatched if "::" in name}
    cases = [c for g in gates.results for c in g.cases if c.file in files]
    if not cases:
        return ""
    bad = [c.name for c in cases if c.status in ("failed", "errored")]
    good = [c.name for c in cases if c.status == "passed"]
    parts = []
    if bad:
        parts.append(f"{len(bad)} failed ({', '.join(bad)})")
    if good:
        parts.append(f"{len(good)} passed")
    return (f"The runner did report {len(cases)} test(s) in that file, under other names: "
            + "; ".join(parts) + ".")


def compute_qa(
    spec: Spec,
    oracle: OracleSuite | None,
    gates: GateReport,
    trace: Sequence[TraceRow],
) -> QAReport:
    """Map gate outcomes onto criteria. No model: a test either ran and passed
    or it did not, and asking a model to characterise that invites a story."""
    # A criterion is only "passed" if something actually executed its tests. A
    # gate that could not start proves nothing, and calling that green is the
    # cheapest way to make this whole system worthless.
    test_gates = [g for g in gates.results if "test" in g.name.lower()]
    tests_all_green = bool(test_gates) and all(g.passed for g in test_gates)
    if not test_gates:
        tests_all_green = gates.passed

    # Evidence comes from `gates.record_evidence`, which parsed each runner's
    # complete output before it was truncated for display. Reading `output_tail`
    # here instead is how two criteria have been awarded a pass because the
    # only test covering them had its filename cut in half by the 4,000-char
    # boundary. `blind_paths` names what has to be settled; `negatives_settled`
    # says whether this run's evidence is capable of settling it.
    scope = test_gates or list(gates.results)
    blind_paths = [name for row in trace for name in row.test_names]
    named_failing, summary, negatives_settled = _gate_evidence(
        gates, scope, blind_paths)

    # The exit code says the process was happy. It does not say a test ran, and
    # nothing here reads a runner's output to find out -- the blind suite is run
    # one file at a time so each file's exit code settles its own criteria.
    # Which leaves one gap, deliberately: a file whose tests were skipped or
    # collected away exits zero exactly as a passing one does. Whether this
    # project's runner is willing to exit zero over nothing was measured at gate
    # 0, and `check_blind_attribution` puts the answer in the packet.

    # If a test gate failed but its output names none of the blind tests, we
    # cannot tell which criteria the failure belongs to. Silence is not a pass.
    attributable = bool(named_failing)
    # Files the load probe could not even import. Empty for a project whose
    # rules carry no `collect`, which leaves every verdict exactly as it was.
    unloadable = {p for g in gates.results for p in g.named_unloadable}
    # Per-test outcomes where a runner reported them, and the tags that matched
    # nothing. Both empty for a project with no `report` command, and then every
    # verdict below is reached file by file.
    by_case, missing_cases = case_verdicts(oracle, gates)
    rows: list[CriterionResult] = []
    failures: list[str] = []

    for row in trace:
        if row.status == "manual":
            rows.append(CriterionResult(
                criterion_id=row.criterion_id,
                status="manual",
                evidence="Not tested by Fabrika: its test level can't run cleanly in this "
                         "project, so a person checks it by hand at review.",
            ))
            continue
        if not row.test_names:
            rows.append(CriterionResult(
                criterion_id=row.criterion_id,
                status="no_test",
                evidence="No blind test was tagged to this criterion.",
            ))
            continue
        # Individual tests first, where the runner reported them. A file is the
        # coarsest thing that can be judged, and judged by file alone one test
        # disagreeing about one field fails the fourteen criteria its file
        # happens to carry, thirteen of which have tests that pass. Where a
        # report exists, this is the difference between "nothing was verified"
        # and one named disagreement.
        mine = by_case.get(row.criterion_id) or []
        unmatched = missing_cases.get(row.criterion_id) or []
        if unmatched:
            # What the runner did say about those files. The verdict stays
            # unknown -- which test is which cannot be told from here -- but a
            # file that reported two failures must not reach a person as a
            # bookkeeping fault and nothing else. One project's API suite did:
            # its names matched nothing, and two failing tests read as
            # "unknown".
            said = reported_in(unmatched, gates)
            status, evidence = "unknown", (
                "The oracle tagged tests to this criterion that the runner never "
                f"reported: {', '.join(unmatched)}. That is a fault in the suite's "
                "own bookkeeping -- a name that matched nothing -- and settles "
                "nothing about the code either way." + (f" {said}" if said else "")
            )
            failures.append(
                f"{row.criterion_id}: tagged test(s) {', '.join(unmatched)} were never "
                "reported by the runner" + (f". {said}" if said else ""))
            rows.append(CriterionResult(
                criterion_id=row.criterion_id, status=status,
                evidence=evidence, test_names=list(row.test_names)))
            continue
        if mine:
            bad = [c for c in mine if c.status in ("failed", "errored")]
            skipped = [c for c in mine if c.status == "skipped"]
            ran = [c for c in mine if c.status == "passed"]
            if bad:
                status, evidence = "failed", (
                    "Failed: " + ", ".join(f"{c.name} ({c.status})" for c in bad))
                failures.append(
                    f"{row.criterion_id}: {', '.join(c.name for c in bad)} failed")
            elif not ran:
                status, evidence = "not_collected", (
                    "Every test for this criterion was skipped: "
                    + ", ".join(c.name for c in skipped))
                failures.append(
                    f"{row.criterion_id}: every test for it was skipped")
            else:
                status, evidence = "passed", (
                    f"{len(ran)} test(s) passed: " + ", ".join(c.name for c in ran))
            rows.append(CriterionResult(
                criterion_id=row.criterion_id, status=status,
                evidence=evidence, test_names=list(row.test_names)))
            continue

        # Asked before "did it fail", because a file that never loaded also
        # exits non-zero and would otherwise be reported as this criterion
        # failing -- which reads as a defect in the feature and has been acted
        # on as one. It is a defect in the test.
        stalled = [t for t in row.test_names if t in unloadable]
        mentioned = [t for t in row.test_names if t in named_failing]
        if stalled:
            status, evidence = "not_collected", (
                "The blind test for this criterion could not be loaded, so it checked "
                f"nothing: {', '.join(stalled)}. This is a fact about that file -- a "
                "broken import, a syntax error, no test the runner could find -- and "
                "not about the code it was written to test."
            )
            failures.append(
                f"{row.criterion_id}: blind test {', '.join(stalled)} did not load, "
                "so nothing was checked")
        elif mentioned:
            status, evidence = "failed", f"Named in failing gate output: {', '.join(mentioned)}"
            failures.append(f"{row.criterion_id}: blind test {', '.join(mentioned)} failed")
        elif summary.zero_ran:
            status, evidence = "not_collected", (
                "The test gate exited zero and its own test report says no tests ran "
                "at all. Nothing was checked."
            )
            failures.append(
                f"{row.criterion_id}: the test gate passed without executing a single test")
        elif tests_all_green:
            if summary.known:
                evidence = (
                    "The blind test file(s) tagged to this criterion were run on their own "
                    f"and exited zero ({summary.passed} of {summary.total} file(s) green "
                    "across the suite)."
                )
            else:
                evidence = (
                    "The test gate passed. The blind suite ran as one command rather than "
                    "file by file, so this criterion's pass is the whole suite's exit code "
                    "and not its own."
                )
            status = "passed"
        elif attributable and negatives_settled:
            status, evidence = "passed", (
                "A test gate failed. Every blind test file was run on its own, the ones that "
                "failed are named, and this criterion's files are not among them."
            )
        elif attributable:
            # Some blind test was named failing, this one was not -- but the
            # search that failed to find it could not have found it. This is the
            # exact shape of the defect that reported AC-3 and AC-4 as passing
            # while the only file testing them was erroring: absence from an
            # incomplete search is not evidence of anything.
            status, evidence = "unknown", (
                "A test gate failed and this criterion's files are not among those that failed "
                "-- but the run's evidence cannot settle a negative "
                f"({_why_unsettled(scope, blind_paths)}), so their absence proves nothing. "
                "Unsettled is not passed."
            )
        else:
            status, evidence = "not_run", (
                "A test gate failed without naming any blind test, so this criterion's outcome "
                "cannot be attributed. Unattributed is not passed."
            )
        rows.append(CriterionResult(
            criterion_id=row.criterion_id, status=status, evidence=evidence,
            test_names=list(row.test_names),
        ))

    if summary.zero_ran and blind_paths:
        failures.append(
            "the suite reported no tests executed: every criterion below is unverified "
            "regardless of the exit code")

    untestable = list(oracle.untestable_criteria) if oracle else []
    for cid in untestable:
        failures.append(f"{cid}: the oracle could not test this from the spec alone")

    unverified = sum(1 for r in rows if r.status in ("not_run", "not_collected", "unknown"))
    unsettled = [r.criterion_id for r in rows if r.status == "unknown"]
    if unsettled:
        # Said as a failure, not a footnote. This is the run telling the human
        # that its own evidence is not good enough to judge these criteria --
        # which is a fact about the harness, and the one thing a reader would
        # otherwise never learn, because the alternative is to call them
        # passed.
        failures.append(
            f"{len(unsettled)} criteri{'on' if len(unsettled) == 1 else 'a'} "
            f"({', '.join(unsettled)}) could not be settled either way by this run's "
            f"evidence: {_why_unsettled(scope, blind_paths)}. They are not verified, and "
            "they are not known to have failed."
        )
    summary_text = (
        f"{sum(1 for r in rows if r.status == 'passed')} of {len(rows)} criteria verified by "
        f"blind tests; {sum(1 for r in rows if r.status == 'no_test')} with no blind test; "
        + (f"{sum(1 for r in rows if r.status == 'manual')} left to a person to check by hand; "
           if any(r.status == "manual" for r in rows) else "")
        + f"{unverified} whose tests cannot be shown to have run"
        + (f", {len(unsettled)} of them unsettled by incomplete evidence" if unsettled else "")
        + f"; {sum(1 for g in gates.results if g.passed)} of {len(gates.results)} gates passed."
    )
    notes_parts = []
    if summary.known:
        notes_parts.append(
            f"Blind suite, run file by file: {summary.passed} of {summary.total} "
            f"file(s) passed, {summary.failed} failed."
        )
    elif test_gates:
        notes_parts.append(
            "The blind suite ran as one command rather than file by file, so no criterion "
            "is settled by its own test -- only by the suite's exit code."
        )
    if oracle and oracle.notes:
        notes_parts.append(f"Oracle notes on interface assumptions: {oracle.notes}")
    notes = " ".join(notes_parts)
    return QAReport(summary=summary_text, results=rows, failures=failures, notes=notes)


#: Outcomes that mean a finding is still in front of a human. A finding the
#: loop dismissed is not open; one it tried and failed to fix is.
OPEN_OUTCOMES = ("open", "escalated", "attempted_not_fixed",
                 "needs_spec_change", "unattempted")


def _open_blockers(findings: Sequence[Finding], records: Sequence[FindingRecord]) -> int:
    """Blockers nobody has dealt with, counted from what became of them.

    A finding with no record has not been through the loop, so it is open by
    default -- the same reading `FindingRecord` takes.
    """
    by_id = {r.finding_id: r for r in records}
    open_now = 0
    for finding in findings:
        if finding.severity != "blocker":
            continue
        record = by_id.get(finding.id)
        if record is not None and record.duplicate_of:
            continue          # the finding it restates is the one being counted
        if record is None or record.outcome in OPEN_OUTCOMES:
            open_now += 1
    return open_now


def hold_for_manual_checks(packet: Packet, spec: Spec, trace: Sequence[TraceRow],
                           levels: Mapping[str, str]) -> Packet:
    """List the criteria nobody tested, and keep the verdict from saying "ships".

    Computed after the rapporteur, because a verdict is the one thing a human
    acts on without reading further: a build whose untested criteria are all
    still to be checked is not ready to ship, whatever the tested part showed.
    At best it ships with rulings -- the person's own checks -- and a headline
    written for a plain "ship" is told what is left. A worse verdict stands.
    """
    manual = {r.criterion_id for r in trace if r.status == "manual"}
    if not manual:
        return packet
    packet.manual_checks = [
        ManualCheck(criterion_id=c.id, title=c.title, statement=c.statement,
                    # Written for a person. Never `verification`: that is the
                    # test author's register ("a Playwright test that seeds...").
                    how=c.check_by_hand, level=c.verified_at or "",
                    reason=levels.get(c.verified_at or "", ""))
        for c in spec.acceptance_criteria if c.id in manual]
    if packet.verdict == "ship":
        packet.verdict = "ship_with_rulings"
        n = len(packet.manual_checks)
        packet.headline = (packet.headline.rstrip()
                           + f" Check {n} criteri{'on' if n == 1 else 'a'} by hand before you ship.")
    return packet


def compute_stats(
    spec: Spec,
    trace: Sequence[TraceRow],
    qa: QAReport,
    gates: GateReport,
    findings: Sequence[Finding],
    files: Sequence[FileWrite],
    decisions: Sequence[Decision],
    records: Sequence[FindingRecord] = (),
    rework: ReworkSummary | None = None,
    breaker: BreakerReport | None = None,
    unclaimed: Sequence[UnclaimedFile] = (),
    diffstat: DiffStat | None = None,
) -> PacketStats:
    sizes = [change_size(f.path, f.contents, diffstat) for f in files]
    added = sum(a for a, _ in sizes)
    removed = sum(r for _, r in sizes)
    # Verified means all three: something implements it, a blind test is tagged
    # to it, and that test passed. Counting the pass alone, which is one of the
    # three, would count as verified a criterion nothing implements, or that no
    # blind test covers, on the strength of a QA row -- while the console
    # (which computes all three) shows "no code" on the very row the headline
    # has just counted. Two renderings of one record must not be able
    # to disagree about it, and the one that asks for less is the wrong one.
    traced = {r.criterion_id for r in trace if r.status == "traced"}
    return PacketStats(
        gates_passed=sum(1 for g in gates.results if g.passed),
        gates_total=len(gates.results),
        criteria_total=len(spec.acceptance_criteria),
        criteria_verified=sum(
            1 for r in qa.results if r.status == "passed" and r.criterion_id in traced),
        orphan_requirements=sum(1 for r in trace if r.status == "orphan_requirement"),
        untested_criteria=sum(1 for r in trace if r.status == "untested"),
        criteria_manual=sum(1 for r in trace if r.status == "manual"),
        unclaimed_files=len(unclaimed),
        # Still standing, not ever raised. The console renders this as
        # "blockers open" and the summary as "N blockers stand", so counting
        # every blocker-severity finding regardless of outcome would have a run
        # that found fifteen and repaired eight report fifteen standing. The
        # record keeps the raised figure beside it, because a packet that got
        # shorter as the loop worked would look better and be worth less
        # (INV-11).
        blockers=_open_blockers(findings, records),
        blockers_raised=sum(1 for f in findings if f.severity == "blocker"),
        files_written=len(files),
        # The diff, not the files. A run that adds one line to a 1400-line page
        # changed one line, and the number a human budgets their reading against
        # has to say so.
        lines_written=added + removed,
        lines_added=added,
        lines_removed=removed,
        decisions_total=len(decisions),
        findings_total=len(findings),
        # Distinct defects, and the repairs that resolved them. A panel sampled
        # several times reports one defect several ways, and counting those
        # separately makes "14 repaired" read as fourteen problems where the
        # loop made four changes. The raised totals stay beside them: the record
        # only grows, and corroboration is evidence rather than noise (INV-11).
        findings_distinct=sum(1 for r in records if not r.duplicate_of),
        findings_duplicate=sum(1 for r in records if r.duplicate_of),
        findings_repaired=sum(
            1 for r in records if r.outcome == "repaired" and not r.duplicate_of),
        # Everything still standing in front of the human. A finding the loop
        # dismissed is not open; a finding it tried and failed to fix is.
        findings_open=sum(1 for r in records if r.outcome in OPEN_OUTCOMES),
        breaker_failures=len(breaker.failing) if breaker else 0,
        breaker_suite_failed=bool(breaker and breaker.ran and breaker.exit_code != 0),
        agent_failures=sum(1 for f in findings if f.id.startswith("agent-")),
        rework_rounds=rework.rounds if rework else 0,
    )


def collect_disclosures(workers: Sequence[WorkerOutput]) -> list[str]:
    """Flatten worker disclosure into lines. An empty disclosure is itself a line,
    because silence is a signal worth surfacing rather than a clean run. (INV-9)"""
    lines: list[str] = []
    for w in workers:
        d = w.disclosure
        for label, items in (
            ("not implemented", d.not_implemented),
            ("assumption", d.assumptions),
            ("deviation from spec", d.deviations_from_spec),
            ("flag", d.flags),
        ):
            for item in items:
                lines.append(f"{w.unit_id} -- {label}: {item}")
        if not (d.not_implemented or d.assumptions or d.deviations_from_spec or d.flags):
            lines.append(
                f"{w.unit_id} -- disclosed nothing at all. An empty disclosure is a claim, "
                "not a clean run: nobody builds a unit from an incomplete spec without assuming something."
            )
    return lines
