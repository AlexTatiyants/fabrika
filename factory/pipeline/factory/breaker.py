"""The breaker: probes authored against the build, run, and promoted.
"""

from __future__ import annotations

import time
from typing import Sequence

from ...gates import run_per_file
from ...sandbox import Sandbox
from ...schemas import (
    BreakerAccount,
    BreakerReport,
    BreakerSuite,
    BreakerTest,
    FeatureState,
    FileWrite,
    Spec,
    TestFileCommand,
)
from ...store import EvidenceStore
from ...workspace import (
    cleanup_context,
    PathEscape,
    Worktree,
    repo_digest,
    safe_join,
    spec_text,
    uncommitted_paths,
)

from ..text import tree_section
from ..records import promoted_this_pass
from ..oracle_suite import assemble_breaker_suite
from ..probes import probe_budget


class BreakerMixin:
    # ---- convergence: assess, route, repair ------------------------------

    async def _author_probes(
        self, store: EvidenceStore, state: FeatureState, spec: Spec, sandbox: Sandbox,
        gate_commands: str, focus: str, system: str, round_index: int,
        report: BreakerReport,
    ) -> tuple[BreakerSuite, list[BreakerTest]]:
        """Write this round's probes, and say which of them are new.

        Under a harness the breaker gets a checkout of the code it is attacking
        and writes its probes as files. Handing it the whole changed tree inside
        its prompt and asking for whole test files inside its answer fails at
        both ends: 80,000 characters of implementation going in, and three
        rounds in a row cut off at the ceiling coming out -- 267,437 characters
        in the worst of them, all of it discarded, the round recorded as a
        breaker that found nothing.

        The checkout avoids both. It can read the file it actually needs
        rather than the truncation of it somebody chose in advance, and it can
        run a probe, watch it pass, and go find a sharper one -- which is what
        "few and lethal" asks for and what a single completion could never do.

        Containment moves with it. The probes are collected from the breaker's
        own directory and nowhere else, so an edit it makes to the code under
        attack never leaves the worktree; `assemble_breaker_suite` records that
        it made one, because a probe that only fails alongside such an edit is a
        finding about a repository nobody has.
        """
        cfg = self.config.rework
        targets = self._breaker_targets(state)
        confine = [d for _, d in targets] or [cfg.breaker_dir.strip("/")]
        where = (
            "\n".join(f"- a file ending `{suffix}` goes in `{d}/`" for suffix, d in targets)
            if targets else f"- `{cfg.breaker_dir}/`")
        # How tests are written here, from the project's testing guides: a probe
        # that ignores them fails for the wrong reason.
        testing = self._guidance(store, state, "breaker", inferred=False)
        if not self.harness_authoring():
            digest = repo_digest(sandbox.path, self.project.state.digest_budget)
            changed = tree_section(sandbox.path, sandbox.changed_files(), budget=80_000)
            prompt = "\n\n".join([
                spec_text(spec),
                *(["---\n\n" + testing] if testing else []),
                "---\n\n# What this feature changed, in full\n\n" + changed,
                "---\n\n# The rest of the repository, in digest\n\n" + digest,
                "---\n\n# How this project runs its own tests\n\n"
                + (gate_commands or "(none recorded)"),
                "---\n\n# Where the tests go\n\n" + where
                + "\n\nPaths outside these are refused before they run.",
                focus,
            ])
            suite = await self.llm.ask("breaker", prompt, BreakerSuite, system=system)
            fresh = []
            for test in suite.tests:
                rel = test.path.strip().lstrip("./")
                if not any(rel.startswith(d + "/") for d in confine):
                    report.rejected.append(
                        f"{test.path}: refused -- breaker tests must live under "
                        + ", ".join(f"{d}/" for d in confine))
                    continue
                test.path = rel
                if test.kind == "regression" and not (test.passes_when or "").strip():
                    test.kind = "demonstration"
                test.path = self._probe_home(state, test.path, test.kind)
                fresh.append(test)
            # Stored after placement, not before it: the payload a human reads
            # and the file the runner runs have to be the same path, and
            # `breaker_findings` matches its findings to probes by exactly that.
            store.append("breaker_suite", suite, role="breaker",
                         model=self._answered_by("breaker"),
                         spec_hash=state.spec_hash, meta={"round": round_index},
                         prompt=prompt)
            return suite, fresh

        brief = "\n\n".join([
            spec_text(spec),
            *(["---\n\n" + testing] if testing else []),
            "---\n\n# The implementation\n\n"
            "You are working inside a checkout of the code this feature produced. Read "
            "it. Nothing in this brief restates it and no summary of it exists that you "
            "should trust over the file itself.\n\n"
            "## What this feature changed\n"
            + ("\n".join(f"- {p}" for p in sandbox.changed_files()) or "- (nothing)"),
            "---\n\n# How this project runs its own tests\n\n"
            + (gate_commands or "(none recorded)"),
            "---\n\n# Where your probes go\n\n"
            "Create these directories and write each probe into the one its file type "
            "belongs in. They are where this project's own test runners are proved to "
            "collect and configure a file:\n\n" + where + "\n\n"
            "Anything you write outside them is discarded unread -- including any change "
            "you make to "
            "the code you are attacking, which cannot help you: the probes are re-run "
            "against the real tree, where your edit does not exist.\n\n"
            "Run them as you go. A probe that fails for the wrong reason costs a human "
            "real minutes to disprove, and you are the only agent in this system in a "
            "position to check before you hand it over.",
            focus,
        ])

        # The worktree is made from the sandbox's HEAD, so anything not committed
        # is not in it -- and a breaker probing the code as it was before the last
        # repair produces findings about a repository nobody has. Every caller
        # commits before reaching here; this is the check that says so out loud if
        # one ever stops, because the failure is otherwise silent and reads as a
        # feature that is broken.
        uncommitted = uncommitted_paths(sandbox.path)
        with Worktree(sandbox.path, label=f"breaker-{round_index}") as tree:
            assert tree.path is not None
            session = await self._author_files(
                role="breaker", brief=brief, tree=tree.path, confine=confine,
                system=system,
                environment=self.unit_env(
                    f"{state.feature_id}-breaker-{round_index}", role="breaker"),
                on_environment=self.env_recorder(
                    store, state, unit="breaker", role="breaker",
                    round_index=round_index))
            store.append("breaker_session", {
                "round": round_index,
                "files": [f.path for f in session.files],
                "outside": session.outside,
                "attempts": session.attempts,
                "cost_usd": session.cost_usd,
                "log_tail": session.log[-4000:],
            }, role="breaker", spec_hash=state.spec_hash)
            if not session.files:
                from ...executors import harness_unstarted
                report.unstarted = harness_unstarted(session.log)
                report.note = (
                    f"the breaker could not start, so no probe was written: {report.unstarted}"
                    if report.unstarted else "the harness wrote no probe on either attempt")
                return BreakerSuite(strategy="", notes=report.note), []
            answer, note = await self._account_for(
                role="breaker", system=system, brief=spec_text(spec), session=session,
                model=BreakerAccount,
                asked=("Account for the probes you wrote. For every file above give its "
                       "`path` exactly as shown and the `hypothesis` it encodes -- a claim "
                       "about the code, in one line, because if the probe fails that "
                       "sentence is the finding a human reads. Give each one a `kind`: "
                       "`regression` if it still passes once the defect is fixed, and then "
                       "`passes_when` saying what correct code does when it runs; "
                       "`demonstration` if it proves the defect is there now and would stop "
                       "meaning anything afterwards. A `regression` with no `passes_when` is "
                       "read as a demonstration, and a demonstration is not worth less -- a "
                       "mislabel is, and the label is checked by running the probe. Name the "
                       "`command` that runs these and nothing else, from the repository "
                       "root. Put what you attacked and could not break in `conceded`."),
            )
            suite = assemble_breaker_suite(session, answer, note)
            for test in suite.tests:
                test.path = self._probe_home(state, test.path, test.kind)
            if uncommitted:
                suite.notes = "\n\n".join(filter(None, [
                    suite.notes,
                    "The sandbox had uncommitted changes when these probes were "
                    "written, and the breaker's checkout was taken from the last "
                    "commit -- so it attacked the code without them. Treat every "
                    f"probe here as provisional: {', '.join(uncommitted[:10])}"]))
        store.append("breaker_suite", suite, role="breaker",
                     model=self._answered_by("breaker"), spec_hash=state.spec_hash,
                     meta={"round": round_index}, prompt=brief)
        return suite, list(suite.tests)

    async def _run_breaker(
        self, store: EvidenceStore, state: FeatureState, spec: Spec, sandbox: Sandbox,
        runner, accumulated: list[BreakerTest], round_index: int, focus: str,
    ) -> tuple[list[BreakerTest], BreakerReport]:
        """Write adversarial tests against the finished code, then run them.

        The epistemics are deliberately the inverse of the oracle's. The oracle
        has never seen the implementation, so *green* is its signal. The breaker
        has read all of it, so only *red* is: a passing breaker test proves
        nothing and is never counted as verification, never enters the
        traceability matrix, and never gates the run. A failing one is a finding
        with a real process behind it rather than a claim to be believed --
        which is the whole difference between this and the adversary.

        The tests are written, executed and then removed. They are kept in the
        evidence store and re-applied every round, so a repair that breaks
        something the breaker already probed shows up as a regression; but they
        never land on the branch, where they would be collected by the project's
        own test gate and turn its signal into noise.
        """
        cfg = self.config.rework
        role = self.config.roles.get("breaker")
        report = BreakerReport()
        if role is None or not role.enabled:
            report.note = "no breaker configured"
            return accumulated, report

        gate_commands = "\n".join(f"- {g.name}: {g.command}" for g in self.project.judging_gates)
        system = self.role_prompt("breaker") + cleanup_context(self.project.state.testing)

        # Attack once; re-run for ever.
        #
        # Authoring a fresh suite every repair round has cost one feature four
        # agentic sessions -- read the checkout, write a probe, run it, watch it
        # fail, check it failed for the reason claimed -- for nine probe files
        # between them. Eleven million tokens, about 1.2M per file, on a route
        # whose quota it exhausted before the review panel ran.
        #
        # The re-running is free: `accumulated` carries every probe
        # forward and they all run again each round, which is what catches a
        # repair breaking something that used to pass. What was paid for four
        # times was the *inventing*, and a repair loop working inside a frozen
        # spec is not a new system to attack -- it is the same one with a
        # narrower defect. Rounds 1-3 wrote two probes each and the suite they
        # were added to was re-run regardless.
        #
        # Unless nothing came of the first attempt. A session that failed, was
        # refused, or wrote nothing leaves no probes to re-run, and skipping on
        # that would turn one bad round into a feature with no adversary at all.
        reauthor = round_index == 0 or not accumulated
        if not reauthor:
            report.note = (
                f"the breaker attacked this code at round 0 and is not attacking it again; "
                f"its {len(accumulated)} probe(s) are re-run here, so a repair that breaks "
                "one still shows up as a regression."
            )
            # The command comes forward with the probes. It was the authored
            # suite's, and without it `accumulated` is a list of files nothing
            # runs -- which would turn "attack once" into "attack once and
            # never check it again", the opposite of the point.
            earlier = store.payload("breaker_suite") or {}
            carried = (earlier.get("command") or "") if isinstance(earlier, dict) else ""
            suite, fresh = BreakerSuite(strategy=report.note, command=carried), []
        else:
            try:
                suite, fresh = await self._author_probes(
                    store, state, spec, sandbox, gate_commands, focus, system,
                    round_index, report)
            except Exception as exc:
                report.note = f"the breaker could not produce a suite: {exc}"
                store.append("breaker_failed", {"round": round_index, "error": str(exc)},
                             role="breaker", spec_hash=state.spec_hash)
                return accumulated, report

        # Every regression probe the breaker has written, re-run every round. A
        # repair that breaks one which used to pass is a regression, and that is
        # only visible if the old probes are still there.
        #
        # Demonstrations are not among them. A demonstration proves the defect
        # is present *now*, which makes it worth exactly one round: once the
        # defect is gone the construction has nothing left to say, and can only
        # hang, error, or reward some different defect. Re-running one buys a
        # fact about the probe rather than about the code, and it is not free --
        # two of them have cost a feature 1801s in round 1 and 1801s again in
        # round 2, on a run capped at two rounds, leaving every finding it
        # raised `unattempted`.
        #
        # They are retired rather than forgotten: the probe, its hypothesis and
        # its result stay in the ledger, and the finding it raised keeps its own
        # record. What stops is the re-running.
        by_path = {t.path: t for t in accumulated if t.kind == "regression"}
        for test in fresh:
            by_path[test.path] = test
        tests = list(by_path.values())
        report.tests_written = len(fresh)

        # Retired, not forgotten. The probes still come forward in the returned
        # list so their hypotheses stay available to `breaker_findings` and the
        # findings they raised keep their records; only the running stops, and
        # the report names them every round rather than only the round the
        # re-running stopped.
        retired = [t for t in accumulated if t.kind != "regression" and t.path not in by_path]
        report.retired = sorted(t.path for t in retired)

        # And what an earlier round killed on the clock. A probe that stopped
        # terminating is reporting about itself, and running it again buys the
        # same timeout, the same `exit -9` and the same nothing -- 1801s of it
        # per round, on one feature.
        held = [t for t in tests if t.quarantined_in_round is not None]
        report.quarantined = sorted(t.path for t in held)
        tests = [t for t in tests if t.quarantined_in_round is None]
        carried = tests + held + retired
        if report.retired and not report.note:
            report.note = (
                f"{len(report.retired)} demonstration probe(s) are not re-run here: a probe "
                "that proves a defect is present now stops meaning anything once the defect "
                "is gone. They are in the ledger with the results they got."
            )

        command = (cfg.breaker_command or suite.command or "").strip()
        per_file = ([TestFileCommand(match="*", command=cfg.breaker_file_command.strip())]
                    if cfg.breaker_file_command.strip()
                    else list(self.project.state.test_file_commands))
        if not tests or not (command or per_file):
            # `_author_probes` may already have said something more specific --
            # that the harness wrote nothing, say -- and "no adversarial tests to
            # run" is the same fact with the reason removed.
            report.note = report.note or (
                "no adversarial tests to run" if not tests
                else "the breaker declared no command to run its tests, and none is configured")
            return carried, report

        writes = [FileWrite(path=t.path, contents=t.contents, purpose="adversarial probe")
                  for t in tests]
        # Never over a file that belongs to somebody else. Probes are deleted
        # after they run, so a file sitting at a probe's path is usually
        # someone else's -- in a directory shared with the blind suite, an
        # oracle test of the same name would be overwritten and then deleted.
        #
        # A probe we promoted is the exception, and getting this wrong breaks
        # the thing promotion exists for. A kept probe is on the branch, so on
        # the next round the guard would find a file at its path, refuse the
        # write, drop the probe from `tests` and report "the breaker's probes
        # could not be run; they prove nothing" -- a round that ran nothing,
        # wearing the costume of an environment failure, because the probe had
        # passed. Its content on disk is byte-identical to the probe carrying
        # it, which is what makes this decidable without asking anything: a
        # file we would write unchanged is ours.
        ours, taken = [], []
        for write in writes:
            here = sandbox.path / write.path
            if not here.exists():
                continue
            try:
                same = here.read_text(encoding="utf-8") == write.contents
            except (OSError, UnicodeDecodeError):
                same = False
            (ours if same else taken).append(write.path)
        writes = [w for w in writes if w.path not in taken]
        tests = [t for t in tests if t.path not in taken]
        applied, rejected = self._apply_writes(sandbox, writes)
        # A promoted probe is not written again -- it is already exactly there
        # -- and it is still applied, because what `tests_applied` means is
        # "this ran", and the run is what the round is for.
        applied = sorted(set(applied) | set(ours))
        rejected += [f"{p}: refused -- a different file is already there" for p in taken]
        report.tests_applied = applied
        report.rejected += rejected
        started = time.monotonic()
        try:
            if per_file:
                # One command per probe, by the rules this project declared for
                # running one test file -- the same ones the blind suite runs by.
                # A probe is a test file, and a suite-wide command the breaker
                # typed itself is how every round came to point the runner at a
                # directory that did not exist: exit 4, nothing ran, and the
                # packet said one probe had failed.
                result, exits = await run_per_file(
                    per_file, [t.path for t in tests], sandbox.path, runner,
                    timeout_s=cfg.breaker_timeout_s, name="breaker",
                    timeouts={t.path: probe_budget(t, cfg) for t in tests})
                report.ran = result.started
                report.command = result.command

                # What each probe cost, so the next round measures it against
                # itself. Only a run that finished: a killed process took its
                # budget, which is a fact about the clock and not about the
                # probe.
                killed = set(result.timed_out_files)
                for test in tests:
                    seconds = result.file_seconds.get(test.path)
                    if seconds is not None and test.path not in killed:
                        test.baseline_s = seconds

                # A killed probe is not a failing probe. `gate_outcome` already
                # says why for a whole gate -- "a gate that never finished did
                # not report on anything" -- and filing one as a failure is how
                # a hang came to wear the breaker's hypothesis as a finding,
                # asserting a claim about the code that no run supports.
                report.timed_out = sorted(killed)
                for test in tests:
                    if test.path in killed:
                        test.quarantined_in_round = round_index
                failed = [p for p, code in exits.items() if code != 0 and p not in killed]
                # Non-zero for a killed probe too: nothing about this run was
                # clean. What changes is which finding it produces, not whether
                # it produced one.
                report.exit_code = 1 if (failed or killed) else 0
                report.output_tail = result.output_tail[-8000:]
                report.duration_s = round(time.monotonic() - started, 2)
                if not result.started:
                    report.note = "the breaker's probes could not be run; they prove nothing"
                elif killed and not failed:
                    report.note = (
                        f"{len(killed)} probe(s) were killed on the clock rather than failing, "
                        "so they reported on nothing and are not blamed for anything. They are "
                        "not run again."
                    )
                elif failed and len(failed) == len(exits) and len(exits) > 1 and not any(
                        c.status == "failed" for c in result.cases):
                    # Every probe failing is what a broken runner looks like, and
                    # it is also what many real defects look like. The runner's
                    # own report tells them apart: a probe whose test ran and
                    # failed an assertion is a defect, and a runner that cannot
                    # start reports no such test. Only with no report at all is
                    # the ambiguity said rather than filed -- on one project two
                    # probes found two real bugs, and blaming the runner would
                    # have blamed neither.
                    report.note = (
                        f"all {len(failed)} probes failed when run one at a time, which is "
                        "what a probe runner that cannot start looks like as well as what "
                        "a suite of real defects looks like. No probe is blamed on that; "
                        "set `rework.breaker_file_command` if this project needs a command "
                        "of its own to run one probe."
                    )
                else:
                    report.failing = failed
            else:
                try:
                    execution = await runner.execute(
                        command, cwd=sandbox.path, timeout_s=cfg.breaker_timeout_s,
                        network=False)
                except Exception as exc:
                    # The breaker is optional evidence; the packet is not.
                    report.note = f"the breaker's command could not be run: {exc}"
                    store.append("breaker_failed", {"round": round_index, "error": str(exc)},
                                 role="breaker", spec_hash=state.spec_hash)
                    return carried, report
                report.ran = execution.started
                report.command = command
                report.exit_code = execution.exit_code
                report.output_tail = execution.output[-8000:]
                report.duration_s = round(time.monotonic() - started, 2)
                if not execution.started:
                    report.note = "the breaker's command could not start; its tests prove nothing"
                elif execution.exit_code != 0:
                    # This project declares no way to run one test file, so a
                    # non-zero exit cannot be attributed to a named probe.
                    report.note = (
                        "the breaker suite exited non-zero and this project declares no way "
                        "to run one test file, so no finding can be attributed to an "
                        "individual probe")
            await self._promote_probes(sandbox, runner, tests, report, per_file)
        finally:
            # Off the branch before anything commits. The human's diff is the
            # feature, not the attack surface someone probed it with.
            #
            # Except what earned a place in it. A promoted probe is part of what
            # this feature delivers -- the same standing as a test the
            # integrator wrote -- so it belongs in the diff and is reviewed
            # there. Promotion is this deletion being withheld and nothing else:
            # no file is moved, so what ships is byte-identical to what was
            # proved to pass, at the path it was proved at.
            keep = set(report.promoted)
            for test in tests:
                if test.path in keep:
                    continue
                try:
                    target = safe_join(sandbox.path, test.path)
                except PathEscape:
                    continue
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    continue
                # And the directories they were in, while they are empty. A
                # bare `tests/breaker/` left behind is still a change to the
                # tree the human reviews.
                parent = target.parent
                while parent != sandbox.path and parent.is_relative_to(sandbox.path):
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
        # Committed here, not left for whatever commits next. Promotion is a
        # deletion withheld, and a file merely not deleted is invisible: the
        # packet's own commit is `commit_file` on one document, a round that
        # ends without a repair commits nothing at all, and the next `reset_to`
        # of a reverted round would take an uncommitted probe with it. A
        # pathspec, because this runs between commits rather than after the
        # last one and the rest of the tree is not this phase's to sweep up.
        # What was kept, and what stopped being kept. A probe promoted in an
        # earlier round is on the branch; if this round finds it failing it is
        # deleted from the tree above, and the deletion has to reach the branch
        # too -- otherwise the packet says the probe is gone while the branch
        # still carries it, and the next round finds a file at its path that
        # nothing accounts for. `git add` stages a removal the same as an
        # addition, so one commit says both.
        earlier = promoted_this_pass(store.records())
        dropped = [p for p in earlier if p not in set(report.promoted)]
        if report.promoted or dropped:
            sha, problem = sandbox.commit_files(
                report.promoted + dropped,
                f"factory: probes kept from the breaker's suite\n\nspec {state.spec_hash}")
            if problem and report.promoted:
                report.note = "\n\n".join(filter(None, [report.note, (
                    f"{len(report.promoted)} probe(s) passed and could not be committed, so "
                    f"they were not kept: {problem}")]))
                # Off the disk as well as out of the report. A probe held back
                # from promotion but left in the tree is the worst of both: the
                # packet says it was deleted, and the next round's `add -A`
                # puts it on the branch anyway, unreported. The source is in
                # the ledger either way.
                for path in report.promoted:
                    try:
                        safe_join(sandbox.path, path).unlink(missing_ok=True)
                    except (PathEscape, OSError):
                        continue
                report.promoted = []
            elif not problem:
                store.append("writes", {
                    "applied": list(report.promoted), "rejected": list(dropped),
                    "stage": "breaker-promote", "branch": sandbox.branch, "commit": sha,
                }, role="orchestrator", spec_hash=state.spec_hash)
        store.append("breaker", report, role="orchestrator", spec_hash=state.spec_hash,
                     meta={"round": round_index})
        return carried, report

    async def _promote_probes(
        self, sandbox: Sandbox, runner, tests: Sequence[BreakerTest],
        report: BreakerReport, per_file: Sequence[TestFileCommand],
    ) -> None:
        """Which regression probes have earned a place in the project's suite.

        One executable check does two jobs. It is the admission criterion --
        a test that cannot pass the code it is going to guard is not a
        regression test -- and it is the detector for a probe whose author
        called it a regression when it was a demonstration, because a
        demonstration cannot pass the repaired code by construction. So the
        classification never has to be trusted: a mislabel fails promotion by
        itself, and nobody has to read the probe to find out.

        Two real probes are the case. Both were demonstrations -- each held two
        requests at a barrier until both reached commit, which the serialising
        repair makes unreachable -- and one could not have passed *any* correct
        implementation, since it also treats the correct rejection of a 21st
        tag as an unhandled error. Round 0 ran them in 0.26s and 0.16s and they
        found two real defects. Rounds 1 and 2 then re-ran them for 1801
        seconds each, on a run capped at two rounds. This check declines them
        both in under a second.

        Decided again every round, so the last round's decision is the one that
        reaches a human: a probe kept in round 1 and broken by round 2's repair
        is deleted in round 2. That is what makes the decision safe to take
        without knowing which round is the last one.

        Deliberately not checked here: whether the finding this probe raised
        ended `repaired`. It is entailed. A probe-derived finding exists while
        its probe fails, so a probe that passes has no finding of its own left
        open, and asking the ledger would be asking a second time.
        """
        cfg = self.config.rework
        report.promoted, report.flaky = [], []
        candidates = [t for t in tests if t.kind == "regression"]
        if not candidates:
            return
        if not per_file:
            # Without a per-probe command a non-zero exit names no probe, so
            # "this one passed" is not a thing this run can say. Nothing is kept
            # on a suite-wide green: it would be keeping files on the strength
            # of other files having passed.
            report.note = "\n\n".join(filter(None, [report.note, (
                f"{len(candidates)} probe(s) could have been kept in the project's suite, and "
                "none was: this project declares no way to run one test file, so no probe can "
                "be shown to pass on its own. Set `rework.breaker_file_command`.")]))
            return
        if not report.ran:
            return

        # Not failing is not the same as passing. A probe killed on the clock
        # is in neither list, and promoting one would put a test that does not
        # terminate into the project's suite on the strength of it not having
        # failed.
        settled = set(report.failing) | set(report.timed_out)
        passed = [t for t in candidates if t.path not in settled]
        if not passed:
            return

        # The round has already run each of these once. `promote_runs` is the
        # total a probe must survive, so what is left is the repeats -- and at 1
        # the repeat check is off and the round's own run is the whole of it.
        repeats = max(0, int(cfg.promote_runs) - 1)
        paths = [t.path for t in passed]
        budgets = {t.path: probe_budget(t, cfg) for t in passed}
        for _ in range(repeats):
            if not paths:
                break
            _, exits = await run_per_file(
                list(per_file), paths, sandbox.path, runner,
                timeout_s=cfg.breaker_timeout_s, name="breaker-promote",
                timeouts=budgets)
            # A probe the repeat run could not account for is not promoted. An
            # absent exit code is an absent pass.
            fell = [p for p in paths if exits.get(p, 1) != 0]
            if fell:
                report.flaky += [p for p in fell if p not in report.flaky]
                paths = [p for p in paths if p not in set(fell)]
        report.promoted = sorted(paths)
        report.flaky = sorted(report.flaky)
        if report.flaky:
            report.note = "\n\n".join(filter(None, [report.note, (
                f"{len(report.flaky)} probe(s) passed and then did not, across "
                f"{cfg.promote_runs} runs, so they were not kept: a race test that passes by "
                "not hitting the window would be a permanent intermittent failure in this "
                f"project's suite. {', '.join(report.flaky)}")]))
