"""Gates: shell commands run against the repo after the lanes converge.

No model is invoked in this module and none should ever be. (AC-6.4) A gate is
either a process that exited zero or a number that cleared a threshold. Both are
facts. Everything else in this system is an argument.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import time
from uuid import uuid4
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import (Any, AsyncIterator, Callable, Literal, Mapping, Protocol, Sequence,
                    runtime_checkable)

from xml.etree import ElementTree

from . import git
from .config import GateConfig
from .schemas import (
    BlindPlacement,
    CaseOutcome,
    DisposableDatabase,
    GateReport,
    GateResult,
    Located,
    PlacementResult,
    Service,
    TierResult,
    TestFileCommand,
)
from .workspace import REPORT_PREFIX, PathEscape, safe_join

TAIL_CHARS = 4_000
#: How much of a red check's output is kept from its start.
HEAD_CHARS = 2_000


# What a gate writes in `parse_metric` when the command's exit status is the
# whole answer. Not a pattern -- a name for "there is nothing to extract".
EXIT_CODE_METRIC = "exit_code"

@dataclass
class Execution:
    exit_code: int
    output: str
    timed_out: bool = False
    started: bool = True


@runtime_checkable
class Runner(Protocol):
    """Where a command actually executes.

    The seam between "run this on the host, in the worktree" and "run this in
    the project's container, with the worktree mounted and the network off".
    Everything above this protocol is identical either way.

    `carries_setup` is the one place they are not identical. Setup installs a
    toolchain, and where that toolchain survives is a property of the runner:
    on the host it stays on the machine, in a container it lives in an image
    this runner made and takes with it when it goes down. A caller that wants
    to run setup once and rely on it afterwards has to ask.
    """

    carries_setup: bool

    async def execute(
        self, command: str, *, cwd: Path, timeout_s: float, network: bool = True,
    ) -> Execution:
        ...


class LocalRunner:
    """Straight onto the host, in the given directory.

    Correct for a single feature at a time. Under concurrency it is not enough:
    a worktree isolates files, not ports, databases or caches, so two suites
    running here at once can interfere and produce results that look like
    signal.
    """

    kind = "local"
    #: A subprocess on the host has the host's network, and nothing here can
    #: take it away. Recorded rather than pretended: `network=False` was honoured
    #: by one of three runners while the design docs asserted it for all of them.
    enforces_egress_policy = False
    # What setup installs here lands on this machine and in this checkout, and
    # both outlive any one runner. This is the only runner for which that holds.
    carries_setup = True

    def __init__(self) -> None:
        self._services: list[asyncio.subprocess.Process] = []
        #: Variables every command in an open `test_session` receives. The
        #: allocated service ports live here, so the command that starts a
        #: service and the command that talks to it cannot disagree about which
        #: port that is -- they read the same name.
        self.session_env: dict[str, str] = {}

    async def start_service(self, command: str, *, cwd: Path, name: str) -> str:
        """Start a service as a process group this runner will kill.

        The container runners get teardown for free -- discarding the container
        takes everything in it -- and this one does not, so it is the only place
        that has to hold a handle. It holds a real one: `start_new_session` puts
        the service in its own process group, and `stop_services` signals the
        group, so a server that forks workers goes down with them.

        That is the same thing `execute` already does for a command that runs
        past its timeout. Nothing new is being invented here; what is new is
        that it is done in Python, once, instead of written as `nohup ... &`
        plus `kill $UPID` into every gate command that needed a server.
        """
        try:
            proc = await asyncio.create_subprocess_shell(
                with_env(command, self.session_env),
                cwd=str(cwd),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            return f"could not start: {exc}"
        self._services.append(proc)
        return ""

    async def stop_services(self) -> None:
        """Signal every service's process group, then stop waiting on it.

        Runs from a `finally`, so it must not raise and must not hang: a service
        that ignores SIGKILL cannot be allowed to hold the run open. Anything
        still alive after the wait is left to the operating system, which is a
        worse outcome than a clean stop and a much better one than never
        returning.
        """
        for proc in self._services:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass
        self._services = []

    async def execute(
        self, command: str, *, cwd: Path, timeout_s: float, network: bool = True,
    ) -> Execution:
        try:
            proc = await asyncio.create_subprocess_shell(
                with_env(command, self.session_env),
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            return Execution(exit_code=127, output=f"could not start: {exc}", started=False)

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            return Execution(
                exit_code=proc.returncode if proc.returncode is not None else -1,
                output=(stdout or b"").decode("utf-8", errors="replace"),
            )
        except asyncio.TimeoutError:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass
            return Execution(
                exit_code=-9, timed_out=True,
                output=f"timed out after {timeout_s}s and was killed",
            )


def _tail(text: str, limit: int = TAIL_CHARS) -> str:
    if len(text) <= limit:
        return text
    return "... [head truncated]\n" + text[-limit:]


#: Below this, a run is not worth starting. A checkout, a node_modules, a
#: database and an image layer or two land in the same place, and the failures
#: a nearly-full disk produces are the expensive kind -- they look like the
#: code. Deliberately generous: the cost of refusing early is one message, and
#: the cost of not refusing is an hour of agents repairing a disk.
_MIN_FREE_MB = 3_072

#: What a full disk says, whichever program was the first to need space.
DISK_FULL = re.compile(
    r"no space left on device|DiskFullError|ENOSPC|could not extend file|disk quota exceeded",
    re.IGNORECASE)


def disk_full_problem(command: str, output: str) -> str:
    """The sentence a full disk deserves, or empty when `output` shows none.

    A full disk surfaces as a traceback from whichever program wrote first --
    once an `alembic upgrade` inside a database driver, seven gates deep and
    all reported as blocked. The cause is said first, in the operator's words.
    """
    if not DISK_FULL.search(output or ""):
        return ""
    return (f"Docker ran out of disk space while `{command}` ran, so nothing was run. "
            "Images, databases and builds all live on Docker's disk, and this project "
            "could not write to it. Free some -- `docker builder prune -af` clears the "
            "build cache, `docker image prune -af` unused images -- or raise the disk "
            "limit in Docker Desktop's settings, then run it again.")


async def _free_space_mb(runner: "Runner", cwd: str | Path, timeout_s: float) -> float | None:
    """Free space where the checks are about to run, in MB.

    Measured inside the environment rather than on the host: a compose project
    writes into the Docker VM's disk, and the host having 80 GB spare says
    nothing about that. `df -P` is the portable output -- one row, blocks of
    1024 -- and anything unexpected returns None so a parsing surprise can
    never be the thing that stops a run.
    """
    try:
        execution = await runner.execute(
            f"df -Pk {shlex.quote(str(cwd))}", cwd=Path(cwd),
            timeout_s=min(timeout_s, 60.0), network=False)
    except Exception:  # noqa: BLE001 -- a probe is never allowed to fail a run
        return None
    if not execution.started or execution.exit_code != 0:
        return None
    rows = [r for r in (execution.output or "").splitlines() if r.strip()]
    if len(rows) < 2:
        return None
    fields = rows[-1].split()
    if len(fields) < 4:
        return None
    try:
        return int(fields[3]) / 1024
    except ValueError:
        return None


async def run_gate(
    gate: GateConfig, cwd: str | Path, runner: Runner | None = None, *, network: bool = False,
) -> GateResult:
    """AC-6.1 / AC-6.3 -- run one command, capture the tail, enforce the timeout.

    One command, one exit code, and no opinion about what the command printed.
    A gate does not search its own output for the blind tests' names: a
    project's ordinary test gate cannot say anything about an individual test.
    What it says is whether it passed. Per-test facts come from running
    the tests one at a time -- `run_per_file`.
    """
    runner = runner or LocalRunner()
    started = time.monotonic()
    result = GateResult(name=gate.name, command=gate.command, threshold=gate.threshold,
                        threshold_max=gate.threshold_max)

    # A check that writes what it found is given somewhere to write it: named
    # the way per-test reports are, so the sweep and the commit both know it is
    # never the project's, and absolute on the side that runs the command.
    command = gate.command
    report: Path | None = None
    ours = False
    before: float | None = None
    if gate.report_format and "{report}" in command:
        stem = f"{REPORT_PREFIX}{uuid4().hex[:8]}.{gate.report_format}"
        report = Path(cwd) / stem
        ours = True
        mounted = workdir_of(runner)
        named = report if mounted in ("", ".") else PurePosixPath(mounted) / stem
        command = command.replace("{report}", str(named))
    elif gate.report_format and gate.report_path.strip():
        # A tool that writes where it likes. Read only if this run rewrote it:
        # last week's report sitting in the checkout is not this run's answer.
        try:
            report = safe_join(Path(cwd), gate.report_path.strip())
            before = report.stat().st_mtime if report.is_file() else None
        except (PathEscape, OSError):
            report = None

    # Gates run without the network. Anything that needs to fetch belongs in the
    # environment's setup step, which runs once per sandbox before this.
    execution = await runner.execute(
        command, cwd=Path(cwd), timeout_s=gate.timeout_s, network=network,
    )
    result.exit_code = execution.exit_code
    result.timed_out = execution.timed_out
    output = execution.output
    if report is not None:
        roots = [str(Path(cwd)), workdir_of(runner)]
        stale = (not ours and report.is_file() and before is not None
                 and report.stat().st_mtime == before)
        if stale:
            result.report = "missing"
        elif gate.report_format == "sarif":
            result.located, result.report = read_sarif(report, roots=roots)
        elif gate.report_format == "mutation-json":
            result.located, result.metric, result.report = read_mutation(report, roots=roots)
        else:
            result.coverage, result.report = read_coverage(
                report, gate.report_format, roots=roots)
        if ours:
            report.unlink(missing_ok=True)
    elif gate.report_format:
        result.report = "missing"

    if not execution.started:
        result.duration_s = round(time.monotonic() - started, 3)
        result.output_tail = output
        record_evidence(result, output)
        result.started = False
        result.passed = bool(gate.optional)
        result.skipped = bool(gate.optional)
        return result

    result.duration_s = round(time.monotonic() - started, 3)
    result.output_tail = _tail(output)
    record_evidence(result, output)

    # AC-6.2 -- a metric with a threshold, for signals the exit code does not carry.
    #
    # `exit_code` is a sentinel, not a pattern: it is what a gate says when the
    # command's own status is the whole answer. It was being compiled as a regex,
    # found nothing, and failed the gate -- so a passing command reported as a
    # failure and `threshold` meant nothing on any gate in the project.
    if gate.parse_metric and gate.parse_metric.strip() != EXIT_CODE_METRIC:
        match = re.search(gate.parse_metric, output)
        if match and match.groups():
            try:
                result.metric = float(match.group(1))
            except ValueError:
                result.metric = None
        bound = _bound_description(gate)
        if result.metric is None:
            # An unparseable metric is missing data. That is fatal only when a
            # threshold was riding on it: you cannot check a bound you never
            # read. Without one the number was informational, so the command's
            # own verdict still stands.
            note = f"\n[gate: pattern {gate.parse_metric!r} did not match a number"
            if bound:
                result.passed = False
                result.output_tail += note + f", and {bound} cannot be checked]"
            else:
                # ...and falling back has to actually happen. This branch wrote
                # the note and left `passed` at its default of False, so a
                # command that exited zero and printed nothing for the pattern
                # to find -- which is exactly what a clean lint or typecheck
                # prints -- was reported red while saying "All checks passed!".
                result.passed = result.exit_code == 0
                result.output_tail += note + "; falling back to the exit code]"
        elif bound:
            # Two bounds, either or both. `threshold` is a floor and reads a
            # number that should not shrink -- coverage, a pass rate.
            # `threshold_max` is a ceiling and reads one that should not grow,
            # which is the shape of every pre-existing debt a repository carries:
            # set it at today's count and the check is green now and red on the
            # next regression, without the cleanup having to happen first.
            ok = True
            if gate.threshold is not None:
                ok = ok and result.metric >= gate.threshold
            if gate.threshold_max is not None:
                ok = ok and result.metric <= gate.threshold_max
            result.passed = ok and _exit_still_counts(gate, result)
        else:
            result.passed = result.exit_code == 0
    elif result.report == "read":
        # A check whose report was read has its number without a pattern: the
        # count of what it found, or the share of lines that ran. Bounds hold
        # against that, which is what lets a tool that prints nothing to a pipe
        # still be held at today's reading.
        result.metric = (float(len(result.located)) if gate.report_format == "sarif"
                         else result.metric if gate.report_format == "mutation-json"
                         else coverage_total(result.coverage))
        if result.metric is None:
            result.passed = result.exit_code == 0 and not _bound_description(gate)
        elif _bound_description(gate):
            ok = True
            if gate.threshold is not None:
                ok = ok and result.metric >= gate.threshold
            if gate.threshold_max is not None:
                ok = ok and result.metric <= gate.threshold_max
            result.passed = ok and _exit_still_counts(gate, result)
        else:
            result.passed = result.exit_code == 0
    else:
        result.passed = result.exit_code == 0

    if gate.optional and not result.passed:
        result.skipped = True
    # A red check keeps how its output began as well as how it ended: the cause
    # is often said first and only summarised last.
    if not result.passed and len(output) > TAIL_CHARS:
        result.output_head = output[:HEAD_CHARS]
    return result


def _exit_still_counts(gate: GateConfig, result: GateResult) -> bool:
    """Whether a bound leaves the exit code's verdict standing.

    A bound replaces the exit code for a count of problems: a linter exits 1
    on 129 old findings, and holding it at 129 is the point. A tests check's
    exit code says something its number does not -- a test failed -- and the
    same exit code carries both. Held at a coverage figure, a suite with a
    failing test read green as long as the figure held. So for the tests
    family the bound is added to the exit code, never put in its place.
    """
    return gate.family != "tests" or result.exit_code == 0


def _relative(uri: str, bases: Sequence[str]) -> str:
    """A path a tool named, as the repository names it."""
    uri = uri[len("file://"):] if uri.startswith("file://") else uri
    for base in bases:
        if uri.startswith(base):
            return uri[len(base):]
    return uri[2:] if uri.startswith("./") else uri


def read_coverage(
    path: Path, fmt: str, roots: Sequence[str] = (),
) -> tuple[dict[str, list[list[int]]], str]:
    """Which lines could run and which did, per file, from a coverage report.

    Three formats, because three cover nearly every ecosystem: Cobertura XML,
    LCOV, and coverage.py's JSON. Read by format, never by tool. As with SARIF,
    a report that is not there or cannot be parsed is said to be so -- it is
    not a project whose every line ran.
    """
    if not path.is_file():
        return {}, "missing"
    bases = [str(r).rstrip("/") + "/" for r in roots if r and r not in (".", "")]
    lines: dict[str, tuple[set[int], set[int]]] = {}

    def note(file: str, number: int, ran: bool) -> None:
        can, did = lines.setdefault(_relative(file, bases), (set(), set()))
        can.add(number)
        if ran:
            did.add(number)

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if fmt == "cobertura":
            tree = ElementTree.fromstring(text)
            sources = [(s.text or "").strip().rstrip("/") for s in tree.iter("source")]
            for cls in tree.iter("class"):
                name = cls.get("filename") or ""
                if not name.startswith("/") and sources:
                    joined = [f"{src}/{name}" for src in sources if src]
                    name = next((j for j in joined if any(j.startswith(b) for b in bases)),
                                name)
                for line in cls.iter("line"):
                    note(name, int(line.get("number") or 0), int(line.get("hits") or 0) > 0)
        elif fmt == "lcov":
            current = ""
            for raw in text.splitlines():
                if raw.startswith("SF:"):
                    current = raw[3:].strip()
                elif raw.startswith("DA:") and current:
                    number, hits = raw[3:].split(",")[:2]
                    note(current, int(number), int(float(hits)) > 0)
                elif raw.startswith("end_of_record"):
                    current = ""
        elif fmt == "coverage-json":
            for name, entry in (json.loads(text).get("files") or {}).items():
                for number in entry.get("executed_lines") or []:
                    note(name, int(number), True)
                for number in entry.get("missing_lines") or []:
                    note(name, int(number), False)
        else:
            return {}, "unreadable"
    except (ValueError, ElementTree.ParseError, AttributeError, TypeError):
        return {}, "unreadable"
    return ({k: [sorted(can), sorted(did)] for k, (can, did) in lines.items()}, "read")


def read_mutation(path: Path, roots: Sequence[str] = ()) -> tuple[list[Located], float | None, str]:
    """The mutants no test caught, and the score, from a mutation report.

    The mutation-testing report schema, which Stryker writes for JavaScript,
    TypeScript, C# and Scala and which other tools write through a plugin. A
    mutant that `Survived`, or that no test even reached (`NoCoverage`), is a
    change to the code that every test let through -- the plainest evidence
    there is that a test asserts less than it seems to. Each comes back as a
    sentence about the line, the way a worker sent back for it reads it.
    """
    if not path.is_file():
        return [], None, "missing"
    try:
        files = (json.loads(path.read_text(encoding="utf-8", errors="replace")).get("files")
                 or {})
    except (ValueError, AttributeError):
        return [], None, "unreadable"
    bases = [str(r).rstrip("/") + "/" for r in roots if r and r not in (".", "")]
    survived: list[Located] = []
    killed = total = 0
    for name, entry in files.items():
        source = str((entry or {}).get("source") or "").splitlines()
        for mutant in (entry or {}).get("mutants") or []:
            status = str(mutant.get("status") or "")
            if status in ("Killed", "Timeout"):
                killed += 1
                total += 1
            elif status in ("Survived", "NoCoverage"):
                total += 1
                start = (mutant.get("location") or {}).get("start") or {}
                end = (mutant.get("location") or {}).get("end") or {}
                line = int(start.get("line") or 0)
                original = ""
                if source and 0 < line <= len(source) and line == int(end.get("line") or line):
                    original = source[line - 1][int(start.get("column") or 1) - 1:
                                                int(end.get("column") or 1) - 1]
                change = (f"changing `{original}` to `{mutant.get('replacement', '')}`"
                          if original else f"a {mutant.get('mutatorName', 'change')} here")
                survived.append(Located(
                    path=_relative(str(name), bases), start_line=line,
                    end_line=int(end.get("line") or line), rule=str(mutant.get("mutatorName") or ""),
                    message=change + (" failed no test" if status == "Survived"
                                      else " was never run by any test")))
    score = round(100.0 * killed / total, 2) if total else None
    return survived, score, "read"


def coverage_total(coverage: Mapping[str, Sequence[Sequence[int]]]) -> float | None:
    """The share of runnable lines that ran, in percent; None when none could."""
    can = sum(len(pair[0]) for pair in coverage.values())
    did = sum(len(pair[1]) for pair in coverage.values())
    return round(100.0 * did / can, 2) if can else None


def read_sarif(path: Path, roots: Sequence[str] = ()) -> tuple[list[Located], str]:
    """What a check found, file and line, from the SARIF report it wrote.

    SARIF because it is the one format the tools that matter here share --
    linters, type checkers and security scanners alike -- so nothing in this
    module needs to know which tool wrote it. Paths are made repo-relative
    against either side of the mount, because a tool in a container names
    files by the path it sees.

    `("missing" | "unreadable")` is never an empty finding list in disguise:
    a report that is not there says nothing about the code.
    """
    if not path.is_file():
        return [], "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        runs = data.get("runs") or []
    except (ValueError, AttributeError):
        return [], "unreadable"
    bases = [str(r).rstrip("/") + "/" for r in roots if r and r not in (".", "")]
    found: list[Located] = []
    for run in runs if isinstance(runs, list) else []:
        for item in (run or {}).get("results") or []:
            for loc in (item or {}).get("locations") or [{}]:
                physical = (loc or {}).get("physicalLocation") or {}
                uri = _relative(
                    str(((physical.get("artifactLocation") or {}).get("uri")) or ""), bases)
                region = physical.get("region") or {}
                start = int(region.get("startLine") or 0)
                message = (item.get("message") or {}).get("text") or ""
                found.append(Located(
                    path=uri, start_line=start, end_line=int(region.get("endLine") or start),
                    rule=str(item.get("ruleId") or ""), message=str(message)[:300]))
                break
    return found, "read"


#: What an operating system's resolver says when a name has no answer --
#: glibc, musl, the JVM, Node, Go, curl -- which is what an offline check says
#: when its tool reaches for something setup did not fetch.
NAME_UNRESOLVED = re.compile(
    r"Temporary failure in name resolution|Unknown host|UnknownHostException|"
    r"Could not resolve host|Name or service not known|Name does not resolve|"
    r"No address associated with hostname|nodename nor servname|"
    r"getaddrinfo (?:ENOTFOUND|EAI_AGAIN)|dial tcp: lookup", re.I)


def warm_name(gate: str) -> str:
    """How a check's warm-up run is named among setup's results: under
    `setup[`, because it is setup -- plumbing the page folds away."""
    return f"setup[warm:{gate}]"


async def run_setup(
    commands: Sequence[str], cwd: str | Path, runner: Runner | None = None,
    warm: Sequence[GateConfig] = (),
) -> list[GateResult]:
    """Dependency reconciliation, with the network available.

    Recorded as gate results so a failure here is visible rather than surfacing
    later as a mystery gate failure. This is not optional decoration: an image
    that installs dependencies at build time still has them shadowed the moment
    a worktree is mounted over the workdir, so a `node_modules` baked into the
    image is gone by the time a gate looks for it. Setup puts it back, in the
    checkout the gates actually run against.

    `warm` is checks to run once more, here, with the network, after the
    commands. Some tools fetch part of what they need only when they run:
    Maven downloads its test provider the first time a test executes, so a
    setup that installs dependencies and skips tests leaves it behind, and the
    check then dies offline on `Unknown host repo.maven.apache.org`. Knowing
    each tool's habits is a list that never ends, so this does not try: it
    runs the check, keeps whatever it fetched, and ignores how it did --
    `optional`, so its outcome is never a setup failure. What it wrote into
    the stack's services goes too: the stack is reset afterwards, so the
    checks start from the same state they would have without it.
    """
    runner = runner or LocalRunner()
    results: list[GateResult] = []
    # One container for the whole sequence, where the runner can offer one.
    # Without it each command gets a fresh container and installs into a
    # filesystem that is discarded before the next line runs -- `pip install`
    # succeeding and `ruff: not found` immediately after.
    opener = getattr(runner, "open_session", None)
    closer = getattr(runner, "close_session", None)
    if opener is not None:
        # Unhardened: this is the session that installs, and a package manager
        # needs the capabilities a gate never does. What it installs is
        # committed to an image, and the session the gates and the agent get is
        # opened from that image with every guard on.
        try:
            await opener(Path(cwd), harden=False)
        except TypeError:                       # a runner from before the flag
            await opener(Path(cwd))
        # A session that could not open is not a detail: without it these
        # commands install into containers that are thrown away, and every gate
        # afterwards fails with "not found" for a reason nothing on the page
        # explains. Say so here, once, rather than eight times downstream.
        failure = getattr(runner, "_session_error", "")
        if failure:
            results.append(GateResult(
                name="setup[session]", command="hold one container open for setup",
                exit_code=1, passed=False, started=True,
                output_tail=(
                    "could not hold a container open, so anything these commands install "
                    "outside the mounted worktree will not survive to the gates:\n" + failure
                ),
            ))
    # A stack that did not come up is not a session that merely could not be
    # held: every command after it fails for the same reason, and listing each
    # as its own failure blamed the project's `npm ci` and `mvnw verify` for a
    # database that could not start on a full disk. One failure, said once.
    stalled = bool(getattr(runner, "up_problem", ""))
    try:
        for i, command in enumerate([] if stalled else commands):
            gate = GateConfig(name=f"setup[{i}]", command=command, timeout_s=1800.0)
            results.append(await run_gate(gate, cwd, runner, network=True))
        for check in ([] if stalled else warm):
            gate = GateConfig(name=warm_name(check.name), command=check.command,
                              timeout_s=check.timeout_s, optional=True)
            results.append(await run_gate(gate, cwd, runner, network=True))
    finally:
        if closer is not None:
            await closer()
    reset = getattr(runner, "reset_stack", None)
    if warm and not stalled and reset is not None:
        await reset()
    return results


def tracked_changes(cwd: str | Path) -> list[str]:
    """Tracked files that differ from the checkout's commit. New files are not
    counted: a build's output is not a rewrite of the code."""
    changed = git.out(["diff", "--name-only", "HEAD"], cwd) or ""
    return [line for line in changed.splitlines() if line.strip()]


