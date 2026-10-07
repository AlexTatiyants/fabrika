"""Checks: running them, the services they need, and what their results mean.

Split out of test_invariants.py, which keeps one test per invariant.
"""

from helpers import *  # noqa: F403


# ==========================================================================
# supporting properties
# ==========================================================================


def test_gates_never_invoke_a_model():
    source = strip_prose(factory_source("gates"))
    for term in ("llm", "LLM", "ask(", "model"):
        assert term not in source, f"gates.py mentions {term!r}; a gate is a fact, not an argument"
    assert not hasattr(gates, "LLM")


def test_a_blind_suite_run_as_one_command_cannot_attribute_anything():
    """The arrangement that makes per-criterion verdicts possible, or not."""
    whole = gates.GateReport(results=[
        _reported("blind-tests", passed=True, total=0, per_file=False)])
    findings = pipeline.check_blind_attribution(whole, empty_run_detected=True)
    assert [f.id for f in findings] == ["attribution-1"]
    assert "test_file_commands" in findings[0].recommendation, \
        "the finding does not say the one thing that would fix it"

    # A suite that never started is a different check's business.
    never = gates.GateReport(results=[gates.GateResult(name="blind-tests", started=False)])
    assert pipeline.check_blind_attribution(never, empty_run_detected=True) == []


def test_a_gate_that_ran_and_failed_is_told_apart_from_one_that_never_ran():
    ran = [
        _result("lint", exit_code=1, output_tail="Found 129 errors."),
        _result("types", exit_code=2, output_tail="Found 69 errors in 9 files"),
    ]
    absent = [
        _result("e2e", exit_code=127, output_tail="sh: playwright: command not found"),
        _result("unit", exit_code=1, output_tail="ModuleNotFoundError: No module named 'pytest'"),
        _result("slow", exit_code=-9, timed_out=True, output_tail="timed out after 600s"),
        _result("broken", exit_code=127, started=False, output_tail="could not start"),
    ]
    for r in ran:
        assert gates.gate_outcome(r) == "ran", r.name
    for r in absent:
        assert gates.gate_outcome(r) == "could_not_run", r.name

    # Non-zero and silent settles nothing, and a guess is not available.
    assert gates.gate_outcome(_result("mystery", exit_code=3, output_tail="")) == "unknown"


def test_nothing_in_the_orchestrator_recognises_a_test_runner():
    """The principle this module got wrong for a long time.

    Which runner a project uses is the surveyor's business -- it reads the
    repository, it is one of two roles allowed to name a tool, and a human
    approves what it proposes at gate 0. Everything downstream reads a report.

    It used to be otherwise: a table of regexes here knew what pytest, jest,
    vitest, unittest and go print when they finish. Vitest changed its summary
    line in a minor release -- no colon, `|` for `,`, the total in parentheses
    -- the table stopped matching and said nothing, and that gate's whole signal
    went dark. No counts, and no `zero_ran`, so the check that catches a suite
    exiting zero over nothing had nothing to fire on.
    """
    # Code, not prose. These names appear all over the docstrings here, telling
    # the story of the table that used to exist -- and the honest thing to do
    # about a comment explaining a past mistake is never to delete it so a grep
    # goes quiet.
    source = (strip_prose(factory_source("gates"))
              + strip_prose(factory_source("pipeline"))).lower()
    for runner in ("pytest", "jest", "vitest", "unittest", "go test", "mocha", "rspec"):
        assert runner not in source, (
            f"{runner!r} is named in the orchestrator's code. Which runner a project "
            "uses belongs to the surveyor and the gate config, never to this module."
        )


def test_running_one_file_at_a_time_is_the_whole_attribution_mechanism():
    """No parsing, no matching, no format. An exit code per file.

    What this replaced, twice: a table of regexes that recognised pytest, jest,
    vitest, unittest and go by their console summaries -- which went silently
    blind when vitest renamed a summary line -- and then a JUnit XML reader,
    which was better but still a format, still had dialects, and still required
    half the ecosystems in use to install a reporter package before they could
    onboard at all.

    The surveyor hands over a command with a `{path}` in it. That is the entire
    contract, and nothing here knows what runs.
    """
    import asyncio

    class Stub:
        carries_setup = True

        def __init__(self):
            self.commands = []

        async def execute(self, command, *, cwd, timeout_s, network=True):
            self.commands.append(command)
            bad = "test_b.py" in command
            return gates.Execution(exit_code=1 if bad else 0, output="anything at all")

    runner = Stub()
    paths = ["tests/oracle/test_a.py", "tests/oracle/test_b.py", "tests/oracle/test_c.py"]
    result, exits = asyncio.run(gates.run_per_file(
        [_rule("pretend-runner {path}")], paths, ".", runner, name="blind-tests"))

    assert runner.commands == [f"pretend-runner {p}" for p in paths], \
        "the template was not run once per file, with the path substituted"
    assert result.named_failing == ["tests/oracle/test_b.py"]
    assert exits["tests/oracle/test_b.py"] == 1
    assert not result.passed and result.summary_known
    assert (result.tests_total, result.tests_passed, result.tests_failed) == (3, 2, 1)
    assert result.witnessed == paths

    # The output said nothing useful and it did not matter, which is the point.
    assert "anything at all" in result.output_tail

    # Nothing to run is not a silent pass.
    empty, _ = asyncio.run(gates.run_per_file([_rule("x {path}")], [], ".", Stub()))
    assert empty.zero_ran and not empty.passed


def test_a_ports_freeness_is_asked_about_on_the_wildcard_not_on_loopback():
    """The distinction that made a suite run against a stranger's application.

    A container held `*:8000` on the IPv6 wildcard. `uvicorn --host 127.0.0.1
    --port 8000` bound alongside it perfectly happily -- two listeners, one
    number, different address families -- nothing failed, the readiness probe
    went green, and the tests ran against the other application.

    Asking about the wildcard sees that listener. Asking about loopback does
    not, and would hand back a port that is already spoken for.
    """
    import socket

    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    held.bind(("0.0.0.0", 0))
    held.listen(1)
    taken = held.getsockname()[1]
    try:
        # 200 draws: the allocator must never return a port already listened on.
        assert taken not in {gates.free_port() for _ in range(200)}, \
            "free_port handed back a port something is already listening on"
    finally:
        held.close()

    # It is the kernel's choice, not a constant and not our own arithmetic.
    picks = {gates.free_port() for _ in range(20)}
    assert len(picks) > 1, "free_port returns a fixed number rather than allocating"
    assert all(p > 1023 for p in picks), "a privileged port was handed back"

    src = code_without_prose(gates.free_port)
    assert "0.0.0.0" in src and "127.0.0.1" not in src, (
        "the probe binds loopback, which cannot see a wildcard listener -- the "
        "exact blindness that let two processes hold one port number"
    )


def test_a_services_port_reaches_the_commands_that_have_to_agree_about_it():
    """The service and the tests are written separately and must match.

    A literal port in both is two chances to disagree with each other and one
    chance to agree with somebody else's container. They read one name instead.
    """
    import asyncio

    runner = _SessionRunner()

    async def go():
        async with gates.test_session(
            ".", runner, services=[_service()], prepare=["PREPARE migrate"],
        ) as prepared:
            assert prepared.ready
            await gates.run_per_file([_rule("run {path}")], ["tests/a.py"], ".", runner)
            return prepared

    prepared = asyncio.run(go())
    assert set(prepared.ports) == {"api"} and prepared.ports["api"] > 1023

    # Published under a name every command in the session can read.
    assert gates.with_env("x", {"FACTORY_PORT_API": "1234"}) == "export FACTORY_PORT_API=1234; x"

    # And withdrawn on the way out: a port from a session that has ended must
    # not be exported into whatever runs next.
    assert getattr(runner, "session_env", {}) == {}, \
        "a finished session left its ports exported"


