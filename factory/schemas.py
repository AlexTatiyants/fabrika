"""Every agent's output contract.

Field descriptions are not documentation for us. They are shipped inside the
JSON schema handed to the model, and they are the cheapest quality lever in the
system. Write them as instructions.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

#: A `NAME=value` assignment in a shell command, where NAME is shouted and the
#: value is not itself an expansion. Shell scripts are full of assignments that
#: mean nothing to a test author -- `RC=$?`, `UPID=$!`, a loop's `i` -- so the
#: shape is deliberately narrow: upper-case name, and a value whose FIRST
#: character is not `$`. What survives is the kind of thing a suite is meant to
#: read, `API_BASE_URL=http://127.0.0.1:8000`, and nothing else.
#:
#: The rest of the value may contain `$`, and must: a value that is partly
#: computed is still a variable the suite has to read. Excluding `$` everywhere
#: truncates `API_BASE_URL=http://127.0.0.1:$(cat ...)` to `http://127.0.0.1:`
#: and shows a blind test author a URL with no port on it -- worse than saying
#: nothing, because it looks usable. `command_env` marks those instead.
_EXPORTED_ENV = re.compile(r"\b([A-Z][A-Z0-9_]{2,})=([^\s;&|'\"$][^\s;&|'\"]*)")


def command_env(command: str) -> list[tuple[str, str]]:
    """The environment variables a gate command hands to the process it runs.

    Derived rather than declared. The alternative is a second place to write
    down what the command already says, and a command maintained apart from a
    description of itself drifts: `pytest tests_acceptance/` goes on naming a
    directory long after it has gone.
    """
    seen: dict[str, str] = {}
    for name, value in _EXPORTED_ENV.findall(command or ""):
        seen.setdefault(name, value)
    return sorted(seen.items())


Severity = Literal["blocking", "significant", "minor"]
Reversibility = Literal["trivial", "moderate", "hard", "irreversible"]
FindingSeverity = Literal["blocker", "major", "minor", "nit"]
MaterialityClass = Literal["generated", "mechanical", "conventional", "novel"]
TraceStatus = Literal["traced", "orphan_requirement", "untested", "manual"]
Stage = Literal[
    "intake",
    "awaiting_answers",
    "writing_spec",
    "awaiting_spec_approval",
    # The plan checker and the architect disagree about the cut, or code found
    # a fact about it nobody can rebut. A hard stop like the other two gates:
    # the build ran to here and ended, and a ruling starts the next one.
    "awaiting_cut_approval",
    # Frozen, and not started: a plan this run needs has too little left to
    # finish on, and the window that binds it resets soon enough to be worth
    # waiting for. A real stage rather than a flag on `building`, because a
    # feature that is waiting is not building and a board that says otherwise
    # is lying about where the work is.
    "waiting_for_plan",
    "building",
    "awaiting_verdict",
    "accepted",
    "rejected",
    "failed",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# scout
# --------------------------------------------------------------------------


class ObservedConvention(BaseModel):
    """A convention the code follows that no guide in the repository states."""

    rule: str = Field(description="The rule as a person would state it: `Routers return 404 through HTTPException, never a None body`.")
    slug: str = Field(description="A short kebab-case name for it: `routers-404-via-httpexception`.")
    files: list[str] = Field(default_factory=list, description="Repo-relative files where it holds -- at least two, or it is not a convention.")


class Contradiction(BaseModel):
    """Where the code does otherwise than a guide in the repository says."""

    guide: str = Field(description="The guide's path, as you were given it.")
    rule: str = Field(description="The guide's rule, QUOTED word for word from the guide.")
    files: list[str] = Field(default_factory=list, description="Repo-relative files that do otherwise.")
    in_feature_area: bool = Field(default=False, description="True when the feature you are scouting for will touch these files, so a person must say which to follow before it is built.")


class ScoutReport(BaseModel):
    """What already exists in the repo, and what must not be rebuilt."""

    summary: str = Field(description="Two or three sentences on what this repo is and how it is organised.")
    stack: list[str] = Field(default_factory=list, description="Languages, frameworks and tooling actually in use, as evidenced by files.")
    observed_conventions: list[ObservedConvention] = Field(default_factory=list, description="Conventions the code in the area this feature touches follows, ONLY where no guide you were given speaks to it. Naming, layout, error handling, testing. Each one concrete, with the files where it holds.")
    contradictions: list[Contradiction] = Field(default_factory=list, description="Where the code does otherwise than a guide you were given says: the guide, its rule quoted exactly, and the files. Only what you actually read in both.")
    existing_capabilities: list[str] = Field(default_factory=list, description="Capabilities relevant to the intent that already exist here.")
    do_not_duplicate: list[str] = Field(default_factory=list, description="THE HIGHEST VALUE FIELD. Named modules, functions or helpers a worker would otherwise reimplement, each with the path and what it does.")
    relevant_files: list[str] = Field(default_factory=list, description="Paths a worker on this intent will need to read or change.")
    risks: list[str] = Field(default_factory=list, description="Places where this change is likely to break something existing.")


# --------------------------------------------------------------------------
# interrogator
# --------------------------------------------------------------------------


class Ambiguity(BaseModel):
    """One thing the intent fails to decide, phrased so a human can settle it fast."""

    id: str = Field(description="Stable identifier, Q-1, Q-2, ...")
    question: str = Field(description="A single closed question, asked as the choice a person would recognise: what someone using the feature sees, does or is prevented from doing. 'Should people see a card's labels on the board?', not 'Should the card response schema gain a labels field rendered by the client?'. The mechanism is the elaboration -- it goes in `why_it_matters`, and the options may be as precise as they need to be. Not a topic, not a list of questions bundled together.")
    why_it_matters: str = Field(description="What breaks, or what gets built wrong, if this is guessed. One or two sentences.")
    category: str = Field(default="", description="One of: boundaries, failure semantics, authority, visibility, data lifecycle, scale, conflict-with-repo.")
    options: list[str] = Field(description="Two to four discrete, mutually exclusive answers. Each stands alone as a complete answer.")
    proposed_default: str = Field(description="The option you would pick if forced to decide alone. Must match one of the options.")
    severity: Severity = Field(description="blocking if the build cannot start without it; significant if it changes design; minor if it is a preference.")


class InterrogationReport(BaseModel):
    restated_intent: str = Field(description="The intent as you understand it, in your own words. The human reads this to check you understood them.")
    ambiguities: list[Ambiguity] = Field(default_factory=list, description="Six to ten. Sorted with blocking first.")
    too_vague: bool = Field(default=False, description="True only if you found more than fifteen genuine ambiguities, meaning the intent is not yet a feature request.")
    notes: str = Field(default="", description="Anything the human should know that is not a question.")


# --------------------------------------------------------------------------
# spec writer
# --------------------------------------------------------------------------


class AcceptanceCriterion(BaseModel):
    """A testable statement. Ids are permanent; the whole system references them."""

    id: str = Field(description="AC-1, AC-2, ... Sequential and stable. Never renumbered later.")
    title: str = Field(default="", description="The same criterion in plain language, as you would say it out loud: 'A second feature never inherits the first one's environment.' No paths, no field names, no literals. This is the only part of the criterion most readers see -- the list of thirteen is read with these -- and `statement` is what they open when they want to know exactly what was promised. Say the behaviour, not the mechanism.")
    statement: str = Field(description="One observable, checkable behaviour. Written so a test author who has never seen the code can test it. Precise to the point of being ugly: this is what the oracle writes its blind tests from, and it has never seen the code. Do not trade a literal here for readability -- `title` is where readability lives.")
    rationale: str = Field(default="", description="Why this criterion exists, traced to the intent or to a human answer.")
    verification: str = Field(default="", description="How an outside observer would confirm this holds.")
    check_by_hand: str = Field(default="", description="How a PERSON confirms this by hand, in one or two plain sentences written for them: what to open, what to do, what they should see. 'Open a board, drag a card into the empty Done list, reload: it's still there.' No test code, no tool, fixture or file names -- this is what they read when their project can't test the criterion automatically and it comes to them at review as a checklist.")
    setup: list[str] = Field(default_factory=list, description="What must already exist before this behaviour can be observed at all, named as the fixtures this project's testing surface provides: `make_patient`, `tenant_client`, `disposable_db`. Empty means a test can observe it with nothing prearranged -- which is true of a criterion about a file's contents and almost never true of one about a request. Name the fixture exactly as the surface lists it. If what this criterion needs is not on that list, say what it is in plain words instead: that is not a failure to answer, it is the answer, and it is put in front of the human before the freeze rather than discovered by the oracle after the build.")
    provides: list[str] = Field(default_factory=list, description="Fixtures this criterion ADDS to the project's testing surface, named exactly as a later `setup` would name them: `make_user`, `app_client`. A criterion whose whole subject is an affordance for testing -- 'a test can create an operator the server will accept' -- belongs here, and every other criterion may then name that fixture in its own `setup` without it counting as a gap. Leave empty unless this criterion is what builds the thing.")
    area: str = Field(default="", description="Which part of the system this concerns: data, api, ui, behaviour, ops. Used only to group the list for a reader; leave empty if it does not fit one.")
    verified_at: Literal["unit", "integration", "user"] | None = Field(default=None, description="The lowest level at which this can honestly be checked. unit: pure logic, no I/O -- a rendered front-end component with hand-built props is this, not `user`. integration: the app's own interfaces -- an HTTP call, a database row, a CLI invocation. user: the whole application driven as a person drives it, in a browser against the real back end. Answer for the criterion as written, not for the implementation you imagine: 'the response contains X' is integration however the value is computed, and 'the page shows X' is user however the page gets it. This is compared against what the project can actually verify, and a mismatch is put in front of the human BEFORE they freeze the spec rather than discovered in a packet three hours later.")
    reversibility: Reversibility | None = Field(default=None, description="How hard this is to undo once shipped and in use. A persisted column or a public contract is 'irreversible'; a badge is 'trivial'. Leave null rather than guessing -- a wrong claim here is worse than none, because the reader uses it to decide what to read closely.")


class DataChange(BaseModel):
    """One column, table or index this change proposes to touch."""

    operation: Literal["add", "alter", "drop", "new_table", "index"] = Field(description="What happens to it.")
    table: str = Field(description="The table or collection.")
    column: str = Field(default="", description="The column, empty for a whole-table operation.")
    type: str = Field(default="", description="The declared type, as the migration would write it.")
    nullable: bool = Field(default=True, description="Whether it admits null.")
    model: str = Field(default="", description="For a new table in a project that maps tables to classes: the name that class must have, e.g. `Session` for a `sessions` table. Name it on the `new_table` row and leave it empty on the columns. This is a contract rather than a detail, because two agents who cannot see each other both have to write it: the implementation must use this name, and the blind suite may import it. Left empty, each of them guesses -- one wrote `UserSession`, the independent suite imported `Session`, three of its files would not load, and twenty criteria came back unverified for a spelling.")
    note: str = Field(default="", description="Anything a reader needs: a backfill, a constraint, a default.")


class InterfaceChange(BaseModel):
    """One endpoint or public function this change proposes to touch."""

    operation: Literal["add", "alter", "remove"] = Field(default="alter", description="Whether this endpoint is new, changed, or being taken away. Default to 'alter' when unsure: claiming something is new when it already exists produces a false finding at feature review.")
    method: str = Field(description="GET, POST, PATCH, DELETE -- or 'function' for a library surface.")
    path: str = Field(description="The route or the qualified name.")
    change: str = Field(description="What actually changes about it -- the fields, the status codes, the shape of the body. Not the word 'added': `operation` already says that.")
    touches: list[str] = Field(default_factory=list, description="The columns from `changes.data` this endpoint reads or writes, named exactly as you named them there. Only names you already declared -- anything else is dropped.")


class SurfaceChange(BaseModel):
    """Somewhere a human will see the difference."""

    operation: Literal["add", "alter", "remove"] = Field(default="alter", description="Whether this surface is new, changed, or being taken away.")
    where: str = Field(description="The page, view or component.")
    change: str = Field(description="What appears or changes there.")
    calls: list[str] = Field(default_factory=list, description="The endpoint paths from `changes.interface` this screen calls, written exactly as you wrote them there. Only paths you already declared -- anything else is dropped.")


class PlannedChanges(BaseModel):
    """The shape of the change, declared up front.

    Not decoration. Whatever is declared here can be checked against what was
    actually built when the packet is assembled -- a paragraph cannot be, and a
    declared column can.
    """

    data: list[DataChange] = Field(default_factory=list, description="Schema changes. Omit entirely if the change touches no persisted data.")
    interface: list[InterfaceChange] = Field(default_factory=list, description="Endpoints or public functions added or altered.")
    surfaces: list[SurfaceChange] = Field(default_factory=list, description="Where a human sees the difference.")


class ResolvedAnswer(BaseModel):
    question_id: str = Field(description="The Q-n id this answers.")
    question: str = Field(description="The question as asked.")
    answer: str = Field(description="The settled answer.")
    source: Literal["human", "default", "deferred"] = Field(default="human", description="Where the answer came from.")


class Spec(BaseModel):
    """The frozen contract. Everything downstream is judged against this."""

    title: str = Field(description="Short feature name.")
    intent: str = Field(description="The human's original intent, verbatim.")
    summary: str = Field(description="What this feature does, in one paragraph written for the person deciding whether to approve it: the behaviour in the words of someone who uses it, plus the few decisions they would want to know they are approving -- a limit, a surprising behaviour, what stays manual. No endpoints, paths, table, field or helper names: those belong in `changes` and in each criterion's `statement`, where the agents that need them read them precisely. This is the first thing a human reads, and a summary that names routes and schemas is a second copy of the implementation rather than an account of the feature.")
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list, description="The complete set of criteria. Anything not here is out of scope.")
    non_goals: list[str] = Field(default_factory=list, description="Things explicitly not being built, so nobody adds them.")
    constraints: list[str] = Field(default_factory=list, description="Technical or process constraints the implementation must respect.")
    assumptions: list[str] = Field(default_factory=list, description="What is being assumed, each one a thing that could be wrong.")
    resolved_answers: list[ResolvedAnswer] = Field(default_factory=list, description="The human's rulings from spec review.")
    open_questions: list[str] = Field(default_factory=list, description="Ambiguities carried forward unanswered, accepted as risk.")
    new_dependencies: list[str] = Field(default_factory=list, description="Libraries this feature requires that the project does not already install, as package names. A dependency is a decision a human should see before freezing a spec, and it is the one thing about the environment the verify lane cannot measure: the packages it is told about were measured before this feature existed.")
    changes: PlannedChanges | None = Field(default=None, description="The shape of the change: what data, interfaces and surfaces it touches. Leave null if you are not confident enough to be specific -- an invented column is worse than an omitted section, because the packet will check what you declare against what gets built.")
    worked_before: str = Field(default="", description="What happens today, concretely, from the point of view of whoever uses this. Two or three sentences naming a real screen, a real number, and what the person has to do instead. Do not describe the change here -- only the situation it is replacing.")
    worked_after: str = Field(default="", description="What happens once this ships, in the same voice and about the same person as worked_before, so the two can be read side by side. Two or three sentences. Together these two fields are the single most useful thing you can write for a human deciding whether to approve.")


# --------------------------------------------------------------------------
# architect
# --------------------------------------------------------------------------


class WorkUnit(BaseModel):
    id: str = Field(description="U-1, U-2, ...")
    title: str = Field(description="Short name for this unit of work.")
    objective: str = Field(description="What this unit must achieve. Written for a worker who sees only this unit.")
    criterion_ids: list[str] = Field(default_factory=list, description="The AC ids this unit is responsible for.")
    files_expected: list[str] = Field(default_factory=list, description="Paths this unit is expected to create or change. Units must not overlap here.")
    read_files: list[str] = Field(default_factory=list, description="Existing paths this unit must READ to do its work but must not change: the base class it extends, the migration its migration descends from, the module whose convention it has to match. Units may overlap freely here. Some harnesses can only see files they were handed, so a file a worker needs and you did not name is a file it cannot open and cannot ask for.")
    provides: list[str] = Field(default_factory=list, description="The contract this unit exposes to other units: names, signatures, shapes.")
    requires: list[str] = Field(default_factory=list, description="The contract this unit consumes from other units, by name and shape.")
    depends_on: list[str] = Field(default_factory=list, description="Unit ids whose contracts this one relies on.")
    notes: str = Field(default="", description="Anything a worker on this unit must not get wrong.")
    #: Not written by the architect, never recorded: the section code builds from
    #: the repository's guides and the scout's observations, attached to the
    #: unit just before it is handed to whoever does it. `guidance` carries the
    #: guides' text, for an executor with no harness to load them; a harness
    #: loads the repository's own files itself and gets `harness_guidance`,
    #: which says where the rules its loader does not read are kept.
    guidance: SkipJsonSchema[str] = Field(default="", exclude=True)
    harness_guidance: SkipJsonSchema[str] = Field(default="", exclude=True)
    #: What `guidance` handed, for the record when no harness ran; and the
    #: repository's guides, from which the executor tells the runner that
    #: actually started what it reads itself and where the rest is.
    guidance_delivered: SkipJsonSchema[list[dict[str, Any]]] = Field(default_factory=list, exclude=True)
    guide_files: SkipJsonSchema[list[dict[str, Any]]] = Field(default_factory=list, exclude=True)


class Plan(BaseModel):
    summary: str = Field(description="The decomposition in a paragraph, and why it was cut this way.")
    units: list[WorkUnit] = Field(default_factory=list, description="Work units with non-overlapping file ownership.")
    seams: list[str] = Field(default_factory=list, description="Every boundary where two units must agree. These are where integration breaks.")
    integration_notes: str = Field(default="", description="What the integrator must check once the units land.")


# --------------------------------------------------------------------------
# checkers -- the spec checker and the plan checker
# --------------------------------------------------------------------------


class CheckObjection(BaseModel):
    """One reason the document in front of the checker will fail the agent that
    has to live with it. Anchored, or it is dropped: an objection naming nothing
    cannot be answered, and cannot later be matched against what broke."""

    id: str = Field(description="K-1, K-2, ... unique within this check.")
    kind: str = Field(description="Which failure this is. Pick from the kinds your instructions list.")
    unit_ids: list[str] = Field(default_factory=list, description="The work units this is about, by id (U-1). Plan checks only.")
    criterion_ids: list[str] = Field(default_factory=list, description="The acceptance criteria this is about, by id (AC-3).")
    question_ids: list[str] = Field(default_factory=list, description="The human's answers this is about, by question id (Q-2). Spec checks only, for an answer no criterion covers.")
    claim: str = Field(description="What is wrong, in one sentence.")
    consequence: str = Field(description="What the agent downstream will do because of it. Concrete: 'U-2 will import a name nobody declared', not 'this may cause problems'.")
    settled_by: str = Field(default="", description="The change that would make this objection go away: a read_files entry, two units merged, a name pinned in the spec.")


class CheckReport(BaseModel):
    objections: list[CheckObjection] = Field(default_factory=list, description="At most five, most consequential first. An empty list is a real answer: say nothing rather than invent something.")
    summary: str = Field(default="", description="One or two sentences on the document as a whole.")


class ObjectionAnswer(BaseModel):
    objection_id: str = Field(description="The id of the objection being answered, exactly as given.")
    answer: Literal["revised", "rebutted"] = Field(description="revised: you changed the document so the objection no longer holds. rebutted: you kept it, and say why the checker is wrong.")
    note: str = Field(description="What you changed, or why the checker is wrong. A person may read this to decide between you.")


class SpecRevision(BaseModel):
    spec: Spec = Field(description="The whole spec, revised where you accepted an objection. Criterion ids never renumber.")
    answers: list[ObjectionAnswer] = Field(default_factory=list, description="One per objection, by id.")


class PlanRevision(BaseModel):
    plan: Plan = Field(description="The whole plan, revised where you accepted an objection. Unchanged where you did not.")
    answers: list[ObjectionAnswer] = Field(default_factory=list, description="One per objection, by id.")


# --------------------------------------------------------------------------
# worker
# --------------------------------------------------------------------------


class Decision(BaseModel):
    """The reviewable unit of this system. Not the diff."""

    id: str = Field(description="D-1, D-2, ... unique within the unit.")
    title: str = Field(description="The decision in one line, phrased as a choice made.")
    rationale: str = Field(description="Why this choice, given the spec and the repo.")
    alternatives_rejected: list[str] = Field(default_factory=list, description="What else you seriously considered, and why you did not do it. Empty here means you did not consider anything.")
    blast_radius: str = Field(default="", description="What else is affected if this is wrong.")
    reversibility: Reversibility = Field(default="moderate", description="How hard this is to undo once merged and in use.")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Your honest confidence this is the right call, 0 to 1.")
    criterion_ids: list[str] = Field(default_factory=list, description="AC ids this decision serves.")
    files: list[str] = Field(default_factory=list, description="Paths this decision is embodied in.")
    tags: list[str] = Field(default_factory=list, description="Short labels: schema, security, performance, api, dependency, scope.")
    rank: int = Field(default=0, description="Presentation rank. Set by the rapporteur, ignored elsewhere.")


class SelfDisclosure(BaseModel):
    """Mandatory. A worker that discloses nothing is a worker that hid something."""

    not_implemented: list[str] = Field(default_factory=list, description="Parts of your unit you did not build, each named specifically.")
    assumptions: list[str] = Field(default_factory=list, description="What you assumed because the spec did not say.")
    deviations_from_spec: list[str] = Field(default_factory=list, description="Where you did something other than what the spec asked, and why.")
    flags: list[str] = Field(default_factory=list, description="Anything a reviewer should look at hard: security, data loss, scope widening, shortcuts.")


class FileWrite(BaseModel):
    path: str = Field(description="Repo-relative path. Never absolute, never containing '..'.")
    contents: str = Field(description="The complete file contents. Not a diff, not a fragment.")
    purpose: str = Field(default="", description="One line on what this file is for.")
    #: A removal is work too, and there was no way to say it. A finding whose
    #: whole fix was "this generated file should not be on the branch" went to
    #: a harness that deleted it -- and a deleted path reads as nothing, so the
    #: unit was booked as having edited no file, twice, and the fallback could
    #: only empty the file. The finding reached a human as a broken harness.
    deleted: bool = Field(default=False, description="True to remove this file from the repository. `contents` is then empty and ignored. Only for a file that should not exist at all -- never to rewrite one.")

    @property
    def line_count(self) -> int:
        """How long this file is. Not how much of it changed.

        `contents` is always the whole file, so this is the size of what an
        agent touched and never the size of what it did. It belongs in a prompt
        that shows the file body and nowhere a human reads a number: the packet
        gets its figures from `Sandbox.diffstat`, off the branch.
        """
        if not self.contents:
            return 0
        return len(self.contents.splitlines())


class WorkerOutput(BaseModel):
    unit_id: str = Field(description="The unit you were given.")
    summary: str = Field(description="What you built, in a paragraph.")
    files: list[FileWrite] = Field(default_factory=list, description="Every file you wrote, complete.")
    decisions: list[Decision] = Field(default_factory=list, description="Every non-obvious choice. This is the output the human actually reads.")
    disclosure: SelfDisclosure = Field(default_factory=SelfDisclosure, description="Mandatory structured disclosure. Empty lists are a claim you will be held to.")
    #: Which repair round dispatched this unit. Set by the orchestrator, never
    #: by a model, and empty for a build unit.
    #:
    #: Repair units are numbered from one within their own round, so `R-1` in
    #: round 2 is a different unit from `R-1` in round 1 and the two say
    #: different things about different files. Everything downstream keyed on
    #: the unit alone therefore had them collide: ten repair decisions were
    #: recorded on one feature and six reached the packet, because the packet
    #: keeps the first of any id it meets twice. The four lost were round 2's,
    #: including the reasoning behind the change that broke a required check.
    round: int | None = None
    #: Each time the worker was sent back during its turn, and what it changed
    #: then. Set by the orchestrator. The packet marks files written after a
    #: send-back, because a test written to make a number go up is the one
    #: most worth reading.
    send_backs: list["SendBackRecord"] = Field(default_factory=list)


class SendBackRecord(BaseModel):
    """One time a worker was sent back, measured, during its own turn."""

    attempt: int
    asked: list[str] = Field(default_factory=list)
    changed: list[str] = Field(default_factory=list)


class IntegrationReport(BaseModel):
    summary: str = Field(description="What you wired together and what you had to fix.")
    files: list[FileWrite] = Field(default_factory=list, description="Files you wrote or rewrote to close the seams.")
    seam_issues: list[str] = Field(default_factory=list, description="Mismatches you found between unit contracts.")
    decisions: list[Decision] = Field(default_factory=list, description="Decisions you had to make to close a seam.")
    unresolved: list[str] = Field(default_factory=list, description="Seams you could not close. Say so rather than papering over them.")
    #: The checks the integrator wrote to show the seams hold: the files it
    #: wrote whose name carries `rework.seam_marker`. They go onto the branch
    #: like any other test and stay there, so a check that showed two units
    #: agreeing is still running next week. Not evidence by themselves -- the
    #: factory runs each one as its own gate, and the gate is the evidence.
    seam_files: list[FileWrite] = Field(default_factory=list)


# --------------------------------------------------------------------------
# oracle  (verify lane -- has never seen the implementation)
# --------------------------------------------------------------------------


class TestFile(BaseModel):
    path: str = Field(description="Repo-relative test file path.")
    contents: str = Field(description="The complete test file.")
    criterion_ids: list[str] = Field(default_factory=list, description="The AC ids this file tests. Every test must be tagged, or it proves nothing.")
    cases: list[TestCaseAccount] = Field(default_factory=list, description="Per-test tags, carried from the oracle's account. Empty where the runner cannot report tests individually, and then the file's exit code settles every criterion above together.")
    framework: str = Field(default="", description="The test runner these are written for.")


# Why `SupportFile` exists, and why the split is declared rather than computed.
#
# A suite needs shared setup, fixtures, sample data, a README. A schema that
# carries all of it in `tests` beside the real tests, while telling the oracle
# that an untagged file proves nothing about the contract, invites an oracle to
# tag its README with the union of every id its real tests cover -- and the
# matrix then counts it as evidence for every one of those criteria (eighteen,
# in one case), each displaying more evidence than it has.
#
# The tempting repair is to filter the scaffolding out downstream by its name.
# That cannot be made safe. A test is `test_*.py` under pytest, `*_test.go` in
# Go, `*Test.java` under JUnit, `*.feature` under Cucumber and a plain `.yaml`
# file under Tavern; an allowlist has to know every one of those and will still
# meet a framework it does not. The failure is also the worse direction: a real
# test mistaken for scaffolding deletes coverage that exists, where the bug it
# replaces only inflated a count.
#
# So the distinction is declared, once, by the only agent that can know it -- the
# one that wrote the file -- and the schema makes the dishonest answer
# unavailable rather than merely discouraged. `TestFile` has `criterion_ids`.
# This does not. There is nowhere to put the tag.
#
# Declaring a real test as support is the safe error: its criteria lose a tag and
# read `untested`, which is loud and shows on the criteria screen.


class SupportFile(BaseModel):
    """A file the suite needs in order to run, which asserts nothing itself."""

    path: str = Field(description="Repo-relative path of the support file.")
    contents: str = Field(description="The complete file.")


# Why `requires` exists, and why it is not `untestable_criteria` or `notes`.
#
# Three sentences say three different things and only two of them had a field.
# "I could not tell what this criterion means" is `untestable_criteria`: the spec
# is thin, and the fix is a better spec. "I assumed the endpoint is a PATCH" is
# `notes`. The third is "I know exactly what this criterion means and how to
# check it, and reaching it needs something the environment does not give me" --
# and that is neither a spec problem nor an assumption. It is a fact about the
# harness, and the only agent positioned to notice it is the one being denied.
#
# It had nowhere to go, so it went into the tests. An oracle needing
# authentication and a way to create rows wrote a setup file that asserted
# someone must provide them, through an environment variable nobody had been
# asked to set. Every test in the suite errored in that assertion on every round
# of a 2h43m run, all twenty-five criteria came back unverified, and the packet
# read as a broken feature. The oracle had in fact written the diagnosis down --
# "authentication and entity-creation interfaces are unspecified", in `notes` --
# where nothing reads it and no check can fire on it.
#
# So the honest answer gets a field with structure a check can act on, and the
# dishonest one -- inventing a precondition and hoping -- gets named in the
# prompt as the thing not to do. A requirement stated here costs the run the
# criteria it names and nothing else. The same requirement smuggled into a
# fixture costs the run everything.


class OracleRequirement(BaseModel):
    """Something the suite needed from its environment and was not given."""

    need: str = Field(description="The capability you needed, in one line, concretely: 'a way to authenticate as a specific user', 'a disposable database I may migrate'. Name the capability, not the test you wanted to write.")
    criterion_ids: list[str] = Field(default_factory=list, description="The AC ids that go unverified for want of it. Empty means it cost no criterion -- say so only if that is true, because this list is what decides how loudly this is reported.")
    detail: str = Field(default="", description="What you would have done with it, and what would close the gap. A human who has seen the repository reads this to decide whether to provide the capability or to accept that the criterion cannot be checked here.")


class OracleSuite(BaseModel):
    strategy: str = Field(description="How you decided to test this spec.")
    command: str = Field(default="", description="The shell command that runs YOUR suite and nothing else, from the repo root. Your tests run on their own, never as part of the project's suite, so this command has to stand alone.")
    tests: list[TestFile] = Field(default_factory=list, description="Files that assert against the spec. Every one is tagged with the criteria it checks. A file that asserts nothing does not belong here -- put it in `support`.")
    support: list[SupportFile] = Field(default_factory=list, description="Files your tests need in order to run but which assert nothing themselves: whatever your runner loads automatically for shared setup, fixtures, factories, helpers, sample data, a README. Nothing outside your own directory is available to your tests, so write every one of them here. They are written to disk and protected exactly like the tests; they simply cannot be evidence for a criterion.")
    untestable_criteria: list[str] = Field(default_factory=list, description="AC ids you could not test from the spec, and nothing else.")
    requires: list[OracleRequirement] = Field(default_factory=list, description="Capabilities you needed from the environment and did not have. Use this instead of writing a test, fixture or setup file that assumes someone will provide them: a suite that stops on a precondition nobody agreed to supply verifies nothing at all, while a requirement stated here costs only the criteria it names and tells a human exactly what to fix.")
    notes: str = Field(default="", description="Assumptions you had to make about the interface because the spec did not pin it down.")


class TestCaseAccount(BaseModel):
    """One test inside a file, and the criteria it bears on.

    `name` has to be the name the *runner* prints, because that is the only
    string a report can be joined on. pytest gives the function name and appends
    `[param]` to a parametrised one; vitest gives the title passed to `it(...)`.
    A name that matches nothing reported, or a reported test matching no name
    here, is a fault in this account rather than a verdict about the code -- and
    is reported that way, because an attribution failure that reads as a failing
    criterion is the specific confusion this whole lane exists to prevent.
    """

    name: str = Field(description="The test's name exactly as the runner reports it.")
    criterion_ids: list[str] = Field(default_factory=list, description="The AC ids this one test checks. A test that checks none is not evidence for anything and does not need an entry.")


class CaseOutcome(BaseModel):
    """What a runner's own report said about one test."""

    file: str = Field(default="", description="The test file, repo-relative.")
    name: str = Field(default="", description="The test's name as reported.")
    status: Literal["passed", "failed", "skipped", "errored"] = Field(default="passed")


