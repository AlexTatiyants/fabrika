"""How a worker actually builds.

Two implementations behind one protocol. `DirectExecutor` proves the pipeline's
shape with no tooling at all; `CommandExecutor` hands the unit to a real coding
harness in an isolated worktree and then buys back the disclosure the harness
will not produce on its own.
"""

from __future__ import annotations

import re
import asyncio
import contextvars
import functools
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, Field

from . import procs
from . import guides as guide_lib, isolation, sessioncreds
from .isolation import IsolationError, require_contained
from .config import Config, fallback_session_model, harness_model, session_fallback, session_model
from .llm import LLM, LLMError
from .schemas import (Decision, FileWrite, Guide, SelfDisclosure, SendBackRecord, Spec, WorkerOutput,
                      WorkUnit)
from .sendback import send_back_brief
from .workspace import PathEscape, Worktree, is_runner_output, safe_join, spec_text

#: This repository's root, for a `command` that names something inside it. The
#: harness runs in the unit's worktree, so relative paths there mean the wrong
#: tree; `{factory_root}` is how a command says "the interpreter I shipped with".
FACTORY_ROOT = Path(__file__).resolve().parent.parent


class ExecutorError(RuntimeError):
    pass


def unprepared_container(route, prepared) -> str:
    """Why a session that runs in the unit's container has none to run in.

    Said in the environment's terms, not the guard's. When a harness layer
    fails to build, the guard's own answer is "would have run on this machine"
    -- true, and no help: the reason is something like a missing `venv` module
    in the project's image, recorded a few lines up. The first paragraph of the
    environment's problem is the summary; its log stays in the record.
    """
    label = route.harness_label or route.name
    problem = (getattr(prepared, "problem", "") or "").strip()
    if problem:
        summary = problem.split("\n\n", 1)[0].strip()
        return (f"{label} runs inside this project's container, and the container could "
                f"not be prepared: {summary} (The full log is in this unit's environment "
                "record.)")
    return (f"{label} runs inside this project's container, and this project has no "
            "container environment, so there is nowhere sealed for it to run.")


def unit_brief(unit: WorkUnit, spec: Spec, digest: str, harness: bool = False) -> str:
    """Everything one worker is allowed to know.

    `harness` when a coding harness runs it: that harness loads the
    repository's AGENTS.md and skills itself, so the brief names where the
    other rules are kept rather than pasting any.
    """
    lines = [
        spec_text(spec),
        "",
        "---",
        "",
        f"# Your work unit: {unit.id} -- {unit.title}",
        "",
        "## Objective",
        unit.objective,
        "",
        f"## Acceptance criteria you own: {', '.join(unit.criterion_ids) or '(none)'}",
        "",
        "## Files you own (do not write outside this list)",
    ]
    lines += [f"- {f}" for f in unit.files_expected] or ["- (decide, but stay narrow)"]
    lines += ["", "## The contract you must expose to other units (`provides`)"]
    lines += [f"- {p}" for p in unit.provides] or ["- (nothing)"]
    lines += [
        "",
        "## The contract you must code against (`requires`)",
        "These units are being built in parallel, in other worktrees, right now.",
        "You cannot see their code. These strings are the whole truth about them.",
    ]
    lines += [f"- {r}" for r in unit.requires] or ["- (nothing)"]
    if unit.notes:
        lines += ["", "## Notes", unit.notes]
    # Built by code from the repository's guides and what the scout observed
    # -- the rules this unit is written to, in their order of precedence. A
    # harness reads the guides from the checkout itself and is told only what
    # its loader would miss; nothing harness-specific goes into the repository.
    guidance = unit.harness_guidance if harness else unit.guidance
    if guidance:
        lines += ["", "---", "", guidance]
    if digest:
        lines += ["", "---", "", "# Repository", "", digest]
    else:
        # A worker with a checkout does not need a copy of it, and a clipped copy
        # is worse than none: the brief and the files disagree, and nothing says
        # which is authoritative. Given both, a model rebuilds a long file from
        # the clipped version, or loops trying to reconcile them.
        lines += [
            "", "---", "", "# The repository",
            "",
            "You are working inside a checkout of it. Open the files you own and read "
            "them before you change anything. What is on disk is the truth; nothing in "
            "this brief restates it, and no summary of it exists that you should trust "
            "over the file itself.",
        ]
    return "\n".join(lines)


@runtime_checkable
class Executor(Protocol):
    async def run(
        self, unit: WorkUnit, spec: Spec, digest: str, system: str, workdir: Path,
        role: str = "worker", on_prompt: Callable[[str, str], None] | None = None,
        environment: Callable[[Path], Any] | None = None,
        on_environment: Callable[[Any], None] | None = None,
        send_back: Any = None,
        on_guides: Callable[[dict[str, Any]], None] | None = None,
    ) -> WorkerOutput:
        """`workdir` is the feature's sandbox. A worker never sees the project's
        own repository, only the checkout its feature was given.

        `role` is which agent is doing this. Integration is the same shape of
        job as a unit -- read a tree, change it, account for what you did -- but
        it is a different agent with its own model and budget, and billing it as
        a worker would hide that.

        `on_environment` receives the prepared environment before the session
        starts, ready or not. It is the only way out for that fact: otherwise a
        stack that failed to come up is a paragraph in the brief and nothing
        else, so whether a unit could run its own code is known to the agent,
        to no record, and to no reader.
        """
        ...


class DirectExecutor:
    """AC-7.1 -- the model returns file contents as structured output. No tools.

    Sufficient to prove the pipeline's shape end to end, and cheap enough to run
    the whole factory while you are still iterating on the prompts.
    """

    kind = "direct"

    def __init__(self, llm: LLM, config: Config) -> None:
        self.llm = llm
        self.config = config

    async def run(
        self, unit: WorkUnit, spec: Spec, digest: str, system: str, workdir: Path,
        role: str = "worker", on_prompt: Callable[[str, str], None] | None = None,
        environment: Callable[[Path], Any] | None = None,
        on_environment: Callable[[Any], None] | None = None,
        send_back: Any = None,
        on_guides: Callable[[dict[str, Any]], None] | None = None,
    ) -> WorkerOutput:
        prompt = (
            unit_brief(unit, spec, digest)
            + "\n\n---\n\n"
            + "Return complete file contents for every file you write -- not diffs, "
            "not fragments. Then return your decision log and your mandatory "
            "self-disclosure.\n"
        )
        if on_prompt:
            on_prompt("brief", prompt)
        # No harness here, so the guides' text was in the brief: say so.
        if on_guides is not None and unit.guidance_delivered:
            on_guides({"harness": False, "guides": list(unit.guidance_delivered)})
        output: WorkerOutput = await self.llm.ask(role, prompt, WorkerOutput, system=system)
        output.unit_id = unit.id
        # The orchestrator's to set. This executor has no checkout to measure a
        # worker in, so it never sends one back; whatever a model put here is
        # not a record of anything.
        output.send_backs = []
        return output


def files_on_disk(
    root: Path, paths: Sequence[str], *, per_file: int, budget: int,
) -> str:
    """Named files, verbatim, for a model that has no checkout to open.

    Deliberately not `pipeline.tree_section`, and not only because importing it
    here would be a cycle. That one renders *model-written* paths and has to
    classify them first, since a finding's `files` entry may be a sentence. These
    paths come from the plan by way of `_context_files`, which has already
    checked every one against the filesystem.

    Truncation is announced in the text rather than done silently, because the
    caller's next move is to ask for these files back complete.
    """
    parts: list[str] = []
    used = 0
    for rel in paths:
        try:
            target = safe_join(root, rel)
        except PathEscape:
            continue
        if not target.is_file():
            parts.append(f"----- {rel} -----\n[does not exist yet: you are creating it]\n")
            continue
        try:
            body = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            parts.append(f"----- {rel} -----\n[unreadable: {exc}]\n")
            continue
        lines = body.count("\n") + 1
        note = ""
        room = min(per_file, max(0, budget - used))
        if len(body) > room:
            body = body[:room]
            note = (f"\n... [TRUNCATED at {room} of {len(target.read_text(encoding='utf-8', errors='replace'))} "
                    "characters. You have not been shown this whole file. Do not return it "
                    "as though you had.]")
        parts.append(f"----- {rel} ({lines} lines) -----\n{body}{note}\n")
        used += len(body)
    return "\n".join(parts) if parts else "(no files)"


#: What the harness driver prints to declare what it spent. Kept in step with
#: `factory/harnesses/openhands_driver.py`.
SPEND_MARKER = "FACTORY-HARNESS-SPEND "


def harness_spend(output: str) -> dict[str, Any] | None:
    """What the harness says this unit cost, if it says anything.

    A coding harness is a separate process on the same provider key, so every
    token it burns is real money that never passes through this app's client.
    Without this, the factory's cost is not an underestimate -- it is a
    different number about a smaller thing, printed where the cost of the run
    belongs. One run reported $1.20 against $4.44 actually billed.

    Returns None rather than raising for a harness that says nothing: not every
    harness can report, and a unit is not a failure because its bookkeeping is.
    """
    for line in reversed((output or "").splitlines()):
        marker = line.find(SPEND_MARKER)
        if marker == -1:
            continue
        try:
            spent = json.loads(line[marker + len(SPEND_MARKER):].strip())
        except (ValueError, TypeError):
            return None
        return spent if isinstance(spent, dict) else None
    return None


def read_spend_file(path: Path) -> dict[str, Any] | None:
    """The harness's running total, which survives being killed.

    A file rather than a line of output, because the output is what a timeout
    destroys: the driver prints its total after the last turn returns, and a
    process killed at the ceiling never gets there. Written after every turn, so
    what is here is what had been spent when the axe fell.
    """
    try:
        spent = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return spent if isinstance(spent, dict) else None


@dataclass
class Authored:
    """What one authoring session produced, measured rather than reported."""

    files: list[FileWrite] = dataclass_field(default_factory=list)
    outside: list[str] = dataclass_field(default_factory=list)
    log: str = ""
    argv: list[str] = dataclass_field(default_factory=list)
    attempts: int = 0
    cost_usd: float = 0.0


