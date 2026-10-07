#!/usr/bin/env python
"""Drive OpenHands as this project's coding harness.

Run by `CommandExecutor` like any other CLI, but it is not one: OpenHands 1.x
ships an SDK and a server, not a task-taking command. This file is the adapter,
and it lives here rather than in `factory/` because it must import a package the
factory's own environment does not have. It runs under its own interpreter
(`.venv-openhands`), so nothing it depends on can move the factory's pins.

The reason for going to this trouble is one line, near the bottom: `while
not wrote_anything()`. Every CLI harness this project has used is a subprocess,
and a subprocess that exits has already ended its turn -- a model that answers
with a question cannot be told to keep going, only started again from nothing.
Here the loop belongs to us. "You have not edited a file" is a message sent into
the *same* conversation, with everything the agent has already read still in
front of it, and the turn does not end until the disk changes or a ceiling is
hit. That is the difference between discouraging a question and not accepting
one.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")

# OpenHands is imported inside `main`, not here. Everything above it in this
# file is git and pathlib, and keeping the import lazy is what lets the
# factory's own test suite exercise those parts -- they run under a different
# interpreter that has never heard of OpenHands, and an untested commit guard
# is exactly the kind of thing that is fine until the day it is not.

# What the agent is told when it stops without having written anything. Stated
# as a fact about the run rather than as encouragement: the task is unchanged
# and repeating it in other words would give the agent two briefs to reconcile.
#: How the harness tells the factory what it spent. Parsed by `CommandExecutor`;
#: a line rather than a file so it survives whatever else the runner prints.
SPEND_MARKER = "FACTORY-HARNESS-SPEND "

NUDGE = """\
You have not changed any file. Nothing you have written so far exists outside \
this conversation, and this conversation is read by nobody.

There is no person here to answer a question, approve a plan, or choose between \
options you have laid out. The only thing that leaves this session is the \
difference you make on disk.

If you were about to ask something: answer it yourself, the most reasonable way \
you can, and write a short comment at the line saying what you assumed. If you \
were describing what you would do: do it now. If something is genuinely \
impossible, implement everything that is possible and leave a comment saying \
exactly what stopped you.

