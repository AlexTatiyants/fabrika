"""The server: its routes, and what each one answers and refuses.

Split out of test_invariants.py, which keeps one test per invariant.
"""

from helpers import *  # noqa: F403


def test_re_adding_an_agent_does_not_destroy_its_prompt(tmp_path):
    """Removing a panel agent leaves its prompt file on purpose. Adding the same
    name back must not then overwrite what you wrote."""
    import shutil
    from fastapi.testclient import TestClient
    from factory.config import load_config as _load
    from factory.server import create_app

    shutil.copyfile(ROOT / "factory.example.yaml", tmp_path / "factory.example.yaml")
    roles_dir = tmp_path / "roles"
    shutil.copytree(PACKAGE / "roles", roles_dir)

    config = _load(tmp_path / "factory.example.yaml")
    config.paths.evidence = str(tmp_path / "evidence")
    config.paths.roles = str(roles_dir)
    client = TestClient(create_app(config))

    assert client.post("/api/roles", json={"name": "security", "model": "x/y"}).status_code == 200
    starter = (roles_dir / "security.md").read_text()
    assert "TODO" in starter

    mine = "# security\n\nEverything I actually want it to look for.\n"
    assert client.put("/api/roles/security/prompt", json={"prompt": mine}).status_code == 200

    assert client.delete("/api/roles/security").status_code == 200
    assert (roles_dir / "security.md").read_text() == mine, "removal keeps what you wrote"

    assert client.post("/api/roles", json={"name": "security", "model": "x/y"}).status_code == 200
    assert (roles_dir / "security.md").read_text() == mine, \
        "re-adding the same name must not overwrite the prompt with the starter"


def test_an_environment_variable_wins_and_locks_the_field(monkeypatch, tmp_path):
    import shutil
    from factory.config import api_key_source, load_config as _load

    shutil.copyfile(ROOT / "factory.example.yaml", tmp_path / "factory.example.yaml")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-from-the-environment")
    config = _load(tmp_path / "factory.example.yaml")

    assert config.api.api_key == "sk-from-the-environment"
    assert api_key_source(config) == "environment"

    from fastapi.testclient import TestClient
    from factory.server import create_app
    config.paths.evidence = str(tmp_path / "evidence")
    client = TestClient(create_app(config))

    refused = client.put("/api/provider", json={"api_key": "sk-would-be-ignored"})
    assert refused.status_code == 409, "storing a key an env var would override is a silent no-op"
    assert "silently ignored" in refused.json()["detail"]


def test_a_screen_is_served_only_from_its_own_feature():
    """The name arrives from a URL. A path that climbs out of the artifact
    directory is refused rather than served, whatever it names."""
    from fastapi.testclient import TestClient
    cfg = load_config(str(Path("factory.yaml")))
    client = TestClient(server.create_app(cfg))
    base = "/api/projects/clinic/features/site-screening-61020a/screens"
    assert client.get(base + "/..%2F..%2Fevidence.jsonl").status_code == 404
    assert client.get(base + "/nothing-here.png").status_code == 404
    # A file that is not a picture is not served either, whatever it is.
    assert client.get(base + "/notes.zip").status_code == 404


def test_a_survey_retires_a_proposal_nobody_ruled_on(tmp_path):
    """A diff outlives the thing it was a diff against, and the screen believes it.

    `latest_proposal` walked the store for the last `resurvey` and no other kind,
    so a proposal stood until somebody ruled on it -- forever, whatever else
    happened. A day-old diff sat on the checks screen through three surveys,
    proposing a testing surface that contradicted the one the project had by
    then, with a live checkbox next to it. Accepting it would have written a
    two-survey-old reading over the current one.

    A survey replaces the gate list, the testing surface, the placements and the
    environment. Nothing a diff against the old list says can still be acted on.
    """
    from fastapi.testclient import TestClient
    from factory.config import Config
    from factory.projects import ProjectRegistry
    from factory.server import create_app

    repo = tmp_path / "repo"; repo.mkdir()
    config = Config(); config.paths.evidence = str(tmp_path / "evidence")
    registry = ProjectRegistry(config.evidence_path)
    project = registry.create(repo, "demo")

    project.store.append("resurvey", {"summary": "a diff", "gate_changes": []},
                         role="resurvey")
    client = TestClient(create_app(config))
    live = client.get(f"/api/projects/{project.id}/proposal").json()
    assert live["diff"] is not None, "this test proves nothing if the proposal never showed"

    project.store.append("survey", {"name": "demo", "gates": []}, role="surveyor")
    after = client.get(f"/api/projects/{project.id}/proposal").json()
    assert after["diff"] is None, (
        "a proposal survived the survey that replaced what it was a diff against, so the "
        "screen still offers a change to a gate list that no longer exists")
    assert after["ruling"] is None


def test_the_testability_check_runs_where_a_human_can_still_act_on_it():
    """At gate 1, against the spec being frozen -- not in the packet."""
    # In `_plan`, which is what `finalize_spec` runs -- beside the record of the
    # spec itself, before the stage moves to awaiting approval.
    src = inspect.getsource(pipeline.Factory._write_spec)
    assert "check_spec_testability(spec, self.project.state.testing," in src
    assert src.index("check_spec_testability") < src.index('state.stage = "awaiting_spec_approval"'), \
        "the check runs after the spec is already waiting for approval"
    assert "spec_testability" in factory_source("server"), \
        "the console never shows it, so the gate-1 reader does not see it"


def test_the_console_and_the_baseline_check_the_same_things():
    """`environment_problems` takes an optional repo, and half its checks are
    no-ops without it. The API omitted it, so the page showed a clean project
    while `_baseline` refused to build it."""
    src = factory_source("server")
    call = src[src.index('"environment_problems": registry.environment_problems'):]
    call = call[:call.index("),") + 2]
    assert "repo_path" in call, \
        "the API must pass the repository, or the on-disk checks silently pass"


