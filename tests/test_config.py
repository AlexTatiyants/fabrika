"""Configuration: roles, routes, prompts and the factory file.

Split out of test_invariants.py, which keeps one test per invariant.
"""

from helpers import *  # noqa: F403


def test_a_gate_metric_below_its_threshold_fails(tmp_path):
    from factory.config import GateConfig
    gate = GateConfig(name="mutation", command="echo '41.0% mutation score'",
                      parse_metric=r"(\d+(?:\.\d+)?)%\s+mutation score", threshold=60.0)
    result = asyncio.run(gates.run_gate(gate, tmp_path))
    assert result.metric == 41.0
    assert not result.passed, "exit zero is not the signal when a threshold is declared"


def test_editing_a_role_never_writes_to_the_shipped_example(editable_config):
    from factory.config import write_role, writable_config_path

    config, tmp_path = editable_config
    before = (tmp_path / "factory.example.yaml").read_text()

    write_role(config, "worker", {"model": "local/something-else"})

    assert (tmp_path / "factory.example.yaml").read_text() == before, \
        "the example is documentation that happens to parse; it is never written to"
    written = writable_config_path(config)
    assert written.name == "factory.yaml"
    assert "local/something-else" in written.read_text()
    assert config.role("worker").model == "local/something-else", "the live config reloaded"


def test_editing_a_role_preserves_every_comment(editable_config):
    from factory.config import write_role

    config, tmp_path = editable_config
    expected = (tmp_path / "factory.example.yaml").read_text().count("#")

    write_role(config, "reviewer", {"temperature": 0.9})
    write_role(config, "oracle", {"reasoning_effort": "medium"})

    written = (tmp_path / "factory.yaml").read_text()
    assert written.count("#") == expected, (
        "the comments in that file carry the reasoning -- why worker effort must not be lowered, "
        "why providers are pinned. An editor that strips them makes the config worse every time."
    )
    assert "hypothesis" in written.lower() and "quantisation" in written.lower()


def test_only_pipeline_roles_are_undeletable(editable_config):
    """The orchestrator calls pipeline roles by name, so removing one would fail
    mid-run. Review agents are declared in config and are yours to remove --
    including the reviewer and the adversary, which are no longer special."""
    from factory.config import delete_role, write_role

    config, _ = editable_config
    for pipeline_role in ("oracle", "worker", "rapporteur"):
        with pytest.raises(Exception) as caught:
            delete_role(config, pipeline_role)
        assert "pipeline role" in str(caught.value)

    assert [r.name for r in config.review_roles()] == ["reviewer"]

    write_role(config, "house_style", {"model": "x/y", "review": True})
    assert [r.name for r in config.review_roles()] == ["reviewer", "house_style"]

    delete_role(config, "house_style")
    assert [r.name for r in config.review_roles()] == ["reviewer"]


def test_v6_still_holds_after_an_edit(editable_config):
    """Writing a model name into factory.yaml must not put one in the package."""
    from factory.config import write_role

    config, tmp_path = editable_config
    write_role(config, "scout", {"model": "vendor/some-model-name"})
    for path in PACKAGE.rglob("*.py"):
        assert "vendor/some-model-name" not in path.read_text(encoding="utf-8")


def test_a_new_agent_lands_inside_the_roles_block(editable_config):
    """A comment block after the last role is attached to that role's last
    scalar, so a naive append emits the new role *after* it -- leaving a role
    stranded under an explainer about something else."""
    from factory.config import delete_role, write_role

    config, tmp_path = editable_config
    original = (tmp_path / "factory.example.yaml").read_text()

    write_role(config, "house_style", {"model": "a/b", "review": True, "samples": 2})
    written = (tmp_path / "factory.yaml").read_text()

    assert written.count("#") == original.count("#")
    assert "house_style:" in written

    lines = written.splitlines()
    added_at = next(i for i, l in enumerate(lines) if l.strip() == "house_style:")
    rapporteur_at = next(i for i, l in enumerate(lines) if l.strip() == "rapporteur:")
    trailing_comment_at = next(
        i for i, l in enumerate(lines) if "keep one, keep that one" in l
    )
    assert rapporteur_at < added_at < trailing_comment_at, (
        "the new role belongs inside the roles block, above the file's trailing "
        f"comment -- got rapporteur@{rapporteur_at}, house_style@{added_at}, "
        f"comment@{trailing_comment_at}"
    )
    assert config.role("house_style").review and config.role("house_style").samples == 2

    delete_role(config, "house_style")
    assert (tmp_path / "factory.yaml").read_text() == original, \
        "removing the agent restores the file exactly, comment and all"


def test_changing_the_key_drops_the_cached_client(provider_app):
    """The Authorization header is baked in when the client is first built, so
    without invalidation a key saved in the console does nothing until restart."""
    import asyncio

    from factory.config import Config, RoleConfig
    from factory.llm import LLM

    config = Config(roles={"scout": RoleConfig(name="scout", model="x/y")})
    config.api.api_key = "first"
    client = LLM(config)

    built = client.client
    assert built.headers["Authorization"] == "Bearer first"

    config.api.api_key = "second"
    assert client.client is built, "still the stale client until it is reset"

    asyncio.run(client.reset_client())
    rebuilt = client.client
    assert rebuilt is not built
    assert rebuilt.headers["Authorization"] == "Bearer second"


def test_a_call_refused_for_want_of_a_key_says_where_the_key_goes():
    """The first thing a fresh install does is survey a repository, and with no
    key the provider's 401 talks about cookies. The error has to name the fix.
    A key that was sent and refused is a different failure and keeps the
    provider's words alone."""
    import asyncio

    import httpx
    import pytest

    from factory.config import Config, RoleConfig
    from factory.llm import LLM, LLMError

    def refuse(request):
        return httpx.Response(401, json={"error": {"message": "No cookie auth credentials found"}})

    def call(key: str) -> str:
        config = Config(roles={"scout": RoleConfig(name="scout", model="x/y")})
        config.api.api_key = key
        client = httpx.AsyncClient(transport=httpx.MockTransport(refuse), base_url="http://provider")
        with pytest.raises(LLMError) as raised:
            asyncio.run(LLM(config, client=client)._post({}))
        return str(raised.value)

    keyless = call("")
    assert keyless.startswith("HTTP 401"), "route health reads the status from the front"
    assert "no provider key is set" in keyless and "crew page" in keyless
    assert "No cookie auth credentials found" in keyless, "the provider's words are kept"

    refused = call("sk-wrong")
    assert "no provider key is set" not in refused


def test_the_harness_runs_the_worker_role_s_model_not_a_second_copy_of_it():
    """The model lives in one place -- the worker role, editable in the agents
    UI. A hardcoded --model in the command line is a second place to keep in
    step, and it will drift."""
    from factory.config import harness_model, load_config

    config = load_config()
    config.api.base_url = "https://openrouter.ai/api/v1"
    config.executor.model_prefix = ""
    config.role("worker").model = "qwen/qwen3.8-27b"
    assert harness_model(config) == "openrouter/qwen/qwen3.8-27b"

    # Change it the way the UI does, and the harness follows.
    config.role("worker").model = "anthropic/claude-sonnet-4"
    assert harness_model(config) == "openrouter/anthropic/claude-sonnet-4"

    # A different provider is derived, not configured twice.
    config.api.base_url = "https://api.openai.com/v1"
    config.role("worker").model = "gpt-4o"
    assert harness_model(config) == "openai/gpt-4o"

    # A local server, which is the whole point of "any model I want".
    config.api.base_url = "http://localhost:11434/v1"
    config.role("worker").model = "qwen2.5-coder:32b"
    assert harness_model(config) == "openai/qwen2.5-coder:32b"

    # An explicit prefix still wins, and is never applied twice.
    config.executor.model_prefix = "ollama/"
    assert harness_model(config) == "ollama/qwen2.5-coder:32b"
    config.role("worker").model = "ollama/already-prefixed"
    assert harness_model(config) == "ollama/already-prefixed"


