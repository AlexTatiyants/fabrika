"""What a station's turn produced, read back from the ledger for the call tree."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..gates import gate_outcome, is_green
from ..schemas import GateResult


#: Besides the `step` record that says how a turn went, every station leaves
#: the work itself in the ledger. The call tree's row carried only the
#: headline -- `6/19 passed`, `2 probe(s), 2 failing`, `7 blind test file(s)`
#: -- and *which* six, which probes, which files was a question only the raw
#: JSONL could answer. These are the kinds each station's work arrives in.
#:
#: The payloads themselves are far too big to travel with a log the console
#: polls: the gates of one run are 73KB, the workers' files 90KB, because both
#: carry whole files and whole command outputs. `step_readout` keeps the names
#: and the outcomes and drops the contents.
READOUT_KINDS: dict[str, tuple[str, ...]] = {
    "scout": ("scout",),
    "interrogator": ("interrogation",),
    "spec_writer": ("spec", "spec_testability"),
    # Steps recorded before the rename carry the old name, and the ledger keeps
    # them for ever (INV-2). They read the same kinds.
    "planner": ("spec", "spec_testability"),
    "spec_checker": ("spec_check",),
    "architect": ("plan",),
    "plan_checker": ("plan_check", "cut_review", "cut_ruling"),
    "oracle": ("oracle", "oracle_revision"),
    "workers": ("worker", "unit_env"),
    "integrator": ("integration",),
    "gates": ("gates",),
    "breaker": ("breaker", "breaker_suite"),
    "qa": ("qa",),
    "review": ("review", "recheck"),
    "arbiter": ("arbiter",),
    "repairers": ("repair_plan", "repair", "unit_env"),
}

#: No readout is worth a scroll. Past this a section says how many it is not
#: showing, and the ledger -- which has all of it -- is one click from the row.
READOUT_CAP = 40

#: How loud a finding's severity is allowed to be. `nit` gets no colour at all:
#: a row tinted for every severity teaches that the tint means nothing.
_SEVERITY_TONE = {"blocker": "bad", "critical": "bad", "major": "bad", "minor": "warn"}


def _words(text: Any, limit: int) -> str:
    """One line of plain text, clipped. Ledger prose runs to paragraphs and a
    readout has one row to say it in."""
    out = " ".join(str(text or "").split())
    return out if len(out) <= limit else out[: limit - 1].rstrip() + "…"


#: Terminal colour and cursor control, which a runner writes and a row cannot.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _first_plain_line(tail: str) -> str:
    """One line of what a check printed, for the row under its name.

    This is a display and never an attribution: what a check *settled* is
    counted file by file and read from `summary_known` and the rest, because
    searching a runner's console text for per-test facts is what once reported
    two erroring criteria to a human as passed.

    A tail begins wherever the buffer began, so its first line is as likely to
    be a truncation marker, half a rendered DOM, or colour codes as it is to be
    the sentence that says what went wrong. Take the first line that reads as a
    line of text and leave the rest to the gates screen, which shows the whole
    tail.
    """
    for line in (tail or "").splitlines():
        line = _ANSI.sub("", line).strip()
        if not line or line.startswith("..."):
            continue
        if "<" in line or any(ch < " " and ch != "\t" for ch in line):
            continue
        return line
    return ""


def _entry(text: Any, outcome: str = "", tone: str = "", note: Any = "") -> dict[str, Any]:
    """One thing the station produced: what it is, how it came out, and why.

    Empty fields are left out rather than sent as empty strings -- a readout
    travels with every step of every run.
    """
    got: dict[str, Any] = {"text": _words(text, 200)}
    if outcome:
        got["outcome"] = outcome
    if tone:
        got["tone"] = tone
    if note:
        # A line of dim text under the row, not the paragraph the agent wrote.
        # Whoever wants the paragraph wants the ledger, one click from the row.
        got["note"] = _words(note, 160)
    return got


def _section(what: str, entries: list[dict[str, Any]], noun: str, *,
             note: Any = "", caveat: str = "", group: str = "") -> dict[str, Any]:
    """A list of those, under a heading. `group` is the unit or the role whose
    work it is, for the stations the call tree draws as parallel rows: the row
    for worker U-2 shows U-2's section and not U-1's."""
    got: dict[str, Any] = {"what": what, "noun": noun, "entries": entries[:READOUT_CAP]}
    if len(entries) > READOUT_CAP:
        got["more"] = len(entries) - READOUT_CAP
    if note:
        got["note"] = _words(note, 240)
    if caveat:
        got["caveat"] = caveat
    if group:
        got["group"] = group
    return got


def _last(by_kind: dict[str, list[dict[str, Any]]], kind: str) -> Any:
    got = by_kind.get(kind) or []
    return got[-1].get("payload") if got else None


def _paths(files: Any) -> list[dict[str, Any]]:
    """A station's file list as rows. The list holds whole file contents; only
    the path and what it was for survive into a readout."""
    out = []
    for f in files or []:
        if isinstance(f, dict):
            out.append(_entry(f.get("path", ""), note=f.get("purpose", "")))
        else:
            out.append(_entry(f))
    return out


def _readout_scout(by_kind):
    p = _last(by_kind, "scout") or {}
    out = []
    if p.get("relevant_files"):
        out.append(_section("Files it named relevant", [_entry(f) for f in p["relevant_files"]],
                            "files", note=p.get("summary", "")))
    if p.get("risks"):
        # No tone: a tone colours the outcome word, and a risk has none. Seven
        # amber lines in a row would be the screen shouting a list at you.
        out.append(_section("Risks it flagged", [_entry(r) for r in p["risks"]], "risks"))
    return out


def _readout_interrogator(by_kind):
    p = _last(by_kind, "interrogation") or {}
    questions = p.get("ambiguities") or []
    if not questions:
        return []
    return [_section(
        "What it could not settle on its own",
        [_entry(" · ".join(x for x in (q.get("id"), q.get("question")) if x),
                note=q.get("why_it_matters", "")) for q in questions],
        "questions",
        note="Too vague to build from as written." if p.get("too_vague") else p.get("notes", ""))]


def _readout_spec_writer(by_kind):
    spec = _last(by_kind, "spec") or {}
    out = []
    criteria = spec.get("acceptance_criteria") or []
    if criteria:
        out.append(_section(
            "What it must do to be done",
            [_entry(" · ".join(x for x in (c.get("id"), c.get("title")) if x)) for c in criteria],
            "criteria", note=spec.get("summary", "")))
    gaps = _last(by_kind, "spec_testability") or []
    if gaps:
        out.append(_section(
            "What this project cannot verify about it",
            [_entry(g.get("title", ""), outcome=g.get("severity", ""),
                    tone=_SEVERITY_TONE.get(g.get("severity", ""), "warn"),
                    note=g.get("detail", "")) for g in gaps],
            "gaps"))
    return out


def _objection_rows(items):
    rows = []
    for item in items or []:
        o = item.get("objection") or {}
        a = item.get("answer") or {}
        anchor = ", ".join((o.get("unit_ids") or []) + (o.get("criterion_ids") or [])
                           + (o.get("question_ids") or []))
        rows.append(_entry(
            " · ".join(x for x in (o.get("id"), anchor, o.get("claim")) if x),
            outcome="settled" if item.get("settled") else item.get("why", "open"),
            tone="" if item.get("settled") else "warn",
            note=a.get("note") or o.get("consequence", "")))
    return rows


def _readout_spec_checker(by_kind):
    p = _last(by_kind, "spec_check") or {}
    rows = _objection_rows(p.get("outcome"))
    return [_section("What it objected to, and what the spec writer did", rows, "objections",
                     note="Anything not settled is carried into the spec as an open question.")]


def _readout_plan_checker(by_kind):
    review = _last(by_kind, "cut_review") or {}
    ruling = _last(by_kind, "cut_ruling") or {}
    out = [_section("What it objected to, and what the architect did",
                    _objection_rows((review.get("settled") or []) + (review.get("open") or [])),
                    "objections")]
    facts = review.get("facts") or []
    if facts:
        out.append(_section("What code measured about the cut",
                            [_entry(f, tone="warn") for f in facts], "facts"))
    if ruling:
        out.append(_section("What a person ruled",
                            [_entry(ruling.get("choice", ""), note=ruling.get("note", ""))],
                            "ruling"))
    elif review.get("reason"):
        out.append(_section("Plan review", [_entry(review["reason"])], "gate"))
    return out


def _readout_architect(by_kind):
    p = _last(by_kind, "plan") or {}
    out = []
    units = p.get("units") or []
    if units:
        out.append(_section(
            "How it cut the work up",
            [_entry(" · ".join(x for x in (u.get("id"), u.get("title")) if x),
                    note=u.get("objective", "")) for u in units],
            "units", note=p.get("summary", "")))
    if p.get("seams"):
        out.append(_section("Seams it named between them",
                            [_entry(s) for s in p["seams"]], "seams",
                            caveat="A seam is where two units must agree. Every one of these is "
                                   "a thing no single unit can get right by itself."))
    return out


def _readout_oracle(by_kind):
    p = _last(by_kind, "oracle_revision") or _last(by_kind, "oracle") or {}
    tests = p.get("tests") or []
    if not tests:
        return []
    entries = []
    for t in tests:
        cases = t.get("cases") or []
        ids = ", ".join(t.get("criterion_ids") or [])
        entries.append(_entry(
            t.get("path", ""),
            outcome=f"{len(cases)} case{'' if len(cases) == 1 else 's'}" if cases else "",
            note=f"tagged to {ids}" if ids else ""))
    return [_section("Test files it wrote", entries, "test files", note=p.get("strategy", ""),
                     caveat="Written against the spec, before the oracle saw any of the code. "
                            "That is the whole of what makes them worth anything.")]


def _units_readout(by_kind, kind: str, what: str):
    """Workers and repairers: one section per unit, because the call tree draws
    them as parallel rows and each row is one unit's turn."""
    unready = {}
    for r in by_kind.get("unit_env") or []:
        env = r.get("payload") or {}
        if not env.get("ready"):
            unready[env.get("unit", "")] = (
                env.get("problem") or env.get("report") or "its environment was not ready")
    out = []
    for r in by_kind.get(kind) or []:
        p = r.get("payload") or {}
        unit = p.get("unit_id") or (r.get("meta") or {}).get("unit_id") or ""
        entries = _paths(p.get("files"))
        if unit in unready:
            entries.insert(0, _entry(unready[unit], outcome="environment", tone="warn"))
        out.append(_section(what, entries, "files", note=p.get("summary", ""), group=unit))
    return out


