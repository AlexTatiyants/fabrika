# Architecture

A hands-off pipeline: a human defines a feature and rules on the result. Every
step in between is done by model agents coordinated by deterministic code.

The output is not a pull request. It is a **review packet** — the code plus a
structured argument that it does what was specified. The human audits the
argument and rules on a handful of decisions rather than reading the diff.

## The pipeline

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

The phase names are the ones the console draws, in the order they run
(`PHASE_NAMES` in `factory/pipeline/text.py`). Cutting a round's findings into
repair units is arithmetic, not an agent (`plan_repairs` in
`factory/pipeline/repairs.py`); the simplifier runs once, after the repair loop
converges, on the findings the arbiter routed to `simplify`. The agents
themselves are described in [agents.md](agents.md).

## Three layers

Three layers, each with one job:

```
Project    a repo + how to test it + what environment it needs    gate 0: approve the checks
  Feature  one unit of work, its spec, its evidence, its packet    gate 1: freeze the spec
    Sandbox  a git worktree + a branch, isolated from every other  gate 2: rule on the packet
```

Features are built concurrently. Each one gets its own worktree branched from
the project, so two features cannot see or corrupt each other's files, and the
project's own checkout is never written to. What you get back is a branch.

When the review finds something, the factory tries to fix it before it stops.
The loop is bounded by numbers you wrote down — rounds, budget, wall clock — and
it can only **add** to the record: a repaired finding stays in the packet with
its outcome beside it. A packet that got shorter as the loop worked would look
better and be worth less.

What each gate asks of a person is in [gates.md](gates.md); how a feature is
kept apart from every other one, and from this machine, is in
[isolation.md](isolation.md).

## Nothing is seeded

There is no fixture: no fictional project written straight to the evidence
store so the console can be judged with no API key and no tokens spent. Every
screen shows a run that actually happened, which is the only kind this console
was ever asked to render honestly.

A fixture nobody re-runs is a second description of the system, drifting from
the first. It can describe a seeded project for months after the fixture has
been rewritten to emit an entirely different one, and nothing fails — because
no test reads prose.

## The invariants

These are the reason the system works. Several of them look like unnecessary
friction. Leave them alone.

**INV-1 — the verify lane cannot see the implementation.** Two mechanisms, and
it needs both. `verify_context` builds the oracle's prompt from a `Spec`, a list
of package names measured in the environment, and three descriptions that come
only from what a human approved at gate 0: `ProjectState.runtime_contract()`,
the test setup the survey recorded, and the project's approved testing guides.
Nothing it accepts is a path. The environment arguments are safe in different
ways, and the difference is the point. The package list is safe by *shape* —
nothing that is not shaped like a package name survives `installable_names`. The
runtime contract is prose and cannot be filtered that way, so it is safe by
*provenance*: it is a pure function of that one project's record — the services
it declares, the variables its own test commands set, and a human's sentences
about it — and the call site is pinned structurally so no later edit can
assemble it from anything read off disk. And the oracle writes its suite in a
`BlankTree` — an empty repository made for the purpose, checked by
`assert_blank` before the agent starts — so there is no implementation on the
filesystem for it to open either. The prompt half is an omission and could be
undone by a helpful paragraph; the filesystem half cannot. The moment a test
author can read the implementation, it starts testing what was built instead of
what was asked for, and green tests stop carrying information.

Blindness to the *implementation* is the invariant. Blindness to the
*environment* is not, and is not free: it cost one run every criterion it had.
An oracle told which packages it could import and nothing about what would be
running invented a live system that had to be provisioned for it, and its whole
suite stopped in the fixture that demanded one. So the oracle is told what its
tests can reach, and given `requires` to say what it needed and did not get —
which is reported as a finding against the harness rather than left to read as
a broken feature.

**INV-2 — evidence is append-only.** No update path, no delete path. A late
agent must not be able to launder an early agent's disclosed gap. State changes
are new records; `latest` wins.

**INV-3 — traceability is computed, never asked for.** Which criteria are
implemented, tested or orphaned is a pure function of worker decisions and
oracle test tags. If a model returns a trace, it is discarded.