def test_every_command_in_a_session_is_told_where_each_service_is():
    """The address, not only the port.

    Only the port was published, and only the page learned where the API was --
    its build command folded the port into a URL. Test code in Node, seeding a
    board before the page loaded, had nothing to fold it into: Kanban's own
    fixture fell back to `localhost:8300`, its oracle's required a variable
    nobody set, and every browser test died in its fixture three rounds running.
    """
    import asyncio

    runner = _SessionRunner()

    async def go():
        async with gates.test_session(".", runner, services=[_service()]) as p:
            return p, dict(runner.session_env)

    prepared, env = asyncio.run(go())
    port = prepared.ports["api"]
    assert env["FACTORY_PORT_API"] == str(port)
    assert env["FACTORY_URL_API"] == f"http://127.0.0.1:{port}", \
        "the session publishes a port but not the address a test would use"


def test_a_service_that_loses_the_race_for_its_port_is_given_another():
    """Allocation is free-at-that-instant, not free-forever.

    The socket closes here and the service binds a moment later. Nothing that
    hands a port to another process closes that window, so the session notices
    the service did not come up and allocates again.
    """
    import asyncio

    class Contended(_SessionRunner):
        """Never ready on the first port it is given; fine on the second."""

        def __init__(self):
            super().__init__()
            self.first_port = None

        async def execute(self, command, *, cwd, timeout_s, network=True):
            self.log.append(f"exec:{command}")
            if command == "READY":
                port = self.session_env.get("FACTORY_PORT_API")
                if self.first_port is None:
                    self.first_port = port
                return gates.Execution(
                    exit_code=1 if port == self.first_port else 0, output="")
            return gates.Execution(exit_code=0, output="")

    runner = Contended()

    async def go():
        async with gates.test_session(".", runner, services=[_service(timeout=1.0)]) as p:
            return p

    prepared = asyncio.run(go())
    assert prepared.ready, "a service was abandoned on one unlucky port"
    assert runner.log.count("start_service:api") == 2, "the service was not retried"
    assert str(prepared.ports["api"]) != runner.first_port, \
        "the retry reused the port that did not work"
    assert any("retrying on" in line for line in prepared.log), \
        "the retry is invisible to anyone reading the report"


def test_the_harness_owns_the_service_lifecycle_not_the_command_string():
    """Start, wait, prepare once, then hand back a standing environment.

    Every one of those used to be a clause in a shell string the surveyor wrote
    blind -- `nohup ... &`, a `for` loop around `curl`, `kill $UPID`, an exit
    code shuffled past it. Three copies of that existed in one project and the
    newest had lost the `kill`. What makes this testable at all is that the
    process management is code now.
    """
    import asyncio

    runner = _SessionRunner()

    async def go():
        async with gates.test_session(
            ".", runner, services=[_service()],
            prepare=["PREPARE migrate", "PREPARE seed"],
        ) as prepared:
            assert prepared.ready
            runner.log.append("--inside--")
            return await gates.run_per_file(
                [_rule("run {path}")],
                ["tests/oracle/test_a.py", "tests/oracle/test_b.py"], ".", runner)

    result, exits = asyncio.run(go())
    assert runner.log == [
        "open_session",
        # Before anything is stood up: the space probe decides whether there is
        # any point starting, and it costs one exec to ask.
        "exec:df -Pk .",
        "start_service:api",
        "exec:READY",
        "exec:PREPARE migrate",
        "exec:PREPARE seed",
        "--inside--",
        "exec:run tests/oracle/test_a.py",
        "exec:run tests/oracle/test_b.py",
        "stop_services",
        "discard_session",
    ], runner.log

    # Session work happened once; only the files repeat.
    assert runner.log.count("exec:PREPARE migrate") == 1
    assert runner.log.count("start_service:api") == 1
    assert result.named_failing == ["tests/oracle/test_b.py"]
    assert exits["tests/oracle/test_a.py"] == 0


def test_services_come_down_even_when_the_tests_blow_up():
    """Teardown is in a `finally`, because the alternative holds a port."""
    import asyncio

    runner = _SessionRunner()

    async def go():
        async with gates.test_session(".", runner, services=[_service()]):
            raise RuntimeError("the runner fell over")

    try:
        asyncio.run(go())
    except RuntimeError:
        pass
    assert runner.stopped and runner.discarded, \
        "a service outlived the run that started it"


def test_a_service_that_never_comes_up_blocks_everything_after_it():
    """Nothing ran, and nothing may read as though it had."""
    import asyncio

    async def go(runner):
        # The shortest wait there is: what is asserted is the report, not the wait.
        async with gates.test_session(".", runner, services=[_service(timeout=1.0)]) as p:
            return p

    runner = _SessionRunner(ready_after=999)          # READY never succeeds
    prepared = asyncio.run(go(runner))
    assert not prepared.ready
    assert "did not become ready" in prepared.report
    assert runner.stopped and runner.discarded

    refused = _SessionRunner(service_error="no such binary")
    prepared = asyncio.run(go(refused))
    assert not prepared.ready and "could not be started" in prepared.report


def test_a_failed_preparation_stops_before_a_single_test_runs():
    """Every file would fail for the same reason, and each would be written up
    as its own criterion's defect. That shape of finding costs an afternoon."""
    import asyncio

    runner = _SessionRunner(prepare_fails=True)

    async def go():
        async with gates.test_session(
            ".", runner, services=[_service()], prepare=["PREPARE migrate"],
        ) as p:
            return p

    prepared = asyncio.run(go())
    assert not prepared.ready
    assert "preparation step 1 of 1 failed" in prepared.report
    assert not any(l.startswith("exec:run") for l in runner.log), \
        "tests ran against a half-prepared environment"


def test_a_project_with_no_services_opens_no_session():
    """Most projects declare none, and must not pay for the machinery."""
    import asyncio

    runner = _SessionRunner()

    async def go():
        async with gates.test_session(".", runner) as p:
            return p

    assert asyncio.run(go()).ready
    assert "open_session" not in runner.log, \
        "a suite needing nothing standing was given a session container anyway"


def test_whether_a_runner_fails_on_nothing_is_measured_not_assumed():
    """The one question an exit code cannot answer, and the honest way to ask it.

    A suite that collects nothing exits zero, so "did anything run" is the
    question every verified criterion rests on. Most runners do exit non-zero
    on an empty selection -- and "most runners do" is exactly the belief that
    put a table of framework regexes in the orchestrator. So it is measured,
    once, against this project's own command.
    """
    import asyncio

    class Stub:
        carries_setup = True

        def __init__(self, code):
            self.code = code
            self.command = ""

        async def execute(self, command, *, cwd, timeout_s, network=True):
            self.command = command
            return gates.Execution(exit_code=self.code, output="")

    strict = Stub(5)
    detected, evidence = asyncio.run(
        gates.probe_empty_selection("pretend-runner {path}", ".", strict))
    assert detected is True and "exit 5" in evidence
    assert "{path}" not in strict.command, "the placeholder was never substituted"

    lax = Stub(0)
    detected, _ = asyncio.run(
        gates.probe_empty_selection("pretend-runner {path}", ".", lax))
    assert detected is False, "a runner that tolerates an empty selection was read as strict"

    # A probe that could not run is `None`, and must never be recorded as a
    # measurement in either direction.
    class Dead:
        carries_setup = True

        async def execute(self, command, *, cwd, timeout_s, network=True):
            return gates.Execution(exit_code=127, output="not found", started=False)

    detected, evidence = asyncio.run(gates.probe_empty_selection("x {path}", ".", Dead()))
    assert detected is None and "could not start" in evidence


