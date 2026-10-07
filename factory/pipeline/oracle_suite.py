"""The blind suite and the breaker's probes, assembled from what is on disk
and placed where this project's runners will find them.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Sequence

from ..executors import Authored
from ..schemas import (
    ArbiterReport,
    BlindPlacement,
    BreakerAccount,
    BreakerSuite,
    BreakerTest,
    IntegrationReport,
    OracleAccount,
    OracleSuite,
    Spec,
    SupportFile,
    TestFile,
)
from ..workspace import PathEscape, safe_join


_PATH_IN_PROSE = re.compile(r"[\w./-]+\.[A-Za-z][A-Za-z0-9]{0,4}\b")


def _disclaimed_paths(integration: IntegrationReport | None) -> list[str]:
    """Paths the integrator said, in its own words, it could not return whole.

    It has no field for "insert this block here": `FileWrite.contents` means the
    complete file. So when it cannot produce one it says so in `unresolved` and
    returns a fragment anyway, having complied with the only schema on offer.
    Reading that sentence is cheap and it is the agent's own testimony.
    """
    if integration is None:
        return []
    out: list[str] = []
    written = {f.path for f in integration.files}
    for line in integration.unresolved:
        lowered = line.lower()
        if "cannot return a complete file" not in lowered and "not a complete file" not in lowered:
            continue
        for candidate in _PATH_IN_PROSE.findall(line):
            if candidate in written:
                out.append(candidate)
    return out


def _title_key(title: str) -> str:
    return re.sub(r"\W+", " ", (title or "").strip().lower()).strip()


#: An acceptance criterion id, however a model spells it.
_CRITERION_ID = re.compile(r"\bAC[-_ ]?(\d{1,3})\b", re.IGNORECASE)


def assemble_oracle_suite(
    session: Authored, account: OracleAccount | None, note: str = "",
) -> OracleSuite:
    """Join what is on disk to what the oracle said about it.

    The files are authoritative and complete: every one the harness wrote is in
    the suite, whether or not the account mentions it. A file the account forgot
    is still a file that will run, and dropping it here would mean running a
    suite the packet does not describe.

    The account contributes only what disk cannot: which files assert, the
    criterion ids of the ones that do, the framework, the command. A file it did
    not tag arrives untagged, which `check_blind_tags` reports as the run's own
    finding rather than hiding.

    The one judgement made here is the split. A file the account named in
    `support` asserts nothing, so it goes to `support` whatever else the account
    said about it -- including a stray tag on the same path in `tests`. That
    precedence is deliberate and it only ever loses evidence: the suite's
    criteria read `untested`, which is visible. Reading it the other way is how
    a README came to be evidence for eighteen criteria.
    """
    meta = {}
    for entry in (account.tests if account else []):
        rel = entry.path.strip().lstrip("./")
        meta[rel] = entry
        # A model naming a file by its basename, or with the confinement
        # directory left off, means that file: matching on the tail is what
        # keeps a whole suite from arriving untagged over a spelling.
        meta.setdefault(PurePosixPath(rel).name, entry)

    # Same tolerance for the support list, and for the same reason: a support
    # file that misses its match is not a spelling mistake with no consequence,
    # it is a conftest back in the matrix tagged to everything.
    declared_support: set[str] = set()
    for raw in (account.support if account else []):
        rel = str(raw).strip().lstrip("./")
        if not rel:
            continue
        declared_support.add(rel)
        declared_support.add(PurePosixPath(rel).name)

    def is_support(path: str) -> bool:
        return path in declared_support or PurePosixPath(path).name in declared_support

    tests: list[TestFile] = []
    support: list[SupportFile] = []
    for written in session.files:
        if is_support(written.path):
            support.append(SupportFile(path=written.path, contents=written.contents))
            continue
        entry = meta.get(written.path) or meta.get(PurePosixPath(written.path).name)
        tests.append(TestFile(
            path=written.path,
            contents=written.contents,
            criterion_ids=list(entry.criterion_ids) if entry else [],
            cases=list(entry.cases) if entry else [],
            framework=(entry.framework if entry else "") or (
                account.tests[0].framework if account and account.tests else ""),
        ))

    notes = [account.notes] if account and account.notes else []
    if note:
        notes.append(note)
    if session.outside:
        notes.append(
            "Files written outside the suite directory were discarded without being "
            f"read: {', '.join(session.outside[:10])}")
    return OracleSuite(
        strategy=(account.strategy if account else ""),
        command=(account.command if account else ""),
        tests=tests,
        support=support,
        untestable_criteria=list(account.untestable_criteria) if account else [],
        # Carried across verbatim. This is the one field on the account that
        # describes the harness rather than the files, so nothing on disk can
        # corroborate or contradict it -- and a requirement dropped here is a
        # run that fails for a reason nobody is told.
        requires=list(account.requires) if account else [],
        notes="\n\n".join(n for n in notes if n),
    )


def assemble_breaker_suite(
    session: Authored, account: BreakerAccount | None, note: str = "",
) -> BreakerSuite:
    """The breaker's half of `assemble_oracle_suite`, and the same rule.

    A probe with no hypothesis still runs. It says so in place of the sentence a
    human would otherwise read as the finding, because an unexplained red test
    is worth less than an explained one but a great deal more than a test that
    was silently dropped for lacking a caption.
    """
    meta = {}
    for entry in (account.tests if account else []):
        rel = entry.path.strip().lstrip("./")
        meta[rel] = entry
        meta.setdefault(PurePosixPath(rel).name, entry)

    tests: list[BreakerTest] = []
    for written in session.files:
        entry = meta.get(written.path) or meta.get(PurePosixPath(written.path).name)
        # A `regression` claim with no pass condition behind it is not a
        # classification, it is a preference. The whole point of asking for the
        # condition is that stating one is the work; a probe whose account
        # skipped it has not done that work, and the safe reading of silence is
        # the one that costs nothing to be wrong about.
        kind = (entry.kind if entry else "demonstration")
        passes_when = ((entry.passes_when if entry else "") or "").strip()
        if kind == "regression" and not passes_when:
            kind = "demonstration"
        tests.append(BreakerTest(
            path=written.path,
            contents=written.contents,
            hypothesis=(entry.hypothesis if entry else "") or (
                f"{written.path} failed, and the breaker gave no hypothesis for it. "
                "Read the test."),
            severity=(entry.severity if entry else "major"),
            targets=list(entry.targets) if entry else [],
            kind=kind,
            passes_when=passes_when,
        ))

    notes = [account.notes] if account and account.notes else []
    if note:
        notes.append(note)
    if session.outside:
        # This one matters more than the oracle's. The breaker works in a
        # checkout of the implementation, so a file it wrote outside its own
        # directory is an edit to the code under attack -- and a probe that only
        # fails because the breaker also changed the source is a finding about a
        # repository nobody has. The edits die with the worktree; the fact that
        # it made them is evidence about the probes that survived.
        notes.append(
            "The breaker wrote outside its own directory, and those writes were "
            "discarded unread. Weigh its probes accordingly -- a probe written "
            "alongside an edit to the code under attack may depend on that edit: "
            f"{', '.join(session.outside[:10])}")
    return BreakerSuite(
        strategy=(account.strategy if account else ""),
        command=(account.command if account else ""),
        tests=tests,
        conceded=list(account.conceded) if account else [],
        notes="\n\n".join(n for n in notes if n),
    )


def _suite_detail(suite: OracleSuite) -> str:
    """How a blind suite is announced in the phase line and on resume.

    Two numbers rather than one, because they answer different questions, and
    folded into one they mislead: "6 blind test file(s)" is true of a suite with
    three tests and three fixtures in exactly the way that matters least.

    An unmet requirement is said here as well as in the ledger, because the
    ledger is read at the end and this line is read while the run is still going.
    The failure it stands for cost 2h43m before anyone saw it, and every minute
    after the oracle finished was spent building against a verification layer
    that was already known not to work.
    """
    detail = f"{len(suite.tests)} blind test file(s)"
    if suite.support:
        detail += f", {len(suite.support)} support file(s)"
    if suite.requires:
        detail += (f" -- {len(suite.requires)} unmet environment requirement(s): "
                   + "; ".join(r.need for r in suite.requires if r.need)[:200])
    return detail


def oracle_files(suite: OracleSuite | None) -> list[TestFile | SupportFile]:
    """Every file the oracle wrote, asserting or not.

    The split between `tests` and `support` exists so that scaffolding cannot be
    evidence for a criterion. It must not leak anywhere else, and there are five
    places where reading `suite.tests` alone would be a bug rather than a
    nuance: the write to disk (a suite whose conftest never lands runs nothing),
    the confinement and relocation pass, `protected_paths`, and the two witness
    lists. A conftest left off the verification surface is a conftest the repair
    loop may rewrite, which is INV-12 with a hole in it -- and the hole would be
    invisible, because the suite still passes afterwards.

    So the sites that mean "the whole suite" say so through this function, and
    the ones that mean "what asserts" keep reading `.tests`.
    """
    if suite is None:
        return []
    return [*suite.tests, *suite.support]


#: What counts as a picture. Narrow on purpose: this directory is named to
#: an agent, and the answer to "what may I put here" should be short.


def place_blind_file(
    placements: Sequence[BlindPlacement], path: str,
    feature_id: str = "", nestable: Sequence[str] = (),
) -> str:
    """Which of this project's proved directories a blind file belongs in.

    Not one place for every blind file, as a single `oracle_dir` string would
    give. That is wrong for any repository with more than one runner, and wrong
    in the way that costs a run: a React component test written by the oracle
    lands in a directory whose per-file command is pytest, and the criteria it
    covers come back failed for a reason no code caused.

    Routed on the file's own extension against the extension of the canary each
    placement was proved with. That is not framework knowledge -- nothing here
    knows what `.tsx` is or which tool reads it. It knows that a placement was
    measured with a file ending in `.tsx` and that this file ends in `.tsx` too,
    so what was proved about one is true of the other.

    Returns "" when no proved placement claims the extension. The caller must
    not guess: putting a file where its command cannot run it is how a usage
    error comes to be reported as a failing criterion, which is the whole thing
    this routing exists to stop.
    """
    rel = (path or "").strip().lstrip("./")
    if not rel:
        return ""
    suffix = PurePosixPath(rel).suffix
    for placement in placements:
        own = (placement.directory or "").strip("/")
        if not own or PurePosixPath(placement.filename or "").suffix != suffix:
            continue
        if rel == own or rel.startswith(own + "/"):
            return rel
        # The directories above the basename are dropped -- a suite that runs on
        # its own does not need the project's layout -- and one is put back: the
        # feature's own.
        #
        # These files accumulate. An accepted feature's acceptance tests stay in
        # the repository and run as regressions against everything built after
        # it, so two features that both write `test_screening.py` would land
        # on each other and the older suite would simply disappear. A folder
        # per feature prevents that and says where each test came from.
        #
        # Only where a nested file was actually measured as collectable, which
        # is `subdirs_ok` on the placement's probe. Under some pytest layouts
        # two same-named files in sibling directories are an import-mismatch
        # error, and a flat directory with colliding names is still better than
        # a nested one nothing runs.
        leaf = PurePosixPath(rel).name
        if feature_id and own in set(nestable):
            return f"{own}/{feature_test_dir(feature_id)}/{leaf}"
        return f"{own}/{leaf}"

    # No placement claims this extension -- but the file may already be sitting
    # in a proved directory, and then it is where it belongs.
    #
    # The extension gate above exists to *route* a file that needs a home. Asked
    # of a file that already has one it answers the wrong question, and the
    # answer costs a suite: the oracle wrote `screeningFixtures.js` into the
    # frontend blind directory beside the tests that import it, `.js` matched
    # neither canary (`.py`, `.tsx`), and the support fallback parked it in the
    # first placement -- a JavaScript module in the pytest directory. Both
    # frontend blind tests then failed to transform on an import of a file that
    # had been moved out from under them, and four criteria were reported as
    # failing on that evidence.
    #
    # A test still has to be routed by extension: a `.tsx` under the pytest
    # directory is collected by nothing and must move, which the loop above
    # already does. This is only for the case the loop has no opinion about.
    for placement in placements:
        own = (placement.directory or "").strip("/")
        if own and (rel == own or rel.startswith(own + "/")):
            return rel
    return ""


def _commit_all(root: Path, message: str) -> None:
    """Commit everything in a scratch checkout, so later changes stand alone."""
    for args in (["add", "-A"],
                 ["-c", "user.name=factory", "-c", "user.email=factory@localhost",
                  "commit", "-q", "--no-verify", "-m", message]):
        subprocess.run(["git", *args], cwd=str(root), check=True,
                       capture_output=True, text=True, timeout=120)


@contextmanager
def set_aside(root: Path, paths: Sequence[str]) -> Iterator[list[str]]:
    """Move these files out of the checkout for the length of a block.

    How a failing check is sorted without reading what it printed: run it
    again without a set of files, and see whether it still fails. Moved rather
    than deleted, to a directory outside the checkout so nothing collects them
    there, and always put back.
    """
    holding = Path(tempfile.mkdtemp(prefix="factory-aside-"))
    moved: list[str] = []
    try:
        for rel in paths:
            try:
                source = safe_join(root, rel)
            except PathEscape:
                continue
            if not source.is_file():
                continue
            target = holding / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            moved.append(rel)
        yield moved
    finally:
        for rel in moved:
            destination = Path(root) / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(holding / rel), str(destination))
        shutil.rmtree(holding, ignore_errors=True)


def runner_naming(placements: Sequence[BlindPlacement], probes: Sequence[Any]) -> str:
    """What each placement's runner calls a test, as measured -- for the oracle.

    The oracle declares a name for every test it writes, and that name is the
    only thing a runner's report can be joined on. Told only to use "the name
    your framework will print", it cannot know it: it has never run a test
    here. One project's API runner reports a test by its plain name and ignores
    a display title; an oracle that declared the titles joined nothing, and two
    failing tests reached the packet as `unknown`. The placement check has
    already measured the answer on a canary, and this passes it on.
    """
    measured = {getattr(r, "directory", ""): r for r in probes}
    rows: list[str] = []
    for placement in placements:
        probe = measured.get(placement.directory)
        declared = (placement.canary_case or "").strip()
        raw = (getattr(probe, "reported_case", "") or "").strip() if probe else ""
        # One entry per distinct name. The measurement records every name the
        # report carried, joined, and a report that lists the canary twice reads
        # `a > b, a > b` -- which, shown as "the runner called it", the oracle
        # copied as a pattern and declared every test as `name, name`. Nothing
        # joined, and eight passing tests reached the packet as unknown.
        listed = [n.strip() for n in raw.split(", ") if n.strip()]
        names = list(dict.fromkeys(listed))
        if not declared or not names:
            continue
        called = (f"called it `{names[0]}`" if len(names) == 1 else
                  "called it by these names: " + ", ".join(f"`{n}`" for n in names))
        repeated = (" The report listed that one test more than once; it is one test "
                    "with one name, and yours are each named once."
                    if len(listed) > len(names) else "")
        rows.append(
            f"### `{placement.directory}/`\n\n"
            "The runner was given this test file:\n\n"
            f"```\n{(placement.canary_passes or '').strip()[:1500]}\n```\n\n"
            f"Its one test was declared as `{declared}`, and the runner's own report "
            f"{called}.{repeated}")
    if not rows:
        return ""
    return (
        "\n\n---\n\n# What this project's runners call a test\n\n"
        "Each test you account for is joined to the runner's report by its name, and "
        "by nothing else. This was measured before the run, on a test written the way "
        "this project writes them:\n\n" + "\n\n".join(rows) + "\n\n"
        "Name each of your tests the way that one is named. Where the way you write a "
        "test lets you give it a second, human-readable title, a runner may report it "
        "under its plain name instead -- the measurement above shows which this one "
        "reports. A name the report does not carry matches nothing, and every criterion "
        "that test checks then comes back unknown, however the test ran.")


def blind_write_targets(
    placements: Sequence[BlindPlacement], feature_id: str = "",
    nestable: Sequence[str] = (),
) -> list[tuple[str, str]]:
    """(file extension, directory) for every proved placement, this feature's.

    The same arithmetic `blind_destination` does, done once and up front so the
    oracle can be told where its files are going *before* it writes them.

    This exists because the alternative silently corrupts a suite. An oracle
    was told to write under `tests/oracle/` and did; it wrote
    `../../frontend/src/pages/TrialDetail`, which from `tests/oracle/` is the
    repository root and is exactly right. The file was then relocated four
    levels deeper, into the directory it actually runs in, and that import --
    correct when written -- resolved to nothing. It collected no tests, exited
    non-zero, and six criteria reached a human as failing while a review agent
    filed the innocent components as a blocker.

    No prompt fixes that, because the prompt was not wrong. An author cannot
    write a relative path from a directory it has been told the wrong name for.
    """
    targets: list[tuple[str, str]] = []
    nest = set(nestable)
    for placement in placements:
        own = (placement.directory or "").strip("/")
        suffix = PurePosixPath(placement.filename or "").suffix
        if not own or not suffix:
            continue
        where = f"{own}/{feature_test_dir(feature_id)}" if feature_id and own in nest else own
        if (suffix, where) not in targets:
            targets.append((suffix, where))
    return targets


def feature_test_dir(feature_id: str) -> str:
    """The name of a feature's own folder of independent tests.

    The feature id as a name every test runner can import from. A feature id
    carries hyphens -- `saved-searches-9840f6` -- and a folder named like that
    cannot be a package in the most common runner there is, so a test inside it
    can never import a helper beside it by package path. An oracle hit exactly
    that and did the sensible thing: it put its helpers in a sibling package
    with a legal name. The folder it was allowed to write to was the hyphenated
    one, so the helpers were discarded as written outside it, four of five test
    files could not load, and eight of nine criteria came back unverified --
    with the implementation passing every check the project owns.

    Letters, digits and underscores, not starting with a digit. The id stays
    recognisable -- `saved_searches_9840f6` -- and the folder says where each
    test came from, which is the reason it exists.
    """
    safe = re.sub(r"[^0-9A-Za-z_]", "_", feature_id or "")
    return f"f_{safe}" if safe[:1].isdigit() else safe


def unruled(report: "ArbiterReport | None", open_ids: Sequence[str]) -> list[str]:
    """The findings the arbiter was handed and did not rule on, in the order
    they were handed over.

    Not "did it route them well" -- nothing here can judge that. Only whether
    each one came back with a disposition against its id, which is the
    difference between a triage and a reply that happened to validate.

    The schema cannot answer this, for the same reason it could not answer it
    for the oracle: `dispositions: list[FindingDisposition]` is satisfied by an
    empty list. And an empty list is not visibly wrong downstream, because
    `restore_dispositions` defaults anything unrouted to a human ruling -- which
    is the correct default and makes a total non-answer look exactly like a
    deliberate decision to escalate everything.

    A route that runs a coding harness makes this easy to hit. Asked for a
    structured answer, such a harness can treat the first turn as
    reconnaissance: one returned `{"summary": "Placeholder while I inspect the
    repo.", "dispositions": []}`, 72 characters where every other round wrote
    seven to seventeen thousand. The packet reported 27 escalations, the repair
    loop had nothing routed to it and never ran, and the run came back with 8
    of 25 criteria verified and no sign that the reason was an agent that did
    not reply.

    All of them, not one of them: a run that rules on three findings and drops
    the other twenty-four has the same hole, only smaller and harder to see.
    The ids this returns are read back to the arbiter on the retry, because
    "you answered about none of them" is not actionable and "you did not rule
    on F-3, F-9, F-17" is.
    """
    if report is None:
        return list(open_ids)
    ruled = {d.finding_id for d in (report.dispositions or ())}
    return [fid for fid in open_ids if fid not in ruled]


def usable_suite(suite: OracleSuite, spec: Spec) -> bool:
    """Whether this suite could verify anything at all.

    Not "is it good" -- nothing here can judge that. Only whether at least one
    test names at least one criterion the spec actually contains, which is the
    difference between a suite that might verify something and one that
    provably cannot.

    The schema cannot answer this. `criterion_ids: list[str]` is satisfied by a
    list containing a comma, and an oracle returned exactly that on every file
    it wrote: twelve criteria came back unverified with nothing anywhere saying
    the tags were the reason.
    """
    return any(normalize_criterion_ids(t.criterion_ids, spec) for t in suite.tests)


def normalize_criterion_ids(raw: Sequence[str], spec: Spec) -> list[str]:
    """Criterion ids a model wrote, reduced to ones this spec actually has.

    `criterion_ids: list[str]` is satisfied by any list of any strings, so the
    schema cannot tell a tag from a typo. A real oracle returned
    `criterion_ids: [", "]` on every one of its test files -- a valid list of
    valid strings, and a comma. Nothing rejected it, `compute_trace` matched
    none of them, and all twelve criteria were reported `no_test`: the blind
    suite existed, ran, and proved nothing about the contract, which is the one
    outcome the whole verify lane is built to prevent.

    So the ids are extracted rather than trusted. A model that writes
    "AC-1, AC-2" in one string, or "ac 3", or "AC-07" against a spec with
    "AC-7", means the criterion, and each of those finds it. Anything that
    names no criterion this spec contains is dropped -- a tag pointing at
    nothing is not a tag, and keeping it would put a test in the matrix under an
    id the human cannot look up.
    """
    known = {c.id.upper(): c.id for c in spec.acceptance_criteria}
    out: dict[str, None] = {}
    for entry in raw:
        for number in _CRITERION_ID.findall(str(entry or "")):
            for candidate in (f"AC-{number}", f"AC-{int(number)}"):
                actual = known.get(candidate.upper())
                if actual:
                    out.setdefault(actual, None)
                    break
    return list(out)


def retarget_command(command: str, moved: Sequence[tuple[str, str]]) -> str:
    """Point a declared command at where its files actually ended up.

    `confine_to` relocates the oracle's tests into the directory its suite owns.
    The oracle also declares the command that runs them, and that command names
    the directory it *chose* -- so relocating the files without rewriting the
    command leaves the suite pointing at a path that no longer exists.

    That has happened: an oracle wrote `tests_acceptance/`, the
    files were moved to `tests/oracle/`, and `python -m pytest tests_acceptance/`
    exited 4 having collected nothing. Every criterion in the packet came back
    unverified, and the cause was two lines of this module disagreeing about
    where a directory was.

    Rewrites the directory segment of each moved path, longest first so that a
    nested directory is not half-replaced by its own parent.
    """
    if not command:
        return command
    swaps: dict[str, str] = {}
    for old, new in moved:
        old_dir = PurePosixPath(old).parent.as_posix()
        new_dir = PurePosixPath(new).parent.as_posix()
        if old_dir in (".", "") or old_dir == new_dir:
            continue
        swaps[old_dir] = new_dir
        swaps[old] = new
    for src in sorted(swaps, key=len, reverse=True):
        command = command.replace(src, swaps[src])
    return command
