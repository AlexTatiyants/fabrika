"""Every finding raised in a build, and what became of it (INV-11).
"""

from __future__ import annotations

import re
from typing import Any, Collection, Sequence

from ..schemas import ArbiterReport, Finding, FindingDisposition, FindingRecord, RepairVerdict

from .oracle_suite import _title_key
from .packet import _severity_rank


def restore_dispositions(
    report: ArbiterReport | None, finding_ids: Sequence[str],
) -> list[FindingDisposition]:
    """INV-11, the arbiter's half: it routes, it cannot drop.

    Anything the arbiter failed to mention defaults to `escalate` -- to a human,
    never to silence. This is the same structural limit the rapporteur has: the
    agent that decides what happens next is the one you can least afford to let
    decide what is seen.
    """
    given = {d.finding_id: d for d in (report.dispositions if report else [])}
    out: list[FindingDisposition] = []
    for fid in finding_ids:
        found = given.get(fid)
        if found is not None:
            out.append(found)
            continue
        out.append(FindingDisposition(
            finding_id=fid,
            disposition="escalate",
            reason=("The arbiter did not route this finding. Unrouted defaults to a human "
                    "ruling, never to dismissal."),
            confidence=0.0,
        ))
    return out


#: What a computed finding can be closed from when its check stops finding it.
#: Not what a person or an agent settled -- a dismissal, a repair, a ruling that
#: it needs the spec -- and not `noted`, which records that something happened.
_CLEARABLE_OUTCOMES = ("open", "escalated", "attempted_not_fixed", "unattempted")


#: Outcomes that a *reading* settled and a re-run of the check may unsettle.
#:
#: Not `no_longer_holds`, which the check itself set and `restate` already
#: reverses, and not `dismissed` or `out_of_spec`, which are rulings about
#: whether the finding should count rather than claims about whether it holds
#: -- a probe going red does not overturn a human deciding the behaviour was
#: never required.
_SETTLED_BUT_CHECKABLE = ("repaired",)


#: The ids each check has always raised, for records written before a record
#: knew its check. Only checks whose ids are theirs alone: `attribution-1` is
#: raised by two different checks, so neither may close it on the other's say.
_LEGACY_CHECK_IDS = {
    "declared_changes": r"spec-\d+",
    "destructive_writes": r"deleted-\d+",
    "blind_suite_loads": r"blind-load-\d+",
    "attribution": r"broke-.+|pre-existing-\d+|unattributed-\d+",
    "oracle_file_failures": r"oracle-files-\d+",
    "blind_helper_errors": r"blind-helper-\d+",
    "setup": r"setup-\d+",
}


