"""Branch packets: a look at a branch Fabrika did not build -- the way in, the cut, the checks."""

import asyncio
import json
import re
import subprocess

import pytest

from helpers import *  # noqa: F403

from factory import pieces as cutting
from factory.pipeline.branch import BranchReview
from factory.projects import ProjectError, ProjectRegistry
from factory.schemas import Gate, GateReport, GateResult, Piece, PieceCall, PieceJoin, PieceJudgement


def _git(repo, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo,
                          check=True, capture_output=True, text=True).stdout.strip()


def _write(repo, files):
    for rel, text in files.items():
        target = repo / rel
        if text is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            target.write_bytes(text)
        else:
            target.write_text(text)


BASE = {
    "app/core.py": (
        '"""The core."""\n\nimport os\n\nLIMIT = 3\n\n\n'
        "def keep(x):\n    return x\n\n\n"
        "def drop(x):\n    return None\n\n\n"
        "class Store:\n    size: int = 0\n\n    def put(self, x):\n        return x\n"),
    "app/settings.yaml": "server:\n  port: 8000\n  host: localhost\nlimits:\n  rows: 10\n",
    "notes.txt": "one\ntwo\nthree\n",
    "app/old_name.py": "def moved():\n    return 1\n",
    "app/gone.py": "def gone():\n    return 0\n",
    "tests/test_core.py": "from app.core import keep\n\n\ndef test_keep():\n    assert keep(1) == 1\n",
}

BRANCH = {
    "app/core.py": (
        '"""The core."""\n\nimport os\nimport sys\n\nLIMIT = 5\n\n\n'
        "def keep(x):\n    # still the same, but louder\n    return x\n\n\n"
        "class Store:\n    size: int = 0\n    name: str = \"\"\n\n    def put(self, x):\n        return x\n\n"
        "    def take(self):\n        return None\n\n\n"
        "class Widget:\n    spin: int = 0\n\n    def turn(self):\n        return self.spin + 1\n"),
    "app/settings.yaml": "server:\n  port: 9000\n  host: localhost\nlimits:\n  rows: 10\n  cols: 4\n",
    "notes.txt": "one\n2\nthree\nfour\n",
    "app/old_name.py": None,
    "app/new_name.py": "def moved():\n    return 2\n",
    "app/gone.py": None,
    "app/logo.png": b"\x89PNG\r\n\x1a\n\x00\x00\x00binary",
    "package-lock.json": '{\n  "lockfileVersion": 3\n}\n',
    "tests/test_core.py": ("from app.core import keep, Widget\n\n\ndef test_keep():\n    assert keep(1) == 1\n\n\n"
                           "def test_widget_turns():\n    assert Widget().turn() == 1\n"),
}

MESSAGE = "Widgets that turn\n\nA Widget class whose turn() adds one, and the limit raised to five."


def _brought(tmp_path, *, base=None, branch=None, gates=(), approved=True, fixers=()):
    """A registered project, and a branch on it built somewhere else.

    The checkout is left on `main`, where a person working in it would be."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, base or BASE)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-q", "-b", "explore")
    _write(repo, branch or BRANCH)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", MESSAGE)
    _git(repo, "checkout", "-q", "main")

    config = load_config(ROOT / "factory.example.yaml")
    config.paths.evidence = str(tmp_path / "evidence")
    config.paths.sandboxes = str(tmp_path / "sandboxes")
    config.pipeline.as_built_on_features = False
    registry = ProjectRegistry(config.evidence_path)
    project = registry.create(repo, "demo")
    project.state.base_ref = "main"
    project.state.gates = list(gates)
    if approved:
        project.state.stage = "ready"
        # A fixer is known as one by what it did at the baseline: it rewrote files.
        project.state.baseline = GateReport(results=[GateResult(name=g.name, passed=True, rewrote=["app/core.py"] if g.name in fixers else [])
                                                     for g in gates])
    registry.save(project, note="test fixture")
    return config, registry.get(project.id), repo


class _LookStub:
    """The rapporteur, answering a look from a table; nothing else may be asked."""

    def __init__(self, klass="mechanical", skip=(), unreasoned=(), joins=(), every_id=False):
        self.klass, self.skip, self.unreasoned = klass, set(skip), set(unreasoned)
        self.joins, self.every_id = list(joins), every_id
        self.asked: list[tuple[str, str, str]] = []

    async def ask(self, role, prompt, schema=None, *, system="", temperature=None):
        assert system, f"role {role} was called without its prompt file"
        self.asked.append((role, prompt, system))
        if schema is not PieceJudgement:
            raise AssertionError(f"a look asked {role} for {schema}")
        ids = re.findall(r"^## (P-\d+) ·", prompt, re.M)
        if self.every_id:
            # A rapporteur that classes what it was told code had classed.
            ids += re.findall(r"^- (P-\d+) `", prompt, re.M)
        return PieceJudgement(
            headline="Widgets that turn.", summary="A Widget class, and a higher limit.",
            pieces=[PieceCall(id=i, klass=self.klass, reason="" if i in self.unreasoned else "Forced.")
                    for i in ids if i not in self.skip],
            joins=self.joins)

    def usage_report(self):
        return {}

    def coverage(self):
        return {}


def _look(config, project, llm=None, branch="explore", intent=""):
    review = BranchReview(config, project, llm=llm or _LookStub())
    state = review.open_look(branch, intent)
    packet = asyncio.run(review.run_look(state.slug))
    return review, review.branch_store(state.slug), packet


def _snapshot(repo):
    return {
        "head": _git(repo, "rev-parse", "HEAD"),
        "on": _git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
        "branch": _git(repo, "rev-parse", "refs/heads/explore"),
        "refs": _git(repo, "for-each-ref", "--format=%(refname) %(objectname)"),
        "status": _git(repo, "status", "--porcelain", "--untracked-files=all"),
        "worktrees": _git(repo, "worktree", "list", "--porcelain"),
    }


# ==========================================================================
# the way in: a branch in a registered project, and nothing of theirs moves
# ==========================================================================


def test_a_look_never_moves_the_checkout_the_branch_or_its_head(tmp_path):
    """The branch belongs to whoever built it, and the checkout to whoever is
    working in it. A look runs a formatter that rewrites a tracked file and a
    check that leaves output behind, and afterwards every ref, the checkout's
    HEAD, its status and the list of worktrees are exactly what they were."""
    gates = [Gate(name="fmt", command="sed -i 's/LIMIT = 5/LIMIT = 6/' app/core.py"),
             Gate(name="tests", command="echo ran > .check-output && test -f app/core.py")]
    config, project, repo = _brought(tmp_path, gates=gates, fixers=["fmt"])
    before = _snapshot(repo)
    _review, store, packet = _look(config, project)
    assert _snapshot(repo) == before, "a look moved something in the person's repository"
    assert packet["head_sha"] == before["branch"]
    assert not (tmp_path / "sandboxes" / project.id / "branches" / "explore-1").exists(), \
        "the look's own checkout outlived it"
    assert store.payload("state")["stage"] == "awaiting_ruling"


def test_a_formatter_on_outside_work_is_reported_not_committed(tmp_path):
    """On a feature Fabrika built, what a fixer rewrites is committed. On a branch
    it did not build, that would be Fabrika writing to someone else's work. The
    packet says what the formatter would change, and the checks after it see
    the branch as it was handed in."""
    gates = [Gate(name="fmt", command="sed -i 's/LIMIT = 5/LIMIT = 6/' app/core.py"),
             Gate(name="sees-the-branch", command="grep -q 'LIMIT = 5' app/core.py")]
    config, project, repo = _brought(tmp_path, gates=gates, fixers=["fmt"])
    _review, store, packet = _look(config, project)
    assert store.payload("located")["would_rewrite"] == {"fmt": ["app/core.py"]}
    rows = {r["name"]: r for r in packet["checks"]["results"]}
    assert rows["fmt"]["would_rewrite"] == ["app/core.py"]
    assert rows["sees-the-branch"]["passed"], "a check after the formatter saw the formatter's rewrite"
    assert _git(repo, "rev-list", "--count", "main..explore") == "1", "a commit landed on the branch"


def test_the_base_is_where_the_branch_left_main_not_where_main_is_now(tmp_path):
    """Main moves after a branch is cut. Diffed against main's tip, the branch
    would be credited with deleting everything main did since."""
    config, project, repo = _brought(tmp_path)
    left_at = _git(repo, "merge-base", "main", "explore")
    _write(repo, {"app/later.py": "def later():\n    return 1\n"})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "main moves on")
    _review, store, packet = _look(config, project)
    assert packet["base_sha"] == left_at
    assert "app/later.py" not in {p["path"] for p in packet["pieces"]}, "main's own work was put on the branch"


def test_a_look_refuses_a_branch_that_is_not_there_or_has_nothing_on_it(tmp_path):
    config, project, repo = _brought(tmp_path)
    review = BranchReview(config, project, llm=_LookStub())
    with pytest.raises(ProjectError, match="no branch"):
        review.open_look("never-made")
    _git(repo, "branch", "empty", "main")
    with pytest.raises(ProjectError, match="no commits"):
        review.open_look("empty")
    assert project.branch_slugs() == [], "a refused look left a ledger behind"


def test_a_second_look_at_a_branch_is_appended_to_the_first(tmp_path):
    """The record only grows: the branch moves, a second look is taken, and the
    first look's packet is still in the ledger, under its own number."""
    config, project, repo = _brought(tmp_path)
    _look(config, project)
    _git(repo, "checkout", "-q", "explore")
    _write(repo, {"notes.txt": "one\n2\nthree\nfour\nfive\n"})
    _git(repo, "commit", "-qam", "five")
    _git(repo, "checkout", "-q", "main")
    _review, store, packet = _look(config, project)
    assert [p["look"] for p in store.payloads("branch_packet")] == [1, 2]
    assert [p["look"] for p in store.payloads("look")] == [1, 2]
    assert packet["head_sha"] == _git(repo, "rev-parse", "explore")