def test_the_revalidate_route_is_reachable_and_guarded(bare_app):
    """The button a human presses when the reading, not the work, is in doubt.

    Only the refusals are exercised here: the accepting path starts a real build
    in a background task, and a test that fires one is a test that spends money.
    What it does when it is allowed to run is asserted against a stubbed factory
    in the end-to-end suite.
    """
    base = "/api/projects/demo/features"
    assert bare_app.post(f"{base}/no-such-feature/revalidate").status_code == 404
    assert bare_app.post("/api/projects/no-such-project/features/x/revalidate").status_code == 404

    # And it is a POST at a path of its own, not a parameter on `dispatch`:
    # dispatch acts on what a human flagged in the work, and this says nothing
    # about the work at all.
    assert bare_app.get(f"{base}/no-such-feature/revalidate").status_code == 405


def test_a_second_press_does_not_start_a_second_run_in_the_same_worktree(tmp_path, monkeypatch):
    """`retry-build` read the stage and nothing else, and the stage is written
    by the run -- after the request that started it has returned. Two presses
    both read `failed`, and two builds went into one worktree. And `building`
    was accepted with nobody asking whether that build was still alive."""
    import threading as _threading

    from fastapi.testclient import TestClient

    from factory.llm import LLM
    from factory.onboarding import ProjectOnboarding
    from factory.pipeline import Factory, this_process
    from factory.server import create_app

    config, project = _demo_project(tmp_path)
    factory = Factory(config, project, llm=LLM(config))
    state = factory.create_feature("a small change", "Small change")
    url = f"/api/projects/{project.id}/features/{state.feature_id}/retry-build"

    def write_stage(stage, owner=""):
        state.stage, state.owner = stage, owner
        factory.store_for(state.feature_id).append(
            "state", state.model_dump(mode="json"), role="orchestrator")

    client = TestClient(create_app(config))
    pressed_again: list[int] = []
    builds: list[str] = []

    async def build(self, feature_id, *, resume=False):
        builds.append(feature_id)
        if len(builds) == 1:
            # The second press, while this run is still going. A thread, so it
            # arrives the way a second request does, on its own.
            t = _threading.Thread(target=lambda: pressed_again.append(client.post(url).status_code))
            t.start()
            await anyio.to_thread.run_sync(t.join)

    async def nothing_moved(self, project):
        return None

    import anyio
    monkeypatch.setattr(Factory, "run_build", build)
    monkeypatch.setattr(ProjectOnboarding, "run_baseline", nothing_moved)

    write_stage("failed")
    assert client.post(url).status_code == 200
    assert pressed_again == [409], "the second press started a second build"
    assert builds == [state.feature_id]

    # Once the run has ended, the feature can be picked up again.
    assert client.post(url).status_code == 200
    assert len(builds) == 2

    # And a feature that says `building` is resumed only if nobody is building it.
    write_stage("building", owner=this_process())
    assert client.post(url).status_code == 409
    assert len(builds) == 2


def test_an_event_published_from_a_worker_thread_reaches_the_console_at_once():
    """A plain `def` route runs on a worker thread, and saving an answer
    publishes from there. Put straight into an asyncio queue from another
    thread, the event did not wake the stream waiting on it, and the console
    heard about it whenever the 15-second keep-alive next woke the loop."""
    import threading as _threading

    from factory.server import ProgressHub

    hub = ProgressHub()

    async def listen():
        stream = hub.stream()
        assert await stream.__anext__() == ": connected\n\n"
        waiting = asyncio.ensure_future(stream.__anext__())

        def save_an_answer():
            time.sleep(0.2)             # by now the loop is idle, waiting on the stream
            hub.publish({"stage": "writing_spec"})

        _threading.Thread(target=save_an_answer).start()
        return await asyncio.wait_for(waiting, timeout=3.0)

    assert json.loads(asyncio.run(listen())[len("data: "):]) == {"stage": "writing_spec"}


def test_every_request_body_the_server_declares_can_actually_be_received(bare_app):
    """`from __future__ import annotations` makes every annotation a string, and
    FastAPI resolves those against the *module's* namespace. A body model
    defined inside `create_app` cannot be found there, so the parameter is
    quietly demoted to a query string and every POST to it fails with
    "field required: body" before a line of the handler runs.

    That is how `proposal/apply` shipped: the one route whose model was nested,
    and the one route that had never once worked.
    """
    app = bare_app.app
    for route in app.routes:
        body_models = [
            name for name, hint in getattr(route, "endpoint", lambda: None).__annotations__.items()
            if isinstance(hint, str) and hint.endswith(("Body", "Ruling", "Update", "Feature",
                                                        "Project", "Agent"))
        ] if hasattr(route, "endpoint") else []
        for name in body_models:
            hint = route.endpoint.__annotations__[name]
            assert hint in vars(sys.modules[route.endpoint.__module__]), (
                f"{route.path} takes {name}: {hint}, which is not resolvable at module scope -- "
                "FastAPI will treat it as a query parameter and the route will 422"
            )

    # And the route that hit it, end to end: a body that parses gets past
    # validation to the handler, which says there is no proposal to rule on.
    posted = bare_app.post(
        "/api/projects/demo/proposal/apply",
        json={"accepted": ["add:frontend-tests"], "environment": False},
    )
    assert posted.status_code != 422, f"the body was refused before the handler ran: {posted.text}"
    assert posted.status_code == 409 and "re-survey" in posted.json()["detail"]


def test_a_proposal_says_which_parts_wait(tmp_path):
    source = factory_source("server")
    assert '"pending_parts": parts' in source


