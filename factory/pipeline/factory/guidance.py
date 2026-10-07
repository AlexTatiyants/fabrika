"""Prompts and the repository's guides, as each agent is handed them (INV-10).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence

from ...schemas import FeatureState, Finding, ScoutReport
from ...store import EvidenceStore


class GuidanceMixin:
    # -- prompts (INV-10: versioned files, loaded at call time) ------------

    def role_prompt(self, name: str) -> str:
        return self.config.role_prompt(name)

    def _base_of(self, state: FeatureState | None) -> str:
        return ((state.sandbox.base_sha if state is not None and state.sandbox else "")
                or self.project.base_ref)

    def _guides_at(self, ref: str, repo: str | Path | None = None) -> list[Any]:
        """The guides committed at `ref`: what binds there. Read by code from
        the repository, and remembered per commit, because a commit's files do
        not change and every role in a feature asks."""
        from ... import guides as guide_lib

        key = (str(repo or self.project.repo_path), ref)
        cache = self.__dict__.setdefault("_guide_cache", {})
        if key not in cache:
            cache[key] = guide_lib.found(key[0], ref)
        return cache[key]

    def _verify_citations(self, store: EvidenceStore, state: FeatureState,
                          findings: Sequence[Finding]) -> list[Finding]:
        """Each finding's cited rule, checked against the guide at the base commit."""
        from ... import guides as guide_lib

        base = self._base_of(state)
        binding = {g.path for g in self._guides_at(base)}
        observed: set[str] = set()
        if store.has("scout"):
            observed = {c.slug for c in ScoutReport.model_validate(
                store.payload("scout")).observed_conventions}
        return guide_lib.verify_citations(
            findings, lambda path: guide_lib.read_at(self.project.repo_path, base, path),
            binding, observed)

    def _oracle_guides(self, store: EvidenceStore, state: FeatureState) -> str:
        """The verify lane's guides: the testing sections of the repository's
        instruction files, and nothing a model inferred from the code. The only
        shape of guide text allowed to reach the oracle --
        `test_v1_the_lane_is_called_with_the_spec_alone` pins this call."""
        return self._guidance(store, state, "oracle", inferred=False)

    def _inferred(self, store: EvidenceStore) -> tuple[list[Any], list[str]]:
        if not store.has("scout"):
            return [], []
        scout = ScoutReport.model_validate(store.payload("scout"))
        return list(scout.observed_conventions), list(scout.do_not_duplicate)

    def _guidance(self, store: EvidenceStore, state: FeatureState | None, role: str,
                  files: Sequence[str] | None = None, *, inferred: bool = True,
                  repo: str | Path | None = None, ref: str = "") -> str:
        """"How this repository is written", built by code for a role with no harness.

        The repository's guides that role is handed -- instruction files that
        cover the files in front of it, DESIGN.md for what a person sees,
        skills by what they are for -- as they stood at the feature's base
        commit; then, unless `inferred` is off, the conventions the scout
        observed where no guide speaks and what it found must not be rebuilt.
        The verify lane passes it off: nothing a model read in the code may
        reach it. What was handed is recorded, with each file's hash.
        """
        from ... import guides as guide_lib

        text, delivered = self._guide_text(state, role, files, repo=repo, ref=ref)
        observed, dnd = self._inferred(store) if inferred else ([], [])
        if delivered:
            store.append("guides_delivered", {"role": role, "files": list(files or [])[:40],
                                              "guides": delivered}, role="orchestrator")
        return guide_lib.section(text, observed, dnd)

    def _guide_text(self, state: FeatureState | None, role: str,
                    files: Sequence[str] | None = None, *, repo: str | Path | None = None,
                    ref: str = "") -> tuple[str, list[dict[str, Any]]]:
        """The guides one role is handed, as text, and what that text holds."""
        from ... import guides as guide_lib

        base = ref or self._base_of(state)
        where = repo or self.project.repo_path
        chosen = guide_lib.select(self._guides_at(base, where), role, files)
        return guide_lib.render(where, base, chosen, self.config.guides.budget_chars)

    def _unit_guidance(self, store: EvidenceStore, state: FeatureState | None, role: str,
                       files: Sequence[str] | None = None) -> dict[str, Any]:
        """Everything a unit needs to be told the repository's rules, for
        whichever executor takes it.

        Which runner that is, is known only when the session starts -- a
        route, or a fallback to another -- so the executor decides: a harness
        is bridged to the files it does not read and told where the rest is;
        an executor with no harness gets the guides' text, as any model role
        does. Both get what the scout inferred. Nothing is recorded here: the
        executor says what was actually handed, through `on_guides`.
        """
        from ... import guides as guide_lib

        text, delivered = self._guide_text(state, role, files)
        observed, dnd = self._inferred(store)
        return {"guidance": guide_lib.section(text, observed, dnd),
                "guidance_delivered": delivered,
                "harness_guidance": guide_lib.section("", observed, dnd),
                "guide_files": [g.model_dump(mode="json")
                                for g in self._guides_at(self._base_of(state))]}

    def _guides_recorder(self, store: EvidenceStore, role: str, files: Sequence[str] | None
                         ) -> Callable[[dict[str, Any]], None]:
        """Where an executor says which of the repository's guides the agent
        had: handed as text, loaded by its own harness, or bridged to it."""
        def record(payload: dict[str, Any]) -> None:
            store.append("guides_delivered", {"role": role, "files": list(files or [])[:40],
                                              **payload}, role="orchestrator")
        return record
