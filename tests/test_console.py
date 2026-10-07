"""The console: what each screen draws, and the stylesheet it is drawn with.

Split out of test_invariants.py, which keeps one test per invariant.
"""

from helpers import *  # noqa: F403


def test_the_key_is_never_returned_to_a_client(provider_app):
    client, tmp_path = provider_app
    secret = "sk-or-v1-neverechothisback0000"

    client.put("/api/provider", json={"api_key": secret})
    body = client.get("/api/provider").json()

    assert body["key_present"] is True
    assert body["key_hint"] == "...0000", "a four-character tail, not the key"
    assert secret not in json.dumps(body)

    for path in ("/api/roles", "/api/config"):
        assert secret not in client.get(path).text, f"{path} leaked the key"


def test_the_key_is_stored_outside_the_config_file(provider_app):
    client, tmp_path = provider_app
    secret = "sk-or-v1-outsidetheyaml1234"
    client.put("/api/provider", json={"api_key": secret})

    creds = tmp_path / ".factory-credentials.json"
    assert creds.exists()
    assert secret in creds.read_text()
    assert oct(creds.stat().st_mode)[-3:] == "600"

    # factory.yaml is round-tripped on every role edit and shown in the console.
    written = tmp_path / "factory.yaml"
    if written.exists():
        assert secret not in written.read_text()
    assert secret not in (tmp_path / "factory.example.yaml").read_text()


def test_testing_with_no_key_says_so_rather_than_blaming_the_key(provider_app):
    """Every failure used to read "rejected the key", including the case where no
    key was sent at all — which sent people hunting a working credential."""
    client, _ = provider_app

    result = client.post("/api/provider/test").json()
    assert result["ok"] is False
    assert result["checked"] == "nothing"
    assert result["sent"] == "", "nothing was sent, and the response says so"
    assert "No key is set" in result["detail"]
    assert "reject" not in result["detail"].lower()
    assert "Save" in result["detail"], "it names the action that was missed"


# ==========================================================================
# the editor url -- control room's one setting
# ==========================================================================


def test_the_editor_url_is_saved_and_read_back(provider_app):
    client, _ = provider_app
    template = "zed://file{path}:{line}"

    saved = client.put("/api/editor", json={"editor_url": template}).json()
    assert saved["editor_url"] == template

    # GET /api/config is what the console actually reads on boot, so the write
    # has to reach the same field that endpoint reports.
    assert client.get("/api/config").json()["editor_url"] == template


def test_the_editor_url_persists_to_the_config_file(provider_app):
    client, tmp_path = provider_app
    template = "cursor://file{path}:{line}"
    client.put("/api/editor", json={"editor_url": template})

    # factory.example.yaml is documentation checked into the repository; the
    # first edit promotes it, exactly as a provider or role edit already does.
    written = tmp_path / "factory.yaml"
    assert written.exists()
    assert template in written.read_text()
    assert template not in (tmp_path / "factory.example.yaml").read_text()


def test_an_empty_editor_url_is_saved_rather_than_refused(provider_app):
    """Empty hides the "open in editor" link -- it is a legal value, not a
    missing one, per `Config.editor_url`'s own docstring."""
    client, _ = provider_app

    saved = client.put("/api/editor", json={"editor_url": ""}).json()
    assert saved["editor_url"] == ""
    assert client.get("/api/config").json()["editor_url"] == ""


def test_the_proposal_shows_every_command_a_rule_carries():
    """A rule carries three invocations now. A card that renders two asks a human
    to approve a change they cannot see -- which is what it did for the reading
    whose whole substance was the report command."""
    app = app_js()
    card = app[app.index("key: 'test_file_commands'"):][:2200]
    assert "r.report" in card, "the report command is not rendered"
    assert "r.collect" in card, "the load command is not rendered"
    assert "r.command" in card


def test_the_resurvey_is_told_not_to_cite_a_bare_criterion_id():
    text = (ROOT / "factory" / "roles" / "surveyor.md").read_text(encoding="utf-8")
    assert "Do not cite a bare criterion id" in text


def test_the_surveyor_is_asked_for_a_way_to_show_a_human_what_happened():
    """A rendered-component test answers "does this show the badge". It cannot
    answer "can a coordinator reach the page and still use every control", and
    criteria are written about the second."""
    text = (ROOT / "factory" / "roles" / "surveyor.md").read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "browser-level `user` tier" in flat
    oracle = (ROOT / "factory" / "roles" / "oracle.md").read_text(encoding="utf-8")

    # The title is the only thing tying a recording to what it evidences, and
    # the harness reports a criterion whose recorded file has no test titled
    # for it -- so the brief says so, with the counter-example beside it.
    assert "titled with its id" in oracle
    assert "`test('[AC-13]" in oracle
    assert "a test whose title carries no id is a recording of nothing in particular" in oracle
    assert "One criterion per test." in oracle, (
        "one recording standing for four claims can lead with one of them")
    assert "Drive it to the state the criterion describes." in oracle, (
        "a real run drove the control the feature was asked for and stopped short of it")

    # No screenshot directory is named to a test any more. A brief that still
    # named one would send a model writing pictures into a directory nothing
    # creates and nothing collects.
    assert "FACTORY_SCREENS_DIR" not in text and "FACTORY_SCREENS_DIR" not in oracle


def test_the_surveyor_is_asked_for_what_the_new_machinery_needs():
    """Three capabilities were built and none is reachable until the one role
    that may recognise a runner by name is asked to fill them in.

    Every one of them is a field on a shape the surveyor produces, and a field
    nobody asks for comes back empty -- which is indistinguishable from a
    project that genuinely cannot offer it.
    """
    text = (ROOT / "factory" / "roles" / "surveyor.md").read_text(encoding="utf-8")
    flat = " ".join(text.split())
    # per-test attribution
    assert "--junitxml={report}" in flat and "reporter=junit" in flat
    # a database the migration criteria need
    assert "`disposable_db` — a test db of the suite's own" in flat
    assert "down_revision" in flat, "the surveyor must be told why it cannot pick a revision"
    # the arrangement gap, which is not the same as the access gap.
    # Asserted by its rule, not by an example: the prompt is deliberately free
    # of one project's domain nouns, so pinning `make_referral` here would put
    # them back and make this test the reason they could not be removed.
    assert "Factories are the gap you will find most often" in flat
    assert "Domain-level, never feature-level" in flat


def test_one_failing_test_fails_one_criterion_not_its_whole_file():
    """Measured, on a real run: a file carried fourteen criteria, one test
    disagreed about whether a response may carry an extra field, and all
    fourteen were reported to a human as failing while thirteen had tests that
    passed. The packet said `0 of 25 verified` and named nothing."""
    qa = _one_file_many_criteria()
    passed = [r.criterion_id for r in qa.results if r.status == "passed"]
    failed = [r.criterion_id for r in qa.results if r.status == "failed"]
    assert failed == ["AC-4"], failed
    assert len(passed) == 13


def test_without_a_report_every_verdict_is_what_it_was():
    """Per-test attribution is additive. A project whose runner cannot emit a
    report loses resolution, never correctness."""
    qa = _one_file_many_criteria(cases_reported=False)
    assert all(r.status != "passed" for r in qa.results)
    assert not any("test_ac" in (r.evidence or "") for r in qa.results)


def test_a_tag_the_runner_never_reported_is_a_bookkeeping_fault():
    """The join can drift -- a renamed test, a parametrised name. Read as a pass
    it would be a lie; read as a failure it would blame the feature for a string
    that did not match. It is neither, and it says so."""
    qa = _one_file_many_criteria(name_drift=True)
    assert all(r.status == "unknown" for r in qa.results)
    assert "never reported" in qa.results[0].evidence
    assert any("never reported" in f for f in qa.failures)


def test_a_suite_qualified_run_attributes_its_criteria():
    """The join end to end: the fault above reached the packet through
    `compute_qa`, so the fix is asserted where the verdict is written."""
    qa = _one_file_many_criteria(suite_prefix="Feature tags > ")
    failed = [r.criterion_id for r in qa.results if r.status == "failed"]
    passed = [r.criterion_id for r in qa.results if r.status == "passed"]
    assert failed == ["AC-4"], failed
    assert len(passed) == 13
    assert not any(r.status == "unknown" for r in qa.results)


def test_the_roster_bands_every_pipeline_agent_and_says_what_it_does():
    """An agent missing from the roster's ordering sorted to the front and
    read as an orphan, which is exactly how four new agents hid in plain
    sight. The ordering is a set of bands now -- the run in the order it
    happens -- and the same hole would be the same bug, so it is still
    checked here.

    And each one says what it is for. The table answered "which model, how
    hot, what has it cost" and never answered the question a person arrives
    with, which is what an arbiter is."""
    source = app_js()
    bands = re.search(r"const ROLE_PHASES = \[(.*?)\n\];", source, re.S)
    assert bands, "the roster's bands are gone"
    # Each band is [title, note, [names]] -- read the third element only, so a
    # band whose title happens to be a word like `build` is not mistaken for
    # an agent called `build`.
    placed = [n for group in re.findall(r"\n\s*\[([^\]]*)\]\]", bands.group(1))
              for n in re.findall(r"'([^']+)'", group.rpartition("[")[2])]
    assert placed, "the bands hold no agents"

    # Every agent the run calls: the pipeline's, plus whatever review agents
    # are configured -- a review role is added by a person and still has to
    # land somewhere on this screen.
    config = load_config(Path("factory.yaml"))
    called = _pipeline_roles() + _project_roles() + [r.name for r in config.review_roles()]
    missing = [r for r in called if r not in placed]
    assert not missing, f"the console roster has no band for: {missing}"

    unknown = [r for r in placed if r not in called]
    assert not unknown, f"a band names agents the orchestrator does not call: {unknown}"

    blurbs = re.search(r"const ROLE_BLURB = \{(.*?)\n\};", source, re.S)
    assert blurbs, "the roster stopped saying what each agent is for"
    described = re.findall(r"^\s{2}(\w+):", blurbs.group(1), re.M)
    undescribed = [r for r in called if r not in described]
    assert not undescribed, f"no sentence saying what these are for: {undescribed}"


def test_a_resurvey_is_told_what_the_project_already_has():
    """A role asked to propose a replacement without being shown the original
    proposes from nothing.

    Each of these has the same shape: the prompt states what is recorded, or
    says plainly that nothing is and what that costs. `blind_placements` was
    added to the role's instructions before it was added here, which would have
    had an opus call inventing directories with no idea whether any existed.
    """
    src = inspect.getsource(
        __import__("factory.onboarding", fromlist=["x"]).ProjectOnboarding.run_resurvey)
    for field, marker in (
        ("testing", "project.state.testing.tiers"),
        ("test_file_commands", "project.state.test_file_commands"),
        ("blind_placements", "project.state.blind_placements"),
    ):
        assert marker in src, f"the resurvey prompt never states this project's {field}"
        assert "(none recorded" in src, "a missing reading has to say what its absence costs"


def test_a_server_running_older_code_than_the_disk_says_so(tmp_path):
    """The fact no screen could state, which has now cost money three times.

    A console started before a field existed round-tripped projects through its
    own older shape and deleted three fields from the stored record. A reloader
    wedged and left a process holding the port and answering nothing. And a fix
    to what counts as test setup sat on disk for two hours while a human paid
    for a re-survey whose answer was byte-identical to the one before it, with
    nothing anywhere saying why.

    Python is imported once, so an edit after start is invisible to the running
    process. Role prompts and the console's own files are deliberately NOT
    counted: both are read per call, so editing them is live, and an indicator
    that lights when nothing is wrong stops being read.
    """
    import time
    from fastapi.testclient import TestClient
    from factory.config import Config
    from factory.server import create_app

    config = Config(); config.paths.evidence = str(tmp_path / "evidence")
    client = TestClient(create_app(config))

    fresh = client.get("/api/version").json()
    assert fresh["stale"] is False, "a server started after its own code read as stale"
    assert fresh["newest_source"] == "", (
        "a fresh server named a file as the culprit -- there is no culprit, and naming one puts "
        "a filename in front of a human that means nothing")

    # A prompt edited now is live, so it must not raise the flag.
    prompt = ROOT / "factory" / "roles" / "surveyor.md"
    prompt.touch()
    assert client.get("/api/version").json()["stale"] is False, \
        "editing a role prompt read as stale -- prompts load at call time (INV-10)"

    # Nor does rewriting a module with the same bytes. This is not pedantry: a
    # git checkout, a restore from a backup and a formatter that changes nothing
    # all do it, and the first version of this check used mtime alone and raised
    # a false alarm within the hour -- which is how an indicator stops being
    # read, and then it is not there for the one time it is true.
    gates_py = ROOT / "factory" / "gates.py"
    body = gates_py.read_bytes()
    time.sleep(0.01)
    gates_py.write_bytes(body)
    assert client.get("/api/version").json()["stale"] is False, \
        "a file rewritten with identical bytes read as changed"

    # A real edit does.
    gates_py.write_bytes(body + b"\n# touched by a test\n")
    try:
        after = client.get("/api/version").json()
        assert after["stale"] is True, "a module edited after start did not read as stale"
        assert after["newest_source"] == "factory/gates.py"
    finally:
        gates_py.write_bytes(body)

    app = app_js()
    assert "api('/api/version')" in app, "the console never asks"
    assert "function staleServerStrip()" in app, "nothing builds the warning"
    # Rendered, and rendered where no screen can decline to. It used to be drawn
    # by the project page alone, so a reader on the yard, a feature, the ledger
    # or settings was told nothing -- while the two actions it warns about are
    # reachable from all of them. Checked by call graph rather than by position
    # in the file: the call moved above the definition when it stopped being one
    # page's business, and an ordering check would have failed for the change
    # that fixed the bug.
    assert "staleServerStrip()" in app.split("function paintStaleBar()")[1], \
        "the warning is built and never painted"
    assert "paintStaleBar();" in app.split("function render()")[1], \
        "painted only where some screen chooses to"
    assert 'id="stale-bar"' in (ROOT / "console" / "index.html").read_text(), \
        "the bar lives inside #main, where a render can drop it"


def test_the_console_will_not_offer_approval_it_knows_will_be_refused():
    """The button has to agree with the API.

    `approve` refuses a project with no proved placement, and refuses one with
    a check that is not green. A page that lets a human press Approve anyway
    turns a correct refusal into what reads as a bug in the console, and the
    reason -- which is the useful part -- arrives as a toast instead of on the
    screen where it can be acted on.

    The greenness predicate has to be the server's, not `passed`: that field is
    set to True for an optional check that never started, and a console reading
    it would light the button on a board the API refuses.
    """
    app = app_js()
    # One function answers "what blocks approval", and the button and the banner
    # both read it. They used to hold separate rules and disagree out loud: a
    # live Accept button captioned "Lets features start here" under a panel
    # reading "One thing blocks approval", because the button asked whether ANY
    # placement proved and the panel asked whether ALL of them had. `approve`
    # refuses on `[r for r in placement_probe if r.usable]` -- any -- so the
    # button was right and the panel was inventing a blockage.
    assert "function blockingItems(p)" in app
    assert "${blockingItems(p).length ? 'disabled' : ''}>Accept checks</button>" in app, \
        "the button no longer derives from the one answer, or no longer says what it does"
    blocking = app.split("function blockingItems(p)", 1)[1][:1200]
    assert "(p.placement_probe || []).some((r) => r.usable)" in blocking, \
        "the console does not read the measurement approval turns on, the way approve reads it"
    for refusal in ("!p.baseline", "has never been run", "reports nothing", "judgingCount(p)"):
        assert refusal in blocking, f"approval can be refused for {refusal} and nothing says so"
    # The two must agree on what "something is judging" means, and both derive it
    # from the last baseline rather than from anything stored.
    assert "function judgingCount(p)" in app and "function parkedChecks(p)" in app
    assert "isGreen(results[g.name])" in app.split("function judgingCount(p)", 1)[1][:320], \
        "the page decides what counts as judging by a rule of its own"
    assert "return Boolean(r && r.started !== false && r.passed && !r.skipped);" in app, \
        "the console judges greenness by a rule of its own"
    # And the reason is on the screen, not only in the toast the refusal returns.
    #
    # This used to assert `placementBlock(p)` appears in the file, which the
    # function's own `function placementBlock(p) {` satisfied -- so it passed for
    # as long as the function existed and went on passing after the last call to
    # it was deleted. It was checking that some code was written, not that a
    # reader would ever see it. What matters is the sentence and the measurement
    # behind it, so assert those.
    #
    # The sentence it then asserted lived in `newCodeSection`, which had no
    # caller either, and went with it. The reason a reader does see is the one
    # `blockingItems` lists, which is what the button is disabled on.
    assert "'nowhere proved to put a blind test'" in blocking, \
        "a human refused approval is not told what would lift the refusal"


def test_closing_a_gap_is_two_presses_not_five():
    """The workflow, as a property rather than a promise.

    Closing a gap the survey found took five presses: write each file, ask for a
    re-read, read it, apply it, re-run the checks. Four of those say the same
    thing twice -- "I did what you asked, look again" -- and one of them is a
    paid call a human has to remember to make.

    Two remain, and the seam between them is deliberate. Writing files and
    re-reading are one intent, so they are one call. Applying the reading is
    where a human still rules, because `usable` decides what an agent that has
    never seen this repository is told it may write against, and the generous
    direction is the one that costs a run. But accepting a reading invalidates
    the measurement taken against the old one, so applying and re-measuring are
    also one intent -- and leaving them apart is how a red row from before a
    change sat on screen for ninety minutes looking like a defect.
    """
    app = app_js()
    server = factory_source("server")

    # Press one: the files land and the repository is read again, in one call.
    assert '/scaffold-and-read' in server and "scaffold-and-read" in app
    # Checked by what the press does, not by what it is called. The old
    # assertion read the label -- "and read again" -- so renaming the button to
    # match the rest of the page's vocabulary failed a test about behaviour.
    # (It read the "write all" button, which was only ever drawn by
    # `scaffoldBlock` and `newCodeSection`; neither had a caller, and both are
    # gone. The press that is drawn and re-reads is the one that lands a file.)
    handler = app.split("if (target.dataset.replaceScaffold)", 1)[1].split("\n  }", 1)[0]
    assert "reread: true" in handler, "the button writes the files and never re-reads"

    # Press two: accepting what the reading proposes re-measures against it,
    # once nothing else in it waits. Unconditionally on the baseline: a
    # first version re-measured only when the apply had cleared it, which
    # skipped every `testing` reading -- `testing` is not in
    # `INVALIDATES_BASELINE` -- and silently did not.
    body = app.split("function rulePart(", 1)[1].split("\nfunction ", 1)[0]
    assert "if (state.project.project.baseline) return;" not in body
    assert "runBaseline()" in body and "accept && last" in body, (
        "accepting a reading does not take a new measurement, so the next thing the human "
        "reads was measured against something that no longer exists")


def test_every_proposal_a_resurvey_can_make_can_be_accepted():
    """A field on `SurveyDiff` that no button can tick is a proposal nobody can take.

    This has happened three times, each time silently: `test_file_commands`
    reached the prompt before it reached the schema, then `testing` reached the
    schema before it reached the console, and both rendered as "nothing
    proposed" with a full reading sitting inside them. The cost is a paid
    re-survey whose answer is unreachable.

    Derived from the model rather than listed here, so the next field added is
    caught by this test instead of by a human wondering why the page is empty.
    """
    from factory.schemas import SurveyDiff

    # These carry their own path: prose, per-row cards, the environment's own
    # checkbox, and the `_reason` that travels with whatever it explains.
    #
    # `considered` is the one field on this model a human does not rule on,
    # because the model does not write it: it is filled in when the reading is
    # taken, from the verify-lane requests actually rendered into its prompt, so
    # that ruling on this diff retires exactly those. A tickbox for it would be
    # asking a human to accept a fact.
    #
    # `guide_nominations` is not ruled on here either: code confirms each file
    # exists and adds it to the project's guides as waiting, and a person rules
    # on it there, in the Survey tab's guides, like any guide code found.
    OWN_PATH = {"summary", "unchanged", "recommendations", "gate_changes", "environment",
                "considered", "guide_nominations"}
    by_name = sorted(
        name for name in SurveyDiff.model_fields
        if name not in OWN_PATH and not name.endswith("_reason")
    )
    assert by_name, "the derivation found nothing, so this test proves nothing"

    app = app_js()
    projects = factory_source("projects")
    for name in by_name:
        assert f"key: '{name}'" in app, (
            f"`SurveyDiff.{name}` can be proposed but the console renders no way to accept it")
        assert f'"{name}" in wanted' in projects, (
            f"`SurveyDiff.{name}` can be ticked but `apply_survey_diff` ignores it")


def test_a_testing_proposal_says_what_it_changes_not_only_the_verdicts():
    """Library's re-reading added two helper files, eight helpers, a canary and
    new import lines to its unit level. The card showed "2 changed lines",
    folded, each reading `unit  usable` -- and called two levels changed whose
    only change was wording. A person accepted the rules card beside it and
    could not see that this one proposed anything."""
    app = app_js()
    card = app[app.index("diff.testing ? (() => {"):]
    card = card[:card.index("})() : null,")]
    assert "tierChanges(before.get(t.tier), t)" in card
    assert "tierChangeList(moved, reworded)" in card
    assert "diffLines(" not in card, "the verdict-only line diff is not what the card shows"
    changes = app[app.index("function tierChanges("):app.index("function tierChangeList(")]
    for field in ("setup_files", "fixtures", "import_examples", "canary", "cleanup_by", "verdict"):
        assert field in changes, f"a change to {field} would not be said on the card"


def test_the_ledger_has_a_word_for_every_record_kind():
    """A kind with no entry falls back to its raw key, which reads as a bug in
    the store rather than as a record."""
    missing = sorted(_feature_record_kinds() - _js_object_keys("KIND_WORDS"))
    assert not missing, f"the ledger has no words for: {missing}"


def test_every_agent_record_is_attributed_to_a_phase():
    """KIND_PHASE places a record on the timeline. An unmapped kind is dropped
    from it silently, so an agent can run and leave no visible trace."""
    mapped = _js_object_keys("KIND_PHASE")
    phases = set(pipeline.PHASE_NAMES)
    # Only the kinds an agent produces need placing; orchestrator bookkeeping
    # (state, writes, usage) has no phase of its own.
    agent_kinds = {
        "scout", "scout_slice", "interrogation", "spec", "plan", "worker",
        "integration", "oracle", "review", "review_sample", "recheck",
        "breaker", "breaker_suite", "arbiter", "repair_plan", "repair",
        "packet_raw",
    }
    assert agent_kinds <= _feature_record_kinds(), \
        "this list has drifted from what the orchestrator actually writes"
    missing = sorted(agent_kinds - mapped)
    assert not missing, f"agent records with no phase on the timeline: {missing}"
    bad = {v for v in re.findall(r":\s*'([^']+)'",
                                 re.search(r"const KIND_PHASE = \{(.*?)\n\};",
                                           app_js(), re.S).group(1))}
    assert bad <= phases, f"KIND_PHASE names phases that do not exist: {sorted(bad - phases)}"


def test_the_diagram_draws_every_agent_the_orchestrator_calls():
    """Review agents come from config and are drawn from the roster. Everything
    called by name has to be a literal in the picture, or the picture is a lie
    about what runs."""
    source = app_js()
    start = source.index("function pipelineDiagram(")
    body = source[start:source.index("\n}", start)]
    # `worker` is drawn singular; the phase that runs it is `workers`.
    missing = [r for r in _pipeline_roles() if f"'{r}'" not in body]
    assert not missing, f"agents the diagram does not draw: {missing}"


def test_a_proposal_that_is_not_a_gate_change_is_still_shown_and_acceptable():
    """Third time in one shape: a field added, and the UI unable to express it.

    A re-read proposed a full testing surface with no gate changes and no
    environment change. The console asked only "are there gate changes or an
    environment?", so it rendered **Nothing proposed** over a complete reading
    nobody could see or accept. `test_file_commands` had the same hole.

    Checked here because the console has no tests of its own, and both bugs in
    this family have been in that file.
    """
    app = app_js()
    block = app[app.index("function partsBlock("):]
    block = block[:block.index("\n}\n")]
    # Every part that waits gets a card -- one whose difference is only words
    # included, since the reading is not current until it is ruled on.
    assert "waiting.map((key) =>" in block and "built.get(key) ||" in block, \
        "a proposal with no gate change still reads as nothing proposed"
    parts = app[app.index("function readingParts("):]
    for key in ("'testing'", "'test_file_commands'"):
        assert key in parts, f"{key} is proposed and then never rendered"
    # Acceptable one at a time, by name, which is how `apply_survey_diff`
    # accepts it.
    assert 'data-rule-part="${esc(key)}" data-accept="1"' in block, \
        "rendered but not acceptable, so it can be read and never applied"
    applied = inspect.getsource(pipeline_projects_module.ProjectRegistry.apply_survey_diff)
    for key in ('"testing" in wanted', '"test_file_commands" in wanted'):
        assert key in applied, f"the tick for {key} would be ignored on the way in"


def test_the_oracle_is_told_what_to_do_at_each_level_rather_than_left_to_guess():
    """`absent` is the one people get wrong by trying.

    An oracle facing a project with no browser tooling wrote a suite anyway;
    every test errored and twenty-five criteria came back unverified for a
    reason that had nothing to do with the code. Worse, it reported the wrong
    reason -- "cannot be exercised with the installed environment" -- while
    jsdom and Testing Library were installed all along. It guessed at its own
    blocker because nobody had told it.
    """
    flat = " ".join((ROOT / "factory" / "roles" / "oracle.md")
                    .read_text(encoding="utf-8").split())
    for verdict in ("`usable`", "`inline_only`", "`absent`"):
        assert verdict in flat, f"the oracle is not told what {verdict} means for it"
    assert "Do not write tests for it and do not improvise one" in flat, \
        "nothing stops the oracle writing a suite for a level with no runner"
    assert "Reporting the gap costs the run nothing" in flat


