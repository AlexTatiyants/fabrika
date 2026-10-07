"""One full run of the pipeline with every model call stubbed.

V-1 asserts the air gap by introspection. This asserts it dynamically: the stub
raises if repository text or implementation source ever appears in the oracle's
prompt, which is the only way to catch a leak that arrives through a path the
static check did not think of.

It also exercises the parts of `pipeline.py` that nothing else touches — the two
lanes converging, writes reaching disk, phase bookkeeping, and the restoration
pass running against a rapporteur that returned almost nothing.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from factory.config import Config, PathsConfig, PipelineConfig, RoleConfig
from factory import pipeline
from factory.pipeline import PHASE_NAMES, Factory
from factory.projects import ProjectError, ProjectRegistry
from factory.schemas import (
    AcceptanceCriterion,
    Ambiguity,
    ArbiterReport,
    BlindPlacement,
    BreakerSuite,
    BreakerTest,
    Decision,
    EnvironmentSpec,
    FileWrite,
    Finding,
    Gate,
    GateReport,
    GateResult,
    FindingDisposition,
    IntegrationReport,
    InterrogationReport,
    MaterialityItem,
    OracleSuite,
    PacketJudgement,
    PlacementResult,
    Plan,
    RecheckReport,
    RepairPlan,
    RepairUnit,
    RepairVerdict,
    ReviewReport,
    ScoutReport,
    SelfDisclosure,
    Spec,
    TestFile,
    TestFileCommand,
    WorkerOutput,
    WorkUnit,
)


def _fails_only(path: str) -> str:
    """A stub per-file test command: fails for one named file, passes otherwise.

    Nothing parses its output, because nothing parses any runner's output any
    more. The exit code is the whole verdict for the file it was pointed at,
    which is exactly what running the blind suite one file at a time buys.
    """
    return f'test "{{path}}" != "{path}"'


ROLES = [
    "scout", "interrogator", "spec_writer", "architect", "worker",
    "integrator", "oracle", "reviewer", "adversary", "rapporteur",
    "breaker", "arbiter", "remediator", "repairer",
]

PANEL_ROLE = "security"

SECRET_REPO_TOKEN = "parse_interval"          # exists only in the repo
SECRET_IMPL_TOKEN = "def spin_the_widget"     # exists only in what the workers wrote


def _spec() -> Spec:
    return Spec(
        title="Widget", intent="build a widget", summary="a widget that spins and logs",
        acceptance_criteria=[
            AcceptanceCriterion(id="AC-1", statement="the widget spins"),
            AcceptanceCriterion(id="AC-2", statement="the widget stops"),
            AcceptanceCriterion(id="AC-3", statement="the widget logs"),
        ],
    )


CANNED = {
    "scout": ScoutReport(
        summary="a small repo",
        do_not_duplicate=[f"core/timeparse.py:{SECRET_REPO_TOKEN}()"],
    ),
    "interrogator": InterrogationReport(
        restated_intent="build a widget",
        ambiguities=[
            Ambiguity(id="Q-1", question="spin which way?", why_it_matters="direction is persisted",
                      options=["clockwise", "anticlockwise"], proposed_default="clockwise",
                      severity="blocking"),
            Ambiguity(id="Q-2", question="log where?", why_it_matters="visibility",
                      options=["stdout", "a file"], proposed_default="stdout", severity="minor"),
        ],
    ),
    "spec_writer": _spec(),
    "architect": Plan(
        summary="two units", seams=["spin() signature"],
        units=[
            WorkUnit(id="U-1", title="spin", objective="make it spin",
                     criterion_ids=["AC-1", "AC-2"], files_expected=["widget/spin.py"]),
            WorkUnit(id="U-2", title="log", objective="make it log",
                     criterion_ids=["AC-3"], files_expected=["widget/log.py"]),
        ],
    ),
    "integrator": IntegrationReport(
        summary="wired the units together",
        files=[FileWrite(path="widget/__init__.py", contents="from .spin import spin_the_widget\n"),
               # The seam check it leaves behind, run as a gate by the
               # project's own per-file rule; it has to pass in the fixture's
               # checkout. `seam` in the NAME is what marks it as one.
               FileWrite(path="tests/test_seam_spin_log.py",
                         contents="def test_seam_spin_log():\n    assert True\n")],
        decisions=[Decision(id="D-1", title="export the entry point", rationale="callers need it",
                            criterion_ids=["AC-1"], files=["widget/__init__.py"])],
    ),
    "oracle": OracleSuite(
        strategy="written from the spec alone",
        tests=[
            TestFile(path="tests/test_spin.py", contents="def test_spin(): ...", criterion_ids=["AC-1"]),
            TestFile(path="tests/test_log.py", contents="def test_log(): ...", criterion_ids=["AC-3"]),
        ],
        untestable_criteria=["AC-2"],
    ),
    "reviewer": ReviewReport(
        summary="mostly fine", verdict="accept_with_changes",
        findings=[Finding(id="F-1", severity="major", title="duplicated helper", detail="d",
                          files=["widget/spin.py"])],
    ),
    "adversary": ReviewReport(
        summary="the case against", verdict="accept_with_changes",
        strongest_objection="the stop path was never built",
        findings=[Finding(id="A-1", severity="blocker", title="no stop path", detail="d")],
    ),
    "breaker": BreakerSuite(
        strategy="attacked the stop path",
        command="exit 1",
        tests=[BreakerTest(
            path="tests/oracle/test_breaker_stop.py",
            contents="def test_stops_twice(): ...",
            hypothesis="stopping twice leaves the widget spinning",
            severity="major", targets=["widget/spin.py"],
        )],
        conceded=["the log path holds up"],
    ),
    "remediator": RepairPlan(
        summary="one unit, one finding",
        units=[RepairUnit(
            id="R-1", title="stop the duplication", finding_ids=["reviewer-1"],
            objective="remove the duplicated helper",
            files_expected=["widget/spin.py"],
            constraints="do not refactor anything else",
        )],
        deferred=[],
    ),
    # A rapporteur that ranks nothing and classifies one file. Every decision,
    # every finding and the second file are restored around it.
    "rapporteur": PacketJudgement(
        headline="ships with rulings", verdict="ship_with_rulings", attention_budget_minutes=5,
        materiality=[MaterialityItem(path="widget/spin.py", klass="novel", lines=0,
                                     reason="the feature itself")],
        decision_order=[], finding_order=[],
    ),
}

WORKERS = {
    "U-1": WorkerOutput(
        unit_id="U-1", summary="it spins",
        files=[FileWrite(path="widget/spin.py", contents=f"{SECRET_IMPL_TOKEN}():\n    return 1\n")],
        decisions=[Decision(id="D-1", title="spin clockwise", rationale="the human said so",
                            criterion_ids=["AC-1"], files=["widget/spin.py"],
                            reversibility="irreversible", confidence=0.4)],
        disclosure=SelfDisclosure(not_implemented=["AC-2, the stop path"]),
    ),
    "U-2": WorkerOutput(
        unit_id="U-2", summary="it logs",
        files=[FileWrite(path="widget/log.py", contents="def log(message):\n    print(message)\n")],
        decisions=[Decision(id="D-1", title="log to stdout", rationale="the default",
                            criterion_ids=["AC-3"], files=["widget/log.py"])],
        disclosure=SelfDisclosure(),  # deliberately empty
    ),
}


REPAIR = WorkerOutput(
    unit_id="R-1", summary="dropped the duplicated helper",
    files=[FileWrite(path="widget/spin.py",
                     contents=f"{SECRET_IMPL_TOKEN}():\n    return 1  # deduplicated\n")],
    decisions=[Decision(id="D-1", title="use the existing helper", rationale="it was already there",
                        blast_radius="callers of the removed helper", files=["widget/spin.py"])],
    disclosure=SelfDisclosure(flags=["touched the spin path"]),
)


class StubLLM:
    """Every model call in the system, answered from a table."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.oracle_prompts: list[str] = []
        self.recheck_prompts: list[str] = []
        self.review_prompts: list[str] = []
        self.arbiter_prompts: list[str] = []

    async def ask(self, role, prompt, schema=None, *, system="", temperature=None):
        self.calls.append(role)
        if schema is ReviewReport:
            self.review_prompts.append(prompt)
        assert system, f"role {role} was called without its prompt file"
        if role == "oracle":
            self.oracle_prompts.append(prompt)
            assert SECRET_REPO_TOKEN not in prompt, "the repository leaked into the verify lane"
            assert SECRET_IMPL_TOKEN not in prompt, "the implementation leaked into the verify lane"
        # The re-check reuses the review agents, so the role alone no longer
        # says what is being asked for.
        if schema is RecheckReport:
            self.recheck_prompts.append(prompt)
            return RecheckReport(
                verdicts=[RepairVerdict(finding_id="reviewer-1", status="fixed",
                                        evidence="widget/spin.py no longer defines it twice")],
                notes="",
            )
        if role == "arbiter":
            # Routes one finding and deliberately leaves the rest unmentioned:
            # the orchestrator must default those to a human, not to silence --
            # and must ask again first, naming the ones it skipped.
            self.arbiter_prompts.append(prompt)
            return ArbiterReport(
                summary="one mechanical fix, the rest need a person",
                dispositions=[FindingDisposition(
                    finding_id="reviewer-1", disposition="repair",
                    reason="a duplicated helper is a mechanical fix",
                    repair_hint="call the existing one", confidence=0.8,
                )],
            )
        if role == PANEL_ROLE:
            return ReviewReport(
                summary="one angle on the same evidence", verdict="accept_with_changes",
                strongest_objection="",
                findings=[Finding(id="X", severity="major", title="widened permission",
                                  detail="d", evidence="widget/spin.py")],
                conceded=["the batching is fine"],
            )
        if role == "repairer":
            assert "Your work unit: R-1" in prompt
            return REPAIR.model_copy(deep=True)
        if role in ("worker", "integrator"):
            for unit_id, output in WORKERS.items():
                if f"Your work unit: {unit_id}" in prompt:
                    return output.model_copy(deep=True)
            if "Your work unit: integration" in prompt:
                # Integration goes through the same executor now: it reads the
                # merged tree and closes seams like any other unit of work.
                seam = CANNED["integrator"]
                return WorkerOutput(
                    unit_id="integration", summary=seam.summary,
                    files=[f.model_copy(deep=True) for f in seam.files],
                    decisions=[d.model_copy(deep=True) for d in seam.decisions],
                    disclosure=SelfDisclosure(flags=list(seam.seam_issues),
                                              not_implemented=list(seam.unresolved)),
                )
            raise AssertionError("a worker was called with no unit in its brief")
        return CANNED[role].model_copy(deep=True)

    def usage_report(self):
        return {"roles": {}, "total_tokens": 0, "total_cost": 0.0,
                "notional_cost": 0.0, "unbilled_turns": 0, "calls": len(self.calls)}

    def coverage(self):
        """Everything here is billed, so the budget guard covers all of it.

        A stub that omitted this would not merely fail -- it would make the
        pipeline's own blind-spot check untestable, which is the one thing that
        check exists to prevent going unnoticed."""
        return {"billed_usd": 0.0, "unbilled_roles": [], "unbilled_calls": 0,
                "unbilled_turns": 0, "notional_usd": 0.0, "routes": ["default"]}


GATES = [
    Gate(name="tests", command="exit 1"),
    Gate(name="lint", command="true"),
]


def make_repo(path: Path) -> Path:
    (path / "core").mkdir(parents=True)
    (path / "core" / "timeparse.py").write_text(f"def {SECRET_REPO_TOKEN}(text):\n    return text\n")
    (path / "README.md").write_text("# a small repo\n")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=path, check=True,
    )
    return path


#: The agents that exist to doubt what the builder produced, and which must not
#: share its priors. Given a different vendor prefix here for the same reason a
#: real config gives them a different provider: a run where every agent shares a
#: family raises `check_independence`, correctly, and an end-to-end test whose
#: fixture violates the system's own invariant would carry that finding through
#: every assertion it makes about the packet.
VERIFY_FAMILY = {"oracle", "breaker", "adversary", "reviewer"}