def restore_tracked(cwd: str | Path) -> None:
    """Put tracked files back as the commit has them. Untracked files -- what
    setup installed -- are left alone."""
    git.run(["checkout", "--", "."], cwd)


async def run_gates(
    gates: list[GateConfig], cwd: str | Path, runner: Runner | None = None,
) -> GateReport:
    """AC-6.1 -- concurrently, in the feature's sandbox."""
    if not gates:
        return GateReport(results=[])
    runner = runner or LocalRunner()
    results = await asyncio.gather(*(run_gate(g, cwd, runner) for g in gates))
    return GateReport(results=list(results))


# --------------------------------------------------------------------------
# what actually ran
#
# A gate's exit code says the process was happy. It does not say a single test
# executed. A suite where every blind test was skipped, deselected by a marker,
# or never collected at all exits zero and reads as green -- which is the
# cheapest possible way to make this whole system worthless, and it was the one
# hole `run_gate` could not see. This parses the runner's own summary so the
# claim "the tests ran" is evidence rather than an inference from silence.
#
# No model is involved. Where the output cannot settle the question, the answer
# is `known=False`, and the caller must not upgrade that to a pass.
# --------------------------------------------------------------------------

@dataclass
class TestSummary:
    """What a run of the blind suite established, counted in FILES.

    Not tests. Nothing here parses a runner's output, so the finest grain
    available is "this file's command exited zero" -- and that is the grain the
    numbers are in. Messages that show them say `file` for the same reason: a
    count labelled `tests` that means files is worse than no count.

    `known` means attribution was possible: the suite was run file by file, so
    a failure can be traced to the criteria that file covers. `zero_ran` means
    nothing was executed at all.
    """

    known: bool = False
    passed: int = 0
    failed: int = 0
    total: int = 0
    zero_ran: bool = False

    @property
    def suspicious(self) -> bool:
        return self.zero_ran