def test_usable_is_measured_not_taken_on_trust():
    """The last claim on gate 0 that nothing checked.

    `usable` decides whether the oracle writes against the project's own setup
    or builds its own. Everything around it is measured -- the gates ran, the
    placements were probed, the package list came off the running environment --
    and this was a model's judgement taken on trust. A generous one costs the
    run every criterion at that level, and it arrives looking like a feature
    that failed, because a test that cannot reach its fixtures fails the same
    way wrong code does.

    So the reading supplies a test that uses the fixtures it just named, and it
    has to pass in a directory already proved to collect and configure files.
    """
    import asyncio, tempfile
    from pathlib import Path

    class Runner:
        """Passes only a canary that actually uses the named fixtures."""

        carries_setup = True

        def __init__(self):
            self.ran = []

        async def execute(self, command, *, cwd, timeout_s, network=True):
            self.ran.append(command)
            body = ""
            for f in Path(cwd).rglob("*tier_canary*"):
                body = f.read_text()
            ok = "USES-FIXTURES" in body
            return gates.Execution(exit_code=0 if ok else 1, output=body)

    with tempfile.TemporaryDirectory() as tmp:
        runner = Runner()
        places = [_placement(directory="tests/blind", filename="canary_test.py")]
        results = asyncio.run(gates.probe_testing(
            _tier_surface(_tier("unit", "usable"),
                     _tier("integration", "usable", canary="CANARY-INVENTS-ITS-OWN"),
                     _tier("user", "absent", canary="", filename="")),
            places, ["tests/blind"], [_rule("pretend-runner {path}")], tmp, runner))

        by = {r.tier: r for r in results}
        assert by["unit"].proved and by["unit"].passed
        assert not by["integration"].proved, (
            "a tier claiming reusable setup was believed even though a test written against that "
            "setup does not pass")
        assert "come back looking like a broken feature" in by["integration"].note
        # A tier claiming nothing is available has nothing to prove, and the
        # cost of it being wrong is conservative rather than catastrophic.
        assert by["user"].proved and by["user"].passed is None
        # Three: the unit canary passed and so ran a second time, to see whether
        # it cleans up after itself; the integration one failed and did not.
        assert len(runner.ran) == 3, "the tier claiming no setup was probed anyway"
        assert by["unit"].repeatable is True and by["integration"].repeatable is None

        # And nothing was left behind.
        import os
        assert not os.path.exists(os.path.join(tmp, "tests", "blind"))


def test_a_usable_tier_with_no_canary_is_not_proved():
    """Silence is not evidence. A reading that claims reusable setup and offers
    no test using it leaves the claim exactly where it was: unchecked."""
    import asyncio, tempfile

    with tempfile.TemporaryDirectory() as tmp:
        results = asyncio.run(gates.probe_testing(
            _tier_surface(_tier("unit", "usable", canary="", filename="")),
            [_placement(directory="tests/blind", filename="canary_test.py")],
            ["tests/blind"], [_rule("x {path}")], tmp, _PlacementRunner()))
        assert not results[0].proved and results[0].passed is None
        assert "supplied no test that does so" in results[0].note


def test_a_tier_is_only_proved_where_a_blind_test_would_actually_live():
    """The canary goes in a directory already proved to collect and configure a
    file, and one whose extension matches. Proving a tier somewhere a blind test
    can never be written proves nothing about that tier."""
    import asyncio, tempfile

    with tempfile.TemporaryDirectory() as tmp:
        runner = _PlacementRunner()
        # A .tsx tier, and the only proved directory takes .py.
        results = asyncio.run(gates.probe_testing(
            _tier_surface(_tier("user", "usable", filename="TierCanary.test.tsx")),
            [_placement(directory="tests/blind", filename="canary_test.py")],
            ["tests/blind"], [_rule("x {path}")], tmp, runner))
        assert not results[0].proved
        assert "no proved directory takes a `.tsx`" in results[0].note
        assert runner.commands == [], "a tier with nowhere to live should cost nothing to refuse"

        # And an unproved directory is not a home either.
        results = asyncio.run(gates.probe_testing(
            _tier_surface(_tier("unit", "usable")),
            [_placement(directory="tests/blind", filename="canary_test.py")],
            [], [_rule("x {path}")], tmp, runner))
        assert not results[0].proved


def test_placement_is_measured_not_assumed():
    """Where a blind test goes is proven at gate 0.

    This is the check that would have caught the failure that cost every run its
    verification: `tests/oracle` sat outside the tree that owns `pytest.ini`, so
    the blind suite ran with no ini, and every `async def` test failed with
    "async def functions are not natively supported" regardless of the feature.
    """
    import asyncio, tempfile

    with tempfile.TemporaryDirectory() as tmp:
        runner = _PlacementRunner(gate_codes={})
        result = asyncio.run(gates.probe_placement(
            _placement(), [_rule("pretend-runner {path}")], tmp, runner))

        assert result.usable
        assert (result.passing_ran, result.failing_seen) == (True, True)
        assert result.path == "tests/blind/canary_test.py"
        assert result.command == "pretend-runner tests/blind/canary_test.py"

        # Both canaries were written and run, in that order -- then both again
        # one directory deeper, which is the separate question of whether each
        # feature's acceptance tests can have their own folder.
        assert [body for _, body in runner.seen] == ["PASS", "FAIL", "PASS", "FAIL"]
        assert result.subdirs_ok is True

        # And nothing was left behind, at either depth. A canary that survived
        # would be collected by the next thing to look at this tree.
        import os
        assert not os.path.exists(os.path.join(tmp, "tests", "blind", "canary_test.py"))
        assert not os.path.exists(os.path.join(tmp, "tests", "blind", "_factory_probe"))
        assert not os.path.exists(os.path.join(tmp, "tests", "blind"))


def test_a_placement_where_subdirectories_do_not_work_is_recorded_as_flat():
    """Whether each feature gets its own folder is measured, never assumed.

    Blind tests accumulate: once a feature is accepted its acceptance tests stay
    in the repository and run as regressions forever after. Two features that
    both write `test_screening.py` would land on each other, so each needs its
    own folder -- and whether a runner treats a nested file the same way is a
    layout rule, not something this tool may decide. Under some pytest layouts
    two same-named files in sibling directories are an import-mismatch error;
    under others they are fine. So it is run, not reasoned about.
    """
    import asyncio, tempfile
    from pathlib import Path

    class FlatOnly:
        """Collects a file in the directory and chokes on one a level deeper."""

        carries_setup = True

        async def execute(self, command, *, cwd, timeout_s, network=True):
            if command.startswith("gate:"):
                return gates.Execution(exit_code=0, output="")
            deep = list(Path(cwd).rglob("_factory_probe/*"))
            if deep:
                return gates.Execution(exit_code=2, output="import file mismatch")
            body = ""
            for f in Path(cwd).rglob("*canary*"):
                if "_factory_probe" not in str(f):
                    body = f.read_text()
            return gates.Execution(exit_code=0 if "PASS" in body else 1, output=body)

    with tempfile.TemporaryDirectory() as tmp:
        result = asyncio.run(gates.probe_placement(
            _placement(), [_rule("pretend-runner {path}")], tmp, FlatOnly()))

        assert result.usable, "the flat placement itself is fine and must stay usable"
        assert result.subdirs_ok is False, (
            "a runner that cannot collect a nested file was recorded as able to, so two "
            "features' acceptance tests would be written into folders nothing runs")


def test_how_this_runner_names_a_test_is_measured_not_assumed():
    """The third thing repo ready proves about a placement.

    A criterion is attributed by joining the name the oracle declared to the
    name the runner's report carries. "Most runners report the test's own name"
    is true and is the same species of belief `empty_run_detected` exists to
    stop anyone acting on -- so it is measured here, against this project's own
    reporting command, on a canary whose name is known.

    card-tags-8e21a7 is why: fourteen criteria, twenty-three blind tests, every
    one run and passed, and nine criteria reached a human as `unknown` because
    one `describe` title sat in front of every name in the report.
    """
    import asyncio, tempfile

    # A runner that qualifies with a separator: the join follows it there.
    with tempfile.TemporaryDirectory() as tmp:
        runner = _ReportingRunner(["blind canary > it passes"])
        result = asyncio.run(gates.probe_placement(
            _placement(canary_case="it passes"),
            [_rule("run {path}", report="run --junit={report} {path}")], tmp, runner))
        assert result.usable
        assert result.case_named_as_declared is True
        assert result.reported_case == "blind canary > it passes"

    # And one that qualifies with a space, which is jest-junit's default pair
    # of templates. Nothing can split that, and the packet must not pretend
    # otherwise: this is recorded, said in words, and left for the human.
    with tempfile.TemporaryDirectory() as tmp:
        runner = _ReportingRunner(["blind canary it passes"])
        result = asyncio.run(gates.probe_placement(
            _placement(canary_case="it passes"),
            [_rule("run {path}", report="run --junit={report} {path}")], tmp, runner))
        assert result.case_named_as_declared is False
        assert "'blind canary it passes'" in result.note
        assert "would reach the packet as `unknown`" in result.note
        assert result.usable, (
            "a runner whose report cannot be joined still collects and reports honestly. "
            "Per-test attribution is additive: losing it costs resolution, not correctness, "
            "and refusing the placement over it would block a project that works."
        )


