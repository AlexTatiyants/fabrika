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
from ..pipeline.branch import (LOOK_PHASES, BranchReview, compare_rounds, look_calls, reading_status,
                               record_system_change, rounds_of)
from ..schemas import Piece, PieceJudgement
from ..projects import ProjectError

from ..pull_requests import pr_target
from .as_built import _system_change_view
from .features import pull_request_view
from .bodies import LookRuling, LookVerdict, NewLook
from .context import Context

log = logging.getLogger(__name__)

#: What a look's branch adds, by look: worked out again from git on reading
#: rather than kept from when the look ran, so a better detector reaches every
#: packet already made. Cheap, and the same for a look whatever asks.
_FACTS: dict[tuple[str, int, str], dict[str, Any]] = {}

#: A file shown whole stops here: past it, the code view's pieces are the way in.
_FILE_LINES = 5000


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


#: Looks whose tidy branch is being made now, as (project, slug).
_TIDYING: set[tuple[str, str]] = set()
#: Looks whose pull request is being opened now, as (project, slug).
_PR_OPENING: set[tuple[str, str]] = set()
#: Looks Fabrika is fixing now, as (project, slug).
_FIXING: set[tuple[str, str]] = set()


def _latest(store, kind: str, look: int) -> dict[str, Any] | None:
    """The latest record of `kind` that belongs to look `look`."""
    return next((r["payload"] for r in reversed(store.all(kind)) if r["payload"].get("look") == look), None)