class TestFileAccount(BaseModel):
    """What a model says *about* a test file it has already written to disk.

    The contents are deliberately absent. They are read from the file, which is
    the same principle as INV-3 applied to the one output that used to be
    exempt: a file's text is a fact, and a model retyping it into an answer is a
    claim that can be truncated, paraphrased or silently shortened. Three runs
    lost their whole verification to exactly that.

    What cannot be computed stays here. Nothing on disk says which acceptance
    criterion a test is *for* -- that is a judgement about the contract, it is
    why this role exists, and it is two dozen characters rather than two hundred
    thousand.
    """

    path: str = Field(description="Repo-relative path of a file you wrote.")
    criterion_ids: list[str] = Field(default_factory=list, description="Every AC id this file bears on, which is the union of the ids on `cases` below. Kept because it is what a runner that cannot report individual tests leaves us with: the file's exit code then settles all of them together.")
    cases: list["TestCaseAccount"] = Field(default_factory=list, description="One entry per individual test in this file, with the criteria that test checks. This is what lets a run say `AC-4 failed` rather than `the file containing AC-3 through AC-17 failed`. Measured on a real run: one test disagreed about one field, its file carried fourteen criteria, and all fourteen were reported to a human as failing while thirteen had passing tests. Give it wherever the runner can report tests individually; leave it empty and the file's exit code is the only verdict available.")
    framework: str = Field(default="", description="The test runner this file is written for.")


class OracleAccount(BaseModel):
    """The oracle's answer once the harness has written the files."""

    strategy: str = Field(description="How you decided to test this spec.")
    command: str = Field(default="", description="The shell command that runs YOUR suite and nothing else, from the repo root. Your tests run on their own, never as part of the project's suite, so this command has to stand alone.")
    tests: list[TestFileAccount] = Field(default_factory=list, description="One entry per file you wrote that ASSERTS something. Tag every one with the criteria it checks. A file that asserts nothing goes in `support` instead, not here untagged.")
    support: list[str] = Field(default_factory=list, description="Paths of the files you wrote that assert nothing themselves -- shared setup, fixtures, factories, helpers, sample data, a README. Exactly as shown above. They still run and are still protected; they simply cannot be evidence for a criterion, so they need no ids. Listing a file here rather than tagging it with every criterion is what keeps the matrix honest.")
    untestable_criteria: list[str] = Field(default_factory=list, description="AC ids you could not test from the spec, and nothing else.")
    requires: list[OracleRequirement] = Field(default_factory=list, description="Capabilities you needed from the environment and did not have. Use this instead of writing a test, fixture or setup file that assumes someone will provide them: a suite that stops on a precondition nobody agreed to supply verifies nothing at all, while a requirement stated here costs only the criteria it names and tells a human exactly what to fix.")
    notes: str = Field(default="", description="Assumptions you had to make about the interface because the spec did not pin it down.")


class CriterionResult(BaseModel):
    criterion_id: str = Field(description="The AC id.")
    status: Literal["passed", "failed", "no_test", "not_run", "not_collected", "unknown", "manual"] = Field(description="Outcome for this criterion. manual means Fabrika did not test it, because its test level cannot run cleanly in this project, and a person checks it by hand. not_collected means the tests never executed at all -- a green exit code over an empty run. unknown means the run's evidence cannot settle it either way, which is never a pass.")
    evidence: str = Field(default="", description="What in the run supports this outcome.")
    test_names: list[str] = Field(default_factory=list, description="Tests that bear on this criterion.")


class QAReport(BaseModel):
    summary: str = Field(description="What the run showed.")
    results: list[CriterionResult] = Field(default_factory=list, description="One row per acceptance criterion.")
    failures: list[str] = Field(default_factory=list, description="Concrete failures worth a human's attention.")
    notes: str = Field(default="", description="Anything about the run itself that affects how much the results are worth.")


# --------------------------------------------------------------------------
# reviewer / adversary
# --------------------------------------------------------------------------


class Citation(BaseModel):
    """The rule a finding holds the code to."""

    source: Literal["guide", "observed", "none"] = Field(default="none", description="`guide`: a rule in one of this repository's guides -- AGENTS.md, DESIGN.md, a SKILL.md, or a document AGENTS.md points to. `observed`: a convention the scout observed, by its slug. `none`: no written rule -- allowed for correctness and security, not for style.")
    ref: str = Field(default="", description="For `guide`, the guide's path exactly as shown under \"How this repository is written\"; for `observed`, the convention's slug.")
    quote: str = Field(default="", description="For `guide`, the rule QUOTED word for word from that guide -- it is checked against the guide, and a quote that is not in it caps the finding at minor.")


class Finding(BaseModel):
    id: str = Field(description="F-1, F-2, ...")
    title: str = Field(description="The problem in one line, said the way someone who uses or maintains this software would say it: what is wrong, not where. 'Anyone can sign in as a workspace they do not belong to', not 'scoping helper skips membership check in current_workspace'. No file paths, criterion ids, line numbers, internal names or this tool's own vocabulary -- those are the elaboration, and they belong in `detail` and `evidence`, where a reader goes once they know what the problem is. Also the de-duplication key across adversary samples, so phrase it canonically.")
    severity: FindingSeverity = Field(description="blocker stops the merge; major needs a ruling; minor and nit are noted.")
    category: str = Field(default="", description="duplication, convention, unearned abstraction, security, correctness, scope, missing -- or, for a failing blind test that is itself wrong, test_code (its own code is broken) or suspect_test (what it asserts is wrong); see your instructions.")
    detail: str = Field(description="What is wrong and why it matters.")
    evidence: str = Field(default="", description="The specific thing you are pointing at. A fabricated citation is worse than no finding.")
    files: list[str] = Field(default_factory=list, description="Paths involved.")
    recommendation: str = Field(default="", description="What should be done about it.")
    criterion_ids: list[str] = Field(default_factory=list, description="AC ids affected, if any.")
    cites: Citation | None = Field(default=None, description="The written rule this finding holds the code to, when it is about how code is written: a guide's rule quoted, or an observed convention's slug. Leave it out for a correctness or security finding that rests on no rule.")
    #: Whether the citation held, decided by code against the guide as it stood
    #: at the feature's base commit. Never the model's to say.
    cite_verified: SkipJsonSchema[bool | None] = None


class ReviewReport(BaseModel):
    """What every review-phase agent returns.

    There is one contract here, not three. The reviewer, the adversary and
    anything you add differ in their *prompt* -- what they hunt for and what
    posture they take -- not in their code path. Specialising the orchestrator
    per agent bought nothing and meant a new angle needed a code change.
    """

    summary: str = Field(description="What you found, from your angle, in a paragraph.")
    verdict: Literal["accept", "accept_with_changes", "reject"] = Field(default="accept", description="Your recommendation from your angle alone. The worst verdict across all review agents is the one the packet carries.")
    strongest_objection: str = Field(default="", description="The single best reason to reject this work, if you have one. Say plainly that you have none rather than inventing one -- an agent that always objects carries no information.")
    findings: list[Finding] = Field(default_factory=list, description="Itemised, each citing the specific thing it is about. A fabricated citation is worse than silence: it burns the credibility of every other finding in the packet.")
    conceded: list[str] = Field(default_factory=list, description="Things you examined and found sound. Concessions are what make the rest of your case credible.")


# --------------------------------------------------------------------------
# breaker  (harness agent -- writes tests and runs them)
# --------------------------------------------------------------------------


#: What a probe is for, which decides how long it lives.
#:
#: A `demonstration` proves the defect is present *now*. It is worth exactly one
#: round: once the defect is gone the construction has nothing left to say, and
#: can only hang, error, or reward a different defect. A `regression` carries a
#: stated pass condition against correct code, so it still means something after
#: the repair and can be re-run and kept.
#:
#: Measured, not taken on faith: a `regression` probe that cannot pass the
#: repaired code is a mislabelled demonstration, and the promotion check catches
#: it without anyone reading the probe. `demonstration` is the default because it
#: is the answer that costs nothing to be wrong about -- a demonstration filed as
#: one is deleted after its round, which is what would have happened anyway.
ProbeKind = Literal["demonstration", "regression"]


class BreakerTest(BaseModel):
    """One hostile experiment, written after reading the implementation.

    The epistemics are the opposite of the oracle's. The oracle has never seen
    the code, so *green* is its signal: a passing blind test means the contract
    is satisfied. The breaker has read everything, so only *red* is its signal.
    A passing breaker test proves nothing and is never counted as verification.
    """

    path: str = Field(description="Repo-relative path, and it must sit under the breaker test directory you were given. Anywhere else is refused.")
    contents: str = Field(description="The complete test file.")
    hypothesis: str = Field(description="What you claim breaks, in one line. This becomes the finding's text if the test fails, so make it a claim about the code, not a description of the test.")
    severity: FindingSeverity = Field(default="major", description="How bad it is if this test fails. blocker means do not ship; do not inflate.")
    targets: list[str] = Field(default_factory=list, description="The files or symbols this test attacks.")
    kind: ProbeKind = Field(default="demonstration", description="`regression` only if this probe still passes once the defect is fixed -- then it is re-run every round and kept in the project's suite. `demonstration` if it proves the defect is there now and would stop meaning anything afterwards; it runs once and is deleted. If you cannot say which, it is a demonstration.")
    passes_when: str = Field(default="", description="For a `regression` probe: what a correct implementation does when this runs, in one line. Required for `regression` -- a probe whose author cannot state this is a demonstration, and saying so costs nothing. Do not guess: a probe that pins a schedule only the defect permits (holding two requests at a barrier that a serialising fix makes unreachable) can never pass, and claiming otherwise is caught by the promotion check anyway.")
    #: What this probe has actually cost, measured, not declared. Set by the
    #: orchestrator from the first run that finished, and carried forward.
    #:
    #: A flat ceiling is the wrong instrument for a probe whose own cost is
    #: known. Two probes that ran in 0.26s and 0.16s measured against 900s is a
    #: factor of about 3,500, and gives a probe that has stopped terminating
    #: fifteen minutes each round to prove it.
    baseline_s: float = Field(default=0.0, description="Seconds this probe took on a run that finished. 0 means it has never finished one.")
    #: Set when a probe was killed on the clock. It is not re-run after that.
    #:
    #: A probe that stopped terminating is reporting about itself, and the one
    #: thing guaranteed not to help is running it again: the same 900s, the
    #: same `exit -9`, the same nothing. On a run capped at two rounds, that
    #: is 1801s in round 1 and 1801s again in round 2.
    quarantined_in_round: int | None = Field(default=None, description="The round this probe was killed on the clock, after which it is not run again.")


class BreakerSuite(BaseModel):
    strategy: str = Field(description="Where you decided the code was weakest, and why you attacked there.")
    command: str = Field(default="", description="The shell command that runs these tests and nothing else, from the repo root. Copy the runner the project already uses.")
    tests: list[BreakerTest] = Field(default_factory=list, description="Adversarial tests. Few and lethal beats many and shallow.")
    conceded: list[str] = Field(default_factory=list, description="What you attacked and could not break. Concessions are what make the failures credible.")
    notes: str = Field(default="", description="Anything about these tests a human needs to know before believing a failure.")


class BreakerTestAccount(BaseModel):
    """One hypothesis, attached to a file that already exists on disk.

    Same split as `TestFileAccount`: the test's text is read from the file, and
    what a human needs -- the claim the test makes about the code -- is the only
    thing asked for. See there for why.
    """

    path: str = Field(description="Repo-relative path of a test file you wrote.")
    hypothesis: str = Field(description="What you claim breaks, in one line. This becomes the finding's text if the test fails, so make it a claim about the code, not a description of the test.")
    severity: FindingSeverity = Field(default="major", description="How bad it is if this test fails. blocker means do not ship; do not inflate.")
    targets: list[str] = Field(default_factory=list, description="The files or symbols this test attacks.")
    kind: ProbeKind = Field(default="demonstration", description="`regression` only if this probe still passes once the defect is fixed -- then it is re-run every round and kept in the project's suite. `demonstration` if it proves the defect is there now and would stop meaning anything afterwards; it runs once and is deleted. If you cannot say which, it is a demonstration.")
    passes_when: str = Field(default="", description="For a `regression` probe: what a correct implementation does when this runs, in one line. Required for `regression` -- a probe whose author cannot state this is a demonstration, and saying so costs nothing.")


class BreakerAccount(BaseModel):
    """The breaker's answer once the harness has written the files."""

    strategy: str = Field(description="Where you decided the code was weakest, and why you attacked there.")
    command: str = Field(default="", description="The shell command that runs these tests and nothing else, from the repo root. Copy the runner the project already uses.")
    tests: list[BreakerTestAccount] = Field(default_factory=list, description="One entry per test file you wrote.")
    conceded: list[str] = Field(default_factory=list, description="What you attacked and could not break. Concessions are what make the failures credible.")
    notes: str = Field(default="", description="Anything about these tests a human needs to know before believing a failure.")


