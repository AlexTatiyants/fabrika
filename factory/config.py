"""The only file in the package that knows a model exists by name is the YAML it
loads. There are no model defaults in code, and no provider-specific branches.

Swapping the whole factory to a local server is a `base_url` change plus edits
to `factory.yaml`. (INV-7)
"""

from __future__ import annotations

import io
import json
import os
from urllib.parse import urlparse
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Literal

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from pydantic import BaseModel, Field

from .files import write_atomic
from .schemas import Gate

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")

class ConfigError(RuntimeError):
    pass


#: Config keys whose value is handed to a shell. `$NAME` in one of these belongs
#: to that shell and must reach it untouched -- see `_expand`. Suffix-matched so
#: a command added later is covered without anybody remembering this set.
_COMMAND_KEYS = frozenset({"command", "ready_when", "package_probes"})


def is_command_key(key: str) -> bool:
    """Whether a config key's value is a shell command rather than a setting."""
    return key in _COMMAND_KEYS or key.endswith(("_command", "_commands"))


def _expand(value: Any, key: str = "") -> Any:
    """AC-2.4 -- environment variables anywhere in the tree, so keys stay out of
    the file. Anywhere except a shell command.

    A command's variables belong to the shell that will run it, and this
    substitutes an unset one with "" before bash ever sees the string. Nothing
    fails when it happens, which is what makes it worth an exception rather than
    a convention: `rework.oracle_command` carried `kill $UPID; exit $RC` and ran
    as `kill ; exit`, so the server was never killed and the suite exited with
    the failed kill's status instead of pytest's -- every blind acceptance run
    reported failure whatever the code did.

    The settings that actually need this -- `api_key: $OPENROUTER_API_KEY` --
    are not commands, so the exception costs them nothing.
    """
    if isinstance(value, str):
        if is_command_key(key):
            return value

        def sub(m: re.Match[str]) -> str:
            name = m.group(1) or m.group(2)
            return os.environ.get(name, "")
        return _ENV_PATTERN.sub(sub, value)
    if isinstance(value, dict):
        return {k: _expand(v, k) for k, v in value.items()}
    if isinstance(value, list):
        # A list under a command key is an argv, and every element of it is part
        # of the command.
        return [_expand(v, key) for v in value]
    return value


class RoleConfig(BaseModel):
    """AC-2.2 -- per-role settings, merged over the `defaults` block."""

    name: str = ""
    #: Whose instructions this agent works from, where that is not its own name.
    #: Empty means `roles/<name>.md`.
    #:
    #: Two roles share a prompt when they are two jobs under one set of rules:
    #: a first reading of a repository and a later one are the same knowledge
    #: applied to different inputs, and they were two files until the same rule
    #: was corrected in one of them and not the other. What stays separate is
    #: everything else a role is -- its model, its route, its budget, and what
    #: its calls are billed to.
    prompt: str = ""
    model: str
    temperature: float = 0.2
    max_tokens: int = 8000
    reasoning_effort: str = "medium"
    providers: list[str] = Field(default_factory=list)
    allow_fallbacks: bool = False
    # Where this role goes when its own route will not carry it, and what to
    # ask for when it gets there.
    #
    # A subscription route has one stopping condition its plan actually has,
    # and waiting is the only remedy. A run that loses the review panel three
    # phases from a packet has spent everything before it for nothing -- and
    # it can happen at 100% of a five-hour window, or on an empty credit
    # balance with both windows open.
    #
    # The model has to change with the route, because a model id is a name one
    # vendor knows and nothing else does. Empty means this role does not fall
    # back, which stays the default -- a substitute reviewer is a weaker
    # reading than the one configured, and that is a choice to make rather
    # than to inherit.
    fallback_route: str = ""
    fallback_model: str = ""
    # Or the name of a lane in the `fallbacks:` block, which sets both. Two
    # lanes is the point: one fallback for everything puts the whole crew on
    # one vendor the moment two routes are dry, and a panel reviewing code its
    # own family wrote is not a panel. `load_config` resolves this into the two
    # fields above, so nothing downstream has to know lanes exist.
    fallback: str = ""
    system: str = ""

    # A review role runs in the review phase over the shared evidence bundle and
    # contributes findings. The reviewer and the adversary are review roles like
    # any other -- what separates them is their prompt. A review agent cannot
    # influence control flow and cannot hide anything, which is why adding one is
    # safe in a way that adding a pipeline phase is not.
    review: bool = False
    samples: int = 1
    enabled: bool = True

    # Which route carries this agent's calls. Empty means the default route,
    # which is the `api`/`executor` pair -- so a config that has never heard of
    # routes still behaves exactly as it says.
    route: str = ""

    # How long one call of this role may run before it is given up on, where
    # that is not the route's. Zero means the route's. A route's limit is set
    # for its ordinary calls; the first reading of a repository is not one --
    # it builds a large structured answer over a whole digest, took eleven to
    # fourteen minutes on four projects in a row, and was killed at fifteen on
    # the fifth. Raising the route's limit would let every stuck call wait as
    # long, so the one role that needs it says so.
    timeout_s: float = 0.0

    # How hard this agent has to think: `deep`, `standard` or `light`. Under a
    # `staffing:` block the level and the agent's side name its route, model
    # and effort through `levels:`, so a level changed once moves every agent
    # on it. A `route`, `model` or `reasoning_effort` written on the role itself
    # still wins -- that is a pin, and it is kept on purpose. Without
    # `staffing:` a level is only where the console's suggestion starts.
    level: str = ""
    # Which side's provider serves an agent that neither builds nor checks.
    # Empty follows `staffing.neither`. A builder or a checker cannot name the
    # other side: what it does decides that, not where it is convenient to run.
    side: str = ""
    # Which of `LEVEL_FIELDS` the role's own block wrote over its level. Filled
    # by `load_config`; never read from the file.
    pinned: list[str] = Field(default_factory=list)


class ContainerCredential(BaseModel):
    """One credential, and where it comes from, for a session in a container.

    Three kinds, because the three harnesses measured here keep their sign-in
    in three different places and none of them can be talked out of it:

      kind: env       a variable this process already has, or the provider key
                      this app stores -- `name` is what the harness reads it as,
                      `source` the variable to take it from (default: `name`),
                      and `from_api_key: true` takes this app's stored key.
      kind: file      a file on this machine (`path`), landed at `target` in
                      the container. Copied, never mounted from its original:
                      a harness that refreshes its own token inside a container
                      must not be able to rewrite what your terminal signs in
                      with.
      kind: keychain   a macOS keychain item (`service`), landed at `target`.
                      Reading it may raise a system prompt the first time,
                      which is the OS asking the human, and correct.

    `optional: true` means a session may start without it. Everything else is
    required, and a missing one fails before the container is touched -- a
    harness that starts without its credential does not fail, it burns a turn
    and answers "please sign in".
    """

    kind: Literal["env", "file", "keychain"] = "env"
    name: str = ""
    source: str = ""
    from_api_key: bool = False
    path: str = ""
    service: str = ""
    target: str = ""
    optional: bool = False


