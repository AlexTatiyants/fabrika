"""The build, start to finish: the cut, both lanes, convergence and the packet.
"""

from __future__ import annotations

import logging
import asyncio
from pathlib import PurePosixPath

from ...projects import ProjectError
from ...guides import index as guide_lib_index
from ...schemas import (
    FeatureState,
    FileWrite,
    GateReport,
    IntegrationReport,
    OracleSuite,
    Packet,
    QAReport,
    ReworkSummary,
    Spec,
    SupportFile,
    TestFile,
    WorkerOutput,
    PacketJudgement,
)
from ...store import EvidenceStore
from ...workspace import unchecked_levels, PathEscape, safe_join, spec_hash

from ..text import BUILD_PHASES, reset_phases
from ..trace import compute_unclaimed
from ..records import current_payload, recorded_attempt, guides_held
from ..oracle_suite import (
    _disclaimed_paths,
    _suite_detail,
    oracle_files,
    place_blind_file,
    normalize_criterion_ids,
    retarget_command,
)
from ..ledger import FindingLedger
from ..ownership import this_process
from ..checks.gate_results import check_attribution_wiring
from ..checks.agents import (
    check_unit_environments,
    check_agent_failures,
    check_headroom,
    observed_draw,
)
from ..checks.seams import integration_detail
from ..packet import (
    merge_reviews,
    order_packet,
    restore_packet,
    hold_for_manual_checks,
    compute_stats,
    collect_disclosures,
)
from ..helpers import _dedupe_writes, _renumber_decisions

log = logging.getLogger(__name__)