def test_the_ledger_answers_whether_an_old_call_was_money(tmp_path):
    """A run recorded before `call_entry` split the figure has rows that say
    what the route reported and nothing about who was charged. The console
    cannot tell from the row, and the guess it used to make was that a reported
    figure is spend -- which is how $6.16 of subscription turns ended up in a
    column headed Cost on a run that cost nothing.

    Answered at read time from the config, so one ledger does not read two ways
    depending on when its rows were written."""
    from fastapi.testclient import TestClient

    from factory.server import create_app

    config, project = _demo_project(tmp_path)
    store = project.feature_store("feat-1")
    store.append("state", {"feature_id": "feat-1", "stage": "built"}, role="orchestrator")
    # Exactly the shape a run wrote before the split existed: a figure, and no
    # word on whether it is money.
    for route in ("claude-code", "default"):
        store.append("call", {
            "kind": "completion", "role": "scout", "route": route,
            "asked": "opus", "answered": "opus", "outcome": "answered",
            "cost_usd": 0.25,
        }, role="scout")

    rows = TestClient(create_app(config)).get(
        f"/api/projects/{project.id}/features/feat-1/log").json()
    calls = {r["payload"]["route"]: r["payload"] for r in rows if r["kind"] == "call"}

    plan = calls["claude-code"]
    assert plan["billed"] is False
    assert plan["cost_usd"] == 0.0, "a plan's turn is not money in the Cost column"
    assert plan["notional_usd"] == 0.25, "and the figure is not thrown away"

    key = calls["default"]
    assert key["billed"] is True and key["cost_usd"] == 0.25


def test_the_console_can_reach_the_routes_api():
    from fastapi.testclient import TestClient
    from factory.server import create_app

    client = TestClient(create_app())
    body = client.get("/api/routes").json()
    assert body["default"] == "default"
    names = {r["name"] for r in body["routes"]}
    assert {"claude-code", "gemini-cli", "codex", "default"} <= names
    assert client.post("/api/routes/nope/check").status_code == 404


def test_a_role_cannot_be_saved_onto_a_route_that_does_not_exist():
    """422 rather than a write. A role pointed at nothing would run on the
    default provider while the file said otherwise."""
    from fastapi.testclient import TestClient
    from factory.server import create_app

    client = TestClient(create_app())
    r = client.patch("/api/roles/spec_writer", json={"route": "nowhere"})
    assert r.status_code == 422 and "nowhere" in r.json()["detail"]


def test_a_probe_that_goes_green_closes_its_own_finding():
    """The other direction, and the reason `restate` rather than a special
    case: the same machinery eleven computed checks already use. A check that
    ran again and no longer finds what it found is the check saying so, which
    is worth more than an agent saying so and costs nothing to obtain.
    """
    ledger = pipeline.FindingLedger()
    probe = _probe("t/test_race.py")

    _probe_round(ledger, [probe], [probe.path], 0)
    fid = "breaker-race"
    assert ledger.records[fid].outcome == "open"

    _probe_round(ledger, [probe], [], 1)
    assert ledger.records[fid].outcome == "no_longer_holds"
    assert "ran again in round 1" in ledger.records[fid].outcome_evidence

    # And if it comes back, it comes back. The record only grows.
    _probe_round(ledger, [probe], [probe.path], 2)
    assert ledger.records[fid].outcome == "open"
    assert "after it had stopped holding" in ledger.records[fid].outcome_evidence


def test_a_probe_run_that_did_not_happen_closes_nothing():
    """`fresh` is the breaker's own `ran`. A suite that could not start has not
    looked, and an empty answer from something that did not look is not an
    answer -- the same rule the computed checks already follow."""
    probe = _probe("t/test_thing.py")
    ledger = pipeline.FindingLedger()
    _probe_round(ledger, [probe], [probe.path], 0)
    assert ledger.records["breaker-thing"].outcome == "open"

    _probe_round(ledger, [probe], [], 1, ran=False)
    assert ledger.records["breaker-thing"].outcome == "open", (
        "a breaker that could not run has not shown that anything was fixed")


def test_a_recording_says_what_stopped_it_and_keeps_its_name_without_frames(tmp_path):
    """Fourteen white cards, and the reason each was white -- `connect
    ECONNREFUSED 127.0.0.1:8300` -- inside every archive where nobody saw it. One
    with no frames at all lost its title too, and with it the criterion it was
    for."""
    import zipfile

    events = [
        {"type": "context-options", "title": "card_drag.spec.ts:69 \u203a [AC-12] a drop"},
        {"type": "before", "startTime": 1, "title": 'Fixture "seededBoard"'},
        {"type": "error", "message": "\u001b[31mTypeError: fetch failed\u001b[39m\n"
                                     "[cause]: Error: connect ECONNREFUSED 127.0.0.1:8300\n"
                                     "    at post (fixtures.ts:36:20)"},
    ]
    archive = tmp_path / "stopped-chromium-trace.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("test.trace", "\n".join(json.dumps(e) for e in events))

    frames, manifest = pipeline.trace_player(archive)
    assert frames == []
    assert "[AC-12]" in manifest["title"], "a recording with no frames lost its name"
    assert manifest["error"] == ("TypeError: fetch failed\n"
                                 "[cause]: Error: connect ECONNREFUSED 127.0.0.1:8300"), \
        "the reason is missing, or carries the terminal's colour codes or the stack"

    # Written beside the archive even so, and listed as having nothing to step through.
    class _Unpacking:
        _unpack_trace = pipeline.Factory._unpack_trace
    assert "[AC-12]" in _Unpacking()._unpack_trace(tmp_path, archive.name)
    assert (tmp_path / "stopped-chromium-trace.player.json").is_file()
    src = factory_source("server").split("def list_traces")[1]
    assert '"frames": bool(shots)' in src and '"error": error' in src