def make_config(tmp_path: Path) -> Config:
    roles = {
        name: RoleConfig(
            name=name,
            model=f"{'stubverify' if name in VERIFY_FAMILY else 'stub'}/{name}")
        for name in ROLES
    }
    # Declared in config exactly like one you would add yourself.
    roles["reviewer"].review = True
    roles["adversary"].review = True
    roles["adversary"].samples = 3
    # Two readings of one prompt, on two model families -- the way the shipped
    # config declares it, so the test cannot pass on a prompt file that is gone.
    roles["adversary"].prompt = "reviewer"
    from factory.config import ReworkConfig

    return Config(
        roles=roles,
        paths=PathsConfig(
            evidence=str(tmp_path / "evidence"), sandboxes=str(tmp_path / "sandboxes"),
        ),
        pipeline=PipelineConfig(max_parallel_workers=2, adversary_samples=3),
        rework=ReworkConfig(
            # One command per blind test file: this fails for the file the stub
            # oracle wrote and passes for anything else, so the criteria that
            # file covers go red and nothing else does.
            oracle_file_command=_fails_only("tests/oracle/test_spin.py"),
            breaker_file_command=_fails_only("tests/oracle/test_breaker_stop.py"),
        ),
    )


def ready_project(config: Config, repo: Path, name: str):
    """A project that has been through gate 0, without running the surveyor."""
    registry = ProjectRegistry(config.evidence_path)
    project = registry.create(repo, name)
    project.state.gates = list(GATES)
    project.state.base_ref = "HEAD"
    project.state.stage = "ready"
    project.state.baseline = GateReport(results=[GateResult(name="tests", passed=True)])
    # Where this project's blind tests go. Every project that has been through
    # gate 0 has this: approval refuses one that cannot carry a blind test, and
    # without it the routing has nowhere to put the oracle's files.
    project.state.blind_placements = [BlindPlacement(
        kind="python", directory="tests/oracle", filename="test_canary.py",
        canary_passes="def test_canary(): assert True",
        canary_fails="def test_canary(): assert False")]
    project.state.placement_probe = [PlacementResult(
        directory="tests/oracle", passing_ran=True, failing_seen=True,
        contained=True, usable=True)]
    # How this project runs ONE test file. The surveyor proposes these and a
    # human approves them at gate 0; the blind suite is attributed with them
    # and the integrator's seam checks are run with them. Trivial here for the
    # same reason the gates above are -- what is under test is the wiring, not
    # a runner. A fixture whose rules were empty would give a seam check no
    # command at all, which is exactly what the code now does rather than
    # inventing one.
    project.state.test_file_commands = [
        TestFileCommand(match="*.py", command="test -f {path}")]
    registry.save(project, note="test fixture")
    return registry.get(project.id)


@pytest.fixture
def factory(tmp_path):
    repo = make_repo(tmp_path / "repo")
    config = make_config(tmp_path)
    project = ready_project(config, repo, "demo")
    events: list[dict] = []
    llm = StubLLM()
    return Factory(config, project, llm=llm, on_progress=events.append), llm, events, repo


def test_a_full_run_reaches_a_packet(factory):
    f, llm, events, repo = factory

    created = f.create_feature("build a widget that spins", "Widget")
    assert created.stage == "intake", (
        "a feature that says it is awaiting answers before anything has been asked "
        "is a lie the console will faithfully repeat"
    )
    assert not f.store_for(created.feature_id).has("interrogation")

    state = asyncio.run(f.run_intake(created.feature_id))
    assert state.stage == "awaiting_answers", "intake stops and waits for a human (INV-8)"
    assert [p.name for p in state.phases] == PHASE_NAMES
    assert llm.calls == ["scout", "interrogator"], "nothing past the gate has run"

    spec = asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    assert f.state_of(f.store_for(state.feature_id)).stage == "awaiting_spec_approval"
    assert [(a.question_id, a.source) for a in spec.resolved_answers] == [("Q-1", "human")]
    assert len(spec.open_questions) == 1, "the unanswered question is carried as accepted risk"
    assert "Q-2" in spec.open_questions[0]

    f.approve_spec(state.feature_id)
    packet = asyncio.run(f.run_build(state.feature_id))

    final = f.state_of(f.store_for(state.feature_id))
    assert final.stage == "awaiting_verdict"
    assert all(p.status == "done" for p in final.phases), \
        {p.name: p.status for p in final.phases}

    # the verify lane ran exactly once, on the spec alone -- the repair rounds
    # do not buy a second oracle, because the contract did not change
    assert llm.calls.count("oracle") == 1
    # Two full panels: one on the first pass, one on the way out. A repair can
    # break something nobody was looking at, and only an agent reading the whole
    # change would see it.
    assert llm.calls.count("adversary") == 6, "the ensemble is sampled, not asked once"

    # writes landed in the SANDBOX, not the project's own repository
    sandbox = f.open_sandbox(final)
    assert (sandbox.path / "widget" / "spin.py").exists()
    assert (sandbox.path / "widget" / "__init__.py").exists()
    assert not (repo / "widget").exists(), "the project repository must never be written to"

    # the blind tests reached disk, or the gates would have run a suite that
    # does not contain them and every criterion would come back green having
    # been checked by nothing -- and they reached it inside the oracle's own
    # directory, not the project's, so that a blind suite which cannot run is
    # never reported as the project's test gate failing
    assert (sandbox.path / "tests" / "oracle" / "test_spin.py").exists()
    assert (sandbox.path / "tests" / "oracle" / "test_log.py").exists()
    assert not (sandbox.path / "tests" / "test_spin.py").exists(), \
        "a blind test landed in the project's own test tree"

    # what the human gets is a branch, visible from the normal checkout
    assert final.sandbox.branch == f"factory/{state.feature_id}"
    assert final.sandbox.commit_sha
    branches = subprocess.run(
        ["git", "branch", "--list"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert final.sandbox.branch in branches
    shown = subprocess.run(
        ["git", "show", f"{final.sandbox.branch}:widget/spin.py"],
        cwd=repo, capture_output=True, text=True,
    ).stdout
    assert SECRET_IMPL_TOKEN in shown

    # traceability is computed: AC-2 was owned by a unit but no decision implements it
    assert {(r.criterion_id, r.status) for r in packet.trace} == {
        ("AC-1", "traced"), ("AC-2", "orphan_requirement"), ("AC-3", "traced"),
    }

    # the rapporteur returned no decisions, no findings and two unclassified files.
    # R1-1 is the repair: a repair is worker output on the same feature, so its
    # decisions are decisions a human rules on like any other. Its unit is
    # named for the round it ran in, so a second round's R-1 is not this one.
    assert {d.id for d in packet.decisions} == {"U-1/D-1", "U-2/D-1", "INT/D-1", "R1-1/D-1"}
    assert {f_.title for f_ in packet.findings} == {
        "duplicated helper", "no stop path",
        # The breaker's finding is a fact about a process, not a claim: this
        # probe was written against the implementation and it failed.
        "stopping twice leaves the widget spinning",
        # The `tests` gate fails here and fails identically at the commit this
        # work branched from, so it is not this feature's doing and the packet
        # says so rather than letting a reviewer assume the worst.
        "Some checks were already failing before this feature — tests",
        # This fixture declares how to run one test file -- every project through
        # gate 0 does, and the integrator's seam checks are run with it -- but
        # the rule emits no JUnit report, so a file's exit code is still the
        # finest verdict available. Read before the build rather than with the
        # packet, and true of this project as written.
        "Some tests can't report which one failed, so a failure can't be traced to a requirement",
    }
    pre = next(f_ for f_ in packet.findings if f_.id == "pre-existing-1")
    assert pre.severity == "minor", "an inherited failure is not this feature's blocker"

    # ---- the repair loop ------------------------------------------------
    # INV-11: a repaired finding is still a finding. The count never goes down,
    # and what became of each one is on the record beside it.
    assert packet.rework.rounds == 1
    assert packet.rework.repaired == 1
    assert packet.rework.stop_reason.startswith("converged")
    outcomes = {r.finding_id: r.outcome for r in packet.records}
    assert outcomes["reviewer-1"] == "repaired"
    repaired = next(r for r in packet.records if r.finding_id == "reviewer-1")
    assert repaired.attempts == 1 and repaired.repaired_in_round == 1 and repaired.commit
    assert len(packet.records) == len(packet.findings), \
        "every finding carries an outcome; none is dropped on the way to the packet"

    # The arbiter routed one finding and ignored the rest. Unrouted defaults to
    # a human ruling, never to dismissal.
    assert outcomes["adversary-1"] == "escalated"
    assert "did not route" in next(
        r for r in packet.records if r.finding_id == "adversary-1").disposition_reason

    # The breaker's probes were run and then removed: the branch a human reads
    # is the feature, not the attack surface someone probed it with.
    assert not (sandbox.path / "tests" / "oracle" / "test_breaker_stop.py").exists()
    assert "test_breaker_stop.py" not in subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", final.sandbox.branch],
        cwd=repo, capture_output=True, text=True).stdout
    unclassified = {m.path: m.klass for m in packet.materiality}
    assert unclassified == {
        "widget/spin.py": "novel", "widget/log.py": "novel", "widget/__init__.py": "novel",
    }
    assert all(m.lines > 0 for m in packet.materiality), "line counts come from the files, not the model"

    # the empty disclosure is surfaced rather than read as a clean run (INV-9)
    assert any("U-2" in line and "disclosed nothing" in line for line in packet.disclosures)
    assert any("AC-2, the stop path" in line for line in packet.disclosures)

    assert packet.stats.orphan_requirements == 1
    assert packet.stats.blockers == 1
    assert packet.stats.files_written == 3

    assert events, "phase transitions were broadcast for the UI"
    assert {e["phase"] for e in events if e["phase"]} >= set(PHASE_NAMES)


def test_a_failing_phase_records_itself_and_stops(factory, monkeypatch):
    f, llm, events, repo = factory

    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)

    original = llm.ask

    async def explode(role, prompt, schema=None, **kwargs):
        if role == "architect":
            raise RuntimeError("the architect fell over")
        return await original(role, prompt, schema, **kwargs)

    monkeypatch.setattr(llm, "ask", explode)

    with pytest.raises(RuntimeError):
        asyncio.run(f.run_build(state.feature_id))

    final = f.state_of(f.store_for(state.feature_id))
    assert final.stage == "failed"
    assert "the architect fell over" in final.error
    architect = next(p for p in final.phases if p.name == "architect")
    assert architect.status == "failed"
    assert "the architect fell over" in architect.error

    # everything recorded before the failure is still readable -- the store is append-only
    store = f.store_for(state.feature_id)
    assert store.has("scout") and store.has("interrogation") and store.has("spec")


def test_two_features_in_one_project_cannot_touch_each_other(tmp_path):
    """The property the whole sandbox layer exists for."""
    repo = make_repo(tmp_path / "repo")
    config = make_config(tmp_path)
    project = ready_project(config, repo, "demo")

    a = Factory(config, project, llm=StubLLM())
    b = Factory(config, project, llm=StubLLM())

    state_a = asyncio.run(a.run_intake("feature a", "Alpha"))
    state_b = asyncio.run(b.run_intake("feature b", "Beta"))

    box_a, box_b = a.open_sandbox(state_a), b.open_sandbox(state_b)
    assert box_a.path != box_b.path
    assert box_a.branch != box_b.branch
    assert box_a.state.base_sha == box_b.state.base_sha, "both branch from the same base"

    box_a.write("core/timeparse.py", "A OWNS THIS\n")
    box_a.write("only_in_a.py", "A = 1\n")
    box_b.write("core/timeparse.py", "B OWNS THIS\n")

    assert (box_a.path / "core" / "timeparse.py").read_text() == "A OWNS THIS\n"
    assert (box_b.path / "core" / "timeparse.py").read_text() == "B OWNS THIS\n"
    assert not (box_b.path / "only_in_a.py").exists()
    assert SECRET_REPO_TOKEN in (repo / "core" / "timeparse.py").read_text(), \
        "the human's own checkout is untouched by either feature"