def test_the_harness_is_handed_the_key_and_never_asked_to_find_it(tmp_path, monkeypatch):
    """It is a separate process: it cannot read this app's credentials file."""
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
    (repo / "a.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "i"], cwd=repo, check=True)

    config = load_config()
    config.api.api_key = "sk-secret-value"
    config.api.base_url = "http://localhost:11434/v1"
    config.executor.kind = "command"
    config.executor.key_env = "OPENROUTER_API_KEY"
    config.executor.base_url_env = "OPENAI_API_BASE"
    # The stand-in harness writes down what it was given.
    _global_harness(config, [
        "sh", "-c",
        'printf "%s\\n%s\\n" "$OPENROUTER_API_KEY" "$OPENAI_API_BASE" > seen.txt',
    ])
    executor = CommandExecutor(LLM(config), config)

    async def fake_ask(role_name, prompt, schema=None, **kw):
        return schema(summary="s", decisions=[], disclosure=SelfDisclosure())
    monkeypatch.setattr(executor.llm, "ask", fake_ask)

    out = asyncio.run(executor.run(WorkUnit(id="u", title="t", objective="o"),
                                   Spec(title="t", intent="i", summary="s"),
                                   "digest", "system", repo))
    seen = next(f.contents for f in out.files if f.path == "seen.txt").split("\n")
    assert seen[0] == "sk-secret-value"
    assert seen[1] == "http://localhost:11434/v1"


def test_a_container_user_with_no_home_cannot_run_npm():
    """--user gives the container a uid that owns no home directory, so anything
    that caches writes to `/` and dies with EACCES. That is how clinic reached
    gate 0 with an environment that could not execute its own gates."""
    import pathlib
    from factory.config import load_config
    from factory.containers import DockerRunner

    config = load_config()
    config.docker.run_as_host_user = True
    argv = DockerRunner("img", "/work", config.docker).argv(
        "npm install", cwd=pathlib.Path("/tmp/w"), network=True, name="n")

    joined = " ".join(argv)
    assert "--user" in joined
    assert "HOME=/work" in joined, "the one directory certainly writable by that uid"
    assert "NPM_CONFIG_CACHE=" in joined


def test_published_ports_are_replaced_not_appended(tmp_path):
    """The collision the whole scheme exists to prevent. Compose merges
    sequences across files, so `ports: []` appends nothing and the project's own
    mapping survives -- two features then fight over one host port. Only the
    !override tag replaces."""
    from factory.config import load_config
    from factory.containers import ComposeRunner
    from factory.schemas import EnvironmentSpec

    (tmp_path / "docker-compose.yml").write_text(
        'services:\n  postgres:\n    image: postgres:16\n    ports: ["5433:5432"]\n'
        '  backend:\n    build: ./backend\n    ports: ["8001:8000"]\n')
    spec = EnvironmentSpec(kind="compose", compose_file="docker-compose.yml",
                           compose_service="backend", workdir="/work", image="factory/x:1")
    text = ComposeRunner(tmp_path, spec, "n", load_config().docker)._override_text(tmp_path / "w")

    assert text.count("ports: !override []") == 2, "every service, not just the gate one"
    assert "5433" not in text and "8001" not in text
    # And the gate service is swapped wholesale, or compose rebuilds production.
    assert "build: !reset null" in text
    assert 'image: "factory/x:1"' in text


def test_a_missing_env_file_is_met_from_its_example_and_names_are_freed(tmp_path):
    """A compose file that reads `.env` cannot be loaded from a clean checkout,
    and it reads `.env` because the file holds a developer's secrets and is
    ignored by git. The project's `.env.example` says what harmless values look
    like, so the stack the checks run in is given those -- and nothing is
    written into the person's repository."""
    from factory.config import load_config
    from factory.containers import ComposeRunner
    from factory.schemas import EnvironmentSpec

    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  db:\n    image: postgres:15\n    container_name: app-db\n"
        "  api:\n    build: ./api\n    container_name: app-api\n"
        "    env_file:\n      - .env\n"
        "    environment:\n      PORT: '9000'\n")
    (tmp_path / ".env.example").write_text(
        "# comment\nDATABASE_URL=postgres://db/app\nPORT=8000\nexport MODE='dev'\n"
        "LLM_MODEL_NAME=\n")
    spec = EnvironmentSpec(kind="compose", compose_file="docker-compose.yml",
                           compose_service="api", workdir="/work",
                           env={"LLM_MODEL_NAME": "mock", "JWT_SECRET": "not-a-secret"})
    text = ComposeRunner(tmp_path, spec, "n", load_config().docker)._override_text(tmp_path / "w")

    assert text.count("container_name: !reset null") == 2, "two stacks cannot share a name"
    assert "env_file: !override []" in text, "the file that is not there is not asked for"
    assert '"DATABASE_URL": "postgres://db/app"' in text
    assert '"MODE": "dev"' in text
    assert '"PORT"' not in text, "what the service sets itself is not overridden by an example"
    assert '"LLM_MODEL_NAME": "mock"' in text and '"JWT_SECRET": "not-a-secret"' in text
    assert not (tmp_path / ".env").exists(), "nothing is written into the repository"

    # A file that is there is left exactly as the project declared it.
    (tmp_path / ".env").write_text("DATABASE_URL=real\n")
    text = ComposeRunner(tmp_path, spec, "n", load_config().docker)._override_text(tmp_path / "w")
    assert "env_file:" not in text and "postgres://db/app" not in text


def test_a_stack_that_failed_to_start_is_still_torn_down(tmp_path):
    """`up` can fail having already created containers, and those hold their
    names and ports until something removes them. The first failure left a
    postgres behind that blocked every retry."""
    import asyncio
    from factory.config import load_config
    from factory.containers import ComposeRunner
    from factory.schemas import EnvironmentSpec

    (tmp_path / "docker-compose.yml").write_text("services:\n  api:\n    image: busybox\n")
    spec = EnvironmentSpec(kind="compose", compose_file="docker-compose.yml",
                           compose_service="api", workdir="/work")
    runner = ComposeRunner(tmp_path, spec, "n", load_config().docker)

    async def failing(argv, timeout=None):
        from factory.gates import Execution
        return Execution(exit_code=1, output="boom")

    import factory.containers as mod
    original, mod._run = mod._run, failing
    try:
        result = asyncio.run(runner.execute("true", cwd=tmp_path, timeout_s=5))
        assert result.exit_code == 1 and not result.started
        assert runner._up is True, (
            "it must believe a stack exists, or teardown skips containers that do")
    finally:
        mod._run = original


def test_concurrent_gates_bring_the_stack_up_once(tmp_path):
    """Gates run concurrently (AC-6.1). Without a lock each one sees no stack,
    each runs `compose up`, and the losers fail on a container-name conflict --
    which reads as a broken environment rather than as three clients asking for
    the same thing."""
    import asyncio
    from factory.config import load_config
    from factory.containers import ComposeRunner
    from factory.gates import Execution
    from factory.schemas import EnvironmentSpec
    import factory.containers as mod

    (tmp_path / "docker-compose.yml").write_text("services:\n  api:\n    image: busybox\n")
    spec = EnvironmentSpec(kind="compose", compose_file="docker-compose.yml",
                           compose_service="api", workdir="/work")
    runner = ComposeRunner(tmp_path, spec, "n", load_config().docker)

    ups = []

    async def fake(argv, timeout=None):
        if "up" in argv:
            ups.append(argv)
            await asyncio.sleep(0.05)      # long enough for a racer to slip in
        return Execution(exit_code=0, output="")

    async def three():
        return await asyncio.gather(*(
            runner.execute(f"gate{i}", cwd=tmp_path, timeout_s=5) for i in range(3)))

    original, mod._run = mod._run, fake
    try:
        results = asyncio.run(three())
    finally:
        mod._run = original

    assert len(ups) == 1, f"the stack must come up once, not {len(ups)} times"
    assert all(r.exit_code == 0 for r in results), "and every gate still runs"


def test_a_survey_that_has_begun_is_not_shown_as_the_one_that_failed(tmp_path):
    """A running survey looked exactly like the failed one before it -- the same
    red card and the same error -- for the eleven minutes it takes."""
    from factory.config import Config
    from factory.projects import ProjectRegistry

    repo = tmp_path / "repo"; repo.mkdir()
    config = Config(); config.paths.evidence = str(tmp_path / "evidence")
    registry = ProjectRegistry(config.evidence_path)
    project = registry.create(repo, "demo")

    registry.record_failure(project, "LLMError: route 'claude-code' did not answer within 900s")
    registry.record_survey_started(project)
    again = registry.get(project.id)
    assert again.state.stage == "surveying"
    assert not again.state.error

    project.state.stage = "ready"
    registry.save(project, note="approved")
    registry.record_survey_started(project)
    assert registry.get(project.id).state.stage == "ready", (
        "only a failed project changes; any other keeps what it holds until it is replaced")