def test_the_console_refuses_the_same_approvals_the_server_does():
    """The button must not offer something the server is about to reject."""
    source = app_js()
    blocking = source.split("function blockingItems(p)", 1)[1].split("\n}\n", 1)[0]
    assert "unrunnableGates(p.baseline)" in blocking and \
        "${blockingItems(p).length ? 'disabled' : ''}>Accept checks</button>" in source, \
        "the approve button is not gated on the same condition the server checks"
    # And the console's classifier has to know every marker the server's does.
    for marker in gates._ABSENT:
        assert marker in source, f"console gateOutcome is missing {marker!r}"


def test_the_evidence_rule_is_a_step_not_a_maxim():
    """Named as an example, the rule did not fire: the re-survey cited
    `pytest.ini` as its source for pytest and still shipped a dev manifest with
    no pytest-asyncio in it."""
    text = SURVEYOR.read_text()
    prose = _prose(text)
    assert "Open every config file you cite, and list the packages it implies." in prose
    assert "a step you perform, not a principle to bear in mind" in prose
    # The table is the instruction; a maxim without cases is what failed.
    for case in ("`pytest-asyncio`", "`pytest-cov`", "plugins ="):
        assert case in text, f"the implied-package table is missing {case}"


def test_the_surveyor_is_told_which_compose_service_to_name():
    text = SURVEYOR.read_text()
    prose = _prose(text)
    assert "Name the service that is built from this repository, never one pulled from a registry." in prose
    assert "no longer a database" in prose


def test_the_surveyor_is_told_to_make_a_red_check_ratchetable():
    """The green rule turned `parse_metric` from a rarity into the field that
    decides whether a red check can be resolved without doing the cleanup first.

    A human can fix a check, decline it, or hold it at today's count -- but only
    if something captured the count, and the surveyor is the only thing that
    sees the tool's output format before the check has ever run. Told the old
    rule ("leave it empty unless the exit code cannot carry the number"), it
    leaves every linter and type checker without a pattern, and ratcheting stops
    being available on exactly the checks that need it.
    """
    text = SURVEYOR.read_text()
    prose = _prose(text)
    assert "Every check on the list must be green before this project can build anything." in prose
    assert "give `parse_metric` for any check that reports a count of problems" in prose
    # And not the bar itself: the count is not knowable until the check has run.
    assert "Leave `threshold` and `threshold_max` empty." in prose
    assert "the human's to set at repo ready" in prose
    # The older reason a metric exists is still a reason.
    assert "Coverage and mutation score still need a metric" in prose
    assert "the number is the first capture group" in prose


def test_the_surveyor_is_told_that_working_directory_is_part_of_the_command():
    prose = _prose(SURVEYOR.read_text())
    assert "A CI step's `working-directory:` is part of the command." in prose
    assert "`cd backend && pytest`" in prose


def test_both_ways_of_reading_a_packet_name_each_outcome_the_same():
    """The review screens and the one-page packet each keep a table of what
    became of a finding, and the review screens' copy said in so many words that
    the two must not differ. They did: the same finding read "attempted, not
    fixed" on one and "attempted, still standing" on the other, under band
    titles one side had rewritten and the other had not."""
    import shutil
    import subprocess

    if not shutil.which("node"):
        pytest.skip("node is not installed")
    app = app_js()
    shared = (ROOT / "console" / "ui" / "shared.js").read_text(encoding="utf-8")

    def table(src, start):
        at = src.index(start)
        return src[at:src.index("\n};\n", at) + 3] if "{" in start else src[at:src.index("\n];\n", at) + 3]

    script = "\n".join([
        table(app, "const OUTCOME = {"), table(app, "const FINDING_BANDS = ["),
        table(shared, "export const OUTCOME = {").replace("export const OUTCOME", "const SHARED_OUTCOME"),
        table(shared, "export const BANDS = [").replace("export const BANDS", "const SHARED_BANDS"),
        "console.log(JSON.stringify({",
        "  app: Object.fromEntries(Object.entries(OUTCOME).map(([k, v]) => [k, [v.label, v.cls]])),",
        "  shared: SHARED_OUTCOME,",
        "  appBands: FINDING_BANDS.map((b) => [b.title, b.sub, b.outcomes]),",
        "  sharedBands: SHARED_BANDS.map((b) => [b.title, b.sub, b.has]),",
        "}));",
    ])
    out = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    read = json.loads(out.stdout)
    assert read["app"] == read["shared"], "an outcome is named differently on the two screens"
    assert read["appBands"] == read["sharedBands"], "the bands are titled differently on the two screens"


def test_no_git_call_can_hang_a_run():
    """A git that hangs -- a lock held by a dead process, a prompt nobody will
    answer -- has to cost seconds. Two calls had no timeout at all, and one of
    them was the snapshot taken before a reset: had it failed, the reset that
    followed would have thrown the uncommitted work away."""
    offenders = []
    for path in factory_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and getattr(node.func.value, "id", "") == "subprocess" and node.args
                    and isinstance(node.args[0], ast.List) and node.args[0].elts
                    and getattr(node.args[0].elts[0], "value", None) == "git"
                    and not any(k.arg == "timeout" for k in node.keywords)):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"git run with no timeout: {offenders}"


def test_a_narrow_screen_rule_is_not_cancelled_by_a_plain_rule_after_it():
    """The stylesheet was re-skinned by writing new rules after the old ones,
    and a narrow-screen rule loses to any plain rule for the same selector that
    comes later in the file. The phone layouts of the timeline, the questions
    and the as-built outline had been silently cancelled that way -- and one
    block was dropped outright, a selector glued to its `@media` by a stray `+`."""
    css = re.sub(r"/\*.*?\*/", "", stylesheet(),
                 flags=re.S)
    glued = re.findall(r"[^\s;{}]\s*@media", css)
    assert not glued, f"something is glued to an @media, and the browser drops the block: {glued}"

    rules = _css_rules(css)
    cancelled = []
    for at, (media, selectors, decls, _) in enumerate(rules):
        if not media or "max-width" not in media:
            continue
        for later_media, later_selectors, later_decls, head in rules[at + 1:]:
            if later_media is None:
                cancelled += [f"{media} {s} {{{p}}} -- undone by `{head}`"
                              for s in selectors & later_selectors
                              for p in set(decls) & set(later_decls) if decls[p] != later_decls[p]]
    assert not cancelled, "narrow-screen rules that never apply:\n" + "\n".join(cancelled)


def test_verifying_again_asks_in_a_dialog_that_says_what_happens():
    """Verifying again spends from a budget the feature cannot get back, so one
    press must not start it. It used to arm the button with "Press again -- a
    full run, about an hour", which was both cramped and wrong: nothing is
    built again. A dialog says what runs again and what is kept."""
    source = app_js()
    handler = source[source.index("if (target.id === 'revalidate')"):][:200]
    assert "revalidate()" not in handler, \
        "the revalidate button fires its POST on the first press"
    assert "askRevalidate()" in handler

    asking = source[source.index("async function askRevalidate()"):]
    asking = asking[:asking.index("\n}\n")]
    assert "confirmDialog(" in asking and "yes ? revalidate()" in asking
    assert "full run" not in asking, "verifying again does not build anything"
    for said in ("stays exactly as it is", "budget"):
        assert said in asking, f"the dialog does not say {said!r}"

def test_a_packet_is_never_drawn_as_current_while_a_run_is_measuring_again():
    """The console's one job is not to present a stale reading as a live one.

    A feature sitting on a packet with a run in flight is exactly that case: the
    verdict, the gates passed, the criteria verified and the blocker count are
    all numbers the running pass exists to replace, and the page drew them with
    no mark on them at all. Seen for real -- a packet reading "0 of 16 criteria
    verified" while the run that had already got 9 of 16 was three phases in.
    """
    source = app_js()
    screen = source[source.index("function reviewScreen()"):]
    screen = screen[:screen.index("\nfunction ")]

    assert "SETTLED_STAGES.includes(d.state.stage)" in screen, \
        "the packet screen does not ask whether its own numbers are still current"
    assert "superseded-strip" in screen, "nothing on the page says the packet is being replaced"
    assert "is-superseded" in screen, "the stale figures are drawn with a live packet's authority"
    # And it names what is running, because "being replaced" without "by what,
    # and how far along" is a spinner with more words.
    assert "runningPhase" in screen

    css = stylesheet()
    assert ".is-superseded .verdict-strip" in css and ".is-superseded .stat-row" in css


def test_a_packet_opens_on_the_work_waiting_and_the_full_packet_is_a_step_away():
    """A review opens on what it is asking of you, not on the record of itself.

    It used to be a list of requirements and a separate list of files, which is
    the same two columns with every line between them rubbed out -- so a reader
    could see that twenty-one things were asked for and that twenty files
    changed, and never which answered which.

    The summary is a wall: a verdict, a paragraph, five counters, then every
    finding and every decision in one column. It is the second thing you want,
    not the first, and it was what the `review` step opened.
    """
    source = app_js()

    gate2 = source[source.index("const GATE2_VIEWS"):]
    gate2 = gate2[:gate2.index("\n")]
    assert "'review'" in gate2, "the review step does not open the packet screens"

    # The summary keeps a home of its own -- it is the only place the decisions
    # are ruled on, the repair loop's cost is stated, and every finding is
    # listed with its outcome. Unreachable is not the same as removed.
    assert "packet: () => f('packet')" in source
    assert "params.packet ? 'packet'" in source
    assert "state.view === 'packet' && state.data" in source

    index = (ROOT / "console" / "ui" / "index.js").read_text(encoding="utf-8")
    landing = index[index.index("    switch (view) {"):]
    landing = landing[:landing.index("\n    }")]
    # The map answers "what was built against what was asked for", which is the
    # second question. The first is "what is waiting on me" -- and a reviewer
    # who opened this packet had to synthesise that across five tabs, because
    # the twenty-three findings that needed a ruling were a band on Objections,
    # a band on Security and a row on Repairs, with no count anywhere.
    assert "default: return html`\n        <${Worklist}" in landing, \
        "a packet with no view in the hash does not land on the work waiting"
    # And the router has to agree with it: `mountedView` names the view for a
    # settled packet with an empty hash, and the switch above only ever sees
    # what it chose. The two disagreed once and the default branch was dead.
    assert "SETTLED_STAGES.includes(stage) ? 'calls' : null" in source, \
        "the router sends a settled packet somewhere other than the worklist"

    work_map = (ROOT / "console" / "ui" / "workmap.js").read_text(encoding="utf-8")
    assert "hrefs.packet()" in work_map, "nothing links to the full packet any more"

    # `?criteria` was the requirement list's route for long enough to be in
    # people's history and in this repository's own notes. Deleting a screen is
    # allowed; leaving its links pointing at nothing is not.
    assert "params.criteria ? 'map'" in source, \
        "an old ?criteria link no longer lands anywhere"


def test_the_strongest_objection_survives_the_screen_it_was_written_on():
    """The adversary's brief calls it the one line a human reads under time
    pressure. It was on the requirement list, and the requirement list is gone.

    A packet-wide claim that lives on exactly one screen disappears silently
    the day that screen does, and nothing fails: the field is still populated,
    still written, still in the JSON, and no longer in front of anybody.

    It also has to appear once. When this screen was two slices the guard was
    `!security`, keeping a packet-wide line off the second tab; with one screen
    there is no second place to print it, so the assertion is the count.
    """
    findings = (ROOT / "console" / "ui" / "findings.js").read_text(encoding="utf-8")
    assert "strongest_objection" in findings, \
        "the strongest case against shipping is not on any tab"
    assert findings.count("fd-worst") == 1, \
        "the strongest objection is rendered in more than one place on the screen"


def test_the_review_says_how_much_of_it_is_left():
    """Every count in this console counts what happened. One has to count what
    is left, or a review has no shape and no end.

    On the packet this was built against, 23 findings needed a human ruling and
    the number 23 appeared nowhere: they were a band on Objections, a band on
    Security and a row on Repairs. The header said `5 blockers open` and `44
    findings raised, 31 distinct`, and neither of those is the job. Nothing
    said how many were dealt with, so a second sitting started from the top.
    """
    shared = (ROOT / "console" / "ui" / "shared.js").read_text(encoding="utf-8")
    work = (ROOT / "console" / "ui" / "worklist.js").read_text(encoding="utf-8")
    tabs = (ROOT / "console" / "ui" / "tabs.js").read_text(encoding="utf-8")

    assert "export function callsFor" in shared and "export function awaitingRuling" in shared

    # Done is a flag on the server, not a tick this screen remembers. A review
    # that forgets what you settled the moment you reload is a review nobody
    # finishes.
    assert "export const settledBy" in shared and "fl.anchor" in shared, \
        "what has been dealt with is not read back from the record"
    assert "source: 'finding'" in work and "anchor: f.id" in work, \
        "a ruling made here does not point at the finding it rules on"

    # The one count on the tab strip that goes down.
    assert "view: 'calls'" in tabs and "${left} of ${total}" in tabs, \
        "the tab does not say how many calls are left"


def test_a_clustered_finding_is_clustered_by_a_join_and_not_by_a_judgement():
    """Six agents found one `audit?limit=` overflow, five found one CDS
    fallback, three found one unprovisioned gate. The arbiter deduplicated
    round 0 and never deduplicated across rounds, so 23 findings reached a
    human as 23 problems when they were 11.

    Grouping them is worth doing and is not the console's judgement to make.
    So it is a join over two fields, checkable by reading them: share a file,
    and either share a criterion or name none. Cross-round `duplicate_of` stays
    the arbiter's, where a human can disagree with it.
    """
    shared = (ROOT / "console" / "ui" / "shared.js").read_text(encoding="utf-8")
    body = shared[shared.index("export function clusterFindings"):]
    body = body[:body.index("\nexport const SEV_RANK")]

    assert "meets(af, bf)" in body, "the join does not require a shared file"
    assert "meets(ac, bc) || (!ac.length && !bc.length)" in body, \
        "two findings about different criteria in one file are being merged"
    # Which title heads a cluster must not depend on the order the packet
    # happens to list its findings in.
    assert "x.id.localeCompare(y.id)" in body, "the cluster head is not deterministic"
    for absent in ("similar", "score", "threshold", "fuzz", "distance"):
        assert absent not in body.lower(), \
            f"the join has grown a {absent}, which is a judgement wearing arithmetic"


def test_deleting_a_screen_never_orphans_what_it_held():
    """A packet holds two things that are an author's account rather than a
    reviewer's reading of one: what each unit disclosed, and the choices it
    logged. Both were on a tab called "Review code", which also held the files.

    The files moved to the work map and the file screen and the tab went with
    them -- and on the packet that prompted this, that tab was the only route
    to 49 of 75 disclosures and 1 of 27 decisions. A screen is allowed to be
    deleted. Its contents are not allowed to leave with it.

    So the routing is one function, in one place, and every screen that shows
    an account asks it rather than filtering for itself.
    """
    shared = (ROOT / "console" / "ui" / "shared.js").read_text(encoding="utf-8")

    # Whose account it is, told from `data.workers` -- a fact -- rather than
    # from whether the unit id starts with `R-`, which is a convention the
    # schema asks a model to follow and cannot make it follow.
    assert "export const buildUnits" in shared and "(d || {}).workers || []" in shared, \
        "who wrote a disclosure is decided by something other than who wrote it"
    assert "export function repairAccount" in shared
    assert "export function homelessDecisions" in shared

    repairs = (ROOT / "console" / "ui" / "repairs.js").read_text(encoding="utf-8")
    assert "repairAccount(data)" in repairs, \
        "the repair rounds' own account of themselves is on no screen"
    assert "<${Disclosures}" in repairs and "account.disclosures" in repairs
    assert "account.decisions" in repairs and "onRule=${onRule}" in repairs, \
        "a repair round's decision can be read and not ruled on"
    # The screen draws decisions on the round that made them, which only works
    # for a packet whose rounds recorded their units. Every packet built before
    # they did has none, so anything no round claims is still drawn -- a screen
    # that knew only the new shape would orphan the account of every earlier
    # run, which is this test's whole subject.
    assert "const orphaned = account.decisions.filter" in repairs
    assert "!claimed.has(d.id)" in repairs

    # A file screen is where a build unit's account belongs, and it was
    # already reading both before the tab went.
    file_view = (ROOT / "console" / "ui" / "file.js").read_text(encoding="utf-8")
    assert "(d.files || []).includes(path)" in file_view
    assert "line.startsWith(`${u} -- `)" in file_view
    assert "onRule(d.id, 'accept')" in file_view, \
        "rulings cannot be made from the screen that now shows the decisions"

    # And the case the routing cannot place is computed and shown, rather than
    # prevented from arising by a tab that listed everything regardless.
    assert "id: 'unreachable-decisions'" in shared, \
        "a decision no screen leads to would now vanish silently"


def test_a_figure_put_away_takes_its_caveat_with_it():
    """Four of the header's figures are discounted by a blind spot, and each
    carries a dagger into the one that discounts it. "0 of 21 criteria
    verified" is not a reading of the code when no criterion had a blind test
    to fail; the dagger is the difference between a number and a claim.

    The figures now live behind a press, which is the exact situation the
    daggers exist for: a caveat that is only visible once the sheet is open is
    a caveat for a reader who already went looking. So the closed button
    carries one whenever any figure inside it does.
    """
    tabs = (ROOT / "console" / "ui" / "tabs.js").read_text(encoding="utf-8")

    stats = tabs[tabs.index("function Stats("):]
    stats = stats[:stats.index("\nexport function Verdict")]

    assert "const discounted = MARKED.filter((k) => disc[k]).length;" in stats, \
        "the button does not count how many of its figures are discounted"
    # `discountsFor` returns a fifth discount, on the repair loop's account of
    # itself, which is marked on Repairs and has no figure here. Counting
    # every key made the button promise four daggers over a sheet drawing
    # three, which is a worse lie than no dagger at all.
    marked = tabs[tabs.index("const MARKED = ["):]
    assert "'repairs'" not in marked[:marked.index("]")], \
        "the button counts a discount its sheet does not draw"
    assert "discounted > 0 &&" in stats and "vs-btn-disc" in stats, \
        "a discounted figure behind a closed button shows no mark at all"

    # And every figure that has a discount still offers the way into it, with
    # the sheet closing behind the reader rather than hanging over the screen
    # it sent them to.
    assert "const go = (spot) => { setOpen(false); onDiscount(spot); };" in stats, \
        "following a dagger leaves the sheet open over the screen it opened"
    for figure in ("disc.gates", "disc.criteria", "disc.probes", "disc.findings"):
        assert f"spot=${{{figure}}}" in stats, f"{figure} is computed and never offered"

    # Escape and a press outside are how every other transient surface in this
    # console closes, and a sheet that only closes by pressing the same button
    # again is a sheet people leave open.
    assert "'Escape'" in stats and "pointerdown" in stats, \
        "the stats sheet cannot be dismissed except by the button that opened it"


def test_the_review_screens_say_what_ran_and_what_it_cost():
    """A verdict with no account of what produced it is a claim, not evidence.

    The packet said what was found and never who looked, for how long, or at
    what price -- so a reader could not tell that three independent agents
    reviewed this and one of them was an adversary sampled three times, or that
    the loop stopped on its round cap rather than because it was finished. Both
    change what the verdict is worth.
    """
    app = app_js()
    summary = app[app.index("function runSummary(d) {"):]
    summary = summary[:summary.index("\nfunction ")]
    for fact in ("phases", "usage", "reviews", "records", "rework"):
        assert fact in summary, f"the run summary ignores {fact}"
    # One agent per row, however many rounds it ran in: the same three agents
    # ran four panels here, and twelve rows said nothing but "stop reading".
    assert "byRole" in summary

    assert "run: state.data && state.data.packet ? runSummary(state.data) : null" in app, \
        "the mounted screens compute their own arithmetic and can disagree with the packet page"
    # Who looked sits with the figures, in the header's stats sheet. It left
    # the strip under the first tab because it answers "what is this verdict
    # worth" and belongs with the other answers to that -- and because the
    # strip it used to live on also carried a finding count the tabs already
    # show and a wall-clock span that mostly measured the feature sitting
    # still.
    tabs = (ROOT / "console" / "ui" / "tabs.js").read_text(encoding="utf-8")
    assert "run.agents" in tabs and "Read by" in tabs, \
        "the header no longer says which agents read this"
    assert "a.samples" in tabs, \
        "an agent sampled three times is a different reading from one sampled once"
    assert "No review agent is configured" in tabs, \
        "a panel of nobody must say so rather than render an empty line"

    # The loop's own account, including why it stopped: a loop that stopped
    # quietly is indistinguishable from one that converged.
    repairs = (ROOT / "console" / "ui" / "repairs.js").read_text(encoding="utf-8")
    # The stop reason is the repair screen's -- it is the round that did not
    # happen. What the loop *cost* sits with the run's other figures instead:
    # at the head of that tab the headline read "$0.00 of a $10.00 budget",
    # corrected three elements below by a note saying eleven agents had run
    # unmeasured for a notional $12.67, more than the whole budget.
    assert "stop_reason" in repairs
    assert "cost_usd" not in repairs, "the cost belongs with the run's other figures"
    assert "rework.notional_usd" in tabs and "the loop cost" in tabs
    assert "could not have stopped them" in tabs, (
        "a budget that could not have stopped the run has to say so beside itself")


def test_the_packet_groups_its_findings_by_what_became_of_them():
    """Sixty-four findings in one column is a list nobody finishes.

    The ordering that mattered -- attempted and still there, first -- was
    invisible inside it, and twenty decisions still needing a ruling were mixed
    in with the ones already ruled.
    """
    app = app_js()
    bands = app[app.index("const FINDING_BANDS = ["):]
    bands = bands[:bands.index("\n];")]
    assert "attempted_not_fixed" in bands and "'open': " not in bands
    assert "open: true" in bands and "open: false" in bands, \
        "every band is expanded, or none is; the settled ones must start shut"

    screen = app[app.index("function reviewScreen()"):]
    screen = screen[:screen.index("\nfunction ")]
    assert "findingGroups(" in screen and "decisionGroups(" in screen
    assert "waiting on you" in screen, "the decisions needing a ruling are not named as such"
    # A ban, not a preference: the heading carries its own weight.
    assert 'class="eyebrow"' not in screen, "the packet page still labels its sections with kickers"


def test_discarding_a_feature_is_reachable_and_asks_twice():
    """It was reachable from one place: the bottom of the agent log.

    Nothing about a run's transcript says "this is where you throw the run
    away", so the one destructive act in the console was also its best-hidden
    one. It now sits last in the action bar and on every row of the rail, and
    both ask twice -- the same two-press arming the expensive action uses,
    because a control that destroys a record must say so before it does it.
    """
    source = app_js()

    bar = source[source.index("function featureBar(d, opts = {})"):]
    bar = bar[:bar.index("\nconst esc = ")]
    assert "act-discard" in bar, "the action bar has no discard"
    assert bar.index("opts.actions") < bar.index("act-discard"), \
        "discard must come last; it is the only action here that destroys anything"

    rail = source[source.index('<a class="pf-log"'):]
    rail = rail[:rail.index("</div>`;")]
    assert "pf-discard act-discard" in rail, "the rail row has no discard"
    assert "data-feature=" in rail, \
        "the rail's discard cannot say which feature it means, so it would take the open one"

    # Both routed through the arming, never straight to the delete.
    handler = source[source.index("if (target.classList.contains('act-discard'))"):][:120]
    assert "armDiscard(target)" in handler and "discardFeature(" not in handler

    arming = source[source.index("function armDiscard"):]
    arming = arming[:arming.index("\n}\n")]
    assert "the branch is kept" in arming, "the armed control does not say what survives"

    css = stylesheet()
    assert ".pf-discard { border: 0;" in css and "opacity: 0;" in css, \
        "a red control resting on every rail row makes the rail itself feel dangerous"


def test_a_ready_project_can_still_see_and_re_run_its_own_gates():
    """Gate 0's screen was reachable only while a project was *not* ready.

    Once approved, the bench took the route, and the gate list, the baseline,
    the drift and the proposal all became unreachable -- including the button
    that re-runs the baseline, which is exactly what a human needs after
    changing a gate. The way to see your own gates was to break the project.
    """
    app = app_js()
    assert "params.gates ? 'gates'" in app, "no route to the gate-0 screen"
    assert "state.view === 'gates' && state.project" in app, \
        "the route exists and renders nothing"

    bench = app[app.index("  if (view === 'start') {"):]
    bench = bench[:bench.index("\n  }")]
    assert 'id="run-baseline"' in bench, \
        "the bench cannot re-run the baseline, and the bench is what a ready project shows"

    # The bench draws projectBar, and the route to the gate list lives in that
    # bar's step strip -- which is where a strip of screen names belongs. It
    # used to be a separate button beside it, which meant two routes to one
    # screen. The invariant is reachability from the bench, not which element
    # carries it.
    assert "projectBar(p, {" in bench, "the bench no longer draws the project bar"
    bar = app[app.index("function projectBar"):]
    bar = bar[:bar.index("\n/* One row per gate")]
    assert "?gates" in bar, "nothing the bench draws reaches the gate list"


def test_the_drift_the_detector_finds_is_actually_drawn():
    """The detector ran, the API served `stale: true`, and the page said nothing.

    `toolingDriftBlock` was written, commented, styled -- and never called from
    anywhere. A gate list that has stopped describing its repository is the one
    thing gate 0 exists to let a human rule on.
    """
    app = app_js()
    calls = [i for i in range(len(app)) if app.startswith("toolingDriftBlock(", i)]
    assert len(calls) >= 2, "toolingDriftBlock is defined and never called"
    screen = app[app.index("function projectScreen()"):]
    screen = screen[:screen.index("\nfunction ")]
    assert "toolingDriftBlock(" in screen
    assert "state.project || {}" in screen, \
        "the block reads a `drift` that is not in this function's scope; the screen throws"