def _readout_integrator(by_kind):
    p = _last(by_kind, "integration") or {}
    out = []
    if p.get("files"):
        out.append(_section("Files it touched closing the seams", _paths(p["files"]), "files",
                            note=p.get("summary", "")))
    trouble = [_entry(s, outcome="seam", tone="warn") for s in (p.get("seam_issues") or [])]
    trouble += [_entry(s, outcome="unresolved", tone="warn") for s in (p.get("unresolved") or [])]
    if trouble:
        out.append(_section("What it could not close", trouble, "open"))
    return out


def _gate_words(result) -> tuple[str, str]:
    """One word for how a check came out, and how loud to say it.

    "could not run" replaces "failed" rather than joining it: a check that
    never executed did not fail, and a row that says both contradicts itself.
    Both readings come from `gates`, which is where they are defined -- this
    is a second screen for them, not a second definition.
    """
    if result.skipped:
        return ("failed, allowed", "warn") if not result.passed else ("skipped", "")
    if gate_outcome(result) == "could_not_run":
        return "could not run", "warn"
    if is_green(result):
        return "passed", "good"
    return "failed", "bad"


def _readout_gates(by_kind):
    records = by_kind.get("gates") or []
    if not records:
        return []
    # Two records for one run of the station: the checks as they came back, and
    # the same checks again once each failure had been attributed to the base
    # commit. The second is the first plus what makes it readable.
    payload = records[-1].get("payload") or {}
    entries, green, not_ours = [], 0, 0
    for raw in payload.get("results") or []:
        try:
            result = GateResult.model_validate(raw)
        except Exception:
            continue
        word, tone = _gate_words(result)
        green += word == "passed"
        # What a red check is worth depends entirely on this. "backend-lint
        # failed" on a repo with 129 standing errors says nothing about the
        # feature; "failed, and passed before this feature" says everything.
        base = {"passed": "passed at the base commit — this feature broke it",
                "failed": "failed at the base commit too — not this feature's",
                "could_not_run": "could not run at the base commit either"}.get(result.at_base, "")
        not_ours += result.at_base == "failed"
        # Which files a failure belongs to, when the run managed to sort it. A
        # check that goes green once this feature's failing blind tests are set
        # aside is the criterion table's news, not the check's.
        whose = {"blind_tests": "passes once this feature's failing blind tests are set aside",
                 "oracle_files": "passes only once the oracle's own files are set aside too",
                 "seam_tests": "passes once the integrator's seam checks are set aside — "
                               "a test this run wrote, not the branch",
                 "code": "fails without any of the oracle's files — it is the branch"
                 }.get(result.failed_on, "")
        # Counted, never read out of the output. Searching a runner's console
        # text for per-test facts is what once reported two erroring criteria
        # to a human as passed; `summary_known` is set only when the check ran
        # file by file, which is the only arrangement that can settle one.
        if result.summary_known and result.tests_total:
            files = (f"{result.tests_failed} of {result.tests_total} test file"
                     f"{'' if result.tests_total == 1 else 's'} failed")
        elif result.zero_ran:
            files = "there was nothing to run"
        else:
            files = ""
        printed = _first_plain_line(result.output_tail)
        note = " · ".join(x for x in (
            base, whose, files,
            f"{result.duration_s:.0f}s" if result.duration_s >= 1 else "",
            f"metric {result.metric:g}" if result.metric is not None else "",
            # A counted summary is strictly better than a line of output, so
            # the line only speaks when nothing counted anything.
            "" if word == "passed" or files else printed,
        ) if x)
        entries.append(_entry(result.name, outcome=word, tone=tone, note=note))
    if not entries:
        return []
    note = f"{green} of {len(entries)} passed."
    if not_ours:
        note += (f" {not_ours} of the failures failed at the base commit too, "
                 "so they are not this feature's.")
    return [_section("Every check, and how it came out", entries, "checks", note=note,
                     caveat="No model is involved in any of this: a check says a process exited "
                            "zero, or that a number cleared a threshold.")]


