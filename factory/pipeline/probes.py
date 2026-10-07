"""The breaker's probes, as evidence and as findings.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from ..schemas import BreakerReport, BreakerSuite, BreakerTest, Finding


def probe_evidence(path: str, test: BreakerTest | None, output_tail: str) -> str:
    """What a human gets to judge a probe's claim by.

    The output, and the probe itself. A breaker finding says the code fails a
    specific attack, so the two ways to check it are to read the attack or to
    run it. The file is deleted from the branch after it runs, on purpose, so a
    finding that carried only a path would point at something that does not
    exist: a reviewer has told a human to "reproduce each probe independently"
    against a directory that had never been committed.

    The source travels with the finding rather than only sitting on a screen,
    because the finding is what goes into the packet document, into the ledger
    and in front of the arbiter. Readers left to judge a hanging probe from
    `exit -9` alone have, every one of them, got it wrong in the same
    direction.
    """
    parts = [path]
    if test is not None and (test.contents or "").strip():
        body = test.contents
        if len(body) > _PROBE_SOURCE_CHARS:
            body = body[:_PROBE_SOURCE_CHARS] + "\n… truncated; the whole file is in the ledger."
        parts.append(f"--- the probe ---\n{body}")
    tail = (output_tail or "")[-1500:]
    if tail.strip():
        parts.append(f"--- what it printed ---\n{tail}")
    return "\n\n".join(parts)


#: A probe is a test file, not a repository. Three typical ones are 2151, 1719
#: and 1132 characters; the cap is for the one that inlines a fixture set, so a
#: single finding cannot crowd out the packet.
_PROBE_SOURCE_CHARS = 8000


def probe_budget(test: BreakerTest, cfg: Any) -> float:
    """How long this probe gets, from what this probe has cost.

    A probe that has finished a run before is measured against that run;
    `breaker_timeout_s` remains the ceiling and the answer for a probe that
    never has. The floor matters more than the multiple: the question is not
    how long the probe should take but how long before it is obviously stuck,
    and a probe with a quarter-second baseline still running after a minute is
    not slow.
    """
    ceiling = float(cfg.breaker_timeout_s)
    if test.baseline_s <= 0:
        return ceiling
    want = max(float(cfg.probe_budget_floor_s), test.baseline_s * float(cfg.probe_budget_multiple))
    return min(ceiling, want)


def probe_finding_id(path: str) -> str:
    """The ledger id for the finding a probe raises, from the probe itself.

    Not `breaker-{i}` over `report.failing`, which is a position in a list
    that changes: a suite where the first probe is fixed and the second is not
    would renumber the second from `breaker-2` to `breaker-1`, so an id in a
    human's notes, in a flag they filed or in an arbiter's reason would name a
    different defect. Only the ledger's de-duplication on title would hide
    that, which is a second mechanism quietly holding up the first.

    Derived from the path instead, because a probe's identity is the file.
    `test_concurrent_tag_limit.py` gives `breaker-concurrent-tag-limit`, which
    also reads as something rather than as a counter -- `broke-api-types` is
    the house style and `breaker-1` never was.
    """
    stem = PurePosixPath(path or "").name
    for cut in (".test.tsx", ".test.ts", ".test.jsx", ".test.js", ".spec.tsx", ".spec.ts"):
        if stem.endswith(cut):
            stem = stem[: -len(cut)]
            break
    else:
        stem = PurePosixPath(stem).stem
    stem = re.sub(r"^(test|spec)[_-]+|[_-]+(test|spec)$", "", stem, flags=re.I)
    slug = re.sub(r"[^0-9a-z]+", "-", stem.lower()).strip("-")
    return f"breaker-{slug}" if slug else "breaker-probe"


def breaker_findings(suite: BreakerSuite, report: BreakerReport) -> list[Finding]:
    """A finding per failing breaker test. Computed from the process, not asked
    for -- the model supplied the hypothesis before it knew the outcome, and the
    evidence is the runner's own output. There is nothing here to fabricate.

    A suite that failed without naming a probe is a finding too. `failing` is
    populated by matching probe paths in the runner's output; a suite that dies
    before any probe reports -- a collection error, a migration that raises on
    import -- names nothing, and an empty list here would make
    `breaker_failures` 0. That is not hypothetical: probes attacking a
    migration nothing else in the pipeline could execute have errored in all
    four rounds of a run, and without this the packet said the adversarial
    suite found no problems.
    """
    if report.unstarted:
        return [Finding(
            id="breaker-unstarted",
            title="No adversarial probe ran: the breaker's harness could not start",
            severity="major",
            category="verification",
            detail=(
                "The breaker is the one agent that reads the finished implementation and "
                "executes attacks against it. Its session died before the agent started, "
                "so nothing was attacked -- this is not a breaker that looked and found "
                "nothing. Read every claim of robustness in this packet as untested for "
                "that reason."),
            evidence=report.unstarted,
            recommendation=(
                "Fix the environment the session runs in -- usually a route whose harness "
                "is not installed in the unit's image -- and re-run the review."),
        )]
    if not report.ran:
        return []

    # A probe that was killed on the clock, before anything about the code.
    #
    # Left in `failing` beside a real assertion failure, the finding below
    # would wear the breaker's own hypothesis as its title -- stating, as a
    # claim about the code, something the run never established: a title
    # reading "Concurrent additions ... can persist 21 tags" off a probe that
    # had hung for 900 seconds and asserted nothing.
    #
    # `verification`, not `robustness`: this is a fact about the measurement,
    # the same family as a blind suite that would not load, and it is routed to
    # whoever owns the probe rather than to whoever owns the code.
    timed_out: list[Finding] = []
    for path in report.timed_out:
        test = {t.path: t for t in suite.tests}.get(path)
        timed_out.append(Finding(
            id=f"{probe_finding_id(path)}-timeout",
            title=f"An adversarial probe never finished, so it tested nothing — {path}",
            severity="major",
            category="verification",
            detail=(
                "This probe was killed on the clock rather than failing, so it reported on "
                "nothing: it is not evidence that the code is broken, and it is not evidence "
                "that the code is sound.\n\n"
                + (f"What it was written to prove, and did not: {test.hypothesis}\n\n"
                   if test and test.hypothesis else "")
                + "Read it as a fact about the probe. A test that stops terminating after a "
                "repair usually encodes a schedule the repair made unreachable -- holding two "
                "requests until both commit is unsatisfiable once they serialise -- and no "
                "implementation passes a probe like that. It is not run again."),
            evidence=probe_evidence(path, test, report.output_tail),
            files=[path],
            recommendation=(
                "Fix the probe or drop it. If the hypothesis is still worth testing, write one "
                "that asserts the invariant without pinning the schedule."),
        ))

    if not report.failing:
        if report.exit_code == 0 or timed_out:
            return timed_out
        return timed_out + [Finding(
            id="breaker-suite",
            title="The attack tests failed, but no single one of them could be blamed for it",
            severity="major",
            category="robustness",
            detail=(
                f"The suite exited {report.exit_code} with "
                f"{len(report.tests_applied)} probe(s) applied, and nothing in its output "
                "matched a probe path -- so no individual finding could be attributed and "
                "the failure count came out as zero.\n\n"
                "This is either a real defect the probes caught before they could report "
                "it, or probes that cannot run here. Both matter and they look identical "
                "from outside: read the output below and decide which one this is. "
                "Configuring a per-probe command for this project makes the distinction "
                "automatic next time."
            ),
            evidence=f"$ {report.command}\n[exit {report.exit_code}]\n"
                     f"{report.output_tail[-1500:]}",
            files=list(report.tests_applied),
            recommendation=(
                "Run the suite by hand against this branch. If the probes are wrong, say so "
                "and they cost nothing; if they are right, this is the only agent in the "
                "system that both read the implementation and executed against it."
            ),
        )]
    by_path = {t.path: t for t in suite.tests}
    findings: list[Finding] = list(timed_out)
    # Two probes can share a basename in different directories, and an id is
    # an identity rather than a counter, so a collision has to be broken here
    # where every path is in hand.
    taken: dict[str, int] = {}
    for path in report.failing:
        test = by_path.get(path)
        fid = probe_finding_id(path)
        taken[fid] = taken.get(fid, 0) + 1
        if taken[fid] > 1:
            fid = f"{fid}-{taken[fid]}"
        findings.append(Finding(
            id=fid,
            title=(test.hypothesis if test and test.hypothesis
                   else f"The code fails one of the attacks written against it — {path}"),
            severity=test.severity if test else "major",
            category="robustness",
            detail=(
                "An adversarial test written against the finished implementation fails. "
                + (f"The hypothesis it was written to prove: {test.hypothesis}" if test else "")
            ),
            evidence=probe_evidence(path, test, report.output_tail),
            files=list(test.targets) if test else [path],
            recommendation="Either fix the behaviour or rule that the spec never required it.",
        ))
    return findings


def probes_proven_passing(tests, report) -> set[str]:
    """Which probes this round can honestly claim passed.

    Not "the ones not named as failing". A suite that exited non-zero and named
    nothing knows which probes passed exactly as well as a suite that never
    ran: not at all. Reading its silence as success is what the next round is
    then compared against, so a repair that breaks every probe's import reads
    as breaking nothing and survives the no-regression check.

    Measured: a repair renamed the model class the probes import. Eleven probes
    stopped loading, the phase line said "11 probe(s), 0 failing", the round
    was kept, and the packet told a human the probe suite was clean.
    """
    if not report.ran:
        return set()
    if report.exit_code != 0 and not report.failing:
        return set()
    return {t.path for t in tests} - set(report.failing)


def breaker_detail(tests, report, suffix: str = "") -> str:
    """The phase line, which has to say when the count means nothing."""
    if not report.ran:
        return (report.note or "did not run") + suffix
    if report.exit_code != 0 and not report.failing:
        return (f"{len(tests)} probe(s), none of them proved anything: the suite "
                f"exited {report.exit_code} and named no probe" + suffix)
    return f"{len(tests)} probe(s), {len(report.failing)} failing" + suffix
