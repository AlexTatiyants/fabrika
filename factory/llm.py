"""One entry point to every model in the system: `ask(role, prompt, schema)`.

Plain OpenAI-compatible `/chat/completions` over httpx. No vendor SDK, no
per-provider branching, no model name anywhere in this file. (INV-7)

Structured output is requested *and* validated. Hosted providers vary in how
reliably they honour a response format, so validation is not optional even when
you ask nicely. (INV-5)
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from . import procs
from . import isolation
from .config import Config, ConfigError, RoleConfig, RouteConfig
from .agentbox import completion_box
from .isolation import IsolationError

T = TypeVar("T", bound=BaseModel)

SCHEMA_ATTEMPTS = 3

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)

# Keywords the strict structured-output subset does not accept. Pydantic emits
# them from Field constraints; we strip them from the wire schema and keep
# enforcing them locally on the way back in.
_UNSUPPORTED = (
    "default", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minLength", "maxLength", "pattern", "minItems", "maxItems", "format",
    "examples", "$comment",
)


# Where a call is written down as it happens, per feature and per phase.
#
# One client serves the whole server, so it cannot know which feature a call
# belongs to. The phase that is running does, so it sets this for the length of
# the phase; every call made inside it -- including from tasks it starts, which
# inherit the context -- lands in that feature's ledger, in the order it
# happened. A call made outside any phase goes nowhere, rather than into the
# wrong feature.
_CALL_JOURNAL: contextvars.ContextVar[Callable[[dict[str, Any]], None] | None] = \
    contextvars.ContextVar("factory_call_journal", default=None)


def journal_calls(write: Callable[[dict[str, Any]], None] | None):
    """Point this task's calls at `write`. Returns the token to reset with."""
    return _CALL_JOURNAL.set(write)


def stop_journaling(token) -> None:
    _CALL_JOURNAL.reset(token)


def journal_call(entry: dict[str, Any]) -> None:
    """Write one call down, if something is listening. Never raises: a ledger
    that cannot be written is not a reason to lose the answer it describes."""
    write = _CALL_JOURNAL.get()
    if write is None:
        return
    try:
        write(entry)
    except Exception:
        pass


def route_tokens(envelope: dict[str, Any], route: Any) -> tuple[int, int]:
    """Input and output tokens as this route reports them, cache included.

    The one place a route's envelope is read for tokens: a completion, a
    session's final report and the session's spend all go through here, so a
    tool that splits its input across fields is counted whole everywhere.
    """
    prompt = _as_float(_dig(envelope, getattr(route, "prompt_tokens_key", "")))
    for key in getattr(route, "prompt_tokens_extra_keys", None) or []:
        prompt += _as_float(_dig(envelope, key))
    completion = _as_float(_dig(envelope, getattr(route, "completion_tokens_key", "")))
    return int(prompt), int(completion)


def call_entry(
    *, role: str, route: str, asked: str, outcome: str, started: float,
    kind: str = "completion", response: dict[str, Any] | None = None,
    reason: str = "", fallback_from: str = "", detail: str = "", unit: str = "",
) -> dict[str, Any]:
    """One model call as it happened: who asked what of whom, and what came back.

    Read top to bottom these are the run, and nothing about them has to be
    inferred. A refused call is a row of its own -- which is the point: the
    alternative was a phase wearing the badge of a model that refused every
    call made to it, beside a substitute that did the work unannounced.
    """
    response = response or {}
    usage = response.get("usage") or {}
    meta = response.get("_route") or {}
    answered = (str(meta.get("resolved_model") or "").strip()
                or str(response.get("model") or "").strip()
                or (asked if outcome == "answered" else ""))
    # The same split `_account` makes, made here too, because this row is
    # written from the envelope and not from the tally. A subscription CLI
    # reports what the work would have cost on an API, and `claude -p` reports
    # it per call while `codex` reports nothing -- so a ledger that took the
    # figure at face value put dollars beside every Claude turn, none beside
    # any Codex one, and read as though one vendor were billing and the other
    # were free. Both were turns against a plan. The number is kept, under a
    # name that says what it is.
    billed = bool(meta.get("billed", True))
    reported = _as_float(usage.get("cost"))
    return {
        "kind": kind,
        "role": role,
        "route": route,
        "asked": asked,
        "answered": answered,
        "outcome": outcome,
        "reason": reason[:300],
        # What the route itself said, verbatim, for whoever opens the row. The
        # reason above is the sentence a person reads; this is the evidence.
        "detail": detail[-2000:],
        "fallback_from": fallback_from,
        # Which unit of a step's parallel work this call was for -- a build
        # unit, a repair unit -- so the run can be drawn as the branches it was.
        "unit": unit,
        "started_at": started,
        "duration_s": round(max(0.0, time.time() - started), 2),
        "prompt_tokens": int(_as_float(usage.get("prompt_tokens"))),
        "completion_tokens": int(_as_float(usage.get("completion_tokens"))),
        "billed": billed,
        "cost_usd": reported if billed else 0.0,
        "notional_usd": 0.0 if billed else reported,
    }


class LLMError(RuntimeError):
    pass


#: How long a route that refused for want of credit is left alone when it gave
#: no time of its own. Long enough that a run stops asking it for every call;
#: short enough that a balance topped up mid-run is noticed within the run.
SPENT_HOLD_S = 1800.0


def refusal_words(text: str) -> str:
    """A refusal as a person would say it: out of credits, or at a limit."""
    tail = (text or "")[-6000:]
    if _EXHAUSTED_SIGNS.search(tail):
        return "out of credits"
    if _RATE_LIMIT_SIGNS.search(tail):
        return "at its usage limit"
    return "refused"


class RouteExhausted(LLMError):
    """The other way a subscription route says no.

    A window is one ceiling and a balance is another, and they are not the same
    fact: one reopens on a clock the tool will tell you, the other reopens when
    somebody pays. `RateLimited` is the first. This is the second, and it was
    arriving as a generic failure -- "route 'codex' reported a failure: Your
    workspace is out of credits" -- which reads as a broken harness and sends a
    human to the config, the credentials and the code, none of which is wrong.
    """