def test_a_reporting_command_that_writes_no_report_is_said_out_loud():
    """The `../{report}` trap, caught at repo ready instead of never.

    A missing report is by design the signal to fall back to the file's exit
    code, so a rule that names a directory the runner cannot write to degrades
    every criterion in every file to one shared verdict and nothing anywhere
    says so. One run reported eighteen of twenty-five criteria as failing off
    four files, fifteen of them from a single one.
    """
    import asyncio, tempfile

    with tempfile.TemporaryDirectory() as tmp:
        runner = _ReportingRunner(["it passes"], writes_report=False)
        result = asyncio.run(gates.probe_placement(
            _placement(canary_case="it passes"),
            [_rule("run {path}", report="run --junit={report} {path}")], tmp, runner))
        assert result.usable
        assert result.case_named_as_declared is None, "unmeasured is never a match"
        assert result.reported_case == ""
        assert "{report}" in result.note and "$PWD" in result.note


def test_the_naming_measurement_costs_nothing_where_it_cannot_be_taken():
    """A project with no reporting command, or a canary whose name the survey
    could not give, pays for neither -- and is left unmeasured rather than
    recorded as agreeing."""
    import asyncio, tempfile

    with tempfile.TemporaryDirectory() as tmp:
        runner = _ReportingRunner(["it passes"])
        result = asyncio.run(gates.probe_placement(
            _placement(canary_case="it passes"), [_rule("run {path}")], tmp, runner))
        assert result.case_named_as_declared is None
        assert len(runner.commands) == 4, "no report command, so no third run"
        assert "{report}" not in result.note

    with tempfile.TemporaryDirectory() as tmp:
        runner = _ReportingRunner(["it passes"])
        result = asyncio.run(gates.probe_placement(
            _placement(), [_rule("run {path}", report="run --junit={report} {path}")],
            tmp, runner))
        assert result.case_named_as_declared is None
        assert len(runner.commands) == 4, "nothing to compare against, so no third run"


def test_a_rule_with_no_reporting_command_is_said_at_repo_ready():
    """It used to be skipped in silence. The naming measurement needs a
    reporting command, so a rule without one left nothing on the page -- and the
    first anyone heard of it was a packet with seven criteria `unknown` over
    tests that had all run."""
    import asyncio, tempfile

    with tempfile.TemporaryDirectory() as tmp:
        result = asyncio.run(gates.probe_placement(
            _placement(canary_case="it passes"), [_rule("run {path}")], tmp,
            _ReportingRunner(["it passes"])))
        assert result.usable
        assert "no reporting command" in result.note and "Resurvey" in result.note


def test_placement_that_the_projects_config_does_not_reach_is_refused():
    """The clinic failure, in one assertion.

    A passing test that is not reported as passing means the configuration this
    project's tests rely on does not reach this directory. Nothing about the
    feature is knowable through such a placement, and every criterion under it
    would come back failed for a reason no code caused.
    """
    import asyncio, tempfile

    with tempfile.TemporaryDirectory() as tmp:
        runner = _PlacementRunner(on_passing=1)      # the canary that must pass, fails
        result = asyncio.run(gates.probe_placement(
            _placement(), [_rule("pretend-runner {path}")], tmp, runner))

        assert not result.usable and result.passing_ran is False
        assert "not reported as passing" in result.note
        assert "configuration does not reach it" in result.note


def test_placement_that_exits_zero_over_a_file_it_never_collected_is_refused():
    """The dangerous one: a green exit code over nothing.

    vitest's `include` is relative to its own root, so a file outside it is not
    failing but invisible -- and a runner asked for nothing can exit zero. A
    placement like that reports every criterion as verified by tests that never
    ran, which is worse than any failure, and it is invisible to every check
    except this one.
    """
    import asyncio, tempfile

    with tempfile.TemporaryDirectory() as tmp:
        runner = _PlacementRunner(on_failing=0)      # the canary that must fail, passes
        result = asyncio.run(gates.probe_placement(
            _placement(), [_rule("pretend-runner {path}")], tmp, runner))

        assert not result.usable
        assert result.passing_ran is True, "the passing canary was fine; only the failing one lied"
        assert result.failing_seen is False
        assert "never ran" in result.note


def test_placement_no_command_claims_is_refused_before_anything_runs():
    """A directory the per-file rules do not match is a directory with no runner."""
    import asyncio, tempfile

    with tempfile.TemporaryDirectory() as tmp:
        runner = _PlacementRunner()
        result = asyncio.run(gates.probe_placement(
            _placement(directory="frontend/blind", filename="Canary.test.tsx"),
            [_rule("pretend-runner {path}", match="backend/*")], tmp, runner))

        assert not result.usable and result.passing_ran is None
        assert "no test-file command claims" in result.note
        assert runner.commands == [], "a placement with no runner should cost nothing to refuse"


def test_placement_outside_the_repository_is_refused():
    """A directory is a path a model wrote."""
    import asyncio, tempfile

    with tempfile.TemporaryDirectory() as tmp:
        result = asyncio.run(gates.probe_placement(
            _placement(directory="../../etc"), [_rule("x {path}")], tmp, _PlacementRunner()))
        assert not result.usable and "outside the repository" in result.note


def test_an_optional_gate_that_could_not_run_does_not_block():
    """A human already said they know about it."""
    report = gates.GateReport(results=[
        _result("e2e", exit_code=127, skipped=True, output_tail="command not found"),
    ])
    assert gates.unrunnable(report) == []


def test_a_ceiling_makes_a_failing_count_green_and_a_regression_red(tmp_path):
    """The mechanism the green rule rests on, measured rather than asserted.

    `threshold` is a floor and reads a number that must not shrink. Every
    pre-existing debt a repository carries is the other shape -- a count that
    must not grow -- and without a ceiling the only ways out of a red check were
    to fix it or drop it. That is what made requiring green unaffordable.
    """
    import asyncio

    from factory.config import GateConfig

    def run(ceiling):
        gate = GateConfig(
            name="lint", command='echo "Found 129 errors."; exit 1',
            parse_metric=r"Found (\d+) errors", threshold_max=ceiling)
        return asyncio.run(gates.run_gate(gate, tmp_path))

    at_the_bar = run(129)
    assert at_the_bar.metric == 129.0
    assert gates.is_green(at_the_bar), "a count sitting on its ceiling is green"

    regressed = run(128)
    assert not gates.is_green(regressed), "one more than the ceiling is red"

    # And the exit code no longer decides it: the command failed in both runs.
    assert at_the_bar.exit_code == 1


def test_an_unread_metric_with_no_bound_falls_back_to_the_exit_code(tmp_path):
    """The note said "falling back to the exit code" and nothing fell back.

    An error-count pattern only matches when the tool found errors, so a clean
    lint or typecheck is precisely the run where it reads nothing. With no
    threshold riding on that number the command's own exit code is the whole
    answer -- but `passed` was left at its default of False, so four commands
    that exited zero and printed "All checks passed!" and "Success: no issues
    found" came back red on a freshly onboarded project, and the onboarding
    page asked the human to fix a repository that had nothing wrong with it.
    """
    import asyncio

    from factory.config import GateConfig

    def run(command):
        gate = GateConfig(
            name="lint", command=command, parse_metric=r"Found (\d+) error")
        return asyncio.run(gates.run_gate(gate, tmp_path))

    clean = run('echo "All checks passed!"; exit 0')
    assert clean.metric is None, "nothing in that output is a number to read"
    assert gates.is_green(clean), \
        "a command that exited zero is green when no bound needed the metric"
    assert "falling back to the exit code" in clean.output_tail

    # And the fallback is the exit code, not a blanket pass.
    broken = run('echo "something went wrong"; exit 1')
    assert broken.metric is None
    assert not gates.is_green(broken), "exit 1 is still red"


