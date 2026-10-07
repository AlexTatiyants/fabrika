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