# ==========================================================================
# The Every Line Once Rule
# ==========================================================================


def test_every_changed_line_is_in_exactly_one_piece(tmp_path):
    """A python file with a function changed, one deleted and a class added; a
    setting changed and one added; a text file nothing outlines; a file moved
    and edited; one deleted; a test file; a lockfile; a binary. Every line git
    says changed is in exactly one piece, counted against git's own numbers."""
    _config, _project, repo = _brought(tmp_path)
    base, head = _git(repo, "rev-parse", "main"), _git(repo, "rev-parse", "explore")
    cut = cutting.cut(repo, base, head)
    numstat = _git(repo, "diff", "--numstat", "--find-renames", base, head)
    expected_added = sum(int(a) for a, _r, _p in (line.split("\t") for line in numstat.splitlines()) if a != "-")
    expected_removed = sum(int(r) for _a, r, _p in (line.split("\t") for line in numstat.splitlines()) if r != "-")
    assert sum(len(p.added) for p in cut) == expected_added
    assert sum(len(p.removed) for p in cut) == expected_removed
    held = [(p.path, "+", n) for p in cut for n in p.added] + [(p.path, "-", n) for p in cut for n in p.removed]
    assert len(held) == len(set(held)), "a changed line is in two pieces"

    by = {(p.path, p.symbol): p for p in cut}
    assert ("app/core.py", "keep") in by and ("app/core.py", "drop (removed)") in by
    assert ("app/core.py", "Widget") in by, "a class the branch added was not one piece"
    assert ("app/settings.yaml", "server.port") in by or ("app/settings.yaml", "server") in by
    assert any(p.path == "notes.txt" and p.kind == "lines" for p in cut)
    moved = next(p for p in cut if p.path == "app/new_name.py")
    assert moved.old_path == "app/old_name.py" and moved.removed and moved.added
    assert any(p.path == "app/gone.py" and p.symbol == "gone (removed)" for p in cut)
    assert next(p for p in cut if p.path == "app/logo.png").binary
    assert next(p for p in cut if p.path == "package-lock.json").klass == "generated"
    assert {p.klass for p in cut if p.path.startswith("tests/")} == {"tests"}


def test_a_cut_that_loses_or_doubles_a_line_is_refused():
    change = cutting.FileChange(path="a.py", old_path="a.py", status="M", hunks=[(1, 1, 1, 2)])
    whole = Piece(id="P-1", path="a.py", symbol="a", added=[1, 2], removed=[1])
    cutting.account([whole], [change])
    with pytest.raises(cutting.LineAccountError, match="in no piece"):
        cutting.account([Piece(id="P-1", path="a.py", symbol="a", added=[1], removed=[1])], [change])
    with pytest.raises(cutting.LineAccountError, match="in two"):
        cutting.account([whole, Piece(id="P-2", path="a.py", symbol="b", added=[2])], [change])
    with pytest.raises(cutting.LineAccountError, match="not in the diff"):
        cutting.account([Piece(id="P-1", path="a.py", symbol="a", added=[1, 2, 3], removed=[1])], [change])


def test_a_join_that_would_hide_or_double_a_line_is_refused(tmp_path):
    """The rapporteur may join adjacent pieces of one file into one thing to read.
    It may not join pieces apart from each other, pieces of two files, tests, or
    a piece twice -- and whatever it joins, the lines are all still there once."""
    _config, _project, repo = _brought(tmp_path)
    cut = cutting.cut(repo, _git(repo, "rev-parse", "main"), _git(repo, "rev-parse", "explore"))
    core = [p for p in cut if p.path == "app/core.py"]
    test = next(p for p in cut if p.klass == "tests")
    other = next(p for p in cut if p.path == "notes.txt")
    judgement = PieceJudgement(headline="h", summary="s", pieces=[
        PieceCall(id=p.id, klass="mechanical", reason="r") for p in cut], joins=[
        PieceJoin(ids=[core[0].id, core[1].id], reason="one thing"),
        PieceJoin(ids=[core[1].id, core[2].id], reason="again"),
        PieceJoin(ids=[core[2].id, core[4].id], reason="apart"),
        PieceJoin(ids=[core[3].id, other.id], reason="two files"),
        PieceJoin(ids=[test.id, test.id], reason="one"),
        PieceJoin(ids=["P-999", core[3].id], reason="invented"),
    ])
    final, refused = cutting.join(cutting.classify([p.model_copy(deep=True) for p in cut], judgement), judgement)
    assert len(final) == len(cut) - 1
    assert [r["refused"] for r in refused] == [
        "a piece in it is already joined", "the pieces are not next to each other",
        "the pieces are in different files", "a join needs two pieces",
        "it names a piece that does not exist"]
    joined = next(p for p in final if p.joined)
    assert joined.joined == [core[0].id, core[1].id]
    assert sorted(joined.added) == sorted(core[0].added + core[1].added)


