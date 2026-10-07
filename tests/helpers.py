"""What the tests share: the imports, fixtures, stand-ins and builders they use.

These were defined among the tests of one thirty-thousand-line file, each
where it was first needed. The tests are split by what they exercise now, and
import all of this with `from helpers import *`.
"""

from __future__ import annotations

import ast
import asyncio
import datetime
import inspect
import os
import pathlib
import json
import re
import sys
import tempfile
import time
import textwrap
import types
import typing
from pathlib import Path, PurePosixPath

import pytest

from factory import executors, gates, llm, onboarding, pipeline, schemas, server, store, unitenv, workspace
from factory import projects as pipeline_projects_module
from factory import config as config_module
from factory import agentbox as agentbox_module
from factory import routes as routes_mod


def _global_harness(config, command):
    """Point every role back at `executor.command` for this test.

    A role's route now supplies its own session command, and the shipped config
    assigns one to every role -- so a test that stubs `executor.command` and
    nothing else is stubbing a harness nobody will launch, and the real one runs
    instead. That is not hypothetical: assigning routes turned this suite into
    one that spawned live agents and hung.

    Clearing the routes is what "use the harness I just configured" means now.
    """
    config.executor.kind = "command"
    config.executor.command = list(command)
    for role in config.roles.values():
        role.route = ""
    return config

from factory.config import Config, RoleConfig, load_config
from factory.llm import LLM, LLMError
from factory.pipeline import (
    FindingLedger,
    compute_rework,
    compute_trace,
    compute_unclaimed,
    human_flags,
    merge_reviews,
    restore_packet,
    seed_flags,
)
from factory.schemas import (
    AcceptanceCriterion,
    ArbiterReport,
    EnvironmentSpec,
    Gate,
    BreakerReport,
    BreakerSuite,
    BreakerTest,
    Decision,
    FileWrite,
    Finding,
    HumanFlag,
    FindingDisposition,
    FindingRecord,
    OracleSuite,
    Packet,
    RepairVerdict,
    ReviewReport,
    ReworkSummary,
    SelfDisclosure,
    Spec,
    SupportFile,
    TestFile as OracleTestFile,
    TraceRow,
    WorkerOutput,
)
from factory.store import EvidenceStore
from support import app_js, class_source, factory_files, factory_source, stylesheet, suite_source

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "factory"


def strip_prose(source: str) -> str:
    """Source with docstrings removed, so a grep looks at code and not at prose."""
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                body.pop(0)
    return ast.unparse(tree)


def code_without_prose(func) -> str:
    """The function's source with docstrings and comments removed.

    A grep for a forbidden name has to look at code. A docstring that says
    'there is no digest here' would trip a naive substring search and, worse,
    would make the honest thing to do be deleting the explanation.
    """
    return strip_prose(inspect.getsource(func))


# ==========================================================================
# V-3 (INV-3) -- traceability is computed, never asked for
# ==========================================================================


def _worker(criterion_id: str, files: list[str]) -> WorkerOutput:
    return WorkerOutput(
        unit_id="U-1",
        summary="built something",
        files=[FileWrite(path=f, contents="x = 1\n") for f in files],
        decisions=[Decision(
            id="D-1", title="a choice", rationale="because",
            criterion_ids=[criterion_id], files=files,
        )],
        disclosure=SelfDisclosure(),
    )


# ==========================================================================
# Every "N lines" in the packet is the size of a change, not of a file
#
# `FileWrite.contents` is always the complete file, so counting what an agent
# returned measured the file it touched. A repair unit that added a six-line
# comment to an 800-line seed module was reported as 800 lines of work nobody
# asked for, and the headline figure a human budgets their reading against was
# the sum of every touched file's size -- ten times the real diff. The numbers
# come off the branch now.
# ==========================================================================


def _repo_with_one_commit(root, files):
    import subprocess

    root.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
                   cwd=root, check=True)
    return root


# ==========================================================================
# A unit that could not run its own code says so where a human reads
#
# `unitenv` gives every authoring agent the project's real stack. When it fails
# to come up, the agent is told and writes the code anyway -- deliberate, and
# fine. What was not fine is that the fact stopped at the brief: a run whose
# build lane and every repair round worked blind produced a packet identical to
# one where they all had a database, and the only reason anybody found out was
# that one worker mentioned it in prose. The verify lane, on the same failure,
# refuses to run and says it knows nothing. Both lanes answer the same way now.
# ==========================================================================


def _env_record(store, unit, ready, problem="", log=""):
    return store.append("unit_env", {
        "unit": unit, "role": "worker", "round": 0,
        "ready": ready, "problem": problem, "log": log, "report": "",
    }, role="orchestrator", spec_hash="sha256:x")


# ==========================================================================
# An agent that did not come back is not an agent that found nothing
#
# Every failure below was already caught, recorded with its error, and survived
# without taking the run down -- which is right. What was missing was the way
# out: nothing read those records, so a run that lost its entire review panel to
# a quota produced the same packet as a run where three agents read the code and
# had no objections.
# ==========================================================================


# ==========================================================================
# What the plans had left, read before the work is committed against them
#
# The gauge is not decoration on a status page: it is what says whether a run
# can be scheduled and finish. The run this was written for lost its entire
# review panel in one moment, on the last pass, to a plan that had nothing left
# -- and the packet reported that identically to three agents finding nothing.
# ==========================================================================


def _headroom(store, routes, spec_hash="sha256:x"):
    return store.append("headroom", {"routes": routes}, role="orchestrator",
                        spec_hash=spec_hash)


def _sched_cfg(tmp_path, **over):
    from factory.config import Config, PathsConfig, RouteConfig

    cfg = Config(paths=PathsConfig(evidence=str(tmp_path), sandboxes=str(tmp_path / "s")))
    cfg.routes = {"claude-code": RouteConfig(
        name="claude-code", kind="cli", command=["true"],
        meter_windows_key="rate_limit_info.unifiedWindows")}
    for key, value in over.items():
        setattr(cfg.scheduling, key, value)
    return cfg


def _seed_gauge(monitor, utilization, resets_in):
    from factory.meters import WindowReading

    monitor._meters.record("claude-code", [WindowReading(
        name="five_hour", utilization=utilization, window_minutes=300,
        resets_at=time.time() + resets_in, observed_at=time.time())])


# ==========================================================================
# One defect found six ways is one defect, and still six findings
#
# The panel samples review agents more than once and at high temperature, so a
# real defect arrives worded several ways. Eleven findings in one run were two
# facts. The arbiter is the only agent that reads them all at once, so it says
# which restate which -- and the corroboration is why it chose `repair` over
# `escalate`, so the merge must keep it.
# ==========================================================================


def _ledger_with(*titles, role="adversary"):
    ledger = FindingLedger()
    ledger.add(role, [Finding(id=f"x-{i}", title=t, severity="blocker",
                              detail="d", evidence="e")
                      for i, t in enumerate(titles)], 0)
    return ledger


#: The real shape, copied from a `codex exec --json` call on codex-cli 0.153.4.
#: `credits` and `plan_type` sit in the same map and are not windows.
CODEX_RATE_LIMITS = {
    "limit_id": "codex",
    "primary": {"used_percent": 0.0, "window_minutes": 300, "resets_at": 0},
    "secondary": {"used_percent": 30.0, "window_minutes": 10080, "resets_at": 0},
    "credits": {"has_credits": False, "unlimited": False, "balance": None},
    "plan_type": "team",
    "individual_limit": None,
}


def _codex_route(**over):
    from factory.config import RouteConfig

    fields = {"name": "codex", "meter_windows_key": "payload.rate_limits",
              "meter_utilization_key": "used_percent", "meter_resets_key": "resets_at",
              "meter_utilization_scale": 0.01}
    return RouteConfig(**{**fields, **over})


# ==========================================================================
# V-5 (INV-5) -- no agent returns free text
# ==========================================================================


def _stub_config() -> Config:
    return Config(roles={"scout": RoleConfig(name="scout", model="test/model")})


def _response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


# ==========================================================================
# A project and a feature at gate 2, with no story attached
# ==========================================================================
#
# The smallest real thing the tests below need. None of them wants a story:
# three want a feature that has reached `awaiting_verdict`, so a budget and a
# route can be computed against it, and two want an app object at all.


def _demo_project(tmp_path):
    """A registered project over a one-commit repo, and a config pointed at it."""
    import subprocess

    from factory.projects import ProjectRegistry

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# a small repo\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=repo, check=True,
    )

    config = load_config(ROOT / "factory.example.yaml")
    config.paths.evidence = str(tmp_path / "evidence")
    config.paths.sandboxes = str(tmp_path / "evidence" / "sandboxes")

    registry = ProjectRegistry(config.evidence_path)
    project = registry.create(repo, "demo")
    project.state.base_ref = "HEAD"
    project.state.stage = "ready"
    registry.save(project, note="test fixture")
    return config, registry.get(project.id)


@pytest.fixture
def bare_app(tmp_path):
    """The server over a store holding one project and nothing else."""
    from fastapi.testclient import TestClient

    from factory.server import create_app

    config, _ = _demo_project(tmp_path)
    return TestClient(create_app(config))


@pytest.fixture
def gate_two_factory(tmp_path):
    """A Factory over one feature sitting on a human's ruling.

    The stage is written rather than reached: getting there honestly means a
    build, and none of the callers are testing the build.
    """
    from factory.llm import LLM
    from factory.pipeline import Factory

    config, project = _demo_project(tmp_path)
    factory = Factory(config, project, llm=LLM(config))
    state = factory.create_feature("a small change", "Small change")
    state.stage = "awaiting_verdict"
    factory.store_for(state.feature_id).append(
        "state", state.model_dump(mode="json"), role="orchestrator")
    return factory, state.feature_id


# ==========================================================================
# editing the roster
# ==========================================================================


@pytest.fixture
def editable_config(tmp_path):
    import shutil
    from factory.config import load_config as _load
    example = tmp_path / "factory.example.yaml"
    shutil.copyfile(ROOT / "factory.example.yaml", example)
    return _load(example), tmp_path


# ==========================================================================
# where the gates run
# ==========================================================================


def _docker_config(**kw):
    from factory.config import DockerConfig
    return DockerConfig(**kw)


# ==========================================================================
# the provider credential
# ==========================================================================


@pytest.fixture
def provider_app(tmp_path):
    import shutil
    from fastapi.testclient import TestClient
    from factory.config import load_config as _load
    from factory.server import create_app

    shutil.copyfile(ROOT / "factory.example.yaml", tmp_path / "factory.example.yaml")
    config = _load(tmp_path / "factory.example.yaml")
    config.paths.evidence = str(tmp_path / "evidence")
    config.api.api_key = ""
    return TestClient(create_app(config)), tmp_path


# ==========================================================================
# reading a repository that does not fit
# ==========================================================================


def _repo_of(tmp_path, sizes: dict[str, int]):
    repo = tmp_path / "repo"
    for rel, size in sizes.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\n" * (size // 6))
    return repo


