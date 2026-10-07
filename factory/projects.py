"""Projects: a repository, how to test it, and what it needs to run.

A project is created at runtime by pointing the factory at a directory, not by
editing YAML. That makes it evidence rather than configuration, so it lives
under the same append-only discipline as everything else: a project's history
is a ledger, and a change of environment or gates is a new record rather than
an edit. (INV-2)

Layout:

    <evidence>/<project_id>/evidence.jsonl                        the project ledger
    <evidence>/<project_id>/features/<feature_id>/evidence.jsonl   one per feature
    <sandboxes>/<project_id>/<feature_id>/                         one worktree per feature

Models are deliberately not part of a project. INV-7 says model identifiers live
in exactly one file, and fragmenting them across projects would end that.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, TypeAdapter

from .git import current_branch
from .schemas import (
    COVERAGE_FORMATS,
    DESCRIPTIVE_ENV_FIELDS,
    DESCRIPTIVE_GATE_FIELDS,
    GATE_FIELDS_SINCE_APPROVAL,
    STATE_VERSION,
    _now,
    CapabilityRuling,
    Diagnosis,
    EnvironmentSpec,
    FamilyRuling,
    GateRuling,
    Gate,
    KeptCheck,
    GateReport,
    ProjectState,
    ProjectSurvey,
    Recommendation,
    RecommendationRuling,
    ScaffoldFile,
    SurveyDiff,
)
from .gates import EXIT_CODE_METRIC, is_green, unrunnable
from .sandbox import Sandbox
from .workspace import (
    _is_test_path,
    commit_paths,
    is_setup_path,
    head_sha,
    PathEscape,
    safe_join,
    tooling_drift,
    launched_scripts,
    tooling_paths,
)
from .store import EvidenceStore, list_feature_ids


class ProjectError(RuntimeError):
    pass


def project_slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "project").lower()).strip("-")
    return slug[:48].strip("-") or "project"


#: Where a project keeps the Dockerfile its checks and agents run in. In the
#: repository, beside the code it builds, so it is edited, reviewed and
#: versioned like any other file.
REPO_DOCKERFILE = ".fabrika/Dockerfile"
#: The environment kinds that are built from a Dockerfile. `compose` only when
#: its check service needs a toolchain its own image lacks.
_BUILT_KINDS = ("derive", "generate", "compose")


def repo_dockerfile(repo: str | Path, ref: str, path: str = REPO_DOCKERFILE) -> str | None:
    """A Dockerfile as committed at `ref` -- `.fabrika/Dockerfile` unless the
    environment names the project's own -- or None when it is not there.

    Committed, not the working copy: every feature branches from a commit, and
    an environment half-edited in somebody's working tree is not one a feature
    was built against. The same rule that makes uncommitted code invisible to
    a feature makes an uncommitted Dockerfile invisible to its checks.
    """
    try:
        r = subprocess.run(["git", "show", f"{ref}:{path}"],
                           cwd=Path(repo).expanduser(), capture_output=True, text=True,
                           timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 and r.stdout.strip() else None


class Project:
    """One registered repository and everything the factory knows about it."""

    def __init__(self, state: ProjectState, evidence_root: Path) -> None:
        self.state = state
        self.evidence_root = Path(evidence_root)
        #: Where `environment.dockerfile` came from: `project` when it is a stage
        #: of the project's own Dockerfile (`dockerfile_path`), `repo` when it
        #: was read from `.fabrika/Dockerfile`, both at the base branch; and
        #: `fabrika` when it is the copy on this record (a survey's proposal not
        #: yet moved in).
        self.dockerfile_source = "fabrika"
        #: This record's own copy, kept as it was while the repository's is in
        #: use, so saving never writes the repository's text into the record.
        self.stored_dockerfile = ""
        #: The repository's text as loaded, so saving can tell it from a
        #: Dockerfile somebody has since put on the record.
        self.loaded_dockerfile = ""

    def adopt_repo_dockerfile(self) -> None:
        """Use the repository's Dockerfile when it has one.

        The record's copy stays the fallback -- a survey's proposal, until it is
        moved in -- and is what a project whose file is later deleted falls back
        to, rather than an environment with no Dockerfile at all.
        """
        env = self.state.environment
        own = (getattr(env, "dockerfile_path", "") or "").strip() if env is not None else ""
        if env is None or (env.kind not in _BUILT_KINDS and not own):
            return
        text = repo_dockerfile(self.repo_path, self.base_ref, own or REPO_DOCKERFILE)
        if text is None:
            return
        self.stored_dockerfile = env.dockerfile
        env.dockerfile = self.loaded_dockerfile = text
        self.dockerfile_source = "project" if own else "repo"

    # -- identity ---------------------------------------------------------

    @property
    def id(self) -> str:
        return self.state.project_id

    @property
    def repo_path(self) -> Path:
        return Path(self.state.repo).expanduser().resolve()

    @property
    def base_ref(self) -> str:
        return self.state.base_ref or "HEAD"

    @property
    def gates(self) -> list[Gate]:
        """Every check on the list, whatever it did last time.

        This is what the baseline measures, what a reading is shown, and what
        the environment is checked against. A parked check has to stay in all
        three or it can never come back: nothing would ever re-run it, so
        nothing would ever notice it had gone green.
        """
        return list(self.state.gates)

    @property
    def parked_checks(self) -> list[str]:
        """Checks that were not green when the baseline was last measured.

        They are still on the list, still run when the checks are run, and still
        drawn on gate 0 -- what they are not is part of what judges a feature.

        This is the middle state the project had no word for. `declined` says
        the check is wrong for this project; green says it is passing. A check
        that is right and does not pass yet was neither, and the only way past
        it was to declare it wrong -- which meant a repository with pre-existing
        failures could not use this tool to clean itself up, the exact deadlock
        the approval rule was written to remove and then reintroduced.

        Derived, never stored, so it arms itself: the run that finds it green
        stops it being parked, with nobody having to remember.
        """
        baseline = self.state.baseline
        if baseline is None:
            return []
        # Measured and not green. A check the baseline says nothing about is
        # unmeasured, not red -- it was added or renamed since the last run --
        # and parking it would quietly drop a check nobody has decided anything
        # about. Unmeasured judges; `approve` refuses while any check is in that
        # state, which is the right place to insist on it.
        results = {r.name: r for r in baseline.results}
        return [g.name for g in self.state.gates
                if g.name in results and not is_green(results[g.name])]

    def check_kinds(self, heavy_after_s: float = 120.0) -> dict[str, str]:
        """What each check is: `fixer`, `light` or `heavy`. (See the plan.)

        A fixer rewrote files on an untouched checkout at baseline; it runs
        first every round and is committed, never judged by. Otherwise a human's
        choice stands, and without one the latest timing decides -- a final
        pass's, or the baseline's.
        """
        base = {r.name: r for r in (self.state.baseline.results if self.state.baseline else [])}
        timing: dict[str, float] = {}
        learned: set[str] = set()
        for record in self.store:
            if record.get("kind") == "check_timing":
                timing.update(record.get("payload") or {})
            elif record.get("kind") == "check_fixer":
                # Caught rewriting files during a build: a formatter on a repository
                # that was already formatted looks like any check at baseline.
                learned.add((record.get("payload") or {}).get("name", ""))
        out: dict[str, str] = {}
        for gate in self.state.gates:
            measured = base.get(gate.name)
            if gate.name in learned or (measured is not None and measured.rewrote):
                out[gate.name] = "fixer"
                continue
            chosen = self.state.check_runs.get(gate.name)
            if chosen:
                out[gate.name] = chosen
                continue
            took = timing.get(gate.name, measured.duration_s if measured else 0.0)
            out[gate.name] = "heavy" if took and took > heavy_after_s else "light"
        return out

    @property
    def judging_gates(self) -> list[Gate]:
        """What a feature built here is actually held to.

        A parked check is left out rather than run and discounted. Running it
        would put a row in every packet that is red for reasons that have
        nothing to do with the feature -- and a row that is red in every packet
        teaches a reader to skim red, which is the whole argument that made
        requiring green look necessary in the first place. A check nobody is
        being judged by does not belong in the evidence that judges them.
        """
        parked = set(self.parked_checks)
        return [g for g in self.state.gates if g.name not in parked]

    @property
    def environment(self) -> EnvironmentSpec | None:
        return self.state.environment

    @property
    def warm_gates(self) -> list[Gate]:
        """The checks every setup runs once with the network. See `warm_checks`."""
        wanted = set(self.state.warm_checks)
        return [g for g in self.state.gates if g.name in wanted]

    @property
    def store(self) -> EvidenceStore:
        """The project's own ledger."""
        return EvidenceStore(self.evidence_root, self.id)

    @property
    def features_root(self) -> Path:
        return self.evidence_root / self.id / "features"

    def feature_store(self, feature_id: str) -> EvidenceStore:
        return EvidenceStore(self.features_root, feature_id)

    def feature_ids(self) -> list[str]:
        return list_feature_ids(self.features_root)

    # -- readiness --------------------------------------------------------

    def require_ready(self) -> None:
        """A feature cannot start in a project whose baseline was never proved.

        If the gates are red on an untouched checkout, every failure a feature
        produces is unattributable, and the packet built from it is noise
        wearing the costume of evidence.
        """
        if self.state.stage == "ready":
            return
        raise ProjectError(
            f"project {self.id!r} is {self.state.stage}, not ready. "
            "Run its checks on an untouched checkout and approve the result before starting "
            "features in it."
        )


def watched_paths(project: Project) -> list[str]:
    """Every file whose change should send this project back for a re-reading.

    `tooling_paths` finds configuration by glob -- manifests, CI, `*.ini`,
    Dockerfiles. That is the right net for "a new tool arrived" and the wrong one
    for "what a test can be written against changed", because a project's setup
    is ordinary source: `conftest.py`, `factories.py`, `test-utils.tsx`. None of
    them match a config glob, so none of them were watched.

    The consequence is quiet and total. A feature adds the domain factories the
    verify lane has been asking for; drift reports nothing, because nothing it
    watches moved; no re-survey happens; `testing.fixtures` never records them;
    and every future blind author is told the project has no way to arrange
    state. The factories sit in the repository, correct and unused, for ever.

    So the survey's own answers are added to the net: the setup files it recorded
    and the scaffolding a human applied. Not guessed at by pattern -- named, by
    the reading that said they were setup in the first place.
    """
    paths = set(tooling_paths(project.repo_path)) | set(launched_scripts(project.repo_path))
    surface = getattr(project.state, "testing", None)
    for tier in getattr(surface, "tiers", None) or ():
        for setup in tier.setup_files or ():
            if (setup.path or "").strip():
                paths.add(setup.path.strip())
    paths.update(p for p in (project.state.scaffolding_applied or ()) if p)
    return sorted(paths)


def proposed_scaffolding(project: Project) -> dict[str, ScaffoldFile]:
    """Every scaffolding file on offer, newest proposal winning.

    Scaffolding is keyed by path and was never updated: `apply_survey_diff`
    merged only paths it did not already have, and `apply_scaffolding` read the
    survey's copy. So a re-reading that proposed a NEW version of a file already
    on the list -- one line added to a conftest -- was shown to a human from the
    proposal and written from the survey. The two differed. The bytes written
    matched what was already on disk, the write was correctly skipped as a
    no-op, and the button appeared to hang while a three-minute re-read ran over
    a repository nothing had changed in.

    Ledger order, so the last reading wins. The survey is the floor.
    """
    files: dict[str, ScaffoldFile] = {}
    survey = project.state.survey
    for f in (survey.scaffolding if survey else []):
        files[f.path] = f
    for record in project.store:
        if record["kind"] != "resurvey":
            continue
        for raw in (record.get("payload") or {}).get("scaffolding") or []:
            try:
                f = ScaffoldFile.model_validate(raw)
            except Exception:  # noqa: BLE001 -- a malformed record is not fatal here
                continue
            files[f.path] = f
    return files


#: The parts of a reading that are not checks. Each is ruled on by itself, on
#: the page it would change -- the testing surface, how a test file runs, where
#: runs are recorded, where blind tests go and the files a project needs on
#: Tests; what opens at review and the environment on Environment -- as each
#: check is in its row. On the Survey tab, under one Apply, they read as changes
#: to the survey.
PROPOSAL_PARTS = ("testing", "test_file_commands", "trace_dirs", "blind_placements",
                  "scaffolding", "preview", "environment")

#: The parts an accepted ruling can be put back from: fields of the record.
#: The environment can write the repository's Dockerfile and scaffolding is
#: an offer of files, so neither is undone here; each is edited instead.
UNDOABLE_PARTS = ("testing", "test_file_commands", "trace_dirs", "blind_placements")


def _part_now(project: Project, part: str) -> Any:
    state = project.state
    if part == "preview":
        return state.environment.preview if state.environment else None
    return getattr(state, part, None)


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def proposed_parts(project: Project, diff: SurveyDiff) -> list[str]:
    """The parts of a reading that would change something if accepted.

    A part restated exactly as recorded is not a proposal, and a file already
    offered, or already on disk as proposed, is not one either. An environment
    is compared on what the reading said: it never carries the built image.
    """
    on_disk = scaffolding_state(project)
    offered = {f.path: f for f in (project.state.survey.scaffolding if project.state.survey else [])}
    in_fix = {path for t in (diff.testing.tiers if diff.testing else [])
              for o in (t.cleanup_options or []) for path in (o.files or [])}
    out: list[str] = []
    for part in PROPOSAL_PARTS:
        value = getattr(diff, part, None)
        if not value:
            continue
        if part == "scaffolding":
            if all(f.path in in_fix or offered.get(f.path) == f or on_disk.get(f.path) == "same"
                   for f in value):
                continue
        elif part == "environment":
            now = _plain(_part_now(project, part)) or {}
            said = {k: v for k, v in _plain(value).items() if v not in (None, "", [], {})}
            if all(now.get(k) == v for k, v in said.items()):
                continue
        elif _plain(value) == _plain(_part_now(project, part)):
            continue
        out.append(part)
    return out


def scaffolding_state(project: Project) -> dict[str, str]:
    """For each proposed file: `missing`, `same`, or `differs`.

    Three states and the screen could see two of them, so it drew the same card
    for a file that needs replacing and a file already replaced -- and pressing
    the second wrote nothing, correctly, which from the outside is a button that
    does not work. Nothing else on this page can answer it: the console never
    sees the repository.
    """
    state: dict[str, str] = {}
    for path, spec in proposed_scaffolding(project).items():
        try:
            target = safe_join(project.repo_path, path)
        except PathEscape:
            continue
        if not target.exists():
            state[path] = "missing"
            continue
        try:
            state[path] = "same" if target.read_text(encoding="utf-8") == spec.contents else "differs"
        except (OSError, UnicodeDecodeError):
            state[path] = "differs"
    return state