def test_a_piece_the_rapporteur_did_not_class_is_novel(tmp_path):
    """Unsure means novel, and silence is the surest kind of unsure: a piece left
    out, or classed with no reason, is shown open, as something to read."""
    config, project, _repo = _brought(tmp_path)
    cut = cutting.cut(project.repo_path, _git(project.repo_path, "rev-parse", "main"),
                      _git(project.repo_path, "rev-parse", "explore"))
    asked = [p.id for p in cut if p.classed_by != "code"]
    stub = _LookStub(skip=[asked[0]], unreasoned=[asked[1]])
    _review, _store, packet = _look(config, project, llm=stub)
    by = {p["id"]: p for p in packet["pieces"]}
    assert (by[asked[0]]["klass"], by[asked[0]]["reason"]) == ("novel", cutting.UNCLASSED)
    assert (by[asked[1]]["klass"], by[asked[1]]["reason"]) == ("novel", cutting.UNREASONED)
    assert by[asked[2]]["klass"] == "mechanical"


def test_tests_are_a_group_of_their_own_whatever_the_rapporteur_says(tmp_path):
    config, project, _repo = _brought(tmp_path)
    _review, _store, packet = _look(config, project, llm=_LookStub(every_id=True))
    tests = [p for p in packet["pieces"] if p["path"].startswith("tests/")]
    assert tests and {p["klass"] for p in tests} == {"tests"}
    assert {p["klass"] for p in packet["pieces"] if p["path"] == "package-lock.json"} == {"generated"}


def test_the_rapporteur_is_told_it_presents_a_branch_nobody_specified(tmp_path):
    config, project, _repo = _brought(tmp_path)
    stub = _LookStub()
    _look(config, project, llm=stub, intent="Let a widget turn.")
    [(role, prompt, system)] = stub.asked
    assert role == "rapporteur"
    assert "a branch Fabrika did not build" in system
    assert "Let a widget turn." in prompt and "Widgets that turn" in prompt
    to_class = prompt.split("# The pieces to class", 1)[1].split("\n# ", 1)[0]
    assert "tests/test_core.py" not in to_class, "the rapporteur was asked to class a test"
    assert "test_widget_turns in tests/test_core.py" in prompt, "the rapporteur was not told the tests it can name"


# ==========================================================================
# the repository's own checks, and whose a failure is
# ==========================================================================


def test_a_gate_red_at_the_merge_base_is_not_charged_to_the_branch(tmp_path):
    """Red at the head says nothing alone. Run again where the branch left main,
    a check that was red there too is inherited, and one that was green there is
    the branch's -- and the answer is kept on the project, for every look and
    feature cut from the same commit."""
    gates = [Gate(name="inherited", command="test ! -f notes.txt"),
             Gate(name="caused", command="test ! -f app/new_name.py"),
             Gate(name="green", command="true")]
    config, project, repo = _brought(tmp_path, gates=gates)
    _review, _store, packet = _look(config, project)
    rows = {r["name"]: r for r in packet["checks"]["results"]}
    assert rows["caused"]["caused_here"] and not rows["caused"]["pre_existing"]
    assert rows["inherited"]["pre_existing"] and not rows["inherited"]["caused_here"]
    assert rows["green"]["passed"] and rows["green"]["at_base"] == "not_checked"
    left_at = _git(repo, "merge-base", "main", "explore")
    kept = [r["payload"] for r in project.store if r.get("kind") == "attribution"]
    assert {(k["gate"], k["base_sha"]) for k in kept} == {("inherited", left_at), ("caused", left_at)}


def test_a_lint_finding_on_an_old_line_is_told_apart_from_one_the_branch_added(tmp_path):
    """A repository with problems before the branch has them after it too. Read
    by line, the packet says which findings sit on lines the branch wrote."""
    config, project, repo = _brought(tmp_path)
    added_line = next(n for n, line in enumerate(BRANCH["app/core.py"].splitlines(), 1) if "class Widget" in line)
    report = tmp_path / "lint.sarif"
    report.write_text(_sarif(("app/core.py", added_line, "W1"), ("app/core.py", 1, "OLD"),  # noqa: F405
                             ("tests/test_core.py", 4, "OLD")))
    project.state.gates = [Gate(name="lint", command=f"cp {report} {{report}}", report_format="sarif")]
    project.state.baseline = GateReport(results=[GateResult(name="lint", passed=True)])
    ProjectRegistry(config.evidence_path).save(project, note="lint")
    _review, store, packet = _look(config, ProjectRegistry(config.evidence_path).get(project.id))
    found = next(r for r in packet["checks"]["results"] if r["name"] == "lint")["located"]
    assert (found["added_count"], found["before_count"]) == (1, 2)
    assert found["added"][0]["rule"] == "W1"


def test_a_project_with_no_approved_checks_still_gets_a_packet_that_says_so(tmp_path):
    gates = [Gate(name="tests", command="true")]
    config, project, _repo = _brought(tmp_path, gates=gates, approved=False)
    _review, _store, packet = _look(config, project)
    assert packet["checks"]["status"] == "proposed"
    assert [r["name"] for r in packet["checks"]["results"]] == ["tests"]
    config, project, _repo = _brought(tmp_path / "bare", gates=(), approved=False)
    _review, _store, packet = _look(config, project)
    assert packet["checks"] == {"status": "none", "results": [], "would_rewrite": {}}
    assert packet["pieces"], "a project with no checks lost its code view"


# ==========================================================================
# the server
# ==========================================================================


def _branch_app(tmp_path, monkeypatch, **kw):
    from fastapi.testclient import TestClient

    from factory.server import create_app

    config, project, repo = _brought(tmp_path, **kw)
    started = []

    async def run_look(self, slug):
        started.append(slug)
        return {}

    monkeypatch.setattr(BranchReview, "run_look", run_look)
    return TestClient(create_app(config)), project, repo, started


def test_a_look_is_started_on_a_branch_and_refused_on_one_that_is_not_there(tmp_path, monkeypatch):
    client, project, repo, started = _branch_app(tmp_path, monkeypatch)
    listed = client.get(f"/api/projects/{project.id}/branches").json()
    assert [c["branch"] for c in listed["candidates"]] == ["explore"] and listed["looked"] == []
    missing = client.post(f"/api/projects/{project.id}/branches", json={"branch": "never-made"})
    assert missing.status_code == 409 and "no branch" in missing.json()["detail"]
    _git(repo, "branch", "empty", "main")
    empty = client.post(f"/api/projects/{project.id}/branches", json={"branch": "empty"})
    assert empty.status_code == 409 and "no commits" in empty.json()["detail"]
    assert started == [], "a refused look started anyway"

    made = client.post(f"/api/projects/{project.id}/branches", json={"branch": "explore", "intent": "spin"})
    assert made.status_code == 200 and started == ["explore"]
    got = client.get(f"/api/projects/{project.id}/branches/explore").json()
    assert got["state"]["intent"] == "spin" and got["looks"][0]["commits"][0]["message"] == MESSAGE
    assert got["packet"] is None and not got["current"]
    listed = client.get(f"/api/projects/{project.id}/branches").json()
    assert [b["branch"] for b in listed["looked"]] == ["explore"] and listed["candidates"] == []
    assert client.get(f"/api/projects/{project.id}/branches/nope").status_code == 404


