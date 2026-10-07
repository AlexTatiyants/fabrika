"""The packet, rendered for someone who will never open the console.

The packet already exists twice: as a record in the ledger, and as the screen
the console draws from it. Both are reachable only from the machine the factory
runs on. On a branch that goes to a remote, the people who have to rule on the
work may have the repository and nothing else -- so the argument travels with
the code or it does not reach them.

This is a rendering, not a second source of truth. Nothing here decides
anything: every number is copied from `PacketStats`, which the orchestrator
computed, and every finding is printed with the text the agent that raised it
gave it. A renderer that summarised would be a place for the record to get
shorter, and the record only grows. (INV-11)
"""

from __future__ import annotations

from typing import Any, Sequence

from .schemas import (
    Decision,
    Finding,
    FindingRecord,
    MaterialityItem,
    Packet,
    Spec,
    TraceRow,
    UnclaimedFile,
)

#: What each outcome is called on the page. The literal values are written for
#: code; these are written for a reader who has not read the code.
_OUTCOME = {
    "open": "open",
    "repaired": "repaired",
    "no_longer_holds": "no longer holds",
    "attempted_not_fixed": "attempted, still standing",
    "escalated": "escalated to you",
    "needs_spec_change": "needs the spec reopened",
    "out_of_spec": "out of spec",
    "dismissed": "dismissed",
    "unattempted": "never attempted",
}

_VERDICT = {
    "ship": "Ship",
    "ship_with_rulings": "Ship, with rulings",
    "send_back": "Send back",
    "reject": "Reject",
}

_TRACE = {
    "traced": "traced",
    "orphan_requirement": "orphan requirement",
    "untested": "untested",
}


def _esc(text: str) -> str:
    """Safe inside a Markdown table cell.

    A finding's evidence is quoted source, and source contains pipes. Without
    this a single `|` in a code sample silently invents a column and every row
    after it is misaligned -- which looks like the renderer is broken and is
    read, reasonably, as the packet being unreliable.
    """
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def _one_line(text: str, limit: int = 160) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _section(lines: list[str], title: str) -> None:
    lines += ["", f"## {title}", ""]


def _lines(total: int, added: int, removed: int) -> str:
    """A line count, with the split only when there is one.

    A run from before the split was measured carries the total and two zeros,
    and "14546 (+0 / -0)" reads as a broken renderer rather than as an older
    record. Absent and zero are different claims here too.
    """
    if added or removed:
        return f"{total} (+{added} / -{removed})"
    return str(total)


def _stat_rows(stats: Any) -> list[tuple[str, str]]:
    """The counted numbers. Only facts -- nothing here is a model's opinion."""
    rows = [
        ("Checks passed", f"{stats.gates_passed} of {stats.gates_total}"),
        ("Criteria verified", f"{stats.criteria_verified} of {stats.criteria_total}"),
        ("Orphan requirements", str(stats.orphan_requirements)),
        ("Untested criteria", str(stats.untested_criteria)),
        ("Files written", str(stats.files_written)),
        ("Lines changed", _lines(stats.lines_written, stats.lines_added, stats.lines_removed)),
        ("Files no criterion accounts for", str(stats.unclaimed_files)),
        ("Decisions", str(stats.decisions_total)),
    ]
    # Raised and standing are different claims, and a packet that printed only
    # the second would look better for having had work done on it. Only when the
    # pair is coherent, though: a record that never populated `blockers_raised`
    # would otherwise render "15 standing, 0 raised", which is not a fact about
    # anything and reads as the renderer being wrong.
    if stats.blockers_raised > stats.blockers:
        rows.append(("Blockers", f"{stats.blockers} standing, {stats.blockers_raised} raised"))
    else:
        rows.append(("Blockers", str(stats.blockers)))
    if stats.findings_duplicate:
        rows.append((
            "Findings",
            f"{stats.findings_total} raised, {stats.findings_distinct} distinct "
            f"({stats.findings_duplicate} restatements)",
        ))
    else:
        rows.append(("Findings", str(stats.findings_total)))
    rows.append(("Findings repaired / open", f"{stats.findings_repaired} / {stats.findings_open}"))
    if stats.breaker_suite_failed:
        rows.append(("Adversary suite", "did not run to completion"))
    rows.append(("Adversary failures", str(stats.breaker_failures)))
    if stats.agent_failures:
        # Never silent: every count above was computed as though these ran.
        rows.append(("Agents that did not return", str(stats.agent_failures)))
    rows.append(("Repair rounds", str(stats.rework_rounds)))
    return rows