def _survey_of_a_compose_project(tmp_path, *, proposes_compose: bool):
    import subprocess
    from factory.config import Config, PathsConfig, RoleConfig
    from factory.onboarding import ProjectOnboarding
    from factory.projects import ProjectRegistry
    from factory.schemas import EnvironmentSpec, Gate, ProjectSurvey, ScaffoldFile

    repo = tmp_path / "repo"; repo.mkdir()
    (repo / "README.md").write_text("x\n")
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "."],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=repo, check=True)
    config = Config(roles={"surveyor": RoleConfig(name="surveyor", model="x/y")},
                    paths=PathsConfig(evidence=str(tmp_path / "e"), sandboxes=str(tmp_path / "s")))
    registry = ProjectRegistry(config.evidence_path)
    project = registry.create(repo, "demo")
    project.state.stage = "failed"; project.state.error = "an earlier failure"
    registry.save(project)
    survey = ProjectSurvey(
        name="demo", summary="s",
        gates=[Gate(name="tests", command="pytest")],
        environment=EnvironmentSpec(kind="compose", compose_file=".fabrika/docker-compose.yml",
                                    compose_service="checks"),
        scaffolding=[ScaffoldFile(path=".fabrika/docker-compose.yml", contents="services: {}\n",
                                  purpose="a check cannot run without it")]
        if proposes_compose else [])

    class Reader:
        async def ask(self, role, prompt, schema, system=""):
            return survey

    return ProjectOnboarding(config, registry, llm=Reader()), project, repo


# --------------------------------------------------------------------------
# INV-11 -- rework only adds
#
# The danger in a repair loop is not that it fixes the wrong thing. It is that
# it turns "six problems found, four fixed" into "two problems found". That is
# INV-2's concern moved into time: a later round laundering an earlier round's
# disclosure, and it would make the packet look better while making it worth
# less.
# --------------------------------------------------------------------------


def _finding(title: str, severity: str = "major") -> Finding:
    return Finding(id="ignored", title=title, severity=severity, detail="d", evidence="e")


# --------------------------------------------------------------------------
# what actually ran: a green exit code is not evidence that a test executed
# --------------------------------------------------------------------------


def _reported(name: str, *, passed: bool, witness=(), failing=(), total=0,
              n_passed=0, zero_ran=False, tail: str = "",
              per_file: bool = True) -> gates.GateResult:
    """A gate result shaped the way `run_per_file` leaves one.

    Built directly rather than by running a gate, because these tests are about
    what `compute_qa` concludes, not about running commands. Every number counts
    FILES: one command per blind test file, that file's exit code as its
    verdict. `per_file=False` is the other arrangement -- the suite ran as one
    command, so nothing can be attributed to a criterion.
    """
    return gates.GateResult(
        name=name, passed=passed, exit_code=0 if passed else 1,
        evidence_parsed=True, output_tail=tail, started=True,
        summary_known=per_file, tests_total=total, tests_passed=n_passed,
        tests_failed=len(failing), zero_ran=zero_ran,
        witnessed=list(witness), named_failing=list(failing),
    )


def _one_file_many_criteria(n=14, failing="AC-4", cases_reported=True, name_drift=False,
                            suite_prefix=""):
    """The shape that made this necessary: one file, many criteria, one bad test."""
    crit = [schemas.AcceptanceCriterion(id=f"AC-{i}", statement=f"c{i}") for i in range(3, 3 + n)]
    spec = Spec(title="t", intent="i", summary="s", acceptance_criteria=crit)
    path = "backend/tests_blind/f/test_api.py"
    suite = schemas.OracleSuite(strategy="s", tests=[schemas.TestFile(
        path=path, contents="...", criterion_ids=[c.id for c in crit],
        cases=[schemas.TestCaseAccount(name=f"test_{c.id.lower()}", criterion_ids=[c.id])
               for c in crit])])
    trace = [TraceRow(criterion_id=c.id, test_names=[path], status="traced") for c in crit]
    reported = []
    if cases_reported:
        for c in crit:
            nm = (suite_prefix + f"test_{c.id.lower()}"
                  + ("_drifted" if name_drift else ""))
            reported.append(schemas.CaseOutcome(
                file=path, name=nm,
                status="failed" if c.id == failing else "passed"))
    gate = gates.GateResult(
        name="blind-tests", passed=False, exit_code=1, started=True, summary_known=True,
        witnessed=[path], named_failing=[path], cases=reported)
    return pipeline.compute_qa(spec, suite, gates.GateReport(results=[gate]), trace)


class _RecordingRunner:
    """Runs everything, and fails whatever the caller names."""

    def __init__(self, fail: str = "", fail_nth_reset: int = 0):
        self.log: list[str] = []
        self.fail = fail
        self.fail_nth_reset = fail_nth_reset

    async def execute(self, command, *, cwd, timeout_s, network=True):
        self.log.append(command)
        if command.startswith("RESET"):
            n = sum(1 for c in self.log if c == command)
            bad = self.fail_nth_reset and n == self.fail_nth_reset
            return gates.Execution(exit_code=1 if bad else 0, output="x", started=True)
        code = 1 if (self.fail and self.fail in command) else 0
        return gates.Execution(exit_code=code, output="x", started=True)


def _reading(**kw):
    from factory.schemas import SurveyDiff
    return SurveyDiff(summary="s", **kw)


# --------------------------------------------------------------------------
# the console's data tables track the pipeline
#
# Four agents were added, wired into the orchestrator, given prompts and models,
# and tested end to end -- and the console still did not know they existed. Not
# because anything was broken, but because the console carries several hardcoded
# lists of role names, phase names and record kinds, and nothing connected them
# to the Python side. The roster's `order` array was the visible symptom: an
# unlisted name got indexOf -1 and sorted above the surveyor, so a new pipeline
# agent read as an orphan.
#
# These are boring tests for a boring failure. That is the point: the failure
# is invisible in review, it produces no error, and it will happen again the
# next time an agent is added.
# --------------------------------------------------------------------------



def _js_list(name: str) -> list[str]:
    """The string entries of a top-level `const NAME = [...]` in the console."""
    source = app_js()
    m = re.search(rf"const {name} = \[(.*?)\];", source, re.S)
    assert m, f"{name} is not declared in the console's scripts"
    return re.findall(r"'([^']+)'", m.group(1))


def _js_object_keys(name: str) -> set[str]:
    """The keys of a top-level `const NAME = {...}` in the console."""
    source = app_js()
    m = re.search(rf"const {name} = \{{(.*?)\n\}};", source, re.S)
    assert m, f"{name} is not declared in the console's scripts"
    body = re.sub(r"//[^\n]*", "", m.group(1))
    return set(re.findall(r"(\w+)\s*:", body))


def _record_kinds(*modules: str) -> set[str]:
    """Every kind written to a ledger, read from the source.

    Read rather than listed, so a new `store.append` is covered the moment it is
    written and cannot be forgotten here. Both `store.append(...)` and
    `project.store.append(...)` count: the console renders one ledger view for
    features and projects alike, and the first thing it missed was a project
    kind, because this only looked at pipeline.py.
    """
    kinds: set[str] = set()
    for module in modules:
        tree = ast.parse(factory_source(module))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "append" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                continue
            target = node.func.value
            name = (target.id if isinstance(target, ast.Name)
                    else target.attr if isinstance(target, ast.Attribute) else "")
            if name == "store":
                kinds.add(node.args[0].value)
    return kinds


def _feature_record_kinds() -> set[str]:
    return _record_kinds("pipeline", "projects", "onboarding")


def _pipeline_roles() -> list[str]:
    """The roles the orchestrator calls by name, as the server classifies them."""
    source = factory_source("server")
    m = re.search(r"PIPELINE_ROLES = \[(.*?)\]", source, re.S)
    assert m
    return re.findall(r'"([^"]+)"', m.group(1))


def _project_roles() -> list[str]:
    """The roles called by name for a project, beside the pipeline's."""
    m = re.search(r"PROJECT_ROLES = \[(.*?)\]", factory_source("server"), re.S)
    assert m
    return re.findall(r'"([^"]+)"', m.group(1))


# --------------------------------------------------------------------------
# prompts do not hardcode the agent roster
#
# Review agents are declared in config. You can add one, and you can delete the
# adversary -- docs/agents.md says so and `delete_role` allows it. Every prompt that
# names one is a prompt a supported configuration change turns into a lie, and
# nothing about that failure is visible: the model is simply told about a
# colleague it does not have.
#
# Naming an agent whose output is in your context is different, and allowed: the
# architect saying "the scout gave you a `do_not_duplicate` list" is naming a
# field, not an org chart. What is banned is the deletable half of the roster.
#
# The rule narrowed once more: a prompt names another agent only where that
# agent's output is in its context, or where what it does changes what this
# agent writes. Naming one purely to explain this agent by contrast is out --
# the breaker used to be defined against the oracle and the repairer against
# the worker, and neither ever sees the other. A rule you can state directly
# is better stated directly, and an agent carrying an org chart it never
# interacts with is carrying dead context.
# --------------------------------------------------------------------------


def _prompt_files() -> list[Path]:
    return sorted((PACKAGE / "roles").glob("*.md"))


# --------------------------------------------------------------------------
# gate 0 asks whether the gates work, not whether the repository is clean
#
# It used to require a green baseline, which meant a repository with
# pre-existing failures could not onboard -- and could not use the factory to
# fix itself, because a feature needs an approved project. Differential
# attribution removed the reason for that rule: a red gate is now compared
# against the commit each feature branched from, so it is attributable without
# ever having been green.
#
# What replaces it is a narrower question. A gate that runs and reports is a
# working gate. A gate whose tool is not installed is red forever, reports on
# nothing, and looks like coverage.
# --------------------------------------------------------------------------


def _result(name: str, **kw) -> gates.GateResult:
    return gates.GateResult(name=name, **kw)


class _SessionRunner:
    """A runner that records the order it was asked to do things in."""

    carries_setup = True

    def __init__(self, *, ready_after: int = 0, prepare_fails: bool = False,
                 service_error: str = "", df_output: str = ""):
        self.log: list[str] = []
        self.services: list[str] = []
        self.stopped = False
        self.discarded = False
        self._ready_calls = 0
        self._ready_after = ready_after
        self._prepare_fails = prepare_fails
        self._service_error = service_error
        self._df_output = df_output
        self._session_error = ""

    async def open_session(self, cwd):
        self.log.append("open_session")

    async def discard_session(self):
        self.log.append("discard_session")
        self.discarded = True

    async def start_service(self, command, *, cwd, name):
        self.log.append(f"start_service:{name}")
        if self._service_error:
            return self._service_error
        self.services.append(name)
        return ""

    async def stop_services(self):
        self.log.append("stop_services")
        self.stopped = True

    async def execute(self, command, *, cwd, timeout_s, network=True):
        self.log.append(f"exec:{command}")
        if command.startswith("df "):
            return gates.Execution(exit_code=0, output=self._df_output)
        if command == "READY":
            self._ready_calls += 1
            ok = self._ready_calls > self._ready_after
            return gates.Execution(exit_code=0 if ok else 1, output="")
        if command.startswith("PREPARE") and self._prepare_fails:
            return gates.Execution(exit_code=1, output="migration failed")
        return gates.Execution(exit_code=1 if "test_b" in command else 0, output="")


def _rule(command, match="*", report=""):
    from factory.schemas import TestFileCommand
    return TestFileCommand(match=match, command=command, report=report)


def _service(name="api", ready="READY", timeout=5.0):
    from factory.schemas import Service
    return Service(name=name, command=f"run-{name}", ready_when=ready,
                   ready_timeout_s=timeout)


def _surface(unit="usable", integration="usable", user="usable", setup=()):
    from factory.schemas import SetupFile, TestingSurface, TestingTier
    return TestingSurface(tiers=[
        TestingTier(tier="unit", runner="pytest", verdict=unit),
        TestingTier(tier="integration", runner="pytest", verdict=integration,
                    note="test_isolation.py builds its own client and engine inline.",
                    fixtures=["api_client", "make_patient"],
                    setup_files=[SetupFile(path=p, contents=c) for p, c in setup]),
        TestingTier(tier="user", verdict=user,
                    note="No Playwright, Cypress or component-test surface."),
    ])