Edit the files and save them."""


def head(tree: Path) -> str:
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tree,
                       capture_output=True, text=True, timeout=60)
    return r.stdout.strip()


def uncommit_to(tree: Path, base: str) -> bool:
    """Put any commits the agent made back into the working tree.

    This agent has a terminal, so it can commit, and one that does leaves a
    clean `git status` -- which every layer above reads as "the harness produced
    nothing". The unit would then be nudged for work it had already done, twice,
    and recorded as empty.

    aider was told not to do this with a flag (`--no-auto-commits`, which
    `factory.yaml` calls not optional). There is no flag here, and asking an
    agent nicely is not a mechanism, so the commits are simply undone: a soft
    reset leaves every change exactly where it was, staged, and the factory
    reads it the way it reads any other edit.
    """
    if not base or head(tree) == base:
        return False
    subprocess.run(["git", "reset", "--soft", base], cwd=tree,
                   capture_output=True, text=True, timeout=60)
    return True


def changed_files(tree: Path, ignore: set[str]) -> list[str]:
    """What is on disk that was not there before, excluding bookkeeping.

    Read from git rather than from the agent's own account of itself, for the
    reason everything else in this system is: an agent's report of what it did
    is a claim, and `git status` is a fact.
    """
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tree,
        capture_output=True, text=True, timeout=60,
    )
    out: list[str] = []
    for line in result.stdout.splitlines():
        path = line[3:].strip().strip('"')
        if not path:
            continue
        head = path.split("/", 1)[0]
        if head in ignore:
            continue
        # An empty file is not work. Handing a harness a path that does not
        # exist yet makes some of them touch it into existence before saying
        # anything, and counting that as an edit is how a unit gets one attempt
        # where it should have had two.
        target = tree / path
        if target.is_file() and target.stat().st_size == 0:
            continue
        out.append(path)
    return out


#: Where a project may keep skills that the SDK's project loading does not
#: read. `.agents/skills/` it reads itself.
EXTRA_SKILL_DIRS = (".claude/skills",)


def skill_dirs(tree: Path) -> list[Path]:
    """The skills folders in this checkout the SDK would not load on its own."""
    return [tree / rel for rel in EXTRA_SKILL_DIRS if (tree / rel).is_dir()]


def project_skills(tree: Path) -> list:
    """Every skill in those folders, loaded by the SDK's own loader.

    A skill that fails to load is the SDK's to warn about; one bad file does
    not cost the agent the rest, and none costs it the run.
    """
    if not skill_dirs(tree):
        return []
    from openhands.sdk.skills import load_skills_from_dir
    out: list = []
    for folder in skill_dirs(tree):
        try:
            for group in load_skills_from_dir(folder):
                out.extend(group.values())
        except Exception as exc:
            print(f"--- skills in {folder.name} not loaded: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
    return out


def record_spend(conversation, args) -> dict:
    """What has been spent so far, written where a kill cannot take it.

    Printed at the end *and* written to a file after every turn, because the end
    is exactly what a timeout removes. A unit killed at the executor's
    1800-second ceiling has spent real money, the marker is emitted after the
    last turn returns, and the unit never reaches it -- so with the marker
    alone, its spend is invisible.

    stdout does not survive either -- a killed process's buffer goes with it --
    so the file is the only channel that outlives the kill.
    """
    spend = {"cost_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0,
             "model": args.model}
    try:
        metrics = conversation.conversation_stats.get_combined_metrics()
        spend["cost_usd"] = float(getattr(metrics, "accumulated_cost", 0.0) or 0.0)
        tokens = getattr(metrics, "accumulated_token_usage", None)
        if tokens is not None:
            spend["prompt_tokens"] = int(getattr(tokens, "prompt_tokens", 0) or 0)
            spend["completion_tokens"] = int(getattr(tokens, "completion_tokens", 0) or 0)
    except Exception as exc:  # never let accounting take the unit down
        spend["error"] = f"{type(exc).__name__}: {exc}"
    if args.spend_file:
        try:
            Path(args.spend_file).write_text(json.dumps(spend), encoding="utf-8")
        except OSError:
            pass
    return spend


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True, help="file holding the unit brief")
    ap.add_argument("--worktree", required=True, help="checkout to work in")
    ap.add_argument("--model", required=True, help="LiteLLM-style model id")
    ap.add_argument("--max-nudges", type=int, default=2,
                    help="times to refuse a turn that wrote nothing")
    ap.add_argument("--spend-file", default="",
                    help="file to keep the running cost in, updated as work proceeds")
    args = ap.parse_args()

    from openhands.sdk import LLM, AgentContext, Conversation
    from openhands.tools.preset import get_default_agent

    tree = Path(args.worktree).resolve()
    task = Path(args.task).read_text(encoding="utf-8")
    ignore = {".git", ".openhands", ".factory-task.md"}

    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or ""
    if not api_key:
        print("no provider key in LLM_API_KEY or OPENROUTER_API_KEY", file=sys.stderr)
        return 2

    llm = LLM(
        model=args.model,
        api_key=api_key,
        base_url=os.environ.get("LLM_BASE_URL") or None,
        usage_id="worker",
    )
    agent = get_default_agent(llm=llm, cli_mode=True)
    # The repository's own rules, read the way OpenHands reads them: the SDK's
    # project loading takes the root AGENTS.md and CLAUDE.md and the skills in
    # `.agents/skills/`. A project that keeps its skills in `.claude/skills/`
    # is read too, through the SDK's own loader and in memory -- nothing is
    # copied into the checkout, so the repository holds only its own files and
    # every tool that writes code here follows the same ones. On for every run,
    # whichever route or fallback started it. A user's or the public skills are
    # nobody's rule for this project, and stay off.
    agent = agent.model_copy(update={"agent_context": AgentContext(
        load_project_skills=True, load_user_skills=False, load_public_skills=False,
        skills=project_skills(tree))})

    # Persistence outside the checkout. Anything written inside it shows up in
    # `git status`, and the factory reads that to decide what the unit built.
    # One conversation per checkout, by an id derived from it. A unit sent
    # back for what its tests left unproved is this same script run again in
    # the same checkout: with the same id and the same persistence, the SDK
    # resumes the conversation, so the agent reads the send-back with
    # everything it already knows about the code it wrote.
    conversation = Conversation(
        agent,
        workspace=str(tree),
        persistence_dir=str(Path(os.environ.get("TMPDIR", "/tmp")) / "openhands-state"),
        conversation_id=uuid.uuid5(uuid.NAMESPACE_URL, f"fabrika-unit:{tree}"),
    )

    base = head(tree)
    conversation.send_message(task)
    conversation.run()
    record_spend(conversation, args)
    if uncommit_to(tree, base):
        print("\n--- the agent committed; commits rolled back into the working tree ---")

    nudges = 0
    while not changed_files(tree, ignore) and nudges < args.max_nudges:
        nudges += 1
        print(f"\n--- nothing written; refusing the turn ({nudges}/{args.max_nudges}) ---\n")
        conversation.send_message(NUDGE)
        conversation.run()
        record_spend(conversation, args)
        uncommit_to(tree, base)

    written = changed_files(tree, ignore)
    status = getattr(getattr(conversation, "state", None), "execution_status", None)

    # What this cost, on a line the factory can parse. Without it the harness is
    # a second process spending real money on the same key that no ledger sees:
    # a run once reported $1.20 while the provider billed $4.44, and the repair
    # budget -- whose whole job is to stop a run outspending what a human agreed
    # to -- was blind to the largest consumer in the system.
    print(SPEND_MARKER + json.dumps(record_spend(conversation, args)))

    print(f"\n--- openhands finished: status={status} nudges={nudges} "
          f"files={len(written)} ---")
    for path in written:
        print(f"    {path}")
    try:
        conversation.close()
    except Exception:
        pass
    # Always zero. The factory decides what happened by reading the diff, not
    # the exit code, and a non-zero here would only add a second story.
    return 0


if __name__ == "__main__":
    sys.exit(main())