class BreakerReport(BaseModel):
    """Computed from a real process, not asked for. (Same principle as INV-3.)"""

    ran: bool = False
    command: str = ""
    exit_code: int = 0
    tests_written: int = 0
    tests_applied: list[str] = Field(default_factory=list)
    failing: list[str] = Field(default_factory=list, description="Breaker test paths named in the failing output.")
    rejected: list[str] = Field(default_factory=list, description="Tests refused before they ran, and why.")
    output_tail: str = ""
    duration_s: float = 0.0
    note: str = Field(default="", description="Why this report is worth what it is worth. A breaker that could not run says so here.")
    unstarted: str = Field(default="", description="The error that kept the breaker's harness from starting at all, when that is why nothing ran.")
    #: Regression probes that passed the repaired code every time they were
    #: asked to, and are therefore kept in the tree rather than deleted with the
    #: rest. Decided again every round, so the last round's decision is the one
    #: that reaches a human -- a probe kept in round 1 and broken in round 2 is
    #: deleted in round 2, and nothing half-promoted survives into the packet.
    promoted: list[str] = Field(default_factory=list, description="Probe paths kept in the project's test tree.")
    #: Probes not re-run here because they had nothing left to say. A
    #: demonstration proves the defect is present now; once its round is over
    #: the construction can only hang, error, or reward a different defect, and
    #: re-running it buys a fact about the probe rather than about the code.
    retired: list[str] = Field(default_factory=list, description="Demonstrations from an earlier round, not re-run.")
    #: Probes killed on the clock this round. Not a subset of `failing`: a
    #: probe that never finished did not report on anything, and filing it as
    #: a failure puts the breaker's hypothesis on a finding no run supports.
    timed_out: list[str] = Field(default_factory=list, description="Probe paths killed on the clock rather than failing.")
    #: Probes not run because an earlier round killed them on the clock.
    quarantined: list[str] = Field(default_factory=list, description="Probe paths skipped after timing out in an earlier round.")
    #: Regression probes that passed, then did not. A race test can pass by
    #: failing to hit the window, and one promoted on a single green run is how
    #: a permanent intermittent failure gets installed in somebody's suite.
    flaky: list[str] = Field(default_factory=list, description="Regression probes that did not pass every repeat run, and so were not kept.")


# --------------------------------------------------------------------------
# arbiter, remediator, repairer  (the rework loop)
# --------------------------------------------------------------------------

Disposition = Literal["repair", "simplify", "oracle", "escalate", "needs_spec_change",
                      "out_of_spec", "not_a_defect", "dismissed"]

# What the arbiter may choose -- unchanged, and kept separate from `Disposition`
# above so that broadening `Disposition` for `HumanFlag` (below) never hands the
# arbiter's structured output an option ("dismissed") it was never taught the
# meaning of.
ArbiterDisposition = Literal["repair", "simplify", "oracle", "escalate",
                             "needs_spec_change", "out_of_spec", "not_a_defect"]

# What a human may choose, going forward, at the worklist. Two outcomes: send
# it to the repair loop, or don't, for whatever reason ends up in the note.
#
# Two is a rule about the CHOICE, not about where the work can go. Which agent
# a repair belongs to is arithmetic -- a flag on a file only the oracle may
# write is the oracle's to fix, and the arbiter moves it there without asking
# (`FindingLedger.dispose`), because handing that question back to a person is
# asking them to know which agent owns which file. So the menu stays two and
# the destinations do not.
#
# `HumanFlag.disposition` below is typed to the broader `Disposition`, not to
# this -- an older flag filed under a five-way choice is still on record
# (INV-11, nothing here is ever edited or deleted), and narrowing the field
# itself would make those old records fail to parse. This is what is actually
# enforced: the server validates a new flag's disposition against it before the
# flag is ever constructed.
HumanDisposition = Literal["repair", "dismissed"]

FindingOutcome = Literal[
    "open",
    # A condition of the run rather than a defect in the work: which agents the
    # budget could not measure, which route carried a role, what the run set
    # over the project's own configuration. Reported, counted, read once -- and
    # never put in front of a human as something to rule on, because there is
    # no ruling to make. Accepting or sending back a branch does not change
    # which plan was nearly out of room.
    "noted",
    "repaired",
    # Raised by one of the factory's own checks, and that check, run again on
    # fresh evidence, no longer finds it. Settled, like `repaired`, but not
    # counted as a repair: nothing was repaired *for* it -- the condition went
    # away, often because an agent fixed something else.
    "no_longer_holds",
    "attempted_not_fixed",
    "escalated",
    "needs_spec_change",
    "out_of_spec",
    "dismissed",
    "unattempted",
]


class FindingDisposition(BaseModel):
    """Where a finding goes. Routing, not editing.

    The arbiter cannot delete a finding, soften it, or reword it. It says what
    should happen next and why, and every finding reaches the human either way
    with its original text intact. (INV-11)
    """

    finding_id: str = Field(description="The id of the finding you are routing. Use the ids exactly as given.")
    disposition: ArbiterDisposition = Field(description="repair: a mechanical, in-spec fix a worker can make now. simplify: not a defect at all -- duplicated code, an abstraction with one user, a convention break -- which no repair can close because the code is correct, and which is fixed after the loop converges by an agent that may delete things for being unnecessary. oracle: the fault is in how a blind test file is written -- it will not load, it breaks one of the project's checks, its import or setup is wrong -- so only the oracle that wrote it may fix it; never for what a test asserts. escalate: needs a human ruling -- irreversible, a design tradeoff, or a judgment call. needs_spec_change: cannot be fixed without reopening the frozen spec. out_of_spec: the finding asks for more than the contract requires. not_a_defect: it is wrong, and you must say why.")
    reason: str = Field(description="Why this disposition. For not_a_defect this is the whole argument, and the human reads it next to the original finding.")
    duplicate_of: str = Field(default="", description="The id of an earlier finding this one restates, so there is one fix rather than two. Set it whenever it is genuinely the same defect, whether the two came from different agents or from two passes of one -- the harness tells those apart afterwards from who raised each, and only the first is corroboration. Two problems in one file are not duplicates, and neither is a cause and its symptom.")
    repair_hint: str = Field(default="", description="For `repair` only: the narrowest change that would resolve it.")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="Your honest confidence in this routing.")


class ArbiterReport(BaseModel):
    summary: str = Field(description="What this round found and what you are routing where, in a paragraph.")
    dispositions: list[FindingDisposition] = Field(default_factory=list, description="One row per finding you were given. Anything you omit defaults to `escalate` -- to a human, never to silence.")


class RepairUnit(BaseModel):
    id: str = Field(description="R-1, R-2, ...")
    title: str = Field(description="Short name for this repair.")
    finding_ids: list[str] = Field(default_factory=list, description="The findings this unit resolves. A unit with no findings is not a repair.")
    objective: str = Field(description="The narrowest change that resolves those findings, written for a repairer who sees only this unit.")
    files_expected: list[str] = Field(default_factory=list, description="Paths this unit may touch. Units in one round must not overlap here, and nothing outside this list may be written.")
    constraints: str = Field(default="", description="What this repair must not do: what it must not refactor, rename, widen or optimise on the way past.")
    notes: str = Field(default="", description="Anything the repairer must not get wrong.")
    unlocked: list[str] = Field(default_factory=list, description="Verification files this unit, and only this unit, may write. Empty for almost every unit: the tests that judge the code are read-only to the agent fixing it, because the cheapest way to close any finding is to weaken the test that reports it. A path appears here only when a finding routed to this unit says that test is itself wrong -- it asserts something the design makes impossible to observe -- and the routing was done by a human or the arbiter before the round, never by the agent deciding mid-repair that the test looked like the problem.")


class RepairPlan(BaseModel):
    summary: str = Field(description="What this round repairs and why it was cut this way.")
    units: list[RepairUnit] = Field(default_factory=list, description="Repair units with non-overlapping file ownership.")
    deferred: list[str] = Field(default_factory=list, description="Finding ids you are deliberately not attempting this round, each with a reason. Deferring is honest; silently dropping is not.")


class RepairVerdict(BaseModel):
    finding_id: str = Field(description="The finding you are re-checking.")
    status: Literal["fixed", "not_fixed", "fixed_with_new_problem"] = Field(description="fixed: the thing you objected to is gone. not_fixed: it is still there, or the change misses the point. fixed_with_new_problem: resolved, but the repair introduced something else.")
    evidence: str = Field(default="", description="The specific thing in the repair that settles it. Cite the path or the symbol.")


class RecheckReport(BaseModel):
    """A focused re-review: only the findings this agent raised, only whether
    the repair actually resolved them."""

    verdicts: list[RepairVerdict] = Field(default_factory=list, description="One per finding you were asked about. Anything you omit is treated as not fixed.")
    new_findings: list[Finding] = Field(default_factory=list, description="Problems the repair itself introduced. Only what the repair caused -- this is not a fresh review of the whole change.")
    notes: str = Field(default="", description="Anything about the repairs as a set.")


class FindingRecord(BaseModel):
    """One finding's whole life, across every round. Computed by the
    orchestrator; no model writes this and no model can shorten it. (INV-11)"""

    finding_id: str
    role: str = ""
    title: str = ""
    severity: FindingSeverity = "minor"
    first_seen_round: int = 0
    rounds_seen: list[int] = Field(default_factory=list)
    attempts: int = 0
    disposition: Disposition = "escalate"
    disposition_reason: str = ""
    #: Another finding that says the same thing, which this one restates. Set by
    #: the arbiter, which already reads every finding at once and already names
    #: the repeats in prose. The finding is not removed and its text is not
    #: touched -- it is routed to whatever resolves the one it duplicates, so
    #: six agents finding one defect produce one repair and one outcome rather
    #: than six of each. (INV-11)
    duplicate_of: str = ""
    #: The model that raised this, beside the role that raised it.
    #:
    #: Two roles on one model are not two opinions. That is not hypothetical:
    #: the reviewer and the adversary were merged into one agent precisely
    #: because both sat on the same vendor, so the roster promised two readings
    #: and bought one. Recorded per finding so the question can be asked of any
    #: two findings without reconstructing which roster was in force.
    model: str = ""
    #: Findings by a *different* agent that say what this one says.
    #:
    #: This is evidence, and it is what the word means. A defect that the
    #: breaker proved by running code and the reviewer found by reading it has
    #: been reached twice by two methods, and the arbiter is right to weigh
    #: that when choosing `repair` over `escalate`.
    corroborated_by: list[str] = Field(default_factory=list)
    #: Findings by the *same* agent that say what this one says.
    #:
    #: Not evidence, and kept apart for that reason. Review agents are sampled
    #: several times at high temperature on purpose -- one pass sees what
    #: another talked itself out of -- so one defect arrives worded three ways
    #: and the ledger ties the three together. Counting those as agreement is
    #: asking one witness to describe the car three times and recording three
    #: witnesses.
    #:
    #: Counted as agreement, it can cost a packet a blocker. Three samples of
    #: one reviewer restating one hanging probe read to an arbiter as "three
    #: independent reviewers reproduce the identical symptom ... strong
    #: corroboration of a real hang rather than a flaky report", and it routes
    #: the probe for repair. There is one reviewer, one model, and one probe
    #: run, whose single line of output all three have read.
    restated_by: list[str] = Field(default_factory=list)
    outcome: FindingOutcome = "open"
    outcome_evidence: str = ""
    #: The computed check that raised this, for a finding one of them raised.
    #: How a later run of the same check knows which findings are its own to
    #: close when it no longer finds them.
    source: str = ""
    repaired_in_round: int | None = None
    commit: str = ""


class RepairDone(BaseModel):
    """What one repair unit changed, so a round can be opened and read.

    The plan's `RepairUnit` is the instruction; this is the account of what
    came back.

    Without it, the loop's own account of itself reaches a human as counts and
    a list of finding ids: four repaired in round 1, two in round 2, and no way
    to see what either round actually did. The units record all of it -- the
    file, a paragraph on the change, the findings they were given -- but the
    packet carries build units and nothing else, so none of it would leave the
    evidence ledger.
    """

    unit_id: str = Field(description="The unit, named uniquely across rounds: `R2-1`.")
    round: int = Field(description="The repair round that dispatched it.")
    summary: str = Field(default="", description="What the change does, in the unit's own words.")
    files: list[str] = Field(default_factory=list, description="Paths it wrote.")
    finding_ids: list[str] = Field(default_factory=list, description="The findings it was given to close.")
    decision_ids: list[str] = Field(default_factory=list, description="Its decisions, by packet id.")
    disclosures: int = Field(default=0, description="How many disclosure lines it filed.")


class ReworkSummary(BaseModel):
    """What the loop did and what it cost. Every number here is a fact."""

    enabled: bool = True
    rounds: int = 0
    stop_reason: str = Field(default="", description="Why the loop stopped. Always populated: a loop that stops silently is indistinguishable from one that converged.")
    cost_usd: float = Field(default=0.0, description="What this dispatch of the loop cost.")
    lifetime_usd: float = Field(default=0.0, description="What every dispatch of the loop has cost this feature. The budget is measured against this, so sending the same packet back four times does not buy four budgets.")
    budget_usd: float = 0.0
    elapsed_s: float = 0.0
    repaired: int = 0
    attempted_not_fixed: int = 0
    escalated: int = 0
    dismissed: int = 0
    unattempted: int = 0
    reverted_rounds: list[int] = Field(default_factory=list, description="Rounds whose commits were reverted because the checks came back worse than before them.")
    #: What each round actually did, in the order the rounds ran.
    units: list[RepairDone] = Field(default_factory=list, description="The repair units each round dispatched, and what each one changed.")
    protected_writes_refused: list[str] = Field(default_factory=list, description="Repair writes refused for touching the verification surface. (INV-12) An entry here is a finding about the repairer, not a nuisance.")

    # What the budget could not measure. `cost_usd` counts dollars somebody was
    # charged; an agent on a subscription is charged none, so the ceiling above
    # cannot stop it. A run entirely on such routes reads "$0.00 of $10.00" and
    # never stops -- correct arithmetic, and a sentence that means the opposite
    # of what it says unless these are beside it.
    unbilled_roles: list[str] = Field(default_factory=list, description="Agents whose route reports no dollars, so this budget does not constrain them. Their real ceiling is the plan's rolling window.")
    unbilled_turns: int = Field(default=0, description="Turns those agents spent, which is the unit their routes are metered in.")
    notional_usd: float = Field(default=0.0, description="What the unbilled work would have cost had it been billed. Reported so it is not invisible; never counted against the budget, because nobody was charged it.")


# --------------------------------------------------------------------------
# rapporteur
# --------------------------------------------------------------------------


class MaterialityItem(BaseModel):
    path: str = Field(description="The file.")
    klass: MaterialityClass = Field(description="generated: mechanical output of a tool or template. mechanical: forced by the language or framework. conventional: the obvious way, following repo convention. novel: a real choice a human must read.")
    lines: int = Field(default=0, description="Lines this run changed in this file, added plus removed. Overwritten from the branch; what you put here is ignored.")
    added: int = Field(default=0, description="Lines added. Measured, not reported.")
    removed: int = Field(default=0, description="Lines removed. Measured, not reported.")
    reason: str = Field(description="Why this class. Required, because the classification is the claim.")


class TraceRow(BaseModel):
    criterion_id: str = Field(description="AC id.")
    statement: str = Field(default="", description="The criterion text.")
    implementing_files: list[str] = Field(default_factory=list, description="Files claimed to implement it.")
    test_names: list[str] = Field(default_factory=list, description="Blind tests tagged to it.")
    status: TraceStatus = Field(default="traced", description="traced, orphan_requirement (nothing implements it), untested (implemented, no blind test), or manual (implemented, at a test level this project cannot run cleanly, so a person checks it by hand).")


class UnclaimedFile(BaseModel):
    """A file no criterion accounts for. The inverse of a `TraceRow`.

    Computed by inverting the same index `compute_trace` builds, so a file
    cannot reach this list because a model chose to mention it, and cannot
    escape it by staying quiet. (INV-3)
    """

    path: str = Field(description="Repo-relative path.")
    lines: int = Field(default=0, description="Lines this run changed in this file, added plus removed.")
    added: int = Field(default=0, description="Lines added.")
    removed: int = Field(default=0, description="Lines removed.")
    written_by: list[str] = Field(default_factory=list, description="Unit ids that wrote it.")
    disclosures: list[str] = Field(default_factory=list, description="What those units said about their own work, verbatim. Usually the only account of why an unclaimed file exists.")


class PacketStats(BaseModel):
    gates_passed: int = 0
    gates_total: int = 0
    criteria_total: int = 0
    criteria_verified: int = 0
    #: Criteria left to a person because their test level cannot run cleanly
    #: here. Not failures and not verified: the part of the promise nobody
    #: automated, counted so a reader can see how much of it that is.
    criteria_manual: int = 0
    orphan_requirements: int = 0
    untested_criteria: int = 0
    unclaimed_files: int = 0
    #: Blockers still standing. `blockers_raised` keeps the total ever found,
    #: because the record only grows even when the loop fixes things (INV-11).
    blockers: int = 0
    blockers_raised: int = 0
    files_written: int = 0
    #: Lines this run changed across the files it wrote, added plus removed --
    #: the size of the diff, not the size of the files. See `Sandbox.diffstat`.
    lines_written: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    decisions_total: int = 0
    findings_total: int = 0
    #: Distinct defects, and how many of the findings were restatements of one.
    findings_distinct: int = 0
    findings_duplicate: int = 0
    findings_repaired: int = 0
    findings_open: int = 0
    breaker_failures: int = 0
    #: The suite exited non-zero. Separate from `breaker_failures`, which counts
    #: probes named in the output: a suite that dies before any probe reports
    #: names none, and read alone that zero says the adversary found nothing.
    breaker_suite_failed: bool = False
    #: Agents that did not come back. Every count below them was computed as
    #: though they had run and reported nothing.
    agent_failures: int = 0
    rework_rounds: int = 0


class PacketJudgement(BaseModel):
    """What the rapporteur actually decides.

    The Packet it used to be asked for made it restate every finding, every
    decision and every trace row -- most of which `restore_packet` overwrites
    the moment it returns. On a small model that is the whole output budget
    spent on answers nobody reads, and it truncated mid-packet.

    Ordering is expressed as ids. The rapporteur can say what matters most and
    what matters least; it cannot reword a finding on the way past, and it
    cannot omit one, because the text of every finding comes from the agent
    that raised it and anything left out of the order is appended anyway.
    """

    headline: str = Field(description="One sentence a human reads first: what this is and whether it ships.")
    gist: str = Field(default="", description="The headline cut to what someone scanning a list of features needs: under 15 words, plain, no file or function names, and not a verdict (the verdict is shown beside it). 'Works and is fully tested, but nothing runs the new migration.' Written for a person, and shown on the project overview.")
    verdict: Literal["ship", "ship_with_rulings", "send_back", "reject"] = Field(default="ship_with_rulings", description="Your recommendation. The human decides; you advise.")
    attention_budget_minutes: int = Field(default=15, description="Honest minutes a competent reviewer needs to rule on this. Not an aspiration.")
    summary: str = Field(default="", description="What was built and what a reviewer should be uneasy about, in a paragraph.")
    strongest_objection: str = Field(default="", description="The single strongest argument against shipping this, stated as its proponent would state it.")
    materiality: list[MaterialityItem] = Field(default_factory=list, description="Every written file, classified. Anything you leave out defaults to `novel`, never to safe.")
    decision_order: list[str] = Field(default_factory=list, description="Decision ids, most in need of a human ruling first. Ids only -- the decisions themselves are already recorded. Anything you omit is appended after these.")
    finding_order: list[str] = Field(default_factory=list, description="Finding ids, most severe and most likely to be right first. Ids only. Anything you omit is appended after these.")


class ManualCheck(BaseModel):
    """A criterion Fabrika did not test, handed to a person to check by hand.

    Its test level cannot run cleanly in this project -- no runner, or tests
    that cannot clean up after themselves -- and a test there would fail for
    reasons that say nothing about the code. So it is not attempted, not
    counted as failed, and not left silent either: it comes to review as a
    check with the criterion's own account of how to confirm it.
    """

    criterion_id: str
    title: str = ""
    statement: str = ""
    how: str = Field(default="", description="How to confirm it by hand: the criterion's `check_by_hand`, written for a person.")
    level: str = ""
    reason: str = Field(default="", description="Why this level is not tested here, in plain words.")


class Packet(BaseModel):
    """The product. A structured argument that the code does what was specified."""

    headline: str = Field(description="One sentence a human can act on: the verdict, and the one reason for it, in plain words. 'Don't ship yet: people can see labels on cards in workspaces they don't belong to.' No criterion ids, counts, file names or confidence figures -- `summary` elaborates, and that is where they go. Not a summary of activity.")
    gist: str = Field(default="", description="The headline in under 15 plain words, for the project overview's list of features. See `PacketJudgement.gist`.")
    verdict: Literal["ship", "ship_with_rulings", "send_back", "reject"] = Field(default="ship_with_rulings", description="Your reading of where this stands.")
    attention_budget_minutes: int = Field(default=10, description="How many minutes of human attention this packet honestly needs.")
    summary: str = Field(default="", description="The argument, in a paragraph: the headline elaborated -- the evidence, the criterion ids, the numbers that support it.")
    strongest_objection: str = Field(default="", description="The adversary's best case, restated fairly.")
    materiality: list[MaterialityItem] = Field(default_factory=list, description="One row per written file. Classify honestly; unsure means novel.")
    decisions: list[Decision] = Field(default_factory=list, description="Every decision, ranked by how much it needs a human. Ordering is your product.")
    findings: list[Finding] = Field(default_factory=list, description="Every finding, ordered by severity.")
    trace: list[TraceRow] = Field(default_factory=list, description="Ignored on return: the orchestrator substitutes the computed matrix.")
    unclaimed: list[UnclaimedFile] = Field(default_factory=list, description="Ignored on return: the orchestrator substitutes the files no criterion accounts for.")
    disclosures: list[str] = Field(default_factory=list, description="Worker self-disclosures worth surfacing, verbatim.")
    open_questions: list[str] = Field(default_factory=list, description="Questions carried unanswered into the build.")
    stats: PacketStats = Field(default_factory=PacketStats, description="Ignored on return: the orchestrator computes these.")
    records: list[FindingRecord] = Field(default_factory=list, description="Ignored on return: the orchestrator substitutes what actually happened to every finding.")
    rework: ReworkSummary = Field(default_factory=ReworkSummary, description="Ignored on return: the orchestrator computes what the loop did and what it cost.")
    manual_checks: list[ManualCheck] = Field(default_factory=list, description="Ignored on return: the orchestrator lists the criteria Fabrika did not test, for a person to check by hand.")
    guides: list[dict[str, Any]] = Field(default_factory=list, description="Ignored on return: the orchestrator lists the guides this feature was held to -- path, hash, whether any agent had only part of it, and which roles were handed it.")


# --------------------------------------------------------------------------
# gates (no model involved)
# --------------------------------------------------------------------------


#: The three questions a check can be asking. Empty is a check recorded before
#: there were families, and the console says so rather than guessing one.
CheckFamily = Literal["", "structure", "quality", "tests"]