class _SkipSpent(Exception):
    """Internal: the primary route is known to be spent, so it is not asked."""

    def __init__(self, held: dict[str, Any]) -> None:
        super().__init__(held.get("why", ""))
        self.held = held


class RateLimited(LLMError):
    """The plan's window, reached.

    Its own exception because it is the one stopping condition a subscription
    run actually has, and it is not a failure of anything. `budget_usd` cannot
    see a plan, so when a run on one stops it stops here -- and read as a broken
    harness it sends a human to the config, the credentials and the code, none
    of which is wrong. Waiting is the fix, and nothing else is.
    """

    def __init__(self, message: str, route: str = "", retry_after_s: float = 0.0) -> None:
        super().__init__(message)
        self.route = route
        self.retry_after_s = retry_after_s


#: A plan's window, as each tool words it. Matched against the tool's own
#: message, which is the only place it exists -- these CLIs exit 0 on a rate
#: limit and put the reason in the body, exactly as they do for auth.
# A balance, not a window. Kept apart from the rate-limit signs because the
# remedy is different -- waiting fixes one and only paying fixes the other --
# and a fallback route is the answer to both.
_EXHAUSTED_SIGNS = re.compile(
    r"(out of credits|insufficient (?:credit|balance|funds|quota)|"
    r"no credits|credit balance|payment required|\b402\b)",
    re.IGNORECASE,
)

# `429` only where a tool is saying it to somebody -- "API Error: 429", "(429)"
# -- and not where it is a number under a key. A harness log is a stream of
# numbers, and a plain `\b429\b` reads `"input_tokens": 429` as a refusal,
# which on the session path is enough to bench a working route for half an
# hour. The two lookbehinds are the two spellings JSON has for it; a colon in
# prose ("Error: 429") is not preceded by the closing quote of a key.
_RATE_LIMIT_SIGNS = re.compile(
    r"(rate[ _-]?limit|(?<![\w.:\"])(?<!\":\s)429\b|too many requests|quota|"
    r"usage limit|retry[- _]?after|resets? at|try again (?:in|later))",
    re.IGNORECASE,
)


def _windows_full(envelope: dict[str, Any], route: Any) -> tuple[str, float] | None:
    """A window this response says is used up, and the seconds until it
    reopens -- or None."""
    try:
        from .meters import read_windows

        for w in read_windows(envelope, route):
            if w.utilization >= 1.0:
                return w.label, max(0.0, (w.resets_at or 0.0) - time.time())
    except Exception:
        return None
    return None


def _retry_after(text: str) -> float:
    """Seconds until the window reopens, if the tool said."""
    match = re.search(
        r"(?:retry[- _]?after|in)\s+(\d+(?:\.\d+)?)\s*"
        r"(s\b|sec|second|m\b|min|minute|h\b|hour)",
        text, re.IGNORECASE)
    if not match:
        return 0.0
    value = float(match.group(1))
    unit = match.group(2).lower()
    return value * (3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1)


@dataclass
class RoleUsage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    models: set[str] = field(default_factory=set)
    # Which routes carried this role, and how many turns went through the ones
    # that do not report dollars. Without these a subscription route books
    # `cost: 0.0` and is indistinguishable in the ledger from an agent that did
    # not run -- so a reader sees $0.00 and concludes the work was free, when
    # what actually happened is that nothing here could be priced.
    routes: set[str] = field(default_factory=set)
    turns: int = 0
    unbilled_calls: int = 0
    # What an unbilled route said the same work would have cost on an API. Real
    # information, and not money: on a subscription nobody is charged it. Kept
    # apart from `cost` because `cost` is what the budget guard reads, and a run
    # stopped at `reserve_usd` for spend that never happened is a run stopped
    # for nothing.
    notional_cost: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost": round(self.cost, 6),
            "models": sorted(self.models),
            "routes": sorted(self.routes),
            "turns": self.turns,
            "unbilled_calls": self.unbilled_calls,
            "notional_cost": round(self.notional_cost, 6),
        }


# --------------------------------------------------------------------------
# JSON recovery
# --------------------------------------------------------------------------


