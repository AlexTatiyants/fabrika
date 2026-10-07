"""Where an agent, or anything an agent wrote, may run: in a sealed container.

Every agent session and every command a check runs happens inside a container
on a network of its own. That network is `--internal`, so it has no route to
this machine, to another feature's containers, or to the internet. Whatever
genuinely needs the internet -- a package install, an agent calling its model
provider -- goes through the egress proxy (`egress.py`), which forwards to
public addresses and refuses everything else.

This was not always so, and the gaps were each found the hard way. Agents with
no prepared environment ran on this machine's shell: the oracle always did, and
every CLI completion did, with whatever tools its harness turns on. Containers
themselves could reach this machine: pointing `host.docker.internal` at the
container's own loopback hid the *name*, and from an ordinary feature container
`http://192.168.65.254:8300` -- Docker Desktop's address for the host --
answered with a developer's own copy of the app. A developer running their
project locally on its default port is the common case, not the exception, so
isolation has to hold with those servers up.

There is one exception, and it is the test suite. It has no Docker, and it runs
fake harnesses and fake CLIs as local scripts by design. `tests/conftest.py`
sets `TEST_SUITE_ON_HOST` and nothing under `factory/` may; an invariant test
reads the source to make sure of it.
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Sequence

#: True only inside the test suite. See the module docstring.
TEST_SUITE_ON_HOST = False


class IsolationError(RuntimeError):
    """Something was about to run outside a sealed container."""


def contained(argv: Sequence[str], docker_binary: str) -> bool:
    """Whether `argv` runs its program inside a container rather than here."""
    if len(argv) < 2:
        return False
    return (PurePath(argv[0]).name == PurePath(docker_binary).name
            and argv[1] in ("exec", "run"))


def require_contained(argv: Sequence[str], docker_binary: str, what: str) -> None:
    """Refuse to start `argv` on this machine.

    Checked where the process is spawned rather than where the decision is
    made, because every path to the spawn has to hold it -- the primary route,
    the fallback route, a retry -- and a check at one decision point is a check
    the next path added does not pass through.
    """
    if TEST_SUITE_ON_HOST or contained(argv, docker_binary):
        return
    raise IsolationError(
        f"{what} would have run on this machine ({PurePath(argv[0]).name if argv else '?'}), "
        "where it could reach every server running here and every other feature's "
        "files. Agents run only inside a sealed container. This usually means the "
        "project has no container environment, or the route has no way to run inside "
        "one (`session_in_container` and `container_session_command` for a session, "
        "`container_install` for a completion).")
