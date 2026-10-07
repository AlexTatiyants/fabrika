"""Repo digest, git worktrees, and the air gap.

The air gap is `verify_context`. It takes a `Spec` and three narrow descriptions
of the world the tests will run in, and returns a `str`. It has no repo argument
and no digest hook, and none of the three can carry one: the first is filtered to
package names, and the other two come only from what a human approved at gate 0 --
configuration, and the test setup the survey recorded. There is no code path by
which implementation source reaches the oracle, even by mistake. (INV-1)

The third is the one that looks like a hole and is not. A conftest is not the
implementation: a fixture that authenticates as a tenant tells a test author how
to reach the application and nothing about whether the feature works. Reading
*how to talk to the app* is not reading *what the app does*, and only the second
corrupts a blind test. Files that assert are never shown, because that would be
showing it the answers.

This is the load-bearing property of the design: the moment a test author can
read the implementation, it starts testing what was built instead of what was
asked for, and a green suite stops carrying information.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from . import git
from .git import has_commit, head_sha, is_repo  # noqa: F401 -- head_sha is read from here too
from .schemas import Spec

#: What a gate names the per-test report it asks a runner to write. The
#: runner writes it inside the checkout, because that is what is mounted.
REPORT_PREFIX = ".factory-report-"
#: A pathspec that keeps those reports out of anything staged for the branch --
#: a per-test report or a check's own report of what it found, whatever its type.
NOT_A_REPORT = f":(exclude,glob)**/{REPORT_PREFIX}*"

#: Where a test runner leaves its own account of a run, wherever it was started.
#: Playwright writes `test-results/.last-run.json` under whatever directory it
#: ran from, so an integrator that ran it from `api/` put a stale "failed" on
#: the branch as the integrator's work. Two reviewers flagged it, no repair
#: could remove it, and it reached a human as a decision. These are the tool's
#: fixed names, not a project's choice, so they are here and not configurable
#: -- `ProjectState.trace_dirs` is where a project says where it records.
RUNNER_OUTPUT = ("test-results", "playwright-report", "blob-report")
#: Pathspecs that keep that output off anything staged for the branch.
NOT_RUNNER_OUTPUT = tuple(f":(exclude,glob)**/{name}/**" for name in RUNNER_OUTPUT)


#: Where a project keeps what Fabrika runs it in -- `.fabrika/Dockerfile`. Its
#: owner edits it; no agent does. A feature branch that changed it would carry
#: a new environment into main as a side effect of a feature, reviewed as code
#: by nobody who knew that was what it was. What an agent writes there stays
#: in its checkout and is never staged.
FABRIKA_DIR = ".fabrika"
NOT_FABRIKA = f":(exclude,glob){FABRIKA_DIR}/**"
#: What Fabrika writes about a repository, inside it. Never read back as the
#: repository: the as-built's pages describe every file, and a digest that
#: listed them had two copies of the code, one of them a model's prose. A
#: resurvey of one project was shown twenty of those pages as its "test files
#: and setup" -- `conftest.py.md` beside `conftest.py` -- and they took the
#: budget ahead of real tests. `.fabrika/Dockerfile` is the project's own and
#: stays visible.
GENERATED_PREFIXES = (f"{FABRIKA_DIR}/as-built/",)


def is_generated(rel: str) -> bool:
    return str(rel).replace("\\", "/").startswith(GENERATED_PREFIXES)


def is_runner_output(relative: str) -> bool:
    """Whether a path lies inside a test runner's output directory."""
    return any(seg in RUNNER_OUTPUT for seg in relative.split("/")[:-1])


SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", "target",
    ".next", ".nuxt", "coverage", "htmlcov", ".factory", ".idea", ".DS_Store",
    ".tox", "site-packages", ".eggs", ".claude", ".vscode", ".github",
}

# Skipping a directory because most of it is noise throws away the part that is
# not. `.github` is mostly issue templates, funding files and PR boilerplate --
# and it also holds the workflows, which are the single most authoritative
# statement a repository makes about how it verifies itself. The surveyor is
# told to prefer CI over every other signal, and for as long as `.github` was
# skipped outright there was never a CI file in the digest to prefer: on every
# project it has ever read, it inferred gates while the answer sat in a
# directory the walker dropped.
KEEP_PREFIXES = (".github/workflows/", ".github/actions/")


def _kept(rel: "PurePosixPath | Path") -> bool:
    """Whether a path inside a skipped directory is worth reading anyway."""
    text = rel.as_posix() if hasattr(rel, "as_posix") else str(rel)
    return text.startswith(KEEP_PREFIXES)
SKIP_SUFFIXES = {
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".class", ".jar", ".zip", ".gz",
    ".tar", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".woff",
    ".woff2", ".ttf", ".eot", ".mp4", ".mov", ".mp3", ".wasm", ".lock", ".bin",
    ".bak", ".orig", ".rej", ".map", ".min.js", ".min.css",
}
MAX_FILE_CHARS = 8_000
# Slices can afford whole files where a single truncated digest could not. On a
# real repository the difference is between a scout reading a 50k-line router
# and reading its imports.
SLICE_FILE_CHARS = 24_000


# --------------------------------------------------------------------------
# the frozen contract
# --------------------------------------------------------------------------


def spec_hash(spec: Spec) -> str:
    """AC-5.1 -- content hash of the contract, so you can prove which spec
    version each lane consumed."""
    body = {
        "title": spec.title,
        "summary": spec.summary,
        "criteria": [
            {"id": c.id, "statement": c.statement, "verification": c.verification}
            for c in spec.acceptance_criteria
        ],
        "non_goals": list(spec.non_goals),
        "constraints": list(spec.constraints),
        "assumptions": list(spec.assumptions),
        "answers": [
            {"q": a.question_id, "a": a.answer} for a in spec.resolved_answers
        ],
    }
    blob = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()[:32]


def spec_text(spec: Spec) -> str:
    """AC-5.3 -- the spec rendered for a prompt."""
    lines: list[str] = [f"# Specification: {spec.title}", ""]
    lines += ["## Intent (verbatim)", spec.intent.strip(), ""]
    lines += ["## Summary", spec.summary.strip(), ""]
    lines.append("## Acceptance criteria")
    if spec.acceptance_criteria:
        for c in spec.acceptance_criteria:
            lines.append(f"- **{c.id}** {c.statement}")
            if c.verification:
                lines.append(f"    - verified by: {c.verification}")
            if c.rationale:
                lines.append(f"    - why: {c.rationale}")
    else:
        lines.append("- (none)")
    lines.append("")

    # The shape the human approved at gate 1. `check_declared_changes` reads it
    # too -- code comparing the declaration against what got built -- but if
    # that were its only reader it would reach no prompt at all, and leave the
    # oracle testing a migration it has not been told the shape of.
    #
    # The spec writer is asked for these precisely because "a paragraph cannot
    # be checked, and a declared column can", and a column that is checkable is
    # a column that is *stateable*: it belongs to the contract, next to an
    # acceptance criterion, not to the implementation that satisfies it. An
    # agent asked to verify `audit_logs.actor` became NOT NULL and not told
    # which column, which table, or that there is a backfill, is being asked to
    # infer the contract from the criteria and then guess the rest -- and it
    # will guess `tenants.name` for a column called `display_name`.
    changes = spec.changes
    if changes is not None and (changes.data or changes.interface or changes.surfaces):
        lines.append("## The shape of this change, as declared at gate 1")
        lines.append("")
        lines.append("Approved by the human with the spec. This is the contract, not the "
                     "implementation: what is named here is what the work must produce, and "
                     "how it produces it is not said and must not be assumed.")
        lines.append("")
        if changes.data:
            lines.append("### Data")
            for d in changes.data:
                where = f"`{d.table}.{d.column}`" if d.column else f"`{d.table}`"
                bits = [f"- **{d.operation}** {where}"]
                if d.type:
                    bits.append(f"type `{d.type}`")
                bits.append("nullable" if d.nullable else "NOT NULL")
                if d.model:
                    bits.append(f"mapped by `{d.model}`")
                lines.append(", ".join(bits))
                if d.note:
                    lines.append(f"    - {d.note}")
            lines.append("")
        if changes.interface:
            lines.append("### Interfaces")
            for i in changes.interface:
                lines.append(f"- **{i.operation}** `{i.method} {i.path}`")
                if i.change:
                    lines.append(f"    - {i.change}")
            lines.append("")
        if changes.surfaces:
            lines.append("### What a person sees")
            for x in changes.surfaces:
                lines.append(f"- **{x.operation}** {x.where}")
                if x.change:
                    lines.append(f"    - {x.change}")
            lines.append("")

    for heading, items in (
        ("Libraries this feature adds", spec.new_dependencies),
        ("Non-goals", spec.non_goals),
        ("Constraints", spec.constraints),
        ("Assumptions", spec.assumptions),
        ("Open questions (accepted as risk)", spec.open_questions),
    ):
        if items:
            lines.append(f"## {heading}")
            lines += [f"- {i}" for i in items]
            lines.append("")
    if spec.resolved_answers:
        lines.append("## Decisions settled by the human at gate 1")
        for a in spec.resolved_answers:
            lines.append(f"- {a.question}\n    - {a.answer}  ({a.source})")
        lines.append("")
    return "\n".join(lines)