def _readout_breaker(by_kind):
    suite = _last(by_kind, "breaker_suite") or {}
    ran = _last(by_kind, "breaker") or {}
    failing = set(ran.get("failing") or [])
    rejected = set(ran.get("rejected") or [])
    entries = []
    for t in suite.get("tests") or []:
        path = t.get("path", "")
        if path in rejected:
            outcome, tone = "never applied", "warn"
        elif path in failing:
            outcome, tone = "the code failed it", "bad"
        elif ran.get("ran"):
            outcome, tone = "the code survived it", ""
        else:
            outcome, tone = "", ""
        entries.append(_entry(path, outcome=outcome, tone=tone, note=t.get("hypothesis", "")))
    if not entries:
        return []
    return [_section("Probes it wrote to break the code", entries, "probes",
                     note=suite.get("strategy", ""),
                     caveat="A probe is worth something because it fails. One the code survives "
                            "proves nothing at all, and none of them land on the branch.")]


def _readout_qa(by_kind):
    p = _last(by_kind, "qa") or {}
    results = p.get("results") or []
    if not results:
        return []
    tone = {"passed": "good", "failed": "bad", "unknown": "warn"}
    return [_section(
        "Every criterion, and what settled it",
        [_entry(c.get("criterion_id", ""), outcome=c.get("status", ""),
                tone=tone.get(c.get("status", ""), "warn"), note=c.get("evidence", ""))
         for c in results],
        "criteria", note=p.get("summary", ""))]