def test_only_this_pass_s_recordings_are_offered():
    """The same rule the pictures follow. A recording of a run against a tree
    three repairs ago describes a branch that no longer exists."""
    src = factory_source("server").split("def list_traces")[1]
    src = src[:src.index("@app.get")]
    # The same question the packet asks, answered in one place.
    assert "current_recordings(store.records())" in src
    assert "if mine is not None and f.name not in mine:" in src
    # Archives only. Unpacking a recording leaves a directory of frames and a
    # manifest beside it, and a listing that walked every file would offer
    # those as recordings of their own.
    assert 'p.suffix.lower() == ".zip"' in src
    # Downloaded, not rendered: nothing in a browser opens one but Playwright's
    # own viewer, and the command to do it comes off the route rather than
    # being written into a string in the console.
    # `def get_trace(` exactly: the player and frame routes share the prefix.
    served = factory_source("server").split("def get_trace(")[1]
    assert 'media_type="application/zip"' in served
    assert '".zip"' in served, "only a trace bundle is served from that route"


def test_the_walkthrough_and_the_whole_recording_are_both_reachable():
    """The player answers "show me what the test did". The archive answers
    "show me the page's own structure at that moment", which only the real
    viewer can. A band that offered one and not the other would be choosing
    for the reader."""
    from factory.server import create_app
    from fastapi.testclient import TestClient

    base = "/api/projects/kanban/features/card-tags-fee892/traces"
    client = TestClient(create_app())
    listed = client.get(base)
    if listed.status_code != 200 or not listed.json().get("traces"):
        import pytest
        pytest.skip("no collected recording on this machine to serve")
    entry = listed.json()["traces"][0]
    name = entry["name"]
    # The listing says whether a walkthrough exists, so a row never offers one
    # that answers 404.
    assert entry["frames"] is True

    player = client.get(f"{base}/{name}/player")
    assert player.status_code == 200 and player.json()["frames"]
    frame = player.json()["frames"][0]["file"]
    assert client.get(f"{base}/{name}/frames/{frame}").headers["content-type"] == "image/jpeg"
    # And the archive itself, for the viewer.
    assert client.get(f"{base}/{name}").headers["content-type"] == "application/zip"

    # A frame name is not a path.
    assert client.get(f"{base}/{name}/frames/..%2F..%2Fx.jpeg").status_code == 404

    ui = (ROOT / "console" / "ui" / "evidence.js").read_text(encoding="utf-8")
    assert "function TracePlayer(" in ui
    # Both ways in, on the card: the thumbnail opens the walkthrough and the
    # download hands over the archive for the real viewer.
    assert "openTrace(t.name)" in ui and "href=${traceUrl(t.name)} download" in ui
    assert "t.frames !== false" in ui, "the walkthrough is offered only where there is one"
    # The card is a picture's card with a play badge, so a reader does not have
    # to learn two shapes to read one band.
    assert 'class="ee-shot"' in ui and "ee-play" in ui
    # Stepping is the point, not playing: thirteen frames of a five-second test
    # play in about a second and tell nobody anything.
    assert "ArrowRight" in ui and "ArrowLeft" in ui
    assert "npx playwright show-trace" in ui, "and it says what the frames cannot show"


def test_the_feature_log_carries_call_records_in_full(tmp_path):
    """The console's call log and each station's models are drawn from call
    records' payloads, and the feature log sent every record without one. Both
    rendered nothing from the day they were written, and nothing said so."""

    assert "call" in server.INLINE_LOG_KINDS
    src = factory_source("server")
    feature_log = src[src.index("def feature_log("):src.index("def feature_log_record(")]
    assert "INLINE_LOG_KINDS" in feature_log
    app = app_js()
    calls = app[app.index("function foldCalls("):app.index("function callRow(")]
    assert "row.r.payload" in calls, "the console reads call payloads from the log"


def test_a_readout_belongs_to_the_turn_that_produced_it():
    """The gates run again every repair round. Reading the *latest* gates
    record for every gates row would report round 1's result under the build's
    row -- a screen that draws each round separately and then tells all of them
    the same thing."""
    records = [
        _work(1, "gates", "2026-01-01T10:05:00+00:00",
              {"results": [_check("api-lint", passed=False, exit_code=1)]}),
        _turn(2, "gates", "2026-01-01T10:00:00+00:00", "2026-01-01T10:06:00+00:00"),
        _work(3, "gates", "2026-01-01T11:05:00+00:00", {"results": [_check("api-lint")]},
              meta={"round": 1}),
        _turn(4, "gates", "2026-01-01T11:00:00+00:00", "2026-01-01T11:06:00+00:00",
              round_index=1),
    ]

    readouts = server.readouts_for(records)

    assert readouts[2][0]["entries"][0]["outcome"] == "failed"
    assert readouts[4][0]["entries"][0]["outcome"] == "passed", (
        "the round that fixed it is not the round that broke it")


def test_a_readout_carries_the_names_of_files_and_never_their_contents():
    """Every station that writes code or tests records the files whole. The
    oracle's are 25KB, the workers' 90KB, and the log they would travel in is
    polled for the length of a run."""
    records = [
        _work(1, "oracle", "2026-01-01T10:01:00+00:00", {"strategy": "per criterion",
              "tests": [{"path": "test_one.py", "contents": "x" * 5000,
                         "criterion_ids": ["AC-1"], "cases": [{"name": "test_one"}]}]},
              role="oracle"),
        _turn(2, "oracle", "2026-01-01T10:00:00+00:00", "2026-01-01T10:02:00+00:00"),
    ]

    readout = server.readouts_for(records)[2]

    assert "x" * 100 not in json.dumps(readout)
    assert len(json.dumps(readout)) < 1000
    entry = readout[0]["entries"][0]
    assert entry["text"] == "test_one.py"
    assert "AC-1" in entry["note"] and entry["outcome"] == "1 case"


def test_a_readout_is_a_convenience_and_never_takes_the_log_down():
    """A payload from a run older than the field this reads is a row that opens
    onto its calls, as it did before. It is not a 500 on the ledger."""
    records = [
        _work(1, "gates", "2026-01-01T10:01:00+00:00", {"results": "not a list at all"}),
        _turn(2, "gates", "2026-01-01T10:00:00+00:00", "2026-01-01T10:02:00+00:00"),
    ]

    assert server.readouts_for(records) == {}


