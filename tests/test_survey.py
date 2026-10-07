"""Reading a repository: the survey, projects, guides and dependencies.

Split out of test_invariants.py, which keeps one test per invariant.
"""

from helpers import *  # noqa: F403


def test_a_dispatch_cannot_choose_its_own_destination(gate_two_factory):
    """The route is computed from the flags, and the guard refuses any other.
    A destination nobody can honour is never reachable. (INV-6)"""
    from factory.pipeline import ProjectError

    factory, feature = gate_two_factory
    assert factory.state_of(factory.store_for(feature)).stage == "awaiting_verdict"

    # Nothing flagged yet: every route but `accept` is refused.
    assert factory.rework_plan(feature).route == "accept"
    for route in ("repair", "reopen_spec"):
        with pytest.raises(ProjectError, match="routes to 'accept'"):
            factory._dispatchable(feature, route)

    # A dismissed flag alone does not move the route either.
    factory.record_flag(feature, "checked, not a duplicate", disposition="dismissed")
    assert factory.rework_plan(feature).route == "accept"
    with pytest.raises(ProjectError, match="routes to 'accept'"):
        factory._dispatchable(feature, "repair")

    # One repairable flag routes to repair, and only to repair -- the dismissed
    # flag alongside it changes nothing about where this goes, and
    # `reopen_spec` stays unreachable: no human disposition produces it any
    # more (see test_a_legacy_spec_flag_no_longer_forces_gate_one).
    factory.record_flag(feature, "print() in a codebase with none", disposition="repair")
    assert factory.rework_plan(feature).route == "repair"
    factory._dispatchable(feature, "repair")
    with pytest.raises(ProjectError, match="routes to 'repair'"):
        factory._dispatchable(feature, "reopen_spec")


def test_the_digest_spends_its_budget_on_what_defines_the_project_first(tmp_path):
    """Alphabetical order spent the whole budget on whichever directory sorted
    first, so one survey never saw the frontend, the compose file or the
    manifests -- the things it exists to read."""
    repo = tmp_path / "repo"
    files = {
        "backend/aaa.py": "a = 1\n" * 400,
        "backend/tests/test_a.py": "t = 1\n" * 400,
        "backend/alembic/versions/001.py": "m = 1\n" * 400,
        "demo-data/dump.sql": "insert into x values (1);\n" * 400,
        "frontend/app.tsx": "const a = 1\n" * 400,
        "docker-compose.yml": "services: {}\n",
        "package.json": "{}\n",
    }
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    digest = workspace.repo_digest(repo, 5_000)
    for kept in ("docker-compose.yml", "package.json", "frontend/app.tsx", "backend/aaa.py"):
        assert f"----- {kept} -----" in digest
    assert "----- demo-data/dump.sql -----" not in digest, "data dumps are what is left out"


def test_unregistering_a_project_never_touches_the_repository(tmp_path):
    import subprocess

    from factory.projects import ProjectRegistry

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("def go(): ...\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
                   cwd=repo, check=True)

    evidence = tmp_path / "evidence"
    registry = ProjectRegistry(evidence)
    registry.create(repo, "demo")

    removed = registry.delete("demo", sandbox_root=tmp_path / "sandboxes")

    assert removed["repo"] == str(repo.resolve())
    assert (repo / "src" / "app.py").exists(), "the source repository is left alone"
    assert not (evidence / "demo").exists(), "the ledger is gone"
    assert not registry.exists("demo")


def test_setup_belongs_in_a_test_directory_and_is_not_a_test(tmp_path):
    """The rule drew its line at the path; the surveyor draws it at behaviour.

    A runner collects neither `conftest.py` nor `factories.py`, so neither can
    be the phantom green this rule exists to prevent -- and a test directory is
    where both belong, which is why the surveyor is told to propose them. Drawing
    the line at the path refused them anyway. One survey proposed three files as
    a cross-referencing set; `api/tests/factories.py` and
    `api/tests/acceptance/conftest.py` were refused and `web/src/testing/
    factories.ts` was kept, on the spelling of its directory alone -- and its
    purpose then opened "The web equivalent of the same gap", naming a gap that
    was no longer anywhere on the page.
    """
    from factory.projects import ProjectRegistry
    from factory.schemas import ScaffoldFile

    def refused(path, contents=""):
        return ProjectRegistry.scaffold_problems(
            [ScaffoldFile(path=path, contents=contents, purpose="x")])

    for path in ("api/tests/factories.py",
                 "api/tests/acceptance/conftest.py",
                 "web/src/testing/factories.ts"):
        assert refused(path) == [], f"{path} is setup, and a test directory is where it goes"

    # The same file the survey already counts as existing setup: it is what
    # makes this project's integration tier `usable`, and proposing it was
    # refused. The two answers could not both be right.
    assert refused("api/tests/conftest.py") == []

    # Exact stem, not a prefix. `testing_paths` accepts one because it is
    # choosing what to show a reading; this is choosing what may be written.
    assert refused("api/tests/testing_the_ranking.py"), \
        "a suite whose name starts with a setup name is still a suite"

    # And nothing above widened the rule itself.
    for path in ("api/tests/test_ranking.py", "src/__tests__/thing.spec.ts",
                 "web/src/components/Foo.test.tsx"):
        assert refused(path, "def test_x(): pass"), f"{path} is a test"


def test_an_incoherent_environment_fails_with_its_reason_not_a_docker_error(tmp_path):
    """Failing in `docker run` says a container would not start -- true, and
    useless. Failing here says which line of configuration is wrong."""
    import asyncio

    from factory.config import Config, PathsConfig, RoleConfig
    from factory.onboarding import ProjectOnboarding
    from factory.projects import ProjectError, ProjectRegistry
    from factory.schemas import EnvironmentSpec, Gate

    config = Config(
        roles={"surveyor": RoleConfig(name="surveyor", model="x/y")},
        paths=PathsConfig(evidence=str(tmp_path / "e"), sandboxes=str(tmp_path / "s")),
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    registry = ProjectRegistry(config.evidence_path)
    project = registry.create(repo, "demo")
    project.state.gates = [Gate(name="tests", command="pytest")]
    project.state.environment = EnvironmentSpec(kind="reuse", image="")
    registry.save(project)

    with pytest.raises(ProjectError) as caught:
        asyncio.run(ProjectOnboarding(config, registry).run_baseline(project))

    message = str(caught.value)
    assert "cannot run its checks" in message
    assert "reuse" in message and "no image is named" in message
    assert "Docker" not in message and "daemon" not in message


def test_a_command_naming_a_file_that_is_not_there_is_flagged(tmp_path):
    """`pip install -r requirements.txt` from a workdir where that file lives one
    directory down is the commonest way a baseline fails for a reason that has
    nothing to do with the code."""
    from factory.projects import ProjectRegistry
    from factory.schemas import EnvironmentSpec

    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "requirements.txt").write_text("fastapi\n")

    wrong = EnvironmentSpec(kind="host", setup=["pip install -r requirements.txt"])
    notes = ProjectRegistry.environment_notes(wrong, [], repo)
    assert notes and "requirements.txt" in notes[0] and "not in the repository" in notes[0]

    right = EnvironmentSpec(kind="host", setup=["pip install -r backend/requirements.txt"])
    assert ProjectRegistry.environment_notes(right, [], repo) == []

    # and a `cd` prefix is resolved rather than ignored
    relative = EnvironmentSpec(kind="host", setup=["cd backend && pip install -r requirements.txt"])
    assert ProjectRegistry.environment_notes(relative, [], repo) == []


def test_a_survey_whose_environment_is_a_file_it_proposed_waits_for_the_file(tmp_path):
    """The baseline of a place that does not exist yet reported the whole
    reading as failed, and the failure overwrote the survey that had just been
    saved."""
    onboarding, project, repo = _survey_of_a_compose_project(tmp_path, proposes_compose=True)
    asyncio.run(onboarding.run_survey(project))

    after = onboarding.registry.get(project.id)
    assert after.state.stage == "awaiting_approval", "the reading stands"
    assert [g.name for g in after.state.gates] == ["tests"]
    assert ".fabrika/docker-compose.yml" in after.state.error
    assert "Apply it" in after.state.error
    assert not (repo / ".fabrika").exists(), "nothing is written into the repository"


def test_a_baseline_that_fails_does_not_undo_the_survey_before_it(tmp_path):
    """A compose file named and not proposed is a real fault, and it is the
    baseline's to report -- beside a reading that is still there."""
    onboarding, project, _ = _survey_of_a_compose_project(tmp_path, proposes_compose=False)
    with pytest.raises(Exception, match="does not exist"):
        asyncio.run(onboarding.run_survey(project))

    after = onboarding.registry.get(project.id)
    assert after.state.stage == "awaiting_approval", "not 'failed': the survey succeeded"
    assert [g.name for g in after.state.gates] == ["tests"]
    assert "does not exist" in after.state.error


def test_a_projects_own_setup_files_are_watched_for_change(tmp_path):
    """`tooling_paths` finds configuration by glob. A project's setup is ordinary
    source -- `conftest.py`, `factories.py`, `test-utils.tsx` -- and matches none
    of those globs, so none of it was watched.

    The failure that follows is silent and total: a feature lands the domain
    factories the verify lane has asked for every run, drift reports nothing
    because nothing it watches moved, no re-reading happens, `testing.fixtures`
    never records them, and every future blind author is told this project has no
    way to arrange state. The factories sit in the repository, correct and
    unused, for ever.
    """
    from factory.schemas import SetupFile, TestingSurface, TestingTier

    # A project of its own. This read a real project from the live store of
    # whoever ran the suite, and failed on any machine that did not have it.
    _, project = _demo_project(tmp_path)
    (project.repo_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (project.repo_path / "tests").mkdir()
    for name in ("conftest.py", "factories.py"):
        (project.repo_path / "tests" / name).write_text("# setup a new test may use\n")
    project.state.testing = TestingSurface(tiers=[TestingTier(
        tier="integration", runner="pytest", verdict="usable",
        setup_files=[SetupFile(path="tests/conftest.py", contents="#"),
                     SetupFile(path="tests/factories.py", contents="#")])])
    project.state.scaffolding_applied = ["tests/test_utils.py"]
    watched = set(pipeline_projects_module.watched_paths(project))
    by_glob = set(workspace.tooling_paths(project.repo_path))

    assert watched >= by_glob, "the glob net must still be inside the wider one"
    recorded = {f.path for t in project.state.testing.tiers for f in t.setup_files}
    assert recorded, "this project records no setup files, so the test proves nothing"
    assert recorded - by_glob, "the glob net catches these anyway, so the test proves nothing"
    assert recorded <= watched, sorted(recorded - watched)
    # And the files a human agreed to write into their own repository.
    assert set(project.state.scaffolding_applied or ()) <= watched


def test_what_the_verify_lane_asked_for_reaches_the_next_reading():
    """The loop that was open for five runs.

    A blind test author reports the capabilities it needed and did not have, and
    names the criteria each one cost. Those reached the packet and stopped: the
    only role that can propose the missing file was never told the request
    existed. So a project sat with four outstanding requests and two permanently
    unverifiable criteria, while its re-survey correctly reported the repository
    unchanged -- which it was. The gap was never in the repository.
    """
    src = inspect.getsource(onboarding.ProjectOnboarding.run_resurvey)
    assert "_capability_requests(project)" in src, (
        "the reading is not shown what the verify lane asked for")

    def _fake(fid):
        return types.SimpleNamespace(records=lambda: [
            {"kind": "oracle", "payload": {"requires": [
                {"need": "a disposable database", "criterion_ids": ["AC-1"],
                 "detail": "migrate it and look"}]}}])
    rendered = onboarding._capability_requests(types.SimpleNamespace(
        feature_ids=lambda: ["f1", "f2"], feature_store=_fake,
        state=types.SimpleNamespace(capability_rulings=[])))
    assert "a disposable database" in rendered
    assert "asked for by 2 feature(s)" in rendered, "a repeated request must read as repeated"
    # An id is meaningless without the feature it was numbered inside: every spec
    # is numbered from one, so two features' `AC-1` are different criteria and a
    # flat list of them reads as one numbering.
    assert "f1: AC-1" in rendered and "f2: AC-1" in rendered, rendered
    assert "belong to the feature beside them" in rendered

    # Silent where there is nothing outstanding: a heading over an empty list is
    # a heading a reader learns to skip.
    assert onboarding._capability_requests(types.SimpleNamespace(
        feature_ids=lambda: [], feature_store=lambda fid: None,
        state=types.SimpleNamespace(capability_rulings=[]))) == ""

    text = (ROOT / "factory" / "roles" / "surveyor.md").read_text(encoding="utf-8")
    assert "Propose `scaffolding` for what the verify lane has asked" in text


def test_criterion_ids_are_never_merged_across_features():
    """`AC-1` is sequential from one inside a single frozen spec, so every
    feature has one. Merging them into a flat list makes two different criteria
    look like a single numbering -- and a project-level field citing a bare
    `AC-23` identifies nothing to a reader once that feature is history."""
    def _store(fid):
        ids = ["AC-1", "AC-2"] if fid == "alpha" else ["AC-1", "AC-9"]
        return types.SimpleNamespace(records=lambda: [
            {"kind": "oracle", "payload": {"requires": [
                {"need": "a disposable database", "criterion_ids": ids}]}}])
    rows = pipeline_projects_module.unmet_capabilities(types.SimpleNamespace(
        feature_ids=lambda: ["alpha", "beta"], feature_store=_store,
        state=types.SimpleNamespace(capability_rulings=[])))
    assert len(rows) == 1, "one capability, however many features asked"
    by_feature = {c["feature_id"]: c["criterion_ids"] for c in rows[0]["cost"]}
    assert by_feature == {"alpha": ["AC-1", "AC-2"], "beta": ["AC-1", "AC-9"]}


def test_every_rule_is_probed_and_the_weakest_answer_wins():
    """One lenient runner is a hole, whichever rule happens to be listed first.

    Measured on a real repository: `npx vitest run <nothing>` exits 1, while
    that same project's `npm test` script -- `vitest run --passWithNoTests` --
    exits 0 on the identical input. A project can hold both. Probing only the
    first rule measures whichever runner the surveyor happened to write down
    first, which for that repository was not the one the blind suite uses.
    """
    src = inspect.getsource(onboarding.ProjectOnboarding._baseline)
    assert "for template in rules:" in src, \
        "only one rule is probed, so a second runner's leniency goes unmeasured"
    assert "False if False in answers" in src, \
        "a lenient rule does not win, so a hole can be averaged away"
    assert "None if None in answers" in src, (
        "a probe that could not run is being read as a strict runner, which is "
        "the one direction this must never guess in"
    )


def test_the_only_button_a_healthy_project_has_can_carry_a_testing_reading():
    """The console offers one re-reading, and it produces a diff.

    "Survey from scratch" appears only on a failed project -- deliberately, so a
    gate list cannot be replaced wholesale by accident. So `SurveyDiff` is the
    only route a working project has to any of this, and a field the diff cannot
    carry is a field unreachable from the UI. That already happened once with
    `test_file_commands`: proposed in a prompt, droppable everywhere else.
    """
    from factory import projects as pipeline_projects
    from factory.schemas import SurveyDiff

    assert "testing" in SurveyDiff.model_fields, \
        "the one button a healthy project has cannot propose a testing surface"
    applied = inspect.getsource(pipeline_projects.ProjectRegistry.apply_survey_diff)
    assert 'changes["testing"] = diff.testing' in applied, \
        "a proposed reading is accepted and then dropped on the floor"
    assert '"testing" in wanted' in applied, \
        "it is applied without the human having accepted it by name"

    # And the re-read has to be shown the files it is judging. Only drifted
    # tooling used to reach it, which says nothing about test setup.
    prompt = inspect.getsource(onboarding.ProjectOnboarding.run_resurvey)
    assert "testing_paths(" in prompt and "project.repo_path" in prompt, \
        "the resurvey is asked about test setup it was never shown"

    flat = " ".join((ROOT / "factory" / "roles" / "surveyor.md")
                    .read_text(encoding="utf-8").split())
    assert "no testing surface recorded is always a `change`" in flat


def test_gate_zero_runs_its_gates_in_the_environment_a_feature_would_get():
    """The baseline has to stand up whatever the project declared standing.

    A project can declare services its gates need -- an application server the
    suite drives over HTTP, an emulator. This was wired into the feature path
    first and not into this one, and a survey then correctly moved `uvicorn` out
    of a gate command and into `services`. That gate would have run against
    nothing, on the one screen where a human decides whether these checks are
    worth having, and the refused connection would have read as the
    repository's fault.
    """
    src = inspect.getsource(onboarding.ProjectOnboarding._baseline)
    session_at = src.index("test_session(")
    assert "run_gates(" in src[session_at:], \
        "gate 0 runs its gates outside the prepared environment"
    assert "started=False" in src[session_at:], (
        "an environment that could not stand up produces gates that look like "
        "they ran and failed, rather than gates that could not run"
    )
    # What the project declared, not an empty tuple. Asserting `services=` alone
    # passed against `services=()` -- a session opened around nothing, which is
    # the bug wearing the shape of the fix.
    assert "services=list(environment.services)" in src, \
        "a session is opened but the project's services are not started in it"
    assert "prepare=list(environment.test_prepare)" in src, \
        "a session is opened but the project's preparation never runs"

    # And a feature gets the same treatment from the same helper, so the two
    # cannot drift: this bug existed because one path had it and the other did not.
    assess = inspect.getsource(pipeline.Factory._assess)
    for passed in ("services=list(environment.services)",
                   "prepare=list(environment.test_prepare)"):
        assert passed in assess, f"the feature path no longer passes {passed}"


def test_a_proven_command_joins_a_reading_as_one_card():
    """Nothing is applied. It arrives as the rules with the command filled in,
    beside everything else the reading proposed, for a person to accept."""
    from factory.onboarding import _propose_reports
    from factory.reporting import Outcome, Search
    from factory.schemas import SurveyDiff

    found = Search(
        rules=[_rule("run {path}", match="web/*", report="run -r {report} {path}"),
               _rule("go {path}")],
        reason="Lets each test report its own result.",
        outcomes=[Outcome(match="web/*", command="run {path}", outcome="verified",
                          report="run -r {report} {path}")])

    diff = SurveyDiff(summary="")
    _propose_reports(diff, found)
    assert [r.report for r in diff.test_file_commands] == ["run -r {report} {path}", ""]
    assert diff.test_file_commands_reason == "Lets each test report its own result."

    # The reading proposed rules of its own: a proof fills only the one it was for.
    diff = SurveyDiff(summary="", test_file_commands=[
        _rule("run {path}", match="web/*"), _rule("run --other {path}", match="lib/*")],
        test_file_commands_reason="The lib runner moved.")
    _propose_reports(diff, found)
    assert [r.report for r in diff.test_file_commands] == ["run -r {report} {path}", ""]
    assert diff.test_file_commands_reason.startswith("The lib runner moved.")
    assert "Lets each test report" in diff.test_file_commands_reason


def test_an_optional_check_is_not_a_way_around_the_rule(tmp_path):
    """`optional` marks a failure as skipped, which every other reader treats as
    "a human already said they know".

    It must not buy a check anything at gate 0. Under the old rule it would have
    been a one-click way to keep a red row on the list without blocking; under
    parking it would be a way to keep a red row in the set that judges features,
    which is worse -- that row then appears in every packet, red for reasons
    that have nothing to do with the feature. `is_green` ignores the flag, so an
    optional red check parks like any other."""
    from factory.projects import ProjectError, ProjectRegistry
    from factory.schemas import Gate

    repo = tmp_path / "repo"
    repo.mkdir()
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "optional-out")
    project.state.gates = [Gate(name="lint", command="ruff check .", optional=True)]
    project.state.baseline = gates.GateReport(results=[
        _result("lint", exit_code=1, skipped=True, output_tail="Found 3 errors."),
    ])
    _proved_placement(project)

    with pytest.raises(ProjectError) as caught:
        registry.approve(project)
    assert "not a way round it" in str(caught.value), (
        "the refusal no longer says that marking a check optional buys it nothing")


