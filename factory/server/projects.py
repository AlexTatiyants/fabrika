"""Registering a repository, and writing what a person chose into it."""

from __future__ import annotations

import difflib
import logging
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException

from .. import dependencies
from ..pipeline import Factory
from ..projects import (dockerfile_state, ProjectError, proposed_scaffolding, scaffolding_state,
                        unmet_capabilities)
from ..schemas import reading_behind
from ..workspace import PathEscape, safe_join, unchecked_levels

from .bodies import NewProject, ScaffoldBody
from .context import Context
from .guides_view import guide_contradictions, guide_offers, guide_view

log = logging.getLogger(__name__)


def register(app: FastAPI, ctx: Context) -> None:
    """Projects: listing, registering, removing, and scaffolding written on a press."""
    cfg = ctx.cfg
    registry = ctx.registry
    llm = ctx.llm
    project_or_404 = ctx.project_or_404
    onboarding = ctx.onboarding

    # ================================================================
    # projects -- gate 0
    # ================================================================

    @app.get("/api/projects")
    def list_projects() -> list[dict[str, Any]]:
        out = []
        for project in registry.list():
            out.append({
                **project.state.model_dump(mode="json"),
                "feature_count": len(project.feature_ids()),
            })
        return out

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, Any]:
        project = project_or_404(project_id)
        store = project.store
        return {
            # `survey` and `baseline` live on the project state and are cleared
            # when an edit invalidates them. They are deliberately not re-served
            # from the ledger here: the last record is history, not truth.
            "project": project.state.model_dump(mode="json"),
            # What each check is -- fixer, light or heavy -- from measurement
            # and a person's choice. See `Project.check_kinds`.
            "check_kinds": project.check_kinds(cfg.pipeline.heavy_check_after_s),
            # Questions this reading was never put, computed from its own stamp
            # and costing nothing. Without it the only way to learn that a field
            # was added is to pay a re-survey and read a proposal about a project
            # that has not changed -- which reads as drift, and is not.
            "reading_behind": reading_behind(
                getattr(project.state, "state_version", 1)),
            "features": Factory(cfg, project, llm=llm).features(),
            "live_features": registry.live_feature_ids(project),
            # Computed live: the environment may have been edited since the survey.
            # With the repo, not without it: half these checks compare the
            # proposal against what is actually on disk, and omitting it made
            # them no-ops here while `_baseline` ran them in full. The console
            # and the baseline have to refuse the same things.
            "environment_problems": registry.environment_problems(
                project.environment, project.gates, project.repo_path
            ),
            "environment_notes": registry.environment_notes(
                project.environment, project.gates, project.repo_path
            ),
            # Surfaced before the intent box, not after a wasted run.
            "drift": Factory(cfg, project, llm=llm).working_tree_drift(),
            # Whether the gate list still describes this repository. Computed
            # from git and a glob list; it leads to a sentence on the page and
            # nothing else, because re-surveying is a human's call.
            "tooling_drift": registry.tooling_drift(project),
            # What the verify lane has asked this project for. It drove every
            # re-survey proposal and appeared on no screen, so a reading that
            # proposed a browser runner looked like it had invented the idea,
            # and a project answering the same four asks over and over looked
            # like a loop rather than like a request nobody had ruled on.
            "capability_requests": unmet_capabilities(project),
            # Served ready-made rather than re-derived on the page. Two answers
            # to "which suggestion is this" is two chances to disagree, and they
            # would disagree silently: a card that will not decline, or one that
            # stays declined and comes back anyway.
            "recommendations": [
                {**r.model_dump(mode="json"), "key": registry.recommendation_key(r)}
                for r in registry.live_recommendations(project)],
            # Which proposed files are missing, already exactly right, or on
            # disk in a different form. The console had no way to tell the
            # second from the third, so a card went on offering to replace a
            # file with itself -- and pressing it wrote nothing, which is
            # indistinguishable from a button that does not work.
            "scaffolding_state": scaffolding_state(project),
            # Why each red check in the last run is red, where a diagnosis
            # explains it: the cause, the fixes, and what each fix's try read.
            # The repository's guides, layer by layer, as committed at the
            # branch features start from: what binds is what is there.
            "guides": guide_view(project),
            "guide_contradictions": guide_contradictions(project),
            # What Fabrika may propose writing -- DESIGN.md from the tokens,
            # AGENTS.md from the checks, CLAUDE.md importing it -- and
            # additions to an AGENTS.md drafted from what features observed.
            # Nothing is written without a press.
            **(lambda d: {
                "guide_offers": guide_offers(project, d),
                "guide_draft": d.model_dump(mode="json") if d is not None and d.mode == "append"
                else None,
            })(registry.pending_guide_draft(project)),
            "diagnoses": {
                r.name: found.model_dump(mode="json")
                for r in (project.state.baseline.results if project.state.baseline else [])
                if r.diagnosis and (found := registry.diagnosis_for(project, r.name)) is not None},
            # What the project already depends on, as last read and looked up.
            # Problems in it are the project's, shown on its page and never
            # blamed on a feature.
            "dependencies": dependencies.latest_inventory(project),
            "dockerfile": dockerfile_state(project),
            # Levels whose criteria Fabrika does not test, and why: the one
            # answer the explainer, the Tests rows and every spec read.
            "unchecked_levels": unchecked_levels(project.state),
            "paths": {
                "sandboxes": str(cfg.sandbox_path / project.id),
                "evidence": str(cfg.evidence_path / project.id),
            },
            "history": [
                {"kind": r["kind"], "at": r["at"], "note": (r.get("meta") or {}).get("note", "")}
                for r in store if r["kind"] in ("project", "survey", "baseline", "approval")
            ],
        }

    @app.post("/api/projects")
    def create_project(body: NewProject, background: BackgroundTasks) -> dict[str, Any]:
        try:
            project = registry.create(body.path, body.name)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

        async def survey() -> None:
            try:
                await onboarding().run_survey(registry.get(project.id))
            except Exception:
                log.exception("the survey of %s failed", project.id)

        background.add_task(survey)
        return project.state.model_dump(mode="json")

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: str, delete_branches: bool = False) -> dict[str, Any]:
        project_or_404(project_id)
        try:
            return registry.delete(
                project_id, sandbox_root=cfg.sandbox_path, delete_branches=delete_branches
            )
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @app.post("/api/projects/{project_id}/dockerfile/move")
    def move_dockerfile(project_id: str) -> dict[str, Any]:
        """Write the record's Dockerfile to `.fabrika/Dockerfile` and commit it.
        A person's press, like scaffolding: a write into their repository."""
        project = project_or_404(project_id)
        try:
            return registry.move_dockerfile_into_repo(project)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/api/projects/{project_id}/dockerfile/retire")
    def retire_dockerfile(project_id: str) -> dict[str, Any]:
        """Remove `.fabrika/Dockerfile` and commit that, once the project's own
        Dockerfile is what is built. A person's press."""
        project = project_or_404(project_id)
        try:
            return registry.retire_fabrika_dockerfile(project)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/api/projects/{project_id}/scaffold")
    def apply_scaffolding(project_id: str, body: ScaffoldBody) -> dict[str, Any]:
        """Write chosen scaffolding into the repository. Explicitly the human's
        action: it is the one write in the system that does not go through a
        sandbox and gate 2."""
        project = project_or_404(project_id)
        try:
            return registry.apply_scaffolding(project, body.paths, replace=body.replace)
        except (ProjectError, PathEscape) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/api/projects/{project_id}/scaffold-and-read")
    def scaffold_and_read(
        project_id: str, body: ScaffoldBody, background: BackgroundTasks,
    ) -> dict[str, Any]:
        """Write the files, then re-read the repository. One press instead of two.

        Closing a gap took five: write each file, ask for a re-read, read it,
        apply it, re-run the checks. Four of those are the same intent said
        again -- "I did what you asked, look again" -- and the round trip in the
        middle is a paid call a human has to remember to make.

        What is NOT collapsed into this is applying the result, and the reason
        is specific rather than procedural. Every other fact on gate 0 is
        measured; `usable` is a judgement, and the dangerous direction is the
        generous one -- a tier that claims reusable setup and has none costs the
        run every criterion at that level. So the reading comes back and a human
        rules on it, with `testing_probe` beside it saying which of its claims
        were actually proved.
        """
        project = project_or_404(project_id)
        try:
            written = registry.apply_scaffolding(project, body.paths, replace=body.replace)
        except (ProjectError, PathEscape) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

        # The write is awaited and the reading is not, because they fail
        # differently and take different lengths of time. Writing into somebody's
        # repository either happened or did not and the answer is owed
        # immediately. The reading is a model call that has measured 7.7 minutes
        # here, and holding the request open for it tied the work to the
        # browser: a reader looking at a page that appeared stuck reloaded it,
        # which cancelled the task, which guaranteed it stayed stuck. Twice, and
        # the second time the files had already landed -- so the money was spent
        # and the answer thrown away with no record that anything had happened.
        #
        # Backgrounded, it reports through the progress stream like every other
        # project-level run, and survives the page it was started from.
        async def read_again() -> None:
            try:
                await onboarding().run_resurvey(registry.get(project_id))
            except Exception:
                log.exception("the re-reading of %s failed", project_id)

        background.add_task(read_again)
        return {"scaffold": written, "reading": "started"}

    @app.get("/api/projects/{project_id}/scaffold-diff")
    def scaffold_diff(project_id: str, path: str) -> dict[str, Any]:
        """What replacing this file would change.

        Overwriting somebody's file on the strength of a card that says a model
        would like to is not a decision anybody can make. This is what makes it
        one: the proposed contents against what is on disk, in the only form
        that answers "is this three lines or a rewrite".

        The proposal is read from the last re-survey first and the survey second,
        which is the order the screen renders them in -- a file offered by both
        is being offered in its newest form.
        """
        project = project_or_404(project_id)
        try:
            target = safe_join(project.repo_path, path)
        except PathEscape as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

        # The same resolution the write uses. Two ways of answering "which
        # version of this file is on offer" is how a diff comes to describe one
        # thing and the button beside it write another.
        offered = proposed_scaffolding(project).get(path)
        if offered is None:
            raise HTTPException(status_code=404, detail=f"nothing proposes {path!r}")
        proposed = offered.contents

        current = target.read_text(encoding="utf-8") if target.exists() else ""
        lines = list(difflib.unified_diff(
            current.splitlines(), proposed.splitlines(),
            fromfile=f"{path} (yours)", tofile=f"{path} (proposed)", lineterm="", n=3))
        changed = sum(1 for x in lines if x[:1] in "+-" and not x.startswith(("+++", "---")))
        return {
            "path": path,
            "exists": target.exists(),
            "identical": current == proposed,
            "changed_lines": changed,
            "diff": "\n".join(lines),
        }
