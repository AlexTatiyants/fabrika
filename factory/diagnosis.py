"""Why a check is red on untouched code.

Two answers, in order. The first is code's: what kind of red it is, from what
was already read -- it never ran, it timed out, it wrote no report, it read
under the floor the project sets for itself, it found problems. The second is a
model's, and only for a check that is red before any feature starts: the cause
in one sentence, and up to three fixes, each tried in the baseline's throwaway
checkout before a person is shown it.

What this exists to stop. A project's API tests all passed and the check was
red: coverage read 78% against the 95% the project's own settings require. The
card offered to hold it at 78, which would have locked in a number wrong by
seventeen points -- the project's CI and this environment ran Python 3.12, where
coverage's older tracer does not follow SQLAlchemy into the greenlets each query
resumes in. One setting fixed it. Nothing on the page could have said so.

No tool is named here. Where a bound lives, which commands print versions and
which files pin them are declared by the surveyor; what a failure means is the
diagnosis's to say, and whether its fix works is a run of the check.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import AbstractAsyncContextManager
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable, Sequence

from .config import DiagnosisConfig
from .gates import Runner, gate_outcome, is_green, restore_tracked, run_gate, run_setup
from .pipeline import check_settings, settings_section
from .schemas import (
    Diagnosis,
    DiagnosisAnswer,
    DiagnosisFix,
    EnvironmentSpec,
    FixTry,
    Gate,
    GateReport,
    GateResult,
    RedKind,
)
from .workspace import PathEscape, safe_join

#: How much of a file the diagnosis is shown, and of the output.
FILE_CHARS = 12_000
HEAD_CHARS = 2_000

#: The order red checks are diagnosed in when there are more than the cap: the
#: kinds code could say least about first, since those are the ones a person
#: can do least with on their own.
_PRIORITY: dict[str, int] = {"under_own_floor": 0, "failed": 1, "didnt_run": 2, "no_report": 3,
                             "timed_out": 4, "findings": 5, "over_bound": 6}

Read = Callable[[str], "str | None"]


# -- part 2: the kind of red, by code ------------------------------------------


def own_floor_value(gate: Gate, read: Read) -> float | None:
    """The bound the project sets for itself, as its settings say it now."""
    floor = gate.own_floor
    if floor is None:
        return None
    where = floor.where.strip()
    if where == "command":
        return floor.value
    path, _, key = where.partition("#")
    if not path or not key:
        return floor.value
    readable, value = settings_section(read(path.strip()), path.strip(), key.strip())
    if not readable:
        # A config that is code -- `vite.config.ts`, `jest.config.js` -- cannot
        # be read as data, so the number the surveyor read in it stands in.
        return floor.value
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def red_kind(gate: Gate, result: GateResult) -> RedKind:
    """What kind of red this is. The first that applies; empty when it passed."""
    if is_green(result):
        return ""
    if result.timed_out:
        return "timed_out"
    if gate_outcome(result) == "could_not_run":
        return "didnt_run"
    if gate.report_format and result.report in ("missing", "unreadable"):
        return "no_report"
    floor, metric = result.own_floor_value, result.metric
    if floor is not None and metric is not None and gate.own_floor is not None:
        under = metric < floor if gate.own_floor.points == "floor" else metric > floor
        if under:
            return "under_own_floor"
    if metric is not None and (
            (gate.threshold is not None and metric < gate.threshold)
            or (gate.threshold_max is not None and metric > gate.threshold_max)):
        return "over_bound"
    if result.located:
        return "findings"
    return "failed"


def classify(gates: Sequence[Gate], report: GateReport, read: Read) -> None:
    """Name the kind of every red result in place, and the floor it was read against."""
    by_name = {g.name: g for g in gates}
    for result in report.results:
        gate = by_name.get(result.name)
        if gate is None:
            continue
        result.own_floor_value = own_floor_value(gate, read)
        result.red_kind = red_kind(gate, result)


# -- part 1: why, by a model, with its fixes tried -----------------------------


def settings_text(gate: Gate, read: Read) -> str:
    """The check's settings as its tool reads them: a section where it names one."""
    parts: list[str] = []
    places = check_settings([gate])
    for _, path, section in places:
        if section and any(p == path and section.startswith(f"{s}.") for _, p, s in places if s):
            continue
        label = f"{path}#{section}" if section else path
        text = read(path)
        if text is None:
            parts.append(f"### {label}\n(not in the repository)")
            continue
        if section:
            readable, value = settings_section(text, path, section)
            if readable:
                text = "(no such section)" if value is None else json.dumps(value, indent=2, default=str)
        parts.append(f"### {label}\n```\n{text.strip()[:FILE_CHARS]}\n```")
    return "\n\n".join(parts)


