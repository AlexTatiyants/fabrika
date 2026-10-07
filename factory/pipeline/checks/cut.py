"""The cut: whether units can check their own work, the objections raised
against a spec or a plan, and a person's ruling on it.
"""

from __future__ import annotations

import re
import sys
from typing import Any, Callable, Collection, Iterable, Sequence

from ...schemas import Finding, Plan, Spec, WorkUnit, CheckObjection, ObjectionAnswer
from ...store import EvidenceStore
from ...workspace import spec_text

from ..flags import _one_line
from ..records import irreversible_changes


#: A dotted code path -- `app.services.screening_capacity.capacity_block`. Two
#: segments minimum, because one bare word is as likely to be a field name the
#: spec pinned as a module another unit is writing, and the whole value of this
#: check is that it does not cry wolf.
_UNIT_SYMBOL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")


def _unit_symbols(entries: Sequence[str]) -> set[str]:
    out: set[str] = set()
    for entry in entries or ():
        out |= set(_UNIT_SYMBOL.findall(str(entry or "")))
    return out


def unit_dependencies(
    plan: Plan, spec: Spec, runtime: Sequence[str] = (),
) -> list[tuple[str, str, str]]:
    """(consumer, provider, symbol) for each unit that needs another unit's code.

    The architect is told to cut so that no unit depends on another's behaviour,
    because a unit that does cannot run the tests it writes: the thing it calls
    is in a worktree it cannot see. That instruction is a prompt, and prompts get
    ignored, so this measures the cut instead of trusting it.

    Two subtractions keep it quiet enough to be worth reading.

    A symbol the **spec** contains is not a dependency between units, it is two
    units reading the same frozen document -- which is the distinction the
    architect's own rule draws, and the reason a frontend unit consuming a
    response key the acceptance criteria spell out is a legitimate cut.

    A symbol rooted in the **standard library or an installed package** is not
    this project's code at all. `uuid.UUID` appearing in one unit's `requires`
    and another's `provides` says they both handle ids. Both lists are facts --
    `sys.stdlib_module_names` from the interpreter, `runtime_packages` measured
    at gate 0 -- rather than a table of names somebody maintained.
    """
    known = {str(name).split(".")[0].split("[")[0].strip().lower()
             for name in runtime} | set(sys.stdlib_module_names)
    frozen = spec_text(spec)
    provided = {u.id: _unit_symbols(u.provides) for u in plan.units}
    found: list[tuple[str, str, str]] = []
    for unit in plan.units:
        for symbol in sorted(_unit_symbols(unit.requires)):
            if symbol.split(".")[0].lower() in known or symbol in frozen:
                continue
            for other in plan.units:
                if other.id != unit.id and symbol in provided[other.id]:
                    found.append((unit.id, other.id, symbol))
    return found


def check_unit_independence(
    plan: Plan, spec: Spec, runtime: Sequence[str] = (),
) -> list[Finding]:
    """The cut that decides whether a worker can check its own work.

    Measured: a spec was cut into a data layer, an API layer and a
    frontend. The API unit required three of the data unit's function
    signatures, could not execute a single test it wrote -- the module it called
    did not exist in its worktree -- and shipped a list of assumptions instead.
    Three were wrong, and the packet came back with nine blockers.

    Reported rather than repaired. Nothing here reorders the units or defers
    one: a cut is a judgement, this run is already built on it, and the useful
    moment for the fact is the next spec rather than this one. What it must not
    do is stay quiet, because the consequence arrives disguised as a worker that
    wrote weak tests.
    """
    edges = unit_dependencies(plan, spec, runtime)
    if not edges:
        return []
    by_consumer: dict[str, list[tuple[str, str]]] = {}
    for consumer, provider, symbol in edges:
        by_consumer.setdefault(consumer, []).append((provider, symbol))
    listed = "; ".join(
        f"{consumer} needs " + ", ".join(f"`{sym}` from {prov}" for prov, sym in pairs)
        for consumer, pairs in by_consumer.items())
    return [Finding(
        id="cut-1",
        title="Parts of this were built in parallel against a connection the spec never defined",
        severity="major",
        category="verification",
        detail=(
            "These units name symbols another unit was writing at the same time, and "
            "the spec does not pin them -- so they are this plan's invention rather "
            "than a contract both sides read.\n\n"
            "A worker in that position cannot execute what it writes. The module it "
            "calls is in a worktree it cannot see, so its tests are unrunnable by "
            "construction and its confidence in them is an assumption. Whatever those "
            "units disclosed about their own testing, read it in that light.\n\n"
            "Nothing here says the code is wrong. It says the cut made it uncheckable "
            "at the moment it was written."
        ),
        evidence=listed,
        recommendation=(
            "For the next spec: cut by behaviour rather than by layer, so each unit "
            "owns a thing the feature does from the column to the endpoint. Where that "
            "collides with non-overlapping file ownership, merge the units -- one unit "
            "is a valid plan. A seam is safe when the frozen spec pins it and unsafe "
            "when this plan invented it."
        ),
    )]