class FindingLedger:
    """Every finding ever raised in this build, and what became of it. (INV-11)

    The failure this exists to prevent: a repair loop that turns "six problems
    found, four fixed" into "two problems found". That is INV-2's concern moved
    into time -- a later round laundering an earlier round's disclosure -- and it
    would make the packet less informative while making it look better.

    Nothing here removes. The only mutation a finding undergoes is gaining an
    outcome, and the outcome is computed from what the rounds actually did.
    """

    def __init__(self) -> None:
        self.findings: dict[str, Finding] = {}
        self.records: dict[str, FindingRecord] = {}
        self._by_key: dict[tuple[str, str], str] = {}
        self._counts: dict[str, int] = {}

    # -- accumulation --------------------------------------------------

    def add(
        self, role: str, findings: Sequence[Finding], round_index: int, *,
        keep_ids: bool = False, model: str = "",
    ) -> list[str]:
        """Record findings from one agent in one round. Returns their ledger ids.

        A finding whose title repeats one this agent already raised is the same
        finding seen again, not a new one: it gets the original id and the round
        is added to `rounds_seen`. Re-raising is evidence the repair did not
        land, and it must not inflate the count.
        """
        ids: list[str] = []
        for finding in findings:
            key = (role, _title_key(finding.title))
            existing = self._by_key.get(key)
            if existing is not None:
                record = self.records[existing]
                if round_index not in record.rounds_seen:
                    record.rounds_seen.append(round_index)
                # The harsher reading survives, exactly as within a panel.
                if _severity_rank(finding.severity) > _severity_rank(
                        self.findings[existing].severity):
                    self.findings[existing].severity = finding.severity
                    record.severity = finding.severity
                ids.append(existing)
                continue
            if keep_ids and finding.id and finding.id not in self.findings:
                fid = finding.id
            else:
                self._counts[role] = self._counts.get(role, 0) + 1
                fid = f"{role}-{self._counts[role]}"
            stored = finding.model_copy(deep=True)
            stored.id = fid
            stored.category = stored.category or role
            self.findings[fid] = stored
            self.records[fid] = FindingRecord(
                finding_id=fid, role=role, model=model, title=stored.title,
                severity=stored.severity,
                first_seen_round=round_index, rounds_seen=[round_index],
            )
            self._by_key[key] = fid
            ids.append(fid)
        return ids

    def note(self, role: str, findings: Sequence[Finding], round_index: int) -> None:
        """Record a condition of the run, restated, and never put it to a human.

        Some of what the factory computes about itself is not a defect anybody
        can rule on: which agents the budget could not measure, which route
        carried a role after its own ran out, what the run set over the
        project's own configuration, which files the build wrote that decide
        whether the tests run at all. A human reading those has nothing to
        decide -- accepting or sending back a branch does not change them.

        Filed as `escalate` -- the disposition meaning "a person should see
        this" -- a fact about a rolling window would queue beside a security
        defect and both would count as calls. Seven of one run's seventeen were
        this, and two of the seven were the same fact counted twice.
        """
        self.restate(role, findings, round_index)
        for finding in findings:
            record = self.records.get(finding.id)
            if record is None:
                continue
            # Never over something a repair already settled, and never over a
            # ruling: `noted` describes where a finding sits, and a finding
            # that has been acted on sits somewhere else.
            if record.outcome in ("open", "noted"):
                record.outcome = "noted"
                record.disposition = record.disposition or "not_a_defect"
                record.disposition_reason = (
                    record.disposition_reason
                    or "a condition of this run rather than a defect in the work: "
                       "reported for reading, with nothing for a human to decide")

    def id_of(self, role: str, title: str) -> str:
        """The ledger id this role's finding with this title already carries.

        A caller that knows a check looked at something, and needs the record
        for it whether or not the check found anything this time. Titles are
        the ledger's identity for a role's findings -- that is how re-raising
        in a later round updates a record instead of creating a second -- so
        this is the same lookup `add` does, exposed.

        It has to be a lookup rather than recomputing the id, because a record
        written under an earlier id scheme keeps the id it was created with:
        the title matches, so `add` hands it back. Predicting the id would
        miss exactly those.
        """
        return self._by_key.get((role, _title_key(title)), "")

    def restate(
        self, role: str, findings: Sequence[Finding], round_index: int, *,
        source: str = "", fresh: bool = True, among: Collection[str] | None = None,
        reopen_settled: bool = False,
    ) -> None:
        """Update a finding already in the ledger, rather than raising a second.

        For the computed checks that describe the *run* rather than the code.
        Those are snapshotted the first time they are asked, and the run keeps
        happening: the fallback check ran in round 0, when one role had run out
        of its route, and the packet went to a human saying "1 agent(s) ran on
        a fallback route" after four of them had -- twenty-five calls, the
        whole verify lane, on a substitute model. Re-adding cannot fix it,
        because the count is in the title and a new title is a new finding.

        `findings` is everything the check `source` finds now, so a finding it
        raised before and does not find now no longer holds, and is closed with
        that as its evidence. Otherwise a blind-test helper error raised in
        round 0 ships as a blocker after every blind test has passed for two
        rounds, because a check that came back empty changes nothing. Closed
        only when `fresh`: a check that did not get new evidence this round has
        not looked again, and an empty answer from it is not an answer. A
        finding that comes back is reopened.

        `among` narrows what an empty answer is allowed to close, for a check
        that does not look at everything it has ever raised. The breaker is
        the case: a demonstration probe is not re-run after its round, so its
        finding is absent from `findings` because nothing asked, not because
        the answer changed. Closing it would put "the check ran again and no
        longer finds it" on a record where no check ran -- a sentence a human
        would read as evidence. Only records named here may be closed; the
        rest keep whatever they had.

        `reopen_settled` is for a check whose result outranks a reading of it.
        A finding is normally reopened only from `no_longer_holds`, because
        `repaired` is a conclusion somebody reached and the record only grows.
        An executable check disagreeing with that conclusion is the one case
        where the record must correct itself instead: on one run two
        findings sat at `repaired` while the probes that define them were
        failing in the same round, and the packet asserted both at once.
        """
        for finding in findings:
            existing = self.findings.get(finding.id)
            if existing is None:
                self.add(role, [finding], round_index, keep_ids=True)
                if source and finding.id in self.records:
                    self.records[finding.id].source = source
                continue
            stale = (role, _title_key(existing.title))
            if self._by_key.get(stale) == finding.id:
                del self._by_key[stale]
            for field in ("title", "detail", "evidence", "recommendation", "severity"):
                setattr(existing, field, getattr(finding, field))
            record = self.records[finding.id]
            record.title, record.severity = existing.title, existing.severity
            if round_index not in record.rounds_seen:
                record.rounds_seen.append(round_index)
            if source:
                record.source = source
            if record.outcome == "no_longer_holds":
                record.outcome, record.outcome_evidence = "open", (
                    f"Found again in round {round_index}, after it had stopped holding.")
            elif reopen_settled and record.outcome in _SETTLED_BUT_CHECKABLE:
                was = record.outcome
                record.outcome, record.outcome_evidence = "open", (
                    f"Reported {was} and found again in round {round_index} by the check that "
                    f"raised it ({source}). The check is the one that can be wrong about this "
                    "by being run; the report of a fix is not, so the check is what stands.")
                record.repaired_in_round, record.commit = None, ''

            self._by_key[(role, _title_key(existing.title))] = finding.id

        if not source or not fresh:
            return
        now = {f.id for f in findings}
        legacy = _LEGACY_CHECK_IDS.get(source)
        for fid, record in self.records.items():
            mine = record.source == source or (
                not record.source and record.role == role and legacy is not None
                and re.fullmatch(legacy, fid) is not None)
            if among is not None and fid not in among:
                continue
            if not mine or fid in now or record.outcome not in _CLEARABLE_OUTCOMES:
                continue
            record.source = source
            record.outcome = "no_longer_holds"
            record.outcome_evidence = (
                f"The check that raised this ({source}) ran again in round {round_index} "
                "on fresh evidence and no longer finds it.")

    # -- routing -------------------------------------------------------

    def dispose(self, dispositions: Sequence[FindingDisposition]) -> None:
        for d in dispositions:
            record = self.records.get(d.finding_id)
            if record is None:
                continue
            # A finding already settled by a repair is not re-routed: the
            # arbiter rules on what is open, not on history.
            if record.outcome in ("repaired", "attempted_not_fixed"):
                continue
            # Nor does it re-route what a human raised. The arbiter decides
            # where a model's finding goes; a person's finding arrives already
            # decided, and a model that could overturn that would be the tool
            # overruling its user.
            #
            # One exception, and it is not a re-ruling. WHICH AGENT fixes a
            # repair is arithmetic about who owns the file, not a judgment
            # about whether the thing is a defect -- and a person cannot be
            # asked to know that the blind tests are the one tree a repairer
            # may not write in. "This assertion is backwards" filed as
            # `repair` would go to an agent forbidden to touch the file, and a
            # review's clearest instruction would come back untouched. So the
            # arbiter may move a human's repair to the oracle, and may do
            # nothing else with it: every other answer leaves the flag as
            # filed, including a dismissal, a duplicate and an escalation.
            if record.role == "human":
                if record.disposition == "repair" and d.disposition == "oracle":
                    record.disposition = "oracle"
                    record.disposition_reason = (
                        "The file that has to change is one only the oracle may write, so "
                        f"the oracle makes the change. {d.reason}".strip())
                continue
            record.disposition = d.disposition
            record.disposition_reason = d.reason
            self._mark_duplicate(d.finding_id, d.duplicate_of)

    def _mark_duplicate(self, finding_id: str, of: str) -> None:
        """Tie a finding to the one it restates, if the arbiter named one.

        Nothing is removed and no text is edited. The duplicate keeps its own
        record, its own words and its own place in the packet; what changes is
        that it is no longer routed, attempted or re-checked on its own, and it
        takes the outcome of whatever resolves the finding it duplicates.

        Six agents converging on one defect is evidence, and the arbiter leans
        on it -- it chose `repair` over `escalate` precisely because three
        adversarial probes and two hacker probes agreed. That is why the
        corroboration is recorded on the survivor rather than thrown away with
        the duplicates.
        """
        root = self._root_of(of)
        if not root or root == finding_id:
            return
        record = self.records.get(finding_id)
        primary = self.records.get(root)
        if record is None or primary is None or record.duplicate_of:
            return
        record.duplicate_of = root
        # A try at the duplicate was a try at the defect. Otherwise a finding
        # attempted in round 2 and only then called a restatement of one never
        # routed leaves the root at zero attempts, and the packet says the
        # defect was never attempted while its own record shows the repair
        # that failed.
        primary.attempts = max(primary.attempts, record.attempts)
        # Which of the two lists it joins is the whole point. A different agent
        # reaching the same defect is evidence; the same agent reaching it
        # again in another sample is the sampling working, which is a fact
        # about the panel rather than about the code.
        #
        # Same agent means same model, and the role does not enter it. Two
        # roles on one model are not two opinions -- that is why the reviewer
        # and the adversary are one agent, rather than two on one vendor with
        # the roster promising two readings.
        #
        # It works because only an *opinion* carries a model. A finding
        # computed from a process -- a probe's exit code, a gate that used to
        # pass -- is stamped with no model at all, because it is not a reading
        # and cannot be the same reading as anything. So a probe that failed
        # and a reader that found the same defect stay two pieces of evidence
        # even when the same vendor wrote both the probe and the prose, which
        # is right: one of them was executed.
        #
        # A record written before models were recorded has none either, and
        # two blanks are not a match. An unknown is not evidence of sameness
        # any more than of difference, and the conservative reading is the one
        # that does not manufacture agreement out of a missing field.
        same_agent = bool(record.model) and record.model == primary.model
        target = primary.restated_by if same_agent else primary.corroborated_by
        if finding_id not in target:
            target.append(finding_id)
        # The harshest reading survives on the finding that carries the fix.
        if _severity_rank(record.severity) > _severity_rank(primary.severity):
            primary.severity = record.severity
            self.findings[root].severity = record.severity

    def _root_of(self, finding_id: str) -> str:
        """Follow a chain of duplicates to the finding that actually carries it.

        A says it duplicates B, B says it duplicates C. Without this, A is tied
        to a record that is itself never routed and nothing ever settles it. The
        walk is bounded because a cycle would otherwise hang the loop on a model
        having pointed two findings at each other.
        """
        seen: set[str] = set()
        current = finding_id
        while current and current in self.records and current not in seen:
            seen.add(current)
            nxt = self.records[current].duplicate_of
            if not nxt:
                return current
            current = nxt
        return "" if current in seen else current

    def settle_duplicates(self) -> None:
        """Give every duplicate the outcome of the finding it duplicates.

        Called after a round's verdicts land. Without it a duplicate sits at
        `open` forever -- never routed, so never repaired -- and the packet
        reports as unresolved the very defects the loop just fixed.
        """
        for fid, record in self.records.items():
            # Through to the root: a restatement marked before the finding it
            # restated was itself called a duplicate points at a middle link,
            # which is never settled on its own.
            root = self._root_of(fid) if record.duplicate_of else ""
            primary = self.records.get(root) if root and root != fid else None
            if primary is None or primary.outcome == "open":
                continue
            record.outcome = primary.outcome
            record.repaired_in_round = primary.repaired_in_round
            record.commit = primary.commit
            said = ("resolved with {root}, which says the same thing: "
                    if primary.outcome == "repaired" else
                    "the same defect as {root}, and settled with it: ").format(root=root)
            record.outcome_evidence = f"{said}{primary.outcome_evidence}".strip()

    def repairable(self, max_attempts: int) -> list[str]:
        """Open, routed to repair, not a restatement, and not tried too often.

        A duplicate is excluded because the fix is the same fix. Routing six
        findings about one defect to six repair units is how a round comes to
        report fourteen repairs over four distinct changes, and it spends the
        arbiter, the repairers and the re-check on the arithmetic of that.
        """
        return [
            fid for fid, r in self.records.items()
            if r.disposition == "repair"
            and r.outcome == "open"
            and not r.duplicate_of
            and r.attempts < max_attempts
        ]

    def human_first(self, ids: Sequence[str]) -> list[str]:
        """This round's targets, worst first, with a person's at the top.

        An ORDER, not a handover. Handing the round the human's targets and
        their same-file neighbours and nothing else turns priority into
        exclusivity: when two of three human targets cannot be fixed that
        round, a round with four unit-slots runs one, and a NUL byte crashing
        the API that seven agents found independently is never offered to
        anybody.

        Priority decides who goes first. It must never decide how much of the
        round goes unused. Whoever plans the round takes from the top of this
        list and fills it.

        After a person's own flags: severity, then how many independent
        readings found the same thing, then the id so the order never depends
        on what the ledger happened to list first.
        """
        rank = {"blocker": 0, "major": 1, "minor": 2, "nit": 3}

        def key(fid: str) -> tuple[int, int, int, str]:
            record = self.records.get(fid)
            if record is None:
                return (1, 9, 0, fid)
            return (
                0 if record.role == "human" else 1,
                rank.get(record.severity, 9),
                # Only the half that is evidence. Ranking on restatements
                # would sort by how many ways one agent happened to word itself.
                -len(record.corroborated_by or []),
                fid,
            )

        return sorted(ids, key=key)

    def for_oracle(self, max_attempts: int) -> list[str]:
        """Open, routed to the oracle, not a restatement, and not tried too often.

        The oracle's files are the one place a worker may not write, so a
        finding about how one is written goes to the agent that wrote it.
        """
        return [
            fid for fid, r in self.records.items()
            if r.disposition == "oracle"
            and r.outcome == "open"
            and not r.duplicate_of
            and r.attempts < max_attempts
        ]

    def simplifiable(self) -> list[str]:
        """Open, routed to `simplify`, and not a restatement of an earlier one.

        No attempt cap, because there is only ever one attempt: the pass runs
        once, after the repair loop has converged, and a finding it does not
        close stays open in front of a human like any other.
        """
        return [
            fid for fid, r in self.records.items()
            if r.disposition == "simplify"
            and r.outcome == "open"
            and not r.duplicate_of
        ]

    def attempted(self, ids: Sequence[str], round_index: int) -> None:
        for fid in ids:
            record = self.records.get(fid)
            if record is not None:
                record.attempts += 1

    def not_attempted(self, ids: Sequence[str], reason: str) -> None:
        """Give back an attempt that nothing was spent on.

        An attempt is a claim that somebody tried, and after enough of them a
        finding retires as `attempted_not_fixed` -- the packet's phrase for "we
        tried and could not". When the coding harness produced no edits at all,
        nobody tried, and charging the finding for it retires a real defect on
        the strength of a tool that was broken. The attempt is returned so the
        next round can make it for real.
        """
        for fid in ids:
            record = self.records.get(fid)
            if record is None or record.outcome != "open":
                continue
            record.attempts = max(0, record.attempts - 1)
            record.outcome_evidence = reason

    def route_to_person(self, ids: Sequence[str], reason: str) -> list[str]:
        """Send findings no repair can act on to a human, before anyone tries.

        Not a failed repair: nobody attempted it, so it costs no attempt and
        is not `attempted_not_fixed`. It settles as escalated, with why.
        """
        moved: list[str] = []
        for fid in ids:
            record = self.records.get(fid)
            if record is None or record.outcome != "open":
                continue
            record.disposition = "escalate"
            record.disposition_reason = reason
            record.outcome_evidence = reason
            moved.append(fid)
        return moved

    def apply_verdicts(
        self, verdicts: Sequence[RepairVerdict], round_index: int, commit: str,
        max_attempts: int,
    ) -> None:
        """What the originating agent said about its own finding after a repair."""
        {v.finding_id for v in verdicts}
        for v in verdicts:
            record = self.records.get(v.finding_id)
            if record is None:
                continue
            if v.status in ("fixed", "fixed_with_new_problem"):
                record.outcome = "repaired"
                record.repaired_in_round = round_index
                record.commit = commit
                record.outcome_evidence = v.evidence
            else:
                record.outcome_evidence = v.evidence
                if record.attempts >= max_attempts:
                    record.outcome = "attempted_not_fixed"
        return None

    def revert_round(self, round_index: int, reason: str) -> None:
        """A round whose commits were thrown away repaired nothing.

        The re-check runs before the next round's gates, so a repair can be
        marked fixed and only then turn out to have made the work worse. When
        the commit goes, the claim has to go with it -- otherwise the packet
        says a finding was repaired in a round that is no longer on the branch,
        which is the most misleading thing this record could say.
        """
        for record in self.records.values():
            if record.repaired_in_round == round_index:
                record.outcome = "open"
                record.repaired_in_round = None
                record.commit = ""
                record.outcome_evidence = (
                    f"Reported fixed in round {round_index}, but that round was reverted: {reason}"
                )

    def exhaust_attempts(self, max_attempts: int) -> None:
        """A finding that has used its attempts and is still open is settled:
        attempted, not fixed. That is a better thing to hand a human than a
        third try at the same wrong idea."""
        for record in self.records.values():
            if (record.disposition in ("repair", "oracle") and record.outcome == "open"
                    and record.attempts >= max_attempts):
                record.outcome = "attempted_not_fixed"

    def finalise(self, stopped_early: bool) -> None:
        """Turn every remaining routing into an outcome. Runs once, at the end."""
        settle = {
            "escalate": "escalated",
            "needs_spec_change": "needs_spec_change",
            "out_of_spec": "out_of_spec",
            "not_a_defect": "dismissed",
            "dismissed": "dismissed",
        }
        for fid, record in self.records.items():
            if record.outcome != "open":
                continue
            if record.duplicate_of and self._root_of(fid) not in ("", fid):
                continue          # takes its root's outcome, below
            if record.disposition in ("repair", "oracle"):
                if record.attempts:
                    record.outcome = "attempted_not_fixed"
                else:
                    record.outcome = "unattempted"
                    if not record.outcome_evidence and stopped_early:
                        record.outcome_evidence = (
                            "Routed for repair and never attempted: the loop stopped first.")
                continue
            record.outcome = settle.get(record.disposition, "escalated")
        # Last, once every root has an outcome. Settled one by one, a
        # restatement of a repair that was tried and failed would read "never
        # attempted: the loop stopped first" beside the record of the attempt.
        self.settle_duplicates()

    # -- reading -------------------------------------------------------

    def all_findings(self) -> list[Finding]:
        return [self.findings[fid] for fid in self.findings]

    def all_records(self) -> list[FindingRecord]:
        order = {"blocker": 0, "major": 1, "minor": 2, "nit": 3}
        return sorted(
            self.records.values(),
            key=lambda r: (order.get(r.severity, 9), r.finding_id),
        )

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for record in self.records.values():
            out[record.outcome] = out.get(record.outcome, 0) + 1
        return out

    def dump(self) -> dict[str, Any]:
        return {
            "findings": [f.model_dump(mode="json") for f in self.findings.values()],
            "records": [r.model_dump(mode="json") for r in self.records.values()],
            "counts": self._counts,
        }

    @classmethod
    def load(cls, payload: dict[str, Any]) -> "FindingLedger":
        ledger = cls()
        for raw in payload.get("findings") or []:
            finding = Finding.model_validate(raw)
            ledger.findings[finding.id] = finding
        for raw in payload.get("records") or []:
            record = FindingRecord.model_validate(raw)
            ledger.records[record.finding_id] = record
            ledger._by_key[(record.role, _title_key(record.title))] = record.finding_id
        ledger._counts = dict(payload.get("counts") or {})
        return ledger


