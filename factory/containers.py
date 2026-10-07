"""Where gates actually run.

A worktree isolates files. It does nothing about port 5432, a test database, a
package cache or a dev server, so two features running their suites at the same
time on one machine can interfere and produce results that look like signal.
This module is the other half of that boundary.

The rule that matters most here is that there is **no silent fallback**. If a
project's environment says its gates run in a container and no container can be
had, the run fails and says why. Quietly dropping to the host would leave you
with results you believe are isolated and are not, which is worse than no
results at all.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from . import procs
from . import composeenv, isolation
from .config import Config, DockerConfig
from .egress import (close_egress, create_sealed, open_egress, release_blocks,
                     remove_sealed, reserve_blocks, resolver_file, subnet_of)
from .egress_proxy import container_range
from .gates import Execution, LocalRunner, Runner, with_env
from .projects import Project
from .schemas import EnvironmentSpec


class DockerError(RuntimeError):
    pass


#: Point the host's own name at the container's loopback. Belt and braces: the
#: sealed network is what actually keeps a container off this machine (see
#: `isolation.py`), and this only stops a tool that looks the name up from
#: getting an answer that would read as though it could.
HOST_UNREACHABLE = "host.docker.internal:127.0.0.1"


#: This repository's root, so a route can name a file inside it -- the pinned
#: requirements a harness installs from, or the driver script that *is* the
#: harness -- relative to the factory rather than to whatever the cwd happens
#: to be when an image is built.
FACTORY_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# availability
# --------------------------------------------------------------------------


async def _run(argv: list[str], timeout: float = 60.0, cwd: Path | None = None) -> Execution:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return Execution(exit_code=127, output=f"could not start {argv[0]!r}: {exc}", started=False)
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return Execution(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            output=(stdout or b"").decode("utf-8", errors="replace"),
        )
    except asyncio.TimeoutError:
        await procs.kill(proc)
        return Execution(exit_code=-9, timed_out=True, output=f"timed out after {timeout}s")


async def _run_id(argv: list[str], timeout: float = 300.0) -> tuple[int, str, str]:
    """Run a command whose useful output is an id on stdout.

    `_run` folds stderr into stdout, which is right for a gate -- one stream, in
    order, is what a human wants to read. It is wrong here: `compose run -d`
    writes its progress to stderr and the container id to stdout, and merging
    them leaves no reliable way to tell which line is the id.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return 127, "", f"could not start {argv[0]!r}: {exc}"
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await procs.kill(proc)
        return -9, "", f"timed out after {timeout}s"
    return (proc.returncode if proc.returncode is not None else -1,
            (out or b"").decode("utf-8", errors="replace"),
            (err or b"").decode("utf-8", errors="replace"))


def _last_id(stdout: str) -> str:
    """The container id, or nothing. Never a guess."""
    for line in reversed(stdout.strip().splitlines()):
        token = line.strip()
        if len(token) >= 12 and all(c in "0123456789abcdef" for c in token):
            return token
    return ""


async def docker_available(docker: DockerConfig) -> tuple[bool, str]:
    """Both halves: the CLI on PATH, and a daemon that answers."""
    if shutil.which(docker.binary) is None:
        return False, (
            f"{docker.binary!r} is not on PATH. Install Docker, or set this project's "
            "environment to `host` if you accept that its gates share this machine."
        )
    probe = await _run([docker.binary, "info", "--format", "{{.ServerVersion}}"], timeout=30)
    if probe.exit_code != 0:
        return False, (
            f"the {docker.binary} daemon is not answering: {probe.output.strip()[:300]}"
        )
    return True, probe.output.strip()


# --------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------


#: What to do about a full Docker disk, said once for every place that finds one.
DISK_REMEDY = ("Free some -- `docker system prune` removes stopped containers, unused "
               "networks and dangling images, and `docker builder prune` the build cache -- "
               "or raise the disk limit in Docker Desktop's settings, then run it again.")


def _size(n: int) -> str:
    return f"{n / 1024 ** 3:.1f} GB"


async def docker_disk(docker: DockerConfig) -> tuple[int, int] | None:
    """Free and total bytes on the disk Docker keeps its images and volumes on.

    Asked of a container rather than of this machine, because under Docker
    Desktop that disk is a virtual one inside its VM: the host can have
    hundreds of gigabytes free while every image build and database in Docker
    fails on "no space left on device". An image already here is used, never
    pulled -- a check for a full disk that begins by downloading something is
    no check. None when it cannot be measured, which is never read as "full".
    """
    for image in dict.fromkeys((docker.egress_image, docker.agent_image)):
        if not image:
            continue
        result = await _run([docker.binary, "run", "--rm", "--pull=never", "--network=none",
                             "--entrypoint", "df", image, "-Pk", "/"], timeout=60)
        if result.exit_code != 0:
            continue
        rows = [r.split() for r in result.output.strip().splitlines()[1:] if r.strip()]
        try:
            _, blocks, _, available = rows[-1][:4]
            return int(available) * 1024, int(blocks) * 1024
        except (IndexError, ValueError):
            continue
    return None


async def low_disk(docker: DockerConfig, need_gb: float) -> str:
    """One paragraph when Docker's disk has less than `need_gb` free, else empty."""
    measured = await docker_disk(docker)
    if measured is None:
        return ""
    free, total = measured
    if free >= need_gb * 1024 ** 3:
        return ""
    return (f"Docker is out of disk space: {_size(free)} free of {_size(total)}. Images, "
            f"databases and builds all live on that disk, so none of them can start. "
            + DISK_REMEDY)


def image_tag(project_id: str, spec: EnvironmentSpec) -> str:
    """Content-addressed by the Dockerfile, so an edited environment rebuilds and
    an unchanged one never does."""
    digest = hashlib.sha256(
        (spec.dockerfile + "\0" + spec.workdir
         + "".join(f"\0{v}" for v in (getattr(spec, "dockerfile_target", ""),
                                       getattr(spec, "build_context", "")) if v)).encode("utf-8")
    ).hexdigest()[:12]
    return f"{NAME_PREFIX}/{_safe_project(project_id)}:{digest}"


def _safe_project(project_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in project_id).lower()


#: Everything this factory creates in Docker wears this, so `docker ps` and
#: `docker images` separate ours from the machine's own work at a glance.
#: Containers called `integ-be` and `slt-pg` look like somebody's project, and
#: can sit running for days without anyone noticing.
NAME_PREFIX = "fabrika"

#: The prefix this tool's resources wore under its earlier name. Kept only so
#: the sweep can still recognise -- and therefore still reclaim -- everything
#: minted under it. Without this every such image becomes permanent garbage,
#: which is the leak this sweep exists to end.
LEGACY_NAME_PREFIX = "factory"


async def image_exists(tag: str, docker: DockerConfig) -> bool:
    return (await _run([docker.binary, "image", "inspect", tag], timeout=30)).exit_code == 0


async def build_image(project: Project, docker: DockerConfig) -> str:
    """Resolve a project's environment to an image tag, building if needed.

    Returns "" for a `host` environment, which is the one case where running
    without a container is a stated choice rather than a silent downgrade.
    """
    spec = project.environment
    if spec is None or spec.kind == "host":
        return ""

    ok, detail = await docker_available(docker)
    if not ok:
        raise DockerError(
            f"project {project.id!r} runs its gates in a container ({spec.kind}) but {detail}"
        )

    if spec.kind == "reuse" and not spec.dockerfile_path:
        if not spec.image:
            raise DockerError(
                f"project {project.id!r} has environment kind 'reuse' but names no image."
            )
        if not await image_exists(spec.image, docker):
            raise DockerError(
                f"image {spec.image!r} is not present locally. Pull or build it, or change this "
                "project's environment to derive from it."
            )
        return spec.image

    if not spec.dockerfile.strip():
        if spec.dockerfile_path:
            raise DockerError(
                f"project {project.id!r} builds from {spec.dockerfile_path}, which is not "
                "committed on the branch features start from.")
        raise DockerError(
            f"project {project.id!r} has environment kind {spec.kind!r} but an empty Dockerfile."
        )

    tag = image_tag(project.id, spec)
    if await image_exists(tag, docker):
        return tag

    # Built with the repository as context so a Dockerfile can COPY the lockfile
    # it installs from. It must not copy the source tree -- the feature worktree
    # is mounted at run time and would shadow it.
    with tempfile.TemporaryDirectory(prefix="factory-build-") as tmp:
        dockerfile = Path(tmp) / "Dockerfile"
        dockerfile.write_text(spec.dockerfile, encoding="utf-8")
        result = await _run(build_argv(docker.binary, spec, dockerfile, tag, project.repo_path),
                            timeout=docker.build_timeout_s)
    if result.exit_code != 0:
        raise DockerError(
            f"building the environment for {project.id!r} failed:\n{result.output[-3000:]}"
        )
    return tag


