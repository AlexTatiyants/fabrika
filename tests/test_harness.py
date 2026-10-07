"""Agents and where they run: harnesses, routes, meters, isolation and containers.

Split out of test_invariants.py, which keeps one test per invariant.
"""

from helpers import *  # noqa: F403


def test_the_docker_command_mounts_the_sandbox_and_cuts_the_network(tmp_path):
    from factory.containers import DockerRunner

    runner = DockerRunner("factory/relay:abc123", "/work", _docker_config())
    argv = runner.argv("python -m pytest -q", cwd=tmp_path, network=False, name="factory-test")

    assert argv[:3] == ["docker", "run", "--rm"]
    assert f"{tmp_path}:/work" in argv, "the feature's worktree is what gets mounted"
    assert argv[argv.index("--workdir") + 1] == "/work"
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--memory") + 1] == "4g"
    assert argv[argv.index("--pids-limit") + 1] == "512"
    assert argv[-3:] == ["factory/relay:abc123", "-c", "python -m pytest -q"]
    assert argv[argv.index("--entrypoint") + 1] == "sh", \
        "an image with its own ENTRYPOINT would otherwise swallow the command"
    assert "--user" in argv, "gates must not leave root-owned files in the worktree"


def test_setup_gets_the_network_and_gates_do_not(tmp_path):
    """Both on a sealed network, never the default bridge -- which routes to this
    machine, where a developer's own copy of the project listens on its usual
    port. What differs is the way out: setup's network has the egress proxy on
    it and the resolver that sends outside names there; the checks' network has
    neither. And no proxy variable anywhere: a JVM never read one."""
    from factory.containers import DockerRunner

    runner = DockerRunner("img", "/work", _docker_config())
    runner._sealed, runner._egress = "fabrika-sealed-x", "fabrika-egress-x"
    runner._egress_subnet = "10.212.4.0/22"
    with_net = runner.argv("npm ci", cwd=tmp_path, network=True, name="a")
    without = runner.argv("npm test", cwd=tmp_path, network=False, name="b")

    assert with_net[with_net.index("--network") + 1] == "fabrika-egress-x", \
        "dependency setup needs a way out, and only through the proxy"
    mount = next(a for a in with_net if a.endswith(":/etc/resolv.conf:ro"))
    assert "nameserver 10.212.5.1" in Path(mount.split(":")[0]).read_text()
    assert without[without.index("--network") + 1] == "fabrika-sealed-x", \
        "gates run against model-written code; they do not need to reach the internet"
    assert not any("resolv.conf" in a for a in without)
    assert not any("_PROXY=" in a.upper() for a in with_net + without)
    assert "bridge" not in with_net and "host" not in with_net


def test_an_unbuildable_environment_fails_rather_than_running_on_the_host(tmp_path):
    """The property the whole module exists for: no silent fallback. Results you
    believe are isolated and are not are worse than no results."""
    import asyncio
    from factory.containers import DockerError, build_image
    from factory.projects import ProjectRegistry
    from factory.schemas import EnvironmentSpec

    registry = ProjectRegistry(tmp_path / "evidence")
    repo = tmp_path / "repo"
    repo.mkdir()
    project = registry.create(repo, "demo")
    project.state.environment = EnvironmentSpec(
        kind="derive", dockerfile="FROM scratch\n", workdir="/work",
    )

    docker = _docker_config(binary="definitely-not-a-real-docker-binary")
    with pytest.raises(DockerError) as caught:
        asyncio.run(build_image(project, docker))
    message = str(caught.value)
    assert "demo" in message and "not on PATH" in message
    assert "host" in message, "the error names the one legitimate way out"


def test_a_host_environment_is_the_only_way_to_skip_the_container(tmp_path):
    import asyncio
    from factory.containers import build_image, runner_for
    from factory.gates import LocalRunner
    from factory.projects import ProjectRegistry
    from factory.schemas import EnvironmentSpec

    registry = ProjectRegistry(tmp_path / "evidence")
    repo = tmp_path / "repo"
    repo.mkdir()
    project = registry.create(repo, "demo")
    project.state.environment = EnvironmentSpec(kind="host", rationale="no container available")

    config = load_config(ROOT / "factory.example.yaml")
    runner, image = asyncio.run(runner_for(project, config))
    assert isinstance(runner, LocalRunner)
    assert image == ""
    assert asyncio.run(build_image(project, config.docker)) == ""


def test_an_image_tag_is_content_addressed(tmp_path):
    """An unchanged environment never rebuilds; an edited one always does."""
    from factory.containers import image_tag
    from factory.schemas import EnvironmentSpec

    a = EnvironmentSpec(kind="generate", dockerfile="FROM python:3.11\n", workdir="/work")
    b = EnvironmentSpec(kind="generate", dockerfile="FROM python:3.11\n", workdir="/work")
    c = EnvironmentSpec(kind="generate", dockerfile="FROM python:3.12\n", workdir="/work")

    assert image_tag("relay", a) == image_tag("relay", b)
    assert image_tag("relay", a) != image_tag("relay", c)
    assert image_tag("relay", a) != image_tag("other", a)
    # The prefix comes from the constant, so a rename lands in one place: what
    # this line is about is the slug, which must stay a legal docker reference.
    from factory.containers import NAME_PREFIX
    assert image_tag("Relay Service", a).startswith(f"{NAME_PREFIX}/relay-service:")


def test_every_agent_exchange_records_what_it_cost(tmp_path):
    """A per-run total appended at the end cannot say which call spent the money,
    and does not exist at all while the run is still going. The cost of an
    exchange is a fact about that exchange, so it is written onto its record --
    and a prompt this process never bought stays silent rather than claiming
    zero, because unknown and free are different claims."""
    from factory.llm import LLM
    from factory.store import EvidenceStore

    client = LLM.__new__(LLM)          # no config, no HTTP: only the accounting
    client.usage = {}
    client._exchanges = {}

    prompt = "# Intent\n\nbuild a thing"
    client._account("scout", "x/y", {"usage": {
        "prompt_tokens": 1200, "completion_tokens": 300, "total_tokens": 1500, "cost": 0.0042}})
    client._park_usage("scout", prompt, {
        "cost_usd": 0.0042, "prompt_tokens": 1200,
        "completion_tokens": 300, "total_tokens": 1500})

    evidence = EvidenceStore(tmp_path, "feature-1")
    evidence.usage_of = client.take_usage

    bought = evidence.append("scout", {"summary": "a repo"}, role="scout", model="x/y",
                             prompt=prompt)
    assert bought["meta"]["cost_usd"] == 0.0042
    assert bought["meta"]["total_tokens"] == 1500

    # The harness spends its own money through its own tooling. Its prompt is
    # recorded; its cost is not this client's to report.
    harness = evidence.append("worker_prompt", {"unit_id": "U1"}, role="worker",
                              prompt="build unit U1")
    assert "cost_usd" not in harness["meta"]

    # Taken once: the same record cannot be written twice and count twice.
    again = evidence.append("scout", {"summary": "a repo"}, role="scout", prompt=prompt)
    assert "cost_usd" not in again["meta"]


def test_a_role_can_be_given_longer_than_its_route(tmp_path):
    """A route's limit is for its ordinary calls. The first reading of a
    repository is not one, and it is the one role that says so."""
    cfg = load_config(ROOT / "factory.example.yaml")
    route = cfg.route("claude-code")
    assert cfg.role("surveyor").timeout_s > route.timeout_s
    assert cfg.role("resurvey").timeout_s > route.timeout_s
    assert cfg.role("reviewer").timeout_s == 0.0, "a role that says nothing keeps its route's"

    slow = route.model_copy(update={"timeout_s": 0.2})
    client = LLM(cfg)
    argv = ["python3", "-c", "import time; time.sleep(2)"]
    with pytest.raises(LLMError, match=r"within 0\.2s"):
        asyncio.run(client._communicate(slow, argv, "", dict(os.environ)))
    # The same process, and the role's longer limit lets it finish.
    code, _, _ = asyncio.run(client._communicate(slow, argv, "", dict(os.environ), timeout_s=10))
    assert code == 0


def test_a_truncated_answer_is_refused_not_retried(monkeypatch):
    """A cut-off response is not a malformed one. Retrying the same prompt
    truncates identically, and each schema repair appends the cut-off output --
    so three attempts burn three full generations to fail the same way."""
    import asyncio
    from factory.llm import LLM, LLMError
    from factory.config import load_config
    from factory.schemas import ScoutReport

    config = load_config()
    # This is a test of the HTTP path, and `_post` is only reached by a role on
    # an api route. Left on its shipped route, `scout` goes to a CLI and the
    # stub below is never called -- which reads as "truncation was not refused"
    # when in fact truncation never happened.
    config.roles["scout"].route = ""
    llm = LLM(config)
    calls = {"n": 0}

    async def fake_post(payload):
        calls["n"] += 1
        return {
            "choices": [{"finish_reason": "length",
                         "message": {"content": '{"summary":"this got cut off mid'}}],
            "usage": {"total_tokens": 32000},
        }

    monkeypatch.setattr(llm, "_post", fake_post)

    with pytest.raises(LLMError) as caught:
        asyncio.run(llm.ask("scout", "read the repo", ScoutReport))

    assert calls["n"] == 1, "it must not spend two more generations on a known-hopeless retry"
    message = str(caught.value)
    assert "output ceiling" in message
    assert "retrying will not fix it" in message
    assert "schema" not in message.split("This is not a schema problem")[0], (
        "it must not be reported as a schema failure, which is what sends a human "
        "looking at the wrong thing")


def test_a_truncated_answer_is_refused_on_the_schemaless_path_too(monkeypatch):
    """Worse there than with a schema: a cut-off answer is indistinguishable
    from a short one, so it would be accepted as complete."""
    import asyncio
    from factory.llm import LLM, LLMError
    from factory.config import load_config

    config = load_config()
    config.roles["scout"].route = ""   # the HTTP path; see the test above
    llm = LLM(config)

    async def fake_post(payload):
        return {"choices": [{"finish_reason": "length",
                             "message": {"content": "half a sentence that stops"}}],
                "usage": {}}

    monkeypatch.setattr(llm, "_post", fake_post)
    with pytest.raises(LLMError, match="output ceiling"):
        asyncio.run(llm.ask("scout", "summarise"))


def test_a_harness_own_files_are_not_applied_as_the_units_work(tmp_path, monkeypatch):
    """aider writes .aider.chat.history.md into the tree it works in. Left
    unfiltered it arrives as a FileWrite and lands in the sandbox as though the
    worker had authored it."""
    import asyncio
    import subprocess
    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.llm import LLM
    from factory.schemas import SelfDisclosure, Spec, WorkUnit

    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=repo, check=True)
    (repo / "seed.txt").write_text("start\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

    config = load_config()
    config.executor.kind = "command"
    # A stand-in harness: writes one real file and three pieces of its own litter.
    _global_harness(config, [
        "sh", "-c",
        "echo built > real.py; echo x > .aider.chat.history.md; "
        "mkdir -p .aider.tags.cache.v3 && echo y > .aider.tags.cache.v3/c; "
        "echo z > .claude.json; printf 'node_modules\\n' > .gitignore",
    ])
    executor = CommandExecutor(LLM(config), config)

    async def fake_ask(role_name, prompt, schema=None, **kw):
        return schema(summary="s", decisions=[], disclosure=SelfDisclosure())
    monkeypatch.setattr(executor.llm, "ask", fake_ask)

    unit = WorkUnit(id="u", title="t", objective="o")
    out = asyncio.run(executor.run(unit, Spec(title="t", intent="i", summary="s"),
                                   "digest", "system", repo))

    written = sorted(f.path for f in out.files)
    assert written == [".gitignore", "real.py"], (
        f"harness litter leaked, or a real dotfile was mistaken for it: {written}")


def test_a_harness_with_no_key_is_refused_rather_than_launched(tmp_path):
    """Given no credential a CLI may open an interactive login and block until
    the executor timeout. A hang reads as a slow build, not a missing key."""
    import asyncio
    import subprocess
    from factory.config import load_config
    from factory.executors import CommandExecutor, ExecutorError
    from factory.llm import LLM
    from factory.schemas import Spec, WorkUnit

    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=repo, check=True)
    (repo / "a.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "i"], cwd=repo, check=True)

    config = load_config()
    config.executor.kind = "command"
    config.executor.key_env = "OPENROUTER_API_KEY"
    config.api.api_key = ""
    _global_harness(config, ["sh", "-c", "echo should-not-run > ran.txt"])
    executor = CommandExecutor(LLM(config), config)

    with pytest.raises(ExecutorError, match="needs a provider key"):
        asyncio.run(executor.run(WorkUnit(id="u", title="t", objective="o"),
                                 Spec(title="t", intent="i", summary="s"),
                                 "digest", "system", repo))
    assert not (repo / "ran.txt").exists(), "the harness must not have been started at all"


def test_a_worker_with_a_checkout_is_not_also_handed_a_transcription_of_it():
    """Two copies of the same code, one of them clipped at 8k per file, with
    nothing saying which is authoritative. Given both, a model rebuilds a long
    file from the clipped one or loops trying to reconcile them -- which is
    exactly what both harness runs did."""
    from factory.executors import unit_brief
    from factory.schemas import Spec, WorkUnit

    unit = WorkUnit(id="u", title="t", objective="o", files_expected=["app/big.py"])
    spec = Spec(title="t", intent="i", summary="s")

    with_digest = unit_brief(unit, spec, "X" * 100_000)
    without = unit_brief(unit, spec, "")

    assert len(without) < 2_000, "the brief is the unit, not a copy of the repository"
    assert "XXXX" not in without
    assert "app/big.py" in without, "it still says which files the unit owns"
    assert "on disk is the truth" in without, (
        "and it says where to look, or a model with no repo section may assume "
        "there is nothing to read")
    assert "X" * 100 in with_digest, "the direct executor still needs the digest: it has no checkout"


def test_integration_is_billed_to_the_integrator_not_the_worker():
    """It goes through the same executor as a unit, which hardcoded the worker
    role -- so the integrator would have run on the worker's model and budget
    and the usage report would have hidden it."""
    import inspect
    from factory.executors import CommandExecutor, DirectExecutor

    for cls in (DirectExecutor, CommandExecutor):
        sig = inspect.signature(cls.run)
        assert "role" in sig.parameters, f"{cls.__name__}.run must be told whose work this is"
        assert sig.parameters["role"].default == "worker"
        # Newlines as well as spaces: the call is wrapped in one of them.
        src = re.sub(r"\s+", "", inspect.getsource(cls.run))
        assert 'ask("worker"' not in src, (
            f"{cls.__name__} still asks as the worker regardless of whose work it is")
        assert "ask(role" in src, (
            f"{cls.__name__} must ask as whichever role was given")


def test_concurrent_features_get_their_own_compose_stack(tmp_path):
    """Isolation is the compose project name, not port arithmetic. Under a name
    per feature every stack has its own containers, network and volumes -- so
    ten features can each have a database on 5432 and none can see another's."""
    import asyncio
    from factory.config import load_config
    from factory.containers import ComposeRunner, runner_for
    from factory.projects import Project
    from factory.schemas import EnvironmentSpec, ProjectState

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker-compose.yml").write_text(
        "services:\n  db:\n    image: postgres:16\n    ports: ['5433:5432']\n"
        "  api:\n    image: python\n    ports: ['8001:8000']\n")

    spec = EnvironmentSpec(kind="compose", compose_file="docker-compose.yml",
                           compose_service="api", workdir="/app")
    state = ProjectState(project_id="proj", name="proj", repo=str(repo),
                         base_ref="main", environment=spec)
    project = Project(state=state, evidence_root=tmp_path / "ev")
    config = load_config()

    names = set()
    for feature in ("alpha", "beta"):
        runner, name = asyncio.run(runner_for(project, config, feature_id=feature))
        assert isinstance(runner, ComposeRunner)
        names.add(name)
        assert feature in name
    assert len(names) == 2, "two features must not share a stack"

    # And the override strips every published port: the one thing that would
    # actually collide between concurrent stacks.
    runner, _ = asyncio.run(runner_for(project, config, feature_id="alpha"))
    override = runner._override(tmp_path / "worktree")
    assert override["services"]["db"]["ports"] == []
    assert override["services"]["api"]["ports"] == []
    assert override["services"]["api"]["volumes"] == [f"{tmp_path / 'worktree'}:/app"], (
        "the gate service runs the feature's worktree, not the checked-out repo")
    assert "volumes" not in override["services"]["db"], "only the gate service is remounted"

    # Compose declares the runtime; a project's own service image is usually a
    # production one with no test runner, so the gate service alone is swapped
    # for the toolchain image while every other service stays as declared.
    spec.image = "factory/proj:abc123"
    swapped = ComposeRunner(repo, spec, "n", config.docker)._override(tmp_path / "w")
    assert swapped["services"]["api"]["image"] == "factory/proj:abc123"
    assert swapped["services"]["api"]["build"] is None, "or compose rebuilds the production image"
    assert "image" not in swapped["services"]["db"], "the database is the project's, untouched"


def test_only_the_gate_service_and_its_dependencies_are_started():
    """`up` with no argument starts everything the project declares. For a
    typical web project that means building a frontend image the gates never
    use -- and failing on that build rather than on the code under test."""
    import inspect
    from factory.containers import ComposeRunner

    src = inspect.getsource(ComposeRunner.execute)
    assert '"up", "-d", "--wait", self.spec.compose_service' in src.replace("\n", "").replace("  ", " ") \
        or "self.spec.compose_service," in src, (
        "up must name the gate service so compose starts its dependency chain and "
        "nothing else")


def test_a_stack_left_by_a_killed_run_does_not_poison_the_next_one():
    """Containers outlive the process that made them. A run killed mid-flight
    leaves its names taken, and without clearing them first every later run of
    that feature fails on the conflict rather than on the code."""
    import inspect
    from factory.containers import ComposeRunner

    src = inspect.getsource(ComposeRunner._ensure_up)
    down_at = src.find('"down"')
    up_at = src.find('"up"')
    assert down_at != -1 and up_at != -1
    assert down_at < up_at, "the stale stack must be cleared before starting a new one"


def test_the_setup_container_and_its_image_are_cleaned_up():
    """A committed image per run is disk that nothing else will reclaim."""
    from factory.containers import ComposeRunner, DockerRunner
    for cls in (DockerRunner, ComposeRunner):
        src = inspect.getsource(cls.down if hasattr(cls, "down") else cls)
        assert "_session" in src and "_committed" in src, \
            f"{cls.__name__}.down leaves the setup container or its image behind"
        assert "rmi" in src, f"{cls.__name__}.down never removes the committed image"


def test_a_container_id_is_read_from_stdout_not_guessed():
    """`compose run --name` is accepted and ignored -- compose names one-off
    containers itself. Exec'ing the name we asked for produced "No such
    container" on every setup command while `run` reported success."""
    from factory.containers import _last_id

    compose_output = (
        " Container proj-backend-run-d33d4fe8 Creating \n"
        " Container proj-backend-run-d33d4fe8 Created \n"
        "6fc15cc9ee0aee3847b4e41f6c282e45d5c9ef3f0df76b5939f95e28123e5af5\n"
    )
    assert _last_id(compose_output).startswith("6fc15cc9ee0a")
    # Nothing that is not an id is ever returned as one.
    assert _last_id(" Container x Created\nError response from daemon: nope\n") == ""
    assert _last_id("") == ""
    assert _last_id("deadbeef") == "", "too short to be a container id"


def test_a_full_docker_disk_is_named_before_a_build_and_where_it_bites(monkeypatch):
    """Docker Desktop's disk is a fixed-size one inside its VM. It fills while
    the host has plenty, and nothing said so: a harness build failed on it,
    then every unit's database. Measured, named, and a build refuses to start
    on it rather than failing an hour in."""
    from factory import containers

    async def measured(docker):
        return 300 * 1024 ** 2, 59 * 1024 ** 3
    monkeypatch.setattr(containers, "docker_disk", measured)
    said = asyncio.run(containers.low_disk(Config().docker, 3.0))
    assert said.startswith("Docker is out of disk space: 0.3 GB free of 59.0 GB")
    assert "docker system prune" in said and "Docker Desktop" in said
    assert asyncio.run(containers.low_disk(Config().docker, 0.1)) == ""

    async def unmeasurable(docker):
        return None
    monkeypatch.setattr(containers, "docker_disk", unmeasurable)
    assert asyncio.run(containers.low_disk(Config().docker, 3.0)) == "", \
        "a disk that could not be measured is never reported full"

    source = inspect.getsource(pipeline.Factory.require_authoring_environment)
    assert "low_disk(self.config.docker, self.config.docker.min_free_gb)" in source
    assert "raise ProjectError(full)" in source


