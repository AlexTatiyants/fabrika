"""Convergence: the review panel, recheck, arbiter, repair and simplify rounds,
and the loop that drives them.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Sequence

from ...llm import RateLimited
from ... import dependencies
from ...sandbox import Sandbox
from ...schemas import (
    ArbiterReport,
    BreakerReport,
    BreakerSuite,
    BreakerTest,
    FeatureState,
    FileWrite,
    Finding,
    FindingDisposition,
    GateReport,
    IntegrationReport,
    OracleSuite,
    Plan,
    QAReport,
    RecheckReport,
    RepairDone,
    RepairPlan,
    RepairUnit,
    ReviewReport,
    ReworkSummary,
    Spec,
    TraceRow,
    WorkerOutput,
    WorkUnit,
)
from ...store import EvidenceStore
from ...workspace import cleanup_context, manual_criteria, tested_spec, repo_digest, spec_text

from ..text import _json, gate_status_line, tree_section
from ..trace import compute_trace
from ..flags import human_flags, seed_flags
from ..records import resume_round
from ..oracle_suite import oracle_files, unruled
from ..protection import protected_paths, project_files_in, is_protected
from ..ledger import restore_dispositions, FindingLedger, suspect_tests_to_a_person, _agent_of
from ..probes import probe_finding_id, breaker_findings, probes_proven_passing
from ..repairs import plan_repairs, reopen_after_panel
from ..ownership import _blocked_reason, HarnessBlocked
from ..checks.gate_results import (
    check_test_execution,
    check_attribution_wiring,
    check_testing_conventions,
)
from ..checks.agents import (
    check_setup,
    check_route_fallbacks,
    check_environment_overrides,
    check_unit_delivery,
    check_independence,
    check_budget_coverage,
    check_harness_delivery,
    check_write_collisions,
)
from ..checks.repo import (
    check_verification_surface,
    check_settings_files,
    check_guides_changed,
    check_settings_changed,
    check_new_suppressions,
    check_hollow_tests,
    check_destructive_writes,
    check_declared_changes,
)
from ..checks.cut import check_unit_independence, check_cut
from ..checks.blind import (
    check_oracle_discards,
    check_blind_suite_missing,
    browser_ran,
    current_recordings,
    check_trace_coverage,
    check_recording_coverage,
    check_blind_tags,
    check_oracle_requirements,
)
from ..packet import merge_reviews, compute_qa, collect_disclosures
from ..helpers import _dedupe_writes, _round_unit_id


class ConvergeMixin:
    async def _review_panel(
        self, store: EvidenceStore, state: FeatureState, evidence_block: str, round_index: int,
        skip: Sequence[str] = (),
    ) -> list[tuple[str, ReviewReport]]:
        """Every review agent, concurrently, over one shared bundle.

        No per-agent branch: the reviewer and anything you add differ in their
        prompt, not in how they are called. (AC-8.6 / AC-8.7)

        `skip` names agents whose report a resumed build already holds, so an
        interrupted panel finishes rather than being paid for twice. What it
        must never do is shrink the panel: a resume that reused two agents of
        three and asked for none of the missing one would produce a packet
        claiming a review that did not happen.
        """
        reviews: list[tuple[str, ReviewReport]] = []
        roles = [r for r in self.config.review_roles() if r.name not in set(skip)]

        async def one_reviewer(role) -> None:
            system = self.role_prompt(role.name)
            samples = await asyncio.gather(*(
                self.llm.ask(role.name, evidence_block, ReviewReport,
                             system=system, temperature=role.temperature)
                for _ in range(max(1, role.samples))
            ), return_exceptions=True)
            good = [r for r in samples if isinstance(r, ReviewReport)]
            if not good:
                store.append("review_failed", {"role": role.name, "error": str(samples[0])},
                             role=role.name, spec_hash=state.spec_hash)
                return
            for i, sample in enumerate(good):
                store.append("review_sample", sample, role=role.name, model=self._answered_by(role.name),
                             spec_hash=state.spec_hash, meta={"sample": i, "round": round_index})
            merged = merge_reviews(good)
            merged.findings = self._verify_citations(store, state, merged.findings)
            store.append("review", merged, role=role.name, model=self._answered_by(role.name),
                         spec_hash=state.spec_hash,
                         meta={"samples": len(good), "round": round_index},
                         prompt=evidence_block)
            reviews.append((role.name, merged))

        async with self._phase(store, state, "review") as phase:
            if not roles:
                phase.detail = "no review agents configured"
                return reviews
            await asyncio.gather(*(one_reviewer(r) for r in roles))
            phase.detail = (
                f"{len(reviews)}/{len(roles)} agent(s), "
                f"{sum(len(r.findings) for _, r in reviews)} finding(s)"
                + (f" · round {round_index}" if round_index else "")
            )
        return reviews

    async def _recheck(
        self, store: EvidenceStore, state: FeatureState, sandbox: Sandbox,
        ledger: FindingLedger, attempted: dict[str, list[str]],
        repair_context: str, round_index: int, changed: Sequence[str] = (),
    ) -> list[tuple[str, RecheckReport]]:
        """The focused re-review: each agent looks only at the findings it raised.

        Cheaper than a full panel and better aimed. The full panel still runs
        when the loop exits, because a repair can break something nobody was
        looking at, and only an agent reading the whole change would see it.

        Aimed at the working tree, not at the round's diff. Given only what the
        repairers said they did, the question "is the defect still there" is
        answered from a narrative -- and a repairer that changed nothing reads
        as a defect unfixed even when an earlier round fixed it. That is not a
        hypothetical: three agents concluded across two rounds that a duplicate
        class was still crashing an import, having been deleted in round 1, and
        the packet led with it.
        """
        out: list[tuple[str, RecheckReport]] = []

        async def one(role_name: str, finding_ids: list[str]) -> None:
            role = self.config.roles.get(role_name)
            if role is None or not finding_ids:
                return
            asked = "\n\n".join(
                f"## {fid} ({ledger.findings[fid].severity})\n"
                f"{ledger.findings[fid].title}\n\n{ledger.findings[fid].detail}\n\n"
                f"Evidence you gave: {ledger.findings[fid].evidence}"
                for fid in finding_ids if fid in ledger.findings
            )
            # What the findings name, and then what this round touched: a
            # finding that named no file is common, and without the second list
            # such an agent would be back to judging from the narrative alone.
            named = [p for fid in finding_ids if fid in ledger.findings
                     for p in ledger.findings[fid].files] + list(changed)
            prompt = "\n\n".join([
                "# The findings raised by this agent\n\n" + asked,
                "---\n\n# The files those findings name, as they are on disk now\n\n"
                + tree_section(sandbox.path, named,
                               removed_prefixes=[self.config.rework.breaker_dir])
                + "\n\nThis is the checkout after every repair round so far, not this round's "
                "diff. Where it disagrees with anything below, it wins. A repairer reporting "
                "that its harness changed nothing is evidence about the harness, not about the "
                "defect; a round that touched no file cannot have un-fixed what an earlier "
                "round fixed. Settle each verdict here first, and cite what you read.",
                "---\n\n# Every file this feature has changed, all rounds\n\n"
                + ("\n".join(f"- {p}" for p in sandbox.changed_files()) or "- (none)"),
                "---\n\n" + repair_context,
            ])
            # The agent keeps its own angle -- the reviewer still hunts
            # duplication, the adversary still argues for rejection -- and the
            # re-check instructions are appended as a versioned file rather than
            # inlined here, for the same reason every other prompt is. (INV-10)
            system = self.role_prompt(role_name) + "\n\n---\n\n" + self.role_prompt("recheck")
            try:
                report: RecheckReport = await self.llm.ask(
                    role_name, prompt, RecheckReport, system=system,
                )
            except Exception as exc:
                store.append("recheck_failed", {"role": role_name, "error": str(exc),
                                                "round": round_index},
                             role=role_name, spec_hash=state.spec_hash)
                return
            store.append("recheck", report, role=role_name, model=self._answered_by(role_name),
                         spec_hash=state.spec_hash, meta={"round": round_index},
                         prompt=prompt)
            out.append((role_name, report))

        async with self._phase(store, state, "review") as phase:
            await asyncio.gather(*(one(r, ids) for r, ids in attempted.items() if ids))
            fixed = sum(1 for _, r in out for v in r.verdicts if v.status != "not_fixed")
            phase.detail = (
                f"re-check · round {round_index} · {fixed} of "
                f"{sum(len(v) for v in attempted.values())} finding(s) reported fixed"
            )
        return out

    async def _arbitrate(
        self, store: EvidenceStore, state: FeatureState, spec: Spec, ledger: FindingLedger,
        open_ids: list[str], gates: GateReport, qa: QAReport, round_index: int,
    ) -> list[FindingDisposition]:
        """Route every open finding. The arbiter proposes; this code disposes.

        It cannot delete a finding, soften it, or reword it, and anything it
        fails to route defaults to a human. (INV-11)
        """
        role = self.config.roles.get("arbiter")
        if role is None or not role.enabled or not open_ids:
            return restore_dispositions(None, open_ids)
        oracle_open = self._may_revise_oracle(store, state)
        # The agent, not just the role. Three entries reading `raised by:
        # reviewer` are what a panel sampled three times looks like from here,
        # and an arbiter read them as three reviewers agreeing -- "three
        # independent reviewers reproduce the identical symptom ... strong
        # corroboration of a real hang rather than a flaky report". There was
        # one reviewer, one model, and one probe run whose single line of
        # output all three passes had read. Naming the model is what makes the
        # three legible as one.
        listed = "\n\n".join(
            f"## {fid}\n"
            f"- severity: {ledger.findings[fid].severity}\n"
            f"- raised by: {_agent_of(ledger.records[fid])}\n"
            f"- attempts so far: {ledger.records[fid].attempts}\n"
            f"- title: {ledger.findings[fid].title}\n"
            f"- detail: {ledger.findings[fid].detail}\n"
            f"- evidence: {ledger.findings[fid].evidence}\n"
            f"- recommendation: {ledger.findings[fid].recommendation}"
            for fid in open_ids if fid in ledger.findings
        )
        prompt = "\n\n".join([
            spec_text(spec),
            # Computed and first, for the same reason the rapporteur gets it
            # that way: the findings below accumulate across rounds and every
            # one of them is written in the present tense. Read without this,
            # the panel restates round 0 as though it were now -- rounds 2 and 3
            # of one run both opened "U1 produced nothing, the migration was
            # never written", which round 1 had already fixed.
            "---\n\n# Gates, as they stand now\n\n" + gate_status_line(gates)
            + "\n\n" + _json(gates, 8_000),
            "---\n\n# Criterion outcomes\n\n" + _json(qa, 8_000),
            "---\n\n# The findings to route, by id\n\n" + listed
            + "\n\nThese were raised across every round so far, including rounds whose "
            "repairs have since landed. A finding describes the state of the work when it "
            "was written; the gates and criterion outcomes above describe the state now. "
            "Where they disagree, the computed facts win."
            + "\n\n**Entries that name the same agent are one agent, not several.** The "
            "review agents are sampled more than once on purpose, so one defect arrives "
            "worded three ways -- and three entries reading `reviewer (some-model)` are "
            "three passes of one reader, which is the sampling working rather than three "
            "readers agreeing. Say they are the same defect with `duplicate_of`, and do "
            "not count them. Agreement is worth something when it crosses agents: a probe "
            "that failed and a reader who found the same defect by reading reached it by "
            "two methods. Two entries from one agent reached it once.",
            f"---\n\nRound {round_index}.",
        ] + ([
            "A finding marked `raised by: human` was written by the person this factory "
            "works for, who has already decided it is a defect and must be fixed. That "
            "judgment is settled and is not yours. One question about it is open: who has "
            "to make the change. Route it to `oracle` if what must change is a blind test "
            "the oracle wrote -- a test that asserts the wrong thing, or asserts something "
            "the spec never promised -- because the oracle's files are the one tree a "
            "repairer may not write in. Route it to `repair` otherwise. Any other route on "
            "one of these is discarded and the finding stays a repair, so spend no "
            "reasoning on whether it is real.",
        ] if any(ledger.records[fid].role == "human"
                 for fid in open_ids if fid in ledger.records) else []
        ) + ([] if oracle_open else [
            "`oracle` is not available this run: it has used its fix sessions. Route a "
            "finding about how a blind test is written to `escalate` instead, so a person "
            "sees it rather than a route that cannot act.",
        ]))
        report: ArbiterReport | None = None
        async with self._phase(store, state, "arbiter") as phase:
            # Asked again when findings come back unruled, the same way the
            # oracle is asked again for a suite tagged to nothing. A reply that
            # validates is not the same as a reply, and the cost of accepting
            # one here is the whole repair loop.
            asked = prompt
            missing = list(open_ids)
            for attempt in range(max(1, self.config.pipeline.arbiter_retries + 1)):
                try:
                    answer = await self.llm.ask(
                        "arbiter", asked, ArbiterReport, system=self.role_prompt("arbiter"),
                    )
                    store.append("arbiter", answer, role="arbiter", model=self._answered_by("arbiter"),
                                 spec_hash=state.spec_hash,
                                 meta={"round": round_index, "attempt": attempt},
                                 prompt=asked)
                except Exception as exc:
                    # A failed arbiter must not silently end the loop or silently
                    # start one. Everything defaults to a human ruling.
                    store.append("arbiter_failed", {"round": round_index, "error": str(exc)},
                                 role="arbiter", spec_hash=state.spec_hash)
                    phase.detail = f"failed; {len(open_ids)} finding(s) defaulted to a human"
                    break
                short = unruled(answer, open_ids)
                # Kept if it ruled on more than the last one did. Twenty of
                # twenty-seven is twenty rulings that exist; throwing them away
                # to ask again would cost the work and gain nothing, since what
                # stays unruled defaults to a human either way.
                if report is None or len(short) < len(missing):
                    report, missing = answer, short
                if not missing:
                    break
                store.append("arbiter_retry", {
                    "round": round_index, "attempt": attempt,
                    "why": f"{len(short)} of {len(open_ids)} finding(s) came back unruled",
                    "unruled": short,
                    "summary": (answer.summary or "")[:400],
                }, role="orchestrator", spec_hash=state.spec_hash)
                phase.detail = f"asking again for {len(missing)} unruled finding(s)"
                # Named, because "answer about all of them" is not actionable
                # and a list of ids is. Appended rather than resent: the CLI
                # routes flatten the exchange anyway, so this reads as the next
                # thing said in the same conversation.
                asked = (
                    f"{asked}\n\n---\n\nYour last answer left "
                    f"{len(short)} of {len(open_ids)} findings without a disposition. "
                    "Every finding you were given needs one -- `escalate` is a ruling and "
                    "is the right one where the call is a human's, but silence is not.\n\n"
                    "Rule on these, by id:\n"
                    + "\n".join(f"- {fid}" for fid in short)
                )

            dispositions = restore_dispositions(report, open_ids)
            counts: dict[str, int] = {}
            for d in dispositions:
                counts[d.disposition] = counts.get(d.disposition, 0) + 1
            if report is not None and not missing:
                phase.detail = " · ".join(f"{v} {k}" for k, v in sorted(counts.items()))
            elif report is not None:
                # Said as what it is. "27 escalate" reads as a judgement, and a
                # default is the absence of one.
                ruled = len(open_ids) - len(missing)
                phase.detail = (
                    f"{ruled} of {len(open_ids)} finding(s) ruled on; the rest default "
                    "to a human ruling"
                )
            elif not phase.detail:
                phase.detail = (
                    f"the arbiter ruled on none of {len(open_ids)} finding(s); all default "
                    "to a human ruling"
                )
        # Held to it as well as told, twice over.
        #
        # A reviewer saying a blind test is wrong is a judgment about the
        # contract, and the prompt says it goes to a person. Told is not held:
        # routed to the oracle it would be a model deciding which assertion to
        # drop, and routed to repair it would set a repairer against a file it
        # may not touch. A person who sends it back is how it reaches the oracle.
        dispositions = suspect_tests_to_a_person(dispositions, ledger)
        # And a finding sent to a route that cannot act is left open and
        # reaches the packet as never attempted -- which reads as the loop's
        # neglect when it was the route's absence.
        if not oracle_open:
            dispositions = [
                d.model_copy(update={
                    "disposition": "escalate",
                    "reason": (d.reason + " [Sent to you: the oracle has used its fix "
                               "sessions this run.]").strip(),
                }) if d.disposition == "oracle" else d
                for d in dispositions]
        return dispositions

    async def _repair_round(
        self, store: EvidenceStore, state: FeatureState, spec: Spec, sandbox: Sandbox,
        ledger: FindingLedger, target_ids: list[str], protected: list[str], round_index: int,
        oracle_paths: Sequence[str] = (),
    ) -> tuple[list[WorkerOutput], list[FileWrite], list[str], str, RepairPlan | None, list[str]]:
        """Plan a round of repairs, then make them. Nothing here changes the spec.

        Gate 1 froze the contract, and a loop that could reopen it would produce
        a packet arguing against something no human approved. A finding that can
        only be resolved by changing the spec is not repairable by definition --
        it is an escalation, and the arbiter routes it as one.

        `oracle_paths` is the test tree this round's repairers may not write
        in. A target that lands there is not a repair that failed, it is a
        repair addressed to the wrong agent, and it leaves here routed to the
        one that owns those files rather than skipped again next round.
        """
        cfg = self.config.rework
        digest = repo_digest(sandbox.path, self.project.state.digest_budget)
        own = self._own_blind_dirs(state) + self._breaker_own_dirs(state)
        plan = plan_repairs(
            ledger, target_ids, cfg.max_repair_units_per_round,
            writable=lambda path: not is_protected(path, protected, self.config, own,
                                                   self._project_files.get(state.feature_id, ())))
        units = list(plan.units)
        store.append("repair_plan", plan, role="orchestrator",
                     spec_hash=state.spec_hash, meta={"round": round_index})
        # Findings no repairer may act on go to a person, with the reason,
        # rather than into a round whose every edit would be refused.
        unroutable = [fid for fid in target_ids
                      if any(d.startswith(fid + ":") for d in plan.deferred)]
        ledger.route_to_person(unroutable, (
            "Every file this finding names is a check, or a test, that the repair loop "
            "may not change -- so no repairer could have acted on it. Read it yourself: "
            "the error it shows says where the fault is."))
        attempted = [fid for unit in units for fid in unit.finding_ids]
        waiting = [fid for fid in target_ids
                   if fid not in set(attempted) and fid not in set(unroutable)]
        if waiting:
            # Not deferred and not refused -- there were more findings than
            # slots. It costs them nothing, because nobody tried.
            store.append("repair_waiting", {
                "round": round_index, "findings": waiting,
                "why": f"{len(units)} unit(s) is this round's cap; these keep their attempts",
            }, role="orchestrator", spec_hash=state.spec_hash)
        if not units:
            return [], [], [], "", plan, list(target_ids)
        ledger.attempted(attempted, round_index)

        semaphore = asyncio.Semaphore(max(1, self.config.pipeline.max_parallel_workers))
        repairer_system = (self.role_prompt("repairer")
                           + cleanup_context(self.project.state.testing))

        async def one(unit: RepairUnit) -> WorkerOutput | None:
            # The repair is handed to the same executor a build unit is, because
            # it is the same shape of job -- read a tree, change it, account for
            # what you did. What differs is the prompt, and the prompt is the
            # whole point: a repairer's instincts have to be the inverse of a
            # builder's.
            work = WorkUnit(
                id=unit.id,
                title=unit.title,
                objective=unit.objective,
                files_expected=list(unit.files_expected),
                **self._unit_guidance(store, state, "repairer", list(unit.files_expected)),
                notes="\n\n".join(filter(None, [
                    unit.constraints and f"Constraints: {unit.constraints}",
                    unit.notes,
                    "The findings this unit must resolve:\n" + "\n\n".join(
                        f"- {fid}: {ledger.findings[fid].title}\n  {ledger.findings[fid].detail}"
                        for fid in unit.finding_ids if fid in ledger.findings),
                    "Read-only paths, refused before they land: "
                    + (", ".join(protected[:60]) or "(none)"),
                    unit.unlocked and (
                        "UNLOCKED FOR THIS UNIT, and nothing else is: "
                        + ", ".join(unit.unlocked) + ".\n\n"
                        "A finding above says that test asserts something this design makes "
                        "impossible to observe, so you may change what it asserts -- to what "
                        "the finding says it should assert, in that file, and nowhere else. "
                        "Say in your summary what it meant before and what it means now. If "
                        "the finding does not tell you what it should assert instead, change "
                        "nothing there and say so: guessing at someone's acceptance test is "
                        "worse than leaving it."),
                ])),
            )
            # Both halves of the exchange, as for a build unit (INV-2). A repair
            # round that goes wrong is the one you most need to interrogate, and
            # without the brief a repairer was handed, "what was it looking at
            # when it changed nothing?" has nothing to answer it but the
            # repairer's own account.
            # Written in the `finally`, because the run that failed is the run
            # whose prompt is worth the most.
            seen: list[tuple[str, str]] = []
            async with semaphore:
                try:
                    return await self.executor.run(
                        work, spec, digest, repairer_system, sandbox.path, role="repairer",
                        on_prompt=lambda label, text: seen.append((label, text)),
                        environment=self.unit_env(
                            f"{state.feature_id}-r{round_index}-{unit.id}",
                            role="repairer"),
                        on_environment=self.env_recorder(
                            store, state, unit=unit.id, role="repairer",
                            round_index=round_index),
                        on_guides=self._guides_recorder(
                            store, "repairer", list(unit.files_expected)))
                except Exception as exc:
                    store.append("repair_failed", {
                        "round": round_index, "unit": unit.id, "error": str(exc),
                    }, role="repairer", spec_hash=state.spec_hash)
                    return None
                finally:
                    for label, text in seen:
                        store.append("repair_prompt", {"unit_id": unit.id, "which": label},
                                     role="repairer", spec_hash=state.spec_hash,
                                     meta={"round": round_index, "unit_id": unit.id,
                                           "which": label}, prompt=text)

        async with self._phase(store, state, "repairers") as phase:
            phase.detail = f"{len(units)} unit(s) · round {round_index}"
            results = list(await asyncio.gather(*(one(u) for u in units)))
            outputs = [r for r in results if r is not None]
            # A unit that raised, and a unit whose harness edited nothing, are
            # the same event to everything downstream: no work was done on the
            # findings it owned. The executor retries a silent harness once
            # before it says so, so reaching here means two runs produced no
            # edit. Recorded as a fact of its own, because a run has spent three
            # rounds on a harness that was not editing, and the only trace of it
            # was one repairer's honesty in its summary.
            silent = [u for u, r in zip(units, results) if r is None or not r.files]
            unrun = [fid for u in silent for fid in u.finding_ids if fid in ledger.findings]
            if silent:
                store.append("repair_noop", {
                    "round": round_index,
                    "units": [u.id for u in silent],
                    "of": len(units),
                    "findings": unrun,
                    "reason": ("the coding harness made no edits, on two attempts per unit; "
                               "no attempt is charged to these findings"),
                }, role="orchestrator", spec_hash=state.spec_hash)
            for output in outputs:
                store.append("repair", output, role="repairer",
                             model=self._answered_by("repairer"),
                             spec_hash=state.spec_hash,
                             meta={"round": round_index, "unit_id": output.unit_id})

            # INV-12, enforced at the door rather than after the fact. A write to
            # the verification surface is refused before it lands, and the
            # attempt itself becomes evidence: a repairer reaching for the test
            # that judges it is a finding about the repairer.
            allowed: list[FileWrite] = []
            refused: list[str] = []
            # Per unit, because the one exception is per finding. A unit whose
            # finding says a test asserts something impossible may rewrite
            # THAT test, and no other unit in the round may touch it.
            unlocked_by_unit = {u.id: set(u.unlocked) for u in units}
            for output in outputs:
                unlocked = unlocked_by_unit.get(output.unit_id, set())
                for f in _dedupe_writes(list(output.files)):
                    if any(f.path == w.path for w in allowed):
                        continue
                    # The same directories the plan was cut against, so what a
                    # unit was told it owns and what is refused cannot differ.
                    if f.path not in unlocked and is_protected(
                        f.path, protected, self.config, own,
                        self._project_files.get(state.feature_id, ()),
                    ):
                        refused.append(f.path)
                    else:
                        allowed.append(f)
            applied, rejected = self._apply_writes(sandbox, allowed, spec=spec)
            # What the deletion guard refused is not on the branch, so it is not
            # part of the change the packet describes.
            landed = [f for f in allowed if f.path in set(applied)]
            commit_sha = sandbox.commit(
                f"factory: repair round {round_index} for {spec.title}\n\nspec {state.spec_hash}")
            store.append("writes", {
                "applied": list(applied), "rejected": rejected,
                "protected_refused": refused, "stage": f"repair-{round_index}",
                "branch": sandbox.branch, "commit": commit_sha,
            }, role="orchestrator", spec_hash=state.spec_hash)
            phase.detail = (
                f"{len(applied)} file(s) changed · round {round_index}"
                + (f" · {len(refused)} refused as protected" if refused else "")
                + (f" · {len(silent)} unit(s) edited nothing" if silent else "")
            )
        return outputs, landed, refused, commit_sha, plan, unrun

    async def _simplify_round(
        self, store: EvidenceStore, state: FeatureState, spec: Spec, sandbox: Sandbox,
        ledger: FindingLedger, protected: list[str],
    ) -> tuple[WorkerOutput | None, list[FileWrite], list[str], str]:
        """The findings no repair can close, once, after the loop has converged.

        Code written twice, an abstraction with one user, a convention the
        repository does not follow: correct, and a permanent tax on whoever
        reads it next. A repairer is forbidden the only fix there is -- it may
        not remove a thing for being unnecessary -- so without this pass these
        go to a human as escalations, which spends the one resource the packet
        budgets in minutes.

        After the loop rather than inside it, for two reasons. The tests pass by
        then, so "behaviour did not change" has something to be measured
        against; and the repair rounds are done, so nothing it deletes is
        something a later round was about to edit.

        It runs as one unit over the whole list. Splitting it would mean two
        agents removing duplication from each other's files in parallel
        worktrees, which is how you get two halves of one helper.
        """
        target_ids = ledger.simplifiable()
        async with self._phase(store, state, "simplifier") as phase:
            role = self.config.roles.get("simplifier")
            if role is None or not role.enabled:
                phase.detail = "not configured"
                return None, [], [], ""
            if not target_ids:
                # The common answer, and a good one. Said out loud because a
                # phase that ran and found nothing and a phase nobody reached
                # are different facts about a run.
                phase.detail = "nothing routed to simplify"
                return None, [], [], ""

            findings = [ledger.findings[fid] for fid in target_ids if fid in ledger.findings]
            files = sorted({path for f in findings for path in f.files})
            if not files:
                # A write boundary is the only thing keeping this pass to the
                # code it was asked about. Without one there is nothing to
                # bound it to, and a simplifier turned loose on a whole tree is
                # the failure this role is most capable of.
                phase.detail = f"{len(findings)} finding(s) name no file"
                store.append("simplify_skipped", {
                    "findings": target_ids,
                    "reason": "no finding named a file, so there is no write boundary",
                }, role="orchestrator", spec_hash=state.spec_hash)
                return None, [], [], ""

            work = WorkUnit(
                id="simplify",
                title="simplify what the panel found harder to read than it needs to be",
                objective=(
                    "Close the findings below and nothing else. The behaviour of this code "
                    "must not change: the project's checks, the blind acceptance suite and "
                    "every adversarial probe are run again after you."),
                files_expected=list(files),
                **self._unit_guidance(store, state, "simplifier", list(files)),
                notes="\n\n".join([
                    "The findings this pass must close:\n" + "\n\n".join(
                        f"- {f.id} [{f.category}]: {f.title}\n  {f.detail}"
                        + (f"\n  Evidence: {f.evidence}" if f.evidence else "")
                        # The rule it breaks, so each change closes a named
                        # rule rather than a taste.
                        + (f"\n  The rule: {f.cites.ref} -- \"{f.cites.quote}\""
                           if f.cites and f.cites.source == "guide" and f.cite_verified else "")
                        for f in findings),
                    "Read-only paths, refused before they land: "
                    + (", ".join(protected[:60]) or "(none)"),
                ]),
            )
            phase.detail = f"{len(findings)} finding(s) · {len(files)} file(s)"
            seen: list[tuple[str, str]] = []
            try:
                output = await self.executor.run(
                    work, spec, "", self.role_prompt("simplifier"), sandbox.path,
                    role="simplifier",
                    on_prompt=lambda label, text: seen.append((label, text)),
                    environment=self.unit_env(f"{state.feature_id}-simplify", role="simplifier"),
                    on_environment=self.env_recorder(
                        store, state, unit=work.id, role="simplifier"),
                    on_guides=self._guides_recorder(store, "simplifier", list(work.files_expected)))
            except Exception as exc:
                # Never fatal. Everything this pass could have improved is
                # already built, reviewed and green; taking the run down here
                # would trade a packet for nothing.
                store.append("simplify_failed", {
                    "error": str(exc), "findings": target_ids,
                }, role="simplifier", spec_hash=state.spec_hash)
                phase.detail = "did not run"
                return None, [], [], ""
            finally:
                for label, text in seen:
                    store.append("simplify_prompt", {"unit_id": work.id, "which": label},
                                 role="simplifier", spec_hash=state.spec_hash,
                                 meta={"unit_id": work.id, "which": label}, prompt=text)

            # INV-12, at the door, exactly as for a repair. An agent that may
            # delete things is the last one that should be able to delete the
            # measurement.
            allowed: list[FileWrite] = []
            refused: list[str] = []
            for f in _dedupe_writes(list(output.files)):
                if is_protected(
                    f.path, protected, self.config,
                    self._own_blind_dirs(state) + self._breaker_own_dirs(state),
                    self._project_files.get(state.feature_id, ()),
                ):
                    refused.append(f.path)
                else:
                    allowed.append(f)
            applied, rejected = self._apply_writes(sandbox, allowed, spec=spec)
            landed = [f for f in allowed if f.path in set(applied)]
            commit_sha = sandbox.commit(
                f"factory: simplify for {spec.title}\n\nspec {state.spec_hash}") if landed else ""
            store.append("writes", {
                "applied": list(applied), "rejected": rejected,
                "protected_refused": refused, "stage": "simplify",
                "branch": sandbox.branch, "commit": commit_sha,
            }, role="orchestrator", spec_hash=state.spec_hash)
            phase.detail = (
                f"{len(applied)} file(s) changed"
                + (f" · {len(refused)} refused as protected" if refused else "")
                if landed else "changed nothing")
            return output, landed, refused, commit_sha

    async def _converge(
        self, store: EvidenceStore, state: FeatureState, spec: Spec, plan: Plan,
        sandbox: Sandbox, digest: str, workers: list[WorkerOutput],
        integration: IntegrationReport, oracle: OracleSuite, written: list[FileWrite],
        done: dict[str, Any],
    ) -> dict[str, Any]:
        """Assess, route, repair, re-check -- until a rule written by a human says stop.

        This is the one loop in the pipeline whose trip count depends on what the
        models found, and INV-6 survives it for a precise reason: the models
        produce findings and routings, and plain code decides what that means.
        Every stopping condition below is a number in `factory.yaml`. None of
        them is a model's opinion, and none of them can be argued with.
        """
        cfg = self.config.rework
        started = time.monotonic()
        # What the project already depended on, looked up again: an advisory
        # published since the last reading is the project's news, shown on its
        # page, and never blamed on this feature.
        await dependencies.refresh_inventory(self.project, self.dependency_lookup)
        spend_at_start = float(self.llm.usage_report().get("total_cost") or 0.0)
        # What every earlier dispatch of this loop already cost this feature.
        # Read from the ledger rather than held in memory, so it survives a
        # restart and cannot be reset by starting a new process.
        prior_spend = sum(
            float((r.get("payload") or {}).get("usd") or 0.0)
            for r in store.records() if r.get("kind") == "rework_spend"
        )

        def this_dispatch() -> float:
            """Spent since this invocation's first gate run -- the loop, plus the
            review panels it causes to be repeated. Not the build lane: that was
            bought before there was anything to decide."""
            return max(
                0.0,
                float(self.llm.usage_report().get("total_cost") or 0.0) - spend_at_start)

        def spent() -> float:
            """What this feature has spent on rework, over its whole life.

            The budget is a lifetime figure per feature, not a fresh allowance
            per invocation. A human who sends the same packet back four times
            must not thereby buy four budgets -- that is the difference between
            a ceiling and a speed limit.
            """
            return prior_spend + this_dispatch()

        def affordable() -> bool:
            # The reserve is never spendable by the loop. Without it the failure
            # mode is a run that spends everything repairing and ends with no
            # packet at all, which is strictly worse than not looping.
            return spent() < max(0.0, cfg.budget_usd - cfg.reserve_usd)

        def out_of_time() -> bool:
            return (time.monotonic() - started) > cfg.wall_clock_minutes * 60

        # The criteria a person checks by hand, and the spec the verify lane was
        # held to without them. Read once: a re-survey landing mid-run must not
        # change which criteria one packet says were tested.
        manual_ids = {c.id for c in manual_criteria(spec, self.project.state)}
        checked = tested_spec(spec, self.project.state)
        ledger = FindingLedger()
        round_index = 0
        # A plan's window, hit mid-round. Held here so the loop can stop the way
        # it stops for a budget or a clock -- with a reason and a packet --
        # rather than raising through every finished round's work.
        limited: RateLimited | None = None
        if "rework_state" in done:
            ledger = FindingLedger.load(done["rework_state"])
            round_index = resume_round(store.records(),
                                       int(done["rework_state"].get("round") or 0))
        state.rework_round, state.rework_closing = round_index, False
        self._save(store, state)

        # Whatever the human flagged at gate 2 enters the ledger already routed,
        # before any agent has an opinion about it. (INV-11 covers these exactly
        # as it covers a reviewer's: they reach every later packet with their
        # original wording and their outcome beside it.)
        seeded = seed_flags(ledger, human_flags(store.records()), round_index)
        if seeded:
            store.append("flags_seeded", {"ids": seeded, "round": round_index},
                         role="orchestrator", spec_hash=state.spec_hash)

        all_workers = list(workers)
        all_written = list(written)
        breaker_tests: list[BreakerTest] = []
        reverted: list[int] = []
        protected_refused: list[str] = []
        repair_summaries: list[str] = []
        # What each round did, kept as it happens. The plan that assigned the
        # findings and the output that reports the change are both in scope
        # only here, inside the round; reconstructing the pairing afterwards
        # would mean guessing it from the files, which is how an account of the
        # work becomes an inference about it.
        rounds_done: list[RepairDone] = []
        stop_reason = ""
        # The simplification pass runs once. This block is reached again on a
        # forced final pass, and a second run would be a second unreviewed edit
        # for findings that are already closed.
        simplified = False
        previous_gate_pass: int | None = None
        # Probes known to have passed in the previous round. A regression is a
        # member of this set that now fails; anything else is a new finding.
        previous_breaker_passing: set[str] = set()
        pre_round_shas: dict[int, str] = {}
        reviews: list[tuple[str, ReviewReport]] = []
        gates = GateReport()
        qa = QAReport(summary="")
        trace: list[TraceRow] = []
        breaker_report = BreakerReport()

        # The blind tests are the verification surface, and so is anything that
        # decides whether they run. Computed from disk plus what this run wrote,
        # never from a model's account of what it wrote. (INV-12)
        own = self._own_blind_dirs(state) + self._breaker_own_dirs(state)
        self._project_files[state.feature_id] = project_files_in(
            sandbox.path, sandbox.state.base_sha or "", own, [f.path for f in written])
        protected = protected_paths(
            self.config, sandbox.path, extra=[t.path for t in oracle_files(oracle)],
            own_dirs=own, project_files=self._project_files[state.feature_id])
        # What decides what a check reports is as much the measurement as the
        # tests are. Kept apart from `protected` for one reader: the finding
        # about the build touching test collection is not about these, which
        # have their own, and one change reported twice reads as two.
        surface = list(protected)
        protected = sorted(set(protected) | set(check_settings_files(self.project.state.gates))
                           | {g.path for g in self._guides_at(self._base_of(state))
                              if g.layer != "linked"})

        while True:
            focus = (
                "This is the first pass. Attack the implementation as you find it."
                if round_index == 0 else
                "# What changed since your last pass\n\n" + "\n\n".join(repair_summaries[-3:])
                + "\n\nProbe the repairs specifically, and keep every earlier probe."
            )
            gates, breaker_tests, breaker_report = await self._assess(
                store, state, spec, sandbox, breaker_tests, round_index, focus,
                witness=[t.path for t in oracle.tests],
                suite=[f.path for f in oracle_files(oracle)],
                oracle=oracle,
                blind_command=oracle.command, seams=integration.seam_files)

            # Before qa, before the panel, before a repair round is opened.
            #
            # A blocked harness would otherwise enter the loop looking exactly
            # like a broken implementation: six red gates, no criterion
            # verified, and findings for all of it. One run spent five rounds
            # and most of an hour that way, repairing code against checks that
            # had never executed -- rounds whose diffs were comments and
            # docstrings, because there was nothing real for them to fix. The
            # packet at the end was honest about it ("Nothing ran"), which is
            # precisely the sentence that should stop the line an hour earlier.
            if gates.blocked:
                raise HarnessBlocked(
                    f"no check ran in round {round_index}, so nothing was measured. "
                    f"{_blocked_reason(gates)} The branch is untouched; fix the "
                    "environment and resume."
                )

            trace = compute_trace(spec, all_workers, oracle, integration.decisions,
                                  plan=plan, written=written, manual=manual_ids)
            async with self._phase(store, state, "qa"):
                qa = compute_qa(spec, oracle, gates, trace)
                store.append("qa", qa, role="orchestrator", spec_hash=state.spec_hash,
                             meta={"round": round_index})
            store.append("trace", [row.model_dump(mode="json") for row in trace],
                         role="orchestrator", spec_hash=state.spec_hash,
                         meta={"round": round_index})

            gate_pass = sum(1 for g in gates.results if g.passed)

            # A probe is a regression only if it PASSED before and fails now.
            #
            # Not `set(failing) - previously_failing`, which is a different
            # question and gives the wrong answer to this one. A probe written
            # *this* round has no earlier result to have passed in, so every
            # newly-authored failing probe would read as something the repair
            # broke. The breaker is told each round to "probe the repairs
            # specifically", and a probe exists to fail -- so any round in which
            # it found anything at all would be guaranteed to be reverted.
            #
            # That is not a hypothetical. A real run repaired three defects, had
            # all of them confirmed fixed by four independent re-checks, and was
            # reverted wholesale because the next breaker pass wrote two new
            # probes: one a genuine new problem, the other a *stricter* demand on
            # code that had just improved. The run ended with 0 repaired and 16
            # findings standing, four rounds and most of the budget unspent.
            #
            # Passing is read from the suite that actually ran. A round whose
            # breaker could not run knows of nothing passing, and therefore
            # cannot claim anything regressed.
            failing_now = set(breaker_report.failing)
            passing_now = probes_proven_passing(breaker_tests, breaker_report)
            regressed = previous_breaker_passing & failing_now

            # No-regression, decided by code. A round that made the gates worse,
            # or broke a probe that used to pass, is undone rather than argued
            # about -- and the attempt stays in the ledger, because the evidence
            # store is append-only and a reverted commit is still something that
            # happened.
            # Only against a round this process performed: a resumed build has no
            # baseline of its own, and judging it against an empty one would
            # revert work it never did.
            if round_index in pre_round_shas and (
                (previous_gate_pass is not None and gate_pass < previous_gate_pass)
                or regressed
            ):
                reverted.append(round_index)
                base = pre_round_shas.get(round_index, "")
                reason = (
                    f"gates fell from {previous_gate_pass} to {gate_pass}"
                    if previous_gate_pass is not None and gate_pass < previous_gate_pass
                    else f"broke probes that used to pass: {', '.join(sorted(regressed))}"
                )
                store.append("rework_reverted", {
                    "round": round_index, "reason": reason, "reset_to": base,
                }, role="orchestrator", spec_hash=state.spec_hash)
                if base:
                    sandbox.reset_to(base)
                ledger.revert_round(round_index, reason)
                # A reverted round is a round that did not work, not a reason to
                # stop trying. Ending the loop outright here has left one run
                # finished at round 1, with four of five rounds and most of the
                # budget unspent, reporting 0 repaired and 16 findings
                # standing.
                #
                # The findings go back to open with their attempt already
                # counted, so `max_attempts_per_finding` bounds the retrying and
                # a repair that keeps breaking the same probe retires as
                # attempted-and-not-fixed rather than looping. Reverting every
                # round available is a worse outcome than reverting one, and it
                # is a *visible* one: every revert is in the ledger and counted
                # in `reverted_rounds`.
                #
                # Stopping only when there is nothing left to try with.
                if len(reverted) >= cfg.max_rounds:
                    stop_reason = (
                        f"every repair round was reverted ({len(reverted)} of "
                        f"{cfg.max_rounds}): {reason}")
                # Re-assess so the packet describes the tree the human will read,
                # not the one that was thrown away.
                gates, breaker_tests, breaker_report = await self._assess(
                    store, state, spec, sandbox, breaker_tests, round_index, focus,
                    witness=[t.path for t in oracle.tests],
                    suite=[f.path for f in oracle_files(oracle)],
                    oracle=oracle,
                blind_command=oracle.command, seams=integration.seam_files)
                trace = compute_trace(spec, all_workers, oracle, integration.decisions,
                                      plan=plan, written=written, manual=manual_ids)
                qa = compute_qa(spec, oracle, gates, trace)
                # Recomputed from the tree that now exists. `gate_pass` above was
                # measured on the work just thrown away, and carrying it forward
                # sets the next round's bar at the broken count -- so a repair
                # that breaks exactly as much passes the comparison and survives.
                # The round after a revert is where the damage lands.
                gate_pass = sum(1 for g in gates.results if g.passed)
                passing_now = probes_proven_passing(breaker_tests, breaker_report)

            previous_gate_pass = gate_pass
            # Recomputed from the re-assessment above when a round was reverted,
            # so the next round is compared against the tree that actually
            # exists rather than against the one that was thrown away.
            previous_breaker_passing = passing_now

            # Facts about the build lane, computed once. They describe what was
            # delivered against the plan, and a repair round does not change the
            # plan it was measured against.
            if round_index == 0:
                ledger.add("computed", check_write_collisions(workers, integration), 0, keep_ids=True)
                ledger.add("computed", check_unit_delivery(plan, workers), 0, keep_ids=True)
                ledger.add("computed", check_unit_independence(
                    plan, spec, self.project.state.runtime_packages), 0, keep_ids=True)
                ledger.add("computed", check_cut(store, state.spec_hash), 0, keep_ids=True)
                ledger.add("computed", check_testing_conventions(
                    self.project.state.testing), 0, keep_ids=True)
                # Read at the top of the run and reported with the packet, for
                # the same reason `check_headroom` is: what the project was
                # able to say, and what the run then said, are two halves a
                # reader needs together.
                ledger.add("computed", check_attribution_wiring(
                    self.project.state), 0, keep_ids=True)
                ledger.add("computed", check_harness_delivery(workers), 0, keep_ids=True)
                ledger.add("computed", check_hollow_tests(
                    workers, [r.match for r in self.project.state.test_file_commands]),
                    0, keep_ids=True)
                ledger.add("computed", check_blind_tags(oracle, checked), 0, keep_ids=True)
                ledger.add("computed", check_oracle_discards(
                    store, state.spec_hash, sandbox.path), 0, keep_ids=True)
                # No criteria left to test is not a missing suite: every one
                # is a person's to check (see `workspace.tested_spec`).
                ledger.add("computed", check_blind_suite_missing(oracle, checked)
                           if checked.acceptance_criteria else [], 0,
                           keep_ids=True)
                ledger.add("computed", check_oracle_requirements(oracle, checked), 0,
                           keep_ids=True)

            # Restated every round, because every one of these is a question
            # about the tree and the run as they stand now -- and the answer
            # moves. Added rather than restated, they have reached a human as
            # several findings saying different numbers about one fact, with
            # nothing to say which was current: "the budget did not constrain 4
            # agent(s)" and "...7 agent(s)", both escalated, from one run. And
            # a repair that fixes what one of them reported cannot clear it,
            # because the text is already in the packet -- a blind suite has
            # gone from five unloadable files to two and told the reader five.
            # A check reading the gates has looked again only if the gates ran.
            gated = bool(gates.results)
            ledger.restate("computed", check_declared_changes(spec, all_written), round_index,
                           source="declared_changes")
            ledger.note("computed", check_environment_overrides(
                self.project.state, all_written), round_index)
            ledger.note("computed", check_route_fallbacks(
                getattr(self.llm, "fallbacks", [])), round_index)
            ledger.restate("computed", check_destructive_writes(sandbox, self.config),
                           round_index, source="destructive_writes")
            self._restate_from_checks(ledger, gates, integration, oracle, round_index,
                                      fresh=gated,
                                      repairable=self._repairable(all_written, protected, state))
            ledger.note("computed", check_verification_surface(
                workers, integration, surface), round_index)
            ledger.restate("computed", check_settings_changed(
                sandbox, self.project.state.gates), round_index, source="check_settings")
            ledger.restate("computed", check_guides_changed(sandbox), round_index,
                           source="guides_changed")
            ledger.restate("computed", check_new_suppressions(
                sandbox, self.project.state.gates), round_index, source="suppressions")
            self._restate_patch_quality(ledger, sandbox, gates, round_index)
            await self._restate_dependencies(
                store, ledger, sandbox, round_index,
                {f.path: w.unit_id for w in workers for f in w.files})
            ledger.note("computed", check_budget_coverage(
                self.llm.coverage(), self.config), round_index)
            ledger.note("computed", check_independence(self.config), round_index)
            ledger.restate("computed", check_setup(store, state.spec_hash), round_index,
                           source="setup")
            ledger.restate("computed",
                           check_recording_coverage(oracle, current_recordings(store.records()) or []),
                           round_index, source="recording_coverage")
            recorded = check_trace_coverage(
                self.project.state.trace_dirs, current_recordings(store.records()) or [],
                browser_ran(oracle, spec, gates))
            ledger.restate("computed", recorded, round_index, source="trace_coverage")
            # Facts about this run, recomputed every round.
            ledger.add("execution", check_test_execution(gates, trace), round_index, keep_ids=True)
            # The probe settles its own finding.
            #
            # Not by `add`, which only ever raises. Through `add` alone, a
            # breaker finding's outcome would come from `apply_verdicts` -- a
            # model reading the diff and saying "fixed" -- while the probe that
            # defines the finding sits in `failing` in the same round, unread.
            # One packet asserted both that its concurrency defects were
            # repaired in round 1 and that concurrency was an open blocker,
            # from one probe run.
            #
            # `restate` is the machinery eleven computed checks already use: a
            # probe that stops failing closes its own finding, and one that
            # comes back reopens it. `reopen_settled` extends that to a finding
            # somebody had already called repaired, which is the only case
            # where this record corrects itself rather than only growing -- a
            # check can be wrong about a defect by being run, and a report that
            # it was fixed cannot, so the check is what stands.
            #
            # `among` is why retirement above is safe. A demonstration is not
            # re-run after its round, so its finding is missing from this list
            # because nothing asked it, not because the answer changed. Without
            # the scope it would be closed with "the check ran again and no
            # longer finds it" -- a sentence about a check that did not run.
            # Only probes that ran this round can be settled by this round.
            probe_suite = BreakerSuite(strategy="", tests=breaker_tests)
            ran_now = ({t.path for t in breaker_tests}
                       - set(breaker_report.retired) - set(breaker_report.quarantined))
            settles = {
                fid for fid in (
                    ledger.id_of("breaker", t.hypothesis) or probe_finding_id(t.path)
                    for t in breaker_tests if t.path in ran_now)
                if fid
            }
            # The two findings about the run rather than about a probe are
            # recomputed from the report every round by definition, so they are
            # exactly what `fresh` is for: a breaker that started closes
            # `breaker-unstarted`, and a suite that can now blame an individual
            # probe closes `breaker-suite`.
            settles |= {"breaker-unstarted", "breaker-suite"}
            ledger.restate("breaker", breaker_findings(probe_suite, breaker_report),
                           round_index, source="breaker", fresh=breaker_report.ran,
                           among=settles, reopen_settled=True)

            forced_final = (
                not cfg.enabled
                or round_index >= cfg.max_rounds
                or not affordable()
                or out_of_time()
                or limited is not None
                or bool(stop_reason)
            )
            evidence_block = self._evidence_block(
                spec, plan, all_workers, integration, gates, qa, trace, digest, all_written,
                breaker=breaker_report, records=ledger.all_records() if round_index else [],
                sandbox=sandbox, blind=[f.path for f in oracle_files(oracle)],
                guidance=self._guidance(store, state, "reviewer", [f.path for f in all_written]),
            )
            # The full panel runs on the first pass and on the way out. In
            # between, the re-check does the looking, and it is aimed.
            #
            # With one exception, and it is the whole of why a review comes
            # back reading "we ignored what you asked for and found twenty
            # more things". The exit panel exists to catch damage a repair
            # did to something nobody was watching, which is worth four
            # agents -- but only if somebody can act on what it finds.
            # Stopping at a ceiling with work already queued and unattempted,
            # it can do exactly one thing: make that queue longer. On one
            # feature it made it longer three times, seventeen
            # findings deep, each one filed "routed for repair and never
            # attempted: the loop stopped first" -- and the next dispatch
            # opened by paying for the same sweep over the same code.
            #
            # So it runs on the way out only when the way out is clear. The
            # next dispatch's round 0 is the same sweep with road ahead of
            # it, and the aimed re-check has been running all along.
            backlog = ledger.repairable(cfg.max_attempts_per_finding)
            panel_now = round_index == 0 or (forced_final and not backlog)
            if forced_final and backlog and cfg.enabled:
                store.append("panel_skipped", {
                    "round": round_index, "queued": len(backlog),
                    "why": "stopping with work already routed and unattempted; a panel here "
                           "can only lengthen that queue",
                }, role="orchestrator", spec_hash=state.spec_hash)
            if round_index == 0 and "reviews" in done:
                # Whatever the interrupted attempt recorded, plus whatever it
                # never got to. A panel is a quorum of angles, not a list of
                # whatever happened to finish: reusing two of three agents and
                # asking for none of the third would hand the packet a review
                # one agent short and say nothing about it. A server killed
                # mid-panel is exactly how that happens.
                reviews = [(role, ReviewReport.model_validate(r)) for role, r in done["reviews"]]
                missing = [r.name for r in self.config.review_roles()
                           if r.name not in {name for name, _ in reviews}]
                if missing:
                    store.append("review_resumed", {
                        "reused": [name for name, _ in reviews], "missing": missing,
                        "round": round_index,
                    }, role="orchestrator", spec_hash=state.spec_hash)
                    reviews += await self._review_panel(
                        store, state, evidence_block, round_index,
                        skip=[name for name, _ in reviews])
            elif panel_now:
                reviews = await self._review_panel(store, state, evidence_block, round_index)
            if panel_now:
                for role_name, report in reviews:
                    ledger.add(role_name, report.findings, round_index,
                               model=self._answered_by(role_name))

            open_ids = [
                fid for fid, r in ledger.records.items()
                if r.outcome == "open" and r.attempts < cfg.max_attempts_per_finding
                # A person's flag is routed already, and asking a model to
                # second-guess its user is not worth the tokens. It goes in
                # for one question only -- who has to make the change -- and
                # only while it is a repair; `dispose` accepts nothing else
                # back. A dismissal is not sent at all: there is no agent
                # whose ownership of a file could matter to it.
                and (r.role != "human" or r.disposition == "repair")
                # Already known to restate another finding. Re-routing it asks
                # the arbiter to rule twice on one defect.
                and not r.duplicate_of
            ]
            dispositions = await self._arbitrate(
                store, state, spec, ledger, open_ids, gates, qa, round_index)
            ledger.dispose(dispositions)

            # A person waiting on a flag is not one voice among the panel's.
            # With one open, the round is handed those and what shares a file
            # with them, and nothing else. (See `human_first`.)
            # A finding about how a blind test is written rides the same
            # round as the rest. Handed to a second agent with its own
            # allowance and its own session, "this test asserts something
            # impossible" can arrive at an agent that cannot write the file:
            # when the test is a seam check, an agent that owns only the blind
            # tree cannot touch it. One queue, one round, and the
            # file the fix needs is unlocked for the unit that has it
            # (`plan_repairs`) rather than handed to whoever owns the tree.
            targets = ledger.human_first(
                ledger.repairable(cfg.max_attempts_per_finding)
                + ledger.for_oracle(cfg.max_attempts_per_finding))
            if forced_final or not targets:
                state.rework_closing = True
                self._save(store, state)
                if not stop_reason:
                    if limited is not None:
                        # Named as itself, and first. A run on a plan has no
                        # dollar ceiling to hit, so this is the ceiling it
                        # actually has -- and reported as "the harness failed"
                        # it sends a human to the config, the credentials and
                        # the code, none of which is wrong.
                        wait = (f", which said to wait {limited.retry_after_s / 60:.0f} min"
                                if limited.retry_after_s else "")
                        stop_reason = (
                            f"route {limited.route!r} reached its plan's limit{wait}; "
                            f"{len(targets)} finding(s) left unattempted. Nothing is "
                            "misconfigured and no budget was exceeded -- this run's "
                            "ceiling is the plan's window, which `budget_usd` cannot see. "
                            "Dispatch again when it resets.")
                    elif not cfg.enabled:
                        stop_reason = "rework is disabled in configuration"
                    elif not targets:
                        stop_reason = (
                            "converged: nothing left that a repair could fix"
                            if round_index else "nothing routed for repair")
                    elif round_index >= cfg.max_rounds:
                        stop_reason = (
                            f"round cap reached ({cfg.max_rounds}); "
                            f"{len(targets)} finding(s) left unattempted")
                    elif not affordable():
                        # The figures live on the summary, which is the one place
                        # they are computed. A reason that restates them reads as
                        # two different numbers the moment one of them changes.
                        stop_reason = (
                            f"the budget was spent, with ${cfg.reserve_usd:.2f} held back to pay "
                            f"for the packet; {len(targets)} finding(s) left unattempted")
                    else:
                        stop_reason = (
                            f"wall clock reached ({cfg.wall_clock_minutes:.0f} min); "
                            f"{len(targets)} finding(s) left unattempted")
                # The simplification pass, before that panel rather than after
                # it. Its diff is the last thing to touch this tree, so it is
                # the change most in need of a reader -- and the panel below is
                # already the mechanism for "a late edit broke something nobody
                # was looking at". Running it afterwards would ship the one
                # change in this run that nothing reviewed.
                if not simplified:
                    simplified = True
                    simp_out, simp_files, _simp_refused, _simp_sha = \
                        await self._simplify_round(
                            store, state, spec, sandbox, ledger, protected)
                    if simp_files:
                        # The gates again, on the tree that changed. Without
                        # this the packet reports checks that ran against code
                        # the branch no longer holds, and "behaviour did not
                        # change" becomes the one claim here nothing measured.
                        gates, breaker_tests, breaker_report = await self._assess(
                            store, state, spec, sandbox, breaker_tests, round_index, focus,
                            witness=[t.path for t in oracle.tests],
                            suite=[f.path for f in oracle_files(oracle)],
                            oracle=oracle,
                            blind_command=oracle.command, seams=integration.seam_files)
                        all_written = _dedupe_writes(list(all_written) + list(simp_files))
                        if simp_out is not None:
                            all_workers = list(all_workers) + [simp_out]
                        trace = compute_trace(spec, all_workers, oracle,
                                              integration.decisions,
                                              plan=plan, written=all_written, manual=manual_ids)
                        qa = compute_qa(spec, oracle, gates, trace)

                # The final pass: every check, the heavy ones included, on the tree
                # going to the human, before the last panel reads it. Only when
                # there is a heavy check -- otherwise every check already ran on
                # this tree this round, and running them again buys nothing.
                kinds = self.project.check_kinds(self.config.pipeline.heavy_check_after_s)
                heavy_failed = False
                if "heavy" in kinds.values():
                    gates, breaker_tests, breaker_report = await self._assess(
                        store, state, spec, sandbox, breaker_tests, round_index, focus,
                        witness=[t.path for t in oracle.tests],
                        suite=[f.path for f in oracle_files(oracle)],
                        oracle=oracle, blind_command=oracle.command,
                        seams=integration.seam_files, final=True)
                    trace = compute_trace(spec, all_workers, oracle, integration.decisions,
                                          plan=plan, written=all_written, manual=manual_ids)
                    qa = compute_qa(spec, oracle, gates, trace)
                    self._restate_from_checks(
                        ledger, gates, integration, oracle, round_index,
                        fresh=bool(gates.results),
                        repairable=self._repairable(all_written, protected, state))
                    self._restate_patch_quality(ledger, sandbox, gates, round_index)
                    heavy_failed = any(
                        not r.passed and not r.skipped and kinds.get(r.name) == "heavy"
                        for r in gates.results)
                    evidence_block = self._evidence_block(
                        spec, plan, all_workers, integration, gates, qa, trace, digest,
                        all_written, breaker=breaker_report, records=ledger.all_records(),
                        sandbox=sandbox, blind=[f.path for f in oracle_files(oracle)],
                        guidance=self._guidance(store, state, "reviewer",
                                                [f.path for f in all_written]),
                    )
                # How long each check took on this tree, so the light-or-heavy
                # split follows the project as it grows rather than its first day.
                timed = {r.name: r.duration_s for r in gates.results
                         if r.name in kinds and r.started and r.duration_s}
                if timed:
                    self.project.store.append("check_timing", timed, role="orchestrator",
                                              meta={"feature": state.feature_id})

                # Whenever the loop exits it exits through a full panel: a repair
                # can break something nobody was looking at, and only an agent
                # reading the whole change would see it. A heavy check failing in
                # the final pass is the same thing, found by a check instead.
                reopened: list[str] = []
                if (round_index or heavy_failed) and not forced_final:
                    reviews = await self._review_panel(store, state, evidence_block, round_index)
                    for role_name, report in reviews:
                        ledger.add(role_name, report.findings, round_index,
                                   model=self._answered_by(role_name))
                    late = [fid for fid, r in ledger.records.items() if r.outcome == "open"
                            and not r.disposition_reason and r.role != "human"]
                    ledger.dispose(await self._arbitrate(
                        store, state, spec, ledger, late, gates, qa, round_index))

                    # What that panel just found, if anything, and whether there
                    # is road left to act on it. See `reopen_after_panel`.
                    reopened = reopen_after_panel(
                        ledger.human_first(
                            ledger.repairable(cfg.max_attempts_per_finding)
                            + ledger.for_oracle(cfg.max_attempts_per_finding)),
                        enabled=cfg.enabled, rate_limited=limited is not None,
                        round_index=round_index, max_rounds=cfg.max_rounds,
                        affordable=affordable(), out_of_time=out_of_time(),
                    )
                    if reopened:
                        store.append("rework_reopened", {
                            "round": round_index,
                            "findings": reopened,
                            "instead_of": stop_reason,
                        }, role="orchestrator", spec_hash=state.spec_hash)
                        # It had not converged. Saying so would put a sentence in
                        # the packet that the rest of the run then contradicts.
                        stop_reason = ""
                        state.rework_closing = False

                if reopened:
                    targets = reopened
                else:
                    # Earlier features' acceptance tests need no run of their
                    # own here: they are ordinary tests in the project, and the
                    # project's checks ran them every round.
                    break

            round_index += 1
            state.rework_round = round_index
            self._save(store, state)
            # What to reset to if this round makes things worse.
            pre_round_shas[round_index] = sandbox.head()
            # The oracle first, so its files are what the workers' repairs and
            # the re-check are measured against.
            try:
                if targets:
                    outputs, applied, refused, commit_sha, repair_plan, unrun = \
                        await self._repair_round(
                            store, state, spec, sandbox, ledger, targets, protected, round_index,
                            oracle_paths=[f.path for f in oracle_files(oracle)])
                else:
                    outputs, applied, refused, repair_plan, unrun = [], [], [], None, []
                    commit_sha = sandbox.head()
            except RateLimited as exc:
                # Not a failure, and it must not be filed as one. A run on a
                # plan has no dollar ceiling to reach, so this is the ceiling it
                # actually has -- and a finding saying "the repair loop stopped:
                # <traceback>" would send a human to the config and the code,
                # neither of which is wrong. The findings go back unattempted
                # rather than charged, because none of them was tried.
                limited = exc
                store.append("rework_rate_limited", {
                    "round": round_index, "route": exc.route,
                    "retry_after_s": exc.retry_after_s, "detail": str(exc)[:2000],
                    "findings": list(targets),
                }, role="orchestrator", spec_hash=state.spec_hash)
                ledger.not_attempted(targets, (
                    f"round {round_index} did not run: route {exc.route!r} reached its "
                    "plan's limit. The attempt was returned rather than charged."))
                continue
            except Exception as exc:
                # This loop improves a result that already exists: the branch,
                # the gates and the reviews are all bought before the first
                # round runs. Letting a dead remediator take the run down trades
                # a packet with unrepaired findings -- which is useful -- for no
                # packet at all, and the whole build for nothing. So the loop
                # stops, says why, and the next pass through the top exits
                # through the full panel like any other ending.
                store.append("rework_failed", {
                    "round": round_index, "error": str(exc),
                    "findings": list(targets),
                }, role="orchestrator", spec_hash=state.spec_hash)
                ledger.not_attempted(targets, (
                    f"round {round_index} never ran: {exc}"))
                stop_reason = (
                    f"the repair loop stopped in round {round_index} and the packet was "
                    f"built from what was already there: {exc}")
                continue
            if unrun:
                # The attempt goes back to the finding, and the failure goes to
                # the human. Otherwise a broken harness quietly spends every
                # finding's attempts and the packet then reports them as tried
                # and unfixable, which is the most expensive lie this loop is
                # capable of telling.
                ledger.not_attempted(unrun, (
                    f"round {round_index} did no work on this: the coding harness made no "
                    "edits. The attempt was returned rather than charged."))
                ledger.add("computed", [Finding(
                    id=f"harness-{round_index}",
                    title=(f"A repair round changed nothing — round {round_index}, "
                           f"{len(unrun)} finding(s) left as they were"),
                    severity="major",
                    category="verification",
                    detail=(
                        "Each of these units was handed to the coding harness twice and "
                        "neither run changed a file. No repair was made and none was "
                        "possible to review. Read the harness output on the repair records "
                        "for this round: a harness that cannot edit makes every round after "
                        "it cost money and produce nothing."
                    ),
                    evidence="findings left unrepaired: " + ", ".join(unrun),
                    recommendation=("Check the harness invocation and its credentials before "
                                    "spending another round."),
                )], round_index, keep_ids=True)
            protected_refused += refused
            # Stamped with the round that dispatched them, and renamed to say
            # so, before they join the run's workers.
            #
            # A repairer numbers its units from one inside its own round, so
            # round 2's `R-1` is a different unit from round 1's and they wrote
            # different things to different files. Everything downstream keyed
            # on the unit alone would have them collide: `_renumber_decisions`
            # namespaces a decision by its unit, which is right for a build
            # unit that exists once, and `order_packet` then keeps the first of
            # any id it sees twice -- ten repair decisions recorded on one
            # feature, and six reaching the packet. Renaming here rather than at
            # each reader means the disclosure lines say it too -- they are
            # prefixed with the unit id -- so a line is attributable to the
            # round that produced it instead of to a name two rounds share.
            stamped = [
                o.model_copy(update={
                    "round": round_index,
                    "unit_id": _round_unit_id(o.unit_id, round_index),
                })
                for o in outputs
            ]
            all_workers += stamped
            assigned = {u.id: list(u.finding_ids)
                        for u in ((repair_plan.units if repair_plan else []) or [])}
            for original, unit in zip(outputs, stamped, strict=True):
                rounds_done.append(RepairDone(
                    unit_id=unit.unit_id,
                    round=round_index,
                    summary=unit.summary,
                    files=[f.path for f in (unit.files or [])],
                    finding_ids=assigned.get(original.unit_id, []),
                    # The packet's ids, so a reader can reach the decision
                    # itself from the unit that made it.
                    decision_ids=[f"{unit.unit_id}/{d.id}" for d in (unit.decisions or [])],
                    disclosures=len(collect_disclosures([unit])),
                ))
            all_written = _dedupe_writes(all_written + applied)
            repair_summaries.append(
                f"## Round {round_index}\n" + "\n".join(
                    f"- {o.unit_id}: {o.summary}" for o in outputs) or "(nothing was changed)")

            if refused:
                # A repairer reaching for the file that judges it is not a
                # nuisance to be logged; it is a finding a human has to see.
                ledger.add("computed", [Finding(
                    id=f"protected-{round_index}",
                    title=f"A repair tried to change the tests that judge it — {len(refused)} file(s)",
                    severity="major",
                    category="verification",
                    detail=("A repairer attempted to write to the tests or the test "
                            "configuration that decide whether this work passes. The writes were "
                            "refused. The attempt is the finding."),
                    evidence="\n".join(refused),
                    files=list(refused),
                    recommendation="Read what it was trying to make pass.",
                )], round_index, keep_ids=True)

            attempted_by_role: dict[str, list[str]] = {}
            for fid in targets:
                role_name = ledger.records[fid].role
                if role_name in self.config.roles:
                    attempted_by_role.setdefault(role_name, []).append(fid)
            repair_context = (
                "# The repairs that were made\n\n"
                + (repair_plan.summary if repair_plan else "")
                + "\n\n"
                + "\n\n".join(
                    f"## {o.unit_id}\n{o.summary}\n\nDecisions:\n{_json(o.decisions, 6_000)}\n\n"
                    f"Disclosure:\n{_json(o.disclosure)}"
                    for o in outputs)
                # Named for what it is. Read as "files changed", this list is
                # taken for the whole change, and a file repaired in an earlier
                # round reads as a file nobody had touched.
                + "\n\n## Files changed in this round only\n"
                + "\n".join(f"- {f.path}" for f in applied)
            )
            rechecks = await self._recheck(
                store, state, sandbox, ledger, attempted_by_role, repair_context,
                round_index, changed=[f.path for f in applied])
            for role_name, report in rechecks:
                ledger.apply_verdicts(
                    report.verdicts, round_index, commit_sha, cfg.max_attempts_per_finding)
                ledger.add(role_name, report.new_findings, round_index,
                           model=self._answered_by(role_name))
            # A duplicate is never routed, so nothing else would ever settle
            # it. Done here, once the round's verdicts are in, so the packet
            # does not report as unresolved the defects the loop just fixed.
            ledger.settle_duplicates()
            ledger.exhaust_attempts(cfg.max_attempts_per_finding)
            store.append("rework_state", {**ledger.dump(), "round": round_index},
                         role="orchestrator", spec_hash=state.spec_hash)

        ledger.finalise(stopped_early=bool(stop_reason))
        counts = ledger.counts()
        coverage = self.llm.coverage()
        if stop_reason and coverage.get("unbilled_roles"):
            # A stop reason that names a budget, in a run the budget could not
            # measure, is the most misleading sentence the packet can carry.
            stop_reason += (
                f" · note: {len(coverage['unbilled_roles'])} agent(s) ran on routes this "
                "budget cannot measure, so the figures above are not the whole cost")
        summary = ReworkSummary(
            enabled=cfg.enabled,
            rounds=round_index,
            stop_reason=stop_reason or "no repair was attempted",
            cost_usd=round(this_dispatch(), 4),
            lifetime_usd=round(spent(), 4),
            budget_usd=cfg.budget_usd,
            elapsed_s=round(time.monotonic() - started, 1),
            repaired=counts.get("repaired", 0),
            attempted_not_fixed=counts.get("attempted_not_fixed", 0),
            escalated=counts.get("escalated", 0) + counts.get("needs_spec_change", 0)
            + counts.get("out_of_spec", 0),
            dismissed=counts.get("dismissed", 0),
            unattempted=counts.get("unattempted", 0),
            reverted_rounds=reverted,
            units=rounds_done,
            protected_writes_refused=protected_refused,
            unbilled_roles=coverage.get("unbilled_roles", []),
            unbilled_turns=int(coverage.get("unbilled_turns") or 0),
            notional_usd=round(float(coverage.get("notional_usd") or 0.0), 4),
        )
        # Only this dispatch's own cost: the next one sums the records rather
        # than trusting a running total it cannot audit.
        store.append("rework_spend", {"usd": round(this_dispatch(), 6), "round": round_index},
                     role="orchestrator", spec_hash=state.spec_hash)
        store.append("rework", summary, role="orchestrator", spec_hash=state.spec_hash)
        store.append("rework_state", {**ledger.dump(), "round": round_index},
                     role="orchestrator", spec_hash=state.spec_hash)
        return {
            "gates": gates, "qa": qa, "trace": trace, "ledger": ledger, "rework": summary,
            "reviews": reviews, "workers": all_workers, "written": all_written,
            "breaker": breaker_report,
        }