class _PlacementRunner:
    """Answers the canary command by exit code, and every gate command by name.

    Nothing about a framework is expressed here, which is the property under
    test: `probe_placement` writes bytes, runs the project's own command, and
    reads an exit code.
    """

    carries_setup = True

    def __init__(self, *, on_passing=0, on_failing=1, gate_codes=None):
        self.on_passing = on_passing
        self.on_failing = on_failing
        self.gate_codes = gate_codes or {}
        self.commands = []
        self.seen = []          # (command, contents-on-disk) at the moment it ran

    async def execute(self, command, *, cwd, timeout_s, network=True):
        self.commands.append(command)
        if command.startswith("gate:"):
            return gates.Execution(exit_code=self.gate_codes.get(command, 0), output="")
        # The canary's own command. Which canary is on disk decides the answer,
        # exactly as a real runner would.
        from pathlib import Path as _P
        body = ""
        for candidate in _P(cwd).rglob("*canary*"):
            body = candidate.read_text()
        self.seen.append((command, body))
        code = self.on_passing if "PASS" in body else self.on_failing
        return gates.Execution(exit_code=code, output=body)


def _placement(**kw):
    from factory.schemas import BlindPlacement
    base = dict(kind="probe", directory="tests/blind", filename="canary_test.py",
                canary_passes="PASS", canary_fails="FAIL")
    base.update(kw)
    return BlindPlacement(**base)


def _gate(name, command):
    from factory.config import GateConfig
    return GateConfig(name=name, command=command)


def _tier(tier, verdict, canary="CANARY-USES-FIXTURES", filename="test_tier_canary.py"):
    from factory.schemas import TestingTier
    return TestingTier(tier=tier, verdict=verdict, runner="pretend",
                       canary=canary, canary_filename=filename)


def _tier_surface(*tiers):
    """Distinct from `_surface` above, which builds one from verdicts alone.
    These tests need tiers carrying their own canaries."""
    from factory.schemas import TestingSurface
    return TestingSurface(tiers=list(tiers))


class _ReportingRunner:
    """Answers the canary by exit code, and writes a JUnit report when asked.

    `names` is what this pretend runner calls the canary's one test, which is
    the whole variable under test: pytest reports `test_x`, vitest reports
    `describe > test_x`, and jest's default templates report `describe test_x`
    with nothing between them that can be split on.
    """

    carries_setup = True

    def __init__(self, names, *, writes_report=True):
        self.names = list(names)
        self.writes_report = writes_report
        self.commands = []

    async def execute(self, command, *, cwd, timeout_s, network=True):
        import re
        from pathlib import Path as _P
        self.commands.append(command)
        body = ""
        for f in _P(cwd).rglob("*canary*"):
            body = f.read_text()
        code = 0 if "PASS" in body else 1
        # Anchored at the `/`: the report is named absolutely, and a greedy
        # \S* would swallow the flag in front of it and write to nowhere.
        at = re.search(r"(/[^\s]*\.factory-report-\w+\.xml)", command)
        if at and self.writes_report:
            cases = "".join(f'<testcase classname="c" name="{n}"/>' for n in self.names)
            _P(at.group(1)).write_text(f"<testsuite>{cases}</testsuite>")
        return gates.Execution(exit_code=code, output=body)


def _reporting_state(**kw):
    from types import SimpleNamespace
    base = dict(
        test_file_commands=[
            _rule("cd web && run {path}", match="web/*"),
            _rule("cd api && run {path}", report="cd api && run --xml={report} {path}"),
        ],
        blind_placements=[
            _placement(directory="web/src", filename="canary.spec.ts", canary_case="it passes"),
            _placement(directory="api/test", filename="CanaryIT.java", canary_case="passes"),
        ],
    )
    base.update(kw)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------
# the gate list is only as current as the reading that produced it
#
# A repository that adds a linter, a runner or a language gains a surface no
# gate covers -- and every existing gate stays green throughout, because none
# of them was ever asked about it. Nothing fails; coverage quietly stops being
# complete. The detector is deterministic on purpose: it decides whether to
# *ask* the surveyor, and that question has a right answer.
# --------------------------------------------------------------------------


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "src").mkdir(exist_ok=True)
    (path / "src" / "a.py").write_text("x = 1\n")
    (path / "pyproject.toml").write_text("[project]\nname='x'\n")
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=path, check=True)
    return path


def _commit(path: Path, message: str) -> str:
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", message], cwd=path, check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=path,
                          capture_output=True, text=True).stdout.strip()


# --------------------------------------------------------------------------
# a re-survey proposes; a human disposes
#
# The same call that can add a gate can drop one. A re-survey that applied
# itself would be a path by which a failing gate quietly disappears -- the
# project-level version of a presenter filtering findings, which INV-4 exists
# to prevent. So it returns a diff, the diff is recorded whether or not it is
# taken, and the rejections are recorded as deliberately as the acceptances.
# --------------------------------------------------------------------------


def _project_with_gates(tmp_path, *gates_):
    from factory.projects import ProjectRegistry

    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "a.py").write_text("x = 1\n")
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "p")
    project.state.gates = list(gates_)
    project.state.baseline = gates.GateReport(results=[
        gates.GateResult(name=g.name, passed=True) for g in gates_])
    project.state.stage = "ready"
    return registry, registry.save(project, note="fixture")


# --------------------------------------------------------------------------
# the surveyor's rules, learned from a real survey that got them wrong
#
# On clinic the surveyor read `pytest.ini` -- it said so in its own rationale
# -- saw `asyncio_mode = auto`, and installed `pytest ruff mypy` without
# `pytest-asyncio`. Every test then errored out and nothing ran, and the check
# was marked optional, so it sat there failing silently and proving nothing.
# It also chose a generated image over the project's own compose file, so the
# suite could never have reached its database either way.
# --------------------------------------------------------------------------

SURVEYOR = PACKAGE / "roles" / "surveyor.md"


def _prose(text: str) -> str:
    """Prompt text with its line wrapping flattened.

    These files are wrapped for a human to read, so any phrase long enough to be
    worth asserting on spans a newline. Matching the raw text asserts on where
    the wrap fell, which is not a property of anything."""
    return " ".join(text.split())


