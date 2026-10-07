"""Checks on what the feature did to the repository: the verification surface,
a check's settings, suppressions, hollow tests, coverage of the patch,
destructive writes and the changes the spec declared.
"""

from __future__ import annotations

import configparser
from fnmatch import fnmatch
import json
import re
import subprocess
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any, Collection, Sequence

from ...config import Config
from ...asbuilt_graph import is_generated
from ...testquality import hollow_tests
from ...sandbox import Sandbox
from ...schemas import (
    COVERAGE_FORMATS,
    FileWrite,
    Finding,
    GateReport,
    Gate,
    IntegrationReport,
    Spec,
    WorkerOutput,
)
from ...workspace import FABRIKA_DIR, REPORT_PREFIX, PathEscape, safe_join

from ..text import _slug
from ..records import _DID


def check_verification_surface(
    workers: Sequence[WorkerOutput], integration: IntegrationReport, protected: Sequence[str],
) -> list[Finding]:
    """The build lane writing to the verification surface.

    INV-12 stops a *repairer* from editing the tests that judge it. The same
    move during the original build is not blocked -- a worker may legitimately
    need a conftest -- but it is never uninteresting, because the cheapest way
    to pass a test you cannot satisfy is to change the test.
    """
    if not protected:
        return []
    guard = set(protected)
    touched: dict[str, str] = {}
    for w in workers:
        for f in w.files:
            if f.path in guard:
                touched[f.path] = w.unit_id
    for f in integration.files:
        if f.path in guard:
            touched[f.path] = "integrator"
    if not touched:
        return []
    return [Finding(
        id="surface-1",
        title="The build changed files that decide which tests run",
        severity="major",
        category="verification",
        detail=(
            "Test configuration and test-collection files control what the suite executes. A "
            "change here can turn a failing criterion green without touching the code it is "
            "about, and nothing downstream would notice."
        ),
        evidence="\n".join(f"{path} (written by {who})" for path, who in sorted(touched.items())),
        recommendation="Read these diffs before any gate result.",
        files=sorted(touched),
    )]


def check_settings(checks: Sequence[Gate]) -> list[tuple[str, str, str]]:
    """(check, file, section) for every place a check's settings live.

    Declared per check by the surveyor and approved at gate 0. Nothing here
    knows which tool keeps its settings where -- that is the reading's job, for
    the same reason it is the reading's job to know which runner a project uses.
    An empty section means the file is the check's own, whole.
    """
    out: list[tuple[str, str, str]] = []
    for gate in checks:
        for entry in gate.config_files or ():
            path, _, section = str(entry).strip().partition("#")
            path = path.strip().strip("/")
            if path:
                out.append((gate.name, path, section.strip()))
    return out


def check_settings_files(checks: Sequence[Gate]) -> list[str]:
    """The files that are a check's settings and nothing else.

    These join the verification surface, so a repair cannot write them. A
    section of a shared file does not: the same file lists the project's
    dependencies, and refusing every write to it would refuse a repair that
    needs a package. A section is watched by `check_settings_changed` instead.
    """
    return sorted({path for _, path, section in check_settings(checks) if not section})


def settings_section(text: str | None, path: str, section: str) -> tuple[bool, Any]:
    """(readable, value) of one section of a settings file.

    Read by the file's format, never by guessing at text: a section moved
    within the file, or reformatted, is the same settings, and a dependency
    added beside it is not a change to them. `None` is a section that is not
    there, which is a reading and not a failure; `readable` is False only when
    the file could not be parsed, and then nothing can be said about the
    section except that its file changed.
    """
    if text is None:
        return True, None
    suffix = PurePosixPath(path).suffix.lower()
    try:
        if suffix == ".toml":
            data: Any = tomllib.loads(text)
        elif suffix == ".json":
            data = json.loads(text)
        elif suffix in (".cfg", ".ini"):
            parser = configparser.ConfigParser(interpolation=None)
            parser.read_string(text)
            return True, (dict(parser[section]) if parser.has_section(section) else None)
        else:
            return False, None
    except (ValueError, configparser.Error):
        return False, None
    for key in section.split("."):
        if not isinstance(data, dict) or key not in data:
            return True, None
        data = data[key]
    return True, data


