"""The model gateway: how a session in a container reaches its model.

A session's sandbox holds no credential. Where its harness would have been
handed this app's provider key, it is handed a *session token* instead -- a
random string that is worth something only to this process, only while the
session is open -- and its base URL is `http://models.fabrika.internal`. The
egress proxy answers that one name by carrying the connection here
(`egress_proxy.carry_to_gateway`), and this module does the rest:

1. **Checks the token.** An unknown or revoked one gets a 401 and nothing is
   sent anywhere. A token copied out of a container stops working when the
   session that held it closes.
2. **Picks the target from the lease.** The token says which route it was
   minted for, and the route says where its model is. The container cannot
   ask for a different provider.
3. **Swaps the credential.** The session token comes off, the route's real key
   goes on, and the route's own headers with it.
4. **Forwards faithfully.** Request and response bodies pass through as they
   are, streamed responses included. A harness that works against the
   provider works against this.
5. **Records what it saw.** Tokens and, where the provider reports it, cost,
   per session -- observed, rather than whatever the harness says about
   itself.

Because it sees every call before it is sent, it also refuses two kinds: one
whose prompt and `max_tokens` cannot fit the route's context window, and any
call once the run's budget is spent. Both are numbers from `factory.yaml`, read
by plain code.

It listens on this machine's loopback only, on `docker.gateway_port`, and
starts with the first session that needs it. Plain code, no model in it.

Why it is here and not in a container: the credentials already live in this
process, and "the credential never leaves the process that holds it" is a
stronger rule than "the credential is in a container Fabrika trusts". The
design, and what was measured before it was built, is docs/model-gateway.md.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import math
import secrets
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlsplit

import h11
import httpx

from .egress_proxy import GATEWAY_NAME

log = logging.getLogger(__name__)

#: What a session in a container is told its provider is.
URL = f"http://{GATEWAY_NAME}"
#: What a session token looks like. The prefix is for a person reading a log;
#: the rest is what makes it unguessable.
TOKEN_PREFIX = "fabrika-session-"
#: A request body larger than this is refused rather than buffered. The largest
#: prompt any role sends is a fraction of it.
MAX_BODY = 32 * 1024 * 1024
#: How much of an unstreamed response is kept to read its usage from.
MAX_USAGE_READ = 4 * 1024 * 1024

_HOP = frozenset({"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
                  "te", "trailer", "transfer-encoding", "upgrade", "proxy-connection"})
#: Never forwarded upstream: the hop-by-hop headers, the session's own
#: credential, and what this side re-frames.
_DROP_UP = _HOP | {"host", "authorization", "x-api-key", "content-length",
                   "accept-encoding"}
#: Never forwarded back: the response is re-framed here, and decoded.
_DROP_DOWN = _HOP | {"content-length", "content-encoding"}


class GatewayError(RuntimeError):
    """The gateway could not be started. Says why, and what to change."""


#: The budget a session opened now is held to: the ceiling in dollars, and how
#: to ask what has been spent against it. Set by whatever owns a budget -- the
#: rework loop -- around the work it pays for, and read when a lease is made.
#: None, the default, is no ceiling: the build lane is bought before there is
#: anything to decide, and the budget never applied to it.
BUDGET: contextvars.ContextVar[tuple[float, Callable[[], float]] | None] = \
    contextvars.ContextVar("fabrika_gateway_budget", default=None)

#: Characters per token, for the estimate a context window is checked with. No
#: tokenizer is to hand for a model this package does not know the name of, and
#: four is the usual figure for English. It errs toward sending: a borderline
#: call goes out, and the server's own refusal is what the human reads.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Roughly how many tokens `text` is. See `CHARS_PER_TOKEN`."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def prompt_of(body: dict[str, Any]) -> str:
    """Everything in a request that counts against the context window except
    the answer: the messages, and the tools a harness describes in every call."""
    parts = [body.get("messages"), body.get("input"), body.get("tools"), body.get("system")]
    return json.dumps([p for p in parts if p], ensure_ascii=False, separators=(",", ":"))


def answer_room(body: dict[str, Any]) -> int:
    """The most the request asks to be answered with, under whichever name the
    harness uses for it."""
    for key in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
        try:
            value = int(body.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value:
            return value
    return 0


def window_problem(route_name: str, window: int, prompt_tokens: int, max_tokens: int) -> str:
    """Why a call cannot fit `window`, or empty when it can (or the window is
    not known)."""
    if window <= 0 or prompt_tokens + max_tokens <= window:
        return ""
    return (f"this call cannot fit route {route_name!r}: about {prompt_tokens:,} prompt tokens "
            f"plus up to {max_tokens:,} for the answer is more than its context window of "
            f"{window:,}. Nothing was sent. Give the server a larger context, lower the "
            "role's `max_tokens`, or point the role at a route with more room.")


def _dig(body: Any, path: str) -> Any:
    for part in [p for p in (path or "").split(".") if p]:
        if not isinstance(body, dict):
            return None
        body = body.get(part)
    return body


#: Where a context window was asked for and not given, so a call does not wait
#: on the same silence every time. A route's Check asks again (`retry`).
_unanswered: set[str] = set()


async def ensure_context_window(route: Any, base_url: str = "", timeout_s: float = 10.0,
                                retry: bool = False) -> int:
    """The route's context window, read from its server the first time it is
    asked for and kept on the route after that.

    Only a route that says where (`context_window_path`, `context_window_key`)
    and has not been given a number is asked. A server that does not answer,
    or answers without the key, leaves the window unknown -- and an unknown
    window checks nothing -- rather than stopping the call over a reading.
    """
    if route.context_window or not (route.context_window_path and route.context_window_key):
        return route.context_window
    parts = urlsplit(base_url or route.base_url)
    if not parts.scheme or not parts.netloc:
        return 0
    url = f"{parts.scheme}://{parts.netloc}/{route.context_window_path.lstrip('/')}"
    if url in _unanswered and not retry:
        return 0
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            answer = await client.get(url)
        value = int(_dig(answer.json(), route.context_window_key) or 0)
    except (httpx.HTTPError, ValueError, TypeError):
        log.info("model gateway: route %s did not say its context window at %s",
                 route.name, url)
        _unanswered.add(url)
        return 0
    if value > 0:
        route.context_window = value
        _unanswered.discard(url)
    else:
        _unanswered.add(url)
    return route.context_window


@dataclass
class Lease:
    """One session's right to reach one route's model, and what it has used."""

    token: str
    role: str
    route: str
    base_url: str
    key: str = field(repr=False)
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    #: The route's context window in tokens; 0 checks nothing.
    context_window: int = 0
    #: The run's ceiling and its spend so far, when one applies. See `BUDGET`.
    budget: tuple[float, Callable[[], float]] | None = field(default=None, repr=False)
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""

    def observe(self, usage: dict[str, Any] | None, model: str) -> None:
        self.calls += 1
        if model:
            self.model = model
        if not usage:
            return
        self.prompt_tokens += _int(usage.get("prompt_tokens", usage.get("input_tokens")))
        self.completion_tokens += _int(usage.get("completion_tokens",
                                                 usage.get("output_tokens")))
        self.cost_usd += _float(usage.get("cost"))

    def refusal(self, body: bytes) -> tuple[int, str]:
        """Why this call must not be sent, as a status and a sentence, or
        (0, "") when it may."""
        if self.budget is not None:
            ceiling, spent = self.budget
            try:
                so_far = float(spent()) + self.cost_usd
            except Exception:  # noqa: BLE001 -- a broken reading refuses nothing
                so_far = 0.0
            if ceiling > 0 and so_far >= ceiling:
                return 402, (f"the run's budget is spent: ${so_far:.2f} of ${ceiling:.2f} "
                             "(`rework.budget_usd`). Nothing was sent. Raise it in "
                             "factory.yaml if the work is worth more.")
        if self.context_window > 0 and body:
            try:
                request = json.loads(body)
            except ValueError:
                request = None
            if isinstance(request, dict):
                problem = window_problem(self.route, self.context_window,
                                         estimate_tokens(prompt_of(request)),
                                         answer_room(request))
                if problem:
                    return 400, problem
        return 0, ""

    def spend(self) -> dict[str, Any] | None:
        """What this session used, in the shape a spend file has, or None if
        it made no call at all."""
        if not self.calls:
            return None
        return {"cost_usd": round(self.cost_usd, 6), "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens, "model": self.model,
                "calls": self.calls, "source": "gateway"}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


class _Reading:
    """The usage a response reports, read while it passes through.

    A streamed response is read event by event and keeps the last usage it
    carries, which is where an OpenAI-compatible stream puts its totals. An
    unstreamed one is read whole, up to a bound. Neither is changed: this only
    looks.
    """

    def __init__(self, streamed: bool) -> None:
        self.streamed = streamed
        self.usage: dict[str, Any] | None = None
        self.model = ""
        self._pending = b""
        self._body = bytearray()

    def feed(self, chunk: bytes) -> None:
        if not self.streamed:
            if len(self._body) < MAX_USAGE_READ:
                self._body.extend(chunk)
            return
        self._pending += chunk
        *lines, self._pending = self._pending.split(b"\n")
        for line in lines:
            line = line.strip()
            if line.startswith(b"data:"):
                self._take(line[5:].strip())

    def finish(self) -> None:
        if self.streamed:
            if self._pending.strip().startswith(b"data:"):
                self._take(self._pending.strip()[5:].strip())
        else:
            self._take(bytes(self._body))

    def _take(self, payload: bytes) -> None:
        if not payload or payload == b"[DONE]":
            return
        try:
            event = json.loads(payload)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        usage = event.get("usage")
        if not isinstance(usage, dict) and isinstance(event.get("response"), dict):
            usage = event["response"].get("usage")
        if isinstance(usage, dict) and usage:
            self.usage = usage
        if isinstance(event.get("model"), str) and event["model"]:
            self.model = event["model"]


class Gateway:
    """The listener, and the leases it honours."""

    def __init__(self, timeout_s: float = 600.0) -> None:
        self.leases: dict[str, Lease] = {}
        self.port = 0
        self._server: asyncio.base_events.Server | None = None
        self._open: set[asyncio.StreamWriter] = set()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=20.0), follow_redirects=False)

    async def start(self, host: str, port: int) -> None:
        self._server = await asyncio.start_server(self._serve, host, port)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            # A harness's HTTP client keeps its connection open between calls,
            # and `wait_closed` waits for every connection: without closing
            # them first, stopping waits for a client that will never hang up.
            for writer in list(self._open):
                writer.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=5)
            except Exception:  # noqa: BLE001 -- closing is best effort
                pass
        await self._client.aclose()
        self.leases.clear()

    def lease(self, *, role: str, route: str, base_url: str, key: str,
              headers: dict[str, str] | None = None, context_window: int = 0,
              budget: tuple[float, Callable[[], float]] | None = None) -> Lease:
        token = TOKEN_PREFIX + secrets.token_urlsafe(24)
        lease = Lease(token=token, role=role, route=route, base_url=base_url, key=key,
                      headers=dict(headers or {}), context_window=context_window,
                      budget=budget)
        self.leases[token] = lease
        return lease

    def revoke(self, lease: Lease) -> None:
        self.leases.pop(lease.token, None)

    def _lease_for(self, headers: dict[str, str]) -> Lease | None:
        scheme, _, value = headers.get("authorization", "").partition(" ")
        token = value.strip() if scheme.lower() == "bearer" else ""
        return self.leases.get(token or headers.get("x-api-key", "").strip())

    # -- the connection ----------------------------------------------------

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = h11.Connection(h11.SERVER)
        self._open.add(writer)
        try:
            while True:
                request, body = await self._read(conn, reader)
                if request is None:
                    break
                await self._answer(conn, writer, request, body)
                if h11.MUST_CLOSE in (conn.our_state, conn.their_state):
                    break
                try:
                    conn.start_next_cycle()
                except h11.LocalProtocolError:
                    break
        except (h11.ProtocolError, ConnectionError, OSError):
            pass
        finally:
            self._open.discard(writer)
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    async def _read(self, conn: h11.Connection,
                    reader: asyncio.StreamReader) -> tuple[h11.Request | None, bytes]:
        request: h11.Request | None = None
        body = bytearray()
        while True:
            event = conn.next_event()
            if event is h11.NEED_DATA:
                conn.receive_data(await reader.read(65536))
            elif isinstance(event, h11.Request):
                request = event
            elif isinstance(event, h11.Data):
                body.extend(event.data)
                if len(body) > MAX_BODY:
                    raise ConnectionError("request body over the gateway's bound")
            elif isinstance(event, h11.EndOfMessage):
                return request, bytes(body)
            else:
                return None, b""

    async def _send(self, conn: h11.Connection, writer: asyncio.StreamWriter,
                    event: Any) -> None:
        data = conn.send(event)
        if data:
            writer.write(data)
            await writer.drain()

    async def _refuse(self, conn: h11.Connection, writer: asyncio.StreamWriter,
                      status: int, message: str) -> None:
        body = json.dumps({"error": {"message": message, "type": "fabrika_gateway"}}).encode()
        await self._send(conn, writer, h11.Response(status_code=status, headers=[
            ("content-type", "application/json"), ("content-length", str(len(body)))]))
        await self._send(conn, writer, h11.Data(data=body))
        await self._send(conn, writer, h11.EndOfMessage())

    async def _answer(self, conn: h11.Connection, writer: asyncio.StreamWriter,
                      request: h11.Request, body: bytes) -> None:
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in request.headers}
        lease = self._lease_for(headers)
        if lease is None:
            await self._refuse(conn, writer, 401,
                               "not a session token this gateway issued, or its session "
                               "has ended")
            return
        status, problem = lease.refusal(body)
        if status:
            log.warning("model gateway: refused a call for %s on %s: %s",
                        lease.role, lease.route, problem)
            await self._refuse(conn, writer, status, problem)
            return
        target = lease.base_url.rstrip("/") + request.target.decode("latin-1")
        upstream = [(k, v) for k, v in headers.items() if k not in _DROP_UP]
        upstream.append(("accept-encoding", "identity"))
        upstream += [(k, v) for k, v in lease.headers.items()]
        if lease.key:
            upstream.append(("authorization", f"Bearer {lease.key}"))
        try:
            response = await self._client.send(self._client.build_request(
                request.method.decode("ascii"), target, headers=upstream,
                content=body or None), stream=True)
        except httpx.HTTPError as exc:
            host = urlsplit(lease.base_url).hostname or lease.base_url
            log.warning("model gateway: %s for %s could not reach %s: %s",
                        lease.role, lease.route, host, type(exc).__name__)
            await self._refuse(conn, writer, 502,
                               f"the model gateway could not reach {host}: "
                               f"{type(exc).__name__}")
            return
        reading = _Reading("text/event-stream" in response.headers.get("content-type", ""))
        try:
            await self._send(conn, writer, h11.Response(
                status_code=response.status_code,
                reason=response.reason_phrase.encode("latin-1", "replace"),
                headers=[(k, v) for k, v in response.headers.items()
                         if k.lower() not in _DROP_DOWN]))
            async for chunk in response.aiter_bytes():
                reading.feed(chunk)
                await self._send(conn, writer, h11.Data(data=chunk))
            await self._send(conn, writer, h11.EndOfMessage())
        finally:
            await response.aclose()
            reading.finish()
            lease.observe(reading.usage, reading.model)


# -- the one per process -----------------------------------------------------

#: The gateway of each event loop that started one, with the loop itself: an
#: id alone can be reused by a later loop, which would be handed a server
#: bound to one that is closed.
_running: dict[int, tuple[asyncio.AbstractEventLoop, Gateway]] = {}
_locks: dict[int, asyncio.Lock] = {}


def _lock() -> asyncio.Lock:
    # One per event loop, as in `egress`: the test suite starts a fresh loop
    # per `asyncio.run`, and a lock or a server made on one cannot be used
    # from another.
    return _locks.setdefault(id(asyncio.get_running_loop()), asyncio.Lock())


async def ensure(config: Any) -> Gateway:
    """The running gateway, started on first use. Raises `GatewayError`."""
    loop = asyncio.get_running_loop()
    async with _lock():
        held = _running.get(id(loop))
        if held is not None and held[0] is loop:
            return held[1]
        gateway = Gateway(timeout_s=config.api.timeout_s)
        port = config.docker.gateway_port
        try:
            await gateway.start("127.0.0.1", port)
        except OSError as exc:
            await gateway.stop()
            raise GatewayError(
                f"the model gateway could not listen on 127.0.0.1:{port} ({exc.strerror or exc}). "
                "Another Fabrika on this machine may hold it; set `docker.gateway_port` in "
                "factory.yaml to a free port.") from exc
        _running[id(loop)] = (loop, gateway)
        return gateway


async def stop() -> None:
    """Stop this loop's gateway, if it started one. Every lease ends with it."""
    loop = asyncio.get_running_loop()
    held = _running.pop(id(loop), None)
    if held is not None and held[0] is loop:
        await held[1].stop()


def leases_its_key(route: Any) -> bool:
    """Whether a route's session would be handed a key this app holds -- the
    provider key, or the route's own, or none at all on a route with
    `auth: none`.

    That key is the one a session never gets any more: it gets a session token
    for the gateway instead, and the gateway forwards to the route's own
    endpoint. A route that signs in some other way -- its own token, a file,
    the keychain -- is not changed here (yet: see docs/model-gateway.md for the
    order those go in).
    """
    return any(spec.kind == "env" and spec.from_api_key
               for spec in getattr(route, "container_credentials", []) or [])