**INV-4 — the rapporteur presents; it cannot omit.** Everything it drops is
restored. Files it fails to classify default to `novel`. A presenter that can
hide things is the most dangerous agent in the pipeline, so its power is
structurally limited to ordering and labelling.

**INV-5 — no agent returns free text.** Every role has a schema, validated on
the way back, with the validation error fed to the model on failure. Three
attempts, then fail loudly.

**INV-6 — no model decides control flow. The orchestrator's branches are a
fixed, readable function of structured data.** Models do work inside a phase;
they never choose the next one. The repair loop is the only place this is worth
restating: its trip count depends on what the agents found, but every stopping
condition — rounds, budget, wall clock, attempts per finding — is a number in
`factory.yaml` that plain code reads. A model produces findings and routings; it
never decides whether to go round again.

**INV-7 — model identifiers appear in exactly one file.** Swapping to a local
server is a `base_url` change plus edits to `factory.yaml`.

**INV-8 — gate 1 is a hard process boundary.** Intake runs to completion and
stops. The build runs to completion and stops.

**INV-9 — worker disclosure is mandatory and structured.** Four separate lists,
not prose. An empty disclosure is surfaced in the packet as a signal, not as a
success.

**INV-10 — prompts are versioned files.** One markdown file per role, loaded at
call time. When output quality shifts you need to diff the prompt to know
whether the model changed or you did.

**INV-11 — rework only adds.** Every finding from every round reaches the packet
with its outcome attached. A repaired finding shows as repaired, with the round
and the commit, never as absent. Nothing in the loop can shorten the list: the
arbiter routes and cannot delete, an unrouted finding defaults to a human, a
missing re-check verdict never closes anything, and a round that gets reverted
withdraws the repair claims it made. This is INV-2's concern moved into time —
a later round laundering an earlier round's disclosure — and it is the property
that makes it safe to let the factory fix its own work.

**INV-12 — a repair cannot touch the verification surface.** The oracle's blind
tests, the breaker's probes and the configuration that decides which tests run
are read-only to a repairer, enforced by refusing the write before it lands. The
cheapest way to pass a test you cannot satisfy is to change the test, and a loop
that could do that is a machine for manufacturing green. The attempt is recorded
and shown in the packet as a finding about the repairer.

**INV-13 — the as-built never reaches the verify lane.** The as-built is a
checked description of the code as it stands, and the oracle is judging that
code. However carefully it is checked, a description of the implementation is
what INV-1 keeps from a test author, and a stale one would manufacture
agreement. No agent is handed it today; when the build lane is, it will be as
claims to check, never as facts.

Each invariant has a test; see [testing.md](testing.md).

## Three things not to improve

All three will feel like obvious wins. They are the failure modes this design
exists to prevent.

**Do not pass the repo to the oracle "so the tests actually compile."** That
destroys blind verification, which is the entire point of the system. The oracle
writing a test against an interface it had to guess at is information. The
oracle writing a test against the code is not.

**Do not let the rapporteur filter findings "to reduce noise."** That hands the
presenter editorial control over what the human sees. Ordering is how it
expresses judgment; omission is not available to it.

**Do not drop repaired findings from the packet "because they are fixed."** It
is the single most tempting change in the system and it destroys the thing the
loop is for. What was found is as much a fact about this work as what is left:
six findings of which four were repaired is a different piece of code from two
findings, and a reviewer who cannot tell those apart has been handed a summary
rather than evidence. The loop's value is that it fixes things *and says so*.

## Layout