def check_guides_changed(sandbox: Sandbox) -> list[Finding]:
    """The feature changed this repository's rules: an AGENTS.md or CLAUDE.md,
    DESIGN.md, or a skill.

    A guide is the standard a feature is held to, and a feature that rewrites
    the standard grades its own work: every later reviewer would cite the
    edited rule. Not refused -- a feature can be asked to update a guide -- but
    a person rules on it, as on a change to a check's settings. Read from the
    paths, so adding a skill counts as much as editing one; compared with the
    base commit every round, so putting the file back clears it.
    """
    from ... import guides as guide_lib

    base = sandbox.state.base_sha
    if not base or not sandbox.exists():
        return []
    hits = []
    for path in guide_lib.edited(sandbox.changed_files()):
        try:
            target = safe_join(sandbox.path, path)
        except PathEscape:
            continue
        after = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None
        if sandbox.show(base, path) != after:
            hits.append(path)
    if not hits:
        return []
    return [Finding(
        id="guide-edited-1",
        title="This feature changed the repository's own rules — " + ", ".join(hits),
        severity="blocker",
        category="verification",
        detail=("These files are the standard this feature is reviewed against, and every tool "
                "that writes code here follows them. Changing them in the same feature means "
                "the work is judged by rules it wrote itself. Sometimes updating them is the "
                "point; a person decides."),
        evidence="\n".join(f"git diff {base} -- {path}" for path in hits),
        recommendation=("Keep the rules as they were and change the code to follow them, or ask "
                        "for the rule change as its own feature."),
    )]


def check_settings_changed(sandbox: Sandbox, checks: Sequence[Gate]) -> list[Finding]:
    """The feature changed what a check measures, rather than the code it measures.

    A blocker, because it is the cheapest way through a check that cannot
    otherwise be satisfied, and nothing downstream can tell: the check goes
    green and stays green. It is not refused -- a feature can be asked for
    exactly this -- but a person rules on it before any result of that check is
    worth reading. Compared against the base commit every round, so a repair
    that puts the settings back clears it.
    """
    base = sandbox.state.base_sha
    entries = check_settings(checks)
    if not base or not entries or not sandbox.exists():
        return []
    changed = set(sandbox.changed_files())
    hits: list[tuple[str, str, str]] = []
    for name, path, section in entries:
        if path not in changed:
            continue
        try:
            target = safe_join(sandbox.path, path)
        except PathEscape:
            continue
        before = sandbox.show(base, path)
        after = (target.read_text(encoding="utf-8", errors="replace")
                 if target.is_file() else None)
        if before == after:
            continue
        if not section:
            hits.append((name, path, path))
            continue
        read_before, was = settings_section(before, path, section)
        read_after, now = settings_section(after, path, section)
        where = f"{path}, section {section}"
        if not read_before and not read_after:
            # Not a format a section can be read from -- settings written as
            # code, in a script. Nothing narrower than the file can be said.
            hits.append((name, path, f"{where} (settings written as code cannot be "
                                     "read section by section, so any change to this "
                                     "file is reported)"))
        elif not (read_before and read_after):
            hits.append((name, path, f"{where} (the file no longer parses, so the "
                                     "section cannot be compared)"))
        elif was != now:
            hits.append((name, path, where))
    if not hits:
        return []
    names = list(dict.fromkeys(name for name, _, _ in hits))
    return [Finding(
        id="check-settings-1",
        title="This feature changed what a check looks for — " + ", ".join(names),
        severity="blocker",
        category="verification",
        detail=(
            "These settings decide what the check reports. Changing them can turn a "
            "failing check green without changing the code it is about, and every later "
            "result of that check is then measured against settings nobody approved. "
            "Sometimes it is what the feature was for; a person decides which."
        ),
        evidence="\n".join(f"{where} — {name}\n  git diff {base} -- {path}"
                           for name, path, where in hits),
        recommendation="Read these diffs before any result of the checks named here.",
        files=sorted({path for _, path, _ in hits}),
    )]


