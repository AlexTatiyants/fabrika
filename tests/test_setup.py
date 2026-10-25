"""Setup: the six stations a new machine passes, what they write, and what the
screen lights from what was measured.

Nothing here has Docker or a network. The machine check, the key check and the
harness build are each stood in for at the one function that would reach out.
"""

import json
import subprocess

from helpers import *  # noqa: F403


def _client(config, tmp_path):
    """Used as a context manager: setup's jobs run on the app's loop, which a
    client outside one tears down after every request."""
    from fastapi.testclient import TestClient
    from factory.server import create_app

    config.paths.evidence = str(tmp_path / "evidence")
    return TestClient(create_app(config))


@pytest.fixture
def open_client():
    """A client inside its context, closed after the test."""
    opened = []

    def make(config, tmp_path):
        client = _client(config, tmp_path)
        client.__enter__()
        opened.append(client)
        return client

    yield make
    for client in opened:
        client.__exit__(None, None, None)


def _settled(client):
    """The setup read once no job is running."""
    import time
    for _ in range(100):
        got = client.get("/api/setup").json()
        if not got["busy"]:
            return got
        time.sleep(0.05)
    raise AssertionError("a setup job never finished")


def _stub_machine(monkeypatch, *, docker=True):
    from factory import commissioning

    async def measured(cfg):
        return {"checked_at": 1.0,
                "docker": {"ok": docker, "version": "28.1" if docker else "",
                           "detail": "" if docker else "the docker daemon is not answering"},
                "disk": {"free_gb": 40.0, "total_gb": 60.0, "need_gb": 10.0, "low": False} if docker else None,
                "egress": {"ok": True, "checks": [], "detail": ""} if docker else None,
                "git": {"ok": True, "version": "2.47.1"}}

    async def absent(cfg, route):
        return False

    monkeypatch.setattr(commissioning, "check_machine", measured)
    monkeypatch.setattr(commissioning, "harness_ready", absent)


def _key_check(monkeypatch, status=200, body=None):
    """The one GET a keyed route proves its key with, answered here."""
    from factory.server import setup as setup_module
    seen = {}

    class Answer:
        status_code = status
        text = json.dumps(body or {})

        def json(self):
            return body or {}

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            seen.update(url=url, headers=headers)
            return Answer()

    monkeypatch.setattr(setup_module.httpx, "AsyncClient", Client)
    return seen


def _with_key(config):
    """The provider key a person would have pasted, so its route is connected."""
    from factory.config import sync_default_route
    config.api.api_key = "sk-or-test-not-real"
    sync_default_route(config)
    return config


def _keyed_route(config):
    """The shipped route that takes a key of its own, found by what it is
    rather than by name -- this suite names no vendor."""
    return next(name for name, r in config.routes.items() if r.kind == "cli" and r.key_env)


# ---------------------------------------------------------------- what is written

def test_the_limits_are_written_in_place_and_hold_from_the_next_feature(editable_config):
    from factory.config import load_config, write_rework_limits

    config, tmp_path = editable_config
    write_rework_limits(config, budget_usd=6.5, max_rounds=3, wall_clock_minutes=120)

    text = (tmp_path / "factory.yaml").read_text()
    assert "Everything the loop may spend" in text, "the reasoning beside each limit survives"
    fresh = load_config(tmp_path / "factory.yaml")
    assert (fresh.rework.budget_usd, fresh.rework.max_rounds, fresh.rework.wall_clock_minutes) == (6.5, 3, 120)
    assert config.rework.budget_usd == 6.5, "a running server holds the next feature to it"
    assert fresh.rework.reserve_usd == 0.75, "the rest of rework is left as it was"

    with pytest.raises(ValueError):
        write_rework_limits(config, budget_usd=0, max_rounds=1, wall_clock_minutes=10)


def test_real_harnesses_are_switched_on_without_a_restart(editable_config):
    from factory.config import load_config, write_executor_kind

    config, tmp_path = editable_config
    assert config.executor.kind == "direct", "the example ships the cheap way"
    write_executor_kind(config, "command")
    assert load_config(tmp_path / "factory.yaml").executor.kind == "command"
    assert config.executor.kind == "command"
    with pytest.raises(ValueError):
        write_executor_kind(config, "aider")


def test_a_keyed_route_is_brought_over_from_the_example_when_the_file_predates_it(editable_config):
    """A factory.yaml copied before the route existed has nothing to switch on.
    Connecting it brings the shipped definition over, on, with the stored key."""
    from factory.config import load_config, write_credential, write_route_enabled
    from ruamel.yaml import YAML

    config, tmp_path = editable_config
    name = _keyed_route(config)
    route = config.routes[name]
    target = tmp_path / "factory.yaml"
    yaml_rt = YAML()
    data = yaml_rt.load((tmp_path / "factory.example.yaml").read_text())
    del data["routes"][name]
    with target.open("w") as fh:
        yaml_rt.dump(data, fh)
    config = load_config(target)
    assert name not in config.routes

    write_credential(config, route.key_env, "a-key-for-the-route")
    write_route_enabled(config, name)

    assert config.routes[name].enabled and config.routes[name].api_key == "a-key-for-the-route"
    again = load_config(target)
    assert again.routes[name].enabled and again.routes[name].container_install
    assert again.routes[name].api_key == "a-key-for-the-route", "the stored key is read at load"


def test_a_keyed_route_s_variable_wins_over_the_stored_key(editable_config, monkeypatch):
    from factory.config import load_config, write_credential

    config, tmp_path = editable_config
    name = _keyed_route(config)
    env = config.routes[name].key_env
    write_credential(config, env, "stored")
    monkeypatch.setenv(env, "from-the-environment")
    assert load_config(tmp_path / "factory.example.yaml").routes[name].api_key == "from-the-environment"


def test_a_crew_from_two_choices_and_a_starting_point(editable_config):
    from factory import commissioning

    config, _ = editable_config
    staffing, levels, roles = commissioning.crew_plan(config, "claude-code", "codex", "lean")
    assert staffing == {"build": "claude-code", "check": "codex", "neither": "build"}
    assert set(levels) == {"claude-code", "codex"}
    for keep in commissioning.LEAN_KEEP_DEEP:
        if keep in roles:
            assert roles[keep]["level"] == "deep", f"a lean crew keeps {keep} deep"
    assert all(r["pin"] is None for r in roles.values())

    _, _, deep = commissioning.crew_plan(config, "claude-code", "codex", "deep")
    assert {r["level"] for r in deep.values()} == {"deep"}
    with pytest.raises(ValueError):
        commissioning.crew_plan(config, "claude-code", "codex", "everything")


def test_only_routes_that_can_carry_a_call_count_as_connected(editable_config):
    from factory import commissioning

    config, _ = editable_config
    config.routes[config.default_route].api_key = "sk-or-test"
    statuses = [{"name": "claude-code", "state": "ok"}, {"name": "codex", "state": "unauthenticated"}]
    got = commissioning.connected_routes(config, statuses)
    assert config.default_route in got and "claude-code" in got
    assert "codex" not in got, "installed is not signed in"
    assert _keyed_route(config) not in got, "a switched-off route carries nothing"


# ---------------------------------------------------------------- the endpoints