def _broken(claims: list, findings: dict[str, Any] | None) -> list:
    """Each claim a failing probe named is broken, whatever its tests said:
    a probe that goes red is a fact about the code, and a test that passes
    beside it is a test of something else. Claims are CL-1 on, in order."""
    if not findings:
        return claims
    by_claim: dict[str, list[str]] = {}
    for probe in findings.get("probes") or []:
        if probe.get("red"):
            for claim in probe.get("claim_ids") or []:
                by_claim.setdefault(claim, []).append(probe.get("finding") or probe["path"])
    return [{**c, "state": "broken", "broken_by": by_claim[f"CL-{n}"]} if f"CL-{n}" in by_claim else c
            for n, c in enumerate(claims, 1)]


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
                raise HTTPException(status_code=409, detail=(
                    "its tidy branch is being made" if (project_id, slug) in _TIDYING
                    else "a look at this branch is already under way"))
        try:
            state = review.open_look(body.branch, body.intent)
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        ctx.start_run(background, project_id, key,
                      lambda _factory: review_for(project_id).run_look(state.slug),
                      rebaseline=False)
        return state.model_dump(mode="json")

    def look_or_404(project_id: str, slug: str):
        review = review_for(project_id)
        if not review.branch_store(slug).has("state"):
            raise HTTPException(status_code=404, detail=f"no look at a branch {slug!r}")
        if (project_id, f"branch-{slug}") in ctx.runs:
            raise HTTPException(status_code=409, detail="This branch is being looked at or tidied; rule once it is done.")
        return review

    @app.post("/api/projects/{project_id}/branches/{slug}/rulings")
    def rule(project_id: str, slug: str, body: LookRuling) -> dict[str, Any]:
        """Your call on one finding: back to whoever built it, or a risk you accept."""
        project_or_404(project_id)
        try:
            return look_or_404(project_id, slug).rule(slug, body.finding_id, body.ruling, body.note)
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @app.post("/api/projects/{project_id}/branches/{slug}/verdict")
    def verdict(project_id: str, slug: str, body: LookVerdict, background: BackgroundTasks) -> dict[str, Any]:
        """The look underwritten, or the branch sent back. Underwritten, it
        ends as a pull request where the project says they go."""
        project = project_or_404(project_id)
        try:
            record = look_or_404(project_id, slug).verdict(slug, body.verdict, body.note)
        except ProjectError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        if record["verdict"] == "underwritten" and not isinstance(pr_target(project), str):
            open_pr_later(background, project_id, slug)
        return record

    def open_pr_later(background: BackgroundTasks, project_id: str, slug: str) -> None:
        async def run() -> None:
            _PR_OPENING.add((project_id, slug))
            try:
                await review_for(project_id).open_pull_request(slug)
            except Exception:  # recorded as pull_request_failed, and said on the screen
                log.warning("the pull request for look %s was not opened", slug, exc_info=True)
            finally:
                _PR_OPENING.discard((project_id, slug))
        background.add_task(run)

    @app.post("/api/projects/{project_id}/branches/{slug}/fix")
    def fix(project_id: str, slug: str, background: BackgroundTasks) -> dict[str, Any]:
        """Fabrika fixes this round's defects on the work's own branch, then
        looks at the result as the work's next round -- all under the work's
        one key, so nothing else starts on it meanwhile."""
        project_or_404(project_id)
        review = look_or_404(project_id, slug)
        store = review.branch_store(slug)
        state = review.look_state(store)
        if not look_calls(store, state.look)["send_back"]:
            raise HTTPException(status_code=409, detail="Nothing is going back, so there is nothing to fix.")

        async def work(_factory) -> None:
            _FIXING.add((project_id, slug))
            try:
                await review_for(project_id).fix(slug)
            finally:
                _FIXING.discard((project_id, slug))
            await review_for(project_id).run_look(slug)

        ctx.start_run(background, project_id, f"branch-{slug}", work, rebaseline=False)
        return {"started": True, "branch": state.working_branch or f"factory/outside/{slug}"}

    @app.post("/api/projects/{project_id}/branches/{slug}/pull-request")
    def pull_request(project_id: str, slug: str, background: BackgroundTasks) -> dict[str, Any]:
        """Open the underwritten look's pull request: again after it failed,
        or now that the project says where they go."""
        project = project_or_404(project_id)
        review = review_for(project_id)
        store = review.branch_store(slug)
        if not store.has("state"):
            raise HTTPException(status_code=404, detail=f"no look at a branch {slug!r}")
        state = review.look_state(store)
        if (_latest(store, "look_verdict", state.look) or {}).get("verdict") != "underwritten":
            raise HTTPException(status_code=409, detail="Only an underwritten look is opened as a pull request.")
        target = pr_target(project)
        if isinstance(target, str):
            raise HTTPException(status_code=409, detail=target)
        if (project_id, slug) in _PR_OPENING:
            raise HTTPException(status_code=409, detail="Its pull request is being opened.")
        open_pr_later(background, project_id, slug)
        return {"started": True}

    @app.post("/api/projects/{project_id}/branches/{slug}/tidy")
    def make_tidy(project_id: str, slug: str, background: BackgroundTasks) -> dict[str, Any]:
        """Make the tidy branch for the latest look, after the response: the
        cleanup and the checks it is fenced by take minutes. Under the look's
        own key, so a tidy branch is never made while the branch is looked at,
        nor a look started while one is made."""
        project_or_404(project_id)
        review = review_for(project_id)
        store = review.branch_store(slug)
        if not store.has("state"):
            raise HTTPException(status_code=404, detail=f"no look at a branch {slug!r}")
        plan = review.tidy_plan(store, review.look_state(store))
        if not (plan["cleanup"] or plan["green"] or plan["red"]):
            raise HTTPException(status_code=409, detail=(
                "This look found nothing to put on a tidy branch: no cleanup, and no regression "
                "probe worth keeping."))

        async def tidy(_factory) -> None:
            _TIDYING.add((project_id, slug))
            try:
                await review_for(project_id).make_tidy(slug)
            finally:
                _TIDYING.discard((project_id, slug))

        ctx.start_run(background, project_id, f"branch-{slug}", tidy, rebaseline=False)
        return {"branch": plan["branch"], "started": True}

    @app.get("/api/projects/{project_id}/branches/{slug}")
    def get_branch(project_id: str, slug: str, look: int | None = None) -> dict[str, Any]:
        """The work, at one of its rounds: the latest, or `look`."""
        project = project_or_404(project_id)
        review = review_for(project_id)
        store = review.branch_store(slug)
        if not store.has("state"):
            raise HTTPException(status_code=404, detail=f"no look at a branch {slug!r}")
        state = review.look_state(store)
        if (project_id, f"branch-{slug}") not in ctx.runs:
            state = review.adopt_legacy_fix(store, state)
        packet = (_latest(store, "branch_packet", look) if look else None) or store.payload("branch_packet")
        # A reading finished after the look did -- Read again -- leaves both
        # ends read and nothing yet comparing them.
        if (project_id, f"branch-{slug}") not in ctx.runs and all(
                r["state"] == "read" for r in reading_status(store, state).values()):
            try:
                record_system_change(store, state)
            except Exception:  # an account of the system: never worth the page
                log.warning("could not compare the readings of %s", slug, exc_info=True)
        system = _system_change_view(store, project.repo_path)
        shown = (packet or {}).get("look", state.look)
        findings = _latest(store, "look_findings", shown)
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
            packet = {**packet, "facts": _FACTS[key],
                      "claims": _broken(_claims_now(project, store, packet, key), findings)}
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
            "findings": findings,
            "shown": shown,
            "rounds": rounds_of(store),
            "compared": compare_rounds(store, shown),
            "calls": look_calls(store, shown),
            "verdict": _latest(store, "look_verdict", shown),
            "tidy": _latest(store, "tidy", shown),
            "tidy_failed": _latest(store, "tidy_failed", shown),
            "tidy_plan": {k: (len(v) if isinstance(v, list) else v)
                          for k, v in review.tidy_plan(store, state).items()},
            "tidying": (project_id, slug) in _TIDYING,
            "fixing": (project_id, slug) in _FIXING,
            "fix": _latest(store, "look_fix", shown),
            "fix_failed": _latest(store, "look_fix_failed", shown),
            **pull_request_view(project, store, opening=(project_id, slug) in _PR_OPENING, look=shown),
            "running": (project_id, f"branch-{slug}") in ctx.runs
                       and (project_id, slug) not in _TIDYING and (project_id, slug) not in _FIXING,
        }

    @app.get("/api/projects/{project_id}/branches/{slug}/file")
    def get_branch_file(project_id: str, slug: str, path: str, look: int | None = None) -> dict[str, Any]:
        """One file the round changed, whole, as it is at the round's head,
        with the lines the branch added in it. The code view is the branch's
        pieces; this is the file they sit in. Only a file the round changed
        can be read here."""
        project = project_or_404(project_id)
        store = review_for(project_id).branch_store(slug)
        if not store.has("state"):
            raise HTTPException(status_code=404, detail=f"no look at a branch {slug!r}")
        packet = (_latest(store, "branch_packet", look) if look else None) or store.payload("branch_packet")
        mine = [p for p in (packet or {}).get("pieces") or [] if p.get("path") == path]
        if not mine:
            raise HTTPException(status_code=404, detail=f"this round did not change {path!r}")
        head = packet.get("head_sha", "")
        if not any(p.get("start") for p in mine):
            return {"path": path, "head_sha": head, "gone": True, "lines": [], "added": []}
        done = git.run(["show", f"{head}:{path}"], project.repo_path, text=False)
        if done is None or done.returncode != 0:
            raise HTTPException(status_code=404, detail=f"{path!r} could not be read at {head[:7]}")
        raw = done.stdout
        if b"\0" in raw[:8192]:
            return {"path": path, "head_sha": head, "binary": True, "lines": [], "added": []}
        lines = raw.decode("utf-8", errors="replace").splitlines()
        return {"path": path, "head_sha": head, "lines": lines[:_FILE_LINES],
                "cut": max(0, len(lines) - _FILE_LINES),
                "added": sorted({n for p in mine for n in p.get("added") or []})}

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
