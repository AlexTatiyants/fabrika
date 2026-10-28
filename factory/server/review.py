"""Gate 2: rebuilding, ruling on decisions, one file's diff, flags and where they
send the work, and the verdict.
"""

from __future__ import annotations

import logging
from typing import Any

import anyio
from fastapi import BackgroundTasks, FastAPI, HTTPException

from ..pipeline import is_orphaned
from ..projects import ProjectError
from ..pull_requests import pr_target
from ..sandbox import SandboxError
from ..schemas import FeatureState

from .bodies import DISPOSITIONS, FlagBody, RetagBody, RulingBody, VerdictBody
from .context import Context

log = logging.getLogger(__name__)

#: Features whose pull request is being opened now, as (project, feature).
_OPENING: set[tuple[str, str]] = set()


def register(app: FastAPI, ctx: Context) -> None:
    """What a person does with a packet awaiting their ruling."""
    store_or_404 = ctx.store_or_404
    factory_for = ctx.factory_for
    start_run = ctx.start_run
    close_preview = ctx.close_preview

    @app.post("/api/projects/{project_id}/features/{feature_id}/rebuild")
    async def rebuild(project_id: str, feature_id: str,
                      background: BackgroundTasks,
                      reset_branch: bool = True) -> dict[str, Any]:
        """Build the units again over the spec this human already froze.

        The mirror of `revalidate`. That one keeps the build and re-measures it,
        for a packet that judged the harness. This replaces the build and keeps
        the spec, for a build that *was* the harness -- units whose coding
        harness edited nothing, blind tests that landed in the project's own
        test tree, a plan written before the architect was asked for a reading
        list. Gate 1 is not reopened either way.

        `reset_branch=false` builds over the commits already on the branch. It
        is available because a human may have looked at that code and decided it
        is worth keeping; it is not the default, because building over a
        half-finished attempt gives the next reader a diff that is two attempts
        deep and says so nowhere.
        """
        store_or_404(project_id, feature_id)
        factory = factory_for(project_id)
        state = FeatureState.model_validate(store_or_404(project_id, feature_id).payload("state"))
        if state.stage in ("intake", "awaiting_answers", "writing_spec", "awaiting_spec_approval"):
            raise HTTPException(
                status_code=409,
                detail=(f"feature is {state.stage}: there is no build to replace. A spec that "
                        "has not been frozen is changed at gate 1, not by rebuilding."))
        # A build that is genuinely running, not one that says it is.
        #
        # This asked the stage and nothing else, and the stage is the field a
        # crash is the reason nobody updated. A server restarted mid-run leaves
        # `building` behind with a dead pid on it, and every rebuild after that
        # is refused for a run that stopped hours ago -- the feature is locked
        # by the record of the thing that broke it, and there is no way out of
        # it through the API at all. `is_orphaned` asks the operating system,
        # which is the only party that knows, and rebuilding is precisely the
        # remedy for what it finds.
        if state.stage == "building" and not is_orphaned(state):
            raise HTTPException(
                status_code=409, detail="feature is already building")

        start_run(background, project_id, feature_id,
                  lambda f: f.rebuild(feature_id, reset_branch=reset_branch))
        await close_preview(project_id, feature_id, "a rebuild")
        return {"rebuilding": True, "feature_id": feature_id,
                "reset_branch": reset_branch,
                "budget": factory.rework_budget(feature_id)}

    @app.post("/api/projects/{project_id}/features/{feature_id}/rulings")
    def post_ruling(project_id: str, feature_id: str, body: RulingBody) -> dict[str, Any]:
        store_or_404(project_id, feature_id)
        if body.ruling not in ("accept", "send_back"):
            raise HTTPException(status_code=422, detail="ruling must be 'accept' or 'send_back'")
        record = factory_for(project_id).record_ruling(
            feature_id, body.decision_id, body.ruling, body.note
        )
        return {"recorded": record["payload"], "seq": record["seq"]}

    @app.get("/api/projects/{project_id}/features/{feature_id}/diff")
    def get_file_diff(project_id: str, feature_id: str, path: str) -> dict[str, Any]:
        """What this feature did to one file, as a patch.

        Files alone never show changes. An agent returns
        whole files -- `FileWrite.contents` is complete, never a fragment -- so
        a screen built from what the agents said can only ever show the result,
        and "418 lines" reads the same whether they are new or were already
        there. The repository is the only thing that knows the difference, so
        this asks it. (Same principle as `Sandbox.show`.)

        One path per call, because the screen opens one row at a time and a
        whole-branch patch is megabytes nobody reads. A problem is a sentence
        rather than a status: a review that 500s because one file was renamed
        is worse than one that says so in the row.
        """
        store = store_or_404(project_id, feature_id)
        state = FeatureState.model_validate(store.payload("state"))
        try:
            sandbox = factory_for(project_id).open_sandbox(state)
        except SandboxError as exc:
            return {"path": path, "diff": "", "problem": str(exc), "editor": ""}
        diff, problem = sandbox.file_diff(path)
        absolute = ""
        if not problem and sandbox.exists():
            absolute = str(sandbox.path / path)
        return {
            "path": path,
            "diff": diff,
            "problem": problem,
            # Where this file actually is, so the console can build the editor
            # link. Absent when there is no checkout, which is also when the
            # link would open nothing.
            "absolute": absolute,
            "base": state.sandbox.base_sha if state.sandbox else "",
            "head": sandbox.head() if sandbox.exists() else "",
        }

    @app.get("/api/projects/{project_id}/features/{feature_id}/rework")
    def get_rework(project_id: str, feature_id: str) -> dict[str, Any]:
        """What this human flagged, and where that sends the review."""
        store_or_404(project_id, feature_id)
        factory = factory_for(project_id)
        return {
            "flags": [f.model_dump(mode="json") for f in factory.flags(feature_id)],
            "plan": factory.rework_plan(feature_id).model_dump(mode="json"),
            "budget": factory.rework_budget(feature_id),
        }

    @app.post("/api/projects/{project_id}/features/{feature_id}/flags")
    def post_flag(project_id: str, feature_id: str, body: FlagBody) -> dict[str, Any]:
        store_or_404(project_id, feature_id)
        if body.disposition not in DISPOSITIONS:
            raise HTTPException(status_code=422,
                                detail=f"disposition must be one of {sorted(DISPOSITIONS)}")
        factory = factory_for(project_id)
        flag = factory.record_flag(
            feature_id, body.text, source=body.source, anchor=body.anchor,
            disposition=body.disposition, severity=body.severity,
        )
        return {"flag": flag.model_dump(mode="json"),
                "plan": factory.rework_plan(feature_id).model_dump(mode="json")}

    @app.post("/api/projects/{project_id}/features/{feature_id}/flags/{flag_id}")
    def retag_flag(project_id: str, feature_id: str, flag_id: str,
                   body: RetagBody) -> dict[str, Any]:
        """Retag one flag. The route recomputes; nothing is edited."""
        store_or_404(project_id, feature_id)
        if body.disposition not in DISPOSITIONS:
            raise HTTPException(status_code=422,
                                detail=f"disposition must be one of {sorted(DISPOSITIONS)}")
        try:
            plan = factory_for(project_id).retag_flag(feature_id, flag_id, body.disposition)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"plan": plan.model_dump(mode="json")}

    @app.post("/api/projects/{project_id}/features/{feature_id}/dispatch")
    async def dispatch(project_id: str, feature_id: str,
                       background: BackgroundTasks) -> dict[str, Any]:
        """Act on a gate-2 review, wherever it computed itself to.

        There is deliberately no route parameter. The destination came from what
        the human tagged, and accepting one here would be the one way to reach a
        place the flags do not support. (INV-6)
        """
        store_or_404(project_id, feature_id)
        factory = factory_for(project_id)
        plan = factory.rework_plan(feature_id)
        if plan.route == "accept":
            raise HTTPException(
                status_code=409,
                detail="nothing flagged needs work; underwrite or drop the feature")
        # Guards run inline so a caller learns now, not from a phase that fails
        # in the background five seconds later.
        try:
            factory._dispatchable(feature_id, plan.route)
            if plan.route == "repair" and factory.rework_budget(feature_id)["exhausted"]:
                budget = factory.rework_budget(feature_id)
                raise ProjectError(
                    f"this feature has spent its rework budget: ${budget['spent_usd']:.2f} of "
                    f"${budget['spendable_usd']:.2f} spendable")
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        start_run(background, project_id, feature_id,
                  lambda f: (f.send_to_repair(feature_id) if plan.route == "repair"
                             else f.reopen_spec(feature_id)),
                  rebaseline=False)
        await close_preview(project_id, feature_id, "sending it for more work")
        return {"dispatched": plan.route, "plan": plan.model_dump(mode="json"),
                "budget": factory.rework_budget(feature_id)}

    @app.post("/api/projects/{project_id}/features/{feature_id}/verdict")
    def post_verdict(project_id: str, feature_id: str, body: VerdictBody,
                     background: BackgroundTasks) -> dict[str, Any]:
        store_or_404(project_id, feature_id)
        if body.verdict not in ("accepted", "rejected"):
            raise HTTPException(status_code=422, detail="verdict must be 'accepted' or 'rejected'")
        anyio.from_thread.run(close_preview, project_id, feature_id, "your verdict")
        state = factory_for(project_id).record_verdict(feature_id, body.verdict, body.note)
        # Accepted is underwritten, and what is underwritten ends as a pull
        # request -- where the project says they go, and nowhere when it says
        # nowhere: the screen then says what to set.
        if body.verdict == "accepted" and not isinstance(pr_target(ctx.project_or_404(project_id)), str):
            open_pr_later(background, project_id, feature_id)
        return state.model_dump(mode="json")

    def open_pr_later(background: BackgroundTasks, project_id: str, feature_id: str) -> None:
        async def run() -> None:
            _OPENING.add((project_id, feature_id))
            try:
                await factory_for(project_id).open_pull_request(feature_id)
            except Exception:  # recorded as pull_request_failed, and said on the screen
                log.warning("the pull request for %s/%s was not opened", project_id, feature_id, exc_info=True)
            finally:
                _OPENING.discard((project_id, feature_id))
        background.add_task(run)

    @app.post("/api/projects/{project_id}/features/{feature_id}/pull-request")
    def post_pull_request(project_id: str, feature_id: str, background: BackgroundTasks) -> dict[str, Any]:
        """Open the accepted feature's pull request: again after it failed, or
        now that the project says where they go."""
        store = store_or_404(project_id, feature_id)
        if (store.payload("state") or {}).get("stage") != "accepted":
            raise HTTPException(status_code=409, detail="Only an underwritten feature is opened as a pull request.")
        target = pr_target(ctx.project_or_404(project_id))
        if isinstance(target, str):
            raise HTTPException(status_code=409, detail=target)
        if (project_id, feature_id) in _OPENING:
            raise HTTPException(status_code=409, detail="Its pull request is being opened.")
        open_pr_later(background, project_id, feature_id)
        return {"started": True}