def _config_command_strings(node, trail=()):
    """Every value in a config tree that is handed to a shell, with its path.

    Keyed on the name rather than on a list of fields, so a command added later
    is covered without anybody remembering to add it here.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _config_command_strings(value, trail + (str(key),))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _config_command_strings(value, trail + (f"{i}",))
    elif isinstance(node, str) and trail:
        # `is_command_key` is the shipped predicate `_expand` itself uses. Asking
        # it rather than restating it is what keeps this test measuring the rule
        # that is in force instead of the one that was in force when it was written.
        if any(config_module.is_command_key(part) for part in trail):
            yield ".".join(trail), node


def _harness_repo(tmp_path):
    """A one-commit git repo for a stand-in harness to work in."""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=repo, check=True)
    (repo / "seed.txt").write_text("start\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def _css_rules(text: str, media: str | None = None) -> list[tuple[str | None, set[str], dict[str, str], str]]:
    """Every style rule in source order: its @media, selectors, declarations, selector text."""
    out = []
    i = 0
    while (j := text.find("{", i)) >= 0:
        head, depth, k = text[i:j].strip(), 1, j + 1
        while depth:
            depth += {"{": 1, "}": -1}.get(text[k], 0)
            k += 1
        body = text[j + 1:k - 1]
        if head.startswith("@media"):
            out += [(head, s, d, n) for _, s, d, n in _css_rules(body)]
        elif not head.startswith("@"):
            decls = dict((p.strip(), v.strip()) for p, v in
                         (d.split(":", 1) for d in body.split(";") if ":" in d))
            out.append((media, {s.strip() for s in head.split(",")}, decls, head))
        i = k
    return out


# --------------------------------------------------------------------------
# INV: a verdict is never an artefact of how much output was kept
#
# The defect these cover is the worst one this system has had, because it fails
# toward green. Per-test verdicts were computed by searching `output_tail` --
# the last 4,000 characters of the runner's output. On a real run the tail began
# mid-word, `test_screening_slots_activate.py` had been cut in half, the search
# for it found nothing, and the two criteria that file was the only test for
# were reported to the human as PASSED while it was erroring along with the rest
# of the suite. Nothing in the packet showed it.
# --------------------------------------------------------------------------


def _long_pytest_output(*, cut: str, kept: tuple[str, ...]) -> str:
    """A pytest run whose tail keeps `kept` and severs `cut`."""
    filler = "\n".join(
        f"ERROR tests/filler_{i}.py::TestSomething::test_case_{i} - fixture 'client' not found"
        for i in range(200)
    )
    head = f"ERROR tests/{cut}::TestAC4_Immutability::test_no_put_endpoint_accepts_slot_fields"
    tail = "\n".join(f"ERROR tests/{k}::TestThing::test_thing" for k in kept)
    return "\n".join([
        "= test session starts =", head, filler, tail,
        f"= {len(kept) + 201} errors in 3.2s =",
    ])


# --------------------------------------------------------------------------
# INV: the blind suite is the project's guest, never part of it
#
# The oracle used to write its tests wherever it chose, which in practice was
# the project's own test directory. The project's pytest then collected them, so
# a blind suite that could not reach a fixture it had assumed into existence
# reported 39 errors as the *feature's* test gate failing; the project's linter
# linted them, so 41 errors landed in files INV-12 forbids a repairer to touch;
# and the loop stopped with "converged: nothing left that a repair could fix",
# which was true and entirely the harness's own doing. The breaker has had this
# containment since it was written. The oracle did not.
# --------------------------------------------------------------------------


def _pl(directory, filename):
    from factory.schemas import BlindPlacement
    return BlindPlacement(directory=directory, filename=filename,
                          canary_passes="PASS", canary_fails="FAIL")


# --------------------------------------------------------------------------
# INV: the matrix and its inverse are built from one index
#
# A real packet reported AC-9 and AC-10 as `traced` while listing TrialDetail.tsx
# and Dashboard.tsx -- the 1,305 lines that *are* AC-9 and AC-10 -- under "Not
# asked for". Both statements came out of this module minutes apart, because
# `compute_trace` and `compute_unclaimed` each built their own idea of which
# files answer to a criterion. "Not asked for" is the packet's scope-creep
# signal; a false row there sends a human to read a thousand lines of code that
# is doing exactly what was asked.
# --------------------------------------------------------------------------


def _unit_output(unit_id, path, *, cids=(), not_implemented=()):
    return WorkerOutput(
        unit_id=unit_id, summary="s",
        files=[FileWrite(path=path, contents="x = 1\n")],
        decisions=([Decision(id="D-1", title="t", rationale="r",
                             criterion_ids=list(cids), files=[path])] if cids else []),
        disclosure=SelfDisclosure(not_implemented=list(not_implemented)),
    )


def _plan(*units):
    return schemas.Plan(summary="a cut", units=[schemas.WorkUnit(**u) for u in units])


# --------------------------------------------------------------------------
# INV: a harness is handed the files it cannot ask for
#
# aider sees the files it was passed plus a repo *map* of the rest -- signatures,
# not contents -- and in one-shot mode it cannot widen that, because asking ends
# the turn. The invocation passed no --file and no --read at all, so the context
# was whatever aider guessed from filenames in the brief.
#
# U1 owned a new Alembic migration, needed the revision it descends from, had
# only the map, and said so: "add it to the chat so I can see the chain." Exit 0
# after twenty-one minutes. Four acceptance criteria had no code. No prompt fixes
# this -- the capability is not there to instruct.
# --------------------------------------------------------------------------


def _harness_for(tmp_path, **overrides):
    from factory.config import load_config
    from factory.executors import CommandExecutor
    from factory.llm import LLM

    config = load_config("factory.yaml")
    config.executor.kind = "command"
    for key, value in overrides.items():
        setattr(config.executor, key, value)
    return CommandExecutor(LLM(config), config)


def _migration_repo(tmp_path):
    versions = tmp_path / "backend/alembic/versions"
    versions.mkdir(parents=True)
    for name in ("3647de_initial.py", "b1a2c3_rls.py", "c3d4e5_cards.py", "d4e5f6_task_state.py"):
        (versions / name).write_text("revision = 'x'\n")
    models = tmp_path / "backend/app/models"
    models.mkdir(parents=True)
    (models / "trial.py").write_text("class TrialParticipation: ...\n")
    (models / "patient.py").write_text("class Encounter: ...\n")
    return tmp_path


# --------------------------------------------------------------------------
# INV: the harness loop belongs to us
#
# aider lost four units across two runs by answering "What would you like me to
# change?" and exiting zero. No prompt fixed it, because a subprocess that exits
# has already ended its turn -- it can be restarted from nothing, never told to
# continue. The OpenHands driver owns the loop instead: a turn that wrote nothing
# is refused, in the same conversation, with everything the agent has read still
# in front of it.
# --------------------------------------------------------------------------


def _driver():
    import sys
    sys.path.insert(0, str(ROOT / "factory" / "harnesses"))
    import openhands_driver
    return openhands_driver


def _tiny_repo(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for cmd in (["git", "config", "user.email", "t@t"], ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return tmp_path


# --------------------------------------------------------------------------
# INV: destruction is reported, never silently refused
#
# The refusing version was a threshold standing in for a judgement, in a system
# whose design says judgements are reached by a human reading an argument. It
# was also invisible -- refusals went into the `writes` record's `rejected`
# list, which nothing has ever read back -- so when it fired the packet
# described a build that was neither what the agent produced nor what anyone
# approved, and said nothing about the difference.
#
# And it did not work. A unit cut queue.py from 1,282 lines to 300 and nothing
# was refused, because the test was arithmetic: does the batch's line count hold
# up? A line count cannot tell rescued code from new code, and 330 lines of new
# migration and schema bought permission to delete 1,293 that went nowhere.
# --------------------------------------------------------------------------


def _branch(tmp_path, base_files: dict, then: dict):
    """A worktree-shaped sandbox with a base commit and uncommitted changes."""
    import subprocess

    from factory.sandbox import Sandbox
    from factory.schemas import SandboxState

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    for cmd in (["git", "config", "user.email", "t@t"], ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=repo, check=True)
    for rel, body in base_files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()
    for rel, body in then.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    state = SandboxState(feature_id="f", project_id="p", kind="worktree",
                         path=str(repo), branch="factory/x", base_sha=base)
    return Sandbox(state, repo)


def _checks_with_settings():
    from factory.schemas import Gate

    return [
        Gate(name="lint", command="lint", family="structure",
             config_files=["ruff.toml", "pyproject.toml#tool.ruff"], suppressions=["# noqa"]),
        Gate(name="js-lint", command="lint:js", family="structure",
             config_files=["package.json#eslintConfig"], suppressions=["eslint-disable"]),
        Gate(name="tests", command="test", family="tests"),
    ]


# -- families at gate 0: proposed, held, or gone without -----------------------


def _project_with_suggestions(tmp_path, baseline_results=None, gates=None):
    from factory.projects import ProjectRegistry
    from factory.schemas import (EnvironmentSpec, Gate, GateReport, ProjectSurvey,
                                 Recommendation)

    repo = tmp_path / "repo"
    repo.mkdir()
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")
    project.state.gates = gates or [Gate(name="tests", command="true", family="tests")]
    project.state.environment = EnvironmentSpec(kind="host", setup=["pip install -e ."])
    project.state.survey = ProjectSurvey(
        name="demo", summary="s", environment=EnvironmentSpec(kind="host"),
        recommendations=[
            Recommendation(title="Measure complexity", kind="other", family="quality",
                           why="w", evidence="e", would_gate="radon cc -n C src",
                           install="pip install radon==6.0.1",
                           parse_metric=r"(\d+) blocks", config_files=["setup.cfg#radon"]),
            Recommendation(title="Lint the code", kind="lint", why="w", evidence="e",
                           would_gate="ruff check ."),
        ])
    if baseline_results is not None:
        project.state.baseline = GateReport(results=baseline_results)
    project.state.stage = "ready"
    return registry, registry.save(project)


# -- a quality check held on a feature's own lines -----------------------------


def _sarif(*hits):
    return json.dumps({"version": "2.1.0", "runs": [{"results": [
        {"ruleId": rule, "message": {"text": "found"},
         "locations": [{"physicalLocation": {"artifactLocation": {"uri": uri},
                                             "region": {"startLine": line}}}]}
        for uri, line, rule in hits]}]})


class _StubLookup:
    """Answers for the suite. Nothing here reaches the network."""

    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    async def facts(self, packages, dates=True):
        from factory.dependencies import Facts
        self.asked += list(packages)
        return {k: self.answers.get(k[1], Facts()) for k in packages}


# -- a proposal's checks, ruled on where they live ------------------------------


def _project_with_proposal(tmp_path):
    from factory.projects import ProjectRegistry
    from factory.schemas import EnvironmentSpec, Gate, ProposedGateChange, SurveyDiff

    repo = _repo_with_one_commit(tmp_path / "repo", {"a.py": "x = 1\n"})
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")
    project.state.gates = [Gate(name="tests", command="pytest -q", family="tests"),
                           Gate(name="old", command="old-tool", family="quality")]
    project.state.environment = EnvironmentSpec(kind="host")
    project.state.stage = "ready"
    project = registry.save(project)
    diff = SurveyDiff(summary="s", gate_changes=[
        ProposedGateChange(action="change", name="tests", reason="r.", evidence="e",
                           gate=Gate(name="tests", command="pytest --cov -q", family="tests")),
        ProposedGateChange(action="add", name="fmt", reason="r.", evidence="e",
                           gate=Gate(name="fmt", command="ruff format --check .", family="structure")),
        ProposedGateChange(action="remove", name="old", reason="r.", evidence="e"),
    ])
    project.store.append("resurvey", diff.model_dump(mode="json"), role="resurvey")
    return registry, registry.get(project.id), diff


# -- the rest of a proposal, ruled on where it lives ------------------------------


def _project_with_parts(tmp_path):
    from factory.schemas import Preview, TestFileCommand

    registry, project, diff = _project_with_proposal(tmp_path)
    project.state.test_file_commands = [TestFileCommand(match="*", command="pytest {path}")]
    project = registry.save(project)
    diff.gate_changes = []
    diff.test_file_commands = [TestFileCommand(match="*", command="pytest -q {path}")]
    diff.trace_dirs = ["web/test-results"]
    diff.preview = Preview(open="web", path="/")
    project.store.append("resurvey", diff.model_dump(mode="json"), role="resurvey")
    return registry, registry.get(project.id), diff


# --------------------------------------------------------------------------
# INV: a tag that names no criterion is not a tag
#
# `criterion_ids: list[str]` is satisfied by any list of any strings, so the
# schema cannot tell a tag from a typo. A real oracle returned
# `criterion_ids: [", "]` on every file it wrote -- a valid list of valid
# strings, and a comma. Nothing rejected it, the matrix matched none of them,
# and all twelve criteria came back `no_test`. The blind suite existed, ran, and
# proved nothing about the contract, which is the one outcome the verify lane
# exists to prevent.
# --------------------------------------------------------------------------


def _spec12():
    return Spec(title="t", intent="i", summary="s", acceptance_criteria=[
        AcceptanceCriterion(id=f"AC-{i}", statement="x") for i in range(1, 13)])


# --------------------------------------------------------------------------
# INV: gate 0 stays where a human left it
#
# Re-running the baseline used to end with `stage = "awaiting_approval"`
# unconditionally. Running it to capture the environment's package list sent the
# project back to gate 0 while a feature was building against those very gates:
# the console said "nothing is built here until you approve" over a build
# already in flight, and because a project mid-gate-0 has no bench, the running
# feature became unreachable in the UI. The state could not be true.
# --------------------------------------------------------------------------


def _proved_placement(project, directory="tests/blind"):
    """Give a fixture project somewhere its blind tests are known to work.

    Approval refuses a project that cannot carry a blind test at all, so every
    fixture that approves one has to say where they go. The measurement is
    stubbed here on purpose: what a real one costs is a sandbox, and what it
    proves is tested directly against `probe_placement`.
    """
    from factory.schemas import BlindPlacement, PlacementResult

    project.state.blind_placements = [BlindPlacement(
        directory=directory, filename="canary_test.py",
        canary_passes="PASS", canary_fails="FAIL")]
    project.state.placement_probe = [PlacementResult(
        directory=directory, passing_ran=True, failing_seen=True, usable=True)]
    return project


def _approved_project(tmp_path):
    import subprocess

    from factory.projects import ProjectRegistry
    from factory.schemas import EnvironmentSpec, Gate, GateReport, GateResult

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    for cmd in (["git", "config", "user.email", "t@t"], ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=repo, check=True)
    (repo / "a.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")
    project.state.gates = [Gate(name="tests", command="true")]
    project.state.environment = EnvironmentSpec(kind="host", rationale="fixture")
    project.state.base_ref = "HEAD"
    project.state.baseline = GateReport(results=[GateResult(name="tests", passed=True)])
    _proved_placement(project)
    registry.save(project, note="fixture")
    registry.approve(project)
    return registry, registry.get(project.id)


def _authored(paths_and_text, outside=()):
    from factory.executors import Authored
    from factory.schemas import FileWrite

    return Authored(
        files=[FileWrite(path=p, contents=c, purpose="") for p, c in paths_and_text],
        outside=list(outside),
    )


def _blank_repo(tmp_path, name="tree"):
    import subprocess

    repo = tmp_path / name
    repo.mkdir()
    for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                ["git", "config", "user.name", "t"],
                ["git", "commit", "-q", "--allow-empty", "-m", "empty"]):
        subprocess.run(cmd, cwd=repo, check=True)
    return repo


# --------------------------------------------------------------------------
# A gauge is not a refusal
#
# The tool that reports how much of the plan is left reports it on every call,
# in the same words a plan with nothing left is refused in. Read off the log as
# text, a session that had just succeeded was recorded as refused at its usage
# limit -- and a session that succeeds without writing a file benches the whole
# route for half an hour on the strength of it.
# --------------------------------------------------------------------------

#: The gauge, verbatim off `claude -p --output-format stream-json`, printed
#: between the last message and the result of a run that was never refused
#: anything. `status: allowed` is the reading; `rate_limit` is the word.
GAUGE_LINE = (
    '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed",'
    '"resetsAt":1790188200,"rateLimitType":"five_hour","overageStatus":"rejected",'
    '"overageDisabledReason":"out_of_credits","isUsingOverage":false,'
    '"unifiedWindows":{"five_hour":{"utilization":0.33,"resetsAt":1790188200},'
    '"seven_day":{"utilization":0.53,"resetsAt":1790434800}}},'
    '"uuid":"592f2049-775b-4f15-812c-7a7737984257","session_id":"s"}'
)


def _session_stream(*, result: str, is_error: bool = False) -> str:
    """One harness session as the tool prints it: events, gauge, envelope."""
    return "\n".join([
        json.dumps({"type": "system", "subtype": "init", "session_id": "s",
                    "model": "claude-sonnet-5", "permissionMode": "bypassPermissions"}),
        json.dumps({"type": "assistant", "session_id": "s", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "All 47 backend tests pass."}],
            # A number, and one of the words. Both are ordinary here.
            "usage": {"input_tokens": 429, "output_tokens": 1103}}}),
        GAUGE_LINE,
        json.dumps({"type": "system", "subtype": "post_turn_summary",
                    "status_category": "completed", "session_id": "s"}),
        json.dumps({"type": "result", "subtype": "success", "is_error": is_error,
                    "num_turns": 25, "total_cost_usd": 0.0290558,
                    "result": result, "session_id": "s",
                    "usage": {"input_tokens": 12, "output_tokens": 800}}),
    ])


def _streamed_session_route(**over):
    """A route shaped like the one that prints a gauge: `factory.yaml`'s keys."""
    from factory.config import RouteConfig

    body = dict(name="claude-code", kind="cli", metered="turns",
                stdout_format="jsonl", result_key="result", error_key="is_error",
                cost_key="total_cost_usd", turns_key="num_turns",
                meter_windows_key="rate_limit_info.unifiedWindows",
                meter_utilization_key="utilization", meter_resets_key="resetsAt",
                session_command=["claude", "-p"])
    body.update(over)
    return RouteConfig(**body)


