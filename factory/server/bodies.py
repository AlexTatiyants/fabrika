"""The request bodies, every one of them at module scope -- see `ResurveyRuling`
for why that is not a matter of taste: FastAPI resolves a route's annotations
against the namespace its function was defined in, so each route group imports
the bodies it reads from here.
"""

from __future__ import annotations

import typing
from typing import Literal

from pydantic import BaseModel, Field

from ..schemas import DependencyPolicy, EnvironmentSpec, Gate, HumanDisposition


class NewProject(BaseModel):
    path: str = Field(min_length=1, description="Directory on this machine to register.")
    name: str = ""


class ProjectUpdate(BaseModel):
    """Every field optional: only what is sent is changed."""

    name: str | None = None
    repo: str | None = None
    base_ref: str | None = None
    digest_budget: int | None = None
    gates: list[Gate] | None = None
    environment: EnvironmentSpec | None = None
    #: The blind suite's settings for this repository. See `ProjectState`.
    oracle_command: str | None = None
    oracle_collect_command: str | None = None
    oracle_runtime: str | None = None
    #: What this project takes on as a dependency. Not part of what the
    #: baseline measured, so changing it does not send the project back.
    dependency_policy: DependencyPolicy | None = None


class CheckRunsBody(BaseModel):
    name: str = Field(min_length=1)
    #: `light` every round, `heavy` once in the final pass, `` to let the
    #: check's timing decide again.
    runs: str = ""


class GateDeclineBody(BaseModel):
    name: str = Field(min_length=1)
    reason: str = ""
    #: Carried for a check that was only ever proposed -- there is no gate on
    #: the list to read it off, and reinstating one without its command would
    #: hand back a name and nothing to run.
    command: str = ""


class GateNameBody(BaseModel):
    name: str = Field(min_length=1)


class RecommendationBody(BaseModel):
    key: str = Field(min_length=1)
    reason: str = ""


class ProposedCheckBody(BaseModel):
    item: str = Field(min_length=1)
    accept: bool = False
    reason: str = ""


class GuideDraftBody(BaseModel):
    decision: Literal["write", "decline"]
    markdown: str = ""


class GuideOfferBody(BaseModel):
    kind: Literal["design_md", "agents_md", "claude_md", "design_line",
                  "skills_link_agents", "skills_link_claude", "commit_working"]
    decision: Literal["write", "decline"]
    #: A person's edits, by path: what they approved is what is written.
    contents: dict[str, str] = Field(default_factory=dict)


class SkillsDirBody(BaseModel):
    folder: Literal[".claude/skills", ".agents/skills"]


class DiagnosisFixBody(BaseModel):
    check: str = Field(min_length=1)
    fix: int = Field(ge=0)


class CheckNameBody(BaseModel):
    name: str = Field(min_length=1)


class NewCodeBody(BaseModel):
    name: str = Field(min_length=1)
    limit: float | None = None


class FamilyBody(BaseModel):
    family: str = Field(min_length=1)
    reason: str = ""


class AdoptRecommendationBody(BaseModel):
    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    #: Empty means "use the reading's `would_gate` as written". It is a default
    #: and not a fact: the schema asks for one command and a reading sometimes
    #: writes two joined by "and", so the human edits it before it is a check.
    command: str = ""


class ScaffoldBody(BaseModel):
    paths: list[str] = Field(min_length=1)
    #: Paths the human has seen a diff of and chosen to overwrite. Separate from
    #: `paths` on purpose: writing a new file and replacing one somebody wrote
    #: are different acts, and a single list would make the second reachable by
    #: accident from any caller that meant the first.
    replace: list[str] = Field(default_factory=list)


class ProviderUpdate(BaseModel):
    api_key: str | None = None
    base_url: str | None = None


class EditorUpdate(BaseModel):
    #: Empty is legal -- it hides the "open in editor" link rather than
    #: refusing the save, the same rule `Config.editor_url`'s own docstring
    #: states.
    editor_url: str


