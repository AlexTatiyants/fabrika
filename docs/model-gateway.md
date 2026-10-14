# The model gateway

**Status: step 1 built.** The gateway, session tokens, the proxy's special case
and OpenHands sessions through them are in (`factory/gateway.py`); the rest of
this page is proposed. It says what was wrong, the rule that fixes it, and the
order to build it in.

## In one paragraph

An agent's sandbox should never hold a credential, and an agent should reach
its model through Fabrika, not through the internet. Today both are false: the
agent's model client runs inside the sealed container with the provider key in
its environment, and its calls leave through the same egress proxy that
contains package installs. A **model gateway** — one fixed name inside every
agent's sandbox, answered by Fabrika — fixes both. Fabrika holds the keys, picks
the target from the route, and forwards the call: to OpenRouter, to a
local model server on this machine, or to one on another machine. Local models stop
being a special case and become one more target.

## What is wrong today

### The key sits next to the code the agent runs

The OpenHands driver builds its LLM client inside the unit's container, from
`LLM_API_KEY` and `LLM_BASE_URL` (`factory/harnesses/openhands_driver.py`
around line 199). `sessioncreds.py` hands that key in through an env file, and
the CLI routes get theirs the same way: Claude Code's OAuth token or keychain
entry copied to `/agent-home/.claude/.credentials.json`, Codex's
`~/.codex/auth.json` copied to `/agent-home/.codex/auth.json`
(`factory.example.yaml`, the `container_credentials` of each route).

`sessioncreds.py` is careful about how the key travels — never in argv, a copy
not the original, deleted afterwards — but once it is there, everything in the
container can read it: the agent's own shell commands, a test it runs, and the
postinstall script of any package it installs. The container can reach any
public address. The egress proxy logs where a stolen key went; it does not stop
it going. (Step 1 closed this for OpenHands; the CLI routes still copy their
sign-ins in.)

### One fence doing two jobs

`docs/isolation.md` says what needs the internet: "an agent calling its model
provider, setup installing packages". Those are different kinds of traffic with
different trust. Package installs are the work's own needs, and the fence
exists to contain them. A model call is Fabrika's own machinery, carrying
Fabrika's credential, and only rides through the fence because the model client
happens to live inside it.

Claude Code and Codex, run on their own, draw the line the other way: the agent
loop that holds the sign-in runs outside the sandbox, and only the commands it
runs go inside. Fabrika puts the whole agent inside, which is the right call for
everything else it does — but it means the model call has to be taken back out
deliberately.

### Local models cannot be reached at all

INV-7 says moving to a local server is "a `base_url` change". That is true for
Fabrika's own calls, which run in the server process on this machine, and false
for every agent session:

- A sealed network is `--internal`, and `host.docker.internal` is pinned to the
  container's own loopback (`HOST_UNREACHABLE`, `factory/containers.py`).
- The egress proxy refuses everything that is not a public address
  (`public()`, `factory/egress_proxy.py`). A local model server on this machine is
  loopback; one on another machine is a private LAN address. Both are refused,
  and a literal IP never reaches the proxy in the first place.

Three smaller gaps sit behind that one, all on the host side:

- **One API endpoint.** Every `kind: api` completion goes through the single
  `api:` block (`factory/llm.py`, `_post`); a second API route's own `base_url`
  is never read. A local server can replace OpenRouter but cannot sit beside it.
- **A key is required.** The executor env, `sessioncreds`, the OpenHands driver
  and the route's connected state all refuse an empty key. A local server has
  none.
- **No context window.** Prompts run to roughly 120,000 characters and
  `max_tokens` to 48,000 on some roles. A local server started with a smaller
  `--ctx-size` answers with a 400 that is not retried, or truncates.

## The rule

> **No credential enters a sandbox. Every model call from a sandbox goes
> through Fabrika.** The egress proxy is for the work's own internet needs and
> nothing else.

