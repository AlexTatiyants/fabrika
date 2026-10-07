"""Is this route actually usable, and if not, what does the human do about it.

A route can fail three ways and they need three different sentences. The tool is
not installed. The tool is installed and nobody has signed in. The tool is
installed, signed in, and something else went wrong. Told apart, each has an
obvious next action; run together as "route unavailable", none of them does.

Nothing here knows the name of a vendor. What to run, how to ask its version,
which models it offers and what a human must do to connect it are all fields on
`RouteConfig`, because a package that hardcodes one CLI has to be edited to add
the next one. (V-6, INV-7)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from dataclasses import dataclass, field, fields
from typing import Any, Sequence

from . import procs
from .config import Config, RoleConfig, RouteConfig
from .files import write_atomic
from .meters import (DrawStore, MeterStore, WindowReading, read_from_disk, read_limit,
                     use_mirror)

#: Past this, the console says how long ago a check was rather than treating it
#: as current. Not an expiry: a sign-in lasts weeks, and a lamp that went amber
#: on a timer taught people to ignore the lamps. The age is shown; the judgement
#: is the human's.
CHECK_STALE_S = 86_400.0

#: A probe is one turn against a live plan, so it is the shortest question that
#: still proves the whole path works: process starts, argv parses, credentials
#: are accepted, an envelope comes back.
PROBE_PROMPT = "Reply with the single word: ok"

#: Errors that mean "nobody has signed in", as opposed to anything else. Matched
#: against the tool's own message, which is the only place this information
#: exists -- these CLIs exit 0 on an auth failure and put the reason in the body.
_AUTH_SIGNS = re.compile(
    r"\b(401|403|oauth|unauthori[sz]ed|authenticat\w*|credential|"
    r"expired|not logged in|log ?in|sign ?in|api[ _-]?key)\b",
    re.IGNORECASE,
)

#: Errors that mean the tool is installed, signed in, and simply too old for
#: what it was asked to run. A fourth state, and it earns one: the fix is a
#: single command, and read as any of the other three it sends a human looking
#: at their credentials or their config instead of running it.
#:
#: This is not hypothetical. A version eighteen months of releases behind
#: resolved every tier alias to the previous model generation and rejected the
#: newest model outright -- while reporting itself connected, because it was.
_OUTDATED_SIGNS = re.compile(
    r"(version_too_old|does not support this model|requires? .{0,20}version|"
    r"run ['\"]?\w+ update)",
    re.IGNORECASE,
)

#: Errors that mean the binary is not there. `_probe` checks the filesystem
#: first, so this only catches the case where the command is a wrapper that
#: fails to find its own dependency.
_MISSING_SIGNS = re.compile(
    r"(command not found|no such file|not recognized|ENOENT|cannot find)",
    re.IGNORECASE,
)


def _tool_message(error: str) -> str:
    """The sentence the tool wrote, out of the envelope it was wrapped in.

    These errors arrive as an HTTP status with a JSON body inside a Python
    exception string. The part worth showing a human is the tool's own
    `message`, which in the case this exists for reads "…version 2.1.251 or
    newer is required. Run 'claude update'" -- an instruction, where the
    surrounding text is noise.
    """
    match = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', error)
    if not match:
        return ""
    try:
        return json.loads(f'"{match.group(1)}"')
    except ValueError:
        return match.group(1)


@dataclass
class RouteStatus:
    """What the console shows on one harness card.

    `connected` is deliberately three-valued. `None` means nobody has checked,
    which is not the same as checked-and-failed and must not be drawn as a red
    lamp -- a human who sees a failure they did not cause goes looking for a
    problem that is not there.
    """

    name: str
    label: str = ""
    kind: str = "api"
    metered: str = "tokens"
    enabled: bool = True
    completes: bool = False
    authors: bool = False
    billed: bool = True

    # The two halves of what a route is, for the two cards the console draws
    # from it. Supplied by config; see `RouteConfig.harness_label`.
    harness_label: str = ""
    account_label: str = ""
    account_kind: str = ""   # subscription | key

    schema_completions: bool = True
    installed: bool | None = None
    version: str = ""
    connected: bool | None = None
    #: ok | missing | unauthenticated | outdated | error | limited | unknown
    #:
    #: `limited` is the account saying no, not the harness failing. It is its
    #: own state because everything else on this list is a thing to fix and
    #: this one is a thing to wait for or pay for -- and because it is the only
    #: one that stops being true on its own. See `retry_at`.
    state: str = "unknown"
    detail: str = ""
    checked_at: float = 0.0
    #: When a `limited` route may be asked again: the second the hold a run
    #: would honour runs out. Past it the check is not a verdict any more, only
    #: something that happened -- see `RouteMonitor.survey`.
    retry_at: float = 0.0
    #: The last refusal and when, kept after the state it set has expired.
    #: `{"why": "out of credits", "at": <epoch>}`. A refusal four hours ago is
    #: not a refusal now, but it is still the most useful thing on the card
    #: when the question is "why did last night's run stop".
    last_refusal: dict[str, Any] = field(default_factory=dict)

    models: list[str] = field(default_factory=list)
    # What the plan itself last said about its rolling windows. Read, not
    # counted -- the human uses the same plan, so anything this factory
    # computed would be short by however much they used.
    windows: list[dict[str, Any]] = field(default_factory=list)
    # The balance behind those windows, where the tool keeps one, exactly as it
    # wrote it. Shown so a person can watch it; acted on by nothing. A run can
    # lose its whole review panel to an empty balance while both windows still
    # have room, so the two are not interchangeable.
    credits: dict[str, Any] = field(default_factory=dict)
    # A limit the tool itself reported hitting, with when. Unlike `credits`
    # this is shown as an alarm, because it is not an ambiguous flag -- it is
    # the tool saying its last call was refused, and naming why.
    limit: dict[str, Any] = field(default_factory=dict)
    # Whether this route's gauge can be brought up to date without asking it
    # anything. One tool writes its windows into a session log this console can
    # read off disk; the other reports them only inside a call it makes. That
    # difference is why one card's bars are current and the other's are as old
    # as the last thing that ran, and a reader who is not told it reasonably
    # concludes the console is simply failing to refresh.
    gauge_self_reads: bool = False
    default_model_ok: bool = False
    # What actually answered the probe. Shown as a mark on the model it names
    # rather than as a sentence: the list is already on the card, and which of
    # those runs when a role names none is the only thing the list cannot say.
    resolved_model: str = ""
    install_command: str = ""
    setup_command: str = ""
    setup_steps: list[str] = field(default_factory=list)
    setup_note: str = ""
    docs_url: str = ""

    def as_dict(self) -> dict[str, Any]:
        body = {k: getattr(self, k) for k in (
            "name", "label", "kind", "metered", "enabled", "completes", "authors",
            "billed", "harness_label", "account_label", "account_kind",
            "schema_completions", "installed", "version", "connected",
            "state", "detail",
            "checked_at", "retry_at", "last_refusal",
            "models", "windows", "credits", "gauge_self_reads",
            "default_model_ok",
            "resolved_model",
            "install_command", "setup_command",
            "setup_steps", "setup_note", "docs_url",
        )}
        body["usable"] = self.state == "ok"
        return body


def base_status(route: RouteConfig) -> RouteStatus:
    """Everything knowable without running anything."""
    return RouteStatus(
        name=route.name,
        label=route.label or route.name,
        # Unset, both halves fall back to the one name this route already has.
        # A console drawing two cards from it then says the same word twice,
        # which is honest -- it is what the config knows -- and is fixed by
        # naming them rather than by guessing a vendor here.
        harness_label=route.harness_label or route.label or route.name,
        account_label=route.account_label or route.label or route.name,
        account_kind=route.account_kind or ("key" if route.billed else "subscription"),
        kind=route.kind,
        metered=route.metered,
        enabled=route.enabled,
        completes=route.completes(),
        authors=route.authors(),
        billed=route.billed,
        models=list(route.models),
        schema_completions=route.schema_completions,
        gauge_self_reads=bool(route.meter_sidecar_glob),
        default_model_ok=route.default_model_ok,
        install_command=route.install_command,
        setup_command=route.setup_command,
        setup_steps=list(route.setup_steps),
        setup_note=route.setup_note,
        docs_url=route.docs_url,
    )


async def _run(argv: list[str], timeout: float = 20.0) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return -1, str(exc)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await procs.kill(proc)
        return -9, f"no answer within {timeout:g}s"
    return proc.returncode or 0, (out or b"").decode("utf-8", errors="replace").strip()


async def check_installed(route: RouteConfig) -> tuple[bool, str]:
    """Is the tool on this machine, and which version.

    Cheap by design: a `which` and a `--version`. No network, no tokens, no
    plan usage -- so the agents screen can answer "is Codex here at all" on
    every load without asking anyone's permission.
    """
    if route.kind != "cli" or not route.command:
        return True, ""
    binary = route.command[0]
    if not shutil.which(binary) and not binary.startswith("/"):
        return False, ""
    argv = list(route.version_command) or [binary, "--version"]
    code, out = await _run(argv)
    if code != 0 and not out:
        return False, ""
    # First line only. Some of these print a banner under the version.
    return True, out.splitlines()[0].strip() if out else ""


def ok_detail(route: RouteConfig) -> str:
    """What a *working* route still has to say, which is only its consequences.

    One line, not three. Two more would say what the card already says
    elsewhere: a turn count restates CONNECTED in the header, and the default
    model is a mark on the model it names, since the list is already on the
    card and which one answers is the only thing the list cannot say.

    What is left stays in prose. That a route's spend never reaches the budget
    guard is not a restatement of "metered in turns" -- it is the consequence of
    it, and the consequence is the part that changes what a person believes
    about the `$0.00` on a packet. A chip states a mechanism; a sentence states
    what it costs you, and putting that behind a hover would hide the
    load-bearing half.

    A function of the route rather than of the probe, so it is recomputed on
    every survey instead of being remembered from whenever Check was last
    pressed. Nothing here is news from the tool; keeping it in the cache meant a
    card could go on showing wording, or a claim, that the config had since
    stopped supporting.
    """
    return (
        ("Nothing from this route reaches the budget guard." if not route.billed else "")
        + ("  It cannot return a schema-shaped answer, and every agent here needs one, "
           "so no role can be pointed at it yet." if not route.schema_completions else "")
    ).strip()


async def check_connection(route: RouteConfig, llm: Any) -> RouteStatus:
    """One real call, and an honest reading of what came back.

    Deliberately routed through `LLM._run_cli` rather than a second, simpler
    subprocess call written here. That function is what a real run uses: it
    builds the same argv, reads the same envelope, and refuses on the same
    `error_key`. A probe that took a different path would happily pass while
    every actual call failed -- which is the whole class of bug a connection
    check exists to prevent.
    """
    status = base_status(route)
    status.checked_at = time.time()

    if route.kind != "cli":
        # An api route's credential is tested by the provider card, which knows
        # how to ask an HTTP endpoint. Saying "connected" on the strength of a
        # key being present would be a claim nothing checked.
        status.installed = True
        status.connected = bool(route.api_key)
        status.state = "ok" if route.api_key else "unauthenticated"
        status.detail = ("A key is stored for this route."
                         if route.api_key else "No key is stored for this route.")
        return status

    installed, version = await check_installed(route)
    status.installed = installed
    status.version = version
    if not installed:
        status.connected = False
        status.state = "missing"
        status.detail = (
            f"{route.command[0]!r} is not on this machine's PATH."
            + (f" Install it with: {route.install_command}" if route.install_command else "")
        )
        return status

    if not route.command:
        status.state = "error"
        status.detail = "This route has no command, so it cannot serve a completion."
        return status

    # No model named where the route can pick its own. The probe then tests the
    # path a "just connect it" role actually takes, and cannot fail on an id
    # that is merely stale -- which is how a working route once reported itself
    # broken, over a model string in the config that was never valid.
    probe_role = RoleConfig(
        name=f"route:{route.name}",
        model=("" if route.default_model_ok
               else (route.models[0] if route.models else "")),
        temperature=0.0,
    )
    try:
        response = await llm._run_cli(
            route, probe_role,
            [{"role": "user", "content": PROBE_PROMPT}], None,
        )
    except Exception as exc:
        message = str(exc)
        status.connected = False
        # Imported here rather than at the top: `llm` imports this module's
        # config types and the probe is called with an `LLM`, so a module-level
        # import would close the circle.
        from .llm import RateLimited, RouteExhausted, SPENT_HOLD_S, refusal_words
        if isinstance(exc, (RouteExhausted, RateLimited)):
            # The account said no. Not a broken harness, and read as one it
            # sends a human to the config, the credentials and the code, none
            # of which is wrong -- the same reasoning that gave these two their
            # own exception types, carried through to the card.
            #
            # A verdict with an expiry, because this is the one check whose
            # answer stops being true without anybody doing anything: a window
            # reopens on a clock, and a balance reopens when somebody pays. The
            # hold is the one a run already honours, so the card and the run
            # agree about when this route is worth asking again.
            hold = getattr(exc, "retry_after_s", 0.0) or SPENT_HOLD_S
            status.connected = True     # it answered; the answer was "no"
            status.state = "limited"
            status.retry_at = status.checked_at + hold
            status.last_refusal = {"why": refusal_words(message), "at": status.checked_at}
            status.detail = _tool_message(message) or message[:400]
            return status
        if _OUTDATED_SIGNS.search(message):
            # Checked before the auth pattern, which would otherwise claim this:
            # the tool's message names a version, and "expired" and "requires"
            # are close enough that the wrong one wins on ordering alone.
            status.state = "outdated"
            status.detail = _tool_message(message) or message[:400]
        elif _MISSING_SIGNS.search(message):
            status.state = "missing"
            status.detail = (
                f"{route.command[0]!r} could not be run: {message[:400]}"
                + (f" Install it with: {route.install_command}"
                   if route.install_command else ""))
        elif _AUTH_SIGNS.search(message):
            status.state = "unauthenticated"
            status.detail = (
                f"{status.label} is installed but nobody is signed in. "
                + (f"Run: {route.setup_command}" if route.setup_command else "")
            )
        else:
            status.state = "error"
            status.detail = message[:600]
        return status

    meta = response.get("_route") or {}
    status.connected = True
    status.state = "ok"
    ran = str(meta.get("resolved_model") or "").strip()
    # One line, not three. Two more would say what the card already says
    # elsewhere: a turn count restates CONNECTED in the header, and the default
    # model is a mark on the model it names, since the list is already on the
    # card and which one answers is the only thing the list cannot say.
    #
    # The one that stays is in full and in prose. That a route's spend never reaches
    # the budget guard is not a restatement of "metered in turns" -- it is the
    # consequence of it, and the consequence is the part that changes what a
    # person believes about the $0.00 on a packet. A chip states a mechanism; a
    # sentence states what it costs you. Shortening this to a tooltip would put
    # the load-bearing half behind a hover.
    status.resolved_model = ran
    status.detail = ok_detail(route)
    return status


def _as_refusal(status: RouteStatus) -> RouteStatus:
    """Read a stored `error` that was really an account saying no.

    A status stored by a version that did not tell the two apart is exactly
    the record the split exists to stop showing: "route 'codex' has no credit
    left: Your workspace is out of credits", saved as a broken harness and
    left on the card for hours. Left alone it would survive the upgrade that
    fixes it -- the store outlives the process, which is the whole point of the
    store -- so it is re-read on the way in rather than waiting for a human to
    press Check on a route that was never broken.

    Only the message decides. A stored `error` that says anything else is still
    an error, because it still is one.
    """
    if status.state != "error" or not status.detail:
        return status
    from .llm import SPENT_HOLD_S, refusal_words
    why = refusal_words(status.detail)
    if why == "refused":
        return status
    status.state = "limited"
    status.connected = True
    status.last_refusal = {"why": why, "at": status.checked_at}
    status.retry_at = status.checked_at + SPENT_HOLD_S
    return status


def route_fingerprint(route: RouteConfig) -> str:
    """What a check was a check *of*.

    A stored answer describes the route as it was configured at the time. Change
    the command, the auth env or the tool being launched and the old answer is
    about something else -- so it is discarded rather than shown, which is the
    difference between a stale green lamp and a wrong one.

    Deliberately narrow: the *binary* and the credential, not the invocation.

    A check establishes two things -- this tool is on the machine, and it
    accepts the credentials it finds. Neither stops being true when a flag
    changes. Fingerprinting the whole argv meant that editing an output format,
    adding a session command or moving a schema onto a file all discarded a
    perfectly good sign-in, and every card went back to amber asking a human to
    re-authorise something nobody had touched. Twice.

    What genuinely invalidates a check: a different binary, or a different place
    for it to look for credentials.
    """
    # Exactly what the check exercised, and nothing else. The session command is
    # deliberately absent: a probe never runs it, so adding or editing one
    # cannot make the probe's answer wrong.
    material = json.dumps(
        [route.kind, route.command[:1],
         route.key_env, sorted(route.env.items()), route.base_url],
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


@dataclass
class PlanVerdict:
    """Whether the plans a run needs can carry it, and if not, when they can.

    Three answers, not two. "Go" and "no" are the obvious pair; the third is the
    one that makes this worth building -- a rolling window resets at a known
    second, so "there is not enough left" and "there will be in 31 minutes" are
    the same fact, and only one of them is useful.
    """

    #: "go" | "wait" | "unknown"
    verdict: str = "go"
    route: str = ""
    #: The second the binding window resets. Only set when waiting helps.
    start_at: float = 0.0
    remaining: float = 0.0
    needed: float = 0.0
    window: str = ""
    reason: str = ""

    @property
    def seconds_to_start(self) -> float:
        return max(0.0, self.start_at - time.time()) if self.start_at else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict, "route": self.route, "start_at": self.start_at,
            "remaining": round(self.remaining, 4), "needed": round(self.needed, 4),
            "window": self.window, "reason": self.reason,
            "seconds_to_start": round(self.seconds_to_start),
        }


#: How long a probe that left a route's reading still out of date holds off
#: the next one. Long enough that browsing the console costs nothing; short
#: enough that a fixed route is measured again within the hour.
PROBE_HOLD_S = 30 * 60


class RouteMonitor:
    """The console's view of every route, and the memory of what was checked.

    Checks are cached because each one spends a turn against a real plan, and
    opening a screen is not consent to spend. `refresh` is the button; `survey`
    is what the page load gets, and it never costs anything.

    The memory is on disk rather than in this object. "Claude Code is installed
    and signed in" is a fact about the machine, and it outlives the process that
    measured it -- held only in memory, a server restart would send every lamp
    back to amber and ask a human to re-authorise something they authorised
    ten minutes earlier. Nothing about the answer has changed; only the
    process holding it has gone.

    Nor does it expire on a clock. A sign-in lasts weeks, and a check that
    quietly stopped counting after ten minutes taught people to ignore the
    lamps. What is stored instead is *when* it was measured, so the console can
    say "checked yesterday" and let a human decide whether that is good enough.
    """

    def __init__(self, config: Config, llm: Any) -> None:
        self.config = config
        self.llm = llm
        self._checked: dict[str, RouteStatus] = {}
        self._meters = MeterStore(config.evidence_path / "meters.json")
        use_mirror(config.evidence_path)
        # A probe that could not produce a current reading, and when. See
        # `gauge_live`: without this, a route whose reading a probe cannot reach
        # is probed on every visit, forever.
        self._probed: dict[str, float] = {}
        # What runs have cost, beside what the plans have left. Two halves of
        # the same question and neither answers it alone.
        self.draws = DrawStore(config.evidence_path / "draws.json")
        self._load()

    # -- what is remembered between runs ----------------------------------

    @property
    def store_path(self) -> Path:
        return self.config.evidence_path / "routes.json"

    def _load(self) -> None:
        """Read back what was measured last time, discarding what no longer applies."""
        try:
            body = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for name, saved in (body.get("routes") or {}).items():
            route = self.config.routes.get(name)
            if route is None:
                continue
            if saved.get("fingerprint") != route_fingerprint(route):
                # The route was reconfigured since. The old answer describes a
                # command that is no longer what runs.
                continue
            known = {f.name for f in fields(RouteStatus)}
            try:
                self._checked[name] = _as_refusal(RouteStatus(
                    **{k: v for k, v in saved.items() if k in known}))
            except TypeError:
                continue

    def _save(self) -> None:
        """Never fatal. A console that cannot remember is worse than one that
        cannot start, but only by a little -- and losing the agents screen
        because a directory is read-only would be worse than both."""
        body = {"routes": {
            name: {**status.as_dict(),
                   "fingerprint": route_fingerprint(self.config.routes[name])}
            for name, status in self._checked.items()
            if name in self.config.routes
        }}
        try:
            write_atomic(self.store_path, json.dumps(body, indent=2))
        except OSError:
            pass

    def gauge(self, name: str) -> list[WindowReading]:
        """This route's windows, brought up to date for free if they can be.

        A tool that keeps a session log has already written its windows there --
        on every call the factory made and on every call the human made at the
        same terminal. Reading that file costs nothing, so the number on screen
        can be current without a run and without spending a turn.

        The distinction the check button draws still holds: opening a screen is
        not consent to spend one. It is consent to read a file.
        """
        route = self.config.routes.get(name)
        if route is None:
            return self._meters.latest(name)
        windows, credits = read_from_disk(route)
        if windows or credits:
            self._meters.record(name, windows, credits)
        self._meters.record_limit(name, read_limit(route))
        return self._meters.latest(name)

    def gauge_is_current(self, name: str) -> bool:
        """Whether this route's windows describe the window that is running now.

        A reading whose reset has passed belongs to the window before last. It
        is not merely old, it is about a different thing -- so "we have a
        reading" and "we know the headroom" are separate questions and this
        answers the second.
        """
        route = self.config.routes.get(name)
        if route is None or not route.meter_windows_key:
            return True          # nothing to know; never a reason to spend
        windows = self._meters.latest(name)
        return bool(windows) and all(not w.stale for w in windows)

    async def gauge_live(self, name: str) -> list[WindowReading]:
        """This route's headroom, current -- reading it free if it can be read
        free, and spending one turn if that is the only way.

        The free read comes first and is usually enough: a tool that writes its
        windows into a session log has them there already, refreshed by the
        human's own use of the same plan. Where a tool reports them only inside
        a call, and the last call was long enough ago that the window has since
        reset, there is no number anywhere on this machine -- and a run
        scheduled against "we had 42% a while ago" is scheduled against nothing.

        One turn, on the shortest prompt in the system, at most once per window.
        Deliberately not on the polled endpoint: this is for the two moments
        that justify it, a human arriving at the board and a build about to be
        committed.
        """
        windows = self.gauge(name)
        route = self.config.routes.get(name)
        if route is None or not route.meter_windows_key:
            return windows
        if self.gauge_is_current(name):
            return windows
        # No early-out for a tool that writes its gauge down. That guard was
        # wrong: a session log holds the reading from the last time the tool
        # *ran*, so when its window resets and nobody has used it since, the
        # newest thing on disk describes a window that has ended. Probing does
        # add a reading there -- the probe makes the tool run, and running is
        # what writes a new one. The card that exposed this said "plan read just
        # now" and "reset since last read" on the same two lines.
        if route.kind != "cli" or not route.command or not route.enabled:
            return windows
        # At most one probe per hold, when the last one did not make the
        # reading current. "At most once per window" assumed a probe always
        # lands; when the reading it writes cannot be read back -- a log left
        # in a container, a tool that changed its format -- nothing stopped the
        # next visit spending another turn, and the next.
        last = self._probed.get(name)
        if last is not None and time.time() - last < PROBE_HOLD_S:
            return windows
        try:
            await self.refresh(name)
        except Exception:
            # A gauge is worth one turn and never worth a failure. The card and
            # the run both carry on with what was already known.
            self._probed[name] = time.time()
            return self._meters.latest(name)
        self.gauge(name)
        if self.gauge_is_current(name):
            self._probed.pop(name, None)
        else:
            self._probed[name] = time.time()
        return self._meters.latest(name)

    async def gauges_live(self) -> dict[str, Any]:
        """Every route's headroom, current. What a run is scheduled against."""
        for name in self.config.routes:
            await self.gauge_live(name)
        return self.gauges()

    async def plan_verdict(self, wanted: Sequence[str]) -> PlanVerdict:
        """Can these routes carry one more run, and if not, when could they.

        Answered from two measurements and no judgement: what each plan has left
        (`gauge_live`) and what a run has drawn from it before (`draws`). Neither
        is a threshold somebody picked -- the first is read from the plan and the
        second is observed, run over run.

        Silent on a route it has no basis to judge. Fewer than
        `min_observations` draws on record is an anecdote, and holding work back
        on an anecdote is worse than starting a run that might not finish. The
        same goes for a route whose gauge cannot be read: that is reported, and
        it is not a reason to wait.

        The binding route is the one with least room relative to what it needs,
        and the window that binds it is the one whose reset would unblock the
        run. Waiting is only offered when that reset is near enough to be a
        schedule rather than a postponement.
        """
        cfg = self.config.scheduling
        if not cfg.enabled:
            return PlanVerdict(reason="scheduling is switched off in the config")

        worst: PlanVerdict | None = None
        unknown: list[str] = []
        for name in wanted:
            route = self.config.routes.get(name)
            if route is None or not route.meter_windows_key:
                continue                      # no window bounds it; the guard does
            needed = self.draws.expected(name)
            if self.draws.observations(name) < cfg.min_observations:
                continue                      # no basis; see the docstring
            windows = await self.gauge_live(name)
            live = [w for w in windows if not w.stale]
            if not live:
                unknown.append(name)
                continue
            tight = max(live, key=lambda w: w.utilization)
            remaining = max(0.0, 1.0 - tight.utilization)
            if remaining >= needed * cfg.margin:
                continue
            candidate = PlanVerdict(
                verdict="wait", route=name, start_at=tight.resets_at,
                remaining=remaining, needed=needed, window=tight.label,
                reason=(f"{name} has {round(remaining * 100)}% of its {tight.label} "
                        f"window left and a run has drawn up to "
                        f"{round(needed * 100)}% of it"),
            )
            if worst is None or remaining - needed < worst.remaining - worst.needed:
                worst = candidate

        if worst is not None:
            # A reset far enough out is a fact to report, not a time to hold a
            # build until. Nobody wants a feature that quietly starts on Sunday.
            if not worst.start_at or worst.seconds_to_start > cfg.max_wait_s:
                worst.verdict = "unknown" if not worst.start_at else "go"
                worst.reason += (
                    "; its reset is too far off to wait for, so this is a warning "
                    "rather than a schedule")
            return worst
        if unknown:
            return PlanVerdict(
                verdict="unknown", route=unknown[0],
                reason=(f"{', '.join(unknown)} could not be read, so whether there is "
                        "room to finish is not known"),
            )
        return PlanVerdict(reason="every plan this run needs has room")

    def gauges(self) -> dict[str, Any]:
        """Every route's gauge and nothing else.

        Its own endpoint because `survey` spawns a `which` and a `--version` per
        route -- a second and a half of processes, which is fine to pay when a
        person opens the page and wrong to pay every twenty seconds to keep a
        bar honest. This reads two files and returns.
        """
        out: dict[str, Any] = {}
        for name in self.config.routes:
            out[name] = {
                "windows": [w.as_dict() for w in self.gauge(name)],
                "credits": self._meters.credits(name),
                "limit": self._meters.limit(name),
            }
        return out

    @staticmethod
    def _expire_refusal(status: RouteStatus) -> RouteStatus:
        """A refusal is something that happened, not something that is.

        Without this, a probe that catches a workspace out of credits writes
        that answer to disk, and the card reads NOT WORKING for hours -- beside
        two plan gauges from the same account reading 4% and 1%, and beside a
        run that has gone on fine through a fallback. Nothing asks again until
        a human presses Check, and the one who would press it is the one being
        told the harness is broken.

        Two things end it, and this is the whole list:

        * the hold runs out, after which "refused" is history rather than
          status, and the honest state is the one it was before anybody asked;
        * the tool's own log shows a later call that named no limit, which is
          the account itself saying the refusal is over -- a refill, or a window
          that reopened. That reading is free; see `meters.read_limit`.

        What never ends it is time alone deciding the account is *fine*. The
        state goes back to "nobody has asked", not to "connected", because
        nobody has asked.
        """
        newer = status.limit or {}
        answered_since = (
            bool(newer)
            and not newer.get("reached")
            and float(newer.get("observed_at") or 0.0) > status.checked_at
        )
        if answered_since:
            status.state, status.connected = "unknown", None
            status.retry_at = 0.0
            status.detail = (
                "It refused a call, and has answered one since without naming a limit. "
                "Check to be sure.")
            return status
        if status.retry_at and time.time() >= status.retry_at:
            status.state, status.connected = "unknown", None
            status.retry_at = 0.0
            status.detail = (
                "The account refused a call. The hold a run honours has run out, "
                "and nothing has asked it since.")
        return status

    async def survey(self) -> list[dict[str, Any]]:
        """Every route, with the free half of the answer filled in.

        Installation is measured on every call -- it is a `which` and it is how
        a card stops saying "not found" a second after the install finishes.
        The connection half comes from cache, or stays `None`, which the console
        draws as "not checked" rather than as a failure.
        """
        out: list[dict[str, Any]] = []
        for name, route in self.config.routes.items():
            offered = list(route.models) or self._models_in_use(name)
            # Stale readings are kept and flagged rather than dropped. A window
            # whose reset has passed cannot be shown as a number -- 42% of the
            # window before last is wrong in the direction that refuses work the
            # plan allows -- but removing it from the list says something worse:
            # a card showing one bar where the plan has two reads as an account
            # with one limit. Unknown is not absent, and the console draws the
            # difference.
            seen = [w.as_dict() for w in self.gauge(name)]
            balance = self._meters.credits(name)
            cached = self._checked.get(name)
            status = base_status(route)
            status.models = offered
            status.windows = seen
            status.credits = balance
            status.limit = self._meters.limit(name)
            if cached is not None:
                status = cached
                status.models = offered
                status.windows = seen
                status.credits = balance
                status.limit = self._meters.limit(name)
                # Recomputed, not remembered: all of these are functions of the
                # route's config, and a cache written before a config change
                # would go on describing the old one. See `ok_detail`.
                #
                # The labels are here for a reason worth keeping: named after a
                # stored check already existed, they came back empty from the
                # cache while the route beside them -- checked since -- showed
                # its own. One card in the row had no name for the account it
                # spends, which is the failure this split was built to end.
                status.gauge_self_reads = bool(route.meter_sidecar_glob)
                fresh = base_status(route)
                status.harness_label = fresh.harness_label
                status.account_label = fresh.account_label
                status.account_kind = fresh.account_kind
                status.label = fresh.label
                if status.state == "ok":
                    status.detail = ok_detail(route)
                # Re-measure the cheap half so an install lands immediately.
                status.installed, status.version = await check_installed(route)
                if status.installed and status.state == "missing":
                    # It arrived since the last check. Do not keep saying it did not.
                    status.state, status.connected = "unknown", None
                    status.detail = "Installed since the last check. Check the connection."
                if status.state == "limited":
                    status = self._expire_refusal(status)
            elif route.kind != "cli":
                # An api route's credential is a fact on disk, not a question
                # for the network. Saying "not checked" about something we can
                # see would put an amber lamp on the one route that has been
                # working all along.
                status.installed = True
                status.connected = bool(route.api_key)
                status.state = "ok" if route.api_key else "unauthenticated"
                status.detail = ("A key is stored for this route."
                                 if route.api_key
                                 else "No key is stored. Set one in provider settings.")
            else:
                status.installed, status.version = await check_installed(route)
                if status.installed is False:
                    status.state = "missing"
                    status.connected = False
                    status.detail = (
                        "Not installed."
                        + (f" Install it with: {route.install_command}"
                           if route.install_command else ""))
                else:
                    status.state = "unknown"
                    status.detail = "Not checked yet."
            out.append(status.as_dict())
        return out

    def _models_in_use(self, route_name: str) -> list[str]:
        """What to offer for a route that lists nothing of its own.

        An api route reaches hundreds of models and no curated list would be
        right, so the honest offer is the ones this project already uses. Without
        it the picker is a one-way door: a role moved onto a subscription could
        never be moved back, because the model it ran on before appears in no
        list anywhere.
        """
        default = self.config.default_route
        return sorted({
            role.model for role in self.config.roles.values()
            if (role.route or default) == route_name and role.model
        })

    async def refresh(self, name: str) -> dict[str, Any]:
        """Check one route for real, and remember the answer past this process."""
        route = self.config.route(name)
        status = await check_connection(route, self.llm)
        self._checked[name] = status
        self._save()
        return status.as_dict()

    def forget(self, name: str = "") -> None:
        """Drop a cached check, so the next survey is honest about not knowing.

        Called when the config is rewritten: a route whose command changed has
        not been checked, whatever the last answer said about the old one.
        """
        if name:
            self._checked.pop(name, None)
        else:
            self._checked.clear()
        self._save()