def test_setup_installs_survive_into_the_gates():
    """`pip install` writes into the container's filesystem, not into the
    mounted worktree. With a throwaway container per command it went out with
    the container that installed it -- a twenty-second install that succeeded,
    then `ruff: not found` on the very next line. `npm ci` survived only
    because `node_modules` lives in the mount, which is why this hid for so
    long behind a green frontend gate."""

    class FakeRunner:
        """Filesystem state that persists only inside a session."""

        def __init__(self) -> None:
            self.installed: set[str] = set()
            self.session: set[str] | None = None
            self.committed: set[str] = set()

        async def open_session(self, cwd):
            self.session = set(self.installed)

        async def close_session(self):
            self.committed = set(self.session or ())
            self.installed = set(self.committed)
            self.session = None

        async def execute(self, command, *, cwd, timeout_s, network=True):
            # A fresh container per command unless a session is open.
            where = self.session if self.session is not None else set(self.installed)
            if command.startswith("install "):
                where.add(command.split()[1])
                return gates.Execution(exit_code=0, output="ok")
            tool = command.split()[0]
            if tool in where:
                return gates.Execution(exit_code=0, output="ran")
            return gates.Execution(exit_code=127, output=f"sh: 1: {tool}: not found")

    runner = FakeRunner()
    setup = asyncio.run(gates.run_setup(["install ruff", "install mypy"], Path("."), runner))
    assert all(r.passed for r in setup), "setup itself has to work"

    # And the gates, in their own containers, still find what setup installed.
    report = asyncio.run(gates.run_gates(
        [Gate(name="lint", command="ruff check ."), Gate(name="types", command="mypy")],
        Path("."), runner))
    assert all(r.passed for r in report.results), \
        f"the gates cannot see what setup installed: {[r.output_tail for r in report.results]}"


def test_a_runner_without_sessions_still_works():
    """LocalRunner has no filesystem to commit and offers neither hook."""
    assert not hasattr(gates.LocalRunner, "open_session")
    results = asyncio.run(gates.run_setup(["true"], Path("."), gates.LocalRunner()))
    assert results and results[0].passed


def test_a_session_that_could_not_open_is_reported_once():
    """Otherwise it surfaces as eight separate 'not found' failures, none of
    which names the cause."""

    class Broken:
        _session_error = "Bind for 0.0.0.0:5433 failed: port is already allocated"
        async def open_session(self, cwd): pass
        async def close_session(self): pass
        async def execute(self, command, *, cwd, timeout_s, network=True):
            return gates.Execution(exit_code=0, output="ok")

    results = asyncio.run(gates.run_setup(["install ruff"], Path("."), Broken()))
    first = results[0]
    assert first.name == "setup[session]" and not first.passed
    assert "will not survive to the gates" in first.output_tail
    assert "port is already allocated" in first.output_tail


def test_a_stack_that_did_not_start_is_one_failure_with_its_own_words(tmp_path, monkeypatch):
    """A full Docker disk stopped a database as it started, and the run said
    "3 setup command(s) failed: `hold one container open`; `mvnw verify`;
    `npm ci`" -- two of them never really ran, and none of it said why. The
    stack is now one failure, its cause said first, and the stopped service's
    own log goes with it."""
    from types import SimpleNamespace
    from factory import containers
    from factory.schemas import EnvironmentSpec

    class Stalled:
        up_problem = "The project's `db` service stopped as it started (exit 1)."
        _session_error = up_problem + "\n\ncompose output"
        ran: list[str] = []
        async def open_session(self, cwd): pass
        async def close_session(self): pass
        async def execute(self, command, *, cwd, timeout_s, network=True):
            self.ran.append(command)
            return gates.Execution(exit_code=1, output="dependency failed to start")

    runner = Stalled()
    results = asyncio.run(gates.run_setup(["mvnw verify", "npm ci"], Path("."), runner))
    assert [r.name for r in results] == ["setup[session]"]
    assert runner.ran == [], "nothing is run into a stack that is not there"

    # And the unit's environment says the one thing.
    async def fake_runner_for(project, config, feature_id=""):
        return runner, None
    monkeypatch.setattr(unitenv, "runner_for", fake_runner_for)
    project = SimpleNamespace(environment=EnvironmentSpec(kind="derive", setup=["mvnw verify"]),
                              warm_gates=[])

    async def open_it():
        async with unitenv.unit_environment(project, Config(), label="x", tree=tmp_path) as env:
            return env
    env = asyncio.run(open_it())
    assert not env.ready
    assert env.problem.startswith("the project's environment did not start")
    assert "`db` service stopped" in env.problem
    assert "mvnw" not in env.problem, "a command that never ran is not named as failing"

    # What compose prints, turned into the project's terms, and the log read
    # before teardown takes it.
    compose = containers.ComposeRunner.__new__(containers.ComposeRunner)
    compose._compose = lambda: {"services": {"api": {}, "db": {}}}
    compose.spec = EnvironmentSpec(kind="compose", compose_service="api")
    said = compose._stack_headline(
        " Container library-u-1-db-1 Error dependency db failed to start\n"
        "dependency failed to start: container fabrika-library-u-1-db-1 exited (1)\n")
    assert said.startswith("The project's `db` service stopped as it started (exit 1)")

    calls = []

    async def fake_run(argv, timeout=60.0, cwd=None):
        calls.append(argv[-3:])
        if "ps" in argv:
            return gates.Execution(exit_code=0, output=(
                '{"Service":"db","State":"exited","Health":""}\n'
                '{"Service":"api","State":"running","Health":""}\n'))
        return gates.Execution(exit_code=0, output="[ERROR] InnoDB: Error number 28: No space left")
    monkeypatch.setattr(containers, "_run", fake_run)
    logs = asyncio.run(compose._stopped_services_said(["docker", "compose"]))
    assert "what `db` said" in logs and "No space left" in logs
    assert "api" not in logs, "a running service's log is not evidence of anything"


def test_only_the_oracles_asserting_files_are_run_one_at_a_time():
    """Support files are loaded by the runner, never invoked as tests.

    They used to be handed to the gates as witnesses so a conftest named in a
    failing line could be attributed. Nothing searches output now, so that
    reason is gone -- and running a README or a conftest as though it were a
    test would fail for reasons that have nothing to do with the feature.

    The arrangement is also strictly better at the thing the old one was for: a
    conftest that will not import fails every test file's own command, so every
    criterion it touches goes red, instead of the suite going red as one number
    nobody can attribute.
    """
    src = inspect.getsource(pipeline.Factory._converge)
    assert "witness=[t.path for t in oracle.tests]" in src, \
        "the blind suite is not handed its asserting files to run one at a time"
    assert "witness=[t.path for t in oracle_files(oracle)]" not in src, \
        "support files are being handed over to be invoked as tests"
    # And they are still protected, which is a different list for a different
    # reason: a repairer that could edit a conftest could make any blind test
    # pass without fixing anything. (INV-12)
    assert "extra=[t.path for t in oracle_files(oracle)]" in src, \
        "the oracle's support files fell out of the protected set"

    # And the gates themselves no longer take a witness list at all.
    for fn in (gates.run_gate, gates.run_gates):
        assert "witness" not in inspect.signature(fn).parameters, (
            f"{fn.__name__} still accepts a witness list, which only made sense "
            "when a gate searched its own output for test names"
        )


