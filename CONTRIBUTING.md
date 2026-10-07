# Contributing

Thanks for wanting to help. This page covers how to set up, how the code is
organised, and what a change needs before it is merged.

## Setting up

You need Python 3.11 or newer, git, and Node (a few tests run console code in
it). Docker is needed for real runs but not for the test suite.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Then check that everything passes before you change anything:

```bash
python -m pytest tests -q
ruff check factory tests
```

The suite takes about two minutes. It needs no Docker, no provider key and no
network: harnesses, CLIs and containers are stood in for by local scripts, and
every test gets a credentials file of its own (`tests/conftest.py`).

## Where things are

| Path | What it is |
|---|---|
| `factory/pipeline/` | The pipeline: phases, checks, the finding ledger, the packet |
| `factory/server/` | The FastAPI app, its routes grouped by what they serve |
| `factory/*.py` | Everything else: config, gates, executors, containers, isolation |
| `factory/roles/` | The prompt for each agent role |
| `console/app/` | The console: classic scripts, loaded in the order `index.html` lists them |
| `console/ui/` | The review screens: Preact + htm modules, loaded on demand |
| `console/css/` | The stylesheet, one file per area, linked in order |
| `tests/` | The suite; `tests/helpers.py` holds what the test files share |
| `docs/` | How it works, and design notes |

The console has no build step and must stay that way: no bundler, no
transpiler, nothing to install to run it. Words for stages, verdicts and
severities live in `console/app/vocab.js` and nowhere else. The visual design
is written down in [DESIGN.md](DESIGN.md).

## Making a change

- **Tests.** A change that alters behaviour comes with a test that fails
  without it. Tests are named as sentences about what must be true
  (`test_a_second_press_does_not_start_a_second_run_in_the_same_worktree`), and
  their docstrings say why it matters. Prefer exercising behaviour over
  asserting on source text; where a test must read source, it reads it through
  `tests/support.py`, never by opening a file directly.
- **Comments say why, in the present tense.** This codebase explains its
  reasons at length, and that is deliberate: a reader should be able to tell
  why a line is there and what breaks without it. A comment describes what is
  true now. How it came to be belongs in the commit message.
- **Commit messages** have a subject that states the outcome as a plain
  sentence ("A restart re-arms scheduled builds"), and a body that says what
  was wrong and why this is the fix.
- **Lint.** `ruff check factory tests` must pass. It is set to pyflakes only:
  undefined names, unused imports and variables, repeated keys.
- **Isolation.** Nothing under `factory/` may run an agent, or anything an
  agent wrote, outside a container. Tests enforce this; see
  [SECURITY.md](SECURITY.md) and [docs/isolation.md](docs/isolation.md).

## Reporting bugs

Open an issue with what you did, what happened and what you expected. For
anything that could be a security problem, follow [SECURITY.md](SECURITY.md)
instead of opening a public issue.

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE).