def _readout_review(by_kind):
    out = []
    for r in by_kind.get("review") or []:
        p = r.get("payload") or {}
        role = r.get("role", "")
        findings = p.get("findings") or []
        if findings:
            # By title, never by the `F-1` the agent wrote. A panel runs the
            # same role several times over and merges what comes back, so the
            # agent's own numbering starts again with every sample: three F-3s
            # in one list, none of them the F-3 the arbiter later ruled on --
            # the arbiter's ids are assigned by the orchestrator and are not in
            # this record. A title says which finding it is; a colliding number
            # says less than nothing.
            out.append(_section(
                "What it objected to",
                [_entry(f.get("title", ""),
                        outcome=f.get("severity", ""),
                        tone=_SEVERITY_TONE.get(f.get("severity", ""), ""),
                        note=f.get("detail", "")) for f in findings],
                "findings", group=role,
                note=" — ".join(x for x in (p.get("verdict"), p.get("summary")) if x)))
        if p.get("conceded"):
            out.append(_section("What it agreed was sound",
                                [_entry(c) for c in p["conceded"]], "concessions", group=role))
    # A repair round's review is a re-check: only the findings this agent
    # raised, only whether the repair closed them.
    for r in by_kind.get("recheck") or []:
        p = r.get("payload") or {}
        tone = {"fixed": "good", "not_fixed": "bad", "fixed_with_new_problem": "warn"}
        entries = [_entry(v.get("finding_id", ""),
                          outcome=(v.get("status", "") or "").replace("_", " "),
                          tone=tone.get(v.get("status", ""), "warn"),
                          note=v.get("evidence", "")) for v in (p.get("verdicts") or [])]
        entries += [_entry(f.get("title", ""),
                           outcome="the repair caused this",
                           tone=_SEVERITY_TONE.get(f.get("severity", ""), "warn"),
                           note=f.get("detail", "")) for f in (p.get("new_findings") or [])]
        if entries:
            out.append(_section("Whether the repairs closed what it raised", entries, "verdicts",
                                group=r.get("role", ""), note=p.get("notes", "")))
    return out