# --------------------------------------------------------------------------
# ROUTES -- where a call goes, and what it is authenticated as
#
# The codebase had two provider systems that did not know about each other:
# `api.*` for completions, `executor.*` for filesystem sessions. Nothing could
# say "the spec writer runs on a Claude subscription", because a subscription is
# neither -- it is a CLI that serves both. A route is the thing that can.
# --------------------------------------------------------------------------

FAKE_CLI = '''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
def flag(name, default=""):
    return args[args.index(name) + 1] if name in args else default
MODE = %r
ok = {"summary": "ok", "stack": [], "conventions": [], "existing_capabilities": [],
      "do_not_duplicate": [], "relevant_files": [], "risks": []}
if MODE == "bad-then-good" and "[your previous answer]" not in prompt:
    body, err = "not json at all", False
elif MODE == "error":
    body, err = "API Error: 401 OAuth access token has expired", True
elif MODE == "not-json":
    print("command not found: claude"); sys.exit(0)
else:
    body, err = json.dumps(ok), False
print(json.dumps({"result": body, "is_error": err,
                  "total_cost_usd": 0.125 if MODE == "costly" else 0,
                  "num_turns": 2, "usage": {"input_tokens": 9, "output_tokens": 4},
                  "seen": {"model": flag("--model"), "schema": flag("--json-schema"),
                           "system": flag("--append-system-prompt"),
                           "argc": len(args), "prompt": prompt}}))
'''


def _cli_route(tmp_path, mode="ok", **over):
    from factory.config import RouteConfig

    script = tmp_path / "fake_cli.py"
    script.write_text(FAKE_CLI % mode, encoding="utf-8")
    body = dict(
        name="claude-code", kind="cli", metered="turns", label="Claude Code",
        command=[sys.executable, str(script), "-p", "--output-format", "json",
                 "--tools", "", "--model", "{model}",
                 "--append-system-prompt", "{system}", "--json-schema", "{schema}"],
        result_key="result", error_key="is_error", cost_key="total_cost_usd",
        turns_key="num_turns", prompt_tokens_key="usage.input_tokens",
        completion_tokens_key="usage.output_tokens",
        session_command=["openhands"],
    )
    body.update(over)
    return RouteConfig(**body)


def _routed(tmp_path, mode="ok", **over):
    from factory.config import Config, RoleConfig
    from factory.llm import LLM

    return LLM(Config(
        roles={"scout": RoleConfig(name="scout", model="opus-5", route="claude-code")},
        routes={"claude-code": _cli_route(tmp_path, mode, **over)},
    ))


FAKE_JSONL_CLI = '''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
sys.stdin.read()
MODE = %r

def emit(obj):
    print(json.dumps(obj))

emit({"type": "thread.started", "thread_id": "t1"})
emit({"type": "turn.started"})
if MODE == "fail":
    emit({"type": "item.completed",
          "item": {"id": "item_0", "type": "error", "message": "model not found"}})
    emit({"type": "error", "message": "400"})
    emit({"type": "turn.failed", "error": {"message": "the model is not supported"}})
    sys.exit(1)
emit({"type": "item.completed",
      "item": {"id": "item_0", "type": "agent_message", "text": "ok"}})
emit({"type": "turn.completed",
      "usage": {"input_tokens": 11, "output_tokens": 3}})
'''


def _jsonl_route(tmp_path, mode="ok"):
    from factory.config import RouteConfig

    script = tmp_path / "fake_jsonl_cli.py"
    script.write_text(FAKE_JSONL_CLI % mode, encoding="utf-8")
    return RouteConfig(
        name="codex", kind="cli", metered="turns", label="Codex",
        command=[sys.executable, str(script), "-m", "{model}"],
        stdout_format="jsonl", result_key="item.text", error_key="error",
        prompt_tokens_key="usage.input_tokens",
        completion_tokens_key="usage.output_tokens",
    )


# --------------------------------------------------------------------------
# Connecting a harness
#
# The sign-in itself cannot happen in the console: every one of these tools
# authenticates through a browser handshake that ends with a paste into a
# terminal, and none of them takes a flag for it. So the console does the parts
# it can -- says which of three things is wrong, hands over the exact line, and
# re-checks on a button.
# --------------------------------------------------------------------------


def _route_cfg(tmp_path=None, **over):
    """A route config whose state lives somewhere disposable.

    `paths.evidence` defaults to `.factory` in the working directory, which is
    the *running console's* store. A test that called `refresh` on a config
    built without one wrote its stand-in route into the real file and dropped
    every connection the human had checked. Tests do not get to touch that.
    """
    import tempfile
    from factory.config import Config, RoleConfig, RouteConfig

    body = dict(name="probe", kind="cli", metered="turns", label="Probe",
                command=["definitely-not-a-real-binary-xyz"], models=["m1"],
                install_command="npm i -g thing", setup_command="thing login",
                setup_steps=["Open a terminal.", "Run: thing login"])
    body.update(over)
    cfg = Config(
        roles={"scout": RoleConfig(name="scout", model="m1", route="probe")},
        routes={"probe": RouteConfig(**body)},
    )
    cfg.paths.evidence = str(tmp_path / "store") if tmp_path is not None \
        else tempfile.mkdtemp(prefix="factory-test-store-")
    return cfg


# --------------------------------------------------------------------------
# The plan's own gauge
#
# The budget guard measures dollars, which do not exist for a subscription.
# What bounds those is a rolling window, and until this a run learned it had
# reached the limit by being refused. One tool reports utilization and an exact
# reset time on every call -- a gauge, not an error.
# --------------------------------------------------------------------------


ENVELOPE = {
    "rate_limit_info": {
        "status": "allowed",
        "unifiedWindows": {
            "five_hour": {"utilization": 0.22, "resetsAt": 1788548400},
            "seven_day": {"utilization": 0.11, "resetsAt": 1788620400},
        },
    },
}


def _metered_route(**over):
    from factory.config import RouteConfig

    body = dict(name="probe", kind="cli", metered="turns",
                meter_windows_key="rate_limit_info.unifiedWindows",
                meter_utilization_key="utilization", meter_resets_key="resetsAt")
    body.update(over)
    return RouteConfig(**body)


DF_HEADER = "Filesystem 1024-blocks Used Available Capacity Mounted on\n"


def _reopen(**over):
    """The rule with every ceiling clear, so each test moves exactly one."""
    args = dict(targets=["f1"], enabled=True, rate_limited=False, round_index=1,
                max_rounds=5, affordable=True, out_of_time=False)
    args.update(over)
    return pipeline.reopen_after_panel(args.pop("targets"), **args)


# --------------------------------------------------------------------------
# the agent inside the unit's container
#
# The run this exists for: eleven Claude Code sessions asked to run pytest,
# ruff, mypy, tsc and npm; the harness's own permission layer refused all 234
# calls; every unit reported code it had never executed, and the fact reached
# the packet as one honest paragraph in prose. The layer was right -- the host
# is somebody's machine. So the agent moves into the container, where running
# the project's commands is no longer a thing anyone has to be talked into.
# --------------------------------------------------------------------------


def _container_route(**kw):
    from factory.config import RouteConfig
    base = dict(
        name="harness", kind="cli", session_in_container=True,
        container_build_image="node:24-bookworm-slim",
        container_install=["install-the-harness"],
        container_session_command=["harness", "--model", "{model}"],
        container_env={"HOME": "/agent-home"},
    )
    base.update(kw)
    return RouteConfig(**base)


class _Prepared:
    def __init__(self, container="c0ffee123456", workdir="/workspace"):
        self.vars = {"FACTORY_ENV_CONTAINER": container,
                     "FACTORY_ENV_WORKDIR": workdir}
        self.ready = True
        self.note = ""


def _executor(tmp_path):
    from factory.config import load_config
    from factory.executors import CommandExecutor
    config = load_config()
    return CommandExecutor(llm=None, config=config), config


def _shipped_container_routes():
    """Every route either config file puts inside a unit's container."""
    from factory.config import load_config
    out = []
    for name in ("factory.example.yaml", "factory.yaml"):
        path = ROOT / name
        if path.exists():
            out += [(name, r) for r in load_config(path).routes.values()
                    if r.authors_in_container()]
    assert out, "no route runs in a container any more; these tests are stale"
    return out


class _ProbeRunner:
    """Runs one probe file per call, failing the paths it is told to.

    `flaky` names paths that pass once and fail on every later run, which is
    the case promotion exists to catch: a race test can pass by not hitting
    the window, and one promoted on a single green run is a permanent
    intermittent failure installed in somebody's suite.
    """

    def __init__(self, fail=(), flaky=()):
        self.fail = set(fail)
        self.flaky = set(flaky)
        self.runs: list[str] = []

    async def execute(self, command, *, cwd, timeout_s, network=True):
        path = command.split()[-1]
        self.runs.append(path)
        seen = self.runs.count(path)
        bad = path in self.fail or (path in self.flaky and seen > 1)
        return gates.Execution(
            exit_code=1 if bad else 0,
            output=f"FAILED {path}" if bad else f"ok {path}", started=True)


def _probe(path, kind="regression", passes_when="correct code returns 409"):
    from factory.schemas import BreakerTest
    return BreakerTest(path=path, contents="x", hypothesis=f"{path} breaks",
                       kind=kind, passes_when=passes_when if kind == "regression" else "")


def _promote(tests, report, *, fail=(), flaky=(), runs=5, per_file=True):
    """`_promote_probes` against a config, with nothing else of the pipeline."""
    config = load_config(Path("factory.yaml"))
    config.rework.promote_runs = runs
    fake = types.SimpleNamespace(config=config)
    runner = _ProbeRunner(fail=fail, flaky=flaky)
    asyncio.run(pipeline.Factory._promote_probes(
        fake, types.SimpleNamespace(path=ROOT), runner, tests, report,
        [_rule("run {path}")] if per_file else []))
    return runner


def _probe_round(ledger, tests, failing, round_index, *, retired=(), ran=True):
    """One round's worth of what the orchestrator does with a probe run."""
    from factory.schemas import BreakerReport, BreakerSuite
    report = BreakerReport(ran=ran, failing=list(failing), retired=list(retired),
                           tests_applied=[t.path for t in tests], output_tail="out")
    suite = BreakerSuite(strategy="", tests=list(tests))
    ran_now = {t.path for t in tests} - set(retired)
    settles = {fid for fid in (
        ledger.id_of("breaker", t.hypothesis) or pipeline.probe_finding_id(t.path)
        for t in tests if t.path in ran_now) if fid}
    ledger.restate("breaker", pipeline.breaker_findings(suite, report), round_index,
                   source="breaker", fresh=report.ran, among=settles, reopen_settled=True)
    return report