def test_the_setup_read_lights_each_station_from_what_was_measured(editable_config, monkeypatch, open_client):
    config, tmp_path = editable_config
    _stub_machine(monkeypatch)
    _with_key(config)
    client = open_client(config, tmp_path)

    first = client.get("/api/setup").json()
    assert first["jobs"]["machine"]["state"] in ("running", "ok"), \
        "the first read starts measuring rather than guessing"
    got = _settled(client)
    assert got["machine"]["docker"]["ok"]
    assert got["provider"]["key_present"]
    assert got["connected"] == [config.default_route]
    assert [h["route"] for h in got["harnesses"]] == [config.default_route]
    assert got["harnesses"][0]["state"] == "none" and got["harnesses"][0]["install"]
    assert not got["crew"]["confirmed"] and not got["limits"]["confirmed"]
    names = {a["name"] for a in got["accounts"]}
    assert _keyed_route(config) in names, "a switched-off key route can still be connected here"
    assert client.get("/api/setup/brief").json() == {"finished": False, "projects": 0}


def test_a_key_that_fails_its_check_is_not_stored(editable_config, monkeypatch, open_client):
    from factory.config import read_credential

    config, tmp_path = editable_config
    _stub_machine(monkeypatch)
    name = _keyed_route(config)
    _key_check(monkeypatch, status=400, body={"error": {"message": "API key not valid."}})
    client = open_client(config, tmp_path)

    said = client.put(f"/api/setup/routes/{name}/key", json={"api_key": "wrong"}).json()
    assert not said["ok"] and "not valid" in said["detail"] and "not saved" in said["detail"]
    assert read_credential(config, config.routes[name].key_env) == ""
    assert not config.routes[name].enabled


def test_a_key_that_passes_is_stored_and_switches_its_route_on(editable_config, monkeypatch, open_client):
    from factory.config import load_config, read_credential

    config, tmp_path = editable_config
    _stub_machine(monkeypatch)
    name = _keyed_route(config)
    route = config.routes[name]
    seen = _key_check(monkeypatch)
    client = open_client(config, tmp_path)

    said = client.put(f"/api/setup/routes/{name}/key", json={"api_key": "good-key"}).json()
    assert said["ok"], said
    assert seen["url"] == route.key_check_url
    assert seen["headers"] == {route.key_check_header: "good-key"}, "in a header, never the URL"
    assert read_credential(config, route.key_env) == "good-key"
    assert config.routes[name].enabled and config.routes[name].api_key == "good-key"
    assert load_config(tmp_path / "factory.yaml").routes[name].enabled
    assert name in client.get("/api/setup").json()["connected"]


def test_a_key_the_environment_would_override_is_refused(editable_config, monkeypatch, open_client):
    config, tmp_path = editable_config
    _stub_machine(monkeypatch)
    name = _keyed_route(config)
    monkeypatch.setenv(config.routes[name].key_env, "already-set")
    client = open_client(config, tmp_path)
    refused = client.put(f"/api/setup/routes/{name}/key", json={"api_key": "ignored"})
    assert refused.status_code == 409 and "silently ignored" in refused.json()["detail"]


def test_the_crew_the_limits_and_finishing_are_written_and_remembered(editable_config, monkeypatch, open_client):
    config, tmp_path = editable_config
    _stub_machine(monkeypatch)
    client = open_client(config, tmp_path)

    crew = client.put("/api/setup/crew", json={"build": "claude-code", "check": "codex",
                                               "preset": "suggested"})
    assert crew.status_code == 200, crew.text
    assert crew.json()["confirmed"] and crew.json()["staffing"]["check"] == "codex"
    refused = client.put("/api/setup/crew", json={"build": _keyed_route(config), "check": "codex"})
    assert refused.status_code == 422 and "switched off" in refused.json()["detail"]

    limits = client.put("/api/setup/limits", json={"budget_usd": 5, "max_rounds": 1,
                                                   "wall_clock_minutes": 60})
    assert limits.status_code == 200 and limits.json()["confirmed"]
    assert client.put("/api/setup/limits", json={"budget_usd": 0, "max_rounds": 1,
                                                 "wall_clock_minutes": 60}).status_code == 422

    got = client.get("/api/setup").json()
    assert got["crew"]["confirmed"] and got["limits"]["confirmed"] and got["limits"]["budget_usd"] == 5

    client.post("/api/setup/finish")
    assert client.get("/api/setup/brief").json()["finished"]


def test_building_the_harnesses_makes_them_the_way_agents_work(editable_config, monkeypatch, open_client):
    from factory import commissioning

    config, tmp_path = editable_config
    _stub_machine(monkeypatch)
    built = []

    async def build(cfg, route):
        built.append(route.name)
        return "fabrika-harness/x:1"

    monkeypatch.setattr(commissioning, "build_harness", build)
    _with_key(config)
    client = open_client(config, tmp_path)
    started = client.post("/api/setup/harnesses").json()
    assert started["started"] == [config.default_route] and started["executor"] == "command"
    _settled(client)
    assert built == [config.default_route]
    assert config.executor.kind == "command"


def test_every_button_that_starts_a_job_starts_it(editable_config, monkeypatch, open_client):
    """Each of these schedules work on the server's loop. One declared as a
    plain function runs in a worker thread with no loop, and every press of
    it failed with "no running event loop" -- so each is pressed here."""
    from factory import commissioning
    from factory.routes import RouteMonitor

    config, tmp_path = editable_config
    _stub_machine(monkeypatch)

    async def done(*a, **kw):
        return "done"

    async def signed_in(self, name):
        return {"name": name, "state": "ok"}

    monkeypatch.setattr(commissioning, "install_route", done)
    monkeypatch.setattr(commissioning, "start_docker", done)
    monkeypatch.setattr(commissioning, "can_start_docker", lambda: True)
    monkeypatch.setattr(RouteMonitor, "refresh", signed_in)
    client = open_client(config, tmp_path)

    for url in ("/api/setup/machine", "/api/setup/docker/start",
                "/api/setup/routes/codex/install", "/api/setup/routes/codex/check"):
        pressed = client.post(url)
        assert pressed.status_code == 200, f"{url}: {pressed.text}"
    jobs = _settled(client)["jobs"]
    assert {jobs[n]["state"] for n in ("machine", "docker", "install:codex", "check:codex")} == {"ok"}, jobs


def test_an_install_runs_only_the_command_the_route_names(editable_config, monkeypatch):
    """Not anything a client sends: the route's own `install_command`."""
    import asyncio
    from factory import commissioning

    config, _ = editable_config
    ran = []

    async def run(argv, timeout):
        ran.append(argv)
        return 0, "added 1 package"

    monkeypatch.setattr(commissioning, "_run", run)
    monkeypatch.setattr(commissioning.shutil, "which", lambda b: "/usr/bin/" + b)
    route = config.routes["codex"]
    asyncio.run(commissioning.install_route(route))
    assert ran == [route.install_command.split()]


#: npm's answer when its global folder is root's, as a system Node on Linux
#: gives it -- measured on Fedora with `dnf install nodejs`.
_NPM_EACCES = ("npm error code EACCES\nnpm error syscall mkdir\n"
               "npm error path /usr/local/lib/node_modules\n"
               "npm error Error: EACCES: permission denied, mkdir '/usr/local/lib/node_modules'\n"
               "npm error   path: '/usr/local/lib/node_modules'\n")