def test_changing_where_blind_tests_go_invalidates_the_baseline(tmp_path):
    """What was measured was measured about particular directories.

    `placement_probe` is a fact about the places named when it ran. Move them and
    the measurement describes somewhere that is no longer in use, which is the
    same reason a changed gate list drops the baseline.
    """
    from factory.projects import ProjectRegistry
    from factory.schemas import BlindPlacement

    registry, project = _approved_project(tmp_path)
    before = registry.approval_fingerprint(project)

    project.state.blind_placements = [BlindPlacement(
        directory="somewhere/else", filename="canary_test.py",
        canary_passes="PASS", canary_fails="FAIL")]
    assert registry.approval_fingerprint(project) != before, (
        "moving the blind suite does not change the fingerprint, so a project would stay "
        "approved on a measurement of directories it no longer uses")
    assert "blind_placements" in ProjectRegistry.INVALIDATES_BASELINE


def test_a_project_with_a_gate_that_cannot_run_is_refused(tmp_path):
    from factory.projects import ProjectError, ProjectRegistry

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / "a.py").write_text("x = 1\n")
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "missing-tool")
    project.state.baseline = gates.GateReport(results=[
        _result("lint", passed=True, output_tail="clean"),
        _result("e2e", exit_code=127, output_tail="sh: playwright: command not found"),
    ])
    with pytest.raises(ProjectError) as exc:
        registry.approve(project)
    assert "could not run" in str(exc.value) and "e2e" in str(exc.value)
    assert project.state.stage != "ready"


def test_the_detector_never_triggers_a_survey_on_its_own():
    """It leads to a sentence on a page. Re-surveying changes what the project
    measures, and a detector that did it unasked would be a model quietly
    rewriting the contract."""
    source = factory_source("projects")
    body = source[source.index("def tooling_drift(self"):]
    body = body[:body.index("\n    def ")]
    assert "run_survey" not in body and "llm" not in body


def test_a_config_file_outranks_a_gitignore_entry():
    """A file that names a tool tells you more than a directory that hints at
    one -- and often names a tool the gitignore cannot, which is exactly how
    pytest-asyncio went missing."""
    text = SURVEYOR.read_text()
    section = text[text.index("What counts as evidence"):]
    section = section[:section.index("\n## ")]
    order = [section.index(m) for m in (
        "A CI step", "A dependency on it", "A config file that names it",
        "A cache directory in `.gitignore`")]
    assert order == sorted(order), "the evidence hierarchy is out of order"
    assert "pytest-asyncio" in section, (
        "the rule is abstract without the case that produced it"
    )
    # The rule is now an instruction with a table rather than a maxim; the
    # dedicated test above pins that form.
    assert "list the packages it implies" in _prose(section)


def test_an_inferred_dev_dependency_is_proposed_as_scaffolding():
    """`scaffolding` exists for exactly this and the surveyor proposed none."""
    text = SURVEYOR.read_text()
    section = text[text.index("## When a tool is simply missing"):]
    section = section[:section.index("\n## ")]
    assert "propose the manifest that would end the guessing" in _prose(section)
    assert "requirements.txt" in section
    # And the older rule it must not have displaced.
    assert "Never propose a test." in section


def test_compose_is_first_when_the_suite_needs_a_service():
    """Gates run as one container with no companions. A suite that opens a
    connection to Postgres cannot pass in any built image, however good -- and
    marking it optional makes a permanent hole look like a decision."""
    text = SURVEYOR.read_text()
    section = text[text.index("## Environment"):]
    section = section[:section.index("\n## ")]
    order = {kind: section.index(f"**{kind}**")
             for kind in ("compose", "reuse", "derive", "generate")}
    assert order["compose"] < order["reuse"] < order["derive"] < order["generate"], (
        f"compose must be resolved first when it applies: {order}"
    )
    assert "optional test that can never run" in _prose(section)

    # Every kind the schema allows has to be *accounted for* in the prompt --
    # offered, or named and excluded with a reason. `compose` was neither, which
    # is how a supported environment and the documented preference went unused
    # for a project that shipped a compose file with the database its tests need.
    from factory.schemas import EnvironmentSpec
    kinds = typing.get_args(EnvironmentSpec.model_fields["kind"].annotation)
    for kind in kinds:
        assert f"**{kind}**" in section, f"the prompt neither offers nor rules out {kind!r}"
    # `host` is named and ruled out: it runs checks on this machine, and the
    # factory refuses to build a project that uses it.
    assert "nobody may choose it" in _prose(section)


def test_a_path_in_the_dockerfile_field_is_caught_before_the_build():
    """A survey once put `backend/Dockerfile` in the field that takes Dockerfile
    *contents*. It reached `docker build` as the file body and failed three
    phases later with "unknown instruction: backend/Dockerfile"."""
    from factory.projects import ProjectRegistry

    def problems(dockerfile: str) -> list[str]:
        env = EnvironmentSpec(kind="generate", rationale="r", dockerfile=dockerfile)
        return [p for p in ProjectRegistry.environment_problems(env, []) if "FROM" in p]

    caught = problems("backend/Dockerfile")
    assert caught and "looks like a path" in caught[0]
    assert problems("FROM python:3.12-slim\nRUN pip install pytest\n") == []
    assert problems("# a comment first\nFROM node:20\n") == [], "FROM need not be line one"
    # Empty is a separate, already-reported problem for derive and generate.
    assert problems("") == []


def test_the_surveyor_is_told_the_checks_run_in_one_place():
    """It proposed compose with the checks in `backend`, then said in its own
    rationale that the frontend checks would run "in the frontend compose service
    or a Node container" -- which the schema cannot express. One environment,
    one service, every check."""
    text = SURVEYOR.read_text()
    section = text[text.index("## Environment"):]
    section = section[:section.index("\n## ")]
    prose = _prose(section)
    assert "All the checks run in one place." in prose
    assert "one service" in prose
    # And the way out, which the code already supports and the prompt did not say.
    assert "A compose environment may also carry a `dockerfile`" in prose
    assert "drop the checks that do not fit" in prose


def test_a_known_bad_environment_never_reaches_docker_build():
    """The guard found `Dockerfile.gates` and the build ran anyway: the problems
    list disabled a button in the console and nothing consulted it on the survey
    path, so the two disagreed about whether the environment was runnable."""
    from factory.onboarding import ProjectOnboarding
    src = inspect.getsource(ProjectOnboarding._baseline)
    assert "environment_problems" in src, \
        "the baseline builds an image without checking what is already known about it"
    assert src.index("environment_problems") < src.index("runner_for"), \
        "the check has to happen before the image is built, not after"


def test_the_gates_are_not_run_inside_the_database(tmp_path):
    """A survey named `postgres` as the gate service and supplied a Dockerfile
    adding Python and Node. Compose started that image as the database, its
    healthcheck failed, and all six gates reported "container is unhealthy" --
    which reads as broken infrastructure rather than as the database having been
    replaced by a toolchain."""
    from factory.projects import ProjectRegistry

    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "services:\n"
        "  postgres:\n    image: postgres:16-alpine\n"
        "  backend:\n    build: ./backend\n    depends_on: [postgres]\n"
        "  frontend:\n    build: ./frontend\n    depends_on: [backend]\n"
    )
    check = ProjectRegistry._compose_service_problems

    bad = check(compose, "postgres", True)
    assert bad and "no longer" not in bad[0]
    assert "pulled from a registry" in bad[0]
    assert "backend, frontend" in bad[0], "it should say which services are buildable"

    # The application services are the right answer even though both are
    # depended on -- "depended on" is not the signal, image provenance is.
    assert check(compose, "backend", True) == []
    assert check(compose, "frontend", True) == []
    # Without a Dockerfile nothing is being replaced, so nothing is wrong.
    assert check(compose, "postgres", False) == []
    # A service that is not in the file at all.
    assert "names no service" in check(compose, "typo", True)[0]


def test_a_metric_that_captures_nothing_is_refused(tmp_path):
    """A survey set `parse_metric='errors'` with `threshold=0` on `tsc --noEmit`,
    whose exit code was already the answer. The pattern has no capture group, so
    it reads no number on any output ever -- and with a threshold riding on it,
    three commands that exited zero were reported as failures."""
    from factory.projects import ProjectRegistry

    caught = ProjectRegistry.gate_problems([
        Gate(name="typecheck", command="tsc --noEmit", parse_metric="errors", threshold=0.0),
    ])
    assert caught and "captures nothing" in caught[0]
    assert "fails on every run" in caught[0], "the consequence is the point, not the pattern"

    # A real metric gate, with the number in the first group, is untouched.
    assert ProjectRegistry.gate_problems([
        Gate(name="mutation", command="mutmut run",
             parse_metric=r"(\d+(?:\.\d+)?)%\s+mutation score", threshold=60.0),
    ]) == []
    # And so is an ordinary gate that reads no number at all.
    assert ProjectRegistry.gate_problems([Gate(name="lint", command="ruff check .")]) == []
    # `exit_code` is a sentinel, not a pattern.
    assert ProjectRegistry.gate_problems([
        Gate(name="x", command="y", parse_metric="exit_code", threshold=1.0)]) == []