def test_shims_map_the_directory_the_agent_is_standing_in():
    """`cd backend && pytest` has to land in `<workdir>/backend`.

    Read with `pwd -P`, never `$PWD`: that is an ordinary inherited variable
    saying where the *parent* stood, so every command after a `cd` ran at the
    tree root while looking like it had worked.
    """
    import subprocess
    tree = Path(tempfile.mkdtemp(prefix="shimtree-"))
    (tree / "backend" / "deep").mkdir(parents=True)
    shims = Path(tempfile.mkdtemp(prefix="shimbin-"))
    written = unitenv.write_shims(
        shims, names=["pytest"], docker="/bin/echo", container="c1",
        tree=tree, workdir="/workspace")
    assert written == ["pytest"]
    assert (shims / "factory-exec").exists(), "no escape hatch for unnamed commands"
    env = {**os.environ, "PATH": f"{shims}{os.pathsep}{os.environ['PATH']}"}
    seen = {}
    for where in (tree, tree / "backend", tree / "backend" / "deep"):
        out = subprocess.run(["pytest", "-q"], cwd=where, env=env,
                             capture_output=True, text=True).stdout
        seen[where.name] = out.split(" -w ")[1].split()[0]
    assert seen[tree.name] == "/workspace"
    assert seen["backend"] == "/workspace/backend"
    assert seen["deep"] == "/workspace/backend/deep"


def test_a_sarif_report_is_read_by_line_and_a_missing_one_is_not_a_clean_result(tmp_path):
    report = tmp_path / "r.sarif"
    report.write_text(_sarif(("app.py", 3, "C901"), (f"file://{tmp_path}/pkg/m.py", 7, "B101"),
                             ("/work/pkg/n.py", 2, "S1")))
    found, status = gates.read_sarif(report, roots=[str(tmp_path), "/work"])
    assert status == "read"
    assert [(f.path, f.start_line, f.rule) for f in found] == [
        ("app.py", 3, "C901"), ("pkg/m.py", 7, "B101"), ("pkg/n.py", 2, "S1")], \
        "a path named from either side of the mount was not made repo-relative"

    assert gates.read_sarif(tmp_path / "absent.sarif") == ([], "missing")
    (tmp_path / "bad.sarif").write_text("not json")
    assert gates.read_sarif(tmp_path / "bad.sarif") == ([], "unreadable")


# -- coverage: the whole project's, and a feature's own lines -------------------


def test_coverage_is_read_by_line_from_each_of_the_three_formats(tmp_path):
    cobertura = tmp_path / "c.xml"
    cobertura.write_text(
        f'<coverage><sources><source>{tmp_path}/src</source></sources><packages><package>'
        '<classes><class filename="app.py"><lines><line number="1" hits="1"/>'
        '<line number="2" hits="0"/></lines></class></classes></package></packages></coverage>')
    lcov = tmp_path / "l.info"
    lcov.write_text("SF:web/a.js\nDA:1,3\nDA:2,0\nDA:3,1\nend_of_record\n")
    cjson = tmp_path / "c.json"
    cjson.write_text(json.dumps({"files": {"pkg/m.py": {"executed_lines": [1, 4],
                                                        "missing_lines": [5]}}}))

    data, status = gates.read_coverage(cobertura, "cobertura", roots=[str(tmp_path)])
    assert status == "read" and data == {"src/app.py": [[1, 2], [1]]}, \
        "a Cobertura path was not joined to its source and made repo-relative"
    data, _ = gates.read_coverage(lcov, "lcov")
    assert data == {"web/a.js": [[1, 2, 3], [1, 3]]}
    data, _ = gates.read_coverage(cjson, "coverage-json")
    assert data == {"pkg/m.py": [[1, 4, 5], [1, 4]]}
    assert gates.coverage_total(data) == 66.67
    assert gates.coverage_total({}) is None, "a report with nothing runnable read as 0% or 100%"
    assert gates.read_coverage(tmp_path / "none.xml", "cobertura") == ({}, "missing")


def test_a_mutation_report_names_each_change_no_test_caught(tmp_path):
    report = tmp_path / "m.json"
    report.write_text(json.dumps({"schemaVersion": "1", "files": {"src/a.js": {
        "source": "if (a > b) {\n  return 1;\n}\n",
        "mutants": [
            {"mutatorName": "EqualityOperator", "replacement": "a >= b", "status": "Survived",
             "location": {"start": {"line": 1, "column": 5}, "end": {"line": 1, "column": 10}}},
            {"mutatorName": "BlockStatement", "replacement": "{}", "status": "Killed",
             "location": {"start": {"line": 1, "column": 12}, "end": {"line": 3, "column": 2}}},
        ]}}}))
    survived, score, status = gates.read_mutation(report)
    assert status == "read" and score == 50.0
    assert [(m.path, m.start_line, m.message) for m in survived] == [
        ("src/a.js", 1, "changing `a > b` to `a >= b` failed no test")]


def test_a_test_report_is_named_on_the_side_that_writes_it():
    """`{report}` is substituted into a command the runner executes, and read
    back by the orchestrator. In a container those are two different paths for
    one file, and naming the wrong one loses the report without an error.

    Seen for real on a compose project with the checkout at `/workspace`: every
    per-file rule had a `report` command, every command ran with `--junitxml=`,
    and zero cases came back. pytest wrote to a host path that does not exist
    inside the container; `read_junit` read the host path and found nothing;
    "a report that is missing returns nothing at all" is by design the signal to
    fall back to the file's exit code. So the whole project silently dropped to
    file-granularity attribution, and one run reported eighteen of twenty-five
    criteria as failing off four files -- fifteen of them off a single file.

    `Gate.report` exists to turn "the file failed" into "this test failed". A
    mount boundary turned it back.
    """
    src = inspect.getsource(gates.run_per_file)
    assert "workdir_of(runner)" in src, \
        "the report is named at the orchestrator's path, not the runner's"

    # And absolute on whichever side, because these rules `cd` before they run.
    class Contained:
        workdir = "/workspace"

    stem = ".factory-report-deadbeef.xml"
    host = Path("/host/sandbox") / stem
    for runner, expected in ((None, str(host)),
                             (gates.LocalRunner(), str(host)),
                             (Contained(), "/workspace/" + stem)):
        mounted = gates.workdir_of(runner)
        named = host if mounted in ("", ".") else PurePosixPath(mounted) / stem
        assert str(named) == expected, f"{type(runner).__name__} is told {named}"
        assert str(named).startswith("/"), \
            "a relative report lands wherever the rule's `cd` left it"


def test_an_authoring_agent_gets_a_container_even_with_no_services_declared():
    """The container is the environment, not machinery a service needs.

    `test_session` opened one when the project declared `services` or a
    disposable database, because those are the cases where two commands have to
    reach each other on 127.0.0.1. An authoring agent wants one for a different
    reason: `unit_environment` writes shims onto its PATH that `docker exec`
    into it, so with no container there are no shims, and the agent has none of
    the project's tools at all.

    A compose project whose own compose file brings the database up declares no
    services. Every authoring agent on such a project was handed an environment
    saying "this runner could not hold a container open" -- true, and nobody's
    intent. Measured on a real one: an agent with a worktree, a stack reported
    ready, and shims on its PATH could not run a single one of the project's
    own commands, and nothing in the run said why.
    """
    import inspect
    from factory import gates, unitenv

    src = inspect.getsource(gates.test_session)
    assert "hold: bool = False" in src, "there is no way to ask for a container on its own"
    assert "needs_container = hold or bool(services)" in src, \
        "asking for one is not what decides whether one is opened"

    # And the one caller whose whole purpose is a place to run commands asks.
    env_src = inspect.getsource(unitenv.unit_environment)
    session = env_src.split("test_session(", 1)[1].split(") as prepared", 1)[0]
    assert "hold=True" in session, \
        "the environment built for an agent still only gets a container when a service wants one"