class RouteEffort(BaseModel):
    """How one route is told one reasoning level: arguments added to its
    command, variables set in its environment, or both."""

    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class RouteConfig(BaseModel):
    """Where a call goes, and what it is authenticated as.

    One concept rather than two provider systems that do not know about each
    other: `api.*` serving completions over HTTP, `executor.*` serving
    filesystem sessions by launching a subprocess, and something bridging them
    by taking a model out of one and re-prefixing it for the other. That pair
    cannot express "the spec writer runs on a subscription", because a
    subscription is neither of those things -- it is a CLI that can serve both.

    A route offers one capability or two:

    - **complete** -- one structured answer against a schema. An `api` route does
      this over HTTP; a `cli` route does it by running a command with tools
      switched off and a JSON schema on the command line.
    - **author** -- a session with a filesystem, for an agent that writes files.

    They are separate because they are separately available. OpenRouter can
    complete and cannot author, which is exactly why OpenHands exists: it is a
    harness *over* an API route rather than a route of its own, and it spends
    that route's key. The three subscription CLIs can do both and bring their own
    sign-in.

    `metered` is not decoration. A route billed per token reports dollars, and
    the budget guard can stop a run before it overspends. A subscription route
    reports nothing, because there is nothing to report -- its ceiling is a
    rolling window, not a balance. Saying so here is what stops the ledger
    reading `$0.00` and a human reading that as free.
    """

    name: str = ""
    kind: str = "api"                      # "api" | "cli"
    metered: str = "tokens"                # "tokens" | "turns"
    enabled: bool = True
    label: str = ""                        # what the console calls it

    # A route is two things wearing one name, and the console draws them apart.
    #
    # The harness is the program that carries a call: it is installed or it is
    # not, it has a version, and reinstalling it changes nothing about anyone's
    # bill. The account is who serves the model and charges for it: it holds
    # the plan windows, the credit balance, and the refusals. One card showing
    # both said "Claude Code · 8% of five hours left", which reads as a
    # property of the CLI and is a property of the subscription behind it --
    # and put a red lamp on a *program* when an *account* ran out of credit,
    # sending a human to the config, the binary and the login, none of which
    # was wrong.
    #
    # Named here and nowhere else. A package that knows which vendor stands
    # behind which command has to be edited to add the next one (V-6, INV-7),
    # so these are labels this file supplies and `routes.py` only passes on.
    harness_label: str = ""                # the program: "Claude Code", "OpenHands"
    account_label: str = ""                # who serves and bills: "Anthropic"
    # "subscription" -- a plan, signed in through this harness and usable by no
    # other; or "key" -- a credential any harness that takes one can spend.
    # Empty means: infer from `billed`, which is the same distinction seen from
    # the money's side.
    account_kind: str = ""

    # --- api routes ---
    base_url: str = ""
    api_key: str = ""
    model_prefix: str = ""

    # --- cli routes -----------------------------------------------------
    # One completion: the prompt arrives on stdin, a JSON envelope leaves on
    # stdout. `{model}`, `{system}` and `{schema}` are substituted in argv.
    #
    # Tools must be off in this command. Several roles are defined by not having
    # them -- the review panel gets "one structured completion over a text
    # bundle and no tools", and the spec writer's whole relationship to the
    # repository is the digest it was given. A command that leaves tools on
    # hands those agents a filesystem and quietly voids both contracts.
    command: list[str] = Field(default_factory=list)
    # Where the answer and the accounting live inside that envelope. Dotted
    # paths, because every CLI names them differently and none of them is wrong.
    result_key: str = "result"
    # Where a *schema* answer arrives, when that is somewhere else. One tool
    # puts the agent's prose in `result` and the structured object beside it,
    # so reading `result` for a schema call returns a sentence about the answer
    # rather than the answer.
    schema_result_key: str = ""
    error_key: str = ""
    cost_key: str = ""
    prompt_tokens_key: str = ""
    completion_tokens_key: str = ""
    # Input the tool reports outside `prompt_tokens_key`, added to it. Claude
    # Code puts only the uncached remainder in `usage.input_tokens` -- 72 of a
    # session's 1.9 million -- and the rest in its cache fields, so a count
    # read from the one key was out by four orders of magnitude. Codex's
    # `input_tokens` already includes what it read from cache: nothing to add.
    prompt_tokens_extra_keys: list[str] = Field(default_factory=list)
    turns_key: str = ""
    # Where a session lists the tool calls it was refused. A harness that asks
    # for permission with nobody at the other end does not fail: it is denied,
    # carries on, and reports code it never ran as finished. Every unit of one
    # run did exactly that -- no test, lint or type check executed -- and
    # nothing but the agents' own prose said so.
    denials_key: str = ""
    # A route whose CLI cannot be told a schema gets it in the prompt instead,
    # and leans on the same repair loop an API route uses when it answers badly.
    schema_in_prompt: bool = False

    # Whether this route can return a schema-shaped answer at all.
    #
    # Set false for a tool that serves prose reliably and structured output
    # badly, and every agent in this pipeline needs structured output. The
    # alternative to saying so here is a role pointed at it that hangs until the
    # timeout, once per phase, with nothing in the log naming the cause.
    schema_completions: bool = True
    # "json": one JSON object on stdout, the shape every route above assumes.
    # "jsonl": one JSON object per line -- an event stream rather than an
    # envelope. `codex exec --json` prints its progress this way and there is
    # no flag to turn it off, so the dotted-path keys above are read against
    # the events merged in order, last write wins. That is a real assumption
    # (the final `item.completed` before the stream ends is the answer) and
    # not a guarantee this format makes, but it is what a flattened,
    # non-interactive exchange produces in practice.
    stdout_format: str = "json"

    # Whether a role here may name no model at all and let the tool pick.
    #
    # This is the opposite of the fallback V-6 forbids, not an instance of it.
    # V-6 refuses a model *this package* invents when a human named none; here
    # the human has explicitly said "whatever this tool is configured to use",
    # and the tool is the thing that knows. What makes it safe is that the
    # answer is not a mystery afterwards: `resolved_model_key` reads back which
    # model actually ran, and that is what the ledger and the packet record.
    default_model_ok: bool = False
    # Where the tool reports the model it actually used. Without it a run with
    # no `--model` leaves no record of what produced the work, which is worse
    # than a wrong id -- a packet from six months ago would say nothing at all.
    resolved_model_key: str = ""

    # --- the plan's own gauge -------------------------------------------
    # Where this tool reports how much of its rolling window has been used, if
    # it reports it at all. A dotted path to a map of window name to window:
    # `{"five_hour": {"utilization": 0.21, "resetsAt": 1788548400}, ...}`.
    #
    # Empty is the honest answer for most routes, and it must stay expressible:
    # of three CLI harnesses measured, one carries this and one carries no rate
    # information in its stream at all. A mechanism that pretended to be uniform
    # would report a confident zero for the routes that cannot answer, which is
    # the one reading worse than none.
    meter_windows_key: str = ""
    meter_utilization_key: str = "utilization"
    meter_resets_key: str = ""
    # The two ways a tool can report the same two numbers differently. One gives
    # a fraction and an absolute epoch second; another gives a percentage and a
    # countdown. Both are describable here rather than being a branch in
    # `meters.py`, which is the rule this whole block follows: the module knows
    # about windows, and nothing in it knows the name of a vendor. (INV-7)
    #
    # `scale` multiplies the reported utilization into a fraction -- 0.01 for a
    # tool reporting `used_percent: 42.1`. `relative` says the reset field is
    # seconds from now rather than a timestamp; without it a countdown of 8100
    # parses as an epoch second in 1970 and every window reads as long expired,
    # which is the failure that looks most like working.
    meter_utilization_scale: float = 1.0
    meter_resets_relative: bool = False
    # Where the tool writes its own log of the call, when the gauge does not
    # reach the output it prints. One tool's `--json` stream carries the answer
    # and the token usage and not the windows -- those go only into its session
    # rollout, a file named after the thread id it did print. A pattern with
    # `{dotted.path}` placeholders filled from the envelope reaches it:
    # `~/.codex/sessions/**/rollout-*-{thread_id}.jsonl`. Empty means the tool
    # says everything it is going to say on stdout.
    meter_sidecar_glob: str = ""
    # Where the tool names a limit it has actually hit, when it does. Not the
    # windows and not the balance: an explicit statement that the last call was
    # refused, and why -- `workspace_member_credits_depleted`. When a tool says
    # that, its windows beside it can be `null`, and a gauge that only reads
    # windows then has nothing new to record: it goes on showing the last
    # numbers it saw, hours old, as the state of a route that is refusing
    # every call. Read from the newest record, so the next call that succeeds
    # clears it.
    meter_limit_key: str = ""
    # Where a record in that log says when it was written. A session log is
    # appended to while the tool runs, so the file's own mtime dates the newest
    # line in it and not the newest *reading* -- which can be hours older in a
    # long session. Empty falls back to the file's mtime, which is right for a
    # log written once.
    meter_observed_key: str = ""
    # How long each window is, where the tool says. A window called `primary`
    # means nothing to a reader; `300` makes it "5h", which is what the tool's
    # own status screen calls it.
    meter_window_minutes_key: str = ""
    # The balance behind the windows, where a tool keeps one. A separate pool,
    # and the reason it is separate is not academic: a run lost its whole review
    # panel to an empty balance while both its windows had room. Read and
    # recorded; nothing acts on it yet. See `meters.read_credits`.
    meter_credits_key: str = ""

    # A filesystem session. Empty means this route cannot author, and a role
    # that needs to write files cannot be pointed at it.
    session_command: list[str] = Field(default_factory=list)
    # What this route's harness reads of a repository's rules on its own, so a
    # session is given the rest by Fabrika rather than by a file committed for
    # one tool. Files it loads from a folder's own copy as well as the root's
    # when `reads_nested`; skills from these folders, the first being where
    # skills kept elsewhere are linked for it. Declared here, where every
    # vendor detail lives: a harness that declares nothing is assumed to read
    # nothing, and is told where every rule is.
    reads_instructions: list[str] = Field(default_factory=list)
    reads_nested: bool = False
    reads_skills: list[str] = Field(default_factory=list)
    # How *this* harness is told where to keep its running cost, if it can be
    # told at all. Per route rather than global: the flag belongs to one CLI,
    # and appending it to another produces a usage error and an exit code that
    # reads as "the harness declined the work". That cost two attempts and a
    # fallback on the first unit ever routed to a second harness.
    session_spend_flag: str = ""

    timeout_s: float = 900.0
    key_env: str = ""
    # How a key for this route is proved without spending a turn: one GET to
    # the provider with the key in this header. Configuration rather than code,
    # because the endpoint that answers "is this key any good" is the vendor's
    # and this package names no vendor. Empty means the route has no such
    # check, and the first real call is the test.
    key_check_url: str = ""
    key_check_header: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    # How a role's `reasoning_effort` reaches this harness, by level. An API
    # route sends it in the request; a command line has its own flag or
    # variable for it, which is configuration, not code. A level missing here
    # is a level this route does not honour, and `effort_for` says so.
    effort: dict[str, RouteEffort] = Field(default_factory=dict)

    def effort_for(self, level: str) -> RouteEffort | None:
        """This route's way of saying `level`, or None when it has none."""
        return self.effort.get((level or "").strip().lower())

    # --- what the console needs to show a human ---------------------------
    # All of it configuration rather than code. A package that knows the name
    # of a vendor's CLI is a package that has to be edited to add the next one,
    # and V-6/INV-7 exist to keep that out of here.
    #
    # Models this route can run, offered in the picker. Not discovered: these
    # CLIs have no list endpoint, and a wrong guess in a dropdown is worse than
    # a short list a human curated.
    models: list[str] = Field(default_factory=list)
    # How to ask whether it is installed at all. Cheap, no network, no tokens.
    version_command: list[str] = Field(default_factory=list)
    # What a human must do, in the order they must do it. `setup_command` is the
    # one line worth a copy button; `setup_steps` is everything around it.
    install_command: str = ""
    setup_command: str = ""
    setup_steps: list[str] = Field(default_factory=list)
    setup_note: str = ""
    docs_url: str = ""

    # --- authoring inside the unit's container -----------------------------
    #
    # A session on the host argues with its own harness: every one of these
    # tools has a safety layer whose job is to stop an unattended agent running
    # commands on somebody's machine, and running the project's commands is
    # exactly the work. Inside the unit's container that argument is settled by
    # the container -- it holds the worktree, the project's dependencies and its
    # database, and nothing else -- so the harness can run in its own
    # no-questions mode.
    #
    # Per route rather than globally, because "this works headless in a
    # container" is a measured property of one tool and not a promise the
    # factory can make on behalf of the next one.
    session_in_container: bool = False
    # The stock image this harness is built in. Its `container_install` runs
    # there, and only what it leaves under `/opt/harness` is carried into the
    # unit's image.
    #
    # Installed in the unit's image itself, every harness would borrow the
    # project's runtime: the Node ones would need the project to have npm, and
    # the Python one a `python3` that can make a venv. A JDK image on Ubuntu
    # has the interpreter and not the venv module, and every agent of a run
    # would fail before it started. A harness that brings its
    # own runtime works in any glibc image and never touches the project's.
    container_build_image: str = ""
    # Shell lines, run as root in `container_build_image`, that put this
    # harness under `/opt/harness`. Anything they write elsewhere stays in the
    # build stage. Configuration, not code: a package that knows how to
    # install a named vendor's CLI is a package that must be edited to add the
    # next one (V-6/INV-7).
    container_install: list[str] = Field(default_factory=list)
    # A command that must succeed in the finished image, so a harness that
    # cannot load there fails the build with the loader's own words rather than
    # the first session with an exit code. Empty runs the session command's
    # program with `--version`.
    container_check: list[str] = Field(default_factory=list)
    # The argv to run in there. Separate from `session_command` because the
    # flags differ -- what is reckless on a host is correct in a container --
    # and because its paths are the container's. Its program is a path under
    # `/opt/harness`, never a name looked up on PATH: the PATH in there is the
    # project's, and the agent's own checks have to find the project's tools.
    container_session_command: list[str] = Field(default_factory=list)
    # Environment for the session process, on top of the container's own.
    # `HOME` belongs here: the container's own points at the mounted worktree,
    # so a harness left to its default would write its config and session logs
    # into the tree the factory collects as the unit's work.
    container_env: dict[str, str] = Field(default_factory=dict)
    # How this harness's credential reaches the container. Nothing is read
    # until a session starts, nothing is written anywhere but a private file
    # the run deletes, and no value is ever logged or put in argv.
    container_credentials: list[ContainerCredential] = Field(default_factory=list)
    # Files baked into the harness layer when it is built: the pinned
    # requirements an install reads from, or the script that *is* the harness.
    # Host path (absolute, or relative to this repository) to the absolute path
    # it lands on, under `/opt/harness`. Their contents are part of the image's
    # cache key, so editing one rebuilds the layer rather than silently running
    # the last build of it.
    container_build_files: dict[str, str] = Field(default_factory=dict)
    # Files this harness needs in there that are not part of the image: for a
    # harness that is a script in this repository rather than an installed
    # program, the script. Host path (absolute, or relative to this
    # repository) to the absolute path it lands on in the container. Copied in
    # before the session and not mounted, because the unit's container is
    # already running by the time a session starts and mounts are fixed when a
    # container is created.
    container_payload: dict[str, str] = Field(default_factory=dict)

    def completes(self) -> bool:
        return self.kind == "api" or bool(self.command)

    def authors(self) -> bool:
        return bool(self.session_command) or bool(self.container_session_command)

    def authors_in_container(self) -> bool:
        """Whether this route's sessions run inside the unit's container."""
        return self.session_in_container and bool(self.container_session_command)

    @property
    def billed(self) -> bool:
        """Whether this route's spend can reach the budget guard at all."""
        return self.metered == "tokens"


class ApiConfig(BaseModel):
    base_url: str = "https://openrouter.ai/api/v1"
    api_key: str = ""
    timeout_s: float = 600.0
    max_transport_retries: int = 4
    backoff_base_s: float = 1.5
    headers: dict[str, str] = Field(default_factory=dict)


class PathsConfig(BaseModel):
    """Where the factory keeps its own things.

    There is deliberately no `repo` here. A repository belongs to a project,
    projects are created at runtime, and a single global repo path would let
    concurrent features corrupt each other.
    """

    evidence: str = ".factory"
    sandboxes: str = ".factory/sandboxes"
    roles: str = ""


class ExecutorConfig(BaseModel):
    kind: str = "direct"
    command: list[str] = Field(default_factory=list)
    timeout_s: float = 1800.0

    # When a `command` harness edits nothing on either attempt, build the unit
    # with the `direct` executor instead of returning an empty result.
    #
    # This is not a fallback in the usual sense -- the harness has not crashed.
    # It has answered, in prose, to nobody. `aider --architect` can do this to
    # several units of one run, in the build lane and in repair rounds alike;
    # one such unit spent 21 minutes asking which revision the new migration
    # should descend from, a question its own brief answered two paragraphs up.
    # Nothing reads the question, and the unit is scored as an attempt.
    #
    # `direct` cannot fail that way. It has no edit syntax to emit and no
    # conversation to lose the thread of: it returns file contents as structured
    # output, and a model that will not answer produces a schema error rather
    # than a polite request for clarification. It is worse at large edits, which
    # is why it is second and not first.
    fallback_to_direct: bool = True
    # A harness drops its own bookkeeping in the tree it works in. Those files
    # are not the unit's work and must not be applied to the sandbox as though
    # they were, so they are filtered out of the changed-file list by prefix.
    #
    # Not `.claude` whole: `.claude/skills/` is where a project keeps its
    # skills, and a unit that edits one has changed this project's rules -- a
    # blocker a person must see, not litter to drop without a word. Only the
    # local settings files a Claude session leaves behind are bookkeeping.
    ignore: list[str] = Field(default_factory=lambda: [
        ".factory-task.md", ".aider", ".claude.json", ".claude/settings.local.json", ".openhands", ".git",
    ])
    # How this harness is told which files to open, if it must be told at all.
    #
    # An agentic harness reads what it needs and these stay empty. A
    # chat-context harness -- aider is the one this has run against -- sees the
    # files it was passed plus a *map* of the rest: signatures, not contents. It
    # cannot open anything else, and in one-shot mode it cannot ask, because
    # asking ends the turn.
    #
    # That is not hypothetical. A unit owning a new Alembic migration spent
    # twenty-one minutes and produced nothing, because it needed to read the
    # revision its migration should descend from and had only the map. It said
    # so -- "add it to the chat so I can see the chain" -- and the process
    # exited zero. Passing the files up front is the only fix; no prompt reaches
    # a capability the harness does not have.
    #
    # Two flags because the distinction matters to the harness: `file_flag`
    # marks what the unit may write, `read_flag` marks context it may only read.
    # Empty disables that half.
    file_flag: str = ""
    read_flag: str = ""

    # Where the harness should keep a running total of what it has spent.
    #
    # A file, because a timeout kills the process before it can print anything,
    # and a unit killed at `timeout_s` has still spent real money that nothing
    # else records. Empty for a harness that
    # does not understand the flag.
    spend_file_flag: str = ""

    # Ceilings on the read-only set, which is computed and could otherwise grow
    # without bound. A directory with more siblings than `max_siblings_per_dir`
    # is skipped whole rather than sampled: an arbitrary twelve of two hundred
    # files is worse than none, because it looks like context and is not.
    max_context_files: int = 24
    max_siblings_per_dir: int = 12

    # The harness needs the same provider key this app already holds. Named
    # here rather than assumed, because every CLI reads a different variable.
    key_env: str = "OPENROUTER_API_KEY"
    # For a harness pointed at a local OpenAI-compatible endpoint: the variable
    # that should carry `api.base_url` into its process.
    base_url_env: str = ""

    # Anything else the harness reads from its environment, as name -> value.
    # `{model}`, `{api_key}` and `{base_url}` are substituted, so a harness
    # configured entirely through the environment -- which is how anything built
    # on LiteLLM works -- needs no code here.
    #
    # `key_env` and `base_url_env` above stay for the single-variable case. This
    # is the general form, and the two are applied in order, so a name set in
    # both ends up with whatever this block says.
    #
    # Secrets belong here rather than in `command`: argv is recorded in the
    # unit's summary and shown in the packet, and the environment is not.
    env: dict[str, str] = Field(default_factory=dict)
    # LiteLLM-style provider prefix put in front of the worker role's model when
    # `{model}` is interpolated. Empty means "look api.base_url up in
    # model_prefixes", so switching provider in one place changes what the
    # harness runs.
    model_prefix: str = ""
    # host -> prefix. Configuration rather than code, so a provider this tool
    # has never heard of is one line in factory.yaml.
    model_prefixes: dict[str, str] = Field(default_factory=dict)


