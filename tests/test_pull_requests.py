"""Underwritten ends as a pull request.

A feature underwritten at gate 2 and a look at outside work underwritten are both
pushed -- exactly the commit that was underwritten, never with force -- and
opened as a pull request where the project says they go. Git is real here,
against a bare repository standing in for the remote; `gh` is stood in for,
so nothing reaches a network.
"""

import asyncio

import pytest

from helpers import *  # noqa: F403
from test_branch import _brought, _found, _git, _look

from factory import pull_requests
from factory.pipeline.branch import BranchReview
from factory.projects import ProjectRegistry


def _remote(tmp_path, repo, *, url=None):
    """A bare repository as `origin`, holding main."""
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))
    _git(repo, "remote", "add", "origin", url or str(bare))
    if url:
        _git(repo, "remote", "set-url", "--push", "origin", str(bare))
    _git(repo, "push", "-q", str(bare), "main")
    return bare


def _gh(monkeypatch, *, open_url=""):
    """`gh`, answering from a table; git still runs for real."""
    asked: list[tuple[list[str], str | None]] = []
    real = pull_requests._run

    async def run(argv, cwd, input=None):
        if argv[0] != "gh":
            return await real(argv, cwd, input)
        asked.append((argv, input))
        if argv[1:3] == ["pr", "list"]:
            return 0, open_url
        if argv[1:3] == ["pr", "create"]:
            return 0, "https://github.com/me/demo/pull/7"
        return 0, ""

    monkeypatch.setattr(pull_requests, "_run", run)
    return asked


def _set(config, project, **changes):
    registry = ProjectRegistry(config.evidence_path)
    return registry.update(registry.get(project.id), changes)


def test_a_project_with_nowhere_for_pull_requests_says_what_to_set(tmp_path):
    """Nothing is pushed anywhere a person did not name, and each way of
    having nowhere says what to set."""
    config, project, repo = _brought(tmp_path)
    assert "nowhere for pull requests" in pull_requests.pr_target(project)
    _remote(tmp_path, repo)
    project = _set(config, project, pr_remote="origin")
    assert "not on GitHub" in pull_requests.pr_target(project), \
        "a remote whose address names no GitHub repository was taken for one"
    project = _set(config, project, pr_repo="me/demo")
    target = pull_requests.pr_target(project)
    assert (target.remote, target.base, target.repo) == ("origin", "main", "me/demo")
    _git(repo, "remote", "remove", "origin")
    assert "not in this repository any more" in pull_requests.pr_target(project)
    with pytest.raises(Exception, match="no remote called"):
        _set(config, project, pr_remote="upstream")


def test_the_settings_screen_saves_where_pull_requests_go(tmp_path):
    """The console's settings editor sends the three fields to the project's
    PATCH; the route once dropped them, so saving them changed nothing."""
    from fastapi.testclient import TestClient

    from factory.server import create_app

    config, project, repo = _brought(tmp_path)
    _remote(tmp_path, repo, url="https://github.com/me/demo.git")
    client = TestClient(create_app(config))
    said = client.patch(f"/api/projects/{project.id}", json={"pr_remote": "origin", "pr_base": "main"})
    assert said.status_code == 200, said.text
    assert (said.json()["pr_remote"], said.json()["pr_base"]) == ("origin", "main")
    target = pull_requests.pr_target(ProjectRegistry(config.evidence_path).get(project.id))
    assert (target.remote, target.base, target.repo) == ("origin", "main", "me/demo")
    refused = client.patch(f"/api/projects/{project.id}", json={"pr_remote": "upstream"})
    assert refused.status_code == 422 and "no remote called" in refused.text

def test_the_repository_is_read_from_a_github_address():
    for url in ("https://github.com/me/demo.git", "git@github.com:me/demo.git", "https://github.com/me/demo"):
        assert pull_requests._GITHUB.search(url).group("repo") == "me/demo"