def check_new_suppressions(sandbox: Sandbox, checks: Sequence[Gate]) -> list[Finding]:
    """Lines this feature added that tell a check to look away.

    Major rather than blocker: some suppressions are right, and the person
    reading is the one who can tell. What is never right is not knowing they
    are there, because a suppressed line is a passing check that did not look.
    Markers are the ones each check declares; one counts as new when the line
    carrying it is not in the file at the base commit, so moving an existing
    suppression is not reported and adding one beside it is.
    """
    base = sandbox.state.base_sha
    markers = [(g.name, m) for g in checks for m in (g.suppressions or ()) if str(m).strip()]
    if not base or not markers or not sandbox.exists():
        return []
    found: dict[str, list[tuple[str, str]]] = {}
    for rel in sorted(sandbox.changed_files()):
        if rel == FABRIKA_DIR or rel.startswith(FABRIKA_DIR + "/"):
            continue
        try:
            target = safe_join(sandbox.path, rel)
        except PathEscape:
            continue
        if not target.is_file():
            continue
        try:
            after = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        before = {line.strip() for line in (sandbox.show(base, rel) or "").splitlines()}
        for number, line in enumerate(after.splitlines(), 1):
            if line.strip() in before:
                continue
            for name, marker in markers:
                if marker in line:
                    found.setdefault(name, []).append(
                        (rel, f"{rel}:{number}: {line.strip()[:160]}"))
    return [Finding(
        id=f"suppressed-{_slug(name)}",
        title=(f"Lines this feature added tell {name} to look away — "
               f"{len(lines)} of them"),
        severity="major",
        category="verification",
        detail=(
            f"Each of these lines carries a marker that makes {name} skip it, and none of "
            "them was there before this feature. The check passing says nothing about "
            "them. Some suppressions are right; each one is a ruling a person makes."
        ),
        evidence="\n".join(text for _, text in lines),
        recommendation="Keep the ones that are right, and have the rest fixed instead.",
        files=sorted({rel for rel, _ in lines}),
    ) for name, lines in found.items()]


def _git_lines(root: Path, args: Sequence[str]) -> list[str]:
    """One git listing as paths, or nothing when git cannot answer."""
    try:
        done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    return [line for line in done.stdout.splitlines() if line] if done.returncode == 0 else []


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def check_hollow_tests(workers: Sequence[WorkerOutput], test_globs: Sequence[str]) -> list[Finding]:
    """Tests the build wrote that cannot fail -- no assertion, an assertion that
    holds whatever the code does, a test switched off, a failure swallowed.

    Major, one per unit: such a test makes coverage go up and proves nothing,
    which is the one trade a worker sent back for coverage is tempted to make.
    """
    findings: list[Finding] = []
    for w in workers:
        said: list[str] = []
        for f in w.files:
            if f.deleted or not any(fnmatch(f.path, g) for g in test_globs):
                continue
            said += [f"{f.path}: {problem}" for problem in hollow_tests(f.path, f.contents)]
        if said:
            findings.append(Finding(
                id=f"hollow-test-{_slug(w.unit_id)}",
                title=f"Tests written for {w.unit_id} cannot fail",
                severity="major",
                category="tests",
                detail=("These tests pass whatever the code does. They make coverage go up and "
                        "show nothing about whether the code is right."),
                evidence="\n".join(said[:40]),
                recommendation="Give each one an assertion that would fail if the code were wrong.",
                files=sorted({line.split(":", 1)[0] for line in said}),
            ))
    return findings