def test_a_pattern_the_tool_cannot_print_into_a_pipe_is_refused(tmp_path):
    """Well-formed, correct for the tool, and dead where the check will run it.

    `tsc` prints its `Found N errors` summary only under `--pretty`, and turns
    `--pretty` off by itself when stdout is not a terminal -- which a gate's
    stdout always is. The surveyor is told to give a count pattern for every
    check that reports one, so a human can ratchet it; on a `npm run typecheck`
    it produced a pattern that reads nothing on any run there will ever be, and
    the ratchet the filled-in field advertised would have turned the gate red
    forever the moment somebody used it.
    """
    from factory.projects import ProjectRegistry

    direct = ProjectRegistry.gate_problems([
        Gate(name="web-types", command="cd web && tsc --noEmit",
             parse_metric=r"Found (\d+) error"),
    ])
    assert direct and "only under --pretty" in direct[0]
    assert "drop the pattern" in direct[0], "it has to say what to do instead"

    # Putting the flag in the command is the other way out, and is accepted.
    assert ProjectRegistry.gate_problems([
        Gate(name="web-types", command="cd web && tsc --noEmit --pretty",
             parse_metric=r"Found (\d+) error"),
    ]) == []
    # `--pretty false` is the flag present and saying no.
    assert ProjectRegistry.gate_problems([
        Gate(name="web-types", command="cd web && tsc --noEmit --pretty false",
             parse_metric=r"Found (\d+) error"),
    ]), "the flag is there and turns the summary off"

    # A bound riding on a pattern that never matches is the always-red case.
    bound = ProjectRegistry.gate_problems([
        Gate(name="web-types", command="tsc --noEmit",
             parse_metric=r"Found (\d+) error", threshold_max=12.0),
    ])
    assert bound and "fails every time" in bound[0]

    # The tool is usually behind the package script that calls it, which is the
    # form CI uses and therefore the form a survey copies.
    repo = tmp_path / "repo"
    (repo / "web").mkdir(parents=True)
    (repo / "web" / "package.json").write_text(
        '{"scripts": {"typecheck": "tsc --noEmit"}}')
    hidden = Gate(name="web-types", command="cd web && npm run typecheck",
                  parse_metric=r"Found (\d+) error")
    assert ProjectRegistry.gate_problems([hidden]) == [], \
        "with no repository to resolve the script, nothing is claimed"
    assert ProjectRegistry.gate_problems([hidden], repo), \
        "the script resolves to tsc and the pattern is dead behind it"

    # Tools that do print their summary into a pipe keep their patterns. These
    # four are what a real onboarding produced; only the tsc one is wrong.
    assert ProjectRegistry.gate_problems([
        Gate(name="api-lint", command="cd api && ruff check .",
             parse_metric=r"Found (\d+) error"),
        Gate(name="api-types", command="cd api && mypy app",
             parse_metric=r"Found (\d+) error"),
        Gate(name="web-lint", command="cd web && npx eslint .",
             parse_metric=r"(\d+) problems? \("),
    ]) == []

    # And the tool that cannot print a summary is not always the one the number
    # was coming from: a chain reads its percentage from the other command.
    assert ProjectRegistry.gate_problems([
        Gate(name="web", command="tsc --noEmit && npx vitest run --coverage",
             parse_metric=r"All files\s+\|\s+(\d+(?:\.\d+)?)", threshold=80.0),
    ]) == []


def test_a_threshold_with_nothing_to_read_is_refused():
    from factory.projects import ProjectRegistry
    caught = ProjectRegistry.gate_problems([Gate(name="cov", command="pytest", threshold=80.0)])
    assert caught and "silently ignored" in caught[0]


def test_an_unparseable_metric_pattern_is_refused():
    from factory.projects import ProjectRegistry
    caught = ProjectRegistry.gate_problems([
        Gate(name="x", command="y", parse_metric="(unclosed")])
    assert caught and "not a valid regular expression" in caught[0]


def test_a_gate_that_cannot_produce_a_result_blocks_the_baseline():
    """Same list, same consumers: a check whose definition cannot yield an
    answer and an environment that cannot run one are the same fact at gate 0."""
    from factory.projects import ProjectRegistry
    problems = ProjectRegistry.environment_problems(
        EnvironmentSpec(kind="host", rationale="r"),
        [Gate(name="typecheck", command="tsc", parse_metric="errors", threshold=0.0)],
    )
    assert any("captures nothing" in p for p in problems), \
        "gate problems have to reach the list that blocks the baseline"


def test_the_recheck_is_given_the_tree_and_not_only_the_repair_narrative(tmp_path):
    """A re-check that reads a repair summary is judging a claim.

    Three agents concluded across two rounds that a duplicate class was still
    crashing an import. It had been deleted in round 1; what they were shown
    was the round's own diff plus a repairer reporting that its harness had
    made no edits, and from that pair the honest inference is exactly the wrong
    one. The packet then led with a defect that was not there.
    """
    (tmp_path / "widget").mkdir()
    (tmp_path / "widget" / "spin.py").write_text("def spin():\n    return 1\n")

    section = pipeline.tree_section(
        tmp_path, ["widget/spin.py", "widget/gone.py", "../../etc/passwd"])
    assert "def spin():" in section, "the file that exists is not shown"
    assert "no such file in the working tree" in section, \
        "a file the repair claims to have written, absent, must read as absent"
    assert "outside the checkout" in section and "root:" not in section, \
        "paths come from findings, which are model-written; they escape the checkout"

    assert pipeline.tree_section(tmp_path, []) == "(the findings name no files)"

    src = inspect.getsource(pipeline.Factory._recheck)
    assert "tree_section(sandbox.path" in src, \
        "the re-check builds its prompt without reading the working tree"


def test_two_projects_open_at_once_do_not_empty_each_others_guides(tmp_path, monkeypatch):
    """The guide view and offers were cached in a dict emptied on every miss,
    then read back by key. Two projects polling in turn threw each other's
    answer away on every request -- and a miss on another thread between the
    write and the read was a KeyError, served as a 500."""
    from factory import guides
    from factory.server import guide_offers, guide_view

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _, one = _demo_project(tmp_path / "a")
    _, two = _demo_project(tmp_path / "b")

    reads: list[str] = []
    tracked = guides.tracked
    # Reading the working copy is not cached and is not counted; reading a commit is.
    monkeypatch.setattr(guides, "tracked", lambda repo, ref: (
        ref != "HEAD" and reads.append(str(repo))) or tracked(repo, ref))

    for _ in range(3):
        for project in (one, two):
            assert guide_view(project)["sha"]
            guide_offers(project)
    assert sorted(reads) == sorted([str(one.repo_path), str(two.repo_path)] * 2), (
        "each project's view and offers are worked out once and kept")


def test_the_gate_two_screens_say_when_their_numbers_are_being_replaced():
    """The requirement-first screens carry the same superseded figures the
    summary does -- blockers open, gates passing, minutes budgeted -- and now
    that they are what a packet opens, they are where a stale reading would be
    read."""
    index = (ROOT / "console" / "ui" / "index.js").read_text(encoding="utf-8")
    assert "function Superseded" in index
    assert "SETTLED.includes(feature.stage)" in index, \
        "the strip does not check whether the reading is still current"
    assert "<${Superseded}" in index, "the strip is defined and never drawn"

    # And "being replaced" is not the same claim as "being replaced right now".
    # A reopened spec sits at `awaiting_answers` with no owner and no phase in
    # flight: nothing is measuring anything, and the strip said a run was under
    # way while the station lamp two inches below it said "questions - you".
    assert "!feature.owner && WAITS[feature.stage]" in index, \
        "the strip announces a run without asking whether one is running"
    body = index[index.index("const WAITS = {"):]
    body = body[:body.index("\n  };")]
    for stage in ("awaiting_answers", "awaiting_spec_approval"):
        assert stage in body, f"{stage} waits on a person and the strip does not say so"
    assert "Nothing is running" in index, \
        "a reader cannot tell a stalled run from one waiting on them"


def test_accepting_a_reading_in_full_counts_as_having_read():
    """Otherwise the drift notice outlives the act that answered it."""
    src = factory_source("projects")
    apply_fn = src[src.index("def apply_survey_diff("):]
    apply_fn = apply_fn[:apply_fn.index("\n    def ")]
    assert "if record and applied and not rejected:" in apply_fn, \
        "a fully accepted reading does not record that it happened"
    assert 'changes["survey_sha"]' in apply_fn and 'changes["survey_paths"]' in apply_fn
    assert apply_fn.index("if record and applied and not rejected:") < apply_fn.index("if applied:"), \
        "the stamp has to be part of the same write, or a crash leaves them disagreeing"

    onb = factory_source("onboarding")
    resurvey = onb[onb.index("async def run_resurvey("):]
    resurvey = resurvey[:resurvey.index("\n    # -- the human's gate")]
    assert "record_reading(project)" in resurvey, \
        "a reading that proposes nothing has still read the repository"


# --------------------------------------------------------------------------
# INV: a finding's `files` list is model-written text, not a filesystem
#
# `tree_section` renders those paths off disk so a re-check settles against the
# checkout instead of a repairer's summary. That is right. Treating every string
# in the list as a path, and every failure to read one as proof of absence, is
# what kept a blocker alive for three rounds on a migration that was on disk.
# --------------------------------------------------------------------------


def test_a_description_where_a_path_belonged_is_not_read_as_absence(tmp_path):
    """The exact string from the run, and the file it wrongly condemned."""
    (tmp_path / "backend" / "alembic" / "versions").mkdir(parents=True)
    (tmp_path / "backend" / "alembic" / "versions" / "e5f6_slots.py").write_text(
        "revision = 'e5f6a7b8c9d0'\n")

    section = pipeline.tree_section(
        tmp_path, ["backend/alembic/versions/ (no new file)"])

    assert "no such file in the working tree" not in section, \
        "a sentence was rendered as proof that a file is missing"
    assert "not a file path" in section
    assert "do not conclude" in section.lower()


def test_a_directory_is_reported_as_a_directory_and_lists_what_is_in_it(tmp_path):
    (tmp_path / "backend" / "alembic" / "versions").mkdir(parents=True)
    (tmp_path / "backend" / "alembic" / "versions" / "e5f6_slots.py").write_text("x = 1\n")

    section = pipeline.tree_section(tmp_path, ["backend/alembic/versions/"])
    assert "no such file in the working tree" not in section
    assert "e5f6_slots.py" in section, \
        "the reviewer is left unable to see the file that answers its own finding"


def test_a_breaker_probe_removed_by_design_is_not_read_as_absence(tmp_path):
    """The breaker's tests are deleted after they run, on purpose.

    Findings still name their paths, so the re-check rendered them off disk,
    found nothing, and every breaker-authored finding became permanently
    `not_fixed` -- unfixable by construction rather than by fact.
    """
    section = pipeline.tree_section(
        tmp_path, ["tests/breaker/test_screening_slots.py"],
        removed_prefixes=["tests/breaker"])

    assert "no such file in the working tree" not in section
    assert "deleted from the tree" in section and "not evidence" in section.lower()

    src = inspect.getsource(pipeline.Factory._recheck)
    assert "removed_prefixes=[self.config.rework.breaker_dir]" in src, \
        "the re-check does not tell tree_section which paths it deletes itself"


def test_the_binaries_a_project_runs_are_derived_from_its_own_commands():
    """A hand-maintained list goes stale the first time a gate changes."""
    found = unitenv.binaries([
        "cd backend && API_BASE_URL=http://127.0.0.1:$FACTORY_PORT_API pytest",
        "cd frontend && npx tsc -p tsconfig.build.json && npx vite build",
        "cd backend && python -m app.seed --reset",
    ])
    assert found[:4] == ["pytest", "npx", "python", "python3"], found
    assert "cd" not in found, "a shell builtin was shimmed as a program"
    assert "tsc" not in found, "only the first word of a segment names the program"


def test_a_family_gone_without_blocks_nothing_and_is_not_suggested_again(tmp_path):
    registry, project = _project_with_suggestions(tmp_path)
    titles = lambda: {r.title for r in registry.live_recommendations(project)}  # noqa: E731
    assert titles() == {"Measure complexity", "Lint the code"}

    project = registry.decline_family(project, "quality", "a prototype")
    assert titles() == {"Lint the code"}, "a declined family is still being suggested"
    assert project.state.stage == "ready", "families are optional; declining one changed the stage"

    from factory.onboarding import _declined_suggestions
    assert "every `quality` check" in _declined_suggestions(project), \
        "the next reading is not told, so it pays to write a card nobody sees"

    project = registry.reinstate_family(project, "quality")
    assert titles() == {"Measure complexity", "Lint the code"}


# -- dependencies: read from lockfiles, looked up, held to a person's policy -----


def test_each_lockfile_says_what_is_direct_and_what_came_through_it():
    from factory import dependencies as dep

    uv = dep.parse_uv(
        '[[package]]\nname = "app"\nversion = "0"\nsource = { virtual = "." }\n'
        'dependencies = [{ name = "Flask" }]\n\n'
        '[[package]]\nname = "flask"\nversion = "3.0.0"\nsource = { registry = "https://pypi.org/simple" }\n'
        'dependencies = [{ name = "jinja2" }]\n\n'
        '[[package]]\nname = "jinja2"\nversion = "3.1.4"\nsource = { registry = "https://pypi.org/simple" }\n\n'
        '[[package]]\nname = "secret-lib"\nversion = "1.0"\nsource = { registry = "https://pkgs.corp.example/simple" }\n',
        set())
    by = {p.name: p for p in uv}
    assert by["flask"].direct and not by["jinja2"].direct and by["jinja2"].via == "flask"
    assert by["secret-lib"].private, "a package from a private registry was taken for public"

    reqs = dep.parse_requirements(
        "click==8.1.7\n    # via -r requirements.in\nrich==13.7.1\n    # via\n"
        "    #   -r requirements.in\nmarkdown-it-py==3.0.0\n    # via rich\n", set())
    by = {p.name: p for p in reqs}
    assert by["click"].direct and by["rich"].direct
    assert not by["markdown-it-py"].direct and by["markdown-it-py"].via == "rich"

    lock = dep.parse_package_lock(json.dumps({"lockfileVersion": 3, "packages": {
        "": {"dependencies": {"express": "^4"}},
        "node_modules/express": {"version": "4.19.2", "dependencies": {"qs": "6"},
                                 "resolved": "https://registry.npmjs.org/express/-/express-4.19.2.tgz"},
        "node_modules/qs": {"version": "6.11.0"}}}), set())
    by = {p.name: p for p in lock}
    assert by["express"].direct and by["qs"].via == "express" and not by["express"].private

    yarn = dep.parse_yarn('express@^4.0.0:\n  version "4.19.2"\n  dependencies:\n    qs "6.11.0"\n\n'
                          'qs@6.11.0:\n  version "6.11.0"\n', {"express"})
    assert {(p.name, p.version, p.direct) for p in yarn} == {("express", "4.19.2", True),
                                                            ("qs", "6.11.0", False)}
    pnpm = dep.parse_pnpm("lockfileVersion: '9.0'\nimporters:\n  .:\n    dependencies:\n"
                          "      express:\n        specifier: ^4\n        version: 4.19.2\n"
                          "snapshots:\n  express@4.19.2:\n    dependencies:\n      qs: 6.11.0\n"
                          "  qs@6.11.0: {}\n", set())
    by = {p.name: p for p in pnpm}
    assert by["express"].direct and by["qs"].via == "express"
    poetry = dep.parse_poetry('[[package]]\nname = "requests"\nversion = "2.32.3"\n'
                              '[package.dependencies]\nidna = ">=2"\n\n'
                              '[[package]]\nname = "idna"\nversion = "3.7"\n', {"requests"})
    by = {p.name: p for p in poetry}
    assert by["requests"].direct and by["idna"].via == "requests"
    assert dep.parse_uv("not toml [", set()) is None, "a broken lockfile read as an empty project"