def _npm(monkeypatch, answers, *, on_path=True):
    """Stand in for npm: `answers` maps an argv's first words to (exit, output),
    and every argv run is recorded."""
    from factory import commissioning
    ran = []

    async def run(argv, timeout):
        ran.append(argv)
        for words, answer in answers.items():
            if argv[:len(words)] == list(words):
                return answer
        return 0, ""

    monkeypatch.setattr(commissioning, "_run", run)
    monkeypatch.setattr(commissioning.shutil, "which",
                        lambda b: "/usr/bin/" + b if on_path or b == "npm" else None)
    return ran


def test_a_refused_npm_install_offers_to_install_into_the_home_folder(editable_config, monkeypatch):
    """Not npm's forty lines: what was refused, and the one fix that needs no
    root, said before it is made."""
    import asyncio
    from factory import commissioning

    config, _ = editable_config
    _npm(monkeypatch, {("npm", "i"): (243, _NPM_EACCES)})
    with pytest.raises(commissioning.Remedied) as refused:
        asyncio.run(commissioning.install_route(config.routes["codex"]))
    assert "/usr/local/lib/node_modules" in str(refused.value)
    assert "npm error" not in str(refused.value)
    remedy = refused.value.remedy
    assert remedy["apply"] == "npm_user_prefix"
    assert remedy["command"] == f"npm config set prefix {commissioning.NPM_USER_PREFIX}"


def test_the_home_folder_fix_moves_the_prefix_then_runs_the_same_install(editable_config, monkeypatch):
    import asyncio
    from factory import commissioning

    config, _ = editable_config
    route = config.routes["codex"]
    ran = _npm(monkeypatch, {})
    asyncio.run(commissioning.install_route(route, npm_user_prefix=True))
    assert ran == [["npm", "config", "set", "prefix", str(commissioning.NPM_USER_PREFIX)],
                   route.install_command.split()]
    assert not any("sudo" in argv for argv in ran)


def test_an_install_fabrika_cannot_see_says_how_to_put_it_on_path(editable_config, monkeypatch, tmp_path):
    """The install succeeded and the card would still say "not on this
    machine": a systemd unit's PATH has no ~/.local/bin. The fix depends on how
    Fabrika was started, and only that one is offered."""
    import asyncio
    from factory import commissioning

    config, _ = editable_config
    route = config.routes["codex"]
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / route.command[0]).write_text("")
    _npm(monkeypatch, {("npm", "prefix", "-g"): (0, str(tmp_path))}, on_path=False)

    monkeypatch.setenv("INVOCATION_ID", "abc")
    with pytest.raises(commissioning.Remedied) as unseen:
        asyncio.run(commissioning.install_route(route))
    assert str(tmp_path / "bin") in str(unseen.value)
    assert "apply" not in unseen.value.remedy
    assert unseen.value.remedy["command"].startswith(f"Environment=PATH={tmp_path / 'bin'}:")

    monkeypatch.delenv("INVOCATION_ID")
    with pytest.raises(commissioning.Remedied) as unseen:
        asyncio.run(commissioning.install_route(route))
    assert unseen.value.remedy["command"] == f'export PATH="{tmp_path / "bin"}:$PATH"'


def test_a_refused_install_comes_back_with_its_fix_and_the_fix_is_pressed(editable_config, monkeypatch, open_client):
    """Through the server: the failed job carries the remedy, and the button's
    flag reaches the install."""
    from factory import commissioning

    config, tmp_path = editable_config
    _stub_machine(monkeypatch)
    pressed = []

    async def install(route, *, npm_user_prefix=False):
        pressed.append(npm_user_prefix)
        if not npm_user_prefix:
            raise commissioning.Remedied("refused", {"apply": "npm_user_prefix", "text": "t"})
        return "done"

    monkeypatch.setattr(commissioning, "install_route", install)
    client = open_client(config, tmp_path)
    assert client.post("/api/setup/routes/codex/install").status_code == 200
    job = _settled(client)["jobs"]["install:codex"]
    assert job["state"] == "failed" and job["remedy"]["apply"] == "npm_user_prefix"
    assert client.post("/api/setup/routes/codex/install",
                       json={"npm_user_prefix": True}).status_code == 200
    assert _settled(client)["jobs"]["install:codex"]["remedy"] is None
    assert pressed == [False, True]


def test_a_stored_token_reaches_the_container_and_the_environment_still_wins(editable_config, monkeypatch):
    """`claude setup-token` prints a token and keeps nothing, so a machine with
    no keychain had nowhere to put it but Fabrika's own environment. Stored on
    the card, it now reaches the session the way a variable would."""
    import asyncio
    from factory.config import write_credential
    from factory.sessioncreds import prepare

    config, _ = editable_config
    route = config.routes["claude-code"]
    monkeypatch.delenv(route.token_env, raising=False)
    write_credential(config, route.token_env, "stored-token")

    def handed():
        bundle = asyncio.run(prepare(route, config))
        try:
            return bundle.env_file.read_text()
        finally:
            bundle.cleanup()

    assert handed() == f"{route.token_env}=stored-token\n"
    monkeypatch.setenv(route.token_env, "from-env")
    assert handed() == f"{route.token_env}=from-env\n"


def test_the_shipped_claude_route_reads_the_sign_in_linux_keeps_in_a_file(editable_config):
    """Linux has no keychain: Claude Code keeps its sign-in in a file. Both land
    where the harness reads them, and the keychain, copied last, wins on a Mac."""
    config, _ = editable_config
    route = config.routes["claude-code"]
    kinds = [c.kind for c in route.container_credentials]
    assert kinds == ["env", "file", "keychain"], kinds
    file, keychain = route.container_credentials[1:]
    assert file.path == "~/.claude/.credentials.json" and file.optional
    assert file.target == keychain.target
    assert route.token_env == route.container_credentials[0].name


def test_a_sign_in_found_nowhere_says_where_it_looked_and_what_fixes_it(editable_config, monkeypatch, tmp_path):
    import asyncio
    from factory import sessioncreds

    config, _ = editable_config
    route = config.routes["claude-code"]
    route.container_credentials[1].path = str(tmp_path / "no-such-credentials.json")
    monkeypatch.delenv(route.token_env, raising=False)
    monkeypatch.setattr(sessioncreds.shutil, "which", lambda b: None)
    with pytest.raises(sessioncreds.CredentialError) as missed:
        asyncio.run(sessioncreds.prepare(route, config))
    said = str(missed.value)
    assert f"${route.token_env} is not set" in said
    assert "no-such-credentials.json is not on this machine" in said
    assert "this is not a Mac" in said
    assert f"Run `{route.setup_command}`" in said


def test_a_token_is_stored_from_the_card_unless_the_environment_already_gives_one(editable_config, monkeypatch, open_client):
    from factory.config import read_credential

    config, tmp_path = editable_config
    _stub_machine(monkeypatch)
    env = config.routes["claude-code"].token_env
    monkeypatch.delenv(env, raising=False)
    client = open_client(config, tmp_path)

    assert client.put("/api/setup/routes/codex/token", json={"token": "t"}).status_code == 409
    saved = client.put("/api/setup/routes/claude-code/token", json={"token": " sk-ant-oat01-minted \n"})
    assert saved.status_code == 200 and saved.json()["ok"]
    assert read_credential(config, env) == "sk-ant-oat01-minted"
    assert "minted" not in saved.text
    row = next(a for a in client.get("/api/setup").json()["accounts"] if a["name"] == "claude-code")
    assert row["token_env"] == env and row["token_present"] and not row["token_from_env"]

    monkeypatch.setenv(env, "from-env")
    assert client.put("/api/setup/routes/claude-code/token", json={"token": "t"}).status_code == 409


