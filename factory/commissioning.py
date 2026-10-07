"""Commissioning: what has to work on this machine before anything is built.

Five things, each measured rather than ticked: Docker answers and the way out
works; at least one account can pay for a model; each connected account's
harness is built; the crew is staffed; and the limits on a feature are chosen.
Adding a project is the yard's job. The console's setup screen lights a lamp for each from what
this module reports, never from what was clicked.

Two of the five are choices rather than measurements -- the crew and the limits
always have a working value, so "set" can only mean a person looked at them.
The crew says so in the file (a `staffing:` block); the limits leave no trace
in it, so that one confirmation, the accounts a person said are not theirs, and
whether setup was finished are kept in `setup.json` under the evidence root.
That file is the console's memory of a conversation, not configuration: delete
it and the screen asks again, and nothing about a run changes.

Anything slow -- a Docker check that builds the proxy image the first time, an
install, a harness build that takes minutes -- runs as a job in this process
and is reported while it runs, so no request waits on it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import containers, egress, procs
from .config import (Config, RouteConfig, independence_problems, staffing_suggestions,
                     write_staffing)
from .files import write_atomic

log = logging.getLogger(__name__)

SETUP_FILE = "setup.json"

#: Free space on Docker's disk below which setup lights amber. Not the
#: per-build floor (`docker.min_free_gb`), which is what one build needs to
#: start: a harness layer is half a gigabyte to a gigabyte and a half, each
#: project's environment is another, and every feature adds a checkout.
SETUP_DISK_GB = 10.0

#: The agents a lean crew leaves deep: the four whose output everything after
#: them is measured against. The same set the agent configuration screen uses
#: (`AC_KEEP_DEEP` in console/app/agent-config.js).
LEAN_KEEP_DEEP = frozenset({"spec_writer", "architect", "oracle", "reviewer"})
PRESETS = ("suggested", "lean", "deep")


# ---------------------------------------------------------------- the memory

def state_path(cfg: Config) -> Path:
    return cfg.evidence_path / SETUP_FILE


def read_state(cfg: Config) -> dict[str, Any]:
    path = state_path(cfg)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    return {"limits_confirmed": bool(data.get("limits_confirmed")),
            "declined": [str(n) for n in data.get("declined") or []],
            "finished_at": data.get("finished_at")}


def write_state(cfg: Config, **changes: Any) -> dict[str, Any]:
    data = {**read_state(cfg), **changes}
    path = state_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(data, indent=2) + "\n")
    return data


# ---------------------------------------------------------------- jobs

class Jobs:
    """Named background work, one at a time per name, reported while it runs.

    Held for the life of the process. A job that dies with it is simply gone:
    the screen measures again on its next load rather than trusting a record
    of work nobody is doing.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def running(self, name: str) -> bool:
        return (self._jobs.get(name) or {}).get("state") == "running"

    def start(self, name: str, work: Callable[[], Awaitable[Any]]) -> dict[str, Any]:
        """Schedule `work` on the running loop. Only from an `async def`
        endpoint: FastAPI runs a plain `def` one in a worker thread, which has
        no loop to schedule on."""
        if self.running(name):
            return self._jobs[name]
        job = {"state": "running", "detail": "", "started_at": time.time(), "finished_at": None}
        self._jobs[name] = job

        async def run() -> None:
            try:
                result = await work()
                job["state"] = "ok"
                job["detail"] = result if isinstance(result, str) else ""
            except Exception as exc:     # reported on the screen, and logged
                log.warning("setup job %s failed", name, exc_info=True)
                job["state"] = "failed"
                job["detail"] = str(exc)
            finally:
                job["finished_at"] = time.time()

        self._tasks[name] = asyncio.get_running_loop().create_task(run())
        return job

    def get(self, name: str) -> dict[str, Any] | None:
        return self._jobs.get(name)

    def as_dict(self) -> dict[str, dict[str, Any]]:
        return {name: dict(job) for name, job in self._jobs.items()}

    def any_running(self) -> bool:
        return any(j["state"] == "running" for j in self._jobs.values())


# ---------------------------------------------------------------- the machine

async def _run(argv: list[str], timeout: float) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except OSError as exc:
        return 127, str(exc)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await procs.kill(proc)
        return -9, f"no answer within {timeout:g}s"
    return proc.returncode or 0, (out or b"").decode("utf-8", errors="replace").strip()


async def check_machine(cfg: Config) -> dict[str, Any]:
    """Docker, its disk, the way out, and git -- each said separately, because
    each has a different fix."""
    ok, said = await containers.docker_available(cfg.docker)
    docker = {"ok": ok, "version": said if ok else "", "detail": "" if ok else said}
    disk = None
    way_out = None
    if ok:
        measured = await containers.docker_disk(cfg.docker)
        if measured is not None:
            free, total = measured
            disk = {"free_gb": round(free / 1024 ** 3, 1), "total_gb": round(total / 1024 ** 3, 1),
                    "need_gb": SETUP_DISK_GB, "low": free < SETUP_DISK_GB * 1024 ** 3}
        try:
            way_out = await egress.self_test(cfg.docker)
        except Exception as exc:
            way_out = {"ok": False, "checks": [], "detail": str(exc)}
    code, out = await _run(["git", "--version"], 20)
    git = {"ok": code == 0, "version": out.replace("git version", "").strip() if code == 0 else ""}
    return {"checked_at": time.time(), "docker": docker, "disk": disk, "egress": way_out,
            "git": git}