def test_a_reading_that_stopped_is_said_and_can_be_read_again(tmp_path, monkeypatch):
    """The head's reading stopped at the reader's usage window, the station said
    "done, 2 readings", and Where it landed said the as-built "was not read".
    Each end of a look's as-built now says what became of it -- read, stopped
    and why, under way -- and Read again carries a stopped one on, at that end
    only. With the as-built off on this factory there is nothing to read, and
    it says so rather than looking pressed."""
    from factory.pipeline.branch import reading_status

    client, project, _repo, _started = _branch_app(tmp_path, monkeypatch)
    client.post(f"/api/projects/{project.id}/branches", json={"branch": "explore"})
    review = BranchReview(load_config(ROOT / "factory.example.yaml"), project)
    store = project.branch_store("explore")
    state = review.look_state(store)
    store.append("as_built_failed", {"label": "head", "commit": state.head_sha,
                                     "error": "AsBuiltError: Stopped: the route is at 85% of its window."},
                 role="orchestrator")
    got = client.get(f"/api/projects/{project.id}/branches/explore").json()
    assert got["readings"]["head"] == {"state": "stopped", "error": "Stopped: the route is at 85% of its window.",
                                       "unread": 0, "files": 0}
    assert got["readings"]["base"]["state"] == "none"
    assert got["folders"], "the folders the head holds were not counted"

    asked = []
    monkeypatch.setattr(BranchReview, "_as_built_begin",
                        lambda self, store, sha, label: asked.append((label, sha)) or object())
    again = client.post(f"/api/projects/{project.id}/branches/explore/read")
    assert again.status_code == 200 and again.json()["started"] == ["base", "head"]
    assert asked == [("base", state.base_sha), ("head", state.head_sha)]

    monkeypatch.setattr(BranchReview, "_as_built_begin", lambda self, store, sha, label: None)
    off = client.post(f"/api/projects/{project.id}/branches/explore/read")
    assert off.status_code == 409 and "not read on this factory" in off.json()["detail"]
    assert reading_status(store, state)["head"]["state"] == "stopped", "asking again changed the record"


def test_a_look_read_whole_at_both_ends_says_where_it_landed_in_groups(tmp_path):
    """A feature compares its two readings as its build ends; a look had no
    step that did, so its groups never showed however whole its readings were.
    Both ends read whole -- by the look, or later by Read again -- the
    comparison is kept once, and a reading with files left over is not one."""
    from test_asbuilt import _two_readings

    from factory.pipeline.branch import record_system_change
    from factory.schemas import BranchState
    from factory.store import EvidenceStore

    base, head, _ = _two_readings()
    store = EvidenceStore(tmp_path / "branches", "explore")
    state = BranchState(look_id="l-1", project_id="demo", branch="explore", slug="explore", base_sha=base.commit,
                        head_sha=head.commit)
    assert not record_system_change(store, state), "compared before either end was read"

    def read(label, reading):
        target = store.artifact_dir("as-built") / f"{label}.json"
        target.write_text(reading.model_dump_json(), encoding="utf-8")
        store.append("as_built", {"label": label, "commit": reading.commit,
                                  "file": str(target.relative_to(store.dir))})

    read("base", base)
    gappy = head.model_copy(deep=True)
    gappy.coverage.unread = [{"path": "web/src/App.tsx", "why": "the reader stopped"}]
    read("head", gappy)
    assert not record_system_change(store, state), "a head read with a file left over was compared"
    read("head", head)
    assert record_system_change(store, state) and record_system_change(store, state)
    assert len(store.all("system_change")) == 1, "the comparison was kept twice"


def test_where_it_landed_is_one_line_a_group_and_names_real_units():
    """With no groups to say it in, where the branch landed is said by folder,
    each with how much of it was touched, and the folders it left alone after.
    A file's chips are what a reader would look for -- a function, a class, a
    route -- and the cut's own seams ("imports", "models.py, top", "Card,
    fields") are folded into a count. A reading that stopped says so, why, and
    offers to read again."""
    pieces = [
        {"id": "P-1", "path": "api/app/models.py", "symbol": "models.py, top · imports", "kind": "module",
         "klass": "mechanical", "fresh": False, "added": [1], "removed": []},
        {"id": "P-2", "path": "api/app/models.py", "symbol": "Card, fields · Card.tag_names", "kind": "setting",
         "klass": "novel", "fresh": False, "added": [2, 3], "removed": [9]},
        {"id": "P-3", "path": "api/app/tags.py", "symbol": "GET /api/tags", "kind": "function",
         "klass": "novel", "fresh": True, "added": [4], "removed": []},
        {"id": "P-4", "path": "api/tests/test_tags.py", "symbol": "test_tags", "kind": "test",
         "klass": "tests", "fresh": True, "added": [5], "removed": []}]
    look = {"readings": {"base": {"state": "read"}, "head": {"state": "stopped", "error": "Stopped: at 85%."}},
            "folders": {"api/app": 15, "api/tests": 8, "web/src": 16, ".fabrika/as-built": 130}}
    got = _branch_js(f"""
      console.log(JSON.stringify(bpLanded({{ pieces: {json.dumps(pieces)} }}, null, '#c', {json.dumps(look)})));
    """)
    assert "stopped" in got and "Stopped: at 85%." in got and 'id="look-read"' in got
    assert "was not read" not in got
    assert got.count('<details class="bp-gr">') == 1 and "api/app/" in got and "2 of 15 files" in got
    alone = got.split("Left alone:", 1)[1].split("bp-tests-line", 1)[0]
    assert "web/src/" in alone, "a folder the branch left alone is not named"
    assert ".fabrika" not in alone and "api/tests/" not in alone
    chips = re.findall(r'class="bp-sym (?:add|chg)"[^>]*>([^<]*)</a>', got)
    assert chips == ["Card", "Card.tag_names", "GET /api/tags"], "a seam of the cut was named as a unit"
    assert "and its imports" in got
    assert "1 file," in got and "1 new test" in got


def test_a_finished_look_is_served_whole(tmp_path, monkeypatch):
    config, project, _repo = _brought(tmp_path)
    _look(config, project)
    from fastapi.testclient import TestClient

    from factory.server import create_app

    client = TestClient(create_app(config))
    got = client.get(f"/api/projects/{project.id}/branches/explore").json()
    listed = client.get("/api/branches").json()
    assert [(b["project_id"], b["branch"], b["stage"]) for b in listed] == [(project.id, "explore", "awaiting_ruling")], \
        "the side panel's list of looks is missing one, or cannot say whose it is"
    assert got["current"] and got["packet"]["headline"] == "Widgets that turn."
    assert got["state"]["stage"] == "awaiting_ruling"
    assert [p["name"] for p in got["state"]["phases"]][:1] == ["reader"]
    assert json.dumps(got)  # everything the screen reads is JSON


# ==========================================================================
# the screen
# ==========================================================================