class BuildMixin:
    # -- build ------------------------------------------------------------

    async def run_build(self, feature_id: str, *, resume: bool = False) -> Packet | None:
        """AC-8.3 -- architect, two lanes that share no state, then convergence.

        `resume` reuses whatever a previous attempt at this same spec already
        recorded, so a phase that failed near the end does not buy the harness
        runs, the container time and the review panel a second time. Reuse is
        keyed on `spec_hash`, so a re-planned spec resumes nothing.
        """
        store = self.store_for(feature_id)
        state = self.state_of(store)
        # Where this run's records begin, so limits that are per run -- the
        # oracle's fix sessions -- count this run and nothing before it.
        records = store.records()
        self.__dict__.setdefault("_run_floor", {})[feature_id] = (
            records[-1]["seq"] if records else 0)
        current = current_payload(store.records(), "spec")
        if current is None:
            raise ProjectError(
                "there is no spec to build: the last one was sent back and the spec writer "
                "has not run since")
        spec = Spec.model_validate(current)
        state.spec_hash = state.spec_hash or spec_hash(spec)
        state.stage = "building"
        state.owner = this_process()
        # Every build phase goes back to pending here, including on a resume.
        # What is genuinely being reused is re-marked within seconds by
        # `reused()`, which runs it through the phase context manager like any
        # other phase -- so the strip fills back in for what was kept and stays
        # dark for what has not happened yet. Without this the lights are those
        # of the attempt being replaced.
        reset_phases(state, BUILD_PHASES)
        # The previous attempt's failure is history the moment this one starts.
        # Left in place it outlives the build that succeeds, and a finished
        # feature reads as a broken one.
        state.error = ""
        self._save(store, state)
        self._emit(state)


        # Before a phase spends anything: can this project say *which* test
        # failed. It is configuration only -- no model, no command run -- and it
        # is read here rather than reported with the packet because every answer
        # it gives is cheaper to act on now than after the run it describes.
        wiring = check_attribution_wiring(self.project.state)
        store.append("attribution_wiring",
                     [f.model_dump(mode="json") for f in wiring],
                     role="orchestrator", spec_hash=state.spec_hash)

        # And before that: can the agents who write this code run it. Checked
        # here rather than reported with the packet, because everything the
        # packet could say about a blind run is worth less than not making one.
        await self.require_authoring_environment()
        try:
            return await self._build(store, state, spec, resume=resume)
        except Exception as exc:
            state.stage = "failed"
            state.owner = ""   # the run is done with it
            state.error = f"{type(exc).__name__}: {exc}"[:4000]
            self._save(store, state)
            self._emit(state)
            raise

    async def _build(
        self, store: EvidenceStore, state: FeatureState, spec: Spec, *, resume: bool = False,
    ) -> Packet | None:
        sandbox = self.open_sandbox(state)
        # Started at intake; started here when it was not, or did not finish.
        as_built_base = self._as_built_begin(store, sandbox.state.base_sha, "base")
        # Before anything is committed against them. See `record_headroom`.
        await self.record_headroom(store, state)
        digest = self._digest_before(state, sandbox)

        # What a previous attempt at this same spec already bought. Empty unless
        # asked for, so an ordinary build is unchanged.
        done = recorded_attempt(store.records(), state.spec_hash) if resume else {}
        if "oracle" in done:
            self._drop_broken_suite(store, state, sandbox, done)

        # The build is in no repair round. Left alone, the counter would still
        # hold the round the last run ended on, and every step this run reused
        # would be recorded as that round's -- a "Round 2" band at the top of a
        # run that has not yet reached round 0. The loop sets it again when it
        # starts. The way out goes with it: a run that ended closing leaves the
        # flag set, and a rebuild at `architect` would read "Closing · first
        # pass".
        state.rework_round, state.rework_closing = 0, False

        async def reused(name: str, detail: str) -> None:
            async with self._phase(store, state, name) as phase:
                phase.detail = f"reused from the last attempt · {detail}"

        cut = await self._cut(store, state, spec, digest, done, reused)
        if cut is None:
            # Stopped at plan review. The run ends here, as every run ends at a
            # gate (INV-8); a ruling starts the next one.
            return None
        plan = cut

        # AC-8.5 -- the system prompt is loaded here, outside the lane, so that
        # the verify lane's own call path touches no files at all.
        oracle_system = self.role_prompt("oracle")

        # AC-8.3 / AC-8.5 -- two lanes, no shared state, no observation of each
        # other's results until both have finished.
        have_build = "workers" in done and "integration" in done
        have_verify = "oracle" in done

        async def build_lane():
            if have_build:
                workers = [WorkerOutput.model_validate(w) for w in done["workers"]]
                await reused("workers", f"{len(workers)} unit(s)")
                # The integration is part of the build, and a run that keeps
                # the build keeps it -- whatever it did or did not show. A gap
                # in its evidence is the review's to report, not a reason to
                # redo build work on every "Verify again".
                integration = IntegrationReport.model_validate(done["integration"])
                await reused("integrator", integration_detail(integration))
                # Already on the branch from the attempt being resumed.
                return workers, integration, [f.path for w in workers for f in w.files], []
            return await self._build_lane(store, state, spec, plan, digest, sandbox)

        async def verify_lane():
            if have_verify:
                suite = OracleSuite.model_validate(done["oracle"])
                await reused("oracle", _suite_detail(suite))
                return suite
            return await self._verify_lane(store, state, spec, oracle_system)

        build_task = asyncio.create_task(build_lane())
        verify_task = asyncio.create_task(verify_lane())
        lane_results = await asyncio.gather(build_task, verify_task, return_exceptions=True)
        build_result, verify_result = lane_results

        # The two lanes fail differently, and are treated differently.
        #
        # The build lane is the thing being judged. If it raises there is no
        # code, nothing to verify and nothing to put in a packet, so the run
        # ends.
        #
        # The verify lane is one input to that judgement. If it raises, the
        # build still exists: units that ran for an hour, a branch, gates that
        # can still be run against it. Re-raising here would throw all of that
        # away to report a failure in the half that was only ever meant to
        # grade it -- and on a long run that is the difference between losing
        # the verification and losing everything.
        #
        # So a failed verify lane becomes an empty suite that says why. Nothing
        # downstream is told the criteria passed: they come back unverified, and
        # `check_blind_suite_missing` raises the reason as a blocker so a human
        # reads "nothing verified this, here is what happened" rather than
        # twelve quiet rows of `no_test`.
        if isinstance(build_result, BaseException):
            raise build_result

        if isinstance(verify_result, BaseException):
            reason = f"{type(verify_result).__name__}: {verify_result}"
            store.append("verify_lane_failed", {"error": reason[:4000]},
                         role="orchestrator", spec_hash=state.spec_hash)
            oracle = OracleSuite(
                strategy="",
                notes=("The verify lane failed before it produced a suite, so nothing in "
                       "this run was checked against the specification by an agent that "
                       f"had not seen the implementation.\n\n{reason}"),
            )
        else:
            oracle = verify_result
        workers, integration, merged, merge_rejected = build_result  # type: ignore[misc]

        # The blind suite goes in its own directory before anything else sees
        # it -- the trace tags these paths, `protected_paths` guards them, and
        # `_apply_writes` puts them on the branch, so a relocation after any of
        # those would leave the three disagreeing. Done here rather than in
        # `_verify_lane`, which stays free of anything that touches a path.
        # Tags first: a test whose ids match nothing is invisible to the matrix,
        # and the schema cannot tell a tag from a comma.
        for test in oracle.tests:
            test.criterion_ids = normalize_criterion_ids(test.criterion_ids, spec)
        oracle.untestable_criteria = normalize_criterion_ids(
            oracle.untestable_criteria, spec)

        # Into the directories this project proved at gate 0, routed per file.
        # One directory for the whole suite is wrong for any repository with
        # two runners, and wrong in the way that costs a run: a component test
        # lands where the per-file command is pytest.
        placements = list(self.project.state.blind_placements)
        moved: list[tuple[str, str]] = []
        unplaceable: list[str] = []

        nestable = [r.directory for r in self.project.state.placement_probe if r.subdirs_ok]

        def _route(blind_file: TestFile | SupportFile, *, asserts: bool) -> bool:
            relocated = place_blind_file(
                placements, blind_file.path, state.feature_id, nestable)
            if not relocated and not asserts and placements:
                # A file that asserts nothing has no runner to be collected by,
                # so nothing about it can be wrong -- a README written beside the
                # tests is the ordinary case. Misplacing one costs nothing;
                # misplacing a test costs the criteria it covers.
                relocated = (placements[0].directory.strip("/") + "/"
                             + PurePosixPath(blind_file.path).name)
            if not relocated:
                # Never guessed at. A test whose extension no proved placement
                # claims cannot be run by anything here, and putting it
                # somewhere anyway turns a usage error into a failing criterion.
                unplaceable.append(blind_file.path)
                return False
            if relocated != blind_file.path:
                moved.append((blind_file.path, relocated))
            blind_file.path = relocated
            return True

        oracle.tests = [t for t in oracle.tests if _route(t, asserts=True)]
        oracle.support = [f for f in oracle.support if _route(f, asserts=False)]
        if unplaceable:
            store.append("blind_unplaceable", {
                "paths": unplaceable,
                "placements": [p.directory for p in placements],
                "why": ("no proved placement claims these extensions, so nothing here could "
                        "run them. The criteria they covered are unverified for a reason "
                        "about this project's test setup, not about the feature."),
            }, role="orchestrator")
        # Only for a project that has deliberately cleared `oracle_command` and
        # is therefore back to the model's. With one set -- the default -- the
        # files are in a fixed place and the command already names it.
        if moved and not self.project.state.oracle_command:
            oracle.command = retarget_command(oracle.command, moved)

        # ---- convergence -------------------------------------------------
        # The units are already on disk -- merged at the end of the build lane.
        # `written` stays the union because materiality, the trace and the
        # gate-2 drift check all describe the whole change, not the last hand
        # to touch it; only the integrator's files still need applying.
        written: list[FileWrite] = _dedupe_writes(
            [f for w in workers for f in w.files] + list(integration.files))

        def already_on_branch(f: FileWrite) -> bool:
            """Is this file's path already checked out?

            Only asked on a resume. What was recorded landed on the branch when
            it was recorded, and the branch has moved on since -- repair rounds
            edit the same files the integrator wrote. Writing the recorded copy
            over them again would silently revert repairs to their pre-repair
            state, which is the one thing resuming must never do. A path that
            escapes the sandbox is left in the list, so `_apply_writes` refuses
            it and says so rather than being quietly skipped here.
            """
            try:
                return safe_join(sandbox.path, f.path).exists()
            except PathEscape:
                return False

        integrator_writes = [f for f in integration.files
                             if not (have_build and already_on_branch(f))]
        # The seam checks land on the branch as well, and stay there: they are
        # this project's integration tests, not scratch files the run
        # writes and deletes. Kept out of `written` above for the same reason
        # the blind tests are -- materiality and the line counts describe the
        # implementation, not the tests that check it.
        integrator_writes += [f for f in integration.seam_files
                              if not (have_build and already_on_branch(f))]
        seam_applied, seam_rejected = self._apply_writes(
            sandbox, integrator_writes,
            disclaimed=_disclaimed_paths(integration), spec=spec)
        # What is on the branch is the units merged earlier plus the seams closed
        # now; what was refused is refused from either pass.
        applied = sorted(set(merged) | set(seam_applied))
        rejected = merge_rejected + seam_rejected

        # The blind tests have to reach disk, or the gates run a suite that does
        # not contain them and every criterion comes back green having been
        # checked by nothing. They are kept out of `written` because materiality
        # and the line counts describe the implementation, not its tests.
        # Written again only where they are missing: on a resume they are
        # already here, and a blind test is the one file on the branch that must
        # not be rewritten from memory -- if something did change it, that is a
        # fact the gates should report rather than something to paper over.
        test_writes = [
            FileWrite(path=t.path, contents=t.contents, purpose="blind acceptance test")
            for t in oracle_files(oracle)
        ]
        test_writes = [f for f in test_writes
                       if not (have_verify and already_on_branch(f))]
        tests_applied, tests_rejected = self._apply_writes(sandbox, test_writes)

        commit_sha = sandbox.commit(f"factory: {spec.title}\n\nspec {state.spec_hash}")
        state.sandbox = sandbox.state
        store.append("writes", {
            "applied": list(applied),
            "rejected": rejected + tests_rejected,
            "test_files": tests_applied,
            "branch": sandbox.branch,
            "commit": commit_sha,
            "diff_command": sandbox.diff_command(),
        }, role="orchestrator", spec_hash=state.spec_hash)

        # ---- convergence -------------------------------------------------
        # Assess, route, repair, re-check, until a rule a human wrote says stop.
        # Everything from the gates to the last review agent lives in here,
        # because none of it happens exactly once.
        outcome = await self._converge(
            store, state, spec, plan, sandbox, digest,
            workers, integration, oracle, written, done,
        )
        gates: GateReport = outcome["gates"]
        qa: QAReport = outcome["qa"]
        trace = outcome["trace"]
        ledger: FindingLedger = outcome["ledger"]
        rework: ReworkSummary = outcome["rework"]
        reviews = outcome["reviews"]
        all_workers: list[WorkerOutput] = outcome["workers"]
        written = outcome["written"]

        verdict_across_review = merge_reviews([r for _, r in reviews])

        # Repairs are worker output on the same feature, so their decisions are
        # decisions the human rules on and their disclosures are disclosures.
        all_decisions = _renumber_decisions(all_workers, integration)
        # Two facts about the run itself, added here rather than inside the loop
        # because both are cumulative and the last of them happens after the
        # last round's checks have already run: the final panel fails on the way
        # out. Neither is repairable. One of them is a defect somebody has to
        # rule on -- an agent that wrote code it could not execute is a claim
        # about the work -- and the other is a condition of the run: a rolling
        # window was nearly empty when this started, which no ruling changes.
        ledger.add("computed", check_unit_environments(store, state.spec_hash),
                   rework.rounds, keep_ids=True)
        ledger.note("computed", check_headroom(store, state.spec_hash),
                    rework.rounds)
        ledger.add("computed", check_agent_failures(
            store, state.spec_hash,
            expected_reviewers=len(self.config.review_roles())),
            rework.rounds, keep_ids=True)
        # INV-11: every finding ever raised, in every round, with what became of
        # it. The ledger is the only source -- there is no path by which a later
        # round can shorten an earlier one's list.
        all_findings = ledger.all_findings()
        records = ledger.all_records()
        disclosures = collect_disclosures(all_workers)
        # How big each change actually is, read off the branch. Taken once,
        # here, so the matrix, the unclaimed list, the materiality table and the
        # headline figure are all summing the same measurement -- and taken
        # after convergence, so a file three repair rounds touched is counted at
        # what it finally came to rather than at any round's account of it.
        diffstat = sandbox.diffstat([f.path for f in written])
        store.append("diffstat", {
            "base_sha": sandbox.state.base_sha,
            "kind": sandbox.state.kind,
            "files": {path: {"added": a, "removed": r} for path, (a, r) in sorted(diffstat.items())},
        }, role="orchestrator", spec_hash=state.spec_hash)
        # INV-3, both directions. The matrix says which criteria have code;
        # this says which code has a criterion. Neither asks a model.
        unclaimed = compute_unclaimed(all_workers, written, integration.decisions,
                                      integration=integration, plan=plan,
                                      diffstat=diffstat)
        store.append("unclaimed", [row.model_dump(mode="json") for row in unclaimed],
                     role="orchestrator", spec_hash=state.spec_hash)
        stats = compute_stats(
            spec, trace, qa, gates, all_findings, written, all_decisions,
            records=records, rework=rework, breaker=outcome["breaker"],
            unclaimed=unclaimed, diffstat=diffstat,
        )

        async with self._phase(store, state, "rapporteur"):
            rapporteur_prompt = self._rapporteur_block(
                spec, all_decisions, all_findings, disclosures,
                trace, gates, qa, written, verdict_across_review, integration,
                records=records, rework=rework, diffstat=diffstat,
                guide_index=guide_lib_index(self._guides_at(self._base_of(state))),
            )
            judgement: PacketJudgement = await self.llm.ask(
                "rapporteur", rapporteur_prompt, PacketJudgement,
                system=self.role_prompt("rapporteur"),
            )
            store.append("packet_raw", judgement, role="rapporteur",
                         model=self._answered_by("rapporteur"), spec_hash=state.spec_hash,
                         prompt=rapporteur_prompt)
            packet = order_packet(judgement, all_decisions, all_findings)
            # AC-8.9 / INV-4
            packet = restore_packet(
                packet,
                decisions=all_decisions, findings=all_findings, files=written,
                trace=trace, unclaimed=unclaimed,
                disclosures=disclosures, open_questions=spec.open_questions,
                stats=stats, records=records, rework=rework, diffstat=diffstat,
            )
            if not packet.strongest_objection:
                packet.strongest_objection = verdict_across_review.strongest_objection
            hold_for_manual_checks(packet, spec, trace, unchecked_levels(self.project.state))
            packet.guides = guides_held(store.records())
            store.append("packet", packet, role="orchestrator", spec_hash=state.spec_hash)

        # What the feature changed in the system, not just in files.
        change = await self._as_built_change(store, state, sandbox, as_built_base)

        # Onto the branch, after everything it argues about is already on it.
        self._commit_packet(store, state, spec, sandbox, packet, system_change=change)

        store.append("usage", self.llm.usage_report(), role="orchestrator")
        # The closing reading. Paired with the one taken before the build it
        # says what this run drew from each plan, which is what the next run's
        # "is there room to finish" is answered against. Free for a route that
        # writes its gauge down; never escalated to a probe here, because a
        # finished run does not need the number badly enough to spend for it.
        try:
            await self.record_headroom(store, state, when="after")
            drew = observed_draw(store, state.spec_hash)
            if drew:
                self.routes.draws.record(drew)
                store.append("plan_draw", {"routes": drew}, role="orchestrator",
                             spec_hash=state.spec_hash)
        except Exception:
            # Never worth the packet. But the next run's "is there room to
            # finish" is answered against these figures, so their absence is
            # said rather than discovered.
            log.warning("could not record what %s drew from its plans", state.feature_id,
                        exc_info=True)
        state.stage = "awaiting_verdict"
        self._save(store, state)
        self._emit(state)
        return packet