def changed_lines(sandbox: Sandbox) -> dict[str, set[int]]:
    """Every line this feature added or changed, by file, as git says.

    Against the base commit and the working tree, so a change not yet committed
    counts. A file git has never seen is new, and every line of it is the
    feature's. Deleted lines are not here: nothing can be found on a line that
    no longer exists. Read from git rather than from any agent's account of
    what it wrote, for the reason everything else about a branch is.
    """
    base = sandbox.state.base_sha
    if not base or not sandbox.exists():
        return {}
    out: dict[str, set[int]] = {}
    try:
        diff = subprocess.run(["git", "diff", "-U0", "--no-color", "--no-ext-diff", base],
                              cwd=sandbox.path, capture_output=True, text=True, timeout=120)
        fresh = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"],
                               cwd=sandbox.path, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return {}
    current = ""
    for line in diff.stdout.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            current = target[2:] if target.startswith("b/") else ""
            continue
        hunk = _HUNK.match(line)
        if hunk and current:
            start, count = int(hunk.group(1)), int(hunk.group(2) or 1)
            out.setdefault(current, set()).update(range(start, start + count))
    for rel in filter(None, fresh.stdout.split("\0")):
        if PurePosixPath(rel).name.startswith(REPORT_PREFIX):
            continue
        try:
            text = safe_join(sandbox.path, rel).read_text(encoding="utf-8", errors="replace")
        except (OSError, PathEscape):
            continue
        out[rel] = set(range(1, len(text.splitlines()) + 1))
    return out


def check_new_quality_findings(
    sandbox: Sandbox, checks: Sequence[Gate], report: GateReport,
) -> list[Finding]:
    """What a check found on the lines this feature wrote, held to its limit.

    The whole-repository count says nothing about a feature on a repository
    that already had problems: fix one old one and add one new one, and it
    does not move. Read by line instead, from the report the check wrote, so a
    feature is held to what it added and to nothing it inherited. A finding
    the tool places on a range counts when any line of the range is new.

    A report that could not be read is not a clean result. It is said, at
    `major`, rather than counted as nothing found.
    """
    held = [g for g in checks if g.report_format and g.patch_max is not None]
    if not held:
        return []
    results = {r.name: r for r in report.results}
    lines = changed_lines(sandbox)
    findings: list[Finding] = []
    for gate in held:
        result = results.get(gate.name)
        if result is None or not result.started:
            continue
        if result.report != "read":
            findings.append(Finding(
                id=f"quality-unread-{_slug(gate.name)}",
                title=f"New code was not checked by {gate.name} — its report could not be read",
                severity="major",
                category="verification",
                detail=(
                    f"{gate.name} is held to what a feature adds, which needs the report it "
                    f"writes of what it found. The report was {result.report or 'not written'}, "
                    "so nothing can be said about the lines this feature changed. This is not "
                    "a pass."
                ),
                evidence=f"$ {gate.command}\n{(result.output_tail or '')[-1200:]}",
                recommendation="Make the command write its report to {report} again.",
            ))
            continue
        new = [loc for loc in result.located
               if lines.get(loc.path) and any(
                   n in lines[loc.path]
                   for n in range(loc.start_line, max(loc.end_line, loc.start_line) + 1))]
        if len(new) <= gate.patch_max:
            continue
        limit = int(gate.patch_max) if float(gate.patch_max).is_integer() else gate.patch_max
        findings.append(Finding(
            id=f"quality-{_slug(gate.name)}",
            title=(f"Lines this feature changed have {len(new)} new "
                   f"{gate.name} finding{'s' if len(new) != 1 else ''} — the limit is {limit}"),
            severity="blocker",
            category="quality",
            detail=(
                f"{gate.name} found these on lines this feature added or changed. What the "
                "repository already had is not counted; these are the feature's own."
            ),
            evidence="\n".join(
                f"{loc.path}:{loc.start_line}: {loc.rule} {loc.message}".rstrip()
                for loc in new[:60]),
            recommendation="Fix them in the code, not in the check's settings.",
            files=sorted({loc.path for loc in new}),
        ))
    return findings


def _ranges(numbers: Collection[int]) -> str:
    """`3-7, 12, 15-16` -- line numbers the way a person reads them."""
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


