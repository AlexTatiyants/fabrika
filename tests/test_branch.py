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
from factory.schemas import (ArbiterReport, BreakerSuite, Gate, GateReport, GateResult, Piece, PieceCall, PieceJoin,
                             PieceJudgement, ReviewReport)


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
    """The rapporteur, answering a look from a table. The attack's roles are
    answered too, as if they found nothing: a breaker with no probes, a panel
    that accepts, an arbiter with nothing to rule on. Anything else is refused.
    `attack` overrides any of them, by schema."""

    def __init__(self, klass="mechanical", skip=(), unreasoned=(), joins=(), every_id=False, attack=None,
                 claims=()):
        self.klass, self.skip, self.unreasoned = klass, set(skip), set(unreasoned)
        self.joins, self.every_id, self.claims = list(joins), every_id, list(claims)
        self.attack = dict(attack or {})
        self.asked: list[tuple[str, str, str]] = []

    async def ask(self, role, prompt, schema=None, *, system="", temperature=None):
        assert system, f"role {role} was called without its prompt file"
        self.asked.append((role, prompt, system))
        if schema in self.attack:
            answer = self.attack[schema]
            return answer(prompt) if callable(answer) else answer
        if schema is BreakerSuite:
            return BreakerSuite(strategy="Nothing worth attacking.")
        if schema is ReviewReport:
            return ReviewReport(summary="Sound.")
        if schema is ArbiterReport:
            return ArbiterReport(summary="Nothing to route.")
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
            joins=self.joins, claims=self.claims)

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
    [(role, prompt, system)] = [a for a in stub.asked if a[0] == "rapporteur"]
    assert "a branch Fabrika did not build" in system
    assert "Let a widget turn." in prompt and "Widgets that turn" in prompt
    to_class = prompt.split("# The pieces to class", 1)[1].split("\n# ", 1)[0]
    assert "tests/test_core.py" not in to_class, "the rapporteur was asked to class a test"
    assert "test_widget_turns in tests/test_core.py" in prompt, "the rapporteur was not told the tests it can name"


# ==========================================================================
# the attack: probes, a panel, and where the arbiter sends what they found
# ==========================================================================


def _attacker(**kw):
    """A look whose breaker writes two probes against the claims -- one that
    fails, one that holds -- and whose panel finds the limit written twice."""
    from factory.schemas import BreakerTest, Claim, Finding, FindingDisposition

    suite = BreakerSuite(strategy="The limit, and the turn.", command="", tests=[
        BreakerTest(path="tests/breaker/test_limit.py", contents="raise SystemExit(1)\n",
                    hypothesis="A sixth widget is let in although the limit is five", severity="major",
                    kind="regression", passes_when="the sixth is refused", claim_ids=["CL-2"],
                    targets=["Widget", "CL-2"]),
        BreakerTest(path="tests/breaker/test_turn.py", contents="raise SystemExit(0)\n",
                    hypothesis="turn() skips a step", kind="regression", passes_when="turn adds one",
                    claim_ids=["CL-1"], targets=["app/core.py"])])
    review = ReviewReport(summary="Readable.", findings=[
        Finding(id="F-1", title="The limit is written in two places", severity="minor", category="duplication",
                detail="LIMIT and a literal 5.", files=["app/core.py"]),
        Finding(id="F-2", title="Nothing says what a widget is for", severity="minor", category="scope",
                detail="No caller.", files=["app/core.py"])])

    def rule(prompt):
        ids = re.findall(r"^## (\S+)$", prompt, re.M)
        def where(fid):
            if fid.startswith("breaker"):
                return "repair"
            return "simplify" if "two places" in prompt.split(f"## {fid}", 1)[1].split("## ", 1)[0] \
                else "needs_spec_change"
        return ArbiterReport(summary="Routed.", dispositions=[
            FindingDisposition(finding_id=fid, disposition=where(fid), reason="because") for fid in ids])

    claims = [Claim(quote="A Widget class whose turn() adds one", tests=["test_widget_turns"]),
              Claim(quote="the limit raised to five", tests=[])]
    return _LookStub(claims=claims, attack={BreakerSuite: suite, ReviewReport: review, ArbiterReport: rule}, **kw)


def test_a_probe_that_goes_red_is_a_finding_and_breaks_the_claim_it_names(tmp_path):
    """The breaker attacks the claims the commits make. A probe that fails is a
    finding with a process behind it, and the claim it names is broken however
    its tests ran; one that holds proves nothing and is only kept if it is a
    regression probe that keeps holding. The panel reads the probes' results,
    and the arbiter routes everything: a defect back to whoever built the
    branch, a cleanup to the tidy branch, and what a look has no route for --
    a spec to change -- to the person. And for all of it, the checkout, the
    branch and every ref are as they were."""
    config, project, repo = _brought(tmp_path)
    config.rework.breaker_file_command = "python3 {path}"
    config.rework.promote_runs = 2
    before = _snapshot(repo)
    stub = _attacker()
    _review, store, _packet = _look(config, project, llm=stub)
    assert _snapshot(repo) == before, "the attack moved something of the person's"

    breaker_prompt = next(p for r, p, _ in stub.asked if r == "breaker")
    assert "CL-2" in breaker_prompt and "the limit raised to five" in breaker_prompt
    assert "Attack these first" in breaker_prompt.split("CL-2", 1)[0] + breaker_prompt, \
        "the claims nothing tests were not put first"
    assert all("In a look at outside work" in system for r, _p, system in stub.asked if r != "rapporteur"), \
        "an attacking role was not told it is in a look"

    found = store.payload("look_findings")
    probes = {p["path"]: p for p in found["probes"]}
    assert probes["tests/breaker/test_limit.py"]["red"] and not probes["tests/breaker/test_turn.py"]["red"]
    assert probes["tests/breaker/test_turn.py"]["promoted"] and not probes["tests/breaker/test_limit.py"]["promoted"], \
        "the regression probe that holds was not kept, or the red one was"
    assert found["broken"] == ["CL-2"]
    routes = found["routes"]
    breaker_ids = [fid for fid in routes if fid.startswith("breaker")]
    assert breaker_ids and all(routes[fid] == "send_back" for fid in breaker_ids)
    files = {f["id"]: f["files"] for f in found["ledger"]["findings"]}
    said = {f["id"]: f["recommendation"] for f in found["ledger"]["findings"]}
    assert all("spec" not in said[fid] for fid in breaker_ids), "a look's probe finding offered a spec to rule by"
    assert all(files[fid] == ["app/core.py"] for fid in breaker_ids), \
        "a probe's target named as a symbol or a claim was not put back to the file it is in"
    titles = {r["finding_id"]: r["title"] for r in found["ledger"]["records"]}
    by_title = {t: routes[fid] for fid, t in titles.items()}
    assert by_title["The limit is written in two places"] == "cleanup"
    assert by_title["Nothing says what a widget is for"] == "you", "a route a look has no use for was kept"
    review_prompt = next(p for r, p, _ in stub.asked if r == "reviewer")
    assert "A sixth widget is let in" in review_prompt, "the panel did not see the probes' results"