def test_the_setup_container_outlives_the_teardown_that_starts_the_stack():
    """`_ensure_up` opens with `down --remove-orphans`, which removes one-off
    `run` containers as well as leftovers from a killed run. Creating the setup
    container first meant the first command that tried to use it had already
    destroyed it: `run -d` returned a real container id and `exec` answered
    "No such container" for every line of setup."""
    from factory.containers import ComposeRunner

    src = inspect.getsource(ComposeRunner.open_session)
    assert "_ensure_up" in src, "the stack has to be up before the container is made"
    assert src.index("_ensure_up") < src.index('"run"'), \
        "the teardown inside _ensure_up would take the session container with it"

    # And `execute` must not repeat the teardown once a session exists.
    body = inspect.getsource(ComposeRunner.execute)
    assert '"down"' not in body, \
        "execute must delegate the up/down dance rather than run it again"


def test_a_gate_states_its_own_entrypoint():
    """The prepared image is a commit of the setup container, and a commit keeps
    that container's config -- including `ENTRYPOINT ["sh"]`. Passing the shell
    as the command as well ran `sh sh -c ...`, which every gate reported as
    "sh: 0: cannot open sh: No such file"."""
    from factory.containers import ComposeRunner, DockerRunner

    run_src = inspect.getsource(ComposeRunner.execute)
    assert '"--entrypoint"' in run_src, \
        "the gate run must name its entrypoint rather than trust the image's"
    # And the commit asks for a neutral image -- which Docker does not give:
    # the committed entrypoint is still `["sh"]`. So the one other place the
    # gate service starts from that image, `up`, names its entrypoint too.
    for cls in (ComposeRunner, DockerRunner):
        src = inspect.getsource(cls.close_session)
        assert "ENTRYPOINT []" in src, f"{cls.__name__} commits a shell entrypoint"
    assert 'entry["entrypoint"] = [self.docker.shell, "-c", "sleep infinity"]' in \
        inspect.getsource(ComposeRunner._override), \
        "a stack brought up again from a committed image runs `sh sleep infinity` and exits"


def test_a_runner_declares_whether_setup_survives_it():
    """Setup is once per runner, not once per checkout.

    The gates skipped setup on every round after the first, on the reasoning
    that the checkout keeps what setup put there. It keeps `node_modules`,
    which lives in the mount, and nothing else: a container runner holds the
    toolchain in an image `down()` removes, and a database setup migrated in a
    volume `down -v` takes with it. One real run spent three repair rounds
    reporting `ruff: not found`, `mypy: not found` and `No module named pytest`
    on code that was fine, and its packet said zero of sixteen criteria were
    verified -- a fact about the harness, presented as a fact about the code.
    """
    from factory.containers import ComposeRunner, DockerRunner

    assert gates.LocalRunner.carries_setup is True, \
        "what setup installs on the host outlives the runner that installed it"
    for cls in (DockerRunner, ComposeRunner):
        assert cls.carries_setup is False, \
            f"{cls.__name__} removes its prepared image on the way down; setup cannot be skipped"

    src = inspect.getsource(pipeline.Factory._assess)
    assert "carries_setup" in src, \
        "the gates decide whether to run setup without asking whether it survived"


def test_a_harness_that_edits_nothing_is_not_narrated_as_work(tmp_path, monkeypatch):
    """The failure that cost a whole run.

    aider read the files it was given, lost the thread of its own conversation,
    asked for a task description and exited zero. Nothing checked the exit code
    and nothing checked the diff, so a reflection model was handed an empty
    change and wrote a paragraph about it -- and that paragraph became the
    round's record. Three rounds went that way, every finding was charged an
    attempt, and the packet reported the defects as tried and unfixable.
    """
    from factory.config import load_config
    from factory.executors import NO_EDITS, CommandExecutor
    from factory.llm import LLM
    from factory.schemas import Spec, WorkUnit

    repo = _harness_repo(tmp_path)
    counter = tmp_path / "runs"
    config = load_config()
    config.executor.kind = "command"
    # A harness that talks and does not write, which is the whole failure mode.
    _global_harness(config, [
        "sh", "-c",
        f"echo run >> {counter}; echo 'Please provide a task description.'",
    ])
    # The fallback is tested separately; here the question is what the record
    # says when nothing at all could build the unit.
    config.executor.fallback_to_direct = False
    executor = CommandExecutor(LLM(config), config)

    async def refuse(role_name, prompt, schema=None, **kw):
        raise AssertionError("a model was asked to account for a change that does not exist")
    monkeypatch.setattr(executor.llm, "ask", refuse)

    unit = WorkUnit(id="R-1", title="delete the duplicate class", objective="o")
    out = asyncio.run(executor.run(unit, Spec(title="t", intent="i", summary="s"),
                                   "digest", "system", repo, role="repairer"))

    assert out.files == [], "a harness that wrote nothing produced files from somewhere"
    assert NO_EDITS in out.disclosure.flags, (
        "nothing downstream can tell this apart from a repair that was simply small"
    )
    assert "exit 0" in out.summary, "the exit code is the fact, and it is not in the record"
    assert counter.read_text().count("run") == 2, (
        "the harness was not retried; one confused run ends the unit"
    )


def test_the_second_attempt_is_told_what_happened_on_the_first(tmp_path, monkeypatch):
    """A retry that repeats the same prompt gets the same silence.

    So the second run is not the same run: the harness's own bookkeeping is
    cleared first, and the task file says the previous attempt edited nothing.
    """
    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.llm import LLM
    from factory.schemas import SelfDisclosure, Spec, WorkUnit

    repo = _harness_repo(tmp_path)
    config = load_config()
    config.executor.kind = "command"
    # Writes only when its task file admits the first attempt failed.
    _global_harness(config, [
        "sh", "-c",
        "echo x > .aider.chat.history.md; "
        "grep -q 'second attempt' {task_file} && echo repaired > real.py",
    ])
    executor = CommandExecutor(LLM(config), config)

    async def fake_ask(role_name, prompt, schema=None, **kw):
        return schema(summary="s", decisions=[], disclosure=SelfDisclosure())
    monkeypatch.setattr(executor.llm, "ask", fake_ask)

    unit = WorkUnit(id="R-1", title="t", objective="o")
    out = asyncio.run(executor.run(unit, Spec(title="t", intent="i", summary="s"),
                                   "digest", "system", repo, role="repairer"))

    assert [f.path for f in out.files] == ["real.py"], (
        "the retry never happened, or it was handed the same brief as the first attempt"
    )


def test_a_repair_survives_the_model_that_cannot_describe_it(tmp_path, monkeypatch):
    """The harness writes the code; the reflection call only narrates it.

    One repairer hit its 32,000-token ceiling 277,023 characters into an answer,
    and the exception threw away a repair that was already on disk. The account
    is worth having and it is not worth the work: the retry asks for less, and
    what cannot be obtained is recorded as missing rather than invented.
    """
    from factory.config import load_config
    from factory.executors import REFLECTION_LOST, CommandExecutor
    from factory.llm import LLM, LLMError
    from factory.schemas import Spec, WorkUnit

    repo = _harness_repo(tmp_path)
    config = load_config()
    config.executor.kind = "command"
    _global_harness(config, ["sh", "-c", "echo repaired > real.py"])
    executor = CommandExecutor(LLM(config), config)

    asked: list[int] = []

    async def truncated(role_name, prompt, schema=None, **kw):
        asked.append(len(prompt))
        raise LLMError("hit its output ceiling of 32000 tokens and was cut off mid-answer")
    monkeypatch.setattr(executor.llm, "ask", truncated)

    unit = WorkUnit(id="R-1", title="t", objective="o")
    out = asyncio.run(executor.run(unit, Spec(title="t", intent="i", summary="s"),
                                   "digest", "system", repo, role="repairer"))

    assert [f.path for f in out.files] == ["real.py"], (
        "the repair was thrown away because the model could not describe it"
    )
    assert len(asked) == 2 and asked[1] < asked[0], (
        "the retry asked for the same thing again, which fails the same way"
    )
    assert REFLECTION_LOST in out.disclosure.flags and not out.decisions, (
        "a missing decision log must read as missing, never be filled in"
    )


# --------------------------------------------------------------------------
# INV: a harness that will not write is not the end of the unit
#
# `aider --architect` answered four units in prose and edited nothing: two in
# the build lane and one in each of two repair rounds. One spent 21 minutes
# asking which revision its migration should descend from -- a question its own
# brief answered two paragraphs above. Nothing reads those questions. The units
# were recorded empty, eight of twelve criteria had no implementing code, and
# the run was decided by that before a single review agent ran.
# --------------------------------------------------------------------------