def build_argv(binary: str, spec: EnvironmentSpec, dockerfile: Path, tag: str,
               repo: Path) -> list[str]:
    """`docker build` for an environment.

    The text is always the one on the environment -- read from the base branch
    when it lives in the repository -- written to a file of its own, so what is
    built is what was measured and not whatever a working copy holds. A stage
    of the project's own Dockerfile is built from that file's own directory,
    or the context the survey read from its compose file, with `--target`.
    """
    argv = [binary, "build", "-f", str(dockerfile), "-t", tag]
    own = (getattr(spec, "dockerfile_path", "") or "").strip()
    target = (getattr(spec, "dockerfile_target", "") or "").strip()
    if target:
        argv += ["--target", target]
    context = repo
    if own:
        rel = (getattr(spec, "build_context", "") or "").strip() or str(Path(own).parent)
        context = (repo / rel).resolve()
        if not context.is_relative_to(repo.resolve()):
            raise DockerError(f"the build context {rel!r} is outside the repository")
    return argv + [str(context)]


def hardening_argv(docker: DockerConfig) -> list[str]:
    """What this container may not do, as arguments.

    The agent inside runs with its own permissions turned off, and this is the
    other half of that trade: the container is the containment, so it is worth
    making the container small. Measured with a live agent doing real work
    under all of it -- it ran the project's checks, fixed a failing test, and
    noticed nothing.
    """
    argv: list[str] = []
    if docker.drop_capabilities:
        argv += ["--cap-drop", "ALL"]
    if docker.no_new_privileges:
        argv += ["--security-opt", "no-new-privileges"]
    if docker.read_only_root:
        # The worktree is a mount and stays writable. Everything else is the
        # image, which nothing in a run is supposed to be editing -- except the
        # few paths that must be, and those arrive as tmpfs rather than as
        # holes in the image.
        argv += ["--read-only"]
        for path, options in docker.writable_paths.items():
            argv += ["--tmpfs", f"{path}:{options}" if options else path]
    return argv


#: Where every harness lives in a unit's image, and the only thing carried out
#: of the stage it was built in.
HARNESS_ROOT = "/opt/harness"
#: What the finished image's own check prints when it refuses, so the reason can
#: be told apart from whatever else the build printed.
HARNESS_CHECK_MARK = "fabrika-harness-check:"


def _harness_files(route) -> list[tuple[Path, str]]:
    """The route's build files as (where it is here, where it lands), in order."""
    out = []
    for source, target in sorted((route.container_build_files or {}).items()):
        origin = Path(source)
        out.append((origin if origin.is_absolute() else (FACTORY_ROOT / origin), target))
    return out


def harness_home(route) -> str:
    """The harness's HOME in there. Made in the image, not mounted: `docker cp`,
    which is how a credential gets in, cannot write into a tmpfs."""
    return (route.container_env or {}).get("HOME", "") or "/agent-home"


def harness_check(route) -> list[str]:
    """The command that has to succeed in the finished image."""
    if route.container_check:
        return list(route.container_check)
    command = route.container_session_command or []
    return [command[0], "--version"] if command else []


def _install_lines(route) -> list[str]:
    return [line for line in (ln.strip() for ln in route.container_install or []) if line]


def harness_image_tag(base_image: str, route) -> str:
    """Content-addressed by everything that goes into the layer.

    Keyed on the whole Dockerfile -- the base tag, so a rebuilt project
    environment gets a rebuilt harness layer rather than an agent running
    against last week's dependencies; the image it is built in, its lines and
    its check -- and on the *contents* of the files, so editing the driver or
    the pinned requirements it installs from rebuilds the layer instead of
    quietly running the previous one. The Dockerfile whole rather than its
    fields, because a change to how the layer is assembled is a change to the
    layer: a fixed check once went unrun for every image built before it.
    """
    files = _harness_files(route)
    staged = [(f"payload-{index}", target) for index, (_, target) in enumerate(files)]
    parts = [harness_dockerfile(base_image, route, staged)]
    for origin, target in files:
        try:
            body = origin.read_bytes()
        except OSError:
            body = b""
        parts.append(f"{target}\0{hashlib.sha256(body).hexdigest()}")
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{NAME_PREFIX}-harness/{_safe_project(route.name)}:{digest}"


def harness_dockerfile(base_image: str, route, staged: Sequence[tuple[str, str]]) -> str:
    """The two-stage build that puts one harness into `base_image`.

    The harness is installed in its own stock image, where the runtime it needs
    is known to be there, and only `/opt/harness` crosses into the project's
    image. Nothing runs a package manager against the project's image: it only
    has to be a glibc Linux with a shell. `staged` is (file in the build
    context, where it lands).

    The last line runs the harness in the finished image. A harness that cannot
    load there -- a musl image, a glibc too old, a missing library -- fails the
    build with the loader's own words, which is where the reason is cheapest to
    read, rather than failing every session that tries to use it.
    """
    check = shlex.join(harness_check(route))
    home = shlex.quote(harness_home(route))
    # The mark is quoted apart from its word so that the build log's echo of
    # this command never contains what the command prints: Docker shows every
    # RUN line as it starts, and a mark spelled whole in here was found in the
    # log of every build, failed or not.
    probe = (
        "if ls /lib/ld-musl-* >/dev/null 2>&1 && "
        "! ls /lib/ld-linux* /lib64/ld-linux* >/dev/null 2>&1; "
        f"then echo '{HARNESS_CHECK_MARK}' musl; exit 1; fi; "
        f"out=$({check} 2>&1) || {{ printf '%s\\n' \"$out\"; "
        f"echo '{HARNESS_CHECK_MARK}' failed; exit 1; }}"
    )
    lines = [
        f"FROM {route.container_build_image} AS harness",
        "USER root",
        *(f"COPY {name} {target}" for name, target in staged),
        *(f"RUN {line}" for line in _install_lines(route)),
        f"RUN chmod -R a+rX {HARNESS_ROOT}",
        f"FROM {base_image} AS unit",
        "USER root",
        f"COPY --from=harness {HARNESS_ROOT} {HARNESS_ROOT}",
        f"RUN mkdir -p {home} && chmod 0777 {home}",
    ]
    if check:
        lines.append(f"RUN {probe}")
    return "\n".join(lines) + "\n"


#: A library the loader could not find, and the Debian/Ubuntu package that has it.
_LIBRARY_PACKAGES = {
    "libstdc++.so.6": "libstdc++6",
    "libgcc_s.so.1": "libgcc-s1",
    "libz.so.1": "zlib1g",
}


def harness_build_problem(route, output: str) -> str:
    """One paragraph a person can act on, read off a failed harness build.

    The build log says what failed in the build's terms; this says it in the
    project's. It is the first paragraph of the error, and the log follows it.
    """
    label = route.harness_label or route.name
    if "no space left on device" in output:
        return (f"Docker ran out of disk space while putting {label} into this project's "
                "image. " + DISK_REMEDY)
    if f"{HARNESS_CHECK_MARK} musl" in output:
        return (f"{label} can't run in this project's image: the image is built on musl "
                "(Alpine, most likely) and the harness needs glibc. Base the image the "
                "checks run in on a Debian or Ubuntu variant -- `node:24-bookworm-slim` "
                "rather than `node:24-alpine`, say.")
    glibc = re.search(r"version `GLIBC_([0-9.]+)' not found", output)
    if glibc:
        return (f"{label} needs glibc {glibc.group(1)} or newer, and this project's image "
                "has an older one. Base the image the checks run in on a current Debian "
                "or Ubuntu release.")
    missing = re.search(r"error while loading shared libraries: ([^:\s]+)", output)
    if missing:
        library = missing.group(1)
        package = _LIBRARY_PACKAGES.get(library)
        fix = (f" On Debian or Ubuntu, add `apt-get install -y {package}` to it."
               if package else "")
        return (f"{label} needs {library}, which this project's image doesn't have."
                f"{fix}")
    if re.search(r'/bin/sh["\']?: (?:stat /bin/sh: )?(?:no such file|not found)', output):
        return (f"This project's image has no shell, so {label} can't be put into it. "
                "Base the image the checks run in on one that has `/bin/sh` -- a Debian "
                "or Ubuntu variant.")
    if f"{HARNESS_CHECK_MARK} failed" in output:
        # What the check printed, without the step-and-seconds prefix the
        # build log puts on every line: the last thing before the mark.
        said = [m.group(1).strip() for m in re.finditer(
                    r"^#\d+ [\d.]+ (.+)$", output, re.M)
                if HARNESS_CHECK_MARK not in m.group(1)]
        tail = f" It said: {said[-1]}" if said else ""
        return f"{label} was installed but doesn't run in this project's image.{tail}"
    if re.search(r"^\s*> \[harness ", output, re.M):
        return (f"Building {label} in its own image ({route.container_build_image}) "
                "failed. That is Fabrika's side, not the project's: the image or a "
                "download it needs could not be reached, or an install line is wrong.")
    return f"Putting {label} into this project's image failed."


