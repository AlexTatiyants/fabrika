"""Running the blind suite, and collecting the recordings and probes it leaves.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable, Sequence

from ...config import GateConfig
from ...gates import run_gate, run_per_file
from ...sandbox import Sandbox
from ...schemas import FeatureState, GateResult, TestFileCommand
from ...store import EvidenceStore
from ...workspace import PathEscape, safe_join

from ..recordings import trace_player


class BlindSuiteMixin:
    async def _run_blind_suite(
        self, sandbox: Sandbox, runner, witness: Sequence[str], declared: str,
        after_file: Callable[[str], None] | None = None,
    ) -> GateResult | None:
        """Run the oracle's suite as its own gate, or say plainly that it did not.

        This exists because of where these tests would otherwise live. Written
        into the project's own directory, they are collected by the project's
        `pytest`, and a blind suite that could not reach a fixture it had
        assumed into existence has reported 39 errors as the *feature's* test
        gate failing. The project's linter lints them too, and has put 41 errors
        into files no repairer is permitted to touch, so the loop stopped with
        nothing it was allowed to fix. Neither number was about the feature.

        The breaker runs the same way. The separation is
        what lets a failure mean something: a red here is a criterion that does
        not hold, and a red in the project's gates is the project's own suite
        saying something else.

        Returning `None` is not an option when the suite could not run -- that
        would leave `compute_qa` inferring verification from the project's
        gates, which is exactly what this exists to stop. A suite that cannot
        run comes back as a failed gate, and the criteria it was supposed to
        check come back unverified, which is the truth.

        `witness` is the oracle's *asserting* files and not its support files.
        Support is written to disk and loaded by the runner in the ordinary way;
        invoking a conftest or a README as though it were a test would fail for
        a reason that has nothing to do with the feature. A support file that is
        broken still attributes correctly under this arrangement: a conftest
        that will not import fails every test file's
        own command, so every criterion it touches goes red rather than the
        whole suite going red as one unattributable number.
        """
        cfg = self.config.rework
        own_dir = (cfg.oracle_dir or "").strip("/")
        if not witness:
            return None

        # The project's command, never the model's. `declared` is accepted only
        # as a last resort for a project that has cleared its own, and a blind
        # agent inventing a path is how a command comes to name a directory
        # nothing was written to.
        command = (self.project.state.oracle_command or declared or "").strip()
        command = command.replace("{oracle_dir}", own_dir)
        name = "blind-tests"
        if not command:
            return GateResult(
                name=name, command="", exit_code=1, passed=False, started=False,
                output_tail=(
                    "The oracle declared no command to run its suite, and this project sets "
                    f"no `oracle_command`. Its tests are on the branch under {own_dir}/ "
                    "and nothing ran them, so no criterion is verified by a blind test. This is "
                    "a fact about the run, not about the code."
                ),
            )

        # Load it before running it. The two failures are different facts and
        # want different readers: a suite that will not import is the oracle's
        # bug, and every criterion under it is unverified for that reason rather
        # than for anything about the feature.
        # The per-file rules, resolved first, because whether the collect probe
        # is worth running at all depends on whether they exist.
        rules = list(self.project.state.test_file_commands)
        if cfg.oracle_file_command.strip():
            rules = [TestFileCommand(match="*", command=cfg.oracle_file_command.strip())]
        if cfg.oracle_file_report_command.strip():
            rules = [TestFileCommand(match=r.match, command=r.command, collect=r.collect,
                                     report=cfg.oracle_file_report_command.strip())
                     for r in rules]
        # `collect` is carried through, or the probe loses its whole point.
        # This line rebuilds each rule to substitute `{oracle_dir}`; rebuilt
        # without the load command, every rule reaching the probe would have an
        # empty one, no loader would be built, and the probe would return
        # "nothing unloadable" for a suite in which two files did not load at
        # all. The gate would then report criteria as failing on evidence that
        # was really a missing import -- the exact confusion the probe exists to
        # end.
        rules = [TestFileCommand(match=r.match,
                                 command=r.command.replace("{oracle_dir}", own_dir),
                                 collect=r.collect.replace("{oracle_dir}", own_dir),
                                 report=r.report.replace("{oracle_dir}", own_dir))
                 for r in rules]

        # Only when the suite runs whole.
        #
        # The probe exists to separate "the oracle's files will not load" from
        # "the feature is broken", which matters when one exit code covers every
        # criterion. Running one command per file already does that, better: a
        # file that will not import fails its own command and costs only the
        # criteria it covered.
        #
        # And it is a single global command against a single directory, so it
        # cannot describe a project whose blind tests live in two places under
        # two runners -- which is the normal case. Run there, it does exactly
        # the damage it exists to prevent: with the tests written to the two
        # proved placements, it looks in `rework.oracle_dir`, finds nothing, and
        # reports the whole lane unloadable. Twenty-five criteria have come back
        # unverified that way, with a green board behind them.
        collect = ("" if rules else (self.project.state.oracle_collect_command or "").strip().replace(
            "{oracle_dir}", own_dir))
        if collect:
            probe = GateConfig(name="blind-collect", command=collect,
                               timeout_s=cfg.oracle_collect_timeout_s)
            loaded = await run_gate(probe, sandbox.path, runner)
            if loaded.started and not loaded.passed:
                return GateResult(
                    name=name, command=collect, exit_code=loaded.exit_code,
                    passed=False, started=True,
                    output_tail=(
                        "The blind suite could not be loaded, so it was not run. No "
                        "criterion is verified, and none of that is a statement about "
                        "the feature: these are the oracle's own files failing to "
                        "import or collect.\n\n" + (loaded.output_tail or "")
                    ),
                )

        # One command per blind test file, where the project said how.
        #
        # The file's exit code is that file's verdict, and the oracle already
        # recorded which criteria each file covers -- so attribution falls out
        # of running them separately and nothing has to read what a runner
        # printed. Reading it would mean a table of regexes recognising runners
        # by their console summaries, and a search of the last 4,000 characters
        # for a filename beside the word FAILED.
        #
        # The cost is one process per file, and this is the only suite that
        # needs it. The project's own gates never decide a criterion, so one
        # exit code is their whole answer and they still run as one command.
        # The project's own answer, with a configuration override for the rare
        # case where a human needs one. How to run a single test file is a fact
        # about the repository -- the surveyor reads it there and a human
        # approves it at gate 0 -- not a setting of this tool.
        if rules:
            # Load every file before running any of them.
            #
            # An exit code cannot tell a broken import from a failed assertion,
            # and they are opposite facts: the second is the finding this
            # system exists to produce, the first is the oracle's own bug and
            # settles nothing. One run lost six criteria to the difference -- a
            # blind UI test imported `../../frontend/src/pages/TrialDetail`
            # from inside `frontend/src/__tests__/`, resolved to nothing,
            # collected no tests, and was reported to a human as AC-18 through
            # AC-23 failing. A review agent then filed the components as a
            # blocker on that evidence.
            #
            # Not parsed from the console. The load is its own command with its
            # own exit code, which is the only kind of answer this file trusts;
            # `record_evidence` says what reading a screen costs.
            unloadable = await self._collect_probe(rules, witness, sandbox, runner)
            # The reset, where the project declared how and a human turned it on.
            # It is off by default because it costs a migrate and a seed per
            # file; what it buys is that a file's verdict is about the file.
            env = self.project.environment
            between = (list(env.test_prepare)
                       if cfg.oracle_reset_between_files and env else [])
            # `repeat`: each file that passes runs again straight after, which is
            # how a test that does not clean up after itself shows. See
            # `check_leaks`.
            result, _ = await run_per_file(
                rules, list(witness), sandbox.path, runner,
                timeout_s=cfg.oracle_timeout_s, name=name, prepare=between,
                after_file=after_file, repeat=True)
            # Merged rather than assigned: `run_per_file` reports the files whose
            # reset failed in this same field, and both are the same fact.
            result.named_unloadable = sorted(
                set(result.named_unloadable) | set(unloadable))
            # The load error says more than the run that followed it.
            result.file_output.update({p: out for p, out in unloadable.items() if out})
            if unloadable:
                result.output_tail = (
                    f"{len(unloadable)} of the oracle's files could not be loaded, so "
                    "the criteria they cover are unverified rather than failed: "
                    + ", ".join(unloadable)
                    + ".\n\nNothing in that sentence is about the feature. A file that "
                    "will not import, will not parse, or contains no test the runner can "
                    "find is the test author's mistake, and the run below repeats it "
                    "once per file.\n\n"
                    + result.output_tail)
            if not result.started:
                result.output_tail = (
                    "The blind suite did not run every file it was given -- a file no "
                    "rule claims, or a command that could not start.\n\n"
                    + result.output_tail)
            return result

        # No per-file command: the suite runs whole, and a failure cannot be
        # traced to a criterion. Recorded as such rather than guessed at.
        gate = GateConfig(name=name, command=command, timeout_s=cfg.oracle_timeout_s)
        result = await run_gate(gate, sandbox.path, runner)
        result.witnessed = list(witness)
        if not result.started:
            result.output_tail = (
                f"The blind suite's command could not start: {command}\n\n"
                + result.output_tail)
        return result

    def _collect_traces(
        self, store: EvidenceStore, state: FeatureState, sandbox: Sandbox,
        test_path: str = "", round_index: int = 0, before: int = 0,
    ) -> list[str]:
        """Recordings a test framework left behind, kept where a human can open
        them.

        The gap this closes is between "a test passed" and "show me". A
        screenshot cannot close it, because it is one moment somebody chose:
        on one run a picture named for a criterion about a tag
        appearing *before* the server responds showed the error message from
        after it failed, because that was the state the test happened to
        reach. A Playwright trace has no moment to choose -- it is a
        frame-by-frame screencast with the DOM, the network and the console at
        every action -- so a reader can watch the thing the criterion is about
        instead of trusting that the right instant was frozen.

        Swept from directories the project declared, because nothing here
        knows what a test runner is called or where it writes. Called once per
        blind file with that file's path, because a runner that clears its
        output directory on start has erased every earlier file's recording by
        the time the suite ends -- and once more afterwards, unattributed, for
        whatever a run that never reached a blind file left behind.

        Named for the test's own directory, so the same test recorded again is
        the same name and replaces the earlier file: the latest recording of a
        test is the one about the tree in front of the reader.

        `before` is how many had been recorded when this assessment began, so
        the ceiling is spent per round. Spent per feature, the first round can
        take 18 of 20, the second 2 and the third none, and the packet then
        shows recordings of a tree two repairs gone while the tree it is asking
        a person to rule on has not a single one.
        """
        cfg = self.config.rework
        # Off the project, not off this tool's configuration: where a runner
        # writes is a fact about one repository, and that file holds one
        # setting for every project the factory manages.
        dirs = [d for d in (self.project.state.trace_dirs or []) if (d or "").strip()]
        if not dirs:
            return []
        suffixes = {s.lower() for s in (cfg.trace_suffixes or []) if s}
        target = store.artifact_dir("traces")
        already = sum(len(p.get("files") or []) for p in store.payloads("traces")) - before
        kept: list[str] = []
        titles: dict[str, str] = {}
        for where in dirs:
            try:
                found = safe_join(sandbox.path, where.strip("/"))
            except PathEscape:
                continue
            if not found.is_dir():
                continue
            for path in sorted(found.rglob("*")):
                if already + len(kept) >= max(0, cfg.traces_max):
                    break
                if not path.is_file() or path.suffix.lower() not in suffixes:
                    continue
                try:
                    if path.stat().st_size > max(0, cfg.traces_max_bytes):
                        # Named relative to the declared directory, not to the
                        # checkout: `safe_join` resolves symlinks and a sandbox
                        # path does not, so on a machine whose temp directory
                        # is a link the two have no common prefix and
                        # `relative_to` raises.
                        store.append("trace_too_big", {
                            "path": f"{where.strip('/')}/"
                                    f"{path.relative_to(found).as_posix()}",
                            "bytes": path.stat().st_size, "cap": cfg.traces_max_bytes,
                        }, role="orchestrator", spec_hash=state.spec_hash)
                        continue
                    # The test's own directory name is the only thing that says
                    # which test this is -- a trace is always `trace.zip` --
                    # so the path becomes the name.
                    rel = path.relative_to(found).as_posix().replace("/", "-")
                    shutil.copy2(path, target / rel)
                    kept.append(rel)
                    # And unpacked, so the packet can walk a reader through it
                    # without asking them to install anything. The archive
                    # stays beside it for whoever wants the whole viewer.
                    titles[rel] = self._unpack_trace(target, rel)
                except OSError:
                    continue
            # Cleared: a run that left these in the checkout would commit them
            # on the next round, and the diff a human reads would grow a
            # suite's worth of zip files.
            shutil.rmtree(found, ignore_errors=True)
        if kept:
            # The test's own title is what ties a recording to a criterion --
            # `[AC-13] the control filters typed input` -- and the file is what
            # ties it to the oracle's account of which criteria that file
            # covers. Both are kept here, so a coverage check can join them
            # without reopening an archive.
            store.append("traces", {
                "files": kept, "from": dirs, "titles": titles,
                "by_test": ({name: test_path for name in kept} if test_path else {}),
            }, role="orchestrator", spec_hash=state.spec_hash,
               meta={"round": round_index})
        return kept

    def _unpack_trace(self, target: Path, name: str) -> str:
        """The frames and steps of one recording, beside the recording.

        Done at collection rather than on request: it is the same work either
        way, and a packet read six times should not unzip the same archive six
        times. Failure is silent by design -- the archive is still there and
        still downloadable, and a recording nobody can step through is a
        smaller loss than a phase that stopped because a zip was odd.
        """
        stem = name[:-4] if name.endswith(".zip") else name
        try:
            frames, manifest = trace_player(target / name)
        except Exception:
            return ""
        if not manifest:
            return ""
        where = target / f"{stem}.frames"
        try:
            where.mkdir(parents=True, exist_ok=True)
            # The old set first. A second unpack that keeps fewer frames than
            # the first -- which is what dropping the blank ones does -- would
            # otherwise leave its tail behind: files the manifest no longer
            # lists, served by name to anyone who asks for them.
            kept = {frame for frame, _ in frames}
            for stale in where.glob("f*.jpeg"):
                if stale.name not in kept:
                    stale.unlink()
            for frame, body in frames:
                (where / frame).write_bytes(body)
            (target / f"{stem}.player.json").write_text(
                json.dumps(manifest, indent=1), encoding="utf-8")
        except OSError:
            return str(manifest.get("title") or "")
        return str(manifest.get("title") or "")

    async def _collect_probe(
        self, rules: Sequence[TestFileCommand], witness: Sequence[str],
        sandbox: Sandbox, runner,
    ) -> dict[str, str]:
        """Which of the oracle's files will not even load, and what each said.

        Reuses `run_per_file` with each rule's `collect` command in place of its
        `command`, so there is one execution path for both questions and the
        answer is an exit code either way.

        Returns nothing when no rule carries a `collect`, which is every project
        whose survey did not record one. That is the honest default: an absent
        probe draws no distinction, and a drawn distinction is never guessed.
        """
        cfg = self.config.rework
        override = cfg.oracle_file_collect_command.strip()
        loaders = [
            TestFileCommand(match=r.match,
                            command=(override or r.collect).strip())
            for r in rules if (override or r.collect).strip()
        ]
        if not loaders or not witness:
            return {}
        probe, _ = await run_per_file(
            loaders, list(witness), sandbox.path, runner,
            timeout_s=cfg.oracle_collect_timeout_s, name="blind-collect")
        # A probe that could not run says nothing. Treating "the loader itself
        # failed to start" as "every file is broken" would take the whole suite
        # out over a configuration mistake -- which is the exact damage a global
        # collect command does when it looks in the wrong place.
        if not probe.started:
            return {}
        return {p: probe.file_output.get(p, "") for p in probe.named_failing}