def test_a_paste_that_is_not_a_token_is_refused_and_says_what_it_was(editable_config, monkeypatch, open_client):
    """`claude setup-token` shows a code in the browser halfway through, and the
    page says "paste this into Claude Code". Pasted into Fabrika instead, it was
    stored as the token, the terminal never printed the real one, and every
    session then failed to sign in. And a blank paste deleted the stored token
    while the reply said it was saved."""
    from factory.config import read_credential

    config, tmp_path = editable_config
    _stub_machine(monkeypatch)
    route = config.routes["claude-code"]
    monkeypatch.delenv(route.token_env, raising=False)
    client = open_client(config, tmp_path)
    url = "/api/setup/routes/claude-code/token"
    assert client.put(url, json={"token": "sk-ant-oat01-real"}).status_code == 200

    code = client.put(url, json={"token": "n108VWjoRBLb9oxT7KR3#I7JhqEQBT2N5ePr2hx"})
    assert code.status_code == 422
    assert "authentication code" in code.json()["detail"] and "terminal" in code.json()["detail"]
    other = client.put(url, json={"token": "abc123"})
    assert other.status_code == 422 and "starts with sk-ant-oat" in other.json()["detail"]
    assert client.put(url, json={"token": "   "}).status_code == 422
    assert read_credential(config, route.token_env) == "sk-ant-oat01-real", "a refused paste replaced the token"