def test_a_feature_s_dependency_changes_are_read_from_the_lockfiles(tmp_path):
    from factory import dependencies as dep

    def tree(files):
        return dep.read_inventory(list(files), files.get)

    base = tree({"requirements.txt": "click==8.1.7\nrich==13.0.0\nold==1.0\n"})
    head = tree({"requirements.txt": "click==8.1.7\nrich==13.7.1\nhttpx==0.27.0\n",
                 "pyproject.toml": '[project]\ndependencies = ["click", "httpx", "brand-new"]\n'})
    changes = {c.name: c.kind for c in dep.diff(base, head)}
    assert changes == {"rich": "upgraded", "httpx": "added", "old": "removed"}
    assert dep.unresolved(base, head) == [("PyPI", "brand-new")], \
        "a declared package no lockfile pins was not noticed"


def test_a_license_is_judged_by_the_choice_it_gives():
    from factory.dependencies import license_verdict

    deny, flag = ["GPL-*", "GPL", "AGPL-*"], ["LGPL-*", "MPL-*"]
    assert license_verdict("MIT", deny, flag) == ""
    assert license_verdict("GPL-3.0-only", deny, flag) == "denied"
    assert license_verdict("LGPL-2.1-or-later", deny, flag) == "flagged", "GPL-* caught LGPL"
    assert license_verdict("MIT OR GPL-3.0", deny, flag) == "", "a choice of MIT was refused"
    assert license_verdict("MIT AND GPL-3.0", deny, flag) == "denied"
    assert license_verdict("", deny, flag) == "unknown", "no license read as permissive"


def test_each_kind_of_bad_dependency_is_its_own_finding(tmp_path):
    import asyncio
    from datetime import datetime, timedelta, timezone

    from factory.dependencies import Facts
    from factory.schemas import DependencyPolicy

    week = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    today = datetime.now(timezone.utc).isoformat()
    stub = _StubLookup({
        "evil": Facts(True, "MIT", week, malicious=["MAL-2026-1"]),
        "leaky": Facts(True, "MIT", week, advisories=["GHSA-xxxx"]),
        "viral": Facts(True, "AGPL-3.0", week),
        "mystery": Facts(True, "", week),
        "fresh": Facts(True, "MIT", today),
        "silent": Facts(False),
        "fine": Facts(True, "MIT", week),
    })
    base = "fine==1.0\n"
    head = "".join(f"{n}==1.0\n" for n in
                   ("evil", "leaky", "viral", "mystery", "fresh", "silent", "fine")) \
        + "--extra-index-url https://pkgs.corp.example/simple\n"
    _branch(tmp_path, {"requirements.txt": base}, {"requirements.txt": head})

    from factory import dependencies as dep
    before = dep.read_inventory(["requirements.txt"], lambda p: base)
    after = dep.read_inventory(["requirements.txt"], lambda p: head)
    changes = dep.diff(before, after)
    assert all(c.private for c in changes), "an extra private index left names public"

    head_public = head.replace("--extra-index-url https://pkgs.corp.example/simple\n", "")
    after = dep.read_inventory(["requirements.txt"], lambda p: head_public)
    changes = dep.diff(before, after)
    looked = asyncio.run(stub.facts([(c.ecosystem, c.name, c.after[-1]) for c in changes]))
    facts = dep.facts_for(changes, looked, DependencyPolicy(), datetime.now(timezone.utc))
    ids = {f.id: f.severity for f in dep.findings(facts)}
    assert ids == {
        "dependency-malicious": "blocker", "dependency-vulnerable": "blocker",
        "dependency-license": "blocker", "dependency-license-unknown": "major",
        "dependency-too-new": "major", "dependency-unchecked": "major",
    }, "a kind of bad dependency was missed, or a good one reported"


def test_the_project_s_own_dependencies_are_read_at_baseline_and_new_advisories_marked(tmp_path):
    import asyncio

    from factory import dependencies as dep
    from factory.dependencies import Facts
    from factory.schemas import DependencyFact, DependencyPolicy

    repo = _repo_with_one_commit(tmp_path / "repo", {"requirements.txt": "click==8.1.7\n"})
    before = [DependencyFact(ecosystem="PyPI", name="click", after=["8.1.7"], checked=True)]
    stub = _StubLookup({"click": Facts(True, "BSD-3-Clause", advisories=["GHSA-new"])})
    facts, note = asyncio.run(dep.project_inventory(repo, "HEAD", DependencyPolicy(), stub, before))
    assert [(f.name, f.verdicts) for f in facts] == [("click", ["new-advisory"])]
    assert stub.asked == [("PyPI", "click", "8.1.7")]

    src = class_source(__import__("factory.onboarding", fromlist=["x"]).ProjectOnboarding)
    assert src.count("dependencies.refresh_inventory(project, self.dependency_lookup)") == 2, \
        "a baseline is taken without reading what the project depends on"
    assert "dependencies.refresh_inventory(self.project" in inspect.getsource(
        pipeline.Factory._converge), "a build starts without looking again"


def test_a_dependency_reading_that_fails_never_takes_its_caller_down(tmp_path):
    import asyncio

    from factory import dependencies as dep

    class Boom:
        async def facts(self, packages, dates=True):
            raise RuntimeError("network on fire")

    registry, project = _project_with_suggestions(tmp_path)
    (project.repo_path / "requirements.txt").write_text("click==8.1.7\n")
    subprocess_run = __import__("subprocess").run
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"]):
        subprocess_run(cmd, cwd=project.repo_path, check=True)
    asyncio.run(dep.refresh_inventory(project, Boom()))
    latest = dep.latest_inventory(project)
    assert latest is not None and "network on fire" in latest["note"]


# -- a worker measured during its own turn, and sent back -----------------------


def test_a_test_that_cannot_fail_is_named_for_what_it_is():
    from factory.testquality import hollow_tests

    python = (
        "import pytest\n"
        "def test_calls():\n    run()\n"
        "def test_constant():\n    assert True\n"
        "def test_self():\n    x = run()\n    assert x == x\n"
        "@pytest.mark.skip\ndef test_off():\n    assert run() == 2\n"
        "def test_raises():\n    with pytest.raises(ValueError):\n        run()\n"
        "def test_swallow():\n    try:\n        run()\n    except Exception:\n        pass\n"
        "    assert done()\n"
        "def test_real():\n    assert run() == 2\n"
    )
    found = " | ".join(hollow_tests("tests/test_a.py", python))
    for name in ("test_calls", "test_constant", "test_self", "test_off", "test_swallow"):
        assert name in found, f"{name} was not named"
    for name in ("test_raises", "test_real"):
        assert name not in found, f"{name} checks something and was named anyway"

    script = ("it('runs', () => { run(); });\n"
              "test.skip('off', () => { expect(run()).toBe(1); });\n"
              "it('constant', () => { expect(true).toBe(true); });\n"
              "it('real', () => { expect(run()).toBe(2); });\n")
    found = " | ".join(hollow_tests("web/a.test.ts", script))
    assert "'runs'" in found and "'off'" in found and "'constant'" in found
    assert "'real'" not in found
    assert hollow_tests("a_test.go", "func TestX(t *testing.T) {}") == [], \
        "a language this does not read was reported on as if it had been read"


def test_each_part_of_a_reading_is_ruled_on_by_itself(tmp_path):
    from factory.projects import proposed_parts

    registry, project, diff = _project_with_parts(tmp_path)
    assert proposed_parts(project, diff) == ["test_file_commands", "trace_dirs", "preview"]
    project = registry.rule_on_proposed_check(project, "test_file_commands", accept=True)
    assert project.state.test_file_commands[0].command == "pytest -q {path}"
    assert project.state.trace_dirs == [], "accepting one part applied another"
    _, ruled = registry.proposal_state(project)
    assert set(ruled) == {"test_file_commands"}

    project = registry.rule_on_proposed_check(project, "trace_dirs", accept=False, reason="no")
    assert project.state.trace_dirs == []
    assert not project.state.survey_sha, "the reading was called current with a part still waiting"
    project = registry.rule_on_proposed_check(project, "preview", accept=True)
    assert project.state.environment.preview.open == "web"
    assert project.state.survey_sha, "every part ruled on, and the reading still not current"


def test_an_accepted_part_can_be_undone_and_waits_again(tmp_path):
    from factory.projects import ProjectError, proposed_parts

    registry, project, diff = _project_with_parts(tmp_path)
    project = registry.rule_on_proposed_check(project, "test_file_commands", accept=True)
    project = registry.undo_proposed_check(project, "test_file_commands")
    assert project.state.test_file_commands[0].command == "pytest {path}"
    _, ruled = registry.proposal_state(project)
    assert "test_file_commands" not in ruled and "test_file_commands" in proposed_parts(project, diff)

    project = registry.rule_on_proposed_check(project, "preview", accept=True)
    with pytest.raises(ProjectError):
        registry.undo_proposed_check(project, "preview")


def test_a_part_restated_as_recorded_is_not_proposed(tmp_path):
    from factory.projects import proposed_parts

    registry, project, diff = _project_with_parts(tmp_path)
    diff.test_file_commands = list(project.state.test_file_commands)
    assert "test_file_commands" not in proposed_parts(project, diff)


def test_the_survey_tab_s_old_ruling_settles_every_part(tmp_path):
    registry, project, diff = _project_with_parts(tmp_path)
    project, _ = registry.apply_survey_diff(project, diff, ["trace_dirs"], checks=False)
    _, ruled = registry.proposal_state(project)
    assert ruled["trace_dirs"]["decision"] == "applied"
    assert ruled["test_file_commands"]["decision"] == "rejected"


def test_a_resurvey_reads_each_checks_settings_whether_or_not_they_moved(tmp_path):
    """A re-reading is shown the tooling that moved, so a linter config that had
    not moved was never in front of it: it restated an accessibility suggestion
    for rules the linter already loaded, and left `also` empty."""
    from factory.onboarding import _check_settings
    from factory.schemas import Gate

    registry, repo = _dockerfile_project(tmp_path)
    (repo / "api").mkdir()
    (repo / "api" / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[tool.ruff.lint]\nselect = ["E", "S"]\n')
    (repo / "web").mkdir()
    (repo / "web" / "eslint.config.js").write_text("import jsxA11y from 'eslint-plugin-jsx-a11y';\n")
    project = registry.get("demo")
    project.state.gates = [
        Gate(name="api-lint", command="ruff check .", family="structure",
             config_files=["api/pyproject.toml#tool.ruff", "api/pyproject.toml#tool.ruff.lint"]),
        Gate(name="web-lint", command="eslint .", family="structure",
             config_files=["web/eslint.config.js"]),
        Gate(name="gone", command="x", config_files=["nowhere.toml"])]
    shown = _check_settings(project)
    assert '"S"' in shown and "jsx-a11y" in shown
    assert '"name"' not in shown, "the whole file shown, not the check's section of it"
    assert shown.count("tool.ruff.lint") == 0, "a section shown twice, inside the one that holds it"
    assert "(not in the repository)" in shown

    source = factory_source("onboarding")
    assert "# Each check's settings, as they read now" in source
    assert "_check_settings(project)" in source
    assert "drop it, do not restate it" in source


def test_the_package_list_is_measured_not_read_from_a_manifest():
    """A manifest says what someone intended to install."""
    src = factory_source("onboarding")
    assert "_probe_packages" in src
    assert "project.state.runtime_packages = await self._probe_packages" in src, \
        "the probe runs but its answer is not kept"
    # After setup, before the gates: the only moment that environment exists.
    setup_at = src.index("run_setup(environment.setup")
    probe_at = src.index("await self._probe_packages(sandbox.path")
    gates_at = src.index("run_gates(project.state.gates")
    assert setup_at < probe_at < gates_at, \
        "the environment is probed before it has been set up, or after it is gone"

    from factory.config import Config
    probes = Config().pipeline.package_probes
    assert any("pip list" in p for p in probes)


def test_a_probe_that_runs_before_setup_finds_nothing_and_that_is_the_point():
    """Ordering is the whole correctness of this.

    The compose runner gives each command its own container; what makes the
    installed packages visible is that setup's session is committed first. Probe
    before setup and the answer is the base image, which is a true answer to the
    wrong question -- and it is silent, because an empty list looks like a
    project with no dependencies.
    """
    src = factory_source("onboarding")
    setup_at = src.index("run_setup(environment.setup")
    probe_at = src.index("await self._probe_packages(sandbox.path")
    assert setup_at < probe_at, "the environment is probed before it is set up"


def test_the_resurvey_can_see_whether_the_load_command_is_already_answered():
    """A reading that cannot see a field will propose it for ever.

    The per-file rules were rendered into the re-survey prompt as
    `match -> command` and nothing else, so a rule that already carried a
    `collect` looked identical to one that never had. The consequence is not a
    wrong answer, it is an endless one: the same change proposed on every run,
    the same box ticked by the same human, with no way for either to tell an
    answered question from an unasked one.
    """
    src = inspect.getsource(onboarding.ProjectOnboarding.run_resurvey)
    assert "r.collect" in src, "the reading is asked about a field it is not shown"
    assert "How this project runs a single test file" in src


def test_a_request_a_human_has_answered_is_not_asked_again_forever(tmp_path):
    """The loop under the loop.

    The verify lane's requests live in append-only feature ledgers, so nothing
    could ever take one off the list. A project answered `a browser-level
    runner` by putting Playwright in the repository and was handed the identical
    request at the next reading, and the one after, forever -- along with two
    that no file could ever close. Every reading was then obliged to propose
    something about four asks that had already been dealt with, which is what a
    human correctly read as being stuck in a permanent loop.

    A ruling retires exactly what that reading was shown. A new feature hitting
    the same wall raises it again, which is the one case worth hearing twice:
    it means the answer did not work.
    """
    import types
    from factory.projects import ProjectRegistry, unmet_capabilities
    from factory.schemas import CapabilityRequest, CapabilityRuling, SurveyDiff

    def store_for(fid):
        return types.SimpleNamespace(records=lambda: [
            {"kind": "oracle", "payload": {"requires": [
                {"need": "a browser-level runner", "criterion_ids": ["AC-23"]}]}}])

    def project_with(rulings, features):
        return types.SimpleNamespace(
            feature_ids=lambda: features, feature_store=store_for,
            state=types.SimpleNamespace(capability_rulings=rulings))

    asked = unmet_capabilities(project_with([], ["alpha"]))
    assert len(asked) == 1, "the request is not being read at all, so this proves nothing"

    ruled = [CapabilityRuling(need="A Browser-Level Runner", features=["alpha"],
                              decision="applied", at="now")]
    assert unmet_capabilities(project_with(ruled, ["alpha"])) == [], (
        "an answered request is handed to the next reading anyway -- the loop")

    # A second feature hitting the same wall is new information.
    assert len(unmet_capabilities(project_with(ruled, ["alpha", "beta"]))) == 1, (
        "a ruling silenced a request for features that never raised it")

    # Rejection retires it too: "we heard this and said no" is an answer, and a
    # reading that keeps proposing against it costs money to be told nothing.
    turned_down = [CapabilityRuling(need="a browser-level runner", features=["alpha"],
                                    decision="rejected", at="now")]
    assert unmet_capabilities(project_with(turned_down, ["alpha"])) == [], (
        "turning a proposal down leaves its request outstanding forever")

    # The retiring happens on the human's ruling, against what that reading was
    # shown -- never recomputed, or a request raised by a feature that finished
    # in between is marked answered by a human who never saw it.
    repo = tmp_path / "repo"
    repo.mkdir()
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")
    diff = SurveyDiff(summary="s", considered=[
        CapabilityRequest(need="a browser-level runner", features=["alpha"])])
    project.store.append("resurvey", diff, role="resurvey")
    updated, _ = registry.apply_survey_diff(project, diff, [])
    assert [r.need for r in updated.state.capability_rulings] == ["a browser-level runner"]
    assert updated.state.capability_rulings[0].features == ["alpha"]
    assert updated.state.capability_rulings[0].decision == "rejected", \
        "nothing accepted is a rejection, and must be recorded as one"


def test_a_proposal_that_changes_an_existing_file_can_be_accepted(tmp_path):
    """Scaffolding only ever added files, and a reading had no way to say
    "change three lines of this one" except by proposing the whole file. So the
    proposal that registers a project's own factories with pytest -- one line,
    `pytest_plugins = ("factories",)`, without which every factory in the
    repository is unreachable by name and the blind author is told the project
    has no way to arrange state -- could be accepted forever and never land.
    The page named the file and shrugged.

    Refusing to overwrite was right as a default and wrong as an absolute. It is
    still never silent and never bulk: a path is written over only by being
    named in `replace`, one at a time, from a card that shows the diff first.
    """
    from factory.projects import ProjectError, ProjectRegistry
    from factory.schemas import EnvironmentSpec, ProjectSurvey, ScaffoldFile

    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "conftest.py").write_text("# what the human has\n")

    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")
    proposed = ScaffoldFile(path="backend/conftest.py",
                            contents='# what the human has\npytest_plugins = ("factories",)\n',
                            purpose="registers the factories")
    project.state.survey = ProjectSurvey(
        name="demo", summary="s", environment=EnvironmentSpec(kind="host"),
        scaffolding=[proposed])
    registry.save(project)

    # The default is unchanged: what is there is left alone.
    result = registry.apply_scaffolding(project, ["backend/conftest.py"])
    assert result["written"] == [], "an existing file was overwritten without being named"
    assert "already exists" in " ".join(result["skipped"])
    assert (repo / "backend" / "conftest.py").read_text() == "# what the human has\n"

    # Named, it lands.
    result = registry.apply_scaffolding(
        project, ["backend/conftest.py"], replace=["backend/conftest.py"])
    assert result["written"] == ["backend/conftest.py"], result
    assert 'pytest_plugins = ("factories",)' in (repo / "backend" / "conftest.py").read_text()

    # Writing the same bytes again is not a change, and committing one makes a
    # diff a reader has to open to find out is empty.
    again = registry.apply_scaffolding(
        project, ["backend/conftest.py"], replace=["backend/conftest.py"])
    assert again["written"] == [], "an identical file was rewritten and committed"

    # `replace` cannot reach past what is being written.
    with pytest.raises(ProjectError) as caught:
        registry.apply_scaffolding(project, ["backend/conftest.py"], replace=["backend/app.py"])
    assert "not being written" in str(caught.value)

    # A file already exactly as proposed is not offered again. `scaffolding_applied`
    # says a file was written once and cannot say whether what is on disk now is
    # what is being proposed -- so a file already replaced drew the same card as
    # one still needing it, and pressing it wrote nothing, correctly, which from
    # the outside is a button that does not work.
    from factory.projects import scaffolding_state
    assert scaffolding_state(project)["backend/conftest.py"] == "same", (
        "the server cannot tell an applied file from one still needing the change")
    (repo / "backend" / "conftest.py").write_text("something else\n")
    assert scaffolding_state(project)["backend/conftest.py"] == "differs"
    (repo / "backend" / "conftest.py").unlink()
    assert scaffolding_state(project)["backend/conftest.py"] == "missing"

    # And the screen offers the diff before the overwrite, never the other way.
    app = app_js()
    assert "st[f.path] === 'differs'" in app, (
        "the replace card is drawn from whether a file was ever written, not from "
        "whether what is there now differs from what is proposed")
    assert "data-scaffold-diff" in app and "data-replace-scaffold" in app, (
        "the console has no way to accept a change to a file that already exists")
    # Sliced on the verb the card is built with rather than on a written label.
    # The label used to be the prose "replaces a file you have"; every proposal
    # is drawn by one primitive now, and its verb is the stable part.
    card = app.split("verb: 'replace file'", 1)[1].split("})).join('')", 1)[0]
    assert "Show what changes" in card, "a file is offered for overwrite with no diff to read"
    assert card.index("data-scaffold-diff") < card.index("data-replace-scaffold"), (
        "the button that overwrites comes before the one that shows what it would do")


