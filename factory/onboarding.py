"""Gate 0: registering a project.

Someone points the factory at a directory. A surveyor reads it and proposes the
gates and the environment those gates need. The environment gets built, the
gates run on an untouched checkout, and only then does a human approve.

The baseline is the point of this phase, but not because it must be green. A
red gate does not make every later failure unattributable: a failing gate is
re-run at the commit each feature branched from, so a red gate is attributable
without ever having been green.

What the baseline proves is narrower and more useful -- that these gates
*produce information*. A gate that runs and reports 129 lint errors is a working
gate on a repository that has never been linted. A gate whose tool is not in the
environment is red forever, reports on nothing, and looks like coverage; that is
what `approve` refuses. The distinction lives in `gates.gate_outcome`, which
returns `unknown` rather than guessing, because blocking on a guess is as wrong
here as waving one through.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from .config import Config
from .containers import low_disk, runner_for
from .config import GateConfig
from . import dependencies, diagnosis, reporting
from .gates import (
    NAME_UNRESOLVED,
    Runner,
    probe_empty_selection,
    probe_placement,
    probe_testing,
    restore_tracked,
    run_gate,
    run_gates,
    run_setup,
    test_session,
    tracked_changes,
)
from .workspace import installable_names, parse_package_lines
from .llm import LLM
from .projects import (cleanup_option_problems, unmet_capabilities, Project, ProjectError,
                       ProjectRegistry)
from .preview import measure as measure_preview
from .sandbox import Sandbox
from .schemas import (
    CapabilityRequest,
    DiagnosisAnswer,
    GateReport,
    GateResult,
    PlacementResult,
    Recommendation,
    ProjectSurvey,
    SurveyDiff,
    TestFileCommand,
)
from .workspace import launched_scripts, read_paths, repo_digest, testing_paths, tooling_paths

BASELINE_ID = "_baseline"
ProgressCallback = Callable[[dict[str, Any]], None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


#: What a reading writes afresh every time in its own words, and what it means
#: to have changed. Compared whole, the prose makes every reading a change;
#: left out, a decision goes missing. See `tier_shape`.
TIER_PROSE = frozenset({"note", "canary", "canary_filename", "import_examples",
                        "cleanup", "cleanup_examples", "cleanup_summary", "cleanup_options"})


def tier_shape(tier) -> dict:
    """One test level, reduced to what a human would be ruling on.

    `runner` is prose with a name in it -- "vitest 2.1 under jsdom" one reading
    and "vitest (web/vite.config.ts), jsdom" the next -- so the tool it opens
    with is the identity. A setup file is its path: the contents are read off
    the repository each time and a re-read that reflows a comment is not a
    change to what a test author can use.
    """
    d = tier.model_dump(mode="json")
    runner = (d.get("runner") or "").strip()
    d["runner"] = runner.split()[0].strip("`,.:;()").lower() if runner else ""
    d["setup_files"] = sorted(f.get("path", "") for f in d.get("setup_files") or [])
    d["fixtures"] = sorted(d.get("fixtures") or [])
    d["run_by"] = sorted(d.get("run_by") or [])
    for name in TIER_PROSE:
        d.pop(name, None)
    # The wording of `cleanup` is prose, but whether there is any is a
    # decision: never read, read as none, or recorded. Popped with the rest, a
    # reading that answers it for the first time would be dropped as identical.
    said = tier.cleanup
    d["cleanup_state"] = ("unread" if said is None
                          else "none" if not said.strip() else "recorded")
    return d


def _propose_reports(diff: SurveyDiff, found: "reporting.Search") -> None:
    """Put proven reporting commands on a reading's diff, as test-file rules.

    Where the reading proposed no rules of its own, the proposal is the rules
    on record with each proven command filled in. Where it did propose some,
    those are kept and a proven command fills only a rule it left without one
    and whose command is the one that was proved -- a command proved for one
    invocation says nothing about a different one.
    """
    proven = {(o.match, o.command): o.report for o in found.outcomes
              if o.outcome == "verified"}
    if not diff.test_file_commands:
        diff.test_file_commands = list(found.rules)
        diff.test_file_commands_reason = found.reason
        return
    filled = False
    rules = []
    for rule in diff.test_file_commands:
        report = proven.get((rule.match or "*", (rule.command or "").strip()))
        if report and not (rule.report or "").strip():
            rule = rule.model_copy(update={"report": report})
            filled = True
        rules.append(rule)
    if filled:
        diff.test_file_commands = rules
        diff.test_file_commands_reason = (
            (diff.test_file_commands_reason or "").rstrip() + "\n\n" + found.reason).strip()


def _unasked_cleanup(surface) -> str:
    """Name the tiers whose cleanup was never read, so a reading answers them.

    A re-survey reads what changed, and a question the last reading was never
    asked has not changed -- so a surface recorded before `cleanup` existed
    kept it null through every re-read. One re-read even said, in its own
    summary, that it would describe "how a test at each level undoes what it
    did", and left the field out because nothing about the tests had moved.
    Same move as the per-file rules' missing `load`: an unasked question is said
    to be one.
    """
    unasked = [t.tier for t in (getattr(surface, "tiers", None) or [])
               if getattr(t, "cleanup", "") is None or getattr(t, "cleanup_by", "") is None]
    if not unasked:
        return ""
    return ("\n\n**Never read:** how a test at "
            + ", ".join(f"`{t}`" for t in unasked)
            + " cleans up after itself, or who does it -- `cleanup` or `cleanup_by` is "
            "null there, which means nobody has looked, not that nothing does. Read it "
            "off this project's tests and propose the whole `testing` surface with it "
            "answered for every tier, and the fixes for any tier that is not `harness`. "
            "That is a `testing` change, whatever else moved.")


def _declined_suggestions(project: Project) -> str:
    """Suggestions this project has turned down.

    A reading writes its recommendations fresh every run from facts that do not
    change, so the same three arrive at every reading unless it is told. The
    filter is in code and this is not what enforces it -- it is here so a paid
    call is not spent writing a card nobody will ever see.
    """
    rulings = list(getattr(project.state, "recommendation_rulings", None) or [])
    families = list(getattr(project.state, "family_rulings", None) or [])
    if not rulings and not families:
        return ""
    lines = ["A human has turned these down for this project. **Do not suggest them again.**", ""]
    for r in rulings:
        lines.append(f"- **{r.title or r.key}**"
                     + (f" -- would have added `{r.would_gate}`" if r.would_gate else ""))
        if r.reason:
            lines.append(f"  - their reason: {r.reason}")
    for f in families:
        lines.append(f"- **every `{f.family}` check** -- this project goes without that family")
        if f.reason:
            lines.append(f"  - their reason: {f.reason}")
    return "\n".join(lines)


def _check_settings(project: Project, budget: int = 30_000) -> str:
    """What each check's settings switch on, read from where it says they live.

    A re-reading is shown the tooling files that moved, and a linter's config
    that did not move is never in front of it -- so it could not see that the
    accessibility rules it was about to suggest were already loaded, or say
    which other family's question a check's rules answer. A section where the
    check names one, read by the file's format; the whole file otherwise.
    """
    from .pipeline import check_settings, settings_section
    from .workspace import PathEscape, safe_join

    base = Path(project.repo_path).expanduser().resolve()
    by_check: dict[str, list[str]] = {}
    used = 0
    places = check_settings(project.gates)
    for name, path, section in places:
        if section and any(n == name and p == path and section.startswith(f"{s}.")
                           for n, p, s in places if s):
            continue  # shown already, inside the section that holds it
        label = f"{path}#{section}" if section else path
        try:
            text = safe_join(base, path).read_text(encoding="utf-8", errors="replace")
        except (PathEscape, OSError):
            by_check.setdefault(name, []).append(f"### {label}\n(not in the repository)")
            continue
        if section:
            readable, value = settings_section(text, path, section)
            if readable:
                text = ("(no such section)" if value is None
                        else json.dumps(value, indent=2, default=str))
        if used + len(text) > budget:
            by_check.setdefault(name, []).append(f"### {label}\n(left out for length)")
            continue
        used += len(text)
        by_check.setdefault(name, []).append(f"### {label}\n\n```\n{text.strip()}\n```")
    blocks = []
    for gate in project.gates:
        if gate.name not in by_check:
            continue
        also = "; ".join(f"{r.family}: {r.what}" for r in gate.also) or "nothing"
        blocks.append(f"## {gate.name} -- family {gate.family or 'unset'}, also answers {also}\n\n"
                      + "\n\n".join(by_check[gate.name]))
    return "\n\n".join(blocks)


def _current_suggestions(registry: "ProjectRegistry", project: Project) -> str:
    """The suggestions on record now, for a re-reading to restate or drop.

    A reading's suggestions replace the list, so one it leaves out is gone. It
    is shown them so that leaving one out is a decision -- done, or no longer
    this tool's business -- rather than something it never saw.
    """
    live = registry.live_recommendations(project)
    if not live:
        return ""
    return "\n".join(
        f"- **{r.title}**" + (f" -- would add `{r.would_gate}`" if r.would_gate else "")
        for r in live)


def _declined_checks(project: Project) -> str:
    """What this project has already turned down, and why.

    The filter that enforces it lives in code, so this section is not what makes
    a decline stick. It is here so a reading does not spend a proposal -- and a
    human does not spend a minute reading one -- on a check that will be dropped
    before it reaches the card.

    The reason is included because a reading can sometimes answer it. "No type
    checker installed" is a decline a later proposal can address by offering to
    install one; "we do not want this rule set" is not.
    """
    rulings = list(getattr(project.state, "gate_rulings", None) or [])
    kept = list(getattr(project.state, "kept_checks", None) or [])
    if not rulings and not kept:
        return ""
    lines = ["A human has declined these checks for this project. **Do not propose them again.**",
             "A proposal naming one is dropped before anybody sees it.", ""]
    for r in rulings:
        lines.append(f"- **{r.name}**" + (f" -- `{r.command}`" if r.command else ""))
        if r.reason:
            lines.append(f"  - their reason: {r.reason}")
    for k in kept:
        lines.append(f"- **{k.name}** -- kept as it is rather than "
                     + (f"run as `{k.command}`" if k.action == "change" else "removed")
                     + ". The same proposal is dropped; propose a change only if the "
                       "repository has moved again.")
        if k.reason:
            lines.append(f"  - their reason: {k.reason}")
    return "\n".join(lines)


def _capability_requests(project: Project) -> str:
    """The verify lane's outstanding requests, rendered for a reading.

    These are the only evidence a re-survey gets that is not a fact about the
    repository. They matter because the repository is not where the gap is: a
    project can be entirely unchanged and still be missing the one fixture that
    would let two of its criteria be checked, and the only role that ever
    noticed is the one that cannot propose a file.
    """
    rows = unmet_capabilities(project)
    if not rows:
        return ""
    out = []
    for row in rows:
        # Qualified by feature, always. `AC-1` is sequential from one inside a
        # single spec, so the id alone says nothing outside it.
        cost = "; ".join(
            f"{c['feature_id']}: {', '.join(c['criterion_ids']) or 'no criterion named'}"
            for c in row["cost"]) or "no criterion named"
        out.append(
            f"- **{row['need']}**\n"
            f"  asked for by {len(row['cost'])} feature(s); went unverified in "
            f"{cost}\n"
            + (f"  what it would have done with it: {row['detail'][:400]}\n"
               if row["detail"] else ""))
    return (
        "Each of these is a blind test author saying, after the fact, that a "
        "criterion went unchecked because this project could not offer something. "
        "They are requests for scaffolding, and they are the reason that field "
        "exists here. A need several features have hit is worth proposing for even "
        "where the repository itself has not moved.\n\n"
        "**Criterion ids belong to the feature beside them and to nothing else.** "
        "They are numbered from one within a single frozen spec, so every feature "
        "has an `AC-1`, and a bare id in anything you write here means nothing to "
        "somebody reading it once that feature is history. Cite the capability, "
        "and name the feature if you cite a criterion at all.\n\n"
        + "\n".join(out))


def _same_gate(proposed, current) -> bool:
    """Whether a proposed check would change anything about the one on record.

    The name is not part of it -- the change is keyed on the name, so the two
    agree by construction. Every other field is, read off the schema rather than
    listed here: a hand-kept list misses a field when one is added, and a
    re-reading that proposes exactly that field for some checks has them all
    dropped as identical to what is on record.
    """
    if current is None or proposed is None:
        return False
    keys = [k for k in type(current).model_fields if k != "name"]
    return all(getattr(proposed, k, None) == getattr(current, k, None) for k in keys)


def strip_unchanged(diff: SurveyDiff, state) -> list[str]:
    """Drop from a re-reading everything that proposes what is already recorded.

    In place, returning one line per thing dropped, for the record.

    A re-reading is asked for a diff and answers with whole fields, because
    `blind_placements`, `testing` and `test_file_commands` are replacement sets:
    to move one rule a model has to resend every rule. So a reading that changed
    nothing is indistinguishable, in the payload, from one that rewrote
    everything -- and a human is handed cards restating what they approved
    twenty minutes ago. After enough of those they stop reading the one that
    matters, which is the failure this whole surface exists to avoid.

    Compared on what decides behaviour, never byte-for-byte. The canaries and
    the prose are regenerated every reading -- same directory, same filename,
    differently worded test -- so equality would drop nothing at all. Two
    consecutive readings of one project can be identical in every load-bearing
    field and differ in `canary_passes`, `canary_fails`, a `kind` label and a
    reworded `runner` sentence.

    All-or-nothing per field, for the same reason the field is a replacement
    set: half a rule list is not a proposal a human can accept.
    """
    dropped: list[str] = []

    kept = []
    for g in diff.gate_changes:
        current = next((x for x in state.gates if x.name == g.name), None)
        if g.action in ("add", "change") and g.gate is None:
            dropped.append(f"gate_change {g.action} {g.name!r}: no check definition to apply")
            continue
        if g.action in ("add", "change") and _same_gate(g.gate, current):
            dropped.append(f"gate_change {g.action} {g.name!r}: identical to the check on record")
            continue
        # A person kept this check as it was when this same change was proposed.
        # A different command is a different question, and is asked.
        refused = next((k for k in (getattr(state, "kept_checks", None) or [])
                        if k.name == g.name and k.action == g.action
                        and (g.action == "remove" or (g.gate and k.command == g.gate.command))), None)
        if refused is not None:
            dropped.append(f"gate_change {g.action} {g.name!r}: kept as it was when this was "
                           "proposed before")
            continue
        kept.append(g)
    diff.gate_changes = kept

    if diff.blind_placements:
        now = [(b.directory, b.filename) for b in state.blind_placements]
        if now and [(b.directory, b.filename) for b in diff.blind_placements] == now:
            dropped.append(
                f"blind_placements: {len(now)} placement(s), same directory and filename "
                "as recorded; only the canaries were reworded")
            diff.blind_placements = []
            diff.blind_placements_reason = ""

    if diff.test_file_commands:
        shape = lambda rs: [(r.match, r.command, r.report, r.collect) for r in rs]
        now = shape(state.test_file_commands)
        if now and shape(diff.test_file_commands) == now:
            dropped.append(f"test_file_commands: {len(now)} rule(s), identical to those recorded")
            diff.test_file_commands = []
            diff.test_file_commands_reason = ""

    if diff.testing is not None and state.testing is not None:
        # `runner` is prose, not a name: "vitest 2.1 under jsdom for web/" one
        # reading and "vitest (web/vite.config.ts), jsdom" the next, describing
        # the same tool. Compared whole it makes every reading look like a
        # change; ignored entirely it would hide vitest becoming jest. So the
        # tool it opens with is the identity and the rest of the sentence is not.
        # Every field except the ones named as prose, rather than a list of the
        # ones to compare. Three readings in a row were dropped as identical
        # because a field added later was not on such a list: a check's
        # `ci_command`, then `run_by`, then a setup file the tier had gained --
        # each time the card said nothing had changed about the only thing that
        # had. `TIER_PROSE` is what a reading rewrites in its own words every
        # time; anything else is a decision, and a new field is compared until
        # somebody says otherwise. `test_the_tier_comparison_has_no_blind_spot`
        # fails if a field is added and neither side is chosen.
        shape = lambda s: [tier_shape(t) for t in s.tiers]
        db = lambda s: (s.disposable_db.model_dump(mode="json") if s.disposable_db else None)
        if shape(diff.testing) == shape(state.testing) and db(diff.testing) == db(state.testing):
            dropped.append(
                "testing: every tier at the verdict, runner and checks already recorded")
            diff.testing = None
            diff.testing_reason = ""

    if diff.preview is not None and state.environment is not None:
        current = state.environment.preview
        if current is not None and diff.preview.model_dump(mode="json") == current.model_dump(mode="json"):
            dropped.append("preview: identical to the one on record")
            diff.preview = None
            diff.preview_reason = ""

    if diff.environment is not None and state.environment is not None:
        if diff.environment.model_dump(mode="json") == state.environment.model_dump(mode="json"):
            dropped.append("environment: identical to the one on record")
            diff.environment = None
            diff.environment_reason = ""

    # A cleanup option is offered only if all of it is in this reading: half a
    # fix -- a reset step with no script, a script with no step -- would be
    # applied as though it were the whole thing.
    if diff.testing is not None:
        for tier in diff.testing.tiers:
            whole = []
            for option in tier.cleanup_options:
                problem = cleanup_option_problems(diff, option)
                if problem:
                    dropped.append(f"{tier.tier} cleanup option {option.title!r}: {problem}")
                else:
                    whole.append(option)
            tier.cleanup_options = whole
    return dropped


def own_image_files(project: Project, already: Sequence[str]) -> list[str]:
    """The project's own Dockerfiles and CI config, while Fabrika runs it on a
    Dockerfile of its own.

    A re-reading is shown only the tooling that moved, and a Dockerfile that has
    not moved is never in front of it -- so the suggestion to give the project's
    own image a stage that runs its checks could only be made blind, and the
    survey is told never to. Shown until the project builds from its own file.
    """
    env = project.environment
    if env is None or env.dockerfile_path or not env.dockerfile.strip():
        return []
    paths = [p for p in tooling_paths(project.repo_path)
             if p not in already and ("dockerfile" in p.lower().rsplit("/", 1)[-1]
                                      or p.startswith(".github/workflows/")
                                      or p.rsplit("/", 1)[-1].startswith(("docker-compose", "compose.")))]
    if not paths:
        return []
    return ["---\n\n# This project's own image and CI\n\n"
            "Not changed since the last reading; shown because the checks run on a Dockerfile "
            "Fabrika wrote, and whether this project's own could run them instead is a "
            "suggestion to make or not.\n\n" + read_paths(project.repo_path, paths, budget=30_000)]


def reading_cost(llm: Any, role: str, prompt: str) -> dict[str, Any]:
    """What one reading of the repository cost, for its record.

    A reading is the most expensive thing a project does outside a feature --
    a whole repository digest on the strongest model -- and a project can be
    read two dozen times with nothing anywhere saying what that came to.
    Empty when the client cannot say, which is unknown and not zero.
    """
    take = getattr(llm, "take_usage", None)
    spent = take(role, prompt) if callable(take) else None
    return dict(spent) if isinstance(spent, dict) else {}


DOCKER_MISSING = re.compile(
    r"(?P<script>[\w./-]+\.(?:sh|bash)): line (?P<line>\d+): docker: (?:command )?not found")


class ProjectOnboarding:
    """Deterministic control flow, same as the feature pipeline. (INV-6)"""

    def __init__(
        self,
        config: Config,
        registry: ProjectRegistry,
        llm: LLM | None = None,
        on_progress: ProgressCallback | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.config = config
        self.registry = registry
        self.llm = llm if llm is not None else LLM(config)
        self.on_progress = on_progress
        self.runner = runner
        # Asks OSV and the registries about what the project depends on. A
        # stub in the suite, which never reaches the network.
        self.dependency_lookup: Any = dependencies.Lookup()

    def _emit(self, project: Project, phase: str, status: str, detail: str = "") -> None:
        if self.on_progress is None:
            return
        try:
            self.on_progress({
                "project_id": project.id, "feature_id": "", "stage": project.state.stage,
                "phase": phase, "status": status, "detail": detail, "error": "", "at": _now(),
            })
        except Exception:
            pass

    # -- survey -----------------------------------------------------------

    async def run_survey(self, project: Project) -> Project:
        """Read the repo, propose gates and an environment, prove a baseline."""
        surveyed = False
        try:
            project = self.registry.record_survey_started(project)
            self._emit(project, "survey", "running")
            digest = repo_digest(project.repo_path, project.state.survey_digest_budget)
            prompt = (
                f"# The directory you were pointed at\n\n"
                f"Path: {project.repo_path}\n"
                f"Name given by the human: {project.state.name}\n\n"
                f"---\n\n{digest}"
            )
            survey: ProjectSurvey = await self.llm.ask(
                "surveyor", prompt, ProjectSurvey,
                system=self.config.role_prompt("surveyor"),
            )
            self.registry.record_survey(project, survey, model=self.config.role("surveyor").model,
                                        spent=reading_cost(self.llm, "surveyor", prompt))
            surveyed = True
            self._emit(project, "survey", "done", f"{len(survey.gates)} check(s), environment: {survey.environment.kind}")

            waiting = self._awaiting_scaffold(project, survey)
            if waiting:
                # The environment runs in a file this reading proposed, and a
                # proposed file is in nobody's repository until a person applies
                # it. Running the baseline now measures a place that does not
                # exist yet and reports the reading as failed.
                project.state.error = (
                    f"The checks run in {waiting}, which this reading proposes and has not "
                    "written. Apply it from the checks page, then run the baseline.")
                self.registry.save(project, note="awaiting_scaffold")
                self._emit(project, "baseline", "waiting", f"apply {waiting} first")
                return project

            self._emit(project, "baseline", "running")
            baseline, image = await self._baseline(project)
            baseline = self._set_aside_what_needs_docker(project, baseline)
            self.registry.record_baseline(project, baseline, image=image)
            await dependencies.refresh_inventory(project, self.dependency_lookup)
            passed = sum(1 for g in baseline.results if g.passed)
            self._emit(project, "baseline", "done", f"{passed}/{len(baseline.results)} green on an untouched checkout")
            return project
        except Exception as exc:
            if surveyed:
                # The reading was made and saved. The baseline failing is a fact
                # about it, not a reason to throw it away: the stage stays where
                # the survey put it and the error says what went wrong after.
                project.state.error = f"{type(exc).__name__}: {exc}"[:4000]
                self.registry.save(project, note="baseline_failed")
                self._emit(project, "baseline", "failed", str(exc)[:200])
            else:
                self.registry.record_failure(project, f"{type(exc).__name__}: {exc}")
                self._emit(project, "survey", "failed", str(exc)[:200])
            raise

    def _awaiting_scaffold(self, project: Project, survey: ProjectSurvey) -> str:
        """The proposed file the environment runs in, if that is all that is missing."""
        env = project.environment
        if env is None or env.kind != "compose" or not env.compose_file:
            return ""
        if env.compose_file not in {f.path for f in survey.scaffolding}:
            return ""
        if (Path(project.repo_path) / env.compose_file).is_file():
            return ""
        return env.compose_file

    async def run_baseline(self, project: Project) -> Project:
        """Re-prove the baseline, and keep gate 0 where a human left it.

        A new baseline is not a new thing to approve, so this does not end with
        `stage = "awaiting_approval"` unconditionally. `approve` says the bar is
        "will these gates produce information" -- an answer about the gate list
        and what it runs against, not about any single run of it. Re-proving an
        unchanged list refreshes a measurement; it does not ask the question
        again.

        Treating it as if it did produces a state that cannot be true. Running
        the baseline to capture the environment's package list would send the
        project back to gate 0 while a feature is building against those very
        gates, so the console would say "nothing is built here until you
        approve" over a build already in flight -- and, because a project
        mid-gate-0 has no bench, the running feature would become unreachable
        in the UI.

        Two rules. The approval stands unless the thing it answered has
        changed, compared by the same `INVALIDATES_BASELINE` set that sends a
        project back to gate 0 in the first place. And approval is never revoked
        while features are live: those features are already building against
        this configuration, and un-approving underneath them describes a world
        that does not exist.
        """
        problems = self.registry.environment_problems(project.environment, project.gates)
        if problems:
            # Failing here says what is wrong with the configuration. Failing in
            # `docker run` says a container would not start, which is true and
            # useless.
            message = (
                "this project's environment cannot run its checks as configured: "
                + "; ".join(problems)
            )
            self.registry.record_failure(project, message)
            self._emit(project, "baseline", "failed", message[:200])
            raise ProjectError(message)
        try:
            self._emit(project, "baseline", "running")
            was_approved = project.state.stage == "ready"
            approved_for = self.registry.approved_fingerprint(project)
            live = self.registry.live_feature_ids(project)

            baseline, image = await self._baseline(project)
            baseline = self._set_aside_what_needs_docker(project, baseline)
            self.registry.record_baseline(project, baseline, image=image)
            await dependencies.refresh_inventory(project, self.dependency_lookup)
            # DESIGN.md's own linter, offered as a check once there is one.
            self.registry.raise_guide_checks(project)

            # `record_baseline` may set the resolved image, so the fingerprint is
            # taken after it: comparing before would report a change this run
            # itself had just made.
            unchanged = bool(approved_for) and (
                approved_for == self.registry.approval_fingerprint(project))
            keep = was_approved and (unchanged or bool(live))
            project.state.stage = "ready" if keep else "awaiting_approval"

            why = (
                "nothing that was approved has changed"
                if keep and unchanged else
                f"{len(live)} feature(s) are building against it" if keep else
                "the gates or the environment moved since it was approved"
            )
            self.registry.save(project, note=f"baseline re-run · {why}")
            passed = sum(1 for g in baseline.results if g.passed)
            self._emit(project, "baseline", "done",
                       f"{passed}/{len(baseline.results)} green · "
                       + ("still approved" if keep else "needs approving again"))
            return project
        except Exception as exc:
            self.registry.record_failure(project, f"{type(exc).__name__}: {exc}")
            self._emit(project, "baseline", "failed", str(exc)[:200])
            raise

    def _set_aside_what_needs_docker(self, project: Project, report: GateReport) -> GateReport:
        """Take off the list a check whose own script needs Docker, and say what to change.

        The checks run in a container with no Docker, by design, so a gate whose
        command starts its own containers can never pass here: it fails as
        `docker: command not found`, on code nobody has touched. Left on the
        list it makes the project permanently red for something no repository
        change in this tool's reach can fix. It is declined -- on the record and
        reinstatable -- and the change to the project's script is offered as a
        suggestion, never made: the file is the project's.
        """
        kept = list(report.results)
        for result in report.results:
            if result.passed or result.name not in {g.name for g in project.state.gates}:
                continue
            found = DOCKER_MISSING.search(result.output_tail or "")
            if not found:
                continue
            script, line = found.group("script"), found.group("line")
            path = next(iter(self._tracked_like(project, script)), script)
            said = (f"`{result.command}` starts its own containers ({path}, line {line}), "
                    "and the checks run where Docker is not available.")
            self.registry.decline_gate(project, result.name, said, command=result.command)
            self.registry.raise_recommendation(project, Recommendation(
                title=f"Let `{path}` use a database it is given instead of starting one with Docker",
                kind="tests",
                why=(f"`{result.command}` needs a database and gets one by calling `docker` from "
                     f"`{path}`. Anywhere Docker is not available -- a CI container, a sandbox -- "
                     "the suite cannot start, and that is the only thing keeping it from being "
                     "run as a check."),
                evidence=f"{path}:{line} -- `docker` is called and is not found (exit 127).",
                how=(f"In `{path}`, skip the lines that call `docker` when a database URL is "
                     "already supplied in the environment, and connect to that one. Keep the "
                     "current behaviour when it is not, so nothing changes for a developer "
                     "running it locally. Seed through `psql` against the same URL rather "
                     "than `docker exec`."),
                levels=["user"],
                would_gate=result.command,
            ))
            kept = [r for r in kept if r.name != result.name]
        return GateReport(results=kept) if len(kept) != len(report.results) else report

    @staticmethod
    def _tracked_like(project: Project, script: str) -> list[str]:
        """Repo-relative paths whose name is the script's, for naming it in full."""
        name = Path(script).name
        return [p for p in launched_scripts(project.repo_path) if Path(p).name == name]

    async def _probe_packages(self, cwd, runner) -> list[str]:
        """Ask the environment what is installed in it.

        Run here because here is the one place a container is already up with
        setup applied, and because the answer belongs to the project rather than
        to any feature. The verify lane cannot ask this itself -- it has no
        repository, no runner and no container, by design.

        Every probe is optional. A project with no Node gets nothing from the
        npm probe, and that is a fact about the project, not a failure.
        """
        names: list[str] = []
        for command in self.config.pipeline.package_probes:
            gate = GateConfig(name="probe", command=command, timeout_s=180.0)
            try:
                result = await run_gate(gate, cwd, runner, network=True)
            except Exception:
                continue
            if not result.started:
                continue
            names.extend(parse_package_lines(result.output_tail or ""))
        return installable_names(names)

    async def _probe_placements(
        self, project: Project, cwd: Any, runner: Runner,
    ) -> list[PlacementResult]:
        """Prove every declared placement, or say which one could not be proved.

        Nothing here decides anything. It measures, stores what it measured, and
        leaves the decision to `approve` and to the human reading gate 0 -- the
        same shape as `empty_run_detected`, and for the same reason: a
        measurement a human can read is worth more than a rule they cannot see.
        """
        override = self.config.rework.oracle_file_command.strip()
        rules = ([TestFileCommand(match="*", command=override)] if override
                 else list(project.state.test_file_commands))
        results: list[PlacementResult] = []
        for placement in project.state.blind_placements:
            results.append(await probe_placement(placement, rules, cwd, runner))
        return results

    async def _find_fixers(self, project: Project, cwd: Any, runner: Runner,
                           report: GateReport) -> None:
        """Which checks rewrite files, measured on this untouched checkout.

        A formatter or `lint --fix` is declared like any other check and cannot
        be told apart by its name. So: if anything tracked changed while the
        checks ran, the tree is put back and each check run alone, and the ones
        that change files are recorded as fixers. Files a command creates -- a
        build's output -- are not rewrites; only tracked files it changed are.

        A fixer that changes untouched code is red here. The repository is not
        in the state its own formatter wants, and a build would otherwise
        reformat the whole repository inside one feature's diff.
        """
        if not tracked_changes(cwd):
            return
        by_name = {g.name: g for g in project.state.gates}
        restore_tracked(cwd)
        for result in report.results:
            gate = by_name.get(result.name)
            if gate is None:
                continue
            await run_gate(gate, cwd, runner)
            changed = tracked_changes(cwd)
            if changed:
                result.rewrote = changed[:50]
                result.passed = False
                result.output_tail = (
                    f"This command rewrote {len(changed)} file(s) on untouched code, so it is a "
                    "fixer, and the repository is not in the state it wants: "
                    + ", ".join(changed[:10]) + (" ..." if len(changed) > 10 else "")
                    + ". Run it on the repository and commit the result, or decline the check."
                    + "\n\n" + result.output_tail)
            restore_tracked(cwd)

    async def _baseline(self, project: Project) -> tuple[GateReport, str]:
        """Run the proposed gates on a clean checkout, in a throwaway sandbox.

        A throwaway sandbox rather than the repo itself, so the baseline is
        measured in the same kind of place features will be built, and so
        nothing here can touch the human's working tree.
        """
        if not project.state.gates:
            return GateReport(results=[]), ""

        environment = project.environment
        # Structural faults are known before anything is built. Letting the
        # build discover them costs an image pull and returns a Docker parse
        # error where a sentence about the survey would do -- and the console
        # already refuses the same list, so the two paths disagreed about
        # whether the environment was runnable.
        problems = self.registry.environment_problems(
            environment, project.state.gates, project.repo_path)
        if problems:
            raise ProjectError(
                f"the environment proposed for {project.id!r} cannot run these checks:\n"
                + "\n".join(f"  - {p}" for p in problems)
                + "\n\nFix them in settings, or survey again."
            )
        # Build (or resolve) the image before anything else: an environment that
        # cannot be built is a fact the human needs at gate 0, not a surprise
        # halfway through the first feature.
        if self.runner is not None:
            runner, image = self.runner, (environment.image if environment else "")
        else:
            # The baseline gets its own stack too, named for the baseline
            # rather than a feature, so proving gates green cannot disturb a
            # build that happens to be running.
            full = await low_disk(self.config.docker, self.config.docker.min_free_gb)
            if full:
                raise ProjectError(full)
            runner, image = await runner_for(project, self.config, feature_id=BASELINE_ID)

        sandbox = Sandbox.create(
            repo=project.repo_path,
            root=self.config.sandbox_path,
            project_id=project.id,
            feature_id=BASELINE_ID,
            base_ref=project.base_ref,
            image=image,
        )
        # Which commit this measures, so a build can tell the base has moved on.
        project.state.baseline_sha = sandbox.state.base_sha or ""
        try:
            setup_results: list[GateResult] = []
            if environment and (environment.setup or project.warm_gates):
                setup_results = await run_setup(environment.setup, sandbox.path, runner,
                                                 warm=project.warm_gates)
            # After setup, before the gates: this is the environment the gates
            # are about to run in, and the only moment it exists to be asked.
            project.state.runtime_packages = await self._probe_packages(sandbox.path, runner)
            # And which toolchain versions the checks run with, by the commands
            # the survey declared: what a check that is red here and green on a
            # developer's machine is diagnosed against.
            project.state.toolchain_versions = await self._probe_versions(
                environment, sandbox.path, runner)
            # And the one question an exit code cannot answer on its own: does
            # this project's runner fail when given nothing to run? Everything
            # the packet says about a criterion being verified rests on the
            # difference between a suite that passed and a suite that collected
            # nothing, and both exit zero. Measured here, against the project's
            # own command, because "most runners exit non-zero on an empty
            # selection" is true and is exactly the sort of belief that ends in
            # a table of framework regexes in the orchestrator.
            # Every rule, not the first one. A project can run two test runners
            # and they need not agree: in one repository `npx vitest run
            # <nothing>` exits 1 while the project's own `npm test` script --
            # `vitest run --passWithNoTests` -- exits 0 on the same input. One
            # lenient rule is a hole, and probing whichever rule happens to be
            # listed first can measure the runner that is not being asked
            # about.
            override = self.config.rework.oracle_file_command.strip()
            rules = ([override] if override
                     else [r.command for r in project.state.test_file_commands])
            answers: list[bool | None] = []
            notes: list[str] = []
            for template in rules:
                detected, evidence = await probe_empty_selection(
                    template.replace("{oracle_dir}", self.config.rework.oracle_dir),
                    sandbox.path, runner)
                answers.append(detected)
                notes.append(evidence)
            if answers:
                # False if any rule tolerates an empty selection; None if none
                # said False and at least one could not be measured. A probe
                # that did not run is not evidence that a runner is strict.
                project.state.empty_run_detected = (
                    False if False in answers
                    else (None if None in answers else True))
                project.state.empty_run_evidence = "\n\n".join(notes)
            # The same prepared environment a feature gets, for the same reason.
            # A project can declare services its gates need standing -- an
            # application server the suite drives over HTTP, an emulator -- and
            # a baseline that skipped them would run those gates against
            # nothing. `tests` would fail on a refused connection, at the one
            # moment a human is deciding whether these checks are worth running,
            # and the failure would read as the repository's.
            #
            # This was wired into the feature path first and not into this one.
            # A survey then correctly moved `uvicorn` out of a gate command and
            # into `services`, which is exactly when the gap starts to bite.
            environment = project.environment

            def session():
                return test_session(
                    sandbox.path, runner,
                    services=list(environment.services) if environment else (),
                    prepare=list(environment.test_prepare) if environment else (),
                )

            def not_ready(why: str) -> GateReport:
                # Not "these gates failed". Nothing ran, and a gate that
                # could not run is what `approve` refuses -- which is the
                # right answer to an environment that cannot stand up.
                return GateReport(results=[GateResult(
                    name=g.name, command=g.command, exit_code=1, passed=False,
                    started=False, output_tail=why)
                    for g in project.state.gates])

            async def assess(report: GateReport) -> None:
                await self._find_fixers(project, sandbox.path, runner, report)
                # Where the blind suite goes, proved rather than assumed --
                # in this session, because a placement's canary is run by
                # the project's own per-file command and that command may
                # need the same services and the same prepared state a real
                # test does.
                #
                project.state.placement_probe = await self._probe_placements(
                    project, sandbox.path, runner)
                # And whether `usable` is true, which nothing measured until
                # now. It runs after the placements because it needs one:
                # the tier's canary is written where a blind test would
                # actually live, so a tier can only be proved in a directory
                # already shown to collect and configure a file.
                override = self.config.rework.oracle_file_command.strip()
                project.state.testing_probe = await probe_testing(
                    project.state.testing,
                    project.state.blind_placements,
                    [r.directory for r in project.state.placement_probe if r.usable],
                    ([TestFileCommand(match="*", command=override)] if override
                     else list(project.state.test_file_commands)),
                    sandbox.path, runner,
                    green=[g for g in project.state.gates
                           if any(r.name == g.name and r.passed for r in report.results)])

            # Measured offline, as every check always is. A check that fails
            # may be failing for something its tool fetches only when it runs
            # -- Maven's test provider is the one that found this -- which no
            # setup command fetched because none of them ran it. So each one
            # that failed is run once more with the network, as setup, and then
            # measured offline again. If that is what it was missing, it passes
            # now, or at least stops failing on a name it could not resolve,
            # and every setup from here on runs it that way first
            # (`warm_checks`). Nobody has to know how any tool behaves.
            async with session() as prepared:
                if not prepared.ready:
                    report = not_ready(prepared.report)
                    failing: list = []
                else:
                    report = await run_gates(project.state.gates, sandbox.path, runner)
                    failing = self._warm_candidates(project, report)
                    if not failing:
                        await assess(report)
            if failing:
                setup_results += await run_setup([], sandbox.path, runner, warm=failing)
                async with session() as prepared:
                    if prepared.ready:
                        again = await run_gates(failing, sandbox.path, runner)
                        report = self._merge_warmed(project, report, again)
                        await assess(report)
            # And whether a person can open it: the preview a review offers,
            # started the way it will be started then and asked for its page
            # through the door. The button is offered on the strength of this
            # having worked, not of the survey having said it would.
            project.state.preview_probe = await measure_preview(
                project, runner, sandbox.path, self.config,
                label=f"{project.id}/{BASELINE_ID}", sha=sandbox.state.base_sha or "")
            # What kind of red each failing check is, from what was read; and,
            # for those red on this untouched commit, why -- with each proposed
            # fix tried here, in this throwaway checkout, before anyone sees it.
            read = diagnosis.reader(Path(sandbox.path))
            diagnosis.classify(project.state.gates, report, read)
            await self._diagnose(project, report, Path(sandbox.path), runner, session, read)
            report.results = setup_results + report.results
            return report, image
        finally:
            # A compose stack outlives the worktree unless it is asked not to,
            # and one left standing holds a database the next run must not find.
            down = getattr(runner, "down", None)
            if down is not None:
                await down()
            sandbox.release(keep_branch=False)

    def _guide_text(self, project: Project) -> str:
        """The repository's guides, for a reading to find rules a tool could enforce."""
        from . import guides as guide_lib

        ref = project.state.base_ref or "HEAD"
        found = self.registry.guides(project)
        if not found:
            return "(none -- no AGENTS.md, DESIGN.md or skills)"
        listed = "\n".join(f"- {g.path} ({guide_lib.LAYER_WORDS[g.layer]})" for g in found)
        chosen = [(g, how) for g, how in guide_lib.select(found, "scout") if how == "whole"]
        text, _ = guide_lib.render(project.repo_path, ref, chosen, self.config.guides.budget_chars)
        return (listed + ("\n\n## What they say\n\nA rule here that a tool could check -- a "
                          "banned import, a naming pattern, a colour used without a token -- is a "
                          "suggestion for a check that enforces it: see the surveyor's "
                          "guide-to-check rule.\n\n" + text if text else ""))

    async def _offer_guide_draft(self, project: Project) -> None:
        """AGENTS.md's code style and testing sections, drafted from the
        conventions features kept observing.

        Not before `promote_after_features` features have run, and only rules at
        least two of them observed, in files that still exist: code counts, a
        model only writes the sentences. With no AGENTS.md it fills the
        sections of the one Fabrika offers; with one, it is an addition to it,
        leaving out what it already says. Recorded for a person to edit and
        approve on the Guides tab.
        """
        import hashlib

        from . import guides as guide_lib
        from .schemas import DraftRule, GuideDraft, GuideDraftAnswer, ScoutReport

        seen: list[list[Any]] = []
        for feature_id in project.feature_ids():
            store = project.feature_store(feature_id)
            if store.has("scout"):
                seen.append(list(ScoutReport.model_validate(store.payload("scout")).observed_conventions))
        if len(seen) < self.config.guides.promote_after_features:
            return
        ref = project.state.base_ref or "HEAD"
        present = set(guide_lib.tracked(project.repo_path, ref))
        rules: dict[str, dict[str, Any]] = {}
        for observed in seen:
            for key in {(c.slug or c.rule).strip().lower() for c in observed}:
                c = next(c for c in observed if (c.slug or c.rule).strip().lower() == key)
                held = rules.setdefault(key, {"rule": c.rule, "slug": c.slug, "features": 0, "files": []})
                held["features"] += 1
                held["files"] = list(dict.fromkeys([*held["files"], *[f for f in c.files if f in present]]))
        kept = [DraftRule(**r) for r in rules.values() if r["features"] >= 2 and r["files"]]
        if not kept:
            return
        path = guide_lib.AGENTS_PATH
        mode = "append" if path in present else "new"
        draft_id = hashlib.sha256(
            (mode + path + "|".join(sorted(r.slug for r in kept))).encode()).hexdigest()[:12]
        if draft_id in project.state.guide_offers_declined or any(
                r.get("kind") == "guide_draft" and (r.get("payload") or {}).get("id") == draft_id
                for r in project.store):
            return
        listed = "\n".join(f"- {r.rule} (`{r.slug}`) -- observed by {r.features} features, "
                           f"e.g. {', '.join(r.files[:3])}" for r in kept)
        existing = guide_lib.read_at(project.repo_path, ref, path) if mode == "append" else ""
        prompt = (f"# The rules, as features observed them\n\n{listed}\n\n---\n\n"
                  + (f"# The AGENTS.md these are added to\n\n{existing}\n\nWrite only what to "
                     "add, under `## Code style` and `## Testing` headings. Leave out any rule it "
                     "already states."
                     if mode == "append" else
                     "# Write AGENTS.md's `## Code style` and `## Testing` sections\n\nThe rest "
                     "of the file -- its commands -- is written by code. Write only these two "
                     "sections; leave one out if no rule belongs in it."))
        try:
            answer = await self.llm.ask("guide_writer", prompt, GuideDraftAnswer,
                                        system=self.config.role_prompt("guide_writer"))
            markdown = answer.markdown.strip()
        except Exception:                                          # noqa: BLE001
            markdown = ""
        if not markdown:
            markdown = "## Code style\n\n" + "\n".join(
                f"- {r.rule} (for example `{r.files[0]}`)" for r in kept)
        project.store.append("guide_draft", GuideDraft(
            id=draft_id, mode=mode, path=path, markdown=markdown, rules=kept,
            features=len(seen)).model_dump(mode="json"), role="guide_writer")

    async def _probe_versions(self, environment: Any, cwd: Any, runner: Any) -> dict[str, str]:
        """What each declared version command prints, first line. Optional, like
        every probe: a command that fails says nothing, and that is not a failure."""
        out: dict[str, str] = {}
        for command in (environment.version_commands if environment else []):
            try:
                ran = await runner.execute(command, cwd=Path(cwd), timeout_s=60.0, network=False)
            except Exception:  # noqa: BLE001 -- a probe is never allowed to fail a run
                continue
            # The line with a version in it. A compose runner's own status lines
            # ("Container app-db-1 Running") arrive on the same stream, first.
            lines = [ln.strip() for ln in (ran.output or "").splitlines() if ln.strip()]
            line = next((ln for ln in lines if re.search(r"\d+\.\d+", ln)), lines[0] if lines else "")
            if ran.started and ran.exit_code == 0 and line:
                out[command] = line[:200]
        return out

    async def _diagnose(self, project: Project, report: GateReport, cwd: Path, runner: Any,
                        session: Callable[[], Any], read: diagnosis.Read) -> None:
        """Why each check red on this commit is red, recorded, and reused while
        nothing it read has changed -- a run of the checks with nothing changed
        asks no model anything."""
        cfg = self.config.diagnosis
        if not cfg.enabled or self.llm is None:
            return
        env = project.environment
        dockerfile = (read(env.dockerfile_path) if env and env.dockerfile_path else None) \
            or (env.dockerfile if env else "") or ""
        versions = dict(project.state.toolchain_versions)
        pinned = diagnosis.read_files(read, env.version_files if env else [])
        known = diagnosis.latest_by_check(list(project.store))
        gates = {g.name: g for g in project.state.gates}

        async def ask(text: str) -> DiagnosisAnswer:
            return await self.llm.ask("diagnose", text, DiagnosisAnswer,
                                      system=self.config.role_prompt("diagnose"))

        for result in diagnosis.worth_diagnosing(report.results, project.state.gates, cfg.max_checks):
            gate = gates[result.name]
            settings = diagnosis.settings_text(gate, read)
            fp = diagnosis.fingerprint(gate, result, settings, env, project.state.baseline_sha or "",
                                       versions)
            result.diagnosis = fp
            held = known.get((gate.name, fp))
            if held is not None and not held.failed:
                continue
            self._emit(project, "baseline", "running", f"working out why {gate.name} is red")
            prompt = diagnosis.brief(gate, result, settings, env, dockerfile, versions, pinned)
            found = await diagnosis.diagnose_check(
                gate=gate, result=result, fp=fp, prompt=prompt, cwd=cwd, runner=runner, ask=ask,
                settings=cfg, session=session)
            found.cost = reading_cost(self.llm, "diagnose", prompt)
            project.store.append("diagnosis", found.model_dump(mode="json"), role="diagnose")

    @staticmethod
    def _warm_candidates(project: Project, report: GateReport) -> list:
        """Checks that failed offline and were not already warmed by setup."""
        failed = {r.name for r in report.results
                  if r.started and not r.passed and not r.skipped}
        known = set(project.state.warm_checks)
        return [g for g in project.state.gates if g.name in failed and g.name not in known]

    @staticmethod
    def _merge_warmed(project: Project, first: GateReport, again: GateReport) -> GateReport:
        """Keep the second offline run of each check the warm-up changed, and
        remember it; keep the first where the warm-up made no difference.

        Changed means it passes now, or it failed on a name it could not
        resolve and no longer does -- a check that was also red for a real
        reason is then red for that reason, which is the one worth reading.
        """
        by_name = {r.name: r for r in again.results}
        results = []
        for old in first.results:
            new = by_name.get(old.name)
            fetched = new is not None and new.started and (
                new.passed or (bool(NAME_UNRESOLVED.search(old.output_tail or ""))
                               and not NAME_UNRESOLVED.search(new.output_tail or "")))
            if fetched:
                new.warmed = True
                if old.name not in project.state.warm_checks:
                    project.state.warm_checks.append(old.name)
                results.append(new)
            else:
                results.append(old)
        first.results = results
        return first

    # -- re-survey --------------------------------------------------------

    async def run_resurvey(self, project: Project) -> SurveyDiff:
        """A second reading, when the tooling has moved. Produces a diff, and
        applies none of it.

        Gates come from what a repository already runs, so they are only as
        current as the reading that produced them. But the call that can add a
        gate can also drop one, and a re-survey that quietly removed a failing
        gate would be a way to make an inconvenient result disappear. So this
        returns a proposal, the proposal is recorded whether or not it is taken,
        and a human applies it.
        """
        drift = self.registry.tooling_drift(project)
        moved = list(dict.fromkeys(
            list(drift.get("added") or [])
            + list(drift.get("changed") or [])
            + list(drift.get("unseen") or [])
        ))
        self._emit(project, "resurvey", "running", f"{len(moved)} tooling file(s)")
        try:
            prompt = "\n\n".join([
                f"# The project\n\nPath: {project.repo_path}\nName: {project.state.name}",
                "---\n\n# The gates as they stand\n\n" + json.dumps(
                    [g.model_dump(mode="json") for g in project.gates], indent=2),
                "---\n\n# What a test here can be written against, as recorded\n\n"
                + (json.dumps(project.state.testing.model_dump(mode="json"), indent=2)
                   if project.state.testing.tiers else
                   "(nothing recorded -- this project has never been read for what a "
                   "NEW test at each level could reuse, so every criterion needing a "
                   "level it cannot offer will come back unverified with no warning "
                   "beforehand. Proposing a reading is a `testing` change.)")
                + _unasked_cleanup(project.state.testing),
                "---\n\n# Its test files and setup\n\n"
                + read_paths(project.repo_path, testing_paths(
                    project.repo_path,
                    # Whatever this project already established is setup, by
                    # name-independent provenance: scaffolding a human applied,
                    # and the setup files the last approved reading recorded.
                    known=[
                        *project.state.scaffolding_applied,
                        *(f.path for tier in project.state.testing.tiers
                          for f in tier.setup_files),
                    ])),
                # `collect` is rendered too, and its absence is named rather than
                # left blank. Shown only as `match -> command`, a rule that
                # already carried a load command looked identical to one that
                # never had, so the reading could not tell an answered question
                # from an unasked one -- and would propose the same change on
                # every run, for ever, with a human ticking the same box.
                "---\n\n# How this project runs a single test file\n\n"
                + ("\n".join(
                    f"{r.match} -> {r.command}\n"
                    f"{' ' * len(r.match)}    load: "
                    + (r.collect or "(none recorded -- see `collect` below)")
                    for r in project.state.test_file_commands)
                   or "(none recorded -- so no acceptance test can be attributed to "
                      "the criterion it checks, and every criterion rests on one exit "
                      "code covering the whole suite. Proposing one is a `change`.)"),
                "---\n\n# Where a blind test file goes, as recorded\n\n"
                + ("\n".join(
                    f"{b.directory}/{b.filename}" + (f"  ({b.kind})" if b.kind else "")
                    for b in project.state.blind_placements)
                   or "(none recorded -- so there is nowhere to put a test written by an "
                      "agent that has never seen this repository, and this project cannot "
                      "be approved at all. Proposing them is a `change`.)"),
                "---\n\n# The environment they run in\n\n" + json.dumps(
                    project.environment.model_dump(mode="json") if project.environment else {},
                    indent=2),
                "---\n\n# Whether a person could open the app, when last measured\n\n"
                + (json.dumps(project.state.preview_probe.model_dump(mode="json"), indent=2)
                   if project.state.preview_probe
                   else "(not measured -- the environment above says nothing a person opens, "
                        "or no reading has run since it did.)"),
                "---\n\n# Each check's settings, as they read now\n\n"
                "Read whether or not they moved. A rule set switched on here that answers "
                "another family's question -- security rules in a linter, accessibility rules "
                "loaded into it -- belongs in that check's `also`; where `also` is empty or "
                "short of what is switched on, that is a `change`, with the command untouched. "
                "And a suggestion on record whose rules are already switched on here is done: "
                "drop it, do not restate it.\n\n"
                + (_check_settings(project) or "(no check records where its settings live)"),
                "---\n\n# This repository's guides\n\n"
                "Read by code from the repository: what is committed is what binds.\n\n"
                + self._guide_text(project),
                "---\n\n# Checks this project has turned down\n\n"
                + (_declined_checks(project)
                   or "(none -- nothing has been declined here)"),
                "---\n\n# Suggestions this project has turned down\n\n"
                + (_declined_suggestions(project)
                   or "(none -- nothing has been turned down here)"),
                "---\n\n# Suggestions on record now\n\n"
                + (_current_suggestions(self.registry, project)
                   or "(none)"),
                "---\n\n# What the verify lane has asked this project for\n\n"
                + (_capability_requests(project)
                   or "(nothing outstanding -- no blind suite has reported a capability "
                      "it needed and did not have.)"),
                "---\n\n# Tooling files that moved since the last reading\n\n"
                "Only tooling is tracked here -- manifests, lockfiles, runner and CI "
                "config. Source and test files are not listed even when they changed, "
                "so \"none\" below is not \"nothing changed\": read the tests again for "
                "anything that depends on them.\n\n"
                + f"Since: {drift.get('since') or 'unknown'} "
                + f"({drift.get('commits', 0)} commit(s))\n"
                + f"- appeared: {', '.join(drift.get('added') or []) or 'none'}\n"
                + f"- changed: {', '.join(drift.get('changed') or []) or 'none'}\n"
                + "- present but never read by the last survey: "
                + (", ".join(drift.get("unseen") or []) or "none"),
                "---\n\n# Those files\n\n" + read_paths(project.repo_path, moved),
                *own_image_files(project, moved),
            ])
            diff: SurveyDiff = await self.llm.ask(
                "resurvey", prompt, SurveyDiff,
                system=self.config.role_prompt("resurvey"),
            )
        except Exception as exc:
            project.store.append("resurvey_failed", {"error": str(exc)}, role="orchestrator")
            self._emit(project, "resurvey", "failed", str(exc))
            raise
        # A rule that runs a test file and cannot say which test in it failed.
        # The reading above cannot fix that: it is one answer over a digest, and
        # nothing in a digest says which flags a runner takes. So an agent asks
        # the runner in the project's own container, the canary proves what it
        # found, and a proven command joins this diff as one more card. Never
        # the reason a reading fails: the reading stands without it.
        try:
            self._emit(project, "resurvey", "running", "asking the test runners about reports")
            found = await reporting.search(project, self.config, self.llm)
        except Exception as exc:                                  # noqa: BLE001
            project.store.append("report_search_failed", {"error": f"{type(exc).__name__}: {exc}"},
                                 role="orchestrator")
            found = None
        if found is not None and found.proposes:
            _propose_reports(diff, found)
        # What this reading was actually shown, recorded on the diff rather than
        # recomputed when a human rules -- a request raised by a feature that
        # finishes in between must not be retired by a ruling that never saw it.
        diff.considered = [
            CapabilityRequest(need=r["need"],
                              features=[c["feature_id"] for c in r["cost"]])
            for r in unmet_capabilities(project)
        ]
        # Everything it proposed that is already true, taken out before a human
        # sees it. Recorded rather than silently discarded: code deciding what
        # does not reach a reader is exactly the power this system keeps on a
        # short leash, so what it dropped and why is on the ledger beside the
        # reading it dropped it from.
        dropped = strip_unchanged(diff, project.state)
        if dropped:
            project.store.append("resurvey_unchanged", {"dropped": dropped},
                                 role="orchestrator")
        # DESIGN.md's own linter, offered as a check once there is one; and,
        # once enough features have run, AGENTS.md sections drafted from what
        # their scouts kept seeing -- never committed until a person approves.
        self.registry.raise_guide_checks(project)
        try:
            await self._offer_guide_draft(project)
        except Exception as exc:                                  # noqa: BLE001
            project.store.append("guide_draft_failed", {"error": f"{type(exc).__name__}: {exc}"},
                                 role="orchestrator")
        project.store.append("resurvey", diff, role="resurvey",
                             model=self.config.role("resurvey").model,
                             meta={**reading_cost(self.llm, "resurvey", prompt),
                                   "since": drift.get("since", ""),
                                   "tooling_files": len(moved),
                                   "dropped_as_unchanged": len(dropped),
                                   "requests_shown": len(diff.considered)})
        # A reading that proposes nothing has still read the repository, and the
        # gate list it looked at is the one in front of you. Stamping here is
        # what stops "the tooling moved" standing over a project whose answer
        # was "and it still checks itself the same way".
        if not diff.gate_changes and diff.environment is None and diff.preview is None:
            self.registry.record_reading(project)
        self._emit(project, "resurvey", "done",
                   f"{len(diff.gate_changes)} proposed change(s)")
        return diff

    # -- the human's gate -------------------------------------------------

    def approve(self, project: Project) -> Project:
        """Gate 0. Nothing is built in a project until somebody has looked at
        its gates, its environment, and its baseline."""
        project = self.registry.approve(project)
        self._emit(project, "approval", "done")
        return project

    def accept(self, project: Project) -> tuple[Project, dict[str, Any] | None]:
        """"Accept checks": approve them, then move the Dockerfile they run
        in into the repository. See `move_dockerfile_on_acceptance`."""
        project = self.approve(project)
        return project, self.registry.move_dockerfile_on_acceptance(project)

