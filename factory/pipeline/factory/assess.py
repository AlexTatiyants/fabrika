"""Assessment and attribution: the gates, seam checks and project checks run
against the branch, and whether a failure is this feature's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from ...containers import DockerError, runner_for
from ...gates import restore_tracked, run_gate, run_gates, tracked_changes, run_setup, test_session
from ... import git
from ...sandbox import Sandbox
from ...schemas import (
    BreakerReport,
    BreakerTest,
    FeatureState,
    FileWrite,
    GateReport,
    Gate,
    GateResult,
    IntegrationReport,
    OracleSuite,
    Spec,
    SupportFile,
)
from ...store import EvidenceStore
from ...workspace import (
    BaseTree,
    tested_spec,
    PathEscape,
    Worktree,
    WorktreeError,
    safe_join,
    testing_context,
    verify_context,
)

from ..text import _now
from ..oracle_suite import oracle_files, _commit_all, set_aside
from ..ledger import FindingLedger
from ..probes import breaker_detail
from ..repairs import oracle_problems
from ..checks.gate_results import (
    check_attribution,
    check_blind_helper_errors,
    check_leaks,
    check_unreset_sort,
    check_oracle_file_failures,
)
from ..checks.seams import is_seam_check, seam_gates, check_seams
from ..checks.blind import check_blind_attribution, check_blind_suite_loads


class AssessMixin:
    async def _run_seam_checks(
        self, sandbox: Sandbox, runner, files: Sequence[FileWrite],
    ) -> list[GateResult]:
        """Run the integrator's seam checks, where they live on the branch.

        They are not deleted afterwards the way the breaker's probes are, as
        this factory's evidence about the seams rather than part of the
        feature. A check that shows two units agree is precisely what the
        repository wants to keep, and deleting it would mean the one artefact
        worth having outlives the run by nothing -- the next feature starts
        with no cross-unit coverage at all and the integrator writes it again
        from scratch.

        So they are ordinary integration tests on the branch, committed with
        the rest of the change. Written again only where they are missing,
        which on a resume is how a recorded check gets back onto a fresh
        checkout; never overwritten, because what is on the branch is what
        runs, and INV-12 keeps a repairer off them regardless.
        """
        # A feature recorded by an older integration carries a different
        # shape: `tests/seams/*.sh` scripts, written to be run with `sh`.
        # They are not seam checks by the definition used here -- nothing in
        # this project collects a shell script -- and a resume must not put
        # them back. It would write ten dead files onto the branch, and on a
        # project whose per-file rules end in a catch-all it would hand each of
        # them to pytest, which is a new failure for a new wrong reason. The
        # run says so instead: no check here, which `check_seams` reports as
        # nothing showing the seams hold.
        marker = self.config.rework.seam_marker
        current = [f for f in files if is_seam_check(f.path, marker)]
        gates = seam_gates(IntegrationReport(summary="", seam_files=current),
                           self.project.state.test_file_commands)
        if not gates:
            return []
        ran = {f.path for f in current if any(f.path in g.command for g in gates)}
        for f in current:
            if f.path not in ran:
                continue
            try:
                target = safe_join(sandbox.path, f.path)
            except PathEscape:
                continue
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f.contents, encoding="utf-8")
        results = list((await run_gates(gates, sandbox.path, runner)).results)
        # A seam check ships in the project's suite and runs beside every other
        # test there, so it is asked what a blind file is asked: does it pass
        # again straight after, and -- if not -- again after the project's
        # reset? The same run as the first, so it is measured the same way.
        env = getattr(self.project, "environment", None)
        reset = list(env.test_prepare) if env else []
        by_name = {g.name: g for g in gates}
        for result in results:
            if not result.passed or result.name not in by_name:
                continue
            again = (await run_gates([by_name[result.name]], sandbox.path, runner)).results[0]
            if again.passed:
                continue
            path = next((f.path for f in current if f.path in (result.command or "")),
                        result.name)
            result.leaked = [path]
            result.leak_output[path] = again.output_tail or ""
            if not reset:
                continue
            for step in reset:
                await runner.execute(step, cwd=sandbox.path, timeout_s=900.0, network=False)
            third = (await run_gates([by_name[result.name]], sandbox.path, runner)).results[0]
            (result.leak_confirmed if third.passed else result.unstable).append(path)
        return results

    async def _assess(
        self, store: EvidenceStore, state: FeatureState, spec: Spec, sandbox: Sandbox,
        accumulated: list[BreakerTest], round_index: int, focus: str,
        witness: Sequence[str] = (), blind_command: str = "", suite: Sequence[str] = (),
        oracle: OracleSuite | None = None, seams: Sequence[FileWrite] = (),
        final: bool = False,
    ) -> tuple[GateReport, list[BreakerTest], BreakerReport]:
        """One round's facts: the project's gates, then the breaker's probes, in
        the project's own environment.

        Sequential rather than concurrent on purpose. The breaker writes files
        into the same worktree the gates run in, and a test file appearing
        halfway through a test gate is collected by it -- which would make the
        project's own signal depend on the breaker's timing.
        """
        suffix = f" · round {round_index}" if round_index else ""
        async with self._phase(store, state, "gates") as phase:
            runner, image = await runner_for(
                self.project, self.config, feature_id=state.feature_id)
            sandbox.state.image = image
            state.sandbox = sandbox.state
            phase.detail = (f"in {image}" if image else "on this machine (environment: host)") + suffix
            try:
                # The baseline runs these, and a feature has to as well. An
                # image installs dependencies at build time and the worktree is
                # then mounted over the workdir, so anything the image put
                # inside it -- `node_modules`, an editable install -- is
                # shadowed by the time a gate looks. The
                # baseline is green because onboarding ran setup; without it
                # every feature would be red for a reason that has nothing to
                # do with the feature.
                #
                # Once per runner, not once per sandbox. "The checkout keeps
                # what setup put there" is true of `npm ci`, whose output is in
                # the mount, and false of everything that installs outside it:
                # a container runner holds the toolchain in an image it removes
                # on the way down, and the database setup migrated in a volume
                # that goes with it. Skipping setup on round 1 because round 0
                # ran it has left every backend gate reporting `ruff: not
                # found` for the rest of a run, and the packet then reported
                # zero criteria verified -- a fact about this harness,
                # presented as a fact about the code. Minutes per round is the
                # correct price.
                setup_results: list[GateResult] = []
                env = self.project.environment
                fresh = not getattr(runner, "carries_setup", True)
                if env and env.setup and (fresh or not sandbox.state.setup_at):
                    phase.detail = f"setup ({len(env.setup)} command(s))" + suffix
                    setup_results = await run_setup(env.setup, sandbox.path, runner,
                                                    warm=self.project.warm_gates)
                    sandbox.state.setup_at = _now()
                    state.sandbox = sandbox.state
                    store.append("setup", {
                        "commands": list(env.setup),
                        "results": [r.model_dump(mode="json") for r in setup_results],
                        "failed": [r.name for r in setup_results
                                   if not r.passed and not r.skipped],
                    }, role="orchestrator", spec_hash=state.spec_hash,
                       meta={"round": round_index})
                # Everything this assessment runs happens inside one prepared
                # environment: the services the project declared are up, and
                # whatever `test_prepare` migrates or seeds has been done once.
                #
                # It wraps the project's gates as well as the blind suite, and
                # that is not symmetry for its own sake. Wired into the blind
                # run alone, services would be missing where a survey has
                # correctly moved `uvicorn` out of the project's own test gate
                # and into `services`, and that gate would run against nothing.
                # Anything declared as standing has to stand for every command.
                environment = self.project.environment
                # One sweep per blind file, attributed to it. The count before
                # is so the phase line can say what *this* round recorded
                # across every sweep, rather than what the last one found.
                traces_before = sum(len(p.get("files") or [])
                                    for p in store.payloads("traces"))

                def sweep(test_path: str) -> None:
                    self._collect_traces(store, state, sandbox, test_path=test_path,
                                         round_index=round_index, before=traces_before)

                async with test_session(
                    sandbox.path, runner,
                    services=list(environment.services) if environment else (),
                    prepare=list(environment.test_prepare) if environment else (),
                    disposable=self.project.state.testing.disposable_db,
                ) as prepared:
                    if prepared.ready:
                        gates = await self._run_project_checks(
                            store, state, sandbox, runner, final=final)
                        gates.results += await self._run_seam_checks(sandbox, runner, seams)
                        # The blind suite runs on its own, after the project's
                        # gates and in the same environment. It is a gate like
                        # any other from here on -- it can fail, it is
                        # attributed, it appears in the packet -- but it is
                        # *this* gate that decides whether a criterion is
                        # verified, and no criterion is ever decided by whether
                        # the project's own suite was happy.
                        #
                        # Recordings are swept after each of its files, not once
                        # at the end: the runner clears its output directory
                        # every time it starts, so the end is where every file
                        # but the last has already been erased.
                        # Whatever the gates and seam checks recorded is theirs,
                        # swept before the first blind file runs. Left in place,
                        # the sweep after that file -- a pytest one, say, which
                        # clears no browser output -- would claim the seam
                        # checks' recordings, and the packet would say an API
                        # suite had driven a browser.
                        sweep("")
                        blind = await self._run_blind_suite(
                            sandbox, runner, witness, blind_command, after_file=sweep)
                        await self._sort_failures(gates, blind, suite, sandbox, runner,
                                                  [f.path for f in seams])
                        # The oracle's own mistakes go back to the oracle, and
                        # the checks run once more on what it changed -- here,
                        # while the environment is still standing, rather than
                        # a round later.
                        if oracle is not None and self._may_revise_oracle(store, state):
                            problems = oracle_problems(gates, blind, oracle)
                            if problems:
                                phase.detail = "the oracle is fixing its own files" + suffix
                                changed = await self._revise_blind_suite(
                                    store, state, spec, sandbox, oracle, problems)
                                if changed:
                                    suite = [f.path for f in oracle_files(oracle)]
                                    gates = await self._run_project_checks(
                                        store, state, sandbox, runner, final=final)
                                    gates.results += await self._run_seam_checks(
                                        sandbox, runner, seams)
                                    sweep("")
                                    blind = await self._run_blind_suite(
                                        sandbox, runner, witness, blind_command,
                                        after_file=sweep)
                                    await self._sort_failures(
                                        gates, blind, suite, sandbox, runner,
                                        [f.path for f in seams])
                    else:
                        # Nothing ran, and nothing may read as though it had.
                        # A gate that could not start settles no criterion.
                        gates = GateReport(results=[GateResult(
                            name=g.name, command=g.command, exit_code=1, passed=False,
                            started=False, output_tail=prepared.report)
                            for g in [*self.project.judging_gates,
                                      *seam_gates(
                                          IntegrationReport(
                                              summary="", seam_files=list(seams)),
                                          self.project.state.test_file_commands)]])
                        blind = GateResult(
                            name="blind-tests", exit_code=1, passed=False, started=False,
                            output_tail=(
                                "The environment these tests need was never ready, so none "
                                "of them ran and no criterion is verified. This is a fact "
                                "about the environment, not about the code.\n\n"
                                + prepared.report))
                    # Anything left, and whether or not the suite passed: a
                    # recording of a failure is the one most worth having. The
                    # per-file sweeps claim what they can, and this catches a run
                    # that never reached one -- no blind suite, or an environment
                    # that would not come up but still drew something. In the
                    # same session, because the checkout is still standing.
                    self._collect_traces(store, state, sandbox,
                                         round_index=round_index, before=traces_before)
                    recorded = sum(len(p.get("files") or [])
                                   for p in store.payloads("traces")) - traces_before
                    if recorded:
                        phase.detail = (phase.detail or "") + f" · {recorded} recording(s)"
                    if blind is not None:
                        gates.results.append(blind)
                    # Setup stays out of the report on purpose. Whether it ran at
                    # all depends on the runner, so a row for it can be present in
                    # one round and absent in the next, and the no-regression check
                    # -- which compares how many gates passed -- would read that as
                    # the repair having broken something. It did exactly that once.
                    # The failure is surfaced as a finding instead.
                    passed = sum(1 for g in gates.results if g.passed)
                    phase.detail = (f"{passed}/{len(gates.results)} passed"
                                    + (f" in {image}" if image else "") + suffix)
                    store.append("gates", gates, role="orchestrator", spec_hash=state.spec_hash,
                                 meta={"round": round_index})

                    # Inside the prepared environment, not after it. Run once
                    # `test_session` has closed, the probes would meet services
                    # stopped, `test_prepare` not re-run, the database in whatever
                    # state the blind suite left it. A probe that cannot reach the
                    # application fails for a reason that has nothing to do with the
                    # code, and a probe is only ever allowed to mean one thing --
                    # this implementation does not survive this input.
                    async with self._phase(store, state, "breaker") as bphase:
                        tests, report = await self._run_breaker(
                            store, state, spec, sandbox, runner, accumulated, round_index, focus)
                        bphase.detail = breaker_detail(tests, report, suffix)
            finally:
                down = getattr(runner, "down", None)
                if down is not None:
                    await down()

        # After the gate runner is down, because this opens its own: a failing
        # gate is re-run at the commit the feature branched from, so the packet
        # can say whether the feature caused it or inherited it.
        await self._attribute_failures(store, state, sandbox, gates, round_index)
        store.append("gates", gates, role="orchestrator", spec_hash=state.spec_hash,
                     meta={"round": round_index, "attributed": True})
        return gates, tests, report

    # ---- attribution ----------------------------------------------------

    def _cached_attribution(self, base_sha: str, gate_name: str) -> dict[str, Any] | None:
        """What this gate did at this commit, if anything already asked.

        Kept on the *project* ledger rather than the feature's, because the
        answer is a property of the commit and not of the feature: three
        features branching from the same commit ask the same question, and the
        second and third should not pay for it.

        Only answers measured with the project's services running. An answer
        measured without them is not trusted, because a check that needs a
        server fails there for want of one: cached, that "failed at base"
        would go on calling a feature's own failure pre-existing for every
        feature built on that commit.
        """
        if not base_sha:
            return None
        found = None
        for record in self.project.store:
            if record.get("kind") != "attribution":
                continue
            payload = record.get("payload") or {}
            if (payload.get("base_sha") == base_sha and payload.get("gate") == gate_name
                    and payload.get("in_session")):
                found = payload
        return found

    def _may_revise_oracle(self, store: EvidenceStore, state: FeatureState) -> bool:
        """Whether the oracle may work on its own files this round.

        Per round, and only sessions that ran. The oracle owns the test tree
        the way a repairer owns the code, and a peer that gets two turns for
        the whole run is not a peer -- it is an exception path with a
        surprise at the end of it. Counted per run, a feature whose tests
        need work in a later round meets a closed route and the finding just
        sits there; the round cap already bounds how many rounds there are.

        Sessions that never ran do not count, either way. Charged for every
        attempt, two sessions a provider refused before doing anything would
        use the whole allowance -- and that has happened: the next run, with a
        working route, sent fifteen findings to an oracle that was never asked,
        while seven test files that one package marker would have fixed stayed
        broken for three rounds.
        """
        if not self.harness_authoring():
            return False
        floor = self.__dict__.get("_run_floor", {}).get(state.feature_id, 0)
        done = sum(1 for r in store.records()
                   if r.get("kind") == "oracle_revision" and r["seq"] > floor
                   and r.get("spec_hash") == state.spec_hash
                   and (r.get("payload") or {}).get("round") == state.rework_round
                   and (r.get("payload") or {}).get("ran", True))
        return done < max(0, self.config.pipeline.oracle_revisions)

    async def _revise_blind_suite(
        self, store: EvidenceStore, state: FeatureState, spec: Spec, sandbox: Sandbox,
        oracle: OracleSuite, problems: str, corrections: str = "",
    ) -> list[str]:
        """Show the oracle what is wrong with its files, and take its fixes.

        The agent that wrote a file is the one told when it is wrong. Workers
        may not edit the oracle's files -- a builder able to rewrite its own
        exam would pass it -- so without this, a helper that breaks the
        project's linter or a test that will not load sits on the branch until
        a human finds it, and CI fails on it.

        The oracle works where it wrote the suite: a checkout of the commit the
        feature branched from, holding its own files as they are on the branch
        and nothing of the implementation. What it learns about the
        implementation is what the errors say, which is only ever about the
        names its own tests asked for.

        Returns the paths it changed, already written to the branch and
        committed.
        """
        cfg = self.config.rework
        confine = self._own_blind_dirs(state) or [cfg.oracle_dir]
        spec = tested_spec(spec, self.project.state)
        context = verify_context(spec, self.project.state.runtime_packages,
                                 self.project.state.runtime_contract(),
                                 testing_context(self.project.state.testing),
                                 self._oracle_guides(store, state))
        # Two jobs, kept apart on purpose. The first is mechanical and the
        # guardrail over it is absolute: nothing an error message says may
        # change what a test asserts. The second is the only thing that may,
        # and it is not a model's opinion -- it is the person who owns the
        # contract saying the contract was written down wrong. Merged into
        # one section, the second would read as permission to soften a test
        # whenever something is failing, which is the whole thing the
        # guardrail exists to stop.
        parts = [context]
        if problems:
            parts.append(
                "---\n\n# Some of your files need fixing\n\n"
                "The feature has been built and your suite has run against it. Some of your "
                "files have problems of their own, separate from whether the feature is "
                "right:\n\n" + problems + "\n\n"
                "Fix what the errors above describe, in those files, and nothing else.\n\n"
                "- **Keep every assertion meaning what it means.** These tests are the "
                "acceptance contract. A test that fails because the feature is wrong must "
                "keep failing; weakening one to make an error go away defeats the whole "
                "suite.\n"
                "- An error may name something in the implementation -- a module, a class, "
                "a route. That tells you what exists; the specification above still says "
                "what it must do.")
        if corrections:
            parts.append(
                "---\n\n# Corrections from the person who owns this contract\n\n"
                "These are not findings and not a model's opinion. The person who approved "
                "the specification has read your tests and is telling you one of them "
                "asserts the wrong thing. They are entitled to: the contract is theirs. "
                "Here is what they wrote, in their words:\n\n" + corrections + "\n\n"
                "- **Change what the named test asserts to what the note says.** This is "
                "the one thing that overrides the rule above, and only here, and only for "
                "the test named.\n"
                "- **Touch no other assertion.** Not in that file, not anywhere. A "
                "correction to one test is not licence to revisit the suite.\n"
                "- **Say what it meant before and what it means now**, in your summary, "
                "one line each. A contract that moved without anyone able to read where it "
                "moved to is worse than one that is wrong.\n"
                "- **If the note does not say what the test should assert instead, change "
                "nothing for it and say so.** \"Fix this\" is not a correction -- it is a "
                "person pointing, and guessing what they meant and rewriting their "
                "acceptance test around the guess is the worst thing you could do here. "
                "Leaving it says the note needs a sentence; that costs a round. Guessing "
                "costs the contract.")
        parts.append(
            "---\n\nYour files are in this directory exactly as they are on the branch.\n\n"
            "- Do not add test files. A helper inside your directory is fine.\n"
            "- Anything you write outside your directory is discarded.")
        brief = "\n\n".join(parts)
        current = {f.path: f for f in oracle_files(oracle)}
        on_branch: dict[str, str] = {}
        for path, f in current.items():
            try:
                on_branch[path] = safe_join(sandbox.path, path).read_text(
                    encoding="utf-8", errors="replace")
            except (OSError, PathEscape):
                on_branch[path] = f.contents
        base_sha = (state.sandbox.base_sha if state.sandbox else "") or ""
        with BaseTree(self.project.repo_path, base_sha,
                      label=f"oracle-fix-{state.feature_id}") as tree:
            tree.assert_at_base()
            assert tree.path is not None
            for path, text in on_branch.items():
                target = safe_join(tree.path, path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            # Committed, so the session reports only what it changes. Left
            # untracked, every file would read as the session's own work: a
            # session the provider refused outright would have "written" the
            # whole suite, so no fallback is tried -- which has spent both of a
            # run's fixes on a route that was out of credits.
            _commit_all(tree.path, "the oracle's files as they are on the branch")
            session = await self._author_files(
                role="oracle", brief=brief, tree=tree.path, confine=confine,
                system=self.role_prompt("oracle"),
                environment=self.unit_env(
                    f"{state.feature_id}-oracle-r{state.rework_round}", role="oracle",
                    serve=False),
                on_environment=self.env_recorder(
                    store, state, unit="oracle", role="oracle",
                    round_index=state.rework_round))

        after = {f.path: f.contents for f in session.files}
        changed: list[str] = []
        for path, f in current.items():
            new = after.get(path)
            # A file it deleted keeps its old contents: a test that disappears
            # takes its criteria with it, and that is not a fix.
            if new is None or new == on_branch.get(path):
                continue
            safe_join(sandbox.path, path).write_text(new, encoding="utf-8")
            f.contents = new
            changed.append(path)
        for path, text in after.items():
            if path in current:
                continue
            target = safe_join(sandbox.path, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            oracle.support.append(SupportFile(path=path, contents=text))
            changed.append(path)
        if changed:
            sandbox.commit(f"factory: the oracle fixed its own files\n\nspec {state.spec_hash}")
            state.sandbox = sandbox.state
            # The suite as it now is, recorded. The fix above changes it in
            # memory only, and a resume rebuilds it from the latest `oracle`
            # record -- so without this, every resume would go back to the
            # suite as first written: the original contents, and none of the
            # helpers a fix had added.
            store.append("oracle", oracle, role="oracle", spec_hash=state.spec_hash,
                         meta={"revision": True})
        from ...executors import _session_exhausted
        # With the route, because the reading is made against the keys it
        # publishes: read as plain text, the window gauge every healthy session
        # prints is itself the word "rate_limit", and a fix session that simply
        # needed no file change would be written down as one that never ran.
        try:
            oracle_route = self.config.route_for("oracle")
        except Exception:      # noqa: BLE001 -- a role with no route reads as before
            oracle_route = None
        store.append("oracle_revision", {
            "problems": problems[:8000],
            "corrections": corrections[:8000],
            # Which round's allowance this spent. The oracle gets its turns
            # per round, the way a repairer does.
            "round": state.rework_round,
            # Whether the oracle was actually asked. A route that refused before
            # doing anything did not spend one of this run's fix sessions.
            "ran": not (not session.files
                        and _session_exhausted(session.log, oracle_route)),
            "changed": changed,
            "outside": list(session.outside),
            "log_tail": session.log[-4000:],
        }, role="oracle", model=self._answered_by("oracle"), spec_hash=state.spec_hash)
        return changed

    def _restate_from_checks(
        self, ledger: FindingLedger, gates: GateReport, integration: IntegrationReport,
        oracle: OracleSuite, round_index: int, *, fresh: bool,
        repairable: Sequence[str] = (),
    ) -> None:
        """Every computed finding read off a run of the checks, said again from
        the run just made: each round's, and the final pass's."""
        ledger.restate("computed", check_blind_suite_loads(gates), round_index,
                       source="blind_suite_loads", fresh=fresh)
        ledger.restate("computed", check_blind_attribution(
            gates, self.project.state.empty_run_detected), round_index,
            source="blind_attribution", fresh=fresh)
        ledger.restate("computed", check_attribution(gates), round_index,
                       source="attribution", fresh=fresh)
        ledger.restate("computed", check_oracle_file_failures(gates), round_index,
                       source="oracle_file_failures", fresh=fresh)
        ledger.restate("computed", check_unreset_sort(gates), round_index,
                       source="unreset_sort", fresh=fresh)
        ledger.restate("computed", check_leaks(gates), round_index,
                       source="leaks", fresh=fresh)
        ledger.restate("computed", check_blind_helper_errors(gates, oracle), round_index,
                       source="blind_helper_errors", fresh=fresh)
        ledger.restate("computed",
                       check_seams(integration, gates, self.config.rework.seam_marker,
                                   written=repairable),
                       round_index,
                       source="seams", fresh=fresh)

    async def _run_project_checks(
        self, store: EvidenceStore, state: FeatureState, sandbox: Sandbox, runner,
        *, final: bool = False,
    ) -> GateReport:
        """The project's checks, by kind: fixers, then light checks, and in the
        final pass the heavy ones too. (See `Project.check_kinds`.)

        Fixers run first and one at a time, since each may rewrite what the
        next reads, and what they change is committed on its own -- so a check
        like `format --check` is never red on formatting the repair loop would
        then spend a round fixing by hand. A fixer is still judged by its exit
        code: `lint --fix` exits non-zero on what it could not fix.

        A check that rewrote files without being known as one is caught here:
        a formatter on a repository that was already formatted changes nothing
        at baseline and looks like any other check. Its changes are committed,
        and it is recorded as a fixer for every build after this one.
        """
        kinds = self.project.check_kinds(self.config.pipeline.heavy_check_after_s)
        checks = self.project.judging_gates
        fixers = [g for g in checks if kinds.get(g.name) == "fixer"]
        rest = [g for g in checks if kinds.get(g.name) != "fixer"
                and (final or kinds.get(g.name) != "heavy")]
        ran: list[GateResult] = []
        for gate in fixers:
            ran.append(await run_gate(gate, sandbox.path, runner))
        if fixers:
            self._commit_rewrites(store, state, sandbox, [g.name for g in fixers])
        report = await run_gates(rest, sandbox.path, runner)
        if tracked_changes(sandbox.path):
            caught = await self._find_rewriters(sandbox, runner, rest)
            for name in caught:
                self.project.store.append("check_fixer", {
                    "name": name, "feature": state.feature_id,
                    "why": "it rewrote files during a build",
                }, role="orchestrator")
            self._commit_rewrites(store, state, sandbox, caught or ["the project's checks"])
        report.results = ran + report.results
        return report

    def _commit_rewrites(self, store: EvidenceStore, state: FeatureState,
                         sandbox: Sandbox, names: Sequence[str]) -> None:
        """Commit what fixers changed, tracked files only, as its own commit."""
        commit = getattr(sandbox, "commit_tracked", None)
        sha = commit(f"factory: formatted by {', '.join(names)}") if commit else ""
        if sha:
            state.sandbox = sandbox.state
            store.append("fixed", {"by": list(names), "commit": sha},
                         role="orchestrator", spec_hash=state.spec_hash)

    async def _find_rewriters(self, sandbox: Sandbox, runner,
                              checks: Sequence[Gate]) -> list[str]:
        """Which of these checks rewrite files: each run alone on the tree as it
        was, and the rewrites they made put back afterwards."""
        root = Path(sandbox.path)
        saved = git.run(["diff", "HEAD", "--binary"], root, text=False)
        if saved is None or saved.returncode != 0:
            # Without the tree as it was there is nothing to put back, and
            # restoring would throw the uncommitted work away. Say nothing rewrites.
            return []
        patch = saved.stdout
        restore_tracked(root)
        found: list[str] = []
        try:
            for gate in checks:
                await run_gate(gate, root, runner)
                if tracked_changes(root):
                    found.append(gate.name)
                restore_tracked(root)
        finally:
            if patch:
                git.run(["apply", "--whitespace=nowarn", "-"], root, input=patch, text=False)
        return found

    async def _sort_failures(
        self, gates: GateReport, blind: GateResult | None, suite: Sequence[str],
        sandbox: Sandbox, runner, seams: Sequence[str] = (),
    ) -> None:
        """Say whose files each failing check is failing on. (`failed_on`)

        The project's checks run over everything on the branch, this feature's
        own test files included, because that is what CI does. So one red check
        can mean four different things: a blind test failed, which the criterion
        table already says; a seam check the integrator wrote failed, which is
        the integrator's; a file the oracle wrote breaks a rule of the
        project's, which only the oracle can fix; or the rest of the branch is
        wrong, which is the workers'. Treating all of them as "this feature
        broke a check" would send the oracle's mistakes to agents forbidden to
        fix them.

        The seam pass is why this can be honest about seam checks at all. They
        live in the project's test tree -- `api-tests` collects them like any
        other test -- and without it a red `api-tests` caused by a seam check
        the integrator wrote would sort to `code` and be reported to a human as
        a defect in the implementation.

        Sorted by exit code, never by reading output: the check runs again with
        this feature's failing blind tests set aside, then with its seam checks
        set aside, then with all of its oracle files set aside, and whichever
        run first passes says which. Narrowest cause first, so a check that
        goes green the moment one file leaves is not blamed on a larger set
        that happens to contain it. Only failing checks are re-run, and only as
        often as it takes.
        """
        judged = {g.name: g for g in self.project.judging_gates}
        failing = [r for r in gates.results
                   if not r.passed and not r.skipped and r.started and r.name in judged]
        if not failing:
            return
        present = [p for p in suite if (sandbox.path / p).is_file()]
        seams_present = [p for p in seams if (sandbox.path / p).is_file()]
        if not present and not seams_present:
            for r in failing:
                r.failed_on = "code"
            return

        # Each re-run is an experiment, and it has to start from the state the
        # first run did. Setting files aside does nothing about what those files
        # already wrote: a suite that added lists to the one demo board left
        # them there, the project's own test that counts that board's lists
        # failed again with the suite gone, and the failure was put on the code.
        # The project's own reset, where it declares one, puts that back first.
        env = self.project.environment
        reset = list(env.test_prepare) if env else []

        async def passes_without(paths: Sequence[str], checks: list[GateResult]) -> set[str]:
            with set_aside(sandbox.path, paths):
                for command in reset:
                    await runner.execute(command, cwd=sandbox.path, timeout_s=900.0,
                                         network=False)
                again = await run_gates([judged[r.name] for r in checks], sandbox.path, runner)
            return {x.name for x in again.results if x.passed}

        async def sort(paths: Sequence[str], verdict: str,
                       rest: list[GateResult]) -> list[GateResult]:
            if not paths or not rest:
                return rest
            cleared = await passes_without(paths, rest)
            for r in rest:
                if r.name in cleared:
                    r.failed_on = verdict
            return [r for r in rest if r.name not in cleared]

        bad = set(blind.named_failing) | set(blind.named_unloadable) if blind else set()
        remaining = await sort([p for p in present if p in bad], "blind_tests", failing)
        remaining = await sort(seams_present, "seam_tests", remaining)
        if remaining:
            cleared = await passes_without(present, remaining) if present else set()
            for r in remaining:
                r.failed_on = "oracle_files" if r.name in cleared else "code"
                # Red with this feature's test files gone is also what their
                # leftovers look like, when nothing could clear them away.
                r.unreset = bool(r.failed_on == "code" and present and not reset)

    async def _attribute_failures(
        self, store: EvidenceStore, state: FeatureState, sandbox: Sandbox,
        gates: GateReport, round_index: int,
    ) -> None:
        """Re-run each failing gate at the commit this feature branched from.

        A red gate on its own says nothing. A repository with 129 pre-existing
        lint errors produces a red lint gate for every feature ever built in it,
        and nobody can tell which of those errors the feature added. Running the
        same gate, in the same image, at the commit the work started from turns
        that into two different sentences:

            failed here, failed there  -> not this feature's doing
            failed here, passed there  -> this feature did it

        Only failing gates, only when a base commit is known, and cached per
        commit. A green run costs nothing.
        """
        base_sha = (sandbox.state.base_sha or "").strip()
        # A failure sorted to this feature's own oracle files has nothing to
        # compare against: those files do not exist at the base commit.
        failed = [g for g in gates.results
                  if not g.passed and not g.skipped and not g.name.startswith("setup[")
                  and g.failed_on not in ("blind_tests", "oracle_files")]
        if not base_sha or not failed:
            return

        by_name = {g.name: g for g in self.project.judging_gates}
        wanted = [g for g in failed if g.name in by_name]
        if not wanted:
            return

        for result in wanted:
            cached = self._cached_attribution(base_sha, result.name)
            if cached is not None:
                result.at_base = cached.get("at_base", "not_checked")
                result.base_sha = base_sha
                result.base_output_tail = cached.get("output_tail", "")

        outstanding = [g for g in wanted if g.at_base == "not_checked"]
        if not outstanding:
            return

        async with self._phase(store, state, "attribution") as phase:
            phase.detail = (
                f"{len(outstanding)} gate(s) at {base_sha[:12]}"
                + (f" · round {round_index}" if round_index else "")
            )
            runner, _ = await runner_for(
                self.project, self.config, feature_id=f"{state.feature_id}-base")
            try:
                # A second checkout of the base commit, beside the feature's own.
                # It shares the object database, so this is cheap in disk and in
                # time; what it costs is one setup run, and only on a failure.
                with Worktree(sandbox.path, label=f"base-{state.feature_id}", ref=base_sha) as tree:
                    assert tree.path is not None
                    env = self.project.environment
                    setup_ok = True
                    if env and env.setup:
                        setup = await run_setup(env.setup, tree.path, runner,
                                                warm=self.project.warm_gates)
                        setup_ok = all(r.passed or r.skipped for r in setup)
                    # In the same prepared environment the branch's run had:
                    # its services up, its state prepared. Without it every
                    # check that needs a running server fails here for want of
                    # one, and is reported as failing before this feature --
                    # one project's browser gate was called pre-existing on a
                    # base where it passes, which is exactly the sentence this
                    # step exists to get right.
                    async with test_session(
                        tree.path, runner,
                        services=list(env.services) if env else (),
                        prepare=list(env.test_prepare) if env else (),
                        disposable=self.project.state.testing.disposable_db,
                    ) as prepared:
                        report = (await run_gates(
                            [by_name[g.name] for g in outstanding], tree.path, runner)
                            if prepared.ready else GateReport(results=[]))
                    outcomes = {r.name: r for r in report.results}
                    for result in outstanding:
                        base_result = outcomes.get(result.name)
                        if base_result is None or not setup_ok:
                            # Setup failing at the base commit means the base
                            # could not be measured, not that it was green.
                            # Saying "passed there" here would blame the feature
                            # for something nobody established.
                            result.at_base = "could_not_run"
                        else:
                            result.at_base = "passed" if base_result.passed else "failed"
                            result.base_output_tail = base_result.output_tail
                        result.base_sha = base_sha
                        self.project.store.append("attribution", {
                            # Which feature's failure this settles. Otherwise it
                            # is recoverable only from a container name in the
                            # output, which newer records do not carry.
                            "feature_id": state.feature_id,
                            "base_sha": base_sha,
                            "gate": result.name,
                            "at_base": result.at_base,
                            "output_tail": result.base_output_tail,
                            "setup_ok": setup_ok,
                            # Measured with the project's services standing.
                            # The cache trusts nothing measured without them.
                            "in_session": True,
                        }, role="orchestrator")
            except (WorktreeError, DockerError) as exc:
                # Attribution is a convenience over a fact, not a fact. Losing it
                # costs a sentence in the packet; failing the build over it would
                # cost the whole run.
                for result in outstanding:
                    result.at_base = "could_not_run"
                    result.base_sha = base_sha
                phase.detail = f"unavailable: {exc}"
                store.append("attribution_failed", {
                    "base_sha": base_sha, "error": str(exc),
                    "gates": [g.name for g in outstanding],
                }, role="orchestrator", spec_hash=state.spec_hash)
            finally:
                down = getattr(runner, "down", None)
                if down is not None:
                    await down()
            pre = sum(1 for g in wanted if g.pre_existing)
            caused = sum(1 for g in wanted if g.caused_here)
            phase.detail = (
                f"{caused} caused here, {pre} pre-existing"
                + (f" · round {round_index}" if round_index else "")
            )
        store.append("attribution", {
            "base_sha": base_sha,
            "results": [
                {"gate": g.name, "at_base": g.at_base} for g in wanted
            ],
        }, role="orchestrator", spec_hash=state.spec_hash)