#: Fields a check gained after projects had already been approved. The approval
#: fingerprint leaves each out while it is empty, so a project approved before
#: they existed is still the project that was approved.
GATE_FIELDS_SINCE_APPROVAL = ("family", "config_files", "suppressions", "report_format",
                              "report_path", "patch_max", "patch_min", "files_command")

#: Fields that say what a check covers rather than how it runs. Changing one
#: changes nothing measured, so it neither clears the baseline nor needs approving.
DESCRIPTIVE_GATE_FIELDS = ("also", "own_floor")

#: The same for an environment: what to ask it about itself, and which files say
#: what the project's own runs use. Neither changes what the checks run in.
DESCRIPTIVE_ENV_FIELDS = ("version_commands", "version_files")


class RuleSet(BaseModel):
    """Rules a check enforces that answer another family's question.

    A linter's configuration often carries more than style: ruff's `S` rules
    are a security scan, `eslint-plugin-jsx-a11y` is an accessibility check.
    The check has one family; this says which other questions its rules
    already answer, so that family is not shown empty -- and not suggested a
    tool for something that is already enforced.
    """

    family: Literal["structure", "quality", "tests"]
    what: str = Field(description="The rules, in a few words a person recognises: `security rules (ruff S)`, `accessibility rules (jsx-a11y)`.")
    evidence: str = Field(default="", description="Where they are switched on: the config file and the line, as found.")

#: What a check can write about what it found. SARIF says where each finding
#: is; the three coverage formats say which lines ran.
ReportFormat = Literal["", "sarif", "cobertura", "lcov", "coverage-json", "mutation-json"]
COVERAGE_FORMATS = ("cobertura", "lcov", "coverage-json")


class Located(BaseModel):
    """One thing a check found, and where -- read from the report it wrote.

    The only way to tell the findings a feature added from the ones the
    repository already had: by the lines they are on, against the lines the
    feature changed. Counting them would not do it; a feature that fixes one
    old problem and adds one new one leaves the count where it was.
    """

    path: str
    start_line: int = 0
    end_line: int = 0
    rule: str = ""
    message: str = ""


class OwnFloor(BaseModel):
    """A bound the project sets for itself, enforced by the check's own tool.

    Coverage's `fail_under`, a runner's coverage threshold, a linter's maximum
    warnings: the tool exits non-zero when its reading crosses it. Declared so a
    red check can be read for what it is -- under the project's own floor -- and
    so nothing here offers to hold it below what the project asks of itself.
    """

    where: str = Field(description="Where the bound lives: `file#dotted.section.key` for a setting -- `api/pyproject.toml#tool.coverage.report.fail_under`, `package.json#jest.coverageThreshold.global.lines` -- read at every run; or `command` when it is a flag in the check's own command, with the number in `value`.")
    value: float | None = Field(default=None, description="The number itself, when it cannot be read as data: with `where: command`, the number the flag sets; for a setting in a config file that is code -- `vite.config.ts`, `jest.config.js` -- the number written there. A setting in TOML, JSON or INI is read at every run and needs none.")
    points: Literal["floor", "ceiling"] = Field(default="floor", description="`floor` when the reading must be at least this -- coverage. `ceiling` when it must be at most this -- a maximum count of warnings.")


class Gate(BaseModel):
    """A check is a fact: a process that exited zero, or a number that cleared a
    threshold. Checks belong to a project -- there is no sensible global default
    across a Python service and a Node app."""

    name: str = Field(description="Short identifier: types, lint, tests, sast, secrets, mutation.")
    command: str = Field(description="The shell command, run in the sandbox at the repo root.")
    timeout_s: float = Field(default=600.0, description="Killed past this, and the check fails.")
    parse_metric: str = Field(default="", description="Optional regex with one capture group, for signals the exit code does not carry.")
    threshold: float | None = Field(default=None, description="The captured metric must be at least this. A floor: coverage, a pass rate, a score.")
    threshold_max: float | None = Field(default=None, description="The captured metric must be at most this. A ceiling, for counts that must not grow -- errors, warnings, findings. This is what makes a check ratchetable: a repository with 129 lint errors sets the ceiling at 129, the check reports green today, and it goes red the moment anything adds the 130th. Both bounds may be set, and then the metric must sit between them.")
    optional: bool = Field(default=False, description="A failure is recorded but does not fail the run. An optional check is not a check -- and at repo ready it does not exempt one either: a check must be fixed, ratcheted or declined.")
    family: CheckFamily = Field(default="", description="What this check asks of the code. `structure`: is it well-formed -- a linter, a type checker, a formatter, import or dependency rules. `quality`: is it healthy -- complexity, duplication, static security analysis, secret scanning, vulnerable dependencies, mutation score, accessibility. `tests`: does it behave -- a test suite at any level, or coverage. One family per check.")
    config_files: list[str] = Field(default_factory=list, description="Where this check's settings live, repo-relative: its own config file, or the section of a shared file that holds them, written `file#dotted.section` -- `pyproject.toml#tool.<tool>`, `package.json#<key>`, `setup.cfg#<section>`. A feature that changes these changes the measurement rather than the code, so they are protected from repair and any change to them is reported. Name the section, never a whole shared file: a shared file also lists dependencies, and a feature may legitimately add one. Only files, and sections, that exist.")
    suppressions: list[str] = Field(default_factory=list, description="The markers that make this check look away from one line or one file, exactly as a developer writes them in source. A new one in a feature's changes is reported beside this check. Empty when the tool has none.")
    report_format: ReportFormat = Field(default="", description="What the command writes about what it found, to `{report}`: `sarif` for findings with file and line (a linter, a type checker, a scanner); `cobertura`, `lcov` or `coverage-json` for which lines ran (a coverage run). The placeholder goes in the command where the tool's output-file flag takes its path, and nothing else about the command changes. This is what lets a check be held on a feature's own lines alone. Empty for a tool that can write none of these.")
    report_path: str = Field(default="", description="Only for a tool that cannot be told where to write its report: the repo-relative file it always writes, such as `coverage/lcov.info`. Read after the command runs, and only if the run rewrote it. Empty whenever `{report}` can be used instead.")
    patch_max: float | None = Field(default=None, description="How many of this check's findings may sit on lines a feature added or changed. Needs a `sarif` report. Set by a person at gate 0 -- never proposed -- and usually zero: no new finding, whatever the repository already had.")
    patch_min: float | None = Field(default=None, description="The share, in percent, of the lines a feature added or changed that its tests must run. Needs a coverage report. Set by a person at gate 0 -- never proposed.")
    also: list[RuleSet] = Field(default_factory=list, description="Rule sets this check's configuration switches on that answer another family's question -- security rules a linter runs, accessibility rules loaded into it. Only rules the config actually loads, each with the line that loads it. Empty for most checks.")
    own_floor: OwnFloor | None = Field(default=None, description="When this check's tool enforces a bound the project keeps in its own settings or command -- `fail_under`, a coverage threshold, `--max-warnings` -- where it lives and which way it points. Only a bound the tool itself fails on, and only one that is there. None otherwise, which is most checks.")
    files_command: str = Field(default="", description="For a coverage or mutation check: the same tool run on some files only, with `{paths}` where the files go and `{report}` where its report is written -- `pytest --cov --cov-report=json:{report} {paths}`, `npx stryker run --mutate {paths} --reporters json --jsonReporter.fileName {report}`. For coverage `{paths}` is test files; for mutation it is the source files to mutate. This is what lets a worker be measured, during its own turn, on the files it wrote alone. Empty when the tool cannot be pointed at files.")


BaseOutcome = Literal["not_checked", "passed", "failed", "could_not_run"]

RedKind = Literal["", "didnt_run", "timed_out", "no_report", "under_own_floor", "over_bound",
                  "findings", "flaky", "failed"]


class GateResult(BaseModel):
    name: str
    command: str = ""
    exit_code: int = 0
    passed: bool = False
    output_tail: str = ""
    duration_s: float = 0.0
    metric: float | None = None
    threshold: float | None = None
    threshold_max: float | None = None
    timed_out: bool = False
    #: Which individual files were killed on the clock, for a per-file run.
    #: `timed_out` is the whole command's, and for a run that executes one
    #: command per file that is not the same question -- the caller needs to
    #: know *which* file never finished, because a file that was killed did not
    #: report on anything and a file that failed did.
    timed_out_files: list[str] = Field(default_factory=list)
    #: Seconds each file's own command took. What lets a later run measure a
    #: file against what it has actually cost rather than against a ceiling
    #: chosen for the slowest thing the harness can imagine.
    file_seconds: dict[str, float] = Field(default_factory=dict)
    skipped: bool = False
    started: bool = Field(default=True, description="Whether the process was launched at all. False means nothing ran, which is not the same as failing.")
    rewrote: list[str] = Field(default_factory=list, description="Files this command changed when run on an untouched checkout, measured at baseline. Non-empty means it is a fixer -- a formatter, `lint --fix` -- which a build runs first each round and commits, rather than a check it judges by.")
    # Read from the report and used in the same process; never written to
    # the ledger, which a repository with thousands of findings would fill.
    located: list[Located] = Field(default_factory=list, exclude=True, description="What the check found, file and line, read from the report it wrote. Empty with `report` at `read` means it found nothing; empty otherwise means nobody knows.")
    coverage: dict[str, list[list[int]]] = Field(default_factory=dict, exclude=True, description="Per file, [lines that can run, lines that ran], read from a coverage report.")
    report: Literal["", "read", "missing", "unreadable"] = Field(default="", description="What became of the report this check was asked to write. Empty for a check that writes none. `missing` and `unreadable` are not a clean result: nothing can be said about what the check found.")

    # -- attribution ------------------------------------------------------
    # What this same gate did at the commit the feature branched from. Set by
    # the orchestrator, never by a model, and only for gates that failed:
    # a green gate has nothing to attribute.
    #
    # This is what makes a red gate readable. "backend-lint failed" on a repo
    # with 129 pre-existing errors says nothing about the feature. "Failed, and
    # failed identically before this feature" says it is not the feature's.
    # "Failed, and passed before" says it is.
    at_base: BaseOutcome = Field(default="not_checked", description="How this check behaved at the feature's base commit.")
    failed_on: Literal["", "blind_tests", "oracle_files", "seam_tests", "code"] = Field(default="", description="Which files a failure belongs to, found by running the check again with this feature's own test files set aside -- the failing blind tests first, then the integrator's seam checks, then all of the oracle's files. `blind_tests`: it passes once this feature's failing blind tests are gone, so the criterion table already says what failed. `seam_tests`: it passes once the integrator's seam checks are gone, so the check is red for a test this run wrote and the integrator owns it. `oracle_files`: it passes only once the oracle's other files are gone too, so the problem is in files the oracle wrote. `code`: it fails without any of them, so it is about the rest of the branch. Empty when the check passed or was not sorted.")
    leaked: list[str] = Field(default_factory=list, description="Files that passed, then failed when run again straight after with nothing reset between: each leaves something behind that breaks its own next run -- and, by the same token, whatever runs after it.")
    leak_confirmed: list[str] = Field(default_factory=list, description="Of `leaked`, the ones that passed a third time after the project's reset: the failure was leftover state, not chance.")
    unstable: list[str] = Field(default_factory=list, description="Of `leaked`, the ones that failed again even after the project's reset: something other than leftover state changes their outcome. Not attributed to anyone.")
    leak_output: dict[str, str] = Field(default_factory=dict, description="What a leaking file printed on the run that failed.")
    unreset: bool = Field(default=False, description="Sorted to `code` by a re-run that could not start clean: this feature's test files were set aside, but the project declares no reset, so whatever they wrote on the first run was still there.")
    base_sha: str = Field(default="", description="The commit `at_base` was measured at.")
    base_output_tail: str = Field(default="", description="What it printed there, for comparing against what it printed here.")
    red_kind: RedKind = Field(default="", description="What kind of red this is, computed by code from what was read: `didnt_run`, `timed_out`, `no_report`, `under_own_floor`, `over_bound`, `findings`, `flaky` or `failed`. Empty when it passed.")
    own_floor_value: float | None = Field(default=None, description="The bound the project sets for itself, read from its settings for this run. None when the check declares none, or it could not be read.")
    diagnosis: str = Field(default="", description="The fingerprint of the diagnosis that explains this red result, when one was made or reused for it.")
    output_head: str = Field(default="", description="The first part of the output, kept for a red check whose output was longer than its tail: the cause is often said first and the summary last.")
    warmed: bool = Field(default=False, description="Failed offline at the baseline because its tool fetches part of what it needs only when it runs -- Maven's test provider, say -- and stopped failing that way once it had run with the network during setup. Every setup now runs it that way once.")

    # -- evidence, parsed from the COMPLETE output before it was truncated ---
    #
    # `output_tail` is a display artefact: the last TAIL_CHARS of what the
    # runner said. A per-test verdict computed by searching it cannot see a
    # test file whose name straddles that boundary: a file name cut mid-word
    # has no failure attributed to it, and the criteria it covers are awarded
    # a pass. So no verdict is read off the tail. A green produced by a buffer
    # size is the worst defect this system can have, because it is the one a
    # human cannot see by reading the packet.
    #
    # Everything below counts FILES, not tests, and is set by `run_per_file`:
    # one command per test file, the file's exit code as its verdict. There is
    # no finer grain available and there is deliberately no attempt to invent
    # one -- reading a runner's console output for per-test facts is what
    # reported two erroring criteria to a human as passed.
    output_chars: int = Field(default=0, description="Length of the runner's complete output, of which output_tail is the tail.")
    output_truncated: bool = Field(default=False, description="Whether output_tail is missing anything.")
    evidence_parsed: bool = Field(default=False, description="Whether this result was produced by this version of the code. False means it predates the per-file run and can settle no negative.")

    witnessed: list[str] = Field(default_factory=list, description="Test files this check was asked to settle, each run as its own command.")
    named_failing: list[str] = Field(default_factory=list, description="Of `witnessed`, the files whose own command exited non-zero.")
    named_unloadable: list[str] = Field(default_factory=list, description="Of `witnessed`, the files that could not even be loaded -- a broken import, a syntax error, a file collecting no tests at all, or one whose environment could not be reset before it ran. These settle nothing, and reporting them as failures blames the feature for the harness.")
    file_output: dict[str, str] = Field(default_factory=dict, description="What each file that failed printed, per file, when a check runs file by file -- the error the agent that wrote the file is shown when it is asked to fix it.")
    cases: list[CaseOutcome] = Field(default_factory=list, description="Individual test outcomes, read from the runner's own machine-readable report. Empty when no `report` command is configured, and then a file's exit code is the whole verdict for every criterion it carries.")

    summary_known: bool = Field(default=False, description="Whether this check ran file by file, which is the only arrangement in which a result can be attributed to a criterion.")
    tests_passed: int = Field(default=0, description="Test FILES whose command exited zero.")
    tests_failed: int = Field(default=0, description="Test FILES whose command did not.")
    tests_total: int = Field(default=0, description="Test FILES run.")
    zero_ran: bool = Field(default=False, description="There was nothing to run.")

    @property
    def pre_existing(self) -> bool:
        """Failed here, and failed there. Not this feature's doing."""
        return not self.passed and not self.skipped and self.at_base == "failed"

    @property
    def caused_here(self) -> bool:
        """Failed here, passed there. This feature did it."""
        return not self.passed and not self.skipped and self.at_base == "passed"


class GateReport(BaseModel):
    results: list[GateResult] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results if not r.skipped)

    @property
    def blocked(self) -> bool:
        """Nothing ran, so nothing was measured.

        Not the same question as `not passed`. A failing gate measured
        something and the failure is a fact about the code; a gate that never
        started measured nothing, and the red beside it is a fact about the
        machine. `started` has recorded the difference all along -- this is the
        caller that finally asks, because the answer decides whether there is
        anything to repair. A run that cannot tell them apart spends repair
        rounds inventing fixes for checks that never executed.
        """
        live = [r for r in self.results if not r.skipped]
        return bool(live) and not any(r.started for r in live)



# --------------------------------------------------------------------------
# projects and their environments
# --------------------------------------------------------------------------

EnvironmentKind = Literal["host", "reuse", "derive", "generate", "compose"]


class TestFileCommand(BaseModel):
    """How to run one test file, for the files this rule matches.

    A list rather than one string, because a repository can hold more than one
    runner and the alternative is the model writing a shell conditional. It did
    exactly that when this was a single field -- `if [[ "$p" == frontend/* ]];
    then npx vitest ...; else pytest ...; fi` -- which is logic written blind,
    in a shell that may not be bash, by an agent that cannot run it. The same
    class of thing as the `nohup ... kill` dance, one layer down.

    So the branching is declared and the harness does the matching.
    """

    match: str = Field(default="*", description="A glob matched against the repo-relative path of a test file: `frontend/*`, `*.test.tsx`, `backend/*`. Rules are tried in order and the first match wins, so put the specific ones first and a catch-all `*` last. A project with one runner needs one rule and can leave this as `*`.")
    command: str = Field(description="The command that runs ONE test file, with `{path}` where the path goes: `pytest {path}`, `npx vitest run {path}`, `cd backend && pytest ../{path}`. The invocation and nothing else -- installing goes in `setup`, anything that must be running goes in `services`, migrations and seeding go in `test_prepare`.")
    report: str = Field(default="", description="Optional. The command that RUNS one test file and writes a JUnit XML report to `{report}`, with `{path}` where the path goes: `pytest --junitxml={report} {path}`, `npx vitest run --reporter=junit --outputFile={report} {path}`, `npx playwright test --reporter=junit {path}` with PLAYWRIGHT_JUNIT_OUTPUT_NAME={report}. Give it wherever the runner can emit one -- pytest has it in core, and vitest, jest, playwright, go, rspec and dotnet all ship it, which is why it is the format asked for rather than each runner's own. It is what turns `the file failed` into `this test failed`: one test disagreeing about one field once took fourteen criteria down with it because a file was the smallest thing that could be judged. `{report}` is replaced with an absolute path: never prefix it with `../` or `$PWD/`, even when the command changes directory first. Leave it empty if this runner cannot, and the file's exit code remains the only verdict.")
    @field_validator("command")
    @classmethod
    def _command_takes_no_report(cls, value: str) -> str:
        """`{report}` belongs to `report` and nowhere else.

        Nothing substitutes it in `command`, so a rule carrying it there runs a
        command with a literal `{report}` in it. The two fields are adjacent on
        the page and describe the same invocation, which is exactly how a
        reading copies one into the other -- for one of its two rules, and not
        the other, so neither the page nor a reader would catch it.

        Raised rather than quietly stripped: a schema failure is asked again and
        fixed by the reading that made it, where a silent repair would leave a
        human approving one command and the project running another.
        """
        if "{report}" in (value or ""):
            raise ValueError(
                "`command` runs one test file and nothing substitutes `{report}` in "
                "it. Put the reporting invocation in `report`, and leave `command` "
                "as the plain one.")
        return value

    collect: str = Field(default="", description="Optional. The command that LOADS one test file without running it, with `{path}` where the path goes: `pytest --collect-only -q {path}`, `npx vitest list {path}`. It answers a question the run cannot: a file that fails to import and a file whose assertions fail both exit non-zero, and they are opposite facts -- the first is the test author's bug and settles no criterion, the second is the finding this whole system exists to produce. Leave empty if this runner has no such mode; the suite then runs exactly as before and the distinction is simply not drawn.")


class BlindPlacement(BaseModel):
    """Where a test written by an agent that has never seen this repository goes.

    Placement used to be a constant in this tool -- `oracle_dir = "tests/oracle"`
    -- and that is the same error as naming a framework in code, one dimension
    over. A test file is not a free-standing thing a command can be pointed at.
    Where it sits decides which configuration applies to it, which shared setup
    loads automatically, how its imports resolve, and whether the runner
    collects it at all.

    Both halves of that were measured on a real repository, and both were fatal.
    pytest takes its rootdir from the *argument's* ancestors, so a file outside
    the tree that owns `pytest.ini` is run with no ini at all: every `async def`
    test in a blind suite failed with "async def functions are not natively
    supported", on every round of every run, for a reason that had nothing to do
    with any feature. And vitest's `include` is relative to its own root, so a
    file outside it is not failing but invisible -- `No test files found` -- which
    made every user-facing criterion unverifiable by construction.

    So the project says where such a file goes, exactly as it says how to run
    one, and this tool learns no framework's name to use the answer.

    The two canaries are what stop this from being one more claim nobody checks.
    They are written in the project's own idiom by the one role allowed to know
    what that idiom is, and the harness proves the placement with them at repo ready:
    the passing one must be reported as passing, the failing one must be reported
    as failing, and the project's own checks must not notice either. A placement
    that cannot show all three is not usable for blind tests, and saying so at
    repo ready costs a minute where discovering it in a packet costs a run.
    """

    kind: str = Field(default="", description="A short label for what kind of test goes here, for a human reading the list: `backend pytest`, `frontend component`. Free text; nothing matches on it.")
    directory: str = Field(description="Repo-relative directory where a blind test of this kind must be written, chosen so THIS project's runner collects and configures it: it must be inside whatever tree owns the runner's configuration and shared setup. The project's own checks may collect it too, and usually will: a blind test is code on the branch like any other, and a check is never narrowed to leave it out.")
    filename: str = Field(description="What a file in that directory must be called for the runner to collect it: `test_blind_canary.py`, `BlindCanary.test.tsx`. Naming is a collection rule in most runners and getting it wrong looks exactly like a suite that found nothing.")
    canary_passes: str = Field(description="The complete source of a test file that contains one test, which passes. Write it the way a real test in THIS project is written -- the same imports, the same style, and async if this project's tests are async. It is not a sample of good practice, it is a measurement instrument: what it proves is that a file placed here is collected, configured and reported honestly, and it can only prove that for the idiom it is written in.")
    canary_case: str = Field(default="", description="The name of the one test inside `canary_passes`, written EXACTLY as you would declare it in `cases[].name` for a real blind test -- for most runners the test's own name and nothing else: `test_blind_canary`, `adds a tag`. Repo ready runs the reporting command against that canary and compares this string with what the report calls it, which is the only way this factory can know how this runner names a test. Leave it empty only if you cannot tell, and the measurement is skipped rather than guessed.")
    canary_fails: str = Field(description="The same file, with its one test failing instead -- a bare failed assertion is ideal. This is what proves the placement can report bad news: a runner that does not collect the file at all exits zero over nothing, which is indistinguishable from success and would make every criterion read as verified.")