def record_evidence(result: GateResult, output: str) -> None:
    """What can be said about a gate from running it once: its exit code.

    Nothing else is read off the output, because the two obvious things to read
    are wrong in the same way. A runner's console summary, read through regexes
    that recognise pytest, jest, vitest, unittest and go by what they print,
    stops matching -- silently, taking the gate's whole signal with it -- the
    moment a runner changes its summary line in a minor release. And searching
    `output_tail` for a test's filename on a line carrying a word like FAILED
    fails whenever the 4,000-character window begins mid-word: a filename cut
    in half is found by nothing, and the criteria that file is the only test
    for are reported to a human as passed while the file is erroring.

    Both are the same mistake: reading a human-readable screen as if it were an
    interface. A command gives you an exit code. That is what is recorded here,
    and everything the packet says about individual tests comes from running
    them individually -- see `run_per_file`.

    `evidence_parsed` stays because callers reason from it: it marks a result
    this code actually looked at, as against one restored from an older run, and
    a result without it may never settle a negative.
    """
    result.output_chars = len(output)
    result.output_truncated = len(output) > TAIL_CHARS
    result.evidence_parsed = True


def command_for(rules: Sequence[TestFileCommand], path: str) -> str:
    """The command that runs this file, or empty if no rule claims it.

    First match wins, so the surveyor puts the specific rules first and a
    catch-all last. `fnmatch` rather than `Path.match` because `*` has to cross
    directory separators here -- `frontend/*` means everything under
    `frontend/`, which is what anyone writing that rule means by it.

    An unmatched file returns empty rather than falling back to the first rule.
    Guessing which runner to point at a file is how a suite comes to report a
    usage error as a failing criterion.
    """
    rule = rule_for(rules, path)
    return rule.command if rule else ""


def rule_for(rules: Sequence[TestFileCommand], path: str) -> TestFileCommand | None:
    """The whole rule, for callers that need more than the run command.

    A rule carries three ways to invoke one file -- run it, load it without
    running, run it and write a report -- and picking between them is the
    caller's business. The matching is not.
    """
    for rule in rules:
        if fnmatch(path, rule.match or "*"):
            return rule
    return None


#: How many times a service may be given a fresh port before the session gives
#: up. More than one because the allocation has a race; small because a service
#: that will not come up on three different free ports is not losing a race.
PORT_ATTEMPTS = 3