# Gates are project data now, defined in schemas.py alongside the other
# contracts. This alias keeps the older name working.
GateConfig = Gate


class DockerConfig(BaseModel):
    """How containers are run. Per-machine settings; a project chooses only
    whether it uses one at all, and from what image."""

    binary: str = "docker"
    shell: str = "sh"
    memory: str = "4g"
    cpus: float = 2.0
    pids_limit: int = 512
    build_timeout_s: float = 1800.0
    run_as_host_user: bool = True
    # The least free space on Docker's own disk a build starts with, in GB.
    # Under Docker Desktop that disk is a virtual one of fixed size, and it
    # fills without the host noticing: a run on a full one fails with every
    # unit's database exiting as it starts. Three units each holding a
    # database, a dependency install and a harness layer want a few GB.
    min_free_gb: float = 3.0
    # No container reaches this machine, and there is no setting that lets one.
    #
    # There was: `reach_host`, off by default, and off meant pointing
    # `host.docker.internal` at the container's own loopback. That hid the
    # name and nothing else -- from an ordinary feature container,
    # `http://192.168.65.254:8300`, Docker Desktop's address for the host,
    # answered with the developer's own copy of the app. Every container is on
    # an `--internal` network now (see `isolation.py`), which has no route to
    # the host at all, and the name is still pointed away as well.
    #
    # The image the egress proxy runs in: a stock Python, because the proxy is
    # one standard-library script mounted into it. Fabrika adds iptables and
    # iproute2 on top, which a helper uses to give the proxy its stand-in
    # addresses (see `egress.py`); Alpine or Debian both work.
    egress_image: str = "python:3.12-alpine"
    # Every sealed network is a /22 out of this range, laid out so the egress
    # proxy can answer for part of it (see `egress_proxy.py`). The default
    # holds 256 networks. Change it if it overlaps a network this machine
    # already reaches -- a company VPN, say.
    sealed_pool: str = "10.212.0.0/14"
    # What a CLI completion runs in when it is not part of any unit: a plain
    # glibc image, because each harness brings its own runtime under
    # `/opt/harness` (see `container_build_image`). Each call gets a container
    # of its own from it.
    agent_image: str = "debian:bookworm-slim"

    # ---- what a container may do to the machine it runs on ----------------
    #
    # An authoring agent runs in here with its own no-questions mode, because
    # the container is what makes that safe rather than reckless. These are the
    # rest of that argument, and all three were measured with a live agent
    # doing real work: it ran the project's checks, fixed a failing test and
    # noticed nothing.
    #
    # `drop_capabilities` takes every Linux capability away. Nothing a test
    # runner does needs one.
    #
    # `no_new_privileges` stops a setuid binary inside from raising its own
    # privileges -- the container already refuses `su`, and this closes the
    # path that does not go through it.
    #
    # `read_only_root` makes everything except the mounted worktree and the
    # writable paths below immutable. A project whose checks install into the
    # image at run time needs this off; one whose image was prepared for it
    # does not.
    #
    # `writable_paths` is what stays writable under a read-only root, as
    # tmpfs: the agent's home (config, caches, its own session log) and /tmp.
    # Sized, because a tmpfs with no limit is the host's memory.
    drop_capabilities: bool = True
    no_new_privileges: bool = True
    read_only_root: bool = False
    writable_paths: dict[str, str] = Field(default_factory=lambda: {
        "/agent-home": "rw,exec,size=512m",
        "/tmp": "rw,exec,size=512m",
    })
    extra_args: list[str] = Field(default_factory=list)


class PipelineConfig(BaseModel):
    max_parallel_workers: int = 3

    # An authoring agent runs in a sealed container, always, and a build that
    # cannot give it one refuses to start (`require_authoring_environment`).
    # No setting lets the agents run on this machine instead; the retired
    # `require_unit_container` is refused in factory.yaml -- see
    # `RETIRED_SETTINGS`.

    # The scout reads the repository in slices rather than one truncated pass,
    # so a small local model can still see all of it. Each slice is one call.
    scout_slice_chars: int = 120_000
    # Commands that ask the environment what is installed in it, run once at
    # gate 0 in the same container the gates use. Each is optional: a project
    # with no Node gets nothing from the npm probe and that is not a failure.
    #
    # Measured rather than read from a manifest, because a manifest says what
    # someone intended to install. The oracle wrote a conftest importing
    # psycopg2 on the strength of nothing at all, the suite failed to collect,
    # and twelve criteria came back unverified -- a guess from requirements.txt
    # would have been a better guess and still a guess.
    # The npm probe walks to wherever a package.json actually is. A repository
    # with its frontend in a subdirectory -- which is most of them -- returns
    # nothing from `npm ls` at the root, silently, and the whole JavaScript half
    # of the stack goes unreported. `-execdir` runs one `npm ls` per manifest.
    package_probes: list[str] = Field(default_factory=lambda: [
        "python -m pip list --format=freeze",
        "find . -maxdepth 3 -name package.json -not -path '*/node_modules/*' "
        "-execdir npm ls --depth=0 --silent \\; 2>/dev/null || true",
    ])

    # How many times to ask the oracle again when its suite names no criterion
    # in the spec. Zero disables it.
    #
    # `llm.ask` already retries a response that fails the schema, and this
    # failure passes the schema: a list containing a comma is a valid
    # `list[str]`. So it needs its own check, and the check is cheap next to
    # what it prevents -- a run that builds everything, runs everything, and
    # reports every criterion unverified because the tags meant nothing.
    oracle_retries: int = 2
    # How many times PER ROUND the oracle is asked to work on its own files --
    # errors in them (a file that will not load, a helper that breaks the
    # project's linter) and corrections a human made to what one of its tests
    # asserts. Two, because a round has two moments where it can need one: the
    # assessment, and the repair. Per round rather than per feature, because
    # the oracle owns the test tree the way a repairer owns the code, and an
    # allowance that runs out mid-feature turns a route into a dead end --
    # findings arrive at an agent that is no longer being asked anything.
    # The round cap already bounds how many of these a run can buy. Zero
    # leaves everything about its files as findings for a human.
    oracle_revisions: int = 2
    # A check that takes longer than this runs once, in the final pass after
    # the repair loop settles, instead of every round. Measured, not guessed
    # from a name: a browser suite of two tests takes two seconds.
    heavy_check_after_s: float = 120.0
    # The arbiter's equivalent, and it exists for the same reason: a schema
    # cannot tell an answer from a placeholder. `dispositions: []` validates,
    # and `restore_dispositions` then defaults every finding to a human -- which
    # is the right default and is indistinguishable from an arbiter that routed
    # nothing because it never really answered. One returned `{"summary":
    # "Placeholder while I inspect the repo.", "dispositions": []}` and the run
    # recorded 27 escalations that nobody had made.
    arbiter_retries: int = 2

    scout_max_slices: int = 16
    # Concurrent scout calls. Against a local server sharing one GPU, more is
    # not faster -- the requests queue and each one gets slower.
    scout_parallel: int = 2
    # Concurrent reader calls while the as-built reads a repository -- one call
    # per file, so a first reading of a large repository is hundreds of them.
    # The same caution as the scout's applies to a local server.
    as_built_parallel: int = 4
    # The share of a plan's window past which a reading pauses itself, before
    # the plan refuses it: the rest is the room left for the person's own work
    # on the same plan. It stops as it does at the limit -- what it read is
    # kept, nothing is committed, and Read again carries on. 0 turns it off.
    as_built_pause_at: float = 0.85
    # Read the as-built at the commit a feature branches from when it starts,
    # and at its head before review, so the packet can say what the feature
    # changed in the system. Kept in the feature's ledger; never committed, and
    # never on the feature's critical path.
    as_built_on_features: bool = True
    repo_digest_budget: int = 120_000
    apply_writes: bool = True

    # Where the packet is written on the feature's own branch, as Markdown, in
    # its own commit after the work. Empty disables it and the packet stays in
    # the ledger only.
    #
    # The packet exists twice already -- a ledger record and a console screen --
    # and both need this machine. A branch goes to a remote, and the people who
    # rule on it may have the repository and nothing else, so the argument
    # travels with the code or it does not reach them.
    #
    # Per feature rather than one file at the root, because two features open at
    # once would otherwise conflict over it on merge, and a packet that has to
    # be resolved by hand is one nobody reads. `{feature_id}` is the only
    # substitution.
    #
    # Named for the factory rather than for what it holds. `packets/` is a
    # plausible directory in somebody's repository already, and a run that
    # quietly committed into one would be writing into a stranger's filing
    # system; nothing else is called `fabrika/`.
    #
    # A directory per feature rather than a file per feature, because the packet
    # is unlikely to stay the only thing worth carrying on the branch -- the
    # frozen spec and the matrix are the obvious next two. `fabrika/<id>.md` and
    # a later `fabrika/<id>/` cannot both exist, and discovering that after the
    # first repository has a year of them in it is a migration nobody wants.
    packet_file: str = "fabrika/{feature_id}/packet.md"

    # The frozen spec, written beside the packet when a human accepts the work.
    # Empty disables it.
    #
    # At the verdict rather than at the build: until somebody accepts, nobody
    # has agreed that this is what was wanted, and a spec on a branch that was
    # then rejected would be a proposal filed as though it were a decision.
    #
    # A year later this is the file that answers "why is this like this" -- the
    # packet argues that the code matches the spec, and only the spec says what
    # was asked for and what was ruled out.
    spec_file: str = "fabrika/{feature_id}/spec.md"

    # When a file loses more of itself than this, and the lines it lost are not
    # in the other files the feature changed, a blocker is raised naming it.
    #
    # Reported, not refused -- see `check_destructive_writes`. The refusing
    # version was a threshold standing in for a judgement, and it was invisible:
    # refusals went into the `writes` record's `rejected` list, which nothing
    # ever read back, so a packet could describe a build neither the agent nor
    # the human had produced. It also missed the case it was written for, twice
    # over, because it asked whether the batch's line count held up rather than
    # where the lines went -- and new code counts the same as rescued code.
    #
    # These two numbers now decide what is worth a human's attention, not what
    # reaches the branch. Nothing here can silently change the work.
    max_deletion_ratio: float = 0.5
    # Below this, a file is too short for a ratio to mean anything.
    deletion_guard_min_lines: int = 40


class GuidesConfig(BaseModel):
    """The repository's own files that say how its code is written. See
    `factory/guides.py`. There is no list of extra names to treat as guides:
    what binds is what the repository's own files say."""

    #: Characters of guide text one agent is handed, apart from the repository
    #: digest, so a large repository cannot crowd its own guides out. A guide
    #: that does not fit is handed as its headings and its path.
    budget_chars: int = Field(default=30_000, ge=0)
    #: Features that must have run before observed conventions are drafted
    #: into AGENTS.md's code style and testing sections.
    promote_after_features: int = Field(default=3, ge=1)


