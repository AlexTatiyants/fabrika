"""A project's history: what happened to it, in words, and who did it.

Read from the project's ledger and nothing else -- no model is asked anything
here. The ledger is the audit trail and stays exactly as it is; this is how a
person reads it. Raw, it is mostly noise: of one project's 132 records, 49
were whole snapshots of the project (45-110k each), 17 were check timings, and
re-readings arrived ten to an evening, each proposing much the same thing.
What a person comes for -- what Fabrika wrote into their repository, what
they decided, when a check went red -- is in there, and is not findable.

So each record becomes at most one event, and some become none:

- a snapshot is never a row. An edit becomes what changed in it ("added
  web-e2e"); a snapshot that only follows another record's event adds a reason
  to it ("3 features were building") or nothing;
- timings and the unchanged-proposal bookkeeping are dropped;
- re-readings in a row, with nothing of yours between them, are one event.

Every event keeps the `seq` it was read from, so nothing is hidden: the record
is one click away, and the raw ledger is still served beside this.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

#: What a ruling accepted, in words a person uses. The keys are the diff's own.
RULING_WORDS = {
    "testing": "what tests can use",
    "trace_dirs": "where tests record",
    "test_file_commands": "how one test file runs",
    "blind_placements": "where Fabrika puts its tests",
    "environment": "the environment",
    "preview": "what a person opens",
    "scaffolding": "files for the repository",
}
_GATE_VERB = {"add": "added", "remove": "removed", "change": "changed"}


def _words(key: str) -> str:
    action, _, name = key.partition(":")
    if name and action in _GATE_VERB:
        return f"{_GATE_VERB[action]} {name}"
    return RULING_WORDS.get(key, key.replace("_", " "))


def _proposed(diff: Mapping[str, Any]) -> list[str]:
    """What one re-reading proposed, as short phrases."""
    items = [f"{c.get('action', 'change')} {c.get('name', '')}".strip()
             for c in diff.get("gate_changes") or []]
    if diff.get("testing"):
        items.append("what tests can use")
    if diff.get("environment"):
        items.append("the environment")
    if diff.get("preview"):
        items.append("what a person opens")
    items += [f"add {str(f.get('path', '')).rsplit('/', 1)[-1]}"
              for f in diff.get("scaffolding") or []]
    if diff.get("test_file_commands"):
        items.append("how one test file runs")
    if diff.get("blind_placements"):
        items.append("where Fabrika puts its tests")
    items += [f"suggest: {r.get('title', '')}" for r in diff.get("recommendations") or []]
    return items


def _cost(meta: Mapping[str, Any]) -> dict[str, float] | None:
    """A reading's cost from its record, or None when it was never recorded."""
    if "cost_usd" not in meta and "notional_usd" not in meta:
        return None
    return {"billed": float(meta.get("cost_usd") or 0.0),
            "notional": float(meta.get("notional_usd") or 0.0)}


def _feature_name(feature_id: str, titles: Mapping[str, str]) -> str:
    if feature_id in titles:
        return titles[feature_id]
    # A feature since discarded keeps a readable name: its id is a slug and a hash.
    slug = re.sub(r"-[0-9a-f]{6}$", "", feature_id)
    return slug.replace("-", " ").title() if slug else "a feature"


def _attributed_feature(payload: Mapping[str, Any]) -> str:
    """The feature a re-run at its starting point was for. Recorded on the
    record now; an older one names it only in a container name in its output."""
    if payload.get("feature_id"):
        return str(payload["feature_id"])
    m = re.search(r"fabrika-[a-z0-9-]+?-([a-z0-9-]+-[0-9a-f]{6})-base",
                  str(payload.get("output_tail") or ""))
    return m.group(1) if m else ""


def _event(record: Mapping[str, Any] | None, *, at: str = "", kind: str, actor: str,
           title: str, detail: str = "", items: Sequence[str] = (), **extra: Any) -> dict[str, Any]:
    return {"seq": record.get("seq") if record else None,
            "at": record.get("at", "") if record else at,
            "kind": kind, "actor": actor, "title": title, "detail": detail,
            "items": list(items), "commit": "", "lamp": "", **extra}