def fingerprint(gate: Gate, result: GateResult, settings: str, environment: EnvironmentSpec | None,
                base_sha: str, versions: dict[str, str]) -> str:
    """What a diagnosis read, as one value. Not the output: it carries timings
    that differ on every run, and a diagnosis redone for a changed duration is
    a model call bought for nothing."""
    material = {
        "gate": gate.model_dump(mode="json", exclude={"also"}),
        "kind": result.red_kind, "exit": result.exit_code, "metric": result.metric,
        "floor": result.own_floor_value, "settings": settings,
        "environment": environment.model_dump(mode="json", exclude={"image"}) if environment else None,
        "base": base_sha, "versions": versions,
    }
    blob = json.dumps(material, sort_keys=True, ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


KIND_WORDS: dict[str, str] = {
    "didnt_run": "the command never ran",
    "timed_out": "it was stopped at its time limit",
    "no_report": "it wrote no report that could be read",
    "under_own_floor": "it read under the floor this project sets for itself",
    "over_bound": "it read past the bound set for it here",
    "findings": "its report lists problems",
    "failed": "it exited non-zero, and nothing read says why",
}


def brief(gate: Gate, result: GateResult, settings: str, environment: EnvironmentSpec | None,
          dockerfile: str, versions: dict[str, str], version_files: str) -> str:
    """Everything the diagnosis is given, gathered by code."""
    env = environment
    floor = gate.own_floor
    lines = [
        f"# The check\n\nName: {gate.name}\nFamily: {gate.family or 'not set'}\n"
        f"Command: `{gate.command}`\nTime limit: {gate.timeout_s:.0f}s",
        (f"Report: {gate.report_format}" if gate.report_format else ""),
        (f"The project's own bound: {floor.where} ({floor.points}), which reads "
         f"{result.own_floor_value if result.own_floor_value is not None else 'unreadable'} now"
         if floor else ""),
        f"\n# What happened on untouched code\n\nKind: {result.red_kind} -- "
        f"{KIND_WORDS.get(result.red_kind, '')}\nExit code: {result.exit_code}\n"
        f"Reading: {result.metric if result.metric is not None else 'none'}"
        + (f"\nFindings its report listed: {len(result.located)}" if result.located else ""),
        (f"\n## The start of its output\n\n```\n{result.output_head}\n```" if result.output_head else ""),
        f"\n## The end of its output\n\n```\n{result.output_tail}\n```",
        f"\n# Its settings, as they are at this commit\n\n{settings or '(it records none)'}",
        "\n# Where it ran\n",
        (f"Kind: {env.kind}" if env else "Kind: on the host, no container"),
        ("Setup, with the network:\n" + "\n".join(f"- `{c}`" for c in env.setup) if env and env.setup else ""),
        ("Before the checks:\n" + "\n".join(f"- `{c}`" for c in env.test_prepare)
         if env and env.test_prepare else ""),
        ("Services running: " + ", ".join(f"{s.name} (`{s.command}`)" for s in env.services)
         if env and env.services else ""),
        ("Environment variables set: " + ", ".join(sorted(env.env)) if env and env.env else ""),
        (f"\n## Its Dockerfile\n\n```\n{dockerfile[:FILE_CHARS]}\n```" if dockerfile.strip() else ""),
        ("\n## Versions where it ran\n\n" + "\n".join(f"- `{c}`: {v}" for c, v in versions.items())
         if versions else ""),
        (f"\n# What the project's own runs use\n\n{version_files}" if version_files else ""),
    ]
    return "\n".join(line for line in lines if line)


def usable_fixes(gate: Gate, result: GateResult, answer: DiagnosisAnswer) -> list[DiagnosisFix]:
    """The fixes a person may be offered: whole, and none that hides the red.

    A hold is never offered for a tests check, whose exit code also says a
    test failed, nor under the project's own floor, which it would contradict.
    A file is written whole and inside the repository, or not at all.
    """
    out: list[DiagnosisFix] = []
    for fix in answer.fixes:
        if len(out) == 3:
            break
        if fix.kind == "hold" and (gate.family == "tests" or result.red_kind == "under_own_floor"
                                   or result.metric is None):
            continue
        if fix.kind == "repo_change":
            path = fix.path.strip()
            if (not path or not fix.contents.strip() or PurePosixPath(path).is_absolute()
                    or ".." in PurePosixPath(path).parts):
                continue
        if fix.kind == "environment_change" and not [c for c in fix.setup if c.strip()]:
            continue
        if fix.kind == "command_change" and not fix.command.strip():
            continue
        if fix.kind == "agent_prompt" and not fix.prompt.strip():
            continue
        out.append(fix)
    return out


async def try_fix(fix: DiagnosisFix, gate: Gate, cwd: Path, runner: Runner) -> GateResult | str:
    """Run the check with one fix applied, then put the checkout back.

    A string is why it could not be tried. The checkout is the baseline's own,
    thrown away when the run ends, so nothing here reaches the person's copy.
    """
    if fix.kind == "command_change":
        return await run_gate(gate.model_copy(update={"command": fix.command}), cwd, runner)
    if fix.kind == "environment_change":
        steps = await run_setup([c for c in fix.setup if c.strip()], cwd, runner)
        broke = next((s for s in steps if not s.passed), None)
        if broke is not None:
            return f"`{broke.command}` failed: {(broke.output_tail or '').strip()[-300:]}"
        return await run_gate(gate, cwd, runner)
    if fix.kind != "repo_change":
        return "nothing to try"
    try:
        target = safe_join(cwd, fix.path.strip())
    except PathEscape:
        return "that file is outside the repository"
    before = target.read_text(encoding="utf-8") if target.is_file() else None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(fix.contents, encoding="utf-8")
        return await run_gate(gate, cwd, runner)
    finally:
        if before is None:
            target.unlink(missing_ok=True)
        else:
            target.write_text(before, encoding="utf-8")
        restore_tracked(cwd)


Ask = Callable[[str], Awaitable[DiagnosisAnswer]]


async def diagnose_check(
    *, gate: Gate, result: GateResult, fp: str, prompt: str, cwd: Path, runner: Runner,
    ask: Ask, settings: DiagnosisConfig,
    session: Callable[[], AbstractAsyncContextManager[Any]],
) -> Diagnosis:
    """One red check: run it once more, ask why, and try what is proposed."""
    diagnosis = Diagnosis(check=gate.name, fingerprint=fp, kind=result.red_kind)
    # Once more first. A check that passes the second time does not fail every
    # time, and no model is needed to say so.
    async with session() as prepared:
        if prepared.ready:
            again = await run_gate(gate, cwd, runner)
            if is_green(again):
                result.red_kind = diagnosis.kind = "flaky"
                diagnosis.where = "flaky"
                diagnosis.cause = "It passed when it was run again, so it does not fail every time."
                return diagnosis
    try:
        answer = await ask(prompt)
        wanted = [p.strip() for p in answer.need_files if p.strip()][:settings.max_files]
        if wanted and not answer.fixes:
            shown: list[str] = []
            for path in wanted:
                try:
                    target = safe_join(cwd, path)
                    text = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None
                except (PathEscape, OSError):
                    text = None
                shown.append(f"### {path}\n" + (f"```\n{text[:FILE_CHARS]}\n```" if text is not None
                                                 else "(not in the repository)"))
            answer = await ask(prompt + "\n\n# The files you asked for\n\n" + "\n\n".join(shown)
                               + "\n\nAnswer now. No more files can be read.")
    except Exception as exc:  # noqa: BLE001 -- a diagnosis that fails says so; it never hides the red
        diagnosis.failed = f"{type(exc).__name__}: {exc}"[:400]
        return diagnosis
    diagnosis.cause = answer.cause.strip()
    diagnosis.where = answer.where
    diagnosis.evidence = [e for e in answer.evidence if e.strip()][:8]
    diagnosis.fixes = usable_fixes(gate, result, answer)
    if not diagnosis.cause:
        diagnosis.failed = "the answer gave no cause"
        return diagnosis
    picked = answer.fixes[answer.recommended] if 0 <= answer.recommended < len(answer.fixes) else None
    diagnosis.recommended = next((i for i, f in enumerate(diagnosis.fixes) if f is picked), 0)

    # Tried in the order that disturbs the checkout least: an environment
    # change installs things that stay, so it goes last.
    order = sorted((i for i, f in enumerate(diagnosis.fixes)
                    if f.kind in ("repo_change", "command_change", "environment_change")),
                   key=lambda i: (diagnosis.fixes[i].kind == "environment_change",
                                  i != diagnosis.recommended))[:settings.max_trials]
    if order:
        async with session() as prepared:
            for i in order:
                if not prepared.ready:
                    diagnosis.tries.append(FixTry(fix=i, ran=False, note=prepared.report[:300]))
                    continue
                outcome = await try_fix(diagnosis.fixes[i], gate, cwd, runner)
                if isinstance(outcome, str):
                    diagnosis.tries.append(FixTry(fix=i, ran=False, note=outcome))
                    continue
                diagnosis.tries.append(FixTry(
                    fix=i, ran=outcome.started, passed=is_green(outcome),
                    exit_code=outcome.exit_code, metric=outcome.metric,
                    note=(outcome.output_tail or "").strip()[-300:]))
    diagnosis.confirmed = any(t.fix == diagnosis.recommended and t.passed for t in diagnosis.tries)
    return diagnosis


def worth_diagnosing(results: Sequence[GateResult], gates: Sequence[Gate], limit: int) -> list[GateResult]:
    """The red checks to diagnose this run, most telling first, at most `limit`."""
    known = {g.name for g in gates}
    # A check that never started was stopped by the environment, which the
    # run already says -- one setup failure, not eight causes. A command that
    # started and was not found is still a check's own, and still diagnosed.
    red = [r for r in results if r.name in known and r.red_kind and r.red_kind != "flaky"
           and r.started]
    red.sort(key=lambda r: _PRIORITY.get(r.red_kind, 9))
    return red[:max(0, limit)]


def latest_by_check(records: Sequence[dict[str, Any]]) -> dict[tuple[str, str], Diagnosis]:
    """Every recorded diagnosis, by (check, fingerprint); the newest wins."""
    out: dict[tuple[str, str], Diagnosis] = {}
    for record in records:
        if record.get("kind") != "diagnosis":
            continue
        try:
            d = Diagnosis.model_validate(record.get("payload") or {})
        except Exception:  # noqa: BLE001 -- a malformed record is not fatal here
            continue
        out[(d.check, d.fingerprint)] = d
    return out


def read_files(read: Read, paths: Sequence[str]) -> str:
    """Files that say what the project's own runs use, whole or by section."""
    shown: list[str] = []
    for entry in paths:
        path, _, section = str(entry).partition("#")
        path = path.strip()
        text = read(path) if path else None
        if text is None:
            continue
        if section.strip():
            readable, value = settings_section(text, path, section.strip())
            if readable and value is not None:
                text = json.dumps(value, indent=2, default=str)
        shown.append(f"### {entry}\n```\n{text.strip()[:FILE_CHARS]}\n```")
    return "\n\n".join(shown)


def reader(root: Path) -> Read:
    """Read a repo-relative file under `root`, or None."""
    def read(path: str) -> str | None:
        try:
            target = safe_join(root, path)
        except PathEscape:
            return None
        try:
            return target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None
        except OSError:
            return None
    return read