class DiagnosisConfig(BaseModel):
    """Working out why a check is red on untouched code. See `factory/diagnosis.py`.

    One model call per red check, reused while nothing it read has changed, and
    each fix it proposes tried before anyone is shown it.
    """

    enabled: bool = True
    #: Red checks diagnosed in one run of the checks, most telling first.
    max_checks: int = Field(default=4, ge=0)
    #: Fixes tried per check. A try is a run of the check, bounded by its own limit.
    max_trials: int = Field(default=3, ge=0)
    #: Repository files a diagnosis may ask to read, in its one follow-up round.
    max_files: int = Field(default=10, ge=0)


class ReworkConfig(BaseModel):
    """The bounds on the repair loop.

    Every one of these is read by the orchestrator, never by a model. (INV-6)
    A loop whose trip count a model could influence is a loop a model controls,
    and the whole argument for letting the factory fix its own work is that the
    stopping conditions are a human's, written down, in one place.
    """

    enabled: bool = True

    # Hard ceiling on repair rounds. Two, because needing a third is itself the
    # finding: the loop has stopped fixing the code and started arguing with
    # the reviewer, and that is a human's to read rather than a round's to pay
    # for. It matches `max_attempts_per_finding`, which is 2 -- a finding gets
    # one attempt per round and is then escalated for good, so a third round
    # could only ever work on damage the repairs themselves caused.
    #
    # The default matters as much as the shipped config here: a file that omits
    # the key is asking for whatever is written down, and a default looser than
    # both configs would hand it a looser loop silently.
    max_rounds: int = 2

    # Everything the loop may spend, in the provider's own units, counted from
    # the first gate run onward: the repair rounds plus the review panels they
    # cause to be repeated.
    budget_usd: float = 4.0

    # Held back from `budget_usd` and never spendable by the loop. The failure
    # mode this exists to prevent: the loop eats the money and the run ends with
    # no packet at all, which is strictly worse than not looping. The final
    # review panel and the rapporteur are paid for out of this.
    reserve_usd: float = 0.75

    wall_clock_minutes: float = 90.0

    # Fan-out per round. Repairers run concurrently with non-overlapping file
    # ownership, so this is also the number of worktrees alive at once.
    max_repair_units_per_round: int = 4

    # A finding that survives this many repair attempts is escalated for good.
    # "Attempted twice, still there, here is what was tried" is worth more to a
    # human than a third attempt at the same wrong idea.
    max_attempts_per_finding: int = 2

    # Where the breaker's tests live. Everything under here is refused to a
    # repairer (INV-12) and excluded from the traceability matrix (INV-3).
    breaker_dir: str = "tests/breaker"

    # How the integrator's seam checks are recognised once they are written.
    #
    # Not `.sh` scripts under a directory of this tool's choosing, written, run
    # and deleted every round. Two things are wrong with that, and both can
    # cost a run. A format invented here rather than read off the project lets
    # an agent write `set -o pipefail` into files the harness then runs with
    # `sh` -- dash, on some images -- and every seam gate dies at its own second
    # line having tested nothing, which reaches a human as a BLOCKER about the
    # feature. `TestFileCommand` writes the same lesson down: logic written
    # blind, in a shell that may not be bash, by an agent that cannot run it.
    # And deleting them afterwards throws away the one artefact worth keeping
    # -- a check that shows two units agree is a check the repository wants in
    # its suite next week.
    #
    # So a seam check is an ordinary integration test, written in whatever
    # idiom the gate-0 survey recorded for this project's integration tier, and
    # left in that tier's own directory beside the project's own tests. That
    # directory is shared -- with the project, and with every feature this tool
    # has built before it -- so a seam check cannot be identified by where it
    # is. It is identified by this marker in the file NAME: a convention every
    # runner already collects on, and that no runner needs a special flag to
    # filter. The authoritative list is still the paths this run recorded;
    # the marker is what keeps a human reading the repo later able to see it.
    #
    # Still decided from the files themselves rather than from an agent's
    # account of them, and still refused to a repairer while the run is on,
    # because a check a repair can edit is not one.
    seam_marker: str = "seam"

    # If set, the command that runs the breaker suite. Empty means "use the one
    # the breaker declared", which is what you want on a project whose runner
    # this tool has never seen.
    breaker_command: str = ""
    # How to run ONE breaker probe, with `{path}` substituted. Optional.
    #
    # Same mechanism as the oracle's, and optional for the same reason the
    # breaker is optional evidence: without it the suite runs as one command and
    # a non-zero exit cannot be attributed to an individual probe, which the
    # report then says rather than guessing at.
    breaker_file_command: str = ""
    breaker_timeout_s: float = 900.0

    # How many times a regression probe must pass before it is kept in the
    # project's test suite.
    #
    # One green run is not enough for the probes this actually promotes. They
    # typically attack a race, and a race test can pass by failing to hit the
    # window -- so promoting on a single pass is how a permanent intermittent
    # failure gets installed in somebody's suite by a tool they were not
    # watching. The repeats are nearly free: such probes typically run in a
    # fraction of a second, and one too slow to run five times is telling you
    # something about whether it belongs in every future run.
    #
    # 1 disables the repeat check and promotes on the single run the round
    # already did.
    promote_runs: int = 5

    # How a probe's own budget is derived from what it has already cost.
    #
    # `breaker_timeout_s` stays the ceiling, and it is the right ceiling: a
    # real browser or end-to-end probe needs minutes, and lowering it globally
    # would kill honest suites. What it is wrong for is a probe whose cost is
    # already measured. Two probes that run in 0.26s and 0.16s given 900s each
    # is a factor of about 3,500, so a probe that has stopped terminating gets
    # fifteen minutes to prove it -- every round.
    #
    # Generous, because the number that matters is not "how long should this
    # take" but "how long before it is obviously stuck". A probe with a
    # quarter-second baseline still running after a minute is not slow.
    # A probe that has never finished has no baseline and gets the ceiling,
    # once: the quarantine is what stops it getting the ceiling every round.
    probe_budget_multiple: float = 20.0
    probe_budget_floor_s: float = 60.0

    # Where the oracle's blind tests live, and how they are run.
    #
    # Not wherever the oracle chooses, which in practice is the project's own
    # test directory. Two things follow from that, and either can end a run.
    # The project's test gate collects them, so a blind suite that cannot even
    # reach its fixtures reports its errors (39, in one case) as *the
    # feature's* gate failure; and the project's lint gate lints them, so
    # errors (41) land in files the repair loop is forbidden to touch (INV-12).
    # The loop then stops with "converged: nothing left that a repair could
    # fix", which is true and entirely the harness's own doing.
    #
    # The breaker has the same containment -- its probes go in `breaker_dir`
    # and run under their own command, so its failures can never be mistaken
    # for the project's. The oracle needs it for the same reason.
    oracle_dir: str = "tests/oracle"

    # How the blind suite runs as a whole, how it is loaded without running, and
    # what it can reach when it does are facts about one repository, so they are
    # on the project and not here. See `ProjectState.oracle_command`,
    # `oracle_collect_command` and `oracle_runtime`, and `REPOSITORY_FACTS`
    # below for what happens to a copy of them left in this file.

    # An override for the project's own `test_file_commands`, which the surveyor
    # proposes and a human approves at gate 0. Settable in code only -- the test
    # suite uses it to stand in for a runner -- and refused in factory.yaml,
    # where it would run one repository's command against every project. To
    # change how a project runs one file, change that project's rules.
    oracle_file_command: str = ""
    #: The load-only counterpart, for a project whose survey predates `collect`
    #: on the per-file rules. Same `{path}` template, same override semantics.
    oracle_file_collect_command: str = ""
    #: The reporting counterpart. `{path}` and `{report}`; the report is read as
    #: JUnit XML, which every runner this has met can emit.
    oracle_file_report_command: str = ""
    #: Re-run `environment.test_prepare` between blind files. Off by default
    #: because it costs a migrate and a seed per file; on, it is the only thing
    #: that stops one file's writes deciding another file's verdict. A run lost
    #: AC-25 to exactly that: an earlier file nulled a row, nothing put it back,
    #: and the file that asserted no row was null failed for it.
    oracle_reset_between_files: bool = False
    # Where a test framework leaves a recording of a run is a property of the
    # repository, so it is on the project and not here: this file is one
    # setting for every project the factory manages, and `web/test-results` is
    # a fact about one of them. See `ProjectState.trace_dirs`.
    #
    # What follows is policy, which is this file's business. A recording is the
    # only picture of the change a run keeps. It replaced screenshots, which
    # were one moment a test author chose -- and choosing was got wrong: a
    # picture named for a criterion about a tag appearing before the server
    # responded showed the error message from after it failed. A recording has
    # no moment to choose. At the viewport's resolution it is about 130KB a
    # test; see the project's runner configuration for how it gets there.
    #
    #: Suffixes worth keeping out of those directories. A trace bundle, not
    #: every scratch file a runner leaves beside it -- in particular not the
    #: video a runner may be told to record only for its resolution.
    trace_suffixes: list[str] = Field(default_factory=lambda: [".zip"])
    #: Ceilings, because this is the one output a test controls the size of.
    #: `traces_max` is per round. A round can run 30-odd browser tests, and a
    #: ceiling of 20 leaves the worker's own specs unrecorded. Recordings
    #: measure 100-950KB each, so 60 is about 25MB a round.
    traces_max: int = 60
    traces_max_bytes: int = 25_000_000
    oracle_collect_timeout_s: float = 180.0

    oracle_timeout_s: float = 900.0

    # The verification surface. A repairer that could edit these could make any
    # finding go away without fixing anything, which turns the loop into a
    # machine for manufacturing green. Paths are matched as globs against the
    # repo-relative path, and the oracle's and breaker's own test files are
    # added to this set at run time.
    protected_globs: list[str] = Field(default_factory=lambda: [
        "conftest.py", "**/conftest.py", "pytest.ini", "tox.ini", "setup.cfg",
        "pyproject.toml", ".coveragerc", "jest.config.*", "vitest.config.*",
        ".mocharc.*", "karma.conf.*", "phpunit.xml", "**/pytest.ini",
    ])
    #: How many times a worker is sent back, during its own turn, for lines its
    #: tests never ran, changes its tests do not catch, or tests that cannot
    #: fail. Each send-back is another turn of the same agent, paid for like
    #: the first. Zero turns it off.
    send_backs: int = 2
    #: The most surviving mutants named in one send-back. A worker given two
    #: hundred lines of "this change failed no test" reads none of them.
    max_mutants: int = 50


class SchedulingConfig(BaseModel):
    """Whether a run has room to finish, and what to do when it does not.

    A build that reaches a plan's limit does not stop at a sensible place. It
    stops wherever it happens to be, and every agent after that point never runs
    -- so a run can lose its whole review panel on the last pass and report
    `0 findings still standing`, which reads as a clean bill of health for a
    panel that never answered.

    The alternative to refusing is waiting. A rolling window resets at a known
    second, so "there is not enough left" and "there will be in 31 minutes" are
    the same fact said usefully.
    """

    enabled: bool = True
    #: What a run needs, times this, must fit in what the plan has left. Above 1
    #: because the draw on record is what past runs took and the next one is not
    #: those runs: a repair round nobody planned is exactly the thing that makes
    #: a build cost more than the last one.
    margin: float = 1.25
    #: Past this, a reset is not something to wait for. A five-hour window
    #: resetting in thirty minutes is a schedule; a weekly window resetting on
    #: Sunday is a warning, and offering to hold a build until then would be a
    #: worse answer than letting a human decide to start it anyway.
    max_wait_s: float = 21_600.0     # six hours
    #: How many runs must have been measured before the draw on record is
    #: allowed to hold work back. One observation is an anecdote, and deferring
    #: a build on an anecdote is worse than starting one that might not finish.
    min_observations: int = 2


#: The three levels of thinking, deepest first. A name rather than a model,
#: because a level outlives the model that serves it: `deep` is what the spec
#: writer needs whichever generation is current.
LEVELS: tuple[str, ...] = ("deep", "standard", "light")

#: What a level decides for a role, and therefore what a role can pin.
LEVEL_FIELDS: tuple[str, ...] = ("route", "model", "reasoning_effort")

#: Blue team and red team. An agent that makes something -- a spec, a plan,
#: code, a repair -- builds; one whose output is a verdict on someone else's
#: work checks. Every review agent checks, including one added from the
#: console. The surveyors, the scout, the interrogator, the arbiter and the
#: rapporteur do neither -- they read, ask, route and present -- and are not
#: forced onto a side.
#:
#: Here rather than in the console because it decides where an agent runs: a
#: `staffing:` block gives each side one provider, and a checker on the
#: builder's provider is the blind spot rendered twice.
BUILDS: frozenset[str] = frozenset({
    "spec_writer", "architect", "worker", "integrator", "repairer", "simplifier"})
CHECKS: frozenset[str] = frozenset({"spec_checker", "plan_checker", "oracle", "breaker"})