This should become an invariant (INV-14), with a test, and INV-7's sentence
becomes true as written.

## The design

### What changes, in a picture

```
today
  agent container ── LLM_API_KEY in env
     ├─ npm, pip ─────────┐
     └─ model call ───────┴── fabrika-egress ── public internet (OpenRouter)
                                    ╳ this machine, your LAN

with the gateway
  agent container ── a session token, worth nothing outside this session
     ├─ npm, pip ──────────── fabrika-egress ── public internet
     └─ models.fabrika.internal
            └── fabrika-egress ── Fabrika (gateway) ─┬─ OpenRouter   + key
                                                     ├─ local server, this machine
                                                     └─ local server, 192.168.1.50
```

### One name inside the sandbox

Every agent container gets one base URL, `http://models.fabrika.internal/…`.
The proxy already refuses `.internal` names (`LOCAL_SUFFIXES`), so this name
can never collide with a real one. The proxy answers it as a special case in
`answer()`, before the `.internal` refusal, when Fabrika started it with a
gateway (`EGRESS_GATEWAY`). It answers on every network the proxy is attached
to, setup's included: the proxy cannot tell an agent's network from setup's,
and the name is not what is guarded. The session token is. A setup script that
finds the name gets a 401 and nothing more. Check containers have no way out
at all and never see it.

A connection to its stand-in is carried by `carry()` to the gateway instead of
going through `resolve()` and the public-address check. That is the only new
path out of the proxy, and its destination is fixed by Fabrika, not named by the
agent.

Plain HTTP inside the sealed network is fine: the hop is container to proxy, on
a network nothing else is on. The gateway makes the TLS connection to the real
provider.

### The gateway runs in Fabrika

The gateway is a listener in the Fabrika server process, not a container,
because that is where the credentials already live: the stored `api.api_key`,
the environment, and the macOS keychain entry Claude Code signs in with, which
no container can read. "The credential never leaves the Fabrika process" is a
stronger rule than "the credential is in a container Fabrika trusts".

It is plain code, not an agent, and does five things per request:

1. **Checks the session token.** Fabrika mints a random token per agent session
   and hands it in where the key used to go (`LLM_API_KEY`,
   `ANTHROPIC_AUTH_TOKEN`, …). The token names the session, the unit and the
   route. It is revoked when the session closes, so a token copied out of a
   container is worth nothing an hour later. An unknown or revoked token gets a
   401.
2. **Picks the target from the route.** The token says which route; the route
   says where it goes and how it authenticates. The agent cannot ask for a
   different provider.
3. **Swaps the credential.** The session token comes off and the route's real
   key goes on — or nothing, for a local route with `auth: none`.
4. **Forwards faithfully.** Request and response bodies pass through untouched,
   including streaming (SSE) and tool calls. The gateway does not reshape what
   the harness sent; a harness that works against the provider works against
   the gateway.
5. **Records what it saw.** Tokens and, where the provider reports it, cost —
   from the response, or from the last SSE event that carries usage. It does
   not add `stream_options.include_usage` to a request: that would change what
   the harness sent, and OpenRouter reports usage on a stream without it. Today a session's
   spend is whatever the harness reports about itself
   (`_record_session_spend`, `factory/executors.py`); the gateway's count is
   observed, and can be checked against the harness's.

Because it sees every call before it is made, the gateway can also refuse one:
when the run's `budget_usd` is spent, or when a prompt plus `max_tokens` is
larger than the route's context window. Both are numbers in `factory.yaml` read
by plain code, so INV-6 holds. Neither is built yet: the context window is a
route field that arrives in step 2, and the budget check goes in with it.

### How the proxy reaches the gateway