def test_two_projects_keep_separate_ledgers_and_sandboxes(tmp_path):
    config = make_config(tmp_path)
    repo_one = make_repo(tmp_path / "one")
    repo_two = make_repo(tmp_path / "two")
    project_one = ready_project(config, repo_one, "one")
    project_two = ready_project(config, repo_two, "two")

    f1 = Factory(config, project_one, llm=StubLLM())
    f2 = Factory(config, project_two, llm=StubLLM())
    s1 = asyncio.run(f1.run_intake("something", "Thing"))
    s2 = asyncio.run(f2.run_intake("something else", "Other"))

    assert s1.project_id == "one" and s2.project_id == "two"
    assert [x["feature_id"] for x in f1.features()] == [s1.feature_id]
    assert [x["feature_id"] for x in f2.features()] == [s2.feature_id]

    box_one = f1.open_sandbox(s1)
    box_two = f2.open_sandbox(s2)
    assert box_one.repo == repo_one.resolve()
    assert box_two.repo == repo_two.resolve()
    assert "one" in str(box_one.path) and "two" in str(box_two.path)


def test_a_feature_cannot_start_in_a_project_that_is_not_ready(tmp_path):
    """No baseline means no way to attribute a later failure to the feature."""
    repo = make_repo(tmp_path / "repo")
    config = make_config(tmp_path)
    registry = ProjectRegistry(config.evidence_path)
    project = registry.create(repo, "unapproved")  # stage: surveying

    f = Factory(config, project, llm=StubLLM())
    with pytest.raises(ProjectError) as caught:
        asyncio.run(f.run_intake("build something", "Thing"))
    assert "not ready" in str(caught.value)

    with pytest.raises(ProjectError):
        registry.approve(project)  # no baseline recorded


def test_accepting_a_feature_drops_the_checkout_and_keeps_the_branch(factory):
    f, llm, events, repo = factory
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    asyncio.run(f.run_build(state.feature_id))

    before = f.open_sandbox(f.state_of(f.store_for(state.feature_id)))
    assert before.path.exists()

    final = f.record_verdict(state.feature_id, "accepted")
    assert final.stage == "accepted"
    assert not before.path.exists(), "the checkout is disposable"
    branches = subprocess.run(
        ["git", "branch", "--list"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert f"factory/{state.feature_id}" in branches, "the branch is the deliverable"


def test_an_added_review_agent_contributes_findings_to_the_packet(tmp_path):
    """The one place the pipeline is pluggable: an extra agent over the same
    evidence, whose findings cannot be dropped by the rapporteur."""
    import shutil

    repo = make_repo(tmp_path / "repo")
    config = make_config(tmp_path)

    # INV-10: a panel agent with no prompt file fails loudly rather than running
    # on nothing. The console writes a starter one; here the test does.
    roles_dir = tmp_path / "roles"
    shutil.copytree(Path(__file__).resolve().parent.parent / "factory" / "roles", roles_dir)
    (roles_dir / f"{PANEL_ROLE}.md").write_text("# security\n\nOne angle on the same evidence.\n")
    config.paths.roles = str(roles_dir)

    config.roles[PANEL_ROLE] = RoleConfig(
        name=PANEL_ROLE, model="stub/security", review=True, samples=2,
    )
    project = ready_project(config, repo, "demo")

    llm = StubLLM()
    f = Factory(config, project, llm=llm)
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    packet = asyncio.run(f.run_build(state.feature_id))

    # Two samples, on each of the two full panels -- the first pass and the one
    # the loop exits through.
    assert llm.calls.count(PANEL_ROLE) == 4, "sampled per its own `samples` setting"

    titles = {x.title for x in packet.findings}
    assert "widened permission" in titles, "a panel finding reached the packet"
    panel_findings = [x for x in packet.findings if x.id.startswith(PANEL_ROLE)]
    assert len(panel_findings) == 1, "two samples of the same objection merge into one finding"
    assert panel_findings[0].category == PANEL_ROLE, \
        "falls back to the agent's name when the agent set no category of its own"

    final = f.state_of(f.store_for(state.feature_id))
    assert next(p for p in final.phases if p.name == "review").status == "done"


def test_no_review_agents_configured_is_not_a_failure(tmp_path):
    """A factory with nothing in the review phase still produces a packet -- with
    no findings, which is visible rather than silent."""
    repo = make_repo(tmp_path / "repo")
    config = make_config(tmp_path)
    for role in config.roles.values():
        role.review = False
    project = ready_project(config, repo, "demo")
    f = Factory(config, project, llm=StubLLM())

    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    packet = asyncio.run(f.run_build(state.feature_id))

    phase = next(p for p in f.state_of(f.store_for(state.feature_id)).phases if p.name == "review")
    assert phase.status == "done"
    assert phase.detail == "no review agents configured"
    # The breaker is not a review agent -- it has tools and it executes -- so
    # deleting every review agent does not delete the adversarial probes.
    # `attribution-2` is this fixture's per-file rule emitting no JUnit report,
    # read before the build. It is not a review finding and does not depend on
    # there being a panel, which is what makes it worth listing here. The
    # breaker's finding is named for its probe file.
    assert sorted(f_.id for f_ in packet.findings) == [
        "attribution-2", "breaker-breaker-stop", "pre-existing-1"]
    assert packet.decisions, "the rest of the packet is unaffected"


def test_a_feature_mid_intake_is_not_reported_as_awaiting_you(factory):
    """The bug this stage exists to prevent: the rail listed a half-built feature
    as awaiting answers, and clicking it claimed the interrogator found nothing."""
    f, llm, events, repo = factory

    created = f.create_feature("build a widget that spins", "Widget")
    listed = next(x for x in f.features() if x["feature_id"] == created.feature_id)
    assert listed["stage"] == "intake"
    assert llm.calls == [], "no agent has run yet"

    asyncio.run(f.run_intake(created.feature_id))
    listed = next(x for x in f.features() if x["feature_id"] == created.feature_id)
    assert listed["stage"] == "awaiting_answers"
    assert f.store_for(created.feature_id).has("interrogation")


def test_a_failed_intake_is_recorded_rather_than_left_running(factory, monkeypatch):
    f, llm, events, repo = factory
    created = f.create_feature("build a widget that spins", "Widget")

    original = llm.ask

    async def explode(role, prompt, schema=None, **kwargs):
        if role == "interrogator":
            raise RuntimeError("the interrogator fell over")
        return await original(role, prompt, schema, **kwargs)

    monkeypatch.setattr(llm, "ask", explode)
    with pytest.raises(RuntimeError):
        asyncio.run(f.run_intake(created.feature_id))

    final = f.state_of(f.store_for(created.feature_id))
    assert final.stage == "failed"
    assert "the interrogator fell over" in final.error
    assert next(p for p in final.phases if p.name == "interrogator").status == "failed"


def test_a_correction_replaces_the_reading_and_reaches_the_spec_writer(factory):
    """The restatement is the one place a human catches a misunderstanding before
    spending their attention on questions built from it. Reading it has to have
    an action attached, and the correction has to survive into the spec."""
    f, llm, events, repo = factory
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    store = f.store_for(state.feature_id)

    first = store.payload("interrogation")
    assert first["restated_intent"] == "build a widget"
    assert len(store.all("interrogation")) == 1

    seen = {}
    original = llm.ask

    async def capture(role, prompt, schema=None, **kwargs):
        seen[role] = prompt
        return await original(role, prompt, schema, **kwargs)

    llm.ask = capture
    asyncio.run(f.reinterrogate(state.feature_id, "It is not a widget. It is a turnstile."))

    # the correction reached the interrogator, framed as overriding
    assert "turnstile" in seen["interrogator"]
    assert "corrected your understanding" in seen["interrogator"]

    # append-only: the superseded reading is still there beside the new one
    assert len(store.all("interrogation")) == 2
    assert len(store.all("correction")) == 1
    assert store.payload("correction")["restated_intent"] == "build a widget"

    reopened = f.state_of(store)
    assert reopened.stage == "awaiting_answers"

    # and it survives into the spec, or the spec writer quietly reverts to the
    # reading the human rejected
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    assert "turnstile" in seen["spec_writer"]
    assert "Corrections the human made" in seen["spec_writer"]


def test_a_correction_is_recorded_before_the_agent_is_asked(factory, monkeypatch):
    """If the re-ask fails, the correction must not be lost with it."""
    f, llm, events, repo = factory
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))

    original = llm.ask

    async def explode(role, prompt, schema=None, **kwargs):
        if role == "interrogator":
            raise RuntimeError("second pass fell over")
        return await original(role, prompt, schema, **kwargs)

    monkeypatch.setattr(llm, "ask", explode)
    with pytest.raises(RuntimeError):
        asyncio.run(f.reinterrogate(state.feature_id, "It is a turnstile."))

    store = f.store_for(state.feature_id)
    assert store.payload("correction")["correction"] == "It is a turnstile."
    assert f.state_of(store).stage == "failed"


def test_rerunning_intake_replaces_the_scout_report(factory):
    """`reinterrogate` reuses the stored scout report; this is for when what the
    scout saw was wrong, not when the interrogator misread a correct report."""
    f, llm, events, repo = factory
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    store = f.store_for(state.feature_id)

    assert len(store.all("scout")) == 1
    calls_before = list(llm.calls)

    asyncio.run(f.rerun_intake(state.feature_id))

    # both halves ran again
    assert llm.calls.count("scout") == calls_before.count("scout") + 1
    assert llm.calls.count("interrogator") == calls_before.count("interrogator") + 1

    # append-only: the superseded report is still there
    assert len(store.all("scout")) == 2
    assert len(store.all("interrogation")) == 2
    assert store.payload("reintake")["superseded_scout"] is True

    final = f.state_of(store)
    assert final.stage == "awaiting_answers"
    assert all(p.status == "done" for p in final.phases if p.name in ("scout", "interrogator"))


def test_intake_cannot_be_rerun_once_gate_one_has_closed(factory):
    """A fresh spec after the build started would not describe what was built."""
    from factory.projects import ProjectError

    f, llm, events, repo = factory
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)

    with pytest.raises(ProjectError) as caught:
        asyncio.run(f.rerun_intake(state.feature_id))
    assert "Gate 1 has closed" in str(caught.value)


def test_the_plan_costs_nothing_to_ask_for(factory):
    """The button says "nine scout calls" before you press it, not after."""
    f, llm, events, repo = factory
    created = f.create_feature("build a widget that spins", "Widget")

    before = list(llm.calls)
    plan = f.intake_plan(created.feature_id)

    assert llm.calls == before, "working out the cost must not spend anything"
    assert plan["calls"] == plan["slices"] + 1, "the scouts, plus one interrogator"
    assert plan["files"] >= 1 and plan["covered"] > 0
    assert "symbols" in plan


def test_a_dirty_working_tree_is_refused_before_anything_is_spent(factory):
    """The failure this exists to prevent: a sandbox is branched from HEAD, so
    uncommitted work is invisible to every agent -- and they report on the
    committed code truthfully, which is what makes it expensive to notice."""
    from factory.projects import ProjectError

    f, llm, events, repo = factory
    (repo / "backend").mkdir(exist_ok=True)
    (repo / "backend" / "sites.py").write_text("class TrialSite: ...\n")
    (repo / "README.md").write_text("# changed\n")

    with pytest.raises(ProjectError) as caught:
        f.create_feature("track screening slots per site", "Slots")

    message = str(caught.value)
    assert "uncommitted change" in message
    assert "branch from" in message.lower() or "branch" in message
    assert llm.calls == [], "refused before a token was spent"
    assert not f.features(), "and before a sandbox was created"


def test_the_override_is_explicit_and_recorded(factory):
    f, llm, events, repo = factory
    (repo / "scratch.md").write_text("notes\n")

    state = f.create_feature("build a widget", "Widget", allow_dirty=True)
    assert state.stage == "intake"

    drift = f.store_for(state.feature_id).payload("drift")
    assert drift["files"] >= 1
    assert drift["untracked"] >= 1
    assert any(x["path"] == "scratch.md" for x in drift["sample"]), \
        "what was skipped is named, so a packet can be read against it later"