def test_the_disk_is_measured_before_anything_is_stood_up():
    """A full disk arrives as whatever first needs space -- once as an
    `asyncpg.DiskFullError` inside a TRUNCATE, forty minutes in, reading as a
    broken database. Measured up front it is one sentence with a number in it.
    """
    runner = _SessionRunner(df_output=DF_HEADER + "/dev/disk1 900000000 899000000 51200 99% /")

    async def go():
        async with gates.test_session(
                ".", runner, services=[_service()], prepare=["PREPARE migrate"]) as session:
            return session

    session = asyncio.run(go())
    assert not session.ready, "a disk this full cannot be the ground for a verdict"
    assert "free" in session.problem and "0.0 GB" in session.problem
    assert "PREPARE migrate" not in " ".join(runner.log), (
        "nothing may be prepared on a disk that cannot hold the result")
    assert not runner.services, "and no service stood up to write into it"

    # Room to work: the session proceeds exactly as it did before the probe.
    fine = _SessionRunner(df_output=DF_HEADER + "/dev/disk1 900000000 400000000 500000000 45% /")

    async def ok():
        async with gates.test_session(
                ".", fine, services=[_service()], prepare=["PREPARE migrate"]) as session:
            return session

    assert asyncio.run(ok()).ready
    assert "exec:PREPARE migrate" in fine.log


def test_a_df_that_cannot_be_read_never_stops_a_run():
    """A probe is allowed to say nothing. It is not allowed to be the reason a
    run did not happen -- an unfamiliar `df`, a busybox shell, a runner that
    refuses the command, all mean "unknown", and unknown proceeds."""

    async def free(output, exit_code=0):
        class R:
            async def execute(self, command, *, cwd, timeout_s, network=True):
                return gates.Execution(exit_code=exit_code, output=output)
        return await gates._free_space_mb(R(), ".", 60.0)

    assert asyncio.run(free("")) is None
    assert asyncio.run(free(DF_HEADER)) is None
    assert asyncio.run(free(DF_HEADER + "garbage")) is None
    assert asyncio.run(free(DF_HEADER + "/dev/d 1 2 not-a-number 9% /")) is None
    assert asyncio.run(free(DF_HEADER + "/dev/d 900 400 500 45% /", exit_code=127)) is None


def test_the_sweep_reclaims_superseded_harness_layers_and_keeps_the_live_one(monkeypatch):
    """The largest images this factory makes: a project image plus a coding
    agent runs to a gigabyte and a half, and every edit to an environment or a
    pinned requirement mints another. The disk filled once already, during a
    run, which is the most expensive moment to find out."""
    import asyncio
    from factory import containers

    live = "fabrika-harness/claude-code:aaaaaaaaaaaa"
    dead = "fabrika-harness/claude-code:bbbbbbbbbbbb"
    listing = "\n".join([
        "fabrika/demo:current", "fabrika/demo:superseded", live, dead,
        "python:3.12-slim",
    ])
    removed: list[str] = []

    async def fake_run(argv, timeout=60.0, cwd=None):
        from factory.gates import Execution
        if argv[1] == "images":
            return Execution(exit_code=0, output=listing)
        if argv[1] == "rmi":
            removed.append(argv[2])
            return Execution(exit_code=0, output="")
        return Execution(exit_code=1, output="")

    monkeypatch.setattr(containers, "_run", fake_run)
    dropped = asyncio.run(containers.sweep_images(
        {"demo": "fabrika/demo:current"}, _docker_config(), [live]))

    assert dead in dropped, "a harness layer nothing can reach any more is garbage"
    assert live not in dropped, "the layer the next unit will run in stays"
    assert "fabrika/demo:superseded" in dropped
    assert "python:3.12-slim" not in removed, "only tags this factory minted"


def test_an_agent_inside_the_container_is_given_no_shims_and_told_so():
    """Shims exist to carry a command from the host into the container. An
    agent that runs inside it needs none of them -- and the paragraph that
    tells it to use them is worse than silence: it teaches the agent to reach
    for a wrapper that is not on its PATH, and an agent that cannot find the
    tool it was told to use concludes it has no tools."""
    import inspect
    from factory import unitenv

    source = inspect.getsource(unitenv.unit_environment)
    assert "[] if in_container else write_shims(" in source, \
        "an in-container session still gets a PATH full of wrappers, each of which " \
        "would launch a second container from inside this one"
    assert "_inside_note() if in_container else _note(" in source

    inside = unitenv._inside_note()
    assert "You are inside this unit's environment" in inside
    for wrong in ("factory-exec", "rather than on this host", "These run there"):
        assert wrong not in inside, f"the in-container brief still says {wrong!r}"
    # What must survive: the reason the environment exists at all.
    assert "a unit that reports work it never ran" in inside

    # The host brief keeps the shims and the hatch, because a host session
    # still needs both.
    host = unitenv._note(True, ["pytest"], "", "")
    assert "factory-exec" in host


def test_the_breaker_is_asked_what_correct_code_does_and_may_say_it_cannot():
    """The classification is only worth having because stating the pass
    condition is the work, and the escape hatch is only safe because it is
    named. Without "I cannot say" the label becomes a guess, and a guess is
    what the promotion check exists to survive rather than what it should be
    fed.

    The brief also has to carry the trap concretely. The probes that prompted
    this pinned a schedule only the defect permits -- two requests held at a
    barrier until both reached commit, which a serialising fix makes
    unreachable -- and one treated the correct rejection of a 21st tag as an
    unhandled error. A brief that says "write regression probes" and stops
    would not have prevented either.
    """
    brief = (ROOT / "factory" / "roles" / "breaker.md").read_text(encoding="utf-8")
    assert "## Say what each probe is for" in brief
    assert '"I cannot say" is a real answer' in brief, "the escape hatch has to be offered"
    assert "passes_when" in brief
    # The three ways a probe comes out unsatisfiable, each named.
    for trap in ("pins a schedule only the defect permits",
                 "treats correct rejection as an error",
                 "rewards a different defect"):
        assert trap in brief.lower(), trap
    # And the reason a demonstration is not a lesser thing to file.
    assert "worth less for being mislabelled" in brief

    # The account is where the answer is actually collected, so the question
    # has to be in the asking too -- a brief nobody is examined on is a brief.
    asked = inspect.getsource(pipeline.Factory._author_probes)
    assert "passes_when" in asked
    assert "read as a demonstration" in asked


def test_a_per_file_run_says_which_file_was_killed_and_what_each_one_cost():
    """`timed_out` on the result is the whole command's, which for a run that
    executes one command per file answers a question nobody asked.

    The caller needs *which* file never finished, because a killed file and a
    failing file both exit non-zero and mean opposite things -- and it needs
    what each file cost, because that is the only honest thing to measure the
    next run against. Both were already known here and thrown away.
    """
    class _Slow:
        def __init__(self):
            self.timeouts = []

        async def execute(self, command, *, cwd, timeout_s, network=True):
            self.timeouts.append((command.split()[-1], timeout_s))
            if "stuck" in command:
                return gates.Execution(exit_code=-9, timed_out=True, started=True,
                                       output=f"timed out after {timeout_s}s")
            return gates.Execution(exit_code=0, output="ok", started=True)

    rules = [schemas.TestFileCommand(match="*", command="run {path}")]
    runner = _Slow()
    result, exits = asyncio.run(gates.run_per_file(
        rules, ["fine.py", "stuck.py"], "/tmp", runner,
        timeout_s=900.0, timeouts={"stuck.py": 60.0}))

    assert result.timed_out_files == ["stuck.py"]
    assert exits["stuck.py"] == -9
    # Kept apart from the exit code, which cannot tell the two apart.
    assert "fine.py" not in result.timed_out_files
    assert set(result.file_seconds) == {"fine.py", "stuck.py"}

    # A file may be given its own budget; anything unnamed keeps the ceiling.
    assert dict(runner.timeouts) == {"fine.py": 900.0, "stuck.py": 60.0}


def test_agents_that_write_tests_are_told_where_the_services_are():
    """The variables were in every authoring agent's environment and nothing
    said so. A worker seeding a board from a browser fixture used the project's
    own `E2E_API_BASE` with a `localhost:8300` default, which is nothing at all
    in the checks."""
    said = unitenv._services_note({"api": 51234, "web-ui": 51235})
    assert "`FACTORY_URL_API`" in said and "`FACTORY_URL_WEB_UI`" in said
    assert "different ports" in said, "an agent is not told the address it sees will change"
    assert unitenv._services_note({}) == ""
    assert "+ _services_note(prepared.ports)" in inspect.getsource(unitenv.unit_environment)