def _branch_js(body: str):
    """Run the console's branch screen in node, over the given packet."""
    import shutil

    if not shutil.which("node"):
        pytest.skip("node is not installed")
    from support import CONSOLE_DIR

    source = (CONSOLE_DIR / "app" / "branches.js").read_text(encoding="utf-8")
    script = ("const state = {};\nconst main = null;\n"
              "const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/\"/g, '&quot;');\n"
              "const icon = (n, c) => `<svg class=\"${c}\"></svg>`;\n"
              # The spec's section, as intake.js draws it: the console loads both.
              "const docSection = (id, name, count, sub, body) => `<section class=\"doc-sec\">"
              "<div class=\"sec-head\" id=\"sec-${id}\"><h2>${name}</h2>${count}</div>${body}</section>`;\n"
              "const docTabs = (sec, panels) => panels.map((p) => p[3]).join('');\n"
              + source + "\n" + body)
    out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_code_view_opens_novel_folds_the_rest_and_draws_every_piece_once(tmp_path):
    """Novel is open and the rest folded, every piece says why it was classed
    the way it was, and what the screen counts is what the cut holds: a view
    that dropped a piece would be a record that got shorter."""
    config, project, _repo = _brought(tmp_path)
    cut = cutting.cut(project.repo_path, _git(project.repo_path, "rev-parse", "main"),
                      _git(project.repo_path, "rev-parse", "explore"))
    asked = [p.id for p in cut if p.classed_by != "code"]
    _review, _store, packet = _look(config, project, llm=_LookStub(skip=asked[:2]))
    got = _branch_js(f"""
      const packet = {json.dumps(packet)};
      const total = Object.values(packet.lines).reduce((a, n) => a + n, 0);
      const out = {{}};
      for (const order of ['klass', 'file']) {{
        state.bpOrder = order;
        const html = bpCode(packet, total);
        const units = [...html.matchAll(/<details class="bp-unit bp-unit-(\\w+)" id="piece-([^"]+)"[^>]*?( open)?>/g)];
        out[order] = {{
          ids: units.map((m) => m[2]),
          open: units.filter((m) => m[3]).map((m) => m[1]),
          shut: units.filter((m) => !m[3]).map((m) => m[1]),
          reasons: [...html.matchAll(/<span class="bp-uwhy">([^<]*)<\\/span>/g)].map((m) => m[1]),
          counted: [...html.matchAll(/<span class="a">\\+(\\d+)<\\/span> <span class="r">−(\\d+)<\\/span>/g)]
            .reduce((a, m) => a + Number(m[1]) + Number(m[2]), 0),
        }};
      }}
      out.total = total;
      console.log(JSON.stringify(out));
    """)
    ids = sorted(p["id"] for p in packet["pieces"])
    for order in ("klass", "file"):
        view = got[order]
        assert sorted(view["ids"]) == ids, f"ordered by {order}, a piece was dropped or drawn twice"
        assert set(view["open"]) == {"novel"} and "novel" not in view["shut"]
        assert len(view["reasons"]) == len(ids) and all(r.strip() for r in view["reasons"]), \
            "a folded piece hid the reason for its class"
        assert view["counted"] == got["total"] == packet["stat"]["added"] + packet["stat"]["removed"]


def test_the_branches_tab_is_a_list_to_pick_from_and_asks_what_for_only_after_a_pick():
    """The tab opened as two empty fields and a button, with no way to see what
    there was to look at. It is a list now: every branch with work past the
    base, its commits and its last message, and the one-line question about
    what it was for appears under the branch picked, and nowhere else."""
    branches = {"project": "demo", "base_ref": "main", "looked": [
        {"slug": "done", "branch": "done", "look": 2, "stage": "awaiting_ruling", "headline": "It turns."}],
        "candidates": [
            {"branch": "explore", "ahead": 1, "at": "2026-10-23T10:00:00Z", "subject": "Widgets that turn"},
            {"branch": "other", "ahead": 3, "at": "2026-10-22T10:00:00Z", "subject": "Something else"}]}
    got = _branch_js(f"""
      state.branches = {json.dumps(branches)};
      const p = {{project_id: 'demo'}};
      const before = branchesSection(p);
      state.lookPick = 'explore';
      const after = branchesSection(p);
      console.log(JSON.stringify({{before, after}}));
    """)
    before, after = got["before"], got["after"]
    assert before.count("data-look-pick=") == 2 and "Widgets that turn" in before and "1 commit past main" in before
    assert 'id="look-intent"' not in before and 'id="look-start"' not in before, \
        "the question was asked before anything was picked"
    assert 'href="#/demo?branch=done"' in before and "It turns." in before
    assert after.count('id="look-intent"') == 1 and after.count('id="look-start"') == 1
    picked = after.split('data-look-pick="explore"', 1)[1].split('data-look-pick="other"', 1)[0]
    assert 'id="look-intent"' in picked, "the question opened somewhere other than under the branch picked"


def test_a_look_s_line_is_every_station_in_order_in_the_shape_the_feature_strip_reads():
    """The look said "the stations light as each finishes" and drew a row of
    dots of its own. It is drawn by the strip a feature's board uses now, so it
    has to hand that strip what it reads: every station in order, attribution
    included before it runs, and a stage the strip knows -- a finished look is
    settled, so a station with nothing to do reads as skipped, not waiting."""
    got = _branch_js("""
      const run = lookLine({stage: 'reviewing', phases: [
        {name: 'reader', status: 'done'}, {name: 'gates', status: 'running'}]});
      const done = lookLine({stage: 'awaiting_ruling', phases: [{name: 'rapporteur', status: 'done'}]});
      console.log(JSON.stringify({run, done}));
    """)
    assert [p["name"] for p in got["run"]["phases"]] == ["reader", "gates", "attribution", "rapporteur"]
    assert [p["status"] for p in got["run"]["phases"]] == ["done", "running", "pending", "pending"]
    assert got["run"]["state"]["stage"] == "building" and got["done"]["state"]["stage"] == "awaiting_verdict"


def test_a_commit_whose_reading_left_files_unread_is_read_again(tmp_path):
    """A look whose reader lost its sign-in kept a reading with most files
    missing, and the next look at the same commit took that as the commit read:
    its "what it built" said 0 groups and 0 ways in. A reading with unread files
    is kept, and is read again; a whole one is not."""
    from factory.asbuilt_graph import AsBuilt, Coverage

    config, project, repo = _brought(tmp_path)
    config.pipeline.as_built_on_features = True
    review = BranchReview(config, project, llm=_LookStub())
    store = review.branch_store("explore")
    head = _git(repo, "rev-parse", "explore")

    def saved(name, unread):
        target = store.artifact_dir("as-built") / name
        target.write_text(AsBuilt(commit=head, coverage=Coverage(unread=unread)).model_dump_json())
        store.append("as_built", {"label": "head", "commit": head,
                                  "file": str(target.relative_to(store.dir))}, role="orchestrator")

    async def begins():
        task = review._as_built_begin(store, head, "head")
        if task is not None:
            task.cancel()
        return task is not None

    saved("partial.json", [{"path": "app/core.py", "why": "the reader failed"}])
    assert asyncio.run(begins()), "a reading with unread files was taken as the commit read"

    async def twice():
        first = review._as_built_begin(store, head, "head")
        again = review._as_built_begin(store, head, "head")
        first.cancel()
        return first is again

    assert asyncio.run(twice()), "a reading still running was started a second time beside itself"
    saved("whole.json", [])
    assert not asyncio.run(begins()), "a whole reading was read again"


def test_a_look_s_packet_offers_the_review_s_reading_mode():
    """A look's packet is read like a feature's, and the review's full-screen
    reading mode is one button in its verdict band, as it is there."""
    from support import app_js

    screen = app_js().split("function branchScreen()", 1)[1].split("\nfunction ", 1)[0]
    band = screen.split('class="v-band', 1)[1].split("</section>", 1)[0]
    assert "focusToggle()" in band, "the look's packet has no way into reading mode"