async def harness_image(base_image: str, route, docker: DockerConfig) -> str:
    """The unit's image plus the harness that will run inside it.

    A layer rather than a separate image: the agent has to run the project's
    own checks, which means standing in the image that has the project's own
    dependencies. Building beside it and mounting the tools in would be a
    second environment to keep in step with the first.

    Cached in Docker's image store by content, like `build_image`, so the cost
    is paid once per (environment, harness) pair and not once per unit.
    """
    if not base_image:
        raise DockerError(
            "a session cannot run in a container for a project that has no image."
        )
    if not _install_lines(route):
        raise DockerError(
            f"route {route.name!r} runs its sessions in the unit's container but says "
            "nothing about how to install itself there; give it `container_install`."
        )
    if not route.container_build_image:
        raise DockerError(
            f"route {route.name!r} names no image to build its harness in, so it could "
            "only be installed with whatever runtime the project's image happens to "
            "have; give it `container_build_image`."
        )
    files = _harness_files(route)
    outside = [t for _, t in files
               if not (t == HARNESS_ROOT or t.startswith(HARNESS_ROOT + "/"))]
    if outside:
        raise DockerError(
            f"route {route.name!r} puts {', '.join(outside)} outside {HARNESS_ROOT}, and "
            f"only {HARNESS_ROOT} is carried out of the stage the harness is built in."
        )
    tag = harness_image_tag(base_image, route)
    if await image_exists(tag, docker):
        return tag

    with tempfile.TemporaryDirectory(prefix="factory-harness-build-") as tmp:
        context = Path(tmp)
        staged: list[tuple[str, str]] = []
        for index, (origin, target) in enumerate(files):
            if not origin.is_file():
                raise DockerError(
                    f"route {route.name!r} builds its image from {origin}, which is "
                    "not a file on this machine."
                )
            staged_as = f"payload-{index}"
            shutil.copyfile(origin, context / staged_as)
            staged.append((staged_as, target))

        dockerfile = context / "Dockerfile"
        dockerfile.write_text(harness_dockerfile(base_image, route, staged), encoding="utf-8")
        result = await _run(
            [docker.binary, "build", "--progress=plain", "-f", str(dockerfile), "-t", tag,
             str(context)],
            timeout=docker.build_timeout_s,
        )
    if result.exit_code != 0:
        raise DockerError(
            f"{harness_build_problem(route, result.output)}\n\n"
            f"putting route {route.name!r} into the unit's image failed:\n"
            f"{result.output[-3000:]}"
        )
    return tag


async def sweep_images(
    keep: Mapping[str, str], docker: DockerConfig,
    harness_keep: Sequence[str] = (),
) -> list[str]:
    """Drop the environment images nothing can reach any more.

    `image_tag` is content-addressed on the Dockerfile, so every edit to an
    environment mints a new tag and orphans the old one, and nothing else
    removes it. One project reached twenty-two tags of which one was live -- sixteen GB,
    and another fifty in the untagged layers underneath -- and the disk filled
    during a run, which is the most expensive moment to find out.

    `keep` maps project id to the tag that project resolves to right now.
    A project absent from it is left entirely alone: not knowing what is
    current is a reason to delete nothing, never a reason to delete all of it.

    `fabrika-prepared:*` is swept too. Those are per-session commits that
    `stop()` removes on the way out, so any that survive to a boot are the
    residue of a process that was killed -- which is exactly how this one
    collected two of them at 2.1 GB each.

    Plain `rmi`, never `-f`: an image backing a container is refused, and being
    refused is the correct outcome. Returns what it actually removed.
    """
    listed = await _run(
        [docker.binary, "images", "--format", "{{.Repository}}:{{.Tag}}"], timeout=60)
    if listed.exit_code != 0:
        return []

    wanted = set(keep.values()) | set(harness_keep)
    prefixes = {f"{prefix}/{_safe_project(pid)}:"
                for pid in keep
                for prefix in (NAME_PREFIX, LEGACY_NAME_PREFIX)}
    prepared = (f"{NAME_PREFIX}-prepared:", f"{LEGACY_NAME_PREFIX}-prepared:")
    # The harness layers, which are the largest thing here: a project image
    # plus a coding agent runs from half a gigabyte to a gigabyte and a half,
    # and every edit to an environment or to a pinned requirement mints a new
    # one. Swept on the same terms as everything else -- only tags this factory
    # minted, and only when the live one can be named.
    if harness_keep:
        prepared = prepared + (f"{NAME_PREFIX}-harness/",)
    doomed = []
    for ref in (listed.output or "").splitlines():
        ref = ref.strip()
        if not ref or ref.endswith(":<none>") or ref in wanted:
            continue
        if ref.startswith(prepared) or any(ref.startswith(p) for p in prefixes):
            doomed.append(ref)

    removed = []
    for ref in doomed:
        result = await _run([docker.binary, "rmi", ref], timeout=120)
        if result.exit_code == 0:
            removed.append(ref)
    return removed


# --------------------------------------------------------------------------
# running
# --------------------------------------------------------------------------