def test_a_harness_that_will_not_write_falls_back_to_the_direct_executor(tmp_path, monkeypatch):
    import subprocess

    from factory.config import load_config
    from factory.executors import FELL_BACK, NO_EDITS, CommandExecutor
    from factory.llm import LLM
    from factory.schemas import Spec, WorkUnit, WorkerOutput

    repo = _harness_repo(tmp_path)
    # A file the unit owns and must return complete. Its contents are the thing
    # the fallback has to be given: without them it reconstructs from memory,
    # and on a real run that turned a 1,282-line router into 300 lines with two
    # endpoints missing.
    (repo / "existing.py").write_text("def ORIGINAL_BODY_THAT_MUST_SURVIVE():\n    return 1\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "existing"], cwd=repo, check=True)

    counter = tmp_path / "runs"
    config = load_config()
    config.executor.kind = "command"
    _global_harness(config, [
        "sh", "-c", f"echo run >> {counter}; echo 'What would you like me to change?'",
    ])
    assert config.executor.fallback_to_direct, "the fallback is off by default"
    executor = CommandExecutor(LLM(config), config)

    seen: dict[str, str] = {}

    async def answer(role_name, prompt, schema=None, **kw):
        seen["prompt"] = prompt
        return WorkerOutput(
            unit_id="U1", summary="wrote the migration",
            files=[FileWrite(path="alembic/e5f6.py", contents="revision = 'e5f6'\n")],
        )
    monkeypatch.setattr(executor.llm, "ask", answer)

    unit = WorkUnit(id="U1", title="Schema, Alembic migration, and model columns",
                    objective="add four columns",
                    files_expected=["existing.py", "alembic/e5f6.py"])
    out = asyncio.run(executor.run(unit, Spec(title="t", intent="i", summary="s"),
                                   "THE-REPO-DIGEST", "system", repo, role="worker"))

    assert counter.read_text().count("run") == 2, "the harness was not retried before falling back"
    assert [f.path for f in out.files] == ["alembic/e5f6.py"], \
        "the unit still produced nothing after the fallback"

    # Not the digest. The fallback must return whole files, so it is given whole
    # files -- anything it is not shown, it invents or drops.
    assert "ORIGINAL_BODY_THAT_MUST_SURVIVE" in seen["prompt"], \
        "the fallback was asked to return a file it was never shown"
    assert "THE-REPO-DIGEST" not in seen["prompt"], \
        "the fallback is still working from a truncated outline of the repository"
    assert "you are creating it" in seen["prompt"], \
        "a file that does not exist yet is not distinguished from one that does"

    # And a reader can tell how this was built. A unit the harness wrote and a
    # unit the fallback wrote are not equally trustworthy, and the packet has to
    # be able to say which it is looking at.
    assert FELL_BACK in out.disclosure.flags
    assert NO_EDITS in out.disclosure.flags
    assert "edited no file" in out.summary and "exit 0" in out.summary


def test_a_fallback_that_also_produces_nothing_records_the_honest_empty_unit(tmp_path, monkeypatch):
    """The fallback must not become a way to always have something to show."""
    from factory.config import load_config
    from factory.executors import FELL_BACK, NO_EDITS, CommandExecutor
    from factory.llm import LLM
    from factory.schemas import Spec, WorkUnit, WorkerOutput

    repo = _harness_repo(tmp_path)
    config = load_config()
    config.executor.kind = "command"
    _global_harness(config, ["sh", "-c", "echo 'no thanks'"])
    executor = CommandExecutor(LLM(config), config)

    async def empty(role_name, prompt, schema=None, **kw):
        return WorkerOutput(unit_id="U1", summary="I did not write anything", files=[])
    monkeypatch.setattr(executor.llm, "ask", empty)

    out = asyncio.run(executor.run(
        WorkUnit(id="U1", title="t", objective="o"),
        Spec(title="t", intent="i", summary="s"), "digest", "system", repo, role="worker"))

    assert out.files == []
    assert NO_EDITS in out.disclosure.flags
    assert FELL_BACK not in out.disclosure.flags, \
        "a fallback that wrote nothing is claiming to have built the unit"
    assert out.disclosure.not_implemented, "the unit does not say what was left undone"


# --------------------------------------------------------------------------
# INV: the coding harness is given its contract
#
# `DirectExecutor` passes its role prompt to the model on every call.
# `CommandExecutor` accepted the same `system` argument and spent it only on the
# reflection call afterwards -- so the agent that actually writes the code was
# handed a bare task and no contract at all. An interactive coding assistant
# with no role behaves like an interactive coding assistant: it asked what the
# user would like it to change, and it asked which revision a migration should
# descend from. Four units across two rounds ended that way, eight of twelve
# criteria had no implementing code, and the run was decided before a single
# review agent ran.
# --------------------------------------------------------------------------


def test_the_coding_harness_is_given_a_role_prompt(tmp_path, monkeypatch):
    """Asserted on the bytes the harness actually reads, not on the call site.

    The task file is the harness's entire input. Checking anything else would
    pass while `system` went on being dropped, which is exactly how this
    survived: the argument was there, threaded correctly, and unused.
    """
    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.llm import LLM
    from factory.schemas import Spec, WorkUnit

    repo = _harness_repo(tmp_path)
    seen = tmp_path / "task-as-the-harness-saw-it"
    config = load_config()
    config.executor.kind = "command"
    config.executor.fallback_to_direct = False
    _global_harness(config, ["sh", "-c", f"cp {{task_file}} {seen}"])
    executor = CommandExecutor(LLM(config), config)

    async def refuse(*a, **kw):
        raise AssertionError("no model call should be needed")
    monkeypatch.setattr(executor.llm, "ask", refuse)

    asyncio.run(executor.run(
        WorkUnit(id="U1", title="t", objective="o", files_expected=["a.py"]),
        Spec(title="t", intent="i", summary="s"), "digest", "SYSTEM", repo, role="worker"))

    task = seen.read_text(encoding="utf-8")
    assert "you are a coding harness" in task.lower(), \
        "the harness was handed a task with no contract attached to it"
    # The two failures verbatim, so the prompt cannot quietly stop naming them.
    assert "A question is the same as silence" in task
    assert "Your work unit: U1" in task, "the brief itself no longer reaches the harness"


def test_the_flags_are_configuration_not_literals(tmp_path):
    """An agentic harness needs none of this, and `--file` is aider's spelling.

    The command template is generic; putting one CLI's flags in the executor
    would be the same mistake as putting a model name in the code.
    """
    from factory.schemas import WorkUnit

    root = _migration_repo(tmp_path)
    unit = WorkUnit(id="U1", title="t", objective="o",
                    files_expected=["backend/app/models/trial.py"])

    off = _harness_for(tmp_path, file_flag="", read_flag="")
    assert off._context_argv(unit, root) == [], \
        "a harness that reads for itself is handed file flags anyway"

    other = _harness_for(tmp_path, file_flag="--edit", read_flag="")
    argv = other._context_argv(unit, root)
    assert argv == ["--edit", "backend/app/models/trial.py"], argv
    assert "--read" not in argv, "the read half was emitted with no flag configured"

    src = factory_source("executors")
    assert '"--file"' not in src and '"--read"' not in src, \
        "aider's flag spelling is hardcoded in the executor"


# --------------------------------------------------------------------------
# INV: the four defects the second run exposed, all introduced by the first
#      round of fixes. Each is a claim about the world made where it had not
#      been checked -- the same shape as everything else this session.
# --------------------------------------------------------------------------


def test_an_empty_file_the_harness_created_is_not_counted_as_work(tmp_path, monkeypatch):
    """`--file path/that/does/not/exist` makes aider create it, empty.

    `git status` then reports a change, the retry is skipped as unnecessary, and
    the empty file contributes nothing to the unit's output -- so a unit that
    should have had two attempts had one, and the log said "1 attempt(s)".
    """

    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.llm import LLM
    from factory.schemas import Spec, WorkUnit

    repo = _harness_repo(tmp_path)
    counter = tmp_path / "runs"
    config = load_config()
    config.executor.kind = "command"
    config.executor.fallback_to_direct = False
    # A harness that touches a file into existence and says nothing, exactly as
    # aider does when handed --file for a path that is not there yet.
    _global_harness(config, [
        "sh", "-c", f"echo run >> {counter}; mkdir -p alembic; : > alembic/new.py",
    ])
    executor = CommandExecutor(LLM(config), config)

    async def refuse(*a, **kw):
        raise AssertionError("no model call should be needed for an empty result")
    monkeypatch.setattr(executor.llm, "ask", refuse)

    out = asyncio.run(executor.run(
        WorkUnit(id="U1", title="t", objective="o", files_expected=["alembic/new.py"]),
        Spec(title="t", intent="i", summary="s"), "digest", "SYS", repo, role="worker"))

    assert counter.read_text().count("run") == 2, \
        "a placeholder file counted as work and the second attempt was skipped"
    assert out.files == [], "an empty file was returned as though it were written"
    assert "2 attempt(s)" in out.summary, "the record undercounts what was tried"


def test_the_fallback_is_shown_the_files_it_must_return_whole(tmp_path):
    """It returns complete files, so anything it is not shown comes back deleted."""
    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.llm import LLM
    from factory.schemas import WorkUnit

    (tmp_path / "r").mkdir()
    (tmp_path / "r" / "big.py").write_text("def keep_me():\n    return 1\n" * 50)
    (tmp_path / "r" / "reference.py").write_text("REFERENCE = True\n")

    config = load_config()
    config.executor.kind = "command"
    executor = CommandExecutor(LLM(config), config)
    unit = WorkUnit(id="U1", title="t", objective="o",
                    files_expected=["r/big.py", "r/new.py"],
                    read_files=["r/reference.py"])

    context = executor._fallback_context(unit, tmp_path)
    assert "def keep_me()" in context, "the file it must return whole was not shown to it"
    assert "REFERENCE = True" in context, "the reference file was not shown"
    assert "you are creating it" in context, \
        "a file that does not exist yet is not distinguished from one that does"
    assert "must return each of these COMPLETE" in context, \
        "nothing tells it that an omission is a deletion"


# --------------------------------------------------------------------------
# INV: what the harness spends is spend
#
# A coding harness is a separate process talking to the same provider on the
# same key. None of it passes through this app's client, so for as long as
# nothing recorded it the factory's cost was not an underestimate -- it was a
# different number, about a smaller thing, printed where the cost of the run
# belongs. A run reported $1.20 and the provider billed $4.44.
#
# The budget is the part that matters. Its whole job is to stop a run spending
# more than a human agreed to, and it was blind to the largest consumer.
# --------------------------------------------------------------------------


def test_the_harness_reports_what_it_spent_and_the_ledger_takes_it(tmp_path, monkeypatch):

    from factory.config import load_config
    from factory.executors import SPEND_MARKER, CommandExecutor
    from factory.llm import LLM
    from factory.schemas import Spec, WorkUnit

    repo = _harness_repo(tmp_path)
    config = load_config()
    config.executor.kind = "command"
    config.executor.fallback_to_direct = False
    payload = ('{"cost_usd": 1.42, "prompt_tokens": 120000, '
               '"completion_tokens": 8000, "model": "some/model"}')
    _global_harness(config, [
        "sh", "-c", f"echo 'work happened'; echo '{SPEND_MARKER}{payload}'; echo x > wrote.py",
    ])
    executor = CommandExecutor(LLM(config), config)

    async def reflect(role_name, prompt, schema=None, **kw):
        from factory.executors import _Reflection
        return _Reflection(summary="did it")
    monkeypatch.setattr(executor.llm, "ask", reflect)

    before = executor.llm.usage_report()["total_cost"]
    asyncio.run(executor.run(
        WorkUnit(id="U1", title="t", objective="o", files_expected=["wrote.py"]),
        Spec(title="t", intent="i", summary="s"), "digest", "SYS", repo, role="worker"))
    after = executor.llm.usage_report()

    assert after["total_cost"] - before >= 1.42, \
        "the harness spent real money and the ledger did not record it"
    assert after["roles"]["worker"]["prompt_tokens"] >= 120000
    assert "some/model" in after["roles"]["worker"]["models"]


def test_a_harness_that_produced_nothing_still_reports_its_spend(tmp_path, monkeypatch):
    """The case a budget most needs to see: money burned for no output."""
    from factory.config import load_config
    from factory.executors import SPEND_MARKER, CommandExecutor
    from factory.llm import LLM
    from factory.schemas import Spec, WorkUnit

    repo = _harness_repo(tmp_path)
    config = load_config()
    config.executor.kind = "command"
    config.executor.fallback_to_direct = False
    _global_harness(config, [
        "sh", "-c",
        f"echo 'What would you like me to change?'; echo '{SPEND_MARKER}" + '{"cost_usd": 0.9}' + "'",
    ])
    executor = CommandExecutor(LLM(config), config)

    async def refuse(*a, **kw):
        raise AssertionError("no reflection call for an empty change")
    monkeypatch.setattr(executor.llm, "ask", refuse)

    out = asyncio.run(executor.run(
        WorkUnit(id="U1", title="t", objective="o"),
        Spec(title="t", intent="i", summary="s"), "digest", "SYS", repo, role="worker"))

    assert out.files == []
    assert executor.llm.usage_report()["total_cost"] >= 0.9, \
        "a unit that burned money and produced nothing recorded no spend"
    assert "producing nothing" in out.summary, \
        "the record does not say the money bought nothing"


def test_a_harness_that_cannot_report_is_not_a_failed_unit():
    from factory.executors import harness_spend

    assert harness_spend("no marker anywhere") is None
    assert harness_spend("") is None
    # Malformed is treated as absent, never as zero-and-fine.
    assert harness_spend("FACTORY-HARNESS-SPEND {not json") is None


def test_the_two_ends_of_the_spend_marker_agree():
    """Driver and executor are different files under different interpreters."""
    from factory import executors

    driver = (ROOT / "factory" / "harnesses" / "openhands_driver.py").read_text(encoding="utf-8")
    assert f'SPEND_MARKER = "{executors.SPEND_MARKER}"' in driver, (
        "the harness prints one marker and the factory looks for another; the "
        "spend would go unrecorded and nothing would say so"
    )


def test_a_configured_command_may_contain_braces(tmp_path, monkeypatch):
    """`str.format` read every brace as a placeholder.

    A command carrying JSON -- which the spend marker does -- died with
    `KeyError: '"cost_usd"'`, an error that names neither the command nor the
    setting that holds it. Only the four known names are substituted now.
    """
    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.llm import LLM
    from factory.schemas import Spec, WorkUnit

    repo = _harness_repo(tmp_path)
    seen = tmp_path / "argv"
    config = load_config()
    config.executor.kind = "command"
    config.executor.fallback_to_direct = False
    _global_harness(config, [
        "sh", "-c", f'printf "%s\\n" "$@" > {seen}', "--",
        '{"literal": "json", "left": "alone"}', "{worktree}",
    ])
    executor = CommandExecutor(LLM(config), config)

    async def refuse(*a, **kw):
        raise AssertionError("no model call expected")
    monkeypatch.setattr(executor.llm, "ask", refuse)

    asyncio.run(executor.run(
        WorkUnit(id="U1", title="t", objective="o"),
        Spec(title="t", intent="i", summary="s"), "digest", "SYS", repo, role="worker"))

    argv = seen.read_text().splitlines()
    assert '{"literal": "json", "left": "alone"}' in argv, "braces were eaten as a placeholder"
    assert not any(a == "{worktree}" for a in argv), "a real placeholder stopped being filled"


def test_a_harness_killed_at_the_timeout_still_reports_what_it_spent(tmp_path, monkeypatch):
    """The hole the first version of the accounting left open.

    The driver prints its total after the last turn returns, and a process
    killed at `timeout_s` never gets there -- nor does its stdout buffer, which
    dies with it. Three of four units in one real run were killed that way, each
    having spent real money, and none of it was recorded. The fix verified
    against a fast task proved only that fast tasks are counted.
    """
    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.llm import LLM
    from factory.schemas import Spec, WorkUnit

    repo = _harness_repo(tmp_path)
    config = load_config()
    config.executor.kind = "command"
    config.executor.fallback_to_direct = False
    config.executor.timeout_s = 3.0
    config.executor.spend_file_flag = "--spend-file"
    # Writes its running total, edits a file, then hangs until it is killed --
    # exactly the shape of a unit that runs past the ceiling.
    # argv is: sh -c <script> -- --spend-file <path>, so inside the script
    # $1 is the flag and $2 is the path the executor chose.
    _global_harness(config, [
        "sh", "-c",
        'printf \'{"cost_usd": 2.5, "prompt_tokens": 90000, "completion_tokens": 1200}\' > "$2"; '
        'echo x > wrote.py; exec sleep 60',
        "--",
    ])
    executor = CommandExecutor(LLM(config), config)

    async def reflect(role_name, prompt, schema=None, **kw):
        from factory.executors import _Reflection
        return _Reflection(summary="did some of it")
    monkeypatch.setattr(executor.llm, "ask", reflect)

    out = asyncio.run(executor.run(
        WorkUnit(id="U1", title="t", objective="o", files_expected=["wrote.py"]),
        Spec(title="t", intent="i", summary="s"), "digest", "SYS", repo, role="worker"))

    assert [f.path for f in out.files] == ["wrote.py"], \
        "work the harness finished before the kill was thrown away"
    report = executor.llm.usage_report()
    assert report["total_cost"] >= 2.5, (
        "a harness killed at the timeout spent real money and the ledger "
        "recorded none of it"
    )
    assert report["roles"]["worker"]["prompt_tokens"] >= 90000


def test_a_timeout_keeps_what_the_harness_managed_to_say(tmp_path, monkeypatch):
    """The log is how the reflection accounts for the diff.

    Discarding it left every long-running unit explained by one sentence:
    "harness timed out".
    """
    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.llm import LLM
    from factory.schemas import Spec, WorkUnit

    repo = _harness_repo(tmp_path)
    config = load_config()
    config.executor.kind = "command"
    config.executor.fallback_to_direct = False
    config.executor.timeout_s = 3.0
    config.executor.spend_file_flag = ""
    _global_harness(config, [
        "sh", "-c", "echo DECISION_I_MADE_BEFORE_THE_KILL; echo x > wrote.py; exec sleep 60"])
    executor = CommandExecutor(LLM(config), config)

    seen: dict[str, str] = {}

    async def reflect(role_name, prompt, schema=None, **kw):
        from factory.executors import _Reflection
        seen["prompt"] = prompt
        return _Reflection(summary="s")
    monkeypatch.setattr(executor.llm, "ask", reflect)

    asyncio.run(executor.run(
        WorkUnit(id="U1", title="t", objective="o", files_expected=["wrote.py"]),
        Spec(title="t", intent="i", summary="s"), "digest", "SYS", repo, role="worker"))

    assert "timed out" in seen["prompt"], "the reflection is not told the unit was cut off"
    assert "DECISION_I_MADE_BEFORE_THE_KILL" in seen["prompt"], \
        "everything the harness said before the kill was discarded"


def test_a_route_that_runs_out_sends_the_role_somewhere_rather_than_ending_the_run():
    """The one stopping condition a subscription run actually has.

    Twice in a day a route reached its ceiling mid-run -- once at 100% of a
    five-hour window, once on a credit balance with both windows still open --
    and took the review panel with it three phases from a packet, after
    everything before it had been paid for.

    A balance is not a window: one reopens on a clock the tool will tell you
    and the other reopens when somebody pays, and the second was arriving as a
    generic failure that reads like a broken harness.
    """
    import asyncio
    from factory.llm import LLM, RouteExhausted
    from factory.schemas import ReviewReport

    cfg = config_module.load_config()
    cfg.roles["reviewer"] = cfg.roles["reviewer"].model_copy(update={
        "route": "codex", "fallback_route": "default", "fallback_model": "openai/gpt-5.6"})

    seen = []

    async def dead_cli(route, role, messages, schema):
        seen.append(route.name)
        raise RouteExhausted("route 'codex' has no credit left: out of credits")

    async def live_api(payload):
        seen.append("default")
        return {"choices": [{"message": {"content":
                '{"summary":"s","findings":[],"conceded":[]}'}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

    llm = LLM(cfg)
    llm._run_cli, llm._post = dead_cli, live_api
    got = asyncio.run(llm.ask("reviewer", "p", ReviewReport, system="s"))
    assert isinstance(got, ReviewReport), "the run died where a fallback was configured"
    assert seen == ["codex", "default"], seen

    # Never silently. The packet names which agents read the work, and one that
    # read it from a substitute is a different reading.
    assert llm.fallbacks and llm.fallbacks[0]["role"] == "reviewer"
    assert llm.fallbacks[0]["to"] == "default"
    assert "credit" in llm.fallbacks[0]["because"]
    assert pipeline.check_route_fallbacks(llm.fallbacks)[0].id == "fallback-1"
    assert pipeline.check_route_fallbacks([]) == [], "a run with no fallback says nothing"

    # And a role that names none still fails, loudly. A fallback is a choice,
    # because a substitute reviewer is a weaker reading than the configured one.
    bare = config_module.load_config()
    bare.roles["reviewer"] = bare.roles["reviewer"].model_copy(update={
        "route": "codex", "fallback_route": "", "fallback_model": "", "fallback": ""})
    plain = LLM(bare)
    plain._run_cli, plain._post = dead_cli, live_api
    with pytest.raises(RouteExhausted):
        asyncio.run(plain.ask("reviewer", "p", ReviewReport, system="s"))

    # A fallback naming a route that is not configured is no fallback: the run
    # fails with the route's own reason. It failed with a NameError, because the
    # error that says "no such route" was never imported where it is caught.
    stale = config_module.load_config()
    stale.roles["reviewer"] = stale.roles["reviewer"].model_copy(update={
        "route": "codex", "fallback_route": "a-route-since-deleted", "fallback_model": "x"})
    gone = LLM(stale)
    gone._run_cli, gone._post = dead_cli, live_api
    with pytest.raises(RouteExhausted):
        asyncio.run(gone.ask("reviewer", "p", ReviewReport, system="s"))


def test_the_agents_with_a_filesystem_fall_back_too():
    """The completion path got a fallback and the authoring path did not.

    Which is backwards by consequence. A review agent that dies costs a
    reading, and the packet says so. An oracle that dies costs the entire
    verify lane -- every criterion comes back unverified, and the packet
    reports a broken harness rather than anything about the code. The three
    agents a run cannot proceed without were the three without a way out.

    A session is a subprocess: it does not raise, it writes its refusal and
    exits having produced no file. So the signal is the log, read with the same
    patterns `llm.py` uses for the same two conditions -- a window and a
    balance -- imported rather than restated.
    """
    from factory.executors import CommandExecutor, _session_exhausted
    from factory.llm import LLM

    assert _session_exhausted("Your workspace is out of credits.")
    assert _session_exhausted("rate limit reached, try again in 42m")
    assert not _session_exhausted("SyntaxError: invalid syntax"), \
        "a broken session is being read as an exhausted one, and retried somewhere else"

    cfg = config_module.load_config()
    ex = CommandExecutor(LLM(cfg), cfg)
    for role in ("oracle", "breaker", "worker"):
        alt = ex._fallback_session(role)
        assert alt is not None, f"{role!r} writes files and has nowhere to go when its route says no"
        assert alt.authors(), \
            f"{role!r} falls back to a route with no session_command, which cannot give it a tree"
        # And the id it is handed has to mean something on the route it lands on.
        # A fallback changes the carrier as well as the model: `session_model`
        # reads the *primary* route's id and skips the provider prefix that a
        # harness spending this app's key needs. The first real use fired
        # correctly, reached the right route, and died on the raw id.
        landed = config_module.fallback_session_model(cfg, role, alt)
        assert landed, f"{role!r} falls back with no model id at all"
        if alt.kind == "api":
            host = (alt.base_url or cfg.api.base_url or "").split("//")[-1].split("/")[0]
            want = cfg.executor.model_prefixes.get(host, "")
            assert not want or landed.startswith(want), (
                f"{role!r} lands on {alt.name!r} with {landed!r}, which names no provider "
                "there -- the harness refuses it and the fallback is worth nothing")

    # It only fires on exhaustion, and only after the ordinary retry. A harness
    # that wrote nothing because the brief was wrong is not fixed by asking a
    # different vendor the same question.
    src = inspect.getsource(CommandExecutor.author)
    assert "session_refusal(" in src and "log_so_far" in src
    # And read with the route's own keys. Without them the log is scanned as
    # text, and the one thing a healthy session always prints is its gauge.
    assert "self.config.routes.get(primary_name)" in src, \
        "the session's refusal is read without the route that would explain it"
    assert src.index("_retry_note") < src.index("alt = self._fallback_session(role)"), \
        "the fallback fires before the ordinary retry, so a flaky session changes vendor"
    # And it is recorded, like every other substitution.
    assert 'getattr(self.llm, "fallbacks", [])' in src, \
        "an authoring fallback is taken without the packet ever saying so"


def test_a_role_that_reports_the_parts_is_not_a_role_that_spent_nothing():
    """Every summary here reads `total_tokens`, and one route reports the parts
    without the total.

    A review panel booked 3.78M prompt tokens across three roles -- the reviewer
    alone 2.2M -- and every one of them showed `total_tokens: 0`, because the
    figure was taken verbatim from a provider that does not send it. So the one
    role whose route does report a total looked like the only thing spending
    anything in the system, and an afternoon of tuning went at that agent while
    the panel stayed invisible.

    And a call is not a turn. `turns` fell back to the call count when a harness
    reported none, so the breaker showed 10 turns for 10 sessions of roughly a
    hundred each, beside a repairer on a reporting route showing 356 for 18
    calls. Two different facts under one name, and the fabricated one looked
    the more believable.
    """
    body = factory_source("llm")

    assert 'int(usage.get("total_tokens") or 0) or (\n            prompt_tokens + completion_tokens)' in body, \
        "a provider that reports the parts and not the total still books zero"

    ext = body[body.index("def record_external"):]
    ext = ext[:ext.index("\n    # -- the plan")]
    assert "u.turns += max(0, turns)" in ext and "or max(0, calls)" not in ext, \
        "a call is being counted as a turn when the route reports none"
    assert "u.unbilled_calls += max(0, calls)" in ext, \
        "dropping the fallback also dropped the count of unmeasurable calls"

    # Both numbers reach a reader, so they can see which routes this run could
    # account for at all.
    assert '"unbilled_calls": sum(u.unbilled_calls' in body, \
        "turns are reported without the calls they should be compared against"


def test_authoring_confines_by_reading_not_by_asking():
    """Containment used to be a refusal of a path the model typed. It is now a
    property of what gets read back, which no answer can talk its way past."""
    src = inspect.getsource(executors.CommandExecutor.author)
    # One prefix or several -- a project can have a pytest directory and a
    # vitest one -- but still a test on the path that was written, not on the
    # path that was asked for.
    assert 'rel.startswith(p + "/") for p in prefixes' in src
    assert "outside.append(rel)" in src


def test_the_harness_task_file_carries_the_role_contract():
    """A harness is launched with a task file and nothing else. A contract left
    in the `system` slot reaches the accounting call and never reaches the agent
    that does the work -- and the oracle's whole reason for existing is in it."""
    src = inspect.getsource(executors.CommandExecutor.author)
    # The contract is part of what reaches disk and part of what reaches stdin.
    # Asserted on the assembly rather than on one call, because the task now
    # carries the unit's environment note too and is built once for both.
    assert "task = contract + brief" in src
    assert "task_file.write_text(task" in src
    assert "system.strip()" in src


def test_the_harness_records_what_authoring_cost():
    """A round that spent real money and reported nothing is the hole this
    closes, and it has three channels because a kill takes them in order.

    The spend file is written as work proceeds and outlives the process; the
    marker line needs the process to reach its last print; the route's own
    envelope needs it to exit cleanly. A harness killed at the timeout has only
    the first, which is exactly why it exists.
    """
    src = inspect.getsource(executors.CommandExecutor._record_session_spend)
    assert "read_spend_file(spend_file)" in src
    assert "harness_spend(output)" in src
    assert "self.llm.record_external(" in src
    assert "billed=" in src, (
        "a session on a subscription reports a figure that is not money; "
        "counted as spend it stops a run at the reserve floor for nothing")
    # And both entry points must go through it, or one of them loses the money.
    for entry in (executors.CommandExecutor.run, executors.CommandExecutor.author):
        assert "_record_session_spend(" in inspect.getsource(entry)


def test_authoring_reads_the_files_the_harness_wrote(tmp_path):
    """The whole point, end to end: the suite is what is on disk."""
    import asyncio
    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.llm import LLM

    repo = _blank_repo(tmp_path)
    config = load_config()
    config.executor.kind = "command"
    _global_harness(config, [
        "sh", "-c",
        "mkdir -p tests/oracle && echo 'def test_a(): assert 1' > tests/oracle/test_a.py "
        "&& echo 'import pytest' > tests/oracle/conftest.py "
        "&& echo 'leak' > backend/app.py 2>/dev/null || "
        "(mkdir -p backend && echo leak > backend/app.py)",
    ])
    executor = CommandExecutor(LLM(config), config)
    session = asyncio.run(executor.author(
        brief="write the suite", tree=repo, role="oracle", confine="tests/oracle"))

    assert sorted(f.path for f in session.files) == [
        "tests/oracle/conftest.py", "tests/oracle/test_a.py"]
    assert session.files[0].contents.strip() != "", "contents come from the file"
    assert session.outside == ["backend/app.py"], \
        "a write outside the confinement must be reported, not silently dropped"
    assert session.attempts == 1


def test_authoring_refuses_a_turn_that_wrote_nothing(tmp_path):
    """Same refusal a build unit gets. A harness that wrote no file has not
    answered, whatever it printed."""
    import asyncio
    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.llm import LLM

    repo = _blank_repo(tmp_path)
    config = load_config()
    config.executor.kind = "command"
    _global_harness(config, ["sh", "-c", "echo 'what would you like me to test?'"])
    executor = CommandExecutor(LLM(config), config)
    session = asyncio.run(executor.author(
        brief="write the suite", tree=repo, role="oracle", confine="tests/oracle"))

    assert session.files == []
    assert session.attempts == 2, "a silent turn must be refused once, as in `run`"
    assert "what would you like" in session.log


def test_a_gauge_saying_the_plan_has_room_is_not_a_refusal():
    """A repairer finished, reported, and was recorded as refused.

    The row read `refused - at its usage limit` beside an envelope that said
    `is_error: false`, `subtype: success` and a paragraph of what the agent had
    done. Nothing had refused anything: the tool prints its window reading as
    an event just before the result, `rate_limit_info` contains the word a
    refusal is recognised by, and the log was being read as text.

    So the tool's own verdict decides, and its message is read only when that
    verdict says the run failed. The words still count when there is no
    verdict to read -- a session killed at the timeout is the case -- but not
    the gauge, which the route itself points at and the console draws.
    """
    from factory.executors import _session_exhausted, _session_outcome
    from factory.llm import _RATE_LIMIT_SIGNS

    route = _streamed_session_route()
    healthy = _session_stream(result="The finding was a false negative.")

    # The difficulty, stated: the words really are in a run that succeeded.
    assert _RATE_LIMIT_SIGNS.search(healthy), \
        "this fixture no longer reproduces the thing it was written for"

    assert not _session_exhausted(healthy, route)
    assert _session_outcome(0, healthy, route) == ("answered", "", "")

    # And a real one still lands, in the tool's own words.
    limited = _session_stream(
        result="Claude AI usage limit reached|1790188200", is_error=True)
    outcome, why, said = _session_outcome(0, limited, route)
    assert (outcome, why) == ("refused", "at its usage limit")
    assert "usage limit reached" in said, \
        "the row would not carry the evidence for its own verdict"

    broke = _session_stream(result="Your workspace is out of credits.", is_error=True)
    assert _session_outcome(0, broke, route)[:2] == ("refused", "out of credits")

    # A stream that never reached its envelope -- killed at the timeout -- has
    # no verdict to read, and falls back to the words. The gauge is not one of
    # them; what the tool said is.
    killed = "\n".join(healthy.splitlines()[:3])
    assert GAUGE_LINE in killed
    assert not _session_exhausted(killed, route)
    assert _session_exhausted(killed + "\nClaude AI usage limit reached|1790188200",
                              route)

    # Every place that reads a session's log for a refusal reads it with the
    # route that explains it. The oracle's fix session is the third: read as
    # text, a fix that needed no file change was written down as a session that
    # never ran, and the round got its allowance back for work it had done.
    revise = inspect.getsource(pipeline.Factory._revise_blind_suite)
    assert "_session_exhausted(session.log, oracle_route)" in revise

    # Nothing here is claude's. Every route that reports a window reports it
    # under a key of its own, and the other one here calls it `rate_limits`.
    cfg = config_module.load_config()
    for name, other in cfg.routes.items():
        key = (other.meter_windows_key or "").split(".")[0]
        if not key:
            continue
        reading = f'{{"type":"gauge","{key}":{{"status":"allowed"}}}}'
        assert not _session_exhausted(
            _session_stream(result="done").replace(GAUGE_LINE, reading), other), \
            f"route {name!r} reads its own gauge as a refusal"


def test_an_empty_file_is_not_a_written_test(tmp_path):
    """A placeholder touched into existence would both suppress the retry and
    contribute a zero-byte test to the suite."""
    import asyncio
    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.llm import LLM

    repo = _blank_repo(tmp_path)
    config = load_config()
    config.executor.kind = "command"
    _global_harness(config, [
        "sh", "-c", "mkdir -p tests/oracle && touch tests/oracle/test_empty.py"])
    executor = CommandExecutor(LLM(config), config)
    session = asyncio.run(executor.author(
        brief="b", tree=repo, role="oracle", confine="tests/oracle"))

    assert session.files == []
    assert session.attempts == 2


def test_the_task_file_is_not_collected_as_one_of_the_tests(tmp_path):
    """It is written into the tree the agent works in, so git reports it."""
    import asyncio
    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.llm import LLM

    repo = _blank_repo(tmp_path)
    config = load_config()
    config.executor.kind = "command"
    _global_harness(config, [
        "sh", "-c", "mkdir -p tests/oracle && echo 'assert 1' > tests/oracle/t.py"])
    executor = CommandExecutor(LLM(config), config)
    session = asyncio.run(executor.author(
        brief="b", tree=repo, role="oracle", confine="tests/oracle"))

    assert [f.path for f in session.files] == ["tests/oracle/t.py"]
    assert ".factory-task.md" not in session.outside


def test_the_oracle_can_write_a_suite_into_a_blank_tree():
    """BlankTree and `author` composed, which is the real verify-lane path.

    Separately tested pieces that have never been run together is how the
    `--file` regression got in, so this drives both.
    """
    import asyncio
    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.llm import LLM
    from factory.workspace import BlankTree

    config = load_config()
    config.executor.kind = "command"
    # A stand-in oracle: writes a suite, and also looks around for the code.
    _global_harness(config, [
        "sh", "-c",
        "ls -A > /dev/null; mkdir -p tests/oracle && "
        "echo 'def test_slot(): assert True' > tests/oracle/test_slots.py",
    ])
    executor = CommandExecutor(LLM(config), config)

    with BlankTree(label="oracle-t") as tree:
        tree.assert_blank()
        assert tree.path is not None
        # There is nothing here to read. That is the invariant, on disk.
        assert [p.name for p in tree.path.iterdir()] == [".git"]
        session = asyncio.run(executor.author(
            brief="write the suite", tree=tree.path, role="oracle",
            confine="tests/oracle", system="# oracle\n\nyou have not seen the code"))

    assert [f.path for f in session.files] == ["tests/oracle/test_slots.py"]
    assert "assert True" in session.files[0].contents


def test_the_role_contract_reaches_the_agent_not_just_the_accounting_call():
    """A harness gets a task file and nothing else. Asserted by reading the file
    the harness was actually handed."""
    import asyncio
    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.llm import LLM
    from factory.workspace import BlankTree

    config = load_config()
    config.executor.kind = "command"
    _global_harness(config, [
        "sh", "-c",
        "mkdir -p tests/oracle && cp .factory-task.md tests/oracle/seen.txt"])
    executor = CommandExecutor(LLM(config), config)

    with BlankTree() as tree:
        assert tree.path is not None
        session = asyncio.run(executor.author(
            brief="THE-BRIEF", tree=tree.path, role="oracle",
            confine="tests/oracle", system="THE-ORACLE-CONTRACT"))

    seen = session.files[0].contents
    assert "THE-ORACLE-CONTRACT" in seen, "the role's contract never reached the agent"
    assert "THE-BRIEF" in seen
    assert "you are a coding harness" in seen, "the harness contract was dropped"


def test_the_prompt_goes_on_stdin_not_the_command_line(tmp_path):
    """A prompt is the one input with no bound -- a repo digest runs to 100kB,
    and argv does not."""
    src = inspect.getsource(llm.LLM._communicate)
    assert "proc.communicate(body.encode())" in src
    assert "stdin=asyncio.subprocess.PIPE" in src
    assert "--interactive" in inspect.getsource(agentbox_module.Box.exec_argv), \
        "a completion in its container gets no stdin, so no prompt"


def test_a_cli_that_fails_with_exit_zero_is_still_a_failure(tmp_path):
    """A `claude -p` whose token had expired returned exit 0 with a 401 in the
    body and `is_error: true` beside it. Read by exit code, that is a successful
    completion of the string 'API Error: 401'."""
    import asyncio
    from factory.llm import LLMError
    from factory.schemas import ScoutReport

    llm = _routed(tmp_path, mode="error")
    with pytest.raises(LLMError) as caught:
        asyncio.run(llm.ask("scout", "look around", ScoutReport))
    assert "401" in str(caught.value)


def test_a_cli_that_prints_prose_is_not_read_as_an_answer(tmp_path):
    import asyncio
    from factory.llm import LLMError
    from factory.schemas import ScoutReport

    llm = _routed(tmp_path, mode="not-json")
    with pytest.raises(LLMError) as caught:
        asyncio.run(llm.ask("scout", "look around", ScoutReport))
    assert "did not return JSON" in str(caught.value)
    assert "command not found" in str(caught.value), "the tail must reach the human"


def test_a_jsonl_route_reads_the_answer_off_the_last_event(tmp_path):
    """`codex exec --json` has no flag to make it print one envelope -- it
    prints one JSON object per line and the answer is whichever
    `item.completed` came last. `_run_cli` must fold the stream rather than
    reject it the way a single malformed JSON blob would be."""
    import asyncio
    from factory.config import RoleConfig
    from factory.llm import LLM

    route = _jsonl_route(tmp_path)
    llm = LLM(None)  # type: ignore[arg-type]
    role = RoleConfig(name="scout", model="m")
    response = asyncio.run(llm._run_cli(
        route, role, [{"role": "user", "content": "hi"}], None))
    assert response["choices"][0]["message"]["content"] == "ok"
    assert response["usage"]["prompt_tokens"] == 11
    assert response["usage"]["completion_tokens"] == 3


def test_a_jsonl_routes_failure_is_read_from_the_last_event_too(tmp_path):
    """`turn.failed` arrives after an unrelated `item.completed` error item and
    a bare `error` event earlier in the same stream. Folding by last-write-wins
    must land on `turn.failed`'s reason, not the first thing on the page that
    happens to be named `error`."""
    import asyncio
    from factory.config import RoleConfig
    from factory.llm import LLM, LLMError

    route = _jsonl_route(tmp_path, mode="fail")
    llm = LLM(None)  # type: ignore[arg-type]
    role = RoleConfig(name="scout", model="m")
    with pytest.raises(LLMError) as caught:
        asyncio.run(llm._run_cli(
            route, role, [{"role": "user", "content": "hi"}], None))
    assert "the model is not supported" in str(caught.value)


def test_a_line_that_is_not_json_does_not_sink_the_whole_stream():
    """Recovery, not parsing -- matching `extract_json` above. One noisy line
    should not turn a real answer into 'did not return JSON'."""
    from factory.llm import _merge_jsonl

    stdout = "\n".join([
        '{"type": "thread.started"}',
        "not json at all",
        '{"type": "item.completed", "item": {"text": "ok"}}',
    ])
    assert _merge_jsonl(stdout)["item"]["text"] == "ok"


def test_a_plans_figure_never_reaches_the_ledger_as_a_cost(tmp_path):
    """The same claim as the test above, made about one row instead of a tally.

    `_account` splits money from notional and the usage report was right all
    along; the ledger row is written from the envelope and skipped the split,
    so a column headed Cost carried $6.16 of a run nobody was charged for. It
    also carried it unevenly: `claude -p` reports a figure per call and `codex`
    reports none, so the same subscription read as one vendor billing and
    another giving it away."""
    import asyncio
    from factory.llm import journal_calls, stop_journaling
    from factory.schemas import ScoutReport

    rows: list[dict] = []
    token = journal_calls(rows.append)
    try:
        asyncio.run(_routed(tmp_path, "costly").ask("scout", "look around", ScoutReport))
    finally:
        stop_journaling(token)

    assert len(rows) == 1, "one exchange is one row"
    row = rows[0]
    assert row["billed"] is False, "a route metered in turns is a plan"
    assert row["cost_usd"] == 0.0, "and a plan's turn is not money"
    assert row["notional_usd"] == 0.125, "the figure is kept, under a name that says what it is"


def test_a_call_on_a_billed_route_still_records_its_cost():
    """The other half, and the reason the split is read off the route rather
    than assumed: a response with no route of its own came from the API route,
    which is billed per token, and its figure is money."""
    from factory.llm import call_entry

    row = call_entry(role="scout", route="default", asked="m", outcome="answered",
                     started=time.time(), response={"usage": {"cost": 0.25}})
    assert row["billed"] is True
    assert row["cost_usd"] == 0.25 and row["notional_usd"] == 0.0


def test_an_empty_placeholder_takes_its_flag_with_it():
    """`--json-schema ''` is not the same as omitting the flag; some CLIs reject
    it outright."""
    from factory.llm import _drop_empty_pairs

    template = ["claude", "--model", "{model}", "--json-schema", "{schema}", "-p"]
    argv = ["claude", "--model", "opus-5", "--json-schema", "", "-p"]
    assert _drop_empty_pairs(argv, template) == ["claude", "--model", "opus-5", "-p"]


def test_an_oversized_argv_names_the_field_that_overran(tmp_path):
    """Past the platform limit the failure is `OSError: [Errno 7]`, which names
    neither the role nor the thing that was too long."""
    import asyncio
    from factory.llm import LLMError
    from factory.schemas import ScoutReport

    llm = _routed(tmp_path)
    with pytest.raises(LLMError) as caught:
        asyncio.run(llm.ask("scout", "look", ScoutReport, system="x" * 400_000))
    assert "schema_in_prompt" in str(caught.value), "the error must offer the way out"


def test_a_file_writing_role_can_be_pointed_at_a_subscription_harness():
    """The point of step 3. A worker on a route with a `session_command` runs
    there; the guard that used to refuse it must now pass."""
    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.llm import LLM

    cfg = load_config(str(Path("factory.yaml")))
    assert cfg.route("claude-code").authors(), \
        "the route declares no session command, so no worker can run on it"

    cfg.roles["worker"].route = "claude-code"
    cfg.roles["worker"].model = "sonnet"
    assert cfg.route_problem("worker", "author") == ""

    executor = CommandExecutor(LLM(cfg), cfg)
    argv, route = executor._session_argv("worker")
    assert route is not None and route.name == "claude-code"
    assert argv[0] == "claude", "the worker would still launch the global harness"
    # `acceptEdits`, because nobody is at the other end: a permission prompt is
    # the same failure as a question -- the turn ends and nothing is written.
    assert "--permission-mode" in argv
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"


def test_a_role_without_a_route_still_uses_the_global_harness():
    """Every config written before routes existed names none, and must keep
    building exactly as it did."""
    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.llm import LLM

    cfg = load_config(str(Path("factory.yaml")))
    # Set here rather than borrowed from whichever role happens to be unrouted
    # in the shipped config: the property under test is "no route means the
    # global harness", and a test that depended on today's assignment would
    # start passing for the wrong reason the moment somebody re-routed a role.
    cfg.roles["integrator"].route = ""
    executor = CommandExecutor(LLM(cfg), cfg)
    argv, route = executor._session_argv("integrator")
    assert route is None
    assert argv == cfg.executor.command


def test_the_default_route_is_a_description_not_a_second_copy():
    """Its `session_command` is snapshotted when the config loads. Honouring it
    would silently ignore any later change to `executor.command` -- which is how
    a stand-in harness set after load launched the real one instead, and hung
    the test suite."""
    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.llm import LLM

    cfg = load_config(str(Path("factory.yaml")))
    executor = CommandExecutor(LLM(cfg), cfg)
    _global_harness(cfg, ["sh", "-c", "echo stand-in"])
    argv, route = executor._session_argv("worker")
    assert route is None
    assert argv == ["sh", "-c", "echo stand-in"], \
        "the executor read a snapshot instead of the live command"


def test_a_harness_told_no_task_file_is_fed_on_stdin():
    """Two conventions and no way to guess: some tools take a path and read it,
    others take the prompt. A brief is the one input with no bound -- a repo
    digest runs past 100kB and argv does not -- so the fallback is stdin."""
    from factory.executors import CommandExecutor

    assert CommandExecutor._task_stdin(["tool", "--task", "{task_file}"], "BRIEF") == ""
    assert CommandExecutor._task_stdin(["tool", "-p"], "BRIEF") == "BRIEF"


def test_a_missing_tool_is_told_apart_from_one_nobody_signed_into():
    """Three failures, three sentences. Run together as 'route unavailable',
    none of them tells a human what to do next."""
    import asyncio
    from factory.llm import LLM
    from factory.routes import RouteMonitor

    cfg = _route_cfg()
    status = asyncio.run(RouteMonitor(cfg, LLM(cfg)).refresh("probe"))
    assert status["state"] == "missing"
    assert status["installed"] is False
    assert "npm i -g thing" in status["detail"], \
        "a missing tool must arrive with the line that installs it"


def test_an_auth_failure_is_read_as_an_auth_failure(tmp_path):
    """The real case: `claude -p` with an expired token exits 0, sets
    `is_error`, and puts a 401 in the body."""
    import asyncio
    from factory.llm import LLM
    from factory.routes import RouteMonitor

    script = tmp_path / "cli.py"
    script.write_text(
        "import json;print(json.dumps({'result':"
        "'API Error: 401 OAuth access token has expired','is_error':True}))",
        encoding="utf-8")
    cfg = _route_cfg(command=[sys.executable, str(script)],
                     error_key="is_error", result_key="result",
                     version_command=[sys.executable, "--version"])
    status = asyncio.run(RouteMonitor(cfg, LLM(cfg)).refresh("probe"))
    assert status["state"] == "unauthenticated"
    assert "nobody is signed in" in status["detail"]
    assert "thing login" in status["detail"], \
        "an unauthenticated route must arrive with the command that fixes it"


def test_a_working_route_says_it_cannot_be_billed(tmp_path):
    """A subscription's ceiling is a rolling window, not a balance. A card that
    said only 'connected' would leave a human thinking the budget guard covers
    agents it cannot see."""
    import asyncio
    from factory.llm import LLM
    from factory.routes import RouteMonitor

    script = tmp_path / "cli.py"
    script.write_text(
        "import json,sys;sys.stdin.read();"
        "print(json.dumps({'result':'ok','is_error':False,'num_turns':1}))",
        encoding="utf-8")
    cfg = _route_cfg(command=[sys.executable, str(script)], error_key="is_error",
                     version_command=[sys.executable, "--version"])
    status = asyncio.run(RouteMonitor(cfg, LLM(cfg)).refresh("probe"))
    assert status["state"] == "ok" and status["usable"] is True
    assert "budget guard" in status["detail"], \
        "nothing warns that this route's spend is invisible to the guard"


def test_an_unchecked_route_is_not_drawn_as_a_failure():
    """`connected: None` is not `connected: False`. A human who sees a red lamp
    they did not cause goes looking for a problem that is not there."""
    import asyncio
    from factory.config import Config, RoleConfig, RouteConfig
    from factory.llm import LLM
    from factory.routes import RouteMonitor

    cfg = Config(
        roles={"scout": RoleConfig(name="scout", model="m", route="probe")},
        routes={"probe": RouteConfig(name="probe", kind="cli",
                                     command=[sys.executable], models=["m"])},
    )
    rows = asyncio.run(RouteMonitor(cfg, LLM(cfg)).survey())
    probe = next(r for r in rows if r["name"] == "probe")
    assert probe["installed"] is True
    assert probe["connected"] is None, "not checked is not the same as failed"
    assert probe["state"] == "unknown"


def test_an_account_refusing_a_call_is_not_a_broken_harness(tmp_path):
    """The bug: a probe caught the workspace out of credits, that answer was
    written to disk, and the card read NOT WORKING for four hours beside two
    plan gauges from the same account reading 4% and 1%.

    An account saying no is its own state, with a time on it. The program is
    installed, signed in and working, and the console draws it green."""
    import asyncio
    from factory.llm import LLM
    from factory.routes import RouteMonitor

    script = tmp_path / "cli.py"
    script.write_text(
        "import json,sys;sys.stdin.read();"
        "print(json.dumps({'result':'','is_error':"
        "{'message':'Your workspace is out of credits.'}}))",
        encoding="utf-8")
    cfg = _route_cfg(tmp_path, command=[sys.executable, str(script)],
                     error_key="is_error", version_command=[sys.executable, "--version"])
    status = asyncio.run(RouteMonitor(cfg, LLM(cfg)).refresh("probe"))
    assert status["state"] == "limited", \
        "an account out of credit is being reported as a broken harness"
    assert status["last_refusal"]["why"] == "out of credits"
    assert status["retry_at"] > status["checked_at"], \
        "a refusal with no expiry is a verdict that outlives the fact"
    assert status["installed"] is True


def test_a_refusal_stops_being_the_verdict_once_its_hold_runs_out(tmp_path):
    """The hold is the one a run already honours, so the card and the run agree
    about when the account is worth asking again. Past it the state goes back to
    'nobody has asked' -- not to 'connected', because nobody has."""
    import asyncio
    import time
    from factory.llm import LLM
    from factory.routes import RouteMonitor

    cfg = _route_cfg(tmp_path, command=[sys.executable], models=["m"],
                     version_command=[sys.executable, "--version"])
    monitor = RouteMonitor(cfg, LLM(cfg))
    held = routes_mod.base_status(cfg.route("probe"))
    held.state, held.connected = "limited", True
    held.checked_at = time.time() - 7200
    held.retry_at = time.time() - 3600
    held.last_refusal = {"why": "out of credits", "at": held.checked_at}
    monitor._checked["probe"] = held

    row = next(r for r in asyncio.run(monitor.survey()) if r["name"] == "probe")
    assert row["state"] == "unknown", "an expired refusal is still being shown as the state"
    assert row["last_refusal"]["why"] == "out of credits", \
        "the refusal itself must survive the state it set -- it is why last night stopped"


def test_a_call_answered_since_the_refusal_clears_it(tmp_path):
    """The account's own log saying a later call named no limit is the account
    saying the refusal is over. That reading is free; waiting for a human to
    press Check is not."""
    import asyncio
    import time
    from factory.llm import LLM
    from factory.routes import RouteMonitor

    cfg = _route_cfg(tmp_path, command=[sys.executable], models=["m"],
                     version_command=[sys.executable, "--version"])
    monitor = RouteMonitor(cfg, LLM(cfg))
    held = routes_mod.base_status(cfg.route("probe"))
    held.state, held.connected = "limited", True
    held.checked_at = time.time() - 600
    held.retry_at = time.time() + 3600          # the hold has NOT run out
    held.last_refusal = {"why": "out of credits", "at": held.checked_at}
    monitor._checked["probe"] = held
    monitor._meters.limit = lambda name: {"reached": "", "observed_at": time.time()}

    row = next(r for r in asyncio.run(monitor.survey()) if r["name"] == "probe")
    assert row["state"] == "unknown", \
        "the tool has answered a call since and the card still says it is refusing"


def test_a_route_names_its_harness_and_its_account_apart():
    """One card showed '8% of five hours left' under a CLI's name, which is a
    property of the subscription behind it. Reinstalling the program does not
    reset a window, and the two are drawn apart."""
    from factory.config import load_config

    cfg = load_config(str(Path("factory.yaml")))
    for name, route in cfg.routes.items():
        status = routes_mod.base_status(route)
        assert status.harness_label and status.account_label, \
            f"{name} names neither half of what it is"
        assert status.account_kind in ("subscription", "key")
    default = routes_mod.base_status(cfg.route("default"))
    assert default.harness_label != default.account_label, \
        "the default route is a driver spending a provider key: two names, not one"


def test_a_refusal_stored_as_a_broken_harness_is_re_read_on_the_way_in(tmp_path):
    """The store outlives the process, so the record this split exists to stop
    showing would otherwise survive the upgrade that fixes it: a human would
    have to press Check on a route that was never broken."""
    import json
    import time
    from factory.llm import LLM
    from factory.routes import RouteMonitor, route_fingerprint

    cfg = _route_cfg(tmp_path, command=[sys.executable], models=["m"],
                     version_command=[sys.executable, "--version"])
    store = tmp_path / "store" / "routes.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps({"routes": {"probe": {
        "name": "probe", "state": "error", "connected": False,
        "checked_at": time.time() - 14400,
        "detail": ("route 'probe' has no credit left: Your workspace is out of "
                   "credits. Ask your workspace owner to refill."),
        "fingerprint": route_fingerprint(cfg.route("probe")),
    }}}), encoding="utf-8")

    held = RouteMonitor(cfg, LLM(cfg))._checked["probe"]
    assert held.state == "limited", \
        "an account out of credit is still being read back as a broken harness"
    assert held.last_refusal["why"] == "out of credits"


