"""The pipeline: the ledger, findings, traces, rework and the packet.

Split out of test_invariants.py, which keeps one test per invariant.
"""

from helpers import *  # noqa: F403


def test_the_rework_budget_is_a_lifetime_figure_per_feature(gate_two_factory):
    """A human who sends the same packet back four times must not thereby buy
    four budgets. That is the difference between a ceiling and a speed limit."""
    factory, feature = gate_two_factory
    store = factory.store_for(feature)

    before = factory.rework_budget(feature)
    assert before["spent_usd"] == 0.0
    assert before["dispatches"] == 0
    # The reserve is never spendable by the loop.
    assert before["spendable_usd"] == pytest.approx(
        before["limit_usd"] - before["reserve_usd"])
    assert before["remaining_usd"] == before["spendable_usd"] > 0
    assert not before["exhausted"]

    # Two dispatches, each recorded the way `_converge` records one.
    chunk = before["spendable_usd"] * 0.6
    store.append("rework_spend", {"usd": chunk, "round": 1}, role="orchestrator")
    mid = factory.rework_budget(feature)
    assert mid["spent_usd"] == pytest.approx(chunk)
    assert mid["remaining_usd"] == pytest.approx(before["spendable_usd"] - chunk)
    assert not mid["exhausted"]

    store.append("rework_spend", {"usd": chunk, "round": 2}, role="orchestrator")
    after = factory.rework_budget(feature)

    # Summed across dispatches rather than reset by each one. Had the budget
    # been per-invocation, two 60% spends would both have been affordable.
    assert after["spent_usd"] == pytest.approx(2 * chunk)
    assert after["dispatches"] == 2
    assert after["exhausted"]
    assert after["remaining_usd"] == 0.0


def test_a_dispatch_is_refused_once_the_lifetime_budget_is_gone(gate_two_factory):
    """The ceiling has to actually stop something, or it is decoration."""
    from factory.pipeline import ProjectError

    factory, feature = gate_two_factory
    factory.record_flag(feature, "print() in a codebase with none", disposition="repair")
    budget = factory.rework_budget(feature)
    factory.store_for(feature).append(
        "rework_spend", {"usd": budget["spendable_usd"], "round": 1}, role="orchestrator")

    assert factory.rework_budget(feature)["exhausted"]
    with pytest.raises(ProjectError, match="spent its rework budget"):
        asyncio.run(factory.send_to_repair(feature))


# ==========================================================================
# V-9 -- the adversary merge
# ==========================================================================


def test_v9_findings_dedupe_and_the_worst_verdict_wins():
    """Applies to samples of one agent and across every review agent alike."""
    shared = "Deploy triggers mass irreversible deletion with no audit trail"
    reports = [
        ReviewReport(
            summary="one angle", verdict="accept_with_changes",
            strongest_objection="the audit record is missing",
            findings=[
                Finding(id="A-1", severity="major", title=shared, detail="one reading"),
                Finding(id="A-2", severity="minor", title="dry run count can drift", detail="d"),
            ],
            conceded=["batching is sound"],
        ),
        ReviewReport(
            summary="another angle", verdict="reject",
            strongest_objection="this destroys customer data on deploy",
            findings=[
                Finding(id="A-1", severity="blocker", title=shared + ".", detail="harsher reading"),
                Finding(id="A-3", severity="major", title="scheduler token widened", detail="d"),
            ],
            conceded=["batching is sound", "the migration is reversible"],
        ),
    ]

    merged = merge_reviews(reports)

    titles = [f.title.rstrip(".") for f in merged.findings]
    assert titles.count(shared) == 1, "the same objection from two passes is one finding"
    assert len(merged.findings) == 3, "an objection raised even once is kept"

    survivor = next(f for f in merged.findings if f.title.rstrip(".") == shared)
    assert survivor.severity == "blocker", "the harsher reading survives"

    assert merged.verdict == "reject", "the worst verdict wins"
    assert merged.strongest_objection == "this destroys customer data on deploy"
    assert merged.conceded == ["batching is sound", "the migration is reversible"]
    assert [f.severity for f in merged.findings] == ["blocker", "major", "minor"]


def test_v9_an_empty_ensemble_does_not_invent_a_verdict():
    merged = merge_reviews([])
    assert merged.verdict == "accept"
    assert merged.findings == []


def test_review_agents_are_not_special_cased_in_the_orchestrator():
    """The reviewer and the adversary must be called the same way as any agent
    you add: by iterating config, not by name."""
    source = strip_prose(factory_source("pipeline"))
    for name in ('"reviewer"',):
        assert f"llm.ask({name}" not in source and f"ask({name}," not in source, \
            f"{name} is still called by literal name; it should come from config.review_roles()"
    assert "self.config.review_roles()" in source


def test_worker_disclosure_is_four_separate_lists():
    fields = SelfDisclosure.model_fields
    assert set(fields) == {"not_implemented", "assumptions", "deviations_from_spec", "flags"}
    for name, field in fields.items():
        annotation = str(field.annotation)
        assert "list" in annotation, f"{name} must be a list, not prose"


def test_an_empty_disclosure_is_surfaced_as_a_signal():
    silent = WorkerOutput(unit_id="U-9", summary="built it", disclosure=SelfDisclosure())
    lines = pipeline.collect_disclosures([silent])
    assert len(lines) == 1
    assert "disclosed nothing at all" in lines[0]


def test_a_review_role_cannot_reach_the_verify_lane(editable_config):
    """Review agents contribute findings. They must not be able to reach the
    verify lane or the control flow."""
    from factory.config import write_role

    config, _ = editable_config
    write_role(config, "security", {"model": "x/y", "review": True})

    pipeline_source = strip_prose(factory_source("pipeline"))
    verify_lane = code_without_prose(pipeline.Factory._verify_lane)
    assert "review_roles" not in verify_lane, "a review agent must not be reachable from the verify lane"
    assert "review_roles" in pipeline_source, "review agents come from config, not from literals"


def test_every_agent_exchange_records_both_halves(tmp_path):
    """INV-2 says every agent *exchange* is written. An exchange has two halves,
    and for a while this kept only one: you could see what an agent said and
    never what it was shown, which makes "why did it conclude that" unanswerable."""
    from factory.store import EvidenceStore

    evidence = EvidenceStore(tmp_path, "feature-1")
    record = evidence.append(
        "scout", {"summary": "a repo"}, role="scout", model="x/y",
        prompt="# Intent\n\nbuild a thing\n\n---\n\n<the whole digest>",
    )

    meta = record["meta"]
    assert meta["prompt_chars"] == len("# Intent\n\nbuild a thing\n\n---\n\n<the whole digest>")
    stored = evidence.dir / meta["prompt_file"]
    assert stored.exists(), "the prompt is on disk, not inline -- a scout prompt is 120k characters"
    assert "the whole digest" in stored.read_text()

    # and the ledger itself stays readable
    line = evidence.file.read_text()
    assert "the whole digest" not in line


def test_merging_scouts_loses_nothing(tmp_path):
    """Deterministic on purpose: a model asked to merge nine reports is a model
    with the opportunity to drop the one entry that mattered."""
    from factory.pipeline import merge_scouts
    from factory.schemas import ObservedConvention, ScoutReport

    reports = [
        ScoutReport(summary="the backend", observed_conventions=[ObservedConvention(
                        rule="routers live in app/routers", slug="routers-dir", files=["app/a.py"])],
                    do_not_duplicate=["app/db.py:get_session()"], risks=["shared Trial model"]),
        ScoutReport(summary="the frontend", observed_conventions=[ObservedConvention(
                        rule="Routers live in app/routers.", slug="routers-dir", files=["app/b.py"])],
                    do_not_duplicate=["src/api/client.ts:request()"], risks=["shared Trial model"]),
    ]
    merged = merge_scouts(reports)

    assert merged.do_not_duplicate == ["app/db.py:get_session()", "src/api/client.ts:request()"]
    assert len(merged.observed_conventions) == 1, "the same convention said twice collapses"
    assert merged.observed_conventions[0].files == ["app/a.py", "app/b.py"], \
        "the files it holds in, from both slices"
    assert len(merged.risks) == 1
    assert "the backend" in merged.summary and "the frontend" in merged.summary


def test_an_environment_that_cannot_run_its_gates_is_caught_at_survey_time(tmp_path):
    """A gate the image cannot run is red on every feature forever, and reads to
    a human as the feature having broken something."""
    from factory.projects import ProjectRegistry
    from factory.schemas import EnvironmentSpec, Gate

    check = ProjectRegistry.environment_problems

    assert check(EnvironmentSpec(kind="reuse", image=""), []) , \
        "'reuse' naming no image cannot start a container"
    assert not check(EnvironmentSpec(kind="reuse", image="myorg/dev:1"), [])

    assert check(EnvironmentSpec(kind="derive", dockerfile=""), []), \
        "'derive' with no Dockerfile builds nothing"

    copied = check(EnvironmentSpec(kind="generate", dockerfile="FROM python:3.12\nCOPY . /app\n"), [])
    assert any("shadow" in x for x in copied), "a baked source tree is shadowed by the mount"

    python_only = EnvironmentSpec(kind="generate", dockerfile="FROM python:3.12-slim\n")
    node_gate = [Gate(name="types-frontend", command="npm run type-check")]
    problems = check(python_only, node_gate)
    assert any("npm" in x and "types-frontend" in x for x in problems)

    with_node = EnvironmentSpec(
        kind="generate", dockerfile="FROM python:3.12-slim\nRUN apt-get install -y nodejs\n",
    )
    assert not check(with_node, node_gate), "and satisfied when the tool is actually installed"


def test_a_project_with_a_running_feature_cannot_be_unregistered(tmp_path):
    import subprocess

    from factory.config import Config, PathsConfig, RoleConfig
    from factory.pipeline import Factory
    from factory.projects import ProjectError, ProjectRegistry
    from factory.schemas import Gate, GateReport, GateResult

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
                   cwd=repo, check=True)

    config = Config(
        roles={"scout": RoleConfig(name="scout", model="x/y")},
        paths=PathsConfig(evidence=str(tmp_path / "e"), sandboxes=str(tmp_path / "s")),
    )
    registry = ProjectRegistry(config.evidence_path)
    project = registry.create(repo, "demo")
    project.state.stage = "ready"
    project.state.gates = [Gate(name="t", command="true")]
    project.state.baseline = GateReport(results=[GateResult(name="t", passed=True)])
    registry.save(project)

    Factory(config, registry.get("demo")).create_feature("build it", "Thing")

    with pytest.raises(ProjectError) as caught:
        registry.delete("demo", sandbox_root=config.sandbox_path)
    assert "still running" in str(caught.value)
    assert registry.exists("demo"), "nothing was removed"


def test_scaffolding_may_install_a_runner_but_never_write_a_test(tmp_path):
    """pytest exits 5 with no tests and 0 with one trivial one. A factory that
    can write that one test can turn any red baseline green while proving
    nothing, so the rule lives in code rather than in a prompt."""
    from factory.projects import ProjectError, ProjectRegistry
    from factory.schemas import ProjectSurvey, EnvironmentSpec, ScaffoldFile

    runner = ScaffoldFile(path="backend/requirements-dev.txt", contents="pytest\n", purpose="tests")
    a_test = ScaffoldFile(path="backend/tests/test_smoke.py", contents="def test_x(): pass", purpose="x")
    nested = ScaffoldFile(path="src/__tests__/thing.spec.ts", contents="it('x', () => {})", purpose="x")
    escape = ScaffoldFile(path="../outside.txt", contents="", purpose="x")

    assert ProjectRegistry.scaffold_problems([runner]) == []
    for bad in (a_test, nested):
        problems = ProjectRegistry.scaffold_problems([bad])
        assert problems and "may not write tests" in problems[0]
    assert "repo-relative" in ProjectRegistry.scaffold_problems([escape])[0]

    # A marker file under a test directory is not a test, and the rule has to
    # know the difference. `frontend/e2e/.gitkeep` was refused for the name of
    # the directory above it, and took the two good files in its batch with it.
    # Git tracks no empty directory, every gate runs in a worktree carrying
    # tracked files only, and a Playwright testDir absent from the sandbox
    # collects nothing -- so the runner beside it cannot work without this.
    keep = ScaffoldFile(path="frontend/e2e/.gitkeep",
                        contents="# Playwright's testDir. One file per feature.\n", purpose="testDir")
    assert ProjectRegistry.scaffold_problems([keep]) == [], (
        "no runner collects a .gitkeep, so it cannot be the thing that makes a gate green")

    # The exception is the name and nothing else. A real test in the same
    # directory still goes, and so does a source file wearing a marker's clothes.
    filled = ScaffoldFile(path="frontend/e2e/nav.spec.ts", contents="test('x', () => {})", purpose="x")
    assert "may not write tests" in ProjectRegistry.scaffold_problems([filled])[0]
    sneaky = ScaffoldFile(path="frontend/e2e/nav.ts", contents="test('x', () => {})", purpose="x")
    assert "may not write tests" in ProjectRegistry.scaffold_problems([sneaky])[0], (
        "the exception widened past the names no runner collects")

    # and the refusal holds at the point of writing, not only at survey time
    repo = tmp_path / "repo"
    repo.mkdir()
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")
    project.state.survey = ProjectSurvey(
        name="demo", summary="s", environment=EnvironmentSpec(kind="host"),
        scaffolding=[runner, a_test],
    )
    registry.save(project)

    with pytest.raises(ProjectError) as caught:
        registry.apply_scaffolding(project, ["backend/tests/test_smoke.py"])
    assert "may not write tests" in str(caught.value)
    assert not (repo / "backend" / "tests").exists()

    result = registry.apply_scaffolding(project, ["backend/requirements-dev.txt"])
    assert result["written"] == ["backend/requirements-dev.txt"]
    assert (repo / "backend" / "requirements-dev.txt").read_text() == "pytest\n"


def test_scaffolding_never_overwrites_what_is_already_there(tmp_path):
    from factory.projects import ProjectRegistry
    from factory.schemas import ProjectSurvey, EnvironmentSpec, ScaffoldFile

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pytest.ini").write_text("# mine\n")

    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")
    project.state.survey = ProjectSurvey(
        name="demo", summary="s", environment=EnvironmentSpec(kind="host"),
        scaffolding=[ScaffoldFile(path="pytest.ini", contents="# theirs\n", purpose="x")],
    )
    registry.save(project)

    result = registry.apply_scaffolding(project, ["pytest.ini"])
    assert result["written"] == []
    assert result["skipped"] and "already exists" in result["skipped"][0]
    assert (repo / "pytest.ini").read_text() == "# mine\n"


def test_a_tool_hidden_behind_cd_is_still_found(tmp_path):
    """`cd frontend && npx tsc` is how gate commands are actually written. A
    check that only reads the first token sees `cd` and passes everything."""
    from factory.projects import ProjectRegistry
    from factory.schemas import EnvironmentSpec, Gate

    python_only = EnvironmentSpec(kind="generate", dockerfile="FROM python:3.12-slim\n")
    hidden = [Gate(name="frontend-typecheck", command="cd frontend && npx tsc --noEmit")]

    problems = ProjectRegistry.environment_problems(python_only, hidden)
    assert any("npx" in x and "frontend-typecheck" in x for x in problems)

    with_node = EnvironmentSpec(
        kind="generate",
        dockerfile="FROM python:3.12-slim\nRUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash -\n",
    )
    assert ProjectRegistry.environment_problems(with_node, hidden) == [], \
        "and satisfied once something actually installs it"

    # a bare word that merely contains a tool name is not an invocation
    innocent = [Gate(name="tests", command="python -m pytest tests/test_nodes.py")]
    assert ProjectRegistry.environment_problems(python_only, innocent) == []


def test_a_declared_change_that_was_never_built_becomes_a_finding(tmp_path):
    """What makes the spec's declared shape worth having: a paragraph cannot be
    checked against the code, and a declared column can."""
    from factory.pipeline import check_declared_changes
    from factory.schemas import (
        AcceptanceCriterion, DataChange, FileWrite, InterfaceChange, PlannedChanges, Spec,
    )

    spec = Spec(
        title="t", intent="i", summary="s",
        acceptance_criteria=[AcceptanceCriterion(id="AC-1", statement="x")],
        changes=PlannedChanges(
            data=[
                DataChange(operation="add", table="trial_participations",
                           column="screening_capacity", type="INTEGER"),
                DataChange(operation="add", table="trial_participations",
                           column="screening_window_end", type="DATE"),
            ],
            # Explicitly `add`: an `alter` absent from the diff proves nothing,
            # which is what the test below asserts.
            interface=[InterfaceChange(operation="add", method="PATCH",
                                       path="/trials/{id}/slots",
                                       change="accepts the three fields")],
        ),
    )

    built = [FileWrite(path="m.py", contents="op.add_column('trial_participations', "
                                            "sa.Column('screening_capacity', sa.Integer))\n")]
    findings = check_declared_changes(spec, built)

    titles = " ".join(f.title for f in findings)
    assert "screening_window_end" in titles, "a declared column that never appeared is reported"
    assert "screening_capacity" not in titles, "and one that did is not"
    assert "slots" in titles, "nor is a declared endpoint that was never built"
    assert all(f.category == "spec-drift" for f in findings)
    assert all(f.severity == "major" for f in findings)


def test_a_spec_that_declares_nothing_is_not_second_guessed(tmp_path):
    """Silence is allowed on purpose: an invented column would produce a false
    finding, which is worse than a missing one."""
    from factory.pipeline import check_declared_changes
    from factory.schemas import FileWrite, Spec

    spec = Spec(title="t", intent="i", summary="s")
    assert spec.changes is None
    assert check_declared_changes(spec, [FileWrite(path="a.py", contents="x = 1")]) == []


def test_a_correction_voids_the_spec_and_answers_that_preceded_it():
    """The ledger is append-only, so a sent-back spec is still the last spec
    record on disk. Currency is about ordering, not recency."""
    from factory.pipeline import current_payload

    records = [
        {"seq": 22, "kind": "spec", "payload": {"title": "first"}},
        {"seq": 24, "kind": "correction", "payload": "you misread it"},
        {"seq": 30, "kind": "answers", "payload": {"resolved": [{"question_id": "multi-site"}]}},
        {"seq": 34, "kind": "spec", "payload": {"title": "second"}},
        {"seq": 37, "kind": "correction", "payload": "still wrong"},
        {"seq": 40, "kind": "interrogation", "payload": {"ambiguities": []}},
    ]

    assert current_payload(records, "spec") is None, "both specs predate the last correction"
    assert current_payload(records, "answers") is None, "so do the answers"
    assert current_payload(records, "interrogation") == {"ambiguities": []}, "this one is newer"

    # Once the spec writer runs again, the new spec is current and the old one stays buried.
    records.append({"seq": 44, "kind": "spec", "payload": {"title": "third"}})
    assert current_payload(records, "spec") == {"title": "third"}


def test_currency_is_unaffected_when_nothing_was_corrected():
    from factory.pipeline import current_payload

    records = [
        {"seq": 1, "kind": "spec", "payload": {"title": "a"}},
        {"seq": 2, "kind": "spec", "payload": {"title": "b"}},
    ]
    assert current_payload(records, "spec") == {"title": "b"}
    assert current_payload([], "spec") is None


def test_a_declared_drop_is_checked_at_all():
    """It matched no branch before: the most destructive operation in the
    vocabulary was the only one gate 2 never looked at."""
    from factory.pipeline import check_declared_changes
    from factory.schemas import DataChange, FileWrite, PlannedChanges, Spec

    spec = Spec(title="t", intent="i", summary="s", changes=PlannedChanges(
        data=[DataChange(operation="drop", table="tenant_configs", column="legacy_flag")]))

    built_nothing = [FileWrite(path="m.py", contents="pass\n")]
    titles = " ".join(f.title for f in check_declared_changes(spec, built_nothing))
    assert "legacy_flag" in titles, "a drop that never happened is now reported"


def test_a_change_built_backwards_against_its_own_spec_is_a_finding():
    """Name presence cannot tell an add from a drop, so a build doing the exact
    opposite of the spec used to pass silently."""
    from factory.pipeline import check_declared_changes
    from factory.schemas import DataChange, FileWrite, PlannedChanges, Spec

    spec = Spec(title="t", intent="i", summary="s", changes=PlannedChanges(
        data=[DataChange(operation="drop", table="tenant_configs", column="legacy_flag")]))

    wrong_way = [FileWrite(path="m.py",
                           contents="op.add_column('tenant_configs', sa.Column('legacy_flag'))")]
    found = check_declared_changes(spec, wrong_way)
    assert any("opposite" in f.title for f in found), "declared a drop, built an add"

    right_way = [FileWrite(path="m.py", contents="op.drop_column('tenant_configs', 'legacy_flag')")]
    assert check_declared_changes(spec, right_way) == [], "and the correct migration is silent"


def test_an_altered_endpoint_absent_from_the_diff_is_not_reported():
    """A response schema can change without the route line moving, so absence
    from what was written proves nothing about an alteration."""
    from factory.pipeline import check_declared_changes
    from factory.schemas import FileWrite, InterfaceChange, PlannedChanges, Spec

    altered = Spec(title="t", intent="i", summary="s", changes=PlannedChanges(
        interface=[InterfaceChange(operation="alter", method="GET",
                                   path="/api/trials/{id}/overview", change="gains slots")]))
    assert check_declared_changes(altered, [FileWrite(path="s.py", contents="x = 1")]) == []

    added = altered.model_copy(deep=True)
    added.changes.interface[0].operation = "add"
    assert check_declared_changes(added, [FileWrite(path="s.py", contents="x = 1")]), (
        "but a new endpoint that appears nowhere still is")


def test_irreversibility_is_derived_from_the_operation_not_asked_for():
    from factory.pipeline import irreversible_changes
    from factory.schemas import (
        DataChange, InterfaceChange, PlannedChanges, Spec, SurfaceChange,
    )

    spec = Spec(title="t", intent="i", summary="s", changes=PlannedChanges(
        data=[DataChange(operation="drop", table="tenant_configs", column="legacy_flag"),
              DataChange(operation="add", table="trials", column="capacity")],
        interface=[InterfaceChange(operation="remove", method="DELETE", path="/api/old",
                                   change="gone"),
                   InterfaceChange(operation="alter", method="GET", path="/api/keep", change="x")],
        surfaces=[SurfaceChange(operation="remove", where="LegacyPanel", change="gone")]))

    assert irreversible_changes(spec) == [
        "tenant_configs.legacy_flag", "DELETE /api/old", "LegacyPanel"]
    assert irreversible_changes(Spec(title="t", intent="i", summary="s")) == []


def test_two_units_writing_one_file_is_reported_not_silently_resolved():
    """_apply_writes calls write_text per entry, so the last author of a path
    wins and the earlier unit's work vanishes with no conflict and no record."""
    from factory.pipeline import check_write_collisions
    from factory.schemas import FileWrite, IntegrationReport, WorkerOutput

    workers = [
        WorkerOutput(unit_id="schema", summary="s",
                     files=[FileWrite(path="app/models/trial.py", contents="A"),
                            FileWrite(path="app/seed.py", contents="seed")]),
        WorkerOutput(unit_id="api", summary="s",
                     files=[FileWrite(path="app/models/trial.py", contents="B"),
                            FileWrite(path="app/routers/trials.py", contents="r")]),
    ]

    found = check_write_collisions(workers)
    assert len(found) == 1, "only the shared path collides"
    assert "app/models/trial.py" in found[0].title
    assert "'schema'" in found[0].detail and "'api'" in found[0].detail
    assert "only the last one survived" in found[0].detail
    assert found[0].severity == "major"

    # The integrator rewriting a unit's file is its job, not a collision.
    settled = check_write_collisions(
        workers, IntegrationReport(summary="merged",
                                   files=[FileWrite(path="app/models/trial.py", contents="C")]))
    assert len(settled) == 1, "still worth telling a human two units disagreed"
    assert "the integrator rewrote this path" in settled[0].detail.lower()
    assert "only the last one survived" not in settled[0].detail

    assert check_write_collisions([]) == []
    assert check_write_collisions([workers[0]]) == [], "one unit cannot collide with itself"


def test_the_rapporteur_orders_by_id_and_cannot_reword_on_the_way_past():
    """It used to be handed the Packet schema, which made it restate every
    finding it wanted to rank -- so it could also quietly reword one, and it
    spent its whole output budget doing it."""
    from factory.pipeline import order_packet, restore_packet
    from factory.schemas import Decision, Finding, PacketJudgement

    decisions = [Decision(id="U-1/D-1", title="first", rationale="r"),
                 Decision(id="U-2/D-1", title="second", rationale="r"),
                 Decision(id="U-2/D-2", title="never ranked", rationale="r")]
    findings = [Finding(id="rev-1", severity="blocker", title="the real problem", detail="d"),
                Finding(id="adv-1", severity="minor", title="a nit", detail="d"),
                Finding(id="hack-1", severity="major", title="left out entirely", detail="d")]

    judgement = PacketJudgement(
        headline="h",
        decision_order=["U-2/D-1", "U-1/D-1", "does-not-exist"],
        finding_order=["adv-1", "rev-1"],
    )
    packet = order_packet(judgement, decisions, findings)

    assert [d.id for d in packet.decisions] == ["U-2/D-1", "U-1/D-1"], "its ranking is honoured"
    assert [d.rank for d in packet.decisions] == [1, 2]
    assert [f.id for f in packet.findings] == ["adv-1", "rev-1"]
    assert all(f.title == next(x.title for x in findings if x.id == f.id) for f in packet.findings), (
        "text comes from the agent that raised it, never from the presenter")

    # And omission is still not available to it.
    whole = restore_packet(packet, decisions=decisions, findings=findings, trace=[])
    assert {d.id for d in whole.decisions} == {d.id for d in decisions}
    assert {f.id for f in whole.findings} == {f.id for f in findings}


def test_the_rapporteur_is_not_asked_to_restate_what_the_pipeline_already_holds():
    """The trace, the stats, the disclosures and the open questions are all
    overwritten by restore_packet. Asking for them costs output and buys
    nothing, which is what truncated the packet on a 27B."""
    from factory.schemas import PacketJudgement

    asked = set(PacketJudgement.model_fields)
    for field in ("trace", "stats", "disclosures", "open_questions"):
        assert field not in asked, (
            f"{field!r} is computed by the pipeline and overwritten after the call; "
            "asking the model to produce it spends output on an answer nobody reads")
    for field in ("decisions", "findings"):
        assert field not in asked, (
            f"{field!r} must be ordered by id, not restated -- restating lets the "
            "presenter reword what another agent said")


def test_a_resume_reuses_only_work_done_against_the_current_spec():
    """The guard that matters: a spec that was re-planned invalidates every
    artifact built against the old one, or a resume silently assembles a packet
    out of work for a different specification."""
    from factory.pipeline import recorded_attempt

    records = [
        {"seq": 10, "kind": "plan", "spec_hash": "OLD", "payload": {"units": []}},
        {"seq": 11, "kind": "worker", "spec_hash": "OLD", "payload": {"unit_id": "a"}},
        {"seq": 12, "kind": "gates", "spec_hash": "OLD", "payload": {"results": []}},
        {"seq": 20, "kind": "plan", "spec_hash": "NEW", "payload": {"units": [1]}},
        {"seq": 21, "kind": "worker", "spec_hash": "NEW", "payload": {"unit_id": "b"}},
        {"seq": 22, "kind": "review", "role": "hacker", "spec_hash": "NEW",
         "payload": {"summary": "s"}},
    ]

    resumed = recorded_attempt(records, "NEW")
    assert resumed["plan"] == {"units": [1]}
    assert [w["unit_id"] for w in resumed["workers"]] == ["b"], "no work from the old spec"
    assert "gates" not in resumed, "the old attempt's gates are not this spec's gates"
    assert [role for role, _ in resumed["reviews"]] == ["hacker"]

    assert recorded_attempt(records, "OLD")["plan"] == {"units": []}
    assert recorded_attempt(records, "NEVER-BUILT") == {}
    assert recorded_attempt(records, "") == {}, "no spec hash means nothing is resumable"


def test_a_resume_takes_nothing_from_an_attempt_that_never_planned():
    """Artifacts before the plan belong to the intake, not to a build."""
    from factory.pipeline import recorded_attempt

    assert recorded_attempt([
        {"seq": 1, "kind": "scout", "spec_hash": "H", "payload": {}},
        {"seq": 2, "kind": "spec", "spec_hash": "H", "payload": {}},
    ], "H") == {}


def test_starting_a_build_clears_the_previous_attempt_s_error():
    """A stale failure string outlives the build that succeeded, so a finished
    feature reads as a broken one. The phases say what happened; `state.error`
    describes only the attempt that is running."""
    import ast
    import inspect
    from factory.pipeline import Factory

    source = textwrap.dedent(inspect.getsource(Factory.run_build))
    tree = ast.parse(source)

    # Find `state.error = ""` before the try/except that runs the build.
    body = tree.body[0].body
    cleared_before_try = False
    for node in body:
        if isinstance(node, ast.Try):
            break
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Assign)
                    and any(isinstance(t, ast.Attribute) and t.attr == "error" for t in sub.targets)
                    and isinstance(sub.value, ast.Constant) and sub.value.value == ""):
                cleared_before_try = True
    assert cleared_before_try, (
        "run_build must clear state.error before it starts, or the last failure "
        "survives a successful run")


def test_the_integrator_is_given_a_merged_tree_not_a_description_of_one():
    """It used to run before anything reached disk, so it reasoned about a merge
    that had not happened and had to emit corrected whole files. Landing the
    units first is what lets it edit what is actually there."""
    import ast
    import inspect
    from factory.pipeline import Factory

    source = textwrap.dedent(inspect.getsource(Factory._build_lane))
    tree = ast.parse(source)

    apply_line = phase_lines = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_apply_writes"):
            apply_line = node.lineno
        # The integrator is its own step now, so a resume can run it alone;
        # the build lane reaches it by calling it.
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_integrate"):
            phase_lines = node.lineno

    assert apply_line is not None, "the build lane must merge the units onto the branch"
    assert phase_lines is not None, "the integrator phase must be in the build lane"
    assert apply_line < phase_lines, (
        "the units must be written before the integrator phase opens, or it is "
        "back to reasoning about a tree that does not exist")


def test_exit_code_is_a_sentinel_not_a_pattern():
    """It was compiled as a regex, matched nothing, and failed the gate -- so a
    passing command reported as a failure on every gate in the project."""
    import re
    from factory.gates import EXIT_CODE_METRIC
    from factory.schemas import Gate

    assert EXIT_CODE_METRIC == "exit_code"
    # It is not a regex anyone could mean: it matches only the literal words.
    assert re.search(EXIT_CODE_METRIC, "3 passed, 0 failed") is None
    assert Gate(name="t", command="pytest", parse_metric="exit_code").parse_metric == "exit_code"


def test_a_unit_that_did_not_do_its_work_is_reported():
    """frontend-types-ui owned three files and wrote one, leaving AC-11 and
    AC-13 unbuilt. That silence is what tempted the integrator to reach outside
    its remit and destroy two files closing the gap."""
    from factory.pipeline import check_unit_delivery
    from factory.schemas import FileWrite, Plan, SelfDisclosure, WorkerOutput, WorkUnit

    plan = Plan(summary="s", units=[
        WorkUnit(id="frontend-types-ui", title="t", objective="o",
                 criterion_ids=["AC-11", "AC-12", "AC-13"],
                 files_expected=["types/index.ts", "pages/TrialDetail.tsx", "pages/Queue.tsx"]),
        WorkUnit(id="backend", title="t", objective="o",
                 files_expected=["app/routers/trials.py"]),
    ])
    workers = [
        WorkerOutput(unit_id="frontend-types-ui", summary="s",
                     files=[FileWrite(path="pages/TrialDetail.tsx", contents="x")]),
        WorkerOutput(unit_id="backend", summary="s",
                     files=[FileWrite(path="app/routers/trials.py", contents="x")]),
    ]

    found = check_unit_delivery(plan, workers)
    assert len(found) == 1, "only the unit that fell short"
    # The title is about the missing software; which unit was supposed to
    # write it is bookkeeping, and lives in the detail. "unit 'U-2' owned 14
    # file(s) and wrote 13" made a reviewer do the subtraction and then still
    # ask what it cost.
    # Two files, so the count leads and the detail names them: eleven paths
    # joined by commas is a paragraph in the position a title occupies.
    assert found[0].title == "Files the plan called for were never written — 2 of them"
    assert "types/index.ts" in found[0].detail and "pages/Queue.tsx" in found[0].detail
    assert "frontend-types-ui" in found[0].detail, "nothing says which unit fell short"
    assert found[0].severity == "major", "it said nothing about the gap"
    # The unit's criteria are evidence, not the claim. Read as a sentence they
    # were fourteen ids on the run this came from, thirteen of which had
    # nothing to do with the file that was missing.
    assert "AC-11" in found[0].evidence
    assert "AC-11" not in found[0].detail, \
        "the detail is reciting the plan instead of saying what happened"
    assert "said nothing about" in found[0].detail, \
        "a unit that skipped a file silently is not reported as having done so"

    # The paths as a field, not only inside a sentence. Every screen in the
    # review joins findings to criteria, diffs and the rapporteur's account of
    # a file through `Finding.files`, and a finding about a missing file that
    # named no file reached none of them.
    assert found[0].files == ["types/index.ts", "pages/Queue.tsx"], \
        "a finding about two files does not name them where anything can join on it"

    # A unit that admits what it skipped is still reported, less loudly.
    workers[0] = workers[0].model_copy(update={"disclosure": SelfDisclosure(
        not_implemented=["index.ts: the ScreeningSlot interface was not added",
                         "Queue.tsx: no slots line on the task card"])})
    honest = check_unit_delivery(plan, workers)
    assert len(honest) == 1 and honest[0].severity == "minor", (
        "disclosure does not make the work done, but it is not the same failure")

    # And what it said comes with it, verbatim. The unit had already explained
    # itself and nothing carried that across, so the only reasoned sentence
    # about the gap reached the packet as an arbiter's rebuttal to an argument
    # the packet never showed.
    assert "the ScreeningSlot interface was not added" in honest[0].detail, \
        "the unit's own account of the gap is dropped on the way to the packet"
    assert "no slots line on the task card" in honest[0].detail


def test_a_unit_that_delivered_everything_is_silent():
    from factory.pipeline import check_unit_delivery
    from factory.schemas import FileWrite, Plan, WorkerOutput, WorkUnit

    plan = Plan(summary="s", units=[
        WorkUnit(id="u", title="t", objective="o", files_expected=["a.py", "b.py"])])
    workers = [WorkerOutput(unit_id="u", summary="s", files=[
        FileWrite(path="a.py", contents="x"), FileWrite(path="b.py", contents="y")])]
    assert check_unit_delivery(plan, workers) == []


def test_the_units_are_committed_before_the_integrator_opens_its_worktree():
    """A nested worktree is created from HEAD. Anything merged but uncommitted
    is invisible to it, which would put the integrator back to reasoning about
    a tree that does not contain the work it is integrating."""
    import ast
    import inspect
    from factory.pipeline import Factory

    tree = ast.parse(textwrap.dedent(inspect.getsource(Factory._build_lane)))
    commit_line = integrator_line = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "commit"):
            commit_line = node.lineno
        # The integrator is its own step now, so a resume can run it alone;
        # the build lane reaches it by calling it.
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_integrate"):
            integrator_line = node.lineno

    assert commit_line is not None, "the merged units must be committed, not merely written"
    assert integrator_line is not None
    assert commit_line < integrator_line


def test_a_harness_run_records_what_it_was_shown():
    """INV-2 wants both halves of every exchange. The harness makes its own
    turns, but the brief it was handed and the diff it was asked to account for
    are ours -- and without them a unit that fails is unaskable. Every other
    agent's prompt was on disk; the workers' never were."""
    import inspect
    from factory.pipeline import Factory

    lane = inspect.getsource(Factory._build_lane) + inspect.getsource(Factory._integrate)
    assert "on_prompt" in lane, "the executor must hand its prompts back"
    assert "worker_prompt" in lane, "and they must reach the ledger"
    assert "integration_prompt" in lane, "integration is an exchange too"


def test_a_compose_environment_that_names_nothing_is_refused(tmp_path):
    import asyncio
    import pytest as _pytest
    from factory.config import load_config
    from factory.containers import DockerError, runner_for
    from factory.projects import Project
    from factory.schemas import EnvironmentSpec, ProjectState

    repo = tmp_path / "repo"; repo.mkdir()
    for spec, why in (
        (EnvironmentSpec(kind="compose", compose_service="api"), "no compose_file"),
        (EnvironmentSpec(kind="compose", compose_file="nope.yml", compose_service="api"),
         "a file that is not there"),
    ):
        project = Project(
            state=ProjectState(project_id="p", name="p", repo=str(repo),
                               base_ref="main", environment=spec),
            evidence_root=tmp_path / "ev")
        with _pytest.raises(DockerError):
            asyncio.run(runner_for(project, load_config(), feature_id="f"))


def test_inv11_a_repaired_finding_stays_in_the_packet():
    from factory.pipeline import FindingLedger

    ledger = FindingLedger()
    ledger.add("reviewer", [_finding("duplicated helper"), _finding("naming", "minor")], 0)
    ledger.add("adversary", [_finding("no stop path", "blocker")], 0)
    assert len(ledger.all_findings()) == 3

    ledger.dispose([
        FindingDisposition(finding_id="reviewer-1", disposition="repair", reason="mechanical"),
    ])
    ledger.attempted(["reviewer-1"], 1)
    ledger.apply_verdicts(
        [RepairVerdict(finding_id="reviewer-1", status="fixed", evidence="gone")],
        round_index=1, commit="abc123", max_attempts=2,
    )
    ledger.finalise(stopped_early=False)

    assert len(ledger.all_findings()) == 3, "fixing a finding must not remove it"
    outcomes = {r.finding_id: r.outcome for r in ledger.all_records()}
    assert outcomes["reviewer-1"] == "repaired"
    assert outcomes["reviewer-2"] == "escalated"
    assert outcomes["adversary-1"] == "escalated"
    record = next(r for r in ledger.all_records() if r.finding_id == "reviewer-1")
    assert record.repaired_in_round == 1 and record.commit == "abc123"


def test_inv11_the_arbiter_cannot_drop_a_finding():
    """Anything it fails to route defaults to a human, never to silence."""
    from factory.pipeline import restore_dispositions

    report = ArbiterReport(summary="s", dispositions=[
        FindingDisposition(finding_id="reviewer-1", disposition="not_a_defect", reason="wrong"),
    ])
    out = restore_dispositions(report, ["reviewer-1", "adversary-1", "hacker-3"])
    assert [d.finding_id for d in out] == ["reviewer-1", "adversary-1", "hacker-3"]
    assert out[0].disposition == "not_a_defect"
    assert out[1].disposition == out[2].disposition == "escalate"
    assert all("did not route" in d.reason for d in out[1:])
    assert restore_dispositions(None, ["a"])[0].disposition == "escalate", \
        "an arbiter that failed entirely must not silently end the loop"


def test_inv11_a_finding_raised_twice_is_one_finding_with_two_rounds():
    """Re-raising is evidence the repair did not land. It must not inflate the
    count, and the harsher reading of the two survives."""
    from factory.pipeline import FindingLedger

    ledger = FindingLedger()
    ledger.add("reviewer", [_finding("Duplicated  helper", "minor")], 0)
    ledger.add("reviewer", [_finding("duplicated helper", "blocker")], 2)

    assert len(ledger.all_findings()) == 1
    record = ledger.all_records()[0]
    assert record.rounds_seen == [0, 2]
    assert record.severity == "blocker" and ledger.findings["reviewer-1"].severity == "blocker"


def test_inv11_a_finding_that_survives_its_attempts_is_settled_not_retried():
    from factory.pipeline import FindingLedger

    ledger = FindingLedger()
    ledger.add("reviewer", [_finding("still there")], 0)
    ledger.dispose([FindingDisposition(
        finding_id="reviewer-1", disposition="repair", reason="mechanical")])

    for round_index in (1, 2):
        assert ledger.repairable(max_attempts=2) == ["reviewer-1"]
        ledger.attempted(["reviewer-1"], round_index)
        ledger.apply_verdicts(
            [RepairVerdict(finding_id="reviewer-1", status="not_fixed", evidence="unchanged")],
            round_index=round_index, commit="c", max_attempts=2,
        )
    ledger.exhaust_attempts(2)

    assert ledger.repairable(max_attempts=2) == [], "a third attempt is not available"
    record = ledger.all_records()[0]
    assert record.outcome == "attempted_not_fixed" and record.attempts == 2


def test_inv11_a_missing_verdict_never_closes_a_finding():
    """Silence must not be able to mark something fixed."""
    from factory.pipeline import FindingLedger

    ledger = FindingLedger()
    ledger.add("reviewer", [_finding("a"), _finding("b")], 0)
    ledger.dispose([
        FindingDisposition(finding_id="reviewer-1", disposition="repair", reason="r"),
        FindingDisposition(finding_id="reviewer-2", disposition="repair", reason="r"),
    ])
    ledger.attempted(["reviewer-1", "reviewer-2"], 1)
    # Only one verdict comes back.
    ledger.apply_verdicts(
        [RepairVerdict(finding_id="reviewer-1", status="fixed", evidence="e")],
        round_index=1, commit="c", max_attempts=2,
    )
    outcomes = {r.finding_id: r.outcome for r in ledger.all_records()}
    assert outcomes["reviewer-1"] == "repaired"
    assert outcomes["reviewer-2"] == "open", "an unanswered finding stays open"


def test_inv11_the_packet_carries_every_outcome_and_the_rapporteur_cannot_edit_it():
    packet = Packet(headline="h", verdict="ship")
    records = [
        FindingRecord(finding_id="reviewer-1", title="a", outcome="repaired"),
        FindingRecord(finding_id="adversary-1", title="b", outcome="attempted_not_fixed"),
    ]
    out = restore_packet(
        packet,
        findings=[_finding("a"), _finding("b")],
        records=records,
        rework=ReworkSummary(rounds=2, stop_reason="converged", repaired=1),
    )
    assert [r.finding_id for r in out.records] == ["reviewer-1", "adversary-1"]
    assert out.rework.rounds == 2 and out.rework.stop_reason == "converged"


# --------------------------------------------------------------------------
# INV-12 -- a repair cannot touch the verification surface
#
# A repairer able to edit the tests that judge it could make any finding go
# away without fixing anything, which turns the loop into a machine for
# manufacturing green -- the exact opposite of what it is for.
# --------------------------------------------------------------------------


def test_inv12_the_blind_tests_and_the_test_config_are_protected(tmp_path):
    from factory.pipeline import is_protected, protected_paths

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text("")
    (tmp_path / "pytest.ini").write_text("")
    (tmp_path / "widget").mkdir()
    (tmp_path / "widget" / "spin.py").write_text("")

    config = Config(roles={"worker": RoleConfig(name="worker", model="m/m")})
    protected = protected_paths(config, tmp_path, extra=["tests/test_ac1.py"])

    assert "tests/conftest.py" in protected
    assert "pytest.ini" in protected
    assert "tests/test_ac1.py" in protected, "the oracle's blind tests are the surface"
    assert "widget/spin.py" not in protected

    # A conftest that did not exist when the set was computed is the more
    # interesting case, not the less: matched by pattern, not only by name.
    assert is_protected("widget/conftest.py", protected, config)
    assert is_protected("tests/breaker/test_edge.py", protected, config)
    assert not is_protected("widget/spin.py", protected, config)


def test_inv12_a_repair_write_to_a_protected_path_is_refused_before_it_lands():
    """Enforced at the door rather than after the fact, so the attempt itself
    becomes evidence."""
    body = inspect.getsource(pipeline.Factory._repair_round)
    assert "is_protected" in body, \
        "the repair round must filter writes against the verification surface"
    assert body.index("is_protected") < body.index("_apply_writes"), \
        "the check has to happen before the write, not after"


def test_inv12_the_repairer_prompt_says_so_in_its_own_words():
    text = (PACKAGE / "roles" / "repairer.md").read_text()
    assert "read-only" in text.lower()
    assert "changing the test" in text.lower()


# --------------------------------------------------------------------------
# INV-6 survives the loop: every stopping condition is a number a human wrote
# --------------------------------------------------------------------------


def test_the_loop_stops_on_numbers_from_config_not_on_a_model_s_opinion():
    body = inspect.getsource(pipeline.Factory._converge)
    for knob in ("max_rounds", "budget_usd", "reserve_usd", "wall_clock_minutes",
                 "max_attempts_per_finding"):
        assert f"cfg.{knob}" in body, f"{knob} is not read where the loop decides to stop"
    assert "stop_reason" in body, "a loop that stops silently reads as one that converged"


def test_the_loop_reserves_enough_to_produce_a_packet():
    """The failure this prevents: the loop spends everything repairing and the
    run ends with no packet at all, which is strictly worse than not looping."""
    body = inspect.getsource(pipeline.Factory._converge)
    assert "cfg.budget_usd - cfg.reserve_usd" in body


# --------------------------------------------------------------------------
# the breaker: red is the signal, green is not evidence of anything
# --------------------------------------------------------------------------


def test_a_passing_breaker_suite_produces_no_findings():
    from factory.pipeline import breaker_findings

    suite = BreakerSuite(strategy="s", tests=[BreakerTest(
        path="tests/breaker/test_a.py", contents="", hypothesis="it breaks")])
    passing = BreakerReport(ran=True, exit_code=0, failing=[])
    assert breaker_findings(suite, passing) == []

    failing = BreakerReport(ran=True, exit_code=1, failing=["tests/breaker/test_a.py"],
                            output_tail="FAILED tests/breaker/test_a.py")
    found = breaker_findings(suite, failing)
    assert [f.title for f in found] == ["it breaks"], \
        "the hypothesis was written before the outcome was known; it is the finding"


def test_a_breaker_that_could_not_run_makes_no_claims():
    from factory.pipeline import breaker_findings

    suite = BreakerSuite(strategy="s", tests=[])
    assert breaker_findings(suite, BreakerReport(ran=False, note="no runner")) == []


def test_breaker_tests_never_reach_the_traceability_matrix():
    """INV-3 is a function of worker decisions and oracle tags. A probe written
    by an agent that has read the implementation is not verification of the
    contract, and must not be able to mark a criterion traced."""
    source = inspect.getsource(compute_trace)
    assert "breaker" not in source.lower()


def test_the_load_command_survives_the_blind_suite_rebuilding_its_rules():
    """A field added to a model is dropped by every place that rebuilds it.

    `_run_blind_suite` reconstructs each rule to substitute `{oracle_dir}` and
    rebuilt it without `collect`. Every rule reaching the probe therefore had an
    empty load command, no loader was built, and the probe reported "nothing
    unloadable" for a suite where two of four files did not load at all -- so
    four criteria came back failing on evidence that was really a missing
    import, which is the confusion the probe exists to end.

    Caught on a live run, not by a test, which is why there is one now.
    """
    src = inspect.getsource(pipeline.Factory._run_blind_suite)
    assert "collect=r.collect" in src, (
        "the blind suite rebuilds its rules and drops the load command")

    # And the reconstruction itself, exercised rather than read.
    rule = schemas.TestFileCommand(
        match="*", command="pytest {oracle_dir}/{path}",
        collect="pytest --collect-only -q {oracle_dir}/{path}")
    rebuilt = schemas.TestFileCommand(
        match=rule.match,
        command=rule.command.replace("{oracle_dir}", "tests_blind"),
        collect=rule.collect.replace("{oracle_dir}", "tests_blind"))
    assert rebuilt.collect == "pytest --collect-only -q tests_blind/{path}"


def test_a_run_command_may_not_carry_the_report_placeholder():
    """The two fields sit next to each other and describe the same invocation.

    A real reading copied one into the other -- for one of its two rules and not
    the other, so neither the page nor a reader would have caught it. Nothing
    substitutes `{report}` in `command`, so that rule would have run a command
    with the literal braces in it.
    """
    ok = schemas.TestFileCommand(
        match="*", command="pytest {path}", report="pytest --junitxml={report} {path}")
    assert ok.report
    with pytest.raises(Exception) as caught:
        schemas.TestFileCommand(match="*", command="pytest --junitxml={report} {path}")
    assert "{report}" in str(caught.value)


def test_a_re_reading_that_proposes_what_is_already_true_reaches_nobody():
    """`blind_placements`, `testing` and `test_file_commands` are replacement
    sets: to move one rule a model must resend every rule. So a reading that
    changed nothing is indistinguishable in the payload from one that rewrote
    everything, and a human gets cards restating what they approved twenty
    minutes ago. After enough of those they stop reading the one that matters.

    Measured on a real project: two consecutive readings, identical in every
    load-bearing field, differing only in `canary_passes`, `canary_fails`, a
    `kind` label and a reworded `runner` sentence -- so byte equality would have
    dropped none of it.
    """
    from factory.onboarding import strip_unchanged
    from factory.schemas import (BlindPlacement, Gate, ProjectState, ProposedGateChange,
                                 TestFileCommand, TestingSurface, TestingTier)

    state = ProjectState(
        project_id="p",
        gates=[Gate(name="web-lint", command="npm run lint")],
        blind_placements=[BlindPlacement(kind="vitest", directory="web/acceptance",
                                         filename="C.test.tsx", canary_passes="a",
                                         canary_fails="b")],
        test_file_commands=[TestFileCommand(match="*", command="pytest {path}")],
        testing=TestingSurface(tiers=[TestingTier(tier="unit", verdict="usable",
                                               runner="vitest 2.1 under jsdom")]),
    )
    diff = _reading(
        gate_changes=[ProposedGateChange(action="change", name="web-lint", reason="r", evidence="e",
                                 gate=Gate(name="web-lint", command="npm run lint"))],
        blind_placements=[BlindPlacement(kind="frontend component (vitest/jsdom)",
                                         directory="web/acceptance", filename="C.test.tsx",
                                         canary_passes="REWORDED", canary_fails="ALSO REWORDED")],
        test_file_commands=[TestFileCommand(match="*", command="pytest {path}")],
        testing=TestingSurface(tiers=[TestingTier(tier="unit", verdict="usable",
                                               runner="vitest (web/vite.config.ts), jsdom")]),
    )
    dropped = strip_unchanged(diff, state)

    assert not diff.gate_changes, "a check proposed exactly as it stands still reaches a human"
    assert not diff.blind_placements, "a placement is being re-proposed because its canary was reworded"
    assert not diff.test_file_commands
    assert diff.testing is None, "a tier is being re-proposed because its runner sentence changed"
    assert len(dropped) == 4 and all(d for d in dropped), "what was dropped has to be sayable"


def test_a_re_reading_that_proposes_something_real_is_left_alone():
    """The other half, and the one that matters: this code decides what a human
    does not see, so it must not be able to swallow a change. Each of these
    differs from the record in exactly one load-bearing field."""
    from factory.onboarding import strip_unchanged
    from factory.schemas import (BlindPlacement, Gate, ProjectState, ProposedGateChange,
                                 TestFileCommand, TestingSurface, TestingTier)

    state = ProjectState(
        project_id="p",
        gates=[Gate(name="web-lint", command="npm run lint")],
        blind_placements=[BlindPlacement(kind="vitest", directory="web/acceptance",
                                         filename="C.test.tsx", canary_passes="a",
                                         canary_fails="b")],
        test_file_commands=[TestFileCommand(match="*", command="pytest {path}")],
        testing=TestingSurface(tiers=[TestingTier(tier="unit", verdict="inline_only",
                                               runner="vitest")]),
    )
    moved = _reading(
        gate_changes=[ProposedGateChange(action="change", name="web-lint", reason="r", evidence="e",
                                 gate=Gate(name="web-lint",
                                           command="npm run lint -- --ignore-pattern x"))],
        blind_placements=[BlindPlacement(kind="vitest", directory="web/e2e/acceptance",
                                         filename="C.spec.ts", canary_passes="a",
                                         canary_fails="b")],
        test_file_commands=[TestFileCommand(match="*", command="pytest ../{path}")],
        testing=TestingSurface(tiers=[TestingTier(tier="unit", verdict="usable", runner="vitest")]),
    )
    strip_unchanged(moved, state)

    assert moved.gate_changes, "a changed command was dropped"
    assert moved.blind_placements, "a moved placement was dropped"
    assert moved.test_file_commands, "a corrected `../` was dropped"
    assert moved.testing is not None, "a tier whose verdict rose was dropped"

    # And a different tool at the same verdict is a real change, not prose.
    swapped = _reading(testing=TestingSurface(
        tiers=[TestingTier(tier="unit", verdict="inline_only", runner="jest 29")]))
    strip_unchanged(swapped, state)
    assert swapped.testing is not None, "vitest becoming jest was read as a rewording"


def test_a_reason_is_not_asked_to_narrate_a_diff_the_page_already_draws():
    """Every proposed field is rendered as a difference against what is recorded,
    with the restated entries named. A `reason` that opens "the user tier moves
    from absent to usable" spends the reader's first sentence on the one thing
    they can already see, and pushes what they cannot -- what breaks without it
    -- past where they stop.

    Measured: one reading's `testing_reason` ran 1,533 characters and its
    `test_file_commands_reason` 1,673, both opening with a recap of their own
    diff and one of them hedging at length about whether this tool's matcher
    lets `*` cross a slash.
    """
    from factory import schemas
    import typing

    surveyor = _prose((ROOT / "factory" / "roles" / "surveyor.md").read_text())
    assert "Say what goes wrong if this does not change" in surveyor
    assert "do not narrate it" in surveyor.lower(), \
        "nothing tells a reading that the difference is already on the page"

    # And the hedge had a cause: the prompt never said what the matcher does.
    assert "`*` crosses `/`" in surveyor and "fnmatch" in surveyor, \
        "the match semantics are knowable from gates.py and not stated here"

    # The schema says it too, because that is what is in front of the model as
    # it fills the field.
    d = schemas.SurveyDiff
    fields = ["testing_reason", "test_file_commands_reason",
              "blind_placements_reason", "environment_reason"]
    gc = typing.get_args(d.model_fields["gate_changes"].annotation)[0]
    for name, model in [(f, d) for f in fields] + [("reason", gc)]:
        desc = model.model_fields[name].description or ""
        assert ("do not describe the change" in desc
                or "do not spend the first sentence restating the change" in desc), \
            f"{name} does not say what it is for"


def test_a_resurvey_can_propose_a_file_and_not_only_a_command():
    """`SurveyDiff` could carry a new command and never a new file, so a factory
    the verify lane kept asking for could arrive only by re-surveying from
    scratch and re-adjudicating every decision already approved."""
    assert "scaffolding" in schemas.SurveyDiff.model_fields
    src = inspect.getsource(pipeline_projects_module.ProjectRegistry.apply_survey_diff)
    assert '"scaffolding" in wanted' in src, "proposed but not applicable"
    # Added to what a human may choose, never written to a repository here.
    assert "changes[\"survey\"] = survey" in src


def test_the_oracle_is_asked_to_tag_individual_tests():
    """A report the runner writes is useless without names to join it to. The
    oracle is the only role that knows which test serves which criterion."""
    src = inspect.getsource(pipeline.Factory._author_blind_suite)
    assert "`cases`" in src
    assert "your framework will" in src, "the name has to be the reported one"
    # And asked without naming a runner: which one this project uses belongs to
    # the surveyor, and `test_nothing_in_the_orchestrator_recognises_a_test_runner`
    # catches it here if a helpful example ever creeps back in.
    assert "pytest" not in src.lower()
    assert "fourteen criteria" in src, "the reason has to travel with the ask"


def test_a_disposable_database_is_named_to_the_suite_and_torn_down():
    """Two criteria have gone unverified in every run of a real project for want
    of this, and the oracle asked for it by name every time: a database it may
    migrate from the previous revision and downgrade. The one it normally uses is
    already at head and shared with a running server, so a criterion about
    `upgrade()` or about rows that existed beforehand cannot be observed on it.
    """
    disposable = schemas.DisposableDatabase(
        env="FACTORY_DISPOSABLE_DATABASE_URL", url="postgresql://owner@pg/prior",
        prepare=["dropdb --if-exists prior", "createdb prior"],
        teardown=["dropdb --if-exists prior"])
    runner = _RecordingRunner()
    runner.session_env = {}

    async def go():
        async with gates.test_session(
                "/tmp", runner, prepare=["MIGRATE"], disposable=disposable) as s:
            return dict(runner.session_env), s
    env, session = asyncio.run(go())

    assert env.get("FACTORY_DISPOSABLE_DATABASE_URL") == "postgresql://owner@pg/prior"
    assert "createdb prior" in runner.log
    assert runner.log.index("createdb prior") < runner.log.index("MIGRATE"), (
        "the database has to exist before anything prepares against it")
    assert runner.log[-1] == "dropdb --if-exists prior", "not torn down"


def test_a_disposable_database_that_could_not_be_made_is_not_named():
    """Its absence costs only the criteria that need it. A variable naming a
    database that is not there is worse than no variable: a test reads it and
    fails for a reason that is not about the code."""
    disposable = schemas.DisposableDatabase(
        env="FACTORY_DISPOSABLE_DATABASE_URL", url="postgresql://owner@pg/prior",
        prepare=["createdb prior"])
    runner = _RecordingRunner(fail="createdb")
    runner.session_env = {}

    async def go():
        async with gates.test_session("/tmp", runner, disposable=disposable) as s:
            return dict(runner.session_env), s
    env, session = asyncio.run(go())

    assert "FACTORY_DISPOSABLE_DATABASE_URL" not in env
    assert session.ready, "one optional capability must not take the whole suite down"


def test_the_oracle_is_told_about_a_database_it_may_wreck():
    """And told nothing when there isn't one -- an absent capability described
    as present is how a suite comes to demand a precondition nobody supplies."""
    tier = schemas.TestingTier(tier="integration", runner="pytest", verdict="usable")
    with_db = schemas.TestingSurface(tiers=[tier], disposable_db=schemas.DisposableDatabase(
        env="FACTORY_DISPOSABLE_DATABASE_URL", url="postgresql://x/y",
        migrate="alembic upgrade {revision}"))
    rendered = workspace.testing_context(with_db)
    assert "FACTORY_DISPOSABLE_DATABASE_URL" in rendered
    assert "alembic upgrade {revision}" in rendered
    assert "Reading the migration file is not running it" in rendered

    without = workspace.testing_context(schemas.TestingSurface(tiers=[tier]))
    assert "database of your own" not in without


def test_each_blind_file_starts_from_a_prepared_database():
    """One database, several files, and the last one sees the others' writes.

    A run lost AC-25 to that: a file set a participation's capacity columns to
    NULL to make itself an "unrecorded" fixture, nothing put them back, and the
    file asserting no participation was NULL failed for it. Two criteria came
    back red about a feature that was fine.

    Before the first file as well as between them -- the project's own checks
    run against the same database and finish before this suite starts.
    """
    rules = [schemas.TestFileCommand(match="*", command="run {path}")]
    runner = _RecordingRunner(fail="b_test")
    result, _ = asyncio.run(gates.run_per_file(
        rules, ["a_test.py", "b_test.py"], "/tmp", runner,
        prepare=["RESET migrate", "RESET seed"]))
    assert runner.log[:2] == ["RESET migrate", "RESET seed"], "the first file ran unprepared"
    assert runner.log.count("RESET migrate") == 2, "no reset between the files"
    assert result.named_failing == ["b_test.py"]


def test_a_file_whose_reset_failed_settles_nothing():
    """Not run, and not failed either. A database that would not migrate says
    nothing about the code, and reporting the file as red would blame the
    feature for the harness."""
    rules = [schemas.TestFileCommand(match="*", command="run {path}")]
    runner = _RecordingRunner(fail_nth_reset=2)
    result, exits = asyncio.run(gates.run_per_file(
        rules, ["a_test.py", "b_test.py"], "/tmp", runner, prepare=["RESET migrate"]))
    assert "b_test.py" in result.named_unloadable
    assert "b_test.py" not in result.named_failing
    assert "b_test.py" not in exits, "it must not have run at all"


def test_the_reset_is_off_unless_the_run_asked_for_it():
    """Additive: a caller that passes no prepare commands gets exactly what it
    got before this existed."""
    rules = [schemas.TestFileCommand(match="*", command="run {path}")]
    runner = _RecordingRunner()
    asyncio.run(gates.run_per_file(rules, ["a.py", "b.py"], "/tmp", runner))
    assert runner.log == ["run a.py", "run b.py"]


def test_a_parametrised_name_still_matches_its_declaration():
    """pytest reports `test_x[case-1]` for a declaration of `test_x`."""
    declared = "test_ac_4_shape"
    reported = [schemas.CaseOutcome(name="test_ac_4_shape[mercy]", status="failed"),
                schemas.CaseOutcome(name="test_ac_4_shape[coastal]", status="passed")]
    assert len(pipeline.match_case(declared, reported)) == 2
    # But never a loose one: a different test that merely starts the same way.
    assert pipeline.match_case("test_ac_4", [
        schemas.CaseOutcome(name="test_ac_40_other", status="failed")]) == []


def test_a_suite_qualified_name_still_matches_its_declaration():
    """vitest and jest report `describe > it`; the oracle declares the `it`.

    card-tags-8e21a7: nine criteria came back `unknown` -- "the oracle tagged
    tests to this criterion that the runner never reported" -- over a describe
    block's title sitting in front of every vitest name in the JUnit report.
    All nine had run, and all nine had passed. The same run's pytest criteria
    were attributed correctly, which is how a suite-wide harness fault wore the
    face of a per-criterion one.
    """
    declared = "adds a tag before the promise settles"
    reported = [schemas.CaseOutcome(
        name="AC-13 optimistic tag display > adds a tag before the promise settles",
        status="passed")]
    assert pipeline.match_case(declared, reported) == reported
    # The other separator the JS reporters format titles with, for the same
    # reason -- unmeasured here, accepted because accepting it costs nothing.
    assert len(pipeline.match_case(declared, [schemas.CaseOutcome(
        name="chips \u203a adds a tag before the promise settles", status="passed")])) == 1
    # A leaf match is still a match of the whole leaf, not of a prefix of it.
    assert pipeline.match_case("adds a tag", reported) == []
    # And a fully-qualified name is preferred where the report carries both.
    both = [schemas.CaseOutcome(name=f"suite > {declared}", status="failed"),
            schemas.CaseOutcome(name=declared, status="passed")]
    assert pipeline.match_case(declared, both) == [both[1]]


def test_the_blind_suite_carries_the_report_command_through_its_rules():
    """The third field on a rule, and the third chance to rebuild it without one.
    `collect` was dropped by exactly this line and the load probe went silent."""
    src = inspect.getsource(pipeline.Factory._run_blind_suite)
    assert "report=r.report" in src


def test_a_blind_test_that_did_not_load_is_not_a_failing_criterion():
    """The two exit non-zero and they are opposite facts.

    site-screening-61020a: a blind UI test imported `../../frontend/src/pages/
    TrialDetail` from inside `frontend/src/__tests__/`, which resolves to
    nothing. It collected no tests, exited 1, and reached a human as AC-18
    through AC-23 failing -- with a review agent filing the components, which
    were fine, as a blocker on that evidence. The file was the defect.
    """
    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-18", statement="the section renders")])
    trace = [TraceRow(criterion_id="AC-18", test_names=["ui.test.tsx"], status="traced")]
    result = _reported("blind-tests", passed=False, total=0,
                       witness=["ui.test.tsx"], failing=["ui.test.tsx"])
    result.named_unloadable = ["ui.test.tsx"]

    qa = pipeline.compute_qa(spec, None, gates.GateReport(results=[result]), trace)
    assert qa.results[0].status == "not_collected", "an unloadable file read as a verdict"
    assert "not about the code" in qa.results[0].evidence
    assert any("did not load" in f for f in qa.failures)


def test_without_a_load_command_every_verdict_is_exactly_what_it_was():
    """The probe is additive. A project surveyed before `collect` existed draws
    no distinction, and a distinction this system cannot measure is never one it
    guesses at."""
    assert schemas.TestFileCommand(match="*", command="pytest {path}").collect == ""
    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="it works")])
    trace = [TraceRow(criterion_id="AC-1", test_names=["a_test.py"], status="traced")]
    report = gates.GateReport(results=[_reported(
        "blind-tests", passed=False, total=1, witness=["a_test.py"],
        failing=["a_test.py"])])

    qa = pipeline.compute_qa(spec, None, report, trace)
    assert qa.results[0].status == "failed"


def test_a_suite_that_ran_nothing_is_not_a_pass():
    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="it works")])
    trace = [TraceRow(criterion_id="AC-1", test_names=["tests/test_one.py"], status="traced")]
    report = gates.GateReport(results=[_reported(
        "tests", passed=True, total=0, zero_ran=True,
        witness=["tests/test_one.py"])])

    qa = pipeline.compute_qa(spec, None, report, trace)
    assert qa.results[0].status == "not_collected"
    assert "no tests ran" in qa.results[0].evidence.lower() or \
        "nothing was checked" in qa.results[0].evidence.lower()

    findings = pipeline.check_test_execution(report, trace)
    assert [f.severity for f in findings] == ["blocker"]


def test_an_ordinary_green_run_is_still_a_pass():
    """The check must not cry wolf: most suites skip something, and a tool that
    calls every ordinary run suspicious gets ignored on the run that matters."""
    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="it works")])
    trace = [TraceRow(criterion_id="AC-1", test_names=["tests/test_one.py"], status="traced")]
    report = gates.GateReport(results=[gates.GateResult(
        name="tests", passed=True, exit_code=0,
        output_tail="======== 12 passed in 0.4s ========")])

    qa = pipeline.compute_qa(spec, None, report, trace)
    assert qa.results[0].status == "passed"
    assert pipeline.check_test_execution(report, trace) == []


def test_a_suite_run_as_one_command_says_so_rather_than_implying_more():
    """A pass is reported at the strength it actually has.

    There used to be a third possibility here -- the runner's output was in a
    format the tool could not read -- and it was the failure mode that cost a
    real gate its whole signal, silently. It is gone with the parsing. What is
    left is an honest distinction between a criterion whose own test file was
    run and passed, and one covered only by a suite-wide exit code.
    """
    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="it works")])
    trace = [TraceRow(criterion_id="AC-1", test_names=["tests/test_one.py"], status="traced")]

    whole = gates.GateReport(results=[_reported(
        "tests", passed=True, total=0, per_file=False)])
    qa = pipeline.compute_qa(spec, None, whole, trace)
    assert qa.results[0].status == "passed"
    assert "one command rather than file by file" in qa.results[0].evidence, \
        "a suite-wide exit code is being presented as if it settled this criterion"

    per_file = gates.GateReport(results=[_reported(
        "tests", passed=True, total=1, n_passed=1, witness=["tests/test_one.py"])])
    qa = pipeline.compute_qa(spec, None, per_file, trace)
    assert qa.results[0].status == "passed"
    assert "on their own" in qa.results[0].evidence


def test_a_probe_written_this_round_is_not_a_regression():
    """The defect that cost a real run every repair it made.

    The check was `set(failing) - previously_failing`, which answers a different
    question: it flags a probe that is failing now and was not failing before.
    A probe written *this* round has no earlier result to have passed in, so
    every newly-authored failing probe read as something the repair broke -- and
    the breaker is told each round to probe the repairs specifically, and a probe
    exists to fail. Any round in which it found anything was guaranteed to be
    reverted.

    That run repaired three defects, had all three confirmed by four independent
    re-checks, and was reverted wholesale because the next breaker pass wrote two
    new probes: one a genuine new problem, the other a *stricter* demand on code
    that had just improved. It ended with 0 repaired and 16 findings standing.

    A regression is a probe that passed before and fails now. Nothing else.
    """
    src = code_without_prose(pipeline.Factory._converge)

    assert "previous_breaker_failing" not in src, \
        "the check still compares against what was failing rather than what passed"
    assert "previous_breaker_passing & failing_now" in src, \
        "a regression is not defined as `passed before, fails now`"

    # Passing is read from a suite that proved something. One that could not
    # run -- or ran, failed and could name no probe -- knows of nothing passing
    # and so cannot claim anything regressed.
    # Both of them: `passing_now` is computed at the top of the round and again
    # on the restored tree after a revert, and a guard on only one of the two
    # leaves the other free to claim a suite that proved nothing proved probes
    # pass.
    assert src.count("passing_now =") == src.count(
        "probes_proven_passing(breaker_tests, breaker_report)") == 2, (
        "a breaker that proved nothing is treated as having proved probes pass, "
        "which would make the next round's failures look like regressions"
    )

    # And the gate comparison is re-measured on the restored tree. Carrying the
    # broken count forward sets the next round's bar at it, so a repair that
    # breaks exactly as much passes the comparison and survives the revert.
    revert_at = src.index("reverted.append(round_index)")
    after = src[revert_at:src.index("previous_gate_pass = gate_pass", revert_at)]
    # Matched loosely: this source is normalised by `ast.unparse`, which
    # re-parenthesises the generator.
    assert "gate_pass = sum(" in after and "passing_now =" in after, \
        "the gate count carried forward is the one measured on the reverted work"


def test_a_reverted_round_is_not_the_end_of_the_loop():
    """One bad round is not a reason to stop, and stopping cost a real run four
    of its five rounds and most of its budget with nothing repaired.

    The findings go back to open with the attempt already counted, so
    `max_attempts_per_finding` bounds the retrying: a repair that keeps breaking
    the same thing retires as attempted-and-not-fixed rather than looping.
    """
    src = code_without_prose(pipeline.Factory._converge)
    revert_at = src.index("reverted.append(round_index)")
    block = src[revert_at:src.index("previous_gate_pass = gate_pass", revert_at)]

    assert "stop_reason = f\"round {round_index} was reverted" not in block, \
        "a single reverted round still ends the loop"
    assert "len(reverted) >= cfg.max_rounds" in block, (
        "nothing stops a loop that reverts every round it is given, which is "
        "the one case where continuing buys nothing"
    )


def test_a_reverted_round_cannot_leave_a_finding_marked_repaired():
    """The re-check runs before the next round's gates, so a repair can be
    reported fixed and only then turn out to have made the work worse. When the
    commit goes, the claim goes with it."""
    from factory.pipeline import FindingLedger

    ledger = FindingLedger()
    ledger.add("reviewer", [_finding("a")], 0)
    ledger.dispose([FindingDisposition(
        finding_id="reviewer-1", disposition="repair", reason="mechanical")])
    ledger.attempted(["reviewer-1"], 1)
    ledger.apply_verdicts(
        [RepairVerdict(finding_id="reviewer-1", status="fixed", evidence="looks gone")],
        round_index=1, commit="deadbeef", max_attempts=2,
    )
    assert ledger.all_records()[0].outcome == "repaired"

    ledger.revert_round(1, "gates fell from 2 to 1")
    ledger.finalise(stopped_early=True)

    record = ledger.all_records()[0]
    assert record.outcome == "attempted_not_fixed"
    assert record.repaired_in_round is None and record.commit == ""
    assert "reverted" in record.outcome_evidence
    assert record.attempts == 1, "the attempt happened; only its result was withdrawn"


def test_every_phase_belongs_to_a_band_and_to_the_build():
    """The rail, the strip and the diagram all read from these. A phase in
    neither is invisible while it runs."""
    source = app_js()
    banded = set(re.findall(r"'([^']+)'", re.search(
        r"const BANDS = \[(.*?)\n\];", source, re.S).group(1)))
    missing = [p for p in pipeline.PHASE_NAMES if p not in banded]
    assert not missing, f"phases in no band: {missing}"

    build = set(_js_list("BUILD_PHASES"))
    intake = {"scout", "interrogator", "spec_writer", "spec_checker"}
    assert build | intake == set(pipeline.PHASE_NAMES), (
        "every phase is either intake or part of the build; "
        f"unaccounted for: {set(pipeline.PHASE_NAMES) - build - intake}"
    )
    assert not build & intake, "a phase cannot be both intake and build"

    # A phase that only runs when there is work for it must be declared, or a
    # finished run reads as stalled at "11 of 15 phases complete".
    conditional = set(_js_list("CONDITIONAL_PHASES"))
    assert conditional <= set(pipeline.PHASE_NAMES), \
        f"conditional phases that do not exist: {conditional - set(pipeline.PHASE_NAMES)}"


def test_a_survey_lands_every_reading_it_paid_for(tmp_path):
    """Everything the surveyor produces has to reach the project.

    `testing` was read by the surveyor and never copied, so a freshly surveyed
    project had an empty testing surface whatever the survey said. Nothing broke
    loudly: `check_spec_testability` returns early on an empty surface, so specs
    were simply never checked against what the project can verify, and the only
    way to get a surface was a re-survey diff.

    Derived from the survey model rather than listed, so the next field added is
    caught here instead of by a reading that quietly goes missing.
    """
    from factory.projects import ProjectRegistry
    from factory.schemas import (
        BlindPlacement, EnvironmentSpec, Gate, ProjectSurvey, TestFileCommand,
        TestingSurface, TestingTier,
    )

    repo = tmp_path / "repo"; repo.mkdir()
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")

    survey = ProjectSurvey(
        name="demo", summary="s",
        gates=[Gate(name="tests", command="true")],
        testing=TestingSurface(tiers=[TestingTier(tier="unit", verdict="usable", runner="pytest")]),
        test_file_commands=[TestFileCommand(match="*", command="pytest {path}")],
        blind_placements=[BlindPlacement(
            directory="tests/blind", filename="test_canary.py",
            canary_passes="PASS", canary_fails="FAIL")],
        environment=EnvironmentSpec(kind="host", rationale="fixture"),
    )
    after = registry.record_survey(project, survey).state

    assert [t.tier for t in after.testing.tiers] == ["unit"], \
        "the surveyor read the testing surface and the project did not keep it"
    assert [g.name for g in after.gates] == ["tests"]
    assert [c.command for c in after.test_file_commands] == ["pytest {path}"]
    assert [b.directory for b in after.blind_placements] == ["tests/blind"]
    assert "project.state.trace_dirs = list(survey.trace_dirs)" in inspect.getsource(
        ProjectRegistry.record_survey), "listed as carried above, and actually carried"

    # Every field the survey and the project both have must actually travel.
    shared = set(ProjectSurvey.model_fields) & set(type(after).model_fields)
    carried = {"gates", "testing", "test_file_commands", "blind_placements",
               "environment", "name", "base_ref", "trace_dirs"}
    assert shared <= carried, (
        f"`record_survey` has no home for {sorted(shared - carried)} -- a reading that "
        "was paid for and then dropped")


def test_the_thing_that_makes_a_verdict_checkable_is_on_the_screen():
    """A field that decides a ruling has to be visible to the person ruling.

    The systemic guard below covers `SurveyDiff`'s own fields; this one is a
    level down and it bit anyway. `TestingTier.canary` is what turns `usable`
    from a model's claim into something gate 0 can run, and it was added to the
    schema, required by the prompt, produced by a real reading -- and never
    rendered. The proposal whose entire difference was carrying canaries drew
    identically to the one without them, so the page said "nothing new here"
    about the only thing in it that was new.
    """
    from factory.schemas import TestingTier, TierResult

    app = app_js()
    assert "canary" in TestingTier.model_fields
    assert "function canaryNote(" in app, "there is no way to see the test that proves the claim"
    # Inside that function specifically. Asserting the field is named *somewhere*
    # in a 6,000-line file passes while the renderer ignores it -- which is what
    # a first version of this test did, and a mutation walked straight through.
    body = app.split("function canaryNote(", 1)[1].split("\n}\n", 1)[0]
    assert "t.canary" in body, "canaryNote does not read the canary it exists to show"
    assert "esc(src)" in body, "the canary is read and never written to the page"

    # And the measurement it produces has to land somewhere too.
    assert "proved" in TierResult.model_fields
    assert "p.testing_probe" in app, (
        "gate 0 measures whether each `usable` is true and the page never says")


def test_no_prompt_names_an_agent_that_can_be_deleted():
    # Both files, resolved by path rather than by working directory: the shipped
    # example is what a new checkout gets, and factory.yaml is what runs. A name
    # deletable in either is a name a prompt must not depend on.
    root = ROOT
    deletable: set[str] = set()
    for name in ("factory.yaml", "factory.example.yaml"):
        path = root / name
        if path.exists():
            deletable |= {n for n, r in load_config(path).roles.items() if r.review}
    assert deletable, "this test is only meaningful while review roles exist"

    offences: list[str] = []
    for path in _prompt_files():
        text = path.read_text(encoding="utf-8")
        for name in deletable:
            # A prompt may name itself: `adversary.md` opening with "# adversary"
            # and describing what an adversary is for is not a claim about the
            # roster, it is the agent's own name.
            if path.stem == name:
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if re.search(rf"\b{re.escape(name)}s?\b", line, re.I):
                    offences.append(f"{path.name}:{i} names {name!r} — {line.strip()[:70]}")
    assert not offences, (
        "these prompts describe a roster the config can change:\n" + "\n".join(offences)
    )


def test_a_contrast_with_another_agent_is_stated_as_a_rule_not_a_roster():
    """The other half of the rule, narrowed.

    Both contracts used to be taught by naming a colleague: the breaker against
    the oracle, the repairer against the worker. Neither ever sees the other, so
    the name was doing no work the rule could not do itself -- and it made two
    prompts depend on the cast list. The substance is what has to survive, and
    it is what this asserts."""
    roles = PACKAGE / "roles"

    breaker = _prose((roles / "breaker.md").read_text())
    assert "Only a failing probe is worth anything here" in breaker, \
        "the breaker is not told that green carries no signal for it"
    assert "never counts toward a criterion" in breaker, \
        "nothing stops the breaker reading a passing probe as verification"
    assert "oracle" not in breaker.lower(), \
        "the breaker is defined against an agent it never meets"

    repairer = _prose((roles / "repairer.md").read_text())
    assert "You are rewarded for none of that" in repairer, \
        "the repairer is not told its ordinary instincts are the wrong ones here"
    assert "worker" not in repairer.lower(), \
        "the repairer is defined against an agent it never meets"


def test_the_arbiter_routes_repairs_to_the_agent_that_makes_them():
    """It spent a while telling the model that a `worker` would make the fix.
    A worker and a repairer follow opposite rules, and the route is chosen on
    whether the fix is possible under the repairer's."""
    text = (PACKAGE / "roles" / "arbiter.md").read_text()
    routes = text[text.index("## The seven routes"):]
    assert "repairer" in routes and "a worker can make" not in routes
    # `simplify` is the sixth, and it exists because a repairer may not remove
    # something for being unnecessary. The two routes must not claim the same
    # examples, or the arbiter is choosing between them on a coin flip.
    assert "**`simplify`**" in routes, "findings no repair can close have nowhere to go"
    assert "whether the behaviour is wrong" in routes, \
        "nothing tells the arbiter how to choose between repair and simplify"


def test_the_arbiter_knows_a_finding_about_the_pipeline_is_not_repairable():
    """A finding whose subject is the harness has no fix a repairer can make.

    `requires-` is the sharpest case: it says the blind test author could read a
    criterion and was denied the means to check it. The fix is a human writing a
    capability into configuration. A repairer sent at it edits the feature's code
    to no effect, and the spent attempt retires the finding as `attempted and not
    fixed` -- so a hole in the verification layer reaches the packet wearing the
    costume of a defect in the work it failed to verify.

    The rule is stated as a test of what would close the finding rather than as a
    list of ids, because the id list grows every time a new computed check is
    added and a prompt pinned to one goes stale silently.
    """
    text = (PACKAGE / "roles" / "arbiter.md").read_text()
    flat = " ".join(text.split())
    assert "Some findings are about this pipeline, not about the code" in flat, \
        "nothing tells the arbiter that some findings have no code fix at all"
    assert "`requires-` finding" in flat, \
        "the clearest case of a harness finding is not named"
    assert "rather than to the feature's source, no repairer can make it" in flat, (
        "the rule is not stated as a test the arbiter can apply to a finding it "
        "has not seen before"
    )
    assert "attempted and not fixed" in flat, (
        "the arbiter is not told what mis-routing costs, which is the whole "
        "reason this matters: the finding degrades rather than merely stalling"
    )


def test_how_to_run_one_test_file_comes_from_the_project_not_from_this_tool():
    """The surveyor reads it off the repository; a human approves it at gate 0.

    This is the whole answer to "what runner does this project use", and it
    lives where the answer lives. Two earlier versions kept it here instead --
    a table of console-output regexes, then a report format -- and both were the
    same mistake: knowledge about somebody else's project, encoded in this one.
    """
    from factory import projects as pipeline_projects
    from factory.schemas import ProjectState, ProjectSurvey

    assert "test_file_commands" in ProjectSurvey.model_fields, \
        "the surveyor has no way to say how a single test file is run"
    assert "test_file_commands" in ProjectState.model_fields, \
        "the surveyor's answer is not carried on the project"
    from factory.schemas import TestFileCommand
    assert "{path}" in TestFileCommand.model_fields["command"].description, \
        "the field does not tell the surveyor where the path goes"

    # Recording a survey has to carry it across, or it is proposed and dropped.
    recorded = inspect.getsource(pipeline_projects.ProjectRegistry.record_survey)
    assert "project.state.test_file_commands = list(survey.test_file_commands)" in recorded

    # And the run reads the project's, with configuration only as an override.
    blind = inspect.getsource(pipeline.Factory._run_blind_suite)
    assert "self.project.state.test_file_commands" in blind, \
        "the run ignores what the project said and uses only its own config"

    surveyor = " ".join(
        (ROOT / "factory" / "roles" / "surveyor.md").read_text(encoding="utf-8").split())
    assert "`test_file_commands` is a list of rules" in surveyor
    assert "Point it at one file, never a directory or a whole suite" in surveyor, \
        "nothing stops the surveyor handing back a whole-suite command"
    assert "Never write a port number" in surveyor, \
        "the surveyor is not told to take ports from the harness"
    assert "$FACTORY_PORT_API" in surveyor, \
        "the variable the harness publishes is never named in the prompt"
    assert "Do not write the branch yourself" in surveyor, \
        "nothing stops the surveyor putting a shell conditional back in the command"
    assert "`{path}` is repo-relative, and `cd` does not change that" in surveyor, (
        "nothing warns that a rule which cd's into a subdirectory must adjust the "
        "path -- measured three times as the one thing the surveyor gets wrong, and "
        "it produces a command that runs cleanly, finds nothing, and reports a "
        "criterion as failing"
    )


def test_criteria_this_project_cannot_verify_are_named_before_the_spec_is_frozen():
    """The change that would have saved a whole day.

    Twenty-five criteria were frozen, built, verified and reported on -- three
    hours -- before anything said that eleven of them could not be checked by
    this project's test setup at all. Six needed a browser and the project had
    no runner for one. Four needed a fixture that could make a patient or a
    database, and the project's only integration test built its own world inline
    and left nothing to reuse.

    Every one was knowable at gate 0. Said at gate 1, a human narrows a
    criterion or freezes it knowing the cost, which is a decision. Said in the
    packet, it is the same fact arriving after the choice it should have
    informed.
    """
    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="a", verified_at="unit"),
        AcceptanceCriterion(id="AC-3", statement="b", verified_at="integration"),
        AcceptanceCriterion(id="AC-18", statement="c", verified_at="user"),
        AcceptanceCriterion(id="AC-19", statement="d", verified_at="user"),
    ])
    [finding] = pipeline.check_spec_testability(
        spec, _surface(integration="inline_only", user="absent"))

    assert finding.severity == "major"
    assert "can't be tested" in finding.title
    assert "3 of 4 criteria" in finding.detail, "the count moved to the elaboration, not away"
    assert set(finding.criterion_ids) == {"AC-3", "AC-18", "AC-19"}, \
        "the unit criterion, which this project can verify, was flagged anyway"
    assert "no user test runner" in finding.detail
    assert "build their own setup inline" in finding.detail
    assert "choice rather than a mistake" in finding.detail, (
        "the finding reads as an error to fix rather than as a trade to make, "
        "which is the only thing a human can act on at this gate"
    )

    # Silent when the project can verify what is being asked of it.
    assert pipeline.check_spec_testability(spec, _surface()) == []

    # And silent on a survey taken before any of this existed: nothing recorded
    # is not the same as "absent", and guessing either way puts a claim in front
    # of a human that no reading supports.
    from factory.schemas import TestingSurface
    assert pipeline.check_spec_testability(spec, TestingSurface()) == []


def test_a_criterion_whose_setup_nothing_can_arrange_is_named_before_the_freeze():
    """The arrange half, which nothing used to ask about.

    Every test is arrange, act, assert. `statement` is the act and
    `verification` is the assert; until `setup` existed there was no field for
    the arrange, and it is the only one of the three that needs affordances the
    repository may not have.

    A feature built expressly to make a repository verifiable was specified as
    twenty-one behaviours of the thing being made testable and not one
    affordance for testing it. Its tiers were all `usable` -- the project really
    did have an integration runner with reusable fixtures -- so the tier check
    passed and said nothing. Seven phases later the oracle reported five
    capabilities it could not reach, and every one was the direct consequence of
    an answer given at gate 1: a user to put in a header, a server with a
    setting unset, three entry points nothing documented how to invoke.
    """
    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="a", verified_at="integration",
                            setup=["make_patient", "api_client"]),
        AcceptanceCriterion(id="AC-2", statement="b", verified_at="integration",
                            setup=["a tenant user whose email can be sent as X-Operator-Id"]),
        AcceptanceCriterion(id="AC-3", statement="c", verified_at="integration"),
    ])
    [finding] = pipeline.check_spec_testability(spec, _surface())

    assert finding.id == "setup-gap-1"
    assert finding.criterion_ids == ["AC-2"], (
        "AC-1 names fixtures this project provides and AC-3 needs no setup at all; "
        "only the one asking for something nobody can arrange belongs here"
    )
    assert "X-Operator-Id" in finding.detail, "say what it needs, in the words it was asked in"
    assert "api_client" in finding.evidence, "and what it could have had instead"
    assert finding.severity == "major", "this is a trade to make, not a build error"


def test_a_survey_that_recorded_no_fixtures_is_not_read_as_a_project_with_none():
    """Absent and empty are different claims, here as everywhere.

    A survey taken before fixtures were recorded knows nothing about them.
    Flagging every criterion in such a project would put a claim in front of a
    human that no reading supports -- and would fire on every project surveyed
    before this field existed.
    """
    from factory.schemas import TestingSurface, TestingTier

    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="a", verified_at="integration",
                            setup=["something nothing provides"]),
    ])
    blank = TestingSurface(tiers=[
        TestingTier(tier="unit", verdict="usable"),
        TestingTier(tier="integration", verdict="usable"),
        TestingTier(tier="user", verdict="usable"),
    ])

    assert pipeline.check_spec_testability(spec, blank) == []


def test_the_interrogator_is_shown_what_a_test_can_already_arrange():
    """The one round of questions is where a missing affordance is still cheap.

    Its brief hunts in eight places and the eighth is the state a behaviour
    starts from -- a question it cannot ask without knowing what this project
    can create. It was given the intent, the scout report and nothing else, so
    the ten questions it asked on a feature built expressly to make a repository
    testable were all about what the software does.
    """
    source = factory_source("pipeline")
    i = source.index('role_prompt("interrogator")')
    body = source[i - 2500:i]

    assert "fixture_inventory(self.project.state.testing)" in body, \
        "the interrogator is asked to spot unreachable state without being shown what is reachable"

    brief = (ROOT / "factory" / "roles" / "interrogator.md").read_text(encoding="utf-8")
    assert "The state a behaviour starts from" in brief
    assert "eight ways" in brief, "the list grew and the sentence above it did not"
    assert brief.count("\n8. ") == 1


def test_the_spec_writer_is_shown_what_a_test_can_already_arrange():
    """It was asked to declare `setup` and never shown the list to declare from.

    The survey has recorded a fixture inventory since gate 0 and no intake phase
    had ever been given it, so the spec writer wrote `verified_at` -- and would now
    write `setup` -- against a project it could not see the test surface of. A
    spec whose criteria need a user nothing can create is a spec written without
    this list.
    """
    source = factory_source("pipeline")
    body = source[source.index("    async def _write_spec("):]
    body = body[:body.index("\n    def ")]

    assert "fixture_inventory(self.project.state.testing)" in body, \
        "the spec writer's prompt is built without ever reading the fixture inventory"
    assert "writer_prompt" in body and "fixture_block" in body
    assert body.index("fixture_block") < body.index("# The interrogator's reading"), \
        "what a test can arrange belongs before the reading it has to be applied to"
    # A criterion whose level nobody declared is not evidence of anything.
    unlabelled = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="a")])
    assert pipeline.check_spec_testability(unlabelled, _surface(user="absent")) == []


def test_the_oracle_sees_test_setup_and_never_a_file_that_asserts():
    """A conftest is not the implementation, and the distinction is the design.

    A fixture that authenticates as a tenant tells a test author how to reach the
    application and nothing about whether the feature works. Reading how to talk
    to the app is not reading what the app does -- only the second corrupts a
    blind test. Files that assert are never shown, because that is showing it
    the answers.
    """
    from factory.schemas import Spec as S

    rendered = workspace.testing_context(_surface(
        integration="usable",
        setup=[("backend/tests/conftest.py", "def api_client(tenant): ...")]))
    assert "def api_client(tenant): ..." in rendered, \
        "the project's own setup is not shown, so the oracle must invent its own"
    assert "`api_client`" in rendered and "`make_patient`" in rendered

    # The verdict drives the instruction, in the prompt the oracle actually gets.
    absent = workspace.testing_context(_surface(user="absent"))
    assert "no runner at this level" in absent
    assert "untestable_criteria" in absent

    # Nothing at all recorded renders nothing at all -- no empty heading that
    # reads as "there is nothing here".
    from factory.schemas import TestingSurface
    assert workspace.testing_context(TestingSurface()) == ""
    assert "What a test can be written against here" not in \
        workspace.verify_context(S(title="t", intent="i", summary="s"), ["httpx"], "", "")


def test_scaffolding_may_propose_setup_but_never_something_that_asserts():
    """The prohibition was right and too wide.

    "Never propose a test file" exists because a test you wrote is a test you
    would then be graded against. That reason does not reach a conftest, which
    asserts nothing -- and a project whose tests each build their own world gives
    a blind test author nothing to write against, which is the gap that has to be
    closable.
    """

    from factory.schemas import ProjectSurvey
    described = " ".join(
        (ProjectSurvey.model_fields["scaffolding"].description or "").split())
    assert "You MAY propose test *support*" in described
    assert "never propose a file that asserts" in described.lower()

    flat = " ".join((ROOT / "factory" / "roles" / "surveyor.md")
                    .read_text(encoding="utf-8").split())
    assert "Support only. Never a test." in flat
    assert "which criteria it would unlock" in flat, \
        "scaffolding is proposed as a file rather than as a trade"


def test_a_process_older_than_the_state_refuses_to_write_it(tmp_path):
    """The class-level fix, for a class with three members in one day.

    A process holds its models for as long as it runs, and a console server runs
    for days. One started before `services`, `test_prepare` and
    `test_file_commands` existed kept writing state without them, so an approve,
    a re-survey and a restore each silently removed the three fields that say how
    to prepare a database. The next build ran against a project with no
    migrations to apply -- no schema, no application role, every gate red on
    `password authentication failed` -- and the packet read as a broken feature.

    `extra="allow"` keeps the fields. It cannot stop an old process *acting* on
    state it half understands, which is the half that produced wrong answers. So
    a write from behind is refused outright.
    """
    from factory.projects import ProjectError, ProjectRegistry
    from factory.schemas import STATE_VERSION

    repo = tmp_path / "repo"
    repo.mkdir()
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")

    # Current code writes freely, and stamps what it wrote.
    registry.save(project, note="ok")
    assert registry.get(project.id).state.state_version == STATE_VERSION

    # State from a newer tool is refused rather than quietly written back
    # missing whatever this code has never heard of.
    project.state.state_version = STATE_VERSION + 1
    with pytest.raises(ProjectError) as caught:
        registry.save(project, note="from the future")
    assert "newer version" in str(caught.value)
    assert "Restart" in str(caught.value), \
        "the refusal does not say the one thing that resolves it"

    # A record written before the stamp existed reads as old, not as current --
    # the safe direction, since only code predating this could have written one.
    from factory.schemas import ProjectState
    assert ProjectState.model_fields["state_version"].default < STATE_VERSION


def test_state_a_process_predates_survives_a_round_trip_through_it():
    """A stale reader must not destroy fields it has never heard of.

    This is persisted state read by long-lived processes, and Pydantic drops
    unknown keys by default. `services`, `test_prepare` and `test_file_commands`
    were added while a console server was up; it read a project record carrying
    them, dropped them, and wrote the state back without them. Not emptied --
    absent from the JSON. The next baseline then ran with no application server
    and its test gate failed on a refused connection, which reads as the
    repository being broken rather than a process being old.

    Keeping them does not let old code act on them, which it cannot. It makes
    restarting the whole of the recovery, instead of re-running a paid survey.
    """
    from factory.schemas import EnvironmentSpec, ProjectState

    stored = {
        "project_id": "p", "name": "p", "repo": "/tmp/x",
        "environment": {"kind": "host", "rationale": "r",
                        "a_field_added_later": [{"name": "api"}]},
        "another_field_added_later": ["pytest {path}"],
    }
    state = ProjectState.model_validate(stored)
    written = state.model_dump(mode="json")

    assert written.get("another_field_added_later") == ["pytest {path}"], \
        "state this code predates was dropped on the way back out"
    assert written["environment"].get("a_field_added_later") == [{"name": "api"}], \
        "nested state this code predates was dropped on the way back out"

    for model in (ProjectState, EnvironmentSpec):
        assert model.model_config.get("extra") == "allow", (
            f"{model.__name__} drops what it does not recognise, so any field "
            "added while a process is running is destroyed by it rather than "
            "carried past"
        )


def test_a_new_survey_does_not_leave_the_old_baseline_standing(tmp_path):
    """A baseline measures one gate list against one environment.

    `update` has always cleared it when either changed -- that is what
    `INVALIDATES_BASELINE` is -- but `record_survey` wrote the fields directly
    and skipped the rule. Invisible in the normal flow, where a fresh baseline
    follows seconds later. Visible the moment it does not: the console draws
    each gate beside the result of the same name, so a new list over old results
    showed four gates as "not run" and four superseded ones as green.
    """
    from factory.projects import ProjectRegistry
    from factory.schemas import EnvironmentSpec, Gate, GateReport, GateResult, ProjectSurvey

    repo = tmp_path / "repo"
    repo.mkdir()
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")

    env = EnvironmentSpec(kind="host", rationale="fixture")
    project.state.gates = [Gate(name="old-tests", command="true")]
    project.state.environment = env
    project.state.baseline = GateReport(
        results=[GateResult(name="old-tests", passed=True, started=True)])
    registry.save(project, note="fixture")

    # A survey proposing a different list must take the baseline with it.
    project = registry.record_survey(registry.get(project.id), ProjectSurvey(
        name="demo", summary="s", environment=env,
        gates=[Gate(name="new-tests", command="true")]))
    assert project.state.baseline is None, (
        "a baseline measured against `old-tests` survived a list that no longer "
        "contains it, and the console would draw the two side by side"
    )

    # An identical list is not a change, and its measurement still stands.
    project.state.baseline = GateReport(
        results=[GateResult(name="new-tests", passed=True, started=True)])
    registry.save(project, note="fixture")
    project = registry.record_survey(registry.get(project.id), ProjectSurvey(
        name="demo", summary="s", environment=env,
        gates=[Gate(name="new-tests", command="true")]))
    assert project.state.baseline is not None, \
        "a survey that changed nothing threw away a measurement that was still true"


def test_the_session_wraps_the_project_gates_and_not_only_the_blind_suite():
    """The bug that got as far as a real survey.

    Services were wired into the blind run alone. The surveyor -- correctly, by
    its prompt -- then moved `uvicorn` out of the project's own test gate and
    into `services`, and that gate would have run against nothing. Anything a
    project declares as standing has to stand for every command in the
    assessment, so the session is opened around both.
    """
    src = inspect.getsource(pipeline.Factory._assess)
    session_at = src.index("async with test_session(")
    assert "self._run_project_checks(" in src[session_at:], \
        "the project's own gates run outside the prepared environment"
    assert "_run_blind_suite(" in src[session_at:], \
        "the blind suite runs outside the prepared environment"
    # And a session that never came up must not let either of them report.
    assert "started=False" in src[session_at:], \
        "gates that never ran are not marked as having not run"


def test_which_command_runs_a_file_is_matched_not_guessed():
    """A repository can hold two runners, and the model must not write the
    branch itself.

    When this was a single string, a real survey came back with
    `if [[ "$p" == frontend/* ]]; then npx vitest ...; else pytest ...; fi` --
    shell logic written blind, in a shell that may not be bash, by an agent that
    cannot run it. The same class of thing as the `nohup ... kill` dance.
    """
    from factory.schemas import TestFileCommand

    rules = [
        TestFileCommand(match="frontend/*", command="npx vitest run {path}"),
        TestFileCommand(match="*", command="cd backend && pytest ../{path}"),
    ]
    assert gates.command_for(rules, "frontend/src/a.test.tsx") == "npx vitest run {path}"
    assert gates.command_for(rules, "tests/oracle/t.py") == "cd backend && pytest ../{path}"

    # First match wins, so order is the surveyor's to get right.
    assert gates.command_for(list(reversed(rules)), "frontend/src/a.test.tsx") \
        == "cd backend && pytest ../{path}"

    # A file nothing claims is not handed to whichever rule came first.
    narrow = [TestFileCommand(match="frontend/*", command="npx vitest run {path}")]
    assert gates.command_for(narrow, "tests/oracle/t.py") == ""


def test_a_file_no_rule_claims_does_not_read_as_a_covered_suite():
    """Guessing a runner for an unmatched file reports a usage error as a
    failing criterion. Refusing to guess has to be loud instead of quiet."""
    import asyncio
    from factory.schemas import TestFileCommand

    runner = _SessionRunner()
    result, exits = asyncio.run(gates.run_per_file(
        [TestFileCommand(match="frontend/*", command="run {path}")],
        ["frontend/a.test.tsx", "tests/oracle/t.py"], ".", runner))

    assert "tests/oracle/t.py" not in exits, "an unmatched file was run by a guess"
    assert not result.started, "a suite with an unrunnable file reports as covered"
    assert not result.summary_known
    assert "no rule claims tests/oracle/t.py" in result.output_tail
    assert result.witnessed == ["frontend/a.test.tsx"]


def test_only_a_rule_a_canary_can_prove_is_searched():
    """A command nobody can prove is a claim. The canary, with its declared
    test name, is what turns an agent's answer into a measurement."""
    from factory import reporting

    found = reporting.candidates(_reporting_state())
    assert [(c.rule.match, c.path) for c in found] == [("web/*", "web/src/canary.spec.ts")], \
        "the api rule already reports; only the web one is looked for"

    unnamed = _reporting_state(blind_placements=[
        _placement(directory="web/src", filename="canary.spec.ts", canary_case="")])
    assert reporting.candidates(unnamed) == [], "no declared name, nothing to prove it against"


def test_a_search_remembers_a_proof_and_a_runner_that_cannot(tmp_path, monkeypatch):
    """A proven command is offered again at no cost; a runner the agent said
    cannot write JUnit is not asked again until its rule changes. A session
    that died settles nothing, so the next reading asks."""
    from types import SimpleNamespace
    from factory import reporting

    store = EvidenceStore(tmp_path, "project")
    state = _reporting_state()
    project = SimpleNamespace(state=state, store=store)
    asked: list[list[str]] = []
    answer = {"outcome": "error"}

    async def fake_ask(project, config, llm, found):
        asked.append([c.rule.match for c in found])
        return [reporting.Outcome(match="web/*", command="cd web && run {path}",
                                  report="cd web && run --junit {report} {path}", **answer)]
    monkeypatch.setattr(reporting, "_ask_and_prove", fake_ask)

    first = asyncio.run(reporting.search(project, Config(), None))
    assert asked == [["web/*"]] and not first.proposes
    asyncio.run(reporting.search(project, Config(), None))
    assert asked == [["web/*"]] * 2, "an error is not an answer about the runner"

    answer["outcome"] = "verified"
    third = asyncio.run(reporting.search(project, Config(), None))
    assert third.proposes and len(asked) == 3
    fourth = asyncio.run(reporting.search(project, Config(), None))
    assert len(asked) == 3, "a proven command is offered again without asking anyone"
    assert [r.report for r in fourth.rules] == [
        "cd web && run --junit {report} {path}", "cd api && run --xml={report} {path}"]
    assert fourth.reason.startswith("Lets each test report its own result")

    state.test_file_commands[0] = _rule("cd web && run --ci {path}", match="web/*")
    asyncio.run(reporting.search(project, Config(), None))
    assert len(asked) == 4, "a changed rule is a new question"


def test_a_reporting_command_is_kept_only_when_the_canary_proves_it(tmp_path, monkeypatch):
    """The agent's answer is a proposal. What makes it evidence is the check
    every placement already passes: the canary run with the proposed command,
    and a report that names its test."""
    import json
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from factory import executors as executors_module, reporting, sandbox as sandbox_module
    from factory import unitenv as unitenv_module
    from factory.config import load_config
    from factory.schemas import EnvironmentSpec, FileWrite

    rules = [_rule("run {path}", match="web/*"), _rule("run {path}", match="api/*"),
             _rule("run {path}", match="lib/*"), _rule("run {path}", match="cli/*")]
    placements = [_placement(directory=d, filename="canary_test.py", canary_case="it passes")
                  for d in ("web", "api", "lib", "cli")]
    state = SimpleNamespace(test_file_commands=rules, blind_placements=placements)
    project = SimpleNamespace(state=state, store=EvidenceStore(tmp_path, "p"),
                              environment=EnvironmentSpec(kind="derive"), repo_path=tmp_path,
                              id="p", base_ref="HEAD")
    released = []
    monkeypatch.setattr(sandbox_module.Sandbox, "create", classmethod(
        lambda cls, **kw: SimpleNamespace(path=str(tmp_path),
                                          release=lambda **k: released.append(True))))
    runner = _ReportingRunner(["it passes"])

    @asynccontextmanager
    async def fake_env(project, config, **kw):
        yield SimpleNamespace(ready=True, runner=runner, problem="")
    monkeypatch.setattr(unitenv_module, "unit_environment", fake_env)

    answer = {
        "rules": [
            {"match": "web/*", "report": "run --junit={report} {path}", "how_found": "its --help"},
            {"match": "api/*", "report": "run --junit=/tmp/fixed.xml {path}"},
            {"match": "lib/*", "report": "run --junit={report} {path}"},
        ],
        "could_not": [{"match": "cli/*", "why": "only through a config file"}],
    }
    names = {"lib": ["something else"]}

    async def fake_author(self, **kw):
        return SimpleNamespace(files=[FileWrite(path=reporting.ANSWER_FILE,
                                                contents=json.dumps(answer), purpose="")])
    monkeypatch.setattr(executors_module.CommandExecutor, "author", fake_author)

    original = runner.execute

    async def execute(command, **kw):
        # What the pretend runner calls the canary depends on the directory run.
        runner.names = next((n for d, n in names.items() if f" {d}/" in command),
                            ["it passes"])
        return await original(command, **kw)
    runner.execute = execute

    got = {o.match: o for o in asyncio.run(
        reporting._ask_and_prove(project, load_config(), None, reporting.candidates(state)))}
    assert got["web/*"].outcome == "verified" and got["web/*"].evidence == "its --help"
    assert got["api/*"].outcome == "unproven" and "{report}" in got["api/*"].why, \
        "a report path the harness cannot substitute is refused before anything runs"
    assert got["lib/*"].outcome == "unproven" and "something else" in got["lib/*"].why, \
        "a report that names a different test proves nothing about this one"
    assert got["cli/*"].outcome == "none" and "config file" in got["cli/*"].why
    assert released == [True], "the throwaway checkout goes, whatever was found"


def test_finding_a_report_command_knows_no_runner():
    """The survey missed Angular's reporter flag, and the tempting fix was to
    teach the survey Angular. Then the next project's runner is missed the same
    way. What is taught instead is to ask the runner that is installed, and
    what is trusted is the canary -- so no runner's name belongs in either."""
    from factory import reporting

    prompt = (ROOT / "factory" / "roles" / "reporter.md").read_text(encoding="utf-8").lower()
    code = strip_prose(inspect.getsource(reporting)).lower()
    for name in ("vitest", "jest", "pytest", "ng test", "angular", "karma", "mocha",
                 "maven", "mvn", "gradle", "surefire", "playwright", "rspec", "go test",
                 "dotnet", "--reporter", "--junit"):
        named = re.compile(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])")
        assert not named.search(prompt), f"the reporter's prompt names {name!r}"
        assert not named.search(code), f"reporting.py names {name!r}"


def test_the_oracle_is_told_what_this_runner_calls_a_test():
    """The oracle declares a name for every test, and the name is all a report
    joins on. It was told to use "the name your framework will print" and could
    not know it. Library's API runner reports a test's plain name and ignores a
    display title; the oracle declared titles, nothing joined, and two failing
    tests reached the packet as `unknown`. The placement check had measured the
    answer on a canary. Now the oracle is shown it -- in the session that writes
    the tests and in the call that names them."""
    from types import SimpleNamespace
    from factory.pipeline import runner_naming

    placements = [_placement(directory="api/it", filename="CanaryIT.java", canary_case="canary",
                             canary_passes="class CanaryIT { void canary() {} }"),
                  _placement(directory="web/app", filename="canary.spec.ts", canary_case="")]
    probes = [SimpleNamespace(directory="api/it", reported_case="canary"),
              SimpleNamespace(directory="web/app", reported_case="suite > runs")]
    said = runner_naming(placements, probes)
    assert "`api/it/`" in said and "class CanaryIT" in said
    assert "declared as `canary`" in said and "report called it `canary`" in said
    assert "web/app" not in said, "a placement with no declared name measured nothing"
    assert runner_naming(placements, []) == "", "unmeasured is said as nothing, not guessed"

    # A report that lists the canary twice is one test, not a naming pattern.
    # Shown verbatim -- `a > b, a > b` -- the oracle declared every test as
    # `name, name`, and eight passing tests reached the packet as unknown.
    twice = [_placement(directory="web/app", filename="canary.spec.ts", canary_case="runs")]
    said = runner_naming(twice, [SimpleNamespace(directory="web/app",
                                                 reported_case="canary > runs, canary > runs")])
    assert "report called it `canary > runs`." in said
    assert "canary > runs, canary > runs" not in said
    assert "more than once" in said and "each named once" in said

    src = inspect.getsource(pipeline.Factory._author_blind_suite)
    assert src.count("runner_naming(") == 2
    assert src.count("brief=context + naming") == 2, "the call that names the tests is told too"


def test_a_name_that_matched_nothing_still_shows_what_the_runner_reported():
    """Unknown stays unknown -- which reported test is which cannot be told --
    but a file whose runner reported two failures must not reach a person as a
    bookkeeping fault and nothing else."""
    from factory.pipeline import reported_in
    from factory.schemas import CaseOutcome, GateReport, GateResult

    gates = GateReport(results=[GateResult(
        name="blind-tests", command="run", exit_code=1, passed=False, cases=[
            CaseOutcome(file="api/it/RatingsIT.java", name="averagesRatings", status="failed"),
            CaseOutcome(file="api/it/RatingsIT.java", name="nullWhenUnrated", status="failed"),
            CaseOutcome(file="api/it/RatingsIT.java", name="rejectsUnknownBook", status="passed"),
            CaseOutcome(file="web/other.spec.ts", name="elsewhere", status="failed"),
        ])])
    said = reported_in(["api/it/RatingsIT.java::[AC-12] stats average every rated book"], gates)
    assert said.startswith("The runner did report 3 test(s) in that file")
    assert "2 failed (averagesRatings, nullWhenUnrated)" in said and "1 passed" in said
    assert "elsewhere" not in said
    assert reported_in(["nowhere.java::x"], gates) == ""


def test_a_red_check_is_parked_rather_than_blocking_or_rotting(tmp_path):
    """The clinic case: 129 lint errors and 69 type errors, nothing broken.

    This rule has been both ways and is now a third thing.

    It first asked only "will these gates produce information", on the grounds
    that a failing gate is re-run at each feature's base commit and so is
    attributable without ever having been green. That was reversed because it
    was not enough: a check failing identically in every packet teaches a reader
    to skim red, and no single feature is to blame while the aggregate rots.

    Both of those kept the red check in the set that judges a feature. Parking
    does not. It never reaches a packet, so it cannot train anyone to skim
    anything, and it stays on gate 0 where it is a job to do rather than noise
    to ignore -- which is what a repository with real debt needs, because
    requiring green locked out the projects most in need of the tool.

    Derived from the last baseline, never stored, so the run that finds it green
    un-parks it with nobody having to remember.
    """
    from factory.projects import ProjectError, ProjectRegistry
    from factory.schemas import Gate

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("x = 1\n")
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "clinic-like")
    project.state.gates = [
        Gate(name="frontend-build", command="npm run build"),
        Gate(name="backend-lint", command="ruff check ."),
    ]
    project.state.baseline = gates.GateReport(results=[
        _result("frontend-build", passed=True, output_tail="built"),
        _result("backend-lint", exit_code=1, output_tail="Found 129 errors."),
    ])
    _proved_placement(project)

    assert project.parked_checks == ["backend-lint"], "a measured red check is not parked"
    assert [g.name for g in project.judging_gates] == ["frontend-build"], (
        "a parked check is still in the set a feature is judged by, so it will be red in "
        "every packet -- which is the failure that made requiring green look necessary")

    approved = registry.approve(project)
    assert approved.state.stage == "ready", (
        "a repository with pre-existing failures still cannot onboard, which is the deadlock "
        "this rule exists to remove")

    # It arms itself. Nothing records that it was parked; the next measurement
    # decides, so the run that finds it green is all it takes.
    approved.state.baseline = gates.GateReport(results=[
        _result("frontend-build", passed=True),
        _result("backend-lint", passed=True),
    ])
    assert approved.parked_checks == []
    assert len(approved.judging_gates) == 2

    # A check the baseline says nothing about is unmeasured, not red. Parking it
    # would quietly drop a check nobody has decided anything about.
    approved.state.gates.append(Gate(name="types", command="mypy"))
    assert approved.parked_checks == [], "an unmeasured check was parked"
    assert len(approved.judging_gates) == 3

    # And something has to be judging. Every check parked is a feature checked
    # by nothing, whose packet would say `0 of 0 passed`.
    bare = registry.create(repo, "all-red")
    bare.state.gates = [Gate(name="lint", command="ruff check .")]
    bare.state.baseline = gates.GateReport(results=[_result("lint", exit_code=1)])
    _proved_placement(bare)
    with pytest.raises(ProjectError) as caught:
        registry.approve(bare)
    assert "no check that passes" in str(caught.value)
    assert "0 of 0 passed" in str(caught.value), (
        "the refusal does not say what would actually happen")


def test_scaffolding_is_committed_so_the_checks_can_see_it(tmp_path):
    """A change this tool recommends has to be a real change to the repository.

    Every check runs in a sandbox, and a sandbox is `git worktree add` at a
    commit -- which carries tracked files only. Scaffolding written into the
    working tree and left uncommitted is therefore invisible to the very gates
    it exists to unblock: three files sat in a working tree while
    `frontend-build` failed with "tsconfig.build.json does not exist", true of
    the sandbox and false of the repository, with nothing saying so.

    Writing the file and leaving the human to commit it is not applying a
    change; it is handing them homework without saying so.
    """
    import subprocess
    from factory.projects import ProjectRegistry
    from factory.schemas import EnvironmentSpec, ProjectSurvey, ScaffoldFile

    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    for cmd in (["git", "config", "user.email", "t@t"], ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=repo, check=True)
    (repo / "a.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

    # Something of the human's own, staged and not theirs to commit for them.
    (repo / "mine.txt").write_text("work in progress\n")
    subprocess.run(["git", "add", "mine.txt"], cwd=repo, check=True)

    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")
    project.state.survey = ProjectSurvey(
        name="demo", summary="s", environment=EnvironmentSpec(kind="host", rationale="x"),
        scaffolding=[ScaffoldFile(path="conftest.py", contents="import pytest\n",
                                  purpose="lets the test gate run")])
    registry.save(project, note="fixture")

    out = registry.apply_scaffolding(registry.get(project.id), ["conftest.py"])
    assert out["written"] == ["conftest.py"]
    assert out["commit"], f"nothing was committed: {out.get('commit_problem')}"

    # It is in the commit, so a worktree at that commit has it.
    tracked = subprocess.run(["git", "ls-files", "conftest.py"], cwd=repo,
                             capture_output=True, text=True).stdout.strip()
    assert tracked == "conftest.py", "the file is written but not tracked, so no sandbox will see it"

    # And the human's own staged work was not swept into it.
    still_staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=repo,
                                  capture_output=True, text=True).stdout.split()
    assert "mine.txt" in still_staged, (
        "a human's staged work was committed along with the scaffolding -- this tool does not get "
        "to decide what else goes into their history")


def test_a_project_that_cannot_carry_a_blind_test_is_refused(tmp_path):
    """The refusal that would have stopped every failed run at gate 0.

    A gate that cannot run is refused because it is red forever and tells nobody
    anything. A placement that cannot be proved is worse, because it is not red:
    `tests/oracle` sat outside the tree that owned `pytest.ini` run after run,
    every async test in every blind suite failed with "async def functions are
    not natively supported", and each packet reported a broken feature. Nothing
    said the harness was the reason, because nothing had asked.
    """
    from factory.projects import ProjectError, ProjectRegistry
    from factory.schemas import BlindPlacement, PlacementResult

    repo = tmp_path / "repo"
    repo.mkdir()
    registry = ProjectRegistry(tmp_path / "evidence")

    # 1. Nowhere declared at all.
    project = registry.create(repo, "no-placement")
    # A check to go with the result. The fixture used to record a baseline for a
    # check that was not on the list, which reached the placement rule only
    # because nothing yet asked whether anything was judging at all.
    project.state.gates = [Gate(name="tests", command="true")]
    project.state.baseline = gates.GateReport(results=[_result("tests", passed=True)])
    try:
        registry.approve(project)
        raise AssertionError("a project with nowhere to put a blind test was approved")
    except ProjectError as exc:
        assert "nowhere to put a blind test" in str(exc)
        assert "Survey it again" in str(exc), "the refusal must say how to fix it"

    # 2. Declared, measured, and not one of them worked.
    project2 = registry.create(repo, "unproved-placement")
    project2.state.gates = [Gate(name="tests", command="true")]
    project2.state.baseline = gates.GateReport(results=[_result("tests", passed=True)])
    project2.state.blind_placements = [BlindPlacement(
        directory="tests/oracle", filename="test_canary.py",
        canary_passes="PASS", canary_fails="FAIL")]
    project2.state.placement_probe = [PlacementResult(
        directory="tests/oracle", passing_ran=False, usable=False,
        note="this project's configuration does not reach it")]
    try:
        registry.approve(project2)
        raise AssertionError("a project whose only placement failed measurement was approved")
    except ProjectError as exc:
        assert "not one of them could be proved" in str(exc)
        assert "tests/oracle" in str(exc), "the human needs to know which one"
        assert "configuration does not reach it" in str(exc), "and why it failed"

    # 3. One that was proved is enough.
    project3 = registry.create(repo, "proved-placement")
    project3.state.gates = [Gate(name="tests", command="true")]
    project3.state.baseline = gates.GateReport(results=[_result("tests", passed=True)])
    _proved_placement(project3)
    assert registry.approve(project3).state.stage == "ready"


# --------------------------------------------------------------------------
# a gate, a scaffold and a recommendation are three different things
#
# Clinic's `backend-lint` came from a `.ruff_cache/` entry in `.gitignore`:
# nobody had ever run ruff to green, and adopting it as a gate produced 129
# blocking errors on a repository that had never been linted. That is what
# happens when "should" is written into the gate list. The tool belongs in
# `recommendations`, where it costs nothing and loses nothing.
# --------------------------------------------------------------------------


def test_a_recommendation_is_not_a_gate_and_cannot_become_one_by_accident():
    from factory.schemas import ProjectSurvey, Recommendation

    survey = ProjectSurvey(
        name="p", summary="s",
        gates=[Gate(name="tests", command="pytest -q")],
        environment=EnvironmentSpec(kind="host", rationale="r"),
        recommendations=[Recommendation(
            title="Adopt mypy", kind="types",
            why="two typed constructors are splatted with untyped dicts",
            evidence="app/routers/queue.py:662",
            would_gate="mypy app/",
        )],
    )
    assert [g.name for g in survey.gates] == ["tests"], \
        "a recommendation must never appear in the gate list"
    assert survey.recommendations[0].would_gate, \
        "what it would make possible is recorded, not proposed"


def test_the_surveyor_is_told_the_difference_between_the_three():
    text = (PACKAGE / "roles" / "surveyor.md").read_text()
    for word in ("`gates`", "`scaffolding`", "`recommendations`"):
        assert word in text, f"the surveyor prompt does not mention {word}"
    # The specific mistake that produced clinic's blocked onboarding. The rule
    # now lives in the evidence hierarchy rather than as its own sentence.
    prose = " ".join(text.split()).lower()
    # Asserted around the markup rather than through it: `**` inside the phrase
    # is where the bold happens to start, which is not a property of the rule.
    assert "cache directory in `.gitignore`" in prose
    assert "somebody ran it once. that is all." in prose
    assert "if the only sign of a tool is a gitignore entry, it belongs in" in prose
    # And the rule that survives into the new field.
    body = text[text.index("Three places a tool can go"):]
    assert "Never the tests themselves." in body


def test_surveying_records_what_it_read(tmp_path):
    from factory.projects import ProjectRegistry

    repo = _git_repo(tmp_path / "repo")
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "p")
    from factory.schemas import ProjectSurvey
    survey = ProjectSurvey(
        name="p", summary="s", gates=[Gate(name="t", command="true")],
        environment=EnvironmentSpec(kind="host", rationale="r"),
    )
    saved = registry.record_survey(project, survey)
    assert saved.state.survey_sha, "the commit the survey read"
    assert "pyproject.toml" in saved.state.survey_paths
    assert not registry.tooling_drift(saved)["stale"], "nothing has changed yet"


def test_only_the_changes_a_human_accepted_are_applied(tmp_path):
    from factory.schemas import ProposedGateChange, SurveyDiff

    registry, project = _project_with_gates(
        tmp_path,
        Gate(name="tests", command="pytest -q"),
        Gate(name="types", command="mypy app/"),
    )
    diff = SurveyDiff(
        summary="a DSL linter arrived",
        gate_changes=[
            ProposedGateChange(action="add", name="dsl-lint", reason="druff.toml appeared",
                               evidence="druff.toml", gate=Gate(name="dsl-lint", command="druff check")),
            ProposedGateChange(action="remove", name="types", reason="allegedly unused",
                               evidence="none really"),
            ProposedGateChange(action="change", name="tests", reason="script moved",
                               evidence="package.json", gate=Gate(name="tests", command="pytest -q tests/")),
        ],
    )
    updated, applied = registry.apply_survey_diff(project, diff, ["add:dsl-lint"])

    names = [g.name for g in updated.state.gates]
    assert "dsl-lint" in names, "the accepted addition landed"
    assert "types" in names, "a removal nobody accepted must not happen"
    assert next(g for g in updated.state.gates if g.name == "tests").command == "pytest -q", \
        "an unaccepted change leaves the gate exactly as it was"
    assert applied == ["add:dsl-lint"]


def test_what_was_turned_down_is_on_the_record(tmp_path):
    from factory.schemas import ProposedGateChange, SurveyDiff

    registry, project = _project_with_gates(tmp_path, Gate(name="types", command="mypy app/"))
    diff = SurveyDiff(
        summary="s",
        gate_changes=[ProposedGateChange(
            action="remove", name="types", reason="keeps failing", evidence="the baseline")],
    )
    updated, applied = registry.apply_survey_diff(project, diff, [])

    assert applied == [] and [g.name for g in updated.state.gates] == ["types"]
    ruling = [r for r in updated.store if r["kind"] == "resurvey_ruling"][-1]["payload"]
    assert ruling["rejected"] == ["remove:types"]
    assert ruling["proposed"] == ["remove:types"], (
        "six months from now, 'the surveyor wanted to drop the type check and we said no' "
        "has to be answerable"
    )


def test_accepting_a_gate_change_clears_the_baseline(tmp_path):
    """The green result a human approved was measured against a gate list that
    no longer exists."""
    from factory.schemas import ProposedGateChange, SurveyDiff

    registry, project = _project_with_gates(tmp_path, Gate(name="tests", command="pytest -q"))
    assert project.state.baseline is not None and project.state.stage == "ready"

    diff = SurveyDiff(summary="s", gate_changes=[ProposedGateChange(
        action="add", name="lint", reason="ruff.toml appeared", evidence="ruff.toml",
        gate=Gate(name="lint", command="ruff check ."))])
    updated, _ = registry.apply_survey_diff(project, diff, ["add:lint"])

    assert updated.state.baseline is None, "the baseline no longer describes these gates"
    assert updated.state.stage != "ready", "and the project is back at gate 0"


def test_an_add_with_no_gate_definition_is_refused_not_guessed(tmp_path):
    from factory.schemas import ProposedGateChange, SurveyDiff

    registry, project = _project_with_gates(tmp_path, Gate(name="tests", command="pytest -q"))
    diff = SurveyDiff(summary="s", gate_changes=[ProposedGateChange(
        action="add", name="lint", reason="a linter appeared", evidence="ruff.toml")])
    updated, applied = registry.apply_survey_diff(project, diff, ["add:lint"])

    assert applied == [] and [g.name for g in updated.state.gates] == ["tests"]
    ruling = [r for r in updated.store if r["kind"] == "resurvey_ruling"][-1]["payload"]
    assert any("no gate definition" in r for r in ruling["rejected"])


def test_the_resurvey_prompt_forbids_dropping_an_inconvenient_check():
    text = (PACKAGE / "roles" / "surveyor.md").read_text()
    body = text[text.index("**`remove`**"):]
    assert "a failing check is a working check" in body.lower()
    assert "Nothing you propose is applied." in text
    assert "Empty is the common answer" in text
    # A red check now has to be resolved before the project builds anything,
    # which gives `remove` a second thing to be mistaken for. The three ways out
    # are a human's, and none of them is this call.
    prose = _prose(body)
    assert "fix it, ratchet it at today's count, or decline it with a reason" in prose
    assert "None of them is `remove`" in prose


def test_no_command_in_config_binds_a_port_it_wrote_down():
    """The rule the surveyor is given, applied to the config that ships.

    A fixed port does not fail cleanly when something else already holds it: it
    binds alongside, the readiness probe goes green, and the suite runs to
    completion against a different process. `rework.oracle_command` hardcoded
    8000 in four places while its own teardown was broken, so every round left
    a server holding the port the next round needed.

    Ask the kernel for one instead. `$(...)` survives `_expand`, so a port put
    in a file and read back is the shape that works here.
    """
    root = ROOT
    listen = re.compile(
        r"(?:--port[= ]|:)(\d{2,5})\b(?!.*\bcurl\b.*--version)")
    offences = []
    for name in ("factory.yaml", "factory.example.yaml"):
        path = root / name
        if not path.exists():
            continue
        import yaml
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for where, command in _config_command_strings(raw):
            for m in listen.finditer(command):
                port = int(m.group(1))
                # 0 is "ask the kernel", which is the thing being asked for.
                if port and port != 0:
                    offences.append(f"{name}:{where} writes down port {port}")
    assert not offences, (
        "a command that writes a port down cannot fail cleanly when something "
        "else holds it:\n" + "\n".join(offences)
    )


def test_a_command_run_from_the_wrong_directory_is_caught(tmp_path):
    """Reading a CI workflow gives correct commands stripped of the
    `working-directory:` that scoped them. Every gate then runs from the repo
    root: alembic cannot find alembic.ini, npm cannot find package.json, and
    mypy exits with a usage error rather than a type error. All five gates fail
    for the same reason and none of them is about the code."""
    from factory.projects import ProjectRegistry

    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "frontend").mkdir()
    for name in ("alembic.ini", "pytest.ini", "ruff.toml", "mypy.ini"):
        (repo / "backend" / name).write_text("")
    (repo / "frontend" / "package.json").write_text("{}")

    env = EnvironmentSpec(kind="host", rationale="r", workdir="/app")
    problems = ProjectRegistry.working_directory_problems(env, [
        Gate(name="migrate", command="alembic upgrade head"),
        Gate(name="build", command="npm run build"),
    ], repo)
    assert len(problems) == 2
    assert "`cd backend && alembic upgrade head`" in problems[0], \
        "naming the fix is the point; the reader should not have to work it out"
    assert "`cd frontend && npm run build`" in problems[1]

    # Correctly scoped commands are silent.
    assert ProjectRegistry.working_directory_problems(env, [
        Gate(name="test", command="cd backend && pytest"),
        Gate(name="build", command="cd frontend && npm run build"),
    ], repo) == []
    # And a tool that reads no configuration from the cwd is not this check's business.
    assert ProjectRegistry.working_directory_problems(
        env, [Gate(name="x", command="echo hello")], repo) == []


def test_an_attempt_nothing_was_spent_on_is_given_back():
    """`attempts` is what retires a finding as tried-and-unfixable.

    Charging it for a round where the harness made no edits retires a real
    defect on the strength of a broken tool -- and `attempted_not_fixed` is the
    packet's way of telling a human that people looked at this and could not
    fix it.
    """
    ledger = pipeline.FindingLedger()
    ids = ledger.add("reviewer", [Finding(
        id="", severity="blocker", title="duplicate class crashes the import", detail="d")], 0)
    ledger.records[ids[0]].disposition = "repair"

    ledger.attempted(ids, 1)
    assert ledger.repairable(1) == [], "one attempt should exhaust a one-attempt budget"

    ledger.not_attempted(ids, "the harness made no edits")
    assert ledger.repairable(1) == ids, (
        "a round nobody worked in has retired a finding nobody tried to fix"
    )
    assert "no edits" in ledger.records[ids[0]].outcome_evidence


def test_looking_for_a_feature_that_does_not_exist_writes_nothing(tmp_path):
    """A store is constructed to *read*, and reading is not an event.

    It used to create its directory and an empty ledger the moment it was
    constructed, so every request for a feature that does not exist left one
    behind: a feature discarded a minute earlier reappeared as an empty
    directory when the console polled it, and one URL carrying a literal "null"
    made a project called null.
    """
    root = tmp_path / "features"
    ghost = store.EvidenceStore(root, "never-existed")
    assert ghost.records() == []
    assert not ghost.dir.exists(), "looking at a feature created it"
    assert not root.exists() or not any(root.iterdir()), "the root grew a ghost"

    # And a real record still lands, directory and all.
    real = store.EvidenceStore(root, "actually-used")
    real.append("state", {"stage": "intake"}, role="orchestrator")
    assert real.file.exists() and len(real.records()) == 1
    # Reopening an existing store still counts what is there, so `seq` cannot
    # restart and overwrite history.
    assert store.EvidenceStore(root, "actually-used").append("state", {})["seq"] == 2


def test_a_name_cut_by_the_output_tail_cannot_become_a_pass():
    """The run that shipped two false passes, and why it cannot recur.

    A failing test's name straddled the 4,000-character tail boundary, the
    search for it found nothing, "not named in the failing output" was read as
    "not implicated", and two criteria that file was the only test for were
    reported to a human as passed while the file was erroring.

    There is no search. Each blind test file is run as its own command and its
    own exit code is its verdict, so the console output can be empty, truncated,
    or in a format nobody has ever seen, and the verdict is unaffected. That is
    the property -- not a bigger search window, but no search window.
    """
    cut = "backend/tests/test_screening_slots_activate.py"
    kept = "backend/tests/test_screening_slots_migration.py"

    # The tail says nothing about either file. It used to be the only evidence.
    tail = "... [head truncated]\nent' not found\n= 2 failed in 0.4s ="
    assert cut not in tail and kept not in tail

    result = _reported("blind-tests", passed=False, tail=tail,
                       witness=[cut, kept], failing=[cut, kept], total=2)
    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-3", statement="activate accepts the fields"),
        AcceptanceCriterion(id="AC-1", statement="the columns exist")])
    trace = [
        TraceRow(criterion_id="AC-3", test_names=[cut], status="traced"),
        TraceRow(criterion_id="AC-1", test_names=[kept], status="traced"),
    ]
    qa = pipeline.compute_qa(spec, None, gates.GateReport(results=[result]), trace)
    by_id = {r.criterion_id: r for r in qa.results}
    assert by_id["AC-3"].status == "failed", \
        "the criterion whose only test errored was not reported as failing"
    assert by_id["AC-1"].status == "failed"


def test_a_negative_is_never_settled_by_evidence_that_could_not_settle_it():
    """A gate with no parsed report cannot clear a criterion, or condemn one.

    This used to salvage a positive from a stale result's console tail -- "this
    name appears on a failing line, so that criterion failed" -- while refusing
    the negative. Both halves are gone, and deliberately: a result that never
    produced a report is a result with no evidence in it, and reading its tail
    is the practice that put two false passes in a packet. Unknown in both
    directions is the honest answer, and it is never a pass.
    """
    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="a"),
        AcceptanceCriterion(id="AC-2", statement="b")])
    trace = [
        TraceRow(criterion_id="AC-1", test_names=["tests/test_a.py"], status="traced"),
        TraceRow(criterion_id="AC-2", test_names=["tests/test_b.py"], status="traced"),
    ]
    # Never parsed: a tail naming test_a, and nothing else. Exactly the kind of
    # result the old code would have drawn a conclusion from.
    stale = gates.GateResult(
        name="tests", passed=False, exit_code=1,
        output_tail="FAILED tests/test_a.py::test_one\n= 1 failed, 3 passed in 0.2s =")

    qa = pipeline.compute_qa(spec, None, gates.GateReport(results=[stale]), trace)
    by_id = {r.criterion_id: r for r in qa.results}
    for cid in ("AC-1", "AC-2"):
        # `not_run`, not `unknown`: with nothing read, the gate names no test at
        # all, so neither criterion is even attributable. What matters is the
        # half that is not a spelling -- neither is `passed`, in either
        # direction, off a result that produced no report.
        assert by_id[cid].status == "not_run", \
            f"{cid} was decided by a console tail that settles nothing"
        assert by_id[cid].status != "passed"
    assert "not passed" in by_id["AC-2"].evidence.lower()
    assert qa.summary.startswith("0 of 2 criteria verified"), qa.summary
    assert "cannot be shown to have run" in qa.summary, \
        "the human is not told the run's evidence was too weak to judge"


def test_an_unsettled_criterion_is_not_counted_as_verified():
    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="a"),
        AcceptanceCriterion(id="AC-2", statement="b")])
    trace = [
        TraceRow(criterion_id="AC-1", test_names=["tests/test_a.py"], status="traced"),
        TraceRow(criterion_id="AC-2", test_names=["tests/test_b.py"], status="traced"),
    ]
    stale = gates.GateResult(
        name="tests", passed=False, exit_code=1,
        output_tail="FAILED tests/test_a.py::test_one\n= 1 failed in 0.2s =")
    qa = pipeline.compute_qa(spec, None, gates.GateReport(results=[stale]), trace)
    assert qa.summary.startswith("0 of 2 criteria verified"), qa.summary


def test_the_witness_list_covers_the_support_files_too():
    """`oracle.tests` is the asserting half of the suite, not the suite.

    A runner names a conftest when it fails to import one, and that is the
    failure the witness list most needs to be able to attribute: an import error
    in a fixture module fails every test in the suite. Passing only `.tests`
    would leave the gate unable to name the file the error is about, and the
    criteria would come back `unknown` for a reason nothing in the packet
    states.
    """
    suite = OracleSuite(
        strategy="s",
        tests=[OracleTestFile(path="tests/oracle/test_a.py", contents="",
                              criterion_ids=["AC-1"])],
        support=[SupportFile(path="tests/oracle/conftest.py", contents="")],
    )
    assert [f.path for f in pipeline.oracle_files(suite)] == [
        "tests/oracle/test_a.py", "tests/oracle/conftest.py"]
    assert pipeline.oracle_files(None) == []


def test_a_genuinely_absent_file_still_reads_as_absent(tmp_path):
    """The narrowing must not go so far that the original signal is lost."""
    section = pipeline.tree_section(tmp_path, ["backend/app/models/trial.py"])
    assert "no such file in the working tree" in section


def test_a_blind_test_goes_where_its_own_runner_was_proved():
    """Routed per file, on the extension the placement was measured with.

    One directory for the whole suite was wrong for any repository with two
    runners, and wrong on a real one in the way that costs a run: a React
    component test landed in a directory whose per-file command is pytest, so
    the criteria it covered came back failed for a reason no code caused.

    Nothing here knows what `.tsx` is. It knows a placement was proved with a
    file ending `.tsx` and this file ends `.tsx` too.
    """
    places = [_pl("backend/tests/oracle", "test_canary.py"),
              _pl("frontend/src/__tests__/oracle", "Canary.test.tsx")]

    assert pipeline.place_blind_file(places, "backend/tests/test_slots.py") == \
        "backend/tests/oracle/test_slots.py"
    assert pipeline.place_blind_file(places, "Screening.test.tsx") == \
        "frontend/src/__tests__/oracle/Screening.test.tsx"
    # Already in a proved place: left alone, not re-flattened.
    assert pipeline.place_blind_file(places, "backend/tests/oracle/conftest.py") == \
        "backend/tests/oracle/conftest.py"
    # The name its failures are reported under survives the move.
    assert pipeline.place_blind_file(places, "./deep/nested/test_a.py").endswith("/test_a.py")

    src = inspect.getsource(pipeline.Factory._build)
    assert "place_blind_file(" in src and "state.feature_id, nestable)" in src, \
        "the oracle's paths reach the branch wherever the model chose to put them"
    assert "self.project.state.blind_placements" in src, \
        "placement comes from the project, not from this tool's configuration"


def test_a_blind_test_no_proved_placement_claims_is_not_guessed_at():
    """This reverses an older rule, deliberately.

    A blind file used to be moved rather than refused, on the reasoning that a
    feature with no acceptance tests is worse than badly-placed ones. That was
    true when there was one directory and the only question was tidiness. It is
    false once placement decides whether the file can run at all: a `.tsx` test
    dropped into a directory whose command is pytest does not produce a weak
    check, it produces a failing one, and the criteria it covers are reported
    against the feature.

    So a test whose extension nothing proved is left out and recorded, and the
    criteria it covered are unverified -- which is true -- rather than failed,
    which is not.
    """
    places = [_pl("backend/tests/oracle", "test_canary.py")]
    assert pipeline.place_blind_file(places, "Screening.test.tsx") == "", \
        "a test was placed where nothing measured can run it"
    assert pipeline.place_blind_file([], "test_a.py") == "", \
        "a project with no proved placement has nowhere to put a blind test"

    src = inspect.getsource(pipeline.Factory._build)
    assert 'store.append("blind_unplaceable"' in src, \
        "a dropped blind test leaves no trace, so nobody can find out why"
    # Support asserts nothing and has no runner to be collected by, so a README
    # beside the tests must not be thrown away for having no extension match.
    assert "if not relocated and not asserts and placements:" in src, \
        "a support file with no matching runner is dropped like a test"


def test_the_oracles_directory_is_protected_like_the_breakers(tmp_path):
    config = Config()
    (tmp_path / "tests" / "oracle").mkdir(parents=True)
    (tmp_path / "tests" / "oracle" / "test_a.py").write_text("def test_a(): ...\n")

    protected = pipeline.protected_paths(config, tmp_path)
    assert "tests/oracle/test_a.py" in protected

    # And a file created there later -- the interesting case, since that is how
    # a repairer would make a blind test pass rather than making the code work.
    assert pipeline.is_protected("tests/oracle/conftest.py", protected, config)
    assert pipeline.is_protected("tests/oracle/test_new.py", [], config)


def test_protection_follows_the_project_to_where_its_blind_tests_actually_go(tmp_path):
    """INV-12 has to cover the directories in use, not the one in the config.

    Blind tests now live where the project proved they can run -- inside the
    tree that owns the runner's configuration, which is nowhere near
    `rework.oracle_dir`. Protection computed from the constant alone would leave
    every real blind test editable by a repairer, and the hole would be
    invisible: the suite still passes afterwards, because the failing test is
    what got edited.
    """
    config = Config()
    own = ["backend/tests/oracle", "frontend/src/__tests__/oracle"]
    (tmp_path / "backend" / "tests" / "oracle").mkdir(parents=True)
    (tmp_path / "backend" / "tests" / "oracle" / "test_a.py").write_text("def test_a(): ...\n")

    assert "backend/tests/oracle/test_a.py" not in pipeline.protected_paths(config, tmp_path), \
        "this test proves nothing unless the configured directory misses it"

    protected = pipeline.protected_paths(config, tmp_path, own_dirs=own)
    assert "backend/tests/oracle/test_a.py" in protected

    # And a file created there later, which is how a repairer would make a
    # blind test pass without making the code work.
    assert pipeline.is_protected("backend/tests/oracle/conftest.py", [], config, own)
    assert pipeline.is_protected("frontend/src/__tests__/oracle/A.test.tsx", [], config, own)
    assert not pipeline.is_protected("frontend/src/App.tsx", [], config, own), \
        "protection spread beyond the blind directories and froze the implementation"

    src = inspect.getsource(pipeline.Factory._converge)
    assert "own = self._own_blind_dirs(state) + self._breaker_own_dirs(state)" in src
    assert "own_dirs=own, project_files=" in src, \
        "the run computes protection from the config alone"


def test_a_blind_directory_that_is_also_the_source_directory_protects_only_the_tests(tmp_path):
    """Angular keeps its specs beside the code, so Library's blind tests went into
    `web/src/app` -- and protecting that directory whole protected the feature.
    A repair to `app.html` was refused as "a test that judges it" in one round,
    and the next round declined to try. The project's own files there -- tracked
    at the base commit, or written by the build -- are the feature's; the blind
    test, the probe, and a file a repair tries to add there are not."""
    import subprocess

    repo = tmp_path / "repo"
    (repo / "web/src/app").mkdir(parents=True)
    (repo / "web/src/app/app.html").write_text("<p>library</p>\n")
    (repo / "web/src/app/app.ts").write_text("export class App {}\n")
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"]):
        subprocess.run(cmd, cwd=repo, check=True)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                          text=True).stdout.strip()
    (repo / "web/src/app/star-rating.ts").write_text("export const stars = 5;\n")
    (repo / "web/src/app/rating.acceptance.spec.ts").write_text("it('x', () => {});\n")
    (repo / "web/src/app/rating.probe.spec.ts").write_text("it('y', () => {});\n")

    config = load_config()
    own = ["web/src/app"]
    mine = pipeline.project_files_in(repo, base, own, ["web/src/app/star-rating.ts"])
    assert mine == {"web/src/app/app.html", "web/src/app/app.ts", "web/src/app/star-rating.ts"}

    protected = pipeline.protected_paths(config, repo, extra=["web/src/app/rating.acceptance.spec.ts"],
                                         own_dirs=own, project_files=mine)
    assert "web/src/app/rating.acceptance.spec.ts" in protected
    assert "web/src/app/rating.probe.spec.ts" in protected, "a probe in there is still the lane's"
    for source in mine:
        assert source not in protected
        assert not pipeline.is_protected(source, protected, config, own, mine), \
            f"{source} is the feature, and a repair must be able to change it"
    assert pipeline.is_protected("web/src/app/new-helper.ts", protected, config, own, mine), \
        "a file a repair tries to add in a blind directory is still refused"

    # A directory holding nothing but blind tests has no project files, and
    # is protected whole, exactly as before.
    only_tests = pipeline.project_files_in(repo, base, ["web/src/app/_feature"], [])
    assert only_tests == set()


def test_only_this_features_blind_tests_are_protected_from_repair():
    """An earlier feature's acceptance tests are ordinary tests in the project.

    Protection used to cover every placement directory whole, so a feature
    accepted last month had its tests frozen forever and excluded from every
    check -- run by one special end-of-build step and linted by nothing. They
    are now code like any other: linted, run, and the workers' to fix when a
    change breaks them. What stays out of a repairer's reach is the suite
    judging this feature, in this feature's own folder."""
    from types import SimpleNamespace

    from factory.schemas import BlindPlacement, PlacementResult

    project = SimpleNamespace(state=SimpleNamespace(
        blind_placements=[BlindPlacement(directory="api/tests/acceptance",
                                         filename="test_x.py",
                                         canary_passes="", canary_fails="")],
        placement_probe=[PlacementResult(directory="api/tests/acceptance",
                                         subdirs_ok=True)]))
    fake = SimpleNamespace(project=project)
    state = SimpleNamespace(feature_id="card-labels-9840f6")
    own = pipeline.Factory._own_blind_dirs(fake, state)
    assert own == ["api/tests/acceptance/card_labels_9840f6"]

    config = Config()
    assert pipeline.is_protected(
        "api/tests/acceptance/card_labels_9840f6/test_add.py", [], config, own)
    assert not pipeline.is_protected(
        "api/tests/acceptance/user_support_58e79f/test_reply.py", [], config, own), \
        "an earlier feature's acceptance test was frozen against the workers"

    # A runner that cannot nest a folder per feature has only the one folder,
    # and it is protected whole rather than not at all.
    project.state.placement_probe[0].subdirs_ok = False
    assert pipeline.Factory._own_blind_dirs(fake, state) == ["api/tests/acceptance"]


def test_the_collect_probe_does_not_run_when_the_files_run_one_at_a_time():
    """A global probe cannot describe a suite that lives in two places.

    The probe exists to separate "the oracle's files will not load" from "the
    feature is broken", which matters when one exit code covers every criterion.
    Per-file runs already do that and do it better: a file that will not import
    fails its own command and costs only the criteria it covered.

    Left in, it did the exact damage it exists to prevent. Blind tests are now
    written to the placements a project proved -- `backend/tests_blind/<feature>`
    and `frontend/src/__tests__/<feature>` on a real repository -- while this
    probe still looked in `rework.oracle_dir`. It found nothing, reported the
    whole lane unloadable, and twenty-five criteria came back unverified behind
    five green gates. Nothing was wrong with the feature or the tests.
    """
    src = inspect.getsource(pipeline.Factory._run_blind_suite)
    assert 'collect = ("" if rules else' in src, (
        "the collect probe still runs when the files are run one at a time, so a single global "
        "command decides the fate of a suite spread across two runners")
    # And the rules have to be resolved before that decision, not after.
    assert src.index("rules = list(self.project.state.test_file_commands)") < src.index("collect ="), \
        "the collect decision is made before it knows whether per-file rules exist"


def test_a_blind_suite_with_no_runner_is_a_failed_gate_not_a_silent_pass():
    """The whole point of the separation. If nothing ran the blind tests, no
    criterion is verified -- and that must not be inferable from the project's
    own gates being green."""
    src = inspect.getsource(pipeline.Factory._run_blind_suite)
    assert '"blind-tests"' in src
    assert "passed=False" in src, "a suite that could not run comes back green"
    assert "Returning `None` is not an option when the suite could not run" in src

    assess = inspect.getsource(pipeline.Factory._assess)
    assert "gates.results.append(blind)" in assess, \
        "the blind suite runs but its result never reaches the report"


def test_the_oracle_still_sees_only_the_spec():
    """The containment must not have become a hole in INV-1.

    The other way to fix the fixture problem was to show the oracle how this
    project writes its tests. That is a smaller change and a much worse one:
    the blindness is the single property that makes a blind test worth anything.
    """
    source = code_without_prose(pipeline.Factory._verify_lane)
    for term in ("repo_digest", "digest", "read_text", "glob", "gates", "confine_to"):
        assert term not in source, f"_verify_lane reaches {term!r}"


def test_a_file_the_plan_assigned_and_somebody_wrote_is_not_unasked_for():
    """The case from the run: the file was written, by a later actor, whose
    decision carried no criterion id. The plan knew what it was for."""
    from factory.schemas import Plan, WorkUnit

    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-9", statement="the detail page shows slots")])
    plan = Plan(summary="s", units=[
        WorkUnit(id="U3", title="frontend", objective="o",
                 criterion_ids=["AC-9"], files_expected=["frontend/TrialDetail.tsx"])])
    # U3 produced nothing; the file arrived in a repair round, with no criterion
    # named on its decision.
    workers = [_unit_output("U3", "frontend/other.ts"),
               _unit_output("R2", "frontend/TrialDetail.tsx")]
    written = [FileWrite(path="frontend/TrialDetail.tsx", contents="x\n"),
               FileWrite(path="frontend/other.ts", contents="x\n")]

    trace = compute_trace(spec, workers, None, plan=plan, written=written)
    assert trace[0].implementing_files == ["frontend/TrialDetail.tsx"]

    unclaimed = pipeline.compute_unclaimed(workers, written, plan=plan)
    assert "frontend/TrialDetail.tsx" not in [u.path for u in unclaimed], \
        "the file that implements AC-9 is listed as answering to no criterion"
    assert "frontend/other.ts" in [u.path for u in unclaimed], \
        "the check no longer detects a file nothing asked for"


def test_the_plan_alone_never_traces_a_criterion():
    """INV-3. Intent is not evidence: if nothing was written, it is an orphan."""
    from factory.schemas import Plan, WorkUnit

    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="a")])
    plan = Plan(summary="s", units=[
        WorkUnit(id="U1", title="u", objective="o",
                 criterion_ids=["AC-1"], files_expected=["a/b.py"])])

    trace = compute_trace(spec, [], None, plan=plan, written=[])
    assert trace[0].status == "orphan_requirement"

    # And written, but a different file than the one planned.
    trace = compute_trace(spec, [], None, plan=plan,
                          written=[FileWrite(path="a/other.py", contents="x\n")])
    assert trace[0].status == "orphan_requirement"


def test_a_criterion_the_worker_disclaimed_is_never_traced_by_the_plan():
    """A unit owning three criteria and writing one file has not built all three.

    The author is the one who knows. When a worker puts an AC id in
    `not_implemented`, that outranks anything the plan intended -- otherwise a
    unit that wrote `spin.py` and said in as many words that the stop path was
    missing would have that criterion traced to `spin.py`.
    """
    from factory.schemas import Plan, WorkUnit

    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="it spins"),
        AcceptanceCriterion(id="AC-2", statement="it stops")])
    plan = Plan(summary="s", units=[
        WorkUnit(id="U1", title="spin", objective="o",
                 criterion_ids=["AC-1", "AC-2"], files_expected=["widget/spin.py"])])
    workers = [_unit_output("U1", "widget/spin.py", cids=["AC-1"],
                       not_implemented=["AC-2, the stop path"])]
    written = [FileWrite(path="widget/spin.py", contents="x\n")]

    trace = {r.criterion_id: r.status for r in
             compute_trace(spec, workers, None, plan=plan, written=written)}
    assert trace["AC-2"] == "orphan_requirement", \
        "a criterion its own author said was not built came back traced"

    assert pipeline.disclaimed_criteria(workers) == {"AC-2"}
    # Word boundary: AC-1 must not also claim AC-12.
    assert pipeline.disclaimed_criteria(
        [_unit_output("U1", "a.py", not_implemented=["AC-12 was skipped"])]) == {"AC-12"}


def test_the_oracle_is_sealed_in_and_given_nothing_running():
    """INV-1, in the one place this change could have broken it.

    Every other authoring agent gets a running instance of the project. The
    oracle must not: a blind test author that can watch the system behave stops
    writing the specification and starts writing what it observed, which is
    the failure the whole verify lane exists to prevent.

    It used to get no environment at all, and that was worse than it looked:
    no environment meant this machine's shell, with every server running here
    in reach and every other feature's checkout -- this one's implementation
    included -- a path away. So it gets a sealed container with the toolchain
    in it and nothing running: `serve=False`, both times it writes.
    """
    for method in (pipeline.Factory._author_blind_suite, pipeline.Factory._revise_blind_suite):
        src = inspect.getsource(method)
        assert "environment=self.unit_env(" in src and "serve=False" in src, (
            f"{method.__name__}: the oracle is not in a sealed container of its own, "
            "or it is in one with the system running")
    unit = inspect.getsource(unitenv.unit_environment)
    assert "services=list(env_spec.services) if prepare and serve else ()" in unit
    assert "prepare=list(env_spec.test_prepare) if prepare and serve else ()" in unit
    breaker = inspect.getsource(pipeline.Factory._author_probes)
    assert "environment=self.unit_env(" in breaker, (
        "the breaker writes probes it still cannot run")


def test_a_units_environment_is_named_uniquely_per_unit():
    """The compose project name *is* the isolation boundary.

    Same name, same containers, same volumes, same database -- so a label
    reused across two concurrent units is a shared database wearing a private
    one's costume, and the seed-order failure in site-screening-61020a is what
    that looks like from the outside.
    """
    src = class_source(pipeline.Factory)
    for label in ('f"{state.feature_id}-{unit.id}"',
                  'f"{state.feature_id}-integration"',
                  'f"{state.feature_id}-r{round_index}-{unit.id}"',
                  'f"{state.feature_id}-breaker-{round_index}"'):
        assert label in src, f"no per-unit environment label {label}"


def test_a_unit_built_against_another_units_code_is_reported():
    """The prompt says do not cut this way. This measures whether it did.

    U-2 in site-screening-61020a required three of U-1's function signatures,
    could not run a single test it wrote, and shipped assumptions instead.
    Nothing noticed until the packet, where it arrived looking like a worker
    that wrote weak tests.
    """
    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="the endpoint returns capacity")])
    plan = _plan(
        dict(id="U-1", title="data", objective="o",
             provides=["`app.services.screening_capacity.capacity_block(p, used)` -> dict"]),
        dict(id="U-2", title="api", objective="o",
             requires=["`app.services.screening_capacity.capacity_block(p, used)` -> dict"]),
    )
    edges = pipeline.unit_dependencies(plan, spec)
    assert edges == [("U-2", "U-1", "app.services.screening_capacity.capacity_block")]
    finding = pipeline.check_unit_independence(plan, spec)
    assert finding and finding[0].severity == "major"
    assert "a connection the spec never defined" in finding[0].title


def test_a_seam_the_spec_pins_is_not_a_dependency_between_units():
    """Two units reading one frozen document is the legitimate cut.

    A frontend unit consuming a response key the acceptance criteria spell out
    is not guessing at another unit -- it is reading the same spec. Flagging it
    would make this check noise, and a noisy check gets ignored exactly when it
    is right.
    """
    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(
            id="AC-3",
            statement="`GET /api/trials/{id}` returns `trial.screening_capacity`")])
    plan = _plan(
        dict(id="U-1", title="api", objective="o",
             provides=["`trial.screening_capacity` on the detail response"]),
        dict(id="U-2", title="ui", objective="o",
             requires=["`trial.screening_capacity` on the detail response"]),
    )
    assert pipeline.unit_dependencies(plan, spec) == []
    assert pipeline.check_unit_independence(plan, spec) == []


def test_shared_library_types_are_not_a_dependency_between_units():
    """`uuid.UUID` in both lists says both handle ids, not that one waits on the
    other. Filtered against facts -- the interpreter's own module list and the
    packages measured at gate 0 -- rather than a maintained table of names."""
    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="it works")])
    plan = _plan(
        dict(id="U-1", title="a", objective="o",
             provides=["a route taking `uuid.UUID`", "`pydantic.BaseModel` subclass"]),
        dict(id="U-2", title="b", objective="o",
             requires=["a route taking `uuid.UUID`", "`pydantic.BaseModel` subclass"]),
    )
    assert pipeline.unit_dependencies(plan, spec, ["pydantic", "httpx"]) == []


def test_an_independent_cut_reports_nothing():
    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="it works")])
    plan = _plan(
        dict(id="U-1", title="a", objective="o", provides=["`app.a.one()`"]),
        dict(id="U-2", title="b", objective="o", provides=["`app.b.two()`"]),
    )
    assert pipeline.check_unit_independence(plan, spec) == []


def test_a_harness_that_refuses_is_a_blocker_about_the_tool_not_the_code():
    """The distinction the packet could not make.

    `check_unit_delivery` softens to `minor` for a unit that disclosed its gap,
    which is right for a unit that made a judgement. A harness that produced
    nothing made no judgement, and reading its silence as "the feature was not
    implemented" is how a tooling failure got written up as a specification
    failure.
    """
    from factory.executors import FELL_BACK, NO_EDITS
    from factory.schemas import SelfDisclosure

    refused = WorkerOutput(
        unit_id="U1", summary="The coding harness edited no file, on 2 attempt(s) (exit 0, exit 0).",
        files=[], disclosure=SelfDisclosure(not_implemented=["the whole of U1"], flags=[NO_EDITS]))
    fell_back = WorkerOutput(
        unit_id="U3", summary="Built by the `direct` executor after the harness edited no file.",
        files=[FileWrite(path="a.tsx", contents="x\n")],
        disclosure=SelfDisclosure(flags=[NO_EDITS, FELL_BACK]))

    found = pipeline.check_harness_delivery([refused, fell_back])
    by_sev = {f.severity: f for f in found}
    assert "blocker" in by_sev, "a harness that built nothing is not raised as a blocker"
    assert "U1" in by_sev["blocker"].title and "U3" not in by_sev["blocker"].title, \
        "a unit the fallback rescued is counted among those that produced nothing"
    assert by_sev["blocker"].category == "harness"
    assert "failure of the tool" in by_sev["blocker"].detail

    assert "minor" in by_sev, "a unit built by the fallback is invisible in the packet"
    assert "U3" in by_sev["minor"].title

    assert pipeline.check_harness_delivery([fell_back]) [0].severity == "minor"
    assert pipeline.check_harness_delivery([]) == []


def test_the_harness_check_runs_on_every_build():
    src = inspect.getsource(pipeline.Factory._converge)
    assert "check_harness_delivery(workers)" in src, \
        "a harness that built nothing raises no computed finding"


def test_the_unit_that_died_asking_now_gets_the_file_it_asked_for(tmp_path):
    from factory.schemas import WorkUnit

    root = _migration_repo(tmp_path)
    executor = _harness_for(tmp_path)
    unit = WorkUnit(
        id="U1", title="Schema, Alembic migration, and model columns", objective="o",
        files_expected=["backend/alembic/versions/e5f6_slots.py",
                        "backend/app/models/trial.py", "backend/app/models/patient.py"])

    writable, reference = executor._context_files(unit, root)

    assert writable == unit.files_expected, \
        "a file the unit must create is not offered to the harness as writable"
    assert "backend/alembic/versions/d4e5f6_task_state.py" in reference, \
        "the migration chain is still invisible; U1 dies the same way"
    # And the neighbours of every directory it writes into, which is the point:
    # no model can name the current head in advance, since that is the question.
    assert "backend/app/models/trial.py" not in reference, \
        "a file the unit owns is being passed as read-only as well as writable"


def test_the_architects_declared_reading_list_is_honoured_and_comes_first(tmp_path):
    """The computed half is a safety net. The architect is the one that knows."""
    from factory.schemas import WorkUnit

    root = _migration_repo(tmp_path)
    (root / "docs").mkdir()
    (root / "docs" / "conventions.md").write_text("write it like this\n")
    executor = _harness_for(tmp_path, max_context_files=2)

    unit = WorkUnit(id="U1", title="t", objective="o",
                    files_expected=["backend/alembic/versions/e5f6_slots.py"],
                    read_files=["docs/conventions.md"])
    _, reference = executor._context_files(unit, root)

    assert reference[0] == "docs/conventions.md", \
        "under the cap, what a model deliberately chose is dropped before what was computed"
    assert len(reference) == 2, "the ceiling is not enforced"


def test_a_directory_too_large_to_pass_is_skipped_whole_not_sampled(tmp_path):
    """An arbitrary twelve files out of two hundred is worse than none.

    It looks like context. A worker that reads it and finds nothing relevant
    concludes the thing it needs does not exist, which is a worse failure than
    knowing it was not given the directory.
    """
    from factory.schemas import WorkUnit

    big = tmp_path / "src" / "components"
    big.mkdir(parents=True)
    for i in range(40):
        (big / f"Widget{i}.tsx").write_text("export const x = 1\n")
    executor = _harness_for(tmp_path, max_siblings_per_dir=12)

    unit = WorkUnit(id="U1", title="t", objective="o",
                    files_expected=["src/components/New.tsx"])
    _, reference = executor._context_files(unit, tmp_path)
    assert reference == [], "a 40-file directory was sampled instead of skipped"


def test_a_nonexistent_or_escaping_reference_is_dropped_not_passed(tmp_path):
    """`read_files` is model-written, like every other path in this system."""
    from factory.schemas import WorkUnit

    root = _migration_repo(tmp_path)
    executor = _harness_for(tmp_path)
    unit = WorkUnit(id="U1", title="t", objective="o",
                    files_expected=["backend/app/models/trial.py"],
                    read_files=["docs/does-not-exist.md", "../../etc/passwd",
                                "backend/app/models"])
    _, reference = executor._context_files(unit, root)
    assert "docs/does-not-exist.md" not in reference
    assert not any("passwd" in r for r in reference), "a path escaped the checkout"
    assert "backend/app/models" not in reference, "a directory was passed as a file"


def test_the_context_flags_reach_the_harness_invocation(tmp_path, monkeypatch):
    """Asserted on the argv the harness is actually launched with."""
    import subprocess

    from factory.schemas import Spec, WorkUnit

    repo = _harness_repo(tmp_path)
    (repo / "widget").mkdir()
    (repo / "widget" / "spin.py").write_text("def spin(): ...\n")
    (repo / "widget" / "neighbour.py").write_text("def near(): ...\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "files"], cwd=repo, check=True)

    seen = tmp_path / "argv"
    # Flags set here rather than taken from factory.yaml: this asserts the
    # mechanism, which must keep working whichever harness is configured. The
    # shipped config now runs OpenHands, which needs neither.
    executor = _harness_for(tmp_path, fallback_to_direct=False,
                            file_flag="--file", read_flag="--read")
    _global_harness(executor.config, [
        "sh", "-c", f'printf "%s\\n" "$@" > {seen}', "--"])

    async def refuse(*a, **kw):
        raise AssertionError("no model call expected")
    monkeypatch.setattr(executor.llm, "ask", refuse)

    asyncio.run(executor.run(
        WorkUnit(id="U1", title="t", objective="o", files_expected=["widget/spin.py"]),
        Spec(title="t", intent="i", summary="s"), "digest", "SYS", repo, role="worker"))

    argv = seen.read_text().split("\n")
    assert "--file" in argv and "widget/spin.py" in argv, \
        "the harness was launched without being told which file it owns"
    assert "--read" in argv and "widget/neighbour.py" in argv, \
        "the harness was launched with no reference context at all"


def test_the_architect_is_told_to_fill_the_reading_list():
    from factory.schemas import WorkUnit

    assert "read_files" in WorkUnit.model_fields
    described = WorkUnit.model_fields["read_files"].description
    assert "must not change" in described and "cannot ask for" in described

    text = (ROOT / "factory" / "roles" / "architect.md").read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "`read_files` is the other half" in flat
    assert "cannot proceed and cannot say so" in flat, \
        "the architect is not told what a missing reading list actually costs"
    assert "do not name files that another unit is writing right now" in flat, \
        "the architect may hand a worker a file that does not exist yet"


def test_a_relocated_blind_suite_is_run_where_it_actually_is():
    """The oracle names the directory it chose; `confine_to` moves the files.

    Relocating without rewriting the command left `pytest tests_acceptance/`
    pointing at a path that no longer existed. Exit 4, zero tests collected, and
    every criterion in the packet came back unverified -- because two lines of
    this module disagreed about where a directory was.
    """
    moved = [("tests_acceptance/conftest.py", "tests/oracle/conftest.py"),
             ("tests_acceptance/test_schema.py", "tests/oracle/test_schema.py")]

    assert pipeline.retarget_command("python -m pytest tests_acceptance/ -v", moved) == \
        "python -m pytest tests/oracle/ -v"
    # A command naming a file rather than the directory.
    assert "tests/oracle/test_schema.py" in pipeline.retarget_command(
        "pytest tests_acceptance/test_schema.py", moved)
    # Nothing to do is not an error.
    assert pipeline.retarget_command("", moved) == ""
    assert pipeline.retarget_command("pytest", []) == "pytest"

    src = inspect.getsource(pipeline.Factory._build)
    assert "retarget_command(oracle.command, moved)" in src, \
        "the files are relocated and the command that runs them is not"
    assert "not self.project.state.oracle_command" in src, \
        "a command a human pinned on the project is being rewritten under them"


def test_the_plan_fills_gaps_in_the_matrix_and_never_widens_a_row():
    """A unit owning seven files and eight criteria must not claim all for all.

    The first version of this did exactly that: ten of twelve criteria traced to
    the same seven files, and AC-1 -- "the table gains four columns" -- listed
    `queue.py` and `patients.py` among its implementation. The packet's promise
    is that opening a criterion shows only the code serving it, so a whole-unit
    answer is worse than the false orphan it replaced.
    """
    from factory.schemas import Plan, WorkUnit

    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="columns"),
        AcceptanceCriterion(id="AC-2", statement="a foreign key")])
    plan = Plan(summary="s", units=[
        WorkUnit(id="U1", title="backend", objective="o",
                 criterion_ids=["AC-1", "AC-2"],
                 files_expected=["m/migration.py", "m/trial.py", "r/queue.py"])])
    # The worker attributed its migration to AC-1 and named nothing for AC-2.
    workers = [WorkerOutput(
        unit_id="U1", summary="s",
        files=[FileWrite(path=p, contents="x\n")
               for p in ("m/migration.py", "m/trial.py", "r/queue.py")],
        decisions=[Decision(id="D-1", title="t", rationale="r",
                            criterion_ids=["AC-1"], files=["m/migration.py"])])]
    written = [FileWrite(path=p, contents="x\n")
               for p in ("m/migration.py", "m/trial.py", "r/queue.py")]

    rows = {r.criterion_id: r for r in
            compute_trace(spec, workers, None, plan=plan, written=written)}

    assert rows["AC-1"].implementing_files == ["m/migration.py"], \
        "a criterion the worker attributed precisely was widened to the whole unit"
    assert set(rows["AC-2"].implementing_files) == {"m/migration.py", "m/trial.py", "r/queue.py"}, \
        "the criterion with no attribution at all was left an orphan"
    assert rows["AC-2"].status != "orphan_requirement"


# -- what a check asks, and what would quiet it --------------------------------


def test_every_check_says_which_of_three_questions_it_asks():
    """Structure, quality or tests -- or nothing yet, for a check recorded
    before there were families. Never a fourth, and never one guessed from a
    name: the console says "family not set" until a reading proposes one."""
    import typing

    from factory.schemas import Gate

    field = Gate.model_fields["family"]
    assert set(typing.get_args(field.annotation)) == {"", "structure", "quality", "tests"}
    assert Gate(name="lint", command="true").family == "", \
        "a check with no family was given one by default"
    for name in ("config_files", "suppressions"):
        assert Gate.model_fields[name].description, \
            f"{name} reaches the surveyor with no instruction about what goes in it"


def test_a_project_approved_before_families_is_still_the_project_that_was_approved(tmp_path):
    """Adding a field to every check must not send every approved project back
    to gate 0. While the new fields are empty the fingerprint is the one the
    approval recorded before they existed; once one says something, it counts."""
    import hashlib

    from factory.schemas import (DESCRIPTIVE_ENV_FIELDS, DESCRIPTIVE_GATE_FIELDS,
                                 GATE_FIELDS_SINCE_APPROVAL)

    registry, project = _approved_project(tmp_path)
    state = project.state
    before_families = {
        "repo": state.repo,
        "base_ref": state.base_ref,
        "gates": [{k: v for k, v in g.model_dump(mode="json").items()
                   if k not in GATE_FIELDS_SINCE_APPROVAL + DESCRIPTIVE_GATE_FIELDS}
                  for g in state.gates],
        "environment": (state.environment.model_dump(mode="json", exclude=set(DESCRIPTIVE_ENV_FIELDS))
                        if state.environment else None),
        "blind_placements": [b.model_dump(mode="json") for b in state.blind_placements],
    }
    blob = json.dumps(before_families, sort_keys=True, ensure_ascii=False)
    old = "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]
    assert registry.approval_fingerprint(project) == old, \
        "an empty new field changed the fingerprint, un-approving every existing project"

    project.state.gates[0].family = "tests"
    assert registry.approval_fingerprint(project) != old, \
        "setting a check's family is not part of what a person approves"


def test_a_check_s_own_settings_file_is_protected_and_a_shared_one_is_not():
    """A repair cannot write the file that decides what a check reports. A
    section of a shared manifest is watched instead of refused, because the
    same file lists dependencies and a repair may need one."""
    from factory.schemas import Gate

    checks = [
        Gate(name="lint", command="lint", config_files=["ruff.toml", "pyproject.toml#tool.ruff"]),
        Gate(name="js", command="eslint", config_files=["package.json#eslintConfig"]),
    ]
    assert pipeline.check_settings_files(checks) == ["ruff.toml"]

    converge = inspect.getsource(pipeline.Factory._converge)
    assert "check_settings_files(self.project.state.gates)" in converge, \
        "a check's settings files never reach the list a repair is refused by"
    assert "check_verification_surface(\n                workers, integration, surface)" in converge, \
        "a check's settings would be reported twice, once as test configuration"


def test_a_dependency_added_beside_a_check_s_settings_is_not_reported(tmp_path):
    """The reason a shared file is named by section. A worker may add a
    package; that is not a change to the linter."""
    base = {"package.json": json.dumps(
        {"dependencies": {"a": "1"}, "eslintConfig": {"rules": {"no-x": "error"}}}, indent=2)}
    added = {"package.json": json.dumps(
        {"dependencies": {"a": "1", "b": "2"}, "eslintConfig": {"rules": {"no-x": "error"}}})}
    sandbox = _branch(tmp_path, base, added)
    assert pipeline.check_settings_changed(sandbox, _checks_with_settings()) == [], \
        "a new dependency, or a file reformatted around the same settings, was reported"


def test_settings_and_suppressions_are_asked_again_every_round():
    """A repair that puts the settings back clears the blocker; one that adds a
    suppression raises it. Restated, so the packet says what is true now."""
    converge = inspect.getsource(pipeline.Factory._converge)
    assert 'source="check_settings"' in converge
    assert 'source="suppressions"' in converge


def test_a_suggestion_says_which_family_it_would_fill():
    """The kind a reading already gives sorts it, so a reading that names only
    the kind has still said which family it meant."""
    from factory.schemas import Recommendation

    def rec(kind, **kw):
        return Recommendation(title="t", kind=kind, why="w", evidence="e", **kw)

    assert rec("lint").family == "structure"
    assert rec("types").family == "structure"
    assert rec("security").family == "quality"
    assert rec("tests").family == "tests"
    assert rec("ci").family == "", "a suggestion about running the project was put in a family"
    assert rec("other", family="quality").family == "quality", "an explicit family was overridden"


def test_a_red_check_with_a_reading_is_held_at_it(tmp_path):
    """The bound is the number the check printed, set by a person pressing for
    it. A count gets a ceiling; a score under its floor gets the floor lowered
    to today's. The checks then run again: the bound is proved, not assumed."""
    from factory.schemas import Gate, GateResult

    gates = [Gate(name="lint", command="lint", parse_metric=r"(\d+) errors", family="structure"),
             Gate(name="cov", command="cov", parse_metric=r"(\d+)%", threshold=80, family="quality")]
    results = [GateResult(name="lint", exit_code=1, metric=129),
               GateResult(name="cov", exit_code=0, metric=72, threshold=80)]
    registry, project = _project_with_suggestions(tmp_path, results, gates)

    project = registry.ratchet_check(project, "lint")
    assert next(g for g in project.state.gates if g.name == "lint").threshold_max == 129
    assert project.state.baseline is None, "the bound was not re-proved"

    project.state.baseline = __import__("factory.schemas", fromlist=["x"]).GateReport(results=results)
    project = registry.ratchet_check(project, "cov")
    assert next(g for g in project.state.gates if g.name == "cov").threshold == 72
    assert any(r.get("kind") == "check_ratcheted" for r in project.store.records())


def test_a_check_with_no_reading_cannot_be_held(tmp_path):
    import pytest

    from factory.projects import ProjectError
    from factory.schemas import Gate, GateResult

    registry, project = _project_with_suggestions(
        tmp_path, [GateResult(name="lint", exit_code=1)], [Gate(name="lint", command="lint")])
    with pytest.raises(ProjectError, match="no reading"):
        registry.ratchet_check(project, "lint")


def test_a_check_that_writes_sarif_is_read_counted_and_leaves_nothing_behind(tmp_path):
    """The count is the number of findings, so a tool that prints nothing to a
    pipe can still be held at today's count -- and the report never stays in
    the checkout for a commit to pick up."""
    import asyncio

    from factory.schemas import Gate

    body = _sarif(("app.py", 3, "C901"), ("app.py", 9, "C901"))
    gate = Gate(name="complexity", command=f"printf '%s' '{body}' > {{report}}",
                report_format="sarif", threshold_max=1)
    result = asyncio.run(gates.run_gate(gate, tmp_path))

    assert result.report == "read" and len(result.located) == 2
    assert result.metric == 2.0 and result.passed is False, "the ceiling was not held against the count"
    assert not list(tmp_path.glob(".factory-report-*")), "the report was left in the checkout"


def test_only_lines_the_feature_changed_are_counted(tmp_path):
    from factory.schemas import Gate, GateReport, GateResult, Located

    sandbox = _branch(
        tmp_path,
        {"app.py": "a = 1\nb = 2\n"},
        {"app.py": "a = 1\nb = 2\nc = 3\n", "new.py": "x = 1\ny = 2\n"},
    )
    lines = pipeline.changed_lines(sandbox)
    assert lines == {"app.py": {3}, "new.py": {1, 2}}

    check = Gate(name="sast", command="scan {report}", report_format="sarif", patch_max=0)
    found = [Located(path="app.py", start_line=1, rule="old"),
             Located(path="app.py", start_line=3, rule="new"),
             Located(path="new.py", start_line=2, rule="new")]
    report = GateReport(results=[GateResult(name="sast", report="read", located=found)])
    findings = pipeline.check_new_quality_findings(sandbox, [check], report)

    assert [f.severity for f in findings] == ["blocker"]
    assert "2 new sast findings" in findings[0].title
    assert "app.py:1" not in findings[0].evidence, "a finding the repository already had was counted"

    check.patch_max = 2
    assert pipeline.check_new_quality_findings(sandbox, [check], report) == []

    unread = GateReport(results=[GateResult(name="sast", report="missing")])
    findings = pipeline.check_new_quality_findings(sandbox, [check], unread)
    assert [f.severity for f in findings] == ["major"], "an unread report passed as nothing found"


def test_a_report_and_its_placeholder_come_together():
    from factory.projects import ProjectRegistry
    from factory.schemas import Gate

    problems = ProjectRegistry.gate_problems([
        Gate(name="a", command="scan", report_format="sarif"),
        Gate(name="b", command="scan -o {report}"),
        Gate(name="c", command="scan", patch_max=0),
        Gate(name="ok", command="scan -o {report}", report_format="sarif", patch_max=0),
    ])
    named = {p.split("'")[1] for p in problems}
    assert named == {"a", "b", "c"}


def test_holding_new_code_is_a_person_s_limit_and_part_of_what_they_approve(tmp_path):
    import pytest

    from factory.projects import ProjectError
    from factory.schemas import Gate

    gates_ = [Gate(name="sast", command="scan -o {report}", report_format="sarif"),
              Gate(name="lint", command="lint")]
    registry, project = _project_with_suggestions(tmp_path, gates=gates_)
    before = registry.approval_fingerprint(project)

    project = registry.hold_new_code(project, "sast", 0)
    assert next(g for g in project.state.gates if g.name == "sast").patch_max == 0
    assert registry.approval_fingerprint(project) != before, \
        "a limit of zero was left out of what a person approves"
    with pytest.raises(ProjectError, match="no report"):
        registry.hold_new_code(project, "lint", 0)


def test_a_coverage_check_reads_its_total_and_holds_its_floor(tmp_path):
    import asyncio

    from factory.schemas import Gate

    body = json.dumps({"files": {"a.py": {"executed_lines": [1], "missing_lines": [2, 3]}}})
    gate = Gate(name="coverage", command=f"printf '%s' '{body}' > {{report}}",
                report_format="coverage-json", threshold=50)
    result = asyncio.run(gates.run_gate(gate, tmp_path))
    assert result.report == "read" and result.metric == 33.33
    assert result.passed is False, "a total under its floor passed"
    assert "coverage" not in result.model_dump(), "per-line coverage would fill the ledger"


def test_a_report_the_run_did_not_rewrite_is_not_this_run_s_answer(tmp_path):
    import asyncio

    from factory.schemas import Gate

    (tmp_path / "coverage").mkdir()
    (tmp_path / "coverage" / "lcov.info").write_text("SF:a.js\nDA:1,1\nend_of_record\n")
    gate = Gate(name="coverage", command="true", report_format="lcov",
                report_path="coverage/lcov.info")
    result = asyncio.run(gates.run_gate(gate, tmp_path))
    assert result.report == "missing", "last run's report was read as this run's"


def test_only_a_feature_s_own_lines_are_held_to_the_coverage_floor(tmp_path):
    from factory.schemas import Gate, GateReport, GateResult

    sandbox = _branch(
        tmp_path,
        {"app.py": "def a():\n    return 1\n"},
        {"app.py": "def a():\n    return 1\n\ndef b():\n    return 2\n",
         "fresh.py": "def c():\n    return 3\n",
         "made_pb2.py": "x = 1\n",
         "notes.md": "words\n"},
    )
    check = Gate(name="coverage", command="cov {report}", report_format="coverage-json",
                 patch_min=80)
    measured = {"app.py": [[1, 2, 4, 5], [1, 2, 4]], "made_pb2.py": [[1], []]}
    report = GateReport(results=[GateResult(name="coverage", report="read", coverage=measured)])
    findings = pipeline.check_patch_coverage(sandbox, [check], report)

    assert [f.severity for f in findings] == ["blocker"]
    # app.py: 4 ran, 5 did not; fresh.py: no test loaded it, both lines count.
    assert "Tests ran 25% of the lines" in findings[0].title
    assert "app.py: lines 5" in findings[0].evidence
    assert "fresh.py: lines 1-2 (no test loaded this file)" in findings[0].evidence, \
        "a module no test loaded was skipped instead of counted as not run"
    assert "made_pb2.py" not in findings[0].files, "generated code was held to the floor"
    assert "notes.md" not in findings[0].evidence

    check.patch_min = 20
    assert pipeline.check_patch_coverage(sandbox, [check], report) == []
    unread = GateReport(results=[GateResult(name="coverage", report="unreadable")])
    assert [f.severity for f in pipeline.check_patch_coverage(sandbox, [check], unread)] == ["major"]


def test_a_coverage_floor_needs_a_coverage_report_and_a_fixed_path_will_do():
    from factory.projects import ProjectRegistry
    from factory.schemas import Gate

    problems = ProjectRegistry.gate_problems([
        Gate(name="a", command="scan -o {report}", report_format="sarif", patch_min=80),
        Gate(name="b", command="cov {report}", report_format="lcov", patch_max=0),
        Gate(name="ok", command="npx vitest run --coverage", report_format="lcov",
             report_path="coverage/lcov.info", patch_min=80),
    ])
    assert {p.split("'")[1] for p in problems} == {"a", "b"}


def test_a_build_records_its_dependency_changes_and_never_names_a_private_package(tmp_path):
    import asyncio

    from factory.pipeline import Factory, FindingLedger

    sandbox = _branch(
        tmp_path,
        {"requirements.txt": "click==8.1.7\n"},
        {"requirements.txt": "click==8.1.7\nhttpx==0.27.0\n",
         "web/package-lock.json": json.dumps({"lockfileVersion": 3, "packages": {
             "": {"dependencies": {"corp-ui": "1"}},
             "node_modules/corp-ui": {"version": "1.0.0",
                                      "resolved": "https://npm.corp.example/corp-ui-1.0.0.tgz"}}})},
    )
    factory = Factory.__new__(Factory)
    from types import SimpleNamespace

    from factory.schemas import DependencyPolicy
    factory.project = SimpleNamespace(state=SimpleNamespace(dependency_policy=DependencyPolicy()))
    factory.dependency_lookup = _StubLookup({})
    factory._dependency_facts = {}
    store = type("S", (), {"records": [], "append": lambda self, kind, payload, **kw:
                           self.records.append((kind, payload))})()
    ledger = FindingLedger()
    asyncio.run(factory._restate_dependencies(store, ledger, sandbox, 0, {}))

    assert [k for k, _ in store.records] == ["dependencies"]
    names = {c["name"]: c for c in store.records[0][1]["changes"]}
    assert set(names) == {"httpx", "corp-ui"}
    assert names["corp-ui"]["private"] and not names["corp-ui"]["checked"]
    assert [k[1] for k in factory.dependency_lookup.asked] == ["httpx"], \
        "a private package's name was sent to a public service"


def test_only_a_worker_s_new_and_changed_lines_are_its_own():
    from factory.sendback import changed_new_lines

    assert changed_new_lines("a\nb\nc\n", "a\nB\nc\nd\n") == {2, 4}
    assert changed_new_lines(None, "x\ny\n") == {1, 2}


def test_a_unit_is_measured_on_its_own_lines_with_the_project_s_commands(tmp_path):
    import asyncio

    from factory.schemas import Gate
    from factory.sendback import measure_unit

    original, tree = tmp_path / "base", tmp_path / "tree"
    for root in (original, tree):
        (root / "app").mkdir(parents=True)
    (original / "app" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (tree / "app" / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    if a < b:\n        return 0\n"
        "    return a - b\n")
    (tree / "tests").mkdir()
    (tree / "tests" / "test_calc.py").write_text(
        "from app.calc import sub\ndef test_sub():\n    sub(3, 1)\n")
    covered = json.dumps({"files": {"app/calc.py": {"executed_lines": [1, 2, 4, 5, 7],
                                                    "missing_lines": [6]}}})
    coverage = Gate(name="coverage", command="cov", report_format="coverage-json",
                    files_command=f"printf '%s' '{covered}' > {{report}} # {{paths}}")
    said = asyncio.run(measure_unit(
        tree, None, ["app/calc.py", "tests/test_calc.py"], original=original,
        test_globs=["tests/*"], test_home=["tests/test_calc.py"], coverage=coverage,
        mutation=None))
    text = " | ".join(said)
    assert "app/calc.py: lines 6 never ran under your tests." in said, \
        "an uncovered line the worker wrote was not handed back"
    assert "lines 1" not in text and "lines 2" not in text, \
        "a line the worker did not write was handed back as its own"
    assert "test_sub" in text and "asserts nothing" in text, \
        "a test that cannot fail was not named"


def test_a_worker_is_sent_back_at_most_the_limit_and_what_it_rewrote_is_recorded(tmp_path):
    import asyncio
    from types import SimpleNamespace

    from factory.executors import CommandExecutor
    from factory.schemas import FileWrite
    from factory.sendback import SendBack

    executor = CommandExecutor.__new__(CommandExecutor)
    invoked: list[str] = []

    async def invoke(argv, cwd, env, stdin=""):
        invoked.append(stdin)
        (cwd / "tests_a.py").write_text(f"assert {len(invoked)}\n")
        return 0, "ok"

    executor._invoke = invoke
    measured: list[int] = []

    async def measure(tree, runner, written):
        measured.append(1)
        return ["app.py: lines 3 never ran under your tests."]

    tree = SimpleNamespace(path=tmp_path)
    collect = lambda: ([FileWrite(path="tests_a.py",  # noqa: E731
                                  contents=(tmp_path / "tests_a.py").read_text())], [], [])
    (tmp_path / "tests_a.py").write_text("old\n")
    records = asyncio.run(executor._send_back(
        SendBack(measure=measure, limit=2), SimpleNamespace(ready=True, runner=object()), tree,
        [FileWrite(path="tests_a.py", contents="old\n")], "THE TASK",
        tmp_path / ".factory-task.md", ["harness"], {}, ["harness"], [], collect, None))

    assert len(invoked) == 2, "the worker was sent back more often than the limit"
    assert all(stdin.startswith("# Before your work is accepted") for stdin in invoked)
    assert "THE TASK" in invoked[0], "a fresh session would not know what it was doing"
    assert [r.changed for r in records] == [["tests_a.py"], ["tests_a.py"]]

    none = asyncio.run(executor._send_back(
        SendBack(measure=measure, limit=2), SimpleNamespace(ready=False, runner=None), tree,
        [FileWrite(path="x", contents="y")], "T", tmp_path / "t.md", [], {}, [], [], collect, None))
    assert none == [], "a worker with no environment was measured on what could not be run"


def test_the_send_back_reaches_every_worker_and_resumes_its_conversation():
    import factory.executors as executors

    build = class_source(pipeline.Factory)
    assert "send_back=self._send_back_for(unit, sandbox.path)" in build
    assert "check_hollow_tests(" in inspect.getsource(pipeline.Factory._converge)
    direct = inspect.getsource(executors.DirectExecutor.run)
    assert "output.send_backs = []" in direct, "a model could write its own send-back record"
    driver = (ROOT / "factory" / "harnesses" / "openhands_driver.py").read_text(encoding="utf-8")
    assert "conversation_id=uuid.uuid5(" in driver, \
        "a worker sent back starts a new conversation and forgets what it wrote"


def test_hollow_tests_the_build_wrote_are_a_finding():
    from factory.schemas import FileWrite, WorkerOutput

    worker = WorkerOutput(unit_id="U-1", summary="s", files=[
        FileWrite(path="tests/test_a.py", contents="def test_a():\n    run()\n"),
        FileWrite(path="app/a.py", contents="def test_like_name():\n    pass\n")])
    findings = pipeline.check_hollow_tests([worker], ["tests/*"])
    assert [f.severity for f in findings] == ["major"]
    assert findings[0].files == ["tests/test_a.py"], "a source file was read as a test"


def test_an_adopted_coverage_suggestion_can_be_measured_on_a_worker_s_files(tmp_path):
    """A coverage suggestion adopted without where it writes, or without how to
    run it on some files, is a check that cannot be read or cannot send a
    worker back. Both travel from the suggestion to the check."""
    from factory.projects import ProjectRegistry
    from factory.schemas import EnvironmentSpec, Gate, ProjectSurvey, Recommendation

    repo = tmp_path / "repo"
    repo.mkdir()
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")
    project.state.gates = [Gate(name="tests", command="true", family="tests")]
    project.state.environment = EnvironmentSpec(kind="host", setup=["install node"])
    project.state.survey = ProjectSurvey(
        name="demo", summary="s", environment=EnvironmentSpec(kind="host"),
        recommendations=[Recommendation(
            title="Measure web coverage", kind="tests", why="w", evidence="e",
            would_gate="cd web && npx vitest run --coverage", report_format="lcov",
            report_path="web/coverage/lcov.info",
            files_command="cd web && npx vitest run --coverage {paths} # {report}")])
    project.state.stage = "ready"
    project = registry.save(project)
    project = registry.adopt_recommendation(
        project, "cd web && npx vitest run --coverage", "web-coverage")
    gate = next(g for g in project.state.gates if g.name == "web-coverage")
    assert gate.report_path == "web/coverage/lcov.info"
    assert "{paths}" in gate.files_command


def test_settings_written_as_code_are_reported_as_such_not_as_a_broken_file(tmp_path):
    from factory.schemas import Gate

    sandbox = _branch(tmp_path, {"web/vite.config.ts": "export default { test: {} }\n"},
                      {"web/vite.config.ts": "export default { test: { globals: true } }\n"})
    findings = pipeline.check_settings_changed(
        sandbox, [Gate(name="web-tests", command="t", config_files=["web/vite.config.ts#test"])])
    assert findings and "settings written as code" in findings[0].evidence
    assert "no longer parses" not in findings[0].evidence


def test_a_tool_that_writes_to_a_fixed_place_can_still_be_run_on_a_worker_s_files():
    """A JavaScript coverage run writes `coverage/lcov.info` whatever it is
    told. Its command for some files has no `{report}`, and must not need one."""
    from factory.projects import ProjectRegistry
    from factory.schemas import Gate
    from factory.sendback import pick_checks

    fixed = Gate(name="web-coverage", command="cd web && npx vitest run --coverage",
                 report_format="lcov", report_path="web/coverage/lcov.info",
                 files_command="cd web && npx vitest run --coverage {paths}")
    assert pick_checks([fixed])[0] is fixed
    assert not ProjectRegistry.gate_problems([fixed])
    nowhere = fixed.model_copy(update={"report_path": ""})
    assert pick_checks([nowhere])[0] is None
    assert ProjectRegistry.gate_problems([nowhere]), "a report nobody can find passed gate 0"


def test_a_command_for_some_files_that_writes_no_report_is_said_not_swallowed(tmp_path):
    """Run only when a worker is measured, never at baseline, so a wrong one
    would otherwise mean the worker is silently never sent back for coverage."""
    import asyncio

    from factory.schemas import Gate
    from factory.sendback import measure_unit

    original, tree = tmp_path / "base", tmp_path / "tree"
    for root in (original, tree):
        (root / "tests").mkdir(parents=True)
    (tree / "calc.py").write_text("def f():\n    return 1\n")
    (tree / "tests" / "test_calc.py").write_text("def test_f():\n    assert f() == 1\n")
    broken = Gate(name="coverage", command="c", report_format="coverage-json",
                  files_command="true {paths} {report}")
    notes: list[str] = []
    asyncio.run(measure_unit(tree, None, ["calc.py", "tests/test_calc.py"], original=original,
                             test_globs=["tests/*"], test_home=[], coverage=broken,
                             mutation=None, notes=notes))
    assert notes and "coverage could not be run on this unit's tests" in notes[0]
    assert "Not fully measured during its turn" in inspect.getsource(
        __import__("factory.executors", fromlist=["x"]).CommandExecutor.run)


def test_keeping_a_check_is_remembered_and_the_same_change_is_not_proposed_again(tmp_path):
    from factory.onboarding import strip_unchanged
    from factory.schemas import Gate, ProposedGateChange, SurveyDiff

    registry, project, _ = _project_with_proposal(tmp_path)
    project = registry.rule_on_proposed_check(project, "change:tests", accept=False, reason="slow")
    project = registry.rule_on_proposed_check(project, "add:fmt", accept=False)
    assert [(k.name, k.command) for k in project.state.kept_checks] == [("tests", "pytest --cov -q")]
    assert "fmt" in registry.declined_gates(project), "a refused new check can be proposed again"

    again = SurveyDiff(summary="s", gate_changes=[
        ProposedGateChange(action="change", name="tests", reason="r", evidence="e",
                           gate=Gate(name="tests", command="pytest --cov -q", family="tests")),
        ProposedGateChange(action="change", name="tests", reason="r", evidence="e",
                           gate=Gate(name="tests", command="pytest --cov --fast -q", family="tests"))])
    strip_unchanged(again, project.state)
    assert [g.gate.command for g in again.gate_changes] == ["pytest --cov --fast -q"], \
        "a kept check was proposed again, or a different change to it was swallowed"


# -- rules a check runs that answer another family's question --------------------


def test_a_family_a_check_s_rules_already_answer_is_not_suggested_a_tool(tmp_path):
    """Security rules switched on in the linter are a quality check already.
    The family read as empty and was offered a tool for what it enforces."""
    from factory.schemas import Gate, RuleSet

    lint = Gate(name="lint", command="ruff check .", family="structure", also=[
        RuleSet(family="quality", what="security rules (ruff S)",
                evidence='api/pyproject.toml: select = [..., "S"]')])
    registry, project = _project_with_suggestions(tmp_path, gates=[lint])
    titles = {r.title for r in registry.live_recommendations(project)}
    assert "Measure complexity" not in titles, "a family the linter covers was suggested a tool"
    assert "Lint the code" in titles


def test_recording_what_a_check_covers_is_not_a_change_to_what_it_runs(tmp_path):
    from factory.schemas import RuleSet

    registry, project = _approved_project(tmp_path)
    before = registry.approval_fingerprint(project)
    described = project.state.gates[0].model_copy(update={"also": [
        RuleSet(family="quality", what="security rules")]})
    project = registry.update(project, {"gates": [described]})
    assert project.state.baseline is not None, "describing a check cleared what it measured"
    assert registry.approval_fingerprint(project) == before, "a description needs approving again"
    assert project.state.gates[0].also, "the description was not kept"


def test_a_write_is_never_refused_for_being_short():
    """The veto is gone. Only what an agent says about its own output stops a
    write now -- a path it disclaimed is one it has told us is a fragment, which
    is evidence rather than a threshold."""
    src = inspect.getsource(pipeline.Factory._apply_writes)
    assert "destructive_writes(" not in src, \
        "the line-count guard can still silently drop a file from a unit's output"
    assert "said it could not return a complete file" in src, \
        "an agent's own disclaimer no longer stops a fragment being written"

    converge = inspect.getsource(pipeline.Factory._converge)
    assert "check_destructive_writes(sandbox, self.config)" in converge, \
        "destruction is neither refused nor reported"


def test_a_feature_s_new_libraries_reach_the_oracle_through_the_spec():
    """The one thing about the environment the verify lane cannot measure.

    Packages are measured at gate 0, before the feature exists, so a library the
    feature itself adds can never appear in that list. It travels on the spec
    instead, which means a human sees it before freezing -- taking on a
    dependency is a decision, and one of the few in a spec that outlives the
    feature.
    """
    from factory.schemas import Spec

    assert "new_dependencies" in Spec.model_fields

    spec = Spec(title="T", intent="i", summary="s", new_dependencies=["croniter"])
    rendered = workspace.verify_context(spec, ["httpx"])
    assert "croniter" in rendered, "a library the feature adds never reaches the oracle"
    assert "Libraries this feature adds" in rendered
    assert "httpx" in rendered, "the measured environment is no longer shown"

    writer = (ROOT / "factory" / "roles" / "spec_writer.md").read_text(encoding="utf-8")
    flat = " ".join(writer.split())
    assert "new_dependencies" in flat, "nothing tells the spec writer to fill the field"
    assert "measured before this feature existed" in flat, \
        "the spec writer is not told why the oracle cannot find these itself"


def test_a_criterion_tag_is_extracted_not_trusted():
    spec = _spec12()
    n = pipeline.normalize_criterion_ids

    # The exact value that cost a run its whole verification layer.
    assert n([", "], spec) == []
    assert n(["", "  ", "none"], spec) == []

    # A model that means the criterion finds it, however it writes it.
    assert n(["AC-1, AC-2"], spec) == ["AC-1", "AC-2"]
    assert n(["ac 3"], spec) == ["AC-3"]
    assert n(["AC_4"], spec) == ["AC-4"]
    assert n(["AC-07"], spec) == ["AC-7"], "a zero-padded id names the same criterion"

    # A tag pointing at nothing is dropped: keeping it would put a test in the
    # matrix under an id the human cannot look up.
    assert n(["AC-99"], spec) == []
    assert n(["AC-1", "AC-1"], spec) == ["AC-1"], "duplicates are one tag"


def test_the_oracles_tags_are_normalised_before_anything_reads_them():
    src = inspect.getsource(pipeline.Factory._build)
    assert "normalize_criterion_ids(test.criterion_ids, spec)" in src, \
        "the matrix is built from tags nothing has checked"
    assert "oracle.untestable_criteria" in src, \
        "a criterion the oracle declared untestable is matched by raw string"
    # Before relocation, so nothing downstream sees an untouched tag.
    tags_at = src.index("normalize_criterion_ids(test.criterion_ids, spec)")
    moved_at = src.index("place_blind_file(")
    assert tags_at < moved_at


def test_a_blind_suite_tagged_to_nothing_is_one_blocker_not_twelve_shrugs():
    """The two readings ask for opposite things from a human.

    Twelve criteria each saying "no blind test was tagged to this" reads as an
    oracle that declined the work. One finding saying the suite exists, ran, and
    is attached to nothing reads as what it is.
    """
    from factory.schemas import OracleSuite, TestFile

    spec = _spec12()
    suite = OracleSuite(strategy="s", tests=[
        TestFile(path="tests/oracle/test_a.py", contents="x", criterion_ids=[]),
        TestFile(path="tests/oracle/test_b.py", contents="x", criterion_ids=[]),
    ])
    found = pipeline.check_blind_tags(suite, spec)
    assert len(found) == 1 and found[0].severity == "blocker"
    assert "don't say which requirements they check" in found[0].title
    assert "tagged to no acceptance criterion" in found[0].detail
    assert "not missing and not failing" in found[0].detail, \
        "the finding does not distinguish an unattached suite from an absent one"

    # One good tag anywhere is enough: this is about a suite attached to nothing.
    suite.tests[0].criterion_ids = ["AC-1"]
    assert pipeline.check_blind_tags(suite, spec) == []
    # And an oracle that wrote nothing is a different finding's business.
    assert pipeline.check_blind_tags(OracleSuite(strategy="s"), spec) == []


def test_the_untagged_check_runs_on_every_build():
    src = inspect.getsource(pipeline.Factory._converge)
    assert "check_blind_tags(oracle, checked)" in src


def test_the_shape_the_human_approved_is_shown_to_the_agents_it_governs():
    """`spec.changes` reached no prompt at all.

    The spec writer is asked to declare columns, endpoints and surfaces because "a
    paragraph cannot be checked, and a declared column can" -- and the only
    reader was `check_declared_changes`, code comparing the declaration against
    what got built. `spec_text` rendered nine sections and this was not one.

    So the oracle was asked to verify a migration without being told which
    column, which table, or that there is a backfill, and invented the
    surrounding schema: `tenants.name` for a table whose column is
    `display_name`. A declared column belongs to the contract, next to an
    acceptance criterion -- it says what the work must produce, not how, which
    is the line the air gap is actually drawn on.
    """
    from factory.schemas import (DataChange, InterfaceChange, PlannedChanges,
                                 Spec, SurfaceChange)
    from factory.workspace import spec_text

    spec = Spec(title="t", intent="i", summary="s", changes=PlannedChanges(
        data=[DataChange(operation="alter", table="audit_logs", column="actor",
                         type="VARCHAR", nullable=False, note="backfill NULLs to 'system'")],
        interface=[InterfaceChange(operation="add", method="GET", path="/api/audit-logs",
                                   change="tenant-scoped read path")],
        surfaces=[SurfaceChange(operation="alter", where="Queue.tsx", change="header instead")]))
    said = spec_text(spec)

    for needle in ("audit_logs.actor", "VARCHAR", "NOT NULL", "backfill",
                   "GET /api/audit-logs", "Queue.tsx"):
        assert needle in said, f"the declared change does not reach a prompt: {needle!r}"

    # And it is named as the contract, because an agent shown a column could
    # otherwise read it as a hint about the implementation -- which is the one
    # thing it must not infer.
    assert "This is the contract, not the implementation" in said

    # A spec that declares nothing says nothing, rather than an empty heading.
    bare = Spec(title="t", intent="i", summary="s")
    assert "The shape of this change" not in spec_text(bare)


def test_an_oracle_with_good_fixtures_is_still_told_not_to_guess():
    """The warning lived on the rule for projects with nothing to reuse, and
    not on the rule for projects with plenty.

    Which is backwards. A project with good fixtures invites the assumption
    that everything is reachable through them, and the gap when it comes is
    never a missing *capability* -- it is missing *knowledge*, which has no
    channel of its own and so gets filled with a guess.

    Seen for real: an oracle needed to stage rows as they stood before a
    migration, found no fixture for it, declared no `requires`, wrote raw SQL
    against a schema it has never seen, named `tenants.name` where the schema
    says `display_name`, and AC-21 came back red -- for a defect in the test,
    while the migration under it was sound. A blocker pointing at the wrong
    file.
    """
    from factory.schemas import TestingSurface, TestingTier
    from factory.workspace import testing_context

    def rule_for(verdict: str) -> str:
        surface = TestingSurface(tiers=[TestingTier(
            tier="integration", runner="pytest", verdict=verdict,
            fixtures=["make_patient(slug) -- a patient row of that tenant"])])
        return testing_context(surface)

    for verdict in ("usable", "inline_only"):
        said = rule_for(verdict)
        assert "`requires`" in said, \
            f"a {verdict!r} tier is never told what to do about a state it cannot arrange"

    usable = rule_for("usable")
    assert "never seen this schema" in usable, \
        "the one agent that cannot read the repository is not told it cannot read the repository"
    # And why it matters, which is not that the criterion is lost -- it is that
    # the criterion is lost while accusing the wrong code.
    assert "as though the implementation were wrong" in usable


def test_a_crashed_run_does_not_lock_the_feature_it_crashed_in():
    """`stage` is the field a crash is the reason nobody updated.

    The rebuild guard read it and nothing else, so a server restarted mid-build
    left `building` behind with a dead pid on it and refused every rebuild
    after -- the feature locked by the record of the thing that broke it, with
    no way out through the API. Seen for real, on the run this was found in.

    `is_orphaned` asks the operating system, which is the only party that knows
    whether anything is working. A rebuild is exactly the remedy for a feature
    that says it is building and is not.
    """
    server_src = factory_source("server")
    guard = server_src[server_src.index('detail="feature is already building"') - 400:]
    guard = guard[:guard.index('detail="feature is already building"') + 80]
    assert "not is_orphaned(state)" in guard, \
        "a crashed build locks its feature against the one action that would clear it"

    # And the predicate itself: a live pid holds the lock, a dead one does not.
    from factory.pipeline import is_orphaned, this_process
    from factory.schemas import FeatureState

    live = FeatureState(feature_id="f", project_id="p", title="t", intent="i",
                        stage="building", owner=this_process())
    assert not is_orphaned(live), "a running build is being treated as abandoned"

    dead = live.model_copy(update={"owner": "999999:not-a-real-process"})
    assert is_orphaned(dead), "a dead owner still holds the feature"

    settled = live.model_copy(update={"stage": "awaiting_verdict"})
    assert not is_orphaned(settled), "a finished feature is being read as a crash"


def test_whether_a_project_can_name_a_failing_test_is_asked_before_it_pays():
    """Three times in one week, attribution machinery was present and unwired,
    and every time the silence read as good news: fewer findings, a cleaner
    number, a packet better than the run deserved.

    All of it was knowable from configuration before the run started. This asks
    at the top, where an answer is cheap to act on, rather than reporting it
    beside the packet it ruined.
    """
    from factory.schemas import TestFileCommand

    class St:
        def __init__(self, rules): self.test_file_commands = rules

    good = [TestFileCommand(match="*", command="pytest {path}",
                            report="pytest --junitxml={report} {path}")]
    assert pipeline.check_attribution_wiring(St(good)) == [], \
        "a project that can name a failing test is being told it cannot"

    bare = [TestFileCommand(match="*", command="pytest {path}")]
    found = pipeline.check_attribution_wiring(St(bare))
    assert [f.id for f in found] == ["attribution-2"]
    assert found[0].severity == "major"
    assert "{report}" in found[0].recommendation, \
        "the finding does not say what to write to fix it"

    none = pipeline.check_attribution_wiring(St([]))
    assert [f.id for f in none] == ["attribution-1"]
    # The breaker falls back to these same rules, so their absence costs both
    # the blind suite and the probes.
    assert "breaker" in none[0].detail

    # Before the build, not with the packet. `run_build` is where a run commits
    # to spending, and every phase after it costs money.
    run = inspect.getsource(pipeline.Factory.run_build)
    assert "check_attribution_wiring(self.project.state" in run, \
        "the check runs after the phases it would have warned about"
    assert run.index("check_attribution_wiring") < run.index("await self._build"), \
        "the check runs after the build it exists to precede"

    # And it reaches the packet too, because what the project could say and
    # what the run then said are two halves of one reading.
    assert "check_attribution_wiring(" in inspect.getsource(pipeline.Factory._converge), \
        "the finding never reaches the packet"

    # And it names no runner. Which one this repository uses is the surveyor's
    # business, approved by a human at gate 0; a table of runner names in the
    # orchestrator is what went dark when vitest changed its summary line.
    src = inspect.getsource(pipeline.check_attribution_wiring)
    for runner in ("pytest", "vitest", "jest", "playwright", "go test"):
        assert runner not in src.lower(), \
            f"{runner!r} is named in the orchestrator; the survey knows and this does not"


def test_a_breaker_probe_is_a_test_file_and_this_project_says_how_to_run_one():
    """A failing probe with no name is a finding nobody can act on.

    Nine probes ran as one command. The suite exited 1 on a real migration
    error -- a downgrade-to-base that left PostgreSQL enum state behind. The
    packet could name no probe, so it said so honestly and filed nothing a
    human could use: `failing: []`, and "no per-probe command is configured for
    it".

    `rework.breaker_file_command` was empty. But a breaker probe *is* a test
    file, and the project already declares how to run one of those -- it has to,
    or the blind suite could not be attributed either. Asking for the same thing
    a second time under a different key is how the attribution that exists comes
    to be switched off by default.
    """
    src = inspect.getsource(pipeline.Factory._run_breaker)

    assert "else list(self.project.state.test_file_commands)" in src, \
        "the breaker will not fall back to the rules this project already declares"
    assert "cfg.breaker_file_command.strip()" in src, \
        "an explicit per-probe command no longer wins over the project's rules"

    # Every probe failing is what a broken runner looks like, and also what a
    # suite of real defects looks like. The first is common and the second is
    # not, so the ambiguity is reported rather than filed as N findings -- the
    # same reason the unattributed case refuses to blame them all.
    guard = src[src.index("if per_file:"):]
    guard = guard[:guard.index("if execution.exit_code != 0")]
    assert "len(failed) == len(exits)" in guard, \
        "a probe runner that cannot start blames every probe it could not run"
    assert "len(exits) > 1" in guard, \
        "a single failing probe is being treated as an ambiguous whole-suite failure"


def test_a_fixture_this_spec_builds_is_not_a_fixture_this_project_lacks():
    """`setup-gap-1` compares each criterion against the gate-0 survey, and a
    feature whose whole job is to make a repository testable adds fixtures the
    survey has never seen.

    Seen for real: a reopened spec answered every gap at gate 1 -- add
    `make_user`, add `app_client`, add `make_portal_token`, each as an
    acceptance criterion of its own -- and was told that fourteen of its
    twenty-five criteria needed setup this project offers no fixture for. Every
    one of the fourteen named a fixture the same spec was committing to build.
    The spec writer even said so in the prose: "added by AC-10".

    The oracle writes its suite after the workers, so a fixture this spec
    promises exists by the time a blind test reaches for one. What is worth
    saying before the freeze is not that it is missing -- it is how much rests
    on it arriving.
    """
    from factory.schemas import AcceptanceCriterion, Spec, TestingSurface, TestingTier

    surface = TestingSurface(tiers=[TestingTier(
        tier="integration", runner="pytest", verdict="usable",
        fixtures=["make_patient(slug, **columns) -- a patient row of that tenant",
                  "api_client -- httpx.AsyncClient against the running API"])])

    def spec_with(provides_on_ac2: list[str]) -> Spec:
        return Spec(title="t", intent="i", summary="s", acceptance_criteria=[
            AcceptanceCriterion(
                id="AC-1", statement="An audit row records the operator that caused it.",
                setup=["make_user(slug, email=...) -- added by AC-2"]),
            AcceptanceCriterion(
                id="AC-2", statement="`backend/factories.py` exports `make_user(slug, email=...)`.",
                setup=["make_patient(slug, **columns) -- a patient row of that tenant"],
                provides=provides_on_ac2),
        ])

    # Without `provides`, AC-1 is reported as unverifiable by the spec that
    # fixes it -- which is the false positive this exists to remove.
    blind = pipeline.unmet_setup(spec_with([]), surface)
    assert [cid for cid, _ in blind] == ["AC-1"]

    fixed = spec_with(["make_user(slug, email=..., role=...)"])
    assert pipeline.unmet_setup(fixed, surface) == [], \
        "a criterion is reported as unverifiable for want of a fixture this spec adds"

    # Not silently, though. AC-2 is now load-bearing for AC-1, and a criterion
    # carrying other criteria on its back reads like any other one until
    # something says so.
    assert pipeline.promised_setup(fixed) == {"make_user": ["AC-1"]}
    found = {f.id: f for f in pipeline.check_spec_testability(fixed, surface)}
    assert "setup-gap-1" not in found
    note = found.get("setup-promised-1")
    assert note is not None, "the promise is dropped instead of being stated"
    assert note.severity == "minor", "a fixture being built is not a defect"
    assert note.criterion_ids == ["AC-1"]
    assert "make_user" in note.detail

    # A fixture that provides only itself is nobody's dependency and is not
    # worth a finding.
    alone = Spec(title="t", intent="i", summary="s", acceptance_criteria=[AcceptanceCriterion(
        id="AC-1", statement="s", setup=["make_user(slug) -- added by this criterion"],
        provides=["make_user(slug)"])])
    assert pipeline.promised_setup(alone) == {}
    assert not [f for f in pipeline.check_spec_testability(alone, surface)
                if f.id.startswith("setup-")]

    # And the spec writer has to be told the field exists, or it fills none of them
    # and the comparison is back to the survey alone.
    plan_src = inspect.getsource(pipeline.Factory._write_spec)
    assert "`provides`" in plan_src, "the spec writer is never told to declare what a criterion adds"


def test_an_unmet_oracle_requirement_is_reported_against_the_harness():
    """The finding whose subject is neither the code nor the oracle's own work.

    An oracle that cannot reach a criterion for want of a capability has been
    able to say so only in prose, where nothing reads it. One did say it --
    "authentication and entity-creation interfaces are unspecified" -- and the
    run went on for 2h43m and reported the feature as the problem.

    So it is a field with structure, and what matters is that the severity
    tracks the real cost: criteria this spec has means the packet is wrong about
    the code unless it says why, and no criteria means a note for next time.
    """
    from factory.schemas import OracleRequirement, OracleSuite

    spec = _spec12()
    costly = OracleSuite(strategy="s", requires=[OracleRequirement(
        need="a way to authenticate as a specific user",
        criterion_ids=["AC-1", "AC-2"],
        detail="AC-1 turns on which user acted; nothing in a request names one.",
    )])
    found = pipeline.check_oracle_requirements(costly, spec)
    assert len(found) == 1
    assert found[0].severity == "blocker", \
        "a requirement that cost verified criteria is reported as a note"
    assert found[0].criterion_ids == ["AC-1", "AC-2"]
    assert "because of the harness, not because" in found[0].detail, (
        "the finding does not say the criteria are unverified for a reason "
        "outside the code, which is the only thing it exists to say"
    )
    assert "oracle_runtime" in found[0].recommendation, \
        "the finding does not tell a human where to close the gap"

    # Cost nothing, so it is a note rather than a blocker -- still reported,
    # because the next run over this project hits the same wall.
    free = OracleSuite(strategy="s", requires=[OracleRequirement(
        need="a disposable database", criterion_ids=[])])
    assert pipeline.check_oracle_requirements(free, spec)[0].severity == "minor"

    # Ids this spec does not have cannot have cost a criterion. A typo must not
    # be able to escalate a note into a blocker.
    typo = OracleSuite(strategy="s", requires=[OracleRequirement(
        need="a disposable database", criterion_ids=["AC-99"])])
    only = pipeline.check_oracle_requirements(typo, spec)[0]
    assert only.severity == "minor" and only.criterion_ids == []
    assert "AC-99" in only.detail, "an unrecognised id is dropped without saying so"

    assert pipeline.check_oracle_requirements(OracleSuite(strategy="s"), spec) == []
    assert pipeline.check_oracle_requirements(None, spec) == []


def test_the_requirements_check_runs_and_is_announced_while_the_run_is_live():
    """Both halves matter and they are read at different times.

    The ledger is read at the end. The phase line is read while the run is still
    going, and the failure this stands for wasted every minute after the oracle
    finished -- building against a verification layer already known to be
    broken.
    """
    from factory.schemas import OracleRequirement, OracleSuite

    assert "check_oracle_requirements(oracle, checked)" in \
        inspect.getsource(pipeline.Factory._converge)

    detail = pipeline._suite_detail(OracleSuite(strategy="s", requires=[
        OracleRequirement(need="a way to authenticate as a specific user")]))
    assert "unmet environment requirement(s)" in detail, \
        "an unmet requirement is invisible until the packet is assembled"
    assert "authenticate as a specific user" in detail


def test_the_blind_suite_runs_a_configured_command_not_an_invented_one():
    """Four runs, four different invented commands, each naming a directory the
    oracle chose and `confine_to` then moved -- so the command pointed at a path
    that did not exist and the suite collected nothing.

    The oracle writes tests. Where they go is fixed and how they run is a
    property of the project, approved by a human who has seen it. A blind agent
    is the worst-placed thing in the system to decide it.
    """
    from factory.schemas import ProjectState

    default = ProjectState(project_id="p").oracle_command
    assert default, "with no configured command the blind suite cannot run at all"
    assert "{oracle_dir}" in default, "the command cannot name where the tests are"

    src = inspect.getsource(pipeline.Factory._run_blind_suite)
    assert 'command.replace("{oracle_dir}", own_dir)' in src, \
        "the configured command is used without being pointed at the directory"
    # The project's first, the model's only if a human cleared the project's.
    assert "self.project.state.oracle_command or declared" in src, \
        "a model-invented command can still take precedence over the configured one"


def test_an_oracle_suite_that_names_no_criterion_is_asked_again():
    """The failure the schema cannot see.

    `llm.ask` retries a response that fails validation, and this one passes:
    `criterion_ids: [", "]` is a valid list of valid strings. So a run built
    everything, ran everything, and reported twelve criteria unverified with
    nothing anywhere saying the tags were why.
    """
    from factory.config import Config
    from factory.schemas import OracleSuite, TestFile

    spec = _spec12()
    comma = OracleSuite(strategy="s", tests=[
        TestFile(path="a.py", contents="x", criterion_ids=[", "])])
    assert not pipeline.usable_suite(comma, spec), \
        "a suite tagged with punctuation counts as usable"

    empty = OracleSuite(strategy="s", tests=[
        TestFile(path="a.py", contents="x", criterion_ids=[])])
    assert not pipeline.usable_suite(empty, spec)

    # One real tag anywhere is enough to be worth running.
    good = OracleSuite(strategy="s", tests=[
        TestFile(path="a.py", contents="x", criterion_ids=[]),
        TestFile(path="b.py", contents="x", criterion_ids=["AC-4"])])
    assert pipeline.usable_suite(good, spec)

    # A suite that wrote nothing is a different problem and not retried here.
    assert not pipeline.usable_suite(OracleSuite(strategy="s"), spec)

    assert Config().pipeline.oracle_retries >= 1
    src = inspect.getsource(pipeline.Factory._verify_lane)
    assert "usable_suite(suite, spec)" in src
    assert 'store.append("oracle_retry"' in src, \
        "the retry happens but leaves no record that it did"


def test_an_arbiter_that_leaves_findings_unruled_is_asked_again_by_id():
    """The same failure as the oracle's untagged suite, one phase later, and
    worse disguised.

    `dispositions: []` validates, so `llm.ask` never retried it. And the
    default for anything unruled is a human ruling -- correct, and the reason
    the non-answer was invisible: the packet said 27 escalations, which reads
    as an arbiter that considered every finding and sent them all up.

    A route running a coding harness is what surfaced it. Asked for a
    structured answer it replied `{"summary": "Placeholder while I inspect the
    repo.", "dispositions": []}` -- 72 characters where the other rounds wrote
    thousands. Nothing was routed, the repair loop never ran, and the packet
    came back at 8 of 25 with no record saying why.
    """
    from factory.config import Config
    from factory.schemas import ArbiterReport, FindingDisposition

    def rule(fid):
        return FindingDisposition(finding_id=fid, disposition="escalate", reason="n/a")

    open_ids = ["F-1", "F-2", "F-3"]
    stub = ArbiterReport(summary="Placeholder while I inspect the repo.")
    assert pipeline.unruled(stub, open_ids) == open_ids, \
        "an empty disposition list counts as an answer"
    assert pipeline.unruled(None, open_ids) == open_ids

    # Partial is the case that matters: ruling on one and dropping two has the
    # same hole as ruling on none, only smaller.
    assert pipeline.unruled(ArbiterReport(summary="s", dispositions=[rule("F-2")]),
                            open_ids) == ["F-1", "F-3"]

    # Findings that were not on the table do not count as answering these.
    assert pipeline.unruled(ArbiterReport(summary="s", dispositions=[rule("F-9")]),
                            open_ids) == open_ids

    complete = ArbiterReport(summary="s", dispositions=[rule(f) for f in open_ids])
    assert pipeline.unruled(complete, open_ids) == []

    assert Config().pipeline.arbiter_retries >= 1
    src = inspect.getsource(pipeline.Factory._arbitrate)
    assert "unruled(answer, open_ids)" in src
    assert 'store.append("arbiter_retry"' in src, \
        "the retry happens but leaves no record that it did"
    # The retry has to name them. "Answer about all of them" is not actionable.
    assert '"\\n".join(f"- {fid}" for fid in short)' in src, \
        "the arbiter is asked again without being told which findings it missed"
    # And the phase has to say when it fell back, or the timeline shows a full
    # set of escalations and calls that a decision.
    assert "default" in src and "ruled on" in src, \
        "a non-answer still renders as though every finding was judged"


# --------------------------------------------------------------------------
# INV: a suite that will not load is the oracle's bug, not the code's
#
# The oracle writes tests it can never execute -- it has not seen the repository
# and it runs nothing. So its files carry ordinary bugs like any unrun code, and
# the cost of one is a whole run's verification. Twice: a suite imported a
# package that was not installed, and a suite mangled a connection string it had
# read correctly. Both times every criterion came back unverified and nothing
# said the tests were the reason, which reads as a verdict on the feature.
# --------------------------------------------------------------------------


def test_a_suite_that_cannot_load_is_reported_against_the_oracle():
    from factory.schemas import GateReport, GateResult

    broken = GateReport(results=[
        GateResult(name="backend-tests", passed=True),
        GateResult(name="blind-tests", passed=False, exit_code=2, output_tail=(
            "The blind suite could not be loaded, so it was not run. No criterion is "
            "verified, and none of that is a statement about the feature: these are "
            "the oracle's own files failing to import or collect.\n\n"
            "ImportError while loading conftest 'tests/oracle/conftest.py'")),
    ])
    found = pipeline.check_blind_suite_loads(broken)
    assert len(found) == 1
    finding = found[0]
    assert finding.severity == "blocker"
    assert finding.files == [], "this is not a defect in any file of the feature"
    assert "says nothing about the feature" in finding.detail
    assert "ImportError" in finding.evidence, "the human is not shown what broke"

    # A suite that ran and failed is the opposite fact and must not be caught.
    ran_and_failed = GateReport(results=[GateResult(
        name="blind-tests", passed=False, exit_code=1,
        output_tail="tests/oracle/test_x.py::test_ac1 FAILED\n1 failed in 0.4s")])
    assert pipeline.check_blind_suite_loads(ran_and_failed) == [], \
        "a real test failure was reported as the suite being broken"

    assert pipeline.check_blind_suite_loads(GateReport(results=[
        GateResult(name="blind-tests", passed=True)])) == []


def test_the_suite_is_loaded_before_it_is_run():
    """Ordering is the whole value: the distinction only exists if the load is
    checked separately, and a run that fails tells you nothing about which of
    the two happened."""
    from factory.schemas import ProjectState

    src = inspect.getsource(pipeline.Factory._run_blind_suite)
    collect_at = src.index("oracle_collect_command")
    run_at = src.index("timeout_s=cfg.oracle_timeout_s")
    assert collect_at < run_at, "the suite is run before anything checks it loads"
    assert "could not be loaded, so it was not run" in src

    default = ProjectState(project_id="p").oracle_collect_command
    assert default and "{oracle_dir}" in default, \
        "the collection check has no command, so it never runs"

    converge = inspect.getsource(pipeline.Factory._restate_from_checks)
    assert "check_blind_suite_loads(gates)" in converge
    assert "self._restate_from_checks(" in inspect.getsource(pipeline.Factory._converge)


def test_a_project_may_turn_the_collection_check_off():
    """Empty disables it, for a runner with no collect-only mode."""
    src = inspect.getsource(pipeline.Factory._run_blind_suite)
    assert '(self.project.state.oracle_collect_command or "").strip()' in src \
        and "if collect:" in src
    assert "if collect:" in src, "an empty command still tries to run something"


def test_approval_answers_the_gate_list_not_one_baseline_run(tmp_path):
    registry, project = _approved_project(tmp_path)
    assert project.state.stage == "ready"

    recorded = registry.approved_fingerprint(project)
    assert recorded, "approval records nothing about what it approved"
    assert recorded == registry.approval_fingerprint(project), \
        "the fingerprint does not match the project it was taken from"

    # The fingerprint is built from exactly what sends a project back to gate 0.
    from factory.schemas import Gate
    project.state.gates = [Gate(name="tests", command="different")]
    assert registry.approval_fingerprint(project) != recorded, \
        "a changed gate does not change the fingerprint, so a real change would be missed"


def test_the_oracle_gets_the_base_commit_and_nothing_newer():
    """The one line that would undo INV-1 is a caller handing this the branch.

    This used to read "never gets a checkout of anything", and an empty
    directory did enforce the invariant -- along with a great deal the
    invariant does not say. "Cannot read the implementation" is not "cannot
    read anything": an author who can read neither the schema nor the fixtures
    nor the existing tests does not write a more independent test, it writes
    one with guesses in it. One did, naming `tenants.name` for a column called
    `display_name`, and a criterion came back red over a migration that was
    correct.

    So the tree is the commit the feature branched from -- every file that
    existed before the work, and by construction not one line of the work. The
    check gets stricter rather than looser, because the failure it guards is
    worse than the one it replaces: an empty tree that is accidentally full
    costs a run its verification, and a base tree that is accidentally the
    branch costs it every green criterion while looking exactly like a run that
    verified something.
    """
    src = inspect.getsource(pipeline.Factory._author_blind_suite)
    assert "BaseTree(" in src, "the verify lane is not given the repository it reads"
    assert "tree.assert_at_base()" in src, \
        "nothing proves the checkout is the base commit, so it holds until someone changes it"
    assert "state.sandbox.base_sha" in src, \
        "the tree is checked out at something other than where the feature branched from"
    assert "Worktree(" not in src, \
        "the verify lane must never open the worker's checkout, which has the work in it"


def test_every_file_on_disk_reaches_the_suite_even_when_untagged():
    """The files are authoritative. One the account forgot still runs, so
    dropping it here would mean running a suite the packet does not describe."""
    from factory.schemas import OracleAccount, TestFileAccount

    session = _authored([("tests/oracle/test_a.py", "assert 1"),
                         ("tests/oracle/test_b.py", "assert 2")])
    account = OracleAccount(
        strategy="s", command="pytest tests/oracle",
        tests=[TestFileAccount(path="tests/oracle/test_a.py", criterion_ids=["AC-1"])],
    )
    suite = pipeline.assemble_oracle_suite(session, account)
    assert [t.path for t in suite.tests] == [
        "tests/oracle/test_a.py", "tests/oracle/test_b.py"]
    assert suite.tests[0].criterion_ids == ["AC-1"]
    assert suite.tests[1].criterion_ids == [], "an untagged file must arrive untagged"
    # Contents come from disk, never from the account -- which has no field for
    # them, which is the whole point.
    assert suite.tests[0].contents == "assert 1"


def test_a_file_tagged_by_its_basename_is_still_tagged():
    """A whole suite arriving untagged over a spelling is the failure this
    prevents: the matrix is built from these ids and nothing else."""
    from factory.schemas import OracleAccount, TestFileAccount

    session = _authored([("tests/oracle/test_slots.py", "assert 1")])
    account = OracleAccount(
        strategy="s",
        tests=[TestFileAccount(path="test_slots.py", criterion_ids=["AC-3"])],
    )
    suite = pipeline.assemble_oracle_suite(session, account)
    assert suite.tests[0].criterion_ids == ["AC-3"]


def test_scaffolding_the_oracle_declares_cannot_become_evidence():
    """The failure this split exists to prevent, in the shape it actually had.

    An oracle wrote four files, one of which asserted anything, and tagged the
    README, the shared setup file and the manifest with the union of every
    criterion its real test covered. Eighteen criteria each reported four pieces
    of evidence, three of which asserted nothing -- and `compute_trace` decides
    `traced` against `untested` on nothing but whether that list is non-empty,
    so a criterion whose only tag was a setup file would have read as verified.
    """
    from factory.schemas import OracleAccount, TestFileAccount

    session = _authored([("tests/oracle/README.md", "# how this suite works"),
                         ("tests/oracle/setup_fixtures.py", "import pytest"),
                         ("tests/oracle/manifest.json", "{}"),
                         ("tests/oracle/test_migration.py", "assert 1")])
    account = OracleAccount(
        strategy="s", command="pytest tests/oracle",
        tests=[TestFileAccount(path="tests/oracle/test_migration.py",
                               criterion_ids=["AC-1", "AC-2"])],
        support=["tests/oracle/README.md", "tests/oracle/setup_fixtures.py",
                 "tests/oracle/manifest.json"],
    )
    suite = pipeline.assemble_oracle_suite(session, account)
    assert [t.path for t in suite.tests] == ["tests/oracle/test_migration.py"]
    assert [f.path for f in suite.support] == [
        "tests/oracle/README.md", "tests/oracle/setup_fixtures.py",
        "tests/oracle/manifest.json"]
    # Nothing is dropped: all four still reach disk and the protected set.
    assert len(pipeline.oracle_files(suite)) == 4
    assert [f.contents for f in suite.support][0] == "# how this suite works"

    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id=f"AC-{i}", statement="s") for i in range(1, 4)])
    rows = {r.criterion_id: r for r in compute_trace(spec, [], suite)}
    assert rows["AC-1"].test_names == ["tests/oracle/test_migration.py"]
    assert rows["AC-3"].test_names == [], \
        "a criterion no test covers must not inherit the suite's scaffolding"


def test_a_support_file_cannot_carry_a_criterion_id_at_all():
    """The guarantee is structural, not a rule the assembler enforces.

    A flag the model sets truthfully or not would leave the same bug one bad
    answer away. `SupportFile` has nowhere to put a tag, so the dishonest answer
    is unavailable rather than discouraged.
    """
    assert "criterion_ids" not in SupportFile.model_fields
    assert set(SupportFile.model_fields) == {"path", "contents"}


def test_support_wins_when_the_account_both_tags_a_file_and_calls_it_support():
    """Precedence chosen for its failure direction, not for tidiness.

    Reading it this way can only lose evidence, and lost evidence reads
    `untested` on the criteria screen where a human sees it. Reading it the
    other way is how a README came to be evidence for eighteen criteria, and
    that failure was silent.
    """
    from factory.schemas import OracleAccount, TestFileAccount

    session = _authored([("tests/oracle/shared_setup.py", "x"),
                         ("tests/oracle/test_a.py", "assert 1")])
    account = OracleAccount(
        strategy="s",
        tests=[TestFileAccount(path="tests/oracle/shared_setup.py",
                               criterion_ids=["AC-1", "AC-2", "AC-3"]),
               TestFileAccount(path="tests/oracle/test_a.py", criterion_ids=["AC-1"])],
        support=["tests/oracle/shared_setup.py"],
    )
    suite = pipeline.assemble_oracle_suite(session, account)
    assert [t.path for t in suite.tests] == ["tests/oracle/test_a.py"]
    assert [f.path for f in suite.support] == ["tests/oracle/shared_setup.py"]
    assert len(pipeline.oracle_files(suite)) == 2


def test_a_support_file_named_by_its_basename_is_still_support():
    """The same tolerance the tags get, for the same reason.

    A support path that misses its match is not a harmless spelling mistake: the
    file lands back in `tests`, and if the account also tagged it there it is
    evidence again.
    """
    from factory.schemas import OracleAccount, TestFileAccount

    session = _authored([("tests/oracle/shared_setup.py", "x"),
                         ("tests/oracle/test_a.py", "assert 1")])
    account = OracleAccount(
        strategy="s",
        tests=[TestFileAccount(path="test_a.py", criterion_ids=["AC-1"])],
        support=["shared_setup.py"],
    )
    suite = pipeline.assemble_oracle_suite(session, account)
    assert [f.path for f in suite.support] == ["tests/oracle/shared_setup.py"]
    assert [t.path for t in suite.tests] == ["tests/oracle/test_a.py"]


def test_an_account_that_declares_no_support_is_read_exactly_as_before():
    """Nothing is reclassified on the model's behalf.

    The split is a declaration, and a model that makes none has declared
    nothing. Inferring one here -- from a filename, or from a tag set that looks
    too broad -- is the guess this design exists to avoid, and it would fire on
    old evidence nobody is watching.
    """
    from factory.schemas import OracleAccount, TestFileAccount

    session = _authored([("tests/oracle/README.md", "#"),
                         ("tests/oracle/test_a.py", "assert 1")])
    account = OracleAccount(
        strategy="s",
        tests=[TestFileAccount(path="tests/oracle/README.md", criterion_ids=["AC-1"]),
               TestFileAccount(path="tests/oracle/test_a.py", criterion_ids=["AC-1"])],
    )
    suite = pipeline.assemble_oracle_suite(session, account)
    assert len(suite.tests) == 2 and suite.support == []


def test_a_suite_of_nothing_but_scaffolding_reports_that_nothing_was_verified():
    """Counting files is what let this pass; counting what asserts is what says it.

    Three support files and no test used to look like a suite, because the three
    were in `tests` and carried tags. Now `check_blind_suite_missing` sees no
    asserting file and raises the blocker it exists for -- and names the
    scaffolding, so the reason is legible rather than looking like a lane that
    produced nothing at all.
    """
    from factory.schemas import OracleAccount

    session = _authored([("tests/oracle/shared_setup.py", "x"),
                         ("tests/oracle/README.md", "#")])
    account = OracleAccount(strategy="s", notes="I could not write a test.",
                            support=["tests/oracle/shared_setup.py",
                                     "tests/oracle/README.md"])
    suite = pipeline.assemble_oracle_suite(session, account)
    spec = Spec(title="t", intent="i", summary="s",
                acceptance_criteria=[AcceptanceCriterion(id="AC-1", statement="s")])
    assert not pipeline.usable_suite(suite, spec)
    findings = pipeline.check_blind_suite_missing(suite, spec)
    assert findings and findings[0].severity == "blocker"
    assert "shared_setup.py" in findings[0].evidence, \
        "the blocker must say what the lane did write, or it reads as a lane that died"


def test_a_suite_survives_an_accounting_call_that_failed():
    """The tests are already on disk. Losing them because the small answer that
    describes them failed would throw away work that is done."""
    session = _authored([("tests/oracle/test_a.py", "assert 1")])
    suite = pipeline.assemble_oracle_suite(session, None, note="the model timed out")
    assert len(suite.tests) == 1
    assert "timed out" in suite.notes


def test_a_probe_written_beside_an_edit_to_the_code_is_declared():
    """The breaker works in a checkout of what it attacks. A probe that only
    fails alongside such an edit is a finding about a repository nobody has --
    the edit dies with the worktree, the fact of it must not."""
    from factory.schemas import BreakerAccount, BreakerTestAccount

    session = _authored([("tests/breaker/test_race.py", "assert 0")],
                        outside=["backend/app/queue.py"])
    account = BreakerAccount(
        strategy="s",
        tests=[BreakerTestAccount(path="tests/breaker/test_race.py",
                                  hypothesis="two callers drop a row")],
    )
    suite = pipeline.assemble_breaker_suite(session, account)
    assert suite.tests[0].hypothesis == "two callers drop a row"
    assert "backend/app/queue.py" in suite.notes
    assert "discarded unread" in suite.notes


def test_a_probe_with_no_hypothesis_still_runs_and_says_why_it_is_bare():
    """An unexplained red test is worth less than an explained one and a great
    deal more than one silently dropped for lacking a caption."""
    session = _authored([("tests/breaker/test_x.py", "assert 0")])
    suite = pipeline.assemble_breaker_suite(session, None)
    assert len(suite.tests) == 1
    assert "gave no hypothesis" in suite.tests[0].hypothesis


def test_a_reading_says_which_questions_it_predates():
    """The version stamp, read the other way.

    It refused a write from code older than the record and had no answer at all
    for the reverse -- so a field added here was empty on every existing
    project, no detector noticed (`tooling_drift` watches the repository, and
    the repository had not moved), and the first sign was a re-survey proposing
    a change to a project that had not changed. That reads as drift, is not, and
    cost a model call to discover.
    """
    assert schemas.reading_behind(schemas.STATE_VERSION) == []
    behind = schemas.reading_behind(schemas.STATE_VERSION - 1)
    assert behind, "a version bump that named nothing it added"
    # Every bump names what it added, whichever bump is latest. Pinning one
    # field here would make this test a version behind on the next one -- which
    # it was, immediately, the first time the version moved after it was written.
    assert all(isinstance(b, str) and b.strip() for b in behind)
    assert schemas.reading_behind(1), "the oldest records must be told everything"
    # Every version in the table must be one this code understands, or the
    # sentence a human reads is about a field that does not exist.
    assert max(schemas.STATE_ADDITIONS) <= schemas.STATE_VERSION


def test_a_build_says_so_when_nothing_told_the_oracle_the_conventions():
    """The durable half. `reading_behind` goes quiet the moment a record is
    written -- including by a re-survey somebody declined -- so the field itself
    has to be read at build time, or an unanswered question becomes silence."""
    usable = schemas.TestingTier(tier="user", runner="vitest", verdict="usable")
    surface = schemas.TestingSurface(tiers=[usable])
    finding = pipeline.check_testing_conventions(surface)
    assert finding and finding[0].severity == "major"
    assert "import_examples" in finding[0].evidence

    usable.import_examples = ["import { Wordmark } from './Wordmark'"]
    assert pipeline.check_testing_conventions(surface) == []

    # A tier nobody can write a test against is not a missing convention.
    absent = schemas.TestingSurface(tiers=[
        schemas.TestingTier(tier="user", runner="", verdict="absent")])
    assert pipeline.check_testing_conventions(absent) == []


def test_the_oracle_is_shown_how_this_repository_imports_its_own_code():
    """The one thing a blind author cannot derive and must not guess.

    A module path follows from the feature and the directory. Whether this
    project exports a page as a default or by name follows from nothing in a
    specification -- so the oracle guessed `import TrialDetail from ...` at a
    repository whose every page is `export function TrialDetail`, and the file
    imported nothing, collected nothing, and arrived as six criteria failing.

    Answered by copying lines out of tests that already pass here, which is a
    fact about how to reach the application and not about what it does -- the
    line `testing_context` has always drawn.
    """
    tier = schemas.TestingTier(
        tier="user", runner="vitest", verdict="usable", fixtures=["render"],
        import_examples=["import { Wordmark } from './Wordmark'"])
    rendered = workspace.testing_context(types.SimpleNamespace(tiers=[tier]))
    assert "import { Wordmark } from './Wordmark'" in rendered
    assert "default or by name" in rendered


def test_a_blind_file_already_in_a_proved_directory_is_left_there():
    """The extension gate routes a file that needs a home. It must not be asked
    about one that already has it.

    The oracle wrote `screeningFixtures.js` into the frontend blind directory,
    beside the two tests that import it. `.js` matched neither canary (`.py`,
    `.tsx`), so the support fallback parked it in the first placement -- a
    JavaScript module in the pytest directory. Both frontend blind tests then
    failed to transform on an import of a file moved out from under them, and
    four criteria were reported as failing on that evidence.

    The routing that must survive is the opposite case: a test whose extension
    belongs to another runner has to move, wherever it currently sits.
    """
    places = [
        schemas.BlindPlacement(kind="pytest", directory="backend/tests_blind",
                               filename="test_canary.py",
                               canary_passes="a", canary_fails="b"),
        schemas.BlindPlacement(kind="vitest", directory="frontend/src/__tests__",
                               filename="canary.test.tsx",
                               canary_passes="a", canary_fails="b"),
    ]
    nest = ["backend/tests_blind", "frontend/src/__tests__"]
    place = lambda path: pipeline.place_blind_file(places, path, "f1", nest)

    # An extension no placement claims, already in a proved directory: kept.
    assert place("frontend/src/__tests__/f1/screeningFixtures.js") == \
        "frontend/src/__tests__/f1/screeningFixtures.js"
    # And still kept on the other side, so this is not a frontend special case.
    assert place("backend/tests_blind/f1/notes.txt") == "backend/tests_blind/f1/notes.txt"
    # A test in the wrong runner's directory still moves -- nothing collects a
    # .tsx under pytest, and leaving it there is the failure this routing exists
    # to prevent.
    assert place("backend/tests_blind/f1/A.test.tsx") == "frontend/src/__tests__/f1/A.test.tsx"
    # A file in no proved directory is still unplaceable, so the caller's
    # support fallback decides rather than this function guessing.
    assert place("docs/notes.md") == ""


def test_the_oracle_writes_where_its_files_will_actually_run():
    """A relative path is only correct relative to something.

    The oracle was told to write under `tests/oracle/` and did. It wrote
    `../../frontend/src/pages/TrialDetail`, which from `tests/oracle/` is the
    repository root and is exactly right. The file was then relocated four
    levels deeper into the directory it actually runs in, and that correct
    import resolved to nothing: no tests collected, six criteria reported to a
    human as failing, and a review agent filing the innocent components as a
    blocker.

    No prompt fixes that, because the prompt was not wrong. So the move is gone:
    the destination is computed first, the oracle is told it, and what it writes
    is what ships.
    """
    placements = [
        schemas.BlindPlacement(kind="pytest", directory="backend/tests_blind",
                               filename="test_canary.py",
                               canary_passes="def test_ok(): assert True",
                               canary_fails="def test_no(): assert False"),
        schemas.BlindPlacement(kind="vitest", directory="frontend/src/__tests__",
                               filename="canary.test.tsx",
                               canary_passes="it('ok', () => {})",
                               canary_fails="it('no', () => { throw 1 })"),
    ]
    nestable = ["backend/tests_blind", "frontend/src/__tests__"]
    targets = pipeline.blind_write_targets(placements, "feat-1", nestable)
    # The feature's folder is named so a test inside it can import a helper
    # beside it: `feat-1` cannot be a package path, and an oracle that worked
    # around that by writing its helpers elsewhere had them discarded.
    assert dict(targets) == {
        ".py": "backend/tests_blind/feat_1",
        ".tsx": "frontend/src/__tests__/feat_1",
    }

    # Nothing moves afterwards: the relocator returns these paths untouched.
    for suffix, where in targets:
        written = f"{where}/x{suffix}"
        assert pipeline.place_blind_file(
            placements, written, "feat-1", nestable) == written, (
            f"{written} was still relocated, so its relative imports still break")

    # And the prefix the oracle is handed actually reaches the repository root.
    import posixpath
    where = dict(targets)[".tsx"]
    up = "../" * len(where.split("/"))
    assert posixpath.normpath(
        posixpath.join(where, up + "frontend/src/pages/TrialDetail")
    ) == "frontend/src/pages/TrialDetail"


def test_a_completion_runs_through_a_cli_route(tmp_path):
    """The whole point: an agent that returns a schema, served by a subscription
    CLI instead of an HTTP endpoint. `Opus 5 to plan` is this test."""
    import asyncio
    from factory.schemas import ScoutReport

    llm = _routed(tmp_path)
    out = asyncio.run(llm.ask("scout", "look around", ScoutReport, system="# scout"))
    assert isinstance(out, ScoutReport) and out.summary == "ok"


def test_the_cli_is_handed_the_model_the_schema_and_the_role_prompt(tmp_path):
    """Each of these is a contract. A missing schema loses the answer's shape, a
    missing system prompt loses the role, and a missing model runs the wrong
    one -- and none of the three fails loudly."""
    import json as _json
    from factory.llm import cli_argv, strict_schema
    from factory.schemas import ScoutReport

    route = _cli_route(tmp_path)
    schema = _json.dumps(strict_schema(ScoutReport))
    argv = cli_argv(route, "opus-5", "# scout contract", schema)

    assert argv[argv.index("--model") + 1] == "opus-5"
    assert argv[argv.index("--append-system-prompt") + 1] == "# scout contract"
    assert _json.loads(argv[argv.index("--json-schema") + 1])["properties"]["summary"]
    # Tools off. Several roles are defined by not having them: the review panel
    # gets "one structured completion over a text bundle and no tools", and the
    # spec writer's whole relationship to the repository is the digest it was given.
    assert argv[argv.index("--tools") + 1] == ""


def test_the_repair_loop_works_over_a_cli_route(tmp_path):
    """One repair loop, both transports. The CLI branch returns the same shape
    the HTTP branch does, which is what lets `ask` stay one function."""
    import asyncio
    from factory.schemas import ScoutReport

    llm = _routed(tmp_path, mode="bad-then-good")
    out = asyncio.run(llm.ask("scout", "look around", ScoutReport))
    assert out.summary == "ok", "the second attempt must carry the first answer back"


def test_an_unbilled_route_is_recorded_as_unbilled_not_as_free(tmp_path):
    """A subscription reports no dollars. Booked as `cost: 0.0` and nothing
    else, it is indistinguishable in the ledger from an agent that never ran --
    and a human reads $0.00 as free."""
    import asyncio
    from factory.schemas import ScoutReport

    llm = _routed(tmp_path)
    asyncio.run(llm.ask("scout", "look around", ScoutReport))
    scout = llm.usage_report()["roles"]["scout"]
    assert scout["cost"] == 0.0
    assert scout["turns"] == 2 and scout["unbilled_calls"] == 1
    assert scout["routes"] == ["claude-code"]


def test_a_route_that_cannot_be_told_a_schema_gets_it_in_the_prompt(tmp_path):
    """Gemini's CLI has --output-format json and no schema flag. The answer is
    the prompt plus the repair loop, not a branch per vendor."""
    import asyncio
    from factory.schemas import ScoutReport

    llm = _routed(tmp_path, schema_in_prompt=True)
    out = asyncio.run(llm._run_cli(
        llm.config.route_for("scout"), llm.config.role("scout"),
        [{"role": "user", "content": "look around"}], ScoutReport))
    echoed = json.loads(out["choices"][0]["message"]["content"])
    assert echoed  # it answered
    # And the flag went away rather than being passed empty.
    assert out["usage"]["prompt_tokens"] == 9


def test_a_schema_answer_is_read_from_where_the_tool_puts_it(tmp_path):
    """One tool returns the agent's prose in `result` and the structured object
    beside it, so reading `result` for a schema call gets a sentence about the
    answer rather than the answer."""
    import asyncio
    from factory.schemas import ScoutReport

    script = tmp_path / "cli.py"
    script.write_text(
        "import json,sys;sys.stdin.read();print(json.dumps({"
        "'result': \"I have provided the structured output as requested.\","
        "'is_error': False,"
        "'structured_output': {'summary':'from the right field','stack':[],"
        "'conventions':[],'existing_capabilities':[],'do_not_duplicate':[],"
        "'relevant_files':[],'risks':[]}}))", encoding="utf-8")
    llm = _routed(tmp_path)
    route = llm.config.route("claude-code")
    route.command = [sys.executable, str(script)]
    route.schema_result_key = "structured_output"

    out = asyncio.run(llm.ask("scout", "look", ScoutReport))
    assert out.summary == "from the right field", \
        "the prose in `result` was read as the answer"


def test_a_subscription_cost_is_reported_but_never_spent(tmp_path):
    """`claude -p` returns `total_cost_usd` on a subscription -- $0.0010305 for
    a two-token question. That is what the same work would have cost on an API,
    not what anyone was charged.

    Added to the ledger's `cost` it would count against `budget_usd` and stop a
    run at the reserve floor for spend that never happened, while the thing that
    can actually stop that run -- a rate limit -- stays invisible.
    """
    import asyncio
    from factory.schemas import ScoutReport

    script = tmp_path / "cli.py"
    script.write_text(
        "import json,sys;sys.stdin.read();print(json.dumps({"
        "'result': json.dumps({'summary':'ok','stack':[],'conventions':[],"
        "'existing_capabilities':[],'do_not_duplicate':[],'relevant_files':[],'risks':[]}),"
        "'is_error': False, 'total_cost_usd': 0.0010305, 'num_turns': 1}))",
        encoding="utf-8")
    llm = _routed(tmp_path)
    llm.config.route("claude-code").command = [sys.executable, str(script)]

    asyncio.run(llm.ask("scout", "look around", ScoutReport))
    report = llm.usage_report()
    assert report["total_cost"] == 0.0, \
        "notional spend reached the number the budget guard stops on"
    # Rounded to six places like every other figure in the ledger.
    assert report["notional_cost"] == pytest.approx(0.0010305, abs=1e-6), \
        "and it must not be thrown away either"
    assert report["unbilled_turns"] == 1
    assert report["roles"]["scout"]["notional_cost"] == pytest.approx(0.0010305, abs=1e-6)


def test_the_finding_reaches_the_packet_like_every_other_run_fact():
    """Wired to the live coverage, and restated rather than re-raised.

    Its title carries a count, and the count moves as roles run: added once
    per round, one run sent a human "the budget did not constrain 4 agent(s)"
    and "...7 agent(s)" as two separate things to rule on, with nothing to say
    which was current."""
    src = inspect.getsource(pipeline.Factory._rework_loop) \
        if hasattr(pipeline.Factory, "_rework_loop") else \
        factory_source("pipeline")
    wiring = 'ledger.note("computed", check_budget_coverage('
    assert wiring in src, (
        "added, it becomes one finding per round each with a different number; "
        "escalated, it queues a fact about a rolling window beside a defect")
    site = src[src.index(wiring):][:200]
    assert "self.llm.coverage()" in site, site


def test_a_stop_reason_naming_a_budget_admits_what_it_could_not_see():
    """The most misleading sentence the packet can carry is a stop reason about
    a budget, in a run that budget could not measure."""
    source = factory_source("pipeline")
    assert "budget cannot measure, so the figures above are not the whole cost" in source


def test_a_rate_limited_round_returns_its_findings_unattempted():
    """None of them was tried. Charged an attempt, a finding gets closer to the
    per-finding cap for work that never happened."""
    source = factory_source("pipeline")
    assert "except RateLimited as exc:" in source
    assert "The attempt was returned rather than charged." in source
    assert '"rework_rate_limited"' in source
    # And it must be caught before the generic handler, or it becomes one.
    assert source.index("except RateLimited as exc:") < source.index(
        "the repair loop stopped in round")


def test_the_rate_limit_stop_reason_says_nothing_is_misconfigured():
    source = factory_source("pipeline")
    assert "Nothing is " in source and "misconfigured and no budget was exceeded" in source
    assert "Dispatch again when it resets." in source


# --------------------------------------------------------------------------
# The two lanes fail differently
#
# They ran under one `asyncio.gather` that re-raised whichever failed, so a
# verify lane that died took a finished build lane with it -- units that had
# run for an hour, a branch, gates that could still have been run. The build
# lane is the thing being judged; the verify lane is one input to judging it.
# --------------------------------------------------------------------------


def test_a_failed_verify_lane_does_not_discard_the_build():
    src = inspect.getsource(pipeline.Factory._build_and_verify) \
        if hasattr(pipeline.Factory, "_build_and_verify") else \
        factory_source("pipeline")
    assert "if isinstance(build_result, BaseException):\n            raise build_result" in src, \
        "a dead build lane must still end the run: there is nothing to package"
    assert "if isinstance(verify_result, BaseException):" in src
    assert '"verify_lane_failed"' in src, "the failure must reach the record"
    # And it must not be re-raised anywhere after being handled.
    handled = src.index("if isinstance(verify_result, BaseException):")
    tail = src[handled:handled + 1200]
    assert "raise verify_result" not in tail


def test_a_missing_suite_is_a_blocker_not_twelve_quiet_rows():
    """`check_blind_tags` deliberately does not cover the empty case, so until
    now a failed verify lane produced no finding at all -- and a criterion table
    of unremarkable `no_test` rows reads as an oracle that looked and declined.
    Nothing looked."""
    from factory.schemas import AcceptanceCriterion, OracleSuite, Spec, TestFile

    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id=f"AC-{i}", statement="x") for i in range(1, 13)])

    findings = pipeline.check_blind_suite_missing(
        OracleSuite(strategy="", notes="RateLimited: the plan's window"), spec)
    assert len(findings) == 1
    assert findings[0].severity == "blocker"
    assert findings[0].category == "verification"
    assert "No independent tests were written" in findings[0].title
    assert "12 acceptance criteria" in findings[0].detail, "the count moved to the elaboration, not away"
    assert "RateLimited" in findings[0].detail, "the reason must travel with the finding"
    assert "unverified for that reason, and not for anything about the code" in \
        findings[0].detail

    # A suite that exists is not this check's business, whatever else is wrong.
    assert pipeline.check_blind_suite_missing(
        OracleSuite(strategy="s", tests=[
            TestFile(path="t.py", contents="x", criterion_ids=["AC-1"])]), spec) == []


def test_the_two_blind_suite_checks_do_not_overlap():
    """One fires when tests exist and name nothing; the other when there are no
    tests. Both firing on one run would say the same thing twice, in two
    severities, and a packet that contradicts itself is worse than either."""
    from factory.schemas import AcceptanceCriterion, OracleSuite, Spec, TestFile

    spec = Spec(title="t", intent="i", summary="s",
                acceptance_criteria=[AcceptanceCriterion(id="AC-1", statement="x")])
    untagged = OracleSuite(strategy="s", tests=[
        TestFile(path="t.py", contents="x", criterion_ids=[])])
    empty = OracleSuite(strategy="")

    assert pipeline.check_blind_tags(untagged, spec) and not \
        pipeline.check_blind_suite_missing(untagged, spec)
    assert pipeline.check_blind_suite_missing(empty, spec) and not \
        pipeline.check_blind_tags(empty, spec)


def test_the_missing_suite_finding_says_the_build_was_kept_on_purpose():
    """A reader must not conclude the build is gone. It was kept deliberately --
    that is the whole change -- and what is missing is the thing that would have
    told them whether it meets the contract."""
    from factory.schemas import OracleSuite, Spec

    finding = pipeline.check_blind_suite_missing(
        OracleSuite(strategy=""), Spec(title="t", intent="i", summary="s"))[0]
    assert "kept deliberately" in finding.recommendation
    assert "Rebuild rather than rule on this packet" in finding.recommendation


def test_the_version_of_a_file_shown_is_the_version_that_gets_written(tmp_path):
    """A card showed one thing and the button beside it wrote another.

    Scaffolding is keyed by path and was never updated: `apply_survey_diff`
    merged only paths it did not already have, and `apply_scaffolding` read the
    survey's copy. So a re-reading that proposed a NEW version of a file already
    on the list -- one line added to a conftest, which is the whole difference
    between a project's factories being reachable by name and not -- was
    rendered from the proposal and written from the survey.

    The bytes written matched what was already on disk. The write was correctly
    skipped as a no-op, and then a three-minute re-read ran over a repository
    nothing had changed in, which from the outside is a button that hangs.

    One resolver now answers "which version of this file is on offer", and the
    diff, the write and the acceptance all ask it.
    """
    from factory.projects import ProjectRegistry, proposed_scaffolding
    from factory.schemas import EnvironmentSpec, ProjectSurvey, ScaffoldFile, SurveyDiff

    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "conftest.py").write_text("old\n")

    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")
    project.state.survey = ProjectSurvey(
        name="demo", summary="s", environment=EnvironmentSpec(kind="host"),
        scaffolding=[ScaffoldFile(path="backend/conftest.py", contents="old\n", purpose="p")])
    registry.save(project)

    # A later reading proposes a new version of a file already on the list.
    newer = SurveyDiff(summary="s", scaffolding=[
        ScaffoldFile(path="backend/conftest.py", contents="old\nnew line\n", purpose="p")])
    project.store.append("resurvey", newer, role="resurvey")

    offered = proposed_scaffolding(project)
    assert offered["backend/conftest.py"].contents == "old\nnew line\n", (
        "the newest proposal does not win, so a revised file can never be written")

    written = registry.apply_scaffolding(
        project, ["backend/conftest.py"], replace=["backend/conftest.py"])
    assert written["written"] == ["backend/conftest.py"], (
        f"the write skipped as a no-op because it wrote the old version: {written}")
    assert (repo / "backend" / "conftest.py").read_text() == "old\nnew line\n"

    # And accepting the proposal revises the offer rather than dropping it.
    project.state.survey.scaffolding[0].contents = "old\n"
    registry.save(project)
    updated, applied = registry.apply_survey_diff(project, newer, ["scaffolding"])
    assert "scaffolding" in applied
    kept = {f.path: f.contents for f in updated.state.survey.scaffolding}
    assert kept["backend/conftest.py"] == "old\nnew line\n", (
        "accepting a revised file kept the old contents and said nothing")
    assert len(updated.state.survey.scaffolding) == 1, "the same path was listed twice"

    # A wait of minutes has to announce itself, and must not be held open by the
    # browser: a modal for eight minutes reads as a hang, a reader reloads, and
    # the reload cancelled the task -- so the reasonable response to it looking
    # stuck was the one thing that guaranteed it was.
    app = app_js()
    assert "phase: 'resurvey'" in app, (
        "a re-read after a write does not report itself as a run in progress")
    assert "you can leave this page" in app, (
        "nothing tells a reader the reading survives the page it was started from")
    server = factory_source("server")
    body = server.split("def scaffold_and_read(", 1)[1].split("\n    @app.", 1)[0]
    assert "background.add_task(read_again)" in body, (
        "the re-read is held open by the request, so a reload throws the work away")
    assert "await onboarding().run_resurvey" in body.split("async def read_again", 1)[1], (
        "the re-read no longer runs at all")


def test_a_dockerfile_is_read_as_instructions_and_not_as_prose():
    """A survey was rejected for the sentence explaining that it got it right.

    The rule is real: the sandbox worktree is mounted at the workdir, so a
    Dockerfile that bakes the source in has it shadowed at run time and the
    checks run against files nobody wrote. But the check read the whole file,
    comments included, and a Dockerfile that correctly does NOT copy the source
    and says why -- "the project's own image does `COPY . .`, which the mount
    would shadow anyway" -- tripped it on the explanation.

    In Dockerfile syntax a comment is a line whose first non-space character is
    `#`, and there is no inline comment, so dropping those lines is exact.

    The tool check is the same read in the opposite direction, and getting it
    wrong there is worse: a comment saying "there is no node in this image"
    would answer "is node installed" with yes, and let a gate through that can
    never run.
    """
    from factory.projects import ProjectRegistry
    from factory.schemas import EnvironmentSpec, Gate

    explains = EnvironmentSpec(kind="derive", dockerfile=(
        "FROM node:22-alpine\n"
        "# The project's own Dockerfile does `COPY . .`, which the sandbox mount\n"
        "# would shadow anyway, so this one does not.\n"
        "RUN apk add --no-cache git curl\n"
        "WORKDIR /app\n"))
    assert ProjectRegistry.environment_problems(explains, [], None) == [], (
        "a Dockerfile was rejected for a comment about what it deliberately does not do")

    actually = EnvironmentSpec(kind="derive", dockerfile=(
        "FROM node:22-alpine\nWORKDIR /app\nCOPY . .\n"))
    assert any("copies the source tree" in x
               for x in ProjectRegistry.environment_problems(actually, [], None)), (
        "a Dockerfile that really does bake the source in is no longer caught")

    # And a comment cannot vouch for a tool that is not there.
    lies = EnvironmentSpec(kind="derive", dockerfile=(
        "FROM alpine\n# no node in this image, on purpose\n"))
    problems = ProjectRegistry.environment_problems(
        lies, [Gate(name="lint", command="npm run lint")], None)
    assert problems, "a comment mentioning node satisfied the check that node is installed"


def test_a_check_you_turned_down_does_not_come_back(tmp_path):
    """A rejection was written to the ledger and read by nothing.

    So a check declined at gate 0 arrived again at the next reading -- the
    evidence that suggests it, a script in package.json or a step in CI, is
    still in the repository and always will be, so the proposal always will be
    too. And a full survey was worse than a re-survey: it replaces the gate list
    wholesale rather than diffing it, and walked past the decision entirely.

    Unlike a capability request, which a new feature can raise again with new
    evidence, a declined check never resurfaces on its own. It is a standing
    decision about this project, and only a human puts it back.
    """
    from factory.projects import ProjectError, ProjectRegistry
    from factory.schemas import EnvironmentSpec, Gate, ProjectSurvey, SurveyDiff
    from factory.schemas import ProposedGateChange

    repo = tmp_path / "repo"
    repo.mkdir()
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")
    project.state.gates = [Gate(name="lint", command="npm run lint --strict")]
    registry.save(project)

    project = registry.decline_gate(project, "lint", "ten pre-existing errors")
    assert [g.name for g in project.state.gates] == [], "the check is still on the list"
    ruling = registry.declined_gates(project)["lint"]
    assert ruling.reason == "ten pre-existing errors", "the reason was not kept"
    assert ruling.command == "npm run lint --strict", (
        "the command was not kept, so putting it back would hand over a name and nothing to run")

    # A full survey proposing it again must not reinstate it. This is the path
    # that used to have nothing guarding it at all.
    survey = ProjectSurvey(
        name="demo", summary="s", environment=EnvironmentSpec(kind="host"),
        gates=[Gate(name="lint", command="npm run lint"),
               Gate(name="types", command="tsc --noEmit")])
    project = registry.record_survey(project, survey)
    assert [g.name for g in project.state.gates] == ["types"], (
        "a full survey put back a check the project had declined")

    # Nor may a re-survey, however the human ticks the box.
    diff = SurveyDiff(summary="s", gate_changes=[
        ProposedGateChange(action="add", name="lint", reason="package.json declares it",
                           evidence="package.json line 8",
                           gate=Gate(name="lint", command="npm run lint"))])
    project, applied = registry.apply_survey_diff(project, diff, ["add:lint"])
    assert "add:lint" not in applied, "accepting a proposal re-added a declined check"
    assert [g.name for g in project.state.gates] == ["types"]

    # And it is visible, with a way back that restores what it ran.
    project = registry.reinstate_gate(project, "lint")
    assert [g.name for g in project.state.gates] == ["types", "lint"]
    assert [g.command for g in project.state.gates if g.name == "lint"] == [
        "npm run lint --strict"], "reinstating rebuilt the command instead of restoring it"
    assert registry.declined_gates(project) == {}, "the ruling outlived the reinstatement"

    with pytest.raises(ProjectError):
        registry.reinstate_gate(project, "never-declined")

    # The console draws it, and asks for the reason it has always claimed to keep.
    app = app_js()
    assert "function declinedChecks(" in app, (
        "a decision filtered out of every reading is invisible and cannot be revisited")
    assert 'data-reinstate-gate' in app, "there is no way back"
    assert 'id="drop-reason"' in app, (
        "this page has promised in three places that declining leaves the reason readable, "
        "and still never asks for one")

    # The reading is told, so a paid call is not spent on a proposal that dies.
    onboarding = factory_source("onboarding")
    assert "def _declined_checks(" in onboarding
    assert "Checks this project has turned down" in onboarding, (
        "the re-survey prompt never mentions what was declined")

def test_a_suggestion_taken_becomes_the_check_it_named(tmp_path):
    """`would_gate` was read on the way out and never on the way in.

    "The check command this would make possible" was used for three things: the
    dedup key, a field copied onto the ruling when a human said no, and a log
    line about declined suggestions. Nothing turned it into a check -- so a
    project could run the whole pipeline, merge a Playwright suite, and still
    not be measured against `npx playwright test`. The only path that touched
    the field at all was the one where the answer was no.
    """
    from factory.projects import ProjectError, ProjectRegistry
    from factory.schemas import EnvironmentSpec, Gate, ProjectSurvey, Recommendation

    repo = tmp_path / "repo"
    repo.mkdir()
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")
    project.state.gates = [Gate(name="lint", command="ruff check .")]
    # On the project, not only on the survey: `environment_problems` reads it
    # from there, and the runnability rule below is the whole reason this call
    # is worth making.
    project.state.environment = EnvironmentSpec(kind="host")
    project.state.survey = ProjectSurvey(
        name="demo", summary="s", environment=EnvironmentSpec(kind="host"),
        recommendations=[
            Recommendation(title="Pin the Python dependencies with a lockfile", kind="build",
                           why="w", evidence="e", how="h", would_gate="uv lock --check"),
            Recommendation(title="Audit dependencies", kind="security", why="w", evidence="e",
                           how="h", would_gate="npm audit --audit-level=high, and pip-audit"),
            Recommendation(title="Write the docs somebody asked for", kind="other",
                           why="w", evidence="e", how="h"),
        ])
    registry.save(project)
    project.state.stage = "ready"
    project = registry.save(project)

    project = registry.adopt_recommendation(project, "uv lock --check", "build")
    added = [g for g in project.state.gates if g.name == "build"]
    assert added and added[0].command == "uv lock --check", \
        "the suggestion's own command is what goes on the list"
    assert len(project.state.gates) == 2, "the checks already there are untouched"

    # Editing a check list is what invalidates a baseline, and adopting is
    # editing one. The new check is measured on untouched code with the rest,
    # where a red one gets the three ways out every red check gets -- so this
    # cannot make a project quietly un-approvable.
    assert project.state.stage == "awaiting_approval"
    assert project.state.baseline is None

    # The command is a default, not a fact. The schema asks for one command and
    # a reading writes two joined by the word "and", so what the human confirms
    # is what is added -- and the ledger keeps both, including that it changed.
    project = registry.adopt_recommendation(
        project, "npm audit --audit-level=high, and pip-audit", "audit", "pip-audit")
    adopted = [g for g in project.state.gates if g.name == "audit"][0]
    assert adopted.command == "pip-audit"
    record = [r for r in project.store if r["kind"] == "recommendation_adopted"][-1]
    assert record["payload"]["edited"] is True
    assert record["payload"]["would_gate"] == "npm audit --audit-level=high, and pip-audit", \
        "what the reading proposed is not recoverable from what was added"

    # Adopted is answered. All four on one project stayed in the suggestion
    # list after their checks were on it, under a Build it button offering to
    # build work the project was by then measuring itself against.
    assert "uv lock --check" not in [
        registry.recommendation_key(r) for r in registry.live_recommendations(project)], \
        "a suggestion already adopted is still being made"

    # Against the list as it stands. Take the check off and the suggestion is
    # live again, because the repository still does not do it.
    without = [g for g in project.state.gates if g.name != "build"]
    project = registry.update(project, {"gates": without})
    assert "uv lock --check" in [
        registry.recommendation_key(r) for r in registry.live_recommendations(project)], \
        "removing the check left the suggestion suppressed by a record of adopting it"
    project = registry.adopt_recommendation(project, "uv lock --check", "build")

    # A reading writes English where the schema asks for a command, and a
    # prefilled field reads as an endorsement. Both shapes it actually produced
    # are refused rather than turned into a check nobody can make green.
    with pytest.raises(ProjectError) as prose:
        registry.adopt_recommendation(
            project, "Write the docs somebody asked for", "both",
            "npm audit --audit-level=high, and pip-audit")
    assert "two commands joined by the word 'and'" in str(prose.value)

    with pytest.raises(ProjectError) as unparseable:
        registry.adopt_recommendation(
            project, "Write the docs somebody asked for", "cov",
            "pytest --cov=app (with a floor on the reported percentage)")
    assert "cannot parse" in str(unparseable.value)

    # A suggestion naming no command has nothing to add, and says so.
    with pytest.raises(ProjectError) as no_command:
        registry.adopt_recommendation(project, "Write the docs somebody asked for", "docs")
    assert "names no command" in str(no_command.value)

    # A name already on the list would silently shadow a check, so it is refused
    # rather than merged.
    with pytest.raises(ProjectError) as clash:
        registry.adopt_recommendation(
            project, "Write the docs somebody asked for", "lint", "make docs")
    assert "already has a check called 'lint'" in str(clash.value)

    # And the check it would add is held to what gate 0 holds every other check
    # to. An adopted check carries no metric, so the rule that bites is the one
    # about whether this environment can run the command at all: a check for a
    # tool nothing installs is red forever and reads as a code failure.
    project.state.survey.recommendations.append(Recommendation(
        title="Drive it in a browser", kind="tests", why="w", evidence="e", how="h",
        would_gate="npx playwright test"))
    project = registry.save(project)
    with pytest.raises(ProjectError) as unrunnable:
        registry.adopt_recommendation(project, "npx playwright test", "e2e")
    assert "nothing in this environment installs" in str(unrunnable.value)
    assert not [g for g in project.state.gates if g.name == "e2e"], \
        "a check that cannot run was added anyway"


def test_a_suggestion_you_turned_down_is_not_made_again(tmp_path):
    """The third place this project met the same loop.

    A reading writes its recommendations fresh on every run, from repository
    facts that do not change -- a script in package.json, a directory with no
    tests in it -- so the same three arrived at every reading forever, and
    nothing recorded that a human had already said no.

    Keyed by the command the suggestion would create, not by its title. `npm run
    typecheck` comes from the repository and survives being described
    differently; "Install TypeScript and @types/node so the typecheck script the
    project already declares can actually run" is prose a reading rewrites every
    time. A title is the fallback for a suggestion that names no command, and it
    is the honest best available rather than a good answer.
    """
    from factory.projects import ProjectError, ProjectRegistry
    from factory.schemas import EnvironmentSpec, ProjectSurvey, Recommendation

    repo = tmp_path / "repo"
    repo.mkdir()
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")
    project.state.survey = ProjectSurvey(
        name="demo", summary="s", environment=EnvironmentSpec(kind="host"),
        recommendations=[
            Recommendation(title="Install TypeScript so the declared typecheck runs",
                           kind="types", why="w", evidence="e", how="h",
                           would_gate="npm run typecheck"),
            Recommendation(title="Adopt the runner that ships with Node", kind="tests",
                           why="w", evidence="e", how="h", would_gate="npm test"),
        ])
    registry.save(project)

    assert [registry.recommendation_key(r) for r in registry.live_recommendations(project)] == [
        "npm run typecheck", "npm test"], "the key is not the command it would create"

    project = registry.decline_recommendation(
        project, "npm run typecheck", "no type checking on this project")
    assert [registry.recommendation_key(r) for r in registry.live_recommendations(project)] == [
        "npm test"], "a suggestion turned down is still being made"
    ruling = project.state.recommendation_rulings[0]
    assert ruling.reason == "no type checking on this project"
    assert ruling.title, "nothing to draw in the declined list, so it is unfindable"

    # The same suggestion, reworded by a later reading, is still the same one.
    reworded = ProjectSurvey(
        name="demo", summary="s", environment=EnvironmentSpec(kind="host"),
        recommendations=[
            Recommendation(title="Add TypeScript and a tsconfig, so the declared check works",
                           kind="types", why="w", evidence="e", how="h",
                           would_gate="npm run typecheck")])
    project = registry.record_survey(project, reworded)
    assert registry.live_recommendations(project) == [], (
        "a reworded title got past the ruling, which is why the key is the command")

    project = registry.reinstate_recommendation(project, "npm run typecheck")
    assert len(registry.live_recommendations(project)) == 1, (
        "letting one back does not let it be suggested again")
    with pytest.raises(ProjectError):
        registry.reinstate_recommendation(project, "never-declined")

    # One implementation of "which suggestion is this". Two would disagree
    # silently: a card that will not decline, or one that comes back anyway.
    app = app_js()
    assert "function liveRecommendations() { return ((state.project || {}).recommendations)" in app, (
        "the page re-derives which suggestions are live instead of being served them")
    assert "would_gate" not in app.split("function liveRecommendations", 1)[1].split("\n", 1)[0], (
        "the key is being computed on the page as well as on the server")
    assert "data-decline-rec" in app and "data-reinstate-rec" in app, "no way to decline, or back"
    assert 'id="rec-reason"' in app, "declined without asking why, which makes the list unreadable"
    assert "function declinedRecommendations(" in app, (
        "turned-down suggestions are filtered out of every reading and drawn nowhere")

    onboarding = factory_source("onboarding")
    assert "def _declined_suggestions(" in onboarding, (
        "a paid reading is not told, so it writes the card again for nobody to see")


def test_a_rendered_component_is_a_unit_test_of_the_front_end():
    """The two halves of the rubric disagreed, and the schema was winning.

    `TestingTier.tier` defined `user` as "what a person sees and does, through a
    browser OR A RENDERED COMPONENT", while `surveyor.md` beside it said a
    rendered-component test "cannot answer 'can a user reach the page, see the
    badge, and still use every control on it' -- and criteria are written about
    the second". The field description is what reaches the model as its output
    contract, so a project with vitest, jsdom and no browser at all was read as
    having a `usable` user tier.

    The cost is not cosmetic and nothing downstream catches it. `verified_at`
    routes a criterion to a tier; a tier claiming `usable` tells the oracle it
    may write there; and a component test hands back "verified" for "a user
    switches workspace and sees the boards" having mocked away routing, the
    network, the API contract and the data. It can pass in full while the
    application is broken.
    """
    from factory.schemas import AcceptanceCriterion, TestingTier

    tier = TestingTier.model_fields["tier"].description
    assert "browser or a rendered component" not in tier, \
        "the user tier still admits a rendered component as one of its two ways"
    unit, user = tier.lower().split("user:", 1)
    assert "component" in unit, "a rendered component is not placed at the unit tier"
    assert "browser" in user, "the user tier no longer requires driving the application"
    assert "absent" in user, "it does not say what a project with no browser should report"

    # And the criterion field that routes work to a tier says the same thing,
    # so a spec cannot send a browser-level requirement somewhere jsdom can
    # claim it.
    where = AcceptanceCriterion.model_fields["verified_at"].description.lower()
    assert "not `user`" in where or "not user" in where, \
        "a criterion can still be routed to `user` and met by a component test"

    # And the prompt says it once, for both readings. This rule lived in three
    # places -- the surveyor's tier list, its scaffolding section, and the
    # re-survey's own file -- and a correction to one of them left a re-survey
    # marking a `user` tier `usable` on jsdom while the schema in front of it
    # said otherwise. There is one file now, and one statement of the rule in
    # it: a second copy is how this came back the first time.
    prompt = (ROOT / "factory" / "roles" / "surveyor.md").read_text()
    assert not (ROOT / "factory" / "roles" / "resurvey.md").exists(), \
        "a second prompt is back, and with it a second place for this rule to be wrong"
    assert prompt.count("unit test of the front end") == 1, \
        "the rule is stated more than once, which is how it drifted before"
    assert "`absent`, not `usable`" in prompt, \
        "the prompt does not say what to report when nothing can drive the app"


def test_a_gate_that_never_started_is_not_a_failing_gate():
    """The distinction that cost an hour of agents repairing a full disk.

    Six gates came back red because the shared preparation step died on a
    `DiskFullError` before any gate command was launched. Every one of them
    recorded `started=False` at the time -- the signal was there and nothing
    asked. The run read them as failures, opened a repair round, and spent five
    of them producing comment-only diffs against checks that had never run.
    """
    started_and_failed = schemas.GateReport(results=[
        schemas.GateResult(name="a", started=True, passed=False),
        schemas.GateResult(name="b", started=True, passed=True),
    ])
    assert not started_and_failed.blocked, (
        "a gate that ran and failed measured something; that is a fact about the code")

    nothing_ran = schemas.GateReport(results=[
        schemas.GateResult(name="a", started=False, passed=False),
        schemas.GateResult(name="b", started=False, passed=False),
    ])
    assert nothing_ran.blocked, "no gate started, so nothing was measured"

    # One survivor is enough to have learned something, so the line goes on.
    one_ran = schemas.GateReport(results=[
        schemas.GateResult(name="a", started=False, passed=False),
        schemas.GateResult(name="b", started=True, passed=False),
    ])
    assert not one_ran.blocked

    # A skipped gate is a choice, not a casualty, and must not make a healthy
    # report look blocked.
    only_skips = schemas.GateReport(results=[
        schemas.GateResult(name="a", skipped=True, started=False),
    ])
    assert not only_skips.blocked
    assert not schemas.GateReport(results=[]).blocked


def test_a_blocked_harness_stops_the_line_before_a_repair_round_opens():
    """Nothing downstream of the gates can act on a measurement that was never
    taken, so the run must stop at the gates rather than at the packet."""
    source = factory_source("pipeline")
    assert "if gates.blocked:" in source, "the run never asks whether anything ran"

    loop = source.split("gates, breaker_tests, breaker_report = await self._assess(", 1)[1]
    stop = loop.index("if gates.blocked:")
    assert stop < loop.index("compute_trace("), (
        "the line must stop before the trace, the panel and the repair round are built "
        "on top of gates that never executed")

    reason = pipeline._blocked_reason(schemas.GateReport(results=[
        schemas.GateResult(
            name="backend-tests", started=False,
            output_tail="$ seed\n[exit 1]\nDiskFullError\n"
                        "[blocked] preparation step 2 of 2 failed, so nothing was run: `seed`"),
    ]))
    assert "preparation step 2 of 2 failed" in reason, (
        "the operator is told what the runner said, not a paraphrase of it")


def test_a_finding_the_exit_panel_raised_is_repairable_input_to_that_rule():
    """The rule is only worth having if what the panel raises reaches it. This
    is the shape of the record that was dropped: first seen in the last round,
    routed for repair, never attempted, not a restatement of anything.
    """
    ledger = pipeline.FindingLedger()
    late = schemas.Finding(
        id="hacker-9", title="Portal bearer token is persisted as the request actor",
        severity="blocker", category="security", detail="", evidence="", recommendation="")
    ledger.add("hacker", [late], 1, keep_ids=True)
    ledger.dispose([schemas.FindingDisposition(
        finding_id="hacker-9", disposition="repair", reason="the token is a credential")])

    assert "hacker-9" in ledger.repairable(2), (
        "a blocker raised on the way out must reach the rule that decides whether "
        "the loop goes round again")
    assert _reopen(targets=ledger.repairable(2)) == ["hacker-9"]

    # And the outcome the old control flow produced, so the phrase stays findable.
    ledger.finalise(stopped_early=True)
    record = ledger.records["hacker-9"]
    assert record.outcome == "unattempted"
    assert "the loop stopped first" in record.outcome_evidence


def test_the_exit_panel_does_not_run_into_a_queue_it_cannot_drain():
    """`reopen_after_panel` covers the case where there is road left. This is
    the other one: a real ceiling, and work already routed that this run
    never reached. A panel there can do exactly one thing -- make that queue
    longer -- for four agents' worth of money, and the next dispatch opens by
    paying for the same sweep over the same code.

    card-tags-8e21a7 did it three times. Seventeen findings, each filed
    "routed for repair and never attempted: the loop stopped first"."""
    source = inspect.getsource(pipeline.Factory._converge)
    line = [ln for ln in source.splitlines() if ln.strip().startswith("panel_now =")]
    assert line, "the exit panel's condition moved; this test is now blind"
    assert "backlog" in line[0], (
        "the exit panel still runs with work queued that the run could not reach")

    # And the condition itself: first pass always, on the way out only when
    # the way out is clear.
    def panel(round_index, forced_final, backlog):
        return round_index == 0 or (forced_final and not backlog)

    assert panel(0, False, ["x"]), "the first pass always looks"
    assert panel(2, True, []), "converged clean: the exit sweep is what it is for"
    assert not panel(2, True, ["x"]), "stopping with a backlog must not add to it"
    assert not panel(1, False, []), "in between, the aimed re-check does the looking"


def test_the_loop_only_breaks_when_the_panel_reopened_nothing():
    """The bug was structural: the exit panel ran, routed what it found, and
    then `break` regardless. The break has to sit under that answer."""
    source = inspect.getsource(pipeline.Factory._converge)
    assert "reopen_after_panel(" in source, "the exit panel's findings go nowhere"

    after = source.split("reopen_after_panel(", 1)[1]
    assert after.index("if reopened:") < after.index("break"), (
        "the loop still breaks before asking whether the panel gave it work")
    assert "targets = reopened" in after, (
        "reopening must hand the panel's findings to the round that follows")
    assert 'stop_reason = ""' in after, (
        "a run that goes round again must not also report that it converged")


def test_a_build_refuses_to_start_when_the_agents_would_have_nowhere_to_run():
    """Checked before a phase spends anything, because everything the packet
    could say about a blind run is worth less than not making one."""
    import inspect
    from factory import pipeline

    source = inspect.getsource(pipeline.Factory.require_authoring_environment)
    assert "host" in source and "require_unit_container" in source
    run_build = inspect.getsource(pipeline.Factory.run_build)
    assert "require_authoring_environment" in run_build, \
        "the check has to run before _build, not with the packet"
    assert inspect.iscoroutinefunction(pipeline.Factory.require_authoring_environment)


def test_a_project_with_no_container_cannot_start_a_build(tmp_path):
    """The check that would have saved the run this all came from: refused in
    seconds, before a phase spends anything, rather than reported with the
    packet ninety minutes later."""
    import asyncio
    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.pipeline import Factory
    from factory.projects import ProjectError
    from factory.schemas import EnvironmentSpec

    class _Project:
        def __init__(self, spec):
            self.environment = spec

    class _Stub:
        """Only what the check reads."""
        def __init__(self, spec):
            self.config = load_config()
            self.project = _Project(spec)
            self.executor = CommandExecutor(llm=None, config=self.config)

        harness_authoring = Factory.harness_authoring
        require_authoring_environment = Factory.require_authoring_environment

    # No environment at all.
    with pytest.raises(ProjectError) as caught:
        asyncio.run(_Stub(None).require_authoring_environment())
    assert "no environment" in str(caught.value)

    # An environment that is explicitly the host.
    host = _Stub(EnvironmentSpec(kind="host", workdir="/work"))
    with pytest.raises(ProjectError) as caught:
        asyncio.run(host.require_authoring_environment())
    assert "host" in str(caught.value)

    # And there is no way out any more: the setting that was one is refused.
    assert "require_unit_container" not in type(load_config().pipeline).model_fields
    example = (ROOT / "factory.example.yaml").read_text()
    written = tmp_path / "factory.yaml"
    written.write_text(example.replace(
        "\npipeline:\n", "\npipeline:\n  require_unit_container: false\n", 1))
    with pytest.raises(config_module.ConfigError, match="pipeline.require_unit_container"):
        load_config(written)


def test_editing_a_project_does_not_unapprove_it_under_a_running_build(tmp_path):
    """The baseline goes -- it measured a gate list against an environment and
    one of those is now something else. The approval stays while features are
    live: they are already building against this configuration, and a project
    mid-gate-0 has no bench, so un-approving hides the running build from the
    console. `run_baseline` has held this rule since the same thing happened
    there; the edit path did not, and one environment fix during a build hid
    the build."""
    from factory.projects import ProjectRegistry
    from factory.schemas import EnvironmentSpec, FeatureState

    registry = ProjectRegistry(tmp_path / "evidence")
    repo = tmp_path / "repo"
    repo.mkdir()
    project = registry.create(repo, "demo")
    project.state.environment = EnvironmentSpec(
        kind="derive", dockerfile="FROM scratch\n", workdir="/work")
    project.state.stage = "ready"
    project = registry.save(project, note="ready")

    # A feature mid-build, which is what makes the difference.
    store = project.feature_store("live-feature")
    store.append("state", FeatureState(
        feature_id="live-feature", project_id="demo", stage="building",
    ).model_dump(mode="json"), role="orchestrator", spec_hash="")
    assert registry.live_feature_ids(project) == ["live-feature"]

    edited = registry.update(project, {"environment": EnvironmentSpec(
        kind="derive", dockerfile="FROM scratch\nRUN true\n", workdir="/work")})
    assert edited.state.baseline is None, "the measurement is of something that no longer exists"
    assert edited.state.stage == "ready", \
        "un-approving underneath a live feature describes a world that does not exist"


def test_a_probe_suite_that_cannot_load_proves_nothing_passed():
    """Silence is not success. A suite that exited non-zero and named no probe
    knows which probes passed exactly as well as one that never ran.

    Measured: a repair renamed the model class every probe imports. Eleven
    probes stopped loading, the phase line read "11 probe(s), 0 failing", the
    no-regression check compared against "everything passed" and kept the
    round, and the packet told a human the probes were clean."""
    from factory.pipeline import breaker_detail, probes_proven_passing
    from factory.schemas import BreakerReport, BreakerTest

    tests = [BreakerTest(path=f"tests/breaker/p{i}.py", hypothesis="h", contents="x")
             for i in range(3)]

    broke = BreakerReport(ran=True, exit_code=2, failing=[])
    assert probes_proven_passing(tests, broke) == set(), \
        "an unattributable non-zero exit cannot claim any probe passed"
    assert "none of them proved anything" in breaker_detail(tests, broke)
    assert "0 failing" not in breaker_detail(tests, broke)

    # The ordinary cases still read as before.
    clean = BreakerReport(ran=True, exit_code=0, failing=[])
    assert probes_proven_passing(tests, clean) == {t.path for t in tests}
    assert breaker_detail(tests, clean) == "3 probe(s), 0 failing"

    named = BreakerReport(ran=True, exit_code=1, failing=["tests/breaker/p1.py"])
    assert probes_proven_passing(tests, named) == {
        "tests/breaker/p0.py", "tests/breaker/p2.py"}
    assert breaker_detail(tests, named) == "3 probe(s), 1 failing"

    never = BreakerReport(ran=False, note="the runner would not start")
    assert probes_proven_passing(tests, never) == set()
    assert breaker_detail(tests, never) == "the runner would not start"


def test_a_run_wide_fact_is_restated_rather_than_raised_twice():
    """The fallback check ran in round 0, when one role had run out of its
    route. By the packet, four had -- twenty-five calls, the whole verify lane
    on a substitute model -- and the human was told "1 agent(s)". Re-adding
    cannot fix it: the count is in the title, and a new title is a new
    finding."""
    from factory.pipeline import FindingLedger, check_route_fallbacks

    ledger = FindingLedger()
    early = [{"role": "breaker", "from": "codex", "to": "default",
              "model": "sub", "because": "out of credit"}]
    ledger.add("computed", check_route_fallbacks(early), 0, keep_ids=True)
    assert "substitute model" in ledger.findings["fallback-1"].title
    assert ledger.findings["fallback-1"].detail.count("- ") == 1, "one role listed"

    later = early + [
        {"role": r, "from": "codex", "to": "default", "model": "sub",
         "because": "out of credit"}
        for r in ("reviewer", "adversary", "hacker")
    ]
    ledger.restate("computed", check_route_fallbacks(later), 1)

    assert len(ledger.findings) == 1, "one fact, restated -- not two findings"
    detail = ledger.findings["fallback-1"].detail
    assert all(f"- {r}:" in detail for r in ("breaker", "reviewer", "adversary", "hacker")), \
        "all four roles, from the latest restatement -- not the one from round 0"
    assert ledger.records["fallback-1"].title == ledger.findings["fallback-1"].title, \
        "the record a reader sorts by has to agree with the finding"
    assert ledger.records["fallback-1"].rounds_seen == [0, 1]


def test_a_pinned_model_name_is_checked_against_what_was_built():
    """The name is a contract between two agents who cannot see each other:
    the implementation writes it, the blind suite imports it. A mismatch does
    not fail a test -- it stops a file loading, and every criterion that file
    covers comes back unverified. Measured: `UserSession` against an imported
    `Session`, three files, twenty criteria, the work correct throughout."""
    from factory.pipeline import check_declared_changes
    from factory.schemas import DataChange, FileWrite, PlannedChanges, Spec

    def spec_with(model: str) -> Spec:
        return Spec(title="t", intent="i", summary="s", changes=PlannedChanges(
            data=[DataChange(operation="new_table", table="sessions",
                             nullable=False, model=model)]))

    wrong = [FileWrite(path="api/app/models.py", purpose="",
                       contents="class UserSession(Base):\n    __tablename__ = 'sessions'\n")]
    titles = [f.title for f in check_declared_changes(spec_with("Session"), wrong)]
    assert any("differently from the spec" in t and "Session" in t for t in titles), titles

    right = [FileWrite(path="api/app/models.py", purpose="",
                       contents="class Session(Base):\n    __tablename__ = 'sessions'\n")]
    assert not [f for f in check_declared_changes(spec_with("Session"), right)
                if "pinned" in f.title]

    # A spec that pinned nothing is not a spec that was disobeyed. Reporting
    # here would punish every project that does not map tables to classes.
    assert not [f for f in check_declared_changes(spec_with(""), wrong)
                if "pinned" in f.title]


def test_a_variable_the_run_replaces_is_named_in_the_packet():
    """A command line beats a file, so what a feature wrote into its own
    configuration is not always what ran.

    Measured: a feature added cookie authentication and set its own allowed
    origin to `localhost`; the service command set the same variable to the
    numeric address. The browser tier could not log in, seven checks were
    green, the one that drives a real browser was red -- and nothing anywhere
    said a variable had been replaced. Not the packet, not the brief, not the
    gate output."""
    from factory.pipeline import check_environment_overrides
    from factory.schemas import FileWrite, Service

    class _State:
        services = [Service(
            name="api",
            command=("cd api && KANBAN_CORS_ORIGINS=http://127.0.0.1:$FACTORY_PORT_WEB "
                     "uvicorn app.main:app"),
            ready_when="curl -sf http://localhost:$FACTORY_PORT_API/api/health")]

    wrote = [FileWrite(path="docker-compose.yml", purpose="", contents=(
        "services:\n  api:\n    environment:\n"
        "      KANBAN_CORS_ORIGINS: http://localhost:5183\n"
        "      KANBAN_COOKIE_SECURE: \"false\"\n"))]
    found = check_environment_overrides(_State(), wrote)
    assert len(found) == 1, "one finding with the list, not one finding per variable"
    assert "KANBAN_CORS_ORIGINS" in found[0].evidence
    assert "KANBAN_COOKIE_SECURE" not in found[0].evidence, \
        "a variable the run does not touch is not an override"
    assert found[0].severity == "minor", \
        "usually deliberate -- a disposable database belongs to the run"
    assert "no agent in the build can" in found[0].recommendation, \
        "the fix is in the project's environment, which the feature cannot reach"

    agreed = [FileWrite(path="docker-compose.yml", purpose="", contents=
                        "      KANBAN_CORS_ORIGINS: http://127.0.0.1:$FACTORY_PORT_WEB\n")]
    assert check_environment_overrides(_State(), agreed) == []

    class _Bare:
        services = []
    assert check_environment_overrides(_Bare(), wrote) == []


def test_a_run_condition_is_reported_and_never_queued_as_a_call():
    """Seven of one run's seventeen calls were the factory talking about
    itself: which agents the budget could not measure, which route carried a
    role after its own ran out, which plan was nearly empty. A human reading
    those has nothing to decide -- accepting or sending back a branch does not
    change any of them -- but `escalate` was the only disposition meaning "a
    person should see this", so they queued beside a security defect.

    Two of the seven were the same fact counted twice, in two rounds, with
    different numbers and no way to tell which was current."""
    from factory.config import load_config
    from factory.pipeline import FindingLedger, check_budget_coverage

    cfg = load_config(str(Path("factory.yaml")))
    ledger = FindingLedger()

    def coverage(n: int) -> dict:
        return {"unbilled_roles": [f"role{i}" for i in range(n)],
                "notional_usd": 3.2 * n, "billed_usd": 0.05, "routes": ["claude-code"]}

    ledger.note("computed", check_budget_coverage(coverage(4), cfg), 0)
    ledger.note("computed", check_budget_coverage(coverage(7), cfg), 1)

    assert len(ledger.findings) == 1, "one fact, restated -- not one per round"
    (fid, finding), = ledger.findings.items()
    assert "7 agent(s)" in finding.detail, "the current count, not the first one"
    record = ledger.records[fid]
    assert record.outcome == "noted"
    assert record.disposition_reason, "a reader is told why it is not a call"

    # The worklist is built from outcomes, and this outcome is not one of them.
    waiting = (ROOT / "console" / "ui" / "shared.js").read_text(encoding="utf-8")
    line = [ln for ln in waiting.splitlines() if "const waiting = [" in ln][0]
    assert "noted" not in line, "a condition of the run is not a call to rule on"
    assert "'escalated'" in line, "everything else still is"


def test_the_spec_summary_is_written_for_the_person_approving_it():
    """The summary is the first paragraph a human reads before letting a run
    go, and it was described only as "what is being built" -- so it came back
    as an implementation brief: two routes, a scoping helper, a response
    schema and a test factory, from a five-line intent about labels on cards.
    The agents that need those names already have them, precisely, in
    `changes` and in each criterion's statement."""
    from factory.schemas import Spec

    described = Spec.model_fields["summary"].description or ""
    assert "deciding whether to approve" in described
    assert "No endpoints" in described, "the precise names live in changes and statements"

    prompt = (ROOT / "factory" / "roles" / "spec_writer.md").read_text(encoding="utf-8")
    assert "- **summary**" in prompt, "the spec writer is told who the summary is for"


def test_a_probe_is_kept_only_if_it_passes_the_code_it_would_guard():
    """The admission criterion and the mislabel detector are one check.

    A regression probe that cannot pass the repaired code is not a regression
    test -- and a demonstration whose author called it a regression cannot
    pass the repaired code, by construction. So one executable question
    settles both, and the breaker's own classification never has to be
    trusted: nobody reads the probe to find out.

    card-tags-fee892 is the case this is measured against. Two probes held
    two requests at a barrier until both reached commit, which the serialising
    repair makes unreachable; one of them also treats the correct rejection of
    a 21st tag as an unhandled error, so no correct implementation passes it
    at all. Both would be declined here in under a second. Instead they were
    re-run for 1801 seconds in round 1 and 1801 again in round 2, on a run
    capped at two rounds.
    """
    from factory.schemas import BreakerReport

    tests = [_probe("t/keep_test.py"), _probe("t/broken_test.py")]
    report = BreakerReport(ran=True, failing=["t/broken_test.py"])
    _promote(tests, report, fail=["t/broken_test.py"])

    assert report.promoted == ["t/keep_test.py"]
    assert "t/broken_test.py" not in report.promoted, (
        "a probe the repaired code fails is not a test of the repaired code")

    # A demonstration is never a candidate, whatever it does when run.
    only_demo = [_probe("t/demo_test.py", kind="demonstration")]
    demo_report = BreakerReport(ran=True)
    _promote(only_demo, demo_report)
    assert demo_report.promoted == []


def test_a_regression_probe_must_pass_more_than_once_to_be_kept():
    """Everything this promotes is a concurrency test, the worst category for
    flake, and a race test can pass by simply not hitting the window.

    The repeats are close to free -- the probes that prompted this ran in
    0.16s and 0.26s -- and a probe too slow to run five times is telling you
    something about whether it belongs in every future run.
    """
    from factory.schemas import BreakerReport

    tests = [_probe("t/solid_test.py"), _probe("t/racy_test.py")]
    report = BreakerReport(ran=True)
    runner = _promote(tests, report, flaky=["t/racy_test.py"], runs=5)

    assert report.promoted == ["t/solid_test.py"]
    assert report.flaky == ["t/racy_test.py"]
    assert "passed and then did not" in report.note
    # Five runs in total, and the round already did one, so four repeats.
    assert runner.runs.count("t/solid_test.py") == 4
    # The flake passes its first repeat and fails the second, and is then not
    # run again: once a probe has failed once there is nothing further to learn
    # by running it, and the repeats are for the probes still in the running.
    assert runner.runs.count("t/racy_test.py") == 2

    # `promote_runs = 1` turns the repeat check off and rests on the round's
    # own run, which is the behaviour a project with slow probes can choose.
    once = BreakerReport(ran=True)
    runner = _promote([_probe("t/racy_test.py")], once, flaky=["t/racy_test.py"], runs=1)
    assert once.promoted == ["t/racy_test.py"]
    assert runner.runs == []


def test_nothing_is_kept_when_no_probe_can_be_shown_to_pass_on_its_own():
    """A suite-wide green says the files passed together, not that this file
    passed. Keeping one on that is keeping it on other files' evidence.

    Said out loud rather than done silently, because the fix is one
    configuration line and the alternative is a project that never promotes
    anything and is never told why.
    """
    from factory.schemas import BreakerReport

    report = BreakerReport(ran=True)
    _promote([_probe("t/a_test.py")], report, per_file=False)
    assert report.promoted == []
    assert "breaker_file_command" in report.note

    # And a run that never started proves nothing either way.
    never = BreakerReport(ran=False)
    _promote([_probe("t/a_test.py")], never)
    assert never.promoted == []


def test_a_regression_claim_with_no_pass_condition_is_a_demonstration():
    """The classification is only worth asking for because stating the pass
    condition is the work. A probe whose account skipped it has not done that
    work, and the safe reading of silence is the one that costs nothing to be
    wrong about: a demonstration filed as one is deleted after its round,
    which is what would have happened anyway.
    """
    from factory.schemas import BreakerAccount, BreakerTestAccount

    session = types.SimpleNamespace(
        files=[types.SimpleNamespace(path="t/one_test.py", contents="x"),
               types.SimpleNamespace(path="t/two_test.py", contents="x")],
        outside=[])
    account = BreakerAccount(strategy="s", command="c", tests=[
        BreakerTestAccount(path="t/one_test.py", hypothesis="h", kind="regression",
                           passes_when="the second request gets a 409"),
        BreakerTestAccount(path="t/two_test.py", hypothesis="h", kind="regression"),
    ])
    suite = pipeline.assemble_breaker_suite(session, account)
    by_path = {t.path: t for t in suite.tests}
    assert by_path["t/one_test.py"].kind == "regression"
    assert by_path["t/two_test.py"].kind == "demonstration", (
        "a `regression` claim with nothing behind it is a preference, not a classification")

    # And the default for a probe nobody accounted for at all.
    bare = pipeline.assemble_breaker_suite(session, None)
    assert {t.kind for t in bare.tests} == {"demonstration"}


def test_the_source_says_a_demonstration_stops_being_run_and_stays_in_the_ledger():
    """The two halves of retirement are in one block and easy to separate by
    accident: dropping a probe from the run list is the point, and dropping it
    from what comes forward would silently delete the hypothesis behind a
    finding raised in an earlier round.
    """
    src = inspect.getsource(pipeline.Factory._run_breaker)
    assert 'if t.kind == "regression"' in src, "the run list is filtered on the classification"
    assert "carried = tests + held + retired" in src, (
        "a retired or quarantined probe still comes forward, or an earlier round's finding "
        "loses the text behind it")
    assert "return tests, report" not in src, (
        "every exit has to hand back the carried list, not the run list")


def test_a_probe_that_may_outlive_the_run_is_written_where_it_will_live():
    """Placement happens before anything here runs the probe, so what ships is
    byte-identical to what was proved to pass.

    `blind_write_targets` records why that matters: an oracle file written with
    a correct relative import, relocated four levels deeper afterwards, and the
    import resolved to nothing while six criteria reached a human as failures
    no code caused. The two probe directories are siblings at equal depth under
    the same proved placement, so a path that resolved from one resolves from
    the other -- which is the only property that makes moving a test file safe.
    """
    from factory.schemas import BlindPlacement, PlacementResult

    project = types.SimpleNamespace(state=types.SimpleNamespace(
        blind_placements=[BlindPlacement(kind="pytest", directory="api/tests/acceptance",
                                         filename="test_canary.py",
                                         canary_passes="x", canary_fails="y")],
        placement_probe=[PlacementResult(kind="pytest", directory="api/tests/acceptance",
                                          subdirs_ok=True)]))
    class _Placing:
        _breaker_targets = pipeline.Factory._breaker_targets
        _breaker_regression_targets = pipeline.Factory._breaker_regression_targets
        _breaker_own_dirs = pipeline.Factory._breaker_own_dirs
        _probe_home = pipeline.Factory._probe_home

        def __init__(self, config, project):
            self.config, self.project = config, project

    fake = _Placing(load_config(Path("factory.yaml")), project)
    state = types.SimpleNamespace(feature_id="card-tags-fee892")

    authored = "api/tests/acceptance/card_tags_fee892_breaker/test_limit.py"
    kept = fake._probe_home(state, authored, "regression")
    assert kept == "api/tests/acceptance/card_tags_fee892_regression/test_limit.py", (
        "a directory that outlives the run is named for what is in it, not for the "
        "phase of a run that produced it")
    # Equal depth, which is what makes the placement safe.
    assert authored.count("/") == kept.count("/")

    # A demonstration is deleted at the end of its round, so its directory
    # never persists and there is nothing to rename.
    assert fake._probe_home(state, authored, "demonstration") == authored

    # An extension no placement was proved with stays where it was written.
    # Guessing a directory for a file type nothing was measured on is how a
    # usage error comes to be reported as a failing criterion.
    odd = "api/tests/acceptance/card_tags_fee892_breaker/probe.rb"
    assert fake._probe_home(state, odd, "regression") == odd

    # Both directories are on the verification surface. Until a probe could be
    # kept this was nothing the repairer could reach -- probes were deleted at
    # the end of every round, so there was never a file there to edit.
    own = fake._breaker_own_dirs(state)
    assert "api/tests/acceptance/card_tags_fee892_regression" in own
    assert "api/tests/acceptance/card_tags_fee892_breaker" in own


def test_a_kept_probe_is_protected_from_the_repairer():
    """INV-12 with a hole in it is invisible: the suite still passes after a
    repairer edits the test that was failing.

    `protected_paths` and `is_protected` have always named
    `rework.breaker_dir`, and that has been a guard over nothing -- the default
    is `tests/breaker`, which no real project's runner collects, which is the
    whole reason `_breaker_targets` exists. It protected a path nothing was
    ever written to, and the moment a probe can stay in the tree that stops
    being harmless.
    """
    config = load_config(Path("factory.yaml"))
    kept = "api/tests/acceptance/card_tags_fee892_regression/test_limit.py"
    assert not pipeline.is_protected(kept, [], config), (
        "nothing about the path alone makes it protected -- the directory has to be named"
    )
    assert pipeline.is_protected(
        kept, [], config, ["api/tests/acceptance/card_tags_fee892_regression"])

    # And the guards actually pass it. Four sites decide what a repairer or a
    # simplifier may write -- the repair round (its plan and its refusals share
    # one value), the simplifier, the verification surface, and what a seam
    # finding may name as repairable -- and a probe directory left out of any
    # one of them is a hole in exactly the place the repair loop is under
    # pressure.
    src = class_source(pipeline.Factory)
    assert src.count("self._own_blind_dirs(state) + self._breaker_own_dirs(state)") == 4, (
        "every write guard has to see the probe directories, or one of them is a hole")


def test_promotion_is_a_delete_withheld_rather_than_a_file_written():
    """Two properties, and the second is why the first is worth insisting on.

    A promoted probe ships byte-identical to the file that was proved to pass,
    at the path it was proved at, because promotion adds no write of its own --
    it withholds one delete. That also means there is no second path into the
    tree to guard: the breaker still writes only where it was confined, and no
    orchestrator-performed copy exists to get wrong.
    """
    src = inspect.getsource(pipeline.Factory._run_breaker)
    keep = src[src.index("finally:"):]
    assert "keep = set(report.promoted)" in keep
    assert "if test.path in keep:\n                    continue" in keep, (
        "the deletion has to skip promoted probes, and skip them by path")
    # Nothing writes a probe anywhere after it has run.
    after = src[src.index("await self._promote_probes"):]
    assert "_apply_writes" not in after, (
        "a promoted probe must not be re-written or copied -- that is how a proved "
        "file stops being the file that ships")


def test_a_probe_still_failing_overturns_a_report_that_it_was_fixed():
    """The defect this whole design was pulled out of.

    On card-tags-fee892 two findings sat at `outcome: repaired`,
    `repaired_in_round: 1`, with `rounds_seen: [0, 1, 2]` -- the probes that
    define them were failing in every one of those rounds. They were closed by
    a recheck agent reading the lock off the diff, and the packet then asserted
    both that the concurrency defects were repaired and that concurrency was an
    open blocker, from one probe run.

    A check can be wrong about a defect by being run. A report that the defect
    was fixed cannot be wrong that way, so when they disagree the check is what
    stands, and the record corrects itself rather than only growing.
    """
    from factory.schemas import RepairVerdict

    ledger = pipeline.FindingLedger()
    probe = _probe(("api/tests/acceptance/f_breaker/test_concurrent_tag_limit.py"))

    # Round 0: the probe fails and raises its finding.
    _probe_round(ledger, [probe], [probe.path], 0)
    fid = "breaker-concurrent-tag-limit"
    assert fid in ledger.records

    # Round 1: the repairer reports it fixed, and the ledger believes it --
    # which is right, because nothing has measured the repair yet.
    ledger.apply_verdicts(
        [RepairVerdict(finding_id=fid, status="fixed",
                       evidence="`add_card_tag` now locks the card row.")], 1, "abc123", 2)
    assert ledger.records[fid].outcome == "repaired"
    assert ledger.records[fid].repaired_in_round == 1

    # Round 2 measures the repair, and the probe is still red.
    _probe_round(ledger, [probe], [probe.path], 2)
    record = ledger.records[fid]
    assert record.outcome == "open", "a probe that is still failing is not a repaired finding"
    assert "found again in round 2" in record.outcome_evidence.lower()
    assert "the check is what stands" in record.outcome_evidence
    # And the claim's trappings go with the claim, or the packet names a commit
    # for a repair that did not hold.
    assert record.repaired_in_round is None
    assert record.commit == ""


def test_a_retired_demonstration_is_not_closed_by_a_check_that_did_not_run():
    """Retirement and settling meet here, and the meeting is a trap.

    A demonstration is not re-run after its round, so its finding is absent
    from the probe results because nothing asked it -- not because the answer
    changed. Closing it on that absence would write "the check ran again and no
    longer finds it" onto a record where no check ran, which a human reads as
    evidence. It is the same error as the one this whole design started from,
    pointed the other way: a sentence about an executable result that no
    execution produced.
    """
    demo = _probe("t/test_demo.py", kind="demonstration")
    keeper = _probe("t/test_keeper.py")
    ledger = pipeline.FindingLedger()

    # Round 0 runs both, and both fail.
    _probe_round(ledger, [demo, keeper], [demo.path, keeper.path], 0)
    assert ledger.records["breaker-demo"].outcome == "open"
    assert ledger.records["breaker-keeper"].outcome == "open"

    # Round 1 retires the demonstration and re-runs the regression probe, which
    # now passes. Exactly one of the two findings may be closed by that.
    _probe_round(ledger, [demo, keeper], [], 1, retired=[demo.path])
    assert ledger.records["breaker-keeper"].outcome == "no_longer_holds"
    assert ledger.records["breaker-demo"].outcome == "open", (
        "a finding whose probe was never re-run has not been answered")
    assert "no longer finds it" not in ledger.records["breaker-demo"].outcome_evidence


def test_a_probe_finding_is_identified_by_its_probe_and_not_its_position():
    """`breaker-{i}` numbered over the failing list, so fixing the first probe
    renumbered the second: an id in a human's notes, in a flag they filed, or
    in an arbiter's reason came to name a different defect. It held together
    only because the ledger de-duplicates on title, which is a second mechanism
    quietly propping up the first."""
    first = _probe("t/test_alpha.py")
    second = _probe("t/test_beta.py")
    ledger = pipeline.FindingLedger()

    _probe_round(ledger, [first, second], [first.path, second.path], 0)
    assert set(ledger.records) == {"breaker-alpha", "breaker-beta"}

    # The first is fixed. The second keeps its own name.
    _probe_round(ledger, [first, second], [second.path], 1)
    assert ledger.records["breaker-alpha"].outcome == "no_longer_holds"
    assert ledger.records["breaker-beta"].outcome == "open"


def test_the_orchestrator_settles_probe_findings_from_the_probes():
    """The tests above exercise the ledger. This one is about the wiring,
    because the ledger having the capability is not the same as the run using
    it -- and `add` versus `restate` at one call site is the whole difference
    between a probe settling its own finding and a model's prose settling it.
    """
    src = class_source(pipeline.Factory)
    call = src[src.index("# The probe settles its own finding."):]
    call = call[:call.index("forced_final")]

    assert 'ledger.restate("breaker"' in call, (
        "`add` only ever raises -- a probe that goes green would never close its own finding")
    assert 'ledger.add("breaker"' not in call
    assert 'source="breaker"' in call
    assert "reopen_settled=True" in call, (
        "without this a finding called repaired stays repaired while its probe fails")
    assert "among=settles" in call, (
        "without a scope, a retired demonstration is closed by a check that never ran")
    # `fresh` is the breaker's own `ran`, not a constant: a suite that could not
    # start has not looked, and an empty answer from it is not an answer.
    assert "fresh=breaker_report.ran" in call
    # The scope is what ran, which is what the report says was applied minus
    # what it says was retired.
    assert "set(breaker_report.retired)" in call


def test_a_probe_killed_on_the_clock_is_not_reported_as_a_failing_probe():
    """A timeout and an assertion failure both exit non-zero and mean opposite
    things.

    Filed together, the hang wore the breaker's own hypothesis as the finding's
    title -- "Concurrent additions ... can persist 21 tags", stated as a claim
    about the code, off a probe that had hung for 900 seconds and established
    nothing. `gate_outcome` already draws this line for a whole gate, because
    "a gate that never finished did not report on anything"; a run that
    executes one command per probe has to draw it per probe.
    """
    from factory.schemas import BreakerReport, BreakerSuite

    suite = BreakerSuite(strategy="", tests=[
        _probe("t/test_limit.py", kind="demonstration"),
        _probe("t/test_real.py", kind="demonstration"),
    ])
    suite.tests[0].hypothesis = "concurrent adds can persist 21 tags"
    report = BreakerReport(ran=True, exit_code=1, failing=["t/test_real.py"],
                           timed_out=["t/test_limit.py"], output_tail="timed out after 60.0s")
    found = {f.id: f for f in pipeline.breaker_findings(suite, report)}

    stuck = found["breaker-limit-timeout"]
    assert stuck.category == "verification", (
        "a probe that did not finish is a fact about the measurement, not about the code")
    assert "never finished" in stuck.title
    assert "concurrent adds can persist 21 tags" not in stuck.title, (
        "the hypothesis is what the probe failed to establish -- it cannot be the finding")
    # It is still recorded, so a human can see what was attempted.
    assert "concurrent adds can persist 21 tags" in stuck.detail
    assert "not run again" in stuck.detail
    assert "without pinning the schedule" in stuck.recommendation

    # The genuine failure is untouched and still carries its hypothesis.
    assert found["breaker-real"].category == "robustness"


def test_a_timeout_does_not_become_the_finding_that_blames_nobody():
    """`breaker-suite` exists for a suite that exited non-zero while naming no
    probe -- "either a real defect the probes caught before they could report
    it, or probes that cannot run here". A timeout is neither of those: it
    names its probe, and the answer is already known."""
    from factory.schemas import BreakerReport, BreakerSuite

    suite = BreakerSuite(strategy="", tests=[_probe("t/test_x.py", kind="demonstration")])
    report = BreakerReport(ran=True, exit_code=1, failing=[], timed_out=["t/test_x.py"])
    ids = [f.id for f in pipeline.breaker_findings(suite, report)]
    assert ids == ["breaker-x-timeout"]
    assert "breaker-suite" not in ids


def test_a_probe_is_measured_against_what_it_has_cost_not_against_a_ceiling():
    """The two probes that prompted this ran in 0.26s and 0.16s and were then
    given 900s each -- a factor of about 3,500 -- so a probe that had stopped
    terminating was handed fifteen minutes to prove it, in each of two rounds.

    The ceiling stays a ceiling: a real browser or end-to-end probe needs
    minutes, and lowering it globally would kill honest suites. What changes is
    that a probe which has finished a run before is measured against that run.
    """
    from factory.schemas import BreakerTest

    cfg = load_config(Path("factory.yaml")).rework

    def budget(baseline):
        return pipeline.probe_budget(
            BreakerTest(path="p.py", contents="", hypothesis="h", baseline_s=baseline), cfg)

    # Never finished a run: no baseline to measure against, so the ceiling.
    assert budget(0.0) == cfg.breaker_timeout_s
    # The real case. The floor governs, not the multiple: the question is not
    # how long the probe should take but how long before it is obviously stuck.
    assert budget(0.26) == cfg.probe_budget_floor_s
    assert budget(0.26) < cfg.breaker_timeout_s / 10
    # A slow probe is given room proportional to what it costs.
    assert budget(5.0) == 100.0
    # And never more than the ceiling.
    assert budget(300.0) == cfg.breaker_timeout_s


def test_a_probe_that_timed_out_is_not_run_again_and_is_never_promoted():
    """Re-running a probe that stopped terminating buys the same timeout, the
    same `exit -9` and the same nothing. It cost card-tags-fee892 1801s in
    round 1 and 1801s again in round 2, on a run capped at two rounds, which is
    why every finding still standing at the end read "the loop stopped first".
    """
    from factory.schemas import BreakerReport

    src = inspect.getsource(pipeline.Factory._run_breaker)
    assert "t.quarantined_in_round is not None" in src, "a killed probe is held out of the run"
    assert "test.quarantined_in_round = round_index" in src, "and is marked when it is killed"

    # A probe killed on the clock is not a candidate for the project's suite:
    # not failing is not the same as passing.
    report = BreakerReport(ran=True, timed_out=["t/stuck.py"])
    _promote([_probe("t/stuck.py")], report)
    assert report.promoted == []

    # And its absence from a later round settles nothing, the same rule
    # retirement needs: nothing asked it.
    call = class_source(pipeline.Factory)
    call = call[call.index("# The probe settles its own finding."):]
    assert "set(breaker_report.quarantined)" in call[:call.index("forced_final")]


def test_the_breaker_keeps_a_killed_probe_out_of_its_failing_list():
    """The split has to survive the trip from the runner into the report, or
    the finding shape in `breaker_findings` never gets the chance to apply."""
    src = inspect.getsource(pipeline.Factory._run_breaker)
    assert "killed = set(result.timed_out_files)" in src
    assert "code != 0 and p not in killed" in src, (
        "a probe that was killed is not a probe that failed")
    assert "report.timed_out = sorted(killed)" in src
    # Non-zero all the same: nothing about that run was clean, and a green exit
    # would read as a breaker that found nothing.
    assert "1 if (failed or killed) else 0" in src
    # And a baseline is only taken from a run that finished -- a killed probe
    # took its whole budget, which is a fact about the clock.
    assert "if seconds is not None and test.path not in killed" in src


def test_a_probe_finding_carries_the_probe_a_human_cannot_otherwise_read():
    """A breaker finding claims the code fails a specific attack, and the
    attack is a file. The two ways to check such a claim are to read it or to
    run it, and neither was on offer: the probe is deleted from the branch
    after it runs -- on purpose, so the diff a human reviews is the feature and
    not the attack surface -- and the finding carried a path to it.

    `reviewer-16` is the case. Its recommendation tells a human to "reproduce
    each probe independently", and `api/tests/acceptance/card_tags_fee892_
    breaker/` was never committed. The source was in the ledger the whole time.

    It travels with the finding rather than only on a screen, because the
    finding is what reaches the packet document, the ledger and the arbiter --
    and on that run every reader who had to judge a hanging probe judged it
    from `exit -9` alone, and all of them got it wrong the same way.
    """
    from factory.schemas import BreakerReport, BreakerSuite, BreakerTest

    probe = BreakerTest(
        path="t/test_limit.py", hypothesis="concurrent adds persist 21 tags",
        contents="import asyncio\n\nbarrier = asyncio.Barrier(2)\nassert count == 20\n")
    suite = BreakerSuite(strategy="", tests=[probe])

    # The probe that failed.
    failed = pipeline.breaker_findings(
        suite, BreakerReport(ran=True, exit_code=1, failing=["t/test_limit.py"],
                             output_tail="E assert 21 == 20"))[0]
    assert "asyncio.Barrier(2)" in failed.evidence, (
        "the reader has to be able to see what was actually run")
    assert "E assert 21 == 20" in failed.evidence, "and what it printed"
    assert "t/test_limit.py" in failed.evidence

    # And the probe that never finished, where it is worth most: the output is
    # one line saying the clock ran out, so the source is the only thing there
    # is to judge.
    stuck = pipeline.breaker_findings(
        suite, BreakerReport(ran=True, exit_code=1, timed_out=["t/test_limit.py"],
                             output_tail="timed out after 60.0s"))[0]
    assert "asyncio.Barrier(2)" in stuck.evidence


def test_a_probe_that_inlines_a_fixture_set_cannot_crowd_out_the_packet():
    """A probe is a test file, not a repository -- the three on the run this
    was written for are 2151, 1719 and 1132 characters. The cap is for the one
    that inlines everything, and it says where the rest went rather than
    stopping mid-line."""
    from factory.schemas import BreakerTest

    huge = BreakerTest(path="t/test_big.py", hypothesis="h", contents="x" * 40_000)
    evidence = pipeline.probe_evidence("t/test_big.py", huge, "out")
    assert len(evidence) < 12_000
    assert "truncated" in evidence
    assert "the whole file is in the ledger" in evidence


def test_the_same_agent_saying_it_again_is_not_a_second_opinion():
    """Review agents are sampled several times at high temperature on purpose,
    so one defect arrives worded three ways. Tying the three together is right.
    Counting them is asking one witness to describe the car three times and
    writing down three witnesses.

    It cost a real packet a blocker: three samples of one agent restated one
    hanging probe, and the arbiter routed it for repair because "three
    independent reviewers reproduce the identical symptom ... strong
    corroboration of a real hang rather than a flaky report". One agent, one
    model, and one probe run whose single line of output all three had read.
    """
    from factory.schemas import FindingDisposition

    ledger = _ledger_of_agents(
        ("r-1", "reviewer", "gpt-5.6-sol"),
        ("r-2", "reviewer", "gpt-5.6-sol"),
        ("r-3", "reviewer", "gpt-5.6-sol"),
    )
    ledger.dispose([
        FindingDisposition(finding_id="r-2", disposition="repair", reason="same", duplicate_of="r-1"),
        FindingDisposition(finding_id="r-3", disposition="repair", reason="same", duplicate_of="r-1"),
    ])
    primary = ledger.records["r-1"]
    assert primary.restated_by == ["r-2", "r-3"]
    assert primary.corroborated_by == [], (
        "three passes of one reader are not three readers agreeing")
    # Nothing is dropped: the restatements keep their records and their words.
    assert ledger.records["r-2"].duplicate_of == "r-1"
    assert "r-2" in ledger.findings


def test_a_different_agent_finding_the_same_defect_is_corroboration():
    """Which is what the word means, and it is worth weighing. A probe that
    failed and a reader that found the same defect by reading arrived by two
    methods, and the probe's half was executed.

    The breaker's finding carries no model because it is not an opinion:
    `breaker_findings` computes it from the runner's own output, and the model
    that wrote the probe supplied the hypothesis before it knew the outcome.
    """
    from factory.schemas import FindingDisposition

    ledger = _ledger_of_agents(
        ("breaker-1", "breaker", ""),
        ("r-4", "reviewer", "gpt-5.6-sol"),
    )
    ledger.dispose([FindingDisposition(
        finding_id="r-4", disposition="repair", reason="same", duplicate_of="breaker-1")])
    assert ledger.records["breaker-1"].corroborated_by == ["r-4"]
    assert ledger.records["breaker-1"].restated_by == []


def test_two_readers_on_one_model_are_one_reader():
    """Not hypothetical: two review roles were merged into one agent precisely
    because both sat on one vendor, so the roster promised two readings and
    bought one. A new name is not a new opinion, so the model decides and the
    role does not enter it.

    It works because only an opinion carries a model. A finding computed from a
    process carries none and can never be "the same reading" as anything, so a
    probe that failed and a reader that found the same defect stay two pieces
    of evidence even when one vendor wrote both — one of them was executed.
    """
    from factory.schemas import FindingDisposition

    ledger = _ledger_of_agents(
        ("a-1", "reviewer", "gpt-5.6-sol"),
        ("a-2", "adversary", "gpt-5.6-sol"),      # a second name on one model
        ("a-3", "adversary", "claude-opus-5"),    # a second model
        ("a-4", "breaker", ""),                   # computed from a probe, not opined
    )
    ledger.dispose([
        FindingDisposition(finding_id=f, disposition="repair", reason="s", duplicate_of="a-1")
        for f in ("a-2", "a-3", "a-4")
    ])
    primary = ledger.records["a-1"]
    assert primary.restated_by == ["a-2"], "one model is one reader, whatever it is called"
    assert primary.corroborated_by == ["a-3", "a-4"]


def test_an_unrecorded_agent_never_counts_as_agreement():
    """A record written before models were recorded has none, and two blanks
    are not a match. An unknown is not evidence of sameness any more than of
    difference, and the conservative reading is the one that does not
    manufacture agreement out of a missing field."""
    from factory.schemas import FindingDisposition

    ledger = _ledger_of_agents(("u-1", "reviewer", ""), ("u-2", "reviewer", ""))
    ledger.dispose([FindingDisposition(
        finding_id="u-2", disposition="repair", reason="s", duplicate_of="u-1")])
    assert ledger.records["u-1"].restated_by == []
    assert ledger.records["u-1"].corroborated_by == ["u-2"]


def test_the_order_a_human_reads_findings_in_ignores_restatements():
    """`human_first` ranked on the count, so a finding sorted higher for the
    number of ways one agent happened to word itself."""
    from factory.schemas import FindingDisposition

    ledger = _ledger_of_agents(
        ("echoed", "reviewer", "m1"), ("e-2", "reviewer", "m1"), ("e-3", "reviewer", "m1"),
        ("backed", "reviewer", "m1"), ("b-2", "breaker", "m2"),
    )
    ledger.dispose([
        FindingDisposition(finding_id="e-2", disposition="repair", reason="s", duplicate_of="echoed"),
        FindingDisposition(finding_id="e-3", disposition="repair", reason="s", duplicate_of="echoed"),
        FindingDisposition(finding_id="b-2", disposition="repair", reason="s", duplicate_of="backed"),
    ])
    order = ledger.human_first(["echoed", "backed"])
    assert order == ["backed", "echoed"], (
        "one agent restating itself twice must not outrank a defect another agent confirmed")


def test_the_arbiter_is_told_which_agent_raised_each_finding():
    """It never saw a corroboration count -- it saw several entries each
    reading `raised by: <role>` and read them as several agents. Naming the
    model is what makes three passes of one reader legible as one."""
    src = inspect.getsource(pipeline.Factory._arbitrate)
    assert "_agent_of(ledger.records[fid])" in src
    assert "Entries that name the same agent are one agent, not several." in src
    assert "crosses agents" in src

    from factory.schemas import FindingRecord
    named = pipeline._agent_of(FindingRecord(
        finding_id="x", role="reviewer", model="gpt-5.6-sol", title="t", severity="major"))
    assert named == "reviewer (gpt-5.6-sol)"
    # And degrades to the role when nothing recorded a model, rather than
    # printing an empty pair of brackets that reads as a missing agent.
    bare = pipeline._agent_of(FindingRecord(
        finding_id="x", role="reviewer", title="t", severity="major"))
    assert bare == "reviewer"


def test_only_an_opinion_carries_a_model():
    """The rule that tells a second reader from a second pass compares models,
    and that only works while a model means "this was somebody's reading".

    A finding computed from a process -- a probe's exit code, a gate that used
    to pass, a file the plan named and nobody wrote -- is not a reading, so it
    is stamped with no model and can never collapse into one. Stamp one on it
    and a probe that failed would start counting as the same evidence as the
    prose of whoever wrote the probe.
    """
    src = class_source(pipeline.Factory)
    # The panel's own findings are the only ones that get a model. Counted on
    # the ledger calls rather than on the expression, which the evidence store
    # also uses for its own records.
    stamped = [chunk for chunk in src.split("ledger.add(role_name,")[1:]]
    assert len(stamped) == 3, "every panel finding enters the ledger by one of these"
    for chunk in stamped:
        assert "model=self._answered_by(role_name)" in chunk[:200], (
            "a panel finding that is not stamped cannot be told from another agent's")
    for computed in ('ledger.add("computed"', 'ledger.add("execution"',
                     'ledger.restate("computed"', 'ledger.restate("breaker"'):
        for line in src.split(computed)[1:]:
            head = line[:240]
            assert "model=" not in head, (
                f"{computed} must not carry a model -- it is computed, not opined")


def test_a_swept_recording_carries_its_file_and_its_title(tmp_path):
    """The coverage check joins two facts: which file a recording came from,
    and which criteria its test's title names. Both are known at the sweep and
    at no later point without reopening an archive, so both are written down
    there."""
    kept, store, _ = _trace_run(
        ["web/test-results"],
        {"web/test-results/card--AC-13-chromium/trace.zip":
             _recording_of(tmp_path, [_DRAWN]).read_bytes()},
        test_path="web/e2e/acceptance/f/card.spec.ts")

    record = store.payloads("traces")[-1]
    assert record["by_test"] == {kept[0]: "web/e2e/acceptance/f/card.spec.ts"}
    assert record["titles"] == {kept[0]: "a recorded test"}, (
        "the title the recording's own trace gave, which is what names its criteria")


def test_a_criterion_a_recorded_test_checked_with_no_recording_naming_it_is_reported():
    """Recordings replaced screenshots, and the question the screenshot check
    asked carries over: of the criteria that could be shown, which were not?

    What could be shown is measured, not declared: a blind file that left a
    recording is a file driving a browser. So a project with no browser is
    never asked for recordings, and a criterion checked below it is not a gap.
    What was shown is the test's title, the only place a runner writes anything
    a criterion can be read from.
    """
    from factory.schemas import OracleSuite, TestFile

    browser = "web/e2e/acceptance/f/card_tags.spec.ts"
    oracle = OracleSuite(strategy="s", tests=[
        TestFile(path=browser, contents="x", criterion_ids=["AC-10", "AC-11", "AC-16"]),
        TestFile(path="api/tests/acceptance/f/test_api.py", contents="x",
                 criterion_ids=["AC-1", "AC-2"]),
    ])
    traces = [{"files": ["a-trace.zip"], "by_test": {"a-trace.zip": browser},
               "titles": {"a-trace.zip": "card_tags.spec.ts:9 \u203a [AC-10] a card wears its tags"}}]
    found = pipeline.check_recording_coverage(oracle, traces)
    assert len(found) == 1 and found[0].id == "recordings-uncovered"
    # Read the listed ids rather than searching the prose: "AC-1" is inside
    # "AC-11", and a test that cannot tell them apart would pass while the
    # check confused them too.
    listed = {line.split("\u2014")[0].strip(" -") for line in found[0].detail.splitlines()
              if line.startswith("- AC-")}
    assert listed == {"AC-11", "AC-16"}, (
        "the recorded criterion is not a gap, and the API file drove no browser so "
        "nothing about it is asked for")

    # A title spelled another way still names its criterion: the same reading
    # every other criterion reference gets.
    spelled = [{**traces[0], "titles": {"a-trace.zip": "[ac_11] [AC 16] [AC-10] all three"}}]
    assert pipeline.check_recording_coverage(oracle, spelled) == []

    # Two recordings titled for one criterion both count, and one titled for
    # none counts for nothing.
    vague = [{**traces[0], "titles": {"a-trace.zip": "a person can create a tag"}}]
    gaps = pipeline.check_recording_coverage(oracle, vague)
    assert {line.split("\u2014")[0].strip(" -") for line in gaps[0].detail.splitlines()
            if line.startswith("- AC-")} == {"AC-10", "AC-11", "AC-16"}

    # Nothing recorded from a blind file: nothing could have been, so nothing
    # is asked for. And no oracle, no question.
    assert pipeline.check_recording_coverage(oracle, [{"files": ["x.zip"]}]) == []
    assert pipeline.check_recording_coverage(None, traces) == []

    src = class_source(pipeline.Factory)
    assert 'source="recording_coverage"' in src


def test_a_repair_unit_is_named_uniquely_across_rounds():
    """A repairer numbers its units from one inside its own round, so round
    2's `R-1` is a different unit from round 1's -- different file, different
    change, same name.

    Everything downstream keyed on the unit alone had them collide.
    `_renumber_decisions` namespaces a decision by its unit, which is right for
    a build unit that exists once, and `order_packet` then keeps the first of
    any id it meets twice. On card-tags-fee892 ten repair decisions were
    recorded and six reached the packet; the four lost were round 2's, all of
    them, including the reasoning behind the change that broke a required
    check. INV-11 says the record only grows.
    """
    assert pipeline._round_unit_id("R-1", 1) == "R1-1"
    assert pipeline._round_unit_id("R-1", 2) == "R2-1", "two rounds, two names"
    assert pipeline._round_unit_id("R-3", 2) == "R2-3"
    # Anything that is not shaped like a repairer's unit is still made unique,
    # because the collision is what matters and the shape is a convention.
    assert pipeline._round_unit_id("odd", 2) == "R2-odd"
    # A build unit never comes through here; round 0 is not a repair round.
    assert pipeline._round_unit_id("R-1", 0) == "R-1"

    src = class_source(pipeline.Factory)
    assert '"unit_id": _round_unit_id(o.unit_id, round_index)' in src, (
        "the rename has to happen once, where the outputs join the run's workers -- doing it "
        "at each reader leaves the disclosure lines saying the old name")

    # Ten decisions in, ten out.
    from factory.schemas import Decision, IntegrationReport, WorkerOutput
    def unit(name, n):
        return WorkerOutput(unit_id=name, summary="s", decisions=[
            Decision(id=f"D-{i}", title="t", choice="c", rationale="w",
                     reversibility="trivial", blast_radius="none", confidence=0.5)
            for i in range(1, n + 1)])

    rounds = [(1, [unit("R-1", 3), unit("R-2", 2), unit("R-3", 1)]),
              (2, [unit("R-1", 3), unit("R-2", 1)])]
    stamped = [o.model_copy(update={"round": n,
                                    "unit_id": pipeline._round_unit_id(o.unit_id, n)})
               for n, outs in rounds for o in outs]
    ids = [d.id for d in pipeline._renumber_decisions(stamped, IntegrationReport(summary=""))]
    assert len(ids) == 10 and len(set(ids)) == 10, (
        "ten decisions were recorded and every one has to keep its own id")
    assert "R2-1/D-3" in ids, "round 2's decisions are the ones that used to be lost"


def test_a_recording_of_a_browser_run_is_kept_where_a_human_can_open_it():
    """The gap between "a test passed" and "show me".

    A screenshot is one moment somebody chose, and choosing is a thing that can
    be got wrong: on one run a picture named for a criterion about a tag
    appearing *before* the server responds showed the error message from after
    it failed, because that was the state the test happened to reach. A trace
    has no moment to choose -- it is a frame-by-frame capture with the DOM at
    every action. Measured on this machine at 32KB plus 3.2KB an action, and
    2.7% of wall clock.
    """
    kept, store, sandbox = _trace_run(
        ["web/test-results"],
        {"web/test-results/board--moves-a-card-chromium/trace.zip": b"PK\x03\x04zip",
         "web/test-results/.last-run.json": b"{}",
         "web/test-results/board--moves-a-card-chromium/error-context.md": b"notes"})

    # The path becomes the name: a trace is always called `trace.zip`, so the
    # directory it sat in is the only thing that says which test it is.
    assert kept == ["board--moves-a-card-chromium-trace.zip"]
    assert (store.artifact_dir("traces") / kept[0]).read_bytes() == b"PK\x03\x04zip"
    # Only the declared suffixes: a runner leaves scratch files beside them.
    assert not any("last-run" in k or "error-context" in k for k in kept)
    # And off the checkout, like the pictures -- otherwise the next round
    # commits them and the diff a human reads grows a suite of zip files.
    assert not (sandbox.path / "web/test-results").exists()
    assert [r.get("from") for r in store.payloads("traces")] == [["web/test-results"]]


def test_nothing_is_collected_until_a_project_says_where():
    """Nothing here knows what a test runner is called or where it writes. A
    path guessed from a framework's defaults is one that quietly stops existing
    when somebody changes a config this tool does not own, so the default
    collects nothing and a project that records its runs says where."""
    from factory.schemas import ProjectState, ProjectSurvey
    assert ProjectState(project_id="p", name="P", repo="/tmp").trace_dirs == [], (
        "off until a reading of the repository says otherwise")
    assert "trace_dirs" in ProjectSurvey.model_fields, "and the surveyor is what says it"
    kept, store, _ = _trace_run([], {"web/test-results/a-chromium/trace.zip": b"PK"})
    assert kept == [] and store.payloads("traces") == []


def test_a_recording_too_big_to_keep_says_so_rather_than_vanishing():
    """A trace is bigger than a picture by design, and it used to inherit
    `screens_max_bytes` -- a 4MB ceiling written for PNGs, which would drop one
    silently for being the size it is meant to be. It has its own ceiling, and
    exceeding it is recorded."""
    kept, store, _ = _trace_run(
        ["web/test-results"], {"web/test-results/big-chromium/trace.zip": b"x" * 5000},
        cap=1000)
    assert kept == []
    said = store.payloads("trace_too_big")
    assert len(said) == 1 and said[0]["bytes"] == 5000 and said[0]["cap"] == 1000
    assert "big-chromium" in said[0]["path"]


def test_each_round_gets_its_own_ceiling_of_recordings():
    """The ceiling was spent per feature. The first round of a Kanban run kept
    18 of 20, the second kept 2, the third kept none, and the packet showed
    recordings of a tree two repairs gone while the tree a person was asked to
    rule on had not one."""
    files = {f"web/test-results/t{i}-chromium/trace.zip": b"PK" for i in range(3)}
    kept, store, _ = _trace_run(["web/test-results"], files, count=20,
                                earlier=(18, 2), round_index=2)
    assert len(kept) == 3, "a later round got nothing because earlier ones used the ceiling"
    assert store.records()[-1]["meta"] == {"round": 2}, "a recording does not say its round"

    # Still a ceiling within the round.
    kept, _, _ = _trace_run(["web/test-results"], files, count=2, earlier=(18,))
    assert len(kept) == 2


def test_the_recordings_offered_are_the_latest_assessment_s_and_only_those():
    """Every round's were shown together once: fifteen of the tree before either
    repair beside two of the one after, and nothing said which was which."""
    def traces(*names):
        return {"kind": "traces", "payload": {"files": list(names)}}

    def gates(attributed=False):
        return {"kind": "gates", "payload": {},
                "meta": {"round": 0, **({"attributed": True} if attributed else {})}}

    names = lambda got: sorted(n for p in got for n in p["files"])  # noqa: E731

    assert pipeline.current_recordings([]) is None, \
        "nothing recorded this way must be told apart from a round that recorded nothing"
    rounds = [traces("a"), traces("b"), gates(), gates(attributed=True),
              traces("c"), gates(), gates(attributed=True)]
    assert names(pipeline.current_recordings(rounds)) == ["c"]
    # A round still running is the newest there is.
    assert names(pipeline.current_recordings(rounds + [traces("d")])) == ["d"]
    # A round that recorded nothing is answered with nothing, not the one before.
    assert pipeline.current_recordings(rounds + [gates(), gates(attributed=True)]) == []
    # A re-check starts again.
    assert pipeline.current_recordings(
        [traces("a"), {"kind": "revalidate", "payload": {}}]) == []

    src = inspect.getsource(pipeline.Factory._converge)
    assert 'check_recording_coverage(oracle, current_recordings(store.records())' in src, \
        "the packet counts recordings from rounds the listing no longer offers"


def test_a_check_is_compared_with_the_base_in_the_same_standing_environment():
    """Without the project's services, every check that needs a server failed at
    the base commit for want of one, and was called pre-existing -- Kanban's
    browser gate, on a base where it passes. Cached, that answer would have
    gone on excusing every feature built on that commit."""
    src = inspect.getsource(pipeline.Factory._attribute_failures)
    assert "async with test_session(" in src and "services=list(env.services)" in src
    assert '"in_session": True' in src

    class _Asking:
        _cached_attribution = pipeline.Factory._cached_attribution

        def __init__(self, records):
            self.project = types.SimpleNamespace(store=records)

    old = {"kind": "attribution", "payload": {"base_sha": "abc", "gate": "web-e2e",
                                              "at_base": "failed"}}
    assert _Asking([old])._cached_attribution("abc", "web-e2e") is None, \
        "an answer measured without the services is still trusted"
    new = {"kind": "attribution", "payload": {**old["payload"], "at_base": "passed",
                                              "in_session": True}}
    assert _Asking([old, new])._cached_attribution("abc", "web-e2e")["at_base"] == "passed"


def test_a_browser_that_could_record_and_did_not_is_reported():
    """The surveyor is asked to name the recording directory and to put the
    switch in `recommendations` when it is off. That is prose in a brief, and
    a brief is guidance a model can skip -- so the outcome is measured, the way
    everything else here is measured rather than trusted.

    Two gaps, two sentences. Nothing declared is a reading that did not answer.
    Declared and empty is a switch nobody turned on, in a config file this
    factory does not own.
    """
    # Declared, and empty: the switch.
    off = pipeline.check_trace_coverage(["web/test-results"], [], True)
    assert [f.id for f in off] == ["traces-none"]
    assert "web/test-results" in off[0].detail
    assert "trace: 'on'" in off[0].recommendation
    # And the setting that brings the frames to the page's size, said with why
    # it looks odd -- otherwise the first tidy-up turns it off again.
    assert "video: { mode: 'on', size: <the viewport> }" in off[0].recommendation
    assert "never collected" in off[0].recommendation
    assert "come off the project" in off[0].recommendation

    # Nothing declared: the reading.
    missing = pipeline.check_trace_coverage([], [], True)
    assert [f.id for f in missing] == ["traces-undeclared"]
    assert "Read the project again" in missing[0].recommendation

    # Recording, so nothing to say; no browser ran, so nothing to ask for.
    assert pipeline.check_trace_coverage(["web/test-results"], [{"files": ["t.zip"]}], True) == []
    assert pipeline.check_trace_coverage(["web/test-results"], [], False) == []
    assert pipeline.check_trace_coverage([], [], False) == []

    # Recomputed every round from the run's own records, so turning the switch
    # on closes it without anybody ruling on anything.
    src = class_source(pipeline.Factory)
    assert 'source="trace_coverage"' in src
    assert "browser_ran(oracle, spec, gates)" in src
    assert "self.project.state.trace_dirs" in src, (
        "off the project, because where a runner writes is a fact about one repository")


def test_whether_a_browser_ran_is_measured_without_a_screenshot():
    """The trace check used to learn that a browser had run from the fact that
    a screenshot existed. Screenshots are gone, and a check that depended on
    them would have gone quiet with them -- reporting nothing on exactly the
    run where the recordings it exists to ask for were missing.

    The replacement is two facts. The spec says which criteria are checked in
    the browser, frozen before the build. The blind gate times every file it
    starts. A timed file covering one of those criteria is a browser that ran.
    """
    from factory.schemas import (AcceptanceCriterion, GateReport, GateResult,
                                 OracleSuite, Spec, TestFile)

    spec = Spec.model_construct(acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="s", verified_at="integration"),
        AcceptanceCriterion(id="AC-13", statement="s", verified_at="user"),
    ])
    oracle = OracleSuite(strategy="s", tests=[
        TestFile(path="api/t.py", contents="x", criterion_ids=["AC-1"]),
        TestFile(path="web/c.spec.ts", contents="x", criterion_ids=["AC-13"]),
    ])

    def ran(*paths):
        return GateReport(results=[GateResult(
            name="blind-tests", file_seconds={p: 1.0 for p in paths})])

    assert pipeline.browser_ran(oracle, spec, ran("api/t.py", "web/c.spec.ts")) is True
    assert pipeline.browser_ran(oracle, spec, ran("api/t.py")) is False, (
        "the browser file never started, so no browser ran")
    assert pipeline.browser_ran(oracle, spec, GateReport()) is False, "no blind gate"
    assert pipeline.browser_ran(None, spec, ran("web/c.spec.ts")) is False


def test_the_surveyor_is_told_to_recommend_the_switch_it_finds_off():
    """Belt to the braces above. The check reports the gap on every run; the
    brief is what stops the gap existing in the first place, and it has to ask
    for both halves -- the directory, which is a fact, and the switch, which is
    a change to a file this factory does not own and therefore a human's."""
    brief = (ROOT / "factory" / "roles" / "surveyor.md").read_text(encoding="utf-8")
    assert "Say where the browser runner leaves a recording" in brief
    assert "one line of the runner's configuration" in brief
    assert "a human's to approve and not this tool's to change" in brief

    # The distinction that was missed, and the reason it was missed.
    #
    # The first wording said to recommend the switch "if it is off today", and
    # a real reading found `retain-on-failure`, correctly reported that nothing
    # needed switching on, and recommended nothing -- so a project whose
    # browser tests all pass would keep leaving no recording at all. A config
    # set to keep one on failure is not off, and it answers a different
    # question.
    assert "counts as off" in brief
    assert "why did this break" in brief and "show me" in brief, (
        "the brief has to say which question each kind of recording answers")

    from factory.schemas import ProjectSurvey, SurveyDiff
    for model in (ProjectSurvey, SurveyDiff):
        said = model.model_fields["trace_dirs"].description or ""
        assert "counts as off" in said, model.__name__
        assert "`recommendations`" in said, model.__name__
    # And not to name a directory the runner does not write to.
    assert "holds nothing" in (ProjectSurvey.model_fields["trace_dirs"].description or "")
    assert "actually writes to" in (SurveyDiff.model_fields["trace_dirs"].description or "")


def test_a_recording_is_unpacked_into_frames_a_page_can_step_through():
    """The full viewer is a service-worker application that reads the archive
    itself, so it cannot run inside another page -- which is why the only way
    into a recording was a download and a command, and why "where are the
    videos" was a fair question about a band that had them.

    The frames and the step names are both in the archive, on one clock, so a
    walkthrough is arithmetic on two lists. Unpacked at collection because a
    packet read six times should not unzip the same archive six times.
    """
    real = sorted(pathlib.Path(
        ".factory/kanban/features/card-tags-fee892/artifacts/traces").glob("*.zip"))
    if not real:
        import pytest
        pytest.skip("no collected recording on this machine to read")

    frames, manifest = pipeline.trace_player(real[0])
    assert frames and manifest["frames"], "a recording with no frames is not a walkthrough"
    assert manifest["width"] and manifest["height"]
    assert manifest["title"], "the test it recorded names itself"

    # Every frame carries the step it belongs to, which is the only thing here
    # a screenshot band cannot say.
    assert all(f["file"] and f["at"] >= 0 for f in manifest["frames"])
    assert any(f["step"] for f in manifest["frames"])

    # Steps a person did, not the harness standing a browser up. A walkthrough
    # that opens on `Fixture "browser"` has spent the reader's first clicks
    # before anything about the feature happens.
    for f in manifest["frames"]:
        assert not f["step"].startswith(("Fixture ", "Before Hooks", "After Hooks")), f["step"]
        assert f["step"] != "Query count", "resolving a locator is not a step"

    # Frames are written under names this code chose, so an archive naming
    # `../../anywhere` writes nothing anywhere.
    assert all(re.fullmatch(r"f\d{4}\.jpeg", name) for name, _ in frames)

    src = inspect.getsource(pipeline.trace_player)
    assert "next((n for n in names" in src, "members are matched against the listing, not joined"


def test_a_walkthrough_does_not_open_on_the_empty_browser(tmp_path):
    """The screencast starts when the browser context opens, so a recording
    begins before anything has been navigated to: three or four frames of
    blank browser, identical bytes, and the player opened on the first of
    them. A reader clicking `next` four times and still seeing nothing has no
    way to tell a slow walkthrough from a broken one.

    The frames are dropped at unpack, not hidden in the page, because every
    reader of the recording has the same problem and the page is not the only
    one of them.
    """
    frames, manifest = pipeline.trace_player(
        _recording_of(tmp_path, [_FLAT, _FLAT, _FLAT, _DRAWN, _DRAWN]))

    assert len(frames) == 2, "the three blanks are gone and the two drawn frames stay"
    assert all(len(data) == _DRAWN for _, data in frames)

    # Numbered from the first frame kept, so `1/2` is the first thing a reader
    # sees and the count is the walkthrough's real length.
    assert [name for name, _ in frames] == ["f0001.jpeg", "f0002.jpeg"]
    assert [f["file"] for f in manifest["frames"]] == ["f0001.jpeg", "f0002.jpeg"]
    assert manifest["width"] == 1280 and manifest["height"] == 720, (
        "the viewport is read off a frame that survived the trim")


def test_a_screen_that_goes_blank_mid_run_is_kept(tmp_path):
    """The trim is deliberately head-only. A blank frame in the middle of a
    run is a screen that went white while the test was working -- which is a
    finding, and the most interesting thing a recording can show. Dropping it
    would make the walkthrough smoother and the evidence worse.
    """
    frames, _ = pipeline.trace_player(
        _recording_of(tmp_path, [_FLAT, _DRAWN, _FLAT, _DRAWN]))

    assert [len(data) for _, data in frames] == [_DRAWN, _FLAT, _DRAWN], (
        "the leading blank goes, the one in the middle stays where it happened")


def test_a_recording_blank_all_the_way_through_keeps_its_frames(tmp_path):
    """An empty player is what an unreadable archive returns. A run that drew
    nothing is a different thing -- it recorded fine and there was nothing on
    the screen -- and trimming it to nothing would report the second as the
    first, which is the one case where a reader most needs the difference.
    """
    frames, manifest = pipeline.trace_player(
        _recording_of(tmp_path, [_FLAT, _FLAT, _FLAT]))

    assert len(frames) == 3 and manifest["frames"], (
        "nothing drawn is still something recorded")


def test_the_blank_test_is_a_ratio_and_not_a_number_of_bytes(tmp_path):
    """The blank frames are the same bytes in every recording measured, so
    matching them exactly would work today. It would also break the first time
    a project records at a different viewport, and break silently -- the
    player would go back to opening on nothing with no failure anywhere.

    So the rule is bytes per pixel. This is the check that it travels: the same
    flat frame at a quarter of the area is still flat, and a frame that is
    blank for a big viewport is drawn for a small one.
    """
    small, _ = pipeline.trace_player(
        _recording_of(tmp_path, [_FLAT // 4, _DRAWN // 4], width=640, height=360))
    assert len(small) == 1, "flat at 640x360 too, where the byte count is different"

    # The same size, over a viewport small enough that it is no longer flat.
    tiny, _ = pipeline.trace_player(
        _recording_of(tmp_path, [_FLAT, _DRAWN], width=320, height=180))
    assert len(tiny) == 2, "2459 bytes over 320x180 is a drawn frame, and it is kept"

    src = inspect.getsource(pipeline._without_leading_blanks)
    assert "width * height" in src, "the threshold is scaled by the area, not compared raw"


def test_the_frames_budget_is_spent_on_frames_with_something_on_them(tmp_path):
    """The cap subsamples a long recording evenly. Applied before the trim it
    would spend part of a reader's sixty frames on the blank browser at the
    front and then thin out the run to make room, which is the budget paying
    for the thing we just decided to throw away.
    """
    sizes = [_FLAT] * 4 + [_DRAWN] * (pipeline._TRACE_FRAMES_MAX + 10)
    frames, _ = pipeline.trace_player(_recording_of(tmp_path, sizes))

    assert len(frames) == pipeline._TRACE_FRAMES_MAX
    assert all(len(data) == _DRAWN for _, data in frames), (
        "a full budget of drawn frames, no blanks taking up a slot")


def test_the_collected_recordings_open_on_something_drawn():
    """The measurement the rest of this rests on, against the real archives:
    three unrelated runs, each of which used to open on an empty browser.
    """
    real = sorted(pathlib.Path(
        ".factory/kanban/features/card-tags-fee892/artifacts/traces").glob("*.zip"))
    if not real:
        pytest.skip("no collected recording on this machine to read")

    for archive in real:
        frames, manifest = pipeline.trace_player(archive)
        assert frames, archive.name
        first = len(frames[0][1])
        area = manifest["width"] * manifest["height"]
        assert first >= area * pipeline._TRACE_BLANK_BYTES_PER_PIXEL, (
            f"{archive.name} opens on {first} bytes over {area} pixels, which is blank")


def test_what_a_human_reads_first_is_asked_for_in_plain_words():
    """Human review is the bottleneck, so every surface a person decides from
    says the thing once, plainly, and then elaborates. The packet headline,
    each finding's title and each interrogation question are the short layer;
    each has a detail field underneath. The models writing them were told
    only "one sentence" or "one line", and wrote the elaboration there
    instead: "AC-1 through AC-20 marked failed, two tests named" as a headline,
    a response schema as a question."""
    from factory.schemas import Ambiguity, Finding, Packet

    headline = Packet.model_fields["headline"].description or ""
    assert "the one reason" in headline and "No criterion ids" in headline
    title = Finding.model_fields["title"].description or ""
    assert "what is wrong, not where" in title and "No file paths" in title
    question = Ambiguity.model_fields["question"].description or ""
    assert "a person would recognise" in question and "why_it_matters" in question

    roles = ROOT / "factory" / "roles"
    assert "Say it once, plainly, then elaborate" in (roles / "rapporteur.md").read_text()
    # Every prompt that writes a finding. `adversary` is not here because it
    # has no prompt of its own any more -- it is the reviewer's second reading
    # on a second model family, which is the part that was worth keeping.
    assert "## How a finding is titled" in (roles / "reviewer.md").read_text()
    assert "Ask the choice a person would recognise" in (roles / "interrogator.md").read_text()


def test_the_factory_s_own_findings_lead_with_what_is_wrong():
    """The factory's computed findings were titled in its own vocabulary, count
    first: "1 unit(s) were built against code in another unit's worktree",
    "the budget did not constrain 7 agent(s)". A reader decoded each before
    understanding it. They now lead with the claim in plain words, and any
    count, path or list follows -- in a short tail, or in the detail, which is
    where the elaboration starts."""
    import re

    src = factory_source("pipeline")
    offenders = []
    # Findings only: a work unit's title is an instruction to an agent, not a
    # line a human reads in a list.
    for match in re.finditer(r'Finding\(\s*id=[^\n]*\n\s*title=\(?\s*f?"(.)', src):
        first = match.group(1)
        if first == "{" or first.islower():
            line = src[:match.start()].count("\n") + 1
            offenders.append(f"line {line}: starts with {first!r}")
    assert not offenders, "a computed title opens with a count or an internal term: " \
        + "; ".join(offenders)


def test_a_record_names_the_model_that_answered_not_the_one_configured():
    """A route ran out mid-run and the oracle fell back. Two sessions on the
    configured model made zero tool calls; the substitute wrote all five test
    files. Every record was stamped with the configured model, so the console
    credited a suite to a model that had written none of it."""
    import inspect

    from factory.llm import LLM
    from factory.pipeline import Factory

    source = class_source(Factory)
    assert 'model=self.config.role(' not in source, \
        "a record stamped from the configuration rather than from what answered"
    assert source.count('model=self._answered_by(') >= 13

    class _Stub:
        config = type("C", (), {"role": staticmethod(
            lambda name: type("R", (), {"model": "configured-model"})())})()
        llm = type("L", (), {"answered": {"oracle": "the-substitute"}})()

    assert Factory._answered_by(_Stub(), "oracle") == "the-substitute"
    assert Factory._answered_by(_Stub(), "spec_writer") == "configured-model", \
        "until something has answered, the configured model is the only answer"

    # And both ways a model can answer record it.
    llm_source = inspect.getsource(LLM)
    assert "self._note_answered(role_name, ran or model)" in llm_source
    assert "self._note_answered(role_name, model)" in llm_source


def test_a_route_that_says_it_is_refusing_calls_is_shown_as_refusing(tmp_path, monkeypatch):
    """On a day its workspace ran dry, every Codex session carried
    `rate_limit_reached_type: workspace_member_credits_depleted` with both
    windows `null`. The gauge read only windows, found none, recorded nothing
    -- and a nineteen-hour-old weekly bar went on standing for a route that was
    refusing every call."""
    import json as _json

    from factory.config import RouteConfig
    from factory.meters import MeterStore, read_limit

    logs = tmp_path / "sessions"
    logs.mkdir()
    route = RouteConfig(
        name="tool", kind="cli", meter_sidecar_glob=str(logs / "rollout-*-{thread_id}.jsonl"),
        meter_observed_key="timestamp", meter_windows_key="payload.rate_limits",
        meter_limit_key="payload.rate_limits.rate_limit_reached_type")

    def write(name: str, reached, ts: str) -> None:
        (logs / f"rollout-x-{name}.jsonl").write_text(_json.dumps({
            "timestamp": ts, "payload": {"rate_limits": {
                "primary": None, "secondary": None,
                "rate_limit_reached_type": reached}}}) + "\n", encoding="utf-8")

    write("a", "workspace_member_credits_depleted", "2026-09-18T16:35:42Z")
    limit = read_limit(route)
    assert limit["reached"] == "workspace_member_credits_depleted"
    assert limit["observed_at"] > 0

    store = MeterStore(tmp_path / "meters.json")
    store.record_limit("tool", limit)
    assert store.limit("tool")["reached"] == "workspace_member_credits_depleted"

    # A later call that succeeds clears it -- and an older reading cannot
    # reinstate a refusal a newer one has cleared.
    import os
    import time
    write("b", None, "2026-09-18T17:00:00Z")
    os.utime(logs / "rollout-x-b.jsonl", (time.time() + 5, time.time() + 5))
    store.record_limit("tool", read_limit(route))
    assert store.limit("tool") == {}, "a success clears the refusal"
    store.record_limit("tool", limit)
    assert store.limit("tool") == {}, "an older refusal cannot come back over a newer success"

    # And the card draws it first, as an alarm, which `creditLine` does not.
    app = app_js()
    assert "${limitLine(r)}${ws.map(" in app


def test_an_oracle_s_helpers_are_kept_and_a_discarded_one_is_named():
    """An oracle found its folder, `card-labels-9840f6`, could not be imported by
    package path, and wrote its helpers into a sibling with a legal name. Only
    its own folder was kept, so the helpers were discarded, four test files
    could not load, and the packet blamed the folder's name -- the list of
    discarded files that would have said so was read by nothing."""

    from factory.pipeline import check_oracle_discards, feature_test_dir
    from factory.store import EvidenceStore

    assert feature_test_dir("card-labels-9840f6") == "card_labels_9840f6"
    assert feature_test_dir("9-lives").startswith("f_"), "never starts with a digit"
    assert feature_test_dir("card_labels").isidentifier()

    root = ROOT / ".pytest-oracle-discards"
    import shutil
    shutil.rmtree(root, ignore_errors=True)
    try:
        suite = root / "api" / "tests" / "acceptance" / "card-labels"
        suite.mkdir(parents=True)
        (suite / "test_add.py").write_text(
            "from tests_acceptance_card_labels.support import add_label\n", encoding="utf-8")
        (suite / "README.md").write_text("Shared support lives beside these.\n",
                                         encoding="utf-8")
        (suite / "test_alone.py").write_text("def test_ok():\n    assert True\n",
                                             encoding="utf-8")
        store = EvidenceStore(root / "evidence", "card-labels")
        store.append("oracle_session", {
            "files": ["api/tests/acceptance/card-labels/test_add.py",
                      "api/tests/acceptance/card-labels/README.md",
                      "api/tests/acceptance/card-labels/test_alone.py"],
            "outside": ["api/tests/acceptance/tests_acceptance_card_labels/__init__.py",
                        "api/tests/acceptance/tests_acceptance_card_labels/support.py"],
        }, role="orchestrator", spec_hash="h")

        found = check_oracle_discards(store, "h", root)
        assert len(found) == 1
        assert found[0].title.startswith("Some independent tests depend on files")
        assert "test_add.py" in found[0].evidence
        assert "README.md" not in found[0].evidence, "prose that mentions a word is not a dependency"
        assert "test_alone.py" not in found[0].evidence

        # Nothing discarded, nothing said.
        store.append("oracle_session", {"files": ["x/test_a.py"], "outside": []},
                     role="orchestrator", spec_hash="h2")
        assert check_oracle_discards(store, "h2", root) == []
    finally:
        shutil.rmtree(root, ignore_errors=True)

    prompt = (ROOT / "factory" / "roles" / "oracle.md").read_text(encoding="utf-8")
    assert "Put your helpers inside your own directory" in prompt


def test_a_resume_rewrites_a_blind_suite_whose_helpers_were_discarded():
    """A resumed card-labels run reused the suite from the last attempt, whose
    helpers had been discarded, and ran every gate against tests the ledger
    already knew could not import. "Verify again" resumes too, and would have
    done the same. The suite is now dropped from the reuse, its files come off
    the branch, and the record says why the oracle ran again."""
    import shutil
    from types import SimpleNamespace

    from factory.pipeline import Factory
    from factory.store import EvidenceStore

    root = ROOT / ".pytest-oracle-rerun"
    shutil.rmtree(root, ignore_errors=True)
    try:
        suite_dir = root / "api" / "tests" / "acceptance" / "card-labels"
        suite_dir.mkdir(parents=True)
        (suite_dir / "test_add.py").write_text(
            "from tests_acceptance_card_labels.support import add_label\n", encoding="utf-8")
        store = EvidenceStore(root / "evidence", "card-labels")
        store.append("oracle_session", {
            "files": ["api/tests/acceptance/card-labels/test_add.py"],
            "outside": ["api/tests/acceptance/tests_acceptance_card_labels/support.py"],
        }, role="orchestrator", spec_hash="h")
        recorded = {"strategy": "", "tests": [{
            "path": "api/tests/acceptance/card-labels/test_add.py",
            "contents": "", "criterion_ids": ["AC-1"], "framework": "pytest"}]}
        state = SimpleNamespace(spec_hash="h")
        sandbox = SimpleNamespace(path=root)

        done = {"oracle": recorded}
        Factory._drop_broken_suite(None, store, state, sandbox, done)
        assert "oracle" not in done, "a suite known not to load is never reused"
        assert not (suite_dir / "test_add.py").exists(), "and does not stay on the branch"
        rerun = [r for r in store.records() if r["kind"] == "oracle_rerun"]
        assert rerun and rerun[-1]["payload"]["removed"] == [
            "api/tests/acceptance/card-labels/test_add.py"]

        # A suite whose last session discarded nothing is reused as before.
        store.append("oracle_session", {
            "files": ["api/tests/acceptance/card-labels/test_add.py"], "outside": [],
        }, role="orchestrator", spec_hash="h")
        done = {"oracle": recorded}
        Factory._drop_broken_suite(None, store, state, sandbox, done)
        assert "oracle" in done
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_failing_check_is_sorted_by_whose_files_it_fails_on(tmp_path):
    """The project's checks now run over this feature's blind tests too, as CI
    does, so one red check can mean three things: a blind test failed (the
    criterion table already says so), a file the oracle wrote breaks a project
    rule (the oracle's to fix), or the rest of the branch is wrong (the
    workers'). Sorted by exit code with files set aside, never by reading what
    a check printed -- and every file is back where it was afterwards."""
    import asyncio
    from types import SimpleNamespace

    from factory.pipeline import Factory, check_attribution, check_oracle_file_failures
    from factory.schemas import Gate, GateReport, GateResult

    suite = ["api/tests/acceptance/f/test_a.py", "api/tests/acceptance/f/helpers.py"]
    for rel in suite:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x\n")

    class Runner:
        carries_setup = True

        async def execute(self, command, *, cwd, timeout_s, network=False):
            there = {rel: (Path(cwd) / rel).exists() for rel in suite}
            fails = {
                "tests": there[suite[0]],          # a failing blind test in the full run
                "lint": there[suite[1]],           # a helper the oracle wrote breaks lint
                "types": True,                     # the implementation itself is wrong
            }[command]
            return gates.Execution(exit_code=1 if fails else 0, output="")

    judging = [Gate(name=n, command=n) for n in ("tests", "lint", "types")]
    fake = SimpleNamespace(project=SimpleNamespace(judging_gates=judging, environment=None))
    report = GateReport(results=[
        GateResult(name=n, command=n, passed=False, exit_code=1) for n in ("tests", "lint", "types")
    ] + [GateResult(name="ok", command="ok", passed=True)])
    blind = GateResult(name="blind-tests", named_failing=[suite[0]])

    asyncio.run(Factory._sort_failures(
        fake, report, blind, suite, SimpleNamespace(path=tmp_path), Runner()))
    got = {r.name: r.failed_on for r in report.results}
    assert got == {"tests": "blind_tests", "lint": "oracle_files", "types": "code", "ok": ""}
    assert all((tmp_path / rel).exists() for rel in suite), "a set-aside file was not put back"

    found = check_oracle_file_failures(report)
    assert len(found) == 1 and found[0].title.endswith("— lint")

    # Neither of the oracle-sorted failures reads as "this feature broke a check".
    for r in report.results:
        if r.failed_on in ("blind_tests", "oracle_files"):
            assert r.at_base == "not_checked"
    assert all(f.id != "broke-tests" and f.id != "broke-lint" for f in check_attribution(report))


def test_the_oracle_is_shown_its_own_errors_and_its_fixes_reach_the_branch(tmp_path, monkeypatch):
    """Whatever agent wrote a bad file is told, so it can fix it. Workers may
    not edit the oracle's files, so a helper that broke the linter or a test
    that would not load used to sit on the branch until CI failed on it. The
    oracle now gets the error, works in a checkout of the base commit holding
    only its own files, and what it changes is written to the branch -- except
    a test it deletes, which keeps its contents: a vanished test takes its
    criteria with it."""
    import asyncio
    import subprocess
    from types import SimpleNamespace

    from factory.executors import Authored
    from factory.pipeline import Factory, oracle_problems
    from factory.schemas import (FileWrite, GateReport, GateResult, OracleSuite,
                                 SupportFile, TestFile)
    from factory.store import EvidenceStore

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("base\n")
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"],
                ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"]):
        subprocess.run(["git", *cmd], cwd=repo, check=True)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()

    own = "api/tests/acceptance/f"
    branch = tmp_path / "branch"
    (branch / own).mkdir(parents=True)
    (branch / own / "test_a.py").write_text("import helpers\n")
    (branch / own / "test_b.py").write_text("def test_b(): assert True\n")
    (branch / own / "helpers.py").write_text("import os\n")
    oracle = OracleSuite(strategy="", tests=[
        TestFile(path=f"{own}/test_a.py", contents="import helpers\n", criterion_ids=["AC-1"]),
        TestFile(path=f"{own}/test_b.py", contents="def test_b(): assert True\n",
                 criterion_ids=["AC-2"]),
    ], support=[SupportFile(path=f"{own}/helpers.py", contents="import os\n")])

    gates_report = GateReport(results=[
        GateResult(name="lint", command="ruff check .", passed=False,
                   failed_on="oracle_files", output_tail=f"{own}/helpers.py:1:8: F401 unused"),
        GateResult(name="types", command="mypy app", passed=False, failed_on="code"),
    ])
    blind = GateResult(name="blind-tests", named_unloadable=[f"{own}/test_a.py"],
                       file_output={f"{own}/test_a.py": "ModuleNotFoundError: helpers"})
    problems = oracle_problems(gates_report, blind, oracle)
    assert "ModuleNotFoundError: helpers" in problems and "F401 unused" in problems
    assert "mypy app" not in problems, "a failure in the code is not the oracle's to fix"

    seen: dict = {}

    async def author(*, role, brief, tree, confine, system="", environment=None,
                     on_environment=None):
        status = subprocess.run(["git", "status", "--porcelain"], cwd=tree,
                                capture_output=True, text=True, check=True).stdout
        assert status == "", (
            "the oracle's files were left uncommitted, so a refused session reads as "
            "having written them all and no fallback is tried")
        seen.update(role=role, brief=brief, confine=confine,
                    files=sorted(p.relative_to(tree).as_posix()
                                 for p in Path(tree).rglob("*.py")))
        (Path(tree) / own / "helpers.py").write_text("")          # emptied: fixed
        (Path(tree) / own / "test_a.py").write_text("from . import helpers\n")
        (Path(tree) / own / "test_b.py").unlink()                  # a test it deleted
        return Authored(files=[
            FileWrite(path=f"{own}/test_a.py", contents="from . import helpers\n", purpose=""),
            FileWrite(path=f"{own}/__init__.py", contents="# package\n", purpose=""),
        ])

    commits: list[str] = []
    sandbox = SimpleNamespace(path=branch, state="sandbox-state", commit=commits.append)
    # Patched where `_revise_blind_suite` looks them up: the module it lives in,
    # not the package's re-export of them.
    from factory.pipeline.factory import assess
    monkeypatch.setattr(assess, "verify_context", lambda *a, **k: "THE SPEC")
    monkeypatch.setattr(assess, "testing_context", lambda *a, **k: "")
    fake = SimpleNamespace(
        config=Config(),
        project=SimpleNamespace(repo_path=repo, state=SimpleNamespace(
            runtime_packages=[], testing=None, runtime_contract=lambda: "")),
        unit_env=lambda *a, **k: None,
        env_recorder=lambda *a, **k: None,
        _own_blind_dirs=lambda state: [own],
        _author_files=author,
        role_prompt=lambda role: "",
        _answered_by=lambda role: "",
        _oracle_guides=lambda store, state: "",
    )
    store = EvidenceStore(tmp_path / "evidence", "f")
    state = SimpleNamespace(spec_hash="h", feature_id="f", rework_round=1,
                            sandbox=SimpleNamespace(base_sha=base))

    changed = asyncio.run(Factory._revise_blind_suite(
        fake, store, state, None, sandbox, oracle, problems))

    assert seen["role"] == "oracle" and seen["confine"] == [own]
    assert seen["brief"].startswith("THE SPEC") and "F401 unused" in seen["brief"]
    assert f"{own}/test_b.py" in seen["files"], "the oracle was not given its files as they are"
    assert not any(p.startswith("app/") for p in seen["files"])

    assert sorted(changed) == [f"{own}/__init__.py", f"{own}/test_a.py"]
    assert (branch / own / "test_a.py").read_text() == "from . import helpers\n"
    assert (branch / own / "test_b.py").exists(), "a deleted test took its criteria with it"
    assert oracle.tests[0].contents == "from . import helpers\n"
    assert any(f.path == f"{own}/__init__.py" for f in oracle.support)
    assert len(commits) == 1
    assert [r["kind"] for r in store.records()] == ["oracle", "oracle_revision"], \
        "the suite it left is recorded, so a resume does not go back to the first one"
    assert store.records()[0]["payload"]["tests"][0]["contents"] == "from . import helpers\n"


def test_the_surveyor_is_told_report_is_an_absolute_path():
    """Every kanban blind run read zero per-test results. The surveyor had
    been told `{report}` takes the same `../` correction as `{path}`, wrote
    `--junitxml=../{report}` -- but `{report}` is always filled with an absolute
    path, so the report went to a directory that does not exist, and every
    test in a file shared one verdict without anything saying so."""
    prompt = (ROOT / "factory" / "roles" / "surveyor.md").read_text(encoding="utf-8")
    assert "same `../` correction.\n" not in prompt
    assert "`{report}` is filled with an absolute path" in prompt
    assert "never prefix it" in schemas.TestFileCommand.model_fields["report"].description


def test_a_finding_about_how_a_blind_test_is_written_goes_to_the_oracle():
    """Card-labels: seven blind files would not load for want of one package
    marker. The arbiter escalated the computed findings and said the fix
    belonged to the oracle -- but there was no such route, so reviewers'
    restatements went to workers as repairs, workers were refused the
    protected files, and the refusals became blockers of their own. Now a
    finding about how a blind test is written is routed to the oracle, which
    fixes its own file; the agent that raised it re-checks, as for a repair."""
    from factory.pipeline import FindingLedger
    from factory.schemas import Finding, FindingDisposition

    assert "oracle" in schemas.Disposition.__args__
    ledger = FindingLedger()
    f = Finding(id="r-1", title="Seven blind files would not load", severity="blocker",
                category="verification", detail="relative import, no package",
                evidence="ImportError: attempted relative import")
    dup = Finding(id="r-2", title="The suite cannot collect", severity="blocker",
                  category="verification", detail="same thing")
    ledger.add("reviewer", [f, dup], 0, keep_ids=True)
    ledger.dispose([
        FindingDisposition(finding_id="r-1", disposition="oracle", reason="its own file"),
        FindingDisposition(finding_id="r-2", disposition="oracle", reason="same",
                           duplicate_of="r-1"),
    ])
    assert ledger.for_oracle(2) == ["r-1"], "a restatement is not sent twice"
    assert ledger.repairable(2) == [], "a finding about the oracle's files never reaches a worker"

    ledger.attempted(["r-1"], 1)
    ledger.attempted(["r-1"], 2)
    assert ledger.for_oracle(2) == [], "tried as often as a repair may be"
    ledger.finalise(stopped_early=False)
    assert ledger.records["r-1"].outcome == "attempted_not_fixed"

    # The loop keeps going while the oracle has work, and its findings are
    # re-checked by whoever raised them.
    src = inspect.getsource(pipeline.Factory._converge)
    assert "+ ledger.for_oracle(cfg.max_attempts_per_finding))" in src, (
        "a finding about a blind test has to reach the round that fixes it")

    # And the arbiter is told what the route is for, and what it is not for.
    text = " ".join((PACKAGE / "roles" / "arbiter.md").read_text().split())
    assert "**`oracle`**" in text
    assert "Never for what a test *asserts*, on your own judgment" in text
    # The one exception, which is the human's judgment rather than a model's.
    assert "raised by: human" in text
    assert "unlocks for the unit that fixes it" in text


def test_the_oracle_s_fix_sessions_are_counted_per_round_and_only_when_they_ran(tmp_path):
    """Card-labels: two fix sessions went to a route out of credits and did
    nothing, and the allowance was counted over the feature's life and per
    attempt -- so the next run, with a working route, never asked the oracle
    at all. Fifteen findings routed to it sat open, and seven test files one
    package marker would have fixed stayed broken for three rounds.

    Counted per round now, not per run. The oracle owns the test tree the way
    a repairer owns the code; an allowance that runs out mid-feature turns a
    route into a dead end, and findings arrive at an agent nobody is asking
    anything. The round cap bounds how many rounds can buy one."""
    from types import SimpleNamespace

    from factory.pipeline import Factory
    from factory.store import EvidenceStore

    store = EvidenceStore(tmp_path / "e", "f")
    for _ in range(2):   # the last run's two, both refused
        store.append("oracle_revision", {"ran": False, "changed": []}, spec_hash="h")
    floor = store.records()[-1]["seq"]
    fake = SimpleNamespace(
        harness_authoring=lambda: True, _run_floor={"f": floor},
        config=SimpleNamespace(pipeline=SimpleNamespace(oracle_revisions=2)))
    state = SimpleNamespace(feature_id="f", spec_hash="h", rework_round=1)
    may = lambda: Factory._may_revise_oracle(fake, store, state)
    ran = lambda ok, rnd: store.append(
        "oracle_revision", {"ran": ok, "changed": [], "round": rnd}, spec_hash="h")

    assert may(), "an earlier run's sessions do not count against this one"
    ran(False, 1)
    assert may(), "a session the route refused was never asked, so it spends nothing"
    ran(True, 1)
    assert may()
    ran(True, 1)
    assert not may(), "two sessions that ran are this round's allowance"

    # The next round opens it again. This is the dead end that closed: under
    # the old count the oracle went quiet for the rest of the feature, and
    # everything routed to it simply stopped moving.
    state.rework_round = 2
    assert may(), "a new round is a new allowance, or the route dies mid-feature"
    ran(True, 2)
    ran(True, 2)
    assert not may()
    state.rework_round = 3
    assert may()

    # And the arbiter is told when the route is closed, and held to it.
    src = inspect.getsource(Factory._arbitrate)
    assert "oracle_open = self._may_revise_oracle(store, state)" in src
    assert "`oracle` is not available this run" in src
    assert '"disposition": "escalate"' in src
    assert "_run_floor" in inspect.getsource(Factory.run_build)


def test_the_oracle_s_two_jobs_are_two_sections_of_its_brief():
    """An error in a file and a person correcting the contract are different
    kinds of instruction, and exactly one of them may change what a test
    asserts.

    Merged into one section, the second reads as permission to soften a test
    whenever something is failing -- which is the whole thing the guardrail
    exists to stop, and the guardrail is why the oracle is allowed to write
    in that tree at all."""
    source = inspect.getsource(pipeline.Factory._revise_blind_suite)
    mechanical = source.index("Some of your files need fixing")
    correction = source.index("Corrections from the person who owns this contract")
    assert mechanical < correction, "the errors come first; the override reads after the rule"

    # The guardrail lives with the errors, where nothing may move an assertion.
    keep = source.index("Keep every assertion meaning what it means")
    assert mechanical < keep < correction, (
        "the rule moved out of the section it governs")

    # And the override is bounded: one named test, nothing else touched, the
    # move recorded, and a vague note refused rather than guessed at.
    after = source[correction:]
    for bound in ("Change what the named test asserts",
                  "Touch no other assertion",
                  "Say what it meant before and what it means now",
                  "nothing for it and say so"):
        assert bound in after, f"the correction section lost its bound: {bound}"
    assert 'is not a correction -- it is a ' in after, (
        "a note that only points must not authorise rewriting an acceptance test")

    # Neither section is written when it has nothing in it: an oracle handed
    # an empty heading invents something to put under it.
    assert 'if problems:' in source and 'if corrections:' in source


def test_a_blind_test_failing_inside_an_oracle_helper_is_raised_for_the_arbiter():
    """Two card-labels tests failed on `os.environ["OWNER_DATABASE_URL"]` in the
    oracle's own support.py, a variable the project never sets, and nothing
    said so unless a reviewer happened to. It is now raised every time -- and
    only raised: an error in a helper can be the feature's fault (a field it
    does not return), so the arbiter decides whose it is."""
    from factory.pipeline import check_blind_helper_errors
    from factory.schemas import GateReport, GateResult, OracleSuite, SupportFile, TestFile

    own = "api/tests/acceptance/card_labels_9840f6"
    oracle = OracleSuite(strategy="", tests=[
        TestFile(path=f"{own}/test_add.py", contents="", criterion_ids=["AC-1"]),
        TestFile(path=f"{own}/test_scope.py", contents="", criterion_ids=["AC-2"]),
        TestFile(path=f"{own}/test_load.py", contents="", criterion_ids=["AC-3"]),
    ], support=[SupportFile(path=f"{own}/support.py", contents="")])
    blind = GateResult(
        name="blind-tests",
        named_failing=[f"{own}/test_add.py", f"{own}/test_scope.py", f"{own}/test_load.py"],
        named_unloadable=[f"{own}/test_load.py"],
        file_output={
            f"{own}/test_add.py": (
                "tests/acceptance/card_labels_9840f6/support.py:71: in label_rows_for_card\n"
                "E   KeyError: 'OWNER_DATABASE_URL'"),
            f"{own}/test_scope.py": (
                "tests/acceptance/card_labels_9840f6/test_scope.py:30: AssertionError\n"
                "E   assert 404 == 200"),
            f"{own}/test_load.py": (
                "tests/acceptance/card_labels_9840f6/support.py:3: ImportError"),
        })
    found = check_blind_helper_errors(GateReport(results=[blind]), oracle)
    assert len(found) == 1
    assert "test_add.py" in found[0].detail
    assert "test_scope.py" not in found[0].detail, "an assertion in the test itself is the table's"
    assert "test_load.py" not in found[0].detail, "a file that never loaded is already raised"
    assert "OWNER_DATABASE_URL" in found[0].evidence
    assert found[0].files == [f"{own}/support.py"]

    src = inspect.getsource(pipeline.Factory._restate_from_checks)
    assert "check_blind_helper_errors(gates, oracle)" in src
    text = (PACKAGE / "roles" / "arbiter.md").read_text()
    assert "`blind-helper-`" in text


def test_the_review_panel_reads_the_blind_tests_as_they_are_now(tmp_path):
    """The blind tests are kept out of `written`, so the panel's only copy of
    them was the digest. It gets them from the branch, as they are now."""
    from factory.schemas import GateReport, IntegrationReport, Plan, QAReport

    _, sandbox, conftest = _resumed_sandbox(tmp_path)
    conftest.write_text("await connection.run_sync(Base.metadata.drop_all)\n")
    sandbox.commit("factory: the oracle fixed its own files")
    spec = Spec(title="Card labels", intent="labels", summary="labels on cards",
                acceptance_criteria=[AcceptanceCriterion(id="AC-1", statement="labels show")])

    block = pipeline.Factory._evidence_block(
        None, spec, Plan(summary="p"), [], IntegrationReport(summary="i"),
        GateReport(), QAReport(summary="q"), [], "(digest)", [], sandbox=sandbox,
        blind=["api/tests/acceptance/card_labels/conftest.py"])

    assert "as they are on the branch now" in block
    assert "metadata.drop_all" in block and "DROP SCHEMA" not in block


def test_a_resume_loads_the_oracle_suite_as_its_last_fix_left_it():
    """An oracle fix changed the suite in memory only, and a resume rebuilds it
    from the latest `oracle` record -- so every resume went back to the suite
    as first written, and to none of the helpers a fix had added."""
    src = class_source(pipeline.Factory)
    fix = src[src.index("the oracle fixed its own files"):]
    fix = fix[:fix.index("store.append(\"oracle_revision\"")]
    assert 'store.append("oracle", oracle' in fix, \
        "a fix that changed files records the suite it left"

    records = [
        {"seq": 1, "kind": "plan", "payload": {"units": []}, "spec_hash": "h"},
        {"seq": 2, "kind": "oracle", "payload": {"strategy": "first"}, "spec_hash": "h"},
        {"seq": 3, "kind": "oracle", "payload": {"strategy": "fixed"}, "spec_hash": "h"},
    ]
    assert pipeline.recorded_attempt(records, "h")["oracle"] == {"strategy": "fixed"}


def test_a_computed_finding_closes_when_its_check_stops_finding_it():
    """blind-helper-1 was raised in round 0. The oracle fixed its helper and all
    29 blind tests passed in rounds 1 and 2, and the packet still led with it as
    a blocker: `restate` updated what a check found and did nothing about what
    it stopped finding."""
    ledger = pipeline.FindingLedger()
    ledger.restate("computed", [_helper_error()], 0, source="blind_helper_errors")
    ledger.records["blind-helper-1"].outcome = "escalated"

    ledger.restate("computed", [], 1, source="blind_helper_errors")

    record = ledger.records["blind-helper-1"]
    assert record.outcome == "no_longer_holds"
    assert "round 1" in record.outcome_evidence and "blind_helper_errors" in record.outcome_evidence
    assert record.outcome not in pipeline.OPEN_OUTCOMES, "not in front of a human"
    assert "blind-helper-1" in ledger.findings, "still in the packet, with what became of it"

    ledger.restate("computed", [_helper_error("KeyError: 'OTHER'")], 2,
                   source="blind_helper_errors")
    assert record.outcome == "open", "a condition that comes back is open again"
    assert "round 2" in record.outcome_evidence


def test_a_check_that_did_not_look_again_closes_nothing():
    """An empty answer from a check with no fresh evidence is not an answer."""
    ledger = pipeline.FindingLedger()
    ledger.restate("computed", [_helper_error()], 0, source="blind_helper_errors")

    ledger.restate("computed", [], 1, source="blind_helper_errors", fresh=False)
    assert ledger.records["blind-helper-1"].outcome == "open"

    ledger.restate("computed", [], 1)
    assert ledger.records["blind-helper-1"].outcome == "open", \
        "and a caller that names no check closes nothing either"


def test_a_check_closes_only_its_own_findings_and_not_what_a_person_settled():
    ledger = pipeline.FindingLedger()
    ledger.restate("computed", [_helper_error()], 0, source="blind_helper_errors")
    ledger.restate("computed", [Finding(
        id="setup-1", severity="major", category="environment", title="setup failed",
        detail="d", evidence="e", recommendation="r")], 0, source="setup")
    ledger.records["setup-1"].outcome = "dismissed"

    ledger.restate("computed", [], 1, source="setup")

    assert ledger.records["blind-helper-1"].outcome == "open", "another check's finding"
    assert ledger.records["setup-1"].outcome == "dismissed", "an agent's ruling stands"


def test_a_ledger_saved_before_findings_knew_their_check_still_closes():
    """The ledger this was found on was written before records carried their
    check, and it is loaded on every later dispatch -- so a finding the check
    has always raised under one id is taken as that check's own."""
    ledger = pipeline.FindingLedger()
    ledger.add("computed", [_helper_error()], 0, keep_ids=True)
    ledger.records["blind-helper-1"].outcome = "escalated"
    ledger.add("computed", [Finding(
        id="attribution-1", severity="major", category="verification", title="wiring",
        detail="d", evidence="e", recommendation="r")], 0, keep_ids=True)
    saved = pipeline.FindingLedger.load(ledger.dump())
    assert saved.records["blind-helper-1"].source == ""

    saved.restate("computed", [], 1, source="blind_helper_errors")
    saved.restate("computed", [], 1, source="blind_attribution")

    assert saved.records["blind-helper-1"].outcome == "no_longer_holds"
    assert saved.records["blind-helper-1"].source == "blind_helper_errors"
    assert saved.records["attribution-1"].outcome == "open", \
        "two checks raise attribution-1, so neither may close it for the other"
    assert pipeline.FindingLedger.load(saved.dump()).records["blind-helper-1"].source \
        == "blind_helper_errors", "the check is saved with the record"


def test_every_computed_check_restated_each_round_names_itself():
    src = class_source(pipeline.Factory)
    calls = src.split('ledger.restate("computed"')[1:]
    assert len(calls) == 19
    assert all("source=" in call[:260] for call in calls), \
        "a restate without its check can never close anything"
    # Read off the checks in one place, so each round and the final pass say
    # the same things about the same run.
    converge = inspect.getsource(pipeline.Factory._converge)
    assert converge.count("self._restate_from_checks(") == 2


def test_a_fallback_route_s_harness_is_layered_into_the_unit_image(tmp_path, monkeypatch):
    """The breaker's route was codex, out of credit, so every session fell back
    to the default route -- whose harness is /opt/harness/venv. The unit image
    had been layered for codex alone, and every breaker session of every round
    died with `stat /opt/harness/venv/bin/python: no such file or directory`."""
    from types import SimpleNamespace
    from factory.schemas import EnvironmentSpec

    codex = _in_container_route("codex", "npm i -g codex", "codex")
    default = _in_container_route("default", "python3 -m venv /opt/harness/venv",
                                  "/opt/harness/venv/bin/python")
    runner = SimpleNamespace(image="fabrika/kanban:abc")
    built: list[tuple[str, str]] = []

    async def fake_runner_for(project, config, feature_id=""):
        return runner, None

    async def fake_harness_image(base, route, docker):
        built.append((base, route.name))
        return f"{base}+{route.name}"

    class Reached(Exception):
        pass

    def fake_session(*args, **kwargs):
        raise Reached

    monkeypatch.setattr(unitenv, "runner_for", fake_runner_for)
    monkeypatch.setattr(unitenv, "harness_image", fake_harness_image)
    monkeypatch.setattr(unitenv, "test_session", fake_session)
    project = SimpleNamespace(environment=EnvironmentSpec(kind="derive"))

    async def open_it(**routes):
        built.clear()
        async with unitenv.unit_environment(project, Config(), label="x", tree=tmp_path,
                                            prepare=False, **routes):
            pass

    with pytest.raises(Reached):
        asyncio.run(open_it(harness_route=codex, fallback_route=default))
    assert built == [("fabrika/kanban:abc", "codex"),
                     ("fabrika/kanban:abc+codex", "default")], \
        "the fallback's harness on top of the route's own"
    assert runner.image == "fabrika/kanban:abc+codex+default"

    runner.image = "fabrika/kanban:abc"
    with pytest.raises(Reached):
        asyncio.run(open_it(harness_route=codex, fallback_route=codex))
    assert built == [("fabrika/kanban:abc", "codex")], "one route, one layer"


def test_the_executor_and_the_unit_image_agree_on_the_fallback():
    """Two copies of "which route does this agent fall back to" is how the
    fallback came to run in a container that did not have it."""
    exe = inspect.getsource(executors.CommandExecutor._fallback_session)
    assert "session_fallback(self.config, role)" in exe
    assert "session_fallback(self.config, role)" in inspect.getsource(pipeline.Factory.unit_env)
    assert "fallback_route=fallback" in inspect.getsource(pipeline.Factory.unit_env)


def test_a_harness_that_never_started_is_not_reported_as_one_that_wrote_nothing():
    from factory.executors import harness_unstarted
    from factory.schemas import BreakerReport, BreakerSuite

    log = ('OCI runtime exec failed: exec failed: unable to start container process: exec: '
           '"/opt/harness/venv/bin/python": stat /opt/harness/venv/bin/python: '
           'no such file or directory\r\n')
    why = harness_unstarted(log)
    assert why.startswith("OCI runtime exec failed") and "/opt/harness/venv/bin/python" in why
    assert harness_unstarted("I read the code and found nothing to attack.") == ""

    findings = pipeline.breaker_findings(
        BreakerSuite(strategy=""), BreakerReport(ran=False, unstarted=why))
    assert [f.id for f in findings] == ["breaker-unstarted"]
    assert why in findings[0].evidence
    assert pipeline.breaker_findings(BreakerSuite(strategy=""), BreakerReport(ran=False)) == []

    src = class_source(pipeline.Factory)
    assert "the breaker could not start" in src


def test_a_seam_check_is_a_file_the_integrator_left_not_something_it_said():
    """Asked afterwards which commands it had run, the integrator's answer came
    from a fresh model that had not been in the session: "placeholder". It had
    loaded the seam in a live browser, and was recorded as having checked
    nothing. A check is now a file on disk, which the factory runs."""
    from factory.executors import NO_EDITS
    from factory.schemas import FileWrite

    done = pipeline.settle_integration(
        _integration(files=[_seam(),
                            _seam("web/e2e/cardLabels.seam.spec.ts", "test('seam', () => {})")],
                     seam_issues=[], unresolved=[]), "seam")
    assert done.files == [], "the checks are not the feature's edits"
    assert [f.path for f in done.seam_files] == ["api/tests/test_seam_card_labels.py",
                                                 "web/e2e/cardLabels.seam.spec.ts"]
    assert done.unresolved == [] and NO_EDITS not in done.seam_issues
    assert pipeline.integration_detail(done) == "0 file(s) touched, 2 seam check(s)"

    edited = pipeline.settle_integration(_integration(
        files=[FileWrite(path="web/src/types.ts", contents="x")], seam_issues=[],
        unresolved=[]), "seam")
    assert edited.unresolved == [pipeline.NO_SEAM_CHECK], "editing is not checking"

    assert "_seam_account" not in class_source(pipeline.Factory), \
        "nobody is asked from memory what they ran"


def test_a_seam_check_is_known_by_its_name_because_the_directory_is_shared():
    """`tests/seams/` was a directory this tool invented, and the checks in it
    were deleted at the end of every run. They are ordinary integration tests
    in the project's own test tree now -- shared with the project and with
    every feature built here before -- so the name is what marks them."""
    from factory.schemas import FileWrite

    assert pipeline.is_seam_check("api/tests/test_seam_tags.py", "seam")
    assert pipeline.is_seam_check("web/e2e/Cards.Seam.test.ts", "seam"), \
        "the marker is matched without regard to case"
    assert not pipeline.is_seam_check("tests/seams/helper.py", "seam"), \
        "a directory called seams does not make somebody else's file a check"
    assert not pipeline.is_seam_check("api/tests/test_tags.py", "seam")
    assert not pipeline.is_seam_check("api/tests/test_seam_tags.py", ""), \
        "no marker configured cannot mean every file is a check"

    mixed = pipeline.settle_integration(
        _integration(files=[_seam(),
                            FileWrite(path="api/tests/support_labels.py", contents="x")],
                     seam_issues=[], unresolved=[]), "seam")
    assert [f.path for f in mixed.files] == ["api/tests/support_labels.py"], \
        "a helper that asserts nothing is an ordinary edit, not a check"
    assert [f.path for f in mixed.seam_files] == ["api/tests/test_seam_card_labels.py"]


def test_the_integrator_is_asked_again_before_the_run_goes_on_without_a_seam_check():
    """The absence used to reach a human an hour later as a MAJOR in the packet
    -- a question only the integrator could answer, asked of the one person who
    could not. It is asked of the integrator instead, once, and if it still
    leaves nothing the line stops rather than carrying an unprovable
    integration into review."""
    src = inspect.getsource(pipeline.Factory._integrate)
    first = src.index("integration = await attempt(brief)")
    body = src[first + len("integration = await attempt(brief)"):]
    assert "await attempt(" in body, \
        "the second ask is gone: a missing seam check reaches review as somebody else's call"
    assert "if not seam_checks(integration)" in body
    assert "raise HarnessBlocked(" in body, \
        "a run with nothing showing the seams hold goes on to review anyway"
    assert body.index("raise HarnessBlocked(") > body.rindex("await attempt("), \
        "the line stops before the integrator has been asked a second time"


def test_a_resume_keeps_the_integration_like_the_rest_of_the_build():
    """"Verify again" re-ran the integrator whenever its record showed no seam
    check -- eleven minutes and $0.36 on every verify, to redo build work."""
    src = inspect.getsource(pipeline.Factory._build)
    lane = src[src.index("async def build_lane():"):src.index("async def verify_lane():")]
    assert "self._integrate(" not in lane
    assert 'await reused("integrator", integration_detail(integration))' in lane
    assert "seams already closed" not in class_source(pipeline.Factory)


def test_seam_checks_run_by_the_projects_own_rule_for_running_one_test_file():
    """`sh {path}` was a command this tool chose for a file an agent wrote
    without being told which shell would run it. On an image where `sh` is
    dash, all ten seam checks died at their own `set -o pipefail` having tested
    nothing, and the ten identical failures reached a human as a BLOCKER about
    the feature. Nothing here decides how to run a test any more."""
    from factory.schemas import GateReport, GateResult, TestFileCommand

    rules = [TestFileCommand(match="*.py", command="cd api && pytest ../{path}"),
             TestFileCommand(match="*.ts", command="npx playwright test {path}")]
    checked = _integration(unresolved=[], seam_issues=[], seam_files=[_seam()])
    gates = pipeline.seam_gates(checked, rules)
    assert [(g.name, g.command) for g in gates] == [
        ("seam-test_seam_card_labels",
         "cd api && pytest ../api/tests/test_seam_card_labels.py")]
    assert pipeline.seam_gates(None, rules) == []
    assert pipeline.seam_gates(checked, []) == [], \
        "no rule matching a check means no command for it, and this invents none"
    body = inspect.getsource(pipeline.seam_gates)
    body = body[body.rindex('"""') + 3:]
    assert "sh " not in body and "rule.command.replace" in body, \
        "the shell this tool guessed at is back; the command comes from the project"

    ran = "cd api && pytest ../api/tests/test_seam_card_labels.py"
    green = GateReport(results=[GateResult(name="seam-test_seam_card_labels",
                                           command=ran, passed=True)])
    red = GateReport(results=[GateResult(name="seam-test_seam_card_labels", command=ran,
                                         exit_code=2, passed=False, output_tail="TS2339")])
    assert pipeline.check_seams(checked, green) == []
    [failing] = pipeline.check_seams(checked, red)
    assert failing.id == "seams-failing" and failing.severity == "blocker"
    assert "TS2339" in failing.evidence
    assert failing.files == ["api/tests/test_seam_card_labels.py"], \
        "the finding names the test file, not whatever the command line happened to be"

    [unchecked] = pipeline.check_seams(_integration(), green)
    assert unchecked.id == "seams-unchecked" and unchecked.severity == "major"
    assert "not a claim that a seam is broken" in unchecked.detail

    src = class_source(pipeline.Factory)
    # Each round, the re-assessment after a revert, after simplifying, and the
    # final pass.
    assert src.count("seams=integration.seam_files") == 4, "every assessment runs them"
    assert pipeline.is_protected("api/tests/test_seam_card_labels.py", [], Config()), \
        "a repair may not edit the check that judges it"


def test_a_seam_check_stays_on_the_branch_instead_of_being_deleted_after_the_run():
    """They used to be written into the tree and removed in a `finally`, the
    way the breaker's probes are. A check that shows two units agree is exactly
    what the repository wants to keep, and deleting it meant the next feature
    started with no cross-unit coverage and wrote it again from scratch."""
    runner = inspect.getsource(pipeline.Factory._run_seam_checks)
    assert "unlink" not in runner and "rmdir" not in runner, \
        "the checks are deleted again at the end of the round"
    assert "if target.exists():" in runner, \
        "a check on the branch is overwritten from a recorded copy"

    build = inspect.getsource(pipeline.Factory._build)
    assert "integration.seam_files" in build, \
        "the checks never reach the branch, so nothing commits them"


def test_a_project_check_failing_on_a_seam_check_is_not_blamed_on_the_branch():
    """The project's own suite collects these now -- they are ordinary tests in
    its own test tree. Without this pass a red `api-tests` caused by a test the
    integrator wrote sorts to `code` and reaches a human as a defect in the
    implementation, which is the exact misattribution `failed_on` exists to
    prevent."""
    from factory.schemas import GateResult

    assert GateResult(name="x", failed_on="seam_tests").failed_on == "seam_tests"

    src = inspect.getsource(pipeline.Factory._sort_failures)
    assert '"seam_tests", remaining' in src, "seam checks are never set aside"
    assert src.index('"seam_tests"') < src.index('"oracle_files" if'), \
        "narrowest cause first, or a seam failure is blamed on the wider set"

    assess = inspect.getsource(pipeline.Factory._assess)
    assert assess.count("[f.path for f in seams]") == 2, \
        "a re-assessment sorts without knowing which files are seam checks"

    assert "seam_tests" in inspect.getsource(pipeline.gate_status_line), \
        "the run's own gate table cannot say whose failure it is"


def test_a_seam_check_runs_where_it_lives_and_is_still_there_afterwards(tmp_path):
    """It used to be written into the tree for the length of the round and
    removed in a `finally`, so it could never be committed. It is an ordinary
    integration test in the project's own tree now: it runs where it lives and
    it stays, and what the run writes is only what a resume is missing."""
    from types import SimpleNamespace
    from factory.schemas import GateReport, GateResult, TestFileCommand

    check = _seam()
    seen = {}

    async def fake_run_gates(gates, root, runner):
        seen["exists"] = (root / check.path).is_file()
        return GateReport(results=[GateResult(name=g.name, command=g.command, passed=True)
                                   for g in gates])

    me = SimpleNamespace(
        config=Config(),
        project=SimpleNamespace(state=SimpleNamespace(
            test_file_commands=[TestFileCommand(match="*.py", command="pytest {path}")])))

    # Swapped where `_run_seam_checks` looks it up: the module it lives in.
    from factory.pipeline.factory import assess
    original = assess.run_gates
    assess.run_gates = fake_run_gates
    try:
        results = asyncio.run(pipeline.Factory._run_seam_checks(
            me, SimpleNamespace(path=tmp_path), None, [check]))
    finally:
        assess.run_gates = original
    assert seen["exists"], "on disk while it runs"
    assert [r.name for r in results] == ["seam-test_seam_card_labels"]
    assert (tmp_path / check.path).is_file(), \
        "deleted again after the round, so the repository never keeps it"

    # What is on the branch wins: a recorded copy never overwrites it.
    (tmp_path / check.path).write_text("def test_seam():\n    assert True  # repaired\n")
    assess.run_gates = fake_run_gates
    try:
        asyncio.run(pipeline.Factory._run_seam_checks(
            me, SimpleNamespace(path=tmp_path), None, [check]))
    finally:
        assess.run_gates = original
    assert "repaired" in (tmp_path / check.path).read_text(), \
        "the branch was rewritten from a copy recorded before it changed"


def test_a_resume_does_not_put_back_a_seam_check_recorded_in_the_old_shape(tmp_path):
    """Features built before seam checks became real tests recorded
    `tests/seams/*.sh`. Nothing in any project collects a shell script, and on
    a project whose per-file rules end in a catch-all every one of them would
    be handed to pytest -- ten dead files committed to the branch and ten red
    gates for a new wrong reason. A resume leaves them alone."""
    from types import SimpleNamespace
    from factory.schemas import FileWrite, GateReport, GateResult, TestFileCommand

    old_shape = FileWrite(path="tests/seams/_lib.sh", contents="set -o pipefail\n")
    me = SimpleNamespace(
        config=Config(),
        project=SimpleNamespace(state=SimpleNamespace(
            # A catch-all, as a real project's last rule is: this would match
            # the shell script happily and run it with pytest.
            test_file_commands=[TestFileCommand(match="*", command="pytest {path}")])))

    async def fake_run_gates(gates, root, runner):
        return GateReport(results=[GateResult(name=g.name, command=g.command, passed=True)
                                   for g in gates])

    # Swapped where `_run_seam_checks` looks it up: the module it lives in.
    from factory.pipeline.factory import assess
    original = assess.run_gates
    assess.run_gates = fake_run_gates
    try:
        results = asyncio.run(pipeline.Factory._run_seam_checks(
            me, SimpleNamespace(path=tmp_path), None, [old_shape]))
    finally:
        assess.run_gates = original

    assert results == [], "the old shape was run as though it were a seam check"
    assert not (tmp_path / old_shape.path).exists(), \
        "a dead shell script was written onto the branch, where it will be committed"

    # And the packet says so. Without the same discount here the guard sees
    # checks, finds no failing gate because none of them ran, and reports
    # nothing -- a silent green over a seam nobody measured, which is the one
    # outcome worse than a red one.
    from factory.schemas import GateReport as GR
    stale = _integration(unresolved=[], seam_issues=[], seam_files=[old_shape])
    assert pipeline.check_seams(stale, GR(results=[])) == [], \
        "without a marker this cannot tell the shapes apart, and must not guess"
    [absent] = pipeline.check_seams(stale, GR(results=[]), "seam")
    assert absent.id == "seams-unchecked", \
        "a record nothing can run is counted as a check, and the absence goes unreported"


def test_the_breaker_writes_where_the_project_s_runners_work_and_runs_file_by_file():
    """Card-labels: the breaker was told `tests/breaker/`, which no runner in
    the project reads. It wrote eight probes where they would run instead; all
    eight were discarded as outside its directory, and every round pointed the
    runner at a folder that did not exist -- exit 4, nothing ran, "1 probe,
    1 failing". Probes now go in the project's proved directories, in a folder
    of their own, and run one file at a time by the project's own rules."""
    from types import SimpleNamespace

    from factory.pipeline import Factory
    from factory.schemas import BlindPlacement, PlacementResult

    project = SimpleNamespace(state=SimpleNamespace(
        blind_placements=[
            BlindPlacement(directory="api/tests/acceptance", filename="test_x.py",
                           canary_passes="", canary_fails=""),
            BlindPlacement(directory="web/acceptance", filename="X.test.tsx",
                           canary_passes="", canary_fails="")],
        placement_probe=[PlacementResult(directory="api/tests/acceptance", subdirs_ok=True),
                         PlacementResult(directory="web/acceptance", subdirs_ok=True)]))
    targets = Factory._breaker_targets(SimpleNamespace(project=project),
                                       SimpleNamespace(feature_id="card-labels-9840f6"))
    assert targets == [(".py", "api/tests/acceptance/card_labels_9840f6_breaker"),
                       (".tsx", "web/acceptance/card_labels_9840f6_breaker")]

    probes = inspect.getsource(Factory._author_probes)
    assert "confine=confine" in probes and "cfg.breaker_dir}/`. Create it" not in probes
    run = inspect.getsource(Factory._run_breaker)
    assert run.index("if per_file:") < run.index("await runner.execute(")
    assert "a different file is already there" in run, (
        "a probe never overwrites a file it did not write")
    # And the file it *did* write is not mistaken for someone else's. A
    # promoted probe is on the branch, so the guard met a file at its path,
    # refused the write, dropped the probe and reported that the breaker's
    # probes could not be run -- a round that ran nothing because a probe had
    # passed. Byte-identical content is what tells the two cases apart.
    assert "same = here.read_text(encoding=\"utf-8\") == write.contents" in run
    assert "set(applied) | set(ours)" in run, "a probe already in place still ran"
    # Two probes, two real bugs, both failing: the runner's report says a test
    # ran and failed, so they are findings rather than a suspected broken runner.
    assert 'c.status == "failed" for c in result.cases' in run


def test_every_command_the_repository_declares_is_a_check():
    """A repository's validation commands are what a build runs, wherever the
    repository declares them. Kanban declares `test:e2e` in package.json;
    CI never calls it, so it was never a check, and the browser tests every
    feature added were run once at build time and never again."""
    prompt = (ROOT / "factory" / "roles" / "surveyor.md").read_text(encoding="utf-8")
    flat = " ".join(prompt.split())
    assert "wherever it is declared" in flat
    assert "CI is one of those places, not the reference" in flat
    assert "exactly as the repository defines it" in flat
    assert "as CI runs it" not in flat and "what CI actually runs" not in flat
    assert "A command that rewrites files is declared too" in flat
    assert "Never recommend running a command the repository already declares" in flat
    assert "A command the repository declares that no check runs is always an `add`" in flat
    # The user level is a surface driven for real -- a browser, or the running API.
    assert "an HTTP client against the running API" in flat
    assert "run_by" in schemas.TestingTier.model_fields


def test_each_check_is_a_fixer_a_light_check_or_a_heavy_check(tmp_path):
    """A fixer rewrote files at baseline and runs first every round. Anything
    else is light unless it took longer than the threshold -- measured, never
    guessed from a name -- or a person chose otherwise, and that choice
    survives a re-reading because it is kept apart from the check list."""
    from factory.projects import ProjectRegistry
    from factory.schemas import Gate, GateReport, GateResult

    registry = ProjectRegistry(tmp_path / "evidence")
    repo = tmp_path / "repo"
    repo.mkdir()
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    project = registry.create(repo, "p")
    project.state.gates = [Gate(name=n, command=n) for n in ("format", "lint", "e2e", "types")]
    project.state.baseline = GateReport(results=[
        GateResult(name="format", passed=True, duration_s=1.0, rewrote=["a.py"]),
        GateResult(name="lint", passed=True, duration_s=0.5),
        GateResult(name="e2e", passed=True, duration_s=300.0),
        GateResult(name="types", passed=True, duration_s=8.0),
    ])
    registry.save(project)
    assert project.check_kinds(120.0) == {
        "format": "fixer", "lint": "light", "e2e": "heavy", "types": "light"}

    # A final pass's timing replaces the baseline's.
    project.store.append("check_timing", {"e2e": 40.0}, role="orchestrator")
    assert project.check_kinds(120.0)["e2e"] == "light"

    # A person's choice wins over timing, and can be withdrawn.
    project = registry.set_check_runs(project, "types", "heavy")
    assert project.check_kinds(120.0)["types"] == "heavy"
    assert registry.get(project.id).state.check_runs == {"types": "heavy"}
    project = registry.set_check_runs(project, "types", "")
    assert project.check_kinds(120.0)["types"] == "light"
    import pytest as _pytest
    with _pytest.raises(pipeline_projects_module.ProjectError):
        registry.set_check_runs(project, "nope", "heavy")


def test_the_baseline_finds_the_checks_that_rewrite_files(tmp_path):
    """A formatter is declared like any other check and cannot be told apart
    by its name, so the baseline measures it: if tracked files changed while
    the checks ran, each is run again alone to find which. A fixer that changes
    untouched code is red -- the repository is not in the state its own
    formatter wants. A file a command creates is not a rewrite."""
    import asyncio
    import subprocess
    from types import SimpleNamespace

    from factory.onboarding import ProjectOnboarding
    from factory.schemas import Gate, GateReport, GateResult

    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "a.py").write_text("x=1\n")
    for cmd in (["init", "-q"], ["add", "-A"],
                ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c"]):
        subprocess.run(["git", *cmd], cwd=repo, check=True)

    class Runner:
        carries_setup = True

        async def execute(self, command, *, cwd, timeout_s, network=False):
            if command == "fmt":
                (Path(cwd) / "a.py").write_text("x = 1\n")
            if command == "build":
                (Path(cwd) / "dist.js").write_text("built\n")
            return gates.Execution(exit_code=0, output="ok")

    gate_list = [Gate(name=n, command=n) for n in ("lint", "fmt", "build")]
    report = GateReport(results=[GateResult(name=g.name, passed=True) for g in gate_list])
    (repo / "a.py").write_text("x = 1\n")          # as the concurrent run left it
    fake = SimpleNamespace()
    project = SimpleNamespace(state=SimpleNamespace(gates=gate_list))
    asyncio.run(ProjectOnboarding._find_fixers(fake, project, repo, Runner(), report))

    by = {r.name: r for r in report.results}
    assert by["fmt"].rewrote == ["a.py"] and not by["fmt"].passed
    assert "rewrote 1 file(s) on untouched code" in by["fmt"].output_tail
    assert by["build"].rewrote == [] and by["build"].passed, "a created file is not a rewrite"
    assert by["lint"].passed
    assert (repo / "a.py").read_text() == "x=1\n", "the tree is put back"


def test_a_build_runs_fixers_first_light_checks_every_round_and_heavy_ones_at_the_end(tmp_path):
    """Fixers run first, one at a time, and what they change is its own
    commit. Light checks run every round; heavy ones only in the final pass. A
    check caught rewriting files -- a formatter on a repository that was
    already formatted looks like any check at baseline -- is committed, and
    recorded as a fixer for every build after."""
    import asyncio
    import subprocess
    from types import SimpleNamespace

    from factory.pipeline import Factory
    from factory.projects import ProjectRegistry
    from factory.schemas import Gate, GateReport, GateResult
    from factory.store import EvidenceStore

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x=1\n")
    (repo / "b.py").write_text("y=2\n")
    for cmd in (["init", "-q"], ["add", "-A"],
                ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c"]):
        subprocess.run(["git", *cmd], cwd=repo, check=True)

    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "p")
    project.state.gates = [Gate(name=n, command=n) for n in ("fmt", "lint", "e2e", "sneaky")]
    project.state.baseline = GateReport(results=[
        GateResult(name="fmt", passed=True, duration_s=1, rewrote=["a.py"]),
        GateResult(name="lint", passed=True, duration_s=1),
        GateResult(name="e2e", passed=True, duration_s=600),
        GateResult(name="sneaky", passed=True, duration_s=1),
    ])
    registry.save(project)
    project = registry.get(project.id)

    ran: list[str] = []

    class Runner:
        carries_setup = True

        async def execute(self, command, *, cwd, timeout_s, network=False):
            ran.append(command)
            if command == "fmt":
                (Path(cwd) / "a.py").write_text("x = 1\n")
            if command == "sneaky":
                (Path(cwd) / "b.py").write_text("y = 2\n")
            return gates.Execution(exit_code=0, output="")

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True).stdout

    class Box:
        path = repo
        state = SimpleNamespace(commit_sha="")

        def commit_tracked(self, message):
            git("add", "-u")
            if not git("diff", "--cached", "--name-only").strip():
                return ""
            git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", message)
            return git("rev-parse", "HEAD").strip()

    fake = SimpleNamespace(project=project, config=Config())
    fake._commit_rewrites = lambda *a: Factory._commit_rewrites(fake, *a)
    fake._find_rewriters = lambda *a: Factory._find_rewriters(fake, *a)
    store = EvidenceStore(tmp_path / "f", "f")
    state = SimpleNamespace(feature_id="f", spec_hash="h", sandbox=None)

    report = asyncio.run(Factory._run_project_checks(fake, store, state, Box(), Runner()))
    assert [r.name for r in report.results] == ["fmt", "lint", "sneaky"], "no heavy check in a round"
    assert ran[0] == "fmt", "fixers run first"
    log = git("log", "--format=%s")
    assert "factory: formatted by fmt" in log and "factory: formatted by sneaky" in log
    assert git("status", "--porcelain") == "", "what the fixers did is committed, nothing left over"
    assert project.check_kinds()["sneaky"] == "fixer", "caught once, a fixer from then on"

    ran.clear()
    report = asyncio.run(Factory._run_project_checks(
        fake, store, state, Box(), Runner(), final=True))
    assert "e2e" in [r.name for r in report.results], "the final pass runs the heavy ones"
    assert ran[:2] == ["fmt", "sneaky"]

    src = inspect.getsource(Factory._converge)
    assert "final=True" in src and 'self.project.store.append("check_timing"' in src
    assert "(round_index or heavy_failed) and not forced_final" in src


def test_a_suggested_file_says_which_kind_it_is():
    """A helper that lets a component's error branch be tested at the unit
    level was offered to a project whose checks were all green, under "makes
    this project easier to write tests against" -- the first sentence of its
    reason, with the argument behind a disclosure. A file that unblocks no
    check has to say so itself.

    The card that printed the whole reason (`fileTodo`) had no caller left and
    is gone; what is still asserted is that the reason says which kind it is."""
    purpose = schemas.ScaffoldFile.model_fields["purpose"].description
    assert "a check cannot run without it" in purpose
    assert "a test at a level cannot be written without it" in purpose
    prompt = " ".join((ROOT / "factory" / "roles" / "surveyor.md").read_text().split())
    assert "`purpose` opens by saying which of the two kinds it is" in prompt


def test_a_dispatch_buys_the_repair_loop_its_rounds_again():
    """Card Tags had used both its rounds. Three findings were flagged for
    repair and sent in, and the loop resumed at round 2, hit the cap before it
    started, and would have returned the same packet with them unattempted
    after paying for a full assessment. The cap stops a loop spending on its
    own; a person asking for work is not that."""
    from factory.pipeline import resume_round

    state_at = {"kind": "rework_state", "seq": 10}
    assert resume_round([state_at], 2) == 2, "nothing was asked for, so the count stands"
    assert resume_round([state_at, {"kind": "dispatch", "seq": 11}], 2) == 0
    assert resume_round([{"kind": "dispatch", "seq": 9}, state_at], 2) == 2, \
        "a dispatch the last run already answered buys nothing"

    src = inspect.getsource(pipeline.Factory._converge)
    assert "round_index = resume_round(store.records()," in src


def test_a_flag_carries_the_finding_it_was_written_on():
    """"fix this" is an instruction about a finding. Sent without it, the
    remediator had a sentence and no subject: on card-tags it read "fix this",
    written on a finding about a removed tag coming back, and repaired a
    different race two files away."""
    from factory.pipeline import flag_findings
    from factory.schemas import Finding, HumanFlag

    anchored = {"hacker-8": Finding(
        id="hacker-8", title="Removing two tags quickly brings one back",
        severity="major", category="correctness", detail="The second delete writes back a "
        "list that still holds the first tag.")}
    said = flag_findings([HumanFlag(
        id="H-4", source="finding", anchor="hacker-8", text="fix this",
        disposition="repair", severity="major")], anchored)[0]
    assert said.title == "fix this"
    assert "Written on hacker-8" in said.detail
    assert "Removing two tags quickly brings one back" in said.detail
    assert "second delete writes back" in said.detail

    # A flag with no words of its own still borrows them, as before.
    bare = flag_findings([HumanFlag(
        id="H-5", source="finding", anchor="hacker-8", text="",
        disposition="repair", severity="major")], anchored)[0]
    assert bare.title == "Removing two tags quickly brings one back"


def test_a_finding_the_round_had_no_slot_for_is_not_charged_an_attempt():
    """It used to be possible to be charged for work nobody did. A remediator
    was asked which findings it could not fix, it answered honestly -- a
    frozen spec decision, a file it may not write -- and an attempt was
    charged for each anyway. Two of a human's own three flags came back
    "attempted, not fixed" with nobody having tried, and at two attempts that
    is permanent.

    Nothing is deferred now, because nothing is asked. The round takes as
    many units as it has slots, and whatever does not fit is simply not
    attempted."""
    ledger = FindingLedger()
    ledger.add("reviewer", [
        Finding(id=f"r-{i}", severity="major", title=f"t{i}", detail="d",
                files=[f"mod{i}.py"])
        for i in range(1, 6)
    ], 0, keep_ids=True)
    ledger.dispose([
        FindingDisposition(finding_id=f"r-{i}", disposition="repair", reason="fix")
        for i in range(1, 6)
    ])
    targets = [f"r-{i}" for i in range(1, 6)]

    plan = pipeline.plan_repairs(ledger, targets, 2)
    assert [u.id for u in plan.units] == ["R-1", "R-2"]
    took = [fid for u in plan.units for fid in u.finding_ids]
    assert took == ["r-1", "r-2"], "the round takes from the top of the order"
    assert "did not fit this round" in plan.summary

    # Only what a unit took is charged, so the three that waited keep every
    # attempt they had.
    ledger.attempted(took, 1)
    assert [ledger.records[f"r-{i}"].attempts for i in range(1, 6)] == [1, 1, 0, 0, 0]


def test_findings_that_name_a_shared_file_are_one_unit():
    """Two units writing one file is how a round produces a worse tree than
    it started with. That grouping is a fact about the findings rather than a
    judgment about them, so it is computed rather than asked for -- and the
    join is transitive: a finding naming two files merges their clusters."""
    ledger = FindingLedger()
    ledger.add("reviewer", [
        Finding(id="r-1", severity="major", title="a", detail="d", files=["app.py"]),
        Finding(id="r-2", severity="major", title="b", detail="d", files=["web.ts"]),
        Finding(id="r-3", severity="major", title="c", detail="d",
                files=["app.py", "web.ts"]),
        Finding(id="r-4", severity="major", title="d", detail="d", files=["other.py"]),
    ], 0, keep_ids=True)

    plan = pipeline.plan_repairs(ledger, ["r-1", "r-2", "r-3", "r-4"], 4)
    groups = [set(u.finding_ids) for u in plan.units]
    assert {"r-1", "r-2", "r-3"} in groups, "r-3 joins the two clusters it names"
    assert {"r-4"} in groups
    assert len(plan.units) == 2

    joined = next(u for u in plan.units if len(u.finding_ids) == 3)
    assert joined.files_expected == ["app.py", "web.ts"]
    # The findings reach the agent in their own words: there is no longer a
    # model standing between a finding and the fix for it.
    assert "r-1" in joined.objective and "r-3" in joined.objective


def test_a_test_file_unlocks_only_for_the_finding_that_says_it_is_wrong():
    """The one exception to the rule that keeps this factory honest.

    A repairer sees the implementation, and anything that can see the
    implementation and edit the exam will converge one onto the other under
    pressure to close findings -- the cheapest way to close any finding is to
    weaken the test that reports it. So a verification file is read-only by
    default and unlocks for exactly one unit: the one holding a finding that
    a human or the arbiter routed `oracle`, meaning that test asserts
    something the design makes impossible to observe.

    Nothing else unlocks it. Not a finding that merely names the file, not
    another unit in the same round, and not the agent deciding mid-repair
    that the test looked like the problem."""
    ledger = FindingLedger()
    ledger.add("reviewer", [
        Finding(id="r-1", severity="major", title="the assertion is impossible", detail="d",
                files=["tests/blind/test_len.py"]),
        Finding(id="r-2", severity="major", title="the endpoint 500s", detail="d",
                files=["api/app/main.py", "tests/blind/test_len.py"]),
    ], 0, keep_ids=True)
    ledger.dispose([
        FindingDisposition(finding_id="r-1", disposition="oracle", reason="asserts the impossible"),
        FindingDisposition(finding_id="r-2", disposition="repair", reason="fix the code"),
    ])

    # Apart, only the routed one carries the key.
    says_wrong = pipeline.plan_repairs(ledger, ["r-1"], 4).units[0]
    assert says_wrong.unlocked == ["tests/blind/test_len.py"]
    ordinary = pipeline.plan_repairs(ledger, ["r-2"], 4).units[0]
    assert ordinary.unlocked == [], "naming a test file is not permission to rewrite it"

    # Together they are one unit, because they share that file -- and the key
    # travels with the unit, which is the smallest thing that can hold it.
    both = pipeline.plan_repairs(ledger, ["r-1", "r-2"], 4).units[0]
    assert set(both.finding_ids) == {"r-1", "r-2"}
    assert both.unlocked == ["tests/blind/test_len.py"]
    assert "api/app/main.py" not in both.unlocked


def test_the_unlock_is_enforced_at_the_door_and_not_by_asking():
    """The brief tells the agent; the door decides. Both have to be there --
    a prompt is a request, and INV-12 is not a request."""
    src = inspect.getsource(pipeline.Factory._repair_round)
    assert "unlocked_by_unit = {u.id: set(u.unlocked) for u in units}" in src, (
        "the protection filter stopped being per unit")
    assert "if f.path not in unlocked and is_protected(" in src, (
        "a protected path is refused unless this unit's own finding unlocked it")
    # And the agent is told, in the unit that holds the key and no other.
    assert "UNLOCKED FOR THIS UNIT, and nothing else is: " in src
    assert "guessing at someone's acceptance test is " in src


def test_a_second_unpack_does_not_leave_the_first_one_s_tail_behind(tmp_path):
    """Unpacking writes each frame by name into a directory it reuses, so a
    second unpack that keeps fewer frames than the first used to leave the
    difference on disk -- files the manifest no longer lists, still served by
    name to anyone who asks for them.

    Dropping the blank frames is exactly the change that shortens a recording,
    so this stopped being hypothetical the moment that landed.
    """
    target = tmp_path / "traces"
    target.mkdir()
    archive = _recording_of(target, [_DRAWN] * 5)
    factory = pipeline.Factory.__new__(pipeline.Factory)

    pipeline.Factory._unpack_trace(factory, target, archive.name)
    where = target / f"{archive.stem}.frames"
    assert sorted(p.name for p in where.glob("f*.jpeg")) == [
        f"f{i:04d}.jpeg" for i in range(1, 6)]

    # The same recording, now with three of those frames blank at the front.
    shorter = _recording_of(target, [_FLAT] * 3 + [_DRAWN] * 2)
    shorter.replace(archive)
    pipeline.Factory._unpack_trace(factory, target, archive.name)

    assert sorted(p.name for p in where.glob("f*.jpeg")) == ["f0001.jpeg", "f0002.jpeg"], (
        "f0003 through f0005 belonged to the longer unpack and are gone with it"
    )
    listed = json.loads((target / f"{archive.stem}.player.json").read_text())
    assert [f["file"] for f in listed["frames"]] == ["f0001.jpeg", "f0002.jpeg"]


def test_the_settle_a_test_ends_on_is_not_the_label_on_its_last_frame(tmp_path):
    """The oracle is briefed to let the screen settle before a test returns,
    because the last assertion's paint otherwise lands after the last frame --
    that is how AC-13's recording ended on the label before the cut it was
    about.

    The settle is a `waitForTimeout`, and it shows up in the trace as a step
    like any other. Labelled, it names the mechanism the final frame exists
    because of, in the one place a reader most wants the step that mattered.
    """
    import zipfile

    events = [{"type": "context-options", "title": "a recorded test"}]
    for i, (at, step) in enumerate([(1.0, 'Fill "a"'), (2.0, 'Expect "toHaveValue"'),
                                    (3.0, "Wait for timeout")]):
        events.append({"type": "before", "startTime": at, "title": step})
    # One frame, after the settle began -- the frame the settle exists to catch.
    events.append({"type": "screencast-frame", "file": "s.jpeg", "timestamp": 3.5,
                   "width": 1280, "height": 720})
    archive = tmp_path / "settled.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("test.trace", "\n".join(json.dumps(e) for e in events))
        zf.writestr("resources/s.jpeg", b"\xff" * 26287)

    _frames, manifest = pipeline.trace_player(archive)
    assert manifest["frames"][-1]["step"] == 'Expect "toHaveValue"', (
        "the assertion that mattered, not the wait that let it be filmed")
    assert "Wait for timeout" in pipeline._TRACE_PLUMBING

    # And the brief that makes the wait happen at all says why.
    brief = (ROOT / "factory" / "roles" / "oracle.md").read_text(encoding="utf-8")
    assert "settle before a test returns" in brief
    assert "is not recorded asserting it" in brief, "the reason, not just the instruction"


def test_a_frame_is_measured_by_its_own_size_and_not_the_page_s(tmp_path):
    """A screencast-frame event reports the size of the *page*. The screencast
    is free to scale below it, and Playwright does: it caps its trace frames at
    800 wide unless the project also records video, in which case the two share
    a stream and the frames come out at the viewport.

    Judging a frame by the declared numbers is therefore wrong by the square of
    whatever scale was applied -- and wrong in a way that passes every test
    until the scale changes. A cut calibrated against 1280x720 while the frames
    were really 800x450 dropped blanks correctly for months of runs and then
    stopped the moment video turned the frames up to full size.
    """
    # Frames really 1280x720 and flat; events claiming a tiny page. Measured
    # against the claim, 5684 bytes over 320x180 looks richly drawn.
    frames, manifest = pipeline.trace_player(_recording_of(
        tmp_path, [_FLAT, _FLAT, _DRAWN], width=1280, height=720, declares=(320, 180)))

    assert len(frames) == 1, (
        "the two flat frames go: what decides is the frame's own area, not the "
        "page size the event happens to report")
    assert (manifest["width"], manifest["height"]) == (1280, 720), (
        "and the player is told the size it will actually be laying out")

    src = inspect.getsource(pipeline._without_leading_blanks)
    assert '_jpeg_size(read(hit))' in src, "read off the frame"
    assert 'frame.get("width")' not in src, "never off the event"


def test_the_frame_header_reader_handles_what_a_screencast_writes():
    """The size lives in a start-of-frame segment, and finding it means walking
    the markers before it -- some of which carry a length and some of which do
    not. Getting that walk wrong returns zero, which the trim reads as "cannot
    judge this" and silently stops trimming.
    """
    # A baseline frame, which is what Chromium's screencast writes.
    assert pipeline._jpeg_size(_jpeg_of(5684, 1280, 720)) == (1280, 720)
    assert pipeline._jpeg_size(_jpeg_of(2459, 800, 450)) == (800, 450)

    # And anything it cannot read is zero rather than a guess, so the caller
    # keeps the frame instead of dropping it on a misparse.
    assert pipeline._jpeg_size(b"") == (0, 0)
    assert pipeline._jpeg_size(b"\xff\xd8" + b"\x00" * 40) == (0, 0)
    assert pipeline._jpeg_size(b"not a jpeg at all") == (0, 0)

    # A real collected frame, if this machine has one.
    real = sorted(pathlib.Path(
        ".factory/kanban/features/card-tags-fee892/artifacts/traces").glob("*.frames/*.jpeg"))
    if real:
        got = pipeline._jpeg_size(real[0].read_bytes())
        assert got[0] > 0 and got[1] > 0, f"{real[0].name} read as {got}"


# --------------------------------------------------------------------------
# the planner became the spec writer, and the two checkers
#
# It writes the spec, not the plan. With a spec checker after it and a plan
# checker after the architect, the old name put the wrong checker next to the
# wrong agent. Evidence is append-only, so the old name lives on in every
# ledger written before; state files are rewritten and read through a map.
# --------------------------------------------------------------------------


def test_a_feature_saved_before_the_rename_still_loads():
    old = schemas.FeatureState.model_validate({
        "feature_id": "f", "stage": "planning",
        "phases": [{"name": "planner", "status": "done"}, {"name": "architect"}],
    })
    assert old.stage == "writing_spec"
    assert [p.name for p in old.phases] == ["spec_writer", "architect"]


def test_objections_are_anchored_capped_and_renumbered():
    raw = [schemas.CheckObjection(id=f"x{i}", kind="guess", claim=f"claim {i}",
                                  consequence="c", unit_ids=["U-1"]) for i in range(8)]
    raw.insert(0, schemas.CheckObjection(id="nowhere", kind="guess", claim="floats",
                                         consequence="c", unit_ids=["U-9"]))
    kept = pipeline.anchored_objections(raw, prefix="K", units=["U-1"])
    assert [o.id for o in kept] == ["K-1", "K-2", "K-3", "K-4", "K-5"]
    assert kept[0].claim == "claim 0", "the one naming nothing real was dropped, not counted"


def test_only_a_changed_document_settles_an_objection():
    o = schemas.CheckObjection(id="K-1", kind="guess", claim="c", consequence="c",
                               unit_ids=["U-1"])
    before = schemas.Plan(summary="s", units=[schemas.WorkUnit(id="U-1", title="t", objective="o")])
    after = before.model_copy(deep=True)
    said = [schemas.ObjectionAnswer(objection_id="K-1", answer="revised", note="done")]
    same = pipeline.settle_objections([o], said, lambda x: pipeline.units_changed(before, after, x))
    assert not same[0]["settled"] and "nothing it names changed" in same[0]["why"]
    after.units[0].read_files = ["app/base.py"]
    moved = pipeline.settle_objections([o], said, lambda x: pipeline.units_changed(before, after, x))
    assert moved[0]["settled"]
    rebutted = [schemas.ObjectionAnswer(objection_id="K-1", answer="rebutted", note="no")]
    assert not pipeline.settle_objections(
        [o], rebutted, lambda x: pipeline.units_changed(before, after, x))[0]["settled"]


def test_the_facts_about_a_cut_are_computed_not_asked_for():
    spec = schemas.Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        schemas.AcceptanceCriterion(id="AC-1", statement="a"),
        schemas.AcceptanceCriterion(id="AC-2", statement="b"),
        schemas.AcceptanceCriterion(id="AC-3", statement="c")])
    plan = schemas.Plan(summary="s", units=[
        schemas.WorkUnit(id="U-1", title="a", objective="o", criterion_ids=["AC-1", "AC-2"],
                         files_expected=["app/x.py"]),
        schemas.WorkUnit(id="U-2", title="b", objective="o", criterion_ids=["AC-2"],
                         files_expected=["app/x.py"])])
    facts = " ".join(pipeline.cut_facts(plan, spec))
    assert "`app/x.py` is written by U-1 and U-2" in facts
    assert "AC-2 is in U-1 and U-2" in facts
    assert "AC-3 is in no unit" in facts
    one = pipeline.merge_units(plan)
    assert pipeline.cut_facts(one, spec) == ["AC-3 is in no unit, so nothing will build it."], \
        "merging settles what merging can settle, and nothing else"


# ==========================================================================
# repairs go where they can land, and reviewers are told what did
# ==========================================================================


def test_a_failing_seam_check_names_the_file_its_error_was_raised_in():
    """A seam check a repair may not change, failing on an error raised in a
    file the feature wrote. The finding named only the check, so two rounds
    sent a repairer to a file it was forbidden to write while the one at fault
    -- `web/e2e/fixtures.ts:36`, with its `localhost:8300` default -- was
    nobody's."""
    from factory.schemas import GateReport, GateResult, TestFileCommand

    seam = _seam()
    checked = _integration(unresolved=[], seam_issues=[], seam_files=[seam])
    [gate] = pipeline.seam_gates(checked, [TestFileCommand(match="*", command="run {path}")])
    output = ("TypeError: fetch failed\n[cause]: Error: connect ECONNREFUSED 127.0.0.1:8300\n"
              "    at post (/workspace/web/e2e/fixtures.ts:36:20)\n"
              "    at seedBoard (/workspace/web/e2e/fixtures.ts:48:17)\n"
              "    at node_modules/@playwright/test/lib/index.js:12:3")
    red = GateReport(results=[GateResult(name=gate.name, command=gate.command, exit_code=1,
                                         passed=False, output_tail=output)])
    [failing] = pipeline.check_seams(
        checked, red, written=["web/e2e/fixtures.ts", "web/src/App.tsx", "e2e/fixtures.ts.bak"])
    assert failing.files == [seam.path, "web/e2e/fixtures.ts"], \
        "the finding still names only the check, or names files the error was not in"
    # Without the feature's files to match, it names what it always did.
    [bare] = pipeline.check_seams(checked, red)
    assert bare.files == [seam.path]


def test_no_repair_unit_owns_only_files_it_may_not_write():
    """A unit's brief once listed a seam check as the one file it owned and,
    twenty lines down, among the paths refused before they land. Every edit it
    made was refused, and the slot was the round's. A unit owns what a repair
    may write; a finding that names nothing else goes to a person, now, with
    the reason, and costs no attempt."""
    ledger = FindingLedger()
    ledger.add("reviewer", [
        Finding(id="seams-failing", severity="blocker", title="a seam check fails", detail="d",
                files=["web/e2e/moveCard.seam.spec.ts"]),
        Finding(id="r-2", severity="major", title="and the helper under it", detail="d",
                files=["web/e2e/moveCard.seam.spec.ts", "web/e2e/fixtures.ts"]),
        Finding(id="r-3", severity="major", title="the endpoint 500s", detail="d",
                files=["api/app/main.py"]),
    ], 0, keep_ids=True)
    guarded = {"web/e2e/moveCard.seam.spec.ts"}
    writable = lambda path: path not in guarded  # noqa: E731

    alone = pipeline.plan_repairs(ledger, ["seams-failing", "r-3"], 4, writable=writable)
    assert [u.finding_ids for u in alone.units] == [["r-3"]], \
        "a unit was made whose every file is refused before it lands"
    assert any(d.startswith("seams-failing:") for d in alone.deferred)

    # Joined with a finding that also names a writable file, the unit is real
    # and owns only that file.
    both = pipeline.plan_repairs(ledger, ["seams-failing", "r-2"], 4, writable=writable)
    [unit] = both.units
    assert set(unit.finding_ids) == {"seams-failing", "r-2"}
    assert unit.files_expected == ["web/e2e/fixtures.ts"]

    # Sent to a person without costing an attempt.
    moved = ledger.route_to_person(["seams-failing"], "every file it names is a check")
    record = ledger.records["seams-failing"]
    assert moved == ["seams-failing"] and record.disposition == "escalate"
    assert record.attempts == 0
    ledger.finalise(stopped_early=False)
    assert record.outcome == "escalated"

    src = inspect.getsource(pipeline.Factory._repair_round)
    assert "writable=lambda path: not is_protected(" in src
    assert "ledger.route_to_person(unroutable" in src


def test_a_reviewer_is_told_which_repairs_never_reached_the_branch(tmp_path):
    """Two reviewers reported a check "made green by changing the check
    itself". The repair's own account said it had changed the check; the write
    had been refused, and nothing beside that account said so."""
    from factory.schemas import FileWrite

    (tmp_path / "web/e2e").mkdir(parents=True)
    (tmp_path / "web/e2e/moveCard.seam.spec.ts").write_text("the original check\n")
    (tmp_path / "web/src").mkdir(parents=True)
    (tmp_path / "web/src/App.tsx").write_text("the repair\n")
    (tmp_path / "web/src/api.ts").write_text("edited again later\n")
    guarded = lambda path: ".seam." in path  # noqa: E731

    refused = pipeline.what_landed(
        [FileWrite(path="web/e2e/moveCard.seam.spec.ts", contents="a rewritten check\n")],
        tmp_path, guarded)
    assert "Refused, and never on the branch" in refused and "moveCard.seam.spec.ts" in refused

    mixed = pipeline.what_landed(
        [FileWrite(path="web/src/App.tsx", contents="the repair\n"),
         FileWrite(path="web/src/api.ts", contents="this unit's version\n")], tmp_path, guarded)
    assert "Changed since by later work" in mixed and "api.ts" in mixed
    assert "App.tsx" not in mixed, "a file that landed is listed as though it had not"

    landed = pipeline.what_landed(
        [FileWrite(path="web/src/App.tsx", contents="the repair\n")], tmp_path, guarded)
    assert "All 1 file(s) this unit wrote are on the branch" in landed

    src = inspect.getsource(pipeline.Factory._evidence_block)
    assert "what_landed(w.files" in src, "the reviewers' bundle does not say what landed"


def test_a_sort_starts_each_re_run_from_the_projects_reset(tmp_path):
    """Setting a suite's files aside does nothing about what they already wrote.
    With the project's reset run first, the re-run is a clean experiment and
    the failure lands on the suite that caused it."""
    from factory.pipeline import check_unreset_sort
    from factory.schemas import GateReport

    result = _polluted_sort(tmp_path, reset=True)
    assert result.failed_on == "blind_tests", \
        "the suite's leftovers were blamed on the code despite a reset being available"
    assert not result.unreset
    assert check_unreset_sort(GateReport(results=[result])) == []


def test_a_sort_that_could_not_start_clean_says_so(tmp_path):
    """No reset declared, so the leftovers are still there on the re-run and the
    check stays red. That is recorded against the code -- and flagged, because
    it is exactly what leftover data would look like too."""
    from factory.pipeline import check_unreset_sort
    from factory.schemas import GateReport

    result = _polluted_sort(tmp_path, reset=False)
    assert result.failed_on == "code" and result.unreset
    [finding] = check_unreset_sort(GateReport(results=[result]))
    assert finding.id == "sort-unreset" and "test_prepare" in finding.recommendation
    assert "unsettled" in finding.detail

    # Red at the base commit as well, where none of this feature's files
    # existed to leave anything behind: pre-existing, and nothing to flag.
    result.at_base = "failed"
    assert check_unreset_sort(GateReport(results=[result])) == [], \
        "a check already failing before the feature was called unsettled by it"


# ==========================================================================
# cleanup -- the repo's own convention, and measured by outcome
# ==========================================================================


def test_a_tier_records_how_its_tests_clean_up_in_the_projects_own_words():
    """No list of mechanisms: a rollback, a teardown, a truncate and a throwaway
    workspace are all the right answer somewhere. The survey records which one
    this repository uses, per tier, and every agent that writes a test is shown
    it -- the oracle and integrator in the full surface, the rest on its own."""
    from factory import onboarding
    from factory.schemas import TestingSurface, TestingTier

    assert {"cleanup", "cleanup_examples", "cleanup_summary"} <= onboarding.TIER_PROSE, \
        "a reading that rewords the cleanup would read as a change to the tier"
    surveyor = (ROOT / "factory" / "roles" / "surveyor.md").read_text()
    assert "### `cleanup`, `cleanup_examples`, `cleanup_summary` and `cleanup_options`" in surveyor
    assert "No names of files, fixtures, functions or tools" in surveyor
    assert "There is no right mechanism here, only this project's" in surveyor

    surface = TestingSurface(tiers=[
        TestingTier(tier="integration", verdict="usable", runner="pytest",
                    cleanup="each test gets a fresh schema from the `client` fixture",
                    cleanup_examples=["await connection.run_sync(Base.metadata.drop_all)"]),
        TestingTier(tier="user", verdict="usable", runner="playwright", cleanup=""),
        TestingTier(tier="unit", verdict="usable", runner="vitest"),   # never asked
    ])
    full = workspace.testing_context(surface)
    assert "fresh schema from the `client` fixture" in full
    assert "Base.metadata.drop_all" in full
    assert "Nothing here cleans up after a test" in full
    brief = workspace.cleanup_context(surface)
    assert "## integration" in brief and "## user" in brief and "## unit" not in brief, \
        "a tier recorded before this was asked is said to clean up nothing"

    src = class_source(pipeline.Factory)
    for role in ("worker", "repairer", "breaker"):
        assert re.search(rf'role_prompt\("{role}"\)\s*\+\s*cleanup_context\(', src) or \
            re.search(rf'self\.role_prompt\("{role}"\)\s*\n\s*\+ cleanup_context\(', src), \
            f"the {role} writes tests and is not told how this project cleans up"


def test_a_criterion_checked_where_tests_do_not_clean_up_is_flagged_before_the_freeze():
    """The browser tier of the project this comes from had no cleanup at all,
    and twelve criteria were checked there. Every blind test written for them
    left lists on the shared demo board. Knowable at gate 0, so said at gate 1."""
    from factory.schemas import TestingSurface, TestingTier

    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="a", verified_at="unit"),
        AcceptanceCriterion(id="AC-2", statement="b", verified_at="integration"),
        AcceptanceCriterion(id="AC-3", statement="c", verified_at="user"),
    ])
    tiers = lambda user_cleanup: TestingSurface(tiers=[  # noqa: E731
        TestingTier(tier="unit", verdict="usable", cleanup=""),
        TestingTier(tier="integration", verdict="usable", cleanup="schema per test"),
        TestingTier(tier="user", verdict="usable", cleanup=user_cleanup)])

    [gap] = [f for f in pipeline.check_spec_testability(spec, tiers(""))
             if f.id == "cleanup-gap-1"]
    assert gap.criterion_ids == ["AC-3"], "a unit criterion or a clean tier was flagged"
    assert "provides" in gap.recommendation
    assert not [f for f in pipeline.check_spec_testability(spec, tiers(None))
                if f.id == "cleanup-gap-1"], "a survey that never answered is read as 'none'"


def test_a_test_that_passes_then_fails_straight_after_is_a_leak(tmp_path):
    """Passed, then failed when run again with nothing reset between: it leaves
    something behind. With the project's reset it is tried a third time from
    clean, which is what tells leftover state from a test that merely flakes."""
    from factory.schemas import GateReport

    confirmed = _leaky_run(tmp_path, reset=True)
    assert confirmed.passed, "the first run's verdict is the file's verdict"
    assert confirmed.leaked == ["e2e/drag.spec.ts"]
    assert confirmed.leak_confirmed == ["e2e/drag.spec.ts"] and not confirmed.unstable
    assert "7 lists" in confirmed.leak_output["e2e/drag.spec.ts"]

    unconfirmed = _leaky_run(tmp_path, reset=False)
    assert unconfirmed.leaked == ["e2e/drag.spec.ts"] and not unconfirmed.leak_confirmed

    flaky = _leaky_run(tmp_path, reset=True, flaky=True)
    assert flaky.unstable == ["e2e/drag.spec.ts"] and not flaky.leak_confirmed

    [leak] = pipeline.check_leaks(GateReport(results=[confirmed]))
    assert leak.id == "leaks-1" and leak.files == ["e2e/drag.spec.ts"]
    assert "Confirmed by the project's reset" in leak.detail
    [guess] = pipeline.check_leaks(GateReport(results=[unconfirmed]))
    assert "Not confirmed" in guess.detail
    [shaky] = pipeline.check_leaks(GateReport(results=[flaky]))
    assert shaky.id == "unstable-1" and shaky.severity == "minor"


def test_a_blind_file_that_leaks_goes_back_to_the_oracle(tmp_path):
    """The oracle's files are the oracle's to fix, through the loop that already
    sends it files that will not load. It is told what happened and told not to
    change what the test asserts."""
    from factory.schemas import GateReport, OracleSuite, TestFile

    blind = _leaky_run(tmp_path, reset=True)
    blind.name = "blind-tests"
    oracle = OracleSuite(strategy="s", tests=[TestFile(path="e2e/drag.spec.ts", contents="x",
                                                       criterion_ids=["AC-1"])])
    said = pipeline.oracle_problems(GateReport(results=[]), blind, oracle)
    assert "`e2e/drag.spec.ts` leaves something behind" in said
    assert "do not change what it asserts" in said
    assert "repeat=True" in inspect.getsource(pipeline.Factory._run_blind_suite)
    seams = inspect.getsource(pipeline.Factory._run_seam_checks)
    assert "again = (await run_gates([by_name[result.name]]" in seams


def test_a_re_read_answers_a_cleanup_question_the_last_reading_was_never_asked():
    """A re-survey reads what changed, and a question never asked has not
    changed. Kanban's first re-read after `cleanup` existed said in its own
    summary that it would describe how each level cleans up -- and left the
    field out, because nothing about the tests had moved; the next was dropped
    as identical, because the answer counted only as rewording. Whether it is
    answered at all is a decision; its wording is prose."""
    from factory import onboarding
    from factory.schemas import TestingSurface, TestingTier

    unread = TestingTier(tier="user", verdict="usable")
    said = TestingTier(tier="user", verdict="usable", cleanup="specs put back what they moved",
                       cleanup_by="each_test")
    reworded = TestingTier(tier="user", verdict="usable", cleanup="each spec restores the order",
                           cleanup_by="each_test")
    none = TestingTier(tier="user", verdict="usable", cleanup="")

    shape = onboarding.tier_shape
    assert shape(unread) != shape(said), "answering it for the first time reads as no change"
    assert shape(said) == shape(reworded), "rewording the answer reads as a change"
    assert shape(said) != shape(none)

    asked = onboarding._unasked_cleanup(TestingSurface(tiers=[unread]))
    assert "`user`" in asked and "`testing` change" in asked
    assert onboarding._unasked_cleanup(TestingSurface(tiers=[said])) == ""
    assert "_unasked_cleanup(project.state.testing)" in inspect.getsource(
        onboarding.ProjectOnboarding.run_resurvey)


def test_a_criterion_where_each_test_must_remember_is_flagged_with_the_fixes():
    from factory.schemas import CleanupOption, TestingSurface, TestingTier

    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="a", verified_at="integration"),
        AcceptanceCriterion(id="AC-2", statement="b", verified_at="user")])
    surface = TestingSurface(tiers=[
        TestingTier(tier="integration", verdict="usable", cleanup="schema per test",
                    cleanup_by="harness"),
        TestingTier(tier="user", verdict="usable", cleanup="each spec undoes its own",
                    cleanup_by="each_test",
                    cleanup_options=[
                        CleanupOption(title="Reset the data between runs", summary="s",
                                      environment=True),
                        CleanupOption(title="Browser tests use their own board", summary="s",
                                      agent_prompt="Browser tests use their own board")])])
    [gap] = [f for f in pipeline.check_spec_testability(spec, surface) if f.id == "cleanup-gap-1"]
    assert gap.criterion_ids == ["AC-2"], "a tier that resets for every test was flagged"
    assert "Reset the data between runs" in gap.recommendation
    assert "Browser tests use their own board" in gap.recommendation


def test_tests_that_keep_to_their_own_data_are_settled_not_a_gap():
    """Kanban's browser tests were changed so every test that writes makes a
    workspace of its own. The re-survey read that exactly -- and filed it under
    "each test must undo its own", the only answer it had, so the card stayed
    amber and offered a reset and a delete that could add nothing. `isolated`
    is its own answer: no gap before a spec is frozen, a green pill, and a
    choice only when a new test cannot reach the means."""
    from factory import onboarding
    from factory.schemas import CleanupOption, TestingSurface, TestingTier

    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="a", verified_at="user")])
    own = TestingTier(tier="user", verdict="usable", cleanup="each spec makes its own workspace",
                      cleanup_by="isolated")
    assert not [f for f in pipeline.check_spec_testability(spec, TestingSurface(tiers=[own]))
                if f.id == "cleanup-gap-1"], "tests that keep to their own data were flagged"
    assert onboarding.tier_shape(own) != onboarding.tier_shape(
        own.model_copy(update={"cleanup_by": "each_test"})), "the change of answer reads as rewording"

    app = app_js()
    assert 'isolated: `<span class="pill pill-green ts-proof">each test uses its own data' in app
    choice = app.split("function cleanupChoice(t, diff, opts = {}) {")[1].split("\n}\n")[0]
    assert "by === 'isolated' && (t.cleanup_options || []).length" in choice

    surveyor = (ROOT / "factory" / "roles" / "surveyor.md").read_text()
    for said in ("`isolated` is settled, not a gap",
                 "offer one option that lifts it into a support file",
                 "taken down with its volumes when the session ends",
                 "Never offer a fix whose only benefit is between runs",
                 "Once tests are `isolated`, a reset or a delete adds nothing"):
        assert said in surveyor, f"the surveyor is no longer told: {said}"
    TestingTier(tier="user", verdict="usable", cleanup="x", cleanup_by="isolated",
                cleanup_options=[CleanupOption(title="Share the helper", summary="s",
                                               files=["web/e2e/support/workspace.ts"])])


def test_ticking_the_reset_fix_gives_the_project_its_test_prepare(tmp_path):
    """The environment fix is an ordinary environment change, applied like any
    other -- the reset lands as the project's `test_prepare`."""
    from factory.schemas import EnvironmentSpec, SurveyDiff

    registry, project = _project_with_gates(tmp_path, Gate(name="tests", command="pytest -q"))
    project.state.environment = EnvironmentSpec(kind="compose", compose_file="c.yml",
                                                compose_service="api")
    project = registry.save(project, note="fixture")
    fixed = project.state.environment.model_copy(update={"test_prepare": ["reset-the-db"]})
    diff = SurveyDiff(summary="s", environment=fixed, environment_reason="r")
    untouched, _ = registry.apply_survey_diff(project, diff, [])
    assert untouched.state.environment.test_prepare == [], "an unticked fix was applied anyway"
    updated, _ = registry.apply_survey_diff(untouched, diff, [], environment=True)
    assert updated.state.environment.test_prepare == ["reset-the-db"]


def test_an_environment_that_runs_an_unwritten_file_is_refused_until_it_is_written(tmp_path):
    """A fix can come in two halves: a reset script offered as a file, and an
    environment step that runs it. Files land by their own press; the
    environment applied first would run a script that is not there, fail its
    preparation, and block every check behind it."""
    from factory.projects import ProjectError
    from factory.schemas import EnvironmentSpec, ScaffoldFile, SurveyDiff

    registry, project = _project_with_gates(tmp_path, Gate(name="tests", command="pytest -q"))
    project.state.environment = EnvironmentSpec(kind="compose", compose_file="c.yml",
                                                compose_service="api")
    project = registry.save(project, note="fixture")
    runs_it = project.state.environment.model_copy(update={
        "test_prepare": ["cd api && python scripts/reset_demo_data.py"]})
    diff = SurveyDiff(summary="s", environment=runs_it, environment_reason="r",
                      scaffolding=[ScaffoldFile(path="api/scripts/reset_demo_data.py",
                                                contents="print('reset')", purpose="p")])
    with pytest.raises(ProjectError, match="api/scripts/reset_demo_data.py"):
        registry.apply_survey_diff(project, diff, [], environment=True)

    (Path(project.state.repo) / "api/scripts").mkdir(parents=True)
    (Path(project.state.repo) / "api/scripts/reset_demo_data.py").write_text("print('reset')")
    updated, _ = registry.apply_survey_diff(project, diff, [], environment=True)
    assert updated.state.environment.test_prepare == runs_it.test_prepare


def test_the_oracle_never_sees_a_criterion_nobody_can_test():
    """Not asked and not attempted, so none of its time goes on them and none
    of its findings -- a `requires` naming a criterion is a blocker -- are
    about them. A spec with nothing left to test writes nothing and raises no
    "missing suite" finding."""
    from factory.workspace import manual_criteria, tested_spec

    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="a", verified_at="integration"),
        AcceptanceCriterion(id="AC-2", statement="b", verified_at="user"),
        AcceptanceCriterion(id="AC-3", statement="c")])
    state = _State(_levels_surface(unit="harness", integration="harness", user="nobody"))
    assert [c.id for c in manual_criteria(spec, state)] == ["AC-2"]
    assert [c.id for c in tested_spec(spec, state).acceptance_criteria] == ["AC-1", "AC-3"]
    assert tested_spec(spec, _State(_levels_surface(unit="harness", integration="harness",
                                             user="harness"))) is spec

    lane = inspect.getsource(pipeline.Factory._verify_lane)
    assert lane.index("spec = tested_spec(spec, self.project.state)") < lane.index("context = verify_context(spec")
    assert "if not spec.acceptance_criteria:" in lane
    revise = inspect.getsource(pipeline.Factory._revise_blind_suite)
    assert revise.index("tested_spec(spec, self.project.state)") < revise.index("context = verify_context(spec")
    converge = inspect.getsource(pipeline.Factory._converge)
    assert "check_blind_suite_missing(oracle, checked)\n                           if checked.acceptance_criteria else []" in converge


def test_an_untested_criterion_is_manual_not_missing_a_test_and_not_failed():
    """Implemented and at a skipped level, a criterion is `manual` in the trace
    and in QA: not "no test", which reads as the oracle having missed one, and
    not a failure. Counted on its own, and said in the QA summary."""
    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="a"), AcceptanceCriterion(id="AC-2", statement="b"),
        AcceptanceCriterion(id="AC-3", statement="c")])
    rows = compute_trace(spec, [_worker("AC-1", ["src/a.py"]), _worker("AC-2", ["src/b.py"])],
                         OracleSuite(strategy="none", tests=[]), manual={"AC-2", "AC-3"})
    by = {r.criterion_id: r.status for r in rows}
    assert by == {"AC-1": "untested", "AC-2": "manual", "AC-3": "orphan_requirement"}, \
        "unimplemented is still no code, whoever was to check it"
    qa = pipeline.compute_qa(spec, None, schemas.GateReport(results=[]), rows)
    assert {r.criterion_id: r.status for r in qa.results}["AC-2"] == "manual"
    assert "left to a person to check by hand" in qa.summary
    stats = pipeline.compute_stats(spec, rows, qa, schemas.GateReport(results=[]), [], [], [])
    assert (stats.criteria_manual, stats.untested_criteria) == (1, 1)


def test_a_build_with_untested_criteria_never_just_ships():
    """At best it ships with rulings -- the person's own checks -- and a
    headline written for a plain ship is told what is left. The checklist is in
    the packet, in words written for a person: `check_by_hand`, never the test
    author's `verification`. A worse verdict stands."""
    from factory.schemas import Packet

    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="a", verified_at="user", title="Drop into an empty list",
                            verification="A Playwright test seeds a board and...",
                            check_by_hand="Drag a card into the empty Done list, then reload.")])
    trace = [schemas.TraceRow(criterion_id="AC-1", status="manual")]
    packet = pipeline.hold_for_manual_checks(
        Packet(headline="Ships: everything tested passes.", verdict="ship"), spec, trace,
        {"user": "tests can't clean up after themselves"})
    assert packet.verdict == "ship_with_rulings"
    assert packet.headline.endswith("Check 1 criterion by hand before you ship.")
    [check] = packet.manual_checks
    assert check.how == "Drag a card into the empty Done list, then reload."
    assert "Playwright" not in check.how
    held = pipeline.hold_for_manual_checks(
        Packet(headline="Send it back.", verdict="send_back"), spec, trace, {})
    assert held.verdict == "send_back" and held.headline == "Send it back."
    assert "hold_for_manual_checks(packet, spec, trace, unchecked_levels(self.project.state))" \
        in inspect.getsource(pipeline.Factory._build)

    from factory import packet_doc
    doc = inspect.getsource(packet_doc)
    assert '_section(lines, "Check these yourself")' in doc


def test_a_spec_says_once_which_criteria_a_person_will_check():
    """Before it is frozen, and only once: an `unchecked-1` note naming them,
    and nothing else about them -- no cleanup gap, no "can't be tested" -- since
    nobody will test into those levels."""
    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id="AC-1", statement="a", verified_at="user"),
        AcceptanceCriterion(id="AC-2", statement="b", verified_at="integration")])
    surface = _levels_surface(unit="harness", integration="each_test", user="absent")
    found = {f.id: f for f in pipeline.check_spec_testability(
        spec, surface, {"user": "there's no test runner"})}
    assert found["unchecked-1"].criterion_ids == ["AC-1"] and found["unchecked-1"].severity == "minor"
    assert "testability-1" not in found, "a skipped level is reported as untestable as well"
    assert found["cleanup-gap-1"].criterion_ids == ["AC-2"]


def test_a_file_the_harness_deleted_is_work_and_leaves_the_branch(tmp_path, monkeypatch):
    """A finding whose whole fix was "this file should not be on the branch"
    went to a harness that removed it -- and a removed path read as nothing, so
    the unit was booked as having edited no file, twice. The fallback could only
    empty the file, and the finding reached a human as a broken harness."""
    import subprocess

    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.llm import LLM
    from factory.schemas import SelfDisclosure, Spec, WorkUnit

    repo = _harness_repo(tmp_path)
    (repo / "api" / "stale").mkdir(parents=True)
    (repo / "api" / "stale" / "left.json").write_text('{"status": "failed"}\n')
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "stray"], cwd=repo, check=True)

    counter = tmp_path / "runs"
    config = load_config()
    config.executor.fallback_to_direct = False
    _global_harness(config, ["sh", "-c", f"echo run >> {counter}; rm api/stale/left.json"])
    executor = CommandExecutor(LLM(config), config)

    async def fake_ask(role_name, prompt, schema=None, **kw):
        return schema(summary="s", decisions=[], disclosure=SelfDisclosure())
    monkeypatch.setattr(executor.llm, "ask", fake_ask)

    out = asyncio.run(executor.run(
        WorkUnit(id="R-4", title="t", objective="o", files_expected=["api/stale/left.json"]),
        Spec(title="t", intent="i", summary="s"), "digest", "SYS", repo, role="repairer"))

    assert counter.read_text().count("run") == 1, "a deletion was not counted as an edit"
    assert [(f.path, f.deleted) for f in out.files] == [("api/stale/left.json", True)]

    # And applying it removes the file, where writing it used to empty it.
    sandbox = types.SimpleNamespace(path=repo)
    fake = types.SimpleNamespace(config=config)
    applied, rejected = pipeline.Factory._apply_writes(fake, sandbox, out.files)
    assert applied == ["api/stale/left.json"] and rejected == []
    assert not (repo / "api" / "stale" / "left.json").exists()

    # A reader told what landed hears that it did.
    assert "Not on the branch" not in pipeline.what_landed(out.files, repo, lambda p: False)


def test_recordings_the_checks_left_are_not_pinned_on_the_first_blind_file():
    """The seam checks drive a browser and leave recordings. The first blind
    file was a pytest one, which clears no browser output, so the sweep after it
    claimed the seam checks' recordings -- and the packet said an API suite had
    driven a browser and left seven criteria without a recording that named
    them."""
    src = inspect.getsource(pipeline.Factory._assess)
    for run in re.finditer(r"blind = await self\._run_blind_suite\(", src):
        before = src[:run.start()].rstrip().splitlines()[-1].strip()
        assert before == 'sweep("")', (
            "the blind suite starts with the checks' recordings still in place")


def test_a_restatement_of_a_tried_repair_is_not_reported_as_never_attempted():
    """reviewer-19 was repaired and failed; only afterwards was it called a
    restatement of reviewer-10, which had never been routed. The packet then
    said reviewer-10 was never attempted, and reviewer-20 -- a restatement of
    reviewer-19 -- "never attempted: the loop stopped first"."""
    ledger = _ledger_with("stray artifact", "stray artifact again", "and again")
    a, b, c = list(ledger.records)
    ledger.dispose([FindingDisposition(finding_id=b, disposition="repair", reason="r")])
    ledger.attempted([b], 1)
    ledger.dispose([
        FindingDisposition(finding_id=c, disposition="repair", reason="s", duplicate_of=b),
        FindingDisposition(finding_id=a, disposition="repair", reason="r"),
    ])
    # Only now is b called what it was: a restatement of a.
    ledger.dispose([FindingDisposition(finding_id=b, disposition="repair", reason="s",
                                       duplicate_of=a)])
    ledger.finalise(stopped_early=True)

    assert ledger.records[a].attempts == 1, "the try at its restatement was a try at it"
    for fid in (a, b, c):
        assert ledger.records[fid].outcome == "attempted_not_fixed", fid
    assert "never attempted" not in ledger.records[c].outcome_evidence
    assert a in ledger.records[c].outcome_evidence, "settled through to the root"


def test_a_revalidation_does_not_drop_the_probes_the_last_pass_kept():
    """After a revalidation the breaker starts over on a branch that keeps
    every commit. The last pass's kept probe was counted as this pass's, found
    missing from the new suite, and reported "no longer kept" -- while the
    branch, which nothing had changed, went on carrying it."""
    def breaker(seq, promoted):
        return {"seq": seq, "kind": "breaker", "payload": {"promoted": promoted}}

    old = "web/acceptance/f_regression/proleptic_year.test.tsx"
    new = "api/tests/acceptance/f_regression/test_wrong_json_type.py"
    before = [breaker(10, [old]), breaker(20, [old])]

    assert pipeline.promoted_this_pass(before) == [old], "within a pass, it is still earlier"
    assert pipeline.promoted_this_pass(
        [*before, {"seq": 30, "kind": "revalidate", "payload": {}}]) == []
    assert pipeline.promoted_this_pass(
        [*before, {"seq": 30, "kind": "revalidate", "payload": {}}, breaker(40, [new]),
         breaker(50, [])]) == [new], "an empty round does not forget what was kept"

    src = inspect.getsource(pipeline.Factory._run_breaker)
    assert "earlier = promoted_this_pass(store.records())" in src


def test_a_level_with_no_reusable_setup_is_still_measured_for_leaks():
    """Library's integration tests had no shared setup, so the probe said there
    was "nothing to prove" and stopped -- and never noticed its own suite fails
    a second run. Whether tests leave things behind has nothing to do with
    reusable setup: the level's own checks, green a moment ago, run again. And
    the measurement outranks the reading: a level that fails it is not tested,
    whatever the reading says cleans up."""
    from factory.schemas import Gate
    from factory.workspace import LEAKS, unchecked_levels

    runs = []

    class Runner:
        carries_setup = True

        async def execute(self, command, *, cwd, timeout_s, network=True):
            runs.append(command)
            return gates.Execution(exit_code=1, output="expected 2 books, found 4")

    tier = _tier("integration", "inline_only")
    tier.run_by = ["api-verify"]
    [result] = asyncio.run(gates.probe_testing(
        _tier_surface(tier), [], [], [], "/tmp", Runner(),
        green=[Gate(name="api-verify", command="./mvnw verify")]))
    assert runs == ["./mvnw verify"] and result.leaves_clean is False
    assert "fail" in result.note and "expected 2 books" in result.evidence

    absent = _tier("user", "absent")
    absent.run_by = ["api-verify"]
    runs.clear()
    asyncio.run(gates.probe_testing(_tier_surface(absent), [], [], [], "/tmp", Runner(),
                                    green=[Gate(name="api-verify", command="./mvnw verify")]))
    assert runs == [], "a level with no runner has no tests of its own to re-run"

    class State:
        testing = _levels_surface(unit="harness", integration="harness", user="harness")
        testing_probe = [schemas.TierResult(tier="integration", claimed="usable", leaves_clean=False)]
        cleanup_applied = {}
    assert unchecked_levels(State()) == {"integration": LEAKS}
    State.cleanup_applied = {"integration": "Empty the library around every test"}
    assert unchecked_levels(State()) == {}, "a fix applied since the measurement counts"


def test_a_packet_carries_a_gist_written_for_a_list_and_readings_name_their_levels():
    """The overview shows each waiting feature in one line. A headline cut short
    by code reads as gibberish, so the report writer writes the short line once,
    with the packet. And a reading says which test levels each helper and each
    testing suggestion serves, so the Tests tab places them under their level
    by a field, not by guessing from prose."""
    from factory.schemas import PacketJudgement, Recommendation, ScaffoldFile
    assert "gist" in PacketJudgement.model_fields and "gist" in schemas.Packet.model_fields
    assert "gist=judgement.gist" in inspect.getsource(pipeline.order_packet)
    assert "`gist`" in (ROOT / "factory" / "roles" / "rapporteur.md").read_text()
    assert "levels" in Recommendation.model_fields and "levels" in ScaffoldFile.model_fields
    assert "`levels`" in (ROOT / "factory" / "roles" / "surveyor.md").read_text()
    app = app_js()
    assert "f.gist || f.headline" in app, "the overview cuts a headline instead of showing the gist"


def test_choosing_not_to_check_a_level_is_recorded_and_the_page_stops_asking(provider_app):
    """Apply with "Don't check criteria at this level" selected answered
    "nothing to apply", which read as a button that did nothing. It is a
    decision, recorded as one: the level stays unchecked, the card says what
    was chosen with a way back, and the Tests tab stops marking it. Applying a
    fix later takes the decision back."""
    from factory.projects import ProjectRegistry
    from factory.schemas import CleanupOption, TestingSurface, TestingTier

    client, tmp = provider_app
    repo = tmp / "repo"; repo.mkdir()
    registry = ProjectRegistry(tmp / "evidence")
    project = registry.create(repo, "demo")
    surface = TestingSurface(tiers=[TestingTier(
        tier="integration", verdict="usable", runner="junit", cleanup="", cleanup_by="nobody",
        cleanup_options=[CleanupOption(title="Empty the library", summary="s", agent_prompt="")])])
    registry.update(project, {"testing": surface})

    said = client.post(f"/api/projects/{project.id}/cleanup", json={"tier": "integration", "option": None})
    assert said.status_code == 200, said.text
    assert said.json()["project"]["cleanup_declined"] == ["integration"]
    said = client.post(f"/api/projects/{project.id}/cleanup", json={"tier": "integration", "reopen": True})
    assert said.json()["project"]["cleanup_declined"] == []

    app = app_js()
    assert "Nothing to apply" not in app
    assert "You chose not to have Fabrika check" in app and "data-reopen-cleanup" in app
    dot = app.split("function sectionDot(id, p) {")[1].split("\n}\n")[0]
    assert "undecidedLevels(p).length" in dot, "a level you decided about still marks the tab"


def test_accepting_a_new_dockerfile_rewrites_the_repositorys_file(tmp_path):
    """Applied to the record alone, the repository's copy would be read over it
    on the next load and the accepted change would silently not happen."""
    import subprocess
    from factory.projects import REPO_DOCKERFILE
    from factory.schemas import EnvironmentSpec, SurveyDiff

    registry, repo = _dockerfile_project(tmp_path)
    registry.move_dockerfile_into_repo(registry.get("demo"))
    diff = SurveyDiff(summary="s", environment=EnvironmentSpec(
        kind="generate", dockerfile="FROM python:3.13\n"))
    registry.apply_survey_diff(registry.get("demo"), diff, [], environment=True)
    shown = subprocess.run(["git", "show", f"main:{REPO_DOCKERFILE}"], cwd=repo,
                           capture_output=True, text=True).stdout
    assert shown == "FROM python:3.13\n"
    assert registry.get("demo").environment.dockerfile == "FROM python:3.13\n"


def test_a_resurvey_proposes_a_preview_without_replacing_the_environment(tmp_path):
    config, registry, project = _previewable(tmp_path)
    project.state.environment.preview = None
    registry.save(project, note="no preview yet")
    project = registry.get(project.id)
    diff = schemas.SurveyDiff(summary="x", preview=schemas.Preview(
        open="web", services=[_web_service()]), preview_reason="You can open the board.")

    kept, applied = registry.apply_survey_diff(project, diff, [])
    assert kept.state.environment.preview is None and "preview" not in applied

    updated, applied = registry.apply_survey_diff(registry.get(project.id), diff, ["preview"])
    assert applied == ["preview"]
    env = updated.state.environment
    assert env.kind == "host" and env.preview.open == "web", "the environment was replaced"
    assert "preview" in schemas.STATE_ADDITIONS[8][0]


def test_as_built_places_every_file_once_or_names_it():
    """The unclaimed record, one level up: a map that places seventy per cent
    of a repository says so and names the rest."""
    src = {p: asbuilt_graph.Source(path=p, blob=p, text="x\n") for p in ("a.py", "b.py", "lonely.py", "t.py")}
    recs = {"a.py": FileRecord(purpose="a", imports=["b.py"]), "b.py": FileRecord(purpose="b"),
            "lonely.py": FileRecord(purpose="alone"), "t.py": FileRecord(purpose="t", is_test=True)}
    src["a.py"] = asbuilt_graph.Source(path="a.py", blob="a", text="import b\n")
    ab = asbuilt_graph.build(src, recs)
    placed = [p for s in ab.subsystems for p in s.files]
    assert sorted(placed) == ["a.py", "b.py"]
    assert len(placed) == len(set(placed)), "a file placed twice"
    assert ab.coverage.unplaced == ["lonely.py"]
    assert ab.file("t.py").placed_by == "test"
    everyone = set(placed) | set(ab.coverage.unplaced) | set(ab.hubs) | {f.path for f in ab.files if f.is_test}
    assert everyone == set(recs)


def test_capabilities_keep_every_way_in_however_they_are_grouped(tmp_path):
    """The grouping is the model's; the membership rule is not. A way in the
    cartographer placed twice stays where it was placed first, and one it left
    out is shown, not lost. Plumbing it named is not reported as unused."""
    root = _ab_repo(tmp_path, _AB_FILES)
    capmap = CapabilityMap(
        capabilities=[CapabilityGroup(name="Make a card", summary="s", ways_in=["route:POST /api/cards"]),
                      CapabilityGroup(name="Make it again", summary="s", ways_in=["route:POST /api/cards"])],
        not_capabilities=["route:GET /health"])
    ab = _ab_read(root, _AbStub(capmap=capmap)).as_built
    placed = [w for c in ab.capabilities for w in c.ways_in]
    assert sorted(placed + ab.not_capabilities) == sorted(w.id for w in ab.ways_in)
    assert len(placed) == len(set(placed))
    assert [c.name for c in ab.capabilities] == ["Make a card", "Not yet grouped"]
    assert not any(f.kind == "no_caller" and "/health" in f.detail for f in ab.findings)


def test_a_capability_says_what_it_does_and_what_it_sets_in_motion():
    """Two depths, because following every call to its end in a pipeline
    reaches everything: the handler and what it calls, then one call further.
    Counted by file or part, so a place already in the first is not repeated."""
    texts = {"s.py": "import a\ndef handle():\n    a()\n", "a.py": "import b\ndef a():\n    b()\n",
             "b.py": "import c\ndef b():\n    c()\n", "c.py": "def c():\n    pass\n"}
    src = {p: asbuilt_graph.Source(path=p, blob=p, text=t) for p, t in texts.items()}
    recs = {"s.py": FileRecord(purpose="s", imports=["a.py"], functions=[dict(name="handle", line=2, calls=["a"])],
                               ways_in=[dict(kind="command", name="go", handler="handle")]),
            "a.py": FileRecord(purpose="a", imports=["b.py"], functions=[dict(name="a", line=2, calls=["b"])]),
            "b.py": FileRecord(purpose="b", imports=["c.py"], functions=[dict(name="b", line=2, calls=["c"])]),
            "c.py": FileRecord(purpose="c", functions=[dict(name="c", line=1)])}
    ab = asbuilt_graph.build(src, recs)
    direct, motion = asbuilt_graph.reach(ab, recs, ["command:go"])
    assert sorted(r.file for r in direct) == ["a.py", "s.py"], "the handler, and what it calls"
    assert [r.file for r in motion] == ["b.py"], "one call further; c.py is beyond"


def test_a_note_that_names_a_route_is_not_calling_it():
    """What calls a route is what runs. A testing report and a verification
    note under `work/` were listed as what calls a capability because they
    named its routes; a document -- by kind, or by being prose whatever the
    reader called it -- is never a caller, and draws no link."""
    texts = {"api/server.py": "@app.get('/api/cards')\ndef cards():\n    return []\n",
             "web/app.js": "fetch('/api/cards')\n",
             "work/report.md": "We checked GET /api/cards and it answered.\n",
             "work/notes.txt": "curl /api/cards\n"}
    src = {p: asbuilt_graph.Source(path=p, blob=p, text=t) for p, t in texts.items()}
    call = [dict(path="/api/cards", method="GET")]
    recs = {"api/server.py": FileRecord(purpose="s", functions=[dict(name="cards", line=2)],
                                        ways_in=[dict(kind="route", name="/api/cards", method="GET", handler="cards")]),
            "web/app.js": FileRecord(purpose="w", routes_called=call),
            "work/report.md": FileRecord(purpose="r", kind="docs", routes_called=call),
            "work/notes.txt": FileRecord(purpose="n", kind="test", is_test=True, routes_called=call)}
    ab = asbuilt_graph.build(src, recs)
    way = next(w for w in ab.ways_in if w.name == "/api/cards")
    assert way.callers == ["web/app.js"]
    assert not any(e.source.startswith("work/") for e in ab.edges)


def test_how_it_runs_is_the_model_s_placement_held_to_the_graph(tmp_path):
    """Where each subsystem runs and what an outside system is are the model's
    to say; the rest is code's. A subsystem goes in one place and one left out
    is not lost; a mention is one system's, matched however it was cased; a
    route an outside system calls must be one nothing here calls; HTTP between
    processes is counted from the route calls code matched; a person comes in
    through a screen. An unchanged system is not asked again."""
    records = {**_AB_RECORDS, "web/app.js": {**_AB_RECORDS["web/app.js"],
                                             "ways_in": [dict(kind="screen", name="/cards", handler="openCard")]}}
    root = _ab_repo(tmp_path, _AB_FILES)
    plain = _ab_read(root, _AbStub(records=records)).as_built
    web, api = plain.file("web/app.js").subsystem, plain.file("api/server.py").subsystem
    assert web and api and web != api
    cards = next(c for c in plain.capabilities if c.name == "Work with cards")
    account = ArchitectureAccount(
        runtimes=[RuntimeGroup(name="Browser app", kind="browser", subsystems=[web, "nowhere.py"]),
                  RuntimeGroup(name="API server", kind="server", subsystems=[api, web])],
        actors=[Actor(name="Card keeper", capabilities=[cards.code, "ZZZ"])],
        outside=[OutsideSystemGroup(name="SQLite", kind="datastore", mentions=["sqlite", "Postgres"],
                                    calls_in=["route:GET /health", "route:POST /api/cards"])],
        dismissed=[DismissedMention(mention="SQLite", why="library")])
    stub = _AbStub(records=records, architecture=account)
    reading = _ab_read(root, stub)
    arch = reading.as_built.architecture
    runs = {r.name: r.subsystems for r in arch.runtimes}
    assert runs["Browser app"] == [web] and runs["API server"] == [api], "one place each, first wins"
    placed = {k for r in arch.runtimes for k in r.subsystems} | set(arch.tooling)
    assert placed == {s.key for s in reading.as_built.subsystems}, "a subsystem the account left out is still placed"
    db = arch.outside[0]
    assert (db.mentions, db.files, db.evidence, db.entities_written) == (["SQLite"], ["api/store.py"], "code", ["cards"])
    assert db.calls_in == ["route:GET /health"], "the browser calls POST /api/cards, so no outside system does"
    assert "SQLite" not in [m for v in arch.dismissed.values() for m in v], "a mention is sorted once"
    assert [(l.source, l.target, l.kind, l.confirmed) for l in arch.links] == [("browser-app", "api-server", "http", True)]
    person = arch.actors[0]
    assert person.capabilities == [cards.key] and person.enters == ["browser-app"]

    asbuilt.commit_into_repo(root, reading, project_name="cards")
    readme = (root / ".fabrika/as-built/README.md").read_text()
    assert "## How it runs" in readme and "r_browser_app -->|\"HTTP · 1\"| r_api_server" in readme
    previous, known = asbuilt.load_committed(root)
    again = _AbStub(records=records, architecture=account)
    _ab_read(root, again, previous=previous, previous_records=known)
    assert getattr(again, "architecture_prompts", 0) == 0, "the same system is not asked how it runs again"


def test_what_builds_or_ships_the_system_is_tooling_by_the_files_that_name_it(tmp_path):
    """GitHub Actions and Docker were drawn beside PostgreSQL as systems the
    app talks to while it runs. The cartographer had no word for CI and
    called them platforms, and a Dockerfile grouped with the API's own setup
    made Docker look reached by the server. Which files name a system says
    which it is: only build, CI and dependency files means tooling, whatever
    the model called it; code that runs -- a settings module included --
    means it is used while running, whatever the model called it."""
    files = {**_AB_FILES, "api/Dockerfile": "FROM python:3.12\n",
             "docker-compose.yml": "services:\n  api:\n    build: api\n",
             ".github/workflows/ci.yml": "on: push\njobs: {}\n",
             "api/settings.py": "REDIS_URL = 'redis://cache'\n",
             "DEPLOY.md": "Shipped with Fly.io.\n"}
    records = {**_AB_RECORDS,
               "api/Dockerfile": dict(purpose="Builds the API image.", outside=["Docker"]),
               "docker-compose.yml": dict(purpose="Runs the API locally.", outside=["Docker"]),
               ".github/workflows/ci.yml": dict(purpose="Runs the tests on push.", outside=["GitHub Actions"]),
               "api/settings.py": dict(purpose="The API's settings.", outside=["Redis"]),
               "DEPLOY.md": dict(purpose="How it ships.", kind="docs", outside=["Fly.io"])}
    root = _ab_repo(tmp_path, files)
    plain = _ab_read(root, _AbStub(records=records)).as_built
    account = ArchitectureAccount(
        runtimes=[RuntimeGroup(name="API server", kind="server", subsystems=[s.key for s in plain.subsystems])],
        outside=[OutsideSystemGroup(name="Docker", kind="platform", mentions=["Docker"], calls_in=["route:GET /health"]),
                 OutsideSystemGroup(name="GitHub Actions", kind="platform", mentions=["GitHub Actions"]),
                 OutsideSystemGroup(name="SQLite", kind="tooling", mentions=["SQLite"]),
                 OutsideSystemGroup(name="Redis", kind="platform", mentions=["Redis"]),
                 OutsideSystemGroup(name="Fly.io", kind="tooling", mentions=["Fly.io"])])
    reading = _ab_read(root, _AbStub(records=records, architecture=account))
    arch = reading.as_built.architecture
    by_name = {o.name: o for o in arch.outside}
    assert [n for n, o in by_name.items() if o.tooling] == ["Docker", "GitHub Actions", "Fly.io"]
    assert by_name["Docker"].kind == "tooling" and by_name["Docker"].runtimes == [], \
        "a health check it probes does not make it something the server talks to"
    assert by_name["SQLite"].kind == "other" and by_name["SQLite"].runtimes == ["api-server"], \
        "code that runs names it, so it is not tooling whatever the model said"
    assert not by_name["Redis"].tooling, "configuring a system is using it"

    asbuilt.commit_into_repo(root, reading, project_name="cards")
    readme = (root / ".fabrika/as-built/README.md").read_text()
    assert "- **Build and tooling** — Docker, GitHub Actions, Fly.io; nothing of it runs." in readme
    assert "o_docker" not in readme and "o_sqlite" in readme, "tooling is listed under the picture, not drawn in it"


def test_v13_nothing_under_fabrika_rides_a_feature_branch():
    """Feature readings stay in the feature's ledger. The as-built in the
    repository is the owner's, written on their press -- the rule the
    Dockerfile already follows -- so the pipeline never writes it there."""
    source = factory_source("pipeline")
    assert "commit_into_repo" not in source and "write_into" not in source
    assert workspace.NOT_FABRIKA == ":(exclude,glob).fabrika/**"
    assert asbuilt_graph.AS_BUILT_DIR.startswith(workspace.FABRIKA_DIR + "/")


def test_a_tests_check_cannot_be_held_at_its_number(tmp_path):
    from factory.projects import ProjectError
    from factory.schemas import Gate, GateResult

    gates = [Gate(name="cov", command="cov", parse_metric=r"(\d+)%", family="tests")]
    results = [GateResult(name="cov", exit_code=1, metric=72)]
    registry, project = _project_with_suggestions(tmp_path, results, gates)
    with pytest.raises(ProjectError, match="tests check"):
        registry.ratchet_check(project, "cov")


def test_a_check_red_under_its_own_floor_says_so_without_a_model():
    """The kind is code's: read from what was read, the same way every time."""
    from factory.diagnosis import classify
    from factory.schemas import Gate, GateReport, GateResult

    files = {"api/pyproject.toml": _PYPROJECT}
    read = files.get
    report = GateReport(results=[
        GateResult(name="api-tests", exit_code=1, metric=78.02, output_tail="33 passed"),
        GateResult(name="lint", exit_code=1, output_tail="E501"),
        GateResult(name="slow", exit_code=-9, timed_out=True),
        GateResult(name="gone", exit_code=127, output_tail="sh: tool: command not found"),
        GateResult(name="ok", exit_code=0, passed=True)])
    gates = [_floor_gate(), Gate(name="lint", command="lint"), Gate(name="slow", command="s"),
             Gate(name="gone", command="tool"), Gate(name="ok", command="true")]
    classify(gates, report, read)
    kinds = {r.name: r.red_kind for r in report.results}
    assert kinds == {"api-tests": "under_own_floor", "lint": "failed", "slow": "timed_out",
                     "gone": "didnt_run", "ok": ""}
    assert report.results[0].own_floor_value == 95


def test_a_floor_in_the_command_is_read_from_the_declaration():
    from factory.diagnosis import own_floor_value
    from factory.schemas import Gate, OwnFloor

    gate = Gate(name="c", command="pytest --cov-fail-under=90", own_floor=OwnFloor(where="command", value=90))
    assert own_floor_value(gate, lambda _p: None) == 90


def test_a_held_hold_is_never_a_fix_a_diagnosis_may_offer_for_a_tests_check():
    from factory.diagnosis import usable_fixes
    from factory.schemas import DiagnosisAnswer, DiagnosisFix, GateResult

    answer = DiagnosisAnswer(cause="c", fixes=[
        DiagnosisFix(kind="hold", title="hold it"),
        DiagnosisFix(kind="repo_change", title="escape", path="../outside", contents="x"),
        DiagnosisFix(kind="repo_change", title="fragment", path="a.toml", contents=""),
        DiagnosisFix(kind="repo_change", title="fine", path="api/pyproject.toml", contents="y")])
    kept = usable_fixes(_floor_gate(), GateResult(name="api-tests", metric=78.0), answer)
    assert [f.title for f in kept] == ["fine"]


def test_a_fix_is_tried_before_it_is_offered_and_the_checkout_is_put_back(tmp_path):
    import asyncio

    from factory.config import DiagnosisConfig
    from factory.diagnosis import diagnose_check
    from factory.schemas import DiagnosisAnswer, DiagnosisFix, Gate, GateResult

    repo = _repo_with_one_commit(tmp_path / "repo", {"floor.txt": "low\n"})
    gate = Gate(name="c", command="grep -q high floor.txt", family="quality")
    result = GateResult(name="c", exit_code=1, red_kind="failed")
    asked: list[str] = []

    async def ask(prompt):
        asked.append(prompt)
        return DiagnosisAnswer(cause="The file says low.", where="project", recommended=1, fixes=[
            DiagnosisFix(kind="command_change", title="look for low", command="grep -q nope floor.txt"),
            DiagnosisFix(kind="repo_change", title="say high", path="floor.txt", contents="high\n")])

    found = asyncio.run(diagnose_check(gate=gate, result=result, fp="f", prompt="p", cwd=repo,
                                       runner=None, ask=ask, settings=DiagnosisConfig(),
                                       session=_session()))
    tries = {t.fix: t for t in found.tries}
    assert tries[1].ran and tries[1].passed and tries[0].ran and not tries[0].passed
    assert found.confirmed, "a fix whose try passed was not called confirmed"
    assert (repo / "floor.txt").read_text() == "low\n", "a try left its change in the checkout"
    assert len(asked) == 1


def test_a_check_that_passes_when_run_again_is_flaky_and_no_model_is_asked(tmp_path):
    import asyncio

    from factory.config import DiagnosisConfig
    from factory.diagnosis import diagnose_check
    from factory.schemas import Gate, GateResult

    gate = Gate(name="c", command="true")
    result = GateResult(name="c", exit_code=1, red_kind="failed")

    async def ask(prompt):
        raise AssertionError("a model was asked about a check that passed the second time")

    found = asyncio.run(diagnose_check(gate=gate, result=result, fp="f", prompt="p", cwd=tmp_path,
                                       runner=None, ask=ask, settings=DiagnosisConfig(),
                                       session=_session()))
    assert found.kind == "flaky" and result.red_kind == "flaky"


def test_a_diagnosis_that_fails_says_so_and_hides_nothing(tmp_path):
    import asyncio

    from factory.config import DiagnosisConfig
    from factory.diagnosis import diagnose_check
    from factory.schemas import Gate, GateResult

    async def ask(prompt):
        raise RuntimeError("no route")

    found = asyncio.run(diagnose_check(
        gate=Gate(name="c", command="false"), result=GateResult(name="c", exit_code=1, red_kind="failed"),
        fp="f", prompt="p", cwd=tmp_path, runner=None, ask=ask, settings=DiagnosisConfig(),
        session=_session()))
    assert "no route" in found.failed and not found.fixes
    app = app_js()
    assert "Fabrika couldn't work out why" in app


def test_a_diagnosis_may_ask_for_files_once(tmp_path):
    import asyncio

    from factory.config import DiagnosisConfig
    from factory.diagnosis import diagnose_check
    from factory.schemas import DiagnosisAnswer, Gate, GateResult

    repo = _repo_with_one_commit(tmp_path / "repo", {"ci.yml": "python-version: '3.12'\n"})
    asked: list[str] = []

    async def ask(prompt):
        asked.append(prompt)
        return (DiagnosisAnswer(need_files=["ci.yml", "../etc/passwd"]) if len(asked) == 1
                else DiagnosisAnswer(cause="Different Python.", where="measurement"))

    found = asyncio.run(diagnose_check(
        gate=Gate(name="c", command="false"), result=GateResult(name="c", exit_code=1, red_kind="failed"),
        fp="f", prompt="p", cwd=repo, runner=None, ask=ask, settings=DiagnosisConfig(),
        session=_session()))
    assert len(asked) == 2 and "python-version: '3.12'" in asked[1]
    assert "(not in the repository)" in asked[1], "a path outside the repository was read"
    assert found.cause == "Different Python."


def test_recording_which_versions_to_read_costs_no_run_of_the_checks(tmp_path):
    from factory.schemas import GateReport

    registry, project = _approved_project(tmp_path)
    before = registry.approval_fingerprint(project)
    project.state.baseline = GateReport(results=[])
    project = registry.save(project)
    env = project.state.environment.model_copy(update={"version_commands": ["python --version"],
                                                       "version_files": [".python-version"]})
    project = registry.update(project, {"environment": env})
    assert project.state.baseline is not None, "describing the environment cleared the run"
    assert registry.approval_fingerprint(project) == before


def test_a_floor_kept_in_a_config_that_is_code_is_read_from_its_declared_number():
    from factory.diagnosis import own_floor_value
    from factory.schemas import Gate, OwnFloor

    gate = Gate(name="web-tests", command="vitest", own_floor=OwnFloor(
        where="web/vite.config.ts#test.coverage.thresholds.lines", value=55))
    files = {"web/vite.config.ts": "export default { test: { coverage: { thresholds: { lines: 55 } } } }"}
    assert own_floor_value(gate, files.get) == 55


def test_checks_the_environment_never_started_are_not_diagnosed():
    """Every check blocked by one setup failure cost four model calls, each
    explaining what the run had already said."""
    from factory.diagnosis import worth_diagnosing
    from factory.schemas import Gate, GateResult

    gates = [Gate(name=n, command="x") for n in ("a", "b", "c")]
    results = [GateResult(name="a", started=False, exit_code=1, red_kind="didnt_run"),
               GateResult(name="b", exit_code=127, red_kind="didnt_run"),
               GateResult(name="c", exit_code=1, red_kind="failed")]
    assert [r.name for r in worth_diagnosing(results, gates, 4)] == ["c", "b"]


def test_a_turned_down_coverage_suggestion_is_known_as_one(tmp_path):
    from factory.schemas import Recommendation

    registry, project = _project_with_suggestions(tmp_path)
    project.store.append("survey", {}, role="surveyor")
    rec = Recommendation(title="Measure coverage", kind="tests", why="w", evidence="e",
                         would_gate="pytest --cov --cov-report=xml:{report}", report_format="cobertura")
    project = registry.raise_recommendation(project, rec) or registry.get(project.id)
    key = registry.recommendation_key(rec)
    project = registry.decline_recommendation(project, key, "a prototype")
    ruling = next(r for r in project.state.recommendation_rulings if r.key == key)
    assert ruling.report_format == "cobertura" and ruling.reason == "a prototype"


def test_nothing_fabrika_keeps_decides_which_guide_binds(tmp_path):
    """What binds is what is committed on the branch features start from:
    no approval, no stored list, nothing another tool cannot see."""
    import subprocess

    from factory.projects import ProjectRegistry
    from factory.schemas import Guide, ProjectState

    assert "guides" not in ProjectState.model_fields
    assert not {"status", "approved", "found_by", "reason"} & set(Guide.model_fields)
    old = ProjectState.model_validate({"project_id": "p", "guides": [{"path": "X.md", "status": "approved"}]})
    assert "guides" not in old.model_dump(), "a retired approval list was carried on"
    repo = _guide_repo(tmp_path)
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")
    project.state.base_ref = "HEAD"
    assert "CONTRIBUTING.md" not in {g.path for g in registry.guides(project)}
    (repo / "AGENTS.md").write_text("# Agents\n\nRead [CONTRIBUTING.md](CONTRIBUTING.md).\n")
    assert "CONTRIBUTING.md" not in {g.path for g in registry.guides(project)}, \
        "an uncommitted edit bound a feature"
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "link"],
                   cwd=repo, check=True)
    assert "CONTRIBUTING.md" in {g.path for g in registry.guides(project)}, \
        "a document AGENTS.md points to does not bind"


def test_every_writing_and_judging_role_gets_the_section_built_by_code():
    src = class_source(pipeline.Factory)
    for call in ('self._guidance(store, state, "scout", inferred=False',
                 'self._guidance(store, state, "architect")',
                 'self._unit_guidance(\n                    store, state, "worker", list(unit.files_expected))',
                 'self._unit_guidance(store, state, "integrator")',
                 'self._unit_guidance(store, state, "repairer", list(unit.files_expected))',
                 'self._unit_guidance(store, state, "simplifier", list(files))',
                 'self._guidance(store, state, "reviewer"',
                 'self._guidance(store, state, "breaker", inferred=False)'):
        assert call in src, call
    assert "guide_index=guide_lib_index(self._guides_at(self._base_of(state)))" in src
    brief = inspect.getsource(__import__("factory.executors", fromlist=["x"]).unit_brief)
    assert "guidance = unit.harness_guidance if harness else unit.guidance" in brief


def test_a_units_guidance_is_never_written_by_the_architect_nor_recorded():
    from factory.schemas import WorkUnit

    schema = json.dumps(WorkUnit.model_json_schema())
    assert "guidance" not in schema and '"guides"' not in schema, \
        "the architect can assign guides, or write a unit's guidance"
    unit = WorkUnit(id="U-1", title="t", objective="o", guidance="# How this repository is written",
                    harness_guidance="## Rules your harness did not load")
    assert "guidance" not in unit.model_dump() and "harness_guidance" not in unit.model_dump()
    from factory.executors import unit_brief
    from factory.schemas import Spec
    spec = Spec(title="t", intent="i", summary="s")
    assert "# How this repository is written" in unit_brief(unit, spec, "")
    harness = unit_brief(unit, spec, "", harness=True)
    assert "Rules your harness did not load" in harness and "# How this repository is written" not in harness, \
        "guide text was pasted into a harness worker's brief"


def test_what_each_agent_was_handed_is_recorded(tmp_path):
    from factory.config import Config, GuidesConfig
    from factory.pipeline import Factory
    from factory.projects import ProjectRegistry
    from factory.schemas import ObservedConvention, ScoutReport
    from factory.store import EvidenceStore
    from types import SimpleNamespace

    repo = _guide_repo(tmp_path)
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")
    project.state.base_ref = "HEAD"
    store = EvidenceStore(tmp_path / "ev", "f")
    store.append("scout", ScoutReport(summary="s", do_not_duplicate=["SECRET helper"],
                 observed_conventions=[ObservedConvention(rule="SECRET rule", slug="x", files=[])]),
                 role="scout")
    factory = Factory.__new__(Factory)
    factory.project, factory.config = project, Config(guides=GuidesConfig())
    state = SimpleNamespace(sandbox=None)
    oracle = factory._guidance(store, state, "oracle", inferred=False)
    assert "Every test owns its data." in oracle and "SECRET" not in oracle, \
        "something a model read in the code reached the verify lane"
    unit = factory._unit_guidance(store, state, "worker", ["server/x.py"])
    assert "SECRET rule" in unit["guidance"] and "SECRET helper" in unit["harness_guidance"]
    assert "No ORM in routers." in unit["guidance"] and "No ORM in routers." not in unit["harness_guidance"]
    assert {g["path"] for g in unit["guide_files"]} >= {"AGENTS.md", ".claude/skills/migrate/SKILL.md"}
    handed = [r["payload"] for r in store.records() if r["kind"] == "guides_delivered"]
    assert [h["role"] for h in handed] == ["oracle"], \
        "a unit's guides were recorded before anyone knew which runner would take it"
    factory._guides_recorder(store, "worker", ["server/x.py"])(
        {"harness": True, "route": "codex", "guides": [{"path": "AGENTS.md", "sha256": "a"}]})
    last = [r["payload"] for r in store.records() if r["kind"] == "guides_delivered"][-1]
    assert last["role"] == "worker" and last["route"] == "codex"
    assert all(g["sha256"] for g in handed[0]["guides"]), "a file was recorded without its hash"


# -- guides, cited (phase 4) -------------------------------------------------------


def test_a_finding_s_citation_is_checked_against_the_guide_by_code():
    from factory.guides import verify_citations
    from factory.schemas import Citation, Finding

    guide = "# Agents\n\nRouters never touch the ORM directly; go through **app/scoping.py**.\n"
    read = {"AGENTS.md": guide}.get
    def f(cites, category="convention", severity="major"):
        return Finding(id="F-1", title="t", severity=severity, category=category, detail="d", cites=cites)
    out = verify_citations([
        f(Citation(source="guide", ref="AGENTS.md", quote="Routers never touch the ORM directly; go through app/scoping.py")),
        f(Citation(source="guide", ref="AGENTS.md", quote="Routers must log every request")),
        f(Citation(source="observed", ref="routers-dir")),
        f(None),
        f(None, category="security", severity="blocker"),
        f(Citation(source="guide", ref="NOTES.md", quote="Routers never touch the ORM directly")),
    ], read, {"AGENTS.md"}, {"routers-dir"})
    assert out[0].cite_verified is True and out[0].severity == "major", "a quoted rule was not found"
    assert out[1].cite_verified is False and out[1].severity == "minor", "a made-up rule kept its weight"
    assert out[2].severity == "minor", "an inferred convention outranked a written rule"
    assert out[3].severity == "minor", "a style finding with no rule kept its weight"
    assert out[4].severity == "blocker", "a security finding was capped for citing no rule"
    assert out[5].cite_verified is False, "a file that does not bind was cited as a rule"
    import json as _json
    from factory.schemas import Finding as F
    assert "cite_verified" not in _json.dumps(F.model_json_schema()), "a model could claim its own verification"


def test_every_review_is_held_to_what_it_cites_before_it_is_recorded():
    src = inspect.getsource(pipeline.Factory._review_panel)
    assert src.index("self._verify_citations(store, state, merged.findings)") < src.index('store.append("review", merged')
    simplify = inspect.getsource(pipeline.Factory._simplify_round)
    assert "The rule:" in simplify, "the simplifier is not given the rule it is closing"
    reviewer = (ROOT / "factory" / "roles" / "reviewer.md").read_text()
    assert "**quoted word for word**" in reviewer
    verify = inspect.getsource(pipeline.Factory._verify_citations)
    assert "binding = {g.path for g in self._guides_at(base)}" in verify