class DockerRunner:
    """One container per command, the sandbox mounted, on a sealed network.

    Per command rather than one long-lived container: a gate that wedges takes
    nothing else down with it, and there is no state carried between gates that
    could make one gate's result depend on another's.

    Setup is the exception, and it has to be. `pip install` writes into the
    container's own filesystem, not into the mounted worktree, so with a
    throwaway container per command everything setup installed went out with the
    container that installed it -- and the gates then ran against an image with
    the project's runtime dependencies and none of its tools. The symptom was a
    `pip install` that took twenty seconds and succeeded, followed immediately
    by `ruff: not found`.

    So setup runs inside one container held open for the duration, and that
    container is then committed to an image the gates run from. Both properties
    survive: setup persists, and every gate still gets its own container.

    It persists for exactly as long as this runner does. The committed image is
    removed in `down()`, so a later runner starts from the project's image again
    with none of it. Setup is per-runner, not per-checkout.
    """

    kind = "docker"
    #: Every container this runner starts is on an `--internal` network of its
    #: own. A command that asks for the network gets the one the egress proxy
    #: is attached to, and reaches public addresses through it; one that does
    #: not gets the one it is not attached to, and reaches nothing.
    enforces_egress_policy = True
    carries_setup = False

    def __init__(self, image: str, workdir: str, docker: DockerConfig) -> None:
        self.image = image
        self.workdir = workdir or "/work"
        self.docker = docker
        self._session: str = ""          # container held open for setup
        self.session_env: dict[str, str] = {}   # set while a test session is open
        self._sealed: str = ""           # no way out at all, created on demand
        self._egress: str = ""           # a way out through the proxy, likewise
        self._egress_subnet: str = ""    # its subnet, which its resolver file names
        self._network_error: str = ""
        self._committed: str = ""        # image made from it, removed on down()
        self._session_error: str = ""
        #: `key=value` labels on every container this runner starts. A preview
        #: sets one, so a server killed with a preview open leaves containers
        #: its next boot can name and remove.
        self.labels: list[str] = []
        #: Put into this runner's network names, which cannot carry a label
        #: here: `create_sealed` makes them. A preview's are
        #: `fabrika-sealed-preview-*`, so a boot can find what a killed one left.
        self.network_infix = ""

    def argv(self, command: str, *, cwd: Path, network: bool, name: str,
             harden: bool = True) -> list[str]:
        argv = [
            self.docker.binary, "run", "--rm",
            "--name", name,
            "--volume", f"{cwd}:{self.workdir}",
            "--workdir", self.workdir,
        ]
        for label in self.labels:
            argv += ["--label", label]
        if self.docker.run_as_host_user and hasattr(os, "getuid"):
            # Otherwise everything the gates write into the worktree comes back
            # owned by root, and the next thing to touch that tree is a human.
            argv += ["--user", f"{os.getuid()}:{os.getgid()}"]
            # That uid owns no home directory in the image, so anything that
            # caches -- npm, pip, yarn -- tries to write to `/` and dies with
            # EACCES. Point HOME at the one directory that is certainly
            # writable. Without this `npm install` cannot run at all, which is
            # how a project reaches gate 0 with an environment that cannot
            # execute its own gates.
            argv += ["--env", f"HOME={self.workdir}",
                     "--env", f"NPM_CONFIG_CACHE={self.workdir}/.npm-cache",
                     "--env", f"XDG_CACHE_HOME={self.workdir}/.cache"]
        # Always a sealed network, never the default bridge: the bridge routes
        # to this machine, and a developer's own copy of the project is
        # listening there on its usual port. `none` only if the network could
        # not be made, which `_network` reports before anything gets here.
        #
        # Checks get the network with no way out. They execute model-authored
        # code against a private repository, and a test that reaches a third
        # party also answers differently when somebody else has an outage --
        # which arrives as a finding about the feature. What asks for the
        # network (setup's installs, an agent's model provider) gets the one
        # the proxy is on, and the resolver that sends outside names to it.
        # Nothing else: no proxy variables, which a JVM never read anyway.
        argv += ["--network", (self._egress if network else self._sealed) or "none"]
        if network and self._egress_subnet:
            argv += ["--volume", f"{resolver_file(self._egress_subnet)}:/etc/resolv.conf:ro"]
        if self.docker.memory:
            argv += ["--memory", self.docker.memory]
        if self.docker.cpus:
            argv += ["--cpus", str(self.docker.cpus)]
        if self.docker.pids_limit:
            argv += ["--pids-limit", str(self.docker.pids_limit)]
        argv += ["--add-host", HOST_UNREACHABLE]
        if harden:
            argv += hardening_argv(self.docker)
        argv += list(self.docker.extra_args)
        # An image with its own ENTRYPOINT would otherwise swallow the command.
        argv += ["--entrypoint", self.docker.shell, self.image, "-c",
                 with_env(command, self.session_env)]
        return argv

    @property
    def session_id(self) -> str:
        """The held container, for callers that must exec into it themselves."""
        return self._session

    async def open_session(self, cwd: Path, *, network: bool = True,
                           harden: bool = True) -> None:
        """Hold one container open so a series of commands share a filesystem.

        `harden` is false for the one session that installs things. Dropping
        every capability is right for a container that runs checks and wrong
        for one running a package manager: `apt-get` drops to its own unwilling
        user and needs `setgroups`, `setegid` and `seteuid` to do it. Measured,
        by hardening this and watching a browser install fail with
        `setgroups 65534 failed` -- an environment that could not come up at
        all, reported as a failed setup command.

        Nothing is lost by the exemption: this container installs and is then
        committed to an image, and the session the gates and the agent get is
        opened from that image with every guard on.
        """
        if self._session:
            return
        name = f"{NAME_PREFIX}-setup-{uuid.uuid4().hex[:12]}"
        if not await self._network(network):
            self._session_error = self._network_error
            return
        argv = self.argv("sleep infinity", cwd=cwd, network=network, name=name,
                         harden=harden)
        argv.remove("--rm")
        argv.insert(argv.index("run") + 1, "-d")
        code, out, err = await _run_id(argv, timeout=300)
        container = _last_id(out) if code == 0 else ""
        if container:
            self._session = container
        else:
            said = (err or out or "no container id returned").strip()[-2000:]
            disk = await low_disk(self.docker, self.docker.min_free_gb)
            self._session_error = f"{disk}\n\n{said}" if disk else said

    async def _network(self, egress: bool) -> str:
        """This runner's sealed network, with or without the way out.

        Two per runner rather than one with the proxy coming and going: a
        check running while setup's proxy was still attached could use it, and
        the order of two awaits is not a boundary. Created on first use and
        removed in `down()`, so a crashed run leaves at most two behind, each
        labelled for sweeping. Returns "" and sets `_network_error` when it
        cannot -- and then nothing runs, rather than running unsealed.
        """
        current = self._egress if egress else self._sealed
        if current:
            return current
        kind = "egress" if egress else "sealed"
        name = f"{NAME_PREFIX}-{kind}-{self.network_infix}{uuid.uuid4().hex[:12]}"
        problem = await create_sealed(self.docker, name)
        if not problem and egress:
            problem = await open_egress(self.docker, name)
            if problem:
                await remove_sealed(self.docker, name)
        if problem:
            self._network_error = problem
            return ""
        if egress:
            self._egress = name
            self._egress_subnet = await subnet_of(self.docker, name)
        else:
            self._sealed = name
        return name

    async def start_service(self, command: str, *, cwd: Path, name: str) -> str:
        """Start a background process inside the session container.

        `exec -d` rather than a shell backgrounding trick: Docker detaches it
        for us, so nothing here writes `nohup`, `&` or a pid file. It needs the
        session container to exist -- a service and the tests that use it have
        to share a network namespace, and one container per command gives them
        different ones.

        Stopping is `discard_session`, which removes the container and every
        process in it. That is the whole teardown, and it cannot leak: it does
        not depend on a pid, on a signal reaching the right process, or on the
        command that started the service having exited cleanly.
        """
        if not self._session:
            return ("no session container is open, so a service would be started in a "
                    "container that is discarded before the tests run")
        result = await _run(
            [self.docker.binary, "exec", "-d", "-w", self.workdir,
             self._session, self.docker.shell, "-c", with_env(command, self.session_env)],
            timeout=60,
        )
        if result.exit_code != 0:
            return (result.output or "no output").strip()[-2000:]
        return ""

    async def discard_session(self) -> None:
        """Close the session WITHOUT committing it, killing anything inside.

        The sibling of `close_session`, and the difference is the whole point of
        having two. Setup's container is committed because what it installed has
        to outlive it. A test session's container holds running services and a
        migrated database, and none of that should survive into the next thing
        that runs.
        """
        if not self._session:
            return
        await _run([self.docker.binary, "rm", "-f", self._session], timeout=60)
        self._session = ""

    async def close_session(self) -> None:
        """Commit what setup installed, and run the gates from that."""
        if not self._session:
            return
        image = f"{NAME_PREFIX}-prepared:{uuid.uuid4().hex[:12]}"
        # See ComposeRunner.close_session: a commit inherits the container's
        # entrypoint, and `argv()` supplies its own.
        committed = await _run(
            [self.docker.binary, "commit", "--change", "ENTRYPOINT []",
             self._session, image], timeout=300)
        await _run([self.docker.binary, "rm", "-f", self._session], timeout=60)
        self._session = ""
        if committed.exit_code == 0:
            # A second setup session -- a warm-up -- is opened from the image
            # the first one made and commits on top of it. The one underneath
            # is still a parent layer, so removing its tag frees nothing it needs.
            if self._committed:
                await _run([self.docker.binary, "rmi", self._committed], timeout=120)
            self.image = image
            self._committed = image

    async def down(self) -> None:
        """The committed image is this run's, and nothing else's."""
        if self._session:
            await _run([self.docker.binary, "rm", "-f", self._session], timeout=60)
            self._session = ""
        if self._committed:
            await _run([self.docker.binary, "rmi", "-f", self._committed], timeout=120)
            self._committed = ""
        for name in (self._sealed, self._egress):
            if name:
                await remove_sealed(self.docker, name)
        self._sealed = self._egress = self._egress_subnet = ""

    async def execute(
        self, command: str, *, cwd: Path, timeout_s: float, network: bool = True,
    ) -> Execution:
        if self._session:
            # Inside the held-open container, so what this installs is still
            # there for the next command.
            return await _run(
                [self.docker.binary, "exec", "-w", self.workdir,
                 self._session, self.docker.shell, "-c",
                 with_env(command, self.session_env)],
                timeout=timeout_s,
            )
        name = f"{NAME_PREFIX}-{uuid.uuid4().hex[:12]}"
        if not await self._network(network):
            return Execution(exit_code=1, started=False, output=(
                "not run: no sealed network to run it on, and it does not run "
                f"unsealed. {self._network_error}"))
        argv = self.argv(command, cwd=cwd, network=network, name=name)
        result = await _run(argv, timeout=timeout_s)
        if result.timed_out:
            # The wait_for killed the client, not the container.
            await _run([self.docker.binary, "kill", name], timeout=30)
            result.output = f"timed out after {timeout_s}s; container {name} killed"
        return result


