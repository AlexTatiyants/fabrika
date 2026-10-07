"""The as-built: the codebase as it was built, for whoever answers for it."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException

from .. import asbuilt
from ..asbuilt import ProjectAsBuilt
from ..asbuilt_graph import SystemChange

from .context import Context

log = logging.getLogger(__name__)


def _system_change_view(payload: Any) -> dict[str, Any] | None:
    if not payload:
        return None
    try:
        change = SystemChange.model_validate(payload)
    except ValueError:
        return None
    return {"base": change.base, "head": change.head, "lines": change.lines()}


def register(app: FastAPI, ctx: Context) -> None:
    """The as-built's status, graph and source, and a reading started on a press."""
    cfg = ctx.cfg
    hub = ctx.hub
    registry = ctx.registry
    llm = ctx.llm
    project_or_404 = ctx.project_or_404

    # ================================================================
    # as-built -- the codebase as it was built, for whoever answers for it
    # ================================================================

    def as_built() -> ProjectAsBuilt:
        return ProjectAsBuilt(cfg, llm, on_progress=hub.publish)

    @app.get("/api/projects/{project_id}/as-built")
    def as_built_status(project_id: str) -> dict[str, Any]:
        """Whether the committed as-built describes what is checked out, and
        whether a reading is running now."""
        project = project_or_404(project_id)
        out = asbuilt.status(project.repo_path, "HEAD")
        out["running"] = ProjectAsBuilt.running.get(project_id)
        out["dir"] = asbuilt.AS_BUILT_DIR
        ok, failed = project.store.latest("as_built"), project.store.latest("as_built_failed")
        out["error"] = (failed["payload"].get("error", "") if failed and (not ok or failed["seq"] > ok["seq"])
                        else "")
        out["last"] = ok["payload"] if ok else None
        out["uncommitted"] = asbuilt.uncommitted_as_built(project.repo_path)[:20]
        # What is read and kept but not yet committed -- a reading that paused.
        try:
            out.update(asbuilt.cache_coverage(project.repo_path, project.store.artifact_dir("as-built-cache"),
                                              asbuilt.reader_contract(cfg.role_prompt("reader"))))
        except Exception:
            out.update({"kept": 0, "listed": 0})
        return out

    @app.get("/api/projects/{project_id}/as-built/graph")
    def as_built_graph(project_id: str) -> dict[str, Any]:
        """The committed as-built, as data -- what the console draws."""
        project = project_or_404(project_id)
        reading, _ = asbuilt.load_committed(project.repo_path, "HEAD", with_records=False)
        if reading is None:
            raise HTTPException(status_code=404, detail=f"{asbuilt.AS_BUILT_DIR} is not committed in this project")
        return reading.model_dump(mode="json")

    @app.get("/api/projects/{project_id}/as-built/source")
    def as_built_source(project_id: str, path: str) -> dict[str, Any]:
        """One file the reading read, as it read it, with what it knows about
        each line -- only a file the reading has, never an arbitrary path."""
        project = project_or_404(project_id)
        try:
            view = asbuilt.source_view(project.repo_path, path)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"the as-built has no file {path}") from None
        if view is None:
            raise HTTPException(status_code=404, detail=f"{asbuilt.AS_BUILT_DIR} is not committed in this project")
        return view

    @app.post("/api/projects/{project_id}/as-built/refresh")
    def refresh_as_built(project_id: str, background: BackgroundTasks) -> dict[str, Any]:
        """Read what is checked out and commit its as-built -- on a person's press."""
        project = project_or_404(project_id)
        if ProjectAsBuilt.running.get(project_id):
            raise HTTPException(status_code=409, detail="a reading of this project is already running")
        dirty = asbuilt.uncommitted_as_built(project.repo_path)
        if dirty:
            raise HTTPException(status_code=409, detail=(
                f"{asbuilt.AS_BUILT_DIR}/ has changes that are not committed ({dirty[0]}). "
                "Commit or discard them first, so nothing of yours is overwritten."))
        ProjectAsBuilt.running[project_id] = {"phase": "starting", "detail": "", "at": ""}

        async def run() -> None:
            ProjectAsBuilt.running.pop(project_id, None)
            try:
                await as_built().refresh(registry.get(project_id))
            except Exception:
                log.exception("reading the as-built of %s failed", project_id)

        background.add_task(run)
        return {"started": True}