class PlacementResult(BaseModel):
    """What was measured about one placement, at repo ready, in the real sandbox."""

    kind: str = Field(default="")
    directory: str = Field(default="")
    path: str = Field(default="", description="The canary's repo-relative path, as it was actually written.")
    command: str = Field(default="", description="The per-file command that claimed that path, with `{path}` substituted.")
    passing_ran: bool | None = Field(default=None, description="The passing canary was reported as passing. None means the measurement itself could not be taken, which is never the same as a pass.")
    failing_seen: bool | None = Field(default=None, description="The failing canary was reported as failing. False here is the dangerous one: the runner did not collect the file and exited zero over nothing.")
    subdirs_ok: bool | None = Field(default=None, description="Whether a file one directory deeper is collected and configured the same way. Measured, because it decides whether each feature's acceptance tests can have their own folder: two `test_screening.py` files in sibling directories are an import-mismatch error under some pytest layouts and fine under others, and that is exactly the kind of thing this tool must never assume. None means unmeasured; False means blind tests have to stay flat in this directory and their names must not collide.")
    reported_case: str = Field(default="", description="What this runner's own JUnit report called the canary's one test. Empty means the measurement was not taken: no `report` command, no `canary_case` to compare it with, or no report written.")
    case_named_as_declared: bool | None = Field(default=None, description="Whether a criterion tagged to that test would actually be attributed to it -- the same join the packet does. False means this runner qualifies a test's name with something the oracle does not write, so every criterion in every file here comes back `unknown`: nine did on card-tags-8e21a7, each one of their tests having run and passed. It does not make the placement unusable, because per-test attribution is additive and losing it costs resolution rather than correctness. None means unmeasured, which is never the same as a match.")
    usable: bool = Field(default=False, description="All three above are true. Only then can a blind test be written here. The naming measurement is deliberately not one of them.")
    note: str = Field(default="", description="Plain sentence naming what failed, for the human at repo ready.")
    evidence: str = Field(default="", description="The commands that were run and what they printed.")


class SetupFile(BaseModel):
    """A file a new test may use, carried with the survey rather than read later.

    Contents travel with the record so the verify lane never touches a
    filesystem to get them. What the oracle is shown is what a human approved at
    repo ready, which is the same guarantee the runtime contract has and the same
    reason: provenance is the only filter prose can be given. (INV-1)
    """

    path: str = Field(description="Repo-relative path.")
    contents: str = Field(description="The complete file.")


class CleanupOption(BaseModel):
    """One complete way to stop tests at a level leaking into each other.

    Picked whole, applied whole, in the right order: its files are written,
    then the environment is changed. A fix that needs real code is
    not applied at all: it is a prompt for the person's own coding agent. A person
    chooses between options, never between the parts of one -- a reset script
    and the step that runs it were once two separate cards, and ticking one
    without the other was either a file nobody called or a step that failed.
    """

    title: str = Field(description="What choosing this does, as an action, under 8 words: 'Reset the data between runs', 'Fail any test that reaches the real network'. Plain words, no file or function names.")
    summary: str = Field(description="ONE plain sentence under 25 words for the person choosing: what stops leaking between tests once this is in, and anything it still does not cover. Say it in terms of what one test can do to the next, never in terms of the mechanism -- not 'restores the real network', but 'one test's fake replies can no longer answer the next test'.")
    files: list[str] = Field(default_factory=list, description="Paths of the support files in THIS answer's `scaffolding` that this option writes or replaces. Every path must be one you proposed there.")
    environment: bool = Field(default=False, description="True if this option applies THIS answer's `environment` -- the `test_prepare` step that puts data back. Anything that has to be RUN, like a reset script, needs this: a script nothing runs fixes nothing.")
    agent_prompt: str = Field(default="", description="When the fix needs real code in this repository: the exact `title` of a recommendation (kind `tests`) in THIS answer, which the person copies as a prompt for their own coding agent, then re-surveys. Never a feature for this factory to build -- its own runs need this to exist first, and building it through one of them is circular.")
    recommended: bool = Field(default=False, description="True on the one option you would pick, including one that is an `agent_prompt`. Prefer an option that makes the leak impossible over one that tidies up after it -- a test that calls an unfaked dependency should fail, not be cleaned up after.")


class TestingTier(BaseModel):
    """What a new test at this level can be written against, if anything.

    The question that matters is not "does this project have tests" -- a project
    can have a full suite and still offer a new test author nothing. It is
    whether *reusable setup* exists: a client that authenticates, a factory that
    makes a row, a fixture that hands over a database.

    A real project had pytest and vitest configured, a green suite, and one
    integration test that built its own client, its own engine and its own
    environment inline. "Has tests" was true and useless. An agent asked to
    verify twenty-five criteria against it had nothing to reuse, invented a
    fixture contract nobody had agreed to, and every criterion came back
    unverified.
    """

    tier: Literal["unit", "integration", "user"] = Field(description="unit: logic in isolation, no I/O -- INCLUDING a front-end component rendered with hand-built props under jsdom, which is a unit test of the front end however much of the UI it draws. integration: the app's own interfaces -- HTTP, database, CLI -- exercised in-process. user: a surface a person or client uses, driven the way they drive it with the whole stack running and nothing mocked -- a browser against the real front and back end, or an HTTP client against the running API where the API is the surface. The line is what is REAL: a component test mocks away routing, the network, the API contract and the data, so it can pass in full while the application is broken, and a criterion written about what a user sees cannot honestly rest on one. A project with no way to drive its surface for real has no `user` tier -- say `absent` and let the recommendation for one carry the weight.")
    runner: str = Field(default="", description="What runs tests at this level, in the project's own words: pytest, vitest, playwright. Empty when there is none.")
    setup_files: list[SetupFile] = Field(default_factory=list, description="Reusable setup a NEW test may use: a conftest, a fixture module, a factory, a render helper, a testing README. Never a file that asserts -- a test is not setup, and handing one to a blind test author shows it the answers.")
    fixtures: list[str] = Field(default_factory=list, description="The named fixtures and factories those files provide, so a reader can see at a glance what a test can get hold of: `api_client`, `make_patient`, `disposable_db`.")
    import_examples: list[str] = Field(default_factory=list, description="How a test at this level imports THE CODE IT IS TESTING, as whole lines copied verbatim out of test files that already exist and already pass in this repository. `from app.config import settings`, `import { Wordmark } from './Wordmark'`. Two or three is plenty; give the ones that show the shape. Copied, never composed: the value here is that these lines are known to work in this repository, and one you wrote from memory of how such things usually look is worth less than nothing. A blind test author can derive the module path from the feature it was given, and cannot derive whether this project exports a component as a default or by name -- so it guesses, and a guess costs every criterion the file covers. That is not hypothetical: `import TrialDetail from ...` against a repository whose every page is `export function TrialDetail` collected no tests at all, and six criteria reached a human as failing.")
    cleanup: str | None = Field(default=None, description="How a test at this level undoes what it did, in this repository's own words, read off the tests that already exist here -- not a framework's documentation: 'each test gets a fresh schema from the `client` fixture', 'Django's TestCase rolls each test back in a transaction', 'fixtures delete what they created in teardown', 'each spec creates its own workspace and nothing reads it afterwards'. Whatever mechanism this project uses is the right one; say which it is. An empty string means tests here do not clean up after themselves -- or you could not find out, and `note` says which. A test that leaves data behind breaks the tests that run after it, and every one of those failures reads as the feature being broken.")
    cleanup_by: Literal["harness", "isolated", "each_test", "nobody"] | None = Field(default=None, description="WHO undoes what a test did at this level -- not how. `harness`: something resets it for every test whether the test remembers or not (a fixture that recreates the schema, a transaction rolled back, a truncate before each test). `isolated`: nothing resets it and nothing needs to -- every test that writes does so only in data it made for itself (its own workspace, tenant, account, temp directory, unique names), so no test can see another's changes. `each_test`: nothing resets it; the convention is that every test undoes its own changes, and a test that forgets leaves its data for the next. `nobody`: nothing resets it and tests do not undo anything either. Null only when `cleanup` is null. `each_test` and `nobody` need `cleanup_options`; `isolated` needs them only when a new test cannot reach the way the existing tests isolate themselves.")
    cleanup_options: list[CleanupOption] = Field(default_factory=list, description="Required when `cleanup_by` is `each_test` or `nobody`, and when it is `isolated` but the means of isolating is written inside one test file where a new test cannot import it: the ways to fix it that you are proposing in THIS answer, one per option, each complete on its own. A person picks one of these -- or leaves it -- and the factory applies it whole. Empty when `cleanup_by` is `harness`, and when it is `isolated` through something a new test can already import.")
    cleanup_summary: str = Field(default="", description="The same answer as `cleanup`, for a person deciding whether to accept this reading -- ONE plain sentence they can read in a breath, under 20 words. Say what a test here can rely on and what one test can leave behind for the next, in everyday words -- what leaks, never how the mechanism works: 'Every test starts with an empty database, so nothing carries over.' 'All tests share one live database and nothing resets it -- each test must undo its own changes.' No file, fixture, function or tool names, no code, no backticks; those belong in `cleanup`. Empty only when `cleanup` is null.")
    cleanup_examples: list[str] = Field(default_factory=list, description="The lines that do it, copied verbatim out of test or setup files that already exist and pass here -- a fixture's teardown, a rollback, a delete in an afterEach, a truncate. Two or three, never composed or paraphrased: they will be followed exactly.")
    run_by: list[str] = Field(default_factory=list, description="The names of the checks that run this project's OWN tests at this level -- `api-tests`, `web-e2e`. Empty when no check runs them, which is worth knowing: tests nobody runs protect nothing, and a level can be ready for a new test while its existing ones go unrun.")
    verdict: Literal["usable", "inline_only", "absent"] = Field(description="usable: a new test can be written against setup that already exists. inline_only: tests exist at this level but each builds its own world, so there is nothing to reuse. absent: no runner for this level at all.")
    note: str = Field(default="", description="For anything but `usable`: what is missing, concretely enough to be built. This is what a human reads when deciding whether to close the gap.")
    canary: str = Field(default="", description="Required when the verdict is `usable`, ignored otherwise. A complete, tiny test file that ACTUALLY USES the fixtures you listed above -- imports them by the names you gave, takes them as arguments, calls them -- and passes. One assertion is plenty; it is not testing the application, it is testing your claim. Everything else on this page is measured and this was the last thing that was merely asserted: a tier that says `usable` and cannot run a two-line test against its own named setup costs a run every criterion at that level, and nothing catches it until the packet. Write it in the project's idiom and import nothing from the repository except the setup you are pointing at.")
    canary_filename: str = Field(default="", description="What that file must be called for the runner to collect it: `test_tier_canary.py`, `TierCanary.test.tsx`. Its extension is how the harness decides which proved directory to put it in, so give it the same extension the tests at this level have.")


class DisposableDatabase(BaseModel):
    """An empty test db the blind suite may migrate, seed and throw away.

    Two acceptance criteria have gone unverified in every run of a real project
    for want of this, and the oracle has asked for it by name every time:

        "An isolated disposable database that may be migrated from the previous
         Alembic revision to head and downgraded."

    Those criteria are about the migration itself -- that `upgrade()` adds the
    columns, that `downgrade()` drops them, that rows written before the change
    are NULL afterwards. None of it can be observed on the database the suite
    normally uses, because that one is already at head and is shared with a
    running server. Reading the migration file instead is what the packet had to
    fall back on, and reading is not running.

    Empty rather than at a particular revision, deliberately. Which revision a
    test wants is a fact about the feature -- `down_revision` of a migration
    written this run -- and nothing here can know it. What the harness can offer
    is a database nobody else is using and a way to move it; the suite decides
    where to move it to.
    """

    env: str = Field(description="The environment variable the suite reads to find it, exported into every command in the test session: `FACTORY_DISPOSABLE_DATABASE_URL`.")
    url: str = Field(description="What that variable is set to. The project's own credentials against a database name nothing else uses.")
    prepare: list[str] = Field(default_factory=list, description="Commands that leave it existing and empty, run once before the suite. Dropping and recreating is the usual shape: a database left behind by a killed run is the one thing that makes this unreliable.")
    migrate: str = Field(default="", description="How to move it to a revision, with `{revision}` where the target goes: `cd backend && DATABASE_URL=$FACTORY_DISPOSABLE_DATABASE_URL alembic upgrade {revision}`. Given to the test author as prose; nothing here runs it, because only a test knows which revision it wants.")
    teardown: list[str] = Field(default_factory=list, description="Commands that remove it afterwards. Best effort -- a failure here is logged and never fails a check.")
    note: str = Field(default="", description="Anything else a test author has to know: which role the URL authenticates as, what the migration tool is called, whether seeding it is possible.")


class TestingSurface(BaseModel):
    """What this project lets an outside agent verify, by level."""

    tiers: list[TestingTier] = Field(default_factory=list, description="One entry per level. Include all three even when a level is absent -- an absent tier is the most important thing on this list, because every criterion needing it will come back unverified.")
    disposable_db: DisposableDatabase | None = Field(default=None, description="An empty test db the blind suite may migrate and throw away, when this project can offer one. It is what makes a criterion about a migration testable at all -- `upgrade()`, `downgrade()`, and what happens to rows that existed before. Null where the project has no migrations or no way to stand a second database up.")

    def tier(self, name: str) -> TestingTier | None:
        for entry in self.tiers:
            if entry.tier == name:
                return entry
        return None


class TierResult(BaseModel):
    """Whether a tier's `usable` was true, measured rather than believed.

    `usable` decides what the oracle is told it may write against, and it was
    the one thing on repo ready that nothing checked. A tier claiming reusable setup
    that a test cannot actually use costs the run every criterion at that level,
    reported as though the feature had failed -- which is the same shape as the
    placement bug, one layer up.
    """

    tier: str = Field(default="")
    claimed: str = Field(default="", description="The verdict the reading gave this tier.")
    path: str = Field(default="", description="Where the canary was written, if it was.")
    command: str = Field(default="", description="What ran it.")
    ran: bool | None = Field(default=None, description="The command started. None means the measurement could not be taken, which is never proof.")
    passed: bool | None = Field(default=None, description="The canary used the named fixtures and passed.")
    proved: bool = Field(default=False, description="This tier claimed `usable` and a test written against its own named setup ran and passed. Only then is the claim worth anything.")
    repeatable: bool | None = Field(default=None, description="The canary passed a second time, run straight after the first with nothing reset between. False means a test written the way this tier says leaves something behind that breaks its own next run. None: not measured.")
    leaves_clean: bool | None = Field(default=None, description="After the canary had run twice, this project's own checks at this level still passed. False means a test written the way this tier says breaks tests the project already has. None: not measured -- a unit tier shares no state, or no check that was green ran this level's tests.")
    note: str = Field(default="")
    evidence: str = Field(default="")


class Service(BaseModel):
    """Something that must be running while the tests run.

    Declared, never scripted. A project whose tests need a live server used to
    get one by having the check command start it -- `nohup uvicorn ... & UPID=$!`,
    poll `/health` in a shell loop, `kill $UPID` at the end, exit code shuffled
    around it. Ten concerns in one string, written blind by a model that cannot
    run it, and repeated verbatim in every command that needed a server. Three
    copies of that dance existed and the fourth, written for the per-file
    command, omitted the `kill`.

    So the model says what to run and how to know it is up, and the harness owns
    everything else: starting it, waiting for it, and taking it down whether the
    tests passed, failed or hung. Process management belongs in code that can be
    tested, not in a string.
    """

    name: str = Field(description="Short identifier, for the log line when it does not come up: api, db, emulator.")
    command: str = Field(description="The command that starts it, in the foreground, exactly as you would run it by hand. Do NOT background it, do not redirect it, do not write a pid file and do not kill anything -- the harness does all of that. If it listens on a port, take the number from `$FACTORY_PORT_<NAME>`, upper-cased from this service's `name`: `uvicorn app.main:app --host 127.0.0.1 --port $FACTORY_PORT_API`, never a literal like 8000. Bind to `127.0.0.1` rather than to `localhost`: in a container `localhost` resolves to `::1` first, so a server bound to it listens on IPv6 only and everything connecting to `127.0.0.1` is refused. Then use that same spelling in every browser-facing value you set here -- base URLs, allowed origins, an app's own base URL -- because to a browser `localhost` and `127.0.0.1` are two different sites and to a cookie two different owners.")
    ready_when: str = Field(default="", description="A command that exits zero once this service is ready to be used, and non-zero until then: `curl -sf http://127.0.0.1:$FACTORY_PORT_API/health`, `pg_isready -h 127.0.0.1 -p $FACTORY_PORT_DB`. Use the same `$FACTORY_PORT_<NAME>` the command binds -- a probe on a different port can go green against a completely different application. Polled until it succeeds or the timeout runs out. Leave empty only if the service is usable the instant it starts, which is rarer than it looks.")
    ready_timeout_s: float = Field(default=60.0, description="How long to keep polling before calling it dead.")


class Preview(BaseModel):
    """How a person runs this app to look at it, and what they open.

    The checks start what the tests need; a person needs the app. The two are
    often the same -- an API server the tests drive over HTTP -- and often not:
    a project whose browser level is not tested declares no front end at all,
    and it is exactly those projects whose criteria a person checks by hand.
    So this names what to open, and anything else a person needs running.

    Started in the same session the checks get: the same image, setup,
    services, `test_prepare` and disposable database, on the same sealed
    network. Fabrika forwards every service's port to this machine's loopback
    under the same number, so a service's own `FACTORY_URL_<NAME>` is the
    address the browser opens.
    """

    model_config = ConfigDict(extra="allow")

    open: str = Field(default="", description="The `name` of the service a person opens in their browser: the front end if there is one, otherwise the application server. One of `environment.services` or of `services` below. Empty when this project has nothing a browser can open -- a library, a CLI -- and then say so in `note`.")
    path: str = Field(default="/", description="Where in that service to start, as a path: `/`, `/app`, `/login`. The page a person lands on, and the page Fabrika requests to confirm the preview works.")
    services: list[Service] = Field(default_factory=list, description="What a person needs running that the tests did not, written exactly like `environment.services`: a front-end dev server (`npx vite --host 127.0.0.1 --port $FACTORY_PORT_WEB --strictPort`), a static file server for a built bundle. Started after the environment's own services, so they can use their `FACTORY_URL_*`. Never repeat a service already declared there.")
    note: str = Field(default="", description="Anything a person should know before opening it, in one plain sentence: what data it starts with, what will not work because nothing outside is reachable. Or why there is nothing to open.")


class PreviewProbe(BaseModel):
    """Whether a preview opened on the baseline, measured at repo ready."""

    model_config = ConfigDict(extra="allow")

    ok: bool = False
    status: int = Field(default=0, description="The HTTP status the page answered with, or 0 when nothing answered.")
    url: str = Field(default="", description="The path requested, on the service named by `preview.open`.")
    problem: str = Field(default="", description="Why it did not open, in plain words, when it did not.")
    seconds: float = Field(default=0.0, description="How long the services took to come up and answer.")
    sha: str = Field(default="", description="The commit it was measured on.")
    at: str = ""


class EnvironmentSpec(BaseModel):
    """How a project's checks get an environment to run in.

    Compose before anything, when the project has one. A generated image is a
    reconstruction of the project's environment and will drift from it -- and it
    cannot supply the databases and queues a test suite needs, because checks run
    as a single container with no companions.

    Otherwise derive before generate. Most project Dockerfiles are production
    images: they install runtime dependencies and carry no test runner, so
    pointing checks at one produces failures that look like code failures.
    Deriving from what exists is nearly always safer than inventing something
    new.
    """
    # Persisted state, so it has to survive being read by code older than the
    # record it is reading. Pydantic drops unknown keys by default, and this is
    # long-lived state carried through a long-lived process: a console server
    # started before a field existed reads a record that has it, drops it, and
    # writes the state back without it. The field is not emptied, it is gone.
    #
    # Fields added while a server is up -- `services`, `test_prepare`,
    # `test_file_commands` -- vanish from the stored record entirely when it
    # round-trips the project through its own older shape, so the next baseline
    # runs with no application server and its test gate fails on a refused
    # connection -- a fact about a stale process, wearing the clothes of a fact
    # about the repository.
    #
    # `extra="allow"` keeps what this code does not understand and writes it
    # back untouched. It does not make an old process act on a new field, which
    # it cannot; it stops one destroying the field on its way past, so restarting
    # is the whole of the recovery rather than re-running a paid survey.
    model_config = ConfigDict(extra="allow")


    kind: EnvironmentKind = Field(description="host: no container. reuse: an existing dev image runs the checks as-is. derive: FROM an existing image, adding the check toolchain. generate: authored from the detected stack because nothing usable exists.")
    source: str = Field(default="", description="What this was built from: a devcontainer path, a Dockerfile path and target, or empty when generated.")
    dockerfile: str = Field(default="", description="The complete TEXT of a Dockerfile, starting with a FROM line, written out in full here. Never a filename or a path -- there is no file to point at, and a value like 'Dockerfile.gates' is handed to `docker build` as the file body and fails on line 1. Used by derive and generate, and by compose when the check service needs a toolchain its own image lacks. Must NOT copy the source tree: the sandbox worktree arrives as a mount at run time and would shadow it.")
    dockerfile_path: str = Field(default="", description="A Dockerfile that is already in this repository, relative to its root, to build the check image from instead of writing one: `api/Dockerfile`. Use it only when a stage of that file can run every check you propose and does not copy the source in -- then name the stage in `dockerfile_target`, leave `dockerfile` empty, and say in `rationale` which stage and why it is enough. The project keeping one image for its checks, used by its CI and its developers and by this, is better than a second definition kept beside theirs.")
    dockerfile_target: str = Field(default="", description="With `dockerfile_path`: the stage to build (`FROM ... AS <this>`). Empty builds the file's last stage, which in most repositories is the production image and cannot run the checks.")
    build_context: str = Field(default="", description="With `dockerfile_path`: the directory the file is built from, relative to the repository root -- what its `COPY` lines are relative to. Usually the Dockerfile's own directory, or what the compose file's `build.context` says. Empty means the Dockerfile's own directory.")
    image: str = Field(default="", description="The resolved image tag the checks run in. Set once the image has actually been built.")
    compose_file: str = Field(default="", description="For kind 'compose': the compose file, relative to the repo, whose services the checks run against. Prefer this whenever the project defines one -- it is the environment the project itself maintains, so it cannot drift from what the app really runs, and it brings the databases and queues the tests need.")
    compose_service: str = Field(default="", description="Which service in that file the check commands run inside: the one carrying the project's toolchain. Usually the backend, or an explicit test service if the project has one.")
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables set in the container the checks run in, for settings the app cannot start without and that are not in the repository -- the ones a developer keeps in an untracked `.env`. Harmless test values only: a mock provider, a placeholder secret, a local database address. Never a real credential, and never something the repository's own `.env.example` already says. For a compose environment, a variable the project's compose file sets itself is left as it is.")
    setup: list[str] = Field(default_factory=list, description="Commands run with the network available, to reconcile dependencies with the lockfile. Once per sandbox where the runner keeps what they install, and once per assessment round where it does not.")
    services: list[Service] = Field(default_factory=list, description="Things that must be RUNNING while the tests run: an application server the tests drive over HTTP, an emulator, a stub. The harness starts each one, waits for its `ready_when`, and takes it down afterwards. Most projects need none -- a suite that runs in-process, or against a database this environment already brings up, has nothing to declare here.")
    test_prepare: list[str] = Field(default_factory=list, description="Commands run ONCE after the services are up and before any test runs: apply migrations, seed fixtures, build a binary the tests invoke. Not per test file -- these are session work, and putting them in the per-file command runs them once per file. Leave empty unless the tests genuinely need state prepared for them.")
    workdir: str = Field(default="/work", description="Where the sandbox worktree is mounted inside the container.")
    version_commands: list[str] = Field(default_factory=list, description="Commands that print the version of each toolchain the checks use, run in this environment after setup: `python --version`, `node --version`. What they print is shown beside what the project's own runs pin, and is what a red check is diagnosed against. Only toolchains a check uses.")
    version_files: list[str] = Field(default_factory=list, description="Repo-relative files that say which versions the project's own runs use: its CI workflow, `.python-version`, `.nvmrc`, `.tool-versions`, `requires-python` in a manifest named as `file#section`. Only files that exist.")
    preview: Preview | None = Field(default=None, description="How a person runs this app to look at it at review, and what they open. Proposed with the environment and measured on the baseline. None when not yet said.")
    rationale: str = Field(default="", description="Why this resolution and not another. The human reads this before approving.")


