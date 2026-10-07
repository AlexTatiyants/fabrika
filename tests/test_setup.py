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
    source = (ROOT / "console" / "app" / "setup.js").read_text()
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
