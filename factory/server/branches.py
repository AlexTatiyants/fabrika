"""Outside work: branches built outside Fabrika, handed in for a look.

A look is started here and read here. Nothing on these routes writes to the
branch or to the checkout it lives in; the only thing they append to is the
branch's own ledger under the project's evidence.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException

from .. import branch_facts, git, pieces as cutting
from ..pipeline.branch import LOOK_PHASES, BranchReview, reading_status, record_system_change
from ..schemas import Piece, PieceJudgement
from ..projects import ProjectError

from .as_built import _system_change_view
from .bodies import NewLook
from .context import Context

log = logging.getLogger(__name__)

#: What a look's branch adds, by look: worked out again from git on reading
#: rather than kept from when the look ran, so a better detector reaches every
#: packet already made. Cheap, and the same for a look whatever asks.
_FACTS: dict[tuple[str, int, str], dict[str, Any]] = {}


def _facts_now(repo, packet: dict[str, Any], report: dict | None) -> dict[str, Any]:
    """What the branch adds, from the cut as it is made today -- the pieces a
    look kept were cut by the code of the day it ran -- with each fact pointing
    at the kept piece that holds the same lines, so its link still opens it."""
    kept = [Piece.model_validate(p) for p in packet["pieces"]]
    fresh = cutting.cut(repo, packet["base_sha"], packet["head_sha"])
    facts = branch_facts.facts(repo, packet["base_sha"], packet["head_sha"], fresh, report)

    def lines(p: Piece) -> set[tuple[str, str, int]]:
        return {(p.path, "+", n) for n in p.added} | {(p.path, "-", n) for n in p.removed}

    home = {}
    for p in fresh:
        mine = lines(p)
        best = max(kept, key=lambda k: len(mine & lines(k)), default=None)
        home[p.id] = best.id if best is not None and mine & lines(best) else ""

    def remap(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: (home.get(v, "") if k == "piece" and isinstance(v, str) else remap(v))
                    for k, v in value.items()}
        if isinstance(value, list):
            return [remap(v) for v in value]
        return value

    return remap(facts)


_CLAIMS: dict[tuple, list] = {}


def _claims_now(project, store, packet: dict[str, Any], key: tuple) -> list:
    """The commit's claims against its tests as they are found today -- a look
    that ran when only Python tests were recognised marked every vitest and
    Playwright test it was told of as not there."""
    if key not in _CLAIMS:
        if len(_CLAIMS) > 64:
            _CLAIMS.clear()
        try:
            judgement = PieceJudgement.model_validate(store.payload("pieces_raw") or {})
            names = {g.name for g in project.state.gates if g.family == "tests"}
            ran = [r for r in (packet.get("checks") or {}).get("results") or [] if r.get("name") in names]
            green = all(r.get("passed") for r in ran) if ran else None
            _CLAIMS[key] = cutting.claims_of(judgement, project.repo_path, packet["head_sha"], green)
        except Exception:  # what the look recorded is still worth showing
            log.warning("could not match the claims of %s to its tests", store.feature_id, exc_info=True)
            _CLAIMS[key] = packet.get("claims") or []
    return _CLAIMS[key]


_FOLDERS: dict[tuple[str, str], dict[str, int]] = {}


def _folders(repo, head: str) -> dict[str, int]:
    """How many files each folder holds at the head, two levels deep: what a
    look with no as-built groups says where the branch landed in, and how much
    of each it left alone."""
    key = (str(repo), head)
    if key not in _FOLDERS:
        if len(_FOLDERS) > 64:
            _FOLDERS.clear()
        names = (git.out(["ls-tree", "-r", "--name-only", head], repo) or "").splitlines()
        counts: dict[str, int] = {}
        for name in names:
            folder = "/".join(name.split("/")[:-1][:2]) or "."
            counts[folder] = counts.get(folder, 0) + 1
        _FOLDERS[key] = counts
    return _FOLDERS[key]


def register(app: FastAPI, ctx: Context) -> None:
    """Listing branches, starting a look at one, and reading it."""
    project_or_404 = ctx.project_or_404

    def review_for(project_id: str) -> BranchReview:
        return ctx.branch_review_for(project_id)

    @app.get("/api/branches")
    def all_branches() -> list[dict[str, Any]]:
        """Every look at outside work, in every project, newest first: the
        side panel lists them beside the features."""
        out: list[dict[str, Any]] = []
        for project in ctx.registry.list():
            out.extend(ctx.branch_review_for(project.id).branches())
        out.sort(key=lambda b: b["updated_at"], reverse=True)
        return out

    @app.get("/api/projects/{project_id}/branches")
    def list_branches(project_id: str) -> dict[str, Any]:
        """The branches looked at, and the ones there are to look at."""
        review = review_for(project_id)
        looked = review.branches()
        seen = {b["branch"] for b in looked}
        return {"looked": looked,
                "candidates": [c for c in review.candidate_branches() if c["branch"] not in seen],
                "base_ref": review.project.base_ref}

    @app.post("/api/projects/{project_id}/branches")
    def start_look(project_id: str, body: NewLook, background: BackgroundTasks) -> dict[str, Any]:
        """Returns as soon as the look is recorded; the checks and the reading
        take minutes, and a POST that hangs that long is a POST people reload."""
        review = review_for(project_id)
        slug = review.branch_slug_for(body.branch)
        key = f"branch-{slug}"
        with ctx.runs_lock:
            if (project_id, key) in ctx.runs:
                raise HTTPException(status_code=409, detail="a look at this branch is already under way")
        try:
            state = review.open_look(body.branch, body.intent)
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        ctx.start_run(background, project_id, key,
                      lambda _factory: review_for(project_id).run_look(state.slug),
                      rebaseline=False)
        return state.model_dump(mode="json")

    @app.get("/api/projects/{project_id}/branches/{slug}")
    def get_branch(project_id: str, slug: str) -> dict[str, Any]:
        project = project_or_404(project_id)
        review = review_for(project_id)
        store = review.branch_store(slug)
        if not store.has("state"):
            raise HTTPException(status_code=404, detail=f"no look at a branch {slug!r}")
        state = review.look_state(store)
        packet = store.payload("branch_packet")
        # A reading finished after the look did -- Read again -- leaves both
        # ends read and nothing yet comparing them.
        if (project_id, f"branch-{slug}") not in ctx.runs and all(
                r["state"] == "read" for r in reading_status(store, state).values()):
            try:
                record_system_change(store, state)
            except Exception:  # an account of the system: never worth the page
                log.warning("could not compare the readings of %s", slug, exc_info=True)
        system = _system_change_view(store, project.repo_path)
        if packet and packet.get("pieces"):
            key = (str(store.dir), packet.get("look", 0), packet.get("head_sha", ""))
            if key not in _FACTS:
                if len(_FACTS) > 64:
                    _FACTS.clear()
                try:
                    _FACTS[key] = _facts_now(project.repo_path, packet, (system or {}).get("report"))
                except Exception:  # what the look recorded is still worth showing
                    log.warning("could not work out what %s adds", slug, exc_info=True)
                    _FACTS[key] = packet.get("facts") or {}
            packet = {**packet, "facts": _FACTS[key], "claims": _claims_now(project, store, packet, key)}
        order = [*LOOK_PHASES[:2], "attribution", *LOOK_PHASES[2:]]
        state.phases.sort(key=lambda p: order.index(p.name) if p.name in order else len(order))
        return {
            "state": state.model_dump(mode="json"),
            "looks": [r["payload"] for r in store.all("look")],
            # The packet of this look, and the last finished one beside it while
            # this one runs: a look in progress does not blank the one before.
            "packet": packet,
            "current": bool(packet) and packet.get("look") == state.look,
            "system": system,
            "readings": reading_status(store, state),
            "folders": _folders(project.repo_path, state.head_sha),
            "running": (project_id, f"branch-{slug}") in ctx.runs,
        }

    @app.post("/api/projects/{project_id}/branches/{slug}/read")
    async def read_again(project_id: str, slug: str) -> dict[str, Any]:
        """Read the as-built again at whichever end of the look is not read
        whole: a reading that stopped carries on from the files it kept."""
        project_or_404(project_id)
        review = review_for(project_id)
        store = review.branch_store(slug)
        if not store.has("state"):
            raise HTTPException(status_code=404, detail=f"no look at a branch {slug!r}")
        state = review.look_state(store)
        status = reading_status(store, state)
        started = []
        for label, sha in (("base", state.base_sha), ("head", state.head_sha)):
            if status[label]["state"] in ("read", "reading"):
                continue
            if review._as_built_begin(store, sha, label) is not None:
                started.append(label)
        if not started and not any(r["state"] == "reading" for r in status.values()):
            raise HTTPException(status_code=409, detail=(
                "Nothing to read: both ends are read whole, or the as-built is not read on this factory."))
        return {"started": started, "readings": reading_status(store, state)}
