"""Repair rounds on a branch Fabrika owns, held to a fence.

The loop a look at outside work runs when it is sent back to Fabrika, made
only of the repair primitives a feature's loop is made of -- `_repair_round`,
`_recheck`, and the ledger's `apply_verdicts`, `revert_round`,
`settle_duplicates` and `exhaust_attempts` -- so a finding is repaired, checked
and settled the same way whichever kind of work it was found in. A feature's
own loop (`_converge`) keeps its own shape: it is woven into the blind suite,
the oracle and the review panel, which outside work does not have.

Each round: repair what is still open, run the fence, throw the round away if
anything that held before no longer does, and settle each finding -- one a
probe shows by the probe, the rest by the agent that raised it.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping, Sequence

from ...sandbox import Sandbox
from ...schemas import RepairVerdict, Spec
from ...store import EvidenceStore
from ..ledger import FindingLedger


class RoundsMixin:

    async def _repair_rounds(
        self, store: EvidenceStore, state: Any, spec: Spec, sandbox: Sandbox, ledger: FindingLedger, *,
        protected: list[str], fence: Callable[[], Awaitable[dict[str, Any]]],
        shows: Mapping[str, str], holds: Sequence[str], max_rounds: int, max_attempts: int,
        message: Callable[[list[str]], str] | None = None,
    ) -> list[dict[str, Any]]:
        """Up to `max_rounds` rounds; each one's account, in order.

        `fence()` runs what must hold and answers `{"checks": [{name, passed}],
        "probes": {path: exit}}`. `shows` maps a finding to the probe that
        shows it, which passes once it is fixed; `holds` are the probes that
        pass already and must go on passing.
        """
        rounds: list[dict[str, Any]] = []
        for n in range(1, max_rounds + 1):
            aimed = ledger.repairable(max_attempts)
            if not aimed:
                break
            before = sandbox.head()
            outputs, landed, refused, commit_sha, _plan, _unrun = await self._repair_round(
                store, state, spec, sandbox, ledger, aimed, protected, n, message=message)
            async with self._phase(store, state, "fix_fence") as phase:
                fenced = await fence()
                broke = [c["name"] for c in fenced["checks"] if not c["passed"]]
                broke += [path for path in holds if fenced["probes"].get(path, 0) != 0]
                passing = sum(1 for c in fenced["checks"] if c["passed"])
                turned = sum(1 for path in shows.values() if fenced["probes"].get(path, 1) == 0)
                phase.detail = (f"round {n}: {passing}/{len(fenced['checks'])} checks, "
                                f"{turned}/{len(shows)} failing probes now pass"
                                + (f"; broke {', '.join(broke)}, thrown away" if broke else ""))
            done = {"round": n, "aimed": aimed, "files": [f.path for f in landed], "refused": refused,
                    "broke": broke}
            if broke:
                # What held before holds after, or the round is not kept: a
                # fix that breaks something is not one.
                sandbox.reset_to(before)
                ledger.revert_round(n, "it broke " + ", ".join(broke))
                rounds.append({**done, "reverted": True})
                continue
            verdicts = [RepairVerdict(
                finding_id=fid, status="fixed" if fenced["probes"].get(path, 1) == 0 else "not_fixed",
                evidence=f"{path} {'passes' if fenced['probes'].get(path, 1) == 0 else 'still fails'}")
                for fid, path in shows.items() if fid in aimed]
            asked: dict[str, list[str]] = {}
            for fid in aimed:
                role = ledger.records[fid].role
                if fid not in shows and role in self.config.roles:
                    asked.setdefault(role, []).append(fid)
            if asked:
                context = "# The repairs that were made\n\n" + "\n\n".join(
                    f"## {o.unit_id}\n{o.summary}" for o in outputs)
                for _role, report in await self._recheck(store, state, sandbox, ledger, asked, context, n,
                                                          changed=[f.path for f in landed]):
                    verdicts += report.verdicts
            ledger.apply_verdicts(verdicts, n, commit_sha, max_attempts)
            ledger.settle_duplicates()
            ledger.exhaust_attempts(max_attempts)
            rounds.append({**done, "reverted": False})
        return rounds