def test_a_build_baselines_the_base_branch_again_when_it_has_moved():
    """The baseline proves the checks were green on the code features start
    from. It re-ran at setup, on request, or when the check list changed --
    never when the base branch moved, so after a merge it described a commit
    no build started from. Every way of starting a build now checks first."""
    src = factory_source("server")
    helper = src[src.index("async def rebaseline_if_moved("):src.index("def factory_for(")]
    assert "head_sha, project.repo_path" in helper, "the base is looked at off the event loop"
    assert "!= project.state.baseline_sha" in helper
    assert "await self.onboarding().run_baseline(project)" in helper
    assert "except Exception:\n            log.exception(" in helper, \
        "a baseline that cannot run does not stop the build, and is said"
    # A route starts a build through `start_run`, which looks at the base first
    # unless told not to; the one start that is not a route does it by hand.
    runner = src[src.index("def start_run("):src.index("background.add_task(run)", src.index("def start_run("))]
    assert re.search(r"if rebaseline:\s+await self\.rebaseline_if_moved\(project_id\)\s+await work\(", runner)
    routed = re.findall(r"start_run\(background, project_id, feature_id,\s+lambda f: f\."
                        r"(run_build|revalidate|rebuild)\([^)]*\)\)", src)
    assert sorted(routed) == ["rebuild", "revalidate", "run_build", "run_build", "run_build"], \
        "a build started without looking at the base"
    direct = re.findall(r"await (?:factory|(?:self\.)?factory_for\(project_id\))\.(?:run_build|revalidate|rebuild)\(", src)
    guarded = re.findall(
        r"await (?:self\.)?rebaseline_if_moved\(project_id\)\s+await (?:factory|(?:self\.)?factory_for\(project_id\))"
        r"\.(?:run_build|revalidate|rebuild)\(", src)
    assert len(direct) == len(guarded) == 1, "a build started without looking at the base"
    assert "project.state.baseline_sha = sandbox.state.base_sha" in inspect.getsource(
        onboarding.ProjectOnboarding._baseline)


def test_the_crew_page_shows_the_way_out_plainly_first():
    """Where the harnesses are, because they are what it carries. It says what
    the proxy is before it says anything about it -- "the way out" and "an
    older version" meant nothing to someone who had not read the code -- then
    one plain status line, then what went out and what was blocked. Starting it is a button,
    and loading the page only reads."""
    app = app_js()
    assert "${routesSection()}\n\n      ${egressSection()}" in app
    section = app.split("function egressSection() {")[1].split("\n}\n")[0]
    assert (section.index("eg-intro") < section.index("eg-line")
            < section.index("eg-dests") < section.index("eg-list"))
    assert "Fabrika runs every agent and every check in a Docker container" in section
    assert "Nothing was blocked" in section
    assert "api('/api/egress').catch(() => null)" in app
    assert "'/api/egress/start', { method: 'POST' }" in app
    src = factory_source("server")
    get = src.split('@app.get("/api/egress")')[1].split("@app.")[0]
    assert "egress.status(" in get and "ensure_proxy" not in get


def test_a_cleanup_fix_that_was_applied_counts_at_once():
    src = factory_source("server")
    apply = src.split('def apply_proposal(')[1].split("@app.")[0]
    assert '"cleanup_applied"' in apply and "o.title for tier, o in chosen" in apply
    assert src.count('"unchecked_levels": unchecked_levels(project.state)') == 2, \
        "the project page and the feature payload both carry the levels"


def test_a_feature_s_spend_is_its_own_calls_not_the_console_s_running_total():
    """Every feature's `usage` record is the console process's running total,
    shared by everything it served -- so a feature built beside another carried
    both. Each call is recorded against the feature that made it."""
    from factory import overview

    records = [
        {"kind": "call", "payload": {"route": "default", "billed": True, "cost_usd": 0.5,
                                     "prompt_tokens": 100, "completion_tokens": 20}},
        {"kind": "call", "payload": {"route": "codex", "billed": False, "notional_usd": 2.25,
                                     "prompt_tokens": 900, "completion_tokens": 80}},
        # From before rows said whether they were billed: judged by the route,
        # so a subscription's per-call figure stops reading as money charged.
        {"kind": "call", "payload": {"route": "claude-code", "cost_usd": 6.16}},
        {"kind": "call", "payload": {"route": "claude-code", "kind": "session", "billed": True}},
        {"kind": "usage", "payload": {"total_cost": 99.0, "notional_cost": 99.0}},
        {"kind": "call", "payload": "unreadable"},
    ]
    got = overview.spend(records, billed_routes={"default"})
    assert (got["billed"], got["notional"]) == (0.5, 8.41)
    assert got["tokens"]["default"] == {"subscription": 0, "charged": 120}
    assert got["tokens"]["codex"] == {"subscription": 980, "charged": 0}

    rows = [{"verdict": "send_back", "since": "2026-09-01"}, {"verdict": "ship_with_rulings", "since": "2026-09-20"},
            {"verdict": "ship_with_rulings", "since": "2026-09-10"}, {"since": "2026-08-01"}]
    assert [r.get("verdict", "") + r["since"] for r in overview.order_waiting(rows)] == [
        "ship_with_rulings2026-09-10", "ship_with_rulings2026-09-20", "send_back2026-09-01", "2026-08-01"]

    src = factory_source("server")
    ep = src.split('@app.get("/api/projects/{project_id}/overview")')[1].split("@app.")[0]
    assert "overview.behind_main(" in ep and "overview.feature_summary(" in ep