def check_patch_coverage(
    sandbox: Sandbox, checks: Sequence[Gate], report: GateReport,
) -> list[Finding]:
    """How much of what this feature wrote its tests actually ran.

    Held per check at a floor a person set. The whole repository's share says
    nothing about a feature on a repository that was at 40% before it: read by
    line instead, against the lines the feature added or changed.

    Two kinds of line are not counted. A file the project's own markers call
    generated is a tool's output, not the feature's work. And a line no
    report lists as runnable -- a comment, a blank, an import a tool skips --
    cannot be covered. But a changed source file the report does not mention
    at all is not skipped: a module no test ever loaded is the plainest case
    of untested code there is, so its changed lines count as not run.
    """
    held = [g for g in checks if g.report_format in COVERAGE_FORMATS and g.patch_min is not None]
    if not held:
        return []
    results = {r.name: r for r in report.results}
    lines = changed_lines(sandbox)
    findings: list[Finding] = []
    for gate in held:
        result = results.get(gate.name)
        if result is None or not result.started:
            continue
        if result.report != "read":
            findings.append(Finding(
                id=f"coverage-unread-{_slug(gate.name)}",
                title=f"New code's coverage was not measured — {gate.name}'s report could not be read",
                severity="major",
                category="verification",
                detail=(
                    f"{gate.name} holds the lines a feature changes to a floor, which needs the "
                    f"coverage report it writes. The report was {result.report or 'not written'}, "
                    "so nothing can be said about how much of this feature's code ran. This is "
                    "not a pass."
                ),
                evidence=f"$ {gate.command}\n{(result.output_tail or '')[-1200:]}",
                recommendation="Make the command write its coverage report again.",
            ))
            continue
        measured = result.coverage
        suffixes = {PurePosixPath(path).suffix for path in measured}
        runnable: dict[str, set[int]] = {}
        unran: dict[str, set[int]] = {}
        unloaded: list[str] = []
        excluded: list[str] = []
        for path, changed in sorted(lines.items()):
            try:
                text = safe_join(sandbox.path, path).read_text(encoding="utf-8", errors="replace")
            except (OSError, PathEscape):
                continue
            if is_generated(path, text):
                excluded.append(path)
                continue
            if path in measured:
                can, did = (set(x) for x in measured[path])
                here = changed & can
                if here:
                    runnable[path] = here
                    if here - did:
                        unran[path] = here - did
            elif PurePosixPath(path).suffix in suffixes:
                body = text.splitlines()
                here = {n for n in changed if 0 < n <= len(body) and body[n - 1].strip()}
                if here:
                    runnable[path] = unran[path] = here
                    unloaded.append(path)
        total = sum(len(v) for v in runnable.values())
        if not total:
            continue                               # nothing this feature wrote could run
        ran = total - sum(len(v) for v in unran.values())
        share = round(100.0 * ran / total, 1)
        if share >= gate.patch_min:
            continue
        floor = int(gate.patch_min) if float(gate.patch_min).is_integer() else gate.patch_min
        evidence = [f"{path}: lines {_ranges(missed)}"
                    + (" (no test loaded this file)" if path in unloaded else "")
                    for path, missed in sorted(unran.items())]
        if excluded:
            evidence.append("Not counted, as generated: " + ", ".join(excluded))
        findings.append(Finding(
            id=f"patch-coverage-{_slug(gate.name)}",
            title=(f"Tests ran {share:g}% of the lines this feature changed — "
                   f"the floor is {floor}%"),
            severity="blocker",
            category="tests",
            detail=(
                f"Of {total} line{'s' if total != 1 else ''} this feature added or changed that "
                f"can run, its tests ran {ran}. The lines below never ran under any test, so "
                "nothing has shown they do what they should."
            ),
            evidence="\n".join(evidence[:80]),
            recommendation="Write tests that run these lines and would fail if they were wrong.",
            files=sorted(unran),
        ))
    return findings