The server binds `127.0.0.1:8077` and nothing else. The proxy runs on the
default bridge. On Docker Desktop, a container on the bridge reaches a listener
bound to the host's `127.0.0.1` through `host.docker.internal` (measured: see
**Step 0, measured** below), so the gateway can stay on loopback. That reach is
not new: the proxy has always had it, and its policy is what refuses it. On a
Linux host it does not work, and the gateway would have to listen on the
bridge's gateway address instead; that is unmeasured. Either way the gateway
gets its own listener and port — never the console's — and only the proxy
container is ever pointed at it.

If neither works cleanly, the fallback is a small gateway container next to the
proxy, handed the keys through an env file. That keeps the keys out of the
agent's container, which is the point, and gives up the keychain and the
"never leaves the process" property, which is a cost to write down rather than
hide.

### Routes get their own endpoints

`llm.py` stops reading `config.api` for every completion and reads the route's
own `base_url` and key, so a local route can sit beside OpenRouter and roles
can choose between them. A route gains three fields:

- `auth: none` — no key is needed, and the four places that require one honour
  it.
- `context_window` — in tokens. For the first local server it is read from `/props`
  (`n_ctx`) when the route is connected, so nothing needs typing in; elsewhere
  it is set in `factory.yaml`.
- `dialect` — `openrouter` or `openai`. Only `openrouter` gets the `reasoning`
  and `provider` fields `llm.py` sends today; only it is probed at `/key`.

Fabrika's own calls go straight from the server to the route's target. They
never pass through the proxy and never did.

### Local servers

This page names no server or model: under INV-7 those live in
`factory.example.yaml`, and "the local server" here means the one chosen for
the first local route. What it needs, all measured before
it is called supported:

- **Started with `--jinja`.** Without it there is no native tool calling, and
  OpenHands needs tool calls.
- **Strict structured output.** It supports part of JSON Schema. Every
  schema in `factory/schemas.py` is run through `strict_schema()` and sent to a
  real server once, and any it rejects is listed.
- **Usage is booked as local.** `usage.cost` is absent, so the ledger shows
  tokens and "local", not "$0.00 billed".

The setup card gains a "Local server" choice: a URL and no key, a model list
filled from `/v1/models`, and a check that runs from inside a throwaway sealed
container through the gateway — the path an agent will actually take, not just
"the server answers from here".

Both deployments are the same configuration with a different URL:
`http://localhost:8080/v1` when the model runs on this machine,
`http://192.168.1.50:8080/v1` when it runs on another. The gateway is on the
host, so a LAN address or a literal IP is no longer a problem.

## Per harness

| Route | Credential today | Through the gateway | Unknowns |
|---|---|---|---|
| default (OpenHands) | `LLM_API_KEY` in an env file | `LLM_BASE_URL` → gateway, `LLM_API_KEY` = session token, model prefix `openai/` | LiteLLM prices nothing behind an `openai/` prefix, so the driver's `accumulated_cost` goes to zero; the gateway's count replaces it |
| Claude Code | OAuth token or keychain entry copied in | `ANTHROPIC_BASE_URL` → gateway, `CLAUDE_CODE_OAUTH_TOKEN` = session token; gateway swaps in the real OAuth bearer | Whether the plan's terms allow it. Measured end to end with the real sign-in: works |
| Codex | `~/.codex/auth.json` copied in, refreshed inside | A custom model provider with `requires_openai_auth = true` and `wire_api = "responses"`, pointed at the gateway; a planted `auth.json` holding the session token; gateway swaps in the real bearer and owns the refresh | Whether the plan's terms allow it. Measured end to end with the real sign-in: works. Not `OPENAI_BASE_URL` or `chatgpt_base_url`: both measured, neither moves the model call |
| Google's CLI | Its API key in the completion box's env file | Its base URL → gateway | None known; completions only |

A route that cannot go through the gateway keeps its credential in the
container, and the console says so on the route's card. That is a recorded
exception to INV-14, not a silent one.

## Step 0, measured