def test_a_path_step_is_added_or_changed_by_the_diff_not_by_the_model():
    """A path's steps open the code. Whether a step is new or changed is what
    the diff says of its piece; a step naming a piece that does not exist is
    drawn as outside the branch's code, not linked to nothing; a piece since
    joined into another is found through the one it is in now."""
    from factory.schemas import CodePath, PathStep

    pieces = [Piece(id="P-1", path="a.py", symbol="a", added=[1], fresh=True, joined=["P-1", "P-2"]),
              Piece(id="P-3", path="a.py", symbol="c", added=[5], removed=[4])]
    judgement = PieceJudgement(headline="h", summary="s", paths=[
        CodePath(title="Install", steps=[PathStep(label="button"), PathStep(label="b", piece="P-2"),
                                         PathStep(label="c", piece="P-3"), PathStep(label="ghost", piece="P-99")],
                 then="pressed", then_steps=[PathStep(label="npm i -g")]),
        CodePath(title=" ", steps=[PathStep(label="x", piece="P-1")])])
    [path] = cutting.paths_of(judgement, pieces)
    assert [(st["label"], st["kind"], st["piece"]) for st in path["steps"]] == [
        ("button", "outside", ""), ("b", "added", "P-1"), ("c", "changed", "P-3"), ("ghost", "outside", "")]
    assert path["then"] == "pressed" and path["then_steps"][0]["kind"] == "outside"


def test_a_claim_counts_only_tests_that_exist_at_the_head(tmp_path):
    """A claim is shown when a test that is really in the branch exercises it
    and the tests ran green. A test the rapporteur names that is not there is
    said to be missing, not counted."""
    from factory.schemas import Claim

    _config, _project, repo = _brought(tmp_path)
    head = _git(repo, "rev-parse", "explore")
    judgement = PieceJudgement(headline="h", summary="s", claims=[
        Claim(quote="A Widget class whose turn() adds one", tests=["test_widget_turns", "test_invented"]),
        Claim(quote="the limit raised to five", tests=[])])
    shown, untested = cutting.claims_of(judgement, repo, head, True)
    assert (shown["state"], shown["tests"], shown["missing"]) == ("shown", ["test_widget_turns"], ["test_invented"])
    assert untested["state"] == "untested"
    assert cutting.claims_of(judgement, repo, head, False)[0]["state"] == "unproved", \
        "a claim was called shown by tests that did not run green"


def test_a_javascript_test_is_found_by_its_title(tmp_path):
    """Only a Python `def` counted, so every vitest and Playwright test the
    rapporteur named -- "TagPicker", "a tag is added, picked again..." -- was
    said not to be in the branch, and claims they show were marked untested.
    A JavaScript test is its title, in whichever quotes the suite used, and a
    nested one is found by its last part."""
    from factory.schemas import Claim

    branch = {"web/Pick.test.tsx": ("describe('TagPicker', () => {\n"
                                     "  it(\"picks the existing tag on Enter\", () => {});\n});\n"),
              "web/e2e/tags.spec.ts": "test(`a tag is added, kept across a reload`, async () => {});\n"}
    _config, _project, repo = _brought(tmp_path, base={"web/x.ts": "export {};\n"}, branch=branch)
    head = _git(repo, "rev-parse", "explore")
    named = ["TagPicker", "TagPicker > picks the existing tag on Enter", "a tag is added, kept across a reload",
             "picks", "a tag that was never written"]
    (claim,) = cutting.claims_of(PieceJudgement(headline="h", summary="s", claims=[
        Claim(quote="Enter picks the tag that exists", tests=named)]), repo, head, True)
    assert claim["tests"] == named[:3], "a JavaScript test the suite has was not found"
    assert claim["missing"] == ["picks", "a tag that was never written"], \
        "a word of a title, or a title no suite has, was taken for a test"
    assert claim["state"] == "shown"


def test_what_a_branch_adds_is_found_in_its_code(tmp_path):
    """Routes, settings, secrets, what it reaches outside the repository and
    its packages: each found in a line the branch added, and each pointing at
    the piece that holds it."""
    from factory import branch_facts

    base = {"app/api.py": "def ok():\n    return 1\n", "settings.yaml": "name: demo\n",
            "package.json": '{\n  "dependencies": {"left-pad": "1"}\n}\n'}
    branch = {
        "app/api.py": ("import os\nfrom pathlib import Path\n\n\ndef ok():\n    return 1\n\n\n"
                       "@app.put(\"/api/token\")\ndef put_token(body):\n"
                       "    key = os.environ.get(\"SERVICE_TOKEN\")\n"
                       "    rc = Path(\"~/.npmrc\").expanduser()\n    return key, rc\n"),
        "settings.yaml": "name: demo\ntoken_env: SERVICE_TOKEN\n",
        "package.json": '{\n  "dependencies": {"left-pad": "1", "chalk": "5"}\n}\n'}
    _config, _project, repo = _brought(tmp_path, base=base, branch=branch)
    b, h = _git(repo, "rev-parse", "main"), _git(repo, "rev-parse", "explore")
    cut = cutting.cut(repo, b, h)
    f = branch_facts.facts(repo, b, h, cut, None)
    by = {p.id: p for p in cut}
    assert [(r["name"], r["state"]) for r in f["routes"]] == [("PUT /api/token", "added")]
    assert by[f["routes"][0]["piece"]].path == "app/api.py"
    assert [s["name"] for s in f["settings"]] == ["token_env"]
    assert [s["name"] for s in f["secrets"]] == ["$SERVICE_TOKEN"]
    assert {o["name"] for o in f["outside"]} == {"~/.npmrc", "expanduser()"}
    assert f["packages"]["added"] == ["chalk"] and not f["packages"]["removed"]


def test_what_it_built_draws_what_it_adds_how_it_runs_and_what_the_commits_claim():
    """The section said almost nothing: one paragraph and a list of files. It
    draws what the work adds, the paths its new code runs along, where it
    landed and its commits' claims against their tests, each name a link to
    its piece."""
    packet = {
        "summary": "Two parts.\n\nA `RouteConfig` gains fields.",
        "pieces": [{"id": "P-1", "path": "a.py", "symbol": "PUT /api/token", "klass": "novel", "start": 4,
                    "fresh": True, "added": [4, 5], "removed": []}],
        "facts": {"routes": [{"name": "PUT /api/token", "state": "added", "piece": "P-1"}], "settings": [],
                  "secrets": [{"name": "$TOKEN", "how": "read from the environment", "piece": "P-1"}],
                  "outside": [], "packages": {"added": [], "removed": [], "lockfiles": []}},
        "paths": [{"title": "Saving a token", "said": "Stores it.", "then": "", "then_steps": [], "steps": [
            {"label": "Save", "note": "card", "piece": "", "kind": "outside"},
            {"label": "put_token", "note": "", "piece": "P-1", "kind": "added", "where": "a.py"}]}],
        "claims": [{"quote": "stores the token", "tests": ["test_store"], "missing": [], "state": "shown"}]}
    got = _branch_js(f"""
      const packet = {json.dumps(packet)};
      console.log(JSON.stringify(bpBuilt(packet, null, [], '#/demo?branch=x&bp=code')));
    """)
    assert "<code>RouteConfig</code>" in got
    assert "What it adds" in got and "SEEN AT · 0" in got and "ENDPOINTS · 1" in got and "DATA · 0" in got
    assert 'class="toc-item" href="#sec-touches"' in got and 'id="sec-l-play"' in got, \
        "the sections are not headed and listed the way the spec's are"
    assert '<a class="bp-map-link" href="#/demo?branch=x&amp;bp=code#piece-P-1">' in got
    assert "place a secret is read or written" in got
    assert "new settings" not in got and "outside the repository" not in got, \
        "a kind of fact the branch has none of was drawn anyway"
    assert "Key flows" in got and 'data-bp-path="0"' in got and 'class="sq"' in got
    assert '<a href="#/demo?branch=x&amp;bp=code#piece-P-1"><text class="sq-msg add"' in got
    assert 'class="sq-who out"' in got, "a step outside the branch's code was not drawn as outside it"
    assert "Where it landed" in got and 'class="bp-sym add"' in got
    assert "stores the token" in got and "test_store" in got and "1 claim · 1 shown by a test" in got