def test_the_page_never_says_a_check_is_unrun_about_something_that_is_not_a_check():
    """It stood under a nameplate reading CERTIFIED · 5 · all green and said
    "environment · test_file_commands · blind_placements · testing were accepted
    and have not been run yet: run the checks, then approve."

    `applied` holds two kinds of entry: gate changes as `add:name`, and whole
    fields accepted by name. The strip read all of them as check names -- and
    none of those four can ever appear in a baseline result, because none of
    them is a check. So they were permanently unrun and the sentence stood
    forever, telling a reader the opposite of what the board above it said.

    A removed check is excluded for a different reason: it will never be run
    again because it is gone. The filter looked for `drop:` and the action word
    is `remove`, so it never matched.
    """
    app = app_js()
    block = app.split("const unrun = (ruled.applied || [])", 1)[1].split(";", 1)[0]
    assert "a.includes(':')" in block, (
        "whole fields accepted by name are still read as check names")
    assert "remove:" in block, "a removed check is still listed as waiting to be run"

    # The four field names are exactly the ones `apply_survey_diff` appends bare.
    projects = factory_source("projects")
    applied = projects.split("def apply_survey_diff(", 1)[1].split("\n    def ", 1)[0]
    for field in ("environment", "test_file_commands", "blind_placements", "testing"):
        assert f'applied.append("{field}")' in applied, (
            f"`{field}` is no longer appended bare -- the console's filter assumes it is")


def test_a_later_reading_can_change_what_a_first_reading_recorded():
    """A first reading of a repository answers with `ProjectSurvey`. Every
    later one answers with `SurveyDiff`, a different model filled in by a role
    that shares the *same brief* on purpose -- `prompt: surveyor`, because the
    two were separate files until a rule was corrected in one and not the
    other and a `user` tier stayed `usable` on jsdom for a whole reading.

    Sharing the brief does not share the schema, and that is the gap this
    closes. `trace_dirs` was added to the survey, to the project and to the
    collector, and not to the diff -- so a re-read was asked for the directory
    by a brief it did read, had nowhere to put the answer, and recorded
    nothing. The field worked on a project surveyed for the first time and was
    unreachable on every project that already existed, which is all of them.

    `gates` is the exception and has its own shape, `gate_changes`, because a
    reading proposes adding and removing one rather than replacing the list.
    `name` and `base_ref` are not proposed again.
    """
    from factory.projects import ProjectRegistry
    from factory.schemas import ProjectState, ProjectSurvey, SurveyDiff

    survey, diff = set(ProjectSurvey.model_fields), set(SurveyDiff.model_fields)
    state = set(ProjectState.model_fields)
    # A field a reading records onto the project is a field a later reading has
    # to be able to change.
    exempt = {"gates", "name", "base_ref"}
    missing = sorted((survey & state) - diff - exempt)
    assert not missing, (
        f"a later reading cannot change {missing} -- the field works on a project surveyed "
        "for the first time and is unreachable on every project that already exists")
    assert "gate_changes" in diff, "the exception, and it has its own shape"

    # And a proposal that is accepted has to land. The diff's own fields are
    # applied by name, so one with no branch in `apply_survey_diff` is a card a
    # human can tick that does nothing.
    applied = inspect.getsource(ProjectRegistry.apply_survey_diff)
    for field in sorted((survey & state & diff) - exempt):
        assert f'changes["{field}"]' in applied, f"accepting {field} writes nothing"
        if field == "environment":
            # Its own consent, not a tick in the accepted list: replacing how
            # this project's tests are stood up is a larger thing to agree to
            # than replacing a list of paths, so it has a flag of its own.
            assert "if environment and diff.environment is not None" in applied
            continue
        assert f'"{field}" in wanted' in applied, f"accepting {field} changes nothing"


def test_a_rereading_that_changes_any_field_of_a_check_is_not_dropped_as_identical():
    """A kanban re-reading proposed a new field for four checks, and all four
    were dropped as identical to the checks on record: the comparison was a
    hand-kept list of fields that predated the one being changed."""
    from factory.onboarding import _same_gate
    from factory.schemas import Gate

    current = Gate(name="api-tests", command="pytest -q --ignore=tests/acceptance")
    assert _same_gate(current.model_copy(), current)
    for field, value in (("command", "pytest -q"), ("timeout_s", 1.0),
                         ("optional", True), ("parse_metric", "(\\d+)")):
        proposed = current.model_copy(update={field: value})
        assert not _same_gate(proposed, current), field


def test_a_turn_says_which_checks_it_ran_and_not_only_how_many():
    """The call tree's row said `6/19 passed` and that was the whole of it.
    Which six was a question only the raw JSONL could answer -- the gates
    payload is 73KB of command output and never travelled with the log."""
    records = [
        _work(1, "gates", "2026-01-01T10:05:00+00:00", {"results": [
            _check("api-lint"),
            _check("web-tests", passed=False, exit_code=1, failed_on="blind_tests",
                   summary_known=True, tests_total=7, tests_failed=2),
            _check("web-e2e", passed=False, started=False),
        ]}),
        _turn(2, "gates", "2026-01-01T10:00:00+00:00", "2026-01-01T10:06:00+00:00",
              detail="1/3 passed"),
    ]

    readout = server.readouts_for(records)[2]

    [section] = readout
    assert section["note"].startswith("1 of 3 passed")
    outcomes = {e["text"]: e["outcome"] for e in section["entries"]}
    assert outcomes == {"api-lint": "passed", "web-tests": "failed",
                        "web-e2e": "could not run"}, (
        "a check that never executed did not fail, and a row saying both "
        "contradicts itself -- the words come from `gates`, not from a second "
        "definition here")
    web = next(e for e in section["entries"] if e["text"] == "web-tests")
    assert "blind tests are set aside" in web["note"], (
        "which files a red check belongs to is what makes it readable")
    assert "2 of 7 test files failed" in web["note"], (
        "counted file by file, never read out of the runner's console text")


def test_a_probe_the_code_survived_is_never_reported_as_a_pass():
    """A breaker probe is worth something *because* it fails. Drawn with the
    green tick every other list on this screen uses, a suite the code shrugged
    off would read as evidence that the code is sound, which is the one thing
    it cannot be."""
    records = [
        _work(1, "breaker_suite", "2026-01-01T10:01:00+00:00", {"strategy": "encoded paths",
              "tests": [{"path": "a_test.py", "contents": "assert False",
                         "hypothesis": "slashes cannot be removed"},
                        {"path": "b_test.py", "contents": "assert True",
                         "hypothesis": "unicode truncates"}]}, role="breaker"),
        _work(2, "breaker", "2026-01-01T10:02:00+00:00",
              {"ran": True, "failing": ["a_test.py"], "rejected": []}),
        _turn(3, "breaker", "2026-01-01T10:00:00+00:00", "2026-01-01T10:03:00+00:00"),
    ]

    [section] = server.readouts_for(records)[3]

    outcomes = {e["text"]: e["outcome"] for e in section["entries"]}
    assert outcomes == {"a_test.py": "the code failed it",
                        "b_test.py": "the code survived it"}
    assert "proves nothing" in section["caveat"], (
        "the limit is stated beside the list, not left for the reader to know")
    assert all(e.get("tone") != "good" for e in section["entries"]), (
        "no probe in this list is good news about the code")


def test_the_repository_before_this_work_is_read_from_the_base_commit(tmp_path):
    """A resume began before the oracle fixed its conftest, and the digest of
    "the repository as it was before this work" was read from the sandbox --
    which by then held the feature's own files. Three review panels found the
    unfixed conftest there, beside a ledger saying it was fixed, and the
    packet's first blocker said the repair had been faked. It had not."""
    from types import SimpleNamespace

    repo, sandbox, _ = _resumed_sandbox(tmp_path)
    fake = SimpleNamespace(project=SimpleNamespace(
        repo_path=str(repo), state=SimpleNamespace(digest_budget=120_000)))
    state = SimpleNamespace(sandbox=sandbox.state, feature_id="card-labels")

    digest = pipeline.Factory._digest_before(fake, state, sandbox)

    assert "api/app.py" in digest, "the base is there"
    assert "card_labels" not in digest and "DROP SCHEMA" not in digest, \
        "and nothing this feature wrote, on any attempt"
    assert digest.startswith("# Repository: kanban"), \
        "named for the project, not for the checkout's directory"


def test_a_re_reading_with_no_suggestions_leaves_none(tmp_path):
    """A reading writes its suggestions fresh every time, so the newest one's
    list is the list -- even when it is empty. Read as "no newer reading", an
    empty list brought back the first survey's three, one of them asking for
    the browser suite to be run after `web-e2e` had become a check."""
    import subprocess

    from factory.projects import ProjectRegistry
    from factory.schemas import ProjectSurvey, Recommendation

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "p")
    project.state.survey = ProjectSurvey.model_construct(recommendations=[
        Recommendation.model_construct(title="Run the Playwright suite in CI", kind="ci",
                                       why="", evidence="", how="",
                                       would_gate="cd web && npm run test:e2e")])
    assert [r.title for r in registry.live_recommendations(project)] == [
        "Run the Playwright suite in CI"]
    project.store.append("resurvey", {"recommendations": []}, role="surveyor")
    assert registry.live_recommendations(project) == []

    src = factory_source("onboarding")
    assert "# Suggestions on record now" in src
    prompt = " ".join((ROOT / "factory" / "roles" / "surveyor.md").read_text().split())
    assert "`recommendations` replaces the suggestions on record" in prompt
    assert "A testing reading whose levels carry no `run_by` is a `change`" in prompt


def test_a_reading_that_fills_in_which_checks_run_a_level_is_not_dropped():
    """The filter that drops no-op proposals compared a testing reading on the
    verdict and the runner alone. A resurvey that filled in `run_by` for all
    three levels was dropped as identical, and its summary -- explaining that
    nothing said which check ran each level -- arrived beside no change."""
    from types import SimpleNamespace

    from factory.onboarding import strip_unchanged
    from factory.schemas import SurveyDiff, TestingSurface, TestingTier

    def surface(run_by):
        return TestingSurface(tiers=[
            TestingTier(tier=t, runner="pytest 8", verdict="usable", run_by=list(run_by))
            for t in ("unit", "integration", "user")])

    state = SimpleNamespace(
        testing=surface([]), gates=[], blind_placements=[], test_file_commands=[],
        scaffolding=[], environment=None, survey=None)
    diff = SurveyDiff(summary="", testing=surface(["api-tests"]))
    dropped = strip_unchanged(diff, state)
    assert diff.testing is not None, "the one thing it proposed was thrown away"
    assert not dropped

    diff = SurveyDiff(summary="", testing=surface([]))
    assert strip_unchanged(diff, state) and diff.testing is None


