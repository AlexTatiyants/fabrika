"""Measuring and reading a project again: a level's cleanup fix applied from the
reading in force, the baseline run again, a fresh survey.
"""

from __future__ import annotations

import logging
import types
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException

from ..projects import cleanup_option_problems, ProjectError, proposed_scaffolding
from ..workspace import PathEscape

from .bodies import CleanupPick
from .context import Context

log = logging.getLogger(__name__)


def register(app: FastAPI, ctx: Context) -> None:
    """Cleanup, baseline and resurvey."""
    registry = ctx.registry
    project_or_404 = ctx.project_or_404
    onboarding = ctx.onboarding

    @app.post("/api/projects/{project_id}/cleanup")
    def apply_cleanup(project_id: str, body: CleanupPick) -> dict[str, Any]:
        """Apply one of a level's cleanup fixes, from the reading in force.

        A first survey records its fixes on the project and had nowhere to pick
        one: the choice lived only on a re-survey's proposal. Same rules as
        there -- applied whole, files first, never a prompt-only fix, and never
        a file the write would refuse -- and the level is tested from here on.
        """
        project = project_or_404(project_id)
        tier = project.state.testing.tier(body.tier)
        if tier is None:
            raise HTTPException(status_code=404, detail=f"no {body.tier} level in this reading")
        declined = [lv for lv in project.state.cleanup_declined if lv != body.tier]
        if body.reopen or body.option is None:
            # A decision, not an absence of one: the page stops asking about a
            # level whose fixes a person saw and turned down.
            if body.option is None and not body.reopen:
                declined.append(body.tier)
            project = registry.update(project, {"cleanup_declined": declined})
            return {"project": project.state.model_dump(mode="json"), "written": {}}
        if not 0 <= body.option < len(tier.cleanup_options):
            raise HTTPException(status_code=422, detail=f"there is no option {body.option} for {body.tier} tests")
        option = tier.cleanup_options[body.option]
        if not (option.files or option.environment):
            raise HTTPException(status_code=422, detail=(
                f"{option.title!r} is done with a coding agent, not applied here: copy its prompt, "
                "then re-survey"))
        source = types.SimpleNamespace(scaffolding=list(proposed_scaffolding(project).values()),
                                       environment=project.environment, recommendations=[])
        problem = cleanup_option_problems(source, option)
        if problem:
            raise HTTPException(status_code=422, detail=f"{option.title!r} cannot be applied whole: {problem}")
        written: dict[str, Any] = {}
        try:
            if option.files:
                existing = [f for f in option.files if (Path(project.state.repo) / f).exists()]
                written = registry.apply_scaffolding(project, list(option.files), replace=existing)
                project = registry.get(project_id)
            project = registry.update(project, {"cleanup_applied": {
                **project.state.cleanup_applied, body.tier: option.title},
                "cleanup_declined": declined})
        except (ProjectError, PathEscape) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {"project": project.state.model_dump(mode="json"), "written": written}

    @app.post("/api/projects/{project_id}/baseline")
    def rerun_baseline(project_id: str, background: BackgroundTasks) -> dict[str, Any]:
        project = project_or_404(project_id)

        async def run() -> None:
            try:
                await onboarding().run_baseline(registry.get(project_id))
            except Exception:
                log.exception("the baseline of %s failed", project_id)

        background.add_task(run)
        return project.state.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/resurvey")
    def resurvey_project(project_id: str, background: BackgroundTasks) -> dict[str, Any]:
        project = project_or_404(project_id)

        async def survey() -> None:
            try:
                await onboarding().run_survey(registry.get(project_id))
            except Exception:
                log.exception("the re-survey of %s failed", project_id)

        background.add_task(survey)
        return project.state.model_dump(mode="json")