def unmet_capabilities(project: Project, limit: int = 12) -> list[dict[str, Any]]:
    """What the verify lane has asked this project for and not been given.

    The blind test author reports, every run, the capabilities it needed and did
    not have -- a disposable database it may migrate, a documented way to make a
    referral, a runner that can drive a browser. Each one names the criteria it
    cost. They reach the packet and a human reads them; without this the trail
    ends there: nothing carries them into the next reading, so the role that
    could actually propose the missing file is never told the request exists.

    A project can then go run after run with the same requests outstanding and
    the same criteria permanently unverifiable, while its re-survey reports the
    repository unchanged -- which it is. The gap is not in the repository.

    Deduplicated by the capability rather than by the run, and counted: a need
    that four features have hit is a different proposition from one that came up
    once, and the count is the part a human weighs.

    Criterion ids stay attached to the feature that raised them, and are never
    merged. `AC-1` is sequential from one within a single spec, so every feature
    has one: a flat list of ids across features reads as though they were one
    numbering, and a project-level field citing a bare `AC-23` is meaningless to
    anyone reading it after that feature is gone.
    """
    seen: dict[str, dict[str, Any]] = {}
    for feature_id in project.feature_ids():
        try:
            store = project.feature_store(feature_id)
            records = store.records()
        except Exception:  # noqa: BLE001 -- a half-written feature is not fatal here
            continue
        latest = None
        for record in records:
            if record.get("kind") == "oracle":
                latest = record.get("payload") or {}
        for entry in (latest or {}).get("requires") or []:
            need = str((entry or {}).get("need") or "").strip()
            if not need:
                continue
            key = need.lower()
            slot = seen.setdefault(key, {"need": need, "cost": [], "detail": ""})
            ids = [str(c) for c in (entry.get("criterion_ids") or []) if str(c).strip()]
            existing = next(
                (c for c in slot["cost"] if c["feature_id"] == feature_id), None)
            if existing is None:
                slot["cost"].append({"feature_id": feature_id, "criterion_ids": ids})
            else:
                for cid in ids:
                    if cid not in existing["criterion_ids"]:
                        existing["criterion_ids"].append(cid)
            if not slot["detail"]:
                slot["detail"] = str(entry.get("detail") or "")
    # What a human has already answered comes off the list -- but only for the
    # features that raised it. These requests live in append-only ledgers, so
    # nothing could ever retire one: a project answered "a runner that can drive
    # a browser" by putting Playwright in the repository and was handed the same
    # request at the next reading, and every reading after it. A new feature
    # hitting the same wall brings it straight back, which is the one case worth
    # hearing about again: it means the answer did not work.
    ruled = {r.need.strip().lower(): set(r.features)
             for r in (project.state.capability_rulings or [])}
    live = []
    for entry in seen.values():
        covered = ruled.get(entry["need"].strip().lower())
        raised_by = {c["feature_id"] for c in entry["cost"]}
        if covered is not None and raised_by <= covered:
            continue
        live.append(entry)
    ordered = sorted(live, key=lambda e: (-len(e["cost"]), e["need"]))
    return ordered[:limit]


def cleanup_option_problems(diff: Any, option: Any) -> str:
    """Why this option could not be applied whole, or "".

    An option is only worth offering if every part of it exists in the same
    reading: its files proposed, its environment proposed, its feature a
    recommendation there. A part that is missing would leave a person holding
    half a fix -- a reset step with no script, or a script with no step.
    """
    offered = {f.path: f for f in (diff.scaffolding or [])}
    missing = [f for f in option.files if f not in offered]
    if missing:
        return f"it writes {', '.join(missing)}, which this reading does not propose"
    # The same rule the write is held to. Checked here too, so an option that
    # would be refused at apply is never put in front of a person: one was, and
    # choosing the recommended fix failed with "scaffolding may not write tests".
    refused = ProjectRegistry.scaffold_problems([offered[f] for f in option.files])
    if refused:
        return refused[0]
    if option.environment and diff.environment is None:
        return "it changes the environment, and this reading proposes no environment"
    if option.agent_prompt and option.agent_prompt not in {
            r.title for r in (diff.recommendations or [])}:
        return f"its prompt, {option.agent_prompt!r}, is not a recommendation in this reading"
    if not (option.files or option.environment or option.agent_prompt):
        return "it does nothing"
    return ""


def chosen_cleanup(diff: Any, choices: Mapping[str, int | None]) -> list[tuple[str, Any]]:
    """The options a person picked, by tier. `None` is "leave it"."""
    tiers = {t.tier: t for t in ((diff.testing.tiers if diff.testing else []) or [])}
    out: list[tuple[str, Any]] = []
    for tier, index in (choices or {}).items():
        if index is None:
            continue
        options = tiers[tier].cleanup_options if tier in tiers else []
        if not 0 <= index < len(options):
            raise ProjectError(f"there is no option {index} for {tier} tests in this reading")
        if not (options[index].files or options[index].environment):
            # A code fix is a prompt the person runs with their own agent; there
            # is nothing here to apply.
            raise ProjectError(f"{options[index].title!r} is done with a coding agent, not "
                               "applied here: copy its prompt, then re-survey")
        out.append((tier, options[index]))
    return out


def unwritten_references(project: "Project", environment: Any,
                         proposed: Sequence[str]) -> list[str]:
    """Proposed files an environment's commands run that are not on disk yet.

    Matched by the path's tail, because a command usually `cd`s first: the file
    `api/scripts/reset_demo_data.py` is run as `python scripts/reset_demo_data.py`.
    A tail of one part -- a bare filename -- is too loose to mean anything.
    """
    commands = [*(environment.setup or []), *(environment.test_prepare or []),
                *(s.command for s in (environment.services or []))]
    root = Path(project.state.repo)
    out: list[str] = []
    for path in dict.fromkeys(proposed):
        if (root / path).is_file():
            continue
        parts = PurePosixPath(path).parts
        tails = ["/".join(parts[i:]) for i in range(0, max(1, len(parts) - 1))]
        if any(t in command for command in commands for t in tails):
            out.append(path)
    return out


