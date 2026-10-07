"""The ledgers, as ledgers: a project's records and a feature's, and one exchange
in full.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException

from ..config import ConfigError

from .context import Context
from .readouts import readouts_for


#: Record kinds whose payload travels with the feature log itself: small, and
#: read in full by the console for every record of the kind. `step` is every
#: run of a station; without it the call tree falls back to the phase list, one
#: row per name, and a second round's steps erase the first's. The three
#: that start a run carry what started it, which the console names each run by.
INLINE_LOG_KINDS = frozenset({"call", "step", "dispatch", "rebuild", "revalidate", "preview"})


def _billing_known(cfg, record: dict[str, Any]) -> Any:
    """A call record with the question "was this money?" already answered.

    `call_entry` answers it when it writes the row. Older rows carry the figure
    the route reported and nothing about whether anybody was charged it -- and
    on a plan those are different numbers. Left unanswered the console has to
    guess, and guessing that a reported figure is spend puts dollars of
    subscription turns ($6.16, in one case) in a column headed Cost.

    Answered here, at read time, from the same config the run was accounted
    under, so one ledger does not read two ways depending on when its rows were
    written. A route since renamed or dropped cannot be asked, and stays
    unanswered rather than being guessed at.
    """
    payload = record.get("payload")
    if record.get("kind") != "call" or not isinstance(payload, dict):
        return payload
    if "billed" in payload:
        return payload
    try:
        route = cfg.route(str(payload.get("route") or ""))
    except Exception:
        return payload
    reported = float(payload.get("cost_usd") or 0.0)
    return {
        **payload,
        "billed": route.billed,
        "cost_usd": reported if route.billed else 0.0,
        "notional_usd": 0.0 if route.billed else reported,
    }


def register(app: FastAPI, ctx: Context) -> None:
    """The project and feature logs."""
    cfg = ctx.cfg
    project_or_404 = ctx.project_or_404
    store_or_404 = ctx.store_or_404

    @app.get("/api/projects/{project_id}/log")
    def project_log(project_id: str) -> list[dict[str, Any]]:
        """A project has a ledger too, and this serves it.

        The survey, the baseline and every approval are recorded in full, but
        `get_project` returns only their metadata -- so without this, diagnosing
        a red gate means opening the JSONL by hand. A gate's output tail is four thousand
        characters and it is the only thing that says *why* it failed.
        """
        project = project_or_404(project_id)
        out = []
        for record in project.store:
            payload = record.get("payload")
            out.append({
                "seq": record["seq"],
                "at": record["at"],
                "kind": record["kind"],
                "role": record.get("role", ""),
                "model": record.get("model", ""),
                "meta": record.get("meta") or {},
                "payload_chars": len(json.dumps(payload, default=str)) if payload is not None else 0,
            })
        return out

    @app.get("/api/projects/{project_id}/log/{seq}")
    def project_log_record(project_id: str, seq: int) -> dict[str, Any]:
        project = project_or_404(project_id)
        record = next((r for r in project.store if r["seq"] == seq), None)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no record {seq}")
        return record

    @app.get("/api/projects/{project_id}/features/{feature_id}/log")
    def feature_log(project_id: str, feature_id: str) -> list[dict[str, Any]]:
        """The ledger, as the ledger. Every record in write order, with the size
        of each half of the exchange, so a run can be read back after the fact."""
        store = store_or_404(project_id, feature_id)
        records = list(store)
        # What each turn of a station produced, beside the `step` record that
        # says how the turn went. Derived at read time rather than written by
        # the pipeline, so a ledger from before any of this existed opens onto
        # its work too. See `readouts_for`.
        readouts = readouts_for(records)
        out = []
        for record in records:
            meta = record.get("meta") or {}
            payload = _billing_known(cfg, record)
            out.append({
                "seq": record["seq"],
                "at": record["at"],
                "kind": record["kind"],
                "role": record.get("role", ""),
                "model": record.get("model", ""),
                "meta": meta,
                "prompt_chars": meta.get("prompt_chars", 0),
                "has_prompt": bool(meta.get("prompt_file")),
                "payload_chars": len(json.dumps(payload, default=str)) if payload is not None else 0,
                # A call record is a few hundred characters and the console
                # draws every one of them -- the chronological call log and the
                # models each station used. Without it inline, both read an
                # absent payload and drew nothing, with nothing to say so.
                **({"payload": payload} if record["kind"] in INLINE_LOG_KINDS else {}),
                **({"readout": readouts[record["seq"]]} if record["seq"] in readouts else {}),
            })
        return out

    @app.get("/api/projects/{project_id}/features/{feature_id}/log/{seq}")
    def feature_log_record(project_id: str, feature_id: str, seq: int) -> dict[str, Any]:
        """One exchange, both halves."""
        store = store_or_404(project_id, feature_id)
        record = next((r for r in store if r["seq"] == seq), None)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no record {seq}")

        prompt = ""
        relative = (record.get("meta") or {}).get("prompt_file")
        if relative:
            target = store.dir / relative
            if target.exists():
                prompt = target.read_text(encoding="utf-8", errors="replace")

        system = ""
        role = record.get("role") or ""
        if role and role in cfg.roles:
            try:
                system = cfg.role_prompt(role)
            except ConfigError:
                system = ""

        return {**record, "prompt": prompt, "system": system}