def team_of(name: str, review: bool = False) -> str:
    """`build`, `check` or `neither`: what this agent does, not where it runs."""
    if name in BUILDS:
        return "build"
    if name in CHECKS or review:
        return "check"
    return "neither"


class LevelChoice(BaseModel):
    """What one level means on one route: a model, and how hard it reasons."""

    model: str = Field(min_length=1)
    reasoning_effort: str = ""


class StaffingConfig(BaseModel):
    """Which route serves each side of the crew.

    One provider per side rather than one per agent, because the sides are the
    thing that has to stay apart: the builders on one vendor and the checkers
    on another is what makes a checker's agreement worth anything.
    """

    build: str = Field(min_length=1, description="The route every builder runs on.")
    check: str = Field(min_length=1, description="The route every checker runs on.")
    #: `build` or `check` to follow that side, or a route of its own.
    neither: str = "build"

    def route_for(self, side: str) -> str:
        if side == "build":
            return self.build
        if side == "check":
            return self.check
        if self.neither == "build":
            return self.build
        if self.neither == "check":
            return self.check
        return self.neither


class FallbackLane(BaseModel):
    """Where one camp of agents goes when its own route will not carry it.

    Named rather than set per role because the camps are the thing that has to
    stay apart: the agents that build, and the agents that check what was
    built. One fallback for everybody is the easy mistake -- it reads as
    prudence and it puts the adversary on the family that wrote the code, on
    the day both routes ran out, which is the day nobody is watching.
    """

    route: str = Field(description="Which route carries this lane.")
    model: str = Field(description="What to ask for there. A model id is a name one vendor knows.")
    note: str = Field(default="", description="Why this lane, for whoever reads the config next.")


