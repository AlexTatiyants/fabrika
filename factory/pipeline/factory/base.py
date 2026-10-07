"""The orchestrator's shared core: construction, the store and the state, events
and the phase context every other part runs inside.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, Callable

from ...config import Config
from ...executors import Executor, build_executor
from ...llm import LLM, journal_calls, stop_journaling
from ...projects import Project
from ... import dependencies
from ...sandbox import Sandbox, SandboxError
from ...schemas import FeatureState, PhaseState
from ...store import EvidenceStore

from ..text import _now

log = logging.getLogger(__name__)

# ==========================================================================
# the factory
# ==========================================================================

ProgressCallback = Callable[[dict[str, Any]], None]


class FactoryBase:
    def __init__(
        self,
        config: Config,
        project: Project,
        llm: LLM | None = None,
        on_progress: ProgressCallback | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.config = config
        self.project = project
        self.llm = llm if llm is not None else LLM(config)
        self.on_progress = on_progress
        self.executor = executor if executor is not None else build_executor(config, self.llm)
        self._routes: Any = None
        # Asks OSV and the registries about new packages. A stub in the suite,
        # which never reaches the network.
        self.dependency_lookup: Any = dependencies.Lookup()
        self._dependency_facts: dict[tuple[str, str, str], Any] = {}

    @property
    def _project_files(self) -> dict[str, set[str]]:
        """Per feature: the project's own files inside its blind directories.

        What the directory rule of `is_protected` must not reach. Filled where
        the protected set is computed, read at every door a repair goes through.
        """
        return self.__dict__.setdefault("_project_files_by_feature", {})

    # -- store / state ----------------------------------------------------

    def store_for(self, feature_id: str) -> EvidenceStore:
        store = self.project.feature_store(feature_id)
        # Every agent exchange is written through this store, and what the
        # exchange cost is one of the facts about it. The client parked the
        # number when the call returned; the store takes it on the way past.
        # `getattr`, because the client is duck-typed: a double that cannot say
        # what a call cost simply reports none, and the records carry no cost.
        store.usage_of = getattr(self.llm, "take_usage", None)
        return store

    def state_of(self, store: EvidenceStore) -> FeatureState:
        payload = store.payload("state")
        if payload is None:
            raise KeyError(f"no state recorded for feature {store.feature_id!r}")
        return FeatureState.model_validate(payload)

    def open_sandbox(self, state: FeatureState) -> Sandbox:
        """Reopen the bounded environment recorded on this feature."""
        if state.sandbox is None:
            raise SandboxError(
                f"feature {state.feature_id!r} has no sandbox recorded. Every feature is built "
                "in its own worktree; there is no shared checkout to fall back to."
            )
        return Sandbox.reopen(state.sandbox, self.project.repo_path)

    def _save(self, store: EvidenceStore, state: FeatureState) -> None:
        state.updated_at = _now()
        store.append("state", state)

    def _emit(self, state: FeatureState, phase: PhaseState | None = None) -> None:
        if self.on_progress is None:
            return
        try:
            self.on_progress({
                "feature_id": state.feature_id,
                "stage": state.stage,
                "phase": phase.name if phase else "",
                "status": phase.status if phase else "",
                "error": phase.error if phase else "",
                "at": _now(),
            })
        except Exception:  # a broken UI listener must never stop the factory
            log.warning("a progress listener failed", exc_info=True)

    @staticmethod
    def _phase_state(state: FeatureState, name: str) -> PhaseState:
        for p in state.phases:
            if p.name == name:
                return p
        p = PhaseState(name=name)
        state.phases.append(p)
        return p

    @asynccontextmanager
    async def _phase(self, store: EvidenceStore, state: FeatureState, name: str):
        """AC-8.10 -- pending / running / done / failed, recorded and broadcast."""
        phase = self._phase_state(state, name)
        phase.status = "running"
        phase.started_at = _now()
        phase.ended_at = ""
        phase.error = ""
        # The previous attempt's detail must go with its error, or a phase reads
        # as "reused from the last attempt" while it is running for real.
        phase.detail = ""
        self._save(store, state)
        self._emit(state, phase)

        # Every model call made while this phase runs goes into this feature's
        # ledger, in the order it happened, tagged with the phase. Set here
        # because the client is shared across the whole server and cannot know
        # which feature it is serving -- the phase does, and the tasks it starts
        # inherit it. Read top to bottom these entries are the run, with no
        # badge to infer: a refused call is a row, and so is the substitute
        # that answered in its place.
        def write(entry: dict[str, Any]) -> None:
            store.append(
                "call", entry, role=entry.get("role") or "orchestrator",
                model=entry.get("answered") or entry.get("asked") or "",
                spec_hash=state.spec_hash, meta={"phase": name})

        # Every run of a step, kept. `state.phases` holds one row per name and
        # each run overwrites the last, so a second round's gates would erase
        # the first's -- and a view of the run as it happened would have nothing
        # to draw repair rounds from.
        def ran() -> None:
            store.append("step", {
                "name": name, "status": phase.status, "detail": phase.detail,
                "error": phase.error, "started_at": phase.started_at,
                "ended_at": phase.ended_at, "round": state.rework_round,
            }, role="orchestrator", spec_hash=state.spec_hash,
               meta={"round": state.rework_round})

        token = journal_calls(write)
        try:
            yield phase
        except Exception as exc:
            phase.status = "failed"
            phase.error = f"{type(exc).__name__}: {exc}"[:2000]
            phase.ended_at = _now()
            self._save(store, state)
            self._emit(state, phase)
            ran()
            raise
        finally:
            stop_journaling(token)
        phase.status = "done"
        phase.ended_at = _now()
        self._save(store, state)
        self._emit(state, phase)
        ran()