def _tidy_look(tmp_path, cleanup):
    """A look attacked as `_attacker` does, with a check that passes at its
    head, and a simplifier that writes `cleanup` as app/core.py."""
    from factory.schemas import FileWrite, WorkerOutput

    config, project, repo = _brought(tmp_path, gates=[Gate(name="limit", command="grep -q 'LIMIT = 5' app/core.py")])
    config.rework.breaker_file_command = "python3 {path}"
    config.rework.promote_runs = 2
    stub = _attacker()
    stub.attack[WorkerOutput] = lambda prompt: WorkerOutput(
        unit_id="simplify", summary="One limit.", files=[FileWrite(path="app/core.py", contents=cleanup)])
    review, store, _packet = _look(config, project, llm=stub)
    return project, repo, review, store, stub


def _theirs(repo):
    """Everything of the person's: the snapshot without Fabrika's own tidy refs."""
    snap = _snapshot(repo)
    snap["refs"] = "\n".join(line for line in snap["refs"].splitlines() if "refs/heads/factory/tidy/" not in line)
    return snap


def test_the_tidy_branch_holds_the_cleanup_and_green_probes_then_red_probes_on_top(tmp_path):
    """On a press, and on a branch of Fabrika's own: the cleanup the arbiter
    routed and the probes that pass, in a commit held to the checks first;
    then the probes that fail, alone, so taking the branch keeps the checks
    green until the fix. The branch under review, the checkout and its HEAD
    are as they were."""
    tidier = BRANCH["app/core.py"].replace("LIMIT = 5", "LIMIT = 5  # the one place it is said")
    _project, repo, review, store, stub = _tidy_look(tmp_path, tidier)
    before = _theirs(repo)
    record = asyncio.run(review.make_tidy("explore"))
    assert _theirs(repo) == before, "making the tidy branch moved something of the person's"

    tidy = "refs/heads/factory/tidy/explore"
    assert _git(repo, "rev-parse", tidy) == record["tip"]
    subjects = _git(repo, "log", "--format=%s", f"refs/heads/explore..{tidy}").splitlines()
    assert subjects == ["Probes that fail until what they found is fixed",
                        "Tidy explore: cleanup and probes that pass"]
    first = set(_git(repo, "show", "--name-only", "--format=", f"{tidy}~1").splitlines())
    second = set(_git(repo, "show", "--name-only", "--format=", tidy).splitlines())
    assert first == {"app/core.py", "tests/breaker/test_turn.py"} and second == {"tests/breaker/test_limit.py"}
    assert record["kept_cleanup"] and record["fence"]["moved"] == []
    assert "In a look at outside work" in next(sys for r, _p, sys in stub.asked if r == "simplifier")

    again = asyncio.run(review.make_tidy("explore"))
    assert _git(repo, "rev-parse", tidy) == again["tip"], "a second press did not move the branch it made"


def test_a_cleanup_that_turns_a_check_red_is_thrown_away_and_the_probes_still_land(tmp_path):
    """The fence is what the cleanup is measured by. A cleanup under which a
    check that passed at the head fails is not cleanup, and is not put on the
    branch; the probes are, without it."""
    broken = BRANCH["app/core.py"].replace("LIMIT = 5", "LIMIT = 4")
    _project, repo, review, _store, _stub = _tidy_look(tmp_path, broken)
    record = asyncio.run(review.make_tidy("explore"))
    assert not record["kept_cleanup"] and record["fence"]["moved"] == ["limit"]
    tidy = "refs/heads/factory/tidy/explore"
    first = set(_git(repo, "show", "--name-only", "--format=", f"{tidy}~1").splitlines())
    assert first == {"tests/breaker/test_turn.py"}, "the cleanup that broke a check was kept"
    assert _git(repo, "show", f"{tidy}:app/core.py") == BRANCH["app/core.py"].rstrip("\n")


def test_a_tidy_branch_someone_committed_to_is_not_moved(tmp_path):
    """Fabrika moves its own tidy branch only from where it left it. Once
    somebody has committed to it, it is theirs, and a press says so."""
    tidier = BRANCH["app/core.py"].replace("LIMIT = 5", "LIMIT = 5  # one place")
    _project, repo, review, _store, _stub = _tidy_look(tmp_path, tidier)
    asyncio.run(review.make_tidy("explore"))
    tidy = "refs/heads/factory/tidy/explore"
    tree = _git(repo, "rev-parse", f"{tidy}^{{tree}}")
    theirs = _git(repo, "commit-tree", tree, "-p", _git(repo, "rev-parse", tidy), "-m", "mine now")
    _git(repo, "update-ref", tidy, theirs)
    with pytest.raises(ProjectError, match="someone has committed to it"):
        asyncio.run(review.make_tidy("explore"))
    assert _git(repo, "rev-parse", tidy) == theirs


def _fix_look(tmp_path, fixed_core):
    """A look whose probe fails until app/core.py names its limit, sent back,
    with a repairer that writes `fixed_core`."""
    from factory.schemas import BreakerTest, FileWrite, WorkerOutput

    config, project, repo = _brought(tmp_path, gates=[Gate(name="limit", command="grep -q 'LIMIT = 5' app/core.py")])
    config.rework.breaker_file_command = "python3 {path}"
    config.rework.promote_runs = 2
    stub = _attacker()
    stub.attack[BreakerSuite] = BreakerSuite(strategy="The limit.", tests=[
        BreakerTest(path="tests/breaker/test_limit.py", kind="regression", passes_when="the limit is named",
                    contents="import sys\nsys.exit(0 if 'MAX_WIDGETS' in open('app/core.py').read() else 1)\n",
                    hypothesis="The limit is a bare number nobody can find", claim_ids=["CL-2"],
                    targets=["app/core.py"]),
        BreakerTest(path="tests/breaker/test_turn.py", contents="raise SystemExit(0)\n", kind="regression",
                    hypothesis="turn() skips a step", passes_when="turn adds one", targets=["app/core.py"])])
    stub.attack[WorkerOutput] = lambda prompt: WorkerOutput(
        unit_id="R-1", summary="Named the limit.", files=[FileWrite(path="app/core.py", contents=fixed_core)])
    review, store, _packet = _look(config, project, llm=stub)
    return review, store, repo, stub