def test_a_stored_error_that_is_a_real_error_stays_one(tmp_path):
    """Only the message decides. A route that genuinely failed is still red."""
    import json
    import time
    from factory.llm import LLM
    from factory.routes import RouteMonitor, route_fingerprint

    cfg = _route_cfg(tmp_path, command=[sys.executable], models=["m"],
                     version_command=[sys.executable, "--version"])
    store = tmp_path / "store" / "routes.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps({"routes": {"probe": {
        "name": "probe", "state": "error", "connected": False,
        "checked_at": time.time() - 60,
        "detail": "route 'probe' reported a failure: could not parse its own envelope",
        "fingerprint": route_fingerprint(cfg.route("probe")),
    }}}), encoding="utf-8")

    assert RouteMonitor(cfg, LLM(cfg))._checked["probe"].state == "error"


def test_a_stored_check_does_not_outlive_the_names_in_the_config(tmp_path):
    """Measured on the running console: routes named after their last check was
    stored came back from the cache with no account name at all, so one card in
    the row could not say what it spends. The labels are config, not findings."""
    import asyncio
    import json
    import time
    from factory.llm import LLM
    from factory.routes import RouteMonitor, route_fingerprint

    cfg = _route_cfg(tmp_path, command=[sys.executable], models=["m"],
                     version_command=[sys.executable, "--version"],
                     harness_label="Thing CLI", account_label="Thing Inc",
                     account_kind="subscription")
    store = tmp_path / "store" / "routes.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps({"routes": {"probe": {
        "name": "probe", "state": "ok", "connected": True,
        "checked_at": time.time() - 600,
        "fingerprint": route_fingerprint(cfg.route("probe")),
    }}}), encoding="utf-8")

    row = next(r for r in asyncio.run(RouteMonitor(cfg, LLM(cfg)).survey())
               if r["name"] == "probe")
    assert row["harness_label"] == "Thing CLI"
    assert row["account_label"] == "Thing Inc", \
        "a cached check is hiding the name the config gives this account"


