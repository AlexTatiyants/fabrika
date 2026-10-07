"""Small helpers the orchestrator uses on what workers hand back.
"""

from __future__ import annotations

import re
from typing import Sequence

from ..schemas import Decision, FileWrite, IntegrationReport, WorkerOutput


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _dedupe_writes(files: Sequence[FileWrite]) -> list[FileWrite]:
    """Later writers win, which means the integrator wins. Order is preserved."""
    index: dict[str, int] = {}
    out: list[FileWrite] = []
    for f in files:
        if f.path in index:
            out[index[f.path]] = f
        else:
            index[f.path] = len(out)
            out.append(f)
    return out


def _round_unit_id(unit_id: str, round_index: int) -> str:
    """A repair unit's name, made unique across rounds.

    `R-1` becomes `R1-1` in round 1 and `R2-1` in round 2 -- still obviously a
    repair unit, still ordered, and never two things with one name. A unit
    that does not look like a repairer's is left alone: the rename exists to
    separate names that repeat, and inventing one for anything else would break
    an id a human may already have written down.
    """
    name = (unit_id or "").strip()
    if not name or round_index < 1:
        return name or "R?"
    match = re.fullmatch(r"R-(\d+)", name)
    return f"R{round_index}-{match.group(1)}" if match else f"R{round_index}-{name}"


def _renumber_decisions(
    workers: Sequence[WorkerOutput], integration: IntegrationReport,
) -> list[Decision]:
    """Workers number decisions independently, so `D-1` collides across units.
    Namespace them by unit before anything downstream references an id."""
    out: list[Decision] = []
    owners: list[tuple[str, list[Decision]]] = [(w.unit_id or "U?", w.decisions) for w in workers]
    owners.append(("INT", integration.decisions))
    for unit_id, group in owners:
        for d in group:
            copy = d.model_copy(deep=True)
            copy.id = f"{unit_id}/{d.id}"
            out.append(copy)
    return out