def check_destructive_writes(
    sandbox: Sandbox, config: Config,
) -> list[Finding]:
    """Files this feature cut down, where the lines went nowhere else.

    Reported, not refused. Refusing would take a threshold with a magic number
    standing in for a judgement, in a system whose design says judgements are
    reached by a human reading an argument -- and it would be invisible:
    refusals go into the `writes` record's `rejected` list, which nothing reads
    back. When one fired, the packet would describe a build that was neither
    what the agent produced nor what anyone approved.

    A threshold on line counts does not work either. Under one, a unit has cut
    a 1,282-line file to 300 and a 498-line one to 187 without anything being
    refused -- because the guard asks the wrong question.

    Its test is arithmetic: did the batch's total line count hold up? The idea
    is to tell a refactor from a loss, since extracting a large file into
    several smaller ones legitimately guts the original and the lines reappear
    alongside. But a line count cannot tell rescued code from new code. That
    unit wrote a 77-line migration and tripled a schema file, and those 330 new
    lines bought it permission to delete 1,293 lines that went nowhere.

    So this asks the question directly: of the lines that left this file, how
    many turn up in the other files the feature changed? For that unit the
    answer was 3.3% and 2.7% -- lost, not moved. A real extraction scores near
    the top of the range and never reaches a human.
    """
    base = sandbox.state.base_sha
    if not base or not sandbox.exists():
        return []
    changed = sandbox.changed_files()
    if not changed:
        return []

    now: dict[str, str] = {}
    for rel in changed:
        try:
            target = safe_join(sandbox.path, rel)
        except PathEscape:
            continue
        if target.is_file():
            try:
                now[rel] = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        elif not target.exists():
            # Removed outright: the furthest a file can be cut down, and a
            # thing a unit can say it did. Measured like any other cut.
            now[rel] = ""

    findings: list[Finding] = []
    for rel, after_text in sorted(now.items()):
        before_text = sandbox.show(base, rel)
        if before_text is None:
            continue                                  # new file: deletes nothing
        before = before_text.splitlines()
        after = after_text.splitlines()
        if len(before) < config.pipeline.deletion_guard_min_lines:
            continue
        if len(after) >= len(before) * (1 - config.pipeline.max_deletion_ratio):
            continue

        kept = {line.strip() for line in after if line.strip()}
        gone = [line.strip() for line in before if line.strip() and line.strip() not in kept]
        if not gone:
            continue
        # Everywhere else this feature touched. A refactor puts the lines it
        # removes into the files it writes alongside; a reconstruction does not.
        elsewhere: set[str] = set()
        for other, text in now.items():
            if other != rel:
                elsewhere |= {line.strip() for line in text.splitlines() if line.strip()}
        moved = sum(1 for line in gone if line in elsewhere)
        ratio = moved / len(gone)
        if ratio >= 0.5:
            continue                                  # it moved; that is a refactor

        removed = len(before) - len(after)
        findings.append(Finding(
            id=f"deleted-{len(findings) + 1}",
            title=("Code was deleted and does not appear anywhere else — "
                   f"{rel}, {removed} of {len(before)} lines"),
            severity="blocker",
            category="destruction",
            detail=(
                f"This file is {len(after)} lines on the branch and was {len(before)} at the "
                f"base commit. Of the {len(gone)} distinct lines that are gone, {moved} "
                f"({ratio:.0%}) appear in any other file this feature changed -- so this is "
                "not code that moved into a new module, it is code that stopped existing. "
                "The usual cause is an agent asked to return a whole file returning what it "
                "could remember of one. Read the diff for this path before anything else in "
                "this packet: everything else here was measured against a file in this state."
            ),
            evidence=f"$ git diff {base[:12]} -- {rel}",
            files=[rel],
            recommendation=(
                "Check what is missing against the base commit. If the removal was "
                "intended, say so and this stops being a finding; if it was not, the "
                "file is recoverable in full from the base commit."
            ),
        ))
    return findings


