"""Finding the command that makes a test runner say which test failed.

A test-file rule has two forms. `command` runs one file and is judged by its
exit code; `report` runs the same file and writes JUnit XML, so each test in it
answers for itself. Without the second, every criterion tagged to a file shares
that file's one verdict -- and when a project runs one of its runners through a
reporter the per-test join needs, the rest come back `unknown` however well
their tests ran. One project's web tests did exactly that: the survey left `report`
empty for an Angular rule whose runner can write JUnit perfectly well.

The survey could not have known. It is one structured answer over a digest of
the repository, and nothing in a digest says which flags a runner takes. So
this does not teach Fabrika any runner's flags either. It asks an agent,
standing in the project's own container with the project's dependencies
installed, to find out from the runner itself -- its help, its configuration
schema, its installed documentation -- and then it proves the answer the way
every placement is already proved: the canary is run with the proposed
command, and the report it writes has to name the canary's test.

Nothing here applies anything. A proven command becomes a replacement set of
rules on the re-survey's diff, and a person accepts it or does not.
"""

from __future__ import annotations

import json
import subprocess
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .config import Config, ConfigError, session_fallback
from .gates import probe_placement, rule_for
from .schemas import BlindPlacement, TestFileCommand

#: The role that looks. Its contract is `roles/reporter.md`.
ROLE = "reporter"
#: Where it writes its answer, inside the throwaway checkout. The only path the
#: session's writes are read from; anything it writes elsewhere is dropped.
ANSWER_DIR = ".factory-reporting"
ANSWER_FILE = f"{ANSWER_DIR}/answer.json"
#: The checkout and the stack it looks in, named for what they are rather than
#: for any feature, like the baseline's.
LABEL = "_reporting"
#: What a search concluded about one rule, recorded so a re-survey does not pay
#: for the same answer twice.
LEDGER_KIND = "report_search"


@dataclass
class Candidate:
    """One rule with no reporting command, and the placement that proves it."""

    rule: TestFileCommand
    placement: BlindPlacement
    path: str


@dataclass
class Outcome:
    """What one rule's search came to."""

    match: str
    command: str
    outcome: str                       # verified | none | unproven | error
    report: str = ""
    why: str = ""
    evidence: str = ""


@dataclass
class Search:
    """The rules as they would be with every proven command filled in, and why."""

    rules: list[TestFileCommand] = field(default_factory=list)
    reason: str = ""
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def proposes(self) -> bool:
        return any(o.outcome == "verified" for o in self.outcomes)


def _key(rule: TestFileCommand) -> tuple[str, str]:
    return (rule.match or "*", (rule.command or "").strip())


def candidates(state: Any) -> list[Candidate]:
    """Rules with no `report` that a placement can prove a command for.

    A placement is what makes the answer checkable: it carries a canary whose
    one test has a declared name, and the proof is that the report names it.
    A rule no placement reaches is left alone -- a command nobody can prove is
    a claim, and claims are what this system is built not to take.
    """
    rules = list(getattr(state, "test_file_commands", None) or ())
    out: list[Candidate] = []
    seen: set[tuple[str, str]] = set()
    for placement in getattr(state, "blind_placements", None) or ():
        if not (placement.canary_passes or "").strip() or not (placement.canary_case or "").strip():
            continue
        path = f"{(placement.directory or '').strip('/')}/{(placement.filename or '').strip('/')}"
        path = path.strip("/")
        rule = rule_for(rules, path)
        if rule is None or (rule.report or "").strip() or _key(rule) in seen:
            continue
        seen.add(_key(rule))
        out.append(Candidate(rule=rule, placement=placement, path=path))
    return out


def earlier_answers(records: Sequence[dict]) -> dict[tuple[str, str], Outcome]:
    """What previous searches concluded, by rule, the latest winning.

    A proven command is offered again without asking anyone; a runner an agent
    said cannot write JUnit is not asked again until its rule changes. An
    error -- the stack would not start, the session died -- settles nothing,
    so it is not remembered.
    """
    out: dict[tuple[str, str], Outcome] = {}
    for record in records:
        if record.get("kind") != LEDGER_KIND:
            continue
        for raw in (record.get("payload") or {}).get("rules") or []:
            if raw.get("outcome") not in ("verified", "none"):
                continue
            key = (raw.get("match") or "*", (raw.get("command") or "").strip())
            out[key] = Outcome(**{k: raw.get(k, "") for k in Outcome.__dataclass_fields__})
    return out


