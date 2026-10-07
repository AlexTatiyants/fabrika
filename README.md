# Fabrika

Fabrika is a software factory. You say what a feature should do and you rule
on what comes back. Everything in between — reading the repository, writing the
spec, building, testing, reviewing, repairing — is done by model agents, and
plain code decides what runs next.

What comes back is not a pull request. It is a **review packet**: the code,
plus a structured argument that it does what was specified. You audit the
argument and rule on a handful of decisions rather than reading the diff.

## Why it is built this way

An agent that writes code and then says it works has given you two things to
check instead of one. Fabrika is arranged so that the claims in a packet are
either facts measured by plain code, or arguments you can see the evidence
for:

- **Blind tests.** The agent that writes the acceptance tests sees the spec and
  never the implementation, so a passing test means the contract was met, not
  that the tests agree with the code.
- **An append-only record.** Nothing an agent reports can be edited or deleted
  later. A finding the repair loop fixed stays in the packet with its outcome
  beside it; a packet that got shorter as the loop worked would look better and
  be worth less.
- **The project's own checks.** Lint, types, tests: the commands the repository
  already runs, re-run at the commit a feature branched from, so the packet
  says whether a red check is this feature's doing or was there before.

### Three gates where a person decides

```
Project    a repo + how to test it + what environment it needs    gate 0: approve the checks
  Feature  one unit of work, its spec, its evidence, its packet    gate 1: freeze the spec
    Sandbox  a git worktree + a branch, isolated from every other  gate 2: rule on the packet
```

There is no input mid-run: a gate you can click through while the build is
going stops being a gate.

### Isolation in Docker

Every feature is built on its own git branch in its own worktree; your checkout
is never written to, and what you get back is a branch. Every agent session and
every check runs in a container on a Docker network with no route to this
machine, to other features, or to the internet. The one way out is a proxy that
reaches public addresses only, so a build cannot find your own dev server on
`localhost` and pass against the wrong app.

## How it works

```
intent → scout → interrogator → [HUMAN answers] → spec writer ⇄ spec checker
                                                        → [HUMAN freezes spec]
                                                                  │
                          ┌───────────────────────────────────────┴────────┐
                       build lane                                    verify lane
     architect ⇄ plan checker → [HUMAN plan review, only if disputed]   oracle (spec only)
                → workers ∥ → integrator
                          └───────────────────────────────────────┬────────┘
                    gates → breaker → attribution → qa → review ∥ → arbiter
                      ↑                                                │
                      └── repairers ∥ ← repair units (code) ←──────────┤
                                (bounded by config)                    │
                                       simplifier → rapporteur → packet ←┘
                                                                  │
                                                      [HUMAN rules on decisions]
```

The two lanes cannot see each other: the oracle writes the blind tests from the
spec alone while workers build. When they converge, the project's checks run,
a breaker attacks the result, and a review panel reads it. When the review
finds something, the factory tries to fix it, inside limits you set in
`factory.yaml` (rounds, budget, wall clock), and the rapporteur assembles the
packet. The whole design is in [docs/](docs/README.md).

## Requirements

- **Python 3.11 or later**, and **git**.
- **Docker**, for real runs. Every check and every agent session runs in a
  container; there is no fallback to running on this machine. Docker Desktop is
  what it is tested on.
- **A model provider.** Any OpenAI-compatible API: `factory.yaml` points at
  OpenRouter by default and reads the key from `OPENROUTER_API_KEY`. Every
  model name lives in `factory.yaml` and nowhere else, so moving to another
  provider or a local server is a `base_url` change plus edits to that file.
  Some roles can instead run on a coding-assistant subscription through its CLI;
  the routes are described, with their trade-offs, in `factory.example.yaml`.
- **Node**, only to run the console tests that execute JavaScript; without it
  they are skipped.

## Quick start

From a clone of this repository:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp factory.example.yaml factory.yaml
export OPENROUTER_API_KEY=sk-...

uvicorn factory.server:app --port 8077
```

Open <http://127.0.0.1:8077>. On a machine with nothing set up it opens on
**setup**, which checks Docker and the way out, connects accounts, builds each
account's harness, staffs the crew and sets the limits on a feature -- each
station lit from what it measured, and each change to `factory.yaml` said
before it is made. Instead of the environment variable you can paste the key
there or into the **Provider** card on the crew page; it is stored in
`.factory-credentials.json` beside `factory.yaml` (owner-only permissions,
ignored by git), and an environment variable, when set, takes precedence.

`factory.yaml` holds models, the API, the executor and where the factory keeps
its own files (`.factory/` by default). It does not name a repository:
repositories belong to projects, and you register one in the console by
pointing Fabrika at a directory. Gate 0 follows — a surveyor proposes the
checks the repository already runs and an environment to run them in, they run
on an untouched checkout, and you approve. Then write an intent for a feature.

The example config ships with `executor.kind: direct`: models return file
contents as structured output, with no tools. It is the cheapest way to see the
pipeline's shape. Set `executor.kind: command` to hand each unit to a real
coding harness — OpenHands, which the `default` route builds into a container
image and runs inside the unit's sealed container.

To use a config somewhere else, set `FACTORY_CONFIG=/path/to/factory.yaml`.

## Tests and lint

```bash
python -m pytest tests -q
ruff check factory tests
```

About twelve hundred tests. They do not test features; each asserts a property
that makes the system worth running — the invariants, isolation, the repair
loop, what the console draws — and whole runs of the pipeline with every model
call stubbed. The lint is pyflakes only; the settings are in `pyproject.toml`.
[docs/testing.md](docs/testing.md) says what each area proves.

**What CI covers, and what it cannot.** CI runs the suite and the lint above.
The suite has no Docker and calls no model: harnesses and CLIs are stand-in
scripts, every model call is stubbed, and checks run in temporary directories.
So CI cannot tell you that a real agent run still works, that a container is
still sealed, or that a screen still looks right. A change to a prompt, to
`factory/isolation.py`, `factory/egress*.py` or `factory/containers.py`, or to
the console's look needs a real run to verify.

## Project layout

```
factory/              the Python package
  pipeline/           the orchestrator: plain code that decides what runs next
  server/             FastAPI: the API, the console and the progress stream
  roles/              one prompt per agent, as Markdown, loaded at call time
  harnesses/          the OpenHands driver and the pins its image installs
  *.py                gates, containers, isolation and egress, projects and
                      onboarding, the evidence store, the as-built, config
console/              the web console: plain HTML, CSS and JavaScript, no build step
  app/  ui/  css/     the router and shared state, the screens, the stylesheets
  help/  vendor/      help topics; vendored preact + htm and CodeMirror
tests/                the suite, a file per area
docs/                 the design, and longer design notes
factory.example.yaml  the only file that names a model; copy it to factory.yaml
```

A fuller map, module by module, is in
[docs/architecture.md](docs/architecture.md#layout).

## Documentation

- [docs/README.md](docs/README.md) — the index
- [Architecture](docs/architecture.md) — the pipeline, the invariants, layout,
  known gaps, non-goals
- [Gates](docs/gates.md) — what each gate asks of you, and how a check is judged
- [Agents](docs/agents.md) — the kinds of agent, the review panel, the repair
  loop, the harness
- [Isolation](docs/isolation.md) — worktrees, containers, and the way out
- [Reading a repository](docs/repository-reading.md) — the scout and the as-built
- [The test suite](docs/testing.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Security

Fabrika runs code that models wrote. Read [docs/isolation.md](docs/isolation.md)
for what keeps it contained, and [SECURITY.md](SECURITY.md) for how to report a
vulnerability.

## License

MIT. See [LICENSE](LICENSE).