def test_env_expansion_skips_shell_commands_and_nothing_else(monkeypatch):
    """`$NAME` in a command belongs to the shell that will run it.

    `_expand` exists so an API key can live in the environment rather than in
    the file (AC-2.4), and it used to rewrite every string in the tree. A shell
    command is the one place that is wrong, and nothing fails when it happens:
    `rework.oracle_command` carried `kill $UPID 2>/dev/null; exit $RC`, both
    names were replaced with "", and it ran as `kill  2>/dev/null; exit`. The
    server was never killed, and bare `exit` returns the status of the last
    command -- the failed kill -- so the blind acceptance suite reported failure
    on every run whatever the code did, and held the port for the next one.

    Both halves are asserted here. A command that keeps its variables is useless
    if the settings that need expanding stopped getting it.
    """
    monkeypatch.setenv("A_TEST_SECRET", "sk-secret")
    monkeypatch.delenv("A_TEST_UNSET", raising=False)

    expanded = config_module._expand({
        "api": {"api_key": "$A_TEST_SECRET"},
        "rework": {"breaker_command": "kill $UPID; exit $RC"},
        "routes": {"r": {"command": ["sh", "-c", "echo $HOME"],
                         "setup_note": "reads $A_TEST_SECRET"}},
        "pipeline": {"package_probes": ["pip list $A_TEST_UNSET"]},
    })

    # Commands, whether a string or an argv, reach the shell untouched.
    assert expanded["rework"]["breaker_command"] == "kill $UPID; exit $RC"
    assert expanded["routes"]["r"]["command"] == ["sh", "-c", "echo $HOME"], \
        "a list under a command key is an argv and every element is the command"
    assert expanded["pipeline"]["package_probes"] == ["pip list $A_TEST_UNSET"], (
        "an unset name in a command is the dangerous case: it expands to \"\" "
        "and the command still runs, silently changed"
    )

    # Everything else still expands, including prose that sits beside a command.
    assert expanded["api"]["api_key"] == "sk-secret", \
        "the setting this mechanism exists for stopped being expanded"
    assert expanded["routes"]["r"]["setup_note"] == "reads sk-secret"


def test_a_settings_file_is_never_found_empty_while_it_is_rewritten(tmp_path):
    """The draw and gauge histories are read, changed and written back, and were
    written by emptying the file and filling it again. A reader that arrived in
    between found nothing -- and a reader that writes back what it found writes
    nothing back, which is weeks of history gone."""
    import threading as _threading

    from factory.config import write_credential
    from factory.meters import DrawStore

    store = DrawStore(tmp_path / "draws.json")
    store.path.write_text(json.dumps({"routes": {f"route-{i}": [1.0] * 8 for i in range(3000)}}))
    stop = _threading.Event()
    empty: list[int] = []

    def read():
        while not stop.is_set():
            if "route-0" not in DrawStore(store.path).all():
                empty.append(1)

    reader = _threading.Thread(target=read)
    reader.start()
    for i in range(100):
        store.record({"route-1": float(i)})
    stop.set()
    reader.join()
    assert not empty, f"a reader found the history empty {len(empty)} times"
    assert [p.name for p in tmp_path.iterdir()] == ["draws.json"], "nothing is left beside it"

    # A credential is written whole and private from its first byte.
    # The credentials file sits beside the config, so this config is placed in
    # the test's own directory -- never beside the one in the repository.
    config = load_config(ROOT / "factory.example.yaml")
    config.source_path = str(tmp_path / "factory.yaml")
    path = write_credential(config, "api_key", "sk-test-not-a-key")
    assert path.parent == tmp_path
    assert (path.stat().st_mode & 0o777) == 0o600
    assert json.loads(path.read_text()) == {"api_key": "sk-test-not-a-key"}


def test_the_driver_is_wired_into_the_configured_command():
    """The shipped config runs the OpenHands driver inside the unit's
    container: the route that carries it copies the driver in from this
    repository and starts it there."""
    from factory.config import load_config

    config = load_config(str(Path("factory.yaml")))
    route = next(r for r in config.routes.values() if r.harness_label == "OpenHands")
    assert route.session_in_container, "the harness runs on this machine"
    assert route.container_build_files.get("factory/harnesses/openhands_driver.py") \
        == "/opt/harness/driver.py", "the image is not built with this repository's driver"
    assert "/opt/harness/driver.py" in route.container_session_command, \
        "the configured harness is not the driver"
    # aider's file flags must be off: OpenHands opens what it needs, and these
    # would be arguments it does not understand.
    assert config.executor.file_flag == "" and config.executor.read_flag == ""


def test_an_extraction_refactor_is_not_reported(tmp_path):
    """The check must not cry wolf. Splitting a large file guts the original by
    every arithmetic measure, and is exactly right -- the lines are in the new
    files beside it, which is the thing the old guard only claimed to test."""
    from factory.config import Config

    body = "".join(f"def helper_{i}():\n    return {i}\n" for i in range(200))
    keep = "".join(f"def helper_{i}():\n    return {i}\n" for i in range(20))
    moved = "".join(f"def helper_{i}():\n    return {i}\n" for i in range(20, 200))
    sandbox = _branch(
        tmp_path,
        {"page.py": body},
        {"page.py": keep, "extracted.py": moved},
    )
    assert pipeline.check_destructive_writes(sandbox, Config()) == [], \
        "a refactor that moved its code into a new file was reported as destruction"


def test_a_small_file_and_a_new_file_are_left_alone(tmp_path):
    from factory.config import Config

    sandbox = _branch(
        tmp_path,
        {"tiny.py": "a = 1\nb = 2\nc = 3\n"},
        {"tiny.py": "a = 1\n", "brand_new.py": "x = 1\n"},
    )
    assert pipeline.check_destructive_writes(sandbox, Config()) == [], \
        "a file below the minimum, or one that did not exist before, was reported"


def test_the_detector_asks_where_the_lines_went_not_how_many_there_are(tmp_path):
    """The specific defect: new code paying for deleted code.

    The unit wrote a 77-line migration and tripled a schema file, and by line
    count that covered deleting 1,293 lines of router. Growth elsewhere must not
    excuse a loss here unless it is the *same* code.
    """
    from factory.config import Config

    big = "".join(f"def original_{i}():\n    return {i}\n" for i in range(200))
    sandbox = _branch(
        tmp_path,
        {"routers/queue.py": big},
        {"routers/queue.py": "def original_0():\n    return 0\n",
         # Plenty of new lines, none of them the ones that vanished.
         "migration.py": "".join(f"op.add_column('c{i}')\n" for i in range(400))},
    )
    findings = pipeline.check_destructive_writes(sandbox, Config())
    assert len(findings) == 1 and findings[0].files == ["routers/queue.py"], \
        "unrelated new code bought permission to delete a file"


def test_two_lanes_keep_the_crew_apart_on_the_day_both_routes_run_out():
    """One fallback for everybody reads as prudence and is the opposite.

    A single `fallback` in `defaults` gives every role the same landing, so the
    day two routes are dry the whole crew is on one vendor -- and the adversary
    is reviewing code its own family wrote, with nothing on any screen saying
    so. Two lanes, builders in one and checkers in the other, keeps
    `adversary != worker` and `arbiter != reviewer` true on the worst day rather
    than only on a good one.

    Different vendors, not different sizes: `model_family` reads the id's vendor
    prefix, and two models from one shop share priors whatever their parameter
    counts.
    """
    import copy
    cfg = config_module.load_config()

    assert cfg.fallbacks, "no lanes are defined, so every role falls back alone or not at all"
    assert len(cfg.fallbacks) >= 2, "one lane is one vendor for the whole crew"
    assert not config_module.independence_problems(cfg), \
        "the configured lanes do not survive both routes running out"

    # The two lanes must be different families, or the split is decorative.
    landings = {config_module.landing_family(cfg, n)
                for n, r in cfg.roles.items() if r.enabled and r.fallback_route}
    assert len(landings) >= 2, f"every role lands on the same family: {landings}"

    # And the failure this guards is reported, not merely avoided.
    same = copy.deepcopy(cfg)
    one = next(iter(cfg.fallbacks.values()))
    for name, role in same.roles.items():
        if role.fallback_route:
            same.roles[name] = role.model_copy(update={"fallback_model": one.model})
    said = config_module.independence_problems(same)
    assert said, "putting every lane on one model is not reported"
    assert any("both fall back onto" in line for line in said)
    assert any("`fallbacks:`" in line for line in said), \
        "the warning does not say what to do about it"