# -- the checkers ------------------------------------------------------------
#
# Each checker reads a document as the agent that will be stuck with it: the
# spec checker as the oracle, the plan checker as each worker. The author then
# answers every objection once, and code -- not either model -- decides what is
# settled. A model can say "revised"; only a changed document makes it true.

CHECK_LIMIT = 5


def anchored_objections(
    objections: Sequence[CheckObjection], *, prefix: str, start: int = 1,
    units: Collection[str] = (), criteria: Collection[str] = (),
    questions: Collection[str] = (), limit: int = CHECK_LIMIT,
) -> list[CheckObjection]:
    """The objections that name something real, capped, and renumbered.

    An objection naming no unit, criterion or answer that exists cannot be
    answered by the author and cannot later be matched against what broke, so
    it is dropped rather than carried as noise. The cap is applied in the order
    given, which the checker is told is most consequential first. Ids are the
    orchestrator's, so the answer round and the ledger agree on them whatever
    the model numbered.
    """
    out: list[CheckObjection] = []
    for raw in objections:
        o = raw.model_copy(deep=True)
        o.unit_ids = [u for u in o.unit_ids if u in units]
        o.criterion_ids = [c for c in o.criterion_ids if c in criteria]
        o.question_ids = [q for q in o.question_ids if q in questions]
        if not (o.unit_ids or o.criterion_ids or o.question_ids) or not o.claim.strip():
            continue
        out.append(o)
        if len(out) >= limit:
            break
    for i, o in enumerate(out, start=start):
        o.id = f"{prefix}-{i}"
    return out


def settle_objections(
    objections: Sequence[CheckObjection],
    answers: Sequence[ObjectionAnswer],
    changed: Callable[[CheckObjection], bool],
) -> list[dict[str, Any]]:
    """Each objection with the author's answer and whether it is settled.

    Settled means the author said `revised` *and* something the objection named
    is different in the revised document. A `revised` that changed nothing is a
    claim that did not happen, and stays open with that said. A rebuttal stays
    open: two agents disagree, and that is exactly what a person rules on.
    """
    by_id = {a.objection_id: a for a in answers}
    out: list[dict[str, Any]] = []
    for o in objections:
        a = by_id.get(o.id)
        if a is None:
            settled, why = False, "not answered"
        elif a.answer == "rebutted":
            settled, why = False, "rebutted"
        elif changed(o):
            settled, why = True, "revised"
        else:
            settled, why = False, "said revised, but nothing it names changed"
        out.append({
            "objection": o.model_dump(mode="json"),
            "answer": a.model_dump(mode="json") if a else None,
            "settled": settled,
            "why": why,
        })
    return out


def objection_block(objections: Sequence[CheckObjection]) -> str:
    return "\n\n".join(
        f"## {o.id} ({o.kind})"
        + (f"\nUnits: {', '.join(o.unit_ids)}" if o.unit_ids else "")
        + (f"\nCriteria: {', '.join(o.criterion_ids)}" if o.criterion_ids else "")
        + (f"\nAnswers: {', '.join(o.question_ids)}" if o.question_ids else "")
        + f"\n\n{o.claim}\n\nConsequence: {o.consequence}"
        + (f"\n\nWould be settled by: {o.settled_by}" if o.settled_by else "")
        for o in objections)