#: A package name, optionally pinned. Two shapes and no others: a plain name
#: with no slash in it, or an npm scoped name, which is `@scope/name` and has
#: exactly one. A general slash would let `backend/app/routers/queue.py`
#: through -- which is a file list, the second thing `verify_context` must
#: never be handed.
_PACKAGE_NAME = re.compile(
    r"^(?:@[A-Za-z0-9][A-Za-z0-9._-]{0,40}/)?"      # optional npm scope
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}"              # the name, never a path
    r"(==[A-Za-z0-9.\-+*]{1,40})?$"                 # optional pin
)

#: Extensions that mean this is a file someone named, not a package. A bare
#: `queue.py` would otherwise pass as a plain name, and while one filename is a
#: far weaker leak than a path, it is still not a package.
#: Code extensions only. `.yaml` and friends were in this list for one draft and
#: it dropped `ruamel.yaml`, which is a real package -- and a false negative here
#: has a cost, since the whole purpose is telling the oracle what it may import.
#: A bare `requirements.txt` getting through is one filename with no path and no
#: information about the implementation, which is the right side of that trade.
_SOURCE_SUFFIX = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb", ".java",
    ".sql", ".sh", ".md", ".c", ".h", ".cpp", ".cs", ".php", ".swift", ".kt",
)

#: How many names the verify lane may be told about. A ceiling rather than a
#: budget: a list this long is already telling the oracle what kind of project
#: this is, and beyond it the marginal name buys nothing.
_MAX_PACKAGES = 400


#: Tree-drawing and bullet characters a package manager puts in front of a name.
_TREE_PREFIX = "+`-|\u2500\u2514\u251c\u2502 \t"


def parse_package_lines(output: str) -> list[str]:
    """Package names out of a probe's output, ignoring everything around them.

    A probe runs through the same machinery as a gate, so its output carries
    whatever the runner said on the way in -- container names, health checks,
    npm's advice about funding. The first run of this took the leading word of
    every line and recorded `Container` as an installed package, which is
    harmless and wrong, and the kind of wrong that becomes load-bearing later.

    A real entry is one token on its own line once any tree drawing is stripped.
    Runner chatter is prose and has spaces in it; `SQLAlchemy==2.0.35` does not.

    npm writes `name@version` where pip writes `name==version`, so the npm form
    is normalised to the pinned spelling -- with the leading `@` of a scoped
    name left alone, since `@scope/pkg@1.2.3` splits at the *last* `@`.
    """
    out: list[str] = []
    for raw in output.splitlines():
        token = raw.strip().strip(_TREE_PREFIX).strip()
        if not token or " " in token or "\t" in token:
            continue
        if "==" not in token:
            at = token.rfind("@")
            if at > 0:
                token = token[:at] + "==" + token[at + 1:]
        out.append(token)
    return out


def installable_names(runtime: Sequence[str]) -> list[str]:
    """The subset of `runtime` that is actually a package name.

    The filter is the point, not the tidiness. `verify_context` must never be
    handed "a digest, a file list, or 'just the public interface so the tests
    compile'", and an extra parameter is exactly how one would arrive. This one
    is safe only because nothing shaped like those things can survive this
    function.

    A name matches or it is dropped. Prose, a path with spaces, a line of
    Python, a whole file: none of them are package names, and none of them
    arrive at the oracle.
    """
    seen: dict[str, None] = {}
    for entry in runtime:
        name = (entry or "").strip()
        if not name or name.lower().endswith(_SOURCE_SUFFIX):
            continue
        if _PACKAGE_NAME.match(name):
            seen.setdefault(name, None)
    return sorted(seen)[:_MAX_PACKAGES]


#: An import line and nothing else. Anchored at both ends, because the value of
#: this field is the form of the line and the risk of it is anything longer.
_IMPORT_LINE = re.compile(
    r"^(?:from\s+\S+\s+import\s+\S.*"          # python: from x import y
    r"|import\s+\S.*"                            # python / js: import x
    r"|(?:const|let|var)\s+\S+\s*=\s*require\(.*\).*"   # cjs
    r"|\w[\w.]*\s*=\s*require\(.*\).*)$"
)
_MAX_IMPORT_LINES = 8
_MAX_IMPORT_CHARS = 200