On Docker Desktop 29.7.2 (arm64), 14 October 2026. A recording stub stood in
for the provider on the host's `127.0.0.1`. A relay stood in for the proxy's
special case: same image and flags as `fabrika-egress`, on the bridge and on
an `--internal` network, piping bytes without reading them, as `carry()` does.
The CLIs ran in their own `fabrika-harness` images, at the versions pinned in
`factory.example.yaml`, on the sealed network, with the gateway name pointed at
the relay and no real credential anywhere.

| Question | Result |
|---|---|
| Does a proxy-shaped container reach a loopback-only listener on the host? | Yes, through `host.docker.internal` (`192.168.65.254`) |
| Does a sealed container reach it directly? | No: connection refused by name, network unreachable by address. Isolation holds |
| Does streaming survive the relay? | Yes. SSE events sent 150 ms apart arrived 150 ms apart; nothing buffered |
| Claude Code 2.1.260, `ANTHROPIC_AUTH_TOKEN` | Works. One request, `POST /v1/messages?beta=true`, streamed, bearer = session token |
| Claude Code, `ANTHROPIC_API_KEY` | Works. Same request, `x-api-key` = session token |
| Claude Code, `CLAUDE_CODE_OAUTH_TOKEN` (subscription mode) | Works. Same request plus the `oauth-2025-04-20` beta header and the subscription-only tools: Claude Code believes it is signed in to a plan, which is what the swap needs |
| Codex 0.153.4, custom provider, `env_key` = session token | Works. `POST /v1/responses` over HTTP, bearer = session token |
| Codex, `OPENAI_BASE_URL` | Ignored. The built-in provider went to `wss://api.openai.com` over a WebSocket |
| Codex, ChatGPT sign-in with `chatgpt_base_url` | Fails. Side calls (plugins, MCP, analytics) move; the model call still goes to `wss://chatgpt.com` |
| Codex, ChatGPT sign-in, custom provider with `requires_openai_auth` | Works. `GET /backend-api/codex/models` and `POST /backend-api/codex/responses` reach the gateway with the session token as bearer and the account-id header; `auth.json` was not rewritten, because a fresh `last_refresh` means no refresh is attempted |

What this settles:

- **The gateway runs in Fabrika, on loopback**, on Docker Desktop. The fallback
  container is not needed there.
- **No WebSocket passthrough.** Both CLIs, configured as above, use plain HTTP
  POST with SSE. Codex's built-in provider prefers WebSockets, which is one
  more reason not to use it.
- **Both subscription CLIs can be relayed on the client side.** The container
  holds only a session token. For Codex, Fabrika writes a fresh `auth.json`
  per session (session token, `last_refresh` = now), so the CLI never tries to
  refresh inside the container; the real refresh happens on the host.

### With the real sign-in

The same path, with a gateway on the host's loopback in place of the stub. The
gateway was started by hand from a terminal, and read the real credentials
itself: Claude Code's from the keychain item `Claude Code-credentials`, Codex's
from `~/.codex/auth.json`. It swapped them in for the session token and
forwarded over TLS. Nothing was refreshed, and neither source was modified.
The containers held only the session token throughout.

| Run | Result |
|---|---|
| Claude Code 2.1.260, `--model sonnet`, subscription mode | Answered "pong". Two `POST /v1/messages` to `api.anthropic.com`, both 200, streamed |
| Codex 0.153.4, the reviewer's model, ChatGPT sign-in, custom provider | Answered "pong". `GET /backend-api/codex/models` ×3 and `POST /backend-api/codex/responses` to `chatgpt.com`, all 200. The container's `auth.json` still held the session token afterwards |

So both providers accept a subscription token swapped in by a relay, and the
whole approach works end to end on Docker Desktop. One thing to fix when this
is built: the spike's usage reading pattern-matched the last 8 kB of the
stream and got Codex's wrong (zero, where the CLI reported 9,476 in and 5
out). The gateway has to parse SSE events properly and read `message_delta` /
`response.completed`.

What it does not settle:

- **The plans' terms.** That the providers accept the token says nothing
  about whether a subscription may be used this way. That needs a reading of
  each plan's terms before the CLI rows are built.
- **Side traffic.** Claude Code sent the gateway nothing but the model call;
  whether it tried other hosts is not visible, since no outside name resolves
  on a sealed network without the proxy. Codex logged attempts at
  `chatgpt.com` for MCP and plugins, which failed without stopping the turn. Through the real proxy those calls
  would go out as public traffic, carrying the session token, which is worth
  nothing outside the gateway.
- **The proxy itself.** The relay copied `carry()`'s byte pipe, not
  `answer()`'s DNS special case or the iptables redirect. Those are built and
  tested in step 1.
- **OpenHands** was not run. It speaks OpenAI chat completions through
  LiteLLM, the plainest case, and is step 1's first test.
- **Linux.**

## Order of work

Each step is usable on its own, and each ends with the old path removed, not
left beside the new one.

0. **Measure.** Done for Docker Desktop, including one real-credential run
   per subscription CLI; see **Step 0, measured**. Linux and the plans' terms
   remain.
1. **Gateway and OpenHands. Built.** Session tokens, the proxy special case,
   the gateway forwarding to the route's provider. Any in-container session
   whose credential was `from_api_key` gets a session token in its place and
   the gateway as its base URL; the key stays in this process. The gateway's
   tally is the session's spend file, with what the driver reported kept
   beside it as `harness_reported`. Verified end to end on Docker Desktop: the
   real proxy code in a container, the gateway on the host, a sealed client
   that knew only its resolver and a token. LiteLLM's OpenRouter provider,
   which the driver uses, was checked against the gateway on the host, plain
   and streamed. Not yet run: a full OpenHands session against a real
   provider.
2. **Per-route endpoints and local routes.** `llm.py` reads the route,
   `auth: none`, `context_window`, `dialect`. The first local server as a route. The
   setup card's "Local server" choice.
3. **Claude Code**, if step 0 says it can.
4. **Codex**, if step 0 says it can.
5. **Google's CLI.**
6. **INV-14 strict.** Whatever is left in `container_credentials` is an
   exception the console names, or it is gone.

## Tests

In the repo's own suites, each persisting:

- **`tests/test_harness.py`, built with step 1**, under "the model gateway".
  The name gets a stand-in only when the proxy has a gateway, and no other
  `.internal` name does. A connection to it on port 80 reaches the gateway, on
  any other port nothing, and every refusal that held before still holds,
  including `192.168.65.254` and loopback. Against a stub upstream: a made-up
  token, the key itself, no token and a revoked token all get 401 and reach
  nothing; the upstream sees the route's key and headers at the route's base
  URL and never the session token; tool calls pass through byte for byte both
  ways; a stream arrives as it is sent, not when it ends; usage from a plain
  and a streamed answer is recorded on the lease. The gateway binds loopback
  only and names the setting when its port is taken. The shipped default route
  opens a session with a session token in its env file, the gateway as its
  base URL, the key nowhere, and the token dead after close.
- **Step 2 adds** a spent budget and an oversized prompt refused before the
  upstream sees them.
- **`tests/test_config.py`.** Two API routes, one local with `auth: none`, each
  role's completion reaches its own route's endpoint. `dialect: openai` sends no
  `reasoning` or `provider` field. `context_window` is read from a stubbed
  `/props`.
- **`tests/test_invariants.py`, INV-14.** A fake session is prepared for every
  route in `factory.example.yaml`, and none of the configured secrets appears in
  any env file or copied file handed to the container, except on a route marked
  as an exception.
- **One end-to-end run** against a real local server, recorded the way the
  other real runs are, before it is called supported.

## What this does not change

- The egress proxy's policy for everything else: public addresses only.
- Gates still run with no way out.
- Fabrika's own model calls: still made from the server process, now through
  the route instead of the single `api:` block.
- INV-7: model names still live only in `factory.yaml`.