class RoleUpdate(BaseModel):
    model: str | None = None
    # Which harness carries this agent. Editable for the same reason the model
    # is: "Opus 5 via Claude Code" and "Claude Opus 5 via OpenRouter" are one
    # choice made in one place, and they are not the same thing.
    route: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    providers: list[str] | None = None
    allow_fallbacks: bool | None = None
    samples: int | None = None
    enabled: bool | None = None


class StaffingPin(BaseModel):
    route: str = ""
    model: str = Field(min_length=1)
    reasoning_effort: str = ""


class StaffingRole(BaseModel):
    level: str
    # Only for an agent on neither team: the side whose provider it takes.
    side: str = ""
    # Its own route, model and effort, whatever its level says.
    pin: StaffingPin | None = None


class StaffingUpdate(BaseModel):
    """The whole of the Agent configuration screen, written at once."""

    staffing: dict[str, str]
    levels: dict[str, dict[str, dict[str, str]]] = Field(default_factory=dict)
    roles: dict[str, StaffingRole] = Field(default_factory=dict)


class SetupKey(BaseModel):
    """A key for a command-line route that spends one. Tested, then stored."""

    api_key: str = Field(min_length=1)


class SetupDecline(BaseModel):
    declined: bool = True


class SetupCrew(BaseModel):
    """Two choices and a starting point; every agent follows from them."""

    build: str
    check: str
    preset: str = "suggested"


class SetupLimits(BaseModel):
    budget_usd: float = Field(gt=0)
    max_rounds: int = Field(ge=0, le=10)
    wall_clock_minutes: float = Field(gt=0)


class NewReviewAgent(BaseModel):
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    model: str = Field(min_length=1)
    samples: int = 1
    prompt: str = ""


class PromptBody(BaseModel):
    prompt: str


class NewFeature(BaseModel):
    intent: str = Field(min_length=1)
    title: str = ""
    allow_dirty: bool = False


class CorrectionBody(BaseModel):
    correction: str = Field(min_length=1)


class AnswersBody(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)
    deferred: list[str] = Field(default_factory=list)


class RulingBody(BaseModel):
    decision_id: str
    ruling: str
    note: str = ""


#: What a *human* may set on a flag, at the worklist -- narrower than the
#: arbiter's own `Disposition`, and enforced here (not on `HumanFlag` itself)
#: so that a flag filed under the old, wider choice still reads back. See
#: `HumanDisposition`'s docstring.
DISPOSITIONS = set(typing.get_args(HumanDisposition))


class FlagBody(BaseModel):
    text: str
    source: str = "comment"
    anchor: str = ""
    disposition: str = "repair"
    severity: str = "major"


class RetagBody(BaseModel):
    disposition: str


class VerdictBody(BaseModel):
    verdict: str
    note: str = ""


class CleanupPick(BaseModel):
    """One of a level's recorded cleanup fixes, picked from the project page.

    `option` null is "don't check criteria at this level", recorded as the
    person's decision; `reopen` takes that decision back."""

    tier: str
    option: int | None = None
    reopen: bool = False


class ResurveyRuling(BaseModel):
    """Which of a proposal's gate changes a human accepted.

    Module scope, with every other request body, and that is not a matter of
    taste. `from __future__ import annotations` makes every annotation a string,
    and FastAPI resolves those against the *module's* namespace -- so a body
    model defined inside `create_app` cannot be found, the parameter is demoted
    to a query string, and every POST to it fails with "field required: body"
    before any of this code runs. The endpoint had never once worked.
    """

    accepted: list[str] = []
    environment: bool = False
    #: Whether this ruling covers the proposal's checks. The Survey tab's
    #: ruling does not: checks are ruled on one at a time, in their rows.
    checks: bool = True
    #: Per test level, which of the reading's cleanup options a person picked;
    #: `None` is "leave it". Each is applied whole -- see `apply_proposal`.
    cleanup: dict[str, int | None] = {}
    #: One part of the reading, accepted where it is shown, with any fix chosen
    #: inside it. Empty is the whole-proposal ruling.
    part: str = ""