def test_history_folds_re_readings_in_a_row_and_keeps_what_they_cost():
    from factory import history

    reading = lambda n, cost: {"kind": "resurvey", "meta": {"cost_usd": 0.0, "notional_usd": cost},
                               "payload": {"gate_changes": [{"action": "add", "name": f"c{i}"}
                                                            for i in range(n)]}}
    records = _ledger(reading(0, 1.0), reading(2, 2.0), reading(1, 0.5),
                      {"kind": "resurvey_ruling", "payload": {"applied": ["add:c0"], "rejected": []}},
                      reading(0, 1.0))
    events = history.project_history(records, [])["events"]
    assert [e["title"] for e in events] == [
        "Fabrika re-read the repository: nothing to change",
        "You accepted 1 change",
        "Fabrika re-read the repository 3 times",
    ]
    folded = events[-1]
    assert folded["detail"] == "the latest proposes 1 change" and folded["items"] == ["add c0"]
    assert folded["cost"] == {"billed": 0.0, "notional": 3.5}
    assert events[1]["items"] == ["added c0"]


def test_a_re_run_at_a_features_start_names_the_feature():
    """Recorded on the attribution now; an older record names it only inside
    a container name, and one with neither still reads as a sentence."""
    from factory import history

    records = _ledger(
        {"kind": "attribution", "payload": {"feature_id": "card-tags-fee892", "gate": "lint",
                                            "at_base": "passed"}},
        {"kind": "attribution", "payload": {"gate": "types", "at_base": "failed",
                                            "output_tail": "fabrika-kanban-user-support-58e79f-base-db-1"}},
        {"kind": "attribution", "payload": {"gate": "e2e", "at_base": "passed"}},
    )
    events = history.project_history(records, [{"feature_id": "card-tags-fee892", "title": "Card Tags",
                                                "created_at": "", "records": []}])["events"]
    by = {e["seq"]: e for e in events}
    assert by[1]["feature_id"] == "card-tags-fee892" and "failed in Card Tags" in by[1]["title"]
    assert "failed in User Support" in by[2]["title"] and "already broken" in by[2]["detail"]
    assert by[3]["title"] == "e2e failed in a feature, and passed at the commit it started from"

    src = class_source(pipeline.Factory)
    assert '"feature_id": state.feature_id,\n                            "base_sha": base_sha,' in src


def test_a_page_that_does_not_answer_says_whether_the_door_stopped(monkeypatch):
    """A dead hop and an app that is not listening look the same from outside:
    the connection is accepted and closed. The failure that found this left an
    empty log and a one-line error. Now the door is asked which hop stopped and
    what it printed, and that goes with the failure."""
    from factory import containers

    door = containers.Door(Config().docker, "p/f")
    door.containers = ["aaaaaaaaaaaa1111", "bbbbbbbbbbbb2222"]

    async def fake_run(argv, timeout=60.0, cwd=None):
        if "inspect" in argv:
            return gates.Execution(exit_code=0,
                                   output="running" if argv[-1].startswith("b") else "exited")
        return gates.Execution(exit_code=0, output="OSError: [Errno 98] address in use")
    monkeypatch.setattr(containers, "_run", fake_run)
    said = asyncio.run(door.trouble())
    assert "aaaaaaaaaaaa stopped" in said and "address in use" in said
    assert "bbbbbbbbbbbb" not in said, "a hop that is running is not reported"

    from factory import preview
    assert "await up.door.trouble()" in inspect.getsource(preview.Previews._open)
    assert "await up.door.trouble()" in inspect.getsource(preview.measure)


def test_the_reading_measures_the_preview_the_way_a_person_will_open_it(tmp_path):
    """The button at review is offered on the strength of the page having
    answered, not of the survey having said it would."""
    from factory import preview
    from factory.gates import LocalRunner

    config, _, project = _previewable(tmp_path)
    cwd = tmp_path / "repo"
    probe = asyncio.run(preview.measure(project, LocalRunner(), cwd, config, label="t", sha="abc"))
    assert probe is not None and probe.ok and probe.status == 200, probe
    assert probe.sha == "abc" and probe.url == "/"

    (tmp_path / "b").mkdir()
    config, _, lost = _previewable(tmp_path / "b", path="/no-such-page")
    missing = asyncio.run(preview.measure(lost, LocalRunner(), tmp_path / "b" / "repo", config,
                                          label="t"))
    assert missing is not None and not missing.ok and missing.status == 404
    assert "path" in missing.problem

    project.state.environment.preview = None
    assert asyncio.run(preview.measure(project, LocalRunner(), cwd, config, label="t")) is None
    src = inspect.getsource(onboarding.ProjectOnboarding._baseline)
    assert "measure_preview(" in src, "the baseline no longer measures the preview"


def test_a_preview_opens_at_review_is_recorded_and_closes_before_the_feature_moves(tmp_path):
    """Only at gate 2; the page really answers; opening and closing are in the
    ledger with the commit; and a verdict takes it down before anything else."""
    import urllib.request

    from fastapi.testclient import TestClient

    from factory.llm import LLM
    from factory.pipeline import Factory
    from factory.server import create_app

    config, registry, project = _previewable(tmp_path)
    factory = Factory(config, project, llm=LLM(config))
    state = factory.create_feature("a small change", "Small change")
    assert state.sandbox is not None, "a feature is created with its worktree"
    store = factory.store_for(state.feature_id)
    store.append("state", state.model_dump(mode="json"), role="orchestrator")
    url = f"/api/projects/{project.id}/features/{state.feature_id}"

    def wait_for(client, status):
        deadline = time.time() + 30
        while time.time() < deadline:
            held = client.get(f"{url}/preview").json()["held"]
            if held and held["status"] == status:
                return held
            time.sleep(0.2)
        raise AssertionError(f"the preview never reached {status}: {held}")

    with TestClient(create_app(config)) as client:
        early = client.post(f"{url}/preview")
        assert early.status_code == 409 and "review" in early.json()["detail"]
        assert client.get(url).json()["preview"]["available"] is False

        state.stage = "awaiting_verdict"
        store.append("state", state.model_dump(mode="json"), role="orchestrator")
        offered = client.get(url).json()["preview"]
        assert offered["available"] and offered["open"] == "web"

        assert client.post(f"{url}/preview").status_code == 200
        held = wait_for(client, "open")
        assert held["url"].startswith("http://127.0.0.1:") and held["commit"]
        assert urllib.request.urlopen(held["url"], timeout=5).status == 200

        opened = [r["payload"] for r in store if r["kind"] == "preview"]
        assert opened and opened[-1]["event"] == "opened"
        assert opened[-1]["commit"] == held["commit"]

        verdict = client.post(f"{url}/verdict", json={"verdict": "rejected", "note": ""})
        assert verdict.status_code == 200
        after = client.get(f"{url}/preview").json()["held"]
        assert after["status"] == "closed" and after["closed_by"] == "your verdict"
        with pytest.raises(OSError):
            urllib.request.urlopen(held["url"], timeout=2)
        closed = [r["payload"] for r in store if r["kind"] == "preview"][-1]
        assert closed["event"] == "closed" and closed["by"] == "your verdict"


