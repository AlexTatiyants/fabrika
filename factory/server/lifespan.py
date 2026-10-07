"""What the server does as it starts and as it stops.

Booting: chain the shutdown signals, sweep what a killed process left behind --
sandboxes, superseded images, previews -- and re-arm every build that was
waiting on a plan. Stopping: end the open streams, cancel what was re-armed,
and close every preview this process opened.
"""

from __future__ import annotations

import asyncio
import signal
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from .. import isolation
from ..config import session_fallback
from ..containers import harness_image_tag, image_tag, sweep_images, sweep_previews
from ..sandbox import Sandbox
from ..schemas import FeatureState
from ..unitenv import _harness_layers
from .context import Context

log = logging.getLogger(__name__)


def lifespan_for(ctx: Context):
    """The app's lifespan, over this context's services."""
    cfg = ctx.cfg
    hub = ctx.hub
    registry = ctx.registry
    previews = ctx.previews
    factory_for = ctx.factory_for
    store_or_404 = ctx.store_or_404
    start_when_ready = ctx.start_when_ready

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Uvicorn waits for active connections *before* it sends the lifespan
        # shutdown event, so hub.close() cannot wait for the `finally` below --
        # that runs on the far side of the very wait an open SSE stream hangs.
        # Nor is there an in-band signal: for an in-flight response uvicorn only
        # sets keep_alive=False, sends no http.disconnect, and leaves the task
        # running. The signal is the one hook that fires early enough. Chained,
        # not replaced, so uvicorn still sees it and still exits.
        loop = asyncio.get_running_loop()
        previous: dict[Any, Any] = {}

        def on_signal(sig: int, frame: Any) -> None:
            loop.call_soon_threadsafe(hub.close)
            handler = previous.get(sig)
            if callable(handler):
                handler(sig, frame)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                previous[sig] = signal.signal(sig, on_signal)
            except ValueError:
                pass        # not the main thread: a gunicorn worker, an embed

        # Sandboxes are real directories on somebody's laptop. They leak.
        for project in registry.list():
            try:
                Sandbox.prune(project.repo_path)
            except Exception:  # a missing repo must not stop the server booting
                log.warning("could not prune the sandboxes of %s", project.id, exc_info=True)
                continue

        # And so are images, for the same reason and at a hundred times the
        # size. Only tags this factory minted, only the superseded ones, and
        # only for projects whose current tag we can name -- see sweep_images.
        keep: dict[str, str] = {}
        for project in registry.list():
            spec = project.state.environment
            if spec and spec.dockerfile.strip():
                try:
                    keep[project.id] = image_tag(project.id, spec)
                except Exception:
                    log.warning("could not name the image of %s; its old images are kept",
                                project.id, exc_info=True)
                    continue
        # A harness layer is keyed on the image under it, so the live ones can
        # only be named after the loop above has named the live environments.
        harness_keep: list[str] = []
        for route in cfg.routes.values():
            if not route.authors_in_container():
                continue
            for base in keep.values():
                try:
                    harness_keep.append(harness_image_tag(base, route))
                except Exception:
                    continue
        # The layers made on the plain agent image rather than a project's: a
        # command-line route's sealed completions run there, and setup builds
        # every harness there before any project exists. Dropped at boot, the
        # next completion or the next setup would pay for the build again.
        for route in cfg.routes.values():
            if not route.container_install:
                continue
            try:
                harness_keep.append(harness_image_tag(cfg.docker.agent_image, route))
            except Exception:
                continue
        # And a role's fallback harness, layered on its own route's, which is
        # keyed on that layer rather than on the environment.
        for role_name in cfg.roles:
            try:
                layers = _harness_layers(cfg.route_for(role_name),
                                         session_fallback(cfg, role_name))
            except Exception:
                continue
            if len(layers) < 2:
                continue
            for base in keep.values():
                tag = base
                try:
                    for layer in layers:
                        tag = harness_image_tag(tag, layer)
                except Exception:
                    continue
                harness_keep.append(tag)
        # Never from the suite, for the reason the preview sweep below gives:
        # it shares this machine's Docker with a real server, whose session
        # images a test has no business judging superseded.
        if keep and not isolation.TEST_SUITE_ON_HOST:
            try:
                dropped = await sweep_images(keep, cfg.docker, harness_keep)
                if dropped:
                    log.info("reclaimed %d superseded image(s)", len(dropped))
            except Exception:  # never allowed to stop the server booting
                log.warning("the image sweep failed; superseded images are kept", exc_info=True)
        # A preview is held by the process that opened it, so whatever one
        # left standing when that process died has nobody to close it. Never
        # from the suite, which shares this machine's Docker with a real server.
        if not isolation.TEST_SUITE_ON_HOST:
            try:
                gone = await sweep_previews(cfg.docker)
                if gone:
                    log.info("removed %d thing(s) a closed preview left behind", len(gone))
            except Exception:  # never allowed to stop the server booting
                log.warning("the preview sweep failed", exc_info=True)

        def preview_stage(project_id: str, feature_id: str) -> tuple[str, Any]:
            store = store_or_404(project_id, feature_id)
            return FeatureState.model_validate(store.payload("state")).stage, store

        async def reap_previews() -> None:
            while True:
                await asyncio.sleep(60)
                try:
                    await previews.reap(preview_stage)
                except Exception:  # noqa: BLE001 -- the next minute tries again
                    log.exception("reaping idle previews failed")

        # Re-arm every run that was waiting on a plan when this process last
        # stopped. A scheduled build that a restart quietly forgets is worse
        # than one that was never scheduled.
        pending = []
        for project in registry.list():
            try:
                for state in factory_for(project.id).deferred_features():
                    pending.append(asyncio.create_task(start_when_ready(
                        project.id, state.feature_id, state.start_at)))
            except Exception:
                # A build somebody was told would start at a given time, which
                # now will not. That has to be said somewhere.
                log.exception("could not re-arm the scheduled builds of %s", project.id)
                continue
        pending.append(asyncio.create_task(reap_previews()))
        try:
            yield
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            hub.close()         # idempotent; covers a shutdown no signal drove
            for task in pending:
                task.cancel()
            try:
                await previews.stop_all(by="Fabrika stopping")
            except Exception:  # noqa: BLE001 -- the next boot's sweep has it
                log.exception("stopping the open previews failed")

    return lifespan