def test_a_clean_tree_is_not_obstructed(factory):
    f, llm, events, repo = factory
    drift = f.working_tree_drift()
    assert drift["checked"] and drift["files"] == 0
    state = f.create_feature("build a widget", "Widget")
    assert state.stage == "intake"


def test_discarding_a_feature_keeps_the_branch(factory):
    """The branch is the only thing here that might be work, and keeping it is
    free."""
    import subprocess

    f, llm, events, repo = factory
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    asyncio.run(f.run_build(state.feature_id))

    store = f.store_for(state.feature_id)
    evidence_dir = store.dir
    sandbox = f.open_sandbox(f.state_of(store))
    assert evidence_dir.exists() and sandbox.path.exists()

    removed = f.delete_feature(state.feature_id)

    assert not evidence_dir.exists(), "the ledger is gone"
    assert not sandbox.path.exists(), "so is the checkout"
    assert removed["branch_deleted"] is False
    branches = subprocess.run(
        ["git", "branch", "--list"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert f"factory/{state.feature_id}" in branches, "the branch survives by default"
    assert state.feature_id not in [x["feature_id"] for x in f.features()]


def test_discarding_can_take_the_branch_too_when_asked(factory):
    import subprocess

    f, llm, events, repo = factory
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    f.delete_feature(state.feature_id, delete_branch=True)

    branches = subprocess.run(
        ["git", "branch", "--list"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert f"factory/{state.feature_id}" not in branches


def test_a_running_feature_cannot_be_discarded(factory):
    """Agents are still writing to it; removing the directory underneath them
    turns a clean failure into a confusing one."""
    from factory.projects import ProjectError

    f, llm, events, repo = factory
    created = f.create_feature("build a widget that spins", "Widget")
    assert created.stage == "intake"

    with pytest.raises(ProjectError) as caught:
        f.delete_feature(created.feature_id)
    assert "still working on it" in str(caught.value)
    assert str(os.getpid()) in str(caught.value), \
        "the refusal must name what is holding it, so it can be checked"
    assert f.store_for(created.feature_id).has("state"), "nothing was removed"


def test_a_feature_whose_run_died_can_be_discarded(factory):
    """The state a crash leaves behind.

    `stage` is a claim, and this is the one case where it is reliably wrong: a
    server killed mid-build leaves "building" with nobody building. A guard that
    reads the field refuses forever -- "wait for it to finish" is advice that
    cannot be satisfied, and the only way out was editing the evidence store by
    hand. So the guard asks the operating system instead.
    """
    from factory.pipeline import is_orphaned

    f, llm, events, repo = factory
    created = f.create_feature("build a widget that spins", "Widget")
    store = f.store_for(created.feature_id)

    state = f.state_of(store)
    state.owner = "999999:a process that is long gone"
    f._save(store, state)

    assert is_orphaned(f.state_of(store))
    removed = f.delete_feature(created.feature_id)
    assert removed["feature_id"] == created.feature_id
    assert not f.store_for(created.feature_id).has("state")


def test_a_feature_recorded_before_ownership_existed_is_not_locked_forever(factory):
    """Every feature already on disk has no owner, and one of them was stuck
    mid-build when this was written. An empty owner has to read as "nobody",
    or the fix would not reach the feature it was written for."""
    from factory.pipeline import is_orphaned, owner_is_alive

    f, llm, events, repo = factory
    created = f.create_feature("build a widget that spins", "Widget")
    store = f.store_for(created.feature_id)
    state = f.state_of(store)
    state.owner = ""
    f._save(store, state)

    assert owner_is_alive("") is False
    assert is_orphaned(f.state_of(store))
    f.delete_feature(created.feature_id)   # must not raise


def test_the_store_still_exposes_no_way_to_erase_one_record(tmp_path):
    """Discarding a whole abandoned feature is a human's call. Editing what an
    agent recorded inside a live one is what INV-2 forbids, and still does."""
    from factory.store import EvidenceStore

    public = [n for n in dir(EvidenceStore) if not n.startswith("_")]
    for banned in ("update", "delete", "remove", "set"):
        assert not any(banned in n.lower() for n in public), f"EvidenceStore gained {banned!r}"


def test_commits_on_another_branch_count_as_drift(factory):
    """`git status` reports a clean tree while every commit of your actual work
    sits on a branch the project does not build from. Same invisibility, and the
    obvious check misses it entirely."""
    import subprocess
    from factory.projects import ProjectError

    f, llm, events, repo = factory

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    # A project pinned to a named branch. With `base_ref: HEAD` there is nothing
    # to diverge from -- features branch from wherever you are -- so only
    # uncommitted files can drift.
    default = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo,
                             capture_output=True, text=True).stdout.strip()
    f.project.state.base_ref = default

    git("checkout", "-q", "-b", "feature-work")
    (repo / "core" / "sites.py").write_text("class TrialSite: ...\n")
    git("add", "-A")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "the real work")

    # a clean tree, and yet
    drift = f.working_tree_drift()
    assert drift["files"] == 0, "nothing uncommitted"
    assert drift["ahead"] >= 1, "but a commit the base ref has never seen"
    assert drift["drifted"] is True

    with pytest.raises(ProjectError) as caught:
        f.create_feature("track screening slots", "Slots")
    message = str(caught.value)
    assert "feature-work" in message
    assert "base ref" in message, "and it names the fix"


def test_answers_are_recorded_before_the_spec_writer_runs(factory):
    """They used to survive only as a field on the spec, so until the spec writer
    returned there was no trace anywhere of what the human had typed -- and a
    spec writer that took two minutes and was then navigated away from took nine
    answers with it."""
    f, llm, events, repo = factory
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    store = f.store_for(state.feature_id)

    f.record_answers(state.feature_id, {"Q-1": "clockwise"}, deferred=["Q-2"])

    assert not store.has("spec"), "nothing has been planned yet"
    recorded = store.payload("answers")["resolved"]
    assert {a["question_id"]: a["source"] for a in recorded} == {"Q-1": "human", "Q-2": "deferred"}
    assert f.state_of(store).stage == "writing_spec"
    assert llm.calls == ["scout", "interrogator"], "no model call was needed to save them"

    spec = asyncio.run(f.finalize_spec(state.feature_id))
    assert [(a.question_id, a.source) for a in spec.resolved_answers] == [
        ("Q-1", "human"), ("Q-2", "deferred")
    ]
    assert f.state_of(store).stage == "awaiting_spec_approval"


def test_a_failed_spec_writer_does_not_take_the_answers_with_it(factory, monkeypatch):
    f, llm, events, repo = factory
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    f.record_answers(state.feature_id, {"Q-1": "clockwise"})

    original = llm.ask

    async def explode(role, prompt, schema=None, **kwargs):
        if role == "spec_writer":
            raise RuntimeError("the spec writer fell over")
        return await original(role, prompt, schema, **kwargs)

    monkeypatch.setattr(llm, "ask", explode)
    with pytest.raises(RuntimeError):
        asyncio.run(f.finalize_spec(state.feature_id))

    store = f.store_for(state.feature_id)
    assert f.state_of(store).stage == "failed"
    assert store.payload("answers")["resolved"][0]["answer"] == "clockwise", \
        "what the human typed survives the failure"


# --------------------------------------------------------------------------
# a repair that makes things worse is undone, not argued about
# --------------------------------------------------------------------------


class BreakingStubLLM(StubLLM):
    """Same run, except the repairer writes a file that fails a gate."""

    async def ask(self, role, prompt, schema=None, *, system="", temperature=None):
        if role == "repairer":
            self.calls.append(role)
            return WorkerOutput(
                unit_id="R-1", summary="fixed it, allegedly",
                files=[FileWrite(path="BROKEN", contents="this file fails the tests gate\n")],
                decisions=[Decision(id="D-1", title="wrote a file", rationale="r")],
                disclosure=SelfDisclosure(),
            )
        return await super().ask(role, prompt, schema, system=system, temperature=temperature)


def test_a_repair_round_that_makes_the_gates_worse_is_reverted(tmp_path):
    repo = make_repo(tmp_path / "repo")
    config = make_config(tmp_path)
    # Pinned, because the subject here is that a revert does not end the loop,
    # and proving that needs a round left over after the two that get reverted.
    # At the shipped cap of 2 the loop would stop having reverted every round it
    # had, which is a true thing to report and a different thing to test.
    config.rework.max_rounds = 3
    registry = ProjectRegistry(config.evidence_path)
    project = registry.create(repo, "demo")
    # Green until the repairer writes BROKEN, which is exactly what it does.
    project.state.gates = [
        Gate(name="tests", command="test ! -f BROKEN && echo '3 passed'"),
        Gate(name="lint", command="true"),
    ]
    project.state.base_ref = "HEAD"
    project.state.stage = "ready"
    project.state.baseline = GateReport(results=[GateResult(name="tests", passed=True)])
    registry.save(project, note="test fixture")

    llm = BreakingStubLLM()
    f = Factory(config, registry.get(project.id), llm=llm)
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    packet = asyncio.run(f.run_build(state.feature_id))

    # This stub breaks the gates on every round, so every round it is given is
    # undone. That is the point: the loop keeps trying and keeps refusing to
    # keep work that made things worse, and the tree below is clean either way.
    #
    # It used to stop dead on the first revert -- a real run reverted round 1 and
    # finished there with four of five rounds and most of its budget unspent,
    # reporting nothing repaired. A round that did not work is not a reason to
    # stop trying; the attempt counter is what bounds the retrying.
    assert packet.rework.reverted_rounds == [1, 2], packet.rework.reverted_rounds
    assert packet.rework.rounds > 1, \
        "the loop stopped at the first reverted round instead of trying again"
    assert "reverted" not in packet.rework.stop_reason, \
        "a revert is being reported as the reason the loop ended"
    reverted = [r for r in f.store_for(state.feature_id).records()
                if r["kind"] == "rework_reverted"]
    assert reverted and "gates fell" in reverted[0]["payload"]["reason"], \
        "the reason the round was undone is not on the record"

    final = f.state_of(f.store_for(state.feature_id))
    sandbox = f.open_sandbox(final)
    assert not (sandbox.path / "BROKEN").exists(), \
        "the branch a human reads must not carry a change that made the work worse"
    assert not (repo / "BROKEN").exists()

    # The attempt is not lost: the evidence store is append-only, so what was
    # tried and why it was undone are both still on the record.
    kinds = [r["kind"] for r in f.store_for(state.feature_id).records()]
    assert "repair" in kinds and "rework_reverted" in kinds

    # And the finding it was trying to fix is still open in front of a human.
    outcome = {r.finding_id: r.outcome for r in packet.records}["reviewer-1"]
    assert outcome in ("attempted_not_fixed", "open", "escalated")


# --------------------------------------------------------------------------
# attribution: a red gate on its own says nothing
# --------------------------------------------------------------------------


def test_a_gate_this_feature_broke_is_told_apart_from_one_it_inherited(tmp_path):
    """The whole point of the comparison. Two gates fail in the same run: one
    was failing before the feature existed, the other the feature caused. A
    packet that reported both the same way would be unreadable on any repository
    with pre-existing failures -- which is most of them."""
    repo = make_repo(tmp_path / "repo")
    config = make_config(tmp_path)
    registry = ProjectRegistry(config.evidence_path)
    project = registry.create(repo, "demo")
    project.state.gates = [
        # Red before and after: nothing the feature did touches it.
        Gate(name="inherited", command="echo 'was already broken' && exit 1"),
        # Green at the base commit, because the file does not exist there yet.
        Gate(name="caused", command="test ! -f widget/spin.py"),
        Gate(name="lint", command="true"),
    ]
    project.state.base_ref = "HEAD"
    project.state.stage = "ready"
    project.state.baseline = GateReport(results=[GateResult(name="lint", passed=True)])
    registry.save(project, note="test fixture")

    f = Factory(config, registry.get(project.id), llm=StubLLM())
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    packet = asyncio.run(f.run_build(state.feature_id))

    titles = {f_.id: f_ for f_ in packet.findings}
    assert "broke-caused" in titles, "a gate that was green at the base commit and is red now"
    assert titles["broke-caused"].severity == "blocker"
    assert "passes at" in titles["broke-caused"].detail

    assert "pre-existing-1" in titles
    assert "inherited" in titles["pre-existing-1"].title
    assert titles["pre-existing-1"].severity == "minor", \
        "inheriting a failure is not the same as causing one"
    assert "broke-inherited" not in titles, "the feature must not be blamed for what it inherited"


def test_the_base_comparison_is_cached_on_the_project(tmp_path):
    """Three features branching from one commit ask the same question. The
    answer belongs to the commit, so the second and third should not pay for
    it -- and it lives on the project's ledger, not the feature's."""
    repo = make_repo(tmp_path / "repo")
    config = make_config(tmp_path)
    registry = ProjectRegistry(config.evidence_path)
    project = registry.create(repo, "demo")
    project.state.gates = [Gate(name="inherited", command="echo nope && exit 1")]
    project.state.base_ref = "HEAD"
    project.state.stage = "ready"
    project.state.baseline = GateReport(results=[GateResult(name="inherited", passed=True)])
    registry.save(project, note="test fixture")

    for _ in range(2):
        f = Factory(config, registry.get(project.id), llm=StubLLM())
        state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
        asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
        f.approve_spec(state.feature_id)
        asyncio.run(f.run_build(state.feature_id))

    ledger = registry.get(project.id).store
    measured = [r for r in ledger
                if r["kind"] == "attribution" and (r["payload"] or {}).get("gate")]
    assert len(measured) == 1, \
        f"the base commit should be measured once, not once per feature: {len(measured)} runs"
    assert measured[0]["payload"]["at_base"] == "failed"


def test_a_feature_runs_the_environment_setup_before_its_gates(tmp_path):
    """The baseline always ran these; a feature never did.

    An image installs dependencies at build time and the worktree is then
    mounted over the workdir, so anything the image put inside it -- a
    `node_modules`, an editable install -- is shadowed by the time a gate looks
    for it. The baseline was green because onboarding ran setup. Every feature
    would have been red for a reason that had nothing to do with the feature.
    """
    repo = make_repo(tmp_path / "repo")
    config = make_config(tmp_path)
    # This test is about setup ordering, and it reads that off "nothing was
    # verified". So the blind suite has to genuinely fail here, as the comment
    # below says it does -- the shared fixture's per-file command passes for
    # every file but one. Failing every file is what makes this fixture's blind
    # suite red for its own reasons.
    config.rework.oracle_file_command = "exit 1"
    registry = ProjectRegistry(config.evidence_path)
    project = registry.create(repo, "demo")
    project.state.environment = EnvironmentSpec(
        kind="host", rationale="test fixture",
        setup=["echo installed > DEPENDENCY"],
    )
    # Green only if setup ran in this checkout.
    project.state.gates = [Gate(name="needs-setup", command="test -f DEPENDENCY")]
    project.state.base_ref = "HEAD"
    project.state.stage = "ready"
    project.state.baseline = GateReport(results=[GateResult(name="needs-setup", passed=True)])
    # This project can carry blind tests; what it cannot do is run them, which
    # is the situation the assertions below are about.
    project.state.blind_placements = [BlindPlacement(
        directory="tests/oracle", filename="test_canary.py",
        canary_passes="def test_canary(): assert True",
        canary_fails="def test_canary(): assert False")]
    registry.save(project, note="test fixture")

    f = Factory(config, registry.get(project.id), llm=StubLLM())
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    packet = asyncio.run(f.run_build(state.feature_id))

    # The project's own gate is the one this test is about. `blind-tests` is
    # also in the report now and is red on purpose: this fixture's oracle
    # declares no command to run its suite, so nothing verified any criterion,
    # and the run says so rather than inferring verification from a green
    # project gate.
    last_gates = [r for r in f.store_for(state.feature_id).records()
                  if r["kind"] == "gates"][-1]["payload"]["results"]
    project_gates = [g for g in last_gates if g["name"] != "blind-tests"]
    assert project_gates and all(g["passed"] for g in project_gates), \
        "the gate needed what setup installs, and setup ran"
    blind = [g for g in last_gates if g["name"] == "blind-tests"]
    # Asserted on the outcome, not the reason. The blind suite has a configured
    # command now, and this fixture's environment cannot run it -- which is the
    # same situation as having no command at all, and must read the same way.
    assert blind and not blind[0]["passed"], \
        "a run with no way to execute its blind tests must not look verified"
    assert packet.stats.criteria_verified == 0

    final = f.state_of(f.store_for(state.feature_id))
    assert final.sandbox.setup_at, "the sandbox records that setup happened"

    # Once, because this project's gates run on the host, and what setup put
    # there is still there next round. That is a property of the runner, not of
    # the checkout -- see the container case below.
    records = [r for r in f.store_for(state.feature_id).records() if r["kind"] == "setup"]
    assert len(records) == 1, f"setup ran {len(records)} times; the host runner carries it"


def test_a_runner_that_cannot_carry_setup_gets_it_again_every_round(tmp_path, monkeypatch):
    """The same question, asked of a container.

    `pip install` writes into the container's filesystem; the toolchain then
    lives in an image the runner commits and `down()` removes, and a database
    setup migrated lives in a volume `down -v` takes with it. Skipping setup on
    round 1 because round 0 ran it left one real run with three repair rounds
    of `ruff: not found`, `mypy: not found` and `No module named pytest`, and a
    packet reporting zero of sixteen criteria verified -- which described this
    harness rather than that code.
    """
    from factory import gates as gates_module

    class Fresh(gates_module.LocalRunner):
        """A container runner in the one respect that matters here."""

        carries_setup = False

    async def fresh_runner_for(project, config, *, feature_id=""):
        return Fresh(), "fake-image"

    # Patched where `_assess` and `_attribute_failures` look it up.
    monkeypatch.setattr("factory.pipeline.factory.assess.runner_for", fresh_runner_for)

    repo = make_repo(tmp_path / "repo")
    config = make_config(tmp_path)
    registry = ProjectRegistry(config.evidence_path)
    project = registry.create(repo, "demo")
    project.state.environment = EnvironmentSpec(
        kind="host", rationale="test fixture", setup=["echo installed > DEPENDENCY"],
    )
    project.state.gates = [Gate(name="needs-setup", command="test -f DEPENDENCY")]
    project.state.base_ref = "HEAD"
    project.state.stage = "ready"
    project.state.baseline = GateReport(results=[GateResult(name="needs-setup", passed=True)])
    registry.save(project, note="test fixture")

    f = Factory(config, registry.get(project.id), llm=StubLLM())
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    asyncio.run(f.run_build(state.feature_id))

    records = f.store_for(state.feature_id).records()
    setups = [r for r in records if r["kind"] == "setup"]
    rounds = {(r.get("meta") or {}).get("round") for r in records if r["kind"] == "gates"}
    assert len(rounds) > 1, "this fixture is only worth anything if the repair loop ran"
    assert len(setups) == len(rounds), (
        f"the gates ran in {len(rounds)} round(s) and setup ran {len(setups)} time(s); "
        "every round after the first was measuring an environment nobody assembled"
    )
    assert {(r.get("meta") or {}).get("round") for r in setups} == rounds, \
        "a setup record does not say which round's environment it describes"


def test_an_arbiter_that_skips_findings_is_asked_again_with_their_ids(factory):
    """Silence is not a ruling, and the retry has to say which silences.

    The arbiter here answers about one finding and never mentions the rest --
    the shape that let a placeholder reply pass for a triage. `escalate` is a
    ruling and is the right one wherever the call is a human's; leaving a
    finding out is not, because what fills the gap afterwards is indis-
    tinguishable from a decision somebody made.
    """
    f, llm, events, repo = factory
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    asyncio.run(f.run_build(state.feature_id))

    assert len(llm.arbiter_prompts) > 1, (
        "an arbiter that ruled on one finding out of several was taken at its word"
    )
    again = llm.arbiter_prompts[1]
    assert "without a disposition" in again
    assert "Rule on these, by id:" in again, "asked again, but not told what it missed"
    named = {line[2:] for line in again.splitlines() if line.startswith("- ")}
    assert named, "the retry names no finding at all"
    assert "reviewer-1" not in named, (
        "the one finding it did rule on is being asked about again"
    )
    store = f.store_for(state.feature_id)
    kinds = [r["kind"] for r in store.records()]
    assert "arbiter_retry" in kinds, "the retry happened and left no record that it did"
    skipped = [r for r in store.records() if r["kind"] == "arbiter_retry"][0]
    assert skipped["payload"]["unruled"], "the record does not say which findings were skipped"
    # And the first answer survives the retries: rulings that exist are not
    # thrown away because others are missing.
    assert "repair_plan" in kinds, \
        "the finding the arbiter did route never reached the repair loop"


def test_the_recheck_agent_is_shown_the_file_and_not_only_the_repair_summary(factory):
    """What settles "is it still there" is the code, not a repairer's account.

    Given the round's diff and a summary, an agent asked whether a defect
    survived is reasoning about a claim. When the harness behind a repairer
    fails and it honestly reports changing nothing, the claim points the wrong
    way: a defect fixed in an earlier round reads as a defect still standing.
    Three agents did exactly that for two rounds on one real feature, and the
    packet led with a class that had been deleted.
    """
    f, llm, events, repo = factory
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    asyncio.run(f.run_build(state.feature_id))

    assert llm.recheck_prompts, "the repair loop never re-checked anything"
    prompt = llm.recheck_prompts[0]
    assert "as they are on disk now" in prompt, "the re-check was given no working tree"
    assert "# deduplicated" in prompt, (
        "the re-check cannot see the repaired file itself; it is judging the summary of it"
    )
    assert "Files changed in this round only" in prompt, (
        "a per-round list labelled as the whole change is how a file repaired in an "
        "earlier round reads as a file nobody touched"
    )


def test_a_repair_round_that_edited_nothing_is_not_charged_to_the_findings(factory):
    """A harness that cannot edit must cost one round, not the whole loop.

    In the run this comes from, the harness produced nothing for three rounds.
    Every finding it was pointed at was charged an attempt anyway, and the
    packet then described blockers nobody had touched as tried and unfixable
    -- while the only trace of the real failure was a sentence in one
    repairer's own summary.
    """
    f, llm, events, repo = factory
    real_run = f.executor.run

    async def silent_repairer(unit, spec, digest, system, workdir,
                              role="worker", on_prompt=None, environment=None,
                              on_environment=None, send_back=None, on_guides=None):
        if role == "repairer":
            return WorkerOutput(
                unit_id=unit.id, summary="the coding harness edited no file",
                files=[], decisions=[], disclosure=SelfDisclosure(),
            )
        return await real_run(unit, spec, digest, system, workdir,
                              role=role, on_prompt=on_prompt,
                              environment=environment, on_environment=on_environment,
                              send_back=send_back)

    f.executor.run = silent_repairer

    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    packet = asyncio.run(f.run_build(state.feature_id))

    records = f.store_for(state.feature_id).records()
    noop = [r for r in records if r["kind"] == "repair_noop"]
    assert noop, "a repair round that changed nothing left no record that it changed nothing"
    assert "reviewer-1" in noop[0]["payload"]["findings"]

    charged = {r.finding_id: r for r in packet.records}["reviewer-1"]
    assert charged.attempts == 0, (
        f"the finding was charged {charged.attempts} attempt(s) for rounds that did no work"
    )
    assert charged.outcome != "attempted_not_fixed", (
        "a finding nobody repaired is reported as one somebody tried and could not fix"
    )
    assert any(f_.id.startswith("harness-") for f_ in packet.findings), (
        "the harness failed on every round and the human's packet does not mention it"
    )


def test_the_repair_loop_can_fail_without_taking_the_packet_with_it(factory, monkeypatch):
    """Everything the loop improves was bought before it ran.

    The branch, the gates, the blind tests and the first review panel are all
    paid for by the time the first repair round starts. A remediator that dies
    used to raise through the phase and end the run, which trades a packet with
    unrepaired findings -- useful -- for no packet at all.
    """
    from factory.llm import LLMError

    f, llm, events, repo = factory

    async def dead_round(*args, **kwargs):
        raise LLMError("remediator: connection reset by peer")

    monkeypatch.setattr(f, "_repair_round", dead_round)

    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    packet = asyncio.run(f.run_build(state.feature_id))

    assert packet.findings, "the run died with the loop and the human got nothing"
    assert "connection reset" in packet.rework.stop_reason, (
        "the loop stopped for a reason the packet does not give"
    )
    failed = [r for r in f.store_for(state.feature_id).records()
              if r["kind"] == "rework_failed"]
    assert failed and failed[0]["payload"]["round"] == 1
    charged = {r.finding_id: r for r in packet.records}["reviewer-1"]
    assert charged.attempts == 0, "a round that never ran was charged to the finding"


def test_what_a_repairer_was_handed_is_kept_like_any_other_prompt(factory):
    """INV-2 covers the repair round too.

    Every other agent's brief is archived; the repairer's was not, which is
    exactly backwards. When a repair round produces nothing, the first question
    is what the repairer was looking at, and the only answer available was its
    own account of it.
    """
    f, llm, events, repo = factory
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    asyncio.run(f.run_build(state.feature_id))

    store = f.store_for(state.feature_id)
    prompts = [r for r in store.records() if r["kind"] == "repair_prompt"]
    assert prompts, "the repairer's brief is the one prompt in the system nobody keeps"
    kept = store.dir / prompts[0]["meta"]["prompt_file"]
    assert kept.exists() and "Your work unit: R-1" in kept.read_text(), \
        "the record points at a prompt that is not there"


def test_revalidation_keeps_the_build_and_starts_the_verdict_pass_from_zero(factory):
    """For a packet that measured the harness rather than the code.

    Gates that ran without their toolchain, a re-check that read a summary
    instead of the tree, a repair loop that spent its rounds on a harness that
    was not editing: none of that is a reason to change a line, and all of it is
    a reason not to believe the packet. Rebuilding would throw away work that is
    fine; resuming normally would carry the ledger's spent rounds forward. This
    keeps the branch and reviews it again from nothing.
    """
    f, llm, events, repo = factory
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))

    # Nothing to replace before there is a packet, and saying so now beats a
    # phase that fails in the background five seconds later.
    with pytest.raises(ProjectError, match="awaiting your ruling"):
        asyncio.run(f.revalidate(state.feature_id))

    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    first = asyncio.run(f.run_build(state.feature_id))
    assert first.rework.rounds > 0, "this fixture is only worth anything if the loop ran"

    store = f.store_for(state.feature_id)
    before = len(store.records())
    worker_calls = llm.calls.count("worker")

    second = asyncio.run(f.revalidate(state.feature_id))

    # The build lane was not bought again, and neither was the oracle.
    assert llm.calls.count("worker") == worker_calls, "revalidation rebuilt the feature"
    phases = {p.name: p for p in f.state_of(store).phases}
    assert "reused" in phases["workers"].detail and "reused" in phases["oracle"].detail

    # And the verdict pass started from nothing rather than from round 3.
    assert second.rework.rounds > 0, (
        "the ledger's spent rounds were carried forward; the loop had nothing left to spend"
    )

    # Nothing was removed to do it. (INV-11)
    records = store.records()
    assert len(records) > before
    assert [r for r in records if r["kind"] == "revalidate"], "no record of who asked, or why"
    assert len([r for r in records if r["kind"] == "packet"]) == 2, (
        "the packet being replaced must survive beside the one replacing it"
    )