def test_the_moves_away_from_review_close_the_preview_first():
    """Every route that releases, rebuilds or re-measures the worktree takes the
    app standing on it down, and a refused request does not."""
    src = factory_source("server")
    for route, words in (("def post_verdict", '"your verdict"'),
                         ("def delete_feature", '"the feature being deleted"'),
                         ("async def dispatch", '"sending it for more work"'),
                         ("async def rebuild", '"a rebuild"'),
                         ("async def revalidate", '"measuring it again"')):
        body = src.split(route, 1)[1].split("\n    @app.", 1)[0]
        assert words in body, f"{route} no longer closes the preview"
        if route in ("async def rebuild", "async def revalidate", "async def dispatch"):
            assert body.index(words) > body.rindex("raise HTTPException"), \
                f"{route} closes the preview before its guards could refuse"
    life = src.split("async def lifespan", 1)[1].split("app = FastAPI", 1)[0]
    assert "reap_previews" in life and "stop_all" in life
    assert "if not isolation.TEST_SUITE_ON_HOST" in life.split("sweep_previews", 1)[0][-400:], \
        "the suite shares this machine's Docker with a real server and must not sweep it"


def test_a_preview_nobody_looks_at_or_that_review_has_moved_past_is_closed():
    from factory import preview
    from factory.config import Config

    class Ledger:
        def __init__(self):
            self.rows = []

        def append(self, kind, payload, **_):
            self.rows.append((kind, payload))

    published = []
    held = preview.Previews(Config(), published.append, idle_s=60)
    for fid in ("idle", "moved", "watched"):
        held._held[("p", fid)] = preview.Preview("p", fid, status="open", commit="abc")
    held._held[("p", "idle")].seen -= 120
    ledger = Ledger()
    stages = {"idle": "awaiting_verdict", "moved": "building", "watched": "awaiting_verdict"}
    closed = asyncio.run(held.reap(lambda p, f: (stages[f], ledger)))
    assert sorted(closed) == [("p", "idle"), ("p", "moved")]
    assert held.get("p", "idle").closed_by == "1 minutes with nobody looking"
    assert held.get("p", "moved").closed_by == "the feature moved on from review"
    assert held.get("p", "watched").status == "open"
    assert [p["event"] for k, p in ledger.rows] == ["closed", "closed"]
    assert all(e["phase"] == "preview" for e in published), "the screen is told"


def test_the_as_built_routes_say_what_is_committed_and_refuse_to_overwrite(tmp_path, monkeypatch):
    """The tab reads what is committed; the press is refused while somebody's
    own edit sits under the directory, and a reading is started, not awaited."""
    from fastapi.testclient import TestClient

    from factory.server import create_app

    config, project = _demo_project(tmp_path)
    started = []

    async def fake_refresh(self, p):
        started.append(p.id)
        return {}

    monkeypatch.setattr(asbuilt.ProjectAsBuilt, "refresh", fake_refresh)
    client = TestClient(create_app(config))

    status = client.get("/api/projects/demo/as-built").json()
    assert status["exists"] is False and status["dir"] == ".fabrika/as-built"
    assert status["kept"] == 0 and status["listed"] > 0
    assert client.get("/api/projects/demo/as-built/graph").status_code == 404
    assert client.get("/api/projects/demo/as-built/source", params={"path": "README.md"}).status_code == 404

    assert client.post("/api/projects/demo/as-built/refresh").json() == {"started": True}
    assert started == ["demo"]

    (project.repo_path / ".fabrika/as-built").mkdir(parents=True)
    (project.repo_path / ".fabrika/as-built/README.md").write_text("mine")
    refused = client.post("/api/projects/demo/as-built/refresh")
    assert refused.status_code == 409 and "not committed" in refused.json()["detail"]
    assert client.get("/api/projects/demo/as-built").json()["uncommitted"]