class Config(BaseModel):
    api: ApiConfig = Field(default_factory=ApiConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    executor: ExecutorConfig = Field(default_factory=ExecutorConfig)
    docker: DockerConfig = Field(default_factory=DockerConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    rework: ReworkConfig = Field(default_factory=ReworkConfig)
    diagnosis: DiagnosisConfig = Field(default_factory=DiagnosisConfig)
    guides: GuidesConfig = Field(default_factory=GuidesConfig)
    scheduling: SchedulingConfig = Field(default_factory=SchedulingConfig)
    roles: dict[str, RoleConfig] = Field(default_factory=dict)
    fallbacks: dict[str, FallbackLane] = Field(default_factory=dict)
    routes: dict[str, RouteConfig] = Field(default_factory=dict)
    #: Which route serves each side, and what each level means on each route.
    #: Resolved into every role that names a `level` by `load_config`, the way
    #: a fallback lane is, so nothing downstream has to know they exist.
    staffing: StaffingConfig | None = None
    levels: dict[str, dict[str, LevelChoice]] = Field(default_factory=dict)
    source_path: str = ""

    #: The url template the review screen opens a file with, so a reader who
    #: wants the real thing gets it in their own editor rather than in a pane.
    #: `{path}` is the absolute path on this machine and `{line}` the first line
    #: the change touched. Empty hides the link.
    #:
    #: A template rather than a list of known editors, because the list is
    #: wrong the week somebody's is not on it:
    #:   zed://file{path}:{line}
    #:   vscode://file{path}:{line}
    #:   cursor://file{path}:{line}
    #:   idea://open?file={path}&line={line}
    #:   subl://open?url=file://{path}&line={line}
    #:
    #: Only honoured when the console is on the same machine as the factory --
    #: a scheme handler resolves against the browser's filesystem, so from
    #: another machine this link opens the wrong file or none at all. The
    #: console checks and shows the `git diff` command instead. (See
    #: `/api/config`.)
    editor_url: str = "vscode://file{path}:{line}"

    #: The route a role gets when it names none. Synthesised from `api` and
    #: `executor` at load time, so a config written before routes existed keeps
    #: behaving exactly as it did.
    default_route: str = "default"

    def review_roles(self) -> list[RoleConfig]:
        """Every agent that runs in the review phase, in config order."""
        return [r for r in self.roles.values() if r.review and r.enabled]

    def model_post_init(self, __context: Any) -> None:
        """Every `Config` has the default route, however it was built.

        Not only the ones `load_config` made. A `Config(roles={...})` put
        together in a test or a script is a real configuration, and a route
        lookup that worked in production and raised there would be a seam that
        only ever fails where nobody is looking.
        """
        install_default_route(self)

    def route(self, name: str = "") -> RouteConfig:
        """A route by name, or the default. A missing one is an error.

        Never a silent fallback to the default: a role pointed at a route that
        is not configured would then run somewhere the human did not choose,
        which is the same class of failure as a silent model default.
        """
        wanted = (name or self.default_route).strip()
        try:
            return self.routes[wanted]
        except KeyError:
            known = ", ".join(sorted(self.routes)) or "<none>"
            raise ConfigError(
                f"no route named {wanted!r} in {self.source_path or 'config'}. "
                f"Configured routes: {known}."
            ) from None

    def route_for(self, role_name: str) -> RouteConfig:
        """Which route carries one agent's calls."""
        return self.route(self.role(role_name).route)

    def route_problem(self, role_name: str, needs: str = "complete") -> str:
        """Why this role cannot run where it is pointed, or empty.

        Checked up front rather than discovered mid-run. A role on a route that
        cannot do what the role needs fails deep inside a phase, after the money
        for everything before it has been spent -- and the error it fails with
        talks about a subprocess rather than about a configuration nobody could
        have satisfied.
        """
        try:
            route = self.route_for(role_name)
        except ConfigError as exc:
            return str(exc)
        return self.route_problem_for(route, self.role(role_name), role_name, needs)

    def route_problem_for(
        self, route: "RouteConfig", role: "RoleConfig", role_name: str, needs: str = "complete",
    ) -> str:
        """The same question asked of a route this role does not name.

        A fallback has to answer it too, and it has to answer it *before* the
        run leans on it: a substitute that cannot return a schema is not a
        fallback, it is a second failure arriving after the first has already
        cost a phase.
        """
        if not route.enabled:
            return (f"role {role_name!r} runs on route {route.name!r}, which is not connected. "
                    "Connect it, or point the role somewhere else.")
        if needs == "author" and not route.authors():
            return (f"role {role_name!r} writes files, but route {route.name!r} has no "
                    "`session_command` and cannot give it a filesystem.")
        if needs == "complete" and not route.completes():
            return (f"role {role_name!r} needs a structured answer, but route "
                    f"{route.name!r} has no `command` and is not an api route.")
        if needs == "complete" and not route.schema_completions:
            return (f"route {route.name!r} cannot return a schema-shaped answer, and "
                    f"every agent here needs one. It is marked that way because it was "
                    f"measured doing it badly, not guessed at -- see the note on the "
                    f"route in the config. Point {role_name!r} somewhere else.")
        if not self.role(role_name).model.strip() and not route.default_model_ok:
            return (f"role {role_name!r} names no model, and route {route.name!r} cannot "
                    "supply one of its own. Name a model, or point the role at a route "
                    "that picks its own default.")
        return ""

    def role(self, name: str) -> RoleConfig:
        """AC-2.3 -- a missing role is an error, never a silent default model."""
        try:
            return self.roles[name]
        except KeyError:
            known = ", ".join(sorted(self.roles)) or "<none>"
            raise ConfigError(
                f"no model configured for role {name!r} in {self.source_path or 'config'}; "
                f"add a `roles.{name}` block. Configured roles: {known}. "
                "There is deliberately no fallback model."
            ) from None

    @property
    def evidence_path(self) -> Path:
        return Path(self.paths.evidence).expanduser().resolve()

    @property
    def sandbox_path(self) -> Path:
        return Path(self.paths.sandboxes).expanduser().resolve()

    def role_prompt(self, name: str) -> str:
        """INV-10 -- versioned files, loaded at call time, never string literals.

        When output quality shifts, the human needs to diff the prompt to know
        whether the model changed or they did.
        """
        path = prompt_path(self, name)
        if not path.exists():
            raise ConfigError(
                f"no prompt file for role {name!r} at {path}. Prompts are versioned files, "
                "not string literals -- create it rather than inlining the text."
            )
        return path.read_text(encoding="utf-8")

    @property
    def roles_path(self) -> Path:
        if self.paths.roles:
            return Path(self.paths.roles).expanduser().resolve()
        return Path(__file__).parent / "roles"


def install_default_route(cfg: Config) -> Config:
    """Express the `api` and `executor` blocks as the route called `default`.

    A config that names no route anywhere must work unchanged -- so the pair
    of provider blocks that would otherwise be implicit becomes one visible
    entry called `default`. It completes over HTTP
    because that is what `api` is, and it authors through `executor.command`
    because that is what `executor` is. Nothing about a run changes.

    Written rather than assumed: a reader looking at the console should see the
    route their calls are actually taking, including when they have never
    configured one.
    """
    name = cfg.default_route
    if not name:
        return cfg
    synthesised = RouteConfig(
        name=name,
        kind="api",
        # Named for where it actually goes. Every row in the crew table carries
        # this label, and "provider api" on all seventeen of them says nothing;
        # the host is the one word that distinguishes this route from the next
        # one somebody adds.
        label=(urlparse(cfg.api.base_url or "").hostname or "provider api"),
        metered="tokens",
        base_url=cfg.api.base_url,
        api_key=cfg.api.api_key,
        model_prefix=cfg.executor.model_prefix,
        session_command=(list(cfg.executor.command)
                         if cfg.executor.kind == "command" else []),
        timeout_s=cfg.executor.timeout_s,
        key_env=cfg.executor.key_env,
        env=dict(cfg.executor.env),
    )
    written = cfg.routes.get(name)
    if written is None:
        cfg.routes[name] = synthesised
        return cfg
    # A config may also write `default` down itself, to say something about it
    # that `api` and `executor` cannot express -- that its sessions run inside
    # the unit's container, and how its harness gets in there. Then this fills
    # in the rest rather than overruling it: anything the file left empty comes
    # from the blocks that describe this route.
    for field_name, value in synthesised.model_dump().items():
        if field_name == "name":
            continue
        if not getattr(written, field_name, None):
            setattr(written, field_name, value)
    written.name = name
    return cfg


def sync_default_route(cfg: Config) -> Config:
    """Re-copy the api block onto the default route.

    The key is not in the file. `load_config` builds the `Config`, and only then
    reads the credential store -- so the route synthesised during construction
    carries the key as it was before it was loaded, which is to say empty, and
    the console would draw "not signed in" over a provider that works. Anything
    that changes `api` calls this.
    """
    route = cfg.routes.get(cfg.default_route)
    if route is not None and route.kind == "api":
        route.base_url = cfg.api.base_url
        route.api_key = cfg.api.api_key
        route.label = urlparse(cfg.api.base_url or "").hostname or route.label
    return cfg


#: `rework` keys that name one repository's commands or environment, refused in
#: factory.yaml because that file applies to every project.
#:
#: The cost of one living there is not hypothetical. An `oracle_runtime`
#: written for one project -- its `backend/`, its tenant header, its tenant
#: names, and "a FastAPI server is already running at `API_BASE_URL`" -- is
#: told to every other project's oracle as the whole truth about its own
#: environment. An oracle for a project that sets no `API_BASE_URL` believes
#: it and writes a fixture that requires it: every browser test dies in its
#: fixture, round after round, and repair rounds are spent on a variable that
#: belongs to another repository.
#:
#: Refused rather than ignored. Pydantic drops a key it does not know, and a
#: value a human wrote that silently stops applying is the same failure turned
#: inside out.
REPOSITORY_FACTS = frozenset({
    "oracle_command", "oracle_collect_command", "oracle_runtime",
    "oracle_file_command", "oracle_file_collect_command", "oracle_file_report_command",
})


#: Settings that are gone because each one was a way out of isolation. Refused
#: rather than ignored, for the same reason as `REPOSITORY_FACTS`: a value a
#: human wrote that silently stops applying reads as though it still did.
RETIRED_SETTINGS = frozenset({
    ("pipeline", "require_unit_container"),
    ("docker", "reach_host"),
})


def load_config(path: str | Path = "factory.yaml") -> Config:
    """AC-2.1 -- YAML in, `Config` out, with `defaults` merged into every role."""
    p = Path(path).expanduser()
    if not p.exists():
        raise ConfigError(
            f"config file not found: {p}. Copy factory.example.yaml to {p.name} and edit it."
        )
    raw = yaml.safe_load(p.read_text()) or {}
    raw = _expand(raw)

    defaults: dict[str, Any] = raw.get("defaults") or {}
    roles_raw: dict[str, Any] = raw.get("roles") or {}
    if not roles_raw:
        raise ConfigError(f"{p}: no `roles` block. Every role must name a model explicitly.")

    lanes: dict[str, FallbackLane] = {
        name: FallbackLane(**(body or {}))
        for name, body in (raw.get("fallbacks") or {}).items()
    }
    staffing = StaffingConfig(**raw["staffing"]) if raw.get("staffing") else None
    levels = _parse_levels(p, raw.get("levels") or {})
    known_routes = set(raw.get("routes") or {}) | {DEFAULT_ROUTE}
    if staffing is not None:
        named = {"build": staffing.build, "check": staffing.check}
        if staffing.neither not in ("build", "check"):
            named["neither"] = staffing.neither
        missing = sorted(f"staffing.{k}: {v}" for k, v in named.items() if v not in known_routes)
        if missing:
            raise ConfigError(
                f"{p}: {', '.join(missing)} -- no such route. Configured routes: "
                f"{', '.join(sorted(known_routes))}.")

    retired = sorted(f"{block}.{key}" for block, key in RETIRED_SETTINGS
                     if key in (raw.get(block) or {}))
    if retired:
        raise ConfigError(
            f"{p}: {', '.join(retired)} no longer exist(s). Each one let an agent, or "
            "code an agent wrote, reach this machine -- by running it here, or by "
            "leaving a container a route back. Every agent and every check now runs in "
            "a sealed container, with no setting to change that (factory/isolation.py). "
            "Delete the line(s).")

    stray = sorted(REPOSITORY_FACTS & set(raw.get("rework") or {}))
    if stray:
        raise ConfigError(
            f"{p}: {', '.join(f'rework.{k}' for k in stray)} describe(s) one repository, "
            "and this file is one setting for every project the factory manages -- "
            "so whatever is written here is told to, or run for, all of them. They "
            "are set per project now (PATCH /api/projects/<id> with the same key). "
            "Move each value to the project it was written for and delete it here.")

    for old, new in RENAMED_ROLES.items():
        if old in roles_raw:
            raise ConfigError(
                f"{p}: role {old!r} is now called {new!r}. Rename the `roles.{old}` block to "
                f"`roles.{new}`, and its prompt file if you keep your own.")

    roles: dict[str, RoleConfig] = {}
    for name, override in roles_raw.items():
        own = {k: v for k, v in (override or {}).items() if k != "pinned"}
        merged = {**defaults, **own}
        level = str(merged.get("level", "") or "")
        if level and level not in LEVELS:
            raise ConfigError(
                f"{p}: role {name!r} names level {level!r}. Levels: {', '.join(LEVELS)}.")
        side = _side_of(p, name, merged)
        # A level resolves here, once, the way a lane does: under the role's own
        # block, so a model written on the role is a pin and wins, and over
        # `defaults`, so the level's effort is not lost to a file-wide default.
        if level and staffing is not None:
            resolved = _level_fields(p, name, side, level, staffing, levels)
            merged = {**defaults, **resolved, **own}
            merged["pinned"] = [k for k in LEVEL_FIELDS if k in own]
        if "model" not in merged:
            raise ConfigError(
                f"{p}: role {name!r} has no `model` and `defaults` does not supply one."
            )
        # A lane resolves here, once, into the two fields the run reads. A name
        # that matches no lane is a typo that would otherwise present itself as
        # "this role has no fallback" on the one day it mattered.
        lane_name = str(merged.get("fallback", "") or "")
        if lane_name:
            lane = lanes.get(lane_name)
            if lane is None:
                raise ConfigError(
                    f"{p}: role {name!r} falls back to lane {lane_name!r}, which the "
                    f"`fallbacks:` block does not define. Known lanes: "
                    f"{', '.join(sorted(lanes)) or '(none)'}."
                )
            merged.setdefault("fallback_route", lane.route)
            merged.setdefault("fallback_model", lane.model)
        # The name is kept, not consumed. Resolving it into a route and a
        # model is what the RUN needs; the lane is what a PERSON chose, and
        # dropping it left every screen that reads it reporting the opposite
        # of the truth -- "no agent uses this lane" under two lanes that
        # every agent in the crew uses.
        roles[name] = RoleConfig(name=name, **merged)

    routes: dict[str, RouteConfig] = {}
    for name, body in (raw.get("routes") or {}).items():
        routes[name] = RouteConfig(name=name, **(body or {}))

    cfg = Config(
        api=ApiConfig(**(raw.get("api") or {})),
        paths=PathsConfig(**(raw.get("paths") or {})),
        executor=ExecutorConfig(**(raw.get("executor") or {})),
        docker=DockerConfig(**(raw.get("docker") or {})),
        pipeline=PipelineConfig(**(raw.get("pipeline") or {})),
        rework=ReworkConfig(**(raw.get("rework") or {})),
        diagnosis=DiagnosisConfig(**(raw.get("diagnosis") or {})),
        guides=GuidesConfig(**(raw.get("guides") or {})),
        scheduling=SchedulingConfig(**(raw.get("scheduling") or {})),
        roles=roles,
        fallbacks=lanes,
        routes=routes,
        staffing=staffing,
        levels=levels,
        editor_url=raw.get("editor_url", Config.model_fields["editor_url"].default),
        source_path=str(p),
    )
    install_default_route(cfg)
    unknown = sorted({r.route for r in cfg.roles.values() if r.route} - set(cfg.routes))
    if unknown:
        raise ConfigError(
            f"{p}: role(s) point at route(s) that are not configured: {', '.join(unknown)}. "
            f"Configured routes: {', '.join(sorted(cfg.routes))}."
        )
    # An environment variable always wins: a deployment sets one and nothing in
    # the UI should be able to override it. Otherwise fall back to whatever was
    # entered in the console.
    if not cfg.api.api_key:
        cfg.api.api_key = read_credential(cfg, "api_key")
    # The same rule for a command-line route that spends a key of its own: the
    # variable it names wins, and a key entered in the console is stored under
    # that variable's name.
    for route in cfg.routes.values():
        if route.kind == "cli" and route.key_env and not route.api_key:
            route.api_key = read_credential(cfg, route.key_env)
    sync_default_route(cfg)
    return cfg


#: The route a role runs on when it names none. Left unnamed on a role that a
#: level puts there, so the file reads the way it would if a person wrote it.
DEFAULT_ROUTE: str = "default"


def _parse_levels(p: Path, raw: dict[str, Any]) -> dict[str, dict[str, LevelChoice]]:
    out: dict[str, dict[str, LevelChoice]] = {}
    for route, body in raw.items():
        unknown = sorted(set(body or {}) - set(LEVELS))
        if unknown:
            raise ConfigError(
                f"{p}: levels.{route} names {', '.join(unknown)}. Levels: {', '.join(LEVELS)}.")
        out[route] = {lv: LevelChoice(**(choice or {})) for lv, choice in (body or {}).items()}
    return out


def _side_of(p: Path, name: str, merged: dict[str, Any]) -> str:
    """Which side's provider serves this role: its team's, or for a role on
    neither team the side it was moved to."""
    team = team_of(name, bool(merged.get("review")))
    side = str(merged.get("side", "") or "")
    if side and side not in ("build", "check"):
        raise ConfigError(f"{p}: role {name!r} names side {side!r}. Sides: build, check.")
    if side and team != "neither" and side != team:
        raise ConfigError(
            f"{p}: role {name!r} is on the {team} side because of what it does, and cannot "
            f"be moved to {side}. Delete its `side:`.")
    return team if team != "neither" else (side or "neither")


def _level_fields(p: Path, name: str, side: str, level: str,
                  staffing: StaffingConfig, levels: dict[str, dict[str, LevelChoice]]) -> dict[str, Any]:
    route = staffing.route_for(side)
    choice = levels.get(route, {}).get(level)
    if choice is None:
        raise ConfigError(
            f"{p}: role {name!r} is a {level} agent on route {route!r}, and "
            f"`levels.{route}.{level}` is not defined. Say which model that level is there.")
    out: dict[str, Any] = {"model": choice.model}
    if route != DEFAULT_ROUTE:
        out["route"] = route
    if choice.reasoning_effort:
        out["reasoning_effort"] = choice.reasoning_effort
    return out


def example_config_path(config: Config) -> Path | None:
    """The shipped example, which carries the suggested levels and each route's
    preset for them. Beside the live file first, then beside the package."""
    beside = Path(config.source_path or "factory.yaml").expanduser().with_name("factory.example.yaml")
    shipped = Path(__file__).resolve().parent.parent / "factory.example.yaml"
    return next((c for c in (beside, shipped) if c.exists()), None)


def staffing_suggestions(config: Config) -> dict[str, Any]:
    """What the console offers before anything is chosen: each route's preset
    for the three levels, and each role's suggested level.

    Read from the shipped example rather than written in code, because a preset
    names models and this package names none.
    """
    path = example_config_path(config)
    raw = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path else {}
    levels = _parse_levels(path or Path("factory.example.yaml"), raw.get("levels") or {})
    return {
        "levels": {r: {lv: c.model_dump() for lv, c in body.items()} for r, body in levels.items()},
        "roles": {name: (body or {})["level"] for name, body in (raw.get("roles") or {}).items()
                  if (body or {}).get("level") in LEVELS},
    }


# --------------------------------------------------------------------------
# writing config back
#
# The console can edit models and parameters. Two rules make that safe:
# comments survive (they carry the reasoning, and a form that strips them makes
# the config worse every time it is used), and the shipped example is never
# written to.
# --------------------------------------------------------------------------

EDITABLE_ROLE_KEYS = {
    "model", "temperature", "max_tokens", "reasoning_effort",
    "providers", "allow_fallbacks", "review", "samples", "enabled",
    "fallback_route", "fallback_model",
    # Which harness carries this agent. Editable for the same reason the model
    # is: choosing "Opus 5 via Claude Code" over "Claude Opus 5 via OpenRouter"
    # is one choice made in one place, and the two are not the same thing.
    "route",
}


def _trailing_comment(mapping) -> tuple[Any, Any] | None:
    """A comment block that follows the last role in the file is attached to that
    role's last scalar, not to the roles mapping. Appending a new role therefore
    emits it *after* the comment, leaving a role block stranded under an
    explainer about something else. Find that comment so it can be carried."""
    if not isinstance(mapping, CommentedMap) or not len(mapping):
        return None
    last_role = mapping[list(mapping)[-1]]
    if not isinstance(last_role, CommentedMap) or not len(last_role):
        return None
    last_field = list(last_role)[-1]
    entry = last_role.ca.items.get(last_field)
    return (last_role, last_field) if entry else None


def _carry_trailing_comment(mapping, held) -> None:
    """Re-attach the trailing comment to whatever is last now."""
    if held is None:
        return
    owner, field = held
    entry = owner.ca.items.pop(field, None)
    if entry is None or not len(mapping):
        return
    new_last = mapping[list(mapping)[-1]]
    if isinstance(new_last, CommentedMap) and len(new_last):
        new_last.ca.items[list(new_last)[-1]] = entry
    else:
        owner.ca.items[field] = entry


def _round_trip() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    # Block sequences keep the indentation the shipped file uses. Without this
    # ruamel re-emits every list flush against its key, so editing one role
    # rewrites an unrelated list and the diff of a config change stops being
    # readable -- which is the same failure as stripping the comments, in a
    # quieter form.
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def config_target_path(config: Config) -> Path:
    """Where edits go, without making it so: for showing a person the file.

    Never factory.example.yaml: it is documentation that happens to parse, and
    it is checked into the repository. Edits to a config read from it go to
    `factory.yaml` beside it.
    """
    source = Path(config.source_path or "factory.yaml")
    if source.name == "factory.example.yaml":
        return source.with_name("factory.yaml")
    return source


def writable_config_path(config: Config) -> Path:
    """Where edits go, made ready to take one: the first edit promotes the
    example into `factory.yaml`. Only for a write -- a page that merely says
    where the file is uses `config_target_path`, or viewing it would create one."""
    target = config_target_path(config)
    source = Path(config.source_path or "factory.yaml")
    if target != source and not target.exists():
        shutil.copyfile(source, target)
    return target


def _reload_into(config: Config, path: Path) -> Config:
    """Refresh the roles in place, so every holder sees the edit.

    Only the roles. `paths`, `api`, `executor` and `pipeline` are left alone,
    because a caller may have set them at runtime -- a server pointed at a
    different evidence root, a test pointed at a temporary roles directory --
    and silently reverting those to whatever the file says would move the
    ledger out from under a running process. Editing a role is not a reason to
    re-read anything else.
    """
    fresh = load_config(path)
    config.roles = fresh.roles
    # What the roles were resolved from, which a staffing edit changes and a
    # role edit reads.
    config.staffing = fresh.staffing
    config.levels = fresh.levels
    config.source_path = str(path)
    return config


def write_role(config: Config, name: str, changes: dict[str, Any]) -> Config:
    """Set fields on one role, preserving every comment in the file."""
    unknown = set(changes) - EDITABLE_ROLE_KEYS
    if unknown:
        raise ConfigError(f"not editable: {', '.join(sorted(unknown))}")

    path = writable_config_path(config)
    yaml_rt = _round_trip()
    data = yaml_rt.load(path.read_text(encoding="utf-8"))
    roles = data.setdefault("roles", {})
    block = roles.get(name)
    appending = block is None
    held = _trailing_comment(roles) if appending else None
    if appending:
        block = CommentedMap()
        roles[name] = block

    defaults = data.get("defaults") or {}
    current = config.roles.get(name)
    # A role that follows its level is sent back whole by the crew sheet, its
    # level's model included. Writing that down would pin it -- an agent saved
    # for its temperature would stop moving with its level -- so only a value
    # that differs from what the level gives, or one already pinned, is written.
    follows = current is not None and bool(current.level) and config.staffing is not None
    for key, value in changes.items():
        if value is None:
            continue
        # A value identical to the default belongs in `defaults`, not repeated
        # in every role -- but only drop it if the role did not already say it.
        if key in defaults and defaults[key] == value and key not in block:
            continue
        if follows and key in LEVEL_FIELDS and key not in block and getattr(current, key) == value:
            continue
        block[key] = value

    if appending:
        _carry_trailing_comment(roles, held)

    buffer = io.StringIO()
    yaml_rt.dump(data, buffer)
    path.write_text(buffer.getvalue(), encoding="utf-8")
    return _reload_into(config, path)


EFFORT_NAMES: tuple[str, ...] = ("none", "low", "medium", "high")


def _pop_keeping_comment(block: CommentedMap, key: str) -> None:
    """Remove a key, and hand the comment that followed it to the key before.

    A comment between two keys belongs, in the parser's view, to the first of
    them. Dropping the key without this drops the comment with it -- and the
    one under a model line is usually the reason that model was chosen."""
    if key not in block:
        return
    keys = list(block)
    at = keys.index(key)
    entry = block.ca.items.pop(key, None)
    del block[key]
    after = entry[2] if entry and len(entry) > 2 else None
    if after is None or not len(block):
        return
    if at == 0:
        # Nothing before it: the comment now leads into the key that was second.
        slot = block.ca.items.setdefault(list(block)[0], [None, None, None, None])
        if slot[2] is not None:
            after.value = after.value + slot[2].value.lstrip("\n")
        slot[2] = after
        return
    slot = block.ca.items.setdefault(keys[at - 1], [None, None, None, None])
    if slot[2] is None:
        slot[2] = after
    else:
        slot[2].value = slot[2].value + after.value.lstrip("\n")


def _set_before(block: CommentedMap, key: str, value: Any, before: tuple[str, ...]) -> None:
    """Set a key in place, or insert it ahead of the first of `before` present.

    Never after the last key: that one carries the blank line and the comment
    leading into the next role, and a key appended after it lands under them."""
    if key in block:
        block[key] = value
        return
    keys = list(block)
    at = min((keys.index(k) for k in before if k in block), default=len(keys))
    if at == len(keys) and keys and block.ca.items.get(keys[-1]):
        at -= 1
    block.insert(at, key, value)


def _flow(mapping: dict[str, Any]) -> CommentedMap:
    out = CommentedMap(mapping)
    out.fa.set_flow_style()
    return out


STAFFING_COMMENT = """\
Who serves each side of the crew, and what each level of thinking means on
each route. A role that names a `level` takes its route, model and effort from
here; a `route`, `model` or `reasoning_effort` on the role itself is a pin, and
wins. Written by the console's Agent configuration screen."""


def write_staffing(config: Config, staffing: dict[str, str],
                   levels: dict[str, dict[str, dict[str, str]]],
                   roles: dict[str, dict[str, Any]]) -> Config:
    """Set the sides, the levels and every listed role's level in one write.

    One write rather than one per role, because the change is one decision: a
    side moved to another provider moves every agent on it, and a file left
    half-written between two of those would run a crew nobody chose.
    """
    routes = set(config.routes)
    sides = StaffingConfig(**staffing)
    for side, route in (("build", sides.build), ("check", sides.check), ("neither", sides.neither)):
        if side == "neither" and route in ("build", "check"):
            continue
        if route not in routes:
            raise ConfigError(f"staffing.{side}: no route named {route!r}.")
        if not config.routes[route].enabled:
            raise ConfigError(
                f"staffing.{side}: route {route!r} is switched off, and every agent on that "
                "side would fail before its first call. Connect it, or choose another.")
    for route, body in levels.items():
        if route not in routes:
            raise ConfigError(f"levels: no route named {route!r}.")
        for lv, choice in body.items():
            if lv not in LEVELS:
                raise ConfigError(f"levels.{route}: no level {lv!r}. Levels: {', '.join(LEVELS)}.")
            if not str(choice.get("model") or "").strip():
                raise ConfigError(f"levels.{route}.{lv} names no model.")
            effort = str(choice.get("reasoning_effort") or "")
            if effort and effort not in EFFORT_NAMES:
                raise ConfigError(f"levels.{route}.{lv}: no effort {effort!r}.")
    here = Path(config.source_path or "factory.yaml")
    for name, want in roles.items():
        role = config.roles.get(name)
        if role is None:
            raise ConfigError(f"no role {name!r}")
        if want.get("level") not in LEVELS:
            raise ConfigError(f"role {name!r}: no level {want.get('level')!r}.")
        _side_of(here, name, {"review": role.review, "side": want.get("side") or ""})
        pin = want.get("pin")
        if pin:
            if (pin.get("route") or DEFAULT_ROUTE) not in routes:
                raise ConfigError(f"role {name!r} is pinned to route {pin.get('route')!r}, "
                                  "which is not configured.")
            if not str(pin.get("model") or "").strip():
                raise ConfigError(f"role {name!r} is pinned to no model.")
            effort = str(pin.get("reasoning_effort") or "")
            if effort and effort not in EFFORT_NAMES:
                raise ConfigError(f"role {name!r} is pinned to no effort called {effort!r}.")

    path = writable_config_path(config)
    yaml_rt = _round_trip()
    data = yaml_rt.load(path.read_text(encoding="utf-8"))

    # Above `roles:`, because that is what they are about -- and inserted there
    # rather than appended, which would put them after the last block's comments.
    def place(key: str, value: Any, comment: str = "") -> None:
        if key in data:
            data[key] = value
            return
        keys = list(data)
        data.insert(keys.index("roles") if "roles" in keys else len(keys), key, value)
        if comment:
            data.yaml_set_comment_before_after_key(key, before=comment, indent=0)

    place("staffing", CommentedMap([("build", sides.build), ("check", sides.check),
                                    ("neither", sides.neither)]), STAFFING_COMMENT)
    table = data["levels"] if isinstance(data.get("levels"), CommentedMap) else CommentedMap()
    for route, body in levels.items():
        row = table[route] if isinstance(table.get(route), CommentedMap) else CommentedMap()
        for lv in LEVELS:
            if lv not in body:
                continue
            choice = {"model": str(body[lv]["model"]).strip()}
            if body[lv].get("reasoning_effort"):
                choice["reasoning_effort"] = body[lv]["reasoning_effort"]
            row[lv] = _flow(choice)
        table[route] = row
    place("levels", table)

    blocks = data["roles"]
    for name, want in roles.items():
        block = blocks.get(name)
        if not isinstance(block, CommentedMap):
            block = CommentedMap()
            blocks[name] = block
        _set_before(block, "level", want["level"], LEVEL_FIELDS)
        side = want.get("side") or ""
        if side and team_of(name, config.roles[name].review) == "neither":
            _set_before(block, "side", side, LEVEL_FIELDS)
        else:
            _pop_keeping_comment(block, "side")
        pin = want.get("pin")
        if not pin:
            for key in LEVEL_FIELDS:
                _pop_keeping_comment(block, key)
            continue
        route = pin.get("route") or DEFAULT_ROUTE
        if route == DEFAULT_ROUTE:
            _pop_keeping_comment(block, "route")
        else:
            _set_before(block, "route", route, ("model", "reasoning_effort"))
        _set_before(block, "model", str(pin["model"]).strip(), ("reasoning_effort",))
        if pin.get("reasoning_effort"):
            _set_before(block, "reasoning_effort", pin["reasoning_effort"], ())
        else:
            _pop_keeping_comment(block, "reasoning_effort")

    buffer = io.StringIO()
    yaml_rt.dump(data, buffer)
    # Parsed before it replaces the file: a staffing that cannot resolve -- a
    # side moved to a route with no model for one of its levels -- is refused
    # here, and the file stays as it was.
    scratch = path.with_name(f".{path.name}.staffing")
    scratch.write_text(buffer.getvalue(), encoding="utf-8")
    try:
        load_config(scratch)
    except ConfigError as exc:
        scratch.unlink(missing_ok=True)
        raise ConfigError(str(exc).replace(str(scratch), str(path))) from None
    scratch.replace(path)
    return _reload_into(config, path)


def delete_role(config: Config, name: str) -> Config:
    """Only a panel role can be removed. The pipeline calls the others by name,
    and deleting one would fail at the point of use rather than here."""
    role = config.roles.get(name)
    if role is None:
        raise ConfigError(f"no role {name!r}")
    if not role.review:
        raise ConfigError(
            f"{name!r} is a pipeline role, not a review agent. The orchestrator calls it by name; "
            "removing it would fail mid-run instead of here."
        )
    path = writable_config_path(config)
    yaml_rt = _round_trip()
    data = yaml_rt.load(path.read_text(encoding="utf-8"))
    roles = data.get("roles") or {}
    # If the role being removed is the one carrying the file's trailing comment,
    # the comment must move back rather than leave with it.
    held = _trailing_comment(roles) if list(roles)[-1:] == [name] else None
    roles.pop(name, None)
    _carry_trailing_comment(roles, held)
    buffer = io.StringIO()
    yaml_rt.dump(data, buffer)
    path.write_text(buffer.getvalue(), encoding="utf-8")
    return _reload_into(config, path)


def prompt_path(config: Config, name: str) -> Path:
    """The file this role's instructions live in, which is not always its name.

    Resolved in one place because three readers ask the question -- the
    pipeline loading it, the crew screen drawing it, and the editor saving it --
    and a convention each of them applies separately is a convention two of them
    can disagree about. A crew screen that derives the path from the name
    finds nothing for a role whose prompt is another's, and reports an agent
    that runs perfectly well as one that would "fail mid-run".
    """
    role = config.roles.get(name)
    return config.roles_path / f"{(role.prompt if role and role.prompt else name)}.md"


def write_role_prompt(config: Config, name: str, text: str) -> Path:
    """Prompts stay files on disk, so `git diff` still tells you whether the
    model changed or you did. (INV-10)"""
    path = prompt_path(config, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# credentials
#
# Deliberately not in factory.yaml. That file is round-tripped on every role
# edit, shown in the console, and the first thing anyone pastes into a bug
# report. A key belongs in its own file, with its own permissions, that nothing
# else writes.
# --------------------------------------------------------------------------

CREDENTIALS_FILE = ".factory-credentials.json"


def credentials_path(config: Config) -> Path:
    return Path(config.source_path or "factory.yaml").expanduser().resolve().parent / CREDENTIALS_FILE


def harness_model(config: Config, role_name: str = "worker") -> str:
    """The worker's model, named the way a LiteLLM-based harness expects it.

    The role config holds a provider-native id; a harness wants that id prefixed
    by its provider. The prefix is derived from `api.base_url` rather than
    written down a second time, so the model chosen in the agents UI is the
    model the harness runs and the two cannot drift apart.
    """
    model = config.role(role_name).model
    prefix = config.executor.model_prefix
    if not prefix:
        host = urlparse(config.api.base_url or "").hostname or ""
        # The host -> prefix map is configuration, not code: adding a provider
        # should be an edit to factory.yaml, and V-6 keeps vendor names out of
        # the package for the same reason.
        prefix = config.executor.model_prefixes.get(host, "")
    return model if not prefix or model.startswith(prefix) else prefix + model


def session_model(config: Config, role_name: str, route: RouteConfig | None) -> str:
    """The model id to hand a file-writing harness, for the route it runs on.

    `harness_model` derives a LiteLLM provider prefix from `api.base_url`, which
    is right for a harness that spends this app's key and talks to this app's
    provider. It is wrong for a route that brings its own sign-in: prefixed, the
    id names a provider that route has never heard of.

    Measured, on the first unit ever routed to a second harness: launched with
    the provider prefix still attached, the tool answered `unrecognized_model`,
    exited 1 twice, and the unit fell through to the `direct` executor with the
    run none the wiser -- a harness that was working, recorded as one that had
    declined the work.
    """
    if route is None:
        return harness_model(config, role_name)
    model = config.role(role_name).model
    prefix = route.model_prefix
    return model if not prefix or model.startswith(prefix) else prefix + model


def fallback_session_model(config: Config, role_name: str, route: RouteConfig | None) -> str:
    """The model id to hand a harness running on this role's *fallback* route.

    Same question as `session_model` and a different answer, because a fallback
    changes the carrier as well as the model. `session_model` reads
    `role.model`, which names the primary route's vendor and means nothing on
    the substitute; and the substitute here is an `api` route, where the harness
    spends this app's key and LiteLLM wants the provider prefix that
    `harness_model` derives from `api.base_url`.

    Skipping both is what a first attempt did: the raw id went straight through
    and the harness answered "LLM Provider NOT provided" and named the id back
    -- a fallback that fired correctly, chose the right route, recorded why, and
    failed on the one thing neither end had been asked to reconcile.
    """
    role = config.roles.get(role_name)
    model = (getattr(role, "fallback_model", "") or "") if role else ""
    if not model:
        return session_model(config, role_name, route)
    prefix = route.model_prefix if route is not None else ""
    if not prefix and (route is None or route.kind == "api"):
        host = urlparse((route.base_url if route is not None else "")
                        or config.api.base_url or "").hostname or ""
        prefix = config.executor.model_prefixes.get(host, "")
    return model if not prefix or model.startswith(prefix) else prefix + model


#: Agents whose whole worth depends on not sharing the builder's priors, and
#: what they must differ from. Three of these are stated in `factory.yaml`'s own
#: comments as instructions to a human -- "if you change `worker`, check that
#: this is still a different family" -- which is a rule nobody enforces at the
#: moment it is broken. The fourth, the oracle, is not written down anywhere and
#: is the most important of the four: it is the agent whose green is the
#: acceptance signal, and an oracle sharing the worker's blind spots produces a
#: suite that certifies the blind spot.
# Roles that were renamed. A config naming the old one is refused rather than
# quietly aliased: the file is live and the person editing it should see the
# name every screen uses.
RENAMED_ROLES = {"planner": "spec_writer"}


INDEPENDENT_OF: dict[str, tuple[str, ...]] = {
    "reviewer": ("worker",),
    "breaker": ("worker",),
    "oracle": ("worker",),
    "arbiter": ("reviewer",),
    "spec_checker": ("spec_writer",),
    "plan_checker": ("architect",),
}


def model_family(config: Config, role_name: str) -> str:
    """A coarse identity for "would these two agents share a blind spot".

    The route, where a role names one: two agents on the same subscription are
    talking to the same vendor whatever tier each picked, and a tier is not
    independence. Otherwise the model id's first segment, which is how a
    provider-prefixed id names its vendor.

    Coarse on purpose. This is here to catch the case where every role ends up
    on one model -- which is what the whole config was on before today -- not
    to adjudicate how far apart two vendors really are.
    """
    role = config.roles.get(role_name)
    if role is None:
        return ""
    if role.route:
        return f"route:{role.route}"
    model = role.model or ""
    return model.split("/")[0] if "/" in model else model


def landing_family(config: Config, role_name: str) -> str:
    """The family a role runs on when its own route will not carry it.

    The model's vendor, not the route. Two lanes can share a carrier --
    OpenRouter is a shop, not a mind -- and what decides whether two agents
    share a blind spot is whose model answers, which is exactly what
    `model_family` already says for a role that names no route.
    """
    role = config.roles.get(role_name)
    if role is None or not role.fallback_route:
        return ""
    # The same rule `model_family` uses, applied to where this one lands. A
    # named CLI route *is* the family -- one vendor, whatever model you pick off
    # its list -- and only an api route leaves the choice open, because there
    # the carrier is a shop and the model id names the mind.
    try:
        route = config.route(role.fallback_route)
    except ConfigError:
        return f"route:{role.fallback_route}"
    if route.kind != "api":
        return f"route:{role.fallback_route}"
    model = role.fallback_model or ""
    return (model.split("/")[0] if "/" in model else model) or f"route:{route.name}"


def independence_problems(config: Config) -> list[str]:
    """Agents sharing a family with something they exist to disagree with.

    Reported, never fatal. A config that cannot start is worse than one that
    verifies itself weakly, and there are legitimate reasons to run everything
    on one family for a while -- proving the machine works, which is exactly
    what this project spent its first months doing. What must not happen is
    that it becomes true silently.
    """
    out: list[str] = []
    for role_name, others in INDEPENDENT_OF.items():
        if role_name not in config.roles or not config.roles[role_name].enabled:
            continue
        mine = model_family(config, role_name)
        if not mine:
            continue
        for other in others:
            if other not in config.roles or not config.roles[other].enabled:
                continue
            if model_family(config, other) == mine:
                out.append(
                    f"{role_name!r} and {other!r} are both on {mine!r}. "
                    f"{role_name!r} exists to disagree with {other!r}, and agreement "
                    "between models that share priors is not verification -- it is one "
                    "blind spot rendered twice.")

    # And the same question of where each one goes when its route says no.
    #
    # A fallback is chosen at rest and taken at 2am, and the failure it guards
    # against -- a route reaching its ceiling -- is exactly the moment nobody is
    # reading. Three ways it can collapse a disagreement, and all three are
    # invisible until the day they are not.
    for role_name, others in INDEPENDENT_OF.items():
        role = config.roles.get(role_name)
        if role is None or not role.enabled:
            continue
        mine_now, mine_then = model_family(config, role_name), landing_family(config, role_name)
        for other in others:
            mate = config.roles.get(other)
            if mate is None or not mate.enabled:
                continue
            theirs_now = model_family(config, other)
            theirs_then = landing_family(config, other)

            if mine_then and mine_then == theirs_now:
                out.append(
                    f"{role_name!r} falls back onto {mine_then!r}, which is where {other!r} "
                    f"already runs. {role_name!r} exists to disagree with {other!r}, so on the "
                    "day that fallback is taken the disagreement stops being one -- and that is "
                    "the day nobody is watching, because it is the day the first route ran out.")
            elif theirs_then and theirs_then == mine_now:
                out.append(
                    f"{other!r} falls back onto {theirs_then!r}, which is where {role_name!r} "
                    f"already runs. {role_name!r} exists to disagree with {other!r}, and after "
                    "that fallback they are the same reader twice.")
            elif mine_then and mine_then == theirs_then:
                out.append(
                    f"{role_name!r} and {other!r} both fall back onto {mine_then!r}. They "
                    "disagree today because they are on different routes; if both of those run "
                    f"out they are on one, and {role_name!r} exists to disagree with {other!r}. "
                    "A single fallback set in `defaults` is the usual way this happens -- name "
                    "two lanes in `fallbacks:` and put the builders in one and the checkers in "
                    "the other.")
    return out


def read_credential(config: Config, name: str) -> str:
    path = credentials_path(config)
    if not path.exists():
        return ""
    try:
        return str((json.loads(path.read_text(encoding="utf-8")) or {}).get(name, "") or "")
    except (json.JSONDecodeError, OSError):
        return ""


def write_credential(config: Config, name: str, value: str) -> Path:
    """Store or clear one credential. 0600, and never echoed back to a client."""
    path = credentials_path(config)
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8")) or {}
        except (json.JSONDecodeError, OSError):
            data = {}
    if value:
        data[name] = value
    else:
        data.pop(name, None)
    write_atomic(path, json.dumps(data, indent=2) + "\n", mode=0o600)
    return path


def api_key_source(config: Config) -> str:
    """Where the key in force came from. The console shows this so nobody edits
    a stored key that an environment variable is quietly overriding."""
    env_name = _api_key_env_name(config)
    if env_name and os.environ.get(env_name):
        return "environment"
    if read_credential(config, "api_key"):
        return "stored"
    return "none"


def _api_key_env_name(config: Config) -> str:
    """The variable factory.yaml points at, if it points at one."""
    path = Path(config.source_path or "factory.yaml").expanduser()
    if not path.exists():
        return ""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return ""
    declared = str(((raw.get("api") or {}).get("api_key")) or "")
    match = _ENV_PATTERN.fullmatch(declared.strip())
    return (match.group(1) or match.group(2)) if match else ""


def mask_key(key: str) -> str:
    if not key:
        return ""
    return f"...{key[-4:]}" if len(key) > 8 else "set"


def write_api_settings(config: Config, base_url: str | None = None) -> Config:
    """base_url is not a secret and belongs in the config file -- it is the whole
    of INV-7's promise that moving to a local server is one edit."""
    if base_url is None:
        return config
    path = writable_config_path(config)
    yaml_rt = _round_trip()
    data = yaml_rt.load(path.read_text(encoding="utf-8"))
    data.setdefault("api", {})["base_url"] = base_url
    buffer = io.StringIO()
    yaml_rt.dump(data, buffer)
    path.write_text(buffer.getvalue(), encoding="utf-8")
    config.api.base_url = base_url
    config.source_path = str(path)
    return config


def write_editor_url(config: Config, editor_url: str) -> Config:
    """The one line of config that is about the reader, not the pipeline --
    same shape as `write_api_settings`, a top-level scalar rewritten in place."""
    path = writable_config_path(config)
    yaml_rt = _round_trip()
    data = yaml_rt.load(path.read_text(encoding="utf-8"))
    data["editor_url"] = editor_url
    buffer = io.StringIO()
    yaml_rt.dump(data, buffer)
    path.write_text(buffer.getvalue(), encoding="utf-8")
    config.editor_url = editor_url
    config.source_path = str(path)
    return config


def _rewrite(config: Config, change: Callable[[Any], None]) -> Path:
    """Load the writable file round-trip, apply `change`, and write it back
    only if the result still loads -- so a bad edit leaves the file as it was."""
    path = writable_config_path(config)
    yaml_rt = _round_trip()
    data = yaml_rt.load(path.read_text(encoding="utf-8"))
    change(data)
    buffer = io.StringIO()
    yaml_rt.dump(data, buffer)
    scratch = path.with_name(f".{path.name}.edit")
    scratch.write_text(buffer.getvalue(), encoding="utf-8")
    try:
        load_config(scratch)
    except ConfigError:
        scratch.unlink(missing_ok=True)
        raise
    scratch.replace(path)
    config.source_path = str(path)
    return path


def write_rework_limits(config: Config, *, budget_usd: float, max_rounds: int,
                        wall_clock_minutes: float) -> Config:
    """The three numbers that bound one feature's repair loop, set in place.

    Applied to the running config as well as the file: a feature started a
    minute from now should be held to what was just chosen, not to whatever
    the process read when it booted.
    """
    if budget_usd <= 0 or wall_clock_minutes <= 0 or max_rounds < 0:
        raise ValueError("a budget and a time must be above zero, and rounds cannot be negative")

    def change(data):
        block = data.setdefault("rework", {})
        block["budget_usd"] = float(budget_usd)
        block["max_rounds"] = int(max_rounds)
        block["wall_clock_minutes"] = float(wall_clock_minutes)

    _rewrite(config, change)
    config.rework.budget_usd = float(budget_usd)
    config.rework.max_rounds = int(max_rounds)
    config.rework.wall_clock_minutes = float(wall_clock_minutes)
    return config


def write_executor_kind(config: Config, kind: str) -> Config:
    """`direct` or `command`. Read live by every factory built after this, so a
    run started next uses it without a restart."""
    if kind not in ("direct", "command"):
        raise ValueError(f"executor.kind is 'direct' or 'command', not {kind!r}")
    _rewrite(config, lambda data: data.setdefault("executor", {}).__setitem__("kind", kind))
    config.executor.kind = kind
    return config


def write_route_enabled(config: Config, name: str, enabled: bool = True) -> Config:
    """Switch a route on or off, bringing its block over from the shipped
    example when this file predates it.

    A route is a block of argv, install lines and credentials, not a switch;
    a factory.yaml copied before the route existed has nothing to switch on,
    and the example is where its definition lives. The running config takes
    the route as the file now describes it, stored key included.
    """
    example = example_config_path(config)
    shipped = {}
    if example is not None:
        shipped = ((_round_trip().load(example.read_text(encoding="utf-8")) or {})
                   .get("routes") or {})

    def change(data):
        routes = data.setdefault("routes", {})
        if name not in routes:
            if name not in shipped:
                raise ConfigError(f"no route named {name!r} here or in the shipped example.")
            routes[name] = shipped[name]
        routes[name]["enabled"] = bool(enabled)

    path = _rewrite(config, change)
    fresh = load_config(path)
    config.routes[name] = fresh.routes[name]
    return config


def session_fallback(config: "Config", role: str) -> "RouteConfig | None":
    """Where an agent authors when its own route will not carry it.

    `None` unless the role names a fallback that can actually run a session:
    a route with no `session_command` cannot give an agent a filesystem, and
    discovering that after the first one has already failed is one wasted run
    instead of none. One definition, because two things need the answer -- the
    executor that switches to it, and the unit environment that has to have
    its harness installed before the switch -- and the day they disagreed, the
    fallback ran in a container that did not have it.
    """
    cfg_role = config.roles.get(role)
    if cfg_role is None or not getattr(cfg_role, "fallback_route", ""):
        return None
    try:
        route = config.route(cfg_role.fallback_route)
    except Exception:  # noqa: BLE001 -- a misnamed fallback is no fallback
        return None
    return route if route.authors() and route.enabled else None
