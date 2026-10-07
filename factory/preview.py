"""Seeing the running app: a test session held open for a person.

Fabrika starts a project's app every time it runs the checks, and throws it
away. At review a person is asked to rule on criteria nobody tested -- "does
the page show X?" -- with nothing on the screen they could look at. A preview
is the same prepared session the checks get, on the same sealed network, built
from the feature's own branch, held open, with a door onto this machine's
loopback (see `doorway.py` and `containers.Door`).

What it is not: evidence. Opening the app records that it was opened and at
which commit. Whether anything works is still the person's ruling.

One per feature, held by this server process, and only at gate 2: before then
the worktree may not build, and a repair round rewrites it. A verdict, a
dispatch or a delete closes it first; so does an hour nobody asked about it.
"""

from __future__ import annotations

import logging
import asyncio
import time
import urllib.error
import urllib.request
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from .config import Config, DockerConfig
from .gates import Runner, run_setup, test_session
from .projects import Project, ProjectRegistry
from .git import head_sha
from .schemas import PreviewProbe

log = logging.getLogger(__name__)

#: A preview nobody has asked about for this long is closed. "Asked" is the
#: review screen reading the feature, which it does while it is open.
IDLE_S = 3600.0
#: The only stage a preview may open at.
REVIEW_STAGE = "awaiting_verdict"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# one standing app
# --------------------------------------------------------------------------


@dataclass
class Standing:
    """A prepared session with its door open, or why it is not."""

    ready: bool = True
    problem: str = ""
    log: str = ""
    ports: dict[str, int] = field(default_factory=dict)
    url: str = ""
    door: Any = None


def _prefer(runner: Runner) -> str:
    """The network the door should join: the one the session container's app is on."""
    sealed = getattr(runner, "_sealed", "")
    if sealed:
        return sealed
    project = getattr(runner, "project_name", "")
    network = getattr(runner, "_service_network", None)
    return f"{project}_{network()}" if project and callable(network) else ""


@asynccontextmanager
async def standing(project: Project, runner: Runner, cwd: str | Path, docker: DockerConfig,
                   *, label: str) -> AsyncIterator[Standing]:
    """The project's app, up in `cwd`, reachable from this machine while inside.

    Exactly the session an assessment runs in -- `test_session` with the
    environment's services, `test_prepare` and disposable database -- plus the
    preview's own services, held open, and a door for every service's port.
    Every port, not only the one that opens: a page in a browser also calls
    the API on its own port, and that call leaves this machine too.
    """
    from .containers import Door   # containers imports this package's gates

    env = project.environment
    preview = env.preview if env is not None else None
    out = Standing()
    if env is None or preview is None or not preview.open:
        out.ready, out.problem = False, "this project declares nothing a person can open"
        yield out
        return
    services = [*env.services, *preview.services]
    async with test_session(
        cwd, runner, services=services, prepare=list(env.test_prepare),
        disposable=project.state.testing.disposable_db, hold=True,
    ) as session:
        out.log = session.report
        out.ports = dict(session.ports)
        if not session.ready:
            out.ready, out.problem = False, session.problem
            yield out
            return
        port = session.ports.get(preview.open)
        if port is None:
            out.ready, out.problem = False, f"{preview.open!r} was given no port"
            yield out
            return
        door = None
        container = getattr(runner, "session_id", "")
        if container:
            door = Door(docker, label)
            problem = await door.open(container, sorted(set(session.ports.values())),
                                      prefer=_prefer(runner))
            if problem:
                out.ready, out.problem = False, problem
                yield out
                return
        out.url = f"http://127.0.0.1:{port}{preview.path or '/'}"
        out.door = door
        try:
            yield out
        finally:
            if door is not None:
                await door.close()


