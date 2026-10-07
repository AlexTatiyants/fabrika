"""Checks on the agents and the environments they ran in: sessions that did
not come back, plans nearly out, routes that fell back, units that
delivered nothing.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from ...config import INDEPENDENT_OF, Config, independence_problems, model_family
from ...schemas import FileWrite, Finding, IntegrationReport, Plan, WorkerOutput
from ...store import EvidenceStore


def check_setup(store: EvidenceStore, spec_hash: str) -> list[Finding]:
    """Dependency reconciliation that did not reconcile.

    Setup is not a gate and does not belong in the gate report, but a gate that
    fails because its toolchain was never installed looks exactly like a gate
    that fails because the code is wrong. This is the difference, stated once.
    """
    latest = None
    for record in store.records():
        if record.get("kind") == "setup" and record.get("spec_hash") == spec_hash:
            latest = record.get("payload") or {}
    if not latest or not latest.get("failed"):
        return []
    failed = latest["failed"]
    results = {r["name"]: r for r in latest.get("results") or []}
    return [Finding(
        id="setup-1",
        title="The project's environment could not be fully set up, so the checks ran without it",
        severity="blocker",
        category="verification",
        detail=(
            "Setup installs what the gates need into the checkout they run against. When it "
            "fails, every gate downstream of it is reporting on an environment that was never "
            "assembled, and their results describe this machine rather than this code."
        ),
        evidence="\n\n".join(
            f"$ {results.get(name, {}).get('command', name)}\n"
            f"{(results.get(name, {}).get('output_tail') or '')[-800:]}"
            for name in failed
        ),
        recommendation="Fix the environment before reading any gate result below.",
    )]


def check_unit_environments(store: EvidenceStore, spec_hash: str) -> list[Finding]:
    """Authoring sessions that ran with no stack under them.

    `unitenv` gives every authoring unit the project's own environment -- its
    database migrated and seeded, its services up, its commands on the agent's
    PATH -- so that a unit can run what it writes. When that fails the unit is
    told, in its brief, and writes the code anyway. That part is deliberate: a
    unit whose deliverable is a CI workflow could not execute it here either,
    and a build that says "I wrote this and could not run it" is worth having.

    What must not happen is that the fact goes no further than the brief. If it
    stops there, a run where the build lane and every repair round worked blind
    produces a packet identical to one where they all had a database, and the
    only way anybody knows is a worker happening to be honest about it in
    prose. Meanwhile the verify lane, on the same failure,
    refuses to run and says it knows nothing (`check_setup`, and the gates
    themselves). Two lanes, one failure, opposite answers.

    A blocker, because it is the premise under every other claim in the packet:
    a green gate board describes code that the agents who wrote it could not
    execute.
    """
    # The last word on each unit in each round, not every word. A retried run
    # keeps its ledger and its spec, so the attempts before a fix are still in
    # it under the same hash: counting every word, a run whose every agent had
    # its stack would report all three as blind, because two attempts earlier
    # none of them had.
    latest: dict[tuple[str, int], dict] = {}
    for record in store.records():
        if record.get("kind") != "unit_env" or record.get("spec_hash") != spec_hash:
            continue
        payload = record.get("payload") or {}
        latest[(str(payload.get("unit") or "?"), int(payload.get("round") or 0))] = payload
    blind = [payload for payload in latest.values() if not payload.get("ready")]
    if not blind:
        return []
    labels = ", ".join(dict.fromkeys(str(b.get("unit") or "?") for b in blind))
    return [Finding(
        id="unitenv-1",
        title=f"Some of this code was written by agents that could not run it — {labels}",
        severity="blocker",
        category="verification",
        detail=(
            "These agents were given a checkout and no running system: no migrated "
            "database, no services, and none of the project's own commands. Every claim "
            "they make about behaviour is inference, including any test they wrote and "
            "any migration they added. The gates that passed afterwards ran in a "
            "different, working environment, so a green board here is not evidence that "
            "these units were ever executed -- only that something else could be.\n\n"
            "Read this before the gate results, not after them."
        ),
        evidence="\n\n".join(
            f"## {b.get('unit') or '?'} ({b.get('role') or 'unknown role'})\n"
            f"{b.get('problem') or 'no reason was recorded'}\n\n"
            f"{(b.get('log') or '')[-800:]}"
            for b in blind
        ),
        recommendation=(
            "Bring the environment up once at the start of the feature, where a failure "
            "is cheap and visible, and fix it there rather than discovering it in the "
            "packet. If it cannot be made to come up, say so in the spec: these criteria "
            "are then verified by hand, not by this pipeline."
        ),
    )]


#: Every record the pipeline writes when an agent does not come back, and what
#: its absence costs the packet. The severity is about what stopped being
#: checked, not about how the process died: an arbiter that failed leaves
#: findings unrouted, a scout that failed leaves the whole build reading a
#: repository nobody surveyed.
#:
#: Each of these is appended where the agent fails and read here. Left unread,
#: none of them would reach a finding -- so a run where every review agent
#: failed would produce the same packet as a run where every review agent found
#: nothing. `verify_lane_failed` is deliberately absent: it already has its own
#: handling upstream.
AGENT_FAILURES: dict[str, tuple[str, str]] = {
    "scout_failed": ("blocker", "the repository was never surveyed, so every agent "
                                "downstream planned against a digest that is missing"),
    "arbiter_failed": ("blocker", "findings were never routed, so nothing was dispositioned "
                                  "and no repair could be planned from them"),
    "rework_failed": ("blocker", "the repair loop stopped early and the findings it was "
                                 "holding were never attempted"),
    "review_failed": ("major", "a review agent returned nothing, so the panel this packet "
                               "reports is smaller than the one configured"),
    "recheck_failed": ("major", "a re-check never ran, so whether that round's repairs "
                                "landed is unverified rather than confirmed"),
    "simplify_failed": ("minor", "the simplification pass never ran, so the findings it "
                                 "would have closed are still open and still true -- "
                                 "about readability, not about behaviour"),
    "repair_failed": ("major", "a repair unit raised, so the findings it owned were not "
                               "attempted in that round"),
    "breaker_failed": ("major", "the adversarial probes were never written or never ran "
                                "for this round"),
    "attribution_failed": ("minor", "a failing check could not be compared against the "
                                    "baseline, so it is not known whether this feature "
                                    "caused it"),
}


def check_agent_failures(
    store: EvidenceStore, spec_hash: str, expected_reviewers: int = 0,
) -> list[Finding]:
    """Agents that did not come back, as findings rather than as ledger entries.

    The pipeline is careful about this on the way in -- every one of these
    failures is caught, recorded with its error, and the run continues rather
    than dying, which is right. This is the way out. Unless something reads
    those records, the packet's numbers are computed as though the agent had
    run and found nothing, and the two cases are indistinguishable to a reader.

    Measured: all three review agents failed on a final pass -- one out of
    credits, two returning a preamble and an empty finding list -- and the
    packet reported `0 still standing` with no mention of it.
    """
    by_kind: dict[str, list[dict]] = {}
    for record in store.records():
        kind = record.get("kind")
        if kind not in AGENT_FAILURES or record.get("spec_hash") != spec_hash:
            continue
        by_kind.setdefault(kind, []).append(record)

    findings: list[Finding] = []
    for kind, records in sorted(by_kind.items()):
        severity, cost = AGENT_FAILURES[kind]
        roles = [str((r.get("payload") or {}).get("role") or r.get("role") or "?")
                 for r in records]
        # A panel is not the sum of its agents: losing one leaves a reading, and
        # losing all of them leaves the packet with no adversarial reading at
        # all, while every count downstream still says zero.
        if (kind == "review_failed" and expected_reviewers
                and len(set(roles)) >= expected_reviewers):
            severity = "blocker"
            cost = ("every configured review agent failed, so this packet has no "
                    "adversarial reading of the finished code -- and 'no findings' "
                    "here means 'nobody looked', not 'nothing was found'")
        findings.append(Finding(
            id=f"agent-{kind.replace('_', '-')}",
            title=(f"Some agents did not produce usable work ({kind.replace('_', ' ')}) — "
               f"{', '.join(dict.fromkeys(roles))}"),
            severity=severity,
            category="verification",
            detail=(
                f"What this costs: {cost}.\n\n"
                "The run continued deliberately -- a dead agent should not take a build "
                "down. But every count computed after this point treats the agent as "
                "having run and reported nothing."
            ),
            evidence="\n\n".join(
                f"## {(r.get('payload') or {}).get('role') or r.get('role') or '?'} "
                f"(seq {r.get('seq')})\n"
                f"{str((r.get('payload') or {}).get('error') or r.get('payload'))[:800]}"
                for r in records
            ),
            recommendation=(
                "Read the error. If it is a quota, a credential or a ceiling, this run's "
                "verification is thinner than its numbers say and the fix is to rerun it "
                "with that resolved -- not to accept the packet as it stands."
            ),
        ))
    return findings


#: Above this, a plan has too little left for a run to be confident of
#: finishing on it. Not a cliff -- the windows are shared with the human at the
#: same terminal, so the figure moves while the build runs -- which is why this
#: is a finding a person weighs and not a refusal.
HEADROOM_TIGHT = 0.9


def check_headroom(store: EvidenceStore, spec_hash: str) -> list[Finding]:
    """Plans that were already nearly out when this run started.

    Read before the build and reported after it, because the two facts a
    reviewer needs are on opposite ends: what the plan had, and what the run did
    with it. A build that started at 96% of a five-hour window and died in the
    review panel did not fail mysteriously.

    Also reports a plan nobody could read. A route whose gauge is not current is
    one this run was scheduled against without knowing the headroom at all --
    which is a different statement from "it had room", and without this a
    reader cannot tell the two apart.
    """
    latest = None
    for record in store.records():
        if record.get("kind") != "headroom" or record.get("spec_hash") != spec_hash:
            continue
        payload = record.get("payload") or {}
        # The reading taken before the work, which is the one that says what the
        # run was committed against. The closing reading answers a different
        # question and is read by `observed_draw`.
        if payload.get("when", "before") == "before":
            latest = payload
    if not latest:
        return []

    tight: list[str] = []
    unknown: list[str] = []
    for name, gauge in sorted((latest.get("routes") or {}).items()):
        if not gauge.get("windowed"):
            continue          # no rolling window bounds it; the guard does
        live = [w for w in (gauge.get("windows") or []) if not w.get("stale")]
        if not gauge.get("current") or not live:
            unknown.append(name)
            continue
        worst = max(live, key=lambda w: float(w.get("utilization") or 0.0))
        if float(worst.get("utilization") or 0.0) >= HEADROOM_TIGHT:
            tight.append(
                f"{name}: {round(float(worst['utilization']) * 100)}% of its "
                f"{worst.get('label') or worst.get('name')} window already used")
    if not tight and not unknown:
        return []

    findings: list[Finding] = []
    if tight:
        findings.append(Finding(
            id="headroom-1",
            title="A subscription was nearly used up when this run started, so it may have stopped early",
            severity="major",
            category="budget",
            detail=(
                "A rolling window is shared with whoever else is using the same plan, so "
                "this figure moved while the build ran. What it says is that the run was "
                "committed against a plan that had little left -- and a run that reaches a "
                "limit does not stop at a sensible place, it stops wherever it happens to "
                "be, with the agents after that point never running at all."
            ),
            evidence="\n".join(tight),
            recommendation=(
                "Read any missing agent below against this. If the panel, the breaker or a "
                "repair round is absent or thin, the plan is the first thing to rule out."
            ),
        ))
    if unknown:
        findings.append(Finding(
            id="headroom-2",
            title="The run could not tell how much of a subscription was left when it started",
            severity="minor",
            category="budget",
            detail=(
                "These routes report their windows only inside a call, and none had been "
                "made recently enough for the reading to describe the window that was "
                "running. The build went ahead against an unknown, which is not the same "
                "as going ahead against room."
            ),
            evidence="\n".join(unknown),
            recommendation=(
                "Press Check on these routes before a long run, or give them a gauge the "
                "factory can read without asking."
            ),
        ))
    return findings


def observed_draw(store: EvidenceStore, spec_hash: str) -> dict[str, float]:
    """How much of each plan this run actually drew, per route.

    The difference between the reading taken before the build and the one taken
    after it. Two things make that an upper bound rather than a measurement, and
    both are worth stating rather than correcting for:

    The window is shared. Whoever else was using the same plan while the build
    ran is inside this figure, and there is no way to subtract them -- which is
    the same reason `meters` reads the gauge instead of counting it. An upper
    bound is the safe direction for the only question this feeds: whether the
    next run has room to finish.

    A window that rolled over during the build is not measurable at all. Its
    closing utilization belongs to a window that started mid-run, so the
    subtraction would produce a negative number or a meaningless small one. Those
    are dropped rather than clamped: one fewer observation is cheaper than a
    wrong one, and this figure is the basis of a decision to defer work.
    """
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for record in store.records():
        if record.get("kind") != "headroom" or record.get("spec_hash") != spec_hash:
            continue
        payload = record.get("payload") or {}
        target = after if payload.get("when") == "after" else before
        target.clear()
        target.update(payload.get("routes") or {})
    if not before or not after:
        return {}

    out: dict[str, float] = {}
    for name, opened in before.items():
        closed = after.get(name)
        if not closed or not opened.get("windowed"):
            continue
        starts = {w.get("name"): w for w in (opened.get("windows") or []) if not w.get("stale")}
        ends = {w.get("name"): w for w in (closed.get("windows") or []) if not w.get("stale")}
        drawn: list[float] = []
        for key, start in starts.items():
            end = ends.get(key)
            if not end:
                continue
            if float(start.get("resets_at") or 0) != float(end.get("resets_at") or 0):
                continue          # the window rolled; see the docstring
            delta = float(end.get("utilization") or 0) - float(start.get("utilization") or 0)
            if delta > 0:
                drawn.append(delta)
        if drawn:
            # The tightest window is what constrains the next run, so the
            # largest draw is the one worth remembering.
            out[name] = max(drawn)
    return out


def check_route_fallbacks(fallbacks) -> list[Finding]:
    """Agents that ran somewhere other than where they were configured.

    A fallback is what keeps a run going when a subscription route reaches its
    ceiling, and the alternative -- losing the review panel three phases from a
    packet, with everything before it already paid for -- is worse. It is still
    not free. The packet names which agents read the work, and a reader decides
    what the verdict is worth partly from that; if one of them read it from a
    substitute on another vendor, at another model, that is a different reading
    from the one the configuration describes.

    So it is reported. Not as a defect -- nothing here failed -- but as a fact
    about this run that a reader would otherwise have to infer from a model id
    in a usage table nobody opens.
    """
    if not fallbacks:
        return []
    by_role: dict[str, list[dict]] = {}
    for f in fallbacks:
        by_role.setdefault(str(f.get("role") or "?"), []).append(f)
    return [Finding(
        id="fallback-1",
        title="Some agents ran on a substitute model because their own ran out",
        severity="minor",
        category="verification",
        detail=(
            "Each of these was pointed at a route that would not carry it, and ran on the "
            "substitute named for it rather than failing the run. What it found is real; what "
            "changed is who found it.\n\n"
            + "\n".join(
                f"- {role}: {hits[0].get('from')} -> {hits[0].get('to')} "
                f"({hits[0].get('model')}), {len(hits)} call(s). "
                f"{hits[0].get('because', '')}"
                for role, hits in sorted(by_role.items()))
        ),
        evidence="\n".join(
            f"{f.get('role')}: {f.get('from')} -> {f.get('to')} ({f.get('model')})"
            for f in fallbacks),
        recommendation=(
            "Nothing, if the substitute is one you would have chosen. If it is not, the "
            "reading it produced is thinner than the configuration claims -- and a role whose "
            "fallback shares a family with something it exists to disagree with is thinner "
            "again, which `independence_problems` reports at rest."
        ),
    )]


_ENV_IN_COMMAND = re.compile(r"\b([A-Z][A-Z0-9_]{2,})=(\S+)")


_ENV_IN_FILE = re.compile(r"^\s*([A-Z][A-Z0-9_]{2,})\s*[:=]\s*[\"\']?([^\"\'#\n]+)")


def check_environment_overrides(project_state, written: Sequence[FileWrite]) -> list[Finding]:
    """Variables this run set over the top of what the feature configured.

    A project's service commands carry environment assignments, and a command
    line beats a file: whatever the feature wrote into its own configuration is
    outvoted for the length of the run. Sometimes that is the whole point -- a
    disposable database belongs to the run, not to the repository -- and
    sometimes it is the run quietly contradicting the work it is judging.

    Measured: a feature added cookie authentication and set its own allowed
    origin to `localhost`. The service command set the same variable to the
    numeric address, so the browser tier could not log in, seven checks were
    green and the one that drives a real browser was red. Nothing anywhere
    said a variable had been replaced -- not the packet, not the brief, not
    the gate output. The value is the project's to fix; the silence is what
    this reports.

    Crude on purpose, in the manner of `check_declared_changes`: it matches
    assignments by name and compares the text, without pretending to
    understand a shell. One finding listing what was replaced, not one per
    variable -- most runs replace something, and a reader needs the list
    rather than a queue.
    """
    services = list(getattr(project_state, "services", ()) or [])
    if not services or not written:
        return []
    ours: dict[str, str] = {}
    for service in services:
        for text in (getattr(service, "command", ""), getattr(service, "ready_when", "")):
            for name, value in _ENV_IN_COMMAND.findall(text or ""):
                ours.setdefault(name, value)
    if not ours:
        return []

    clashes: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    for item in written:
        for line in (item.contents or "").splitlines():
            found = _ENV_IN_FILE.match(line)
            if not found:
                continue
            name, value = found.group(1), found.group(2).strip()
            if name not in ours or name in seen:
                continue
            mine, theirs = value.strip("\"'"), ours[name].strip("\"'")
            if mine and theirs and mine != theirs:
                seen.add(name)
                clashes.append((name, mine, theirs, item.path))
    if not clashes:
        return []

    listed = "\n".join(
        f"- `{name}`: this feature set `{mine}` in {path}; the run sets `{theirs}`"
        for name, mine, theirs, path in clashes)
    return [Finding(
        id="envoverride-1",
        title="The run overrode settings this feature configured for itself",
        severity="minor",
        category="verification",
        detail=(
            "A project's service commands carry environment assignments, and a command "
            "line beats a file -- so what this feature wrote into its own configuration "
            "is not what ran. Often that is deliberate and right; a disposable database "
            "belongs to the run rather than to the repository. It is worth reading once, "
            "because the alternative is a check that fails against a setting nobody in "
            "the run can see.\n\n" + listed
        ),
        evidence=listed,
        recommendation=(
            "Nothing, where the run's value is the one that should win. Where the "
            "feature's value is, the project's environment is the place to change it -- "
            "no agent in the build can, because those commands are the project's rather "
            "than the feature's."
        ),
    )]


def _gap_line(path: str, worker: WorkerOutput, label: bool) -> str:
    """One missing file and what its author said about it, verbatim.

    A disclosure usually opens with the path it is about, so prefixing it with
    the path again reads as a stutter. The prefix earns its place only when
    there is more than one file to tell apart, and even then only when the line
    does not already begin by naming one.
    """
    said = next((line for line in worker.disclosure.not_implemented
                 if path.rsplit("/", 1)[-1].lower() in line.lower()), "")
    if not said:
        return f"{path} — the unit said nothing about this one."
    return f"{path} — {said}" if label and not said.lstrip().startswith(path) else said


def check_unit_delivery(
    plan: Plan, workers: Sequence[WorkerOutput],
) -> list[Finding]:
    """Units that were given work and did not do it.

    A unit that owns three files and writes one has left criteria unbuilt, and
    the honest signal for that is sitting in the plan next to the output. It is
    worth computing rather than reading, because the consequence is not just a
    missing feature: the integrator sees the gap, tries to close it, and reaches
    outside its own remit to do so. That is how a run that merely under-built
    became a run that destroyed a page.
    """
    by_unit = {w.unit_id: w for w in workers}
    findings: list[Finding] = []

    for unit in plan.units:
        worker = by_unit.get(unit.id)
        if worker is None:
            continue                                  # a lane failure, reported elsewhere
        written = {f.path for f in worker.files}
        owed = [p for p in unit.files_expected if p not in written]
        disclosed = " ".join(worker.disclosure.not_implemented).lower()
        if not owed:
            continue
        # A unit that says what it did not build is doing its job; the finding is
        # still worth raising, at a severity that reflects the honesty.
        silent = [p for p in owed if p.rsplit("/", 1)[-1].lower() not in disclosed]
        # The title is about the software, not about the plan's bookkeeping.
        # "unit 'U-2' owned 14 file(s) and wrote 13: …portal.py untouched" is
        # true, and a sentence about counting rather than about a file that
        # is missing. A reviewer reading fourteen-versus-thirteen
        # has to do the subtraction and then still ask what it cost.
        findings.append(Finding(
            id=f"undelivered-{len(findings) + 1}",
            # One path is the most useful title there is; eleven joined by commas
            # is a paragraph in the position a title occupies, and it wraps to
            # four lines on a list of collapsed rows whose whole point is that
            # each is one line. The detail names every one of them, with what
            # the unit said about it.
            title=(f"A file the plan called for was never written — {owed[0]}" if len(owed) == 1
                   else f"Files the plan called for were never written — {len(owed)} of them"),
            severity="major" if silent else "minor",
            category="under-delivery",
            # The unit's own account of why, per file, verbatim.
            #
            # Not "The plan gave 'U-2' these files and these criteria: AC-6,
            # AC-7, AC-8 … AC-19" -- fourteen ids, thirteen of which have
            # nothing to do with the file that is missing, and not one word
            # about the file that is. Where the unit has explained itself in
            # its own disclosure and nothing carries that across, the only
            # reasoned sentence about this gap reaches the packet as an
            # arbiter's rebuttal to an argument the packet never showed.
            #
            # Which criteria the missing file actually cost is not knowable
            # here: this runs at build time and the trace is computed after.
            # Saying who was to write it and what they say about not writing it
            # is what this check can honestly say, and it is the whole of what
            # a reviewer needs to start.
            detail="\n\n".join(
                [f"The unit building {unit.title!r} ({unit.id}) was to write "
                 f"{'it' if len(owed) == 1 else 'these'} and did not."]
                + [_gap_line(path, worker, len(owed) > 1) for path in owed]
            ),
            # The paths themselves, so every screen that joins on `files` can
            # reach this one: the criteria the trace says depend on the missing
            # file, the rapporteur's account of what is there now, the diff.
            # Empty, it would be a finding about a file that names no file.
            files=list(owed),
            # The unit's criteria belong here rather than in the detail: they
            # are what a reader checks the claim against, not what the claim
            # is. Fourteen ids read as a sentence are noise; read as evidence
            # they are the plan.
            evidence=(f"unit {unit.id} — {unit.title} — was responsible for: "
                      f"{', '.join(unit.criterion_ids) or 'no named criteria'}\n"
                      f"files_expected: {unit.files_expected}\nwritten: {sorted(written)}"),
            recommendation=(
                "Criteria owned by this unit are unbuilt. Check whether the integrator "
                "then tried to close the gap from outside its own remit."
            ),
        ))
    return findings


def check_independence(config: Config) -> list[Finding]:
    """Dissenting agents that share a family with what they exist to doubt.

    The config states three of these rules in its own comments, addressed to a
    human: "if you change `worker`, check that this is still a different
    family". That is a rule enforced by memory, at the one moment memory is
    least reliable -- somebody changing a model. This computes it instead.

    A finding rather than a refusal, because running everything on one family
    is a legitimate thing to do while proving the machine works. What must not
    happen is that it becomes true without anybody noticing, and a packet whose
    review panel shared the builder's priors is a packet whose agreement means
    less than it appears to.
    """
    problems = config.independence_problems() if hasattr(
        config, "independence_problems") else independence_problems(config)
    if not problems:
        return []
    return [Finding(
        id="independence-1",
        title="Some reviewers use the same model family as the work they are meant to challenge",
        severity="major",
        category="independence",
        detail=("\n\n".join(problems) + "\n\nThis does not mean the findings in this "
                "packet are wrong. It means the agreement between these agents carries "
                "less information than it looks like it does, and a criterion they all "
                "passed is weaker evidence than the same criterion passed by agents that "
                "could actually have disagreed."),
        evidence="; ".join(
            f"{name}={model_family(config, name)}"
            for name in sorted(set(INDEPENDENT_OF) | {"worker", "reviewer"})
            if name in config.roles),
        recommendation=("Point one of each pair at a different route, or accept it "
                        "knowingly -- the point is that it is now visible either way."),
    )]


def check_budget_coverage(coverage: dict[str, Any], config: Config) -> list[Finding]:
    """Agents the budget did not constrain, as a fact about the run.

    `budget_usd` stops a run by comparing dollars against a ceiling, and it can
    only do that for work somebody is charged dollars for. An agent on a
    subscription reports a figure that is not money, so it stays out of the
    total the guard reads -- which means the guard is blind to it, not that the
    work was free.

    The number is right and the sentence it produces is not. A packet saying
    "$0.00 of $10.00 spent" over a run that did every unit of work on a plan
    reads as a cheap run, and the reader draws exactly the wrong conclusion
    about how much room is left. What actually bounds such a run is the plan's
    rolling window, which is not a figure this system holds at all.

    A finding rather than a log line, for the reason every other run-fact is
    one: the packet is the argument, and a limit that was never enforced is
    part of the argument whether or not anything went wrong because of it.
    """
    roles = list(coverage.get("unbilled_roles") or [])
    if not roles:
        return []
    turns = int(coverage.get("unbilled_turns") or 0)
    notional = float(coverage.get("notional_usd") or 0.0)
    billed = float(coverage.get("billed_usd") or 0.0)
    return [Finding(
        id="budget-1",
        title=("Part of this run's cost is on subscriptions the budget can't measure — "
               + ", ".join(roles[:6])
               + (f" and {len(roles) - 6} more" if len(roles) > 6 else "")),
        severity="minor",
        category="budget",
        detail=(
            f"{len(roles)} agent(s). "
            f"These agents ran on routes metered against a plan rather than billed per "
            f"token, so nothing they spent could be compared to `budget_usd` "
            f"(${config.rework.budget_usd:.2f}) and the ceiling never applied to them. "
            f"The run's billed total is ${billed:.4f}; the same work would have cost "
            f"about ${notional:.4f} had it been billed, across {turns} turn(s).\n\n"
            "Nothing is necessarily wrong here. It is recorded because the packet's "
            "cost figures describe a smaller thing than the run, and a reader deciding "
            "whether there is room for another attempt would otherwise be reading a "
            "number that cannot answer that question."),
        evidence=(f"routes in use: {', '.join(coverage.get('routes') or []) or 'none recorded'}"),
        recommendation=(
            "Nothing, unless the run stopped unexpectedly -- in which case the cause is "
            "more likely the plan's rate limit than this budget, and the two look "
            "nothing alike in the log."),
    )]


def check_harness_delivery(workers: Sequence[WorkerOutput]) -> list[Finding]:
    """Units the coding harness declined to build, as a fact about the tooling.

    Separate from `check_unit_delivery` on purpose, because the two ask for
    opposite responses from a human. "This unit under-built" is a question about
    the code. "The harness ran twice, exited zero, and edited nothing" is a
    question about the harness -- and reading the second as the first is exactly
    what happened: a run lost four units this way, and the packet described the
    result as a feature that was never implemented rather than as a tool that
    never ran.

    A blocker regardless of what was disclosed. `check_unit_delivery` softens to
    `minor` when a unit is honest about its gap, which is right for a unit that
    made a judgement. A harness that produced nothing made no judgement to be
    honest about, and every criterion the unit owned is unbuilt for a reason
    that has nothing to do with the specification.
    """
    from ...executors import FELL_BACK, NO_EDITS

    refused = [w for w in workers if NO_EDITS in w.disclosure.flags
               and FELL_BACK not in w.disclosure.flags]
    salvaged = [w for w in workers if FELL_BACK in w.disclosure.flags]
    findings: list[Finding] = []

    if refused:
        findings.append(Finding(
            id="harness-1",
            title=("Some units' coding agent produced no changes at all — "
                   f"{', '.join(w.unit_id for w in refused)}"),
            severity="blocker",
            category="harness",
            detail=(
                "Each of these units ran the harness twice, and twice it exited without "
                "changing a file. Nothing these units owned exists on the branch. This is a "
                "failure of the tool, not of the specification and not of the code: the "
                "criteria they owned are unbuilt, and every downstream finding about those "
                "criteria describes an absence this caused. Read the harness output below "
                "before reading anything else in this packet."
            ),
            evidence="\n\n".join(f"## {w.unit_id}\n{w.summary}" for w in refused)[:6000],
            files=[],
            recommendation=(
                "Check whether the harness is being given its role prompt and whether its "
                "model will edit files at all. Re-running the same configuration reproduces "
                "this; it is not flaky."
            ),
        ))

    if salvaged:
        findings.append(Finding(
            id=f"harness-{len(findings) + 1}",
            title=("Some units were written without a coding agent, as a fallback — "
                   f"{', '.join(w.unit_id for w in salvaged)}"),
            severity="minor",
            category="harness",
            detail=(
                "The harness edited nothing on either attempt, so these units were rebuilt "
                "by the `direct` executor, which returns whole files instead of editing in "
                "place. The work is on the branch. It is worth knowing which units came from "
                "which path: whole-file writes are a blunter instrument than edits, and this "
                "is the number that says whether the harness is working."
            ),
            evidence="\n\n".join(f"## {w.unit_id}\n{w.summary[:800]}" for w in salvaged)[:6000],
            files=[f.path for w in salvaged for f in w.files],
            recommendation="No action if the diff is sound. Investigate if this is every unit.",
        ))
    return findings


def check_write_collisions(
    workers: Sequence[WorkerOutput], integration: IntegrationReport | None = None,
) -> list[Finding]:
    """Two units that wrote the same file.

    `_apply_writes` walks the list and calls `write_text` per entry, so the last
    author of a path silently wins and the earlier unit's work is gone with no
    conflict, no rejection and nothing in the packet. The units are built in
    parallel from contracts and cannot see each other, so this is a normal thing
    for them to do and an abnormal thing for a human not to be told about.

    The integrator overwriting a worker is excluded: closing seams by rewriting
    what a unit produced is precisely its job.
    """
    by_path: dict[str, list[str]] = {}
    for worker in workers:
        for f in worker.files:
            authors = by_path.setdefault(f.path, [])
            if worker.unit_id not in authors:
                authors.append(worker.unit_id)

    findings: list[Finding] = []
    fixed = {f.path for f in (integration.files if integration else [])}
    for path, authors in sorted(by_path.items()):
        if len(authors) < 2:
            continue
        settled = path in fixed
        findings.append(Finding(
            id=f"collision-{len(findings) + 1}",
            title=f"Two parts of the build wrote the same file — {path}",
            severity="major",
            category="write-collision",
            detail=(
                f"Units {', '.join(repr(a) for a in authors)} each produced complete contents "
                f"for {path}. They were built in parallel and could not see each other."
                + (" The integrator rewrote this path afterwards, so what landed is its version."
                   if settled else
                   " Writes are applied in order, so only the last one survived and the earlier "
                   "unit's work is not on disk.")
            ),
            evidence=f"{path} appears in the output of: {', '.join(authors)}.",
            recommendation=(
                "Check the file against what each unit said it built. If the units disagreed "
                "about the same code, the seam between them was wrong at the architect stage."
            ),
        ))
    return findings
