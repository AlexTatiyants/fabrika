"""A feature from intake to its build: asking again, answering, freezing the spec,
plan review, and starting, resuming or re-measuring a run.
"""

from __future__ import annotations

import logging
from typing import Any

import anyio
from fastapi import BackgroundTasks, FastAPI, HTTPException

from ..pipeline import is_orphaned
from ..projects import ProjectError
from ..schemas import FeatureState

from .bodies import AnswersBody, CorrectionBody
from .context import Context

log = logging.getLogger(__name__)


def register(app: FastAPI, ctx: Context) -> None:
    """Intake, answers, the spec gate, plan review and the runs they start."""
    store_or_404 = ctx.store_or_404
    factory_for = ctx.factory_for
    start_run = ctx.start_run
    start_when_ready = ctx.start_when_ready
    close_preview = ctx.close_preview

    @app.get("/api/projects/{project_id}/features/{feature_id}/intake-plan")
    def intake_plan(project_id: str, feature_id: str) -> dict[str, Any]:
        store_or_404(project_id, feature_id)
        return factory_for(project_id).intake_plan(feature_id)

    @app.post("/api/projects/{project_id}/features/{feature_id}/reintake")
    def rerun_intake(project_id: str, feature_id: str, background: BackgroundTasks) -> dict[str, Any]:
        """Read the repository again, then ask again."""
        store_or_404(project_id, feature_id)

        async def again() -> None:
            try:
                await factory_for(project_id).rerun_intake(feature_id)
            except Exception:
                log.exception("reading the repository again for a feature of %s failed", project_id)

        background.add_task(again)
        return {"queued": True}

    @app.post("/api/projects/{project_id}/features/{feature_id}/correct")
    def correct_interrogation(
        project_id: str, feature_id: str, body: CorrectionBody, background: BackgroundTasks,
    ) -> dict[str, Any]:
        """The restatement is wrong. Ask again, knowing that."""
        store_or_404(project_id, feature_id)

        async def again() -> None:
            try:
                await factory_for(project_id).reinterrogate(feature_id, body.correction.strip())
            except Exception:
                log.exception("asking again for a feature of %s failed", project_id)

        background.add_task(again)
        return {"queued": True}

    @app.delete("/api/projects/{project_id}/features/{feature_id}")
    def delete_feature(
        project_id: str, feature_id: str, delete_branch: bool = False,
    ) -> dict[str, Any]:
        store_or_404(project_id, feature_id)
        anyio.from_thread.run(close_preview, project_id, feature_id, "the feature being deleted")
        try:
            return factory_for(project_id).delete_feature(
                feature_id, delete_branch=delete_branch
            )
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @app.post("/api/projects/{project_id}/features/{feature_id}/answers")
    def post_answers(
        project_id: str, feature_id: str, body: AnswersBody, background: BackgroundTasks,
    ) -> dict[str, Any]:
        """Records the answers, then plans in the background.

        Recording first is the point: the spec writer takes minutes, and until it
        returned there was no trace anywhere of what the human had typed.
        """
        store_or_404(project_id, feature_id)
        factory = factory_for(project_id)
        resolved = factory.record_answers(feature_id, body.answers, body.deferred)

        async def plan() -> None:
            try:
                await factory_for(project_id).finalize_spec(feature_id)
            except Exception:
                log.exception("writing the spec for a feature of %s failed", project_id)

        background.add_task(plan)
        return {"recorded": len(resolved), "stage": "writing_spec"}

    @app.post("/api/projects/{project_id}/features/{feature_id}/approve-spec")
    async def approve_spec(project_id: str, feature_id: str, background: BackgroundTasks,
                           when: str = "now") -> dict[str, Any]:
        """Freeze the spec, and start the build now or when a plan can carry it.

        `when` is the human's answer to what the gate told them. The console
        shows the reading before the button is pressed -- what each plan has
        left against what a run has drawn -- so "start at 14:05" is a choice
        made with the consequence in front of it, which is what gate 1 is for.
        """
        store_or_404(project_id, feature_id)
        factory = factory_for(project_id)

        if when == "at_reset":
            verdict = await factory.plan_verdict()
            if verdict.verdict == "wait" and verdict.start_at:
                state = factory.defer_build(feature_id, verdict)
                background.add_task(
                    start_when_ready, project_id, feature_id, verdict.start_at)
                return state.model_dump(mode="json")
            # Nothing to wait for any more -- the window reset while the page
            # was open, or another run freed the room. Start rather than
            # inventing a delay that no reading supports.

        state = factory.approve_spec(feature_id)
        start_run(background, project_id, feature_id, lambda f: f.run_build(feature_id))
        return state.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/features/{feature_id}/cut-ruling")
    async def rule_on_cut(project_id: str, feature_id: str, body: dict[str, Any],
                          background: BackgroundTasks) -> dict[str, Any]:
        """Plan review: a person rules between the architect and the plan checker.

        `choice` is keep (agree with the architect), revise (agree with the
        checker) or alternative (the person's own cut, in `note`). one_piece and
        back_to_spec are accepted too, though the screen does not offer
        them. Every one but back_to_spec starts the build that acts on it --
        resuming, so the plan the gate stopped on is the one the ruling applies to.
        """
        store_or_404(project_id, feature_id)
        factory = factory_for(project_id)
        try:
            state = factory.rule_on_cut(feature_id, str(body.get("choice") or ""),
                                        str(body.get("note") or ""))
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        if state.stage == "building":
            start_run(background, project_id, feature_id,
                      lambda f: f.run_build(feature_id, resume=True))
        return state.model_dump(mode="json")

    @app.get("/api/projects/{project_id}/features/{feature_id}/plan-check")
    async def plan_check(project_id: str, feature_id: str) -> dict[str, Any]:
        """Can the plans carry this run, asked before the gate is pressed.

        Costs at most one probe on a route whose gauge cannot be read for free
        and has gone stale -- the same escalation the crew page makes, for the
        same reason. Everything else is read off disk.
        """
        store_or_404(project_id, feature_id)
        verdict = await factory_for(project_id).plan_verdict()
        return verdict.as_dict()

    @app.post("/api/projects/{project_id}/features/{feature_id}/cancel-wait")
    async def cancel_wait(project_id: str, feature_id: str) -> dict[str, Any]:
        """Stop waiting for the window. The spec stays frozen."""
        store_or_404(project_id, feature_id)
        return factory_for(project_id).cancel_deferral(feature_id).model_dump(mode="json")

    @app.post("/api/projects/{project_id}/features/{feature_id}/retry-build")
    async def retry_build(project_id: str, feature_id: str,
                          background: BackgroundTasks) -> dict[str, Any]:
        """Pick a failed build back up rather than paying for all of it again."""
        store = store_or_404(project_id, feature_id)
        state = FeatureState.model_validate(store.payload("state"))
        if state.stage not in ("failed", "building"):
            raise HTTPException(
                status_code=409,
                detail=f"feature is {state.stage}, not a build that stopped")
        # `building` is accepted for the run a crash left behind, and only that:
        # the same question `rebuild` asks, of the operating system.
        if state.stage == "building" and not is_orphaned(state):
            raise HTTPException(status_code=409, detail="feature is already building")
        start_run(background, project_id, feature_id,
                  lambda f: f.run_build(feature_id, resume=True))
        return {"resuming": True, "feature_id": feature_id}

    @app.post("/api/projects/{project_id}/features/{feature_id}/revalidate")
    async def revalidate(project_id: str, feature_id: str,
                         background: BackgroundTasks) -> dict[str, Any]:
        """Measure this branch again from zero, keeping the code as it stands.

        Distinct from `dispatch`, which acts on what a human flagged in the
        work. This one says nothing about the work: it says the last verdict
        pass was measuring the harness, and asks for the same branch to be
        measured and reviewed again from nothing. No route parameter for the
        same reason dispatch has none -- there is only one thing this can mean.
        """
        store_or_404(project_id, feature_id)
        factory = factory_for(project_id)
        # Inline, so the caller learns now rather than from a phase that fails
        # in the background five seconds later.
        state = FeatureState.model_validate(store_or_404(project_id, feature_id).payload("state"))
        if state.stage != "awaiting_verdict":
            raise HTTPException(
                status_code=409,
                detail=f"feature is {state.stage}, not a packet awaiting your ruling")

        start_run(background, project_id, feature_id, lambda f: f.revalidate(feature_id))
        await close_preview(project_id, feature_id, "measuring it again")
        return {"revalidating": True, "feature_id": feature_id,
                "budget": factory.rework_budget(feature_id)}