def test_a_key_flow_is_a_sequence_diagram_of_the_files_it_runs_through():
    """The paths were a map in columns read in a zig-zag, then lanes that did
    not read as a sequence. A flow is a sequence diagram: a lifeline a file,
    ordered screen, client, server, data, then what is outside the branch's
    code -- read from the step's piece, not the model's word -- and each step a
    message to its file from the one before, numbered in order. A call within
    one file loops back to it; a step from the server back to the screen is a
    dashed reply; the call that crosses to the server says its request, named
    by the route's own handler."""
    pieces = [
        {"id": "P-1", "path": "web/src/Card.tsx", "symbol": "Card", "klass": "novel"},
        {"id": "P-5", "path": "web/src/Card.tsx", "symbol": "onAdd", "klass": "novel"},
        {"id": "P-2", "path": "web/src/api.ts", "symbol": "api.addTag", "klass": "novel"},
        {"id": "P-3", "path": "api/app/routers/tags.py", "symbol": "POST /api/cards/{card_id}/tags", "klass": "novel"},
        {"id": "P-4", "path": "api/app/models.py", "symbol": "card_tags", "klass": "novel"}]
    step = lambda label, piece="", kind="outside": {"label": label, "note": "", "piece": piece, "kind": kind}  # noqa: E731
    packet = {"pieces": pieces, "paths": [{"title": "Add", "said": "", "then": "", "then_steps": [], "steps": [
        step("+ tag", "P-1", "changed"), step("onAdd", "P-5", "added"), step("api.addTag", "P-2", "changed"),
        step("add_tag", "P-3", "added"), step("Postgres"), step("insert", "P-4", "added"),
        step("Card again", "P-1", "changed")]}]}
    got = _branch_js(f"""
      console.log(JSON.stringify(bpFlow({json.dumps(packet)}, '#x', 0)));
    """)
    assert got["who"] == ["Card", "api", "tags", "models", "Postgres"], "the lifelines are not the files, in order"
    svg = got["svg"]
    assert [int(n) for n in re.findall(r'data-step="(\d+)"', svg)] == list(range(1, 8))
    step2 = svg.split('data-step="2"', 1)[1].split("</g>", 1)[0]
    assert " V" in step2.split('class="sq-line"', 1)[1].split("/>", 1)[0], "a call within one file did not loop back"
    step7 = svg.split('data-step="7"', 1)[1].split("</g>", 1)[0]
    assert 'class="sq-line back"' in step7, "the step back to the screen is not drawn as a reply"
    step4 = svg.split('data-step="4"', 1)[1].split("</g>", 1)[0]
    assert ">POST /api/cards/{card_id}/tags</text>" in step4, "the call to the server does not say its request"
    assert 'class="sq-who out"' in svg


def test_what_the_person_does_sits_where_they_do_it():
    """"Then, the user types "bug": the steps numbered from 4 on" asked the
    reader to map a sentence onto numbers. It is the person's message now, from
    their lifeline, on the first step it causes."""
    step = lambda label: {"label": label, "note": "", "piece": "", "kind": "outside"}  # noqa: E731
    packet = {"pieces": [], "paths": [{"title": "P", "said": "", "then": "the user presses Enter",
                                       "steps": [step("opens")], "then_steps": [step("saves")]}]}
    got = _branch_js(f"""
      console.log(JSON.stringify(bpFlow({json.dumps(packet)}, '#x', 0).svg));
    """)
    assert got.index("</tspan> opens") < got.index("[the user presses Enter]") < got.index("</tspan> saves")
    assert got.count('class="sq-dot"') == 2, "what the person does does not start the messages it causes"
    assert "numbered from" not in got


def test_the_paths_are_drawn_one_at_a_time_however_many_there_are():
    """Every path on one map grew with the branch and stopped fitting on a screen
    at four. One flow is drawn at a time, holding its steps and no other's; the
    rest are a numbered list, one line each, and the drawn one steps to its
    neighbours -- round from the last to the first."""
    step = lambda label: {"label": label, "note": "", "piece": "", "kind": "outside"}  # noqa: E731
    packet = {"pieces": [], "paths": [
        {"title": f"Path {i}", "said": "", "then": "", "then_steps": [], "steps": [step(f"a{i}"), step(f"b{i}")]}
        for i in range(6)]}
    got = _branch_js(f"""
      state.bpPath = 2;
      console.log(JSON.stringify(bpPaths({json.dumps(packet)}, '#/demo?branch=x&bp=code')));
    """)
    assert got.count('<button type="button" class="bp-fkey') == 6, "the list of flows is missing one"
    assert 'class="bp-fkey on" data-bp-path="2"' in got and '<span class="bp-fkey-i">6</span>' in got
    assert "Flow 3 of 6" in got
    assert re.findall(r'class="bp-fstep-btn" data-bp-path="(\d)"', got) == ["1", "3"]
    flow = got.split('class="sq"', 1)[1]
    last = _branch_js(f"""
      state.bpPath = 5;
      console.log(JSON.stringify(bpPaths({json.dumps(packet)}, '#x')));
    """)
    assert re.findall(r'class="bp-fstep-btn" data-bp-path="(\d)"', last) == ["4", "0"]
    assert "a2</text>" in flow and "b2</text>" in flow
    assert "a0</text>" not in flow and "a5</text>" not in flow, "another flow's steps were drawn with it"



def test_a_long_note_in_a_key_flow_is_drawn_whole():
    """A note in the old map got two lines and an ellipsis. Under its message
    it gets as many lines as it needs, so all of it is there."""
    note = "takes the command and steps from route.sign_in(), not the cached status, on every load of the page"
    packet = {"pieces": [], "paths": [{"title": "P", "said": "", "then": "", "then_steps": [], "steps": [
        {"label": "GET /api/setup", "note": note, "piece": "", "kind": "outside"}]}]}
    got = _branch_js(f"""
      console.log(JSON.stringify(bpFlow({json.dumps(packet)}, '#x', 0).svg));
    """)
    shown = " ".join(re.findall(r'<text class="sq-note"[^>]*>([^<]*)</text>', got))
    assert shown == note and "textLength" not in got