class ComposeRunner:
    """Gates inside the project's own compose stack, one stack per feature.

    A generated image is a reconstruction: it drifts from what the project
    actually runs, and it cannot supply a database or a queue, because gates run
    as one container with no companions. A project that already declares its
    services in a compose file has answered all of that, and the file is
    maintained by the people who maintain the code.

    Isolation is the compose project name, not port arithmetic. Under
    `-p fabrika-<feature>` every stack gets its own containers, network and
    volumes, so ten features can each have a Postgres listening on 5432 and none
    of them can see another's. Host ports are the one thing that *would* collide,
    so the override strips every published port: nothing outside needs to reach
    in, and the gate command runs inside the network where service names already
    resolve.

    Nothing setup does survives this runner. The toolchain lives in the image
    committed from the session container, which `down()` removes; a database
    setup migrated and seeded lives in a volume, which `down -v` takes with it.
    Both have to be rebuilt by the next runner that wants them.
    """

    kind = "compose"
    carries_setup = False

    def __init__(self, repo: Path, spec: EnvironmentSpec, project_name: str,
                 docker: DockerConfig) -> None:
        self.repo = Path(repo)
        self.spec = spec
        self.project_name = project_name
        self.docker = docker
        self.workdir = spec.workdir or "/app"
        self._session: str = ""      # gate-service container held open for setup
        self.session_env: dict[str, str] = {}   # set while a test session is open
        self._committed: str = ""    # image made from it
        self._session_error: str = ""
        self._up = False
        # Gates run concurrently (AC-6.1). Without this, three of them each see
        # no stack, each run `compose up`, and two lose a race for the same
        # container names -- which reads as a broken environment rather than as
        # three clients asking for one thing.
        self._lock = asyncio.Lock()
        self._up_error: str | None = None
        self._egress_on = False
        # Compose network name -> the pool block it is created on. Chosen once
        # per runner, so every override this runner writes names the same ones.
        self._blocks: dict[str, str] = {}
        #: `key=value` labels on every container of the stack. See DockerRunner.
        self.labels: list[str] = []

    # -- the override ------------------------------------------------------

    def _override_text(self, cwd: Path) -> str:
        """The override as YAML text, because one line of it cannot be a value.

        Compose *merges* sequences across files rather than replacing them, so
        `ports: []` appends nothing and leaves the project's own mappings in
        place -- which is how two features end up fighting over one host port.
        `!override` is the Compose Spec tag for "replace, do not merge", and a
        tag has to be written rather than dumped from a Python value.
        """
        doc = self._override(cwd)
        out = ["services:"]
        for name, entry in doc["services"].items():
            out.append(f"  {name}:")
            out.append("    ports: !override []")
            if self.labels:
                out.append("    labels:")
                out += [f"      {json.dumps(k)}: {json.dumps(v)}"
                        for k, _, v in (label.partition("=") for label in self.labels)]
            for key, value in entry.items():
                if key == "ports":
                    continue
                if value is None:
                    out.append(f"    {key}: !reset null")
                elif key == "command":
                    out.append("    command: !override")
                    out += [f"      - {json.dumps(str(v))}" for v in value]
                elif key == "environment" and isinstance(value, dict):
                    out.append("    environment:")
                    out += [f"      {json.dumps(str(k))}: {json.dumps(str(v))}"
                            for k, v in value.items()]
                elif isinstance(value, list) and not value:
                    out.append(f"    {key}: !override []")
                elif isinstance(value, list):
                    out.append(f"    {key}: !override")
                    # A long-syntax volume is a mapping, and JSON is YAML's flow
                    # style -- so it goes out whole rather than as its repr.
                    out += [f"      - {json.dumps(v if isinstance(v, dict) else str(v))}"
                            for v in value]
                else:
                    out.append(f"    {key}: {json.dumps(str(value))}")
        # Every network the stack has, made `--internal`: no route to this
        # machine, another feature's stack, or the internet. See `_seal`. And
        # on a block of the sealed-network pool, laid out so the egress proxy
        # can answer for part of it while the stack is let out.
        out.append("networks:")
        for name in ["default", *self._declared_networks()]:
            out.append(f"  {name}:")
            out.append("    internal: true")
            block = self._blocks.get(name)
            if block:
                out.append("    ipam: !override")
                out.append("      config:")
                out.append(f"        - subnet: {json.dumps(block)}")
                out.append(f"          ip_range: {json.dumps(container_range(block))}")
        return "\n".join(out) + "\n"

    def _override(self, cwd: Path) -> dict[str, Any]:
        """What has to change about the project's compose file for a gate run.

        Three things, and no more. The feature's worktree replaces the source,
        so the stack runs the code under test rather than the checked-out repo.
        Published ports go, because they are the only thing two concurrent
        features can fight over. And the service the gates run in is told not to
        start its own long-running command, so `run` gets a shell rather than a
        dev server.
        """
        services: dict[str, Any] = {}
        for name in self._service_names():
            entry: dict[str, Any] = {"ports": []}
            declared = (self._compose().get("services") or {}).get(name) or {}
            # A name is one thing two stacks cannot both have, and a stack
            # per feature is the point. Compose names the containers itself
            # when this is gone.
            if isinstance(declared, dict) and declared.get("container_name"):
                entry["container_name"] = None
            # Env files the checkout does not have, met from their examples.
            files, supplied, _ = composeenv.resolve(self.repo, declared)
            if files is not None:
                entry["env_file"] = files
            environment = dict(supplied)
            if name == self.spec.compose_service:
                environment.update(self.spec.env)
            if environment:
                entry["environment"] = environment
            # The same thing `--add-host` does for a docker runner, which a
            # compose service never sees: `extra_args` reaches only the other
            # runner. Written per service rather than once, because this file
            # is merged into the project's own.
            entry["extra_hosts"] = [HOST_UNREACHABLE]
            if name != self.spec.compose_service:
                rebased = self._rebased_volumes(name, cwd)
                if rebased is not None:
                    entry["volumes"] = rebased
            if name == self.spec.compose_service:
                # No hardening here, and the reason is structural rather than a
                # preference. A compose override describes a *service*, and this
                # one service runs the setup commands and then the gates and
                # the agent. Setup installs system packages -- `apt-get` needs
                # `setgroups`, `setegid` and `seteuid` -- so a capability-less
                # definition takes the environment down before it exists, which
                # is what it did: `setgroups 65534 failed`, and a project whose
                # browser tier could not install.
                #
                # The docker runner can harden, because there the two are
                # different containers: setup runs in one, is committed to an
                # image, and everything after runs from that with every guard
                # on. Giving compose the same property means a second override
                # and a second `compose up`, which is worth doing deliberately
                # and not as a side effect of this line.
                # The whole worktree, not a subdirectory, so one service can run
                # every gate the project has -- backend and frontend alike.
                entry["volumes"] = [f"{cwd}:{self.workdir}"]
                entry["working_dir"] = self.workdir
                if self.spec.image:
                    # Compose declares the *runtime*. A project's own service
                    # image is usually a production one with no test runner, so
                    # the gate service is swapped for the toolchain image the
                    # factory built while every other service stays as declared.
                    entry["image"] = self.spec.image
                    entry["build"] = None
                    # It must stay up, not run the project's server. Its own
                    # command would be a dev server this image cannot run, and
                    # with no command at all the container exits immediately and
                    # `--wait` reports the stack as failed to start.
                    #
                    # Said as the entrypoint, not left to the image's. The image
                    # is often a commit of a setup container, and `docker commit
                    # --change "ENTRYPOINT []"` does not clear the entrypoint: it
                    # comes out `["sh"]`, the command is appended to it, and the
                    # service ran `sh sleep infinity` and exited -- measured the
                    # first time a stack was brought up again after a warm-up.
                    # `sh -c` ignores the command's words, so both agree.
                    entry["entrypoint"] = [self.docker.shell, "-c", "sleep infinity"]
                    entry["command"] = ["sleep", "infinity"]
            services[name] = entry
        return {"services": services}

    def _rebased_volumes(self, service: str, cwd: Path) -> list[Any] | None:
        """A companion's volumes with every bind into the repository moved into `cwd`.

        The gate service's source is replaced outright; its companions keep
        what the project declared -- and a declared `./frontend:/app` resolves
        against the compose project directory, which is the person's own
        checkout. So a feature's browser tests were served the checkout's
        frontend rather than the feature's, and the container serving it could
        write there. Named volumes, anonymous ones and binds to anything
        outside the repository are left as declared. `None` when nothing moved.
        """
        declared = ((self._compose().get("services") or {}).get(service) or {}).get("volumes")
        if not isinstance(declared, list):
            return None
        repo = self.repo.resolve()

        def inside(source: str) -> Path | None:
            if not source or not source.startswith((".", "/", "~")):
                return None   # a named volume
            path = Path(os.path.expanduser(source))
            if not path.is_absolute():
                path = repo / path
            try:
                return Path(os.path.normpath(path)).relative_to(repo)
            except ValueError:
                return None

        moved = False
        out: list[Any] = []
        for volume in declared:
            if isinstance(volume, str):
                source, sep, rest = volume.partition(":")
                rel = inside(source) if sep else None
                if rel is not None:
                    volume = f"{Path(cwd) / rel}:{rest}"
                    moved = True
            elif isinstance(volume, dict) and volume.get("type", "bind") == "bind":
                rel = inside(str(volume.get("source") or ""))
                if rel is not None:
                    volume = {**volume, "source": str(Path(cwd) / rel)}
                    moved = True
            out.append(volume)
        return out if moved else None

    def _compose(self) -> dict[str, Any]:
        try:
            raw = yaml.safe_load(
                (self.repo / self.spec.compose_file).read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _service_names(self) -> list[str]:
        raw = self._compose()
        if not raw:
            return [self.spec.compose_service] if self.spec.compose_service else []
        return list((raw.get("services") or {}).keys())

    def _declared_networks(self) -> list[str]:
        return [name for name in (self._compose().get("networks") or {})
                if name != "default"]

    def _unsealable(self) -> str:
        """Why this stack cannot be sealed, or "".

        A network the project declares `external` belongs to something else
        and cannot be made internal from here, and a service on the host's own
        network stack has no network to seal. Either one is a route out of the
        stack, so the stack does not start rather than start open.
        """
        raw = self._compose()
        for name, body in (raw.get("networks") or {}).items():
            if isinstance(body, dict) and body.get("external"):
                return (f"network {name!r} is declared external, so it cannot be sealed "
                        "and would give this stack a route off its own network")
        for name, body in (raw.get("services") or {}).items():
            mode = (body or {}).get("network_mode") if isinstance(body, dict) else None
            if mode:
                return (f"service {name!r} sets network_mode {mode!r}, which takes it off "
                        "the stack's own network and so out of reach of any seal")
        return ""

    async def _stack_networks(self) -> list[str]:
        result = await _run(
            [self.docker.binary, "network", "ls", "--format", "{{.Name}}",
             "--filter", f"label=com.docker.compose.project={self.project_name}"],
            timeout=60)
        return [n for n in (result.output or "").split() if n]

    async def _seal(self, egress: bool) -> str:
        """Attach the egress proxy to this stack's networks, or detach it.

        The networks are internal from the moment `up` makes them. What varies
        is whether the proxy is on them: during setup and while an agent works
        it is, so installs and the model provider are reachable; while the
        checks run it is not, so nothing is.
        """
        for network in await self._stack_networks():
            if egress:
                problem = await open_egress(self.docker, network)
                if problem:
                    return problem
            else:
                await close_egress(self.docker, network)
        self._egress_on = egress
        return ""

    async def _plan_blocks(self) -> str:
        """A pool block for each network the stack will have, once. "" or why not."""
        if self._blocks:
            return ""
        names = ["default", *self._declared_networks()]
        got, problem = await reserve_blocks(self.docker, len(names))
        if problem:
            return problem
        self._blocks = dict(zip(names, got))
        return ""

    def _service_network(self) -> str:
        """The compose network the gate service is on: its first, or `default`."""
        body = ((self._compose().get("services") or {}).get(self.spec.compose_service) or {})
        declared = body.get("networks") if isinstance(body, dict) else None
        names = list(declared) if isinstance(declared, (list, dict)) else []
        return str(names[0]) if names else "default"

    def _egress_flags(self, egress: bool) -> list[str]:
        """What a container that is let out gets: its network's resolver, which
        sends outside names to the proxy. Nothing else -- the proxy needs no
        variable and no setting in the container."""
        block = self._blocks.get(self._service_network())
        if not egress or not block:
            return []
        return ["-v", f"{resolver_file(block)}:/etc/resolv.conf:ro"]

    def _base_argv(self, override: Path) -> list[str]:
        return [
            self.docker.binary, "compose",
            "--project-name", self.project_name,
            "--project-directory", str(self.repo),
            "-f", str(self.repo / self.spec.compose_file),
            "-f", str(override),
        ]

    # -- lifecycle ---------------------------------------------------------

    async def execute(
        self, command: str, *, cwd: Path, timeout_s: float, network: bool = True,
    ) -> Execution:
        problem = await self._plan_blocks()
        if problem:
            return Execution(exit_code=1, started=False, output=f"not run: {problem}")
        override = cwd / f".factory-compose-{uuid.uuid4().hex[:8]}.yml"
        override.write_text(self._override_text(cwd), encoding="utf-8")
        argv = self._base_argv(override)
        try:
            await self._ensure_up(argv, timeout_s)
            if self._up_error is not None:
                return Execution(exit_code=1,
                                 output=f"compose up failed:\n{self._up_error}", started=False)
            if self._session:
                # Setup is running: one container, held open, so that what one
                # command installs the next one can still see.
                return await _run(
                    [self.docker.binary, "exec", "-w", self.workdir,
                     self._session, self.docker.shell, "-c",
                     with_env(command, self.session_env)],
                    timeout=timeout_s,
                )
            if network != self._egress_on:
                problem = await self._seal(network)
                if problem:
                    return Execution(exit_code=1, started=False, output=(
                        f"not run: {problem}"))
            # `--entrypoint` explicitly, rather than relying on the image
            # having none. The prepared image is a commit of the setup
            # container, and a commit keeps that container's config -- so it
            # carries `ENTRYPOINT ["sh"]`, and passing the shell as the command
            # too ran `sh sh -c ...`: "sh: 0: cannot open sh: No such file".
            return await _run(
                argv + ["run", "--rm", "-T", "--workdir", self.workdir,
                        *self._egress_flags(network),
                        "--entrypoint", self.docker.shell,
                        self.spec.compose_service, "-c",
                        with_env(command, self.session_env)],
                timeout=timeout_s,
            )
        finally:
            override.unlink(missing_ok=True)

    async def _ensure_up(self, argv: list[str], timeout_s: float) -> None:
        """Bring the stack up once, and never after a session container exists.

        Not inline at the top of `execute`, because it begins with
        `down -v --remove-orphans` -- which clears containers a killed run left
        holding these names, and also removes any one-off `run` container in the
        project. Run per command, it would tear the setup container, created
        moments earlier, down under the first command that tried to use it:
        `run -d` returns a real id and `exec` answers "No such container" for
        every line of setup.
        """
        async with self._lock:
            if self._up:
                return
            unsealable = self._unsealable()
            if unsealable:
                self._up = False
                self._up_error = f"not started: {unsealable}."
                return
            # A run that was killed rather than finished leaves its containers
            # holding these names. Nothing else clears them, so every later run
            # of this feature would fail on the conflict instead of on the code.
            # The proxy first: a network with an endpoint still on it cannot be
            # removed, and a killed run can leave the proxy on one.
            await self._seal(False)
            await _run(argv + ["down", "-v", "--remove-orphans"], timeout=120)
            # --wait honours the healthchecks the project already declares, so a
            # gate does not race a database still starting. Named, because `up`
            # with no argument starts everything the file declares -- for a
            # typical web project that means building a frontend image the gates
            # never use, and failing on its build rather than on the code.
            up = await _run(
                argv + ["up", "-d", "--wait", self.spec.compose_service],
                timeout=max(timeout_s, 300),
            )
            # Set before checking: `up` can fail having already created
            # containers, and those hold names and ports until something removes
            # them. Teardown must run either way.
            self._up = True
            if up.exit_code == 0:
                self._up_error = None
                return
            # Compose says which service stopped and never why. The why is in
            # that service's own log, which is gone once the stack is taken
            # down -- so it is read now. A database that exited because the
            # disk was full said so there, and the run reported "3 setup
            # commands failed" instead.
            said = await self._stopped_services_said(argv)
            headline = (await low_disk(self.docker, self.docker.min_free_gb)
                        or self._stack_headline(up.output))
            self._up_error = f"{headline}\n\n{up.output.strip()}{said}"

    @property
    def up_problem(self) -> str:
        """Why the stack did not start, in one paragraph; empty when it did."""
        return (self._up_error or "").split("\n\n", 1)[0].strip()

    def _stack_headline(self, output: str) -> str:
        """The failure compose reported, said in the project's terms."""
        stopped = re.search(r"container (\S+) exited \((\d+)\)", output)
        if stopped:
            name, code = stopped.groups()
            service = next((s for s in self._service_names()
                            if re.search(rf"[-_]{re.escape(s)}[-_]\d+$", name)), name)
            return (f"The project's `{service}` service stopped as it started (exit {code}). "
                    "What it said is below.")
        lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
        return f"The project's services did not start: {lines[-1] if lines else 'no reason given'}"

    async def _stopped_services_said(self, argv: list[str]) -> str:
        """The last lines each stopped or unhealthy service printed, or empty."""
        listed = await _run(argv + ["ps", "-a", "--format", "json"], timeout=60)
        if listed.exit_code != 0:
            return ""
        text = listed.output.strip()
        try:
            rows = json.loads(text) if text.startswith("[") else [
                json.loads(line) for line in text.splitlines() if line.strip().startswith("{")]
        except ValueError:
            return ""
        stopped = [str(r.get("Service") or "") for r in rows if isinstance(r, dict)
                   and (str(r.get("State") or "") != "running"
                        or str(r.get("Health") or "") == "unhealthy")]
        out = []
        for service in [s for s in dict.fromkeys(stopped) if s][:3]:
            logs = await _run(argv + ["logs", "--no-color", "--tail", "40", service], timeout=60)
            if logs.output.strip():
                out.append(f"\n\n--- what `{service}` said ---\n{logs.output.strip()[-3000:]}")
        return "".join(out)

    #: Every network in the stack is made `--internal` by the override, and the
    #: egress proxy is attached only for what asks for the network. Were it
    #: `False`, compose would own the networking and a stack's checks would run
    #: with the default bridge's route to the internet and to this machine.
    enforces_egress_policy = True

    @property
    def session_id(self) -> str:
        """The held container, for callers that must exec into it themselves."""
        return self._session

    async def open_session(self, cwd: Path, *, network: bool = True) -> None:
        """A container from the gate service, held open for setup.

        `compose run --rm` gives a fresh container per command, so anything
        setup wrote outside the mounted worktree -- site-packages, above all --
        was discarded before the next line ran. `npm ci` survived only because
        `node_modules` lives in the mount; `pip install` never did.
        """
        if self._session:
            return
        problem = await self._plan_blocks()
        if problem:
            self._session_error = problem
            return
        override = cwd / f".factory-compose-{uuid.uuid4().hex[:8]}.yml"
        override.write_text(self._override_text(cwd), encoding="utf-8")
        try:
            # Before the session container exists, not after: the teardown that
            # starts a stack would take it with it.
            await self._ensure_up(self._base_argv(override), 600)
            if self._up_error is not None:
                self._session_error = self._up_error
                return
            problem = await self._seal(network)
            if problem:
                self._session_error = problem
                return
            # `--name` is accepted and ignored: compose names a one-off run
            # container itself, `<project>-<service>-run-<hash>`. Exec'ing the
            # name we asked for got "No such container" on every setup command
            # while `run` reported success. The id it prints is the only handle
            # that is actually ours.
            code, out, err = await _run_id(
                self._base_argv(override) + [
                    "run", "-d", "-T", "--workdir", self.workdir,
                    *[arg for label in self.labels for arg in ("--label", label)],
                    *self._egress_flags(network),
                    "--entrypoint", self.docker.shell,
                    self.spec.compose_service, "-c", "sleep infinity",
                ],
                timeout=600,
            )
        finally:
            override.unlink(missing_ok=True)
        container = _last_id(out) if code == 0 else ""
        if container:
            self._session = container
        else:
            self._session_error = (err or out or "no container id returned").strip()[-2000:]

    async def start_service(self, command: str, *, cwd: Path, name: str) -> str:
        """Start a background process inside the held-open gate-service container.

        The same mechanism as `DockerRunner.start_service` and for the same
        reason. Compose already supplies the databases and queues the project
        declares -- what it does not supply is the application itself, because a
        gate runs as a one-off `compose run` rather than against a standing app.
        That gap is why every command needing a live server carried its own
        `nohup uvicorn ... & ... kill` and why one of the copies forgot the kill.
        """
        if not self._session:
            return ("no session container is open, so a service would be started in a "
                    "container that is discarded before the tests run")
        result = await _run(
            [self.docker.binary, "exec", "-d", "-w", self.workdir,
             self._session, self.docker.shell, "-c", with_env(command, self.session_env)],
            timeout=60,
        )
        if result.exit_code != 0:
            return (result.output or "no output").strip()[-2000:]
        return ""

    async def discard_session(self) -> None:
        """Close the session without committing it. Everything inside goes."""
        if not self._session:
            return
        await _run([self.docker.binary, "rm", "-f", self._session], timeout=60)
        self._session = ""

    async def close_session(self) -> None:
        """Commit the setup container, and point the gates at the result."""
        if not self._session:
            return
        image = f"{NAME_PREFIX}-prepared:{uuid.uuid4().hex[:12]}"
        # Cleared, because a commit inherits the session container's config and
        # the session was started as `sh -c "sleep infinity"`. An image whose
        # entrypoint is a shell and whose command is a sleep is not a thing any
        # caller should have to know about.
        committed = await _run(
            [self.docker.binary, "commit",
             "--change", "ENTRYPOINT []", "--change", 'CMD ["sleep", "infinity"]',
             self._session, image],
            timeout=300)
        await _run([self.docker.binary, "rm", "-f", self._session], timeout=60)
        self._session = ""
        if committed.exit_code == 0:
            # See DockerRunner.close_session: a warm-up commits on top of this.
            if self._committed:
                await _run([self.docker.binary, "rmi", self._committed], timeout=120)
            # The override already swaps the gate service's image when one is
            # named, which is exactly the hook this needs.
            self.spec.image = image
            self._committed = image

    async def down(self) -> None:
        """Tear the stack down, volumes included.

        Volumes especially: a database that survives between features is shared
        state, and shared state is the thing this whole arrangement exists to
        prevent.
        """
        # The setup container and the image made from it are this run's, and
        # nothing else's. Neither is reachable once the stack is gone.
        if self._session:
            await _run([self.docker.binary, "rm", "-f", self._session], timeout=60)
            self._session = ""
        if self._committed:
            await _run([self.docker.binary, "rmi", "-f", self._committed], timeout=120)
            self._committed = ""
        try:
            await self._take_down()
        finally:
            release_blocks(list(self._blocks.values()))
            self._blocks = {}

    async def _take_down(self) -> None:
        if not self._up:
            return
        # Before `down`, which cannot remove a network the proxy is still on.
        await self._seal(False)
        override = self.repo / ".factory-compose-down.yml"
        override.write_text(yaml.safe_dump({"services": {}}), encoding="utf-8")
        try:
            await _run(self._base_argv(override) + ["down", "-v", "--remove-orphans"],
                       timeout=180)
        finally:
            override.unlink(missing_ok=True)
            self._up = False

    async def reset_stack(self) -> None:
        """Take the stack down with its volumes and let the next command bring
        it up fresh, from the image setup committed.

        For after a warm-up, which runs checks for real with the network: an
        integration suite writes into the stack's database as it goes, and a
        check that expects to start from an empty one would then fail on rows
        nothing it ran put there. The prepared image and the pool blocks stay.
        """
        await self._take_down()


# --------------------------------------------------------------------------
# the door into a preview
# --------------------------------------------------------------------------

#: The forwarder both hops run. See its docstring for why there are two.
DOORWAY_SCRIPT = Path(__file__).with_name("doorway.py")
#: On every container and network a preview makes, valued with the feature.
PREVIEW_LABEL = f"{NAME_PREFIX}.preview"
#: In the names of a preview runner's sealed networks. See `network_infix`.
PREVIEW_NETWORK_INFIX = "preview-"


def _doorway_guard() -> list[str]:
    """The egress proxy's hardening, for a process that only forwards bytes."""
    return ["--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "65534:65534", "--memory", "64m", "--pids-limit", "64",
            "--volume", f"{DOORWAY_SCRIPT}:/doorway.py:ro", "--entrypoint", "python"]


def inner_argv(docker: DockerConfig, name: str, session: str, address: str,
               ports: Sequence[int], label: str) -> list[str]:
    """The hop inside the app's network namespace: its sealed address to its
    loopback. Joins another container's namespace, so it publishes nothing and
    is on no network of its own."""
    return [docker.binary, "run", "-d", "--name", name,
            "--label", f"{PREVIEW_LABEL}={label}",
            "--network", f"container:{session}", *_doorway_guard(),
            docker.egress_image, "/doorway.py", "--listen", address, "--to", "127.0.0.1",
            *[str(p) for p in ports]]


def door_argv(docker: DockerConfig, name: str, network: str, address: str,
              ports: Sequence[int], label: str) -> list[str]:
    """The door: this machine's loopback, and nothing wider, to the app's address.

    The only place Fabrika publishes a port. Each port under its own number,
    so the address a service was told it has is the one a browser opens. On a
    network made for this door alone rather than the default bridge, so no
    other container on this machine shares a network with it.
    """
    publish = [arg for p in ports for arg in ("-p", f"127.0.0.1:{p}:{p}")]
    return [docker.binary, "run", "-d", "--name", name,
            "--label", f"{PREVIEW_LABEL}={label}",
            "--network", network, *publish, *_doorway_guard(),
            docker.egress_image, "/doorway.py", "--listen", "0.0.0.0", "--to", address,
            *[str(p) for p in ports]]


async def session_address(docker: DockerConfig, container: str,
                          prefer: str = "") -> tuple[str, str]:
    """The container's (address, network): on `prefer` if it is on it, else its first."""
    result = await _run([docker.binary, "inspect", "-f",
                         "{{json .NetworkSettings.Networks}}", container], timeout=30)
    try:
        networks = json.loads(result.output.strip() or "{}") if result.exit_code == 0 else {}
    except json.JSONDecodeError:
        networks = {}
    order = sorted(networks, key=lambda n: n != prefer)
    for name in order:
        address = (networks[name] or {}).get("IPAddress") or ""
        if address:
            return address, name
    return "", ""


class Door:
    """Both hops into one preview, and the network the door publishes from."""

    def __init__(self, docker: DockerConfig, label: str) -> None:
        self.docker = docker
        self.label = label
        self.containers: list[str] = []
        self.network = ""

    async def open(self, session: str, ports: Sequence[int], prefer: str = "") -> str:
        """Open the door to `session` on `ports`. Returns "" or why not.

        Half an open door is closed again before returning: a failure here
        leaves nothing standing.
        """
        address, sealed = await session_address(self.docker, session, prefer)
        if not address:
            return "the preview's container has no network address to forward to"
        tag = uuid.uuid4().hex[:12]
        name = f"{NAME_PREFIX}-door-{tag}"
        network = name
        steps: list[tuple[str, list[str]]] = [
            ("network", [self.docker.binary, "network", "create",
                         "--label", f"{PREVIEW_LABEL}={self.label}", network]),
            ("inner", inner_argv(self.docker, f"{NAME_PREFIX}-inner-{tag}", session,
                                 address, ports, self.label)),
            ("door", door_argv(self.docker, f"{NAME_PREFIX}-door-{tag}", network,
                               address, ports, self.label)),
            ("connect", [self.docker.binary, "network", "connect", sealed,
                         f"{NAME_PREFIX}-door-{tag}"]),
        ]
        for step, argv in steps:
            result = await _run(argv, timeout=180)
            if result.exit_code != 0:
                await self.close()
                return f"could not open the door ({step}): {result.output.strip()[-600:]}"
            if step == "network":
                self.network = network
            elif step in ("inner", "door"):
                self.containers.append(_last_id(result.output) or argv[argv.index("--name") + 1])
        return ""

    async def trouble(self) -> str:
        """Which hop of this door has stopped, and what it printed; empty when both run.

        Asked when the page does not answer. A dead hop and an app that is not
        listening look identical from outside -- the connection is accepted
        and closed -- and a preview that failed that way once left an empty
        log and a one-line error nobody could act on.
        """
        said: list[str] = []
        for container in self.containers:
            state = await _run([self.docker.binary, "inspect", "-f", "{{.State.Status}}",
                                container], timeout=30)
            if state.exit_code == 0 and state.output.strip() == "running":
                continue
            logs = await _run([self.docker.binary, "logs", "--tail", "20", container], timeout=30)
            said.append(f"--- {container[:12]} stopped ---\n{logs.output.strip()[-1500:]}")
        return "\n\n".join(said)

    async def close(self) -> None:
        if self.containers:
            await _run([self.docker.binary, "rm", "-f", *self.containers], timeout=60)
            self.containers = []
        if self.network:
            await _run([self.docker.binary, "network", "rm", self.network], timeout=60)
            self.network = ""


async def sweep_previews(docker: DockerConfig) -> list[str]:
    """Remove what a preview left behind when the process holding it died.

    A preview is held by this server, so after a restart nothing can close
    one: its containers, the door's network, the stack of a compose project
    and a docker runner's sealed networks are all orphans. Everything a
    preview makes is labelled or named for it, and only those are touched.
    Returns what was removed.
    """
    removed: list[str] = []
    listed = await _run([docker.binary, "ps", "-aq", "--filter", f"label={PREVIEW_LABEL}"],
                        timeout=60)
    ids = listed.output.split() if listed.exit_code == 0 else []
    projects: set[str] = set()
    for cid in ids:
        info = await _run([docker.binary, "inspect", "-f",
                           '{{index .Config.Labels "com.docker.compose.project"}}', cid],
                          timeout=30)
        name = info.output.strip() if info.exit_code == 0 else ""
        if name and name != "<no value>":
            projects.add(name)
    if ids:
        await _run([docker.binary, "rm", "-f", *ids], timeout=120)
        removed += ids
    nets = await _run([docker.binary, "network", "ls", "--format", "{{.Name}}",
                       "--filter", f"label={PREVIEW_LABEL}"], timeout=60)
    for net in nets.output.split() if nets.exit_code == 0 else []:
        await _run([docker.binary, "network", "rm", net], timeout=60)
        removed.append(net)
    for kind in ("sealed", "egress"):
        found = await _run([docker.binary, "network", "ls", "--format", "{{.Name}}",
                            "--filter", f"name={NAME_PREFIX}-{kind}-{PREVIEW_NETWORK_INFIX}"],
                           timeout=60)
        for net in found.output.split() if found.exit_code == 0 else []:
            await remove_sealed(docker, net)
            removed.append(net)
    for project in projects:
        label = f"label=com.docker.compose.project={project}"
        for what in ("container", "network", "volume"):
            listing = await _run([docker.binary, what, "ls", "-q", *(["-a"] if what == "container" else []),
                                  "--filter", label], timeout=60)
            names = listing.output.split() if listing.exit_code == 0 else []
            if not names:
                continue
            if what == "network":
                for net in names:
                    await remove_sealed(docker, net)
            else:
                await _run([docker.binary, what, "rm", *(["-f"] if what == "container" else []),
                            *names], timeout=120)
            removed += names
    return removed


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------


async def runner_for(
    project: Project, config: Config, *, feature_id: str = "",
) -> tuple[Runner, str]:
    """The runner a project's gates should use, and the image behind it.

    `feature_id` names the compose project, which is what keeps concurrent
    features apart: separate containers, network and volumes per feature, so
    each one's database is its own. Without it two features would share a stack
    and their suites would interfere in ways that look exactly like bugs.
    """
    spec = project.environment
    if spec is None or spec.kind == "host":
        if isolation.TEST_SUITE_ON_HOST:
            return LocalRunner(), ""
        # Not a runner on this machine instead. Checks execute code a model
        # wrote, and on this machine that code can reach every server running
        # here -- a developer's own copy of the project on its usual port, for
        # one -- and every other feature's files.
        raise DockerError(
            f"project {project.id!r} has "
            + ("no environment" if spec is None else "a `host` environment")
            + ", so its checks would run on this machine rather than in a sealed "
            "container. Give it a container environment -- compose, dockerfile or "
            "reuse -- on its environment page."
        )
    if spec.kind == "compose":
        if not spec.compose_file or not spec.compose_service:
            raise DockerError(
                f"project {project.id!r} has environment kind 'compose' but names "
                "no compose_file and/or compose_service"
            )
        target = Path(project.state.repo) / spec.compose_file
        if not target.is_file():
            raise DockerError(
                f"project {project.id!r} names compose file {spec.compose_file!r}, "
                f"which does not exist at {target}"
            )
        slug = re.sub(r"[^a-z0-9]+", "-", f"{project.id}-{feature_id or 'baseline'}".lower())
        name = f"{NAME_PREFIX}-{slug.strip('-')[:48]}"
        # A compose environment may still carry a Dockerfile: the services come
        # from the project, the gate service's toolchain from us.
        if spec.dockerfile:
            spec.image = await build_image(project, config.docker)
        return ComposeRunner(Path(project.state.repo), spec, name, config.docker), name
    image = await build_image(project, config.docker)
    return DockerRunner(image, spec.workdir, config.docker), image