def _screen(monkeypatch, platform, **env):
    """This machine, as `has_display` reads it."""
    from factory import config as config_module
    monkeypatch.setattr(config_module.sys, "platform", platform)
    for name in ("DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def test_a_machine_with_no_screen_is_told_the_sign_in_that_can_finish_there(editable_config, monkeypatch, open_client):
    """`codex login` waits for the browser to come back to 127.0.0.1:1455 on
    this machine. On a server reached over SSH the browser is on another one,
    and the redirect was refused. The device flow has no way back to need."""
    from factory.routes import base_status

    config, tmp_path = editable_config
    codex = config.routes["codex"]

    _screen(monkeypatch, "linux")
    assert codex.sign_in()[0] == "codex login --device-auth"
    assert any("--device-auth" in s for s in codex.sign_in()[1])
    assert base_status(codex).setup_command == "codex login --device-auth"
    _stub_machine(monkeypatch)
    client = open_client(config, tmp_path)
    row = next(a for a in client.get("/api/setup").json()["accounts"] if a["name"] == "codex")
    assert row["setup_command"] == "codex login --device-auth", \
        "the card says what this machine needs, not what a cached status said"

    for platform, env in (("linux", {"DISPLAY": ":0"}), ("linux", {"WAYLAND_DISPLAY": "wayland-0"}),
                          ("darwin", {})):
        _screen(monkeypatch, platform, **env)
        assert codex.sign_in() == (codex.setup_command, list(codex.setup_steps)), platform

    _screen(monkeypatch, "linux")
    claude = config.routes["claude-code"]
    assert claude.sign_in()[0] == claude.setup_command, "a route with no other sign-in keeps its own"


def test_setup_harness_layers_survive_the_boot_sweep():
    """Built before any project exists, on the plain agent image -- a sweep that
    kept only project layers would throw them away at every restart."""
    import inspect
    from factory.server import lifespan

    source = inspect.getsource(lifespan)
    assert "harness_image_tag(cfg.docker.agent_image, route)" in source


# ---------------------------------------------------------------- the screen

def _setup_screen(payload, at=0):
    """Draw the setup screen in node from a payload, and return its lamps and HTML."""
    import shutil
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    source = "\n".join((ROOT / "console" / "app" / name).read_text()
                       for name in ("local-server.js", "setup.js"))
    script = (
        "const esc = (s) => String(s ?? '').replace(/[&<>\"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));\n"
        "const AC_HREF = '#/?agent-config';\n"
        f"const state = {{ view: 'setup', setup: {json.dumps(payload)}, su: {{ at: {at}, preset: 'suggested', said: {{}} }} }};\n"
        + source
        + "\nconsole.log(JSON.stringify({ lamps: SU_STATIONS.map(([id]) => suLamp(id)), html: setupScreen() }));\n")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _payload(**over):
    base = {
        "machine": {"checked_at": 1, "docker": {"ok": True, "version": "28.1", "detail": ""},
                    "disk": {"free_gb": 40, "total_gb": 60, "need_gb": 10, "low": False},
                    "egress": {"ok": True, "checks": []}, "git": {"ok": True, "version": "2.47"}},
        "can_start_docker": True,
        "provider": {"route": "default", "account_label": "openrouter.ai", "key_present": True,
                     "key_hint": "...abcd", "key_source": "stored", "connected": True},
        "accounts": [
            {"name": "plan-a", "label": "Plan A", "harness_label": "Plan A", "account_label": "Company A",
             "takes_key": False, "installed": True, "state": "ok", "connected": True, "declined": False,
             "models": ["m1"], "version": "1.0", "install_command": "npm i -g a", "job": None},
            {"name": "keyed", "label": "Keyed CLI", "harness_label": "Keyed CLI", "account_label": "Company K",
             "takes_key": True, "installed": True, "state": "unknown", "connected": False, "declined": False,
             "key_present": False, "key_checkable": True, "setup_steps": ["Get a key"], "job": None},
        ],
        "switched_off": [],
        "connected": ["default", "plan-a"],
        "harnesses": [
            {"route": "default", "label": "OpenHands", "account_label": "openrouter.ai", "default": True,
             "build_image": "uv", "install": ["uv pip install"], "state": "ready", "detail": ""},
            {"route": "plan-a", "label": "Plan A", "account_label": "Company A", "default": False,
             "build_image": "node", "install": ["npm i"], "state": "none", "detail": ""},
        ],
        "executor": "command",
        "crew": {"confirmed": False, "staffing": None, "independence": []},
        "limits": {"budget_usd": 4, "max_rounds": 2, "wall_clock_minutes": 90, "confirmed": False},
        "projects": 0, "finished": False, "jobs": {}, "busy": False,
    }
    base.update(over)
    return base


def test_the_board_lights_from_the_reading_not_from_presses():
    got = _setup_screen(_payload())
    lamps = dict(zip(["machine", "accounts", "harness", "crew", "limits"],
                     [cls for cls, _ in got["lamps"]]))
    assert len(got["lamps"]) == 5, "adding a project is the yard's job, not a station"
    assert lamps == {"machine": "green", "accounts": "green", "harness": "",
                     "crew": "", "limits": ""}

    down = _setup_screen(_payload(machine={**_payload()["machine"],
                                           "docker": {"ok": False, "version": "", "detail": "no daemon"}}))
    assert down["lamps"][0][0] == "red"
    assert "Start Docker Desktop for me" in down["html"], "a fix offered, not only a fault named"

    one = _setup_screen(_payload(connected=["default"]))
    assert one["lamps"][1][0] == "amber", "one company is a warning, not a stop"


def test_the_harness_station_explains_itself_behind_a_click():
    html = _setup_screen(_payload(), at=2)["html"]
    assert '<details class="su-why"><summary>Why these images need building' in html
    assert "Build the image" in html, "one image left to build"
    assert "Agents never run on this machine." in html


def test_the_accounts_station_offers_a_key_field_to_a_route_that_takes_one():
    html = _setup_screen(_payload(), at=1)["html"]
    assert 'id="su-key-keyed"' in html and "Test and save" in html
    assert "signed in" in html


def test_the_claude_route_asks_for_a_token_its_containers_can_carry(editable_config):
    """Sessions run Claude Code in containers on a copy of this machine's
    sign-in, and that sign-in refreshes itself: each refresh retires the copy,
    and a session failed with "OAuth session expired and could not be
    refreshed" while the card, checking this machine, said signed in. A token
    from `claude setup-token` never refreshes, so it is what the card asks for."""
    from factory.routes import base_status

    config, _ = editable_config
    route = config.routes["claude-code"]
    assert route.sign_in()[0] == route.token_command == "claude setup-token"
    assert route.token_env == "CLAUDE_CODE_OAUTH_TOKEN"
    assert "refreshes" in route.setup_note
    steps = " ".join(route.sign_in()[1])
    assert "authentication code" in steps and "back into the terminal" in steps, \
        "the steps skip the browser's code, which is the one thing people paste in the wrong place"
    assert route.token_prefix == "sk-ant-oat" and "sk-ant-oat01-" in steps
    assert base_status(route).as_dict()["token_command"] == "claude setup-token"


def test_the_routes_card_has_somewhere_to_paste_the_token_its_steps_ask_for():
    import shutil
    import subprocess

    if not shutil.which("node"):
        pytest.skip("node is not installed")
    from support import CONSOLE_DIR

    source = (CONSOLE_DIR / "app" / "routes.js").read_text(encoding="utf-8")
    block = source[source.index("function setupBlock(r)"):source.index("/* When a check was made.")]
    script = ("const esc = (s) => String(s ?? '');\n" + block + "\nconsole.log(JSON.stringify(["
              "setupBlock({name: 'claude-code', state: 'unauthenticated', setup_command: 'claude auth login', "
              "token_env: 'CLAUDE_CODE_OAUTH_TOKEN', token_command: 'claude setup-token', setup_steps: []}),"
              "setupBlock({name: 'codex', state: 'unauthenticated', setup_command: 'codex login', setup_steps: []}),"
              "setupBlock({name: 'claude-code', state: 'ok', token_env: 'CLAUDE_CODE_OAUTH_TOKEN', "
              "token_command: 'claude setup-token', token_prefix: 'sk-ant-oat', "
              "setup_steps: ['The browser shows an authentication code. Paste it into the terminal.']}),"
              "setupBlock({name: 'codex', state: 'ok'})]));")
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    claude, codex, claude_green, codex_green = json.loads(out.stdout)
    assert 'id="route-token-claude-code"' in claude and 'data-save-route-token="claude-code"' in claude
    assert "paste the token claude setup-token printed" in claude
    assert "route-token" not in codex, "a route that takes no token was offered a token field"
    assert 'id="route-token-claude-code"' in claude_green, \
        "a card green on this machine's sign-in hid the box its containers needed"
    assert "authentication code" in claude_green, "a green card hid the steps that say where each paste goes"
    assert "paste the line starting sk-ant-oat" in claude_green
    assert codex_green == "", "a green route that takes no token grew a setup section"


def test_a_working_route_with_its_token_stored_stops_asking_for_one(editable_config, monkeypatch, open_client):
    """Once the token is saved and the check comes back green, the card has
    nothing left to ask: it goes back to the size of every other working card."""
    import shutil
    import subprocess

    config, tmp_path = editable_config
    _stub_machine(monkeypatch)
    env = config.routes["claude-code"].token_env
    monkeypatch.delenv(env, raising=False)
    client = open_client(config, tmp_path)
    row = lambda: next(r for r in client.get("/api/routes").json()["routes"] if r["name"] == "claude-code")  # noqa: E731
    assert row()["token_present"] is False
    assert client.put("/api/setup/routes/claude-code/token", json={"token": "sk-ant-oat01-x"}).status_code == 200
    assert row()["token_present"] is True

    if not shutil.which("node"):
        pytest.skip("node is not installed")
    from support import CONSOLE_DIR

    source = (CONSOLE_DIR / "app" / "routes.js").read_text(encoding="utf-8")
    block = source[source.index("function setupBlock(r)"):source.index("/* When a check was made.")]
    script = ("const esc = (s) => String(s ?? '');\n" + block + "\nconsole.log(JSON.stringify("
              "setupBlock({name: 'claude-code', state: 'ok', token_env: 'CLAUDE_CODE_OAUTH_TOKEN', "
              "token_present: true, setup_steps: ['x']})));")
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == "", "a working card with its token stored still asked for one"


def test_a_plan_that_found_no_sign_in_offers_the_token_field_and_one_never_checked_does_not():
    def drawn(state, **over):
        plan = {"name": "plan", "label": "A Plan", "harness_label": "A Plan", "account_label": "Company P",
                "takes_key": False, "installed": True, "state": state, "connected": False,
                "declined": False, "detail": "none of this route's credentials could be found",
                "setup_command": "plan setup-token", "setup_steps": ["Run: plan setup-token"],
                "token_env": "PLAN_TOKEN", "token_present": False, "token_from_env": False,
                "job": None, **over}
        return _setup_screen(_payload(accounts=[plan]), at=1)["html"]

    html = drawn("error", job={"state": "failed", "detail": "none of this route's credentials could be found"})
    assert 'id="su-token-plan"' in html and 'data-su="route-token"' in html
    assert html.count("none of this route&#39;s credentials") + html.count("none of this route's credentials") == 1, \
        "a failed Check that says what the card says is said once"
    assert "Run: plan setup-token" in html
    assert 'id="su-token-plan"' in drawn("unauthenticated")
    assert 'id="su-token-plan"' not in drawn("unknown")
    assert 'id="su-token-plan"' not in drawn("error", token_env="")
    from_env = drawn("error", token_from_env=True, token_present=True)
    assert 'id="su-token-plan"' not in from_env and "PLAN_TOKEN" in from_env


def test_a_failed_install_draws_its_fix_and_a_button_only_for_one_fabrika_can_make():
    def drawn(remedy):
        missing = {"name": "cli", "label": "A CLI", "harness_label": "A CLI", "account_label": "Company C",
                   "takes_key": False, "installed": False, "state": "missing", "connected": False,
                   "declined": False, "install_command": "npm i -g c",
                   "job": {"state": "failed", "detail": "refused", "remedy": remedy}}
        return _setup_screen(_payload(accounts=[missing]), at=1)["html"]

    html = drawn({"title": "Install it into my home folder", "text": "Moves the prefix.",
                  "command": "npm config set prefix /home/me/.local",
                  "apply": "npm_user_prefix", "button": "Install there"})
    assert 'data-su="install-fix"' in html and 'data-fix="npm_user_prefix"' in html
    assert "npm config set prefix /home/me/.local" in html

    html = drawn({"text": "Add this line to your shell profile.",
                  "command": 'export PATH="/home/me/.local/bin:$PATH"'})
    assert 'data-su="install-fix"' not in html
    assert "export PATH=&quot;/home/me/.local/bin:$PATH&quot;" in html




# ---------------------------------------------------------------- a local server

#: A chat template that reads both thinking variables, and the effort values
#: it accepts -- any other raises, as the measured one did.
_THINKING_TEMPLATE = ("{%- if enable_thinking is defined and enable_thinking is false %}"
                      "{%- set e = reasoning_effort|default('xhigh') %}")


def _model_server(*, tools=True, n_ctx=131072, template=_THINKING_TEMPLATE,
                  accepts=("low", "medium", "high")):
    """A stand-in for a model server: its model list, its context and chat
    template, a strict schema answered, and a tool call made -- or refused, as
    a server started without native tool calling refuses one. An effort its
    template does not accept fails the call with a 500."""
    def serve(req):
        ok = {"content-type": "application/json"}
        if req["path"] == "/props":
            return 200, ok, [json.dumps({"default_generation_settings": {"n_ctx": n_ctx},
                                         "chat_template": template}).encode()]
        if req["path"].endswith("/models"):
            return 200, ok, [json.dumps({"data": [{"id": "local-model"}]}).encode()]
        body = json.loads(req["body"] or b"{}")
        effort = (body.get("chat_template_kwargs") or {}).get("reasoning_effort")
        if effort is not None and effort not in accepts:
            return 500, ok, [b'{"error": {"message": "Unexpected reasoning effort"}}']
        if body.get("tools"):
            if not tools:
                return 500, ok, [b'{"error": {"message": "tools param requires a flag"}}']
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "current_time", "arguments": "{}"}}]}
        else:
            message = {"role": "assistant", "content": '{"answer": "ok"}'}
        return 200, ok, [json.dumps({"choices": [{"message": message}]}).encode()]
    return serve