def _findings_table(findings: Sequence[Finding], records: Sequence[FindingRecord]) -> list[str]:
    by_id = {r.finding_id: r for r in records}
    out = [
        "| # | Severity | Finding | Outcome | Files |",
        "| --- | --- | --- | --- | --- |",
    ]
    for f in findings:
        rec = by_id.get(f.id)
        outcome = _OUTCOME.get(rec.outcome, rec.outcome) if rec else "open"
        if rec and rec.duplicate_of:
            outcome += f" (restates {rec.duplicate_of})"
        if rec and rec.attempts > 1:
            outcome += f", {rec.attempts} attempts"
        files = ", ".join(f"`{p}`" for p in f.files[:3]) or "—"
        if len(f.files) > 3:
            files += f" +{len(f.files) - 3}"
        out.append(
            f"| {f.id} | {f.severity} | {_esc(_one_line(f.title))} | {_esc(outcome)} | {files} |"
        )
    return out


def _finding_detail(f: Finding, rec: FindingRecord | None) -> list[str]:
    out = [f"#### {f.id} — {_one_line(f.title)}", ""]
    bits = [f"**{f.severity}**"]
    if f.category:
        bits.append(f.category)
    if rec:
        bits.append(_OUTCOME.get(rec.outcome, rec.outcome))
        if rec.role:
            bits.append(f"raised by {rec.role}")
        if rec.rounds_seen:
            bits.append("round " + ", ".join(str(r) for r in rec.rounds_seen))
    out += [" · ".join(bits), ""]
    if f.detail:
        out += [f.detail.strip(), ""]
    if f.evidence:
        out += ["> " + _one_line(f.evidence, 600), ""]
    if f.recommendation:
        out += [f"**Recommended:** {f.recommendation.strip()}", ""]
    if f.criterion_ids:
        out += [f"**Criteria affected:** {', '.join(f.criterion_ids)}", ""]
    if rec and rec.corroborated_by:
        # Corroboration is why this was repaired rather than escalated, and it
        # means a *different* agent found the same defect.
        out += [f"**Corroborated by:** {', '.join(rec.corroborated_by)}", ""]
    if rec and rec.restated_by:
        # Said, and said apart from the line above. The panel is sampled more
        # than once on purpose, so the same agent wording one defect three ways
        # is the sampling working -- a fact about the panel, not a second
        # opinion about the code. Printed because a reader counting entries in
        # the packet would otherwise count it as one.
        out += [
            f"**Also restated by the same agent:** {', '.join(rec.restated_by)}"
            " — further passes of one reader, not further readers.", "",
        ]
    if rec and rec.disposition_reason:
        out += [f"**Ruling:** {rec.disposition} — {rec.disposition_reason.strip()}", ""]
    if rec and rec.outcome_evidence:
        out += [f"**Outcome:** {rec.outcome_evidence.strip()}", ""]
    if rec and rec.commit:
        out += [f"**Repaired in:** `{rec.commit[:12]}`", ""]
    return out