def import_lines(examples: Sequence[str]) -> list[str]:
    """The subset of `import_examples` that is actually an import line.

    The same filter, and the same reason, as `installable_names` one function
    up. This field exists so a blind test author can see whether a thing arrives
    as a default or by name -- which is the form of one line -- and a field that
    accepts one line will be handed a file eventually. Safe by shape: prose, a
    function body, a test, a docstring, none of them match, and none of them
    reach the oracle.

    Multi-line entries are split rather than dropped, because a survey that put
    two imports in one string meant two imports and there is nothing unsafe
    about the second one.

    But an import can itself be several lines, and splitting one of those on
    newlines produces `from app.services.outreach import (` -- a fragment that
    is not valid in any language and is handed over as the example to follow.
    A real resurvey proposed exactly that entry. So brackets are counted, and a
    line that opens one is joined to what follows until it closes; a bracket
    that never closes is dropped rather than passed on half-written.
    """
    seen: dict[str, None] = {}
    for entry in examples or ():
        held = ""
        for raw in str(entry or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            if held:
                held = f"{held} {line}"
            elif _IMPORT_LINE.match(line):
                held = line
            else:
                continue            # not an import, and not inside one
            if held.count("(") <= held.count(")") and held.count("{") <= held.count("}"):
                flat = " ".join(held.split())
                if len(flat) <= _MAX_IMPORT_CHARS:
                    seen.setdefault(flat, None)
                held = ""
    return list(seen)[:_MAX_IMPORT_LINES]


def fixture_inventory(surface: Any) -> str:
    """What a test in this project can already arrange, for the intake lane.

    The short form of `testing_context`: names and one-line descriptions, no
    setup-file contents. Intake does not need to see a conftest -- it needs to
    know whether the state a criterion depends on is something this repository
    can create, which is the question neither the interrogator nor the spec writer
    was ever in a position to ask.

    Empty for a survey that recorded no fixtures, so a project surveyed before
    they were recorded reads as "nothing known" rather than "nothing exists".
    """
    tiers = [t for t in (getattr(surface, "tiers", ()) or ()) if getattr(t, "fixtures", None)]
    if not tiers:
        return ""
    blocks = []
    for tier in tiers:
        listed = "\n".join(f"- {f}" for f in tier.fixtures)
        blocks.append(f"## {tier.tier}\n\n{listed}")
    return (
        "# What a test in this project can already arrange\n\n"
        "Recorded by the survey a human approved at gate 0. A behaviour whose "
        "starting state is not on this list cannot be checked here until "
        "something is added that creates it.\n\n"
        + "\n\n".join(blocks)
        + "\n"
    )


def cleanup_line(tier: Any) -> str:
    """How a test at this level undoes what it did, as this project does it.

    Said in the project's own words and never in the factory's: a schema made
    fresh per test, a transaction rolled back, a teardown that deletes, a
    workspace nobody else reads. There is no right mechanism, only the one the
    repository already uses -- and a test that follows a different one works
    until it runs beside the others.
    """
    said = getattr(tier, "cleanup", None)
    if said is None:
        return ""          # a reading that never asked; nothing is not "none"
    examples = [line for line in (getattr(tier, "cleanup_examples", ()) or ()) if line.strip()]
    if not said.strip():
        return (
            "**Nothing here cleans up after a test.** Tests at this level leave whatever "
            "they create for the tests that run after them. So a test you write here "
            "creates its own data, changes nothing it did not create, and removes what "
            "it made when it is done -- or, if nothing lets it, keeps it where no other "
            "test looks. If even that is impossible, say so rather than write a test "
            "that pollutes.")
    return ("**How a test here cleans up after itself:** " + said.strip()
            + (" Copied from tests that already pass here:\n\n"
               + "\n".join(f"    {line}" for line in examples) if examples else "")
            + "\n\nDo it the same way. A test that cleans up by some other mechanism, or "
            "not at all, passes alone and fails the tests that run after it.")


def cleanup_context(surface: Any) -> str:
    """Just the cleanup, by level, for an agent that is not given the whole surface.

    Workers, repairers and the breaker write tests too -- the unit tests that
    ship with the code, the seam checks, the probes -- and each one runs in the
    project's suite beside every other. They are not handed the testing surface
    in full; this is the part of it every test author needs.
    """
    lines = [f"## {t.tier}\n\n{line}" for t in list(getattr(surface, "tiers", ()) or ())
             if (line := cleanup_line(t))]
    if not lines:
        return ""
    return ("\n\n---\n\n# How tests in this project clean up after themselves\n\n"
            + "\n\n".join(lines))


#: Why a test level is not tested here, as a person reads it.
NO_RUNNER = "there's no test runner"
NO_CLEANUP = "tests can't clean up after themselves"
LEAKS = "its tests fail when run a second time, so they leave data behind"


def unchecked_levels(state: Any) -> dict[str, str]:
    """Test levels Fabrika does not test criteria at, and why.

    A level is tested when it has a runner and a way to leave the world as it
    found it: something resets for every test, each test keeps to its own
    data, or each test undoes its own changes. Without one, a test there fails
    for reasons that have nothing to do with the code -- a missing runner,
    another test's leftovers -- and a failure like that looks exactly like a
    broken feature. So those criteria are not attempted; a person checks them.

    A cleanup nobody was ever asked about is not a reason to skip: a reading
    from before the question existed says nothing either way. Unit tests share
    no data, so only a missing runner takes them out. A fix a person chose and
    Fabrika applied counts at once, without waiting for the next reading. And a
    level whose own tests were measured failing their second run leaks, whatever
    the reading says: the measurement outranks the reading.
    """
    surface = getattr(state, "testing", None)
    applied = getattr(state, "cleanup_applied", None) or {}
    # What was measured outranks what was read: a level whose own tests fail
    # their second run leaks, whatever the reading says cleans up.
    measured = {r.tier: r for r in (getattr(state, "testing_probe", None) or [])}
    out: dict[str, str] = {}
    for level in ("unit", "integration", "user"):
        tier = surface.tier(level) if surface is not None else None
        if tier is None or tier.verdict == "absent":
            out[level] = NO_RUNNER
            continue
        if level == "unit" or level in applied:
            continue
        probe = measured.get(level)
        if probe is not None and probe.leaves_clean is False:
            out[level] = LEAKS
            continue
        if tier.cleanup is None:
            continue
        by = tier.cleanup_by or ("nobody" if not tier.cleanup.strip() else None)
        if by == "nobody":
            out[level] = NO_CLEANUP
    return out


def manual_criteria(spec: Spec, state: Any) -> list[Any]:
    """The criteria a person checks by hand, because their level is not tested.

    A criterion with no level is tested wherever the oracle can reach it, as
    before: nothing says which level would have to be skipped.
    """
    if spec is None:
        return []
    skipped = unchecked_levels(state)
    return [c for c in spec.acceptance_criteria if c.verified_at in skipped]


def tested_spec(spec: Spec, state: Any) -> Spec:
    """The spec as the oracle sees it: without the criteria nobody can test
    here. Not asked and not attempted, so none of its time goes on them and
    none of its findings are about them."""
    manual = {c.id for c in manual_criteria(spec, state)}
    if not manual:
        return spec
    return spec.model_copy(update={"acceptance_criteria": [
        c for c in spec.acceptance_criteria if c.id not in manual]})


def testing_context(surface: Any) -> str:
    """What a new test at each level can be written against, for the oracle.

    Rendered from the survey a human approved at gate 0 -- the tiers and the
    contents of the setup files recorded there -- so nothing here reaches a
    filesystem and nothing arrives that was not reviewed. Safe by provenance,
    the same way the runtime contract is. (INV-1)

    The line it draws is the one that matters: a conftest is not the
    implementation. A fixture that authenticates as a tenant, or makes a
    patient, tells a test author how to talk to the application and nothing
    whatever about whether the feature works. Reading *how to reach the app* is
    not reading *what the app does*, and only the second corrupts a blind test.
    Setup files are shown in full; a file that asserts is never one of them,
    because that would be showing the oracle the answers.

    Without it the same knowledge has to be transcribed into configuration by
    hand, project by project, in prose -- and an oracle given none of it
    invents a fixture contract nobody has agreed to and can cost a run every
    one of its criteria.
    """
    tiers = list(getattr(surface, "tiers", ()) or ())
    if not tiers:
        return ""
    WORD = {
        "unit": "logic in isolation, no I/O",
        "integration": "the application's own interfaces -- HTTP, database, CLI",
        "user": "what a person sees and does",
    }
    RULE = {
        "usable": (
            "Write your tests against this setup. It is the project's own, it is "
            "what its tests already use, and it is shown in full below.\n\n"
            # The warning `inline_only` carries, which this rule needs even
            # more. A project with good fixtures invites
            # the assumption that everything is reachable through them, and the
            # gap is never a missing *capability* -- it is missing *knowledge*,
            # which has no channel of its own and so gets filled with a guess.
            # One oracle needed to stage rows before a migration ran, found no
            # fixture for it, wrote raw SQL against a schema it has never seen,
            # named a column that does not exist, and a criterion came back red
            # for a defect in the test while the code under it was sound.
            "What is listed here is what a test can arrange. Where you need a "
            "starting state none of it reaches -- rows as they stood before a "
            "migration, a table you would have to name column by column, a "
            "record only the database can make -- that is a `requires`, not a "
            "gap to improvise across. You have never seen this schema and you "
            "cannot derive it: a literal you invent for it is a guess wearing "
            "the costume of a test, and when it fails it fails as though the "
            "implementation were wrong. Saying you cannot reach the state costs "
            "one criterion, honestly, and naming the wrong column costs the "
            "same criterion while pointing a human at the wrong file."),
        "inline_only": (
            "There is nothing here to reuse: tests at this level exist but each "
            "builds its own world. Write the support you need as your own files, "
            "and where you need something this project cannot give you, put it in "
            "`requires`. Do not invent a precondition and assume somebody will "
            "honour it -- an oracle that did cost a run every criterion it had."),
        "absent": (
            "There is no runner at this level. Do not write tests for it and do "
            "not improvise one. Put the criteria that need it in "
            "`untestable_criteria`, with one `requires` entry naming the missing "
            "level. This is a fact about the project, it is already known, and "
            "reporting it costs the run nothing."),
    }
    blocks: list[str] = []
    for tier in tiers:
        head = (f"## {tier.tier} — {WORD.get(tier.tier, tier.tier)}\n\n"
                f"**{tier.verdict}**"
                + (f", runner: `{tier.runner}`" if tier.runner else ", no runner")
                + (f". {tier.note}" if tier.note else "."))
        body = [head, RULE.get(tier.verdict, "")]
        if tier.fixtures:
            body.append("Available to a test here: "
                        + ", ".join(f"`{f}`" for f in tier.fixtures) + ".")
        examples = import_lines(getattr(tier, "import_examples", ()) or ())
        if examples:
            body.append(
                "How a test here imports the code it is testing, copied from files "
                "that already pass in this repository:\n\n"
                + "\n".join(f"    {line}" for line in examples)
                + "\n\nFollow the form exactly -- the name style, the quoting, "
                "whether a thing arrives as a default or by name. The path is yours "
                "to work out from the feature and the directory you are writing in; "
                "the form is not something you can infer, and getting it wrong "
                "produces a file that imports nothing and checks nothing.")
        body.append(cleanup_line(tier))
        for setup in tier.setup_files or ():
            body.append(f"----- {setup.path} -----\n{setup.contents}")
        blocks.append("\n\n".join(b for b in body if b))
    disposable = getattr(surface, "disposable_db", None)
    extra = ""
    if disposable and (disposable.env or "").strip():
        extra = (
            "\n\n## a test db of your own\n\n"
            f"`{disposable.env}` names an **empty database nobody else is using**, and it "
            "is yours to migrate, seed, corrupt and abandon. It is not the database the "
            "rest of your suite talks to, and nothing that runs after you depends on what "
            "you leave in it.\n\n"
            + (f"Move it with: `{disposable.migrate}`\n\n" if disposable.migrate.strip() else "")
            + (disposable.note.strip() + "\n\n" if disposable.note.strip() else "")
            + "This is what makes a criterion about a *migration* testable: put the "
            "database at the revision before the change, write rows into it, apply the "
            "change, and look. A criterion about what `upgrade()` or `downgrade()` does, "
            "or about rows that existed beforehand, cannot be checked any other way -- "
            "the database your other tests use is already at the latest revision and is "
            "shared with a running server. Reading the migration file is not running it."
        )
    return (
        "\n\n---\n\n# What a test can be written against here\n\n"
        "This is the project's own test setup, by level. It is not the "
        "implementation and it says nothing about whether this feature works -- "
        "it is how a test reaches the application at all.\n\n"
        + "\n\n".join(blocks) + extra + "\n"
    )


def verify_context(spec: Spec, runtime: Sequence[str] = (),
                   where_they_run: str = "", testing: str = "", guides: str = "") -> str:
    """AC-5.4 / INV-1 -- the verify lane's entire view of the world.

    Three parameters, and the two after the spec are narrow on purpose in two
    different ways.

    The first is the spec, typed `Spec`, and it comes alone: no digest, no
    file list, no "just the public interface
    so the tests compile". That temptation is the failure this function exists
    to prevent, and an extra argument is exactly how it would arrive.

    So neither of the others can carry it, and the reasons differ. `runtime` is
    safe by *shape*: it is filtered through `installable_names`, and a string
    either matches the pattern of a package name or is dropped. Source code
    cannot be smuggled through a field that only accepts `psycopg2==2.9.9`.

    `where_they_run` is prose and cannot be filtered that way, so it is safe by
    *provenance* instead: it comes from `ProjectState.runtime_contract()`, which
    is a pure function of that one project's record -- the services it declares,
    the variables its own test commands set, and a human's sentences about it.
    That record is not the repository, and it is not another project's. No caller may assemble this argument
    from anything read off disk, and `test_v1_the_lane_is_called_with_the_spec_alone`
    pins the one expression allowed to produce it.

    Both are here because total blindness has costs, and they are the same
    cost twice. An oracle that does not know its packages writes a
    `conftest.py` importing `psycopg2`, which is not installed; the suite fails
    to collect and every criterion it covers (twelve, in one case) comes back
    unverified. Knowing its packages but not its environment, it writes a
    suite demanding a live system be provisioned through a JSON fixture nobody
    sets, and every criterion (twenty-five) comes back unverified while the
    packet reports the feature as the problem.

    Knowing that `httpx` exists, or that a server will be listening on
    `API_BASE_URL`, tells you nothing about whether this feature works. Not
    knowing costs every criterion in the packet.
    """
    packages = installable_names(runtime)
    block = ""
    if packages:
        block = (
            "\n\n---\n\n# What is installed where your tests will run\n\n"
            + ", ".join(packages)
            + "\n\nThis is the environment, not the implementation. It tells you what your "
            "test file may import and nothing whatever about whether the feature works. "
            "Import only from this list and the standard library: a test that cannot be "
            "collected asserts nothing, and one that fails on a missing import costs a "
            "human the time to find out that is all it was.\n"
        )
    if where_they_run.strip():
        block += (
            "\n\n---\n\n# What your tests can reach when they run\n\n"
            + where_they_run.strip()
            + "\n\nThis is the whole environment your suite gets. It is the system under "
            "test as it will actually be standing when your command runs, and it is the "
            "only way you have to reach the feature.\n\n"
            "**Everything not stated here is not there.** No fixture anyone else wrote, "
            "no seeded row you did not create, no environment variable that is not named "
            "above. If you need something this section does not give you, you cannot "
            "arrange for it and you must not write a test that assumes someone will: a "
            "suite that stops on a missing fixture reports nothing about the feature and "
            "costs the run every criterion it was meant to check. Put those criteria in "
            "`untestable_criteria` and say in `notes` exactly what you would have needed. "
            "An honest gap a human can close next run is worth more than a suite that "
            "cannot start.\n"
        )
    # The project's approved testing guides, and nothing else a person wrote
    # about the code: its tests join the repository, so they are written the
    # way the project writes tests. Safe by provenance, as `testing` is --
    # approved documents, rendered by code, never anything a model read in the
    # code -- and pinned by the same test.
    if guides.strip():
        block += "\n\n---\n\n" + guides.strip() + "\n"
    return (
        spec_text(spec)
        + block
        + testing
        + "\n\n---\n\n"
        + "You have not seen the implementation and you will not see it. "
        + "Everything you know about this feature is above.\n"
    )


# --------------------------------------------------------------------------
# the build lane's view
# --------------------------------------------------------------------------


def _is_probably_text(path: Path) -> bool:
    if path.suffix.lower() in SKIP_SUFFIXES:
        return False
    try:
        chunk = path.open("rb").read(2048)
    except OSError:
        return False
    return b"\0" not in chunk


def _git_listed_files(root: Path) -> list[Path] | None:
    """What git considers part of this repository.

    A real project keeps far more on disk than it tracks -- logs, scraped data,
    database dumps, dependency caches -- and a hand-maintained skip list will
    always be behind. On one real repository this is the difference between
    walking 750 million characters and reading the 11 million that are actually
    the codebase. `.gitignore` is the answer the project already wrote down.

    Untracked-but-not-ignored files are included, so work in progress is visible.
    """
    if shutil.which("git") is None:
        return None
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root, capture_output=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    out: list[Path] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        rel = Path(raw.decode("utf-8", errors="replace"))
        if any(part in SKIP_DIRS for part in rel.parts) and not _kept(rel):
            continue
        if rel.suffix.lower() in SKIP_SUFFIXES:
            continue
        path = root / rel
        if path.is_file():
            out.append(path)
    return sorted(out)


def iter_repo_files(root: Path) -> list[Path]:
    """Every file that is the repository, and nothing Fabrika wrote into it."""
    return [p for p in _all_repo_files(root) if not is_generated(p.relative_to(root).as_posix())]


def _all_repo_files(root: Path) -> list[Path]:
    listed = _git_listed_files(root)
    if listed is not None:
        return listed
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames
            if (d not in SKIP_DIRS and not d.startswith(".egg"))
            # Descend into a skipped directory when something under it is kept.
            or any((here / d).relative_to(root).as_posix().startswith(k.rstrip("/").rsplit("/", 1)[0])
                   for k in KEEP_PREFIXES)
        )
        for name in sorted(filenames):
            p = Path(dirpath) / name
            rel = p.relative_to(root)
            if not _kept(rel) and (p.name in SKIP_DIRS or p.suffix.lower() in SKIP_SUFFIXES):
                continue
            if any(part in SKIP_DIRS for part in rel.parts) and not _kept(rel):
                continue
            out.append(p)
    return out