def test_the_tier_comparison_has_no_blind_spot():
    """Three readings in a row were dropped as identical because the
    comparison had never heard of a field added later: a check's ci_command,
    then run_by, then a setup file a level had gained. Each time the card said
    nothing had changed about the only thing that had. Every field of a level
    is compared unless it is named as prose, and a new one is compared until
    somebody says otherwise."""
    from factory.onboarding import TIER_PROSE, strip_unchanged, tier_shape
    from factory.schemas import SetupFile, SurveyDiff, TestingSurface, TestingTier

    # Plus one derived key: `cleanup`'s wording is prose, but whether it has been
    # answered at all is compared (see tier_shape).
    compared = (set(TestingTier.model_fields) - TIER_PROSE) | {"cleanup_state"}
    assert set(tier_shape(TestingTier(tier="unit", verdict="usable"))) == compared

    def surface(**over):
        fields = {"tier": "unit", "runner": "vitest 2 with jsdom", "verdict": "usable", **over}
        return TestingSurface(tiers=[TestingTier(**fields)])

    state = types.SimpleNamespace(
        testing=surface(), gates=[], blind_placements=[], test_file_commands=[],
        scaffolding=[], environment=None, survey=None)

    # A setup file the level gained: the reading that proposed exactly this was
    # dropped, and a blind test author was never told the helper existed.
    gained = surface(setup_files=[SetupFile(path="web/src/testing/http.ts", contents="x")])
    diff = SurveyDiff(summary="", testing=gained)
    assert not strip_unchanged(diff, state) and diff.testing is not None

    # Prose rewritten in the same words: still nothing to rule on.
    reworded = surface(note="said differently", runner="vitest (web/vite.config.ts), jsdom")
    diff = SurveyDiff(summary="", testing=reworded)
    assert strip_unchanged(diff, state) and diff.testing is None


# ==========================================================================
# a gap comes with fixes a person can tick
# ==========================================================================


def test_a_tier_says_who_cleans_up_and_offers_fixes_when_nobody_reliably_does():
    """Kanban's browser tier was read as "each spec must undo its own changes"
    -- recorded, and the card looked settled while the gap stayed open: nothing
    catches the spec that forgets. Who resets the data is what decides whether a
    person must act, and when they must, the reading offers the fixes -- through
    channels that already apply with a tick -- instead of leaving them to work
    out what to do."""
    from factory import onboarding
    from factory.schemas import TestingSurface, TestingTier

    fields = TestingTier.model_fields
    assert "cleanup_by" in fields and "cleanup_options" in fields
    assert "cleanup_options" in onboarding.TIER_PROSE and "cleanup_by" not in onboarding.TIER_PROSE, \
        "who cleans up is a decision, compared; the options are the reading's words"
    shape = onboarding.tier_shape
    by = lambda who: TestingTier(tier="user", verdict="usable", cleanup="x", cleanup_by=who)  # noqa: E731
    assert shape(by("each_test")) != shape(by("harness"))

    surveyor = (ROOT / "factory" / "roles" / "surveyor.md").read_text()
    for said in ("`each_test` and `nobody` come with options, in `cleanup_options`",
                 "Never propose that this factory build it as a feature",
                 "a script nothing runs fixes nothing",
                 "Only if it works on this repository today",
                 "makes an *unfaked* call fail the test",
                 "Say what leaks, not how the mechanism works"):
        assert said in surveyor, f"the surveyor is no longer told: {said}"

    # Answered `cleanup` without `cleanup_by` is still an unasked question.
    half = TestingTier(tier="user", verdict="usable", cleanup="specs put things back")
    assert "`user`" in onboarding._unasked_cleanup(TestingSurface(tiers=[half]))


def test_a_test_helper_in_a_support_folder_is_setup_and_an_unwritable_fix_is_never_offered():
    """The recommended fix -- lift the workspace helper into
    `web/e2e/support/workspace.ts` -- was refused at apply as "scaffolding may
    not write tests", for sitting under `e2e/`. No runner collects that file;
    the rule exists for files a runner counts as a passing test. And the option
    had been offered at all, because the check that decides what to offer did
    not hold it to the rule the write is held to."""
    from factory.projects import ProjectRegistry, cleanup_option_problems
    from factory.schemas import CleanupOption, ScaffoldFile, SurveyDiff
    from factory.workspace import is_setup_path

    for setup in ("web/e2e/support/workspace.ts", "api/tests/helpers/api.py",
                  "tests/fixtures/board.py", "api/tests/conftest.py"):
        assert is_setup_path(setup), f"{setup} is a helper, not a test"
    for test in ("web/e2e/support/login.spec.ts", "web/src/__tests__/helpers/x.ts",
                 "api/tests/test_boards.py", "web/e2e/acceptance/drag.ts"):
        assert not is_setup_path(test), f"{test} is something a runner collects"

    helper = ScaffoldFile(path="web/e2e/support/workspace.ts", contents="export {}", purpose="p")
    spec = ScaffoldFile(path="web/e2e/cleanup.spec.ts", contents="test()", purpose="p")
    assert ProjectRegistry.scaffold_problems([helper]) == []
    diff = SurveyDiff(summary="s", scaffolding=[helper, spec])
    ok = CleanupOption(title="Share it", summary="s", files=[helper.path])
    bad = CleanupOption(title="A test that cleans", summary="s", files=[spec.path])
    assert cleanup_option_problems(diff, ok) == ""
    assert "may not write tests" in cleanup_option_problems(diff, bad), \
        "an option the write would refuse is offered to a person"



def test_a_chosen_fix_is_applied_whole_files_first(tmp_path, monkeypatch):
    """A person picks the fix; the order is the factory's. Its file lands first,
    because its environment step runs that file and would fail preparation
    without it; then the reading and the environment. An option missing one of
    its parts never reaches the card."""
    from factory import onboarding as ob
    from factory.projects import ProjectError, chosen_cleanup, cleanup_option_problems
    from factory.schemas import (CleanupOption, EnvironmentSpec, ScaffoldFile, SurveyDiff,
                                 TestingSurface, TestingTier)

    reset = ScaffoldFile(path="api/scripts/reset.py", contents="print('reset')", purpose="p")
    whole = CleanupOption(title="Reset the data between runs", summary="s",
                          files=[reset.path], environment=True, recommended=True)
    half = CleanupOption(title="Reset, but the script is missing", summary="s",
                         files=["api/scripts/nowhere.py"], environment=True)
    env = EnvironmentSpec(kind="compose", compose_file="c.yml", compose_service="api",
                          test_prepare=["cd api && python scripts/reset.py"])
    diff = SurveyDiff(summary="s", environment=env, environment_reason="r", scaffolding=[reset],
                      testing=TestingSurface(tiers=[TestingTier(
                          tier="user", verdict="usable", cleanup="x", cleanup_by="each_test",
                          cleanup_options=[whole, half])]))
    assert cleanup_option_problems(diff, whole) == ""
    assert "does not propose" in cleanup_option_problems(diff, half)
    dropped = ob.strip_unchanged(diff.model_copy(deep=True), types.SimpleNamespace(
        testing=TestingSurface(), gates=[], blind_placements=[], test_file_commands=[],
        scaffolding=[], environment=None, survey=None, trace_dirs=[]))
    assert any("script is missing" in d for d in dropped), "half a fix reached the card"
    assert [o.title for _, o in chosen_cleanup(diff, {"user": 0})] == [whole.title]
    assert chosen_cleanup(diff, {"user": None}) == []
    with pytest.raises(ProjectError):
        chosen_cleanup(diff, {"user": 7})

    src = factory_source("server")
    apply = src.split("def apply_proposal(")[1].split("@app.post(")[0]
    assert apply.index("registry.apply_scaffolding(") < apply.index("registry.apply_survey_diff("), \
        "the environment is applied before the file it runs exists"
    assert "create_feature" not in apply, \
        "applying a fix starts a factory feature; a code fix is a prompt for the person's agent"
    assert "any(o.environment for _, o in chosen)" in apply

    # A code fix is a prompt, not something to apply.
    prompt = CleanupOption(title="Give each browser test its own workspace", summary="s",
                           agent_prompt="Let a browser test create its own workspace")
    from factory.schemas import Recommendation
    coded = diff.model_copy(deep=True)
    coded.recommendations = [Recommendation(title=prompt.agent_prompt, kind="tests", why="w",
                                            evidence="e")]
    coded.testing.tiers[0].cleanup_options = [prompt]
    assert cleanup_option_problems(coded, prompt) == ""
    with pytest.raises(ProjectError, match="coding agent"):
        chosen_cleanup(coded, {"user": 0})


def test_a_check_that_fails_offline_for_want_of_a_fetch_is_warmed_and_measured_again(tmp_path):
    """The Library case, end to end. Setup skipped the tests, so the offline check
    died on `Unknown host`. The baseline runs it once with the network, as
    setup, resets the stack it wrote into, and measures it offline again: green,
    marked `warmed`, and remembered, so every later setup does the same."""
    runner = _LazyFetchRunner()
    onboarding, project = _warm_project(tmp_path, runner)
    report, _ = asyncio.run(onboarding._baseline(project))

    by_name = {r.name: r for r in report.results}
    assert by_name["api-verify"].passed and by_name["api-verify"].warmed
    assert not by_name["web-test"].warmed, "only what the warm-up changed is marked"
    assert project.state.warm_checks == ["api-verify"]
    assert by_name["setup[warm:api-verify]"].command == "mvn verify"
    offline = [e for e in runner.log if e == "offline:mvn verify"]
    assert len(offline) == 2 and "reset_stack" in runner.log, \
        "measured offline again, on a stack the warm-up's rows were cleared from"
    assert runner.log.index("reset_stack") < len(runner.log) - runner.log[::-1].index(
        "offline:mvn verify") - 1

    # And the next setup warms it up front: no failure to detect, no second pass.
    again = _LazyFetchRunner()
    onboarding.runner = again
    report, _ = asyncio.run(onboarding._baseline(project))
    assert again.log.count("offline:mvn verify") == 1
    assert again.log.index("online:mvn verify") < again.log.index("offline:mvn verify")
    assert {r.name: r for r in report.results}["api-verify"].passed


def test_a_check_red_for_its_own_reasons_is_not_remembered_as_needing_the_network(tmp_path):
    """A warm-up that changes nothing proves nothing, and the first result
    stands. Nothing is added to every future setup on a guess."""
    runner = _LazyFetchRunner(fetch_fixes=False)
    onboarding, project = _warm_project(tmp_path, runner)
    report, _ = asyncio.run(onboarding._baseline(project))
    result = {r.name: r for r in report.results}["api-verify"]
    assert not result.passed and not result.warmed
    assert "Unknown host" in result.output_tail, "the first, real measurement is kept"
    assert project.state.warm_checks == []


def test_the_repositorys_committed_dockerfile_is_the_one_used_and_never_stored(tmp_path):
    """Committed at the branch features start from wins over the record's copy,
    is read on every load, and never written back into the record -- a second
    copy that would drift. Uncommitted is not used, as for code; and a Dockerfile
    that changed since the checks ran is said to have."""
    from factory.projects import REPO_DOCKERFILE, dockerfile_state
    from factory.schemas import GateReport

    registry, repo = _dockerfile_project(tmp_path, "FROM python:3.12\n")
    project = registry.get("demo")
    assert project.dockerfile_source == "fabrika"
    assert project.environment.dockerfile == "FROM python:3.12\n"

    (repo / ".fabrika").mkdir()
    (repo / REPO_DOCKERFILE).write_text("FROM python:3.13\n")
    assert registry.get("demo").dockerfile_source == "fabrika", "uncommitted is not used"
    assert dockerfile_state(registry.get("demo"))["uncommitted"]

    _commit(repo, "add the dockerfile")
    project = registry.get("demo")
    assert project.dockerfile_source == "repo"
    assert project.environment.dockerfile == "FROM python:3.13\n"
    registry.record_baseline(project, GateReport(results=[]))
    stored = project.store.payload("project")["environment"]["dockerfile"]
    assert stored == "FROM python:3.12\n", "the repository's text was written into the record"
    assert not dockerfile_state(registry.get("demo"))["unmeasured"]

    (repo / REPO_DOCKERFILE).write_text("FROM python:3.14\n")
    _commit(repo, "a newer python")
    state = dockerfile_state(registry.get("demo"))
    assert state["source"] == "repo" and state["unmeasured"] and not state["uncommitted"]


def test_moving_the_dockerfile_in_writes_and_commits_it_and_never_overwrites_your_edit(tmp_path):
    import subprocess
    from factory.projects import REPO_DOCKERFILE, ProjectError

    registry, repo = _dockerfile_project(tmp_path)
    (repo / ".fabrika").mkdir()
    (repo / REPO_DOCKERFILE).write_text("FROM mine\n")
    with pytest.raises(ProjectError, match="not committed"):
        registry.move_dockerfile_into_repo(registry.get("demo"))
    assert (repo / REPO_DOCKERFILE).read_text() == "FROM mine\n"

    (repo / REPO_DOCKERFILE).unlink()
    out = registry.move_dockerfile_into_repo(registry.get("demo"))
    assert out["commit"] and not out["commit_problem"]
    shown = subprocess.run(["git", "show", f"main:{REPO_DOCKERFILE}"], cwd=repo,
                           capture_output=True, text=True).stdout
    assert shown == "FROM python:3.12\n"
    assert registry.get("demo").dockerfile_source == "repo"
    with pytest.raises(ProjectError, match="already in the repository"):
        registry.move_dockerfile_into_repo(registry.get("demo"))


def test_accepting_a_new_projects_checks_moves_its_dockerfile_into_the_repository(tmp_path):
    """A project is not left on Fabrika's own copy by default. Accepting its
    checks commits the Dockerfile they run in -- after the approval, never over
    a file already there, and a failure is reported rather than undoing the
    acceptance."""
    import subprocess
    from factory import onboarding
    from factory.projects import REPO_DOCKERFILE

    src = inspect.getsource(onboarding.ProjectOnboarding.accept)
    assert src.index("self.approve(project)") < src.index("registry.move_dockerfile_on_acceptance(")

    registry, repo = _dockerfile_project(tmp_path)
    (repo / ".fabrika").mkdir()
    (repo / REPO_DOCKERFILE).write_text("FROM somebody-elses\n")
    assert registry.move_dockerfile_on_acceptance(registry.get("demo")) is None
    assert (repo / REPO_DOCKERFILE).read_text() == "FROM somebody-elses\n"

    (repo / REPO_DOCKERFILE).unlink()
    moved = registry.move_dockerfile_on_acceptance(registry.get("demo"))
    assert moved["commit"] and not moved["commit_problem"]
    assert subprocess.run(["git", "show", f"main:{REPO_DOCKERFILE}"], cwd=repo,
                          capture_output=True, text=True).stdout == "FROM python:3.12\n"
    assert registry.move_dockerfile_on_acceptance(registry.get("demo")) is None, \
        "a second acceptance moved it again"