def test_a_fix_moves_the_item_s_own_working_branch_and_starts_its_next_round(tmp_path):
    """Sending work back to Fabrika is a round of the same work, not a new
    piece of it. The fix goes on the work's own branch, factory/outside/<branch>,
    off this round's head -- never on the person's branch: the probes first,
    as tests, then each fix under what it fixes. Then the next round of the same
    work opens, at the fixed head: one item, two rounds, no second entry."""
    named = BRANCH["app/core.py"].replace("LIMIT = 5", "LIMIT = 5\nMAX_WIDGETS = LIMIT")
    review, store, repo, stub = _fix_look(tmp_path, named)
    before = _theirs(repo)
    record = asyncio.run(review.fix("explore"))
    snap = _theirs(repo)
    snap["refs"] = "\n".join(x for x in snap["refs"].splitlines() if "refs/heads/factory/outside/" not in x)
    assert snap == before, "fixing it moved something of the person's"

    work = "refs/heads/factory/outside/explore"
    assert _git(repo, "rev-parse", work) == record["tip"]
    subjects = _git(repo, "log", "--format=%s", f"refs/heads/explore..{work}").splitlines()
    assert subjects[-1] == "Add tests from Fabrika's review of explore" and len(subjects) == 2
    assert subjects[0].startswith("Fix: The limit is a bare number nobody can find"), \
        "a fix commit is not called what it fixes"
    assert "MAX_WIDGETS" in _git(repo, "show", f"{work}:app/core.py")
    probe_id = next(p["finding"] for p in store.payload("look_findings")["probes"] if p["red"])
    assert record["fixed"] == [probe_id] and record["not_fixed"] == []
    assert any(r == "repairer" and "In a look at outside work" in sys for r, _p, sys in stub.asked)

    state = review.look_state(store)
    assert (state.look, state.head_sha, state.working_branch) == (2, record["tip"], "factory/outside/explore")
    assert state.stage == "reviewing" and record["next_look"] == 2
    assert list(review.project.branch_slugs()) == ["explore"], "the fix became a second piece of work"


def test_fabrika_s_commits_are_never_read_as_claims(tmp_path):
    """The next round's rapporteur sees Fabrika's commits for what they are --
    its own account of its own fixes -- and not among the branch's claims."""
    named = BRANCH["app/core.py"].replace("LIMIT = 5", "LIMIT = 5\nMAX_WIDGETS = LIMIT")
    review, store, _repo, stub = _fix_look(tmp_path, named)
    asyncio.run(review.fix("explore"))
    asyncio.run(review.run_look("explore"))
    prompt = [p for r, p, _ in stub.asked if r == "rapporteur"][-1]
    said = prompt.split("# What its commits say", 1)[1].split("# Commits Fabrika made", 1)
    assert MESSAGE.splitlines()[0] in said[0] and "Fix: " not in said[0], "a commit of Fabrika's was read as a claim"
    assert "Fix: The limit is a bare number" in said[1]
    commits = store.payload("look")["commits"]
    assert [c["fabrika"] for c in commits] == [False, True, True]


def test_your_new_commits_are_merged_into_the_working_branch_before_a_round(tmp_path):
    """Work goes on on the person's branch while Fabrika fixes its own. The
    next round takes their new commits in, merged into the working branch;
    where they clash with Fabrika's fixes, it does not start, and names the
    files."""
    named = BRANCH["app/core.py"].replace("LIMIT = 5", "LIMIT = 5\nMAX_WIDGETS = LIMIT")
    review, store, repo, _stub = _fix_look(tmp_path, named)
    asyncio.run(review.fix("explore"))
    asyncio.run(review.run_look("explore"))
    _git(repo, "checkout", "-q", "explore")
    _write(repo, {"docs/notes.md": "More.\n"})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "Notes")
    _git(repo, "checkout", "-q", "main")
    theirs = _git(repo, "rev-parse", "explore")
    state = review.open_look("explore")
    work = _git(repo, "rev-parse", "refs/heads/factory/outside/explore")
    assert state.look == 3 and state.head_sha == work and state.theirs_head == theirs
    assert _git(repo, "log", "-1", "--format=%s", work) == "Merge your commits from explore"
    assert "MAX_WIDGETS" in _git(repo, "show", f"{work}:app/core.py")
    assert _git(repo, "show", f"{work}:docs/notes.md") == "More."

    _git(repo, "checkout", "-q", "explore")
    _write(repo, {"app/core.py": BRANCH["app/core.py"].replace("LIMIT = 5", "LIMIT = 7")})
    _git(repo, "commit", "-qam", "Seven")
    _git(repo, "checkout", "-q", "main")
    with pytest.raises(ProjectError, match="conflict with Fabrika's fixes.*app/core.py"):
        review.open_look("explore")
    assert _git(repo, "rev-parse", "refs/heads/factory/outside/explore") == work, "a clash moved the branch"


def test_round_two_says_what_was_fixed_and_carries_forward_what_you_accepted_unless_its_code_changed(tmp_path):
    """The next round is read against the last: what Fabrika fixed in between,
    and each finding new, still there, or accepted before. A risk you accepted
    is accepted again, with your note -- unless the code it is about changed
    in between, and then it is yours to call again."""
    from factory.pipeline.branch import compare_rounds
    from factory.schemas import Finding

    named = BRANCH["app/core.py"].replace("LIMIT = 5", "LIMIT = 5\nMAX_WIDGETS = LIMIT")
    review, store, _repo, stub = _fix_look(tmp_path, named)
    panel = stub.attack[ReviewReport]
    stub.attack[ReviewReport] = panel.model_copy(update={"findings": [*panel.findings, Finding(
        id="F-3", title="The settings say nothing about why ten rows", severity="minor", category="docs",
        detail="No comment.", files=["app/settings.yaml"])]})
    asyncio.run(review.run_look("explore"))  # round 1 again, with the third finding
    calls = {c["title"]: c["finding_id"] for c in look_calls_of(review, store)}
    review.rule("explore", calls["Nothing says what a widget is for"], "accept", "It is a demo.")
    review.rule("explore", calls["The settings say nothing about why ten rows"], "accept", "Ten is fine.")
    record = asyncio.run(review.fix("explore"))
    asyncio.run(review.run_look("explore"))
    state = review.look_state(store)
    compared = compare_rounds(store, state.look)
    assert compared["previous"] == state.look - 1
    assert compared["fixed"] == ["The limit is a bare number nobody can find"] and record["fixed"]
    now = {f["title"]: f["id"] for f in store.payload("look_findings")["ledger"]["findings"]}
    assert compared["status"][now["The settings say nothing about why ten rows"]] == "accepted_before"
    assert compared["status"][now["Nothing says what a widget is for"]] == "changed_since_accepted", \
        "a risk was carried forward over code that changed under it"
    carried = {c["title"]: c for c in look_calls_of(review, store)}
    assert carried["The settings say nothing about why ten rows"]["ruling"]["note"] == "Ten is fine."
    assert carried["Nothing says what a widget is for"]["goes"] == "", "a risk on changed code was decided for you"
    panel_prompt = [p for r, p, _ in stub.asked if r == "reviewer"][-1]
    assert "Risks the person accepted last round" in panel_prompt and "Ten is fine." in panel_prompt