def test_a_resumed_build_does_not_write_its_old_files_over_newer_ones(factory):
    """The recorded copy of a file is not the current one.

    A resume reuses what the build lane produced, and the integrator's files and
    the blind tests are among them. Applying those recorded contents again would
    overwrite whatever the repair rounds did to the same paths -- silently
    reverting repairs to their pre-repair state, in the one operation whose
    whole purpose is to preserve them.
    """
    f, llm, events, repo = factory
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    asyncio.run(f.run_build(state.feature_id))

    sandbox = f.open_sandbox(f.state_of(f.store_for(state.feature_id)))
    seam = sandbox.path / "widget" / "__init__.py"          # written by the integrator
    blind = sandbox.path / "tests" / "oracle" / "test_spin.py"   # written by the oracle
    assert seam.exists() and blind.exists()
    seam.write_text("# repaired after the integrator wrote this\n")
    blind.write_text("# edited after the oracle wrote this\n")

    asyncio.run(f.revalidate(state.feature_id))

    assert seam.read_text() == "# repaired after the integrator wrote this\n", (
        "the resume wrote the integrator's recorded copy over a later repair"
    )
    assert blind.read_text() == "# edited after the oracle wrote this\n", (
        "the resume rewrote a blind test from memory instead of measuring what is there"
    )