def _ledger_of_agents(*raised):
    """A ledger holding one finding per (id, role, model)."""
    from factory.schemas import Finding
    ledger = pipeline.FindingLedger()
    for fid, role, model in raised:
        ledger.add(role, [Finding(id=fid, title=fid, severity="major", detail="d")],
                   0, keep_ids=True, model=model)
    return ledger


def _trace_run(trace_dirs, files, *, cap=None, count=None, test_path="",
               earlier=(), round_index=0):
    """`_collect_traces` against a checkout holding the given files.

    `earlier` is how many recordings earlier rounds already kept, as one
    `traces` record per round, so the ceiling can be seen to reset."""
    import tempfile

    tmp = tempfile.mkdtemp()
    root = pathlib.Path(tmp)
    store = EvidenceStore(root / "ledger", "f-1")
    config = load_config(Path("factory.yaml"))
    if cap is not None:
        config.rework.traces_max_bytes = cap
    if count is not None:
        config.rework.traces_max = count
    project = types.SimpleNamespace(
        state=types.SimpleNamespace(trace_dirs=list(trace_dirs)))

    class _Collecting:
        """The collector, and the unpacking it calls, off the real class."""

        _collect_traces = pipeline.Factory._collect_traces
        _unpack_trace = pipeline.Factory._unpack_trace

        def __init__(self, config, project):
            self.config, self.project = config, project

    fake = _Collecting(config, project)
    for i, n in enumerate(earlier):
        store.append("traces", {"files": [f"old-{i}-{k}.zip" for k in range(n)]},
                     role="orchestrator", meta={"round": i})
    before = sum(earlier)
    sandbox = types.SimpleNamespace(path=root / "checkout")
    for rel, body in files.items():
        f = sandbox.path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(body)
    kept = fake._collect_traces(
        store, types.SimpleNamespace(spec_hash="sha256:x"), sandbox, test_path=test_path,
        round_index=round_index, before=before)
    return kept, store, sandbox


def _jpeg_of(size, width, height):
    """A JPEG of the given byte length that declares the given dimensions.

    The blank test reads a frame's size out of its own header rather than
    trusting the trace event, so a stand-in frame needs a real one: start of
    image, then a start-of-frame segment carrying the size, then filler to the
    requested length. Nothing decodes the scan, here or in the player.
    """
    import struct
    head = (b"\xff\xd8\xff\xc0" + struct.pack(">HBHHB", 17, 8, height, width, 3)
            + b"\x01\x11\x00\x02\x11\x01\x03\x11\x01")
    assert size >= len(head), "a frame smaller than its own header is not one"
    return head + b"\x00" * (size - len(head))


def _recording_of(tmp_path, sizes, *, width=1280, height=720, declares=None):
    """A trace archive holding frames of the given byte sizes, in order.

    `declares` is what the trace events claim the page was, when that differs
    from the size of the frames actually shipped -- which is the real case
    Playwright produces whenever it scales the screencast below the viewport.
    """
    import zipfile

    said = declares or (width, height)
    events = [{"type": "context-options", "title": "a recorded test"},
              {"type": "before", "startTime": 0, "title": "Navigate"}]
    for i, size in enumerate(sizes, start=1):
        events.append({"type": "screencast-frame", "file": f"shot{i}.jpeg",
                       "timestamp": float(i), "width": said[0], "height": said[1]})
    archive = tmp_path / f"trace-{len(sizes)}-{sum(sizes)}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("test.trace", "\n".join(json.dumps(e) for e in events))
        for i, size in enumerate(sizes, start=1):
            zf.writestr(f"resources/shot{i}.jpeg", _jpeg_of(size, width, height))
    return archive


#: A flat frame and a drawn one, in bytes, at 1280x720. Both measured off real
#: recordings: 5684 is the empty browser a run opens on, 25225 the first frame
#: with a page in it. At 800x450 -- what Playwright records when a project does
#: not also record video -- the same two are 2459 and 4859, and it is the ratio
#: to the frame's own area that carries across, not either number.
_FLAT, _DRAWN = 5684, 25225


def _turn(seq, name, started, ended, *, round_index=0, detail=""):
    return {"seq": seq, "at": ended, "kind": "step", "role": "orchestrator", "meta": {},
            "payload": {"name": name, "round": round_index, "status": "done", "detail": detail,
                        "started_at": started, "ended_at": ended}}


def _work(seq, kind, at, payload, *, role="orchestrator", meta=None):
    return {"seq": seq, "at": at, "kind": kind, "role": role, "meta": meta or {},
            "payload": payload}


def _check(name, **fields):
    return {"name": name, "command": f"run {name}", "passed": True, "started": True, **fields}


def _resumed_sandbox(tmp_path):
    """A repository, its base commit, and a sandbox that already carries an
    earlier attempt's work -- the state every resume starts from."""
    from factory.sandbox import Sandbox

    repo = _repo_with_one_commit(tmp_path / "kanban", {"api/app.py": "app = 1\n"})
    sandbox = Sandbox.create(repo=repo, root=tmp_path / "sandboxes",
                             project_id="kanban", feature_id="card-labels")
    conftest = sandbox.path / "api/tests/acceptance/card_labels/conftest.py"
    conftest.parent.mkdir(parents=True)
    conftest.write_text('DROP = "DROP SCHEMA public CASCADE"\n')
    sandbox.commit("factory: Card labels")
    return repo, sandbox, conftest


def _helper_error(detail: str = "KeyError: 'OWNER_DATABASE_URL'") -> Finding:
    return Finding(id="blind-helper-1", severity="blocker", category="verification",
                   title="Some independent tests fail inside a helper the oracle wrote",
                   detail=detail, evidence=detail, recommendation="fix the helper")


def _in_container_route(name: str, install: str, command: str):
    from factory.config import RouteConfig
    return RouteConfig(name=name, kind="cli", session_in_container=True,
                       container_install=[install], container_session_command=[command])


def _integration(**over):
    from factory.executors import NO_EDITS
    from factory.schemas import IntegrationReport
    fields = dict(summary="The coding harness edited no file, on 2 attempt(s) (exit 0, exit 0).",
                  files=[], seam_issues=[NO_EDITS],
                  unresolved=["the whole of integration: Close the seams between the units"])
    return IntegrationReport(**{**fields, **over})


def _seam(path="api/tests/test_seam_card_labels.py",
          body="def test_seam(api_client):\n    assert api_client\n"):
    from factory.schemas import FileWrite
    return FileWrite(path=path, contents=body)


def _run_console_js(body: str) -> typing.Any:
    """Run a slice of the console's own code in node and return what it prints."""
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    app = app_js()
    runs = app[app.index("const RUN_STARTS = {"):app.index("function phaseUsage(")]
    steps = app[app.index("/* Stations a run can take"):app.index("function callsIn(")]
    script = ("const state = {};\nconst esc = (s) => String(s);\n" + runs + steps
              + "\n" + body)
    out = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


_LEDGER = [
    {"seq": 1, "kind": "scout", "at": "2026-09-18T14:28:00Z"},
    {"seq": 2, "kind": "plan", "at": "2026-09-18T15:53:00Z"},
    {"seq": 3, "kind": "worker", "at": "2026-09-18T16:01:00Z"},
    {"seq": 4, "kind": "integration", "at": "2026-09-18T16:06:00Z"},
    {"seq": 5, "kind": "oracle", "at": "2026-09-18T16:08:00Z"},
    {"seq": 6, "kind": "packet", "at": "2026-09-18T17:23:00Z"},
    {"seq": 7, "kind": "revalidate", "at": "2026-09-19T03:05:00Z"},
    {"seq": 8, "kind": "step", "at": "2026-09-19T03:05:01Z", "payload": {
        "name": "workers", "status": "done", "round": 2,
        "detail": "reused from the last attempt · 2 unit(s)",
        "started_at": "2026-09-19T03:05:01Z"}},
    {"seq": 9, "kind": "step", "at": "2026-09-19T03:30:00Z", "payload": {
        "name": "gates", "status": "done", "round": 0, "detail": "6/8 passed",
        "started_at": "2026-09-19T03:05:02Z"}},
    {"seq": 10, "kind": "rework_reopened", "at": "2026-09-19T04:02:00Z"},
    {"seq": 11, "kind": "step", "at": "2026-09-19T04:09:00Z", "payload": {
        "name": "gates", "status": "done", "round": 2, "detail": "8/8 passed · round 2",
        "started_at": "2026-09-19T04:09:00Z"}},
    {"seq": 12, "kind": "dispatch", "at": "2026-09-19T05:00:00Z",
     "payload": {"route": "repair"}},
    {"seq": 13, "kind": "step", "at": "2026-09-19T05:01:00Z", "payload": {
        "name": "gates", "status": "running", "round": 0, "detail": "",
        "started_at": "2026-09-19T05:01:00Z"}},
]


async def _carry_once(proxy, dest, send: bytes = b"") -> bytes:
    """One connection through the proxy's listener, arriving as if the kernel
    had redirected it from `dest` -- which is all a test without iptables can
    stand in for."""
    import unittest.mock as mock
    with mock.patch.object(proxy, "original_destination", lambda sock: dest):
        server = await asyncio.start_server(proxy.carry, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            if send:
                writer.write(send)
                await writer.drain()
            got = await asyncio.wait_for(reader.read(), timeout=10)
            writer.close()
            return got


def _dns_query(name: str, qtype: int = 1, ident: int = 0x1234) -> bytes:
    import struct
    labels = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"
    return struct.pack("!HHHHHH", ident, 0x0100, 1, 0, 0, 0) + labels + struct.pack("!HH", qtype, 1)


def _polluted_sort(tmp_path, reset):
    """A gate that fails while the oracle's spec is present, and also whenever
    the shared "database" holds more than the three seeded rows -- which is
    what the spec leaves behind. As the Kanban run had it."""
    from types import SimpleNamespace

    from factory.pipeline import Factory
    from factory.schemas import EnvironmentSpec, Gate, GateReport, GateResult

    spec = "web/e2e/acceptance/f/drag.spec.ts"
    (tmp_path / spec).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / spec).write_text("x\n")
    db = tmp_path / "db.txt"
    db.write_text("seed\n" * 3 + "left by the spec\n" * 2)   # the first run's leftovers

    class Runner:
        carries_setup = True

        async def execute(self, command, *, cwd, timeout_s, network=False):
            if command == "reset":
                db.write_text("seed\n" * 3)
                return gates.Execution(exit_code=0, output="")
            present = (Path(cwd) / spec).exists()
            if present:
                db.write_text(db.read_text() + "left by the spec\n" * 2)
            polluted = len(db.read_text().splitlines()) > 3
            return gates.Execution(exit_code=1 if (present or polluted) else 0, output="")

    environment = EnvironmentSpec(kind="compose", test_prepare=["reset"] if reset else [])
    fake = SimpleNamespace(project=SimpleNamespace(
        judging_gates=[Gate(name="e2e", command="e2e")], environment=environment))
    report = GateReport(results=[GateResult(name="e2e", command="e2e", passed=False, exit_code=1)])
    blind = GateResult(name="blind-tests", named_failing=[spec])
    asyncio.run(Factory._sort_failures(
        fake, report, blind, [spec], SimpleNamespace(path=tmp_path), Runner()))
    return report.results[0]