class ScaffoldFile(BaseModel):
    """A file the project needs before its checks can run.

    A runner and its configuration only. Never a test: a test written by the
    factory is a test the factory grades itself against, and a suite of one
    trivial assertion turns a red baseline green while proving nothing. If the
    check stays red because there is nothing to run, that is the true answer and
    the packet should inherit it.
    """

    path: str = Field(description="Repo-relative path. Never absolute, never containing '..'.")
    contents: str = Field(description="The complete file.")
    summary: str = Field(default="", description="What this file does, for the person deciding whether it goes into their repository -- ONE plain sentence under 20 words, in everyday words: 'Puts the demo data back the way the app seeded it, so each test run starts from the same board.' No category labels, no 'the user level', no file, fixture or function names -- those belong in `purpose`.")
    levels: list[Literal["unit", "integration", "user"]] = Field(default_factory=list, description="For a test helper: the test levels whose tests would use it. The project page shows it under those levels. Empty for a file that is not a test helper.")
    purpose: str = Field(description="What this file is for, and why the project cannot do it today. Open by saying which of the two kinds it is, because they are read differently: `a check cannot run without it` -- name the check -- or `a test at a level cannot be written without it` -- name the level and what such a test would reach. The second kind adds nothing to a green check list and reads as noise unless it says so: a helper that lets a component's error branch be tested at the unit level was offered to a project whose checks were all green, under the sentence \"makes this project easier to write tests against\", and meant nothing to the person reading it.")


RecommendationKind = Literal["tests", "lint", "types", "security", "ci", "build", "other"]


class Recommendation(BaseModel):
    """Something this project should do and does not. Advice, never a check.

    The distinction is load-bearing. A check is a contract: it comes from what
    the repository actually runs, and adopting one the repo has never satisfied
    is how a project ends up permanently red on a standard nobody agreed to. A
    recommendation is the honest place for "you should have a linter" -- it
    reaches the human, it does not block anything, and it becomes a check only
    when they decide it should.
    """

    title: str = Field(description="What to adopt, in one line. Name the tool.")
    kind: RecommendationKind = Field(description="What it would cover.")
    why: str = Field(description="Why this repository specifically. Not why the tool is good in general. Handed verbatim to an engineer or agent working in this repository who has never heard of this tool, so name nothing from inside it -- no survey, no check list, no phase, and no environment variable this tool sets. Say the capability itself.")
    evidence: str = Field(description="The code that makes the case: a path, a symbol, a pattern you actually found. A recommendation without evidence is a template, and templates get skimmed past. A fabricated one is worse than none.")
    how: str = Field(default="", description="The smallest first step. A config file, a dependency, one command. Read by someone working in the repository alone: commands they can run there, and nothing about what this tool does with the result.")
    levels: list[Literal["unit", "integration", "user"]] = Field(default_factory=list, description="For a suggestion about testing: the test levels it would help -- `user` for a browser test runner, `integration` for a way for API tests to clean up. The project page shows it under those levels. Empty for anything not about testing.")
    would_gate: str = Field(default="", description="The check command this would make possible, once the repo can satisfy it. Not proposed as a check now. The command alone -- adopting it is a decision taken here, by a human, and is not the business of whoever does the work in the repository.")
    # What adopting it makes. Everything a check needs to be complete the
    # moment a person says yes, because a check adopted half-described -- no
    # family, no pattern to read its count, nothing saying where its settings
    # live -- is one that cannot be ratcheted, grouped or guarded until a
    # second reading fills it in.
    family: CheckFamily = Field(default="", validate_default=True, description="The family of the check this would make: `structure`, `quality` or `tests`, by the same rule as a check's own `family`. Empty for a suggestion that would make no check -- a CI change, a build step.")
    install: str = Field(default="", description="The one command that installs the tool in this project's check environment, pinned: `pip install radon==6.0.1`, `npm install --no-save eslint-plugin-jsx-a11y@6.10.0`. Run by setup before every check, not committed to the repository. Empty when the repository already installs it.")
    parse_metric: str = Field(default="", description="For a check that reports a count or a score, the regex whose first group reads it, written against what the tool prints into a pipe -- the same rules as a check's own `parse_metric`. It is what lets a person hold the check at today's reading instead of fixing everything before it can be adopted.")
    config_files: list[str] = Field(default_factory=list, description="Where the check's settings would live, by the same rule as a check's own `config_files`.")
    suppressions: list[str] = Field(default_factory=list, description="The markers that make the tool skip a line, by the same rule as a check's own `suppressions`.")
    report_format: ReportFormat = Field(default="", description="The report `would_gate` writes to `{report}`, by the same rule as a check's own `report_format`. A suggestion with a report format and neither `{report}` in `would_gate` nor a `report_path` cannot be adopted.")
    report_path: str = Field(default="", description="Only for a tool that cannot be told where to write: the repo-relative file it always writes, by the same rule as a check's own `report_path`.")
    files_command: str = Field(default="", description="For a coverage or mutation tool: the same tool run on some files only, with `{paths}` and `{report}`, by the same rule as a check's own `files_command`. Without it a worker cannot be measured on what it wrote.")

    @field_validator("family", mode="before")
    @classmethod
    def _family_from_kind(cls, value: Any, info: Any) -> Any:
        """A reading that names the kind and not the family has said which
        family it meant: the closed set of kinds already sorts that way."""
        if value:
            return value
        return KIND_FAMILY.get((info.data or {}).get("kind", ""), "")


#: The family a suggestion's kind implies. `ci`, `build` and `other` imply none:
#: they are about how a project is run, not about what judges its code.
KIND_FAMILY = {"lint": "structure", "types": "structure", "security": "quality", "tests": "tests"}


class DependencyPolicy(BaseModel):
    """What this project will take on as a dependency. A person's, at gate 0.

    The license lists are a legal choice and differ by project, so these are
    proposals a person changes, not rules. Patterns are SPDX identifiers with
    `*` for any version: `GPL-*` denies GPL-2.0 and GPL-3.0-only and does not
    touch LGPL-2.1. An unknown license is always reported, never let through.
    """

    enabled: bool = True
    deny: list[str] = Field(default_factory=lambda: ["GPL-*", "GPL", "AGPL-*", "SSPL-*"])
    flag: list[str] = Field(default_factory=lambda: ["LGPL-*", "MPL-*"])
    min_age_days: int = 7


class DependencyFact(BaseModel):
    """One package a tree pins, or a feature changed, and what is known about it.

    The worker's own account plays no part: the package comes from the
    lockfile, the rest from OSV and the package's registry, read by code.
    """

    ecosystem: str
    name: str
    kind: str = ""
    before: list[str] = Field(default_factory=list)
    after: list[str] = Field(default_factory=list)
    direct: bool = False
    via: str = ""
    lockfile: str = ""
    private: bool = False
    checked: bool = False
    license: str = ""
    published: str = ""
    advisories: list[str] = Field(default_factory=list)
    malicious: list[str] = Field(default_factory=list)
    #: What the policy made of it: denied, flagged, unknown-license, too-new.
    verdicts: list[str] = Field(default_factory=list)
    added_by: str = ""


class FamilyRuling(BaseModel):
    """A family of checks this project has decided to go without.

    Families are optional. A project with no quality check is allowed; what is
    not allowed is the same suggestion arriving at every reading after a person
    has said no to the whole idea. Kept with the reason, which is shown where
    the family would be.
    """

    family: Literal["structure", "quality", "tests"]
    reason: str = ""
    at: str = ""


GuideLayer = Literal["agent", "design", "skill", "linked"]


class Guide(BaseModel):
    """A file in a standard place that says how this repository's code is written.

    Read from the repository at the commit in question, never stored: what
    binds is what is committed, so every tool that writes code here follows
    the same files. `agent` is an AGENTS.md or CLAUDE.md, `design` the root
    DESIGN.md, `skill` a SKILL.md in a skills folder, `linked` a document an
    AGENTS.md points to.
    """

    path: str
    layer: GuideLayer
    scope: str = Field(default="", description="The folder a nested AGENTS.md covers; empty for the whole repository.")
    sha256: str = Field(default="", description="Of its contents at the commit it was read at.")
    description: str = Field(default="", description="For a skill: when it applies, from its own front matter.")
    referenced_by: str = Field(default="", description="For a linked document: the AGENTS.md that points to it.")


class DraftRule(BaseModel):
    """One observed convention, with what code counted about it."""

    rule: str
    slug: str
    features: int = Field(description="How many features' scouts observed it, counted by code.")
    files: list[str] = Field(default_factory=list, description="Files it holds in that exist at the base branch.")


class GuideDraft(BaseModel):
    """AGENTS.md sections Fabrika proposes from conventions seen across features.

    Never written from nothing: every rule is one several features observed,
    with the files it holds in. Committed to the repository only when a person
    edits and approves it.
    """

    id: str
    mode: Literal["new", "append"] = "new"
    path: str = Field(description="Where it would go: AGENTS.md, new or added to.")
    markdown: str
    rules: list[DraftRule] = Field(default_factory=list)
    features: int = 0


class GuideDraftAnswer(BaseModel):
    """The text of a draft guide, from rules code has already counted."""

    markdown: str = Field(description="AGENTS.md's `## Code style` and `## Testing` sections, in Markdown -- either may be left out if no rule belongs in it. Each rule a sentence a person would write, followed by one example path. Only the rules you were given; no rule of your own.")


class ProjectSurvey(BaseModel):
    """What the surveyor reports about a directory it was pointed at."""

    name: str = Field(description="A short human name for this project.")
    summary: str = Field(description="What this repository is, in two or three sentences.")
    stack: list[str] = Field(default_factory=list, description="Languages, frameworks and tooling actually in use, evidenced by files.")
    base_ref: str = Field(default="HEAD", description="The ref features should branch from. Usually the default branch.")
    gates: list[Gate] = Field(default_factory=list, description="Commands that establish whether this project is healthy: types, lint, tests, and anything else it already runs in CI. Take them from what the repo actually uses, not from what it should use.")
    testing: TestingSurface = Field(default_factory=TestingSurface, description="What this project lets an outside agent verify, by level. Read from the repository like everything else here: which runners exist, and whether a NEW test at each level has reusable setup to write against.")
    test_file_commands: list[TestFileCommand] = Field(default_factory=list, description="How to run a single test file, as an ordered list of rules -- the first whose `match` glob fits the path wins. This is the only per-criterion attribution the system has: the blind acceptance tests are run one file at a time and each file's exit code is its verdict, because nothing downstream reads a runner's output or knows what a runner is called. One rule is the normal answer; give more only when this repository genuinely runs more than one test runner. Leave empty only if a single file cannot be run at all, and say so in `concerns`: the suite then runs whole and every criterion rests on one exit code covering all of them.")
    trace_dirs: list[str] = Field(default_factory=list, description="Directories, relative to the repository root, where this project's test runners leave a recording of a whole run -- a frame-by-frame capture with the page's structure at each step, which a person can step through afterwards. Playwright writes one per test under its output directory when tracing is on. Empty when no runner here records, which is the normal answer. Name only a directory the runner actually writes to: a path taken from a framework's documentation rather than from this repository holds nothing, and a band of recordings that is always empty teaches a reader to stop opening it. Name the directory whether or not recording is switched on. A runner set to keep a recording only when a test fails counts as off: that answers why something broke, and the question a packet cannot otherwise answer is 'the test says this passes, show me', which a passing run leaves nothing for. In that case, and when it is off outright, put the switch in `recommendations` -- turning it on is a change to a config file this project owns, which is a human's to make.")
    blind_placements: list[BlindPlacement] = Field(default_factory=list, description="Where a test written by an agent that has never seen this repository must be put, one entry per runner this project uses. This is not a detail: where a test file sits decides which configuration reaches it, which shared setup loads, and whether the runner collects it at all. Every entry is proved at repo ready with the canaries you supply -- a passing one that must be reported as passing and a failing one that must be reported as failing -- so a wrong answer here costs a minute rather than a run.")
    environment: EnvironmentSpec = Field(description="How to give those checks something to run in. Prefer reuse, then derive, then generate.")
    concerns: list[str] = Field(default_factory=list, description="Anything that will make this project awkward to build in: no lockfile, tests needing a live service, no test suite at all.")
    recommendations: list[Recommendation] = Field(default_factory=list, description="Verification this project should have and does not. These are never checks: every check on the list must be green before this project can build anything, so proposing a tool the repo has never satisfied stops the project rather than improving it. Put the tool the repo already runs in `gates`, the file that would let a check run in `scaffolding`, and everything else here.")
    scaffolding: list[ScaffoldFile] = Field(default_factory=list, description="Files this project is missing that would let a check run at all -- a dev requirements file, a pytest.ini, a tsconfig for type-checking. Propose them only when the tool is genuinely absent. You MAY propose test *support* -- a conftest, a fixture module, a factory, a render helper, an end-to-end harness -- because none of it asserts anything, and a project whose tests each build their own world offers a blind test author nothing to write against. You may NEVER propose a file that asserts: a test you wrote is a test you would then be graded against. Setup is not a test; the line is whether the file makes a claim about behaviour.")
    notes: str = Field(default="", description="Anything the human should know before approving.")


GateAction = Literal["add", "remove", "change"]


class ProposedGateChange(BaseModel):
    """One change to the check list, proposed and not applied.

    A re-survey exists because repositories acquire tooling: a DSL arrives with
    its own linter and nothing checks it, while every existing check stays green
    because none of them was ever asked about it. But the same call that can add
    a check can drop one, and a re-survey that quietly removed a failing check
    would be the project-level version of a presenter filtering findings. So
    nothing here applies itself. Each entry is a proposal a human accepts or
    rejects, and the rejections stay on the ledger too.
    """

    action: GateAction = Field(description="add: this project verifies something no check covers. remove: this check is for a tool the project no longer uses. change: the command moved.")
    name: str = Field(description="The check this is about. For `add`, the name the new check would have.")
    reason: str = Field(description="Why, in terms of what changed in the repository. Not why the tool is good. The reader is shown the difference against what is recorded before they reach this, so do not describe the change -- say what goes wrong if it is not made, about this repository, in a sentence or two.")
    evidence: str = Field(description="The file that makes the case: the CI step, the manifest entry, the config that appeared. A proposal without evidence is a guess, and a human cannot check a guess.")
    gate: Gate | None = Field(default=None, description="The check definition, required for `add` and `change`, omitted for `remove`.")


class RecommendationRuling(BaseModel):
    """A suggestion this project has decided against.

    Keyed by the command it would create rather than by its title, because a
    title is prose and a reading rewrites prose every time it runs. `npm run
    typecheck` is derived from the repository's own scripts and survives being
    described differently; "Install TypeScript and @types/node so the typecheck
    script the project already declares can actually run" does not.

    Enough of the suggestion is kept to draw it in the declined list, because a
    suggestion is not stored on the project the way a check is -- filtering it
    out of every reading would otherwise make it unfindable.
    """

    key: str = Field(description="The command it would create, normalised; its title where it names no command.")
    title: str = Field(default="", description="As the reading wrote it, so the declined list is readable.")
    kind: str = Field(default="other", description="Which question it was under.")
    would_gate: str = Field(default="", description="The check it would have created.")
    report_format: str = Field(default="", description="What its check would have reported, so a turned-down coverage suggestion is known as one: coverage is then off by choice.")
    reason: str = Field(default="", description="Why not this project.")
    at: str = Field(default="", description="When.")


FixKind = Literal["repo_change", "environment_change", "command_change", "agent_prompt", "hold"]


class DiagnosisFix(BaseModel):
    """One way to make a red check honest, as the diagnosis proposes it."""

    kind: FixKind = Field(description="`repo_change`: a file in the repository, written whole -- a setting, a config line. `environment_change`: setup steps added to where the checks run -- a package, a tool. `command_change`: a new command for the check. `agent_prompt`: real code must change; the text a person gives their own coding agent. `hold`: keep the check at today's reading -- only when the code really fails and the debt is the project's, never under the project's own floor and never for a tests check.")
    title: str = Field(description="What it does, in one plain line a person can say yes to: `Add concurrency = [\"greenlet\", \"thread\"] to api/pyproject.toml`.")
    path: str = Field(default="", description="For `repo_change`: the repo-relative file.")
    contents: str = Field(default="", description="For `repo_change`: the COMPLETE new contents of that file -- every line, unchanged ones included. Never a fragment or a diff.")
    commit_message: str = Field(default="", description="For `repo_change`: the commit's first line, then a blank line and why, in the project's own words.")
    setup: list[str] = Field(default_factory=list, description="For `environment_change`: commands added to the environment's setup, run with the network.")
    command: str = Field(default="", description="For `command_change`: the check's whole new command.")
    prompt: str = Field(default="", description="For `agent_prompt`: what to give a coding agent working in this repository, naming files and the failure, and nothing about this tool.")


class DiagnosisAnswer(BaseModel):
    """Why one check is red on untouched code, and what would fix it."""

    need_files: list[str] = Field(default_factory=list, description="Repository files you must read before you can answer, by repo-relative path. Only in your first answer, and only when the answer depends on them; they are read and you are asked once more. Empty otherwise.")
    cause: str = Field(default="", description="Why it is red, in ONE sentence a person reads in a breath: under 25 words, their words not a tool's, and none of this factory's vocabulary. Empty only while asking for files.")
    where: Literal["measurement", "environment", "project", "flaky", "unknown"] = Field(default="unknown", description="Which side is wrong. `measurement`: this environment measures differently from the project's own runs. `environment`: something the check needs is missing or wrong where it runs here. `project`: the code or its tests really fail, anywhere. `flaky`: it does not fail every time. `unknown`: you cannot tell from what you were given.")
    evidence: list[str] = Field(default_factory=list, description="The lines that make the case, each quoted with where it is from: `output: FAIL Required test coverage of 95.0% not reached`, `api/pyproject.toml:52 fail_under = 95`, `version: Python 3.12.14`.")
    fixes: list[DiagnosisFix] = Field(default_factory=list, description="Up to three, best first. Each is tried before a person sees it, so propose what you believe would turn it green honestly -- never a fix that lowers what the project asks of itself, skips tests, or silences the check.")
    recommended: int = Field(default=0, description="The index in `fixes` of the one you would pick.")


class FixTry(BaseModel):
    """What happened when a proposed fix was tried in a throwaway checkout."""

    fix: int
    ran: bool = Field(description="Whether the try ran the check at all.")
    passed: bool = False
    exit_code: int | None = None
    metric: float | None = None
    note: str = Field(default="", description="Why it did not run, or the end of what the check said.")


class Diagnosis(BaseModel):
    """One diagnosis of one red check, as recorded."""

    check: str
    fingerprint: str = Field(description="What it read, as one value: reused while this is unchanged.")
    kind: RedKind = ""
    cause: str = ""
    where: str = "unknown"
    evidence: list[str] = Field(default_factory=list)
    fixes: list[DiagnosisFix] = Field(default_factory=list)
    recommended: int = 0
    tries: list[FixTry] = Field(default_factory=list)
    confirmed: bool = Field(default=False, description="The recommended fix was tried and the check passed.")
    failed: str = Field(default="", description="Why no diagnosis could be made, when none could.")
    cost: dict[str, Any] = Field(default_factory=dict)


class KeptCheck(BaseModel):
    """A change to a check a reading proposed, and a person kept the check instead.

    Keyed by what was proposed: the same command for the same check is not
    proposed again, and a different one is -- the repository moved, and that
    is a new question. A kept `remove` keeps the check against any reading.
    """

    action: Literal["change", "remove"]
    name: str
    command: str = ""
    reason: str = ""
    at: str = ""


class GateRuling(BaseModel):
    """A check this project has decided against, and why.

    A rejection written to the ledger and read by nothing does not hold. A
    check declined at gate 0 comes back at the next reading -- the evidence
    behind it is still in the repository and always will be, so the proposal
    always will be too -- and a full survey is worse than a re-survey, because
    it replaces the gate list wholesale rather than diffing it and walks past
    the decision entirely.

    The command is kept so the check can be put back exactly as it was. The
    reason is kept because the console promises that declining a check
    "leaves the reason readable".

    Deliberately never expires and never resurfaces on its own. Unlike a
    capability request, which a new feature can raise again with new evidence,
    a declined check is a standing decision about this project. Only a human
    puts it back.
    """

    name: str = Field(description="The check's name, which is what a later proposal is matched against.")
    command: str = Field(default="", description="What it ran, so reinstating restores it rather than re-deriving it.")
    reason: str = Field(default="", description="Why this project does not want it. Readable later, when somebody asks why there is no type check.")
    at: str = Field(default="", description="When.")