def criteria_changed(before: Spec, after: Spec, objection: CheckObjection) -> bool:
    """Did the spec move where the objection pointed.

    A named criterion that is gone, merged away or reworded counts. An
    objection about an answer no criterion covers names no criterion, so any
    change to the spec's text counts for it -- the fix is a criterion or a
    constraint that did not exist before.
    """
    if not objection.criterion_ids:
        return spec_text(before) != spec_text(after)
    old = {c.id: c.model_dump(mode="json") for c in before.acceptance_criteria}
    new = {c.id: c.model_dump(mode="json") for c in after.acceptance_criteria}
    return any(old.get(cid) != new.get(cid) for cid in objection.criterion_ids)


def units_changed(before: Plan, after: Plan, objection: CheckObjection) -> bool:
    """Did the plan move where the objection pointed. A unit that is gone --
    merged into another -- counts; so does one whose contract or reading list
    moved. A `revised` that left every named unit byte-identical does not. An
    objection naming only criteria is about where they sit, so any change to the
    plan counts for it."""
    if not objection.unit_ids:
        return before.model_dump(mode="json") != after.model_dump(mode="json")
    old = {u.id: u.model_dump(mode="json") for u in before.units}
    new = {u.id: u.model_dump(mode="json") for u in after.units}
    return any(old.get(uid) != new.get(uid) for uid in objection.unit_ids)


def cut_facts(plan: Plan, spec: Spec, runtime: Sequence[str] = ()) -> list[str]:
    """What is measurably wrong with a cut, before anyone builds on it.

    Facts rather than opinions, so no answer from the architect can rebut them.
    Without this each one arrives only in the packet, after the workers are
    paid: `check_unit_independence` says in its own words that "the useful
    moment for the fact is the next spec". This is that fact, at the moment it
    is useful.
    """
    out: list[str] = []
    for consumer, provider, symbol in unit_dependencies(plan, spec, runtime):
        out.append(f"{consumer} needs `{symbol}` from {provider}, and the spec does not "
                   "pin it: the two are built at the same time and neither can see the other.")
    owners: dict[str, list[str]] = {}
    for unit in plan.units:
        for path in unit.files_expected:
            owners.setdefault(path, []).append(unit.id)
    for path, ids in owners.items():
        if len(ids) > 1:
            out.append(f"`{path}` is written by {' and '.join(ids)}: a merge conflict "
                       "scheduled in advance.")
    for criterion in spec.acceptance_criteria:
        holders = [u.id for u in plan.units if criterion.id in u.criterion_ids]
        if not holders:
            out.append(f"{criterion.id} is in no unit, so nothing will build it.")
        elif len(holders) > 1:
            out.append(f"{criterion.id} is in {' and '.join(holders)}: two half-implementations.")
    permanent = irreversible_changes(spec)
    if permanent and len(plan.units) > 1 and plan.seams:
        out.append(
            f"This change cannot be undone ({', '.join(permanent)}), and it is split across "
            f"{len(plan.units)} units that must agree at {len(plan.seams)} seam(s). A seam "
            "that is wrong there cannot be repaired afterwards.")
    return out