def _leaky_run(tmp_path, reset, flaky=False):
    """One file that pollutes a shared state file and fails once it is polluted;
    optionally a project reset that clears it."""
    from factory.schemas import TestFileCommand

    state = tmp_path / "db.txt"
    state.write_text("")
    counts: dict[str, int] = {}

    class Runner:
        carries_setup = True

        async def execute(self, command, *, cwd, timeout_s, network=False):
            if command == "reset":
                state.write_text("")
                return gates.Execution(exit_code=0, output="")
            counts[command] = counts.get(command, 0) + 1
            if flaky:
                return gates.Execution(exit_code=0 if counts[command] == 1 else 1, output="flaked")
            dirty = bool(state.read_text())
            state.write_text(state.read_text() + "list\n")
            return gates.Execution(exit_code=1 if dirty else 0, output="7 lists, expected 3")

    result, _ = asyncio.run(gates.run_per_file(
        [TestFileCommand(match="*", command="run {path}")], ["e2e/drag.spec.ts"], tmp_path,
        Runner(), prepare=["reset"] if reset else [], repeat=True))
    return result


def onboarding_module():
    from factory import onboarding
    return onboarding


# ==========================================================================
# a level that can't run tests cleanly is not tested; a person checks it
# ==========================================================================


def _levels_surface(**by_level):
    """A reading with one tier per level: `absent`, or a cleanup answer."""
    from factory.schemas import TestingSurface, TestingTier
    tiers = []
    for level, how in by_level.items():
        if how == "absent":
            tiers.append(TestingTier(tier=level, verdict="absent"))
        else:
            tiers.append(TestingTier(tier=level, verdict="usable", runner="r",
                                     cleanup=None if how is None else ("" if how == "nobody" else "x"),
                                     cleanup_by=how if how not in (None,) else None))
    return TestingSurface(tiers=tiers)


class _State:
    def __init__(self, surface, applied=None):
        self.testing = surface
        self.cleanup_applied = applied or {}


# -- a check whose tool fetches only when it runs ------------------------------

class _LazyFetchRunner:
    """A Maven in miniature: its check needs something it downloads the first
    time a test actually runs, which a setup that skips tests never fetches.
    Offline without it, the check dies on the name it cannot resolve. It also
    writes a row into the stack's database whenever it runs, and fails on a
    database that already has one -- Library's `BooksApiIT` expects an empty library."""

    def __init__(self, *, fetch_fixes: bool = True):
        self.log: list[str] = []
        self.fetched = False
        self.rows = 0
        self.fetch_fixes = fetch_fixes
        self._session_error = ""
        self.image = "img"

    async def open_session(self, cwd, harden=True):
        self.log.append("open_session")

    async def close_session(self):
        self.log.append("close_session")

    async def discard_session(self):
        self.log.append("discard_session")

    async def reset_stack(self):
        self.log.append("reset_stack")
        self.rows = 0

    async def execute(self, command, *, cwd, timeout_s, network=True):
        from factory.gates import Execution
        self.log.append(f"{'online' if network else 'offline'}:{command}")
        if command != "mvn verify":
            return Execution(exit_code=0, output="")
        if not self.fetched:
            if network:
                self.fetched = self.fetch_fixes
            else:
                return Execution(exit_code=1, output=(
                    "Could not transfer artifact surefire-junit-platform from/to central: "
                    "Unknown host repo.maven.apache.org: Temporary failure in name resolution"))
        self.rows += 1
        if self.rows > 1:
            return Execution(exit_code=1, output="expected 2 books, found 4")
        return Execution(exit_code=0, output="Tests run: 7, Failures: 0")


def _warm_project(tmp_path, runner):
    import subprocess
    from factory.config import Config, PathsConfig, RoleConfig
    from factory.onboarding import ProjectOnboarding
    from factory.projects import ProjectRegistry
    from factory.schemas import EnvironmentSpec, Gate

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pom.xml").write_text("<project/>\n")
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "."],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=repo, check=True)
    config = Config(
        roles={"surveyor": RoleConfig(name="surveyor", model="x/y")},
        paths=PathsConfig(evidence=str(tmp_path / "e"), sandboxes=str(tmp_path / "s")),
    )
    registry = ProjectRegistry(config.evidence_path)
    project = registry.create(repo, "library")
    project.state.gates = [Gate(name="api-verify", command="mvn verify"),
                           Gate(name="web-test", command="echo web")]
    project.state.environment = EnvironmentSpec(kind="reuse", image="img",
                                                setup=["mvn -DskipTests verify"])
    registry.save(project)
    return ProjectOnboarding(config, registry, runner=runner), project


# -- the Dockerfile lives in the repository ------------------------------------


def _dockerfile_project(tmp_path, text="FROM python:3.12\n"):
    import subprocess
    from factory.projects import ProjectRegistry
    from factory.schemas import EnvironmentSpec

    repo = _git_repo(tmp_path / "repo")
    for key, value in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "config", key, value], cwd=repo, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, check=True)
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")
    project.state.base_ref = "main"
    project.state.environment = EnvironmentSpec(kind="generate", dockerfile=text)
    registry.save(project)
    return registry, repo



# -- history: the project's ledger, read ---------------------------------------


def _ledger(*records):
    return [{"seq": i + 1, "at": f"2026-09-{17 + i // 10:02d}T{i % 10:02d}:00:00+00:00", **r}
            for i, r in enumerate(records)]


# -- the project's own Dockerfile, when it has a stage that runs the checks ----

_OWN_DOCKERFILE = """FROM python:3.12-slim AS test
RUN pip install ruff
WORKDIR /workspace

FROM python:3.12-slim AS app
WORKDIR /app
COPY . .
CMD ["uvicorn", "app.main:app"]
"""


def _own_dockerfile_project(tmp_path):
    import subprocess
    from factory.projects import REPO_DOCKERFILE
    from factory.schemas import EnvironmentSpec

    registry, repo = _dockerfile_project(tmp_path)
    registry.move_dockerfile_into_repo(registry.get("demo"))      # Fabrika's own, first
    (repo / "api").mkdir()
    (repo / "api" / "Dockerfile").write_text(_OWN_DOCKERFILE)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "a test stage"], cwd=repo, check=True)
    project = registry.get("demo")
    project.state.environment = EnvironmentSpec(
        kind="reuse", dockerfile_path="api/Dockerfile", dockerfile_target="test", build_context="api")
    registry.save(project)
    return registry, repo, REPO_DOCKERFILE


def containers_module():
    from factory import containers
    return containers


# ==========================================================================
# Seeing the running app: a preview at review
# ==========================================================================
#
# The suite has no Docker, so the door's
# containers are tested as arguments and against a stand-in `docker`, and the
# preview itself runs on this machine through `LocalRunner`, which is what the
# suite's other session tests do.


def _web_service(name: str = "web"):
    """A real server on the allocated port, and a probe that really asks it."""
    key = name.upper()
    return schemas.Service(
        name=name,
        command=f"{sys.executable} -m http.server $FACTORY_PORT_{key} --bind 127.0.0.1",
        ready_when=(f"{sys.executable} -c \"import os,urllib.request; urllib.request.urlopen("
                    f"'http://127.0.0.1:'+os.environ['FACTORY_PORT_{key}'], timeout=2)\""),
        ready_timeout_s=20.0,
    )


def _previewable(tmp_path, path: str = "/"):
    """A registered project whose environment says what a person opens."""
    from factory.projects import ProjectRegistry

    config, project = _demo_project(tmp_path)
    project.state.environment = schemas.EnvironmentSpec(
        kind="host", preview=schemas.Preview(open="web", path=path, services=[_web_service()]))
    registry = ProjectRegistry(config.evidence_path)
    registry.save(project, note="test fixture")
    return config, registry, registry.get(project.id)


