# Gates

Three places a person decides, and the checks that stand between them. Gate 0
approves what a project measures; gate 1 freezes what a feature must do; gate 2
rules on what came back. The checks — the project's own commands, run by plain
code — are the only facts in the packet.

`factory.yaml` holds models, the API, the executor and where the factory keeps
its own files. It deliberately does **not** name a repository: repositories
belong to projects, and projects are registered at runtime.

## Gate 0: approve the checks

In the console, point the factory at a directory. A surveyor reads it and
proposes the gates the repo already runs — from its CI workflow, its Makefile,
its package scripts — plus an environment for them to run in. Those gates then
run on an untouched checkout, and you approve the result.

The console says this in three plain steps, because "survey" and "baseline" are
this system's words and not everyone's: **read the project** (a model proposes
the check list), **run the checks** (on code nobody has touched, which is what
proves the commands work and shows what they say about an untouched repo), then
**approve**. There is one reading button, and it always proposes a diff you
accept item by item — a reading that replaces your gate list wholesale is
offered only when a survey failed and there is no list to diff against.

**What you are approving is that the gates work, not that the repository is
clean.** A gate that runs and reports 129 lint errors is a working gate: every
feature re-runs a failing gate at the commit it branched from, so its packet
says whether the change caused the failure or inherited it. That is information
from the first feature, on a repository that has never been green.

A gate that *cannot run* is refused. Its tool is not in the environment, or the
command never started, or it timed out — it will be red for every feature ever
built here and will never report on anything. That is worse than having no gate,
because a red row looks like coverage. The classification is heuristic and says
so: exit 127, a missing module, a launch failure and a timeout are the cases it
is sure about, and anything it cannot settle is surfaced rather than guessed.

A green baseline is not required. Requiring one would make a repository with
pre-existing failures unable to onboard at all — and unable to use the factory
to clean itself up, since a feature needs an approved project. Differential
attribution (below, under **A red gate on its own says nothing**) removes the
reason for it.

A check that is red on untouched code is explained before any feature starts:
code says what kind of red it is, and the `diagnose` role proposes a cause and
up to three fixes, each tried in the baseline's throwaway checkout before a
person sees it (`factory/diagnosis.py`).

### Gates, scaffolding, recommendations

A tool the surveyor notices can land in three places, and they are not
interchangeable:

- **`gates`** — the project runs this today. A gate is a contract.
- **`scaffolding`** — it would run today if one small file existed. The
  surveyor writes the file; you choose whether to apply it.
- **`recommendations`** — it should run and does not, and no single file
  changes that. Advice. Blocks nothing.

The split exists because collapsing it is expensive. A gate the repository has
never satisfied fails on every feature forever, and a red row that never changes
teaches people to ignore red. The rule the surveyor is given: a `.ruff_cache/`
entry in `.gitignore` means somebody once ran ruff — it does not mean the
project holds itself to ruff, and it belongs in `recommendations`.

Recommendations have to cite code. *"FastAPI projects should use mypy"* is a
template and gets skimmed past; *"`app/routers/queue.py` splats
`**dict[str, object]` into a typed constructor in two places"* is a finding
about this repository. Same evidence bar the review agents are held to. And the
existing rule holds: recommend the harness, the config, the CI step — never the
tests themselves.

### When the gate list goes stale

Gates come from what a repository already runs, so they are only as current as
the reading that produced them. A project that pulls in a DSL with its own
linter gains a surface nothing gates — and every existing gate stays green
throughout, because none of them was ever asked about it. Nothing fails;
coverage quietly stops being complete.

You cannot watch for the tool, because you cannot enumerate the tools. You can
watch for the shape of tooling changing, and tools announce themselves: a config
file, a manifest entry, a CI step, a task-runner target. The survey records the
commit it read and the tooling files it was shown, and the detector is a
`git diff` against a glob list. No model, no tokens.

Two signals, and they are different failures:

- **drift** — a tooling file appeared or changed since the survey
- **unseen** — a tooling file exists that the survey was never shown, because
  the digest is bounded. A surveyor that never saw `druff.toml` did not decline
  to gate it.

The detector never re-surveys on its own. It puts a sentence on the page, and
re-surveying stays a human's call — because it changes what the project
measures, and a detector that could do that unasked would be a model quietly
rewriting the contract.

Honest limit: a tool with no config file and no CI entry is undetectable. If
somebody runs `druff` by hand, nothing here will ever know.

### Asking what should change

When the detector says the tooling moved, **What should change?** runs one
bounded call — the current gates, the environment, and the contents of the files
that moved — and returns a **diff**, not a survey. Every entry is a proposal
with an action, a reason and the file that makes the case:

- **`add`** — this repository now verifies something no gate covers
- **`change`** — the command moved; the gate still means what it meant
- **`remove`** — the tool is gone from the repository

Nothing is applied. Each change is a checkbox, the default is off, and **what
you leave unchecked is recorded as a rejection** alongside what you accepted.
That asymmetry is deliberate: the same call that can add a gate can drop one,
and a re-survey that applied itself would be a way to make an inconvenient
result disappear — the project-level version of a presenter filtering findings.
The prompt says it in as many words: *a failing gate is a working gate reporting
bad news*, and `remove` is for a tool that no longer exists, never for a gate
that keeps going red.