class CapabilityRequest(BaseModel):
    """One thing the verify lane asked this project for, and who asked.

    Recorded on the diff rather than recomputed when a human rules, so a request
    raised by a feature that finished *between* the reading and the ruling is not
    silently marked as ruled on. The human ruled on what they were shown.
    """

    need: str = Field(description="The capability, as the blind author wrote it.")
    features: list[str] = Field(default_factory=list, description="The features that raised it.")


class CapabilityRuling(BaseModel):
    """That a human has already answered a request, and from which features.

    The verify lane's requests live in append-only feature ledgers, so nothing
    could ever take one off the list: a project answered `a runner that can
    drive a browser` by putting Playwright in the repository and was told about
    the same request at the next reading, and the one after, forever. Every
    reading then felt obliged to propose something about it.

    `features` is the point. This says the request has been answered *for the
    features that raised it so far* -- so a new feature hitting the same wall
    raises it again, which is exactly the signal that the answer did not work.
    """

    need: str = Field(description="The capability, as the blind author wrote it.")
    features: list[str] = Field(default_factory=list, description="The features whose requests this ruling covers.")
    decision: str = Field(default="ruled", description="What the human did with the reading that carried it: `applied` or `rejected`.")
    at: str = Field(default="", description="When.")


class SurveyDiff(BaseModel):
    """What a second reading of a repository would change about its checks.

    Deliberately a diff and not a fresh survey. A fresh one re-proposes every
    check, and a human then has to re-adjudicate a list they already approved to
    find the one line that moved -- which is how a real change gets waved
    through with the noise around it.
    """

    summary: str = Field(description="What changed in this repository and what follows from it. Two or three sentences at most. Every change you propose is rendered directly below this as its own card carrying its own reasoning, so a summary that walks through them says everything twice and buries the one thing it is for: whether this reading found anything worth a human's attention at all. Say that, and stop. Name the thing the change is about -- `one change below, about what a test at the unit level can use` -- never the argument for it, which is on the card.")
    gate_changes: list[ProposedGateChange] = Field(default_factory=list, description="Every change you propose. Empty is a fine answer and the common one.")
    testing: TestingSurface | None = Field(default=None, description="A replacement reading of what this project lets an outside agent verify, by level. Propose one when the project has none recorded, or when what a new test can be written against has actually changed -- a conftest appeared, a browser runner was added, fixtures were extracted out of a test file. Omit it when nothing about that moved.")
    testing_reason: str = Field(default="", description="Why it must change. Required if you propose one. Open with ONE plain sentence that a person can read in a breath: what this changes and what goes wrong without it, in their words rather than the tool's. That sentence is what the card shows; everything after it is one click down, and a reader who never opens it must still know what they are ruling on. Under 25 words, one clause where you can, and none of this factory's vocabulary in it -- not `reading`, `tier`, `blind test author` or `on record`, which name nothing a person outside this tool thinks about. Then say the rest: the file, the command, the evidence. The reader is shown the difference against what is recorded before they reach this, so do not spend the first sentence restating the change.")
    test_file_commands: list[TestFileCommand] = Field(default_factory=list, description="A replacement set of rules for running ONE test file, `{path}` substituted -- only if the project has none, or the ones it has no longer work. Omit otherwise. Without them no acceptance test can be attributed to the criterion it checks, and every criterion rests on one exit code covering the whole suite.")
    test_file_commands_reason: str = Field(default="", description="Why they must change. Required if you propose any. Open with ONE plain sentence that a person can read in a breath: what this changes and what goes wrong without it, in their words rather than the tool's. That sentence is what the card shows; everything after it is one click down, and a reader who never opens it must still know what they are ruling on. Under 25 words, one clause where you can, and none of this factory's vocabulary in it -- not `reading`, `tier`, `blind test author` or `on record`, which name nothing a person outside this tool thinks about. Then say the rest: the file, the command, the evidence. The reader is shown the difference against what is recorded before they reach this, so do not spend the first sentence restating the change.")
    trace_dirs: list[str] = Field(default_factory=list, description="A replacement set of directories where this project's test runners leave a recording of a whole run -- propose them when the project has none recorded and a runner here can record, or when the recorded ones are no longer where it writes. Omit when nothing about that moved. A recording is a frame-by-frame capture with the page's structure at each step, and it is worth proposing for a project that has none: a screenshot is one instant somebody chose, and choosing is a thing that can be got wrong. Name only a directory the runner actually writes to. Recording that happens only when a test fails counts as off -- it answers why something broke, not 'the test says this passes, show me' -- so propose the directory and put the switch in `recommendations` in that case as much as when it is off outright.")
    trace_dirs_reason: str = Field(default="", description="Why they must change. Required if you propose any. Open with ONE plain sentence a person can read in a breath: what this changes and what goes wrong without it, in their words rather than the tool's. Under 25 words, and none of this factory's vocabulary in it. Then say the rest: the directory, what switches recording on, the evidence you read.")
    blind_placements: list[BlindPlacement] = Field(default_factory=list, description="A replacement set of places where a blind test file must be written, one per runner -- only if the project has none recorded, a measured one no longer works, or the recorded ones carry no `canary_case` (propose them again with that field filled in and everything else identical, which is the one case where a replacement is not a disagreement). A project with none cannot have its acceptance criteria verified by anything the feature's own authors did not write, so proposing them for a project that has none is never noise.")
    blind_placements_reason: str = Field(default="", description="Why they must change. Required if you propose any. Open with ONE plain sentence that a person can read in a breath: what this changes and what goes wrong without it, in their words rather than the tool's. That sentence is what the card shows; everything after it is one click down, and a reader who never opens it must still know what they are ruling on. Under 25 words, one clause where you can, and none of this factory's vocabulary in it -- not `reading`, `tier`, `blind test author` or `on record`, which name nothing a person outside this tool thinks about. Then say the rest: the file, the command, the evidence. The reader is shown the difference against what is recorded before they reach this, so do not spend the first sentence restating the change.")
    environment: EnvironmentSpec | None = Field(default=None, description="A replacement environment, only if the checks cannot run in the current one. Omit it otherwise -- rebuilding an image nobody asked to change costs the human a baseline.")
    preview: Preview | None = Field(default=None, description="What a person opens at review -- only when the environment has no `preview`, or the measurement on record says it did not open and you can see why. Proposed on its own, without replacing the environment: accepting it changes nothing the checks run in. Omit it otherwise.")
    preview_reason: str = Field(default="", description="Why, if you propose one. Open with ONE plain sentence a person can read in a breath: what they will be able to open, or what stopped it opening. Then the evidence: the dev command and the file you read it from.")
    environment_reason: str = Field(default="", description="Why the environment must change. Required if you propose one. Open with ONE plain sentence that a person can read in a breath: what this changes and what goes wrong without it, in their words rather than the tool's. That sentence is what the card shows; everything after it is one click down, and a reader who never opens it must still know what they are ruling on. Under 25 words, one clause where you can, and none of this factory's vocabulary in it -- not `reading`, `tier`, `blind test author` or `on record`, which name nothing a person outside this tool thinks about. Then say the rest: the file, the command, the evidence. The reader is shown the difference against what is recorded before they reach this, so do not spend the first sentence restating the change.")
    scaffolding: list[ScaffoldFile] = Field(default_factory=list, description="Files this project needs and does not have, proposed the same way a first survey proposes them: support only, never a test. A re-survey could not offer these at all until it was asked to, which meant a capability the verify lane had been asking for by name every run -- a factory that makes a domain object, a runner that can drive a browser -- could only arrive by re-surveying the project from scratch and re-adjudicating every decision already approved.")
    scaffolding_reason: str = Field(default="", description="Open with ONE plain sentence a person can read in a breath: what these files let this project do that it cannot do today. That sentence is what the card shows, and everything after it is one click down. Under 25 words, and none of this factory's vocabulary in it. Then say which files, and which criteria they unlock. Required if you propose any.")
    recommendations: list[Recommendation] = Field(default_factory=list, description="Verification this project should have and still does not. Same rules as a survey: never a check, always evidenced.")
    #: Not written by the model. Filled in when the reading is taken, from the
    #: requests actually rendered into its prompt, so ruling on this diff can
    #: retire exactly those and nothing else.
    considered: list[CapabilityRequest] = Field(default_factory=list, description="The verify lane's outstanding requests this reading was shown. Recorded, not proposed.")
    unchanged: str = Field(default="", description="What you looked at and found still correct. This is what makes the proposals credible -- a re-survey that always finds something carries no information.")


#: Bumped whenever a field is added to `ProjectState` or anything it holds.
#:
#: The number itself means nothing; what matters is that it goes up. A process
#: reads it to find out whether the state in front of it was written by code
#: newer than itself, which is a question nothing could ask before and which
#: cost a full day of runs.
#:
#: The failure it exists to stop: a console server started on Friday held a
#: `ProjectState` that had never heard of `services`, `test_prepare` or
#: `test_file_commands`. Pydantic drops unknown keys, so every write it made
#: stripped them -- an approve, a re-survey, a restore -- and the next build ran
#: against a project with no migrations to apply. Its database had no schema and
#: no application role, every gate that touched it failed on `password
#: authentication failed`, and the packet reported a broken feature. Nothing in
#: it was about the feature.
#:
#: 1 -- before services, test_prepare and test_file_commands.
#: 2 -- those three, and this field.
#: 3 -- the testing surface.
#: 4 -- blind placements and what was measured about them.
#: 5 -- proved testing tiers, and when the baseline was measured.
#: 6 -- how a test imports the code it tests, and how to load one without
#:      running it.
#: 7 -- how to get a verdict per test rather than per file, and a database the
#:      suite may throw away.
#: 8 -- what a person opens at review, and whether it opened on the baseline.
#: 9 -- a check's own floor, the kind of red it is, and the toolchain versions
#:      the checks run with.
#: 10 -- the guides: the repository's AGENTS.md, DESIGN.md and skills, read
#:       from it and approved nowhere else; where this project keeps skills.
STATE_VERSION = 10


#: What each version began asking for, in the words a human needs.
#:
#: The stamp above was read in one direction only: it refuses a write from code
#: older than the record, so nothing half-understood is silently dropped. The
#: other direction had no answer at all, and the gap has a cost. A field added
#: here is empty on every existing project, no detector notices -- `tooling_drift`
#: watches the repository, and the repository did not move -- and the first sign
#: is a re-survey proposing a change to a project that has not changed. That
#: reads as drift and is not: it is this tool having grown a question it never
#: put.
#:
#: Read against a record's stored version, this says which questions were never
#: put to it. It cannot say whether an empty field is an unasked question or an
#: answered one, because a record is stamped when it is written and a re-survey
#: you decline still writes. So this is the cheap signal, true until the record
#: is next touched; `check_testing_conventions` is the durable one, and it reads
#: the field rather than the version.
STATE_ADDITIONS: dict[int, list[str]] = {
    6: [
        "`testing.tiers[].import_examples` -- the lines this repository's own tests "
        "use to import the code they test, which is the one thing a blind test "
        "author cannot derive and will otherwise guess",
        "`test_file_commands[].collect` -- the command that loads one test file "
        "without running it, which is what separates a test whose import is broken "
        "from a test whose assertion failed",
    ],
    7: [
        "`test_file_commands[].report` -- the command that runs one test file and "
        "writes a JUnit report, which is what makes a verdict per test rather than "
        "per file: one test disagreeing about one field once failed the fourteen "
        "criteria its file happened to carry, thirteen of which had tests that passed",
        "`testing.disposable_db` -- an empty database the blind suite may migrate and "
        "throw away, without which no criterion about a migration can be checked by "
        "running one, only by reading it",
    ],
    8: [
        "`environment.preview` -- what a person opens in their browser at review, and "
        "anything that must run for it that the tests do not need, without which a "
        "person rules on criteria nobody tested with no way to look at them. Propose it "
        "as `preview`, never as a new environment",
    ],
    9: [
        "`gates[].own_floor` -- the bound a check's own tool enforces from the project's "
        "settings, such as coverage's `fail_under`, without which a check red under the "
        "project's own floor is offered to be held below it. Propose it as a `change` to the "
        "check, with the command untouched",
        "`environment.version_commands` and `environment.version_files` -- what prints each "
        "toolchain's version where the checks run, and which files say what the project's own "
        "runs use, without which a check that is red here and green on the developer's machine "
        "cannot be explained. Propose them as an environment that changes nothing else: "
        "recording them rebuilds nothing and costs no run of the checks",
    ],
    10: [
        "`recommendations` for a rule in this repository's AGENTS.md or DESIGN.md that a tool "
        "can enforce -- a banned import, a colour used without a token -- which a reading "
        "before this was never shown the guides to find. Each with the rule quoted in "
        "`evidence`",
    ],
}


def reading_behind(stored_version: int) -> list[str]:
    """The questions never put to a record written at `stored_version`."""
    return [entry
            for version in sorted(STATE_ADDITIONS)
            if version > (stored_version or 1)
            for entry in STATE_ADDITIONS[version]]


class ProjectState(BaseModel):
    """A project is a repository plus how to test it plus what it needs to run.

    Created at runtime by pointing the factory at a directory, then approved by
    a human once its environment builds and its checks are green on an untouched
    checkout. A project whose baseline is red makes every packet built from it
    unattributable.
    """
    # Persisted state, so it has to survive being read by code older than the
    # record it is reading. Pydantic drops unknown keys by default, and this is
    # long-lived state carried through a long-lived process: a console server
    # started before a field existed reads a record that has it, drops it, and
    # writes the state back without it. The field is not emptied, it is gone.
    #
    # Fields added while a server is up -- `services`, `test_prepare`,
    # `test_file_commands` -- vanish from the stored record entirely when it
    # round-trips the project through its own older shape, so the next baseline
    # runs with no application server and its test gate fails on a refused
    # connection -- a fact about a stale process, wearing the clothes of a fact
    # about the repository.
    #
    # `extra="allow"` keeps what this code does not understand and writes it
    # back untouched. It does not make an old process act on a new field, which
    # it cannot; it stops one destroying the field on its way past, so restarting
    # is the whole of the recovery rather than re-running a paid survey.
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _drop_retired(cls, data: Any) -> Any:
        """A field this code removed on purpose is not kept as an unknown one.

        `guides`, in an older record, holds which documents a person approved,
        in Fabrika's own record. Guides are read from the repository and
        nothing here decides which bind, so that list is dropped rather than
        carried on as though it still meant something.
        """
        if isinstance(data, dict) and "guides" in data:
            data = {k: v for k, v in data.items() if k != "guides"}
        return data

    project_id: str
    name: str = ""
    repo: str = Field(default="", description="Absolute path to the base repository on this machine.")
    base_ref: str = "HEAD"
    stage: Literal["surveying", "awaiting_approval", "ready", "failed"] = "surveying"
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    environment: EnvironmentSpec | None = None
    gates: list[Gate] = Field(default_factory=list)
    #: When each check runs, where a human chose rather than the timing. Kept
    #: apart from `gates`, which is what the repository declares, so a
    #: re-reading that rewrites the check list does not wipe a person's choice.
    check_runs: dict[str, Literal["light", "heavy"]] = Field(default_factory=dict, description="A human's choice of when a check runs, by check name: `light` every round, `heavy` once in the final pass. A check with no entry is decided by how long it takes.")
    #: The surveyor's answer to "how do I run one test file", carried on the
    #: project because that is what it is a property of. See `SurveyReport`.
    testing: TestingSurface = Field(default_factory=TestingSurface, description="What this project lets an outside agent verify, by level. Proposed by the surveyor, approved by a human at repo ready.")
    test_file_commands: list[TestFileCommand] = Field(default_factory=list, description="Ordered rules for running one test file, first match wins. Proposed by the surveyor, approved by a human at repo ready.")
    blind_placements: list[BlindPlacement] = Field(default_factory=list, description="Where the blind suite's files go, one per runner. Proposed by the surveyor, approved by a human at repo ready, and proved by measurement in `placement_probe`.")
    preview_probe: PreviewProbe | None = Field(default=None, description="Whether the environment's preview opened on the baseline, measured at repo ready. None when there is no preview to measure.")
    testing_probe: list[TierResult] = Field(default_factory=list, description="Whether each tier claiming `usable` can actually be written against, measured at repo ready by running a test that uses the fixtures the reading named.")
    placement_probe: list[PlacementResult] = Field(default_factory=list, description="What was measured about each placement at repo ready, in the sandbox: whether a passing test there is reported as passing, and a failing one as failing. A placement with no usable result cannot carry blind tests.")
    survey: ProjectSurvey | None = None
    # What the survey was derived from, so staleness is a computed fact rather
    # than something a human has to remember. Without these the system cannot
    # know whether its gate list still describes this repository.
    survey_sha: str = Field(default="", description="The commit the survey read. Empty for surveys taken before this was recorded.")
    survey_paths: list[str] = Field(default_factory=list, description="Repo-relative paths the surveyor was shown, so a change to one of them is detectable.")
    scaffolding_applied: list[str] = Field(default_factory=list, description="Scaffolding files the human chose to write into the repository.")
    cleanup_declined: list[str] = Field(default_factory=list, description="Test levels whose cleanup fixes a person looked at and chose not to take: Fabrika does not check criteria there, by their decision. Said so the page stops asking; applying a fix later takes the level off this list.")
    cleanup_applied: dict[str, str] = Field(default_factory=dict, description="Per test level, the cleanup fix a person chose and Fabrika applied, by its title. A level whose reading says nothing cleans up is tested once a fix is in, without waiting for the next reading.")
    capability_rulings: list[CapabilityRuling] = Field(default_factory=list, description="Verify-lane requests a human has already answered, and the features they came from. Without this the same four requests are handed to every reading forever, because the ledgers they live in only grow.")
    gate_rulings: list[GateRuling] = Field(default_factory=list, description="Checks this project has declined, with the reason and the command. A reading may not propose one of these again, and a full survey may not reintroduce it: the evidence that suggests a check never leaves the repository, so without this the same proposal returns forever.")
    #: Where this project's runners leave recordings of a run. A property of
    #: the repository, so it sits here rather than in the factory's own
    #: configuration: that file is one setting for every project the factory
    #: manages, and `web/test-results` is a fact about one of them.
    trace_dirs: list[str] = Field(default_factory=list, description="Directories this project's test runners write run recordings into, read from the repository by the surveyor.")
    #: How the blind suite runs, and what it can reach, for this repository.
    #: They were settings of the factory once -- one value for every project --
    #: and the value was one project's: see `config.REPOSITORY_FACTS`.
    oracle_command: str = Field(default="python -m pytest {oracle_dir} -v", description="How the blind suite runs as a whole, for a project with no per-file rules. `{oracle_dir}` is substituted. Unused while `test_file_commands` has any rule.")
    oracle_collect_command: str = Field(default="python -m pytest {oracle_dir} --collect-only -q", description="Loads the blind suite without running it, for a project with no per-file rules, so a suite that will not import is reported against its author rather than the feature. Empty disables it.")
    oracle_runtime: str = Field(default="", description="What a blind test can reach here that no command says for itself -- how a request authenticates, how a tenant is chosen, how test data comes into being -- written by a person who has seen this repository. Told to this project's oracle as the whole truth about its environment, so write only what is true and stop.")
    recommendation_rulings: list[RecommendationRuling] = Field(default_factory=list, description="Suggestions this project has turned down. A reading regenerates its recommendations every time it runs, so without this the same three come back at every reading forever -- the repository facts that prompt them do not change.")
    skills_dir: Literal["", ".claude/skills", ".agents/skills"] = Field(default="", description="Where Fabrika writes a skill for this project, chosen once by a person: the folder its people already use. It decides nothing about what binds -- skills in either folder do. Empty until chosen.")
    guide_offers_declined: list[str] = Field(default_factory=list, description="Guides Fabrika offered to write and a person turned down: `design_md`, `agents_md`, `claude_md`, `design_line`, or a draft's id.")
    kept_checks: list[KeptCheck] = Field(default_factory=list, description="Changes to checks a reading proposed and a person turned down, keeping the check as it was. Not proposed again until what would be proposed differs.")
    family_rulings: list[FamilyRuling] = Field(default_factory=list, description="Families of checks this project goes without, with the reason. No suggestion in a declined family is shown, and a reading is told not to make one.")
    dependency_policy: DependencyPolicy = Field(default_factory=DependencyPolicy, description="What this project will take on as a dependency: licenses denied and flagged, and how new a release may be. What the project already depends on is a record in its ledger, `dependency_inventory`, appended at each baseline and at the start of each build.")
    baseline: GateReport | None = Field(default=None, description="Check results on an untouched checkout. The proof that failures later belong to a feature.")
    baseline_sha: str = Field(default="", description="The commit the baseline measured. When the base branch has moved past it -- a feature merged, anyone's commit -- the next build baselines the new commit first, so a failure it meets is compared with the code it actually started from.")
    baseline_at: str = Field(default="", description="When that measurement was taken. A check row is only true of the tree as it stood then, and a human has no other way to know the tree has moved since.")
    #: A digest of the Dockerfile the last baseline ran in. The Dockerfile can
    #: now change without anyone touching this record -- it is a file in the
    #: repository, committed whenever someone commits it -- and checks measured
    #: in one environment say nothing about another.
    baseline_dockerfile: str = ""
    #: Whether this project's test runner fails when pointed at a selection that
    #: cannot match. Measured at gate 0 against the project's own command, never
    #: assumed -- "most runners exit non-zero on an empty selection" is true and
    #: is exactly the kind of belief that put a table of framework regexes in the
    #: orchestrator. `None` means the probe could not run, which is not the same
    #: as a runner that tolerates emptiness and must not be recorded as one.
    empty_run_detected: bool | None = Field(default=None, description="Measured at repo ready: does this project's test command exit non-zero when given nothing to run? None means unmeasured.")
    empty_run_evidence: str = Field(default="", description="The probe's command and what it exited with, so the measurement can be read rather than trusted.")
    warm_checks: list[str] = Field(default_factory=list, description="Checks that setup runs once with the network, after its own commands, so a tool that fetches only when it runs has fetched before the checks run without it. Found at the baseline, never proposed: a check lands here by failing offline and then not failing that way after one such run.")
    toolchain_versions: dict[str, str] = Field(default_factory=dict, description="What each of the environment's `version_commands` printed at the last run of the checks, by command.")
    runtime_packages: list[str] = Field(default_factory=list, description="Packages actually installed where the checks run, measured in the environment rather than read from a manifest. The verify lane is told these names and nothing else about the world outside its spec: an oracle that does not know what it may import writes a test suite that cannot be collected.")
    #: What wrote this. Defaults low, so a record from before the stamp existed
    #: is treated as old rather than as current -- the safe direction, because
    #: the only code that could have written one is code that predates this.
    state_version: int = 1
    digest_budget: int = 120_000
    #: The surveyor's own budget, and larger on purpose.
    #:
    #: `digest_budget` above is paid per feature, several times, by the scout and
    #: the review panel. The survey is paid ONCE per project and decides how
    #: every feature afterwards is built and tested, so the two are budgeted for
    #: opposite reasons, and sharing one number prices the important call like
    #: the repeated one. On one repository a shared 120k omitted 78 files, and
    #: two surveys of it resolved the environment differently -- one to the
    #: project's compose file and one to a hand-rolled Postgres.
    survey_digest_budget: int = 600_000
    error: str = ""

    def runtime_contract(self) -> str:
        """What this project's oracle is told about where its tests will run.

        Everything here is read off this record -- the services it declares,
        the commands that run its files, and a person's prose about it -- and
        nothing off any other project or off the factory's own configuration.
        That is the whole point of it being a method here: as a method on the
        factory's configuration, it would tell every project's oracle about
        whichever project that configuration was written for.

        Three parts, and the first two are derived rather than declared, so they
        cannot drift from what actually runs. The services are up for every
        command in the session, and their addresses are published to all of
        them -- including code that runs outside the page, like a fixture that
        seeds data through an API, which is the code that went without. The
        variables a per-file command sets itself are named against the files it
        runs. The prose is `oracle_runtime`.

        None of it comes from the repository, so it is safe in front of a blind
        agent for the same reason the package list is. (INV-1)
        """
        lines: list[str] = []
        services = [s for s in (self.environment.services if self.environment else [])
                    if (s.name or "").strip()]
        if services:
            shown = []
            for service in services:
                key = service.name.upper().replace("-", "_")
                shown.append(f"- `{service.name}` -- at the address in `FACTORY_URL_{key}`; "
                             f"its port alone is in `FACTORY_PORT_{key}`")
            lines.append(
                "These are running before your first test starts and stay up until "
                "your last one finishes. Every command that runs your files can read "
                "these variables, and so can code of yours that runs outside a "
                "browser -- a fixture that creates data through an API, say:\n\n"
                + "\n".join(shown)
                + "\n\nBuild every address from these variables. The ports are chosen "
                "fresh for every run, so a hard-coded port -- or a fallback like "
                "`localhost:8300` for when a variable is missing -- reaches nothing, "
                "and a test that uses one stops before it asserts anything."
            )

        def shown_env(exported: list[tuple[str, str]]) -> list[str]:
            # A value the shell computes has no literal to show, and showing the
            # part before the expansion is how a URL arrives with no port on it.
            return [f"- `{name}` -- computed when the command runs; read it, do not rebuild it"
                    if "$" in value else f"- `{name}={value}`"
                    for name, value in exported]

        # The command a file actually runs under, not the one that also writes a
        # report: that one adds the harness's own bookkeeping, which is nothing
        # a test should read.
        scopes = ([(r.match, command_env(r.command)) for r in self.test_file_commands]
                  if self.test_file_commands else [("", command_env(self.oracle_command))])
        # Said for the whole suite only when one command runs the whole suite.
        # A variable the pytest rule sets is not set for a Playwright file, and
        # dropping the rules that set nothing must not make it sound as if it were.
        whole = len(self.test_file_commands) <= 1
        scopes = [(match, exported) for match, exported in scopes if exported]
        if scopes:
            if whole:
                head = "The command that runs your suite sets these environment variables itself:"
                body = "\n".join(shown_env(scopes[0][1]))
            else:
                head = ("The command that runs each of your files sets these environment "
                        "variables itself, and only for the files it runs:")
                body = "\n\n".join(f"Files matching `{match}`:\n" + "\n".join(shown_env(exported))
                                   for match, exported in scopes)
            lines.append(
                head + "\n\n" + body
                + "\n\nRead them from the environment rather than hard-coding the values "
                "shown. Anything else you may rely on is named in this section; a "
                "variable that appears nowhere in it is not set, and reading one is how "
                "a suite comes to stop before it asserts anything."
            )
        if self.oracle_runtime.strip():
            lines.append(self.oracle_runtime.strip())
        return "\n\n".join(lines)