def test_opening_a_pr_pushes_exactly_the_underwritten_commit_and_never_forces(tmp_path, monkeypatch):
    config, project, repo = _brought(tmp_path)
    bare = _remote(tmp_path, repo)
    project = _set(config, project, pr_remote="origin", pr_repo="me/demo")
    asked = _gh(monkeypatch)
    sha = _git(repo, "rev-parse", "explore")
    opened = asyncio.run(pull_requests.open_pr(project, branch="explore", sha=sha, title="Widgets", body="The body."))
    assert _git(bare, "rev-parse", "refs/heads/explore") == sha
    assert opened["url"] == "https://github.com/me/demo/pull/7" and opened["opened"]
    create = next(argv for argv, _ in asked if argv[1:3] == ["pr", "create"])
    assert create[create.index("--base") + 1] == "main" and create[create.index("--head") + 1] == "explore"
    assert next(text for argv, text in asked if argv[1:3] == ["pr", "create"]) == "The body."

    # Someone pushed to it since: the remote is not overwritten.
    tree = _git(bare, "rev-parse", "main^{tree}")
    theirs = _git(bare, "commit-tree", tree, "-p", _git(bare, "rev-parse", "main"), "-m", "theirs")
    _git(bare, "update-ref", "refs/heads/explore", theirs)
    with pytest.raises(pull_requests.PullRequestError, match="moved past what was underwritten"):
        asyncio.run(pull_requests.open_pr(project, branch="explore", sha=sha, title="Widgets", body="b"))
    assert _git(bare, "rev-parse", "refs/heads/explore") == theirs


def test_a_branch_with_an_open_pr_gets_its_body_updated_not_a_second_pr(tmp_path, monkeypatch):
    config, project, repo = _brought(tmp_path)
    _remote(tmp_path, repo)
    project = _set(config, project, pr_remote="origin", pr_repo="me/demo")
    asked = _gh(monkeypatch, open_url="https://github.com/me/demo/pull/3")
    sha = _git(repo, "rev-parse", "explore")
    opened = asyncio.run(pull_requests.open_pr(project, branch="explore", sha=sha, title="t", body="New body."))
    assert opened["url"] == "https://github.com/me/demo/pull/3" and not opened["opened"]
    assert not any(argv[1:3] == ["pr", "create"] for argv, _ in asked), "a second pull request was opened"
    assert ("New body." in [text for argv, text in asked if argv[1:3] == ["pr", "edit"]])


def test_underwriting_a_look_opens_a_pr_from_the_person_s_branch_with_the_risks_they_accepted(
        tmp_path, monkeypatch):
    """The person's own branch, at the commit they underwrote, into the base;
    the body is the record, and every risk they accepted is in it with their
    reason. Their checkout does not move."""
    config, project, repo = _brought(tmp_path)
    bare = _remote(tmp_path, repo)
    project = _set(config, project, pr_remote="origin", pr_repo="me/demo")
    asked = _gh(monkeypatch)
    review, store, _packet = _look(config, project)
    store.append("look_findings", {**_found(), "look": 1}, role="orchestrator")
    review.rule("explore", "breaker-limit", "accept", "Five is a soft limit for now.")
    review.rule("explore", "reviewer-2", "accept")
    head_before = _git(repo, "rev-parse", "HEAD")
    review.verdict("explore", "underwritten")
    opened = asyncio.run(review.open_pull_request("explore"))
    assert opened["branch"] == "explore" and _git(bare, "rev-parse", "refs/heads/explore") == opened["sha"]
    body = next(text for argv, text in asked if argv[1:3] == ["pr", "create"])
    assert "A sixth widget is let in" in body and "Five is a soft limit for now." in body
    assert _git(repo, "rev-parse", "HEAD") == head_before
    assert store.payload("pull_request")["url"] == "https://github.com/me/demo/pull/7"


def test_a_pr_that_could_not_be_opened_is_recorded_and_said(tmp_path, monkeypatch):
    config, project, repo = _brought(tmp_path)
    _remote(tmp_path, repo)
    project = _set(config, project, pr_remote="origin", pr_repo="me/demo")

    async def refuse(argv, cwd, input=None):
        return (1, "HTTP 403: not allowed") if argv[0] == "gh" else (0, "")

    monkeypatch.setattr(pull_requests, "_run", refuse)
    review, store, _packet = _look(config, project)
    store.append("look_findings", {"look": 1, "routes": {}, "ledger": {"findings": [], "records": []},
                                   "probes": []}, role="orchestrator")
    review.verdict("explore", "underwritten")
    with pytest.raises(pull_requests.PullRequestError):
        asyncio.run(review.open_pull_request("explore"))
    assert "403" in store.payload("pull_request_failed")["error"]


def test_only_an_underwritten_look_is_opened_as_a_pr(tmp_path):
    config, project, _repo = _brought(tmp_path)
    review, _store, _packet = _look(config, project)
    with pytest.raises(Exception, match="Only an underwritten look"):
        asyncio.run(BranchReview(config, project).open_pull_request("explore"))
    assert review is not None
