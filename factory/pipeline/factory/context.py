"""Writing files to the branch, and the evidence blocks agents are shown.
"""

from __future__ import annotations

from typing import Sequence

from ...sandbox import Sandbox
from ...schemas import (
    BreakerReport,
    Decision,
    FileWrite,
    Finding,
    FindingRecord,
    GateReport,
    IntegrationReport,
    Plan,
    QAReport,
    ReviewReport,
    ReworkSummary,
    Spec,
    TraceRow,
    WorkerOutput,
)
from ...workspace import PathEscape, safe_join, spec_text

from ..text import _json, gate_status_line, files_section, tree_section
from ..trace import DiffStat, change_size
from ..protection import is_protected
from ..packet import what_landed


class ContextMixin:
    # ---- writing --------------------------------------------------------

    def _apply_writes(
        self, sandbox: Sandbox, files: Sequence[FileWrite], *,
        disclaimed: Sequence[str] = (), spec: Spec | None = None,
    ) -> tuple[list[str], list[str]]:
        """AC-8.11 -- every model-authored path is checked against escaping the sandbox.

        The project's own repository is never written to. What the human gets is
        a branch.
        """
        applied: list[str] = []
        rejected: list[str] = []
        if not self.config.pipeline.apply_writes:
            return applied, [f"{f.path}: writes disabled by configuration" for f in files]
        root = sandbox.path
        # Only what an agent said about its own output can stop a write here.
        # A path it disclaimed is one it has told us is a fragment, and writing
        # a known fragment over a real file is not a judgement call.
        #
        # Nothing else refuses here, a line-count guard least of all. That
        # would be a threshold standing in for a judgement, in a system whose
        # whole design says judgements are reached by a human reading an
        # argument -- and when it fired it would leave no trace anywhere a human
        # reads. `rejected` is written into the `writes` record and nothing
        # reads it back, so the packet would describe a build that was neither
        # what the agent produced nor what anybody approved, and say nothing
        # about the difference. A silent veto is the one thing this system must
        # not do.
        #
        # Destruction is detected instead, at `check_destructive_writes`, and
        # arrives as a finding the panel weighs and a human rules on. See there
        # for why line-count arithmetic is the wrong test.
        refused = {
            f.path: ("the agent that produced it said it could not return a complete file "
                     "for this path, so what it returned is a fragment, not the file")
            for f in files if f.path in {d.strip() for d in disclaimed if d and d.strip()}
        }
        for f in files:
            if f.path in refused:
                rejected.append(f"{f.path}: refused -- {refused[f.path]}")
                continue
            try:
                target = safe_join(root, f.path)
            except PathEscape as exc:
                rejected.append(f"{f.path}: {exc}")
                continue
            try:
                if f.deleted:
                    # `commit` stages with `add -A`, so the removal rides onto
                    # the branch like any other change.
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(f.contents, encoding="utf-8")
            except OSError as exc:
                rejected.append(f"{f.path}: {exc}")
                continue
            applied.append(f.path)
        return applied, rejected

    # ---- context assembly ----------------------------------------------

    def _evidence_block(
        self, spec: Spec, plan: Plan, workers: Sequence[WorkerOutput],
        integration: IntegrationReport, gates: GateReport, qa: QAReport,
        trace: Sequence[TraceRow], digest: str, written: Sequence[FileWrite],
        breaker: BreakerReport | None = None,
        records: Sequence[FindingRecord] = (),
        sandbox: Sandbox | None = None,
        blind: Sequence[str] = (),
        guidance: str = "",
    ) -> str:
        extra: list[str] = []
        if breaker is not None and (breaker.ran or breaker.note):
            # The adversary can cite a real failing test instead of
            # describing an experiment it could not run.
            extra.append(
                "---\n\n# Adversarial probes run against this implementation\n\n"
                + _json(breaker.model_dump(mode="json"), 12_000)
                + "\n\nA failing probe is a fact. A passing one proves nothing and is not "
                "verification of anything.")
        if records:
            extra.append(
                "---\n\n# What has already been found and what became of it\n\n"
                + _json([r.model_dump(mode="json") for r in records], 20_000)
                + "\n\nDo not re-raise a finding recorded as repaired unless you can show it "
                "is still there. Do re-raise one recorded as attempted and not fixed.")
        return "\n\n".join(extra + [
            spec_text(spec),
            # The rules this code is held to, by code: a reviewer who cites one
            # quotes it from here, and cannot hold the code to a taste instead.
            *(["---\n\n" + guidance] if guidance else []),
            "---\n\n# The plan\n\n" + _json(plan, 20_000),
            "---\n\n# What each worker reports\n\n" + "\n\n".join(
                f"## {w.unit_id}\n{w.summary}\n\n"
                + what_landed(w.files, sandbox.path if sandbox is not None else None,
                              lambda path: is_protected(path, list(blind), self.config))
                + f"### Decisions\n{_json(w.decisions, 12_000)}\n\n"
                f"### Self-disclosure\n{_json(w.disclosure)}"
                for w in workers
            ),
            "---\n\n# Integration\n\n" + _json(integration.model_dump(exclude={"files"}), 12_000),
            # Read off the branch, not out of the agents' answers. What a
            # worker reported writing was true when it wrote it and stops being
            # true the moment anything repairs the same file -- and on a resumed
            # or revalidated build, `written` is the *first* attempt's copy,
            # predating every repair a previous dispatch made. A panel handed
            # that judges code that has not existed for hours: three agents
            # reported a duplicate class at a line number where it had been
            # deleted two commits earlier, and the packet led with it.
            ("---\n\n# The files this feature wrote, as they are on the branch now\n\n"
             + tree_section(sandbox.path, [f.path for f in written], budget=90_000)
             if sandbox is not None else
             "---\n\n# Files written\n\n" + files_section(written)),
            # The blind tests are kept out of `written`, so without this the
            # panel's only copy of them would be the digest -- which, on a
            # resume, is the copy from before the oracle fixed them.
            *(["---\n\n# The blind tests and their helpers, as they are on the branch now\n\n"
               + tree_section(sandbox.path, list(blind), budget=40_000)]
              if sandbox is not None and blind else []),
            "---\n\n# Gates\n\n" + _json(gates, 12_000),
            "---\n\n# Verification against criteria (computed, not claimed)\n\n" + _json(qa, 12_000),
            "---\n\n# Traceability matrix (computed)\n\n"
            + _json([r.model_dump(mode="json") for r in trace], 12_000),
            "---\n\n# The repository as it was before this work\n\n" + digest[:60_000],
        ])

    def _rapporteur_block(
        self, spec: Spec, decisions: Sequence[Decision], findings: Sequence[Finding],
        disclosures: Sequence[str], trace: Sequence[TraceRow], gates: GateReport,
        qa: QAReport, written: Sequence[FileWrite], review: ReviewReport,
        integration: IntegrationReport,
        records: Sequence[FindingRecord] = (),
        rework: ReworkSummary | None = None,
        diffstat: DiffStat | None = None,
        guide_index: str = "",
    ) -> str:
        # The size of each change, not the size of each file. The rapporteur is
        # deciding how much reading a human owes this build; handing it an
        # 800-line seed module that grew by a six-line comment and calling that
        # "800 lines" invites exactly the wrong answer.
        def size(f: FileWrite) -> str:
            added, removed = change_size(f.path, f.contents, diffstat)
            return f"+{added} -{removed}"

        file_index = "\n".join(
            f"- {f.path} ({size(f)}){' -- ' + f.purpose if f.purpose else ''}"
            for f in written
        ) or "- (no files)"
        return "\n\n".join([
            spec_text(spec),
            # Which guides this feature was held to, by name, so the packet can
            # cite them. Their text was the writers' and reviewers' to read.
            *(["---\n\n" + guide_index] if guide_index else []),
            "---\n\n# Every decision collected (classify and rank; you cannot drop one)\n\n"
            + _json([d.model_dump(mode="json") for d in decisions], 60_000),
            "---\n\n# Every finding collected (order by severity; you cannot drop one)\n\n"
            + _json([f.model_dump(mode="json") for f in findings], 40_000),
            "---\n\n# The strongest objection raised by any review agent\n\n"
            + (review.strongest_objection or "(none offered)"),
            "---\n\n# Worker self-disclosures\n\n" + ("\n".join(f"- {d}" for d in disclosures) or "- (none)"),
            "---\n\n# Seams the integrator could not close\n\n"
            + ("\n".join(f"- {u}" for u in integration.unresolved) or "- (none)"),
            "---\n\n# Every file written (classify every one of these)\n\n" + file_index,
            # Computed, and first, because it is the one fact in this prompt
            # that a truncated blob below cannot be allowed to contradict.
            "---\n\n# Gates, as they stand now\n\n" + gate_status_line(gates)
            + "\n\nThese counts are computed from the final run and are not open to "
            "interpretation. State them as they are or do not state them at all: every "
            "number here is on the human's screen beside whatever you write, and a headline "
            "that disagrees with it is read as the packet being wrong about everything. "
            "Findings raised in earlier rounds describe earlier rounds -- a gate named in one "
            "of them is not evidence about the gate now.\n\n"
            + _json(gates, 10_000),
            "---\n\n# Verification against criteria\n\n" + _json(qa, 10_000)
            + ("\n\nCriteria with status `manual` were not tested by Fabrika: their test "
               "level can't run cleanly in this project, so a person checks them by hand at "
               "review. They are neither failures nor verified. While any are unchecked the "
               "verdict is at most `ship_with_rulings`, and a headline for a build that "
               "otherwise ships says the person has these to check."
               if any(r.status == "manual" for r in qa.results) else ""),
            "---\n\n# Traceability (computed; yours will be discarded)\n\n"
            + _json([r.model_dump(mode="json") for r in trace], 10_000),
            "---\n\n# What happened to each finding (computed; you cannot change it)\n\n"
            + _json([r.model_dump(mode="json") for r in records], 20_000)
            + "\n\nA repaired finding is still a finding: it stays in the packet with its "
            "outcome. Order by what the human must rule on -- attempted and not fixed, and "
            "anything dismissed as not-a-defect, are the rows most worth their attention. "
            "`no_longer_holds` means the factory's own check that raised it ran again on "
            "fresh evidence and no longer finds it: it is settled, and must not be presented "
            "as standing, least of all in the headline.",
            "---\n\n# What the repair loop did and what it cost\n\n"
            + _json((rework or ReworkSummary()).model_dump(mode="json"), 4_000),
        ])