def merge_units(plan: Plan) -> Plan:
    """The whole feature as one unit. Arithmetic, not an agent.

    Always a valid plan -- the architect's own rules say so -- which is what makes
    "build as one piece" safe to offer as a button: every file, every reading
    list and every criterion is kept, and the seams stop being seams because
    nothing is on the other side of them.
    """
    if len(plan.units) <= 1:
        return plan.model_copy(deep=True)

    def union(lists: Iterable[Sequence[str]]) -> list[str]:
        seen: list[str] = []
        for items in lists:
            for item in items:
                if item not in seen:
                    seen.append(item)
        return seen

    own = union(u.files_expected for u in plan.units)
    merged = WorkUnit(
        id="U-1",
        title=" + ".join(u.title for u in plan.units)[:200],
        objective="\n\n".join(f"{u.id} as planned -- {u.title}: {u.objective}"
                              for u in plan.units),
        criterion_ids=union(u.criterion_ids for u in plan.units),
        files_expected=own,
        read_files=[p for p in union(u.read_files for u in plan.units) if p not in own],
        provides=union(u.provides for u in plan.units),
        requires=[],
        depends_on=[],
        notes="\n".join(
            [f"Merged from {len(plan.units)} units at a person's ruling; the seams between "
             "them are now inside this one unit and are yours to keep consistent:"]
            + [f"- {s}" for s in plan.seams]
            + [f"{u.id}: {u.notes}" for u in plan.units if u.notes]),
    )
    return Plan(
        summary=f"{plan.summary}\n\nBuilt as one piece at a person's ruling.".strip(),
        units=[merged], seams=[], integration_notes=plan.integration_notes,
    )


def cut_open(records: Sequence[dict[str, Any]], spec_hash: str) -> bool:
    """A plan review that stopped the run and has not been ruled on."""
    reviews = [r for r in records if r.get("kind") == "cut_review"
               and r.get("spec_hash") == spec_hash]
    if not reviews or not (reviews[-1].get("payload") or {}).get("needed"):
        return False
    after = reviews[-1]["seq"]
    return not any(r.get("kind") == "cut_ruling" and r["seq"] > after
                   and r.get("spec_hash") == spec_hash for r in records)


def pending_ruling(records: Sequence[dict[str, Any]], spec_hash: str) -> dict[str, Any] | None:
    """A ruling on the cut that no build has acted on yet.

    Acting on one appends a `plan`, so a ruling with a plan after it is spent.
    "Back to the spec" is never pending here: it sends the feature back to spec
    review, and the next approval builds from a fresh architect.
    """
    rulings = [r for r in records if r.get("kind") == "cut_ruling"
               and r.get("spec_hash") == spec_hash]
    if not rulings:
        return None
    last = rulings[-1]
    if (last.get("payload") or {}).get("choice") == "back_to_spec":
        return None
    if any(r.get("kind") == "plan" and r["seq"] > last["seq"] for r in records):
        return None
    return last["payload"]


def check_cut(store: EvidenceStore, spec_hash: str) -> list[Finding]:
    """What a person chose to build on anyway, carried into the packet.

    A cut kept over the plan checker's objection is an accepted risk, and the
    packet is where accepted risks are read. Each one keeps its units, so if the
    build later breaks at that seam the reader can see it was said beforehand.
    """
    records = store.records()
    rulings = [r for r in records if r.get("kind") == "cut_ruling"
               and r.get("spec_hash") == spec_hash]
    if not rulings or (rulings[-1].get("payload") or {}).get("choice") != "keep":
        return []
    reviews = [r for r in records if r.get("kind") == "cut_review"
               and r.get("spec_hash") == spec_hash and r["seq"] < rulings[-1]["seq"]]
    if not reviews:
        return []
    review = reviews[-1]["payload"] or {}
    out: list[Finding] = []
    for item in review.get("open") or []:
        o = item.get("objection") or {}
        a = item.get("answer") or {}
        out.append(Finding(
            id=f"cut-kept-{o.get('id', len(out) + 1)}",
            title=f"Built on a cut the plan checker objected to: {_one_line(o.get('claim', ''))}",
            severity="minor",
            category="verification",
            detail=(
                f"{o.get('claim', '')}\n\nWhat it said would follow: {o.get('consequence', '')}"
                + (f"\n\nThe architect: {a.get('note', '')}" if a.get("note") else "")
                + "\n\nA person kept the cut at plan review. If the build broke at this seam, "
                  "it was predicted here."),
            evidence=", ".join(o.get("unit_ids") or []),
            recommendation="",
        ))
    for i, fact in enumerate(review.get("facts") or [], start=1):
        out.append(Finding(
            id=f"cut-kept-fact-{i}",
            title="Built on a cut with a measured problem a person accepted",
            severity="minor",
            category="verification",
            detail=f"{fact}\n\nA person kept the cut at plan review.",
            evidence="", recommendation="",
        ))
    return out