def test_connect_checks_a_server_from_both_sides_and_says_what_to_do(editable_config, monkeypatch):
    """Six rows, in the order a failure explains the next. The sandbox row is
    the path an agent's calls take -- a sealed container, through a lease on
    the gateway -- and is stood in for here at the one function that reaches
    Docker. A server without tool calls, or with too little context for some
    agents, is a warning with the command that fixes it, from the
    `local_server` block; nothing is written."""
    from factory import commissioning, egress
    from factory.config import local_server_template

    config, tmp_path = editable_config
    config.docker.gateway_port = 0
    through = []

    async def sealed(docker, token, url, body=None):
        through.append((token, url))
        return {"status": 200, "body": '{"data": []}'}

    monkeypatch.setattr(egress, "gateway_check", sealed)

    async def scenario(**server):
        from factory import gateway
        async with _upstream(_model_server(**server)) as (base, seen):
            try:
                got = await commissioning.connect_local_server(config, base, name="Studio")
            finally:
                await gateway.stop()
            return got, list(seen)

    good, seen = asyncio.run(scenario())
    assert [c["id"] for c in good["checks"]] == ["host", "sandbox", "models", "schema", "tools",
                                                 "context", "thinking"]
    assert all(c["ok"] for c in good["checks"]), good["checks"]
    assert good["ok"] and good["can_add"] and good["models"] == ["local-model"]
    assert good["context_window"] == 131072
    assert through and through[0][1] == "http://models.fabrika.internal/models"
    assert through[0][0].startswith("fabrika-session-"), "the sandbox call carries a lease"
    assert all("authorization" not in r["headers"] for r in seen), "no key, none sent"
    assert not (tmp_path / "factory.yaml").exists(), "Connect writes nothing"

    bad, _ = asyncio.run(scenario(tools=False, n_ctx=8192))
    rows = {c["id"]: c for c in bad["checks"]}
    command = local_server_template(config)["setup_command"]
    assert rows["tools"]["ok"] is False and "OpenHands" in rows["tools"]["remedy"]["text"]
    assert rows["tools"]["remedy"]["command"] == command, "an appliable remedy, from config"
    assert rows["context"]["ok"] is False and "worker" in rows["context"]["detail"]
    assert "refused before they are sent" in rows["context"]["detail"]
    assert rows["context"]["remedy"]["command"] == command
    assert not bad["ok"] and bad["can_add"], "a warning is the person's to weigh"
    assert good["effort"] == {
        "none": {"chat_template_kwargs": {"enable_thinking": False}},
        "low": {"chat_template_kwargs": {"reasoning_effort": "low"}},
        "medium": {"chat_template_kwargs": {"reasoning_effort": "medium"}},
        "high": {"chat_template_kwargs": {"reasoning_effort": "high"}}}
    assert "off, low, medium, high" in {c["id"]: c for c in good["checks"]}["thinking"]["detail"]


def test_a_server_that_does_not_answer_stops_the_checks_that_need_it(editable_config):
    from factory import commissioning

    config, _ = editable_config
    got = asyncio.run(commissioning.connect_local_server(config, "http://127.0.0.1:9/v1",
                                                         name="Gone"))
    rows = {c["id"]: c for c in got["checks"]}
    assert rows["host"]["ok"] is False and rows["host"]["remedy"]["command"]
    assert rows["sandbox"]["ok"] is None and rows["context"]["ok"] is None
    assert not got["can_add"]


def test_adding_a_server_writes_its_route_and_its_key_apart(editable_config, monkeypatch,
                                                           open_client):
    """Add server asks the server again rather than trusting the client, writes
    the route, keeps a key in the credentials file, and checks it so it shows as
    answering. A bad address is refused before anything is sent."""
    from factory import commissioning, gateway, routes
    from factory.config import load_config, read_credential

    config, tmp_path = editable_config
    _stub_machine(monkeypatch)
    _with_key(config)
    asked = []

    async def listing(base_url, key="", timeout_s=10.0):
        asked.append((base_url, key))
        return ["local-model"], ""

    async def window(route, base_url="", **_):
        route.context_window = route.context_window or 65536
        return route.context_window

    async def thinking(cfg, base, key, model):
        return {"none": {"chat_template_kwargs": {"enable_thinking": False}}}, "off"

    monkeypatch.setattr(routes, "list_models", listing)
    monkeypatch.setattr(gateway, "ensure_context_window", window)
    monkeypatch.setattr(commissioning, "probe_thinking", thinking)
    client = open_client(config, tmp_path)

    refused = client.post("/api/local-servers", json={"base_url": "localhost:8080", "name": "x"})
    assert refused.status_code == 422 and not asked

    added = client.post("/api/local-servers", json={
        "base_url": "http://192.168.1.50:8080/v1/", "name": "Studio Mac",
        "api_key": "server-secret"}).json()
    assert added["name"] == "studio-mac" and added["state"] == "ok", added
    assert added["account_kind"] == "local" and added["base_url"] == "http://192.168.1.50:8080/v1"
    assert ("http://192.168.1.50:8080/v1", "server-secret") in asked
    route = load_config(tmp_path / "factory.yaml").routes["studio-mac"]
    assert route.context_window == 65536 and route.models == ["local-model"]
    assert route.effort_for("none").body == {"chat_template_kwargs": {"enable_thinking": False}}
    assert route.effort_for("low") is None, "only what the server was found to take"
    import yaml as _yaml
    block = _yaml.safe_load((tmp_path / "factory.yaml").read_text())["routes"]["studio-mac"]
    assert "thinking_probe" not in block, "how to ask is the template's, not the route's"
    assert "server-secret" not in (tmp_path / "factory.yaml").read_text()
    assert read_credential(config, route.key_env) == "server-secret"
    setup = _settled(client)
    assert "studio-mac" in setup["connected"]
    assert [s["route"] for s in setup["local_servers"]] == ["studio-mac"]


