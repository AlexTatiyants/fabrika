# Diagnose a red check

A check this project runs is red on code nobody has touched: the commit every
feature will start from. Until it is explained, a person cannot tell whether the
code is broken, the check is set up wrong, or this environment measures
differently from the project's own runs. You are given the check, what it printed,
its settings at this commit, the environment it ran in and what the project's own
runs use. Say why it is red, and what would make it honestly green.

## How to read it

- **Read the output before anything else.** Most causes are said in it, often in
  its first lines and summarised in its last. Quote what you rely on.
- **A floor the project sets for itself and does not meet on its own code is a
  contradiction.** The project's own runs would be red too, or this environment
  measures differently. Before you conclude the code is short of its own bar,
  look for why the measurement would differ here: a different language version
  than the project pins, a tool whose defaults change between versions, a
  setting that only matters on one of them, a service or variable the project's
  runs have and this one lacks.
- **Compare versions.** What ran here is listed under *Versions where it ran*;
  what the project's own runs use is in its CI and version files. A difference
  is a lead, not a verdict: say what it would change and why.
- **Know the tools.** Coverage that drops sharply only here, for code that runs
  after awaiting a database, often means the tracer is not following greenlets
  or threads (`concurrency` in coverage.py's settings). A test runner that finds
  nothing exits differently by runner. A linter whose rule set grew with its
  version reports findings the project's pinned version never saw. Use what you
  know; say it plainly.
- **Ask for files only when the answer depends on them.** Name them in
  `need_files` with no fixes, and you will be shown them once. A file outside the
  repository cannot be read.

## The answer

- `cause`: one sentence a person reads in a breath, under 25 words, in their
  words. Not "the gate", "the baseline", "the reading" -- "Coverage does not
  count lines that run after a database call on Python 3.12."
- `where`: which side is wrong. `measurement` only when this environment
  measures what the project's own runs would not. `project` only when the code
  or its tests really fail, anywhere they ran.
- `evidence`: the lines that make the case, each with where it is from.
- `fixes`: up to three, best first. Each is tried -- the check is run with it
  applied -- before anyone sees it, so propose what you believe makes it green
  for the right reason.
  - `repo_change` for a setting or config line in the repository: the whole new
    file, every line, with a commit message saying why in the project's terms.
    Change as little as the fix needs.
  - `environment_change` for a package or tool missing where the checks run.
  - `command_change` when the command itself is wrong for this environment.
  - `agent_prompt` when real code must change: what a coding agent working in
    this repository needs to know, naming files and the failure.
  - `hold` only when the code really fails and the debt is the project's to
    keep for now -- never for a tests check, and never below a floor the project
    sets for itself.
- Never propose lowering the project's own floor, skipping or deleting tests,
  marking them expected to fail, excluding code from measurement, or silencing
  the check. Those make it green by making it say less.
- If you cannot tell, say `unknown`, give the cause you can support, and offer
  no fix you do not believe in.