def ignored(path: str, ignore: Sequence[str]) -> bool:
    """Whether a changed path is a harness's bookkeeping rather than work.

    A bare name matches a whole path segment with a dot boundary, so `.git`
    does not also swallow a `.gitignore` a unit wrote. An entry with a slash
    matches that path and everything under it -- which is how a tool's local
    settings file is dropped while the skills beside it are not: a skill is
    one of this project's rules, and a unit that edits one must be seen to.
    """
    for entry in ignore:
        if "/" in entry:
            if path == entry or path.startswith(entry.rstrip("/") + "/"):
                return True
        elif any(seg == entry or seg.startswith(entry + ".") for seg in path.split("/")):
            return True
    return False


def changed_under(tree: Path, ignore: Sequence[str] = ()) -> list[str]:
    """Every path git reports as new or modified in `tree`.

    A free function rather than a `Worktree` method because the oracle's tree is
    not a worktree of anything -- it is an empty repository made for the
    purpose, and that is the whole point of it.
    """
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=str(tree), capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    drop = tuple(ignore)
    out: list[str] = []
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip('"')
        if ignored(path, drop) or is_runner_output(path):
            continue
        out.append(path)
    return out


# Who the next harness invocation is, for the call ledger. Set by the session
# that is about to run and read by `_invoke`, which is where every attempt --
# first, retry, fallback -- actually happens. Per task, because one executor
# serves every unit running at once.
_SESSION_CALL: contextvars.ContextVar[dict[str, str] | None] = \
    contextvars.ContextVar("factory_session_call", default=None)


def _session_outcome(code: int, output: str, route=None) -> tuple[str, str, str]:
    """What happened to one harness run, in the call ledger's words.

    Three values where there were two: what happened, the sentence a person
    reads, and the text that verdict was taken from. The third is here because
    the row keeps the last 2000 characters of a stream the verdict was read out
    of 6000 -- so a call could be recorded as refused with the evidence for it
    outside the record, and the only way to learn what the harness had said was
    to run the thing again.
    """
    if code == -9:
        return "timed_out", ((output or "").strip().splitlines()[0][:200]
                             if output else ""), ""
    why, said = session_refusal(output or "", route)
    if why:
        return "refused", why, said
    if code != 0:
        return "failed", f"exited {code}", ""
    return "answered", "", ""


NO_EDITS = "the coding harness produced no edits"
FELL_BACK = "this unit was built by the `direct` executor, not the coding harness"
REFLECTION_LOST = "no account of this work could be obtained from the model"
REFLECTION_EMPTY = (
    "the account of this work was a placeholder, so the diff is the only record of it"
)


def _is_placeholder(reflection, files) -> bool:
    """Is this an answer, or a model going through the motions.

    One repair unit's entire report was the string `Test.`, with one decision
    titled `test` and a disclosure whose flags, assumptions and
    not_implemented were all empty -- while its diff changed the login and
    invite routers: a row lock, an integrity error turned into a 404, an
    advisory lock. Accepted as written, it reached the packet as an account,
    and the only reason anybody knew was that a reviewer read the diff and
    said so.

    Two conditions, and both have to hold. It disclosed nothing at all -- no
    flag, no assumption, nothing missing, no deviation -- and its summary is
    too short to be an account: under thirty characters whatever the diff, or
    under twenty-five per file once a diff reaches three of them, to a ceiling
    of two hundred. A sentence
    about eleven files is not a summary, it is a receipt.

    A terse answer that discloses something is left alone: "Renamed the class"
    plus one flag is a small change described small, which is what a small
    change deserves. The cost of a false positive is one more call.
    """
    summary = (getattr(reflection, "summary", "") or "").strip()
    disclosure = getattr(reflection, "disclosure", None)
    said_nothing = not any((
        getattr(disclosure, "flags", None),
        getattr(disclosure, "assumptions", None),
        getattr(disclosure, "not_implemented", None),
        getattr(disclosure, "deviations_from_spec", None),
    ))
    if not said_nothing:
        return False
    if len(summary) < 30:
        return True
    # Capped, not open-ended. Scaling the bar with the diff alone flagged a
    # two-hundred-character account of eleven files -- detailed, specific, and
    # simply not disclosing anything, which is allowed.
    return len(files) >= 3 and len(summary) < min(200, 25 * len(files))
DENIED = "the harness was refused {count} tool call(s) and could not run them: {calls}"


@dataclass
class _ContainerSession:
    """A session running inside the unit's container, and its aftermath.

    Holds the three things that have to happen whatever becomes of the run: the
    credential comes back out of the container, the private copy on this
    machine is deleted, and an agent still running in there when the clock runs
    out is stopped rather than left billing.
    """

    bundle: Any
    container: str
    docker: str
    config: Any
    spend_in_container: str = ""
    route: Any = None
    home: str = ""
    argv: list[str] = dataclass_field(default_factory=list)
    host_spend_file: Path | None = None
    marker: str = dataclass_field(
        default_factory=lambda: f"factory-session-{uuid.uuid4().hex[:12]}")

    async def _exec(self, argv: Sequence[str], timeout: float = 120.0) -> tuple[int, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            return 127, str(exc)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await procs.kill(proc)
            return -9, f"timed out after {timeout}s"
        return (proc.returncode if proc.returncode is not None else -1,
                (out or b"").decode("utf-8", errors="replace"))

    async def copy_in(self, source: Path, target: str) -> None:
        """A file the harness needs that the image does not carry.

        For a harness that is a script in this repository rather than an
        installed program: it has to be in there to run, and it is not a
        credential, so it is copied plainly and left for the container's life.
        """
        origin = source if source.is_absolute() else (FACTORY_ROOT / source)
        if not origin.exists():
            raise ExecutorError(
                f"this route needs {origin} inside the unit's container and that path "
                "does not exist on this machine."
            )
        code, output = await self._exec(
            [self.docker, "exec", self.container, "mkdir", "-p",
             str(PurePosixPath(target).parent)])
        if code != 0:
            raise ExecutorError(
                f"could not make room for {target} in the unit's container: "
                f"{output.strip()[-300:]}")
        code, output = await self._exec(
            [self.docker, "cp", str(origin), f"{self.container}:{target}"])
        if code != 0:
            raise ExecutorError(
                f"could not copy {origin.name} into the unit's container: "
                f"{output.strip()[-300:]}")

    async def collect_spend(self) -> None:
        """Bring the running-cost file out to where the ledger reads it.

        The harness keeps it inside the container, because a file in the
        mounted worktree would be collected as part of the unit's work. Read
        out rather than left behind: this is the only record of what a session
        spent that survives the session being killed.
        """
        if not (self.spend_in_container and self.host_spend_file):
            return
        code, output = await self._exec(
            [self.docker, "exec", self.container, "cat", self.spend_in_container])
        if code != 0 or not output.strip():
            return
        try:
            self.host_spend_file.parent.mkdir(parents=True, exist_ok=True)
            self.host_spend_file.write_text(output, encoding="utf-8")
        except OSError:
            pass

    async def collect_sidecars(self) -> None:
        """The tool's session log, out to where the gauge reads it. Before the
        container goes, for the same reason as the spend file: that is where the
        plan's windows were written, and nowhere else has them."""
        if self.route is None:
            return
        from .agentbox import collect_sidecars

        await collect_sidecars(self.docker, self.container, self.route,
                               self.home, self.config.evidence_path)

    async def stop_agent(self) -> None:
        """Stop an agent left running in the container after a timeout.

        `docker exec` returns a client. Killing it says nothing to the process
        it started, which goes on working -- and, on a metered route, goes on
        spending -- inside a container nobody is reading any more.
        """
        await self._exec(
            [self.docker, "exec", self.container, "sh", "-c",
             f"pkill -f {shlex.quote(self.marker)} || true"], timeout=60)

    async def close(self) -> None:
        try:
            await sessioncreds.uninstall(self.bundle, self.container, self.config.docker)
        finally:
            self.bundle.cleanup()


def _denied_calls(route, log: str) -> list[str]:
    """Every tool call the route reports it refused, across all attempts.

    Read line by line rather than through the merged envelope: each attempt
    ends in its own result event, and folding them keeps only the last one's.
    """
    if route is None or not getattr(route, "denials_key", ""):
        return []
    from .llm import _dig

    calls: list[str] = []
    for line in log.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        denied = _dig(event, route.denials_key)
        for item in denied if isinstance(denied, list) else []:
            if isinstance(item, dict):
                name = str(item.get("tool_name") or "tool")
                detail = item.get("tool_input")
                shown = detail.get("command") if isinstance(detail, dict) else None
                calls.append(f"{name}: {shown}" if shown else name)
            else:
                calls.append(str(item))
    return calls


def _denial_flag(calls: Sequence[str]) -> str:
    shown = "; ".join(f"`{c[:120]}`" for c in list(dict.fromkeys(calls))[:5])
    return DENIED.format(count=len(calls), calls=shown)


def session_response(log: str, route=None) -> dict[str, Any]:
    """A finished session's own account of itself, shaped like a completion's.

    What it used, what it would have cost, and whether its route bills --
    read from the tool's final report, before the session's row is written.
    A row written from the outcome alone says zero tokens, zero cost, and
    "billed" on a subscription, while the same figures go only into a
    process-wide tally no feature can read.
    """
    if route is None:
        return {}
    from .llm import _as_float, _dig, route_tokens

    envelope = session_envelope(log, route)
    prompt, completion = route_tokens(envelope, route) if envelope else (0, 0)
    return {
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                  "cost": _as_float(_dig(envelope, route.cost_key)) if envelope else 0.0},
        "_route": {"billed": route.billed},
    }


def session_envelope(log: str, route=None) -> dict[str, Any]:
    """The tool's own final envelope, out of whatever it printed.

    The same two readings `_run_cli` makes of a completion: an event stream
    folded down by top-level key, or one object on the last line. A session is
    the same tool run the same way, so it is read the same way -- and once,
    here, rather than a second time wherever somebody next needs it.
    """
    from .llm import _merge_jsonl, extract_json

    text = (log or "").strip()
    if not text:
        return {}
    if route is None or route.stdout_format != "jsonl":
        try:
            one = json.loads(extract_json(text.splitlines()[-1]))
            if isinstance(one, dict) and one:
                return one
        except (json.JSONDecodeError, IndexError, TypeError, ValueError):
            pass
    folded = _merge_jsonl(text)
    return folded if isinstance(folded, dict) else {}


