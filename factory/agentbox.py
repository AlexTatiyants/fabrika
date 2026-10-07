"""A sealed container for every CLI completion, one per call.

As a subprocess on this machine -- `claude -p` or `codex exec`, started in
whatever directory the console happens to be in -- a completion is only as
contained as its CLI's own switches. Claude's can be launched with
`--tools ""` and do nothing but answer. Codex has no such switch -- shell,
browser and computer use are on by default -- so a reviewer or a plan checker
answering through it could read any file here and reach any server here.
Being asked for a structured answer is not a reason to be let loose on the
machine that asked.

So every one runs in a container of its own, made from a plain glibc image plus
the route's harness layer, on the shared agents network: sealed, with
the egress proxy attached so the CLI can reach its provider and nothing
private. Nothing is mounted. The credential is copied in the way a session's
is, and the container is removed when the answer is in, whatever the answer
was. A route with no `container_install` has nowhere sealed to answer from and
is refused.

The network is shared rather than per call, and that is a deliberate trade.
Docker's address pools hold a few dozen networks, a review panel asks a dozen
questions at once, and each feature's stack already takes one. Nothing in a
completion container listens on anything, so a neighbour on that network has
nothing to reach.
"""

from __future__ import annotations

import asyncio
import os
import stat
import tempfile
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Mapping, Sequence

from . import procs
from . import sessioncreds
from .config import Config, RouteConfig
from .containers import (HARNESS_ROOT, HOST_UNREACHABLE, NAME_PREFIX, DockerError,
                         harness_home, harness_image, hardening_argv)
from .egress import create_sealed, open_egress, resolver_argv, subnet_of
from .isolation import IsolationError

AGENTS_NETWORK = f"{NAME_PREFIX}-agents"
WORKDIR = "/tmp"
#: The harness's own commands first. Unlike a unit's container, nothing in here
#: is the project's, so there is no toolchain for the harness to shadow -- and a
#: route's completion `command` names its program the way it does on this
#: machine, by name.
BOX_PATH = f"{HARNESS_ROOT}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


async def _docker(argv: Sequence[str], timeout: float = 120.0) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except OSError as exc:
        return 127, str(exc)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await procs.kill(proc)
        return -9, f"timed out after {timeout:g}s"
    return (proc.returncode if proc.returncode is not None else -1,
            (out or b"").decode("utf-8", errors="replace").strip())


@dataclass
class Box:
    container: str
    docker: str
    env_file: Path | None = None
    bundle_env: Path | None = None
    extra: list[str] = field(default_factory=list)

    async def put(self, local: str, name: str) -> str:
        """Copy one file in, and say where it landed."""
        target = f"{WORKDIR}/{name}"
        code, out = await _docker([self.docker, "cp", local, f"{self.container}:{target}"])
        if code != 0:
            raise IsolationError(f"could not copy {name} into the completion's container: {out[-300:]}")
        return target

    def exec_argv(self, argv: Sequence[str]) -> list[str]:
        out = [self.docker, "exec", "--interactive", "--workdir", WORKDIR]
        if hasattr(os, "getuid"):
            out += ["--user", f"{os.getuid()}:{os.getgid()}"]
        for env_file in (self.bundle_env, self.env_file):
            if env_file is not None:
                out += ["--env-file", str(env_file)]
        return [*out, *self.extra, self.container, *argv]


def _env_file(values: Mapping[str, str], directory: Path) -> Path | None:
    """Variables for the CLI, on a path rather than a command line.

    A value in `--env` is a value in the argv of a process every user on this
    machine can list. A key belongs in a file only this user can read.
    """
    lines = [f"{k}={v}" for k, v in values.items() if v is not None and "\n" not in str(v)]
    if not lines:
        return None
    path = directory / "route.env"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path


async def collect_sidecars(docker_binary: str, container: str, route: RouteConfig,
                           home: str, evidence_path: str | Path) -> None:
    """Copy the tool's own session logs out before its container goes.

    Where a tool writes its plan's windows only into its session log, that log
    is the gauge -- and in a sealed container it lives in the container's home.
    Copied to a folder of Fabrika's own, never into this person's `~/.codex`:
    their history of their own sessions is theirs. Best effort throughout; a
    gauge is never worth failing a call over.
    """
    from . import meters

    rel = meters.sidecar_dir(route)
    if not rel or not home:
        return
    base = meters.use_mirror(evidence_path)
    target = meters.mirror_home(base, route.name) / rel
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    # `dir/.` copies what is in it into a folder that already exists, rather
    # than nesting a second `sessions` inside the first.
    await _docker([docker_binary, "cp", f"{container}:{home.rstrip('/')}/{rel}/.", str(target)],
                  timeout=60)
    meters.prune_mirror(base, route.name)


@asynccontextmanager
async def completion_box(route: RouteConfig, config: Config,
                         route_env: Mapping[str, str]) -> AsyncIterator[Box]:
    docker = config.docker
    if not route.container_install:
        raise IsolationError(
            f"route {route.name!r} has no `container_install`, so there is no sealed "
            "container it can answer from, and it does not answer from this machine.")
    problem = await create_sealed(docker, AGENTS_NETWORK) or await open_egress(
        docker, AGENTS_NETWORK)
    if problem:
        raise IsolationError(problem)
    home = harness_home(route)
    try:
        image = await harness_image(docker.agent_image, route, docker)
    except DockerError as exc:
        raise IsolationError(str(exc)) from None

    name = f"{NAME_PREFIX}-answer-{uuid.uuid4().hex[:12]}"
    fills = {"base_url": route.base_url or config.api.base_url or "", "worktree": WORKDIR,
             "factory_root": ""}
    run = [docker.binary, "run", "-d", "--rm", "--name", name,
           "--network", AGENTS_NETWORK, "--add-host", HOST_UNREACHABLE]
    if docker.memory:
        run += ["--memory", docker.memory]
    if docker.cpus:
        run += ["--cpus", str(docker.cpus)]
    if docker.pids_limit:
        run += ["--pids-limit", str(docker.pids_limit)]
    run += hardening_argv(docker)
    run += resolver_argv(await subnet_of(docker, AGENTS_NETWORK))
    for key, value in route.container_env.items():
        for token, filled in fills.items():
            value = value.replace("{" + token + "}", filled)
        run += ["--env", f"{key}={value}"]
    run += ["--env", f"HOME={home}", "--env", f"PATH={BOX_PATH}",
            "--entrypoint", "sh", image, "-c", "sleep infinity"]

    bundle = None
    scratch = Path(tempfile.mkdtemp(prefix="factory-answer-"))
    started = False
    try:
        code, out = await _docker(run, timeout=180)
        if code != 0:
            raise IsolationError(f"could not start a container for route {route.name!r}: {out[-600:]}")
        started = True
        try:
            bundle = await sessioncreds.prepare(route, config)
            await sessioncreds.install(bundle, name, docker)
        except sessioncreds.CredentialError as exc:
            raise IsolationError(str(exc)) from None
        yield Box(container=name, docker=docker.binary,
                  env_file=_env_file(route_env, scratch),
                  bundle_env=bundle.env_file if bundle else None)
    finally:
        if started:
            await collect_sidecars(docker.binary, name, route, home, config.evidence_path)
            await _docker([docker.binary, "rm", "-f", name], timeout=60)
        if bundle is not None:
            bundle.cleanup()
        for leftover in scratch.glob("*"):
            leftover.unlink(missing_ok=True)
        scratch.rmdir()