def project_events(records: Sequence[Mapping[str, Any]],
                   titles: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """The project's own records as events, oldest first."""
    titles = titles or {}
    events: list[dict[str, Any]] = []
    previous: Mapping[str, Any] | None = None
    last_checks: dict[str, list[str]] | None = None
    for i, record in enumerate(records):
        kind = record.get("kind")
        payload = record.get("payload") or {}
        meta = record.get("meta") or {}
        note = str(meta.get("note") or "")
        if not isinstance(payload, Mapping):
            continue

        if kind == "project":
            if note == "registered":
                events.append(_event(record, kind="you", actor="you",
                                     title="You added this project to Fabrika",
                                     detail=str(payload.get("repo") or "")))
            elif note.startswith("edited:") and previous is not None:
                changes = _edits(previous, payload, [f.strip() for f in note[7:].split(",")])
                # A ruling already said what it changed; its snapshot says it again.
                before = records[i - 1].get("kind") if i else ""
                if changes and before not in ("resurvey_ruling", "recommendation_ruling"):
                    events.append(_event(record, kind="you", actor="you",
                                         title="You edited the project", items=changes))
            elif "baseline re-run" in note and events and events[-1]["kind"] == "checks" \
                    and events[-1].get("raw") == "baseline":
                why = note.split("·", 1)[-1].strip()
                why = re.sub(r"(\d+) feature\(s\) are building against it",
                             lambda m: f"{m.group(1)} feature"
                                       + (" was" if m.group(1) == "1" else "s were") + " building", why)
                why = why.replace("the gates or the environment moved since it was approved",
                                  "the checks or the environment had changed")
                events[-1]["detail"] = why
            previous = payload
            continue

        if kind == "survey":
            env = (payload.get("environment") or {}).get("kind", "")
            events.append(_event(
                record, kind="reading", actor="fabrika", raw="survey",
                title="Fabrika surveyed the repository",
                detail=f"{len(payload.get('gates') or [])} checks proposed"
                       + (f" · runs in {'Docker Compose' if env == 'compose' else env}" if env else ""),
                cost=_cost(meta), count=1))
        elif kind == "resurvey":
            items = _proposed(payload)
            events.append(_event(
                record, kind="reading", actor="fabrika", raw="resurvey",
                title="Fabrika re-read the repository: " + (
                    f"{len(items)} change{'s' if len(items) != 1 else ''} proposed"
                    if items else "nothing to change"),
                items=items, lamp="" if items else "quiet", cost=_cost(meta), count=1,
                proposed=len(items)))
        elif kind == "baseline":
            results = payload.get("results") or []
            setup_bad = [r for r in results if str(r.get("name", "")).startswith("setup[")
                         and not r.get("passed")]
            checks = [r for r in results if not str(r.get("name", "")).startswith("setup[")]
            ok = [r["name"] for r in checks if r.get("passed")]
            bad = [r["name"] for r in checks if not r.get("passed") and not r.get("skipped")]
            moved: list[str] = []
            if last_checks is not None:
                moved += [f"{n} now passes" for n in ok if n in last_checks["bad"]]
                moved += [f"{n} now fails" for n in bad if n in last_checks["ok"]]
                moved += [f"{n} is new" for n in ok + bad
                          if n not in last_checks["ok"] + last_checks["bad"]]
            title = (f"Checks ran: all {len(checks)} passed" if not bad
                     else f"Checks ran: {len(bad)} of {len(checks)} failed")
            if setup_bad:
                title = "Checks could not run: setup failed"
            events.append(_event(record, kind="checks", actor="fabrika", raw="baseline",
                                 title=title, items=moved,
                                 lamp="bad" if bad or setup_bad else "ok"))
            last_checks = {"ok": ok, "bad": bad}
        elif kind == "approval":
            n = len([g for g in payload.get("gates") or [] if not str(g).startswith("setup[")])
            parked = payload.get("red_at_approval") or []
            events.append(_event(record, kind="you", actor="you", title="You accepted the checks",
                                 detail=f"{n} checks judge every feature"
                                        + (f" · parked: {', '.join(parked)}" if parked else "")))
        elif kind == "resurvey_ruling":
            applied, rejected = payload.get("applied") or [], payload.get("rejected") or []
            title = ("You accepted " + (f"{len(applied)} change{'s' if len(applied) != 1 else ''}"
                                        if applied else "nothing")
                     + (f", turned down {len(rejected)}" if rejected else ""))
            events.append(_event(record, kind="you", actor="you", title=title,
                                 items=[_words(a) for a in applied]
                                 + [f"turned down: {_words(r)}" for r in rejected]))
        elif kind == "recommendation_ruling":
            events.append(_event(record, kind="you", actor="you",
                                 title=f"You turned down a suggestion: {payload.get('title', '')}",
                                 detail=str(payload.get("reason") or "")))
        elif kind in ("scaffold", "dockerfile"):
            written = payload.get("written") or ([payload["path"]] if payload.get("path") else [])
            if not written:
                continue
            what = "the Dockerfile" if kind == "dockerfile" else (
                f"{len(written)} file{'s' if len(written) != 1 else ''}")
            verb = "Removed from" if payload.get("removed") else "Written to"
            events.append(_event(record, kind="repo", actor="you",
                                 title=f"{verb} your repository: {what}",
                                 detail=str(payload.get("commit_problem") or ""), items=written,
                                 commit=str(payload.get("commit") or "")))
        elif kind == "attribution":
            feature = _attributed_feature(payload)
            name = _feature_name(feature, titles) if feature else "a feature"
            passed = payload.get("at_base") == "passed"
            could_not = payload.get("at_base") == "could_not_run"
            events.append(_event(
                record, kind="checks", actor="fabrika", raw="attribution", lamp="quiet",
                feature_id=feature, feature=name, gates=[payload.get("gate", "")],
                title=f"{payload.get('gate')} failed in {name}, and "
                      + ("could not be run" if could_not else "passed" if passed else "failed too")
                      + " at the commit it started from",
                detail=("so it cannot be told apart yet" if could_not else
                        "so the feature broke it" if passed else
                        "so it was already broken, and the feature is not blamed")))
        # check_timing and resurvey_unchanged are bookkeeping, not events.
    return events


def _edits(before: Mapping[str, Any], after: Mapping[str, Any], fields: Iterable[str]) -> list[str]:
    fields = set(fields)
    out: list[str] = []
    if "gates" in fields:
        was = {g.get("name"): g for g in before.get("gates") or []}
        now = {g.get("name"): g for g in after.get("gates") or []}
        out += [f"added {n}" for n in now if n not in was]
        out += [f"removed {n}" for n in was if n not in now]
        out += [f"changed {n}" for n in now if n in was and now[n] != was[n]]
    if "environment" in fields:
        out.append("changed the environment")
    if "base_ref" in fields:
        out.append(f"features branch from {after.get('base_ref')}")
    if "repo" in fields:
        out.append(f"moved to {after.get('repo')}")
    if "name" in fields:
        out.append(f"renamed it {after.get('name')}")
    if "digest_budget" in fields:
        out.append("changed the digest budget")
    return out


def feature_events(features: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each feature's start and the points it came back to you, from its own ledger.

    `features` are `{feature_id, title, created_at, stage, records}`; the
    feature's step-by-step record stays on its page.
    """
    out: list[dict[str, Any]] = []
    for f in features:
        fid, title = f["feature_id"], f["title"]
        out.append(_event(None, at=f.get("created_at") or "", kind="feature", actor="you",
                          title="You started", feature_id=fid, feature=title))
        # The first packet is the feature coming back to you. Every later one --
        # a rework round, a revalidation -- is the same review brought up to
        # date, so they are one line carrying the latest verdict.
        packets = [r for r in f.get("records") or [] if r.get("kind") == "packet"]
        for n, record in enumerate(packets[:2]):
            latest = packets[-1] if n else record
            out.append(_event(None, at=latest.get("at", ""), kind="feature", actor="fabrika",
                              title="is ready for your review" if not n else (
                                  "has an updated review" if len(packets) == 2
                                  else f"has an updated review ({len(packets) - 1} updates)"),
                              feature_id=fid, feature=title,
                              verdict=(latest.get("payload") or {}).get("verdict") or ""))
        for record in f.get("records") or []:
            if record.get("kind") == "packet":
                continue
            elif record.get("kind") == "verdict":
                ruled = (record.get("payload") or {}).get("verdict")
                out.append(_event(None, at=record.get("at", ""), kind="feature", actor="you",
                                  title="You accepted" if ruled == "accepted" else "You rejected",
                                  feature_id=fid, feature=title))
    return out


def fold(events: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Newest first, with runs of alike events made one.

    Re-readings in a row -- nothing of yours between them -- are one event
    carrying the latest's proposal. Failing checks re-run for the same feature
    in the same minute are one event.
    """
    ordered = sorted(events, key=lambda e: (e["at"], e["seq"] or 0))
    out: list[dict[str, Any]] = []
    for e in ordered:
        last = out[-1] if out else None
        if last and e.get("raw") == "resurvey" and last.get("raw") in ("resurvey",) \
                and last["kind"] == "reading":
            n = last["count"] + 1
            quiet = last["lamp"] == "quiet" and e["lamp"] == "quiet"
            cost = _sum_costs([last.get("cost"), e.get("cost")])
            k = e["proposed"]
            merged = {**e, "count": n, "first_at": last.get("first_at", last["at"]), "cost": cost,
                      "first_seq": last.get("first_seq", last["seq"]),
                      "lamp": "quiet" if quiet else "",
                      "title": (f"Fabrika re-read the repository {n} times"
                                + (": nothing to change" if quiet else "")),
                      "detail": "" if quiet else
                      f"the latest proposes {k} change{'s' if k != 1 else ''}"}
            out[-1] = merged
            continue
        if last and e.get("raw") == "attribution" and last.get("raw") == "attribution" \
                and e.get("feature_id") == last.get("feature_id") and e["at"][:16] == last["at"][:16]:
            gates = [*last.get("gates", []), *e.get("gates", [])]
            name = e.get("feature", "a feature")
            out[-1] = {**last, "gates": gates, "items": gates,
                       "title": f"{len(gates)} checks failed in {name}, and each was re-run "
                                "at the commit it started from",
                       "detail": "to tell what the feature broke from what was already broken"}
            continue
        out.append(e)
    out.reverse()
    return out


def _sum_costs(costs: Iterable[Mapping[str, float] | None]) -> dict[str, float] | None:
    known = [c for c in costs if c]
    if not known:
        return None
    return {"billed": round(sum(c["billed"] for c in known), 4),
            "notional": round(sum(c["notional"] for c in known), 4)}


def summary(project_records: Sequence[Mapping[str, Any]],
            events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The four numbers at the top of the tab, each also a filter."""
    readings = [r for r in project_records if r.get("kind") in ("survey", "resurvey")]
    quiet = [r for r in project_records if r.get("kind") == "resurvey"
             and not _proposed(r.get("payload") or {})]
    costs = [_cost(r.get("meta") or {}) for r in readings]
    repo = [e for e in events if e["kind"] == "repo"]
    runs = [r for r in project_records if r.get("kind") == "baseline"]
    last_run = next((e for e in events if e.get("raw") == "baseline"), None)
    return {
        "commits": len([e for e in repo if e.get("commit")]),
        "files": sum(len(e["items"]) for e in repo),
        "decisions": len([e for e in events if e["actor"] == "you" and e["kind"] != "feature"]),
        "check_runs": len(runs),
        "last_run": {"at": last_run["at"], "lamp": last_run["lamp"], "title": last_run["title"]}
                    if last_run else None,
        "readings": len(readings),
        "surveys": len([r for r in readings if r.get("kind") == "survey"]),
        "quiet": len(quiet),
        "reading_cost": _sum_costs(costs),
        "readings_costed": len([c for c in costs if c]),
    }


def project_history(project_records: Sequence[Mapping[str, Any]],
                    features: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    titles = {f["feature_id"]: f["title"] for f in features}
    events = fold([*project_events(project_records, titles), *feature_events(features)])
    return {"events": events, "summary": summary(project_records, events),
            "records": len(project_records),
            "since": project_records[0].get("at", "") if project_records else ""}
