"""One prepared environment per authoring unit.

An agent that writes code as a bare subprocess in a git worktree has no
toolchain, no services, no database. If the environment the project declares
-- the image, the compose stack, `setup`, `test_prepare`, the services -- is
built for the gates and handed to nothing else, a worker writes a migration it
cannot run, a repairer fixes a defect it cannot reproduce, and a breaker is
told to run its probes with nothing to run them against.

That gap is not a discipline problem and no prompt closes it. `harness.md`
tells workers that their tests exist to "prove the code runs -- a unit once
shipped a migration that was never executed, only syntax-checked", and the
same thing still happens, verbatim, without an environment. An agent cannot
execute a migration against a database that does not exist.

What this module does is give each unit the same environment the gates get, and
one that is *its own*:

  * `runner_for(feature_id=...)` names a compose project, and a compose project
    is a container set, a network and a set of volumes. Passing a per-unit name
    is the whole of the isolation: three workers building in parallel get three
    Postgres instances that cannot see each other, and each goes away with its
    stack. Nothing a unit does to its database reaches another unit, another
    round, or another feature.

  * The unit's worktree is what the stack mounts, so the files the agent edits
    on this side are the files the tests run against on that side.

  * An agent whose route authors inside the unit's container is simply there.
    One that runs on the host -- a CLI signed in as you -- gets instead a small
    set of shims on its PATH, one per binary the project's
    own commands name. Typing `pytest` runs pytest in the container, in the
    directory that corresponds to the one it is standing in. The indirection is
    deliberately invisible: an agent told to use a special command will not, and
    "pytest: not found" is exactly the observation that produces silent,
    unverified work.

The cost is honest and it is not small: a stack, a dependency install and a
migrate/seed per unit. That is minutes. The alternative is units of work whose
every claim of correctness rests on reading, discovered a day later by the
only lane that can actually run anything.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Sequence

from .containers import DockerError, harness_image, runner_for
from .gates import GateResult, run_setup, test_session

#: Words that begin a command segment without naming a program. `cd` is the one
#: that matters -- every gate command in every project this has seen starts with
#: it -- but the rest are here so a compound command cannot produce a shim
#: called `then`.
SHELL_WORDS = frozenset({
    "cd", "export", "set", "unset", "source", ".", "exec", "eval", "env",
    "if", "then", "else", "elif", "fi", "for", "while", "do", "done", "case",
    "esac", "echo", "true", "false", "return", "shift", "wait", "trap",
})

#: Interpreters that come in pairs. A project naming `python` in one command and
#: an agent reaching for `python3` in the next is not a different intention, and
#: leaving the second unshimmed sends it to a host interpreter with none of the
#: project's dependencies -- which fails in a way that reads as broken code.
ALIASES = {
    "python": "python3", "python3": "python",
    "pip": "pip3", "pip3": "pip",
}


@dataclass
class UnitEnv:
    """What an authoring agent gets on top of a checkout.

    `ready` is false when the environment could not be brought up. That is not
    fatal -- the agent still writes code, in the bare checkout -- but it is
    said out loud in `note` so the agent knows it is working blind, and in
    `report` so the run can say which units were.

    `problem` and `log` are that same failure in the two forms something other
    than a prompt can use: one line for a finding's title, and the commands with
    their output for its evidence. With `report` as the only account of a blind
    unit, and nothing reading it, a run where two thirds of the authoring
    sessions had no database looks, in the packet, exactly like one where they
    all did.
    """

    ready: bool = False
    vars: dict[str, str] = field(default_factory=dict)
    #: The session's own variables -- where each service is, the disposable
    #: database -- apart from `vars`, which also carries this machine's PATH
    #: and the container's name. An agent running *inside* the container gets
    #: these and only these; handing it the host's PATH would be nonsense.
    session: dict[str, str] = field(default_factory=dict)
    note: str = ""
    report: str = ""
    problem: str = ""
    log: str = ""
    #: What runs a command in this environment, for the caller that has to
    #: measure something in it after an agent is done -- a reporting command
    #: an agent proposed is proved in the container it was found in. None
    #: whenever `ready` is false.
    runner: Any = None


#: What a unit with no environment gets: nothing, and no claim to have one.
NO_ENV = UnitEnv()


def binaries(commands: Iterable[str]) -> list[str]:
    """The programs a project's own commands name, in the order they appear.

    Derived rather than configured, because a list a human maintains is a list
    that goes stale the first time a gate changes. The rule is small enough to
    hold in the head: split on shell operators, drop any leading `VAR=value`
    assignments, and take the first remaining word of each segment unless it is
    a shell builtin or carries a path (an absolute or relative program is
    already unambiguous and must not be redirected).
    """
    found: list[str] = []
    for command in commands:
        for segment in re.split(r"&&|\|\||[;|()]", command or ""):
            try:
                tokens = shlex.split(segment, posix=True)
            except ValueError:      # unbalanced quotes: not ours to interpret
                continue
            for token in tokens:
                head = token.split("=", 1)[0]
                if "=" in token and head.isidentifier():
                    continue        # an env assignment prefixing the real word
                if (token in SHELL_WORDS or "/" in token
                        or token.startswith(("$", "-", '"', "'"))):
                    break
                if token not in found:
                    found.append(token)
                break               # one program per segment, the first word
    for name in list(found):
        twin = ALIASES.get(name)
        if twin and twin not in found:
            found.append(twin)
    return found


def project_binaries(project) -> list[str]:
    """Every program this project runs, across gates, setup, prepare, services."""
    commands: list[str] = [g.command for g in project.gates]
    spec = project.environment
    if spec is not None:
        commands += list(spec.setup)
        commands += list(spec.test_prepare)
        commands += [s.command for s in spec.services]
    return binaries(commands)


def write_shims(
    directory: Path, *, names: Sequence[str], docker: str, container: str,
    tree: Path, workdir: str,
) -> list[str]:
    """A program of each name that runs the real one inside `container`.

    The directory mapping is the part worth reading twice. A gate command is
    `cd backend && pytest`, and an agent writes exactly that: the `cd` happens
    on this side, in the worktree, and the `pytest` has to happen on the other
    side, in the same relative place. So each shim takes its own `$PWD`, strips
    the worktree prefix, and hands the remainder to `docker exec -w`. Standing
    in `<tree>/backend` and typing `pytest` runs pytest in `<workdir>/backend`,
    which is the same directory through the mount.

    Written outside the worktree on purpose. Anything inside it shows up in
    `git status`, and the executor reads `git status` to decide what the unit
    wrote -- a shim in there would be collected as the agent's work.
    """
    written: list[str] = []
    # Resolved, and read back with `pwd -P` on the other side. `$PWD` is an
    # ordinary environment variable inherited from whoever launched the shim --
    # it says where the *parent* stood, not where this process is, so a shim
    # invoked after a `cd` reported the wrong directory and every command ran at
    # the tree root. On macOS the resolution matters twice over: a worktree
    # under /var is really under /private/var, and the two never match as
    # strings.
    prefix = str(Path(tree).resolve()).rstrip("/")
    for name in names:
        if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,64}", name):
            continue
        path = directory / name
        path.write_text(
            "#!/bin/sh\n"
            f"# {name}, run in this unit's own container rather than on the host.\n"
            "# The worktree you are editing is mounted there, so this is the same\n"
            "# code -- with the project's dependencies and its database present.\n"
            'here=$(pwd -P)\n'
            f'rel=${{here#{shlex.quote(prefix)}}}\n'
            'if [ "$rel" = "$here" ]; then rel=""; fi\n'
            f'exec {shlex.quote(docker)} exec -i -w "{workdir}$rel" '
            f'{shlex.quote(container)} {shlex.quote(name)} "$@"\n',
            encoding="utf-8",
        )
        path.chmod(0o755)
        written.append(name)

    # An escape hatch, always present. The shims above cover what the project's
    # own commands name, which is what an agent reaches for nine times out of
    # ten -- but not `psql` when a query would settle an argument, and not a
    # tool a unit legitimately needs and this project has never run. Without
    # this, the answer to "the environment does not have what I need" is a
    # silent fall back to the host, which is the failure this module exists to
    # end.
    escape = directory / "factory-exec"
    escape.write_text(
        "#!/bin/sh\n"
        "# Run any command line inside this unit's container:\n"
        "#     factory-exec psql \"$DATABASE_URL\" -c '\\dt'\n"
        "here=$(pwd -P)\n"
        f'rel=${{here#{shlex.quote(prefix)}}}\n'
        'if [ "$rel" = "$here" ]; then rel=""; fi\n'
        f'exec {shlex.quote(docker)} exec -i -w "{workdir}$rel" '
        f'{shlex.quote(container)} sh -c "$*"\n',
        encoding="utf-8",
    )
    escape.chmod(0o755)
    return written


def _session_container(runner) -> str:
    """The held container a shim can exec into, if this runner offers one."""
    return (getattr(runner, "session_id", "") or "").strip()


def _services_note(ports: dict[str, int]) -> str:
    """Where each running service is, said to an agent that will write tests.

    The variables were always in its environment and nothing said so. A worker
    writing a browser fixture that seeds a board through the API had no word for
    where the API was, so it used the project's own `E2E_API_BASE` with a
    `localhost:8300` default -- right on a developer's laptop, nothing at all in
    the checks, where every port is chosen fresh. Every browser test died in
    that fixture, three rounds running, and the repairers could not see why.
    """
    if not ports:
        return ""
    rows = "\n".join(
        f"- `{name}` -- `FACTORY_URL_{name.upper().replace('-', '_')}` "
        f"(now `http://127.0.0.1:{port}`)"
        for name, port in sorted(ports.items()))
    return (
        "\n## Where the running services are\n\n"
        f"{rows}\n\n"
        "They will be running again when the project's checks run your code later, "
        "on different ports. So any code of yours that reaches one from outside the "
        "page -- a fixture that creates data through an API, a test that calls it "
        "directly -- reads its address from that variable when it runs. A fallback "
        "for someone running the tests by hand is fine after it; nothing may come "
        "before it, not a variable the project uses elsewhere and not a port "
        "written down, because either one reaches nothing in the checks and the "
        "test dies before it asserts anything.\n"
    )


def _inside_note() -> str:
    """The brief, for an agent that is already standing in the environment.

    No shims, no escape hatch, no mapping between a path here and a path there:
    it is in the container, so the project's commands are simply the project's
    commands. Saying otherwise would be worse than saying nothing -- the host
    paragraph teaches an agent to reach for a wrapper that is not on its PATH,
    and an agent that cannot find the tool it was told to use concludes it has
    no tools.
    """
    return (
        "\n\n---\n\n# The environment\n\n"
        "**You are inside this unit's environment.** Not beside it -- in it. The "
        "project's dependencies are installed here, its services are running here, and "
        "its database has been migrated and seeded here exactly as its own checks "
        "require. The checkout you are editing is this container's working directory.\n\n"
        "So run the project's commands the way its own documentation does, from the "
        "worktree, with nothing in front of them. Your edits are live the moment you "
        "save, because the tree you are editing is the tree they run against.\n\n"
        "It is yours alone. Nothing you migrate, seed, truncate or corrupt reaches "
        "another unit, another round, or the machine this factory runs on, and all of "
        "it is destroyed when this unit ends. So there is no reason to be careful with "
        "it and every reason to use it: run the migration, run the seed, run the tests "
        "you wrote. Code you have executed is worth more here than code you have "
        "reasoned about, and a unit that reports work it never ran is the failure this "
        "environment exists to end.\n"
    )


def _note(ready: bool, names: Sequence[str], problem: str, prepared_log: str) -> str:
    if not ready:
        # The log goes into the note, not only the command: an agent told which
        # command failed and never what it said cannot tell "the seed is
        # broken" from a missing environment variable it could set itself.
        seen = (prepared_log or "").strip()
        detail = (
            "\n\nWhat the attempt printed:\n\n```\n" + seen[-2000:] + "\n```"
        ) if seen else ""
        return (
            "\n\n---\n\n# The environment\n\n"
            "This unit has **no running environment**. The factory tried to bring one "
            "up and could not:\n\n"
            f"{problem or 'no reason was recorded'}"
            f"{detail}\n\n"
            "Write the code anyway, and say plainly in your disclosure that nothing was "
            "executed. Do not describe unrun code as verified.\n"
        )
    listed = ", ".join(f"`{n}`" for n in names) or "(none)"
    return (
        "\n\n---\n\n# The environment\n\n"
        "This unit has a **running environment of its own**, and you are expected to "
        "use it. The project's dependencies are installed, its services are up, and "
        "its database has been migrated and seeded exactly as its own checks require.\n\n"
        f"These run there rather than on this host: {listed}. Use them normally -- "
        "`cd backend && pytest`, `ruff check .`, `alembic upgrade head` -- from inside "
        "the worktree. Your edits are visible to them the moment you save, because they "
        "run against this same tree. For anything else, `factory-exec <command>` runs "
        "one command line in there (`factory-exec psql \"$DATABASE_URL\" -c '\\dt'`).\n\n"
        "It is yours alone. Nothing you migrate, seed, truncate or corrupt reaches "
        "another unit, another round, or the machine you are running on, and it is "
        "destroyed when this unit ends. So there is no reason to be careful with it "
        "and every reason to use it: run the migration, run the seed, run the tests you "
        "wrote. Code you have executed is worth more here than code you have reasoned "
        "about, and a unit that reports work it never ran is the failure this "
        "environment exists to end.\n"
    )


class _UnitProject:
    """A project view whose `environment` is this unit's alone.

    `runner_for` writes the built image back onto the spec, and `run_setup`
    closes its container by committing an image and writing *that* back too.
    Both mutate `project.environment`, which is one object shared by every
    concurrent unit -- so three workers preparing at once would overwrite each
    other's image and two of them would run their checks in a stack prepared for
    somebody else. A deep copy per unit costs nothing and removes the race
    rather than narrowing the window.
    """

    def __init__(self, project, spec) -> None:
        self._project = project
        self.environment = spec

    def __getattr__(self, name):
        return getattr(self._project, name)


def _harness_layers(*routes) -> list:
    """The routes whose harness has to be in the unit's image, in layer order.

    Each one that runs its sessions inside the container, once. A fallback on
    the same route as the primary adds nothing.
    """
    out: list = []
    for route in routes:
        if (route is not None and route.authors_in_container()
                and route.name not in {r.name for r in out}):
            out.append(route)
    return out


@asynccontextmanager
async def unit_environment(
    project, config, *, label: str, tree: Path, prepare: bool = True,
    serve: bool = True, harness_route=None, fallback_route=None,
) -> AsyncIterator[UnitEnv]:
    """Bring up one unit's stack around `tree`, and take it down after.

    `label` names the compose project and therefore the isolation boundary. It
    must be unique per concurrently-running unit; `<feature>-<unit>` is what the
    pipeline passes, and a repair round adds its own number.

    `harness_route` is the route whose agent will run *inside* this container
    rather than on the host. Given one, the unit's image gains a layer holding
    that harness, because an agent has to stand in the image that has the
    project's own dependencies -- that is the whole reason for putting it
    there.

    `fallback_route` is where the same agent goes when its own route refuses,
    and it runs in this same container. Its harness is layered on too. Left
    out, the breaker's fallback exec'd `/opt/harness/venv/bin/python` into an
    image holding only the codex harness, died before it started in every
    round of a run, and the packet said the breaker had written no probe.

    `serve=False` is a container with the toolchain installed and nothing
    running: no services, no prepared state. It is what the oracle gets. It
    writes tests for a system it must never have seen run, so it is given
    somewhere sealed to work and to load its own files -- the base commit's
    checkout, set up -- and nothing standing that it could ask questions of.
    """
    spec = project.environment
    if spec is None or spec.kind == "host":
        # A host project's environment is the host, which the agent already has.
        # Saying "ready" here would be a claim about services nobody started.
        yield NO_ENV
        return

    shim_dir = Path(tempfile.mkdtemp(prefix="factory-shims-"))
    runner = None
    try:
        unit_project = _UnitProject(project, spec.model_copy(deep=True))
        try:
            runner, _ = await runner_for(unit_project, config, feature_id=label)
        except DockerError as exc:
            yield UnitEnv(report=str(exc), problem=str(exc),
                          note=_note(False, (), str(exc), ""))
            return

        layers = _harness_layers(harness_route, fallback_route)
        if layers:
            # Two runners, two places the same fact lives. A docker runner holds
            # the image it runs; a compose runner holds a spec whose `image`
            # replaces the gate service's own in the override it generates
            # (`_override`) -- which is already how a production service image
            # gets swapped for the toolchain one. Either way the agent ends up
            # standing in the image that has the project's dependencies, which
            # is the whole reason for putting it in there.
            spec_holder = getattr(runner, "spec", None)
            base = getattr(runner, "image", "") or getattr(spec_holder, "image", "")
            if not base:
                problem = (
                    f"route {layers[0].name!r} runs its agent inside the unit's "
                    "container, and this project's environment resolves to no image "
                    "the factory built -- so there is nothing to add a harness to. "
                    "Give the environment a Dockerfile (its gate service is swapped "
                    "for that image anyway), or take `session_in_container` off the "
                    "route."
                )
                yield UnitEnv(report=problem, problem=problem,
                              note=_note(False, (), problem, ""))
                return
            try:
                layered = base
                for layer in layers:
                    layered = await harness_image(layered, layer, config.docker)
            except DockerError as exc:
                yield UnitEnv(report=str(exc), problem=str(exc),
                              note=_note(False, (), str(exc), ""))
                return
            if hasattr(runner, "image"):
                runner.image = layered
            elif spec_holder is not None:
                # The unit's own deep copy of the spec, so this cannot reach
                # another unit preparing at the same time.
                spec_holder.image = layered

        env_spec = unit_project.environment
        setup: list[GateResult] = []
        if prepare and env_spec.setup:
            setup = await run_setup(env_spec.setup, tree, runner,
                                    warm=unit_project.warm_gates)
        failed = [r for r in setup if not r.passed and not r.skipped]
        if failed:
            stalled = getattr(runner, "up_problem", "")
            # A stack that did not start is said as the one thing it is. The
            # commands after it were not run, and naming them would put the
            # fault on the project's own setup.
            problem = (
                f"the project's environment did not start, so nothing was set up. {stalled}"
                if stalled else
                f"{len(failed)} setup command(s) failed, so the toolchain this "
                f"unit needs is not installed: "
                + "; ".join(f"`{r.command}`" for r in failed))
            log = "\n\n".join(
                f"$ {r.command}\n[exit {r.exit_code}]\n{(r.output_tail or '')[-800:]}"
                for r in failed
            )
            yield UnitEnv(report=problem, problem=problem, log=log,
                          note=_note(False, (), problem, log))
            return

        in_container = bool(layers)
        async with test_session(
            tree, runner,
            services=list(env_spec.services) if prepare and serve else (),
            # An agent that runs in here has to reach its provider, and the
            # session the gates use must not. Same machinery, opposite needs,
            # so this is the one thing the two kinds of session differ by.
            network=in_container,
            prepare=list(env_spec.test_prepare) if prepare and serve else (),
            # Unconditionally: the container IS the environment being prepared
            # here. Everywhere else it is machinery a service needs; for an
            # agent it is the whole point, and a project with no services
            # declared wants one no less than a project with two.
            hold=True,
        ) as prepared:
            container = _session_container(runner)
            if not prepared.ready or not container:
                problem = prepared.problem or (
                    "this runner could not hold a container open, so there is nothing "
                    "for the project's own commands to run in")
                yield UnitEnv(report=problem, problem=problem, log=prepared.report,
                              note=_note(False, (), problem, prepared.report))
                return

            # Shims exist to carry a command from the host into this
            # container. An agent that runs inside it needs none of them, and
            # writing them anyway leaves a PATH full of wrappers that would
            # each launch a second container from within this one.
            names = [] if in_container else write_shims(
                shim_dir, names=project_binaries(project), docker=config.docker.binary,
                container=container, tree=tree, workdir=env_spec.workdir or "/workspace",
            )
            variables = {
                "PATH": f"{shim_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "FACTORY_ENV_CONTAINER": container,
                "FACTORY_ENV_WORKDIR": env_spec.workdir or "/workspace",
                **dict(getattr(runner, "session_env", {}) or {}),
            }
            yield UnitEnv(
                ready=True, vars=variables, runner=runner,
                session=dict(getattr(runner, "session_env", {}) or {}),
                note=(_inside_note() if in_container else _note(True, names, "", ""))
                + _services_note(prepared.ports),
                report=(f"{label}: the agent runs inside {container[:12]}" if in_container
                        else f"{label}: {len(names)} command(s) routed into {container[:12]}"),
            )
    finally:
        shutil.rmtree(shim_dir, ignore_errors=True)
        if runner is not None:
            down = getattr(runner, "down", None)
            if down is not None:
                await down()
