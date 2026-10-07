"""Gate 0: what a person decides about a project's checks, guides and suggestions
before anything is built on it, and the approval that closes it.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from ..projects import ProjectError
from ..workspace import PathEscape

from .bodies import (AdoptRecommendationBody, CheckNameBody, CheckRunsBody, DiagnosisFixBody, FamilyBody,
                     GateDeclineBody, GateNameBody, GuideDraftBody, GuideOfferBody, NewCodeBody,
                     ProjectUpdate, RecommendationBody, SkillsDirBody)
from .context import Context
from .guides_view import guide_offers


def register(app: FastAPI, ctx: Context) -> None:
    """Recommendations, checks, guides, gates, approval, settings and the proposal request."""
    hub = ctx.hub
    registry = ctx.registry
    project_or_404 = ctx.project_or_404
    onboarding = ctx.onboarding

    @app.post("/api/projects/{project_id}/recommendations/decline")
    def decline_recommendation(project_id: str, body: RecommendationBody) -> dict[str, Any]:
        """Turn a suggestion down for good.

        A reading regenerates these from the repository every time it runs, and
        the facts that prompt them do not change, so without a record the same
        ones arrive forever.
        """
        project = project_or_404(project_id)
        try:
            updated = registry.decline_recommendation(project, body.key, body.reason)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return updated.state.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/recommendations/adopt")
    def adopt_recommendation(project_id: str, body: AdoptRecommendationBody) -> dict[str, Any]:
        """Take the suggestion by adding the check it names.

        This is the only path that ever reads `would_gate` for anything but a
        refusal, and it is what a suggestion was for: a check that runs on every
        feature built here afterwards, rather than a packet read once.
        """
        project = project_or_404(project_id)
        try:
            updated = registry.adopt_recommendation(
                project, body.key, body.name, body.command)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return updated.state.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/recommendations/reinstate")
    def reinstate_recommendation(project_id: str, body: RecommendationBody) -> dict[str, Any]:
        """Stop suppressing it. It reappears when a reading makes it again."""
        project = project_or_404(project_id)
        try:
            updated = registry.reinstate_recommendation(project, body.key)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return updated.state.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/checks/ratchet")
    def ratchet_check(project_id: str, body: CheckNameBody) -> dict[str, Any]:
        """Hold a red check at the reading it gave on untouched code."""
        project = project_or_404(project_id)
        try:
            updated = registry.ratchet_check(project, body.name)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return updated.state.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/guides/draft")
    def rule_on_guide_draft(project_id: str, body: GuideDraftBody) -> dict[str, Any]:
        """Add the drafted sections, as edited, to AGENTS.md -- or not."""
        project = project_or_404(project_id)
        draft = registry.pending_guide_draft(project)
        if draft is None or draft.mode != "append":
            raise HTTPException(status_code=409, detail="there are no drafted additions waiting")
        try:
            if body.decision == "decline":
                registry.decline_guide_offer(project, draft.id)
                return {"declined": draft.id}
            return registry.write_guide(
                project, [{"path": draft.path, "contents": body.markdown or draft.markdown,
                           "append": True}],
                offer=draft.id,
                message=("Add the conventions features here kept to AGENTS.md\n\n"
                         f"Drafted by Fabrika from what {draft.features} features observed, "
                         "edited and approved by a person."))
        except (ProjectError, PathEscape) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/api/projects/{project_id}/guides/offer")
    def rule_on_guide_offer(project_id: str, body: GuideOfferBody) -> dict[str, Any]:
        """Write a guide Fabrika proposed, as a person edited it -- or not."""
        project = project_or_404(project_id)
        draft = registry.pending_guide_draft(project)
        offer = next((o for o in guide_offers(project, draft) if o["kind"] == body.kind), None)
        if offer is None or not offer.get("writes"):
            raise HTTPException(status_code=409, detail=f"nothing to write for {body.kind!r}")
        try:
            if body.decision == "decline":
                registry.decline_guide_offer(project, offer.get("key") or body.kind)
                return {"declined": body.kind}
            writes = [{**w, "contents": body.contents.get(w["path"], w["contents"])}
                      for w in offer["writes"]]
            return registry.write_guide(
                project, writes, offer=offer.get("draft") or body.kind,
                message={"design_md": "Start DESIGN.md from the design tokens the code defines",
                         "agents_md": "Add AGENTS.md: how to work in this repository",
                         "claude_md": "Point Claude Code at AGENTS.md",
                         "design_line": "Point AGENTS.md at DESIGN.md",
                         "skills_link_agents": "Link .agents/skills to .claude/skills",
                         "skills_link_claude": "Link .claude/skills to .agents/skills",
                         "commit_working": "Commit this project's guides"}[body.kind]
                + "\n\nProposed by Fabrika from the project's own files, edited and approved "
                  "by a person.")
        except (ProjectError, PathEscape) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/api/projects/{project_id}/guides/skills-dir")
    def set_skills_dir(project_id: str, body: SkillsDirBody) -> dict[str, Any]:
        """Where Fabrika writes skills for this project: the folder its people use."""
        project = project_or_404(project_id)
        try:
            return registry.set_skills_dir(project, body.folder).state.model_dump(mode="json")
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/api/projects/{project_id}/diagnosis/apply")
    def apply_diagnosis_fix(project_id: str, body: DiagnosisFixBody) -> dict[str, Any]:
        """Apply one fix a diagnosis proposed for a red check. The console then
        runs the checks again, so what the fix did is measured, not assumed."""
        project = project_or_404(project_id)
        try:
            return registry.apply_diagnosis_fix(project, body.check, body.fix)
        except (ProjectError, PathEscape) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/api/projects/{project_id}/checks/new-code")
    def hold_new_code(project_id: str, body: NewCodeBody) -> dict[str, Any]:
        """Hold a check on the lines a feature changes; `limit: null` lets go."""
        project = project_or_404(project_id)
        try:
            updated = registry.hold_new_code(project, body.name, body.limit)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return updated.state.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/families/decline")
    def decline_family(project_id: str, body: FamilyBody) -> dict[str, Any]:
        """Go without a family of checks. Blocks nothing; stops the suggestions."""
        project = project_or_404(project_id)
        try:
            updated = registry.decline_family(project, body.family, body.reason)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return updated.state.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/families/reinstate")
    def reinstate_family(project_id: str, body: FamilyBody) -> dict[str, Any]:
        """Be asked about the family again, from the next reading on."""
        project = project_or_404(project_id)
        try:
            updated = registry.reinstate_family(project, body.family)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return updated.state.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/checks/runs")
    def set_check_runs(project_id: str, body: CheckRunsBody) -> dict[str, Any]:
        """Choose when a check runs, overriding what its timing says."""
        project = project_or_404(project_id)
        try:
            updated = registry.set_check_runs(project, body.name, body.runs)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return updated.state.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/gates/decline")
    def decline_gate(project_id: str, body: GateDeclineBody) -> dict[str, Any]:
        """Take a check off this list, or refuse one that was proposed.

        A rejection written to the ledger and read by nothing would not hold:
        the check would come back at the next reading -- and at every reading
        after it, because the script in package.json that suggested it is still
        there.
        """
        project = project_or_404(project_id)
        try:
            updated = registry.decline_gate(
                project, body.name, body.reason, command=body.command)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return updated.state.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/gates/reinstate")
    def reinstate_gate(project_id: str, body: GateNameBody) -> dict[str, Any]:
        """Put a declined check back, with the command it had.

        Declining is not deleting: a project turns a check down because it is
        not ready for it, and "not now" has to be revisitable without anybody
        reconstructing the command from memory.
        """
        project = project_or_404(project_id)
        try:
            updated = registry.reinstate_gate(project, body.name)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return updated.state.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/approve")
    def approve_project(project_id: str) -> dict[str, Any]:
        project = project_or_404(project_id)
        try:
            approved, moved = onboarding().accept(project)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {**approved.state.model_dump(mode="json"), "dockerfile_moved": moved}

    @app.patch("/api/projects/{project_id}")
    def update_project(project_id: str, body: ProjectUpdate) -> dict[str, Any]:
        project = project_or_404(project_id)
        changes = {
            key: value for key, value in (
                ("name", body.name), ("repo", body.repo), ("base_ref", body.base_ref),
                ("digest_budget", body.digest_budget), ("gates", body.gates),
                ("environment", body.environment),
                ("oracle_command", body.oracle_command),
                ("oracle_collect_command", body.oracle_collect_command),
                ("oracle_runtime", body.oracle_runtime),
                ("dependency_policy", body.dependency_policy),
            ) if value is not None
        }
        try:
            updated = registry.update(project, changes)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        hub.publish({
            "project_id": project_id, "feature_id": "", "stage": updated.state.stage,
            "phase": "settings", "status": "done", "error": "", "at": "",
        })
        return updated.state.model_dump(mode="json")

    @app.post("/api/projects/{project_id}/proposal")
    async def propose_gate_changes(project_id: str) -> dict[str, Any]:
        """Ask what should change about this project's gates. Applies nothing.

        Awaited rather than backgrounded: it is one bounded call over a handful
        of files, and a human is looking at the page waiting to rule on it.
        """
        project = project_or_404(project_id)
        try:
            diff = await onboarding().run_resurvey(project)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None
        return diff.model_dump(mode="json")
