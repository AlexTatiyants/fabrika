"""A project at a glance: every feature that needs a person, and how close it is.

Computed from the record and nothing else -- no model is asked anything here.
Each number is one the review screen already shows, so the two can never
disagree about it: the calls left are counted exactly as `console/ui/shared.js`
counts them (`awaitingRuling`, `clusterFindings`, `settledBy`), and the checks
by hand exactly as `manualChecksFor` and `manualRulings` do. A change to one
is a change to the other; the tests pin both.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Collection, Mapping, Sequence

from . import git

#: Where a feature is waiting on a person, and what for.
WAITING_ON_YOU = {
    "awaiting_answers": "questions to answer",
    "awaiting_spec_approval": "a spec to freeze",
    "awaiting_cut_approval": "a plan to review",
    "awaiting_verdict": "a packet to rule on",
    "failed": "a run that stopped",
}
IN_FLIGHT = {"intake", "writing_spec", "waiting_for_plan", "building"}
FINISHED = {"accepted", "rejected"}

#: A finding still in front of a person. `noted` is deliberately absent: a
#: condition of the run is reported and counted, never queued as a call.
_WAITING = {"open", "escalated", "unattempted", "attempted_not_fixed", "needs_spec_change"}
#: Ship-ready first; a worse verdict after; a packet without one last.
_VERDICT_ORDER = {"ship": 0, "ship_with_rulings": 1, "send_back": 2, "reject": 3}


def awaiting_ruling(packet: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Findings a person still has to rule on, restatements folded away."""
    records = {r.get("finding_id"): r for r in packet.get("records") or []}
    out = []
    for f in packet.get("findings") or []:
        rec = records.get(f.get("id")) or {}
        if rec.get("duplicate_of"):
            continue
        if (rec.get("outcome") or "open") in _WAITING:
            out.append(f)
    return out


def cluster_findings(findings: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    """Two findings are one call when they share a file and either share a
    criterion or neither names one. Union-find, as the review screen does it."""
    parent = {f["id"]: f["id"] for f in findings}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(findings):
        for b in findings[i + 1:]:
            af, bf = set(a.get("files") or []), set(b.get("files") or [])
            if not af & bf:
                continue
            ac, bc = set(a.get("criterion_ids") or []), set(b.get("criterion_ids") or [])
            if ac & bc or (not ac and not bc):
                parent[find(a["id"])] = find(b["id"])
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for f in findings:
        groups.setdefault(find(f["id"]), []).append(f)
    return list(groups.values())


def calls_left(packet: Mapping[str, Any], flags: Sequence[Any]) -> dict[str, int]:
    """How many calls, and checks by hand, the review screen shows, and how many
    of each are still open. A call is settled by any flag on one of its
    findings; a check by hand by a criterion flag on its criterion."""
    anchors: dict[str, list[Any]] = {}
    for flag in flags:
        anchor = _get(flag, "anchor")
        if anchor:
            anchors.setdefault(anchor, []).append(flag)
    calls = cluster_findings(awaiting_ruling(packet))
    open_calls = sum(1 for group in calls if not any(anchors.get(f["id"]) for f in group))
    checks = [c.get("criterion_id") for c in packet.get("manual_checks") or []]
    ruled = {_get(f, "anchor") for f in flags if _get(f, "source") == "criterion"}
    return {
        "calls": len(calls), "calls_left": open_calls,
        "checks": len(checks), "checks_left": sum(1 for c in checks if c not in ruled),
    }


def spend(records: Sequence[Mapping[str, Any]],
          billed_routes: Collection[str] | None = None) -> dict[str, Any]:
    """What a feature cost, from its own call records.

    Not from its `usage` record: that is the console process's running total,
    shared by every feature it served, so a feature built beside another carries
    both. Each call is recorded against the feature that made it, with what was
    billed and -- for a subscription route, which bills nothing per call -- what
    the same work would have cost.

    A row from before rows said whether they were billed carries a cost figure
    either way, and on a subscription route that figure is not money. Such a
    row is judged by its route (`billed_routes`, from the config), so seventeen
    old Claude Code calls stop reading as $6.16 charged.

    Tokens are split the same way, per route: what went through a subscription
    and what was charged per token.
    """
    billed = notional = 0.0
    tokens: dict[str, dict[str, int]] = {}
    for record in records:
        if record.get("kind") != "call":
            continue
        payload = record.get("payload")
        if not isinstance(payload, Mapping):
            continue
        route = str(payload.get("route") or "")
        is_billed = payload.get("billed")
        if is_billed is None:
            is_billed = billed_routes is not None and route in billed_routes
        cost = float(payload.get("cost_usd") or 0.0) + float(payload.get("notional_usd") or 0.0)
        if is_billed:
            billed += cost
        else:
            notional += cost
        n = int(payload.get("prompt_tokens") or 0) + int(payload.get("completion_tokens") or 0)
        bucket = tokens.setdefault(route or "unknown", {"subscription": 0, "charged": 0})
        bucket["charged" if is_billed else "subscription"] += n
    return {"billed": round(billed, 2), "notional": round(notional, 2), "tokens": tokens}


def behind_main(repo: str | Path, base_ref: str, branch: str) -> int | None:
    """Commits the base branch has gained since this feature's branch was cut.

    What actually makes a waiting feature stale: every one is a change its
    branch was never built or checked against. None when git cannot say.
    """
    base = (git.out(["merge-base", branch, base_ref], repo, timeout=10) or "").strip()
    count = git.out(["rev-list", "--count", f"{base}..{base_ref}"], repo, timeout=10) if base else None
    try:
        return int(count) if count is not None else None
    except ValueError:
        return None


def feature_summary(state: Any, packet: Mapping[str, Any] | None, flags: Sequence[Any],
                    records: Sequence[Mapping[str, Any]], behind: int | None,
                    billed_routes: Collection[str] | None = None) -> dict[str, Any]:
    stage = state.stage
    out: dict[str, Any] = {
        "feature_id": state.feature_id, "title": state.title or state.feature_id,
        "stage": stage, "needs": WAITING_ON_YOU.get(stage, ""),
        "since": state.updated_at, "created_at": state.created_at,
        "behind_main": behind, "spend": spend(records, billed_routes),
        "phase": next((p.name for p in reversed(state.phases or []) if p.status == "running"), ""),
        "error": (state.error or "")[:300] if stage == "failed" else "",
    }
    if packet:
        stats = packet.get("stats") or {}
        out.update({
            "verdict": packet.get("verdict") or "",
            "gist": (packet.get("gist") or "").strip(),
            "headline": (packet.get("headline") or "").strip(),
            "criteria_verified": stats.get("criteria_verified") or 0,
            "criteria_total": stats.get("criteria_total") or 0,
            "blockers": stats.get("blockers") or 0,
            "rounds": (packet.get("rework") or {}).get("rounds") or 0,
            **calls_left(packet, flags),
        })
    return out


def order_waiting(features: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ship-ready first, then by verdict, then the longest-waiting."""
    return sorted(features, key=lambda f: (_VERDICT_ORDER.get(f.get("verdict") or "", 4),
                                          f.get("since") or ""))


def _get(obj: Any, key: str) -> Any:
    return obj.get(key) if isinstance(obj, Mapping) else getattr(obj, key, None)