def _get(url: str, timeout: float) -> int:
    request = urllib.request.Request(url, headers={"User-Agent": "fabrika-preview"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


async def probe(url: str, *, timeout_s: float = 30.0) -> tuple[int, str]:
    """Ask for the page until something answers. (status, problem)

    Retried, because `ready_when` says a service is up and a door that has just
    opened may still be a moment from forwarding. Any answer below 500 settles
    it; a 404 is an answer about the path, and is reported as one.
    """
    deadline = time.monotonic() + timeout_s
    last = ""
    while True:
        try:
            status = await asyncio.to_thread(_get, url, 10.0)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            status, last = 0, str(getattr(exc, "reason", exc))
        if status and status < 500:
            if status >= 400:
                return status, (f"the page answered {status}: the path may be wrong, or the "
                                "app needs something this environment does not give it")
            return status, ""
        if status:
            last = f"the page answered {status}"
        if time.monotonic() >= deadline:
            return status, last or "nothing answered"
        await asyncio.sleep(1.0)


async def measure(project: Project, runner: Runner, cwd: str | Path, config: Config,
                  *, label: str, sha: str = "") -> PreviewProbe | None:
    """Whether the preview opens, measured the way a person will open it.

    For the baseline, at repo ready: the button at review is offered on the
    strength of this having worked, not of a survey having said it would.
    None when the project declares nothing to open.
    """
    env = project.environment
    if env is None or env.preview is None or not env.preview.open:
        return None
    started = time.monotonic()
    try:
        async with standing(project, runner, cwd, config.docker, label=label) as up:
            if not up.ready:
                return PreviewProbe(ok=False, problem=up.problem, url=env.preview.path,
                                    seconds=round(time.monotonic() - started, 1), sha=sha,
                                    at=_now())
            status, problem = await probe(up.url)
            if problem and up.door is not None:
                stopped = await up.door.trouble()
                if stopped:
                    problem = f"{problem}; the door to the app stopped:\n{stopped}"
    except Exception as exc:  # noqa: BLE001 -- a measurement never fails a reading
        return PreviewProbe(ok=False, problem=f"{type(exc).__name__}: {exc}",
                            url=env.preview.path, sha=sha, at=_now())
    return PreviewProbe(ok=not problem, status=status, problem=problem, url=env.preview.path,
                        seconds=round(time.monotonic() - started, 1), sha=sha, at=_now())


# --------------------------------------------------------------------------
# the previews this server holds
# --------------------------------------------------------------------------


@dataclass
class Preview:
    project_id: str
    feature_id: str
    #: starting | open | failed | closed
    status: str = "starting"
    step: str = ""
    url: str = ""
    commit: str = ""
    problem: str = ""
    log: str = ""
    started_at: str = field(default_factory=_now)
    opened_at: str = ""
    closed_at: str = ""
    closed_by: str = ""
    seen: float = field(default_factory=time.monotonic)
    task: asyncio.Task | None = None
    stack: AsyncExitStack | None = None
    runner: Any = None

    def view(self, idle_s: float) -> dict[str, Any]:
        left = max(0.0, idle_s - (time.monotonic() - self.seen))
        return {"status": self.status, "step": self.step, "url": self.url,
                "commit": self.commit, "problem": self.problem, "log": self.log[-4000:],
                "started_at": self.started_at, "opened_at": self.opened_at,
                "closed_at": self.closed_at, "closed_by": self.closed_by,
                "idle_left_s": round(left) if self.status == "open" else None}


def setup_seconds(store: Any) -> float:
    """How long setup took the last time it ran for this feature."""
    last = store.payload("setup") if store is not None else None
    results = (last or {}).get("results") or []
    return round(sum(float(r.get("duration_s") or 0.0) for r in results), 1)


class PreviewError(RuntimeError):
    pass


class Previews:
    """Every preview this process holds, by (project, feature)."""

    def __init__(self, config: Config, publish: Callable[[dict[str, Any]], None],
                 *, idle_s: float = IDLE_S) -> None:
        self.config = config
        self.publish = publish
        self.idle_s = idle_s
        self._held: dict[tuple[str, str], Preview] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _lock(self, key: tuple[str, str]) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def _tell(self, preview: Preview, stage: str = REVIEW_STAGE) -> None:
        self.publish({"project_id": preview.project_id, "feature_id": preview.feature_id,
                      "stage": stage, "phase": "preview", "status": preview.status,
                      "error": preview.problem, "at": _now(), "detail": preview.step})

    def get(self, project_id: str, feature_id: str, *, touch: bool = False) -> Preview | None:
        preview = self._held.get((project_id, feature_id))
        if preview is not None and touch:
            preview.seen = time.monotonic()
        return preview

    # -- what the review screen is told -----------------------------------

    def describe(self, project: Project, feature_id: str, stage: str, store: Any = None,
                 *, touch: bool = False) -> dict[str, Any]:
        env = project.environment
        declared = env.preview if env is not None else None
        probe_ = project.state.preview_probe
        held = self.get(project.id, feature_id, touch=touch) if feature_id else None
        reason = ""
        if stage != REVIEW_STAGE:
            reason = "A preview opens at review, once the packet is ready."
        elif declared is None:
            reason = "Fabrika doesn't know how to run this app for a person yet."
        elif not declared.open:
            reason = declared.note or "This project has nothing a browser can open."
        elif env is not None and ProjectRegistry.preview_problems(env):
            reason = ProjectRegistry.preview_problems(env)[0]
        setup = setup_seconds(store)
        return {
            "available": not reason,
            "reason": reason,
            "declared": bool(declared and declared.open),
            "open": declared.open if declared else "",
            "path": declared.path if declared else "",
            "note": declared.note if declared else "",
            "probe": probe_.model_dump(mode="json") if probe_ else None,
            "estimate_s": round(setup + ((probe_.seconds if probe_ else 0.0) or 30.0)),
            "idle_s": self.idle_s,
            **({"held": held.view(self.idle_s)} if held else {"held": None}),
        }

    # -- lifecycle ---------------------------------------------------------

    async def start(self, project: Project, feature_id: str, stage: str,
                    sandbox_path: Path | None, store: Any) -> Preview:
        """Begin opening a preview. Returns at once; the work runs in a task."""
        key = (project.id, feature_id)
        async with self._lock(key):
            if stage != REVIEW_STAGE:
                raise PreviewError("a preview opens only at review, once the packet is ready")
            info = self.describe(project, "", stage)
            if not info["available"]:
                raise PreviewError(info["reason"])
            if sandbox_path is None or not Path(sandbox_path).is_dir():
                raise PreviewError("this feature's worktree is not there to run")
            held = self._held.get(key)
            if held is not None and held.status in ("starting", "open"):
                held.seen = time.monotonic()
                return held
            preview = Preview(project.id, feature_id, commit=head_sha(sandbox_path))
            self._held[key] = preview
            preview.task = asyncio.create_task(
                self._open(preview, project, Path(sandbox_path), store))
            self._tell(preview)
            return preview

    async def _open(self, preview: Preview, project: Project, cwd: Path, store: Any) -> None:
        from .containers import PREVIEW_LABEL, PREVIEW_NETWORK_INFIX, runner_for

        started = time.monotonic()
        label = f"{project.id}/{preview.feature_id}"

        def step(words: str) -> None:
            preview.step = words
            self._tell(preview)

        try:
            step("building the environment")
            runner, _ = await runner_for(project, self.config,
                                         feature_id=f"{preview.feature_id}-preview")
            if hasattr(runner, "labels"):
                runner.labels = [f"{PREVIEW_LABEL}={label}"]
            if hasattr(runner, "network_infix"):
                runner.network_infix = PREVIEW_NETWORK_INFIX
            preview.runner = runner
            env = project.environment
            if env is not None and (env.setup or project.warm_gates):
                step("setup")
                results = await run_setup(env.setup, cwd, runner, warm=project.warm_gates)
                failed = [r for r in results if not r.passed and not r.skipped]
                if failed:
                    preview.log = "\n\n".join(
                        f"$ {r.command}\n{r.output_tail or ''}" for r in failed)
                    raise PreviewError(f"setup failed: `{failed[0].command}`")
            step("starting the services")
            preview.stack = AsyncExitStack()
            up = await preview.stack.enter_async_context(
                standing(project, runner, cwd, self.config.docker, label=label))
            preview.log = up.log
            if not up.ready:
                raise PreviewError(up.problem)
            step("checking the page")
            _, problem = await probe(up.url)
            if problem:
                stopped = await up.door.trouble() if up.door is not None else ""
                if stopped:
                    preview.log = f"{preview.log}\n\n{stopped}".strip()
                    problem = f"{problem} -- the door to the app stopped (see what it printed)"
                raise PreviewError(problem)
            preview.url = up.url
            preview.status = "open"
            preview.opened_at = _now()
            preview.seen = time.monotonic()
            preview.step = ""
            store.append("preview", {"event": "opened", "commit": preview.commit,
                                     "url": preview.url,
                                     "seconds": round(time.monotonic() - started, 1)},
                         role="human")
            self._tell(preview)
        except asyncio.CancelledError:
            await self._release(preview)
            raise
        except Exception as exc:  # noqa: BLE001 -- said on the screen and in the record
            if not isinstance(exc, PreviewError):
                log.exception("a preview of %s failed to open", preview.feature_id)
            preview.status = "failed"
            preview.problem = str(exc) if isinstance(exc, PreviewError) \
                else f"{type(exc).__name__}: {exc}"
            preview.step = ""
            await self._release(preview)
            store.append("preview", {"event": "failed", "commit": preview.commit,
                                     "problem": preview.problem}, role="orchestrator")
            self._tell(preview)

    async def _release(self, preview: Preview) -> None:
        """Everything the preview holds, down. Never raises."""
        stack, preview.stack = preview.stack, None
        runner, preview.runner = preview.runner, None
        try:
            if stack is not None:
                await stack.aclose()
        except Exception:  # noqa: BLE001
            log.exception("closing a preview's containers failed")
        try:
            down = getattr(runner, "down", None)
            if down is not None:
                await down()
        except Exception:  # noqa: BLE001
            log.exception("taking a preview's stack down failed")

    async def stop(self, project_id: str, feature_id: str, *, by: str,
                   store: Any = None, stage: str = REVIEW_STAGE) -> Preview | None:
        """Close a feature's preview, if it has one. `by` says why, in words."""
        key = (project_id, feature_id)
        async with self._lock(key):
            preview = self._held.get(key)
            if preview is None or preview.status == "closed":
                return preview
            was = preview.status
            task = preview.task
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            await self._release(preview)
            preview.status = "closed"
            preview.closed_at = _now()
            preview.closed_by = by
            preview.url = ""
            preview.step = ""
            if store is not None and was in ("starting", "open"):
                store.append("preview", {"event": "closed", "commit": preview.commit, "by": by},
                             role="human" if by == "you" else "orchestrator")
            self._tell(preview, stage)
            return preview

    async def stop_all(self, *, by: str) -> None:
        for project_id, feature_id in list(self._held):
            await self.stop(project_id, feature_id, by=by)

    async def reap(self, stage_of: Callable[[str, str], tuple[str, Any]]) -> list[tuple[str, str]]:
        """Close what nobody is looking at, and what the feature has moved past.

        `stage_of(project, feature)` gives the feature's stage and its store.
        """
        closed = []
        for (project_id, feature_id), preview in list(self._held.items()):
            if preview.status not in ("starting", "open"):
                continue
            try:
                stage, store = stage_of(project_id, feature_id)
            except Exception:  # noqa: BLE001 -- a feature deleted underneath
                stage, store = "", None
            idle = time.monotonic() - preview.seen > self.idle_s
            if stage != REVIEW_STAGE or idle:
                await self.stop(project_id, feature_id, store=store, stage=stage or REVIEW_STAGE,
                                by=f"{round(self.idle_s / 60)} minutes with nobody looking"
                                if idle
                                else "the feature moved on from review")
                closed.append((project_id, feature_id))
        return closed