def extract_json(text: str) -> str:
    """AC-3.3 -- strip fences, take the outermost JSON object.

    Providers wrap output in prose or fences despite the response format. This
    is recovery, not parsing: if there is no balanced object we hand the raw
    text back and let validation produce a useful error.
    """
    if not text:
        return text
    body = text.strip()
    m = _FENCE.search(body)
    if m:
        body = m.group(1).strip()
    start = body.find("{")
    if start == -1:
        return body
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(body)):
        ch = body[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return body[start : i + 1]
    return body[start:]


#: How many bytes of argv a route may be launched with. Well under every
#: platform's real limit, because the failure past it is `OSError: [Errno 7]`
#: and nothing in it says which field was too long.
_ARGV_BUDGET = 200_000


def _dig(envelope: Any, path: str) -> Any:
    """A dotted path into a CLI's JSON envelope, or None.

    Every one of these tools names its fields differently and none of them is
    wrong, so where the answer lives is configuration rather than a branch per
    vendor. (INV-7: no provider-specific code in the package.)
    """
    if not path:
        return None
    node = envelope
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _merge_jsonl(stdout: str) -> dict[str, Any]:
    """An event stream read down into one envelope, last write wins.

    `codex exec --json` prints one JSON object per line -- thread started,
    turn started, each item as it completes, turn completed or failed -- with
    no single object holding the whole answer. Folding the events into one
    dict by top-level key lets the same dotted-path `result_key`/`error_key`
    machinery read it: the final `item.completed` overwrites `item` with the
    answer, and a `turn.failed` overwrites `error` with the reason. A line
    that is not a JSON object (a stray banner, a blank line) is skipped rather
    than failing the whole read -- this is recovery, matching `extract_json`.
    """
    envelope: dict[str, Any] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            envelope.update(event)
    return envelope


def principal_model(usage: Any) -> str:
    """The model that did the work, from a tool's per-model usage map.

    One of these tools reports every model it touched in a call, and it touches
    a small helper model for housekeeping on every one -- so joining the keys
    put two model names on a phase one of them did, the helper first because
    it sorted first, and a reader had to know which half to ignore. The one
    that cost the most, then wrote the most, is the one that answered.
    """
    if not isinstance(usage, dict) or not usage:
        return str(usage or "")
    def weight(item: tuple[str, Any]) -> tuple[float, float, float]:
        stats = item[1] if isinstance(item[1], dict) else {}
        return (
            _as_float(stats.get("costUSD") or stats.get("cost_usd")),
            _as_float(stats.get("outputTokens") or stats.get("output_tokens")),
            _as_float(stats.get("inputTokens") or stats.get("input_tokens")),
        )
    return max(usage.items(), key=weight)[0]


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def cli_argv(route: RouteConfig, model: str, system: str, schema_text: str,
             schema_file: str = "") -> list[str]:
    """The command line one completion is launched with.

    Its own function because three separate things have to be true about it and
    each of them has been wrong somewhere: the placeholders are substituted, a
    flag whose value came back empty is dropped rather than passed empty, and
    the whole thing fits in the argument space the platform will actually give.
    """
    subs = {"model": model, "system": system, "schema": schema_text,
            "schema_file": schema_file}
    argv: list[str] = []
    for token in route.command:
        for key, value in subs.items():
            token = token.replace("{" + key + "}", value)
        argv.append(token)
    argv = _drop_empty_pairs(argv, route.command)

    # A system prompt plus a JSON schema runs to tens of kilobytes, and the
    # failure past the limit is `OSError: [Errno 7]` -- which names neither the
    # role nor the field that overran, and looks like the binary is missing.
    size = sum(len(a.encode()) for a in argv)
    if size > _ARGV_BUDGET:
        raise LLMError(
            f"route {route.name!r} would be launched with {size:,} bytes of arguments, "
            f"over the {_ARGV_BUDGET:,} this platform can be relied on for. The system "
            "prompt and JSON schema are passed on the command line; shorten one, or set "
            "`schema_in_prompt: true` on this route to move the schema into the prompt."
        )
    return argv


def _drop_empty_pairs(argv: list[str], template: list[str]) -> list[str]:
    """Remove a flag whose value was a placeholder that resolved to nothing.

    `--json-schema ''` is not the same as omitting the flag: a CLI given an
    empty schema may reject it outright. A token that was purely a placeholder
    and came back empty takes the flag in front of it with it.
    """
    out: list[str] = []
    skip = False
    for i, value in enumerate(argv):
        if skip:
            skip = False
            continue
        raw = template[i] if i < len(template) else value
        nxt = template[i + 1] if i + 1 < len(template) else ""
        placeholder = nxt.startswith("{") and nxt.endswith("}")
        if (raw.startswith("-") and placeholder
                and i + 1 < len(argv) and not argv[i + 1]):
            skip = True
            continue
        out.append(value)
    return out


def strict_schema(model: Type[BaseModel]) -> dict[str, Any]:
    """Pydantic's JSON schema, hardened into the strict structured-output subset."""
    schema = model.model_json_schema()

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        for key in _UNSUPPORTED:
            node.pop(key, None)
        if "properties" in node and isinstance(node["properties"], dict):
            node["type"] = "object"
            node["additionalProperties"] = False
            node["required"] = list(node["properties"].keys())
        for value in list(node.values()):
            walk(value)

    walk(schema)
    return schema


# --------------------------------------------------------------------------
# the client
# --------------------------------------------------------------------------


class LLM:
    def __init__(self, config: Config, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        from .meters import use_mirror

        # Where a sealed call's session logs are copied to, so the gauge that
        # reads them after the call finds them. See `agentbox.collect_sidecars`.
        if getattr(config, "evidence_path", None) is not None:
            use_mirror(config.evidence_path)
        self._client = client
        self._owns_client = client is None
        self.usage: dict[str, RoleUsage] = {}
        # Every role that ran somewhere other than where it was configured, and
        # why. A packet says which agents read the work; if one of them read it
        # from a substitute, that is a different reading and the verdict is
        # worth something different for it.
        self.fallbacks: list[dict[str, Any]] = []
        # Routes that refused for want of credit or at a usage limit, and until
        # when to leave them alone. Without this a run asks a route that is out
        # of credits again and again -- every agent, every round, each refusal a
        # few seconds and a row in the log (forty, in one case) -- because
        # nothing remembers the first.
        self.spent_routes: dict[str, dict[str, Any]] = {}
        # The model that last answered for each role -- not the one configured
        # for it. The two part company exactly when it matters: a role whose
        # route ran out goes to its substitute, and every record stamped with
        # the configured name then credits the work to a model that did none of
        # it. Measured: an oracle's five test files were written by a flash
        # model on the fallback route after two sessions on the configured one
        # made zero tool calls, and the console named the configured one.
        self.answered: dict[str, str] = {}
        # What each individual exchange cost, waiting to be written onto the
        # record of that exchange. See `take_usage`.
        self._exchanges: dict[tuple[str, str], list[dict[str, Any]]] = {}

    # -- lifecycle --------------------------------------------------------

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            api = self.config.api
            headers = {"Content-Type": "application/json", **api.headers}
            if api.api_key:
                headers["Authorization"] = f"Bearer {api.api_key}"
            self._client = httpx.AsyncClient(
                base_url=api.base_url.rstrip("/"),
                headers=headers,
                timeout=httpx.Timeout(api.timeout_s),
            )
        return self._client

    async def reset_client(self) -> None:
        """Drop the cached client so a changed key or base_url takes effect.

        The Authorization header is baked in when the client is first built, so
        without this a key set in the console would be silently ignored until a
        restart -- the worst kind of "saved" .
        """
        if self._client is not None and self._owns_client:
            try:
                await self._client.aclose()
            except Exception:
                pass
        if self._owns_client:
            self._client = None

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    # -- transport --------------------------------------------------------

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """AC-3.5 -- transport and 429/5xx retries with exponential backoff.

        The single HTTP seam in the codebase. Tests replace this method.
        """
        api = self.config.api
        last: Exception | None = None
        for attempt in range(api.max_transport_retries):
            try:
                resp = await self.client.post("/chat/completions", json=payload)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last = exc
            else:
                if resp.status_code == 429 or resp.status_code >= 500:
                    last = LLMError(f"HTTP {resp.status_code}: {resp.text[:400]}")
                elif resp.status_code in (401, 403) and not api.api_key:
                    # The provider's own words for this are about cookies or
                    # sessions, and they are the first thing a new install shows.
                    # The status stays in front: route health reads it.
                    raise LLMError(
                        f"HTTP {resp.status_code}: no provider key is set, and the provider "
                        "wants one. Paste it into the Provider card on the crew page, or set "
                        "the environment variable factory.yaml names for it. "
                        f"The provider said: {resp.text[:300]}")
                elif resp.status_code >= 400:
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:1000]}")
                else:
                    return resp.json()
            if attempt < api.max_transport_retries - 1:
                await asyncio.sleep(api.backoff_base_s * (2 ** attempt))
        raise LLMError(f"transport failed after {api.max_transport_retries} attempts: {last}")

    # -- request assembly -------------------------------------------------

    def _payload(
        self,
        role: RoleConfig,
        messages: list[dict[str, str]],
        schema: Type[BaseModel] | None,
        temperature: float | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": role.model,
            "messages": messages,
            "temperature": role.temperature if temperature is None else temperature,
            "max_tokens": role.max_tokens,
        }
        if role.reasoning_effort and role.reasoning_effort.lower() not in ("", "none"):
            payload["reasoning"] = {"effort": role.reasoning_effort}
        if role.providers:
            # AC-3.4 -- without pinning, quantisation and context window differ
            # between hosts of the same model, and an eval measures the host.
            payload["provider"] = {
                "order": list(role.providers),
                "allow_fallbacks": role.allow_fallbacks,
            }
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": strict_schema(schema),
                },
            }
        return payload

    # -- routes -----------------------------------------------------------

    def mark_spent(self, route: str, why: str, retry_after_s: float = 0.0,
                   detail: str = "") -> None:
        """Remember that a route refused for want of credit, until it may be asked again."""
        routes = self.__dict__.setdefault("spent_routes", {})
        now = time.time()
        held = routes.get(route) or {}
        routes[route] = {
            "why": why,
            "since": held.get("since") or now,
            "until": now + (retry_after_s if retry_after_s > 0 else SPENT_HOLD_S),
            "detail": detail[-500:],
        }

    def spent(self, route: str) -> dict[str, Any] | None:
        """Why this route is being left alone, or None if it may be asked."""
        routes = self.__dict__.setdefault("spent_routes", {})
        held = routes.get(route)
        if held and held["until"] > time.time():
            return held
        routes.pop(route, None)
        return None

    async def _complete(
        self, role: RoleConfig, role_name: str, messages: list[dict[str, str]],
        schema: Type[BaseModel] | None, temperature: float | None,
    ) -> dict[str, Any]:
        """One completion, wherever this role's route sends it.

        Both branches return the same shape -- an OpenAI-style response dict --
        which is the whole trick. `ask` above keeps one repair loop, `_content`
        keeps one extractor and `_account` keeps one ledger, and none of them
        has to learn that a subscription CLI exists.
        """
        primary = self.config.route_for(role_name)
        started = time.time()
        # A route known to be out of credit is not asked again until it may
        # have come back; the call goes straight to the substitute and says why.
        held = self.spent(primary.name)
        substitute = self._fallback_for(role, role_name) if held else None
        try:
            if substitute is not None:
                raise _SkipSpent(held)
            response = await self._on_route(
                primary, role, role_name, messages, schema,
                temperature, self.config.route_problem(role_name, "complete"))
            journal_call(call_entry(
                role=role_name, route=primary.name, asked=role.model,
                outcome="answered", started=started, response=response))
            return response
        except (RateLimited, RouteExhausted, _SkipSpent) as exc:
            if isinstance(exc, _SkipSpent):
                why = f"{exc.held['why']} since {time.strftime('%H:%M', time.localtime(exc.held['since']))}"
            else:
                why = "out of credits" if isinstance(exc, RouteExhausted) else "at its usage limit"
                self.mark_spent(primary.name, why,
                                getattr(exc, "retry_after_s", 0.0) or 0.0, str(exc))
                journal_call(call_entry(
                    role=role_name, route=primary.name, asked=role.model,
                    outcome="refused", started=started, reason=why, detail=str(exc)))
                substitute = self._fallback_for(role, role_name)
            if substitute is None:
                raise
            route, stand_in = substitute
            # Said once, where a human reads it, and carried into the packet by
            # `route_fallbacks`. A panel that ran somewhere other than where it
            # was configured is a different reading from the one the verdict
            # claims, and the one thing that must not happen is that it becomes
            # true quietly.
            self.fallbacks.append({
                "role": role_name,
                "from": self.config.route_for(role_name).name,
                "to": route.name,
                "model": stand_in.model,
                "because": str(exc)[:300],
            })
            started = time.time()
            try:
                response = await self._on_route(
                    route, stand_in, role_name, messages, schema, temperature,
                    self.config.route_problem_for(route, stand_in, role_name, "complete"))
            except Exception as failed:
                journal_call(call_entry(
                    role=role_name, route=route.name, asked=stand_in.model,
                    outcome="failed", started=started, reason=str(failed),
                    fallback_from=primary.name))
                raise
            journal_call(call_entry(
                role=role_name, route=route.name, asked=stand_in.model,
                outcome="answered", started=started, response=response,
                fallback_from=primary.name, reason=f"{primary.name} {why}"))
            return response
        except LLMError as exc:
            journal_call(call_entry(
                role=role_name, route=primary.name, asked=role.model,
                outcome="failed", started=started, reason=str(exc)))
            raise

    def _fallback_for(
        self, role: RoleConfig, role_name: str,
    ) -> tuple[RouteConfig, RoleConfig] | None:
        """Where this role goes when its own route will not carry it.

        `None` unless the role names one, which is the default: a substitute
        reviewer is a weaker reading than the one configured, and that is a
        choice to make rather than to inherit.
        """
        if not role.fallback_route:
            return None
        try:
            route = self.config.route(role.fallback_route)
        except ConfigError:
            return None
        stand_in = role.model_copy(update={
            "route": route.name,
            "model": role.fallback_model or role.model,
            # Not the fallback's fallback. One hop, or a route that is down in
            # a cycle with another takes a run round it until something times
            # out.
            "fallback_route": "",
        })
        if self.config.route_problem_for(route, stand_in, role_name, "complete"):
            return None
        return route, stand_in

    async def _on_route(
        self, route: RouteConfig, role: RoleConfig, role_name: str,
        messages: list[dict[str, str]], schema: Type[BaseModel] | None,
        temperature: float | None, problem: str,
    ) -> dict[str, Any]:
        if problem:
            raise LLMError(problem)
        if route.kind == "cli":
            response = await self._run_cli(route, role, messages, schema)
        else:
            response = await self._post(self._payload(role, messages, schema, temperature))
        # The model this answer was asked of, carried with it. `ask` accounts
        # for a response against the role it was given, and on a fallback that
        # is the configured role -- not the stand-in the request actually went
        # out as. So a review panel that ran entirely on the substitute was
        # booked, and stamped, and shown, as the model that refused it.
        if isinstance(response, dict):
            response["_asked"] = role.model
        return response

    async def _communicate(self, route: RouteConfig, argv: Sequence[str], body: str,
                           env: dict[str, str], timeout_s: float = 0.0,
                           ) -> tuple[int | None, bytes, bytes]:
        """Start one completion process, hand it the prompt, and read it out."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
            )
        except OSError as exc:
            raise LLMError(
                f"route {route.name!r} could not start {argv[0]!r}: {exc}") from exc
        limit = timeout_s or route.timeout_s
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(body.encode()), timeout=limit)
        except asyncio.TimeoutError:
            # The client, here. The process it started goes with its container,
            # which `completion_box` removes on the way out.
            await procs.kill(proc)
            raise LLMError(
                f"route {route.name!r} did not answer within {limit:g}s") from None
        return proc.returncode, out, err

    async def _run_cli(
        self, route: RouteConfig, role: RoleConfig, messages: list[dict[str, str]],
        schema: Type[BaseModel] | None,
    ) -> dict[str, Any]:
        """A structured answer out of a command-line harness.

        The conversation is flattened rather than resumed. Every one of these
        CLIs has some way to continue a session and no two spell it alike -- a
        session id here, an index there -- so the repair loop hands over the
        whole exchange as text instead. It costs input tokens on the second
        attempt and it works the same on all of them, which is the better trade
        while the third one is not even installed.
        """
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        body = "\n\n".join(
            (m["content"] if m["role"] == "user" else f"[your previous answer]\n{m['content']}")
            for m in messages if m["role"] != "system"
        )
        schema_text = ""
        if schema is not None:
            schema_text = json.dumps(strict_schema(schema))
            if route.schema_in_prompt:
                # No flag on this CLI to hold the answer to a shape, so it goes
                # where it can go. The repair loop is what catches the rest.
                body += (
                    "\n\n---\n\nReturn raw JSON only -- no prose, no code fences -- "
                    "matching this schema exactly:\n\n" + schema_text
                )
                schema_text = ""

        # A schema on a path rather than in argv, where the tool takes one.
        # Strictly better: no argument-size ceiling to reason about, and the
        # file is there to read when a run has to be explained afterwards.
        schema_path = ""
        if schema_text and any("{schema_file}" in t for t in route.command):
            handle = tempfile.NamedTemporaryFile(
                "w", suffix=".schema.json", delete=False, encoding="utf-8")
            with handle:
                handle.write(schema_text)
            schema_path = handle.name
            schema_text = ""
        routed = dict(route.env)
        if route.key_env and route.api_key:
            routed[route.key_env] = route.api_key
        # The role's reasoning level, said the way this harness hears it.
        effort = route.effort_for(role.reasoning_effort) if role.reasoning_effort else None
        if effort is not None:
            routed.update(effort.env)
        try:
            if isolation.TEST_SUITE_ON_HOST:
                # The test suite's fake CLIs, which are local scripts. Nothing
                # else takes this branch; see `isolation.py`.
                argv = cli_argv(route, role.model, system, schema_text, schema_path) + (effort.args if effort else [])
                returncode, out, err = await self._communicate(
                    route, argv, body, {**os.environ, **routed}, role.timeout_s)
            else:
                # In a sealed container of its own, never here: see `agentbox`.
                try:
                    async with completion_box(route, self.config, routed) as box:
                        inside = (await box.put(schema_path, "factory-schema.json")
                                  if schema_path else "")
                        argv = box.exec_argv(
                            cli_argv(route, role.model, system, schema_text, inside) + (effort.args if effort else []))
                        returncode, out, err = await self._communicate(
                            route, argv, body, dict(os.environ), role.timeout_s)
                except IsolationError as exc:
                    raise LLMError(f"route {route.name!r}: {exc}") from None
        finally:
            if schema_path:
                try:
                    os.unlink(schema_path)
                except OSError:
                    pass
        stdout = (out or b"").decode("utf-8", errors="replace")
        stderr = (err or b"").decode("utf-8", errors="replace")
        if route.stdout_format == "jsonl":
            envelope = _merge_jsonl(stdout)
        else:
            try:
                envelope = json.loads(stdout.strip() or "{}")
            except json.JSONDecodeError:
                raise LLMError(
                    f"route {route.name!r} did not return JSON. Exit {returncode}. "
                    f"stdout: {stdout.strip()[-1200:] or '(empty)'} "
                    f"stderr: {stderr.strip()[-600:]}"
                ) from None

        # Before anything below can refuse: a call that was rejected still
        # reported the state of the window, and that reading is the whole point
        # of asking. Discarding it on the failure path would lose exactly the
        # measurement taken closest to the limit.
        self._note_windows(route, envelope)

        key = (route.schema_result_key if schema is not None and route.schema_result_key
               else route.result_key)
        answer = _dig(envelope, key)
        # Already an object where the tool parsed it for us. Re-encoded rather
        # than special-cased downstream, so validation and the repair loop stay
        # one path whatever the route did.
        text = answer if isinstance(answer, str) else (
            json.dumps(answer) if answer is not None else "")
        # The exit code is not the answer. A `claude -p` whose token had expired
        # returned exit 0 with a 401 in the body and `is_error: true` beside it,
        # and read as a successful completion of the string "API Error: 401".
        error_detail = _dig(envelope, route.error_key) if route.error_key else None
        failed = bool(error_detail)
        if failed or (returncode not in (0, None) and not text):
            # Claude's envelope happens to put the failure text in the same
            # field as a real answer (`result`), so for it `text` alone would
            # be enough. Codex's does not -- the reason lives under `error_key`
            # and `text` comes back empty on a failure -- so read that field
            # too rather than falling straight to stderr, which a CLI that
            # reports its errors as JSON on stdout leaves empty.
            if isinstance(error_detail, dict):
                error_detail = error_detail.get("message") or json.dumps(error_detail)
            # A schema call reads its answer from the schema's key, which a
            # refusal leaves empty -- the refusal's words are in the plain
            # result beside it. Without them the whole reason was `True`.
            prose = _dig(envelope, route.result_key) if schema is not None and route.result_key else None
            prose = prose if isinstance(prose, str) else ""
            detail = (text or prose or (str(error_detail) if isinstance(error_detail, str) else "")
                      or stderr or (str(error_detail) if error_detail else ""))
            full = _windows_full(envelope, route)
            if full is not None and not _EXHAUSTED_SIGNS.search(detail):
                # The tool's own gauge says a window is used up: that is the
                # refusal, whatever words did or did not come with it.
                raise RateLimited(
                    f"route {route.name!r} has reached its plan's limit ({full[0]} window full)"
                    + (f": {detail[:600]}" if detail and detail != "True" else ""),
                    route=route.name, retry_after_s=full[1])
            if _RATE_LIMIT_SIGNS.search(detail):
                # Not a broken route. The one ceiling a plan actually has, and
                # the only remedy is time -- so it is raised as itself rather
                # than as "the harness failed", which sends a human to the
                # config, the credentials and the code, none of which is wrong.
                raise RateLimited(
                    f"route {route.name!r} has reached its plan's limit: {detail[:800]}",
                    route=route.name, retry_after_s=_retry_after(detail))
            if _EXHAUSTED_SIGNS.search(detail):
                raise RouteExhausted(
                    f"route {route.name!r} has no credit left: {detail[:800]}")
            raise LLMError(
                f"route {route.name!r} reported a failure: {detail[:800]}")

        cost = _as_float(_dig(envelope, route.cost_key))
        # Which model actually answered. It is not always the one that was
        # asked for: a short alias resolves to whatever this tool's *version*
        # points at, which on a real machine was a generation behind the newest
        # model the same API would serve -- and a role that names no model
        # asked for nothing at all. Read back rather than assumed, so the ledger
        # and the packet record what ran instead of what was configured.
        # (Same principle as INV-3.)
        resolved = _dig(envelope, route.resolved_model_key)
        if isinstance(resolved, dict):
            resolved = principal_model(resolved)
        return {
            "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": dict(zip(("prompt_tokens", "completion_tokens"), route_tokens(envelope, route)),
                          cost=cost),
            # Not part of the API shape, and read only by `_account`.
            "_route": {
                "name": route.name,
                "billed": route.billed,
                "turns": int(_as_float(_dig(envelope, route.turns_key)) or 1),
                "resolved_model": str(resolved or ""),
            },
        }

    def _account(self, role_name: str, model: str, response: dict[str, Any]) -> dict[str, Any]:
        """AC-3.6 -- token usage per role, for the cost breakdown.

        Returns this one response's numbers as well as summing them, because the
        per-role total cannot answer "what did *this* call cost" and the ledger
        is written one call at a time.
        """
        u = self.usage.setdefault(role_name, RoleUsage())
        u.calls += 1
        # The model that answered, where the route could tell us, and otherwise
        # the one that was asked for. A role that names no model would
        # otherwise book an empty string, and the packet would say a phase ran
        # on nothing.
        # Most specific first: what the tool says answered, then what the
        # provider says answered, then what the request actually asked for --
        # which on a fallback is the stand-in, not the role's own model.
        ran = (str((response.get("_route") or {}).get("resolved_model") or "").strip()
               or str(response.get("model") or "").strip()
               or str(response.get("_asked") or "").strip())
        u.models.add(ran or model or "(the route's own default)")
        if ran or model:
            self._note_answered(role_name, ran or model)
        usage = response.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        # A provider that reports the parts and not the total is not reporting
        # nothing. `codex` does exactly that, and because every summary in this
        # system reads `total_tokens`, an entire review panel booked 3.78M
        # prompt tokens and showed up as zero -- so the one role whose route
        # does report a total looked like the only thing spending anything, and
        # the tuning that followed went at the wrong agent.
        total_tokens = int(usage.get("total_tokens") or 0) or (
            prompt_tokens + completion_tokens)
        u.prompt_tokens += prompt_tokens
        u.completion_tokens += completion_tokens
        u.total_tokens += total_tokens
        try:
            cost = float(usage.get("cost") or 0.0)
        except (TypeError, ValueError):
            cost = 0.0

        # Which route this was, and whether the figure it reported is money.
        #
        # A subscription CLI reports a cost -- `claude -p` returned $0.0010305
        # for a two-token question -- and that figure is what the same work
        # would have cost on an API, not what anybody was charged. Added to
        # `cost` it would count against `budget_usd` and stop a run at the
        # reserve floor for spend that never happened, while the thing that can
        # actually stop that run, a rate limit, remains invisible.
        meta = response.get("_route") or {}
        billed = bool(meta.get("billed", True))
        if meta:
            u.routes.add(str(meta.get("name") or ""))
            if not billed:
                u.turns += int(meta.get("turns") or 1)
                u.unbilled_calls += 1
        else:
            u.routes.add("default")

        notional = 0.0
        if billed:
            u.cost += cost
        else:
            u.notional_cost += cost
            notional, cost = cost, 0.0
        return {
            "cost_usd": cost,
            # What a subscription route's work would have cost on an API. Not
            # money; kept so a reading on a plan can still say what it weighed.
            "notional_usd": notional,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    # -- what one exchange cost -------------------------------------------
    #
    # The per-role totals are a summary appended once, at the end of a run.
    # They cannot say what any single call cost, and they do not exist at all
    # until the run finishes -- so a run in flight showed no spend anywhere.
    # What a call cost is a fact about that call, so it is parked here between
    # the call and the write, and the store puts it on the record.
    #
    # Keyed by role and the exact prompt, held as a queue: the same role asking
    # the same question twice is two exchanges, and each is taken once.

    @staticmethod
    def _exchange_key(role_name: str, prompt: str) -> tuple[str, str]:
        return (role_name, hashlib.sha256(prompt.encode("utf-8")).hexdigest())

    def _park_usage(self, role_name: str, prompt: str, spent: dict[str, Any]) -> None:
        self._exchanges.setdefault(self._exchange_key(role_name, prompt), []).append(spent)

    def take_usage(self, role_name: str, prompt: str) -> dict[str, Any] | None:
        """What one exchange cost, taken once.

        `None` for a prompt this client never sent -- a harness prompt handed to
        aider, or any run that predates this accounting. A record with no cost on
        it is one whose cost is genuinely unknown, which is not the same claim as
        zero and is not rendered as one.
        """
        queue = self._exchanges.get(self._exchange_key(role_name, prompt))
        if not queue:
            return None
        return queue.pop(0)

    @staticmethod
    def _truncated(response: dict[str, Any]) -> bool:
        """Did the model stop because it ran out of room, rather than finished?

        This matters more than it looks. A truncated response is not a malformed
        one: retrying the same prompt produces the same truncation, so the schema
        repair loop below would burn three full generations to fail identically
        -- and each repair appends the truncated output, leaving *less* room than
        the attempt before. Worse, on the schema-less path a cut-off answer is
        indistinguishable from a short one and would be accepted as complete.
        """
        choices = response.get("choices") or []
        if not choices:
            return False
        reason = choices[0].get("finish_reason") or choices[0].get("native_finish_reason") or ""
        return str(reason).lower() in ("length", "max_tokens")

    def _refuse_truncated(self, role: RoleConfig, role_name: str, response: dict[str, Any]) -> None:
        if not self._truncated(response):
            return
        got = len(self._content(response))
        raise LLMError(
            f"role {role_name!r} ({role.model}) hit its output ceiling of "
            f"{role.max_tokens} tokens and was cut off mid-answer after {got:,} characters. "
            "This is not a schema problem and retrying will not fix it: the same prompt "
            "produces the same truncation. Either raise max_tokens for this role, or ask it "
            "for less -- check whether the schema makes it restate things the pipeline "
            "already holds, because every field it must fill costs output whether or not "
            "the answer is used."
        )

    @staticmethod
    def _content(response: dict[str, Any]) -> str:
        choices = response.get("choices") or []
        if not choices:
            raise LLMError(f"no choices in response: {json.dumps(response)[:500]}")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if content is None:
            content = message.get("reasoning") or ""
        if isinstance(content, list):  # some hosts return content parts
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return content or ""

    # -- the one public method -------------------------------------------

    async def ask(
        self,
        role_name: str,
        prompt: str,
        schema: Type[T] | None = None,
        *,
        system: str = "",
        temperature: float | None = None,
    ) -> Any:
        """Ask one role one question.

        Without `schema`, returns the text. With `schema`, returns a validated
        instance -- or raises after three failed attempts. There is no third
        outcome and no partially-trusted result. (INV-5)
        """
        role = self.config.role(role_name)
        system_text = system or role.system
        messages: list[dict[str, str]] = []
        if system_text:
            messages.append({"role": "system", "content": system_text})
        messages.append({"role": "user", "content": prompt})

        # One exchange, however many attempts it takes: a repair round is part
        # of what this answer cost, not a separate call the ledger knows about.
        spent: dict[str, Any] = {}

        def note(response: dict[str, Any]) -> None:
            for key, value in self._account(role_name, role.model, response).items():
                spent[key] = spent.get(key, 0) + value

        if schema is None:
            response = await self._complete(role, role_name, messages, None, temperature)
            note(response)
            self._refuse_truncated(role, role_name, response)
            self._park_usage(role_name, prompt, spent)
            return self._content(response)

        last_error = ""
        for attempt in range(1, SCHEMA_ATTEMPTS + 1):
            response = await self._complete(role, role_name, messages, schema, temperature)
            note(response)
            self._refuse_truncated(role, role_name, response)
            raw = self._content(response)
            candidate = extract_json(raw)
            try:
                value = schema.model_validate_json(candidate)
            except (ValidationError, ValueError) as exc:
                last_error = str(exc)
                if attempt == SCHEMA_ATTEMPTS:
                    break
                # AC-3.2 -- feed the validation error back and let it repair.
                messages.append({"role": "assistant", "content": raw[:8000]})
                messages.append({
                    "role": "user",
                    "content": (
                        "That response did not validate against the required schema.\n\n"
                        f"Validation error:\n{last_error[:4000]}\n\n"
                        "Return the corrected object as raw JSON only. No prose, no code fences. "
                        "Every required field must be present."
                    ),
                })
            else:
                self._park_usage(role_name, prompt, spent)
                return value
        raise LLMError(
            f"role {role_name!r} ({role.model}) failed schema {schema.__name__} "
            f"after {SCHEMA_ATTEMPTS} attempts. Last error: {last_error[:2000]}"
        )

    def _note_answered(self, role_name: str, model: str) -> None:
        """Remember who answered for a role. Tolerates an instance built
        without `__init__`, which some callers construct deliberately to
        count usage without a client."""
        self.__dict__.setdefault("answered", {})[role_name] = model

    def record_external(
        self, role_name: str, model: str, cost: float,
        prompt_tokens: int = 0, completion_tokens: int = 0, calls: int = 1,
        *, route: str = "", billed: bool = True, turns: int = 0,
    ) -> None:
        """Spend that did not go through this client, folded into the same tally.

        A coding harness is a separate process talking to the same provider on
        the same key. Every token it burns is real money and none of it passes
        through `ask`, so for as long as this did not exist the factory's own
        figure was not an underestimate -- it was a different number about a
        smaller thing, presented as the cost of the run.

        On the run that found this the packet reported $1.20 and the provider
        billed $4.44. The harness had spent 75 minutes of agentic work across
        three units and the ledger recorded none of it, which also meant the
        repair budget -- the mechanism whose entire job is to stop a run
        spending more than a human agreed to -- was blind to the largest single
        consumer in the system.
        """
        u = self.usage.setdefault(role_name, RoleUsage())
        u.calls += max(0, calls)
        if model:
            u.models.add(model)
            self._note_answered(role_name, model)
        u.prompt_tokens += max(0, prompt_tokens)
        u.completion_tokens += max(0, completion_tokens)
        u.total_tokens += max(0, prompt_tokens) + max(0, completion_tokens)
        if route:
            u.routes.add(route)
        try:
            amount = max(0.0, float(cost))
        except (TypeError, ValueError):
            amount = 0.0
        # A session on a subscription reports a figure that is not money, for
        # the same reason a completion on one does: it is what the work would
        # have cost on an API. Counted as spend it would stop a run at the
        # reserve floor for nothing.
        if billed:
            u.cost += amount
        else:
            u.notional_cost += amount
            # Only turns the harness actually reported. This fell back to the
            # call count, so a route that says nothing about turns booked one
            # per call and the field read as an agentic turn count: the breaker
            # showed 10 turns for 10 sessions of roughly a hundred each, beside
            # a repairer on a route that does report them showing 356 for 18.
            # Two different facts under one name, and the wrong one was the
            # believable-looking one. `unbilled_calls` already counts calls.
            u.turns += max(0, turns)
            u.unbilled_calls += max(0, calls)

    # -- the plan's own gauge ---------------------------------------------

    @property
    def meters(self):
        """Window readings, shared on disk across features and processes.

        Built lazily and from the config's evidence path rather than held as
        state, so a config edited at runtime -- which the console does -- is
        followed rather than snapshotted.
        """
        from .meters import MeterStore

        return MeterStore(self.config.evidence_path / "meters.json")

    def _note_windows(self, route: RouteConfig, envelope: dict[str, Any]) -> None:
        """Record what a tool just said about its own limits.

        Never allowed to fail a call. This is an observation about the account,
        and losing one is a smaller thing than losing the answer that carried
        it -- which is why the whole body is guarded rather than the write
        alone.
        """
        try:
            from .meters import read_credits, read_windows

            windows = read_windows(envelope, route)
            credits = read_credits(envelope, route)
            if windows or credits:
                self.meters.record(route.name, windows, credits)
        except Exception:
            pass

    def coverage(self) -> dict[str, Any]:
        """What the budget guard can see, and what it cannot.

        `budget_usd` stops a run by comparing dollars against a ceiling, and it
        can only do that for work somebody is charged dollars for. An agent on a
        subscription reports a figure that is not money, so it is kept out of
        `total_cost` -- which means the guard is *blind* to it, not that the work
        was free.

        A run entirely on subscription routes therefore reads `$0.00 of $10.00`
        and never stops, which is correct arithmetic and a misleading sentence.
        This is what makes it sayable: which agents the ceiling does not cover,
        how much work went through them, and what it would have cost if it had
        been billed.
        """
        unbilled = sorted(
            name for name, u in self.usage.items() if u.unbilled_calls)
        return {
            "billed_usd": round(
                sum(u.cost for u in self.usage.values()), 6),
            "unbilled_roles": unbilled,
            "unbilled_turns": sum(u.turns for u in self.usage.values()),
            # Calls on routes that price nothing, beside the turns. A route that
            # reports no turns contributes to the second and not the first, and
            # a reader comparing them can see which routes this run could
            # actually account for.
            "unbilled_calls": sum(u.unbilled_calls for u in self.usage.values()),
            "fallbacks": list(self.fallbacks),
            "notional_usd": round(
                sum(u.notional_cost for u in self.usage.values()), 6),
            "routes": sorted({r for u in self.usage.values() for r in u.routes if r}),
        }

    def usage_report(self) -> dict[str, Any]:
        roles = {name: u.as_dict() for name, u in sorted(self.usage.items())}
        return {
            "roles": roles,
            "total_tokens": sum(u.total_tokens for u in self.usage.values()),
            "total_cost": round(sum(u.cost for u in self.usage.values()), 6),
            # What the subscription routes say the same work would have cost.
            # Reported so a human can see it; deliberately not part of
            # `total_cost`, which is the number the budget guard stops on.
            "notional_cost": round(
                sum(u.notional_cost for u in self.usage.values()), 6),
            "unbilled_turns": sum(u.turns for u in self.usage.values()),
            # Calls on routes that price nothing, beside the turns. A route that
            # reports no turns contributes to the second and not the first, and
            # a reader comparing them can see which routes this run could
            # actually account for.
            "unbilled_calls": sum(u.unbilled_calls for u in self.usage.values()),
            "fallbacks": list(self.fallbacks),
            "calls": sum(u.calls for u in self.usage.values()),
        }
