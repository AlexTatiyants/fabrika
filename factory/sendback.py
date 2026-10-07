"""A worker measured before its unit is accepted, and sent back with what was found.

The cheapest moment to have a test written is while the agent that wrote the
code still has it in mind. So once a worker says it is done, its own tests are
run in its own environment, on the files it wrote, and three things are read:
the lines it changed that no test of its ran, the changes to its code that no
test of its caught, and the tests it wrote that cannot fail. Any of those goes
back to the same worker as plain sentences, a limited number of times.

What is sent back is chosen so that a hollow test cannot satisfy it. A test
that calls the code and asserts nothing covers every line; it kills no mutant,
and it is named by `testquality` for what it is. Coverage alone would have
taught the worker to write exactly that.

Nothing here knows a tool. The commands are the project's own, declared by the
surveyor on its coverage and mutation checks as `files_command`; the reports
are read by format. A worker whose project measures neither is sent back only
for tests that cannot fail, and for a test home it was given and left empty.
"""

from __future__ import annotations

import difflib
import shlex
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable, Sequence

from .gates import run_gate
from .schemas import COVERAGE_FORMATS, Gate
from .testquality import hollow_tests


@dataclass
class SendBack:
    """What an executor needs to send a worker back: how to measure, and how often."""

    measure: Callable[[Path, Any, list[str]], Awaitable[list[str]]]
    limit: int
    #: What could not be measured, for the person, not the worker: a command
    #: for some files that wrote no report is a send-back that silently never
    #: happens, and nothing else would say so.
    notes: list[str] = field(default_factory=list)


def changed_new_lines(old: str | None, new: str) -> set[int]:
    """Line numbers, in `new`, that `old` did not have. All of them for a new file."""
    new_lines = new.splitlines()
    if old is None:
        return set(range(1, len(new_lines) + 1))
    out: set[int] = set()
    matcher = difflib.SequenceMatcher(a=old.splitlines(), b=new_lines, autojunk=False)
    for tag, _, _, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "insert"):
            out.update(range(j1 + 1, j2 + 1))
    return out


def _ranges(numbers: Sequence[int]) -> str:
    out: list[str] = []
    run: list[int] = []
    for n in sorted(numbers):
        if run and n == run[-1] + 1:
            run.append(n)
            continue
        if run:
            out.append(f"{run[0]}-{run[-1]}" if len(run) > 1 else str(run[0]))
        run = [n]
    if run:
        out.append(f"{run[0]}-{run[-1]}" if len(run) > 1 else str(run[0]))
    return ", ".join(out)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
    except OSError:
        return None


async def measure_unit(
    tree: Path, runner: Any, written: Sequence[str], *, original: Path,
    test_globs: Sequence[str], test_home: Sequence[str], coverage: Gate | None,
    mutation: Gate | None, max_mutants: int = 50, notes: list[str] | None = None,
) -> list[str]:
    """What this unit's own tests leave unproved, as sentences for its worker.

    `original` is the checkout the unit started from, so a line counts as the
    worker's only if it is new or changed there. `test_globs` are the
    project's own rules for what a test file is.
    """
    tests = [p for p in written if any(fnmatch(p, g) for g in test_globs)]
    sources = [p for p in written if p not in tests]
    said: list[str] = []

    for path in tests:
        for problem in hollow_tests(path, _read(tree / path) or ""):
            said.append(f"{path}: {problem}.")

    home = [p for p in test_home if p not in tests]
    if sources and not tests and home:
        said.append(f"This unit owns {', '.join(home)} for its tests, and you wrote none. "
                    "Write the tests for the logic you changed there.")

    changed = {p: changed_new_lines(_read(original / p), _read(tree / p) or "")
               for p in sources}
    changed = {p: lines for p, lines in changed.items() if lines}

    if coverage is not None and tests and changed:
        result = await run_gate(Gate(
            name=f"{coverage.name} (this unit)", report_format=coverage.report_format,
            report_path=coverage.report_path,
            command=coverage.files_command.replace(
                "{paths}", " ".join(shlex.quote(t) for t in tests)),
            timeout_s=coverage.timeout_s), tree, runner)
        if result.report != "read" and notes is not None:
            notes.append(f"{coverage.name} could not be run on this unit's tests -- its report "
                         f"was {result.report or 'not written'}: "
                         f"{(result.output_tail or '').strip()[-300:]}")
        if result.report == "read":
            measured = result.coverage
            suffixes = {PurePosixPath(p).suffix for p in measured}
            for path, lines in sorted(changed.items()):
                if path in measured:
                    can, did = (set(x) for x in measured[path])
                    missed = sorted((lines & can) - did)
                    if missed:
                        said.append(f"{path}: lines {_ranges(missed)} never ran under your tests.")
                elif PurePosixPath(path).suffix in suffixes:
                    said.append(f"{path}: none of your tests loads this file, so nothing you "
                                "changed in it ran.")

    if mutation is not None and tests and changed:
        result = await run_gate(Gate(
            name=f"{mutation.name} (this unit)", report_format="mutation-json",
            report_path=mutation.report_path,
            command=mutation.files_command.replace(
                "{paths}", " ".join(shlex.quote(p) for p in sorted(changed))),
            timeout_s=mutation.timeout_s), tree, runner)
        if result.report != "read" and notes is not None:
            notes.append(f"{mutation.name} could not be run on this unit's files -- its report "
                         f"was {result.report or 'not written'}: "
                         f"{(result.output_tail or '').strip()[-300:]}")
        if result.report == "read":
            mine = [m for m in result.located
                    if any(n in changed.get(m.path, ()) for n in
                           range(m.start_line, max(m.end_line, m.start_line) + 1))]
            for m in mine[:max_mutants]:
                said.append(f"{m.path}:{m.start_line}: {m.message}.")
            if len(mine) > max_mutants:
                said.append(f"...and {len(mine) - max_mutants} more changes no test caught.")
    return said


def send_back_brief(found: Sequence[str], brief: str, attempt: int, limit: int) -> str:
    """What the worker reads when it is sent back. The finding first, the task after."""
    return (
        "# Before your work is accepted\n\n"
        "Your tests were run on what you wrote, and this is what they left unproved:\n\n"
        + "\n".join(f"- {line}" for line in found)
        + "\n\nWrite or fix tests so that each of these would fail if the code were wrong. "
        "A test that runs a line without checking what it did does not count, and will be "
        "named the next time. Do not delete or exclude code to make a line disappear, and do "
        "not change the project's test or coverage settings. Change the code itself only "
        "where a test shows it is wrong.\n\n"
        f"(This is send-back {attempt} of at most {limit}.)\n\n"
        "---\n\n# The task you were given, for reference\n\n" + brief
    )


def runs_on_files(gate: Gate) -> bool:
    """Whether a check can be run on some files and its report found afterwards:
    `{paths}` for the files, and `{report}` or a fixed `report_path` for the report."""
    return ("{paths}" in gate.files_command
            and ("{report}" in gate.files_command or bool(gate.report_path.strip())))


def pick_checks(gates: Sequence[Gate]) -> tuple[Gate | None, Gate | None]:
    """The coverage check and the mutation check that can be run on a unit's files."""
    coverage = next((g for g in gates if g.report_format in COVERAGE_FORMATS
                     and runs_on_files(g)), None)
    mutation = next((g for g in gates if g.report_format == "mutation-json"
                     and runs_on_files(g)), None)
    return coverage, mutation