def _harness_said(log: str, route=None) -> str:
    """The part of a harness's output that is the tool speaking about the run.

    Everything else in there is the agent's work: its prose, its diffs, the
    output of every command it ran, and the machine's own bookkeeping. Read a
    refusal off that and you are reading the wrong speaker -- `429` is a number
    like any other in a stream of token counts, and a gauge reporting that the
    plan has room is written in the same words as a plan that has none.

    So: if the tool declared a verdict, that verdict decides, and only when it
    says the run failed is its message read for the reason. A stream that never
    reached its envelope -- killed at the timeout, or a harness that does not
    declare one -- falls back to the tail, minus the meter's own events.
    """
    text = (log or "")
    if not text.strip():
        return ""
    if route is not None and route.error_key:
        from .llm import _dig

        envelope = session_envelope(text, route)
        verdict = _dig(envelope, route.error_key) if envelope else None
        if verdict is not None:
            if not verdict:
                return ""       # the tool says it completed; nothing to read
            if isinstance(verdict, dict):
                verdict = verdict.get("message") or json.dumps(verdict)
            answer = _dig(envelope, route.result_key) if route.result_key else None
            said = answer if isinstance(answer, str) else ""
            return said or str(verdict)
    # Cut wider than the window that is read, then narrow. Cutting straight to
    # the window can leave the gauge as half a line, its key above the cut and
    # its `rateLimitType` below -- dropped by nothing, and matched by the same
    # pattern as the key would have been.
    return _without_the_gauge(text[-20000:], route)[-6000:]


def _without_the_gauge(log: str, route=None) -> str:
    """The stream minus the lines carrying the meter's own reading.

    A run of this factory was recorded as refused at its usage limit by a
    session that had just finished successfully, because the tool prints
    `{"type":"rate_limit_event","rate_limit_info":{"status":"allowed",...}}`
    immediately before its result and `rate_limit` is one of the words a
    refusal is recognised by. That line is the gauge -- the one `factory.yaml`
    points `meter_windows_key` at and the console draws the window from -- so
    the only thing it proves is that the plan was working.

    Dropped by the route's own key rather than by a vendor's event name: the
    other tool here calls its reading `rate_limits`, which trips the same word.
    """
    key = ((route.meter_windows_key or "").split(".")[0].strip()
           if route is not None else "")
    if not key or key not in log:
        return log
    return "\n".join(line for line in log.splitlines() if key not in line)


def session_refusal(log: str, route=None) -> tuple[str, str]:
    """Whether a session was refused by the account behind it, and in whose words.

    A completion raises; a session is a subprocess that writes its refusal and
    exits, so the only channel is what it printed. The patterns are `llm.py`'s,
    imported rather than restated -- one condition described in two places is
    two conditions the day somebody edits one of them. What has to be decided
    here is which part of the printing they are read against, which is
    `_harness_said`'s job.
    """
    from .llm import _EXHAUSTED_SIGNS, _RATE_LIMIT_SIGNS

    said = _harness_said(log, route)
    if not said:
        return "", ""
    for signs, why in ((_EXHAUSTED_SIGNS, "out of credits"),
                       (_RATE_LIMIT_SIGNS, "at its usage limit")):
        found = signs.search(said)
        if found:
            start = max(0, found.start() - 200)
            return why, said[start:found.end() + 200].strip()
    return "", ""


def _session_exhausted(log: str, route=None) -> bool:
    """Did this session stop because the plan did, rather than because it failed."""
    return bool(session_refusal(log, route)[0])


#: What a harness that never started leaves in its log. Not a refusal and not
#: an agent declining the work: the program named to run the agent was not
#: there to run.
_UNSTARTED_SIGNS = re.compile(
    r"OCI runtime exec failed[^\n]*|executable file not found in \$PATH[^\n]*"
    r"|exec format error[^\n]*")


def harness_unstarted(log: str) -> str:
    """The line saying this session's harness never started, or empty.

    Every breaker session of a run can die this way -- its fallback's
    interpreter not in the container -- and the packet then says the breaker
    wrote no probe, which reads as an agent that looked at the code and found
    nothing to attack. It never saw the code.
    """
    found = _UNSTARTED_SIGNS.search(log or "")
    return found.group(0).strip() if found else ""


def _retry_note(exit_code: int) -> str:
    """What the second attempt is told, and only the second.

    Stated as a fact about the last run rather than as encouragement. The task
    itself is unchanged above; repeating it in different words would give the
    harness two briefs to reconcile.
    """
    return (
        "\n\n---\n\n"
        "# This is the second attempt. The first one edited nothing.\n\n"
        f"The harness ran, exited {exit_code}, and left every file exactly as it was. "
        "Nothing reads what it printed: the deliverable is the edit on disk, and there "
        "is no one on the other end of a question. Open the files named above, change "
        "them, and save them.\n"
    )


#: Roles for which editing nothing can be the right answer, and so is not by
#: itself a failure to report.
#:
#: The integrator's remit is the space *between* units. When the seams already
#: hold there is nothing to change, and its brief says so in as many words. Read
#: as a failed session it is recorded as "the coding harness produced no
#: edits", "the whole of integration" not implemented and "$0.6393 producing
#: nothing" -- about a session that spent 106 seconds walking the seams,
#: confirming the cascade delete, the workspace-scoping and the e2e hooks, and
#: said so. Three inaccurate claims, which the arbiter then has to reason
#: around, and the true one -- that it left no way to show the seams hold -- is
#: nowhere in them. That is added downstream where it can be checked against
#: the files: see `pipeline.settle_integration`.
NO_EDIT_IS_AN_OUTCOME = frozenset({"integrator"})


def _no_edits_output(unit: WorkUnit, argv: Sequence[str],
                     runs: Sequence[tuple[int, str]],
                     role: str = "") -> WorkerOutput:
    """The harness ran and changed nothing, recorded as a fact rather than prose.

    An empty `files` is the half a caller can act on without reading a word. The
    summary carries the exit codes and the tail of the log, because when this
    happens that log is the only account of why it happened -- otherwise the
    failure is invisible except where an agent happens to be honest about it.

    What it does not do is say what the silence *means*. For a worker, nothing
    written is nothing built. For a role in `NO_EDIT_IS_AN_OUTCOME` it may be
    the job done, and only the artifacts on disk can tell the two apart -- so
    the reading is left to whoever can check them.
    """
    tried = ", ".join(f"exit {code}" for code, _ in runs)
    tail = (runs[-1][1] if runs else "").strip()[-800:]
    an_outcome = role in NO_EDIT_IS_AN_OUTCOME
    return WorkerOutput(
        unit_id=unit.id,
        summary=(
            f"The coding harness edited no file, on {len(runs)} attempt(s) ({tried}). "
            + ("Whether that is right depends on what this step was for, and on "
               "what it left behind.\n\n" if an_outcome
               else "Nothing was written for this unit and nothing is on the branch.\n\n")
            + f"Command: {' '.join(shlex.quote(a) for a in argv)}\n\n"
            + f"Harness output (tail):\n{tail}"
        ),
        files=[],
        decisions=[],
        disclosure=SelfDisclosure(
            not_implemented=[] if an_outcome else [f"the whole of {unit.id}: {unit.title}"],
            flags=[] if an_outcome else [NO_EDITS],
        ),
    )


class _NoEnv:
    """What a session gets when nobody prepared an environment for it.

    Duck-typed rather than imported from `unitenv`: the executor's job is to
    launch a harness in a directory, and it should not acquire an opinion about
    containers to do it. All it needs from a prepared environment is the two
    things it can act on -- variables for the process, and a paragraph for the
    brief -- plus the two it has to pass on, because "nobody prepared one" and
    "one was prepared and failed" are different facts and a reader needs both.
    """

    ready = False
    vars: dict[str, str] = {}
    note = ""
    report = ""
    problem = ""
    log = ""


@asynccontextmanager
async def _prepared(factory, tree: Path):
    """The environment for one session, or none."""
    if factory is None:
        yield _NoEnv()
        return
    async with factory(tree) as ready:
        yield ready


class _Reflection(BaseModel):
    """What a coding harness will not give you: the reviewable part."""

    summary: str = Field(description="What was built, in a paragraph, based on the diff.")
    decisions: list[Decision] = Field(default_factory=list, description="Every non-obvious choice visible in the diff.")
    disclosure: SelfDisclosure = Field(default_factory=SelfDisclosure, description="Mandatory structured disclosure derived from the diff and the unit brief.")


