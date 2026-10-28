"""Asking git a question, the one way this codebase does it.

Nine modules had a wrapper of their own, and they had drifted: some caught a
git that could not start and some let it raise, two had no timeout at all, and
two functions both called `git_available` answered different questions -- one
asked whether a directory is a repository, the other whether it has a commit.
Both questions are real, so both are here, under names that say which.

Every call has a timeout. A git that hangs (a lock held by a dead process, a
credential prompt nobody will answer) must cost a caller seconds, not a run.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Sequence

#: Long enough for `ls-tree -r` on a large repository; short enough that a hung
#: git is noticed.
TIMEOUT = 60


def run(args: Sequence[str], cwd: str | Path, *, timeout: float = TIMEOUT,
        input: bytes | None = None, text: bool = True) -> subprocess.CompletedProcess | None:
    """Run `git args` in `cwd`. None when git could not start or ran out of time;
    a git that ran and failed comes back with its returncode for the caller to read."""
    try:
        return subprocess.run(["git", *args], cwd=Path(cwd).expanduser(), capture_output=True,
                              text=text, input=input, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def out(args: Sequence[str], cwd: str | Path, *, timeout: float = TIMEOUT) -> str | None:
    """What `git args` printed, or None if it did not succeed."""
    done = run(args, cwd, timeout=timeout)
    return done.stdout if done is not None and done.returncode == 0 else None


def head_sha(repo: str | Path, ref: str = "HEAD") -> str:
    """The commit `ref` names, or "" when there is none."""
    return (out(["rev-parse", ref], Path(repo).expanduser().resolve(), timeout=30) or "").strip()


def current_branch(repo: str | Path) -> str:
    """The checked-out branch's name, or "" when git cannot say."""
    return (out(["rev-parse", "--abbrev-ref", "HEAD"], repo, timeout=20) or "").strip()


def is_repo(path: str | Path) -> bool:
    """Whether `path` is inside a git repository, commits or not."""
    return out(["rev-parse", "--git-dir"], path, timeout=30) is not None


def has_commit(path: str | Path) -> bool:
    """Whether `path` is a repository with a commit at HEAD -- what a worktree
    or a branch needs to be made from."""
    return shutil.which("git") is not None and out(["rev-parse", "--verify", "HEAD"], path,
                                                   timeout=20) is not None


def merge_base(repo: str | Path, a: str, b: str) -> str:
    """The commit `a` and `b` last shared, or "" when they share none.

    What outside work is measured against. Not the tip of `main`: main
    moves after a branch is cut, and a diff against where it is now would
    credit the branch with undoing everything main did since.
    """
    return (out(["merge-base", a, b], Path(repo).expanduser().resolve(), timeout=30) or "").strip()


def create_branch(repo: str | Path, name: str, sha: str, *, replace: str = "") -> str:
    """Point the branch `name` at `sha`, making it if it is not there. "" when
    done, else why not.

    A branch that is already there is moved only from `replace`, the commit
    the caller made it at last time, and only while nobody has it checked out:
    one somebody committed to since, or is working on, is theirs now. Nothing
    else in the repository changes -- not the checkout, not its HEAD, not any
    other ref.
    """
    root = Path(repo).expanduser().resolve()
    ref = f"refs/heads/{name}"
    current = (out(["rev-parse", "--verify", "-q", ref], root, timeout=30) or "").strip()
    if current == sha:
        return ""
    if current and current != replace:
        return (f"{name} is there already, at {current[:12]}, and Fabrika did not leave it there: "
                "someone has committed to it. It was not moved.")
    if current:
        checked = out(["worktree", "list", "--porcelain"], root, timeout=30) or ""
        if f"branch {ref}" in checked.splitlines():
            return f"{name} is checked out, so it was not moved under whoever is working on it."
    done = run(["update-ref", "-m", "fabrika: tidy branch", ref, sha, current or "0" * 40], root, timeout=30)
    if done is None or done.returncode != 0:
        return f"could not point {name} at {sha[:12]}: {(done.stderr if done else '').strip()[:200]}"
    return ""