def _stand_in_docker(tmp_path, answers: dict[str, str]):
    """A `docker` that logs every call and answers by the first words it matches."""
    log = tmp_path / "docker.log"
    table = json.dumps(answers)
    script = tmp_path / "docker"
    script.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json, sys
        args = " ".join(sys.argv[1:])
        open({str(log)!r}, "a").write(args + "\\n")
        for prefix, (code, out) in json.loads({table!r}).items():
            if args.startswith(prefix):
                sys.stdout.write(out)
                sys.exit(code)
        sys.exit(0)
    """))
    script.chmod(0o755)
    return str(script), log


# ===========================================================================
# as-built -- the codebase as it was built, read file by file and checked
#
# A reader reports what each file contains in any language; code joins the
# reports and checks every claim it can. These hold the properties that make
# the result trustworthy without a person ruling on it: nothing the reader
# says becomes structure unchecked, every file is placed or named, the groups
# are the same on every reading, and the whole thing stays out of the verify
# lane.
# ===========================================================================

import subprocess

from factory import asbuilt, asbuilt_graph, asbuilt_pages
from factory.schemas import (Actor, ArchitectureAccount, BatchedFileRecord, CapabilityGroup, CapabilityMap,
                             DismissedMention, FileRecord, FileRecordBatch, GroupNaming, NamedGroup,
                             OutsideSystemGroup, RuntimeGroup, StartHere, SystemAccount)


def _ab_repo(tmp_path, files):
    root = tmp_path / "repo"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
                 ["add", "-A"], ["commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root


def _ab_commit(root, message="change"):
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=root, check=True, capture_output=True)


_AB_FILES = {
    "api/server.py": "from .store import save_card, load_card\n\n@app.post('/api/cards')\ndef create_card(body):\n"
                     "    return save_card(body)\n\n@app.get('/api/cards/{card_id}')\ndef get_card(card_id):\n"
                     "    return load_card(card_id)\n\n@app.get('/health')\ndef health():\n    return 'ok'\n",
    "api/store.py": "import sqlite3\n\ndef save_card(body):\n    return _db().insert(body)\n\ndef load_card(card_id):\n"
                    "    return _db().get(card_id)\n\ndef _db():\n    return sqlite3.connect('x')\n",
    "web/app.js": "import { render } from './view.js';\nconst cardUrl = (id) => `/api/cards/${id}`;\n"
                  "export async function openCard(id) {\n  render(await (await fetch(cardUrl(id))).json());\n}\n"
                  "export async function newCard(b) { return fetch('/api/cards', {method: 'POST'}); }\n",
    "web/view.js": "export function render(card) {\n  document.body.textContent = card.title;\n}\n",
    "tests/test_api.py": "from api.server import create_card\n\ndef test_create():\n    assert create_card({})\n",
}

_AB_RECORDS = {
    "api/server.py": dict(purpose="The HTTP API for cards.", imports=["api/store.py"], functions=[
        dict(name="create_card", line=4, calls=["save_card"]), dict(name="get_card", line=8, calls=["load_card"]),
        dict(name="health", line=12)],
        ways_in=[dict(kind="route", name="/api/cards", method="POST", handler="create_card"),
                 dict(kind="route", name="/api/cards/{card_id}", method="GET", handler="get_card"),
                 dict(kind="route", name="/health", method="GET", handler="health")]),
    "api/store.py": dict(purpose="Stores cards.", functions=[
        dict(name="save_card", line=3, calls=["_db"]), dict(name="load_card", line=6, calls=["_db"]),
        dict(name="_db", line=9)], entities_written=["cards"], outside=["SQLite"]),
    "web/app.js": dict(purpose="Opens and creates cards.", imports=["web/view.js"], functions=[
        dict(name="openCard", line=3, calls=["render"]), dict(name="newCard", line=6)],
        routes_called=[dict(path="/api/cards/{id}", method="GET"), dict(path="/api/cards", method="POST")]),
    "web/view.js": dict(purpose="Draws a card.", functions=[dict(name="render", line=1)]),
    "tests/test_api.py": dict(purpose="Tests creating a card.", is_test=True, imports=["api/server.py"],
                              functions=[dict(name="test_create", line=3, calls=["create_card"])]),
}


class _AbStub:
    """Answers the reader from a table and the cartographer from its prompt."""

    def __init__(self, records=None, capmap=None, architecture=None):
        self.records = records or _AB_RECORDS
        self.capmap = capmap
        self.architecture = architecture
        self.asked: list[tuple[str, str]] = []

    async def ask(self, role, prompt, schema, system=""):
        from factory.llm import RateLimited
        if role == "reader" and getattr(self, "limit_after", None) is not None:
            if sum(1 for r, _ in self.asked if r == "reader") >= self.limit_after:
                raise RateLimited("route 'claude-code' has reached its plan's limit", route="claude-code",
                                  retry_after_s=600)
        self.asked.append((role, prompt))
        if schema is FileRecord:
            path = re.search(r"# File\n\n(\S+)", prompt).group(1)
            return FileRecord.model_validate(self.records[path])
        if schema is FileRecordBatch:
            paths = re.findall(r"^# (\S+), numbered -- the whole file", prompt, re.M)
            self.batches = getattr(self, "batches", []) + [paths]
            thin = getattr(self, "thin", set())
            return FileRecordBatch(files=[BatchedFileRecord(path=p, record=FileRecord.model_validate(
                                              {**self.records[p], "functions": self.records[p].get("functions", [])[:1]} if p in thin
                                              else self.records[p]))
                                          for p in paths if p not in getattr(self, "drop", set())]
                                   + [BatchedFileRecord(path="invented.py", record=FileRecord(purpose="not asked for"))])
        if schema is GroupNaming:
            keys = re.findall(r"## key: (\S+)", prompt)
            return GroupNaming(groups=[NamedGroup(key=k, name=f"Group {i}", summary="s") for i, k in enumerate(keys)])
        if schema is ArchitectureAccount:
            self.architecture_prompts = getattr(self, "architecture_prompts", 0) + 1
            if self.architecture is not None:
                return self.architecture
            keys = re.findall(r"## key: (\S+)", prompt)
            return ArchitectureAccount(runtimes=[RuntimeGroup(name="Server", kind="server", subsystems=keys)])
        if schema is CapabilityMap:
            if self.capmap is not None:
                return self.capmap
            ids = re.findall(r"- id: (.+?) -- in", prompt)
            return CapabilityMap(capabilities=[CapabilityGroup(name="Work with cards", summary="s", area="Cards",
                                                               ways_in=[i for i in ids if "cards" in i])],
                                 not_capabilities=[i for i in ids if "health" in i])
        return SystemAccount(summary="A card app.", start_reading=[StartHere(path="api/server.py", why="entry"),
                                                                   StartHere(path="nowhere.py", why="invented")])


def _ab_read(root, stub=None, **kw):
    config = load_config(ROOT / "factory.example.yaml")
    reader = asbuilt.AsBuiltReader(config, stub or _AbStub(), cache=kw.pop("cache", None))
    return asyncio.run(reader.read(root, "HEAD", **kw))


# -- why a check is red ----------------------------------------------------------

_PYPROJECT = '[tool.coverage.report]\nfail_under = 95\n'


def _floor_gate(**over):
    from factory.schemas import Gate, OwnFloor

    return Gate(name="api-tests", command="pytest", family="tests", parse_metric=r"(\d+\.?\d*)%",
                own_floor=OwnFloor(where="api/pyproject.toml#tool.coverage.report.fail_under"), **over)


def _session():
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    @asynccontextmanager
    async def session():
        yield SimpleNamespace(ready=True, report="")
    return session


def _diagnosed_project(tmp_path):
    import subprocess

    from factory.projects import ProjectRegistry
    from factory.schemas import Diagnosis, DiagnosisFix, GateReport, GateResult

    repo = _repo_with_one_commit(tmp_path / "repo", {"api/pyproject.toml": _PYPROJECT})
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                         text=True).stdout.strip()
    registry = ProjectRegistry(tmp_path / "evidence")
    project = registry.create(repo, "demo")
    project.state.gates = [_floor_gate()]
    project.state.baseline_sha = sha
    project.state.baseline = GateReport(results=[GateResult(
        name="api-tests", exit_code=1, metric=78.0, red_kind="under_own_floor", diagnosis="fp1")])
    project = registry.save(project)
    fixed = _PYPROJECT.replace("[tool.coverage.report]", '[tool.coverage.run]\nconcurrency = ["greenlet"]\n\n'
                               "[tool.coverage.report]")
    project.store.append("diagnosis", Diagnosis(
        check="api-tests", fingerprint="fp1", cause="c", where="measurement", confirmed=True,
        fixes=[DiagnosisFix(kind="repo_change", title="count greenlets", path="api/pyproject.toml",
                            contents=fixed, commit_message="Count greenlets")]).model_dump(mode="json"),
        role="diagnose")
    return registry, registry.get(project.id), repo, fixed


# -- guides: the repository's own files, delivered by its harness or by code -------


def _guide_repo(tmp_path, extra=None):
    files = {
        "AGENTS.md": ("# Agents\n\n## Code style\n\nUse ruff.\n\n## Testing\n\n"
                      "See [the testing guide](docs/testing.md).\n"),
        "server/AGENTS.md": "# Server agents\n\nNo ORM in routers.\n",
        "CLAUDE.md": "@AGENTS.md\n",
        "CONTRIBUTING.md": "# Contributing\n\n## Style\nFour spaces.\n",
        "docs/testing.md": "# Testing\n\nEvery test owns its data.\n",
        "DESIGN.md": "---\nname: Demo\ncolors:\n  primary: \"#123456\"\n---\n\n## Overview\n\nCalm.\n",
        "web/DESIGN.md": "# not at the root\n",
        ".claude/skills/migrate/SKILL.md":
            "---\nname: migrate\ndescription: Use when adding a migration\n---\nStep one.\n",
        ".agents/skills/release/SKILL.md": "---\nname: release\ndescription: Cut a release\n---\nTag.\n",
        "node_modules/pkg/AGENTS.md": "# someone else's\n",
        "README.md": "# Kanban\n",
        "server/app.py": "x = 1\n",
    }
    files.update(extra or {})
    return _repo_with_one_commit(tmp_path / "repo", files)


def _git_status(repo):
    import subprocess
    return subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo,
                          capture_output=True, text=True).stdout.split("\n")


__all__ = [
    '_FLAT',
    '_DRAWN',
    'annotations',
    'ast',
    'asyncio',
    'datetime',
    'inspect',
    'os',
    'pathlib',
    'json',
    're',
    'sys',
    'tempfile',
    'time',
    'textwrap',
    'types',
    'typing',
    'Path',
    'PurePosixPath',
    'pytest',
    'executors',
    'gates',
    'llm',
    'onboarding',
    'pipeline',
    'schemas',
    'server',
    'store',
    'unitenv',
    'workspace',
    'pipeline_projects_module',
    'config_module',
    'agentbox_module',
    'routes_mod',
    '_global_harness',
    'Config',
    'RoleConfig',
    'load_config',
    'LLM',
    'LLMError',
    'FindingLedger',
    'compute_rework',
    'compute_trace',
    'compute_unclaimed',
    'human_flags',
    'merge_reviews',
    'restore_packet',
    'seed_flags',
    'AcceptanceCriterion',
    'ArbiterReport',
    'EnvironmentSpec',
    'Gate',
    'BreakerReport',
    'BreakerSuite',
    'BreakerTest',
    'Decision',
    'FileWrite',
    'Finding',
    'HumanFlag',
    'FindingDisposition',
    'FindingRecord',
    'OracleSuite',
    'Packet',
    'RepairVerdict',
    'ReviewReport',
    'ReworkSummary',
    'SelfDisclosure',
    'Spec',
    'SupportFile',
    'OracleTestFile',
    'TraceRow',
    'WorkerOutput',
    'EvidenceStore',
    'app_js',
    'class_source',
    'factory_files',
    'factory_source',
    'stylesheet',
    'suite_source',
    'ROOT',
    'PACKAGE',
    'strip_prose',
    'code_without_prose',
    '_worker',
    '_repo_with_one_commit',
    '_env_record',
    '_headroom',
    '_sched_cfg',
    '_seed_gauge',
    '_ledger_with',
    'CODEX_RATE_LIMITS',
    '_codex_route',
    '_stub_config',
    '_response',
    '_demo_project',
    'bare_app',
    'gate_two_factory',
    'editable_config',
    '_docker_config',
    'provider_app',
    '_repo_of',
    '_survey_of_a_compose_project',
    '_finding',
    '_reported',
    '_one_file_many_criteria',
    '_RecordingRunner',
    '_reading',
    '_js_list',
    '_js_object_keys',
    '_record_kinds',
    '_feature_record_kinds',
    '_pipeline_roles',
    '_project_roles',
    '_prompt_files',
    '_result',
    '_SessionRunner',
    '_rule',
    '_service',
    '_surface',
    '_PlacementRunner',
    '_placement',
    '_gate',
    '_tier',
    '_tier_surface',
    '_ReportingRunner',
    '_reporting_state',
    '_git_repo',
    '_commit',
    '_project_with_gates',
    'SURVEYOR',
    '_prose',
    '_config_command_strings',
    '_harness_repo',
    '_css_rules',
    '_long_pytest_output',
    '_pl',
    '_unit_output',
    '_plan',
    '_harness_for',
    '_migration_repo',
    '_driver',
    '_tiny_repo',
    '_branch',
    '_checks_with_settings',
    '_project_with_suggestions',
    '_sarif',
    '_StubLookup',
    '_project_with_proposal',
    '_project_with_parts',
    '_spec12',
    '_proved_placement',
    '_approved_project',
    '_authored',
    '_blank_repo',
    'GAUGE_LINE',
    '_session_stream',
    '_streamed_session_route',
    'FAKE_CLI',
    '_cli_route',
    '_routed',
    'FAKE_JSONL_CLI',
    '_jsonl_route',
    '_route_cfg',
    'ENVELOPE',
    '_metered_route',
    'DF_HEADER',
    '_reopen',
    '_container_route',
    '_Prepared',
    '_executor',
    '_shipped_container_routes',
    '_ProbeRunner',
    '_probe',
    '_promote',
    '_probe_round',
    '_ledger_of_agents',
    '_trace_run',
    '_jpeg_of',
    '_recording_of',
    '_turn',
    '_work',
    '_check',
    '_resumed_sandbox',
    '_helper_error',
    '_in_container_route',
    '_integration',
    '_seam',
    '_run_console_js',
    '_LEDGER',
    '_carry_once',
    '_dns_query',
    '_polluted_sort',
    '_leaky_run',
    'onboarding_module',
    '_levels_surface',
    '_State',
    '_LazyFetchRunner',
    '_warm_project',
    '_dockerfile_project',
    '_ledger',
    '_OWN_DOCKERFILE',
    '_own_dockerfile_project',
    'containers_module',
    '_web_service',
    '_previewable',
    '_stand_in_docker',
    'subprocess',
    'asbuilt',
    'asbuilt_graph',
    'asbuilt_pages',
    'Actor',
    'ArchitectureAccount',
    'BatchedFileRecord',
    'CapabilityGroup',
    'CapabilityMap',
    'DismissedMention',
    'FileRecord',
    'FileRecordBatch',
    'GroupNaming',
    'NamedGroup',
    'OutsideSystemGroup',
    'RuntimeGroup',
    'StartHere',
    'SystemAccount',
    '_ab_repo',
    '_ab_commit',
    '_AB_FILES',
    '_AB_RECORDS',
    '_AbStub',
    '_ab_read',
    '_PYPROJECT',
    '_floor_gate',
    '_session',
    '_diagnosed_project',
    '_guide_repo',
    '_git_status',
]