def test_a_resumed_build_finishes_the_panel_it_interrupted(factory, monkeypatch):
    """A panel is a quorum of angles, not whatever finished before the crash.

    A server killed mid-panel leaves some agents' reports recorded and others
    never asked. Reusing the recorded ones and asking for none of the rest hands
    the packet a review one agent short, and nothing in it says so -- the
    missing angle is the one nobody can see is missing.
    """
    f, llm, events, repo = factory
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)

    # One agent of the panel records, then the process dies before the rest are
    # asked -- which is what a restart mid-panel leaves behind.
    every_role = f.config.review_roles()
    assert len(every_role) > 1, "this fixture needs a panel to be able to halve one"
    unasked = every_role[1:]
    for role in unasked:
        role.review = False

    async def killed(*args, **kwargs):
        raise RuntimeError("the server was restarted mid-panel")

    monkeypatch.setattr(f, "_arbitrate", killed)
    with pytest.raises(RuntimeError):
        asyncio.run(f.run_build(state.feature_id))

    recorded = {r["role"] for r in f.store_for(state.feature_id).records()
                if r["kind"] == "review"}
    assert recorded == {every_role[0].name}, "the fixture did not halve the panel"

    monkeypatch.undo()
    for role in unasked:
        role.review = True
    packet = asyncio.run(f.run_build(state.feature_id, resume=True))

    asked = {r["role"] for r in f.store_for(state.feature_id).records()
             if r["kind"] == "review"}
    configured = {r.name for r in f.config.review_roles()}
    assert asked == configured, (
        f"the resumed build shipped a packet reviewed by {sorted(asked)}, "
        f"short of {sorted(configured - asked)}"
    )
    resumed = [r for r in f.store_for(state.feature_id).records()
               if r["kind"] == "review_resumed"]
    assert resumed and resumed[0]["payload"]["missing"], \
        "finishing an interrupted panel left no record that it had been interrupted"
    assert packet.findings


def test_the_review_panel_reads_the_branch_not_the_agents_account_of_it(factory):
    """What a worker reported writing stops being true the moment it is repaired.

    On a resumed or revalidated build the recorded copy is the *first* attempt's,
    predating every repair an earlier dispatch made — so a panel handed it judges
    code that has not existed for hours. Seen for real: three agents reported a
    duplicate class at a line number where it had been deleted two commits
    earlier, the arbiter routed it for repair, and the packet led with it.
    """
    f, llm, events, repo = factory
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    asyncio.run(f.run_build(state.feature_id))

    # Something the workers never wrote, on the branch, in a file they did.
    sandbox = f.open_sandbox(f.state_of(f.store_for(state.feature_id)))
    spin = sandbox.path / "widget" / "spin.py"
    spin.write_text(spin.read_text() + "\n# repaired out of band\n")

    asyncio.run(f.revalidate(state.feature_id))

    seen = [p for p in llm.review_prompts if "# The files this feature wrote" in p]
    assert seen, "the panel was handed the agents' recorded copies, not the branch"
    assert any("# repaired out of band" in p for p in llm.review_prompts), (
        "the panel is judging a copy of the code that no longer exists on the branch"
    )


def test_a_rebuild_keeps_the_frozen_spec_and_replaces_everything_under_it(tmp_path):
    """The mirror of `revalidate`.

    That one keeps the build and discards the verdict, for a packet that
    measured the harness. This discards the build and keeps the spec, for a
    *build* that was the harness: units whose coding harness edited nothing, a
    plan written before the architect was asked for a reading list.

    What must survive is the part that cost a person their attention -- the
    frozen spec, the answers, the approval. What must not survive is anything
    built against it.
    """
    repo = make_repo(tmp_path / "repo")
    config = make_config(tmp_path)
    project = ready_project(config, repo, "demo")

    f = Factory(config, project, llm=StubLLM())
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    asyncio.run(f.run_build(state.feature_id))

    store = f.store_for(state.feature_id)
    before = f.state_of(store)
    frozen = store.payload("spec")
    first_head = before.sandbox.commit_sha
    assert first_head, "the first build committed nothing to discard"

    asyncio.run(f.rebuild(state.feature_id))

    after = f.state_of(store)
    assert after.spec_hash == before.spec_hash, "the rebuild moved the frozen spec"
    assert store.payload("spec") == frozen, "the spec was rewritten under the human"

    # Gate 1 was not reopened: no new interrogation, no new approval.
    kinds = [r["kind"] for r in store.records()]
    assert kinds.count("interrogation") == 1, "the human was asked the same questions again"
    assert kinds.count("approval") == 1, "the human was made to freeze the same spec twice"

    # And everything under the spec was bought again.
    assert kinds.count("plan") == 2, "the plan was reused; it predates read_files"
    assert kinds.count("oracle") == 2, "the blind suite was reused"

    record = [r for r in store.records() if r["kind"] == "rebuild"][-1]["payload"]
    assert record["discarded_head"] == first_head
    assert first_head in record["recover_with"], \
        "the discarded commits are unrecoverable; the record must carry the sha"
    assert "the frozen spec" in record["keeps"]


def test_a_rebuild_returns_the_branch_to_its_base_by_default(tmp_path):
    """Building over the last attempt is a second layer, not a restart."""
    repo = make_repo(tmp_path / "repo")
    config = make_config(tmp_path)
    project = ready_project(config, repo, "demo")

    f = Factory(config, project, llm=StubLLM())
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    asyncio.run(f.run_build(state.feature_id))

    store = f.store_for(state.feature_id)
    base = f.state_of(store).sandbox.base_sha

    # A file the last attempt left behind that this one's plan never mentions.
    sandbox = f.open_sandbox(f.state_of(store))
    (sandbox.path / "leftover.py").write_text("# from the attempt being replaced\n")
    sandbox.commit("factory: stray")

    asyncio.run(f.rebuild(state.feature_id))

    sandbox = f.open_sandbox(f.state_of(store))
    assert not (sandbox.path / "leftover.py").exists(), \
        "the previous attempt's files are still on the branch the human will read"
    assert base in subprocess.run(
        ["git", "log", "--format=%H"], cwd=sandbox.path,
        capture_output=True, text=True).stdout, "the branch no longer contains its base"


def test_a_rebuild_refuses_before_the_spec_is_frozen(tmp_path):
    """There is nothing to discard, and the fix is gate 1, not a rebuild."""
    repo = make_repo(tmp_path / "repo")
    config = make_config(tmp_path)
    project = ready_project(config, repo, "demo")

    f = Factory(config, project, llm=StubLLM())
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))

    with pytest.raises(ProjectError) as caught:
        asyncio.run(f.rebuild(state.feature_id))
    assert "no build to discard" in str(caught.value)


