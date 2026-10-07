"""A human's flags at gate 2, and where code decides they go (INV-6).
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from ..schemas import Finding, ReworkPlan, HumanFlag

from .ledger import FindingLedger


def compute_rework(flags: Sequence[HumanFlag]) -> ReworkPlan:
    """Where a gate-2 review goes, decided by code from what a human flagged.

    Two outcomes, not five: sent to the repair loop, or not. `dismissed` is a
    catch-all -- anything not `repair`, whatever the reason, including a flag
    an older ledger filed under a finer-grained choice. Nothing about *where
    the review goes* is lost by collapsing them: of those choices only `repair`
    routes anywhere but `accept`, since `needs_spec_change` is not offered (see
    `ReworkPlan`), so two outcomes say everything the finer ones did.

    `oracle` is not a third thing a human picked. It is a repair that landed
    on a file only the oracle may write, moved there by the arbiter (`dispose`), and it
    counts as work the same way a repair does: it rides this route, not
    around it.

    This is INV-6 at the human boundary. The human supplies judgment -- one
    disposition per flag -- and plain code decides what that judgment means. The
    route is never a menu, so a destination nobody can honour is never offered.
    """
    def ids(disposition: str) -> list[str]:
        return [f.id for f in flags if f.disposition == disposition]

    to_repair = ids("repair")
    to_oracle = ids("oracle")
    working = {*to_repair, *to_oracle}
    dismissed = [f.id for f in flags if f.id not in working]

    if working:
        route = "repair"
        n = len(working)
        oracle_note = (
            f" {len(to_oracle)} of them {'is' if len(to_oracle) == 1 else 'are'} on a file "
            "only the oracle may write, so the oracle fixes "
            f"{'it' if len(to_oracle) == 1 else 'those'}." if to_oracle else ""
        )
        because = (
            f"{n} flag{'' if n == 1 else 's'} routed to the repair loop. The build resumes "
            f"against the same spec.{oracle_note}"
        )
    elif flags:
        route = "accept"
        because = "Everything flagged was dismissed. There is no work for the repair loop."
    else:
        route = "accept"
        because = "Nothing flagged. Accepting takes the branch as it stands."

    return ReworkPlan(
        route=route, because=because, forced_by=[],
        to_repair=to_repair, to_oracle=to_oracle, dismissed=dismissed,
    )


def human_flags(records: Sequence[dict[str, Any]]) -> list[HumanFlag]:
    """Fold the flag records into the flags as they stand now.

    Retagging is a new record, never an edit -- the store has no update path and
    a review is evidence like everything else. What you first called something
    survives beside what you settled on; `latest` decides only the routing.
    """
    flags: dict[str, HumanFlag] = {}
    for record in records:
        kind = record.get("kind")
        if kind == "flag":
            flag = HumanFlag.model_validate(record["payload"])
            flags[flag.id] = flag
        elif kind == "flag_retag":
            payload = record["payload"]
            flag = flags.get(payload.get("flag_id", ""))
            if flag is not None:
                flag.disposition = payload["disposition"]
    return list(flags.values())


def _one_line(text: str, limit: int = 96) -> str:
    """A finding's title is one line and is the de-duplication key. A human's
    flag is a paragraph, so the first sentence stands in for it."""
    first = re.split(r"(?<=[.!?])\s", " ".join(text.split()).strip(), maxsplit=1)[0]
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "\u2026"


def _flag_note(flag: HumanFlag, anchored: Mapping[str, Finding]) -> str:
    """What the human actually wrote, which may be nothing.

    Two shapes mean "nothing". An empty `text` is what the console files. The
    other is a note equal to the anchored finding's own title, which an older
    console substituted for a note nobody typed (`note.trim() || f.title`,
    worklist.js): a ledger written by it carries flags whose words are a
    model's, stored as the human's. The ledger is append-only and those records
    say what they say, so that shape is recognised here rather than rewritten
    there.

    A note that genuinely repeats the finding's title reads as no note. That
    is the whole cost of the rule, and it costs nothing that matters: those
    words reach the repair loop either way.
    """
    said = flag.text.strip()
    source = anchored.get(flag.anchor)
    if source is not None and said == source.title.strip():
        return ""
    return said


def flag_findings(
    flags: Sequence[HumanFlag], anchored: Mapping[str, Finding] | None = None,
) -> list[Finding]:
    """A human flag, as the ledger sees it.

    Every flag becomes a finding, withdrawn ones included: INV-11 says rework
    only adds, and a thing you looked at and dismissed is still something that
    happened. Its disposition carries the dismissal.

    A FLAG WITH NO WORDS OF ITS OWN is not a claim, it is a routing decision on
    a claim that already exists: you read the finding, agreed with it, and had
    nothing to add. Filing the finding's own title as though you had typed it
    would paper over that: the review screen would show the machine's sentence
    back to you under the heading "your comments", and the ledger would gain a
    human finding that is a copy of one already in it.

    It carries the anchored finding's words instead, unaltered and not
    attributed to you -- `seed_flags` says whose they are in the disposition
    reason. Summarising or inventing a title here is precisely the paraphrase
    INV-11 exists to forbid, and an empty title would leave the repair loop
    with a nameless thing to fix.
    """
    anchored = anchored or {}
    out: list[Finding] = []
    for flag in flags:
        said = _flag_note(flag, anchored)
        source = anchored.get(flag.anchor)
        if said:
            title, detail = _one_line(said), flag.text
            # And what it was written on. "fix this" is an instruction about a
            # finding, and sent without it the remediator has a sentence and no
            # subject: one read "fix this" on a finding about a card losing a
            # removal and repaired a different race two files away. Carried
            # verbatim and attributed, never summarised. (INV-11)
            if source is not None:
                detail = (f"{detail}\n\n---\n\nWritten on {flag.anchor}, which said:\n\n"
                          f"{source.title}\n\n{source.detail}")
        elif source is not None:
            title, detail = source.title, source.detail
        else:
            # No words, and no finding to borrow them from. Say that, rather
            # than file something blank the loop cannot act on.
            where = f" on {flag.anchor}" if flag.anchor else ""
            title = f"Routed by the human at gate 2 with no note{where}"
            detail = title
        out.append(Finding(
            id=flag.id,
            title=title,
            severity=flag.severity,
            category="human",
            detail=detail,
            evidence=flag.anchor,
            recommendation="",
        ))
    return out


def seed_flags(
    ledger: "FindingLedger", flags: Sequence[HumanFlag], round_index: int,
) -> list[str]:
    """Put the human's flags into the ledger, already routed.

    They skip the arbiter entirely. Routing is a judgment about what should
    happen next, and this one was made by the person the arbiter works for;
    asking a model to re-rule on it would be the tool overruling its user.

    A retag between rounds moves an open flag. It does not move one the loop has
    already settled -- a repaired finding is history, and history does not take
    instructions.
    """
    if not flags:
        return []
    # Snapshotted before the add, which puts the human's own findings into the
    # same dict: an anchor names a finding that was already there.
    anchored = dict(ledger.findings)
    ids = ledger.add("human", flag_findings(flags, anchored), round_index, keep_ids=True)
    by_id = {flag.id: flag for flag in flags}
    for fid in ids:
        record = ledger.records.get(fid)
        flag = by_id.get(fid)
        if record is None or flag is None:
            continue
        if record.outcome in ("repaired", "attempted_not_fixed"):
            continue
        record.disposition = flag.disposition
        # Two different acts, so two different sentences. With a note, the
        # human raised something; without one, they routed something already
        # raised, and the words on the finding are not theirs.
        if _flag_note(flag, anchored):
            record.disposition_reason = (
                "Raised and routed by the human at gate 2"
                + (f", on {flag.anchor}" if flag.anchor else "")
                + "."
            )
        else:
            record.disposition_reason = (
                "Routed by the human at gate 2 with no note of their own"
                + (f"; the words are {flag.anchor}'s" if flag.anchor else "")
                + "."
            )
    return ids