def check_declared_changes(spec: Spec, written: Sequence[FileWrite]) -> list[Finding]:
    """Did the build do what the spec said it would?

    The spec writer declares columns, endpoints and surfaces at gate 1. This is what
    makes that declaration worth having: a paragraph cannot be checked, and a
    declared column can. Crude on purpose -- it looks for the name in what was
    written, without pretending to understand the code.

    It also looks for the *verb*. Name-presence alone cannot tell an add from a
    drop, so a spec that promised to remove a column and a build that added one
    would both pass. A declared drop that produced an `add_column` is exactly
    the finding a human wants before shipping, and it is invisible to a check
    that only asks whether the word appears.
    """
    if spec.changes is None or not written:
        return []

    haystack = "\n".join(f.contents for f in written)
    lowered = haystack.lower()
    findings: list[Finding] = []

    def finding(title: str, detail: str, evidence: str, recommendation: str) -> None:
        findings.append(Finding(
            id=f"spec-{len(findings) + 1}", title=title, severity="major",
            category="spec-drift", detail=detail, evidence=evidence,
            recommendation=recommendation,
        ))

    def missing(kind: str, needle: str, detail: str) -> None:
        if needle and needle not in haystack:
            finding(
                f"Something the spec promised was not built — the {kind} {needle}",
                detail,
                f"{needle!r} appears in no file written by this run.",
                "Either it was not built, or it was built under a different name than the spec "
                "promised. Both are worth a human knowing before this ships.",
            )

    def backwards(kind: str, needle: str, operation: str, detail: str) -> None:
        """Present, but the code around it says the opposite verb."""
        if not needle or needle not in haystack:
            return
        wanted = _DID.get(operation, ())
        opposite = _DID["add"] if operation in ("drop", "remove") else _DID["drop"]
        if any(w in lowered for w in wanted):
            return
        if any(w in lowered for w in opposite):
            finding(
                f"The build appears to do the opposite of what the spec said — {operation} {kind} {needle}",
                detail,
                f"{needle!r} was written, but no {'/'.join(wanted[:2])} appears anywhere in this "
                f"run while {'/'.join(opposite[:2])} does.",
                "A change that runs backwards against its own spec is worse than one that was "
                "never made, because the gates can still pass.",
            )

    for change in spec.changes.data:
        if change.operation in ("add", "alter", "index") and change.column:
            missing("column", change.column,
                    f"The spec declared {change.operation} {change.column} on {change.table}.")
            backwards("column", change.column, change.operation,
                      f"The spec declared {change.operation} {change.column} on {change.table}.")
        elif change.operation == "new_table" and change.table:
            missing("table", change.table, f"The spec declared a new table {change.table}.")
            # And the name the row type has to carry, when the spec pinned one.
            # That name is a contract between two agents who cannot see each
            # other: the implementation writes it, and the suite that tests
            # without reading the code imports it. A mismatch does not fail a
            # test -- it stops a file loading, and every criterion that file
            # covers comes back unverified. Measured: `UserSession` against an
            # imported `Session`, three files, twenty criteria, the work
            # correct throughout.
            if change.model and f"class {change.model}" not in haystack:
                finding(
                    f"The code names something differently from the spec, so the independent "
                    f"tests cannot find it — {change.model}",
                    f"The spec declared a new table {change.table} mapped by "
                    f"{change.model}. Nothing written by this run defines "
                    f"`class {change.model}`.",
                    f"`class {change.model}` appears in no file written by this run.",
                    "The independent suite imports that name because the spec promised it. "
                    "Rename the class to match the spec, or change the spec -- but a name "
                    "only one side uses costs a whole file's criteria, silently.",
                )
        elif change.operation == "drop":
            # A branch of its own: the most destructive operation in the
            # vocabulary must not match no branch and fall straight through.
            needle = change.column or change.table
            missing("dropped column" if change.column else "dropped table", needle,
                    f"The spec declared dropping {needle} from {change.table}.")
            backwards("column" if change.column else "table", needle, "drop",
                      f"The spec declared dropping {needle} from {change.table}.")

    for change in spec.changes.interface:
        if not change.path.startswith("/"):
            continue
        leaf = [p for p in change.path.split("/") if p and not p.startswith("{")]
        if not leaf:
            continue
        detail = f"The spec declared {change.operation} of {change.method} {change.path}: {change.change}"
        if change.operation == "add":
            missing("endpoint", leaf[-1], detail)
        elif change.operation == "remove":
            missing("removed endpoint", leaf[-1], detail)
        # An altered endpoint may live entirely in a file this run never touched
        # -- a response schema can change without the route line moving -- so its
        # absence from the diff proves nothing and is not reported.

    return findings