def test_a_rebuild_does_not_inherit_the_lights_of_the_run_it_replaced(tmp_path):
    """The progress strip described a run that no longer existed.

    After a rebuild the console showed fifteen of sixteen phases complete and
    "now at 05 workers": every light from `integrator` rightwards was still lit
    by the attempt the rebuild had just discarded. Nothing downstream reads
    these records, so no assertion about the packet catches it -- the only
    symptom is a human being told the wrong thing about where their run is.
    """
    repo = make_repo(tmp_path / "repo")
    config = make_config(tmp_path)
    project = ready_project(config, repo, "demo")

    f = Factory(config, project, llm=StubLLM())
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    asyncio.run(f.run_build(state.feature_id))

    store = f.store_for(state.feature_id)
    finished = f.state_of(store)
    lit_after_first = {p.name for p in finished.phases if p.status == "done"}
    assert {"architect", "workers", "rapporteur"} <= lit_after_first, \
        "the first build did not finish"

    # What the console would have shown the moment the rebuild started.
    seen: list[list[tuple[str, str]]] = []
    original = f._save

    def capture(store_, state_):
        seen.append([(p.name, p.status) for p in state_.phases])
        return original(store_, state_)

    f._save = capture
    asyncio.run(f.rebuild(state.feature_id))
    f._save = original

    first_build_save = next(
        snapshot for snapshot in seen
        if any(name == "architect" and status == "pending" for name, status in snapshot))
    lit = {name for name, status in first_build_save if status == "done"}
    assert lit == set(pipeline.INTAKE_PHASES), (
        "the strip carried lights from the discarded attempt: "
        f"{sorted(lit - set(pipeline.INTAKE_PHASES))}"
    )

    # Gate 1's own lights stay on, because gate 1 was not reopened.
    for name in pipeline.INTAKE_PHASES:
        assert (name, "done") in first_build_save, \
            f"{name} was cleared; the human is being told to answer gate 1 again"


def test_reset_phases_touches_only_what_it_is_given():
    from factory.schemas import FeatureState, PhaseState

    state = FeatureState(
        feature_id="f", title="t",
        phases=[PhaseState(name=n, status="done", detail="d", started_at="x",
                           ended_at="y", error="e") for n in pipeline.PHASE_NAMES])

    pipeline.reset_phases(state, pipeline.BUILD_PHASES)
    by_name = {p.name: p for p in state.phases}

    assert by_name["scout"].status == "done" and by_name["scout"].detail == "d"
    assert by_name["architect"].status == "pending"
    for field in ("detail", "started_at", "ended_at", "error"):
        assert getattr(by_name["rapporteur"], field) == "", \
            f"{field} survived the reset and will be shown beside a pending phase"


def test_the_integrator_s_seam_check_runs_as_a_gate_and_a_resume_keeps_it(factory):
    """The seam check is a test the integrator leaves in the project's own test
    tree, run by the factory with the gates using the project's own per-file
    rule and committed with the rest of the change; and a "Verify again" keeps
    the integration like the rest of the build rather than re-running it."""
    f, llm, events, repo = factory
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    asyncio.run(f.run_build(state.feature_id))
    store = f.store_for(state.feature_id)

    gates = [r for r in store.records() if r["kind"] == "gates"][-1]["payload"]
    seam = [g for g in gates["results"] if g["name"] == "seam-test_seam_spin_log"]
    assert seam and seam[0]["passed"], "the integrator's seam check runs with the gates"
    assert seam[0]["command"] == "test -f tests/test_seam_spin_log.py", \
        "the command came from somewhere other than the project's own per-file rule"
    sandbox = f.open_sandbox(f.state_of(store))
    assert (sandbox.path / "tests" / "test_seam_spin_log.py").exists(), \
        "the check was deleted, so the repository does not keep it"
    import subprocess
    tracked = subprocess.run(["git", "ls-files"], cwd=sandbox.path, capture_output=True,
                             text=True).stdout
    assert "tests/test_seam_spin_log.py" in tracked, \
        "a seam check that is never committed stops running the day the run ends"

    asked = llm.calls.count("integrator")
    asyncio.run(f.revalidate(state.feature_id))
    phases = {p.name: p for p in f.state_of(store).phases}
    assert llm.calls.count("integrator") == asked, "Verify again re-ran the integrator"
    assert phases["integrator"].detail.startswith("reused from the last attempt")
    assert "1 seam check(s)" in phases["integrator"].detail
    gates = [r for r in store.records() if r["kind"] == "gates"][-1]["payload"]
    assert any(g["name"] == "seam-test_seam_spin_log" for g in gates["results"]), \
        "a kept integration's checks still run"


# --------------------------------------------------------------------------
# the checkers: the spec checker reads as the oracle, the plan checker as each
# worker, and code decides what is settled and whether a person is needed
# --------------------------------------------------------------------------

from factory.schemas import (  # noqa: E402
    CheckObjection, CheckReport, ObjectionAnswer, PlanRevision, SpecRevision,
)
from factory.workspace import verify_context  # noqa: E402


class CheckerStubLLM(StubLLM):
    """The same run with both checkers configured, and scripted answers.

    `spec_objections` and `plan_objections` are what each checker says; the
    `*_revision` callables are how the author answers them. Everything else is
    the ordinary stub.
    """

    def __init__(self, *, spec_objections=(), spec_revision=None,
                 plan_objections=(), plan_revision=None, plan=None, recut=None):
        super().__init__()
        self.spec_objections = list(spec_objections)
        self.spec_revision = spec_revision
        self.plan_objections = list(plan_objections)
        self.plan_revision = plan_revision
        self.plan = plan
        self.recut = recut
        self.checker_prompts: list[str] = []

    async def ask(self, role, prompt, schema=None, *, system="", temperature=None):
        if role == "spec_checker":
            self.calls.append(role)
            self.checker_prompts.append(prompt)
            assert system, "the spec checker was called without its prompt file"
            seat = "oracle" if "Your seat: the oracle" in prompt else "asked"
            return CheckReport(objections=[
                o for o in self.spec_objections if o.kind.startswith(seat + ":")])
        if role == "spec_writer" and schema is SpecRevision:
            self.calls.append("spec_writer:revision")
            return self.spec_revision(prompt)
        if role == "plan_checker":
            self.calls.append(role)
            assert system, "the plan checker was called without its prompt file"
            return CheckReport(objections=list(self.plan_objections))
        if role == "architect" and schema is PlanRevision:
            self.calls.append("architect:revision")
            return self.plan_revision(prompt)
        if role == "architect" and self.recut is not None and "A person ruled at plan review" in prompt:
            self.calls.append("architect:recut")
            return self.recut.model_copy(deep=True)
        if role == "architect" and self.plan is not None:
            self.calls.append(role)
            return self.plan.model_copy(deep=True)
        return await super().ask(role, prompt, schema, system=system, temperature=temperature)


def checker_factory(tmp_path, llm):
    repo = make_repo(tmp_path / "repo")
    config = make_config(tmp_path)
    # A different family from the authors they read, as a real config has it.
    for name in ("spec_checker", "plan_checker"):
        config.roles[name] = RoleConfig(name=name, model=f"stubverify/{name}")
    project = ready_project(config, repo, "demo")
    return Factory(config, project, llm=llm, on_progress=lambda e: None)


def _objection(kind, claim, **anchors):
    return CheckObjection(id="K-9", kind=kind, claim=claim,
                          consequence=f"because {claim}", **anchors)


def _to_spec_review(f):
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    spec = asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    return state.feature_id, spec


def test_the_spec_checker_improves_the_spec_and_shows_only_the_disagreement(tmp_path):
    def revise(prompt):
        assert "S-1" in prompt and "S-2" in prompt, "the objections are answered by id"
        spec = _spec()
        spec.acceptance_criteria[0].statement = "spin() turns the widget clockwise by 90 degrees"
        return SpecRevision(spec=spec, answers=[
            ObjectionAnswer(objection_id="S-1", answer="revised", note="said how far"),
            ObjectionAnswer(objection_id="S-2", answer="rebutted",
                            note="the log destination is a constraint, not a behaviour"),
        ])

    llm = CheckerStubLLM(spec_objections=[
        _objection("oracle:untestable", "AC-1 says it spins but not how far",
                   criterion_ids=["AC-1"]),
        _objection("oracle:untestable", "an objection anchored to nothing real",
                   criterion_ids=["AC-99"]),
        _objection("asked:uncovered_answer", "no criterion says where it logs",
                   question_ids=["Q-1"]),
    ], spec_revision=revise)
    f = checker_factory(tmp_path, llm)
    feature_id, spec = _to_spec_review(f)

    # One seat is exactly the oracle's: built by the same function, from the
    # draft, so the checker cannot read the spec with more than the oracle has.
    draft = pipeline.Spec.model_validate(f.store_for(feature_id).payload("spec_draft"))
    assert verify_context(draft) in llm.checker_prompts[0]
    assert llm.calls.count("spec_checker") == 2

    # Settled: the spec is simply better, and nothing about it reaches the human.
    assert spec.acceptance_criteria[0].statement.endswith("by 90 degrees")
    assert not any(q.startswith("S-1 ") for q in spec.open_questions)
    # Rebutted: carried as an open question, with the spec writer's reason.
    carried = [q for q in spec.open_questions if q.startswith("S-2 (spec checker)")]
    assert carried and "a constraint, not a behaviour" in carried[0]
    # Anchored to nothing that exists: dropped, not carried as noise.
    assert not any("anchored to nothing" in q for q in spec.open_questions)
    assert f.state_of(f.store_for(feature_id)).stage == "awaiting_spec_approval"


def test_a_revision_that_changed_nothing_is_not_taken_on_its_word(tmp_path):
    llm = CheckerStubLLM(
        spec_objections=[_objection("oracle:untestable", "AC-2 asserts nothing",
                                    criterion_ids=["AC-2"])],
        spec_revision=lambda prompt: SpecRevision(spec=_spec(), answers=[
            ObjectionAnswer(objection_id="S-1", answer="revised", note="fixed it")]))
    f = checker_factory(tmp_path, llm)
    _, spec = _to_spec_review(f)
    carried = [q for q in spec.open_questions if q.startswith("S-1 (spec checker)")]
    assert carried, "a claimed revision that changed nothing stays open"


def test_a_failing_spec_checker_does_not_cost_the_spec(tmp_path):
    class Broken(CheckerStubLLM):
        async def ask(self, role, prompt, schema=None, **kwargs):
            if role == "spec_checker":
                raise RuntimeError("checker down")
            return await super().ask(role, prompt, schema, **kwargs)

    f = checker_factory(tmp_path, Broken())
    feature_id, spec = _to_spec_review(f)
    assert [c.id for c in spec.acceptance_criteria] == ["AC-1", "AC-2", "AC-3"]
    assert f.state_of(f.store_for(feature_id)).stage == "awaiting_spec_approval"


def _build(f, feature_id, resume=False):
    return asyncio.run(f.run_build(feature_id, resume=resume))


def test_a_quiet_cut_goes_straight_to_the_workers(tmp_path):
    llm = CheckerStubLLM()
    f = checker_factory(tmp_path, llm)
    feature_id, _ = _to_spec_review(f)
    f.approve_spec(feature_id)
    _build(f, feature_id)

    store = f.store_for(feature_id)
    assert f.state_of(store).stage == "awaiting_verdict"
    review = store.payload("cut_review")
    assert review["needed"] is False
    assert review["reason"].startswith("plan review not needed"), \
        "a gate that did not fire says why, so the log shows it was checked"
    assert "worker" in llm.calls


def _disputed(tmp_path, **extra):
    llm = CheckerStubLLM(
        plan_objections=[_objection(
            "needs_other_unit", "U-2 logs what spin() returns, which U-1 is writing",
            unit_ids=["U-2"])],
        plan_revision=lambda prompt: PlanRevision(plan=CANNED["architect"], answers=[
            ObjectionAnswer(objection_id="K-1", answer="rebutted",
                            note="U-2 only needs the log line the spec pins")]),
        **extra)
    f = checker_factory(tmp_path, llm)
    feature_id, _ = _to_spec_review(f)
    f.approve_spec(feature_id)
    return f, llm, feature_id


def test_a_disputed_cut_stops_before_any_worker_is_paid(tmp_path):
    f, llm, feature_id = _disputed(tmp_path)
    assert _build(f, feature_id) is None

    store = f.store_for(feature_id)
    state = f.state_of(store)
    assert state.stage == "awaiting_cut_approval"
    assert not state.owner, "a run stopped at a gate has let go of the feature"
    assert "worker" not in llm.calls and "oracle" not in llm.calls
    review = store.payload("cut_review")
    assert review["needed"] and [i["objection"]["id"] for i in review["open"]] == ["K-1"]

    # Resuming must not walk past the gate.
    assert _build(f, feature_id, resume=True) is None
    assert "worker" not in llm.calls