class SandboxState(BaseModel):
    """The bounded environment one feature is built in.

    A worktree isolates the code; a container isolates the execution. Two
    features on one machine need both -- a worktree does nothing about a port,
    a test database, or a package cache.
    """

    feature_id: str
    project_id: str
    kind: Literal["worktree", "copy"] = "worktree"
    path: str = Field(default="", description="The checkout the feature is built in.")
    branch: str = Field(default="", description="factory/<feature_id>, living in the project's own repository.")
    base_sha: str = Field(default="", description="The commit it branched from, resolved at creation.")
    image: str = Field(default="", description="The project image its checks run in.")
    container: str = Field(default="", description="The container name, while one is running.")
    created_at: str = Field(default_factory=_now)
    released_at: str = ""
    commit_sha: str = Field(default="", description="The commit the finished work landed on, once there is one.")
    setup_at: str = Field(default="", description="When the environment's setup commands last ran in this checkout. Setup runs with the network; the checks run without it. A container runner reruns it every round -- its toolchain goes down with the image.")


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------


# Names written to disk before the planner became the spec writer. Evidence is
# append-only, so those records keep the old name for ever; state files are
# rewritten on every save, so they are read through this once and stay current.
RENAMED_PHASES = {"planner": "spec_writer"}
RENAMED_STAGES = {"planning": "writing_spec"}


class PhaseState(BaseModel):
    name: str

    @field_validator("name", mode="before")
    @classmethod
    def _current_name(cls, value: object) -> object:
        return RENAMED_PHASES.get(value, value) if isinstance(value, str) else value
    status: Literal["pending", "running", "done", "failed"] = "pending"
    started_at: str = ""
    ended_at: str = ""
    detail: str = ""
    error: str = ""

    @property
    def elapsed_s(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.ended_at or _now()
        try:
            return (datetime.fromisoformat(end) - datetime.fromisoformat(self.started_at)).total_seconds()
        except ValueError:
            return 0.0


class FeatureState(BaseModel):
    feature_id: str
    project_id: str = ""
    title: str = ""
    intent: str = ""
    stage: Stage = "awaiting_answers"

    @field_validator("stage", mode="before")
    @classmethod
    def _current_stage(cls, value: object) -> object:
        return RENAMED_STAGES.get(value, value) if isinstance(value, str) else value

    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    spec_hash: str = ""
    sandbox: SandboxState | None = None
    phases: list[PhaseState] = Field(default_factory=list)
    error: str = ""

    # Which process is doing the work, as "<pid>:<start time>". Written when a
    # run takes the feature and cleared when it lets go.
    #
    # It exists because `stage` is a claim and not a fact. A server killed
    # mid-build leaves "building" behind with nobody building, and every guard
    # that reads the stage then refuses forever: discard says "agents are still
    # writing to it, wait for it to finish", and the wait cannot end. The pid is
    # what turns that claim back into something checkable.
    #
    # The start time is in there because pids are reused. A stale pid that has
    # been handed to something unrelated would otherwise read as a live run.
    owner: str = ""

    # When a build that is frozen and waiting on a plan will start, as an epoch
    # second, and why it is waiting. On disk rather than only in the sleeping
    # task, because a scheduled run a server restart silently forgets is worse
    # than one that was never scheduled: the human was told it would happen.
    start_at: float = 0.0
    waiting_reason: str = ""

    # Which pass of the repair loop the line is on, while it is on it: 0 is the
    # first pass over what the build made, N is repair round N. `rework_closing`
    # is the way out -- the simplifier, the last panel, the regressions. Kept
    # here because the packet that carries the round count is written at the
    # very end, and a run in round 2 looked exactly like one in its first pass.
    rework_round: int = 0
    rework_closing: bool = False


class Ruling(BaseModel):
    decision_id: str
    ruling: Literal["accept", "send_back"]
    note: str = ""
    at: str = Field(default_factory=_now)


class HumanFlag(BaseModel):
    """Something a human identified at feature review, routed by that human.

    A finding with authority. It skips the arbiter -- routing is a judgment and
    this judgment was made by a person -- and enters the ledger already carrying
    its disposition. INV-11 applies to it exactly as to any other finding: it
    reaches every later packet with its original text and its outcome beside it.
    """

    id: str = Field(description="H-1, H-2, ...")
    source: Literal["comment", "ruling", "finding", "unclaimed", "criterion"] = Field(default="comment", description="Where in the review this was raised.")
    anchor: str = Field(default="", description="What it is attached to: a path and line, a decision id, a criterion id.")
    text: str = Field(description="What the human wrote, verbatim. Never summarised. Empty is meaningful and is not a missing value: it says they ruled on a finding without adding anything of their own, and the ledger entry then carries that finding's words rather than words invented for it.")
    disposition: Disposition = Field(default="repair", description="Where this goes, from here on: `repair` sends it to the repair loop; anything else is `dismissed`, for whatever reason the note gives. A person chooses between those two and nothing else. `oracle` appears here too but is never chosen: it is what the arbiter writes over a repair whose file only the oracle may edit, so the flag reads back saying where it actually went. (An older flag may still carry one of the finer-grained choices this used to offer -- needs_spec_change, out_of_spec, escalate, not_a_defect -- and reads back exactly as filed; it is just no longer offered going forward.)")
    severity: FindingSeverity = Field(default="major", description="How much it matters.")
    at: str = Field(default_factory=_now)


ReworkRoute = Literal["accept", "repair", "reopen_spec"]


class ReworkPlan(BaseModel):
    """Where a check-2 review goes, computed from what was flagged.

    Not chosen from a menu. This is INV-6 at the human boundary: the human
    supplies the judgment, one disposition per flag, and plain code decides
    what it means. A route nobody can honour is never offered.

    Two buckets, because a human picks between two things: `to_repair`, and
    everything else in `dismissed` -- including an older flag filed under one
    of the finer-grained dispositions that are no longer offered.

    `to_oracle` is not a third choice. It is a repair whose file only the
    oracle may write, sorted out of `to_repair` by the arbiter so the screen
    can say where it went; it rides the same dispatch, and the loop runs the
    oracle's fix session at the top of the round. `forced_by` stays, always
    empty: no human disposition can produce `reopen_spec`, but the code that
    guards that route still reads this field, and it costs nothing to leave it
    computed rather than touch code that is already safely unreachable.
    """

    route: ReworkRoute = "accept"
    because: str = ""
    forced_by: list[str] = Field(default_factory=list, description="Flag ids that forced a reopened spec. Always empty now that no human disposition can produce that route.")
    to_repair: list[str] = Field(default_factory=list)
    to_oracle: list[str] = Field(default_factory=list, description="Flag ids the factory sorted to the oracle, because the file they land on is one only the oracle may edit. A subset of what was ruled `repair`, never chosen as such, and dispatched with the repair round rather than instead of it.")
    dismissed: list[str] = Field(default_factory=list, description="Everything not sent to the repair loop, whatever the reason.")


class Verdict(BaseModel):
    verdict: Literal["accepted", "rejected"]
    note: str = ""
    at: str = Field(default_factory=_now)


# --------------------------------------------------------------------------
# as-built -- the reader and the cartographer
#
# A standing account of a repository as it was actually built, for the person
# answering for code agents wrote. The reader reports what one file contains, in
# any language; code joins those reports into a graph and checks every claim it
# can against the repository. The cartographer only names and describes what
# code already grouped. Nothing either returns becomes structure unchecked.
# --------------------------------------------------------------------------


class FunctionEntry(BaseModel):
    name: str = Field(description="The function, method or class name exactly as written in the file, without its parameters. For a method, the bare method name -- not `Class.method`.")
    line: int = Field(default=0, description="The line number where it is defined, read off the numbered listing you were given. Code checks this line contains the name; a guessed number is worse than 0.")
    calls: list[str] = Field(default_factory=list, description="Names this function calls or instantiates: other functions and methods in this file, and names it imported from elsewhere in this repository. Bare names, last segment only (`save`, not `self.store.save`). Leave out the language's standard library and third-party packages. Each name once.")


class WayIn(BaseModel):
    kind: Literal["route", "command", "job", "screen", "export", "event"] = Field(description="route: an HTTP/RPC endpoint this file serves. command: a CLI command or entry script. job: something run on a schedule or from a queue. screen: a page or view a person navigates to. export: the public API of a library, when the repository is a library. event: a handler for a message, webhook or signal from outside.")
    name: str = Field(description="How the outside names it: the route path as declared (`/api/cards/{card_id}`), the command (`app migrate`), the job's name, the screen's URL or title, the exported symbol.")
    method: str = Field(default="", description="For a route, its HTTP method in capitals. Empty otherwise.")
    handler: str = Field(default="", description="The function in THIS file that handles it, exactly as it appears in `functions`. Empty if it is not handled by a named function here.")


class RouteCall(BaseModel):
    method: str = Field(default="", description="The HTTP method, in capitals, when the code says it; empty when it does not.")
    path: str = Field(description="The path this code calls on its own back end, as a template. When the URL is built by a helper (`featureUrl(id) + '/diff'`), resolve the helper to the path it produces, with each variable part written `{name}`: `/api/features/{id}/diff`. Only calls to this repository's own server, never to third-party APIs.")


class FileRecord(BaseModel):
    """What one file contains, as reported by the reader. Code checks the rest."""

    purpose: str = Field(description="What this file is for, in one or two plain sentences a developer new to the repository can use. What it does and for whom, not how. No 'This file...' preamble.")
    kind: Literal["code", "test", "config", "docs", "other"] = Field(default="code", description="code: part of what the product runs or ships -- source, a page, a stylesheet, a prompt the product loads, a migration, a build script. test: a test, fixture or test support. config: settings the product or its tooling reads. docs: written for people -- documentation, design notes, mockups, examples kept for reference, snapshots of old versions. other: anything else, such as data files.")
    is_test: bool = Field(default=False, description="True if this file is a test, a test fixture or test support, rather than code the product runs. Same as kind == test.")
    imports: list[str] = Field(default_factory=list, description="Files in THIS repository this file imports, requires or includes, as repository-relative paths (`src/cards/store.py`). Resolve relative and module-style imports to the file they name, using the file tree you were given. Leave out the standard library and third-party packages.")
    launches: list[str] = Field(default_factory=list, description="Files in this repository this file runs as a separate process, mounts into a container, loads by path or registers by filename rather than importing -- a script it executes, a worker it spawns, a file it copies into an image. Repository-relative paths. Empty for most files.")
    defines: list[str] = Field(default_factory=list, description="The public names this file defines at its top level: functions, classes, constants, components, types. Leave out private helpers.")
    functions: list[FunctionEntry] = Field(default_factory=list, description="Every function, method and class defined in the part of the file you were shown, in order, with the line it starts on and what it calls. Include private ones: this is how code finds the file's internal structure.")
    ways_in: list[WayIn] = Field(default_factory=list, description="Everything outside this code can call that is declared in this file. Empty for most files; the files that declare a server's routes, a CLI, jobs or screens are where these live.")
    routes_called: list[RouteCall] = Field(default_factory=list, description="Calls this file makes to this repository's own back end, typically from a front end or a client. Empty for most files.")
    entities_defined: list[str] = Field(default_factory=list, description="Data entities this file defines -- tables, models, schemas, record types -- by the name the code uses.")
    entities_read: list[str] = Field(default_factory=list, description="Entities this file reads from storage, by the name used where they are defined.")
    entities_written: list[str] = Field(default_factory=list, description="Entities this file creates, updates or deletes in storage.")
    outside: list[str] = Field(default_factory=list, description="Systems outside this repository this file talks to, named plainly: 'PostgreSQL', 'Stripe API', 'Docker', 'a model provider', 'the local git checkout'. Not libraries -- systems.")
    domain_terms: list[str] = Field(default_factory=list, description="The project's own nouns this file uses -- the words a person working on this product would have to learn (a board, a card, a packet). Two to six, each a short noun phrase. Not programming terms.")


class BatchedFileRecord(BaseModel):
    """One file's record, in a call that read several."""

    path: str = Field(description="The file this record is about, copied exactly from its heading.")
    record: FileRecord


class FileRecordBatch(BaseModel):
    """What several small files contain -- one record for each, each as if it had been read alone."""

    files: list[BatchedFileRecord] = Field(default_factory=list, description="One record per file you were shown, in the order shown. Every file gets its own; none is merged into another.")


class NamedGroup(BaseModel):
    key: str = Field(description="The key you were given for this group, copied exactly.")
    name: str = Field(description="A short plain name for the group, how a developer on the project would say it out loud: 'Running agents', 'Checkout and payments'. Two to five words. Not a file name, not a directory name.")
    code: str = Field(default="", description="A three-letter ID for the group, in capitals, that a person can say and recognise -- built from its name: 'RQB' for 'Referral queue backend', 'AGT' for 'Running agents'. Distinct from every other code in the message, and never reading as a rude, offensive or awkward word.")
    summary: str = Field(description="What this group is for, in one to three sentences, from its members' purposes. What it does and why the rest of the system needs it.")


class GroupNaming(BaseModel):
    """Names and summaries for groups code already formed."""

    groups: list[NamedGroup] = Field(default_factory=list, description="Exactly one entry per key you were given.")


class CapabilityGroup(BaseModel):
    name: str = Field(description="What a person can do, in their words: 'Review the packet', 'Pay for an order'. A verb phrase, three to six words.")
    code: str = Field(default="", description="A three-letter ID for the capability, in capitals, built from its name: 'RVP' for 'Review the packet'. Distinct from every other capability's code and from the subsystem codes you were given, and never reading as a rude, offensive or awkward word.")
    summary: str = Field(description="One or two sentences on what this capability lets someone do, in the words of someone who uses it.")
    area: str = Field(default="", description="A one- or two-word area it belongs to, shared with related capabilities: 'Projects', 'Billing', 'Setup'.")
    ways_in: list[str] = Field(default_factory=list, description="The ids of the ways in that make up this capability, copied exactly from the list you were given.")


class CapabilityMap(BaseModel):
    """The ways into a system, grouped into what a person can do with it."""

    capabilities: list[CapabilityGroup] = Field(default_factory=list, description="Every capability. A way in belongs to exactly one.")
    not_capabilities: list[str] = Field(default_factory=list, description="Ids of ways in that are plumbing, not something a person does: a health check, a version endpoint, serving static files.")


class RuntimeGroup(BaseModel):
    name: str = Field(description="What runs, as a developer on the project would say it: 'Browser app', 'API server', 'Worker', 'CLI'. Two or three words.")
    what: str = Field(default="", description="How it runs, in one line: the framework and where it runs -- 'React, built by Vite; runs in the user's browser', 'FastAPI on uvicorn'.")
    kind: Literal["browser", "server", "worker", "command", "mobile", "desktop", "library", "other"] = Field(default="other", description="browser: a web front end. server: something that serves requests. worker: something that runs jobs from a queue or a schedule. command: scripts and commands a person runs. mobile, desktop: an app installed on a device. library: code other programs import.")
    subsystems: list[str] = Field(default_factory=list, description="The keys of the subsystems whose code runs in this process, copied exactly from the list you were given. A subsystem goes in one place only.")


class Actor(BaseModel):
    name: str = Field(description="Who uses the system, by their role: 'Coordinator', 'Patient', 'Operator'. A person, not a system -- a system that calls in is an outside system.")
    what: str = Field(default="", description="Who they are, in a few words: 'the trial site's staff'.")
    capabilities: list[str] = Field(default_factory=list, description="The codes of the capabilities this role uses, from the list you were given.")


class OutsideSystemGroup(BaseModel):
    name: str = Field(description="The outside system, as a person would name it: 'PostgreSQL', 'Stripe', 'EHR (Epic)', 'Email / SMS provider'.")
    what: str = Field(default="", description="What this system is used for, in one sentence.")
    kind: Literal["datastore", "service", "provider", "platform", "tooling", "other"] = Field(default="other", description="datastore: a database, a cache, object storage. service: an API this system calls. provider: a third party that sends or receives on its behalf -- email, SMS, payments. platform: where it runs in production -- a cloud, a host, an orchestrator. tooling: what builds, tests, packages or ships it and is not used while it runs -- CI, containers and compose for packaging or running it locally, a registry, a deploy service.")
    mentions: list[str] = Field(default_factory=list, description="Every mention from the list you were given that names this system, copied exactly. A mention belongs to one system, or is dismissed.")
    calls_in: list[str] = Field(default_factory=list, description="Ids of routes nothing in this repository calls that this system calls -- a webhook, a callback, a protocol it speaks to us. Copied from the list you were given.")


class DismissedMention(BaseModel):
    mention: str = Field(description="A mention from the list you were given, copied exactly.")
    why: Literal["library", "tool", "this repository", "not a system"] = Field(description="library: a package the code imports. tool: a program run on a developer's machine to build or check it -- a compiler, a package manager, a linter. this repository: its own server or code, named as if it were outside. not a system: a standard, a format, a regulation, a browser API.")


class ArchitectureAccount(BaseModel):
    """How the system runs: what runs where, who uses it, and what it talks to."""

    runtimes: list[RuntimeGroup] = Field(default_factory=list, description="The processes the product runs as, the one people reach first first.")
    tooling: list[str] = Field(default_factory=list, description="Keys of subsystems that are build configuration, dependency lists, development setup or CI -- nothing that runs in production.")
    actors: list[Actor] = Field(default_factory=list, description="The people who use it, by role.")
    outside: list[OutsideSystemGroup] = Field(default_factory=list, description="The systems outside this repository it talks to while it runs, and the ones that build, test or ship it.")
    dismissed: list[DismissedMention] = Field(default_factory=list, description="Every mention that is not an outside system, and why.")


class StartHere(BaseModel):
    path: str = Field(description="A repository-relative path, copied from the list you were given.")
    why: str = Field(description="One sentence on why a newcomer should read it early.")


class SystemAccount(BaseModel):
    """The top of the as-built: what this system is, and where to begin."""

    summary: str = Field(description="What this system is, in three or four sentences for a developer arriving cold: what it does, for whom, and how it is put together at the largest scale. Plain words; name the stack once.")
    start_reading: list[StartHere] = Field(default_factory=list, description="Three to five files to read first, most useful first.")
