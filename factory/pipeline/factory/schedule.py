"""The plans a run draws on: which routes it needs, what they have left, and
holding a build until a window resets.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ...schemas import FeatureState
from ...store import EvidenceStore

log = logging.getLogger(__name__)


class ScheduleMixin:
    @property
    def routes(self):
        """The same route monitor the console uses, for the same meter store.

        Built lazily and shared, because the gauge is per account rather than
        per run: two features building at once draw on one plan, and so does the
        human at the next terminal. A monitor per run would let each of them
        believe it had the whole thing.
        """
        if self._routes is None:
            from ...routes import RouteMonitor
            self._routes = RouteMonitor(self.config, self.llm)
        return self._routes

    def routes_in_use(self) -> list[str]:
        """The routes this run's enabled agents will actually call."""
        default = self.config.default_route
        seen: list[str] = []
        for role in self.config.roles.values():
            if getattr(role, "enabled", True) is False:
                continue
            name = getattr(role, "route", "") or default
            if name and name not in seen:
                seen.append(name)
        return seen

    async def plan_verdict(self) -> Any:
        """Whether the plans this run needs can carry it. See `routes.PlanVerdict`."""
        return await self.routes.plan_verdict(self.routes_in_use())

    def defer_build(self, feature_id: str, verdict: Any) -> FeatureState:
        """Freeze the spec and hold the build until the window resets.

        The intent goes on the feature and into the evidence, not only into the
        sleeping task that will run it. A scheduled run a restart forgets is
        worse than one that was never scheduled, because the human was told it
        would happen and stopped watching.
        """
        store = self.store_for(feature_id)
        state = self.state_of(store)
        state.stage = "waiting_for_plan"
        state.start_at = float(getattr(verdict, "start_at", 0.0) or 0.0)
        state.waiting_reason = str(getattr(verdict, "reason", "") or "")
        state.error = ""
        store.append("deferred", verdict.as_dict(), role="orchestrator",
                     spec_hash=state.spec_hash)
        self._save(store, state)
        self._emit(state)
        return state

    def cancel_deferral(self, feature_id: str) -> FeatureState:
        """Stop waiting. The spec stays frozen; only the schedule is dropped."""
        store = self.store_for(feature_id)
        state = self.state_of(store)
        if state.stage != "waiting_for_plan":
            return state
        state.stage = "awaiting_spec_approval"
        state.start_at = 0.0
        state.waiting_reason = ""
        store.append("deferral_cancelled", {"at": time.time()}, role="human",
                     spec_hash=state.spec_hash)
        self._save(store, state)
        self._emit(state)
        return state

    def deferred_features(self) -> list[FeatureState]:
        """Every feature of this project that is waiting on a plan.

        Read at boot. The sleeping task died with the process that held it; this
        is how the promise outlives it.
        """
        out: list[FeatureState] = []
        for feature_id in self.project.feature_ids():
            try:
                state = self.state_of(self.store_for(feature_id))
            except Exception:
                # Unreadable, so not re-armed: a build somebody was promised
                # would start, which now will not unless this is seen.
                log.warning("could not read the state of %s; if it was scheduled, it is not "
                            "re-armed", feature_id, exc_info=True)
                continue
            if state.stage == "waiting_for_plan":
                out.append(state)
        return out

    async def record_headroom(self, store: EvidenceStore, state: FeatureState,
                              when: str = "before") -> dict[str, Any]:
        """What every plan this run needs has left, before anything is committed.

        Read here rather than inferred later, and read *before* the build,
        because this is what decides whether the work can be scheduled and
        finish. A run that starts with a plan nearly out does not fail cleanly
        -- it fails wherever it happens to be: one run lost its entire review
        panel in one moment, on the last pass, with the packet reporting
        nothing about it.

        Mostly free. A route whose tool writes its windows into a session log is
        read off disk; only one that reports them inside a call, and whose
        window has since reset, spends the probe. Never fatal: a gauge is worth
        one turn and is never worth the build.
        """
        wanted = self.routes_in_use()
        gauges: dict[str, Any] = {}
        for name in wanted:
            try:
                if when == "before":
                    await self.routes.gauge_live(name)
                else:
                    # Free only. A finished run does not need the closing number
                    # badly enough to spend a turn on it, and a draw computed
                    # from readings this run itself paid for would include them.
                    self.routes.gauge(name)
            except Exception:
                log.warning("could not read the gauge of route %s", name, exc_info=True)
        for name in wanted:
            windows = self.routes._meters.latest(name)
            route = self.config.routes.get(name)
            gauges[name] = {
                "windows": [w.as_dict() for w in windows],
                "credits": self.routes._meters.credits(name),
                "current": self.routes.gauge_is_current(name),
                # Whether a rolling window bounds this route at all. A route
                # billed per token has none and is covered by the budget guard
                # instead -- "no window reading" there is not an unknown, it is
                # a route that is not measured this way.
                "windowed": bool(route is not None and route.meter_windows_key),
            }
        store.append("headroom", {"when": when, "routes": gauges}, role="orchestrator",
                     spec_hash=state.spec_hash, meta={"when": when})
        return gauges