_DIGEST_FIRST_NAMES = {
    "readme.md", "package.json", "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg",
    "go.mod", "cargo.toml", "gemfile", "pom.xml", "build.gradle", "makefile", "justfile",
    "dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
    "tox.ini", "pytest.ini", "tsconfig.json", "vite.config.ts", "vite.config.js", ".env.example",
}
_DIGEST_LAST_PARTS = {
    "alembic", "migrations", "versions", "fixtures", "demo-data", "seeds", "snapshots",
    "tests", "test", "__tests__", "e2e", "docs",
}
_DIGEST_LAST_SUFFIXES = {".sql", ".csv", ".json", ".svg", ".css", ".af", ".md", ".txt"}


_LAUNCHER_CONFIGS = ("playwright.config.ts", "playwright.config.js", "playwright.config.mjs",
                     "cypress.config.ts", "cypress.config.js")
_LAUNCHED = re.compile(r"""['"`]((?:\.{1,2}/)[\w./-]+\.(?:sh|bash|py|js|mjs|cjs|ts))\b""")


def launched_scripts(root: str | Path) -> list[str]:
    """Scripts a browser-test config starts before any test runs, repo-relative.

    `webServer: { command: './e2e/support/start-backend.sh' }` is where a
    project's end-to-end suite says what it needs to exist -- a database, an
    API -- and the script holds the answer. It is not a manifest and matches no
    tooling glob, so a reading saw the config, never what the config runs, and
    proposed a check whose script began with `docker run` in an environment
    that has no Docker. Named from the config rather than guessed at by
    pattern, relative to the directory the config sits in.
    """
    root = Path(root).expanduser().resolve()
    found: list[str] = []
    for path in iter_repo_files(root):
        if path.name not in _LAUNCHER_CONFIGS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _LAUNCHED.finditer(text):
            target = (path.parent / match.group(1)).resolve()
            try:
                rel = target.relative_to(root).as_posix()
            except ValueError:
                continue
            if target.is_file() and rel not in found:
                found.append(rel)
    return found


def _digest_tier(rel: Path) -> int:
    """Where a file falls in the order the digest spends its budget.

    Alphabetical order spent all of it on whatever directory sorted first --
    on one repository, 149 files of `backend/` and none of the frontend, the
    compose file or the CI config, which are what a survey is for. What says
    how the project is built and run comes first, source next, and what only
    illustrates it (tests, migrations, data, generated assets) last.
    """
    if rel.name.lower() in _DIGEST_FIRST_NAMES or rel.parts[0] in (".github", ".gitlab-ci.yml"):
        return 0
    if any(part in _DIGEST_LAST_PARTS for part in rel.parts) or rel.suffix.lower() in _DIGEST_LAST_SUFFIXES:
        return 2
    return 1


