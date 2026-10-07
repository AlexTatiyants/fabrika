# Agents

Twenty-two agents in the shipped config, each with a model in `factory.yaml`
and a prompt in `factory/roles/` (the re-survey shares the surveyor's). The
console shows them at `#/?roles`: the pipeline drawn as a diagram — agents,
deterministic steps, the two lanes that cannot see each other, the repair loop
and its bounds, the three human gates — over the roster of who does each
station. Clicking a node in the diagram or a row in the roster opens that
agent's settings and its prompt in place. The same page holds the routes each
call can take, the accounts that serve the models, the harnesses, the network
proxy and the provider key.

Models and prompts are editable from that screen. Edits are written back to
`factory.yaml` with a round-trip parser, so the comments survive: they carry the
reasoning about why worker effort must not be lowered for cost and why providers
should be pinned, and an editor that stripped them would make the config worse
every time it was used. The shipped `factory.example.yaml` is never written to —
the first edit promotes it to a real `factory.yaml`.

## Three kinds of agent

They differ by what they are *allowed to do*, which turns out to matter more
than what they are called.

**Pipeline agents** — every role in the shipped config but the reviewer, called
by name at a fixed point (in gate 0, in a build, or in an as-built reading),
because each carries a contract an invariant depends on: the oracle is handed
the spec and nothing else, the rapporteur's output goes through the restoration
pass, worker output must carry its disclosure, the arbiter routes and cannot
delete. Those cannot be added to or removed.

**Review agents** — declared in config, described below. One structured
completion each over a shared text bundle. No tools; nothing they say is
executed.

**Harness agents** — the agents that write files and run them. Under a real
harness (`executor.kind: command`) the workers, the integrator, the oracle, the
breaker, the repairers and the simplifier each get a worktree and a command
runner inside a sealed container. This is the class that makes the difference
between an adversary that *describes* an experiment and one that *runs* it, and
it is where a UAT step will go when it exists.

**The review phase is different: it runs whatever you declare.** Out of the box
that is one role, the reviewer: it reads the evidence bundle at high temperature,
sampled as many times as it asks for. Findings are de-duplicated by title keeping
the harsher reading, and the worst verdict is the one the packet carries.

The reviewer is an ordinary review agent — nothing in the code path is specific
to it. Add your own and it reads the same bundle alongside the reviewer; the
worst verdict across all of them still wins:

```yaml
roles:
  security:
    model: your/model
    review: true
    samples: 2
```

Plus `factory/roles/security.md`. Add one from the console and a starter prompt
is written for you; remove it and the prompt file stays, because deleting a
config entry should not destroy something you wrote.

A review agent cannot influence control flow (INV-6) and cannot hide anything
(INV-4), which is why this is the one part of the pipeline that is yours to
shape. It also means you can delete the reviewer — the tool will let you, and
the packet will simply have fewer findings.

## Sides and levels

Setting twenty-two models one at a time is how a crew ends up with a checker on
the builder's vendor. The **Agent configuration** screen, reached from the crew
page, sets it by two choices instead:

```yaml
staffing:
  build: one-route        # who serves the blue team
  check: another-route    # who serves the red team
  neither: build          # the agents on neither team follow blue
levels:
  one-route:
    deep: {model: <its strongest>, reasoning_effort: high}
    standard: {model: <its middle>, reasoning_effort: high}
    light: {model: <its fastest>, reasoning_effort: medium}
  another-route: ...
roles:
  worker:
    level: standard
```

Which team an agent is on is what it does (`team_of` in `factory/config.py`):
builders build, checkers give a verdict on someone else's work, and the rest
read, ask, route and present. Only those last can be moved onto a side. A level
resolves at load into the role's route, model and effort, the way a fallback
lane does, so nothing downstream knows levels exist. A `route`, `model` or
`reasoning_effort` written on a role with a level is a pin and wins.

Without a `staffing:` block a level is only a suggestion, which is how the
shipped example uses it: the presets and the suggested level for each role come
from `factory.example.yaml`, because a preset names a model and the package
names none.

## Prompts do not name the roster

Because that roster is yours, **no prompt names a review agent.** A prompt that
says "the adversary will read this" is a prompt that a supported config change
turns into a lie, and the failure is invisible: the model is simply told about a
colleague it does not have. Prompts describe the *consequence* instead — what
reaches the packet, what a human will spend attention on — which stays true
however you configure the review phase. A test enforces it.

Naming a **pipeline** agent is different and allowed, for two reasons: those
cannot be deleted, and in almost every case the reference is to data the agent
is holding rather than to an org chart. When `architect.md` says "the scout gave
you a `do_not_duplicate` list", it is naming a field.

The two exceptions are contrasts rather than handoffs, and they are the load-
bearing sentences in their prompts: the breaker is defined against the oracle
(green proves nothing; only red is a signal), and the repairer is defined against
the worker (every instinct that makes a good builder makes a bad repair).

One agent names nobody at all, and that is deliberate. The oracle is not told
there is a build lane, because being told would tell it there is an
implementation. This is also why there is no shared orientation preamble across
all the prompts: it would have to describe the pipeline, and describing the
pipeline to the oracle breaks INV-1.

## The breaker

The breaker reads the implementation and writes tests designed to break it, then
runs them. Its epistemics are the exact inverse of the oracle's, and the
asymmetry is the point:

- **Oracle: green is the signal.** It has never seen the code, so a passing
  blind test means the contract was met.
- **Breaker: red is the signal.** It has read everything, so a passing probe
  proves nothing. It is never counted as verification, never enters the
  traceability matrix, and never gates the run. A failing one is a finding with
  a real process behind it.

That is why reading the implementation does not compromise it: it is not making
the acceptance argument. It is making a smaller and harder claim — here is an
input this code does not survive — and the adversary can cite a failing test
instead of describing an experiment it had no way to run.

Its probes are written, executed, and then removed. They stay in the evidence
store and are re-applied every round, so a repair that breaks something already
probed shows up as a regression; but they never land on the branch, where the
project's own test gate would collect them and its signal would become a
function of what the breaker happened to write that round.

## The repair loop

When the review finds something, the factory tries to fix it:

```
review ∥ + breaker + gates → arbiter → repair units → repairers ∥ → re-check → …
```

The **arbiter** routes every finding — `repair`, `simplify`, `oracle`,
`escalate`, `needs_spec_change`, `out_of_spec`, or `not_a_defect` — and cannot
delete one. Anything it fails to route defaults to a human ruling, never to
silence. The repairable ones are cut into units with non-overlapping file
ownership by code, not by an agent (`plan_repairs` in
`factory/pipeline/repairs.py`): findings that name a shared file are one unit,
because two units writing one file is how a round produces a worse tree than it
started with. **Repairers** make the narrowest change that resolves them,
through the same executor a worker uses but with the opposite instincts: no
refactoring, no new abstractions, no scope widening, and a hard stop if the fix
is bigger than the finding. A finding on a file only the oracle may write goes
back to the oracle. Findings routed to `simplify` — code that is correct and
written twice, an abstraction with one user — are left to the **simplifier**,
which runs once after the loop converges; the checks, the blind suite and every
probe run again afterwards, so "behaviour did not change" is measured rather
than claimed.

The spec is never reopened. It was frozen by a human at gate 1, and a finding
that could only be resolved by changing it is an escalation by definition — the
loudest signal the system produces about itself, because it means the
interrogator did not ask something it should have.

Intermediate rounds re-check narrowly: only the agent that raised a finding
looks at whether the repair resolved it. Whenever the loop exits, for any
reason, one full panel runs first, because a repair can break something nobody
was looking at.

Four independent stopping conditions, all read from `factory.yaml` by plain code:

```yaml
rework:
  max_rounds: 2               # hard ceiling
  budget_usd: 4.00            # counted from the first gate run onward
  reserve_usd: 0.75           # never spendable by the loop
  wall_clock_minutes: 90
  max_attempts_per_finding: 2
```

The reserve is the one worth explaining. Without it the failure mode is a run
that spends everything repairing and then cannot afford to produce a packet at
all, which is strictly worse than not looping.

An attempt is only charged when one was made. A harness that produces no edits
has not attempted anything, and a round it silently ate would otherwise retire
every finding it was pointed at as *tried and unfixable* — the most expensive
sentence this loop can write, because a human reads it as "people looked at
this". So a unit whose two harness runs both changed nothing gives the attempt
back, and the failure is raised as a finding of its own: a harness that cannot
edit makes every round after it cost money and produce nothing, and that belongs
in front of a person on the first round, not the third.

The loop can also fail without taking the run with it. Everything it improves —
the branch, the gates, the blind tests, the first panel — is bought before the
first round starts, so a round that dies stops the loop, says so in
`stop_reason`, and the packet is built from what is already there.

A finding that survives its attempts is escalated for good: *attempted twice,
still there, here is what was tried* is worth more to a human than a third go at
the same wrong idea. And a round whose gates come back worse than the round
before it is reset off the branch — decided by code, comparing two numbers —
with the attempt left standing in the ledger, because the evidence store is
append-only and a reverted commit is still something that happened.

## Verifying it again

At gate 2 you rule on the work. Sometimes the thing you disbelieve is not the
work but the reading: gates that ran without their toolchain, a re-check that
judged a repair summary instead of the tree, a repair loop that spent its rounds
on a harness that was not editing. None of that is a reason to change a line of
code, and all of it is a reason not to believe a number in the packet.

**Verify again** — on the packet's own bar, beside the verdict —
keeps the frozen spec, the plan, the units as merged, the blind tests and the
branch with every commit on it, and starts the verdict pass from nothing: gates,
breaker, panel, arbiter, and a finding ledger with no rounds spent. It is not a
rebuild and not a repair round; the code it measures is byte for byte what you
were already looking at. It sits with the verdict rather than on the rework
screen because it is not an exit from the review: the rework screen is about
what you flagged in the work, and this is about the packet.

The packet it replaces is not deleted, edited or contradicted. It stays in the
ledger with a `revalidate` record beside it saying a human stopped trusting it,
and the new packet is written next to it — two readings of one branch, which is
the honest shape of what happened. (INV-11)

The budget does not reset. It is counted over the feature's whole life precisely
so that sending the same work round again cannot buy more of it than you agreed
to, and verifying it again is not a second allowance. The button takes two
presses for the same reason: one press buys about an hour of gates, containers
and review agents, and the second press is where that gets said.

## Executors

`executor.kind: direct` — the model returns file contents as structured output,
no tools. Enough to prove the pipeline's shape and cheap enough to iterate on
prompts with.

`executor.kind: command` — each unit gets a git worktree, the brief is written
to a task file, and a configured harness is invoked with `{task_file}` and
`{worktree}` substituted. Changed files are collected with
`git status --porcelain`, and a second model call reads the diff to produce the
decision log and the disclosure, because a coding harness will not produce
either on its own.

Two things that path has to survive:

**A harness that edits nothing.** Exit codes are read, and an empty diff is
treated as an answer that was not given rather than as a small change. The unit
is retried once — with the harness's own bookkeeping cleared, so stale chat state
cannot confuse it twice, and with the task file saying that the last run edited
nothing — and if the second run is also silent, the unit reports no files, the
exit codes and the harness log tail, and no narrative. A reflection model handed
an empty diff writes a paragraph about work that did not happen, and every stage
downstream reads that paragraph as the record.

**A model that cannot describe the work.** The harness writes the code before
the reflection call exists, so losing the unit because its narrator ran out of
room throws away a repair that is already on disk. The ask is retried smaller,
and if that fails too the files are kept and the decision log is recorded as
missing. Missing is a hole a human can see; invented is one they cannot.

Both were found on real runs, in build and repair mode, and both are fixed and
tested.

## The harness

**The harness is OpenHands**, driven through its SDK by
`factory/harnesses/openhands_driver.py`. It runs inside the unit's sealed
container: the `default` route in `factory.example.yaml` builds an image from a
stock `uv` image, installs a standalone Python 3.13 and the pinned set in
`factory/harnesses/requirements-openhands.txt`, and starts the driver there, so
its dependency tree cannot move this project's pins. The pins are
`pip freeze` from a local `.venv-openhands`
(`python3.13 -m venv .venv-openhands && .venv-openhands/bin/pip install openhands-ai`),
minus the macOS-only packages; regenerate them the same way when that venv
moves.

The reason it is not a CLI harness such as aider is one property. Every CLI
harness is a subprocess, and a subprocess that exits has already ended its turn:
a model that answers with a question can be restarted from nothing, never told
to continue. aider answered "What would you like me to change?" and exited zero
on four units across two runs, and no prompt, flag or file-passing changed that.
The driver owns the loop, so a turn that wrote nothing is refused *in the same
conversation*, with everything the agent has already read still in front of it.