#: A reviewer's finding that a failing blind test is itself wrong.
SUSPECT_TEST = "suspect_test"


def suspect_tests_to_a_person(
    dispositions: Sequence[FindingDisposition], ledger: "FindingLedger",
) -> list[FindingDisposition]:
    """Escalate every `suspect_test` finding an agent raised, whatever it was routed.

    A person's finding is left alone: a person sending back a test's assertion
    is the one ruling that may reach the oracle (see the arbiter's contract).
    """
    out: list[FindingDisposition] = []
    for d in dispositions:
        finding = ledger.findings.get(d.finding_id)
        record = ledger.records.get(d.finding_id)
        if (finding is not None and (finding.category or "").strip() == SUSPECT_TEST
                and d.disposition != "escalate"
                and (record is None or record.role != "human")):
            d = d.model_copy(update={
                "disposition": "escalate",
                "reason": (d.reason + " [Sent to you: whether a blind test is wrong is a "
                           "ruling on the contract, and only a person makes it. Send it "
                           "back to have the oracle revise the test.]").strip(),
            })
        out.append(d)
    return out


def _agent_of(record: FindingRecord) -> str:
    """Who raised a finding, said so that two of them can be compared.

    The role alone cannot answer "is this the same reader as that one". Two
    roles on one model are not two opinions -- the reason the reviewer and the
    adversary are one agent -- and one role sampled three times is not three.
    """
    return f"{record.role} ({record.model})" if record.model else record.role