class CommandExecutor:
    """AC-7.2 -- hand the unit to a real coding harness, then interrogate the diff.

    The harness writes code. It does not write a decision log and it does not
    disclose what it skipped, so a second model call reads the diff against the
    brief and produces both. Without that call the packet has nothing to present.
    """

    kind = "command"

    def __init__(self, llm: LLM, config: Config) -> None:
        self.llm = llm
        self.config = config
        routed = any(r.authors() for r in config.routes.values())
        if not config.executor.command and not routed:
            raise ExecutorError(
                "executor.kind is 'command' but executor.command is empty and no route "
                "declares a `session_command`; configure one or the other, using "
                "{task_file} and {worktree}."
            )

    def _harness_prompt(self) -> str:
        """The contract a coding harness works under, as a versioned file (INV-10).

        Missing is not fatal: a run with a bare brief is what every run before
        this did, and refusing to start would be a worse failure than the one
        this prevents. It is loud, though -- the harness is the only agent in
        the system whose instructions are not enforced by a schema, so a silent
        drop here is invisible until a unit comes back empty.
        """
        try:
            return self.config.role_prompt("harness") + "\n\n---\n\n"
        except Exception:
            return ""

    @staticmethod
    def _task_stdin(template: Sequence[str], brief: str) -> str:
        """The brief, for a harness whose command does not name a task file.

        Two conventions and no way to guess which: some tools take a path and
        read it, others take the prompt itself. A template naming `{task_file}`
        has been told where to look; one that does not is fed on stdin, which
        is the only channel with no length limit. The file is written either
        way, so a human debugging a unit can read what it was asked.
        """
        return "" if any("{task_file}" in token for token in template) else brief

    def session_route(self, role: str):
        """Which route carries this agent's file-writing work.

        A role that names a route with a `session_command` runs there; anything
        else runs on `executor.command`, which is what every version before
        routes existed did and still the default. The two are the same kind of
        thing -- a CLI handed a task file and a checkout -- so nothing below
        this line has to know which one it got.
        """
        try:
            route = self.config.route_for(role)
        except Exception:
            return None
        if route.name == self.config.default_route:
            # The default route is a *description* of `api` and `executor`, not
            # a second copy of them. Its `session_command` was snapshotted when
            # the config loaded, so honouring it here would silently ignore any
            # later change to `executor.command` -- which is how a stand-in
            # harness set after load launched the real one instead, and hung.
            # One source of truth: for the default, read `executor` live.
            return None
        return route if route.authors() else None

    def _fallback_session(self, role: str):
        """Where this agent authors when its own route will not carry it."""
        return session_fallback(self.config, role)

    def _session_argv(self, role: str) -> tuple[list[str], Any]:
        """The command template for one agent's session, and the route it came from."""
        route = self.session_route(role)
        if route is not None:
            return list(route.session_command), route
        return list(self.config.executor.command), None

    def _record_session_spend(
        self, role: str, route, output: str, spend_file: Path,
    ) -> dict[str, Any] | None:
        """What a session cost, from whichever channel survived.

        Three, in order of how much a kill can take from them. The spend file is
        written as work proceeds and outlives the process; the marker line needs
        the process to reach its last print; the route's own envelope needs it
        to exit cleanly. A harness killed at the timeout has only the first --
        which is exactly why it exists, and why a route that cannot write one
        loses its spend when a unit runs long.
        """
        spent = read_spend_file(spend_file) or harness_spend(output)
        if spent is None and route is not None and route.kind == "cli":
            # The tool's own final envelope, read with the same dotted paths the
            # completion path uses. No second parser, and no vendor names here.
            from .llm import _as_float, _dig

            # Read the way `_harness_said` reads it, out of the one function
            # that knows how: a stream folded, an envelope taken whole. Read
            # here as only-the-last-line, a streamed route's window reading was
            # never in the envelope at all -- the gauge is its own event, and
            # the last line is the result.
            envelope: dict[str, Any] = session_envelope(output, route)
            if envelope:
                # The window reading, from the call that consumed the most of
                # it. Recorded before the spend is folded in, so a session that
                # produced no cost figure still reports what it used.
                try:
                    from .meters import read_windows

                    windows = read_windows(envelope, route)
                    if windows:
                        self.llm.meters.record(route.name, windows)
                except Exception:
                    pass
                from .llm import route_tokens

                prompt_tokens, completion_tokens = route_tokens(envelope, route)
                spent = {
                    "cost_usd": _as_float(_dig(envelope, route.cost_key)),
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "turns": int(_as_float(_dig(envelope, route.turns_key))),
                    # Not stringified here: this field is a map of model to
                    # usage on at least one tool, and `str()` on a dict puts the
                    # whole repr in the ledger's model column. Narrowed below,
                    # where the shape is known.
                    "model": _dig(envelope, route.resolved_model_key),
                }
        if not spent:
            return None
        model = spent.get("model")
        if isinstance(model, dict):
            from .llm import principal_model
            model = principal_model(model)
        elif model is not None and not isinstance(model, str):
            model = str(model)
        self.llm.record_external(
            role,
            str(model or session_model(self.config, role, route)),
            float(spent.get("cost_usd") or 0.0),
            int(spent.get("prompt_tokens") or 0),
            int(spent.get("completion_tokens") or 0),
            route=(route.name if route is not None else ""),
            billed=(route.billed if route is not None else True),
            turns=int(spent.get("turns") or 0),
        )
        return spent

    def _env(self, role: str = "worker") -> dict[str, str]:
        """The harness runs as its own process and cannot read this app's
        credentials file, so hand it the key under the name its CLI expects."""
        env = dict(os.environ)
        key = self.config.api.api_key
        key_env = self.config.executor.key_env
        if key_env and not key:
            # Never launch a harness that cannot authenticate. Given no key, a
            # CLI is as likely to open an interactive login and block until the
            # timeout as it is to exit -- and a hang reads as a slow build
            # rather than a missing credential.
            raise ExecutorError(
                f"the harness needs a provider key in {key_env} and this app has none "
                "stored. Set it in the console under provider settings before building; "
                "without it the harness may sit waiting on an interactive login."
            )
        if key and key_env:
            env[key_env] = key
        if self.config.executor.base_url_env and self.config.api.base_url:
            env[self.config.executor.base_url_env] = self.config.api.base_url

        # The general form. A harness configured entirely through its
        # environment -- anything built on LiteLLM -- carries its model there
        # rather than in argv, so `{model}` has to be substitutable here as well
        # as in `command`, or `factory.yaml` stops being the only file that
        # names a model.
        substitutions = {
            "model": harness_model(self.config, role),
            "api_key": key,
            "base_url": self.config.api.base_url,
        }
        for name, template in self.config.executor.env.items():
            try:
                env[name] = template.format(**substitutions)
            except KeyError as exc:
                raise ExecutorError(
                    f"executor.env[{name!r}] uses {exc} , which is not one of "
                    f"{sorted(substitutions)}"
                ) from exc
        return env

    async def _invoke(self, argv: Sequence[str], cwd: Path,
                      env: dict[str, str], stdin: str = "") -> tuple[int, str]:
        """One harness run: its exit code and everything it said.

        The exit code is read rather than discarded. Without it a harness that
        crashed, timed out, or refused the job is indistinguishable from one
        that looked at the work and decided nothing needed doing.
        """
        started = time.time()
        code, output = await self._run_harness(argv, cwd, env, stdin)
        meta = _SESSION_CALL.get()
        if meta:
            from .llm import call_entry, journal_call
            # Which route this ran on, so the run's verdict is read with the
            # keys that route publishes rather than by looking at the stream and
            # guessing. `meta` carries the name; the config holds the rest.
            route = self.config.routes.get(meta.get("route", ""))
            outcome, reason, said = _session_outcome(code, output, route)
            detail = output if outcome != "answered" else ""
            if said:
                # Appended rather than prepended: the row keeps the *last* 2000
                # characters, so evidence put at the front is the first thing
                # truncation takes.
                detail = f"{detail}\n\n-- read as a refusal from --\n{said}"
            journal_call(call_entry(
                role=meta.get("role", ""), route=meta.get("route", ""),
                asked=meta.get("model", ""), outcome=outcome, started=started,
                kind="session", reason=reason or meta.get("because", ""),
                fallback_from=meta.get("fallback_from", ""),
                detail=detail,
                unit=meta.get("unit", ""),
                response=session_response(output, route)))
        return code, output

    async def _run_harness(self, argv: Sequence[str], cwd: Path,
                           env: dict[str, str], stdin: str = "") -> tuple[int, str]:
        # Every path to a session comes through here -- the route's own, its
        # fallback, the retry -- so this is where "never on this machine" is
        # held. Refused loudly rather than degraded: a session with nowhere
        # sealed to run is a broken environment, and saying so costs one phase
        # rather than an agent loose on the host.
        try:
            require_contained(argv, self.config.docker.binary, "an agent session")
        except IsolationError as exc:
            raise ExecutorError(str(exc)) from None
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=str(cwd), env=env,
                stdin=asyncio.subprocess.PIPE if stdin else None,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            raise ExecutorError(f"could not start harness {argv[0]!r}: {exc}") from exc
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(stdin.encode() if stdin else None),
                timeout=self.config.executor.timeout_s,
            )
        except asyncio.TimeoutError:
            proc.kill()
            # What it managed to say before it was killed, not just the fact of
            # the kill. Discarding it threw away the whole harness log for every
            # unit that ran long -- and the reflection below reads that log to
            # account for the diff, so a timed-out unit was explained by the
            # single sentence "harness timed out".
            partial = b""
            try:
                partial, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            except (asyncio.TimeoutError, Exception):
                pass
            tail = (partial or b"").decode("utf-8", errors="replace")
            return -9, (
                f"harness timed out after {self.config.executor.timeout_s}s\n\n{tail}"
            )
        code = proc.returncode if proc.returncode is not None else -1
        return code, (stdout or b"").decode("utf-8", errors="replace")

    async def _fallback(
        self, unit: WorkUnit, spec: Spec, digest: str, system: str, workdir: Path,
        role: str, on_prompt: Callable[[str, str], None] | None,
        argv: Sequence[str], runs: Sequence[tuple[int, str]],
    ) -> WorkerOutput | None:
        """Build this unit with `direct`, after the harness edited nothing twice.

        Returns None if that fails too, so the caller records the honest empty
        result rather than a fallback's excuse for one. What the fallback *did*
        is recorded on the output either way: a unit built this way is not the
        same as a unit the harness built, and a reader who cannot tell them
        apart cannot tell how much to trust the diff.
        """
        # The files themselves, not the digest. `run` drops the digest because a
        # harness has a checkout to read; the fallback has neither, and handing
        # it a truncated outline of a repository it must return *complete files*
        # for is asking it to reconstruct from memory. It did exactly that: a
        # 1,282-line router came back as 300 lines with two endpoints missing,
        # and the model said so in its own disclosure -- "written from scratch
        # because their original content was not visible in the repo digest".
        #
        # With the real contents in front of it the job becomes editing rather
        # than remembering, which is the job it can actually do.
        context = self._fallback_context(unit, Path(workdir))
        try:
            output = await DirectExecutor(self.llm, self.config).run(
                unit, spec, context, system, workdir, role=role, on_prompt=on_prompt,
            )
        except Exception as exc:
            # Not fatal: the caller's empty-unit path is still correct, and it
            # says more about the run than an exception here would.
            runs = [*runs, (-1, f"direct fallback raised {type(exc).__name__}: {exc}")]
            return None
        if not output.files:
            return None

        tried = ", ".join(f"exit {code}" for code, _ in runs)
        output.summary = (
            f"Built by the `direct` executor after the coding harness edited no file on "
            f"{len(runs)} attempt(s) ({tried}).\n\n"
            f"Harness command: {' '.join(shlex.quote(a) for a in argv)}\n\n"
            f"Harness output (tail):\n{(runs[-1][1] if runs else '').strip()[-800:]}\n\n"
            f"---\n\n{output.summary}"
        )
        output.disclosure.flags = [*output.disclosure.flags, NO_EDITS, FELL_BACK]
        return output

    def _fallback_context(self, unit: WorkUnit, root: Path) -> str:
        """The unit's own files, in full, as the `direct` executor's context.

        Writable files first and unabridged -- the fallback must return each of
        them complete, so anything elided here comes back deleted. Reference
        files follow and may be trimmed; losing the tail of a file the unit only
        reads costs accuracy, not code.
        """
        writable, reference = self._context_files(unit, root)
        parts: list[str] = []

        if writable:
            parts.append(
                "# The files this unit owns, as they are on disk right now\n\n"
                "You must return each of these COMPLETE. Anything you leave out is "
                "deleted from the repository. If a file below is long, that is not an "
                "invitation to summarise it -- return it in full with your change applied.\n\n"
                + files_on_disk(root, writable, per_file=120_000, budget=400_000))
        if reference:
            parts.append(
                "---\n\n# Files this unit must read but must not change\n\n"
                + files_on_disk(root, reference, per_file=20_000, budget=120_000))
        return "\n\n".join(parts) or "(this unit owns no files that exist yet)"

    def _context_files(
        self, unit: WorkUnit, root: Path,
    ) -> tuple[list[str], list[str]]:
        """Which files to hand the harness as writable, and which as reference.

        A chat-context harness sees what it is passed plus a map -- signatures,
        not contents -- of the rest, and in one-shot mode it cannot widen that:
        asking for a file ends the turn. So what it is passed here is the whole
        of what it can read, and a file missing from this list is a file the
        unit will have to guess at or fail on.

        Writable is exactly `files_expected`, including paths that do not exist
        yet: telling the harness "you will be creating this" is the point.

        Reference has two sources. The architect names what a unit must read,
        which is the honest one -- it is the only agent that knows why a unit
        exists. And then the siblings of every directory the unit writes into,
        because that is the answer to the failure this exists for: the unit
        owning `alembic/versions/e5f6_slots.py` needed the rest of
        `alembic/versions/` to know what its migration descends from, and no
        model was ever going to name that file in advance -- the whole question
        is which revision is currently at the head.

        Bounded, because it is computed. A directory with more siblings than the
        cap is skipped entirely rather than sampled: an arbitrary twelve files
        out of two hundred is worse than none, because it looks like context.
        """
        cfg = self.config.executor
        # Resolved once, and it matters: `safe_join` resolves, so on a platform
        # where the worktree sits under a symlink (macOS puts /var under
        # /private/var) an unresolved root makes every `relative_to` below raise.
        root = Path(root).resolve()
        writable = [p for p in unit.files_expected if p]
        owned = set(writable)

        def usable(rel: str) -> bool:
            if not rel or rel in owned:
                return False
            try:
                target = safe_join(root, rel)
            except PathEscape:
                return False
            return target.is_file()

        reference: list[str] = [p for p in unit.read_files if usable(p)]

        seen_dirs: set[str] = set()
        for rel in writable:
            parent = PurePosixPath(rel).parent
            if str(parent) in (".", "") or str(parent) in seen_dirs:
                continue
            seen_dirs.add(str(parent))
            try:
                directory = safe_join(root, str(parent))
            except PathEscape:
                continue
            if not directory.is_dir():
                continue
            siblings = sorted(
                q.relative_to(root).as_posix()
                for q in directory.iterdir()
                if q.is_file() and not q.name.startswith(".")
            )
            if len(siblings) > cfg.max_siblings_per_dir:
                continue
            reference.extend(s for s in siblings if usable(s))

        # Order preserved, duplicates dropped, then capped. The architect's own
        # list is first, so what survives the cap is what somebody chose.
        reference = list(dict.fromkeys(reference))[: max(0, cfg.max_context_files)]
        return writable, reference

    async def _retry_if_empty(
        self, argv, env, template, brief, task_file, tree, runs,
        files, changed, litter, collect,
    ):
        """One more attempt when the harness edited nothing, or the same result.

        A harness that edited nothing has not answered, whatever it printed and
        whatever it exited with. aider, for one, can read the files it was
        given, lose the thread of its own conversation, ask for a task
        description, and exit -- and the repair round it takes with it is
        scored as an attempt.

        The retry earns its cost by not being the same run. The harness's own
        bookkeeping is cleared first, so stale chat state cannot confuse it
        twice, and the task file says what just happened. Empty files go
        with the litter: a placeholder the harness created and never filled
        would otherwise still be sitting there on the second pass, looking like
        prior work.
        """
        if files:
            return files, changed, litter
        empties = [rel for rel in changed
                   if not (tree.path / rel).is_dir() and not tree.read(rel)]
        for rel in [*litter, *empties]:
            stray = tree.path / rel
            if stray.is_dir():
                shutil.rmtree(stray, ignore_errors=True)
            else:
                stray.unlink(missing_ok=True)
        retry = brief + _retry_note(runs[0][0])
        task_file.write_text(retry, encoding="utf-8")
        runs.append(await self._invoke(
            argv, tree.path, env, self._task_stdin(template, retry)))
        return collect()

    async def _send_back(
        self, send_back: Any, prepared: Any, tree: Worktree, files: list[FileWrite],
        brief: str, task_file: Path, argv: Sequence[str], env: dict[str, str],
        template: Sequence[str], runs: list[tuple[int, str]],
        collect: Callable[[], tuple[list[FileWrite], list[str], list[str]]],
        on_prompt: Callable[[str, str], None] | None,
    ) -> list[SendBackRecord]:
        """Measure the unit; send what is unproved back to the same session.

        At most `send_back.limit` times, and then the unit finishes whatever is
        left: what the gates find afterwards is the packet's to say. Needs a
        prepared environment -- a worker with nothing to run its tests in is
        not measured, rather than being sent back for what could not be run.
        A measurement that fails is recorded and ends the loop; it never fails
        the unit, whose work is already on disk.
        """
        if send_back is None or not files or not getattr(prepared, "ready", False) \
                or getattr(prepared, "runner", None) is None or send_back.limit <= 0:
            return []
        records: list[SendBackRecord] = []
        current = files
        for attempt in range(1, send_back.limit + 1):
            try:
                found = await send_back.measure(
                    tree.path, prepared.runner, [f.path for f in current if not f.deleted])
            except Exception as exc:  # noqa: BLE001 -- measuring never fails a unit
                records.append(SendBackRecord(
                    attempt=attempt, asked=[f"could not be measured: {type(exc).__name__}: {exc}"]))
                break
            if not found:
                break
            before = {f.path: f.contents for f in current}
            again = send_back_brief(found, brief, attempt, send_back.limit)
            task_file.write_text(again, encoding="utf-8")
            if on_prompt:
                on_prompt("send_back", again)
            runs.append(await self._invoke(argv, tree.path, env, self._task_stdin(template, again)))
            current, _, _ = collect()
            records.append(SendBackRecord(
                attempt=attempt, asked=list(found),
                changed=sorted(f.path for f in current if before.get(f.path) != f.contents)))
        return records

    def _container_route(self, role: str, session_route):
        """The route whose agent should run inside the unit's container, if any.

        `session_route` is None for the default route, on purpose: its host
        command lives in the `executor` block and is read live, so that path
        must not be served from a snapshot. But the default route is also the
        one carrying every role billed against a provider key -- most of them --
        so reading only `session_route` here would have left exactly those
        agents on the host. Resolve the role's route directly for this one
        question.
        """
        if session_route is not None:
            return session_route if session_route.authors_in_container() else None
        try:
            route = self.config.route_for(role)
        except Exception:
            return None
        return route if route.authors_in_container() else None

    async def _open_container_session(
        self, role: str, route, prepared, template: Sequence[str], *,
        task_file: Path, spend_file: Path, model: str = "",
    ):
        """One authoring session, to be run inside the unit's own container.

        The agent has to run the project's commands. On the host it is refused
        them: every one of these harnesses carries a permission layer whose job
        is to stop an unattended agent executing anything, and the layer is
        right -- the host is somebody's machine. Inside the unit's container
        that objection is answered by the container, which holds this worktree,
        the project's dependencies and its database and nothing else. So the
        harness runs in its own no-questions mode in there.

        Returns None when this unit has no container, which the caller reads as
        "run it on the host as before". Otherwise the caller must `close()` what
        comes back, however the session ends: it holds a credential.
        """
        variables = dict(getattr(prepared, "vars", {}) or {})
        container = variables.get("FACTORY_ENV_CONTAINER", "")
        workdir = variables.get("FACTORY_ENV_WORKDIR", "") or "/workspace"
        if not container:
            return None

        docker = self.config.docker.binary
        bundle = await sessioncreds.prepare(route, self.config)
        session = _ContainerSession(
            bundle=bundle, container=container, docker=docker,
            config=self.config, spend_in_container="",
        )
        try:
            await sessioncreds.install(bundle, container, self.config.docker)
            for source, target in route.container_payload.items():
                await session.copy_in(Path(source), target)
        except BaseException:
            await session.close()
            raise

        # Everything the templates name, in the container's terms. The task
        # file is inside the worktree, which is mounted, so it is already
        # there under another name; the spend file must not be, because
        # anything inside the tree is collected as the unit's work.
        home = route.container_env.get("HOME", "").rstrip("/")
        session.spend_in_container = f"{home or '/tmp'}/factory-session-spend.json"
        session.route, session.home = route, home
        fills = {
            "worktree": workdir,
            "task_file": f"{workdir}/{task_file.name}",
            "spend_file": session.spend_in_container,
            # The same id the host path would have used for this route. The
            # default route's harness spends this app's key through LiteLLM,
            # which wants the provider prefix `harness_model` derives; a route
            # with its own sign-in wants the bare id. `session_model` answers
            # both, given the right argument -- and given the wrong one, one of
            # these harnesses exits on "LLM Provider NOT provided" and the unit
            # reads as an agent that declined the work.
            "model": model or session_model(
                self.config, role,
                None if route.name == self.config.default_route else route),
            # Where the provider is, for a harness configured through its
            # environment rather than its argv. Its key is not here: a value in
            # `--env` is a value in a command line, which every process on this
            # machine can read. Keys travel as credentials, through a file.
            "base_url": route.base_url or self.config.api.base_url or "",
            "factory_root": "",
        }

        def fill(token: str) -> str:
            for name, value in fills.items():
                token = token.replace("{" + name + "}", value)
            return token

        argv = [docker, "exec", "--interactive", "--workdir", workdir]
        if self.config.docker.run_as_host_user and hasattr(os, "getuid"):
            # Two reasons, and the second one is not obvious. Files the agent
            # writes into the mounted worktree must come back owned by a person
            # rather than by root -- that is why every other container call
            # does this. And a harness may *refuse to run at all* as root: one
            # of these declines its own no-questions mode with "cannot be used
            # with root/sudo privileges", which is exactly the mode a session
            # in here depends on.
            #
            # A docker runner already starts its container as this user; a
            # compose runner does not, because the project's compose file says
            # who its services are. So this is said here, where both kinds of
            # session go through.
            argv += ["--user", f"{os.getuid()}:{os.getgid()}"]
        if bundle.env_file is not None:
            argv += ["--env-file", str(bundle.env_file)]
        for name, value in route.container_env.items():
            argv += ["--env", f"{name}={fill(value)}"]
        # Where the services are, and anything else the session published.
        # Only reached the host path once: an agent in here was told to read
        # `FACTORY_URL_API` and had no such variable, because `docker exec`
        # gives a process the container's environment and not the session's.
        for name, value in (getattr(prepared, "session", {}) or {}).items():
            argv += ["--env", f"{name}={value}"]
        # A label to find this process by. `docker exec` hands back a client,
        # not the process it started, so killing the client on a timeout leaves
        # an agent running inside the container with nobody reading it.
        argv += ["--env", f"FACTORY_SESSION_ID={session.marker}"]
        argv += [container, *(fill(token) for token in template)]
        session.argv = argv
        session.host_spend_file = spend_file
        return session

    def _spend_argv(self, spend_file: Path, route=None) -> list[str]:
        """The flag that tells a harness where to keep its running cost.

        Configuration, like the file flags: a harness that does not understand
        it gets nothing, and a harness that does keeps a figure that outlives
        being killed.

        Read from the route when the session came from one. The flag belongs to
        a particular CLI -- appending one harness's flag to another is a usage
        error, and the exit code it produces is indistinguishable from a harness
        that looked at the work and declined it. A worker on a second harness
        was refused twice and fell back to `direct` for exactly that.
        """
        flag = (route.session_spend_flag if route is not None
                else self.config.executor.spend_file_flag) or ""
        flag = flag.strip()
        return [flag, str(spend_file)] if flag else []

    def _context_argv(self, unit: WorkUnit, root: Path) -> list[str]:
        """The file flags, or nothing at all when this harness needs none.

        Both flags are configuration rather than literals because the command
        template is generic: an agentic harness reads what it wants and leaves
        these empty, and hardcoding `--file` here would put one CLI's spelling
        into a class that is supposed to know about none of them.
        """
        cfg = self.config.executor
        if not cfg.file_flag and not cfg.read_flag:
            return []
        writable, reference = self._context_files(unit, root)
        argv: list[str] = []
        if cfg.file_flag:
            for rel in writable:
                argv += [cfg.file_flag, rel]
        if cfg.read_flag:
            for rel in reference:
                argv += [cfg.read_flag, rel]
        return argv

    def _classify(self, tree: Worktree, also: Sequence[str] = ()) -> tuple[list[str], list[str]]:
        """What the harness wrote, and what it merely left behind.

        Litter can be a file or a whole directory (.aider.tags.cache.v3/), at
        any depth. The boundary matters: `.git` must not also swallow
        `.gitignore`, which a unit may legitimately write.
        """
        ignore = tuple(self.config.executor.ignore)
        # What Fabrika put in the checkout for this session -- a skill linked
        # into the folder this runner reads -- matched by exact path.
        bridged = set(also)

        def is_litter(path: str) -> bool:
            return path in bridged or is_runner_output(path) or ignored(path, ignore)

        seen = tree.changed_files()
        return ([p for p in seen if not is_litter(p)],
                [p for p in seen if is_litter(p)])

    async def author(
        self, *, brief: str, tree: Path, role: str,
        confine: str | Sequence[str], system: str = "",
        environment: Callable[[Path], Any] | None = None,
        on_environment: Callable[[Any], None] | None = None,
    ) -> "Authored":
        """Run the harness in `tree` and read back what it wrote under `confine`.

        The authoring half of `run`, for the two agents that produce test files
        rather than an implementation: the oracle and the breaker. Returning the
        text of every file inside a single structured answer cannot work -- a
        suite is tens of thousands of characters, the ceiling is one answer, and
        a suite cut off mid-file costs a run its whole verify lane.

        Writing to disk removes the ceiling, and it removes something worse. A
        model retyping a file into an answer is *claiming* what the file says,
        and every other output in this system is a fact rather than a claim.
        Here the file is the file, and the model is only asked for
        what no filesystem can answer: which criterion a test is for, what
        hypothesis it encodes.

        `confine` is one prefix or several, enforced by reading rather than by
        refusing. Anything the agent wrote elsewhere is reported in `outside`
        and dropped on the floor --
        which matters most for the breaker, whose tree contains the
        implementation it is attacking. A breaker that edits the code to make
        its own test fail produces a finding about a repository nobody has;
        those edits never leave this function, and the fact that it made them
        does.
        """
        env = self._env(role)
        runs: list[tuple[int, str]] = []
        tree = Path(tree).resolve()
        # A sequence, because a project can have more than one place a blind
        # test may live -- a pytest directory and a vitest one -- and the oracle
        # now writes into the directory each file will actually run in rather
        # than into one holding pen the orchestrator empties afterwards.
        prefixes = tuple(
            c.strip("/") for c in
            ([confine] if isinstance(confine, str) else list(confine))
            if c and c.strip("/")
        )

        # The harness contract, then this role's own, then the task. The role
        # prompt has to be here: a harness is launched with a task file and
        # nothing else, so a contract left in the `system` slot reaches the
        # accounting call and never reaches the agent that does the work. The
        # oracle's whole reason for existing is in that file, and a run where it
        # arrived without it would look like a run where the model ignored it.
        # The breaker is handed a running environment; the oracle a sealed one
        # with nothing running in it. That asymmetry is the point of both
        # roles: the breaker's probes are worth something only if it can watch
        # them fail, and the oracle's only because it has never seen the system
        # run.
        async with _prepared(environment, tree) as prepared:
            if on_environment:
                on_environment(prepared)
            env = {**env, **dict(prepared.vars)}
            task_file = tree / ".factory-task.md"
            contract = self._harness_prompt() + (
                system.strip() + "\n\n---\n\n" if system.strip() else "")
            # One string, used for both channels. The task reaches a harness by
            # file or by stdin depending on the route, and a note that made it
            # into only one of them would leave half the harnesses unaware they
            # had an environment at all.
            task = contract + brief + prepared.note
            task_file.write_text(task, encoding="utf-8")
            spend_file = Path(tempfile.mkdtemp(prefix="factory-spend-")) / "spend.json"

            template, route = self._session_argv(role)

            def build(tmpl, rt, model_override: str = "") -> list[str]:
                fills = {
                    "task_file": str(task_file),
                    "worktree": str(tree),
                    "model": model_override or session_model(self.config, role, rt),
                    "factory_root": str(FACTORY_ROOT),
                }
                out = [
                    functools.reduce(
                        lambda t, kv: t.replace("{" + kv[0] + "}", kv[1]), fills.items(), token)
                    for token in tmpl
                ]
                if "{spend_file}" not in " ".join(tmpl):
                    out += self._spend_argv(spend_file, rt)
                return out

            argv = build(template, route)

            # The same move as in `run`: a route that authors inside the unit's
            # container execs into it instead of running here. Every author has
            # a container now, the oracle included; one that somehow does not
            # is refused where the session would be spawned.
            opened: list[_ContainerSession] = []
            authoring = self._container_route(role, route)
            if authoring is not None:
                in_container = await self._open_container_session(
                    role, authoring, prepared, authoring.container_session_command,
                    task_file=task_file, spend_file=spend_file,
                )
                if in_container is not None:
                    opened.append(in_container)
                    argv = in_container.argv
                    template = list(authoring.container_session_command)
                elif not isolation.TEST_SUITE_ON_HOST:
                    raise ExecutorError(unprepared_container(authoring, prepared))

            def collect() -> tuple[list[FileWrite], list[str]]:
                """Files under `confine`, and the paths that went elsewhere.

                Empty files are not work, for the same reason as in `run`: a
                placeholder a harness touched into existence would otherwise both
                suppress the retry and contribute a zero-byte test.
                """
                inside: list[FileWrite] = []
                outside: list[str] = []
                for rel in changed_under(tree, self.config.executor.ignore):
                    if rel == task_file.name:
                        continue
                    if prefixes and not any(
                            rel == p or rel.startswith(p + "/") for p in prefixes):
                        outside.append(rel)
                        continue
                    try:
                        text = safe_join(tree, rel).read_text(
                            encoding="utf-8", errors="replace")
                    except (OSError, PathEscape):
                        continue
                    if text.strip():
                        inside.append(FileWrite(path=rel, contents=text, purpose=""))
                return inside, outside

            primary = (authoring if opened else route)
            _SESSION_CALL.set({
                "role": role,
                "route": primary.name if primary is not None else self.config.default_route,
                "model": session_model(
                    self.config, role,
                    None if primary is None or primary.name == self.config.default_route
                    else primary),
            })
            # A route already known to be out of credit is not asked again: the
            # session goes straight to the fallback, and the call log says why.
            primary_name = primary.name if primary is not None else self.config.default_route
            spent_check = getattr(self.llm, "spent", None)
            held = (spent_check(primary_name)
                    if callable(spent_check) and self._fallback_session(role) is not None
                    else None)
            files, outside = [], []
            if not held:
                runs.append(await self._invoke(
                    argv, tree, env, self._task_stdin(template, task)))
                files, outside = collect()
            if not files and not held:
                # Same refusal as a build unit's: a harness that wrote no file has
                # not answered, whatever it printed. The task file is rewritten so
                # the second attempt is not a replay of the first.
                retry = task + _retry_note(runs[0][0])
                task_file.write_text(retry, encoding="utf-8")
                runs.append(await self._invoke(
                    argv, tree, env, self._task_stdin(template, retry)))
                files, outside = collect()

            # Still nothing, and the route said why: this agent has run out.
            #
            # The completion path has a fallback, because a review panel can die
            # three phases from a packet; the agents with a filesystem need one
            # more, because they are the ones a run cannot proceed without. An
            # oracle that writes no suite does not cost a phase, it costs the whole verify
            # lane -- every criterion comes back unverified, and the packet says
            # the harness failed rather than anything about the code.
            #
            # Read off the log because a session is a subprocess: it does not
            # raise, it writes its refusal and exits. The signs are the same ones
            # `llm.py` reads for the same two conditions, a window and a balance.
            if not files:
                alt = self._fallback_session(role)
                log_so_far = "\n".join(text for _, text in runs)
                # Read with the route's own keys. Read without them, a session
                # that finished cleanly and merely wrote no file -- a repairer
                # that decided the finding was a false negative, say -- benched
                # this route for half an hour and sent every agent after it to
                # a substitute, on the strength of the gauge saying the plan
                # had room.
                why, said = session_refusal(
                    log_so_far, self.config.routes.get(primary_name))
                if not held and why:
                    mark = getattr(self.llm, "mark_spent", None)
                    if callable(mark):
                        mark(primary_name, why, detail=said or log_so_far)
                if alt is not None and (held or why):
                    alt_role = self.config.roles.get(role)
                    task_file.write_text(task, encoding="utf-8")
                    alt_model = fallback_session_model(self.config, role, alt)
                    # Where the substitute runs is its own route's business, not
                    # the exhausted one's. Left out, a fallback ran on the host
                    # while its route said `session_in_container` -- harmless
                    # for a harness with no permission layer, and a silent
                    # return to the original failure for one that has: every
                    # command refused, code written and never executed.
                    alt_session = None
                    if alt.authors_in_container():
                        alt_session = await self._open_container_session(
                            role, alt, prepared, alt.container_session_command,
                            task_file=task_file, spend_file=spend_file,
                            model=alt_model)
                        if alt_session is None and not isolation.TEST_SUITE_ON_HOST:
                            raise ExecutorError(unprepared_container(alt, prepared))
                    if alt_session is not None:
                        opened.append(alt_session)
                        alt_argv = alt_session.argv
                        alt_template = list(alt.container_session_command)
                    else:
                        alt_argv = build(list(alt.session_command), alt, alt_model)
                        alt_template = list(alt.session_command)
                    spent_now = spent_check(primary_name) if callable(spent_check) else None
                    _SESSION_CALL.set({
                        "role": role, "route": alt.name, "model": alt_model,
                        "fallback_from": primary_name,
                        "because": (f"{primary_name} {spent_now['why']} since "
                                    f"{time.strftime('%H:%M', time.localtime(spent_now['since']))}"
                                    if spent_now else f"{primary_name} refused"),
                    })
                    runs.append(await self._invoke(
                        alt_argv, tree, env,
                        self._task_stdin(alt_template, task)))
                    files, outside = collect()
                    argv, route = alt_argv, alt
                    getattr(self.llm, "fallbacks", []).append({
                        "role": role,
                        "from": (self.config.route_for(role).name
                                 if self.config.roles.get(role) else ""),
                        "to": alt.name,
                        "model": getattr(alt_role, "fallback_model", "") or "",
                        "because": "the session wrote nothing and its route reported a limit",
                    })

            # Inside the prepared block, because all three of these need the
            # container that is about to be taken down: the cost the session
            # kept in there, the credential that must come back out, and an
            # agent that outlived its timeout.
            for session in opened:
                if any(code == -9 for code, _ in runs):
                    await session.stop_agent()
                await session.collect_spend()
                await session.collect_sidecars()
                await session.close()

            spent = self._record_session_spend(
                role, route, "\n".join(log for _, log in runs), spend_file)
            return Authored(
                files=files, outside=outside, log=runs[-1][1] if runs else "",
                argv=argv, attempts=len(runs),
                cost_usd=float((spent or {}).get("cost_usd") or 0.0),
            )

    async def run(
        self, unit: WorkUnit, spec: Spec, digest: str, system: str, workdir: Path,
        role: str = "worker", on_prompt: Callable[[str, str], None] | None = None,
        environment: Callable[[Path], Any] | None = None,
        on_environment: Callable[[Any], None] | None = None,
        send_back: Any = None,
        on_guides: Callable[[dict[str, Any]], None] | None = None,
    ) -> WorkerOutput:
        # The digest is deliberately dropped: this executor gives the model a real
        # checkout, so a truncated transcription of the same code is not context,
        # it is a second, wrong copy competing with the first.
        #
        # The harness prompt is not dropped. `system` spent only on the
        # reflection call below would leave the thing that actually writes the
        # code with a bare task and no contract at all -- while `DirectExecutor`,
        # one class up, passes its role prompt to the model every time. A
        # harness with no role behaves like what it is: an interactive
        # assistant, for which "what would you like me to change?" is a
        # perfectly good answer, and one it gives.
        #
        # It gets its own prompt rather than the worker's, because the two
        # contracts genuinely differ: the worker returns whole files as
        # structured output, the harness edits a checkout in place, and giving
        # a harness `roles/worker.md` would tell it to do the opposite of its job.
        env = self._env(role)
        runs: list[tuple[int, str]] = []
        # Nested inside the feature's sandbox: units are isolated from each
        # other, and the whole set is isolated from every other feature.
        with Worktree(workdir, label=unit.id) as tree:
            assert tree.path is not None
            # Opened around the checkout and closed with it, because it is built
            # *on* the checkout: the stack mounts this tree, so it cannot exist
            # before the tree does and must not outlive it. Its variables reach
            # the harness process; its note reaches the brief, because an
            # environment the agent is never told about is one it will not use.
            async with _prepared(environment, tree.path) as prepared:
                # Before anything is launched, because a session that starts
                # without a stack is a fact about this unit whether or not the
                # session goes on to succeed -- and merging an empty dict and
                # appending a paragraph to the brief is not the same as
                # reading `ready`.
                if on_environment:
                    on_environment(prepared)
                env = {**env, **dict(prepared.vars)}
                brief = (self._harness_prompt() + unit_brief(unit, spec, "", harness=True)
                         + prepared.note)
                task_file = tree.path / ".factory-task.md"
                task_file.write_text(brief, encoding="utf-8")
                # Outside the worktree: anything inside it shows up in `git status`
                # and would be read as part of the unit's work.
                spend_file = Path(tempfile.mkdtemp(prefix="factory-spend-")) / "spend.json"

                # `factory_root` because the harness runs with the *worktree* as its
                # cwd, so a command naming an interpreter or a script relative to
                # this repository would resolve against the wrong tree entirely.
                # Substituted by name rather than through `str.format`, which reads
                # every brace in a configured command as a placeholder -- so a
                # command containing JSON died with `KeyError: '"cost_usd"'`, which
                # says nothing about the command or the config that holds it.
                template, route = self._session_argv(role)
                placeholders = {
                    "task_file": str(task_file),
                    "worktree": str(tree.path),
                    "model": session_model(self.config, role, route),
                    "factory_root": str(FACTORY_ROOT),
                }

                def fill(token: str) -> str:
                    for name, value in placeholders.items():
                        token = token.replace("{" + name + "}", value)
                    return token

                argv = [fill(token) for token in template]
                argv += self._context_argv(unit, tree.path)
                if "{spend_file}" not in " ".join(template):
                    # Appended rather than templated, so a harness that knows the
                    # flag gets it without every command having to name it.
                    argv += self._spend_argv(spend_file, route)

                # A route that authors inside the container replaces all of the
                # above: the command becomes an exec into this unit's own
                # container, where the project's commands are simply there to be
                # run. When there is none, the session stops here and says why
                # the container is missing; the host command built above is
                # only ever run by the test suite, and is refused where it would
                # be spawned (`isolation.py`) if anything else reached it.
                in_container = None
                authoring = self._container_route(role, route)
                if authoring is not None:
                    in_container = await self._open_container_session(
                        role, authoring, prepared,
                        authoring.container_session_command,
                        task_file=task_file, spend_file=spend_file,
                    )
                    if in_container is not None:
                        argv = in_container.argv
                        template = list(authoring.container_session_command)
                    elif not isolation.TEST_SUITE_ON_HOST:
                        raise ExecutorError(unprepared_container(authoring, prepared))
                done = guide_lib.Bridge()

                def collect() -> tuple[list[FileWrite], list[str], list[str]]:
                    """What the harness actually wrote, as opposed to what it touched.

                    The two differ, and the difference cost a run. Handing a harness
                    `--file path/that/does/not/exist/yet` makes aider *create* it,
                    empty, before the model says anything. `git status` then reports
                    a changed file, the retry below is skipped as unnecessary, and
                    the empty file contributes nothing to `files` -- so a unit that
                    got one attempt instead of two went straight to the fallback,
                    and the log said "1 attempt(s)" where it should have said 2.

                    An empty file is not work. Both the retry decision and the
                    unit's output are taken from the same list, so they can no
                    longer disagree about whether anything happened.

                    A path git reports that is no longer on disk was deleted, and
                    a deletion is work: it goes out as one rather than as an empty
                    file, which the rule above would have dropped as nothing.
                    """
                    seen, stray = self._classify(tree, done.made)
                    written: list[FileWrite] = []
                    for rel in seen:
                        if not tree.exists(rel):
                            written.append(FileWrite(path=rel, contents="", deleted=True))
                        elif contents := tree.read(rel):
                            written.append(FileWrite(path=rel, contents=contents, purpose=""))
                    return written, seen, stray

                ran_on = authoring if in_container is not None else route
                # The repository's rules, put where this runner reads them --
                # the runner that actually starts, whichever route or fallback
                # chose it -- and named in the task where they cannot be.
                runner = ran_on if ran_on is not None else self.config.routes.get(
                    self.config.default_route)
                reads = guide_lib.Reads.of(runner)
                done = guide_lib.bridge(tree.path, reads)
                found = [Guide.model_validate(g) for g in unit.guide_files]
                note = guide_lib.harness_note(found, list(unit.files_expected), reads, done)
                if note:
                    brief = brief + "\n\n---\n\n" + note
                    task_file.write_text(brief, encoding="utf-8")
                if on_guides is not None and found:
                    on_guides({
                        "harness": True,
                        "route": runner.name if runner is not None else self.config.default_route,
                        "guides": [{"path": g.path, "sha256": g.sha256, "partial": False,
                                    "part": ("bridged" if g.path in done.skills
                                             or g.path in {src for _, src in done.instructions}
                                             else "loaded")}
                                   for g in found if guide_lib._loaded(g, reads, done)],
                        "pointed": [g.path for g in found if not guide_lib._loaded(g, reads, done)
                                    and guide_lib.applies(g, list(unit.files_expected))],
                    })
                _SESSION_CALL.set({
                    "role": role,
                    "unit": unit.id,
                    "route": (ran_on.name if ran_on is not None else self.config.default_route),
                    "model": session_model(
                        self.config, role,
                        None if ran_on is None or ran_on.name == self.config.default_route
                        else ran_on),
                })
                sent_back: list[SendBackRecord] = []
                try:
                    runs.append(await self._invoke(
                        argv, tree.path, env, self._task_stdin(template, brief)))
                    files, changed, litter = collect()
                    files, changed, litter = await self._retry_if_empty(
                        argv, env, template, brief, task_file, tree, runs,
                        files, changed, litter, collect)
                    # Measured before it is accepted, while the same session is
                    # still open: its own tests, in its own environment, on what
                    # it wrote. Whatever they leave unproved goes back to it.
                    sent_back = await self._send_back(
                        send_back, prepared, tree, files, brief, task_file, argv, env,
                        template, runs, collect, on_prompt)
                    if sent_back:
                        files, changed, litter = collect()
                finally:
                    # What was bridged in comes out, so nothing of it can reach
                    # the unit's changes or outlive the session.
                    guide_lib.unbridge(tree.path, done)
                    # However this ended -- written, empty, raised or killed --
                    # the credential comes back out of the container and the
                    # copy on this machine is deleted, and an agent still
                    # running in there is stopped rather than left billing.
                    if in_container is not None:
                        if any(code == -9 for code, _ in runs):
                            await in_container.stop_agent()
                        await in_container.collect_spend()
                        await in_container.close()
                diff = tree.diff(exclude=litter)
                harness_log = runs[-1][1]

            # Before anything returns, and outside the worktree block so it happens
            # on every path including the empty one: the harness spent this whether
            # or not it produced a file, and a failed unit that burned money is
            # exactly the case a budget exists to notice.
            # The file first. A harness killed at the timeout never reaches the line
            # it prints its total on, and a killed process's stdout buffer dies with
            # it -- so for every long-running unit the printed marker does not exist.
            # Read from the marker alone, the spend of every unit killed that way
            # goes unrecorded, which is the hole a spend record exists to close.
            spent = self._record_session_spend(
                role, route, "\n".join(log for _, log in runs), spend_file)
            # Measured, not narrated: the reflection below may or may not mention
            # it, and a unit that could not run its own checks must say so either way.
            denied = _denied_calls(route, "\n".join(log for _, log in runs))

            if not files:
                # Two runs, no edits. The harness did not crash and did not refuse:
                # it answered in prose, to nobody. Rather than record an empty unit,
                # hand the same brief to the executor that cannot fail this way.
                #
                # `direct` has no edit syntax to emit and no conversation to lose
                # the thread of. It returns file contents as structured output, so a
                # model that will not do the work produces a schema error -- a
                # failure that is visible -- instead of a question nothing reads.
                # The digest goes back in because this executor has no checkout to
                # read; `run` dropped it precisely because the harness did.
                if self.config.executor.fallback_to_direct:
                    fallback = await self._fallback(
                        unit, spec, digest, system, workdir, role, on_prompt, argv, runs)
                    if fallback is not None:
                        return fallback
                # There is no diff, so there is nothing to reflect on. Asking a
                # model to account for an empty change gets a paragraph about work
                # that did not happen, and that paragraph is what every stage
                # downstream would read as the record of this unit.
                if on_prompt:
                    on_prompt("brief", brief)
                empty = _no_edits_output(unit, argv, runs, role=role)
                if spent:
                    cost = float(spent.get("cost_usd") or 0.0)
                    empty.summary += (
                        f"\n\nThe harness spent ${cost:.4f} and wrote no file."
                        if role in NO_EDIT_IS_AN_OUTCOME
                        else f"\n\nThe harness spent ${cost:.4f} producing nothing.")
                return empty

            reflection_prompt = (
                brief
                + "\n\n---\n\n"
                + "# What the coding harness actually did\n\n"
                + f"Command: {' '.join(shlex.quote(a) for a in argv)}\n\n"
                + f"## Files changed ({len(changed)})\n"
                + "\n".join(f"- {c}" for c in changed)
                + "\n\n## Diff\n\n```diff\n"
                + (diff or "(no diff available)")
                + "\n```\n\n## Harness output (tail)\n\n```\n"
                + harness_log[-6000:]
                + "\n```\n\n---\n\n"
                + "Read the diff against the unit brief. Produce the decision log and the "
                "mandatory self-disclosure. Judge what was actually written, not what the "
                "brief asked for: anything in the brief that is missing from the diff belongs "
                "in `not_implemented`, and anything done differently belongs in "
                "`deviations_from_spec`.\n"
            )
            # INV-2: both halves of every exchange. Without this the only account of
            # a harness run is the answer it produced, and a run that fails leaves no
            # way to ask what it was shown.
            if on_prompt:
                on_prompt("brief", brief)
                on_prompt("reflection", reflection_prompt)
            try:
                reflection: _Reflection = await self.llm.ask(
                    role, reflection_prompt, _Reflection, system=system
                )
            except LLMError as first:
                # The code is already on disk. The harness wrote it before this call
                # was made, and this call only narrates it -- so losing the unit
                # because its narrator ran out of room throws away work that is
                # done. A repairer's narration can run 277,023 characters into a
                # 32,000-token ceiling, and the round would lose a repair that has
                # already been made.
                #
                # The retry asks for less rather than asking again. A cut-off answer
                # means the same prompt fails the same way, so the diff is trimmed
                # hard and the answer is capped; what cannot be shortened is the
                # obligation to say what is missing.
                compact = (
                    brief
                    + "\n\n---\n\n# What the coding harness did\n\n"
                    + f"## Files changed ({len(changed)})\n"
                    + "\n".join(f"- {c}" for c in changed)
                    + "\n\n## Diff (truncated)\n\n```diff\n"
                    + (diff or "(no diff available)")[:4000]
                    + "\n```\n\n---\n\n"
                    + "Your previous answer was cut off before it finished. Answer briefly: "
                    "at most 120 words of `summary`, at most three `decisions`, and a "
                    "`disclosure` that names only what is missing or done differently.\n"
                )
                if on_prompt:
                    on_prompt("reflection", compact)
                try:
                    reflection = await self.llm.ask(
                        role, compact, _Reflection, system=system
                    )
                except LLMError as second:
                    # Recorded as missing rather than invented. An empty decision
                    # log is a hole in the packet a human can see; a fabricated one
                    # is a hole they cannot.
                    reflection = _Reflection(
                        summary=(
                            f"{len(files)} file(s) were changed by the harness and are on the "
                            f"branch, but no account of them could be obtained. First attempt: "
                            f"{first}. Second, shortened: {second}. The diff is the only record "
                            "of this unit; read it."
                        ),
                        decisions=[],
                        disclosure=SelfDisclosure(flags=[REFLECTION_LOST]),
                    )
            # An answer that is not an answer. Asked again, once, with the
            # refusal said out loud -- and if it comes back the same, recorded
            # as missing rather than passed off as an account. A hole in the
            # packet a human can see beats one they cannot.
            if _is_placeholder(reflection, files):
                insist = (
                    reflection_prompt
                    + "\n\n---\n\n"
                    + "Your previous answer was a placeholder: "
                    + f"{(reflection.summary or '').strip()[:80]!r} with nothing disclosed, "
                    + f"for a change across {len(files)} file(s). That is not an account of "
                    "this work and it will not be published as one. Read the diff above and "
                    "write what was actually done, what was decided and what was left "
                    "undone. If you cannot, say so in `summary` and leave the rest empty.\n"
                )
                if on_prompt:
                    on_prompt("reflection", insist)
                try:
                    second_try = await self.llm.ask(
                        role, insist, _Reflection, system=system)
                except LLMError:
                    second_try = None
                if second_try is not None and not _is_placeholder(second_try, files):
                    reflection = second_try
                else:
                    reflection = _Reflection(
                        summary=(
                            f"{len(files)} file(s) were changed by the harness and are on "
                            "the branch. Asked twice what it did, the model answered with a "
                            f"placeholder both times (latest: "
                            f"{(reflection.summary or '').strip()[:80]!r}). The diff is the "
                            "only record of this unit; read it."
                        ),
                        decisions=[],
                        disclosure=SelfDisclosure(flags=[REFLECTION_EMPTY]),
                    )

            if denied:
                reflection.disclosure.flags = [
                    _denial_flag(denied), *reflection.disclosure.flags]
            went = [r for r in sent_back if r.asked and not r.asked[0].startswith("could not be measured")]
            if went:
                rewritten = sorted({p for r in went for p in r.changed})
                reflection.disclosure.flags = [
                    f"Sent back {len(went)} time(s) during its turn for what its tests "
                    "left unproved; written or rewritten after a send-back: "
                    + (", ".join(rewritten) or "nothing"),
                    *reflection.disclosure.flags]
            # What could not be measured is a blind spot, said where the unit is
            # read: otherwise a send-back that never happened looks like work
            # that needed none.
            unmeasured = [r.asked[0] for r in sent_back
                          if r.asked and r.asked[0].startswith("could not be measured")]
            unmeasured += list(getattr(send_back, "notes", None) or [])
            if unmeasured:
                reflection.disclosure.flags = [
                    "Not fully measured during its turn: " + "; ".join(dict.fromkeys(unmeasured)),
                    *reflection.disclosure.flags]
            return WorkerOutput(
                unit_id=unit.id,
                summary=reflection.summary,
                files=files,
                decisions=reflection.decisions,
                disclosure=reflection.disclosure,
                send_backs=sent_back,
            )


def build_executor(config: Config, llm: LLM) -> Executor:
    """AC-7.3 -- select on `executor.kind`; refuse a half-configured harness."""
    kind = (config.executor.kind or "direct").strip().lower()
    if kind == "direct":
        return DirectExecutor(llm, config)
    if kind == "command":
        return CommandExecutor(llm, config)
    raise ExecutorError(f"unknown executor.kind {kind!r}; expected 'direct' or 'command'")
