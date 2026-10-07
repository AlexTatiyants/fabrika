"""The running app, at review: a feature's preview opened, read and closed."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from ..preview import PreviewError
from ..sandbox import Sandbox
from ..schemas import FeatureState

from .context import Context


def register(app: FastAPI, ctx: Context) -> None:
    """Opening, reading and stopping a feature's preview."""
    previews = ctx.previews
    project_or_404 = ctx.project_or_404
    store_or_404 = ctx.store_or_404

    # ================================================================
    # the running app, at review
    # ================================================================

    @app.post("/api/projects/{project_id}/features/{feature_id}/preview")
    async def open_preview(project_id: str, feature_id: str) -> dict[str, Any]:
        """Start the feature's app for a person to look at. Returns at once; the
        screen follows it on the event stream."""
        project = project_or_404(project_id)
        store = store_or_404(project_id, feature_id)
        state = FeatureState.model_validate(store.payload("state"))
        path = (Sandbox.reopen(state.sandbox, project.repo_path).path
                if state.sandbox else None)
        try:
            await previews.start(project, feature_id, state.stage, path, store)
        except PreviewError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return previews.describe(project, feature_id, state.stage, store)

    @app.get("/api/projects/{project_id}/features/{feature_id}/preview")
    def preview_state(project_id: str, feature_id: str) -> dict[str, Any]:
        project = project_or_404(project_id)
        store = store_or_404(project_id, feature_id)
        state = FeatureState.model_validate(store.payload("state"))
        return previews.describe(project, feature_id, state.stage, store, touch=True)

    @app.delete("/api/projects/{project_id}/features/{feature_id}/preview")
    async def stop_preview(project_id: str, feature_id: str) -> dict[str, Any]:
        project = project_or_404(project_id)
        store = store_or_404(project_id, feature_id)
        state = FeatureState.model_validate(store.payload("state"))
        await previews.stop(project_id, feature_id, by="you", store=store, stage=state.stage)
        return previews.describe(project, feature_id, state.stage, store)