def _decisions(decisions: Sequence[Decision]) -> list[str]:
    out: list[str] = []
    for d in decisions:
        out += [f"#### {d.id} — {_one_line(d.title)}", ""]
        bits = [
            f"**{d.reversibility}**",
            f"confidence {d.confidence:.2f}",
        ]
        if d.tags:
            bits.append(", ".join(d.tags))
        out += [" · ".join(bits), ""]
        if d.rationale:
            out += [d.rationale.strip(), ""]
        if d.alternatives_rejected:
            out += ["**Rejected:**", ""]
            out += [f"- {a.strip()}" for a in d.alternatives_rejected]
            out += [""]
        if d.blast_radius:
            out += [f"**If this is wrong:** {d.blast_radius.strip()}", ""]
        if d.criterion_ids:
            out += [f"**Serves:** {', '.join(d.criterion_ids)}", ""]
        if d.files:
            out += ["**In:** " + ", ".join(f"`{p}`" for p in d.files), ""]
    return out


def _trace(rows: Sequence[TraceRow]) -> list[str]:
    out = [
        "| Criterion | Status | Implemented in | Blind tests |",
        "| --- | --- | --- | --- |",
    ]
    for r in rows:
        files = ", ".join(f"`{p}`" for p in r.implementing_files) or "—"
        tests = ", ".join(f"`{t}`" for t in r.test_names) or "—"
        out.append(
            f"| **{r.criterion_id}** {_esc(_one_line(r.statement, 90))} "
            f"| {_TRACE.get(r.status, r.status)} | {files} | {tests} |"
        )
    return out


def _materiality(items: Sequence[MaterialityItem]) -> list[str]:
    out = ["| File | Class | Lines | Why |", "| --- | --- | --- | --- |"]
    for m in items:
        out.append(
            f"| `{m.path}` | {m.klass} | {_lines(m.lines, m.added, m.removed)} "
            f"| {_esc(_one_line(m.reason, 120))} |"
        )
    return out


def _unclaimed(items: Sequence[UnclaimedFile]) -> list[str]:
    out = ["| File | Lines | Written by | What the unit said |", "| --- | --- | --- | --- |"]
    for u in items:
        who = ", ".join(u.written_by) or "—"
        said = _esc(_one_line(" ".join(u.disclosures), 160)) or "—"
        out.append(f"| `{u.path}` | {_lines(u.lines, u.added, u.removed)} | {who} | {said} |")
    return out


def _rework(rw: Any) -> list[str]:
    if not rw.enabled:
        return ["The repair loop was disabled for this run."]
    out = [
        f"{rw.rounds} round(s). **Stopped because:** {rw.stop_reason or 'not recorded'}.",
        "",
        f"- Repaired: {rw.repaired}",
        f"- Attempted, still standing: {rw.attempted_not_fixed}",
        f"- Escalated: {rw.escalated}",
        f"- Dismissed: {rw.dismissed}",
        f"- Never attempted: {rw.unattempted}",
        "",
        f"Cost ${rw.cost_usd:.2f} of a ${rw.budget_usd:.2f} budget "
        f"(${rw.lifetime_usd:.2f} lifetime), {rw.elapsed_s / 60:.1f} minutes.",
    ]
    if rw.unbilled_roles:
        # Correct arithmetic that means the opposite of what it says, unless
        # this is beside it: a budget cannot constrain what nobody is charged.
        out += [
            "",
            f"{len(rw.unbilled_roles)} role(s) ran on routes that report no dollars "
            f"({', '.join(rw.unbilled_roles)}), spending {rw.unbilled_turns} turn(s) "
            f"— notionally ${rw.notional_usd:.2f}, never counted against the budget.",
        ]
    if rw.reverted_rounds:
        out += ["", f"Rounds reverted for making the checks worse: "
                    f"{', '.join(str(r) for r in rw.reverted_rounds)}."]
    if rw.protected_writes_refused:
        # A finding about the repairer, not a nuisance. (INV-12)
        out += ["", "**Repair writes refused for reaching the verification surface:**", ""]
        out += [f"- `{p}`" for p in rw.protected_writes_refused]
    return out