def test_a_survey_spends_nothing():
    """Opening a screen is not consent to spend a turn against a real plan."""
    src = inspect.getsource(routes_mod.RouteMonitor.survey)
    assert "_run_cli" not in src and "check_connection" not in src, \
        "the page load runs a real completion, which costs a turn every visit"
    assert "check_installed" in src, "it must still answer 'is this thing here'"


def test_the_connection_check_goes_down_the_path_a_real_call_takes():
    """A probe that built its own argv would happily pass while every actual
    call failed -- which is the whole class of bug this check exists to catch."""
    src = inspect.getsource(routes_mod.check_connection)
    assert "llm._run_cli(" in src


def test_an_api_route_offers_the_models_this_project_already_uses():
    """Otherwise the picker is a one-way door: a role moved onto a subscription
    could never move back, because the model it used to run on is in no list."""
    import asyncio
    from factory.config import load_config
    from factory.llm import LLM
    from factory.routes import RouteMonitor

    cfg = load_config(str(Path("factory.yaml")))
    # The shipped config routes every role away from the api route, which is the
    # case this exists for: without it that route offers nothing and a role
    # moved onto a subscription could never be moved back.
    cfg.roles["worker"].route = ""
    rows = asyncio.run(RouteMonitor(cfg, LLM(cfg)).survey())
    default = next(r for r in rows if r["name"] == "default")
    assert cfg.role("worker").model in default["models"]