```
factory/
  schemas.py        every agent's output contract
  config.py         models, routes, API, paths, prompt loading
  llm.py            one entry point: ask(role, prompt, schema)
  store.py          append-only JSONL
  projects.py       the project registry and its ledger
  onboarding.py     gate 0: survey, environment, baseline
  diagnosis.py      why a check is red on untouched code
  reporting.py      finding the command that makes a runner write JUnit XML
  guides.py         the repository's own guides (AGENTS.md and the like)
  dependencies.py   what a feature changed about what the project depends on
  sandbox.py        one worktree and branch per feature
  workspace.py      repo digest, worktrees and the air gap (verify_context)
  gates.py          shell commands behind a runner protocol, no model
  containers.py     images, and the container a gate runs in
  isolation.py      the rule that nothing an agent wrote runs on this machine
  egress.py         sealed networks, and the proxy that is their only way out
  egress_proxy.py   the proxy itself: public addresses, nothing else
  agentbox.py       a sealed container for every CLI completion
  unitenv.py        one prepared environment per authoring unit
  sessioncreds.py   getting a harness's sign-in into its container, and nothing else
  executors.py      how a worker actually builds
  sendback.py       a worker measured before its unit is accepted
  testquality.py    tests that cannot fail, read from the test file
  preview.py        seeing the running app; doorway.py is its door onto this machine
  pipeline/         the orchestrator, the finding ledger and the repair loop:
                    pure functions a file per group (ledger.py, packet.py,
                    repairs.py, trace.py, checks/ by theme), and factory/ -- the
                    Factory, one mixin per stretch of the run
  asbuilt.py        reading a repository into its as-built, file by file
  asbuilt_graph.py  the join: links checked, subsystems, parts, reach, findings
  asbuilt_pages.py  the as-built as Markdown a person reads in the repository
  server/           FastAPI: API + console + progress stream, a module per route group
  harnesses/        the OpenHands driver and the pins its image installs
  roles/*.md        one prompt per role, loaded at call time, plus recheck.md
                    which is appended to a review agent's own prompt when it is
                    asked whether a repair landed, and harness.md, the preamble
                    every coding-harness session gets
console/            plain HTML, CSS and JavaScript. No build step.
  app/              the hash router and the screens' shared state, as classic scripts
  ui/               screen modules
  css/              the stylesheets, linked in order by index.html
  help/             the help topics, one plain HTML file each
  vendor/           preact + htm and CodeMirror, vendored
                    A left rail lists active features grouped by project; the
                    screen itself is still chosen by stage, never by navigation.
tests/              the invariants, plus a test file per area and a stubbed end-to-end run
docs/               this design, and the design notes
factory.example.yaml
```

Storage:

```
<evidence>/<project>/evidence.jsonl                          the project ledger
<evidence>/<project>/features/<feature>/evidence.jsonl        one per feature
<sandboxes>/<project>/<feature>/                              the worktree
<evidence>/<project>/artifacts/as-built-cache/                what the reader said, by file content
<repository>/.fabrika/as-built/                               the as-built, committed on your press
```

## Known gaps

**The repair loop's prompts are where the risk is.** The loop has run against a
real harness, and the mechanical failures that run exposed are closed. What one
run cannot tell you is whether the repairer's instincts hold: a repairer that
refactors on the way past produces a diff nobody can evaluate, and no invariant
stops it. One measured run repaired nine findings over three rounds and left
sixteen standing, which is a number worth beating and not yet a diagnosis.

**No UAT step.** A person can open a feature's running app from review and step through the
recordings a project's own runners leave, but no agent uses the app the way a
user would. A UAT agent cannot be a review agent — those get one structured
completion over a text bundle, with no tools — but the harness agent class the
breaker introduced is the right shape for it, and it belongs in the converge
band next to `gates` and `qa`.

**Context degradation across rounds is not addressed.** Each agent gets one shot
per round with a fresh call, but nothing prunes what it is shown as the ledger
grows. On a feature with three rounds and forty findings the evidence bundle is
large, and no measurement has been taken of what that does to the review.

What is known about the harness, and why it is OpenHands, is in
[agents.md](agents.md#the-harness).

## Non-goals

No auth, no multi-user, no database, no durable execution engine, no build step
for the console, and no mid-run human input. Resume is "skip any phase already
in the evidence store", plus the finding ledger, so a resumed build does not
re-open everything the last attempt repaired.

The repair loop does not change this. It runs between gate 1 and gate 2 with no
human in it — that is what makes it a gate — and the loop stopping is not the
same as the work being right. It clears the mechanical findings so that what
reaches you is the part that actually needed a person.

The tool does not manage your attention. It will not throttle how many features
you run, rank work across projects, warn you that two features overlap, or
resolve a merge conflict between them. Those are human decisions, and a tool
that made them for you would be wrong more often than you would be.
