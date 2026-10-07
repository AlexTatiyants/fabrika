"""The cut: the architect's plan, its check, and a person's ruling on it.
"""

from __future__ import annotations

from typing import Any, Callable

from ...projects import ProjectError
from ...schemas import FeatureState, Plan, Spec, CheckObjection, CheckReport, PlanRevision
from ...store import EvidenceStore
from ...workspace import spec_text

from ..text import _now, _json
from ..ownership import this_process
from ..checks.cut import (
    anchored_objections,
    settle_objections,
    objection_block,
    units_changed,
    cut_facts,
    merge_units,
    cut_open,
    pending_ruling,
)


class CutMixin:
    # -- the cut ------------------------------------------------------------

    @staticmethod
    def _architect_prompt(spec: Spec, digest: str, guidance: str = "") -> str:
        written = f"{guidance}\n\n---\n\n" if guidance else ""
        return f"{spec_text(spec)}\n\n---\n\n{written}# Repository\n\n{digest}\n"

    def _stop_at_cut(self, store: EvidenceStore, state: FeatureState) -> None:
        state.stage = "awaiting_cut_approval"
        state.owner = ""   # the run is done with it
        self._save(store, state)
        self._emit(state)
        return None

    async def _cut(self, store, state, spec: Spec, digest: str, done: dict[str, Any],
                   reused: Callable[[str, str], Any]) -> Plan | None:
        """The architect's cut, checked -- or None when a person has to rule on it.

        Three ways in. A ruling nobody has acted on is acted on. A plan already
        recorded for this spec is reused, unless its plan review is still open --
        resuming must not walk past a gate. Otherwise the architect cuts and the
        plan checker reads the cut.
        """
        records = store.records()
        if "plan" in done:
            plan = Plan.model_validate(done["plan"])
            ruling = pending_ruling(records, state.spec_hash)
            if ruling is not None:
                return await self._apply_ruling(store, state, spec, digest, plan, ruling)
            if cut_open(records, state.spec_hash):
                return self._stop_at_cut(store, state)
            await reused("architect", f"{len(plan.units)} unit(s)")
            await reused("plan_checker", "the cut was checked before")
            return plan

        architect_prompt = self._architect_prompt(
            spec, digest, self._guidance(store, state, "architect"))
        async with self._phase(store, state, "architect"):
            plan = await self.llm.ask(
                "architect", architect_prompt, Plan, system=self.role_prompt("architect"),
            )
            store.append("plan", plan, role="architect",
                         model=self._answered_by("architect"),
                         spec_hash=state.spec_hash, prompt=architect_prompt)
        return await self._check_plan(store, state, spec, plan, digest)

    async def _check_plan(self, store, state, spec: Spec, plan: Plan,
                          digest: str) -> Plan | None:
        """The plan checker takes each worker's seat; the architect answers once.

        Then code decides whether a person is needed, from two things only: an
        objection the architect did not settle, and a fact about the cut that
        nobody can rebut. With no plan checker configured there is no cut
        review at all, and the build runs on the architect's cut as it stands.
        """
        role = self.config.roles.get("plan_checker")
        runtime = self.project.state.runtime_packages
        architect_prompt = self._architect_prompt(
            spec, digest, self._guidance(store, state, "architect"))
        async with self._phase(store, state, "plan_checker") as phase:
            if role is None or not role.enabled:
                phase.detail = "no plan checker configured"
                return plan
            objections: list[CheckObjection] = []
            try:
                report = await self.llm.ask(
                    "plan_checker",
                    f"# The spec\n\n{spec_text(spec)}\n\n---\n\n"
                    f"# The architect's plan\n\n{_json(plan)}\n\n---\n\n"
                    f"# Repository\n\n{digest}\n",
                    CheckReport, system=self.role_prompt("plan_checker"))
                objections = anchored_objections(
                    report.objections, prefix="K",
                    units=[u.id for u in plan.units],
                    criteria=[c.id for c in spec.acceptance_criteria])
                store.append("plan_check", {
                    "objections": [o.model_dump(mode="json") for o in objections],
                    "summary": report.summary,
                }, role="plan_checker", model=self._answered_by("plan_checker"),
                    spec_hash=state.spec_hash)
            except Exception as exc:
                # Advisory: a checker that fails leaves the cut unargued, not
                # the run dead. The facts below are still measured.
                store.append("plan_check", {"error": f"{type(exc).__name__}: {exc}"[:600]},
                             role="orchestrator", spec_hash=state.spec_hash)

            revised, answers = plan, []
            if objections:
                try:
                    revision = await self.llm.ask(
                        "architect",
                        architect_prompt
                        + "\n---\n\n# The plan you wrote\n\n" + _json(plan)
                        + "\n\n---\n\n# The plan checker's objections\n\n"
                        + objection_block(objections)
                        + "\n\n---\n\nAnswer every objection once, by id: revise the plan so it "
                          "no longer holds, or rebut it and say why the checker is wrong. One unit "
                          "is always a valid plan. Return the whole plan.",
                        PlanRevision, system=self.role_prompt("architect"))
                    revised, answers = revision.plan, revision.answers
                    store.append("plan", revised, role="architect",
                                 model=self._answered_by("architect"),
                                 spec_hash=state.spec_hash, meta={"revision": True})
                except Exception as exc:
                    store.append("plan_check", {"revision_error": f"{type(exc).__name__}: {exc}"[:600]},
                                 role="orchestrator", spec_hash=state.spec_hash)

            outcome = settle_objections(
                objections, answers, lambda o: units_changed(plan, revised, o))
            still_open = [i for i in outcome if not i["settled"]]
            facts = cut_facts(revised, spec, runtime)
            needed = bool(still_open or facts)
            store.append("cut_review", {
                "needed": needed,
                "open": still_open,
                "settled": [i for i in outcome if i["settled"]],
                "facts": facts,
                "units": len(revised.units),
                "seams": len(revised.seams),
                "reason": ("" if needed else
                           f"plan review not needed: {len(revised.units)} unit(s), "
                           f"no open objections, nothing measured"),
            }, role="orchestrator", spec_hash=state.spec_hash)
            phase.detail = (
                f"{len(objections)} objection(s), {len(still_open)} open, {len(facts)} fact(s)"
                + (" -- stopping at plan review" if needed else ""))
        if needed:
            return self._stop_at_cut(store, state)
        return revised

    async def _apply_ruling(self, store, state, spec: Spec, digest: str, plan: Plan,
                            ruling: dict[str, Any]) -> Plan | None:
        """What a person decided at plan review, done.

        Every branch appends a `plan`, which is what marks the ruling spent --
        and what `recorded_attempt` keys this build's artifacts on.
        """
        choice = ruling.get("choice")
        note = (ruling.get("note") or "").strip()
        if choice == "one_piece":
            async with self._phase(store, state, "architect") as phase:
                plan = merge_units(plan)
                store.append("plan", plan, role="orchestrator", spec_hash=state.spec_hash,
                             meta={"ruling": choice})
                phase.detail = "built as one piece at a person's ruling"
        elif choice in ("revise", "alternative"):
            reviews = [r for r in store.records() if r.get("kind") == "cut_review"
                       and r.get("spec_hash") == state.spec_hash]
            review = (reviews[-1]["payload"] if reviews else None) or {}
            objections = [CheckObjection.model_validate(i["objection"])
                          for i in review.get("open") or []]
            architect_prompt = self._architect_prompt(
                spec, digest, self._guidance(store, state, "architect"))
            async with self._phase(store, state, "architect") as phase:
                plan = await self.llm.ask(
                    "architect",
                    architect_prompt
                    + "\n---\n\n# The plan you wrote\n\n" + _json(plan)
                    + "\n\n---\n\n# A person ruled at plan review\n\n"
                    + ("They sided with the plan checker. This is a ruling, not a suggestion: "
                       "change the cut so none of the objections below holds, and so none of "
                       "the measured problems is still true.\n\n"
                       if choice == "revise" else
                       "They sided with neither of you, and proposed a cut of their own. This is "
                       f"a ruling, not a suggestion -- cut it this way:\n\n> {note}\n\n"
                       "What you and the plan checker disagreed about, for context:\n\n")
                    + objection_block(objections)
                    + ("\n\n## Measured problems with the cut\n\n"
                       + "\n".join(f"- {f}" for f in review.get("facts") or [])
                       if review.get("facts") else "")
                    + (f"\n\nWhat they added: {note}" if note and choice == "revise" else "")
                    + "\n\nReturn the whole plan.",
                    Plan, system=self.role_prompt("architect"))
                store.append("plan", plan, role="architect", model=self._answered_by("architect"),
                             spec_hash=state.spec_hash, meta={"ruling": choice})
                phase.detail = ("re-cut as a person proposed" if choice == "alternative"
                                else "re-cut at a person's ruling")
            # A person's ruling settles the argument, not the arithmetic. What
            # code can measure is measured again, and only that can stop it.
            facts = cut_facts(plan, spec, self.project.state.runtime_packages)
            store.append("cut_review", {
                "needed": bool(facts), "open": [], "settled": [], "facts": facts,
                "units": len(plan.units), "seams": len(plan.seams),
                "reason": "" if facts else f"re-cut at a person's ruling ({choice}); nothing measured",
            }, role="orchestrator", spec_hash=state.spec_hash)
            if facts:
                return self._stop_at_cut(store, state)
        else:   # keep
            async with self._phase(store, state, "architect") as phase:
                store.append("plan", plan, role="human", spec_hash=state.spec_hash,
                             meta={"ruling": "keep"})
                phase.detail = f"{len(plan.units)} unit(s), kept at plan review"
        async with self._phase(store, state, "plan_checker") as phase:
            phase.detail = {"keep": "a person sided with the architect",
                            "one_piece": "a person chose one piece",
                            "revise": "a person sided with the plan checker",
                            "alternative": "a person proposed a cut of their own"}.get(choice, "ruled")
        return plan

    def rule_on_cut(self, feature_id: str, choice: str, note: str = "") -> FeatureState:
        """Record a person's ruling at plan review. The build that acts on it is
        started by the caller, the same way approving the spec starts one."""
        if choice not in ("keep", "revise", "alternative", "one_piece", "back_to_spec"):
            raise ProjectError(f"unknown ruling {choice!r}")
        if choice == "alternative" and not note.strip():
            raise ProjectError("an alternative needs saying: write the cut you want the "
                               "architect to make")
        store = self.store_for(feature_id)
        state = self.state_of(store)
        if state.stage != "awaiting_cut_approval":
            raise ProjectError(f"there is no cut to rule on: the feature is {state.stage}")
        store.append("cut_ruling", {"choice": choice, "note": note.strip(), "at": _now()},
                     role="human", spec_hash=state.spec_hash)
        if choice == "back_to_spec":
            # The spec is not voided -- a person may correct it, or approve it
            # again and get a fresh cut. Only the build stops being the next step.
            state.stage = "awaiting_spec_approval"
            state.owner = ""
        else:
            state.stage = "building"
            state.owner = this_process()
        state.error = ""
        self._save(store, state)
        self._emit(state)
        return state