def test_a_billed_route_still_counts_against_the_budget(tmp_path):
    """The guard must keep working for the routes it can actually see."""
    import asyncio
    from factory.config import Config, RoleConfig
    from factory.llm import LLM
    from factory.schemas import ScoutReport

    cfg = Config(roles={"scout": RoleConfig(name="scout", model="m")})
    llm = LLM(cfg)

    async def fake_post(payload):
        return {"choices": [{"message": {"content": json.dumps({
                    "summary": "ok", "stack": [], "conventions": [],
                    "existing_capabilities": [], "do_not_duplicate": [],
                    "relevant_files": [], "risks": []})}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.25}}
    llm._post = fake_post
    asyncio.run(llm.ask("scout", "look", ScoutReport))
    assert llm.usage_report()["total_cost"] == 0.25
    assert llm.usage_report()["notional_cost"] == 0.0


def test_a_tool_too_old_for_a_model_is_its_own_state(tmp_path):
    """Four states, not three. `claude-fable-5-1` on an eighteen-month-old
    install returned 400 `claude_code_version_too_old` -- installed, signed in,
    and simply behind. Read as any of the other three it sends a human to their
    credentials or their config instead of to `claude update`.
    """
    import asyncio
    from factory.llm import LLM
    from factory.routes import RouteMonitor

    script = tmp_path / "cli.py"
    script.write_text(
        "import json,sys;sys.stdin.read();print(json.dumps({'is_error':True,'result':"
        "'API Error: 400 {\"type\":\"error\",\"error\":{\"type\":"
        "\"invalid_request_error\",\"message\":\"Claude Code 2.0.72 does not support "
        "this model; version 2.1.251 or newer is required. Run \\'claude update\\', "
        "then try again.\"}}'}))", encoding="utf-8")
    cfg = _route_cfg(command=[sys.executable, str(script)], error_key="is_error",
                     version_command=[sys.executable, "--version"])
    status = asyncio.run(RouteMonitor(cfg, LLM(cfg)).refresh("probe"))
    assert status["state"] == "outdated", \
        "a version problem was reported as something a human cannot act on"
    # And what is shown is the tool's own sentence, not the HTTP envelope.
    assert status["detail"].startswith("Claude Code 2.0.72 does not support")
    assert "claude update" in status["detail"]


def test_an_outdated_tool_is_not_mistaken_for_an_auth_failure():
    """The two patterns overlap: a version message contains 'required' and
    'requires', and the auth pattern matches 'expired'. Ordering decides, so
    the ordering is asserted."""
    from factory.routes import _OUTDATED_SIGNS

    message = ("Claude Code 2.0.72 does not support this model; version 2.1.251 "
               "or newer is required. Run 'claude update'.")
    assert _OUTDATED_SIGNS.search(message)
    src = inspect.getsource(routes_mod.check_connection)
    assert src.index("_OUTDATED_SIGNS") < src.index("_AUTH_SIGNS"), \
        "the auth pattern is tested first and will claim version errors"


def test_no_model_named_means_no_model_flag_sent():
    """An empty `--model ''` is not the same as omitting it."""
    from factory.config import RouteConfig
    from factory.llm import cli_argv

    route = RouteConfig(name="cli", kind="cli", default_model_ok=True,
                        command=["tool", "-p", "--model", "{model}", "--json"])
    assert cli_argv(route, "", "", "") == ["tool", "-p", "--json"]
    assert cli_argv(route, "m1", "", "") == ["tool", "-p", "--model", "m1", "--json"]


def test_the_model_that_answered_is_recorded_not_the_one_configured(tmp_path):
    """A short alias resolves to whatever the installed tool points at, which on
    a real machine was a generation behind the newest model the same API would
    serve. And a role that named no model asked for nothing at all -- booked as
    an empty string, the packet would say a phase ran on nothing."""
    import asyncio
    from factory.schemas import ScoutReport

    script = tmp_path / "cli.py"
    script.write_text(
        "import json,sys;sys.stdin.read();print(json.dumps({"
        "'result': json.dumps({'summary':'ok','stack':[],'conventions':[],"
        "'existing_capabilities':[],'do_not_duplicate':[],'relevant_files':[],'risks':[]}),"
        "'is_error': False, 'num_turns': 1,"
        "'modelUsage': {'the-model-that-actually-ran': {}}}))",
        encoding="utf-8")
    llm = _routed(tmp_path)
    route = llm.config.route("claude-code")
    route.command = [sys.executable, str(script)]
    route.resolved_model_key = "modelUsage"
    route.default_model_ok = True
    llm.config.role("scout").model = ""

    asyncio.run(llm.ask("scout", "look", ScoutReport))
    assert llm.usage_report()["roles"]["scout"]["models"] == [
        "the-model-that-actually-ran"]


def test_a_role_with_no_model_never_books_an_empty_string(tmp_path):
    """Where the route reports nothing, the ledger must still say something a
    human can read."""
    import asyncio
    from factory.schemas import ScoutReport

    script = tmp_path / "cli.py"
    script.write_text(
        "import json,sys;sys.stdin.read();print(json.dumps({"
        "'result': json.dumps({'summary':'ok','stack':[],'conventions':[],"
        "'existing_capabilities':[],'do_not_duplicate':[],'relevant_files':[],'risks':[]}),"
        "'is_error': False}))", encoding="utf-8")
    llm = _routed(tmp_path)
    llm.config.route("claude-code").command = [sys.executable, str(script)]
    llm.config.route("claude-code").default_model_ok = True
    llm.config.role("scout").model = ""

    asyncio.run(llm.ask("scout", "look", ScoutReport))
    assert llm.usage_report()["roles"]["scout"]["models"] == ["(the route's own default)"]


def test_the_connection_check_does_not_fail_on_a_stale_model_id():
    """The check proves the route works. Asking it with a configured model
    conflates 'this harness is connected' with 'this id is still valid' -- and
    a working route once reported itself broken for the second reason."""
    src = inspect.getsource(routes_mod.check_connection)
    assert 'model=("" if route.default_model_ok' in src


def test_a_connection_check_outlives_the_process_that_made_it(tmp_path):
    """"Claude Code is installed and signed in" is a fact about the machine, and
    it outlived the process that measured it. Held in memory, a server restart
    sent every lamp back to amber and asked a human to re-authorise something
    they had authorised ten minutes earlier -- while nothing about the answer
    had changed.
    """
    import asyncio
    from factory.llm import LLM
    from factory.routes import RouteMonitor

    script = tmp_path / "cli.py"
    script.write_text(
        "import json,sys;sys.stdin.read();print(json.dumps({'result':'ok','is_error':False}))",
        encoding="utf-8")
    cfg = _route_cfg(tmp_path, command=[sys.executable, str(script)],
                     error_key="is_error",
                     version_command=[sys.executable, "--version"])

    first = RouteMonitor(cfg, LLM(cfg))
    assert asyncio.run(first.refresh("probe"))["state"] == "ok"

    # A new process, reading only what is on disk.
    second = RouteMonitor(cfg, LLM(cfg))
    probe = next(r for r in asyncio.run(second.survey()) if r["name"] == "probe")
    assert probe["state"] == "ok", "the check did not survive a restart"
    assert probe["checked_at"] > 0, "the age must survive too, or it cannot be shown"


def test_a_reconfigured_route_discards_the_answer_about_the_old_one(tmp_path):
    """A stored check describes the tool it was taken against. Point the route
    at a different binary and the old answer is about something else -- which
    is the difference between a stale green lamp and a wrong one.

    Narrow on purpose: a changed *flag* keeps the check, because the check
    established that this tool is installed and its credentials work, and
    neither stops being true when an argument changes."""
    import asyncio
    from factory.llm import LLM
    from factory.routes import RouteMonitor

    script = tmp_path / "cli.py"
    script.write_text(
        "import json,sys;sys.stdin.read();print(json.dumps({'result':'ok','is_error':False}))",
        encoding="utf-8")
    cfg = _route_cfg(tmp_path, command=[sys.executable, str(script)],
                     error_key="is_error",
                     version_command=[sys.executable, "--version"])
    asyncio.run(RouteMonitor(cfg, LLM(cfg)).refresh("probe"))

    cfg.route("probe").command = ["/opt/some-other-tool", "-c", "print('x')"]
    reloaded = RouteMonitor(cfg, LLM(cfg))
    probe = next(r for r in asyncio.run(reloaded.survey()) if r["name"] == "probe")
    assert probe["state"] == "unknown", \
        "a check of the old command was shown as a check of the new one"


def test_the_route_store_never_takes_the_console_down(tmp_path):
    """Losing the agents screen because a directory is read-only would be worse
    than not remembering."""
    from factory.llm import LLM
    from factory.routes import RouteMonitor

    cfg = _route_cfg()
    cfg.paths.evidence = "/proc/nonexistent-and-unwritable"
    monitor = RouteMonitor(cfg, LLM(cfg))
    monitor._checked["probe"] = routes_mod.base_status(cfg.route("probe"))
    monitor._save()   # must not raise


def test_one_harness_s_flags_are_never_appended_to_another():
    """The spend flag belongs to a particular CLI. Appended to another it is a
    usage error, and the exit code is indistinguishable from a harness that
    looked at the work and declined it."""
    from pathlib import Path as _P
    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.llm import LLM

    cfg = load_config(str(Path("factory.yaml")))
    cfg.roles["worker"].route = "claude-code"
    cfg.executor.spend_file_flag = "--spend-file"   # the global harness understands it
    executor = CommandExecutor(LLM(cfg), cfg)
    _, route = executor._session_argv("worker")

    assert executor._spend_argv(_P("/tmp/s.json"), route) == [], \
        "a flag this harness does not understand was appended to its command"
    assert executor._spend_argv(_P("/tmp/s.json"), None) == [
        cfg.executor.spend_file_flag, "/tmp/s.json"], \
        "the harness that does understand it stopped being told"


def test_the_ledger_records_a_model_name_not_a_usage_dump():
    """One tool reports the resolved model as a map of model to usage. Passed
    through `str()` the whole repr lands in the ledger's model column."""
    src = inspect.getsource(executors.CommandExecutor._record_session_spend)
    assert 'if isinstance(model, dict):' in src
    # One name, not the map and not every key joined: the tool touches a helper
    # model on every call, and the joined list named it first.
    assert "principal_model(model)" in src


# --------------------------------------------------------------------------
# STEP 4 -- the budget says what it cannot measure
#
# `budget_usd` stops a run by comparing dollars to a ceiling, and it can only
# do that for work somebody is charged dollars for. A run entirely on
# subscription routes reads "$0.00 of $10.00" and never stops: correct
# arithmetic, and a sentence meaning the opposite of what it says.
# --------------------------------------------------------------------------


def test_the_ledger_can_say_which_agents_the_budget_did_not_cover():
    from factory.config import Config, RoleConfig
    from factory.llm import LLM

    cfg = Config(roles={"worker": RoleConfig(name="worker", model="m"),
                        "scout": RoleConfig(name="scout", model="m")})
    llm = LLM(cfg)
    llm.record_external("worker", "m1", 0.42, 10, 5, route="sub", billed=False, turns=6)
    llm.record_external("scout", "m2", 0.10, 5, 2, route="default", billed=True)

    cover = llm.coverage()
    assert cover["unbilled_roles"] == ["worker"]
    assert cover["billed_usd"] == 0.1, "the guard's own figure must stay untouched"
    assert cover["notional_usd"] == 0.42
    assert cover["unbilled_turns"] == 6
    assert cover["routes"] == ["default", "sub"]


# ---- the ceiling a plan actually has -------------------------------------


def test_a_rate_limit_is_its_own_exception_not_a_broken_harness():
    """A run on a plan has no dollar ceiling to reach, so this is the ceiling it
    actually has. Reported as "the harness failed" it sends a human to the
    config, the credentials and the code, none of which is wrong."""
    from factory.llm import LLMError, RateLimited, _RATE_LIMIT_SIGNS, _retry_after

    assert issubclass(RateLimited, LLMError), \
        "a caller catching LLMError must not suddenly stop catching this"
    for message in ("API Error: 429 rate_limit_error",
                    "You have exceeded your usage limit; resets at 3pm",
                    "Please try again in 42 minutes",
                    "retry-after 90 seconds"):
        assert _RATE_LIMIT_SIGNS.search(message), message
    # And it must not claim the failures that look nothing like it.
    for message in ("API Error: 404 not_found_error", "model: something not found",
                    "OAuth access token has expired"):
        assert not _RATE_LIMIT_SIGNS.search(message), message

    assert _retry_after("Please try again in 42 minutes") == 42 * 60
    assert _retry_after("retry-after 90 seconds") == 90
    assert _retry_after("no number here") == 0.0


def test_a_windows_reading_is_pulled_from_wherever_the_route_says():
    from factory.meters import read_windows

    windows = read_windows(ENVELOPE, _metered_route())
    assert [w.name for w in windows] == ["five_hour", "seven_day"]
    assert windows[0].utilization == 0.22
    assert windows[0].resets_at == 1788548400


def test_a_route_that_reports_no_windows_reports_none():
    """Of three CLI harnesses measured, one carries this and one carries no rate
    information in its stream at all. A mechanism that pretended to be uniform
    would report a confident zero for the routes that cannot answer, which is
    the one reading worse than none."""
    from factory.meters import read_windows

    assert read_windows(ENVELOPE, _metered_route(meter_windows_key="")) == []
    assert read_windows({}, _metered_route()) == []
    assert read_windows({"rate_limit_info": "not a map"}, _metered_route()) == []


def test_the_tightest_window_is_the_one_that_constrains():
    """11% of a week and 96% of five hours is nearly out of room. An average
    would say 53% and let a run walk into the wall."""
    import time
    from factory.meters import MeterStore, WindowReading

    import tempfile
    store = MeterStore(pathlib.Path(tempfile.mkdtemp()) / "m.json")
    later = time.time() + 3600
    store.record("r", [WindowReading("seven_day", 0.11, later, time.time()),
                       WindowReading("five_hour", 0.96, later, time.time())])
    worst = store.worst("r")
    assert worst is not None and worst.name == "five_hour"


def test_a_reading_from_before_the_reset_is_not_used():
    """It is not merely old, it is wrong in the one direction that matters: it
    says a run has less room than it does, and a ceiling built on it would
    refuse work the plan would have allowed."""
    import time
    from factory.meters import MeterStore, WindowReading

    import tempfile
    store = MeterStore(pathlib.Path(tempfile.mkdtemp()) / "m.json")
    store.record("r", [WindowReading("five_hour", 0.99, time.time() - 10, time.time() - 60)])
    assert store.latest("r")[0].stale is True
    assert store.worst("r") is None, "a window that has reset was still constraining a run"


def test_the_meter_store_merges_rather_than_replaces(tmp_path):
    """It sits in the evidence directory, which more than one process reaches.
    A wholesale rewrite meant whichever wrote last erased what the others had
    measured -- which is how a test suite once deleted a human's connection
    checks."""
    import time
    from factory.meters import MeterStore, WindowReading

    store = MeterStore(tmp_path / "m.json")
    store.record("first", [WindowReading("w", 0.1, time.time() + 60, time.time())])
    store.record("second", [WindowReading("w", 0.2, time.time() + 60, time.time())])
    assert sorted(store.all()) == ["first", "second"]


def test_a_measurement_never_takes_the_call_down_with_it():
    """This is an observation about the account. Losing one is a smaller thing
    than losing the answer that carried it."""
    src = inspect.getsource(llm.LLM._note_windows)
    assert "except Exception:" in src and "pass" in src
    from factory.meters import MeterStore, WindowReading
    import time
    MeterStore("/proc/nope/m.json").record(
        "r", [WindowReading("w", 0.1, time.time() + 60, time.time())])  # must not raise


def test_the_gauge_is_read_even_when_the_call_is_refused():
    """A rejected call still reported the state of the window, and that reading
    is taken closest to the limit -- which is exactly when it matters."""
    src = inspect.getsource(llm.LLM._run_cli)
    assert src.index("self._note_windows(") < src.index("_RATE_LIMIT_SIGNS"), \
        "the reading is discarded on the path where it is most informative"


def test_editing_a_flag_does_not_discard_a_sign_in(tmp_path):
    """A check establishes two things: this tool is on the machine, and it
    accepts the credentials it finds. Neither stops being true when a flag
    changes.

    Fingerprinting the whole argv meant that changing an output format, adding
    a session command or moving a schema onto a file each discarded a perfectly
    good sign-in, and every card went back to amber asking a human to
    re-authorise something nobody had touched. That happened twice.
    """
    import asyncio
    from factory.llm import LLM
    from factory.routes import RouteMonitor

    script = tmp_path / "cli.py"
    script.write_text(
        "import json,sys;sys.stdin.read();print(json.dumps({'result':'ok','is_error':False}))",
        encoding="utf-8")
    cfg = _route_cfg(tmp_path, command=[sys.executable, str(script)],
                     error_key="is_error",
                     version_command=[sys.executable, "--version"])
    asyncio.run(RouteMonitor(cfg, LLM(cfg)).refresh("probe"))

    # Flags change; the tool and its credentials do not.
    cfg.route("probe").command = [sys.executable, str(script), "--new-flag", "value"]
    cfg.route("probe").session_command = [sys.executable, str(script), "-s", "write"]
    probe = next(r for r in asyncio.run(RouteMonitor(cfg, LLM(cfg)).survey())
                 if r["name"] == "probe")
    assert probe["state"] == "ok", "a flag edit threw away a working sign-in"


def test_a_different_binary_or_credential_still_discards_it(tmp_path):
    """The narrowing must not go so far that a check describes another tool."""
    import asyncio
    from factory.llm import LLM
    from factory.routes import RouteMonitor

    script = tmp_path / "cli.py"
    script.write_text(
        "import json,sys;sys.stdin.read();print(json.dumps({'result':'ok','is_error':False}))",
        encoding="utf-8")
    base = dict(command=[sys.executable, str(script)], error_key="is_error",
                version_command=[sys.executable, "--version"])
    cfg = _route_cfg(tmp_path, **base)
    asyncio.run(RouteMonitor(cfg, LLM(cfg)).refresh("probe"))

    for change in ("binary", "credential"):
        fresh = _route_cfg(tmp_path, **base)
        if change == "binary":
            fresh.route("probe").command = ["/some/other/tool", str(script)]
        else:
            fresh.route("probe").key_env = "A_DIFFERENT_KEY"
        probe = next(r for r in asyncio.run(RouteMonitor(fresh, LLM(fresh)).survey())
                     if r["name"] == "probe")
        assert probe["state"] != "ok", (
            f"a changed {change} kept a check that was about something else")


def test_the_image_sweep_keeps_what_is_live_and_only_drops_what_it_minted():
    """Environment images are content-addressed, so every edit orphans the last
    one and nothing removed it: twenty-two tags for one project, one of them
    live, and a disk that filled during a run.
    """
    from factory import containers

    listing = "\n".join([
        "fabrika/clinic:6c0ef269788c",   # live
        "fabrika/clinic:ddb2657da7bd",   # superseded
        "fabrika/clinic:<none>",         # dangling; `image prune` owns these
        "fabrika-prepared:760177b261f3",   # a killed run's residue
        "fabrika/noteboard:ea349a9c3c34",  # live, another project
        "fabrika/unregistered:abc12345678",  # a project we cannot vouch for
        # Minted before the rename. These are the whole reason the sweep knows
        # the old spelling: unrecognised, they would be permanent garbage.
        "factory/clinic:aaaaaaaaaaaa",
        "factory-prepared:bbbbbbbbbbbb",
        "postgres:16-alpine",              # not ours
        "ai-paralegal-dev:latest",         # very much not ours
    ])
    calls: list[list[str]] = []

    async def fake_run(argv, timeout=0):
        calls.append(argv)
        if "images" in argv:
            return containers.Execution(exit_code=0, output=listing)
        return containers.Execution(exit_code=0, output="")

    original, containers._run = containers._run, fake_run
    try:
        removed = asyncio.run(containers.sweep_images(
            {"clinic": "fabrika/clinic:6c0ef269788c",
             "noteboard": "fabrika/noteboard:ea349a9c3c34"},
            containers.DockerConfig()))
    finally:
        containers._run = original

    assert removed == [
        "fabrika/clinic:ddb2657da7bd",
        "fabrika-prepared:760177b261f3",
        "factory/clinic:aaaaaaaaaaaa",
        "factory-prepared:bbbbbbbbbbbb",
    ], removed
    assert not any("-f" in argv for argv in calls if "rmi" in argv), (
        "an image a container still holds must be refused, not forced")

    swept = {argv[-1] for argv in calls if "rmi" in argv}
    assert "fabrika/unregistered:abc12345678" not in swept, (
        "not knowing a project's current tag is a reason to delete nothing of its")
    assert "ai-paralegal-dev:latest" not in swept, "only tags this factory minted"
    assert "postgres:16-alpine" not in swept
    assert "fabrika/clinic:6c0ef269788c" not in swept, "the live tag is never touched"


def test_everything_this_factory_makes_in_docker_wears_its_name():
    """Five containers ran for eight days unnoticed because they were called
    `integ-be` and `slt-pg` and read as somebody's own project. A name that
    says who made it is how `docker ps` stays answerable.
    """
    from factory import containers

    source = inspect.getsource(containers)
    minted = re.findall(r'(?:name|image) = f"([^"]+)"', source)
    assert len(minted) >= 6, "no docker names are minted here any more; this test is stale"
    stray = [m for m in minted if not m.startswith("{NAME_PREFIX}")]
    assert not stray, f"a docker name is spelled out rather than taking the prefix: {stray}"

    spec = schemas.EnvironmentSpec(kind="derive", dockerfile="FROM scratch", workdir="/w")
    assert containers.image_tag("p", spec).startswith(f"{containers.NAME_PREFIX}/")
    assert containers.LEGACY_NAME_PREFIX != containers.NAME_PREFIX, (
        "the old spelling must stay knowable, or images made before the rename "
        "can never be reclaimed")


def test_a_route_whose_credentials_all_miss_is_refused_before_anything_runs():
    """Several optional credentials mean "any one of these will do". None of
    them resolving is a session that will start, spend a turn, and be told it
    is not signed in."""
    import asyncio
    from factory.config import load_config
    from factory.sessioncreds import CredentialError, prepare

    route = _container_route(container_credentials=[
        {"kind": "env", "name": "NO_SUCH_TOKEN_FOR_A_TEST", "optional": True},
        {"kind": "keychain", "service": "no-such-keychain-item-for-a-test",
         "target": "/agent-home/.creds", "optional": True},
    ])
    with pytest.raises(CredentialError) as caught:
        asyncio.run(prepare(route, load_config()))
    assert "NO_SUCH_TOKEN_FOR_A_TEST" in str(caught.value)


def test_the_harness_layer_is_keyed_on_the_base_image_and_the_files_copied_in(tmp_path):
    """A rebuilt project environment must not leave an agent running against
    last week's dependencies, and editing the driver must rebuild the layer."""
    from factory.containers import harness_image_tag

    payload = tmp_path / "driver.py"
    payload.write_text("print(1)\n", encoding="utf-8")
    route = _container_route(container_build_files={str(payload): "/opt/harness/driver.py"},
                             container_install=["install"])

    first = harness_image_tag("base:one", route)
    assert first == harness_image_tag("base:one", route)
    assert first != harness_image_tag("base:two", route)
    assert first != harness_image_tag("base:one", route.model_copy(
        update={"container_install": ["install-v2"]}))
    assert first != harness_image_tag("base:one", route.model_copy(
        update={"container_build_image": "node:26-bookworm-slim"})), \
        "the image the harness is built in is part of what it is"
    assert first != harness_image_tag("base:one", route.model_copy(
        update={"container_check": ["/opt/harness/bin/harness", "--help"]})), \
        "a new check has to be run, not skipped by a cached layer"
    payload.write_text("print(2)\n", encoding="utf-8")
    assert first != harness_image_tag("base:one", route), \
        "the file's contents are part of the key, not just its name"


def test_a_harness_brings_its_own_runtime_and_leaves_the_project_s_alone():
    """Every harness used to be installed with whatever the project's image had:
    the Node ones with its npm, OpenHands with its `python3 -m venv`. Library's
    JDK image on Ubuntu had the interpreter and not the venv module, and every
    agent of the run failed before it started. Now each is built in a stock
    image of its own, and only /opt/harness crosses into the project's."""
    from factory.containers import HARNESS_ROOT, harness_dockerfile

    for source, route in _shipped_container_routes():
        where = f"{source}: {route.name}"
        assert route.container_build_image, f"{where} names no image to build in"
        text = harness_dockerfile("fabrika/project:abc", route, [])
        stage, unit = text.split("FROM fabrika/project:abc AS unit\n")
        assert stage.startswith(f"FROM {route.container_build_image} AS harness\n")
        for line in route.container_install:
            assert f"RUN {line.strip()}" in stage, f"{where}: its install runs in its own stage"
        assert f"COPY --from=harness {HARNESS_ROOT} {HARNESS_ROOT}" in unit
        runs = [ln for ln in unit.splitlines() if ln.startswith("RUN ")]
        assert len(runs) == 2 and runs[0].startswith("RUN mkdir -p "), (
            f"{where}: nothing but its home and its check may run in the project's "
            f"image, and this runs {runs}")
        for tool in ("apt-get", "apk ", "npm ", "pip ", "uv ", "-m venv"):
            assert tool not in unit, f"{where} runs {tool.strip()} against the project's image"

        program = route.container_session_command[0]
        assert program.startswith(HARNESS_ROOT + "/"), (
            f"{where} starts {program!r} by name, so it runs whatever the project's "
            "PATH finds -- or nothing")
        for target in route.container_build_files.values():
            assert target.startswith(HARNESS_ROOT + "/"), \
                f"{where} puts {target} where the stage leaves it behind"


def test_a_harness_that_breaks_the_rules_is_refused_before_anything_is_built():
    from factory.containers import DockerError, harness_image

    docker = Config().docker
    docker.binary = "/nonexistent/docker-for-a-test"
    with pytest.raises(DockerError, match="container_build_image"):
        asyncio.run(harness_image("base:one", _container_route(container_build_image=""),
                                  docker))
    with pytest.raises(DockerError, match="outside /opt/harness"):
        asyncio.run(harness_image("base:one", _container_route(
            container_build_files={str(ROOT / "README.md"): "/usr/local/bin/driver"}),
            docker))


def test_a_harness_that_cannot_run_in_the_project_s_image_says_why_in_its_terms():
    """The build log says what failed in the build's terms. The person reading
    the run needs it in the project's, with something to change."""
    from factory.containers import HARNESS_CHECK_MARK, harness_build_problem, harness_dockerfile

    route = _container_route(harness_label="Claude Code")

    def said(output):
        return harness_build_problem(route, output)

    text = harness_dockerfile("base:one", route, [])
    echoed = "\n".join(f"#{i} [unit {i}/4] {ln}" for i, ln in enumerate(text.splitlines()))
    assert said(echoed + "\n#12 DONE 0.2s") == "Putting Claude Code into this project's image failed.", (
        "Docker echoes every RUN line into the log, so the check's own text must not "
        "read as its verdict -- a Debian image was once called musl for it")

    musl = said(f"#9 0.2 {HARNESS_CHECK_MARK} musl\nERROR: failed to solve")
    assert "musl" in musl and "Debian or Ubuntu" in musl and "Claude Code" in musl
    glibc = said("/opt/harness/bin/claude: /lib/libc.so.6: version `GLIBC_2.28' not found")
    assert "glibc 2.28 or newer" in glibc
    lib = said("node: error while loading shared libraries: libstdc++.so.6: cannot open")
    assert "libstdc++.so.6" in lib and "apt-get install -y libstdc++6" in lib
    shell = said('exec: "/bin/sh": stat /bin/sh: no such file or directory')
    assert "no shell" in shell
    broken = said(f"#12 [unit 4/4] RUN if ...\n#12 0.31 Segmentation fault\n"
                  f"#12 0.31 {HARNESS_CHECK_MARK} failed\n#12 ERROR: process ...")
    assert broken.endswith("It said: Segmentation fault")
    stage = said("#6 [harness 1/5] FROM node:24\n#7 [harness 3/5] RUN npm install\n"
                 "------\n > [harness 3/5] RUN npm install -g ...:\nnpm error network")
    assert "Fabrika's side" in stage, "a failed download is not the project's to fix"
    assert "Fabrika's side" not in said("#6 [harness 1/5] FROM node:24\n#6 DONE 0.1s\n"
                                        " > [unit 2/4] COPY --from=harness"), \
        "every step of the stage is logged; only a failed one is the stage's fault"
    full = said("ERROR: failed to copy files: no space left on device")
    assert "disk space" in full and "docker system prune" in full


def test_a_session_whose_container_was_not_prepared_says_why_instead_of_the_guard(
        tmp_path, monkeypatch):
    """Every agent of a run was refused with "would have run on this machine",
    which was true and no help: the unit's environment had recorded the reason
    -- a harness layer that failed to build -- and the session fell through to a
    host command anyway. Now it stops with that reason, and nothing is spawned."""
    from contextlib import asynccontextmanager
    from factory import isolation
    from factory.executors import ExecutorError

    monkeypatch.setattr(isolation, "TEST_SUITE_ON_HOST", False)
    executor, _ = _executor(tmp_path)
    route = _container_route(harness_label="Codex")
    monkeypatch.setattr(executor, "_container_route", lambda role, session_route: route)
    spawned = []

    async def no_spawn(*args, **kwargs):
        spawned.append(args)
        return 0, ""
    monkeypatch.setattr(executor, "_invoke", no_spawn)

    unprepared = _Prepared()
    unprepared.vars = {}
    unprepared.ready = False
    unprepared.problem = ("Codex can't run in this project's image: the image is built on "
                          "musl.\n\nputting route 'codex' into the unit's image failed:\n#1 ...")

    @asynccontextmanager
    async def environment(tree):
        yield unprepared

    with pytest.raises(ExecutorError) as caught:
        asyncio.run(executor.author(brief="write the suite", tree=tmp_path, role="oracle",
                                    confine="tests/oracle", environment=environment))
    message = str(caught.value)
    assert "the image is built on musl" in message
    assert "#1 ..." not in message, "the summary, not the build log"
    assert "would have run on this machine" not in message
    assert spawned == []

    unprepared.problem = ""
    with pytest.raises(ExecutorError, match="no container environment"):
        asyncio.run(executor.author(brief="write the suite", tree=tmp_path, role="oracle",
                                    confine="tests/oracle", environment=environment))

    # The build session and the fallback route stop the same way.
    for method in (executors.CommandExecutor.run, executors.CommandExecutor.author):
        source = code_without_prose(method)
        assert source.count("raise ExecutorError(unprepared_container(") >= 1, method.__name__
    assert "unprepared_container(alt, prepared)" in code_without_prose(
        executors.CommandExecutor.author)


def test_a_container_cannot_reach_the_machine_it_runs_on(tmp_path):
    """An authoring agent needs egress to reach its model. That is not a reason
    to leave it a route back to the host, where this factory's own API is
    listening -- the one that starts builds and records verdicts. Measured on
    Docker Desktop: on the default bridge, `host.docker.internal:8077` answered.

    And pointing that name away was not enough, measured again: from an
    ordinary feature container `http://192.168.65.254:8300` -- the host by
    address -- answered with a developer's own copy of the app. So every
    network is `--internal` (no route off it at all), and the name is still
    pointed away as well.

    Both runner kinds, because they are told in different places and only one
    of them reads `extra_args`."""
    import yaml
    from factory.containers import HOST_UNREACHABLE, ComposeRunner, DockerRunner
    from factory.schemas import EnvironmentSpec

    runner = DockerRunner("img", "/work", _docker_config())
    argv = runner.argv("pytest -q", cwd=tmp_path, network=True, name="n")
    assert "--add-host" in argv
    assert argv[argv.index("--add-host") + 1] == HOST_UNREACHABLE

    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(
        yaml.safe_dump({"services": {"api": {"image": "x"}, "db": {"image": "y"}}}),
        encoding="utf-8")
    spec = EnvironmentSpec(
        kind="compose", compose_file="docker-compose.yml", compose_service="api",
        workdir="/workspace", image="fabrika/demo:abc")
    compose = ComposeRunner(tmp_path, spec, "fabrika-demo", _docker_config())
    text = compose._override_text(tmp_path)
    assert text.count(HOST_UNREACHABLE) == 2, \
        "every service in the override, since compose merges it into the project's own"
    assert "extra_hosts: !override" in text, "replace, never append"
    override = yaml.safe_load(text.replace("!override", "").replace("!reset", ""))
    assert override["networks"]["default"]["internal"] is True, \
        "the stack's own network still routes off the machine"

    # A stack with a network the seal cannot reach does not start at all.
    compose_file.write_text(yaml.safe_dump({
        "services": {"api": {"image": "x"}, "db": {"image": "y", "network_mode": "host"}}}),
        encoding="utf-8")
    assert "network_mode" in compose._unsealable()
    compose_file.write_text(yaml.safe_dump({
        "services": {"api": {"image": "x"}}, "networks": {"shared": {"external": True}}}),
        encoding="utf-8")
    assert "external" in compose._unsealable()

    # And there is no way out: the setting that was one is refused.
    example = (ROOT / "factory.example.yaml").read_text()
    written = tmp_path / "factory.yaml"
    written.write_text(example.replace("\ndocker:\n", "\ndocker:\n  reach_host: true\n", 1))
    with pytest.raises(config_module.ConfigError, match="docker.reach_host"):
        config_module.load_config(written)


def test_a_container_is_stripped_of_what_a_test_runner_never_needs(tmp_path):
    """The agent inside runs with its own permission layer turned off, because
    the container is what makes that safe rather than reckless. This is the
    other half of that trade. Measured with a live agent doing real work under
    all of it: it ran the project's checks, fixed a failing test, and noticed
    nothing."""
    import inspect

    import yaml
    from factory import gates
    from factory.containers import ComposeRunner, DockerRunner, hardening_argv
    from factory.schemas import EnvironmentSpec

    argv = hardening_argv(_docker_config())
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--security-opt") + 1] == "no-new-privileges"
    assert "--read-only" not in argv, \
        "off by default: a project whose checks install at run time needs to write"

    # Under a read-only root the worktree is a mount and stays writable; the few
    # paths that must be writable arrive as tmpfs rather than as holes.
    locked = hardening_argv(_docker_config(read_only_root=True))
    assert "--read-only" in locked
    assert any(a.startswith("/agent-home:") for a in locked), \
        "the agent's own home has to be writable or the harness cannot start"

    assert hardening_argv(_docker_config(
        drop_capabilities=False, no_new_privileges=False)) == [], "every guard is optional"

    # It reaches a real command.
    runner = DockerRunner("img", "/work", _docker_config())
    assert "--cap-drop" in runner.argv(
        "pytest -q", cwd=tmp_path, network=False, name="n")

    # But not the session that installs things. A package manager drops to its
    # own unwilling user and needs `setgroups`, `setegid` and `seteuid` to do
    # it: hardening that container took an environment down before it existed
    # ("setgroups 65534 failed", a browser install, a project whose user tier
    # could not come up). What it installs is committed to an image, and the
    # session the gates and the agent get is opened from that image with every
    # guard on.
    assert "--cap-drop" not in runner.argv(
        "apt-get install -y chromium", cwd=tmp_path, network=True, name="n", harden=False)
    setup = inspect.getsource(gates.run_setup)
    assert "opener(Path(cwd), harden=False)" in setup

    # Compose gets none of it, and the reason is structural: an override
    # describes a service, and that one service runs setup, then the gates,
    # then the agent. Hardening it hardens the installer too.
    (tmp_path / "docker-compose.yml").write_text(
        yaml.safe_dump({"services": {"api": {"image": "x"}, "db": {"image": "postgres"}}}),
        encoding="utf-8")
    spec = EnvironmentSpec(
        kind="compose", compose_file="docker-compose.yml", compose_service="api",
        workdir="/workspace", image="img:1")
    text = ComposeRunner(tmp_path, spec, "proj", _docker_config())._override_text(tmp_path)
    assert "cap_drop" not in text


def test_an_account_that_is_a_placeholder_is_not_published_as_an_account():
    """One repair unit's entire report was the string `Test.`, with one
    decision titled `test` and an empty disclosure -- while its diff changed
    the login and invite routers: a row lock, an integrity error turned into a
    404, an advisory lock. It reached the packet as an account, and the only
    reason anybody knew was that a reviewer read the diff."""
    import inspect
    from factory.executors import CommandExecutor, _is_placeholder
    from factory.schemas import FileWrite, SelfDisclosure

    class _Answer:
        def __init__(self, summary, disclosure=None):
            self.summary = summary
            self.disclosure = disclosure or SelfDisclosure()

    files = [FileWrite(path=f"f{i}.py", contents="x", purpose="") for i in range(2)]
    assert _is_placeholder(_Answer("Test."), files)
    assert _is_placeholder(_Answer("test"), files)

    # What must not be caught: a small change described small, and a detailed
    # account that simply has nothing to disclose.
    assert not _is_placeholder(_Answer("Fixed the misspelled header constant."), files)
    assert not _is_placeholder(
        _Answer("Renamed the class.", SelfDisclosure(flags=["one call site unverified"])),
        files)
    long_account = (
        "The diff renames the session model, updates every call site, adds the advisory "
        "lock around invite creation and converts the integrity error into a 404, leaving "
        "the router signatures untouched because the spec pins them.")
    assert not _is_placeholder(_Answer(long_account), files * 6)

    # And the loop around it: asked again, then recorded as missing.
    source = inspect.getsource(CommandExecutor.run)
    assert "_is_placeholder(reflection, files)" in source
    assert "REFLECTION_EMPTY" in source, \
        "a hole in the packet a human can see beats one they cannot"


def test_a_fallback_is_booked_as_the_model_it_actually_ran_on():
    """A whole review panel fell back to a substitute because its own route was
    refusing calls, and every row still said the configured model. `ask`
    accounted for the answer against the role it was handed -- the configured
    one -- not the stand-in the request actually went out as. Fixing the stamp
    on each record changed nothing, because the stamp faithfully copied the
    wrong usage."""
    import asyncio

    from factory.config import load_config
    from factory.llm import LLM, RouteExhausted, principal_model

    cfg = load_config(str(Path("factory.yaml")))
    # On a CLI route, so there is a refusal to fall back from.
    cfg.roles["reviewer"].route = "codex"
    role = cfg.role("reviewer")
    if not role.fallback_route:
        pytest.skip("this config gives the reviewer no fallback to take")
    llm = LLM(cfg)
    sent: list[tuple[str, str]] = []

    async def fake_post(payload):
        sent.append(("api", payload.get("model", "")))
        return {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    async def refuse(*args, **kwargs):
        raise RouteExhausted("Your workspace is out of credits")

    llm._post = fake_post
    llm._run_cli = refuse
    response = asyncio.run(llm._complete(
        role, "reviewer", [{"role": "user", "content": "x"}], None, None))
    llm._account("reviewer", role.model, response)

    assert response["_asked"] == (role.fallback_model or role.model)
    assert llm.answered["reviewer"] == (role.fallback_model or role.model)
    assert role.model not in llm.usage["reviewer"].models or \
        role.model == (role.fallback_model or role.model), \
        "the refusing model did no work and must not be booked as having done it"

    # Every record stamp now reads what answered, including the panel's.
    from factory.pipeline import Factory
    source = class_source(Factory)
    assert "model=role.model" not in source

    # And a tool's map of every model it touched names the one that worked.
    assert principal_model({
        "helper-model": {"costUSD": 0.004, "outputTokens": 900},
        "main-model": {"costUSD": 0.41, "outputTokens": 12000},
    }) == "main-model"

    # And the station list names models only from the calls it recorded --
    # what answered, and what refused first -- never from configuration.
    app = app_js()
    assert "foldCalls(r.calls)" in app
    models = app[app.index("function turnFacts("):app.index("function stepLane(")]
    assert "r.payload.outcome === 'answered'" in models and "r.payload.answered" in models
    assert "d.roles" not in models and "config" not in models
    assert app.count("function phaseModels(") <= 1, "a second declaration silently replaces the first"


def test_every_call_is_written_down_in_the_order_it_happened(tmp_path):
    """Labelling a phase with "the model it ran on" meant summarising many
    calls into one badge, and the honest summary was often "two, one of which
    refused". So each call is a row instead: what it asked for, where it went,
    what came back. A refusal is its own row and the substitute that answered
    in its place is the next one -- nothing is left to infer."""
    import asyncio

    from factory.config import load_config
    from factory.llm import LLM, RouteExhausted, journal_calls, stop_journaling
    from factory.store import EvidenceStore

    cfg = load_config(str(Path("factory.yaml")))
    # On a CLI route, so there is a refusal to fall back from.
    cfg.roles["reviewer"].route = "codex"
    role = cfg.role("reviewer")
    if not role.fallback_route:
        pytest.skip("this config gives the reviewer no fallback to take")
    llm = LLM(cfg)

    async def answer(payload):
        return {"model": "provider/the-one-that-answered",
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30, "cost": 0.002}}

    async def refuse(*args, **kwargs):
        raise RouteExhausted("Your workspace is out of credits")

    llm._post, llm._run_cli = answer, refuse
    store = EvidenceStore(tmp_path / "evidence", "demo")

    def write(entry):
        store.append("call", entry, role=entry["role"], spec_hash="h",
                     meta={"phase": "review"})

    async def one(text):
        await llm._complete(role, "reviewer", [{"role": "user", "content": text}], None, None)

    async def run():
        token = journal_calls(write)
        try:
            await one("inside a phase")
        finally:
            stop_journaling(token)
        await one("outside any phase")

    asyncio.run(run())
    calls = [r["payload"] for r in store.records() if r["kind"] == "call"]
    assert [c["outcome"] for c in calls] == ["refused", "answered"], \
        "the refusal is a row, then the substitute; a call outside a phase goes nowhere"
    refused, answered = calls
    assert refused["asked"] == role.model and "out of credits" in refused["reason"]
    assert answered["fallback_from"] == refused["route"]
    assert answered["answered"] == "provider/the-one-that-answered"
    assert answered["prompt_tokens"] == 120

    # Sessions are written down the same way, from the one place every harness
    # attempt runs.
    import inspect

    from factory import executors
    from factory.pipeline import Factory
    assert "journal_call(call_entry(" in inspect.getsource(executors.CommandExecutor._invoke)
    assert "journal_calls(write)" in inspect.getsource(Factory._phase)
    assert executors._session_outcome(-9, "harness timed out after 1800.0s")[0] == "timed_out"
    assert executors._session_outcome(0, "done")[0] == "answered"



def test_a_route_that_ran_out_is_not_asked_again_until_it_may_have_come_back():
    """One card-labels run sent 40 calls to a route that had been out of
    credits since the first of them -- every agent, every round, each refusal
    a few seconds and a row in the log. The first refusal now marks the route
    spent; later calls, completions and sessions alike, go straight to the
    fallback and say why, until the route's own reset time or half an hour."""
    import time as _time

    from factory.llm import LLM, SPENT_HOLD_S, refusal_words

    assert refusal_words("Your workspace is out of credits.") == "out of credits"

    llm = LLM.__new__(LLM)
    assert llm.spent("codex") is None
    llm.mark_spent("codex", "out of credits", detail="{...}")
    held = llm.spent("codex")
    assert held and held["why"] == "out of credits"
    assert held["until"] - _time.time() == pytest.approx(SPENT_HOLD_S, abs=5)
    first = held["since"]
    llm.mark_spent("codex", "out of credits")
    assert llm.spent("codex")["since"] == first, "a second refusal does not reset when it began"
    llm.mark_spent("claude-code", "at its usage limit", retry_after_s=60)
    assert llm.spent("claude-code")["until"] - _time.time() == pytest.approx(60, abs=5)
    llm.spent_routes["codex"]["until"] = _time.time() - 1
    assert llm.spent("codex") is None, "a route is asked again once its hold is over"

    # Both paths consult it before asking the primary route.
    complete = inspect.getsource(LLM._complete)
    assert complete.index("self.spent(primary.name)") < complete.index("await self._on_route(")
    author = inspect.getsource(executors.CommandExecutor.author)
    assert "held = (spent_check(primary_name)" in author
    assert author.index("if not held:") < author.index("self._task_stdin(template, task)")


def test_editing_nothing_is_not_reported_as_a_failed_session_for_the_integrator():
    """Measured: an integrator that spent 106 seconds walking the seams,
    confirmed the cascade delete and the workspace scoping, and correctly
    concluded nothing needed changing was recorded as "the coding harness
    produced no edits", "the whole of integration" not implemented, and
    "$0.6393 producing nothing". Three claims, none of them true, and the one
    that was -- it left no seam check -- was in none of them."""
    from factory import executors
    from factory.schemas import WorkUnit

    unit = WorkUnit(id="integration", title="Close the seams between the units",
                    objective="...", criterion_ids=[])
    runs = [(0, "No edits were needed -- the seams hold. I did not modify any files.")]

    done = executors._no_edits_output(unit, ["claude", "-p"], runs, role="integrator")
    assert done.disclosure.flags == [], \
        "a legitimate no-edit outcome is being flagged as a harness failure"
    assert done.disclosure.not_implemented == [], \
        "integration happened; only the check is missing, and that is checked from disk"
    assert "Nothing was written for this unit" not in done.summary

    worker = executors._no_edits_output(unit, ["claude", "-p"], runs, role="worker")
    assert worker.disclosure.flags == [executors.NO_EDITS], \
        "for a worker, nothing written is nothing built, and that must still be said"
    assert worker.disclosure.not_implemented


# ==========================================================================
# isolation -- no agent, and nothing an agent wrote, runs on this machine
# ==========================================================================


def test_no_agent_runs_on_this_machine(monkeypatch):
    """Every agent session and every CLI completion runs in a sealed container.

    Agents with no prepared environment used to run on this machine's shell --
    the oracle always, every CLI completion always -- where they could reach
    every server here and every other feature's files. The one exception left is
    this suite, which has no Docker; it is switched on in `tests/conftest.py`
    and nowhere under `factory/`.
    """
    from factory import containers, executors, isolation

    root = ROOT / "factory"
    setters = [f"{p.name}:{i}" for p in root.rglob("*.py")
               for i, line in enumerate(p.read_text().splitlines(), 1)
               if re.search(r"TEST_SUITE_ON_HOST\s*=", line)
               and not (p.name == "isolation.py" and line.strip() == "TEST_SUITE_ON_HOST = False")]
    assert not setters, f"production code lets agents onto this machine: {setters}"

    monkeypatch.setattr(isolation, "TEST_SUITE_ON_HOST", False)
    with pytest.raises(isolation.IsolationError):
        isolation.require_contained(["claude", "-p"], "docker", "an agent session")
    isolation.require_contained(["docker", "exec", "-i", "c", "claude"], "docker", "x")
    isolation.require_contained(["/usr/local/bin/docker", "run", "img"], "docker", "x")

    # Held where the process is spawned, so no path to a session can skip it.
    assert "require_contained(argv, self.config.docker.binary" in inspect.getsource(
        executors.CommandExecutor._run_harness)
    cli = inspect.getsource(llm.LLM._run_cli)
    assert "completion_box(route, self.config, routed)" in cli
    assert cli.index("if isolation.TEST_SUITE_ON_HOST:") < cli.index("completion_box(")

    # And checks: no environment is not a licence to run them here either.
    class _P:
        id = "p"
        environment = None
    with pytest.raises(containers.DockerError, match="sealed container"):
        asyncio.run(containers.runner_for(_P(), load_config(ROOT / "factory.example.yaml")))


def test_the_way_out_forwards_to_public_addresses_and_nothing_else():
    """The egress proxy is the only route off a sealed network. It resolves the
    name itself, refuses if any address is not public, and connects to the
    address it checked -- so neither a name pointing home nor a rebinding trick
    gets through. Docker Desktop's range for the host is named on its own:
    `192.168.65.254:8300` is exactly what answered before this existed."""
    from factory import egress_proxy as proxy

    for private in ("127.0.0.1", "10.0.0.5", "172.17.0.1", "192.168.65.254",
                    "192.168.1.10", "169.254.169.254", "::1", "::ffff:127.0.0.1",
                    "fd00::1", "0.0.0.0"):
        assert not proxy.public(private), f"{private} would be forwarded"
    for open_ in ("8.8.8.8", "104.18.0.1", "2606:4700::1111"):
        assert proxy.public(open_), f"{open_} would be refused"

    # A stand-in handed out for a name that points home is refused on
    # whatever port it is asked for -- 8300 is a developer's own app.
    for name in ("127.0.0.1", "192.168.65.254"):
        address = proxy.STAND_INS.address_for("10.212.4.0/22", name)
        assert asyncio.run(_carry_once(proxy, (address, 8300))) == b"", \
            f"{name} was carried"


def test_a_stack_sends_only_its_setup_and_its_agents_out(tmp_path):
    """The resolver that sends outside names to the proxy goes to what asks for
    the network, never to a check -- and it is the resolver of the network the
    gate service is actually on. Every stack network is made on a block of the
    pool, so the proxy can answer for part of it."""
    from factory.containers import ComposeRunner

    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  api:\n    image: x\n    networks: [back]\n  db:\n    image: y\n"
        "networks:\n  back: {}\n")
    spec = schemas.EnvironmentSpec(kind="compose", source="t", compose_file="docker-compose.yml",
                                   compose_service="api")
    runner = ComposeRunner(tmp_path, spec, "fabrika-t", _docker_config())
    runner._blocks = {"default": "10.212.8.0/22", "back": "10.212.12.0/22"}
    assert runner._egress_flags(False) == []
    flags = runner._egress_flags(True)
    assert flags[0] == "-v" and flags[1].endswith(":/etc/resolv.conf:ro")
    assert "nameserver 10.212.13.1" in Path(flags[1].split(":")[0]).read_text(), \
        "the gate service is on `back`, so it is `back`'s proxy address it must ask"
    text = runner._override_text(tmp_path)
    for block, span in (("10.212.8.0/22", "10.212.8.0/24"), ("10.212.12.0/22", "10.212.12.0/24")):
        assert f'subnet: "{block}"' in text and f'ip_range: "{span}"' in text
    assert "ipam: !override" in text, "a project's own ipam must not merge into the pool's"
    assert "enforces_egress_policy = True" in inspect.getsource(ComposeRunner)


