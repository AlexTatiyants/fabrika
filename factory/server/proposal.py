"""A re-survey's proposal, and a person's rulings on it, one check at a time or whole."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from ..projects import chosen_cleanup, cleanup_option_problems, ProjectError, proposed_parts
from ..schemas import SurveyDiff
from ..workspace import PathEscape

from .bodies import ProposedCheckBody, ResurveyRuling
from .context import Context


def register(app: FastAPI, ctx: Context) -> None:
    """The standing proposal, a ruling on one of its checks, and applying it."""
    registry = ctx.registry
    project_or_404 = ctx.project_or_404

    @app.get("/api/projects/{project_id}/proposal")
    def latest_proposal(project_id: str) -> dict[str, Any]:
        """The last proposal, if it still describes this project.

        A proposal is a diff against one gate list. A full survey replaces that
        list -- and the testing surface, the placements and the environment with
        it -- so every change the proposal describes is a change to something
        that is no longer there. It is not stale in the sense of being old; it
        is answering a question nobody can ask any more.

        So a survey after it drops it. Looking only for the last `resurvey`
        would mean "the last proposal ever produced", forever, unless somebody
        ruled on it: a day-old diff sitting on the checks screen through three
        surveys, proposing a testing surface that contradicts the one the
        project now has -- and ticking it would overwrite today's reading with a
        reading of a repository state two surveys back.
        """
        project = project_or_404(project_id)
        diff = None
        ruling = None
        for record in project.store:
            if record["kind"] == "resurvey":
                diff, ruling = record.get("payload"), None
            elif record["kind"] == "resurvey_ruling":
                ruling = record.get("payload")
            elif record["kind"] == "survey":
                # What it was a diff against is gone. Dropped rather than
                # marked, because there is nothing here a human can act on: the
                # gate list it argues with no longer exists to be changed.
                diff, ruling = None, None
        # Per check, what has been decided since, and what still waits -- the
        # checks are ruled on in their rows, one at a time.
        standing, ruled = registry.proposal_state(project)
        pending = [f"{c.action}:{c.name}" for c in (standing.gate_changes if standing else [])
                   if f"{c.action}:{c.name}" not in ruled]
        # The rest of the reading, ruled on the same way on the page it changes.
        parts = [part for part in (proposed_parts(project, standing) if standing else [])
                 if part not in ruled]
        return {"diff": diff, "ruling": ruling, "checks": ruled, "pending_checks": pending,
                "pending_parts": parts}

    @app.post("/api/projects/{project_id}/proposal/check")
    def rule_on_proposed_check(project_id: str, body: ProposedCheckBody) -> dict[str, Any]:
        """Apply, or keep the check as it is, for one change a reading proposed."""
        project = project_or_404(project_id)
        try:
            updated = registry.rule_on_proposed_check(project, body.item, body.accept, body.reason)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return updated.state.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/proposal/check/undo")
    def undo_proposed_check(project_id: str, body: ProposedCheckBody) -> dict[str, Any]:
        """Put a check back as it was before its proposed change was accepted."""
        project = project_or_404(project_id)
        try:
            updated = registry.undo_proposed_check(project, body.item)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return updated.state.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/proposal/apply")
    def apply_proposal(project_id: str, body: ResurveyRuling) -> dict[str, Any]:
        """Apply what a person accepted, and each cleanup fix they picked, whole.

        A cleanup fix is several parts -- files, and an environment step that
        runs one of them -- and a person picks the fix, never the parts.
        So the order is this endpoint's, not theirs: the files are written
        first, because the environment step may run one and would fail its
        preparation without it; then the reading is applied, the environment
        with it. Choosing a fix accepts the reading it came from.

        Nothing here starts a feature. A fix that needs real code is a prompt
        for the person's own coding agent: building the repository's own test
        infrastructure as a factory feature is circular, because the run needs
        it to exist first.
        """
        project = project_or_404(project_id)
        diff_payload = None
        for record in project.store:
            if record["kind"] == "resurvey":
                diff_payload = record.get("payload")
        if diff_payload is None:
            raise HTTPException(status_code=409, detail="no re-survey has been run for this project")
        diff = SurveyDiff.model_validate(diff_payload)
        try:
            chosen = chosen_cleanup(diff, body.cleanup)
            for tier, option in chosen:
                problem = cleanup_option_problems(diff, option)
                if problem:
                    raise ProjectError(f"{option.title!r} for {tier} tests cannot be "
                                       f"applied whole: {problem}")
            files = list(dict.fromkeys(f for _, o in chosen for f in o.files))
            written: dict[str, Any] = {}
            if files:
                existing = [f for f in files if (Path(project.state.repo) / f).exists()]
                written = registry.apply_scaffolding(project, files, replace=existing)
                project = registry.get(project_id)
            accepted = list(body.accepted)
            if any(c is not None for c in body.cleanup.values()) and "testing" not in accepted:
                accepted.append("testing")
            if body.part:
                # One part, ruled on where it is shown, with the fix chosen in
                # it: the environment step a fix runs comes with it, and the
                # rest of the reading waits on.
                # The environment goes first: the fix's files are written, and
                # the step that runs one is in place before anything needs it.
                parts = list(dict.fromkeys([
                    *(["environment"] if any(o.environment for _, o in chosen) else []),
                    body.part, *(["testing"] if chosen else [])]))
                _, ruled = registry.proposal_state(project)
                applied = []
                updated = project
                for part in parts:
                    if part not in ruled:
                        updated = registry.rule_on_proposed_part(updated, part, True)
                        applied.append(part)
            else:
                updated, applied = registry.apply_survey_diff(
                    project, diff, accepted,
                    environment=body.environment or any(o.environment for _, o in chosen),
                    checks=body.checks,
                )
            if chosen:
                # A level whose reading says nothing cleans up is tested from
                # here on: the fix is in, and waiting for the next reading to
                # say so would skip its criteria for no reason.
                updated = registry.update(updated, {"cleanup_applied": {
                    **updated.state.cleanup_applied, **{tier: o.title for tier, o in chosen}}})
        except (ProjectError, PathEscape) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

        return {"project": updated.state.model_dump(mode="json"), "applied": applied,
                "written": written}
