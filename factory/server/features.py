"""Features: every one, a project's, starting one, and everything the review
screens read about one.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException

from ..pipeline import current_payload, current_record, Factory, is_orphaned, PHASE_NAMES
from ..projects import ProjectError
from ..sandbox import Sandbox
from ..schemas import FeatureState
from ..workspace import unchecked_levels

from .as_built import _system_change_view
from .bodies import NewFeature
from .context import Context

log = logging.getLogger(__name__)


def register(app: FastAPI, ctx: Context) -> None:
    """Listing, creating and reading features."""
    cfg = ctx.cfg
    registry = ctx.registry
    previews = ctx.previews
    llm = ctx.llm
    project_or_404 = ctx.project_or_404
    store_or_404 = ctx.store_or_404
    factory_for = ctx.factory_for

    # ================================================================
    # features
    # ================================================================

    @app.get("/api/features")
    def all_features() -> list[dict[str, Any]]:
        """Every feature in every project, newest first."""
        out: list[dict[str, Any]] = []
        for project in registry.list():
            out.extend(Factory(cfg, project, llm=llm).features())
        out.sort(key=lambda f: f["updated_at"], reverse=True)
        return out

    @app.get("/api/projects/{project_id}/features")
    def list_features(project_id: str) -> list[dict[str, Any]]:
        return factory_for(project_id).features()

    @app.post("/api/projects/{project_id}/features")
    def create_feature(project_id: str, body: NewFeature, background: BackgroundTasks) -> dict[str, Any]:
        """Returns as soon as the feature and its sandbox exist.

        The scout and the interrogator take minutes on a real repository, and a
        POST that hangs that long is a POST people reload.
        """
        factory = factory_for(project_id)
        try:
            state = factory.create_feature(
                body.intent, body.title, allow_dirty=body.allow_dirty)
        except ProjectError as exc:
            raise HTTPException(
                status_code=409,
                detail={"message": str(exc), "drift": factory.working_tree_drift()},
            ) from None

        async def intake() -> None:
            try:
                await factory_for(project_id).run_intake(state.feature_id)
            except Exception:  # recorded into the failed phase, never silent
                log.exception("intake for a feature of %s failed", project_id)

        background.add_task(intake)
        return state.model_dump(mode="json")

    @app.get("/api/projects/{project_id}/features/{feature_id}")
    def get_feature(project_id: str, feature_id: str) -> dict[str, Any]:
        project = project_or_404(project_id)
        store = store_or_404(project_id, feature_id)
        state = FeatureState.model_validate(store.payload("state"))
        records = store.records()
        factory = factory_for(project_id)
        flags = factory.flags(feature_id)
        rework = factory.rework_plan(feature_id)
        return {
            "project": project.state.model_dump(mode="json"),
            "state": state.model_dump(mode="json"),
            # Asked of the operating system rather than of `stage`. A server
            # killed mid-build leaves "building" behind with nobody building,
            # and the screen that reads the stage alone spins on a run that
            # ended hours ago -- promising a question nothing will ever ask.
            "orphaned": is_orphaned(state),
            "sandbox": state.sandbox.model_dump(mode="json") if state.sandbox else None,
            # In the order the phases run, whatever order this feature stored
            # them in. A feature keeps the list it was created with, so one
            # started before the order was corrected would otherwise go on
            # drawing the breaker beneath two phases it runs before.
            "phases": [
                {**p.model_dump(mode="json"), "elapsed_s": round(p.elapsed_s, 1)}
                for p in sorted(state.phases, key=lambda p: (
                    PHASE_NAMES.index(p.name) if p.name in PHASE_NAMES else len(PHASE_NAMES)))
            ],
            "digest": store.payload("digest"),
            "drift": store.payload("drift"),
            "scout": store.payload("scout"),
            "interrogation": store.payload("interrogation"),
            # A correction voids what was built on the reading it corrected. The
            # records stay -- the ledger is append-only -- but they are not current.
            "answers": current_payload(records, "answers"),
            "corrections": store.payloads("correction"),
            "spec": current_payload(records, "spec"),
            # What this project cannot verify about the spec in front of you.
            # Carried beside the spec because that is the decision it bears on:
            # the same facts reaching the packet instead arrive after the choice
            # they should have informed.
            "spec_testability": current_payload(records, "spec_testability") or [],
            "unchecked_levels": unchecked_levels(project.state),
            # Read before the build spent anything, so it is readable while the
            # run it describes is still going.
            "attribution_wiring": current_payload(records, "attribution_wiring") or [],
            # Which model planned it -- the spec is agent-authored, and a reader
            # deciding whether to trust it wants to know that up front, not by
            # digging through the ledger.
            "spec_model": (current_record(records, "spec") or {}).get("model", ""),
            "plan": store.payload("plan"),
            # What the two checkers said, and what plan review is waiting on. The
            # plan review screen is drawn from the last `cut_review`; the plan it
            # argues about is the last `plan`, which is the revised one.
            "spec_check": store.payload("spec_check"),
            "plan_check": store.payload("plan_check"),
            "cut_review": store.payload("cut_review"),
            "cut_ruling": store.payload("cut_ruling"),
            "workers": store.payloads("worker"),
            "integration": store.payload("integration"),
            "oracle": store.payload("oracle"),
            "gates": store.payload("gates"),
            "qa": store.payload("qa"),
            "trace": store.payload("trace"),
            "reviews": [
                {"role": r["role"], "model": r["model"],
                 "samples": (r.get("meta") or {}).get("samples", 1),
                 # Which pass this was. The panel runs again after every repair
                 # round, so a role has as many readings as there were rounds --
                 # and only its last one is about the tree that is shipping.
                 # Recorded since the loop was written and never served.
                 "round": (r.get("meta") or {}).get("round", 0),
                 "report": r["payload"]}
                for r in store.all("review")
            ],
            # Served because the evidence screen shows it. Absent, that screen
            # rendered "nothing failing", which is a false green about the one
            # agent whose passing result is meant to prove nothing.
            "breaker": store.payload("breaker"),
            "breaker_suite": store.payload("breaker_suite"),
            # Every package this feature added or moved, read from its
            # lockfiles and looked up. Null until the first round has run --
            # which the Dependencies tab says, rather than showing none.
            "dependencies": store.payload("dependencies"),
            "packet": store.payload("packet"),
            # What the feature changed in the system, in sentences -- from its
            # as-built at the base and at the head. Null until both are read.
            "system_change": _system_change_view(store.payload("system_change")),
            "writes": store.payload("writes"),
            # The command a human types to see the whole change. Derived from
            # the sandbox rather than read off a `writes` record: the build
            # writes one carrying it, every repair round writes another that
            # does not, and `payload` returns the last -- so on any feature the
            # loop touched, the console fell back to a bare `git diff`, which
            # shows the working tree and not this branch. One definition,
            # `Sandbox.diff_command`, and it is this one.
            "diff_command": (
                Sandbox.reopen(state.sandbox, project.repo_path).diff_command()
                if state.sandbox else ""
            ),
            "usage": store.payload("usage"),
            "rulings": store.payloads("ruling"),
            "verdict": store.payload("verdict"),
            "flags": [f.model_dump(mode="json") for f in flags],
            "rework": rework.model_dump(mode="json"),
            "budget": factory.rework_budget(feature_id),
            # The running app, if a person can open one, and the one they have.
            # Asking is what keeps an open preview from closing as idle: the
            # review screen reads this while it is on the screen.
            "preview": previews.describe(project, feature_id, state.stage, store, touch=True),
        }
