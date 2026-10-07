"""The verify lane: the blind suite authored from the spec alone (INV-1).
"""

from __future__ import annotations

from fnmatch import fnmatch
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from ... import dependencies, sendback
from ...sandbox import Sandbox
from ...schemas import (
    FeatureState,
    FileWrite,
    GateReport,
    OracleAccount,
    OracleSuite,
    Spec,
    WorkUnit,
)
from ...store import EvidenceStore
from ...workspace import (
    BaseTree,
    tested_spec,
    PathEscape,
    safe_join,
    testing_context,
    verify_context,
)

from ..text import _slug
from ..oracle_suite import (
    assemble_oracle_suite,
    _suite_detail,
    oracle_files,
    runner_naming,
    blind_write_targets,
    usable_suite,
)
from ..protection import is_protected
from ..ledger import FindingLedger
from ..checks.repo import _git_lines, check_new_quality_findings, check_patch_coverage
from ..checks.blind import check_oracle_discards


class VerifyLaneMixin:
    # ---- verify lane ----------------------------------------------------

    async def _author_blind_suite(
        self, store: EvidenceStore, state: FeatureState, spec: Spec, context: str,
        system: str, phase,
    ) -> tuple[OracleSuite, str]:
        """The oracle writes its suite in the repository as it stood before the work.

        INV-1 says this lane cannot read the implementation. Held by omission
        -- nothing putting the code in the prompt -- it would take the weakest
        form an invariant can take, because omission survives only as long as
        nobody adds a helpful paragraph. It holds by construction instead: the
        directory the agent works in is a checkout of the commit the feature
        branched from, `assert_at_base` proves that before it starts, and no
        line of the work exists in it.

        It is not an *empty* directory, which would enforce more than the
        invariant says. "Cannot read the implementation" is not "cannot read
        anything", and an author who can read neither the schema nor the
        fixtures nor the existing tests does not write a more independent test
        -- it writes one with guesses in it, and a guess fails as though the
        implementation were wrong. One did: raw SQL naming a column that does
        not exist, and a red criterion over a migration that was correct.

        What this buys, besides the stronger invariant, is the ceiling. Three
        consecutive runs have lost their entire verification because a suite of
        test files did not fit in one answer; written with a tool, the files are
        read back off disk, and the only thing asked for is the tagging -- which
        is a judgement about the contract that no filesystem can supply, and
        which costs a few hundred characters instead of two hundred thousand.
        """
        cfg = self.config.rework
        # Where these files will actually live, decided before they are written
        # rather than by moving them afterwards. A relative path is only correct
        # relative to something, and an author told the wrong directory writes a
        # wrong path while doing the arithmetic perfectly -- which has cost a
        # run six criteria.
        targets = blind_write_targets(
            self.project.state.blind_placements, state.feature_id,
            [r.directory for r in self.project.state.placement_probe if r.subdirs_ok])
        confine: list[str] = [d for _, d in targets] or [cfg.oracle_dir]
        if targets:
            rows = "\n".join(
                f"- a file ending `{suffix}` goes in `{where}/`, and from there the "
                f"repository root is `{'../' * len(where.split('/'))}`"
                for suffix, where in targets)
            where_to_write = (
                "\n\n---\n\n# Where to write\n\n"
                "Create these directories and write your files straight into them, "
                "from the directory you are working in:\n\n" + rows + "\n\n"
                "**These are the paths your files keep.** Nothing is moved afterwards, "
                "so a relative path you write is the relative path that runs. If a test "
                "has to reach outside its own directory -- to import the thing it is "
                "testing -- count from the directory that file is in, and the line above "
                "gives you the prefix that reaches the repository root from it.\n\n"
                "Anything you write outside these directories is discarded without being "
                "read."
                + runner_naming(self.project.state.blind_placements,
                                self.project.state.placement_probe)
                + "\n\n# What is in those directories\n\n")
        else:
            where_to_write = (
                "\n\n---\n\n# Where to write\n\n"
                f"Write your suite as files under `{cfg.oracle_dir}/`, from the directory "
                "you are working in. Create it. Anything you write outside it is discarded "
                "without being read.\n\n"
                "# What is in that directory\n\n")
        base_sha = (state.sandbox.base_sha if state.sandbox else "") or ""
        brief = context + (
            where_to_write
            + (
            "This repository, as it stood before any of this work started. Every file "
            "that was here then is here now -- the source, the tests, the fixtures, the "
            "configuration, the documentation. Read it. It is the world this change "
            "lands in, and you are the only agent asked to describe what that world "
            "should look like afterwards without being shown the answer.\n\n"
            "What is not here is the work itself. This is a checkout of the commit the "
            "feature branched from, so not one line of the implementation exists in it "
            "and none will appear while you are working. That is the air gap, and it is "
            "the filesystem that holds it rather than what this brief leaves out.\n\n"
            "So: look things up rather than assuming them. A column name, a fixture's "
            "signature, how the existing tests reach the application, what a config key "
            "is called -- all of it is here to be read, and a literal invented instead "
            "fails as though the implementation were wrong. The one thing none of it can "
            "answer is what the change looks like: the contract is the specification "
            "above, and code that predates the work says nothing about work that came "
            "after it.\n\n"
            "Write the files and save them. Do not paste them into a reply -- a reply "
            "is read by nobody, and only what is on disk leaves this session."
        ))
        with BaseTree(self.project.repo_path, base_sha,
                      label=f"oracle-{state.feature_id}") as tree:
            tree.assert_at_base()
            assert tree.path is not None
            # A sealed container of its own, and nothing running in it. No
            # environment at all would mean this machine's shell: every server
            # running here in reach, and every other feature's checkout -- this
            # one's implementation included -- a path away.
            session = await self._author_files(
                role="oracle", brief=brief, tree=tree.path, confine=confine,
                system=system,
                environment=self.unit_env(
                    f"{state.feature_id}-oracle", role="oracle", serve=False),
                on_environment=self.env_recorder(
                    store, state, unit="oracle", role="oracle"))
            store.append("oracle_session", {
                "files": [f.path for f in session.files],
                "outside": session.outside,
                "attempts": session.attempts,
                "cost_usd": session.cost_usd,
                "log_tail": session.log[-4000:],
            }, role="oracle", spec_hash=state.spec_hash)
            if not session.files:
                # No files is a real answer and it says so. Returning an empty
                # suite lets `check_blind_tags` report "nothing was verified and
                # here is why" instead of the run failing with a traceback.
                phase.detail = "the harness wrote no test files"
                return OracleSuite(
                    strategy="",
                    notes=("The harness produced no test file on either attempt, so nothing "
                           "in this run was verified against the spec. Harness output "
                           f"(tail):\n\n{session.log[-2000:]}"),
                ), brief

            phase.detail = f"{len(session.files)} file(s) written; tagging"
            naming = runner_naming(self.project.state.blind_placements,
                                   self.project.state.placement_probe)
            answer, note = await self._account_for(
                role="oracle", system=system, brief=context + naming, session=session,
                model=OracleAccount,
                asked=("Sort what you wrote, then tag it. Every file above is either a "
                       "file that ASSERTS something about the spec or a file that does "
                       "not.\n\n"
                       "For each one that asserts, give an entry in `tests` with its "
                       "`path` exactly as shown, the `criterion_ids` it checks, and the "
                       "`framework` it is written for. Also give `cases`: one entry per "
                       "individual test in that file, with the name your framework will "
                       "print for it and the criteria that one test checks. Copy those "
                       "names off the file you wrote rather than composing them -- they "
                       "are joined against the report your own framework writes, and a "
                       "name matching nothing there is reported as a fault in this "
                       "account rather than as a verdict about the code. You know which "
                       "framework you wrote for; this does not.\n\n"
                       "That is what decides how much one mistake costs. Without `cases` "
                       "a file is the smallest thing that can be judged, so every "
                       "criterion in it shares one exit code: on a real run one test "
                       "disagreed about one field, its file carried fourteen criteria, "
                       "and all fourteen were reported as failing while thirteen had "
                       "tests that passed. For each one that asserts nothing "
                       "-- shared setup your runner loads on its own, a fixture or "
                       "factory module, a helper, sample data, a README -- put its path "
                       "in `support` and nothing else. "
                       "Name the `command` that runs this suite and nothing else, from "
                       "the repository root. Put any criterion you could not test in "
                       "`untestable_criteria`.\n\n"
                       "If any of those criteria was clear enough to test and you were "
                       "stopped by the environment rather than by the spec -- no way to "
                       "authenticate as a particular user, no way to create the data the "
                       "criterion needs, no database you may migrate -- put that in "
                       "`requires`, one entry per capability, naming the criterion ids it "
                       "cost. That field is read by the orchestrator and reported against "
                       "the harness, so those criteria are recorded as unverified because "
                       "of what you were given rather than because of anything in the "
                       "code. Say it there rather than writing a fixture that demands the "
                       "capability: a suite that stops on a precondition nobody agreed to "
                       "supply verifies nothing at all.\n\n"
                       "An asserting file that names no criterion proves nothing about "
                       "the contract, however good the test in it is: the matrix is built "
                       "from these ids and a file that names none is invisible to it. But "
                       "do not reach for a tag to avoid that. A setup file tagged with "
                       "every criterion is worse than an untagged one -- it makes each of "
                       "those criteria look covered by evidence that asserts nothing, "
                       "which is the one failure this whole lane exists to prevent. If a "
                       "file does not assert, it goes in `support`."),
            )
            suite = assemble_oracle_suite(session, answer, note)

            # A suite tagged to nothing verifies nothing, whatever is in it.
            # Re-asking is worth doing: the tests are already on disk and safe,
            # so this retry costs one small answer rather than rewriting the
            # whole suite -- which would make it a gamble.
            for _ in range(max(0, self.config.pipeline.oracle_retries)):
                if usable_suite(suite, spec):
                    break
                store.append("oracle_retry", {
                    "why": "no file named a criterion in this spec",
                    "files": [t.path for t in suite.tests],
                    "tags": [t.criterion_ids for t in suite.tests][:20],
                    "support": [f.path for f in suite.support],
                }, role="oracle", spec_hash=state.spec_hash)
                phase.detail = "retagging: the suite named no criterion"
                answer, note = await self._account_for(
                    role="oracle", system=system, brief=context + naming, session=session,
                    model=OracleAccount,
                    asked=("Your last answer tagged no file with a criterion this spec "
                           "contains. Every id must be one of the `AC-` ids listed in the "
                           "specification above, copied exactly. Give one entry in `tests` "
                           "per file that asserts, with its `path` as shown and the ids it "
                           "checks, and put the paths of the files that assert nothing in "
                           "`support`. Do not tag a support file to make this pass."),
                )
                suite = assemble_oracle_suite(session, answer, note)
        return suite, brief

    def _breaker_targets(self, state: FeatureState) -> list[tuple[str, str]]:
        """(file extension, directory) for this feature's probes.

        The directories this project proved a test file runs in, with a folder
        of the breaker's own. Told `tests/breaker/`, which no runner here reads,
        a breaker wrote its eight probes where they would run instead; all eight
        were discarded as written outside its directory, and every round then
        pointed the test runner at a folder that did not exist. Probes never
        reach the branch -- they are run and deleted -- so living where the
        runners work costs nothing.
        """
        return blind_write_targets(
            self.project.state.blind_placements, f"{state.feature_id}-breaker",
            [r.directory for r in self.project.state.placement_probe if r.subdirs_ok])

    def _breaker_regression_targets(self, state: FeatureState) -> list[tuple[str, str]]:
        """(file extension, directory) for the probes that may outlive the run.

        A sibling of the authoring directory, one namespace over, resolved
        through the same proved placements. Two reasons it is not just the
        authoring directory.

        It says what the tests are rather than who made them. `-breaker` is a
        name for a phase of a run, and a directory that outlives the run should
        not be named after one; a person meeting
        `saved_searches_9840f6_regression` in six months can tell what is in
        it.

        And it separates what is kept from what is deleted by something other
        than a per-file decision. The deletion reads `kind` off each probe, so
        it does not need the directory to tell them apart -- but a directory
        holding only files that stay is one `rmdir` and no partial state, where
        a mixed one is a loop that must be right about every file in it.
        """
        return blind_write_targets(
            self.project.state.blind_placements, f"{state.feature_id}-regression",
            [r.directory for r in self.project.state.placement_probe if r.subdirs_ok])

    def _breaker_own_dirs(self, state: FeatureState) -> list[str]:
        """Every directory this feature's probes are written into.

        On the verification surface. A probe deleted at the end of its round
        leaves no file there for a repairer to edit, but a promoted probe is in
        the tree while the next round runs, and a repairer that can edit the
        test that is failing makes the finding go away without fixing anything
        (INV-12).

        `protected_paths` and `is_protected` already name `rework.breaker_dir`
        unconditionally, and on its own that is a hole rather than a guard: the
        default is `tests/breaker`, which no runner in a real project collects,
        which is exactly why `_breaker_targets` exists. It protects a path
        nothing is written to.
        """
        return [where for _, where in
                (self._breaker_targets(state) + self._breaker_regression_targets(state))]

    def _probe_home(self, state: FeatureState, path: str, kind: str) -> str:
        """Where a probe belongs once it has said what it is for.

        Placement happens after the breaker has written the file and before
        anything here runs it, so what runs is what ships and no proved file is
        ever moved afterwards -- the failure `blind_write_targets` exists to
        prevent. The two directories are siblings at equal depth under the same
        placement, so a relative path that resolved from one resolves from the
        other, which is the only property that makes moving a test file safe.

        Unknown extensions stay where they were written. Guessing a directory
        for a file type no placement was proved with is how a usage error comes
        to be reported as a failing criterion.
        """
        if kind != "regression":
            return path
        name = PurePosixPath(path).name
        suffix = PurePosixPath(path).suffix
        for ext, where in self._breaker_regression_targets(state):
            if ext == suffix:
                return f"{where}/{name}"
        return path

    def _send_back_for(self, unit: WorkUnit, original: Path) -> sendback.SendBack | None:
        """How this unit's worker is measured during its turn, or None when it is not.

        The project's own commands: its coverage and mutation checks, where the
        surveyor declared how to run them on some files. Tests that cannot fail
        are read whatever the project measures.
        """
        limit = self.config.rework.send_backs
        if limit <= 0:
            return None
        globs = [r.match for r in self.project.state.test_file_commands if r.match]
        home = [p for p in unit.files_expected if any(fnmatch(p, g) for g in globs)]
        coverage, mutation = sendback.pick_checks(self.project.state.gates)

        async def measure(tree: Path, runner: Any, written: list[str]) -> list[str]:
            return await sendback.measure_unit(
                tree, runner, written, original=original, test_globs=globs, test_home=home,
                coverage=coverage, mutation=mutation,
                max_mutants=self.config.rework.max_mutants, notes=sent.notes)

        sent = sendback.SendBack(measure=measure, limit=limit)
        return sent

    async def _restate_dependencies(
        self, store: EvidenceStore, ledger: FindingLedger, sandbox: Sandbox,
        round_index: int, written_by: Mapping[str, str],
    ) -> None:
        """Every package this feature added or moved, and what is known about it.

        Read from the lockfiles at the base commit and now, never from what a
        worker said it did. Recorded every round for the packet's Dependencies
        tab; held to the project's policy as findings when the policy is on.
        Looked up once per package and version per run: a new advisory between
        rounds is not worth a second wave of requests.
        """
        base = sandbox.state.base_sha
        if not base or not sandbox.exists():
            return
        before = dependencies.read_inventory(
            _git_lines(sandbox.path, ["ls-tree", "-r", "--name-only", base]),
            lambda path: sandbox.show(base, path))

        def now_text(path: str) -> str | None:
            try:
                target = safe_join(sandbox.path, path)
                return target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None
            except (OSError, PathEscape):
                return None

        after = dependencies.read_inventory(
            _git_lines(sandbox.path, ["ls-files", "-co", "--exclude-standard"]), now_text)
        changes = dependencies.diff(before, after)
        policy = self.project.state.dependency_policy
        wanted = [(c.ecosystem, c.name, c.after[-1]) for c in changes
                  if c.kind != "removed" and c.after and not c.private]
        missing = [k for k in wanted if k not in self._dependency_facts]
        if missing and policy.enabled:
            self._dependency_facts.update(await self.dependency_lookup.facts(missing))
        facts = dependencies.facts_for(changes, self._dependency_facts, policy,
                                       datetime.now(timezone.utc), written_by)
        store.append("dependencies", {
            "changes": [f.model_dump(mode="json") for f in facts],
            "lockfiles": after.lockfiles, "unreadable": after.unreadable,
            "unlocked_manifests": after.unlocked, "policy_on": policy.enabled,
        }, role="orchestrator")
        found = (dependencies.findings(facts, dependencies.unresolved(before, after),
                                       after.unreadable)
                 if policy.enabled else [])
        ledger.restate("computed", found, round_index, source="dependencies")

    def _restate_patch_quality(
        self, ledger: FindingLedger, sandbox: Sandbox, gates: GateReport, round_index: int,
    ) -> None:
        """What each check found on this feature's lines, asked again.

        Every round and again at the final pass, because a heavy check runs only
        there. Only a check that ran may close what it raised: one that runs once,
        at the end, has said nothing about the rounds before it.
        """
        ran = {r.name for r in gates.results if r.started}
        ledger.restate("computed", check_new_quality_findings(
            sandbox, self.project.state.gates, gates), round_index,
            source="patch_quality", fresh=bool(ran),
            among={f"{prefix}-{_slug(g.name)}" for g in self.project.state.gates
                   if g.name in ran for prefix in ("quality", "quality-unread")})
        ledger.restate("computed", check_patch_coverage(
            sandbox, self.project.state.gates, gates), round_index,
            source="patch_coverage", fresh=bool(ran),
            among={f"{prefix}-{_slug(g.name)}" for g in self.project.state.gates
                   if g.name in ran for prefix in ("patch-coverage", "coverage-unread")})

    def _repairable(self, written: Sequence[FileWrite], protected: Sequence[str],
                    state: FeatureState) -> list[str]:
        """The files this feature wrote that a repair may change."""
        own = self._own_blind_dirs(state) + self._breaker_own_dirs(state)
        mine = self._project_files.get(state.feature_id, ())
        return [f.path for f in written
                if not is_protected(f.path, protected, self.config, own, mine)]

    def _own_blind_dirs(self, state: FeatureState) -> list[str]:
        """The folders this feature's own blind tests were written into.

        What a repair may not touch is the suite judging *this* feature. An
        earlier feature's acceptance tests are not covered, as they would be if
        the whole placement directory were: they are ordinary tests in the
        project, linted and run like everything else, and a regression in one is
        the workers' to fix. Where a runner cannot take one folder per feature
        the placement directory is all there is, and it stays protected whole.
        """
        return [where for _, where in blind_write_targets(
            self.project.state.blind_placements, state.feature_id,
            [r.directory for r in self.project.state.placement_probe if r.subdirs_ok])]

    def _drop_broken_suite(
        self, store: EvidenceStore, state: FeatureState, sandbox: Sandbox,
        done: dict[str, Any],
    ) -> None:
        """Forget a recorded blind suite that is already known not to load.

        Resuming reuses whatever the last attempt produced, and for the suite
        that can mean one whose helpers were discarded: the tests that import
        them could not load then and cannot load now, so a resume would re-run
        every gate against a verify lane it already knows is short -- and so
        would "Verify again", which also resumes.

        Only the discard is treated as proof. A test file that fails to load
        for any other reason may be failing on the implementation, which a new
        suite written without seeing that implementation cannot fix.

        The old files come off the branch as well. The new suite is written
        where the old one was, but not necessarily under the same names, and a
        stale file that cannot import is still collected by the project's own
        test command.
        """
        broken = check_oracle_discards(store, state.spec_hash, sandbox.path)
        if not broken:
            return
        stale = [f.path for f in oracle_files(OracleSuite.model_validate(done.pop("oracle")))]
        removed: list[str] = []
        for path in stale:
            try:
                target = safe_join(sandbox.path, path)
            except PathEscape:
                continue
            if target.is_file():
                target.unlink()
                removed.append(path)
        store.append("oracle_rerun", {
            "why": broken[0].title,
            "detail": broken[0].detail,
            "removed": removed,
        }, role="orchestrator", spec_hash=state.spec_hash)

    async def _verify_lane(
        self, store: EvidenceStore, state: FeatureState, spec: Spec, system: str,
    ) -> OracleSuite:
        """AC-8.5 / INV-1 -- the oracle sees `verify_context(spec)` and nothing else.

        Every name in this function's body is deliberate. There is no digest
        here, no repository, no file, and no argument by which one could arrive.

        Two things besides the spec reach the oracle, and both describe the
        environment rather than the feature. `runtime_packages` is a list of
        package names measured at gate 0 -- not read from this repository, which
        this lane still cannot touch -- and `verify_context` filters it, so
        nothing that is not shaped like a package name survives the trip.
        `runtime_contract()` is a pure function of this project's record: the
        services it declares, the variables its per-file commands set, and a
        human's description of what the suite can reach. That record is not the
        repository, and this expression is pinned by the invariant test so no
        later edit can widen it into one.
        """
        # Without the criteria whose level this project cannot test: they are
        # a person's to check, and a test for one would fail on the missing
        # runner or another test's leftovers and read as a broken feature.
        spec = tested_spec(spec, self.project.state)
        async with self._phase(store, state, "oracle") as phase:
            if not spec.acceptance_criteria:
                suite = OracleSuite(strategy="nothing to test", notes=(
                    "Every criterion is at a test level this project can't run cleanly, "
                    "so a person checks them all by hand. Nothing was written."))
                phase.detail = "every criterion is checked by hand"
                store.append("oracle", suite, role="orchestrator", spec_hash=state.spec_hash)
                return suite
            context = verify_context(spec, self.project.state.runtime_packages,
                                     self.project.state.runtime_contract(),
                                     testing_context(self.project.state.testing),
                                     self._oracle_guides(store, state))
            if self.harness_authoring():
                suite, context = await self._author_blind_suite(
                    store, state, spec, context, system, phase)
            else:
                suite = await self.llm.ask(
                    "oracle", context, OracleSuite, system=system
                )
            # A suite tagged to nothing verifies nothing, whatever it contains.
            # `llm.ask` retries a response that fails the schema, and this one
            # passes it: `criterion_ids: [", "]` is a valid list of valid
            # strings. So the check that matters is whether the ids name any
            # criterion this spec has, and a suite that names none is a failed
            # answer -- asked again rather than carried into a run that will
            # report twelve unverified criteria and no reason.
            for _ in range(max(0, self.config.pipeline.oracle_retries)):
                if usable_suite(suite, spec) or self.harness_authoring():
                    # Under a harness the tags are re-asked in
                    # `_author_blind_suite`, against the files themselves. Asking
                    # again here would rewrite the whole suite from scratch and
                    # orphan what is already on disk.
                    break
                store.append("oracle_retry", {
                    "why": "no test named a criterion in this spec",
                    "tests": len(suite.tests),
                    "tags": [t.criterion_ids for t in suite.tests][:20],
                    "support": [f.path for f in suite.support],
                }, role="oracle", spec_hash=state.spec_hash)
                phase.detail = "retrying: the suite named no criterion"
                suite = await self.llm.ask(
                    "oracle", context, OracleSuite, system=system
                )
            phase.detail = _suite_detail(suite)
            # Said on the line while the run is going, not only in the packet:
            # a discarded helper means some of this suite will not load, and
            # every minute after this is spent building against a verify lane
            # that is already known to be short.
            dropped = [str(x) for r in store.records()
                       if r.get("kind") == "oracle_session"
                       and r.get("spec_hash") == state.spec_hash
                       for x in ((r.get("payload") or {}).get("outside") or [])]
            if dropped:
                phase.detail += (f" · {len(dropped)} file(s) written outside its "
                                 "directory were discarded")
            # Recorded so the air gap is auditable after the fact, not merely
            # asserted by a test: this is everything the oracle ever saw.
            store.append("oracle", suite, role="oracle",
                         model=self._answered_by("oracle"), spec_hash=state.spec_hash,
                         prompt=context)
        return suite