def test_a_resolved_lane_keeps_the_name_a_person_chose():
    """Resolving is not consuming.

    A role says `fallback: check`, and `load_config` turns that into a route
    and a model because that is what the run reads. It also used to drop the
    name -- and every screen that shows a human where an agent goes when its
    route refuses reads the name. So the crew screen printed a dash in the
    fallback column for all fifteen agents, and both lane cards said "no
    agent uses this lane", under two lanes that the entire crew uses.

    The wrong answer was the confident one, which is the kind worth a test."""
    config = load_config(Path("factory.yaml"))
    lanes = set(config.fallbacks)
    assert lanes, "the shipped config defines no lanes; this test is measuring nothing"

    named = [r for r in config.roles.values() if r.fallback]
    assert named, "resolving a lane is eating its name again"
    for role in named:
        assert role.fallback in lanes, f"{role.name} names a lane that does not exist"
        # And the name did not replace the resolution it stands for.
        assert role.fallback_route and role.fallback_model, (
            f"{role.name} kept its lane name but lost the route it resolves to")


def test_a_fallback_that_lands_on_the_family_it_argues_with_says_so_at_rest():
    """A fallback is chosen at rest and taken at 2am.

    The failure it guards against is a route reaching its ceiling, which is
    precisely the moment nobody is reading. A panel that falls back onto the
    family that wrote the code is reviewing its own work, and it would do that
    without a word.
    """
    import copy
    cfg = config_module.load_config()
    cfg.roles["worker"].route = "claude-code"     # where the worker runs, here

    def problems(role, route, model):
        c = copy.deepcopy(cfg)
        c.roles[role] = c.roles[role].model_copy(update={
            "fallback_route": route, "fallback_model": model})
        return config_module.independence_problems(c)

    # The API route is a third family, so this costs nothing.
    assert not problems("reviewer", "default", "z-ai/glm-5.3-flash"), \
        "a fallback onto an unrelated family is being reported as a problem"

    # claude-code is where the worker runs, and the reviewer exists to read
    # the worker's output without sharing its priors.
    # A named CLI route is the family: one vendor, whatever model off its
    # list -- so this lands where the worker already is.
    said = problems("reviewer", "claude-code", "sonnet")
    assert said, "a fallback onto the worker's own family passes unremarked"
    assert "falls back onto" in said[0] and "worker" in said[0]
    assert "nobody is watching" in said[0], \
        "the warning does not say why this one matters more than the others"


def test_the_breaker_attacks_once_and_its_probes_are_re_run_for_ever():
    """A repair loop working inside a frozen spec is not a new system to attack.

    `_run_breaker` authored a fresh suite every repair round. On one feature
    that was four agentic sessions -- read the checkout, write a probe, run it,
    watch it fail, check it failed for the reason claimed -- producing nine
    probe files between them, at eleven million tokens. It exhausted the route's
    quota before the review panel ran, so six agents died on empty and six of
    the seven calls that reached the human were about fabrika rather than about
    the code.

    The re-running was always free: `accumulated` carries every probe forward
    and they all run again each round, which is what catches a repair breaking
    something that used to pass. Only the inventing was paid for four times.
    """
    src = inspect.getsource(pipeline.Factory._run_breaker)

    assert "reauthor = round_index == 0 or not accumulated" in src, \
        "the breaker authors a fresh suite on every repair round"

    # A round that produced nothing leaves nothing to re-run, and skipping on
    # that would turn one bad session into a feature with no adversary at all.
    assert "or not accumulated" in src, \
        "a failed first attack is never retried, so the feature gets no adversary"

    # And the command has to come forward with the probes. Without it
    # `accumulated` is a list of files nothing runs, which is the same outcome
    # as not having written them -- silently, because a breaker that runs no
    # probes reports no failures and reads as a clean bill of health.
    assert 'store.payload("breaker_suite")' in src, \
        "the probes are carried forward and the command that runs them is not"
    carried = src[src.index("reauthor = round_index"):]
    carried = carried[:carried.index("else:")]
    assert "command=carried" in carried, \
        "the carried suite has no command, so the accumulated probes never run"

    # Effort is per turn on a role that takes hundreds of them.
    cfg = config_module.load_config()
    assert cfg.roles["breaker"].reasoning_effort != "high", \
        "the breaker pays high reasoning effort on every turn of a long loop"


def test_test_writers_have_room_for_their_accounting_call():
    """They no longer return file text, but the tagging must never fail: an
    untagged suite runs and verifies nothing, which is the one outcome the
    verify lane exists to prevent."""
    from factory.config import load_config

    config = load_config(str(Path("factory.yaml")))
    for name in ("oracle", "breaker"):
        assert config.role(name).max_tokens >= 32000, (
            f"{name} has no headroom for the answer that says what its files are for"
        )


def test_a_session_that_wrote_nothing_does_not_bench_a_working_route(tmp_path):
    """The consequence, on the path that has one.

    A harness that finishes and writes no file is ordinary -- a repairer that
    decides the finding was a false negative writes nothing, which is the run
    this was found in. What must not follow is the route being marked spent:
    that holds it for half an hour and sends every agent after it to a
    substitute, over a gauge that said the plan had room.
    """
    import asyncio
    from factory.config import RoleConfig
    from factory.executors import CommandExecutor
    from factory.llm import LLM

    repo = _blank_repo(tmp_path)
    config = config_module.load_config()
    config.executor.kind = "command"
    for role in config.roles.values():
        role.route = ""
        role.fallback_route = ""

    # A stand-in for the tool: prints the stream a healthy session prints,
    # writes nothing, exits 0.
    script = tmp_path / "quiet_harness.py"
    script.write_text(
        "import sys\nsys.stdin.read()\nprint(%r)\n"
        % _session_stream(result="Nothing to change; the index already exists."),
        encoding="utf-8")
    config.routes["stub"] = _streamed_session_route(
        name="stub", session_command=[sys.executable, str(script), "{worktree}"])
    config.roles["oracle"] = RoleConfig(
        name="oracle", model="sonnet", route="stub", fallback_route="default")

    llm = LLM(config)
    session = asyncio.run(CommandExecutor(llm, config).author(
        brief="check the index", tree=repo, role="oracle", confine="tests/oracle"))

    assert session.files == [], "the fixture is meant to write nothing"
    assert llm.spent("stub") is None, \
        "a route that answered was benched for half an hour by its own gauge"
    assert llm.fallbacks == [], \
        "the work was sent to a substitute although the route never refused it"


def test_a_config_that_never_heard_of_routes_still_has_one():
    """Every version before this named no route anywhere and must keep working.
    The `api`/`executor` pair becomes one visible entry rather than staying an
    implicit pair of provider systems."""
    from factory.config import Config, RoleConfig

    cfg = Config(roles={"scout": RoleConfig(name="scout", model="m")})
    route = cfg.route_for("scout")
    assert route.name == "default" and route.kind == "api"
    assert route.billed, "an api route is billed per token; the guard applies to it"
    assert cfg.route_problem("scout") == ""