def test_a_reading_records_what_it_cost():
    """Twenty-four readings of one project on the strongest model, and nothing
    said what they came to. The subscription figure is kept beside the charged
    one, because a plan's reading is not free, only unbilled."""
    from factory import onboarding
    from factory.llm import LLM

    client = LLM.__new__(LLM)
    client.usage, client._exchanges = {}, {}
    spent = client._account("resurvey", "m", {"usage": {"prompt_tokens": 10, "cost": 0.8},
                                               "_route": {"name": "claude-code", "billed": False}})
    assert spent["cost_usd"] == 0.0 and spent["notional_usd"] == 0.8
    client._park_usage("resurvey", "p", spent)
    assert onboarding.reading_cost(client, "resurvey", "p")["notional_usd"] == 0.8
    assert onboarding.reading_cost(client, "resurvey", "p") == {}, "taken twice"

    src = class_source(onboarding.ProjectOnboarding)
    assert 'reading_cost(self.llm, "resurvey", prompt)' in src
    assert 'spent=reading_cost(self.llm, "surveyor", prompt)' in src


def test_a_stage_of_the_projects_own_dockerfile_is_built_from_and_never_stored(tmp_path):
    from factory.projects import ProjectRegistry, dockerfile_state

    registry, repo, fabrika = _own_dockerfile_project(tmp_path)
    project = registry.get("demo")
    assert project.dockerfile_source == "project"
    assert project.environment.dockerfile == _OWN_DOCKERFILE
    assert project.store.payload("project")["environment"]["dockerfile"] == ""
    assert ProjectRegistry.environment_problems(project.environment, [], repo) == []
    state = dockerfile_state(project)
    assert (state["path"], state["target"], state["own"]) == ("api/Dockerfile", "test", True)
    assert state["fabrika_unused"], "the old copy is still there and should be offered for removal"

    out = registry.retire_fabrika_dockerfile(registry.get("demo"))
    assert out["commit"] and not (repo / fabrika).exists()
    assert not dockerfile_state(registry.get("demo"))["fabrika_unused"]
    assert registry.move_dockerfile_on_acceptance(registry.get("demo")) is None, \
        "accepting the checks put Fabrika's copy back"


def test_only_the_built_stage_is_judged_and_a_missing_one_is_said(tmp_path):
    """The production stage copies the source, as it should; that is not the
    check image's problem. The stage that is built is judged, and a stage the
    file does not have, or a file not on the branch, is said in words."""
    from factory.projects import ProjectRegistry
    from factory.schemas import EnvironmentSpec

    env = lambda **kw: EnvironmentSpec(kind="reuse", dockerfile_path="api/Dockerfile", **kw)
    assert ProjectRegistry.environment_problems(
        env(dockerfile=_OWN_DOCKERFILE, dockerfile_target="test"), []) == []
    copies = ProjectRegistry.environment_problems(
        env(dockerfile=_OWN_DOCKERFILE, dockerfile_target="app"), [])
    assert any("copies the source tree" in p for p in copies)
    missing = ProjectRegistry.environment_problems(
        env(dockerfile=_OWN_DOCKERFILE, dockerfile_target="ci"), [])
    assert any("no stage named 'ci'" in p for p in missing)
    gone = ProjectRegistry.environment_problems(env(dockerfile_target="test"), [])
    assert any("not committed on the branch features start from" in p for p in gone)


def test_a_resurvey_is_shown_the_projects_own_image_while_fabrika_writes_one(tmp_path):
    """A re-reading sees only the tooling that moved, so an unchanged
    Dockerfile was never in front of it and the suggestion to give it a test
    stage could only have been made blind."""
    import subprocess
    from factory.onboarding import own_image_files

    registry, repo = _dockerfile_project(tmp_path)
    (repo / "api").mkdir()
    (repo / "api" / "Dockerfile").write_text("FROM python:3.12\nCOPY . .\n")
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "image and ci"], cwd=repo, check=True)
    shown = own_image_files(registry.get("demo"), [])
    assert shown and "api/Dockerfile" in shown[0] and ".github/workflows/ci.yml" in shown[0]
    assert "api/Dockerfile" not in own_image_files(registry.get("demo"), ["api/Dockerfile"])[0]

    project = registry.get("demo")
    project.state.environment.dockerfile_path = "api/Dockerfile"
    assert own_image_files(project, []) == [], "shown after the project builds from its own"


def test_a_declared_preview_must_open_a_service_that_exists():
    from factory.projects import ProjectRegistry

    env = schemas.EnvironmentSpec(kind="host", services=[_web_service("api")],
                                  preview=schemas.Preview(open="web"))
    assert any("'web', which is not a service here" in p
               for p in ProjectRegistry.preview_problems(env))
    env.preview.services = [_web_service("web")]
    assert ProjectRegistry.preview_problems(env) == []
    env.preview.services.append(_web_service("api"))
    assert any("already starts" in p for p in ProjectRegistry.preview_problems(env))
    env.preview.services.pop()
    env.preview.path = "app"
    assert any("must start with '/'" in p for p in ProjectRegistry.preview_problems(env))
    # Nothing declared, nothing to say; and nothing to open is an answer, not a fault.
    assert ProjectRegistry.preview_problems(schemas.EnvironmentSpec(kind="host")) == []
    assert ProjectRegistry.preview_problems(schemas.EnvironmentSpec(
        kind="host", preview=schemas.Preview(open="", note="a library"))) == []


def test_every_baseline_that_is_recorded_first_sets_aside_what_needs_docker():
    source = factory_source("onboarding")
    calls = [m.start() for m in re.finditer(r"registry\.record_baseline\(", source)]
    assert calls
    for at in calls:
        assert "_set_aside_what_needs_docker(project, baseline)" in source[max(0, at - 250):at]


def test_a_fix_never_writes_over_a_file_you_changed(tmp_path):
    from factory.projects import ProjectError

    registry, project, repo, _ = _diagnosed_project(tmp_path)
    (repo / "api/pyproject.toml").write_text(_PYPROJECT + "# mine\n")
    with pytest.raises(ProjectError, match="not the one the checks ran against"):
        registry.apply_diagnosis_fix(project, "api-tests", 0)
    assert (repo / "api/pyproject.toml").read_text().endswith("# mine\n")


def test_the_baseline_classifies_and_diagnoses_what_it_measured():
    src = factory_source("onboarding")
    body = src.split("async def _baseline(", 1)[1].split("\n    async def ", 1)[0]
    assert "diagnosis.classify(project.state.gates, report, read)" in body
    assert "await self._diagnose(" in body
    assert body.index("diagnosis.classify(") < body.index("report.results = setup_results + report.results")
    diag = src.split("async def _diagnose(", 1)[1].split("\n    @staticmethod", 1)[0]
    assert "held is not None and not held.failed" in diag, "a diagnosis is redone with nothing changed"
    assert 'project.store.append("diagnosis"' in diag


def test_a_version_is_read_past_a_runner_s_own_status_lines(tmp_path):
    import asyncio
    from types import SimpleNamespace

    from factory.onboarding import ProjectOnboarding
    from factory.schemas import EnvironmentSpec

    class Runner:
        async def execute(self, command, **_):
            return SimpleNamespace(started=True, exit_code=0,
                                   output="Container app-db-1 Running\nPython 3.12.14\n")

    env = EnvironmentSpec(kind="compose", version_commands=["python --version"])
    seen = asyncio.run(ProjectOnboarding._probe_versions(None, env, tmp_path, Runner()))
    assert seen == {"python --version": "Python 3.12.14"}



def test_the_verify_lane_is_handed_testing_sections_and_nothing_inferred(tmp_path):
    """Its tests join the repository, so they are written the way the project
    writes tests -- from the testing sections of its own AGENTS.md and what
    they point to, and never from anything a model read in the code."""
    from factory import guides

    src = inspect.getsource(pipeline.Factory._oracle_guides)
    assert 'self._guidance(store, state, "oracle", inferred=False)' in src
    repo = _guide_repo(tmp_path)
    found = guides.found(repo, "HEAD")
    chosen = guides.select(found, "oracle")
    assert {how for _, how in chosen} == {"testing"}, "the verify lane saw more than testing sections"
    assert guides.select(found, "breaker") == chosen
    text, delivered = guides.render(repo, "HEAD", chosen, 30_000)
    assert "Every test owns its data." in text, "the testing doc AGENTS.md points to was not handed"
    assert "Use ruff." not in text and "No ORM in routers." not in text, "code style reached the verify lane"
    assert {d["path"]: d["part"] for d in delivered}["AGENTS.md"] == "testing sections"


def test_guides_are_the_standard_files_and_what_agents_md_points_to(tmp_path):
    from factory import guides

    repo = _guide_repo(tmp_path)
    found = {g.path: g for g in guides.found(repo, "HEAD")}
    assert set(found) == {"AGENTS.md", "server/AGENTS.md", "CLAUDE.md", "docs/testing.md", "DESIGN.md",
                          ".claude/skills/migrate/SKILL.md", ".agents/skills/release/SKILL.md"}, \
        "a vendored file, a document nothing points to, or a nested DESIGN.md was taken for a guide"
    assert found["server/AGENTS.md"].scope == "server" and found["AGENTS.md"].scope == ""
    assert found["docs/testing.md"].layer == "linked" and found["docs/testing.md"].referenced_by == "AGENTS.md"
    assert found["DESIGN.md"].layer == "design"
    assert found[".claude/skills/migrate/SKILL.md"].description == "Use when adding a migration"
    assert found["AGENTS.md"].sha256 == guides.digest((repo / "AGENTS.md").read_text())


def test_each_role_with_no_harness_is_handed_the_files_that_cover_its_work(tmp_path):
    from factory import guides

    found = guides.found(_guide_repo(tmp_path), "HEAD")
    def handed(role, files=None):
        return {g.path: how for g, how in guides.select(found, role, files)}
    plain = handed("worker", ["web/src/app.py"])
    assert "server/AGENTS.md" not in plain and "DESIGN.md" not in plain, \
        "a worker was handed another folder's rules, or DESIGN.md for code nobody sees"
    assert plain["AGENTS.md"] == "whole" and plain["docs/testing.md"] == "whole"
    assert handed("worker", ["web/src/Board.tsx"])["DESIGN.md"] == "whole"
    assert "server/AGENTS.md" in handed("reviewer", ["server/routes.py"])
    architect = handed("architect")
    assert architect[".claude/skills/migrate/SKILL.md"] == "index", "a skill was read whole, not by name"
    assert architect["server/AGENTS.md"] == "whole" and architect["DESIGN.md"] == "whole"


def test_a_partly_delivered_guide_is_said_to_be_partial(tmp_path):
    from factory import guides

    repo = _guide_repo(tmp_path, {"server/AGENTS.md": "# Server\n\n## Style\n" + "x" * 500})
    found = guides.found(repo, "HEAD")
    chosen = [(g, how) for g, how in guides.select(found, "worker", ["server/x.py"])
              if g.path in ("AGENTS.md", "server/AGENTS.md")]
    text, delivered = guides.render(repo, "HEAD", chosen, budget=120)
    assert "Use ruff." in text and "PARTIAL" in text and "## Style" in text and "xxxx" not in text
    assert {d["path"]: d["partial"] for d in delivered} == {"AGENTS.md": False, "server/AGENTS.md": True}
    alone, _ = guides.render(repo, "HEAD", [(g, "whole") for g in found if g.path == "CLAUDE.md"], 30_000)
    assert alone == "", "a CLAUDE.md that only imports AGENTS.md was handed as a second copy"


def test_the_section_puts_guides_before_what_was_inferred():
    from factory import guides
    from factory.schemas import ObservedConvention

    text = guides.section("### `AGENTS.md`\n\nUse ruff.",
                          [ObservedConvention(rule="404 via HTTPException", slug="s", files=["a.py"])],
                          ["app/db.py:get_session()"])
    assert text.index("Guides -- authoritative") < text.index("Observed conventions -- inferred")
    assert text.index("Observed conventions") < text.index("do not rebuild it")
    assert "a check this project runs (it is a fact), the repository's guides (its people" in text
    assert guides.section("") == "", "an empty section was handed out"


def test_each_runner_is_bridged_to_the_rules_it_does_not_read_in_the_checkout_only(tmp_path):
    """A project keeps its rules where its first tool wanted them; a worker may
    start on another. Before the session, what that runner would not read is
    put where it reads it -- linked, never copied, so an edit lands on the real
    file -- and taken out after. Nothing that exists is touched."""
    from factory import guides

    repo = _guide_repo(tmp_path)
    codex = guides.Reads(["AGENTS.md"], True, [".agents/skills"])
    done = guides.bridge(repo, codex)
    link = repo / ".agents/skills/migrate"
    assert link.is_symlink() and (link / "SKILL.md").read_text().startswith("---\nname: migrate")
    assert done.made == [".agents/skills/migrate"] and done.instructions == [], \
        "a runner was given an instruction file it already reads"
    assert "?? .agents/skills/migrate" in _git_status(repo)
    guides.unbridge(repo, done)
    assert not link.exists() and [ln for ln in _git_status(repo) if ln] == [], \
        "the bridge outlived the session"

    claude = guides.Reads(["CLAUDE.md"], True, [".claude/skills"])
    done = guides.bridge(repo, claude)
    assert (repo / ".claude/skills/release").is_symlink()
    assert done.instructions == [("server/CLAUDE.md", "server/AGENTS.md")], \
        "a folder that has its own CLAUDE.md was given another, or one with AGENTS.md got none"
    assert "No ORM in routers." in (repo / "server/CLAUDE.md").read_text()
    (repo / "server/CLAUDE.md").write_text("changed by the session\n")
    guides.unbridge(repo, done)
    assert (repo / "server/CLAUDE.md").read_text() == "changed by the session\n", \
        "a session's edit to a bridged file was thrown away"
    assert not (repo / ".claude/skills/release").exists()

    root_only = guides.Reads(["AGENTS.md", "CLAUDE.md"], False,
                             [".agents/skills", ".claude/skills", ".openhands/skills"])
    assert guides.bridge(repo, root_only).made == [], "a runner that reads it all was bridged anyway"

    claude_first = _repo_with_one_commit(tmp_path / "claude-first", {
        "CLAUDE.md": "# Rules\n\n@docs/style.md\n", "docs/style.md": "Use tabs.\n"})
    guides.bridge(claude_first, codex)
    written = (claude_first / "AGENTS.md").read_text()
    assert "Use tabs." in written and "@docs/style.md" not in written, \
        "an import was handed to a tool that does not follow it"