Accepting a gate change clears the baseline and returns the project to gate 0,
because the result you approved was measured against a gate list that no longer
exists.

**Survey from scratch** re-derives everything. It is the right call when a
project changed fundamentally, and the wrong one the rest of the time: it
re-proposes every gate, and a human then has to re-adjudicate a list they
already approved to find the one line that moved.

## Gates 1 and 2

Write the intent, answer the interrogator, freeze the spec, approve it. The
build runs to completion and stops with a packet. There is no mid-run input —
deliberately, because a gate you can click through mid-run stops being a gate.
(The one stop inside a build is plan review, and it happens only when the plan
checker disputes the architect's plan.)

At gate 2 you rule on the packet. What you can do there besides accept or
reject — flag findings for the repair loop, or verify the packet again — is in
[agents.md](agents.md#verifying-it-again).

## How a gate is judged

Shell commands run concurrently in the repo after both lanes converge. A gate is
either a process that exited zero or a number that cleared a threshold. Those
are the only facts in the packet; everything else in it is an argument.

### A red gate on its own says nothing

On a repository with pre-existing failures — 129 lint errors, a type checker
nobody has ever run to green — every feature's packet carries the same red
gates, and no reviewer can tell which of them the change caused. So a failing
gate is re-run at the commit the feature branched from: same command, same
image, same project.

```
failed here, failed there  →  not this feature's doing
failed here, passed there  →  this feature did it
```

Only failing gates, only when a base commit is known, and cached on the project
ledger per commit — three features branching from the same commit ask the same
question and the second and third do not pay for it. A green run costs nothing.

This is what lets a project onboard without being clean first. Gate 0 does not
have to prove the repository is green; it has to prove the gates *produce
results*. Whether 129 lint errors are acceptable stops being a gate 0 question
and becomes a per-feature one, answered by comparison. It is also the ratchet
you cannot express with `threshold`, which is a floor rather than a ceiling: the
previous run is the ceiling.

### An exit code says the process was happy

It does not say a test ran. A suite whose blind tests were skipped, deselected
by a marker, or never collected at all exits zero, and reads as green. So the
blind suite is run **one file at a time**: the surveyor supplies test-file
rules — `command`, the command that runs a single test file, with `{path}` where
the path goes — and each file's exit code is that file's verdict.

Which splits the work four ways, because session work must not run per file:
`environment.setup` installs dependencies once; `environment.services` declares
what has to be **running** (a command plus a `ready_when` that exits zero when
it is up); `environment.test_prepare` migrates and seeds once after the services
are ready; and the test-file command is the invocation and nothing else. The
harness starts services, polls, and tears them down in a `finally`. The
alternative is a gate command carrying `nohup uvicorn ... & UPID=$!`, a `curl`
loop and a `kill $UPID` — ten concerns in one string, copied between gates
until one copy loses the `kill`. Most projects declare no services and no
prepare at all. The oracle already recorded which criteria each file covers, so
attribution falls straight out. No parsing of a runner's output, no dependency
to install.

A rule can also say `report`: the same file run so that it writes a JUnit XML
report, which turns "the file failed" into "this test failed". One test
disagreeing about one field can otherwise take every criterion tagged to its
file down with it, because a file is the smallest thing that can be judged.
JUnit XML is a documented interchange format the runner was *asked* to emit —
versioned, machine-facing, and the same shape from pytest, vitest, jest,
playwright, go and dotnet — and a report that is missing, empty or malformed
yields nothing, so the file's exit code remains the verdict. When the survey
left `report` empty for a runner that can write one, the `reporter` role asks
the runner itself, in the project's own container, and the answer is kept only
if a canary test proves it (`factory/reporting.py`). Nothing is applied: a
proven command is a proposal a person accepts.

**Nothing in the orchestrator recognises a test runner**, and an invariant test
enforces it over prose-stripped source. A table of regexes that knows what
pytest, jest, vitest, unittest and go print when they finish goes dark without
saying so: vitest changed its summary line in a minor release, the table stopped
matching, and a whole gate's signal was lost. Which runner a repository uses is
the surveyor's business, approved by a human at gate 0. A command is the thing
every project already has.

Two limits are stated rather than papered over. Without a `report`, a file whose
tests were *skipped* exits zero exactly as a passing one does, so those skips
are not detected — the exposure is bounded by the oracle being told never to
skip a criterion. And whether a runner fails when handed nothing to run is
**measured at gate 0** against the project's own command, not assumed: "most
runners exit non-zero on an empty selection" is true, and is exactly the kind of
belief that put a framework table in the orchestrator. A project measured as
tolerating an empty selection gets a finding saying so, rather than a criterion
table that means less than it appears to.

The point is not to cry wolf — an ordinary green run still passes — but a
criterion marked verified by a test that never ran is worse than one marked
untested, because it carries a claim.

The breaker's probes are not gates. They run separately, they never gate the
run, and a failing one is a finding a human rules on.

Note the mutation gate. Coverage tells you which lines ran; mutation score tells
you whether the tests would have noticed if those lines were wrong. A suite can
sit at 95% coverage and 20% mutation score, which means it executes the code and
asserts almost nothing about it.

Where the gates run — the image, the container, the network — is in
[isolation.md](isolation.md#where-the-gates-run).