def test_setup_offers_a_local_server_with_the_crew_page_s_form():
    html = _setup_screen(_payload(local_offered=True), at=1)["html"]
    assert "Use a local server" in html and 'data-ls="open"' in html
    assert 'id="ls-url"' not in html, "the form opens on request"

    on = [{"route": "studio-mac", "label": "Studio Mac", "account_label": "Studio Mac",
           "base_url": "http://192.168.1.50:8080/v1", "models": ["local-model"], "connected": True}]
    accounts = _setup_screen(_payload(local_offered=True, local_servers=on), at=1)["html"]
    assert "Studio Mac" in accounts and "192.168.1.50" in accounts
    crew = _setup_screen(_payload(local_servers=on, connected=["default", "studio-mac"]), at=3)
    assert "Studio Mac" in crew["html"], "a connected server can staff a side"


def test_a_model_file_is_named_by_its_file_not_its_path():
    """A local server names its model by the file it loaded. Every screen shows
    the file's own name -- no directories, no extension, no shard suffix --
    and keeps the whole id for what is written and for the hover text."""
    import shutil
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    ids = ["/models/x/UD-IQ4_XS/Big-Model-UD-IQ4_XS-00001-of-00003.gguf",
           "vendor/model-name", "sonnet", "small.gguf"]
    out = subprocess.run(["node", "-e", _model_name_js()
                          + f"console.log(JSON.stringify({json.dumps(ids)}.map(modelName)));"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == ["Big-Model-UD-IQ4_XS", "vendor/model-name", "sonnet", "small"]
    app = app_js()
    for where in ("${esc(modelName(m))}", "esc(modelName(r.model))", "const model = modelName(id);",
                  "modelName(role.model)", "esc(modelName(r.id) || 'its own default model')"):
        assert where in app, f"a screen still shows the raw id: {where}"


def test_an_agent_can_be_pinned_to_a_model_on_another_route():
    """The pin bar opens the model picker, on the selection's own side, with
    every route on offer; choosing a model on another route pins it there, and
    the chip says where it went. The effort carried is the level's on the route
    pinned to, and only one that route can say."""
    import shutil
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    data = {"roles": [{"name": "worker", "team": "build", "level": "standard"},
                      {"name": "oracle", "team": "check", "level": "deep"}]}
    routes = [{"name": "cloud", "account_label": "Cloud plan", "models": ["big", "small"],
               "efforts": ["high"]},
              {"name": "studio", "account_label": "Studio Mac", "kind": "api", "models": ["coder"],
               "efforts": []},
              {"name": "off", "enabled": False, "models": ["never"]}]
    draft = {"staffing": {"build": "cloud", "check": "cloud", "neither": "build"},
             "levels": {"cloud": {"standard": {"model": "big", "reasoning_effort": "high"}},
                        "studio": {"standard": {"model": "coder", "reasoning_effort": "high"}}},
             "roles": {"worker": {"level": "standard", "side": "", "pin": None},
                       "oracle": {"level": "deep", "side": "", "pin": None}}}
    script = (
        "const esc = (s) => String(s ?? '').replace(/[&<>\"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));\n"
        "let rendered = 0; const render = () => { rendered += 1; };\n"
        "let opened = null; const openModelPicker = (anchor, opts) => { opened = opts; };\n"
        f"const state = {{ ac: {{ data: {json.dumps(data)}, routes: {json.dumps(routes)}, "
        f"draft: {json.dumps(draft)}, sel: ['worker'], preset: null }} }};\n"
        + _model_name_js() + (ROOT / "console" / "app" / "agent-config.js").read_text()
        + "\nconst bar = acSelbar();\n"
        "agentConfigClick({ dataset: { ac: 'pin-pick' } }, {});\n"
        "const offered = { route: opened.route, routes: opened.routes.map((r) => r.name), roles: opened.roles,"
        " used: opened.used };\n"
        "opened.onPick('studio', 'coder');\n"
        "console.log(JSON.stringify({ bar, offered, pin: state.ac.draft.roles.worker.pin,"
        " resolved: acResolve('worker'), chip: acChip('worker'), oracle: acResolve('oracle') }));\n")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)

    assert 'data-ac="pin-pick"' in got["bar"] and "<select id=\"ac-pin\"" not in got["bar"]
    assert got["offered"]["route"] == "cloud", "it opens on the agent's own side"
    assert got["offered"]["routes"] == ["cloud", "studio"], "a switched-off route is not offered"
    assert got["offered"]["roles"] == ["worker"], "what the agent needs decides the filters"
    assert {"route": "studio", "model": "coder", "why": "standard level on Studio Mac"} in got["offered"]["used"]
    assert got["pin"] == {"route": "studio", "model": "coder", "reasoning_effort": ""}, \
        "the level's effort there is one the route cannot say, so none is carried"
    assert got["resolved"]["route"] == "studio" and got["resolved"]["pinned"]
    assert "pinned · Studio Mac · coder" in got["chip"]
    assert got["oracle"]["route"] == "cloud", "the rest of the crew stays where its side is"


def test_the_picker_filters_by_what_the_agents_need_and_never_by_what_it_does_not_know():
    """Tool calls for a harness agent, a schema for all, room for the largest
    call -- from the catalogue's `needs`. A fact a route does not report is
    unknown, and an unknown never drops a model."""
    import shutil
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    rows = [{"id": "v/tooled", "name": "Tooled", "vendor": "v", "context": 200000, "tools": True,
             "structured": True, "thinking": True},
            {"id": "v/no-tools", "name": "Plain", "vendor": "v", "context": 200000, "tools": False,
             "structured": True, "thinking": False},
            {"id": "v/small", "name": "Small", "vendor": "v", "context": 8000, "tools": True,
             "structured": True, "thinking": False},
            {"id": "listed", "name": "", "vendor": "", "context": None, "tools": None,
             "structured": None, "thinking": None}]
    needs = {"worker": {"tools": True, "structured": True, "context": 90000},
             "reader": {"tools": False, "structured": True, "context": 30000}}
    script = (
        "const esc = (s) => String(s ?? '');\n" + _model_name_js()
        + (ROOT / "console" / "app" / "model-picker.js").read_text()
        + f"\nmpCatalogs.r = {{ models: {json.dumps(rows)}, needs: {json.dumps(needs)} }};\n"
        "const ids = (f, q = '') => { mp = { route: 'r', q, filters: f, needs: mpNeeds(mpCatalogs.r, ['worker']) };"
        " return mpVisible().map((r) => r.id); };\n"
        "console.log(JSON.stringify({\n"
        "  worker: mpNeeds(mpCatalogs.r, ['worker']), reader: mpNeeds(mpCatalogs.r, ['reader']),\n"
        "  tools: ids({ tools: true, structured: true }), fits: ids({ fits: true }),\n"
        "  thinking: ids({ thinking: true }), search: ids({}, 'plain'), words: ids({}, 'v tooled'),"
        " all: ids({}) }));\n")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["worker"] == {"tools": True, "structured": True, "context": 90000}
    assert got["reader"]["tools"] is False
    assert got["tools"] == ["v/tooled", "v/small", "listed"], "no tools is out; unknown stays"
    assert got["fits"] == ["v/tooled", "v/no-tools", "listed"], "too small is out; unknown stays"
    assert got["thinking"] == ["v/tooled", "listed"]
    assert got["search"] == ["v/no-tools"], "searched by name as well as id"
    assert got["words"] == ["v/tooled"], "each word is matched on its own, across the id's parts"
    assert len(got["all"]) == 4

def test_a_local_server_is_not_offered_as_a_subscription():
    """A model server of one's own bills nothing and has no plan, so the side
    picker says so, rather than filing it under whichever of the other two it
    is not."""
    import shutil
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    draft = {"staffing": {"build": "cloud", "check": "cloud", "neither": "build"},
             "levels": {}, "roles": {}}
    script = (
        "const esc = (s) => String(s ?? '');\n"
        f"const state = {{ ac: {{ draft: {json.dumps(draft)}, routes: [] }} }};\n"
        + _model_name_js() + (ROOT / "console" / "app" / "agent-config.js").read_text()
        + "\nconsole.log(JSON.stringify(['local', 'key', 'subscription'].map((k) => acProvButton("
        "'build', { name: k, account_kind: k, harness_label: 'OpenHands' }))));\n")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    local, key, plan = json.loads(out.stdout)
    assert "local · no billing" in local and "subscription" not in local
    assert "billed per token" in key and "subscription" in plan


def test_a_pinned_agent_s_thinking_is_set_beside_its_pin():
    """The thinking menu offers what every route in the selection can take,
    says so when a route takes none, and choosing one holds each agent to the
    model it runs now at that effort."""
    import shutil
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    data = {"roles": [{"name": "worker", "team": "build", "level": "standard"},
                      {"name": "integrator", "team": "build", "level": "standard"}]}
    routes = [{"name": "cloud", "account_label": "Cloud plan", "models": ["big"],
               "efforts": ["low", "medium", "high"]},
              {"name": "studio", "account_label": "Studio Mac", "models": ["coder"], "efforts": []}]
    draft = {"staffing": {"build": "cloud", "check": "cloud", "neither": "build"},
             "levels": {"cloud": {"standard": {"model": "big", "reasoning_effort": "high"}}},
             "roles": {"worker": {"level": "standard", "side": "", "pin": None},
                       "integrator": {"level": "standard", "side": "",
                                      "pin": {"route": "studio", "model": "coder",
                                              "reasoning_effort": ""}}}}
    script = (
        "const esc = (s) => String(s ?? '').replace(/[&<>\"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));\n"
        "const render = () => {};\n"
        f"const state = {{ ac: {{ data: {json.dumps(data)}, routes: {json.dumps(routes)}, "
        f"draft: {json.dumps(draft)}, sel: ['worker'], preset: null }} }};\n"
        + _model_name_js() + (ROOT / "console" / "app" / "agent-config.js").read_text()
        + "\nconst one = acEffortChoice(['worker']);\n"
        "const local = acEffortChoice(['integrator']);\n"
        "agentConfigChange({ id: 'ac-effort', value: 'mixed', dataset: {} });\n"
        "const untouched = state.ac.draft.roles.worker.pin;\n"
        "agentConfigChange({ id: 'ac-effort', value: 'low', dataset: {} });\n"
        "console.log(JSON.stringify({ one, local, untouched, pin: state.ac.draft.roles.worker.pin }));\n")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)

    assert '<option value="high" selected>high</option>' in got["one"], "shows what it runs now"
    assert 'value="none"' not in got["one"], "only the efforts the route can take"
    assert "Studio Mac takes no thinking setting" in got["local"] and "<select" not in got["local"]
    assert got["untouched"] is None, "choosing the mixed marker changes nothing"
    assert got["pin"] == {"route": "cloud", "model": "big", "reasoning_effort": "low"}