def test_a_measured_problem_stops_the_cut_even_when_the_checker_is_quiet(tmp_path):
    tangled = CANNED["architect"].model_copy(deep=True)
    tangled.units[0].provides = ["widget.spin.spin_the_widget()"]
    tangled.units[1].requires = ["widget.spin.spin_the_widget()"]
    llm = CheckerStubLLM(plan=tangled)
    f = checker_factory(tmp_path, llm)
    feature_id, _ = _to_spec_review(f)
    f.approve_spec(feature_id)
    assert _build(f, feature_id) is None

    review = f.store_for(feature_id).payload("cut_review")
    assert review["open"] == []
    assert any("U-2 needs" in fact for fact in review["facts"])


def test_building_as_one_piece_is_arithmetic_not_an_agent(tmp_path):
    f, llm, feature_id = _disputed(tmp_path)
    _build(f, feature_id)
    architects = llm.calls.count("architect")

    f.rule_on_cut(feature_id, "one_piece")
    _build(f, feature_id, resume=True)

    store = f.store_for(feature_id)
    assert llm.calls.count("architect") == architects, "merging the units asked no model"
    plan = store.payload("plan")
    assert [u["id"] for u in plan["units"]] == ["U-1"]
    assert plan["units"][0]["criterion_ids"] == ["AC-1", "AC-2", "AC-3"]
    assert set(plan["units"][0]["files_expected"]) == {"widget/spin.py", "widget/log.py"}
    assert f.state_of(store).stage == "awaiting_verdict"


def test_keeping_the_cut_carries_the_objection_into_the_packet(tmp_path):
    f, llm, feature_id = _disputed(tmp_path)
    _build(f, feature_id)
    f.rule_on_cut(feature_id, "keep")
    packet = _build(f, feature_id, resume=True)

    kept = [x for x in packet.findings if x.id == "cut-kept-K-1"]
    assert kept, [x.id for x in packet.findings]
    assert "U-2 only needs the log line" in kept[0].detail
    assert kept[0].evidence == "U-2"


def test_siding_with_the_checker_re_cuts_once(tmp_path):
    one = pipeline.merge_units(CANNED["architect"])
    f, llm, feature_id = _disputed(tmp_path, recut=one)
    _build(f, feature_id)
    f.rule_on_cut(feature_id, "revise", note="one unit is fine")
    _build(f, feature_id, resume=True)

    assert llm.calls.count("architect:recut") == 1
    assert f.state_of(f.store_for(feature_id)).stage == "awaiting_verdict"


def test_back_to_the_spec_gets_a_fresh_cut_on_the_next_approval(tmp_path):
    f, llm, feature_id = _disputed(tmp_path)
    _build(f, feature_id)
    state = f.rule_on_cut(feature_id, "back_to_spec")
    assert state.stage == "awaiting_spec_approval"
    assert "worker" not in llm.calls

    before = llm.calls.count("architect")
    f.approve_spec(feature_id)
    _build(f, feature_id)
    assert llm.calls.count("architect") == before + 1, "a fresh architect, not the old cut"


def test_a_ruling_needs_a_cut_waiting_for_one(tmp_path):
    llm = CheckerStubLLM()
    f = checker_factory(tmp_path, llm)
    feature_id, _ = _to_spec_review(f)
    with pytest.raises(ProjectError):
        f.rule_on_cut(feature_id, "keep")


def test_a_proposed_alternative_reaches_the_architect_as_the_ruling(tmp_path):
    one = pipeline.merge_units(CANNED["architect"])
    f, llm, feature_id = _disputed(tmp_path, recut=one)
    _build(f, feature_id)
    seen = []
    original = llm.ask

    async def spy(role, prompt, schema=None, **kwargs):
        if role == "architect":
            seen.append(prompt)
        return await original(role, prompt, schema, **kwargs)

    llm.ask = spy
    with pytest.raises(ProjectError):
        f.rule_on_cut(feature_id, "alternative", note="   ")
    f.rule_on_cut(feature_id, "alternative", note="spin and log are one unit; nothing to split")
    _build(f, feature_id, resume=True)

    assert len(seen) == 1 and "> spin and log are one unit; nothing to split" in seen[0]
    assert "sided with neither of you" in seen[0]
    assert f.state_of(f.store_for(feature_id)).stage == "awaiting_verdict"


def test_a_seam_check_failing_in_a_workers_file_is_repaired_in_that_file(factory):
    """The seam check is the integrator's and a repair may not change it; the
    error it fails with is raised in a file a worker wrote. So the repair unit
    owns that file, and not the check.

    The run this comes from sent `seams-failing` to a repairer twice with the
    check as the one file it owned -- and the same check listed among the
    paths refused before they land. Both edits were refused; the file at fault
    was on no unit's list.
    """
    from factory.schemas import ArbiterReport, FindingDisposition

    f, llm, events, repo = factory
    # The seam check fails, and says where: a traceback into the worker's file.
    f.project.state.test_file_commands = [
        TestFileCommand(match="tests/test_seam_*",
                        command="echo 'File \"/workspace/widget/spin.py:3\", in spin' >&2; exit 1"),
        TestFileCommand(match="*.py", command="test -f {path}"),
    ]
    asked = llm.ask

    async def routes_the_seam(role, prompt, schema=None, **kw):
        if role == "arbiter":
            llm.calls.append(role)
            return ArbiterReport(summary="the seam is mechanical", dispositions=[
                FindingDisposition(finding_id="seams-failing", disposition="repair",
                                   reason="fix whichever side is wrong", confidence=0.8)])
        return await asked(role, prompt, schema, **kw)

    llm.ask = routes_the_seam
    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    packet = asyncio.run(f.run_build(state.feature_id))

    seam = next(x for x in packet.findings if x.id == "seams-failing")
    assert "widget/spin.py" in seam.files, "the finding does not name where the error was raised"
    plans = [r["payload"] for r in f.store_for(state.feature_id).records()
             if r["kind"] == "repair_plan"]
    assert plans, "the seam finding was never planned for repair"
    unit = next(u for u in plans[0]["units"] if "seams-failing" in u["finding_ids"])
    assert unit["files_expected"] == ["widget/spin.py"], (
        "the unit owns the seam check it may not change, or not the file the error is in")
    writes = [r["payload"] for r in f.store_for(state.feature_id).records()
              if r["kind"] == "writes" and str(r["payload"].get("stage", "")).startswith("repair")]
    assert not any("tests/test_seam_spin_log.py" in w.get("protected_refused", []) for w in writes)


def test_a_blind_test_that_does_not_clean_up_is_found_and_sent_to_the_oracle(factory, tmp_path):
    """A blind test that passes once and leaves something behind -- here, a row
    in a shared "database" that makes its own next run fail -- is caught by
    running it again straight after. The packet names the file, the project's
    own tests are not blamed for it, and the oracle is handed the problem to
    fix in its own file.

    The run this comes from had the oracle's fixture add two lists per test to
    the one demo board every browser test opens, and the project's board test
    failed for it -- reported as the feature's doing.
    """
    f, llm, events, repo = factory
    shared = tmp_path / "shared-db"
    # test_spin.py fails as it always does in this suite; test_log.py passes the
    # first time and leaves a row that makes the next run of it fail.
    f.config.rework.oracle_file_command = (
        f'if [ "{{path}}" = "tests/oracle/test_spin.py" ]; then exit 1; '
        f'elif [ "{{path}}" = "tests/oracle/test_log.py" ]; then '
        f'test ! -e "{shared}" && touch "{shared}"; else true; fi')

    state = asyncio.run(f.run_intake("build a widget that spins", "Widget"))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    packet = asyncio.run(f.run_build(state.feature_id))

    leak = next((x for x in packet.findings if x.id == "leaks-1"), None)
    assert leak is not None, "a blind test that breaks its own next run went unreported"
    assert leak.files == ["tests/oracle/test_log.py"]
    assert "Not confirmed" in leak.detail, \
        "this project declares no reset, so the leak cannot be told from a flake"
    # What the oracle's fix loop is handed: the leak, in its own file, from the
    # blind run the packet was read off.
    from factory.pipeline import oracle_problems
    from factory.schemas import GateReport, OracleSuite
    # The round it first leaked in. Every round after starts with its row
    # already there, so from then on the file simply fails -- which is what
    # leftover state does, and why it is worth catching the first time.
    store = f.store_for(state.feature_id)
    gates = next(
        report for r in store.records() if r["kind"] == "gates"
        if (report := GateReport.model_validate(r["payload"]))
        and next(g for g in report.results if g.name == "blind-tests").leaked)
    blind = next(g for g in gates.results if g.name == "blind-tests")
    assert blind.leaked == ["tests/oracle/test_log.py"]
    assert "tests/oracle/test_log.py" not in blind.named_failing, \
        "the leak changed the file's own verdict; it passed, and that stands"
    # The oracle's files as they sit on the branch -- the recorded suite is the
    # one it answered with, before its files were placed.
    from factory.schemas import TestFile
    placed = OracleSuite(strategy="s", tests=[
        TestFile(path=path, contents="", criterion_ids=["AC-1"]) for path in blind.witnessed])
    assert "`tests/oracle/test_log.py` leaves something behind" in oracle_problems(
        gates, blind, placed)


class AsBuiltStubLLM(StubLLM):
    """The same run, with the as-built's two roles answered too: every file
    reports itself plainly, and the cartographer names what it is given."""

    async def ask(self, role, prompt, schema=None, *, system="", temperature=None):
        from factory.schemas import CapabilityMap, FileRecord, GroupNaming, NamedGroup, SystemAccount
        import re as _re
        if schema is FileRecord:
            self.calls.append(role)
            path = _re.search(r"# File\n\n(\S+)", prompt).group(1)
            return FileRecord(purpose=f"{path}, as read", is_test=path.startswith("tests/"))
        if schema is GroupNaming:
            self.calls.append(role)
            return GroupNaming(groups=[NamedGroup(key=k, name=f"around {k}", summary="s")
                                       for k in _re.findall(r"## key: (\S+)", prompt)])
        if schema is CapabilityMap:
            self.calls.append(role)
            return CapabilityMap()
        if schema is SystemAccount:
            self.calls.append(role)
            return SystemAccount(summary="a widget library")
        return await super().ask(role, prompt, schema, system=system, temperature=temperature)


def test_a_review_says_what_the_feature_changed_in_the_system(tmp_path):
    """The as-built is read at the commit the feature branched from and at its
    head, beside the run, and the packet says what moved in the system's terms.
    Neither reading is committed anywhere: the branch carries the work and the
    packet, and nothing under `.fabrika/`."""
    repo = make_repo(tmp_path / "repo")
    config = make_config(tmp_path)
    config.roles["reader"] = RoleConfig(name="reader", model="stub/reader")
    config.roles["cartographer"] = RoleConfig(name="cartographer", model="stub/cartographer")
    project = ready_project(config, repo, "demo")
    llm = AsBuiltStubLLM()
    f = Factory(config, project, llm=llm, on_progress=lambda e: None)

    created = f.create_feature("build a widget that spins", "Widget")
    state = asyncio.run(f.run_intake(created.feature_id))
    asyncio.run(f.finalize_spec(state.feature_id, {"Q-1": "clockwise"}))
    f.approve_spec(state.feature_id)
    asyncio.run(f.run_build(state.feature_id))

    store = f.store_for(state.feature_id)
    labels = [(r["payload"] or {}).get("label") for r in store.all("as_built")]
    assert "base" in labels and "head" in labels
    change = store.payload("system_change")
    assert change, "both readings exist, so the change is computed"
    assert "widget/spin.py" in change["files_added"]
    assert llm.calls.count("reader") >= 1

    final = f.state_of(store)
    sandbox = f.open_sandbox(final)
    tree = subprocess.run(["git", "ls-tree", "-r", "--name-only", sandbox.head()], cwd=repo,
                          capture_output=True, text=True).stdout
    assert ".fabrika/as-built" not in tree, "a feature's reading was committed onto its branch"
    packet_file = store.payload("packet_file") or {}
    if packet_file.get("commit"):
        body = subprocess.run(["git", "show", f"{packet_file['commit']}:{packet_file['path']}"], cwd=repo,
                              capture_output=True, text=True).stdout
        assert "## What this changes in the system" in body