class ProjectRegistry:
    """Storage and identity for projects. Onboarding lives in the pipeline."""

    def __init__(self, evidence_root: str | Path) -> None:
        self.evidence_root = Path(evidence_root).expanduser().resolve()
        self.evidence_root.mkdir(parents=True, exist_ok=True)

    # -- read -------------------------------------------------------------

    def ids(self) -> list[str]:
        return list_feature_ids(self.evidence_root)

    def get(self, project_id: str) -> Project:
        store = EvidenceStore(self.evidence_root, project_id)
        payload = store.payload("project")
        if payload is None:
            raise ProjectError(f"no project {project_id!r}")
        project = Project(ProjectState.model_validate(payload), self.evidence_root)
        project.adopt_repo_dockerfile()
        return project

    def list(self) -> list[Project]:
        out: list[Project] = []
        for project_id in self.ids():
            try:
                out.append(self.get(project_id))
            except ProjectError:
                continue
        out.sort(key=lambda p: p.state.updated_at, reverse=True)
        return out

    def exists(self, project_id: str) -> bool:
        try:
            self.get(project_id)
            return True
        except ProjectError:
            return False

    # -- write ------------------------------------------------------------

    def create(self, repo: str | Path, name: str = "") -> Project:
        """Register a directory. The survey and the environment come later."""
        repo_path = Path(repo).expanduser().resolve()
        if not repo_path.is_dir():
            raise ProjectError(f"not a directory: {repo_path}")

        name = (name or repo_path.name).strip()
        project_id = project_slug(name)
        if self.exists(project_id):
            existing = self.get(project_id)
            raise ProjectError(
                f"project {project_id!r} already exists, pointing at {existing.state.repo}. "
                "Pick a different name if this is a different repository."
            )

        state = ProjectState(
            project_id=project_id, name=name, repo=str(repo_path), stage="surveying",
        )
        project = Project(state, self.evidence_root)
        self.save(project, note="registered")
        return project

    def save(self, project: Project, *, note: str = "", meta: dict[str, Any] | None = None) -> Project:
        """A new record, never an edit. `latest` wins. (INV-2)

        Refuses when this code is older than the state it was handed.

        A process holds its models for as long as it runs, and a console server
        runs for days. One started before `services`, `test_prepare` and
        `test_file_commands` existed kept writing state without them -- Pydantic
        drops what it does not recognise -- so an approve, a re-survey and a
        restore each silently removed the three fields that say how to prepare a
        database. The next build ran against a project with no migrations to
        apply: no schema, no application role, every gate red on `password
        authentication failed`, and a packet that read as a broken feature.

        `extra="allow"` on the state stops the fields being destroyed. It cannot
        stop an old process *acting* on state it only half understands, which is
        the half that produced the wrong answers. So the write is refused, with
        the one sentence that fixes it.

        This protects only against code that carries this check, which is the
        most any version stamp can do. What it buys is that the next time state
        and code drift apart, somebody is told instead of reading a packet about
        a repository that was never the problem.
        """
        from datetime import datetime, timezone
        stored = getattr(project.state, "state_version", 1)
        if stored > STATE_VERSION:
            raise ProjectError(
                f"project {project.id!r} was last written by a newer version of this "
                f"tool (state v{stored}, this process understands v{STATE_VERSION}). "
                "Writing would silently drop whatever it does not recognise. Restart "
                "this process on the current code and try again."
            )
        project.state.state_version = STATE_VERSION
        project.state.updated_at = datetime.now(timezone.utc).isoformat()
        stored = project.state
        if (project.dockerfile_source != "fabrika" and stored.environment is not None
                and stored.environment.dockerfile == project.loaded_dockerfile):
            # The repository's text is read on every load and never stored:
            # a record holding it would be a second copy, and the two would drift.
            stored = stored.model_copy(deep=True)
            stored.environment.dockerfile = project.stored_dockerfile
        project.store.append(
            "project", stored, role="orchestrator",
            meta={"note": note, **(meta or {})},
        )
        return project

    @staticmethod
    def environment_problems(
        env: EnvironmentSpec | None, gates: list[Gate], repo: Path | None = None,
    ) -> list[str]:
        """Incoherences a human should see at gate 0, not discover when a
        container fails to start three phases into a run.

        Only structural faults are returned here, because this list disables the
        baseline button. Things that are merely suspect go in `environment_notes`.
        """
        problems: list[str] = ProjectRegistry.gate_problems(gates, repo)
        if env is None:
            return problems + ["no environment was proposed"]
        if repo is not None:
            problems += ProjectRegistry.working_directory_problems(env, gates, repo)
        if env.kind == "reuse" and not env.image.strip() and not env.dockerfile_path.strip():
            problems.append(
                "kind is 'reuse', which means an existing image runs the gates, but no image is "
                "named. Name one, or derive from it instead."
            )
        own = (getattr(env, "dockerfile_path", "") or "").strip()
        if own and not env.dockerfile.strip():
            problems.append(
                f"the environment builds from {own}, which is not committed on the branch "
                "features start from. Commit it there, or survey again.")
        elif env.kind in ("derive", "generate") and not env.dockerfile.strip():
            problems.append(f"kind is {env.kind!r} but the Dockerfile is empty.")
        if env.kind == "compose":
            # The two things a compose environment cannot be run without: which
            # file declares the services, and which of them the gates run in.
            if not env.compose_file.strip():
                problems.append(
                    "kind is 'compose', which runs the gates against the project's own "
                    "services, but no compose file is named."
                )
            elif repo is not None and not (repo / env.compose_file).is_file():
                problems.append(
                    f"the compose file {env.compose_file!r} does not exist in the repository."
                )
            if not env.compose_service.strip():
                problems.append(
                    "kind is 'compose' but no service is named for the gates to run in. It "
                    "should be the one carrying the project's toolchain."
                )
            elif repo is not None:
                problems += ProjectRegistry._compose_service_problems(
                    repo / env.compose_file, env.compose_service, bool(env.dockerfile.strip()))
        # A Dockerfile begins with FROM. A survey once put the *path*
        # `backend/Dockerfile` in this field, which reached `docker build` as the
        # file's contents and failed with "unknown instruction: backend/Dockerfile"
        # three phases later. The field takes contents; saying so here costs one
        # comparison and saves a build.
        body = env.dockerfile.strip()
        if body and not re.search(r"^\s*FROM\s+\S", body, re.IGNORECASE | re.MULTILINE):
            looks_like_path = "\n" not in body and len(body) < 200
            problems.append(
                "the Dockerfile has no FROM instruction, so it is not a Dockerfile"
                + (f" -- {body!r} looks like a path, and this field takes the file's contents."
                   if looks_like_path else ".")
            )
        # Instructions only. This read the whole file including comments, so a
        # Dockerfile that correctly does NOT copy the source and says why --
        # "the project's own image does `COPY . .`, which the mount would shadow
        # anyway" -- was rejected for the sentence explaining that it got it
        # right. In Dockerfile syntax a comment is a line whose first non-space
        # character is `#`; there is no inline comment, so this is exact.
        instructions = "\n".join(
            line for line in env.dockerfile.splitlines() if not line.lstrip().startswith("#"))
        target = (getattr(env, "dockerfile_target", "") or "").strip()
        if target and env.dockerfile.strip():
            # The project's own file: only the stage that is built is ours to
            # judge. Its production stage copies the source, as it should.
            stage = dockerfile_stage(instructions, target)
            if stage is None:
                problems.append(f"{own or 'the Dockerfile'} has no stage named {target!r}.")
            instructions = stage or ""
        if "COPY . " in instructions or "COPY ./" in instructions:
            problems.append(
                "the Dockerfile copies the source tree. The feature worktree is mounted at the "
                "workdir at run time and would shadow it."
            )

        # A gate the image cannot run is red forever and reads as a code failure.
        # Scanned across the whole command: `cd frontend && npx tsc` hides the
        # tool behind a `cd`, which is how most real gate commands are written.
        # Comments stripped here for the opposite reason: this asks whether a
        # tool is present, and a comment saying "there is no node in this image"
        # would answer yes. The generous direction, and the one that lets a gate
        # through that cannot run.
        installed = (instructions + " " + " ".join(env.setup)).lower()
        for gate in gates:
            for tool, marker in (("npm", "node"), ("npx", "node"), ("yarn", "yarn"),
                                 ("pnpm", "pnpm"), ("node", "node")):
                if re.search(rf"(^|[\s;&|]){tool}\s", gate.command.lower()) and marker not in installed:
                    problems.append(
                        f"gate {gate.name!r} runs {tool!r}, which nothing in this environment installs."
                    )
                    break
        problems += ProjectRegistry.preview_problems(env)
        return problems

    @staticmethod
    def preview_problems(env: EnvironmentSpec) -> list[str]:
        """What stops a declared preview from being opened, whatever the code does."""
        preview = getattr(env, "preview", None)
        if preview is None:
            return []
        problems: list[str] = []
        own = [s.name for s in env.services]
        extra = [s.name for s in preview.services]
        for name in sorted({n for n in extra if n in own}):
            problems.append(
                f"the preview declares a service {name!r}, which the environment already "
                "starts. Declare it once, in the environment's services.")
        if preview.open and preview.open not in own + extra:
            problems.append(
                f"the preview opens {preview.open!r}, which is not a service here. Name one "
                "of: " + (", ".join(own + extra) or "none are declared") + ".")
        if preview.open and not preview.path.startswith("/"):
            problems.append(f"the preview's path {preview.path!r} must start with '/'.")
        return problems

    #: A tool whose error-count summary is printed only in a mode no gate gets:
    #: the flag that would restore it, and the shape of the line it would print.
    #: `tsc` prints "Found N errors" only under `--pretty`, and turns `--pretty`
    #: off by itself whenever stdout is not a terminal. A gate's stdout is a
    #: pipe, always -- so on the command as written that line does not exist to
    #: be read.
    #:
    #: Matched against the pattern as well as the command because a gate command
    #: is often a chain, and the tool that cannot print a summary may not be the
    #: one the number was coming from: `tsc --noEmit && vitest --coverage` reads
    #: its percentage from vitest, and tsc's silence is nothing to do with it.
    _SUMMARY_NEEDS_FLAG = {
        "tsc": ("--pretty", re.compile(r"found\b.{0,20}\berror", re.I | re.S)),
    }

    @staticmethod
    def _script_body(command: str, repo: Path | None) -> str:
        """`npm run typecheck` with the script it names substituted in.

        A gate rarely calls the tool it runs. It calls the package script that
        calls it, which is the form CI uses and therefore the form a survey
        copies -- and `npm run typecheck` names no tool at all, so every check
        that reads a command for the tool inside it sees nothing here.

        Unresolvable is the command itself: a repo we were not handed, a script
        that is not in package.json. Returning it unchanged means the checks
        downstream find no tool and say nothing, which is the direction that
        lets a gate through rather than the one that invents a fault.
        """
        match = re.match(
            r"\s*(?:cd\s+([\w./-]+)\s*&&\s*)?(?:npm|pnpm)\s+run\s+([\w:.-]+)"
            r"|\s*(?:cd\s+([\w./-]+)\s*&&\s*)?yarn\s+(?!run\b)([\w:.-]+)",
            command,
        )
        if not match or repo is None:
            return command
        here = match.group(1) or match.group(3) or ""
        script = match.group(2) or match.group(4) or ""
        manifest = repo / here.strip("/") / "package.json"
        if not manifest.is_file():
            return command
        try:
            scripts = json.loads(manifest.read_text()).get("scripts") or {}
        except (json.JSONDecodeError, OSError, AttributeError):
            return command
        body = scripts.get(script)
        return f"{command} && {body}" if isinstance(body, str) and body else command

    @staticmethod
    def gate_problems(gates: list[Gate], repo: Path | None = None) -> list[str]:
        """Gates that cannot produce a result, whatever the code does.

        A metric exists for a number the exit code cannot carry -- a coverage
        percentage, a mutation score. It is read with a regex, and the number is
        whatever the first capture group holds. A pattern with no capture group
        therefore matches text and yields nothing, every time, on every run.

        That is not a gate that sometimes fails. Paired with a threshold it is a
        gate that *always* fails, including on a command that exited zero, and
        the packet says the check failed while the tool said it passed. A survey
        once set `parse_metric='errors'` on `tsc --noEmit`, whose exit code was
        already the whole answer, and turned three green commands red.

        A well-formed pattern the tool never prints is the same fault wearing
        better clothes, and it hides for longer: it reads nothing, which on a
        passing run is indistinguishable from there being nothing to read. The
        console tells a human to ratchet a red check by capturing its count and
        setting the ceiling to today's number. Do that to a pattern that cannot
        match and the gate moves straight to always-failing -- so the pattern is
        worth refusing while it is still only carrying a number nobody reads.
        """
        problems: list[str] = []
        for gate in gates:
            # A report and its placeholder come together or not at all. One
            # without the other is a command that writes nowhere anyone reads,
            # or a `{report}` handed to the shell as a literal path.
            asks = "{report}" in (gate.command or "")
            if gate.report_format and not asks and not gate.report_path.strip():
                problems.append(
                    f"gate {gate.name!r} says it writes a {gate.report_format} report, but its "
                    "command has no {report} for the path. Put {report} where the tool's "
                    "output-file flag takes it, or name the file it always writes."
                )
            elif asks and not gate.report_format:
                problems.append(
                    f"gate {gate.name!r} has {{report}} in its command but names no report "
                    "format, so nothing would be written there or read back."
                )
            if gate.patch_max is not None and gate.report_format != "sarif":
                problems.append(
                    f"gate {gate.name!r} is held on new code alone, which needs a SARIF report "
                    "of where each finding is -- and it writes none."
                )
            if gate.files_command.strip() and not (
                    "{paths}" in gate.files_command
                    and ("{report}" in gate.files_command or gate.report_path.strip())):
                problems.append(
                    f"gate {gate.name!r} has a command for some files only, which needs "
                    "{paths} for the files, and {report} -- or the check's fixed report "
                    "path -- for where it writes."
                )
            if gate.patch_min is not None and gate.report_format not in COVERAGE_FORMATS:
                problems.append(
                    f"gate {gate.name!r} holds new lines to a coverage floor, which needs a "
                    "coverage report -- and it writes none."
                )
            pattern = (gate.parse_metric or "").strip()
            if pattern and pattern != EXIT_CODE_METRIC:
                try:
                    compiled = re.compile(pattern)
                except re.error as exc:
                    problems.append(
                        f"gate {gate.name!r} has parse_metric {pattern!r}, which is not a valid "
                        f"regular expression: {exc}"
                    )
                    continue
                if compiled.groups < 1:
                    problems.append(
                        f"gate {gate.name!r} has parse_metric {pattern!r}, which captures nothing. "
                        "The metric is whatever the first capture group holds, so this pattern "
                        "can never read a number"
                        + (f" -- and with threshold {gate.threshold} the gate fails on every run, "
                           "including runs where the command succeeds."
                           if gate.threshold is not None
                           else ". Drop it, or capture the number: e.g. r'(\\d+(?:\\.\\d+)?)%\\s+coverage'.")
                    )
                    continue
                body = ProjectRegistry._script_body(gate.command, repo)
                for tool, (flag, shape) in ProjectRegistry._SUMMARY_NEEDS_FLAG.items():
                    if not shape.search(pattern):
                        continue
                    if not re.search(rf"(^|[\s;&|/]){tool}(\s|$)", body):
                        continue
                    if re.search(rf"{re.escape(flag)}(?:[=\s]+(?!false)|$)", body):
                        continue
                    bound = gate.threshold if gate.threshold is not None else gate.threshold_max
                    problems.append(
                        f"gate {gate.name!r} has parse_metric {pattern!r}, which reads a summary "
                        f"line {tool!r} prints only under {flag}. It turns {flag} off by itself "
                        "when stdout is not a terminal, and a gate's stdout is a pipe, so this "
                        "pattern matches nothing on any run"
                        + (f" -- and the bound of {bound} riding on it cannot be checked, so the "
                           "gate fails every time, including when the command succeeds."
                           if bound is not None
                           else f". Add {flag} to the command if the count is worth having, "
                                "or drop the pattern and let the exit code answer.")
                    )
                    break
            elif gate.threshold is not None and not pattern:
                problems.append(
                    f"gate {gate.name!r} sets a threshold of {gate.threshold} but names no "
                    "parse_metric to read a number from, so the threshold is silently ignored."
                )
        return problems

    @staticmethod
    def _compose_service_problems(
        compose_file: Path, service: str, overriding: bool,
    ) -> list[str]:
        """The gates replace the named service's image. Name the wrong one and
        the thing the tests need stops existing.

        A survey once named `postgres` and supplied a Dockerfile adding Python
        and Node to it. Compose then started that image as the database, its
        `pg_isready` healthcheck failed, and every gate reported "container is
        unhealthy" -- which reads as broken infrastructure rather than as the
        database having been overwritten with a toolchain.

        A service other services depend on is infrastructure. Overriding it is
        never what anyone meant.
        """
        try:
            import yaml
            data = yaml.safe_load(compose_file.read_text(encoding="utf-8")) or {}
        except Exception:
            return []                       # unreadable is reported elsewhere
        services = data.get("services") or {}
        if not isinstance(services, dict) or service not in services:
            return [
                f"the compose file names no service {service!r}. It has: "
                + (", ".join(sorted(services)) or "none")
            ]
        spec = services.get(service) or {}
        # "Depended on" is the wrong test: the application service is usually
        # depended on too. What separates infrastructure is where its image comes
        # from. A service built from this repository is the project's own code; a
        # service pulled from a registry -- `postgres:16-alpine`, `redis:7` -- is a
        # dependency, and replacing its image deletes the thing the tests need.
        from_registry = bool(spec.get("image")) and not spec.get("build")
        if overriding and from_registry:
            buildable = sorted(
                name for name, s in services.items()
                if isinstance(s, dict) and s.get("build")
            )
            return [
                f"the gates are set to run in {service!r}, and this environment supplies a "
                f"Dockerfile, which replaces that service's image. But {service!r} is pulled "
                f"from a registry ({spec.get('image')!r}) rather than built from this "
                "repository: it is a dependency the tests connect to, not the place they run. "
                + (f"Name a service built from the repo instead: {', '.join(buildable)}."
                   if buildable else "No service in this file is built from the repo.")
            ]
        return []

    # A tool that reads its configuration from the working directory, and the
    # file it looks for there. Running one of these from the wrong directory
    # does not produce a smaller answer -- it produces a different one, or none.
    _NEEDS_IN_CWD = {
        "npm": ("package.json",), "npx": ("package.json",), "yarn": ("package.json",),
        "pnpm": ("package.json",),
        "alembic": ("alembic.ini",),
        "mypy": ("mypy.ini", ".mypy.ini", "setup.cfg", "pyproject.toml"),
        "pytest": ("pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"),
        "ruff": ("ruff.toml", ".ruff.toml", "pyproject.toml"),
        "tox": ("tox.ini",),
    }

    @staticmethod
    def working_directory_problems(
        env: EnvironmentSpec | None, gates: list[Gate], repo: Path,
    ) -> list[str]:
        """Commands run from a directory that does not hold their configuration.

        A CI workflow scopes each step with `working-directory:`. Read the
        commands out of one and you get `pytest`, `mypy`, `npm run build` --
        correct commands, stripped of the directory that made them work. Every
        gate here runs from the workdir, so a survey that copies CI faithfully
        and drops that scoping produces a full set of gates where every one
        fails: alembic cannot find alembic.ini, npm cannot find package.json,
        and mypy exits with a usage error rather than a type error.

        Detectable because the file is in the repository, just not where the
        command will look. Where it is says what the command was missing.
        """
        if env is None or not repo.is_dir():
            return []
        problems: list[str] = []

        def effective_dir(command: str) -> str:
            match = re.match(r"\s*cd\s+([\w./-]+)\s*&&", command)
            return match.group(1).strip("/") if match else ""

        for gate in gates:
            here = effective_dir(gate.command)
            body = re.sub(r"^\s*cd\s+[\w./-]+\s*&&", "", gate.command).strip()
            tool = (body.split() or [""])[0].rsplit("/", 1)[-1]
            wanted = ProjectRegistry._NEEDS_IN_CWD.get(tool)
            if not wanted:
                continue
            if any((repo / here / name).is_file() for name in wanted):
                continue
            # Not where it will run. Is it anywhere?
            found = sorted({
                str(hit.parent.relative_to(repo)) or "."
                for name in wanted
                for hit in repo.glob(f"*/{name}")
                if hit.is_file()
            })
            if found:
                problems.append(
                    f"gate {gate.name!r} runs {tool!r} from "
                    + (f"{here!r}" if here else "the repository root")
                    + f", which has no {' or '.join(wanted[:2])}. "
                    f"It is in {', '.join(repr(d) for d in found)} — the command needs to run "
                    f"there: `cd {found[0]} && {body}`."
                )
        return problems

    @staticmethod
    def environment_notes(env: EnvironmentSpec | None, gates: list[Gate], repo: Path) -> list[str]:
        """Advisory: a command naming a file the repository does not have.

        `pip install -r requirements.txt` from a workdir where that file lives
        one directory down is the most common way a baseline fails for a reason
        that has nothing to do with the code.
        """
        if env is None or not repo.is_dir():
            return []

        notes: list[str] = []
        pattern = re.compile(r"[\w./-]+\.(?:txt|ini|toml|cfg|json|yaml|yml)\b")

        def check(label: str, command: str) -> None:
            prefix = ""
            match = re.match(r"\s*cd\s+([\w./-]+)\s*&&", command)
            if match:
                prefix = match.group(1)
            for token in pattern.findall(command):
                if token.startswith(("http", "-")):
                    continue
                if (repo / prefix / token).exists() or (repo / token).exists():
                    continue
                where = f"{prefix}/{token}" if prefix else token
                notes.append(
                    f"{label} refers to {where!r}, which is not in the repository at that path."
                )

        for i, command in enumerate(env.setup):
            check(f"setup[{i}]", command)
        for gate in gates:
            check(f"gate {gate.name!r}", gate.command)
        return notes

    #: Names no test runner collects, whatever is in them. Kept explicit rather
    #: than inferred from the contents: "does this file contain an assertion" is
    #: a different question in every language, and getting it wrong in the
    #: generous direction is how a factory grades itself.
    _PLACEHOLDERS = frozenset({".gitkeep", ".keep", ".gitignore", "README.md"})

    @staticmethod
    def scaffold_problems(files: list[ScaffoldFile]) -> list[str]:
        """The one rule that cannot be left to a prompt.

        A test written by the factory is a test the factory is then graded
        against, and one trivial assertion turns a red baseline green while
        proving nothing -- pytest exits 5 with no tests and 0 with one useless
        one. Scaffolding may install a runner. It may not write the thing the
        runner runs.

        The exception is a short list of names, and it is a list rather than a
        judgement about contents because the danger is exact: a file some runner
        *collects* and counts as a passing test. Nothing in `_PLACEHOLDERS` is
        collected by any runner, whatever is written in it.

        Setup is the other exception. A runner does not collect `conftest.py`
        or `factories.py`, so neither can be the phantom green this rule exists
        to prevent -- and a test directory is exactly where both belong, which
        is why the surveyor is told to propose them and draws its own line at
        "whether the file makes a claim about behaviour". A rule drawn at the
        path instead breaks a set apart: of `api/tests/factories.py`,
        `api/tests/acceptance/conftest.py` and `web/src/testing/factories.ts`
        proposed as a cross-referencing set, it refuses the first two and keeps
        the third -- on the spelling of its directory -- whose purpose then
        opens "The web equivalent of the same gap", naming a gap no longer on
        the page. The names come from `_SETUP_NAMES`, which already says this:
        "the modules people put fixtures and factories in". A second list of
        the same names is the drift this file keeps paying for.

        A placeholder such as `frontend/e2e/.gitkeep` is allowed whatever the
        directory above it is called. Git tracks files and not directories,
        every gate runs in a `git worktree add` that carries tracked files only,
        and a Playwright `testDir` missing from the sandbox collects nothing --
        so the runner beside it cannot work unless the placeholder is
        committable. One refused file also takes the good files in its batch
        down with it, which is the right behaviour for a set a human accepted as
        a set, and the wrong thing to be triggered by a marker file.
        """
        problems: list[str] = []
        for f in files:
            path = Path(f.path)
            if path.is_absolute() or ".." in path.parts:
                problems.append(f"{f.path}: paths must be repo-relative")
                continue
            if (
                _is_test_path(f.path)
                and path.name not in ProjectRegistry._PLACEHOLDERS
                and not is_setup_path(f.path)
            ):
                problems.append(
                    f"{f.path}: scaffolding may not write tests. A gate that is green because the "
                    "factory wrote the test it runs proves nothing."
                )
        return problems

    def apply_scaffolding(
        self, project: Project, paths: list[str], *, replace: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Write chosen scaffolding into the repository. The human's action, not
        the factory's: this is the one write that does not go through a sandbox
        and a review, so it is never taken on the factory's own initiative.

        `replace` names the paths a human has looked at a diff of and chosen to
        overwrite. Refusing to overwrite was right as a default and wrong as an
        absolute: a reading that wants to add one line to an existing conftest
        can only say so as a whole file, so the proposal that registers a
        project's own factories with pytest -- without which every factory in it
        is unreachable by name -- could be accepted forever and never land. The
        page showed it and shrugged.

        Still never silent, and never bulk: a path is overwritten only by being
        named here, one at a time, from a card showing the diff. The previous
        contents are recoverable because everything written here is committed.
        """
        survey = project.state.survey
        if survey is None:
            raise ProjectError(f"project {project.id!r} has no survey to take scaffolding from")

        # Newest proposal wins. Reading the survey's copy here is what made
        # "Replace the file" write the version already on disk.
        offered = proposed_scaffolding(project)
        wanted = {path: offered[path] for path in paths if path in offered}
        missing = sorted(set(paths) - set(wanted))
        if missing:
            raise ProjectError(f"nothing proposes: {', '.join(missing)}")

        problems = self.scaffold_problems(list(wanted.values()))
        if problems:
            raise ProjectError("; ".join(problems))

        allowed = set(replace)
        unknown = sorted(allowed - set(wanted))
        if unknown:
            raise ProjectError(
                f"asked to overwrite files that are not being written: {', '.join(unknown)}")

        written, skipped = [], []
        for path, spec in wanted.items():
            target = safe_join(project.repo_path, path)
            if target.exists():
                if path not in allowed:
                    skipped.append(f"{path}: already exists, left alone")
                    continue
                # Identical content is not a change, and committing one makes a
                # diff a reader has to open to discover is empty.
                if target.read_text(encoding="utf-8") == spec.contents:
                    skipped.append(f"{path}: already exactly this, left alone")
                    continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(spec.contents, encoding="utf-8")
            written.append(path)

        # Committed, not merely written. Everything this project is measured by
        # runs in a sandbox, and a sandbox is `git worktree add` at a commit --
        # which carries tracked files only. Scaffolding left uncommitted is
        # therefore invisible to the very gates it exists to unblock: three
        # files sat in a working tree while `frontend-build` went on failing
        # with "tsconfig.build.json does not exist", which was true of the
        # sandbox and false of the repository, and nothing said so.
        #
        # A change this tool recommends has to be a real change to the
        # repository. Anything less is homework it has handed to a human without
        # saying that it did.
        commit, problem = ("", "")
        if written:
            commit, problem = commit_paths(
                project.repo_path, written,
                "Add the test harness the factory needs to verify features\n\n"
                + "\n".join(f"- {path}" for path in written)
                + "\n\nProposed by the survey and applied at repo ready. Setup only: none of these\n"
                  "files asserts anything. They are committed rather than left in the working\n"
                  "tree because every check runs in a worktree at a commit, which carries\n"
                  "tracked files only -- uncommitted, they would be invisible to the gates they\n"
                  "exist to unblock.",
            )
        project.store.append("scaffold", {
            "written": written, "skipped": skipped,
            "commit": commit, "commit_problem": problem,
        }, role="human")
        project.state.scaffolding_applied = sorted(
            set(project.state.scaffolding_applied) | set(written)
        )
        self.save(project, note=f"scaffolding written: {', '.join(written) or 'none'}")
        return {"written": written, "skipped": skipped, "repo": str(project.repo_path),
                "commit": commit, "commit_problem": problem}

    def write_repo_dockerfile(self, project: Project, text: str, message: str) -> dict[str, Any]:
        """Write `.fabrika/Dockerfile` and commit it -- only on a person's press.

        Committed for the reason scaffolding is: a feature branches from a
        commit, so a file left in the working tree is a file no check will ever
        be built from. Committed on whatever branch the working copy is on,
        which is the person's choice to have made; when that is not the branch
        features start from, saying so is the whole of the answer.
        """
        if not text.strip():
            raise ProjectError("there is no Dockerfile to write")
        target = safe_join(project.repo_path, REPO_DOCKERFILE)
        if target.exists() and target.read_text(encoding="utf-8") != text \
                and repo_dockerfile(project.repo_path, "HEAD") != target.read_text(encoding="utf-8"):
            raise ProjectError(
                f"{REPO_DOCKERFILE} already exists in your working copy with changes that are "
                "not committed. Commit or discard them first, so nothing of yours is overwritten.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        commit, problem = commit_paths(project.repo_path, [REPO_DOCKERFILE], message)
        if not problem and repo_dockerfile(project.repo_path, project.base_ref) != text:
            branch = current_branch(project.repo_path) or "the current branch"
            problem = (f"committed on {branch}, but features branch from {project.base_ref}, "
                       f"so Fabrika will use it once it is merged into {project.base_ref}.")
        project.store.append("dockerfile", {"path": REPO_DOCKERFILE, "commit": commit,
                                            "commit_problem": problem}, role="human")
        return {"path": REPO_DOCKERFILE, "commit": commit, "commit_problem": problem}

    def diagnosis_for(self, project: Project, check: str) -> "Diagnosis | None":
        """The diagnosis that explains this check's red result in the last run."""
        from .diagnosis import latest_by_check

        baseline = project.state.baseline
        result = next((r for r in (baseline.results if baseline else []) if r.name == check), None)
        if result is None or not result.diagnosis:
            return None
        return latest_by_check(list(project.store)).get((check, result.diagnosis))

    def apply_diagnosis_fix(self, project: Project, check: str, index: int) -> dict[str, Any]:
        """Apply one fix a diagnosis proposed -- only on a person's press.

        A file goes into the repository and is committed, as moving the
        Dockerfile does; a setup step goes into the environment; a command
        becomes the check's; a hold is a hold. Each is recorded as the
        person's, beside the diagnosis it came from.
        """
        found = self.diagnosis_for(project, check)
        if found is None or not 0 <= index < len(found.fixes):
            raise ProjectError(f"there is no fix {index} proposed for {check!r} in the last run")
        fix = found.fixes[index]
        gate = next((g for g in project.state.gates if g.name == check), None)
        if gate is None:
            raise ProjectError(f"no check called {check!r}")
        out: dict[str, Any] = {"check": check, "kind": fix.kind}
        if fix.kind == "repo_change":
            path = fix.path.strip()
            target = safe_join(project.repo_path, path)
            # The diagnosis read the file as the run's commit had it. A working
            # copy that differs -- edited, or on another commit -- is not what
            # it wrote a whole new file against, so nothing of yours is overwritten.
            read_at = project.state.baseline_sha or project.base_ref
            was = repo_dockerfile(project.repo_path, read_at, path)
            now = target.read_text(encoding="utf-8") if target.is_file() else None
            if (now or None) != (was or None):
                raise ProjectError(
                    f"{path} in your working copy is not the one the checks ran against. Commit or "
                    "discard your changes, or run the checks again, so nothing of yours is overwritten.")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(fix.contents, encoding="utf-8")
            message = fix.commit_message.strip() or fix.title.strip() or f"Fix {check}"
            commit, problem = commit_paths(project.repo_path, [path], message)
            if not problem and repo_dockerfile(project.repo_path, project.base_ref, path) != fix.contents:
                branch = current_branch(project.repo_path) or "the current branch"
                problem = (f"committed on {branch}, but features branch from {project.base_ref}, so "
                           f"the checks see it once it is merged into {project.base_ref}.")
            out.update(path=path, commit=commit, commit_problem=problem)
        elif fix.kind == "environment_change":
            env = project.state.environment
            if env is None:
                raise ProjectError("this project has no environment to add a setup step to")
            grown = env.model_copy(deep=True)
            grown.setup = [*grown.setup, *[c for c in fix.setup if c.strip() and c not in grown.setup]]
            project = self.update(project, {"environment": grown})
        elif fix.kind == "command_change":
            gates = [g.model_copy(update={"command": fix.command}) if g.name == check else g
                     for g in project.state.gates]
            project = self.update(project, {"gates": gates})
        elif fix.kind == "hold":
            project = self.ratchet_check(project, check)
        else:
            raise ProjectError("this fix is a prompt for your coding agent; there is nothing to apply here")
        project.store.append("diagnosis_fix", {**out, "fingerprint": found.fingerprint,
                                               "fix": index, "title": fix.title}, role="human")
        return out

    def move_dockerfile_into_repo(self, project: Project) -> dict[str, Any]:
        """The record's Dockerfile, moved into the repository it describes."""
        env = project.state.environment
        if env is None or env.kind not in _BUILT_KINDS or not env.dockerfile.strip():
            raise ProjectError(f"project {project.id!r} has no Dockerfile to move")
        if project.dockerfile_source == "repo":
            raise ProjectError(f"{REPO_DOCKERFILE} is already in the repository")
        if project.dockerfile_source == "project" or env.dockerfile_path:
            raise ProjectError(f"this project builds from its own {env.dockerfile_path}")
        return self.write_repo_dockerfile(
            project, env.dockerfile,
            f"Add {REPO_DOCKERFILE}, the environment Fabrika checks this project in\n\n"
            "Proposed by Fabrika's survey and kept on its own record until now. In the\n"
            "repository it is edited, reviewed and versioned like any other file:\n"
            "Fabrika reads it from the branch features start from each time it builds.")

    def move_dockerfile_on_acceptance(self, project: Project) -> dict[str, Any] | None:
        """At "Accept checks", the survey's Dockerfile goes into the repository.

        Accepting a new project's checks is accepting what they run in, and it
        is the one press every project makes before anything is built -- so a
        project is never left on Fabrika's own copy by default, needing a second
        visit to a second button to be set up properly. Only after the approval
        itself has succeeded, and never over a file already in the working copy,
        committed or not: that is somebody's own, and the tab says whose is used.
        A failure here is reported, not raised -- the checks were accepted.
        """
        env = project.state.environment
        if (env is None or env.kind not in _BUILT_KINDS or not env.dockerfile.strip()
                or project.dockerfile_source != "fabrika" or env.dockerfile_path
                or (project.repo_path / REPO_DOCKERFILE).exists()):
            return None
        try:
            return self.move_dockerfile_into_repo(project)
        except (ProjectError, OSError) as exc:
            return {"path": REPO_DOCKERFILE, "commit": "", "commit_problem": str(exc)}

    def retire_fabrika_dockerfile(self, project: Project) -> dict[str, Any]:
        """Remove `.fabrika/Dockerfile` once the project's own is what is built.

        One definition of the check image, not two: once the project has taken
        the suggestion to give its own Dockerfile a stage that runs its checks,
        and a reading has moved the environment onto it, Fabrika's file is a
        copy of nothing. Removed on a person's press, and committed, like its
        arrival.
        """
        env = project.state.environment
        if env is None or not env.dockerfile_path or project.dockerfile_source != "project":
            raise ProjectError(f"{REPO_DOCKERFILE} is still what this project builds from")
        target = safe_join(project.repo_path, REPO_DOCKERFILE)
        if not target.is_file():
            raise ProjectError(f"{REPO_DOCKERFILE} is not in your working copy")
        if target.read_text(encoding="utf-8") != (repo_dockerfile(project.repo_path, "HEAD") or ""):
            raise ProjectError(f"{REPO_DOCKERFILE} has changes that are not committed. Commit or "
                               "discard them first, so nothing of yours is removed unseen.")
        target.unlink()
        commit, problem = commit_paths(
            project.repo_path, [REPO_DOCKERFILE],
            f"Remove {REPO_DOCKERFILE}: Fabrika now builds from {env.dockerfile_path}"
            + (f", stage {env.dockerfile_target}" if env.dockerfile_target else "") + "\n\n"
            "The project's own Dockerfile has a stage that runs its checks, so Fabrika's\n"
            "separate copy is no longer used.")
        project.store.append("dockerfile", {"path": REPO_DOCKERFILE, "removed": True,
                                            "commit": commit, "commit_problem": problem},
                             role="human")
        return {"path": REPO_DOCKERFILE, "commit": commit, "commit_problem": problem}

    def record_survey(self, project: Project, survey: ProjectSurvey, *, model: str = "",
                      spent: dict[str, Any] | None = None) -> Project:
        project.store.append("survey", survey, role="surveyor", model=model, meta=spent or {})
        project.state.survey = survey
        # What this reading was of. Gates are only as current as the reading
        # that produced them, and without this the system cannot tell whether
        # its gate list still describes the repository in front of it.
        project.state.survey_sha = head_sha(project.repo_path, project.state.base_ref or "HEAD")
        project.state.survey_paths = watched_paths(project)
        project.state.name = project.state.name or survey.name
        project.state.base_ref = survey.base_ref or project.state.base_ref
        # Everything the approval was an answer to, as it stood before this
        # reading replaced it.
        was = self.approval_fingerprint(project)
        # A full survey replaces the gate list rather than diffing it, so
        # without this a declined check would survive a re-survey and not a
        # survey: the reading proposes the check again from evidence that never
        # leaves the repository, and the wholesale write walks straight past
        # the decision. Filtered here rather than in the reading, so it holds
        # however the list was produced.
        declined = self.declined_gates(project)
        project.state.gates = [g for g in survey.gates if g.name not in declined]
        project.state.test_file_commands = list(survey.test_file_commands)
        project.state.blind_placements = list(survey.blind_placements)
        project.state.trace_dirs = list(survey.trace_dirs)
        # A new reading is measured afresh: which checks need a warm-up is
        # something the next baseline finds out, not something to carry over
        # from checks and a setup that may no longer be the ones on record.
        project.state.warm_checks = []
        # Copied like every other field the surveyor produces. Without it a
        # freshly surveyed project has an empty testing surface no matter what
        # the survey said, and the effect is invisible and expensive --
        # `check_spec_testability` returns early on an empty surface, so no spec
        # is checked against what the project can verify, and the only route to
        # a surface at all is a re-survey diff.
        project.state.testing = survey.testing
        project.state.environment = survey.environment
        project.state.stage = "awaiting_approval"
        # A baseline is a measurement of one gate list against one environment.
        # Replace either and it is a measurement of something that no longer
        # exists, so it goes -- the same rule `update` applies through
        # `INVALIDATES_BASELINE`, which writing the fields directly would skip.
        #
        # Skipping it is invisible in the normal flow, because `run_survey`
        # runs a fresh baseline seconds later. It shows when that does not
        # happen: the console renders each gate beside the result of the same
        # name, so a new list over old results draws new gates as "not run"
        # and superseded ones as green -- under a heading that reads "every
        # check ran and passed", because the verdict is computed from the
        # results and the gates without any are counted by neither.
        if was != self.approval_fingerprint(project):
            project.state.baseline = None
        bad_scaffolding = self.scaffold_problems(list(survey.scaffolding))
        if bad_scaffolding:
            survey.scaffolding = [
                f for f in survey.scaffolding
                if not any(f.path in problem for problem in bad_scaffolding)
            ]
            project.store.append(
                "scaffold_refused", {"problems": bad_scaffolding}, role="orchestrator",
            )
        problems = self.environment_problems(survey.environment, list(survey.gates))
        if problems:
            project.store.append("environment_problems", {"problems": problems}, role="orchestrator")
            project.state.error = " ".join(problems)[:4000]
        else:
            project.state.error = ""
        return self.save(project, note="surveyed")

    def tooling_drift(self, project: Project) -> dict[str, Any]:
        """Whether the gate list still describes this repository.

        A fact, computed from git and a glob list. It never triggers anything:
        the only thing it leads to is telling a human, because re-surveying
        changes what the project measures and that is theirs to approve.
        """
        return tooling_drift(
            project.repo_path,
            project.state.survey_sha,
            known_paths=project.state.survey_paths,
        )

    @staticmethod
    def recommendation_key(rec: Recommendation | dict[str, Any]) -> str:
        """What identifies a suggestion across readings.

        The command it would create, because that is derived from the
        repository -- `npm run typecheck` is in package.json whoever describes
        it -- and a title is prose a reading rewrites on every run. Falling back
        to the title when it names no command is imperfect and is the honest
        best available: a suggestion with no command is one with no stable
        handle at all.
        """
        get = rec.get if isinstance(rec, dict) else lambda k, d="": getattr(rec, k, d)
        raw = str(get("would_gate", "") or "").strip() or str(get("title", "") or "").strip()
        return " ".join(raw.lower().split())

    def live_recommendations(self, project: Project) -> list[Recommendation]:
        """What a reading suggests that this project has not turned down.

        Computed here and served ready-made, so the console never re-derives the
        key. Two implementations of "which suggestion is this" is two chances to
        disagree, and they would disagree silently -- a card that will not
        decline, or one that stays declined and reappears.

        The newest reading wins: a proposal supersedes the survey's own list,
        which is what the screen renders.
        """
        declined = {r.key for r in (project.state.recommendation_rulings or [])}
        recs: list[Recommendation] = []
        #: Whether a re-reading has answered since the survey. Its list replaces
        #: the survey's even when it is empty -- a reading writes its
        #: suggestions fresh every time, so none means none. Read as "no newer
        #: reading", an empty list brought back the survey's three, including
        #: one asking for the browser suite to be run after it had become a check.
        reread = False
        #: key -> the check it became. A suggestion answered by adopting it is
        #: as answered as one turned down, and it was still being made: all
        #: four on one project sat in the list under a Build it button after
        #: their checks were already on it, inviting a human to build work the
        #: project was by then measuring itself against.
        adopted: dict[str, str] = {}
        for record in project.store:
            if record["kind"] == "resurvey":
                raw = (record.get("payload") or {}).get("recommendations") or []
                recs = [Recommendation.model_validate(x) for x in raw]
                reread = True
            elif record["kind"] == "survey":
                recs = []
                reread = False
            elif record["kind"] == "recommendation_adopted":
                payload = record.get("payload") or {}
                key = " ".join(str(payload.get("key") or "").lower().split())
                if key:
                    adopted[key] = str(payload.get("name") or "")
        if not reread:
            survey = project.state.survey
            recs = list(survey.recommendations) if survey else []
        # Raised by a run rather than by a reading, so a later reading does not
        # replace them: what a baseline found is a fact about the repository
        # that no re-reading of it will say.
        for record in project.store:
            if record["kind"] == "recommendation_raised":
                raised = Recommendation.model_validate(record.get("payload") or {})
                if self.recommendation_key(raised) not in {self.recommendation_key(r) for r in recs}:
                    recs.append(raised)
        # Against the list as it stands, not against the record of adopting.
        # Take the check off again and the suggestion is live again, which is
        # true: the repository still does not do it, and every later reading
        # will say so. Declining is the way to stop being asked.
        on_the_list = {g.name for g in project.state.gates}
        # A family turned down is every suggestion in it turned down. Read
        # here, where every suggestion is filtered, so no screen and no reading
        # can offer one the project has already said it goes without.
        gone_without = {r.family for r in (project.state.family_rulings or [])}
        # A family no check is in, whose question a check's rules already
        # answer -- security rules in the linter -- is not empty, and gets no
        # suggestion for what is already enforced.
        covered = {a.family for g in project.state.gates for a in g.also}
        own = {g.family for g in project.state.gates}
        gone_without |= covered - own
        def answered(key: str) -> bool:
            return key in declined or adopted.get(key, "") in on_the_list
        return [r for r in recs
                if not answered(self.recommendation_key(r)) and r.family not in gone_without]

    def decline_family(self, project: Project, family: str, reason: str) -> Project:
        """Go without a family of checks: no quality check here, say.

        Families are optional, so this blocks nothing. What it changes is that
        nothing in the family is suggested again, and the place where the
        family would be says why there is none -- an empty family a person
        chose reads differently from one nobody looked at.
        """
        if family not in ("structure", "quality", "tests"):
            raise ProjectError(f"{family!r} is not a family of checks")
        rulings = [r for r in (project.state.family_rulings or []) if r.family != family]
        rulings.append(FamilyRuling(family=family, reason=(reason or "").strip(), at=_now()))
        project.store.append("family_ruling", {"family": family, "reason": reason}, role="human")
        return self.update(project, {"family_rulings": rulings})

    def reinstate_family(self, project: Project, family: str) -> Project:
        """Be asked about a family again. Its suggestions come back with the next reading."""
        rulings = [r for r in (project.state.family_rulings or []) if r.family != family]
        if len(rulings) == len(project.state.family_rulings or []):
            raise ProjectError(f"no family {family!r} was declined here")
        project.store.append("family_reinstated", {"family": family}, role="human")
        return self.update(project, {"family_rulings": rulings})

    def hold_new_code(self, project: Project, name: str, limit: float | None) -> Project:
        """Hold a check on the lines a feature changes, at a limit a person sets.

        `None` lets go again. The limit is never proposed by a reading; the
        console offers zero, which is what almost everyone means -- no new
        finding, whatever the repository already had. A check edit like any
        other, so the checks run again before approval.
        """
        gate = next((g for g in project.state.gates if g.name == name), None)
        if gate is None:
            raise ProjectError(f"no check called {name!r}")
        # Which limit depends on what the check reports: findings are held to
        # a most, coverage to a least.
        coverage = gate.report_format in COVERAGE_FORMATS
        if limit is not None and not gate.report_format:
            raise ProjectError(
                f"{name!r} writes no report of where each finding is, so it cannot be held "
                "on new code alone. Its command needs the tool's SARIF or coverage output, "
                "to {report}."
            )
        if limit is not None and limit < 0:
            raise ProjectError("a limit on new code cannot be below zero")
        if limit is not None and coverage and limit > 100:
            raise ProjectError("a coverage floor is a percentage, at most 100")
        held = gate.model_copy(update={("patch_min" if coverage else "patch_max"): limit})
        project.store.append("check_held_on_new_code", {"name": name, "limit": limit},
                             role="human")
        return self.update(project, {"gates": [held if g.name == name else g
                                               for g in project.state.gates]})

    def ratchet_check(self, project: Project, name: str) -> Project:
        """Hold a red check at the reading it gave on untouched code.

        The bound is the number the check printed, set by a person pressing
        for it -- never a model's figure and never a guess. A count of problems
        gets a ceiling at today's count; a score below its floor gets the floor
        lowered to today's score. Either way the check is green now and red the
        moment a feature makes it worse, which is what lets a repository with
        old problems use a check without fixing them all first.

        Editing a check is editing the list, so the checks run again before
        approval, as for any other edit: the bound is proved, not assumed.
        """
        gate = next((g for g in project.state.gates if g.name == name), None)
        if gate is None:
            raise ProjectError(f"no check called {name!r}")
        baseline = project.state.baseline
        result = next((r for r in (baseline.results if baseline else []) if r.name == name), None)
        if result is None or result.metric is None:
            raise ProjectError(
                f"{name!r} has no reading to hold it at: its last run printed no number its "
                "pattern could read. Give it a pattern that reads the count, and run the checks again."
            )
        if is_green(result):
            raise ProjectError(f"{name!r} already passes; there is nothing to hold it at.")
        if gate.family == "tests":
            # Its exit code also says whether a test failed, and a bound never
            # replaces that here (`_exit_still_counts`), so a hold could not make
            # it green -- and before that rule, made it green over failing tests.
            raise ProjectError(
                f"{name!r} is a tests check: it fails when a test fails, whatever its number "
                "reads, so it can't be held at one. Fix what fails, or set the floor where the "
                "project keeps it.")
        if result.red_kind == "under_own_floor":
            raise ProjectError(
                f"{name!r} reads under the floor this project sets for itself; holding it lower "
                "would contradict the project. Correct the measurement, or change the project's "
                "own floor.")
        held = gate.model_copy()
        if gate.threshold is not None and result.metric < gate.threshold:
            held.threshold = result.metric
        else:
            held.threshold_max = result.metric
        project.store.append(
            "check_ratcheted",
            {"name": name, "metric": result.metric,
             "bound": "threshold" if held.threshold != gate.threshold else "threshold_max"},
            role="human",
        )
        return self.update(project, {"gates": [held if g.name == name else g
                                               for g in project.state.gates]})

    def raise_recommendation(self, project: Project, rec: Recommendation) -> None:
        """Put a suggestion in front of the human that a run, not a reading, found."""
        project.store.append("recommendation_raised", rec.model_dump(), role="orchestrator")

    def decline_recommendation(self, project: Project, key: str, reason: str) -> Project:
        """Turn a suggestion down, for good.

        A reading regenerates its recommendations from the repository every time
        it runs, and the facts that prompt them do not change -- so without a
        record the same three arrive at every reading forever, which is the loop
        this project has now hit in three separate places.
        """
        key = " ".join((key or "").lower().split())
        if not key:
            raise ProjectError("a suggestion needs a key to be declined")
        known = {self.recommendation_key(r): r for r in self.live_recommendations(project)}
        rec = known.get(key)
        if rec is None:
            raise ProjectError(f"no live suggestion under {key!r}")
        rulings = [r for r in (project.state.recommendation_rulings or []) if r.key != key]
        rulings.append(RecommendationRuling(
            key=key, title=rec.title, kind=rec.kind, would_gate=rec.would_gate,
            report_format=rec.report_format, reason=(reason or "").strip(), at=_now()))
        project.store.append("recommendation_ruling",
                             {"key": key, "title": rec.title, "reason": reason}, role="human")
        return self.update(project, {"recommendation_rulings": rulings})

    @staticmethod
    def command_problems(command: str) -> list[str]:
        """Whether this is one command, or a sentence about two.

        `would_gate` is documented as a command and a reading writes English:
        on one project two of four were `A, and B` -- two commands joined by a
        conjunction -- and one carried `(with a floor on the reported
        percentage)`, an instruction to a person. Both were adopted unedited
        from a prefilled field and became checks that could never pass, which
        reads on the page exactly like a check for work not yet done.

        Two rules, and they catch different things. `bash -n` parses without
        executing and finds the parenthetical, which is a syntax error. It does
        not find `A, and B`: `npm audit --audit-level=high, and cd api` is
        valid shell that hands npm two arguments it will not understand, which
        is the worse failure because nothing about it looks wrong. That one is
        caught by its shape.

        A heuristic, and it says so when it fires. The cost of being wrong is
        an edit; the cost of not having it is a check nobody can make green.
        """
        command = (command or "").strip()
        if not command:
            return ["there is no command here"]
        problems: list[str] = []
        if re.search(r",\s+and\s+\S", command):
            problems.append(
                "this looks like two commands joined by the word 'and', not one command. A check "
                "runs one. Split it, name them separately, or join them with '&&' if both must "
                "pass"
            )
        try:
            syntax = subprocess.run(
                ["bash", "-n", "-c", command],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            syntax = None  # No bash to ask. Say nothing rather than guess.
        if syntax is not None and syntax.returncode != 0:
            detail = (syntax.stderr or "").strip().splitlines()
            problems.append(
                "the shell cannot parse this"
                + (f": {detail[-1].split(': ', 1)[-1]}" if detail else "")
            )
        return problems

    def adopt_recommendation(
        self, project: Project, key: str, name: str, command: str = "",
    ) -> Project:
        """Turn a suggestion into the check it was always for.

        `would_gate` is documented as "the check command this would make
        possible". Every other path that reads it is the one where a human
        says no: it keys the dedup, it is copied onto the ruling, and it is
        named in a log line about declined suggestions. Without this nothing
        turns it into a check. A project could run the full pipeline, merge a
        Playwright suite, and still not be measured against `npx playwright
        test` -- the suggestion's whole payoff dropped on the floor at the
        moment it was taken, with the loop closing only by accident, on a later
        reading, because gates are read from what a repository already runs.

        The command is a default here and not a fact. The schema asks for one
        command and a reading writes prose: two of four on one project were
        `A, and B` -- two commands joined by English -- and one carried a
        parenthetical instruction to a human. So this takes what it is given,
        offers `would_gate` as the starting text, and refuses nothing except
        what `gate_problems` already refuses for every other check.

        Adding a check invalidates the baseline, as editing the list always
        has. That is the point rather than a cost: the project drops back to
        gate 0 and the new check is measured on untouched code with the rest,
        where a red one gets the three ways out every red check gets. Adopting
        cannot make a project quietly un-approvable.
        """
        key = " ".join((key or "").lower().split())
        if not key:
            raise ProjectError("a suggestion needs a key to be adopted")
        known = {self.recommendation_key(r): r for r in self.live_recommendations(project)}
        rec = known.get(key)
        if rec is None:
            raise ProjectError(f"no live suggestion under {key!r}")

        name = (name or "").strip()
        if not name:
            raise ProjectError("a check needs a name")
        taken = {g.name for g in project.state.gates}
        if name in taken:
            raise ProjectError(
                f"this project already has a check called {name!r}. Give it another name, or "
                "edit the existing one in settings."
            )
        declined = self.declined_gates(project)
        if name in declined:
            raise ProjectError(
                f"a check called {name!r} was declined for this project"
                + (f": {declined[name].reason}" if declined[name].reason else "")
                + ". Reinstate it in settings rather than adding a second one under the same name."
            )

        command = (command or "").strip() or (rec.would_gate or "").strip()
        if not command:
            raise ProjectError(
                f"{rec.title!r} names no command, so there is nothing to add. Write one, or "
                "build it and add the check afterwards."
            )

        # Held to what gate 0 holds every other check to, and for the reason
        # that matters here: an adopted check carries no metric, so
        # `gate_problems` alone has nothing to say about it. What does is the
        # rest of `environment_problems` -- whether this environment installs
        # the tool the command names, and whether the directory it runs from
        # holds the config that tool reads. Adopting `npx playwright test` into
        # an image with no node buys a check that is red forever and reads as a
        # code failure.
        #
        # Filtered to this gate. The environment's own faults are gate 0's to
        # report and are already on that screen; failing here for them would
        # refuse a good check because something unrelated is broken.
        bad = self.command_problems(command)
        if bad:
            raise ProjectError(
                f"{command!r} is not a command this can run — " + "; ".join(bad) + "."
            )

        gate = Gate(name=name, command=command, family=rec.family,
                    parse_metric=rec.parse_metric, config_files=list(rec.config_files),
                    suppressions=list(rec.suppressions), report_format=rec.report_format,
                    report_path=rec.report_path, files_command=rec.files_command)
        # The tool goes into setup, not into the repository: the environment
        # is this project's configuration here, and pressing Adopt is the
        # consent to change it. Before the check is judged against it, so a
        # tool the suggestion installs is one the environment installs.
        environment = project.environment
        install = (rec.install or "").strip()
        if install and environment is not None and install not in environment.setup:
            environment = environment.model_copy(
                update={"setup": [*environment.setup, install]})
        problems = [
            p for p in self.environment_problems(
                environment, [gate], project.repo_path)
            if f"gate {name!r}" in p
        ]
        if problems:
            raise ProjectError("; ".join(problems))

        project.store.append(
            "recommendation_adopted",
            {"key": key, "title": rec.title, "name": name, "command": command,
             "would_gate": rec.would_gate, "edited": command != (rec.would_gate or "").strip(),
             "family": rec.family, "install": install},
            role="human",
        )
        changes: dict[str, Any] = {"gates": [*project.state.gates, gate]}
        if environment is not project.environment:
            changes["environment"] = environment
        return self.update(project, changes)

    def reinstate_recommendation(self, project: Project, key: str) -> Project:
        """Stop suppressing it. Unlike a check there is nothing to put back --
        a suggestion is not stored on the project -- so this only lifts the
        ruling, and it reappears when a reading makes it again."""
        key = " ".join((key or "").lower().split())
        rulings = [r for r in (project.state.recommendation_rulings or []) if r.key != key]
        if len(rulings) == len(project.state.recommendation_rulings or []):
            raise ProjectError(f"nothing declined under {key!r}")
        project.store.append("recommendation_reinstated", {"key": key}, role="human")
        return self.update(project, {"recommendation_rulings": rulings})

    def declined_gates(self, project: Project) -> dict[str, GateRuling]:
        """Checks this project has decided against, by name."""
        return {r.name: r for r in (project.state.gate_rulings or [])}

    def set_check_runs(self, project: Project, name: str, runs: str) -> Project:
        """A person's choice of when a check runs: `light`, `heavy`, or `` to
        let its timing decide again. Never touches the check itself, so it
        neither clears the baseline nor asks for approval again."""
        if name not in {g.name for g in project.state.gates}:
            raise ProjectError(f"there is no check called {name!r}")
        if runs not in ("light", "heavy", ""):
            raise ProjectError("a check runs `light` (every round) or `heavy` (once, at the end)")
        choices = dict(project.state.check_runs)
        if runs:
            choices[name] = runs
        else:
            choices.pop(name, None)
        project.state.check_runs = choices
        project.store.append("check_runs", {"name": name, "runs": runs}, role="human")
        return self.save(project, note=f"{name} runs {runs or 'by its timing'}")

    def decline_gate(
        self, project: Project, name: str, reason: str, command: str = "",
    ) -> Project:
        """Take a check off the list, or refuse one that was proposed.

        Both are the same decision from a human's side and both have to stick.
        A check that exists is removed; one that was only proposed never arrives.
        Either way the name is on the record, so no later reading can raise it
        again by pointing at the same evidence -- which is still in the
        repository, and always will be.
        """
        name = (name or "").strip()
        if not name:
            raise ProjectError("a check needs a name to be declined")
        existing = {g.name: g for g in project.state.gates}
        kept = [g for g in project.state.gates if g.name != name]
        ruling = GateRuling(
            name=name,
            command=command or (existing[name].command if name in existing else ""),
            reason=(reason or "").strip(),
            at=_now(),
        )
        rulings = [r for r in (project.state.gate_rulings or []) if r.name != name]
        rulings.append(ruling)
        project.store.append("gate_ruling", {
            "name": name, "reason": ruling.reason, "command": ruling.command,
            "was_on_the_list": name in existing,
        }, role="human")
        changes: dict[str, Any] = {"gate_rulings": rulings}
        # Only touch the list when there is something on it to remove -- an
        # `update` naming `gates` clears the baseline, and declining a check
        # that was never added invalidates nothing.
        if name in existing:
            changes["gates"] = kept
        return self.update(project, changes)

    def reinstate_gate(self, project: Project, name: str) -> Project:
        """Put a declined check back, exactly as it was.

        Declining is not deleting. A project turns a check down because it is not
        ready for it, and "not now" has to be revisitable without reconstructing
        the command from memory -- which is why the ruling carries it.
        """
        ruling = self.declined_gates(project).get(name)
        if ruling is None:
            raise ProjectError(f"nothing declined under the name {name!r}")
        rulings = [r for r in project.state.gate_rulings if r.name != name]
        changes: dict[str, Any] = {"gate_rulings": rulings}
        if ruling.command and not any(g.name == name for g in project.state.gates):
            changes["gates"] = list(project.state.gates) + [
                Gate(name=ruling.name, command=ruling.command)]
        project.store.append("gate_reinstated", {"name": name}, role="human")
        return self.update(project, changes)

    # -- a proposal's checks, ruled on one at a time ---------------------------

    @staticmethod
    def proposal_state(project: Project) -> tuple[SurveyDiff | None, dict[str, dict[str, Any]]]:
        """The proposal still standing, and what has been decided about each of its checks.

        A check change is ruled on in the row it would change, one at a time,
        so "is this proposal waiting" is no longer one question: some of its
        checks can be settled while the rest wait. Read from the ledger, newest
        reading first, and dropped -- like the proposal -- when a full survey
        replaces what it was a diff against.
        """
        diff_payload: dict[str, Any] | None = None
        ruled: dict[str, dict[str, Any]] = {}
        for record in project.store:
            kind = record.get("kind")
            payload = record.get("payload") or {}
            if kind == "resurvey":
                diff_payload, ruled = payload, {}
            elif kind == "survey":
                diff_payload, ruled = None, {}
            elif kind == "resurvey_ruling" and diff_payload is not None:
                # The whole-proposal ruling. An older record of it rules on
                # checks too, and those keys are read as check rulings.
                for key in payload.get("applied") or []:
                    if ":" in key and key in (payload.get("proposed") or []):
                        ruled[key] = {"decision": "applied", "at": record.get("at", "")}
                for key in payload.get("rejected") or []:
                    bare = key.split(" (")[0]
                    if ":" in bare and bare in (payload.get("proposed") or []):
                        ruled[bare] = {"decision": "kept", "at": record.get("at", "")}
                # And every other part it carried: under a single Apply for
                # the whole proposal, what was not applied was left.
                for part in PROPOSAL_PARTS:
                    if diff_payload.get(part) and part not in ruled:
                        ruled[part] = {"decision": "applied" if part in (payload.get("applied") or [])
                                       else "rejected", "at": record.get("at", "")}
            elif kind == "proposal_check_ruling" and diff_payload is not None:
                ruled[payload.get("item", "")] = {**payload, "at": record.get("at", "")}
            elif kind == "proposal_check_undone" and diff_payload is not None:
                ruled.pop(payload.get("item", ""), None)
        if diff_payload is None:
            return None, {}
        return SurveyDiff.model_validate(diff_payload), ruled

    def rule_on_proposed_check(
        self, project: Project, item: str, accept: bool, reason: str = "",
    ) -> Project:
        """Apply, or turn down, one check change a reading proposed.

        Accepting changes the list, so the checks run again before approval, as
        for any edit. Turning it down keeps the check as it is and is recorded
        so the same change is not proposed again: a refused new check becomes a
        declined one, a refused change or removal a kept check.
        """
        if ":" not in item:
            return self.rule_on_proposed_part(project, item, accept, reason)
        diff, ruled = self.proposal_state(project)
        if diff is None:
            raise ProjectError("there is no proposal waiting for this project")
        change = next((c for c in diff.gate_changes if f"{c.action}:{c.name}" == item), None)
        if change is None:
            raise ProjectError(f"the proposal has no {item!r}")
        if item in ruled:
            raise ProjectError(f"{item!r} has already been ruled on")
        gates = list(project.state.gates)
        before = next((g for g in gates if g.name == change.name), None)
        reason = (reason or "").strip()
        if accept:
            if change.action != "remove" and change.name in self.declined_gates(project):
                raise ProjectError(f"{change.name!r} was declined for this project; reinstate it first")
            if change.action == "remove":
                gates = [g for g in gates if g.name != change.name]
            elif change.gate is None:
                raise ProjectError(f"{item!r} comes with no check to apply")
            elif before is not None:
                gates = [change.gate if g.name == change.name else g for g in gates]
            else:
                gates.append(change.gate)
            project.store.append("proposal_check_ruling", {
                "item": item, "decision": "applied", "reason": reason,
                "before": before.model_dump(mode="json") if before else None,
                "after": change.gate.model_dump(mode="json") if change.gate else None,
            }, role="human")
            return self.update(project, {"gates": gates, **self._reading_settled(project)})
        changes: dict[str, Any] = {}
        if change.action == "add":
            rulings = [r for r in (project.state.gate_rulings or []) if r.name != change.name]
            rulings.append(GateRuling(name=change.name, command=(change.gate.command if change.gate else ""),
                                      reason=reason, at=_now()))
            changes["gate_rulings"] = rulings
            decision = "declined"
        else:
            kept = [k for k in (project.state.kept_checks or [])
                    if not (k.name == change.name and k.action == change.action)]
            kept.append(KeptCheck(action=change.action, name=change.name,
                                  command=(change.gate.command if change.gate else ""),
                                  reason=reason, at=_now()))
            changes["kept_checks"] = kept
            decision = "kept"
        project.store.append("proposal_check_ruling",
                             {"item": item, "decision": decision, "reason": reason}, role="human")
        return self.update(project, {**changes, **self._reading_settled(project)})

    def rule_on_proposed_part(
        self, project: Project, part: str, accept: bool, reason: str = "",
    ) -> Project:
        """Apply, or leave, one part of a reading that is not a check.

        Applied exactly as the whole-proposal Apply applies it, and recorded
        the way a check's ruling is, so the rest of the reading waits on.
        """
        diff, ruled = self.proposal_state(project)
        if diff is None:
            raise ProjectError("there is no proposal waiting for this project")
        if part not in PROPOSAL_PARTS or not getattr(diff, part, None):
            raise ProjectError(f"the proposal has no {part!r}")
        if part in ruled:
            raise ProjectError(f"{part!r} has already been ruled on")
        reason = (reason or "").strip()
        if not accept:
            project.store.append("proposal_check_ruling",
                                 {"item": part, "decision": "rejected", "reason": reason}, role="human")
            settled = self._reading_settled(project)
            return self.update(project, settled) if settled else self.save(
                project, note=f"re-survey: {part} left as it is")
        before = _plain(_part_now(project, part))
        project, applied = self.apply_survey_diff(
            project, diff, [part], environment=part == "environment", checks=False, record=False)
        if part not in applied:
            raise ProjectError(f"{part!r} could not be applied: there is no environment to attach "
                               "it to" if part == "preview" else f"{part!r} could not be applied")
        project.store.append("proposal_check_ruling", {
            "item": part, "decision": "applied", "reason": reason,
            "before": before, "after": _plain(_part_now(project, part)),
        }, role="human")
        settled = self._reading_settled(project)
        return self.update(project, settled) if settled else project

    def _reading_settled(self, project: Project) -> dict[str, Any]:
        """The stamp that says the reading on record is the current one -- once
        every part of the proposal has been ruled on, and only then. Asked after
        the ruling is in the ledger, so the one just made counts. It answers the
        verify lane's requests the reading was shown, too, as the one Apply did."""
        diff, ruled = self.proposal_state(project)
        if diff is None:
            return {}
        if any(f"{c.action}:{c.name}" not in ruled for c in diff.gate_changes):
            return {}
        if any(part not in ruled for part in proposed_parts(project, diff)):
            return {}
        changes: dict[str, Any] = {
            "survey_sha": head_sha(project.repo_path, project.state.base_ref or "HEAD"),
            "survey_paths": watched_paths(project)}
        whole = any(r.get("kind") == "resurvey_ruling" for r in self._since_reading(project))
        if diff.considered and not whole:
            changes["capability_rulings"] = self._answer_requests(
                project, diff, any(r.get("decision") == "applied" for r in ruled.values()))
        return changes

    @staticmethod
    def _answer_requests(project: Project, diff: SurveyDiff, applied: bool) -> list[CapabilityRuling]:
        """The verify lane's requests a reading was shown, answered by its ruling."""
        now = _now()
        outcome = "applied" if applied else "rejected"
        ledger = {r.need.strip().lower(): r for r in list(project.state.capability_rulings)}
        for request in diff.considered:
            key = request.need.strip().lower()
            held = ledger.get(key)
            if held is None:
                ledger[key] = CapabilityRuling(
                    need=request.need, features=list(request.features), decision=outcome, at=now)
                continue
            # Widened, never narrowed: a request now raised by a second
            # feature is answered for both once this ruling covers it.
            held.features = sorted(set(held.features) | set(request.features))
            held.decision, held.at = outcome, now
        return list(ledger.values())

    @staticmethod
    def _since_reading(project: Project) -> list[dict[str, Any]]:
        """Ledger records after the newest re-survey."""
        out: list[dict[str, Any]] = []
        for record in project.store:
            if record.get("kind") in ("resurvey", "survey"):
                out = []
            else:
                out.append(record)
        return out

    def undo_proposed_check(self, project: Project, item: str) -> Project:
        """Put a check back as it was before its proposed change was accepted.

        Only while the check is still what was accepted: undoing over a later
        edit would throw that edit away. The change is waiting again afterwards.
        """
        diff, ruled = self.proposal_state(project)
        done = ruled.get(item)
        if diff is None or not done or done.get("decision") != "applied":
            raise ProjectError(f"{item!r} is not an accepted change that can be undone")
        if ":" not in item:
            return self._undo_part(project, item, done)
        name = item.split(":", 1)[1]
        now = next((g for g in project.state.gates if g.name == name), None)
        after = done.get("after")
        if after is not None and (now is None or now.model_dump(mode="json") != after):
            raise ProjectError(f"{name!r} has changed since; edit it instead")
        before = done.get("before")
        gates = [g for g in project.state.gates if g.name != name]
        if before is not None:
            restored = Gate.model_validate(before)
            original = [g.name for g in project.state.gates]
            gates = ([restored if g.name == name else g for g in project.state.gates]
                     if name in original else [*gates, restored])
        project.store.append("proposal_check_undone", {"item": item}, role="human")
        return self.update(project, {"gates": gates})

    def _undo_part(self, project: Project, part: str, done: dict[str, Any]) -> Project:
        """Put one accepted part of a reading back, while it is still what was accepted."""
        before = done.get("before")
        if part not in UNDOABLE_PARTS or before is None or "after" not in done:
            raise ProjectError(f"{part!r} cannot be put back from here; edit it instead")
        if _plain(_part_now(project, part)) != done.get("after"):
            raise ProjectError(f"{part!r} has changed since; edit it instead")
        restored = TypeAdapter(ProjectState.model_fields[part].annotation).validate_python(before)
        project.store.append("proposal_check_undone", {"item": part}, role="human")
        return self.update(project, {part: restored})

    def apply_survey_diff(
        self, project: Project, diff: SurveyDiff, accepted: Sequence[str],
        *, environment: bool = False, checks: bool = True, record: bool = True,
    ) -> tuple[Project, list[str]]:
        """Apply the gate changes a human accepted, and only those.

        `accepted` names them; anything absent is a rejection, and rejections
        are recorded as deliberately as acceptances. Six months from now "the
        surveyor wanted to drop the type check and we said no" needs to be
        answerable, and a diff that left no trace of what was turned down would
        make the gate list look like nobody had ever considered the alternative.

        Changing gates invalidates the baseline, which is what `update` already
        does -- the green result a human approved was measured against a gate
        list that no longer exists.
        """
        wanted = set(accepted)
        # A check this project has declined cannot be added back by a reading,
        # whichever way a human ticks the box. The evidence that suggests it --
        # a script in package.json, a step in CI -- never leaves the repository,
        # so without this the same proposal arrives at every reading forever.
        declined = self.declined_gates(project)
        gates = {g.name: g for g in project.state.gates}
        applied: list[str] = []
        rejected: list[str] = []

        # Checks are ruled on one at a time, in their rows, when `checks` is off:
        # this applies the rest of the reading and leaves them waiting.
        for change in (diff.gate_changes if checks else []):
            key = f"{change.action}:{change.name}"
            if change.action != "remove" and change.name in declined:
                rejected.append(f"{key} (declined earlier)")
                continue
            if key not in wanted and change.name not in wanted:
                rejected.append(key)
                continue
            if change.action == "remove":
                if gates.pop(change.name, None) is not None:
                    applied.append(key)
                continue
            if change.gate is None:
                # An add or a change with nothing to apply is not a proposal,
                # it is a sentence. Refused rather than guessed at.
                rejected.append(f"{key} (no gate definition)")
                continue
            gates[change.gate.name or change.name] = change.gate
            applied.append(key)

        changes: dict[str, Any] = {}
        # A reading you took in full is a reading. `record_survey` stamped what
        # it had read and this did not, so the only way to clear "the tooling
        # moved and nobody looked" was the button that replaces your whole gate
        # list -- the safer path left the flag standing forever. Stamped only
        # when nothing was turned down: a diff you accepted half of describes
        # the repository half way, and the flag is right to stay up.
        if record and applied and not rejected:
            changes["survey_sha"] = head_sha(project.repo_path, project.state.base_ref or "HEAD")
            changes["survey_paths"] = watched_paths(project)
        if applied:
            # Order is reading order, and it should stay stable: keep the gates
            # that were already there where they were, and append new ones.
            existing = [g.name for g in project.state.gates if g.name in gates]
            added = [n for n in gates if n not in existing]
            changes["gates"] = [gates[n] for n in existing + added]
        if environment and diff.environment is not None:
            # A fix can come in two halves: a support file, and an environment
            # step that runs it. Files are written into the repository by their
            # own press; an environment applied first would run a script that is
            # not there, fail its preparation, and block every check behind it.
            unwritten = unwritten_references(project, diff.environment, [
                *(f.path for f in diff.scaffolding),
                *(f.path for f in (project.state.survey.scaffolding
                                   if project.state.survey else []))])
            if unwritten:
                raise ProjectError(
                    "this environment runs " + ", ".join(f"`{p}`" for p in unwritten)
                    + ", which is not in the repository yet. Write "
                    + ("it" if len(unwritten) == 1 else "them")
                    + " first -- offered under the files this project needs -- and then "
                    "apply the environment; applied now, its preparation would fail and "
                    "no check could run.")
            # Accepting a new Dockerfile is a change to the repository's file
            # when that is where it lives. Applied to the record alone it would
            # be read over by the repository's copy on the next load, and the
            # accepted change would silently not happen.
            current = project.state.environment
            if (project.dockerfile_source == "repo" and diff.environment.dockerfile.strip()
                    and current is not None and diff.environment.dockerfile != current.dockerfile):
                self.write_repo_dockerfile(
                    project, diff.environment.dockerfile,
                    f"Update {REPO_DOCKERFILE} as proposed by Fabrika's re-survey")
            changes["environment"] = diff.environment
            applied.append("environment")
        # What a person opens at review. Its own tick, and never a reason to
        # rebuild anything: it rides on whichever environment is being kept.
        if diff.preview is not None and "preview" in wanted:
            base = changes.get("environment") or project.state.environment
            if base is None:
                rejected.append("preview (no environment to attach it to)")
            else:
                env = base.model_copy(deep=True)
                env.preview = diff.preview
                changes["environment"] = env
                applied.append("preview")
        # Accepted the same way a gate is, by name, because it decides the same
        # thing a gate does: whether a result can be attributed to a criterion.
        # The resurvey is told to propose one, and this is where it lands.
        if diff.test_file_commands and "test_file_commands" in wanted:
            changes["test_file_commands"] = list(diff.test_file_commands)
            applied.append("test_file_commands")
        # Accepted by name like the rest. This is the only route a healthy
        # project has to a testing surface at all: the console's one button runs
        # the diff, and a diff that could not carry this would leave the reading
        # unreachable from the UI -- proposed in a prompt and dropped everywhere
        # else.
        # Accepted by name like the rest. Changing where blind tests go
        # invalidates what was measured about the old place, which is why it is
        # in `INVALIDATES_BASELINE`: the stored `placement_probe` is a
        # measurement of directories that are no longer the ones in use.
        if diff.blind_placements and "blind_placements" in wanted:
            changes["blind_placements"] = list(diff.blind_placements)
            applied.append("blind_placements")
        if diff.testing is not None and "testing" in wanted:
            changes["testing"] = diff.testing
            applied.append("testing")
        if diff.trace_dirs and "trace_dirs" in wanted:
            changes["trace_dirs"] = list(diff.trace_dirs)
            applied.append("trace_dirs")
        # Merged into the survey's own list rather than written to disk here.
        # Writing a file into somebody's repository is a separate action with a
        # separate button, and it stays that way: this makes the file available
        # to be chosen, exactly as a first survey does.
        if diff.scaffolding and "scaffolding" in wanted and project.state.survey:
            survey = project.state.survey.model_copy(deep=True)
            # Replaced by path, not merely added. Skipping a path already on the
            # list meant a reading could never revise a file it had proposed
            # before -- and the newer contents, which a human had just accepted,
            # were dropped on the floor without a word.
            merged = {f.path: f for f in survey.scaffolding}
            for f in diff.scaffolding:
                merged[f.path] = f
            survey.scaffolding = list(merged.values())
            changes["survey"] = survey
            applied.append("scaffolding")

        # A ruling answers the requests the reading was shown, whichever way it
        # went. Without this the verify lane's asks could never be retired: they
        # live in append-only feature ledgers, so a project that answered "a
        # runner that can drive a browser" by putting Playwright in the
        # repository was handed the same request at the next reading and the one
        # after, forever -- and every reading felt obliged to propose something
        # about it. Rejection retires them too: "we heard this and said no" is an
        # answer, and a reader who wants to be asked again can say so by letting
        # the next feature raise it.
        if diff.considered and record:
            changes["capability_rulings"] = self._answer_requests(project, diff, bool(applied))

        # One part ruled on by itself is recorded by its caller, as a check is.
        if not record:
            if not changes:
                return project, applied
            return self.update(project, changes), applied
        project.store.append("resurvey_ruling", {
            "applied": applied,
            "rejected": rejected,
            "proposed": [f"{c.action}:{c.name}" for c in diff.gate_changes] if checks else [],
            "requests_answered": [r.need for r in diff.considered],
        }, role="human")

        if not changes:
            return self.save(project, note="re-survey: nothing accepted"), applied
        return self.update(project, changes), applied

    def record_reading(self, project: Project) -> Project:
        """Write down what the repository looked like when it was last read.

        The gate list is only as current as the reading behind it, and this is
        the record of that reading. Kept separate from `record_survey` because a
        reading does not have to change anything to have happened: a re-read
        that proposes nothing has still looked, and the gate list it looked at
        is the one in front of the human.
        """
        project.state.survey_sha = head_sha(project.repo_path, project.state.base_ref or "HEAD")
        project.state.survey_paths = watched_paths(project)
        return self.save(project, note="reading recorded")

    def record_baseline(self, project: Project, baseline: GateReport, image: str = "") -> Project:
        project.store.append("baseline", baseline, role="orchestrator")
        project.state.baseline = baseline
        # When it was measured. Without this a human reads gate results with no
        # way to know the tree moved under them: three scaffolding files landed
        # an hour and a half after a baseline, and `frontend-build` went on
        # showing the failure it had before the file that fixes it existed.
        # Nothing on the page was wrong; nothing on it said when.
        project.state.baseline_at = _now()
        project.state.baseline_dockerfile = dockerfile_digest(project.state.environment)
        if image and project.state.environment is not None:
            project.state.environment.image = image
        return self.save(project, note="baseline recorded")

    def approve(self, project: Project) -> Project:
        """Gate 0: whether this project's checks can judge a feature.

        The bar is not "is the repository healthy". Requiring a green
        repository would mean one with pre-existing failures could not onboard
        at all, and could not use the factory to fix itself, because a feature
        needs an approved project. A failing gate is re-run at the commit each
        feature branched from, so a red gate is attributable without ever
        having been green.

        The bar is "will these gates produce information". A gate that runs and
        reports 129 lint errors is a working gate. A gate whose tool is not
        installed is red forever and tells nobody anything, and approving around
        one approves a permanent blind spot.
        """
        baseline = project.state.baseline
        if baseline is None:
            raise ProjectError(
                f"project {project.id!r} has never had its checks run. Build its environment "
                "and run them on an untouched checkout before approving it."
            )
        broken = unrunnable(baseline)
        if broken:
            names = ", ".join(f"{r.name!r}" for r in broken)
            raise ProjectError(
                f"project {project.id!r} has {len(broken)} check(s) that could not run at all: "
                f"{names}. These are not failures, they are absences -- the tool was not there, "
                "or the command never started. They will be red for every feature ever built "
                "here and will never report on anything. Fix the environment or drop the gate; "
                "a check that cannot run is worse than no check, because it looks like coverage."
            )
        # Every check on the list has been run.
        #
        # That a failing check is attributable is true, and it is not enough. A
        # check that fails identically in every packet teaches a reader to skim
        # red -- the same argument this file already makes about a check that
        # cannot run, which does not care why the row never changes -- and each
        # feature stays individually blameless while the aggregate rots.
        #
        # Requiring every check green would lock out any repository with
        # pre-existing failures, including from using this tool to clean itself
        # up. `threshold_max` is what pays that back: a check can be made green
        # at today's count without the work being done first. So there are
        # three ways out of a red row -- fix it, ratchet it, or decline it --
        # and all three are recorded.
        results = {r.name: r for r in baseline.results}
        unmeasured = [g.name for g in project.state.gates if g.name not in results]
        if unmeasured:
            names = ", ".join(repr(n) for n in unmeasured)
            raise ProjectError(
                f"project {project.id!r} has {len(unmeasured)} check(s) that this run says "
                f"nothing about: {names}. They were added or renamed after the last run, so "
                "whatever the other rows say, these are unproved. Run the checks again."
            )
        # A red check is parked, not a refusal -- and the reason to refuse does
        # not apply to a parked one.
        #
        # A parked check is out of the judging set. It never reaches a packet,
        # so it cannot teach anyone to skim
        # anything -- and it stays on gate 0, where it is a job to do rather
        # than noise to ignore. What has to hold is that something is still
        # judging: approving a project where every check is parked would build
        # features against nothing at all.
        # A project with no checks at all fails for its own reason. Falling
        # through to the floor below would tell a reader that none of their
        # checks passes, naming none, which is a sentence about a list that does
        # not exist.
        if not project.state.gates:
            raise ProjectError(
                f"project {project.id!r} has no checks at all. A feature built here would be "
                "judged by nothing, and its packet would say `0 of 0 passed`."
            )
        judging = [g.name for g in project.state.gates if is_green(results[g.name])]
        if not judging:
            parked = ", ".join(repr(g.name) for g in project.state.gates)
            raise ProjectError(
                f"project {project.id!r} has no check that passes on an untouched checkout: "
                f"{parked}. A red check can be parked -- it stays on the list, keeps being run, "
                "and is left out of what judges a feature until it goes green -- but they cannot "
                "all be. A feature judged by an empty list is a feature nothing checked, and the "
                "packet it produces would say `0 of 0 passed`.\n\n"
                "Get one of them passing, or decline the ones this project does not want and "
                "add one it does. `optional` is not a way round it: a check kept on the list and "
                "exempted from the rule is exactly the red row that trains people to skim red."
            )

        # And the same question asked of the blind lane, which is the one place
        # this system writes files into somebody else's repository and then
        # trusts an exit code.
        #
        # A gate that cannot run is refused above because it will be red forever
        # and tell nobody anything. A placement that cannot be proved is worse
        # than that, because it is not red. A blind directory outside the tree
        # that owns `pytest.ini` fails every async test in every blind suite
        # with "async def functions are not natively supported", on every run:
        # every criterion comes back unverified, and each packet reads as a
        # broken feature. Nothing says the harness is the reason unless
        # something asks.
        #
        # So it is asked here, once, against a measurement taken in the sandbox
        # rather than against anybody's belief about paths.
        usable = [r for r in project.state.placement_probe if r.usable]
        if not usable:
            if not project.state.blind_placements:
                raise ProjectError(
                    f"project {project.id!r} declares nowhere to put a blind test. Every "
                    "acceptance criterion here would rest on tests written by the same "
                    "agents that wrote the feature, which is the one thing this pipeline "
                    "exists to avoid. Survey it again: the surveyor proposes a placement "
                    "per runner, and repo ready proves each one before you approve it."
                )
            lines = "\n".join(
                f"  - {r.directory or '(unnamed)'}: {r.note}"
                for r in (project.state.placement_probe or [])
            ) or "  - none of them was measured at all; run the baseline again."
            raise ProjectError(
                f"project {project.id!r} has {len(project.state.blind_placements)} declared "
                f"placement(s) for its blind tests and not one of them could be proved:\n"
                f"{lines}\n\n"
                "A blind test written into any of these is not a check on the feature -- it "
                "either cannot run, cannot report bad news, or would have its failures "
                "reported as the feature's. Fix the placement or the gate that collects it, "
                "then run the baseline again."
            )

        # Which checks were red when a human said yes -- parked checks, or a
        # failed setup command -- computed rather than hardcoded, because the
        # ledger is append-only and older approval records carry the field too.
        #
        # The other question an approval answers is "which checks were
        # ratcheted, and at what number", and that is what `ratcheted` holds.
        red = [r.name for r in baseline.results if not r.passed and not r.skipped]
        ratcheted = [
            {"name": g.name, "metric": g.parse_metric, "ceiling": g.threshold_max,
             "floor": g.threshold}
            for g in project.state.gates
            if g.threshold_max is not None or g.threshold is not None
        ]
        project.state.stage = "ready"
        project.store.append("approval", {
            "approved": True,
            "red_at_approval": red,
            "ratcheted": ratcheted,
            "gates": [r.name for r in baseline.results],
            # What this approval is an answer to. A later baseline run compares
            # against it rather than assuming any re-run invalidates consent.
            "fingerprint": self.approval_fingerprint(project),
        }, role="human")
        return self.save(project, note=(
            f"approved · {len(ratcheted)} check(s) held at a bound" if ratcheted
            else "approved · every check green"))

    # Changing any of these means the green baseline you approved was measured
    # against something that no longer exists. Keeping it would be approving a
    # result nobody ever proved.
    INVALIDATES_BASELINE = {"repo", "base_ref", "gates", "environment", "blind_placements"}

    @classmethod
    def approval_fingerprint(cls, project: Project) -> str:
        """What gate 0 is an answer to, as one comparable value.

        Approval is of the *gate list* and what it runs against -- `approve`
        says the bar is "will these gates produce information". It is not an
        answer about any particular baseline run, so re-proving an unchanged
        list is refreshing a measurement rather than asking the question again.

        Built from exactly `INVALIDATES_BASELINE`, so the thing that sends a
        project back to gate 0 and the thing that keeps it there are the same
        set, and neither can drift from the other.
        """
        state = project.state
        material = {
            "repo": state.repo,
            "base_ref": state.base_ref,
            # A field a check gained later counts once it is set. Dumped whole,
            # one still at its default would change every recorded fingerprint
            # and send every approved project back to gate 0 for a question
            # nobody had been asked. At its default, not merely falsy: a limit
            # of zero new findings is a decision, and the most common one.
            "gates": [g.model_dump(mode="json", exclude={
                          f for f in GATE_FIELDS_SINCE_APPROVAL
                          if getattr(g, f) == Gate.model_fields[f].get_default(
                              call_default_factory=True)} | set(DESCRIPTIVE_GATE_FIELDS))
                      for g in (state.gates or [])],
            "environment": (state.environment.model_dump(
                                mode="json", exclude=set(DESCRIPTIVE_ENV_FIELDS))
                            if state.environment else None),
            "blind_placements": [b.model_dump(mode="json")
                                 for b in (state.blind_placements or [])],
        }
        assert set(material) == cls.INVALIDATES_BASELINE
        blob = json.dumps(material, sort_keys=True, ensure_ascii=False)
        return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def approved_fingerprint(self, project: Project) -> str:
        """The fingerprint recorded the last time a human approved this project."""
        for record in reversed(project.store.records()):
            if record.get("kind") == "approval":
                return str((record.get("payload") or {}).get("fingerprint") or "")
        return ""

    def update(self, project: Project, changes: dict[str, Any]) -> Project:
        """Edit a project. Append-only: this is a new record, not an edit in place."""
        applied: list[str] = []
        for key, value in changes.items():
            if value is None or not hasattr(project.state, key):
                continue
            if getattr(project.state, key) == value:
                continue

            if key == "repo":
                repo_path = Path(str(value)).expanduser().resolve()
                if not repo_path.is_dir():
                    raise ProjectError(f"not a directory: {repo_path}")
                # An in-flight feature's worktree lives in the old repository.
                # Moving the project out from under it would orphan the branch
                # and everything on it.
                live = self.live_feature_ids(project)
                if live:
                    raise ProjectError(
                        f"cannot move {project.id!r} while {len(live)} feature(s) are still in "
                        f"flight: {', '.join(live)}. Their worktrees and branches live in "
                        f"{project.state.repo}. Settle or reject them first."
                    )
                value = str(repo_path)

            if key == "gates":
                # What a check's rules also cover is a description, not a change
                # to what it runs: recorded, without clearing the baseline.
                def bare(gates: Any) -> list[dict[str, Any]]:
                    return [(g if isinstance(g, Gate) else Gate.model_validate(g)).model_dump(
                        mode="json", exclude=set(DESCRIPTIVE_GATE_FIELDS)) for g in gates or []]
                if bare(project.state.gates) == bare(value):
                    setattr(project.state, key, value)
                    applied.append("gates_described")
                    continue

            if key == "environment" and project.state.environment is not None:
                # Which versions to ask for, and which files say what the
                # project's own runs use, describe the environment: recorded
                # without a rebuild or a run of the checks.
                def plain(env: Any) -> dict[str, Any]:
                    return (env if isinstance(env, EnvironmentSpec)
                            else EnvironmentSpec.model_validate(env)).model_dump(
                        mode="json", exclude=set(DESCRIPTIVE_ENV_FIELDS))
                if plain(project.state.environment) == plain(value):
                    setattr(project.state, key, value)
                    applied.append("environment_described")
                    continue

            setattr(project.state, key, value)
            applied.append(key)

        if not applied:
            return project

        if self.INVALIDATES_BASELINE & set(applied):
            # The measurement goes either way: it was taken against a gate list
            # and an environment, and one of those is now something else.
            project.state.baseline = None
            # The approval does not, while features are building. They are
            # already running against this configuration, and un-approving
            # underneath them describes a world that does not exist -- the
            # console then says "nothing is built here until you approve" over
            # a build in flight, and, because a project mid-gate-0 has no
            # bench, the running feature becomes unreachable in the UI.
            #
            # `run_baseline` holds the same rule; without it here, one edit to
            # an environment during a build would hide the build. Same rule,
            # both doors.
            if not self.live_feature_ids(project):
                project.state.stage = "awaiting_approval"

        return self.save(project, note=f"edited: {', '.join(sorted(applied))}")

    def live_feature_ids(self, project: Project) -> list[str]:
        """Features that still own a worktree in this project's repository."""
        from .schemas import FeatureState

        settled = {"accepted", "rejected"}
        live: list[str] = []
        for feature_id in project.feature_ids():
            payload = project.feature_store(feature_id).payload("state")
            if not payload:
                continue
            state = FeatureState.model_validate(payload)
            if state.stage not in settled:
                live.append(feature_id)
        return live

    def delete(
        self, project_id: str, *, sandbox_root: Path | None = None, delete_branches: bool = False,
    ) -> dict[str, Any]:
        """Unregister a project and remove what the factory made for it.

        Never touches the repository itself. What goes is the ledger and the
        worktrees; the branches survive unless asked for, because they are the
        only place a finished feature's code lives.
        """
        import shutil

        from .schemas import FeatureState

        project = self.get(project_id)
        live = self.live_feature_ids(project)
        in_flight = []
        for feature_id in live:
            payload = project.feature_store(feature_id).payload("state")
            if payload and FeatureState.model_validate(payload).stage in ("intake", "building"):
                in_flight.append(feature_id)
        if in_flight:
            raise ProjectError(
                f"{len(in_flight)} feature(s) are still running in {project_id!r}: "
                f"{', '.join(in_flight)}. Agents are writing to them; wait or discard those first."
            )

        removed: dict[str, Any] = {
            "project_id": project_id, "repo": project.state.repo,
            "features": len(project.feature_ids()), "branches": [], "branches_deleted": False,
        }

        for feature_id in project.feature_ids():
            payload = project.feature_store(feature_id).payload("state")
            if not payload:
                continue
            state = FeatureState.model_validate(payload)
            if state.sandbox is None:
                continue
            removed["branches"].append(state.sandbox.branch)
            try:
                Sandbox.reopen(state.sandbox, project.repo_path).release(
                    keep_branch=not delete_branches
                )
            except Exception:
                continue
        removed["branches_deleted"] = delete_branches

        if sandbox_root is not None:
            shutil.rmtree(Path(sandbox_root) / project_id, ignore_errors=True)
        shutil.rmtree(self.evidence_root / project_id, ignore_errors=True)
        return removed

    # -- guides ---------------------------------------------------------------

    def guides(self, project: Project, ref: str = "") -> list[Any]:
        """The guides committed at the branch features start from: what binds.

        Read from the repository every time, never stored -- Fabrika approves
        nothing, and a rule is in force because the repository says so.
        """
        from . import guides

        return guides.found(project.repo_path, ref or project.state.base_ref or "HEAD")

    def set_skills_dir(self, project: Project, folder: str) -> Project:
        """Where Fabrika writes a skill for this project: the folder its people use.

        A setting about where to write. It decides nothing about what binds --
        skills in either folder do, and the OpenHands driver loads both.
        """
        from . import guides

        if folder not in guides.SKILL_DIR_CHOICES:
            raise ProjectError(f"skills live in {' or '.join(guides.SKILL_DIR_CHOICES)}, not {folder!r}")
        project.state.skills_dir = folder  # type: ignore[assignment]
        project.store.append("skills_dir_set", {"folder": folder}, role="human")
        return self.save(project, note=f"skills folder: {folder}")

    def raise_guide_checks(self, project: Project) -> None:
        """DESIGN.md's own linter, offered as a quality check once a DESIGN.md exists.

        Through the suggestions every check comes from, so taking it or turning
        it down is the same press as for any other, and remembered the same way.
        Raised once; a check that already runs it answers it.
        """
        from . import guides
        from .schemas import Recommendation

        files = guides.tracked(project.repo_path, project.state.base_ref or "HEAD")
        if guides.DESIGN_PATH not in files:
            return
        if any("@google/design.md" in (g.command or "") for g in project.state.gates):
            return
        rec = Recommendation(
            title="Lint DESIGN.md with its own linter",
            kind="lint", family="quality",
            why=("DESIGN.md is the design guide every tool that writes code here is sent to. "
                 "Its own linter catches a token that refers to nothing, a section heading "
                 "repeated, a colour missing from the palette -- the mistakes that make an "
                 "agent follow a rule that is not there."),
            evidence="DESIGN.md at the root of the repository",
            how="Run it once: npx --yes @google/design.md lint DESIGN.md",
            would_gate=guides.DESIGN_LINT_COMMAND)
        key = self.recommendation_key(rec)
        if any(r["kind"] == "recommendation_raised"
               and self.recommendation_key(r.get("payload") or {}) == key for r in project.store):
            return
        self.raise_recommendation(project, rec)

    def write_guide(self, project: Project, writes: Sequence[dict[str, Any]], *, message: str,
                    offer: str = "") -> dict[str, Any]:
        """Write what a person approved into the repository, as one commit.

        Only on a press, and only over nothing of theirs: a new file must not
        exist, and an addition goes to a file whose working copy is what was
        committed. Committed on the branch the working copy is on, as the
        Dockerfile is, and said when that is not where features start. Every
        file is a standard one -- AGENTS.md, CLAUDE.md, DESIGN.md, a SKILL.md
        -- and nothing harness-specific is ever written.
        """
        from . import guides

        planned: list[tuple[str, str]] = []
        links: list[tuple[str, str]] = []
        working = None
        for w in writes:
            path = str(w.get("path") or "").strip().lstrip("/")
            contents = str(w.get("contents") or "")
            if w.get("as_is"):
                # A guide file already in the working copy, committed as the
                # person saw it -- or as they changed it on the page. Only one
                # that is still as it was shown: anything else is theirs, and
                # changed after they looked.
                if working is None:
                    working = {g["path"]: g for g in guides.working_guides(project.repo_path)}
                held = working.get(path)
                if held is None:
                    raise ProjectError(f"{path} is no longer an uncommitted guide in your working copy")
                if held["was"] != w.get("was"):
                    raise ProjectError(f"{path} changed in your working copy after it was shown here. "
                                       "Look at it again before committing it.")
                planned.append((path, contents if contents != held["contents"] else ""))
                continue
            if w.get("link"):
                # A folder linked to another: the one way to give a tool run by
                # hand skills kept for a different one, without a second copy.
                target = str(w["link"])
                if path not in guides.SKILL_DIR_CHOICES:
                    raise ProjectError(f"{path} is not a skills folder Fabrika links")
                where = safe_join(project.repo_path, path)
                if where.exists() or where.is_symlink():
                    raise ProjectError(f"{path} already exists.")
                links.append((path, target))
                continue
            if not path or not contents.strip():
                raise ProjectError("a guide needs a path and some text")
            if guides.edited([path]) != [path]:
                raise ProjectError(f"{path} is not a guide file Fabrika writes: AGENTS.md, "
                                   "CLAUDE.md, DESIGN.md or a skill")
            target = safe_join(project.repo_path, path)
            committed = guides.read_at(project.repo_path, "HEAD", path)
            now = target.read_text(encoding="utf-8") if target.is_file() else None
            if w.get("append"):
                if now is None:
                    raise ProjectError(f"{path} is not in your working copy to add to")
                if now != committed:
                    raise ProjectError(f"{path} has changes that are not committed. Commit or "
                                       "discard them first, so nothing of yours is overwritten.")
                text = now.rstrip() + "\n\n" + contents.strip() + "\n"
            else:
                if now is not None:
                    raise ProjectError(f"{path} already exists. Add to it instead.")
                text = contents.rstrip() + "\n"
            planned.append((path, text))
        for path, text in planned:
            if not text:
                continue  # as it stands in the working copy: committed untouched
            target = safe_join(project.repo_path, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        for path, link in links:
            where = safe_join(project.repo_path, path)
            where.parent.mkdir(parents=True, exist_ok=True)
            where.symlink_to(link, target_is_directory=True)
        paths = [p for p, _ in planned] + [p for p, _ in links]
        commit, problem = commit_paths(project.repo_path, paths, message)
        base = project.state.base_ref or "HEAD"
        if not problem and (any(guides.read_at(project.repo_path, base, p)
                                != (t or (safe_join(project.repo_path, p).read_text(
                                    encoding="utf-8", errors="replace"))) for p, t in planned)
                            or any(guides.read_at(project.repo_path, base, p) != t for p, t in links)):
            branch = current_branch(project.repo_path) or "the current branch"
            problem = (f"committed on {branch}, but features branch from {base}, so agents get it "
                       f"once it is merged into {base}.")
        project.store.append("guide_written", {"paths": paths, "commit": commit,
                                               "commit_problem": problem, "offer": offer},
                             role="human")
        return {"paths": paths, "commit": commit, "commit_problem": problem}

    def decline_guide_offer(self, project: Project, key: str) -> Project:
        """Fabrika offered to write a guide and a person said no: not offered again."""
        if key not in project.state.guide_offers_declined:
            project.state.guide_offers_declined = [*project.state.guide_offers_declined, key]
        project.store.append("guide_offer_declined", {"offer": key}, role="human")
        return self.save(project, note=f"guide offer declined: {key}")

    def pending_guide_draft(self, project: Project) -> Any:
        """The newest draft guide nobody has ruled on, if any."""
        from .schemas import GuideDraft

        draft = None
        ruled: set[str] = set()
        for record in project.store:
            if record.get("kind") == "guide_draft":
                draft = record.get("payload")
            elif record.get("kind") in ("guide_written", "guide_offer_declined"):
                ruled.add(str((record.get("payload") or {}).get("offer") or ""))
        if not draft or draft.get("id") in ruled or draft.get("id") in project.state.guide_offers_declined:
            return None
        return GuideDraft.model_validate(draft)

    def record_survey_started(self, project: Project) -> Project:
        """A new reading has begun, so a failed one is no longer the state.

        Only a failed project changes. Without this, a running survey looks
        exactly like the one that has just failed -- the same red card and the
        same error -- for the eleven minutes it takes. A project at any
        other stage keeps it: what it holds is still true until the reading
        replaces it.
        """
        if project.state.stage != "failed":
            return project
        project.state.stage = "surveying"
        project.state.error = ""
        return self.save(project, note="surveying")

    def record_failure(self, project: Project, error: str) -> Project:
        project.state.stage = "failed"
        project.state.error = error[:4000]
        return self.save(project, note="failed")


def dockerfile_state(project: Project) -> dict[str, Any] | None:
    """What the Environment tab says about the Dockerfile. None when there is none.

    `source` is which copy is in use. `uncommitted` is a working-copy edit the
    checks cannot see yet; `unmeasured` is a Dockerfile that changed since the
    checks last ran, so what they said was said about another environment.
    """
    env = project.state.environment
    if env is None or (env.kind not in _BUILT_KINDS and not env.dockerfile_path) \
            or not (env.dockerfile.strip() or env.dockerfile_path):
        return None
    own = (env.dockerfile_path or "").strip()
    working = safe_join(project.repo_path, own or REPO_DOCKERFILE)
    on_disk = working.read_text(encoding="utf-8") if working.is_file() else None
    return {
        "path": own or REPO_DOCKERFILE,
        "target": env.dockerfile_target or "",
        "own": bool(own),
        "source": project.dockerfile_source,
        # Fabrika's own file, left behind once the project's is in use.
        "fabrika_unused": bool(own) and project.dockerfile_source == "project"
                          and repo_dockerfile(project.repo_path, project.base_ref) is not None,
        "ref": project.base_ref,
        "branch": current_branch(project.repo_path),
        "on_disk": on_disk is not None,
        "uncommitted": on_disk is not None and on_disk != env.dockerfile,
        "unmeasured": bool(project.state.baseline_dockerfile)
                      and project.state.baseline_dockerfile != dockerfile_digest(env),
    }


def dockerfile_digest(env: EnvironmentSpec | None) -> str:
    text = env.dockerfile if env is not None else ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text.strip() else ""


def dockerfile_stage(instructions: str, target: str) -> str | None:
    """The instructions of one build stage (`FROM ... AS target`), or None.

    Only that stage's own lines: a stage built FROM another inherits what it
    did, and a test stage built from the production one would inherit its
    `COPY . .` -- which is exactly what a test stage must not do, and why the
    survey is told to start one from the base image instead.
    """
    out: list[str] | None = None
    for line in instructions.splitlines():
        m = re.match(r"\s*FROM\s+\S+(?:\s+AS\s+(\S+))?", line, re.IGNORECASE)
        if m:
            if out is not None:
                break
            if (m.group(1) or "").lower() == target.lower():
                out = []
        if out is not None:
            out.append(line)
    return "\n".join(out) if out is not None else None