def test_the_slow_project_actions_say_they_are_working():
    """`withBusy` raises a toast, and a toast is gone in 3.8 seconds.

    The re-survey behind "What should change?" is a model reading a repository:
    it runs for the better part of a minute, and for fifty of those seconds the
    page looked exactly like a page where nothing had been pressed.
    """
    app = app_js()
    helper = app[app.index("function working(button, label)"):]
    helper = helper[:helper.index("\nasync function withBusy")]
    assert "button.disabled = true" in helper and "finally" not in helper
    assert "return () => {" in helper, "nothing restores the label when the call ends"

    # Awaited calls: the fetch is open for the duration, so the button holds the
    # state and releases it in a `finally`.
    for fn, label in (("proposeChanges", "Reading the repository"),
                      ("resurvey", "Surveying")):
        body = app[app.index(f"function {fn}()"):]
        body = body[:body.index("\n}\n")]
        assert "working(" in body and label in body, f"{fn} gives no sign that it is running"
        assert "finally" in body and "done()" in body, \
            f"{fn} leaves its button disabled when the call throws"

    # Backgrounded calls: the POST returns in milliseconds and the work takes
    # half a minute, so releasing on the response is a lie. The flag is set here
    # and cleared by the progress stream -- with a ceiling, because a dropped
    # stream must not disable a button forever.
    baseline = app[app.index("function runBaseline()"):]
    baseline = baseline[:baseline.index("\n}\n")]
    assert "state.projectRun = {" in baseline and "setTimeout" in baseline
    assert "done()" not in baseline, \
        "the button is released when the request is accepted, not when the run ends"


def test_a_proposal_lands_where_the_question_was_asked():
    """It rendered at the foot of the page, below the gates and every panel --
    an answer three screens away from its question, arriving with nothing to say
    it had arrived."""
    app = app_js()
    screen = app[app.index("function projectScreen()"):]
    screen = screen[:screen.index("\nfunction driftNote(")]
    assert screen.count("resurveyBlock(p)") == 1, "the proposal is drawn twice, or not at all"
    # Above the checks it proposes changing. The anchor used to be the markup of
    # the gate list itself; that list is now built by `checksQuestion`, so the
    # test asks about the question rather than about one element inside it.
    assert screen.index("resurveyBlock(p)") < screen.index("checksQuestion(p)"), \
        "the proposal renders below the checks it proposes changing"
    assert screen.index("toolingDriftBlock(") < screen.index("resurveyBlock(p)"), \
        "the answer is drawn above the card that prompts it"

    block = app[app.index("function resurveyBlock(p) {"):]
    block = block[:block.index("\n/* ")] if "\n/* " in block else block
    assert 'class="eyebrow"' not in block, "the proposal still labels itself with a kicker"


def test_every_proposed_part_carries_its_own_verbs():
    """One change does not need a checkbox and a general-purpose "apply".

    The card *is* the change. It said "Apply what I checked" in a row below the
    cards, so leaving one unticked was a ruling nobody saw themselves make.
    Each part carries the two answers, and leaving it is recorded as such.
    """
    app = app_js()
    block = app[app.index("function partsBlock("):]
    block = block[:block.index("\n}\n")]
    assert "'Record it', 'Leave it'" in block and "'Use this environment'" in block
    assert 'data-accept="0"' in block
    assert "Apply what I checked" not in app, "the one Apply for a set is back"
    assert "esc(diff.unchanged)" not in app, \
        "the paragraph restating the gate list rendered below it is back"


def test_the_drift_card_and_the_proposal_are_never_both_on_screen():
    """They are the same fact twice.

    The drift card asks whether the gate list still describes the repository and
    names the files that moved; a proposal answers it and names the same files,
    with the same reasoning at greater length. Whichever is showing, the reader
    needs one of them.
    """
    app = app_js()
    screen = app[app.index("function projectScreen()"):]
    screen = screen[:screen.index("\nfunction driftNote(")]

    guard = screen[screen.index("state.resurvey"):screen.index("toolingDriftBlock(")]
    assert "? ''" in guard, "the drift card is drawn whatever the proposal says"
    assert "ruling" not in guard, (
        "the card comes back the moment you rule on the proposal -- telling a human the gate "
        "list may be stale in the same breath as they update it"
    )

    block = app[app.index("function resurveyBlock(p) {"):]
    block = block[:block.index("\nfunction ")]
    assert block.count("diff.summary") <= 2 and "diff.summary" not in app[
        app.index("function partsBlock("):app.index("function partsDone(")], \
        "the summary is said beside a card that carries its own reason"


def test_a_ruled_proposal_stops_asking():
    """After you accept it, it is a record, not a decision.

    It went on rendering the heading, the summary and the whole card with a dead
    checkbox in it — a page still arguing for a change you had already made,
    with the gate you added listed live twenty lines below.
    """
    app = app_js()
    block = app[app.index("function resurveyBlock(p) {"):]
    block = block[:block.index("\nfunction ")]

    ruled_branch = block[block.index("if (ruled) {"):]
    ruled_branch = ruled_branch[:ruled_branch.index("\n  }")]
    assert "rs-ruled" in ruled_branch and "return `" in ruled_branch, \
        "the ruled case still falls through to the full proposal"
    assert "then approve" in ruled_branch, \
        "accepting un-proves the check list and nothing says what to do next"
    # The early return has to come before anything that draws a card or a tick.
    assert block.index("if (ruled) {") < block.index("state.askedProposal")


def test_gate_zero_offers_one_reading_not_two():
    """"Survey from scratch" and "What should change?" asked the same model the
    same question. The only difference was what happened to the answer --
    `record_survey` replaces the gate list, `apply_survey_diff` appends to it --
    and that is an implementation detail promoted to a button, on a screen where
    the reader has no basis to choose between them.

    Worse, the safer path had the worse bookkeeping: only the replacing one
    stamped what it had read, so accepting a diff left "the tooling moved and
    nobody looked" standing forever.
    """
    app = app_js()
    screen = app[app.index("function projectScreen()"):]
    screen = screen[:screen.index("\nfunction driftNote(")]

    # The bar a project carries once it has a gate list at all.
    bar = screen[screen.index("  const actions = `"):]
    bar = bar[:bar.index("`;")]
    assert 'id="resurvey"' not in bar, \
        "the replacing survey is still offered beside the diffing one"
    assert 'id="propose-changes"' in bar and "Resurvey" in bar
    assert "Run checks" in screen and "Run the baseline" not in screen, \
        "the rail calls it the first run, the button called it the baseline"

    # It survives where a diffing survey has nothing to diff against. Scoped to
    # the branch rather than its first 600 characters: that offset was a proxy
    # for "in the action bar" and it failed for a comment being added above it,
    # which is a test measuring the wrong thing.
    failed = app[app.index("if (p.stage === 'failed')"):]
    failed = failed[:failed.index("\n  }\n")]
    assert 'id="resurvey"' in failed, "a failed survey offers no way to run another"

    # And paying for one is not the only way out. A reading that succeeded and a
    # baseline that did not are the same stage, and only one of them needs
    # paying for again: a survey read a repository correctly and the baseline
    # after it refused the environment over a false positive, leaving "Survey
    # from scratch" as the only button on screen.
    assert 'id="run-baseline"' in failed, (
        "a project whose reading survived can only recover by buying another one")
    assert "surveying again would pay a model to say the same thing" in failed, (
        "nothing tells a reader the reading survived")

    # And the words are said, ONCE. They used to be in the ruling block as well
    # as the lede, which is the provenance stated twice on one screen -- the
    # comment beside the ruling records the removal and why. Pinning the copy
    # itself made a deliberate edit look like a regression, so what is pinned
    # now is the property the edit was for: the lede carries it, the ruling
    # does not repeat it.
    lede = app[app.index("const SECTION_LEDE = {"):]
    lede = lede[:lede.index("\n};")]
    assert "by reading" in lede, "the lede no longer says where the checks came from"

    # Asserted on what renders, not on prose: the phrase appears in the comment
    # beside the ruling that records why it was taken out of the ruling.
    assert "<b>reading</b>" not in app and "<b>running</b>" not in app, \
        "the ruling block renders the provenance the lede already said"


def test_a_project_level_run_is_visible_until_it_ends():
    """It changes no stage and writes no record until it is over.

    "Run the checks" is accepted in milliseconds and works for half a minute.
    The toast fades in under four seconds, the POST has already returned, and
    the page then looks exactly like a page where nothing was pressed -- which
    is what a reader reports as "nothing is happening".
    """
    app = app_js()

    sub = app[app.index("function subscribe()"):]
    sub = sub[:sub.index("\n}\n")]
    assert "state.projectRun" in sub and "!event.feature_id" in sub, \
        "the progress stream carries project runs and nothing listens for them"
    assert "'running', 'started'" in sub and "null" in sub, \
        "the flag is set by the stream and never cleared by it"

    screen = app[app.index("function projectScreen()"):]
    screen = screen[:screen.index("\nfunction driftNote(")]
    assert "running-strip" in screen, "the page shows nothing while a run is in flight"
    assert "state.projectRun ? 'Running…'" in screen, \
        "the button that started it goes back to looking idle"

    css = stylesheet()
    assert ".running-strip" in css and "@keyframes spin" in css
    assert "prefers-reduced-motion" in css, "the one animated thing must honour the setting"


def test_the_recheck_prompt_distinguishes_the_two_kinds_of_absence():
    text = (ROOT / "factory" / "roles" / "recheck.md").read_text(encoding="utf-8")
    assert "only that exact wording carries that weight" in text, \
        "the role is still told that any unreadable path settles a finding"
    assert "not a file path" in text and "removed from" in text


def test_the_oracle_is_told_to_bring_its_own_fixtures():
    text = (ROOT / "factory" / "roles" / "oracle.md").read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "No *file* outside your directory is available to your tests" in flat
    assert "Write every fixture and helper your tests need" in flat, \
        "the oracle is not told to write the fixtures it needs"
    assert "Declare the `command` that runs your suite" in flat, \
        "the oracle is not told to declare how its suite runs"


def test_the_oracle_is_told_files_are_its_job_and_the_environment_is_not():
    """The two halves of the fixture rule, which collided and cost a run.

    "Write every fixture your tests need" and "assume nothing you were not
    given" are both right, and read together they are contradictory: you can
    write the file, and you cannot write the running system it connects to. An
    oracle resolved that by writing a fixture which *demanded* a live system be
    provisioned through an environment variable nobody set. Its whole suite
    errored in that assertion, on every round, and the packet reported the
    feature as the problem.

    So the rule about files has to say it is about files, and the prompt has to
    say plainly what the environment will be and that nothing else is coming.
    """
    text = (ROOT / "factory" / "roles" / "oracle.md").read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "The *running system* your tests talk to is a different thing" in flat, \
        "the fixture rule does not distinguish files it must write from the system it is given"
    assert "Never write one that **demands** an environment be arranged for you" in flat, \
        "nothing stops the oracle inventing a provisioning contract nobody agreed to"
    assert "the **environment** may not let you run it" in flat, (
        "`untestable_criteria` still reads as being only about an underspecified "
        "spec, so a criterion the environment cannot reach has nowhere honest to go"
    )
    assert "**Put it in `requires`**" in flat, \
        "the oracle is not told where an unmet environment capability goes"
    assert "A requirement you declare costs the run the criteria it names." in flat, (
        "nothing tells the oracle what declaring a requirement costs, which is the "
        "argument for declaring it rather than smuggling it into a fixture"
    )


def test_the_harness_prompt_does_not_promise_a_capability_the_harness_may_not_have():
    """The prompt must not tell a harness to do something it cannot do.

    The first version of this file said "You are inside a real checkout … if you
    need to see a migration chain, open it." That is true of an agentic harness
    and false of aider, which sees the files it was passed plus a *map* --
    signatures, not contents -- of everything else. So the migration question
    was not a model declining to look. It was a model that could not look,
    using the only mechanism it had, and the prompt written to prevent it was
    instructing it to do the impossible.

    Which is the same defect as everything else this session: a claim about the
    world asserted where it had not been checked. In a prompt it is worse than
    in code, because nothing fails -- the model simply cannot comply, and the
    turn ends the way it always did.
    """
    text = (ROOT / "factory" / "roles" / "harness.md").read_text(encoding="utf-8")
    flat = " ".join(text.split())

    assert "Nobody is going to answer you" in text
    assert "inside a real checkout" not in flat, \
        "the prompt tells every harness it can open any file; aider cannot"
    assert "depends on the tool you are running as" in flat, \
        "the prompt does not acknowledge that read access differs by harness"
    assert "requesting another file does not produce one" in flat, \
        "a harness with a fixed context is not told that asking cannot widen it"

    # The write boundary is still absolute -- that one is enforced by the
    # harness's file list and does not vary by tool.
    assert "a write boundary, not a read one" in flat

    # Matched against the flattened text: these are prose sentences and the file
    # is hard-wrapped, so a literal search over the raw bytes fails on where the
    # line happens to break -- which is the same class of bug as the gate tail.
    for phrase in ("choose the most reasonable option", "Partial work with a marked gap"):
        assert phrase in flat, f"the prompt does not say what to do instead of asking: {phrase!r}"


def test_the_harness_prompt_answers_the_question_it_names():
    """The migration example is only useful if the prompt says what to do there.

    The model asked for a value the brief had already given it. A prompt that
    quotes that failure and does not say "take the value you were given and mark
    it" is describing the problem rather than removing it.
    """
    flat = " ".join((ROOT / "factory" / "roles" / "harness.md")
                    .read_text(encoding="utf-8").split())
    assert "It asked for something it had been given" in flat, \
        "the example no longer says the answer was already in the brief"
    assert "take the value the brief gives you" in flat
    assert "one-line fix for the repair loop" in flat, \
        "the prompt does not say guessing wrong is cheaper than not guessing"


def test_the_harness_prompt_does_not_contradict_the_executor_it_runs_under():
    """It must not be `roles/worker.md`.

    The worker returns whole files as structured output; the harness edits a
    checkout in place. Handing a harness the worker's prompt would tell it to do
    the opposite of its job, which is a worse failure than the silence it
    replaces because the output would look like work.
    """
    harness = (ROOT / "factory" / "roles" / "harness.md").read_text(encoding="utf-8")
    worker = (ROOT / "factory" / "roles" / "worker.md").read_text(encoding="utf-8")
    assert "Write complete files" in worker
    assert "Write complete files" not in harness
    assert "the entire deliverable" in " ".join(harness.split())


def test_the_architect_cuts_by_behaviour_and_may_return_one_unit():
    """The cut is what decides whether a worker can check its own work.

    site-screening-61020a was cut into a data layer, an API layer and a
    frontend. That is clean file ownership and the worst available shape: layers
    are the axis along which code depends on code, so U-2 required three of
    U-1's function signatures, could not run a single test it wrote, and shipped
    assumptions instead. Three were wrong.

    The rule that prevents it has to survive edits, and so does its escape
    hatch: when independence and non-overlapping files collide -- which is
    often, because a behaviour reaches across the files layers separate -- the
    answer is fewer units, never a dependency. One unit has to read as a
    success or the architect will split anyway.
    """
    text = (ROOT / "factory" / "roles" / "architect.md").read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "Cut by behaviour, not by layer" in flat
    assert "No unit may depend on another unit's behaviour" in flat
    assert "One unit is a valid plan" in flat, "the fallback the other rules need"
    # The distinction that keeps a frontend/backend split legal: a seam the
    # frozen spec pins is shared reading, not a guess about another unit.
    assert "the same frozen document" in flat


def test_the_harness_prompt_asks_for_the_environment_to_be_used():
    """An environment nobody is told to use is one that goes unused.

    The units in site-screening-61020a had no way to run anything, and this
    prompt answered that with better instructions -- twice, in the same words,
    about the same migration. The environment is the fix; this is the half that
    makes an agent reach for it, and the half that keeps `run`'s note honest
    when there is none to reach for.
    """
    harness = (ROOT / "factory" / "roles" / "harness.md").read_text(encoding="utf-8")
    flat = " ".join(harness.split())
    assert "exercised" in flat, "the contract still ends at 'saved'"
    assert "no environment" in flat, "an agent without one is told nothing about it"


def test_both_builder_prompts_ask_for_unit_tests_and_deny_them_authority():
    """Two halves, and the second is what keeps the first from doing harm.

    Builders used to be told flatly not to write tests, for a reason that was
    correct: a test written against your own implementation anchors on what the
    code does and cannot verify the contract. What the rule missed is that unit
    tests were never claiming to. A run cost by that omission shipped a
    migration nothing had executed -- `py_compile` was the whole of its
    evidence -- and the packet had to say so.

    So both builder prompts now ask for unit tests, and both must still say
    plainly that the blind suite alone decides a criterion. Asking for tests
    without that sentence invites a builder to write its own acceptance suite,
    which is the failure the original rule existed to prevent.
    """
    for role in ("harness", "worker"):
        flat = " ".join((ROOT / "factory" / "roles" / f"{role}.md")
                        .read_text(encoding="utf-8").split())
        assert "unit tests for the" in flat, \
            f"{role}.md does not ask for unit tests on the code it writes"
        assert "not the verification" in flat or "not the verification," in flat, \
            f"{role}.md asks for tests without saying they verify nothing"
        assert "never seen your code" in flat, (
            f"{role}.md does not say who actually decides a criterion, so its "
            "tests read as coverage of the contract"
        )
        assert "does what your code does" in flat or "agrees with itself" in flat, (
            f"{role}.md does not say what a self-written acceptance suite is "
            "worth, which is the one thing that stops a builder writing one"
        )
        # The rule it replaces must be gone, not merely contradicted later on.
        assert "Do not write tests unless the brief asks for them" not in flat, \
            f"{role}.md still carries the old prohibition alongside the new rule"


def test_the_architect_gives_a_unit_somewhere_to_put_its_tests():
    """`files_expected` is a write boundary, so this is not advice.

    A worker told to unit-test its code, in a unit given no test path, has been
    told two contradictory things: it skips the tests or it writes into a file
    another unit owns. One real run gave two of three units a test file and the
    third none, and the unit that got none held the migration and the seed --
    which reached the packet with no test at any level.
    """
    flat = " ".join((ROOT / "factory" / "roles" / "architect.md")
                    .read_text(encoding="utf-8").split())
    assert "A unit that writes logic owns the test file for it" in flat, \
        "nothing tells the architect to allocate a test path to a unit"
    assert "`files_expected` is a write boundary" in flat, (
        "the architect is not told why this is a constraint rather than a "
        "preference, which is the only reason it cannot be skipped"
    )
    assert "not the acceptance suite" in flat, \
        "the architect could allocate test files believing they verify criteria"