def look_calls_of(review, store):
    from factory.pipeline.branch import look_calls
    return look_calls(store, review.look_state(store).look)["calls"]


def test_a_fix_an_earlier_version_made_on_its_own_branch_becomes_the_work_s_working_branch(tmp_path):
    """An earlier version fixed work on factory/fix/<branch>, and looked at it as
    a second item. Opened now, that branch becomes the work's own -- moved to
    factory/outside/<branch> -- and the next round of the same work looks at it;
    the second item leaves the list."""
    config, project, repo = _brought(tmp_path)
    review, store, _packet = _look(config, project)
    tip = _git(repo, "commit-tree", _git(repo, "rev-parse", "explore^{tree}"), "-p",
               _git(repo, "rev-parse", "explore"), "-m", "factory: repair round 1")
    _git(repo, "update-ref", "refs/heads/factory/fix/explore", tip)
    store.append("look_fix", {"look": 1, "branch": "factory/fix/explore", "head": _git(repo, "rev-parse", "explore"),
                              "tip": tip, "targets": [], "fixed": [], "not_fixed": [], "rounds": []},
                 role="orchestrator")
    state = review.adopt_legacy_fix(store, review.look_state(store))
    assert (state.working_branch, state.working_head) == ("factory/outside/explore", tip)
    assert _git(repo, "rev-parse", "refs/heads/factory/outside/explore") == tip
    assert "factory/fix/explore" not in _git(repo, "branch", "--list", "factory/*")
    nxt = review.open_look("explore")
    assert (nxt.look, nxt.head_sha) == (2, tip)


def test_the_rounds_strip_and_each_finding_say_where_the_work_has_been():
    """One row of rounds, each with its head and what came of it, and between
    two, what Fabrika fixed; each finding against the round before."""
    look = {"state": {"slug": "explore", "look": 2, "stage": "awaiting_ruling"}, "shown": 2, "rounds": [
        {"look": 1, "head_sha": "abc1234567", "working": False, "found": True, "send_back": 3, "unruled": 0,
         "accepted": 1, "verdict": "", "fix": {"fixed": 2, "of": 3, "branch": "factory/outside/explore"}},
        {"look": 2, "head_sha": "def7654321", "working": True, "found": True, "send_back": 1, "unruled": 0,
         "accepted": 1, "verdict": "", "fix": None}]}
    compared = {"previous": 1, "fixed": ["A", "B"], "not_fixed": ["C"],
                "status": {"x": "new", "y": "accepted_before", "z": "changed_since_accepted"}}
    got = _branch_js(f"""
      state.projectId = 'demo';
      console.log(JSON.stringify({{ strip: bpRounds({json.dumps(look)}, 'demo'),
        x: bpSince({json.dumps(compared)}, 'x'), y: bpSince({json.dumps(compared)}, 'y'),
        z: bpSince({json.dumps(compared)}, 'z'), fixed: bpFixedSince({json.dumps(compared)}) }}));
    """)
    strip = got["strip"]
    assert strip.index("Round 1") < strip.index("fixed 2 of 3") < strip.index("Round 2")
    assert 'class="bp-round on" href="#/demo?branch=explore&look=2"' in strip
    assert "your branch @abc1234" in strip and "branch @def7654" in strip
    assert ">new<" in got["x"] and ">accepted before<" in got["y"] and ">code changed<" in got["z"]
    assert "could not fix 1" in got["fixed"] and ">fixed</span>A</li>" in got["fixed"] \
        and ">not fixed</span>C</li>" in got["fixed"]


def test_a_fix_that_breaks_a_check_that_passed_is_reverted_and_the_finding_stays_open(tmp_path):
    """What passed at the head passes on the fixed branch, or the round is not
    kept: a fix that breaks something is not one."""
    breaks = BRANCH["app/core.py"].replace("LIMIT = 5", "LIMIT = 4\nMAX_WIDGETS = LIMIT")
    review, _store, repo, _stub = _fix_look(tmp_path, breaks)
    record = asyncio.run(review.fix("explore"))
    assert record["rounds"] and all(r["reverted"] and r["broke"] == ["limit"] for r in record["rounds"])
    assert record["fixed"] == [] and len(record["not_fixed"]) == 1
    work = "refs/heads/factory/outside/explore"
    assert "MAX_WIDGETS" not in _git(repo, "show", f"{work}:app/core.py"), "a fix that broke a check was kept"


def test_a_round_ends_only_by_underwriting_or_dropping_and_fixes_only_what_goes_back(tmp_path):
    """Sending work back is a round, not an ending: the only endings are to
    underwrite it or drop it. A round with nothing going back has nothing to
    fix, and a dropped one is not open."""
    config, project, _repo = _brought(tmp_path)
    review, _store, _packet = _look(config, project)
    with pytest.raises(ProjectError, match="Nothing is going back"):
        asyncio.run(review.fix("explore"))
    with pytest.raises(ProjectError, match="Sending it back is a round"):
        review.verdict("explore", "sent_back")
    review.verdict("explore", "dropped")
    with pytest.raises(ProjectError, match="not open"):
        asyncio.run(review.fix("explore"))


def test_nothing_to_tidy_is_refused(tmp_path):
    config, project, _repo = _brought(tmp_path)
    review, _store, _packet = _look(config, project)
    with pytest.raises(ProjectError, match="nothing to put on a tidy branch"):
        asyncio.run(review.make_tidy("explore"))


def _found():
    """A look's findings record: a defect a probe showed, a cleanup, a call for
    the person, and one ruled out."""
    finding = lambda fid, title, sev="major": {  # noqa: E731
        "id": fid, "title": title, "severity": sev, "category": "", "detail": f"About {title}.",
        "evidence": "assert 6 <= 5", "files": ["app/core.py"], "recommendation": "Refuse the sixth.",
        "criterion_ids": [], "cites": None}
    findings = [finding("breaker-limit", "A sixth widget is let in", "blocker"),
                finding("reviewer-1", "The limit is written twice", "minor"),
                finding("reviewer-2", "Nothing says what a widget is for", "minor"),
                finding("reviewer-3", "The name could be shorter", "nit")]
    return {"look": 1, "routes": {"breaker-limit": "send_back", "reviewer-1": "cleanup",
                                  "reviewer-2": "you", "reviewer-3": "none"},
            "ledger": {"findings": findings, "records": [
                {"finding_id": f["id"], "disposition_reason": "because", "duplicate_of": ""} for f in findings]},
            "probes": [{"path": "tests/breaker/test_limit.py", "kind": "regression", "claim_ids": ["CL-2"],
                        "hypothesis": "A sixth widget is let in", "contents": "assert len(w) <= 5\n",
                        "red": True, "timed_out": False, "promoted": False, "finding": "breaker-limit"}],
            "breaker": {"note": ""}, "broken": ["CL-2"]}