def test_the_crew_screen_counts_the_as_built_s_agents_and_sees_them_work(tmp_path):
    """The as-built's readings run outside any feature's build, so its two
    agents never reached the `usage` record the crew screen adds up -- a
    reading of 125 calls showed both as never having run. Each reading now
    carries its calls by agent, and the screen is told who is working now."""
    from fastapi.testclient import TestClient

    from factory.server import create_app

    config, project = _demo_project(tmp_path)
    project.store.append("as_built", {"commit": "c"}, role="human", meta={
        "calls": 5, "by_role": {"reader": {"calls": 4, "prompt_tokens": 100, "completion_tokens": 20,
                                           "unbilled_calls": 4},
                                "cartographer": {"calls": 1, "total_tokens": 50, "cost_usd": 0.25}}})
    project.store.append("as_built", {"commit": "b"}, role="human", meta={"calls": 7})
    asbuilt.ProjectAsBuilt.running["demo"] = {"phase": "reading", "detail": "3 of 9 files read", "role": "reader",
                                              "live": {"inflight": {"reader": 2}, "done": 3, "todo": 9}}
    try:
        body = TestClient(create_app(config)).get("/api/roles").json()
    finally:
        asbuilt.ProjectAsBuilt.running.pop("demo", None)
    usage = {r["name"]: r["usage"] for r in body["roles"]}
    assert usage["reader"]["calls"] == 4 and usage["reader"]["total_tokens"] == 120
    assert usage["reader"]["unbilled_calls"] == 4
    assert usage["cartographer"]["calls"] == 1 and usage["cartographer"]["cost"] == 0.25
    assert body["as_built_unsplit"] == {"readings": 1, "calls": 7}, "an unsplit reading is a total, not a guess"
    assert body["as_built_running"] == [{"project": "demo", "feature": "", "label": "refresh", "phase": "reading",
                                         "detail": "3 of 9 files read", "role": "reader",
                                         "inflight": {"reader": 2}, "done": 3, "todo": 9}]
    app = app_js()
    diagram = app[app.index("function pipelineDiagram("):app.index("/* --------------------------------------------------------- compact roster */")]
    assert "agent('reader')" in diagram and "agent('cartographer')" in diagram and "asBuiltNow(roles)" in diagram
    gate0 = diagram[diagram.index("const gate0"):diagram.index("const intake")]
    assert "reader" not in gate0, "the as-built is not a step after gate 0"


def test_the_project_view_carries_each_red_check_s_diagnosis(tmp_path):
    registry, project, _, _ = _diagnosed_project(tmp_path)
    assert registry.diagnosis_for(project, "api-tests").cause == "c"
    source = factory_source("server")
    assert '"diagnoses": {' in source and '"/api/projects/{project_id}/diagnosis/apply"' in source


def test_a_build_promised_for_later_is_re_armed_when_the_server_restarts(tmp_path, monkeypatch):
    """A build deferred until a plan's window resets is a promise on disk as
    well as a sleeping task, because the task dies with the process. The boot
    re-arms it -- and never had: the loop asked each project for `project_id`,
    a `Project` has `id`, and the AttributeError was swallowed as the
    project's own problem. Every scheduled build was forgotten by a restart."""
    import time as _time

    from fastapi.testclient import TestClient

    from factory.llm import LLM
    from factory.pipeline import Factory
    from factory.server import create_app
    from factory.server.context import Context

    config, project = _demo_project(tmp_path)
    factory = Factory(config, project, llm=LLM(config))
    state = factory.create_feature("a small change", "Small change")
    state.stage, state.start_at = "waiting_for_plan", _time.time() + 3600
    factory.store_for(state.feature_id).append("state", state.model_dump(mode="json"),
                                               role="orchestrator")
    armed = []

    async def start_when_ready(self, project_id, feature_id, start_at):
        armed.append((project_id, feature_id))

    monkeypatch.setattr(Context, "start_when_ready", start_when_ready)
    with TestClient(create_app(config)):
        pass
    assert armed == [(project.id, state.feature_id)], "a restart forgot a scheduled build"


def test_looking_at_the_settings_does_not_write_a_config(tmp_path):
    """Run from the example, the first edit promotes it into `factory.yaml`.
    The crew and provider pages only say where that file is -- and asked the
    function that makes it, so opening either page created one. A read writes
    nothing; an edit still promotes."""
    import shutil

    from fastapi.testclient import TestClient

    from factory.config import load_config, writable_config_path
    from factory.server import create_app

    shutil.copyfile(ROOT / "factory.example.yaml", tmp_path / "factory.example.yaml")
    config = load_config(tmp_path / "factory.example.yaml")
    config.paths.evidence = str(tmp_path / "evidence")
    client = TestClient(create_app(config))
    for page in ("/api/roles", "/api/provider"):
        answer = client.get(page)
        assert answer.status_code == 200, page
        assert not (tmp_path / "factory.yaml").exists(), f"opening {page} wrote a config"
    assert client.get("/api/roles").json()["config_path"] == str(tmp_path / "factory.yaml")

    assert writable_config_path(config) == tmp_path / "factory.yaml"
    assert (tmp_path / "factory.yaml").exists(), "the first edit still promotes the example"


def test_the_agent_configuration_screen_writes_the_crew_in_one_request(tmp_path):
    """What the screen reads and what it writes. The roster the crew page draws
    from then says which team each agent is on and whether it follows its
    level, because that page is where a single agent is still edited."""
    import shutil
    from fastapi.testclient import TestClient
    from factory.config import load_config as _load
    from factory.server import create_app

    shutil.copyfile(ROOT / "factory.example.yaml", tmp_path / "factory.example.yaml")
    config = _load(tmp_path / "factory.example.yaml")
    config.paths.evidence = str(tmp_path / "evidence")
    client = TestClient(create_app(config))

    seen = client.get("/api/staffing").json()
    assert seen["staffing"] is None and seen["suggested"]["worker"] == "standard"
    assert seen["presets"]["claude-code"]["deep"]["model"] == "opus"

    body = {"staffing": {"build": "claude-code", "check": "codex", "neither": "build"},
            "levels": {r: seen["presets"][r] for r in ("claude-code", "codex")},
            "roles": {r["name"]: {"level": seen["suggested"][r["name"]]} for r in seen["roles"]}}
    written = client.put("/api/staffing", json=body)
    assert written.status_code == 200, written.text
    assert written.json()["staffing"]["build"] == "claude-code"

    roster = {r["name"]: r for r in client.get("/api/roles").json()["roles"]}
    assert roster["worker"]["team"] == "build" and roster["worker"]["follows_level"]
    assert roster["oracle"]["team"] == "check" and roster["oracle"]["model"] == "gpt-5.6-sol"
    assert roster["scout"]["team"] == "neither"

    refused = client.put("/api/staffing", json={**body, "staffing": {"build": "gemini-cli",
                                                                       "check": "codex"}})
    assert refused.status_code == 422 and "switched off" in refused.json()["detail"]