def test_a_harness_that_commits_its_work_has_still_done_the_work(tmp_path):
    """This agent has a terminal, so it can commit. One that does leaves a clean
    `git status`, which every layer above reads as "produced nothing" -- and the
    unit is then nudged for work it has already finished, then recorded empty.

    aider was stopped from this with a flag that `factory.yaml` calls not
    optional. There is no flag here, so the commits are undone instead.
    """
    import subprocess

    d = _driver()
    repo = _tiny_repo(tmp_path)
    base = d.head(repo)

    (repo / "a.py").write_text("x = 2\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "the agent committed"], cwd=repo, check=True)
    assert d.changed_files(repo, {".git"}) == [], "the fixture does not reproduce the problem"

    assert d.uncommit_to(repo, base) is True
    assert d.changed_files(repo, {".git"}) == ["a.py"], \
        "work the agent committed is still invisible to the factory"
    assert d.head(repo) == base, "the branch was not returned to where the unit started"

    # And it is a no-op when the agent behaved.
    assert d.uncommit_to(repo, base) is False


def test_the_driver_does_not_count_an_empty_file_as_work(tmp_path):
    """Same rule the executor applies, applied where the nudge is decided.

    If they disagreed, a placeholder file would satisfy the driver's loop and
    then vanish from the executor's output -- a unit reported as built, arriving
    empty.
    """
    d = _driver()
    repo = _tiny_repo(tmp_path)

    (repo / "placeholder.py").touch()
    assert d.changed_files(repo, {".git"}) == [], "an empty file counted as an edit"

    (repo / "placeholder.py").write_text("# now it has something in it\n")
    assert d.changed_files(repo, {".git"}) == ["placeholder.py"]


def test_the_driver_ignores_the_task_file_and_the_harness_bookkeeping(tmp_path):
    d = _driver()
    repo = _tiny_repo(tmp_path)
    (repo / ".factory-task.md").write_text("the brief\n")
    (repo / ".openhands").mkdir()
    (repo / ".openhands" / "state.json").write_text("{}\n")

    assert d.changed_files(repo, {".git", ".openhands", ".factory-task.md"}) == [], \
        "the harness's own bookkeeping is being counted as the unit's work"


def test_the_driver_refuses_a_turn_that_wrote_nothing():
    """The mechanism, asserted on the source: the loop, and what it says."""
    text = (ROOT / "factory" / "harnesses" / "openhands_driver.py").read_text(encoding="utf-8")
    flat = " ".join(text.split())

    assert "while not changed_files(tree, ignore) and nudges < args.max_nudges" in flat, \
        "a turn that wrote nothing is accepted"
    assert "conversation.send_message(NUDGE)" in text, \
        "the refusal starts a new conversation instead of continuing this one"
    assert "You have not changed any file" in text
    assert "read by nobody" in flat, \
        "the agent is not told that the thing it is about to do reaches no one"


def test_adopting_a_suggestion_makes_a_complete_check_and_installs_its_tool(tmp_path):
    """A check adopted half-described cannot be held at a reading, grouped or
    guarded. And a tool the suggestion needs goes into setup -- pressing Adopt
    is the consent to change the environment, not the repository."""
    registry, project = _project_with_suggestions(tmp_path)
    project = registry.adopt_recommendation(project, "radon cc -n c src", "complexity")

    gate = next(g for g in project.state.gates if g.name == "complexity")
    assert gate.family == "quality"
    assert gate.parse_metric == r"(\d+) blocks"
    assert gate.config_files == ["setup.cfg#radon"]
    assert project.state.environment.setup == ["pip install -e .", "pip install radon==6.0.1"]
    assert project.state.baseline is None, "a new check was approved without being measured"


def test_the_console_offers_to_hold_a_check_on_new_code():
    app = app_js()
    server = factory_source("server")
    assert 'data-new-code="' in app and "/checks/new-code" in server
    assert 'data-limit="0"' in app, "the limit offered is not zero"


def test_holding_new_lines_to_coverage_is_a_floor_a_person_types(tmp_path):
    import pytest

    from factory.projects import ProjectError
    from factory.schemas import Gate

    gates_ = [Gate(name="coverage", command="cov {report}", report_format="cobertura")]
    registry, project = _project_with_suggestions(tmp_path, gates=gates_)
    project = registry.hold_new_code(project, "coverage", 80)
    held = next(g for g in project.state.gates if g.name == "coverage")
    assert held.patch_min == 80 and held.patch_max is None
    with pytest.raises(ProjectError, match="at most 100"):
        registry.hold_new_code(project, "coverage", 120)

    app = app_js()
    assert 'data-limit-from="' in app, "the coverage floor is not typed by a person"


def test_the_packet_has_a_dependencies_tab_that_every_route_reaches():
    """A tab nothing routes to is a tab that opens the wrong screen. Every list
    that names the packet's views names this one."""
    app = app_js()
    tabs = (ROOT / "console" / "ui" / "tabs.js").read_text(encoding="utf-8")
    index = (ROOT / "console" / "ui" / "index.js").read_text(encoding="utf-8")
    deps = (ROOT / "console" / "ui" / "dependencies.js").read_text(encoding="utf-8")
    assert "view: 'dependencies'" in tabs
    assert tabs.index("view: 'evidence'") < tabs.index("view: 'dependencies'") \
        < tabs.index("view: 'objections'"), "it is not beside Evidence, among the facts"
    assert "case 'dependencies':" in index
    for marker in ("const GATE2_VIEWS", "const PACKET_VIEWS"):
        listing = app.split(marker, 1)[1].split("];", 1)[0]
        assert "'dependencies'" in listing, f"{marker} does not know the tab"
    assert "dependencies: () => f('dependencies')" in app
    assert "params.dependencies ? 'dependencies'" in app
    # Three states that are not a blank.
    for words in ("Not read yet", "No dependencies changed", "Not checked: this project has no lockfile"):
        assert words in deps


def test_the_project_shows_what_it_depends_on_and_the_policy_it_holds_features_to():
    app = app_js()
    server = factory_source("server")
    # Shown with the reading it came from, on the Survey tab -- not as a check.
    survey = app.split("${removeProject(live)}", 1)[0].rsplit("factRow('still current?'", 1)[1]
    assert "dependencyInventory(p)" in survey, "the project's dependencies left the Survey tab"
    question = app.split("function checksQuestion(p)", 1)[1].split("\n}\n", 1)[0]
    assert "dependencyInventory(p)" not in question
    assert '"dependencies": dependencies.latest_inventory(project)' in server
    assert '("dependency_policy", body.dependency_policy)' in server, \
        "the policy can be shown but not changed"


def test_a_waiting_re_survey_is_said_on_the_checks_it_would_change():
    """The proposal is ruled on on the Survey tab. Said only there, a reading
    that proposed exactly the coverage checks a person had just added looked,
    from the Checks page they were on, as if it had found nothing."""
    app = app_js()
    question = app.split("function checksQuestion(p)", 1)[1].split("\n}\n", 1)[0]
    assert "proposalNotice()" in question
    row = app.split("function checkRow(", 1)[1].split("\n}\n", 1)[0]
    assert "proposedFor(g, r)" in row and "proposalTodo(pending)" in row


def test_each_proposed_check_is_ruled_on_by_itself(tmp_path):
    registry, project, _ = _project_with_proposal(tmp_path)
    project = registry.rule_on_proposed_check(project, "change:tests", accept=True)
    assert next(g for g in project.state.gates if g.name == "tests").command == "pytest --cov -q"
    assert project.state.baseline is None, "a changed check was not sent back to be measured"
    _, ruled = registry.proposal_state(project)
    assert set(ruled) == {"change:tests"}, "ruling on one check ruled on the others"
    assert [g.name for g in project.state.gates] == ["tests", "old"], "the others moved too"


def test_an_accepted_change_can_be_undone_and_waits_again(tmp_path):
    registry, project, _ = _project_with_proposal(tmp_path)
    project = registry.rule_on_proposed_check(project, "remove:old", accept=True)
    assert "old" not in [g.name for g in project.state.gates]
    project = registry.undo_proposed_check(project, "remove:old")
    assert "old" in [g.name for g in project.state.gates]
    _, ruled = registry.proposal_state(project)
    assert "remove:old" not in ruled


def test_the_reading_is_current_only_once_every_check_is_ruled_on(tmp_path):
    registry, project, _ = _project_with_proposal(tmp_path)
    project.state.survey_sha = ""
    project = registry.save(project)
    project = registry.rule_on_proposed_check(project, "change:tests", accept=True)
    project = registry.rule_on_proposed_check(project, "add:fmt", accept=False)
    assert not project.state.survey_sha, "the reading was called current with a check still waiting"
    project = registry.rule_on_proposed_check(project, "remove:old", accept=False)
    assert project.state.survey_sha, "every check ruled on, and the reading still not current"


def test_the_survey_tab_s_ruling_leaves_the_checks_waiting(tmp_path):
    registry, project, diff = _project_with_proposal(tmp_path)
    project, applied = registry.apply_survey_diff(project, diff, [], checks=False)
    _, ruled = registry.proposal_state(project)
    assert ruled == {}, "the rest of the reading ruled on the checks too"
    assert [g.name for g in project.state.gates] == ["tests", "old"]

    app = app_js()
    block = app.split("function resurveyBlock(p) {", 1)[1].split("\n}\n", 1)[0]
    assert "proposalCard(" not in block, "the Survey tab still draws what it does not rule on"
    assert "waitingChanges().length, 'checks'" in block and "partsWaiting('tests')" in block, \
        "the Survey tab does not say where the reading's changes wait"
    row = app.split("function checkRow(", 1)[1].split("\n}\n", 1)[0]
    assert "proposalTodo(pending)" in row, "a proposed change is not in the row it would change"


def test_a_check_with_no_result_is_not_told_to_get_its_command_to_pass():
    """Accepting a change to the list clears the run, and every check then had
    the red check's card -- "get this command to pass" -- over a check nobody
    had seen fail."""
    app = app_js()
    row = app.split("function checkRow(", 1)[1].split("\n}\n", 1)[0]
    assert "todo: green || !r ? '' : todoCard({" in row


def test_the_family_shows_the_rules_another_check_runs_for_it():
    app = app_js()
    groups = app.split("function familyGroups(", 1)[1].split("\n}\n", 1)[0]
    assert "alsoRow(g, a, by[g.name], p)" in groups and "${alsoLine}" in groups
    assert "alsoRow({ ...now, ...c.gate }, a, null, p, true)" in groups, "a proposed one is not shown"
    row = app.split("function alsoRow(", 1)[1].split("\n}\n", 1)[0]
    assert "qRow({" in row and "run by" in row
    todo = app.split("function proposalTodo(c) {", 1)[1].split("\n}\n", 1)[0]
    assert "Record what this check covers; the command stays as it is." in todo, \
        "a change that only describes a check asked to run the command it already runs"


def test_no_console_script_declares_the_same_top_level_name_twice():
    """A browser keeps the last of two function declarations and says nothing.
    A new `newCodeLine` for the checks screen shadowed the one the start page
    already had, and every check row read "nowhere proved to put a blind test"."""
    import re

    declared = re.compile(
        r"^(?:export )?(?:async )?(?:function\*? |const |let |var |class )([A-Za-z_$][\w$]*)", re.M)
    # The scripts under app/ share one global scope, so they are one text here:
    # a name declared in two of them is declared twice.
    texts = [("app/", app_js()), ("asbuilt.js", (ROOT / "console" / "asbuilt.js").read_text(encoding="utf-8")),
             *((path.name, path.read_text(encoding="utf-8"))
               for path in sorted((ROOT / "console" / "ui").glob("*.js")))]
    for name, text in texts:
        names = declared.findall(text)
        twice = sorted({n for n in names if names.count(n) > 1})
        assert not twice, f"{name} declares {twice} more than once"


def test_the_checks_screen_is_drawn_by_family_with_a_way_to_hold_and_to_go_without():
    app = app_js()
    server = factory_source("server")
    question = app[app.index("function checksQuestion"):]
    question = question[:question.index("\n}\n")]
    assert "familyGroups(" in question, "the checks are a flat list again"
    assert 'data-ratchet="' in app and "/checks/ratchet" in server
    assert 'data-decline-family="' in app and "/families/decline" in server
    assert 'data-reinstate-family="' in app and "/families/reinstate" in server


def test_the_oracle_is_told_what_it_may_import():
    """The failure this admitted a second parameter for.

    A blind suite opened its conftest with `import psycopg2`, which was not
    installed; nothing collected, and twelve criteria came back unverified. Not
    one of them was about psycopg2.
    """
    text = (ROOT / "factory" / "roles" / "oracle.md").read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "Import only from that list" in flat
    assert "fails to *load*" in flat, \
        "the prompt does not say why a missing import costs more than a failing test"
    assert "not the implementation" in flat, \
        "the oracle is not told the package list says nothing about the code"
    assert "Libraries this feature adds" in flat, \
        "the oracle is not told that feature-new libraries will also exist"


def test_the_generic_role_prompts_do_not_name_one_project_s_technology():
    """These prompts ship with the tool and run against every repository.

    An incident from one Python project put `psycopg2`, `alembic` and
    `down_revision` into three of them. Each was a true story and each made the
    prompt worse everywhere else: a role prompt that names a stack is telling
    every future project that this is what projects look like.

    `surveyor` and `resurvey` are exempt, and only they. Recognising tooling by
    name *is* their job -- the whole point of those two is to look at a
    repository and say "this runs pytest, so something must install pytest".
    """
    # Names that belong to one ecosystem, not to the idea of building software.
    branded = [
        "psycopg2", "alembic", "down_revision", "sqlalchemy", "fastapi", "django",
        "flask", "postgres", "postgresql", "mysql", "redis", "react", "vue",
        "tailwind", "webpack", "vite", "tsx", "conftest",
    ]
    exempt = {"surveyor", "resurvey"}

    offenders: list[str] = []
    for path in sorted((ROOT / "factory" / "roles").glob("*.md")):
        if path.stem in exempt:
            continue
        text = path.read_text(encoding="utf-8").lower()
        for name in branded:
            if name in text:
                offenders.append(f"{path.name}: {name}")
    assert not offenders, (
        "a generic role prompt names a specific technology: " + ", ".join(offenders)
    )


def test_the_crew_screen_is_served_what_it_draws():
    """`lanesSection` reads `state.roles`, which is `/api/roles`.

    The lanes went onto `/api/config` first, which is a different endpoint the
    crew screen never asks for -- so the section rendered nothing, silently,
    and looked exactly like a config with no lanes in it. A guarantee nobody
    can see is one nobody checks, which is the failure this was built against.
    """
    server_src = factory_source("server")
    listing = server_src[server_src.index('@app.get("/api/roles")'):]
    listing = listing[:listing.index('@app.get("/api/routes")')]
    for field in ('"fallbacks"', '"independence"'):
        assert field in listing, f"/api/roles does not serve {field}, and the crew screen reads it"

    app = app_js()
    assert "state.roles && state.roles.fallbacks" in app
    assert "${lanesSection()}" in app, "the section is defined and never drawn"


def test_the_oracle_is_told_the_files_are_the_delivery():
    """It writes its suite to disk now, so the prompt must not still argue from
    a ceiling. It used to say the whole suite came back in one answer -- which
    was true, and which killed three runs: 200,357 characters cut off
    mid-answer, the verify lane raised, and the build running alongside it had
    every result discarded.
    """
    text = (ROOT / "factory" / "roles" / "oracle.md").read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "**Write each test as a file and save it.**" in flat
    assert "does not exist" in flat, \
        "nothing tells the oracle that a suite living only in an answer is lost"
    assert "comes back inside a single answer" not in flat, \
        "the prompt still argues from a ceiling that no longer applies"


def test_the_oracle_is_told_its_directory_is_empty():
    """INV-1 is the filesystem now, and the prompt has to say so. An agent that
    believes there is a checkout somewhere spends turns looking for it."""
    flat = " ".join((ROOT / "factory" / "roles" / "oracle.md")
                    .read_text(encoding="utf-8").split())
    assert "**You are working in an empty directory.**" in flat
    assert "costs you a turn and finds nothing" in flat


def test_the_breaker_is_told_edits_to_the_code_are_discarded():
    """It works in a checkout of what it is attacking, which is new, and the
    hazard is new with it: a probe that only fails because the breaker also
    changed the source is a finding about a repository nobody has."""
    flat = " ".join((ROOT / "factory" / "roles" / "breaker.md")
                    .read_text(encoding="utf-8").split())
    assert "**Anything you write outside it is discarded unread**" in flat
    assert "you have not broken it" in flat, \
        "nothing tells the breaker that breaking it by editing it does not count"


def test_a_rerun_that_changes_nothing_keeps_the_project_approved():
    """Asserted on the source: running a real baseline needs a container."""
    src = inspect.getsource(
        __import__("factory.onboarding", fromlist=["x"]).ProjectOnboarding.run_baseline)
    assert 'project.state.stage = "ready" if keep else "awaiting_approval"' in src, \
        "the baseline still moves gate 0 unconditionally"
    assert "approved_for == self.registry.approval_fingerprint(project)" in src, \
        "nothing compares the new configuration against what was approved"
    # After record_baseline, which may set the resolved image: comparing before
    # would report a change this run itself had just made.
    record_at = src.index("record_baseline(project, baseline")
    compare_at = src.index("approved_for == self.registry.approval_fingerprint")
    assert record_at < compare_at


def test_approval_is_never_revoked_under_a_live_feature():
    """Those features are already building against this configuration.

    Un-approving underneath them describes a world that does not exist, and the
    console then hides the very run it is describing.
    """
    src = inspect.getsource(
        __import__("factory.onboarding", fromlist=["x"]).ProjectOnboarding.run_baseline)
    assert "live = self.registry.live_feature_ids(project)" in src
    assert "unchanged or bool(live)" in src, \
        "a live feature does not protect the approval it is building against"
    assert "feature(s) are building against it" in src, \
        "the ledger does not say why the approval was kept"


def test_the_resurvey_asks_for_the_load_command():
    """A field added after most readings were taken has to be asked for.

    The re-survey is a drift detector: it reports what moved in the repository.
    `collect` did not move in any repository -- it appeared here -- so nothing
    proposed it, and it came back empty with an empty reason. Empty and
    unexplained is indistinguishable from never asked, which is what makes the
    next reading pay to find out again.
    """
    text = (ROOT / "factory" / "roles" / "surveyor.md").read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "A rule carrying no `collect` is also a `change`" in flat
    assert "--collect-only" in flat and "vitest list" in flat
    # An empty answer has to be recorded as an answer, or it reads as a gap.
    assert "test_file_commands_reason" in flat


def test_the_console_reads_every_call_cost_through_the_billed_check():
    """Three places render what a call cost -- the ledger row's Cost column,
    the opened turn's own rows, and the call tree's caption -- and each one
    read `cost_usd` straight off the record. One of them keeping the raw read
    is the whole bug back in one column.

    So the field is readable in exactly two places: the pair of helpers that
    make the split. Everything else asks them. `recordCost` is a different
    field on a different object -- the tally `take_usage` writes, which was
    right all along -- and the rework summary's is the budget's own figure.
    """
    source = app_js()
    helpers = source[source.index("function callSpend("):source.index("const recordCost =")]
    elsewhere = source.replace(helpers, "")
    stray = [line.strip() for line in elsewhere.splitlines()
             if "cost_usd" in line
             and not line.strip().startswith(("*", "/*", "//"))
             and not re.search(r"\b(rw|rework|budget)\.cost_usd", line)
             and "(r.meta || {}).cost_usd" not in line]
    assert not stray, (
        "a call's cost is rendered from the record's own figure instead of "
        f"callSpend(): {stray}")
    assert source.count("callSpend(") >= 4, "the helper is declared and not read"


def test_the_model_picker_names_the_route_on_every_option():
    """A closed <select> shows only the selected option's text, and `opus-5`
    alone cannot say whether it is the copy metered against a plan or the one
    billed per token."""
    source = app_js()
    assert "} · via ${esc(r.label || r.name)}" in source
    assert "<optgroup" in source
    # And the configured value survives even when no route lists it.
    assert 'label="Configured"' in source


def test_the_crew_table_can_say_turns_instead_of_dollars():
    source = app_js()
    assert "function meterCell" in source
    assert "u.unbilled_calls" in source, \
        "a subscription row would show $0.00, which reads as free"


def test_the_picker_offers_the_route_s_own_default():
    source = app_js()
    assert "if (r.default_model_ok) models.unshift('');" in source
    assert "its own default model" in source


def test_a_check_is_not_expired_on_a_clock():
    """A sign-in lasts weeks. A lamp that went amber after ten minutes taught
    people to ignore the lamps; the age is shown and the judgement is theirs."""
    src = inspect.getsource(routes_mod.RouteMonitor.survey)
    assert "CHECK_TTL" not in src and "time.time() -" not in src, \
        "the survey still expires a check on a timer"
    assert "checkedAgo" in app_js()


def test_no_test_writes_into_the_running_console_s_route_store():
    """`paths.evidence` defaults to `.factory` in the working directory, which
    is the live store. A test that called `refresh` on a config built without an
    override wrote its stand-in route into that file and dropped every
    connection the human had checked -- a test suite deleting the user's state
    as a side effect of passing."""
    source = suite_source()
    body = source[source.index("def _route_cfg("):]
    body = body[:body.index("\ndef ", 1)]
    assert "cfg.paths.evidence" in body, \
        "route configs built for tests must not inherit the real evidence path"


def test_the_console_explains_a_budget_that_measured_nothing():
    source = app_js()
    assert "function unbilledNote" in source
    assert "which this budget cannot measure" in source


def test_the_console_shows_the_window_a_run_shares_with_its_human():
    source = app_js()
    assert "function windowBars" in source
    assert "resets in" in source
    # Per bar, not averaged: see the tightest-window test above.
    assert "pct >= 0.8" in source


def test_a_test_that_stubs_the_harness_clears_the_routes_that_would_override_it():
    """The shipped config routes every role, so a test setting
    `executor.command` and nothing else stubs a harness nobody launches and the
    real one runs instead. That turned this suite into one that spawned live
    agents and hung."""
    source = suite_source()
    assert "def _global_harness(" in source
    # Real assignments only -- a line mentioning the pattern inside a string is
    # this test describing itself, not a stub going around the helper.
    assignments = [
        line for line in source.splitlines()
        if line.strip().endswith(("= list(command)",))
        or (".executor.command = " in line and '"' not in line and "'" not in line)
    ]
    assert len(assignments) == 1, (
        "a harness stub bypasses the helper and will launch the real tool:\n"
        + "\n".join(assignments))


def test_a_failure_the_console_reports_is_distinguishable_and_stays():
    """Three separate presses were reported as doing nothing, and all three did
    say something -- in a dark pill at the foot of the window, indistinguishable
    from "Saved.", gone in under four seconds.

    A confirmation nobody reads costs nothing. A failure nobody reads costs the
    session it takes to work out what happened, so the two must not look alike
    and the failure must not clear itself. And the dispatch is wrapped rather
    than each branch guarded, because a handler that throws into an unhandled
    rejection is the case that looked exactly like an unwired button.
    """
    app = app_js()
    css = stylesheet()

    assert "function errorToast(" in app, "nothing in the console can report a failure as one"

    # Every catch that tells a human something must reach for the red one. A
    # failure narrated in the same voice as a success is the bug being pinned.
    for line in app.splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        if ".catch((e) => toast(" in stripped or "catch (e) { toast(" in stripped:
            raise AssertionError(f"a failure reported in the success voice: {stripped}")

    body = app.split("function toast(", 1)[1].split("\nfunction ", 1)[0]
    assert "kind !== 'error'" in body, (
        "every note clears itself on a timer -- an error that vanishes is an error nobody read")
    assert "toast-x" in body and "note.remove()" in body, "a note that stays cannot be dismissed"
    assert "appendChild(note)" in body, (
        "one note overwrites the last -- two failures in a row read as one")

    assert ".toast-error" in css, "a failure is styled the same as a confirmation"
    assert "var(--red)" in css.split(".toast-error", 1)[1][:220], "a failure is not red"

    # Position is the third complaint and the easiest to regress: the foot of
    # the window is as far from the pressed control as the viewport allows.
    stack = css.split(".toasts {", 1)[1].split("}", 1)[0]
    assert "--mast-h" in stack, "notes render away from the masthead, where nobody is looking"
    assert "bottom:" not in stack, "notes are pinned to the foot of the window again"

    # And the wrapper, without which a throwing branch is silent.
    assert "async function onMainClick(event) {\n  try {" in app, \
        "a handler that throws does it into an unhandled rejection, and the press looks unwired"
    assert "dispatchMainClick" in app, "the dispatch is no longer wrapped"

    # A press dropped by the busy guard is the other silent path: the overlay is
    # over the pane, so a reader scrolled away from it sees a dead page.
    guard = app.split("if (inFlight.has(busyScope()))", 1)
    assert len(guard) == 2, "the busy guard is gone, or is no longer scoped to one project"
    assert "toast(" in guard[1][:200], "the busy guard swallows presses silently"


def test_a_call_running_on_one_project_does_not_dim_another():
    """The overlay was one boolean for the whole console.

    Start a ten-minute re-survey on one project, switch to another, and that
    project's pane came up dimmed and locked with the first one's clock ticking
    over it -- and every button in it dead. The pane was the right unit and the
    scope was the wrong one: the reader most likely to look at a second project
    is exactly the one waiting ten minutes on the first.
    """
    app = app_js()

    assert "state.busy" not in app, (
        "a single global flag decides whether the console is busy -- it cannot answer "
        "'busy on which project'")
    assert "function busyScope()" in app, "nothing says which pane a call belongs to"

    # Keyed by project, and counted rather than flagged: two calls can be in
    # flight on different projects and the first to finish must not clear the
    # second.
    body = app.split("async function withBusy(", 1)[1].split("\nfunction ", 1)[0]
    assert "inFlight.set(scope" in body, "a call in flight is not recorded against its project"
    assert "n: call.n + 1" in body and "call.n -= 1" in body, (
        "in-flight calls are flagged, not counted -- the first to finish clears the rest")
    assert "paintBusy();" in body.split("finally", 1)[1], (
        "the overlay is not re-decided when a call ends")

    # Decided on every render, so navigating away hides it and navigating back
    # brings it up with the clock still counting from when the call began.
    paint = app.split("function paintBusy() {", 1)[1].split("\n}", 1)[0]
    assert "inFlight.get(busyScope())" in paint, (
        "the overlay does not ask which project the reader is looking at")
    assert "call.started" in paint, (
        "the clock restarts on navigation -- it must count from when the call began")
    assert "paintBusy();" in app.split("function render() {", 1)[1][:200], (
        "the overlay is not repainted on render, so a route change cannot clear it")


def test_a_proposal_shows_what_changed_and_not_what_it_restated():
    """A reader kept meeting decisions they had already made.

    `testing`, `test_file_commands` and `blind_placements` are replaced whole --
    a reading has no way to say "and the other two are fine" except by restating
    them, and a model asked to write a canary twice writes it differently the
    second time. So a re-read that added ONE browser placement drew three cards,
    two of them things already accepted with their comments reworded. The
    proposal's own prose said so ("restated only because the set is replaced
    whole"); the page believed the cards.

    What is recorded is right there to compare against.
    """
    app = app_js()

    assert "function classifyProposed(" in app, "the console renders replacement sets as changes"
    body = app.split("function classifyProposed(", 1)[1].split("\n}", 1)[0]
    assert "DIFF_IDENTITY" in body and "DIFF_PROSE" in body, (
        "a comparison with no notion of identity, or none of which fields are prose")

    # Every replacement set on the model must be compared, or the one that is
    # not is the one that comes back forever.
    for kind, recorded in [("test_file_commands", "p.test_file_commands"),
                           ("blind_placements", "p.blind_placements")]:
        assert f"classifyProposed(\n          '{kind}', diff.{kind}, {recorded})" in app, (
            f"`{kind}` is rendered whole rather than compared against what is recorded")
    assert "classifyProposed(\n        'tiers', diff.testing.tiers" in app, (
        "the testing surface is rendered whole")

    # A set with nothing new in it is not a change, and must not be counted as
    # one -- "5 changes proposed" stood over two cards already ruled on.
    assert ".filter((x) => x.key === 'testing' || x.key === 'scaffolding'" in app, (
        "a set of entirely restated items still counts toward the change count")
    # ...except `testing`, whose substance is not only its tiers.
    assert "if (!fresh.length && !dbMoved) return null;" in app, (
        "a reading whose whole point was a disposable database would be dropped as unchanged")

    # And the restated items are still named. Silence invites a reader to open
    # the card to find out whether their thing is in it.
    assert "function restatedLine(" in app, "restated items vanish with no account of them"


def test_the_checks_page_is_organised_by_the_two_things_a_build_must_prove():
    """It was organised by where each fact came from -- "1 from the repository",
    "2 from the factory", "3 the two together", "4 what follows". That is the
    shape of the code that produces the page, not a shape anyone reads with.

    And the fixes lived somewhere else entirely. A reading wrote out how to
    install the missing type checker, with the command, and the console rendered
    it on the survey tab -- three clicks from the row saying the type check
    cannot run. So every to-do now sits inside the row or the question it
    answers, routed by the `kind` the reading already assigns from a closed set.
    """
    app = app_js()
    screen = app.split("function projectScreen()", 1)[1]

    assert "sectionHead('checks', 'Checks'" in app, "the first question has no heading"
    assert "sectionHead('tests', 'Tests'" in app, "the second question has no heading"
    assert "${checksQuestion(p)}" in screen and "testsTab(p)" in screen, (
        "the two questions are not what this screen is made of")
    # Nothing on it that is not a row or a to-do inside a row.
    for gone in ("sec-title", "class=\"ruling "):
        assert gone not in screen.split("${standingBlocks(p)}", 1)[1].split("testsTab(p)", 1)[0], (
            f"{gone} is back between the counts and the foot")
    for gone in ("1 &mdash; from the repository", "2 &mdash; from the factory",
                 "3 &mdash; the two together", "4 &mdash; what follows"):
        assert gone not in screen, f"the old provenance spine is back: {gone}"

    lede = app.split("const SECTION_LEDE = {", 1)[1].split("checks: `", 1)[1].split("`,", 1)[0]
    assert "nothing which already worked is broken" in lede, (
        "the page never states the first question it exists to answer")
    assert "tested and correct" in lede, "the page never states the second"

    # A suggestion belongs to the question it answers, and gets there by the
    # reading's own enum -- never by the words in its title or a tool's name.
    assert "function todoSection(rec)" in app, "nothing routes a suggestion to a question"
    route = app.split("function todoSection(rec)", 1)[1].split("\n}\n", 1)[0]
    assert "kind" in route, "suggestions are routed by something other than their kind"
    # A suggestion that makes a check -- coverage -- sits with the checks.
    assert "report_format" in route, "a coverage suggestion is filed with the test runners"
    checks = app.split("function checksQuestion(p)", 1)[1].split("\nfunction ", 1)[0]
    tests = app.split("function testsTab(p)", 1)[1].split("\nfunction ", 1)[0]
    assert "todoSection(r) === 'checks'" in checks, (
        "a reading's suggestions about checks are still on another screen")
    assert "todoSection(r) === 'tests'" in tests, (
        "a reading's suggestions about tests are still on another screen")
    # And a suggestion is a row in the list, not a card floating after it --
    # on the Tests tab, inside the level it would help.
    # On the Checks tab the row sits in the family it would fill.
    row = app.split("function suggestionRow(", 1)[1].split("\nfunction ", 1)[0]
    groups = app.split("function familyGroups(", 1)[1].split("\nfunction ", 1)[0]
    assert ("familyGroups(" in checks and "qRow({" in row and "suggestionRow" in groups
            and "recsHere.map(recBlock)" in tests), (
        "a suggestion is drawn as a note about the page rather than a line in it")

    # Blocking and offered are two counts, because a red check stops a feature
    # being built and a missing harness does not.
    assert "function blockingItems(p)" in app and "function offeredItems(p)" in app, (
        "one vague number again, standing for two different kinds of thing")

    # And no button that forgets what it was told.
    assert "data-drop-rec" not in app, (
        "declining a suggestion is offered and not recorded, so the next reading writes it again")


def test_the_console_calls_nothing_it_does_not_define():
    """A deleted function takes its callers with it, and nothing said so.

    Consolidating four blocks into one removed `staleBaselineNote` along with
    them and left the call standing. The page rendered `Loading…` forever. The
    suite was green: every console test here reads the script as text, and text has
    no opinion about whether a name resolves.

    Worse, the test that should have caught the other one was passing on the
    definition. `assert "placementBlock(p)" in app` was satisfied by
    `function placementBlock(p) {` -- so it held for as long as the function
    existed and went on holding after the last call to it was deleted. It was
    checking that some code had been written, not that anyone would see it.

    Restricted to camelCase names with an interior capital, which is what an app
    function looks like and what English prose in a comment never does -- a
    looser pattern reads `check (this)` in a comment as a call, and a JS-aware
    parse is not worth carrying to catch a typo.

    A binding counts however it was written. `const { syntaxTree } = window.CM`
    defines `syntaxTree` exactly as much as `const syntaxTree = ...` does, and a
    check that only understood the second reported the first as a call into
    nothing -- which is the same false alarm, pointed the other way, as the bug
    this exists to catch.
    """
    import re
    # The plain scripts index.html loads share one set of globals, so a name
    # defined in one is defined for the others -- and a call in any of them has
    # to resolve in one of them.
    src = app_js() + "\n" + (ROOT / "console" / "asbuilt.js").read_text()
    called = set(re.findall(r"(?<![.\w$])([a-z][A-Za-z0-9$]*[A-Z][A-Za-z0-9$]*)\s*\(", src))
    defined = set(re.findall(r"(?:function\s+|(?:const|let|var)\s+)([A-Za-z_$][\w$]*)", src))
    # Destructured bindings, object and array alike. In `{ a: b }` the name that
    # enters scope is `b`, so a renamed key is read from the right of the colon.
    for pattern in (r"(?:const|let|var)\s*\{([^{}]*)\}\s*=", r"(?:const|let|var)\s*\[([^\[\]]*)\]\s*="):
        for binding in re.findall(pattern, src):
            for part in binding.split(","):
                name = part.split(":")[-1].split("=")[0].strip().lstrip(".")
                if re.fullmatch(r"[A-Za-z_$][\w$]*", name):
                    defined.add(name)
    for params in re.findall(r"\(([^()]*)\)\s*=>", src):
        defined |= set(re.findall(r"[A-Za-z_$][\w$]*", params))
    builtin = {"parseInt", "parseFloat", "encodeURIComponent", "decodeURIComponent",
               "setTimeout", "setInterval", "clearTimeout", "clearInterval", "isNaN",
               "requestAnimationFrame", "queueMicrotask", "structuredClone", "getComputedStyle"}
    missing = sorted(called - defined - builtin)
    assert not missing, (
        f"the console calls {missing}, which nothing defines -- the page will render "
        f"'Loading…' and stop, and every string-matching test here will still pass")


def test_a_page_does_not_rebuild_itself_for_work_on_another_project():
    """A build in one project polled every other project's page every four
    seconds -- and a build that died eight days ago, still recorded as
    `building` because nothing closes a run its process did not survive, did it
    forever. Each poll rebuilt `#main`, and each rebuild shut every section the
    reader had opened, which is what "the page keeps refreshing" is.

    Nothing is lost by narrowing it: the progress stream pushes every event from
    everywhere already, and the interval is the fallback for when that stream is
    not connected. Looking at no project in particular still means looking at
    all of them.
    """
    app = app_js()
    poll = app.split("function managePolling()", 1)[1].split("\n}", 1)[0]
    assert "const here = (f) =>" in poll, (
        "the poll asks the whole floor whether anything is running")
    assert "here(f) &&" in poll, "features in other projects still drive this page's poll rate"
    assert "!state.projectId || f.project_id === state.projectId" in poll, (
        "the yard, which is about every project, must not be narrowed too")


def test_what_a_reader_opened_survives_a_rebuild():
    """Most of the checks page is `<details>` now -- the two questions, the
    recommendations, the harnesses a reading asked for. An identical render is
    already skipped, but any real change rewrites the subtree and takes every
    open section with it, behind a reader who opened it on purpose.
    """
    app = app_js()
    # Anchored on the comparison rather than the whole line: the condition has
    # since grown a second clause (a repaint waits on an armed control), and
    # what this test is about is the body, not the guard.
    block = app.split("if (html !== lastMainHtml", 1)[1].split("lastMainHtml = html;", 1)[0]
    assert "wasOpen" in block, "a rebuild closes every section the reader opened"
    assert block.index("d.open") < block.index("main.innerHTML = html"), (
        "what was open is read after the subtree has already been replaced")
    assert "d.open = true" in block, "the open sections are recorded and never restored"


def test_a_project_opens_on_its_overview_and_checks_and_tests_are_tabs():
    """Checks and Tests were one long page answering two questions, with the
    testing half two screens down and an explainer drawing its three levels a
    second time at the top. Now an approved project opens on its overview --
    one line of status per area, and the features waiting on a person -- and
    Checks and Tests are tabs of their own, each marked when it needs you. A
    project still being set up opens there too, and says its checks are
    waiting to be accepted."""
    app = app_js()

    steps = app.split("const PROJECT_STEPS = [", 1)[1].split("];", 1)[0]
    order = [m for m in re.findall(r"id: '(\w+)'", steps)]
    assert order == ["overview", "checks", "tests", "guides", "survey", "environment", "history"], order
    section = app.split("function projectSection()", 1)[1].split("\n}", 1)[0]
    assert "SECTION_STEP[want] ? want : 'overview'" in section, "a project opens somewhere other than its overview"
    ov = app.split("function overviewSection(p) {", 1)[1].split("\n}\n", 1)[0]
    assert "accept its checks" in ov and "Waiting for you to accept them" in ov, \
        "a project being set up opens on an overview that never says its checks need accepting"
    assert "sectionDot(step.id, p)" in app and "st-dot amber" in app and "st-dot red" in app

    screen = app.split("function projectScreen() {", 1)[1]
    assert "${sec === 'overview' ? overviewSection(p) : ''}" in screen
    assert "${sec === 'checks' ? `${standingBlocks(p)}${checksQuestion(p)}` : ''}" in screen
    assert "${sec === 'tests' ? testsTab(p) : ''}" in screen
    assert "${sec === 'survey' ? resurveyBlock(p) : ''}" in screen, "a proposal left the survey tab"
    assert "verifyExplainer" not in app and "Build it</b> goes through the pipeline" not in app

    # And the name leads home only from a page that is not home.
    title = app.split('${boardHead(`<h1 class="ftitle"', 1)[1].split("`)}", 1)[0]
    assert "at ?" in title
    bar = app.split("function projectBar(p, opts = {}) {", 1)[1].split("\n}\n", 1)[0]
    assert "const at = state.view === 'gates' ? projectSection()" in bar


def test_a_suggestion_hands_over_a_prompt_rather_than_a_pipeline_run():
    """Seventeen phases to add `pip-audit` to a dev extra.

    The pipeline earns its cost when a diff cannot be trusted to show what a
    change does. These suggestions are the other kind -- a lockfile, an audit
    step, a coverage reporter, work whose whole promise is a command -- and one
    bundle of six cost 6.4 million tokens, of which the verify lane's share was
    a blind agent writing a test that greps a TOML file.

    So the card hands over what a session needs and gets out of the way. What it
    must also hand over is the doubt: `why`, `evidence` and `how` are a reading,
    not a measurement -- one said an audit command "needs nothing installed"
    when that command exits 1 on the tree it was read from.
    """
    app = app_js()
    assert "data-build-rec" not in app and "function startBuild(" not in app, (
        "a suggestion still offers to spend the whole pipeline on a command")
    assert "function recPrompt(" in app, "nothing assembles the handoff"

    body = app.split("function recPrompt(", 1)[1].split("\nfunction ", 1)[0]
    # The checks are the one part of that text that was measured rather than read.
    assert "gates" in body and "run them" in body, \
        "the prompt does not tell a session what has to still pass"
    assert "not a set of verified" in body, \
        "the prompt presents a reading as fact, which is how a session wastes an hour"
    assert "would_gate" in body, "the check this unlocks is not mentioned as the follow-up"

def test_the_whole_list_at_once_is_not_offered_as_one_build():
    """The batch argued cost and charged it to the reader.

    A button put every outstanding suggestion into one intent -- "one branch,
    one review, one baseline" -- and that is a real saving, of pipeline runs,
    which is this factory's cost and not the human's. What it cost them was the
    thing gate 1 is for. Six suggestions concatenated produced a spec of
    thirty-six acceptance criteria, against a spec writer prompt whose own stated
    scale is "something they can hold in their head in ten seconds"; and
    because it was one spec, the one item in it that could never go green --
    an audit of a tree with a live critical advisory -- could not be dropped
    without sending back the other five.

    A feature is a coherent unit everywhere else here: one worktree, one
    branch, one spec, one packet, one ruling. `join('\\n\\n')` over a to-do
    list is not one, and nothing checked that the items had anything to do with
    each other. The cards already carry the granularity -- each builds, adopts
    or declines on its own -- so nothing replaces it.
    """
    app = app_js()
    assert "buildAllOffered" not in app and "do-all-offered" not in app, (
        "the batch is back, and with it one ruling over things nothing has checked belong "
        "together")

    # What it was covering for is still there, per suggestion.
    assert "data-copy-rec" in app, "no way to take a suggestion away and do it"
    assert "data-adopt-rec" in app, "no way to take one as a check"
    assert "data-decline-rec" in app, "no way to turn one down"


def test_accepting_the_checks_does_not_take_the_checks_away():
    """An approved project rendered the composer at its own route, and the
    checks were reachable only through the `checks` link in the strip. Taking
    that link out -- because the checks are the landing page -- meant accepting
    them replaced the screen with a blank "describe the feature" box and left
    the list with no route at all. The one page a project is mostly about became
    unreachable by ruling on it.

    Starting a feature is an action with a button. `?start` is where it goes.
    """
    app = app_js()

    mounted = app.split("function mountedView()", 1)[1].split("\n}", 1)[0]
    assert "state.view === 'start'" in mounted, (
        "an approved project still hands its own route to the composer")
    assert "!state.view &&" not in mounted.split("if (!state.id)", 1)[1][:200], (
        "the composer is still the default screen of an approved project")

    assert "params.start ? 'start'" in app, "`?start` is not a route, so the button goes nowhere"
    assert '?start"\n           title="Opens the composer' in app, (
        "Start a feature does not point at the composer's own route")

    # Nothing seeds an intent any more: a suggestion hands over a prompt for a
    # session to run rather than routing into the composer with words in it, so
    # `?start` is reached by the button above and by nothing else.
    assert "window.__seedIntent" not in app, (
        "something still puts words in the composer on the way past, and the screen that "
        "reads them is no longer the only way in")


def test_reading_modes_button_cannot_push_the_page_sideways():
    """The gate-2 screens have no contents rail, so the reading-mode button sits
    outside the measure -- and it did that with `margin-right: -44px`.

    A negative margin walks out of the column whether or not there is anywhere
    to walk to. Between roughly 1240 and 1400 pixels of window with the rail in,
    the column is already as wide as the page allows and there is no shoulder to
    step into: the button went off the right-hand edge and took a horizontal
    scrollbar with it. A wider box cannot do that -- it collapses to the room
    that exists.
    """
    css = stylesheet()

    row = css.split(".wrap.doc-tools {", 1)[1].split("}", 1)[0]
    assert "max-width" in row, (
        "the gate-2 tools row is unbounded again; it must not exceed `main`")

    tools = css[css.index("/* ------------------------------------------------------------ reading mode"):]
    assert "margin-right: -" not in tools and "margin-left: -" not in tools, (
        "reading mode's button is pushed out by a negative margin again -- it "
        "will escape the page wherever the column has no shoulder to spare")


def test_a_dead_reading_is_recoverable_and_a_live_one_is_left_alone():
    """`is_orphaned` covers intake as well as building, and for a while the
    console detected a dead reading it then offered no way out of: the build
    screen had Resume, the intake screen spun, and `retry-build` refuses a
    feature with no spec to build. The way out of a dead reading is to read
    again -- and the same door must stay shut on a reading still in progress.
    """
    app = app_js()
    intake = app.split("function intakeRunningScreen()", 1)[1].split("\nfunction ", 1)[0]
    assert "d.orphaned" in intake, "a dead reading still says it is worth the wait"
    assert "reintake-stalled" in intake, "it says so and offers nothing to do about it"
    assert "retry-build" not in intake, (
        "a feature at intake has no spec, so resuming a build is not the way back")

    handler = app.split("if (target.id === 'reintake-stalled')", 1)[1][:220]
    assert "sendReintake()" in handler, "the button must reuse the one reintake path"
    assert "armed(" in handler, "and ask twice, like every other control that spends"

    guard = inspect.getsource(pipeline.Factory.rerun_intake)
    assert "owner_is_alive" in guard, (
        "nothing stops a second reading racing a live one, and both write the "
        "same scout slices")


def test_the_panel_on_the_way_out_can_send_the_loop_back_round():
    """A real run found a patient's bearer token being written into the audit
    table and returned by the endpoint the same change added. Five agents found
    it, the arbiter routed every one for repair -- and the loop had already
    decided to stop, so nothing was attempted. Four of five rounds and most of
    the budget were unspent. The packet carried the defect instead of the fix.
    """
    assert _reopen() == ["f1"], (
        "work in hand and road left: the loop has not converged")


def test_a_reopened_loop_still_stops_at_every_ceiling():
    """`_converge` promises that every stopping condition is a number in
    factory.yaml. Reopening must not become the exception to that."""
    assert _reopen(targets=[]) == [], "nothing to do is the one honest convergence"
    assert _reopen(round_index=5, max_rounds=5) == [], "the round cap is a number"
    assert _reopen(round_index=6, max_rounds=5) == []
    assert _reopen(affordable=False) == [], "the budget is a number"
    assert _reopen(out_of_time=True) == [], "the wall clock is a number"
    assert _reopen(enabled=False) == [], "rework off means rework off"
    assert _reopen(rate_limited=True) == [], (
        "a plan's window is this run's real ceiling; re-entering would spend the "
        "round on a route that is already refusing")


def test_a_unit_with_no_container_gets_no_session_in_one(tmp_path):
    """No container, no in-container session. What happens next is not a
    fallback to this machine: the host command is refused where it would be
    spawned (`test_no_agent_runs_on_this_machine`)."""
    import asyncio

    executor, _ = _executor(tmp_path)
    route = _container_route()
    prepared = _Prepared()
    prepared.vars = {}

    session = asyncio.run(executor._open_container_session(
        "worker", route, prepared, route.container_session_command,
        task_file=tmp_path / "t.md", spend_file=tmp_path / "spend.json"))
    assert session is None


def test_a_credential_never_reaches_the_command_line(tmp_path, monkeypatch):
    """Every process on the machine can read a command line. A token passed as
    `-e NAME=secret` is a token published to the machine, so these go through a
    file only this user can read."""
    import asyncio

    executor, _ = _executor(tmp_path)
    monkeypatch.setenv("TEST_HARNESS_TOKEN", "sk-not-a-real-token")
    route = _container_route(container_credentials=[
        {"kind": "env", "name": "TEST_HARNESS_TOKEN"},
    ])

    session = asyncio.run(executor._open_container_session(
        "worker", route, _Prepared(), route.container_session_command,
        task_file=tmp_path / "t.md", spend_file=tmp_path / "spend.json"))
    try:
        argv = " ".join(session.argv)
        assert "sk-not-a-real-token" not in argv
        assert "--env-file" in session.argv
        env_file = Path(session.argv[session.argv.index("--env-file") + 1])
        assert env_file.read_text().startswith("TEST_HARNESS_TOKEN=")
        assert oct(env_file.stat().st_mode)[-3:] == "600"
        assert oct(env_file.parent.stat().st_mode)[-3:] == "700"
    finally:
        directory = session.bundle.directory
        asyncio.run(session.close())
        assert not directory.exists(), "the copy on this machine does not outlive the run"


def test_the_criteria_list_is_read_with_titles_not_statements():
    """The schema says `title` is "the only part of the criterion most readers
    see" and `statement` is "what they open when they want to know exactly
    what was promised". The spec review rendered the statement in every row --
    file paths, index definitions, the test author's register -- and the title,
    written for a person, was read by nobody."""
    app = app_js()
    body = app[app.index("function criteriaByArea("):]
    body = body[:body.index("\nfunction ", 1)]
    assert "esc(c.title || c.statement)" in body, \
        "the row leads with the title, falling back for specs written before titles"
    assert "<b>Exactly.</b> ${esc(c.statement)}" in body, \
        "and the statement is still there, one click away"


def test_one_agent_owns_the_user_surface_and_it_is_not_the_integrator():
    """Two agents were writing browser tests for the same feature.

    The integrator was told that when the integration tier cannot reach a
    seam it should "go up, not sideways" and write at the user tier. It did:
    card-tags-8e21a7 has an integrator seam spec AND a blind oracle suite,
    both Playwright, both against the same interface. The seam spec sat red
    for the life of the feature, asserting a server rejection the control's
    own 32-character cap meant the server never saw -- and no agent in the
    repair loop could edit it.

    The surface has one owner, and it is the one that writes blind, before
    the code exists. The integrator stops below it and reports what it could
    not reach instead of climbing."""
    roles = ROOT / "factory" / "roles"
    integrator = (roles / "integrator.md").read_text()
    assert "subsurface: the API and below, and never the user interface" in integrator
    assert "do not go up" in integrator
    # The instruction that caused it must be gone, not merely balanced by a
    # later paragraph saying otherwise.
    assert "Write it at the user tier instead" not in integrator
    assert "`unresolved`" in integrator, "the seam it cannot reach has to be reported somewhere"

    oracle = (roles / "oracle.md").read_text()
    assert "The user surface is yours alone" in oracle
    assert "the API is that surface" in oracle, "a project with no UI still has a top tier"


def test_a_demonstration_is_not_re_run_after_the_round_that_wrote_it():
    """A probe that proves a defect is present *now* is worth one round.

    Once the defect is gone the construction has nothing left to say: it can
    hang, error, or reward some different defect, and any of the three is a
    fact about the probe rather than about the code. Re-running one is not
    free either -- two of them cost card-tags-fee892 1801s in round 1 and 1801
    again in round 2, on a run capped at two rounds, which is why every
    finding still standing at the end read "the loop stopped first".

    Retired, not forgotten. The probes come forward so their hypotheses stay
    available to `breaker_findings` and the findings they raised keep their own
    records; what stops is the running.
    """
    demo, keeper = _probe("t/d_test.py", kind="demonstration"), _probe("t/r_test.py")

    def split(accumulated, fresh):
        """The two lists `_run_breaker` computes: what runs, and what comes forward."""
        by_path = {t.path: t for t in accumulated if t.kind == "regression"}
        for t in fresh:
            by_path[t.path] = t
        running = list(by_path.values())
        retired = [t for t in accumulated
                   if t.kind != "regression" and t.path not in by_path]
        return [t.path for t in running], [t.path for t in retired], running + retired

    # Round 0 wrote both, so round 0 ran both: a demonstration runs in the
    # round that writes it or it is nothing at all.
    running, retired, carried = split([], [demo, keeper])
    assert sorted(running) == ["t/d_test.py", "t/r_test.py"]
    assert retired == []

    # Round 1 inherits both and runs only the one with something left to say.
    running, retired, carried = split(carried, [])
    assert running == ["t/r_test.py"]
    assert retired == ["t/d_test.py"]
    # Carried forward regardless, so the hypothesis behind the finding round 0
    # raised is still there to be read.
    assert sorted(t.path for t in carried) == ["t/d_test.py", "t/r_test.py"]

    # And it stays retired rather than coming back next round.
    running, retired, _ = split(carried, [])
    assert running == ["t/r_test.py"]
    assert retired == ["t/d_test.py"]


def test_the_console_has_a_state_for_a_probe_that_reported_nothing():
    """Two states could not say it. "Failing -- this is the signal" is a claim
    about the code and "passing -- which proves nothing" is a claim about the
    probe having run; a probe that was killed supports neither, and showing it
    as either is the same error the pipeline was making."""
    ui = (ROOT / "console" / "ui" / "evidence.js").read_text(encoding="utf-8")
    assert "never finished — reports nothing either way" in ui
    assert "breaker.timed_out" in ui and "breaker.quarantined" in ui
    # Amber rather than red: nothing here stands against the change, but
    # something meant to measure it did not report.
    css = stylesheet()
    assert ".ee-probe.stuck" in css and "var(--amber)" in css


def test_the_console_opens_a_probe_onto_its_own_source():
    """It had the source the whole time -- it arrives with `breaker_suite` --
    and drew a path and a mark. The blind tests beside this band have rendered
    theirs since they were written.

    Whole, not the first lines. A blind test links out to its criterion's full
    evidence; a probe has nowhere to link to, because it is not on the branch
    and will not be, so a truncation here is a reader who cannot finish the
    argument.
    """
    ui = (ROOT / "console" / "ui" / "evidence.js").read_text(encoding="utf-8")
    assert "function ProbeSource(" in ui
    assert "probeSource[t.path] = t.contents" in ui, "keyed by path, as the report names them"
    assert "<summary class=\"ee-probe-h\">" in ui, "the row itself opens"
    assert ".slice(0, 24)" not in ui.split("function ProbeSource(")[1].split("\n}")[0], (
        "a probe's source is not truncated on screen -- there is nowhere to link on to")
    # A probe with no recorded source still renders as a plain row rather than
    # an empty disclosure that opens onto nothing.
    assert "if (!src) {" in ui

    css = stylesheet()
    assert "details.ee-probe > summary" in css
    assert "details.ee-probe > summary::-webkit-details-marker { display: none; }" in css, (
        "a row that is obviously clickable does not need a glyph saying so")


def test_no_screen_or_document_calls_a_restatement_an_independent_finding():
    """The packet said "Independently found by 2 more" over the restatements of
    one agent, and the arbiter's brief taught the same thing in as many words.
    A reader counting entries would have counted them."""
    brief = (ROOT / "factory" / "roles" / "arbiter.md").read_text(encoding="utf-8")
    assert "independent passes converging on it is exactly the corroboration" not in brief, (
        "the brief was instructing the model to treat repeat samples as evidence")
    assert "Agreement counts when it crosses agents, and not otherwise." in brief
    assert "never write a count of restatements into your reason" in brief

    ui = (ROOT / "console" / "ui" / "findings.js").read_text(encoding="utf-8")
    assert "Independently found by ${restatements.length} more" not in ui
    assert "Independently found by ${crossAgent.length} other" in ui
    assert "by the same agent" in ui
    # The count on the collapsed row is the half that is evidence.
    assert "+${crossAgent.length} agreed" in ui
    # And the screen reads the ledger's own answer rather than deciding again.
    # Two answers to one question drift, and the record is the one the arbiter
    # saw and the packet document prints.
    assert "new Set(rec.restated_by || [])" in ui
    assert "agentOf(r) === mine" not in ui

    doc = factory_source("packet_doc")
    assert "Also restated by the same agent:" in doc


def test_the_repairs_screen_is_a_list_of_rounds():
    """It was five sections with five axes, and the one thing it did not show
    was what a round had done. Everything that belongs on it is an attribute of
    a round, so the round is the only axis and it opens onto the units it
    dispatched.
    """
    repairs = (ROOT / "console" / "ui" / "repairs.js").read_text(encoding="utf-8")
    assert "function Round(" in repairs and "function Unit(" in repairs
    assert "rework.units" in repairs, "a round opens onto what it actually changed"
    # The round that did not happen is the last row, not a paragraph above the
    # list: a loop that converged and one that ran out mean opposite things.
    assert "function Stopped(" in repairs and "No round ${(rework.rounds || 0) + 1}" in repairs

    # And the three things that left.
    assert "onDispatch" not in repairs, (
        "the way out of the packet is on Your calls, which was built to hold it")
    assert "<${Rework}" not in repairs and "flags=" not in repairs
    assert "attempted_not_fixed" not in repairs, (
        "counting findings by outcome is what the Objections bands do, and counting records "
        "there while the round strip counted findings is how one screen carried two answers")

    index = (ROOT / "console" / "ui" / "index.js").read_text(encoding="utf-8")
    assert "<${Repairs} data=${initial.data} busy=${busy} onRule=${onRule} />" in index


def test_a_round_that_recorded_no_units_still_says_what_it_closed():
    """Every packet built before the rounds recorded their units has none, and
    a row reading "0 findings closed" over a round that closed four would be
    worse than what it replaced. The ledger still knows which findings a round
    repaired."""
    repairs = (ROOT / "console" / "ui" / "repairs.js").read_text(encoding="utf-8")
    assert "const known = units.length > 0" in repairs
    assert "repaired.length" in repairs
    assert "this round did not record its units" in repairs, (
        "and it says which of the two readings it is giving")
    assert "repaired_in_round === n" in repairs


def test_naming_a_recording_for_a_criterion_is_not_a_claim_about_the_frames():
    """The check knows a test *said* it was about a criterion. It cannot know
    what the test showed, and it must not be read as though it could.

    With screenshots this went wrong exactly: the repair loop satisfied the
    check with one file per criterion, correctly named, and two of the five
    showed something other than the criterion's leading claim. A recording
    shows everything the test did, which narrows the gap without closing it --
    a test titled for AC-13 that never types a disallowed character is a
    recording of AC-13 that does not show it.
    """
    ui = (ROOT / "console" / "ui" / "evidence.js").read_text(encoding="utf-8")
    assert "which criterion a test was <i>for</i>" in ui, (
        "for, not of -- the band must not claim the recording shows the criterion")
    assert "Nothing checks that the frame shows it" in ui
    assert "than one state \u2014 so read the criterion, then look" in ui

    src = inspect.getsource(pipeline.check_recording_coverage)
    assert "never what the test showed" in src
    assert "driven all the way" in src

    # What is asked for is a test driven to the state, not a title.
    oracle = (ROOT / "factory" / "roles" / "oracle.md").read_text(encoding="utf-8")
    assert "Drive it to the state the criterion describes." in oracle
    assert "are two tests, both titled for the criterion" in oracle, (
        "states that cannot happen in one run each get their own recording")


def test_the_recordings_band_says_what_it_cannot_do():
    """Recordings are the whole of the On screen band now, drawn as thumbnails
    a reader opens in place, with the archive a click away for the viewer that
    shows the page's own structure."""
    ui = (ROOT / "console" / "ui" / "evidence.js").read_text(encoding="utf-8")
    assert "traceUrl" in ui and "download" in ui
    onscreen = ui[ui.index('title="On screen"'):ui.index('title="The project\'s gates"')]
    assert "recordings.map((t) =>" in onscreen
    assert "SHOT_GROUPS" not in ui and "/screens/" not in ui, "no pictures, and no route to one"
    # Absent is said, and said as an absence rather than a pass.
    assert "No browser test was recorded on this run." in onscreen
    # The surveyor is what makes any of it arrive, because the runner's config
    # is the project's and not this tool's to change.
    surveyor = (ROOT / "factory" / "roles" / "surveyor.md").read_text(encoding="utf-8")
    assert "Say where the browser runner leaves a recording" in surveyor
    assert "A recording smaller than the page counts as half on." in surveyor
    assert "Do not name a directory the runner does not actually write to" in surveyor


def test_a_proposal_card_a_reader_cannot_see_is_never_offered():
    """`extras` drops a card whose `body` is empty, and that rule is right: a
    tick under a button that applies something, with nothing visible to read,
    is how a person agrees to what they could not see.

    It is also easy to fall into from the other side. A `trace_dirs` card was
    built with a delta, a diff and the reading's own reason, and no body -- so
    it was constructed and silently filtered, and a re-survey that proposed the
    directory rendered as a screen proposing nothing. The reading was paid for,
    the answer was in the API, and the only thing missing was the part a person
    looks at.
    """
    app = app_js()
    extras = app[app.index("const extras = ["):]
    extras = extras[:extras.index("].filter(Boolean)")]

    # Every card in `extras` either carries a body or is one of the two keys
    # whose rendering supplies its own.
    keys = re.findall(r"key: '([a-z_]+)'", extras)
    assert "trace_dirs" in keys, "the card is built"
    for key in keys:
        if key in {"testing", "scaffolding"}:
            continue
        chunk = extras[extras.index(f"key: '{key}'"):]
        end = chunk.find("key: '", 6)
        chunk = chunk[:end] if end > 0 else chunk
        assert "body:" in chunk, (
            f"the {key} card has no body, so the filter below drops it and a reading that "
            "proposed it renders as a screen proposing nothing")

    # And the filter itself stays, because the rule it enforces is the point.
    assert "(x.body || '').trim() !== ''" in app


def test_the_console_does_not_keep_a_screen_for_the_merged_away_security_agent():
    """The merge above left the console holding a Security tab, and the tab
    could not be reached.

    It sliced findings on whether `category` contained the word "security",
    while the reviewer -- now the only prose reader of the angle -- is told to
    categorise concretely (`authz`, `exposure`, `data-loss`, ...). So the tab
    drew a zero on every run, the findings it existed for were filed under
    Objections all along, and the concessions block on it filtered for a role
    the roster no longer has, which is a section that cannot ever fill.

    A deleted agent is not finished being deleted while a screen still divides
    the record by where it used to sit. What is asserted here is that the split
    is gone and that the one remaining screen says out loud that it carries
    security, because a reader who learnt the old tab will come looking.
    """
    ui = ROOT / "console" / "ui"

    # The predicate the split was keyed on, and every route into the screen.
    assert "isSecurity" not in (ui / "shared.js").read_text(encoding="utf-8")
    for name in ("findings.js", "tabs.js", "index.js"):
        assert "isSecurity" not in (ui / name).read_text(encoding="utf-8"), name

    # Not a bare search for the word: `security` is also a call tag, which is a
    # different thing and still real. What must be gone is the route.
    app = app_js()
    assert "f('security')" not in app, "no href may still build a link to the screen"
    assert "'objections', 'security'" not in app, "no view list may still mount it"
    # Kept deliberately: an old link has somewhere to land rather than nowhere.
    assert "params.security ? 'objections'" in app

    index = (ui / "index.js").read_text(encoding="utf-8")
    assert 'kind="security"' not in index
    assert "case 'security'" not in index

    tabs = (ui / "tabs.js").read_text(encoding="utf-8")
    assert "view: 'security'" not in tabs
    assert "label: 'Security'" not in tabs

    # No code path may branch on a role that cannot appear in a run. Historical
    # prose in the comments is fine -- a string literal is not.
    for name in ("findings.js", "tabs.js", "shared.js", "index.js"):
        assert "'hacker'" not in (ui / name).read_text(encoding="utf-8"), name
    assert "wl-role-hacker" not in stylesheet()

    # And the screen that absorbed it says so, in one clause.
    findings = (ui / "findings.js").read_text(encoding="utf-8")
    prose = " ".join(findings.split())
    assert "security included" in prose


def test_the_phase_list_reads_in_the_order_the_phases_run():
    """A person reads a list top to bottom as "this, then this". The breaker was
    drawn after attribution and qa, which it runs before -- it runs inside the
    gates' environment while its services are up -- so a human watching it run
    beneath two rows marked "not reached" asked why they had been skipped."""

    names = pipeline.PHASE_NAMES
    assert names.index("gates") < names.index("breaker") < names.index("attribution") \
        < names.index("qa")

    # Served in that order whatever a feature stored, so one created before the
    # correction reads the same way.
    assert "PHASE_NAMES.index(p.name)" in factory_source("server")

    # And a station the line has not reached yet is "waiting" while the line
    # runs; "not reached" is kept for a line that stopped.
    app = app_js()
    assert "const pendingWord = (d) => (lineRunning(d) ? 'waiting' : 'not reached');" in app
    assert "return pendingWord(d);" in app
    assert "${pendingWord(d) === 'waiting' ? 'Still to come' : 'Not reached'}" in app


def test_the_feature_log_carries_step_records_in_full(tmp_path):
    """The call tree draws every run of every step, repair rounds included, from
    step records' payloads. The log sent them without one, so the tree fell back
    to the phase list -- one row per station, the latest run only -- and round 1
    vanished from the screen while it sat in the ledger."""
    assert "step" in server.INLINE_LOG_KINDS
    app = app_js()
    runs = app[app.index("function stepRuns("):app.index("function callsIn(")]
    assert "r.kind === 'step' && r.payload" in runs, "the console reads step payloads from the log"


def test_a_station_that_bought_no_model_call_can_still_be_opened():
    """The gates ran nineteen commands and the QA station settled fourteen
    criteria, neither of them through a model. Both rows had a call count of
    nothing, so neither could be opened, so neither said what it had done."""
    app = app_js()
    graph = app[app.index("function runGraph("):app.index("function runningScreen(")]
    assert "r.calls.length || (r.readout || []).length" in graph, \
        "a turn opens on its work, not only on what it spent"
    assert "readoutBlock(r.readout, ledger)" in graph
    steps = app[app.index("function stepRuns("):app.index("function callsIn(")]
    assert "readout: r.readout" in steps, "the console reads readouts from the log"



def test_the_run_is_drawn_as_every_turn_with_its_branches_and_rounds():
    """The step list kept one row per step, so a second round's gates erased
    the first's and repair rounds could not be seen at all. Every run of a step
    is now recorded, and the console draws the run from those records: one row
    per agent turn, parallel units and panel agents as branches that rejoin,
    the oracle on its own line, and each repair round as its own band."""
    src = inspect.getsource(pipeline.Factory._phase)
    assert 'store.append("step"' in src and '"round": state.rework_round' in src
    assert src.count("ran()") >= 2, "a failed step is recorded as well as a finished one"

    from factory.llm import call_entry
    assert "unit" in call_entry(role="worker", route="r", asked="m", outcome="answered",
                                started=0.0, unit="U-1")

    app = app_js()
    assert "kind === 'step'" in app, "the graph reads every run of a step"
    assert "`Round ${s.round}`" in app, "a repair round is its own band"
    assert "STEP_FANOUT = { workers: 'unit', repairers: 'unit', review: 'role' }" in app
    assert "${runGraph(d, scoped, calls" in app, "the running screen draws the graph"
    # The note under the graph indents to the graph's second column, and only
    # the graph knows how wide that is -- so the graph emits the note and hands
    # it the same width. It used to be a sibling guessing 60px.
    assert 'class="rg-next" style="${gw}"' in app, "the note is given the graph's width"
    assert "step: 'a step ran'" in app


def test_the_console_files_a_finding_that_no_longer_holds_with_the_settled_ones():
    app = app_js()
    shared = (ROOT / "console" / "ui" / "shared.js").read_text(encoding="utf-8")
    tabs = (ROOT / "console" / "ui" / "tabs.js").read_text(encoding="utf-8")
    assert "outcomes: ['repaired', 'no_longer_holds']" in app
    assert "has: ['repaired', 'no_longer_holds']" in shared
    assert "no_longer_holds: ['no longer holds'" in shared
    assert "'no_longer_holds'" in tabs, "a closed blocker does not light the tab"
    waiting = shared[shared.index("export function awaitingRuling"):]
    waiting = waiting[:waiting.index("}\n")]
    assert "no_longer_holds" not in waiting, "and it is never a call to make"


def test_the_route_box_does_not_say_accept_while_calls_are_open():
    """Ten calls open, none filed, and the box under them read "Nothing left to
    fix. Nothing flagged. Accepting takes the branch as it stands." -- the
    route computed from no flags at all."""
    rework = (ROOT / "console" / "ui" / "rework.js").read_text(encoding="utf-8")
    worklist = (ROOT / "console" / "ui" / "worklist.js").read_text(encoding="utf-8")
    route = rework[rework.index("export function Route("):]
    assert "pending = 0" in route and "const waiting = pending > 0;" in route
    assert "waiting ? ROUTE.waiting" in route, "the waiting shape wins over any computed route"
    assert "disabled=${busy || waiting" in route, "and nothing can be dispatched from it"
    assert "title: 'Waiting on your calls'" in rework
    exit_block = worklist[worklist.index("wl-exit"):]
    assert "pending=${left}" in exit_block


def test_the_call_tree_shows_one_run_and_names_the_others():
    """Five passes over one feature drew as one tree, rounds 2, 0, 1, 2, 0,
    because only a rebuild started a new run."""
    got = _run_console_js(
        f"const log = {json.dumps(_LEDGER)};\n"
        "const runs = runsOf(log).map((r) => [r.seq, r.label]);\n"
        "const latest = attemptScope(log).map((r) => r.seq);\n"
        "state.runSeq = 7;\n"
        "const earlier = attemptScope(log).map((r) => r.seq);\n"
        "console.log(JSON.stringify({ runs, latest, earlier }));")
    assert got["runs"] == [[0, "Build"], [7, "Verify again"], [12, "Repair"]], \
        "the loop reopening itself mid-run is not a run; a person sending it back is"
    assert got["latest"] == [13], "the latest run by default, and only its own records"
    assert got["earlier"] == [8, 9, 10, 11], "an earlier run, whole, one link away"


def test_what_a_run_did_not_run_itself_is_drawn_as_the_earlier_run_s():
    """A "Verify again" reuses the build. Its reused steps were drawn as work of
    that run, in a "Round 2" band it had no part in."""
    got = _run_console_js(
        f"const log = {json.dumps(_LEDGER)};\n"
        "state.runSeq = 7;\n"
        "const d = { phases: [], state: { rework_round: 0 } };\n"
        "const steps = stepRuns(d, attemptScope(log), log);\n"
        "console.log(JSON.stringify(steps.map((s) => ({ name: s.name, carried: !!s.carried,"
        " from: s.from || '', round: s.round, detail: s.detail || '' }))));")
    carried = [s for s in got if s["carried"]]
    assert [s["name"] for s in carried] == ["scout", "architect", "workers", "integrator", "oracle"]
    assert all(s["from"].startswith("Build · ") and s["round"] == 0 for s in carried), \
        "from the run that produced it, and in no round"
    assert next(s for s in carried if s["name"] == "workers")["detail"] == "2 unit(s)"
    own = [s for s in got if not s["carried"]]
    assert [s["name"] for s in own] == ["gates", "gates"], "the reused step is not drawn as this run's"

    app = app_js()
    assert "not run this time · from ${r.from}" in app, "said in words, not only in grey"
    assert "const mine = s.carried ? [] : callsIn(s, calls);" in app
    assert "${runLinks(state.log)}" in app


def test_the_project_page_says_when_each_check_runs_and_what_runs_each_level():
    """A level marked ready read as covered while nothing ran the project's own
    tests there, and nothing said when a check ran. Each check now shows its
    kind with a way to change it, and each level names the checks that run the
    tests it already has -- or says that none does."""
    app = app_js()
    line = app[app.index("function gateLine("):app.index("function factRow(")]
    assert "'fixer · runs first'" in line and "'runs once, at the end'" in line
    runs = app[app.index("function runsLine("):app.index("function gateLine(")]
    assert 'data-check-runs="${esc(gate.name)}"' in runs
    assert "Let its timing decide" in runs
    assert line.count("runsLine(") == 1 and app.count("runsLine(g, p)") == 1, \
        "both check rows say it the same way"
    assert "/checks/runs`" in app
    assert "your tests here run in ${runBy}" in app, \
        "a level no longer names the checks that run the tests it already has"
    assert "no check runs the tests already here" in app, \
        "a level whose tests nothing runs must say so, or ready reads as covered"
    assert "Then CI and this page agree" not in app
    src = factory_source("server")
    assert '"check_kinds": project.check_kinds(cfg.pipeline.heavy_check_after_s)' in src


def test_both_survey_actions_ask_first_and_say_what_they_change():
    """Resurvey calls a model for several minutes and proposes; surveying from
    scratch replaces the reading and asks for approval again. Neither starts
    on one press, and each dialog says which of the two it is."""
    app = app_js()
    assert "if (target.id === 'propose-changes') return askProposeChanges();" in app
    assert "if (target.id === 'resurvey') return askResurvey();" in app
    ask = app[app.index("async function askResurvey()"):app.index("function resurvey() {")]
    assert "replaces the current reading" in ask and "approve the project again" in ask
    assert "Nothing changes until you accept each proposal" in ask
    assert ask.count("confirmDialog(") == 2


def test_a_colour_is_named_before_it_is_used():
    """The console is one scheme with two grounds, and it could not be two.

    Not for want of a palette -- for want of names. Two hundred and twenty-five
    colour literals sat in rules rather than in tokens, and every one of them
    was a place where a second scheme had to be told something a token would
    have told it for free. Worse, the same literal meant three different
    things: `#fff` was paper lifted off the floor, the board's brightest
    lettering, and lettering reversed out of a lamp, and no amount of
    find-and-replace can tell those apart.

    So: a colour is named in `:root` before it is used, and the name says what
    the colour is FOR. A literal below the token block is a role nobody wrote
    down, and the next scheme pays for it.

    This guards hex only. Around fifty `rgba()` literals remain -- shadows and
    scrims, which are not part of a scheme, and lamp haloes, which hardcode a
    lamp's own channels and should be mixed from the token instead. That is
    the next pass, and this test will be widened when it lands.
    """
    css = stylesheet()

    # A `:root` block holds the values -- the default scheme and one per
    # alternative -- and comments hold the argument for them. Neither is a
    # use, so neither is searched.
    blind: list[tuple[int, int]] = []
    at = 0
    while (a := css.find("/*", at)) >= 0:
        b = css.find("*/", a + 2)
        b = len(css) if b < 0 else b + 2
        blind.append((a, b))
        at = b
    at = 0
    while (a := css.find(":root", at)) >= 0:
        b = css.index("\n}", a)
        blind.append((a, b))
        at = b
    # Every `:root` block is values -- the three schemes, and three that set a
    # gutter or a rail width at a breakpoint. If a fourth scheme is added it
    # is blinded above automatically; it still has to be a block of values.
    for scheme in (':root {', ':root[data-theme="day"]', ':root[data-theme="night"]'):
        assert scheme in css, f"{scheme} is gone, and the schemes are its only home"

    # One exception, and it is already a name: `.rgraph` declares its own
    # custom properties, so those values are a local palette, not a literal.
    allowed = {"--graph-main", "--graph-oracle"}

    loose = []
    for m in re.finditer(r"#[0-9a-fA-F]{3,8}\b", css):
        if any(a <= m.start() < b for a, b in blind):
            continue
        line = css.count("\n", 0, m.start()) + 1
        text = css[css.rfind("\n", 0, m.start()) + 1:css.find("\n", m.start())]
        if any(name in text for name in allowed):
            continue
        loose.append(f"stylesheet line {line}  {text.strip()}")

    assert not loose, (
        "a colour without a name, below the token block:\n  "
        + "\n  ".join(loose)
    )


def test_the_tests_read_the_stylesheet_the_browser_loads():
    """The stylesheet is several files, and a later one overrides an earlier
    one, so the order index.html links them in is part of the design. The
    tests read them through one list; if that list drifts from the page, a
    test passes against a cascade no browser ever builds.
    """
    from support import STYLESHEETS

    page = (ROOT / "console" / "index.html").read_text(encoding="utf-8")
    linked = re.findall(r'<link rel="stylesheet" href="/static/([^"]+)">', page)
    assert linked == STYLESHEETS, "tests/support.py and index.html disagree on the stylesheet"
    on_disk = sorted(p.relative_to(ROOT / "console").as_posix()
                     for p in (ROOT / "console" / "css").glob("*.css"))
    assert sorted(linked) == on_disk, "a stylesheet on disk that the page never loads, or the reverse"


def test_a_token_used_without_a_fallback_is_declared_somewhere():
    """`var(--x)` for an `--x` nobody declares is not a colour that goes wrong.
    It is a declaration the browser throws away.

    Three rules asked for `--ink-1` -- plainly the first step of the
    `--ink / --ink-2 / --ink-3` ramp, and a step that was never written down.
    Each was invalid at computed-value time, so the property was dropped and
    the element inherited: `.hcrew b` came out the same colour as the sentence
    around it, and `.hmore:hover` did nothing at all. Nothing looked broken.
    That is the whole problem with this mistake -- it fails by doing nothing,
    in exactly the places built to draw attention.

    A fallback makes it legal (`var(--x, 60px)` renders the 60px), so this
    only guards the bare form. A token may be declared in the stylesheet or
    set from the console at render time -- `--graph-w` is a column width only
    the graph knows, and `--size` is a marker's diameter -- so both are read.
    """
    css = stylesheet()
    js = "\n".join(
        f.read_text(encoding="utf-8")
        for f in sorted((ROOT / "console").rglob("*.js"))
        if "vendor" not in f.parts
    )

    declared = set(re.findall(r"(--[\w-]+)\s*:", css)) | set(re.findall(r"(--[\w-]+)\s*:", js))
    bare = {m.group(1) for m in re.finditer(r"var\(\s*(--[\w-]+)\s*\)", css)}

    orphans = sorted(bare - declared)
    assert not orphans, (
        "used as var(--x) with no fallback, and declared nowhere the browser "
        "will look:\n  " + "\n  ".join(orphans)
    )


def test_a_proposal_card_shows_the_thing_it_proposes():
    """Four cards -- the tiers, the per-file rules, the files, the placements
    -- passed a `body` to a card that never took one, so each drew a header, a
    count and a reason with the thing itself missing. And the rules diff
    compared match and command alone, so a proposal changing one rule's report
    showed no change at all under a button that applies it."""
    app = app_js()
    card = app[app.index("function proposalCard("):app.index("function classifyProposed(")]
    assert "body, why" in card and "${body || ''}" in card
    assert "body: x.body" in app, "the cards that pass one must reach it"
    rules = app[app.index("(diff.test_file_commands || []).length"):]
    rules = rules[:rules.index("</p>`)")]
    assert "report: ${r.report || '(none)'}" in rules and "load: ${r.collect || '(none)'}" in rules
    assert "fresh.map((r) => r.match).join(', ')" in rules, "the card names the rule that changed"


def test_a_proposal_says_why_in_one_sentence_a_person_can_read():
    """A card folded the whole reason away, so it said what changed and never
    why -- and the reading opened mid-argument: "A file here is a large unit
    of blame: the integration tier arranges state through the API...". The
    first sentence is on the card now, and every reason field asks for one
    plain sentence first."""
    app = app_js()
    card = app[app.index("function proposalCard("):app.index("function classifyProposed(")]
    assert "p-gist" in card and "firstSentence(why)" in card
    assert "the rest of the reading's words" in card

    for field in ("testing_reason", "test_file_commands_reason", "blind_placements_reason",
                  "environment_reason", "scaffolding_reason"):
        text = schemas.SurveyDiff.model_fields[field].description
        assert "ONE plain sentence" in text, field
        assert "read in a breath" in text, field

    prompt = " ".join((ROOT / "factory" / "roles" / "surveyor.md").read_text().split())
    assert "Every `*_reason` opens with one plain sentence" in prompt

    css = stylesheet()
    assert ".p .ts-rule { padding-left: .85rem" in css, "a card's rules sit inside the card"
    assert ".p .ts-tier { padding-left: .85rem" in css, "and so do its test levels"
    assert ".p .ts-why > summary .ts-more { margin-left: auto; }" in css, \
        "the disclosure on a level's row reads as a word in the sentence beside it"


def test_one_proposal_is_said_once_and_in_plain_words():
    """A reading with one change wrote the whole case as the page summary and
    again as the card's reason -- "`web/src/testing/http.ts` ... exists in the
    repository but is missing from the unit tier on record" above a card
    saying the same. And the card's own sentence ran two nested clauses into
    this tool's vocabulary. One proposal of any kind now drops the summary,
    and the first sentence is written for the person deciding."""
    app = app_js()
    # The summary is said once, where the reading's changes are listed by the
    # page they wait on; each card carries only its own reason.
    parts = app[app.index("function partsBlock("):app.index("function partsDone(")]
    assert "diff.summary" not in parts and "why: x.reason" in parts

    for field in ("testing_reason", "scaffolding_reason"):
        text = schemas.SurveyDiff.model_fields[field].description
        assert "Under 25 words" in text and "vocabulary" in text, field
    assert "Name the thing the change is about" in schemas.SurveyDiff.model_fields[
        "summary"].description

    prompt = " ".join((ROOT / "factory" / "roles" / "surveyor.md").read_text().split())
    assert "Write the first sentence for the person, not for this tool" in prompt
    assert "Name the thing, not the argument" in prompt
    assert "A test that mounts a page cannot fake the API's replies" in prompt


def test_a_recording_says_what_it_was_supposed_to_show_in_words():
    """A criterion id is a reference. `AC-13` tells a reader which of eighteen
    statements to go and look up, and the whole point of a recording is that
    they should not have to leave it to find out what they are watching.

    The spec keeps two forms of every criterion for exactly this: `title` is
    the plain-language one written to be read, and `statement` is the precise
    one the oracle writes tests from -- four sentences, literals and all. A
    band that showed `statement` under a thumbnail would be unreadable, which
    is why this checks which of the two is wired up.
    """
    ui = (ROOT / "console" / "ui" / "evidence.js").read_text(encoding="utf-8")

    assert "c.title || ''" in ui, "the plain-language name"
    assert "acSaid[String(c.id).toUpperCase()] = c.title" in ui
    assert "c.statement" not in ui, (
        "`statement` is precise to the point of being ugly and belongs on the "
        "criterion's own screen, not under a thumbnail")

    # On the card and in the player both -- one reader looking at the band and
    # one stepping through the frames, and neither should have to guess.
    card = ui[ui.index('title="On screen"'):ui.index('title="The project\'s gates"')]
    assert "criteriaNamed(t.title, acIds, acSaid)" in card, "on the thumbnail"
    assert "class=\"ee-shot-ac\"" in card

    assert "criteriaNamed(walk.frames.title, acIds, acSaid)" in ui, "and into the player"
    assert 'class="tp-ac"' in ui, "above the frames it is a claim about"


def test_the_player_does_not_head_itself_with_a_path_to_a_file():
    """A Playwright title is addressed to a runner:
    `acceptance/card_tags.spec.ts:44 > [AC-13] the real Add tag control ...`.
    The path answers "which file do I re-run", which is not a question anybody
    has while looking at the frames that file produced -- and it was taking the
    width of the header, so the part a person wanted was elided.

    What is kept is the sentence the test's author wrote. The bracketed ids go
    too, because they are the line directly above it.
    """
    ui = (ROOT / "console" / "ui" / "evidence.js").read_text(encoding="utf-8")

    said = ui[ui.index("const testNamed"):]
    said = said[:said.index("\n};")]
    assert "lastIndexOf" in said and "\\u203a" in said, (
        "split on the runner's own separator, and take what is after the last one")
    assert r"replace(/^\s*\[[^\]]*\]\s*/, '')" in said, "and not the ids again"

    # The header shows the criteria when a test named any, and falls back to
    # the test's sentence when it named none -- never the raw title.
    head = ui[ui.index('<div class="tp-head">'):]
    head = head[:head.index("</div>")]
    assert "testNamed(frames.title)" in head, "the trimmed one is"
    # And never the raw one: every mention of it in the header goes through
    # the trim, so a path cannot come back by being read straight.
    assert head.count("frames.title") == head.count("testNamed(frames.title)")
    assert "criteria.map((c) => c.id).join(', ')" in head


def test_criteria_on_a_recording_are_ordered_the_way_they_are_numbered():
    """The id list is sorted longest-first, so that `AC-11` is never read as
    `AC-1` when matching. That ordering then leaked into the display, where it
    means nothing: a test covering AC-2 and AC-10 would head itself `AC-10,
    AC-2`, and a reader checking a recording against a numbered spec has to
    sort it themselves.
    """
    ui = (ROOT / "console" / "ui" / "evidence.js").read_text(encoding="utf-8")
    named = ui[ui.index("const criteriaNamed"):]
    named = named[:named.index(".map((id) =>")]

    assert ".slice()" in named, "sorted on a copy -- `acIds` is shared and matching needs its order"
    assert "parseInt" in named and "replace(/\\D+/g, '')" in named, (
        "by the number in the id, not by the string: 'AC-10' < 'AC-2' as text")


def test_screenshots_are_gone_and_nothing_still_expects_them():
    """Recordings replaced screenshots: at the viewport's resolution a
    recording shows every frame a picture would have and the steps between,
    and a picture was one moment somebody chose -- chosen wrong on the run
    that made that point.

    Removed end to end, because half a removal is worse than none. A brief
    that still asks for pictures sends a model writing into a directory
    nothing creates; a coverage check still reading them reports a gap no
    repair can close and spends a round trying; a signal still derived from
    them goes quiet. That last one was real -- `check_trace_coverage` learned
    that a browser had run from a screenshot existing.
    """
    code = "\n".join(p.read_text(encoding="utf-8")
                     for p in factory_files())
    briefs = "\n".join(p.read_text(encoding="utf-8")
                       for p in sorted((ROOT / "factory" / "roles").glob("*.md")))
    console = "\n".join([app_js(), *(p.read_text(encoding="utf-8")
                                      for p in sorted((ROOT / "console" / "ui").glob("*.js")))])

    assert "FACTORY_SCREENS_DIR" not in code + briefs + console
    assert "screens_dir" not in code, "no directory is named to a test or swept"
    assert "file_screens" not in code
    assert 'payloads("screens")' not in code, "nothing reads the old records as current"
    assert "/screens" not in code.replace("screencast", "") + console, "no route, no fetch"
    assert not hasattr(pipeline, "check_screen_coverage")
    assert not hasattr(pipeline.Factory, "_collect_screens")
    assert not hasattr(config_module.ReworkConfig(), "screens_max")


def test_no_module_binds_the_same_top_level_name_twice():
    """A second top-level definition of a name replaces the first without a
    word, and everything written against the first quietly gets the second.

    It happened twice in one piece of work. A new `_CRITERION_ID` pattern
    replaced one fourteen hundred lines up that captured the number, and two
    unrelated checks started calling `int("AC-1")`. Earlier, a test helper
    redefined with a different signature broke five tests that had never been
    touched. In a test module it is worse, because the replaced test does not
    fail -- it stops existing.
    """
    import ast
    import collections

    files = [*factory_files(), ROOT / "tests" / "helpers.py",
             *sorted((ROOT / "tests").glob("test_*.py"))]
    for path in files:
        bound: dict[str, list[int]] = collections.defaultdict(list)
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound[node.name].append(node.lineno)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bound[target.id].append(node.lineno)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                bound[node.target.id].append(node.lineno)
        twice = {name: lines for name, lines in bound.items() if len(lines) > 1}
        assert not twice, f"{path.name} binds these more than once: {twice}"


def test_the_walkthrough_fits_in_the_window_whatever_size_the_frames_are():
    """The player was bounded in width only. That was fine while frames were
    800x450; at the page's own 1280x720, full width is taller than a laptop
    screen, and the controls -- the only way to step a walkthrough -- ended up
    below the bottom of it.

    Bounded in height as well, as a column: the claim, the controls and the
    step keep their size and the frame takes what is left, shrinking without
    being cropped, because a cropped frame can cut off the line the criterion
    is about.
    """
    css = stylesheet()
    box = css[css.index(".tp { background"):]
    box = box[:box.index("}")]
    assert "max-height: calc(100dvh" in box and "max-height: calc(100vh" in box, (
        "no taller than the window, with a fallback where dynamic units are missing")
    assert "flex-direction: column" in box
    assert ".tp > * { flex: none; }" in css, "text and controls keep their height"
    assert ".tp > .tp-img { flex: 0 1 auto; min-height: 0; }" in css, "the frame gives way"
    frame = css[css.index(".tp-img { display: block"):]
    frame = frame[:frame.index("}")]
    assert "object-fit: contain" in frame and "height: auto" not in frame, (
        "shrunk whole, not cropped and not stretched")


def test_the_way_out_can_be_proved_from_the_crew_page(monkeypatch):
    """`Test it` makes a throwaway network, lets it out, runs a container with
    no settings on it, reads what it could reach, and removes the network
    whatever happened."""
    from factory import egress
    from factory.config import DockerConfig

    seen: list[list[str]] = []

    async def fake(argv, timeout=60.0):
        seen.append(list(argv))
        if argv[1] == "run" and "--entrypoint" in argv and "python" in argv:
            return 0, ('{"what": "An outside name resolves", "ok": true, "detail": "x"}\n'
                       '{"what": "SSH, on port 22", "ok": false, "detail": "timed out"}\n')
        return 0, ""

    async def ok(*a, **k):
        return ""

    async def subnet(docker, name):
        return "10.212.4.0/22"
    monkeypatch.setattr(egress, "_docker", fake)
    monkeypatch.setattr(egress, "create_sealed", ok)
    monkeypatch.setattr(egress, "open_egress", ok)
    monkeypatch.setattr(egress, "subnet_of", subnet)
    got = asyncio.run(egress.self_test(DockerConfig()))
    assert not got["ok"] and [c["ok"] for c in got["checks"]] == [True, False]
    probe = next(c for c in seen if c[1] == "run")
    assert any(a.endswith(":/etc/resolv.conf:ro") for a in probe)
    assert not any("PROXY" in a.upper() for a in probe)
    assert any(c[1:3] == ["network", "rm"] for c in seen), "the throwaway network stays behind"
    app = app_js()
    assert "data-egress-test" in app and "'/api/egress/test', { method: 'POST' }" in app


def test_a_proposed_reading_shows_each_tiers_cleanup_without_opening_it():
    """A re-read whose whole substance was the cleanup rendered three tiers
    identical to the ones in force; the change was only visible by opening each
    "why". The same failure the canary and the test db each had once. And the
    pill says the convention was read, never that it is good -- a green "cleans
    up" beside "Nothing is undone for you here" vouched for what it denied."""
    app = app_js()
    body = app.split("function testingSummary(testing, diff) {")[1].split("\nfunction ")[0]
    assert "${cleanupPill(t)}" in body and "${cleanupGist(t)}" in body
    assert "${cleanupNote(t)}" in body
    pill = app.split("const cleanupPill")[1].split(";\n")[0]
    assert "no cleanup" in pill and "cleans up" not in pill and "recorded" not in pill
    # The visible line is the one written for a person, never a slice of the
    # agents' prose -- which is what read as gibberish.
    gist = app.split("function cleanupGist(t) {")[1].split("\n}\n")[0]
    assert "t.cleanup_summary" in gist and "t.cleanup ||" not in gist and "match(" not in gist
    assert "between tests" not in gist, "a stray label beside the sentence, read as noise"


def test_the_resurvey_is_told_its_change_list_covers_tooling_only():
    """"No file in the repository changed since the last reading", said over a
    commit that changed nine: the list it was shown tracks tooling only, and was
    headed as though it tracked everything."""
    src = inspect.getsource(onboarding_module().ProjectOnboarding.run_resurvey)
    assert "# Tooling files that moved since the last reading" in src
    assert "is not \\\"nothing changed\\\"" in src


def test_a_tier_that_needs_a_fix_is_one_choice_with_its_parts_inside():
    """Four unrelated-looking cards -- the reading, a file, a file to replace,
    an environment -- made one decision nobody could see as one, told the reader
    to "tick one below" over cards with no tick, and let them pick half a fix.
    Now each fix is an option with its parts inside it, "leave it" is the
    default, and those parts are not drawn again as cards of their own."""
    app = app_js()
    assert "${cleanupChoice(t, diff)}" in app and "cleanupFixes" not in app
    choice = " ".join(app.split("function cleanupChoice(t, diff, opts = {}) {")[1].split("\n}\n")[0].split())
    for said in ('type="radio" class="${cls}"', "Leave it", "How should Fabrika fix this?",
                 "show what changes", "read it", "see the command",
                 "i === '' ? 'checked' : ''", "Fix it in the code", "data-copy-diff-rec",
                 "Give the prompt to your coding agent, then re-survey"):
        assert said in choice, f"the choice no longer has: {said}"
    assert "full run" not in choice and "feature" not in choice.replace("featur e", ""), \
        "a fix is offered as a feature for the factory to build -- circular, and a mess"
    files = app.split("{ key: 'scaffolding',")[0].split("const inFix = new Set(")[-1][:400]
    assert "cleanup_options" in files, "a fix's files are drawn again as a card of their own"
    assert "moved.every((k) => k === 'test_prepare')" in app, \
        "a fix's reset step is drawn again as an environment card"
    apply = app.split("function rulePart(button) {")[1].split("\n}\n")[0]
    assert "{ part, cleanup, checks: false }" in apply
    pill = app.split("const cleanupPill")[1].split(";\n")[0]
    assert "each test must undo its own" in pill and "no cleanup" in pill




def test_a_proposed_file_card_has_a_title_and_a_plain_sentence():
    """The files card drew a bare checkbox and an empty badge -- it passed a
    label the card never reads, and no title, action or count -- and its one line
    was the first sentence of `purpose`, which opens with a category ("A test at
    the user level cannot...") and meant nothing to the person deciding. A file
    already in the repository is drawn as its own card below, and the header now
    says so, because the reason above speaks for both."""
    from factory.schemas import ScaffoldFile

    assert "summary" in ScaffoldFile.model_fields
    app = app_js()
    card = app.split("{ key: 'scaffolding',")[1].split("}; })()")[0]
    for said in ("field: 'files this project needs'", "verb: 'add'", "to replace, below",
                 'class="sc-summary"', "f.summary"):
        assert said in card, f"the files card no longer has: {said}"
    assert "longText(f.purpose" not in card, "a slice of the purpose is shown as the summary"
    placements = app.split("{ key: 'blind_placements',")[1][:200]
    assert "field: 'where blind tests go'" in placements and "verb: 'replace'" in placements


def test_the_console_says_what_is_not_tested_wherever_it_matters():
    """The explainer on the project page, the level's own row, the spec before
    it is frozen, the calls at review -- counted with them -- and the decline
    on the re-survey card, said as what it does."""
    app = app_js()
    tab = app.split("function testsTab(p) {")[1].split("\n}\n")[0]
    for said in ("Fabrika tests everything it builds", "you check", "checked by Fabrika",
                 "unchecked_levels", "To have Fabrika check this level"):
        assert said in tab, said
    ov = app.split("function overviewSection(p) {")[1].split("\n}\n")[0]
    assert "levels checked" in ov and "unchecked_levels" in ov
    assert "Fabrika won't check criteria at this level, because" in app
    spec = app.split("secs.push(['mustdo'")[0].rsplit("const skipped", 1)[1]
    assert "won't be checked by" in spec and "criteriaByArea(criteria, skipped)" in app
    choice = app.split("function cleanupChoice(t, diff, opts = {}) {")[1].split("\n}\n")[0]
    assert "Don't check criteria at this level" in choice

    worklist = (ROOT / "console" / "ui" / "worklist.js").read_text()
    assert "Check these yourself" in worklist and "const total = calls.length + checks.length;" in worklist
    assert "disposition: works ? 'dismissed' : 'repair', source: 'criterion'" in worklist
    tabs = (ROOT / "console" / "ui" / "tabs.js").read_text()
    assert "const total = calls.length + checks.length;" in tabs
    shared = (ROOT / "console" / "ui" / "shared.js").read_text()
    assert "if (traceStatus === 'manual' || qaStatus === 'manual') return 'manual';" in shared


def test_help_is_a_page_and_a_drawer_over_one_set_of_articles():
    """Written once, in console/help: a page of its own that needs no project,
    and a drawer a `?` opens over whatever you were reading. Every topic in the
    manifest has its article, every article opens with a plain sentence, and
    every link between articles names a topic that exists."""
    root = ROOT / "console" / "help"
    manifest = json.loads((root / "topics.json").read_text())
    slugs = [t["slug"] for g in manifest["groups"] for t in g["topics"]]
    assert "checking-the-work" in slugs and "unchecked-levels" in slugs
    for slug in slugs:
        body = (root / f"{slug}.html").read_text()
        assert body.lstrip().startswith('<p class="lead">'), f"{slug} does not open plainly"
        for linked in re.findall(r'data-help="([^"]+)"', body):
            assert linked in slugs, f"{slug} links to {linked}, which is not a topic"

    app = app_js()
    assert "const HELP_HREF = '#/?help';" in app
    assert '`<a href="${HELP_HREF}" class="${state.view === \'help\' ? \'on\' : \'\'}">help</a>`' in app
    assert "const view = params.help ? 'help'" in app and "html = helpScreen();" in app
    # Every tab of a project opens the same way: icon, name, and a ? to its help.
    helped = re.search(r"const HELP_FOR = \{([^}]*)\};", app).group(1)
    for tab in ("checks", "tests", "survey", "environment"):
        assert f"{tab}:" in helped, f"the {tab} tab has no ? to its help"
        slug = re.search(rf"{tab}: '([\w-]+)'", helped).group(1)
        assert slug in slugs, f"the {tab} tab's ? opens {slug}, which is not a topic"
    assert "tabHead(sec, p)" in app
    assert "event.key === '?'" in app and "openHelp(helpForHere())" in app
    for slug in re.findall(r"data-help-open=\"([a-z-]+)\"", app):
        assert slug in slugs, f"the console opens help at {slug}, which is not a topic"


def test_every_setup_a_feature_gets_runs_the_warm_ups_the_baseline_found():
    """Found once, at the baseline; used everywhere setup runs -- a feature's
    own sandbox, the base-commit comparison, a unit's environment -- or the
    first feature fails exactly as the baseline did before it learned. A new
    reading starts the list again."""
    from factory import projects
    src = factory_source("pipeline") + factory_source("unitenv")
    calls = re.findall(r"run_setup\((?:[^()]|\([^()]*\))*\)", src)
    assert calls and all("warm=" in c for c in calls), calls
    assert "project.state.warm_checks = []" in inspect.getsource(
        projects.ProjectRegistry.record_survey)
    app = app_js()
    assert "result.warmed ?" in app and "!r.passed && !r.skipped" in app
    row = app.split("function checkRow(")[1].split("\nfunction ")[0]
    assert "r.warmed" in row, "the gate-0 check list, where a person decides, does not say it"


def test_a_first_survey_s_fix_can_be_picked_on_the_project_page():
    """The choice lived only on a re-survey's proposal, so a project fresh from
    its first survey had its fixes recorded and nothing to click. Now the
    level's row carries the same choice, with an Apply that uses the same rules:
    never a prompt-only fix, never a file the write would refuse, and the level
    tested from then on."""
    src = factory_source("server")
    ep = src.split('@app.post("/api/projects/{project_id}/cleanup")')[1].split("@app.")[0]
    for said in ("cleanup_option_problems(source, option)", "registry.apply_scaffolding(",
                 '"cleanup_applied"', "is done with a coding agent"):
        assert said in ep, said

    app = app_js()
    tests = app.split("function testsTab(p) {")[1].split("\n}\n")[0]
    assert "{ row: true }" in tests and "!fixFiles.has(f.path)" in tests
    # The waiting reading is named once, and its checks are not part of it.
    assert "partsWaiting('tests').includes('testing')" in tests, \
        "the row and a waiting proposal both offer the same fix"
    choice = app.split("function cleanupChoice(t, diff, opts = {}) {")[1].split("\n}\n")[0]
    assert "const cls = opts.row ? 'row-cleanup' : 'rs-cleanup';" in choice, \
        "a proposal's Apply would collect the row's picks too"
    assert "data-apply-cleanup" in choice and "if (target.dataset.applyCleanup)" in app


def test_the_overview_counts_calls_exactly_as_the_review_screen_does():
    """The dashboard said "21 findings" for a packet whose review screen said
    "11 of 11 calls left": two counts of one thing. Now the server counts the
    way the review screen does -- restatements folded, `noted` excluded,
    findings sharing a file and a criterion (or neither naming one) are one
    call, and a flag on any of a call's findings settles it -- and the checks
    by hand are counted beside them."""
    from factory import overview

    packet = {
        "findings": [
            {"id": "a", "files": ["x.py"], "criterion_ids": ["AC-1"]},
            {"id": "b", "files": ["x.py"], "criterion_ids": ["AC-1"]},     # same call as a
            {"id": "c", "files": ["x.py"], "criterion_ids": ["AC-2"]},     # same file, other criterion
            {"id": "d", "files": ["y.py"]},
            {"id": "e", "files": ["y.py"]},                                # same call as d
            {"id": "f", "files": ["z.py"]},                                # a restatement
            {"id": "g", "files": ["w.py"]},                                # noted: a condition, not a call
        ],
        "records": [{"finding_id": "f", "duplicate_of": "a"},
                    {"finding_id": "g", "outcome": "noted"}],
        "manual_checks": [{"criterion_id": "AC-7"}, {"criterion_id": "AC-8"}],
    }
    flags = [{"anchor": "b", "source": "finding"}, {"anchor": "AC-7", "source": "criterion"}]
    assert overview.calls_left(packet, flags) == {"calls": 3, "calls_left": 2, "checks": 2, "checks_left": 1}

    shared = (ROOT / "console" / "ui" / "shared.js").read_text()
    for kept in ("'open', 'escalated', 'unattempted', 'attempted_not_fixed', 'needs_spec_change'",
                 "if (meets(ac, bc) || (!ac.length && !bc.length)) union(a.id, b.id);"):
        assert kept in shared, "the review screen's rule moved; the overview's copy must move with it"


def test_the_cost_tile_says_how_the_work_was_paid_for_not_a_dollar_figure():
    """Spend added what subscriptions report the work would have cost at API
    prices to what was billed, and showed "$43.23" for a project where nothing
    was charged. Cost says whether anything was charged, splits the tokens by
    route -- subscriptions against anything charged per token -- and shows
    dollars only for what a route actually billed."""
    app = app_js()
    tile = app.split("function costTile(ov) {", 1)[1].split("\n}\n", 1)[0]
    for said in ("'All on subscriptions'", "charged` : `${split.pct}% on subscriptions`",
                 "db-bar", "db-key", 'data-cost-info="1"', "How the work was paid for"):
        assert said in tile, said
    assert "notional" not in tile, "the tile adds up what work would have cost on an API"
    assert "costTile(ov)," in app and "overviewTile('Spend'" not in app
    row = app.split("function waitingRow(f, pid) {", 1)[1].split("\n}\n", 1)[0]
    assert "all on plans" in row and "notional" not in row


def test_settings_live_on_the_tabs_they_describe_and_the_dockerfile_is_read_only():
    """There is no settings page. Checks and Environment are edited on their own
    tabs, and so is Survey -- the project's name, repository and branch, shown
    first -- each behind an Edit in the heading; removing the project sits at
    the foot of Survey. The
    Dockerfile is Fabrika's own copy, not the repository's, so it is shown and
    never edited: an edit would drift from how the repository builds."""
    app = app_js()
    assert "function settingsScreen" not in app and "state.view === 'settings'" not in app
    assert ">settings</a>" not in app and "settingsHref" not in app
    assert "const EDITABLE = ['checks', 'environment', 'survey'];" in app
    assert "const editHref = (p, tab, check = '') =>" in app
    # An old link still lands somewhere sensible.
    assert "if (params.settings) { params.gates = 'survey'; delete params.settings; }" in app
    # Saving a change to what the checks run in, or run, measures them again.
    assert "if (rerun) runBaseline();" in app
    # A form is not rebuilt under the person typing in it.
    assert "const wanted = state.editing ? 0 :" in app
    editor = app[app.index("function environmentEditor()"):app.index("function checksEditor()")]
    assert 'data-draft="environment.dockerfile"' not in editor
    assert "environment.dockerfile" not in editor
    assert "read-only" in editor and "in your repository and commit it" in editor
    panels = app[app.index("function projectPanels("):app.index("function firstClause(")]
    assert "missing, and worth adding" not in panels
    assert "removeProject(live)" in panels
    survey = panels[panels.index("survey: `"):]
    order = [survey.index(f"factRow('{k}'") for k in ("name", "repository", "features branch from", "what it is")]
    assert order == sorted(order)
    # The overview says so while Fabrika's own copy is the one in use.
    assert ": (df.source === 'fabrika' && !df.own) ? 'Dockerfile to move' : 'Ready'," in app
    help_ = (ROOT / "console" / "help" / "environment.html").read_text()
    assert "settings" not in help_ and ".fabrika/Dockerfile" in help_


def test_history_is_a_tab_and_the_ledger_leaves_the_top_navigation():
    app = app_js()
    assert ">ledger</a>" not in app and "nav-scope" not in app.split("function schemePicker")[0][-3000:]
    assert "{ id: 'history', label: 'history', has: () => true }" in app
    assert "${sec === 'history' ? historySection(p) : ''}" in app
    assert "state.view === 'log' && !state.id ? 'history' : ''" in app
    assert "?log\">Show every record</a>" in app
    assert (ROOT / "console" / "help" / "history.html").exists()


def test_the_survey_suggests_the_projects_own_test_stage_and_prefers_it():
    prompt = (ROOT / "factory" / "roles" / "surveyor.md").read_text()
    assert "name the file in `dockerfile_path`, the stage in `dockerfile_target`" in prompt
    assert "also suggest the fix to theirs" in prompt and "of kind `ci`" in prompt
    assert "Do not suggest it again if it was declined." in prompt
    app = app_js()
    assert 'data-retire-dockerfile="1">Remove .fabrika/Dockerfile</button>' in app


def test_the_resurvey_dialog_warns_about_uncommitted_work_only_when_there_is_some():
    app = app_js()
    assert "UNCOMMITTED_UNSEEN" not in app
    assert app.count("warn: uncommittedUnseen(),") == 2
    fn = app[app.index("function uncommittedUnseen()"):app.index("async function askResurvey()")]
    assert "return n ?" in fn and ": '';" in fn


def test_nothing_else_fabrika_starts_publishes_a_port():
    """A preview is the only thing that reaches out of a sealed network, and
    only to loopback. Any other publish would be a second door nobody closes."""
    sources = {p.relative_to(PACKAGE).as_posix(): p.read_text() for p in factory_files()}
    # `-p` is also `mkdir -p` and `ps -p`; a publish is the one whose next
    # argument is a port mapping.
    mappings = [(name, m.group(1)) for name, text in sources.items()
                for m in re.finditer(r'"(?:-p|--publish)"\s*,\s*([^,\)\]]+)', text)
                if ":" in m.group(1)]
    assert mappings == [("containers.py", 'f"127.0.0.1:{p}:{p}"')], \
        f"a port is published outside the door: {mappings}"
    assert not [n for n, t in sources.items() if re.search(r'"(?:--publish-all|-P)"', t)]
    door = inspect.getsource(__import__("factory.containers", fromlist=["x"]).door_argv)
    assert '"0.0.0.0:' not in door and "127.0.0.1:{p}:{p}" in door
    # And a compose stack still has every port it declared taken away.
    assert "ports: !override []" in sources["containers.py"]


def test_review_offers_the_app_where_the_checking_is():
    ui = ROOT / "console" / "ui"
    worklist = (ui / "worklist.js").read_text()
    manual = worklist.split("function ManualChecks(", 1)[1].split("\nexport function", 1)[0]
    assert "<${PreviewInline} data=${data} />" in manual.split('<div class="mc-head">', 1)[1] \
        .split("</div>", 1)[0], "the app is offered in the heading of the list it is for"
    assert "Checked by hand in the running app at" in manual, \
        "a ruling made with the app open no longer says which commit it was"
    tabs = (ui / "tabs.js").read_text()
    band = tabs.split("export function Verdict(", 1)[1].split("\nexport function", 1)[0]
    assert "<${PreviewButton} data=${data} />" in band
    panel = (ui / "preview.js").read_text()
    assert 'rel="noopener noreferrer"' in panel and "estimate_s" in panel
    assert "It can't reach" in panel, "the page no longer says what the app cannot reach"
    topics = json.loads((ROOT / "console" / "help" / "topics.json").read_text())
    slugs = [t["slug"] for g in topics["groups"] for t in g["topics"]]
    assert "running-the-app" in slugs
    assert (ROOT / "console" / "help" / "running-the-app.html").is_file()


def test_a_reading_behind_with_a_proposal_waiting_says_rule_not_resurvey():
    """The stamp moves on the next save, and a ruling is one. Telling someone
    to re-survey while a proposal that answers the question waits sends them to
    pay for the same proposal again -- which is what it did, twice."""
    app = app_js()
    strip = app.split("function readingBehindStrip()", 1)[1].split("\nfunction ", 1)[0]
    assert "surveyWaiting()" in strip
    assert "no need to re-survey again" in strip
    assert "Re-survey to fill them" in strip.split("waiting\n", 1)[-1], \
        "with nothing waiting, re-surveying is still the advice"
    # And ruling really is a save, whichever way it goes: the stamp is set there.
    src = inspect.getsource(pipeline_projects_module.ProjectRegistry.apply_survey_diff)
    assert 'self.save(project, note="re-survey: nothing accepted")' in src
    assert "project.state.state_version = STATE_VERSION" in inspect.getsource(
        pipeline_projects_module.ProjectRegistry.save)


def test_the_preview_proposal_is_said_as_a_sentence_about_the_button():
    """It was a one-line diff behind a fold, `opens web /` in code, and a note
    running past the card's edge -- the one card about something a person does,
    and the one they could not read."""
    app = app_js()
    card = app.split("diff.preview ? (() => {", 1)[1].split("})() : null,", 1)[0]
    assert "At review, <b>Open the app</b> will open" in card
    assert "diff: ''" in card, "the preview card is back to a diff behind a fold"
    assert "ticks(pv.note)" in card and 'class="p-plain"' in card
    assert "Accepting changes nothing your checks run in" in card
    assert "gist: false" in app.split("function partsBlock(", 1)[1].split("\n}\n", 1)[0]
    css = stylesheet()
    assert ".p-plain {" in css and "padding: .7rem .85rem" in css


def test_as_built_draws_only_what_code_confirmed(tmp_path):
    """The reader's word is never enough on its own. An import has to name a
    file that exists and whose name the importer's text carries; a route call
    has to land on a route something serves. What fails is kept, marked, and
    counted -- a map that dropped it would look complete and be missing it."""
    records = dict(_AB_RECORDS)
    records["web/view.js"] = dict(purpose="Draws a card.", imports=["web/ghost.js", "api/store.py"],
                                  functions=[dict(name="render", line=1), dict(name="invented", line=2)],
                                  routes_called=[dict(path="/api/nowhere")])
    root = _ab_repo(tmp_path, _AB_FILES)
    ab = _ab_read(root, _AbStub(records)).as_built

    edges = {(e.source, e.target, e.kind): e for e in ab.edges}
    assert edges[("api/server.py", "api/store.py", "import")].confirmed
    assert edges[("web/app.js", "api/server.py", "route")].confirmed, \
        "a front end's call resolved through a helper must still land on the route it names"
    shaky = edges[("web/view.js", "api/store.py", "import")]
    assert not shaky.confirmed, "view.js never names store -- the reader's claim is not evidence"
    assert {"file": "web/view.js", "kind": "import", "named": "web/ghost.js"} in ab.coverage.unresolved_links
    assert any(c["path"] == "/api/nowhere" for c in ab.coverage.unmatched_calls)
    view = ab.file("web/view.js")
    assert [f.name for f in view.functions if not f.confirmed] == ["invented"]
    assert ab.coverage.edges_unconfirmed >= 2
    assert [r["path"] for r in ab.start_reading] == ["api/server.py"], \
        "a file the cartographer invented is not a place to start reading"


def test_a_subsystem_nothing_a_person_does_reaches_is_support_and_numbered_last():
    """Of clinic's seven subsystems four were build configuration, dependency
    lists, the development environment and a bootstrap -- drawn at the weight
    of the three that do the work. Product is what a capability reaches or
    where a way in is declared; everything else is support, and goes last so
    the product subsystems carry the low numbers."""
    AB = asbuilt_graph
    reading = AB.AsBuilt(
        files=[AB.FileNode(path=p, subsystem=k) for p, k in
               (("api/routes.py", "api/routes.py"), ("store/db.py", "store/db.py"), ("deps/requirements.txt", "deps/requirements.txt"))],
        subsystems=[AB.Subsystem(key="deps/requirements.txt", files=["deps/requirements.txt"], lines=900),
                    AB.Subsystem(key="store/db.py", files=["store/db.py"], lines=100),
                    AB.Subsystem(key="api/routes.py", files=["api/routes.py"], lines=50)],
        ways_in=[AB.WayInNode(id="route:GET /x", kind="route", name="/x", file="api/routes.py")])
    reading.capabilities = [AB.Capability(key="c", name="Do x", ways_in=["route:GET /x"],
                                          in_motion=[AB.ReachItem(unit="file:store/db.py", file="store/db.py",
                                                                  subsystem="store/db.py", lines=5)])]
    AB.classify_subsystems(reading)
    assert [(s.key, s.role) for s in reading.subsystems] == [
        ("store/db.py", "product"), ("api/routes.py", "product"), ("deps/requirements.txt", "support")]
    # A way in that is plumbing -- the app's root screen -- does not make its
    # subsystem product.
    reading.files.append(AB.FileNode(path="web/main.tsx", subsystem="web/main.tsx"))
    reading.subsystems.append(AB.Subsystem(key="web/main.tsx", files=["web/main.tsx"], lines=10))
    reading.ways_in.append(AB.WayInNode(id="screen:/", kind="screen", name="/", file="web/main.tsx"))
    reading.not_capabilities = ["screen:/"]
    AB.classify_subsystems(reading)
    assert dict((s.key, s.role) for s in reading.subsystems)["web/main.tsx"] == "support"
    reading.ways_in = []
    AB.classify_subsystems(reading)
    assert {s.role for s in reading.subsystems} == {""}, "with no ways in, nothing can be told apart"


def test_every_subsystem_and_capability_has_three_letters_of_its_own():
    """A number was a position and moved whenever the order did; a code names
    the group, so it can be said in a finding or a packet. What the
    cartographer proposed stands when it is three capitals nobody else holds;
    the last reading's code for the same group stands before that; a code a
    dissolved group held is not handed to a different one."""
    AB = asbuilt_graph
    prev = AB.AsBuilt(subsystems=[AB.Subsystem(key="a.py", name="Running agents", code="AGT"),
                                  AB.Subsystem(key="gone.py", name="Old queue", code="OLQ")],
                      capabilities=[AB.Capability(key="review", name="Review the packet", code="RVP")])
    now = AB.AsBuilt(subsystems=[AB.Subsystem(key="a.py", name="Running agents", code="AGT"),
                                 AB.Subsystem(key="b.py", name="Order ledger", code="OLQ"),
                                 AB.Subsystem(key="c.py", name="Checkout", code="agt")],
                     capabilities=[AB.Capability(key="review", name="Review the packet", code="RVP"),
                                   AB.Capability(key="pay", name="Pay for an order", code="TOOLONG"),
                                   AB.Capability(key="see", name="Review a patient's trial journey", code="")])
    AB.assign_codes(now, prev)
    codes = {g.key: g.code for g in now.subsystems + now.capabilities}
    assert codes["a.py"] == "AGT" and codes["review"] == "RVP", "a group keeps its code"
    assert codes["b.py"] != "OLQ", "a dissolved group's code is not handed to another"
    assert codes["c.py"] != "AGT", "a code already held is not taken twice"
    assert codes["see"] == "RPT", "derived from the words that carry meaning"
    assert all(re.fullmatch(r"[A-Z]{3}", c) for c in codes.values())
    assert len(set(codes.values())) == len(codes)


def test_a_derived_code_never_takes_the_code_the_model_gave_another_group():
    """A group whose proposed code cannot stand gets one derived from its name,
    and that must not be a code the model gave a different group: "Asset
    service index" derives ASI, and "Asset importer" was given ASI. Groups were
    settled in turn, so the first to come up took it and the second, whose
    code was fine, lost it. The console, which derives codes for readings made
    before the engine did, holds them the same way."""
    import shutil
    import subprocess

    AB = asbuilt_graph
    assert AB.derive_code("Asset service index", set()) == "ASI"
    now = AB.AsBuilt(subsystems=[AB.Subsystem(key="idx.py", name="Asset service index", code="TOOLONG"),
                                 AB.Subsystem(key="imp.py", name="Asset importer", code="ASI")])
    AB.assign_codes(now, None)
    codes = [s.code for s in now.subsystems]
    assert codes[1] == "ASI" and codes[0] != "ASI" and re.fullmatch(r"[A-Z]{3}", codes[0]), codes

    if not shutil.which("node"):
        pytest.skip("node is not installed")
    source = (ROOT / "console" / "asbuilt.js").read_text()
    start = source.index("const AB_QUIET")
    script = source[start:source.index("\n}\n", source.index("function abNormalize(")) + 3] + """
const g = abNormalize({ subsystems: [{ name: 'Asset service index', code: '', role: 'product' },
                                      { name: 'Asset importer', code: 'ASI', role: 'product' }],
                        capabilities: [] });
console.log(JSON.stringify(g.subsystems.map((x) => x.code)));
"""
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    drawn = json.loads(out.stdout)
    assert drawn[1] == "ASI" and drawn[0] != "ASI", drawn


def test_the_checks_tab_opens_on_a_list_that_has_not_been_run():
    """A list edited since it was last run has no run on record, and its tab was
    drawn as a step not reached -- over the page that says to run it."""
    app = app_js()
    assert "has: (p) => !!p.baseline || !!(p.gates || []).length }" in app


def test_a_reading_s_parts_wait_on_the_tab_they_would_change():
    """A change to how one test file runs waited on the Survey tab, under one
    Apply, and read as a change to the survey. Each part waits where it
    belongs, with a dot on that tab, and the Survey tab says where."""
    app = app_js()
    tests = app.split("function testsTab(p) {")[1].split("\n}\n")[0]
    assert "${partsBlock(p, 'tests')}" in tests
    panels = app.split("function projectPanels(")[1].split("\n}\n")[0]
    assert "environment: partsBlock(p, 'environment')" in panels
    dot = app.split("function sectionDot(")[1].split("\n}\n")[0]
    assert "partsWaiting('tests')" in dot and "partsWaiting('environment')" in dot
    tabs = app.split("const PART_TAB = {")[1].split("};")[0]
    for part in ("testing", "test_file_commands", "trace_dirs", "blind_placements", "scaffolding"):
        assert f"{part}: 'tests'" in tabs, part
    for part in ("preview", "environment"):
        assert f"{part}: 'environment'" in tabs, part
    from factory.projects import PROPOSAL_PARTS
    assert all(f"{part}:" in tabs for part in PROPOSAL_PARTS), "a part has no tab to wait on"


def test_nothing_offers_to_hold_a_check_under_its_own_floor(tmp_path):
    from factory.projects import ProjectError
    from factory.schemas import GateResult

    gate = _floor_gate().model_copy(update={"family": "quality"})
    results = [GateResult(name="api-tests", exit_code=1, metric=78.02, red_kind="under_own_floor",
                          own_floor_value=95)]
    registry, project = _project_with_suggestions(tmp_path, results, [gate])
    with pytest.raises(ProjectError, match="floor this project sets"):
        registry.ratchet_check(project, "api-tests")
    app = app_js()
    row = app.split("function checkRow(", 1)[1].split("\n}\n", 1)[0]
    assert "g.family !== 'tests'" in row and "r.red_kind !== 'under_own_floor'" in row


def test_the_red_card_leads_with_the_kind_then_the_cause_then_the_fixes():
    app = app_js()
    row = app.split("function checkRow(", 1)[1].split("\n}\n", 1)[0]
    assert "redKindSentence(g, r)" in row and "diagnosisBlock(g, r, dx)" in row
    card = app.split("function todoCard(", 1)[1].split("\n}\n", 1)[0]
    assert card.index("${dx || ''}") < card.index('<div class="acts">')
    block = app.split("function diagnosisBlock(", 1)[1].split("\n}\n", 1)[0]
    assert "Tried: passes" in block and "'Confirmed'" in block and "data-dx-apply" in block
    assert "if (target.dataset.dxApply)" in app


def test_a_press_that_runs_the_checks_after_it_is_told_it_succeeded():
    """`withBusy` threw its function's result away, so applying a fix -- and
    ruling on the last part of a reading -- never ran the checks it promised."""
    app = app_js()
    busy = app.split("async function withBusy(", 1)[1].split("\n}\n", 1)[0]
    assert "return await fn();" in busy
    for fn in ("function applyDiagnosisFix(", "function rulePart("):
        body = app.split(fn, 1)[1].split("\n}\n", 1)[0]
        assert "runBaseline()" in body and ".then((" in body, fn


def test_coverage_is_its_own_figure_coloured_against_the_projects_floor():
    app = app_js()
    pill = app.split("function coveragePill(", 1)[1].split("\n}\n", 1)[0]
    assert "r.own_floor_value" in pill and "r.metric < floor ? 'bad'" in pill and "< 2 ? 'warn'" in pill
    row = app.split("function checkRow(", 1)[1].split("\n}\n", 1)[0]
    assert "metric: covers ? coveragePill(g, r) : ''" in row
    assert "!covers ? readingWord(g, r.metric)" in row, "the figure is said twice"


def test_coverage_is_said_to_be_on_or_off_at_the_top_of_the_tests_family():
    """A project measuring no coverage looked the same as one nobody had asked."""
    app = app_js()
    groups = app.split("function familyGroups(", 1)[1].split("\n}\n", 1)[0]
    assert "fam === 'tests' ? coverageLine(p, by)" in groups and "${covered}${rows}" in groups
    line = app.split("function coverageLine(", 1)[1].split("\n}\n", 1)[0]
    for said in ("head('on', 'ok')", "data-adopt-rec", "data-decline-rec", "data-reinstate-rec",
                 "no reading has suggested one yet"):
        assert said in line, said
    assert "`coverage ${coverageState(p).on ? 'on' : 'off'}`" in app, "the overview does not say it"


def test_the_architecture_map_draws_tooling_in_the_band_and_nothing_to_it():
    """An outside system that builds or ships the code is drawn in the build
    and tooling band at the bottom, not in the column of what the running
    system talks to, and no line runs to it."""
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    source = (ROOT / "console" / "asbuilt.js").read_text()

    def fn(name: str) -> str:
        start = source.index(f"function {name}(")
        return source[start:source.index("\n}\n", start) + 3]

    script = ("const state = {}; const esc = (s) => String(s);\n"
              + "".join(fn(n) for n in ("abHref", "abWrap", "abCallsInRuntimes", "abArchMap")) + """
const o = (key, name, extra) => ({ key, name, what: '', kind: 'other', mentions: [name], files: ['x'],
  evidence: 'code', subsystems: [], runtimes: [], capabilities: [], calls_in: [], entities_written: [],
  entities_read: [], tooling: false, ...extra });
const g = { subsystems: [{ key: 'api', code: 'API', name: 'API', role: 'product', files: [] }],
  capabilities: [], files: [], ways_in: [],
  architecture: { runtimes: [{ key: 'srv', name: 'API server', what: '', subsystems: ['api'], ways_in: {} }],
    tooling: [], actors: [], links: [], dismissed: {}, unsorted: [],
    outside: [o('pg', 'PostgreSQL', { runtimes: ['srv'] }), o('gha', 'GitHub Actions', { tooling: true })] } };
console.log(abArchMap(g));
""")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    svg = out.stdout
    band_y = float(re.search(r'<rect class="band" x="20" y="([\d.]+)"', svg).group(1))
    box = lambda name: re.search(rf"<title>{name}</title>\s*<rect class=\"box out\" x=\"(\d+)\" y=\"([\d.]+)\"", svg)
    pg, gha = box("PostgreSQL"), box("GitHub Actions")
    assert pg.group(1) == "760" and float(pg.group(2)) < band_y, "what it talks to stays in the right column"
    assert gha.group(1) == "38" and float(gha.group(2)) > band_y, "what builds it is in the band"
    assert svg.count('class="e said"') == 1, "one line, to PostgreSQL; none to the band"
    assert "Nothing —" not in svg


def test_the_as_built_has_help_for_every_page_it_links_to():
    manifest = json.loads((ROOT / "console" / "help" / "topics.json").read_text())
    slugs = [t["slug"] for g in manifest["groups"] for t in g["topics"]]
    source = (ROOT / "console" / "asbuilt.js").read_text()
    opened = set(re.findall(r'data-help-open="([a-z-]+)"', source))
    assert {"as-built", "as-built-system", "as-built-capabilities", "as-built-architecture"} <= opened
    for slug in opened:
        assert slug in slugs, f"the as-built opens help at {slug}, which is not a topic"
    assert "abHelpHere()" in app_js(), "the ? key ignores the as-built"


def test_every_help_topic_the_console_names_has_an_article_and_every_article_is_listed():
    """A `?` that opens "There's no help page called ..." is a broken promise,
    and an article missing from topics.json is one nobody can reach: it sits in
    no group, matches no search and is nobody's landing. So every slug the
    console names -- a link on any screen, a tab's `?`, the topic the `?` key
    picks for a screen or a stage, the landing page -- is a listed topic with
    an article, every article is listed once, and every feature screen and
    stage has a topic of its own rather than falling back to the landing."""
    root = ROOT / "console" / "help"
    manifest = json.loads((root / "topics.json").read_text())
    slugs = [t["slug"] for g in manifest["groups"] for t in g["topics"]]
    assert len(slugs) == len(set(slugs)), "a topic is listed twice"
    articles = {p.stem for p in root.glob("*.html")}
    assert set(slugs) == articles, (
        f"listed with no article: {sorted(set(slugs) - articles)}; "
        f"an article nobody lists: {sorted(articles - set(slugs))}")

    console = ROOT / "console"
    files = [*console.glob("app/*.js"), *console.glob("ui/*.js"), console / "asbuilt.js",
             console / "index.html", *root.glob("*.html")]
    sources = {p.relative_to(console).as_posix(): p.read_text() for p in files}
    named = {(name, slug) for name, text in sources.items()
             for slug in re.findall(r'data-help(?:-open)?="([a-z0-9-]+)"', text)}
    help_js = sources["app/help.js"]
    tables = {}
    for table in ("HELP_FOR", "HELP_FOR_VIEW", "HELP_FOR_STAGE"):
        body = re.search(rf"const {table} = \{{([^}}]*)\}};", help_js).group(1)
        tables[table] = dict(re.findall(r"([\w-]+):\s*'([a-z0-9-]+)'", body))
        named |= {(table, slug) for slug in tables[table].values()}
    named.add(("HELP_START", re.search(r"const HELP_START = '([a-z0-9-]+)';", help_js).group(1)))
    for fn, text in (("helpForHere", help_js), ("abHelpHere", sources["asbuilt.js"])):
        body = text.split(f"function {fn}()", 1)[1].split("\n}\n", 1)[0]
        named |= {(fn, slug) for slug in re.findall(r"return '([a-z0-9-]+)'", body)}
    missing = sorted(f"{where} -> {slug}" for where, slug in named if slug not in slugs)
    assert not missing, f"the console names help topics that do not exist: {missing}"

    vocab = sources["app/vocab.js"].split("const STAGES = Object.freeze({", 1)[1].split("});", 1)[0]
    stages = re.findall(r"^  (\w+): \{", vocab, re.M)
    assert stages and not set(stages) - set(tables["HELP_FOR_STAGE"]), \
        f"stages with no help topic: {sorted(set(stages) - set(tables['HELP_FOR_STAGE']))}"
    render = sources["app/render.js"]
    views = re.findall(r"'(\w+)'", render.split("const GATE2_VIEWS = [", 1)[1].split("];", 1)[0])
    steps = re.findall(r"\{ id: '(\w+)'", sources["app/steps.js"].split("const STEPS = [", 1)[1].split("];", 1)[0])
    screens = set(views) | set(steps) | {"packet", "log"}
    assert not screens - set(tables["HELP_FOR_VIEW"]), \
        f"feature screens with no help topic: {sorted(screens - set(tables['HELP_FOR_VIEW']))}"


def test_the_scout_reports_what_the_guides_leave_out_and_where_the_code_disagrees():
    scout = (ROOT / "factory" / "roles" / "scout.md").read_text()
    assert "only where no guide you were given speaks to it" in scout
    assert "Quote the guide's rule word for word" in scout
    assert "in_feature_area" in (ROOT / "factory" / "roles" / "interrogator.md").read_text()
    spec_writer = (ROOT / "factory" / "roles" / "spec_writer.md").read_text()
    assert "Not the repository's conventions" in spec_writer
    worker = (ROOT / "factory" / "roles" / "worker.md").read_text()
    assert "The scout listed conventions" not in worker, "the worker is told of a list it never sees"
    for role in ("architect", "harness", "surveyor", "worker", "reviewer", "scout", "spec_writer"):
        text = (ROOT / "factory" / "roles" / f"{role}.md").read_text()
        assert "approved guide" not in text, f"{role}.md still speaks of guides a person approved"


def test_every_project_tab_draws_its_own_heading_or_none():
    """A tab with no entry in the lede tables drew the word "undefined" above
    its heading -- the guides tab did, the day it was added."""
    app = app_js()
    steps = re.findall(r"id: '([\w-]+)'", app.split("const PROJECT_STEPS = [", 1)[1].split("];", 1)[0])
    heads = app.split("readingBehindStrip()}", 1)[1].split("SECTION_LEDE[sec]", 1)[0]
    titled = set(re.findall(r"^  (\w+): ", app.split("const SECTION_TITLE = {", 1)[1].split("};", 1)[0], re.M))
    for step in steps:
        assert f"'{step}'" in heads or step in titled, f"the {step} tab has no heading and no lede"


def test_the_packet_says_which_guides_the_feature_was_held_to():
    from factory.pipeline import guides_held

    records = [{"kind": "guides_delivered", "payload": {"role": "worker", "harness": True, "guides": [
                   {"path": "AGENTS.md", "sha256": "a", "partial": False}]}},
               {"kind": "guides_delivered", "payload": {"role": "reviewer", "guides": [
                   {"path": "AGENTS.md", "sha256": "a", "partial": True}]}}]
    assert guides_held(records) == [{"path": "AGENTS.md", "sha256": "a", "partial": True,
                                     "roles": ["reviewer"], "loader": ["worker"]}]
    assert "packet.guides = guides_held(store.records())" in class_source(pipeline.Factory)
    evidence = (ROOT / "console" / "ui" / "evidence.js").read_text()
    findings = (ROOT / "console" / "ui" / "findings.js").read_text()
    assert "Guides this feature was held to" in evidence
    assert "not in the guide — capped at minor" in findings


def test_the_guides_tab_shows_the_repository_layer_by_layer():
    app = app_js()
    assert "{ id: 'guides', label: 'guides', has: () => true }" in app, "guides have no tab of their own"
    assert "${sec === 'guides' ? guidesTab() : ''}" in app and "overviewTile('Guides'" in app
    tab = app.split("function guidesTab(", 1)[1].split("\n}\n", 1)[0]
    assert "GUIDE_LAYERS.map((l) => guideLayer(l, all))" in tab and "Fabrika approves nothing" in tab
    layer = app.split("function guideLayer(", 1)[1].split("\n}\n", 1)[0]
    assert "skillsDirPick()" in layer and "guideProposalRow(o.kind)" in layer \
        and "guideProposalRow('draft')" in layer
    for gone in ("data-guide-approve", "data-guide-exclude", "data-guide-reopen", "/guides/rule"):
        assert gone not in app, f"{gone}: an approval control is still on the page"
    assert 'data-skills-dir=' in app
    assert "guides_delivered:" in app and "skills_dir_set:" in app
    server = factory_source("server")
    for route in ("/guides/draft", "/guides/offer", "/guides/skills-dir"):
        assert f'"/api/projects/{{project_id}}{route}"' in server, route
    assert "/guides/rule" not in server and '"guides": guide_view(project)' in server


def test_a_proposed_guide_is_one_line_until_opened_and_reviewed_in_the_editor():
    """Each proposal is a collapsed line: what it does and the files it writes.
    Review opens the whole text in the same sheet an agent's prompt is written
    in; nothing is committed from the line, and what is committed is what the
    editor holds."""
    app = app_js()
    row = app.split("function guideProposalRow(", 1)[1].split("\n}\n", 1)[0]
    assert '<details class="gd-prop-line"><summary>' in row and "data-guide-review=" in row
    assert row.count("data-guide-commit") == 1 and "? `<button class=\"btn btn-sm btn-primary\" data-guide-commit=" in row \
        and "const onlyLinks = prop.files.every((f) => f.link);" in row, \
        "a proposal with text to read can be committed without being opened"
    sheet = app.split("function syncGuideSheet(", 1)[1].split("\n}\n", 1)[0]
    assert "mountMarkdownEditor(host" in sheet and "sheetHost.innerHTML = guideSheetHtml(kind, ed)" in sheet
    prompt = app.split("function mountPromptEditor(", 1)[1].split("\n}\n", 1)[0]
    assert "mountMarkdownEditor(host" in prompt, "the two editors have drifted apart"
    assert "if (syncGuideSheet()) return;" in app
    decide = app.split("function decideGuide(", 1)[1].split("\n}\n", 1)[0]
    assert "Object.fromEntries(ed.files.map((f) => [f.path, f.contents]))" in decide
    assert 'data-guide-commit="${esc(kind)}"' in app.split("function guideSheetHtml(", 1)[1].split("\n}\n", 1)[0]


def test_what_a_reader_typed_survives_the_page_rebuilding():
    app = app_js()
    assert "if (el.value !== el.defaultValue) typed.set(el.id, el.value);" in app
    assert app.index("typed.set(el.id") < app.index("main.innerHTML = html;")


def test_a_poll_asks_for_nothing_the_page_already_has():
    """The console polls as a fallback for what the progress stream does not
    say, and it polled regardless: in a tab nobody was looking at, and a moment
    after an event from the stream had already refreshed the same page."""
    import shutil
    import subprocess

    if not shutil.which("node"):
        pytest.skip("node is not installed")
    app = app_js()
    tick = app[app.index("function pollTick("):]
    tick = tick[:tick.index("\n}\n") + 3]
    script = tick + """
const asked = [];
const refresh = () => asked.push('feature'), refreshProject = () => asked.push('project');
const document = { hidden: false };
const state = { id: 'f', pollEvery: 4000, lastRefreshAt: Date.now() };
pollTick();                                   // the stream refreshed a moment ago
state.lastRefreshAt = Date.now() - 10000;
document.hidden = true; pollTick();           // nobody is looking
document.hidden = false; pollTick();          // stale, and on screen
state.id = null; state.lastRefreshAt = 0; pollTick();
console.log(JSON.stringify(asked));
"""
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == ["feature", "project"]


def test_a_stage_and_a_verdict_are_named_in_one_place():
    """Each stage's word, band, step and gate lived in its own table, eight of
    them across three files, and the review screens kept copies of their own --
    four sets of verdict words, three severity rankings. They drifted: an
    outcome read one way on one screen and another on the next, and a stage
    had a word on the rail and none on the start screen. app/vocab.js is the
    one table; everything else is derived from it."""
    files = sorted([*(ROOT / "console" / "app").glob("*.js"), *(ROOT / "console" / "ui").glob("*.js"),
                    ROOT / "console" / "asbuilt.js"])
    for word in ("'awaiting freeze'", "'Ships, with rulings", "blocker: 0", "'feature review · you'"):
        holders = [p.name for p in files if word in p.read_text(encoding="utf-8")]
        assert holders == ["vocab.js"], f"{word} is written in {holders}, not only in app/vocab.js"
    from support import CONSOLE_SCRIPTS
    assert CONSOLE_SCRIPTS[0] == "app/vocab.js", "the words load before anything reads them"
