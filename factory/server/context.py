"""What every route group shares, built once by `create_app`.

The configuration, the long-lived services made from it, and the lookups and
run-starters the routes call. These were closures inside one `create_app`;
they are one object now, handed to each group's `register`, so a route reads
`registry` or `store_or_404` exactly as it always did -- `register` binds the
names it uses off the context before defining its routes.
"""

from __future__ import annotations

import asyncio
import threading
import time
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import BackgroundTasks, HTTPException

from ..config import Config
from ..git import head_sha
from ..llm import LLM
from ..onboarding import ProjectOnboarding
from ..pipeline import Factory
from ..preview import Previews
from ..projects import Project, ProjectError, ProjectRegistry
from ..routes import RouteMonitor
from .hub import ProgressHub

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
CONSOLE_DIR = ROOT / "console"


@dataclass(eq=False)
class Context:
    cfg: Config
    hub: ProgressHub
    registry: ProjectRegistry
    previews: Previews
    llm: LLM
    routes: RouteMonitor
    #: Features with a run under way in this process. A feature's stage cannot
    #: say so: it is written by the run, a moment after the request that asked
    #: for it returned, so two presses of the same button both read the stage
    #: before either run had moved it -- and two builds went into one worktree.
    runs: set[tuple[str, str]] = field(default_factory=set)
    runs_lock: threading.Lock = field(default_factory=threading.Lock)

    async def start_when_ready(self, project_id: str, feature_id: str, start_at: float) -> None:
        """Sleep until the window resets, then build.

        The sleep is the whole mechanism and it is deliberately dumb: a rolling
        window resets at a known second, so there is nothing to poll for. What
        matters is that the promise is on disk as well as in this task -- the
        task dies with the process, and `lifespan` (lifespan.py) re-arms from the
        feature's own state, because a human who was told a run would start at
        14:05 has stopped watching for it.
        """
        wait = max(0.0, start_at - time.time())
        try:
            if wait:
                await asyncio.sleep(wait)
            factory = self.factory_for(project_id)
            state = factory.state_of(factory.store_for(feature_id))
            if state.stage != "waiting_for_plan":
                return          # cancelled, or started by hand while we slept
            factory.approve_spec(feature_id)
            await self.rebaseline_if_moved(project_id)
            await factory.run_build(feature_id)
        except asyncio.CancelledError:
            raise
        except Exception:       # recorded into the failed phase, never silent
            log.exception("a scheduled build of %s failed", project_id)

    def project_or_404(self, project_id: str) -> Project:
        try:
            return self.registry.get(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    async def rebaseline_if_moved(self, project_id: str) -> None:
        """Baseline the base branch again if it has moved since it was measured.

        Run before every build starts. A merged feature, or anyone's commit,
        leaves the baseline describing a commit builds no longer start from.
        Costs nothing when nothing moved. A baseline that cannot run does not
        stop the build: a check that fails is still compared with the commit
        the feature branched from.
        """
        try:
            project = self.project_or_404(project_id)
            # Off the event loop: git can take seconds on a large repository.
            head = await asyncio.to_thread(head_sha, project.repo_path, project.base_ref or "HEAD")
            if head and head != project.state.baseline_sha:
                await self.onboarding().run_baseline(project)
        except Exception:
            log.exception("baselining %s before a build failed", project_id)

    def factory_for(self, project_id: str) -> Factory:
        """Built fresh per request so it never holds a stale project record."""
        return Factory(self.cfg, self.project_or_404(project_id), llm=self.llm,
                       on_progress=self.hub.publish)

    def onboarding(self) -> ProjectOnboarding:
        return ProjectOnboarding(self.cfg, self.registry, llm=self.llm, on_progress=self.hub.publish)

    def start_run(self, background: BackgroundTasks, project_id: str, feature_id: str,
                  work: Callable[[Factory], Awaitable[Any]], *, rebaseline: bool = True) -> None:
        """Run `work` on this feature after the response, unless a run already is."""
        key = (project_id, feature_id)
        with self.runs_lock:
            if key in self.runs:
                raise HTTPException(status_code=409,
                                    detail="a run on this feature is already under way")
            self.runs.add(key)

        async def run() -> None:
            try:
                if rebaseline:
                    await self.rebaseline_if_moved(project_id)
                await work(self.factory_for(project_id))
            except Exception:  # recorded into the failed phase, never silent
                log.exception("a run on a feature of %s failed", project_id)
            finally:
                with self.runs_lock:
                    self.runs.discard(key)

        background.add_task(run)

    def store_or_404(self, project_id: str, feature_id: str):
        store = self.project_or_404(project_id).feature_store(feature_id)
        if not store.has("state"):
            raise HTTPException(status_code=404, detail=f"no feature {feature_id!r}")
        return store

    async def close_preview(self, project_id: str, feature_id: str, by: str) -> None:
        """Before anything moves a feature on from review. Its worktree is about
        to be released, rebuilt or measured again, and the app standing on it
        would show neither the old code nor the new."""
        try:
            store = self.store_or_404(project_id, feature_id)
        except HTTPException:
            store = None
        await self.previews.stop(project_id, feature_id, by=by, store=store)