def test_the_way_out_writes_down_what_it_forwarded_and_what_it_stopped(monkeypatch, capsys):
    """Every connection is one JSON line on the proxy's log: when, from which
    address, to which name and port, and -- for a refusal -- why. A public
    address that did not answer is logged as `failed`, not `refused`, so the
    count of refusals means only what the rule kept out. A connection to an
    address the proxy never handed out, or straight to its listener, is refused
    rather than guessed at."""
    from factory import egress_proxy as proxy

    stand_ins = proxy.StandIns()
    monkeypatch.setattr(proxy, "STAND_INS", stand_ins)
    home = stand_ins.address_for("10.212.4.0/22", "127.0.0.1")
    registry = stand_ins.address_for("10.212.4.0/22", "registry.example.org")

    async def scenario() -> bytes:
        upstream = await asyncio.start_server(
            lambda r, w: (w.write(b"hi"), w.close()), "127.0.0.1", 0)
        up_port = upstream.sockets[0].getsockname()[1]
        async with upstream:
            await _carry_once(proxy, (home, 8300))
            # Stand-ins for a public address: one that answers, one that does not.
            monkeypatch.setattr(proxy, "public", lambda a: True)
            real = proxy.resolve

            async def to_loopback(host, port):
                return (await real("127.0.0.1", port))[0], ""
            monkeypatch.setattr(proxy, "resolve", to_loopback)
            got = await _carry_once(proxy, (registry, up_port))
            await _carry_once(proxy, (registry, 1))
            await _carry_once(proxy, ("10.212.7.200", 443))
            await _carry_once(proxy, None)
            return got

    assert asyncio.run(scenario()) == b"hi", "bytes are carried both ways, unread"
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]
    assert [x["event"] for x in lines] == ["refused", "forwarded", "failed", "refused", "refused"]
    assert lines[0]["target"] == "127.0.0.1:8300" and "loopback" in lines[0]["why"]
    assert lines[1]["target"].startswith("registry.example.org:"), "logged by name, not stand-in"
    assert "not a stand-in" in lines[3]["why"] and "directly" in lines[4]["why"]
    assert all(x["client"] == "127.0.0.1" and x["at"].endswith("Z") for x in lines)


def test_a_sealed_network_is_laid_out_so_the_proxy_can_answer_for_part_of_it():
    """A /22 from the pool: Docker hands out the first /24, the proxy sits at a
    fixed address a resolver file can name in advance, and the top /23 is
    stand-ins. All on one subnet, so a container reaches a stand-in with no
    route, no gateway and no capability."""
    from factory import egress_proxy as proxy

    block = "10.212.4.0/22"
    assert proxy.container_range(block) == "10.212.4.0/24"
    assert proxy.proxy_address(block) == "10.212.5.1"
    assert proxy.stand_in_block(block) == "10.212.6.0/23"
    assert proxy.laid_out(block, "10.212.0.0/14")
    assert not proxy.laid_out("172.18.0.0/16", "10.212.0.0/14"), "Docker's own pick"
    assert not proxy.laid_out("10.212.4.0/24", "10.212.0.0/14"), "the wrong size"
    assert len(proxy.blocks("10.212.0.0/14")) == 256
    assert str(proxy.block_of("10.212.6.9", "10.212.0.0/14")) == block
    assert proxy.block_of("172.17.0.3", "10.212.0.0/14") is None


def test_a_full_stand_in_block_gives_up_the_name_used_least_recently():
    from factory import egress_proxy as proxy

    table = proxy.StandIns()
    subnet = "10.212.4.0/22"
    first = table.address_for(subnet, "n0.example")
    for i in range(1, 510):
        table.address_for(subnet, f"n{i}.example")
    table.address_for(subnet, "n0.example")                 # used again: kept
    fresh = table.address_for(subnet, "late.example")
    assert table.name_for(first) == "n0.example"
    assert table.name_for(fresh) == "late.example"
    assert fresh == table.address_for(subnet, "late.example")
    assert table.address_for(subnet, "n1.example") != fresh, "n1 was the one given up"


def test_a_sealed_network_is_made_on_the_first_free_block_and_moves_on_when_taken(monkeypatch):
    """Fabrika picks the subnet, because the layout depends on it. It skips any
    block a Docker network already overlaps, moves on when another process
    takes one between looking and creating, and remakes a network of the same
    name that an older Fabrika made off the pool."""
    from factory import egress
    from factory.config import DockerConfig

    created: list[list[str]] = []
    state = {"existing": "", "overlap_once": True}

    async def fake(argv, timeout=60.0):
        if argv[1:3] == ["network", "ls"]:
            return 0, "id1 id2"
        if argv[1:3] == ["network", "inspect"] and argv[-2:] == ["id1", "id2"]:
            return 0, "172.17.0.0/16 10.212.0.0/22 10.212.4.0/23"
        if argv[1:3] == ["network", "inspect"]:
            return (0, state["existing"]) if state["existing"] else (1, "No such network")
        if argv[1:3] == ["network", "create"]:
            created.append(list(argv))
            if state["overlap_once"]:
                state["overlap_once"] = False
                return 1, "Pool overlaps with other one on this address space"
            return 0, "netid"
        return 0, ""

    monkeypatch.setattr(egress, "_docker", fake)
    monkeypatch.setattr(egress, "_subnets", {})
    monkeypatch.setattr(egress, "_reserved", set())
    docker = DockerConfig()
    assert asyncio.run(egress.create_sealed(docker, "fabrika-sealed-a")) == ""
    assert len(created) == 2, "an overlap is a race lost, and it is retried"
    argv = created[-1]
    assert argv[argv.index("--subnet") + 1] == "10.212.8.0/22", "0 and 4 are taken"
    assert argv[argv.index("--ip-range") + 1] == "10.212.8.0/24"
    assert "--internal" in argv

    # Made by an older Fabrika, on a subnet Docker chose: removed and made again.
    removed: list[list[str]] = []

    async def legacy(argv, timeout=60.0):
        if argv[1:3] == ["network", "rm"]:
            removed.append(list(argv))
            state["existing"] = ""
            return 0, ""
        return await fake(argv, timeout)
    monkeypatch.setattr(egress, "_docker", legacy)
    state["existing"] = "172.20.0.0/16"
    assert asyncio.run(egress.create_sealed(docker, "fabrika-agents")) == ""
    assert removed and removed[0][-1] == "fabrika-agents"