def test_the_findings_tab_says_where_each_finding_goes_and_the_prompt_holds_only_what_goes_back():
    """Drawn as a feature's Your calls: a shut row a finding, in the order a
    reader acts on them -- what goes back first, what was ruled out last and
    dimmed -- with where each goes and what can be done on the row itself.
    The prompt to send back is made from the record, and holds every defect
    sent back and nothing that went anywhere else; a row's own prompt holds
    that one. The tidy branch is a line, not a panel."""
    look = {"state": {"branch": "explore", "head_sha": "abc123def456"}, "findings": _found(),
            "tidy_plan": {"cleanup": 1, "green": 0, "red": 1, "branch": "factory/tidy/explore"}}
    got = _branch_js(f"""
      const d = {json.dumps(look)};
      console.log(JSON.stringify({{
        tab: bpFindings(d, {{ pieces: [{{ id: 'P-1', path: 'app/core.py' }}] }}, '#c'),
        prompt: bpSendBackPrompt(d),
        one: bpSendBackPrompt(d, 'breaker-limit'),
        made: bpTidy({{ ...d, tidy: {{ tip: 'f00', kept_cleanup: true, cleanup_findings: ['reviewer-1'],
          first: {{ sha: 'aaaaaaaaaaaa', files: ['app/core.py'] }},
          second: {{ sha: 'bbbbbbbbbbbb', files: ['tests/breaker/test_limit.py'] }}, fence: {{ moved: [] }} }} }}),
      }}));
    """)
    tab = got["tab"]
    rows = re.findall(r'<details class="wl-call wl-(\w+)( wl-done)?">.*?<span class="wl-t">([^<]*)', tab, re.S)
    assert [title for _sev, _done, title in rows] == [
        "A sixth widget is let in", "Nothing says what a widget is for", "The limit is written twice",
        "The name could be shorter"], "the rows are not in the order a reader acts on them"
    assert [bool(done) for _s, done, _t in rows] == [False, False, False, True], "what was ruled out is not dimmed"
    assert tab.count('data-bp-prompt=') == 1 and 'data-bp-prompt="breaker-limit"' in tab, \
        "a row that does not go back offers a prompt to send it back"
    assert 'id="look-send-back"' in tab and 'href="#c#piece-P-1"' in tab
    assert "Fails today, against CL-2" in tab and 'id="look-tidy"' in tab and "1 cleanup and 1 test" in tab
    prompt = got["prompt"]
    assert "A sixth widget is let in" in prompt and "assert len(w) <= 5" in prompt
    assert not any(t in prompt for t in ("written twice", "what a widget is for", "could be shorter")), \
        "the prompt to send back holds a finding that does not go back"
    assert got["one"] == prompt, "with one defect sent back, its own prompt is the whole prompt"
    assert "Add this test at `tests/breaker/test_limit.py` with your fix" in prompt, \
        "the probe was handed over as evidence, not as a test to keep"

    found = _found()
    found["routes"]["reviewer-2"] = "send_back"
    found["ledger"]["findings"].insert(0, found["ledger"]["findings"].pop(2))  # the minor one first in the ledger
    tidy = {"first": {"files": []}, "second": {"files": ["web/acceptance/branch_x_regression/test_limit.py"]}}
    both = _branch_js(f"""
      console.log(JSON.stringify(bpSendBackPrompt({{ state: {{ branch: 'explore', head_sha: 'abc' }},
        findings: {json.dumps(found)}, tidy: {json.dumps(tidy)},
        tidy_plan: {{ branch: 'factory/tidy/explore' }} }})));
    """)
    assert both.index("## 1. A sixth widget is let in (blocker)") < both.index("## 2. Nothing says what"), \
        "the defects are not most severe first"
    assert "Add this test at `web/acceptance/branch_x_regression/test_limit.py`" in both
    assert "It is also on `factory/tidy/explore`" in both
    assert "factory/tidy/explore" in got["made"] and "2 files" in got["made"] and 'id="look-tidy"' not in got["made"]


def test_a_look_is_underwritten_only_once_every_finding_is_accepted_and_sent_back_with_its_calls(
        tmp_path, monkeypatch):
    """To underwrite a branch is to sign for it at its head: every defect the
    attack found accepted by you as a risk, with your reason, and every call
    made. Until then the refusal names what is left. A branch sent back needs
    one defect going back, and the prompt carries what you said. Ruled, a look
    takes no more calls, and its verdict and the risks you accepted are on the
    record."""
    client, project, _repo, _started = _branch_app(tmp_path, monkeypatch)
    client.post(f"/api/projects/{project.id}/branches", json={"branch": "explore"})
    store = project.branch_store("explore")
    review = BranchReview(load_config(ROOT / "factory.example.yaml"), project)
    state = review.look_state(store)
    state.stage = "awaiting_ruling"
    review._save(store, state)
    store.append("look_findings", _found(), role="orchestrator")
    url = f"/api/projects/{project.id}/branches/explore"

    calls = client.get(url).json()["calls"]
    assert {c["finding_id"]: c["goes"] for c in calls["calls"]} == {"breaker-limit": "send_back", "reviewer-2": ""}, \
        "a defect did not go back by default, or a call for you was made for you"
    early = client.post(f"{url}/verdict", json={"verdict": "underwritten"})
    assert early.status_code == 409 and "A sixth widget is let in" in early.json()["detail"] \
        and "Nothing says what a widget is for" in early.json()["detail"]
    assert client.post(f"{url}/rulings", json={"finding_id": "reviewer-3", "ruling": "accept"}).status_code == 409, \
        "a finding ruled out was taken as a call"

    client.post(f"{url}/rulings", json={"finding_id": "breaker-limit", "ruling": "accept",
                                        "note": "Five is a soft limit for now."})
    still = client.post(f"{url}/verdict", json={"verdict": "underwritten"})
    assert still.status_code == 409 and "Nothing says what a widget is for" in still.json()["detail"]
    back = client.post(f"{url}/verdict", json={"verdict": "sent_back"})
    assert back.status_code == 409 and "Sending it back is a round" in back.json()["detail"]

    client.post(f"{url}/rulings", json={"finding_id": "reviewer-2", "ruling": "accept"})
    signed = client.post(f"{url}/verdict", json={"verdict": "underwritten", "note": "Merging."})
    assert signed.status_code == 200
    record = signed.json()
    assert [a["finding_id"] for a in record["accepted"]] == ["breaker-limit", "reviewer-2"]
    assert record["accepted"][0]["note"] == "Five is a soft limit for now." and record["head_sha"] == state.head_sha
    view = client.get(url).json()
    assert view["state"]["stage"] == "ruled" and view["verdict"]["verdict"] == "underwritten"
    late = client.post(f"{url}/rulings", json={"finding_id": "reviewer-2", "ruling": "send_back"})
    assert late.status_code == 409 and "ruled already" in late.json()["detail"]


