"""What a person does at gate 2 and after: rulings, flags, rework, verdicts,
discarding a feature, listing them.
"""

from __future__ import annotations

from typing import Any

from ...projects import ProjectError
from ...sandbox import SandboxError
from ...schemas import FeatureState, ReworkPlan, HumanFlag, InterrogationReport, Packet

from ..text import _now
from ..flags import compute_rework, human_flags
from ..ownership import owner_is_alive, is_orphaned


class HumanMixin:
    # -- gate 2 -----------------------------------------------------------

    def record_ruling(self, feature_id: str, decision_id: str, ruling: str, note: str = "") -> dict:
        store = self.store_for(feature_id)
        return store.append(
            "ruling", {"decision_id": decision_id, "ruling": ruling, "note": note, "at": _now()},
            role="human",
        )

    def flags(self, feature_id: str) -> list[HumanFlag]:
        """Every flag this human raised, with its current disposition.

        Retagging is a new record, never an edit: the store has no update path
        and a review is evidence like everything else. What you flagged and what
        you first called it both survive; `latest` decides only the routing.
        """
        return human_flags(self.store_for(feature_id).of_kinds("flag", "flag_retag"))

    def record_flag(
        self, feature_id: str, text: str, *, source: str = "comment", anchor: str = "",
        disposition: str = "repair", severity: str = "major",
    ) -> HumanFlag:
        """One thing the human identified, routed by the human.

        It skips the arbiter, which routes findings a model raised. Routing is a
        judgment and this one was made by a person; asking a model to second-guess
        it would be the arbiter overruling the human it works for.
        """
        store = self.store_for(feature_id)
        taken = {f.id for f in self.flags(feature_id)}
        n = 1
        while f"H-{n}" in taken:
            n += 1
        flag = HumanFlag(id=f"H-{n}", source=source, anchor=anchor, text=text,
                         disposition=disposition, severity=severity)
        store.append("flag", flag, role="human")
        return flag

    def retag_flag(self, feature_id: str, flag_id: str, disposition: str) -> ReworkPlan:
        """Change where one flag goes, and say where that leaves the review."""
        store = self.store_for(feature_id)
        if flag_id not in {f.id for f in self.flags(feature_id)}:
            raise ProjectError(f"no flag {flag_id!r} on feature {feature_id!r}")
        store.append("flag_retag", {"flag_id": flag_id, "disposition": disposition,
                                    "at": _now()}, role="human")
        return compute_rework(self.flags(feature_id))

    def rework_plan(self, feature_id: str) -> ReworkPlan:
        """Where this review goes. Computed, never chosen. (INV-6)"""
        return compute_rework(self.flags(feature_id))

    def rework_budget(self, feature_id: str) -> dict[str, Any]:
        """What this feature has left to spend on rework, over its whole life.

        Summed from the ledger, so it cannot be reset by restarting the process
        or by sending the same packet back again. The reserve is never spendable
        by the loop: without it the failure mode is a feature that spends
        everything repairing and then cannot afford to produce a packet at all.
        """
        cfg = self.config.rework
        spends = self.store_for(feature_id).all("rework_spend")
        spent = sum(float((r.get("payload") or {}).get("usd") or 0.0) for r in spends)
        spendable = max(0.0, cfg.budget_usd - cfg.reserve_usd)
        return {
            "limit_usd": cfg.budget_usd,
            "reserve_usd": cfg.reserve_usd,
            "spendable_usd": round(spendable, 4),
            "spent_usd": round(spent, 4),
            "remaining_usd": round(max(0.0, spendable - spent), 4),
            "exhausted": spent >= spendable,
            "max_rounds": cfg.max_rounds,
            "dispatches": len(spends),
        }

    def _dispatchable(self, feature_id: str, route: str) -> tuple[Any, ReworkPlan]:
        """Shared guard. The route is never an argument to a dispatch.

        It is computed from what the human tagged, and letting a caller pass it
        in would be the one way to reach a destination the flags do not support.
        (INV-6)
        """
        store = self.store_for(feature_id)
        state = self.state_of(store)
        if state.stage != "awaiting_verdict":
            raise ProjectError(
                f"feature {feature_id!r} is {state.stage}, not a packet awaiting your ruling")
        plan = self.rework_plan(feature_id)
        if plan.route != route:
            raise ProjectError(
                f"this review routes to {plan.route!r}, not {route!r}: {plan.because}")
        return state, plan

    async def send_to_repair(self, feature_id: str) -> Packet:
        """Dispatch a gate-2 review to the repair loop.

        The spec is untouched, so its hash is untouched, so everything the last
        attempt bought is reused: the plan, the blind tests, the work already
        merged, and the finding ledger with what became of every earlier
        finding. What is new is the human's flags, which enter it already routed.
        """
        state, plan = self._dispatchable(feature_id, "repair")
        store = self.store_for(feature_id)
        budget = self.rework_budget(feature_id)
        if budget["exhausted"]:
            raise ProjectError(
                f"this feature has spent its rework budget: ${budget['spent_usd']:.2f} of "
                f"${budget['spendable_usd']:.2f} spendable, with ${budget['reserve_usd']:.2f} "
                "held back for the packet. Raise `rework.budget_usd` if it is worth more.")
        store.append("dispatch", {
            # The oracle's flags travel on this dispatch too -- its fix session
            # runs at the top of the round, before the workers' repairs -- so
            # the record of what was sent names them.
            "route": "repair", "flags": [*plan.to_repair, *plan.to_oracle],
            "budget": budget, "at": _now(),
        }, role="human")
        return await self.run_build(feature_id, resume=True)

    async def revalidate(self, feature_id: str) -> Packet:
        """Run the verdict pass again from nothing, over the same branch.

        For when the packet is an artifact of the harness rather than of the
        code: gates that ran without their toolchain, a re-check that judged a
        narrative instead of the tree, a repair loop that spent its rounds on a
        harness that was not editing. None of that is a reason to rebuild -- the
        code is the code -- and all of it is a reason not to believe a single
        number in the packet.

        So the build lane is kept exactly as it stands (the plan, the units as
        merged, the blind tests, the branch and every commit on it) and the
        verdict pass starts from zero: gates, breaker, panel, arbiter, and a
        finding ledger with no rounds spent. The earlier pass is not deleted,
        edited or contradicted -- it stays in the ledger with a `revalidate`
        record beside it saying a human stopped trusting it and why. (INV-11)

        The budget does not reset. It is counted over the feature's whole life
        precisely so that sending the same work round again cannot buy more of
        it than a human agreed to.
        """
        store = self.store_for(feature_id)
        state = self.state_of(store)
        if state.stage != "awaiting_verdict":
            raise ProjectError(
                f"feature {feature_id!r} is {state.stage}, not a packet awaiting your ruling. "
                "Revalidation replaces a verdict pass you have in front of you; there is "
                "nothing here to replace.")
        if state.sandbox is None or not state.sandbox.branch:
            raise ProjectError(
                f"feature {feature_id!r} has no branch to measure; there is nothing to "
                "revalidate.")
        store.append("revalidate", {
            "at": _now(),
            "branch": state.sandbox.branch,
            "commit": state.sandbox.commit_sha,
            "discards": [
                "the gate results", "the review panel", "the finding ledger and its rounds",
            ],
            "keeps": [
                "the frozen spec", "the plan", "the units as merged",
                "the blind tests", "the branch and every commit on it",
            ],
            "why": ("a human judged the last verdict pass to be measuring the harness "
                    "rather than the code"),
        }, role="human", spec_hash=state.spec_hash)
        return await self.run_build(feature_id, resume=True)

    async def rebuild(self, feature_id: str, *, reset_branch: bool = True) -> Packet:
        """Build the units again, over the spec the human already froze.

        The mirror of `revalidate`. That one keeps the build and throws away the
        verdict, for when the packet measured the harness instead of the code.
        This throws away the build and keeps the spec, for when the *build* was
        the harness: units whose coding harness edited nothing, blind tests that
        landed where the project's own gate would collect them, a plan with no
        reading list.

        Gate 1 is not reopened. The frozen spec, the human's answers, and the
        approval all survive -- they are keyed on `spec_hash` and nothing here
        touches them. That is the part of a run that costs a person their
        attention rather than a provider's money, and it is exactly the part
        worth not spending twice.

        Everything downstream of the spec is bought again: the architect (so
        the plan carries `read_files`, which an older plan may lack), the
        oracle (so its suite declares a command and brings its own fixtures, in
        its own directory), the units, and the whole verdict pass.

        `reset_branch` is on by default and should stay on. The branch still
        holds every commit the last attempt made, and building over them is not
        a restart -- the workers would edit half-built files, the diff would be
        measured against work nobody re-approved, and "not asked for" would be
        computed from two attempts at once. Turning it off is for the case where
        a human has looked at that branch and decided the code on it is worth
        keeping, which is a judgement this method will not make for them.

        The budget does not reset, for the same reason it does not in
        `revalidate`: it is counted over the feature's whole life so that sending
        the same work round again cannot buy more of it than a human agreed to.
        """
        store = self.store_for(feature_id)
        state = self.state_of(store)
        if state.stage in ("intake", "awaiting_answers", "writing_spec",
                           "awaiting_spec_approval"):
            raise ProjectError(
                f"feature {feature_id!r} is {state.stage}: there is no build to discard. "
                "A spec that has not been frozen is changed by answering gate 1 again, "
                "not by rebuilding.")
        if store.payload("spec") is None:
            raise ProjectError(
                "there is no frozen spec to build against: the last one was sent back and "
                "the spec writer has not run since.")
        if state.sandbox is None or not state.sandbox.branch:
            raise ProjectError(
                f"feature {feature_id!r} has no sandbox to rebuild in.")

        base = state.sandbox.base_sha
        if reset_branch and not base:
            raise ProjectError(
                f"feature {feature_id!r} has no recorded base commit, so its branch cannot "
                "be returned to one. Rebuild with reset_branch=False and read the diff "
                "knowing it carries the last attempt as well as this one.")

        discarded: list[str] = []
        if reset_branch:
            sandbox = self.open_sandbox(state)
            head_before = sandbox.head()
            sandbox.reset_to(base)
            # The commits are unreachable, not gone: the sha is written down
            # here, so a human who wants the old attempt back has the one thing
            # they need to get it. (INV-11 -- the record only ever grows.)
            state.sandbox = sandbox.state
            state.sandbox.commit_sha = ""
            discarded = [head_before]

        store.append("rebuild", {
            "at": _now(),
            "branch": state.sandbox.branch,
            "reset_to": base if reset_branch else "",
            "discarded_head": discarded[0] if discarded else "",
            "recover_with": (f"git checkout {discarded[0]}" if discarded else ""),
            "discards": [
                "the plan", "every unit and what it wrote", "the integration",
                "the blind tests", "the gate results", "the review panel",
                "the finding ledger and its rounds",
            ] + (["every commit the last attempt made"] if reset_branch else []),
            "keeps": [
                "the frozen spec", "the human's answers at gate 1", "the approval",
                "every record of the attempt being replaced",
            ],
            "why": ("a human judged the last build to be measuring the coding harness "
                    "rather than the specification"),
        }, role="human", spec_hash=state.spec_hash)

        # `resume=False` on purpose: reuse is keyed on the last `plan`, and the
        # plan is one of the things being replaced.
        return await self.run_build(feature_id, resume=False)

    async def reopen_spec(self, feature_id: str) -> InterrogationReport:
        """Send a gate-2 review back to gate 1, and reset the branch to do it.

        The reset is both why this is expensive and why it is allowed at all.
        `rerun_intake` refuses to re-read a repository once gate 1 has closed,
        on the grounds that it would produce a spec which does not describe what
        was built -- and it is right. Resetting dissolves its objection rather
        than arguing with it: there is nothing built any more, so the new spec
        describes what will be.

        Everything the old attempt produced stays in the ledger. A reset branch
        is not a forgotten one.
        """
        state, plan = self._dispatchable(feature_id, "reopen_spec")
        store = self.store_for(feature_id)
        flags = {f.id: f for f in self.flags(feature_id)}
        forced = [flags[i] for i in plan.forced_by if i in flags]

        # The branch first. If this fails the spec must not reopen, or gate 1
        # runs against a checkout still holding a superseded spec's code.
        if state.sandbox is not None and state.sandbox.base_sha:
            sandbox = self.open_sandbox(state)
            before = sandbox.head()
            sandbox.reset_to(state.sandbox.base_sha)
            store.append("branch_reset", {
                "from": before, "to": state.sandbox.base_sha,
                "why": "the spec is reopening; what was built answers to a spec nobody approved",
                "at": _now(),
            }, role="orchestrator")

        store.append("dispatch", {
            "route": "reopen_spec", "flags": plan.forced_by, "at": _now(),
        }, role="human")

        correction = "\n\n".join(
            ["The frozen spec was built and reviewed, and at gate 2 I found that what I "
             "asked for was itself wrong. Ask again with this in hand:"]
            + [f"- {f.anchor + ': ' if f.anchor else ''}{f.text}" for f in forced]
        )
        return await self.reinterrogate(feature_id, correction)

    def record_verdict(self, feature_id: str, verdict: str, note: str = "") -> FeatureState:
        store = self.store_for(feature_id)
        state = self.state_of(store)
        store.append("verdict", {"verdict": verdict, "note": note, "at": _now()}, role="human")
        state.stage = "accepted" if verdict == "accepted" else "rejected"

        # Accepted: the branch is the deliverable, so drop the checkout and keep
        # the ref. Rejected: leave everything standing, because the first thing
        # anyone does after rejecting is go and look at what was actually built.
        if verdict == "accepted" and state.sandbox is not None:
            try:
                sandbox = self.open_sandbox(state)
                # Before the release, which is the last moment there is a
                # checkout to write into. After it the branch is still there and
                # nothing can be added to it without making one again.
                self._commit_spec(store, state, sandbox, verdict=verdict, note=note)
                sandbox.release(keep_branch=True)
                state.sandbox = sandbox.state
            except SandboxError as exc:
                store.append("sandbox_release_failed", {"error": str(exc)}, role="orchestrator")

        self._save(store, state)
        self._emit(state)
        return state

    # -- discarding -------------------------------------------------------

    def delete_feature(self, feature_id: str, *, delete_branch: bool = False) -> dict[str, Any]:
        """Discard a feature: its worktree, and its ledger.

        Not a hole in INV-2. That invariant is about a *running* feature: no
        agent can edit or erase what an earlier one recorded, so a late one
        cannot launder an early one's disclosure. A human throwing away an
        abandoned feature whole is a different act, and it is theirs to make.

        The branch survives by default. It is the only thing here that might
        represent work, and it costs nothing to keep.
        """
        import shutil

        store = self.store_for(feature_id)
        if not store.has("state"):
            raise ProjectError(f"no feature {feature_id!r}")
        state = self.state_of(store)

        # Asked of the operating system, not of the stage field.
        #
        # `stage` is a claim, and the one case where it is reliably wrong is
        # exactly this one: a server killed mid-build leaves "building" behind
        # with nobody building, and a guard that reads it refuses forever. "Wait
        # for it to finish" would be advice that can never be satisfied, and the
        # only way out would be editing the evidence store by hand.
        if state.stage in ("intake", "building") and owner_is_alive(state.owner):
            raise ProjectError(
                f"feature {feature_id!r} is {state.stage} and process "
                f"{state.owner.partition(':')[0]} is still working on it. Wait for it to "
                "finish or fail, then discard it."
            )

        removed = {"feature_id": feature_id, "branch": "", "branch_deleted": False,
                   "worktree": "", "evidence": str(store.dir)}
        if state.sandbox is not None:
            removed["branch"] = state.sandbox.branch
            removed["worktree"] = state.sandbox.path
            try:
                sandbox = self.open_sandbox(state)
                sandbox.release(keep_branch=not delete_branch)
                removed["branch_deleted"] = delete_branch
            except SandboxError:
                pass

        shutil.rmtree(store.dir, ignore_errors=True)
        self._emit(state)
        return removed

    # -- listing ----------------------------------------------------------

    def features(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for feature_id in self.project.feature_ids():
            store = self.store_for(feature_id)
            payload = store.payload("state")
            if not payload:
                continue
            state = FeatureState.model_validate(payload)
            active = next(
                (p for p in state.phases if p.status in ("running", "failed")), None
            )
            # What the packet says, for the rail: a row waiting on a ruling is
            # read differently when the review says ship than when it says stop.
            packet = ((store.current("packet") or {}).get("payload")
                      if state.stage == "awaiting_verdict" else None)
            out.append({
                "feature_id": state.feature_id,
                "phase": active.name if active else "",
                "phase_status": active.status if active else "",
                "project_id": self.project.id,
                "project_name": self.project.state.name,
                "title": state.title,
                "intent": state.intent,
                "stage": state.stage,
                # `stage` is a claim the crash is the reason nobody updated.
                # A board that reads it alone shows a dead run as in flight
                # forever, which is the one row a reader cannot act on.
                "orphaned": is_orphaned(state),
                "created_at": state.created_at,
                "updated_at": state.updated_at,
                "branch": state.sandbox.branch if state.sandbox else "",
                "has_packet": store.has("packet"),
                "verdict": (packet or {}).get("verdict") or "",
            })
        out.sort(key=lambda f: f["updated_at"], reverse=True)
        return out