def _readout_arbiter(by_kind):
    p = _last(by_kind, "arbiter") or {}
    dispositions = p.get("dispositions") or []
    if not dispositions:
        return []
    # Only the two that need a person. `repair` is the ordinary outcome -- a
    # defect the line closes by itself -- and twenty-one red rows for it would
    # bury the two that are actually waiting on somebody.
    tone = {"escalate": "warn", "needs_spec_change": "warn"}
    return [_section(
        "Where each finding went",
        [_entry(d.get("finding_id", ""), outcome=(d.get("disposition", "") or "").replace("_", " "),
                tone=tone.get(d.get("disposition", ""), ""), note=d.get("reason", ""))
         for d in dispositions],
        "rulings", note=p.get("summary", ""))]


def _readout_repair_plan(by_kind):
    """How the round was cut. Nobody is asked for this -- findings
    that name a shared file are one unit, which is a fact about the findings
    rather than a judgment about them, so it is computed."""
    p = _last(by_kind, "repair_plan") or {}
    units = p.get("units") or []
    if not units:
        return []
    return [_section(
        "The repairs it took",
        [_entry(" · ".join(x for x in (u.get("id"), u.get("title")) if x),
                outcome=", ".join(u.get("finding_ids") or []),
                note=u.get("objective", "")) for u in units],
        "units", note=p.get("summary", ""))]


_READOUTS: dict[str, Any] = {
    "scout": _readout_scout,
    "interrogator": _readout_interrogator,
    "spec_writer": _readout_spec_writer,
    "planner": _readout_spec_writer,
    "spec_checker": _readout_spec_checker,
    "architect": _readout_architect,
    "plan_checker": _readout_plan_checker,
    "oracle": _readout_oracle,
    "workers": lambda k: _units_readout(k, "worker", "Files it wrote"),
    "integrator": _readout_integrator,
    "gates": _readout_gates,
    "breaker": _readout_breaker,
    "qa": _readout_qa,
    "review": _readout_review,
    "arbiter": _readout_arbiter,
    "repairers": lambda k: _readout_repair_plan(k)
                          + _units_readout(k, "repair", "Files it changed"),
}


def step_readout(name: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What one turn of `name` produced, small enough to travel with the log.

    A station's work is recorded under its own kinds, minutes before or after
    the `step` record that frames it, and nothing tied the two together: the
    console could say a turn of the gates passed six of nineteen checks and not
    which six. `records` are the ledger records that fall inside this turn --
    which is why this is per *turn* and not per station: the gates run again
    every repair round, and round 1's result is not round 0's.
    """
    build = _READOUTS.get(name)
    if not build:
        return []
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_kind.setdefault(record["kind"], []).append(record)
    try:
        return [s for s in build(by_kind) if s["entries"]]
    except Exception:
        # A readout is a convenience on top of the ledger. A payload from an
        # older run with a field this does not expect must not take the log
        # down with it -- the row simply opens onto its calls, as before.
        return []


def readouts_for(records: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Every step record in a ledger, against what its turn produced.

    Which records belong to a turn is settled the same way the console settles
    which model calls do: by the window the step itself declares. A station's
    records are written while it runs, and the tolerances take in the ones
    written on the same second as its first or last breath.
    """
    def when(stamp: Any) -> float:
        try:
            return datetime.fromisoformat(str(stamp)).timestamp()
        except (TypeError, ValueError):
            return 0.0

    steps = [r for r in records if r.get("kind") == "step" and isinstance(r.get("payload"), dict)]
    if not steps:
        return {}
    wanted = {k for s in steps for k in READOUT_KINDS.get(s["payload"].get("name", ""), ())}
    stamped = [(when(r.get("at")), r) for r in records if r.get("kind") in wanted]
    out = {}
    for step in steps:
        payload = step["payload"]
        kinds = READOUT_KINDS.get(payload.get("name", ""), ())
        if not kinds:
            continue
        started = when(payload.get("started_at")) - 1
        ended = when(payload.get("ended_at")) + 2 if payload.get("ended_at") else float("inf")
        mine = [r for at, r in stamped if r["kind"] in kinds and started <= at <= ended]
        readout = step_readout(payload.get("name", ""), mine)
        if readout:
            out[step["seq"]] = readout
    return out