def test_your_calls_are_on_the_rows_and_the_prompt_carries_what_you_said():
    """Send back and accept sit on the row, in the column Your calls rules in,
    the one you made lit. A call for you sent back joins the prompt with your
    note; a defect you accepted leaves it. The verdict at the foot says what
    is left before the branch can be underwritten."""
    found = _found()
    calls = {"found": True, "calls": [
        {"finding_id": "breaker-limit", "route": "send_back", "goes": "accept", "severity": "blocker",
         "title": "A sixth widget is let in", "ruling": {"ruling": "accept", "note": "soft limit"}},
        {"finding_id": "reviewer-2", "route": "you", "goes": "send_back", "severity": "minor",
         "title": "Nothing says what a widget is for", "ruling": {"ruling": "send_back", "note": "say what it is for"}}],
        "send_back": ["reviewer-2"], "accepted": ["breaker-limit"], "unruled": []}
    look = {"state": {"branch": "explore", "head_sha": "abc123def456", "stage": "awaiting_ruling"},
            "findings": found, "calls": calls, "tidy_plan": {}}
    got = _branch_js(f"""
      const d = {json.dumps(look)};
      console.log(JSON.stringify({{ tab: bpFindings(d, {{ pieces: [] }}, '#c'), prompt: bpSendBackPrompt(d) }}));
    """)
    tab = got["tab"]
    assert 'class="wl-act on" aria-pressed="true"\n    data-bp-rule="breaker-limit:accept"' in tab
    assert 'data-bp-rule="reviewer-2:send_back"' in tab and 'data-bp-rule="reviewer-3:' not in tab
    assert "You accepted this as a risk: soft limit" in tab
    assert "To underwrite it, accept the 1 defect still going back." in tab
    assert 'data-bp-verdict="dropped"' in tab and 'data-bp-verdict="underwritten"' in tab
    assert 'data-bp-verdict="sent_back"' not in tab, "sending back was offered as an ending"
    assert 'id="look-fix"' in tab and 'id="look-send-back"' in tab, "the round's two ways back are not offered"
    prompt = got["prompt"]
    assert "Nothing says what a widget is for" in prompt and "From the person reviewing it: say what it is for" in prompt
    assert "A sixth widget is let in" not in prompt, "a defect you accepted was sent back anyway"


def test_the_pull_request_line_says_what_became_of_it_and_a_look_sent_back_offers_both_routes():
    """One line for the pull request, a feature's or a look's: opened, being
    opened, failed with a way to try again, or nowhere to open it and where to
    say where. Sent back, a look offers Fabrika's fix and the prompt side by
    side, and once fixed, what was fixed and the look at what it made."""
    states = {
        "opened": {"pull_request": {"url": "https://github.com/me/demo/pull/7", "branch": "explore", "base": "main",
                                    "repo": "me/demo"}, "pr_target": {"repo": "me/demo"}},
        "opening": {"pr_opening": True},
        "failed": {"pull_request_failed": {"error": "HTTP 403"}, "pr_target": {"repo": "me/demo"}},
        "nowhere": {"pr_target": {"missing": "This project has nowhere for pull requests yet."}},
    }
    sent = {"state": {"branch": "explore", "slug": "explore", "head_sha": "abc"}, "findings": _found(),
            "calls": {"found": True, "calls": [{"finding_id": "breaker-limit", "title": "A sixth widget",
                                                "goes": "send_back", "route": "send_back"}],
                      "send_back": ["breaker-limit"], "accepted": [], "unruled": []},
            "verdict": {"verdict": "sent_back", "accepted": [], "unruled": [],
                        "sent_back": [{"finding_id": "breaker-limit", "title": "A sixth widget", "severity": "blocker"}]}}
    fixed = {**sent, "fix": {"branch": "factory/fix/explore", "targets": ["breaker-limit"], "fixed": ["breaker-limit"],
                             "not_fixed": [], "rounds": [{"reverted": False}], "next_look": 2, "tip": "f00"}}
    got = _branch_js(f"""
      state.projectId = 'demo';
      const lines = {{}};
      for (const [k, d] of Object.entries({json.dumps(states)})) lines[k] = prLine(d, 'id="look-pr"');
      console.log(JSON.stringify({{ lines, sent: bpVerdict({json.dumps(sent)}), fixed: bpVerdict({json.dumps(fixed)}) }}));
    """)
    lines = got["lines"]
    assert 'href="https://github.com/me/demo/pull/7"' in lines["opened"] and "Try again" not in lines["opened"]
    assert "Opening the pull request" in lines["opening"]
    assert "HTTP 403" in lines["failed"] and 'id="look-pr"' in lines["failed"] and "Try again" in lines["failed"]
    assert "nowhere for pull requests" in lines["nowhere"] and "Project settings" in lines["nowhere"]
    assert 'id="look-fix"' in got["sent"] and 'id="look-send-back"' in got["sent"]
    assert "factory/outside/explore" in got["sent"]
    assert "Fabrika fixed 1 of 1" in got["fixed"] and "branch=explore&amp;look=2" in got["fixed"].replace("&look", "&amp;look")
    assert 'id="look-fix"' not in got["fixed"]