def test_a_role_pointed_at_an_unconfigured_route_is_refused_at_load(tmp_path):
    """Never a silent fallback to the default. A role would then run somewhere
    the human did not choose, which is the failure a missing model already
    raises for."""
    from factory.config import ConfigError, load_config

    src = (Path("factory.yaml")).read_text(encoding="utf-8")
    assert "\n  worker:\n" in src
    src = src.replace("\n  worker:\n", "\n  worker:\n    route: nowhere\n", 1)
    doctored = tmp_path / "factory.yaml"
    doctored.write_text(src, encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        load_config(str(doctored))
    assert "nowhere" in str(caught.value)


def test_the_shipped_codex_route_reads_its_events_as_jsonl():
    """`codex exec --json` streams events; reading it with the default
    single-envelope parser would fail every call, success or not, with 'did
    not return JSON' -- the exact error this route once shipped with."""
    from factory.config import load_config

    route = load_config(str(Path("factory.yaml"))).route("codex")
    assert route.stdout_format == "jsonl"
    assert route.result_key == "item.text"
    assert route.error_key == "error"


def test_a_route_knows_what_it_can_and_cannot_serve():
    """OpenRouter completes and cannot author -- which is why OpenHands exists,
    as a harness over a route rather than a route of its own."""
    from factory.config import RouteConfig

    api = RouteConfig(name="openrouter", kind="api")
    assert api.completes() and not api.authors() and api.billed

    cli = RouteConfig(name="claude-code", kind="cli", metered="turns",
                      command=["claude"], session_command=["claude"])
    assert cli.completes() and cli.authors() and not cli.billed


def test_a_role_that_writes_files_cannot_be_pointed_at_a_completion_only_route():
    """Caught up front. Discovered mid-run, it fails deep inside a phase after
    everything before it has been paid for, with an error about a subprocess."""
    from factory.config import Config, RoleConfig, RouteConfig

    cfg = Config(
        roles={"worker": RoleConfig(name="worker", model="m", route="chat-only")},
        routes={"chat-only": RouteConfig(name="chat-only", kind="cli",
                                         command=["something"])},
    )
    assert cfg.route_problem("worker", "complete") == ""
    problem = cfg.route_problem("worker", "author")
    assert "cannot give it a filesystem" in problem


def test_a_disconnected_route_is_refused_before_it_is_used():
    from factory.config import Config, RoleConfig, RouteConfig

    cfg = Config(
        roles={"scout": RoleConfig(name="scout", model="m", route="codex")},
        routes={"codex": RouteConfig(name="codex", kind="cli", command=["codex"],
                                     enabled=False)},
    )
    assert "not connected" in cfg.route_problem("scout")


def test_the_shipped_routes_declare_what_they_can_actually_do():
    """A route's declared capabilities are what the guards read, so a wrong one
    lets a role be pointed somewhere it cannot run and fail deep in a phase."""
    from factory.config import load_config

    cfg = load_config(str(Path("factory.yaml")))
    assert cfg.route("default").authors(), "the executor's harness must stay reachable"
    for name in ("claude-code", "codex"):
        route = cfg.route(name)
        assert route.completes(), f"{name} is configured but cannot serve a completion"
        assert not route.billed, f"{name} is a subscription; it must not claim to be billed"
    # A key billed per token since its plan sign-in went away: its spend must
    # reach the budget guard, and its key must reach the sealed container.
    keyed = cfg.route("gemini-cli")
    assert keyed.completes() and keyed.billed and keyed.key_env
    assert keyed.container_install, "a completion runs sealed or not at all"


def test_a_route_that_cannot_hold_an_answer_to_a_schema_is_refused_up_front():
    """The mechanism, exercised on a route that declares it.

    It exists because a route which serves prose well and schemas badly would
    otherwise be discovered by a phase that hangs to `timeout_s`, once per
    round, with nothing in the log naming the cause. Kept and tested even though
    no shipped route sets it today: the flag is the difference between a clear
    refusal and a silent hang, and it should not have to be re-derived the next
    time a tool has this shape.
    """
    from factory.config import Config, RoleConfig, RouteConfig

    cfg = Config(
        roles={"spec_writer": RoleConfig(name="spec_writer", model="m", route="prose-only")},
        routes={"prose-only": RouteConfig(name="prose-only", kind="cli",
                                          command=["tool"], schema_completions=False)},
    )
    problem = cfg.route_problem("spec_writer", "complete")
    assert "cannot return a schema-shaped answer" in problem
    assert "measured" in problem, "the refusal must say it is a finding, not a guess"


def test_the_shipped_claude_route_switches_tools_off():
    """Required and cheaper, both measured.

    Several roles are defined by not having tools -- the review panel gets one
    structured completion over a text bundle and nothing else, and the spec writer's
    whole relationship to the repository is the digest it was handed. Leaving
    tools on voids both contracts silently, and costs four times as much (4.6c
    against 18.5c for the same answer) because every tool definition rides in
    the system prompt of every call.
    """
    from factory.config import load_config

    route = load_config(str(Path("factory.yaml"))).route("claude-code")
    assert route.schema_completions is True, (
        "this was set false against an install a year behind, where the same "
        "command returned 400 and the same schema never returned; on a current "
        "build it answers in about ten seconds")
    assert "--tools" in route.command
    assert route.command[route.command.index("--tools") + 1] == "", \
        "tools are not disabled, so every completion role gains a filesystem"
    assert route.schema_result_key == "structured_output", \
        "`result` holds the agent's prose, not the answer"


def test_a_roles_route_survives_a_console_edit(tmp_path):
    """The console rewrites factory.yaml on every role edit. A key it does not
    know about is a key it drops, and the agent silently moves back to the
    default provider."""
    from factory.config import EDITABLE_ROLE_KEYS, load_config, write_role

    assert "route" in EDITABLE_ROLE_KEYS
    target = tmp_path / "factory.yaml"
    target.write_text((Path("factory.yaml")).read_text(encoding="utf-8"), encoding="utf-8")

    reloaded = write_role(load_config(str(target)), "spec_writer",
                          {"route": "claude-code", "model": "opus-5"})
    assert reloaded.role("spec_writer").route == "claude-code"
    assert reloaded.role("spec_writer").model == "opus-5"
    assert reloaded.route_for("spec_writer").kind == "cli"
    # And the comments that carry the reasoning are still there.
    assert "metered is load-bearing" in target.read_text(encoding="utf-8").replace("`", "")


def test_an_api_route_reports_its_key_without_asking_the_network():
    """It is a fact on disk. Saying 'not checked' about it put an amber lamp on
    the one route that had been working all day."""
    import asyncio
    from factory.config import Config, RoleConfig
    from factory.llm import LLM
    from factory.routes import RouteMonitor

    cfg = Config(roles={"scout": RoleConfig(name="scout", model="m")})
    cfg.api.api_key = "sk-test"
    from factory.config import sync_default_route
    sync_default_route(cfg)
    rows = asyncio.run(RouteMonitor(cfg, LLM(cfg)).survey())
    assert next(r for r in rows if r["name"] == "default")["state"] == "ok"


def test_the_key_reaches_the_default_route_after_it_is_loaded():
    """`load_config` builds the Config and only then reads the credential store,
    so a route synthesised during construction carries the key as it was before
    it was loaded -- empty. The console drew 'not signed in' over a provider
    that had been working all day."""
    from factory.config import load_config

    cfg = load_config(str(Path("factory.yaml")))
    assert cfg.route("default").api_key == cfg.api.api_key


def test_every_cli_route_tells_a_human_how_to_connect_it():
    """A card that says 'not signed in' and stops is a dead end. The sign-in
    cannot happen in the browser, so the exact line has to be on the card."""
    from factory.config import load_config

    cfg = load_config(str(Path("factory.yaml")))
    for name, route in cfg.routes.items():
        if route.kind != "cli":
            continue
        # A route that spends a key is connected by pasting it on the setup
        # screen; one that signs in needs the line to run in a terminal.
        assert route.setup_command or route.key_env, f"{name} has no command to run and takes no key"
        assert route.setup_steps, f"{name} has no steps"
        assert route.install_command, f"{name} does not say how to install it"
        assert route.models, f"{name} offers no models, so it can never be selected"
        assert route.setup_note, f"{name} does not say why this cannot be done in the UI"

def test_the_shipped_models_are_tiers_or_real_ids_and_never_invented():
    """Two shapes are valid and a third is not.

    A tier (`opus`) is resolved by the installed tool and is what a human
    usually means: the scout wants the cheap fast one, the spec writer wants the
    best one, and written that way the intent survives a model generation.
    A full id (`claude-opus-5`) is what a reproducible run pins. A shortened id
    (`opus-5`) is neither -- it is a 404 that arrives only after someone has
    connected the route and selected the model.
    """
    from factory.config import load_config

    models = load_config(str(Path("factory.yaml"))).route("claude-code").models
    assert models, "no models means the route can never be selected"
    tiers = {"opus", "sonnet", "haiku", "fable"}
    for m in models:
        assert m in tiers or m.startswith("claude-"), (
            f"{m!r} is neither a tier nor a model id this API answers to")
    assert tiers & set(models), (
        "no tier is offered, so every pick has to be re-decided by hand each "
        "time a model generation ships")


# --------------------------------------------------------------------------
# "Just connect it" -- a route that picks its own model
#
# Looking up an id is the step between connecting a harness and using it, and
# it is the step that broke first: a model string that was never valid reached
# the config, and the failure arrived as a 404 only after a human had connected
# the route and selected it. A route that can answer without being told which
# model removes that step entirely.
# --------------------------------------------------------------------------


def test_a_role_may_name_no_model_where_the_route_supplies_one():
    from factory.config import Config, RoleConfig, RouteConfig

    cfg = Config(
        roles={"scout": RoleConfig(name="scout", model="", route="cli")},
        routes={"cli": RouteConfig(name="cli", kind="cli", command=["tool"],
                                   default_model_ok=True)},
    )
    assert cfg.route_problem("scout") == ""


def test_a_role_naming_no_model_on_a_route_that_cannot_choose_is_refused():
    """This is not the fallback V-6 forbids -- that is a model this package
    invents when a human named none. Here the human explicitly delegated to the
    tool, and a route that cannot honour that must say so rather than sending
    an empty model id."""
    from factory.config import Config, RoleConfig

    cfg = Config(roles={"scout": RoleConfig(name="scout", model="")})
    problem = cfg.route_problem("scout")
    assert "names no model" in problem
    assert "cannot supply one of its own" in problem


def test_all_four_tiers_are_offered():
    """Three rungs and one different axis.

    haiku/sonnet/opus are a price-capability ladder; `fable` is not a rung on
    it but a different trade, for the roles judged on prose a human reads. A
    picker missing it forces those roles onto a ladder that does not measure
    what they are for.
    """
    from factory.config import load_config

    models = set(load_config(str(Path("factory.yaml"))).route("claude-code").models)
    assert {"haiku", "sonnet", "opus", "fable"} <= models


def test_a_routed_session_is_not_given_another_provider_s_model_prefix():
    """`harness_model` derives a LiteLLM prefix from `api.base_url`, which is
    right for a harness spending this app's key and wrong for a route with its
    own sign-in. Prefixed, the id names a provider that route never heard of.

    Measured: the first unit ever routed to a second harness was launched with
    the prefix still attached, answered `unrecognized_model`, exited 1 twice,
    and fell through to `direct` -- a working harness recorded as one that had
    declined the work.
    """
    from factory.config import load_config, session_model

    cfg = load_config(str(Path("factory.yaml")))
    cfg.api.base_url = "https://openrouter.ai/api/v1"
    cfg.executor.model_prefixes = {"openrouter.ai": "openrouter/"}
    cfg.roles["worker"].route = "claude-code"
    cfg.roles["worker"].model = "sonnet"
    route = cfg.route("claude-code")

    assert session_model(cfg, "worker", route) == "sonnet"
    # And the global harness still gets the prefix it needs.
    assert session_model(cfg, "integrator", None).startswith("openrouter/")


def test_a_routed_session_books_its_spend_against_the_right_meter():
    """A session on a subscription reports a figure that is not money. Counted
    as spend it stops a run at the reserve floor for nothing; discarded, the
    packet says a phase that ran for six turns cost nothing and used no model."""
    from factory.config import Config, RoleConfig, RouteConfig
    from factory.llm import LLM

    cfg = Config(
        roles={"worker": RoleConfig(name="worker", model="m", route="sub")},
        routes={"sub": RouteConfig(name="sub", kind="cli", metered="turns",
                                   session_command=["tool"])},
    )
    llm = LLM(cfg)
    llm.record_external("worker", "the-model", 0.42, 10, 5,
                        route="sub", billed=False, turns=6)
    report = llm.usage_report()
    assert report["total_cost"] == 0.0, "notional spend reached the budget guard"
    assert report["notional_cost"] == 0.42
    assert report["roles"]["worker"]["turns"] == 6
    assert report["roles"]["worker"]["routes"] == ["sub"]
    assert report["roles"]["worker"]["models"] == ["the-model"]


def test_a_fully_billed_run_raises_no_such_finding():
    from factory.config import load_config

    cfg = load_config(str(Path("factory.yaml")))
    assert pipeline.check_budget_coverage({"unbilled_roles": []}, cfg) == []


def test_the_metered_route_streams_because_the_gauge_only_appears_there():
    """The single-envelope form carries the answer and the cost but not this."""
    from factory.config import load_config

    route = load_config(str(Path("factory.yaml"))).route("claude-code")
    assert route.meter_windows_key, "the route claims no gauge"
    assert route.stdout_format == "jsonl"
    for command in (route.command, route.session_command):
        assert "stream-json" in command, (
            "this call cannot see the window it consumes")


def test_the_shipped_assignment_satisfies_its_own_independence_rules():
    """Three of these rules live in `factory.yaml`'s comments, addressed to a
    human: "if you change `worker`, check that this is still a different
    family". That is a rule enforced by memory, at the one moment memory is
    least reliable -- somebody changing a model."""
    from factory.config import load_config, independence_problems

    problems = independence_problems(load_config(str(Path("factory.yaml"))))
    assert problems == [], "\n".join(problems)


def test_an_agent_sharing_priors_with_what_it_doubts_is_a_finding():
    from factory.config import Config, RoleConfig
    from factory.pipeline import check_independence

    same = Config(roles={
        "worker": RoleConfig(name="worker", model="vendor/m"),
        "oracle": RoleConfig(name="oracle", model="vendor/other"),
        "adversary": RoleConfig(name="adversary", model="vendor/third"),
    })
    findings = check_independence(same)
    assert len(findings) == 1 and findings[0].category == "independence"
    assert "blind spot rendered twice" in findings[0].detail
    # It must not read as "the findings are wrong" -- they may be fine.
    assert "does not mean the findings in this packet are wrong" in findings[0].detail


def test_a_route_counts_as_a_family_because_a_tier_is_not_independence():
    """Two agents on one subscription are talking to one vendor whatever tier
    each picked."""
    from factory.config import Config, RoleConfig, RouteConfig, model_family

    cfg = Config(
        roles={"worker": RoleConfig(name="worker", model="sonnet", route="r"),
               "oracle": RoleConfig(name="oracle", model="opus", route="r")},
        routes={"r": RouteConfig(name="r", kind="cli", command=["t"])},
    )
    assert model_family(cfg, "worker") == model_family(cfg, "oracle")
    from factory.config import independence_problems
    assert independence_problems(cfg), "different tiers were read as independence"


def test_the_oracle_is_held_to_the_rule_the_config_never_wrote_down():
    """Its green is the acceptance signal. An oracle sharing the worker's blind
    spots produces a suite that certifies the blind spot -- which is the exact
    failure the air gap exists to prevent, and the one rule `factory.yaml`'s
    comments do not state."""
    from factory.config import INDEPENDENT_OF

    assert "worker" in INDEPENDENT_OF["oracle"]


def test_a_session_in_the_container_execs_into_it_rather_than_running_here(tmp_path):
    """The whole change, in one argv: the command is an exec into this unit's
    own container, standing in the mounted worktree."""
    import asyncio

    executor, config = _executor(tmp_path)
    config.roles["worker"].model = "sonnet"
    route = _container_route()
    task_file = tmp_path / ".factory-task.md"
    task_file.write_text("do the thing", encoding="utf-8")

    session = asyncio.run(executor._open_container_session(
        "worker", route, _Prepared(), route.container_session_command,
        task_file=task_file, spend_file=tmp_path / "spend.json"))
    try:
        argv = session.argv
        assert argv[:2] == [config.docker.binary, "exec"]
        assert "--interactive" in argv, "the brief arrives on stdin"
        assert argv[argv.index("--workdir") + 1] == "/workspace"
        assert "c0ffee123456" in argv
        assert argv[-3:] == ["harness", "--model", "sonnet"]
        assert "--env" in argv and "HOME=/agent-home" in argv, (
            "the container's own HOME is the mounted worktree, which would put the "
            "harness's config and session log inside the unit's diff")
    finally:
        asyncio.run(session.close())

    # Where the services are reaches the agent in there too. `docker exec` gives
    # a process the container's environment, not the session's, so an agent told
    # to read `FACTORY_URL_API` had no such variable -- and the host's own PATH,
    # which `vars` also carries, must not follow it in.
    prepared = _Prepared()
    prepared.vars["PATH"] = "/host/shims:/usr/bin"
    prepared.session = {"FACTORY_URL_API": "http://127.0.0.1:51234",
                        "FACTORY_PORT_API": "51234"}
    session = asyncio.run(executor._open_container_session(
        "worker", route, prepared, route.container_session_command,
        task_file=task_file, spend_file=tmp_path / "spend.json"))
    try:
        assert "FACTORY_URL_API=http://127.0.0.1:51234" in session.argv
        assert not any(a.startswith("PATH=") for a in session.argv)
    finally:
        asyncio.run(session.close())


def test_the_default_route_can_author_in_a_container_too():
    """It is synthesised from `api` and `executor`, and it carries every role
    billed against a provider key -- so reading only the non-default routes
    here would have left exactly those agents on the host."""
    from factory.config import load_config
    from factory.executors import CommandExecutor

    config = load_config()
    default = config.route(config.default_route)
    assert default.authors_in_container(), \
        "factory.yaml declares the default route's sessions as running in the container"

    # A role on the default route. `session_route` returns None for it by
    # design -- its host command is read live from the `executor` block rather
    # than from a snapshot -- so the container branch cannot be driven from
    # that return value alone.
    config.role("worker").route = config.default_route
    executor = CommandExecutor(llm=None, config=config)
    assert executor.session_route("worker") is None
    resolved = executor._container_route("worker", None)
    assert resolved is not None and resolved.name == config.default_route


def test_a_fallback_session_runs_where_its_own_route_says(tmp_path, monkeypatch):
    """Where a substitute runs is its own route's business, not the exhausted
    one's. Observed live: a route ran out mid-build, its role fell back, and the
    substitute ran on the host while its route said `session_in_container`.
    Harmless for that harness, which has no permission layer to refuse it --
    and a silent return to the original failure for one that does."""
    import asyncio

    executor, config = _executor(tmp_path)
    monkeypatch.setenv("TEST_FALLBACK_TOKEN", "sk-not-a-real-token")
    substitute = _container_route(
        name="substitute",
        container_session_command=["substitute-harness", "--model", "{model}"],
        container_credentials=[{"kind": "env", "name": "TEST_FALLBACK_TOKEN"}],
    )

    # The model a fallback runs is the role's fallback model, not the model the
    # exhausted route was using -- an id that names a vendor the substitute has
    # never heard of gets refused as `unrecognized_model`.
    session = asyncio.run(executor._open_container_session(
        "worker", substitute, _Prepared(), substitute.container_session_command,
        task_file=tmp_path / "t.md", spend_file=tmp_path / "spend.json",
        model="a-substitute-model"))
    try:
        assert session.argv[:2] == [config.docker.binary, "exec"]
        assert session.argv[-3:] == ["substitute-harness", "--model", "a-substitute-model"]
    finally:
        asyncio.run(session.close())

    # And the wiring that reaches it: the fallback branch asks the substitute
    # route where it runs, rather than assuming the host.
    import inspect
    source = inspect.getsource(executor.author)
    assert "alt.authors_in_container()" in source
    assert "alt.container_session_command" in source
    assert source.index("alt_session = await self._open_container_session") < \
        source.index("alt_argv = build(list(alt.session_command)"), \
        "the container is tried first; the host command is the fallback's fallback"


def test_security_is_an_angle_on_two_agents_not_an_agent_of_its_own():
    """The hacker read for security and wrote prose. The breaker attacks and
    writes executable probes, where a passing probe counts for nothing.

    Those are not the same job, so the merge splits along the seam: the half
    that can be proved goes to the breaker, where a call from the wrong
    workspace returning 200 settles in one line what a written argument
    costs a human an afternoon to disprove. The half that cannot -- a secret
    in a log no test reaches, a design-level exposure -- goes to the reviewer,
    which was already reading everything in prose.

    What must not happen is the half that cannot be executed quietly going
    nowhere, so both halves are asserted here."""
    roles = ROOT / "factory" / "roles"
    assert not (roles / "hacker.md").exists()

    breaker = (roles / "breaker.md").read_text()
    assert "Security, as an input this code does not survive" in breaker
    assert "There is no separate security agent" in breaker
    # And it is told where the unprovable half goes, rather than inventing a
    # prose channel of its own -- `breaker_findings` computes findings from
    # probe results precisely so there is nothing there to fabricate.
    assert "The review panel reads for the same things in prose" in breaker

    reviewer = (roles / "reviewer.md").read_text()
    assert "Security is one of the angles, not a separate reading" in reviewer
    for angle in ("Authority", "Widened scope", "Exposure", "Untrusted input"):
        assert angle in reviewer, angle
    assert "Theoretical is not reachable" in reviewer

    config = load_config(Path("factory.yaml"))
    assert "hacker" not in config.roles
    from factory.config import INDEPENDENT_OF
    assert "hacker" not in INDEPENDENT_OF["arbiter"]


def test_there_is_one_reviewer_and_it_does_not_share_the_builder_s_priors():
    """There were two entries, `reviewer` and `adversary`, and the reason
    given for keeping both was that they read on two model families. They
    did not. Both sat on `codex` -- one vendor, two model ids -- so the pair
    was two calls for one reading, and the console drew two agents where the
    roster promised one.

    The independence that is real is this role against the one that wrote
    the code, and one entry has it. What the second entry actually bought
    was volume at a high temperature, which is a number and now lives on
    this role's own `samples`."""
    config = load_config(Path("factory.yaml"))
    assert [r.name for r in config.review_roles()] == ["reviewer"]
    assert "adversary" not in config.roles
    assert not (ROOT / "factory" / "roles" / "adversary.md").exists()
    assert config.roles["reviewer"].samples > 1, (
        "the volume the pair provided has to survive the merge somewhere")

    # The property the pair was said to have, held by the one that remains.
    from factory.config import INDEPENDENT_OF, model_family
    assert INDEPENDENT_OF["reviewer"] == ("worker",)
    assert model_family(config, "reviewer") != model_family(config, "worker")

    # And the one prompt carries both jobs, or the merge lost the case for
    # rejection along with the file.
    text = (ROOT / "factory" / "roles" / "reviewer.md").read_text()
    assert "strongest available case against shipping" in text
    assert "Not a balanced review" in text
    assert "A fabricated objection is worse than no objection" in text


def test_a_config_naming_the_planner_is_refused_with_the_new_name(tmp_path):
    target = tmp_path / "factory.yaml"
    target.write_text("roles:\n  planner:\n    model: m\n")
    with pytest.raises(config_module.ConfigError) as caught:
        load_config(str(target))
    assert "spec_writer" in str(caught.value), "the refusal says what to call it now"


def test_each_checker_is_on_a_different_family_from_what_it_reads():
    assert config_module.INDEPENDENT_OF["spec_checker"] == ("spec_writer",)
    assert config_module.INDEPENDENT_OF["plan_checker"] == ("architect",)
    for path in ("factory.example.yaml",):
        cfg = load_config(str(ROOT / path))
        assert {"spec_writer", "spec_checker", "plan_checker"} <= set(cfg.roles)
        assert not [w for w in config_module.independence_problems(cfg)
                    if "checker" in w], "a shipped config has a checker sharing its author's priors"



# ==========================================================================
# test hygiene -- every test leaves the world as it found it
# ==========================================================================


def test_every_test_writer_is_told_to_leave_the_world_as_it_found_it():
    """The oracle's fixture added lists to the one demo board every browser test
    opens and never took them away. Twenty lists in, later tests' drags missed
    lists off the edge of the screen and the project's own test that counts
    that board's lists failed -- all of it read as the feature being broken.
    The rule reaches every agent that writes a test, because every one of them
    works under the same harness contract."""
    from factory.config import load_config

    contract = load_config(ROOT / "factory.example.yaml").role_prompt("harness")
    assert "## Every test leaves the world as it found it" in contract
    for said in ("change only data you created", "Remove what you created",
                 "make it unreachable", "run alone, run twice in a row, and run in any order"):
        assert said in contract, f"the harness contract no longer says: {said}"


# ---------------------------------------------------------------------------
# kanban card-due-dates, 2026-09-25: two of the three calls a human was asked
# to make were the factory's own defects, and a recording was pinned on a suite
# that never opened a browser.
# ---------------------------------------------------------------------------


def test_a_companion_service_serves_the_worktree_not_the_checkout(tmp_path):
    """The gate service's source is replaced; a companion's was kept as
    declared. `./frontend:/app` resolves against the compose project directory,
    which is the person's own checkout -- so a feature's browser tests were
    served the checkout's frontend, by a container that could write there."""
    from factory.config import load_config
    from factory.containers import ComposeRunner
    from factory.schemas import EnvironmentSpec

    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  api:\n    build: ./api\n    volumes: [\"./api:/app\"]\n"
        "  frontend:\n    image: node:22\n    volumes:\n"
        "      - ./frontend:/app\n"
        "      - /app/node_modules\n"
        "      - cache:/root/.cache\n"
        "      - /etc/hosts:/etc/hosts:ro\n"
        "      - {type: bind, source: ./shared, target: /shared}\n"
        "  db:\n    image: postgres:16\n    volumes: [\"pgdata:/var/lib/postgresql/data\"]\n"
        "volumes:\n  cache: {}\n  pgdata: {}\n")
    spec = EnvironmentSpec(kind="compose", compose_file="docker-compose.yml",
                           compose_service="api", workdir="/workspace", image="img:1")
    tree = tmp_path / "tree"
    runner = ComposeRunner(tmp_path, spec, "n", load_config().docker)
    services = runner._override(tree)["services"]

    assert services["api"]["volumes"] == [f"{tree}:/workspace"]
    assert services["frontend"]["volumes"] == [
        f"{tree / 'frontend'}:/app",
        "/app/node_modules",                 # anonymous: left alone
        "cache:/root/.cache",                # named: left alone
        "/etc/hosts:/etc/hosts:ro",          # outside the repository: left alone
        {"type": "bind", "source": str(tree / "shared"), "target": "/shared"},
    ]
    assert "volumes" not in services["db"], "nothing to move, so nothing overridden"

    text = runner._override_text(tree)
    assert f'"{tree / "frontend"}:/app"' in text
    assert '{"type": "bind", "source": "' + str(tree / "shared") in text, \
        "a long-syntax volume went out as its Python repr"
    assert str(tmp_path / "frontend") not in text


def test_a_role_s_reasoning_level_reaches_a_command_line_route(tmp_path):
    """`reasoning_effort` is the role's, and generic; a command line is told it
    the way that tool hears it -- a flag, a variable -- from the route's own
    configuration. A level the route has no way to say is not faked."""
    import asyncio
    from factory.config import RouteEffort
    from factory.llm import LLM

    script = tmp_path / "cli.py"
    script.write_text(
        "import json,os,sys;sys.stdin.read();"
        "print(json.dumps({'result': json.dumps({'argv': sys.argv[1:], 'think': os.environ.get('THINK', '')}),"
        "'is_error': False, 'num_turns': 1}))", encoding="utf-8")
    cfg = _route_cfg(tmp_path, command=[sys.executable, str(script), "-m", "{model}"],
                     error_key="is_error", result_key="result",
                     effort={"none": RouteEffort(env={"THINK": "0"}), "high": RouteEffort(args=["--effort", "high"])})
    seen = {}
    for level in ("none", "high", "medium"):
        cfg.roles["scout"].reasoning_effort = level
        seen[level] = json.loads(asyncio.run(LLM(cfg).ask("scout", "x")))
    assert seen["none"]["think"] == "0" and "--effort" not in seen["none"]["argv"]
    assert seen["high"]["argv"][-2:] == ["--effort", "high"] and seen["high"]["think"] == ""
    assert seen["medium"]["argv"] == ["-m", "m1"], "a level this route cannot say is left out, not guessed"
    # And the shipped routes say it the way their tools hear it.
    routes = load_config(ROOT / "factory.example.yaml").routes
    assert routes["claude-code"].effort_for("none").env.get("MAX_THINKING_TOKENS") == "0"
    assert routes["codex"].effort_for("none").args == ["-c", "model_reasoning_effort=minimal"]


def test_every_route_that_writes_code_says_what_its_harness_reads():
    """Declared beside the route's command, where every vendor detail lives,
    so the code that bridges names no tool."""
    from factory.config import load_config

    config = load_config(ROOT / "factory.example.yaml")
    for name, route in config.routes.items():
        if route.authors() or name == config.default_route:
            assert route.reads_instructions, f"route {name} does not say what its harness reads"
    src = factory_source("guides")
    for tool in ("Codex", "Claude Code", "OpenHands", "Gemini"):
        assert tool not in src.split("def bridge(", 1)[1].split("def harness_note(", 1)[0], tool


# --------------------------------------------------------------------------
# staffing: one provider per side, three levels per route
# --------------------------------------------------------------------------


def _staff(config, *, build="claude-code", check="codex", neither="build", **roles):
    """The whole crew on its suggested levels, with `roles` laid over it."""
    from factory.config import staffing_suggestions, write_staffing

    suggested = staffing_suggestions(config)
    want = {name: {"level": suggested["roles"].get(name, "standard"), "side": "", "pin": None}
            for name in config.roles}
    for name, over in roles.items():
        want[name] = {**want[name], **over}
    return write_staffing(config, {"build": build, "check": check, "neither": neither},
                          suggested["levels"], want)


def test_a_level_becomes_the_model_its_side_runs_on(editable_config):
    """The point of a level: the worker names `standard`, not a model, and
    which model that is follows from who serves the blue team -- so moving the
    blue team to another provider moves the worker with it."""
    config, _ = editable_config
    _staff(config)

    worker, oracle = config.role("worker"), config.role("oracle")
    assert (worker.route, worker.model, worker.reasoning_effort) == ("claude-code", "sonnet", "high")
    assert (oracle.route, oracle.model) == ("codex", "gpt-5.6-sol"), \
        "a checker runs on the red team's provider, not the builder's"
    assert worker.pinned == [] and worker.level == "standard"

    _staff(config, build="default")
    assert config.role("worker").route == "", "the default route is left unnamed, as a person would"
    assert config.role("worker").model == "qwen/qwen3.8-27b"


def test_a_model_written_on_a_levelled_role_is_a_pin_and_wins(editable_config):
    """A level is the default, not a cage. An agent held to one model on
    purpose keeps it when its level's model changes -- and says that it is
    pinned, so nobody mistakes it for one that follows."""
    config, _ = editable_config
    _staff(config, breaker={"level": "standard",
                            "pin": {"route": "codex", "model": "gpt-5.6-sol",
                                    "reasoning_effort": "medium"}})

    breaker = config.role("breaker")
    assert (breaker.model, breaker.reasoning_effort) == ("gpt-5.6-sol", "medium")
    assert breaker.pinned == ["route", "model", "reasoning_effort"]
    assert config.role("reviewer").pinned == []


def test_without_staffing_a_level_changes_nothing():
    """The shipped example names a level on every role and no `staffing:`. Its
    roles must run on the models they name -- a level that silently moved
    every agent the day it appeared in the file would be a model default in
    disguise."""
    from factory.config import load_config as _load

    config = _load(ROOT / "factory.example.yaml")
    assert config.staffing is None
    assert config.role("worker").model == "qwen/qwen3-coder"
    assert config.role("worker").level == "standard"


def test_the_example_suggests_a_level_for_every_role_and_completes_every_preset():
    """The Agent configuration screen starts from these. A role with no
    suggestion lands on a guess, and a route whose preset lacks a level puts
    every agent at that level on no model at all."""
    from factory.config import LEVELS, load_config as _load, staffing_suggestions

    config = _load(ROOT / "factory.example.yaml")
    suggested = staffing_suggestions(config)
    assert set(suggested["roles"]) == set(config.roles), "a shipped role has no suggested level"
    for route in config.routes:
        assert set(suggested["levels"].get(route, {})) == set(LEVELS), \
            f"route {route!r} ships no preset for every level"


def test_writing_the_crew_keeps_every_comment(editable_config):
    """The staffing write deletes keys -- a role that follows its level no
    longer names a model -- and the comment under a model line is usually the
    reason that model was chosen. Every one of them must survive."""
    from factory.config import STAFFING_COMMENT

    config, tmp_path = editable_config
    before = (tmp_path / "factory.example.yaml").read_text().count("#")
    _staff(config)

    written = (tmp_path / "factory.yaml").read_text()
    assert written.count("#") == before + len(STAFFING_COMMENT.splitlines())
    assert "Do not economise here." in written and "HYPOTHESIS, NOT A FINDING." in written


def test_saving_a_levelled_agent_from_the_crew_sheet_does_not_pin_it(editable_config):
    """The crew sheet sends every field back, its level's model included.
    Writing that down would pin the agent -- saved for its temperature, it
    would stop moving with its level, and nothing would say why."""
    from factory.config import write_role

    config, tmp_path = editable_config
    _staff(config)
    worker = config.role("worker")
    write_role(config, "worker", {"model": worker.model, "route": worker.route,
                                  "reasoning_effort": worker.reasoning_effort,
                                  "temperature": 0.4})

    assert config.role("worker").pinned == []
    assert config.role("worker").temperature == 0.4

    write_role(config, "worker", {"model": "opus"})
    assert config.role("worker").pinned == ["model"], "a model that differs is a choice, and pins"


def test_a_builder_cannot_be_moved_to_the_checkers_side(editable_config):
    """Which side an agent is on follows from what it does. Moving the worker
    to the red team would put the author on the checkers' provider -- the
    blind spot rendered twice, chosen from a menu. An agent on neither team
    can be moved."""
    config, _ = editable_config
    with pytest.raises(config_module.ConfigError, match="cannot be moved"):
        _staff(config, worker={"side": "check"})

    _staff(config, scout={"side": "check"})
    assert config.role("scout").route == "codex"


def test_a_crew_that_cannot_run_is_refused_and_the_file_is_left_alone(editable_config):
    """A side on a switched-off route would fail every agent on it before its
    first call. Refused at the write, with the file exactly as it was."""
    config, tmp_path = editable_config
    _staff(config)
    before = (tmp_path / "factory.yaml").read_text()

    with pytest.raises(config_module.ConfigError, match="switched off"):
        _staff(config, build="gemini-cli")
    assert (tmp_path / "factory.yaml").read_text() == before
    assert config.role("worker").route == "claude-code"