def brief(found: Sequence[Candidate]) -> str:
    """The task, as data. What the role is and how it must behave is its prompt."""
    sections = []
    for c in found:
        sections.append(
            f"## Files matching `{c.rule.match or '*'}`\n\n"
            f"The command that runs one test file, with `{{path}}` where the file goes:\n\n"
            f"```\n{c.rule.command}\n```\n\n"
            f"A test file that is known to work, to prove your answer with. Write it to "
            f"`{c.path}`, and remove it when you are done:\n\n"
            f"```\n{c.placement.canary_passes}\n```\n\n"
            f"Its one test is declared as `{c.placement.canary_case}`. A report that proves "
            f"your command names that test.")
    return (
        "# Rules that cannot say which test failed\n\n"
        "Each rule below runs one test file and is judged only by its exit code. Find, for "
        "each, the command that runs the same file the same way and also writes a JUnit XML "
        "report to a path given as `{report}`.\n\n"
        + "\n\n".join(sections)
        + "\n\n# Your answer\n\n"
        f"Write `{ANSWER_FILE}` and nothing else outside the test files you remove again:\n\n"
        "```json\n"
        '{"rules": [{"match": "<the rule\'s match, exactly>", '
        '"report": "<the command, with {path} and {report}>", '
        '"how_found": "<what the runner told you, and what you ran to prove it>"}],\n'
        ' "could_not": [{"match": "<match>", "why": "<what you tried and what it said>"}]}\n'
        "```\n")


def read_answer(files: Sequence[Any]) -> dict[str, Any]:
    """The answer file, parsed; empty when it is missing or not JSON."""
    for f in files:
        if getattr(f, "path", "") == ANSWER_FILE:
            try:
                data = json.loads(f.contents)
            except (TypeError, ValueError):
                return {}
            return data if isinstance(data, dict) else {}
    return {}


def usable_report(report: str, command: str) -> str:
    """Why a proposed reporting command cannot be taken, or empty."""
    text = (report or "").strip()
    if not text:
        return "no command was given"
    if "{path}" not in text:
        return "it has no `{path}`, so it would not run the one file it is given"
    if "{report}" not in text:
        return "it has no `{report}`, so nothing says where the report goes"
    if text == (command or "").strip():
        return "it is the plain command unchanged"
    return ""


def reset_tree(tree: Path) -> None:
    """Put the checkout back the way the commit has it, before the proof.

    The agent could run anything in there, and a report command that works
    only because it edited the runner's configuration would pass the proof and
    fail in the repository it is offered to. Tracked files go back; files it
    added go, except what is ignored -- which is where setup put the project's
    dependencies, and the proof needs those.
    """
    for argv in (["git", "checkout", "--", "."], ["git", "clean", "-fdq"]):
        subprocess.run(argv, cwd=tree, capture_output=True, timeout=120)


def reason(proven: Sequence[Outcome]) -> str:
    """The card's words: one plain sentence, then the evidence."""
    lines = [
        "Lets each test report its own result, so one failing test stops marking every "
        "requirement in its file as failed.",
        "",
        "Found by asking each runner how it writes JUnit XML, then proved on this "
        "project's canary test in its own container: the report named that test.",
    ]
    for o in proven:
        lines += ["", f"`{o.match}`: `{o.report}`"]
        if o.evidence:
            lines.append(o.evidence)
    return "\n".join(lines)


