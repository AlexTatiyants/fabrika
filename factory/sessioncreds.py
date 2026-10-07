"""Getting a harness's sign-in into the unit's container, and nothing else.

A session that runs inside the container is a session that cannot read the
keychain, the home directory or the environment this process was started with.
That is the point of it. So the one thing it does need -- the credential its
harness authenticates with -- has to be handed in deliberately, and this module
is the only place that happens.

The rules it keeps, all of them learned from how this goes wrong elsewhere:

* **A copy, never the original.** A harness that refreshes its own token inside
  a container would otherwise rewrite the file your own terminal signs in with.
  Measured: one of the two CLIs here does exactly that refresh.
* **Never in argv.** Every process on the machine can read a command line, so a
  value passed as `-e NAME=secret` is a value published to the machine. These
  go through a file only this user can read, handed to `docker exec --env-file`.
* **Never in a log, an error or a record.** Failures here name the credential
  and where it was looked for, and say nothing about what was or was not found
  inside it.
* **Gone afterwards.** The private directory is removed when the session ends,
  and a file copied into the container is deleted out of it.
* **Missing is fatal before anything is spent.** A harness started without its
  credential does not fail: it burns a turn and answers "please sign in", which
  arrives downstream as a unit that mysteriously did nothing.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from . import procs
from .config import Config, ContainerCredential, DockerConfig, RouteConfig


class CredentialError(RuntimeError):
    """A credential a session needs could not be assembled.

    Carries the credential's name and where it was looked for. Never its
    value, and never any part of a file's contents.
    """


@dataclass
class Bundle:
    """What one session's credentials became on disk, and where they go."""

    directory: Path
    env_file: Path | None = None
    #: (file on this machine, absolute path it must land on in the container)
    files: list[tuple[Path, str]] = field(default_factory=list)

    def cleanup(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)


async def _keychain(service: str, *, optional: bool = False) -> bytes:
    """One macOS keychain item, as bytes, by service name.

    Reading it can raise a system prompt the first time, which is the operating
    system asking the human whether this may happen. That is correct and is not
    worked around.
    """
    if shutil.which("security") is None:
        if optional:
            return b""
        raise CredentialError(
            f"credential {service!r} is stored in the macOS keychain, and this machine "
            "has no `security` command to read it with. Configure the same credential "
            "as `kind: env` instead -- every one of these harnesses can take a token "
            "from a variable."
        )
    proc = await asyncio.create_subprocess_exec(
        "security", "find-generic-password", "-w", "-s", service,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    if proc.returncode != 0 or not (out or b"").strip():
        if optional:
            return b""
        raise CredentialError(
            f"the keychain has no readable item for service {service!r}. Sign the "
            "harness in on this machine, or give the route a token in a variable."
        )
    return out.strip()


def _env_value(spec: ContainerCredential, config: Config) -> str:
    if spec.from_api_key:
        value = (config.api.api_key or "").strip()
        source = "this app's stored provider key"
    else:
        source = spec.source or spec.name
        value = (os.environ.get(source) or "").strip()
        source = f"${source}"
    if not value and not spec.optional:
        raise CredentialError(
            f"credential {spec.name!r} comes from {source}, which is empty. A session "
            "started without it spends a turn to be told it is not signed in."
        )
    return value


async def prepare(route: RouteConfig, config: Config) -> Bundle:
    """Assemble one session's credentials into a private directory.

    The caller owns the result and must call `cleanup()`, whatever happens to
    the session.
    """
    directory = Path(tempfile.mkdtemp(prefix="factory-session-creds-"))
    directory.chmod(stat.S_IRWXU)
    bundle = Bundle(directory=directory)
    try:
        lines: list[str] = []
        for index, spec in enumerate(route.container_credentials):
            if spec.kind == "env":
                if not spec.name:
                    raise CredentialError(
                        "a credential of kind 'env' must say what variable the harness "
                        "reads it as (`name`)."
                    )
                value = _env_value(spec, config)
                if value:
                    lines.append(f"{spec.name}={value}")
                continue

            if not spec.target:
                raise CredentialError(
                    f"credential of kind {spec.kind!r} must say where it lands in the "
                    "container (`target`)."
                )
            if spec.kind == "file":
                origin = Path(spec.path).expanduser()
                if not origin.is_file():
                    if spec.optional:
                        continue
                    raise CredentialError(
                        f"credential file {spec.path} is not on this machine. Sign the "
                        "harness in, or point the route at the file it writes."
                    )
                data = origin.read_bytes()
            else:
                if not spec.service:
                    raise CredentialError(
                        "a credential of kind 'keychain' must name the keychain "
                        "`service` to read."
                    )
                data = await _keychain(spec.service, optional=spec.optional)
                if not data:
                    continue

            local = directory / f"cred-{index}"
            local.write_bytes(data)
            local.chmod(stat.S_IRUSR | stat.S_IWUSR)
            bundle.files.append((local, spec.target))

        # Several optional credentials mean "any one of these will do" -- a
        # token minted for a container, or the sign-in this machine already
        # has. None of them resolving is not a configuration with nothing to
        # do: it is a session that will start, spend a turn, and be told it is
        # not signed in.
        if route.container_credentials and not lines and not bundle.files:
            looked = ", ".join(
                spec.name or spec.service or spec.path or spec.kind
                for spec in route.container_credentials
            )
            raise CredentialError(
                f"none of this route's credentials could be found ({looked}), so a "
                "session in the container would run as nobody. Sign the harness in on "
                "this machine, or give the route a token in a variable."
            )

        if lines:
            env_file = directory / "env"
            env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
            bundle.env_file = env_file
    except BaseException:
        bundle.cleanup()
        raise
    return bundle


async def _docker(argv: Sequence[str], timeout: float = 120.0) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await procs.kill(proc)
        return -9, f"timed out after {timeout}s"
    return (proc.returncode if proc.returncode is not None else -1,
            (out or b"").decode("utf-8", errors="replace"))


async def install(bundle: Bundle, container: str, docker: DockerConfig) -> None:
    """Copy the file-shaped credentials into a running container.

    `docker cp` rather than a mount, because the container is already running
    by the time a session starts: mounts are fixed when a container is created,
    and the unit's container is created by whatever prepared its environment.

    Numeric ownership survives the copy, so a file written here by this user
    arrives owned by the same uid the session runs as -- which is the uid the
    container was started with.
    """
    for local, target in bundle.files:
        parent = str(Path(target).parent)
        code, output = await _docker(
            [docker.binary, "exec", "--user", f"{os.getuid()}:{os.getgid()}",
             container, "mkdir", "-p", parent])
        if code != 0:
            raise CredentialError(
                f"could not make {parent} in the unit's container for a credential: "
                f"{output.strip()[:300]}"
            )
        code, output = await _docker(
            [docker.binary, "cp", str(local), f"{container}:{target}"])
        if code != 0:
            raise CredentialError(
                f"could not place a credential at {target} in the unit's container: "
                f"{output.strip()[:300]}"
            )


async def uninstall(bundle: Bundle, container: str, docker: DockerConfig) -> None:
    """Take the copies back out of the container.

    Best effort on purpose. The container is destroyed when the unit ends, so
    a failure here is not worth failing a unit that has already written its
    code -- but leaving a token sitting in a container that a later phase may
    still commit into an image is worth one attempt to prevent.
    """
    for _, target in bundle.files:
        await _docker([docker.binary, "exec", container, "rm", "-f", target])