def test_a_claim_a_failing_probe_names_is_broken_whatever_its_tests_said(tmp_path, monkeypatch):
    """A probe that goes red is a fact about the code; a test that passes
    beside it is a test of something else. And the tidy branch is made on a
    press, refused when there is nothing to put on it."""
    from factory.server.branches import _broken

    claims = [{"quote": "turns", "tests": ["t"], "missing": [], "state": "shown"},
              {"quote": "limit five", "tests": ["t2"], "missing": [], "state": "shown"}]
    got = _broken(claims, _found())
    assert [c["state"] for c in got] == ["shown", "broken"] and got[1]["broken_by"] == ["breaker-limit"]

    client, project, _repo, _started = _branch_app(tmp_path, monkeypatch)
    client.post(f"/api/projects/{project.id}/branches", json={"branch": "explore"})
    nothing = client.post(f"/api/projects/{project.id}/branches/explore/tidy")
    assert nothing.status_code == 409 and "nothing to put on a tidy branch" in nothing.json()["detail"]
    pressed = []

    async def make_tidy(self, slug):
        pressed.append(slug)

    monkeypatch.setattr(BranchReview, "make_tidy", make_tidy)
    project.branch_store("explore").append("look_findings", _found(), role="orchestrator")
    made = client.post(f"/api/projects/{project.id}/branches/explore/tidy")
    assert made.status_code == 200 and made.json()["branch"] == "factory/tidy/explore" and pressed == ["explore"]
    view = client.get(f"/api/projects/{project.id}/branches/explore").json()
    assert view["findings"]["routes"]["breaker-limit"] == "send_back"
    assert view["tidy_plan"] == {"cleanup": 1, "green": 0, "red": 1, "branch": "factory/tidy/explore"}


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
    landed = got.split("bp-tests-line", 1)[0]
    chips = re.findall(r'class="bp-sym (?:add|chg)"[^>]*>([^<]*)</a>', landed)
    assert chips == ["Card", "Card.tag_names", "GET /api/tags"], "a seam of the cut was named as a unit"
    assert "and its imports" in got
    assert "1 file," in got and "1 new test" in got


def test_the_tests_are_a_list_of_files_each_linked_with_its_tests():
    """The tests were a row of file names, none of them a link. They are a
    list a file a line, the file a link to the whole of it, with how many new
    tests it has and each test, or helper, a link to its piece."""
    piece = lambda i, path, symbol, kind, fresh=True: {  # noqa: E731
        "id": f"P-{i}", "path": path, "symbol": symbol, "kind": kind, "klass": "tests", "fresh": fresh,
        "added": [i], "removed": []}
    pieces = [piece(1, "api/tests/factories.py", "tag_card", "function"),
              piece(2, "api/tests/test_tags.py", "test_tags.py, top · imports", "module"),
              piece(3, "api/tests/test_tags.py", "test_one", "test"),
              piece(4, "api/tests/test_tags.py", "test_two", "test"),
              piece(5, "web/src/App.test.tsx", "App", "test", fresh=False)]
    got = _branch_js(f"""
      console.log(JSON.stringify(bpLanded({{ pieces: {json.dumps(pieces)} }}, null, '#c', {{}})));
    """)
    tests = got.split("bp-tests-line", 1)[1]
    assert re.findall(r'<div class="bp-file-p"><a href="([^"]*)"\s+title="[^"]*">([^<]*)</a>', tests) == [
        ("#c&amp;bpfile=api%2Ftests%2Ffactories.py", "api/tests/factories.py"),
        ("#c&amp;bpfile=api%2Ftests%2Ftest_tags.py", "api/tests/test_tags.py"),
        ("#c&amp;bpfile=web%2Fsrc%2FApp.test.tsx", "web/src/App.test.tsx")], \
        "a test file is not a link to the whole of it"
    assert re.findall(r'href="#c#piece-(P-\d)"\s+title="[^"]*">([^<]*)</a>', tests) == [
        ("P-1", "tag_card"), ("P-3", "test_one"), ("P-4", "test_two"), ("P-5", "App")]
    assert '<span class="bp-file-n">2 new tests</span>' in tests and tests.count("bp-file-n") == 1
    assert "3 files," in got and "2 new tests</summary>" in got.replace("\n", "").replace("  ", "")


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


def test_a_file_the_round_changed_is_served_whole_at_its_head(tmp_path, monkeypatch):
    """A link to a test file landed on one of its pieces, and the reader
    wanted the file. A file the round changed is served whole, as it is at
    the round's head, with the lines the branch added; a file it did not
    change is not served at all."""
    config, project, repo = _brought(tmp_path)
    _look(config, project)
    from fastapi.testclient import TestClient

    from factory.server import create_app

    client = TestClient(create_app(config))
    packet = client.get(f"/api/projects/{project.id}/branches/explore").json()["packet"]
    piece = next(p for p in packet["pieces"] if p["start"] and p["added"])
    got = client.get(f"/api/projects/{project.id}/branches/explore/file", params={"path": piece["path"]}).json()
    at_head = subprocess.run(["git", "show", f"{packet['head_sha']}:{piece['path']}"], cwd=repo,
                             capture_output=True, text=True, check=True).stdout
    assert got["lines"] == at_head.splitlines(), "the file is not the whole of it at the round's head"
    assert set(piece["added"]) <= set(got["added"]) and got["head_sha"] == packet["head_sha"]
    assert client.get(f"/api/projects/{project.id}/branches/explore/file",
                      params={"path": "README-not-changed.md"}).status_code == 404, \
        "a file the round did not change was served"


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
              "const SEVERITY_ORDER = { blocker: 0, major: 1, minor: 2, nit: 3 };\n"
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
    assert [p["name"] for p in got["run"]["phases"]] == ["reader", "gates", "attribution", "rapporteur",
                                                         "breaker", "review", "arbiter"]
    assert [p["status"] for p in got["run"]["phases"]] == ["done", "running"] + ["pending"] * 5
    assert got["run"]["state"]["stage"] == "building" and got["done"]["state"]["stage"] == "awaiting_verdict"

    fixing = _branch_js("""
      const line = lookLine({stage: 'ruled', phases: [{name: 'arbiter', status: 'done'},
        {name: 'repairers', status: 'running', detail: '2 units'}]}, {fixing: true});
      console.log(JSON.stringify(line));
    """)
    assert [p["name"] for p in fixing["phases"]][-5:] == ["arbiter", "fix_repair", "fix_fence", "fix_recheck",
                                                          "fix_branch"], "the fix's stations do not follow the arbiter"
    assert fixing["phases"][-4]["status"] == "running" and fixing["state"]["stage"] == "building", \
        "a fix under way does not read as running"


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


def test_a_round_s_packet_is_not_shown_until_the_whole_look_is_done():
    """The packet is written after the rapporteur, with the breaker, the panel
    and the arbiter still to run. Shown then, it is a verdict band, tabs and
    counts for a review that has not happened; until the last station is
    done, the line is the page."""
    from support import app_js

    screen = app_js().split("function branchScreen()", 1)[1].split("\nfunction ", 1)[0]
    waiting, rest = screen.split("if (!packet || (d.current && s.stage === 'reviewing')) {", 1)
    assert 'class="v-band' not in waiting and 'class="v-band' in rest, \
        "the packet is drawn before the look is done"
    assert "packet opens here when the last one has" in rest.split("</p></div>`;", 1)[0]


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