def repo_digest(root: str | Path, budget: int = 120_000, name: str = "") -> str:
    """AC-5.2 -- a bounded textual view of the repo for the scout and reviewer.

    `name` is what the heading calls it, for a checkout whose directory is not
    the project's name -- a base-commit worktree lives in a directory called
    `tree`, and a sandbox in one named after the feature.
    """
    root = Path(root).expanduser().resolve()
    if not root.exists():
        return f"(no repo at {root})"

    files = iter_repo_files(root)
    tree_lines = [str(p.relative_to(root)) for p in files]
    header = [f"# Repository: {name or root.name}", "", f"## File tree ({len(files)} files)", ""]
    header += tree_lines[:600]
    if len(tree_lines) > 600:
        header.append(f"... and {len(tree_lines) - 600} more files")
    header += ["", "## Contents", ""]

    parts = ["\n".join(header)]
    used = len(parts[0])
    truncated_files = 0
    skipped_files = 0

    launched = set(launched_scripts(root))
    for p in sorted(files, key=lambda f: 0 if f.relative_to(root).as_posix() in launched
                    else _digest_tier(f.relative_to(root))):
        if used >= budget:
            skipped_files += 1
            continue
        if not _is_probably_text(p):
            continue
        rel = p.relative_to(root)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        note = ""
        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS]
            note = f"\n... [truncated: file is longer than {MAX_FILE_CHARS} characters]"
            truncated_files += 1
        block = f"----- {rel} -----\n{text}{note}\n"
        if used + len(block) > budget:
            remaining = max(0, budget - used)
            block = block[:remaining] + "\n... [digest budget exhausted]\n"
            skipped_files += 1
        parts.append(block)
        used += len(block)

    footer = []
    if truncated_files:
        footer.append(f"[{truncated_files} file(s) truncated]")
    if skipped_files:
        footer.append(f"[{skipped_files} file(s) omitted: digest budget of {budget} characters reached]")
    if footer:
        parts.append("\n".join(footer))
    return "\n".join(parts)


# --------------------------------------------------------------------------
# the symbol index
#
# Slicing costs one thing: no scout sees the whole repository, so no scout can
# know that a name is defined twice. That is not a judgment call, though --
# it is a lookup, and code can do it exactly where attention over 200k tokens
# does it probabilistically. Same principle as INV-3, one level up: compute the
# facts, ask the model only for the judgment.
# --------------------------------------------------------------------------

SYMBOL_PATTERNS = [
    re.compile(r"^\s*(?:async\s+)?def\s+(\w+)"),
    re.compile(r"^\s*class\s+(\w+)"),
    re.compile(r"^\s*export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)"),
    re.compile(r"^\s*export\s+(?:const|let|var)\s+(\w+)"),
    re.compile(r"^\s*export\s+(?:interface|type|enum|class)\s+(\w+)"),
    re.compile(r"^\s*(?:interface|type|enum)\s+(\w+)\s*[=<{]"),
]
INDEXABLE = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rb", ".java", ".kt"}
# A test function is never something you would accidentally reimplement, which
# is the question this index exists to answer. Counting them also drowns the
# signal: a suite of 400 `def test_*` outranks the module it tests.
TEST_MARKERS = ("test", "tests", "spec", "specs", "__tests__", "e2e")


#: Files that look like reusable test *setup* rather than tests: whatever a
#: runner loads on its own, and the modules people put fixtures and factories in.
_SETUP_NAMES = ("conftest", "setuptests", "setup_tests", "fixtures", "factories",
                "factory", "helpers", "testing", "render", "playwright.config",
                "cypress.config", "vitest.config", "jest.config", "jest.setup")


def is_setup_name(name: str) -> bool:
    """Whether a filename is reusable test *setup* rather than a test.

    Exact stem match, where `testing_paths` below also accepts a prefix. The
    generosity that is right for deciding what to show a reading is wrong for
    deciding what may be written into a test directory: `testing_the_ranking.py`
    starts with a setup name and is a suite.
    """
    return name.lower().rsplit(".", 1)[0] in _SETUP_NAMES


#: Directories people keep test support in, by name. A file in one of them is
#: setup unless it is named like a test: no runner's default pattern collects
#: `e2e/support/workspace.ts` or `tests/helpers/api.py`, and those are where a
#: helper that every new test should import belongs.
_SETUP_DIRS = ("support", "helpers", "fixtures", "factories", "testing")


def is_setup_path(rel: str) -> bool:
    """Whether a repo path is reusable test setup rather than a test.

    The name decides first (`is_setup_name`). Otherwise a file inside a setup
    directory counts, unless its own name is a test's -- or it sits under
    `__tests__`, where Jest collects every file whatever it is called.
    """
    parts = rel.replace("\\", "/").split("/")
    if is_setup_name(parts[-1]):
        return True
    dirs = [p.lower() for p in parts[:-1]]
    if "__tests__" in dirs or _is_test_name(parts[-1]):
        return False
    return any(d in _SETUP_DIRS for d in dirs)


def testing_paths(
    root: str | Path, limit: int = 40, known: Sequence[str] = (),
) -> list[str]:
    """Files that would tell a reader what a NEW test here can be written against.

    Setup first, then a sample of the tests themselves. Both matter and they
    answer different halves: setup is what a new test could reuse, and the tests
    are how you tell reusable setup from a suite where every file builds its own
    world -- which is the distinction between `usable` and `inline_only`, and the
    one nothing could see before.

    Bounded, because this rides in a prompt beside everything else. A sample is
    enough: the question is what the suite's shape is, not what every test says.
    """
    root = Path(root).expanduser().resolve()
    # Files something already established are setup, whatever they are called:
    # scaffolding a human applied, and the setup files the last approved reading
    # recorded. Provenance, not spelling.
    #
    # `_SETUP_NAMES` below is a guess at what a setup file tends to be called,
    # and a guess is all it can be. It missed a real one in the worst possible
    # way: the factory proposed `frontend/src/test-utils.tsx` as scaffolding, a
    # human wrote it, and the next reading could not see the file the factory
    # had asked for -- so it reported that tier as having nothing to reuse and
    # described, as missing, the helper sitting in front of it. A name table
    # cannot be completed; this is the half that does not depend on one.
    seen: set[str] = set()
    setup: list[str] = []
    for rel in known:
        rel = str(rel or "").strip().lstrip("./")
        if rel and rel not in seen and (root / rel).is_file():
            seen.add(rel)
            setup.append(rel)
    tests: list[str] = []
    for path in iter_repo_files(root):
        rel = path.relative_to(root).as_posix()
        if rel in seen:
            continue
        stem = path.name.lower().rsplit(".", 1)[0]
        if stem.startswith(_SETUP_NAMES) or stem in _SETUP_NAMES:
            setup.append(rel)
        elif _is_test_path(rel):
            tests.append(rel)
    return (setup + tests)[:limit]


def _is_test_name(name: str) -> bool:
    lower = name.lower()
    return lower.startswith(("test_", "spec_")) or lower.rsplit(".", 1)[0].endswith(
        ("_test", "_spec", ".test", ".spec"))


def _is_test_path(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    return any(part.lower() in TEST_MARKERS for part in parts[:-1]) or _is_test_name(parts[-1])


@dataclass
class SymbolIndex:
    by_file: dict[str, list[str]] = field(default_factory=dict)
    by_name: dict[str, list[str]] = field(default_factory=dict)

    @property
    def duplicates(self) -> dict[str, list[str]]:
        return {n: p for n, p in self.by_name.items() if len(p) > 1}

    def render(self, budget: int = 60_000) -> str:
        """Duplicates first and in full -- they are the whole point. The per-file
        listing fills whatever budget is left."""
        dupes = self.duplicates
        parts = [
            "# Symbol index (computed, not inferred)",
            "",
            "Every name defined anywhere in this repository, including the parts you were not "
            "given. Use it to answer 'does this already exist' without having read the file.",
            "",
        ]
        if dupes:
            parts += [
                f"## Defined in more than one file ({len(dupes)})",
                "",
                "These are where duplication already exists, or where two layers deliberately "
                "mirror each other and must stay in step. Either way they belong in "
                "`do_not_duplicate` if they bear on the intent.",
                "",
            ]
            for name, paths in sorted(dupes.items()):
                parts.append(f"{name}: {', '.join(sorted(paths)[:6])}")
            parts.append("")

        # Duplicates are the reason this exists, so they are written first and
        # trimmed last. If even they do not fit, say so rather than silently
        # showing a prefix.
        if sum(len(x) + 1 for x in parts) > budget:
            keep = []
            used = 0
            for line in parts:
                if used + len(line) > budget - 120:
                    keep.append(f"... [{len(dupes)} duplicated names, list truncated]")
                    break
                keep.append(line)
                used += len(line) + 1
            return "\n".join(keep) + "\n"

        used = sum(len(x) + 1 for x in parts)
        parts += [f"## Defined once, by file ({len(self.by_file)} files)", ""]
        singles = {n for n, p in self.by_name.items() if len(p) == 1}
        for path in sorted(self.by_file):
            names = [n for n in self.by_file[path] if n in singles]
            if not names:
                continue
            line = f"{path}: {', '.join(names)}"
            if used + len(line) > budget:
                parts.append(f"... [index truncated at {budget} characters]")
                break
            parts.append(line)
            used += len(line) + 1
        return "\n".join(parts) + "\n"


def symbol_index(root: str | Path, paths: list[Path] | None = None) -> SymbolIndex:
    root = Path(root).expanduser().resolve()
    index = SymbolIndex()
    for path in (paths if paths is not None else iter_repo_files(root)):
        if path.suffix.lower() not in INDEXABLE:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(root))
        if _is_test_path(rel):
            continue
        for line in text.splitlines():
            if len(line) > 400:
                continue
            for pattern in SYMBOL_PATTERNS:
                match = pattern.match(line)
                if not match:
                    continue
                name = match.group(1)
                if name.startswith("_") or len(name) < 3:
                    break
                index.by_file.setdefault(rel, [])
                if name not in index.by_file[rel]:
                    index.by_file[rel].append(name)
                paths_for = index.by_name.setdefault(name, [])
                if rel not in paths_for:
                    paths_for.append(rel)
                break
    return index


