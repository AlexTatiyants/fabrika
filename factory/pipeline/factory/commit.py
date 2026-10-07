"""Committing the spec and the packet, and the as-built reading of what changed.
"""

from __future__ import annotations

import asyncio

from ...asbuilt import ProjectAsBuilt, feature_reading
from ...config import ConfigError
from ...asbuilt_graph import SystemChange, system_change
from ...packet_doc import packet_markdown, spec_markdown
from ...sandbox import Sandbox
from ...schemas import FeatureState, Packet, Spec
from ...store import EvidenceStore

from ..text import AS_BUILT_WAIT_S, _AS_BUILT_TASKS, _now
from ..records import current_payload


class CommitMixin:
    def _commit_spec(
        self, store: EvidenceStore, state: FeatureState, sandbox: Sandbox,
        *, verdict: str, note: str,
    ) -> None:
        """The frozen spec onto the branch, when a human accepts the work.

        Written at the verdict rather than at the build, because until then
        nobody has agreed that this is what was wanted -- a spec sitting on a
        branch that was later rejected would be a proposal filed as though it
        were a decision.

        Never raises, for the reason `_commit_packet` does not: this runs after
        the human has already ruled, and a ruling has to land whatever becomes
        of a document.
        """
        template = (self.config.pipeline.spec_file or "").strip()
        if not template:
            return
        relative = template.format(feature_id=state.feature_id)
        if not sandbox.exists():
            # `Sandbox.write` makes the directories it needs, which on a
            # checkout that is gone conjures one -- and a sandbox that exists
            # again is one `release` will then try to remove with git, in a
            # repository that may itself no longer be there. Say so instead.
            store.append("spec_file", {
                "path": relative, "commit": "", "branch": sandbox.branch,
                "problem": "the checkout is gone, so nothing could be written to the branch.",
            }, role="orchestrator", spec_hash=state.spec_hash)
            return
        try:
            # The spec as corrected, not the first one recorded: a correction
            # voids what was built on the reading it corrected.
            payload = current_payload(store.records(), "spec")
            if not payload:
                return
            spec = Spec.model_validate(payload)
            body = spec_markdown(
                spec,
                feature_id=state.feature_id,
                spec_hash=state.spec_hash,
                verdict=verdict,
                note=note,
                at=_now(),
                branch=sandbox.branch,
            )
            sandbox.write(relative, body)
            sha, problem = sandbox.commit_file(
                relative, f"factory: accepted spec for {spec.title}\n\nspec {state.spec_hash}")
            if sha:
                sandbox.state.commit_sha = sha      # the tip, as `_commit_packet` keeps it
                state.sandbox = sandbox.state
        except Exception as exc:                    # a document must not lose a ruling
            sha, problem = "", f"{type(exc).__name__}: {exc}"[:400]
        store.append("spec_file", {
            "path": relative, "commit": sha, "problem": problem,
            "branch": sandbox.branch,
        }, role="orchestrator", spec_hash=state.spec_hash)

    # -- the as-built: what a feature changed in the system -----------------

    def _as_built_begin(self, store: EvidenceStore, sha: str, label: str) -> "asyncio.Task | None":
        """Start a reading at one of this feature's commits, alongside the run.

        Never on the feature's path and never fatal: the as-built is an account
        of the system for the person reviewing, not a check on the change. A
        commit already read under this label is not read again.
        """
        if not getattr(self.config.pipeline, "as_built_on_features", False) or not sha:
            return None
        for r in store.all("as_built"):
            if (r.get("payload") or {}).get("label") == label and r["payload"].get("commit") == sha:
                return None
        try:
            self.config.role("reader")
            self.config.role("cartographer")
        except ConfigError:
            return None
        reader = ProjectAsBuilt(self.config, self.llm)
        task = asyncio.create_task(reader.read_for_feature(self.project, store, sha, label))
        _AS_BUILT_TASKS.add(task)
        task.add_done_callback(_AS_BUILT_TASKS.discard)
        return task

    async def _as_built_change(self, store: EvidenceStore, state: FeatureState, sandbox: Sandbox,
                               base_task: "asyncio.Task | None") -> SystemChange | None:
        """The feature's head read against its base, compared. None when either
        reading is missing -- the packet then simply has no such section."""
        try:
            head_sha = sandbox.head()
            head_task = self._as_built_begin(store, head_sha, "head")
            waits = [t for t in (base_task, head_task) if t is not None]
            if waits:
                await asyncio.wait(waits, timeout=AS_BUILT_WAIT_S)
            base, head = feature_reading(store, "base"), feature_reading(store, "head")
            if base is None or head is None or head.commit != head_sha:
                return None
            change = system_change(base, head)
            store.append("system_change", change, role="orchestrator", spec_hash=state.spec_hash)
            return change
        except Exception as exc:
            store.append("as_built_skipped", {"label": "change", "error": f"{type(exc).__name__}: {exc}"[:500]},
                         role="orchestrator")
            return None

    def _commit_packet(
        self, store: EvidenceStore, state: FeatureState, spec: Spec,
        sandbox: Sandbox, packet: Packet, system_change: SystemChange | None = None,
    ) -> None:
        """Write the packet onto the branch it argues about, in its own commit.

        Never raises. This runs after the work is finished and committed, so
        every failure here is "the branch is good and one document did not land
        on it" -- which belongs in the ledger as a fact, not in the traceback of
        a run that succeeded.
        """
        template = (self.config.pipeline.packet_file or "").strip()
        if not template:
            return
        relative = template.format(feature_id=state.feature_id)
        if not sandbox.exists():        # see `_commit_spec`: writing would conjure one
            store.append("packet_file", {
                "path": relative, "commit": "", "branch": sandbox.branch,
                "problem": "the checkout is gone, so nothing could be written to the branch.",
            }, role="orchestrator", spec_hash=state.spec_hash)
            return
        try:
            body = packet_markdown(
                packet,
                title=spec.title,
                feature_id=state.feature_id,
                spec_hash=state.spec_hash,
                branch=sandbox.branch,
                base_sha=sandbox.state.base_sha,
                # The tip after every repair round, read here rather than taken
                # from the first commit: a packet that named the build's commit
                # would point at the tree before the loop fixed anything.
                commit_sha=sandbox.head(),
                system=system_change.lines() if system_change is not None else None,
            )
            sandbox.write(relative, body)
            sha, problem = sandbox.commit_file(
                relative, f"factory: packet for {spec.title}\n\nspec {state.spec_hash}")
            if sha:
                # `commit_sha` means the tip the factory left the branch at, not
                # the commit the code arrived in -- the repair loop already moves
                # it every round. It has to keep meaning that: a rebuild writes
                # this sha down as the one command that gets a discarded attempt
                # back, and a tip it did not name is a commit nobody can reach.
                sandbox.state.commit_sha = sha
                state.sandbox = sandbox.state
        except Exception as exc:                # a rendering bug must not lose the run
            sha, problem = "", f"{type(exc).__name__}: {exc}"[:400]
        store.append("packet_file", {
            "path": relative, "commit": sha, "problem": problem,
            "branch": sandbox.branch,
        }, role="orchestrator", spec_hash=state.spec_hash)
