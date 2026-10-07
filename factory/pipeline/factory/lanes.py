"""The build lane: units in parallel, then the integrator.
"""

from __future__ import annotations

import asyncio
import time
from typing import Sequence

from ...sandbox import Sandbox
from ...schemas import FeatureState, IntegrationReport, Plan, Spec, WorkerOutput, WorkUnit
from ...store import EvidenceStore
from ...workspace import cleanup_context, testing_context

from ..text import _json
from ..ownership import HarnessBlocked
from ..checks.seams import settle_integration, seam_checks, integration_detail
from ..helpers import _dedupe_writes


class BuildLaneMixin:
    # ---- build lane -----------------------------------------------------

    async def _build_lane(
        self, store: EvidenceStore, state: FeatureState, spec: Spec, plan: Plan, digest: str,
        sandbox: Sandbox,
    ) -> tuple[list[WorkerOutput], IntegrationReport, list[str], list[str]]:
        """AC-8.4 -- workers in parallel, bounded, each in its own worktree.

        Returns the unit outputs, the integration report, and what merging the
        units onto the branch actually applied and refused.
        """
        worker_system = (self.role_prompt("worker")
                         + cleanup_context(self.project.state.testing))
        semaphore = asyncio.Semaphore(max(1, self.config.pipeline.max_parallel_workers))

        async def one(unit) -> WorkerOutput:
            async with semaphore:
                started = time.monotonic()
                cost_before = float(self.llm.usage_report().get("total_cost") or 0.0)
                # INV-2 wants both halves of the exchange. A harness makes its own
                # turns, but what it was handed and what it was asked to account
                # for are ours, and without them a failed unit is unaskable.
                seen: list[tuple[str, str]] = []
                unit = unit.model_copy(update=self._unit_guidance(
                    store, state, "worker", list(unit.files_expected)))
                output = await self.executor.run(
                    unit, spec, digest, worker_system, sandbox.path,
                    on_prompt=lambda label, text: seen.append((label, text)),
                    environment=self.unit_env(f"{state.feature_id}-{unit.id}", role="worker"),
                    on_environment=self.env_recorder(
                        store, state, unit=unit.id, role="worker"),
                    send_back=self._send_back_for(unit, sandbox.path),
                    on_guides=self._guides_recorder(store, "worker", list(unit.files_expected)),
                )
                def unit_cost() -> float:
                    return max(0.0, float(
                        self.llm.usage_report().get("total_cost") or 0.0) - cost_before)

                for label, text in seen:
                    store.append("worker_prompt", {"unit_id": unit.id, "which": label},
                                 role="worker", spec_hash=state.spec_hash,
                                 meta={"unit_id": unit.id, "which": label}, prompt=text)
                # What the unit cost, measured as the ledger's movement across
                # it rather than as the sum of the calls this app made. Those
                # are different numbers: the coding harness is a separate
                # process on the same key, and for a unit built by one the
                # reflection call is a rounding error against the build. A
                # timeline summing per-call costs showed four units at $0.08
                # while the harness had spent dollars, because nothing it did
                # was a call this app placed.
                store.append("worker", output, role="worker",
                             model=self._answered_by("worker"), spec_hash=state.spec_hash,
                             meta={"unit_id": unit.id,
                                   "seconds": round(time.monotonic() - started, 2),
                                   "cost_usd": round(unit_cost(), 6)})
                return output

        async with self._phase(store, state, "workers") as phase:
            phase.detail = f"{len(plan.units)} units, {self.config.pipeline.max_parallel_workers} at a time"
            workers = list(await asyncio.gather(*(one(u) for u in plan.units)))

            # The units are built in isolated worktrees and merged here, in the
            # order the plan lists them. Landing them before the integrator runs
            # is what makes integration a real job: it can read the merged tree
            # rather than reason about a merge that has not happened, and two
            # units writing one path becomes a fact on disk instead of two
            # entries in a prompt.
            unit_writes = _dedupe_writes([f for w in workers for f in w.files])
            merged, merge_rejected = self._apply_writes(sandbox, unit_writes, spec=spec)
            phase.detail += f" · {len(merged)} file(s) merged"
            if merge_rejected:
                phase.detail += f", {len(merge_rejected)} refused"
            # Committed, not merely written. The integrator works in a worktree
            # branched from HEAD, so anything left uncommitted here is invisible
            # to it -- and it would be reasoning about the pre-merge tree again,
            # which is the whole thing this ordering exists to stop.
            merge_sha = sandbox.commit(
                f"factory: units merged for {spec.title}\n\nspec {state.spec_hash}")
            store.append("writes", {
                "applied": list(merged), "rejected": merge_rejected,
                "stage": "units", "branch": sandbox.branch, "commit": merge_sha,
            }, role="orchestrator", spec_hash=state.spec_hash)

        integration = await self._integrate(store, state, spec, plan, digest, sandbox, workers)
        return workers, integration, merged, merge_rejected

    async def _integrate(
        self, store: EvidenceStore, state: FeatureState, spec: Spec, plan: Plan, digest: str,
        sandbox: Sandbox, workers: Sequence[WorkerOutput],
    ) -> IntegrationReport:
        """Close the seams between the merged units, and name how to show they hold.

        Its own step so that a resume can run it again over units it reuses.
        Reused, an integration recorded as having closed nothing would carry
        through every resume -- four on one feature, labelled "seams already
        closed" each time -- while the one seam the plan said both unit suites
        were blind to was never checked by anything.
        """
        async with self._phase(store, state, "integrator") as phase:
            # The same testing surface the oracle is given, and for the same
            # reason: an agent asked to write a test against a repository it
            # has not surveyed invents a fixture contract nobody agreed to.
            # Without it the integrator writes shell scripts -- the one format
            # no project ever asked for, chosen because nothing has told it what
            # this project actually uses.
            surface = testing_context(self.project.state.testing)
            # What the units agreed to and what each says it actually did. The
            # files themselves are not described here: they are on the
            # branch, one commit back, and the integrator opens them.
            brief = (
                "# Seams the architect said must hold\n\n"
                + ("\n".join(f"- {s}" for s in plan.seams) or "- (none listed)")
                + f"\n\n{plan.integration_notes}\n\n"
                + "# What each unit says it built\n\n"
                + "\n\n".join(
                    f"## {w.unit_id}\n{w.summary}\n\n"
                    f"provides: {_json([u.provides for u in plan.units if u.id == w.unit_id])}\n"
                    f"files: {', '.join(f.path for f in w.files) or '(none)'}\n"
                    f"disclosure: {_json(w.disclosure)}"
                    for w in workers
                )
                + "\n\nEvery unit above has been merged onto the branch you are working in. "
                "Open the files and read them: what is on disk is what shipped, and a unit's "
                "summary is its own account of its work, not evidence of it. Close the seams "
                "by editing in place. Do not rewrite a file to insert a line into it.\n\n"
                "Then leave behind a way to show that they hold. For each seam above, write "
                "a test that exercises both sides of it together -- calls the real provider "
                "and reads the result where the consumer does, or drives the running "
                "service -- and fails if they disagree. Run each one and see it pass.\n\n"
                "Write them the way this repository already writes tests at that level. The "
                "testing surface below is this project's own, recorded from the survey a "
                "human approved: use its fixtures, copy its import lines, and put your file "
                "where that tier's tests live. Do not invent a format and do not write a "
                "shell script -- a previous run did, in a shell it had not been told the "
                "name of, and all ten of its checks died on their own second line having "
                "tested nothing.\n\n"
                f"Put `{self.config.rework.seam_marker}` in the NAME of every file that is a "
                "check -- `test_seam_tag_round_trip.py`, `cardTags.seam.test.ts` -- in "
                "whatever shape that runner collects. That directory is shared with the "
                "project's own tests and with every feature built here before you, so the "
                "name is the only thing that marks which checks are yours. A helper that "
                "asserts nothing must not carry it.\n\n"
                "If the integration tier cannot reach a seam honestly, write it at the user "
                "tier against the running stack rather than at the unit tier with the "
                "network mocked -- a mocked test passes in full while the application is "
                "broken. If no tier this project has can reach it, say so in `unresolved`.\n\n"
                "These go onto the branch with your edits and stay there as part of this "
                "repository's suite; the factory also runs each one as its own check, in "
                "the project's environment, every round. Editing nothing is a fine outcome "
                "when the seams already hold -- the test is how you show that they do. "
                "Leaving no test is not an outcome: this step is not finished without one, "
                "and a session that ends without one is sent back for it."
                + ("\n\n---\n\n# This project's testing surface\n\n" + surface
                   if surface else "")
            )
            # An integration is a work unit like any other: it owns the whole
            # tree, it has an objective, and it goes through the same executor.
            # On the `direct` executor that means a model returning whole
            # files, because that path has no checkout to edit.
            unit = WorkUnit(
                id="integration",
                title="Close the seams between the units",
                objective=brief,
                criterion_ids=[c.id for c in spec.acceptance_criteria],
                **self._unit_guidance(store, state, "integrator"),
            )
            async def attempt(objective: str) -> IntegrationReport:
                seen: list[tuple[str, str]] = []
                output = await self.executor.run(
                    unit.model_copy(update={"objective": objective}), spec, digest,
                    self.role_prompt("integrator"), sandbox.path,
                    role="integrator",
                    on_prompt=lambda label, text: seen.append((label, text)),
                    environment=self.unit_env(
                        f"{state.feature_id}-integration", role="integrator"),
                    on_environment=self.env_recorder(
                        store, state, unit="integrator", role="integrator"),
                    on_guides=self._guides_recorder(store, "integrator", None),
                )
                for label, text in seen:
                    store.append("integration_prompt", {"which": label}, role="integrator",
                                 spec_hash=state.spec_hash, meta={"which": label}, prompt=text)
                report = IntegrationReport(
                    summary=output.summary,
                    files=output.files,
                    decisions=output.decisions,
                    seam_issues=list(output.disclosure.flags),
                    unresolved=list(output.disclosure.not_implemented),
                )
                settle_integration(report, self.config.rework.seam_marker)
                store.append("integration", report, role="integrator",
                             model=self._answered_by("integrator"),
                             spec_hash=state.spec_hash, prompt=objective)
                return report

            integration = await attempt(brief)
            # The step's own completion condition, and the reason it is one:
            # the brief's last instruction is to leave a script that shows the
            # seams hold, and a session that concludes "no edits were needed"
            # reads the sentence before it as permission and stops. Measured:
            # 106 seconds of real seam-walking, no script, and the absence
            # surfaced an hour later as a MAJOR in the packet for a human to
            # rule on -- a question nobody but the integrator could answer.
            #
            # Asked again before anything downstream runs, with the one thing
            # it owes named on its own. Editing nothing stays a fine outcome;
            # leaving no check does not.
            if not seam_checks(integration):
                phase.detail = "no seam check · asking again"
                integration = await attempt(
                    brief
                    + "\n\n---\n\n# You left no seam check\n\n"
                    "The last session wrote no test carrying "
                    f"`{self.config.rework.seam_marker}` in its name, so nothing in this "
                    "run shows the seams hold. That is what you owe here, and it is owed "
                    "whether or not the seams needed fixing: if they already hold, the "
                    "test is how that is shown, and if you changed something, it is how "
                    "the change is shown to have worked.\n\n"
                    "Write one test per seam, in this project's own idiom and in its own "
                    "test tree, run each one, and see it pass. Change nothing else -- the "
                    "edits from the last session, if any, are already on the branch."
                )
            if not seam_checks(integration):
                # Stopping the line rather than carrying an unprovable
                # integration into review, for the same reason `HarnessBlocked`
                # exists at the gates: every station after this one reasons
                # about measurements, and there is no measurement of the seams
                # to reason about. `retry-build` picks it up.
                raise HarnessBlocked(
                    "the integrator left no seam check, twice, so nothing shows the seams "
                    "between the units hold. No test carrying "
                    f"`{self.config.rework.seam_marker}` in its name was written and the "
                    "branch is as the units left it. Resume the build to ask again."
                )
            phase.detail = integration_detail(integration)
        return integration
