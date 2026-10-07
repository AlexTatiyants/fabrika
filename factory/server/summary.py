"""What a project is doing and what has happened to it, computed from its record:
the overview and the history.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from ..pipeline import current_payload
from ..schemas import FeatureState

from .context import Context


def register(app: FastAPI, ctx: Context) -> None:
    """The overview and the history."""
    cfg = ctx.cfg
    project_or_404 = ctx.project_or_404
    factory_for = ctx.factory_for

    @app.get("/api/projects/{project_id}/overview")
    def project_overview(project_id: str) -> dict[str, Any]:
        """Every feature that needs a person, what is building, what finished,
        and what the project has cost -- computed from the record, no model.
        See `factory/overview.py`."""
        from .. import overview

        project = project_or_404(project_id)
        factory = factory_for(project_id)
        waiting, building, finished = [], [], []
        billed = notional = 0.0
        tokens: dict[str, dict[str, int]] = {}
        billed_routes = {name for name, route in cfg.routes.items() if route.billed}
        for feature_id in project.feature_ids():
            store = project.feature_store(feature_id)
            if not store.has("state"):
                continue
            state = FeatureState.model_validate(store.payload("state"))
            records = store.records()
            packet = current_payload(records, "packet") if state.stage in (
                "awaiting_verdict", "accepted", "rejected") else None
            behind = (overview.behind_main(project.state.repo, project.state.base_ref,
                                           f"factory/{feature_id}")
                      if state.stage in overview.WAITING_ON_YOU else None)
            row = overview.feature_summary(state, packet, factory.flags(feature_id), records, behind,
                                           billed_routes)
            billed += row["spend"]["billed"]
            notional += row["spend"]["notional"]
            for route, split in row["spend"]["tokens"].items():
                acc = tokens.setdefault(route, {"subscription": 0, "charged": 0})
                acc["subscription"] += split["subscription"]
                acc["charged"] += split["charged"]
            if state.stage in overview.WAITING_ON_YOU:
                waiting.append(row)
            elif state.stage in overview.IN_FLIGHT:
                building.append(row)
            elif state.stage in overview.FINISHED:
                finished.append(row)
        finished.sort(key=lambda f: f.get("since") or "", reverse=True)
        return {"waiting": overview.order_waiting(waiting), "building": building,
                "finished": finished[:5], "finished_total": len(finished),
                "spend": {"billed": round(billed, 2), "notional": round(notional, 2),
                          "tokens": tokens}}

    @app.get("/api/projects/{project_id}/history")
    def project_history(project_id: str) -> dict[str, Any]:
        """What happened to this project, in words, and who did it -- read from
        its ledger and its features' own. See `factory/history.py`; the raw
        ledger is still `/log`."""
        from .. import history

        project = project_or_404(project_id)
        features = []
        for feature_id in project.feature_ids():
            store = project.feature_store(feature_id)
            if not store.has("state"):
                continue
            state = FeatureState.model_validate(store.payload("state"))
            features.append({
                "feature_id": feature_id, "title": state.title or feature_id,
                "created_at": state.created_at, "stage": state.stage,
                "records": [r for r in store.records() if r.get("kind") in ("packet", "verdict")],
            })
        return history.project_history(list(project.store), features)
