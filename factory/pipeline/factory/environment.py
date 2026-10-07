"""Authoring: the environment an agent writes in, files to disk, and the account
it gives of them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from pydantic import BaseModel

from ...config import session_fallback
from ...containers import docker_available, low_disk
from ...unitenv import unit_environment
from ...executors import Authored, CommandExecutor
from ...projects import ProjectError
from ...sandbox import Sandbox
from ...schemas import FeatureState
from ...store import EvidenceStore
from ...workspace import BaseTree, WorktreeError, repo_digest

from ..text import _env_label


class EnvironmentMixin:
    # ---- authoring: files to disk, account to the model ------------------

    def env_recorder(self, store: EvidenceStore, state: FeatureState, *,
                     unit: str, role: str, round_index: int = 0):
        """Write down whether an authoring session got the stack it asked for.

        Passed as `on_environment` to every agent that writes code. The record
        is appended before the session runs, so a unit that then crashes or
        hangs still leaves behind the answer to "could it have run anything?" --
        which is usually the first question worth asking about it.
        """
        def record(prepared) -> None:
            store.append("unit_env", {
                "unit": unit,
                "role": role,
                "round": round_index,
                "ready": bool(getattr(prepared, "ready", False)),
                "problem": getattr(prepared, "problem", "") or "",
                "log": getattr(prepared, "log", "") or "",
                "report": getattr(prepared, "report", "") or "",
            }, role="orchestrator", spec_hash=state.spec_hash,
                meta={"unit_id": unit, "round": round_index,
                      "ready": bool(getattr(prepared, "ready", False))})
        return record

    def _digest_before(self, state: FeatureState, sandbox: Sandbox) -> str:
        """The repository as it was before this feature, read from the base commit.

        Not from the sandbox. On a fresh build the two are the same tree; on a
        resume the sandbox already holds every earlier attempt's work, and this
        digest reaches every review panel under the heading "as it was before
        this work". A resume that began before the oracle fixed its conftest
        handed three panels the unfixed copy there, beside a ledger saying it
        was fixed -- and they reported the repair as faked, as the packet's
        first blocker, about a file that was correct.

        Falls back to the sandbox only where no base was ever recorded, which is
        a sandbox from before base commits were kept.
        """
        budget = self.project.state.digest_budget
        name = Path(self.project.repo_path).name
        base = (state.sandbox.base_sha if state.sandbox else "") or ""
        if base:
            try:
                with BaseTree(self.project.repo_path, base,
                              label=f"digest-{state.feature_id}") as tree:
                    tree.assert_at_base()
                    assert tree.path is not None
                    return repo_digest(tree.path, budget, name=name)
            except WorktreeError:
                pass
        return repo_digest(sandbox.path, budget, name=name)

    def unit_env(self, label: str, role: str = "worker", serve: bool = True):
        """An environment factory for one authoring unit, or nothing.

        `label` names the compose project, and a compose project is the
        isolation: its own containers, its own network, its own volumes. So a
        label that is unique per concurrently-running unit is the whole of what
        keeps three workers from migrating each other's database -- and a label
        that is reused is a shared database wearing a private one's costume.
        Callers pass `<feature>-<unit>`, plus the round where there is one.

        `role` is who will author in it. It decides nothing about the stack and
        one thing about the image: a role whose route runs its sessions inside
        the container needs that harness present in the image, and the role is
        the only thing that knows which harness that is.

        Returns `None` under the `direct` executor, which has no checkout to
        prepare, and for a host-kind project, whose environment is the host.
        """
        if not self.harness_authoring():
            return None
        spec = self.project.environment
        if spec is None or spec.kind == "host":
            return None
        try:
            route = self.config.route_for(role)
        except Exception:
            route = None
        fallback = session_fallback(self.config, role)

        def factory(tree: Path):
            return unit_environment(
                self.project, self.config, label=_env_label(label), tree=tree,
                serve=serve, harness_route=route, fallback_route=fallback)

        return factory

    async def require_authoring_environment(self) -> None:
        """Refuse to build when the agents would have nothing to run code in.

        Three ways this project can fail to give an authoring agent a place to
        execute what it writes, and all three produce the same run: code that
        was never compiled, tests that were never collected, and a packet whose
        every claim is inference. The messages name the fix rather than the
        rule, because the person reading one is trying to start a build.

        Silent under the `direct` executor, which has no session to place
        anywhere. There is no other exception: `require_unit_container: false`
        would put the agents on this machine, and is refused as a retired
        setting (`RETIRED_SETTINGS`).
        """
        if not self.harness_authoring():
            return
        spec = self.project.environment
        if spec is None:
            raise ProjectError(
                "this project has no environment, so an authoring agent would get a "
                "checkout with nothing to run: no dependencies, no database, none of "
                "its own commands -- and no sealed container to keep it in. Add one on "
                "the project's environment page."
            )
        if spec.kind == "host":
            raise ProjectError(
                "this project's environment is `host`, which gives an authoring agent "
                "no container of its own: it would run on this machine, where it can "
                "reach every server running here and every other feature's files. Give "
                "the project a container environment."
            )
        ok, detail = await docker_available(self.config.docker)
        if not ok:
            raise ProjectError(
                f"this project builds in a container and {detail} Until Docker "
                "answers, an authoring agent has nowhere to run what it writes."
            )
        # Measured before anything is spent, because a full disk fails a run
        # late and in disguise: every unit's database exits as it starts, and
        # the run reads as the project's setup commands failing. Checked again
        # where a stack fails to start, in case it filled up during the run.
        full = await low_disk(self.config.docker, self.config.docker.min_free_gb)
        if full:
            raise ProjectError(full)

    def _answered_by(self, role: str) -> str:
        """The model a record should be stamped with: the one that answered.

        Not the role's configured model, which is a fact about the
        configuration file and not about the work. When a route runs out and
        the role falls back, those diverge -- and a console reading the
        configured model off the stamp would name a model that made zero tool
        calls as the author of a suite a different model wrote. The configured
        one is only the answer when nothing has answered yet.
        """
        answered = getattr(self.llm, "answered", {}) or {}
        return answered.get(role) or self.config.role(role).model

    def harness_authoring(self) -> bool:
        """Whether the test-writing agents get a filesystem.

        They do whenever a real harness is configured. `direct` is the
        no-tooling executor -- it exists to prove the pipeline's shape without
        any CLI at all -- and under it these agents return file text inside
        one answer, ceiling and all.
        """
        return isinstance(self.executor, CommandExecutor)

    async def _author_files(
        self, *, role: str, brief: str, tree: Path, confine: str | Sequence[str],
        system: str = "", environment=None, on_environment=None,
    ) -> Authored:
        """One harness session that writes test files into `tree`.

        Every caller passes an `environment`: a session with none would run on
        this machine, and is refused where it would be spawned. The oracle's is
        `serve=False` -- sealed, set up, nothing running -- because a running
        instance is the one thing it must never have seen; the breaker's has
        everything, because watching its probes fail is its whole job.
        """
        assert isinstance(self.executor, CommandExecutor)
        return await self.executor.author(
            brief=brief, tree=tree, role=role, confine=confine, system=system,
            environment=environment, on_environment=on_environment)

    async def _account_for(
        self, *, role: str, system: str, brief: str, session: Authored,
        model: type[BaseModel], asked: str,
    ) -> tuple[BaseModel | None, str]:
        """Ask an agent what the files it just wrote are *for*.

        Everything a filesystem can answer has already been answered by reading
        it. What is left is the judgement only this role can make -- which
        criterion a test covers, what hypothesis a probe encodes -- and it is
        small enough that the ceiling on one answer, which whole files can hit,
        is out of reach.

        The files go back in as input, in full. A model tagging tests it can no
        longer see is guessing, and the whole reason this call exists is that
        its answer has to be true about the file on disk.
        """
        listing = "\n\n".join(
            f"----- {f.path} -----\n{f.contents}" for f in session.files)
        prompt = (
            brief
            + "\n\n---\n\n# The files you wrote\n\n"
            + "These are on disk now, exactly as shown. Do not restate them: they are "
            "already saved, and this answer is only about what they are for.\n\n"
            + listing
            + "\n\n---\n\n" + asked
        )
        try:
            answer = await self.llm.ask(role, prompt, model, system=system)
        except Exception as exc:
            return None, f"the files were written but {role} could not account for them: {exc}"
        return answer, ""