def test_a_harness_is_told_what_its_runner_loaded_and_where_the_rest_is(tmp_path):
    from factory import guides

    repo = _guide_repo(tmp_path)
    found = guides.found(repo, "HEAD")
    files = ["server/routes.py", "web/Board.tsx"]
    codex = guides.Reads(["AGENTS.md"], True, [".agents/skills"])
    note = guides.harness_note(found, files, codex, guides.bridge(repo, codex))
    loaded, rest = note.split("Your harness does not load these", 1)
    assert "`AGENTS.md`" in loaded and "`server/AGENTS.md`" in loaded and "2 skills" in loaded, \
        "a bridged skill was not counted as loaded"
    assert "`DESIGN.md`" in rest and "`docs/testing.md`" in rest
    assert "Use ruff." not in note, "a guide's text was pasted into a harness's task"
    bare = guides.harness_note(found, ["api/x.py"], guides.Reads(), guides.Bridge())
    assert "`AGENTS.md` -- how to work in this repository" in bare and "Loaded for you" not in bare, \
        "a runner that declares nothing was told something was loaded for it"
    assert "`DESIGN.md`" not in bare, "DESIGN.md was pointed at for code nobody sees"


def test_guides_in_the_working_copy_are_offered_for_commit_and_never_offered_to_create(tmp_path):
    """A DESIGN.md written but not committed binds no agent yet. Offering to
    create one in its place was refused at the press -- the file exists --
    so it is offered for what it is: a commit of the file as it stands."""
    import subprocess

    from factory import guides
    from factory.projects import ProjectError, ProjectRegistry

    repo = _repo_with_one_commit(tmp_path / "repo", {
        "web/App.tsx": "x", "web/styles.css": ":root { --accent: #2f6f4f; }\n"})
    (repo / "DESIGN.md").write_text("---\nname: Mine\n---\n\n## Overview\n")
    (repo / "AGENTS.md").write_text("# A\n\nSee [testing](docs/testing.md).\n")
    (repo / "docs").mkdir()
    (repo / "docs/testing.md").write_text("# Testing\n")
    (repo / "notes.md").write_text("not a guide\n")
    working = guides.working_guides(repo)
    assert [w["path"] for w in working] == ["AGENTS.md", "DESIGN.md", "docs/testing.md"], \
        "a file nothing points to was taken for a guide, or a linked one was missed"
    kinds = [o["kind"] for o in guides.offers(
        ["web/App.tsx", "web/styles.css"], [], tokens=[("web/styles.css", "--accent", "#2f6f4f")],
        checks=[], agents_text=None, name="d", working=working)]
    assert kinds[0] == "commit_working" and not {"design_md", "agents_md"} & set(kinds), \
        "Fabrika offered to create a file that is already in the working copy"

    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")
    writes = [{**w, "as_is": True} for w in working]
    (repo / "DESIGN.md").write_text("changed after it was shown\n")
    with pytest.raises(ProjectError, match="changed in your working copy"):
        registry.write_guide(project, writes, message="m")
    working = guides.working_guides(repo)
    writes = [{**w, "as_is": True} for w in working]
    writes[0]["contents"] = "# A\n\nSee [testing](docs/testing.md). Edited on the page.\n"
    out = registry.write_guide(project, writes, message="Commit the guides")
    assert out["commit"] and "Edited on the page." in (repo / "AGENTS.md").read_text()
    assert (repo / "DESIGN.md").read_text() == "changed after it was shown\n"
    shown = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=repo,
                           capture_output=True, text=True).stdout.split()
    assert sorted(shown) == ["AGENTS.md", "DESIGN.md", "docs/testing.md"]
    assert "notes.md" in subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                                        capture_output=True, text=True).stdout, \
        "a file that is not a guide was committed with them"
    assert guides.working_guides(repo) == []


def test_a_new_skill_goes_where_the_projects_skills_already_are():
    from factory.guides import skills_home

    assert skills_home([".claude/skills/a/SKILL.md", ".claude/skills/b/SKILL.md",
                        ".agents/skills/c/SKILL.md"])[0] == ".claude/skills"
    assert skills_home([".agents/skills/c/SKILL.md"])[0] == ".agents/skills"
    assert skills_home(["src/app.py"])[0] == ".agents/skills", "no skills, and not the open standard's folder"
    assert skills_home([".claude/settings.json"])[0] == ".claude/skills", \
        "a team that runs Claude Code by hand was given a folder it never reads"
    assert skills_home([".claude/skills/a/SKILL.md"], ".agents/skills") == (
        ".agents/skills", "chosen for this project")


def test_skills_are_linked_in_the_repository_only_for_people_who_use_another_tool(tmp_path):
    import subprocess

    from factory import guides
    from factory.projects import ProjectRegistry

    def kinds(files):
        return [o["kind"] for o in guides.offers(files, [], tokens=[], checks=[],
                                                  agents_text="# A\n", name="d")]
    claude_kept = [".claude/skills/x/SKILL.md", "AGENTS.md"]
    assert "skills_link_agents" not in kinds(claude_kept), "a link was proposed with no one to use it"
    assert "skills_link_agents" in kinds([*claude_kept, ".codex/config.toml"])
    assert "skills_link_claude" in kinds([".agents/skills/x/SKILL.md", ".claude/settings.json", "AGENTS.md"])
    repo = _repo_with_one_commit(tmp_path / "repo", {
        ".claude/skills/x/SKILL.md": "---\nname: x\n---\n", ".codex/config.toml": "", "AGENTS.md": "# A\n"})
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")
    out = registry.write_guide(project, [{"path": ".agents/skills", "link": "../.claude/skills"}],
                               message="Link the skills")
    assert out["commit"] and (repo / ".agents/skills/x/SKILL.md").is_file()
    assert subprocess.run(["git", "show", "HEAD:.agents/skills"], cwd=repo, capture_output=True,
                          text=True).stdout == "../.claude/skills"
    assert guides.bridge(repo, guides.Reads(["AGENTS.md"], True, [".agents/skills"])).made == [], \
        "skills already shared through a committed link were bridged again"


def test_a_feature_that_changes_agents_md_design_md_or_a_skill_raises_a_blocker(tmp_path):
    import subprocess
    from types import SimpleNamespace

    from factory.pipeline import check_guides_changed

    repo = _guide_repo(tmp_path)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                          text=True).stdout.strip()
    (repo / "AGENTS.md").write_text("# Agents\n\nAnything goes.\n")
    (repo / ".claude/skills/new").mkdir(parents=True)
    (repo / ".claude/skills/new/SKILL.md").write_text("---\nname: new\n---\n")
    (repo / "CONTRIBUTING.md").write_text("# changed, but nothing points here\n")
    changed = ["AGENTS.md", ".claude/skills/new/SKILL.md", "CONTRIBUTING.md"]
    sandbox = SimpleNamespace(state=SimpleNamespace(base_sha=base), path=repo, exists=lambda: True,
                              changed_files=lambda: changed,
                              show=lambda ref, path: (lambda r: r.stdout if r.returncode == 0 else None)(
                                  subprocess.run(["git", "show", f"{ref}:{path}"], cwd=repo,
                                                 capture_output=True, text=True)))
    found = check_guides_changed(sandbox)
    assert [f.id for f in found] == ["guide-edited-1"] and found[0].severity == "blocker"
    assert "AGENTS.md" in found[0].title and ".claude/skills/new/SKILL.md" in found[0].title
    assert "CONTRIBUTING.md" not in found[0].title
    src = inspect.getsource(pipeline.Factory._converge)
    assert "for g in self._guides_at(self._base_of(state))" in src, "the guides are not in the protected set"


# -- guides, proposed (phase 5) ----------------------------------------------------


def test_design_md_front_matter_comes_from_tokens_the_code_defines():
    """Fabrika writes no design rule nobody stated: the front matter is what
    the code defines, and every section is headed and left for a person."""
    import yaml

    from factory import guides

    tokens = [("web/styles.css", "--color-primary", "#1A1C1E"), ("web/styles.css", "--brand", "var(--color-primary)"),
              ("web/styles.css", "--radius-md", "8px"), ("web/styles.css", "--space-4", "16px"),
              ("web/styles.css", "--font-sans", "Inter, system-ui, sans-serif"),
              ("web/styles.css", "--width", "calc(100% - 2rem)")]
    text = guides.design_md("Kanban", tokens)
    front = yaml.safe_load(text.split("---", 2)[1])
    assert front["name"] == "Kanban" and front["version"] == "alpha"
    assert front["colors"] == {"primary": "#1A1C1E", "brand": "{colors.primary}"}
    assert front["rounded"] == {"md": "8px"} and front["spacing"] == {"4": "16px"}
    assert front["typography"] == {"sans": {"fontFamily": "Inter"}}
    assert "calc(" not in text, "a value the format cannot hold was bent to fit"
    headings = re.findall(r"^## (.+)$", text, re.M)
    assert headings == list(guides.DESIGN_SECTIONS)
    body = text.split("---", 2)[2]
    assert all(not ln.strip() or ln.startswith(("## ", "<!--")) for ln in body.splitlines()), \
        "a design rule was written that nobody stated"


def test_fabrika_proposes_only_what_its_evidence_supports():
    from types import SimpleNamespace

    from factory import guides

    checks = [SimpleNamespace(name="lint", command="ruff check ."), SimpleNamespace(name="tests", command="pytest")]
    tokens = [("web/src/styles.css", "--color-brand", "#123456")]
    ui = ["web/src/App.tsx", "web/src/styles.css", ".claude/settings.json"]
    def kinds(files, agents_text=None, declined=(), tok=tokens):
        return [o["kind"] for o in guides.offers(files, list(declined), tokens=tok, checks=checks,
                                                  agents_text=agents_text, name="demo")]
    assert kinds(ui) == ["design_md", "agents_md", "claude_md"]
    offers = {o["kind"]: o for o in guides.offers(ui, [], tokens=tokens, checks=checks,
                                                   agents_text="# A\n", name="demo")}
    assert [w["path"] for w in offers["design_md"]["writes"]] == ["DESIGN.md", "AGENTS.md"], \
        "DESIGN.md came without the line that sends agents to it"
    assert offers["design_md"]["writes"][1]["contents"].endswith(guides.DESIGN_LINE)
    assert kinds(["api/app.py"]) == ["agents_md"], "a proposal was made from nothing"
    agents = guides.offers(["api/app.py"], [], tokens=[], checks=checks, agents_text=None,
                           name="demo")[0]["writes"][0]["contents"]
    assert "- lint: `ruff check .`" in agents and "## Code style" in agents and "## Testing" in agents
    assert kinds(["DESIGN.md", "web/App.tsx"], agents_text="# A\n") == ["design_line"]
    assert kinds(["DESIGN.md", "web/App.tsx"], agents_text="Follow DESIGN.md.\n") == []
    assert kinds(ui, tok=[]) == ["ux_gap", "agents_md", "claude_md"]
    assert kinds(ui, declined=["design_md", "agents_md", "claude_md"]) == []


def test_every_file_fabrika_writes_is_a_standard_convention_file(tmp_path):
    import subprocess

    from factory.projects import ProjectError, ProjectRegistry

    repo = _guide_repo(tmp_path)
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")
    project.state.base_ref = "HEAD"
    with pytest.raises(ProjectError, match="not a guide file"):
        registry.write_guide(project, [{"path": "docs/conventions.md", "contents": "# x\n"}], message="m")
    with pytest.raises(ProjectError, match="already exists"):
        registry.write_guide(project, [{"path": "DESIGN.md", "contents": "# new\n"}], message="m")
    (repo / "AGENTS.md").write_text("# mine, uncommitted\n")
    with pytest.raises(ProjectError, match="not committed"):
        registry.write_guide(project, [{"path": "AGENTS.md", "contents": "## more\n", "append": True}],
                             message="m")
    subprocess.run(["git", "checkout", "--", "AGENTS.md"], cwd=repo, check=True)
    out = registry.write_guide(project, [
        {"path": "AGENTS.md", "contents": "## Design\n\nFollow it.", "append": True},
        {"path": "api/AGENTS.md", "contents": "# API\n\nErrors are {\"detail\": ...}.\n"}],
        message="Add rules")
    assert out["commit"] and out["paths"] == ["AGENTS.md", "api/AGENTS.md"]
    assert (repo / "AGENTS.md").read_text().rstrip().endswith("Follow it.")
    shown = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=repo,
                           capture_output=True, text=True).stdout.split()
    assert sorted(shown) == ["AGENTS.md", "api/AGENTS.md"], "the proposal was not one commit of its files"
    assert ".openhands" not in subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True,
                                              text=True).stdout


def test_a_proposed_rule_cites_the_features_and_files_it_was_observed_in(tmp_path):
    import asyncio
    from types import SimpleNamespace

    from factory.config import Config
    from factory.onboarding import ProjectOnboarding
    from factory.projects import ProjectRegistry
    from factory.schemas import ObservedConvention, ScoutReport

    repo = _guide_repo(tmp_path)
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")
    project.state.base_ref = "HEAD"
    project = registry.save(project)
    for i in range(3):
        store = project.feature_store(f"f{i}")
        observed = [ObservedConvention(rule="Routers raise HTTPException for 404", slug="routers-404",
                                       files=["server/app.py", "gone.py"])]
        if i == 0:
            observed.append(ObservedConvention(rule="Only once", slug="once", files=["server/app.py"]))
        store.append("scout", ScoutReport(summary="s", observed_conventions=observed), role="scout")

    class LLM:
        async def ask(self, role, prompt, schema, system=""):
            raise RuntimeError("no guide writer configured")

    onboarding = SimpleNamespace(config=Config(), llm=LLM(), registry=registry)
    asyncio.run(ProjectOnboarding._offer_guide_draft(onboarding, project))
    draft = registry.pending_guide_draft(registry.get(project.id))
    assert draft is not None and draft.features == 3
    assert [(r.slug, r.features, r.files) for r in draft.rules] == [("routers-404", 3, ["server/app.py"])], \
        "a rule one feature saw, or a file that is gone, made the draft"
    assert draft.mode == "append" and draft.path == "AGENTS.md", "the draft went somewhere but AGENTS.md"
    assert draft.markdown.startswith("## Code style") and "Routers raise HTTPException for 404" in draft.markdown
    registry.decline_guide_offer(registry.get(project.id), draft.id)
    assert registry.pending_guide_draft(registry.get(project.id)) is None


def test_a_reading_is_shown_the_repositorys_guides_and_design_md_gets_its_lint(tmp_path):
    from factory.projects import ProjectRegistry

    src = factory_source("onboarding")
    assert "+ self._guide_text(project)," in src
    assert src.count("self.registry.raise_guide_checks(project)") == 2, "the baseline or a reading does not offer it"
    surveyor = (ROOT / "factory" / "roles" / "surveyor.md").read_text()
    assert "### From a guide's rule to a check" in surveyor and "guide_nominations" not in surveyor
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(_guide_repo(tmp_path), "demo")
    project.state.base_ref = "HEAD"
    registry.raise_guide_checks(project)
    registry.raise_guide_checks(project)
    recs = [r for r in registry.live_recommendations(project) if "design.md" in r.would_gate]
    assert len(recs) == 1 and recs[0].family == "quality", "DESIGN.md's lint was not offered once, as a quality check"