# --------------------------------------------------------------------------
# slicing
#
# A 1.5M-character repository does not fit in a small model's context, and
# truncating to the alphabetically-first 8% is the worst available answer: it
# looks like a complete report and is a partial one. Cut the repo into slices
# that each fit comfortably, scout every slice, and union the results.
# --------------------------------------------------------------------------


@dataclass
class RepoSlice:
    name: str
    paths: list[Path]
    chars: int
    score: float = 0.0


def repo_tree(root: str | Path, limit: int = 1200) -> str:
    """Every path, always. Cheap, and it lets a scout reason about what exists
    in the parts of the repo it was not given."""
    root = Path(root).expanduser().resolve()
    files = iter_repo_files(root)
    lines = [str(p.relative_to(root)) for p in files]
    body = "\n".join(lines[:limit])
    if len(lines) > limit:
        body += f"\n... and {len(lines) - limit} more"
    return f"# Every file in this repository ({len(files)})\n\n{body}\n"


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 2}


def slice_repo(
    root: str | Path,
    slice_chars: int,
    max_slices: int,
    focus: str = "",
    file_cap: int = SLICE_FILE_CHARS,
    index: "SymbolIndex | None" = None,
) -> tuple[list[RepoSlice], list[RepoSlice]]:
    """Pack the repository into slices, most relevant first.

    Grouped by directory so a slice holds code that belongs together, then
    packed to `slice_chars`. Returns (kept, dropped) -- dropped is never empty
    silently: the caller records it.
    """
    root = Path(root).expanduser().resolve()
    wanted = _tokens(focus)

    groups: dict[str, list[Path]] = {}
    for path in iter_repo_files(root):
        if not _is_probably_text(path):
            continue
        rel = path.relative_to(root)
        key = "/".join(rel.parts[:2]) if len(rel.parts) > 1 else "."
        groups.setdefault(key, []).append(path)

    # Split any directory too big for one slice, then pack the pieces so a
    # 219-character Dockerfile does not consume a whole call.
    units: list[tuple[str, list[Path], int]] = []
    for key, paths in sorted(groups.items()):
        current: list[Path] = []
        size = 0
        part = 0
        for path in sorted(paths):
            try:
                length = min(path.stat().st_size, file_cap)
            except OSError:
                continue
            if current and size + length > slice_chars:
                part += 1
                units.append((f"{key}#{part}", current, size))
                current, size = [], 0
            current.append(path)
            size += length
        if current:
            part += 1
            units.append((f"{key}#{part}" if part > 1 else key, current, size))

    slices: list[RepoSlice] = []
    names: list[str] = []
    paths_acc: list[Path] = []
    size_acc = 0
    for name, paths, size in units:
        if paths_acc and size_acc + size > slice_chars:
            slices.append(RepoSlice(_slice_name(names), paths_acc, size_acc))
            names, paths_acc, size_acc = [], [], 0
        names.append(name)
        paths_acc.extend(paths)
        size_acc += size
    if paths_acc:
        slices.append(RepoSlice(_slice_name(names), paths_acc, size_acc))

    # Relevance decides which slices survive a cap, not which files a slice
    # holds. On a repository too big to read entirely, this is the difference
    # between reading the ten percent that bears on the intent and reading
    # whatever sorts first.
    for sl in slices:
        paths = [str(p.relative_to(root)) for p in sl.paths]
        path_hits = len(wanted & _tokens(" ".join(paths))) if wanted else 0

        symbol_hits = 0
        defines = 0
        if index is not None:
            names: set[str] = set()
            for rel in paths:
                found = index.by_file.get(rel, [])
                defines += len(found)
                names.update(found)
            if wanted:
                # A slice defining `ScreeningSlot` matters to an intent about
                # screening slots even when no path says so.
                symbol_hits = len(wanted & _tokens(" ".join(names)))

        # Files that *define* things tell a scout more than files that *use*
        # them: a test suite mentions the domain constantly and declares almost
        # nothing, so without this it outranks the code it is testing.
        density = min(2.0, defines / 40.0)

        # Size is only the tiebreak. A tie broken by alphabet is the failure
        # this whole function exists to replace.
        sl.score = round(
            (path_hits * 2) + (symbol_hits * 3) + density
            + min(0.5, sl.chars / max(1, slice_chars)),
            2,
        )

    ordered = sorted(slices, key=lambda x: (-x.score, x.name))
    return ordered[:max_slices], ordered[max_slices:]


def _slice_name(names: list[str]) -> str:
    if not names:
        return "slice"
    if len(names) == 1:
        return names[0]
    return f"{names[0]} … {names[-1]} ({len(names)} areas)"


def slice_digest(
    root: str | Path, sl: RepoSlice, tree: str, file_cap: int = SLICE_FILE_CHARS,
) -> str:
    """One slice, rendered: the whole tree for orientation, these files in full."""
    root = Path(root).expanduser().resolve()
    parts = [tree, f"\n# The part of the repository you are reading: {sl.name}\n"]
    for path in sl.paths:
        rel = path.relative_to(root)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        note = ""
        if len(text) > file_cap:
            text = text[:file_cap]
            note = f"\n... [truncated: longer than {file_cap} characters]"
        parts.append(f"----- {rel} -----\n{text}{note}\n")
    return "\n".join(parts)


def slice_coverage(
    kept: list["RepoSlice"], dropped: list["RepoSlice"], root: str | Path,
    file_cap: int = SLICE_FILE_CHARS,
) -> dict[str, int | float]:
    """What the scouts will collectively have seen, against what exists."""
    root = Path(root).expanduser().resolve()
    total = 0
    files = 0
    for path in iter_repo_files(root):
        if not _is_probably_text(path):
            continue
        files += 1
        try:
            total += min(path.stat().st_size, file_cap)
        except OSError:
            continue
    carried = sum(sl.chars for sl in kept)
    return {
        "slices": len(kept),
        "slices_dropped": len(dropped),
        "files": files,
        "files_read": sum(len(sl.paths) for sl in kept),
        "chars": total,
        "chars_read": carried,
        "covered": round(carried / total, 4) if total else 1.0,
        "file_cap": file_cap,
    }


def digest_coverage(root: str | Path, budget: int) -> dict[str, int | float]:
    """How much of the repository the digest could actually carry.

    Cheap: file sizes, not contents. Recorded on every run because "the agent
    misunderstood the codebase" and "the agent was shown 8% of the codebase"
    look identical from the outside, and only one of them is the agent's fault.
    """
    root = Path(root).expanduser().resolve()
    if not root.exists():
        return {"files": 0, "chars": 0, "budget": budget, "covered": 0.0}

    total = 0
    files = 0
    for path in iter_repo_files(root):
        if not _is_probably_text(path):
            continue
        files += 1
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return {
        "files": files,
        "chars": total,
        "budget": budget,
        "covered": round(min(1.0, budget / total), 4) if total else 1.0,
    }


# --------------------------------------------------------------------------
# path safety
# --------------------------------------------------------------------------


class PathEscape(ValueError):
    pass


def safe_join(root: str | Path, relative: str) -> Path:
    """Resolve `relative` under `root`, or refuse. Used on every model-authored path."""
    root = Path(root).expanduser().resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise PathEscape(f"path {relative!r} escapes {root}") from None
    if Path(relative).is_absolute():
        raise PathEscape(f"path {relative!r} is absolute")
    return candidate


# --------------------------------------------------------------------------
# worker isolation
# --------------------------------------------------------------------------


class WorktreeError(RuntimeError):
    """A checkout that could not be made. Never a checkout of the wrong thing."""


def uncommitted_paths(root: str | Path) -> list[str]:
    """Paths in `root` that differ from its last commit.

    A checkout taken with `git worktree add HEAD` reproduces the last commit and
    nothing else, so this is the difference between the tree an agent is given
    and the tree everyone else is looking at. Empty is the expected answer
    everywhere it is called; a non-empty one is worth saying out loud, because
    an agent working from stale code fails in a way that reads as the code being
    wrong.
    """
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=str(root), capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    out: list[str] = []
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        out.append(path.strip('"'))
    return out