def test_what_a_branch_adds_says_its_screens_endpoints_and_data(tmp_path):
    """Most software is a screen, the endpoints behind it and the data it
    keeps, so those are what the packet says first: a component added and one
    changed, a route named in full with its router's prefix, a table added with
    its columns and a column added to a table that was there."""
    from factory import branch_facts

    base = {
        "app/models.py": ("from sqlalchemy.orm import Mapped, mapped_column\n\n\nclass Card(Base):\n"
                          "    __tablename__ = \"cards\"\n\n    id: Mapped[int] = mapped_column(primary_key=True)\n"),
        "app/routes.py": ("router = APIRouter(prefix=\"/api/cards\")\n\n\n@router.get(\"\")\n"
                          "def cards():\n    return []\n"),
        "web/Card.tsx": "export function Card() {\n  return null;\n}\n",
    }
    branch = {
        "app/models.py": ("from sqlalchemy.orm import Mapped, mapped_column\n\n\nclass Card(Base):\n"
                          "    __tablename__ = \"cards\"\n\n    id: Mapped[int] = mapped_column(primary_key=True)\n"
                          "    title: Mapped[str] = mapped_column()\n\n\n"
                          "class Tag(Base):\n    __tablename__ = \"tags\"\n\n"
                          "    id: Mapped[int] = mapped_column(primary_key=True)\n"
                          "    name: Mapped[str] = mapped_column()\n"),
        "app/routes.py": ("router = APIRouter(prefix=\"/api/cards\")\n\n\n@router.get(\"\")\n"
                          "def cards():\n    return []\n\n\n@router.delete(\"/{card_id}\")\n"
                          "def drop(card_id):\n    return None\n"),
        "web/Card.tsx": "export function Card() {\n  return <TagList />;\n}\n",
        "web/TagList.tsx": "const MAX = 3;\n\nexport function TagList() {\n  return null;\n}\n",
    }
    _config, _project, repo = _brought(tmp_path, base=base, branch=branch)
    b, h = _git(repo, "rev-parse", "main"), _git(repo, "rev-parse", "explore")
    f = branch_facts.facts(repo, b, h, cutting.cut(repo, b, h), None)
    assert [(c["name"], c["state"]) for c in f["components"]] == [("TagList", "added"), ("Card", "changed")]
    assert [(r["name"], r["state"]) for r in f["routes"]] == [("DELETE /api/cards/{card_id}", "added")]
    assert [(t["name"], t["columns"]) for t in f["data"]["tables_added"]] == [("tags", ["id", "name"])]
    assert [(t["name"], t["added"]) for t in f["data"]["tables_changed"]] == [("cards", ["title"])]


def test_the_data_tab_says_what_each_column_s_own_code_says(tmp_path):
    """The spec's Data tab gives each column a type, its keys, whether it may be
    empty and a note. A branch has no writer to say so, so each is read from
    the declaration: the type and keys from its call, NOT NULL from a primary
    key or an annotation without None, the note from the comment it is written
    under, and the table's from its docstring and its unique constraints. What
    the code leaves unsaid stays blank. Each endpoint and screen carries its
    docstring or its comment the same way, for the Interface and Where you see
    it tabs."""
    from factory import branch_facts

    base = {"app/models.py": "from sqlalchemy import ForeignKey\n",
            "app/tags.py": "router = APIRouter()\n",
            "web/Pick.tsx": "export const x = 1;\n"}
    branch = {
        "app/models.py": (
            "from sqlalchemy import ForeignKey\n\n\nclass Tag(Base):\n"
            "    \"\"\"A label a workspace uses.\"\"\"\n\n    __tablename__ = \"tags\"\n"
            "    __table_args__ = (UniqueConstraint(\"workspace_id\", \"name\"),)\n\n"
            "    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)\n"
            "    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), "
            "ForeignKey(\"workspaces.id\", ondelete=\"CASCADE\"))\n"
            "    # Stored already normalised.\n"
            "    name: Mapped[str] = mapped_column(String(32))\n"
            "    colour: Mapped[str | None] = mapped_column()\n"
            "    cards: Mapped[list[\"Card\"]] = relationship(secondary=\"card_tags\")\n"),
        "app/tags.py": ("router = APIRouter()\n\n\n@router.get(\"/api/tags\")\n"
                        "def pool():\n    \"\"\"Every tag name in use, in name order.\"\"\"\n    return []\n"),
        "web/Pick.tsx": ("export const x = 1;\n\n/** Picks a tag or makes one. */\n"
                         "export function Pick() {\n  return null;\n}\n"),
    }
    _config, _project, repo = _brought(tmp_path, base=base, branch=branch)
    b, h = _git(repo, "rev-parse", "main"), _git(repo, "rev-parse", "explore")
    f = branch_facts.facts(repo, b, h, cutting.cut(repo, b, h), None)
    tags = f["data"]["tables_added"][0]
    assert tags["note"] == "A label a workspace uses. Unique on (workspace_id, name)."
    d = tags["detail"]
    assert (d["id"]["type"], d["id"]["key"], d["id"]["nullable"]) == ("UUID", "primary key", False)
    assert d["workspace_id"]["key"] == "foreign key to workspaces.id"
    assert (d["name"]["type"], d["name"]["note"]) == ("String(32)", "Stored already normalised.")
    assert (d["colour"]["type"], d["colour"]["nullable"]) == ("str", True), "an Optional column read as required"
    assert d["cards"]["type"] == "relation to Card (many), through card_tags"
    assert [r["note"] for r in f["routes"]] == ["Every tag name in use, in name order."]
    assert [c["note"] for c in f["components"]] == ["Picks a tag or makes one."]

    tabs = _branch_js(f"""
      console.log(JSON.stringify(bpTouchTabs({json.dumps(f)}, (id, inner) => inner)));
    """)
    assert "UUID, foreign key to workspaces.id" in tabs and "NOT NULL" in tabs
    assert "Stored already normalised." in tabs and "Every tag name in use" in tabs


def test_the_map_s_lines_are_read_from_the_code(tmp_path):
    """The spec's map draws the lines its writer declared; this one draws the
    lines the branch's code makes. A component that calls a shared client's
    function reaches the endpoint whose path that function names -- with the
    method of its own call, not the next one's -- and a handler that uses a
    model touches its table. A model named only as a type touches nothing."""
    from factory import branch_facts

    models = ("from sqlalchemy.orm import Mapped, mapped_column\n\n\n"
              "class Workspace(Base):\n    __tablename__ = \"workspaces\"\n\n"
              "    id: Mapped[int] = mapped_column(primary_key=True)\n")
    base = {"app/models.py": models, "web/api.ts": "export const api = {};\n",
            "web/Board.tsx": "export function Board() {\n  return null;\n}\n"}
    branch = {
        "app/models.py": models + ("\n\nclass Tag(Base):\n    __tablename__ = \"tags\"\n\n"
                                   "    id: Mapped[int] = mapped_column(primary_key=True)\n"),
        "app/tags.py": ("router = APIRouter()\n\n\n@router.get(\"/api/tags\")\n"
                        "def pool(workspace: Workspace):\n    return named(workspace)\n\n\n"
                        "@router.post(\"/api/cards/{card_id}/tags\")\ndef add(card_id):\n    return None\n\n\n"
                        "def named(workspace: Workspace):\n    return select(Tag.name)\n"),
        "web/api.ts": ("export const api = {\n  listTags: (ws: string) => request(ws, '/api/tags'),\n\n"
                       "  addTag: (ws: string, id: string) =>\n    request(ws, `/api/cards/${id}/tags`, {\n"
                       "      method: 'POST',\n    }),\n};\n"),
        "web/Board.tsx": "export function Board() {\n  void api.listTags('a');\n  return null;\n}\n",
    }
    _config, _project, repo = _brought(tmp_path, base=base, branch=branch)
    b, h = _git(repo, "rev-parse", "main"), _git(repo, "rev-parse", "explore")
    links = branch_facts.facts(repo, b, h, cutting.cut(repo, b, h), None)["links"]
    assert links["ui_endpoint"] == [["Board", "GET /api/tags"]], \
        "the screen's call through the client was not followed, or took the next function's method"
    assert links["endpoint_data"] == [["GET /api/tags", "tags"]], \
        "a model named only as a type was counted as touched, or a helper's use was missed"