def test_a_stray_report_is_swept_after_the_run_wherever_it_landed(tmp_path):
    repo = _repo_with_one_commit(tmp_path / "repo", {"a.py": "x = 1\n", "b.xml": "<kept/>"})
    (repo / "workspace").mkdir()
    (repo / "workspace/.factory-report-0ccfb34c.xml").write_text("<testsuites/>")
    (repo / "notes.xml").write_text("<mine/>")

    removed = gates.sweep_reports(repo)

    assert removed == ["workspace/.factory-report-0ccfb34c.xml"]
    assert (repo / "notes.xml").exists() and (repo / "b.xml").exists(), \
        "only what the factory named, and nothing tracked"
    src = inspect.getsource(gates.run_per_file)
    assert "sweep_reports(cwd)" in src


def test_gate_zero_measures_whether_a_tiers_tests_clean_up():
    """By outcome, never by mechanism. The canary runs twice in a row; at a level
    whose tests share state, the project's own checks for that level run again
    after it. A canary that leaves a row behind fails its own second run, and
    one that leaves a list on the demo board fails the project's board test."""
    import tempfile
    from factory.schemas import Gate

    def probe(canary_cleans: bool):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "db.txt"
            state.write_text("")

            class Runner:
                carries_setup = True

                async def execute(self, command, *, cwd, timeout_s, network=True):
                    if command == "project-suite":
                        return gates.Execution(exit_code=1 if state.read_text() else 0, output="")
                    dirty = bool(state.read_text())
                    if not canary_cleans:
                        state.write_text(state.read_text() + "row\n")
                    return gates.Execution(exit_code=1 if dirty else 0, output="unique row taken")

            tier = _tier("integration", "usable")
            tier.run_by = ["suite"]
            [result] = asyncio.run(gates.probe_testing(
                _tier_surface(tier), [_placement(directory="tests/blind", filename="canary_test.py")],
                ["tests/blind"], [_rule("pretend-runner {path}")], tmp, Runner(),
                green=[Gate(name="suite", command="project-suite")]))
            return result

    clean = probe(canary_cleans=True)
    assert clean.proved and clean.repeatable is True and clean.leaves_clean is True
    dirty = probe(canary_cleans=False)
    assert dirty.proved, "passing once still proves the fixtures can be used"
    assert dirty.repeatable is False and dirty.leaves_clean is False
    assert "leaves something behind" in dirty.note and "breaks the tests already here" in dirty.note


def test_setup_runs_a_warmed_check_once_online_and_its_outcome_is_not_setup_s():
    """A warm-up is the check itself, run with the network after setup's own
    commands, inside the same session so what it fetched is committed with the
    rest. Its outcome is not a setup failure -- it may well fail on the way --
    and the stack is reset afterwards, so the checks do not start on what it
    wrote."""
    from factory.gates import run_setup
    from factory.schemas import Gate

    runner = _LazyFetchRunner()
    results = asyncio.run(run_setup(["npm ci"], ".", runner,
                                    warm=[Gate(name="api-verify", command="mvn verify")]))
    assert runner.log == ["open_session", "online:npm ci", "online:mvn verify",
                          "close_session", "reset_stack"]
    assert [r.name for r in results] == ["setup[0]", "setup[warm:api-verify]"]
    runner = _LazyFetchRunner(fetch_fixes=False)
    runner.rows = 5
    failing = asyncio.run(run_setup([], ".", runner, warm=[Gate(name="api-verify",
                                                                command="mvn verify")]))
    assert failing[0].skipped and not failing[0].passed, \
        "a warm-up that fails must not read as setup failing"
    assert "reset_stack" not in _LazyFetchRunner().log
    plain = _LazyFetchRunner()
    asyncio.run(run_setup(["npm ci"], ".", plain))
    assert "reset_stack" not in plain.log, "no warm-up, no reason to throw the stack away"


def test_the_doorway_carries_bytes_inward_and_nothing_else():
    """The forwarder both hops run: a connection in reaches the far side and
    comes back, a closed far side closes the client, and it listens on the
    ports it was given and on no other."""
    import socket

    from factory import doorway

    def free() -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    async def go():
        async def echo(reader, writer):
            writer.write(b"echo:" + await reader.read(100))
            await writer.drain()
            writer.close()

        upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
        up_port = upstream.sockets[0].getsockname()[1]
        # The same handler `serve` uses, pointed across two ports so both ends
        # can live on one loopback address.
        door = await asyncio.start_server(doorway._handler("127.0.0.1", up_port), "127.0.0.1", 0)
        door_port = door.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", door_port)
        writer.write(b"hello")
        await writer.drain()
        got = await asyncio.wait_for(reader.read(100), 5)
        tail = await asyncio.wait_for(reader.read(100), 5)
        writer.close()

        # Nothing on the far side: the client is closed, not left hanging.
        dead = await asyncio.start_server(doorway._handler("127.0.0.1", free()), "127.0.0.1", 0)
        r2, w2 = await asyncio.open_connection("127.0.0.1", dead.sockets[0].getsockname()[1])
        closed = await asyncio.wait_for(r2.read(100), 5)
        w2.close()

        # Exactly the ports it is told.
        wanted = [free(), free()]
        servers = await doorway.serve("127.0.0.1", "127.0.0.1", wanted)
        bound = sorted(s.sockets[0].getsockname()[1] for s in servers)
        for s in [upstream, door, dead, *servers]:
            s.close()
        return got, tail, closed, bound, sorted(wanted)

    got, tail, closed, bound, wanted = asyncio.run(go())
    assert got == b"echo:hello"
    assert tail == b"", "the far side closed and the door kept the client open"
    assert closed == b"", "a refused far side must close the client's connection"
    assert bound == wanted


def test_a_full_disk_is_named_rather_than_left_as_a_traceback():
    traceback = ('sqlalchemy.exc.DBAPIError: (asyncpg.Error) could not extend file '
                 '"base/16384/24579": No space left on device')
    said = gates.disk_full_problem("cd backend && alembic upgrade head", traceback)
    assert said.startswith("Docker ran out of disk space") and "alembic upgrade head" in said
    assert "docker builder prune" in said and "Docker Desktop" in said
    assert gates.disk_full_problem("pytest", "1 failed, 40 passed") == ""


# -- a hold never hides a failing test ---------------------------------------------


def test_a_tests_check_is_red_when_a_test_fails_whatever_its_number_reads(tmp_path):
    """Held at a coverage figure, a suite with a failing test read green as long
    as the figure held: the bound replaced the exit code, and for a tests check
    the same exit code also says a test failed."""
    import asyncio

    from factory.gates import run_gate
    from factory.schemas import Gate

    def run(family, code):
        gate = Gate(name="t", command=f"echo 'cover 80%'; exit {code}", parse_metric=r"cover (\d+)%",
                    threshold=78, family=family)
        return asyncio.run(run_gate(gate, tmp_path)).passed

    assert run("tests", 0) and not run("tests", 1), "a failing test was hidden by a held number"
    assert run("quality", 1), "a held count stopped replacing a linter's exit code"


def test_a_diagnosis_is_reused_while_nothing_it_read_changed():
    """Not the output: it carries timings, and a diagnosis redone for a changed
    duration is a model call bought for nothing."""
    from factory.diagnosis import fingerprint
    from factory.schemas import GateResult

    gate = _floor_gate()
    a = GateResult(name="api-tests", exit_code=1, metric=78.0, output_tail="in 3.20s")
    b = a.model_copy(update={"output_tail": "in 3.41s"})
    assert fingerprint(gate, a, "s", None, "abc", {}) == fingerprint(gate, b, "s", None, "abc", {})
    assert fingerprint(gate, a, "s", None, "abc", {}) != fingerprint(gate, a, "t", None, "abc", {})