def test_connect_keeps_only_the_thinking_levels_a_server_takes(editable_config):
    """A level is kept when the template reads its variable and a request
    carrying it is answered. One the template ignores would succeed and change
    nothing; one it does not accept would fail every call made with it."""
    from factory import commissioning

    config, _ = editable_config

    async def found(**server):
        async with _upstream(_model_server(**server)) as (base, seen):
            got = await commissioning.probe_thinking(config, base, "", "local-model")
            return got, [r for r in seen if r["method"] == "POST"]

    (picky, _), _ = asyncio.run(found(accepts=("low", "high")))
    assert sorted(picky) == ["high", "low", "none"], "medium failed the call, so it is not offered"
    (only_off, _), asked = asyncio.run(found(template="{% if enable_thinking %}{% endif %}"))
    assert list(only_off) == ["none"] and len(asked) == 1, \
        "a variable the template never reads is not even tried"
    (nothing, said), asked = asyncio.run(found(template="{{ messages }}"))
    assert nothing == {} and not asked and "model's own default" in said


def test_a_route_s_models_are_read_from_its_server_kept_for_an_hour_and_said_as_rows(
        editable_config, monkeypatch, open_client):
    """An API route with no list of its own is asked at `/models` -- once, then
    kept -- and each entry becomes a row with what the server says of it. A
    fact it does not say is None, not False. A route with a list in its config
    is that list. The endpoint adds what each agent needs of a model."""
    from factory import catalog
    from factory.config import RouteConfig

    listing = {"data": [
        {"id": "vendor/big", "name": "Vendor: Big", "context_length": 200000,
         "pricing": {"prompt": "0.000003", "completion": "0.000015"},
         "supported_parameters": ["tools", "response_format", "reasoning"]},
        {"id": "vendor/plain", "name": "Vendor: Plain", "context_length": 32000,
         "pricing": {"prompt": "0", "completion": "0"}, "supported_parameters": ["temperature"]},
        {"id": "bare"}]}

    async def scenario():
        async with _upstream(lambda req: (200, {"content-type": "application/json"},
                                          [json.dumps(listing).encode()])) as (base, seen):
            route = RouteConfig(name="agg", kind="api", base_url=base, auth="none",
                                dialect="openrouter")
            catalog.forget()
            first = await catalog.catalog(config, route)
            again = await catalog.catalog(config, route)
            fresh = await catalog.catalog(config, route, refresh=True)
            return first, again, fresh, len(seen)

    config, tmp_path = editable_config
    first, again, fresh, asked = asyncio.run(scenario())
    assert asked == 2, "asked once, kept, and asked again only on refresh"
    assert first == again and first["source"] == "server" and not first["problem"]
    bare, big, plain = first["models"]
    assert big == {"id": "vendor/big", "name": "Vendor: Big", "vendor": "vendor", "context": 200000,
                   "price_in": 3.0, "price_out": 15.0, "tools": True, "structured": True,
                   "thinking": True}
    assert (plain["tools"], plain["structured"], plain["thinking"]) == (False, False, False)
    assert (bare["tools"], bare["context"], bare["price_in"]) == (None, None, None), \
        "what the server does not say is unknown, not absent"

    listed = asyncio.run(catalog.catalog(config, config.route("claude-code")))
    assert listed["source"] == "config"
    assert [r["id"] for r in listed["models"]] == list(config.route("claude-code").models)

    _stub_machine(monkeypatch)
    client = open_client(config, tmp_path)
    body = client.get("/api/routes/claude-code/catalog").json()
    assert body["needs"]["worker"]["tools"] and not body["needs"]["reader"]["tools"]
    assert body["needs"]["reader"]["structured"] and body["needs"]["worker"]["context"] > 0
    assert client.get("/api/routes/no-such/catalog").status_code == 404