async def search(project: Any, config: Config, llm: Any, *, store: Any = None) -> Search | None:
    """Look for, prove, and return reporting commands. None when there is nothing to do.

    Every outcome is recorded on the project's ledger, including the ones that
    came to nothing, because a re-survey that quietly found no command reads
    exactly like one that never looked.
    """
    found = candidates(project.state)
    if not found:
        return None
    remembered = earlier_answers(list(store or project.store))
    outcomes: list[Outcome] = []
    to_ask: list[Candidate] = []
    for c in found:
        earlier = remembered.get(_key(c.rule))
        if earlier is None:
            to_ask.append(c)
        elif earlier.outcome == "verified":
            outcomes.append(earlier)
    if to_ask:
        outcomes += await _ask_and_prove(project, config, llm, to_ask)
        (store or project.store).append(LEDGER_KIND, {
            "rules": [o.__dict__ for o in outcomes if (o.match, o.command) in
                      {_key(c.rule) for c in to_ask}],
        }, role="orchestrator")

    proven = {(o.match, o.command): o for o in outcomes if o.outcome == "verified"}
    rules = [
        r.model_copy(update={"report": proven[_key(r)].report}) if _key(r) in proven else r
        for r in project.state.test_file_commands
    ]
    return Search(rules=rules, reason=reason(list(proven.values())) if proven else "",
                  outcomes=outcomes)


async def _ask_and_prove(project: Any, config: Config, llm: Any,
                         found: Sequence[Candidate]) -> list[Outcome]:
    from .executors import CommandExecutor
    from .sandbox import Sandbox
    from .unitenv import unit_environment

    def failed(why: str) -> list[Outcome]:
        return [Outcome(match=c.rule.match or "*", command=c.rule.command,
                        outcome="error", why=why) for c in found]

    spec = project.environment
    if spec is None or spec.kind == "host":
        return failed("this project has no container environment to look in")
    try:
        route = config.route_for(ROLE)
        system = config.role_prompt(ROLE)
    except ConfigError as exc:
        return failed(str(exc))

    sandbox = Sandbox.create(
        repo=project.repo_path, root=config.sandbox_path, project_id=project.id,
        feature_id=LABEL, base_ref=project.base_ref)
    tree = Path(sandbox.path)
    try:
        async with unit_environment(
                project, config, label=LABEL, tree=tree,
                harness_route=route, fallback_route=session_fallback(config, ROLE)) as env:
            if not env.ready or env.runner is None:
                return failed(env.problem or "the project's environment did not come up")

            @asynccontextmanager
            async def this_environment(_tree: Path):
                yield env

            try:
                authored = await CommandExecutor(llm, config).author(
                    brief=brief(found), tree=tree, role=ROLE, confine=ANSWER_DIR,
                    system=system, environment=this_environment)
            except Exception as exc:                        # noqa: BLE001
                return failed(f"{type(exc).__name__}: {exc}")
            answer = read_answer(authored.files)
            proposed = {str(r.get("match") or "*"): r for r in answer.get("rules") or []
                        if isinstance(r, dict)}
            declined = {str(r.get("match") or "*"): str(r.get("why") or "")
                        for r in answer.get("could_not") or [] if isinstance(r, dict)}

            reset_tree(tree)
            out: list[Outcome] = []
            for c in found:
                match = c.rule.match or "*"
                if match not in proposed:
                    # Said it cannot, which is remembered; or said nothing,
                    # which is not -- a session that died is no answer about
                    # the runner, and must not stop the next reading asking.
                    out.append(Outcome(
                        match=match, command=c.rule.command,
                        outcome="none" if match in declined else "error",
                        why=declined.get(match) or "the agent gave no answer for this rule"))
                    continue
                given = proposed[match]
                report = str(given.get("report") or "").strip()
                problem = usable_report(report, c.rule.command)
                if problem:
                    out.append(Outcome(
                        match=match, command=c.rule.command, outcome="unproven",
                        report=report, why=f"the answer could not be used: {problem}"))
                    continue
                trial = c.rule.model_copy(update={"report": report})
                rules = [trial if _key(r) == _key(c.rule) else r
                         for r in project.state.test_file_commands]
                measured = await probe_placement(c.placement, rules, tree, env.runner)
                if measured.case_named_as_declared:
                    out.append(Outcome(
                        match=match, command=c.rule.command, outcome="verified", report=report,
                        evidence=str(given.get("how_found") or "")[:600]))
                else:
                    out.append(Outcome(
                        match=match, command=c.rule.command, outcome="unproven", report=report,
                        why=("the proposed command did not produce a report naming the canary's "
                             f"test (it named: {measured.reported_case or 'nothing readable'})"),
                        evidence=(measured.evidence or "")[-1500:]))
            return out
    finally:
        sandbox.release(keep_branch=False)
