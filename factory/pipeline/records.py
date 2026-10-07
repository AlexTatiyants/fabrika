"""Reading the evidence ledger: the current record of a kind, and what an
earlier attempt at the same spec already left behind.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..schemas import Spec


def current_record(
    records: Sequence[dict[str, Any]],
    kind: str,
    invalidated_by: Sequence[str] = ("correction",),
) -> dict[str, Any] | None:
    """The latest record of `kind`, unless the human invalidated it afterwards.

    The ledger is append-only, so a superseded spec is still the last `spec`
    record on disk and `latest()` will hand it back forever. A correction says
    the reading everything downstream was built on was wrong; the spec and the
    answers to the old questions are void from that moment, but nothing deletes
    them and nothing should. Currency is a question about ordering, so it is
    answered here by sequence number rather than by trusting recency.
    """
    cutoff = max((r["seq"] for r in records if r.get("kind") in invalidated_by), default=-1)
    found = None
    for r in records:
        if r.get("kind") == kind and r["seq"] > cutoff:
            found = r
    return found


def current_payload(
    records: Sequence[dict[str, Any]],
    kind: str,
    invalidated_by: Sequence[str] = ("correction",),
) -> Any:
    """The payload half of `current_record` -- most callers only want that."""
    found = current_record(records, kind, invalidated_by)
    return found["payload"] if found else None


# What a migration or a router says when it does the thing, in the dialects this
# tool is likely to meet. Crude on purpose -- it is looking for the verb, not
# parsing the code.
_DID = {
    "add": ("add_column", "add column", "create_table", "create table", "addcolumn"),
    "drop": ("drop_column", "drop column", "drop_table", "drop table", "dropcolumn"),
    "remove": ("delete", "remove", "deprecat"),
}


def irreversible_changes(spec: Spec) -> list[str]:
    """Which declared changes cannot be undone by editing code afterwards.

    Derived, never asked for. A dropped column is irreversible by definition and
    a model's opinion on that adds nothing -- the same reason traceability is
    computed. It is the one thing on this page that should be loud, not a thin
    red minus three sections above a summary that never mentions it.
    """
    if spec.changes is None:
        return []
    out = []
    for d in spec.changes.data:
        if d.operation == "drop":
            out.append(f"{d.table}.{d.column}" if d.column else d.table)
    for i in spec.changes.interface:
        if i.operation == "remove":
            out.append(f"{i.method} {i.path}")
    for x in spec.changes.surfaces:
        if x.operation == "remove":
            out.append(x.where)
    return out


def promoted_this_pass(records: Sequence[Mapping[str, Any]]) -> list[str]:
    """The probes this pass's breaker last kept.

    This pass's only. A revalidation starts the breaker over on a branch that
    keeps every commit, so a probe the last pass kept is on the branch and is
    not this suite's to drop: counted as earlier, it would be reported "no
    longer kept" while the branch goes on carrying it, because a removal nobody
    made has nothing to commit. A rebuild resets the branch, which takes the old
    probes with it.
    """
    floor = max((r["seq"] for r in records
                 if r.get("kind") in ("revalidate", "rebuild")), default=0)
    earlier: list[str] = []
    for record in records:
        payload = record.get("payload")
        if (record.get("kind") == "breaker" and record["seq"] > floor
                and isinstance(payload, dict)):
            earlier = list(payload.get("promoted") or []) or earlier
    return earlier


def resume_round(records: Sequence[dict[str, Any]], saved: int) -> int:
    """Which repair round a resumed run starts at.

    The cap exists to stop the loop spending round after round on its own.
    A person reading a packet, flagging findings and sending them in is not
    that: it is a new instruction, and the work they asked for should happen.
    So a dispatch made since the last recorded state starts the allowance
    again, and the budget -- counted over the feature's whole life -- is what
    bounds the total.

    Measured: a feature that had used both its rounds was dispatched with
    three findings flagged for repair. The loop resumed at round 2, hit the
    cap before it began, and returned the same packet with "3 finding(s) left
    unattempted" after paying for a full assessment.
    """
    last = max((r["seq"] for r in records if r.get("kind") == "rework_state"), default=0)
    sent = max((r["seq"] for r in records if r.get("kind") == "dispatch"), default=0)
    return 0 if sent > last else saved


def recorded_attempt(
    records: Sequence[dict[str, Any]], spec_hash: str,
) -> dict[str, Any]:
    """What a previous attempt at *this* spec already produced.

    A build is expensive in a way the rest of the pipeline is not: two coding
    harness runs, an oracle, gates in a container, and a review panel sampled
    several times over. When the last phase fails there is no reason to buy all
    of that again, and every artifact is already on disk.

    Two conditions make reuse safe, and both are answered by data the ledger
    already holds. The record must carry the current `spec_hash`, so a spec that
    was re-planned invalidates everything built against the old one. And it must
    come after the `plan` that this attempt used, so artifacts from an earlier
    attempt at the same spec are not mixed with this one's.
    """
    if not spec_hash:
        return {}
    plans = [r for r in records
             if r.get("kind") == "plan" and r.get("spec_hash") == spec_hash]
    if not plans:
        return {}
    start = plans[-1]["seq"]
    # A revalidation keeps the build and discards the verdict pass. The code on
    # the branch is still what the build lane produced -- that is why this is
    # worth doing rather than rebuilding -- but the gates, the panel and the
    # finding ledger were produced by a harness whose measurements were wrong,
    # and reusing them would carry that forward into the run meant to replace
    # it. So the floor moves for those artifacts and for nothing else.
    revalidations = [r for r in records
                     if r.get("kind") == "revalidate" and r["seq"] > start]
    verdict_floor = revalidations[-1]["seq"] if revalidations else start

    def latest(kind: str, since: int | None = None) -> Any:
        floor = start if since is None else since
        found = [r for r in records if r.get("kind") == kind
                 and r["seq"] >= floor and r.get("spec_hash") == spec_hash]
        return found[-1]["payload"] if found else None

    def every(kind: str) -> list[Any]:
        return [r["payload"] for r in records if r.get("kind") == kind
                and r["seq"] > start and r.get("spec_hash") == spec_hash]

    out: dict[str, Any] = {"plan": plans[-1]["payload"]}
    for kind, key in (("oracle", "oracle"), ("integration", "integration"),
                      ("writes", "writes")):
        value = latest(kind)
        if value is not None:
            out[key] = value
    gates = latest("gates", verdict_floor)
    if gates is not None:
        out["gates"] = gates
    workers = every("worker")
    if workers:
        out["workers"] = workers
    # One entry per agent, not one per round: the review phase runs more
    # than once, and reusing every panel would hand the resumed build the same
    # agent's findings several times over.
    by_role: dict[str, Any] = {}
    for r in records:
        if (r.get("kind") == "review" and r["seq"] > verdict_floor
                and r.get("spec_hash") == spec_hash):
            by_role[r["role"]] = r["payload"]
    if by_role:
        out["reviews"] = list(by_role.items())
    # What the repair loop already knows: which findings exist and what became
    # of them. Without this a resumed build starts the ledger empty and every
    # finding the last attempt repaired comes back as open.
    rework_state = latest("rework_state", verdict_floor)
    if rework_state is not None:
        out["rework_state"] = rework_state
    return out


def guides_held(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The guides this feature was held to, from what each agent was handed.

    Read off the record, so the packet says what agents actually received --
    and whether any had only part of a guide. For a harness worker the record
    is what its loader had available, not what it read: only the harness
    knows that, and the entry says `loader` so nobody reads it as more.
    """
    held: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("kind") != "guides_delivered":
            continue
        payload = record.get("payload") or {}
        for g in payload.get("guides") or []:
            entry = held.setdefault(g["path"], {"path": g["path"], "sha256": g.get("sha256", ""),
                                                "partial": False, "roles": [], "loader": []})
            entry["partial"] = entry["partial"] or bool(g.get("partial"))
            role = payload.get("role")
            bucket = entry["loader"] if payload.get("harness") else entry["roles"]
            if role and role not in bucket:
                bucket.append(role)
    return sorted(held.values(), key=lambda e: e["path"])