def test_a_container_s_resolver_asks_docker_first_and_the_proxy_second():
    """Order is the whole mechanism. Docker answers the stack's own names and
    refuses outside ones, and a refusal sends glibc and musl on to the next
    nameserver -- the proxy, at the address it always has on that network."""
    from factory import egress

    text = egress.resolver_file("10.212.4.0/22").read_text()
    servers = [line.split()[1] for line in text.splitlines() if line.startswith("nameserver")]
    assert servers == ["127.0.0.11", "10.212.5.1"]
    assert "timeout:1" in text
    assert egress.resolver_argv("10.212.4.0/22")[1].endswith(":/etc/resolv.conf:ro")


def test_nothing_tells_a_container_about_the_proxy_any_more():
    """The variables are gone, from every path that starts a container. They were
    a convention some programs honoured, and the ones that did not -- a JVM, git
    over SSH -- simply had no way out. The resolver file replaced them, and the
    agent box gets it too."""
    import re as _re
    for path in factory_files():
        src = path.read_text()
        assert not _re.search(r"[\"']HTTPS?_PROXY", src, _re.I), f"{path.name} sets a proxy variable"
    assert "resolver_argv(" in inspect.getsource(agentbox_module.completion_box)


def test_the_way_out_is_summarized_by_who_tried_not_just_by_address():
    """The console's reading of that log: counts, where traffic went, and each
    refusal newest first with the container that made it. An address no
    container holds any more is said to be gone, never guessed -- addresses on a
    network are reused."""
    from factory import egress

    log = "\n".join([
        "fabrika egress listening on 3128",               # an older proxy's line
        '{"event": "listening", "port": 3128}',
        '{"at": "t1", "event": "forwarded", "client": "10.9.0.2", "target": "api.anthropic.com:443"}',
        '{"at": "t2", "event": "forwarded", "client": "10.9.0.2", "target": "api.anthropic.com:443"}',
        '{"at": "t3", "event": "forwarded", "client": "10.9.0.3", "target": "registry.npmjs.org:443"}',
        '{"at": "t4", "event": "refused", "client": "10.9.0.3", "target": "host.docker.internal:8300",'
        ' "why": "host.docker.internal resolves to 192.168.65.254, which is not a public address"}',
        '{"at": "t5", "event": "refused", "client": "10.9.0.7", "target": "10.0.0.5:5432", "why": "x"}',
        '{"at": "t6", "event": "failed", "client": "10.9.0.2", "target": "example.com:443", "why": "y"}',
        "Traceback (most recent call last):",
    ])
    members = {"10.9.0.2": "fabrika-answer-abc", "10.9.0.3": "fabrika-kanban-card-drag-api-1"}
    got = egress.summarize(egress.read_log(log), members)
    assert (got["forwarded"], got["refused_count"], got["failed_count"]) == (3, 2, 1)
    assert got["destinations"][0] == {"host": "api.anthropic.com", "count": 2}
    first, second = got["refused"]
    assert first["at"] == "t5" and first["what"] == "a container that has since stopped"
    assert second["what"] == "kanban-card-drag-api-1" and "8300" in second["target"]
    assert got["failed"][0]["what"] == "a model call"
    # Gone, but on a network that still says what it was.
    on_agents = egress.summarize(egress.read_log(log), {},
                                 subnets={"10.9.0.0/24": "fabrika-agents"})
    assert on_agents["refused"][0]["what"] == "a model call, since stopped"


def test_a_restarted_way_out_keeps_every_build_s_way_out(monkeypatch):
    """The proxy is recreated when its script or its start-up changes. It used
    to come back on the default bridge alone, so a build in flight when the
    console restarted lost the internet mid-run. It now rejoins what it was on.
    And the status read never starts anything: opening a screen is not a
    reason to change what runs on the machine."""
    from factory import egress
    from factory.config import DockerConfig

    calls: list[list[str]] = []
    answers = {"inspect-state": (0, "true stale-version t0"),
               "inspect-networks": (0, "bridge fabrika-egress-aaa fabrika-kanban-x_default")}
    subnets = {"fabrika-egress-aaa": "10.212.4.0/22", "fabrika-kanban-x_default": "10.212.8.0/22"}

    async def fake(argv, timeout=60.0):
        calls.append(list(argv))
        if argv[1:3] == ["network", "inspect"]:
            return 0, subnets.get(argv[-1], "")
        if argv[1] == "inspect" and "Running" in argv[3]:
            return answers["inspect-state"]
        if argv[1] == "inspect" and "Networks" in argv[3]:
            return answers["inspect-networks"]
        return 0, ""

    monkeypatch.setattr(egress, "_docker", fake)
    monkeypatch.setattr(egress, "_subnets", {})
    monkeypatch.setattr(egress, "_installed", set())
    docker = DockerConfig()
    assert asyncio.run(egress.ensure_proxy(docker)) == ""
    ran = next(c for c in calls if c[1] == "run" and "--name" in c)
    assert ran.index("--name") < ran.index(egress._image_tag(docker))
    assert "max-size=10m" in ran, "the connection log is unbounded"
    assert "--cap-drop" in ran and "NET_ADMIN" not in ran, "the proxy itself holds no capability"
    joined = [(c[-2], c[c.index("--ip") + 1]) for c in calls if c[1:3] == ["network", "connect"]]
    assert joined == [("fabrika-egress-aaa", "10.212.5.1"),
                      ("fabrika-kanban-x_default", "10.212.9.1")], \
        "rejoined at the address each network's resolver file names"
    helpers = [c for c in calls if c[1] == "run" and "container:fabrika-egress" in c]
    assert len(helpers) == 2 and all("NET_ADMIN" in c for c in helpers)
    assert "10.212.6.0/23" in helpers[0][-1] and "REDIRECT" in helpers[0][-1], \
        "the stand-ins come back with the proxy, or a restart strands every build"

    # Current version: nothing is touched.
    calls.clear()
    answers["inspect-state"] = (0, f"true {egress._version(docker)} t1")
    asyncio.run(egress.ensure_proxy(docker))
    assert not any(c[1] in ("run", "rm") for c in calls)

    # A different start-up is a different version, not only a different script.
    other = DockerConfig(egress_image="python:3.13-slim")
    assert egress._version(other) != egress._version(docker)

    # Status: absent is said plainly, and never started.
    calls.clear()
    answers["inspect-state"] = (1, "Error: No such object: fabrika-egress")
    got = asyncio.run(egress.status(docker))
    assert got["state"] == "absent" and "first build" in got["detail"]
    assert not any(c[1] in ("run", "rm", "network") for c in calls)


def test_a_session_s_row_carries_what_the_session_itself_reported():
    """Every agent session on record said zero tokens, zero cost, and "billed"
    on a subscription: its row was written from the outcome alone, and the
    figures went only into a process-wide tally no feature could read. The row
    is now written from the tool's own final report -- and Claude's input is
    counted whole: `input_tokens` is only the uncached remainder (72 of 1.9
    million in the session this sample comes from), the rest is in its cache
    fields. Codex's `input_tokens` already includes what it read from cache."""
    from factory.config import load_config as _load
    from factory.executors import session_response
    from factory.llm import call_entry, route_tokens

    config = _load(ROOT / "factory.example.yaml")
    claude, codex = config.routes["claude-code"], config.routes["codex"]

    # Claude Code's last line, as recorded from a real repair session.
    claude_log = "working...\n" + json.dumps({
        "type": "result", "is_error": False, "result": "done", "total_cost_usd": 0.6108286,
        "num_turns": 31, "usage": {"input_tokens": 72, "cache_creation_input_tokens": 35789,
                                   "cache_read_input_tokens": 1874463, "output_tokens": 8061}})
    response = session_response(claude_log, claude)
    assert response["usage"]["prompt_tokens"] == 72 + 35789 + 1874463
    assert response["usage"]["completion_tokens"] == 8061
    row = call_entry(role="repairer", route="claude-code", asked="sonnet", outcome="answered",
                     started=time.time(), kind="session", response=response)
    assert row["prompt_tokens"] == 1910324 and row["billed"] is False, "a subscription session reads as billed"
    assert row["cost_usd"] == 0.0 and row["notional_usd"] == pytest.approx(0.6108286)

    # Codex's event stream: cached input is already inside input_tokens.
    codex_log = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "t-1"}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1929313, "cached_input_tokens": 1855104,
                                                        "output_tokens": 25886}})])
    usage = session_response(codex_log, codex)["usage"]
    assert (usage["prompt_tokens"], usage["completion_tokens"]) == (1929313, 25886)
    assert route_tokens({"usage": {"input_tokens": 5}}, codex) == (5, 0)

    src = inspect.getsource(executors.CommandExecutor._invoke)
    assert "response=session_response(output, route)" in src


def test_the_build_names_the_stage_and_the_projects_own_context(tmp_path):
    from factory.containers import DockerError, build_argv
    from factory.schemas import EnvironmentSpec

    repo = tmp_path / "repo"
    (repo / "api").mkdir(parents=True)
    own = EnvironmentSpec(kind="reuse", dockerfile_path="api/Dockerfile", dockerfile_target="test")
    argv = build_argv("docker", own, Path("/tmp/Dockerfile"), "t", repo)
    assert argv[-3:] == ["--target", "test", str((repo / "api").resolve())]
    fabrika = EnvironmentSpec(kind="generate", dockerfile="FROM x\n")
    assert build_argv("docker", fabrika, Path("/tmp/Dockerfile"), "t", repo)[-1] == str(repo)
    with pytest.raises(DockerError, match="outside the repository"):
        build_argv("docker", EnvironmentSpec(kind="reuse", dockerfile_path="api/Dockerfile",
                                             build_context="../.."), Path("/d"), "t", repo)
    src = inspect.getsource(containers_module().build_image)
    assert 'if spec.kind == "reuse" and not spec.dockerfile_path:' in src


def test_the_door_publishes_this_machines_loopback_and_nothing_wider():
    """The one place Fabrika publishes a port. Every service port under its own
    number, so the address a service was told it has is the one a browser opens;
    never on 0.0.0.0; on a network of its own; with the egress proxy's guards."""
    from factory import containers
    from factory.config import DockerConfig

    docker = DockerConfig()
    door = containers.door_argv(docker, "fabrika-door-x", "fabrika-door-x", "10.212.0.5",
                                [41001, 41002], "demo/f-1")
    published = [door[i + 1] for i, a in enumerate(door) if a == "-p"]
    assert published == ["127.0.0.1:41001:41001", "127.0.0.1:41002:41002"]
    assert door[door.index("--network") + 1] == "fabrika-door-x", "never the default bridge"
    assert f"{containers.PREVIEW_LABEL}=demo/f-1" in door
    for guard in ("--read-only", "no-new-privileges", "65534:65534"):
        assert guard in door
    assert door[door.index("--cap-drop") + 1] == "ALL"
    assert door[door.index("--listen") + 1] == "0.0.0.0"
    assert door[door.index("--to") + 1] == "10.212.0.5"
    assert docker.egress_image in door, "Fabrika's image, never the project's"

    inner = containers.inner_argv(docker, "fabrika-inner-x", "abc123", "10.212.0.5",
                                  [41001], "demo/f-1")
    assert "-p" not in inner, "the inner hop shares a namespace and publishes nothing"
    assert inner[inner.index("--network") + 1] == "container:abc123"
    assert inner[inner.index("--listen") + 1] == "10.212.0.5"
    assert inner[inner.index("--to") + 1] == "127.0.0.1"


def test_half_an_open_door_is_closed_again(tmp_path):
    from factory import containers
    from factory.config import DockerConfig

    binary, log = _stand_in_docker(tmp_path, {
        "inspect": [0, json.dumps({"fabrika-sealed-preview-1": {"IPAddress": "10.212.0.5"}})],
        "network connect": [1, "no such network"],
    })
    door = containers.Door(DockerConfig(binary=binary), "demo/f-1")
    problem = asyncio.run(door.open("session-id", [41001], prefer="fabrika-sealed-preview-1"))
    assert "connect" in problem and "no such network" in problem
    calls = log.read_text().splitlines()
    assert any(c.startswith("rm -f fabrika-inner-") and "fabrika-door-" in c for c in calls), calls
    assert any(c.startswith("network rm fabrika-door-") for c in calls), calls
    assert door.containers == [] and door.network == ""


def test_the_boot_sweep_removes_only_what_a_preview_left(tmp_path):
    """Labelled or named for a preview, and nothing else on this machine."""
    from factory import containers
    from factory.config import DockerConfig

    binary, log = _stand_in_docker(tmp_path, {
        "ps -aq --filter label=fabrika.preview": [0, "c1\nc2\n"],
        "inspect -f {{index .Config.Labels \"com.docker.compose.project\"}} c1":
            [0, "fabrika-demo-f-1-preview\n"],
        "inspect": [0, "<no value>\n"],
        "network ls --format {{.Name}} --filter label=fabrika.preview": [0, "fabrika-door-1\n"],
        "network ls --format {{.Name}} --filter name=fabrika-sealed-preview-":
            [0, "fabrika-sealed-preview-9\n"],
        "container ls -q -a --filter label=com.docker.compose.project=fabrika-demo-f-1-preview":
            [0, "k1\n"],
        "volume ls -q --filter label=com.docker.compose.project=fabrika-demo-f-1-preview":
            [0, "v1\n"],
    })
    removed = asyncio.run(containers.sweep_previews(DockerConfig(binary=binary)))
    assert {"c1", "c2", "fabrika-door-1", "fabrika-sealed-preview-9", "k1", "v1"} <= set(removed)
    calls = log.read_text().splitlines()
    removals = [c for c in calls if " rm" in f" {c}"]
    assert removals, calls
    for call in removals:
        assert any(name in call for name in ("c1", "fabrika-door-1", "fabrika-sealed-preview-",
                                             "k1", "v1")), f"removed something unmarked: {call}"


def test_a_reading_pauses_itself_before_the_window_is_full(tmp_path):
    """The plan is the person's as well as the reading's. Past 85% of its
    tightest window the reading stops asking -- as it does at the limit, so
    nothing is committed and Read again carries on -- rather than taking the
    last of the window from them."""
    import time as _time
    from factory.meters import WindowReading

    class Gauge:
        def __init__(self, used):
            self.used = used

        def worst(self, route):
            return WindowReading(name="five_hour", utilization=self.used, resets_at=_time.time() + 3600,
                                 window_minutes=300)

    root = _ab_repo(tmp_path, _AB_FILES)
    config = load_config(ROOT / "factory.example.yaml")
    assert config.pipeline.as_built_pause_at == 0.85
    stub = _AbStub()
    stub.meters = Gauge(0.9)
    with pytest.raises(asbuilt.AsBuiltError, match="is at 90% of its 5h window, past the 85%") as got:
        asyncio.run(asbuilt.AsBuiltReader(config, stub, cache=None).read(root, "HEAD"))
    assert "It reopens at" in str(got.value) and not [r for r, _ in stub.asked if r == "reader"]

    stub = _AbStub()
    stub.meters = Gauge(0.6)
    assert asyncio.run(asbuilt.AsBuiltReader(config, stub, cache=None).read(root, "HEAD")).records


def test_a_refusal_with_no_words_is_read_as_the_limit_when_the_window_is_full(tmp_path):
    """A schema call refused at the limit came back `is_error: true` with its
    words in the plain result -- which a schema call did not read -- so the
    whole reason was `True`, and every file after it failed as if broken. The
    words are read now, and the response's own gauge showing a full window is
    the limit whatever the words say."""
    import asyncio
    from factory.llm import LLM, RateLimited

    def route(envelope: dict) -> typing.Any:
        script = tmp_path / f"cli{len(list(tmp_path.glob('cli*.py')))}.py"
        script.write_text(f"import json,sys;sys.stdin.read();print(json.dumps({envelope!r}))", encoding="utf-8")
        return _route_cfg(tmp_path, command=[sys.executable, str(script)], error_key="is_error", result_key="result",
                          schema_result_key="structured_output", meter_windows_key="rate_limit_info.unifiedWindows",
                          meter_utilization_key="utilization", meter_resets_key="resetsAt")

    full = {"is_error": True, "rate_limit_info": {"unifiedWindows": {"five_hour": {"utilization": 1.0, "resetsAt": 4102444800}}}}
    with pytest.raises(RateLimited, match="window full"):
        asyncio.run(LLM(route(full)).ask("scout", "x", FileRecord))
    worded = {"is_error": True, "result": "Claude AI usage limit reached|1790665800"}
    with pytest.raises(RateLimited):
        asyncio.run(LLM(route(worded)).ask("scout", "x", FileRecord))


def test_the_session_bridges_the_runner_that_starts_and_none_of_it_reaches_the_unit(tmp_path, monkeypatch):
    import asyncio

    from factory import guides
    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.llm import LLM
    from factory.schemas import SelfDisclosure, Spec, WorkUnit

    repo = _guide_repo(tmp_path)
    config = _global_harness(load_config(), [
        "sh", "-c", "ls .agents/skills > seen.txt; cp .factory-task.md task.txt"])
    default = config.routes[config.default_route]
    default.reads_instructions, default.reads_nested, default.reads_skills = (
        ["AGENTS.md"], True, [".agents/skills"])
    executor = CommandExecutor(LLM(config), config)

    async def fake_ask(role_name, prompt, schema=None, **kw):
        return schema(summary="s", decisions=[], disclosure=SelfDisclosure())
    monkeypatch.setattr(executor.llm, "ask", fake_ask)
    told = []
    unit = WorkUnit(id="u", title="t", objective="o", files_expected=["web/Board.tsx"],
                    guide_files=[g.model_dump(mode="json") for g in guides.found(repo, "HEAD")])
    out = asyncio.run(executor.run(unit, Spec(title="t", intent="i", summary="s"), "digest",
                                   "system", repo, on_guides=told.append))
    written = {f.path: f.contents for f in out.files}
    assert set(written) == {"seen.txt", "task.txt"}, f"the bridge reached the unit's work: {sorted(written)}"
    assert "migrate" in written["seen.txt"], "the skill was not where the runner reads"
    assert "Your harness does not load these" in written["task.txt"] and "`DESIGN.md`" in written["task.txt"]
    assert told and told[0]["harness"] and {
        g["path"]: g["part"] for g in told[0]["guides"]}[".claude/skills/migrate/SKILL.md"] == "bridged"
    assert not (repo / ".agents/skills/migrate").exists()


def test_an_openhands_worker_loads_the_root_agents_md_and_the_projects_skills():
    """Through the SDK, on every run, whichever route or fallback started it --
    and nothing harness-specific is written into the repository to do it."""
    from factory.config import ExecutorConfig
    from factory.executors import ignored

    src = (ROOT / "factory" / "harnesses" / "openhands_driver.py").read_text()
    assert "load_project_skills=True, load_user_skills=False, load_public_skills=False" in src
    assert "skills=project_skills(tree)" in src and 'EXTRA_SKILL_DIRS = (".claude/skills",)' in src
    assert "load_skills_from_dir" in src and "write_text" not in src.split("def project_skills", 1)[1].split("\ndef ", 1)[0]
    ignore = ExecutorConfig().ignore
    assert ".claude" not in ignore, "a worker's edit to a skill would be dropped without a word"
    assert not ignored(".claude/skills/x/SKILL.md", ignore)
    assert ignored(".claude/settings.local.json", ignore) and ignored(".claude.json", ignore)
    assert not ignored(".gitignore", ignore) and ignored(".git/config", ignore)
