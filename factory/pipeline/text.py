"""Phase names, and the text helpers every prompt is assembled from.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from ..schemas import FeatureState, FileWrite, GateReport
from ..store import jsonable


# In the order they run, because the console draws them in this order and a
# person reads a list top to bottom as "this, then this". With the breaker
# listed after attribution and qa, a human watching it run beneath two rows
# marked "not reached" would reasonably conclude those two had been skipped.
# They have not: the breaker runs inside the gates' environment while
# its services are still up, attribution opens its own at the base commit once
# that environment is gone, and qa scores the criteria after both.
PHASE_NAMES = [
    "scout", "interrogator", "spec_writer", "spec_checker", "architect", "plan_checker",
    "workers", "integrator",
    "oracle", "gates", "breaker", "attribution", "qa", "review", "arbiter",
    "repairers", "simplifier", "rapporteur",
]


# Which of those belong to gate 1, and which to the build. The split exists
# because the two are invalidated by different things: answering the
# interrogator again discards the first group, and building again discards the
# second, and neither should clear the other's lights.
INTAKE_PHASES = ("scout", "interrogator", "spec_writer", "spec_checker")


BUILD_PHASES = tuple(n for n in PHASE_NAMES if n not in INTAKE_PHASES)


def _env_label(text: str) -> str:
    """A compose-project-safe name. Docker rejects most of what a unit id allows,
    and a rejected name reads as a broken environment rather than a bad string."""
    return (re.sub(r"[^a-z0-9]+", "-", (text or "unit").lower()).strip("-") or "unit")[:40]


def reset_phases(state: FeatureState, names: Sequence[str]) -> None:
    """Return these phases to pending, as though they had never run.

    A run that reuses or replaces earlier work inherits that work's phase
    records, and the console reads them literally: left alone, after a rebuild
    the strip shows fifteen of sixteen phases complete and "now at 05 workers",
    because every light from `integrator` rightwards is still lit by the
    attempt the rebuild has just discarded -- a progress bar describing a run
    that no longer exists.

    Cheap to get wrong and easy to miss, because nothing downstream reads these
    -- they are display only, so no test of the pipeline's output catches it and
    the only symptom is a human being told the wrong thing about where their run
    is. Which is the failure this whole system exists to prevent.
    """
    wanted = set(names)
    for phase in state.phases:
        if phase.name in wanted:
            phase.status = "pending"
            phase.started_at = phase.ended_at = phase.detail = phase.error = ""


VERDICT_RANK = {"accept": 0, "accept_with_changes": 1, "reject": 2}


#: How long review waits for a feature's as-built readings before going ahead
#: without the "what this changed in the system" section.
AS_BUILT_WAIT_S = 600


#: Readings running alongside a feature. Held here so the event loop does not
#: drop a task nobody awaits -- the kickoff reading outlives the intake that
#: started it.
_AS_BUILT_TASKS: set = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(text: str, limit: int = 32) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "feature").lower()).strip("-")
    return (s[:limit].strip("-") or "feature")


def _json(value: Any, limit: int = 0) -> str:
    text = json.dumps(jsonable(value), indent=2, ensure_ascii=False)
    if limit and len(text) > limit:
        text = text[:limit] + "\n... [truncated]"
    return text


def gate_status_line(gates: GateReport) -> str:
    """The gates as a table, computed, ahead of anything a model might say.

    The gates as JSON alone are not enough. Five gates each carrying a
    4,000-character output tail do not fit in `_json(gates, 10_000)`, so the
    later ones are simply cut off -- and a rapporteur handed only that has
    opened its packet with "all four gates still red after three repair rounds"
    when two of them had been green since round 1, while the console, computing
    from the same report, said 3/5 on the same screen.

    That headline is the first thing a human reads. It cannot be left to a model
    reading a truncated JSON blob, so it is stated here, first, in a form small
    enough that nothing can push it out of the prompt.
    """
    if not gates.results:
        return "(no gates are configured for this project)"
    rows = []
    for g in gates.results:
        mark = "PASS" if g.passed else "FAIL"
        note = ""
        if not g.started:
            note = " (never started -- proves nothing either way)"
        elif not g.passed and g.failed_on == "blind_tests":
            note = (" (only because this feature's blind tests fail -- the criterion "
                    "table says which; not a separate problem)")
        elif not g.passed and g.failed_on == "seam_tests":
            note = (" (only because of a seam check the integrator wrote -- the "
                    "integrator's to fix, not the implementation's)")
        elif not g.passed and g.failed_on == "oracle_files":
            note = (" (only because of files the oracle wrote -- the oracle's to fix, "
                    "not the implementation's)")
        elif not g.passed and g.at_base == "failed":
            note = " (and failed identically at the base commit: not this feature's)"
        elif not g.passed and g.at_base == "passed":
            note = " (passed at the base commit: this feature broke it)"
        rows.append(f"- {mark}  {g.name}{note}")
    passed = sum(1 for g in gates.results if g.passed)
    return (
        f"{passed} of {len(gates.results)} gates passed.\n\n" + "\n".join(rows)
    )


def files_section(files: Sequence[FileWrite], budget: int = 90_000) -> str:
    """Written files, complete where the budget allows, truncated where it does not."""
    parts: list[str] = []
    used = 0
    for f in files:
        body = f.contents
        note = ""
        if used + len(body) > budget:
            body = body[: max(0, budget - used)]
            note = "\n... [truncated]"
        if f.deleted:
            parts.append(f"----- {f.path} (deleted) -----\n")
            continue
        parts.append(f"----- {f.path} ({f.line_count} lines) -----\n{body}{note}\n")
        used += len(body)
        if used >= budget:
            parts.append(f"[{len(files) - len(parts)} further file(s) omitted: context budget]")
            break
    return "\n".join(parts) if parts else "(no files were written)"


# Characters that appear in prose and effectively never in a repo-relative path
# a model could have written. A finding's `files` entry is free text: one can
# read `backend/alembic/versions/ (no new file)`, which is a sentence, not a
# path.
_PROSE_IN_PATH = re.compile(r"[()\[\]{}<>*?\"']|\s")


def classify_claimed_path(
    root: Path, claimed: str, removed_prefixes: Sequence[str] = (),
) -> tuple[str, Path | None]:
    """What a model-written path actually is, before anything is concluded from it.

    Findings carry a `files` list the model wrote, and the re-check renders those
    paths off disk so a verdict can be settled against the checkout rather than
    against a repairer's summary. That is right. What would be wrong is treating
    every string in the list as a path, and every failure to read one as proof
    the file is missing.

    One reviewer wrote its entry as `backend/alembic/versions/ (no new file)`
    -- a description, not a path. Read as one, `read_text` raised, the prompt
    said "[no such file in the working tree]", the role prompt said that
    settles it against the repair, and a blocker stayed on a migration that was
    on disk the whole time, for three rounds and into the packet.

    So the failures are separated. `absent` is a claim about the repository and
    may be reasoned from. `not_a_path`, `directory` and `removed_by_design` are
    claims about the *finding*, and nothing about the code follows from them.
    """
    text = (claimed or "").strip()
    if not text:
        return "not_a_path", None
    if _PROSE_IN_PATH.search(text):
        return "not_a_path", None
    # A trailing slash is a directory the model meant, not a malformed path.
    # It routes to the directory branch, which lists what is actually there --
    # far more use to a re-check than either "absent" or "not a path".
    text = text.rstrip("/") or "."

    rel = text.lstrip("./")
    for prefix in removed_prefixes:
        prefix = (prefix or "").strip("/")
        if prefix and (rel == prefix or rel.startswith(prefix + "/")):
            return "removed_by_design", None

    try:
        target = (root / text).resolve()
    except (OSError, ValueError):
        return "not_a_path", None
    if not target.is_relative_to(root):
        return "outside", None
    if target.is_dir():
        return "directory", target
    if not target.exists():
        return "absent", target
    return "read", target


def tree_section(root: str | Path, paths: Sequence[str],
                 budget: int = 60_000, per_file: int = 12_000,
                 removed_prefixes: Sequence[str] = ()) -> str:
    """Named files as they are on disk right now.

    The counterpart to `files_section`, which renders what an agent reported
    writing. This renders what is there -- including nothing, where a file an
    agent claims to have written is absent. The distinction is the whole point:
    a round's diff cannot show that a defect was already fixed two rounds ago,
    and an agent handed only the diff concluded three times that a class deleted
    in round 1 was still present in round 3.

    Paths come from findings, which are model-written. Every one of them goes
    through `classify_claimed_path` first, because "this string could not be
    read as a file" and "this file is not in the repository" are different
    facts and only the second one says anything about the code.
    """
    base = Path(root).resolve()
    parts: list[str] = []
    used = 0
    ordered = list(dict.fromkeys(p for p in paths if p))
    for i, path in enumerate(ordered):
        if used >= budget:
            parts.append(f"[{len(ordered) - i} further file(s) omitted: context budget]")
            break
        kind, target = classify_claimed_path(base, path, removed_prefixes)

        if kind == "not_a_path":
            parts.append(
                f"----- {path} -----\n[this is not a file path, so it was not read. It says "
                f"nothing about what is or is not in the repository -- do not conclude from "
                f"it that any file is missing.]\n")
            continue
        if kind == "directory":
            listing = sorted(
                q.relative_to(base).as_posix()
                for q in target.rglob("*") if q.is_file()
            )[:40] if target else []
            parts.append(
                f"----- {path} -----\n[a directory, not a file. It contains: "
                + (", ".join(listing) if listing else "(nothing)") + "]\n")
            continue
        if kind == "removed_by_design":
            parts.append(
                f"----- {path} -----\n[an adversarial probe. The breaker's tests are deleted "
                f"from the tree after they run, on purpose, so that the diff a human reviews is "
                f"the feature and not the attack surface. Its absence here is the harness "
                f"working as intended and is not evidence about the code.]\n")
            continue
        if kind == "outside":
            parts.append(f"----- {path} -----\n[outside the checkout; not read]\n")
            continue

        assert target is not None
        if kind == "absent":
            # Said plainly, because for half of these findings this *is* the
            # answer: the file the repair claims to have written is not there.
            parts.append(f"----- {path} -----\n[no such file in the working tree]\n")
            continue
        try:
            body_text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            parts.append(f"----- {path} -----\n[unreadable: {exc}]\n")
            continue
        lines = body_text.count("\n") + 1
        note = ""
        if len(body_text) > per_file or used + len(body_text) > budget:
            body_text = body_text[: max(0, min(per_file, budget - used))]
            note = "\n... [truncated]"
        parts.append(f"----- {path} ({lines} lines) -----\n{body_text}{note}\n")
        used += len(body_text)
    return "\n".join(parts) if parts else "(the findings name no files)"


def _first_lines(text: str, n: int) -> str:
    """The head of an error, without the terminal's colour codes."""
    plain = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return "\n".join(line.strip() for line in plain.strip().splitlines()[:n] if line.strip())