class BlankTree:
    """An empty git repository, for an agent that must not see the project.

    Not what the oracle gets -- see `BaseTree`, which gives it the repository
    as it stood before the work instead. The reasoning below is right about the
    half it addresses: an invariant enforced by omission survives only until
    somebody adds a helpful paragraph, and moving it onto the filesystem is
    what makes it real. For the oracle, the line it draws is wrong; the
    mechanism is not.

    INV-1 says the verify lane cannot read the implementation. Without a
    harness that is enforced by omission -- the code is simply not put in the
    prompt -- and omission is the weakest form an invariant can take, because
    it holds only as long as nobody adds a helpful paragraph.

    A harness has a filesystem, so the invariant moves onto the filesystem: the
    directory it works in contains nothing. Not a trimmed checkout, not the
    project's own tests, not a manifest. There is no implementation here to
    read, and `assert_blank` says so out loud before the agent starts, because
    an isolation property that is never checked is a property that has already
    quietly stopped holding somewhere.

    It is a git repository rather than a bare directory only because the harness
    reads `git status` to decide whether the agent wrote anything -- the same
    mechanism, on a tree whose baseline happens to be empty.
    """

    def __init__(self, label: str = "blind") -> None:
        self.label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
        self.path: Path | None = None

    def __enter__(self) -> "BlankTree":
        base = Path(tempfile.mkdtemp(prefix=f"factory-{self.label}-"))
        target = base / "tree"
        target.mkdir(parents=True)
        if shutil.which("git") is None:
            raise WorktreeError(
                "git is required to give the verify lane an empty tree: the harness "
                "reads `git status` to tell what the agent wrote"
            )
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "factory", "GIT_AUTHOR_EMAIL": "factory@localhost",
            "GIT_COMMITTER_NAME": "factory", "GIT_COMMITTER_EMAIL": "factory@localhost",
        }
        for argv in (
            ["git", "init", "--quiet"],
            # A root commit, so `git rev-parse HEAD` answers and the harness has
            # a baseline to reset onto. `--allow-empty` because there is by
            # construction nothing to commit.
            ["git", "commit", "--quiet", "--allow-empty", "-m", "empty"],
        ):
            r = subprocess.run(argv, cwd=target, capture_output=True, text=True,
                               timeout=60, env=env)
            if r.returncode != 0:
                shutil.rmtree(base, ignore_errors=True)
                raise WorktreeError(
                    f"could not prepare an empty tree ({' '.join(argv)}): "
                    f"{(r.stderr or r.stdout).strip()}"
                )
        self.path = target
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.path is not None:
            shutil.rmtree(self.path.parent, ignore_errors=True)
            self.path = None

    def assert_blank(self) -> None:
        """Refuse to start if anything but git's own bookkeeping is here.

        Cheap, and it is the check that would catch the one change nobody would
        think to review: a future caller reusing this class with a directory
        that is not actually empty.
        """
        if self.path is None:
            raise WorktreeError("blank tree is not open")
        intruders = sorted(
            p.name for p in self.path.iterdir() if p.name != ".git"
        )
        if intruders:
            raise WorktreeError(
                "the verify lane's tree is not empty, so the oracle could read "
                f"something it must not see: {', '.join(intruders[:10])}"
            )


class BaseTree:
    """The repository as it stood before this feature, for the verify lane.

    Not `BlankTree` -- an empty directory, checked empty before the oracle
    starts. That enforces INV-1 on the filesystem instead of by omission, which
    is right, but it enforces more than INV-1 says. "Cannot read the
    implementation" becomes "cannot read anything", and those are not the same
    claim: `tenants.display_name` is not the implementation of a feature that
    does not touch `tenants`. It is the world the feature acts on.

    What that costs is exactly what you would predict. An oracle asked to stage
    rows as they stood before a migration, with no schema to stage them
    against, writes raw SQL naming `tenants.name` for a column called
    `display_name` -- and the criterion comes back red for a defect in the test
    while the migration under it is sound. A blind test author who cannot read
    the existing code does not write a more independent test. It writes a
    test with guesses in it, and a guess fails as though the implementation
    were wrong.

    So: a worktree at the commit the feature branched from. Everything that
    existed before the work -- source, tests, fixtures, documentation, config --
    and by construction not one line of the work itself, because none of it is
    in that commit. The isolation is still on the filesystem and still checked,
    and the check is the harder one: not "is this empty" but "is this the
    base commit and not the branch". An empty tree that is accidentally full
    costs a run its verification; a base tree that is accidentally the branch
    costs it every green criterion it reports, which is worse, so
    `assert_at_base` refuses rather than warns.
    """

    def __init__(self, repo_root: str | Path, base_sha: str, label: str = "blind") -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.base_sha = (base_sha or "").strip()
        self.label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
        self.path: Path | None = None
        self._holder: Path | None = None

    def __enter__(self) -> "BaseTree":
        if not self.base_sha:
            raise WorktreeError(
                "the verify lane needs the commit this feature branched from, and "
                "nothing recorded one. Without it there is no tree that is provably "
                "free of the implementation."
            )
        if not has_commit(self.repo_root):
            raise WorktreeError(
                "git is required to give the verify lane the repository as it stood "
                "before this work"
            )
        holder = Path(tempfile.mkdtemp(prefix=f"factory-{self.label}-"))
        target = holder / "tree"
        r = subprocess.run(
            ["git", "worktree", "add", "--detach", str(target), self.base_sha],
            cwd=self.repo_root, capture_output=True, text=True, timeout=300,
        )
        if r.returncode != 0:
            shutil.rmtree(holder, ignore_errors=True)
            raise WorktreeError(
                f"could not check out {self.base_sha[:12]} for the verify lane: "
                f"{(r.stderr or r.stdout).strip()[:400]}"
            )
        self._holder, self.path = holder, target
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.path is not None:
            subprocess.run(["git", "worktree", "remove", "--force", str(self.path)],
                           cwd=self.repo_root, capture_output=True, timeout=120)
        if self._holder is not None:
            shutil.rmtree(self._holder, ignore_errors=True)
        self.path = self._holder = None

    def assert_at_base(self) -> None:
        """Refuse to start unless this checkout is the base commit.

        The one check worth more than the tree it guards. An oracle handed the
        feature branch reads the implementation, writes tests against it, and
        every criterion it then reports green is worth nothing -- while looking
        exactly like a run that verified the work. That failure is silent and
        flattering, which is the shape this whole system exists to refuse.
        """
        if self.path is None:
            raise WorktreeError("base tree is not open")
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(self.path),
                           capture_output=True, text=True, timeout=60)
        head = (r.stdout or "").strip()
        if r.returncode != 0 or not head:
            raise WorktreeError(
                "could not read the verify lane's checkout, so it cannot be shown free "
                "of the implementation"
            )
        if not (head.startswith(self.base_sha) or self.base_sha.startswith(head)):
            raise WorktreeError(
                f"the verify lane's tree is at {head[:12]}, not the base commit "
                f"{self.base_sha[:12]}. An oracle that reads the implementation "
                "verifies nothing, and reports that it did."
            )


