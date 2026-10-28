"""Opening a pull request for what was underwritten.

A feature underwritten at gate 2, or a look at outside work underwritten, ends as
a pull request where the project says pull requests go (`pr_remote`,
`pr_base`, `pr_repo` on the project). Fabrika pushes exactly the commit that
was underwritten -- never with force, so a branch that moved on the remote is
a question for a person, not an overwrite -- and opens the PR with `gh`, or
updates the body of the one already open for that branch.

Every outside command goes through `_run`, so a test can stand in for `gh`
without a network.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from . import git
from .pipeline.text import _now

#: How long a push or a `gh` call may take before it is given up on.
TIMEOUT_S = 120.0

_GITHUB = re.compile(r"github\.com[:/](?P<repo>[\w.-]+/[\w.-]+?)(?:\.git)?/?$")


@dataclass
class Target:
    remote: str
    base: str
    repo: str


async def _run(argv: list[str], cwd: str, input: str | None = None) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, stdin=asyncio.subprocess.PIPE if input is not None else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except OSError as exc:
        return 127, str(exc)
    try:
        out, _ = await asyncio.wait_for(
            proc.communicate(input.encode("utf-8") if input is not None else None), timeout=TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        return -9, f"no answer within {TIMEOUT_S:g}s"
    return proc.returncode or 0, (out or b"").decode("utf-8", errors="replace").strip()


def pr_target(project) -> Target | str:
    """Where this project's pull requests go, or why there is nowhere."""
    state = project.state
    remote = (getattr(state, "pr_remote", "") or "").strip()
    if not remote:
        return "This project has nowhere for pull requests yet: set the remote they go to in its settings."
    url = (git.out(["remote", "get-url", remote], project.repo_path) or "").strip()
    if not url:
        return f"The remote {remote!r} is not in this repository any more."
    repo = (getattr(state, "pr_repo", "") or "").strip()
    if not repo:
        found = _GITHUB.search(url)
        if not found:
            return (f"{remote!r} is not on GitHub ({url}), so Fabrika cannot tell which repository to open "
                    "the pull request in: set it as owner/name in the project's settings.")
        repo = found.group("repo")
    base = (getattr(state, "pr_base", "") or "").strip() or _branch_name(project.base_ref)
    if not base:
        return "This project's base is not a branch, so a pull request has nothing to go into: set its base."
    return Target(remote=remote, base=base, repo=repo)


def _branch_name(ref: str) -> str:
    ref = (ref or "").strip()
    if not ref or ref == "HEAD" or re.fullmatch(r"[0-9a-f]{7,40}", ref):
        return ""
    return ref.removeprefix("refs/heads/")


async def open_pr(project, *, branch: str, sha: str, title: str, body: str) -> dict[str, Any]:
    """Push `sha` as `branch` and open its pull request, or update the one
    already open for it. Raises `PullRequestError` with what a person needs to
    know when it cannot."""
    target = pr_target(project)
    if isinstance(target, str):
        raise PullRequestError(target)
    cwd = str(project.repo_path)
    code, said = await _run(["git", "push", target.remote, f"{sha}:refs/heads/{branch}"], cwd)
    if code != 0:
        if "rejected" in said or "non-fast-forward" in said or "fetch first" in said:
            raise PullRequestError(
                f"{branch} on {target.remote} has moved past what was underwritten, so it was not "
                "overwritten. Look at it again.")
        raise PullRequestError(f"Could not push {branch} to {target.remote}: {said[-400:]}")

    code, said = await _run(["gh", "pr", "list", "--repo", target.repo, "--head", branch,
                             "--state", "open", "--json", "url", "--jq", ".[0].url"], cwd)
    if code != 0:
        raise PullRequestError(f"gh could not list the pull requests on {target.repo}: {said[-400:]}")
    url = said.strip()
    if url:
        code, said = await _run(["gh", "pr", "edit", url, "--body-file", "-"], cwd, input=body)
        if code != 0:
            raise PullRequestError(f"gh could not update {url}: {said[-400:]}")
        opened = False
    else:
        code, said = await _run(["gh", "pr", "create", "--repo", target.repo, "--base", target.base,
                                 "--head", branch, "--title", title, "--body-file", "-"], cwd, input=body)
        if code != 0:
            raise PullRequestError(f"gh could not open the pull request: {said[-400:]}")
        url = said.strip().splitlines()[-1] if said.strip() else ""
        opened = True
    return {"url": url, "branch": branch, "base": target.base, "repo": target.repo, "sha": sha,
            "opened": opened, "at": _now()}


class PullRequestError(Exception):
    """Why a pull request could not be opened, said for a person."""