def test_a_module_s_exports_are_not_endpoints():
    """The as-built lists what a module exports among its ways in -- a
    constant, a class, a test factory -- and counts none of them. Neither does
    the map: only a way a person or a client comes in by is an endpoint."""
    from factory import branch_facts

    report = {"ways_in_added": [
        {"kind": "export", "name": "MAX_TAGS_PER_CARD", "method": "", "test": False},
        {"kind": "export", "name": "tag_pool", "method": "", "test": True},
        {"kind": "route", "name": "/api/tags", "method": "GET", "test": False},
        {"kind": "route", "name": "/api/_probe", "method": "GET", "test": True},
    ]}
    assert [r["name"] for r in branch_facts.routes([], report)] == ["GET /api/tags"]


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
    assert "stores the token" in got and "test_store" in got and "Shown by a test · 1" in got


def test_a_whole_file_marks_what_the_branch_added_and_the_line_a_link_named():
    """The file, whole: every line numbered, the branch's own lines marked as
    they are in a piece, the line a link named marked too, and a way back to
    the pieces. A file the branch removed, or a binary one, says so."""
    f = {"path": "a.py", "head_sha": "abc1234def", "lines": ["one", "two", "three"], "added": [2], "cut": 0, "key": "k"}
    got = _branch_js(f"""
      state.fragment = 'L3';
      console.log(JSON.stringify(bpWholeFile({json.dumps(f)}, 'a.py', '#c')));
    """)
    rows = re.findall(r'<div class="dl([^"]*)" id="L(\d)">\s*<span class="dl-no">\d</span><span class="dl-txt">(\w+)', got)
    assert rows == [("", "1", "one"), (" dl-add", "2", "two"), (" dl-at", "3", "three")], rows
    assert '<a class="bp-file-back" href="#c">' in got and "3 lines, 1 line of them added" in got
    gone = _branch_js(f"""
      console.log(JSON.stringify(bpWholeFile({json.dumps({**f, "gone": True, "lines": []})}, 'a.py', '#c')));
    """)
    assert "removes the file" in gone
    piece = {"id": "P-1", "path": "a/b.py", "symbol": "f", "klass": "novel", "start": 7, "added": [7], "removed": [],
             "rows": [], "reason": "", "classed_by": "rapporteur"}
    linked = _branch_js(f"""
      console.log(JSON.stringify(bpPiece({json.dumps(piece)}, '#c')));
    """)
    assert 'href="#c&amp;bpfile=a%2Fb.py#L7"' in linked and ">whole file</a>" in linked, \
        "a piece does not open its file, whole, at its first line"


def test_a_claim_s_tests_open_their_file_at_the_test():
    """A claim's tests were names. Each is a link to its file, whole, at the
    line the test starts -- named bare, with its file or suite before it, or
    as a spec's title. A test the branch did not touch has no piece to find
    it by, and stays a name."""
    tests = lambda i, path, symbol, start: {"id": f"P-{i}", "path": path, "symbol": symbol,  # noqa: E731
                                            "klass": "tests", "start": start}
    packet = {"pieces": [tests(1, "api/tests/test_tags.py", "test_one", 9),
                         tests(2, "web/e2e/tags.spec.ts", "a tag is added", 12),
                         {"id": "P-3", "path": "api/app/tags.py", "symbol": "test_one", "klass": "novel", "start": 3}],
              "claims": [{"quote": "q", "state": "shown", "missing": [],
                          "tests": ["api/tests/test_tags.py::test_one", "Tags > a tag is added", "test_old"]}]}
    got = _branch_js(f"""
      console.log(JSON.stringify(bpClaims({json.dumps(packet)}, [], '#c')));
    """)
    assert re.findall(r'<a href="([^"]*)" title="[^"]*"><code>([^<]*)</code></a>', got) == [
        ("#c&amp;bpfile=api%2Ftests%2Ftest_tags.py#L9", "api/tests/test_tags.py::test_one"),
        ("#c&amp;bpfile=web%2Fe2e%2Ftags.spec.ts#L12", "Tags > a tag is added")]
    assert " · <code>test_old</code>" in got, "a test with no piece was linked, or dropped"


def test_the_claims_are_grouped_by_what_stands_behind_them_worst_first():
    """The claims were listed in the commits' order, a broken one between two
    shown ones. They are grouped by what stands behind each -- broken by a
    probe, then nothing tests it, then tests that did not run green, then
    shown -- with the commits' order kept within a group, and what broke a
    claim on its own line above its tests."""
    claim = lambda q, state, **kw: {"quote": q, "tests": kw.get("tests", []), "missing": [],  # noqa: E731
                                    "state": state, **({"broken_by": kw["by"]} if "by" in kw else {})}
    packet = {"claims": [claim("one", "shown", tests=["t1"]), claim("two", "untested"),
                         claim("three", "shown", tests=["t3"]), claim("four", "broken", tests=["t4"], by=["probe-x"]),
                         claim("five", "untested")]}
    got = _branch_js(f"""
      console.log(JSON.stringify(bpClaims({json.dumps(packet)}, [])));
    """)
    heads = re.findall(r'class="bp-claim bp-claim-h">([^<]*)</div>', got)
    assert heads == ["Broken by a probe · 1", "No test exercises it · 2", "Shown by a test · 2"], heads
    assert re.findall(r"<q>(\w+)</q>", got) == ["four", "two", "five", "one", "three"]
    assert "did not run green" not in got, "a group with no claims in it was drawn"
    assert '<span class="ev-broke">broken by <code>probe-x</code></span><code>t4</code>' in got



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



def test_a_lifeline_s_name_fits_its_box():
    """A step outside the branch's code is named by what it is -- "Delete
    button onDelete", "DELETE /api/cards/{card_id}" -- and ran out of its box
    into the next one. A name is drawn in what the box holds, on up to two
    lines, cut with an ellipsis past that; the whole of it is on hover, and
    every box is as tall as the tallest so the lifelines start level."""
    names = ["Delete button onDelete", "DELETE /api/cards/{card_id}", "App's act() surface", "Postgres"]
    packet = {"pieces": [], "paths": [{"title": "P", "said": "", "then": "", "then_steps": [], "steps": [
        {"label": n, "note": "", "piece": "", "kind": "outside"} for n in names]}]}
    got = _branch_js(f"""
      console.log(JSON.stringify(bpFlow({json.dumps(packet)}, '#x', 0).svg));
    """)
    room = (124 - 26) // (13 * 0.6)
    drawn = re.findall(r'<text class="sq-name"[^>]*>([^<]*)</text>', got)
    assert drawn and all(len(line.replace("&#39;", "'")) <= room for line in drawn), \
        f"a name runs past its box: {drawn}"
    assert "DELETE" in drawn and "Postgres" in drawn
    for n in names:
        assert f"<title>{n.replace(chr(39), '&#39;')}: outside" in got or f"<title>{n}: outside" in got, \
            f"{n!r} is not whole on hover"
    assert len(set(re.findall(r'<rect x="[^"]*" y="4" width="[^"]*" height="(\d+)"', got))) == 1


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