def free_port() -> int:
    """A port nothing holds, chosen by the kernel rather than by us.

    Bound on the **wildcard**, not on loopback, and that is the whole of the
    care required. A fixed port was found held by an unrelated container on the
    IPv6 wildcard while `uvicorn --host 127.0.0.1 --port 8000` bound alongside
    it perfectly happily -- two listeners, one number, different address
    families. Nothing failed, the readiness probe went green, and the suite ran
    against the stranger's application. Asking about `0.0.0.0` sees a wildcard
    listener that asking about `127.0.0.1` does not:

        bind 0.0.0.0:8000    -> Address already in use
        bind 127.0.0.1:8000  -> succeeded

    Not a fixed high number and not a random one. The registered range is where
    every dev server already lives, and the dynamic range above it is also where
    the kernel draws ephemeral source ports -- so a number picked by us can
    collide with an outbound connection, once, unreproducibly. A number picked
    by the kernel is one it has just confirmed is free.

    Free at the instant this returns, which is not the same as free forever: the
    socket closes here and the service binds a moment later. Nothing that hands
    a port to another process closes that window, and `test_session` handles it
    the way it must be handled -- by noticing the service did not come up and
    allocating again.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("0.0.0.0", 0))
        return int(probe.getsockname()[1])


def with_env(command: str, env: Mapping[str, str]) -> str:
    """Prefix a command with the session's variables.

    Exported rather than set inline: `FOO=1 cd x && y` applies `FOO` to `cd`
    alone, which is a footgun with a subtle blast radius when every command in
    this system is of exactly that shape.
    """
    if not env:
        return command
    exports = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in sorted(env.items()))
    return f"export {exports}; {command}"


async def _await_ready(
    service: Service, cwd: Path, runner: Runner, chunks: list[str],
) -> str:
    """Poll `ready_when` until it succeeds, or say why it never did."""
    if not service.ready_when.strip():
        return ""
    deadline = time.monotonic() + max(1.0, service.ready_timeout_s)
    last = ""
    while time.monotonic() < deadline:
        probe = await runner.execute(
            service.ready_when, cwd=cwd, timeout_s=30.0, network=False)
        if probe.started and probe.exit_code == 0:
            return ""
        last = _tail(probe.output, 600)
        await asyncio.sleep(1.0)
    chunks.append(f"[service {service.name}] never became ready\n{last}")
    return (f"the {service.name!r} service did not become ready within "
            f"{service.ready_timeout_s:g}s: `{service.ready_when}` never succeeded")


def service_env(ports: Mapping[str, int]) -> dict[str, str]:
    """Where each service is, as every command in the session reads it.

    The port and the whole address. The port alone was published first, and the
    page was the only thing that learned where the API was -- its build command
    folded the port into a URL. Test code running in Node, which seeds data
    before the page loads, had nothing to fold it into: one project's own
    fixture fell back to `localhost:8300` and its oracle's required a variable
    nobody set, and every browser test died in its fixture, three rounds
    running. The address is the thing a test wants, so it is published whole.

    `127.0.0.1` because that is what a service is told to bind; see
    `Service.command`.
    """
    env: dict[str, str] = {}
    for name, port in ports.items():
        key = name.upper().replace("-", "_")
        env[f"FACTORY_PORT_{key}"] = str(port)
        env[f"FACTORY_URL_{key}"] = f"http://127.0.0.1:{port}"
    return env


@dataclass
class TestSession:
    """A prepared environment, standing, with everything it needs running."""

    ready: bool = True
    problem: str = ""
    log: list[str] = field(default_factory=list)
    #: The port allocated to each service, by name. Published to every command
    #: in the session as `FACTORY_PORT_<NAME>`, with its address beside it as
    #: `FACTORY_URL_<NAME>`.
    ports: dict[str, int] = field(default_factory=dict)

    @property
    def report(self) -> str:
        return "\n\n".join(self.log)


@asynccontextmanager
async def test_session(
    cwd: str | Path, runner: Runner, *,
    services: Sequence[Service] = (), prepare: Sequence[str] = (),
    disposable: "DisposableDatabase | None" = None,
    timeout_s: float = 900.0, hold: bool = False, network: bool = False,
) -> AsyncIterator[TestSession]:
    """Start what has to be running, prepare state once, then yield.

    Everything inside runs in one container where the runner offers a session,
    because a service and the things that use it have to reach each other on
    `127.0.0.1` and one container per command gives them separate network
    namespaces. `execute` already routes into the session container when one is
    open, so the gates and the per-file run both land there without knowing.

    This exists at the level of the whole assessment rather than inside the
    per-file run, and the difference is a bug that got as far as a real survey.
    Services were wired only into the blind suite; the surveyor -- correctly,
    per its prompt -- moved `uvicorn` out of the project's own test gate and
    into `services`, and that gate would then have run against nothing.
    Anything a project declares as standing has to be standing for every command
    in the assessment.

    Teardown is unconditional. A `uvicorn` that survives a raised exception
    holds a port the next round cannot bind, and the failure surfaces as a gate
    that will not start for reasons nothing on the page explains.
    """
    session = TestSession()
    # One kernel-chosen port per service, published under a name both halves of
    # the answer can read. The command that starts a service and the command
    # that talks to it are written separately by an agent that cannot run
    # either, so a literal port in both is two chances to disagree and one
    # chance to agree with somebody else's container.
    session.ports = {s.name: free_port() for s in services if s.name}
    env = service_env(session.ports)
    # The disposable database is named before anything runs, so `prepare` can
    # reach it too. It is removed again below if it could not be created --
    # a variable naming a database that is not there is worse than no variable,
    # because a test would read it and fail for the wrong reason.
    if disposable and disposable.env.strip():
        env[disposable.env.strip()] = disposable.url
    setattr(runner, "session_env", dict(env))

    opener = getattr(runner, "open_session", None)
    discard = getattr(runner, "discard_session", None)
    starter = getattr(runner, "start_service", None)
    stopper = getattr(runner, "stop_services", None)
    # Services and a disposable database need one because the things that talk
    # to each other have to share a network namespace. `hold` is the other
    # reason to want one, and it is not the same reason: an agent given this
    # session needs somewhere to run commands at all.
    #
    # A project whose compose file brings up its own database declares no
    # `services`, so nothing here asked for a container -- and every authoring
    # agent on such a project was handed an environment reporting "could not
    # hold a container open", which is true and was nobody's intent. The shims
    # `unit_environment` puts on an agent's PATH all `docker exec` into that
    # container, so without one the agent has none of the project's tools: a
    # worker asked to run a migration it just wrote cannot, on a project that
    # declares no services, and the run says so nowhere.
    needs_container = hold or bool(services) or bool(disposable and disposable.prepare)

    if needs_container and opener is not None:
        # No egress for the session the tests run in, by default. Setup's
        # session is the one that needs the network -- to install things -- and
        # this is not it.
        #
        # `network=True` is for one case, and it is a real trade rather than an
        # exemption: a session holding an *authoring agent* has to reach the
        # model API, and there is no way to give it that and withhold
        # everything else here. What is inside stays the same -- this unit's
        # worktree, this unit's database -- but an agent with egress is also an
        # agent that could send something out of it. Narrowing that to an
        # allowlist belongs in front of this call, not inside it.
        try:
            await opener(Path(cwd), network=network)
        except TypeError:                       # a runner from before the flag
            await opener(Path(cwd))
        failure = getattr(runner, "_session_error", "")
        if failure:
            session.ready = False
            session.problem = (
                "could not hold one container open, so a service and the commands "
                "that need it would run in different containers:\n" + failure)
    try:
        # Asked first, because everything below it needs somewhere to write.
        #
        # A full disk does not announce itself: it arrives as whatever the
        # first thing to need space happens to raise. Once it was an
        # `asyncpg.DiskFullError` inside a TRUNCATE, forty minutes into a run,
        # and it read as a broken database. The space is measured here instead,
        # in the environment the checks will actually use, and reported in the
        # words the operator needs -- how much is free and where.
        if session.ready:
            free_mb = await _free_space_mb(runner, cwd, timeout_s)
            if free_mb is not None and free_mb < _MIN_FREE_MB:
                session.ready = False
                session.problem = (
                    f"only {free_mb / 1024:.1f} GB free at {cwd} inside the test "
                    f"environment, below the {_MIN_FREE_MB / 1024:.0f} GB this needs. "
                    "Nothing was run: reclaim space and start again."
                )

        # Stood up before the services, because it is a database and they may
        # want it. Its failure costs only the criteria that need it: a project
        # that cannot spare a second database still gets its whole suite, and
        # the oracle is told the capability is absent rather than lied to.
        if disposable and session.ready and disposable.prepare:
            for command in disposable.prepare:
                execution = await runner.execute(
                    command, cwd=Path(cwd), timeout_s=timeout_s, network=False)
                session.log.append(f"$ {command}\n[exit {execution.exit_code}]\n"
                                   f"{_tail(execution.output, 400)}")
                if execution.exit_code != 0 or not execution.started:
                    session.log.append(
                        "[disposable] could not be created, so nothing names it; "
                        "criteria needing it are unverified rather than failed")
                    env.pop(disposable.env.strip(), None)
                    setattr(runner, "session_env", dict(env))
                    break

        for service in services if session.ready else ():
            if starter is None:
                session.ready = False
                session.problem = (
                    f"this runner cannot start the {service.name!r} service, so "
                    "anything needing it would run against nothing")
                break
            # Retried, because an allocated port is free at the moment the
            # kernel says so and the service binds a moment later. Nothing that
            # hands a port to another process closes that window; noticing the
            # service did not come up and allocating again does.
            for attempt in range(1, PORT_ATTEMPTS + 1):
                problem = await starter(service.command, cwd=Path(cwd), name=service.name)
                if problem:
                    session.log.append(f"[service {service.name}] {problem}")
                    session.ready = False
                    session.problem = f"the {service.name!r} service could not be started"
                    break
                unready = await _await_ready(service, Path(cwd), runner, session.log)
                if not unready:
                    break
                if attempt == PORT_ATTEMPTS or service.name not in session.ports:
                    session.ready = False
                    session.problem = unready
                    break
                # Take the port away from whatever has it and try once more.
                session.ports[service.name] = free_port()
                env.update(service_env({service.name: session.ports[service.name]}))
                setattr(runner, "session_env", dict(env))
                session.log.append(
                    f"[service {service.name}] did not come up on the allocated port; "
                    f"retrying on {session.ports[service.name]}")
                if stopper is not None:
                    await stopper()
            if not session.ready:
                break

        for i, command in enumerate(prepare if session.ready else ()):
            execution = await runner.execute(
                command, cwd=Path(cwd), timeout_s=timeout_s, network=False)
            session.log.append(f"$ {command}\n[exit {execution.exit_code}]\n"
                               f"{_tail(execution.output, 1200)}")
            if execution.exit_code != 0 or not execution.started:
                # A failed preparation is not a failing test. Everything after
                # it fails for the same reason, and each would be written up as
                # its own defect -- the shape of finding that costs an afternoon.
                session.ready = False
                session.problem = (
                    disk_full_problem(command, execution.output)
                    or f"preparation step {i + 1} of {len(prepare)} failed, so nothing "
                       f"was run: `{command}`")
                break
        if not session.ready:
            blocked = f"[blocked] {session.problem}"
            # Said first as well as last: the log's head is what gets cut.
            if DISK_FULL.search(session.problem) or session.problem.startswith("Docker ran out"):
                session.log.insert(0, blocked)
            session.log.append(blocked)
        yield session
    finally:
        # Best effort, and never allowed to fail a check: a database left behind
        # is tidied by the next run's `prepare`, which drops before it creates.
        if disposable and disposable.teardown:
            for command in disposable.teardown:
                try:
                    await runner.execute(
                        command, cwd=Path(cwd), timeout_s=120.0, network=False)
                except Exception:  # noqa: BLE001 -- teardown may not raise
                    pass
        if stopper is not None:
            await stopper()
        if discard is not None:
            await discard()
        setattr(runner, "session_env", {})


#: A test's outcome, as JUnit records it. `failure` is an assertion that did not
#: hold; `error` is the test blowing up before it could assert. Both are red and
#: the packet reads them the same way, but they are kept apart here because a
#: report that says which is which costs nothing to preserve.
_JUNIT_BAD = ("failure", "error")


def read_junit(path: str | Path, file_hint: str = "") -> list[CaseOutcome]:
    """Individual test outcomes from a JUnit XML report.

    Parsed rather than read off the screen, and the distinction is the whole
    point. `record_evidence` says what console-scraping costs -- a runner's
    minor release changes a summary line and a gate loses its signal
    silently. This reads a documented interchange format the runner was *asked*
    to emit, which is the opposite kind of thing: versioned, machine-facing, and
    the same shape from pytest, vitest, jest, playwright, go and dotnet.

    A report that is missing, empty or malformed returns nothing at all. That is
    deliberate: no outcomes means the caller falls back to the file's exit code,
    which every project has whether or not it can report. Guessing at a half-read
    report would put a criterion's verdict on a parse error.
    """
    p = Path(path)
    try:
        root = ElementTree.parse(p).getroot()
    except (OSError, ElementTree.ParseError):
        return []
    out: list[CaseOutcome] = []
    for case in root.iter("testcase"):
        name = (case.get("name") or "").strip()
        if not name:
            continue
        status = "passed"
        for child in case:
            tag = ElementTree.QName(child).localname if "}" in child.tag else child.tag
            if tag in _JUNIT_BAD:
                status = "failed" if tag == "failure" else "errored"
                break
            if tag == "skipped":
                status = "skipped"
                break
        out.append(CaseOutcome(file=file_hint, name=name, status=status))
    return out


#: What a runner puts between a suite and the test inside it. Measured: vitest
#: reports `describe > it` where the test is nested and the bare title where it
#: is not, and pytest and playwright report the test's own name -- which is what
#: the oracle declares. The second separator is the other one the JS reporters
#: format titles with; no report in this repository's evidence has used it in a
#: `testcase` name, and it is here because accepting it costs a tuple entry.
_CASE_NESTING = (" > ", " \u203a ")


def _leaf(name: str) -> str:
    """A reported name with the suites it is nested in taken off the front."""
    for sep in _CASE_NESTING:
        name = name.rsplit(sep, 1)[-1]
    return name.strip()


#: How a declared test name is matched to one the runner reported. Exact first;
#: then the reported name starting with the declared one, which is how pytest's
#: `test_x[case-1]` answers a declaration of `test_x`. Both are tried again
#: against the reported name's leaf, because vitest answers a declaration of
#: `adds a tag` with `AC-10 CardItem tag chips > adds a tag` -- without the
#: leaf match, every criterion behind such a prefix is reported `unknown`, its
#: tests having run and passed. A fully-qualified match still wins over a leaf
#: one. Nothing looser: a name that matches two tests is not evidence about
#: either, and guessing here would put a criterion's verdict on a substring.
def match_case(declared: str, reported: Sequence[CaseOutcome]) -> list[CaseOutcome]:
    want = (declared or "").strip()
    if not want:
        return []
    for name_of in (lambda c: c.name, lambda c: _leaf(c.name)):
        exact = [c for c in reported if name_of(c) == want]
        if exact:
            return exact
        parametrised = [c for c in reported if name_of(c).startswith(want + "[")]
        if parametrised:
            return parametrised
    return []


def sweep_reports(cwd: str | Path) -> list[str]:
    """Remove any per-test report left in the checkout, wherever it landed.

    Each report is unlinked where it was asked to be written. A rule that `cd`s
    and then names the report relatively writes it somewhere else, and there it
    stays: 36 of them were committed to one feature branch before the rule was
    corrected. Untracked files only, so nothing on the branch is touched, and
    through git so an ignored `node_modules` is not walked.
    """
    root = Path(cwd)
    try:
        listed = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=root, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    if listed.returncode != 0:
        return []
    removed: list[str] = []
    for rel in filter(None, listed.stdout.split("\0")):
        name = rel.rsplit("/", 1)[-1]
        if name.startswith(REPORT_PREFIX):
            (root / rel).unlink(missing_ok=True)
            removed.append(rel)
    return removed


def workdir_of(runner: Runner) -> str:
    """Where this runner's commands stand, as they see it.

    A container runs the checkout at its own workdir; a local runner is already
    standing in it. The difference matters exactly once -- naming a directory to
    a test that will be read on the other side of a mount.
    """
    return str(getattr(runner, "workdir", "") or ".")


async def run_per_file(
    rules: Sequence[TestFileCommand], paths: Sequence[str], cwd: str | Path,
    runner: Runner | None = None, *, timeout_s: float = 900.0, name: str = "per-file",
    prepare: Sequence[str] = (), timeouts: Mapping[str, float] | None = None,
    after_file: Callable[[str], None] | None = None, repeat: bool = False,
) -> tuple[GateResult, dict[str, int]]:
    """Run one command per test file, and let each exit code speak for its file.

    With `repeat`, a file that passes is run once more straight after, with
    nothing reset between -- the one check that tells whether a test cleans up
    after itself without knowing how it is meant to. See `run_again`.

    This is the whole attribution mechanism, and it is deliberately the dullest
    thing that could work. A rule carries a `{path}` -- `pytest {path}`,
    `npx vitest run {path}` -- and comes from the surveyor, which is the one
    role that may know what this project's runner is called. Nothing here knows.
    Nothing here parses. A file whose command exits zero passed; a file whose
    command did not, failed; and which criteria that file covers is already
    recorded by the oracle that wrote it.

    Whatever has to be running while these run is already running: the caller
    holds a `test_session` open around this. See there for why that is not this
    function's business.

    What this cannot see is a file that ran nothing -- a test collected away or
    skipped exits zero like a test that passed. Whether this project's runner
    says so is measured once at gate 0 rather than assumed here; see
    `probe_empty_selection`.
    """
    runner = runner or LocalRunner()
    started_at = time.monotonic()
    result = GateResult(name=name, command="; ".join(r.command for r in rules))

    exits: dict[str, int] = {}
    cases: list[CaseOutcome] = []
    chunks: list[str] = []
    unstarted: list[str] = []
    unclaimed: list[str] = []
    unprepared: list[str] = []

    for path in paths:
        # Back to a known state before each file, where the caller asked for it.
        #
        # These files share one database. `test_session` prepares it once, the
        # project's own gates run against it, and then each blind file runs in
        # turn -- so by the time the last one starts, every earlier file's writes
        # are still there. One file sets a row's columns to NULL to make an
        # "unrecorded" fixture, nothing puts them back, and a later file that
        # asserts no row is NULL fails for a reason that is not about the code.
        #
        # Before the first file too, not only between them: the project's own
        # suite runs before this one against the same database.
        if prepare:
            failed_prepare = ""
            for command in prepare:
                execution = await runner.execute(
                    command, cwd=Path(cwd), timeout_s=timeout_s, network=False)
                chunks.append(f"$ {command}\n[exit {execution.exit_code}]\n"
                              f"{_tail(execution.output, 400)}")
                if execution.exit_code != 0 or not execution.started:
                    failed_prepare = command
                    break
            if failed_prepare:
                # Not run, and not failed either. A file whose database could not
                # be put back settles nothing, and saying it failed would blame
                # the feature for a migration that would not apply.
                unprepared.append(path)
                chunks.append(
                    f"[skipped] {path}: could not reset the environment first "
                    f"(`{failed_prepare}`), so nothing it says would be about the code")
                continue

        rule = rule_for(rules, path)
        if rule is None or not (rule.command or "").strip():
            # Guessing a runner for an unmatched file is how a usage error
            # comes to be reported as a failing criterion.
            unclaimed.append(path)
            chunks.append(f"[unmatched] no rule claims {path}")
            continue

        # The reporting command runs the tests *and* writes a report, so it
        # replaces the plain one rather than running beside it -- two runs of the
        # same file is twice the cost and two chances to disagree.
        #
        # The report is written inside the checkout because that is the only
        # directory both sides of the mount can see, and removed straight after
        # reading: anything left there shows up in `git status` and is collected
        # as somebody's work.
        #
        # Two names for the one file, and they are not the same string. The
        # command is told the path the runner will see -- `/workspace/.factory-
        # report-ab12cd34.xml` inside a container -- and the read happens at the
        # host path the mount puts it at. Naming the host path to a containerised
        # runner writes the report to a directory that does not exist on that
        # side, and then `read_junit` finds nothing at the path it asks for.
        #
        # Silently. A report that is missing returns no outcomes, which is by
        # design the signal to fall back to the file's exit code -- so a mount
        # boundary degraded every project with a container environment to
        # file-granularity attribution and nothing anywhere said so. One run
        # reported eighteen of twenty-five criteria as failing off four files,
        # fifteen of them from a single one.
        report: Path | None = None
        template = (rule.command or "").strip()
        if (rule.report or "").strip():
            # Absolute on whichever side it is named, because a rule may `cd`
            # before it runs -- `cd backend && pytest --junitxml=... ../{path}`
            # is what these look like -- and a relative report would land
            # wherever that cd left it. `LocalRunner` has no workdir and is
            # already standing in the checkout, so for it the host path is both
            # the name and the place.
            stem = f"{REPORT_PREFIX}{uuid4().hex[:8]}.xml"
            report = Path(cwd) / stem
            mounted = workdir_of(runner)
            named = report if mounted in ("", ".") else PurePosixPath(mounted) / stem
            template = rule.report.strip().replace("{report}", str(named))
        command = template.replace("{path}", path)
        try:
            each_started = time.monotonic()
            # A file may be given its own budget. The caller is the only thing
            # that knows what this file has cost before; `timeout_s` stays the
            # answer for one it has never seen finish.
            execution = await runner.execute(
                command, cwd=Path(cwd),
                timeout_s=float((timeouts or {}).get(path, timeout_s)), network=False)
            result.file_seconds[path] = round(time.monotonic() - each_started, 3)
            # Between the files, because that is the only moment anything the
            # file left behind can be told apart from what the next one leaves
            # -- and for a runner that clears its output directory whenever it
            # starts, the only moment it exists at all. Playwright does: a
            # sweep after the last file found that file's recordings and
            # nothing from the ones before it.
            if after_file is not None:
                try:
                    after_file(path)
                except Exception:  # noqa: BLE001 -- a sweep never fails a file
                    pass
            exits[path] = execution.exit_code
            if execution.exit_code != 0:
                result.file_output[path] = _tail(execution.output, 2000)
            if execution.timed_out:
                # Kept apart from the exit code on purpose. A killed process
                # exits non-zero and so does a failing test, and the caller
                # cannot tell them apart afterwards -- but they mean opposite
                # things. `gate_outcome` already draws this line for a whole
                # gate ("a gate that never finished did not report on
                # anything"); a per-file run has to draw it per file.
                result.timed_out_files.append(path)
            if not execution.started:
                unstarted.append(path)
            chunks.append(f"$ {command}\n[exit {execution.exit_code}]\n"
                          f"{_tail(execution.output, 1200)}")
            if report is not None:
                cases.extend(read_junit(report, file_hint=path))
            if repeat and execution.started and execution.exit_code == 0:
                await run_again(
                    path, (rule.command or "").strip().replace("{path}", path),
                    cwd, runner, prepare, float((timeouts or {}).get(path, timeout_s)),
                    result, chunks)
        finally:
            if report is not None:
                report.unlink(missing_ok=True)

    sweep_reports(cwd)
    output = "\n\n".join(chunks)
    result.duration_s = round(time.monotonic() - started_at, 3)
    result.output_tail = _tail(output)
    record_evidence(result, output)
    result.witnessed = [p for p in paths if p not in unclaimed]
    result.named_failing = [p for p, code in exits.items() if code != 0]
    result.cases = cases
    # Reported in the same bucket as a file that would not load, because the
    # consequence is the same: it settles nothing, and the reason is the harness
    # rather than the code.
    result.named_unloadable = list(unprepared)
    # A file no rule claims never ran, and the gate must not read as though the
    # suite was covered. `started=False` is how a gate says "this settles
    # nothing", and every criterion under it stays unverified.
    result.started = (
        bool(paths) and not unclaimed and len(unstarted) < max(1, len(exits)))
    result.exit_code = 0 if (result.started and not result.named_failing) else 1
    result.passed = result.started and not result.named_failing
    result.summary_known = result.started
    result.tests_total = len(exits)
    result.tests_failed = len(result.named_failing)
    result.tests_passed = result.tests_total - result.tests_failed
    result.zero_ran = not paths
    return result, exits


async def run_again(
    path: str, command: str, cwd: str | Path, runner: Runner, prepare: Sequence[str],
    timeout_s: float, result: GateResult, chunks: list[str],
) -> None:
    """Run a file that just passed once more, and say what that shows.

    Passed, then failed with nothing reset between: the file leaves something
    behind -- rows, lists, a user -- that breaks its own next run, and whatever
    runs after it. One suite added two lists per test to the demo board every
    browser test opens; the project's own test that counts that board's lists
    failed for it, and the failure read as the feature's.

    It is measured by outcome because the mechanism is the project's business:
    a rollback, a teardown, a truncate, a throwaway workspace. Nothing here has
    to know which, only whether it happened.

    A flaky test also fails a second time sometimes. Where the project has a
    reset, the file runs a third time after it: passing then means leftover
    state (`leak_confirmed`), failing means something else moves it
    (`unstable`) and nobody is blamed. With no reset it stays in `leaked`, and
    the finding says it could not be confirmed.
    """
    again = await runner.execute(command, cwd=Path(cwd), timeout_s=timeout_s, network=False)
    if not again.started or again.exit_code == 0:
        return
    result.leaked.append(path)
    result.leak_output[path] = _tail(again.output, 2000)
    chunks.append(f"[run again] $ {command}\n[exit {again.exit_code}]\n"
                  f"{_tail(again.output, 1200)}")
    if not prepare:
        return
    for step in prepare:
        reset = await runner.execute(step, cwd=Path(cwd), timeout_s=timeout_s, network=False)
        if reset.exit_code != 0 or not reset.started:
            return
    third = await runner.execute(command, cwd=Path(cwd), timeout_s=timeout_s, network=False)
    if third.started and third.exit_code == 0:
        result.leak_confirmed.append(path)
    else:
        result.unstable.append(path)
    chunks.append(f"[after the reset] $ {command}\n[exit {third.exit_code}]")


async def probe_empty_selection(
    template: str, cwd: str | Path, runner: Runner | None = None,
    *, timeout_s: float = 300.0,
) -> tuple[bool | None, str]:
    """Does this project's runner fail when pointed at nothing?

    The one question an exit code cannot answer on its own is whether anything
    ran, and it is the question the whole verification story rests on: a suite
    that collects nothing exits zero and would read as every criterion verified.

    Most runners do exit non-zero on an empty selection -- and "most runners do"
    is exactly the kind of belief that put a table of framework regexes in this
    module. So it is not believed. It is measured, once, against this project's
    own command, and the answer is stored with the project and shown to the
    human who approves it. A project whose runner exits zero over nothing is not
    thereby broken; it has a blind spot, and the packet is able to say so
    instead of quietly assuming otherwise.

    Returns `None` when the probe itself could not run, which is not the same as
    a runner that tolerates emptiness and must not be recorded as one.
    """
    runner = runner or LocalRunner()
    # A path that cannot match anything and cannot be created by accident. The
    # runner is asked about a file that is not there, which is the closest
    # runner-independent stand-in for "a selection with no tests in it".
    absent = "__factory_no_such_test__/__none__"
    command = template.replace("{path}", absent)
    try:
        execution = await runner.execute(
            command, cwd=Path(cwd), timeout_s=timeout_s, network=False)
    except Exception as exc:                                  # noqa: BLE001
        return None, f"the probe could not be run: {exc}"
    if not execution.started:
        return None, "the probe's command could not start"
    return execution.exit_code != 0, (
        f"$ {command}\n[exit {execution.exit_code}]\n{_tail(execution.output, 600)}")


async def probe_testing(
    surface: Any,
    placements: Sequence[BlindPlacement],
    proved_dirs: Sequence[str],
    rules: Sequence[TestFileCommand],
    cwd: str | Path,
    runner: Runner | None = None,
    *,
    timeout_s: float = 900.0,
    green: Sequence[GateConfig] = (),
) -> list[TierResult]:
    """Prove `usable`, or say it was never proved.

    `usable` is what decides whether the oracle is told to write against this
    project's own setup or to build its own, and without this it is the one
    thing on gate 0 that nothing measures. Everything around it is measured: the
    gates ran, the placements were probed with canaries, the package list came
    off the running environment. Unproved, it is a model's judgement, taken on
    trust, and a generous one costs the run every criterion at that level -- reported as a
    feature that failed, because a test that cannot reach its fixtures fails the
    same way a wrong implementation does.

    So the reading supplies a tiny test that uses the fixtures it just named,
    and the harness runs it in a directory already proved to collect and
    configure files. A tier that cannot pass a two-line test written against its
    own setup is not usable, whatever it says.

    Only `usable` tiers are probed. `inline_only` and `absent` are claims that
    something is NOT available, and the cost of being wrong about them is a
    criterion reported unverified that could have been checked -- which is
    conservative, visible, and nothing like the other direction.

    And whether a test written the tier's way cleans up after itself, measured
    the only way that works whatever the mechanism is: by outcomes. The canary
    runs a second time straight after the first (`repeatable`), and at a level
    whose tests share state, the project's own checks for that level run again
    after it (`leaves_clean`). `green` is the checks that passed just before, so
    a check that was already red cannot be read as the canary's doing. Nothing
    here knows what a rollback or a teardown is, and nothing needs to.
    """
    runner = runner or LocalRunner()
    proved = set(proved_dirs)
    green_by_name = {g.name: g for g in green}
    out: list[TierResult] = []
    for tier in list(getattr(surface, "tiers", ()) or ()):
        result = TierResult(tier=tier.tier, claimed=tier.verdict)
        checks = [green_by_name[n] for n in (tier.run_by or []) if n in green_by_name]
        if tier.verdict != "usable":
            result.proved = True      # nothing to prove: it claims no setup
            result.note = f"`{tier.verdict}` claims no reusable setup, so there is nothing to prove."
            # Whether its tests leave the world as they found it does not depend
            # on reusable setup, so it is measured here too: measured only where
            # there is some, a suite that fails its own second run would never
            # be shown to be leaking. Its own checks, green a moment ago, run
            # once more with nothing reset.
            if tier.verdict != "absent" and tier.tier in ("integration", "user") and checks:
                after = (await run_gates(checks, cwd, runner)).results
                result.leaves_clean = all(r.passed for r in after)
                if not result.leaves_clean:
                    result.note += (" Its own tests, run again straight after they passed, fail: "
                                    "they leave something behind for the next run.")
                    result.evidence = "\n\n".join(
                        f"[second run] $ {r.command}\n{_tail(r.output_tail or '', 1200)}"
                        for r in after if not r.passed)
            out.append(result)
            continue
        if not (tier.canary or "").strip() or not (tier.canary_filename or "").strip():
            result.note = (
                "this tier says a new test can be written against setup that already exists, and "
                "supplied no test that does so. The claim is unproved, and it is the claim that "
                "decides what an agent which has never seen this repository is told it may use."
            )
            out.append(result)
            continue

        suffix = PurePosixPath(tier.canary_filename).suffix
        home = next(
            (pl.directory for pl in placements
             if (pl.directory or "").strip("/") in proved
             and PurePosixPath(pl.filename or "").suffix == suffix),
            "",
        )
        if not home:
            result.note = (
                f"no proved directory takes a `{suffix or 'file'}` here, so this tier's claim "
                "could not be tested where a blind test would actually live."
            )
            out.append(result)
            continue

        rel = f"{home.strip('/')}/{PurePosixPath(tier.canary_filename).name}"
        template = command_for(rules, rel)
        if not template:
            result.note = f"no test-file command claims {rel}, so the canary could not be run."
            out.append(result)
            continue

        result.path = rel
        result.command = template.replace("{path}", rel)
        target = Path(cwd) / rel
        created = not target.parent.exists()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(tier.canary)
            execution = await runner.execute(
                result.command, cwd=Path(cwd), timeout_s=timeout_s, network=False)
            if execution.started and execution.exit_code == 0:
                again = await runner.execute(
                    result.command, cwd=Path(cwd), timeout_s=timeout_s, network=False)
                result.repeatable = again.started and again.exit_code == 0
                if not result.repeatable:
                    result.evidence = (f"[second run] $ {result.command}\n"
                                       f"[exit {again.exit_code}]\n{_tail(again.output, 1200)}")
        except Exception as exc:                                  # noqa: BLE001
            result.note = f"the canary could not be run: {exc}"
            out.append(result)
            continue
        finally:
            target.unlink(missing_ok=True)
            if created:
                try:
                    target.parent.rmdir()
                except OSError:
                    pass

        result.ran = execution.started
        result.passed = execution.started and execution.exit_code == 0
        result.proved = bool(result.passed)
        second = result.evidence
        result.evidence = (f"$ {result.command}\n[exit {execution.exit_code}]\n"
                           f"{_tail(execution.output, 1200)}"
                           + (f"\n\n{second}" if second else ""))
        # The project's own tests at this level, again, now that the canary has
        # run twice. Only at a level whose tests share state, and only checks
        # that were green a moment ago.
        if result.proved and tier.tier in ("integration", "user") and checks:
            after = (await run_gates(checks, cwd, runner)).results
            result.leaves_clean = all(r.passed for r in after)
            if not result.leaves_clean:
                result.evidence += "\n\n" + "\n\n".join(
                    f"[after the canary] $ {r.command}\n{_tail(r.output_tail or '', 1200)}"
                    for r in after if not r.passed)
        result.note = (
            "a test written against this tier's own named setup runs and passes."
            if result.proved else
            "this tier says a new test can be written against setup that already exists, and a "
            "test doing exactly that does not pass. An agent told to use this setup would write "
            "a suite that cannot run, and every criterion at this level would come back looking "
            "like a broken feature."
        )
        if result.repeatable is False:
            result.note += (" It does not pass a second time straight after the first, so a test "
                            "written this way leaves something behind for its own next run.")
        if result.leaves_clean is False:
            result.note += (" And after it ran, this project's own tests at this level failed "
                            "where they had just passed: a test written this way breaks the "
                            "tests already here.")
        out.append(result)
    return out


async def probe_placement(
    placement: BlindPlacement,
    rules: Sequence[TestFileCommand],
    cwd: str | Path,
    runner: Runner | None = None,
    *,
    timeout_s: float = 900.0,
) -> PlacementResult:
    """Prove that a blind test written into this directory would actually work.

    Two facts, in this order, each of which costs a whole run when assumed:

    1. A passing test placed here is reported as passing. Where a file sits
       decides which configuration reaches it, and a file outside the tree that
       owns the runner's config is run without it. Measured, not reasoned about:
       `cd backend && pytest ../tests/oracle/x.py` takes its rootdir from the
       argument, finds no ini above it, and runs every `async def` test without
       `asyncio_mode` -- so a suite whose tests assert `1 == 1` fails on every
       round of every run, and the packet reads as a broken feature.

    2. A failing test placed here is reported as failing. This is the one that
       matters most and reads as pedantry until you see it: a runner that does
       not collect the file at all exits zero over nothing, which is
       indistinguishable from a suite that passed. Every criterion would come
       back verified by tests that never ran.

    Whether the project's own checks also look in here is deliberately not
    asked. They should: a blind test is code on the branch like any other, and
    CI will lint and run it whatever this tool prefers. A failure there is
    sorted by the file it is in, not hidden by the folder it sits in.

    Nothing in here knows what a test framework is. The canary's source is
    written by the survey -- the one role that may know -- and this only writes
    bytes, runs the project's own command, and reads exit codes.
    """
    runner = runner or LocalRunner()
    result = PlacementResult(kind=placement.kind, directory=placement.directory)
    root = Path(cwd)
    rel = f"{(placement.directory or '').strip('/')}/{(placement.filename or '').strip('/')}"
    rel = rel.strip("/")
    if not placement.directory or not placement.filename:
        result.note = "the placement names no directory or no filename, so nothing could be written there."
        return result
    try:
        target = safe_join(root, rel)
    except PathEscape as exc:
        result.note = f"the placement points outside the repository: {exc}"
        return result

    result.path = rel
    rule = rule_for(rules, rel)
    template = (rule.command or "").strip() if rule else ""
    if not template:
        # The same refusal `run_per_file` makes, made early enough to fix. A
        # placement no rule claims is a directory the harness would write blind
        # tests into and then have no way to run.
        result.note = (
            f"no test-file command claims {rel}. A blind test written here could not be run "
            "at all, and the criteria it covered would come back unverified."
        )
        return result
    command = template.replace("{path}", rel)
    result.command = command

    log: list[str] = []
    created_dir = not target.parent.exists()
    #: Where the subdirectory probe writes. A blind suite that accumulates needs
    #: one folder per feature, and whether that works is a property of the
    #: runner's layout rules rather than something this tool may decide.
    deeper = target.parent / "_factory_probe" / target.name
    deeper_rel = f"{PurePosixPath(rel).parent}/_factory_probe/{PurePosixPath(rel).name}"

    async def _run_canary(
        contents: str, label: str, at: Path | None = None, at_rel: str = "",
        invocation: str = "",
    ) -> Execution | None:
        # The relative path is carried, never derived from the absolute one:
        # `safe_join` resolves symlinks and the sandbox root may not be resolved,
        # so `/var` and `/private/var` disagree and `relative_to` raises.
        where = at or target
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_text(contents)
        cmd = (invocation or template).replace("{path}", at_rel or rel)
        try:
            execution = await runner.execute(
                cmd, cwd=root, timeout_s=timeout_s, network=False)
        except Exception as exc:                                  # noqa: BLE001
            log.append(f"[{label}] $ {cmd}\nthe command could not be run: {exc}")
            return None
        log.append(f"[{label}] $ {cmd}\n[exit {execution.exit_code}]\n"
                   f"{_tail(execution.output, 1200)}")
        return execution

    try:
        passing = await _run_canary(placement.canary_passes, "passing canary")
        if passing is None or not passing.started:
            result.note = (
                "the per-file command could not be started against a canary in this "
                "directory, so nothing about it could be measured."
            )
            return result
        result.passing_ran = passing.exit_code == 0

        failing = await _run_canary(placement.canary_fails, "failing canary")
        if failing is None or not failing.started:
            result.note = "the failing canary could not be run, so this placement is unproven."
            return result
        result.failing_seen = failing.exit_code != 0

        # One directory deeper, only once the flat answer is good -- an
        # accumulating blind suite wants a folder per feature so two features
        # cannot land the same filename on each other, and whether a runner
        # treats a nested file the same way is measured here rather than
        # believed.
        if result.passing_ran and result.failing_seen:
            deep_pass = await _run_canary(
                placement.canary_passes, "passing canary, one deeper", deeper, deeper_rel)
            deep_fail = await _run_canary(
                placement.canary_fails, "failing canary, one deeper", deeper, deeper_rel)
            if deep_pass is not None and deep_fail is not None and deep_pass.started:
                result.subdirs_ok = deep_pass.exit_code == 0 and deep_fail.exit_code != 0

        # 3. What this runner calls a test, in the report the packet reads.
        #
        # The packet joins the oracle's declared case names to the names in the
        # runner's own report. A runner that qualifies a name with the suite
        # around it -- `AC-13 optimistic tag display > shows a tag`, where the
        # oracle declared `shows a tag` -- joins nothing, and that is not a
        # quiet loss of resolution: every criterion in every file comes back
        # `unknown`, which reads as separate mysteries about the code rather
        # than one fact about the report -- a whole run can be spent that way
        # with every one of its blind tests passing.
        #
        # "Most runners report the test's own name" is true, and is exactly the
        # belief `empty_run_detected` exists to stop anyone acting on. So it is
        # measured here against this project's own reporting command, and
        # recorded for the human at repo ready rather than worked around.
        #
        # A third run of the same file, and the only place in this harness that
        # runs one twice on purpose. `run_per_file` swaps the reporting command
        # in for the plain one because two runs cost twice and can disagree --
        # here the disagreement IS the measurement, and the price is one file,
        # once, on a day a human is already waiting.
        declared = (placement.canary_case or "").strip()
        reporting = (rule.report or "").strip() if rule else ""
        if result.passing_ran and declared and reporting:
            # Named on whichever side the runner sees and read on the host
            # side, for the reason `run_per_file` spells out: a containerised
            # runner told the host path writes its report into a directory that
            # does not exist there, and `read_junit` then finds nothing --
            # silently, because a missing report is by design the signal to
            # fall back to the file's exit code.
            stem = f"{REPORT_PREFIX}{uuid4().hex[:8]}.xml"
            written = root / stem
            mounted = workdir_of(runner)
            named = written if mounted in ("", ".") else PurePosixPath(mounted) / stem
            try:
                await _run_canary(
                    placement.canary_passes, "passing canary, with a report",
                    invocation=reporting.replace("{report}", str(named)))
                reported = read_junit(written, file_hint=rel)
            finally:
                written.unlink(missing_ok=True)
                sweep_reports(root)
            # Every name the report carried, not the first: a runner that
            # reports the canary twice under two names is telling us something
            # a single string would hide.
            result.reported_case = ", ".join(c.name for c in reported)
            if reported:
                result.case_named_as_declared = bool(match_case(declared, reported))
            log.append(
                f"[naming] declared {declared!r}; reported "
                f"{result.reported_case or 'nothing this harness could read'}")
    finally:
        target.unlink(missing_ok=True)
        deeper.unlink(missing_ok=True)
        if deeper.parent.exists() and not any(deeper.parent.iterdir()):
            deeper.parent.rmdir()
        if created_dir:
            try:
                target.parent.rmdir()
            except OSError:
                pass

    result.usable = result.passing_ran is True and result.failing_seen is True
    if result.usable:
        result.note = "a blind test written here is collected, configured, and reported honestly."
    else:
        why = []
        if result.passing_ran is not True:
            why.append("a passing test placed here was not reported as passing, so this "
                       "project's configuration does not reach it")
        if result.failing_seen is not True:
            why.append("a FAILING test placed here was not reported as failing -- the runner "
                       "exits zero over a file it never collected, which would make every "
                       "criterion read as verified by tests that never ran")
        result.note = "; ".join(why) + "."

    # Said beside the verdict rather than folded into it. A placement that
    # cannot report a failure is unusable; one whose report the packet cannot
    # join is usable and lossy, and the difference is worth a human's minute at
    # repo ready -- per-test attribution is additive, so losing it costs
    # resolution and never correctness.
    if result.case_named_as_declared is False:
        result.note += (
            f" Its report calls that test {result.reported_case!r}, where a blind test here "
            f"would be declared as {(placement.canary_case or '').strip()!r} -- so nothing the "
            "oracle tags would join to anything the runner reports, and every criterion covered "
            "from this directory would reach the packet as `unknown` however well its tests ran. "
            "Configure this runner's reporter to emit the test's own name."
        )
    elif (result.passing_ran and rule and (rule.report or "").strip()
            and (placement.canary_case or "").strip()
            and result.case_named_as_declared is None):
        result.note += (
            " Its reporting command wrote no report this harness could read, so it proves "
            "nothing per test: every criterion in a file here shares that file's one exit code. "
            "A `{report}` written as `../{report}` or `$PWD/{report}` is the usual cause -- it "
            "names a directory that does not exist, and a missing report is indistinguishable "
            "from a runner that cannot emit one."
        )
    elif result.passing_ran and rule and not (rule.report or "").strip():
        # Said, not skipped. The naming measurement above needs a reporting
        # command. Left silent, a rule without one is first heard of as a
        # packet whose criteria all come back `unknown` from tests that ran.
        result.note += (
            " Its test-file rule has no reporting command, so a test here can only be judged "
            "by its file's exit code: every criterion covered from this directory shares one "
            "verdict per file. Resurvey looks for one: it asks this runner how it writes JUnit "
            "XML, and proves the answer on this canary before offering it."
        )
    result.evidence = _tail("\n\n".join(log))
    return result


# --------------------------------------------------------------------------
# did this gate produce a result, or merely a failure?
#
# The distinction decides what gate 0 is for. A gate that runs and reports 129
# lint errors is a working gate on a repository that has never been linted --
# useful from the first feature, because a failing gate is compared against the
# commit the work branched from. A gate that cannot run at all is red forever
# and will never tell anyone anything, and approving a project around one is
# approving a permanent blind spot.
#
# The classification is heuristic and says so. `started` only catches the shell
# failing to launch; a missing binary comes back through the shell as exit 127,
# indistinguishable at that level from a linter with an opinion. So this returns
# `unknown` where it cannot tell, and the caller must not upgrade that to either
# answer -- blocking on a guess is as wrong here as waving one through.
# --------------------------------------------------------------------------

GateOutcome = Literal["ran", "could_not_run", "unknown"]

# Shell and runtime conventions for "there was nothing to execute". These are
# the cases worth blocking on, because every one of them means the same thing
# whatever the language: the tool this gate names is not here.
_ABSENT = (
    "command not found",
    "no such file or directory",
    "is not recognized as an internal or external command",
    "modulenotfounderror",
    "no module named",
    "cannot find module",
    "executable file not found",
    "permission denied",
)


def _bound_description(gate: GateConfig) -> str:
    """The bounds this gate is checked against, for an output note. Empty when
    the metric is informational and the exit code is still the verdict."""
    parts = []
    if gate.threshold is not None:
        parts.append(f"threshold {gate.threshold}")
    if gate.threshold_max is not None:
        parts.append(f"ceiling {gate.threshold_max}")
    return " and ".join(parts)


def is_green(result: GateResult) -> bool:
    """Whether this check passed on its own terms.

    Deliberately not `result.passed`, and deliberately not `gate_outcome(r) ==
    "ran"`. Those answer other questions: the first is set to True for an
    optional gate that never started, and the second asks whether the gate
    produced information -- an optional gate that ran and failed produced plenty.

    This asks the one thing gate 0 turns on: did the command run and report
    success. `optional` buys a gate nothing here, because a check kept on the
    list and exempted from the rule is the red row that trains people to skim
    red, which is the whole reason the rule exists. Fix it, ratchet it with
    `threshold_max`, or decline it.
    """
    return bool(result.started and result.passed and not result.skipped)


def gate_outcome(result: GateResult) -> GateOutcome:
    """Whether this gate produced information, could not, or cannot be told."""
    if result.passed and not result.skipped:
        return "ran"
    if not result.started:
        return "could_not_run"
    if result.timed_out:
        # A gate that never finished did not report on anything. It may be a
        # slow suite rather than a broken one, which is why the caller is told
        # rather than left to infer it from a red row.
        return "could_not_run"
    # 127 is "command not found" and 126 is "found, not executable". Both are
    # shell conventions and both mean the gate never got as far as the code.
    if result.exit_code in (126, 127):
        return "could_not_run"
    tail = (result.output_tail or "").lower()
    if any(marker in tail for marker in _ABSENT):
        return "could_not_run"
    if result.exit_code != 0 and not tail.strip():
        # Non-zero and silent. Something refused to speak, and a gate that
        # reports nothing has not reported.
        return "unknown"
    if result.exit_code != 0:
        return "ran"
    return "ran"


def unrunnable(report: GateReport) -> list[GateResult]:
    """Gates that cannot produce information in this environment, ever.

    Optional gates are excluded: a human already said they know.
    """
    return [r for r in report.results
            if not r.skipped and gate_outcome(r) == "could_not_run"]
