# The test suite

```bash
python -m pytest tests -q
```

And the lint the codebase is held to -- pyflakes, nothing stylistic; the
settings are in `pyproject.toml`:

```bash
ruff check factory tests
```

About twelve hundred tests: one or more per invariant, supporting properties,
isolation properties, config-editing properties, repair-loop properties,
console-coverage properties, and full runs of the pipeline with every model
call stubbed. They do not test features; each one asserts a property that makes
the system worth running, and a failure means the design has been violated even
if everything still works.

The suite has no Docker and calls no model. Harnesses and CLIs are local
scripts written to stand in for real ones, and checks run in temporary
directories; `tests/conftest.py` is the one place allowed to let them run on
this machine (see `factory/isolation.py`). It also gives every test its own
credentials file holding a key that is no key, and clears `OPENROUTER_API_KEY`
for the duration, so no test reads or writes the credentials of whoever runs
the suite. Some console tests run JavaScript and need Node; without it they are
skipped.

## Where the tests are

| File | What it covers |
|---|---|
| `tests/test_invariants.py` | the invariants (`test_v1_…` to `test_v13_…`, `test_inv11_…`, `test_inv12_…`), and the properties next to them |
| `tests/test_pipeline.py` | the ledger, findings, traces, rework and the packet |
| `tests/test_pipeline_e2e.py` | whole runs with every model call stubbed |
| `tests/test_gates.py` | checks: running them, the services they need, what their results mean |
| `tests/test_harness.py` | agents and where they run: harnesses, routes, meters, isolation, containers |
| `tests/test_survey.py` | reading a repository: the survey, projects, guides, dependencies |
| `tests/test_workspace.py` | checkouts: worktrees, sandboxes, git, what a change measures |
| `tests/test_config.py` | roles, routes, prompts and the factory file |
| `tests/test_server.py` | the server's routes, what each answers and refuses |
| `tests/test_console.py` | what each console screen draws, and its stylesheet |
| `tests/test_asbuilt.py` | the as-built, read file by file |

`tests/helpers.py` and `tests/support.py` hold what the test files share.

## What the suite proves

A sample, by the property rather than the file. The `V-` and `INV-` rows test
the invariants in [architecture.md](architecture.md#the-invariants), and their
tests are named for the row (`grep -rn "def test_v1_" tests`). The `V-` numbers
are the suite's own and part company with the invariants' from V-6, so each row
from there names the invariant it holds.

| Test | Proves |
|---|---|
| V-1 | `verify_context` takes a `Spec` and descriptions of the world the tests will run in — one filtered to package names, the rest from what a human approved at gate 0 — and returns `str`; no reference to the repo exists anywhere on the verify lane's call path |
| V-2 | `EvidenceStore` exposes no update, delete, remove or set; two appends both survive with increasing `seq` |
| V-3 | Traceability is computed from worker decisions and oracle tags; a model-claimed trace is discarded |
| V-4 | Decisions, findings and files dropped by the rapporteur are restored; an unclassified file defaults to `novel` |
| V-5 | A malformed body is retried with the validation error fed back; three failures raise |
| V-6 (INV-7) | No model identifier appears anywhere outside `factory.example.yaml` |
| V-7 (INV-10) | Every role the pipeline calls has a prompt file in `roles/` |
| V-9 (the review panel, not an invariant) | Review samples de-duplicate by title, the harsher reading survives, and the worst verdict wins |
| e2e | A whole run with every model call stubbed — including one repair round, and a stub that raises if repo text or implementation source ever reaches the oracle's prompt |
| INV-11 | A repaired finding stays in the packet with its outcome; the arbiter cannot drop one; a finding re-raised in a later round is one finding, not two; a missing re-check verdict never closes anything; a reverted round withdraws its own repair claims |
| INV-12 | The oracle's blind tests, the breaker's probes and the test configuration are refused to a repairer before the write lands, not reverted after |
| V-13 (INV-13) | The verify lane's call path names nothing of the as-built, and nothing of it is committed onto a feature branch |
| ab | The as-built draws only links code confirmed and counts the rest; every file is placed once or named; the same graph gives the same groups whatever order it is read in; a big file is shown in parts with its seams counted; every way in lands in exactly one capability; a refresh reads only changed files and commits only its own directory; a feature's packet says what it changed in the system |
| exe | A gate that exits zero over a suite that ran nothing is `not_collected`, not `passed`; a named skipped blind test does not verify its criterion; an ordinary green run still passes |
| iso | Two features in one project cannot touch each other's files; two projects keep separate ledgers, sandboxes and repos; a feature cannot start in a project with no approved baseline |
| rev | A repair round that makes the gates worse is reset off the branch, the attempt stays in the ledger, and the finding goes back in front of a human |
| con | The console knows about every agent, every phase and every record kind: the roster orders each pipeline agent, every phase is in a band, the diagram draws everything called by name, and no record falls through to its raw key |
| rst | No prompt names an agent the config can delete, so deleting a review agent cannot leave a prompt describing a colleague that does not exist |
| atr | A gate that was green at the base commit and is red now is a blocker attributed to this feature; one that was red at both is a minor note attributed to nobody; the base commit is measured once and shared across features |
| g0 | A gate that ran and failed is told apart from one that never ran; a project with red-but-working gates approves and records which were red; one with a gate that cannot run is refused; the console refuses exactly what the server does |
| rec | A recommendation never reaches the gate list, and the surveyor is told the difference between the three places a tool can go |
| gate | A `parse_metric` with no capture group is refused: it reads no number on any output, and with a threshold it fails a command that succeeded. A threshold with nothing to read, and an unparseable pattern, are refused the same way |
| surv | The surveyor ranks its evidence — a CI step over a dependency over a config file over a gitignore entry; an inferred dev dependency is proposed as scaffolding; `compose` is resolved before any built image; every environment kind the schema allows is either offered or ruled out with a reason |
| drift | A new linter's config file is noticed; ordinary source commits are not; a tooling file the survey never read is reported separately; a survey with no recorded commit says so rather than claiming fresh; the detector calls no model and starts no survey |
| prop | Only the gate changes a human checked are applied; what they left unchecked is recorded as a rejection; accepting one clears the baseline; an `add` with no gate definition is refused rather than guessed at |
| env | A feature runs the environment's setup before its gates; once per sandbox where the runner carries it, every round where it does not — a container's toolchain goes down with the image and its database with the volume — and again if a reverted round cleaned it away |
| cfg | Editing a role preserves every comment, never writes to the shipped example, and cannot delete a pipeline role; an added review agent's findings reach the packet and cannot reach the verify lane; the orchestrator calls no review agent by literal name |

## What the suite cannot see

It never runs a real agent, never starts a container and never looks at a
screen. A change to a prompt, to how a container is sealed, or to how the
console looks is verified by a real run, not by this suite.
