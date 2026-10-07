"""The repository's guides as the project page shows them, and what Fabrika may
offer to write -- each cached by the commit features start from.
"""

from __future__ import annotations

import threading
from typing import Any

from ..git import head_sha


def _base_sha(project: Any) -> str:
    return head_sha(project.repo_path, project.state.base_ref or "HEAD")


class _Recent:
    """The last few answers, by key, safe to share between request threads.

    These were dicts emptied on every miss and read back by key afterwards: two
    projects polling in turn emptied each other's answer on every request, and
    a miss in one thread could empty the dict between another thread's write
    and its read -- a KeyError, served as a 500.
    """

    def __init__(self, size: int = 32) -> None:
        self.size = size
        self.items: dict[Any, Any] = {}
        self.lock = threading.Lock()

    def get(self, key: Any) -> Any:
        with self.lock:
            value = self.items.pop(key, None)
            if value is not None:
                self.items[key] = value     # most recent last
            return value

    def put(self, key: Any, value: Any) -> Any:
        with self.lock:
            self.items.pop(key, None)
            self.items[key] = value
            while len(self.items) > self.size:
                del self.items[next(iter(self.items))]
        return value


_VIEW_CACHE = _Recent()


def guide_view(project: Any) -> dict[str, Any]:
    """The repository's guides, layer by layer, as committed at the branch
    features start from. Cached by that commit: the page asks every few
    seconds, and the answer only moves when the commit does."""
    from .. import guides
    from ..schemas import Guide

    sha = _base_sha(project)
    if not sha:
        return {"guides": [], "skill_dirs": [], "sha": ""}
    key = (str(project.repo_path), sha)
    cached = _VIEW_CACHE.get(key)
    if cached is None:
        files = guides.tracked(project.repo_path, sha)
        out = []
        for g in guides.found(project.repo_path, sha, files):
            text = guides.read_at(project.repo_path, sha, g.path) or ""
            lines = [ln for ln in text.splitlines() if ln.strip()][:6]
            out.append({**g.model_dump(mode="json"), "first_lines": lines, "chars": len(text)})
        dirs = [d for d in guides.SKILL_DIRS if any(f.startswith(d + "/") for f in files)]
        cached = _VIEW_CACHE.put(key, {
            "guides": out, "skill_dirs": dirs, "sha": sha[:12], "files": files,
            "clashes": [{"name": n, "paths": p} for n, p in guides.skill_clashes(
                [Guide.model_validate(g) for g in out])]})
    view = {k: v for k, v in cached.items() if k != "files"}
    # Where a new skill goes: a person's choice, else read from the repository.
    home, why = guides.skills_home(cached["files"], project.state.skills_dir)
    return {**view, "skills_home": home, "skills_home_why": why,
            "skills_home_chosen": bool(project.state.skills_dir)}


_OFFER_CACHE = _Recent()


def guide_offers(project: Any, draft: Any = None) -> list[dict[str, Any]]:
    """What Fabrika may propose writing, worked out from the branch features
    start from. Cached by its commit and what was decided: the page asks every
    few seconds, and the answer only moves when one of those does."""
    from .. import guides

    sha = _base_sha(project)
    if not sha:
        return []
    new_draft = draft if draft is not None and draft.mode == "new" else None
    # The working copy too: a guide file sitting there uncommitted is offered
    # for commit, and nothing is offered to create in its place.
    working = guides.working_guides(project.repo_path)
    decided = "|".join([*project.state.guide_offers_declined,
                        *(f"{g.name}={g.command}" for g in project.state.gates),
                        new_draft.id if new_draft is not None else "",
                        *(f"{w['path']}={w['was']}" for w in working)])
    key = (str(project.repo_path), sha, decided)
    cached = _OFFER_CACHE.get(key)
    if cached is None:
        files = guides.tracked(project.repo_path, sha)
        read = lambda path: guides.read_at(project.repo_path, sha, path)  # noqa: E731
        tokens = ([] if guides.DESIGN_PATH in files or "design_md" in project.state.guide_offers_declined
                  else guides.design_tokens(read, files))
        cached = _OFFER_CACHE.put(key, guides.offers(
            files, project.state.guide_offers_declined, tokens=tokens,
            checks=project.state.gates, agents_text=read(guides.AGENTS_PATH),
            name=project.state.name or project.repo_path.name, draft=new_draft, working=working))
    return cached


def guide_contradictions(project: Any, limit: int = 20) -> list[dict[str, Any]]:
    """Where features' scouts saw the code do otherwise than a guide says,
    newest first, once each -- a health note for a person to settle."""
    seen: dict[str, dict[str, Any]] = {}
    for record in reversed(list(project.store)):
        if record.get("kind") != "guide_contradictions":
            continue
        payload = record.get("payload") or {}
        for c in payload.get("contradictions") or []:
            key = f"{c.get('guide')}|{c.get('rule')}"
            if key not in seen:
                seen[key] = {**c, "feature": payload.get("feature", "")}
        if len(seen) >= limit:
            break
    return list(seen.values())[:limit]