def can_start_docker() -> bool:
    """Only where there is one obvious way to: Docker Desktop on a Mac."""
    return sys.platform == "darwin" and shutil.which("open") is not None


async def start_docker(cfg: Config, wait_s: float = 120.0) -> str:
    if not can_start_docker():
        raise RuntimeError("Fabrika can only start Docker Desktop on a Mac. Start Docker yourself.")
    code, out = await _run(["open", "-a", "Docker"], 30)
    if code != 0:
        raise RuntimeError(f"`open -a Docker` failed: {out or code}")
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        ok, _ = await containers.docker_available(cfg.docker)
        if ok:
            return "Docker is running."
        await asyncio.sleep(3)
    raise RuntimeError(f"Docker Desktop opened, but the engine did not answer within {wait_s:g}s.")


# ---------------------------------------------------------------- accounts

async def install_route(route: RouteConfig) -> str:
    """Run the route's own `install_command` on this machine -- the command its
    card already shows, never anything a client sent."""
    argv = shlex.split(route.install_command or "")
    if not argv:
        raise RuntimeError(f"{route.label or route.name} names no install command.")
    if shutil.which(argv[0]) is None:
        raise RuntimeError(f"`{argv[0]}` is not on this machine, so `{route.install_command}` "
                           "cannot run. Install Node.js, then try again.")
    code, out = await _run(argv, 600)
    if code != 0:
        raise RuntimeError(f"`{route.install_command}` exited {code}:\n{out[-1500:]}")
    return f"Ran `{route.install_command}`."


def route_takes_key(route: RouteConfig) -> bool:
    """A command-line route that spends a key rather than a sign-in."""
    return route.kind == "cli" and bool(route.key_env)


def connected_routes(cfg: Config, statuses: list[dict[str, Any]]) -> list[str]:
    """The routes that can carry a call now: the provider key's route when a key
    is set, a keyed route that is on and has its key, and a signed-in plan."""
    by_name = {s["name"]: s for s in statuses}
    out = []
    for name, route in cfg.routes.items():
        if not route.enabled:
            continue
        if name == cfg.default_route:
            if route.kind == "api" and route.api_key:
                out.append(name)
            continue
        if route.kind != "cli":
            continue
        if route_takes_key(route):
            if route.api_key:
                out.append(name)
        elif (by_name.get(name) or {}).get("state") == "ok":
            out.append(name)
    return out


# ---------------------------------------------------------------- harnesses

def harness_tag(cfg: Config, route: RouteConfig) -> str:
    """Built on the plain agent image: the image a command-line route's sealed
    completions already run on, and one that exists before any project does.
    The harness stage is the same Dockerfile lines a project's layer uses, so
    Docker's cache carries the expensive half over when a project is added."""
    return containers.harness_image_tag(cfg.docker.agent_image, route)


async def harness_ready(cfg: Config, route: RouteConfig) -> bool:
    return await containers.image_exists(harness_tag(cfg, route), cfg.docker)


async def build_harness(cfg: Config, route: RouteConfig) -> str:
    return await containers.harness_image(cfg.docker.agent_image, route, cfg.docker)


# ---------------------------------------------------------------- the crew

def crew_plan(cfg: Config, build: str, check: str, preset: str) -> tuple[dict, dict, dict]:
    """Sides, levels and every agent's level from two choices and a preset --
    the same three starting points the agent configuration screen offers."""
    if preset not in PRESETS:
        raise ValueError(f"preset is one of {', '.join(PRESETS)}, not {preset!r}")
    suggested = staffing_suggestions(cfg)
    down = {"deep": "standard", "standard": "light", "light": "light"}
    levels = {}
    for route in dict.fromkeys((build, check)):
        preset_levels = suggested["levels"].get(route) or {
            lv: c.model_dump() for lv, c in (cfg.levels.get(route) or {}).items()}
        if not preset_levels:
            raise ValueError(f"route {route!r} has no levels to staff from. Set them on the agent "
                             "configuration screen.")
        levels[route] = {lv: {"model": c["model"], "reasoning_effort": c.get("reasoning_effort") or ""}
                         for lv, c in preset_levels.items()}
    roles = {}
    for name in cfg.roles:
        base = suggested["roles"].get(name, "standard")
        level = ("deep" if preset == "deep"
                 else down[base] if preset == "lean" and name not in LEAN_KEEP_DEEP
                 else base)
        roles[name] = {"level": level, "side": "", "pin": None}
    return {"build": build, "check": check, "neither": "build"}, levels, roles


def staff(cfg: Config, build: str, check: str, preset: str) -> None:
    staffing, levels, roles = crew_plan(cfg, build, check, preset)
    write_staffing(cfg, staffing, levels, roles)


def crew_summary(cfg: Config) -> dict[str, Any]:
    staffing = cfg.staffing.model_dump() if cfg.staffing else None
    return {"confirmed": staffing is not None, "staffing": staffing,
            "independence": independence_problems(cfg)}