def packet_markdown(
    packet: Packet,
    *,
    title: str,
    feature_id: str,
    spec_hash: str,
    branch: str = "",
    base_sha: str = "",
    commit_sha: str = "",
    system: Sequence[str] | None = None,
) -> str:
    """The whole packet as one Markdown document.

    Every section the console shows is here, in the order the rapporteur ranked
    them. Nothing is elided for length: a reader who wants the short version has
    the headline and the stats table, and a reader ruling on the work needs the
    finding whose detail a summary would have dropped.
    """
    verdict = _VERDICT.get(packet.verdict, packet.verdict)
    lines = [
        f"# {title}",
        "",
        f"**{verdict}** · {packet.attention_budget_minutes} minutes of review · "
        f"spec `{spec_hash[:12]}`",
        "",
        packet.headline.strip(),
        "",
        "> This is a review packet, not a diff. It is the argument that this branch does what",
        "> was specified, with everything that argues against it left in. Rule on the decisions",
        "> and the open findings; the matrix below is how you reach the code.",
    ]

    if packet.summary:
        _section(lines, "Summary")
        lines.append(packet.summary.strip())

    if packet.manual_checks:
        # Before the case against: nobody has checked these, and the verdict
        # above is "with rulings" because of them.
        _section(lines, "Check these yourself")
        lines += ["Fabrika didn't test these: their test level can't run cleanly in this "
                  "project. Check each by hand before you ship.", ""]
        for m in packet.manual_checks:
            lines.append(f"- [ ] **{m.criterion_id}** {m.title or m.statement}"
                         + (f" — {m.how.strip()}" if m.how.strip() else ""))

    if packet.strongest_objection:
        _section(lines, "The strongest case against shipping this")
        lines.append(packet.strongest_objection.strip())

    _section(lines, "Counted")
    lines += ["| | |", "| --- | --- |"]
    lines += [f"| {label} | {value} |" for label, value in _stat_rows(packet.stats)]

    if system:
        # Beside "Counted": the same change, in the system's terms rather than
        # the file's -- computed from the as-built at the base and at the head.
        _section(lines, "What this changes in the system")
        lines += [f"- {line}" for line in system]

    if packet.decisions:
        _section(lines, "Decisions")
        lines.append("Ranked by how much they need you. Ordering is the rapporteur's product.")
        lines.append("")
        lines += _decisions(packet.decisions)

    if packet.findings:
        _section(lines, "Findings")
        lines.append(
            f"All {len(packet.findings)}, including the ones that were fixed. "
            "A packet that got shorter as the loop worked would look better and be worth less."
        )
        lines.append("")
        lines += _findings_table(packet.findings, packet.records)
        lines += ["", "### In full", ""]
        by_id = {r.finding_id: r for r in packet.records}
        for f in packet.findings:
            lines += _finding_detail(f, by_id.get(f.id))

    if packet.trace:
        _section(lines, "Traceability")
        lines.append("Computed by the orchestrator, not reported by an agent. (INV-3)")
        lines.append("")
        lines += _trace(packet.trace)

    if packet.unclaimed:
        _section(lines, "Not asked for")
        lines.append(
            "Files no criterion accounts for — the matrix inverted, so a file cannot "
            "escape this list by staying quiet."
        )
        lines.append("")
        lines += _unclaimed(packet.unclaimed)

    if packet.materiality:
        _section(lines, "Materiality")
        lines.append("Every written file, classified. Unsure means novel, never safe.")
        lines.append("")
        lines += _materiality(packet.materiality)

    if packet.disclosures:
        _section(lines, "What the workers disclosed")
        lines.append("Verbatim.")
        lines.append("")
        lines += [f"- {d.strip()}" for d in packet.disclosures]

    if packet.open_questions:
        _section(lines, "Carried into the build unanswered")
        lines += [f"- {q.strip()}" for q in packet.open_questions]

    _section(lines, "The repair loop")
    lines += _rework(packet.rework)

    _section(lines, "Provenance")
    rows = [("Feature", f"`{feature_id}`"), ("Spec hash", f"`{spec_hash}`")]
    if branch:
        rows.append(("Branch", f"`{branch}`"))
    if base_sha:
        rows.append(("Branched from", f"`{base_sha[:12]}`"))
    if commit_sha:
        rows.append(("Work committed at", f"`{commit_sha[:12]}`"))
    lines += ["| | |", "| --- | --- |"]
    lines += [f"| {label} | {value} |" for label, value in rows]
    lines += [
        "",
        "Generated by the software factory. The full evidence ledger — every agent exchange,",
        "every prompt, every round — stays in the factory's evidence store; this document is a",
        "rendering of the packet record, not a summary of it.",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# the frozen spec
# --------------------------------------------------------------------------


def spec_markdown(
    spec: Spec,
    *,
    feature_id: str,
    spec_hash: str,
    verdict: str = "",
    note: str = "",
    at: str = "",
    branch: str = "",
) -> str:
    """What was asked for, as it stood when a human froze it.

    `workspace.spec_text` renders a spec for a prompt and drops what a model
    cannot use: an acceptance criterion's `area`, the level it can honestly be
    verified at, and how hard it is to undo. Those three are exactly what a
    person reads a frozen spec for a year later, so this is a second rendering
    rather than a reuse. Neither is the record -- the ledger is.
    """
    lines = [f"# {spec.title}", ""]
    if verdict:
        lines += [
            f"**{verdict.capitalize()}**"
            + (f" on {at[:10]}" if at else "")
            + f" · spec `{spec_hash[:12]}`",
            "",
        ]
        if note:
            lines += [f"> {_one_line(note, 400)}", ""]
    lines += [
        "This is the specification this branch was built against, frozen before any code was",
        "written and unchanged since. It is here so the branch carries what it was asked to do,",
        "not only what it did.",
        "",
    ]

    if spec.intent:
        _section(lines, "The intent, verbatim")
        lines.append(spec.intent.strip())
    if spec.summary:
        _section(lines, "Summary")
        lines.append(spec.summary.strip())

    _section(lines, "Acceptance criteria")
    if spec.acceptance_criteria:
        lines.append("Anything not here was out of scope. Ids are permanent and never renumbered.")
        lines.append("")
        lines += ["| # | Criterion | Area | Verified at | Reversibility |",
                  "| --- | --- | --- | --- | --- |"]
        for c in spec.acceptance_criteria:
            lines.append(
                f"| **{c.id}** | {_esc(c.statement)} | {c.area or '—'} "
                f"| {c.verified_at or '—'} | {c.reversibility or '—'} |"
            )
        lines += ["", "### In full", ""]
        for c in spec.acceptance_criteria:
            lines += [f"#### {c.id}", "", c.statement.strip(), ""]
            if c.rationale:
                lines += [f"**Why:** {c.rationale.strip()}", ""]
            if c.verification:
                lines += [f"**Confirmed by:** {c.verification.strip()}", ""]
    else:
        lines.append("None recorded.")

    for heading, items in (
        ("Non-goals", spec.non_goals),
        ("Constraints", spec.constraints),
        # Each one a thing that could be wrong, and worth as much as the
        # criteria to whoever reads this after it turns out to have been.
        ("Assumptions", spec.assumptions),
        ("Libraries this feature adds", spec.new_dependencies),
        ("Carried into the build unanswered", spec.open_questions),
    ):
        if items:
            _section(lines, heading)
            lines += [f"- {str(i).strip()}" for i in items]

    if spec.resolved_answers:
        _section(lines, "Settled by a human before the freeze")
        for a in spec.resolved_answers:
            lines += [f"- **{_one_line(a.question, 200)}**",
                      f"    - {_one_line(a.answer, 400)} ({a.source})"]

    _section(lines, "Provenance")
    rows = [("Feature", f"`{feature_id}`"), ("Spec hash", f"`{spec_hash}`")]
    if branch:
        rows.append(("Branch", f"`{branch}`"))
    if at:
        rows.append(("Ruled on", at))
    lines += ["| | |", "| --- | --- |"]
    lines += [f"| {label} | {value} |" for label, value in rows]
    lines.append("")
    return "\n".join(lines)