class Worktree:
    """AC-5.5 -- one isolated checkout per worker.

    `git worktree add --detach` where git allows it, a filtered copy where it
    does not. The isolation property holds either way; only the speed differs.
    """

    def __init__(self, repo_root: str | Path, label: str = "unit", ref: str = "HEAD") -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
        self.path: Path | None = None
        self._mode = "copy"
        # Any ref other than HEAD is only meaningful in git mode. The copy
        # fallback reproduces the working tree, and handing that back as though
        # it were an older commit would answer "did this feature break the gate"
        # against the wrong code -- a wrong answer where none was available.
        self.ref = ref

    def __enter__(self) -> "Worktree":
        base = Path(tempfile.mkdtemp(prefix=f"factory-{self.label}-"))
        target = base / "tree"
        if has_commit(self.repo_root):
            r = subprocess.run(
                ["git", "worktree", "add", "--detach", str(target), self.ref],
                cwd=self.repo_root, capture_output=True, text=True, timeout=180,
            )
            if r.returncode == 0:
                self._mode = "git"
                self.path = target
                return self
        if self.ref != "HEAD":
            shutil.rmtree(base, ignore_errors=True)
            raise WorktreeError(
                f"cannot check out {self.ref!r} in {self.repo_root}: git is unavailable here and "
                "the copy fallback can only reproduce the working tree"
            )
        shutil.copytree(
            self.repo_root, target,
            ignore=shutil.ignore_patterns(*SKIP_DIRS),
            symlinks=True, dirs_exist_ok=True,
        )
        self._mode = "copy"
        self.path = target
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.path is None:
            return
        if self._mode == "git":
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(self.path)],
                cwd=self.repo_root, capture_output=True, timeout=120,
            )
        shutil.rmtree(self.path.parent, ignore_errors=True)
        self.path = None

    @property
    def mode(self) -> str:
        return self._mode

    def write(self, relative: str, contents: str) -> Path:
        """Path-checked against escaping the tree."""
        if self.path is None:
            raise RuntimeError("worktree is not open")
        target = safe_join(self.path, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
        return target

    def changed_files(self) -> list[str]:
        if self.path is None:
            return []
        if self._mode != "git":
            return []
        r = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=self.path, capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            return []
        out: list[str] = []
        for line in r.stdout.splitlines():
            if not line.strip():
                continue
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            out.append(path.strip('"'))
        return out

    def diff(self, max_chars: int = 60_000, exclude: Sequence[str] = ()) -> str:
        """The unit's work as a diff.

        `exclude` drops paths the harness created for itself. Without it the
        reflection call reads the harness's own chat log as though it were the
        change under review -- and is then asked to judge what was built from it.
        """
        if self.path is None or self._mode != "git":
            return ""
        subprocess.run(["git", "add", "-A"], cwd=self.path, capture_output=True, timeout=120)
        if exclude:
            subprocess.run(["git", "reset", "-q", "--", *exclude],
                           cwd=self.path, capture_output=True, timeout=120)
        r = subprocess.run(
            ["git", "diff", "--cached", "--unified=3"],
            cwd=self.path, capture_output=True, text=True, timeout=180,
        )
        text = r.stdout or ""
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... [diff truncated]\n"
        return text

    def exists(self, relative: str) -> bool:
        """Whether the path is on disk -- a changed path that is not was deleted."""
        if self.path is None:
            raise RuntimeError("worktree is not open")
        try:
            return safe_join(self.path, relative).exists()
        except PathEscape:
            return False

    def read(self, relative: str) -> str:
        if self.path is None:
            raise RuntimeError("worktree is not open")
        target = safe_join(self.path, relative)
        try:
            return target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""


# --------------------------------------------------------------------------
# has the tooling moved under the survey?
#
# Gates come from what a repository already runs, so they are only as current
# as the reading that produced them. A repo that adds a linter, a test runner,
# a language or a CI step gains a surface nothing is gating -- and the existing
# gates stay green throughout, because they were never asked about it. Nothing
# fails; coverage just quietly stops being complete.
#
# You cannot watch for the tool, because you cannot enumerate the tools. You can
# watch for the shape of tooling changing, and tools announce themselves: a
# config file, a manifest entry, a CI step, a script. All of that is a git diff
# and a glob list. No model is involved, and none should be: this decides
# whether to *ask* the surveyor, which is a question with a right answer.
# --------------------------------------------------------------------------

# Paths whose appearance or change means "how this project verifies itself may
# have moved". Deliberately broad on config and narrow on source: a thousand
# commits to `src/` say nothing about the gate list, and one new `.druffrc` does.
TOOLING_GLOBS = [
    # the authoritative statement, where a project has one
    ".github/workflows/*", ".github/workflows/**/*", ".gitlab-ci.yml",
    ".circleci/config.yml", "azure-pipelines.yml", "Jenkinsfile",
    # task runners
    "Makefile", "makefile", "justfile", "Justfile", "Taskfile.yml", "Taskfile.yaml",
    # manifests, where a new dev dependency shows up
    "package.json", "*/package.json", "pyproject.toml", "*/pyproject.toml",
    "requirements*.txt", "*/requirements*.txt", "setup.cfg", "*/setup.cfg",
    "Cargo.toml", "go.mod", "Gemfile", "composer.json", "pom.xml", "build.gradle*",
    # tool configuration: the strongest signal that a new tool arrived
    "*.toml", "*.ini", "*.cfg", ".*rc", ".*rc.json", ".*rc.yml", ".*rc.yaml",
    "*.config.js", "*.config.ts", "*.config.mjs", "tsconfig*.json",
    ".pre-commit-config.yaml", "Dockerfile", "*/Dockerfile",
    "docker-compose*.yml", "docker-compose*.yaml", "compose*.yml", "compose*.yaml",
]


def _matches_tooling(path: str) -> bool:
    # Fabrika's own environment is not the repository's tooling. Committing
    # `.fabrika/Dockerfile` -- which Fabrika itself proposes -- read as a new
    # tool the survey had never seen, and asked for a re-reading of a file whose
    # every byte is already on the project's record. A change to it is the
    # Environment tab's to report ("changed since the checks ran").
    if path == FABRIKA_DIR or path.startswith(FABRIKA_DIR + "/"):
        return False
    pure = PurePosixPath(path)
    return any(pure.match(pattern) for pattern in TOOLING_GLOBS)


def tooling_paths(root: str | Path) -> list[str]:
    """Every file in the repository that says something about how it is verified.

    Recorded at survey time so a later reading can tell not only what changed,
    but what was there and never looked at -- the digest is bounded, and a
    surveyor that was never shown `druff.toml` did not decline to gate it.
    """
    root = Path(root).expanduser().resolve()
    if not root.exists():
        return []
    out = []
    for path in iter_repo_files(root):
        rel = path.relative_to(root).as_posix()
        if _matches_tooling(rel):
            out.append(rel)
    return sorted(out)


def tooling_drift(
    repo: str | Path, since_sha: str, known_paths: Sequence[str] = (),
) -> dict[str, Any]:
    """What has changed about how this repository verifies itself.

    Returns a plain fact, not a judgement: which tooling files appeared, which
    changed, and how far the ref has moved. Whether that warrants asking the
    surveyor is the human's call, and asking is the only thing this ever leads
    to -- a detector that re-surveyed on its own would be a model quietly
    rewriting what the project measures.
    """
    root = Path(repo).expanduser().resolve()
    root = Path(repo).expanduser().resolve()
    out: dict[str, Any] = {
        "checked": False, "since": since_sha, "added": [], "changed": [],
        "unseen": [], "commits": 0, "reason": "", "stale": False,
    }
    # Independent of git, and true even for a survey that recorded no commit:
    # tooling that exists now and was never in front of the surveyor.
    if known_paths:
        known = set(known_paths)
        out["unseen"] = [p for p in tooling_paths(root) if p not in known]
    if not since_sha:
        out["reason"] = "the survey did not record which commit it read"
        out["stale"] = bool(out["unseen"])
        return out
    if not is_repo(root):
        out["reason"] = "not a git repository"
        out["stale"] = bool(out["unseen"])
        return out

    status = git.out(["diff", "--name-status", f"{since_sha}..HEAD"], root, timeout=30)
    if status is None:
        out["reason"] = f"cannot diff from {since_sha[:12]}: it is not in this repository"
        out["stale"] = bool(out["unseen"])
        return out

    out["checked"] = True
    for line in status.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        code, path = parts[0].strip(), parts[-1].strip()
        if not _matches_tooling(path):
            continue
        if code.startswith("A"):
            out["added"].append(path)
        elif code.startswith(("M", "R", "D")):
            # A file the survey never read is new information even when git
            # calls it a modification -- the surveyor's digest is bounded, so
            # "changed" and "was never shown to it" are both worth surfacing.
            out["changed"].append(path)

    count = git.out(["rev-list", "--count", f"{since_sha}..HEAD"], root, timeout=30)
    try:
        out["commits"] = int((count or "0").strip())
    except ValueError:
        out["commits"] = 0
    out["stale"] = bool(out["added"] or out["changed"] or out["unseen"])
    return out


def commit_paths(repo: str | Path, paths: Sequence[str], message: str) -> tuple[str, str]:
    """Commit exactly these paths, and nothing else that happens to be staged.

    Returns (sha, problem). A problem is a sentence for a human, never a
    traceback: the files are already on disk by the time this runs, so a failure
    here means "they are written but not committed", which is a different thing
    to say than "it did not work".

    The pathspec on `commit` is not decoration. A human's working tree is their
    own -- there may be staged work in it -- and a tool that writes three files
    and then commits whatever else was sitting in the index has taken a decision
    that was never offered to it.
    """
    root = Path(repo).expanduser().resolve()
    if not is_repo(root):
        return "", "this project is not a git repository, so nothing was committed."
    rel = [str(x) for x in paths if x]
    if not rel:
        return "", ""
    if git.out(["add", "--", *rel], root, timeout=30) is None:
        return "", f"`git add` failed for {', '.join(rel)}."
    if git.out(["commit", "-m", message, "--", *rel], root, timeout=30) is None:
        return "", (
            "`git commit` failed. The files are written and staged; commit them yourself, or "
            "check that this repository has a configured user.name and user.email."
        )
    return head_sha(root), ""


def read_paths(root: str | Path, paths: Sequence[str], budget: int = 60_000) -> str:
    """The contents of specific files, bounded.

    A re-survey needs the handful of files that moved, not a digest of the whole
    repository. What is dropped for budget is named, because a reading that
    silently saw less than it was asked to is the failure mode the scout's
    coverage record exists to prevent, one level down.
    """
    base = Path(root).expanduser().resolve()
    parts: list[str] = []
    used = 0
    dropped: list[str] = []
    for rel in paths:
        try:
            target = safe_join(base, rel)
        except PathEscape:
            continue
        if not target.is_file():
            dropped.append(f"{rel} (gone)")
            continue
        if used >= budget:
            dropped.append(rel)
            continue
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            dropped.append(f"{rel} ({exc})")
            continue
        room = max(0, budget - used)
        clipped = text[:room]
        used += len(clipped)
        parts.append(
            f"### {rel}\n\n```\n{clipped}"
            + ("\n... [truncated]" if len(clipped) < len(text) else "")
            + "\n```"
        )
    if dropped:
        parts.append("### not read\n\n" + "\n".join(f"- {d}" for d in dropped))
    return "\n\n".join(parts) or "(nothing to read)"
